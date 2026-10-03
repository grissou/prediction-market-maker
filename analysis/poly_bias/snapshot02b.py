"""Snapshot 2 Oct 20:37 UTC (ops-snapshot-2026-10-02b): tilt by 4 h bin, tilt exposure, reduce-only, exit ratio,
account marks, writes. Usage: python analysis/poly_bias/snapshot02b.py DATA_DIR
DATA_DIR holds market_data.sql, fills.csv, order_notes.json, status.json, journal_2026-10-02_0814_to_now.log
(staged with git show; never committed). Fill convention: for a sell (our_side=ask) fill_price is the NO price."""
import sys, sqlite3, json, csv, re, collections, statistics as st, datetime as dt
D = sys.argv[1]
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
U = lambda h: T(f"2026-10-0{h}+00:00")            # U("2T08:14") -> epoch
iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %H:%M")
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
P = print
T0814, T1626, END = U("2T08:14"), U("2T16:26:14"), U("2T20:37")

# ---------- 1. tilt by 4 h bin
rows = []
for ts, eid, lab, bb, ba, ref, ob, oa, pos in db.execute(
        "select ts,eid,label,best_bid,best_ask,reference,our_bid,our_ask,position from snapshots where mode='live'"):
    if bb is None or ba is None or ref is None: continue
    own = (ob is not None and abs(ob - bb) < 1e-9) or (oa is not None and abs(oa - ba) < 1e-9)
    rows.append((T(ts), eid, lab, (bb + ba) / 2, ref, own, pos or 0.0, bb, ba))
def fit(X, Y):
    n = len(X); mx, my = sum(X) / n, sum(Y) / n
    sxx = sum((x - mx) ** 2 for x in X); b = sum((x - mx) * (y - my) for x, y in zip(X, Y)) / sxx
    syy = sum((y - my) ** 2 for y in Y)
    return b, 1 - sum(((y - my) - b * (x - mx)) ** 2 for x, y in zip(X, Y)) / syy, n
P("## 1. Tilt s = slope of gap (ref - mid) on (ref - 0.5), pooled rows; ex-own = rows where our quote is NOT the best bid/ask")
P("| bin (UTC) | s | R2 | n | s ex-own | R2 ex-own | n ex-own | own-best share |\n|---|---|---|---|---|---|---|---|")
edges = [U("1T16:00") + 4 * 3600 * k for k in range(8)] + [END + 60]
bins = list(zip(edges, edges[1:]))
bins = bins[:-2] + [(bins[-2][0], END + 60)]          # last bin 16:00-20:37
bins += [(U("2T04:14"), T0814), (T0814, END + 60), (T1626, END + 60)]
for a, b in bins:
    R = [r for r in rows if a <= r[0] < b]; Rx = [r for r in R if not r[5]]
    s, r2, n = fit([r[4] - .5 for r in R], [r[4] - r[3] for r in R])
    sx, r2x, nx = fit([r[4] - .5 for r in Rx], [r[4] - r[3] for r in Rx])
    P(f"| {iso(a)}-{iso(min(b, END))} | {100*s:.2f}% | {r2:.2f} | {n} | {100*sx:.2f}% | {r2x:.2f} | {nx} | {1-nx/n:.1%} |")
hs = []
for h in range(8, 21):
    R = [r for r in rows if U(f"2T{h:02d}:00") <= r[0] < U(f"2T{h:02d}:00") + 3600]
    if R: hs.append(f"{h:02d}h {100*fit([r[4]-.5 for r in R], [r[4]-r[3] for r in R])[0]:.1f}")
P("hourly s (%), 2 Oct: " + ", ".join(hs))
P("\nGap by Polymarket bucket, all rows in the last 4 h (16:37-20:37) vs the 08:14 snapshot's bins (cents, mean)")
nm = ["<5%", "5-15", "15-35", "35-65", "65-85", "85-95", ">95%"]
kf = lambda r: 0 if r < .05 else 1 if r < .15 else 2 if r < .35 else 3 if r < .65 else 4 if r < .85 else 5 if r < .95 else 6
for lab_, a, b in (("last 4 h", END - 4 * 3600, END + 60), ("04:14-08:14", U("2T04:14"), T0814)):
    bk = collections.defaultdict(list); bx = collections.defaultdict(list)
    for r in rows:
        if a <= r[0] < b:
            bk[kf(r[4])].append(r[4] - r[3])
            if not r[5]: bx[kf(r[4])].append(r[4] - r[3])
    P(f"{lab_}: " + "; ".join(f"{nm[k]} {100*st.mean(bk[k]):+.2f}c (ex-own {100*st.mean(bx[k]):+.2f}c, n {len(bk[k])})" for k in sorted(bk)))

