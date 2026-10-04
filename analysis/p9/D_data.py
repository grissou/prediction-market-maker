"""D_data: explorer D data checks on /home/claude/snap03 (read-only, sqlite/pandas). Sections:
 1 tick grid / min price / level sizes   2 cheap-side panel (10-min bins): s_i, price
 3 maker-short vs taker-long the tilt (24 h outcomes)   4 laggard/leader pair (tilt-neutral)   5 lead/lag tournament vs Polymarket
 6 own impact: $80k across cheap-side asks, Δs it causes; exit capacity on bad books   7 settled-rule loss of a held basket
 8 3-leg races and control markets   9 entrants per hour (rank lines)   10 mark vs mid by side (positions table)
Run: python3 analysis/p9/D_data.py"""
import sqlite3, json, gzip, re
import numpy as np, pandas as pd

SNAP = '/home/claude/snap03/'
con = sqlite3.connect(SNAP + 'md.sqlite')
pd.set_option('display.width', 200)

s = pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where ts>='2026-10-01T00:00'", con)
s['t'] = pd.to_datetime(s.ts)
s['party'] = s.label.str.split(' ').str[0]
s['race'] = s.label.str.split(' ', n=1).str[1]
legs = s.groupby('race').eid.nunique()
s['c'] = 1.0 / s.race.map(legs)

print('#1 TICK GRID / PRICES')
px = pd.concat([s.best_bid, s.best_ask]).dropna()
for g in [0.001, 0.005, 0.01]:
    print('  share of quotes on a %.3f grid: %.3f' % (g, (np.abs(px / g - np.round(px / g)) < 1e-6).mean()))
print('  min best_bid %.4f, min best_ask %.4f, max best_ask %.4f' % (s.best_bid.min(), s.best_ask.min(), s.best_ask.max()))
b = pd.read_sql("select ts,eid,bids,asks from books", con)
lv = []
for r in b.itertuples():
    for side in ('bids', 'asks'):
        for i, (p, q) in enumerate(json.loads(getattr(r, side)) or []):
            lv.append((r.ts, r.eid, side, i, p, q))
lv = pd.DataFrame(lv, columns=['ts', 'eid', 'side', 'lvl', 'p', 'q'])
print('  book levels: %d; share < 200 sh %.3f; < 50 sh %.3f; median level %.0f sh' % (len(lv), (lv.q < 200).mean(), (lv.q < 50).mean(), lv.q.median()))
print('  level-0 < 200 sh share: %.3f' % (lv[lv.lvl == 0].q < 200).mean())

print('\n#2 CHEAP-SIDE PANEL')
s['mid'] = (s.best_bid + s.best_ask) / 2
s['spr'] = s.best_ask - s.best_bid
s['bin'] = s.t.dt.floor('10min')
p = s.dropna(subset=['mid', 'reference']).groupby(['bin', 'eid']).agg(mid=('mid', 'last'), bid=('best_bid', 'last'), ask=('best_ask', 'last'),
                                                                      ref=('reference', 'last'), c=('c', 'last'), label=('label', 'last')).reset_index()
p['gap'] = p.ref - p.c
cheap = p[(p.ref < p.c - 0.1) & (p.ref < 0.10)].copy()
cheap['si'] = 1 - (cheap.mid - cheap.c) / (cheap.ref - cheap.c)
print('  cheap-side contracts (ref < 0.10 and < c - 0.1):', cheap.eid.nunique())

print('\n#3 MAKER-SHORT vs TAKER-LONG on cheap sides, 24 h later (bins on 2-3 Oct)')
W = cheap.pivot(index='bin', columns='eid', values='mid')
A = cheap.pivot(index='bin', columns='eid', values='ask'); B = cheap.pivot(index='bin', columns='eid', values='bid')
for hrs in [6, 24]:
    k = hrs * 6
    fut_mid = W.shift(-k); fut_bid = B.shift(-k)
    long_taker = ((fut_bid - A) / A).stack()  # buy the ask now, sell the bid later
    long_mark = ((fut_mid - A) / A).stack()
    short_maker = ((A - fut_mid) / A).stack()   # sell at the ask as a maker (assumes filled), value at the later mid
    print('  +%2dh: long taker (ask->bid) mean %+.3f med %+.3f | long taker marked at mid %+.3f | short maker at ask %+.3f (n %d)'
          % (hrs, long_taker.mean(), long_taker.median(), long_mark.mean(), short_maker.mean(), len(long_taker)))
