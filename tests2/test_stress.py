"""
Stress run: long randomised sessions on the fake exchange with faults on any request. The bot never crashes, never
crosses itself and keeps its caps; once the faults stop it recovers and its view matches the exchange.

World     eight races, 17 markets: middle-band races for market making, a 3-leg race, longshots (p <= 10%) and
          favourites (p >= 90%) for the ladder and the value quotes, a national race (no state), two races in Ohio
          for the per-state cap, and starting positions for the allocator and the recycler. Caps are small so they
          bind. Books sit a few cents rich in the tails (the tilt), so the ladder finds edge.
Each cycle (seeded)  random fail_rate on reads and writes, fail_next bursts, place batches and cancels whose outcome
          is lost (done on the exchange, reported as unknown / raised), other traders filling our resting orders,
          books and Polymarket moving (sometimes jumping), the feed pushing dirty markets / resyncs / going
          unhealthy, tight request and write budgets, the simulated clock advancing (seconds to 20 minutes, so the
          allocator's hour, the ladder's requote and order expiry happen), and now and then a settings-file edit.
After every cycle  no bug escaped the cycle (ApiErrors are expected; anything else is recorded with its traceback),
          the fake counted no self-cross, no resting bid at or above our own resting ask, cash never negative; and
          after a clean cycle that did a full REST check: bot.orders equals the fake's resting orders and the caps
          hold (see check_caps for exactly what the bot can guarantee).
Then      faults off: within a few cycles orders rest again, the bot's orders and positions match the fake, and a
          quiet cycle sends nothing.
Run:  python3 tests2/test_stress.py [cycles_per_seed] [seeds]      (default 1000 x 4; exit code 0 = all passed)
"""
import json
import logging
import os
import random
import sys
import tempfile
import time as real_time
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes                                                         # noqa: E402
from fakes import UNLIMITED, FakeClient, FakeFeed, FakeRefs, raw_market   # noqa: E402
from mmbot2 import bot as bot_module, config, exchange, ops, value    # noqa: E402
from mmbot2.bot import Bot                                           # noqa: E402
from mmbot2.exchange import ApiError, Placed, parse_markets          # noqa: E402
from mmbot2.state import ceil_tick, floor_tick, held_usd             # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


class Clock:
    """One simulated clock for the bot (utcnow, time.monotonic), value.py (time.time) and the fake (expiry, the
    account's read time), so hours of trading run in seconds."""
    def __init__(self):
        self.mono, self.wall = 10000.0, datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    def monotonic(self): return self.mono
    def time(self): return self.wall.timestamp()
    def now(self): return self.wall
    def sleep(self, s): self.advance(s)
    def advance(self, s):
        self.mono += s
        self.wall += timedelta(seconds=s)


CLOCK = Clock()
bot_module.utcnow, bot_module.time, value.time, fakes.time, exchange.time = CLOCK.now, CLOCK, CLOCK, CLOCK, CLOCK


