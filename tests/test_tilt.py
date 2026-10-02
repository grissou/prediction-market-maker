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
import strategy_sim as S                                  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


print("--- settings")
c = M.Config()
check("defaults: off, headline gate off, 50 markets, 30 min half-life, max 12%, winsor 8c",
      (c.ref_tilt_enabled, c.ref_tilt_headline, c.ref_tilt_min_markets, c.ref_tilt_halflife_min, c.ref_tilt_max,
       c.ref_tilt_winsor) == (False, False, 50, 30.0, 0.12, 0.08))
names = ["ref_tilt_enabled", "ref_tilt_headline", "ref_tilt_min_markets", "ref_tilt_halflife_min", "ref_tilt_max",
         "ref_tilt_winsor"]
_ov = list(M.OVERRIDABLE)
_i = _ov.index(names[0])
check("all six live-overridable, one block after the savers", _ov[_i:_i + 6] == names and _i > _ov.index("ttl_expire_grace_seconds"),
      _ov[_i:_i + 6])
good, bad = M.validate_overrides({"ref_tilt_enabled": True, "ref_tilt_max": 0.5}, c)
check("override ranges: flag accepted, ref_tilt_max 0.5 refused", good == {"ref_tilt_enabled": True} and len(bad) == 1,
      (good, bad))

print("--- tilted_ref and blend_fv")
check("two legs: 0.80 with s 5% -> 0.5 + 0.95 x 0.30 = 0.785", close(M.tilted_ref(0.80, 0.05, 2), 0.785))
check("two legs: 0.10 with s 5% -> 0.12 (longshots pulled up)", close(M.tilted_ref(0.10, 0.05, 2), 0.12))
check("three legs: c = 1/3", close(M.tilted_ref(0.6, 0.1, 3), 1 / 3 + 0.9 * (0.6 - 1 / 3)))
check("one leg counts as c = 0.5", close(M.tilted_ref(0.3, 0.1, 1), 0.32) and close(M.tilted_ref(0.3, 0.1, 0), 0.32))
check("s = 0: unchanged", close(M.tilted_ref(0.37, 0.0, 4), 0.37))
off, on = M.Config(), M.Config()
on.ref_tilt_enabled = True
for bfv, r in ((0.14, 0.30), (0.5123, 0.6789), (0.91, 0.88)):
    check(f"flag off: blend_fv == the old expression bit for bit (book {bfv}, r {r})",
          M.blend_fv(bfv, r, off, 0.07, 2) == (1 - off.ref_weight) * bfv + off.ref_weight * r)
check("flag on: blend uses r' (0.3 * 0.14 + 0.7 * 0.785)", close(M.blend_fv(0.14, 0.80, on, 0.05, 2), 0.3 * 0.14 + 0.7 * 0.785))
check("flag on, headline market: raw r (staging gate)",
      M.blend_fv(0.14, 0.80, on, 0.05, 2, headline=True) == M.blend_fv(0.14, 0.80, off, 0.05, 2))
on_h = M.Config(); on_h.ref_tilt_enabled = on_h.ref_tilt_headline = True
check("...unless ref_tilt_headline", close(M.blend_fv(0.14, 0.80, on_h, 0.05, 2, headline=True), 0.3 * 0.14 + 0.7 * 0.785))

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
e_m = M.TiltEstimator(M.Config())
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

print("--- persistence")
d = est.to_dict()
r1 = M.TiltEstimator(M.Config()).from_dict(json.loads(json.dumps(d)))
check("to_dict / from_dict round trip through JSON", r1.s == est.s and r1.ready and r1.t is None, (d, r1.s))
check("missing state = 0, not ready", M.TiltEstimator(M.Config()).from_dict({}).s == 0.0
      and not M.TiltEstimator(M.Config()).from_dict(None).ready)
check("bad state = 0", M.TiltEstimator(M.Config()).from_dict({"s": "x"}).s == 0.0)
r1.update(synth(100, 0.09, noise=0.0), 12345.0)
check("after a restore the first update only sets the clock (no jump to the new raw value)", r1.s == est.s, r1.s)

print("--- main loop: flag off identical, flag on tilted, headline gate")


