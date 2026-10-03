"""C_carousel.py (explorer C, Package 9): the set carousel. Build a NO-set when the bids of a race sum >= 1+x (sell YES on
every leg), dissolve it when the asks sum <= 1-y (buy YES back on every leg): profit bid-sum - ask-sum per set, capital freed.
Measures, per race, the wait from each build signal to the next dissolve signal, and the marked value of the sets we hold
(k - sum of exchange marks) vs their payout (k - 1). Own quotes excluded as in C_sets.
Usage: python3 analysis/p9/C_carousel.py [/home/claude/snap03]
"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, our_bid, our_ask from snapshots where ts >= '2026-10-01'", c)
s["race"] = s.label.str.split(" ", n=1).str[1]
s["k"] = s.race.map(s.groupby("race").eid.nunique())
s = s[s.k >= 2]
s["ownb"] = s.our_bid.notna() & (s.our_bid >= s.best_bid - 1e-9)
s["owna"] = s.our_ask.notna() & (s.our_ask <= s.best_ask + 1e-9)
a = s.groupby(["race", "ts"]).agg(b=("best_bid", "sum"), a=("best_ask", "sum"), nb=("best_bid", "count"), na=("best_ask", "count"),
                                  n=("eid", "count"), k=("k", "first"), ob=("ownb", "any"), oa=("owna", "any")).reset_index()
a = a[a.n == a.k]; a["t"] = pd.to_datetime(a.ts)
a["mid"] = (a.b + a.a) / 2
print("race mid-sum (bid+ask)/2 summed over legs: median %.4f, p25 %.4f, p75 %.4f" % (a.mid.median(), a.mid.quantile(.25), a.mid.quantile(.75)))
for x, y in ((0.03, 0.0), (0.02, 0.0), (0.03, 0.01), (0.02, -0.01)):
    waits, profits = [], []
    for race, g in a.sort_values("t").groupby("race"):
        build = g[(g.nb == g.k) & (g.b >= 1 + x - 1e-9) & ~g.ob]
        diss = g[(g.na == g.k) & (g.a <= 1 - y + 1e-9) & ~g.oa]
        if build.empty: continue
        dt = diss.t.values
        last_end = None
        for _, r in build.iterrows():
            if last_end is not None and r.t < last_end: continue
            j = np.searchsorted(dt, r.t.to_datetime64())
            if j < len(dt):
                w = (pd.Timestamp(dt[j]).tz_localize("UTC") if pd.Timestamp(dt[j]).tzinfo is None else pd.Timestamp(dt[j])) - r.t
                waits.append(w.total_seconds() / 3600); profits.append(r.b - diss.a.values[j]); last_end = r.t + w
            else:
                waits.append(np.nan); profits.append(r.b - 1); last_end = a.t.max()
    w = pd.Series(waits); p = pd.Series(profits)
    done = w.notna()
    print(f"build bid-sum>={1+x:.2f}, dissolve ask-sum<={1-y:.2f}: round trips {len(w)}, closed {done.sum()} "
          f"(median wait {w[done].median():.1f} h, p75 {w[done].quantile(.75):.1f} h), mean profit/set {p[done].mean()*100:.2f}c, "
          f"open (hold to 4 Nov) {(~done).sum()}")
# held sets marked at the exchange's marks
p = pd.read_sql("select ts, eid, quantity, current_price from positions", c).sort_values("ts").groupby("eid").tail(1)
lab = s.groupby("eid").agg(race=("race", "last"), k=("k", "last"))
p = p.join(lab, on="eid").dropna(subset=["race"])
tot_mark = tot_pay = 0
for race, g in p.groupby("race"):
    k = int(g.k.iloc[0])
    if len(g) < k or (g.quantity >= 0).any(): continue
    n = (-g.quantity).min()
    if n < 1: continue
    tot_mark += n * (k - g.current_price.sum()); tot_pay += n * (k - 1)
print(f"held NO-sets: marked {tot_mark:.0f} vs payout {tot_pay:.0f}: the leaderboard shows {tot_pay - tot_mark:.0f} less than settlement "
      f"(cost 21,323 from C_sets)")
# set split: sell only the longshot-NO leg(s) of each held set (buy YES at the ask / sell NO), keep the favourite NO
last = s.sort_values("ts").groupby("eid").tail(1).set_index("eid")
ref = pd.read_sql("select eid, reference, best_bid, best_ask from snapshots where ts = (select max(ts) from snapshots)", c).set_index("eid")
ref = ref.reference.fillna((ref.best_bid + ref.best_ask) / 2)
cash = expo = 0
for race, g in p.groupby("race"):
    k = int(g.k.iloc[0])
    if len(g) < k or (g.quantity >= 0).any(): continue
    n = (-g.quantity).min()
    if n < 1: continue
    fav = max(g.eid, key=lambda e: ref.get(e, 0) or 0)
    for e in g.eid:
        if e == fav: continue
        cash += n * (1 - last.loc[e, "best_ask"]); expo += n * (1 / k - (ref.get(e) or 0))
print(f"set split: selling the non-favourite NO legs frees {cash:.0f} cash; tilt exposure change (long-tilt +) {expo:.0f} "
      f"(= {expo/100:.0f} marked per point of s)")
# YES-set carousel (mirror): buy YES on every leg when the asks sum <= 1 - x, sell them when the bids sum >= 1 + y
print("--- YES-set carousel ---")
hours = (a.t.max() - a.t.min()).total_seconds() / 3600
for x, y in ((0.015, 0.0), (0.015, 0.01), (0.02, 0.0), (0.01, 0.0)):
    waits, profits = [], []
    for race, g in a.sort_values("t").groupby("race"):
        build = g[(g.na == g.k) & (g.a <= 1 - x + 1e-9) & ~g.oa]
        diss = g[(g.nb == g.k) & (g.b >= 1 + y - 1e-9) & ~g.ob]
        dt = diss.t.values; last_end = None
        for _, r in build.iterrows():
            if last_end is not None and r.t < last_end: continue
            j = np.searchsorted(dt, r.t.to_datetime64())
            if j < len(dt):
                w = (pd.Timestamp(dt[j]).tz_localize("UTC") if pd.Timestamp(dt[j]).tzinfo is None else pd.Timestamp(dt[j])) - r.t
                waits.append(w.total_seconds() / 3600); profits.append(diss.b.values[j] - r.a); last_end = r.t + w
            else:
                waits.append(np.nan); profits.append(1 - r.a); last_end = a.t.max()
    w = pd.Series(waits); p2 = pd.Series(profits); done = w.notna()
    print(f"buy ask-sum<={1-x:.3f}, sell bid-sum>={1+y:.2f}: trips {len(w)} ({len(w)/hours*24:.0f}/day), closed {done.sum()} "
          f"(median wait {w[done].median():.1f} h, p75 {w[done].quantile(.75):.1f} h), mean profit/set {p2[done].mean()*100:.2f}c; "
          f"$/day at 500 sets/trip {p2[done].sum()*500/hours*24:.0f}")
