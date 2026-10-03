import sys
sys.path.insert(0,'/home/user/prediction-market-maker/analysis'); sys.path.insert(0,'r2')
import data2 as D; from u import *
from collections import defaultdict
snap,T=D.snapshots(); Tl=[t for t in T if t>=D.OPEN]
# RI Senate book sanity
print("RI Senate Rep: (hh:mm bb ba pos) sample")
for t in Tl[::12]:
    r=snap[t].get('Rep Rhode Island Senate')
    if r: print("  ",D.hh(t,'%H:%M'),r['bb'],r['ba'],r['pos'],end=';')
print()
# fill probability at best vs behind: per (snapshot, market, side) with our quote; fill on that side before next snapshot
fl=D.fills_reconciled(); fl=[f for f in fl if f['sgn']!=0]
fb=defaultdict(list)
for f in fl: fb[(f['label'],f['side'])].append(f['t'])
for k in fb: fb[k].sort()
import bisect
stat=defaultdict(lambda:[0,0,0.0])
for i,t in enumerate(Tl[:-1]):
    t2=Tl[i+1]
    if t2-t>600: continue
    for lab,r in snap[t].items():
        for side,our,best in (('bid',r['ob'],r['bb']),('ask',r['oa'],r['ba'])):
            if our is None or best is None: continue
            d=round((best-our)*100) if side=='bid' else round((our-best)*100)
            d=min(d,3); d=max(d,0)
            ts_=fb.get((lab,side),[])
            n=bisect.bisect_left(ts_,t2)-bisect.bisect_left(ts_,t)
            s=stat[(side,d)]; s[0]+=1; s[1]+= n>0; s[2]+=(t2-t)/60
print("P(fill on our side before next snapshot) by ticks behind best (0=at best):")
for k in sorted(stat): s=stat[k]; print("  %s behind %d: n=%5d  P=%.1f%%  per 10 min %.1f%%"%(k[0],k[1],s[0],100*s[1]/s[0],100*s[1]/s[0]*10/(s[2]/s[0])))
# pair sums for Senate/House
for race in ('U.S. Senate','U.S. House'):
    bs=[];as_=[]
    for t in Tl:
        d=snap[t].get('Dem '+race); r=snap[t].get('Rep '+race)
        if d and r and None not in (d['bb'],d['ba'],r['bb'],r['ba']):
            bs.append(d['bb']+r['bb']); as_.append(d['ba']+r['ba'])
    print(race,"bid-sum p50/p90/max %.3f/%.3f/%.3f  share>=1.00 %.0f%%  ; ask-sum p50/p10/min %.3f/%.3f/%.3f share<=1.00 %.0f%%  n=%d"%(med(bs),pct(bs,90),max(bs),100*sum(b>=1.0 for b in bs)/len(bs),med(as_),pct(as_,10),min(as_),100*sum(a<=1.0 for a in as_)/len(as_),len(bs)))
# all races: share of race-snapshots with bid-sum>=1.00 and <=0.995
labs=set(l for t in Tl for l in snap[t])
races=set(l[4:] for l in labs if l.startswith('Dem ') and ('Rep '+l[4:]) in labs)
c=[0,0,0]
for t in Tl:
    for rc in races:
        d=snap[t].get('Dem '+rc); r=snap[t].get('Rep '+rc)
        if d and r and None not in (d['bb'],d['ba'],r['bb'],r['ba']):
            c[0]+=1; c[1]+= d['bb']+r['bb']>=1.0; c[2]+= d['ba']+r['ba']<=0.995
print("all races: race-snapshots %d, bid-sum>=1.00 %.1f%%, ask-sum<=0.995 %.1f%%"%(c[0],100*c[1]/c[0],100*c[2]/c[0]))
# sweep persistence per market (first half vs second half)
half=D.ts('2026-10-02T00:00:00Z'); sw=defaultdict(lambda:[0,0])
for f in fl:
    if f['fvq'] is None: continue
    e=f['sgn']*(f['fvq']-f['yes_px'])
    if e>0.03: sw[f['label']][0 if f['t']<half else 1]+=1
both=sum(1 for v in sw.values() if v[0] and v[1]); print("markets with sweeps: %d; in both halves: %d; sweep counts top:"%(len(sw),both), sorted(sw.items(), key=lambda kv:-sum(kv[1]))[:8])
# open lots: entry edge vs unreal at mid, by age bucket
S=D.Series(snap,Tl); END=Tl[-1]
byl=defaultdict(list)
for f in fl: byl[f['label']].append(f)
buck=defaultdict(lambda:[0,0,0])
for lab,fs in byl.items():
    lots=[]
    for f in fs:
        q=f['qty']; s=f['sgn']
        while q>1e-9 and lots and lots[0][1]!=s:
            m=min(q,lots[0][2]); q-=m; lots[0][2]-=m
            if lots[0][2]<1e-9: lots.pop(0)
        if q>1e-9: lots.append([f['t'],s,q,f['yes_px'],f['fvq']])
    r=S.at(lab,END,after=False,tol=3600); m=D.mid(r) if r else None
    if m is None: continue
    for t0,s0,q0,p0,fv0 in lots:
        age=(END-t0)/3600; b='<1h' if age<1 else '1-3h' if age<3 else '3-8h' if age<8 else '>8h'
        buck[b][0]+=q0; buck[b][1]+=q0*(m-p0)*s0
        if fv0 is not None: buck[b][2]+=q0*(fv0-p0)*s0
print("open lots by age: shares / P&L at mid / entry edge $")
for b in ('<1h','1-3h','3-8h','>8h'): v=buck[b]; print("  %-5s %6.0f  %+6.0f  %+6.0f"%(b,v[0],v[1],v[2]))
