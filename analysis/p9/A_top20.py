"""A_top20: top 20 contracts by |mid - Polymarket| x take-side depth; per-$ return of the toward-Polymarket side:
settled at Polymarket probability, and marked at the close with the tilt moved by ds (mark = c + (1 - s_i - ds)(r - c))."""
import pandas as pd,numpy as np
SCR='/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/'
d=pd.read_csv(SCR+'book.csv'); d=d[d.ref.notna()&d.bid.notna()&d.ask.notna()].copy()
d['s_i']=1-(d.mid-d.c)/(d.ref-d.c)
over=d.gap>0
d['side']=np.where(over,'NO','YES'); d['cost']=np.where(over,1-d.bid,d.ask)
d['settle_ret']=np.where(over,1-d.ref,d.ref)/d.cost-1
for ds in [-0.11,0.1,0.3]:
    m=d.c+(1-(d.s_i+ds).clip(0,0.95))*(d.ref-d.c)
    d[f'mk{ds:+.2f}']=np.where(over,1-m,m)/d.cost-1
d['take_$']=d.take_sh*d.cost
t=d.sort_values('score',ascending=False).head(20)
cols=['label','side','cost','ref','gap','s_i','take_sh','take_$','settle_ret','mk-0.11','mk+0.10','mk+0.30','pos']
print(t[cols].round(3).to_string(index=False))
print("top20 take $",t['take_$'].sum().round(),"capital-weighted settle ret",(t.settle_ret*t['take_$']).sum()/t['take_$'].sum())
print("U.S. markets:"); print(d[d.label.str.startswith(('Dem U.S','Rep U.S'))][cols].round(3).to_string(index=False))
