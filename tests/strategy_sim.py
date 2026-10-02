"""
Strategy simulator: the bot's QUOTING decisions (the real mm_bot functions) in a crowded tournament book,
fast enough to compare settings over many seeds. It does not run the Bot class, threads or HTTP: that is
tests/scenario.py (Run A). Calibrated with DATA_REPORT.md section 8 (day one, 2026-10-01).

World, one market at a time, 1-second steps:
  Polymarket   the true probability p: tiny diffusion plus jumps (rate per hour by regime).
  Consensus    where tournament traders think the price is: p + per-market bias (sd 1.2c, favourite-longshot)
               + AR(1) noise (sd 0.58c, half-life 5 min). Drives noise-trader direction and human depth.
  Rival bots   2-4 market makers: fair = Polymarket seen with a lag + own offset (sd 0.5c); they penny one
               tick inside the best other quote down to their floor (0-1.5c), re-check every 2-10 s (fast)
               or 30-120 s (slow), and TAKE any quote >= 1.5c through their fair value (pick-offs).
  Humans       resting limit orders 1-6c around consensus, 100-2,000 shares, replaced every 5-30 min.
  Noise flow   market orders, Poisson; side leans toward consensus; size median 100, 8% sweeps of
               1,000-8,000 shares that walk the book; limit 10c through the mid.
  Informed     after a Polymarket jump, 5-60 s later, an order in the jump's direction (limit = new p).
  Us           every `cycle` seconds: fair value = 70% Polymarket (refreshed every 5 s) + 30% depth-filtered
               book (real fair_value), quotes from `strategy` (default: the real compute_quote), reconciled
               with the real side_needs_change; cancels confirm after a write latency, then new orders go out
               and rest after another. One write budget for all markets (a share of writes_per_minute, costed
               as live: a cancel request per market, new orders in batches) in the bot's priority order;
               churn control as Bot.hold_side. News days: most jumps hit many markets at once.
  Rival inventory  each rival holds 2-6 quote sizes at most and skews 0.25-1c per size held.

Metrics per run (summed over markets): fills, shares, edge (c/share vs our fair value at quote), markout
(c/share vs p 15 min later), P&L marked at Polymarket and at the tournament mid, max drawdown of that P&L,
worst-case loss (peak), share of time quoted, writes per market-hour, duplicate quotes (must be 0).

Run:  python tests/strategy_sim.py [seeds] [hours] [regime] [key=value ...]
      regime: quiet (day one) | news (a debate/poll day) | slow (day-one writes) ; key=value overrides Config.
      python tests/strategy_sim.py sweep SEEDS HOURS REGIME '{base overrides}' '{variant}' ...   (same seeds;
      "_share" = share of writes_per_minute these 12 markets get (default WRITE_SHARE); "_rival_inv": 0 = rivals
      without inventory limits (the Builder's world). See SIM_NOTES.md.)
"""
import math
import os
import random
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("SUPERMARKET_API_KEY", "test-key")
os.environ.setdefault("TOURNAMENT_SLUG", "test")
import mm_bot as M                                          # noqa: E402
from mm_bot import TICK, Config, Resting, compute_quote, fair_value, floor_tick, ceil_tick, side_needs_change, rnd  # noqa: E402

M.notify = lambda *a, **k: False

# Calibration (DATA_REPORT_2.md section 9, 16 h of day one and the night; SIM_NOTES.md). "day1" = the Builder's.
CAL = dict(
    jumps=0.04,                   # Polymarket moves >= 1c per market-hour (quiet)
    lat=(0.2, 0.6), slow_share=0.1,   # write latency median 0.4 s; slow (15-30 s) share
    bias_sd=0.016, tr_sd=0.0095, tr_hl=810,   # tournament - Polymarket: bias sd 1.75c with the favourite-longshot
                                              #   pattern; transient sd 0.95c, half-life 13.5 min
    rate_head=38, rate_busy=26, rate_quiet=4,   # market orders per hour (fills per market-hour 8 / 6.6 / 1.2)
    sweep_share=0.0,              # day one's extra 8% of 1,000-8,000-share sweeps (folded into the size tail)
    riv_off=0.024,                # rival fair-value error sd vs Polymarket
    riv_half=(0.0025, 0.0125),    # rival half-spread (rival-only half-spreads: median 0.75c, p10 0.5c, p90 1c)
    riv_penny=0.3,                # share of rivals that penny the best other quote (the rest sit at their own price)
    riv_every=((20, 120), (120, 600)),   # rival re-quote interval, fast / slow (undercut median 151 s); fast ones
                                         #   still react to a Polymarket move after their lag (1-3 s)
)
CAL_DAY1 = dict(jumps=0.05, lat=(0.5, 2.0), slow_share=0.05, bias_sd=0.009, tr_sd=0.0058, tr_hl=300,
                rate_head=60, rate_busy=14, rate_quiet=4, sweep_share=0.08, riv_off=0.005, riv_half=None,
                riv_penny=1.0, riv_every=((2, 10), (30, 120)))
if os.environ.get("SIM_CAL") == "day1":
    CAL.update(CAL_DAY1)

# Market kinds: count per run, market orders per hour, their size (lognormal median, sigma), rivals (fast ones)
KINDS = {
    "headline": dict(n=2, rate=CAL["rate_head"], size=(300, 1.6), rivals=4, fast=3, headline=True),
    "busy": dict(n=4, rate=CAL["rate_busy"], size=(100, 1.3), rivals=3, fast=2, headline=False),
    "quiet": dict(n=6, rate=CAL["rate_quiet"], size=(100, 1.3), rivals=2, fast=1, headline=False),
}
REGIMES = {
    # Polymarket jumps (>=1c) per market-hour; write latency (s) normal and slow share
    "quiet": dict(jumps=CAL["jumps"], lat=CAL["lat"], slow_share=CAL["slow_share"]),
    "news": dict(jumps=0.6, lat=CAL["lat"], slow_share=CAL["slow_share"], common=0.7, common_q=0.5),
    "slow": dict(jumps=CAL["jumps"], lat=CAL["lat"], slow_share=0.7),
}
# Share of the bot's writes_per_minute these 12 markets get (the other ~225 markets use the rest). Writes are
# costed as live: one cancel request per market (both sides at once) or per order, new orders in batches of
# batch_size per cycle; pulls always go, the rest in the bot's priority order (see Bot.change_key).
WRITE_SHARE = 0.24


