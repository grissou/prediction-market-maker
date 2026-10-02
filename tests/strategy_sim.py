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

# Market kinds: (count per run, noise trades per hour, rival count, our size frac, headline?)
KINDS = {
    "headline": dict(n=2, rate=60, rivals=4, fast=3, headline=True),
    "busy": dict(n=4, rate=14, rivals=3, fast=2, headline=False),
    "quiet": dict(n=6, rate=4, rivals=2, fast=1, headline=False),
}
REGIMES = {
    # Polymarket jumps (>=1c) per market-hour; write latency (s) normal and slow share
    "quiet": dict(jumps=0.05, lat=(0.5, 2.0), slow_share=0.05),
    "news": dict(jumps=0.6, lat=(0.5, 2.0), slow_share=0.05, common=0.7, common_q=0.5),
    "slow": dict(jumps=0.05, lat=(0.5, 2.0), slow_share=0.7),
}
# Share of the bot's writes_per_minute these 12 markets get (the other ~225 markets use the rest). Writes are
# costed as live: one cancel request per market (both sides at once) or per order, new orders in batches of
# batch_size per cycle; pulls always go, the rest in the bot's priority order (see Bot.change_key).
WRITE_SHARE = 0.1


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
        self.cycle, self.wcap, self.wlog = cycle, self.cfg.writes_per_minute * share, []
        self.deferred = 0
        self.news = {}                              # common news: second -> direction
        reg = self.reg
        for t in range(self.T + 1):
            if self.nrng.random() < reg["jumps"] * reg.get("common", 0) / reg.get("common_q", 1) / 3600.0:
                self.news[t] = self.nrng.choice((-1, 1))
        self.bias_hl = 0.0
        self.fills, self.writes, self.dups, self.quoted_s, self.alive_s = [], 0, 0, 0, 0
        self.side_s, self.best_s = 0, 0          # our quoted side-seconds, and those at/inside the best other price
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
        bias = fl + r.gauss(0, 0.009)
        rivals = []
        for i in range(k["rivals"]):
            fast = i < k["fast"]
            rivals.append(Rival(f"r{i}", lag=r.uniform(1, 3) if fast else r.uniform(5, 30),
                                every=r.uniform(2, 10) if fast else r.uniform(30, 120),
                                floor=TICK * r.randint(0, 3), size=r.choice([100, 200, 500, 1000]) * (5 if k["headline"] else 1),
                                offset=r.gauss(0, 0.005)))
            if rival_inv_on:
                rivals[-1].cap = rivals[-1].size * self.nrng.uniform(2, 6)
                rivals[-1].skew = self.nrng.uniform(0.0025, 0.01)
        return Mkt(kind, p0, bias, k["rate"], k["headline"], rivals)

    def make_paths(self, m):
        """Polymarket path p[t] and consensus path c[t] for one market."""
        r, T = self.rng, self.T
        p, c = [0.0] * (T + 1), [0.0] * (T + 1)
        x, phi = 0.0, 0.5 ** (1 / 300.0)
        sd_x = 0.0058 * math.sqrt(1 - phi * phi)
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
            if o.owner == "us":
                side = 1 if o.is_bid else -1
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
        bid = min(floor_tick(f - rv.floor - TICK), floor_tick(bb + TICK) if bb is not None else floor_tick(f - 0.02))
        ask = max(ceil_tick(f + rv.floor + TICK), ceil_tick(ba - TICK) if ba is not None else ceil_tick(f + 0.02))
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
        if self.rng.random() < 0.08:
            q = math.exp(self.rng.gauss(math.log(2000), 0.6)) * (3 if m.headline else 1)
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
        if self.bias_hl and bfv is not None:
            # T1 prototype: Polymarket + exponentially weighted average of (book price - Polymarket)
            k = 1 - 0.5 ** (self.cycle / self.bias_hl)
            g = m.state.get("gap")
            m.state["gap"] = bfv - ref if g is None else g + k * ((bfv - ref) - g)
            fv = ref + max(-0.03, min(0.03, m.state["gap"]))
        want = [] if t < m.cooldown_until else self.strategy(self, m, t, fv, bfv, ref, book)
        mine = [o for o in m.orders if o.owner == "us"]
        cancels, places = plan_changes(cfg, mine, want, t, m)
        if not cancels and not places:
            return None
        urgent = t - m.state.get("ref_moved_at", -99) < 15
        key = (0 if not places else 0.5 if urgent else 1, 0 if m.headline else 1, 1 if cancels else 0,
               -max([w[2] for w in places] or [0]))
        return key, m, cancels, places, fv, len(cancels) == len(mine)

    def our_cycle(self, t, paths):
        """One bot cycle over all markets: plan each, then send in priority order within the write budget."""
        self.wlog = [x for x in self.wlog if t - x[0] < 60]
        spare = self.wcap - sum(c for _, c in self.wlog)
        plans = sorted((x for x in (self.our_step(m, t, p) for m, p, c in paths) if x),
                       key=lambda x: x[0])
        bs, n, used = self.cfg.batch_size, 0, 0.0
        for i, (key, m, cancels, places, fv, whole) in enumerate(plans):
            c = (1 if whole else len(cancels)) if cancels else 0
            c += math.ceil((n + len(places)) / bs) - math.ceil(n / bs)
            if used + c > spare and key[0] != 0:
                self.deferred += len(plans) - i          # everything after the first misfit waits
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
                self.alive_s += 1
                if t % 60 == 0:
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
        fpnl = [f[2] * f[3] * (pend[id(f[1])] - f[4]) for f in self.fills]
        big = [i for i, f in enumerate(self.fills) if f[2] * (f[5] - f[4]) > 0.03]
        big_sh = sum(self.fills[i][3] for i in big)
        tot = sum(fpnl)
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
                    big_pnl=round(sum(fpnl[i] for i in big) / tot, 2) if tot > 0 else 0.0)


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


def plan_changes(cfg, mine, want, t, m=None):
    """Reconcile our resting orders with the wanted list [(is_bid, price, size, level)]: same rules as
    Bot.reconcile (keep an order a tick off if still safe, churn control, cancel before placing).
    Returns (cancels, places)."""
    cancels, places = [], []
    for lvl_key in {(w[0], w[3]) for w in want} | {(o.is_bid, o.level) for o in mine}:
        is_bid, lvl = lvl_key
        cur = [o for o in mine if o.is_bid == is_bid and o.level == lvl]
        w = next((w for w in want if w[0] == is_bid and w[3] == lvl), None)
        price, size, limit = (w[1], w[2], w[4] if len(w) > 4 else None) if w else (None, 0, None)
        rest = [Resting(0, "", o.is_bid, o.price, o.qty, None) for o in cur]
        if side_needs_change(rest, price, size, cfg, None, limit, is_bid=is_bid) and not hold(cfg, m, cur, w, t):
            cancels += cur
            if w is not None:
                places.append((is_bid, price, size, lvl))
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
    q = M.compute_quote(fv, m.inv, m.inv, bb, ba, cfg, no_bid=no_bid, no_ask=no_ask,
                        kelly_p=None if m.headline else ref, order_size=size, position_limit=plimit)
    return quote_to_want(q)


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
    sim = Sim(s, hours, regime, make_cfg(ov), strategy, share=share, rival_inv=rival_inv)
    sim.bias_hl = bias_hl
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
