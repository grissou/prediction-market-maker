"""P6 research, snapshot 2 Oct 20:37 UTC (ops-snapshot-2026-10-02b): (a) backstop pinning, (b) capital lock-up,
(c) exchange mark vs book, (d) Polymarket pass-through after 16:26. Report only; no trading logic.
Usage: python analysis/poly_bias/p6_research.py DATA_DIR   (market_data.sql, fills.csv, order_notes.json,
position_lots.json, journal_2026-10-02_0814_to_now.log; staged with git show, never committed).
Fill convention: for a sell (our_side=ask) fill_price is the NO price, so YES price = 1 - fill_price."""
import sys, sqlite3, json, csv, re, collections, bisect, statistics as st, datetime as dt
D = sys.argv[1]
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
U = lambda h: T(f"2026-10-0{h}+00:00")
iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M")
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
P = print
T1626, END = U("2T16:26:14"), U("2T20:37")
race = lambda lab: lab.split(" ", 1)[1] if lab.split(" ", 1)[0] in ("Dem", "Rep", "Ind") else lab

# ---------- shared: snapshots (live), fills with side and YES price, position replay
S = collections.defaultdict(list)                       # eid -> [(t, bb, ba, fv, ref, ob, oa, pos)]
labels = {}
for ts, e, lab, bb, ba, fv, ref, ob, oa, pos in db.execute(
        "select ts,eid,label,best_bid,best_ask,fair_value,reference,our_bid,our_ask,position from snapshots where mode='live' order by ts"):
    S[e].append((T(ts), bb, ba, fv, ref, ob, oa, pos or 0.0)); labels[e] = lab
ST = {e: [r[0] for r in v] for e, v in S.items()}
def snap(e, t):                                          # last snapshot row at or before t
    i = bisect.bisect_right(ST[e], t) - 1; return S[e][i] if i >= 0 else None
mid = lambda r: (r[1] + r[2]) / 2 if r and r[1] is not None and r[2] is not None else None
notes = json.load(open(f"{D}/order_notes.json"))
F = []; conv = collections.Counter()
# The NO-price convention does not hold for every sell: quote_price is always a YES price, and on 436 of 1,555 sells
# fill_price == quote_price (YES). So a sell's YES price is whichever of fp / 1 - fp is nearer quote_price (or the mid).
for f in sorted(csv.DictReader(open(f"{D}/fills.csv")), key=lambda f: f["filled_at"]):
    side = f["our_side"] if f["our_side"] in ("bid", "ask") else (notes.get(f["order_id"]) or {}).get("our_side")
    if side not in ("bid", "ask"): continue
    q = float(f["qty"]); fp = float(f["fill_price"]); t = T(f["filled_at"]); e = f["exchange_id"]; yes = fp
    if side == "ask":
        anchor = float(f["quote_price"]) if f["quote_price"] else (mid(snap(e, t)) if e in S and snap(e, t) else None)
        yes = 1 - fp if anchor is None or abs(1 - fp - anchor) <= abs(fp - anchor) else fp
        conv["sell NO" if yes != fp else "sell YES"] += 1
    F.append((t, e, q if side == "bid" else -q, yes, f["order_id"]))
pos = {e: (snap(e, T1626) or (0,) * 8)[7] for e in S}
R = []                                                   # fills after 16:26 with (t, e, d, yes_price, reduced, added, pos_before)
for t, e, d, p, o in F:
    if t < T1626: continue
    p0 = pos.get(e, 0.0); red = min(abs(d), abs(p0)) if p0 * d < 0 else 0.0
    R.append((t, e, d, p, red, abs(d) - red, p0)); pos[e] = p0 + d

# ---------- (a) backstop pinning
P(f"fills.csv sell price convention: {dict(conv)}")
P("## (a) Reduce-only episodes after 16:26 (journal ENTERING / leaving lines)")
J = open(f"{D}/journal_2026-10-02_0814_to_now.log").read().splitlines()
tj = lambda l: T(l.split()[0].replace("+0000", "+00:00"))
tr = [(tj(l), "ENTERING" in l) for l in J if "reduce-only: risk" in l and tj(l) >= T1626]
eps = []; s0 = None
for t, ent in tr:
    if ent: s0 = t
    elif s0 is not None: eps.append((s0, t)); s0 = None
