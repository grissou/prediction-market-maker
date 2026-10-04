"""A_tilt_path: hourly tilt s from snapshots: (mid - c) = (1 - s)(ref - c), OLS through origin, |ref - c| > 0.1, spread <= 3c."""
import sqlite3,pandas as pd,numpy as np
c=sqlite3.connect('/home/claude/snap03/md.sqlite')
s=pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where best_bid is not null and best_ask is not null and reference is not null",c)
s['race']=s.label.str.split(n=1).str[1]
legs=s.groupby('race').eid.nunique(); s['c']=1/s.race.map(legs)
s['mid']=(s.best_bid+s.best_ask)/2; s=s[(s.best_ask-s.best_bid)<=0.03]
s['x']=s.reference-s.c; s['y']=s.mid-s.c; s=s[s.x.abs()>0.1]
s['h']=pd.to_datetime(s.ts).dt.floor('h')
g=s.groupby('h').apply(lambda z: pd.Series({'s':1-(z.x*z.y).sum()/(z.x*z.x).sum(),'n':len(z)}))
g=g[g.n>200]
print(g.resample('6h').s.mean().round(4).to_string())
d=g.s.resample('D').mean(); print("daily",d.round(4).to_dict())
hs=g.s.dropna(); dh=hs.diff().dropna()
print("hourly ds: mean %.4f sd %.4f n %d; 6h-ds sd %.4f; max 6h drop %.4f"%(dh.mean(),dh.std(),len(dh),hs.resample('6h').mean().diff().std(),hs.resample('6h').mean().diff().min()))
print("last 24h hourly s:",hs[-24:].round(3).tolist())
g.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/tilt_hourly.csv')
