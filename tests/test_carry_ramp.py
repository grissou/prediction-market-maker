"""
Offline tests for T2.3, the carry ramp (ref_tilt_carry_days, HARD GATE, default 0): the pure helper carry_ramp and
its use in blend_fv (off = identical, ramp values at N days, N/2 and 0 h, bounds, ref_tilt_enabled off = no effect),
plus the setting, which is NOT live-overridable (hard gate). No network.

Run:  python tests/test_carry_ramp.py      (exit code 0 = all passed)
"""
import dataclasses
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import mm_bot as M                                        # noqa: E402

RESULTS = []
INF = float("inf")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-12):
    return a is not None and b is not None and abs(a - b) <= tol


print("--- setting")
c = M.Config()
check("default 0 (off)", c.ref_tilt_carry_days == 0.0)
check("HARD GATE: NOT live-overridable (not in OVERRIDABLE)", "ref_tilt_carry_days" not in M.OVERRIDABLE)
_f = [f.name for f in dataclasses.fields(M.Config)]
check("in Config after the T2.5 block", _f.index("ref_tilt_carry_days") > _f.index("pair_unwind_max_cost"))
for v in (3.0, 0.0, 31.0):
    good, bad = M.validate_overrides({"ref_tilt_carry_days": v}, c)
    check(f"override to {v} refused (code change + restart only)", good == {} and len(bad) == 1, (good, bad))

print("--- carry_ramp")
s = 0.06
check("days 0 (off): s unchanged at any horizon", all(M.carry_ramp(s, h, 0.0) == s for h in (0, 1, 24, 1000, INF)))
check("unknown close (inf): s unchanged", M.carry_ramp(s, INF, 3.0) == s)
check("N days out: full s", close(M.carry_ramp(s, 72.0, 3.0), s))
check("beyond N days: full s", close(M.carry_ramp(s, 500.0, 3.0), s))
check("N/2 out: s/2", close(M.carry_ramp(s, 36.0, 3.0), s / 2))
check("one quarter of the way: s/4", close(M.carry_ramp(s, 18.0, 3.0), s / 4))
check("at the close (0 h): 0", M.carry_ramp(s, 0.0, 3.0) == 0.0)
check("past the close (negative hours): 0, never negative", M.carry_ramp(s, -5.0, 3.0) == 0.0)
vals = [M.carry_ramp(s, h, d) for d in (0.0, 0.5, 1.0, 3.0, 7.0, 30.0) for h in range(-48, 24 * 40, 7)]
check("never negative, never above s", all(0.0 <= v <= s for v in vals))
check("monotone in hours to close",
      all(M.carry_ramp(s, h, 3.0) <= M.carry_ramp(s, h + 1, 3.0) for h in range(-5, 100)))

print("--- blend_fv")
on = dataclasses.replace(c, ref_tilt_enabled=True)
ramp = dataclasses.replace(on, ref_tilt_carry_days=3.0)
bfv, r, legs = 0.70, 0.80, 2
w = c.ref_weight
for h in (INF, 500.0, 72.0, 36.0, 1.0, 0.0):
    check(f"carry off: identical to T2.1 blend ({h} h)",
          M.blend_fv(bfv, r, on, s, legs, False, h) == M.blend_fv(bfv, r, on, s, legs, False))
check("default hours argument: identical to the T2.1 call",
      M.blend_fv(bfv, r, ramp, s, legs) == M.blend_fv(bfv, r, on, s, legs))
check("N days out: full tilt",
      close(M.blend_fv(bfv, r, ramp, s, legs, False, 72.0), (1 - w) * bfv + w * M.tilted_ref(r, s, legs)))
check("N/2 out: half the tilt",
      close(M.blend_fv(bfv, r, ramp, s, legs, False, 36.0), (1 - w) * bfv + w * M.tilted_ref(r, s / 2, legs)))
check("at the close: raw Polymarket",
      close(M.blend_fv(bfv, r, ramp, s, legs, False, 0.0), (1 - w) * bfv + w * r))
off = dataclasses.replace(c, ref_tilt_enabled=False, ref_tilt_carry_days=3.0)
plain = (1 - w) * bfv + w * r
check("ref_tilt_enabled off: carry has no effect at any horizon",
      all(M.blend_fv(bfv, r, off, s, legs, False, h) == plain for h in (INF, 72.0, 36.0, 0.0)))
check("headline market with ref_tilt_headline off: no effect",
      M.blend_fv(bfv, r, ramp, s, legs, True, 36.0) == plain)
check("the estimator is untouched (s passed in is not modified)", s == 0.06)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
