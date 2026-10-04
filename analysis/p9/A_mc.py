"""A_mc: Monte Carlo of the account on 4 Nov under two end valuations, for candidate books.
Settled: each race resolves at Polymarket probabilities (Gaussian copula, one national D/R factor).
Marked:  close value = mark_T = c + (1 - s_iT)(ref_T - c) with s_iT = clip(s_i + ds), ref_T = partial-information posterior.
Read-only on /home/claude/snap03. Usage: python3 A_mc.py [N]"""
import numpy as np, pandas as pd, json, sys
from scipy.stats import norm
rng=np.random.default_rng(7)
N=int(sys.argv[1]) if len(sys.argv)>1 else 20000
SCR='/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/A/'
d=pd.read_csv(SCR+'book.csv')
d['ref']=d.ref.fillna(d.mid)
d['party']=d.label.str.split().str[0]; d['race']=d.label.str.split(n=1).str[1]
d['natl']=d.race.str.startswith('U.S.')
d['s_i']=np.where((d.ref-d.c).abs()>0.05,1-(d.mid-d.c)/(d.ref-d.c),0.11)
d['s_i']=d.s_i.clip(-0.1,0.6)
d['pos']=d.pos.fillna(0)
races=d.race.unique(); R=len(races); ridx={r:i for i,r in enumerate(races)}
d['ri']=d.race.map(ridx)
RHO_STATE=float(sys.argv[2]) if len(sys.argv)>2 else 0.35; RHO_NAT=0.8; ALPHA=0.6
def simulate(N):
    F1=rng.standard_normal((N,1)); F2=rng.standard_normal((N,1))
    rho=np.array([RHO_NAT if r.startswith('U.S.') else RHO_STATE for r in races])[None,:]
    u=np.sqrt(rho)*F1+np.sqrt(1-rho)*rng.standard_normal((N,R))
    v=np.sqrt(rho)*F2+np.sqrt(1-rho)*rng.standard_normal((N,R))
    z=np.sqrt(ALPHA)*u+np.sqrt(1-ALPHA)*v
    U_ind=rng.random((N,R))
    win=np.zeros((N,len(d))); refT=np.zeros((N,len(d)))
    for r,i in ridx.items():
        legs=d[d.ri==i]; p=legs.ref.values.clip(0.001,0.999); p=p/p.sum()
        parties=list(legs.party)
        pI=p[parties.index('Ind')] if 'Ind' in parties else 0.0
        indwin=U_ind[:,i]<pI
        if 'Dem' in parties and 'Rep' in parties:
            pd_=p[parties.index('Dem')]/(1-pI)
            k=norm.ppf(np.clip(pd_,1e-4,1-1e-4))
            demwin=(z[:,i]<k)&~indwin
            pdT=norm.cdf((k-np.sqrt(ALPHA)*u[:,i])/np.sqrt(1-ALPHA))*(1-pI)
            for j,(idx,row) in enumerate(legs.iterrows()):
                col=d.index.get_loc(idx)
                if row.party=='Dem': win[:,col]=demwin; refT[:,col]=pdT
                elif row.party=='Rep': win[:,col]=(~demwin)&~indwin; refT[:,col]=(1-pI)-pdT
                else: win[:,col]=indwin; refT[:,col]=pI
        else:  # odd race: independent multinomial by U
            cum=np.cumsum(p); pick=np.searchsorted(cum,rng.random(N))
            for j,(idx,row) in enumerate(legs.iterrows()):
                col=d.index.get_loc(idx); win[:,col]=(pick==j); refT[:,col]=p[j]
    return win,refT
