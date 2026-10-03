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
check("default 20 minutes (SIM_NOTES Round 5: same P&L as the step-on, smoother switch-on)", getattr(c, KEY, None) == 20.0)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("its own block in Config and in OVERRIDABLE (after the X12 take_tilted_ref one)",
      KEY in _f and KEY in _ov and _f[_f.index(KEY) - 1] == "take_tilted_ref" and _ov[_ov.index(KEY) - 1] == "take_tilted_ref",
      (_f[-3:], _ov[-3:]))
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
b.cfg.ref_tilt_rampin_min = 120.0                      # (the default is 0: this block tests the ramp itself)
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

print("--- headline gate: its own ramp from ref_tilt_headline's switch-on")
b.cfg.ref_tilt_rampin_min = 10.0
hl = next(e for e in b.ex.values() if e.group in b.cfg.headline_races) if any(
    e.group in b.cfg.headline_races for e in b.ex.values()) else None
if hl is None:                                   # make Ohio Senate a headline race for this block
    b.cfg.headline_races = ("Ohio Senate",)
    hl = b.ex["11"]
b.tilt_on_at, b.tilt_headline_on_at = now, None
check("headline, gate off: raw r", b.tilted_ref_for(hl, 0.05, now + 6000) == 0.05)
b.cfg.ref_tilt_headline = True
b.tilt_headline_on_at = now + 6000               # the gate opened long after the flag
check("headline, gate just opened: raw r (its own ramp starts)", b.tilted_ref_for(hl, 0.05, now + 6000) == 0.05)
check("...halfway through its ramp: s/2", close(b.tilted_ref_for(hl, 0.05, now + 6300), M.tilted_ref(0.05, S / 2, 2)))
check("...after it: full s", close(b.tilted_ref_for(hl, 0.05, now + 6600), full))
check("a non-headline market is unaffected by the headline clock", close(b.tilted_ref_for(b.ex["21"], 0.50, now + 6000), M.tilted_ref(0.50, S, 2)))
b.cfg.ref_tilt_enabled = True
b.cycle()
check("cycle sets tilt_headline_on_at with both flags on", b.tilt_headline_on_at is not None)
b.cfg.ref_tilt_headline = False
b.cycle()
check("...and clears it when the gate closes", b.tilt_headline_on_at is None)
b.cfg.headline_races = M.Config().headline_races

print("--- status.json")
b.tilt_s_applied = 0.01234567
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("tilt_s_applied next to tilt_s (4 places)", st.get("tilt_s_applied") == 0.0123 and "tilt_s" in st,
      st.get("tilt_s_applied"))

print("--- red team: a quick restart continues the ramp (wall-clock switch-on times in tilt_state)")
b.cfg.ref_tilt_rampin_min = 120.0
b.cfg.ref_tilt_enabled, b.cfg.ref_tilt_headline = True, False
mono = M.time.monotonic()
b.tilt_on_at, b.tilt_headline_on_at = mono - 1800.0, None    # 30 min into a 120-min ramp
b.write_status(True)
with open(b.cfg.status_file) as f:
    ts_ = json.load(f).get("tilt_state", {})
check("tilt_state carries on_wall = wall time at switch-on, headline_on_wall null",
      close(ts_.get("on_wall"), M.time.time() - 1800.0, 5.0) and ts_.get("headline_on_wall", "x") is None, ts_)
b2 = M.Bot(a, b.cfg)
check("restart < 10 min later: tilt_on_at restored on the new monotonic clock",
      b2.tilt_on_at is not None and close(M.time.monotonic() - b2.tilt_on_at, 1800.0, 5.0), b2.tilt_on_at)
check("...headline clock stays None", b2.tilt_headline_on_at is None)
b2.refs = FakeRefs(dict(REFS))
b2.tilt.update = lambda samples, now_m: S
b2.cycle()
check("...first cycle: the ramp continues (applied s ~ 0.25 x S, not 0)",
      close(b2.tilt_s_applied, 0.25 * S, 0.01 * S), b2.tilt_s_applied)
b.tilt_headline_on_at = mono - 600.0
b.cfg.ref_tilt_headline = True
b.write_status(True)
b3 = M.Bot(a, b.cfg)
check("headline clock restored too", b3.tilt_headline_on_at is not None
      and close(M.time.monotonic() - b3.tilt_headline_on_at, 600.0, 5.0), b3.tilt_headline_on_at)
with open(b.cfg.status_file) as f:
    st = json.load(f)
st["tilt_state"]["saved_wall"] = M.time.time() - 700.0
with open(b.cfg.status_file, "w") as f:
    json.dump(st, f)
b4 = M.Bot(a, b.cfg)
check("status.json older than 10 min: both clocks None (the ramp restarts)",
      b4.tilt_on_at is None and b4.tilt_headline_on_at is None, (b4.tilt_on_at, b4.tilt_headline_on_at))
del st["tilt_state"]["saved_wall"]
with open(b.cfg.status_file, "w") as f:
    json.dump(st, f)
b5 = M.Bot(a, b.cfg)
check("no saved_wall: the file mtime decides (just written: resumed)", b5.tilt_on_at is not None)
old = M.time.time() - 3600
os.utime(b.cfg.status_file, (old, old))
b6 = M.Bot(a, b.cfg)
check("...an hour-old file: None", b6.tilt_on_at is None)
b.tilt_on_at = b.tilt_headline_on_at = None
b.write_status(True)
with open(b.cfg.status_file) as f:
    ts_ = json.load(f).get("tilt_state", {})
check("flag off clocks: on_wall / headline_on_wall null", ts_.get("on_wall", "x") is None
      and ts_.get("headline_on_wall", "x") is None, ts_)
check("...and the restart starts with None", M.Bot(a, b.cfg).tilt_on_at is None)
b.cfg.ref_tilt_headline = False

print("--- red team: status.json tilt_s_applied_headline")
b.tilt.update = lambda samples, now_m: S
b.cfg.ref_tilt_enabled, b.cfg.ref_tilt_headline = True, False
b.cycle()
check("gate off: tilt_s_applied_headline 0", getattr(b, "tilt_s_applied_headline", None) == 0.0)
b.cfg.ref_tilt_headline = True
b.cycle()
b.tilt_on_at = M.time.monotonic() - 7200.0                # the flag long on, the headline gate just opened
b.cycle()
check("gate just opened: ramped from the later (headline) clock, ~0",
      0.0 <= b.tilt_s_applied_headline < 0.01 * S and close(b.tilt_s_applied, S), (b.tilt_s_applied_headline,
                                                                                   b.tilt_s_applied))
b.tilt_headline_on_at = M.time.monotonic() - 3600.0
b.cycle()
check("...an hour into its 120-min ramp: S/2", close(b.tilt_s_applied_headline, S / 2, 0.01 * S),
      b.tilt_s_applied_headline)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("status.json carries tilt_s_applied_headline (4 places)",
      st.get("tilt_s_applied_headline") == round(b.tilt_s_applied_headline, 4), st.get("tilt_s_applied_headline"))
b.cfg.ref_tilt_enabled, b.cfg.ref_tilt_headline = False, False
b.cycle()
check("flag off: both applied 0", b.tilt_s_applied == 0.0 and b.tilt_s_applied_headline == 0.0)

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
