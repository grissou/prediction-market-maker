"""
Offline tests for the strategy changes on claude/pr2-strategy (Builder run): pure quoting functions, the
Bot wiring, and a short run of the strategy simulator (tests/strategy_sim.py).

Run:  python tests/test_strategy.py      (exit code 0 = all passed)
"""
import json
import logging
import math
import threading
from collections import deque
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeFeed, FakeRefs, lvl, make_bot, market, run_cycles   # noqa: E402,F401
import mm_bot as M                                        # noqa: E402
from mm_bot import *                                      # noqa: E402,F401,F403
import strategy_sim as S                                  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


# =============================================================================================
# R1 EQUITY BASE

check("R1: reserved_cash_mode defaults to 'ignore' (the API value already includes locked cash)",
      Config().reserved_cash_mode == "ignore")
a, b = make_bot()
a.pnl = lambda: {"totalAccountValue": 100000}
b.cycle(); b.cycle()
check("R1: account value = the API's number, locked cash not added on top",
      b.last_equity == 100000 and b.health["locked_in_orders"] > 0, (b.last_equity, b.health.get("locked_in_orders")))

# =============================================================================================
# R2 SKEW SCALED TO THE QUOTE SIZE, NEVER THROUGH FAIR VALUE

c = Config()
q_old = compute_quote(0.50, 8000, 8000, 0.40, 0.60, Config(skew_mode="share", skew_max=1.0, max_skew_through=1.0),
                      order_size=10000, position_limit=10000)
q_new = compute_quote(0.50, 8000, 8000, 0.40, 0.60, c, order_size=10000, position_limit=10000)
check("R2: old rule, 8,000-share headline long -> ask 4c THROUGH fair value", q_old.ask is not None and q_old.ask <= 0.465, q_old)
check("R2: new rule, same position -> ask never below fair value", q_new.ask is not None and q_new.ask >= 0.50, q_new)
check("R2: new rule, long -> bid backs off (below fv - min_edge)", q_new.bid is not None and q_new.bid < 0.49, q_new)
q_small = compute_quote(0.50, 100, 100, 0.47, 0.53, c, order_size=100)
check("R2: one quote's worth of shares moves the reservation price 0.5c (limits 0.49/0.51 -> 0.485/0.505)",
      abs(q_small.bid_limit - 0.485) < 1e-9 and abs(q_small.ask_limit - 0.505) < 1e-9, q_small)
q_red = compute_quote(0.50, 8000, 8000, 0.40, 0.60, Config(skew_mode="share", skew_max=0.05), reduce_only=True, order_size=10000,
                      position_limit=10000)
check("R2: reduce-only may still go through fair value to get out", q_red.ask is not None and q_red.ask < 0.50, q_red)

# =============================================================================================
# R4 JOIN OR STEP BACK (settings; defaults keep pennying)

q = compute_quote(0.50, 0, 0, 0.47, 0.53, Config())
check("R4: default pennies the best other price (0.475 / 0.525)", (q.bid, q.ask) == (0.475, 0.525), q)
q = compute_quote(0.50, 0, 0, 0.47, 0.53, Config(improve_ticks=0))
check("R4: improve_ticks=0 joins it (0.47 / 0.53)", (q.bid, q.ask) == (0.47, 0.53), q)
q = compute_quote(0.50, 0, 0, 0.495, 0.505, Config(undercut_step_back=0.02))
check("R4: a rival inside our 1c band -> step back to 2c (0.48 / 0.52)", (q.bid, q.ask) == (0.48, 0.52), q)
q = compute_quote(0.50, 0, 0, 0.495, 0.505, Config())
check("R4: default with a rival inside the band -> our 1c floor (0.49 / 0.51)", (q.bid, q.ask) == (0.49, 0.51), q)

# =============================================================================================
# R5 THIN BOOKS PRICED FROM POLYMARKET

