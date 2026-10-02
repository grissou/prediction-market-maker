"""
Offline tests for Package 5 X5, the gap-size shrink (gap_size_shrink, gap_size_floor): where the quoted fv and the
book's own price disagree, the side that ADDS to the position shrinks (factor max(floor, 1 - gap / shrink)); the
reducing side is untouched; ref-only markets and markets without a book price are untouched; flag off = identical.
Fake exchange, no network.

Run:  python tests/test_gap_shrink.py      (exit code 0 = all passed)
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
KEYS = ["gap_size_shrink", "gap_size_floor"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: off (0), floor 0.25", tuple(getattr(c, k, None) for k in KEYS) == (0.0, 0.25))
good, bad = M.validate_overrides({"gap_size_shrink": 0.03, "gap_size_floor": 0.5}, c)
check("both live-overridable", len(good) == 2 and not bad, bad)
_, bad = M.validate_overrides({"gap_size_shrink": 0.5, "gap_size_floor": 1.5}, c)
check("ranges checked (0.5, 1.5 refused)", len(bad) == 2, bad)
_, bad = M.validate_overrides({"gap_size_shrink": -0.01, "gap_size_floor": -0.1}, c)
check("negative values refused", len(bad) == 2, bad)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("one contiguous block in OVERRIDABLE and in Config",
      _ov[_ov.index(KEYS[0]):_ov.index(KEYS[0]) + 2] == KEYS and _f[_f.index(KEYS[0]):_f.index(KEYS[0]) + 2] == KEYS)
check("in Config after the T2.3 block", _f.index(KEYS[0]) > _f.index("ref_tilt_carry_days"))

print("--- the factor")
on = M.Config()
on.gap_size_shrink, on.gap_size_floor = 0.03, 0.25
check("gap 0 -> 1", M.gap_size_factor(0.5, 0.5, on) == 1.0)
check("gap = shrink -> floor", abs(M.gap_size_factor(0.53, 0.50, on) - 0.25) < 1e-9)
check("gap beyond shrink -> floor", M.gap_size_factor(0.40, 0.50, on) == 0.25)
check("gap half -> 0.5", abs(M.gap_size_factor(0.485, 0.50, on) - 0.5) < 1e-9)
check("off -> 1 whatever the gap", M.gap_size_factor(0.30, 0.50, M.Config()) == 1.0)
check("book_fv None -> 1", M.gap_size_factor(0.30, None, on) == 1.0)

api, b = make_bot()
b.cycle()
BOOK = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}


def dec(pos, fv, book_fv, shrink=0.0, floor=0.25, eid="21", ref_only=False):
    b.cfg.gap_size_shrink, b.cfg.gap_size_floor = shrink, floor
    b.ref_only = {eid} if ref_only else set()
    ex = b.ex[eid]
    ex.book = BOOK
    ex.last_fv, ex.cooldown_until = None, -1e9          # (no jump guard between scripted cases)
    q = b.decide(ex, fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic(), book_fv=book_fv)
    b.ref_only = set()
    return q


print("--- flag off = identical")
GRID = [(fv, bfv, inv) for fv in (0.50, 0.52, 0.55) for bfv in (None, 0.50, 0.52, 0.45) for inv in (-300, 0, 300)]
same = all(dec(i, f, g) == dec(i, f, g, 0.0, 0.9) and dec(i, f, g).bid_limit == dec(i, f, g, 0.0, 0.9).bid_limit
           for f, g, i in GRID)
check("shrink 0: identical on a (fv, book_fv, inv) grid whatever the floor", same)
check("shrink on but gap 0: identical to off",
      all(dec(i, f, f) == dec(i, f, f, 0.03) for f in (0.50, 0.52, 0.55) for i in (-300, 0, 300)))

print("--- flat: both sides shrink")
off, half, full = dec(0, 0.52, 0.52), dec(0, 0.52, 0.505, 0.03), dec(0, 0.52, 0.49, 0.03)
check("setup: flat quotes both sides with size > 8", off.bid_size > 8 and off.ask_size > 8, off)
check("gap half: both sides ~0.5x (within a share: float)",
      abs(half.bid_size - off.bid_size * 0.5) <= 1 and abs(half.ask_size - off.ask_size * 0.5) <= 1,
      (half, off))
check("gap = shrink: both sides at the floor",
      full.bid_size == int(off.bid_size * 0.25) and full.ask_size == int(off.ask_size * 0.25), (full, off))
check("prices unchanged by the shrink", (half.bid, half.ask) == (dec(0, 0.52, 0.505).bid, dec(0, 0.52, 0.505).ask))

print("--- long / short: only the adding side shrinks")
for pos, add, red in ((300, "bid_size", "ask_size"), (-300, "ask_size", "bid_size")):
    o, s = dec(pos, 0.52, 0.49), dec(pos, 0.52, 0.49, 0.03)
    check(f"inv {pos}: reducing side ({red}) unchanged", getattr(s, red) == getattr(o, red) and getattr(o, red) > 0,
          (s, o))
    check(f"inv {pos}: adding side ({add}) at the floor", getattr(s, add) == int(getattr(o, add) * 0.25)
          and getattr(o, add) > 4, (s, o))

print("--- untouched cases")
check("book_fv None: unchanged", dec(0, 0.52, None, 0.03) == dec(0, 0.52, None))
check("ref-only market: unchanged", dec(0, 0.52, 0.45, 0.03, ref_only=True) == dec(0, 0.52, 0.45, ref_only=True))

print("--- never own bid >= own ask")
bad = [(f, g, i) for f in (0.30, 0.50, 0.52, 0.70) for g in (0.40, 0.50, 0.55, 0.60) for i in (-500, -50, 0, 50, 500)
       for q in [dec(i, f, g, 0.03)] if q.bid is not None and q.ask is not None and q.bid >= q.ask]
check("on a grid with the shrink on", not bad, bad[:3])

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
