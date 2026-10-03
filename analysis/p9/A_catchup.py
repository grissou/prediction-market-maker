"""A_catchup: do contracts that lag the common tilt catch up? Per-contract implied s_i at t0, change to t1; regress ds_i on (s_i - median)."""
import sqlite3,pandas as pd,numpy as np
c=sqlite3.connect('/home/claude/snap03/md.sqlite')
s=pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where best_bid is not null and best_ask is not null and reference is not null",c)
s['race']=s.label.str.split(n=1).str[1]; legs=s.groupby('race').eid.nunique(); s['c']=1/s.race.map(legs)
s['mid']=(s.best_bid+s.best_ask)/2; s['t']=pd.to_datetime(s.ts)
s=s[(s.reference-s.c).abs()>0.25]
def at(t0,w='2h'):
    x=s[(s.t>=pd.Timestamp(t0,tz='UTC'))&(s.t<pd.Timestamp(t0,tz='UTC')+pd.Timedelta(w))]
    g=x.groupby('eid').agg(mid=('mid','median'),ref=('reference','median'),c=('c','first'),label=('label','first'))
    g['si']=1-(g.mid-g.c)/(g.ref-g.c); g['price']=np.where(g.ref<g.c,g.mid,1-g.mid); return g
for t0,t1 in [('2026-10-02 06:00','2026-10-02 20:00'),('2026-10-02 12:00','2026-10-03 06:00'),('2026-10-03 00:00','2026-10-03 20:45'),('2026-10-02 06:00','2026-10-03 20:45')]:
    a=at(t0); b=at(t1); j=a.join(b[['si','price']],rsuffix='1').dropna()
    j['dev']=j.si-j.si.median(); j['dsi']=j.si1-j.si
    beta=np.polyfit(j.dev,j.dsi,1)[0]
    lo=j[j.dev<j.dev.quantile(0.25)]; hi=j[j.dev>j.dev.quantile(0.75)]
    # return of the cheap side (longshot YES or favourite NO) per $
    r=lambda z:(z.price1/z.price-1).mean()
    print(f"{t0} -> {t1}: n {len(j)} median s {j.si.median():.3f}->{j.si1.median():.3f} slope dsi on dev {beta:.2f}; laggard quartile ret {r(lo):+.1%}, leader quartile ret {r(hi):+.1%}, all {r(j):+.1%}")
