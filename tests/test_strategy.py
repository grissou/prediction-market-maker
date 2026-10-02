"""
Offline tests for the strategy changes on claude/pr2-strategy (Builder run): pure quoting functions, the
Bot wiring, and a short run of the strategy simulator (tests/strategy_sim.py).

Run:  python tests/test_strategy.py      (exit code 0 = all passed)
"""
import logging
import math
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