win,refT=simulate(N)
_dev=pd.DataFrame({'label':d.label,'ref':d.ref,'simwin':win.mean(0),'refT':refT.mean(0)}); _dev['err']=_dev.simwin-_dev.ref
print("sim calibration: max |simwin-ref|",_dev.err.abs().max().round(3),"; sum pos-weighted err", round(float((_dev.err*np.sign(d.pos)*d.pos.abs()).sum())))
print(_dev.reindex(_dev.err.abs().sort_values(ascending=False).index).head(5).to_string())
def value(pos,cash,ds):
    """returns (settled, marked) arrays of account value"""
    pos=np.asarray(pos); yes=np.clip(pos,0,None); no=np.clip(-pos,0,None)
    settled=cash+win@yes+(1-win)@no
    sT=np.clip(d.s_i.values+ds,0,0.95)
    mk=d.c.values+(1-sT)*(refT-d.c.values)+rng.normal(0,0.01,refT.shape)
    mk=np.clip(mk,0.005,0.995)
    marked=cash+mk@yes+(1-mk)@no
    return settled,marked
def stats(x): return f"mean {x.mean()/1e3:6.1f}k P>=150 {np.mean(x>=150e3):5.1%} P>=200 {np.mean(x>=200e3):5.1%} P<=85 {np.mean(x<=85e3):5.1%} p5 {np.percentile(x,5)/1e3:5.1f}k"
CASH=101017.2-100998.0
LIQ=100309.5
books={}
books['current']=(d.pos.values.copy(),CASH)
# toward-Poly concentrated: liquidate and rebuy toward ref at take price, weight by gap, cap 3x visible depth
def toward(cap=LIQ, depth_mult=3, min_gap=0.03):
    pos=np.zeros(len(d)); cash=cap
    g=d.mid-d.ref
    cand=d[(g.abs()>=min_gap)].copy()
    cand['cost']=np.where(g[cand.index]>0,1-cand.bid,cand.ask)  # NO cost or YES cost
    cand['ev']=np.where(g[cand.index]>0,1-cand.ref,cand.ref)
    cand['edge']=cand.ev/cand.cost-1
    cand=cand[cand.edge>0.02].sort_values('edge',ascending=False)
    for idx,r in cand.iterrows():
        sh_cap=depth_mult*(r.bid_sh if g[idx]>0 else r.ask_sh)
        spend=min(cash, sh_cap*r.cost, 0.06*cap)
        sh=spend/r.cost; pos[d.index.get_loc(idx)]+= -sh if g[idx]>0 else sh; cash-=spend
        if cash<1: break
    return pos,cash,cand
pT,cT,cand=toward()
books['toward_all']=(pT,cT)
print("toward candidates",len(cand),"median edge",cand.edge.median().round(3),"cash left",round(cT))
def momentum(f, refmax=0.05, depth_mult=2):
    base=d.pos.values*(1-f); cash=CASH+f*LIQ
    budget=f*LIQ; L=d[(d.ref<refmax)&d.ask.notna()]
    per=budget/len(L); pos=base.copy()
    for idx,r in L.iterrows():
        spend=min(per,depth_mult*r.ask_sh*r.ask); j=d.index.get_loc(idx); sh=spend/r.ask
        netted=min(sh,max(0.0,-pos[j])); cash+=netted  # YES+NO of one contract = 1: netting returns 1 per pair
        pos[j]+=sh; cash-=spend
    return pos,cash
for f in [0.1,0.25,0.5,1.0]: books[f'momentum_{f}']=momentum(f)
books['cash']=(np.zeros(len(d)),LIQ)
DS=[-0.11,0.0,0.1,0.2,0.35]
out=[]
for name,(pos,cash) in books.items():
    tx=(pos*(d.ref-d.c)).sum()
    print(f"\n== {name}: tilt_exposure {tx/1e3:.1f}k, cost per +1pt s {tx/100:.0f}")
    st,_=value(pos,cash,0.0); print("  settled        ",stats(st))
    for ds in DS:
        _,mk=value(pos,cash,ds); print(f"  marked ds={ds:+.2f}",stats(mk))
        out.append(dict(book=name,ds=ds,P150=np.mean(mk>=150e3),P200=np.mean(mk>=200e3),P85=np.mean(mk<=85e3),mean=mk.mean()))
    out.append(dict(book=name,ds='settled',P150=np.mean(st>=150e3),P200=np.mean(st>=200e3),P85=np.mean(st<=85e3),mean=st.mean()))
pd.DataFrame(out).to_csv(SCR+'mc_out.csv',index=False)
