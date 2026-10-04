"""B_mark3: delayed response of the exchange mark to our larger fills (qty >= 100, |px - mark| > 0.5c; YES price = quote_price):
mark change from just before the fill to +k minutes, as a fraction of (px - mark); and the same for the book mid."""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
p = pd.read_sql("select ts,eid,current_price m from positions where current_price is not null", con)
p['eid'] = p.eid.astype(str); p = p.sort_values('ts')
f = pd.read_csv('/home/claude/snap03/fills.csv'); f['eid'] = f.exchange_id.astype(str)
f['ts'] = pd.to_datetime(f.filled_at, utc=True).astype('datetime64[ns, UTC]').astype('int64') / 1e9
f = f[f.ts >= p.ts.min() + 600]
# bursts: one per eid per minute
f['mn'] = (f.ts // 60).astype(int)
f['yp'] = f.quote_price.where(f.quote_price.notna(), f.fill_price)
b = f.groupby(['eid', 'mn']).agg(ts=('ts', 'min'), qty=('qty', 'sum'), fp=('yp', 'mean')).reset_index()
def asof(eid, t):
    z = P.get(eid)
    if z is None: return np.nan
    i = np.searchsorted(z[0], t, side='right') - 1
    return z[1][i] if i >= 0 and t - z[0][i] < 900 else np.nan
P = {e: (g.ts.values, g.m.values) for e, g in p.groupby('eid')}
b['m0'] = [asof(e, t - 30) for e, t in zip(b.eid, b.ts)]
b = b.dropna(subset=['m0'])
b['px'] = np.where((b.fp - b.m0).abs() <= (1 - b.fp - b.m0).abs(), b.fp, 1 - b.fp)
b['gap'] = b.px - b.m0
b = b[(b.qty >= 100) & (b.gap.abs() > 0.005)]
print("bursts n %d, median qty %.0f, median |gap| %.2fc" % (len(b), b.qty.median(), 100 * b.gap.abs().median()))
for k in [1, 5, 15, 30, 60, 120]:
    m1 = np.array([asof(e, t + 60 * k) for e, t in zip(b.eid, b.ts)])
    fr = (m1 - b.m0) / b.gap
    print("+%3d min: median dm/gap %.3f, mean %.3f, same sign %.0f%% (n %d)" % (k, np.nanmedian(fr), np.nanmean(np.clip(fr, -2, 2)), 100 * np.nanmean(np.sign(m1 - b.m0) == np.sign(b.gap)), np.isfinite(fr).sum()))
