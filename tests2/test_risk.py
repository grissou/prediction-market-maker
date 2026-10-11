"""
Tests for mmbot2/risk.py: the worst case, the correlated measure, reduce-only, the reserve pause, the kill switch,
the cash an order needs, and every rule of the Gate in priority order.

Run:  python3 tests2/test_risk.py      (exit code 0 = all passed)
"""
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mmbot2.config import Settings                                          # noqa: E402
from mmbot2.state import Account, Book, Market, Order, View                 # noqa: E402
from mmbot2 import risk as R                                                # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   [{detail}]"))


def mk(eid, race, party, state=None):
    return Market(eid, eid, race, party, state, None)


MARKETS = {m.eid: m for m in (
    mk("rOH", "Ohio Senate", "Republican", "OH"), mk("dOH", "Ohio Senate", "Democratic", "OH"),
    mk("rRI", "Rhode Island 1", "Republican", "RI"), mk("dRI", "Rhode Island 1", "Democratic", "RI"),
    mk("rUS", "U.S. Senate", "Republican"), mk("dUS", "U.S. Senate", "Democratic"))}
P = {"rOH": 0.6, "dOH": 0.4, "rRI": 0.05, "dRI": 0.95, "rUS": 0.5, "dUS": 0.5}


def view(positions=None, cash=50000.0, value=100000.0, read_at=0.0, mono=10.0, p=None, resting=None, books=None):
    return View(now=datetime(2026, 10, 10, tzinfo=timezone.utc), mono=mono, markets=MARKETS, books=books or {},
                positions=positions or {}, resting=resting or {},
                account=Account(value=value, cash=cash, start=100000.0, read_at=read_at),
                p=dict(P if p is None else p))


def gate(v, s=None, was_ro=False, risk=None):
    s = s or Settings()
    return R.Gate(v, risk or R.assess(v, s, was_ro), s)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---- worst case on a 2-race book ----
v = view({"rOH": 1000, "dRI": -2000})      # long 1000 Rep Ohio @0.6; short 2000 Dem RI (NO @ 0.05)
r = R.assess(v, Settings(), False)
# Ohio: worth 600, pays 0 if Dem wins -> 600; RI: NO worth 2000 x 0.05 = 100, pays 0 if Dem wins -> 100
check("worst case sums each race's worst outcome", near(r.worst_case, 700), r.worst_case)
check("hedged race loses nothing", near(R.race_worst_loss([(500, 0.14), (500, 0.86)]), 0.0))
check("lone market worst case", near(R.race_worst_loss([(-100, 0.3)]), 70.0))

# ---- correlated vs backstop ----
# party delta = +1000 (Rep YES) + 0 (short Dem RI: -2000 x -1 is +2000) -> 3000 shares; 0.15 x 3000 = 450 + 3 sd
sd = (1000 ** 2 * 0.24 + 2000 ** 2 * 0.05 * 0.95) ** 0.5
expect = min(700, 0.15 * 3000 + 3 * sd)
check("correlated = min(worst, swing + 3 sd)", near(r.correlated, expect), (r.correlated, expect))
big = view({"rOH": 100, "dOH": -100, "rRI": 100, "dRI": -100, "rUS": 50})
rb = R.assess(big, Settings(), False)
check("correlated never above the plain worst case", rb.correlated <= rb.worst_case + 1e-9)
# many small independent races: correlated well below the sum (why release 7 replaced the sum)
many = {f"r{i}": mk(f"r{i}", f"Race {i}", None) for i in range(50)}
vm = View(datetime(2026, 10, 10, tzinfo=timezone.utc), 0.0, many, {}, {e: 100 for e in many}, {},
          Account(100000.0, 50000.0), {e: 0.5 for e in many})
rm = R.assess(vm, Settings(), False)
check("diversified book: correlated below backstop", rm.correlated < 0.6 * rm.worst_case,
      (rm.correlated, rm.worst_case))

# ---- reduce-only with hysteresis ----
s = Settings()
def ro_at(worst_frac_value, was):      # a single lone short whose worst case is fixed; vary the account value
    vv = View(datetime(2026, 10, 10, tzinfo=timezone.utc), 0.0, {"x": mk("x", "X", None)}, {}, {"x": -10000}, {},
              Account(value=worst_frac_value), {"x": 0.5})
    return R.assess(vv, s, was)
