import sys
sys.path.insert(0,'/home/user/prediction-market-maker/analysis'); sys.path.insert(0,'r2')
import data2 as D; from u import *
from collections import defaultdict
import bisect
snap,T=D.snapshots(); Tl=[t for t in T if t>=D.OPEN]; S=D.Series(snap,Tl)
fl=D.fills_reconciled(); fl=[f for f in fl if f['sgn']!=0 and f['yes_px'] is not None]
# 2. night fills: with-gap vs against-gap (gap = mid - ref at fill time)
g=defaultdict(lambda:[0,0.0,0.0,0.0])
for f in fl:
    r=S.at(f['label'],f['t'],after=False,tol=900)
    if not r or r['ref'] is None: continue
    m=D.mid(r)
    if m is None: continue
    gap=m-r['ref']
    if abs(gap)<0.015: cls='small-gap'
    else:
        # we buy (sgn+1) when tournament is cheap vs ref (gap<0): "fading the tournament toward Polymarket"
        cls='fade' if (f['sgn']>0)==(gap<0) else 'follow'
    h=int(D.hh(f['t'],'%H')); night = h<8 or h>=23
    r60=S.at(f['label'],f['t']+3600,after=True,tol=1800); m60=D.mid(r60) if r60 else None
    if m60 is None or f['fvq'] is None: continue
    k=('night' if night else 'day',cls); v=g[k]; v[0]+=f['qty']; v[1]+=f['qty']*f['sgn']*(f['fvq']-f['yes_px']); v[2]+=f['qty']*f['sgn']*(m60-f['yes_px']); v[3]+=f['qty']*f['sgn']*(r['ref']-f['yes_px'])
print("fills by period x gap class: shares, edge vs fv, markout vs mid+60, edge vs Polymarket ref (c/sh)")
for k in sorted(g): v=g[k]; print("  %-6s %-9s %7.0f  %+.2f  %+.2f  %+.2f"%(k[0],k[1],v[0],100*v[1]/v[0],100*v[2]/v[0],100*v[3]/v[0]))
# 6. exit availability after ordinary fills (1-3c edge): P(mid crosses fv within X), P(opposite best >= fill+1c)
cnt=defaultdict(lambda:[0,0,0])
for f in fl:
    if f['fvq'] is None: continue
    e=f['sgn']*(f['fvq']-f['yes_px'])
    if e<0.01: continue
    cls='sweep' if e>0.03 else 'normal'
    lab=f['label']; T_=S.t.get(lab,[]); R_=S.r.get(lab,[])
    i0=bisect.bisect_left(T_,f['t'])
    for X in (15,30,60):
        hit_fv=False; hit_1c=False
        for j in range(i0,len(T_)):
            if T_[j]>f['t']+X*60: break
            r=R_[j]; m=D.mid(r)
            if m is None: continue
            if f['sgn']*(m-f['fvq'])>=0: hit_fv=True
            opp=r['bb'] if f['sgn']>0 else r['ba']
            if opp is not None and f['sgn']*(opp-f['yes_px'])>=0.01: hit_1c=True
        c=cnt[(cls,X)]; c[0]+=f['qty']; c[1]+=f['qty']*hit_fv; c[2]+=f['qty']*hit_1c
print("P(an exit is available within X min), share-weighted: mid reaches fv / opposite best reaches fill price +1c")
for k in sorted(cnt): c=cnt[k]; print("  %-6s %2d min: %.0f%%  %.0f%%"%(k[0],k[1],100*c[1]/c[0],100*c[2]/c[0]))
# 3. reducing side at best?  snapshots with pos!=0 : our reducing quote at best / behind / absent
st_=defaultdict(int)
for t in Tl:
    for lab,r in snap[t].items():
        p=r['pos'] or 0
        if p==0 or r['fv'] is None: continue
        our=r['oa'] if p>0 else r['ob']; best=r['ba'] if p>0 else r['bb']
        if our is None: st_['absent']+=1
        elif best is None: st_['only']+=1
        elif abs(our-best)<1e-6: st_['at best']+=1
        else: st_['behind']+=1
n=sum(st_.values()); print("reducing side when holding inventory (market-snapshots n=%d):"%n, {k:"%.0f%%"%(100*v/n) for k,v in st_.items()})
