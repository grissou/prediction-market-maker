"""P12 ops: risk_unheld_legs - the probability used for an unheld race leg with no fair value in settlement_risk /
total_worst_case ("half" = 0.5 as before; "ref" = its Polymarket reference, else the race's residual, else 0.5).
Run:  python tests/test_risk_unheld.py"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                     # noqa: E402
import mm_bot as M                             # noqa: E402

logging.basicConfig(level=logging.ERROR)
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


c = M.Config()
check("default half", c.risk_unheld_legs == "half")
check("overridable one-of", M.OVERRIDABLE.get("risk_unheld_legs") == ("half", "ref") and "risk_unheld_legs" in M.ONE_OF_SETTINGS)
good, bad = M.validate_overrides({"risk_unheld_legs": "ref"}, c)
check("ref accepted", not bad and good.get("risk_unheld_legs") == "ref", (good, bad))
good, bad = M.validate_overrides({"risk_unheld_legs": "mean"}, c)
check("other refused", bool(bad))

api, bt = make_bot(live=False)
# race Ohio: 11 Rep, 12 Dem. Hold 1000 YES on 11 (fv 0.95); 12 has no fair value this cycle.
for e in bt.ex.values():
    e.last_fv = None
inv = {"11": 1000.0}
fvs = {"11": 0.95}
bt.cur_refs = {}
legs_half = bt.risk_legs(inv, fvs, ["11", "12"])
check("half: unheld unpriced leg at 0.5", legs_half[1] == (0.0, 0.5), legs_half)
r_half = bt.settlement_risk(inv, fvs, 0.0)
w_half = bt.total_worst_case(inv, fvs)
bt.cfg.risk_unheld_legs = "ref"
legs_res = bt.risk_legs(inv, fvs, ["11", "12"])
check("ref, no reference: the residual 1 - 0.95 = 0.05", abs(legs_res[1][1] - 0.05) < 1e-9, legs_res)
r_ref = bt.settlement_risk(inv, fvs, 0.0)
check("ref: settlement risk falls (the race is no longer a 0.655/0.345 toss-up)", r_ref < r_half, (r_ref, r_half))
check("ref: worst case unchanged (a long's worst outcome does not depend on the other leg's p)", abs(w_half - bt.total_worst_case(inv, fvs)) < 1e-9)
bt.cur_refs = {"12": 0.08}
legs_r = bt.risk_legs(inv, fvs, ["11", "12"])
check("ref with a reference: uses it", legs_r[1] == (0.0, 0.08), legs_r)
# held legs always keep risk_fv
bt.cfg.risk_unheld_legs = "ref"
legs_both = bt.risk_legs({"11": 1000.0, "12": -200.0}, {"11": 0.95}, ["11", "12"])
check("a held leg is never replaced by the reference", legs_both[1][0] == -200.0 and legs_both[1][1] == bt.risk_fv("12", {"11": 0.95}, ["11", "12"]))
# two unpriced legs share the residual
api2, bt2 = make_bot(live=False)
for e in bt2.ex.values():
    e.last_fv = None
bt2.cfg.risk_unheld_legs = "ref"
bt2.cur_refs = {}
legs3 = bt2.risk_legs({"11": 500.0}, {"11": 0.4}, ["11", "12", "21"])
check("two unpriced legs share the residual 0.6 -> 0.3 each", all(abs(p - 0.3) < 1e-9 for q, p in legs3 if q == 0.0), legs3)
# flag off: identical to the old inline expression
bt2.cfg.risk_unheld_legs = "half"
old = [(({"11": 500.0}).get(e, 0.0), bt2.risk_fv(e, {"11": 0.4}, ["11", "12"]) if ({"11": 500.0}).get(e) else ({"11": 0.4}.get(e) or bt2.ex[e].last_fv or 0.5)) for e in ["11", "12"]]
check("half: identical to the previous expression", bt2.risk_legs({"11": 500.0}, {"11": 0.4}, ["11", "12"]) == old)

n_ok, n = sum(RESULTS), len(RESULTS)
print(f"{n_ok}/{n} passed")
sys.exit(0 if n_ok == n else 1)
