"""F_flow.py (explorer F, Package 10): exchange flow seen from our quotes, top-of-book presence, capture and mark-outs,
order placement rate. Usage: python3 analysis/p10/F_flow.py [/home/claude/snap03]"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, our_bid, our_ask, reference, position from snapshots where ts >= '2026-10-01'", c)
s["t"] = pd.to_datetime(s.ts); s["day"] = s.t.dt.strftime("%m-%d"); s["mid"] = (s.best_bid + s.best_ask) / 2
s["race"] = s.label.str.split(" ", n=1).str[1]; s["cc"] = 1 / s.race.map(s.groupby("race").eid.nunique())
s["spr"] = s.best_ask - s.best_bid
s["topb"] = (s.our_bid.notna()) & ((s.our_bid - s.best_bid).abs() < 1e-9)
s["topa"] = (s.our_ask.notna()) & ((s.our_ask - s.best_ask).abs() < 1e-9)
print("== 1. presence per day (share of market-cycles) ==")
g = s.groupby("day")
print(pd.DataFrame({"cycles": g.ts.nunique(), "quote_bid": g.our_bid.apply(lambda x: x.notna().mean()),
                    "quote_ask": g.our_ask.apply(lambda x: x.notna().mean()), "top_bid": g.topb.mean(), "top_ask": g.topa.mean(),
                    "med_spread_c": 100 * g.spr.median(), "mean_spread_c": 100 * g.spr.mean(),
                    "spr<=1c": g.spr.apply(lambda x: (x <= 0.01 + 1e-9).mean())}).round(3).to_string())
# top-of-book changes per market per hour (book activity)
s = s.sort_values(["eid", "t"])
s["chg"] = (s.groupby("eid").best_bid.diff().fillna(0) != 0) | (s.groupby("eid").best_ask.diff().fillna(0) != 0)
s["bid_dn"] = s.groupby("eid").best_bid.diff() < 0     # best bid fell: hit (or pulled)
s["ask_up"] = s.groupby("eid").best_ask.diff() > 0     # best ask rose: lifted (or pulled)
hrs = s.groupby("day").t.agg(lambda x: (x.max() - x.min()).total_seconds() / 3600)
print("top-of-book changes per market-hour (70 s sampling, a lower bound):",
      (s.groupby("day").chg.sum() / hrs / 237).round(1).to_dict(), "| bid falls", (s.groupby("day").bid_dn.sum() / hrs / 237).round(1).to_dict(),
      "| ask rises", (s.groupby("day").ask_up.sum() / hrs / 237).round(1).to_dict())

print("== 2. maker fills vs our presence ==")
n = json.load(open(f"{D}/order_notes.json"))
skip = {k for k, v in n.items() if v.get("take") or v.get("arb")}
f = pd.read_csv(f"{D}/fills.csv"); f["oid"] = f.order_id.astype(str)
f["t"] = pd.to_datetime(f.filled_at); f["eid"] = f.exchange_id.astype(str); f["day"] = f.t.dt.strftime("%m-%d")
allf = f.copy()
f = f[~f.oid.isin(skip) & f.our_side.isin(["bid", "ask"]) & f.quote_price.notna()].copy()
f["yes_px"] = np.where((f.fill_price - f.quote_price).abs() <= (1 - f.fill_price - f.quote_price).abs(), f.fill_price, 1 - f.fill_price)
f["q"] = np.where(f.our_side == "bid", f.qty, -f.qty)
f = f.sort_values("t")
print("all fills by day (shares):", allf.groupby("day").qty.sum().round(0).to_dict(), "maker:", f.groupby("day").qty.sum().round(0).to_dict())
ss = s[["t", "eid", "mid", "reference", "cc", "spr", "topb", "topa", "best_bid", "best_ask"]].sort_values("t")
m = pd.merge_asof(f.sort_values("t"), ss, on="t", by="eid", direction="backward", tolerance=pd.Timedelta("5min"))
for lag, nm in ((3600, "m1"), (6 * 3600, "m6")):
    g2 = m[["t", "eid"]].copy(); g2["tt"] = g2.t + pd.Timedelta(seconds=lag); g2["k"] = range(len(g2))
    mm = pd.merge_asof(g2.sort_values("tt"), ss[["t", "eid", "mid"]].rename(columns={"t": "tt"}), on="tt", by="eid",
                       direction="backward", tolerance=pd.Timedelta("10min")).sort_values("k")
    m[nm] = mm.mid.values
m["cap0"] = m.q * (m.mid - m.yes_px); m["mo1"] = m.q * (m.m1 - m.yes_px); m["mo6"] = m.q * (m.m6 - m.yes_px)
m["halfspr_px"] = (m.yes_px - m.mid).abs()
m["toward"] = np.sign(m.q * (m.reference - m.cc)) > 0
m["edge_fv"] = np.where(m.q > 0, m.fv_at_quote - m.quote_price, m.quote_price - m.fv_at_quote)
def agg(x):
    return pd.Series({"fills": len(x), "sh": x.qty.sum(), "cap0": x.cap0.sum(), "mo1": x.mo1.sum(), "mo6": x.mo6.sum(),
                      "c_per_sh0": 100 * x.cap0.sum() / x.qty.sum(), "c_per_sh1": 100 * x.mo1.sum() / x.qty.sum(),
                      "c_per_sh6": 100 * x.mo6.sum() / x.qty.sum(), "edge_vs_fv_c": 100 * (x.edge_fv * x.qty).sum() / x.qty.sum(),
                      "med_size": x.qty.median()})
print(m.groupby("day").apply(agg).round(2).to_string())
print("by tilt direction (toward Polymarket = counterparty to tilt-pushing flow):"); print(m.groupby(["day", "toward"]).apply(agg).round(2).to_string())
m["cheap"] = pd.cut(m.yes_px, [0, .05, .15, .85, .95, 1.0], labels=["<5c", "5-15", "15-85", "85-95", ">95"])
print("by price bucket:"); print(m.groupby("cheap").apply(agg).round(2).to_string())

print("== 3. flow rate at the top: maker shares per market-hour while our quote was AT the best ==")
# market-hours at top, per side and day
cyc_h = 70 / 3600.
for side, col in (("bid", "topb"), ("ask", "topa")):
    th = s.groupby("day")[col].sum() * cyc_h
    fs = m[(m.our_side == side)].groupby("day").qty.sum()
    fs_top = m[(m.our_side == side) & (m[col] == True)].groupby("day").qty.sum()
    print(side, "market-hours at top", th.round(0).to_dict(), "| fills while at top (sh)", fs_top.round(0).to_dict(),
          "| sh per market-hour at top", (fs_top / th).round(1).to_dict(), "| all maker sh", fs.round(0).to_dict())

print("== 4. our order placements per hour (order_notes) and fills ==")
o = pd.DataFrame([{"t": v.get("t"), "take": bool(v.get("take")), "arb": bool(v.get("arb"))} for v in n.values() if v.get("t")])
o["h"] = pd.to_datetime(o.t, unit="s").dt.strftime("%m-%d %H")
ph = o.groupby("h").agg(orders=("t", "size"), takes=("take", "sum"), arbs=("arb", "sum"))
print("order_notes span", ph.index.min(), ph.index.max(), "| median orders/h", ph.orders.median(), "| p90", ph.orders.quantile(.9))
print(ph.tail(40).to_string())
m.to_pickle("/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/F_fills.pkl")
