import sys, bisect, statistics as st
sys.path.insert(0,'/home/user/prediction-market-maker/analysis')
import data2 as D
from collections import defaultdict
fl=D.fills_reconciled()
fl=[f for f in fl if f['sgn']!=0 and f['yes_px'] is not None]
snap,T=D.snapshots(); S=D.Series(snap,T)
END=T[-1]
def midat(lab,t,after=True,tol=900):
    r=S.at(lab,t,after=after,tol=tol); return D.mid(r) if r else None
byl=defaultdict(list)
for f in fl: byl[f['label']].append(f)

# 1. FIFO round trips
rt_times=[]; rt_pnl=0; rt_sh=0; open_sh=0; open_age=[]; open_unreal=0
per_market={}
realised_by_hour=defaultdict(float)
for lab,fs in byl.items():
    lots=[]  # (t, sgn, qty, px)
    real=0; openq=0
    for f in fs:
        q=f['qty']; s=f['sgn']; px=f['yes_px']
        while q>1e-9 and lots and lots[0][1]!=s:
            t0,s0,q0,p0=lots[0]; m=min(q,q0)
            pnl=m*(px-p0)*s0  # s0=+1 bought at p0, now selling at px
            real+=pnl; rt_pnl+=pnl; rt_sh+=m; rt_times.append((f['t']-t0)/60)
            realised_by_hour[D.hh(f['t'])]+=pnl
            q-=m; q0-=m
            if q0<1e-9: lots.pop(0)
            else: lots[0]=(t0,s0,q0,p0)
        if q>1e-9: lots.append((f['t'],s,q,px))
    m=midat(lab,END,after=False,tol=3600)
    unreal=0; sh=0; cap=0
    for t0,s0,q0,p0 in lots:
        open_sh+=q0; sh+=q0; open_age.append(((END-t0)/3600,q0))
        if m is not None: unreal+=q0*(m-p0)*s0; cap+=q0*(m if s0>0 else 1-m)
    tot=sum(f['qty'] for f in fs)
    per_market[lab]=dict(real=real,unreal=unreal,open=sh,traded=tot,n=len(fs),cap=cap,mid=m,
                         turnover=tot/sh if sh else None, age=max(((END-l[0])/3600 for l in lots),default=0))
print("FIFO round trips: shares %.0f pnl %+.0f ; open shares %.0f"%(rt_sh,rt_pnl,open_sh))
w=sorted(rt_times); 

from u import *
print("time-to-unwind (min) of round-tripped lots p10/25/50/75/90: ",[round(pct(rt_times,p),1) for p in (10,25,50,75,90)])
pairs=[(q,a) for a,q in open_age]; Q=sum(q for q,a in pairs)
print("open lots age (h) share-weighted p25/50/75:", [round(wpct(pairs,p),1) for p in (25,50,75)], " >3h share %.2f >12h %.2f"%(sum(q for q,a in pairs if a>3)/Q, sum(q for q,a in pairs if a>12)/Q))
print("realised by hour:", {k:round(v) for k,v in sorted(realised_by_hour.items())})
print("sum realised %+.0f  unreal(mid) %+.0f"%(sum(v['real'] for v in per_market.values()), sum(v['unreal'] for v in per_market.values())))
print()
print("## Capital efficiency: biggest open positions (capital at mid) with turnover = traded/open, fills/h")
rows=sorted(per_market.items(), key=lambda kv:-kv[1]['cap'])[:25]
H=16.2
for lab,v in rows:
    print("%-28s cap %6.0f open %6.0f traded %7.0f turn %5.1f fills/h %4.1f real %+6.0f unreal %+6.0f age %4.1fh"%(lab[:28],v['cap'],v['open'],v['traded'],v['turnover'] or 0,v['n']/H,v['real'],v['unreal'],v['age']))
tot_cap=sum(v['cap'] for v in per_market.values())
low=[v for v in per_market.values() if v['turnover'] and v['turnover']<2]
print("total cap(mid) %.0f ; markets with turnover<2: %d, cap %.0f"%(tot_cap,len(low),sum(v['cap'] for v in low)))
# shares/hour vs open: implied hours to flatten at observed two-way flow rate
imp=[]
for lab,v in per_market.items():
    if v['open']>0: imp.append((v['open']/(v['traded']/H/2+1e-9), v['cap'], lab))
imp.sort(reverse=True)
print("implied hours to flatten at half the observed flow rate (top):")
for h,c,l in imp[:12]: print("   %-28s %6.1f h  cap %6.0f"%(l[:28],h,c))
cw=sum(c for h,c,l in imp if h>24); print("capital in positions needing >24h at observed flow: %.0f of %.0f"%(cw,tot_cap))
