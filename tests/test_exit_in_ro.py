"""
Offline tests for the Package 6 candidates "exits keep quoting in reduce-only":
  exit_quotes_in_reduce_only   reduce_join_best also runs while reduce-only (reducing side only, sized <= the
                               race-netted position, every other guard kept); the hold target (C) that shared the
                               flag was removed on simplify (never enabled live);
  pair_passive_in_reduce_only  T2.5's passive slice may rest on a complete-set leg whose slice side decide() left
                               empty ONLY because of the reduce-only race-net clip (ex.ro_clip).
Flags off = unchanged. Fake exchange, no network.

Run:  python tests/test_exit_in_ro.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot                           # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEYS = ["exit_quotes_in_reduce_only", "pair_passive_in_reduce_only"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: both off", tuple(getattr(c, k, None) for k in KEYS) == (False, False))
good, bad = M.validate_overrides({k: True for k in KEYS}, c)
check("both live-overridable", len(good) == 2 and not bad, bad)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("one contiguous block in OVERRIDABLE and in Config",
      KEYS[0] in _ov and KEYS[0] in _f and _ov[_ov.index(KEYS[0]):_ov.index(KEYS[0]) + 2] == KEYS
      and _f[_f.index(KEYS[0]):_f.index(KEYS[0]) + 2] == KEYS)

_T0 = time.time()                                      # frozen wall clock: ages never drift between two decide calls
time.time = lambda: _T0                                # (the process ends with the test)

api, b = make_bot()
b.cycle()
ex = b.ex["21"]
BOOK = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}


def dec(pos, eff, fv=0.62, book_fv=0.52, reduce=True, ref=None, book=BOOK, eid="21"):
    x = b.ex[eid]
    x.book = book
    x.last_fv, x.cooldown_until = None, -1e9           # (no jump guard between scripted cases)
    return b.decide(x, fv, {eid: float(pos)}, {eid: float(eff)}, reduce, 0.0, time.monotonic(), ref=ref,
                    book_fv=book_fv)


def flags(**kw):
    for k, v in {**dict(exit_quotes_in_reduce_only=False, pair_passive_in_reduce_only=False,
                        reduce_join_best=False, pair_unwind_passive=False), **kw}.items():
        setattr(b.cfg, k, v)


def no_cross(q):
    return not (q.bid is not None and q.ask is not None and q.bid >= q.ask - 1e-9)


print("--- (1) grid: reduce-only, flag on: never bid >= ask, reducing size <= race-netted position")
bad, n = [], 0
for bb, ba in ((0.48, 0.56), (0.45, 0.46), (0.30, 0.35), (None, 0.50), (0.52, 0.53), (0.60, 0.70)):
    for fv in (0.40, 0.52, 0.62):
        for bfv in (0.45, 0.52, 0.65):
            for pos, eff in ((500, 500), (500, 120), (500, 0), (-500, -500), (-500, -80), (500, -200)):
                for rjb in (False, True):
                    book = {"bids": [lvl(bb, 1000)] if bb else [], "asks": [lvl(ba, 1000)]}
                    b.lots["21"] = [[float(pos), _T0 - 9 * 3600]]
                    flags(exit_quotes_in_reduce_only=True, reduce_join_best=rjb, reduce_join_min_shares=1)
                    q = dec(pos, eff, fv=fv, book_fv=bfv, book=book)
                    n += 1
                    if not no_cross(q):
                        bad.append(("cross", bb, ba, fv, bfv, pos, eff, q))
                    if q.ask_size > max(0, eff) or q.bid_size > max(0, -eff):
                        bad.append(("size", bb, ba, fv, bfv, pos, eff, q))
                    if q.ask is not None and bb is not None and q.ask <= bb + 1e-9:
                        bad.append(("crosses best bid", bb, ba, fv, bfv, pos, eff, q))
                    if q.bid is not None and q.bid >= ba - 1e-9:
                        bad.append(("crosses best ask", bb, ba, fv, bfv, pos, eff, q))
check(f"{n} cases", not bad, bad[:2])
b.cfg.reduce_join_min_shares = M.Config().reduce_join_min_shares

print("--- (1) reduce_join_best in reduce-only (compute_quote; skews off, 2c edge: normal ask 0.52 at fv 0.50)")
J = dict(bankroll=100000, order_size=100, min_edge=0.02, reduce_only=True)
c_off = M.Config(reduce_join_best=True, skew_per_quote=0.0, skew_age_enabled=False)
c_on = replace(c_off, exit_quotes_in_reduce_only=True)


def jq(inv, eff, bb, ba, cfg, **k):
    return M.compute_quote(0.50, inv, eff, bb, ba, cfg, **{**J, **k})


q0, q1 = jq(1000, 1000, 0.45, 0.515, c_off), jq(1000, 1000, 0.45, 0.515, c_on)
check("flag off: the ask stays at fv + edge 0.52 in reduce-only", q0.ask == 0.52 and q0.bid is None, q0)
check("flag on: joins the rival 0.515, same size, no bid", q1.ask == 0.515 and q1.ask_size == q0.ask_size
      and q1.bid is None and no_cross(q1), (q0, q1))
q1 = jq(1000, 60, 0.45, 0.515, replace(c_on, reduce_join_min_shares=1))
check("race-netted 60: joins with at most 60 shares", q1.ask == 0.515 and 1 <= q1.ask_size <= 60, q1)
check("never closer than reduce_join_min_edge to fair (rival 0.495 -> 0.51)",
      jq(1000, 1000, 0.45, 0.495, c_on).ask == 0.51)
check("needs reduce_join_best itself (flag alone does nothing)",
      jq(1000, 1000, 0.45, 0.515, replace(c_on, reduce_join_best=False)) == q0)
check("no_ask (guard) still blocks", jq(1000, 1000, 0.45, 0.515, c_on, no_ask=True).ask is None)
qs0, qs1 = jq(-1000, -1000, 0.485, 0.60, c_off), jq(-1000, -1000, 0.485, 0.60, c_on)
check("short mirror: off bid 0.48, on bid joins 0.485, no ask", qs0.bid == 0.48 and qs1.bid == 0.485
      and qs1.ask is None, (qs0, qs1))
check("fast unload stays off in reduce-only (flag on)",
      jq(1000, 1000, 0.45, 0.60, c_on, unload_side="ask", unload_edge=0.0, unload_size=1000)
      == jq(1000, 1000, 0.45, 0.60, c_on))

print("--- (2) complete set (long 7,335 both Ohio legs) in reduce-only: the passive pair slice")
SETS = 7335
PBOOKS = {"11": {"bids": [lvl(0.62, 1000)], "asks": [lvl(0.64, 1000)]},
          "12": {"bids": [lvl(0.37, 1000)], "asks": [lvl(0.375, 1000)]},
          "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
          "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
api, b = make_bot(books=PBOOKS)
b.cycle()
for e, bk in PBOOKS.items():
    b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
INV = {"11": float(SETS), "12": float(SETS)}
EFF = b.effective_inventory(INV)
RACE = b.ex["11"].group
plan = b.pair_passive_plan(["11", "12"], INV, {})
cand = b.pp_candidate("11", "12", 1, SETS, b.bankroll())
check("setup: the plan rests leg A's ask at pp_candidate's price (0.64), one slice",
      plan and plan["leg"] == "11" and cand and plan["price"] == cand[1] == 0.64 and plan["slice"] == cand[2], plan)


def pq(fv=0.63, book_fv=0.63, ref=None, eid="11"):
    x = b.ex[eid]
    x.last_fv, x.cooldown_until = None, -1e9
    q = b.decide(x, fv, INV, EFF, True, 0.0, time.monotonic(), ref=ref, book_fv=book_fv)
    b.pp = {RACE: dict(plan)}
    return q, b.pair_passive_quote(x, q)


for cfgs in ({}, {"pair_unwind_passive": True}, {"pair_passive_in_reduce_only": True}):
    for k in ("pair_unwind_passive", "pair_passive_in_reduce_only", "exit_quotes_in_reduce_only"):
        setattr(b.cfg, k, cfgs.get(k, False))
    q, p = pq()
    check(f"{cfgs or 'flags off'}: decide leaves both sides empty (race-net 0), the slice does not rest",
          q.ask is None and q.bid is None and p == q and p.ask is None, (q, p))
check("decide records why: the ask was emptied by the reduce-only clip alone", "ask" in b.ex["11"].ro_clip.split(),
      b.ex["11"].ro_clip)
b.cfg.pair_unwind_passive = b.cfg.pair_passive_in_reduce_only = True
q, p = pq()
check("pair_unwind_passive + pair_passive_in_reduce_only: the slice ask rests at pp_candidate's price for one slice",
      (p.ask, p.ask_size) == (plan["price"], plan["slice"]) and no_cross(p), p)
check("...the other leg (B) still quotes nothing (it is not the resting leg)", pq(eid="12")[1].ask is None)
q, p = pq(fv=None)
check("fv None: nothing rests", q == M.NO_QUOTE and p.ask is None and b.ex["11"].ro_clip == "", (q, p))
q, p = pq(ref=0.75)
check("reference guard (Polymarket 0.75 vs book 0.63): nothing rests", p.ask is None and b.ex["11"].ro_clip == "",
      (q, p))
b.ex["11"].cooldown_until = time.monotonic() + 60
b.ex["11"].last_fv = 0.63
q = b.decide(b.ex["11"], 0.63, INV, EFF, True, 0.0, time.monotonic(), book_fv=0.63)
check("jump cooldown: nothing rests", q == M.NO_QUOTE and b.pair_passive_quote(b.ex["11"], q).ask is None)
q, p = pq()
INV2 = {"11": float(SETS), "12": float(SETS - 300)}            # 300 more of A than B: race-net +300 on A
x = b.ex["11"]
x.last_fv, x.cooldown_until = None, -1e9
q2 = b.decide(x, 0.63, INV2, b.effective_inventory(INV2), True, 0.0, time.monotonic(), book_fv=0.63)
check("race-net +300 on A: decide's own reducing ask (no clip flag), the slice replaces it as before",
      q2.ask is not None and x.ro_clip == "" and b.pair_passive_quote(x, q2).ask == plan["price"], q2)
b.cfg.pair_passive_in_reduce_only = False
check("flag off: unchanged (the clipped side stays empty)", pq()[1].ask is None)

print("--- flags off = identical: decide grid with only the new flags toggled where they must not act")
api, b = make_bot()
b.cycle()
bad, n = [], 0
for bb, ba in ((0.48, 0.56), (0.45, 0.46), (None, 0.50), (0.52, 0.53)):
    for fv in (0.40, 0.52, 0.62):
        for pos, eff in ((500, 500), (500, 0), (-500, -500), (500, -200), (0, 0)):
            for reduce in (False, True):
                b.lots["21"] = [[float(pos), _T0 - 9 * 3600]] if pos else []
                book = {"bids": [lvl(bb, 1000)] if bb else [], "asks": [lvl(ba, 1000)]}
                outs = []
                for fl in (False, True):
                    # reduce-only: identical while reduce_join_best is off; otherwise identical always
                    flags(exit_quotes_in_reduce_only=fl, pair_passive_in_reduce_only=fl, reduce_join_best=not reduce)
                    q = dec(pos, eff, fv=fv, book_fv=0.52, reduce=reduce, book=book)
                    outs.append((q, q.bid_limit, q.ask_limit, q.bid_max, q.ask_max))
                n += 1
                if outs[0] != outs[1]:
                    bad.append((bb, ba, fv, pos, eff, reduce, outs))
check(f"{n} cases identical", not bad, bad[:2])
q = M.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True)
w = {}
q2 = M.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True, why=w)
check("the why dict never changes the quote (long 500, race-net 0: both sides clipped)",
      q == q2 and w == {"ro_clip": "bid ask"}, (q, q2, w))
w = {}
M.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True, no_ask=True, why=w)
check("...a blocked side is not 'clipped'", w == {"ro_clip": "bid"}, w)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
