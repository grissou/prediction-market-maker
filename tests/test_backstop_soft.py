"""
Offline tests for the Package 6 candidate "backstop soft band" (backstop_soft_frac): in a band below the
sum-of-maxima reduce-only backstop, the ADDING side's size shrinks linearly to 0 (backstop_soft_factor); the
reducing side is never shrunk; flag off = identical quotes; cycle step 6 stores the factor; live-overridable 0..0.3.
Fake exchange, no network.

Run:  python tests/test_backstop_soft.py      (exit code 0 = all passed)
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


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("default off (0.0)", getattr(c, "backstop_soft_frac", None) == 0.0)
check("last field of Config", list(M.Config.__dataclass_fields__)[-1] == "backstop_soft_frac")
check("last key of OVERRIDABLE", list(M.OVERRIDABLE)[-1] == "backstop_soft_frac")
good, bad = M.validate_overrides({"backstop_soft_frac": 0.05}, c)
check("0.05 accepted live", good == {"backstop_soft_frac": 0.05} and not bad, bad)
_, bad = M.validate_overrides({"backstop_soft_frac": 0.31}, c)
check("0.31 refused (range 0..0.3)", len(bad) == 1, bad)
_, bad = M.validate_overrides({"backstop_soft_frac": -0.01}, c)
check("-0.01 refused", len(bad) == 1, bad)

print("--- helper (backstop 0.80, band 0.05 -> 75k..80k on a 100k account)")
f = getattr(M, "backstop_soft_factor", None)
check("backstop_soft_factor exists", callable(f))
if callable(f):
    c = M.Config()
    c.worst_case_backstop_frac, c.risk_model = 0.80, "correlated"
    check("off (0.0) -> 1 even above the backstop", f(90000.0, 100000.0, c) == 1.0)
    c.backstop_soft_frac = 0.05
    check("below the band (70k) -> 1", f(70000.0, 100000.0, c) == 1.0)
    check("at the band's start (75k) -> 1", f(75000.0, 100000.0, c) == 1.0)
    check("mid-band (77.5k) -> 0.5", abs(f(77500.0, 100000.0, c) - 0.5) < 1e-9, f(77500.0, 100000.0, c))
    check("79k -> 0.2", abs(f(79000.0, 100000.0, c) - 0.2) < 1e-9, f(79000.0, 100000.0, c))
    check("at the backstop (80k) -> 0", f(80000.0, 100000.0, c) == 0.0)
    check("above (85k) -> 0", f(85000.0, 100000.0, c) == 0.0)
    check("no account value -> 1", f(85000.0, None, c) == 1.0 and f(85000.0, 0.0, c) == 1.0)
    c.risk_model = "sum_max"
    check("risk_model sum_max (no backstop) -> 1", f(77500.0, 100000.0, c) == 1.0)

print("--- decide: the adding side shrinks, the reducing side never")
_T0 = time.time()
time.time = lambda: _T0                                # frozen wall clock (the process ends with the test)
BOOK = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}


def dec(b, pos, fv, eid="21"):
    ex = b.ex[eid]
    ex.book = BOOK
    ex.last_fv, ex.cooldown_until = None, -1e9
    return b.decide(ex, fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic(), book_fv=fv)


api, b = make_bot()
b.cycle()
b.cfg.behind_best_size_enabled = False                 # exact sizes (behind-the-best sizing has its own tests)
for pos, add, red in ((300, "bid", "ask"), (-300, "ask", "bid")):
    b.cfg.backstop_soft_frac, b.backstop_adding_factor = 0.0, 1.0
    base = dec(b, pos, 0.52)
    b.cfg.backstop_soft_frac, b.backstop_adding_factor = 0.05, 0.5
    half = dec(b, pos, 0.52)
    b.backstop_adding_factor = 0.0
    zero = dec(b, pos, 0.52)
    bs, rs = getattr(base, add + "_size"), getattr(base, red + "_size")
    check(f"pos {pos:+d}: setup quotes both sides", bs > 1 and rs > 0, base)
    check(f"pos {pos:+d}: factor 0.5 -> adding {add} size halved ({bs} -> {getattr(half, add + '_size')})",
          getattr(half, add + "_size") == int(bs * 0.5) or abs(getattr(half, add + "_size") - bs * 0.5) <= 1, half)
    check(f"pos {pos:+d}: factor 0.5 -> reducing {red} untouched",
          getattr(half, red + "_size") == rs and getattr(half, red) == getattr(base, red), (half, base))
    check(f"pos {pos:+d}: factor 0 -> adding {add} not quoted", getattr(zero, add + "_size") == 0
          or getattr(zero, add) is None, zero)
    check(f"pos {pos:+d}: factor 0 -> reducing {red} untouched", getattr(zero, red + "_size") == rs, zero)

print("--- flag off: identical quotes on a grid (a stale factor is ignored)")
same = True
for pos in (-800, -300, -50, 0, 50, 300, 800):
    for fv in (0.30, 0.50, 0.52, 0.60, 0.75):
        b.cfg.backstop_soft_frac, b.backstop_adding_factor = 0.0, 1.0
        q1 = dec(b, pos, fv)
        b.backstop_adding_factor = 0.3
        q2 = dec(b, pos, fv)
        if q1 != q2 or q1.bid_size != q2.bid_size or q1.ask_size != q2.ask_size:
            same = False
            print("    differs", pos, fv, q1, q2)
check("off: 35 cases identical", same)
b.cfg.backstop_soft_frac, b.backstop_adding_factor = 0.0, 1.0
q_off = dec(b, 300, 0.52)
b.cfg.backstop_soft_frac = 0.05
q_on = dec(b, 300, 0.52)
check("on with factor 1 (below the band): identical", q_on == q_off and q_on.bid_size == q_off.bid_size
      and q_on.ask_size == q_off.ask_size, (q_on, q_off))

print("--- cycle step 6 stores the factor")
api, b = make_bot()
check("Bot.__init__ default 1.0", getattr(b, "backstop_adding_factor", None) == 1.0)
seen = []
real = getattr(M, "backstop_soft_factor", None)


def spy(worst, equity, cfg):
    r = real(worst, equity, cfg)
    seen.append((worst, equity, r))
    return r


if real is not None:
    M.backstop_soft_factor = spy
api.inv = {"21": 1000.0}                               # long 1000 Utah Rep: worst case ~ 1000 x price
b.cycle()
check("cycle called the helper once", len(seen) == 1, seen)
if seen:
    w, eq, _ = seen[0]
    check("with the sum-of-maxima worst case (> 0) and the account value", w > 0 and eq and eq > 0, seen)
    # place a band around that worst case: backstop at worst/eq + 0.002, band 0.004 -> factor 0.5
    b.cfg.worst_case_backstop_frac = w / eq + 0.002
    b.cfg.backstop_soft_frac = 0.004
    b.cfg.max_worst_case_frac = 0.60
    seen.clear()
    b.cycle()
    w2, eq2, r2 = seen[-1]
    check("factor stored on the bot = the helper's value", b.backstop_adding_factor == r2, (b.backstop_adding_factor, r2))
    check("inside the band -> strictly between 0 and 1", 0.0 < b.backstop_adding_factor < 1.0, seen)
    b.cfg.backstop_soft_frac = 0.0
    b.cycle()
    check("flag off -> stored 1.0", b.backstop_adding_factor == 1.0)
if real is not None:
    M.backstop_soft_factor = real

print("--- live_sim mirror")
src = open(os.path.join(HERE, "live_sim.py")).read()
check("live_sim sets bot.backstop_adding_factor from M.backstop_soft_factor",
      "bot.backstop_adding_factor = M.backstop_soft_factor(worst, equity, cfg)" in src)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
