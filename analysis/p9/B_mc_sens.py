"""B_mc_sens: sensitivity of the best B_mc variant (ratchet CPPI m5, floor max(86k, 0.85 peak), cap 80k, exit T-7d) to costs,
crash odds, exit timing and the ceiling K, in the BASE world (B_mc.tilt_paths). Run: python3 analysis/p9/B_mc_sens.py [paths]"""
import sys
import numpy as np
sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p9')
import B_mc as M
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
rng = np.random.default_rng(11)
best = dict(mult=5.0, floor=86000.0, exit_h=168, cap=80000.0)
S = M.tilt_paths(rng, N)
for nm, kw in [('best', {}), ('tc 5%', {'tc': 0.05}), ('exit T-1d', {'exit_h': 24}), ('exit T-14d', {'exit_h': 336}),
               ('exit day 10 (T-21d)', {'exit_h': 480}), ('cap 50k', {'cap': 50000.0}), ('m 8 cap 100k', {'mult': 8.0, 'cap': 100000.0})]:
    a, d, _ = M.run(S, rng, 'cppi_ratchet', **{**best, **kw}); print(M.summary(nm, a, d))
for nm, args in [('crash 25%', (0.25, M.END_P, 0.30)), ('crash 0%', (0.0, M.END_P, 0.30)), ('K median 0.20', (M.CRASH_P, M.END_P, 0.20)),
                 ('K median 0.15 (plateau now)', (M.CRASH_P, M.END_P, 0.15)), ('endgame 70%', (M.CRASH_P, 0.7, 0.30))]:
    S2 = M.tilt_paths(rng, N, *args)
    a, d, _ = M.run(S2, rng, 'cppi_ratchet', **best); print(M.summary(nm, a, d))