@dataclass
class Order:
    owner: str          # "us", "r0".., "h" (human)
    is_bid: bool
    price: float
    qty: float
    t: float            # time it became live (queue priority)
    fv: float = 0.0     # our fair value when placed (ours only)
    level: int = 0      # ladder level (ours only)


@dataclass
class Rival:
    name: str
    lag: float          # how old its Polymarket view is (s)
    every: float        # re-check interval (s)
    floor: float        # min distance from its fair value
    size: float
    offset: float       # its fair-value error
    take_edge: float = 0.015
    penny: bool = True  # penny the best other quote (down to the floor), or sit at its own price
    next_t: float = 0.0
    inv: float = 0.0
    cap: float = 1e9    # most shares it will hold either way (then it quotes/takes only the reducing side)
    skew: float = 0.0   # reservation-price shift per `size` shares held


@dataclass
class Mkt:
    kind: str
    p0: float
    bias: float
    rate: float
    headline: bool
    rivals: list
    orders: list = field(default_factory=list)
    inv: float = 0.0
    cash: float = 0.0
    # our in-flight writes: list of (time it lands, action, payload)
    inflight: list = field(default_factory=list)
    last_ref: float | None = None
    ref_seen: float | None = None
    cooldown_until: float = -1.0
    state: dict = field(default_factory=dict)   # strategy scratch space


