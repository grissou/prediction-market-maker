"""Z_board20: the final leaderboard if fewer than ~20% of participants ever trade (owner, 4 Oct 17:00).
H_board's field re-scaled: total ~1,090 accounts; active share ACT (0.20 / 0.42 as in H / 0.60 if late entrants pile in);
the active mix as H_board (tilt-long 24%, directional 33%, value 9%, noise 30%, market makers 4%); inactive accounts at exactly 100k
INCLUDED in the ranking (an account below 100k ranks behind every one of them). Same outcome worlds as H_outcome (rho 0.45, k 1).
Usage: python3 analysis/p12/Z_board20.py [N]"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'p10'))
from H_outcome import load, simulate, settle

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
TOTAL = 1090
SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash, st = load()
win, F = simulate(d, N, rho=0.45, k=1.0)
p, c, mid, ask = d.p0.values, d.c.values, d.mid.values, d.best_ask.values
M = len(d)
longpool = np.nonzero((p < 0.08) & (mid < 0.25) & d.party.isin(['Dem', 'Rep']).values)[0]
comppool = np.nonzero((p > 0.25) & (p < 0.75) & d.party.isin(['Dem', 'Rep']).values)[0]
ctrl = np.nonzero(d.race.str.startswith('U.S.').values)[0]
pos_a = np.load(SCR + 'H_pos_a.npy')
cost_a = (pos_a.clip(min=0) * ask + (-pos_a).clip(min=0) * (1 - d.best_bid.values)).sum()
v_cur = settle(win, d.pos.values, cash)

def field(active_n, rng):
    nt, nd, nv, nn, nm = [max(1, int(round(active_n * f))) for f in (0.24, 0.33, 0.09, 0.30, 0.04)]
    S, C = [], []
    for _ in range(nt):
        k = 1 + rng.poisson(4); js = rng.choice(longpool, min(k, len(longpool)), replace=False)
        f = rng.uniform(0.5, 1.0); w = rng.dirichlet(np.ones(len(js))); e = p[js] + rng.uniform(0.01, 0.08, len(js)) * (c[js] - p[js])
        sh = np.zeros(M); sh[js] = 100e3 * f * w / e; S.append(sh); C.append(100e3 * (1 - f))
    cw = np.ones(len(comppool)); cw[np.isin(comppool, ctrl)] = 3; cw /= cw.sum()
    for _ in range(nd):
        k = 1 + rng.poisson(1.5); f = rng.uniform(0.2, 1.0)
        js = rng.choice(comppool, min(k, len(comppool)), replace=False, p=cw)
        if rng.random() < 0.7:
            party = 'Dem' if rng.random() < 0.5 else 'Rep'
            js = np.unique(np.array([d.index[(d.race == d.race[j]) & (d.party == party)][0] for j in js]))
        w = rng.dirichlet(np.ones(len(js))); sh = np.zeros(M); sh[js] = 100e3 * f * w / (ask[js] + 0.005); S.append(sh); C.append(100e3 * (1 - f))
    for _ in range(nv):
        g = rng.uniform(0.2, 1.0); S.append(g * pos_a); C.append(100e3 - g * cost_a)
    for _ in range(nn):
        k = rng.integers(1, 4); js = rng.choice(M, k, replace=False); f = rng.uniform(0.05, 0.5); w = rng.dirichlet(np.ones(k))
        sh = np.zeros(M); sh[js] = 100e3 * f * w / np.clip(mid[js] + 0.02, 0.01, 0.99); S.append(sh); C.append(100e3 * (1 - f))
    S, C = np.array(S), np.array(C)
    final = C[None, :] + win @ S.clip(min=0).T + (1 - win) @ (-S).clip(min=0).T
    mm = 100e3 + rng.uniform(0, 15e3, (N, nm))
    inactive = np.full((N, TOTAL - active_n - 1), 100e3)
    return np.hstack([final, mm, inactive]), (nt, nd, nv, nn, nm)

def q(x): return f"p10 {np.percentile(x,10)/1e3:5.0f}k p50 {np.percentile(x,50)/1e3:5.0f}k p90 {np.percentile(x,90)/1e3:5.0f}k"
for act in (0.20, 0.42, 0.60):
    rng = np.random.default_rng(5)
    active_n = int(TOTAL * act)
    final, mix = field(active_n, rng)
    srt = -np.sort(-final, axis=1)
    print(f"\n#### active share {act:.0%} ({active_n} active: tilt/dir/value/noise/mm {mix}; {TOTAL-active_n-1} inactive at 100k)")
    for r in (0, 2, 9, 24, 49, 99):
        print(f"  place {r+1:3d}: {q(srt[:, r])}")
    above100 = (final[:, :sum(mix)] > 100e3).mean(1)
    print(f"  active rivals finishing above 100k: mean {above100.mean()*sum(mix):.0f} of {sum(mix)}")
    for a in (103e3, 107e3, 112e3, 120e3, 133e3, 150e3):
        rk = 1 + (final > a).sum(1)
        print(f"  account {a/1e3:.0f}k: rank median {np.median(rk):4.0f} (p10 {np.percentile(rk,10):4.0f}, p90 {np.percentile(rk,90):4.0f}); P(top 10) {np.mean(rk<=10):5.1%} P(top 50) {np.mean(rk<=50):5.1%} P(top 100) {np.mean(rk<=100):5.1%}")
    rk = 1 + (final > v_cur[:, None]).sum(1)
    print(f"  OUR current book (E {v_cur.mean()/1e3:.1f}k): rank median {np.median(rk):.0f} (p10 {np.percentile(rk,10):.0f}, p90 {np.percentile(rk,90):.0f}); P(top 10) {np.mean(rk<=10):.1%} P(top 50) {np.mean(rk<=50):.1%} P(top 100) {np.mean(rk<=100):.1%}; P(below 100k) {np.mean(v_cur<100e3):.1%}")
