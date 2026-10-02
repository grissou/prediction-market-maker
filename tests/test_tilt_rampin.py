"""
Offline tests for Package 5 T2.1 ramp-in (ref_tilt_rampin_min): with ref_tilt_enabled the tilt s APPLIED rises
linearly from 0 to the estimate over ref_tilt_rampin_min minutes after the flag is switched on (rampin_factor,
Bot.tilt_on_at), in the blend and the takes alike; status.json carries tilt_s_applied; the strategy_sim mirrors it.
No network.

Run:  python tests/test_tilt_rampin.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeRefs, lvl, make_bot                 # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEY = "ref_tilt_rampin_min"
S = 0.06


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


print("--- settings")
c = M.Config()
check("default 120 minutes", getattr(c, KEY, None) == 120.0)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("last in Config and in OVERRIDABLE", _f and _f[-1] == KEY and _ov and _ov[-1] == KEY, (_f[-1:], _ov[-1:]))
check("range (0, 1440)", M.OVERRIDABLE.get(KEY) == (0.0, 1440.0))
good, bad = M.validate_overrides({KEY: 30.0}, c)
check("live-overridable", good == {KEY: 30.0} and not bad, (good, bad))

print("--- rampin_factor")
rf = getattr(M, "rampin_factor", None)
check("exists", callable(rf))
if callable(rf):
    check("0 at the switch-on", rf(1000.0, 1000.0, 10) == 0.0)
    check("0.5 halfway", close(rf(1300.0, 1000.0, 10), 0.5))
    check("1 at the end and after", rf(1600.0, 1000.0, 10) == 1.0 and rf(99999.0, 1000.0, 10) == 1.0)
    check("1 when minutes 0 (no ramp)", rf(1000.0, 1000.0, 0) == 1.0 and rf(1000.0, 1000.0, 0.0) == 1.0)
    check("1 when on_at None", rf(1000.0, None, 10) == 1.0)
    check("never negative (clock before on_at)", rf(900.0, 1000.0, 10) == 0.0)

BOOKS = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.14, 1000)]},
         "12": {"bids": [lvl(0.86, 1000)], "asks": [lvl(0.90, 1000)]},
         "21": {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.45, 1000)]},
         "22": {"bids": [lvl(0.55, 1000)], "asks": [lvl(0.60, 1000)]}}
REFS = {"Ohio Senate|Republican": 0.05, "Ohio Senate|Democratic": 0.95,
        "Utah Senate|Republican": 0.50, "Utah Senate|Democratic": 0.50}

print("--- tilt_on_at: set on enable, cleared on disable, re-enable restarts")
a, b = make_bot(books=json.loads(json.dumps(BOOKS)))
b.refs = FakeRefs(dict(REFS))
b.tilt.update = lambda samples, now_m: S
b.tilt_s = S
check("starts None", getattr(b, "tilt_on_at", "missing") is None)
b.cycle()
check("flag off: stays None, applied s 0", b.tilt_on_at is None and getattr(b, "tilt_s_applied", None) == 0.0)
b.cfg.ref_tilt_enabled = True
b.cycle()
t1 = b.tilt_on_at
check("enabled: set to the cycle's monotonic time", t1 is not None and 0 <= M.time.monotonic() - t1 < 60, t1)
check("...and applied s ~0 at the start of a 120-min ramp", 0.0 <= b.tilt_s_applied < 0.01 * S, b.tilt_s_applied)
b.cycle()
check("kept while on", b.tilt_on_at == t1)
b.cfg.ref_tilt_enabled = False
b.cycle()
check("disabled: cleared", b.tilt_on_at is None)
b.cfg.ref_tilt_enabled = True
b.cycle()
check("re-enabled: the ramp restarts (a later on_at)", b.tilt_on_at is not None and b.tilt_on_at >= t1)

print("--- tilted_ref_for / take_ref ramp")
ex11 = b.ex["11"]
b.tilt_s = S
b.cfg.ref_tilt_rampin_min = 10.0
now = 5000.0
b.tilt_on_at = now
full = M.tilted_ref(0.05, S, 2)
check("0 into the ramp: raw r", b.tilted_ref_for(ex11, 0.05, now) == 0.05)
check("halfway: s/2", close(b.tilted_ref_for(ex11, 0.05, now + 300), M.tilted_ref(0.05, S / 2, 2)))
check("full ramp: s", close(b.tilted_ref_for(ex11, 0.05, now + 600), full)
      and close(b.tilted_ref_for(ex11, 0.05, now + 6000), full))
b.cfg.take_tilted_ref = True
check("take_ref: raw at 0", b.take_ref(ex11, 0.05, now) == 0.05)
check("take_ref: s/2 halfway", close(b.take_ref(ex11, 0.05, now + 300), M.tilted_ref(0.05, S / 2, 2)))
check("take_ref: s at full", close(b.take_ref(ex11, 0.05, now + 600), full))
b.cfg.ref_tilt_rampin_min = 0.0
check("rampin 0: the full s immediately", close(b.tilted_ref_for(ex11, 0.05, now), full))
b.cfg.ref_tilt_rampin_min = 10.0
b.tilt_on_at = M.time.monotonic()
check("now_m omitted: the monotonic clock (just switched on: ~raw r)", abs(b.tilted_ref_for(ex11, 0.05) - 0.05) < 1e-3)

print("--- status.json")
b.tilt_s_applied = 0.01234567
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("tilt_s_applied next to tilt_s (4 places)", st.get("tilt_s_applied") == 0.0123 and "tilt_s" in st,
      st.get("tilt_s_applied"))

print("--- strategy_sim mirror")
import strategy_sim as SS                                 # noqa: E402


class Rec(SS.Sim):
    def our_cycle(self, t, paths):
        super().our_cycle(t, paths)
        self.rec = getattr(self, "rec", [])
        self.rec.append((t, getattr(self, "tilt_s_applied", None), self.tilt.s))


ON = {"ref_tilt_enabled": True, "ref_tilt_min_markets": 5}
sim = Rec(1, 0.02, "quiet", SS.make_cfg(dict(ON, ref_tilt_rampin_min=1)))
sim.run()
early = [(t, a_, s_) for t, a_, s_ in sim.rec if t < 60 + sim.tilt_on_at and s_ > 0]
late = [(t, a_, s_) for t, a_, s_ in sim.rec if t >= 60 + sim.tilt_on_at and s_ > 0]
check("tilt_on_at = the first cycle with the flag on", sim.tilt_on_at == sim.rec[0][0], sim.tilt_on_at)
check("t < 60 s: applied s below the estimate", early and all(a_ is not None and a_ < s_ for _, a_, s_ in early),
      early[:3])
check("after the ramp: applied s = the estimate", late and all(close(a_, s_) for _, a_, s_ in late), late[:3])

m0 = SS.Sim(1, 0.02, "quiet", SS.make_cfg(dict(ON, ref_tilt_rampin_min=0))).run()
saved = M.rampin_factor
M.rampin_factor = lambda *a: 1.0                          # the old formula: tilted_ref(ref, tilt.s, legs)
try:
    m_old = SS.Sim(1, 0.02, "quiet", SS.make_cfg(dict(ON, ref_tilt_rampin_min=0))).run()
finally:
    M.rampin_factor = saved
check("rampin 0: metrics identical to the pre-ramp formula", m0 == m_old)
m_off = SS.Sim(1, 0.02, "quiet", SS.make_cfg({"ref_tilt_enabled": False})).run()
m_abs = SS.Sim(1, 0.02, "quiet", SS.make_cfg({})).run()
check("flag off: identical metrics", m_off == m_abs)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
