"""§5 Edge at quote and markouts per hour, per group and per market; adverse selection after Polymarket moves.
Edge = sgn x (fv at quote - YES price). Markouts (c/share, + = good for us):
  fv path: the bot's fv from the journal quote lines (last logged fv <= t+h; lines are logged on every quote
  change, so this is the best 1-min resolution we have), h = 1, 5, 15, 60 min;
  mid path: tournament mid from the first snapshot >= t+h (snapshots are 2-4 min apart), h = 5, 15, 60.
Polymarket moves: consecutive journal reference values for a market differing by >= 1c. For each fill we find
the latest such move in the previous 15 min; 'stale side' = we bought after a down-move or sold after an up-move.
Run: python3 analysis/markout2.py"""
import bisect
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
S = d.Series(snap, T)
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN and f["sgn"]]
qs = d.quotes()
fvp = defaultdict(lambda: ([], []))
refmoves = defaultdict(list)       # label -> [(t, move)]
lastref = {}
for q in qs:
    if q["fv"] is not None:
        a, b = fvp[q["label"]]; a.append(q["t"]); b.append(q["fv"])
    if q["ref"] is not None:
        p = lastref.get(q["label"])
        if p is not None and abs(q["ref"] - p) >= 0.01 - 1e-9:
            refmoves[q["label"]].append((q["t"], q["ref"] - p))
        lastref[q["label"]] = q["ref"]


def fv_at(lab, t):
    a, b = fvp.get(lab, ([], []))
    i = bisect.bisect_right(a, t) - 1
    return b[i] if i >= 0 else None


HZ = (60, 300, 900, 3600)
for f in fills:
    f["mo_fv"] = {}
    f["mo_mid"] = {}
    for h in HZ:
        v = fv_at(f["label"], f["t"] + h)
        if v is not None and f["t"] + h <= T[-1]:
            f["mo_fv"][h] = f["sgn"] * (v - f["yes_px"])
        if h >= 300:
            m = d.mid(S.at(f["label"], f["t"] + h, tol=420))
            if m is not None:
                f["mo_mid"][h] = f["sgn"] * (m - f["yes_px"])
    f["edge"] = f["sgn"] * (f["fvq"] - f["yes_px"]) if f["fvq"] is not None else None
    f["grp"] = "headline" if "U.S." in f["label"] else ("race >=500" if f["qty"] >= 500 else "race <500")


def agg(fl):
    out = []
    sh = sum(f["qty"] for f in fl)
    e = [f for f in fl if f["edge"] is not None]
    out.append(len(fl)); out.append("%.0f" % (sh / 1e3) + "k")
    out.append("%+.2f" % (100 * sum(f["qty"] * f["edge"] for f in e) / sum(f["qty"] for f in e)) if e else "")
    for h in HZ:
        g = [f for f in fl if h in f["mo_fv"]]
        out.append("%+.2f" % (100 * sum(f["qty"] * f["mo_fv"][h] for f in g) / sum(f["qty"] for f in g)) if g else "")
    for h in (300, 900, 3600):
        g = [f for f in fl if h in f["mo_mid"]]
        out.append("%+.2f" % (100 * sum(f["qty"] * f["mo_mid"][h] for f in g) / sum(f["qty"] for f in g)) if g else "")
    g = [f for f in fl if 3600 in f["mo_mid"] or 900 in f["mo_mid"]]
    out.append("%+.0f" % sum(f["qty"] * f["mo_mid"].get(3600, f["mo_mid"].get(900)) for f in g))
    return out


HDR = ["fills", "shares", "edge c", "fv+1m", "fv+5m", "fv+15m", "fv+60m", "mid+5m", "mid+15m", "mid+60m", "P&L mid+60 (or +15)"]
print("## by hour\n")
byh = defaultdict(list)
for f in fills:
    byh[int(f["t"] // 3600)].append(f)
print(d.table([[d.hh(h * 3600)] + agg(byh[h]) for h in sorted(byh)], ["hour"] + HDR))
print("\n## by group\n")
rows = []
for g in ("headline", "race >=500", "race <500"):
    rows.append([g] + agg([f for f in fills if f["grp"] == g]))
rows.append(["all"] + agg(fills))
for name, lo, hi in (("edge <= -1c", -9, -0.01), ("-1..0", -0.01, 0), ("0..1", 0, 0.01), ("1..3", 0.01, 0.03), ("> 3c (sweeps)", 0.03, 9)):
    rows.append(["edge " + name] + agg([f for f in fills if f["edge"] is not None and lo < f["edge"] <= hi]))
rows.append(["inferred side"] + agg([f for f in fills if f["inferred"]]))
rows.append(["recorded side"] + agg([f for f in fills if not f["inferred"] and not f.get("recon")]))
print(d.table(rows, ["group"] + HDR))

print("\n## worst and best markets by P&L at mid +60 (or +15)\n")
bym = defaultdict(list)
for f in fills:
    bym[f["label"]].append(f)
res = sorted(((float(agg(v)[-1]), k, v) for k, v in bym.items()))
print(d.table([[k] + agg(v) for _, k, v in res[:10] + res[-6:]], ["market"] + HDR))

print("\n## fills after a Polymarket move (>=1c, journal reference, previous 15 min)\n")
grp = defaultdict(list); delays = []
for f in fills:
    mv = [m for m in refmoves.get(f["label"], []) if f["t"] - 900 <= m[0] <= f["t"]]
    if not mv:
        grp["no move in 15 min"].append(f); continue
    t0, m = mv[-1]
    stale = (m < 0 and f["sgn"] > 0) or (m > 0 and f["sgn"] < 0)
    k = ("stale side" if stale else "with the move") + (" <=2 min" if f["t"] - t0 <= 120 else " 2-15 min")
    grp[k].append(f)
    if stale:
        delays.append(f["t"] - t0)
print(d.table([[k] + agg(v) for k, v in sorted(grp.items())], ["fills"] + HDR))
delays.sort()
if delays:
    print("\nstale-side fills: delay after the move p25 %.0f s, median %.0f s, p75 %.0f s (n=%d)" % (
        delays[len(delays) // 4], delays[len(delays) // 2], delays[3 * len(delays) // 4], len(delays)))
nm = sum(len(v) for v in refmoves.values())
hrs = (T[-1] - d.OPEN) / 3600
print("Polymarket moves >=1c seen in the journal: %d (%.1f/h over %d markets)" % (nm, nm / hrs, len(refmoves)))
