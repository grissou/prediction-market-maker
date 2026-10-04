"""A_moves: which contracts moved most since 28 Sep (book mid, Polymarket ref, exchange mark). Read-only on /home/claude/snap03/md.sqlite."""
import sqlite3, pandas as pd, numpy as np, json, sys
DB='/home/claude/snap03/md.sqlite'
c=sqlite3.connect(DB)
s=pd.read_sql("select ts,eid,label,best_bid,best_ask,reference,position from snapshots",c)
s['ts']=pd.to_datetime(s.ts)
s['mid']=np.where(s.best_bid.notna()&s.best_ask.notna(),(s.best_bid+s.best_ask)/2,np.nan)
s['day']=s.ts.dt.strftime('%m-%d')
# daily medians
d=s.groupby(['eid','label','day']).agg(mid=('mid','median'),ref=('reference','median')).reset_index()
pm=d.pivot_table(index=['eid','label'],columns='day',values='mid')
pr=d.pivot_table(index=['eid','label'],columns='day',values='ref')
first=s.sort_values('ts').groupby('eid').head(30).groupby('eid').agg(mid0=('mid','median'),ref0=('reference','median'),t0=('ts','min'))
last=s.sort_values('ts').groupby('eid').tail(5).groupby('eid').agg(mid1=('mid','median'),ref1=('reference','median'),label=('label','first'))
m=first.join(last)
p=pd.read_sql("select ts,eid,quantity,current_price from positions where current_price is not null",c)
pf=p.sort_values('ts').groupby('eid').agg(mk0=('current_price','first'),mk1=('current_price','last'),mkmin=('current_price','min'),mkmax=('current_price','max'))
m=m.join(pf)
m['ratio_mid']=m.mid1/m.mid0
m['dmid']=m.mid1-m.mid0
m['dref']=m.ref1-m.ref0
print("days:",list(pm.columns))
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
print("\nTop 25 by mid ratio (YES side multiplier):")
print(m.sort_values('ratio_mid',ascending=False).head(25)[['label','mid0','mid1','ref0','ref1','mk0','mk1','mkmin','mkmax','ratio_mid']].round(3).to_string())
m['no_ratio']=(1-m.mid1)/(1-m.mid0)
print("\nTop 15 by NO ratio:")
print(m.sort_values('no_ratio',ascending=False).head(15)[['label','mid0','mid1','ref0','ref1','no_ratio']].round(3).to_string())
print("\nTop 15 |dmid|:")
print(m.reindex(m.dmid.abs().sort_values(ascending=False).index).head(15)[['label','mid0','mid1','ref0','ref1','dmid','dref']].round(3).to_string())
# daily median mid of longshots (ref<0.1) and favourites
s2=s.dropna(subset=['mid','reference'])
for lo,hi in [(0,0.05),(0.05,0.15),(0.15,0.35),(0.65,0.85),(0.85,0.95),(0.95,1.01)]:
    g=s2[(s2.reference>=lo)&(s2.reference<hi)].groupby('day').apply(lambda x:(x.mid-x.reference).median())
    print(f"ref in [{lo},{hi}) median mid-ref by day:", g.round(3).to_dict())
m.to_csv(sys.argv[1] if len(sys.argv)>1 else '/tmp/A_moves.csv')