if s0 is not None: eps.append((s0, END))
P("| episode | min | reduced sh | re-added <=30 min | <=60 min | churn cost 60 min $ (c/sh) | adds in episode |\n|---|---|---|---|---|---|---|")
tot = collections.Counter()
for a, b in eps:
    red = collections.defaultdict(lambda: [0.0, 0.0, 0.0])   # e -> [shares, sum price*shares, sign of the position]
    add_in = 0.0
    for t, e, d, p, rd, ad, p0 in R:
        if a <= t < b:
            if rd: red[e][0] += rd; red[e][1] += rd * p; red[e][2] = 1 if p0 > 0 else -1
            add_in += ad
    out = []
    for w in (1800, 3600):
        n = cost = 0.0
        for e, (q, qp, sg) in red.items():
            left = q
            for t, e2, d, p, rd, ad, p0 in R:
                if e2 != e or not (b <= t < b + w) or left <= 0 or ad <= 0 or d * sg <= 0: continue
                k = min(ad, left); left -= k; n += k; cost += k * sg * (p - qp / q)
        out.append((n, cost))
    rs = sum(v[0] for v in red.values())
    if b < END: tot["red"] += rs; tot["r30"] += out[0][0]; tot["r60"] += out[1][0]; tot["c60"] += out[1][1]
    P(f"| {iso(a)}-{iso(b)}{'' if b < END else ' (open)'} | {(b-a)/60:.0f} | {rs:,.0f} | {out[0][0]:,.0f} | {out[1][0]:,.0f} | "
      f"{out[1][1]:+,.0f} ({100*out[1][1]/max(out[1][0],1):+.2f}) | {add_in:,.0f} |")
P(f"closed episodes: reduced {tot['red']:,.0f}, re-added {tot['r30']:,.0f} ({tot['r30']/tot['red']:.0%}) in 30 min, "
  f"{tot['r60']:,.0f} ({tot['r60']/tot['red']:.0%}) in 60 min; churn cost {tot['c60']:+,.0f} (positive = bought back higher / sold lower)")

# worst case by race from snapshots (sum of per-race maxima at fair value, as worst_case_loss does)
def wc_legs(legs):
    v = sum(x * f if x > 0 else -x * (1 - f) for x, f in legs)
    sc = [[True], [False]] if len(legs) == 1 else [[j == i for j in range(len(legs))] for i in range(len(legs))]
    pay = lambda w: sum((x if ww else 0) if x > 0 else (0 if ww else -x) for (x, _), ww in zip(legs, w))
    return max(0.0, v - min(pay(s) for s in sc))
races = collections.defaultdict(list)
for e, lab in labels.items(): races[race(lab)].append(e)
def wc_at(t):
    out = {}
    for rc, mem in races.items():
        legs = []
        for e in mem:
            r = snap(e, t)
            if r is None: continue
            f = r[3] if r[3] is not None else (mid(r) or 0.5)
            legs.append((r[7], f))
        if any(x for x, _ in legs): out[rc] = wc_legs(legs)
    return out
rx = re.compile(r"^(\S+) .*realtime \| account (\d+).*worst-case loss (\d+) \(risk (\d+)\)( -> REDUCE-ONLY)?")
ev = [(T(m.group(1).replace("+0000", "+00:00")), int(m.group(2)), int(m.group(3)), int(m.group(4)), bool(m.group(5)))
      for m in map(rx.match, J) if m]
E6 = [x for x in ev if x[0] >= T1626]
chk = [(sum(wc_at(x[0]).values()), x[2]) for x in E6[::40]]
P(f"snapshot recompute of the worst case vs journal (every 40th line): mean diff {st.mean(a-b for a, b in chk):+,.0f}, "
  f"max |diff| {max(abs(a-b) for a, b in chk):,.0f}")
dl = collections.Counter(); up = collections.Counter()
for (a, b), (a2, _) in zip(eps, eps[1:]):                 # exit -> next entry: who drove the worst case back up
    w0, w1 = wc_at(b), wc_at(a2)
    for rc in set(w0) | set(w1): dl[rc] += w1.get(rc, 0) - w0.get(rc, 0)
