"""A_imbalance: cheap-side book imbalance (longshot YES bid depth / ask depth, 3 levels, ref<0.1) per 6h vs the next 6h change of the hourly tilt (A_tilt_path output)."""
import sqlite3,pandas as pd,numpy as np,json
SCR='/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/'
c=sqlite3.connect('/home/claude/snap03/md.sqlite')
b=pd.read_sql("select * from books",c); b['t']=pd.to_datetime(b.ts,unit='s',utc=True)
ref=pd.read_sql("select eid,avg(reference) r from snapshots where ts>'2026-10-02' group by eid",c).set_index('eid').r
b=b[b.eid.map(ref)<0.1]
b['bd']=b.bids.map(lambda s:sum(min(q,50000) for p,q in json.loads(s)))   # cap single walls at 50k
b['ad']=b.asks.map(lambda s:sum(min(q,50000) for p,q in json.loads(s)))
g=b.groupby(b.t.dt.floor('6h')).agg(bd=('bd','sum'),ad=('ad','sum'),n=('eid','size')); g['imb']=np.log(g.bd/g.ad)
tl=pd.read_csv(SCR+'tilt_hourly.csv',parse_dates=['h']).set_index('h').s.resample('6h').mean()
g=g.join(tl.rename('s')); g['ds_next']=g.s.shift(-1)-g.s
print(g.round(3).to_string()); x=g.dropna(); print("corr(imb, next ds)",np.corrcoef(x.imb,x.ds_next)[0,1].round(2),"n",len(x))