thin = {"11": {"bids": [lvl(0.29, 50)], "asks": [lvl(0.31, 50)]}, "12": {"bids": [lvl(0.69, 50)], "asks": [lvl(0.71, 50)]},
        "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
a, b = make_bot(books=thin)
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70})
b.cycle()
def yes_side(o): return "buy" if (o["side"] == "yes") == (o["action"] == "buy") else "sell"
mine = {(o["exchangeId"], yes_side(o)): o for o in a.orders.values()}
check("R5: thin Ohio book (50 sh at top) + liquid Polymarket -> quoted around Polymarket",
      ("11", "buy") in mine and ("11", "sell") in mine and "11" in b.ref_only, sorted(mine))
bid11 = mine[("11", "buy")]["priceLimit"]
check("R5: wider than usual (bid <= 0.285) and small (<= 100 shares)",
      bid11 <= 0.285 and mine[("11", "buy")]["quantity"] <= 100, (bid11, mine[("11", "buy")]))
a, b = make_bot(books=thin)
b.refs = FakeRefs({"Ohio Senate|Republican": 0.40, "Ohio Senate|Democratic": 0.60})   # 10c from the book
b.cycle()
check("R5: Polymarket 10c from the thin book's mid -> not priced (possible wrong match)",
      "11" not in b.ref_only and not any(o["exchangeId"] == "11" for o in a.orders.values()))
a, b = make_bot(books=thin)
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70}, spread=None)
b.cycle()
check("R5: Polymarket not liquid -> thin book stays unpriced", not b.ref_only)
a, b = make_bot(books=thin); b.cfg.ref_only_enabled = False
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70})
b.cycle()
check("R5: switched off -> old behaviour (thin book unpriced)", not any(o["exchangeId"] == "11" for o in a.orders.values()))

q = compute_quote(0.30, 2000, 2000, 0.25, 0.35, Config(), order_size=100, reduce_size=1500)
check("R5b: reduce_size: long 2,000 -> sell side 1,470 (1,500 within the 1,000 cash cap), buy side stays 100",
      q.ask_size == 1470 and q.bid_size <= 100, q)
q = compute_quote(0.30, -300, -300, 0.25, 0.35, Config(), order_size=100, reduce_size=1500)
check("R5b: reduce_size never more than the position itself (short 300 -> bid 300)", q.bid_size == 300, q)
for on in (True, False):
    a, b = make_bot(books=thin)
    b.cfg.ref_only_reduce_full, b.cfg.order_size_frac = on, 0.01    # this market's normal size: 1,000
    b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70})
    a.positions = lambda: {"positions": [{"exchangeId": "11", "quantity": 2000}]}
    b.cycle()
    sells = [o for o in a.orders.values() if o["exchangeId"] == "11" and yes_side(o) == "sell"]
    buys = [o for o in a.orders.values() if o["exchangeId"] == "11" and yes_side(o) == "buy"]
    size = sells[0]["quantity"] if sells else 0
    check(f"R5b: thin book holding 2,000 Rep Ohio, reduce_full={on} -> sell size {'> 100' if on else '<= 100'}",
          bool(sells) and ((size > 100) if on else (size <= 100)) and all(o["quantity"] <= 100 for o in buys),
          (size, [o["quantity"] for o in buys]))

# =============================================================================================
# R5c THIN-BOOK PRICING FROM THE BULK BEST BID/ASK (no downloaded book, e.g. after a restart)

def r5c(top_age=10.0, top=(0.29, 0.31), ref=0.30, liquid=True, book=None, book_age=0.0, **cfg):
    a, b = make_bot(books=thin)
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    now = 5000.0
    for ex in b.ex.values():
        ex.book = None
    if book is not None:
        b.ex["11"].book, b.ex["11"].verified = book, now - book_age
    b.other_tops = {"11": (*top, now - top_age)}
    fvs = {e: None for e in b.ex}
    out = b.thin_book_prices(fvs, {"11": ref}, {"11"} if liquid else set(), now)
    return b, out, fvs

b, out, fvs = r5c()
check("R5c: no book, fresh two-sided bulk tops -> priced from Polymarket (and remembered as from tops)",
      out == {"11"} and fvs["11"] == 0.30 and b.ref_tops == {"11": (0.29, 0.31)}, (out, fvs, b.ref_tops))
