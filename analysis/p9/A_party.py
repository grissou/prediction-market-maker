"""A_party: settled-at-outcome party baskets (Gaussian copula, A_mc's simulate): can any correlated bet give P(>=150k) with P(<=85k) < 10%?
Book = current book + extra stake S bought at the ask (YES) in the named basket, funded by selling the current book pro rata (liquidation cost 0.7%)."""
import sys; sys.argv=['x','20000']
exec(open('/home/claude/prediction-market-maker/analysis/p9/A_mc.py').read().split("DS=[")[0])
def basket(sel,S):
    pos=d.pos.values*(1-S/LIQ); cash=CASH
    L=d[sel&d.ask.notna()]; per=S/len(L)
    for idx,r in L.iterrows(): pos[d.index.get_loc(idx)]+=per/r.ask
    return pos,cash
tests={'Dem tossups (ref .3-.7)':(d.party=='Dem')&d.ref.between(.3,.7),'Rep tossups':(d.party=='Rep')&d.ref.between(.3,.7),
'Dem House+Senate YES':d.label.isin(['Dem U.S. House','Dem U.S. Senate']),'Rep House YES (longshot of the deep pair)':d.label.eq('Rep U.S. House'),
'Dem underpriced favourites (ref>.85, YES)':(d.party=='Dem')&(d.ref>.85)&(d.mid<d.ref)}
for nm,sel in tests.items():
    for S in [15000,30000,60000]:
        p,c=basket(sel,S); st,mk=value(p,c,0.0)
        print(f"{nm:44s} S {S/1e3:3.0f}k n {sel.sum():3d}  settled: {stats(st)}")
