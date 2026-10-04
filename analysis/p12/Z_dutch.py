"""Z-2: Dutch-book carousel capacity on snap04 books (3 levels, own price levels stripped via the nearest snapshot).
Every 10 min (4 Oct 00:00-15:56), per race with every leg's book younger than 15 min: sets sellable while the bid sum
>= 1.01/1.02/1.03 (walking levels) and sets buyable while the ask sum <= 0.99/0.985. Episodes = consecutive samples
that qualify; one capture per episode. Read-only. ~20 s."""
import sys, json, sqlite3, bisect
from collections import defaultdict
sys.path.insert(0, "/home/claude/prediction-market-maker/analysis/p12")
from X_common import SNAP, load_snapshots, Series, iso

c = sqlite3.connect(f"{SNAP}/md.sqlite")
lab = dict(c.execute("select eid,label from snapshots where ts>='2026-10-04T15' group by eid"))
race_of = {e: l.split(" ", 1)[1] for e, l in lab.items()}
races = defaultdict(list)
for e, r in race_of.items():
    races[r].append(e)
races = {r: m for r, m in races.items() if len(m) >= 2}
ser = Series(load_snapshots("2026-10-03T23:30"))
books = c.execute("select ts,eid,bids,asks from books where ts>=1791072000 order by ts").fetchall()
T0, T1 = 1791072000, books[-1][0]
latest = {}
bi = 0

def strip(levels, own):
    return [(p, q) for p, q in levels if own is None or abs(p - own) > 1e-9]

def walk(sides, thr, sign):
    """sides: per leg [(price, qty)] best first. sign +1: sell all legs while sum >= thr; -1: buy while sum <= thr."""
    idx = [0] * len(sides); rem = [s[0][1] if s else 0 for s in sides]
    sets = 0.0; gross = 0.0; cash = 0.0
    while all(idx[k] < len(sides[k]) for k in range(len(sides))):
        px = [sides[k][idx[k]][0] for k in range(len(sides))]
        tot = sum(px)
        if (sign > 0 and tot < thr - 1e-9) or (sign < 0 and tot > thr + 1e-9):
            break
        q = min(rem)
        sets += q; gross += q * sign * (tot - 1); cash += q * (len(px) - tot if sign > 0 else tot)
        for k in range(len(sides)):
            rem[k] -= q
            if rem[k] <= 1e-9:
                idx[k] += 1
                if idx[k] < len(sides[k]):
                    rem[k] = sides[k][idx[k]][1]
    return sets, gross, cash

SYNC = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
THR_B = (1.01, 1.02, 1.03); THR_S = (0.99, 0.985)
ep = {("b", x): defaultdict(list) for x in THR_B}; ep.update({("s", x): defaultdict(list) for x in THR_S})
open_ = {}
hourly = defaultdict(lambda: defaultdict(float))
t = T0 + 600
nsamp = 0
while t <= T1:
    while bi < len(books) and books[bi][0] <= t:
        ts, e, b, a = books[bi]; latest[e] = (ts, json.loads(b), json.loads(a)); bi += 1
    _, snap = ser.at(t)
    nsamp += 1
    for r, mem in races.items():
        if not all(e in latest and t - latest[e][0] < 900 for e in mem) or \
                max(latest[e][0] for e in mem) - min(latest[e][0] for e in mem) > SYNC:
            for key in ep: open_.pop((key, r), None)
            continue
        bids = [strip(latest[e][1], (snap.get(e) or {}).get("ob")) for e in mem]
        asks = [strip(latest[e][2], (snap.get(e) or {}).get("oa")) for e in mem]
        if not all(bids) or not all(asks):
            continue
        for x in THR_B:
            n, g, cash = walk(bids, x, +1)
            key = ("b", x)
            if n > 0:
                hourly[iso(t)[6:8]][f"b{x}"] += g
                if (key, r) not in open_:
                    open_[(key, r)] = t; ep[key][r].append([t, t, n, g, cash])
                else:
                    ep[key][r][-1][1] = t
                    if g > ep[key][r][-1][3]:
                        ep[key][r][-1][2:] = [n, g, cash]
            else:
                open_.pop((key, r), None)
        for x in THR_S:
            n, g, cash = walk(asks, x, -1)
            key = ("s", x)
            if n > 0:
                if (key, r) not in open_:
                    open_[(key, r)] = t; ep[key][r].append([t, t, n, g, cash])
                else:
                    ep[key][r][-1][1] = t
            else:
                open_.pop((key, r), None)
    t += 600
