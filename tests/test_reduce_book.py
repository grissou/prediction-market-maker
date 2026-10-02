"""
Offline tests for Package 5: B (kelly_edge_cap, kelly_max_market_frac, headline_position_frac) and A
(reduce_from_book: the reducing side priced from the tournament book, the self-cross rule, the jump pause, the
ref-only / no-book / headline gates, flag off = unchanged). No network.

Run:  python tests/test_reduce_book.py      (exit code 0 = all passed)
"""
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
BASE_COMMIT = "528b0d2"     # the commit Package 5 B/A was built on: flag off must quote exactly as it did


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: kelly_edge_cap 0 (off), reduce_from_book off, pause 120 s, headline gate off",
      (c.kelly_edge_cap, c.reduce_from_book, c.reduce_from_book_pause_s, c.reduce_from_book_headline)
      == (0.0, False, 120.0, False))
check("existing B limits unchanged in code: kelly_max_market_frac 0.02, headline_position_frac 0.10",
      (c.kelly_max_market_frac, c.headline_position_frac) == (0.02, 0.10))
good, bad = M.validate_overrides({"kelly_edge_cap": 0.015, "reduce_from_book": True, "reduce_from_book_pause_s": 60.0,
                                  "reduce_from_book_headline": True}, c)
check("all four settings are live-overridable", len(good) == 4 and not bad, bad)
_ovk, _rb = list(M.OVERRIDABLE), ["kelly_edge_cap", "reduce_from_book", "reduce_from_book_pause_s",
                                    "reduce_from_book_headline"]
_k = _ovk.index("kelly_edge_cap")            # later Package 5 blocks may follow: one contiguous block, in order
check("...and one contiguous OVERRIDABLE block, in order", _ovk[_k:_k + 4] == _rb)

print("--- B: kelly_edge_cap")
BANK = 100_000.0
k = M.kelly_position
mid = k(0.56, 0.50, BANK, c)                                  # 6c edge at 0.50: 0.25 x 0.06 / 0.5 = 3% -> 2% cap
check("off: 6c edge at 0.50 -> 2% cap / 0.50 = 4,000 shares", mid == 4000, mid)
c15, c10 = replace(c, kelly_edge_cap=0.015), replace(c, kelly_edge_cap=0.01)
check("cap 1.5c binds: 0.25 x 0.015 / 0.5 = 0.75% -> 1,500 shares", k(0.56, 0.50, BANK, c15) == 1500,
      k(0.56, 0.50, BANK, c15))
check("cap 1c binds: 0.5% -> 1,000 shares", k(0.56, 0.50, BANK, c10) == 1000, k(0.56, 0.50, BANK, c10))
check("cap does not bind below it: 1c edge at 0.50, cap 1.5c -> unchanged 0.5% = 1,000 shares",
      k(0.51, 0.50, BANK, c15) == k(0.51, 0.50, BANK, c) == 1000, (k(0.51, 0.50, BANK, c15), k(0.51, 0.50, BANK, c)))
check("selling side too: ask 0.56 vs p 0.50 (NO at 0.44), cap 1c -> 0.25 x 0.01 / 0.56 x 100k / 0.44",
      k(0.50, 0.56, BANK, c10, yes=False) == int(0.25 * 0.01 / 0.56 * BANK / 0.44), k(0.50, 0.56, BANK, c10, yes=False))
fav = k(0.915, 0.90, BANK, c)
check("PIN favourite: cost 0.90, 1.5c edge = 3.75% quarter Kelly > 2% cap -> 2,222 shares, edge cap 1.5c and 1c "
      "do NOT bind (2.5% still > 2%)", fav == 2222 and k(0.915, 0.90, BANK, c15) == 2222
      and k(0.915, 0.90, BANK, c10) == 2222, fav)
check("...kelly_max_market_frac 0.01 is what binds there: 1,111", k(0.915, 0.90, BANK,
                                                                   replace(c, kelly_max_market_frac=0.01)) == 1111)
check("no edge: allowance only, cap irrelevant", k(0.50, 0.55, BANK, c15) == k(0.50, 0.55, BANK, c) == 100)

