"""H_risk: the bot's risk numbers (settlement_risk R7 'correlated', sum-of-maxima worst case, party delta) next to the outcome
model's ruin numbers, for the current book, plan (a) and Rep-Senate / bloc sleeves. Formulas copied from mm_bot.py
(race_variance, worst_case_loss, settlement_risk = risk_swing_shock x |party_delta| + risk_z x sqrt(sum var); the cycle uses
min(that, worst)); probabilities = Polymarket (p0). Usage: python3 analysis/p10/H_risk.py"""
import sys, os, math
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, simulate, settle, stats

SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash, st = load()
win, F = simulate(d, 20000, rho=0.45, k=1.0)
sign = d.party.map({'Rep': 1, 'Dem': -1}).fillna(0).values
groups = [g.index.values for _, g in d.groupby('race')]


def race_variance(legs):
    if len(legs) == 1:
        (x, p), = legs; return x * x * p * (1 - p)
    tot = sum(p for _, p in legs) or 1.0; probs = [p / tot for _, p in legs]
    pays = [sum((x if j == i else 0.0) if x > 0 else (0.0 if j == i else -x) for j, (x, _) in enumerate(legs)) for i in range(len(legs))]
    mean = sum(p * v for p, v in zip(probs, pays)); return sum(p * (v - mean) ** 2 for p, v in zip(probs, pays))


def worst_case_loss(legs):
    value = sum(x * fv if x > 0 else -x * (1 - fv) for x, fv in legs)
    sc = [[True], [False]] if len(legs) == 1 else [[j == i for j in range(len(legs))] for i in range(len(legs))]
    pay = lambda w: sum((x if ww else 0.0) if x > 0 else (0.0 if ww else -x) for (x, _), ww in zip(legs, w))
    return max(0.0, value - min(pay(s) for s in sc))


def bot_risk(pos, fv, swing=0.15, z=3.0):
    var = 0.0; worst = 0.0
    for g in groups:
        legs = [(pos[i], fv[i]) for i in g]
        if any(x for x, _ in legs):
            var += race_variance(legs); worst += worst_case_loss(legs)
    pdl = float((sign * pos).sum())
    sr = swing * abs(pdl) + z * math.sqrt(var)
    return min(sr, worst), sr, worst, pdl


acct = st['account_value']
fv = d.p0.values
pos_a = np.load(SCR + 'H_pos_a.npy')
ask = d.best_ask.values
cost_a = (pos_a.clip(min=0) * ask + (-pos_a).clip(min=0) * (1 - d.best_bid.values)).sum()
cash_a = st['liquidation_value'] - cost_a
ia = d.index[d.label == 'Rep U.S. Senate'][0]
print(f"account {acct:,.0f}; caps: max_worst_case_frac 0.30 -> {0.30*acct:,.0f}; backstop 0.8 -> {0.8*acct:,.0f}; "
      f"party cap 0.15 x bankroll -> {0.15*acct:,.0f} shares; kill (marks) < 70,000")
print(f"{'book':44s} {'risk':>7s} {'R7':>7s} {'worst':>7s} {'pdelta':>8s} | {'E':>6s} {'P<=85':>6s} {'P<=70':>6s} {'q1%':>6s} {'q0.1%':>6s} {'P150':>6s}")
books = [('current book (MC check of the status numbers)', d.pos.values, cash),
         ('(a) reallocation', pos_a, cash_a)]
for X in [5e3, 10e3, 15e3, 20e3, 22.5e3, 25e3, 30e3]:
    p_ = pos_a * (1 - X / 100e3); p_ = p_.copy(); p_[ia] += X / 0.35
    books.append((f"(c) (a) x {1-X/100e3:.3f} + Rep U.S. Senate YES ${X/1e3:.1f}k", p_, cash_a * (1 - X / 100e3)))
# current book + Rep Senate sleeve funded by unwinding NO+NO sets (cost <= 2c per set)
cur = d.pos.values.copy()
setraces = [g for g in groups if len(g) >= 2 and all(cur[i] < 0 for i in g)]
nsets = sum(min(-cur[i] for i in g) for g in setraces)
print(f"NO+NO set races in the current book: {len(setraces)}, sets {nsets:,.0f}")
for X in [10e3, 20e3]:
    p_ = cur.copy(); left = X
    for g in setraces:
        k = min(min(-p_[i] for i in g), left)
        for i in g: p_[i] += k
        left -= k
        if left <= 0: break
    p_[ia] += X / 0.35
    books.append((f"current - ${X/1e3:.0f}k sets + Rep Senate YES ${X/1e3:.0f}k", p_, cash - 0.02 * X))
for name, pos, cs in books:
    r, sr, w, pdl = bot_risk(pos, fv)
    v = settle(win, pos, cs)
    print(f"{name:44s} {r:7,.0f} {sr:7,.0f} {w:7,.0f} {pdl:+8,.0f} | {v.mean()/1e3:5.1f}k {np.mean(v<=85e3):6.1%} {np.mean(v<=70e3):6.1%} "
          f"{np.percentile(v,1)/1e3:5.1f}k {np.percentile(v,0.1)/1e3:5.1f}k {np.mean(v>=150e3):6.1%} | F-beta {np.polyfit(F, v, 1)[0]:+7,.0f}/sd")
# what does R7's 30% cap mean for a diversified value book? MC loss at the 3-sd-equivalent tail
v = settle(win, d.pos.values, cash)
print(f"\ncurrent book: liquidation {st['liquidation_value']:,.0f}; MC loss below liquidation at q0.13% (3 sd) "
      f"{st['liquidation_value']-np.percentile(v,0.135):,.0f}; vs status settlement_risk {st['settlement_risk']:,.0f} "
      f"(which is measured from MARK value, not from EV: EV - q0.13% = {v.mean()-np.percentile(v,0.135):,.0f})")

# ---- closed-form bloc delta (Gaussian copula): Cov(1{Dem wins}, F) = -sqrt(rho) phi(Phi^-1(pDem)); Rep leg the negative ----
from scipy.stats import norm as _n
def bloc_beta(pos, rho=0.45, rho_ctrl=0.85):
    b = np.zeros(len(d))
    for race, g in d.groupby('race'):
        parties = list(g.party); rr = rho_ctrl if race.startswith('U.S.') else rho
        if 'Dem' not in parties or 'Rep' not in parties:
            continue
        pI = g.p0[g.party == 'Ind'].sum(); pdem = float(g.p0[g.party == 'Dem'].iloc[0]) / (1 - pI)
        slope = np.sqrt(rr) * _n.pdf(_n.ppf(np.clip(pdem, 1e-5, 1 - 1e-5))) * (1 - pI)
        for i, pa in zip(g.index, parties):
            b[i] = -slope if pa == 'Dem' else (slope if pa == 'Rep' else 0)
    # NO shares (pos < 0) pay 1 - win: d/dF = -b; YES shares: +b  -> in YES terms simply pos x b
    return float((pos * b).sum())
for name, pos, cs in books[:2] + books[6:7]:
    v = settle(win, pos, cs)
    print(f"bloc delta closed form {bloc_beta(pos):+8,.0f}/sd  vs MC slope {np.polyfit(F, v, 1)[0]:+8,.0f}/sd  | share party delta "
          f"{float((sign*pos).sum()):+8,.0f}  : {name}")
