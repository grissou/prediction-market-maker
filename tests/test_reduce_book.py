"""
Offline tests for Package 5 B (kelly_edge_cap, kelly_max_market_frac, headline_position_frac). Package 5 A
(reduce_from_book: the reducing side priced from the tournament book) was removed on simplify, never enabled live;
its checks and the twin against the base commit went with it. No network.

Run:  python tests/test_reduce_book.py      (exit code 0 = all passed)
"""
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
check("defaults: kelly_edge_cap 0 (off)", c.kelly_edge_cap == 0.0, c.kelly_edge_cap)
check("existing B limits unchanged in code: kelly_max_market_frac 0.02, headline_position_frac 0.10",
      (c.kelly_max_market_frac, c.headline_position_frac) == (0.02, 0.10))
good, bad = M.validate_overrides({"kelly_edge_cap": 0.015}, c)
check("kelly_edge_cap is live-overridable", len(good) == 1 and not bad, bad)

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
q2 = M.quoting.compute_quote(0.55, 0, 0, None, None, c, **big)
q1 = M.quoting.compute_quote(0.55, 0, 0, None, None, replace(c, kelly_max_market_frac=0.01), **big)
check("flat, huge planned size: bid size = the Kelly long limit; 0.01 = half of 0.02 (+-1 share rounding)",
      q2.bid == q1.bid and abs(q1.bid_size - q2.bid_size / 2) <= 1 and q1.bid_size > 0, (q1, q2))
q2 = M.quoting.compute_quote(0.55, 1500, 1500, None, None, c, **big)
q1 = M.quoting.compute_quote(0.55, 1500, 1500, None, None, replace(c, kelly_max_market_frac=0.01), **big)
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

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
