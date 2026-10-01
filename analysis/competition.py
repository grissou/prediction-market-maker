"""Section 3b: competition at the top of book, from the 2-3 min snapshots (best bid/ask INCLUDE our own quotes)."""
import os, numpy as np, pandas as pd
from load import snapshots
SNAP = os.environ.get("SNAP", "snap")
s = snapshots(); s = s[s.ts >= "2026-10-01T16:05"]
s["spread"] = s.best_ask - s.best_bid
s["fvx"] = s.fair_value.fillna(s.reference)
HEAD = {"Dem U.S. House", "Rep U.S. House", "Dem U.S. Senate", "Rep U.S. Senate"}
s["cls"] = np.where(s.label.isin(HEAD), "headline", "race")
tick = 0.005
def rel(our, best, sign):
    d = np.round(sign * (best - our) / tick)
    return pd.cut(d, [-99, -0.5, 0.5, 1.5, 2.5, 99], labels=["behind?", "at best", "1 tick inside", "2 ticks", "3+ ticks"])
b = s[s.our_bid.notna()]; a = s[s.our_ask.notna()]
rb = rel(b.our_bid, b.best_bid, 1); ra = rel(a.our_ask, a.best_ask, -1)
print("bid vs best:", (rb.value_counts(normalize=True).round(3)).to_dict(), "n", len(b))
print("ask vs best:", (ra.value_counts(normalize=True).round(3)).to_dict(), "n", len(a))
both = pd.concat([pd.DataFrame({"label": b.label, "cls": b.cls, "ts": b.ts, "r": rb}), pd.DataFrame({"label": a.label, "cls": a.cls, "ts": a.ts, "r": ra})])
both["half"] = both.ts.dt.floor("h").dt.strftime("%H")
print((both.groupby(["half"]).r.value_counts(normalize=True).unstack().round(2)).to_string())
print((both.groupby(["cls"]).r.value_counts(normalize=True).unstack().round(2)).to_string())
# spread width over time, markets with two-sided books
t = s[s.best_bid.notna() & s.best_ask.notna()].copy(); t["h"] = t.ts.dt.floor("h").dt.strftime("%H")
print("median spread (c) by hour/class:"); print((100 * t.groupby(["h", "cls"]).spread.median()).unstack().round(2).to_string())
print("share of 2-sided books with spread<=1c by hour:", (t.groupby("h").spread.apply(lambda x: (x <= 0.0101).mean())).round(2).to_dict())
# contested = we quote and are not alone at best, or best one tick inside us often
both["undercut"] = both.r.isin(["1 tick inside", "2 ticks", "3+ ticks"])
cm = both.groupby("label").agg(n=("r", "size"), undercut=("undercut", "mean"), one_tick=("r", lambda x: (x == "1 tick inside").mean()))
sp = t.groupby("label").spread.median().rename("med_spread")
cm = cm.join(sp)
cm.to_csv(os.path.join(SNAP, "contested.csv"))
q = cm[cm.n >= 10]
print("markets quoted >=10 snapshots:", len(q), "| undercut >50% of time:", (q.undercut > .5).sum(), "| never undercut:", (q.undercut == 0).sum())
print(q.sort_values("undercut", ascending=False).head(12).round(2).to_string())
print("uncontested examples:", q[q.undercut == 0].sort_values("n", ascending=False).head(10).index.tolist())
# other traders' floors: where best is NOT ours, distance of best other bid/ask from fair value (c)
ob = s[s.fvx.notna() & s.best_bid.notna() & ~np.isclose(s.best_bid, s.our_bid.fillna(-1))]
oa = s[s.fvx.notna() & s.best_ask.notna() & ~np.isclose(s.best_ask, s.our_ask.fillna(-1))]
eb = 100 * (ob.fvx - ob.best_bid); ea = 100 * (oa.best_ask - oa.fvx)
for nm, e, x in (("bid", eb, ob), ("ask", ea, oa)):
    print(f"other best {nm} edge vs our fv (c): quantiles", e.quantile([.1, .25, .5, .75, .9]).round(2).to_dict(),
          "| headline median", round(e[x.cls == "headline"].median(), 2), "| share <1c", round((e < 1).mean(), 2), "| share crossing fv", round((e < 0).mean(), 2))
