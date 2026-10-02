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
# FAVOURITE-LONGSHOT SIDE BIAS (fl_*): below 20c the bid is the bad side, above 80c the ask

FL = Config()


def flq(fv, inv=0, bb=None, ba=None, cfg=FL, prev=None, **kw):
    side, e, f = fl_side(fv, prev, cfg)
    side = "bid" if side == "mid" else side
    return compute_quote(fv, inv, inv, bb, ba, cfg, order_size=200, bias_side=side, bias_edge=e, bias_size=f, **kw)


def plain(fv, inv=0, bb=None, ba=None, cfg=FL, **kw):
    return compute_quote(fv, inv, inv, bb, ba, cfg, order_size=200, **kw)


check("FL: on by default, 20c / 80c, +1c, half size, 1c hysteresis, mid-band off",
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
big = Config(fl_bad_side_extra_edge=0.05)
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
mid = Config(fl_mid_bid_extra_edge=0.005)
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