for a, b in eps:                                         # entry -> exit: who brought it down
    if b >= END: continue
    w0, w1 = wc_at(a), wc_at(b)
    for rc in set(w0) | set(w1): up[rc] += w1.get(rc, 0) - w0.get(rc, 0)
P("races moving the worst case most, summed over the 7 exit->next-entry gaps (+ = up) and the 7 closed episodes:")
P("gaps: " + "; ".join(f"{rc} {v:+,.0f}" for rc, v in dl.most_common(6)) + f"; all races {sum(dl.values()):+,.0f}")
P("episodes: " + "; ".join(f"{rc} {v:+,.0f}" for rc, v in sorted(up.items(), key=lambda x: x[1])[:6]) + f"; all {sum(up.values()):+,.0f}")
wl = wc_at(END); P("largest race worst cases at 20:37: " + "; ".join(f"{rc} {v:,.0f}" for rc, v in sorted(wl.items(), key=lambda x: -x[1])[:6])
                   + f"; total {sum(wl.values()):,.0f}, races {len(wl)}")
P("\nBackstop counterfactual on the observed path (worst / account from realtime lines, hysteresis 0.03; risk cap 0.30 never binds:"
  f" max risk/account {max(x[3]/x[1] for x in E6):.3f})")
P("| backstop | RO minutes 16:26-20:37 | share | episodes | max worst/account seen |\n|---|---|---|---|---|")
mx = max(x[2] / x[1] for x in E6)
for fr in (0.80, 0.83, 0.85):
    ro = False; m = 0.0; n = 0
    for x, y in zip(E6, E6[1:]):
        r = x[2] / x[1]
        if not ro and r > fr: ro = True; n += 1
        elif ro and r < fr - 0.03: ro = False
        m += (y[0] - x[0]) * ro
    P(f"| {fr:.2f} | {m/60:.0f} | {m/(E6[-1][0]-E6[0][0]):.0%} | {n} | {mx:.3f} |")

# ---------- (b) capital lock-up: classes at 20:37 from our flow (trades table is empty: our fills are the only tape)
P("\n## (b) Capital lock-up by market class at 20:37 (flow = our filled shares/h, 14:37-20:37; trades table has 0 rows)")
flow = collections.Counter()
for t, e, d, p, o in F:
    if END - 6 * 3600 <= t <= END: flow[e] += abs(d) / 6
bk = {}
for ts, e, bids, asks in db.execute("select ts,eid,bids,asks from books order by ts"): bk[e] = (ts, json.loads(bids), json.loads(asks))
def cls(e):
    if "U.S. " in labels[e]: return "headline"
    return "dead" if flow[e] < 50 else "quiet" if flow[e] < 200 else "busy"
lots = json.load(open(f"{D}/position_lots.json"))
C = collections.defaultdict(lambda: collections.Counter()); ages = collections.defaultdict(lambda: collections.Counter())
held = []
for e, rows in S.items():
    r = rows[-1]; q = r[7]
    if abs(q) < 1: continue
    m = mid(r) or r[3] or 0.5; c = cls(e); cap = q * m if q > 0 else -q * (1 - m)
    ts_, bids, asks = bk.get(e, (0, [], []))
    ours = r[5] if q > 0 else r[6]                       # our quote on the ADDING side sits in the exit book
    side = [lv for lv in (bids if q > 0 else asks) if ours is None or abs(lv[0] - ours) > 1e-9]
    left = abs(q); proceeds = 0.0
    for px, sz in side:
        k = min(left, sz); left -= k; proceeds += k * (px if q > 0 else 1 - px)
    at_mid = (abs(q) - left) * (m if q > 0 else 1 - m)
    spread = r[2] - r[1] if r[1] is not None and r[2] is not None else None
    oq = r[6] if q > 0 else r[5]                         # our reducing quote vs best other quote on our side
    oside = [lv[0] for lv in (asks if q > 0 else bids) if oq is None or abs(lv[0] - oq) > 1e-9]
    dist = None if oq is None or not oside else (oq - oside[0] if q > 0 else oside[0] - oq)
    if r[4] is not None: C[c]["edge"] += q * (r[4] - m); C[c]["ncap_ref"] += cap
    C[c]["n"] += 1; C[c]["cap"] += cap; C[c]["nobid"] += not side; C[c]["cost"] += at_mid - proceeds
    C[c]["fill_sh"] += abs(q) - left; C[c]["sh"] += abs(q); C[c]["spread"] += spread or 0; C[c]["nsp"] += spread is not None
    if dist is not None: C[c]["dist"] += dist; C[c]["ndist"] += 1; C[c]["behind"] += dist > 1e-9
    else: C[c]["noq"] += 1
    tl = sum(abs(x) for x, _ in lots.get(e, []))
    for lq, lt in lots.get(e, []):
        h = (END - lt) / 3600; b = "<3h" if h < 3 else "3-6h" if h < 6 else "6-12h" if h < 12 else ">12h"
        ages[c][b] += abs(lq) / tl * cap
    held.append(e)
