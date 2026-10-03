"""A_path: 31-day tilt-path Monte Carlo for books with tilt exposure X (value change = -X * ds, exact for marks at c+(1-s)(r-c)),
a kill switch at peak drawdown k, an optional exit to cash 1 day before the close, and the two end valuations.
Priors on the final tilt S_T are JUDGEMENT (stated in ideas_A.md), not data. s path: s0 + (S_T - s0)(1-exp(-t/tau))/(1-exp(-T/tau)) + bridge noise."""
import numpy as np, sys
rng=np.random.default_rng(11)
N=20000; T=31; H=T*24; t=np.arange(H+1)/24
s0=0.12; V0=101017.
PRIOR=[(0.15,0.0,0.06),(0.25,0.08,0.16),(0.30,0.16,0.30),(0.30,0.30,0.55)]  # (weight, lo, hi) for S_T
def paths(prior=PRIOR, crash_p=0.10):
    w=np.array([p[0] for p in prior]); k=rng.choice(len(prior),N,p=w/w.sum())
    lo=np.array([p[1] for p in prior])[k]; hi=np.array([p[2] for p in prior])[k]
    ST=rng.uniform(lo,hi); tau=rng.uniform(3,15,N)[:,None]
    g=(1-np.exp(-t[None,:]/tau))/(1-np.exp(-T/tau))
    S=s0+(ST[:,None]-s0)*g
    dW=rng.normal(0,0.010/np.sqrt(24),(N,H)); W=np.concatenate([np.zeros((N,1)),np.cumsum(dW,1)],1)
    S=S+W-W[:,-1:]*t[None,:]/T
    # crash: whale liquidation / SIG intervention, the tilt halves within a day at a random time
    crash=rng.random(N)<crash_p; tc=rng.integers(24,H-24,N)
    for i in np.where(crash)[0]:
        S[i,tc[i]:]=S[i,tc[i]:]-0.5*S[i,tc[i]]
    return np.clip(S,0,0.95),ST,k
S,ST,K=paths()
def run(X, cost, killdd=None, exit_day=None, settle_gap=0.0, label=''):
    """X: tilt exposure (value change -X per unit s). cost: entry spread. settle_gap: settled-end value minus marked-end value at the close (from A_mc)."""
    V=V0-cost-X*(S-s0)
    news=rng.normal(0,1,(N,1))*np.sqrt(t/T)[None,:]*4000*min(1,abs(X)/35900+0.3)  # election-news noise on the book
    V=V+news
    end=np.full(N,H); 
    if exit_day is not None: end[:]=int((T-exit_day)*24)
    peak=np.maximum.accumulate(V,1); dd=1-V/peak
    if killdd is not None:
        hit=(dd>killdd); first=np.where(hit.any(1),hit.argmax(1),H+1)
        end=np.minimum(end,first)
    stopped=end<H
    fin=V[np.arange(N),np.minimum(end,H)]-np.where(stopped,0.03*abs(X)*0.1,0)  # exit slippage ~3% of gross momentum value
    mdd=np.array([dd[i,:end[i]+1].max() for i in range(N)])
    marked=fin
    settled=np.where(stopped,fin,fin+settle_gap)
    r=lambda x:(np.mean(x>=150e3),np.mean(x>=200e3),np.mean(x<=85e3))
    a,b=r(marked),r(settled)
    print(f"{label:38s} marked P150 {a[0]:5.1%} P200 {a[1]:5.1%} P<=85 {a[2]:5.1%} | settled P150 {b[0]:5.1%} P200 {b[1]:5.1%} P<=85 {b[2]:5.1%} | P(DD>20%) {np.mean(mdd>0.2):5.1%} stop {np.mean(stopped):5.1%}")
    return a,b
if __name__=='__main__':
    print("prior S_T buckets", [p for p in PRIOR], "s0",s0, "P(S_T>=0.29)",np.mean(ST>=0.29).round(3), "median S_T",np.median(ST).round(3))
    # settle gaps from A_mc (settled mean - marked ds=0 mean): current +8.1k, toward_all +12k, momentum_f: f=1 -47.8k, .5 -23k, .25 -9k
    run(35900,0,None,None,8100,'current book, hold')
    run(35900,0,0.2,None,8100,'current book, kill 20%')
    run(0,700,None,None,0,'flat tilt (sell toward book)')
    for f,X,cost,gap in [(0.25,-81400,800,-9000),(0.5,-169700,1600,-23000),(1.0,-316200,3200,-47800)]:
        run(X,cost,None,None,gap,f'momentum f={f}, hold to close')
        run(X,cost,0.2,None,gap,f'momentum f={f}, kill 20%')
        run(X,cost,0.2,1,0,f'momentum f={f}, kill 20%, exit T-1d')
        run(X,cost,0.15,1,0,f'momentum f={f}, kill 15%, exit T-1d')
    print("\nsensitivity: pessimistic prior (R 30%, F 35%, G 25%, H 10%), crash 20%")
    S,ST,K=paths([(0.30,0.0,0.06),(0.35,0.08,0.16),(0.25,0.16,0.30),(0.10,0.30,0.55)],0.2)
    run(35900,0,None,None,8100,'current book, hold')
    for f,X,cost in [(0.5,-169700,1600),(1.0,-316200,3200)]:
        run(X,cost,0.2,1,0,f'momentum f={f}, kill 20%, exit T-1d')
    print("\nsensitivity: trend prior (R 5%, F 15%, G 35%, H 45%), crash 5%")
    S,ST,K=paths([(0.05,0.0,0.06),(0.15,0.08,0.16),(0.35,0.16,0.30),(0.45,0.30,0.55)],0.05)
    for f,X,cost in [(0.5,-169700,1600),(1.0,-316200,3200)]:
        run(X,cost,0.2,1,0,f'momentum f={f}, kill 20%, exit T-1d')