class Sim:
    def __init__(self, seed, hours=2.0, regime="quiet", cfg=None, strategy=None, cycle=2.0,
                 share=WRITE_SHARE, rival_inv=True):
        # separate streams, so a change in OUR behaviour leaves the world (prices, flow) identical: paired seeds
        self.rng, self.hrng = random.Random(seed), random.Random(seed * 7919 + 1)
        self.rrng, self.lrng = random.Random(seed * 104729 + 2), random.Random(seed * 1299709 + 3)
        self.T = int(hours * 3600)
        self.reg = REGIMES[regime]
        self.cfg = cfg or Config()
        self.strategy = strategy or baseline_strategy
        self.nrng = random.Random(seed * 15485863 + 4)
        self.cycle, self.wcap, self.wlog, self.share = cycle, self.cfg.writes_per_minute * share, [], share
        self.deferred = 0
        self.news = {}                              # common news: second -> direction
        reg = self.reg
        for t in range(self.T + 1):
            if self.nrng.random() < reg["jumps"] * reg.get("common", 0) / reg.get("common_q", 1) / 3600.0:
                self.news[t] = self.nrng.choice((-1, 1))
        self.bias_hl = 0.0
        self.fills, self.writes, self.dups, self.quoted_s, self.alive_s = [], 0, 0, 0, 0
        self.side_s, self.best_s = 0, 0          # our quoted side-seconds, and those at/inside the best other price
        self.locked_s = 0.0                      # cash locked in our resting orders, summed over seconds
        # Round 2: the rest of the account (other ~225 markets) holds bg_cap in positions; capital fraction =
        # (bg_cap + our positions here) / account. Fast unload and the ladder's free-cash gate are prototypes.
        self.bg_cap, self.cap_frac, self.fast_unload, self.lad_gate = 60_000.0, 0.0, None, 0.0
        self.age_samples, self.free_cash = [], 1e9
        self.ladder, self.rival_aware = None, 0.0    # R3 / rival-aware prototypes (see ladder_want)
        self.pnl_curve, self.worst_peak = [], 0.0
        self.mkts, self.paths = [], []
        for kind, k in KINDS.items():
            for _ in range(k["n"]):
                self.mkts.append(self.new_market(kind, k, rival_inv))

    # ---------------------------------------------------------------- world
    def new_market(self, kind, k, rival_inv_on=True):
        r = self.rng
        p0 = r.uniform(0.35, 0.65) if k["headline"] else r.choice([r.uniform(0.05, 0.3), r.uniform(0.3, 0.7), r.uniform(0.7, 0.95)])
        fl = 0.011 if p0 < 0.1 else 0.009 if p0 < 0.3 else -0.006 if p0 > 0.7 else 0.0025
        bias = fl + r.gauss(0, CAL["bias_sd"])
        rivals = []
        for i in range(k["rivals"]):
            fast = i < k["fast"]
            rivals.append(Rival(f"r{i}", lag=r.uniform(1, 3) if fast else r.uniform(5, 30),
                                every=r.uniform(*CAL["riv_every"][0 if fast else 1]),
                                floor=TICK * r.randint(0, 3), size=r.choice([100, 200, 500, 1000]) * (5 if k["headline"] else 1),
                                offset=r.gauss(0, CAL["riv_off"])))
            if CAL["riv_half"]:
                rivals[-1].floor = self.nrng.uniform(*CAL["riv_half"])
                rivals[-1].penny = self.nrng.random() < CAL["riv_penny"]
            if rival_inv_on:
                rivals[-1].cap = rivals[-1].size * self.nrng.uniform(2, 6)
                rivals[-1].skew = self.nrng.uniform(0.0025, 0.01)
        return Mkt(kind, p0, bias, k["rate"], k["headline"], rivals)

    def make_paths(self, m):
        """Polymarket path p[t] and consensus path c[t] for one market."""
        r, T = self.rng, self.T
        p, c = [0.0] * (T + 1), [0.0] * (T + 1)
        x, phi = 0.0, 0.5 ** (1 / CAL["tr_hl"])
        sd_x = CAL["tr_sd"] * math.sqrt(1 - phi * phi)
        cur = m.p0
        jump_p = self.reg["jumps"] * (1 - self.reg.get("common", 0)) / 3600.0
        orient, q = self.nrng.choice((-1, 1)), self.reg.get("common_q", 0)
        for t in range(T + 1):
            if r.random() < jump_p:
                cur += r.choice((-1, 1)) * (0.01 + r.expovariate(1 / 0.012))
            if t in self.news and self.nrng.random() < q:
                cur += self.news[t] * orient * (0.01 + self.nrng.expovariate(1 / 0.012))
            cur += r.gauss(0, 0.00007)
            cur = min(0.98, max(0.02, cur))
            x = phi * x + r.gauss(0, sd_x)
            p[t] = cur
            c[t] = min(0.99, max(0.01, cur + m.bias + x))
        return p, c

    def lat(self):
        lo, hi = self.reg["lat"]
        r = self.lrng
        return r.uniform(15, 30) if r.random() < self.reg["slow_share"] else r.uniform(lo, hi)

    # ---------------------------------------------------------------- book helpers
    @staticmethod
    def others(m, owner=None):
        return [o for o in m.orders if o.owner != owner]

    @staticmethod
    def best(orders, is_bid):
        ps = [o.price for o in orders if o.is_bid == is_bid]
        return (max(ps) if is_bid else min(ps)) if ps else None

    def book_dict(self, m):
        """Others' book in API shape (levels aggregated, best first)."""
        lv = {True: {}, False: {}}
        for o in m.orders:
            if o.owner != "us":
                lv[o.is_bid][o.price] = lv[o.is_bid].get(o.price, 0) + o.qty
        return {"bids": [{"price": p, "quantity": q} for p, q in sorted(lv[True].items(), reverse=True)],
                "asks": [{"price": p, "quantity": q} for p, q in sorted(lv[False].items())]}

    def trade(self, m, t, is_buy, qty, limit, taker):
        """A market order walks the book (price, then time priority). Returns shares done."""
        book = sorted((o for o in m.orders if o.is_bid != is_buy and o.owner != taker
                       and (o.price <= limit + 1e-9 if is_buy else o.price >= limit - 1e-9)),
                      key=lambda o: ((o.price if is_buy else -o.price), o.t))
        done = 0.0
        for o in book:
            if done >= qty:
                break
            q = min(o.qty, qty - done)
            o.qty -= q
            done += q
            if o.owner.startswith("r"):
                self.rival(m, o.owner).inv += q if o.is_bid else -q
            m.state.setdefault("prints", []).append((t, o.price, q))
            if o.owner == "us":
                side = 1 if o.is_bid else -1
                add_lot(m.state.setdefault("lots", []), side * q, t)
                if self.fast_unload and side * (o.fv - o.price) >= self.fast_unload["edge"]:
                    m.state["fu"] = (t + self.fast_unload["secs"], side, q)
                m.inv += side * q
                m.cash -= side * q * o.price
                self.fills.append((t, m, side, q, o.price, o.fv, o.level, taker))
        m.orders = [o for o in m.orders if o.qty > 1e-9]
        if taker.startswith("r"):
            self.rival(m, taker).inv += done if is_buy else -done
        return done

    @staticmethod
    def rival(m, name):
        return m.rivals[int(name[1:])]

    # ---------------------------------------------------------------- agents
    def humans(self, m, t, c):
        if t % 60 == 0 or not any(o.owner == "h" for o in m.orders):
            m.orders = [o for o in m.orders if not (o.owner == "h" and self.hrng.random() < 60 / 900)]
            for is_bid in (True, False):
                while sum(1 for o in m.orders if o.owner == "h" and o.is_bid == is_bid) < 4:
                    d = self.hrng.uniform(0.01, 0.06)
                    px = floor_tick(c - d) if is_bid else ceil_tick(c + d)
                    q = round(math.exp(self.hrng.gauss(math.log(300), 0.8)))
                    m.orders.append(Order("h", is_bid, px, q * (4 if m.headline else 1), t))

    def rival_step(self, m, rv, t, p):
        seen = p[max(0, int(t - rv.lag))]
        f = seen + rv.offset - rv.skew * rv.inv / rv.size
        # 1. take anything clearly through our fair value (bots are takers too), within its inventory cap
        for is_buy in (True, False):
            lim = f - rv.take_edge if is_buy else f + rv.take_edge
            room = rv.cap - rv.inv if is_buy else rv.cap + rv.inv
            if room >= 1:
                self.trade(m, t, is_buy, min(rv.size * 2, room), lim, rv.name)
        # 2. penny one tick inside the best OTHER quote, down to the floor
        mine = [o for o in m.orders if o.owner == rv.name]
        oth = self.others(m, rv.name)
        bb, ba = self.best(oth, True), self.best(oth, False)
        if rv.penny:
            bid = min(floor_tick(f - rv.floor - TICK), floor_tick(bb + TICK) if bb is not None else floor_tick(f - 0.02))
            ask = max(ceil_tick(f + rv.floor + TICK), ceil_tick(ba - TICK) if ba is not None else ceil_tick(f + 0.02))
        else:
            bid, ask = floor_tick(f - rv.floor), ceil_tick(f + rv.floor)
        if ba is not None:
            bid = min(bid, floor_tick(ba - TICK))
        if bb is not None:
            ask = max(ask, ceil_tick(bb + TICK))
        for is_bid, px in ((True, bid), (False, ask)):
            cur = [o for o in mine if o.is_bid == is_bid]
            if (rv.inv if is_bid else -rv.inv) >= rv.cap:
                m.orders = [o for o in m.orders if not (o.owner == rv.name and o.is_bid == is_bid)]
                continue                                 # full on this side: no more
            if cur and abs(cur[0].price - px) < 1e-9:
                continue
            m.orders = [o for o in m.orders if not (o.owner == rv.name and o.is_bid == is_bid)]
            m.orders.append(Order(rv.name, is_bid, rnd(px), rv.size, t + self.rrng.uniform(0.3, 1.5)))
        rv.next_t = t + rv.every * self.rrng.uniform(0.7, 1.3)

    def noise(self, m, t, c):
        if self.rng.random() >= m.rate / 3600.0:
            return
        oth = m.orders
        bb, ba = self.best(oth, True), self.best(oth, False)
        mid = (bb + ba) / 2 if bb is not None and ba is not None else c
        is_buy = self.rng.random() < 0.5 + max(-0.3, min(0.3, 8 * (c - mid)))
        k = KINDS[m.kind]["size"]
        if self.rng.random() < CAL["sweep_share"]:
            q = math.exp(self.rng.gauss(math.log(2000), 0.6)) * (3 if m.headline else 1)
        elif CAL["riv_half"]:
            q = math.exp(self.rng.gauss(math.log(k[0]), k[1]))
        else:
            q = math.exp(self.rng.gauss(math.log(100), 1.0)) * (3 if m.headline else 1)
        self.trade(m, t, is_buy, round(q), mid + 0.10 if is_buy else mid - 0.10, "noise")

    # ---------------------------------------------------------------- us
    def see_ref(self, m, t, p):
        """Polymarket as we see it: refreshed every ref_refresh_seconds (checked every second; it used to be
        checked only on cycle seconds, so with a 2 s cycle and 5 s refresh we saw Polymarket every 10 s)."""
        cfg = self.cfg
        if m.ref_seen is None or t % int(max(1, cfg.ref_refresh_seconds)) == 0:
            new = round(p[t], 3)
            if m.ref_seen is not None and abs(new - m.ref_seen) >= cfg.ref_jump_threshold:
                m.cooldown_until = t + cfg.ref_jump_cooldown_seconds
            if m.ref_seen is not None and abs(new - m.ref_seen) >= cfg.urgent_ref_move - 1e-9:
                m.state["ref_moved_at"] = t
            m.last_ref, m.ref_seen = m.ref_seen, new

    def our_step(self, m, t, p):
        cfg = self.cfg
        if m.inflight:
            return None                              # a write for this market is still in flight
        book = self.book_dict(m)
        bfv = fair_value(book, cfg)
        ref = m.ref_seen
        fv = (1 - cfg.ref_weight) * bfv + cfg.ref_weight * ref if bfv is not None else None
        m.state["fv"] = fv
        if self.bias_hl and bfv is not None:
            # T1 prototype: Polymarket + exponentially weighted average of (book price - Polymarket)
            k = 1 - 0.5 ** (self.cycle / self.bias_hl)
            g = m.state.get("gap")
            m.state["gap"] = bfv - ref if g is None else g + k * ((bfv - ref) - g)
            fv = ref + max(-0.03, min(0.03, m.state["gap"]))
        want = [] if t < m.cooldown_until else self.strategy(self, m, t, fv, bfv, ref, book)
        mine = [o for o in m.orders if o.owner == "us"]
        cancels, places = plan_changes(cfg, mine, want, t, m)
        real = bool(self.ladder and self.ladder.get("real"))
        if real:
            cancels, places = real_ladder_rules(want, cancels, places)
        if not cancels and not places:
            return None
        urgent = t - m.state.get("ref_moved_at", -99) < 15
        key = (0 if not places else 0.5 if urgent else 1, 0 if m.headline else 1, 1 if cancels else 0,
               -max([w[2] for w in places] or [0]))
        if real and all(o.level > 0 for o in cancels) and all(w[3] > 0 for w in places):
            key = (0 if not places and any(ladder_unsafe(o, want) for o in cancels) else 2,) + key[1:]
        return key, m, cancels, places, fv, len(cancels) == len(mine)

    def our_cycle(self, t, paths):
        """One bot cycle over all markets: plan each, then send in priority order within the write budget."""
        self.wlog = [x for x in self.wlog if t - x[0] < 60]
        pos = sum(m.inv * p[t] if m.inv > 0 else -m.inv * (1 - p[t]) for m, p, c in paths)
        self.cap_frac = (self.bg_cap + pos) / 100_000.0
        self.free_cash = 100_000.0 - self.bg_cap - pos - sum(
            o.qty * (o.price if o.is_bid else 1 - o.price) for m in self.mkts for o in m.orders
            if o.owner == "us" and o.level == 0)
        spare = self.wcap - sum(c for _, c in self.wlog)
        plans = sorted((x for x in (self.our_step(m, t, p) for m, p, c in paths) if x),
                       key=lambda x: x[0])
        bs, n, used = self.cfg.batch_size, 0, 0.0
        for i, (key, m, cancels, places, fv, whole) in enumerate(plans):
            c = (1 if whole else len(cancels)) if cancels else 0
            c += len(places) / bs                    # batches are shared with ~225 other markets: amortised
            if used + c > spare and key[0] != 0:
                self.deferred += len(plans) - i          # everything after the first misfit waits
                break
            if key[0] == 2 and spare - used - c < self.cfg.ladder_min_writes * self.share:
                self.deferred += len(plans) - i          # R3 (real): the ladder only with ladder_min_writes to spare
                break
            used, n = used + c, n + len(places)
            lc = self.lat()
            if cancels:
                m.inflight.append((t + lc, "cancel", cancels))
                hist = m.state.setdefault("reprices", {})
                for side in {(o.is_bid, o.level) for o in cancels} & {(w[0], w[3]) for w in places}:
                    hist.setdefault(side, []).append(t)
            if places:
                m.inflight.append((t + (lc if cancels else 0) + self.lat(), "place", [(w, fv) for w in places]))
        if used:
            self.wlog.append((t, used))
            self.writes += used

    def land(self, m, t):
        keep = []
        for when, what, payload in m.inflight:
            if when > t:
                keep.append((when, what, payload))
            elif what == "cancel":
                ids = {id(o) for o in payload}
                m.orders = [o for o in m.orders if id(o) not in ids]
            else:
                for (is_bid, px, q, lvl), fv in payload:
                    m.orders.append(Order("us", is_bid, px, q, when, fv if fv is not None else px, lvl))
        m.inflight = keep
        # after the last write lands, check duplicates (more than one of ours at one level and side)
        if not keep:
            seen = set()
            for o in m.orders:
                if o.owner == "us":
                    k = (o.is_bid, o.level)
                    if k in seen:
                        self.dups += 1
                    seen.add(k)

    def at_best(self, m):
        for is_bid in (True, False):
            ours = [o.price for o in m.orders if o.owner == "us" and o.is_bid == is_bid and o.level == 0]
            if not ours:
                continue
            ob = self.best(self.others(m, "us"), is_bid)
            self.side_s += 1
            if ob is None or (ours[0] >= ob - 1e-9 if is_bid else ours[0] <= ob + 1e-9):
                self.best_s += 1

    # ---------------------------------------------------------------- run
    def run(self):
        for m in self.mkts:
            p, c = self.make_paths(m)
            self.paths.append((m, p, c))
        T = self.T
        curve_pts = list(range(0, T + 1, 60))
        marked = {tt: 0.0 for tt in curve_pts}
        worst = {tt: 0.0 for tt in curve_pts}
        due = {id(m): [] for m in self.mkts}
        for t in range(T + 1):
            for m, p, c in self.paths:
                jump_due = due[id(m)]
                if t > 0 and abs(p[t] - p[t - 1]) >= 0.009:
                    jump_due.append((t + self.rng.uniform(5, 60), p[t] > p[t - 1], p[t]))
                    for rv in m.rivals:                  # rivals react to a Polymarket move after their lag
                        rv.next_t = min(rv.next_t, t + rv.lag + 1)
                self.humans(m, t, c[t])
                for rv in m.rivals:
                    if t >= rv.next_t:
                        self.rival_step(m, rv, t, p)
                self.noise(m, t, c[t])
                for j in [j for j in jump_due if j[0] <= t]:
                    jump_due.remove(j)
                    q = math.exp(self.rng.gauss(math.log(300), 0.7)) * (3 if m.headline else 1)
                    self.trade(m, t, j[1], round(q), j[2], "informed")
                self.land(m, t)
                self.see_ref(m, t, p)
            if t % int(self.cycle) == 0:
                self.our_cycle(t, self.paths)
            for m, p, c in self.paths:
                if any(o.owner == "us" for o in m.orders):
                    self.quoted_s += 1
                    self.at_best(m)
                    self.locked_s += sum(o.qty * (o.price if o.is_bid else 1 - o.price)
                                         for o in m.orders if o.owner == "us")
                self.alive_s += 1
                if t % 60 == 0:
                    lots = m.state.get("lots")
                    if lots:
                        self.age_samples.append(lot_age(lots, t))
                    marked[t] += m.cash + m.inv * p[t]
                    worst[t] += m.inv * p[t] if m.inv > 0 else -m.inv * (1 - p[t])
        peak, dd = -1e18, 0.0
        for tt in curve_pts:
            peak = max(peak, marked[tt])
            dd = max(dd, peak - marked[tt])
        self.max_dd, self.worst_peak = dd, max(worst.values())
        return self.metrics()

    def metrics(self):
        T = self.T
        pend = {id(m): p[T] for m, p, c in self.paths}
        pathp = {id(m): p for m, p, c in self.paths}
        mids = {}
        for m, p, c in self.paths:
            bb, ba = self.best(self.others(m, "us"), True), self.best(self.others(m, "us"), False)
            mids[id(m)] = (bb + ba) / 2 if bb is not None and ba is not None else p[T]
        sh = sum(f[3] for f in self.fills)
        edge = sum(f[2] * f[3] * (f[5] - f[4]) for f in self.fills)
        mk = sum(f[2] * f[3] * (pathp[id(f[1])][min(T, int(f[0]) + 900)] - f[4]) for f in self.fills)
        pnl = sum(m.cash + m.inv * pend[id(m)] for m in self.mkts)
        pnl_mid = sum(m.cash + m.inv * mids[id(m)] for m in self.mkts)
        lag = {}                                  # exchange-style mark: VWAP of all trades in the last 30 min
        for m, p, c in self.paths:
            pr = [x for x in m.state.get("prints", []) if x[0] >= T - 1800]
            v = sum(x[2] for x in pr)
            lag[id(m)] = sum(x[1] * x[2] for x in pr) / v if v else (m.state["prints"][-1][1] if m.state.get("prints") else p[T])
        pnl_lag = sum(m.cash + m.inv * lag[id(m)] for m in self.mkts)
        held = sorted((lot_age([x], T), abs(x[0])) for m in self.mkts for x in m.state.get("lots", []))
        tot_h, acc, age_med = sum(w for _, w in held), 0.0, 0.0
        for a, w in held:
            acc += w
            if acc >= tot_h / 2:
                age_med = a
                break
        fpnl = [f[2] * f[3] * (pend[id(f[1])] - f[4]) for f in self.fills]
        big = [i for i, f in enumerate(self.fills) if f[2] * (f[5] - f[4]) > 0.03]
        big_sh = sum(self.fills[i][3] for i in big)
        tot = sum(fpnl)
        lvl = [i for i, f in enumerate(self.fills) if f[6] > 0]
        pick_cost = -sum(f[2] * f[3] * (pathp[id(f[1])][min(T, int(f[0]) + 900)] - f[4]) for f in self.fills
                         if f[7].startswith("r") or f[7] == "informed")
        picked = sum(f[3] for f in self.fills if f[7].startswith("r") or f[7] == "informed")
        hours = T / 3600.0
        return dict(fills=len(self.fills), shares=round(sh), edge_c=round(100 * edge / sh, 2) if sh else 0.0,
                    markout15_c=round(100 * mk / sh, 2) if sh else 0.0, pnl=round(pnl), pnl_mid=round(pnl_mid),
                    max_dd=round(self.max_dd), worst=round(self.worst_peak),
                    quoted=round(self.quoted_s / max(1, self.alive_s), 3),
                    writes_mh=round(self.writes / len(self.mkts) / hours, 1), dups=self.dups,
                    deferred=round(self.deferred / hours),
                    picked_sh=round(picked), at_best=round(self.best_s / max(1, self.side_s), 3),
                    big_vol=round(big_sh / sh, 3) if sh else 0.0,
                    big_pnl=round(sum(fpnl[i] for i in big) / tot, 2) if tot > 0 else 0.0,
                    locked=round(self.locked_s / (T + 1)), lvl_sh=round(sum(self.fills[i][3] for i in lvl)),
                    lvl_pnl=round(sum(fpnl[i] for i in lvl)), pick_cost=round(pick_cost),
                    pnl_lag=round(pnl_lag), age_med=round(age_med, 2),
                    age_mean=round(sum(self.age_samples) / len(self.age_samples), 2) if self.age_samples else 0.0,
                    cap_frac=round((self.bg_cap + self.worst_peak) / 100_000.0, 3))


