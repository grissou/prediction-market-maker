"""
Offline tests for T2.1, the tilt-corrected reference (ref_tilt_*): tilted_ref, blend_fv, the TiltEstimator
(recovery, minimum markets, winsorising, clip, EMA half-life, persistence), the main loop (flag off identical,
flag on tilted, headline gate, which markets feed the estimator, status.json and restore), and the strategy_sim
mirror. No network.

Run:  python tests/test_tilt.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeRefs, make_bot                      # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


print("--- settings")
c = M.Config()
check("defaults: 50 markets, 30 min half-life, max 20%, winsor 8c (the estimator's knobs; applying the tilt to",
      " quotes was removed on simplify)",
      (c.ref_tilt_min_markets, c.ref_tilt_halflife_min, c.ref_tilt_max, c.ref_tilt_winsor) == (50, 30.0, 0.20, 0.08))
names = ["ref_tilt_min_markets", "ref_tilt_halflife_min", "ref_tilt_max", "ref_tilt_winsor"]
_ov = list(M.OVERRIDABLE)
_i = _ov.index(names[0])
check("all four live-overridable, one block after the savers",
      _ov[_i:_i + 4] == names and _i > _ov.index("ttl_expire_grace_seconds"), _ov[_i:_i + 4])
good, bad = M.validate_overrides({"ref_tilt_enabled": True, "ref_tilt_max": 0.5}, c)
check("override ranges: the removed flag and ref_tilt_max 0.5 both refused", not good and len(bad) == 2, (good, bad))

print("--- tilted_ref (the estimator's model) and blend_fv")
check("two legs: 0.80 with s 5% -> 0.5 + 0.95 x 0.30 = 0.785", close(M.tilted_ref(0.80, 0.05, 2), 0.785))
check("two legs: 0.10 with s 5% -> 0.12 (longshots pulled up)", close(M.tilted_ref(0.10, 0.05, 2), 0.12))
check("three legs: c = 1/3", close(M.tilted_ref(0.6, 0.1, 3), 1 / 3 + 0.9 * (0.6 - 1 / 3)))
check("one leg counts as c = 0.5", close(M.tilted_ref(0.3, 0.1, 1), 0.32) and close(M.tilted_ref(0.3, 0.1, 0), 0.32))
check("s = 0: unchanged", close(M.tilted_ref(0.37, 0.0, 4), 0.37))
off = M.Config()
for bfv, r in ((0.14, 0.30), (0.5123, 0.6789), (0.91, 0.88)):
    check(f"blend_fv == the old expression bit for bit (book {bfv}, r {r})",
          M.blend_fv(bfv, r, off) == (1 - off.ref_weight) * bfv + off.ref_weight * r)

print("--- TiltEstimator")
rng = random.Random(7)


def synth(n, s, noise=0.005, legs=2):
    out = []
    for _ in range(n):
        r = rng.uniform(0.03, 0.97)
        out.append((r, r - s * (r - 1.0 / legs) + rng.gauss(0, noise), legs))   # gap r - book = s(r - c) + noise
    return out


est = M.TiltEstimator(M.Config())
check("starts at 0, not ready", est.s == 0.0 and est.n == 0 and not est.ready)
est.update(synth(200, 0.05), 0.0)
check("recovers s = 0.05 from 200 synthetic markets (first estimate taken as is)", abs(est.s - 0.05) < 0.004, est.s)
check("n = sample count", est.n == 200)
e3 = M.TiltEstimator(M.Config())
e3.update(synth(200, 0.08, legs=3), 0.0)
check("three-leg races: recovers 0.08 with c = 1/3", abs(e3.s - 0.08) < 0.004, e3.s)
held = est.s
est.update(synth(49, 0.11), 60.0)
check("49 markets (< 50): holds the last value", est.s == held and est.n == 49, (est.s, est.n))
fresh = M.TiltEstimator(M.Config())
fresh.update(synth(49, 0.11), 0.0)
check("...and a fresh estimator stays at 0", fresh.s == 0.0 and not fresh.ready)
base = synth(60, 0.05, noise=0.0)
e_a, e_b = M.TiltEstimator(M.Config()), M.TiltEstimator(M.Config())
e_a.update(base, 0.0)
e_b.update(base + [(0.95, 0.35, 2)], 0.0)                 # one wild 60c gap at the tail
nw = M.Config(); nw.ref_tilt_winsor = 1.0
e_c = M.TiltEstimator(nw)
e_c.update(base + [(0.95, 0.35, 2)], 0.0)
check("winsorised: one 60c outlier moves s by < 1 point (clipped at 8c)", abs(e_b.s - e_a.s) < 0.01, (e_a.s, e_b.s))
check("...without winsorising it would move s by > 2 points", e_c.s - e_a.s > 0.02, (e_a.s, e_c.s))
cm = M.Config(); cm.ref_tilt_max = 0.12
e_m = M.TiltEstimator(cm)
e_m.update(synth(200, 0.20, noise=0.0), 0.0)
check("clipped at ref_tilt_max 0.12 (true 0.20, gaps under 8c)", e_m.s == 0.12, e_m.s)
e_n = M.TiltEstimator(M.Config())
e_n.update(synth(200, -0.05, noise=0.0), 0.0)
check("clipped at 0 (an inverse tilt)", e_n.s == 0.0, e_n.s)
e_h = M.TiltEstimator(M.Config())
e_h.update(synth(100, 0.02, noise=0.0), 0.0)
s0 = e_h.s
e_h.update(synth(100, 0.06, noise=0.0), 30 * 60.0)        # one half-life later
check("EMA: one half-life later it is half way (0.02 -> 0.04)", abs(e_h.s - (s0 + 0.5 * (0.06 - s0))) < 1e-6, (s0, e_h.s))
s1 = e_h.s
e_h.update(synth(100, 0.06, noise=0.0), 30 * 60.0)        # same time: dt 0 -> no move
check("EMA: dt 0 -> no change", abs(e_h.s - s1) < 1e-12)
e_h.update(synth(100, 0.06, noise=0.0), 90 * 60.0)        # two more half-lives: 3/4 of the remaining gap
check("EMA: two half-lives close 3/4 of the gap", abs(e_h.s - (s1 + 0.75 * (0.06 - s1))) < 1e-6, e_h.s)

print("--- ref_tilt_estimator (slope / median / wls)")
c = M.Config()
check("default 'slope', live-overridable right after ref_tilt_winsor",
      c.ref_tilt_estimator == "slope" and _ov[_ov.index("ref_tilt_winsor") + 1] == "ref_tilt_estimator")
for v in ("slope", "median", "wls"):
    good, bad = M.validate_overrides({"ref_tilt_estimator": v}, c)
    check(f"override '{v}' accepted", good == {"ref_tilt_estimator": v} and not bad, (good, bad))
for v in ("slope,median", "mean", "", 3, None, ["median"]):
    good, bad = M.validate_overrides({"ref_tilt_estimator": v}, c)
    check(f"override {v!r} refused (exactly one of slope, median, wls)", not good and len(bad) == 1, (good, bad))


def est_with(kind, samples, **kw):
    cf = M.Config()
    cf.ref_tilt_estimator = kind
    for k, v in kw.items():
        setattr(cf, k, v)
    e = M.TiltEstimator(cf)
    e.update(samples, 0.0)
    return e


lin = synth(200, 0.05)
for kind in ("median", "wls"):
    check(f"{kind}: recovers s = 0.05 on a linear cross-section", abs(est_with(kind, lin).s - 0.05) < 0.004,
          est_with(kind, lin).s)
# 3 Oct shape: the tournament tilt is 0.09, but a third of the far tails (|x| > 0.45) read a gap pinned at the winsor
rng2 = random.Random(11)
pin = []
for i in range(225):
    r = rng2.choice([rng2.uniform(0.005, 0.05), rng2.uniform(0.95, 0.995), rng2.uniform(0.05, 0.95)])
    x = r - 0.5
    gap = 0.08 * (1 if x > 0 else -1) if abs(x) > 0.45 and i % 3 == 0 else 0.09 * x + rng2.gauss(0, 0.003)
    pin.append((r, r - gap, 2))
s_sl, s_md, s_wl = (est_with(k, pin).s for k in ("slope", "median", "wls"))
check("pinned tails: the slope over-reads 0.09 by > 1 point", s_sl > 0.10, s_sl)
check("pinned tails: the median stays within 0.5 point of 0.09", abs(s_md - 0.09) < 0.005, s_md)
check("pinned tails: wls in between (closer than the slope)", abs(s_wl - 0.09) < abs(s_sl - 0.09), (s_wl, s_sl))
mid_only = [(r, r - 0.05 * (r - 0.5), 2) for r in [0.42 + 0.0025 * i for i in range(60)]]   # all |x| < 0.1
e_sl, e_md = est_with("slope", mid_only), est_with("median", mid_only)
check("median: markets with |x| <= 0.1 give no ratio -> fewer than min_markets -> holds (the slope estimates)",
      not e_md.ready and e_md.s == 0.0 and e_sl.ready and e_md.diag["n_ratio"] == 0, (e_md.s, e_md.diag))
check("odd/even median: middle value, mean of the two middle values",
      close(est_with("median", [(0.9, 0.9 - 0.4 * k, 2) for k in (0.01, 0.02, 0.06)], ref_tilt_min_markets=1,
                     ref_tilt_winsor=1.0).s, 0.02)
      and close(est_with("median", [(0.9, 0.9 - 0.4 * k, 2) for k in (0.01, 0.02, 0.04, 0.06)],
                         ref_tilt_min_markets=1, ref_tilt_winsor=1.0).s, 0.03))
dg = est_with("slope", pin).diag
check("diag: every estimator's raw reading, n, the pinned share of the slope's weight",
      dg["n"] == 225 and close(dg["slope"], s_sl, 1e-4) and close(dg["median"], s_md, 1e-4)
      and close(dg["wls"], s_wl, 1e-4) and 0.1 < dg["pinned_weight"] < 0.5, dg)
check("an unknown value (set in code) falls back to the slope", close(est_with("x", pin).s, s_sl))

print("--- persistence")
d = est.to_dict()
r1 = M.TiltEstimator(M.Config()).from_dict(json.loads(json.dumps(d)))
check("to_dict / from_dict round trip through JSON", r1.s == est.s and r1.ready and r1.t is None, (d, r1.s))
check("missing state = 0, not ready", M.TiltEstimator(M.Config()).from_dict({}).s == 0.0
      and not M.TiltEstimator(M.Config()).from_dict(None).ready)
check("bad state = 0", M.TiltEstimator(M.Config()).from_dict({"s": "x"}).s == 0.0)
r1.update(synth(100, 0.09, noise=0.0), 12345.0)
check("after a restore the first update only sets the clock (no jump to the new raw value)", r1.s == est.s, r1.s)

print("--- which markets feed the estimator; tilt_exposure; status.json and restore")
a, b = make_bot()
b.cfg.ref_tilt_min_markets = 1
seen = []
b.tilt.update = lambda samples, now: (seen.append(list(samples)), b.tilt.s)[1]
refs = {"11": 0.20, "12": 0.80, "21": 0.55, "22": 0.45}
bfv = {"11": 0.18, "12": 0.82, "21": 0.52, "22": None}
b.ref_only = {"12"}
b.ex["21"].cooldown_until = 1e12                           # reference jump guard active
b.update_tilt(bfv, refs, {"11", "12", "21", "22"}, {}, 1000.0)
check("excluded: ref_only, jump guard, no book price (only Rep Ohio feeds it)",
      seen[-1] == [(0.20, 0.18, 2)], seen[-1])
b.ex["21"].cooldown_until = 0.0
b.update_tilt(bfv, refs, {"11", "21"}, {}, 1000.0)
check("excluded: not liquid; the guard expired -> included", [x[0] for x in seen[-1]] == [0.20, 0.55], seen[-1])
b.cfg.headline_races = ("Utah Senate",)
b.update_tilt(bfv, refs, {"11", "21"}, {}, 1000.0)
check("excluded: headline races", [x[0] for x in seen[-1]] == [0.20], seen[-1])
b.update_tilt(bfv, refs, {"11"}, {"11": 100.0, "21": -50.0, "22": 30.0, "99": 5.0}, 1000.0)
check("tilt_exposure = sum pos x (raw r - c): 100 x -0.30 - 50 x 0.05 + 30 x -0.05 = -34",
      close(b.tilt_exposure, -34.0), b.tilt_exposure)

a, b = make_bot()
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30})
b.tilt.s, b.tilt.ready = 0.0613, True
a.inv["11"] = 200
b.cycle()
b.write_status(True)                                      # the run loop writes it after every cycle
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("status.json: tilt_s (4 places) and tilt_exposure (whole shares)",
      st.get("tilt_s") == 0.0613 and st.get("tilt_exposure") == -40, {k: st.get(k) for k in ("tilt_s", "tilt_exposure")})
check("status.json: tilt_diag with the estimator in use and every estimator's raw reading",
      isinstance(st.get("tilt_diag"), dict) and st["tilt_diag"].get("estimator") == "slope"
      and {"n", "n_ratio", "pinned_weight", "slope", "median", "wls"} <= set(st["tilt_diag"]), st.get("tilt_diag"))
b2 = M.Bot(a, b.cfg)
check("restart restores s from status.json", b2.tilt.s == 0.0613 and b2.tilt.ready and b2.tilt_s == 0.0613, b2.tilt.s)
with open(b.cfg.status_file, "w") as f:
    json.dump({"updated": "x"}, f)
check("status.json without the key: s = 0", M.Bot(a, b.cfg).tilt.s == 0.0)
os.remove(b.cfg.status_file)
check("no status.json: s = 0", M.Bot(a, b.cfg).tilt.s == 0.0)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
