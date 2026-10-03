"""B_tilt: the tilt's path in detail (explorer B, Package 9). Read-only on /home/claude/snap03/md.sqlite.

Per snapshot cycle, per contract: x = ref - c, y = mid - c (c = 1/legs), keep |x| > 0.1 and spread <= 4c.
Per-contract s_i = 1 - y/x. Hourly cross-section estimates: OLS through origin, winsorised mean of s_i (5-95%),
median s_i; split longshot side (x < 0) vs favourite side (x > 0). Then: hour-of-day profile of ds, daily slope,
acceleration, a book-depth driver (cheap-side bid vs ask depth) and a saturation check (s_i by price bucket).
Writes scratch CSVs to SCR. Run: python3 analysis/p9/B_tilt.py"""
import sqlite3, json, os
import numpy as np, pandas as pd

SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/B'
os.makedirs(SCR, exist_ok=True)
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
s = pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where mode='live' and best_bid is not null "
                "and best_ask is not null and reference is not null", con)
s['race'] = s.label.str.split(n=1).str[1]
legs = pd.read_sql("select distinct eid,label from snapshots", con)
legs['race'] = legs.label.str.split(n=1).str[1]
nl = legs.groupby('race').eid.nunique()
s['c'] = 1 / s.race.map(nl)
s['mid'] = (s.best_bid + s.best_ask) / 2
s = s[(s.best_ask - s.best_bid) <= 0.04]
s['x'] = s.reference - s.c
s['y'] = s.mid - s.c
s = s[s.x.abs() > 0.1].copy()
s['si'] = 1 - s.y / s.x
s['t'] = pd.to_datetime(s.ts)
s['h'] = s.t.dt.floor('h')


def est(z):
    q = z.si.clip(z.si.quantile(0.05), z.si.quantile(0.95))
    lo = z[z.x < 0]; hi = z[z.x > 0]
    ols = lambda w: 1 - (w.x * w.y).sum() / (w.x * w.x).sum() if len(w) > 20 else np.nan
    return pd.Series({'ols': ols(z), 'wins': q.mean(), 'med': z.si.median(), 'long_ols': ols(lo), 'fav_ols': ols(hi),
                      'long_med': lo.si.median(), 'fav_med': hi.si.median(), 'n': len(z)})


g = s.groupby('h').apply(est)
g = g[g.n > 200]
g.to_csv(f'{SCR}/tilt_hourly_B.csv')
print("== 6h blocks (ols, winsorised, median, longshot-side ols, favourite-side ols)")
print(g[['ols', 'wins', 'med', 'long_ols', 'fav_ols']].resample('6h').mean().round(4).to_string())
w = g.wins
print("\n== daily mean of winsorised s:", w.resample('D').mean().round(4).to_dict())
dw = w.diff().dropna()
recent = dw[dw.index >= '2026-10-01']
print("hourly ds since 1 Oct: mean %.5f sd %.5f n %d; share of hours up %.2f" % (recent.mean(), recent.std(), len(recent), (recent > 0).mean()))
# hour-of-day profile (UTC) since 1 Oct
prof = recent.groupby(recent.index.hour).agg(['mean', 'count'])
print("\n== ds by UTC hour (since 1 Oct, x1000):")
print((prof['mean'] * 1000).round(2).to_dict())
# coarse sessions
sess = {'EU 06-12': range(6, 12), 'US day 12-18': range(12, 18), 'US eve 18-24': range(18, 24), 'night 00-06': range(0, 6)}
for k, r in sess.items():
    v = recent[recent.index.hour.isin(list(r))]
    print("%-14s ds/h %.5f (n %d)" % (k, v.mean(), len(v)))
# acceleration: rolling 12h slope
sl = []
for t in w.index[w.index >= '2026-10-01T12']:
    z = w[(w.index > t - pd.Timedelta('12h')) & (w.index <= t)].dropna()
    if len(z) > 6:
        tt = (z.index - z.index[0]).total_seconds() / 3600
        sl.append((t, np.polyfit(tt, z.values, 1)[0]))
