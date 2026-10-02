import sys
sys.path.insert(0,'/home/user/prediction-market-maker/analysis')
import data2 as D
from collections import defaultdict
fl=D.fills_reconciled(); fl=[f for f in fl if f['sgn']!=0 and f['yes_px'] is not None]
snap,T=D.snapshots(); S=D.Series(snap,T)
# 2. sweep exits: for fills with edge>3c, what exit was available at +5/+15/+30/+60 min
def row(lab,t): return S.at(lab,t,after=True,tol=600)
res=defaultdict(list)
for f in fl:
    if f['fvq'] is None: continue
    edge=f['sgn']*(f['fvq']-f['yes_px'])
    if edge<=0.03: continue
    for dt in (0,5,15,30,60):
        r=row(f['label'],f['t']+dt*60)
        if not r or r['bb'] is None or r['ba'] is None: continue
        m=(r['bb']+r['ba'])/2
        # exit by posting at mid (passive) or hitting opposite best (aggressive)
        hit = (r['bb']-f['yes_px']) if f['sgn']>0 else (f['yes_px']-r['ba'])
        mm = f['sgn']*(m-f['yes_px'])
        res[dt].append((f['qty'],hit,mm,edge))
print("Sweep fills (edge>3c): realisable c/sh if exited at +dt by hitting the opposite best / at mid; share-weighted")
for dt in (0,5,15,30,60):
    a=res[dt]; Q=sum(r[0] for r in a)
    print(" +%2d min n=%3d  hit %+.2f  mid %+.2f  entry edge %+.2f  (hit>=50%% of edge: %.0f%% of shares)"%(dt,len(a),100*sum(r[0]*r[1] for r in a)/Q,100*sum(r[0]*r[2] for r in a)/Q,100*sum(r[0]*r[3] for r in a)/Q, 100*sum(r[0] for r in a if r[1]>=0.5*r[3])/Q))
# 3. all fills with edge>=1c: same
res=defaultdict(list)
for f in fl:
    if f['fvq'] is None: continue
    edge=f['sgn']*(f['fvq']-f['yes_px'])
    if edge<0.01 or edge>0.03: continue
    for dt in (0,5,15,30):
        r=row(f['label'],f['t']+dt*60)
        if not r or r['bb'] is None or r['ba'] is None: continue
        m=(r['bb']+r['ba'])/2
        hit = (r['bb']-f['yes_px']) if f['sgn']>0 else (f['yes_px']-r['ba'])
        res[dt].append((f['qty'],hit,f['sgn']*(m-f['yes_px']),edge))
print("Fills with 1c<edge<=3c:")
for dt in (0,5,15,30):
    a=res[dt]; Q=sum(r[0] for r in a)
    print(" +%2d min n=%3d  hit %+.2f  mid %+.2f  entry edge %+.2f"%(dt,len(a),100*sum(r[0]*r[1] for r in a)/Q,100*sum(r[0]*r[2] for r in a)/Q,100*sum(r[0]*r[3] for r in a)/Q))
# 4. Was the reducing side quoted, and how tight, when we held inventory?  from quote lines
Q=D.quotes()
cnt=defaultdict(float); n=0
for q in Q:
    if q['fv'] is None or q['inv']==0: continue
    n+=1
    red = 'ask' if q['inv']>0 else 'bid'
    add = 'bid' if q['inv']>0 else 'ask'
    rp=q[red]; ap=q[add]
    cnt['red_quoted']+= rp is not None; cnt['add_quoted']+= ap is not None
    if rp is not None: cnt['red_dist']+= abs(rp-q['fv']); cnt['red_n']+=1
    if ap is not None: cnt['add_dist']+= abs(ap-q['fv']); cnt['add_n']+=1
    if rp is not None and ap is not None: cnt['both']+=1
print("quote lines with inventory: %d ; reducing side quoted %.0f%% (mean dist from fv %.2fc) ; adding side quoted %.0f%% (dist %.2fc) ; both %.0f%%"%(n,100*cnt['red_quoted']/n,100*cnt['red_dist']/cnt['red_n'],100*cnt['add_quoted']/n,100*cnt['add_dist']/cnt['add_n'],100*cnt['both']/n))
