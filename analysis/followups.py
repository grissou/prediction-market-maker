"""§10 Follow-ups for the Explorer's ideas #10, #4 and #21. Markout = sgn x (mid at t+60 min - YES price), falling back to
mid +15 min (the data end at 08:14); edge = sgn x (fv at quote - YES price). c/share, share-weighted.
(a) slow-exchange minutes: >= 2 409 batch failures, or any batch that failed with a network error/timeout, in the minute
(b) same-side refill runs: fills on the same side of one market each within W s of the run's first fill (W = 10, 60);
    'sweep' = the whole run within 2 s (one taker), 'walk' = spread over > 2 s (repeated refills)
(c) markout persistence per market (16:00-00:00 vs 00:00-08:14) and the favourite-longshot cells by period
Run: python3 analysis/followups.py"""
from collections import Counter, defaultdict
import data2 as d

snap, T = d.snapshots(); S = d.Series(snap, T)
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN and f["sgn"]]
for f in fills:
    m = d.mid(S.at(f["label"], f["t"] + 3600, tol=420)) or d.mid(S.at(f["label"], f["t"] + 900, tol=420))
    f["mo"] = f["sgn"] * (m - f["yes_px"]) if m is not None else None
    f["edge"] = f["sgn"] * (f["fvq"] - f["yes_px"]) if f["fvq"] is not None else None


def stats(fl):
    sh = sum(f["qty"] for f in fl)
    e = [f for f in fl if f["edge"] is not None]; m = [f for f in fl if f["mo"] is not None]
    es = sum(f["qty"] for f in e); ms = sum(f["qty"] for f in m)
    return [len(fl), "%.0f" % sh, "%+.2f" % (100 * sum(f["qty"] * f["edge"] for f in e) / es) if es else "",
            "%+.2f" % (100 * sum(f["qty"] * f["mo"] for f in m) / ms) if ms else "", "%+.0f" % sum(f["qty"] * f["mo"] for f in m)]


H = ["fills", "shares", "edge c", "markout c", "P&L"]

# (a)
c409, bad = Counter(), set()
for t, k, txt in d.events():
    if k == "batch_fail":
        mi = int(t // 60)
        if "409" in txt:
            c409[mi] += 1
        if "NETWORK" in txt or "timed out" in txt.lower():
            bad.add(mi)
slow = {m for m, n in c409.items() if n >= 2} | bad
minutes = (int(T[-1] // 60) - int(d.OPEN // 60))
sf = [f for f in fills if int(f["t"] // 60) in slow]
nf = [f for f in fills if int(f["t"] // 60) not in slow]
nb = [f for f in fills if int(f["t"] // 60) not in slow and any(int(f["t"] // 60) + k in slow for k in (-2, -1, 1, 2))]
print("## (a) slow-exchange minutes: %d of %d trading minutes (%d with >=2 409s, %d with a network/timeout batch failure)\n"
      % (len(slow), minutes, sum(1 for n in c409.values() if n >= 2), len(bad)))
print(d.table([["slow minutes"] + stats(sf), ["calm minutes"] + stats(nf), ["  of which within 2 min of a slow minute"] + stats(nb)], ["minutes"] + H))

# (b)
print("\n## (b) same-side runs of >= 3 fills\n")
by = defaultdict(list)
for f in fills:
    by[(f["label"], f["sgn"])].append(f)
for W in (10, 60):
    rows = defaultdict(list); nrun = Counter()
    for k, fl in by.items():
        fl.sort(key=lambda f: f["t"])
        i = 0
        while i < len(fl):
            j = i
            while j + 1 < len(fl) and fl[j + 1]["t"] - fl[i]["t"] <= W:
                j += 1
            run = fl[i:j + 1]
            if len(run) >= 3:
                kind = "sweep (<=2 s)" if run[-1]["t"] - run[0]["t"] <= 2 else "walk (>2 s)"
                nrun[kind] += 1
                rows[(kind, "1st")].append(run[0]); rows[(kind, "2nd")].append(run[1]); rows[(kind, "3rd+")].extend(run[2:])
            i = j + 1
    print("window %d s: runs %s" % (W, dict(nrun)))
    print(d.table([[k[0], k[1]] + stats(v) for k, v in sorted(rows.items())], ["run", "fill"] + H))
    print()

# (c)
print("## (c) markout persistence per market (16:00-00:00 vs 00:00-08:14), markets with >= 500 sh in both\n")
MID = d.ts("2026-10-02T00:00:00Z")
pm = defaultdict(lambda: [[0.0, 0.0], [0.0, 0.0]])
for f in fills:
    if f["mo"] is None:
        continue
    h = 0 if f["t"] < MID else 1
    pm[f["label"]][h][0] += f["qty"] * f["mo"]; pm[f["label"]][h][1] += f["qty"]
pairs = [(v[0][0] / v[0][1], v[1][0] / v[1][1], k) for k, v in pm.items() if v[0][1] >= 500 and v[1][1] >= 500]


def ranks(x):
    o = sorted(range(len(x)), key=lambda i: x[i]); r = [0] * len(x)
    for k, i in enumerate(o):
        r[i] = k
    return r


a, b = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
n = len(pairs); ma = sum(a) / n
rho = sum((x - ma) * (y - ma) for x, y in zip(a, b)) / sum((x - ma) ** 2 for x in a)
tab = Counter((p[0] > 0, p[1] > 0) for p in pairs)
print("markets %d; Spearman rho %.2f" % (n, rho))
print(d.table([["first half +", tab[(True, True)], tab[(True, False)]], ["first half -", tab[(False, True)], tab[(False, False)]]],
              ["", "second half +", "second half -"]))
print("\n## favourite-longshot cells by period\n")
P = [("day 16-22", d.OPEN, d.ts("2026-10-01T22:00:00Z")), ("night 22-02", d.ts("2026-10-01T22:00:00Z"), d.ts("2026-10-02T02:00:00Z")),
     ("night 02-08:14", d.ts("2026-10-02T02:00:00Z"), d.DEPLOY + 600)]
cells = [("bids, fv < 0.20", lambda f: f["sgn"] > 0 and f["fvq"] < 0.20), ("asks, fv > 0.80", lambda f: f["sgn"] < 0 and f["fvq"] > 0.80),
         ("asks, fv < 0.20", lambda f: f["sgn"] < 0 and f["fvq"] < 0.20), ("bids, fv > 0.80", lambda f: f["sgn"] > 0 and f["fvq"] > 0.80)]
rows = []
for cn, fn in cells:
    for pn, lo, hi in P:
        rows.append([cn, pn] + stats([f for f in fills if f["fvq"] is not None and fn(f) and lo <= f["t"] < hi]))
print(d.table(rows, ["cell", "period"] + H))
