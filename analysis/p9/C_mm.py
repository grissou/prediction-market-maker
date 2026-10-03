"""C_mm.py (explorer C, Package 9): what market making earns per day (maker fills, take/arb orders excluded):
spread capture vs the mid at the fill and the mark-out at +1 h / +6 h (mid moves against us), split by tilt direction of the fill.
Usage: python3 analysis/p9/C_mm.py [/home/claude/snap03]
"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, reference from snapshots where ts >= '2026-10-01'", c)
s["t"] = pd.to_datetime(s.ts); s["mid"] = (s.best_bid + s.best_ask) / 2
s["race"] = s.label.str.split(" ", n=1).str[1]; s["cc"] = 1 / s.race.map(s.groupby("race").eid.nunique())
s = s.dropna(subset=["mid"]).sort_values("t")
n = json.load(open(f"{D}/order_notes.json"))
skip = {k for k, v in n.items() if v.get("take") or v.get("arb")}
f = pd.read_csv(f"{D}/fills.csv"); f["oid"] = f.order_id.astype(str)
f = f[~f.oid.isin(skip) & f.our_side.isin(["bid", "ask"]) & f.quote_price.notna()].copy()
f["t"] = pd.to_datetime(f.filled_at); f["eid"] = f.exchange_id.astype(str)
f["yes_px"] = np.where((f.fill_price - f.quote_price).abs() <= (1 - f.fill_price - f.quote_price).abs(), f.fill_price, 1 - f.fill_price)
f["q"] = np.where(f.our_side == "bid", f.qty, -f.qty)
f = f.sort_values("t")
out = f[["t", "eid", "q", "yes_px", "qty"]].copy()
for lag, nm in ((0, "m0"), (3600, "m1"), (6 * 3600, "m6")):
    g = out.copy(); g["tt"] = g.t + pd.Timedelta(seconds=lag)
    m = pd.merge_asof(g.sort_values("tt"), s[["t", "eid", "mid", "reference", "cc"]].rename(columns={"t": "tt"}).sort_values("tt"),
                      on="tt", by="eid", direction="backward", tolerance=pd.Timedelta("10min"))
    out = out.merge(m[["t", "eid", "q", "mid"] + (["reference", "cc"] if lag == 0 else [])].rename(columns={"mid": nm}),
                    on=["t", "eid", "q"], how="left").drop_duplicates(subset=["t", "eid", "q"])
out["cap0"] = out.q * (out.m0 - out.yes_px)
out["mo1"] = out.q * (out.m1 - out.yes_px); out["mo6"] = out.q * (out.m6 - out.yes_px)
out["toward"] = np.sign(out.q * (out.reference - out.cc)) > 0
out["day"] = out.t.dt.strftime("%m-%d")
print(out.groupby("day").agg(fills=("q", "size"), sh=("qty", "sum"), capture_at_fill=("cap0", "sum"), markout_1h=("mo1", "sum"),
                             markout_6h=("mo6", "sum")).round(0).to_string())
print(out.groupby("toward").agg(fills=("q", "size"), sh=("qty", "sum"), cap=("cap0", "sum"), mo1=("mo1", "sum"), mo6=("mo6", "sum")).round(0).to_string())
print("capture per share at the fill (c): %.2f; mark-out +1h %.2f; +6h %.2f" % (
    100 * out.cap0.sum() / out.qty.sum(), 100 * out.mo1.sum() / out.qty.sum(), 100 * out.mo6.sum() / out.qty.sum()))