# worst = 5000, correlated = min(5000, 3 x 5000) = 5000; cap 40%
check("below the cap: not reduce-only", not ro_at(5000 / 0.39, False).reduce_only)
check("above the cap: reduce-only", ro_at(5000 / 0.41, False).reduce_only)
check("hysteresis: at 39% stays reduce-only once in", ro_at(5000 / 0.39, True).reduce_only)
check("hysteresis: below 37% leaves", not ro_at(5000 / 0.365, True).reduce_only)
nov = R.assess(replace(v, account=Account(value=None)), s, True)
check("no account value keeps reduce-only as it was", nov.reduce_only and nov.room_wc is None)
check("backstop also forces reduce-only",
      R.assess(view({"rOH": 1000}, value=1000.0), replace(s, max_worst_case_frac=1.0,
                                                           worst_case_backstop_frac=0.5), False).reduce_only)

# ---- the market-making reserve pause ----
rr = ro_at(5000 / 0.3, False)          # value 16.7k: room_corr = 0.4 x 16.7k - 5k = 1.67k < 4k reserve
check("room below reserve pauses value adds", rr.adds_paused)
roomy = ro_at(1e6, False)
check("roomy book: no pause", not roomy.adds_paused)
vr = View(datetime(2026, 10, 10, tzinfo=timezone.utc), 0.0, {"x": mk("x", "X", None)}, {}, {"x": -10000}, {},
          Account(value=23000.0), {"x": 0.5})   # room_corr 0.4 x 23k - 5k = 4.2k: above 4k, below 4.4k
s0 = replace(s, mm_risk_reserve_wc=0.0)
check("pause resumes only at 1.1x the reserve", R.assess(vr, s0, False, was_paused=True).adds_paused
      and not R.assess(vr, s0, False, was_paused=False).adds_paused)
vm = replace(vr, account=Account(value=20000.0))   # room_corr 0.4 x 20k - 5k = 3k: below the 4k reserve
check("the book's own risk pauses value adds", R.assess(vm, s0, False).adds_paused)
check("market making's own inventory is credited back first (mm_room_guard)",
      not R.assess(vm, s0, False, mm_shares={"x": -10000}).adds_paused)
check("...but only what market making holds", R.assess(vm, s0, False, mm_shares={"x": -1000}).adds_paused)

# ---- kill switch ----
check("kill switch at 30% below start", R.kill_switch_hit(Account(value=69999.0, start=100000.0), s))
check("kill switch not above", not R.kill_switch_hit(Account(value=70001.0, start=100000.0), s))
check("kill switch needs a value", not R.kill_switch_hit(Account(value=None), s))

# ---- cash_need ----
check("bid pays its price", near(R.cash_need(Order("a", True, 0.4, 100, "mm"), 0), 40))
check("ask from flat buys NO at 1 - price", near(R.cash_need(Order("a", False, 0.4, 100, "mm"), 0), 60))
check("ask on YES held is free", near(R.cash_need(Order("a", False, 0.4, 100, "mm"), 150), 0))
check("covered sell NO is free", near(R.cash_need(Order("a", True, 0.4, 100, "mm"), -100), 0))
check("covered part free, the rest pays", near(R.cash_need(Order("a", True, 0.4, 100, "mm"), -30), 28))

# ---- the gate: cash and priority ----
g = gate(view(cash=1025.0))            # 1000 free after the 25 margin
a = g.admit(Order("rUS", True, 0.5, 3000, "alloc"))
check("cash trims the first order", a is not None and a.size == 2000, a)
b = g.admit(Order("dUS", True, 0.5, 100, "mm"))
check("priority: the later order lacks the cash", b is None)
check("refused_usd counts the cash wanted", near(g.refused_usd.get("alloc", 0), 500)
      and near(g.refused_usd.get("mm", 0), 50), g.refused_usd)
c = g.admit(Order("rOH", False, 0.6, 10, "ladder"))
check("an ask from flat buys NO: needs cash too", c is None)

g = gate(view({"rOH": 100}, cash=0.0))
o = g.admit(Order("rOH", False, 0.6, 100, "refill"))
check("covered sale passes with no cash", o is not None and o.size == 100, o)
check("second ask on the same YES is not covered", g.admit(Order("rOH", False, 0.6, 100, "ladder")) is None)
g = gate(view({"dRI": -300}, cash=0.0))
o = g.admit(Order("dRI", True, 0.95, 500, "value"))
check("covered sell NO trimmed to the NO held", o is not None and o.size == 300, o)

g = gate(view(cash=100000.0, read_at=0.0, mono=301.0))
check("stale account read: only cash-free orders", g.admit(Order("rUS", True, 0.5, 10, "mm")) is None)
g = gate(view({"rUS": 50}, cash=100000.0, read_at=0.0, mono=301.0))
check("stale account read: covered sale still passes", g.admit(Order("rUS", False, 0.5, 50, "refill")) is not None)

