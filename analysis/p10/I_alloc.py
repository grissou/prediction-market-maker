"""I_alloc: the capital allocator's first action list (Package 10, explorer I). Read-only on /home/claude/snap03 (22:47 3 Oct).
Edge per $ to the OUTCOME, p = Polymarket (race-normalised, H_outcome.load), optional calibration k and tilt shift:
  held long YES   : (p - bid)/bid              (what we keep vs selling at the bid)
  held long NO    : (ask - p)/(1 - ask)        (closing = buying YES at the ask frees 1 - ask)
  new buy YES     : (p - ask)/ask   per book level (3 levels)
  new short (NO)  : (bid - p)/(1 - bid)
Rotation = sell the lowest-edge holding, buy the highest-edge level: gain = $ x (e_buy - e_held), both legs at the touch (net of spread).
Bloc delta per share (H_risk closed form): Dem YES -sqrt(rho) phi(Phi^-1 pDem), Rep YES the negative; $ per sd of F (F>0 = Rep wave).
Env: I_K (calibration k, default 1.0), I_DS (tilt today minus 0.11: shifts every book price by -DS (p - c); default 0), I_CAP (per-contract
$ cap, default 10000), I_TOP (rows printed, 30).
Usage: python3 analysis/p10/I_alloc.py"""
import sys, os
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, calibrate

K = float(os.environ.get('I_K', 1.0)); DS = float(os.environ.get('I_DS', 0.0))
CAP = float(os.environ.get('I_CAP', 10000)); TOP = int(os.environ.get('I_TOP', 30))
d, cash, st = load()
d['p'] = calibrate(d, K)
sh = lambda px, i: float(np.clip(px - DS * (d.p0[i] - d.c[i]), 0.001, 0.999))   # today's tilt: prices further from p toward c
# bloc beta per YES share
beta = np.zeros(len(d))
for race, g in d.groupby('race'):
    parties = list(g.party); rr = 0.85 if race.startswith('U.S.') else 0.45
    if 'Dem' not in parties or 'Rep' not in parties:
        continue
    pI = g.p0[g.party == 'Ind'].sum(); pdem = float(g.p0[g.party == 'Dem'].iloc[0]) / (1 - pI)
    s_ = np.sqrt(rr) * norm.pdf(norm.ppf(np.clip(pdem, 1e-5, 1 - 1e-5))) * (1 - pI)
    for i, pa in zip(g.index, parties):
        beta[i] = -s_ if pa == 'Dem' else (s_ if pa == 'Rep' else 0)
d['beta'] = beta

# ---------------- held positions ----------------
H = []
for i, r in d[d.pos != 0].iterrows():
    bids = [(sh(px, i), q) for px, q in r.bids] or [(sh(r.best_bid, i), 1e9)]
    asks = [(sh(px, i), q) for px, q in r.asks] or [(sh(r.best_ask, i), 1e9)]
    if r.pos > 0:
        px = bids[0][0]; depth = sum(q for _, q in bids); usd = r.pos * px; e = (r.p - px) / px
    else:
        px = asks[0][0]; depth = sum(q for _, q in asks); usd = -r.pos * (1 - px); e = (px - r.p) / (1 - px)
    H.append(dict(i=i, label=r.label, pos=r.pos, px=px, p=r.p, usd=usd, edge=e, depth3=depth, noref=r.noref,
                  bloc=r.pos * r.beta))
H = pd.DataFrame(H)
# NO+NO sets (both legs of a 2-party race short)
sets = []
for race, g in d.groupby('race'):
    gs = g[g.pos < 0]
    if len(gs) >= 2:
        n = float((-gs.pos).min()); ask_sum = sum(sh(r.best_ask, i) for i, r in gs.iterrows())
        payout = len(gs) - g.loc[gs.index].p.sum()          # sum of (1 - p) over the short legs, per set
        free = len(gs) - ask_sum                              # cash freed per set by buying every YES back at the ask
        sets.append(dict(race=race, legs=len(gs), sets=n, ask_sum=ask_sum, unwind_cost_c=100 * (payout - free),
                         usd_freed=n * free, edge=(payout - free) / free, cost_usd=n * (payout - free)))
sets = pd.DataFrame(sets).sort_values('edge')
print(f"account {st['account_value']:,.0f}  cash {cash:,.0f}  held {len(H)}  k {K}  tilt shift {DS:+.2f}")
print(f"held hold-edge: $-weighted {np.average(H.edge, weights=H.usd):.1%}; median {H.edge.median():.1%}; $ at edge<0 {H.usd[H.edge<0].sum():,.0f} "
      f"({(H.edge<0).sum()}), <2% {H.usd[H.edge<0.02].sum():,.0f} ({(H.edge<0.02).sum()}), <5% {H.usd[H.edge<0.05].sum():,.0f} ({(H.edge<0.05).sum()})")
print(f"bloc delta (closed form, $ per sd of F, + = gains in a Rep wave): {H.bloc.sum():+,.0f}")
print("\nNO+NO sets (sum of short legs): unwind = buy every YES back at the ask")
print(sets.round(3).to_string(index=False))
print(f"sets total: {sets.sets.sum():,.0f} sets, ${sets.usd_freed.sum():,.0f} freed if unwound, EV given up ${sets.cost_usd.sum():,.0f} "
      f"({sets.cost_usd.sum()/sets.usd_freed.sum():.1%} of the freed cash)")
