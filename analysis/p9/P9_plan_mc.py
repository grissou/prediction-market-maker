"""P9_plan_mc: the catch-up plan's odds as the owner will run it (executor, Package 9 Phase 2).
D_mc.run (unchanged) on B_mc's tilt worlds, with: deployment delay from the model's t0 (3 Oct 23:00 UTC) to the morning switch-on,
the planner's build time (basket_build_hours), m / cap / ratchet (= basket_floor_peak_frac) / kill, liquidation guard, own impact 0.010,
post-crash slippage 10%, exit at day 14 (basket_exit_utc 18 Oct 12:00 ~ T-16 d; D-22h), and carry from the set carousel / market making
added linearly to the final account (conservative: it does not feed the cushion). Prints P(>= 150k), P(>= 200k), P(<= 85k), P(DD > 20%)
under the mixed prior (base 0.5 / bear 0.3 / bull 0.2) and D's prior (adds the greater-fool world at 0.2).
Run: nice python3 analysis/p9/P9_plan_mc.py [paths]  (~N x 721 x variants; 4000 paths ~ a few minutes on one CPU)"""
import sys
import numpy as np
sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p9')
import B_mc as M
import D_mc as D

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
rng = np.random.default_rng(7)
worlds = [('BASE', lambda: M.tilt_paths(rng, N), 0.5, 0.35), ('BEAR', lambda: M.tilt_paths(rng, N, 0.25, 0.5, 0.18), 0.3, 0.3),
          ('BULL', lambda: M.tilt_paths(rng, N, 0.06, 0.2, 0.45), 0.2, 0.15), ('FOOL', lambda: D.fool_paths(rng, N), 0.0, 0.2)]
real = dict(guard='liq', impact=0.010, crash_slip=0.10, exit_h=385, staged=True, m_fail=1.5)
DELAY = 10            # switch-on ~09:00 UTC 4 Oct
variants = [
    ('hold the current book (no basket)', dict(real, mult=0.0, m0=0.0, cap=0.0), 0),
    ('S2: m5 cap80k, on at +10h, built 4h', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=4), 0),
    ('S2 + carry 300/day (carousel + MM)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=4), 300),
    ('S2b: m6 cap90k, +10h, built 4h', dict(real, m0=6.0, mult=6.0, cap=90000.0, delay_h=DELAY, ramp_h=4), 0),
    ('S2b + carry 300/day', dict(real, m0=6.0, mult=6.0, cap=90000.0, delay_h=DELAY, ramp_h=4), 300),
    ('S2 built 12h (ask-share caps bind)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=12), 0),
    ('S2 tighter: ratchet 0.88 (kill_dd 0.12)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=4, ratchet=0.88), 0),
    ('S2 looser: ratchet 0.80 (kill_dd 0.20)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=4, ratchet=0.80), 0),
    ('S1 only: half size m2.5 cap40k', dict(real, m0=2.5, mult=2.5, cap=40000.0, delay_h=DELAY, ramp_h=4), 0),
    ('S2 on at +20h (evening switch-on)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=20, ramp_h=4), 0),
    ('S2 on at +34h (5 Oct morning)', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=34, ramp_h=4), 0),
    ('S2 + 5% exit failure, SETTLED rule', dict(real, m0=5.0, mult=5.0, cap=80000.0, delay_h=DELAY, ramp_h=4, p_fail=0.05), 0),
]
days = M.H / 24.0
res = {}
for wn, gen, w_mix, w_d in worlds:
    S = gen()
    print('#### world', wn, '| s +1d med %.3f +7d %.3f +14d %.3f close %.3f' % tuple(np.median(S[[24, 168, 336, M.H]], axis=1)), flush=True)
    for nm, kw, carry in variants:
        am, aset, d, gap = D.run(S, rng, **kw)
        a = am + carry * days
        if kw.get('p_fail'):
            a = aset + carry * days
        p = lambda f: 100 * f(a, d)
        print("  %-44s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% DD>20 %5.1f%% med %6.0f p10 %6.0f" % (
            nm, p(lambda a, d: (a >= 150000).mean()), p(lambda a, d: (a >= 200000).mean()), p(lambda a, d: (a <= 85000).mean()),
            p(lambda a, d: (d > 0.2).mean()), np.median(a), np.percentile(a, 10)), flush=True)
        res.setdefault(nm, []).append((w_mix, w_d, a, d))
print('\n#### MIXED PRIOR (base .5 / bear .3 / bull .2) | D PRIOR (base .35 / bear .3 / bull .15 / greater fool .2)')
for nm, L in res.items():
    out = []
    for idx in (0, 1):
        f = lambda fn: sum(x[idx] * fn(x[2], x[3]) for x in L)
        out.append("P150 %4.1f%% P200 %4.1f%% P<=85 %3.1f%% DD>20 %3.1f%%" % (100 * f(lambda a, d: (a >= 150000).mean()),
                   100 * f(lambda a, d: (a >= 200000).mean()), 100 * f(lambda a, d: (a <= 85000).mean()), 100 * f(lambda a, d: (d > 0.2).mean())))
    print("%-44s | %s | %s" % (nm, out[0], out[1]))