# ---------- 2/5. positions table at the last timestamp; marks
last_snap = {}
for r in rows: last_snap[r[1]] = r                       # rows are in ts order within the dump? enforce:
last_snap = {}
for r in sorted(rows, key=lambda r: r[0]): last_snap[r[1]] = r
labels = {r[1]: r[2] for r in rows}
pos_last = {}
for ts, eid, q, cp in db.execute("select ts,eid,quantity,current_price from positions order by ts"):
    pos_last[eid] = (ts, q, cp)
pt = max(v[0] for v in pos_last.values())
held = {e: v for e, v in pos_last.items() if abs(v[1]) > 1e-9}
P(f"\n## 2. Tilt exposure at {iso(pt)} (positions table, {len(held)} held; ref from the last snapshot)")
E = G = Ex = 0.0
for e, (_, q, cp) in held.items():
    ref = last_snap[e][4] if e in last_snap else None
    if ref is None: continue
    E += q * (ref - .5); G += abs(q * (ref - .5))
    if "U.S. House" not in labels.get(e, ""): Ex += q * (ref - .5)
Es = sum(r[6] * (r[4] - .5) for r in last_snap.values())
P(f"sum pos*(ref-0.5) = {E:+,.0f} (gross {G:,.0f}; snapshots.position: {Es:+,.0f}); excluding the U.S. House pair {Ex:+,.0f}. "
  f"+1 point of tilt marks the book {-E/100:+,.0f}")
am = db.execute("select ts,account_value,market_value from account_marks order by ts").fetchall()
def value(t_pos, t_snap_rows, cash, how):
    v = cash
    for e, (q, cp) in t_pos.items():
        r = t_snap_rows.get(e)
        if how == "exchange": m = cp
        elif r is None: m = cp
        elif how == "mid": m = r[3]
        elif how == "poly": m = r[4]
        elif how == "liq": m = r[7] if q > 0 else r[8]
        v += q * m if q > 0 else -q * (1 - m)
    return v
cash = am[-1][1] - am[-1][2]
hp = {e: (q, cp) for e, (_, q, cp) in held.items()}
P(f"\n## 5. Account value at {iso(am[-1][0])}: exchange reply {am[-1][1]:,.0f} (holdings {am[-1][2]:,.0f}, cash {cash:,.0f})")
for how in ("exchange", "liq", "mid", "poly"):
    v = value(hp, last_snap, cash, how); P(f"- {how:8}: {v:,.0f} ({v-am[-1][1]:+,.0f} vs reply)")
own_liq = sum(1 for e, (q, cp) in hp.items() if e in last_snap and
              ((q > 0 and last_snap[e][5] and abs((db.execute("select our_bid from snapshots where eid=? order by ts desc limit 1", (e,)).fetchone()[0] or -1) - last_snap[e][7]) < 1e-9) or
               (q < 0 and last_snap[e][5] and abs((db.execute("select our_ask from snapshots where eid=? order by ts desc limit 1", (e,)).fetchone()[0] or -1) - last_snap[e][8]) < 1e-9)))
P(f"- held markets where the liquidating side's best quote is our own: {own_liq} (liq mark is then optimistic there)")
# exchange value vs mid value over the day (positions and snapshots as of each account_marks row)
pos_rows = db.execute("select ts,eid,quantity,current_price from positions order by ts").fetchall()
snap_sorted = sorted(rows, key=lambda r: r[0])
def state_at(t):
    p = {}
    for ts, e, q, cp in pos_rows:
        if ts > t + 1: break
        p[e] = (q, cp)
    s = {}
    for r in snap_sorted:
        if r[0] > t + 1: break
        s[r[1]] = r
    return {e: v for e, v in p.items() if abs(v[0]) > 1e-9}, s
