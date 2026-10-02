import sys
sys.path.insert(0,'/home/user/prediction-market-maker/analysis'); sys.path.insert(0,'r2')
import data2 as D; from u import *
from collections import defaultdict
import bisect
snap,T=D.snapshots(); Tl=[t for t in T if t>=D.OPEN]; S=D.Series(snap,Tl)
fl=D.fills_reconciled(); fl=[f for f in fl if f['sgn']!=0 and f['yes_px'] is not None]
# fills/hour by hour-of-day and by at-best status; night vs day volume share by market concentration
byh=defaultdict(lambda:[0,0.0])
for f in fl: h=int(D.hh(f['t'],'%H')); byh[h][0]+=1; byh[h][1]+=f['qty']
print("fills/h:", {h:v[0] for h,v in sorted(byh.items())})
# reducing fills: fills that reduce |position| vs add, by hour group; and their edge
pos=defaultdict(float); red=defaultdict(lambda:[0.0,0.0]); add=defaultdict(lambda:[0.0,0.0])
for f in fl:
    p=pos[f['label']]; q=f['sgn']*f['qty']
    h=int(D.hh(f['t'],'%H')); k='night' if (h<8 or h>=23) else 'day'
    e=(f['sgn']*(f['fvq']-f['yes_px'])) if f['fvq'] is not None else None
    reducing = p*q<0
    d=red if reducing else add
    d[k][0]+=f['qty']; 
    if e is not None: d[k][1]+=f['qty']*e
    pos[f['label']]+=q
for k in ('day','night'): print(k,"reducing shares %.0f edge %+.2fc ; adding shares %.0f edge %+.2fc"%(red[k][0],100*red[k][1]/red[k][0],add[k][0],100*add[k][1]/add[k][0]))
# how many distinct markets filled per hour at night vs day (breadth of flow)
mk=defaultdict(set)
for f in fl: mk[D.hh(f['t'])].add(f['label'])
print("distinct markets with fills per hour:", {h:len(v) for h,v in sorted(mk.items())})
# position size vs fills: at end, markets with |pos|>=1000: how many fills on the reducing side per hour in last 6h
END=Tl[-1]
lastpos={lab:(snap[END][lab]['pos'] or 0) for lab in snap[END]}
big=[l for l,p in lastpos.items() if abs(p)>=1000]
cnt=defaultdict(int)
for f in fl:
    if f['label'] in big and f['t']>END-6*3600 and f['sgn']*lastpos[f['label']]<0: cnt[f['label']]+=1
print("positions >=1000 sh:", len(big), "reducing fills in last 6h:", dict(cnt))