print("\nLOWEST edge-held (sell candidates):")
print(H.sort_values('edge').head(25)[['label', 'pos', 'px', 'p', 'usd', 'edge', 'depth3', 'bloc', 'noref']].round(3).to_string(index=False))

# ---------------- opportunities ----------------
O = []
for i, r in d.iterrows():
    if r.noref:
        continue
    held = r.pos
    for lv, (px, q) in enumerate(r.asks):
        px = sh(px, i)
        if r.p > px and held >= 0:           # buy YES (only if we are not short it: that would be a close, counted in H)
            O.append(dict(i=i, label=r.label, side='buy', lvl=lv, px=px, q=q, usd=q * px, edge=(r.p - px) / px, ev=q * (r.p - px),
                          bloc_per_usd=r.beta / px))
    for lv, (px, q) in enumerate(r.bids):
        px = sh(px, i)
        if r.p < px and held <= 0:
            O.append(dict(i=i, label=r.label, side='short', lvl=lv, px=px, q=q, usd=q * (1 - px), edge=(px - r.p) / (1 - px),
                          ev=q * (px - r.p), bloc_per_usd=-r.beta / (1 - px)))
O = pd.DataFrame(O).sort_values('edge', ascending=False).reset_index(drop=True)
# per-contract cap incl. the current holding's $
held_usd = H.set_index('i').usd
cum = O.groupby('i').usd.cumsum() + O.i.map(held_usd).fillna(0)
allowed = (CAP - (cum - O.usd)).clip(lower=0)
O['usd_cap'] = np.minimum(O.usd, allowed)
O = O[O.usd_cap > 5].reset_index(drop=True)
print(f"\nopportunities (3 levels, cap ${CAP:,.0f}/contract incl. holdings): ${O.usd_cap.sum():,.0f}; "
      + ", ".join(f">= {e:.0%}: ${O.usd_cap[O.edge >= e].sum():,.0f}" for e in [0.05, 0.10, 0.15, 0.20, 0.30]))
print("HIGHEST edge per $ (buy list):")
print(O.head(30)[['label', 'side', 'lvl', 'px', 'q', 'usd_cap', 'edge', 'ev', 'bloc_per_usd']].round(3).to_string(index=False))

# ---------------- rotation: pair sells (ascending edge, sets as units) with buys (descending edge) ----------------
H['bpu'] = H.bloc / H.usd.clip(lower=1)
PS = d.party.map({'Rep': 1, 'Dem': -1}).fillna(0)
H['ppu'] = [PS[i] * np.sign(q) * abs(q) / max(u, 1e-9) for i, q, u in zip(H.i, H.pos, H.usd)]   # party-delta shares per $ held
O['ppu'] = [PS[i] * (1 if sd == 'buy' else -1) / (px if sd == 'buy' else 1 - px) for i, sd, px in zip(O.i, O.side, O.px)]
sellq = H.sort_values('edge')[['label', 'usd', 'edge', 'bpu', 'ppu']].rename(columns={'usd': 'avail'}).to_dict('records')
buyq = O[['label', 'side', 'px', 'usd_cap', 'edge', 'bloc_per_usd', 'ppu']].rename(columns={'usd_cap': 'avail'}).to_dict('records')
moves = []; si = bi = 0
while si < len(sellq) and bi < len(buyq) and len(moves) < 200:
    s_, b_ = sellq[si], buyq[bi]
    if b_['edge'] - s_['edge'] < 0.03:          # min improvement per rotation 3 points
        break
    x = min(s_['avail'], b_['avail'])
    moves.append(dict(sell=s_['label'], e_sell=s_['edge'], buy=f"{b_['side']} {b_['label']} @{b_['px']:.3f}", e_buy=b_['edge'],
                      usd=x, gain=x * (b_['edge'] - s_['edge']),
                      dbloc=(b_['bloc_per_usd'] - s_['bpu']) * x, dparty=(b_['ppu'] - s_['ppu']) * x))
    s_['avail'] -= x; b_['avail'] -= x
    if s_['avail'] < 1: si += 1
    if b_['avail'] < 1: bi += 1
M = pd.DataFrame(moves)
M['cum_gain'] = M.gain.cumsum(); M['cum_usd'] = M.usd.cumsum()
print(f"\nROTATION (sell lowest edge-held, buy highest edge; stop at < 3 points improvement): {len(M)} pair-moves, ${M.usd.sum():,.0f} rotated, "
      f"gain ${M.gain.sum():,.0f} net of the spread (both legs at the touch)")
print(M.head(TOP).round(3).to_string())
for n in [10, 30, 60, len(M)]:
    m = M.head(n)
    print(f"  top {n:3d}: ${m.usd.sum():8,.0f} rotated, gain ${m.gain.sum():6,.0f}, ~{2*n} order writes (sell + buy, + cancels of our resting quote)")
# write cost: distinct contracts touched
print(f"distinct contracts touched in the rotation: sells {M.sell.nunique()}, buys {M.buy.nunique()}")
for n in [30, len(M)]:
    print(f"  top {n}: bloc delta change {M.head(n).dbloc.sum():+,.0f} $/sd (now {H.bloc.sum():+,.0f}); share party delta change "
          f"{M.head(n).dparty.sum():+,.0f} (now {st['party_delta']:+,.0f}, cap +-{0.15*st['account_value']:,.0f})")
M.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/I_moves.csv', index=False)
H.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/I_held.csv', index=False)
O.to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/I_opp.csv', index=False)
