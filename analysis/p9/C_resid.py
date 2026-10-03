"""C_resid.py (explorer C, Package 9): rivals' residual mispricing after removing the common tilt, and the bot's takes.
1. Per cycle (hourly-binned) tilt s_h: OLS of (mid - c) on (ref - c) through the origin, liquid refs, spread <= 4c.
   Residual r = mid - [c + (1 - s_h)(ref - c)]. Does r mean-revert (lag 1/3/6/12 h)? A take rule on the residual:
   buy at the ask when ask <= model - e, sell at the bid when bid >= model + e; P&L marked at the mid +H h later.
2. The bot's TAKE fills (order_notes take=true): P&L at the final mid and at the final Polymarket price; tilt direction.
3. Rival re-quote speed: after a Polymarket move >= 3c between hourly bins, the share of it the mid follows within 1/3/6 h.
Usage: python3 analysis/p9/C_resid.py [/home/claude/snap03]
"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, reference, our_bid, our_ask from snapshots where ts >= '2026-10-01'", c)
s["t"] = pd.to_datetime(s.ts)
s["race"] = s.label.str.split(" ", n=1).str[1]
s["k"] = s.race.map(s.groupby("race").eid.nunique())
s["cc"] = 1.0 / s.k
s = s.dropna(subset=["best_bid", "best_ask", "reference"])
s["mid"] = (s.best_bid + s.best_ask) / 2
s["spr"] = s.best_ask - s.best_bid
s["h"] = s.t.dt.floor("h")
# hourly bins: last reading per eid per hour
hb = s.sort_values("t").groupby(["eid", "h"]).tail(1).copy()
ok = (hb.spr <= 0.04) & ((hb.reference - hb.cc).abs() > 0.1)
def slope(g):
    x = (g.reference - g.cc).values; y = (g.mid - g.cc).values
    return 1 - (x * y).sum() / (x * x).sum()
sh = hb[ok].groupby("h").apply(slope).rename("s_h")
hb = hb.join(sh, on="h")
hb["model"] = hb.cc + (1 - hb.s_h) * (hb.reference - hb.cc)
hb["r"] = hb.mid - hb.model
print("tilt s by 6h:", sh.resample("6h").last().round(3).to_dict())
print("residual |r| quantiles (spread<=4c):", hb[hb.spr <= 0.04].r.abs().quantile([.5, .75, .9, .95, .99]).round(4).to_dict())
hb = hb.sort_values(["eid", "h"])
for L in (1, 3, 6, 12, 24):
    hb[f"r{L}"] = hb.groupby("eid").r.shift(-L)
    hb[f"m{L}"] = hb.groupby("eid").mid.shift(-L)
liq = hb[(hb.spr <= 0.04) & hb.reference.between(0.03, 0.97)]
for L in (1, 3, 6, 12, 24):
    d = liq.dropna(subset=[f"r{L}"])
    b = np.polyfit(d.r, d[f"r{L}"], 1)[0]
    print(f"residual persistence lag {L}h: beta {b:.3f} (n {len(d)})")
# take rule on the residual (the model includes the tilt, so this is NOT a tilt bet)
print("--- residual take rule: buy ask <= model - e / sell bid >= model + e; P&L per share at the mid +H (and at model+H) ---")
for e in (0.02, 0.03, 0.05):
    for L in (1, 6, 24):
        d = liq.dropna(subset=[f"m{L}"])
        buy = d[d.best_ask <= d.model - e]; sell = d[d.best_bid >= d.model + e]
        pb = (buy[f"m{L}"] - buy.best_ask); ps = (sell.best_bid - sell[f"m{L}"])
        allp = pd.concat([pb, ps])
        print(f"  e {e:.2f} H {L:2d}h: buys {len(buy)} sells {len(sell)} (eid-hours), mean {allp.mean()*100:+.2f}c/sh, "
              f"win {(allp > 0).mean():.0%}, distinct eids {pd.concat([buy.eid, sell.eid]).nunique()}")
# current residual top
now = hb[hb.h == hb.h.max()]
now = now[(now.spr <= 0.04)]
print("NOW biggest |r| (spread<=4c):")
print(now.reindex(now.r.abs().sort_values(ascending=False).index).head(10)[["label", "best_bid", "best_ask", "reference", "model", "r"]].round(3).to_string(index=False))

# 3. rival re-quote speed after Polymarket moves
hb["dref"] = hb.groupby("eid").reference.diff()
mv = hb[(hb.dref.abs() >= 0.03) & hb.reference.between(0.05, 0.95)].copy()
mv["mid0"] = hb.groupby("eid").mid.shift(1).loc[mv.index]
for L in (0, 1, 3, 6):
    col = "mid" if L == 0 else f"m{L}"
    d = mv.dropna(subset=[col, "mid0"])
    pt = ((d[col] - d.mid0) / d.dref)
    print(f"pass-through of a >=3c Polymarket hourly move at +{L}h: median {pt.median():.2f}, mean {pt.clip(-2, 2).mean():.2f} (n {len(d)})")

# 2. the bot's takes
n = json.load(open(f"{D}/order_notes.json"))
tk = {k for k, v in n.items() if v.get("take")}
f = pd.read_csv(f"{D}/fills.csv"); f["oid"] = f.order_id.astype(str)
t = f[f.oid.isin(tk)].copy()
# fill_price is the YES price on some rows and the NO price on others: pick the reading closest to the YES quote_price
t["yes_px"] = np.where((t.fill_price - t.quote_price).abs() <= (1 - t.fill_price - t.quote_price).abs(), t.fill_price, 1 - t.fill_price)
t["q"] = np.where(t.our_side == "bid", t.qty, -t.qty)
last = s.sort_values("t").groupby("eid").tail(1).set_index("eid")
t["eid"] = t.exchange_id.astype(str)
t = t.join(last[["mid", "reference", "cc", "label"]], on="eid")
t["pnl_mid"] = t.q * (t.mid - t.yes_px); t["pnl_ref"] = t.q * (t.reference - t.yes_px)
t["tilt_dir"] = np.sign(t.q * (t.reference - t.cc))     # + = toward Polymarket (short the tilt)
print(f"TAKES: {len(t)} fills, {t.qty.sum():.0f} sh, notional {np.abs(t.q * t.yes_px).sum():.0f}; P&L at final mid {t.pnl_mid.sum():+.0f}, "
      f"at final Polymarket {t.pnl_ref.sum():+.0f}; share toward-Polymarket (short tilt) {(t.tilt_dir > 0).mean():.0%} of fills, "
      f"{t[t.tilt_dir > 0].qty.sum() / t.qty.sum():.0%} of shares")
t["day"] = t.filled_at.str[:10]
print(t.groupby("day").agg(n=("q", "size"), sh=("qty", "sum"), pnl_mid=("pnl_mid", "sum"), pnl_ref=("pnl_ref", "sum")).round(0).to_string())
t["tiltx"] = t.q * (t.reference - t.cc)
print(f"tilt exposure added by take fills (sum q x (ref - c), final ref): {t.tiltx.sum():+.0f} (bot total now +35,933)")
