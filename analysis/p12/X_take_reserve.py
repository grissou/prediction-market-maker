"""Q4b: the takes skipped by take_respect_reserve (journal lines), valued at the nearest recorder snapshot: the touch
on the side Polymarket favours vs the race-scaled p, times the touch depth from the nearest recorder book (own price
level stripped). An upper bound per skip: repeats of the same market within minutes are the same opportunity."""
import sys, re, sqlite3, json, bisect
from collections import defaultdict
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from X_common import *

S = Series(load_snapshots("2026-10-04T11:00"))
db = sqlite3.connect(f"{SNAP}/md.sqlite")
rx = re.compile(r"^(\S+) .*take on (.+?) skipped: it would spend the market-making reserve")
rows = []
for l in journal_lines("skipped: it would spend the market-making reserve"):
    m = rx.search(l)
    t = ts_of(m.group(1).replace("+0000", "+00:00")); label = m.group(2)
    _, snap = S.at(t)
    r = next((x for x in snap.values() if x["label"] == label), None)
    if r is None or r["p"] is None: rows.append((t, label, None)); continue
    p = r["p"]
    b = db.execute("select ts,bids,asks from books where eid=? order by abs(ts-?) limit 1", (r["eid"], t)).fetchone()
    bids, asks = (json.loads(b[1]), json.loads(b[2])) if b else ([], [])
    bids = [x for x in bids if r["ob"] is None or abs(x[0] - r["ob"]) > 1e-9]
    asks = [x for x in asks if r["oa"] is None or abs(x[0] - r["oa"]) > 1e-9]
    best = None
    if asks and p > asks[0][0]: best = ("buy", asks[0][0], asks[0][1], (p - asks[0][0]) * asks[0][1], asks[0][0] * asks[0][1])
    if bids and bids[0][0] > p:
        c = ("sell", bids[0][0], bids[0][1], (bids[0][0] - p) * bids[0][1], (1 - bids[0][0]) * bids[0][1])
        if best is None or c[3] > best[3]: best = c
    rows.append((t, label, p, best, abs(b[0] - t) if b else None))
uniq = {}
for x in rows:
    if x[2] is None or x[3] is None: continue
    k = x[1]
    if k not in uniq or x[3][3] > uniq[k][3][3]: uniq[k] = x
print(f"skip lines {len(rows)}, distinct markets {len({x[1] for x in rows})}, hours: "
      f"{sorted(set(iso(x[0])[6:8] for x in rows))}")
for x in rows:
    t, label, p = x[:3]
    if p is None: print(f"  {iso(t)} {label:30s} no liquid p"); continue
    bst = x[3]
    print(f"  {iso(t)} {label:30s} p {p:.3f} " + (f"{bst[0]} {bst[2]:.0f} @ {bst[1]:.3f} edge ${bst[3]:.0f} cash ${bst[4]:.0f} (book {x[4]:.0f}s off)" if bst else "no edge at touch now"))
print(f"distinct-market max edge at touch: ${sum(x[3][3] for x in uniq.values()):.0f} "
      f"using ${sum(x[3][4] for x in uniq.values()):.0f} cash over {len(uniq)} markets")