b, out, _ = r5c(top_age=121.0)
check("R5c: bulk tops older than tops_max_age (120 s) -> not priced", not out and not b.ref_tops, out)
b, out, _ = r5c(top=(0.29, None))
check("R5c: one-sided bulk tops -> not priced", not out, out)
b, out, _ = r5c(ref_only_use_tops=False)
check("R5c: ref_only_use_tops off -> no book, no price (as before)", not out and not b.ref_tops, out)
b, out, _ = r5c(ref=0.40)
check("R5c: same R5 checks: tops mid 10c from Polymarket -> not priced (possible wrong match)", not out, out)
b, out, _ = r5c(liquid=False)
check("R5c: same R5 checks: Polymarket not liquid -> not priced", not out, out)
b, out, _ = r5c(book={"bids": [lvl(0.29, 50)], "asks": []})
check("R5c: a current downloaded book wins over the tops (one-sided book -> not priced)", not out, out)
b, out, _ = r5c(book={"bids": [lvl(0.20, 50)], "asks": [lvl(0.40, 50)]}, book_age=901.0)
check("R5c: a stale book (> book_stale) falls back to fresh tops", out == {"11"} and "11" in b.ref_tops, out)

mine = [Resting(1, "11", True, 0.295, 100, None)]
check("R5c: bulk top = our own bid -> other traders' bid unknown (None); their ask kept",
      others_top((0.295, 0.31), mine) == (None, 0.31), others_top((0.295, 0.31), mine))
check("R5c: our bid behind the top -> the top is someone else's", others_top((0.30, 0.31), mine) == (0.30, 0.31))
a, b = make_bot(books=thin)
b.other_tops = {"11": (0.29, 0.31, 100.0)}
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 130.0)
check("R5c: once our bid is the top, the others' bid seen before (still fresh) is kept",
      b.other_tops["11"] == (0.29, 0.31, 130.0), b.other_tops["11"])
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 200.0)
check("R5c: ...and carried on while every reading comes within tops_max_age", b.other_tops["11"] == (0.29, 0.31, 200.0))
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 321.0)
check("R5c: ...but not after a gap longer than tops_max_age (bid unknown -> R5 can't use it)",
      b.other_tops["11"] == (None, 0.31, 321.0), b.other_tops["11"])
b.other_tops = {"11": (0.30, 0.31, 100.0)}
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 130.0)
check("R5c: never carried when the old others' bid is better than the top now (it must have gone)",
      b.other_tops["11"][0] is None, b.other_tops["11"])
b.note_other_tops({"11": (0.295, 0.31)}, {}, 140.0)
check("R5c: ...and a plain reading replaces it", b.other_tops["11"] == (0.295, 0.31, 140.0), b.other_tops["11"])

# Whole cycle with every book download failing (the state right after a restart, before the books arrive).
REFS4 = {"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70,
         "Utah Senate|Republican": 0.52, "Utah Senate|Democratic": 0.48}
a, b = make_bot(books=thin)
a.fail_book = set(thin)
b.refs = FakeRefs(REFS4)
b.cycle()
mine = {(o["exchangeId"], yes_side(o)): round(o["priceLimit"] if o["side"] == "yes" else 1 - o["priceLimit"], 3)
        for o in a.orders.values()}                       # YES prices
check("R5c cycle: no book downloaded, all 4 markets quoted from the bulk tops (R5, small)",
      b.ref_only == {"11", "12", "21", "22"} and len(mine) == 8 and all(o["quantity"] <= 100 for o in a.orders.values()),
      (b.ref_only, mine))
check("R5c cycle: thin Ohio (others 0.29 / 0.31): R5 edge 1.5c from Polymarket, never crossing (0.285 / 0.315)",
      (mine.get(("11", "buy")), mine.get(("11", "sell"))) == (0.285, 0.315), mine)
check("R5c cycle: Utah (others 0.48 / 0.56): one tick inside the KNOWN best other price (0.485 / 0.555)",
      (mine.get(("21", "buy")), mine.get(("21", "sell"))) == (0.485, 0.555), mine)
