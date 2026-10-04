"""C_sets.py (explorer C, Package 9): riskless sets.
1. Bid-sum (sell-side arbitrage = buy NO on every leg) and ask-sum (buy YES on every leg) frequency per race per cycle
   from `snapshots`, excluding cycles where OUR quote is the best on a leg (that would be a self-trade); sizes from the
   nearest earlier `books` row; $ available per hour at thresholds 1.00/1.02/1.03/1.04.
2. The NO+NO sets we hold now: cost (exchange avgCost), payout (k-1), locked return to 4 Nov, and the cash we would
   get by unwinding now (sell NO = buy YES at the ask; or sell YES-equivalent).
Usage: python3 analysis/p9/C_sets.py [/home/claude/snap03]
"""
import sys, json, sqlite3, bisect
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')

D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, mode, eid, label, best_bid, best_ask, reference, our_bid, our_ask, position from snapshots", c)
s["t"] = pd.to_datetime(s.ts)
s = s[s.t >= "2026-10-01"]           # live era (1 Oct onward)
s["race"] = s.label.str.split(" ", n=1).str[1]
legs = s.groupby("race").eid.nunique()
s["k"] = s.race.map(legs)
s = s[s.k >= 2]

# books for sizes at the best level
b = pd.read_sql("select ts, eid, bids, asks from books", c)
bk = {}
for eid, g in b.sort_values("ts").groupby("eid"):
    bk[eid] = (g.ts.values, [json.loads(x) for x in g.bids], [json.loads(x) for x in g.asks])

def size_at(eid, ts, px, side):
    if eid not in bk: return np.nan
    tsv, bids, asks = bk[eid]
    i = bisect.bisect_right(tsv, ts) - 1
    if i < 0 or ts - tsv[i] > 7200: return np.nan
    lv = bids[i] if side == "b" else asks[i]
    for p, q in lv:
        if abs(p - px) < 1e-9: return q
    return lv[0][1] if lv else np.nan     # nearest book's best-level size when the price moved since

