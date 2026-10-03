"""Tier-2 evidence (Finisher 2a-v2). Usage: python analysis/poly_bias/tier2.py DATA_DIR
Public-book measurements from snapshots: gap persistence and who closes it, favourite-longshot shape,
pass-through of Polymarket moves into the tournament mid, leg sums per race, who is best, untouched markets."""
import sys, sqlite3, collections, datetime as dt, statistics as st, re
D = sys.argv[1]
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
print("modes:", db.execute("select mode,count(*),min(ts),max(ts),count(distinct ts) from snapshots group by mode").fetchall())
rows = db.execute("select ts,eid,label,best_bid,best_ask,fair_value,reference,our_bid,our_ask,position,mode from snapshots "
                  "where mode='live' order by ts").fetchall()
by = collections.defaultdict(list)
for r in rows:
    if r[3] is None or r[4] is None or r[6] is None: continue
    by[r[1]].append((T(r[0]), (r[3]+r[4])/2, r[6], r[3], r[4], r[7], r[8], r[9] or 0.0, r[2]))
def ols(xs, ys):
    n = len(xs)
    if n < 3: return float('nan'), float('nan'), n
    mx, my = sum(xs)/n, sum(ys)/n
    sxx = sum((x-mx)**2 for x in xs); sxy = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    if sxx == 0: return float('nan'), float('nan'), n
    b = sxy/sxx; res = [(y-my) - b*(x-mx) for x, y in zip(xs, ys)]
    se = (sum(e*e for e in res)/(n-2)/sxx)**0.5
    return b, se, n
def at(s, t):   # last obs at or before t (s sorted)
    lo, hi = 0, len(s)-1
    if s[0][0] > t: return None
    while lo < hi:
        m = (lo+hi+1)//2
        if s[m][0] <= t: lo = m
        else: hi = m-1
    return s[lo]
print("\n## 1. Who closes the gap? gap=ref-mid at t; regress d_mid and d_ref over H on gap (pooled, |gap|>=1c, non-overlapping-ish)")
for H in (0.5, 1, 2, 4, 8):
    X = []; Ym = []; Yr = []
    for eid, s in by.items():
        tl = s[0][0] - 1
        for o in s:
            if o[0] < tl + H*3600/2: continue          # half-overlapping samples
            e = at(s, o[0] + H*3600)
            if e is None or e[0] < o[0] + H*3600*0.8 or s[-1][0] < o[0] + H*3600: continue
            g = o[2] - o[1]
            if abs(g) < 0.01: continue
            X.append(g); Ym.append(e[1]-o[1]); Yr.append(e[2]-o[2]); tl = o[0]
    bm = ols(X, Ym); br = ols(X, Yr)
    print(f"H={H}h n={bm[2]}: mid moves {bm[0]:+.3f}+-{bm[1]:.3f} of gap toward Polymarket; Polymarket moves {br[0]:+.3f}+-{br[1]:.3f} (neg = toward tournament)")
print("\n## 1b. Per-market gap persistence over the live window")
hl = []; pers = 0; n = 0; first_last = []
for eid, s in by.items():
    if len(s) < 50: continue
    g = [o[2]-o[1] for o in s]
    n += 1
    g0 = st.mean(g[:10]); g1 = st.mean(g[-10:])
    first_last.append((g0, g1, s[0][8]))
    same = sum(1 for x in g if x * g1 > 0 and abs(x) >= 0.01) / len(g)
    if abs(g1) >= 0.01 and same >= 0.9: pers += 1
print(f"markets {n}; gap >=1c with the same sign in >=90% of snapshots: {pers} ({100*pers/n:.0f}%)")
b = ols([a for a, _, _ in first_last], [c for _, c, _ in first_last])
print(f"gap(last 10 snaps) on gap(first 10 snaps): slope {b[0]:.2f}+-{b[1]:.2f} (1 = no convergence in {((max(o[0] for s in by.values() for o in s)-min(o[0] for s in by.values() for o in s))/3600):.1f} h)")
ab0 = st.mean(abs(a) for a, _, _ in first_last); ab1 = st.mean(abs(c) for _, c, _ in first_last)
print(f"mean |gap| first {100*ab0:.2f}c -> last {100*ab1:.2f}c")
print("\n## 2. Favourite-longshot shape: gap (ref-mid) by Polymarket price bucket, latest snapshot")
lastt = {eid: s[-1] for eid, s in by.items()}
bk = collections.defaultdict(list)
for eid, o in lastt.items():
    r = o[2]; k = 0 if r < .05 else 1 if r < .15 else 2 if r < .35 else 3 if r < .65 else 4 if r < .85 else 5 if r < .95 else 6
    bk[k].append(o[2]-o[1])
