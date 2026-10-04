"""F_flow2.py (explorer F, Package 10): exchange print rate (mark steps), per-market capture vs time at the top,
quote age at fill, concentration of maker P&L, top-of-book depth. Needs F_flow.py run first (scratch pickle).
Usage: python3 analysis/p10/F_flow2.py [/home/claude/snap03]"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
SCR = "/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad"
c = sqlite3.connect(f"{D}/md.sqlite")
p = pd.read_sql("select ts, prev_ts, eid, quantity, current_price from positions", c).sort_values(["eid", "ts"])
p["dq"] = p.groupby("eid").quantity.diff().fillna(0); p["dp"] = p.groupby("eid").current_price.diff().fillna(0)
p["gap"] = p.groupby("eid").ts.diff()
k = p[(p.dq == 0) & (p.dp != 0) & p.current_price.notna()]
span = (p.ts.max() - p.ts.min()) / 3600
per = k.groupby("eid").size() / span
print("== A. mark steps with our quantity unchanged (others' prints or ageing) ==")
print("eids %d, span %.1f h, steps per market-hour median %.1f, mean %.1f; median poll gap %.0f s; median |step| %.3fc"
      % (per.size, span, per.median(), per.mean(), p.gap.median(), 100 * k.dp.abs().median()))
m = pd.read_pickle(f"{SCR}/F_fills.pkl")
print("== B. quote age at fill (order_notes t -> fill) ==")
n = json.load(open(f"{D}/order_notes.json"))
m["placed"] = m.order_id.map(lambda o: n.get(str(int(o)), {}).get("t") if o == o else None)
mm = m[m.placed.notna()].copy()
mm["age_s"] = (mm.t - pd.to_datetime(mm.placed.astype(float), unit="s", utc=True)).dt.total_seconds()
mm["ageb"] = pd.cut(mm.age_s, [-1e9, 30, 120, 600, 1800, 1e9], labels=["<30s", "30s-2m", "2-10m", "10-30m", ">30m"])
print(mm.groupby("ageb").apply(lambda x: pd.Series({"fills": len(x), "sh": x.qty.sum(), "c_sh0": 100 * x.cap0.sum() / x.qty.sum(),
      "c_sh1h": 100 * x.mo1.sum() / x.qty.sum(), "c_sh6h": 100 * x.mo6.sum() / x.qty.sum()})).round(2).to_string())
print("== C. concentration of maker P&L (mark-out 1 h) by market, 1-3 Oct ==")
g = m.groupby("eid").agg(sh=("qty", "sum"), mo1=("mo1", "sum"), fills=("q", "size")).sort_values("mo1", ascending=False)
tot = g.mo1.sum()
print("markets with maker fills %d; total mo1 %.0f; top10 %.0f, top30 %.0f, top60 %.0f; markets with mo1 < 0: %d (sum %.0f)" % (
    len(g), tot, g.mo1.head(10).sum(), g.mo1.head(30).sum(), g.mo1.head(60).sum(), (g.mo1 < 0).sum(), g.mo1[g.mo1 < 0].sum()))
print("== D. per-market (2 Oct) maker shares vs market-hours at the top ==")
s = pd.read_sql("select ts, eid, best_bid, best_ask, our_bid, our_ask from snapshots where ts >= '2026-10-02' and ts < '2026-10-03'", c)
s["top"] = ((s.our_bid - s.best_bid).abs() < 1e-9).astype(int) + ((s.our_ask - s.best_ask).abs() < 1e-9).astype(int)
s["q"] = s.our_bid.notna().astype(int) + s.our_ask.notna().astype(int)
th = s.groupby("eid").agg(top_h=("top", lambda x: x.sum() * 70 / 3600), q_h=("q", lambda x: x.sum() * 70 / 3600))
fd = m[m.day == "10-02"].groupby("eid").agg(sh=("qty", "sum"), mo1=("mo1", "sum"))
j = th.join(fd).fillna(0)
X = np.c_[np.ones(len(j)), j.top_h, j.q_h - j.top_h]
b = np.linalg.lstsq(X, j.sh, rcond=None)[0]
print("OLS shares/day = %.0f + %.0f x at-top side-hours + %.0f x quoted-not-top side-hours (n %d markets)" % (b[0], b[1], b[2], len(j)))
b2 = np.linalg.lstsq(X, j.mo1, rcond=None)[0]
print("OLS mark-out-1h $/day = %.1f + %.2f x at-top side-hours + %.3f x quoted-not-top side-hours" % tuple(b2))
print("totals 2 Oct: at-top side-hours %.0f, quoted-not-top %.0f, of %d possible" % (j.top_h.sum(), (j.q_h - j.top_h).sum(), 474 * 24))
print("== E. top-of-book depth (books, latest day): size at the best level, and best-level size excluding ours ==")
bk = pd.read_sql("select ts, eid, bids, asks from books", c)
bb = bk.bids.map(lambda x: (json.loads(x) or [[None, 0]])[0][1]); ba = bk.asks.map(lambda x: (json.loads(x) or [[None, 0]])[0][1])
print("best bid size median %.0f p25 %.0f p75 %.0f; best ask size median %.0f p25 %.0f p75 %.0f" % (
    bb.median(), bb.quantile(.25), bb.quantile(.75), ba.median(), ba.quantile(.25), ba.quantile(.75)))
