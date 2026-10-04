"""I_carry: what a funded value market maker adds to P(>= 150k) at the outcome (Package 10, explorer I). Core = H's plan (a)
(H_pos_a.npy) on H_outcome's worlds (rho 0.45, k 1.0, seed 7); the MM's outcome EV over the 30 days left = a normal draw, mean M,
sd 0.5 M (independent of the election; the reserve R it needs comes out of the core pro rata at the core's marginal edge 11.6%,
i.e. costs 0.116 R). Optionally + the Rep U.S. Senate sleeve (walks the 22:47 asks). Usage: python3 analysis/p10/I_carry.py"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, simulate, settle, stats
SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash, st = load()
win, F = simulate(d, 20000, rho=0.45, k=1.0)
pa = np.load(SCR + 'H_pos_a.npy')
cost_a = (pa.clip(min=0) * d.best_ask.values + (-pa).clip(min=0) * (1 - d.best_bid.values)).sum()
liq = st['liquidation_value']; iR = d.index[d.label == 'Rep U.S. Senate'][0]
rng = np.random.default_rng(4)
print("MM outcome EV over 30 d (mean) | reserve | sleeve | E | P>=120 | P>=150 | P<=85 | P<=70")
for R in [20e3]:
    for X in [0, 15e3, 20e3]:
        for M in [0, 10e3, 20e3, 30e3]:
            f = 1 - (R + X) / cost_a
            pos = pa * f; pos[iR] += X / 0.35 if X else 0
            v = settle(win, pos, liq - cost_a * f - X) + rng.normal(M, 0.5 * M, len(F)) if M else settle(win, pos, liq - cost_a * f - X)
            s = stats(v)
            print(f"{M/1e3:4.0f}k | {R/1e3:.0f}k | {X/1e3:4.1f}k | {s['E']/1e3:6.1f}k | {s['p120']:5.1%} | {s['p150']:5.1%} | {s['p85']:5.1%} | {s['p70']:5.1%}")
