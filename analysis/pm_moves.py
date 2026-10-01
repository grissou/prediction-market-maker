"""Section 3c: fills just after a Polymarket move, and how often/fast our quotes get chased (journal quote lines)."""
import os, numpy as np, pandas as pd
SNAP = os.environ.get("SNAP", "snap")
f = pd.read_pickle(os.path.join(SNAP, "f_mk.pkl")); q = pd.read_pickle(os.path.join(SNAP, "q.pkl"))
q = q.sort_values("ts"); f = f.sort_values("filled_at")
q["ts"] = q.ts.dt.tz_convert("UTC"); f["filled_at"] = f.filled_at.dt.tz_convert("UTC")
def ref_at(dt):
    x = f[["fill_id", "label", "filled_at"]].copy(); x["t"] = x.filled_at - pd.Timedelta(seconds=dt)
    m = pd.merge_asof(x.sort_values("t"), q[["ts", "label", "ref"]].rename(columns={"ts": "t"}), on="t", by="label")
    return f.fill_id.map(m.set_index("fill_id").ref)
f["ref0"], f["ref120"], f["ref600"] = ref_at(0), ref_at(120), ref_at(600)
f["pm_move"] = f.sgn * (f.ref0 - f.ref600)          # + = Polymarket moved in our direction before the fill
f["dref"] = (f.ref0 - f.ref600).abs()
f["grp"] = pd.cut(f.dref, [-1, 0.0049, 0.0149, 1], labels=["<0.5c", "0.5-1.5c", ">=1.5c"])
f["against"] = np.where(f.dref >= 0.005, np.where(f.pm_move < 0, "PM moved against our fill side", "PM moved with"), "no PM move")
def agg(g):
    q_ = g.qty.sum(); ok = g.mk15_mid.notna()
    return pd.Series({"fills": len(g), "shares": q_, "edge_c": (g.qty * g.edge_c).sum() / q_,
                      "mk15_mid_c": 100 * (g.sgn[ok] * g.qty[ok] * (g.mk15_mid[ok] - g.fv_at_quote[ok])).sum() / max(g.qty[ok].sum(), 1),
                      "pnl15_mid": (g.sgn[ok] * g.qty[ok] * (g.mk15_mid[ok] - g.yes_px[ok])).sum()})
print(f.groupby("against").apply(agg).round(2).to_string())
print("fills with ref known:", f.ref0.notna().sum(), "of", len(f))
# quote-change cadence: per market, seconds between consecutive logged quote changes (only markets we quoted)
q["dt"] = q.groupby("label").ts.diff().dt.total_seconds()
q["dbid"] = q.groupby("label").bid.diff(); q["dask"] = q.groupby("label").ask.diff()
ch = q[q.dt.notna()]
print("seconds between our quote changes per market: median %.0f, p25 %.0f, p75 %.0f" % tuple(ch.dt.quantile([.5, .25, .75])))
tight = ((ch.dbid.round(3) == 0.005) | (ch.dask.round(3) == -0.005))
print("share of quote changes that are a 1-tick tightening (we chase a penny): %.2f; 1-tick widening %.2f" % (
    tight.mean(), ((ch.dbid.round(3) == -0.005) | (ch.dask.round(3) == 0.005)).mean()))
ch["h"] = ch.ts.dt.strftime("%H")
print("quote changes per hour:", ch.groupby("h").size().to_dict())
f.to_pickle(os.path.join(SNAP, "f_mk.pkl"))
