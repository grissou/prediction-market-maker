"""
The 10 Oct shadow's conditions (docs/REWRITE_FIX_BRIEF.md): it planned no orders in four hours. Reproduced here:

World     120 two-leg races (240 markets), Polymarket liquid on all; books 8c wide around a mid tilted 8% toward
          the race's even split (so the tilt estimator has something to find); positions in 60 markets; 10k free
          cash on a ~105k account.
Foreign   170 resting orders on the account that this bot did not place (the live bot's), two cents inside the
          other traders in 85 markets, as the live bot quotes.
Budget    20 requests a minute counted on a sliding minute of the simulated clock, as the real Client counts them;
          the feed healthy, flagging a few books dirty each cycle as the live bot requotes.
Checks    within the first 10 minutes the dry run logs DRY PLACE lines; the count of fresh books grows to every
          market in 40 minutes; the tilt forms (near 8%); the minute's requests never pass the budget; status.json
          carries books_fresh and blocked_by; foreign orders are never cancelled, never counted as ours, never
          crossed, and do not stop us quoting; live, a stop cancels only our own orders.
Run:  python3 tests2/test_shadow.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import sys
import tempfile
from collections import deque
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes                                                         # noqa: E402
from fakes import FakeClient, FakeFeed, FakeRefs, raw_market         # noqa: E402
from mmbot2 import bot as bot_module, config, exchange, value       # noqa: E402
from mmbot2.bot import Bot, STATUS_FILE                              # noqa: E402
from mmbot2.exchange import parse_markets                            # noqa: E402
from mmbot2.state import Order                                       # noqa: E402

RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


class Clock:
    def __init__(self):
        self.mono, self.wall = 10000.0, datetime(2026, 10, 10, 17, 24, tzinfo=timezone.utc)
    def monotonic(self): return self.mono
    def time(self): return self.wall.timestamp()
    def now(self): return self.wall
    def sleep(self, s): self.advance(s)
    def advance(self, s):
        self.mono += s
        self.wall += timedelta(seconds=s)


CLOCK = Clock()
bot_module.utcnow, bot_module.time, value.time, fakes.time, exchange.time = CLOCK.now, CLOCK, CLOCK, CLOCK, CLOCK


class LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []
    def emit(self, record):
        self.lines.append(record.getMessage())


class BudgetClient(FakeClient):
    """The fake exchange with the real Client's budget: requests counted on a sliding minute; a request beyond the
    budget is recorded as an overrun (the real one would block the cycle until the minute has room)."""
    def __init__(self, *a, rpm=20, **k):
        super().__init__(*a, **k)
        self.rpm, self.starts, self.overruns, self.peak = rpm, deque(), 0, 0
        self.cancelled = []

    def used(self):
        while self.starts and CLOCK.mono - self.starts[0] >= 60.0:
            self.starts.popleft()
        return len(self.starts)

    def call(self, name, kind):
        if name not in ("tops",):
            self.count()
        super().call(name, kind)

    def count(self):
        if self.used() >= self.rpm:
            self.overruns += 1
        self.starts.append(CLOCK.mono)
        self.peak = max(self.peak, self.used())

    def tops(self, eids):
        for _ in range(0, len(eids), exchange.BULK_MAX_IDS):
            self.count()                  # one request per 100 ids, as the real bulk read
        return super().tops(eids)

    def requests_left(self):
        return self.rpm - self.used()

    def clock(self):
        return CLOCK.wall

    def cancel(self, oid, wait=False):
        self.cancelled.append(oid)
        return super().cancel(oid, wait)


TILT = 0.08


def shadow_world(rpm=20, live=False, races=120):
    raws, refs, books, inv = [], {}, {}, {}
    for i in range(races):
        race = f"Race {i:03d} Senate"
        r_rep = 0.20 + 0.60 * (i % 13) / 12              # 0.20 .. 0.80: all in the market-making band
        for leg, (party, r) in enumerate((("Republican", r_rep), ("Democratic", 1 - r_rep))):
            eid = f"{i}{leg}"
            raws.append(raw_market(f"m{eid}", eid, party, race))
            refs[f"{race}|{party}"] = r
            mid = round(0.5 + (1 - TILT) * (r - 0.5), 3)    # the book leans toward 50/50: g = s * (r - c)
            books[eid] = {"bids": [(round(mid - 0.04, 3), 1000)], "asks": [(round(mid + 0.04, 3), 1000)]}
            if i < 30:
                inv[eid] = 3500 if leg == 0 else 3000         # both legs: a hedged book
    markets = [m for r in raws for m in parse_markets(r, None)]
    client = BudgetClient(markets, books, cash=10025.0, live=live, rpm=rpm, start=100000.0)
    client.inv = inv
    for i in range(min(85, races)):                        # the live bot's 170 orders, 2c inside the others
        eid = f"{i}0"
        mid = (books[eid]["bids"][0][0] + books[eid]["asks"][0][0]) / 2
        for k, (is_bid, px) in enumerate(((True, round(mid - 0.02, 3)), (False, round(mid + 0.02, 3)))):
            oid = f"F{i}{k}"
            client.orders[oid] = Order(eid, is_bid, px, 50, "", oid=oid, expires=CLOCK.wall + timedelta(days=1))
            client.locks[oid] = px if is_bid else 1 - px
    client.cash += client.locked()                         # 10,025 free after the live bot's locks
    client.start = client.account().value                  # no drawdown: the kill switch stays out of it
    client.calls = []
    return client, FakeFeed(), FakeRefs(refs)


def make_bot(client, feed, refs, live, rpm=20, wpm=5):
    d = tempfile.mkdtemp(prefix="mm2shadow")
    env = config.Env("k", "t", "http://x", "", d, os.path.join(d, "settings_override.json"))
    s = config.Settings(requests_per_minute=rpm, writes_per_minute=wpm)
    bot = Bot(client, feed, refs, s, env, live)
    bot.load_markets(CLOCK.mono)
    return bot


def run(bot, client, feed, minutes, catcher, step_s=10.0, churn=3):
    """Cycles every step_s for `minutes`; each cycle the feed flags `churn` books dirty (the live bot requoting)."""
    status, k = [], 0
    for _ in range(int(minutes * 60 / step_s)):
        eids = sorted(client.others)
        feed.push(dirty=[eids[(k + j * 37) % len(eids)] for j in range(churn)])
        k += churn
        bot.cycle()
        status.append(json.load(open(os.path.join(bot.env.run_dir, STATUS_FILE))))
        CLOCK.advance(step_s)
    return status


def test_dry_run_shadow():
    client, feed, refs = shadow_world()
    bot = make_bot(client, feed, refs, live=False)
    catcher = LogCatcher()
    logging.getLogger("mm2").addHandler(catcher)
    logging.getLogger("mm2").setLevel(logging.INFO)
    status = run(bot, client, feed, 10, catcher)
    first = [ln for ln in catcher.lines if ln.startswith("DRY PLACE")]
    check("the dry run plans and logs orders within 10 minutes (DRY PLACE lines)", len(first) > 0,
          f"{len(first)} lines; last status {status[-1].get('books_fresh')} fresh, {status[-1].get('blocked_by')}")
    check("each DRY PLACE line has market, side, qty@price and reason",
          all(len(ln.split()) >= 5 and "@" in ln for ln in first), first[:3])
    status += run(bot, client, feed, 30, catcher)
    st = status[-1]
    check("status.json has books_fresh and blocked_by", "books_fresh" in st and "blocked_by" in st, list(st)[:40])
    check("every market has a fresh book after 40 minutes at 20 requests a minute",
          st.get("books_fresh", 0) >= 230, st.get("books_fresh"))
    check("the tilt forms", st.get("tilt_s") is not None, st.get("tilt_s"))
    check("the tilt is near the books' 8%", st.get("tilt_s") is not None and abs(st["tilt_s"] - TILT) < 0.02,
          st.get("tilt_s"))
    check("the requests never pass 20 a minute", client.overruns == 0, f"{client.overruns} overruns, peak {client.peak}")
    check("market-making quotes are resting (simulated)", st["orders_resting"] > 50, st["orders_resting"])
    check("the dry run sent no write", not [c for c in client.calls if c in ("place", "cancel", "cancel_all")])
    check("foreign orders are not counted as ours", not any(o.oid.startswith("F") for o in bot.orders.values()))
    lines = [ln for ln in catcher.lines if ln.startswith("account ")]
    check("the status line shows fresh books and blocked_by", lines and "books" in lines[-1]
          and "blocked" in lines[-1], lines[-1:] if lines else None)
    logging.getLogger("mm2").removeHandler(catcher)


def test_foreign_live():
    """Live, with foreign orders: they are never cancelled (not by reconcile, not by a stop), never crossed, and
    the bot still quotes, including in the markets where they rest."""
    client, feed, refs = shadow_world(rpm=1000, live=True, races=20)
    bot = make_bot(client, feed, refs, live=True, rpm=1000, wpm=1000)
    for _ in range(6):
        feed.push(dirty=list(client.others)[:3])
        bot.cycle()
        CLOCK.advance(10)
    foreign = {k for k in client.orders if k.startswith("F")}
    check("live: no foreign order cancelled", not [o for o in client.cancelled if o.startswith("F")],
          client.cancelled[:5])
    check("live: all 40 foreign orders still rest", len(foreign) == 40, len(foreign))
    check("live: foreign orders are not adopted as ours", not any(k.startswith("F") for k in bot.orders))
    ours = [o for o in client.orders.values() if not o.oid.startswith("F")]
    check("live: the bot quotes", len(ours) > 20, len(ours))
    check("live: the bot quotes in markets where foreign orders rest",
          any(o.eid == "00" or o.eid == "10" for o in ours), sorted({o.eid for o in ours})[:10])
    check("live: no self-cross with our own or foreign orders", client.self_crosses == 0, client.self_crosses)
    bot.cancel_everything()
    left = set(client.orders)
    check("live: a stop cancels all our orders", not [o for o in left if not o.startswith("F")], sorted(left)[:5])
    check("live: a stop leaves the foreign orders", foreign <= left, sorted(foreign - left)[:5])


def test_foreign_cross():
    """A wanted order that would trade against a foreign order is not sent (a self-trade), and is counted."""
    client, feed, refs = shadow_world(rpm=1000, live=True, races=2)
    eid = "00"
    ask = min(o.price for o in client.orders.values() if o.eid == eid and not o.is_bid)
    bot = make_bot(client, feed, refs, live=True, rpm=1000, wpm=1000)
    bot.cycle()
    bot.reconcile = lambda view, wanted: ([], [], [Order(eid, True, ask, 10, "mm")])
    bot.cycle()
    check("an order crossing a foreign order is not sent", client.self_crosses == 0 and not any(
        o.eid == eid and o.is_bid and o.price >= ask for o in client.orders.values()))
    st = json.load(open(os.path.join(bot.env.run_dir, STATUS_FILE)))
    check("and is counted in blocked_by", st.get("blocked_by", {}).get("self_trade", 0) >= 1, st.get("blocked_by"))


def test_dirty_without_book():
    """The feed only flags books: a flag for a market never downloaded does not make a book, and a flag the budget
    could not serve this cycle is remembered."""
    client, feed, refs = shadow_world(rpm=20, races=10)
    bot = make_bot(client, feed, refs, live=False)
    client.starts.extend([CLOCK.mono] * 20)              # this minute's budget already spent
    feed.push(dirty=["50"])
    bot.cycle()
    check("no book from a dirty flag alone", "50" not in bot.books)
    check("the flag is remembered", "50" in bot.dirty)
    CLOCK.advance(61)
    bot.cycle()
    check("and served once the budget allows", "50" in bot.books and "50" not in bot.dirty)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    for t in (test_dry_run_shadow, test_foreign_live, test_foreign_cross, test_dirty_without_book):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran without an exception", False, repr(e))
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)
