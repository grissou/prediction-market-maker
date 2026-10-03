"""A_book: latest book per eid, |mid - Polymarket| x depth ranking, our position settlement EV vs exchange mark."""
import sqlite3, pandas as pd, numpy as np, json
c=sqlite3.connect('/home/claude/snap03/md.sqlite')
s=pd.read_sql("select * from snapshots where ts=(select max(ts) from snapshots)",c)
print("latest snapshot rows",len(s), s.ts.iloc[0])
b=pd.read_sql("select * from books",c)
b=b.sort_values('ts').groupby('eid').tail(1).set_index('eid')
def depth(js,side,ref,mid):
    lv=json.loads(js); return sum(q for p,q in lv), sum(p*q for p,q in lv)
rows=[]
for _,r in s.iterrows():
    e=r.eid
    bd=ad=bn=an=np.nan; bts=np.nan
    if e in b.index:
        bb=json.loads(b.loc[e,'bids']); aa=json.loads(b.loc[e,'asks']); bts=b.loc[e,'ts']
        bd=sum(q for p,q in bb); ad=sum(q for p,q in aa)
    rows.append(dict(eid=e,label=r.label,bid=r.best_bid,ask=r.best_ask,ref=r.reference,pos=r.position,bid_sh=bd,ask_sh=ad,book_ts=bts))
d=pd.DataFrame(rows)
d['mid']=(d.bid+d.ask)/2
d['party']=d.label.str.split().str[0]
d['race']=d.label.str.split(n=1).str[1]
legs=d.groupby('race').eid.transform('count'); d['legs']=legs; d['c']=1/legs
d['gap']=d.mid-d.ref
# depth on the side you'd hit to trade toward ref: if mid>ref you sell YES (hit bids) / buy NO; else buy YES (lift asks)
d['take_sh']=np.where(d.gap>0,d.bid_sh,d.ask_sh)
d['score']=d.gap.abs()*d.take_sh
p=pd.read_sql("select eid,quantity,current_price,fields from positions",c).groupby('eid').tail(1).set_index('eid')
d=d.join(p[['current_price']],on='eid')
pd.set_option('display.width',250)
print(d.sort_values('score',ascending=False).head(20)[['label','bid','ask','mid','ref','gap','take_sh','score','pos','current_price']].round(3).to_string())
d.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/book.csv',index=False)
# our positions: settlement EV at Poly vs exchange mark vs book mid
st=json.load(open('/home/claude/snap03/status.json'))
P=d[d.pos.fillna(0)!=0].copy()
P['val_mark']=np.where(P.pos>0,P.pos*P.current_price,-P.pos*(1-P.current_price))
P['val_mid']=np.where(P.pos>0,P.pos*P.mid,-P.pos*(1-P.mid))
P['val_ref']=np.where(P.pos>0,P.pos*P.ref,-P.pos*(1-P.ref))
P['tilt_x']=P.pos*(P.ref-P.c)
print("positions",len(P),"mark",P.val_mark.sum().round(),"mid",P.val_mid.sum().round(),"settleEV@ref",P.val_ref.sum().round(),"tilt_exposure",P.tilt_x.sum().round())
print("no-ref positions:",P[P.ref.isna()][['label','pos']].to_string())
P['edge_settle']=P.val_ref-P.val_mark
print(P.sort_values('edge_settle',ascending=False).head(25)[['label','pos','mid','current_price','ref','val_mark','val_ref','edge_settle','tilt_x']].round(3).to_string())
print(P.sort_values('edge_settle').head(10)[['label','pos','mid','current_price','ref','val_mark','val_ref','edge_settle']].round(3).to_string())
P.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/pos.csv',index=False)
