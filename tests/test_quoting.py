"""
Offline tests for the strategy changes on claude/pr2-strategy (Builder run): pure quoting functions, the
Bot wiring, and a short run of the strategy simulator (tests/strategy_sim.py).

Run:  python tests/test_quoting.py      (exit code 0 = all passed)
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
q_old = compute_quote(0.50, 8000, 8000, 0.40, 0.60, Config(skew_mode="share", skew_max=1.0, max_skew_through=1.0,
                                                         reduce_join_best=False),
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

q = compute_quote(0.30, 2000, 2000, 0.25, 0.35, Config(reduce_join_best=False), order_size=100, reduce_size=1500)
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
check("R5c: once our bid is the top, the others' bid seen before (still fresh) is kept, with the time it was SEEN",
      b.other_tops["11"] == (0.29, 0.31, 100.0), b.other_tops["11"])
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 200.0)
check("R5c: ...and carried on while that observation is within tops_max_age", b.other_tops["11"] == (0.29, 0.31, 100.0))
b.note_other_tops({"11": (0.295, 0.31)}, {"11": mine}, 225.0)
check("R5c: ...but not once the observation is older than tops_max_age (a side we can't see is not carried for ever)",
      b.other_tops["11"] == (None, 0.31, 225.0), b.other_tops["11"])
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

# ======================================================================================# STARTUP PRIMING (books first after a start or handover restart)

many = [market(str(100 + i), str(1000 + i), "Republican", f"Race {i:03d}") for i in range(70)]
a, b = make_bot(extra_markets=many)
a.budget_left = lambda: 80
check("priming: 74 markets, none loaded, not trading yet -> max_books_per_cycle (30)", len(b.books_to_fetch([])) == 30)
b.trading_since = M.util.time.monotonic()
check("priming: just started -> min(startup_prime_books 60, startup_prime_books_per_min 45) = 45",
      b.priming() and len(b.books_to_fetch([])) == 45)
b.cfg.startup_prime_books_per_min = 100
check("priming: per-minute cap raised -> startup_prime_books (60) per cycle", len(b.books_to_fetch([])) == 60)
b.cfg.startup_prime_books_per_min = 45
b.download_books([], {})
b.book_reqs.extend([M.util.time.monotonic()] * 40)            # 40 downloads in the last minute
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
b.trading_since = M.util.time.monotonic() - 181
check("priming: over after startup_prime_seconds (180 s)", not b.priming() and len(b.books_to_fetch([])) == 30)
check("priming over: order writes leave just write_read_reserve", b.write_reserve() == b.cfg.write_read_reserve)
b.trading_since = M.util.time.monotonic()
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

# --- Held markets' books first (live 11:29 UTC: Rep U.S. House, short 9,396, unpriced after a handover restart:
# its book not downloaded, our adopted reducing bid at the best blanks the bulk bid, so R5 refuses it).
def handover_bot(n_extra=70, held=-2000):
    extra = [market(str(100 + i), str(1000 + i), "Republican", f"Race {i:03d}") for i in range(n_extra)]
    books = dict(thin)
    books.update({str(1000 + i): {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.48, 1000)]} for i in range(n_extra)})
    a, b = make_bot(books=books, extra_markets=extra)
    b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70})
    a.inv = {"11": held}
    a.orders[1] = {"id": 1, "exchangeId": "11", "quantity": 500, "open": True, "side": "yes", "action": "buy",
                   "priceLimit": 0.295}                          # adopted reducing bid, above the others' 0.29
    for i in range(n_extra):                                     # adopted orders everywhere (sort before "11")
        a.orders[2 + i] = {"id": 2 + i, "exchangeId": str(1000 + i), "quantity": 100, "open": True,
                           "side": "yes", "action": "buy", "priceLimit": 0.39}
    b.cfg.startup_books_first, b.cfg.max_books_per_cycle = False, 5
    return a, b

a, b = handover_bot()
b.cycle()
check("held first: handover start, short 2,000 Rep Ohio with our bid at the best, 5 books a cycle -> its book is "
      "downloaded in the first cycle and it is priced (R5 from the book)",
      b.ex["11"].book is not None and "11" in b.ref_only and b.ex["11"].last_fv is not None,
      (b.ex["11"].book, b.ref_only))
b.ex["11"].book = None
b.ex["11"].verified = 0.0
check("held first: ...without the book, the bulk bid is our own (blanked) -> R5 cannot price it (the live bug)",
      b.r5_top(b.ex["11"], M.util.time.monotonic()) is None and b.other_tops["11"][0] is None, b.other_tops.get("11"))

a, b = make_bot(extra_markets=many)
b.held = {"1069": -500.0, "1050": 100.0, "1060": 2000.0}
b.ex["1060"].book = {"bids": [], "asks": []}
b.ex["1069"].last_fv = 0.10                                     # short 500 NO at 0.90 = 450
b.ex["1050"].last_fv = 0.80                                     # long 100 YES at 0.80 = 80
b.my_orders[9] = Resting(9, "1001", True, 0.1, 10, None)
b.pending_dirty = {"1060"}
order = b.books_to_fetch([])[:4]
check("held first: missing held books before feed-reported ones and before markets with orders, biggest "
      "|position| x price of the shares held first", order == ["1069", "1050", "1060", "1001"], order)
check("held first: held_missing lists only held markets without a book, biggest first", b.held_missing() == ["1069", "1050"])

for ex in b.ex.values():
    ex.book = {"bids": [], "asks": []}
b.ex["1069"].book = None
b.trading_since = M.util.time.monotonic() - 300
check("held priming: past startup_prime_seconds and 1 of 74 missing, but it is held -> still priming",
      b.priming() and b.prime_need() == 1, (b.priming(), b.prime_need()))
check("held priming: ...the order writes give up only that one book's request (write_reserve)",
      b.write_reserve() == b.cfg.write_read_reserve + 1, b.write_reserve())
b.trading_since = M.util.time.monotonic() - 60
check("held priming: within startup_prime_seconds, below the missing share, a held book missing -> priming", b.priming())
b.held = {"1050": 100.0}
check("held priming: ...not when the missing book is not held", not b.priming())
b.held = {"1069": -500.0}
b.trading_since = M.util.time.monotonic() - 901
check("held priming: hard cap startup_prime_held_max_seconds (900 s) ends it anyway", not b.priming())
b.trading_since = M.util.time.monotonic() - 300
g = Grab()
M.log.addHandler(g); M.log.propagate = False; M.log.setLevel(logging.INFO)
b.log_priming()
b.trading_since = M.util.time.monotonic() - 901
b.log_priming()
M.log.removeHandler(g); M.log.setLevel(old_level); M.log.propagate = True
check("held priming: the extended priming line names the held markets still missing, then one 'done' line",
      len(g.lines) == 2 and "extended" in g.lines[0] and "Race 069" in g.lines[0] and g.lines[1].startswith("priming books done"),
      g.lines)

a, b = handover_bot(n_extra=0)
a.fail_book = {"11"}                                            # its book never arrives
adopted = dict(a.orders[1])
g = Grab()
M.log.addHandler(g); M.log.propagate = False; M.log.setLevel(logging.INFO)
for k in range(7):
    a.orders.pop(1, None); a.orders.pop(500 + k - 1, None)
    a.orders[500 + k] = dict(adopted, id=500 + k)              # our bid stays the best (e.g. its cancel not sent yet)
    b.cycle()
M.log.removeHandler(g); M.log.setLevel(old_level); M.log.propagate = True
warns = [l for l in g.lines if l.startswith("UNPRICED")]
check("unpriced warning: a held market unpriced for unpriced_held_warn_cycles (5) cycles -> ONE warning with the "
      "reason (no book, bulk bid blanked by our own order)",
      len(warns) == 1 and "inv -2000" in warns[0] and "no book downloaded" in warns[0], warns)
check("unpriced warning: ...the reason names the blanked side", len(warns) == 1 and "bid blanked" in warns[0], warns)
a.fail_book = set()
b.cycle()
check("unpriced warning: ...cleared once the market is priced again", "11" not in b.unpriced_held and not b.unpriced_warned,
      (b.unpriced_held, b.unpriced_warned))
# =============================================================================================
# FAVOURITE-LONGSHOT SIDE BIAS (fl_*): below 20c the bid is the bad side, above 80c the ask

FL = Config()
check("FL: OFF by default (the bad-side losses were day one's skew quoting through fair value, DATA_REPORT_2 §10c)",
      FL.fl_bias_enabled is False)
FL.fl_bias_enabled = True                   # the tests below exercise the feature switched on


def flq(fv, inv=0, bb=None, ba=None, cfg=FL, prev=None, **kw):
    side, e, f = fl_side(fv, prev, cfg)
    side = "bid" if side == "mid" else side
    return compute_quote(fv, inv, inv, bb, ba, cfg, order_size=200, bias_side=side, bias_edge=e, bias_size=f, **kw)


def plain(fv, inv=0, bb=None, ba=None, cfg=FL, **kw):
    return compute_quote(fv, inv, inv, bb, ba, cfg, order_size=200, **kw)


check("FL: settings when on: 20c / 80c, +1c, half size, 1c hysteresis, mid-band off",
      (FL.fl_bias_enabled, FL.fl_low, FL.fl_high, FL.fl_bad_side_extra_edge, FL.fl_bad_side_size_factor,
       FL.fl_hysteresis, FL.fl_mid_bid_extra_edge) == (True, 0.20, 0.80, 0.01, 0.5, 0.01, 0.0))
check("FL: fl_side picks bid below 20c, ask above 80c, nothing between",
      [fl_side(f, None, FL)[0] for f in (0.10, 0.199, 0.20, 0.50, 0.80, 0.801, 0.95)] ==
      ["bid", "bid", None, None, None, "ask", "ask"])
q0, q1 = plain(0.10, bb=0.095, ba=0.105), flq(0.10, bb=0.095, ba=0.105)
check("FL: fv 10c, rival 0.5c away -> bid 2c from fair (0.08) at half size, was 0.09 x200",
      (q0.bid, q0.bid_size, q1.bid, q1.bid_size, q1.bid_limit) == (0.09, 200, 0.08, 100, 0.08), (q0, q1))
check("FL: fv 10c -> the ask (good side) is unchanged, still at the 1c floor",
      (q1.ask, q1.ask_size) == (q0.ask, q0.ask_size) == (0.11, 200), (q0, q1))
q0, q1 = plain(0.90, bb=0.895, ba=0.905), flq(0.90, bb=0.895, ba=0.905)
check("FL: fv 90c -> ask 2c from fair (0.92) at half size; bid unchanged (0.89 x200)",
      (q1.ask, q1.ask_size, q1.bid, q1.bid_size) == (0.92, 100, 0.89, 200) and q0.ask == 0.91, (q0, q1))
check("FL: mid band (50c) identical to no bias", flq(0.50, bb=0.495, ba=0.505) == plain(0.50, bb=0.495, ba=0.505))
q1 = flq(0.10, bb=0.06, ba=0.14)
check("FL: wide book -> bad bid still pennies a rival outside its wider floor (0.065), at half size",
      (q1.bid, q1.bid_size) == (0.065, 100), q1)
off = Config(fl_bias_enabled=False)
check("FL: disabled -> fl_side gives no bias and the quote is today's",
      fl_side(0.10, "bid", off) == (None, 0.0, 1.0)
      and flq(0.10, cfg=off, bb=0.095, ba=0.105) == plain(0.10, bb=0.095, ba=0.105))
# A side that shrinks a position quotes normally.
q0, q1 = plain(0.10, inv=-500, bb=0.095, ba=0.105), flq(0.10, inv=-500, bb=0.095, ba=0.105)
check("FL: short 500 at 10c -> the bid unloads: same price and size as without the bias", q1 == q0, (q0, q1))
q0, q1 = plain(0.90, inv=500, bb=0.895, ba=0.905), flq(0.90, inv=500, bb=0.895, ba=0.905)
check("FL: long 500 at 90c -> the ask unloads: same as without the bias", q1 == q0, (q0, q1))
q1 = flq(0.10, inv=-30, bb=0.095, ba=0.105)
check("FL: short only 30 -> bid at the normal edge, but sized max(half size, position) = 100",
      (q1.bid, q1.bid_size) == (plain(0.10, inv=-30, bb=0.095, ba=0.105).bid, 100), q1)
q1 = flq(0.10, inv=500, bb=0.095, ba=0.105)
check("FL: long 500 at 10c -> the bid (adds) is still biased: half size, at least 2c below fair",
      q1.bid_size == 100 and q1.bid <= 0.08, q1)
# Skew: the bias is measured from the skewed reservation price, and nothing quotes through fair.
bad = []
for inv in (-2000, -500, -100, 0, 100, 500, 2000):
    for fv in (0.10, 0.15, 0.85, 0.90):
        q1, q0 = flq(fv, inv=inv, bb=fv - 0.005, ba=fv + 0.005), plain(fv, inv=inv, bb=fv - 0.005, ba=fv + 0.005)
        ok = ((q1.bid is None or (q1.bid <= fv + 1e-9 and q1.bid <= q0.bid + 1e-9 and q1.bid >= fv - 0.06 - 1e-9))
              and (q1.ask is None or (q1.ask >= fv - 1e-9 and q1.ask >= q0.ask - 1e-9 and q1.ask <= fv + 0.06 + 1e-9)))
        if not ok:
            bad.append((fv, inv, q0, q1))
check("FL + skew: never through fair, never tighter than without the bias, within skew + max_half_spread",
      not bad, bad[:2])
# max_half_spread cap
big = Config(fl_bias_enabled=True, fl_bad_side_extra_edge=0.05)
q1 = flq(0.10, cfg=big)
check("FL: extra edge 5c with no rival -> bad bid capped at max_half_spread (0.06), as today's empty-book bid",
      q1.bid == plain(0.10, cfg=big).bid == 0.06 and q1.bid_limit == 0.06, q1)
q1 = flq(0.10, cfg=big, bb=0.095, ba=0.105)
check("FL: extra edge 5c, rival 0.5c away -> bad bid at the 4c cap (0.06), never further", q1.bid == 0.06, q1)
# ref_only path: the extra adds to ref_only_min_edge
q0 = plain(0.10, bb=0.095, ba=0.105, min_edge=0.015)
q1 = flq(0.10, bb=0.095, ba=0.105, min_edge=0.015)
check("FL: ref_only edge 1.5c -> bad bid 2.5c from fair (0.075), good ask stays at 1.5c (0.115)",
      (q0.bid, q1.bid, q1.ask) == (0.085, 0.075, 0.115) and q1.ask == q0.ask, (q0, q1))
q1 = flq(0.10, inv=-1000, bb=0.095, ba=0.105, min_edge=0.015, reduce_size=1000)
check("FL + R5b: ref_only, short 1,000 -> the bid unloads at the normal ref_only edge and full reduce size",
      q1 == plain(0.10, inv=-1000, bb=0.095, ba=0.105, min_edge=0.015, reduce_size=1000), q1)
# Hysteresis: a market sitting at 20c doesn't flip every cycle.
prev, sides = None, []
for f in (0.195, 0.203, 0.198, 0.209, 0.211, 0.205, 0.199):
    prev = fl_side(f, prev, FL)[0]
    sides.append(prev)
check("FL: hysteresis at 20c: on below 0.20, stays on below 0.21, off from 0.21, back on only below 0.20",
      sides == ["bid", "bid", "bid", "bid", None, None, "bid"], sides)
prev, sides = None, []
for f in (0.805, 0.795, 0.79, 0.80, 0.801):
    prev = fl_side(f, prev, FL)[0]
    sides.append(prev)
check("FL: hysteresis at 80c: stays on above 0.79", sides == ["ask", "ask", None, None, "ask"], sides)
mid = Config(fl_bias_enabled=True, fl_mid_bid_extra_edge=0.005)
qm = flq(0.5, cfg=mid, bb=0.495, ba=0.505)
check("FL: optional mid-band bid extra edge -> mid-band bid 0.5c wider at full size, ask unchanged",
      fl_side(0.5, None, mid)[0] == "mid" and (qm.bid, qm.bid_size, qm.ask) == (0.485, 200, 0.51), qm)
check("FL: settings are live-overridable", all(k in M.OVERRIDABLE for k in (
      "fl_bias_enabled", "fl_low", "fl_high", "fl_hysteresis", "fl_bad_side_extra_edge", "fl_bad_side_size_factor",
      "fl_mid_bid_extra_edge")))


# Through the bot: Rep Ohio at 14c (bid is the bad side), Dem Ohio at 86c (ask), the Utah markets mid band.
def yes_orders(on):
    a, b = make_bot()
    b.cfg.fl_bias_enabled = on
    b.cycle()
    return {(o["exchangeId"], yes_side(o)): (o["priceLimit"] if o["side"] == "yes" else round(1 - o["priceLimit"], 3),
                                             o["quantity"]) for o in a.orders.values()}, b


off_o, _ = yes_orders(False)
on_o, b = yes_orders(True)
check("FL in the bot: Rep Ohio (14c) bid and Dem Ohio (86c) ask at half size (50); the 8c-wide house leaves the "
      "pennied prices (0.105 / 0.895) already more than 2c from fair, so they stay",
      on_o[("11", "buy")] == (0.105, 50) and on_o[("12", "sell")] == (0.895, 50)
      and off_o[("11", "buy")] == (0.105, 100) and off_o[("12", "sell")] == (0.895, 100), (off_o, on_o))
check("FL in the bot: good sides and the mid-band Utah markets unchanged",
      all(on_o[k] == off_o[k] for k in (("11", "sell"), ("12", "buy"), ("21", "buy"), ("21", "sell"), ("22", "buy"),
                                        ("22", "sell"))), (off_o, on_o))
check("FL in the bot: tag for the quote log line, counts in status.json",
      b.ex["11"].fl_tag == " fl:bid+1c" and b.ex["12"].fl_tag == " fl:ask+1c" and b.ex["21"].fl_tag == ""
      and b.health.get("fl_bias_markets") == {"bid": 1, "ask": 1}, (b.ex["11"].fl_tag, b.health.get("fl_bias_markets")))
a, b = make_bot()
b.cfg.fl_bias_enabled = True
b.cycle()
now_m = time.monotonic()
b.write_log.extend([(now_m, 18.0, True), (now_m, 16.0, True)])
b.cfg.burst_markets, b.size_plan = 2, {"11": 300, "12": 300, "21": 100, "22": 100}
b.update_burst(now_m)
for e in a.books:                                          # every book moves 1.5c: all quotes want repricing
    a.books[e] = {"bids": [lvl(round(l["price"] + 0.015, 3), 1000) for l in a.books[e]["bids"]],
                  "asks": [lvl(round(l["price"] + 0.015, 3), 1000) for l in a.books[e]["asks"]]}
b.feed = FakeFeed(); b.feed.push(dirty=set(a.books)); b.last_cycle_seconds = 0
b.update_burst = lambda now_m: None                       # hold burst mode on for this cycle
b.cycle()
sz = {(o["exchangeId"], yes_side(o)): o["quantity"] for o in a.orders.values() if o["exchangeId"] == "11"}
check("FL + burst: size factors multiply (Rep Ohio bid 100 x 0.5 x 0.5 = 25, ask 100 x 0.5 = 50)",
      b.burst and sz == {("11", "buy"): 25, ("11", "sell"): 50}, sz)

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
b.download_books(["11", "12"], b.orders_by_eid(M.util.utcnow())); b.last_record = -1e9; b.record({}, 2e9)
check("recorder: books downloaded again but unchanged -> no new rows",
      b.db.execute("select count(*) from books").fetchone()[0] == 4)
a.books["11"]["bids"].insert(0, lvl(0.12, 250))
b.download_books(["11"], b.orders_by_eid(M.util.utcnow())); b.last_record = -1e9; b.record({}, 3e9)
last = b.db.execute("select bids from books where eid='11' order by ts desc, rowid desc limit 1").fetchone()[0]
check("recorder: a rival steps in at 0.12 x 250 -> new row with both levels",
      json.loads(last) == [[0.12, 250.0], [0.10, 1000.0]], last)
b.cfg.record_books = False
a.books["11"]["bids"].insert(0, lvl(0.125, 50))
b.download_books(["11"], b.orders_by_eid(M.util.utcnow())); b.last_record = -1e9; b.record({}, 4e9)
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
