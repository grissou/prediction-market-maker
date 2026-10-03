"""§7 Before/after the 08:08 deploy: 60 min before (old code, reduce-only) vs the ~6 min after (new code).
Run: python3 analysis/deploy.py"""
from collections import Counter
import data2 as d

snap, T = d.snapshots()
fl = d.fills_reconciled()
raw = d.fills(attribute=False)
for name, a, b in (("old code 07:08-08:08", d.DEPLOY - 3600, d.DEPLOY), ("new code 08:08-08:15", d.DEPLOY, d.DEPLOY + 420)):
    f = [x for x in fl if a <= x["t"] < b and x["sgn"]]
    r = [x for x in raw if a <= x["t"] < b]
    e = [x for x in f if x["fvq"] is not None]
    es = sum(x["qty"] for x in e)
    m = (b - a) / 60
    ss = [s for s in d.summaries() if a <= s["t"] < b]
    sn = [t for t in T if a <= t < b]
    at, tot = 0, 0
    for t in sn:
        for lab, x in snap[t].items():
            if x["ob"] is not None and x["bb"] is not None:
                tot += 1; at += x["ob"] >= x["bb"] - 1e-9
            if x["oa"] is not None and x["ba"] is not None:
                tot += 1; at += x["oa"] <= x["ba"] + 1e-9
    print("%s: fills %d (%.1f/min), shares %.0f, unrecorded by the bot %d of %d, edge %+.2fc (%+.0f), sizes p50 %.0f;"
          " resting avg %.0f, priced %s, RO lines %d/%d, our quoted sides at best %.0f%% over %d snapshots"
          % (name, len(f), len(f) / m, sum(x["qty"] for x in f), sum(1 for x in r if x["side"] == "?"), len(r),
             100 * sum(x["sgn"] * x["qty"] * (x["fvq"] - x["yes_px"]) for x in e) / es if es else 0,
             sum(x["sgn"] * x["qty"] * (x["fvq"] - x["yes_px"]) for x in e), sorted(x["qty"] for x in f)[len(f) // 2] if f else 0,
             sum(s["resting"] for s in ss) / max(1, len(ss)), "%d->%d" % (ss[0]["priced"], ss[-1]["priced"]) if ss else "",
             sum(s["ro"] for s in ss), len(ss), 100 * at / max(1, tot), len(sn)))
    print("   by edge bucket:", Counter("<-1c" if x["sgn"] * (x["fvq"] - x["yes_px"]) < -0.01 else (">3c" if x["sgn"] * (x["fvq"] - x["yes_px"]) > 0.03 else "-1..3c") for x in e))
s = [x for x in d.summaries() if x["t"] >= d.DEPLOY]
print("new code summary lines: worst-case %0.f -> %.0f, correlated risk %.0f -> %.0f, party delta %+.0f -> %+.0f"
      % (s[0]["worst"], s[-1]["worst"], s[0]["risk"] or 0, s[-1]["risk"] or 0, s[0]["delta"], s[-1]["delta"]))