# by day of entry
for day in ['2026-10-01', '2026-10-02', '2026-10-03']:
    sub = W.loc[day]
    if len(sub) > 40:
        r6 = ((W.shift(-36) - A) / A).loc[day].stack()
        print('  entry day %s: +6h long-taker-at-mid mean %+.3f' % (day, r6.mean()))

print('\n#4 LAGGARD-vs-LEADER PAIR (tilt-neutral): quartiles of s_i at t, price return to t+24h')
SI = cheap.pivot(index='bin', columns='eid', values='si')
R = (W.shift(-144) - W) / W
out = []
for t in SI.index[::6]:
    row = SI.loc[t].dropna()
    if len(row) < 20 or t not in R.index:
        continue
    q1, q3 = row.quantile(0.25), row.quantile(0.75)
    lo = R.loc[t, row[row <= q1].index].mean(); hi = R.loc[t, row[row >= q3].index].mean()
    out.append((t, lo, hi))
o = pd.DataFrame(out, columns=['t', 'laggards', 'leaders']).dropna()
print('  n %d starts; laggards mean %+.3f, leaders %+.3f, long-lag/short-lead %+.3f (sd %.3f, share > 0 %.2f)'
      % (len(o), o.laggards.mean(), o.leaders.mean(), (o.laggards - o.leaders).mean(), (o.laggards - o.leaders).std(),
         ((o.laggards - o.leaders) > 0).mean()))

print('\n#5 LEAD/LAG: does the tournament lead Polymarket or vice versa? (all contracts, 10-min bins)')
M = p.pivot(index='bin', columns='eid', values='mid'); F = p.pivot(index='bin', columns='eid', values='ref')
dM = M.diff(); dF = F.diff()
for k in [0, 1, 3, 6, 18]:
    a = pd.concat([dM.stack(), dF.shift(k).stack()], axis=1).dropna()
    bb = pd.concat([dF.stack(), dM.shift(k).stack()], axis=1).dropna()
    big = a[np.abs(a.iloc[:, 1]) >= 0.02]
    print('  k=%2d bins: corr(dMid_t, dRef_t-k) %+.3f | corr(dRef_t, dMid_t-k) %+.3f | pass-through after |dRef|>=2c: %.2f (n %d)'
          % (k, a.corr().iloc[0, 1], bb.corr().iloc[0, 1], (big.iloc[:, 0] / big.iloc[:, 1]).median(), len(big)))
# cumulative pass-through of big Polymarket jumps
jumps = dF.stack()[lambda x: np.abs(x) >= 0.03]
cum = []
for (t, e), j in jumps.items():
    i = M.index.get_loc(t)
    if i + 36 < len(M) and i >= 1:
        cum.append([(M[e].iloc[i + h] - M[e].iloc[i - 1]) / j for h in [0, 1, 6, 36]])
cum = np.array(cum)
if len(cum):
    print('  Polymarket jumps >= 3c: n %d, tournament move / jump at +0, +10m, +1h, +6h: %s' % (len(cum), np.round(np.nanmedian(cum, axis=0), 2)))

print('\n#6 OWN IMPACT and EXIT CAPACITY (books table, cheap sides = latest ref < 0.10)')
last_ref = p.sort_values('bin').groupby('eid').last()
cheap_eids = set(last_ref[(last_ref.ref < 0.10) & (last_ref.ref < last_ref.c - 0.1)].index)
lv['dollars'] = lv.p * lv.q
lvc = lv[lv.eid.isin(cheap_eids)]
lastts = lvc.groupby('eid').ts.max()
latest = lvc[lvc.ts == lvc.eid.map(lastts)]
asks = latest[latest.side == 'asks']; bids = latest[latest.side == 'bids']
print('  latest: %d cheap contracts with books; top-3 asks $%.0f (%.0f sh), bids $%.0f' % (latest.eid.nunique(), asks.dollars.sum(), asks.q.sum(), bids.dollars.sum()))
# buy fraction f of every contract's top-3 asks: $ spent, mid move, ds
lr = last_ref
for f in [0.25, 0.5, 0.75, 1.0]:
    spent = 0; dsl = []; w = []
    for e, g in asks.groupby('eid'):
        g = g.sort_values('p'); need = f * g.q.sum(); took = 0; lastp = g.p.iloc[0]
        for r in g.itertuples():
            q = min(r.q, need - took)
            if q <= 0: break
            spent += q * r.p; took += q; lastp = r.p
        bb = bids[bids.eid == e].p.max()
        if np.isnan(bb): continue
        mid0 = (bb + g.p.iloc[0]) / 2; mid1 = (bb + lastp) / 2 if f < 1 else (g.p.iloc[0] + g.p.max()) / 2 + 0.0025  # asks gone: new best bid ~ old best ask
        if f >= 1: mid1 = (g.p.iloc[0] + g.p.max() + 0.005) / 2
        c = lr.loc[e, 'c']; ref = lr.loc[e, 'ref']
        dsl.append((mid1 - mid0) / (c - ref)); w.append(1)
    print('  buy %3.0f%% of top-3 asks: $%.0f spent; mean mid move -> ds %+.3f (s ~0.12: %.0f%% of the tilt)' % (100 * f, spent, np.mean(dsl), 100 * np.mean(dsl) / 0.12))