print("--- B: kelly_max_market_frac 0.01 halves the add size cap (compute_quote)")
big = dict(kelly_p=0.60, bankroll=BANK, order_size=1_000_000)
q2 = M.compute_quote(0.55, 0, 0, None, None, c, **big)
q1 = M.compute_quote(0.55, 0, 0, None, None, replace(c, kelly_max_market_frac=0.01), **big)
check("flat, huge planned size: bid size = the Kelly long limit; 0.01 = half of 0.02 (+-1 share rounding)",
      q2.bid == q1.bid and abs(q1.bid_size - q2.bid_size / 2) <= 1 and q1.bid_size > 0, (q1, q2))
q2 = M.compute_quote(0.55, 1500, 1500, None, None, c, **big)
q1 = M.compute_quote(0.55, 1500, 1500, None, None, replace(c, kelly_max_market_frac=0.01), **big)
lim2 = k(0.60, q2.bid, BANK, c)
check("holding 1,500: remaining add room = limit - 1,500 (0.02), and less than half that at 0.01",
      q2.bid_size == lim2 - 1500 and q1.bid_size == max(0, k(0.60, q1.bid, BANK, replace(c, kelly_max_market_frac=0.01))
                                                         - 1500), (q1, q2, lim2))

print("--- B: headline_position_frac below a held position only stops adding")


def bot(**kw):
    a, b = make_bot()
    for kk, v in kw.items():
        setattr(b.cfg, kk, v)
    b.cycle()
    return a, b


def dec(b, pos, fv=0.52, eid="21", book_fv=None, ref=None, now_m=None):
    return b.decide(b.ex[eid], fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0,
                    time.monotonic() if now_m is None else now_m, ref=ref, book_fv=book_fv)


a, b = bot(size_by_activity=True, headline_races=("Utah Senate",))
bank = b.bankroll()
b.cfg.headline_position_frac = 1000 / bank                  # limit 1,000 shares
under = dec(b, 500)
b.cfg.headline_position_frac = 300 / bank                   # limit 300 < 500 held
over = dec(b, 500)
check("limit 1,000 holding 500: both sides quote", under.bid_size > 0 and under.ask_size > 0, under)
planned = b.size_plan.get("21")
check("limit 300 holding 500: adding bid 0; reducing ask at the same price, still offering the whole position",
      over.bid_size == 0 and over.bid is None and over.ask == under.ask and over.ask_limit == under.ask_limit
      and over.ask_size >= 500, (over, under))
check("...nothing forced out: ask size = min(planned, 300 + 500) (only the flip to short is limited; no dump, no "
      "cross)", over.ask_size == min(planned, 800) and over.ask_size <= under.ask_size, (over, planned))
b.cfg.headline_position_frac = 1000 / bank
under_s = dec(b, -500)
b.cfg.headline_position_frac = 300 / bank
over_s = dec(b, -500)
check("short mirror: adding ask 0, reducing bid at the same price for the whole position", over_s.ask_size == 0
      and over_s.bid == under_s.bid and over_s.bid_size == min(planned, 800) and under_s.ask_size > 0,
      (over_s, under_s))

print("--- A: compute_quote, long with the book 5c under the blend")
FV, BFV, INV = 0.55, 0.50, 500
kw = dict(order_size=200, bankroll=BANK)
off = M.compute_quote(FV, INV, INV, 0.48, 0.51, c, **kw)
on = M.compute_quote(FV, INV, INV, 0.48, 0.51, c, reduce_fv=BFV, **kw)
check("flag-off ask sits at/above the blend (0.55), nobody trades there", off.ask >= 0.55, off)
check("A: ask = book_fv + min_edge = 0.51 band floor (best other ask 0.51 -> penny 0.505 clamped... "
      "to max(book + skew band, book): here 0.505 >= book 0.50 and <= 0.51)",
      0.50 <= on.ask <= 0.51 and on.ask_limit == 0.50, on)
on2 = M.compute_quote(FV, INV, INV, 0.48, None, replace(c, max_skew_through=1.0), reduce_fv=BFV, **kw)
r_book = BFV - c.skew_per_quote * INV / 200
check("no other ask, no skew-through clamp: ask band from the book's reservation price (book - skew + 4c)",
      on2.ask == M.ceil_tick(r_book + c.max_half_spread) and on2.ask_limit == M.ceil_tick(r_book + c.min_edge), on2)
