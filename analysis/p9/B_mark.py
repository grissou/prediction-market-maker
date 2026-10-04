"""B_mark: fit the exchange's mark (positions.current_price) against the book mid and our own fills.

1) Lag scan: RMS(mark_t - mid_{t-L}) over L = 0..480 min, and an EWMA of the mid with half-life H, per held eid.
2) Change events: does the mark move between two polls with none of OUR fills in between (others' trades / ageing)?
3) Fill impact: for polls bracketing exactly one of our fill bursts, dm = m1 - m0 vs q x (p - m0): fits
   dm = q (p - m0) / (V + q) (a volume window of V shares) vs dm = (p - p_out)/N (last-N average).
4) Today's gap: sum q x (mark - mid) and q x (mark - bid/ask liquidation) per position, and by tilt side.
All prices in YES terms (quantity < 0 = NO). Run: python3 analysis/p9/B_mark.py"""
import sqlite3, json
import numpy as np, pandas as pd

SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/B'
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
p = pd.read_sql("select ts,eid,quantity q,current_price m from positions where current_price is not null and quantity != 0", con)
p['t'] = pd.to_datetime(p.ts, unit='s', utc=True).astype('datetime64[ns, UTC]')
p['eid'] = p.eid.astype(str)
s = pd.read_sql("select ts,eid,label,best_bid bb,best_ask ba,reference ref from snapshots where mode='live' and best_bid is not null and best_ask is not null", con)
s['t'] = pd.to_datetime(s.ts, utc=True).astype('datetime64[ns, UTC]')
s['eid'] = s.eid.astype(str)
s['mid'] = (s.bb + s.ba) / 2
s = s.sort_values('t')
p = p.sort_values('t')
print("positions rows %d eids %d, polls %d, span %s -> %s" % (len(p), p.eid.nunique(), p.ts.nunique(), p.t.min(), p.t.max()))

# 1) lag scan
res = {}
for L in [0, 5, 15, 30, 45, 60, 90, 120, 180, 240, 360, 480]:
    pp = p.assign(tq=p.t - pd.Timedelta(minutes=L)).sort_values('tq')
    m = pd.merge_asof(pp, s[['t', 'eid', 'mid']].rename(columns={'t': 'tq'}), on='tq', by='eid', direction='backward', tolerance=pd.Timedelta('3min'))
    d = (m.m - m.mid).dropna()
    res[L] = (np.sqrt((d ** 2).mean()) * 100, d.abs().median() * 100, len(d))
print("\n== lag scan: L(min) -> RMS c, median |gap| c, n")
for k, v in res.items():
    print("%4d  %.3f  %.3f  %d" % (k, *v))

# EWMA of mid in time (per eid, resampled to 1 min)
mids = s.pivot_table(index='t', columns='eid', values='mid').resample('1min').last().ffill(limit=10)
print("\n== EWMA half-life scan (minutes): RMS c, median |gap| c")
pm = p.assign(tm=p.t.dt.floor('1min'))
for H in [15, 30, 60, 120, 240, 480]:
    e = mids.ewm(halflife=pd.Timedelta(minutes=H), times=mids.index).mean() if False else mids.ewm(halflife=H).mean()
    st = e.stack().rename('ew').reset_index().rename(columns={'t': 'tm'})
    m = pm.merge(st, on=['tm', 'eid'], how='inner')
    d = m.m - m.ew
    print("%4d  %.3f  %.3f  n %d" % (H, np.sqrt((d ** 2).mean()) * 100, d.abs().median() * 100, len(d)))

# 2) change events vs our fills
f = pd.read_csv('/home/claude/snap03/fills.csv')
f['t'] = pd.to_datetime(f.filled_at, utc=True).astype('datetime64[ns, UTC]')
f['eid'] = f.exchange_id.astype(str)
p['eid'] = p.eid.astype(str)
p = p.sort_values(['eid', 't'])
p['m0'] = p.groupby('eid').m.shift(); p['t0'] = p.groupby('eid').t.shift(); p['q0'] = p.groupby('eid').q.shift()
ev = p.dropna(subset=['m0']).copy()
ev = ev[(ev.t - ev.t0) < pd.Timedelta('5min')]
ev['chg'] = (ev.m - ev.m0).abs() > 5e-5
ev['qchg'] = (ev.q != ev.q0)
fg = f.groupby('eid')
def nfill(r):
    if r.eid not in fg.groups: return 0
    z = fg.get_group(r.eid)
    return int(((z.t > r.t0) & (z.t <= r.t)).sum())
evs = ev.sample(min(len(ev), 40000), random_state=1) if len(ev) > 40000 else ev
evs = evs.assign(nf=evs.apply(nfill, axis=1))
evs['ours'] = (evs.nf > 0) | evs.qchg
print("\n== poll pairs (<5 min apart): %d; mark changed in %.1f%%" % (len(evs), 100 * evs.chg.mean()))
print("changed | our fill/qty change in interval: %.1f%%; changed | no fill of ours: %.1f%%" % (
    100 * evs[evs.ours].chg.mean(), 100 * evs[~evs.ours].chg.mean()))
