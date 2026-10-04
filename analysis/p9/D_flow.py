"""D_flow: what moved during the fastest-entrant block (3 Oct 18:00-22:45, ~10 entrants/h) vs the slower block (10:00-18:00,
~4.5/h): mean mid change by group (cheap side by ref bucket, party-name vs independent, control markets, favourites).
Also: hourly cheap-side median price change by UTC hour on 1-3 Oct (calendar). Run: python3 analysis/p9/D_flow.py"""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
s = pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where ts>='2026-10-01'", con)
s['t'] = pd.to_datetime(s.ts); s['mid'] = (s.best_bid + s.best_ask) / 2
s['race'] = s.label.str.split(' ', n=1).str[1]; s['c'] = 1 / s.race.map(s.groupby('race').eid.nunique())
s = s.dropna(subset=['mid', 'reference'])
def at(ts):
    x = s[(s.t <= pd.Timestamp(ts, tz='UTC'))]
    return x.sort_values('t').groupby('eid').last()
a, b, c = at('2026-10-03T10:00'), at('2026-10-03T18:00'), at('2026-10-03T22:45')
d = c[['label', 'reference', 'c', 'mid']].rename(columns={'mid': 'm22'}).join(b.mid.rename('m18')).join(a.mid.rename('m10'))
d['grp'] = np.select([d.label.str.contains('U.S.'), d.label.str.startswith('Ind'), d.reference < 0.03, d.reference < 0.10,
                      d.reference > d.c + 0.1], ['control', 'independent', 'cheap<3c', 'cheap3-10c', 'favourite'], 'middle')
d['slow_pct_per_h'] = (d.m18 / d.m10 - 1) / 8; d['fast_pct_per_h'] = (d.m22 / d.m18 - 1) / 4.75
print(d.groupby('grp')[['slow_pct_per_h', 'fast_pct_per_h']].agg(['mean', 'count']).round(4).to_string())
print('top 8 movers 18:00-22:45 (% per h):')
print(d.sort_values('fast_pct_per_h', ascending=False).head(8)[['label', 'reference', 'm18', 'm22', 'fast_pct_per_h']].round(4).to_string())
# calendar: cheap-side median log price change per UTC hour
ch = s[(s.reference < 0.10) & (s.reference < s.c - 0.1)].copy(); ch['h'] = ch.t.dt.floor('h')
hp = ch.groupby(['h', 'eid']).mid.last().unstack()
r = np.log(hp).diff().median(axis=1)
prof = r.groupby(r.index.hour).mean()
print('cheap-side median log-price change by UTC hour (1-3 Oct):')
print(' '.join('%02d:%+.3f' % (h, v) for h, v in prof.items()))
print('by day: ', {str(k.date()): round(v, 3) for k, v in r.groupby(r.index.floor('D')).sum().items()})
