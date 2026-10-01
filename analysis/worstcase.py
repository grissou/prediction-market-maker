"""Section 2: what drives the worst-case settlement loss (same formula as mm_bot.worst_case_loss)."""
import os, json, numpy as np, pandas as pd
from load import snapshots, account
SNAP = os.environ.get("SNAP", "snap")

def wcl(legs):
    value = sum(x * p if x > 0 else -x * (1 - p) for x, p in legs)
    sc = [[True], [False]] if len(legs) == 1 else [[j == i for j in range(len(legs))] for i in range(len(legs))]
    pay = lambda w: sum((x if wi else 0) if x > 0 else (0 if wi else -x) for (x, _), wi in zip(legs, w))
    return max(0.0, value - min(pay(s) for s in sc))

s = snapshots()
def race_table(ts):
    g = s[s.ts == ts].copy()
    g["px"] = g.fair_value.fillna(g.reference).fillna((g.best_bid + g.best_ask) / 2)
    rows = []
    for race, r in g.groupby("race"):
        legs = [(x, p) for x, p in zip(r.position, r.px)]
        if not any(x for x, _ in legs):
            continue
        # legs of a race are mutually exclusive only for races (not single markets) - mirror bot: all labels in a race
        rows.append((race, wcl(legs), sum(abs(x) for x, _ in legs), ", ".join(f"{p[:3]} {x:+.0f}@{q:.2f}" for p, x, q in zip(r.party, r.position, r.px) if x)))
    return pd.DataFrame(rows, columns=["race", "worst", "gross", "legs"]).sort_values("worst", ascending=False)

a = account()
for ts in [a.ts.iloc[i] for i in (45, 75, -1)]:
    t = race_table(ts)
    print(ts, "recomputed worst %.0f (bot %.0f), races with positions %d, top10 share %.0f%%" % (
        t.worst.sum(), a.set_index("ts").worst_case_loss[ts], len(t), 100 * t.worst.head(10).sum() / t.worst.sum()))
t = race_table(a.ts.iloc[-1])
print(t.head(15).round(0).to_string(index=False))
print("races by worst bucket:", pd.cut(t.worst, [0, 50, 100, 250, 500, 1000, 5000]).value_counts().sort_index().to_dict())
# simple risk numbers at 20:18: independent-race settlement sd and a national-swing scenario
g = s[s.ts == a.ts.iloc[-1]].copy(); g["px"] = g.fair_value.fillna(g.reference).fillna((g.best_bid + g.best_ask) / 2)
g = g[g.position != 0]
sd = np.sqrt(((g.position ** 2) * g.px * (1 - g.px)).sum())  # ignores within-race correlation
print("settlement P&L sd (independent legs, approx): %.0f" % sd)
for shift in (0.05, 0.10):
    d = np.where(g.party == "Dem", shift, np.where(g.party == "Rep", -shift, 0))
    print("Dem swing +%.0fc on all prices: P&L %+.0f ; Rep swing: %+.0f" % (100 * shift, (g.position * d).sum(), -(g.position * d).sum()))
w = a.set_index("ts").worst_case_loss
w2 = w[w.index >= "2026-10-01T18:00"]
slope = np.polyfit((w2.index - w2.index[0]).total_seconds() / 3600, w2.values, 1)[0]
real = (a.account_value - a.locked_in_orders).iloc[-1]
print("worst slope 18:00-20:18: %.0f/h; real equity %.0f -> 30%% cap %.0f, hours to cap %.1f; bot's own cap (inflated equity) %.0f" % (
    slope, real, 0.3 * real, (0.3 * real - w.iloc[-1]) / slope, 0.3 * a.account_value.iloc[-1]))