sl = pd.Series(dict(sl))
print("\n== 12h rolling slope of s (per hour), every 6h:", sl.resample('6h').last().round(5).to_dict())
# log-growth fit vs linear fit since 1 Oct 00h
z = w[w.index >= '2026-10-01'].dropna(); tt = (z.index - z.index[0]).total_seconds() / 3600
lin = np.polyfit(tt, z.values, 1); quad = np.polyfit(tt, z.values, 2)
pos = z.values > 0.005
lg = np.polyfit(tt[pos], np.log(z.values[pos]), 1)
print("linear: %.5f/h  quad: a=%.2e b=%.5f  log-growth: %.4f/h (doubling %.1f h)" % (lin[0], quad[0], quad[1], lg[0], np.log(2) / lg[0]))
res_lin = z.values - np.polyval(lin, tt); res_q = z.values - np.polyval(quad, tt)
print("resid sd lin %.4f quad %.4f; last value %.4f at %s" % (res_lin.std(), res_q.std(), z.values[-1], z.index[-1]))

# saturation: per-contract s_i by reference bucket (latest 3 hours)
last = s[s.t >= s.t.max() - pd.Timedelta('3h')]
last = last.assign(b=pd.cut(last.reference, [0, .02, .05, .1, .2, .35, .5, .65, .8, .9, .95, .98, 1.0]))
print("\n== latest 3h: median s_i and median (mid - ref) by reference bucket")
print(last.groupby('b', observed=True).apply(lambda z: pd.Series({'s_i': z.si.median(), 'mid-ref': (z.mid - z.reference).median(),
                                                                  'n_eid': z.eid.nunique()})).round(3).to_string())
# first-hour vs last per contract for the longshots: where did the cheapest go?
first = s[(s.h >= g.index[0]) & (s.h < g.index[0] + pd.Timedelta('3h'))].groupby('eid').agg(mid0=('mid', 'mean'), ref0=('reference', 'mean'))
lastc = last.groupby('eid').agg(mid1=('mid', 'mean'), ref1=('reference', 'mean'), label=('label', 'first'), c=('c', 'first'))
j = first.join(lastc, how='inner')
ls = j[(j.ref1 < 0.05) & (j.c == 0.5)]
print("\nlongshots ref<5c (n %d): mid 1 Oct 00-03h %.3f -> now %.3f; ref %.3f -> %.3f" % (len(ls), ls.mid0.mean(), ls.mid1.mean(), ls.ref0.mean(), ls.ref1.mean()))
j.to_csv(f'{SCR}/contract_path.csv')

# depth driver: cheap-side (ref<0.1 YES) top-3 bid vs ask depth per 6h block, uncapped and capped 50k/level
b = pd.read_sql("select ts,eid,bids,asks from books", con)
refl = s.groupby('eid').reference.last()
cheap = set(refl[refl < 0.1].index)
b = b[b.eid.isin(cheap)].copy()
b['t'] = pd.to_datetime(b.ts, unit='s', utc=True)


def dep(js, cap):
    try:
        return sum(min(q, cap) for p, q in json.loads(js))
    except Exception:
        return np.nan


b['bd'] = b.bids.map(lambda v: dep(v, 1e12)); b['ad'] = b.asks.map(lambda v: dep(v, 1e12))
b['bdc'] = b.bids.map(lambda v: dep(v, 5e4)); b['adc'] = b.asks.map(lambda v: dep(v, 5e4))
b['blk'] = b.t.dt.floor('6h')
bb = b.groupby(['blk', 'eid'])[['bd', 'ad', 'bdc', 'adc']].mean().groupby('blk').sum()
bb['imb_capped'] = np.log(bb.bdc / bb.adc)
blk = w.resample('6h').mean()
bb['s'] = blk.reindex(bb.index)
bb['ds_next'] = blk.diff().shift(-1).reindex(bb.index)
print("\n== cheap-side (ref<10c) top-3 depth per 6h block (shares, millions) and log imbalance (50k cap)")
print((bb[['bd', 'ad']] / 1e6).round(2).join(bb[['imb_capped', 's', 'ds_next']].round(3)).to_string())
ok = bb.dropna()
if len(ok) > 3:
    print("corr(imb, ds_next) %.2f  n %d" % (np.corrcoef(ok.imb_capped, ok.ds_next)[0, 1], len(ok)))