class StressClient(FakeClient):
    """The fake exchange plus lost outcomes: a batch that IS placed but answers unknown, a cancel that IS done but
    raises. Also logs the tags the bot sends, for the coverage checks."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.lose_places = self.lose_cancels = 0
        self.cancels_fail = False         # every cancel this cycle raises and cancels nothing
        self.tags_sent = set()

    def clock(self):
        return CLOCK.wall

    def place(self, orders, positions):
        self.tags_sent |= {o.tag for o in orders}
        if self.lose_places <= 0:
            return super().place(orders, positions)
        self.lose_places -= 1
        super().place(orders, positions)
        return [Placed(o, None, 0.0, "response lost", unknown=True) for o in orders]

    def cancel(self, oid):
        if self.cancels_fail:
            self.calls.append("cancel")
            raise ApiError(503, "INJECTED", "cancel failed")
        if self.lose_cancels <= 0:
            return super().cancel(oid)
        self.lose_cancels -= 1
        super().cancel(oid)
        raise ApiError(504, "GATEWAY_TIMEOUT", "cancel response lost")


# race -> (state for the label, {party: Polymarket probability})
RACES = {
    "Ohio Senate": {"Republican": 0.55, "Democratic": 0.45},
    "Ohio Governor": {"Republican": 0.68, "Democratic": 0.32},          # second Ohio race: the state cap
    "Texas Senate": {"Republican": 0.93, "Democratic": 0.07},           # favourite / longshot
    "Utah Senate": {"Republican": 0.96, "Democratic": 0.04},
    "Rhode Island Governor": {"Republican": 0.05, "Democratic": 0.95},
    "Maine Senate": {"Republican": 0.42, "Democratic": 0.34, "Independent": 0.24},   # three legs
    "Florida Senate": {"Republican": 0.78, "Democratic": 0.22},
    "U.S. Senate": {"Republican": 0.60, "Democratic": 0.40},            # national: no state, pinned
}
BASE_SETTINGS = {"market_max_usd": 2500.0, "state_max_usd": 4000.0, "mm_reserve_usd": 4000.0,
                 "harvest_level_usd": 500.0, "harvest_total_usd": 2500.0, "alloc_max_turnover_usd": 3000.0,
                 "mm_risk_reserve_wc": 2000.0, "mm_risk_reserve_corr": 500.0, "mm_inv_max_usd": 800.0,
                 "mm_inv_max_age_h": 1.0}
START, CASH = 30000.0, 21000.0


# Books off Polymarket in one race: Rep cheap, Dem rich, so the allocator has a buy at >= 5% edge and a holding
# (Dem Florida, held long) worth selling into a bid above its value.
BOOK_OFFSET = {("Florida Senate", "Republican"): -0.07, ("Florida Senate", "Democratic"): 0.07}


def house_book(p, off=0.0):
    """Other traders' book around fair value p: rich in the tails (the favourite-longshot tilt), 6-8c wide."""
    mid = off + (p + 0.03 if p < 0.15 else p - 0.03 if p > 0.85 else p)
    half = 0.03 if 0.15 <= p <= 0.85 else 0.025
    bid, ask = floor_tick(mid - half), ceil_tick(mid + half)
    bids = [(bid, 800), (round(bid - 0.02, 3), 2000)]
    asks = [(ask, 800), (round(ask + 0.02, 3), 2000)]
    return {"bids": [b for b in bids if b[0] >= 0.005], "asks": [a for a in asks if a[0] <= 0.995]}


