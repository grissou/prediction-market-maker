"""B_mark2: timing structure of the exchange mark: are changes batched across markets (periodic recompute) or per trade?
Per poll: share of held eids whose mark changed since the previous poll; inter-change intervals per eid; minute-of-hour profile."""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
p = pd.read_sql("select ts,eid,quantity q,current_price m from positions where current_price is not null and quantity!=0", con)
p = p.sort_values(['eid', 'ts']); p['m0'] = p.groupby('eid').m.shift(); p['ts0'] = p.groupby('eid').ts.shift()
p = p.dropna(); p = p[(p.ts - p.ts0) < 300]
p['chg'] = (p.m - p.m0).abs() > 5e-5
per = p.groupby('ts').agg(n=('chg', 'size'), k=('chg', 'sum'))
per = per[per.n >= 50]; per['frac'] = per.k / per.n
print("polls with >=50 held: %d; frac changed per poll quantiles:" % len(per), per.frac.quantile([.1, .25, .5, .75, .9, .99]).round(3).to_dict())
print("polls with frac>0.5: %d ; frac==0: %d" % ((per.frac > .5).sum(), (per.frac == 0).sum()))
dt = pd.Series(per.index).diff().describe(); print("poll interval s:", dt.round(1).to_dict())
t = pd.to_datetime(per.index, unit='s')
per['min'] = t.minute; per['hr'] = t.hour
print("mean frac changed by minute-of-hour (5-min bins):", per.groupby(per['min'] // 5 * 5).frac.mean().round(3).to_dict())
print("by UTC hour:", per.groupby('hr').frac.mean().round(3).to_dict())
# per eid inter-change time
ch = p[p.chg]
ic = ch.groupby('eid').ts.diff().dropna() / 60
print("inter-change minutes per eid: quantiles", ic.quantile([.1, .25, .5, .75, .9]).round(1).to_dict())
# change size distribution and on-grid?
d = (ch.m - ch.m0)
print("|dm| quantiles (c):", (d.abs() * 100).quantile([.1, .25, .5, .75, .9, .99]).round(3).to_dict())
on = ((p.m * 1000).round(6) % 5 == 0).mean(); print("share of marks on the 0.5c grid: %.3f" % on)
# full polls: latest with many eids
cnt = pd.read_sql("select ts,count(*) n from positions where quantity!=0 group by ts order by ts desc limit 20", con); print(cnt.head(8).to_string())