P("| class | held | capital | no exit-side bid | exit-cost vs mid (book depth) | book covers | mean spread | our reducing quote behind best other (mean c, n behind/n quoted) |\n|---|---|---|---|---|---|---|---|")
for c in ("dead", "quiet", "busy", "headline"):
    x = C[c]
    P(f"| {c} | {x['n']} | {x['cap']:,.0f} | {x['nobid']} | {x['cost']:,.0f} | {x['fill_sh']/max(x['sh'],1):.0%} | "
      f"{100*x['spread']/max(x['nsp'],1):.1f}c | {100*x['dist']/max(x['ndist'],1):+.1f}c ({x['behind']}/{x['ndist']}; no quote {x['noq']}) |")
P("Polymarket edge given up by exiting at the mid (sum q x (Polymarket - mid)): " + "; ".join(
    f"{c} {C[c]['edge']:+,.0f} on {C[c]['ncap_ref']:,.0f}" for c in ("dead", "quiet", "busy", "headline")))
P(f"held with 0 fills in 6 h: {sum(1 for e in held if flow[e] == 0)}; held markets {len(held)}; status.json dead flag 41 (41.8k)")
A = collections.defaultdict(lambda: [0.0, 0.0])
for t, e, d, p, rd, ad, p0 in R: A[cls(e)][0] += ad; A[cls(e)][1] += rd
P("adds / reduces 16:26-20:37 (shares): " + "; ".join(f"{c} {A[c][0]:,.0f} / {A[c][1]:,.0f} (ratio {A[c][1]/max(A[c][0],1):.2f})" for c in ("dead", "quiet", "busy", "headline")))
P("capital by lot age (position_lots FIFO, capital-weighted): " + "; ".join(
    f"{c}: " + ", ".join(f"{b} {ages[c][b]/max(sum(ages[c].values()),1):.0%}" for b in ("<3h", "3-6h", "6-12h", ">12h")) for c in ("dead", "quiet", "busy", "headline")))

# ---------- (c) exchange mark vs book
P("\n## (c) Exchange mark (positions.current_price) vs book at the last positions row")
pl = {}
PS = collections.defaultdict(list)
for ts, e, q, cp in db.execute("select ts,eid,quantity,current_price from positions order by ts"): pl[e] = (ts, q, cp); PS[e].append((ts, cp))
G = []
for e, (ts, q, cp) in pl.items():
    if abs(q) < 1e-9 or e not in S: continue
    r = snap(e, ts); m = mid(r)
    if m is None: continue
    liq = r[1] if q > 0 else r[2]
    G.append((q * (cp - m), labels[e], q, cp, m, liq, r[4], cp - liq))
P(f"held {len(G)}: sum q x (mark - mid) {sum(g[0] for g in G):+,.0f}; mean |mark - mid| {100*st.mean(abs(g[3]-g[4]) for g in G):.2f}c; "
  f"mark above liquidation (long: bid; short: below ask) in {sum(1 for g in G if (g[2] > 0) == (g[7] > 0))}/{len(G)}; "
  f"sum q x (mark - liq) {sum(g[2]*g[7] for g in G):+,.0f}")
