"""
Randomised stress test: a long simulated trading session against the fake exchange, with everything
going wrong that can go wrong on the day, while checking after EVERY cycle that nothing that must
never happen does.

What it throws at the bot (all at random, reproducible from the seed):
  - rivals moving the books, pennying our quotes, pulling a side, bidding high enough for arbitrage
  - other traders filling our quotes, with the realtime fill message sometimes lost
  - Polymarket drifting and sometimes jumping 5c (guards, weighting, takes)
  - a flaky API: 503s on any request, order batches that fail before placing, batches that DO place
    but whose response is lost, cancels that fail, an open-orders list that lags (new orders missing,
    cancelled ones still shown)
  - the realtime feed dropping and coming back (resync)
  - the last part of the session inside the election-night exit window

What must never happen (checked after every cycle):
  - an unexpected exception (a bug: API errors are expected and handled)
  - two of our orders on the same side of one exchange (duplicate quotes)
  - our own bid at or above our own ask on one exchange
  - positions running away past the risk limits (worst-case loss, national swing)
And once the faults stop:
  - the bot's own record of its orders matches the exchange exactly
  - no exchange is left stuck (paused / pending), and a quiet cycle changes nothing

Run:  python tests/test_stress.py [steps_per_seed] [seeds]      (default 1500 x 5; exit code 0 = all passed)
"""
import random
import sys
import time as real_time
import traceback

from fakes import FakeApi, FakeFeed, FakeRefs, lvl, market   # noqa: F401  (sets up the path + env)
import mm_bot as M
from mm_bot import ApiError, Bot, Config, PARTY_SIGN, timedelta, utcnow
import os
import tempfile

RESULTS = []
real_utcnow = M.utcnow


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


class Clock:
    """Stands in for the `time` module inside mm_bot (and for mm_bot.utcnow), so the simulated session
    covers hours of heartbeats, cooldowns, grace periods and order expiry in a few seconds."""
    def __init__(self):
        self.t = self.t0 = real_time.monotonic()
        self.base = M.datetime.now(M.timezone.utc)
    def utcnow(self): return self.base + timedelta(seconds=self.t - self.t0)
    def monotonic(self): return self.t
    def time(self): return real_time.time()
    def sleep(self, s): self.t += s
    def gmtime(self, *a): return real_time.gmtime(*a)
    def advance(self, s): self.t += s


class FlakyApi(FakeApi):
    """The fake exchange, plus faults at `rate` (0 = a perfect exchange)."""
    def __init__(self, rng):
        super().__init__(True)
        self.rng, self.rate = rng, 0.0
        # Open-orders list lag, lasting into the NEXT step's reads: new orders missing, cancelled ones still shown.
        self.lag_hide, self.ghosts, self.next_hide, self.next_ghosts = set(), [], set(), []
        self.faults = 0

    def fault(self, weight=1.0):
        hit = self.rng.random() < self.rate * weight
        self.faults += hit
        return hit

    def new_step(self):
        # What changed during the last step may be missing from / still in the list during this one;
        # after that the list has caught up (a few seconds, well inside recent_order_grace_seconds).
        self.lag_hide, self.ghosts, self.next_hide, self.next_ghosts = self.next_hide, self.next_ghosts, set(), []

    def positions(self):
        if self.fault(0.2):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky positions")
        return super().positions()

    def open_orders(self, tid, eid=None):
        if self.fault(0.2):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky orders")
        out = [o for o in super().open_orders(tid, eid) if o["id"] not in self.lag_hide]
        return out + [dict(g) for g in self.ghosts if eid is None or g["exchangeId"] == eid]

    def pnl(self):
        if self.fault(0.2):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky pnl")
        return super().pnl()

    def book(self, eid, tid):
        if self.fault(0.3):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky book")
        return super().book(eid, tid)

    def bulk_prices(self, eids, tid):
        if self.fault(0.3):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky bulk")
        return super().bulk_prices(eids, tid)

    def fills_page(self, cursor=None):
        if self.fault(0.2):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky fills")
        return super().fills_page(cursor)

    def cancel_order(self, oid):
        if self.fault(0.5):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky cancel")      # NOT cancelled
        o = self.orders.get(oid)
        super().cancel_order(oid)
        if o and self.fault(1.0):
            self.next_ghosts.append(dict(o))                              # the list still shows it a moment
        return True

    def cancel_all(self, tid, eid=None):
        if self.fault(0.3):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky cancel-all")  # NOT cancelled
        gone = [dict(o) for o in self.orders.values() if eid is None or o["exchangeId"] == eid]
        super().cancel_all(tid, eid)
        if gone and self.fault(1.0):
            self.next_ghosts += gone
        return True

    def place_batch(self, orders):
        if self.fault(0.3):
            raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky batch")       # nothing placed
        before = set(self.orders)
        res = super().place_batch(orders)
        new = set(self.orders) - before
        if self.fault(0.3):
            raise ApiError(0, "NETWORK", "response lost")                   # placed, but we never hear back
        if self.fault(1.0):
            self.next_hide |= new                                         # not in the open-orders list yet
        for r in res:                                                     # the odd single order rejected
            if self.fault(0.2):
                oid = r["data"].get("orderId")
                self.orders.pop(oid, None)
                r.update(ok=False, status=400, data={"error": {"code": "VALIDATION_ERROR", "message": "flaky"}})
        return res


