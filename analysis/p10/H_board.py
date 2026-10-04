"""H_board: the final leaderboard under outcome settlement (Package 10, explorer H). Same outcome draws as H_outcome
(rho 0.45, k 1.0, seed 7), so our account and every rival's are settled on the SAME world.
Field (1,039 accounts; journal: Rank 174 of 1039, Smart Score ranks 457 -> ~457 active, ~582 never traded = 100k):
  tilt-long 110 (longshot YES bought at p + s_e (c - p), s_e ~ U(0.01, 0.08) = the 2-5c fills; f ~ U(0.5, 1) of the account in
    1 + Poisson(4) contracts), directional 150 (f ~ U(0.2, 1) in 1 + Poisson(1.5) competitive / control contracts at the ask, 70% one
    party), value 40 (our plan (a) book scaled U(0.2, 1)), market makers 20 (100k + U(0, 15k)), noise traders 137 (f ~ U(0.05, 0.5)
    in 1-3 random contracts at mid + 2c), inactive 582 (100k).
Usage: python3 analysis/p10/H_board.py [N] [tilt_n] [dir_n]"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, simulate, settle

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
NT = int(sys.argv[2]) if len(sys.argv) > 2 else 110
ND = int(sys.argv[3]) if len(sys.argv) > 3 else 150
SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash, st = load()
win, F = simulate(d, N, rho=0.45, k=1.0)
rng = np.random.default_rng(21)
p, c, mid, ask = d.p0.values, d.c.values, d.mid.values, d.best_ask.values
M = len(d)
longpool = np.nonzero((p < 0.08) & (mid < 0.25) & d.party.isin(['Dem', 'Rep']).values)[0]
comppool = np.nonzero((p > 0.25) & (p < 0.75) & d.party.isin(['Dem', 'Rep']).values)[0]
ctrl = np.nonzero(d.race.str.startswith('U.S.').values)[0]
print(f"longshot pool {len(longpool)} contracts, sum p {p[longpool].sum():.2f} (expected longshot hits); competitive pool {len(comppool)}")
players = []   # (type, shares vector, cash)
for _ in range(NT):
    k = 1 + rng.poisson(4); js = rng.choice(longpool, min(k, len(longpool)), replace=False)
    f = rng.uniform(0.5, 1.0); w = rng.dirichlet(np.ones(len(js))); e = p[js] + rng.uniform(0.01, 0.08, len(js)) * (c[js] - p[js])
    sh = np.zeros(M); sh[js] = 100e3 * f * w / e
    players.append(('tilt', sh, 100e3 * (1 - f), (100e3 * f * w / e * mid[js]).sum() + 100e3 * (1 - f)))
cw = np.ones(len(comppool)); cw[np.isin(comppool, ctrl)] = 3; cw /= cw.sum()
for _ in range(ND):
    k = 1 + rng.poisson(1.5); f = rng.uniform(0.2, 1.0)
    js = rng.choice(comppool, min(k, len(comppool)), replace=False, p=cw)
    if rng.random() < 0.7:   # partisan: map every pick to one party's leg in that race
        party = 'Dem' if rng.random() < 0.5 else 'Rep'
        js = np.array([d.index[(d.race == d.race[j]) & (d.party == party)][0] for j in js])
        js = np.unique(js)
    w = rng.dirichlet(np.ones(len(js))); sh = np.zeros(M); sh[js] = 100e3 * f * w / (ask[js] + 0.005)
    players.append(('directional', sh, 100e3 * (1 - f), None))
pos_a = np.load(SCR + 'H_pos_a.npy')
cost_a = (pos_a.clip(min=0) * ask + (-pos_a).clip(min=0) * (1 - d.best_bid.values)).sum()
for _ in range(40):
    g = rng.uniform(0.2, 1.0)
    players.append(('value', g * pos_a, 100e3 - g * cost_a, None))
for _ in range(137):
    k = rng.integers(1, 4); js = rng.choice(M, k, replace=False); f = rng.uniform(0.05, 0.5); w = rng.dirichlet(np.ones(k))
    sh = np.zeros(M); sh[js] = 100e3 * f * w / np.clip(mid[js] + 0.02, 0.01, 0.99)
    players.append(('noise', sh, 100e3 * (1 - f), None))
S = np.array([pl[1] for pl in players])                        # players x contracts (YES shares; NO via negative)
C = np.array([pl[2] for pl in players])
final = C[None, :] + win @ S.clip(min=0).T + (1 - win) @ (-S).clip(min=0).T     # N x players
mm = 100e3 + rng.uniform(0, 15e3, (N, 20))
inactive_n = 1039 - len(players) - 20 - 1                     # minus us
final = np.hstack([final, mm])
types = np.array([pl[0] for pl in players] + ['mm'] * 20)
marks = np.array([pl[3] for pl in players if pl[0] == 'tilt'])
print(f"field: {len(types)} active rivals + {inactive_n} inactive at 100k (+ us)")
print(f"tilt-long accounts on marks today (bought 2-5c, marked at the 22:47 mids): median {np.median(marks)/1e3:.0f}k, max {marks.max()/1e3:.0f}k "
      f"(the real leader is +600% = 700k)")
for t in ['tilt', 'directional', 'value', 'noise', 'mm']:
    x = final[:, types == t]
    print(f"  {t:12s} n {x.shape[1]:3d}: final mean {x.mean()/1e3:6.1f}k median {np.median(x)/1e3:6.1f}k; P(account >= 150k) {np.mean(x>=150e3):5.1%}; "
          f"P(<= 50k) {np.mean(x<=50e3):5.1%}; mean count >= 150k per world {np.sum(x>=150e3,1).mean():.1f}")
srt = -np.sort(-final, axis=1)
def q(x): return f"p10 {np.percentile(x,10)/1e3:6.0f}k  p50 {np.percentile(x,50)/1e3:6.0f}k  p90 {np.percentile(x,90)/1e3:6.0f}k"
print("1st place :", q(srt[:, 0]))
print("10th place:", q(srt[:, 9]))
print("50th place:", q(srt[:, 49]))
print("100th place:", q(srt[:, 99]))
print("200th place:", q(srt[:, 199]))
for a in [108e3, 120e3, 150e3, 200e3]:
    rk = 1 + (final > a).sum(1)
    print(f"account {a/1e3:.0f}k ranks: p10 {np.percentile(rk,10):.0f} median {np.median(rk):.0f} p90 {np.percentile(rk,90):.0f};"
          f" P(top 10) {np.mean(rk<=10):.1%} P(top 50) {np.mean(rk<=50):.1%} P(top 100) {np.mean(rk<=100):.1%}")
# our plans on the same worlds
v_cur = settle(win, d.pos.values, cash)
v_a = settle(win, pos_a, 100e3 - cost_a + (st['liquidation_value'] - 100e3))
ia = d.index[d.label == 'Rep U.S. Senate'][0]
pos_c = pos_a * (1 - 22.5 / 100); pos_c[ia] += 22.5e3 / 0.35
v_c = settle(win, pos_c, (100e3 - cost_a) * (1 - 22.5 / 100) + (st['liquidation_value'] - 100e3))
for name, v in [('current book held', v_cur), ('(a) reallocation', v_a), ('(c) (a) x 0.775 + Rep Senate YES 22.5k', v_c)]:
    rk = 1 + (final > v[:, None]).sum(1)
    print(f"US {name:42s}: E {v.mean()/1e3:6.1f}k | rank median {np.median(rk):.0f} p10 {np.percentile(rk,10):.0f} p90 {np.percentile(rk,90):.0f} | "
          f"P(top 10) {np.mean(rk<=10):.1%} P(top 50) {np.mean(rk<=50):.1%} P(top 100) {np.mean(rk<=100):.1%}")