days = (T1 - T0) / 86400
print(f"{nsamp} samples, {days:.2f} days, {len(races)} races")
for key, d in ep.items():
    eps = [e for v in d.values() for e in v]
    if not eps:
        print(key, "none"); continue
    life = sorted((e[1] - e[0]) / 60 + 10 for e in eps)
    g = sum(e[3] for e in eps); cash = sum(e[4] for e in eps); n = sum(e[2] for e in eps)
    top = sorted(eps, key=lambda e: -e[3])[:3]
    tops = "; ".join(f"{r} {e[2]:.0f}sets ${e[3]:.0f}" for r, v in d.items() for e in v if e in top)
    print(f"{key}: {len(eps)} episodes ({len(eps)/days:.0f}/day) in {len(d)} races, median life {life[len(life)//2]:.0f} min, "
          f"sets {n:,.0f}, gross ${g:,.0f} (${g/days:,.0f}/day), cash locked ${cash:,.0f}; top: {tops}")
# carousel: build episode at >=1.02 then the first later dissolve (asks <= 0.99) in the same race
rt = []
for r, v in ep[("b", 1.02)].items():
    ds = [e[0] for e in ep[("s", 0.99)].get(r, [])]
    for e in v:
        nxt = [d for d in ds if d > e[0]]
        rt.append((nxt[0] - e[0]) / 3600 if nxt else None)
done = [x for x in rt if x is not None]
print(f"build>=1.02 -> dissolve<=0.99 in same race: {len(done)}/{len(rt)} episodes, median {sorted(done)[len(done)//2] if done else float('nan'):.1f} h")
# capacity-limited $/day: capture per episode capped by rotating cash, 50% capture vs rivals
for cap in (10000, 20000):
    eps = sorted((e for v in ep[("b", 1.02)].values() for e in v), key=lambda e: e[0])
    g = 0
    for e in eps:
        frac = min(1.0, cap / max(e[4], 1)) * 0.5
        g += e[3] * frac
    print(f"rotating cash {cap}: >=1.02 episodes, 50% capture, cash-capped: ${g/days:,.0f}/day")
print("hourly gross at >=1.01/1.02 (sum over 10-min samples, upper bound):")
print({h: (round(v['b1.01']), round(v['b1.02'])) for h, v in sorted(hourly.items())})

# ---- B: synchronous check from the recorder snapshots (one row per market per cycle; own top level -> race skipped)
print("\nB. snapshot-synchronous (top of book only, a race is skipped in a cycle where our quote IS the touch):")
for thr in (1.01, 1.02, 1.03):
    eps = defaultdict(list); last = {}
    for t in ser.ts:
        if t < T0:
            continue
        rows = ser.s[t]
        for r, mem in races.items():
            if not all(e in rows for e in mem):
                continue
            bb = [rows[e]["bb"] for e in mem]
            own = any(rows[e]["ob"] is not None and rows[e]["bb"] is not None and abs(rows[e]["ob"] - rows[e]["bb"]) < 1e-9 for e in mem)
            ok = (not own) and all(x is not None for x in bb) and sum(bb) >= thr - 1e-9
            if ok:
                if r in last and t - last[r] < 200:
                    eps[r][-1][1] = t; eps[r][-1][2] = max(eps[r][-1][2], sum(bb))
                else:
                    eps[r].append([t, t, sum(bb)])
                last[r] = t
    allp = [e for v in eps.values() for e in v]
    lives = sorted((e[1] - e[0]) / 60 + 1 for e in allp)
    long_ = [e for e in allp if e[1] - e[0] >= 1800]
    print(f"  bids sum >= {thr}: {len(allp)} episodes ({len(allp)/days:.0f}/day), {len(eps)} races, median life "
          f"{lives[len(lives)//2] if lives else 0:.0f} min; lasting >= 30 min: {len(long_)} "
          f"({', '.join(sorted({r for r, v in eps.items() for e in v if e in long_}))[:300]})")