def build(seed):
    rng = random.Random(seed)
    api = FlakyApi(rng)
    races = [f"Race {i}" for i in range(8)]
    truth = {r: rng.uniform(0.08, 0.92) for r in races}                  # Republican win probability
    mk = []
    for i, r in enumerate(races):
        mk.append(market(str(2 * i + 1), str(100 + 2 * i), "Republican", r))
        mk.append(market(str(2 * i + 2), str(101 + 2 * i), "Democratic", r))
    api.markets_list = mk
    cfg = Config()
    d = tempfile.mkdtemp()
    cfg.fills_csv, cfg.status_file, cfg.order_notes_file, cfg.kill_file = (
        os.path.join(d, n) for n in ("fills.csv", "status.json", "notes.json", "kill.tripped"))
    cfg.record_file, cfg.ref_map_file, cfg.daily_summary_hour_utc, cfg.realtime_enabled = "", "", -1, False
    cfg.slow_poll_seconds, cfg.reserved_cash_mode = 30.0, "ignore"
    cfg.parallel_requests = 1             # one request thread: the random faults then fire in a repeatable order
    eids = {}
    for m in mk:
        x = M.RACE_TITLE.match(m["title"])
        eids[m["exchanges"][0]["id"]] = (x.group(2), x.group(1))
    api.books = {}
    bot_holder = {}

    def house(eid):
        race, party = eids[eid]
        p = truth[race] if party == "Republican" else 1 - truth[race]
        bid, ask = M.floor_tick(p - 0.04), M.ceil_tick(p + 0.04)
        return {"bids": [lvl(bid, 1000)] if bid > M.PMIN else [], "asks": [lvl(ask, 1000)] if ask < M.PMAX else []}

    for eid in eids:
        api.books[eid] = house(eid)
    bot = Bot(api, cfg)
    bot.feed = FakeFeed()
    bot.refs = FakeRefs({f"{r}|Republican": truth[r] for r in races} | {f"{r}|Democratic": 1 - truth[r] for r in races})
    bot_holder["bot"] = bot
    return rng, api, bot, truth, eids, house


def our_orders_by_side(api):
    sides = {}
    api.expire()
    for o in api.orders.values():
        is_bid, p = api.yes_view(o)
        sides.setdefault((o["exchangeId"], is_bid), []).append(p)
    return sides


