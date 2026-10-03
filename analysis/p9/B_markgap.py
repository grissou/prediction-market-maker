"""B_markgap: at each FULL positions poll (>= 80 held), sum q x (mark - mid), q x (mark - liquidation side), split into
cheap-side holdings (long the tilt) and toward-Polymarket holdings (short the tilt); the latest poll's top/bottom lines;
and a longshot-only lag check: for YES contracts with ref < 10c, mark - mid by hour (rising tilt => mark below mid)."""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
p = pd.read_sql("select ts,eid,quantity q,current_price m from positions where current_price is not null and quantity!=0", con)
cnt = p.groupby('ts').size(); full = cnt[cnt >= 80].index
p = p[p.ts.isin(full)].copy(); p['eid'] = p.eid.astype(str)
p['t'] = pd.to_datetime(p.ts, unit='s', utc=True).astype('datetime64[ns, UTC]')
s = pd.read_sql("select ts,eid,label,best_bid bb,best_ask ba,reference ref from snapshots where mode='live' and best_bid is not null and best_ask is not null", con)
s['t'] = pd.to_datetime(s.ts, utc=True).astype('datetime64[ns, UTC]'); s['eid'] = s.eid.astype(str)
s['race'] = s.label.str.split(n=1).str[1]
nl = s.groupby('race').eid.nunique(); s['c'] = 1 / s.race.map(nl)
m = pd.merge_asof(p.sort_values('t'), s[['t', 'eid', 'bb', 'ba', 'ref', 'c', 'label']].sort_values('t'), on='t', by='eid', direction='backward', tolerance=pd.Timedelta('3min')).dropna(subset=['bb'])
m['mid'] = (m.bb + m.ba) / 2; m['liq'] = np.where(m.q > 0, m.bb, m.ba)
m['gm'] = m.q * (m.m - m.mid); m['gl'] = m.q * (m.m - m.liq)
m['X'] = m.q * (m.ref - m.c)            # tilt exposure: > 0 toward Polymarket (short the tilt)
m['side'] = np.where(m.X > 0, 'toward', 'cheap')
agg = m.groupby(['ts', 'side']).gm.sum().unstack().fillna(0)
agg['all'] = agg.sum(axis=1); agg['liq_all'] = m.groupby('ts').gl.sum(); agg['X'] = m.groupby('ts').X.sum()
agg.index = pd.to_datetime(agg.index, unit='s')
print("== full polls: sum q(mark - mid) by side, all, sum q(mark - liq), tilt exposure X")
print(agg.round(0).to_string())
last = m[m.ts == m.ts.max()]
print("\nlatest full poll %s: n %d" % (pd.to_datetime(last.ts.iloc[0], unit='s'), len(last)))
print("toward lines: gap %.0f over %d ; cheap lines: gap %.0f over %d" % (last[last.side == 'toward'].gm.sum(), (last.side == 'toward').sum(), last[last.side == 'cheap'].gm.sum(), (last.side == 'cheap').sum()))
print("lines with mark above liquidation price (selling now books a mark loss): %d, %.0f; below: %d, %.0f" % ((last.gl > 0).sum(), last.gl[last.gl > 0].sum(), (last.gl < 0).sum(), last.gl[last.gl < 0].sum()))
cols = ['label', 'q', 'm', 'bb', 'ba', 'ref', 'gm', 'gl']
print("\nmost below mid:"); print(last.sort_values('gm')[cols].head(8).round(3).to_string(index=False))
print("\nmost above mid:"); print(last.sort_values('gm')[cols].tail(6).round(3).to_string(index=False))
# all-market longshot lag: positions only exist for held eids, so use held YES-equivalents with ref<0.1
ls = m[m.ref < 0.1]
print("\nheld contracts with ref<10c: mean (mark - mid) by full poll, cents:")
print((ls.groupby(pd.to_datetime(ls.ts, unit='s').dt.floor('2h')).apply(lambda z: (z.m - z.mid).mean()) * 100).round(2).to_dict())
fv = m[m.ref > 0.9]
print("held contracts with ref>90c: mean (mark - mid) cents:", (fv.groupby(pd.to_datetime(fv.ts, unit='s').dt.floor('2h')).apply(lambda z: (z.m - z.mid).mean()) * 100).round(2).to_dict())
