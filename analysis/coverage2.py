"""§4 Coverage: markets priced per hour and why the rest were unpriced (from snapshots; the journal logs no
reasons). Unpriced = fair_value NULL. Classes: one-sided/empty book; spread > 30c; no Polymarket reference;
|book mid - Polymarket| > 3c; else 'R5-eligible' (two-sided, <=30c, reference within 3c: the new code's thin-book
rule would price it if the book is downloaded and verified and Polymarket is liquid).
Also: priced count vs our order activity (quote changes) per hour, and the post-deploy ramp.
Run: python3 analysis/coverage2.py"""
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
H = defaultdict(lambda: defaultdict(float))
for t in T:
    h = H[int(t // 3600)]
    h["n"] += 1
    for lab, r in snap[t].items():
        if r["fv"] is not None:
            h["priced"] += 1
            if r["ob"] is not None or r["oa"] is not None:
                h["quoted"] += 1
            continue
        if abs(r["pos"]) >= 1:
            h["held_unpriced"] += 1
        bb, ba = r["bb"], r["ba"]
        if bb is None or ba is None or ba <= bb:
            h["onesided"] += 1
        elif ba - bb > 0.30:
            h["wide"] += 1
        elif r["ref"] is None:
            h["noref"] += 1
        elif abs((bb + ba) / 2 - r["ref"]) > 0.03:
            h["gap3"] += 1
        else:
            h["r5"] += 1
            h["r5_spread"] += ba - bb
sm = defaultdict(list)
for s in d.summaries():
    sm[int(s["t"] // 3600)].append(s)
prev, ch = {}, defaultdict(int)
for q in d.quotes():
    p = prev.get(q["label"])
    if p:
        ch[int(q["t"] // 3600)] += (p["bid"] != q["bid"]) + (p["ask"] != q["ask"])
    prev[q["label"]] = q
rows = []
for hk in sorted(H):
    h = H[hk]; n = h["n"]
    s = sm.get(hk, [])
    rows.append((d.hh(hk * 3600), int(n), "%.0f" % (h["priced"] / n), "%.0f" % (sum(x["priced"] for x in s) / len(s)) if s else "",
                 "%.0f" % (h["quoted"] / n), "%.0f" % (h["r5"] / n), "%.1f" % (100 * h["r5_spread"] / h["r5"]) if h["r5"] else "",
                 "%.0f" % (h["gap3"] / n), "%.0f" % (h["noref"] / n), "%.0f" % (h["onesided"] / n), "%.0f" % (h["wide"] / n),
                 "%.0f" % (h["held_unpriced"] / n), ch[hk]))
print(d.table(rows, ["hour", "snaps", "priced (snap)", "priced (journal)", "quoted", "unpriced R5-eligible", "their spread c",
                     "unpriced gap>3c", "no ref", "one-sided", "spread>30c", "held & unpriced", "quote side changes"]))

# correlation: priced vs order activity (old code, hours with summaries)
xs, ys = [], []
for hk in sorted(sm):
    if d.OPEN <= hk * 3600 < d.DEPLOY - 3600:
        xs.append(ch[hk]); ys.append(sum(x["priced"] for x in sm[hk]) / len(sm[hk]))
n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
c = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** .5
print("\nold code, hourly: corr(priced, quote side changes) = %.2f over %d hours" % (c, n))
print("\nafter the 08:08 deploy (journal summary lines):")
for s in d.summaries():
    if s["t"] >= d.DEPLOY:
        print("  ", d.hh(s["t"], "%H:%M:%S"), "priced", s["priced"], "resting", s["resting"])