def run_seed(seed, steps):
    clock = Clock()
    M.time, M.utcnow = clock, clock.utcnow
    rng, api, bot, truth, eids, house = build(seed)
    problems, errors_handled, cycles = [], 0, 0
    feed = bot.feed

    def step(faulty):
        nonlocal errors_handled, cycles
        clock.advance(rng.uniform(0.5, 6.0))
        api.new_step()
        dirty, account = set(), False
        # Polymarket drifts; sometimes a race jumps 5c. The house follows Polymarket, sometimes late.
        if rng.random() < 0.8:
            moves = {}
            for r in truth:
                old = truth[r]
                truth[r] = min(0.97, max(0.03, truth[r] + rng.gauss(0, 0.004) + (rng.choice((-0.05, 0.05)) if rng.random() < 0.01 else 0)))
                moves[f"{r}|Republican"] = moves[f"{r}|Democratic"] = abs(truth[r] - old)
            prices = {f"{r}|Republican": truth[r] for r in truth} | {f"{r}|Democratic": 1 - truth[r] for r in truth}
            bot.refs.new_reading(prices, moves)
        for eid in eids:
            if rng.random() < 0.05:                                       # the house re-quotes
                api.books[eid] = house(eid); dirty.add(eid)
        # Rivals: penny us, pull a side, or bid high (arbitrage bait).
        for _ in range(rng.randint(0, 3)):
            eid = rng.choice(list(eids))
            b = api.books[eid]
            r = rng.random()
            if r < 0.4 and b["bids"]:
                b["bids"].insert(0, lvl(min(M.PMAX - 0.01, round(b["bids"][0]["price"] + 0.005, 3)), rng.randint(10, 400)))
            elif r < 0.8 and b["asks"]:
                b["asks"].insert(0, lvl(max(M.PMIN + 0.01, round(b["asks"][0]["price"] - 0.005, 3)), rng.randint(10, 400)))
            elif r < 0.9:
                b[rng.choice(("bids", "asks"))] = []
            else:
                race, party = eids[eid]
                p = truth[race] if party == "Republican" else 1 - truth[race]
                b["bids"].insert(0, lvl(M.floor_tick(min(0.99, p + 0.03)), rng.randint(50, 300)))
            if b["bids"] and b["asks"] and b["bids"][0]["price"] >= b["asks"][0]["price"]:
                api.books[eid] = house(eid)                               # keep the rival book uncrossed
            dirty.add(eid)
        # Other traders fill our quotes.
        for o in list(api.orders.values()):
            if rng.random() < 0.03:
                is_bid, _ = api.yes_view(o)
                api.fill(o["exchangeId"], is_bid, rng.randint(1, int(o["quantity"])))
                account = True; dirty.add(o["exchangeId"])
        # The feed: drops sometimes, loses messages sometimes.
        if faulty and rng.random() < 0.02:
            feed.ok = not feed.ok
            if feed.ok:
                feed.push(resync=True)
        if feed.ok and not (faulty and rng.random() < 0.1):
            feed.push(dirty=dirty, account=account)
        try:
            if clock.monotonic() - bot.last_reload > bot.cfg.market_reload_seconds:
                bot.load_markets()
            bot.cycle()
            bot.failed_cycles, bot.pulled_after_errors = 0, False
        except ApiError:
            errors_handled += 1
            bot.on_cycle_error("stress", pull_now=False)
        except SystemExit as e:
            problems.append(f"step {cycles}: bot stopped itself (exit {e.code})")
        except Exception:
            problems.append(f"step {cycles}: unexpected exception\n{traceback.format_exc()}")
        cycles += 1
        # --- invariants ---
        sides = our_orders_by_side(api)
        dup = {k: v for k, v in sides.items() if len(v) > 1}
        if dup:
            problems.append(f"step {cycles}: duplicate quotes {dup}")
        for eid in eids:
            bids, asks = sides.get((eid, True), []), sides.get((eid, False), [])
            if bids and asks and max(bids) >= min(asks) - 1e-9:
                problems.append(f"step {cycles}: our own quotes cross on {eid}: bid {max(bids)} ask {min(asks)}")
        bank = bot.bankroll()
        fvs = {e: ex.last_fv for e, ex in bot.ex.items()}
        worst = bot.total_worst_case(api.inv, fvs)
        slack = len(eids) * bot.cfg.max_order_cash_frac * bank * 2
        if worst > bot.cfg.max_worst_case_frac * bank + slack:
            problems.append(f"step {cycles}: worst-case loss {worst:.0f} ran away past the cap")
        delta = sum(PARTY_SIGN.get(eids[e][1], 0) * q for e, q in api.inv.items())
        if abs(delta) > bot.cfg.max_party_delta_frac * bank + len(eids) * bot.cfg.order_size_frac * bank * 2:
            problems.append(f"step {cycles}: national-swing exposure {delta:+.0f} ran away past the cap")

    # 1. A normal session with faults.
    api.rate = 0.08
    for _ in range(steps):
        step(True)
    # 2. The election-night exit window, still with faults.
    for ex in bot.ex.values():
        ex.close = M.utcnow() + timedelta(hours=1.5)
    for _ in range(steps // 5):
        step(True)
    for ex in bot.ex.values():                                            # back to normal trading hours
        ex.close = M.utcnow() + timedelta(days=30)
    # 3. Faults stop; the feed is healthy; a minute of quiet trading.
    api.rate, feed.ok = 0.0, True
    feed.push(resync=True)
    for _ in range(30):
        step(False)
    # Freeze the world (no more moves), every book back to normal house quotes, and let the bot settle.
    moves_off = bot.refs.version
    for eid in eids:
        api.books[eid] = house(eid)
    feed.push(dirty=set(eids))
    for _ in range(12):
        clock.advance(20)
        api.new_step()
        bot.last_full_check = -1e9                                        # force full checks
        try:
            bot.cycle()
        except Exception:
            problems.append(f"settling: unexpected exception\n{traceback.format_exc()}")
    now_m = clock.monotonic()
    api.expire()
    record = sorted((o.order_id, round(o.qty)) for o in bot.my_orders.values())
    actual = sorted((o["id"], round(o["quantity"])) for o in api.orders.values())
    stuck = [ex.label for ex in bot.ex.values() if max(ex.pending_until, ex.pause_until, ex.cooldown_until) > now_m]
    before = len(api.calls)
    bot.last_full_check = -1e9
    bot.cycle()
    changes = [c for c in api.calls[before:] if c[0] in ("batch", "cancel_order", "cancel_all")]
    # Every exchange with a real two-sided book must be quoted. (Near 0 or 1 the house can't quote past
    # 0.5c / 99.5c, so a one-sided book has no fair value and is correctly left alone.)
    priceable = {e for e, ex in bot.ex.items() if M.fair_value(ex.book, bot.cfg) is not None}
    unquoted = sorted(bot.ex[e].label for e in priceable - {o["exchangeId"] for o in api.orders.values()})
    M.time, M.utcnow = real_time, real_utcnow
    return {"problems": problems, "record_ok": record == actual, "record": (record, actual), "stuck": stuck,
            "changes": changes, "unquoted": unquoted, "priceable": len(priceable), "cycles": cycles,
            "errors_handled": errors_handled, "faults": api.faults, "fills": len(api.fills),
            "takes": bot.takes_today, "arbs": bot.arbs_today, "moves_off": moves_off}


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)                 # thousands of expected error lines otherwise
    M.alert = lambda msg: None
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    t0 = real_time.time()
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    for seed in range(1, seeds + 1):
        r = run_seed(seed, steps)
        print(f"--- seed {seed}: {r['cycles']} cycles, {r['faults']} injected faults, {r['errors_handled']} failed cycles "
              f"handled, {r['fills']} fills, {r['takes']} takes, {r['arbs']} arbitrages")
        check(f"seed {seed}: no crash, no duplicate or self-crossing quotes, positions within limits",
              not r["problems"], "\n".join(r["problems"][:3]))
        check(f"seed {seed}: once faults stop, the bot's order record matches the exchange exactly", r["record_ok"], r["record"])
        check(f"seed {seed}: no exchange left stuck (paused / pending / cooling down)", not r["stuck"], r["stuck"])
        check(f"seed {seed}: settled - a quiet cycle changes nothing, and all {r['priceable']} priceable exchanges are quoted",
              not r["changes"] and not r["unquoted"], (r["changes"], r["unquoted"]))
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed in {real_time.time() - t0:.0f} s")
    sys.exit(0 if all(RESULTS) else 1)