on3 = M.compute_quote(FV, INV, INV, 0.48, 0.515, replace(c, skew_per_quote=0.0), reduce_fv=BFV, **kw)
check("no skew, best other ask 0.515: ask = book_fv + min_edge = 0.51 exactly (not blend + min_edge = 0.56)",
      on3.ask == 0.51 and on3.ask_limit == 0.51, on3)
check("...adding bid capped at the reducing price - 2 x min_edge = 0.49", on3.bid == 0.49 and on3.bid_limit == 0.49
      and on3.bid_size > 0, on3)
check("A: adding bid <= the lowest reducing ask that may rest - 2 x min_edge", on.bid <= on.ask_limit - 0.02 + 1e-9
      and on.bid_limit <= on.ask_limit - 0.02 + 1e-9, on)
check("A: the book-priced ask sells at most the position", on.ask_size <= INV and (on.ask_max or 0) <= INV, on)
small = M.compute_quote(FV, 50, 50, 0.48, 0.51, c, reduce_fv=BFV, **kw)
check("...position 50 < quote 200: the ask is 50 shares", small.ask_size == 50, small)
check("never through the book: ask >= book_fv (max_skew_through 0 now measured from the book)", on.ask >= BFV, on)
above = M.compute_quote(0.50, INV, INV, 0.47, 0.56, c, reduce_fv=0.55, **kw)
base = M.compute_quote(0.50, INV, INV, 0.47, 0.56, c, **kw)
check("book ABOVE the blend while long: never beyond the blend -> identical to flag off", above == base, (above, base))
flat = M.compute_quote(FV, 0, 0, 0.48, 0.51, c, reduce_fv=BFV, **kw)
check("flat: nothing to reduce -> identical to flag off", flat == M.compute_quote(FV, 0, 0, 0.48, 0.51, c, **kw))

print("--- A: short mirror (book 5c over the blend)")
FV, BFV = 0.45, 0.50
offs = M.compute_quote(FV, -INV, -INV, 0.49, 0.52, c, **kw)
ons = M.compute_quote(FV, -INV, -INV, 0.485, 0.52, replace(c, skew_per_quote=0.0), reduce_fv=BFV, **kw)
check("flag-off bid at/below the blend 0.45", offs.bid <= 0.45, offs)
check("A: bid = book_fv - min_edge = 0.49 (best other bid 0.485 -> penny 0.49)", ons.bid == 0.49
      and ons.bid_limit == 0.49, ons)
check("...adding ask capped at reducing bid + 2 x min_edge = 0.51", ons.ask == 0.51 and ons.ask_limit == 0.51, ons)
check("...book-priced bid buys back at most the position", ons.bid_size <= INV)
below = M.compute_quote(0.50, -INV, -INV, 0.44, 0.53, c, reduce_fv=0.45, **kw)
check("book BELOW the blend while short -> identical to flag off",
      below == M.compute_quote(0.50, -INV, -INV, 0.44, 0.53, c, **kw))

print("--- A: self-cross rule, gaps 1-8c, both directions, several books / edges / skews")
bad = []
n = 0
for gap_c in range(1, 9):
    gap = gap_c / 100
    for fv in (0.10, 0.30, 0.50, 0.70, 0.90):
        for side in (1, -1):
            bfv = fv - side * gap                    # long: book under the blend; short: book over it
            if not 0.02 < bfv < 0.98:
                continue
            for inv in (side * 50, side * 500, side * 5000):
                for spread in (0.01, 0.02, 0.04, 0.08):
                    for bb, ba in ((bfv - spread / 2, bfv + spread / 2), (bfv - spread, None), (None, bfv + spread),
                                   (None, None)):
                        bb = None if bb is None else M.floor_tick(bb)
                        ba = None if ba is None else M.ceil_tick(ba)
                        for cfg in (c, replace(c, min_edge=0.015), replace(c, improve_ticks=0),
                                    replace(c, max_skew_through=1.0), replace(c, reduce_join_best=True)):
                            q = M.compute_quote(fv, inv, inv, bb, ba, cfg, reduce_fv=bfv, kelly_p=fv, **kw)
                            n += 1
                            e2 = 2 * cfg.min_edge - 1e-9
                            if q.bid is None and q.ask is None:
                                continue
                            if q.bid is not None and q.ask is not None and q.bid >= q.ask:
                                bad.append(("cross", gap_c, fv, inv, bb, ba, q))
                            if side > 0 and q.bid is not None and q.bid_size > 0 and (
                                    q.bid > q.ask_limit - e2 or q.bid_limit > q.ask_limit - e2
                                    or (q.ask is not None and q.bid > q.ask - e2)):
                                bad.append(("long gap", gap_c, fv, inv, bb, ba, q))
                            if side < 0 and q.ask is not None and q.ask_size > 0 and (
                                    q.ask < q.bid_limit + e2 or q.ask_limit < q.bid_limit + e2
                                    or (q.bid is not None and q.ask < q.bid + e2)):
                                bad.append(("short gap", gap_c, fv, inv, bb, ba, q))
