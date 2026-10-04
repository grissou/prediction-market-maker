"""Z-5/6: leaderboard path, longshot bid depth by hour, control contracts vs seat races, and cross-race hedges that free
sum-of-maxima backstop room per $ of EV given up (mm_bot.worst_case_loss on the 15:57 snapshot). Read-only. ~15 s."""
import sys, json, re, sqlite3
from collections import defaultdict
sys.path.insert(0, "/home/claude/prediction-market-maker/analysis/p12")
sys.path.insert(0, "/home/claude/prediction-market-maker")
from X_common import SNAP, load_snapshots, Series, journal_lines, iso
from mm_bot import worst_case_loss

print("leaderboard:", "; ".join(re.sub(r"^(\S{16}).*?Rank (\d+) of (\d+).*", r"\1 \2/\3", l) for l in journal_lines(r"Rank \d+ of")))

snaps = load_snapshots("2026-10-04T00:00")
ser = Series(snaps)
last = snaps[ser.ts[-1]]
# --- control contracts
ctrl = [r for r in last.values() if "U.S." in r["label"] or r["label"].split(" ", 1)[1] in ("House", "Senate")]
for r in sorted(ctrl, key=lambda r: r["label"]):
    print(f"control: {r['label']:22s} bid {r['bb']} ask {r['ba']} ref {r['ref']} pos {r['pos']}")
# seat-implied Senate: count of Dem-favoured seats (p>0.5) among Senate races + expectation
sen = defaultdict(dict)
for r in last.values():
    if r["label"].endswith(" Senate") and "U.S." not in r["label"] and r["p"] is not None:
        sen[r["race"]][r["label"].split()[0]] = (r["p"], (r["bb"] + r["ba"]) / 2 if r["bb"] and r["ba"] else None)
ed = sum(v.get("Dem", (0, 0))[0] for v in sen.values()); edm = sum((v.get("Dem", (0, 0))[1] or 0) for v in sen.values())
print(f"Senate seats up with a Dem leg: {len(sen)}; expected Dem wins at Polymarket {ed:.2f}, at tournament mids {edm:.2f}")

# --- longshot bid depth by hour (books: 3 levels, legs with p < 0.10 at the nearest snapshot)
c = sqlite3.connect(f"{SNAP}/md.sqlite")
dep = defaultdict(lambda: [0.0, 0])
for ts, eid, b in c.execute("select ts,eid,bids from books where ts>=1791072000"):
    p = ser.p(eid, ts)
    if p is None or p >= 0.10:
        continue
    _, rows = ser.at(ts)
    ob = (rows.get(eid) or {}).get("ob")
    d = sum(px * q for px, q in json.loads(b) if ob is None or abs(px - ob) > 1e-9)
    h = iso(ts)[6:8]; dep[h][0] += d; dep[h][1] += 1
print("longshot (p<0.10) bid $ depth per book sample, by hour:", "  ".join(f"{h}h {v[0]/v[1]:,.0f}" for h, v in sorted(dep.items())))

# --- hedges: per race worst_case_loss at fv (fair_value, else ref); try +100 YES on each leg at its ask
races = defaultdict(list)
for r in last.values():
    races[r["race"]].append(r)
def fv(r):
    return r["fv"] if r["fv"] is not None else (r["ref"] if r["ref"] is not None else 0.5)
tot = 0.0; cands = []
for race, mem in races.items():
    legs = [((m["pos"] or 0.0), fv(m)) for m in mem]
    if not any(x for x, _ in legs):
        continue
    w0 = worst_case_loss(legs); tot += w0
    for j, m in enumerate(mem):
        if m["ba"] is None or m["p"] is None:
            continue
        best = None
        for q in (100, 300, 1000, 3000, 10000):
            l2 = [(x + (q if k == j else 0), f) for k, (x, f) in enumerate(legs)]
            dw = w0 - worst_case_loss(l2)
            cost = q * m["ba"]; evloss = q * (m["ba"] - m["p"])
            if dw > 0 and (best is None or dw / max(evloss, 1) > best[0]):
                best = (dw / max(evloss, 1), q, dw, cost, evloss)
        if best:
            cands.append((best[0], m["label"], m["ba"], m["p"], best[1], best[2], best[3], best[4], w0))
print(f"\nsum of per-race worst cases (fv marks, pos at 15:57): {tot:,.0f}")
print("best hedges by backstop room freed per $ of EV given up (buy YES at the ask):")
cands.sort(reverse=True)
free = 0; ev = 0; cash = 0
for k, (ratio, l, a, p, q, dw, cost, evl, w0) in enumerate(cands[:15]):
    print(f"  {l:32s} ask {a:.3f} p {p:.3f} +{q:5d}: room +{dw:6,.0f}, cash {cost:6,.0f}, EV {-evl:+6,.0f}  (race worst {w0:,.0f})")
seen = set()
for ratio, l, a, p, q, dw, cost, evl, w0 in cands:
    race = l.split(" ", 1)[1]
    if race in seen or ratio < 5:
        continue
    seen.add(race); free += dw; ev += evl; cash += cost
print(f"one hedge per race, ratio >= 5: room +{free:,.0f}, cash {cash:,.0f}, EV given up {ev:,.0f} ({len(seen)} races)")
# room per $ of cash, excluding +EV value buys (those are value adds, not hedges); greedy one per race
c2 = sorted((dw / cost, l, a, p, q, dw, cost, evl) for _, l, a, p, q, dw, cost, evl, _w in cands if evl > 0 and cost > 0)[::-1]
seen = set(); room = cash = evx = 0
for r_, l, a, p, q, dw, cost, evl in c2:
    race = l.split(" ", 1)[1]
    if race in seen or dw / cost < 1.5:
        continue
    seen.add(race); room += dw; cash += cost; evx += evl
print(f"pure hedges (EV-negative), room per cash >= 1.5, one per race: room +{room:,.0f} for cash {cash:,.0f}, EV given up {evx:,.0f} ({len(seen)} races)")
print("  top by room/cash: " + "; ".join(f"{l} +{q} room {dw:,.0f}/cash {cost:,.0f}/EV -{evl:,.0f}" for _, l, a, p, q, dw, cost, evl in c2[:6]))
