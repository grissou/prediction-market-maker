"""C_rivals.py (explorer C, Package 9): who else quotes. Size signatures in `books`, time-of-day profile, pennying of our quotes.
Usage: python3 analysis/p9/C_rivals.py [/home/claude/snap03]
"""
import sys, json, sqlite3, warnings
import pandas as pd, numpy as np
from collections import Counter, defaultdict
warnings.filterwarnings("ignore")
D = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/snap03"
c = sqlite3.connect(f"{D}/md.sqlite")
b = pd.read_sql("select ts, eid, bids, asks from books", c)
cnt = Counter(); mk = defaultdict(set); lvl0 = Counter()
for r in b.itertuples():
    for side in (r.bids, r.asks):
        for j, (p, q) in enumerate(json.loads(side)):
            cnt[q] += 1; mk[q].add(r.eid)
            if j == 0: lvl0[q] += 1
tot = sum(cnt.values())
print(f"book levels {tot}, distinct sizes {len(cnt)}")
top = sorted(cnt.items(), key=lambda x: -len(mk[x[0]]))[:20]
print("sizes seen in the most markets (size: levels, markets, share of all levels):")
print([(q, n, len(mk[q]), round(n / tot, 3)) for q, n in top])
round_sizes = sum(n for q, n in cnt.items() if q in (25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000))
print(f"round sizes (25..10000) share of levels {round_sizes/tot:.2%}")
s = pd.read_sql("select ts, eid, label, best_bid, best_ask, our_bid, our_ask, reference from snapshots where ts >= '2026-10-01'", c)
s["t"] = pd.to_datetime(s.ts); s["hod"] = s.t.dt.hour; s["spr"] = s.best_ask - s.best_bid
print("median spread (c) by UTC hour:", (s.groupby("hod").spr.median() * 100).round(2).to_dict())
print("share spread<=1c by UTC hour:", s.groupby("hod").spr.apply(lambda x: (x <= 0.0101).mean()).round(2).to_dict())
b["t"] = pd.to_datetime(b.ts, unit="s"); b["hod"] = b.t.dt.hour
b["d0"] = b.bids.apply(lambda x: sum(q for p, q in json.loads(x)[:1])) + b.asks.apply(lambda x: sum(q for p, q in json.loads(x)[:1]))
print("median top-of-book shares (bid+ask) by UTC hour:", b.groupby("hod").d0.median().round(0).to_dict())
# pennying: we are alone at the best bid at cycle i; at cycle i+1 our bid is unchanged and someone bids above it
s = s.sort_values(["eid", "t"])
for side, ours, best, better in (("bid", "our_bid", "best_bid", lambda nb, o: nb > o + 1e-9), ("ask", "our_ask", "best_ask", lambda na, o: na < o - 1e-9)):
    s["n_best"] = s.groupby("eid")[best].shift(-1); s["n_ours"] = s.groupby("eid")[ours].shift(-1)
    s["dt"] = (s.groupby("eid").t.shift(-1) - s.t).dt.total_seconds()
    at = s[(s[ours].notna()) & ((s[ours] - s[best]).abs() < 1e-9) & ((s.n_ours - s[ours]).abs() < 1e-9) & (s.dt < 300)]
    pen = better(at.n_best, at[ours])
    print(f"{side}: we are at the best and keep it {len(at)} cycle-pairs; next cycle someone is better {pen.mean():.2%} "
          f"(by 0.5c exactly: {(((at.n_best - at[ours]).abs() - 0.005).abs() < 1e-9)[pen].mean():.0%} of those); median gap {at.dt.median():.0f}s")