# exit capacity through time: $ bids within 1c of best bid on cheap sides, per books timestamp (books rounded to 30 min)
lvc2 = lvc[lvc.side == 'bids'].copy()
lvc2['bb'] = lvc2.groupby(['ts', 'eid']).p.transform('max')
lvc2 = lvc2[lvc2.p >= lvc2.bb - 0.01 - 1e-9]
lvc2['hb'] = pd.to_datetime(lvc2.ts, unit='s').dt.floor('2h')
per = lvc2.groupby(['hb', 'eid']).apply(lambda g: g[g.ts == g.ts.max()].dollars.sum()).groupby('hb').sum()
per = per[per.index >= '2026-10-01']
print('  $ bids within 1c of best on cheap sides per 2-h block: min %.0f p10 %.0f median %.0f max %.0f (blocks %d)'
      % (per.min(), per.quantile(0.1), per.median(), per.max(), len(per)))
print('  worst blocks:', {str(k)[5:16]: int(v) for k, v in per.nsmallest(3).items()})

print('\n#7 SETTLED RULE: a held equal-$ basket of all cheap sides at the current mid -> settlement value (at Polymarket odds)')
lc = last_ref.loc[list(cheap_eids)]
ratio = (lc.ref / lc.mid)
print('  n %d, settlement value per $ (mean ref/mid) %.3f -> 80k basket settles at %.1fk (loss %.1fk)' % (len(lc), ratio.mean(), 80 * ratio.mean(), 80 * (1 - ratio.mean())))
for s2 in [0.2, 0.3]:
    m2 = lc.c - (1 - s2) * (lc.c - lc.ref)
    print('  if bought later at s %.1f: per $ %.3f -> 80k basket settles at %.1fk' % (s2, (lc.ref / m2).mean(), 80 * (lc.ref / m2).mean()))
lev = ((lc.c - lc.ref) / lc.mid)
print('  dP/ds per $ of price (leverage): median %.1f, top-10 mean %.1f, bottom-10 %.1f' % (lev.median(), lev.nlargest(10).mean(), lev.nsmallest(10).mean()))

print('\n#8 3-LEG RACES and CONTROL MARKETS (latest)')
three = last_ref[last_ref.c < 0.4]
print(three[['label', 'bid', 'ask', 'ref', 'c']].to_string())
ctrl = last_ref[last_ref.label.str.contains('U.S.')]
ctrl = ctrl.assign(si=1 - (ctrl.mid - ctrl.c) / (ctrl.ref - ctrl.c))
print(ctrl[['label', 'mid', 'ref', 'si']].to_string())

print('\n#9 ENTRANTS (rank lines) and Smart Score')
with gzip.open(SNAP + 'journal_2026-10-02_2037_to_now.log.gz', 'rt') as f:
    for line in f:
        if 'Rank ' in line and 'Smart' in line:
            print('  ', line[:19], re.search(r'Rank.*', line).group(0))

print('\n#10 MARK vs MID by side (positions current_price vs snapshot mid, 3 Oct)')
pos = pd.read_sql("select ts,eid,quantity,current_price from positions where current_price is not null", con)
pos['bin'] = pd.to_datetime(pos.ts, unit='s', utc=True).dt.floor('10min').dt.tz_localize(None)
pm = p.copy(); pm['bin'] = pd.to_datetime(pm.bin).dt.tz_localize(None)
j = pos.merge(pm[['bin', 'eid', 'mid', 'ref', 'c']], on=['bin', 'eid'])
j = j[j.bin >= '2026-10-03']
j['side'] = np.where(j.ref < j.c - 0.1, 'cheap', np.where(j.ref > j.c + 0.1, 'fav', 'mid'))
j['prem'] = (j.current_price - j.mid)
print(j.groupby('side').prem.describe()[['count', 'mean', '50%']].round(4).to_string())
j['rel'] = j.prem / j.mid
print('  cheap side mark premium as share of mid: mean %+.3f median %+.3f' % (j[j.side == 'cheap'].rel.mean(), j[j.side == 'cheap'].rel.median()))