class World:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        raws, self.key, self.truth, n = [], {}, {r: dict(legs) for r, legs in RACES.items()}, 0
        for race, legs in RACES.items():
            for party in legs:
                n += 1
                raws.append(raw_market(str(n), str(100 + n), party, race))
        markets = [m for r in raws for m in parse_markets(r, None)]
        self.eids = {(m.race, m.party): m.eid for m in markets}
        books = {self.eids[(r, party)]: house_book(p, BOOK_OFFSET.get((r, party), 0.0))
                 for r, legs in self.truth.items() for party, p in legs.items()}
        self.client = StressClient(markets, books, cash=CASH, start=START)
        self.client.rng = random.Random(seed + 1)
        e = self.eids
        self.client.inv = {e[("Texas Senate", "Republican")]: 3000.0,        # a favourite held long
                           e[("Rhode Island Governor", "Republican")]: -2500.0,   # a longshot held short
                           e[("Ohio Senate", "Republican")]: 2000.0,         # Ohio already part used
                           e[("Florida Senate", "Democratic")]: 1500.0}
        self.feed, self.refs = FakeFeed(), FakeRefs(self.ref_prices())
        d = tempfile.mkdtemp(prefix="mm2stress")
        self.env = config.Env("k", "t", "http://x", "", d, os.path.join(d, "settings_override.json"))
        self.edits, self.mtime = {}, 1.0e9
        self.write_settings()
        s = config.SettingsFile(self.env.settings_path).load(config.Settings())
        self.bot = Bot(self.client, self.feed, self.refs, s, self.env, True)
        self.bot.load_markets(CLOCK.mono)
        self.runner = ops.Runner(self.bot, self.env)
        self.bugs, self.raised = [], 0
        real_cycle = self.bot.cycle

        def cycle():                     # ApiErrors are the faults; anything else is a bug, kept with its traceback
            try:
                real_cycle()
            except ApiError:
                self.raised += 1
                raise
            except Exception:
                self.raised += 1
                self.bugs.append(traceback.format_exc())
                raise
        self.bot.cycle = cycle

    def ref_prices(self):
        return {f"{r}|{party}": p for r, legs in self.truth.items() for party, p in legs.items()}

    def write_settings(self):
        with open(self.env.settings_path, "w") as f:
            json.dump({**BASE_SETTINGS, **self.edits}, f)
        self.mtime += 10                                  # a distinct mtime, however fast the edits come
        os.utime(self.env.settings_path, (self.mtime, self.mtime))

    # ---------------------------------------------------------------- the world moving between cycles
    def move_race(self, race, jump=False, news=False):
        """Polymarket drifts (or jumps) for one race and the house books follow, hitting our orders they cross. On
        news Polymarket moves 10c first and the books stay put until the next move (our quotes must turn round)."""
        legs = self.truth[race]
        first = next(iter(legs))
        step = self.rng.gauss(0, 0.01)
        if jump or news:
            step = self.rng.choice((-1, 1)) * (0.17 if jump else 0.10)
        legs[first] = min(0.97, max(0.03, legs[first] + step))
        rest = sum(p for k, p in legs.items() if k != first)
        for k in legs:
            if k != first:
                legs[k] = max(0.01, legs[k] / rest * (1 - legs[first]))
        self.refs.prices = self.ref_prices()
        if news:
            return []
        dirty = []
        for party, p in legs.items():
            eid = self.eids[(race, party)]
            self.client.others[eid] = house_book(p, BOOK_OFFSET.get((race, party), 0.0))
            self.hit_crossed(eid)
            dirty.append(eid)
        return dirty

    def hit_crossed(self, eid):
        """A house order that moved through one of our resting orders trades with it (the exchange would match)."""
        book = self.client.others[eid]
        for is_bid, side in ((True, "asks"), (False, "bids")):
            while True:
                ours = [o for o in self.client.orders.values() if o.eid == eid and o.is_bid == is_bid]
                if not ours or not book[side]:
                    break
                best = max(ours, key=lambda o: o.price if is_bid else -o.price)
                if (best.price < book[side][0][0]) if is_bid else (best.price > book[side][0][0]):
                    break
                self.client.fill(eid, is_bid, best.size)

    def traders_hit_us(self):
        """Other traders fill some of our resting orders; the feed usually (not always) says the account changed."""
        for o in list(self.client.orders.values()):
            if o.oid in self.client.orders and self.rng.random() < 0.04:
                self.client.fill(o.eid, o.is_bid, max(1, int(o.size * self.rng.choice((0.2, 0.5, 1.0)))))
                if self.rng.random() < 0.8:
                    self.feed.push(account=True)

    def arm_faults(self, rng):
        """This cycle's faults; False if it has none (a clean cycle)."""
        c = self.client
        c.fail_rate, c.faults, c.lose_places, c.lose_cancels, c.cancels_fail = 0.0, [], 0, 0, False
        c.req_left = c.write_left = UNLIMITED
        if rng.random() < 0.35:
            return False
        c.fail_rate = rng.choice((0.0, 0.03, 0.1, 0.25))
        if rng.random() < 0.3:
            c.fail_next(rng.randint(1, 4), rng.choice(("read", "write", "any")), rng.choice((429, 500, 503)))
        if rng.random() < 0.15:
            c.lose_places = 1
        if rng.random() < 0.15:
            c.lose_cancels = 1
        c.cancels_fail = rng.random() < 0.08
        if rng.random() < 0.1:
            c.req_left = rng.choice((5, 15, 25))           # the request budget nearly spent
        if rng.random() < 0.1:
            c.write_left = rng.choice((0, 1, 3))           # the write budget nearly spent
        return True

    def step(self, rng):
        """Move the world, arm this cycle's faults, advance the clock. Returns True if the cycle is fault-free."""
        dirty = []
        for race in self.truth:
            if rng.random() < 0.3:
                dirty += self.move_race(race, jump=rng.random() < 0.01, news=rng.random() < 0.04)
        self.traders_hit_us()
        self.feed.ok = (rng.random() < 0.9) if self.feed.ok else (rng.random() < 0.4)
        if rng.random() < 0.8:
            self.feed.push(dirty=dirty)
        if rng.random() < 0.03:
            self.feed.push(resync=True)
        if rng.random() < 0.02:
            self.edit_settings(rng)
        CLOCK.advance(rng.choice((5, 10, 20, 35, 60)) if rng.random() < 0.93 else rng.uniform(600, 1300))
        return not self.arm_faults(rng)

    def edit_settings(self, rng):
        name, val = rng.choice((("market_max_usd", rng.choice((1500.0, 2500.0, 3500.0))),
                                ("state_max_usd", rng.choice((3000.0, 4000.0, 6000.0))),
                                ("mm_enabled", rng.random() < 0.7), ("ladder_enabled", rng.random() < 0.8),
                                ("harvest_requote_s", rng.choice((300.0, 900.0))),
                                ("full_check_s", rng.choice((20.0, 30.0, 60.0)))))
        self.edits[name] = val
        self.write_settings()