check("R5c cycle: status shows books_loaded 0 and 4 markets priced from tops",
      b.health.get("books_loaded") == 0 and b.health.get("markets_priced_from_tops") == 4, b.health)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("R5c: status.json carries books_loaded", st.get("books_loaded") == 0, st.get("books_loaded"))
a, b = make_bot(books=thin)
a.fail_book = set(thin)
b.refs = FakeRefs({k: v for k, v in REFS4.items() if k.startswith("Ohio")})
b.cycle()
check("R5c cycle: depth-checked fair value still needs a book: deep Utah without Polymarket -> unpriced",
      not any(o["exchangeId"] in ("21", "22") for o in a.orders.values()) and not {"21", "22"} & b.ref_only, b.ref_only)
a, b = make_bot(books=thin)
a.fail_book = set(thin)
b.cfg.ref_only_use_tops = False
b.refs = FakeRefs(REFS4)
b.cycle()
check("R5c cycle: ref_only_use_tops off -> nothing quoted without books (as before)", not a.orders and not b.ref_only)
a.fail_book = set()
b.cycle()
check("R5c cycle: ...and with the books downloaded Utah is depth-priced (not R5)",
      "21" not in b.ref_only and "11" in b.ref_only, b.ref_only)

# =============================================================================================
# STARTUP PRIMING (books first after a start or handover restart)

many = [market(str(100 + i), str(1000 + i), "Republican", f"Race {i:03d}") for i in range(70)]
a, b = make_bot(extra_markets=many)
a.budget_left = lambda: 80
check("priming: 74 markets, none loaded, not trading yet -> max_books_per_cycle (30)", len(b.books_to_fetch([])) == 30)
b.trading_since = M.time.monotonic()
check("priming: just started -> min(startup_prime_books 60, startup_prime_books_per_min 45) = 45",
      b.priming() and len(b.books_to_fetch([])) == 45)
b.cfg.startup_prime_books_per_min = 100
check("priming: per-minute cap raised -> startup_prime_books (60) per cycle", len(b.books_to_fetch([])) == 60)
b.cfg.startup_prime_books_per_min = 45
b.download_books([], {})
b.book_reqs.extend([M.time.monotonic()] * 40)            # 40 downloads in the last minute
check("priming: 40 downloads in the last 60 s -> 5 more this cycle", len(b.books_to_fetch([])) == 5)
check("priming: order writes leave write_read_reserve + those 5 free (write_reserve)",
      b.write_reserve() == b.cfg.write_read_reserve + 5, b.write_reserve())
b.book_reqs.clear()
check("priming: ...45 when no book was downloaded this minute", b.write_reserve() == b.cfg.write_read_reserve + 45)
a.budget_left = lambda: 40
check("priming: ...still within the budget, keeping startup_prime_reserve (8): 32",
      len(b.books_to_fetch([])) == 32, len(b.books_to_fetch([])))
a.budget_left = lambda: 40
b.cfg.startup_books_first = False
check("priming off: the old cap and reserve (40 - 20 = 20)", len(b.books_to_fetch([])) == 20)
b.cfg.startup_books_first = True
a.budget_left = lambda: 80
b.trading_since = M.time.monotonic() - 181
check("priming: over after startup_prime_seconds (180 s)", not b.priming() and len(b.books_to_fetch([])) == 30)
check("priming over: order writes leave just write_read_reserve", b.write_reserve() == b.cfg.write_read_reserve)
b.trading_since = M.time.monotonic()
for ex in list(b.ex.values())[:67]:
    ex.book = {"bids": [], "asks": []}
check("priming: over once at most 10% of the books are missing (7 of 74)", not b.priming())
for ex in b.ex.values():
    ex.book = None
b.my_orders[9] = Resting(9, "1069", True, 0.1, 10, None)
b.ref_tops = {"1068": (0.1, 0.2)}
first = b.books_to_fetch([])[:2]
check("priming: missing books with our orders resting come first, then those quoted from tops",
      first == ["1069", "1068"], first)