check(f"{n} quotes: our bid never meets our ask; the adding side stays >= 2 x min_edge behind every reducing "
      f"price that may rest (wanted and keep limits)", not bad, bad[:3])

print("--- A: decide gates")


def abot(**kw2):
    return bot(**{"reduce_from_book": True, **kw2})


a, b = abot()
now = time.monotonic()
b.ex["21"].ref_jump_at = -1e9
qa = dec(b, 300, fv=0.57, book_fv=0.52, now_m=now)
a0, b0 = bot()
q0 = dec(b0, 300, fv=0.57, book_fv=0.52, now_m=now)
check("decide long 300, blend 0.57 / book 0.52: A lowers the ask below the blend's, bid capped (flag off: blend)",
      qa.ask < q0.ask and qa.ask >= 0.52 and qa.bid <= qa.ask_limit - 0.02 + 1e-9 and q0.ask >= 0.57, (qa, q0))
b.ex["21"].ref_jump_at = now - 30
qp = dec(b, 300, fv=0.57, book_fv=0.52, now_m=now)
check("30 s after a Polymarket jump: A paused -> flag-off quote", qp == q0, (qp, q0))
b.ex["21"].ref_jump_at = now - 121
check("121 s after it: A back on", dec(b, 300, fv=0.57, book_fv=0.52, now_m=now) == qa)
b.cfg.reduce_from_book_pause_s = 0.0
b.ex["21"].ref_jump_at = now - 1
check("pause 0 s: on straight after a jump", dec(b, 300, fv=0.57, book_fv=0.52, now_m=now) == qa)
b.cfg.reduce_from_book_pause_s = 120.0
b.ex["21"].ref_jump_at = -1e9
b.refs = SimpleNamespace(version=b.ref_version_seen + 1, last_moves={"Utah Senate|Republican": 0.04,
                                                                     "Ohio Senate|Republican": 0.01})
b.reference_jump_guard(now)
check("reference_jump_guard records the jump time on the market that moved >= ref_jump_threshold only",
      b.ex["21"].ref_jump_at == now and b.ex["11"].ref_jump_at == -1e9, (b.ex["21"].ref_jump_at, b.ex["11"].ref_jump_at))
b.ex["21"].cooldown_until = 0.0                             # (the jump guard's own 60 s pull is not under test)
check("...and A is then paused there", dec(b, 300, fv=0.57, book_fv=0.52, now_m=now + 61) == dec(
    b0, 300, fv=0.57, book_fv=0.52, now_m=now + 61))
b.ex["21"].ref_jump_at = -1e9
check("book_fv None: off", dec(b, 300, fv=0.57, book_fv=None, now_m=now) == dec(b0, 300, fv=0.57, book_fv=None,
                                                                                  now_m=now))
b.ref_only, b0.ref_only = {"21"}, {"21"}
check("ref-only market: off", dec(b, 300, fv=0.57, book_fv=0.52, now_m=now) == dec(b0, 300, fv=0.57, book_fv=0.52,
                                                                                     now_m=now))
b.ref_only, b0.ref_only = set(), set()
b.cfg.headline_races = b0.cfg.headline_races = ("Utah Senate",)
check("headline market: off by default (staging gate)",
      dec(b, 300, fv=0.57, book_fv=0.52, now_m=now) == dec(b0, 300, fv=0.57, book_fv=0.52, now_m=now))
