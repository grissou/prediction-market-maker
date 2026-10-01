"""Section 9: quick tests of ideas against day-one fills."""
import os, numpy as np, pandas as pd
SNAP = os.environ.get("SNAP", "snap")
f = pd.read_pickle(os.path.join(SNAP, "f_mk.pkl"))
f["pnl60"] = f.sgn * f.qty * (f.mk60_mid.fillna(f.mk15_mid) - f.yes_px)
f["pnl60_ref"] = f.sgn * f.qty * (f.mk60_reference.fillna(f.mk15_reference) - f.yes_px)
def t(by):
    g = f.groupby(by, observed=True).agg(fills=("qty", "size"), shares=("qty", "sum"), pnl60_mid=("pnl60", "sum"), pnl60_ref=("pnl60_ref", "sum"))
    g["c_per_sh_mid"] = 100 * g.pnl60_mid / g.shares; return g.round(1)
print("by fair-value bucket (favourite-longshot):"); print(t(pd.cut(f.fv_at_quote, [0, .1, .3, .7, .9, 1])).to_string())
print("by side x longshot:"); print(t([f.our_side, f.fv_at_quote < .3]).to_string())
print("by fill size:"); print(t(pd.cut(f.qty, [0, 100, 500, 2000, 20000])).to_string())
print("by minute-of-hour (round-time effects):"); print(t(pd.cut(f.filled_at.dt.minute, [-1, 4, 29, 34, 59])).to_string())
print("by our price on round numbers (x.x0/x.x5 vs other):"); print(t((np.round(f.yes_px * 100) % 5 == 0)).to_string())
# min-edge counterfactual: keep only fills whose edge >= k
for k in (0, 1, 1.5, 2, 3):
    x = f[f.edge_c >= k]; print(f"edge>={k}c: fills {len(x)}, shares {x.qty.sum():.0f}, pnl60_mid {x.pnl60.sum():.0f}")