# ---- C: YES+YES carousel: buy every leg at asks sum <= 0.99 (no own ask at the touch), then sell at bids sum >= 1.00
print("\nC. YES+YES carousel from snapshots: asks-sum <= 0.99 episodes and time to a bids-sum >= 1.00 exit, same race")
eps = defaultdict(list); last = {}; bidok = defaultdict(list)
for t in ser.ts:
    if t < T0:
        continue
    rows = ser.s[t]
    for r, mem in races.items():
        if not all(e in rows for e in mem):
            continue
        ba = [rows[e]["ba"] for e in mem]; bb = [rows[e]["bb"] for e in mem]
        own_a = any(rows[e]["oa"] is not None and rows[e]["ba"] is not None and abs(rows[e]["oa"] - rows[e]["ba"]) < 1e-9 for e in mem)
        if all(x is not None for x in bb) and sum(bb) >= 1.0 - 1e-9:
            bidok[r].append((t, sum(bb)))
        if not own_a and all(x is not None for x in ba) and sum(ba) <= 0.99 + 1e-9:
            if r in last and t - last[r] < 200:
                eps[r][-1][1] = t
            else:
                eps[r].append([t, t, sum(ba)])
            last[r] = t
res = []
for r, v in eps.items():
    for e in v:
        nxt = [(t, s) for t, s in bidok[r] if t > e[1]]
        res.append((r, e[2], (nxt[0][0] - e[1]) / 3600 if nxt else None, nxt[0][1] if nxt else None))
done = sorted(x[2] for x in res if x[2] is not None)
print(f"  {len(res)} buy episodes ({len(res)/days:.0f}/day) in {len(eps)} races; exit found for {len(done)}; "
      f"exit delay median {done[len(done)//2] if done else float('nan'):.2f} h, p75 {done[3*len(done)//4] if done else float('nan'):.2f} h")
gain = [(1 - x[1]) + (x[3] - 1) for x in res if x[3] is not None]
print(f"  round-trip gain per set: median {sorted(gain)[len(gain)//2] if gain else 0:.3f}; races: "
      + ", ".join(f"{r}({len(v)})" for r, v in sorted(eps.items(), key=lambda kv: -len(kv[1]))[:10]))

# ---- D: size the YES+YES carousel: top-of-book size from the nearest book (<= 5 min) for every leg at entry and exit
bk = defaultdict(list)
for ts, e, b, a in books:
    bk[e].append((ts, json.loads(b), json.loads(a)))
def near(e, t, side):
    v = bk.get(e) or []
    ts_ = [x[0] for x in v]
    i = bisect.bisect_left(ts_, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(v) and abs(v[j][0] - t) <= 300:
            if best is None or abs(v[j][0] - t) < abs(best[0] - t):
                best = v[j]
    if best is None:
        return None
    lv = best[2] if side == "a" else best[1]
    return lv[0][1] if lv else None
tot_g = 0; tot_cash = 0; n_sized = 0; persist = 0; trips = []
for r, v in eps.items():
    mem = races[r]
    for e in v:
        nxt = [(t, s) for t, s in bidok[r] if t > e[1]]
        if not nxt:
            continue
        qa = [near(m, e[0], "a") for m in mem]; qb = [near(m, nxt[0][0], "b") for m in mem]
        if any(x is None for x in qa + qb):
            continue
        q = min(qa + qb); g = q * ((1 - e[2]) + (nxt[0][1] - 1))
        n_sized += 1; tot_g += g; tot_cash += q * e[2]
        persist += (e[1] - e[0]) >= 100
        trips.append((g, r, q, e[2], nxt[0][1], (nxt[0][0] - e[1]) / 3600))
print(f"\nD. sized YES+YES round trips: {n_sized} ({n_sized/days:.0f}/day; {persist} lasted >= 2 cycles), gross ${tot_g:,.0f} "
      f"(${tot_g/days:,.0f}/day at 100% capture), cash cycled ${tot_cash:,.0f}")
trips.sort(reverse=True)
print("  biggest: " + "; ".join(f"{r} {q:.0f} sets {a:.3f}->{b:.3f} in {h:.1f}h ${g:.0f}" for g, r, q, a, b, h in trips[:6]))
med_q = sorted(t[2] for t in trips)[len(trips)//2] if trips else 0
print(f"  median top size {med_q:.0f} sets; at 25% capture and <= 5k sets per trip: "
      f"${sum(min(t[2], 5000) / t[2] * t[0] for t in trips) * 0.25 / days:,.0f}/day")
