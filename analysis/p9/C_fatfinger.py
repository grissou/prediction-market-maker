"""C_fatfinger.py (explorer C, Package 9): rival quotes far from the tilt-adjusted Polymarket model (fat fingers / dead quotes).
Per snapshot cycle (all cycles, not hourly): model = c + (1 - s_h)(ref - c) with the hourly tilt s_h from C_resid's method.
A 'gross error' = other trader's best ask <= model - E or best bid >= model + E (our own quote excluded), liquid ref 0.02-0.98.
Episode = consecutive cycles in one market; size from the nearest `books` row at that price (else level-0 size), capped.
Usage: python3 analysis/p9/C_fatfinger.py [/home/claude/snap03]
"""
import sys, json, sqlite3, bisect, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, reference, our_bid, our_ask from snapshots where ts >= '2026-10-01'", c)
s["t"] = pd.to_datetime(s.ts); s["tsn"] = (s.t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
s["race"] = s.label.str.split(" ", n=1).str[1]
s["cc"] = 1.0 / s.race.map(s.groupby("race").eid.nunique())
s = s.dropna(subset=["reference"])
s["h"] = s.t.dt.floor("h")
hb = s.dropna(subset=["best_bid", "best_ask"]).sort_values("t").groupby(["eid", "h"]).tail(1)
hb = hb[((hb.best_ask - hb.best_bid) <= 0.04) & ((hb.reference - hb.cc).abs() > 0.1)]
def slope(g):
    x = (g.reference - g.cc).values; y = ((g.best_bid + g.best_ask) / 2 - g.cc).values
    return 1 - (x * y).sum() / (x * x).sum()
sh = hb.groupby("h").apply(slope).rename("s_h")
s = s.join(sh, on="h"); s["s_h"] = s.s_h.ffill()
s["model"] = s.cc + (1 - s.s_h) * (s.reference - s.cc)
s = s[s.reference.between(0.02, 0.98)]
b = pd.read_sql("select ts, eid, bids, asks from books", c)
bk = {e: (g.ts.values, [json.loads(x) for x in g.bids], [json.loads(x) for x in g.asks]) for e, g in b.sort_values("ts").groupby("eid")}
def size_at(eid, ts, px, side):
    if eid not in bk: return np.nan
    tsv, bids, asks = bk[eid]; i = bisect.bisect_right(tsv, ts) - 1
    if i < 0 or ts - tsv[i] > 7200: return np.nan
    lv = bids[i] if side == "b" else asks[i]
    for p, q in lv:
        if abs(p - px) < 1e-9: return q
    return np.nan
cyc = sorted(s.ts.unique()); ix = {t: i for i, t in enumerate(cyc)}
hours = (s.t.max() - s.t.min()).total_seconds() / 3600
for E in (0.08, 0.10, 0.15, 0.25):
    out = []
    for side in ("a", "b"):
        if side == "a":
            m = s[(s.best_ask <= s.model - E) & ~((s.our_ask.notna()) & (s.our_ask <= s.best_ask + 1e-9))]
        else:
            m = s[(s.best_bid >= s.model + E) & ~((s.our_bid.notna()) & (s.our_bid >= s.best_bid - 1e-9))]
        for eid, g in m.sort_values("ts").groupby("eid"):
            last = -10
            for _, r in g.iterrows():
                i = ix[r.ts]; new = i > last + 1; last = i
                if not new: continue
                px = r.best_ask if side == "a" else r.best_bid
                q = size_at(eid, r.tsn, px, side)
                edge = (r.model - px) if side == "a" else (px - r.model)
                out.append((r.label, r.ts, side, px, r.reference, round(r.model, 3), edge, q))
    o = pd.DataFrame(out, columns=["label", "ts", "side", "px", "ref", "model", "edge", "q"])
    o["usd2k"] = o.edge * o.q.clip(upper=2000).fillna(0)
    o["usd_cash"] = o.edge * np.minimum(o.q.fillna(0), 5000 / np.where(o.side == "a", o.px, 1 - o.px))
    print(f"E {E:.2f}: episodes {len(o)} ({len(o)/hours*24:.1f}/day) in {o.label.nunique()} markets, sized {o.q.notna().sum()}, "
          f"median edge {o.edge.median():.3f}, $ at <=2000 sh {o.usd2k.sum():.0f} ({o.usd2k.sum()/hours*24:.0f}/day), "
          f"$ at <=5k cash {o.usd_cash.sum():.0f} ({o.usd_cash.sum()/hours*24:.0f}/day); by day {o.groupby(o.ts.str[:10]).size().to_dict()}")
    if E >= 0.15:
        print(o.sort_values("usd2k", ascending=False).head(12).round(3).to_string(index=False))
