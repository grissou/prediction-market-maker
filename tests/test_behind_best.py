"""
Offline tests for behind-the-best sizing (behind_best_*): compute_quote shrinks an ADDING quote resting
behind_best_ticks or more behind the best other order on its side (floor behind_best_min_size), decide's ref-only
exemption, the " bb" log tag, status.json's behind_best_markets, and hot-fix 2.2's resting-order rule. No network.

Run:  python tests/test_behind_best.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import sys
import time
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: OFF, 2 ticks, x0.5, min 100 sh",
      (c.behind_best_size_enabled, c.behind_best_ticks, c.behind_best_size_factor, c.behind_best_min_size)
      == (False, 2, 0.5, 100))
good, bad = M.validate_overrides({"behind_best_size_enabled": True, "behind_best_ticks": 3,
                                  "behind_best_size_factor": 0.25, "behind_best_min_size": 50}, c)
check("all four settings are live-overridable", len(good) == 4 and not bad, bad)
_ov = list(M.OVERRIDABLE)   # (Round 4's write-saver keys were added after them, at the end)
_i = _ov.index("behind_best_size_enabled")
check("...and they are one block of OVERRIDABLE keys, in order", _ov[_i:_i + 4] == [
      "behind_best_size_enabled", "behind_best_ticks", "behind_best_size_factor", "behind_best_min_size"])

print("--- compute_quote")
off = replace(M.CFG, fl_bias_enabled=False, reduce_join_best=False)
on = replace(off, behind_best_size_enabled=True)


def cq(cfg, inv, bb, ba=0.60, **kw):
    """fv 0.50, min_edge 1c: our bid is at most 0.49; the best other ask 0.60 sits behind our 0.54 ask."""
    return M.compute_quote(0.50, inv, inv, bb, ba, cfg, order_size=400, **kw)


q0, q = cq(off, 0, 0.50), cq(on, 0, 0.50)
check("bid 0.49 two ticks behind the best 0.50 (flat: adding): 400 -> 200, price unchanged",
      q.bid == q0.bid == 0.49 and q0.bid_size == 400 and q.bid_size == 200 and q.behind, (q0, q))
check("...ask 0.54 AHEAD of the best 0.60: unchanged", (q.ask, q.ask_size) == (q0.ask, q0.ask_size) == (0.54, 400))
check("...bid_max keeps the unscaled 400 (a resting full-size order may stay)", q.bid_max == 400, q)
q = cq(on, 0, 0.495)
check("one tick behind (best 0.495): unchanged", q == cq(off, 0, 0.495) and q.bid_size == 400 and not q.behind, q)
q = cq(on, 0, 0.49)
check("at the best (joined 0.49): unchanged", q.bid == 0.49 and q.bid_size == 400 and not q.behind, q)
q = cq(on, 0, 0.40)
check("ahead of the best (pennying 0.40): unchanged", q == cq(off, 0, 0.40) and not q.behind, q)
q = cq(on, 0, None)
check("empty bid side (no other order): unchanged", q == cq(off, 0, None) and q.bid_size == 400, q)
q, q0 = cq(on, -300, 0.50), cq(off, -300, 0.50)
check("short 300: the bid REDUCES -> unchanged even 2+ ticks behind", (q.bid, q.bid_size) == (q0.bid, q0.bid_size)
      and not q.behind, (q0, q))
q0, q = cq(off, 300, 0.30, ba=0.50), cq(on, 300, 0.30, ba=0.50)
check("long 300, best ask 0.50 below our ask: the ask reduces -> unchanged",
      (q.ask, q.ask_size) == (q0.ask, q0.ask_size) and q0.ask - 0.50 >= 2 * M.TICK - 1e-9, (q0, q))
q0, q = cq(off, -300, 0.30, ba=0.50), cq(on, -300, 0.30, ba=0.50)
check("short 300: the ask ADDS, behind the best 0.50 -> halved", q0.ask - 0.50 >= 2 * M.TICK - 1e-9
      and q.ask_size == q0.ask_size // 2 and q.behind and q.ask_max == q0.ask_size, (q0, q))
check("3 ticks setting: a 2-tick gap is no longer 'behind'",
      cq(replace(on, behind_best_ticks=3), 0, 0.50).bid_size == 400)
check("min size floor: x0.1 of 400 -> 100 (behind_best_min_size)",
      cq(replace(on, behind_best_size_factor=0.1), 0, 0.50).bid_size == 100)
q = M.compute_quote(0.50, 0, 0, 0.50, 0.60, on, order_size=150)
check("...150 x0.5 = 75 floors at 100", q.bid_size == 100 and q.behind, q)
q = M.compute_quote(0.50, 0, 0, 0.50, 0.60, on, order_size=80)
check("...never grows a size already below the floor (80 stays 80, not tagged)", q.bid_size == 80 and not q.behind, q)
q = cq(on, 0, 0.50, adding_factor=0.5)
check("composes with the capital ceiling (x0.5): 400 x 0.5 x 0.5 = 100", q.bid_size == 100 and q.bid_max == 400, q)
q = cq(replace(on, behind_best_min_size=0), 0, 0.50, adding_factor=0.25)
check("...ceiling x0.25 then x0.5 (no floor): 400 -> 50", q.bid_size == 50 and q.bid_max == 400, q)
check("...ceiling x0 (withdrawn): the floor never brings it back", cq(on, 0, 0.50, adding_factor=0.0).bid is None)
check("behind_best=False (ref-only markets): unchanged", cq(on, 0, 0.50, behind_best=False) == cq(off, 0, 0.50))
check("factor 1.0: unchanged", cq(replace(on, behind_best_size_factor=1.0), 0, 0.50) == cq(off, 0, 0.50))
grid = [(fv, inv, bb, ba) for fv in (0.2, 0.5, 0.8) for inv in (-500, 0, 500)
        for bb, ba in ((None, None), (0.15, 0.85), (0.49, 0.51), (0.19, 0.81))]
check("disabled = today: same quotes as with the other knobs changed, over a grid", all(
      M.compute_quote(fv, inv, inv, bb, ba, off, order_size=400)
      == M.compute_quote(fv, inv, inv, bb, ba, replace(off, behind_best_ticks=1, behind_best_size_factor=0.0,
                                                       behind_best_min_size=0), order_size=400)
      for fv, inv, bb, ba in grid) and cq(off, 0, 0.50).bid_size == 400)

print("--- decide: market 21 (book 0.48 / 0.56), fair 0.47 -> our bid 0.46, 4 ticks behind 0.48")
FV = 0.47


def bot(**kw):
    a, b = make_bot()
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    b.cycle()
    return a, b


def dec(b, pos, eid="21"):
    return b.decide(b.ex[eid], FV, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic())


a0, b0 = bot()
ref = dec(b0, 0)
check("disabled: full size 100, no tag", ref.bid == 0.46 and ref.bid_size == 100 and b0.ex["21"].bb_tag == "", ref)
a, b = bot(behind_best_size_enabled=True, behind_best_min_size=10)
q = dec(b, 0)
check("enabled (min 10): adding bid 100 -> 50, ask unchanged, tagged ' bb'", q.bid == 0.46 and q.bid_size == 50
      and (q.ask, q.ask_size) == (ref.ask, ref.ask_size) and b.ex["21"].bb_tag == " bb", q)
q = dec(b, -300)
check("short 300: the bid reduces -> unchanged, no tag", q.bid_size == dec(b0, -300).bid_size
      and b.ex["21"].bb_tag == "", q)
b.ref_only.add("21")
q = dec(b, 0)
check("ref-only market: unchanged (already small), no tag", not q.behind and b.ex["21"].bb_tag == "", q)
b.ref_only.discard("21")
b.cfg.behind_best_size_enabled = False                   # live override back off
check("switched off live: back to today", dec(b, 0) == ref and b.ex["21"].bb_tag == "")

print("--- the bot: resting orders (hot-fix 2.2), log line, status.json")


class Grab(logging.Handler):
    def __init__(self):
        super().__init__(); self.msgs = []

    def emit(self, r): self.msgs.append(r.getMessage())


a, b = bot(min_edge=0.05, max_half_spread=0.08)          # every quote well inside fair: behind the best others
rest0 = sorted(a.ours("21"))
check("off: full-size orders rest (100 each side)", rest0 and all(o[2] == 100 for o in rest0), rest0)
n_cancel = len(a.sent("cancel_order"))
b.cfg.behind_best_size_enabled, b.cfg.behind_best_min_size = True, 10
b.cycle()
check("factor kicks in: the resting full-size orders are NOT cancelled (hot-fix 2.2)",
      len(a.sent("cancel_order")) == n_cancel and sorted(a.ours("21")) == rest0, a.sent("cancel_order")[n_cancel:])
check("...while the wanted size is 50 (bid_max 100)", b.ex["21"].quote.bid_size == 50
      and b.ex["21"].quote.bid_max == 100, b.ex["21"].quote)
n = sum(1 for x in b.ex.values() if x.bb_tag)
check("status: behind_best_markets counts the affected markets", b.health.get("behind_best_markets") == n >= 1,
      (b.health.get("behind_best_markets"), n))
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries behind_best_markets", st.get("behind_best_markets") == n)
a, b = make_bot()
b.cfg.min_edge, b.cfg.max_half_spread, b.cfg.behind_best_size_enabled, b.cfg.behind_best_min_size = 0.05, 0.08, True, 10
g = Grab()
M.log.addHandler(g)
old_level, old_prop = M.log.level, M.log.propagate
M.log.setLevel(logging.INFO)
M.log.propagate = False
b.cycle()                                                # first cycle: places (and logs) every quote
M.log.removeHandler(g)
M.log.setLevel(old_level)
M.log.propagate = old_prop
line = [m for m in g.msgs if "Utah" in m and "fv" in m and "| bid" in m]
check("quote log line ends ' bb'", line and line[-1].endswith(" bb"), line[-1:] if line else g.msgs[:3])
check("...and the new order is placed at the shrunk size (50)", sorted(o[2] for o in a.ours("21")) == [50, 50],
      a.ours("21"))
a, b = bot()
check("disabled: behind_best_markets 0", b.health.get("behind_best_markets") == 0)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
