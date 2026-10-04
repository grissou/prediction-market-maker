"""Y_onepct: the candidate "~1% a day of expected value at the outcome" streams, measured on snap04. Read-only.
(a) rotation: edge-held of the 96 holdings vs the supply of >= 10% edge-per-$ levels (own quotes stripped) at 4 times today
(b) stale-quote takes: journal TAKE / take_reserve_blocked lines per hour; snapshot episodes of >= 8% per $ past Polymarket
(c) race Dutch books (bids sum > 1, asks sum < 1) per day
(d) middle band: book changes per day in 15-85c markets
(e) the tilt over the day
Usage: python3 analysis/p12/Y_onepct.py   (~30-60 s)"""
import sys, re, json, sqlite3, math
from collections import defaultdict
sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p12'); sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p10')
import numpy as np
import X_common as X
import H_outcome as H
H.SNAP = X.SNAP

# ---------------- (a) edge-held of the holdings
d, cash, st = H.load()
h = d[d.pos != 0].copy()
rows = []
for _, r in h.iterrows():
    p = r.p0
    if r.pos > 0:      # sell YES at the bid: edge given up per $ freed = (p - bid) / bid
        b = float(r.bids[0][0]) if r.bids else None
        if not b: continue
        eh, usd, depth = (p - b) / b, r.pos * b, sum(float(q) * float(px) for px, q in r.bids[:1])
    else:              # buy back at the ask: $ freed = |q| (1 - ask), edge given up = (ask - p) / (1 - ask)
        a = float(r.asks[0][0]) if r.asks else None
        if not a: continue
        eh, usd, depth = (a - p) / (1 - a), -r.pos * (1 - a), sum(float(q) * (1 - float(px)) for px, q in r.asks[:1])
    rows.append((r.label, r.pos, eh, usd, depth))
eh = np.array([x[2] for x in rows]); usd = np.array([x[3] for x in rows]); dep = np.array([min(x[3], x[4]) for x in rows])
print(f"(a) holdings priced at the touch {len(rows)}, $ at touch-exit value {usd.sum():,.0f}")
for t in (0.0, 0.02, 0.04, 0.06, 0.10):
    m = eh < t
    print(f"   edge-held < {t:.0%}: {m.sum():3d} positions, ${usd[m].sum():8,.0f} (exit-able at the top level ${dep[m].sum():7,.0f})")
print("   lowest edge-held:", ", ".join(f"{l} {e:+.3f} ${u:,.0f}" for l, q, e, u, _ in sorted(rows, key=lambda x: x[2])[:8]))

# ---------------- book replay helpers (books are per-eid updates, 3 levels; strip our own price level from snapshots)
c = sqlite3.connect(X.SNAP + '/md.sqlite')
books = c.execute("select ts,eid,bids,asks from books where ts >= ? order by ts", (X.ts_of('2026-10-03T22:47:00Z'),)).fetchall()
snaps = X.load_snapshots(since='2026-10-03T22:47')
sts = sorted(snaps)
label = {}
for t in sts:
    for e, r in snaps[t].items():
        label[e] = (r['label'], r['race'])


def replay():
    """yield (t, snapshot rows, {eid: (bids, asks)}) at every snapshot time, own levels stripped"""
    cur = {}; i = 0
    for t in sts:
        while i < len(books) and books[i][0] <= t:
            _, e, b, a = books[i]; cur[e] = (json.loads(b), json.loads(a)); i += 1
        rows = snaps[t]; out = {}
        for e, (b, a) in cur.items():
            r = rows.get(e)
            if r is None: continue
            ob, oa = r['ob'], r['oa']
            out[e] = ([x for x in b if ob is None or abs(float(x[0]) - ob) > 1e-9],
                      [x for x in a if oa is None or abs(float(x[0]) - oa) > 1e-9])
        yield t, rows, out


def levels(rows, bk, min_edge):
    """all levels with edge per $ >= min_edge: (edge, $locked, label, side, price)"""
    out = []
    for e, (b, a) in bk.items():
        r = rows.get(e)
        if not r or r['p'] is None or r['race'] in ('U.S. House', 'U.S. Senate'): continue
        p = r['p']
        for px, q in a:
            px, q = float(px), float(q)
            if 0 < px < 1 and (p - px) / px >= min_edge: out.append(((p - px) / px, q * px, r['label'], 'buy', px))
        for px, q in b:
            px, q = float(px), float(q)
            if 0 < px < 1 and (px - p) / (1 - px) >= min_edge: out.append(((px - p) / (1 - px), q * (1 - px), r['label'], 'short', px))
    return out