def hold(cfg, m, cur, w, t):
    """Bot.hold_side: keep a single safe order that is off target while it is young or this side keeps being
    re-quoted (another bot stepping in front each time), unless Polymarket moved here in the last 15 s."""
    if not cfg.churn_control or m is None or w is None or len(cur) != 1 or len(w) < 5 or w[4] is None:
        return False
    o, is_bid, limit = cur[0], w[0], w[4]
    if (o.price > limit + 1e-9) if is_bid else (o.price < limit - 1e-9):
        return False
    if o.qty > w[2] + 1e-9 or t - m.state.get("ref_moved_at", -99) < 15:
        return False
    hist = [x for x in m.state.get("reprices", {}).get((is_bid, w[3]), []) if t - x <= cfg.churn_window_seconds]
    m.state.setdefault("reprices", {})[(is_bid, w[3])] = hist
    return t - o.t < cfg.min_quote_life_seconds or len(hist) >= cfg.churn_max_reprices


SIM_EPOCH = M.datetime(2026, 10, 1, tzinfo=M.timezone.utc)   # sim second t = SIM_EPOCH + t (order expiry)


def plan_changes(cfg, mine, want, t, m=None):
    """Reconcile our resting orders with the wanted list [(is_bid, price, size, level)]: same rules as
    Bot.reconcile (keep an order a tick off if still safe, churn control, cancel before placing).
    Order expiry as live (Round 4): each order expires order_ttl after it was planned (the TTL saver's tier TTL with
    ttl_tiers_enabled, level 0) and is refreshed refresh_before_expiry before that (or, with ttl_expire_as_cancel,
    left to expire and its side re-quoted ttl_expire_grace_seconds later, as Bot.expiry_wait); an expired order
    leaves the book here. No-chase as Bot.side_fix (mm_bot.no_chase_needs_change, level 0).
    Returns (cancels, places)."""
    cancels, places = [], []
    st = m.state if m is not None else {}
    now = SIM_EPOCH + M.timedelta(seconds=t)
    # Per order: (order, expiry t, inventory and size when planned). Planned values wait in "pend" for the order.
    pend, known, live, gone = st.setdefault("pend", {}), st.get("exp", {}), {}, set()
    last_exp = st.setdefault("last_exp", {})
    for o in mine:
        rec = known.get(id(o))
        if rec is None or rec[0] is not o:
            inv0, qty0, exp_t = pend.pop((o.is_bid, o.level), (None, None, o.t + cfg.order_ttl))
            rec = (o, exp_t, inv0, qty0)
        if rec[1] <= t:
            gone.add(id(o))                                  # expired: the engine drops it
            last_exp[(o.is_bid, o.level)] = rec[1]
        else:
            live[id(o)] = rec
    st["exp"] = live
    if gone:
        mine = [o for o in mine if id(o) not in gone]
        if m is not None:
            m.orders = [o for o in m.orders if id(o) not in gone]
    for lvl_key in {(w[0], w[3]) for w in want} | {(o.is_bid, o.level) for o in mine}:
        is_bid, lvl = lvl_key
        cur = [o for o in mine if o.is_bid == is_bid and o.level == lvl]
        w = next((w for w in want if w[0] == is_bid and w[3] == lvl), None)
        price, size, limit = (w[1], w[2], w[4] if len(w) > 4 else None) if w else (None, 0, None)
        rest = [Resting(0, "", o.is_bid, o.price, o.qty, SIM_EPOCH + M.timedelta(seconds=live[id(o)][1]))
                for o in cur]
        eac = cfg.ttl_expire_as_cancel and lvl == 0
        if lvl == 0 and len(cur) == 1:
            rec = live[id(cur[0])]
            fix = M.no_chase_needs_change(rest, price, size, cfg, now, limit, is_bid, None, st.get("fv"), cur[0].fv,
                                          m.inv if m is not None else None, rec[2], rec[3], expire_as_cancel=eac)
        else:
            fix = side_needs_change(rest, price, size, cfg, now, limit, is_bid=is_bid, expire_as_cancel=eac)
        if eac:                                              # Bot.expiry_wait
            le = last_exp.get((is_bid, lvl))
            if cur or le is None or t >= le + cfg.ttl_expire_grace_seconds:
                last_exp.pop((is_bid, lvl), None)
            elif le <= t:
                fix = False                                  # just expired: re-quote after the grace
        expiring = any(r.expires and (r.expires - now).total_seconds() < cfg.refresh_before_expiry for r in rest)
        if fix and (expiring or not hold(cfg, m, cur, w, t)):   # (Bot.hold_side never holds an expiring order)
            cancels += cur
            if w is not None:
                places.append((is_bid, price, size, lvl))
                ttl = cfg.order_ttl
                if lvl == 0 and m is not None:
                    rng = st.get("ttl_rng") or st.setdefault("ttl_rng", random.Random(f"{m.kind}{m.p0:.6f}"))
                    frac = cfg.size_max_frac if m.kind == "busy" else cfg.size_min_frac
                    ttl = M.order_ttl_for(cfg, M.ttl_tier(m.headline, frac, 1.0, cfg), rng.random())
                pend[(is_bid, lvl)] = (m.inv if m is not None else None, size, t + ttl)
    return cancels, places