P("account_marks: exchange value vs mid value")
for ts, av, mv in (am[0], am[len(am) // 2], am[-1]):
    p, s = state_at(ts); vm = value(p, s, av - mv, "mid")
    P(f"- {iso(ts)}: exchange {av:,.0f}, mid {vm:,.0f} (mid - exchange {vm-av:+,.0f}); recomputed at current_price {value(p, s, av-mv, 'exchange'):,.0f}")

# ---------- 3. reduce-only from the journal
rx = re.compile(r"^(\S+) .*realtime \| account (\d+).*worst-case loss (\d+) \(risk (\d+)\)( -> REDUCE-ONLY)?")
ev = []
for line in open(f"{D}/journal_2026-10-02_0814_to_now.log"):
    m = rx.match(line)
    if m: ev.append((T(m.group(1).replace("+0000", "+00:00")), int(m.group(2)), int(m.group(3)), int(m.group(4)), bool(m.group(5))))
P("\n## 3. Reduce-only (journal realtime lines)")
P("| window | lines | REDUCE-ONLY | worst case min-max | account min-max | risk min-max | episodes | median length min | time-weighted RO |\n|---|---|---|---|---|---|---|---|---|")
for nm_, a, b in (("08:14-20:37", T0814, END + 60), ("16:26-20:37", T1626, END + 60)):
    E_ = [e for e in ev if a <= e[0] < b]
    eps = []; start = None
    for e in E_:
        if e[4] and start is None: start = e[0]
        if not e[4] and start is not None: eps.append(e[0] - start); start = None
    open_ep = start is not None
    tw = sum((y[0] - x[0]) * x[4] for x, y in zip(E_, E_[1:])) / (E_[-1][0] - E_[0][0])
    P(f"| {nm_} | {len(E_)} | {sum(e[4] for e in E_)/len(E_):.0%} | {min(e[2] for e in E_):,}-{max(e[2] for e in E_):,} | "
      f"{min(e[1] for e in E_):,}-{max(e[1] for e in E_):,} | {min(e[3] for e in E_):,}-{max(e[3] for e in E_):,} | "
      f"{len(eps)} closed{' + 1 open at the end' if open_ep else ''} | {st.median(eps)/60:.1f} (max {max(eps)/60:.0f}) | {tw:.0%} |")
E_ = [e for e in ev if e[0] >= T1626]
P(f"wc/account on REDUCE-ONLY lines after 16:26: min {min(e[2]/e[1] for e in E_ if e[4]):.3f}; on normal lines max {max(e[2]/e[1] for e in E_ if not e[4]):.3f}")

# ---------- 4. exit ratio from fills.csv with position replay (start: snapshots.position just before 08:14)
notes = json.load(open(f"{D}/order_notes.json"))
start = {}
for r in snap_sorted:
    if r[0] <= T0814: start[r[1]] = r[6]
# snapshots rows with no book were dropped above; take positions from all live rows
for ts, e, p in db.execute("select ts,eid,position from snapshots where mode='live' order by ts"):
    if T(ts) <= T0814: start[e] = p or 0.0
fills = sorted(csv.DictReader(open(f"{D}/fills.csv")), key=lambda f: f["filled_at"])
P("\n## 4. Exit ratio (fills after 08:14, replayed from snapshot positions at 08:14)")
pos = dict(start); acc = collections.defaultdict(lambda: [0.0, 0.0]); unk = 0
for f in fills:
    t = T(f["filled_at"])
    if t < T0814: continue
    side = f["our_side"] if f["our_side"] in ("bid", "ask") else (notes.get(f["order_id"]) or {}).get("our_side")
    if side not in ("bid", "ask"): unk += 1; continue
    e = f["exchange_id"]; q = float(f["qty"]); d = q if side == "bid" else -q; p0 = pos.get(e, 0.0)
    red = min(q, abs(p0)) if p0 * d < 0 else 0.0
    house = "U.S. House" in labels.get(e, "")
    for w, a in (("08:14-20:37", T0814), ("16:26-20:37", T1626)):
        if t >= a:
            for k in (w, w + (" ex U.S. House" if not house else " U.S. House only")):
                acc[k][0] += red; acc[k][1] += q - red
    pos[e] = p0 + d
for k in sorted(acc):
    r, a = acc[k]; P(f"- {k}: reducing {r:,.0f} sh, adding {a:,.0f} sh, ratio {r/max(a,1):.2f}")
end_snap = {}
for ts, e, p in db.execute("select ts,eid,position from snapshots where mode='live' order by ts"): end_snap[e] = p or 0.0
mis = [(labels.get(e, e), round(pos.get(e, 0)), round(end_snap.get(e, 0))) for e in set(pos) | set(end_snap) if abs(pos.get(e, 0) - end_snap.get(e, 0)) > 1]
P(f"- fills with no side: {unk}; replay vs last snapshot position: {len(mis)} markets differ, e.g. {sorted(mis, key=lambda x: -abs(x[1]-x[2]))[:3]}")

# ---------- 7. writes
P("\n## 7. Writes")
j = open(f"{D}/journal_2026-10-02_0814_to_now.log").read().splitlines()
tm = lambda l: T(l.split()[0].replace("+0000", "+00:00"))
r429 = [tm(l) for l in j if "RATE LIMITED" in l]; burst = [tm(l) for l in j if "BURST MODE:" in l]
place = sorted(v["t"] for v in notes.values() if v["t"] >= T0814)
for nm_, a, b in (("08:14-15:30", T0814, U("2T15:30:23")), ("15:30-16:26", U("2T15:30:23"), T1626), ("16:26-20:37", T1626, END + 60)):
    pl = [t for t in place if a <= t < b]; per = collections.Counter(int(t // 60) for t in pl)
    P(f"- {nm_}: 429s {sum(a <= t < b for t in r429)}, burst episodes {sum(a <= t < b for t in burst)}, placements {len(pl)} = "
      f"{len(pl)/((b-a)/60):.1f}/min (max {max(per.values()) if per else 0} in one minute; cancels not logged)")
st_ = json.load(open(f"{D}/status.json"))
P(f"- status: rate_limited_total {st_['rate_limited_total']}, write budget {st_['write_budget_per_min']}/min, requests_last_min {st_['requests_last_min']}, "
  f"write_budget_wait_total {st_['write_budget_wait_total']}, pauses_total {st_['pauses_total']}")

# ---------- 6. takes since 08:14 (same convention as takes.py; marked at the last snapshot)
P("\n## 6. Takes since 08:14")
tko = {o: v for o, v in notes.items() if v.get("take") and v["t"] >= T0814}
byeid = collections.defaultdict(list)
for r in snap_sorted: byeid[r[1]].append(r)
def before(e, t):
    s = [r for r in byeid[e] if r[0] < t]; return s[-1] if s else None
tf = [f for f in fills if f["order_id"] in tko]
Q = pp = pm = p0 = 0.0; mk = collections.Counter()
for f in tf:
    side = f["our_side"] if f["our_side"] in ("bid", "ask") else tko[f["order_id"]]["our_side"]
    q = float(f["qty"]); fp = float(f["fill_price"]); p = fp if side == "bid" else 1 - fp; sg = 1 if side == "bid" else -1
    e = f["exchange_id"]; o0 = before(e, T(f["filled_at"])); oe = last_snap[e]
    Q += q; pp += q * sg * (oe[4] - p); pm += q * sg * (oe[3] - p); p0 += q * sg * (o0[3] - p); mk[labels.get(e, e)] += 1
P(f"take orders {len(tko)}, filled orders {len({f['order_id'] for f in tf})}, fills {len(tf)}, shares {Q:,.0f}; "
  f"P&L at the end: Polymarket {pp:+,.0f} ({100*pp/Q:+.2f}c/sh), tournament mid {pm:+,.0f} ({100*pm/Q:+.2f}c/sh); at the take, mid {p0:+,.0f}")
P(f"markets: {dict(mk.most_common(6))}")
arbo = {o for o, v in notes.items() if v.get("arb") and v["t"] >= T0814}
P(f"arb orders since 08:14: {len(arbo)}, filled legs {sum(1 for f in fills if f['order_id'] in arbo)}")