nm = ["<5%", "5-15", "15-35", "35-65", "65-85", "85-95", ">95%"]
for k in sorted(bk): print(f"ref {nm[k]:>6}: n={len(bk[k]):3d} mean gap {100*st.mean(bk[k]):+.2f}c median {100*st.median(bk[k]):+.2f}c")
print("\n## 3. Pass-through: Polymarket moved >=1c between two snapshots; cumulative mid move / ref move after k snapshots")
for thr in (0.01, 0.02):
    acc = collections.defaultdict(list)
    for eid, s in by.items():
        for i in range(1, len(s)-8):
            dr = s[i][2] - s[i-1][2]
            if abs(dr) < thr or s[i][0]-s[i-1][0] > 600: continue
            for k in (0, 1, 2, 4, 8):
                acc[k].append(((s[i+k][1] - s[i-1][1]) * (1 if dr > 0 else -1), abs(dr)))
    print(f"thr {100*thr:.0f}c: " + "; ".join(f"k={k}: {sum(a for a, _ in v)/sum(b for _, b in v):.2f} (n={len(v)})" for k, v in sorted(acc.items())))
print("\n## 4. Leg sums per race (latest snapshot): Dem X + Rep X")
races = collections.defaultdict(dict)
for eid, o in lastt.items():
    m = re.match(r"(Dem|Rep) (.*)", o[8])
    if m: races[m.group(2)][m.group(1)] = o
sb = []; sa = []; sm = []; sr = []
for nme, d in races.items():
    if len(d) == 2:
        sb.append((d["Dem"][3]+d["Rep"][3], nme)); sa.append((d["Dem"][4]+d["Rep"][4], nme)); sm.append(d["Dem"][1]+d["Rep"][1]); sr.append(d["Dem"][2]+d["Rep"][2])
print(f"two-leg races {len(sm)}; mid sum mean {st.mean(sm):.3f} median {st.median(sm):.3f} min {min(sm):.3f} max {max(sm):.3f}; Polymarket sum mean {st.mean(sr):.3f}")
print(f"ask sum < 1 (buy set under 1): {sum(1 for x, _ in sa if x < 0.9999)}; bid sum > 1 (sell set over 1): {sum(1 for x, _ in sb if x > 1.0001)}; mid sum > 1.02: {sum(1 for x in sm if x > 1.02)}; < 0.98: {sum(1 for x in sm if x < 0.98)}")
print("widest:", [(n_, round(x, 3)) for x, n_ in sorted(sb, reverse=True)[:5]], [(n_, round(x, 3)) for x, n_ in sorted(sa)[:5]])
# over time: share of race-snapshots with bid sum>1 or ask sum<1
ts_r = collections.defaultdict(dict)
lab = {eid: s[0][8] for eid, s in by.items()}
cnt = tot = 0; ex_c = []
for nme, d in races.items():
    if len(d) != 2: continue
    e1 = [e for e in by if lab[e] == "Dem " + nme][0]; e2 = [e for e in by if lab[e] == "Rep " + nme][0]
    m2 = {round(o[0]): o for o in by[e2]}
    for o in by[e1]:
        p = m2.get(round(o[0]))
        if not p: continue
        tot += 1
        if o[3]+p[3] > 1.0001: cnt += 1; ex_c.append(o[3]+p[3]-1)
        elif o[4]+p[4] < 0.9999: cnt += 1; ex_c.append(1-o[4]-p[4])
