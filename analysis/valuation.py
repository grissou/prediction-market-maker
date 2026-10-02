"""§1 How does the exchange value open positions? Tests mark rules against the account table.

Between consecutive snapshots (2-4 min apart) with NO fills, the change in real equity must equal
sum(position x change in mark) under the exchange's rule. Candidate marks: tournament mid, liquidation
(bid for longs, ask for shorts), best bid, best ask, our fair value, Polymarket reference, and
'mid where two-sided else previous mark'. Reports the residual (RMS) per rule. Positions are the
snapshot `position` column (net YES shares; negative = NO shares).
Also: level test of equity = 100,000 + cash flows from fills + sum(position x mark), at each snapshot.
Run: python3 analysis/valuation.py"""
import math
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
acct = {r[0]: r for r in d.account()}
fills = d.fills()
ft = [f["t"] for f in fills]


def marks(row, prev):
    bb, ba = row["bb"], row["ba"]
    m = d.mid(row)
    pos = row["pos"]
    out = {}
    out["mid"] = m
    out["bid"] = bb
    out["ask"] = ba
    out["liq"] = (bb if pos > 0 else ba)
    out["fv"] = row["fv"]
    out["ref"] = row["ref"]
    # mid when two-sided, else the one side present (exchange 'valuation price' guess)
    out["mid_or_side"] = m if m is not None else (bb if bb is not None else ba)
    return out


def interval_test(t0, t1):
    """sum pos*(mark1-mark0) per rule; None if any held position lacks the mark at either end."""
    res = {}
    rules = ["mid", "bid", "ask", "liq", "fv", "ref", "mid_or_side"]
    for rule in rules:
        tot, ok, missing = 0.0, True, 0.0
        for lab, r1 in snap[t1].items():
            r0 = snap[t0].get(lab)
            if r0 is None or abs(r0["pos"]) < 1:
                continue
            if abs(r1["pos"] - r0["pos"]) > 0.5:
                ok = False; break
            m0, m1 = marks(r0, None)[rule], marks(r1, None)[rule]
            if m0 is None or m1 is None:
                missing += abs(r0["pos"])
                continue
            tot += r0["pos"] * (m1 - m0)
        res[rule] = (tot if ok else None, missing)
    return res


rows = []
for t0, t1 in zip(T, T[1:]):
    if t1 - t0 > 400 or t0 not in acct or t1 not in acct:
        continue
    lo = sum(1 for x in ft if t0 - 5 <= x <= t1 + 5)
    if lo:
        continue
    r = interval_test(t0, t1)
    if r["mid"][0] is None:
        continue
    de = acct[t1][6] - acct[t0][6]
    rows.append((t0, de, r))

print("no-fill intervals with unchanged positions:", len(rows))
rules = ["mid", "bid", "ask", "liq", "fv", "ref", "mid_or_side"]
print("rule        RMS(resid)  mean|dE|  mean|pred|  corr   slope")
for rule in rules:
    xs = [r[2][rule][0] for r in rows]
    ys = [r[1] for r in rows]
    res = [y - x for x, y in zip(xs, ys)]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)); sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    corr = sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")
    print("%-11s %9.1f %9.1f %10.1f %6.3f %6.2f" % (rule, math.sqrt(sum(e * e for e in res) / n),
          sum(abs(y) for y in ys) / n, sum(abs(x) for x in xs) / n, corr, sxy / sxx if sxx else float("nan")))
# exact-match share per rule (|resid| < 2 SUSQies)
for rule in rules:
    k = sum(1 for r in rows if abs(r[1] - r[2][rule][0]) < 2)
    print("rule %-11s matches within 2: %d/%d" % (rule, k, len(rows)))
big = sorted(rows, key=lambda r: -abs(r[1]))[:8]
print("\nlargest equity moves on no-fill intervals: t, dE, pred(mid), pred(liq), pred(bid), pred(mid_or_side)")
for r in big:
    print(d.hh(r[0], "%d %H:%M"), round(r[1]), *[round(r[2][k][0]) for k in ("mid", "liq", "bid", "mid_or_side")])