print("share of all mark changes with no fill of ours (others' trades or ageing): %.1f%%" % (100 * (evs.chg & ~evs.ours).sum() / max(evs.chg.sum(), 1)))
# do the no-own-fill changes come with a book move? (ageing would move the mark with a static book)
bk = s[['t', 'eid', 'mid']].copy(); bk['eid'] = bk.eid.astype(str)
a = evs[evs.chg & ~evs.ours].copy()
a1 = pd.merge_asof(a.sort_values('t'), bk.sort_values('t'), on='t', by='eid', direction='backward', tolerance=pd.Timedelta('3min')).rename(columns={'mid': 'mid1'})
a1 = pd.merge_asof(a1.assign(tt=a1.t0).sort_values('tt'), bk.rename(columns={'t': 'tt', 'mid': 'mid0'}).sort_values('tt'), on='tt', by='eid', direction='backward', tolerance=pd.Timedelta('3min'))
a1['dmid'] = a1.mid1 - a1.mid0; a1['dm'] = a1.m - a1.m0
a1 = a1.dropna(subset=['dmid'])
print("no-own-fill changes: n %d, book mid unchanged in %.1f%%; corr(dm, dmid) %.2f; median |dm| %.3fc; sign(dm)==sign(mid0-m0) %.1f%%" % (
    len(a1), 100 * (a1.dmid.abs() < 1e-9).mean(), a1[['dm', 'dmid']].corr().iloc[0, 1], 100 * a1.dm.abs().median(),
    100 * (np.sign(a1.dm) == np.sign(a1.mid0 - a1.m0)).mean()))

# 3) fill impact: intervals with our fills (one eid), the mark change vs fill size and distance
b = evs[(evs.nf > 0)].copy()
rows = []
for r in b.itertuples():
    z = fg.get_group(r.eid); z = z[(z.t > r.t0) & (z.t <= r.t)]
    qty = z.qty.sum()
    px = []
    for fp, qq in zip(z.fill_price, z.qty):
        yp = fp if abs(fp - r.m0) <= abs(1 - fp - r.m0) else 1 - fp
        px.append(yp)
    vw = np.average(px, weights=z.qty)
    rows.append((r.eid, r.m0, r.m, qty, vw, len(z)))
fi = pd.DataFrame(rows, columns=['eid', 'm0', 'm1', 'qty', 'px', 'nf'])
fi['dm'] = fi.m1 - fi.m0; fi['gap'] = fi.px - fi.m0
fi = fi[fi.gap.abs() > 0.003]
fi['frac'] = fi.dm / fi.gap      # = qty/(V+qty) under a volume window
fi['Vimp'] = fi.qty * (1 / fi.frac - 1)
ok = fi[(fi.frac > 0.001) & (fi.frac < 1)]
print("\n== our-fill intervals with |px - mark| > 0.3c: n %d; dm/gap median %.3f (IQR %.3f-%.3f); dm same sign as gap %.1f%%" % (
    len(fi), fi.frac.median(), fi.frac.quantile(.25), fi.frac.quantile(.75), 100 * (np.sign(fi.dm) == np.sign(fi.gap)).mean()))
print("implied window volume V = qty (1/frac - 1): median %.0f shares (IQR %.0f-%.0f), n %d" % (ok.Vimp.median(), ok.Vimp.quantile(.25), ok.Vimp.quantile(.75), len(ok)))
# frac rises with qty? (volume window) or flat (last-N)?
fi['qb'] = pd.qcut(fi.qty, 4, duplicates='drop')
print(fi.groupby('qb', observed=True).frac.median().round(3).to_dict())
print("median per-fill-interval |dm| %.3fc, median qty %.0f" % (100 * fi.dm.abs().median(), fi.qty.median()))
fi.to_csv(f'{SCR}/fill_impact.csv', index=False)

# 4) the gap now
lastp = pd.read_sql("select eid,quantity q,current_price m from positions where ts=(select max(ts) from positions) and quantity!=0", con)
lastp['eid'] = lastp.eid.astype(str)
ls = s[s.t >= s.t.max() - pd.Timedelta('2min')].groupby('eid').agg(bb=('bb', 'last'), ba=('ba', 'last'), mid=('mid', 'last'), ref=('ref', 'last'), label=('label', 'last'))
ls.index = ls.index.astype(str)
g = lastp.join(ls, on='eid').dropna(subset=['mid'])
g['liq'] = np.where(g.q > 0, g.bb, g.ba)
g['gap_mid'] = g.q * (g.m - g.mid); g['gap_liq'] = g.q * (g.m - g.liq)
g['val'] = np.where(g.q > 0, g.q * g.m, -g.q * (1 - g.m))
print("\n== now: %d positions, sum q(mark - mid) %.0f, sum q(mark - liquidation) %.0f, value at mark %.0f" % (len(g), g.gap_mid.sum(), g.gap_liq.sum(), g.val.sum()))
print("positions marked ABOVE mid (good for us): %d, +%.0f ; BELOW: %d, %.0f" % ((g.gap_mid > 0).sum(), g.gap_mid[g.gap_mid > 0].sum(), (g.gap_mid < 0).sum(), g.gap_mid[g.gap_mid < 0].sum()))
print("marked above liquidation (exit would book a loss vs mark): %d, +%.0f; below liquidation: %d, %.0f" % ((g.gap_liq > 0).sum(), g.gap_liq[g.gap_liq > 0].sum(), (g.gap_liq < 0).sum(), g.gap_liq[g.gap_liq < 0].sum()))
g['side'] = np.where(g.q * (g.ref - 0.5) > 0, 'toward-fav/longshotNO', 'cheap-side')
print(g.groupby(g.q > 0).gap_mid.sum().round(0).to_dict(), "(True = YES held)")
print("\nworst 10 below mid:"); print(g.sort_values('gap_mid')[['label', 'q', 'm', 'bb', 'ba', 'ref', 'gap_mid', 'gap_liq']].head(10).round(3).to_string())
print("\ntop 8 above mid:"); print(g.sort_values('gap_mid')[['label', 'q', 'm', 'bb', 'ba', 'ref', 'gap_mid', 'gap_liq']].tail(8).round(3).to_string())
g.to_csv(f'{SCR}/mark_gap_now.csv', index=False)
