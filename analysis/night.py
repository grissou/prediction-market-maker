"""§2 What reduce-only cost overnight. Splits 22:48-08:08 into time spent in reduce-only vs not (from the summary
lines' '-> REDUCE-ONLY' flag; each line's state holds until the next line), and compares rates with the
19:00-22:48 evening baseline (after the opening rush). Exchange-degraded hours (04:20-06:30, timeouts/500s)
and the 07:32-07:46 outage are reported separately.
Run: python3 analysis/night.py"""
import bisect
from collections import defaultdict
import data2 as d

snap, T = d.snapshots(); S = d.Series(snap, T)
sums = d.summaries()
st = [s["t"] for s in sums]
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN and f["sgn"]]
DEG0, DEG1 = d.ts("2026-10-02T04:20:00Z"), d.ts("2026-10-02T06:30:00Z")


def state(t):
    i = bisect.bisect_right(st, t) - 1
    return sums[i] if i >= 0 else None


def bucket(t):
    if t < d.ts("2026-10-01T19:00:00Z"):
        return None
    if t < d.FIRST_RO:
        return "evening baseline 19:00-22:48"
    if t >= d.DEPLOY:
        return None
    if d.OUT0 <= t < d.OUT1:
        return "outage 07:32-07:46"
    s = state(t)
    deg = " (exchange degraded 04:20-06:30)" if DEG0 <= t < DEG1 else ""
    return ("night, reduce-only" if s and s["ro"] else "night, normal") + deg


dur = defaultdict(float); rest = defaultdict(float)
for a, b in zip(sums, sums[1:]):
    k = bucket(a["t"])
    if k:
        w = min(b["t"], d.DEPLOY) - a["t"]
        if w > 0:
            dur[k] += w; rest[k] += w * a["resting"]
agg = defaultdict(lambda: defaultdict(float))
for f in fills:
    k = bucket(f["t"])
    if not k:
        continue
    g = agg[k]
    g["n"] += 1; g["sh"] += f["qty"]
    if f["fvq"] is not None:
        g["edge"] += f["sgn"] * f["qty"] * (f["fvq"] - f["yes_px"]); g["esh"] += f["qty"]
    m = d.mid(S.at(f["label"], f["t"] + 3600, tol=420)) or d.mid(S.at(f["label"], f["t"] + 900, tol=420)) or d.mid(S.at(f["label"], T[-1] - 1, tol=600))
    if m is not None:
        g["p60"] += f["sgn"] * f["qty"] * (m - f["yes_px"])
rows = []
for k in sorted(dur, key=lambda k: -dur[k]):
    h = dur[k] / 3600; g = agg[k]
    rows.append((k, "%.1f" % h, "%.0f" % (rest[k] / dur[k]), "%.0f" % (g["n"] / h), "%.1f" % (g["sh"] / h / 1e3),
                 "%+.2f" % (100 * g["edge"] / g["esh"]) if g["esh"] else "", "%+.0f" % (g["edge"] / h), "%+.0f" % (g["p60"] / h)))
print(d.table(rows, ["period", "hours", "avg resting", "fills/h", "k sh/h", "edge c", "edge/h", "P&L mid+60/h"]))
base = agg["evening baseline 19:00-22:48"]; bh = dur["evening baseline 19:00-22:48"] / 3600
ro_h = sum(v for k, v in dur.items() if "reduce-only" in k) / 3600
ro_p = sum(agg[k]["p60"] for k in dur if "reduce-only" in k); ro_e = sum(agg[k]["edge"] for k in dur if "reduce-only" in k)
print("\nreduce-only hours %.1f; forgone vs the evening baseline: P&L at mid+60 %.0f, edge %.0f"
      % (ro_h, ro_h * base["p60"] / bh - ro_p, ro_h * base["edge"] / bh - ro_e))
