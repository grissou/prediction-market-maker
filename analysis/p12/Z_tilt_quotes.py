"""Z-3/4: hourly tilt s and band richness (3 Oct 22:00 - 4 Oct 15:57), top edge-per-$ levels at 4 times and their turnover,
and our value-mode maker fills after 11:10 (band, side, edge vs p, rest time) + the 0.5c undercut rate. Read-only. ~20 s."""
import sys, json, sqlite3, bisect
from collections import defaultdict
sys.path.insert(0, "/home/claude/prediction-market-maker/analysis/p12")
from X_common import SNAP, load_snapshots, Series, load_fills, load_notes, ts_of, iso

snaps = load_snapshots("2026-10-03T22:00")
ser = Series(snaps)
# --- hourly s: mid - c = (1 - s)(p - c), least squares through the origin over 2-leg races, p in (0.01, 0.99)
hs = defaultdict(lambda: [0.0, 0.0]); rich = defaultdict(lambda: defaultdict(list))
BANDS = [(0, .03), (.03, .08), (.08, .15), (.15, .5), (.5, .85), (.85, .92), (.92, .97), (.97, 1.01)]
for t in ser.ts:
    h = iso(t)[:8]
    rows = snaps[t]
    nleg = defaultdict(int)
    for r in rows.values():
        nleg[r["race"]] += 1
    for r in rows.values():
        if r["p"] is None or r["bb"] is None or r["ba"] is None or nleg[r["race"]] != 2:
            continue
        mid = (r["bb"] + r["ba"]) / 2; c = 0.5
        x = r["p"] - c; y = mid - c
        hs[h][0] += x * y; hs[h][1] += x * x
        for lo, hi in BANDS:
            if lo <= r["p"] < hi:
                rich[h][(lo, hi)].append(mid - r["p"])
print("hour      s     | mean (mid - p) in cents by p band:", " ".join(f"{lo:.2f}-{hi:.2f}" for lo, hi in BANDS))
for h in sorted(hs):
    s = 1 - hs[h][0] / hs[h][1]
    cells = []
    for b in BANDS:
        v = rich[h][b]
        cells.append(f"{100 * sum(v) / len(v):+5.1f}" if v else "   . ")
    print(f"{h}  {s:.3f} |", "     ".join(cells))

# --- top 20 edge-per-$ levels (touch, own levels skipped) at 4 times; turnover between them
def top20(t):
    _, rows = ser.at(t)
    out = []
    for r in rows.values():
        p = r["p"]
        if p is None:
            continue
        if r["ba"] is not None and (r["oa"] is None or abs(r["oa"] - r["ba"]) > 1e-9) and r["ba"] < p:
            out.append(((p - r["ba"]) / r["ba"], "buy " + r["label"], r["ba"], p))
        if r["bb"] is not None and (r["ob"] is None or abs(r["ob"] - r["bb"]) > 1e-9) and r["bb"] > p:
            out.append(((r["bb"] - p) / (1 - r["bb"]), "short " + r["label"], r["bb"], p))
    return sorted(out, reverse=True)[:20]
prev = None
for hh in ("2026-10-04T04:30:00Z", "2026-10-04T08:30:00Z", "2026-10-04T12:30:00Z", "2026-10-04T15:55:00Z"):
    top = top20(ts_of(hh))
    names = {x[1] for x in top}
    new = len(names - prev) if prev else 20
    print(f"\n{hh}: top-20 edge/$ median {sorted(x[0] for x in top)[10]:.2f}, min {top[-1][0]:.2f}; new vs previous: {new}")
    print("  " + "; ".join(f"{n} {px:.3f}/p{p:.3f} {e:.2f}" for e, n, px, p in top[:8]))
    prev = names

# --- our value-mode maker fills after 11:10:58
notes = load_notes()
T_V = ts_of("2026-10-04T11:10:58Z")
fills = [f for f in load_fills() if ts_of(f["filled_at"]) >= T_V]
lab = {}
for r in snaps[ser.ts[-1]].values():
    lab[r["eid"]] = r["label"]
agg = defaultdict(lambda: [0, 0.0, 0.0, []])
for f in fills:
    n = notes.get(str(f["order_id"]), {})
    kind = "take" if n.get("take") else ("arb" if n.get("arb") else ("maker" if n.get("our_side") else "other"))
    if kind not in ("maker",):
        continue
    t = ts_of(f["filled_at"])
    p = ser.p(f["exchange_id"], t)
    if p is None:
        continue
    q = float(f["qty"]); px = float(f["quote_price"] or f["fill_price"])
    side = n.get("our_side")
    edge = (p - px) if side == "bid" else (px - p)
    band = "tail<0.15" if p < 0.15 else ("fav>0.85" if p > 0.85 else "mid")
    a = agg[(band, side)]
    a[0] += 1; a[1] += q; a[2] += q * edge
    if n.get("t"):
        a[3].append((t - n["t"]) / 60)
print("\nvalue-mode maker fills (11:10:58-15:56), edge vs race-scaled p at fill:")
for k, a in sorted(agg.items()):
    rest = sorted(a[3])
    print(f"  {k[0]:9s} {k[1]:4s} fills {a[0]:3d} shares {a[1]:7.0f} edge ${a[2]:+7.0f} ({a[2]/max(a[1],1)*100:+.1f}c/sh) "
          f"rest median {rest[len(rest)//2] if rest else float('nan'):.0f} min")
# both sides filled in the same mid-band market?
mk = defaultdict(set)
for f in fills:
    n = notes.get(str(f["order_id"]), {})
    if n.get("our_side") and not n.get("take") and not n.get("arb"):
        p = ser.p(f["exchange_id"], ts_of(f["filled_at"]))
        if p is not None and 0.15 <= p <= 0.85:
            mk[f["exchange_id"]].add(n["our_side"])
print(f"  mid-band markets with maker fills: {len(mk)}; with BOTH sides filled: {sum(1 for v in mk.values() if len(v) == 2)}")

# --- undercut: per hour, share of snapshot quotes where the touch is exactly 0.5c better than ours on the same side
c = sqlite3.connect(f"{SNAP}/md.sqlite")
uc = defaultdict(lambda: [0, 0, 0])
for t in ser.ts:
    if t < T_V:
        continue
    h = iso(t)[6:8]
    for r in snaps[t].values():
        for o, b, sg in ((r["ob"], r["bb"], 1), (r["oa"], r["ba"], -1)):
            if o is None or b is None:
                continue
            uc[h][0] += 1
            if abs(b - o - sg * 0.005) < 1e-9:
                uc[h][1] += 1
                if r["p"] is not None and sg * (b - r["p"]) > 0:
                    uc[h][2] += 1      # the undercutter is through p (bids above p / asks below p)
print("\nhour: our quotes / touch exactly 0.5c better / of which the undercutter is through p")
print("  " + "  ".join(f"{h}h {v[0]}/{v[1]}/{v[2]}" for h, v in sorted(uc.items())))
# the 20-share level: books where the top level holds exactly 20 shares, by hour (all day)
h20 = defaultdict(lambda: [0, 0])
for ts, b, a in c.execute("select ts,bids,asks from books where ts>=1791072000"):
    h = iso(ts)[6:8]
    for lv in (json.loads(b)[:1] + json.loads(a)[:1]):
        h20[h][0] += 1; h20[h][1] += abs(lv[1] - 20) < 1e-9
print("top levels holding exactly 20 shares, by hour: " + "  ".join(f"{h}h {v[1]}/{v[0]}" for h, v in sorted(h20.items())))
