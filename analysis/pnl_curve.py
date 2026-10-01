"""Reconstructed P&L over time (fills marked at tournament mid / Polymarket ref / bot fv) vs the API's own value."""
import os, numpy as np, pandas as pd
from load import snapshots, account
SNAP = os.environ.get("SNAP", "snap")
f = pd.read_pickle(os.path.join(SNAP, "f.pkl")); f = f[f.our_side != "?"]
s = snapshots(); s["mid"] = (s.best_bid + s.best_ask) / 2
a = account().set_index("ts"); api = a.account_value - a.locked_in_orders - 100000
out = []
for ts, g in s.groupby("ts"):
    x = f[f.filled_at <= ts]
    m = g.set_index("label")
    def pl(col):
        px = x.label.map(m[col]).fillna(x.label.map(m["mid"]))
        return (x.sgn * x.qty * (px - x.yes_px)).sum()
    edge = (x.sgn * x.qty * (x.fv_at_quote - x.yes_px)).sum()
    out.append((ts, api.get(ts, np.nan), pl("mid"), pl("reference"), edge, len(x)))
o = pd.DataFrame(out, columns=["ts", "api_pnl", "recon_mid", "recon_ref", "edge_cum", "fills"]).set_index("ts")
o.to_csv(os.path.join(SNAP, "pnl_curve.csv"))
print(o.iloc[::6].round(0).to_string()); print(o.iloc[-1].round(0).to_dict())