def run_bot(enabled, s, headline_races=None, tilt_headline=False):
    a, b = make_bot()
    b.refs, b.cfg.ref_weight = FakeRefs({"Ohio Senate|Republican": 0.30}), 0.5
    b.cfg.ref_tilt_enabled, b.cfg.ref_tilt_headline = enabled, tilt_headline
    if headline_races is not None:
        b.cfg.headline_races = headline_races
    b.tilt.s = s                                          # 1 market < 50: the estimator holds it
    b.cycle()
    return a, b


_, b_off = run_bot(False, 0.0)
_, b_off_s = run_bot(False, 0.05)
check("flag off: the estimate does not change fair value", b_off.ex["11"].last_fv == b_off_s.ex["11"].last_fv,
      (b_off.ex["11"].last_fv, b_off_s.ex["11"].last_fv))
exp_off = M.normalise({"11": 0.5 * 0.14 + 0.5 * 0.30, "12": 0.86})["11"]
check("flag off: fair value = the old blend (0.14 / 0.30, normalised)", close(b_off.ex["11"].last_fv, exp_off),
      (b_off.ex["11"].last_fv, exp_off))
_, b_on = run_bot(True, 0.05)
exp_on = M.normalise({"11": 0.5 * 0.14 + 0.5 * M.tilted_ref(0.30, 0.05, 2), "12": 0.86})["11"]
check("flag on: fair value blends toward r' = 0.31", close(b_on.ex["11"].last_fv, exp_on), (b_on.ex["11"].last_fv, exp_on))
check("flag on: the race's legs set c (Ohio: 2 markets)", b_on.legs(b_on.ex["11"]) == 2)
_, b_hd = run_bot(True, 0.05, headline_races=("Ohio Senate",))
check("flag on, headline race: raw r", close(b_hd.ex["11"].last_fv, exp_off), b_hd.ex["11"].last_fv)
_, b_hd2 = run_bot(True, 0.05, headline_races=("Ohio Senate",), tilt_headline=True)
check("...tilted with ref_tilt_headline", close(b_hd2.ex["11"].last_fv, exp_on), b_hd2.ex["11"].last_fv)

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
b2 = M.Bot(a, b.cfg)
check("restart restores s from status.json", b2.tilt.s == 0.0613 and b2.tilt.ready and b2.tilt_s == 0.0613, b2.tilt.s)
with open(b.cfg.status_file, "w") as f:
    json.dump({"updated": "x"}, f)
check("status.json without the key: s = 0", M.Bot(a, b.cfg).tilt.s == 0.0)
os.remove(b.cfg.status_file)
check("no status.json: s = 0", M.Bot(a, b.cfg).tilt.s == 0.0)

print("--- strategy_sim mirror")


def sim_metrics(over, patch=None):
    saved = (M.tilted_ref, M.TiltEstimator)
    if patch:
        M.tilted_ref, M.TiltEstimator = patch
    try:
        return S.Sim(1, 0.05, "quiet", S.make_cfg(over)).run()
    finally:
        M.tilted_ref, M.TiltEstimator = saved


def boom(*a, **k):
    raise AssertionError("tilt code called with the flag off")


m_absent = sim_metrics({})
m_off = sim_metrics({"ref_tilt_enabled": False}, patch=(boom, boom))
check("flag off: identical metrics, and no tilt code runs", m_off == m_absent)
calls = {"ref": 0, "upd": 0}
real_ref, real_est = M.tilted_ref, M.TiltEstimator


def counting_ref(*a):
    calls["ref"] += 1
    return real_ref(*a)


class CountingEst(real_est):
    def update(self, samples, now):
        calls["upd"] += 1
        return super().update(samples, now)


m_on = sim_metrics({"ref_tilt_enabled": True, "ref_tilt_min_markets": 5}, patch=(counting_ref, CountingEst))
check("flag on: the sim calls mm_bot's own tilted_ref and TiltEstimator (one update per cycle)",
      calls["ref"] > 0 and 0 < calls["upd"] <= int(0.05 * 3600 / 2) + 1, calls)
sim = S.Sim(1, 0.05, "quiet", S.make_cfg({"ref_tilt_enabled": True, "ref_tilt_min_markets": 5}))
sim.run()
check("flag on: the sim's estimator ran on its markets", sim.tilt.n >= 5 and 0.0 <= sim.tilt.s <= 0.12, (sim.tilt.n, sim.tilt.s))

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
