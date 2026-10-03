"""A_cppi: dynamic momentum sizing on the tilt (CPPI on the tilt exposure) with a trend filter, over A_path's tilt paths.
X_t (tilt exposure, value change -X ds; momentum is X<0) = -min(Xcap(V,s), (V - floor)/(gapfrac*s)), re-set every `reb` hours;
trend filter: hold only while s_t >= max(s over last 24h) - band; else flat. Trading cost: `tc` per $ of momentum notional traded
(notional = |dX| * price/(c - r), price ~ c - (1-s)(c-r) with c-r ~ 0.47). Exit to cash 1 day before the close (valuation-free)."""
import numpy as np, sys
sys.path.insert(0,'/home/claude/prediction-market-maker/analysis/p9')
import A_path as P
def price(s): return 0.5-(1-s)*0.47
def run(S, floor=88000., gapfrac=0.5, reb=24, band=0.01, tc=0.04, cap_frac=1.0, X0_cur=35900., exit_day=1, label=''):
    N,H1=S.shape; H=H1-1; V=np.full(N,P.V0); X=np.zeros(N); peak=V.copy(); mdd=np.zeros(N)
    # start: flatten the current toward book (cost ~700) - assumed in floor slack
    V-=700
    endh=H-exit_day*24
    for h in range(endh):
        if h%reb==0:
            s=S[:,h]; trend=s>=S[:,max(0,h-24):h+1].max(1)-band
            cush=np.maximum(V-floor,0)
            Xcap=cap_frac*V*0.47/price(s)
            tgt=np.where(trend,np.minimum(Xcap,cush/(gapfrac*np.maximum(s,0.03))),0.0)
            dX=np.abs(tgt-X); V-=tc*dX*price(s)/0.47; X=tgt
        V=V+X*(S[:,h+1]-S[:,h])
        peak=np.maximum(peak,V); mdd=np.maximum(mdd,1-V/peak)
    V-=tc*X*price(S[:,endh])/0.47  # exit
    p=lambda c:np.mean(c)
    print(f"{label:46s} P150 {p(V>=150e3):5.1%} P200 {p(V>=200e3):5.1%} P<=85 {p(V<=85e3):5.1%} P(DD>20%) {p(mdd>0.2):5.1%} median {np.median(V)/1e3:5.1f}k")
    return V
if __name__=='__main__':
    for name,prior,cr in [('base',P.PRIOR,0.10),('pessimistic',[(0.30,0.0,0.06),(0.35,0.08,0.16),(0.25,0.16,0.30),(0.10,0.30,0.55)],0.2),('trend',[(0.05,0.0,0.06),(0.15,0.08,0.16),(0.35,0.16,0.30),(0.45,0.30,0.55)],0.05)]:
        S,ST,K=P.paths(prior,cr); print(f"--- prior {name}, crash {cr}")
        for fl,g,band,reb in [(88000,0.5,0.01,24),(88000,0.5,0.01,6),(88000,0.3,0.01,6),(90000,0.5,0.005,6),(86000,0.25,0.01,6),(88000,0.5,1.0,24)]:
            run(S,fl,g,reb,band,label=f'floor {fl/1e3:.0f}k gap {g} band {band} reb {reb}h')
