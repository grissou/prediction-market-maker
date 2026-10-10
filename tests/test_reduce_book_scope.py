"""
Offline tests for Package 5 X11: reduce_from_book_dead_only / reduce_from_book_max_turnover (A, the book-priced
reducing side, restricted to turnover-dead or low-flow markets). Flag off = A alone; A off = unchanged. No network.

Run:  python tests/test_reduce_book_scope.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEYS = ["reduce_from_book_dead_only", "reduce_from_book_max_turnover"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: dead_only off, max_turnover 0",
      (getattr(c, KEYS[0], None), getattr(c, KEYS[1], None)) == (False, 0.0))
good, bad = M.validate_overrides({KEYS[0]: True, KEYS[1]: 50.0}, c)
check("both live-overridable", len(good) == 2 and not bad, bad)
_, bad = M.validate_overrides({KEYS[1]: -1.0}, c)
check("negative max_turnover refused", len(bad) == 1, bad)
_, bad = M.validate_overrides({KEYS[1]: 1e6}, c)
check("max_turnover above 100,000 refused", len(bad) == 1, bad)
good, bad = M.validate_overrides({KEYS[1]: 0.0}, c)
check("max_turnover 0 accepted", good == {KEYS[1]: 0.0} and not bad, (good, bad))
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("one contiguous block in OVERRIDABLE and in Config, in order",
      all(k in _ov and k in _f for k in KEYS)
      and _ov[_ov.index(KEYS[0]):_ov.index(KEYS[0]) + 2] == KEYS and _f[_f.index(KEYS[0]):_f.index(KEYS[0]) + 2] == KEYS)
check("...after the T2.4 tilt-exposure / T2.3 blocks (the C hold target between them was removed on simplify)",
      KEYS[0] in _f and _f.index(KEYS[0]) > _f.index("ref_tilt_carry_days")
      and KEYS[0] in _ov and _ov.index(KEYS[0]) > _ov.index("tilt_exposure_headline"))


def bot(**kw):
    api, b = make_bot()
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    b.cycle()
    b.ex["21"].ref_jump_at = -1e9
    return b


def dec(b, pos, fv=0.57, book_fv=0.52, eid="21", now_m=None):
    return b.decide(b.ex[eid], fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0,
                    time.monotonic() if now_m is None else now_m, book_fv=book_fv)


def set_flow(b, flow, dead, eid="21"):
    """What refresh_turnover leaves behind: the observed flow and the hysteresis verdict."""
    b.turnover_flow[eid] = flow
    if flow is None:
        b.turnover_state.pop(eid, None)
    else:
        b.turnover_state[eid] = (dead, 0.0)


now = time.monotonic()
POS = 300
print("--- flag off = A alone")
for label, flow, dead in (("live market", 400.0, False), ("dead market", 5.0, True), ("not judged", None, False)):
    a1, a0 = bot(reduce_from_book=True), bot(reduce_from_book=True, reduce_from_book_dead_only=False,
                                             reduce_from_book_max_turnover=30.0)
    set_flow(a1, flow, dead)
    set_flow(a0, flow, dead)
    check(f"{label}: dead_only off (threshold set or not) quotes exactly as A alone",
          dec(a1, POS, now_m=now) == dec(a0, POS, now_m=now))

print("--- dead_only on")
plain, A, X = bot(), bot(reduce_from_book=True), bot(reduce_from_book=True, reduce_from_book_dead_only=True)
for b in (plain, A, X):
    set_flow(b, 5.0, True)
qp, qa, qx = dec(plain, POS, now_m=now), dec(A, POS, now_m=now), dec(X, POS, now_m=now)
check("dead market: ex.turnover_dead set", X.ex["21"].turnover_dead, X.turnover_state)
check("dead market: A applies (same quote as A alone, ask lowered below the blend's toward the book)",
      qx == qa and qx.ask < qp.ask and qx.ask >= 0.52, (qx, qa, qp))
# the book-priced ask: its limit is book + min_edge (as test_reduce_book shows), skew 0
X0 = bot(reduce_from_book=True, reduce_from_book_dead_only=True, skew_per_quote=0.0)
set_flow(X0, 5.0, True)
qx0 = dec(X0, POS, now_m=now)
check("decide in a dead market, no skew: reducing ask_limit = book 0.52 + min_edge, ask between book and blend",
      qx0.ask_limit == M.ceil_tick(0.52 + X0.cfg.min_edge) and 0.52 <= qx0.ask <= 0.57, qx0)
for b in (plain, A, X):
    set_flow(b, 400.0, False)
qp, qa, qx = dec(plain, POS, now_m=now), dec(A, POS, now_m=now), dec(X, POS, now_m=now)
check("live market: plain quote (identical to A off), while A alone would move it", qx == qp and qa != qp,
      (qx, qp, qa))
for b in (plain, X):
    set_flow(b, 5.0, False)                                # low flow but not (yet) flagged dead, threshold 0
check("low flow but not flagged, max_turnover 0: plain quote", dec(X, POS, now_m=now) == dec(plain, POS, now_m=now))
for b in (plain, X):
    set_flow(b, 5.0, True)
check("flagged dead but flat (no position): turnover_dead False -> plain quote",
      dec(X, 0, now_m=now) == dec(plain, 0, now_m=now) and not X.ex["21"].turnover_dead)

print("--- reduce_from_book_max_turnover")
plain = bot()
X = bot(reduce_from_book=True, reduce_from_book_dead_only=True, reduce_from_book_max_turnover=100.0)
A = bot(reduce_from_book=True)
for flow, want in ((99.0, True), (100.0, False), (150.0, False), (0.0, True), (None, False)):
    for b in (plain, X, A):
        set_flow(b, flow, False)                           # never flagged dead: the threshold alone decides
    qx, qp, qa = dec(X, POS, now_m=now), dec(plain, POS, now_m=now), dec(A, POS, now_m=now)
    check(f"flow {flow} vs threshold 100 (not flagged dead): {'A applies' if want else 'plain quote'}",
          (qx == qa and qx != qp) if want else qx == qp, (qx, qa, qp))
for b in (plain, X, A):
    set_flow(b, 400.0, True)                               # flagged dead (hysteresis) though flow is above threshold
check("flagged dead, flow above the threshold: A applies (the flag alone suffices)",
      dec(X, POS, now_m=now) == dec(A, POS, now_m=now) != dec(plain, POS, now_m=now))

print("--- A off entirely")
plain = bot()
X = bot(reduce_from_book=False, reduce_from_book_dead_only=True, reduce_from_book_max_turnover=1000.0)
for flow, dead in ((5.0, True), (400.0, False), (None, False)):
    set_flow(plain, flow, dead)
    set_flow(X, flow, dead)
    check(f"A off, flow {flow} dead {dead}: unchanged", dec(X, POS, now_m=now) == dec(plain, POS, now_m=now))

print("--- the sim reaches it (live_sim feeds bot.turnover and calls refresh_turnover)")
src = open(os.path.join(HERE, "live_sim.py")).read()
check("live_sim feeds bot.turnover and calls bot.refresh_turnover",
      "bot.turnover.add(" in src and "bot.refresh_turnover(" in src)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
