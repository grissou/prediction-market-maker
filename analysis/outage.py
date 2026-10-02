"""§3 The 2026-10-02 07:32-07:45 exchange outage: per-minute event counts, quotes resting, fills, account.
Run: python3 analysis/outage.py [start] [end]   (ISO UTC, default 07:25-07:55)"""
import sys
from collections import Counter, defaultdict
import data2 as d

a = d.ts(sys.argv[1]) if len(sys.argv) > 1 else d.ts("2026-10-02T07:25:00Z")
b = d.ts(sys.argv[2]) if len(sys.argv) > 2 else d.ts("2026-10-02T07:55:00Z")
per = defaultdict(Counter)
for t, k, txt in d.events():
    if a <= t <= b:
        per[int(t // 60)][k] += 1
with open(d.J) as fh:
    for line in fh:
        if line[:4] != "2026":
            continue
        t = d.ts(line[:24])
        if a <= t <= b:
            for k, p in (("take", "WARNING TAKE"), ("alert", "ALERT"), ("ro_line", "REDUCE-ONLY"), ("tracking", "Tracking ")):
                if p in line:
                    per[int(t // 60)][k] += 1
fl = [f for f in d.fills_reconciled() if a <= f["t"] <= b]
for f in fl:
    per[int(f["t"] // 60)]["fills"] += 1
sm = {int(x["t"] // 60): x for x in d.summaries() if a <= x["t"] <= b}
kinds = ["unexpected", "cycle_failed", "cancel_fail", "409", "429", "net_err", "take", "fills", "tracking"]
print("min   " + " ".join("%6s" % k[:6] for k in kinds) + "  resting real_eq")
for m in range(int(a // 60), int(b // 60) + 1):
    s = sm.get(m)
    print(d.hh(m * 60, "%H:%M"), " ".join("%6d" % per[m][k] for k in kinds),
          "  %5s %8s" % (s["resting"] if s else "", round(s["real"]) if s else ""))
print("\nfills in window:")
for f in fl:
    print(d.hh(f["t"], "%H:%M:%S"), f["label"], f["side"], f["qty"], round(f["yes_px"], 3) if f["yes_px"] else None,
          "fvq", f["fvq"])