def sizes_for(sim, m):
    cfg = sim.cfg
    bank = 100_000.0
    if m.headline:
        return cfg.headline_size_frac * bank, cfg.headline_position_frac * bank
    if m.kind == "busy":
        return cfg.size_max_frac * bank, None
    return cfg.size_min_frac * bank, None


def baseline_strategy(sim, m, t, fv, bfv, ref, book):
    """What the bot quotes: decide() reduced to one market (no race netting, no party shading)."""
    if fv is None:
        return []
    cfg = sim.cfg
    bb = book["bids"][0]["price"] if book["bids"] else None
    ba = book["asks"][0]["price"] if book["asks"] else None
    size, plimit = sizes_for(sim, m)
    no_ask = ref - bfv > cfg.ref_guard_gap
    no_bid = bfv - ref > cfg.ref_guard_gap
    side, bias_edge, bias_size = M.fl_side(fv, m.state.get("fl"), cfg)     # favourite-longshot side bias
    m.state["fl"] = side
    over = cfg.capital_in_positions_max_frac < 1.0 and sim.cap_frac > cfg.capital_in_positions_max_frac
    u_side, u_size = unload_window(sim, m, t)
    q = M.compute_quote(fv, m.inv, m.inv, bb, ba, cfg, no_bid=no_bid, no_ask=no_ask,
                        kelly_p=None if m.headline else ref, order_size=size, position_limit=plimit,
                        bias_side="bid" if side == "mid" else side, bias_edge=bias_edge, bias_size=bias_size,
                        net_inv=m.inv, age_hours=lot_age(m.state.get("lots"), t),
                        adding_factor=cfg.capital_ceiling_adding_size_factor if over else 1.0,
                        unload_side=u_side, unload_edge=cfg.fast_unload_edge, unload_size=u_size)
    want = quote_to_want(q)
    fu = m.state.get("fu")
    if fu and t < fu[0] and fu[1] * m.inv > 0:
        # fast unload prototype: the reducing side at fair -/+ offset (never crossing), size = the sweep fill
        is_bid = fu[1] < 0
        px = floor_tick(fv - sim.fast_unload["off"]) if is_bid else ceil_tick(fv + sim.fast_unload["off"])
        px = min(px, floor_tick(ba - TICK)) if is_bid and ba is not None else px
        px = max(px, ceil_tick(bb + TICK)) if not is_bid and bb is not None else px
        cur = next((w for w in want if w[0] == is_bid), None)
        if cur is None or (px > cur[1] if is_bid else px < cur[1]):
            qty = int(min(abs(m.inv), max(fu[2], cur[2] if cur else 0)))
            want = [w for w in want if w[0] != is_bid] + ([(is_bid, px, qty, 0, None)] if qty >= 1 else [])
    if sim.ladder and sim.lad_gate and sim.free_cash < sim.lad_gate * 100_000.0:
        return want                                   # R3 gate: no ladder while free cash is short
    if sim.ladder or sim.rival_aware:
        want = ladder_want(sim, m, t, fv, ref, q, want, bb, ba, size, plimit)
    return want