print(f"race-snapshots with a locked set at top of book: {cnt}/{tot} ({100*cnt/max(tot,1):.1f}%), mean size {100*st.mean(ex_c) if ex_c else 0:.2f}c")
print("\n## 5. Spread and who is best (latest snapshot)")
sp_pos = []; sp_no = []; webest_b = webest_a = 0; n = 0; untouched = []
for eid, o in lastt.items():
    n += 1; sp = o[4]-o[3]
    (sp_pos if abs(o[7]) > 0 else sp_no).append(sp)
    if o[5] is not None and abs(o[5]-o[3]) < 1e-9: webest_b += 1
    if o[6] is not None and abs(o[6]-o[4]) < 1e-9: webest_a += 1
    if abs(o[7]) == 0: untouched.append((abs(o[2]-o[1]), sp, o[8], o[2], o[5], o[6]))
print(f"markets {n}; we are the best bid in {webest_b}, best ask in {webest_a}")
print(f"spread median: with position {100*st.median(sp_pos):.1f}c (n={len(sp_pos)}), without {100*st.median(sp_no):.1f}c (n={len(sp_no)})")
print(f"no-position markets: {len(untouched)}; quoting both sides {sum(1 for u in untouched if u[4] and u[5])}, one side {sum(1 for u in untouched if bool(u[4]) != bool(u[5]))}, none {sum(1 for u in untouched if not u[4] and not u[5])}; ref <5% or >95%: {sum(1 for u in untouched if u[3] < .05 or u[3] > .95)}")
print("\n## 6. Spread over time (all markets, median per snapshot quartile) and markout of the mid in gap direction")
allsp = sorted((o[0], o[4]-o[3]) for s in by.values() for o in s)
q = len(allsp)//4
print("median spread by quarter of the live window (c):", [round(100*st.median(x for _, x in allsp[i*q:(i+1)*q]), 2) for i in range(4)])
print("\n## 7. Carry to settlement if Polymarket is right: return on capital of the toward-Polymarket side, latest snapshot")
car = []
for eid, o in lastt.items():
    g = o[2]-o[1]
    if abs(g) < 0.01: continue
    cost = o[4] if g > 0 else 1-o[3]          # buy at ask if ref above, short at bid otherwise
    ev = (o[2]-o[4]) if g > 0 else (o[3]-o[2])
    if cost > 0.02: car.append((ev/cost, ev, cost, o[8], o[2]))
car.sort(reverse=True)
pos = [c for c in car if c[1] > 0]
print(f"markets with |gap|>=1c: {len(car)}; positive edge after crossing: {len(pos)}; median return on capital {100*st.median(c[0] for c in pos):.1f}%; top decile >= {100*pos[len(pos)//10][0]:.0f}%")
k = collections.defaultdict(list)
for c in pos: k['fav' if (c[4] > .85 or c[4] < .15) else 'mid'].append(c[0])
for a, v in k.items(): print(f"  {a}: n={len(v)} median return {100*st.median(v):.1f}%")
print("\n## 8. The tilt over time: per quarter of the live window, slope of gap on (ref-0.5) and R2; tournament mid ~ 0.5 + (1-s)(ref-0.5)")
t0 = min(o[0] for s in by.values() for o in s); t1 = max(o[0] for s in by.values() for o in s)
def r2(xs, ys, b):
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    return 1 - sum(((y-my)-b*(x-mx))**2 for x, y in zip(xs, ys)) / sum((y-my)**2 for y in ys)
tilt_q = []
for qn in range(4):
    X = []; Y = []
    for s in by.values():
        for o in s:
            if t0 + qn*(t1-t0)/4 <= o[0] <= t0 + (qn+1)*(t1-t0)/4: X.append(o[2]-0.5); Y.append(o[2]-o[1])
    b = ols(X, Y); tilt_q.append(b[0])
    print(f"quarter {qn+1}: slope {b[0]:.4f}+-{b[1]:.4f} R2 {r2(X, Y, b[0]):.2f} (n={b[2]})")
