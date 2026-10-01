"""Section 5: tournament mid vs Polymarket reference. Episodes = consecutive snapshots with |mid-ref| >= threshold
(two-sided book, spread <= 5c). Also party-sum anomalies within races."""
import os, numpy as np, pandas as pd
from load import snapshots
SNAP = os.environ.get("SNAP", "snap")
s = snapshots(); s = s[s.ts >= "2026-10-01T16:05"].sort_values(["label", "ts"]).copy()
s["mid"] = (s.best_bid + s.best_ask) / 2
ok = s.best_bid.notna() & s.best_ask.notna() & ((s.best_ask - s.best_bid) <= 0.05) & s.reference.notna()
s["gap"] = np.where(ok, s.mid - s.reference, np.nan)
g = s.gap.abs()
print("snapshot-market obs:", ok.sum(), "| |gap| quantiles (c):", (100 * g.quantile([.5, .75, .9, .95, .99])).round(1).to_dict())
print("share |gap|>=3c: %.3f, >=5c: %.3f, >=10c: %.3f" % ((g >= .03).mean(), (g >= .05).mean(), (g >= .10).mean()))
snaps = sorted(s.ts.unique()); idx = {t: i for i, t in enumerate(snaps)}
s["i"] = s.ts.map(idx)
for thr in (0.03, 0.05):
    eps = []
    for lab, x in s.groupby("label"):
        x = x[x.gap.notna()]
        on = x.gap.abs() >= thr
        run = None
        for (_, r), flag in zip(x.iterrows(), on):
            if flag and run is None: run = [r.ts, r.ts, abs(r.gap), r.gap, r.reference, r.mid, r.i]
            elif flag: run[1] = r.ts; run[2] = max(run[2], abs(r.gap))
            elif run is not None:
                eps.append((lab, *run[:4], r.gap, r.reference - run[4], r.mid - run[5], True)); run = None
        if run is not None: eps.append((lab, *run[:4], np.nan, np.nan, np.nan, False))
    e = pd.DataFrame(eps, columns=["label", "start", "end", "max_gap", "gap0", "gap_after", "d_ref", "d_mid", "closed"])
    e["dur_min"] = (e.end - e.start).dt.total_seconds() / 60 + 2.7   # +~1 snapshot interval
    c = e[e.closed]
    who = np.where(c.d_mid.abs() > c.d_ref.abs(), "tournament moved", "Polymarket moved")
    print(f"\n|gap|>={thr*100:.0f}c: episodes {len(e)} in {e.label.nunique()} markets; per hour {len(e)/4.2:.1f}; "
          f"closed within data {c.shape[0]} ({c.shape[0]/max(len(e),1):.0%}); median dur {e.dur_min.median():.1f} min (p75 {e.dur_min.quantile(.75):.1f}, max {e.dur_min.max():.0f}); "
          f"median max gap {100*e.max_gap.median():.1f}c; closed by: {pd.Series(who).value_counts().to_dict()}")
    print("  largest:", e.sort_values("max_gap", ascending=False).head(6)[["label", "start", "max_gap", "dur_min", "closed"]].assign(start=lambda d: d.start.dt.strftime("%H:%M"), max_gap=lambda d: (100*d.max_gap).round(1), dur_min=lambda d: d.dur_min.round(0)).values.tolist())
    print("  gap sign: tournament above PM %.0f%%; Dem-contract gaps median %.1fc, Rep %.1fc" % (100 * (e.gap0 > 0).mean(), 100 * e[e.label.str.startswith('Dem')].gap0.median(), 100 * e[e.label.str.startswith('Rep')].gap0.median()))
# persistent bias by party
print("\nmean gap (c) by party:", (100 * s.groupby("party").gap.mean()).round(2).to_dict(), "| by price bucket:", (100 * s.groupby(pd.cut(s.reference, [0, .1, .3, .7, .9, 1])).gap.mean()).round(2).to_dict())
# party sums of mids within races (all members two-sided)
r = s[s.best_bid.notna() & s.best_ask.notna()].groupby(["ts", "race"]).agg(n=("mid", "size"), bidsum=("best_bid", "sum"), asksum=("best_ask", "sum"), mids=("mid", "sum"))
r = r[r.n >= 2]
print("race party sums: bid-sum>1.00 in %.1f%% of race-snapshots (>1.03: %.1f%%); ask-sum<1.00 in %.1f%% (<0.97: %.1f%%)" % (
    100 * (r.bidsum > 1.0001).mean(), 100 * (r.bidsum > 1.03).mean(), 100 * (r.asksum < 0.9999).mean(), 100 * (r.asksum < 0.97).mean()))