LADDER = dict(offs=(0.02, 0.035, 0.05), mults=(1, 2, 3), head_offs=(0.02, 0.04, 0.06, 0.08),
              head_mults=(0.25, 0.25, 0.5, 0.5), move=0.01, pull=0.015, cool=30)


def ladder_want(sim, m, t, fv, ref, q, want, bb, ba, size, plimit):
    """R3 prototype: extra resting levels at anchor -/+ offs (sizes mults x the quote size), the anchor being our
    fair value when last set; held until fair value moves `move` from it; all pulled for `cool` s when
    Polymarket jumps `pull` from where it was at the anchor. Never at or inside level 0, never crossing.
    Rival-aware (sim.rival_aware = step): a side where another trader sits inside our floor drops level 0;
    without a ladder it rests one held order at anchor -/+ step instead."""
    L, st, cfg = sim.ladder or {}, m.state, sim.cfg
    if st.get("lad_ref") is not None and abs(ref - st["lad_ref"]) >= L.get("pull", 0.015):
        st["lad_cool"], st["lad_fv"] = t + L.get("cool", 30), None
    if t < st.get("lad_cool", -1):
        return want
    if st.get("lad_fv") is None or abs(fv - st["lad_fv"]) >= L.get("move", 0.01):
        st["lad_fv"], st["lad_ref"] = fv, ref
    a = st["lad_fv"]
    if sim.rival_aware:
        inside = {True: q.bid_limit is not None and bb is not None and bb > q.bid_limit + 1e-9,
                  False: q.ask_limit is not None and ba is not None and ba < q.ask_limit - 1e-9}
        keep = [w for w in want if not inside[w[0]]]
        if not sim.ladder:
            keep += [(w[0], floor_tick(a - sim.rival_aware) if w[0] else ceil_tick(a + sim.rival_aware), w[2], 1, None)
                     for w in want if inside[w[0]]]
        want = keep
    if not sim.ladder:
        return want
    offs, mults = (L["head_offs"], L["head_mults"]) if m.headline else (L["offs"], L["mults"])
    bank = 100_000.0
    if L.get("real"):
        # mm_bot's own planner: ladder_levels with the same caps the bot uses (headline flat limit, else Kelly at
        # the level's price), and ladder_max_inv_quotes (no adding-side ladder beyond 3 quote sizes)
        def cap(is_bid):
            def f(px):
                lim = plimit if plimit is not None else M.kelly_position(ref, px, bank, cfg, yes=is_bid)
                c = lim - m.inv if is_bid else lim + m.inv
                if size > 0 and abs(m.inv) > cfg.ladder_max_inv_quotes * size and (m.inv > 0) == is_bid:
                    c = 0
                return c
            return f
        lw, _ = M.ladder_levels(a, q, bb, ba, offs, mults, size, {True: cap(True), False: cap(False)})
        return want + [(k[0], px, sz, k[1], None) for k, (px, sz) in sorted(lw.items(), key=lambda kv: kv[0][1])]
    cum = {True: m.inv + (q.bid_size if q.bid is not None else 0), False: -m.inv + (q.ask_size if q.ask is not None else 0)}
    for i, (d, k) in enumerate(zip(offs, mults), start=1):
        for is_bid in (True, False):
            px = floor_tick(a - d) if is_bid else ceil_tick(a + d)
            l0 = q.bid if is_bid else q.ask
            if px < 0.01 or px > 0.99 or (l0 is not None and (px >= l0 - 1e-9 if is_bid else px <= l0 + 1e-9)):
                continue
            if (is_bid and ba is not None and px >= ba - 1e-9) or (not is_bid and bb is not None and px <= bb + 1e-9):
                continue
            lim = plimit if plimit is not None else M.kelly_position(ref, px, bank, cfg, yes=is_bid)
            qty = int(min(k * size, lim - cum[is_bid]))
            if qty >= 1:
                cum[is_bid] += qty
                want.append((is_bid, px, qty, i, None))
    return want


