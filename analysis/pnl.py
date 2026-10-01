"""Section 1: where the P&L came from. Run with SNAP=<snapshot dir>; needs f.pkl from attribute.py."""
import os, numpy as np, pandas as pd
from load import snapshots, account
SNAP = os.environ.get("SNAP", "snap")
T = pd.Timestamp("2026-10-01T20:18:54.706Z")

f = pd.read_pickle(os.path.join(SNAP, "f.pkl"))
f = f[(f.filled_at <= T) & (f.our_side != "?")].copy()
s = snapshots(); last = s[s.ts == s.ts.max()].set_index("label")
last["mid"] = (last.best_bid + last.best_ask) / 2
a = account()
api = a.account_value - a.locked_in_orders
print("API value (account_value - locked): min %.0f max %.0f last %.0f" % (api.min(), api.max(), api.iloc[-1]))
print("corr(account_value, locked) = %.4f" % np.corrcoef(a.account_value, a.locked_in_orders)[0, 1])

rows = []
for lab, g in f.groupby("label"):
    b, k = g[g.sgn > 0], g[g.sgn < 0]
    qb, qs = b.qty.sum(), k.qty.sum()
    pb = (b.qty * b.yes_px).sum() / qb if qb else np.nan
    ps = (k.qty * k.yes_px).sum() / qs if qs else np.nan
    closed = min(qb, qs)
    real = closed * (ps - pb) if closed else 0.0
    pos = qb - qs
    cost = pb if pos > 0 else ps
    L = last.loc[lab] if lab in last.index else None
    mid, ref, fv = (L.mid, L.reference, L.fair_value) if L is not None else (np.nan,) * 3
    edge = (g.sgn * g.qty * (g.fv_at_quote - g.yes_px)).sum()
    rows.append(dict(label=lab, fills=len(g), shares=g.qty.sum(), pos=pos, realised=real,
                     unreal_mid=pos * (mid - cost) if pos else 0.0, unreal_ref=pos * (ref - cost) if pos and ref == ref else np.nan,
                     unreal_fv=pos * (fv - cost) if pos and fv == fv else np.nan,
                     edge=edge, mid=mid, ref=ref, cost=cost))
d = pd.DataFrame(rows).set_index("label")
d["ref_minus_mid"] = d.pos * (d.ref - d.mid)
tot = d[["realised", "unreal_mid", "unreal_ref", "unreal_fv", "edge", "ref_minus_mid"]].sum()
print(tot.round(0).to_string())
print("fills", len(f), "shares", f.qty.sum())
print("\nTop marks (unrealised at tournament mid) and reversal if marked at Polymarket:")
d["absu"] = d.unreal_mid.abs()
print(d.sort_values("absu", ascending=False).head(12)[["pos", "cost", "mid", "ref", "unreal_mid", "ref_minus_mid", "realised"]].round(3).to_string())
print("\nTop realised:")
print(d.sort_values("realised").iloc[[0, 1, 2, -3, -2, -1]][["fills", "shares", "realised", "edge"]].round(0).to_string())
d.to_csv(os.path.join(SNAP, "pnl_by_market.csv"))
