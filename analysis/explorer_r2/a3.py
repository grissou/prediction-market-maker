import sys
sys.path.insert(0,'/home/user/prediction-market-maker/analysis')
import data2 as D
from u import *
from collections import defaultdict
snap,T=D.snapshots()
Tl=[t for t in T if t>=D.OPEN]
# 5. night gap persistence & size by hour
byh=defaultdict(list)
for t in Tl:
    for lab,r in snap[t].items():
        m=D.mid(r)
        if m is None or r['ref'] is None: continue
        byh[D.hh(t,'%H')].append(abs(m-r['ref']))
print("hour: median |mid-ref| c, share >2c, share >3c, n")
for h in sorted(byh):
    a=[x*100 for x in byh[h]]
    print(" %s  %.2f  %.0f%%  %.0f%%  %d"%(h,med(a),100*sum(x>2 for x in a)/len(a),100*sum(x>3 for x in a)/len(a),len(a)))
# persistence: for market-snapshots with gap>2c at t, gap at next snapshot >=10 min later still >2c same sign?
S=D.Series(snap,Tl)
keep=defaultdict(lambda:[0,0])
for t in Tl:
    for lab,r in snap[t].items():
        m=D.mid(r)
        if m is None or r['ref'] is None: continue
        g=m-r['ref']
        if abs(g)<=0.02: continue
        r2=S.at(lab,t+600,after=True,tol=900)
        m2=D.mid(r2) if r2 else None
        if m2 is None or r2['ref'] is None: continue
        g2=m2-r2['ref']
        night = 0<=int(D.hh(t,'%H'))<8 or int(D.hh(t,'%H'))>=23
        k='night' if night else 'day'
        keep[k][1]+=1; keep[k][0]+= (abs(g2)>0.01 and (g2>0)==(g>0))
print("gap>2c still >1c same sign 10+ min later:", {k:"%.0f%% of %d"%(100*v[0]/v[1],v[1]) for k,v in keep.items()})
# 6. mark fragility: per market, mid sd over session x final |pos|; spread; best change rate
per=defaultdict(list)
for t in Tl:
    for lab,r in snap[t].items():
        m=D.mid(r)
        per[lab].append((t,m,r['bb'],r['ba'],r['pos']))
rows=[]
for lab,v in per.items():
    pos=v[-1][4] or 0
    mids=[m for t,m,bb,ba,p in v if m is not None]
    if len(mids)<10 or pos==0: continue
    d=[mids[i]-mids[i-1] for i in range(1,len(mids))]
    chg=st.mean([ 1.0 if ((v[i][2]!=v[i-1][2]) or (v[i][3]!=v[i-1][3])) else 0.0 for i in range(1,len(v))])
    spr=med([ (ba-bb) for t,m,bb,ba,p in v if bb is not None and ba is not None])
    rows.append((abs(pos)*sd(d)*math.sqrt(3), lab, pos, 100*sd(d), 100*spr, 100*chg, abs(pos)*spr))
rows.sort(reverse=True)
print("mark-fragility: |pos| x sd(mid step between snapshots) x sqrt(3) ($ per ~10 min); spread; top-change rate; |pos| x spread")
for a,lab,pos,sd,spr,chg,ps in rows[:15]: print("  %-26s pos %+6.0f  mtm10m $%5.0f  sd %.2fc spr %.2fc chg %.0f%%  pos*spr $%.0f"%(lab[:26],pos,a,sd,spr,chg,ps))
print("sum over all positions of |pos| x spread/2 (half-spread exit cost at mid) $%.0f ; sum pos x sd-step $%.0f"%(sum(r[6] for r in rows)/2, sum(r[0] for r in rows)))
# 7. account vs model: the 22:04 dip detail
acc=D.account()
for t,av,lk,w,pd,rs,real in acc:
    if D.ts('2026-10-01T21:50:00Z')<t<D.ts('2026-10-01T22:30:00Z'): print(D.hh(t,'%H:%M'), round(real), 'resting',rs)