# ---------------------------------------------------------------- the invariants
def own_cross(client):
    """A market where our resting bid is at or above our resting ask, or None."""
    for e in {o.eid for o in client.orders.values()}:
        bids = [o.price for o in client.orders.values() if o.eid == e and o.is_bid]
        asks = [o.price for o in client.orders.values() if o.eid == e and not o.is_bid]
        if bids and asks and max(bids) >= min(asks) - 1e-9:
            return e
    return None


def check_caps(w):
    """The first cap breach on the fake's real book, or None.

    What the bot can guarantee: its Gate admits every order (kept ones first) against the positions it read and the
    risk prices p of this cycle; so straight after a clean cycle with a full REST check, valued at that cycle's
    bot.risk.p, (a) each side of a market, if every resting order on it filled (the holding counted on the side it
    grows), ties up at most market_max_usd, and (b) each state's positions plus the adding part of every resting order
    there (both sides, as the gate counts them) is at most state_max_usd. Fills by other traders cannot break either:
    a fill only turns resting collateral into held collateral. What fills and moving prices CAN do is put a holding
    alone over a cap; then the bound is the holding itself (no adding order may rest). Under faults no bound is
    claimed: a cancel that failed leaves an order the gate refused until the next cycle can cancel it. Tolerance:
    one share's collateral per order (the gate floors shares) plus a cent."""
    if w.bot.risk is None:
        return None
    s, p, c = w.bot.s, w.bot.risk.p, w.client
    state_held, state_add, n_orders = {}, {}, {}
    for e, m in w.bot.markets.items():
        q, pe = c.inv.get(e, 0.0), p.get(e)
        if pe is None:
            continue
        B = sum(o.size for o in c.orders.values() if o.eid == e and o.is_bid)
        A = sum(o.size for o in c.orders.values() if o.eid == e and not o.is_bid)
        k = sum(1 for o in c.orders.values() if o.eid == e)
        tol = 0.01 + k * 1.0
        for name, total, held in (("long", max(0.0, q + B) * pe, max(0.0, q) * pe),
                                  ("short", max(0.0, -q + A) * (1 - pe), max(0.0, -q) * (1 - pe))):
            if total > max(s.market_max_usd, held) + tol:
                return f"{m.label} {name}: {total:.0f} if every order fills > cap {s.market_max_usd:.0f}"
        if m.state is not None:
            state_held[m.state] = state_held.get(m.state, 0.0) + held_usd(q, pe)
            add = max(0.0, B - max(0.0, -q)) * pe + max(0.0, A - max(0.0, q)) * (1 - pe)
            state_add[m.state] = state_add.get(m.state, 0.0) + add
            n_orders[m.state] = n_orders.get(m.state, 0) + k
    for st, held in state_held.items():
        total = held + state_add[st]
        if total > max(s.state_max_usd, held) + 0.01 + n_orders[st]:
            return f"state {st}: {total:.0f} (held {held:.0f}) > cap {s.state_max_usd:.0f}"
    return None


def orders_mismatch(w):
    """'' if the bot's orders are exactly the fake's resting orders, else what differs."""
    mine = {k: (o.eid, o.is_bid, o.price, o.size) for k, o in w.bot.orders.items()}
    theirs = {k: (o.eid, o.is_bid, o.price, o.size) for k, o in w.client.orders.items()}
    if mine == theirs:
        return ""
    return f"bot only {sorted(set(mine) - set(theirs))}, fake only {sorted(set(theirs) - set(mine))}, " \
           f"differ {[k for k in mine if k in theirs and mine[k] != theirs[k]]}"


def writes(client):
    return sum(1 for c in client.calls if c in ("place", "cancel", "cancel_all"))