# ---------------- one pass over the day
probe = [X.ts_of(x) for x in ('2026-10-04T06:00:00Z', '2026-10-04T10:00:00Z', '2026-10-04T13:00:00Z', '2026-10-04T15:55:00Z')]
pi = 0; tops = {}
ep = {}            # (eid, side) -> start t, for >= 8% episodes
episodes = []      # (label, side, start, dur_s, max edge, $ at touch at start)
dutch = {}; dutch_ep = []
mid_changes = 0; mid_n = 0; prev_top = {}
t0 = None
for t, rows, bk in replay():
    t0 = t0 or t
    if pi < len(probe) and t >= probe[pi]:
        lv = levels(rows, bk, 0.10)
        tops[probe[pi]] = (sum(x[1] for x in lv), sorted(lv, reverse=True)[:20])
        pi += 1
    # >= 8% at the touch episodes
    now = set()
    for e, (b, a) in bk.items():
        r = rows.get(e)
        if not r or r['p'] is None or r['race'] in ('U.S. House', 'U.S. Senate'): continue
        p = r['p']
        if a:
            px, q = float(a[0][0]), float(a[0][1])
            if 0 < px < 1 and (p - px) / px >= 0.08: now.add((e, 'buy', (p - px) / px, q * px))
        if b:
            px, q = float(b[0][0]), float(b[0][1])
            if 0 < px < 1 and (px - p) / (1 - px) >= 0.08: now.add((e, 'short', (px - p) / (1 - px), q * (1 - px)))
        if 0.15 <= p <= 0.85:
            top = (b[0][0] if b else None, a[0][0] if a else None, b[0][1] if b else None, a[0][1] if a else None)
            mid_n += 1
            if e in prev_top and prev_top[e] != top: mid_changes += 1
            prev_top[e] = top
    keys = {(e, s) for e, s, _, _ in now}
    for e, s, edge, usdv in now:
        if (e, s) not in ep: ep[(e, s)] = [t, edge, usdv]
        else: ep[(e, s)][1] = max(ep[(e, s)][1], edge)
    for k in list(ep):
        if k not in keys:
            st0, edge, usdv = ep.pop(k); episodes.append((label[k[0]][0], k[1], st0, t - st0, edge, usdv))
    # Dutch books per race (all legs with a book)
    races = defaultdict(list)
    for e, (b, a) in bk.items():
        if e in rows: races[rows[e]['race']].append((b, a))
    nraces = defaultdict(int)
    for e, r in rows.items(): nraces[r['race']] += 1
    cur = set()
    for race, legs in races.items():
        if len(legs) < 2 or len(legs) != nraces[race]: continue
        if all(b for b, _ in legs):
            sb = sum(float(b[0][0]) for b, _ in legs)
            if sb > 1.0:
                cur.add((race, 'bids>1', sb - 1, min(float(b[0][1]) for b, _ in legs)))
        if all(a for _, a in legs):
            sa = sum(float(a[0][0]) for _, a in legs)
            if sa < 1.0:
                cur.add((race, 'asks<1', 1 - sa, min(float(a[0][1]) for _, a in legs)))
    ck = {(r, k) for r, k, _, _ in cur}
    for r, k, g, q in cur:
        if (r, k) not in dutch: dutch[(r, k)] = [t, g, q]
    for k in list(dutch):
        if k not in ck:
            s0, g, q = dutch.pop(k); dutch_ep.append((k[0], k[1], s0, t - s0, g, q))
T = (sts[-1] - sts[0]) / 86400
print(f"\nwindow {X.iso(sts[0])} -> {X.iso(sts[-1])} = {T:.2f} days, {len(sts)} snapshots")

print("\n(a) supply of >= 10% edge-per-$ levels (own levels stripped, 3 recorded levels, no headline):")
sets = []
for tp, (tot, top) in tops.items():
    sets.append({(x[2], x[3]) for x in top})
    print(f"  {X.iso(tp)}: ${tot:,.0f} locked-cash depth at >= 10%; top 5: " +
          "; ".join(f"{x[3]} {x[2]} @{x[4]:.3f} {x[0]:.0%} ${x[1]:,.0f}" for x in top[:5]))
for i in range(1, len(sets)):
    print(f"  top-20 overlap with the previous probe: {len(sets[i] & sets[i-1])}/20; new: {len(sets[i] - sets[i-1])}")

print("\n(b2) >= 8% per $ at the touch past Polymarket (episodes ended in the window):")
ep_arr = [x for x in episodes if x[2] >= X.ts_of('2026-10-04T00:00:00Z')]
n = len(ep_arr); dur = np.array([x[3] for x in ep_arr]) if n else np.zeros(1)
gain = sum(x[4] * x[5] for x in ep_arr)
print(f"  4 Oct 00:00-15:57: {n} episodes, {len({x[0] for x in ep_arr})} markets; duration median {np.median(dur):.0f}s, "
      f"p75 {np.percentile(dur,75):.0f}s; $ at the touch {sum(x[5] for x in ep_arr):,.0f}, edge x $ {gain:,.0f}")