class Grab(logging.Handler):
    def __init__(self):
        super().__init__(); self.lines = []
    def emit(self, r): self.lines.append(r.getMessage())
g = Grab()
M.log.addHandler(g); old_level, M.log.propagate = M.log.level, False
M.log.setLevel(logging.INFO)
b.my_orders.clear()
b.log_priming()
for ex in b.ex.values():
    ex.book = {"bids": [], "asks": []}
b.log_priming(); b.log_priming()
M.log.removeHandler(g); M.log.setLevel(old_level); M.log.propagate = True
check("priming: one log line per cycle while priming, one when done",
      g.lines[0] == "priming books: 0 of 74 loaded" and len(g.lines) == 2 and g.lines[1].startswith("priming books done: 74 of 74"),
      g.lines)

a, b = make_bot(); b.cfg.selftest_enabled = False
run_cycles(b, 1)
check("priming: the trading loop sets trading_since (fresh start and handover alike: both go through run())",
      b.trading_since is not None)

# =============================================================================================
# R7 CORRELATED SETTLEMENT RISK

check("R7: lone 500 YES @0.5 -> variance 500^2 x 0.25", abs(race_variance([(500, 0.5)]) - 62500) < 1e-6)
check("R7: hedged race pair -> variance 0", race_variance([(500, 0.14), (500, 0.86)]) < 1e-9)
check("R7: long Rep + short Dem = the same bet twice -> variance of 1,000 shares",
      abs(race_variance([(500, 0.5), (-500, 0.5)]) - 1000 ** 2 * 0.25) < 1e-6)
a, b = make_bot()
b.cycle()
inv = {"11": 3000.0, "21": 3000.0}                       # Rep Ohio + Rep Utah: national swing adds up
fvs = {"11": 0.14, "12": 0.86, "21": 0.52, "22": 0.48}
r = b.settlement_risk(inv, fvs, party_delta=6000)
want = 0.15 * 6000 + 3.0 * math.sqrt(race_variance([(3000, 0.14), (0, 0.86)]) + race_variance([(3000, 0.52), (0, 0.48)]))
check("R7: risk = 15c swing x |party delta| + 3 sd of the races", abs(r - want) < 1e-6, (r, want))
# 40 small races: sum-of-maxima says reduce-only, the correlated measure does not
extra = []
for i in range(40):
    extra += [market(str(100 + 2 * i), str(1000 + 2 * i), "Republican", f"Race {i}"),
              market(str(101 + 2 * i), str(1001 + 2 * i), "Democratic", f"Race {i}")]
a, b = make_bot(extra_markets=extra)
held = [{"exchangeId": str(1000 + 2 * i + i % 2), "quantity": 2000} for i in range(40)]   # alternate Rep/Dem
a.positions = lambda: {"positions": held}
b.cycle(); b.cycle()
h = b.health
check("R7: 40 independent 2,000-share races: sum of maxima 40k > 30% of 100k, correlated risk ~19k -> keeps quoting",
      h["worst_case_loss"] > 30000 and h["settlement_risk"] < 30000 and not h["reduce_only"],
      (h["worst_case_loss"], h["settlement_risk"], h["reduce_only"]))
b.cfg.risk_model = "sum_max"; b.cycle()
check("R7: switched off (sum_max) -> same book is reduce-only", b.health["reduce_only"])
b.cfg.risk_model = "correlated"; b.cfg.worst_case_backstop_frac = 0.30; b.cycle()
check("R7: sum-of-maxima backstop still forces reduce-only", b.health["reduce_only"])
check("R7: risk never above the sum of maxima", b.health["settlement_risk"] <= b.health["worst_case_loss"] + 1e-6)

# =============================================================================================
# BOOK + TRADE RECORDER (data to measure the rival bots)

import sqlite3, tempfile
a, b = make_bot()
b.cfg.record_file = os.path.join(tempfile.mkdtemp(), "md.sqlite")
b.db = b.open_recorder()
class TradeFeed(FakeFeed):
    def __init__(self): super().__init__(); self.trades = []
    def take_trades(self): out, self.trades = self.trades, []; return out