def ladder_unsafe(o, want):
    """R3 (real): a resting ladder order that must go now - level 0 no longer quotes its side, or it sits at or
    inside level 0's price (mm_bot.plan_exchange's urgent pulls)."""
    l0 = next((w for w in want if w[0] == o.is_bid and w[3] == 0), None)
    return l0 is None or (o.price >= l0[1] - 1e-9 if o.is_bid else o.price <= l0[1] + 1e-9)


def real_ladder_rules(want, cancels, places):
    """R3 (real): mm_bot.plan_exchange's ordering - while level 0 changes in a market, only unsafe ladder pulls go
    with it; other ladder work waits for a cycle where level 0 needs no write."""
    if any(o.level == 0 for o in cancels) or any(w[3] == 0 for w in places):
        return ([o for o in cancels if o.level == 0 or ladder_unsafe(o, want)], [w for w in places if w[3] == 0])
    return cancels, places
def add_lot(lots, x, t):
    """FIFO lots [signed shares, time]: same sign adds a lot, opposite sign closes the oldest first."""
    while x and lots and lots[0][0] * x < 0:
        take = min(abs(x), abs(lots[0][0]))
        lots[0][0] += take if lots[0][0] < 0 else -take
        x += -take if x > 0 else take
        if abs(lots[0][0]) < 1e-9:
            lots.pop(0)
    if abs(x) > 1e-9:
        lots.append([x, t])


def lot_age(lots, t):
    """Share-weighted age of the held lots, hours (Bot.age_hours)."""
    n = sum(abs(x) for x, _ in lots) if lots else 0.0
    return sum(abs(x) * (t - u) for x, u in lots) / n / 3600 if n else 0.0


def unload_window(sim, m, t):
    """Bot.note_unloads + Bot.unload_side for one market: (reducing side or None, shares to unload)."""
    cfg = sim.cfg
    if not cfg.fast_unload_enabled:
        return None, None
    st = m.state
    i = st.get("nf", 0)
    st["nf"] = len(sim.fills)                    # markets run one after another: new fills are this market's
    w = st.get("unload")
    for (ft, fm, sgn, q, price, fv, _lvl, _taker) in sim.fills[i:]:
        if fm is not m:
            continue
        side = "bid" if sgn > 0 else "ask"
        if w and w["side"] == side:
            w["left"] -= q
            continue
        if t - ft > cfg.fast_unload_seconds or (fv - price) * sgn < cfg.fast_unload_min_edge - 1e-9 \
                or q < cfg.fast_unload_min_shares or m.inv * sgn <= 0:
            continue                             # (position now: approximates "added" for fills since last step)
        left = min(q, abs(m.inv)) + (w["left"] if w else 0.0)
        w = {"until": ft + cfg.fast_unload_seconds, "side": "ask" if side == "bid" else "bid",
             "left": min(left, abs(m.inv))}
    if w and (t >= w["until"] or w["left"] < 1 or (m.inv < 1 if w["side"] == "ask" else m.inv > -1)):
        w = None
    st["unload"] = w
    if w:
        st["unload_s"] = st.get("unload_s", 0) + 1
    return (w["side"], int(w["left"] * cfg.fast_unload_size_mult)) if w else (None, None)