short = [x for x in ep_arr if x[3] <= 600]
print(f"  of them <= 10 min (new mispricings, not standing ones): {len(short)}, $ {sum(x[5] for x in short):,.0f}, edge x $ {sum(x[4]*x[5] for x in short):,.0f}")
by_h = defaultdict(lambda: [0, 0.0])
for x in ep_arr:
    hh = X.iso(x[2])[6:8]; by_h[hh][0] += 1; by_h[hh][1] += x[4] * x[5]
print("  per hour (starts, edge x $):", " ".join(f"{k}h {v[0]}/{v[1]:.0f}" for k, v in sorted(by_h.items())))

print("\n(c) race Dutch books (top of book, own levels stripped):")
for kind in ('bids>1', 'asks<1'):
    e = [x for x in dutch_ep if x[1] == kind]
    for th in (0.0, 0.015):
        ee = [x for x in e if x[4] >= th]
        print(f"  {kind} gap>={th:.3f}: {len(ee)} episodes in {T:.2f} d ({len(ee)/T:.1f}/day), races {len({x[0] for x in ee})}, "
              f"median dur {np.median([x[3] for x in ee]) if ee else 0:.0f}s, gap x size {sum(x[4]*x[5] for x in ee):,.0f} "
              f"({sum(x[4]*x[5] for x in ee)/T:,.0f}/day)")
    for x in sorted(e, key=lambda x: -x[4] * x[5])[:4]:
        print(f"     {x[0]} {X.iso(x[2])} {x[3]:.0f}s gap {x[4]:.3f} x {x[5]:.0f} sets")

print(f"\n(d) middle band: {mid_changes} top-of-book changes over {mid_n} market-snapshots ({mid_changes/T:,.0f}/day, "
      f"{mid_changes/max(mid_n,1):.1%} of snapshots)")
# mid-band fills by day from fills.csv (maker, priced by the snapshot p nearest)
fills = X.load_fills(); S = X.Series(snaps)
agg = defaultdict(lambda: [0, 0.0])
for f in fills:
    t = X.ts_of(f['filled_at'])
    if t < sts[0]: continue
    e = int(f['exchange_id']) if f['exchange_id'].isdigit() else f['exchange_id']
    p = S.p(e, t)
    if p is None or not (0.15 <= p <= 0.85): continue
    hh = X.iso(t)[:5]; agg[hh][0] += 1; agg[hh][1] += float(f['qty']) * float(f['fill_price'])
print("  our mid-band fills by day:", agg and {k: (v[0], round(v[1])) for k, v in agg.items()})

# ---------------- (b) journal takes
tk = defaultdict(lambda: [0, 0.0, 0.0]); blk = defaultdict(int)
rx = re.compile(r"^(\S+) .*TAKE (.+?): Polymarket ([0-9.]+) vs stale (bid|ask) ([0-9.]+) -> (buying|selling) ([0-9,]+) YES at ([0-9.]+)")
for line in X.journal_lines(r"TAKE |market-making reserve"):
    m = rx.match(line)
    hh = line[:13]
    if m:
        p, px, q = float(m.group(3)), float(m.group(8)), float(m.group(7).replace(',', ''))
        lock = px if m.group(6) == 'buying' else 1 - px
        edge = abs(p - px)
        tk[hh][0] += 1; tk[hh][1] += q * lock; tk[hh][2] += q * edge
    elif 'market-making reserve' in line:
        blk[hh] += 1
print("\n(b) journal TAKEs per hour (count, $ locked, raw-ref edge $) and reserve-blocked lines:")
for hh in sorted(set(tk) | set(blk)):
    v = tk.get(hh, [0, 0, 0])
    print(f"  {hh}: takes {v[0]:3d} ${v[1]:7,.0f} edge ${v[2]:6,.0f} | blocked {blk.get(hh,0)}")

# ---------------- (e) tilt over the day
tl = []
for line in X.journal_lines(r"\| tilt [0-9.]+%"):
    m = re.search(r"\| tilt ([0-9.]+)%, exposure ([+-][0-9.]+)k", line)
    if m: tl.append((line[:16], m.group(1), m.group(2)))
print("\n(e) tilt (journal 2-hourly line):", ", ".join(f"{a[11:16]} {b}%" for a, b, _ in tl[::2]), "| status tilt_s", st['tilt_s'])