P("| label | pos | mark | mid | liq side | Polymarket | q x (mark - mid) $ |\n|---|---|---|---|---|---|---|")
for g in sorted(G, key=lambda g: -abs(g[0]))[:10]:
    P(f"| {g[1]} | {g[2]:+,.0f} | {g[3]:.3f} | {g[4]:.3f} | {g[5]} | {g[6] if g[6] is None else round(g[6],3)} | {g[0]:+,.0f} |")
H = [(e, v) for e, v in pl.items() if abs(v[1]) > 1e-9 and e in S]
lag = {L: st.mean(abs(cp - mid(snap(e, ts - L * 3600))) for e, (ts, q, cp) in H if mid(snap(e, ts - L * 3600)) is not None)
       for L in (0, 1, 2, 4, 8)}
far = sum(1 for e, (ts, q, cp) in H if snap(e, ts)[4] is not None and mid(snap(e, ts)) and (cp - mid(snap(e, ts))) * (snap(e, ts)[4] - mid(snap(e, ts))) < 0)
P("mean |mark - mid L hours earlier|: " + ", ".join(f"L={L}h {100*v:.2f}c" for L, v in lag.items())
  + f"; mark on the far side of the mid from Polymarket in {far}/{len(H)}")
PT = {e: [x[0] for x in v] for e, v in PS.items()}
def mark(e, t):
    i = bisect.bisect_right(PT[e], t) - 1; return PS[e][i][1] if i >= 0 else None
M = collections.defaultdict(list)
for t, e, d, p, o in F:
    if t < PT.get(e, [1e20])[0] + 60 or e not in PT: continue
    m0 = mark(e, t - 1)
    if m0 is None or abs(p - m0) < 0.005: continue
    for h in (120, 900, 3600):
        m1 = mark(e, t + h); b0, b1 = mid(snap(e, t - 1)), mid(snap(e, t + h))
        if m1 is not None and b0 is not None and b1 is not None:
            M[(h, "<500" if abs(d) < 500 else ">=500")].append(((m1 - m0) / (p - m0), m1 - m0, abs(d), (b1 - b0) / (p - m0)))
P("mark move after our fills (fills >= 0.5c from the prior mark); share of the gap (fill - mark) closed, median / mean, mean move c:")
for k in sorted(M):
    v = M[k]; P(f"- +{k[0]//60} min, size {k[1]}: n {len(v)}, closed {st.median(x[0] for x in v):+.3f} / {st.mean(x[0] for x in v):+.3f}, "
                f"mark move {100*st.mean(x[1] for x in v):+.2f}c; book mid over the same gap {st.median(x[3] for x in v):+.3f} / {st.mean(x[3] for x in v):+.3f}")

# ---------- (d) Polymarket moves >= 2c after 16:26 and the tournament mid's response
P("\n## (d) Polymarket moves between consecutive snapshots after 16:26 (mid pass-through)")
for thr in (0.02, 0.01):
  W = collections.defaultdict(list)
  for e, rows in S.items():
      for r0, r1 in zip(rows, rows[1:]):
          if r1[0] < T1626 or r0[4] is None or r1[4] is None or abs(r1[4] - r0[4]) < thr: continue
          m0 = mid(r0)
          if m0 is None: continue
          for h in (900, 3600):
              if r1[0] + h > END: continue
              x = snap(e, r1[0] + h); m1 = mid(x)
              if m1 is None or x[4] is None: continue
              W[h].append(((m1 - m0) / (r1[4] - r0[4]), (x[4] - r0[4]) / (r1[4] - r0[4]), (m1 - m0), r1[4] - r0[4], e))
  for h, v in W.items():
      sl = sum(a[2] * a[3] for a in v) / sum(a[3] ** 2 for a in v)
      P(f"- >= {100*thr:.0f}c, +{h//60} min: moves {len(v)} in {len({a[4] for a in v})} markets; mid pass-through median {st.median(a[0] for a in v):.2f}, "
        f"mean {st.mean(a[0] for a in v):.2f}, regression {sl:.2f}; Polymarket kept {st.median(a[1] for a in v):.2f} of its move (median)")