s["tsn"] = (s.t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
own_b = (s.our_bid.notna()) & (s.our_bid >= s.best_bid - 1e-9)
own_a = (s.our_ask.notna()) & (s.our_ask <= s.best_ask + 1e-9)
s["own_b"] = own_b; s["own_a"] = own_a
g = s.groupby(["ts", "race"])
agg = g.agg(bsum=("best_bid", "sum"), asum=("best_ask", "sum"), n=("eid", "count"), k=("k", "first"),
            ownb=("own_b", "any"), owna=("own_a", "any"), nb=("best_bid", "count"), na=("best_ask", "count"),
            tsn=("tsn", "first")).reset_index()
agg = agg[(agg.n == agg.k)]
cyc = agg.ts.nunique()
t0, t1 = pd.to_datetime(agg.ts.min()), pd.to_datetime(agg.ts.max())
hours = (t1 - t0).total_seconds() / 3600
print(f"cycles {cyc}, {hours:.1f} h, {cyc/hours:.1f} cycles/h, race-cycles {len(agg)}")

# payout of a YES set = 1; NO set on k legs = k-1. Sell-side arb: sell YES on all legs at bids => receive bsum, owe 1.
rows = []
for thr in (1.00, 1.01, 1.02, 1.03, 1.04, 1.06):
    m = agg[(agg.nb == agg.k) & (agg.bsum >= thr + 1e-9)]
    mc = m[~m.ownb]
    rows.append(("bid>=%.2f" % thr, len(m), len(mc), mc.race.nunique()))
for thr in (1.00, 0.99, 0.98, 0.97, 0.95):
    m = agg[(agg.na == agg.k) & (agg.asum <= thr - 1e-9)]
    mc = m[~m.owna]
    rows.append(("ask<=%.2f" % thr, len(m), len(mc), mc.race.nunique()))
print(pd.DataFrame(rows, columns=["cond", "race_cycles", "excl_own", "races"]).to_string(index=False))

# $ per hour: per race episode (consecutive cycles over threshold) capture once at the first cycle, size = thinnest leg
cyc_ts = sorted(agg.ts.unique()); idx = {t: i for i, t in enumerate(cyc_ts)}
sx = s.set_index(["ts", "race"]).sort_index()
def episodes(m, side):
    out = []
    for race, gg in m.sort_values("ts").groupby("race"):
        last = -10
        for _, r in gg.iterrows():
            i = idx[r.ts]
            new = i > last + 1
            last = i
            if not new: continue
            lg = sx.loc[(r.ts, race)]
            sizes = []
            for _, L in lg.iterrows():
                px = L.best_bid if side == "b" else L.best_ask
                sizes.append(size_at(L.eid, r.tsn, px, side))
            q = np.nanmin(sizes) if not all(np.isnan(sizes)) else np.nan
            edge = (r.bsum - 1) if side == "b" else (1 - r.asum)
            cost = (r.k - r.bsum) if side == "b" else r.asum    # capital per set: NO legs cost (1-bid) each
            out.append((race, r.ts, edge, q, cost))
    return pd.DataFrame(out, columns=["race", "ts", "edge", "q", "cost"])

res = {}
for thr in (1.00, 1.02, 1.03, 1.04):
    m = agg[(agg.nb == agg.k) & (agg.bsum >= thr + 1e-9) & (~agg.ownb)]
    e = episodes(m, "b")
    e["usd"] = e.edge * e.q
    res[thr] = e
    print(f"SELL bid-sum>={thr:.2f}: episodes {len(e)} ({len(e)/hours*24:.1f}/day), sized {e.q.notna().sum()}, "
          f"median edge {e.edge.median():.3f}, median thin-leg {e.q.median():.0f}, $ locked {e.usd.sum():.0f} "
          f"({e.usd.sum()/hours:.1f}/h), capital {np.nansum(e.q*e.cost):.0f} (return {e.usd.sum()/max(1,np.nansum(e.q*e.cost)):.2%})")
for thr in (1.00, 0.985, 0.97):
    m = agg[(agg.na == agg.k) & (agg.asum <= thr - 1e-9) & (~agg.owna)]
    e = episodes(m, "a")
    e["usd"] = e.edge * e.q
    print(f"BUY ask-sum<={thr:.3f}: episodes {len(e)} ({len(e)/hours*24:.1f}/day), median edge {e.edge.median():.3f}, "
          f"median thin-leg {e.q.median():.0f}, $ locked {e.usd.sum():.0f} ({e.usd.sum()/hours:.1f}/h), "
          f"capital {np.nansum(e.q*e.cost):.0f}")
    if len(e): print("   top:", e.sort_values("usd", ascending=False).head(5)[["race", "ts", "edge", "q"]].values.tolist())

# by day for bid>=1.03
e = res[1.03]; e["day"] = e.ts.str[:10]
print("bid>=1.03 by day:", e.groupby("day").agg(n=("usd", "size"), usd=("usd", "sum")).to_dict())
print("bid>=1.03 top races:", e.groupby("race").usd.sum().sort_values(ascending=False).head(8).round(0).to_dict())
# last 6 h
last = agg[agg.ts >= (t1 - pd.Timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M")]
print("last 6h: share of race-cycles with bid-sum>=1.03 (excl own):",
      round(((last.bsum >= 1.03) & (last.nb == last.k) & ~last.ownb).mean(), 4),
      "; ask-sum<=0.985:", round(((last.asum <= 0.985) & (last.na == last.k) & ~last.owna).mean(), 4))
now = agg[agg.ts == agg.ts.max()]
print("NOW median bid-sum", round(now.bsum.median(), 3), "max", now.sort_values("bsum").tail(5)[["race", "bsum"]].values.tolist())
print("NOW median ask-sum", round(now.asum.median(), 3), "min", now.sort_values("asum").head(5)[["race", "asum"]].values.tolist())

# ---- 2. NO+NO sets held now
p = pd.read_sql("select ts, eid, quantity, current_price, fields from positions", c)
p = p.sort_values("ts").groupby("eid").tail(1)
p["avg"] = p.fields.apply(lambda f: json.loads(f).get("avgCost") if f and f != "{}" else None)
lab = s.groupby("eid").agg(label=("label", "last"), race=("race", "last"), k=("k", "last"))
snap_now = s[s.ts == s.ts.max()].set_index("eid")
p = p.join(lab, on="eid").dropna(subset=["race"])
tot_sets = tot_cost = tot_pay = tot_unw = 0
lines = []
for race, gg in p.groupby("race"):
    k = int(gg.k.iloc[0])
    if len(gg) < k: continue
    if (gg.quantity >= 0).any(): continue
    n = (-gg.quantity).min()
    if n < 1: continue
    cost = gg.avg.sum()                         # NO avgCost per leg
    pay = k - 1
    # unwind value now: sell NO on each leg = buy YES at best ask -> cash 1-ask per leg
    asks = [snap_now.loc[e, "best_ask"] if e in snap_now.index else np.nan for e in gg.eid]
    unw = sum(1 - a for a in asks)
    tot_sets += n; tot_cost += n * cost; tot_pay += n * pay; tot_unw += n * unw
    lines.append((race, k, int(n), round(cost, 4), pay, round(unw, 4)))
L = pd.DataFrame(lines, columns=["race", "k", "sets", "cost_per_set", "payout", "unwind_now"])
print(L.sort_values("sets", ascending=False).to_string(index=False))
print(f"NO+NO sets {tot_sets:.0f} in {len(L)} races: cost {tot_cost:.0f}, payout {tot_pay:.0f} (+{tot_pay-tot_cost:.0f}, "
      f"{tot_pay/tot_cost-1:.2%} to 4 Nov), unwind now {tot_unw:.0f} (vs payout: give up {tot_pay-tot_unw:.0f}, "
      f"{tot_pay/tot_unw-1:.2%} remaining locked return over 31 d)")

# ---- 3. robust $ per hour: size capped (2000 sets, and the bot's arb_max_frac 500), one capture per episode
print("--- capped sizes (one capture per episode; size = min(thin leg, cap)) ---")
for thr in (1.00, 1.02, 1.03, 1.04):
    e = res[thr].dropna(subset=["q"])
    for cap in (500, 2000):
        q = e.q.clip(upper=cap)
        usd = (e.edge * q).sum(); capu = (q * e.cost).sum()
        print(f"  bid>={thr:.2f} cap {cap}: ${usd:.0f} over {hours:.0f} h = {usd/hours:.1f}/h = {usd/hours*24:.0f}/day; "
              f"capital {capu:.0f} ({capu/hours:.0f}/h), edge/capital {usd/capu:.2%}")
    # last 24h only
    e2 = e[e.ts >= (t1 - pd.Timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")]
    q = e2.q.clip(upper=2000)
    print(f"     last 24h cap 2000: {len(e2)} episodes, ${(e2.edge*q).sum():.0f}/day, capital {(q*e2.cost).sum():.0f}")