print("\n## 9. Split the gap: tilt part s*(ref-0.5) (s from the same quarter) and residual; which part does the mid close over H?")
for H in (0.5, 2, 4, 8):
    A = []; B = []; Y = []
    for eid, s in by.items():
        tl = s[0][0]-1
        for o in s:
            if o[0] < tl + H*1800: continue
            e = at(s, o[0]+H*3600)
            if s[-1][0] < o[0]+H*3600 or e[0] < o[0]+H*3600*0.8: continue
            qn = min(3, int(4*(o[0]-t0)/(t1-t0))); ti = tilt_q[qn]*(o[2]-0.5)
            A.append(ti); B.append(o[2]-o[1]-ti); Y.append(e[1]-o[1]); tl = o[0]
    # two-variable OLS
    n = len(Y); ma, mb, my = sum(A)/n, sum(B)/n, sum(Y)/n
    saa = sum((a-ma)**2 for a in A); sbb = sum((b-mb)**2 for b in B); sab = sum((a-ma)*(b-mb) for a, b in zip(A, B))
    say = sum((a-ma)*(y-my) for a, y in zip(A, Y)); sby = sum((b-mb)*(y-my) for b, y in zip(B, Y))
    det = saa*sbb-sab*sab
    print(f"H={H}h n={n}: mid closes {(say*sbb-sby*sab)/det:+.3f} of the tilt part, {(sby*saa-say*sab)/det:+.3f} of the residual")
print("\n## 10. Locked sets excluding races where the Polymarket legs sum < 0.97 (third candidate)")
cnt = tot = 0; ex_c = []; per = collections.Counter(); runs = []
for nme, d in races.items():
    if len(d) != 2 or d["Dem"][2]+d["Rep"][2] < 0.97: continue
    e1 = [e for e in by if lab[e] == "Dem " + nme][0]; e2 = [e for e in by if lab[e] == "Rep " + nme][0]
    m2 = {round(o[0]): o for o in by[e2]}; run = 0
    for o in by[e1]:
        p = m2.get(round(o[0]))
        if not p: continue
        tot += 1; x = max(o[3]+p[3]-1, 1-o[4]-p[4])
        ours = (o[5] is not None and abs(o[5]-o[3]) < 1e-9) or (p[5] is not None and abs(p[5]-p[3]) < 1e-9) if o[3]+p[3] > 1 else \
               (o[6] is not None and abs(o[6]-o[4]) < 1e-9) or (p[6] is not None and abs(p[6]-p[4]) < 1e-9)
        if x > 0.0001:
            cnt += 1; ex_c.append(x); per[nme] += 1; run += 1; per["_ours"] += ours
        else:
            if run: runs.append(run)
            run = 0
print(f"{cnt}/{tot} ({100*cnt/max(tot,1):.1f}%), mean {100*st.mean(ex_c):.2f}c median {100*st.median(ex_c):.2f}c; one of the two quotes is ours in {per['_ours']}; median run {st.median(runs) if runs else 0} snapshots; races ever locked {len(per)-1}")
print("top races:", [(k, v) for k, v in per.most_common(8) if k != "_ours"])
print("\n## 11. Pass-through by price zone (thr 1c, k=4)")
z = collections.defaultdict(list)
for eid, s in by.items():
    for i in range(1, len(s)-4):
        dr = s[i][2]-s[i-1][2]
        if abs(dr) < 0.01 or s[i][0]-s[i-1][0] > 600: continue
        z['tails' if (s[i][2] < .15 or s[i][2] > .85) else 'middle'].append(((s[i+4][1]-s[i-1][1])*(1 if dr > 0 else -1), abs(dr), (s[i+4][2]-s[i-1][2])*(1 if dr > 0 else -1)))
for k, v in z.items(): print(f"  {k}: mid follows {sum(a for a, _, _ in v)/sum(b for _, b, _ in v):.2f}; Polymarket keeps {sum(c for _, _, c in v)/sum(b for _, b, _ in v):.2f} of its own move (n={len(v)})")
print("\n## 12. The book's exposure to the tilt (latest snapshot): mark P&L if the tilt s rises by 1 point = -sum(pos*(ref-0.5))*0.01")
E = sum(o[7]*(o[2]-0.5) for o in lastt.values()); G = sum(abs(o[7]*(o[2]-0.5)) for o in lastt.values())
print(f"sum pos*(ref-0.5) = {E:+.0f} (gross {G:.0f}): +1 point of tilt marks the book {-E*0.01:+.0f}; tilt 5.1% -> 10% marks it {-E*0.049:+.0f}; tilt -> 0 (settlement at outcome) {E*tilt_q[3]:+.0f}")