b.feed = TradeFeed()
b.feed.trades = [(1.0, {"exchangeId": "11", "price": 0.15, "quantity": 300, "tournamentId": "T"})]
b.last_full_check = -1e9; b.cycle(); b.last_record = -1e9; b.record({}, 1e9)
rows = b.db.execute("select eid, bids, asks from books order by eid").fetchall()
check("recorder: one book row per market downloaded, other traders only (our orders stripped)",
      len(rows) == 4 and json.loads(rows[0][1]) == [[0.10, 1000.0]] and json.loads(rows[0][2]) == [[0.18, 1000.0]], rows[:1])
tr = b.db.execute("select eid, price, quantity from trades").fetchall()
check("recorder: a tournament trade from the feed is stored with price and size", tr == [("11", 0.15, 300.0)], tr)
b.download_books(["11", "12"], b.orders_by_eid(M.utcnow())); b.last_record = -1e9; b.record({}, 2e9)
check("recorder: books downloaded again but unchanged -> no new rows",
      b.db.execute("select count(*) from books").fetchone()[0] == 4)
a.books["11"]["bids"].insert(0, lvl(0.12, 250))
b.download_books(["11"], b.orders_by_eid(M.utcnow())); b.last_record = -1e9; b.record({}, 3e9)
last = b.db.execute("select bids from books where eid='11' order by ts desc, rowid desc limit 1").fetchone()[0]
check("recorder: a rival steps in at 0.12 x 250 -> new row with both levels",
      json.loads(last) == [[0.12, 250.0], [0.10, 1000.0]], last)
b.cfg.record_books = False
a.books["11"]["bids"].insert(0, lvl(0.125, 50))
b.download_books(["11"], b.orders_by_eid(M.utcnow())); b.last_record = -1e9; b.record({}, 4e9)
check("recorder: record_books=False -> nothing more recorded", b.db.execute("select count(*) from books").fetchone()[0] == 5)
f = RealtimeFeed.__new__(RealtimeFeed)
f.lock, f.trade_log = threading.Lock(), deque([(1.0, {"exchangeId": "1"})])
check("recorder: RealtimeFeed.take_trades drains its log", f.take_trades() == [(1.0, {"exchangeId": "1"})] and not f.trade_log)

# =============================================================================================
# CANCEL-EVERYTHING THAT FAILS MUST NOT FORGET LIVE ORDERS (bug on main and pr1; found by test_stress seed 3)

a, b = make_bot()
b.cycle()
before = dict(a.orders)
real_cancel_all = a.cancel_all
def failing_cancel_all(tid, eid=None):
    raise ApiError(409, "REQUEST_IN_FLIGHT", "busy")
a.cancel_all = failing_cancel_all
b.failed_cycles = b.cfg.max_failed_cycles - 1
b.on_cycle_error("test", pull_now=False)                 # 3rd failed cycle -> cancel everything -> it fails
a.cancel_all = real_cancel_all
check("cancel-all fails: the bot still knows its orders (none forgotten while they rest)",
      set(b.my_orders) == set(before) and not any(o in b.recent_cancels for o in before),
      (sorted(b.my_orders), sorted(before)))
b.pulled_after_errors, b.failed_cycles = False, 0
b.cycle()
dup = {}
for o in a.orders.values():
    k = (o["exchangeId"], (o["side"] == "yes") == (o["action"] == "buy"))
    dup[k] = dup.get(k, 0) + 1
check("cancel-all fails: the next cycle places no second set (no duplicate quotes)", max(dup.values()) == 1, dup)

# =============================================================================================
# SIMULATOR SANITY

agg, rows = S.run_many(1, 0.5, "quiet")
check("simulator: runs, fills happen, no duplicate quotes", agg["fills"] > 0 and agg["dups_max"] == 0, agg)
a1, _ = S.run_many(1, 0.5, "quiet")
check("simulator: deterministic for a seed", a1 == agg)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