b.cfg.reduce_from_book_headline = True
check("...on with reduce_from_book_headline", dec(b, 300, fv=0.57, book_fv=0.52, now_m=now) == qa)
b.cfg.headline_races = b0.cfg.headline_races = ("U.S. House", "U.S. Senate")
qs = dec(b, -300, fv=0.47, book_fv=0.52, now_m=now)
qs0 = dec(b0, -300, fv=0.47, book_fv=0.52, now_m=now)
check("decide short mirror: bid raised toward the book, ask capped 2 x min_edge above the reducing bid",
      qs.bid > qs0.bid and qs.bid <= 0.52 and qs.ask >= qs.bid_limit + 0.02 - 1e-9, (qs, qs0))

print("--- flag off = identical quotes on a grid (against the base commit's compute_quote / kelly_position)")
try:
    src = subprocess.run(["git", "show", f"{BASE_COMMIT}:mm_bot.py"], cwd=os.path.dirname(HERE),
                         capture_output=True, text=True, check=True).stdout
    path = os.path.join(tempfile.mkdtemp(), "mm_bot_base.py")
    with open(path, "w") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("mm_bot_base", path)
    OLD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(OLD)
except Exception as exc:                                   # no git history (e.g. a copied tree): say so, don't fail
    OLD = None
    print(f"SKIP base-commit comparison ({exc.__class__.__name__})")
if OLD is not None:
    oc = OLD.Config()
    diffs, n = [], 0
    for fv in (0.03, 0.12, 0.35, 0.50, 0.66, 0.91, 0.97):
        for inv in (-3000, -400, -1, 0, 1, 250, 4000):
            for bb, ba in ((fv - 0.04, fv + 0.04), (fv - 0.01, fv + 0.005), (None, fv + 0.03), (fv - 0.03, None),
                           (None, None)):
                bb = None if bb is None or bb <= 0 else M.floor_tick(bb)
                ba = None if ba is None or ba >= 1 else M.ceil_tick(ba)
                for kp in (None, fv + 0.05, fv - 0.05):
                    for extra in ({}, {"min_edge": 0.02, "age_hours": 30.0}, {"unload_side": "ask", "unload_edge": 0.005,
                                                                            "unload_size": 300}):
                        args = (fv, inv, inv, bb, ba)
                        kwq = dict(kelly_p=kp, bankroll=BANK, order_size=300, **extra)
                        new, old = M.compute_quote(*args, c, **kwq), OLD.compute_quote(*args, oc, **kwq)
                        n += 1
                        if vars(new) != vars(old):
                            diffs.append((args, extra, new, old))
                        if kp is not None:
                            for yes in (True, False):
                                n += 1
                                if M.kelly_position(kp, fv, BANK, c, yes) != OLD.kelly_position(kp, fv, BANK, oc, yes):
                                    diffs.append(("kelly", kp, fv, yes))
    check(f"{n} compute_quote / kelly_position calls with both flags off: identical to {BASE_COMMIT}", not diffs,
          diffs[:2])
seen = []
real = M.compute_quote


def spy(*a2, **k2):
    seen.append(k2.get("reduce_fv"))
    return real(*a2, **k2)


M.compute_quote = spy
try:
    _, boff = bot()
    for pos in (-800, -1, 0, 1, 800):
        for fv in (0.40, 0.52, 0.60):
            dec(boff, pos, fv=fv, book_fv=0.52)
finally:
    M.compute_quote = real
check("decide with reduce_from_book off never passes a book price to compute_quote", seen and
      all(x is None for x in seen), seen[:5])

print("--- live_sim feeds the jump pause (behind the flag)")
src = open(os.path.join(HERE, "live_sim.py")).read()
body = src[src.index("def bot_strategy"):src.index("def _Clock") if "def _Clock" in src else src.index("class _Clock")]
check("bot_strategy sets ex.ref_jump_at from the sim's Polymarket jump, only with reduce_from_book",
      "if sim.cfg.reduce_from_book and m.cooldown_until >= 0" in body and "ex.ref_jump_at" in body)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