kept = Order("rUS", True, 0.5, 200, "mm", oid="x1")
g = gate(view(cash=25.0, resting={"rUS": [kept]}))
o = g.admit(Order("rUS", True, 0.5, 200, "mm"))
check("an order the bot keeps resting is already paid for", o is not None and o.size == 200, o)
o = g.admit(Order("rUS", True, 0.49, 10, "mm"))
check("...but a new one still needs cash", o is None)

# ---- reduce-only gate ----
v = view({"rOH": 100, "dOH": -50})
rk = replace(R.assess(v, s, False), reduce_only=True)
g = gate(v, risk=rk)
check("reduce-only: an add is refused", g.admit(Order("rUS", True, 0.5, 10, "mm")) is None)
o = g.admit(Order("rOH", False, 0.6, 300, "refill"))
check("reduce-only: a sale is trimmed to the holding (no flip)", o is not None and o.size == 100, o)
o = g.admit(Order("dOH", True, 0.4, 80, "ladder"))
check("reduce-only: buying back NO passes up to the NO held", o is not None and o.size == 50, o)

# ---- reserve pause in the gate ----
rp = replace(R.assess(view(), s, False), adds_paused=True)
g = gate(view(), risk=rp)
check("paused: allocator buy refused", g.admit(Order("rUS", True, 0.5, 10, "alloc")) is None)
check("paused: market making goes on", g.admit(Order("rUS", True, 0.5, 10, "mm")) is not None)

# ---- per-market cap ----
g = gate(view({"rUS": 18000}, cash=1e6), s=replace(s, max_bloc_delta_frac=1.0))    # 18000 x 0.5 = 9000 held; cap 10k
o = g.admit(Order("rUS", True, 0.5, 5000, "alloc"))
check("per-market cap trims the add to 1000 more", o is not None and o.size == 2000, o)
o = g.admit(Order("rUS", False, 0.5, 18000 + 30000, "mm"))
check("other side: holding sold first, then up to the cap short", o is not None and o.size == 18000 + 20000, o)

# ---- per-state cap (Rhode Island: shorts to a longshot buyer) ----
# short 14000 Rep RI at p 0.05: the NO is worth 14000 x 0.95 = 13.3k of the state's 15k
g = gate(view({"rRI": -14000}, cash=1e6), s=replace(s, market_max_usd=1e6))
o = g.admit(Order("rRI", False, 0.06, 5000, "value"))
# room 15000 - 13300 = 1700 at 0.95 a share -> 1789 shares
check("state cap trims a longshot short", o is not None and o.size == 1789, o)
check("state cap: the other leg of the state shares the room", g.admit(Order("dRI", True, 0.95, 10, "mm")) is None)
check("national markets have no state cap", g.admit(Order("rUS", True, 0.5, 100, "mm")) is not None)
check("state cap: a reducing order still passes", g.admit(Order("rRI", True, 0.05, 1000, "ladder")) is not None)

# ---- bloc-delta cap ----
rb = R.assess(view(), s, False)
sens = rb.bloc_sens["rUS"]
check("control market sensitivity uses bloc_rho_control", near(sens, 0.85 ** 0.5 * 0.3989422804, 1e-6), sens)
check("Dem YES has the opposite sign", near(rb.bloc_sens["dUS"], -sens))
g = gate(view(cash=1e6), s=replace(s, market_max_usd=1e9))
o = g.admit(Order("rUS", True, 0.5, 100000, "alloc"))     # cap 5000 $/sd -> 5000 / 0.368 = 13,6xx shares
check("bloc cap trims an order that grows |bloc delta|", o is not None and o.size == int(5000 / sens), o)
check("beyond the cap the same direction is refused", g.admit(Order("dUS", False, 0.5, 10, "alloc")) is None)
check("the opposite direction passes", g.admit(Order("dUS", True, 0.5, 10, "alloc")) is not None)

# ---- risk prices for unpriced markets ----
v = view({"dOH": -100}, p={"rOH": 0.7, "rUS": 0.5, "dUS": 0.5, "rRI": 0.1, "dRI": 0.9})
check("unpriced leg takes the race's residual", near(R.assess(v, s, False).p["dOH"], 0.3))
vlone = View(datetime(2026, 10, 10, tzinfo=timezone.utc), 0.0, {"x": mk("x", "X", None)},
             {"x": Book([(0.2, 10)], [(0.3, 10)])}, {"x": 10}, {}, Account(100000.0), {})
check("unpriced lone market uses the book mid, not 0.5", near(R.assess(vlone, s, False).p["x"], 0.25))

n = len(RESULTS)
print(f"{sum(RESULTS)}/{n} passed")
sys.exit(0 if all(RESULTS) else 1)
