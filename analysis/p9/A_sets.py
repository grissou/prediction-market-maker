"""A_sets: riskless sets over history. For each race and snapshot: sum of YES asks (<1 = buy a YES set below 1) and sum of YES bids (>1 = sell a set / buy NO set below legs-1)."""
import sqlite3,pandas as pd,numpy as np
c=sqlite3.connect('/home/claude/snap03/md.sqlite')
s=pd.read_sql("select ts,eid,label,best_bid,best_ask from snapshots where mode='live'",c)
s['race']=s.label.str.split(n=1).str[1]
g=s.groupby(['ts','race']).agg(n=('eid','size'),sa=('best_ask','sum'),sb=('best_bid','sum'),na=('best_ask','count'),nb=('best_bid','count'))
legs=s.groupby('race').eid.nunique()
g=g.join(legs.rename('legs'),on='race')
g=g[(g.n==g.legs)]
ya=g[(g.na==g.n)&(g.sa<0.995)]; yb=g[(g.nb==g.n)&(g.sb>1.005)]
print("snapshots",g.index.get_level_values(0).nunique())
print("YES set below 0.995: race-snapshots",len(ya),"races",ya.index.get_level_values(1).nunique(),"min sum",ya.sa.min() if len(ya) else None)
print(ya.reset_index().groupby('race').agg(k=('sa','size'),min=('sa','min')).sort_values('k',ascending=False).head(10))
print("bids sum above 1.005:",len(yb),"races",yb.index.get_level_values(1).nunique(),"max",yb.sb.max() if len(yb) else None)
print(yb.reset_index().groupby('race').agg(k=('sb','size'),max=('sb','max')).sort_values('k',ascending=False).head(10))
last=g.reset_index(); last=last[last.ts==last.ts.max()]
print("latest: median sum asks",last.sa.median(),"median sum bids",last.sb.median())
print(last.sort_values('sb',ascending=False).head(5)[['race','sa','sb']]); print(last.sort_values('sa').head(5)[['race','sa','sb']])
