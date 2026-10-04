"""P9_plan_mc2: the catch-up odds after the dry run (analysis/p9/DRYRUN.md): the basket is cash- and spread-bound, so it settles
near 40-45k (m 5 / m 6) and is built over ~24 h; variants with a lower floor (bigger cushion) to see what it costs in ruin. Same
model as P9_plan_mc.py (D_mc.run on B_mc worlds, liquidation guard, impact 0.010, crash slip 10%, exit day 14, switch-on +10 h)."""
import sys
import numpy as np
sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p9')
import B_mc as M
import D_mc as D
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
rng = np.random.default_rng(11)
worlds = [('BASE', lambda: M.tilt_paths(rng, N), 0.5, 0.35), ('BEAR', lambda: M.tilt_paths(rng, N, 0.25, 0.5, 0.18), 0.3, 0.3),
          ('BULL', lambda: M.tilt_paths(rng, N, 0.06, 0.2, 0.45), 0.2, 0.15), ('FOOL', lambda: D.fool_paths(rng, N), 0.0, 0.2)]
real = dict(guard='liq', impact=0.010, crash_slip=0.10, exit_h=385, staged=True, m_fail=1.5, delay_h=10)
V = [
 ('cap 40k m5 built 24h (dry run: m 5 settles ~40k)', dict(real, m0=5.0, mult=5.0, cap=40000.0, ramp_h=24), 0),
 ('cap 45k m6 built 24h (stage3b)', dict(real, m0=6.0, mult=6.0, cap=45000.0, ramp_h=24), 0),
 ('cap 45k m6 built 24h + carry 300/day', dict(real, m0=6.0, mult=6.0, cap=45000.0, ramp_h=24), 300),
 ('cap 30k m5 built 24h (cash-bound day 1)', dict(real, m0=5.0, mult=5.0, cap=30000.0, ramp_h=24), 0),
 ('cap 45k m6 built 48h', dict(real, m0=6.0, mult=6.0, cap=45000.0, ramp_h=48), 0),
 ('floor 80k m5 cap 60k built 24h', dict(real, m0=5.0, mult=5.0, cap=60000.0, ramp_h=24, floor=80000.0), 0),
 ('floor 80k m6 cap 60k built 24h + carry', dict(real, m0=6.0, mult=6.0, cap=60000.0, ramp_h=24, floor=80000.0), 300),
 ('floor 75k m6 cap 70k built 24h', dict(real, m0=6.0, mult=6.0, cap=70000.0, ramp_h=24, floor=75000.0), 0),
 ('floor 75k m8 cap 80k built 24h ratchet 0.75', dict(real, m0=8.0, mult=8.0, cap=80000.0, ramp_h=24, floor=75000.0, ratchet=0.75), 0),
]
days = M.H / 24.0
res = {}
for wn, gen, w_mix, w_d in worlds:
    S = gen()
    print('#### world', wn, flush=True)
    for nm, kw, carry in V:
        am, aset, d, gap = D.run(S, rng, **kw)
        a = am + carry * days
        print("  %-46s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% DD>20 %5.1f%% med %6.0f" % (nm, 100 * (a >= 150000).mean(), 100 * (a >= 200000).mean(),
              100 * (a <= 85000).mean(), 100 * (d > 0.2).mean(), np.median(a)), flush=True)
        res.setdefault(nm, []).append((w_mix, w_d, a, d))
print('\n#### MIXED PRIOR | D PRIOR')
for nm, L in res.items():
    out = []
    for idx in (0, 1):
        f = lambda fn: sum(x[idx] * fn(x[2], x[3]) for x in L)
        out.append("P150 %4.1f%% P200 %4.1f%% P<=85 %3.1f%% DD>20 %3.1f%%" % (100 * f(lambda a, d: (a >= 150000).mean()),
                   100 * f(lambda a, d: (a >= 200000).mean()), 100 * f(lambda a, d: (a <= 85000).mean()), 100 * f(lambda a, d: (d > 0.2).mean())))
    print("%-46s | %s | %s" % (nm, out[0], out[1]))

