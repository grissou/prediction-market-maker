"""
Offline tests for the Package 6 candidate "tail adding-size factor" (tail_adding_factor): in markets whose raw
Polymarket reference r is below tail_low or above tail_high, the ADDING side's size is multiplied by the factor
(the shared adding factor in Bot.decide); the reducing side is never touched; no ref -> unchanged; flag off (1.0)
= identical quotes; live-overridable 0..1. Fake exchange, no network.

Run:  python tests/test_tail_factor.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot                           # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEY = "tail_adding_factor"


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("default off (1.0)", getattr(c, KEY, None) == 1.0)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("its own block in Config and OVERRIDABLE, after the Package 6 reduce-only one",
      KEY in _f and KEY in _ov and _f[_f.index(KEY) - 1] == "pair_passive_in_reduce_only"
      and _ov[_ov.index(KEY) - 1] == "pair_passive_in_reduce_only", (_f[-3:], _ov[-3:]))
check("range (0, 1)", M.OVERRIDABLE.get(KEY) == (0.0, 1.0))
good, bad = M.validate_overrides({KEY: 0.25}, c)
check("0.25 accepted live", good == {KEY: 0.25} and not bad, bad)
_, bad = M.validate_overrides({KEY: 1.01}, c)
check("1.01 refused", len(bad) == 1, bad)
_, bad = M.validate_overrides({KEY: -0.01}, c)
check("-0.01 refused", len(bad) == 1, bad)

print("--- decide")
_T0 = time.time()
time.time = lambda: _T0                                # frozen wall clock (the process ends with the test)
BOOK = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}


def dec(b, pos, fv, ref, eid="21"):
    ex = b.ex[eid]
    ex.book = BOOK
    ex.last_fv, ex.cooldown_until = None, -1e9
    return b.decide(ex, fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic(), ref=ref, book_fv=fv)


def sz(q, side):
    return getattr(q, side + "_size") if getattr(q, side) is not None else 0


def same_q(q1, q2):
    return q1 == q2 and q1.bid_size == q2.bid_size and q1.ask_size == q2.ask_size


def ok_cross(q):
    return q.bid is None or q.ask is None or q.bid < q.ask


api, b = make_bot()
b.cycle()
b.cfg.behind_best_size_enabled = False                 # exact sizes (behind-the-best sizing has its own tests)
b.cfg.ref_guard_gap = 1.0                              # isolate: r far from the book must not trip the reference guard
F = 0.25
for r, tail in ((0.03, True), (0.97, True), (0.50, False), (None, False)):
    for pos, add, red in ((300, "bid", "ask"), (-300, "ask", "bid")):
        setattr(b.cfg, KEY, 1.0)
        base = dec(b, pos, 0.52, r)
        setattr(b.cfg, KEY, F)
        on = dec(b, pos, 0.52, r)
        bs, rs = sz(base, add), sz(base, red)
        check(f"r {r} pos {pos:+d}: setup quotes both sides", bs > 4 and rs > 0, base)
        if tail:
            check(f"r {r} pos {pos:+d}: adding {add} x{F} ({bs} -> {sz(on, add)})",
                  abs(sz(on, add) - bs * F) <= 1 and sz(on, add) < bs, (on, base))
        else:
            check(f"r {r} pos {pos:+d}: adding {add} unchanged", same_q(on, base), (on, base))
        check(f"r {r} pos {pos:+d}: reducing {red} untouched",
              sz(on, red) == rs and getattr(on, red) == getattr(base, red), (on, base))
        check(f"r {r} pos {pos:+d}: own bid < own ask", ok_cross(on) and ok_cross(base), on)

print("--- boundaries: the existing tail_low / tail_high (strict, like the tail guard)")
lo, hi = b.cfg.tail_low, b.cfg.tail_high
for r, shrink in ((lo, False), (lo - 0.001, True), (hi, False), (hi + 0.001, True)):
    setattr(b.cfg, KEY, 1.0)
    base = dec(b, 300, 0.52, r)
    setattr(b.cfg, KEY, F)
    on = dec(b, 300, 0.52, r)
    check(f"r {r:.3f}: {'shrunk' if shrink else 'unchanged'}",
          (sz(on, "bid") < sz(base, "bid")) if shrink else same_q(on, base), (on, base))
b.cfg.tail_low, b.cfg.tail_high = 0.10, 0.90           # the settings are honoured, not a hard-coded 5% / 95%
setattr(b.cfg, KEY, 1.0)
base = dec(b, 300, 0.52, 0.08)
setattr(b.cfg, KEY, F)
on = dec(b, 300, 0.52, 0.08)
check("tail_low 0.10: r 0.08 shrunk", sz(on, "bid") < sz(base, "bid"), (on, base))
b.cfg.tail_low, b.cfg.tail_high = lo, hi

print("--- raw r, not fv")
setattr(b.cfg, KEY, 1.0)
base = dec(b, 300, 0.03, 0.30)
setattr(b.cfg, KEY, F)
on = dec(b, 300, 0.03, 0.30)
check("fv 0.03, r 0.30: unchanged", same_q(on, base), (on, base))
setattr(b.cfg, KEY, 1.0)
base = dec(b, 300, 0.04, 0.03)
setattr(b.cfg, KEY, F)
on = dec(b, 300, 0.04, 0.03)
check("fv 0.04, r 0.03, long: bid shrunk, ask (reducing) untouched",
      sz(on, "bid") < sz(base, "bid") and sz(on, "ask") == sz(base, "ask") and on.ask == base.ask, (on, base))
check("...own bid < own ask", ok_cross(on))

print("--- flag off: identical quotes on a grid (a tail r changes nothing vs no r; ref None unchanged when on)")
same, crossed, n = True, False, 0
for pos in (-800, -300, -50, 0, 50, 300, 800):
    for fv in (0.04, 0.30, 0.52, 0.75, 0.96):
        setattr(b.cfg, KEY, 1.0)
        q_none = dec(b, pos, fv, None)
        setattr(b.cfg, KEY, F)
        q_none_on = dec(b, pos, fv, None)
        if not same_q(q_none, q_none_on):
            same = False
            print("    ref None differs", pos, fv, q_none, q_none_on)
        for r in (0.02, 0.04, 0.50, 0.96, 0.98):
            n += 1
            setattr(b.cfg, KEY, 1.0)
            q_off = dec(b, pos, fv, r)
            setattr(b.cfg, KEY, F)
            q_on = dec(b, pos, fv, r)
            crossed = crossed or not ok_cross(q_off) or not ok_cross(q_on)
            if not same_q(q_off, q_none):
                same = False
                print("    off differs", pos, fv, r, q_off, q_none)
check(f"off: {n} cases identical to no reference; ref None identical with the flag on", same)
check(f"never own bid >= own ask ({n} cases, on and off)", not crossed)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
