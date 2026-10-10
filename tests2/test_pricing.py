"""
Offline tests for mmbot2/pricing.py: the four edge formulas (README §4.2), edge_held, race scaling, the liquidity
and wrong-match filters, quarter-Kelly sizes (the README example), and the tilt estimator recovering a known s
from a synthetic cross-section m = c + (1 - s)(r - c). Inputs are mmbot2.state records; no exchange, no network.

Run:  python3 tests2/test_pricing.py      (exit code 0 = all passed)
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mmbot2 import pricing as P                           # noqa: E402
from mmbot2.config import Settings                        # noqa: E402
from mmbot2.state import Book, Market                     # noqa: E402

RESULTS = []
S = Settings()


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def near(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


def market(eid, race, party, state="OH"):
    return Market(eid=eid, label=f"{party[:3]} {race}", race=race, party=party, state=state, close=None)


def book_around(mid, half=0.01, shares=500):
    return Book(bids=[(mid - half, shares)], asks=[(mid + half, shares)])


# --- the four edge formulas, README §4.2 (a 97% favourite trading at 85c, a 2% longshot bid at 15c) ---
check("edge_buy: (p - a) / a = (0.97 - 0.85) / 0.85", near(P.edge_buy(0.97, 0.85), 0.12 / 0.85))
check("edge_short: (b - p) / (1 - b) = (0.15 - 0.02) / 0.85", near(P.edge_short(0.02, 0.15), 0.13 / 0.85))
check("edge_keep_long: (p - b) / b = (0.97 - 0.85) / 0.85", near(P.edge_keep_long(0.97, 0.85), 0.12 / 0.85))
check("edge_keep_short: (a - p) / (1 - a) = (0.15 - 0.02) / 0.85", near(P.edge_keep_short(0.02, 0.15), 0.13 / 0.85))
check("edge_buy is negative above value", P.edge_buy(0.40, 0.45) < 0)
check("edge_short at the top of the grid stays finite", near(P.edge_short(0.5, 0.995), 99.0, 1e-6))

# --- edge_held ---
bk = Book(bids=[(0.85, 100)], asks=[(0.15, 100)])
check("edge_held long: against the best bid", near(P.edge_held(500, 0.97, Book([(0.85, 10)], [])), 0.12 / 0.85))
check("edge_held short: against the best ask", near(P.edge_held(-500, 0.02, Book([], [(0.15, 10)])), 0.13 / 0.85))
check("edge_held: no bid for a long -> None", P.edge_held(500, 0.97, Book([], [(0.9, 10)])) is None)
check("edge_held: no position -> None", P.edge_held(0.4, 0.97, bk) is None)
check("edge_held: no book -> None", P.edge_held(100, 0.5, None) is None)

# --- fair value: key, race scaling, liquidity, wrong matches ---
mk = {"r": market("r", "Ohio Senate", "Republican"), "d": market("d", "Ohio Senate", "Democratic"),
      "x": market("x", "Iowa Governor", "Republican", "IA"), "y": market("y", "Iowa Governor", "Democratic", "IA")}
check("ref_key matches ref_prices.py", P.ref_key(mk["r"]) == "Ohio Senate|Republican")
refs = {"Ohio Senate|Republican": 0.55, "Ohio Senate|Democratic": 0.50,
        "Iowa Governor|Republican": 0.70, "Iowa Governor|Democratic": 0.25}
tight = {k: 0.01 for k in refs}
fv = P.fair_values(mk, refs, tight, {}, S)
check("race scaling: 0.55 / 1.05 and 0.50 / 1.05", near(fv["r"], 0.55 / 1.05) and near(fv["d"], 0.50 / 1.05), fv)
check("race scaling: legs sum to 1", near(fv["x"] + fv["y"], 1.0))
fv = P.fair_values(mk, {k: v for k, v in refs.items() if k != "Ohio Senate|Democratic"}, tight, {}, S)
check("a race with an unpriced leg is left raw", near(fv["r"], 0.55) and "d" not in fv, fv)
wide = dict(tight, **{"Ohio Senate|Democratic": 0.04})
fv = P.fair_values(mk, refs, wide, {}, S)
check("Polymarket spread above 3c: unpriced", "d" not in fv, fv)
check("...but the illiquid leg still scales the race", near(fv["r"], 0.55 / 1.05), fv)
fv = P.fair_values(mk, refs, {k: v for k, v in tight.items() if k != "Iowa Governor|Republican"}, {}, S)
check("no spread known: unpriced", "x" not in fv and "y" in fv, fv)
check("liquid_refs are raw (unscaled)", near(P.liquid_refs(mk, refs, tight, {}, S)["r"], 0.55))
books = {"r": book_around(0.20), "d": book_around(0.80)}     # the book says the opposite: a swapped match
check("wrong match: 35c and 30c from the book -> both dropped",
      set(P.wrong_matches(mk, refs, books, S)) == {"r", "d"} and not {"r", "d"} & set(P.fair_values(mk, refs, tight, books, S)))
books = {"r": book_around(0.50), "d": book_around(0.50)}
fv = P.fair_values(mk, refs, tight, books, S)
check("within 25c of the book: kept", near(fv["r"], 0.55 / 1.05) and not P.wrong_matches(mk, refs, books, S), fv)
thin = {"r": Book(bids=[(0.10, 50)], asks=[(0.90, 50)])}
check("a thin book (under 200 shares) has no price, so no wrong-match test", P.book_price(thin["r"]) is None
      and "r" in P.fair_values(mk, refs, tight, thin, S))
check("book_price walks the depth to 200 shares", P.book_price(Book([(0.50, 100), (0.48, 150)], [(0.53, 300)])) == 0.505)

# --- Kelly sizes ---
n = P.kelly_shares(0.20, 0.14, True, 100000.0, S)
check("README example: p 0.20, buy at 0.14 -> quarter Kelly 1.7% of the account",
      near(n * 0.14 / 100000.0, 0.25 * 0.06 / 0.86, 2e-6) and round(n * 0.14 / 100) == 17, n)
check("capped at 2% of the account", P.kelly_shares(0.90, 0.50, True, 100000.0, S) == 4000.0)
check("an ask buys NO at 1 - price: p 0.10, sell at 0.16 -> 2% cap / 0.84",
      P.kelly_shares(0.10, 0.16, False, 100000.0, S) == float(int(2000 / 0.84)))
check("no edge: the 100-share allowance at 100k", P.kelly_shares(0.20, 0.25, True, 100000.0, S) == 100.0)


# --- the tilt estimator: a synthetic cross-section m = c + (1 - s)(r - c) ---
def cross_section(s_true, races=80, seed=1, headline=False):
    rnd, mk, refs, books = random.Random(seed), {}, {}, {}
    for i in range(races):
        legs = 3 if i % 5 == 0 else 2
        raw = [rnd.uniform(0.02, 1.0) for _ in range(legs)]
        race = "U.S. Senate" if headline and i == 0 else f"Race {i}"
        for j, w in enumerate(raw):
            eid, r, c = f"{i}-{j}", w / sum(raw), 1.0 / legs
            mk[eid] = market(eid, race, f"P{j}")
            refs[eid] = r
            books[eid] = book_around(c + (1 - s_true) * (r - c))
    return mk, refs, books


mk, r, books = cross_section(0.09)
t = P.TiltEstimator()
check("not ready before the first estimate", t.to_dict()["ready"] is False)
est = t.update(mk, books, r, 0.0, S)
check(f"recovers s = 0.09 from {len(mk)} markets", near(est, 0.09, 1e-3), est)
few = dict(list(mk.items())[:40])
check("fewer than 50 markets: None until there is an estimate", P.TiltEstimator().update(few, books, r, 0.0, S) is None)
check("fewer than 50 markets: hold the last estimate", near(t.update(few, books, r, 60.0, S), est))
mk2, r2, books2 = cross_section(0.25)
check("clipped at tilt_max 0.15", P.TiltEstimator().update(mk2, books2, r2, 0.0, S) == S.tilt_max)
t2 = P.TiltEstimator()
t2.update(mk2, books2, r2, 0.0, S)
check("the unclipped reading is still reported (winsorised below 0.25)", 0.15 < t2.to_dict()["raw"] < 0.25, t2.raw)
bad = dict(books, **{e: book_around(0.995 - 0.01) for e in list(mk)[:3]})     # three stale books far from r
check("winsorising: three wild books move s by under 1 point", near(P.TiltEstimator().update(mk, bad, r, 0.0, S),
                                                                    0.09, 0.01))
paused = set(list(mk)[:3])
check("markets paused after a jump are left out", P.TiltEstimator().update(mk, bad, r, 0.0, S, paused) is not None
      and near(P.TiltEstimator().update(mk, bad, r, 0.0, S, paused), 0.09, 1e-3))
mkh, rh, bh = cross_section(0.09, headline=True)
bh.update({e: book_around(0.9) for e, m in mkh.items() if m.race == "U.S. Senate"})
check("headline races are left out", near(P.TiltEstimator().update(mkh, bh, rh, 0.0, S), 0.09, 1e-3))
mk3, r3, books3 = cross_section(0.13)
t = P.TiltEstimator()
t.update(mk, books, r, 0.0, S)
half = t.update(mk3, books3, r3, 30 * 60.0, S)
check("EMA: one half-life later it is half way from 0.09 to 0.13", near(half, 0.11, 1e-3), half)
saved = t.to_dict()
back = P.TiltEstimator.from_dict(saved)
check("to_dict / from_dict keep s and ready", back.ready and near(back.s, saved["s"]) and saved["n"] == len(mk3))
check("after a restore the first update only starts the clock", near(back.update(mk, books, r, 999.0, S), saved["s"]))
check("from_dict of junk: 0, not ready", P.TiltEstimator.from_dict(None).s == 0.0
      and not P.TiltEstimator.from_dict({"s": "x"}).ready)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
