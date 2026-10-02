"""
Offline tests for Package 5 X12: take_tilted_ref (take_stale_quotes measures the gap from, confirms, re-checks and
sizes from the tilt-corrected Polymarket price r' = c + (1 - s)(r - c) instead of the raw r). Flag off, or
ref_tilt_enabled off = unchanged. No network.

Run:  python tests/test_take_tilted.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeRefs, lvl, make_bot                 # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEY = "take_tilted_ref"
S = 0.06


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


print("--- settings")
c = M.Config()
check("default off", getattr(c, KEY, None) is False)
good, bad = M.validate_overrides({KEY: True}, c)
check("live-overridable", good == {KEY: True} and not bad, (good, bad))
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("after the X11 block in Config and in OVERRIDABLE (contiguous with it)",
      KEY in _f and _f.index(KEY) == _f.index("reduce_from_book_max_turnover") + 1
      and KEY in _ov and _ov.index(KEY) == _ov.index("reduce_from_book_max_turnover") + 1)

# Ohio: longshot Rep 11 (r 0.05, bid 0.10: raw 5c past the bid), favourite Dem 12 (r 0.95, ask 0.90: raw 5c past
# the ask). Utah: mid-price, Rep 21 r 0.50 vs ask 0.45 (buy), Dem 22 r 0.50 vs bid 0.55 (sell). Two legs: c = 0.5.
BOOKS = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.14, 1000)]},
         "12": {"bids": [lvl(0.86, 1000)], "asks": [lvl(0.90, 1000)]},
         "21": {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.45, 1000)]},
         "22": {"bids": [lvl(0.55, 1000)], "asks": [lvl(0.60, 1000)]}}
REFS = {"Ohio Senate|Republican": 0.05, "Ohio Senate|Democratic": 0.95,
        "Utah Senate|Republican": 0.50, "Utah Senate|Democratic": 0.50}


def later(b, seconds=31):
    for e in b.ex.values():
        e.take_since -= seconds


def run(books=BOOKS, refs=REFS, s=S, close_h=None, kelly_log=None, **cfg_kw):
    """Two cycles (gap seen, then confirmed 31 s later): the inventory the takes left, and the bot."""
    a, b = make_bot(books=json.loads(json.dumps(books)))
    b.cfg.ref_tilt_rampin_min = 0.0                       # the full tilt at once (ramp-in: test_tilt_rampin.py)
    for k, v in cfg_kw.items():
        setattr(b.cfg, k, v)
    b.tilt.update = lambda samples, now_m: s              # pin the tilt estimate
    b.tilt_s = s
    if close_h is not None:
        for e in b.ex.values():
            e.close = M.utcnow() + timedelta(hours=close_h)
    b.refs = FakeRefs(dict(refs))
    real_kelly = M.kelly_position
    if kelly_log is not None:
        def spy(p, price, bank, cfg, yes=True):
            kelly_log.append((p, price, yes))
            return real_kelly(p, price, bank, cfg, yes=yes)
        M.kelly_position = spy
    try:
        b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
    finally:
        M.kelly_position = real_kelly
    return {k: v for k, v in a.inv.items() if v}, b


print("--- tilted_ref_for")
_, b = run()
ex11, ex21 = b.ex["11"], b.ex["21"]
check("ref_tilt off: raw r", b.tilted_ref_for(ex11, 0.05) == 0.05)
b.cfg.ref_tilt_enabled = True
check("ref_tilt on, s 0.06: 0.5 + 0.94 x (0.05 - 0.5) = 0.077", close(b.tilted_ref_for(ex11, 0.05), 0.077))
check("...a mid-price market (r 0.50) is unchanged", close(b.tilted_ref_for(ex21, 0.50), 0.50))
check("take_ref with take_tilted_ref off: the raw r", b.take_ref(ex11, 0.05) == 0.05)
b.cfg.take_tilted_ref = True
check("take_ref with take_tilted_ref on: tilted_ref_for", b.take_ref(ex11, 0.05) == b.tilted_ref_for(ex11, 0.05))
b.cfg.headline_races = ("Ohio Senate",)
check("headline market without ref_tilt_headline: raw r", b.tilted_ref_for(ex11, 0.05) == 0.05)
b.cfg.ref_tilt_headline = True
check("...with ref_tilt_headline: tilted", close(b.tilted_ref_for(ex11, 0.05), 0.077))
b.cfg.headline_races, b.cfg.ref_tilt_carry_days = (), 1.0
ex11.close = M.utcnow() + timedelta(hours=12)
check("carry ramp (1 day, 12 h left): s/2 applied", close(b.tilted_ref_for(ex11, 0.05), M.tilted_ref(0.05, S / 2, 2), 1e-4))
ex11.close = M.utcnow() - timedelta(hours=1)
check("carry ramp at / past the close: the raw r exactly", b.tilted_ref_for(ex11, 0.05) == 0.05)
b.cfg.ref_tilt_carry_days = 0.0
check("tilted_ref_for == the r blend_fv uses (s, legs, headline, carry identical)",
      all(close(M.blend_fv(0.3, r, b.cfg, S, 2, False), M.blend_fv(0.3, b.tilted_ref_for(ex21, r), b.cfg))
          for r in (0.02, 0.2, 0.5, 0.8, 0.97)))

print("--- flag off / ref_tilt off: identical take decisions")
GRID = [0.03, 0.05, 0.08, 0.20, 0.50]
same = True
for r11 in GRID:
    refs = dict(REFS, **{"Ohio Senate|Republican": r11, "Ohio Senate|Democratic": 1 - r11})
    base, _ = run(refs=refs)
    for kw in ({"ref_tilt_enabled": True}, {KEY: True}, {"ref_tilt_enabled": False, KEY: False}):
        got, _ = run(refs=refs, **kw)
        if got != base:
            same = False
            print("   differs", r11, kw, got, base)
check("grid of Polymarket prices: tilt-only / take_tilted_ref-only / both off take exactly the same", same)
base, _ = run()
check("(control, flag off: the 5c longshot and favourite gaps and both Utah gaps are taken)",
      base.get("11", 0) < 0 and base.get("12", 0) > 0 and base.get("21", 0) > 0 and base.get("22", 0) < 0, base)

print("--- flag on, s = 0.06")
on, _ = run(ref_tilt_enabled=True, take_tilted_ref=True)
check("longshot r 0.05 vs bid 0.10: r' 0.077 is only 2.3c past it -> NOT taken", not on.get("11"), on)
check("favourite r 0.95 vs ask 0.90: r' 0.923 -> NOT taken", not on.get("12"), on)
check("mid-price Utah (r 0.50): unaffected, both taken as with the flag off",
      on.get("21") == base.get("21") and on.get("22") == base.get("22"), (on, base))
far = dict(BOOKS, **{"11": {"bids": [lvl(0.13, 1000)], "asks": [lvl(0.17, 1000)]}})
got, _ = run(books=far, ref_tilt_enabled=True, take_tilted_ref=True)
check("a stale bid 0.13 (5.3c past r' 0.077) is still taken", got.get("11", 0) < 0, got)

print("--- headline gate")
got, _ = run(ref_tilt_enabled=True, take_tilted_ref=True, headline_races=("Ohio Senate",))
check("headline Ohio without ref_tilt_headline: the raw r -> taken as with the flag off",
      got.get("11", 0) < 0 and got.get("12", 0) > 0, got)
got, _ = run(ref_tilt_enabled=True, take_tilted_ref=True, headline_races=("Ohio Senate",), ref_tilt_headline=True)
check("...with ref_tilt_headline: not taken", not got.get("11") and not got.get("12"), got)

print("--- carry ramp")
b11 = dict(BOOKS, **{"11": {"bids": [lvl(0.11, 1000)], "asks": [lvl(0.15, 1000)]}})
got, _ = run(books=b11, ref_tilt_enabled=True, take_tilted_ref=True, close_h=13.0)
check("(full tilt: the bid 0.11 is 3.3c past r' -> not taken)", not got.get("11"), got)
got, _ = run(books=b11, ref_tilt_enabled=True, take_tilted_ref=True, close_h=13.0, ref_tilt_carry_days=10.0)
check("carry ramp, 13 h of 10 days left (s ~0.003): r' ~ raw r -> taken", got.get("11", 0) < 0, got)

print("--- Kelly size uses the tilted p")
log_off, log_on = [], []
run(books=far, kelly_log=log_off)
run(books=far, kelly_log=log_on, ref_tilt_enabled=True, take_tilted_ref=True)
p_off = [p for p, price, yes in log_off if close(price, 0.13) and not yes]
p_on = [p for p, price, yes in log_on if close(price, 0.13) and not yes]
check("flag off: Kelly p = the raw 0.05", p_off and all(close(p, 0.05) for p in p_off), log_off)
check("flag on: Kelly p = r' 0.077", p_on and all(close(p, 0.077) for p in p_on), log_on)
q_off, _ = run(books=far)
q_on, _ = run(books=far, ref_tilt_enabled=True, take_tilted_ref=True)
check("...so the take is smaller (or equal at a cap)", abs(q_on.get("11", 0)) <= abs(q_off.get("11", 0)), (q_on, q_off))

print("--- red team: toggling take_tilted_ref restarts every confirmation clock")
a, b = make_bot(books=json.loads(json.dumps(far)))
b.cfg.ref_tilt_rampin_min = 0.0
b.cfg.ref_tilt_enabled = True
b.tilt.update = lambda samples, now_m: S
b.tilt_s = S
b.refs = FakeRefs(dict(REFS))
b.cycle()                                                 # gap seen on the raw r (11: -1 on both raw and r')
check("(gap seen: 11 direction -1, nothing taken yet)", b.ex["11"].take_dir == -1 and not a.inv.get("11"),
      (b.ex["11"].take_dir, a.inv.get("11")))
later(b)                                                  # the raw gap has been confirmed for 31 s ...
b.cfg.take_tilted_ref = True                              # ... and then the take price switches to r'
b.refs.new_reading(b.refs.prices, {})
b.cycle()
check("first cycle after the toggle: nothing taken (same direction, but r' not yet confirmed)",
      not {k: v for k, v in a.inv.items() if v}, a.inv)
check("...the clocks restarted (take_since this cycle)", b.ex["11"].take_dir == -1
      and M.time.monotonic() - b.ex["11"].take_since < 30, (b.ex["11"].take_dir, b.ex["11"].take_since))
later(b)
b.refs.new_reading(b.refs.prices, {})
b.cycle()
check("...confirmed take_confirm_seconds later on r': taken", a.inv.get("11", 0) < 0, a.inv)
_, b2 = run()
check("no toggle: take_tilted_seen tracks the flag, no reset", b2.take_tilted_seen is False)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