# ---------------------------------------------------------------- one session
def run_seed(seed, cycles):
    rng, w = random.Random(seed), World(seed)
    first = {}                                    # check name -> first failure detail
    clean_full, fills_before = 0, len(w.client.fill_log)

    def note(name, bad):
        if bad and name not in first:
            first[name] = bad

    for i in range(cycles):
        clean = w.step(rng)
        reads_before = w.client.calls.count("open_orders")
        try:
            w.runner.run_cycle()
        except Exception as e:                    # a fatal ApiError or a bug in the runner itself
            note("cycle", f"cycle {i}: {e!r}")
        c = w.client
        note("self", c.self_crosses and f"cycle {i}: {c.self_crosses} self-crosses")
        note("cross", own_cross(c) and f"cycle {i}: market {own_cross(c)}")
        note("cash", (c.cash < -1e-6 or c.cash - c.locked() < -1e-6) and f"cycle {i}: cash {c.cash:.2f}")
        if not w.bot.running:
            note("running", f"cycle {i}: the bot stopped (killed={w.bot.killed})")
            break
        full = clean and c.calls.count("open_orders") > reads_before and w.runner.errors == 0
        if full:
            clean_full += 1
            note("caps", (check_caps(w) or "") and f"cycle {i}: {check_caps(w)}")
            note("agree", orders_mismatch(w) and f"cycle {i}: {orders_mismatch(w)}")

    tag = f"seed {seed}"
    check(f"{tag}: no bug escaped the cycle ({w.raised} cycles raised, all ApiErrors)", not w.bugs,
          w.bugs[0][-1500:] if w.bugs else "")
    if w.bugs:
        print(w.bugs[0])
    check(f"{tag}: every cycle ran under the Runner and the bot kept running", not first.get("cycle")
          and not first.get("running"), first.get("cycle", "") + first.get("running", ""))
    check(f"{tag}: the fake counted no self-cross", "self" not in first, first.get("self", ""))
    check(f"{tag}: never a resting bid at or above our own resting ask", "cross" not in first, first.get("cross", ""))
    check(f"{tag}: cash never negative", "cash" not in first, first.get("cash", ""))
    check(f"{tag}: per-market and per-state caps hold after every clean full check ({clean_full} of them)",
          "caps" not in first and clean_full >= cycles // 10, first.get("caps", f"{clean_full} clean full checks"))
    check(f"{tag}: bot.orders equals the fake's after every clean full check", "agree" not in first,
          first.get("agree", ""))
    check(f"{tag}: the session was a stress (faults raised cycles, traders filled us)",
          w.raised >= cycles // 20 and len(w.client.fill_log) > fills_before + 10,
          f"raised {w.raised}, fills {len(w.client.fill_log) - fills_before}")
    wanted_tags = {"alloc", "ladder", "value", "mm"}
    check(f"{tag}: the strategies all traded ({sorted(w.client.tags_sent)})", wanted_tags <= w.client.tags_sent,
          f"missing {sorted(wanted_tags - w.client.tags_sent)}")
    recover(w, tag)


def recover(w, tag):
    """Faults off, the feed healthy: within a few cycles the bot quotes again and its view matches the fake."""
    c = w.client
    c.fail_rate, c.faults, c.lose_places, c.lose_cancels, c.cancels_fail = 0.0, [], 0, 0, False
    c.req_left = c.write_left = UNLIMITED
    w.feed.ok = True
    w.feed.push(resync=True)
    w.edits = {}
    w.write_settings()
    for _ in range(4):
        CLOCK.advance(40)
        w.runner.run_cycle()
    check(f"{tag}: recovery: cycles run without error", w.runner.errors == 0 and w.bot.running)
    check(f"{tag}: recovery: orders rest again ({len(c.orders)})", len(c.orders) > 0)
    check(f"{tag}: recovery: bot.orders equals the fake's", not orders_mismatch(w), orders_mismatch(w))
    pos = {e: q for e, q in c.inv.items() if q}
    check(f"{tag}: recovery: bot.positions equals the fake's",
          {e: q for e, q in w.bot.positions.items() if q} == pos)
    check(f"{tag}: recovery: caps hold", check_caps(w) is None, check_caps(w) or "")
    CLOCK.advance(2)
    before = writes(c)
    w.runner.run_cycle()
    check(f"{tag}: recovery: a quiet cycle sends nothing", writes(c) == before,
          str([x for x in c.calls[-8:]]))


if __name__ == "__main__":
    if os.environ.get("PYTHONHASHSEED") != "0":     # set order (the bot iterates sets) must repeat with the seed
        os.execve(sys.executable, [sys.executable] + sys.argv, {**os.environ, "PYTHONHASHSEED": "0"})
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    t0 = real_time.time()
    for seed in range(1, seeds + 1):
        try:
            run_seed(seed, cycles)
        except Exception as e:                     # a crash in the harness is a failure of that seed
            traceback.print_exc()
            check(f"seed {seed} ran without an exception", False, repr(e))
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed in {real_time.time() - t0:.0f} s")
    sys.exit(0 if all(RESULTS) else 1)