def quote_to_want(q):
    out = []
    if q.bid is not None and q.bid_size > 0:
        out.append((True, q.bid, q.bid_size, 0, q.bid_limit))
    if q.ask is not None and q.ask_size > 0:
        out.append((False, q.ask, q.ask_size, 0, q.ask_limit))
    return out


def make_cfg(overrides):
    cfg = Config()
    for k, v in (overrides or {}).items():
        cur = getattr(cfg, k)
        setattr(cfg, k, v in (True, "1", "true", "True") if isinstance(cur, bool) else
                tuple(v) if isinstance(cur, tuple) else type(cur)(v))
    return cfg


def _one(args):
    s, hours, regime, overrides, strategy = args
    ov = dict(overrides or {})
    share = float(ov.pop("_share", WRITE_SHARE))                # share of writes_per_minute for these markets
    rival_inv = bool(ov.pop("_rival_inv", True))               # rivals with inventory caps and skew
    bias_hl = float(ov.pop("_bias_hl", 0))                     # T1 prototype: fair value = Polymarket + EMA gap
    ladder = ov.pop("_ladder", None)                           # R3 prototype: 1 = LADDER, or a dict of changes
    rival_aware = float(ov.pop("_rival_aware", 0))             # rival-aware: step behind (price units) or 0
    ov_bg = ov.pop("_bg_cap", None)                            # positions held in the other markets (default 60k)
    lad_gate = float(ov.pop("_lad_gate", 0))                   # ladder only while free cash >= this x account
    fast_unload = ov.pop("_fast_unload", None)                 # 1 or dict(edge, off, secs)
    sim = Sim(s, hours, regime, make_cfg(ov), strategy, share=share, rival_inv=rival_inv)
    sim.bias_hl, sim.rival_aware = bias_hl, rival_aware
    sim.bg_cap = float(ov_bg) if ov_bg is not None else sim.bg_cap
    sim.lad_gate = lad_gate
    if fast_unload:
        sim.fast_unload = {**dict(edge=0.02, off=0.005, secs=300), **(fast_unload if isinstance(fast_unload, dict) else {})}
    if ladder:
        sim.ladder = {**LADDER, **(ladder if isinstance(ladder, dict) else {})}
    return sim.run()


def run_many(seeds, hours, regime, overrides=None, strategy=None, procs=None):
    jobs = [(s, hours, regime, overrides, strategy) for s in range(1, seeds + 1)]
    procs = procs or int(os.environ.get("SIM_PROCS", "4"))
    if procs > 1 and seeds > 1:
        import multiprocessing
        with multiprocessing.Pool(min(procs, seeds)) as pool:
            rows = pool.map(_one, jobs)
    else:
        rows = [_one(j) for j in jobs]
    agg = {}
    for k in rows[0]:
        vals = [r[k] for r in rows]
        agg[k] = round(sum(vals) / len(vals), 3)
    agg["pnl_p10"] = sorted(r["pnl"] for r in rows)[max(0, len(rows) // 10 - 1)] if rows else 0
    agg["dups_max"] = max(r["dups"] for r in rows)
    return agg, rows


def compare(seeds, hours, regime, base=None, new=None, base_strategy=None, new_strategy=None):
    """Paired comparison (same seeds = same world): returns (base agg, new agg, mean P&L gain, its std error)."""
    a, ra = run_many(seeds, hours, regime, base, base_strategy)
    b, rb = run_many(seeds, hours, regime, new, new_strategy)
    d = [y["pnl"] - x["pnl"] for x, y in zip(ra, rb)]
    mean = sum(d) / len(d)
    se = (sum((x - mean) ** 2 for x in d) / max(1, len(d) - 1)) ** 0.5 / len(d) ** 0.5
    return a, b, round(mean), round(se)


def fmt_row(name, a):
    return (f"{name:<28} fills {a['fills']:6.0f} sh {a['shares']:8.0f} edge {a['edge_c']:5.2f}c mk15 {a['markout15_c']:5.2f}c "
            f"pnl {a['pnl']:7.0f} (mid {a['pnl_mid']:7.0f}, p10 {a['pnl_p10']:6.0f}) dd {a['max_dd']:5.0f} worst {a['worst']:6.0f} "
            f"quoted {a['quoted']:.2f} w/mh {a['writes_mh']:5.1f} picked {a['picked_sh']:6.0f} dups {a['dups_max']} "
            f"best {a['at_best']:.2f} big {a['big_vol']:.2f}/{a['big_pnl']:.2f} defer/h {a['deferred']:.0f}")


def sweep(seeds, hours, regime, base, variants):
    """Each variant (dict of overrides on top of base) against base, same seeds. Prints one line each."""
    a, ra = run_many(seeds, hours, regime, base)
    print(fmt_row("BASE " + str(base)[:23], a))
    for v in variants:
        b, rb = run_many(seeds, hours, regime, {**base, **v})
        d = [y["pnl"] - x["pnl"] for x, y in zip(ra, rb)]
        mean = sum(d) / len(d)
        se = (sum((x - mean) ** 2 for x in d) / max(1, len(d) - 1)) ** 0.5 / len(d) ** 0.5
        print(fmt_row(str(v)[:28], b), f"| dPnL {mean:+.0f} +- {se:.0f}", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "sweep":
        # python tests/strategy_sim.py sweep SEEDS HOURS REGIME '{"base": 1}' '{"variant": 2}' ...
        import json
        sweep(int(args[1]), float(args[2]), args[3], json.loads(args[4]), [json.loads(v) for v in args[5:]])
        sys.exit(0)
    seeds = int(args[0]) if args else 5
    hours = float(args[1]) if len(args) > 1 else 2.0
    regime = args[2] if len(args) > 2 else "quiet"
    ov = dict(a.split("=", 1) for a in args[3:])
    agg, _ = run_many(seeds, hours, regime, ov)
    print(fmt_row(f"{regime} {ov or 'defaults'}", agg))
