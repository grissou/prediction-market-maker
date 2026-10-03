"""Package 5 yardstick pins (PLAN_POLY_BIAS.md 3.1): live_sim's world knobs and liquidation-marked fields.
Run: python tests/test_live_sim_marks.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_sim as L                                   # noqa: E402
import strategy_sim as S                               # noqa: E402

OK = FAIL = 0


def check(name, cond):
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        print("FAIL", name)


# 1. Knobs at 0 = the simulator before Package 5 (numbers recorded at fd28b0a + this file's parent, seed 1/2, 0.05 h)
r = L._one((1, 0.05, "quiet", {}))
check("pin quiet", (r["pnl"], r["cap_end"], r["writes_pm"], r["shares"], r["pnl_lag"], r["wc_end"])
      == (23, 0.878, 37.97, 6024, -341, 43294))
r0 = L._one((1, 0.05, "quiet", {"_rival_anchor": 0, "_world_tilt": 0, "_world_tilt_growth": 0}))
check("explicit zeros identical", {k: r0[k] for k in r} == r)
r = L._one((2, 0.05, "news", {}))
check("pin news", (r["pnl"], r["cap_end"], r["writes_pm"], r["shares"]) == (26, 0.883, 31.87, 2601))
for k in ("pnl_mid", "pnl_liq", "mk15_mid", "exit_ratio", "hold_med", "pick_cost", "wc_end"):
    check(f"key {k} printed", k in L.KEYS and k in r)

# 1b. The live reduce-only backstop (_bg_wc): 0 = never; huge = reduce-only every cycle, so nothing is added
r = L._one((1, 0.02, "quiet", {"_bg_wc": 1e6, "worst_case_backstop_frac": 0.8}))
check("backstop pinned: reduce-only every cycle", r["ro_frac"] == 1.0 and r["wc_start"] > 1e6)
rq = L._one((1, 0.02, "quiet", {}))
check("backstop off: ro_frac 0, wc_start 0", rq["ro_frac"] == 0.0 and rq["wc_start"] == 0)

# 1c. Package 5 mirrors charge a take's writes (3) and refuse without room
st0 = L.LiveSim(1, 0.01, "quiet", S.make_cfg({}))
st0.wlog, st0.writes = [], 0
check("take charged 3 writes", st0.take_writes_ok(10) and st0.writes == 3 and st0.wlog == [(10, 3)])
st0.wlog = [(5, st0.wcap - 2)]
check("no room: refused", not st0.take_writes_ok(10) and st0.take_refused == 1)
rp = L._one((1, 0.05, "quiet", {}))
check("base unchanged by the charge (flags off)", rp["pnl"] == 23 and rp["writes_pm"] == 37.97)

# 2. The tilt world: start consensus unchanged (residual bias), tilt grows, rivals anchored
cfg = S.make_cfg({})
a = L.LiveSim(3, 0.02, "quiet", cfg)
b = L.LiveSim(3, 0.02, "quiet", S.make_cfg({}), rival_anchor=1, world_tilt=0.05, world_tilt_growth=0.002)
for ma, mb in zip(a.mkts, b.mkts):
    check("residual bias", abs((mb.bias - 0.05 * (mb.p0 - 0.5)) - ma.bias) < 1e-12)
check("tilt term", abs(b.tilt_term(3600, 0.9) - (-0.052 * 0.4)) < 1e-12)
b2 = L.LiveSim(3, 0.02, "quiet", S.make_cfg({}), rival_anchor=1, world_tilt=0.05, world_tilt_add=0.08)
check("_world_tilt_add: same residual bias, more tilt", all(abs(x.bias - y.bias) < 1e-12 for x, y in zip(b.mkts, b2.mkts))
      and abs(b2.tilt_term(0, 0.9) - (-0.13 * 0.4)) < 1e-12)
b.run()
m = b.mkts[0]
p, c = b.paths_by[id(m)]
check("rival path = Polymarket + bias + tilt", abs(b.rp[id(m)][50] - (p[50] + m.bias + b.tilt_term(50, p[50]))) < 1e-12)
check("informed at the anchored price", b.informed_px(m, 50, p) == b.rp[id(m)][50])
a.run()
check("anchor 0: no rival path", a.rp == {} and a.informed_px(a.mkts[0], 5, [0.4] * 10) == 0.4)


# 3. Scripted marks on a stub (one market)
class Stub(L.LiveSim):
    def __init__(self):            # no world: fields set by hand
        pass


def stub(inv0, fills, takes=(), others=((0.465, 100), (0.475, 100)), c=0.47, lots0=None):
    st = Stub()
    mk = S.Mkt("busy", 0.50, 0.0, 1, False, [])
    mk.orders = [S.Order("h", True, others[0][0], others[0][1], 0), S.Order("h", False, others[1][0], others[1][1], 0)]
    mk.inv, mk.cash = inv0, -inv0 * 0.50
    st.mkts, st.T = [mk], 7200
    st.inv0, st.cash0 = {id(mk): inv0}, {id(mk): -inv0 * 0.50}
    st.lots0 = {id(mk): lots0 or ([[inv0, -3600.0]] if inv0 else [])}
    st.cpath = {id(mk): [c] * 7201}
    st.mid0 = {id(mk): st.mid_px(mk, c)}
    st.liq0 = {id(mk): st.liq_px(mk, inv0, c)}
    st.fills, st.takes = [], list(takes)
    for t, side, q, px in fills:
        mk.inv += side * q
        mk.cash -= side * q * px
        st.fills.append((t, mk, side, q, px, 0.5, 0, "noise"))
    return st, mk


# one long bought 3c under Polymarket (0.50), the tournament gap held (consensus 0.47, book 0.465 / 0.475)
st, mk = stub(0.0, [(10, 1, 1000, 0.47)])
f = st.liq_fields(7200)
pnl_poly = mk.cash + mk.inv * 0.50
check("Polymarket mark > 0", pnl_poly > 0)
check("pnl_mid ~ 0", abs(f["pnl_mid"]) <= 0.5)
check("pnl_liq < 0", f["pnl_liq"] < 0 and f["pnl_liq"] == -5)
check("mk15_mid 0", f["mk15_mid"] == 0.0)

# no trades: pnl_mid = inv0 x (mid_T - mid0), pnl_liq = inv0 x (liq_T - liq0); liquidation never above the mid
st, mk = stub(1000.0, [])
mk.orders = [S.Order("h", True, 0.48, 100, 0), S.Order("h", False, 0.50, 100, 0)]
f = st.liq_fields(7200)
check("no-trade pnl_mid", f["pnl_mid"] == round(1000 * (0.49 - 0.47)))
check("no-trade pnl_liq", f["pnl_liq"] == round(1000 * (0.48 - 0.465)))
check("liq <= mid (long)", st.liq_px(mk, 1000, 0.47) <= st.mid_px(mk, 0.47))
check("liq >= mid (short)", st.liq_px(mk, -1000, 0.47) >= st.mid_px(mk, 0.47))
mk.orders = []
check("no bid: consensus - 2c", abs(st.liq_px(mk, 5, 0.47) - 0.45) < 1e-12)
check("no ask: consensus + 2c", abs(st.liq_px(mk, -5, 0.47) - 0.49) < 1e-12)

# scripted buy-then-sell from flat: exit_ratio 1, hold exactly 2 h
st, mk = stub(0.0, [(0, 1, 100, 0.47), (7200, -1, 100, 0.48)])
f = st.liq_fields(7200)
check("exit_ratio 1", f["exit_ratio"] == 1.0)
check("hold_med 2 h", f["hold_med"] == 2.0)
# starting long 300 (lot aged 1 h at the start), sell 100 at 1 h: FIFO closes the old lot, held 2 h; add 0
st, mk = stub(300.0, [(3600, -1, 100, 0.47)])
f = st.liq_fields(7200)
check("reduce only: exit_ratio = red / max(add, 1)", f["exit_ratio"] == 100.0)
check("starting lot age counted", f["hold_med"] == 2.0)
# a fill through zero: 300 long, sell 500 -> 300 reduce, 200 add
st, mk = stub(300.0, [(60, -1, 500, 0.47)])
red, add, closed = st.replay()
check("through zero split", (red, add) == (300.0, 200.0))
# takes count too
st, mk = stub(300.0, [], takes=[(60, None, -1, 100.0, 0.46)])
st.takes = [(60, mk, -1, 100.0, 0.46)]
red, add, closed = st.replay()
check("takes replayed", (red, add) == (100.0, 0.0))

print(f"test_live_sim_marks: {OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
