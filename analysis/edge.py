"""Section 3a: edge and markout per market class and over time, with markouts measured properly
(fills.csv's fv_after is the fair value when the fill was *detected*, usually the same cycle, so its
markout is ~0 by construction). Here: bot fair value / Polymarket ref / tournament mid from the
snapshots at +5, +15, +60 min after each fill."""
import os, numpy as np, pandas as pd
from load import snapshots
SNAP = os.environ.get("SNAP", "snap")
f = pd.read_pickle(os.path.join(SNAP, "f.pkl")); f = f[f.our_side != "?"].copy()
s = snapshots().sort_values("ts"); s["mid"] = (s.best_bid + s.best_ask) / 2
s["fvx"] = s.fair_value.fillna(s.reference).fillna(s.mid)
f = f.sort_values("filled_at")
for h in (5, 15, 60):
    f["t"] = f.filled_at + pd.Timedelta(minutes=h)
    m = pd.merge_asof(f.sort_values("t"), s[["ts", "label", "fvx", "reference", "mid"]].rename(columns={"ts": "t"}),
                      on="t", by="label", direction="forward", tolerance=pd.Timedelta(minutes=10))
    m = m.set_index("fill_id")
    for c in ("fvx", "reference", "mid"):
        f[f"mk{h}_{c}"] = f.fill_id.map(m[c])
f.drop(columns="t", inplace=True)
HEAD = {"Dem U.S. House", "Rep U.S. House", "Dem U.S. Senate", "Rep U.S. Senate"}
f["cls"] = np.where(f.label.isin(HEAD), "headline", np.where(f.qty >= 500, "busy(>=500)", "small(<500)"))
f["edge_c"] = 100 * f.sgn * (f.fv_at_quote - f.yes_px)
def agg(g):
    q = g.qty.sum(); out = {"fills": len(g), "shares": q, "edge_c": (g.qty * g.edge_c).sum() / q}
    for h in (5, 15, 60):
        for c in ("fvx", "mid"):
            v = g[f"mk{h}_{c}"]; ok = v.notna()
            out[f"mk{h}_{c}"] = 100 * (g.sgn[ok] * g.qty[ok] * (v[ok] - g.fv_at_quote[ok])).sum() / max(g.qty[ok].sum(), 1)
    out["pnl60_mid"] = (g.sgn * g.qty * (g.mk60_mid - g.yes_px)).sum()
    return pd.Series(out)
pd.set_option("display.width", 200)
print("ALL"); print(agg(f).round(2).to_dict())
print(f.groupby("cls").apply(agg).round(2).to_string())
f["hour"] = f.filled_at.dt.floor("30min").dt.strftime("%H:%M")
print(f.groupby("hour").apply(agg)[["fills", "shares", "edge_c", "mk5_fvx", "mk15_fvx", "mk60_fvx", "mk15_mid", "pnl60_mid"]].round(2).to_string())
print("inferred vs matched:"); print(f.groupby("inferred").apply(agg)[["fills", "shares", "edge_c", "mk15_fvx", "mk60_mid", "pnl60_mid"]].round(2).to_string())
bym = f.groupby("label").apply(agg)
bym.to_csv(os.path.join(SNAP, "edge_by_market.csv"))
print("best/worst markets by pnl60_mid:")
print(bym.sort_values("pnl60_mid").iloc[list(range(8)) + list(range(-8, 0))][["fills", "shares", "edge_c", "mk15_fvx", "mk60_mid", "pnl60_mid"]].round(2).to_string())
f.to_pickle(os.path.join(SNAP, "f_mk.pkl"))
