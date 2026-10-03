"""C_alloc.py (explorer C, Package 9): ruin numbers for the risk settings, on explorer B's tilt Monte Carlo (B_mc.py, imported).
The risk settings cap the long-tilt basket (longshot YES / favourite NO): for such a basket both risk measures equal its cost
(sum-of-maxima: every leg can lose its whole value; "correlated": 3 sd of ~40-60 independent 10c legs = 9X/sqrt(N) > X, so the
min() in the cycle picks the sum-of-maxima = X). So:
  max_worst_case_frac 0.30            -> basket <= 0.30 x account - W_other
  worst_case_backstop_frac 0.8 / 0.9  -> basket <= 0.8 / 0.9 x account - W_other   (only if the 0.30 cap is raised past it)
  off                                 -> basket <= 0.9 x account (B_mc's cash cap)
W_other = the rest of the book's sum-of-maxima after the toward book is flattened: ~5k (assumed; the NO+NO sets add ~0).
Each cap is run (a) as B-2's ratchet-CPPI (m 5, floor max(86k, 0.85 peak), exit T-7d) and (b) with no floor (always at the cap,
exit T-7d) = what the setting alone allows. Plus a fixed carry from sets/market making (+0.3k/day... see ideas_C C-15) as a variant.
Run: python3 analysis/p9/C_alloc.py [paths]   (4000 paths x 3 worlds, ~1-2 min)
"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import B_mc as B
N = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
W_OTHER = 5000.0
caps = [("cap 0.30 (risk cap as is)", 0.30 * 101000 - W_OTHER), ("cap 0.60 (override max)", 0.60 * 101000 - W_OTHER), ("backstop 0.8", 0.8 * 101000 - W_OTHER),
        ("backstop 0.9", 0.9 * 101000 - W_OTHER), ("off (cash only)", 0.9 * 101000)]
worlds = [('BASE', B.CRASH_P, B.END_P, 0.30, 0.5), ('BEAR', 0.25, 0.5, 0.18, 0.3), ('BULL', 0.06, 0.2, 0.45, 0.2)]
rng = np.random.default_rng(11)
mix = {}
for wl, cp, ep, km, w in worlds:
    S = B.tilt_paths(rng, N, cp, ep, km)
    for cn, cap in caps:
        for vn, kw in (("CPPI m5 floor 86k/0.85pk", dict(mult=5.0, floor=86000.0)),
                       ("no floor, at cap", dict(mult=100.0, floor=0.0))):
            acct, mdd, hist = B.run(S, rng, 'cppi_ratchet' if vn.startswith("CPPI") else 'cppi', exit_h=168, cap=cap, **kw)
            nm = f"{cn:28s} | {vn}"
            mix.setdefault(nm, []).append((w, acct, mdd))
            # + carry: sets/MM/arb income of `carry`/day added linearly (riskless approximation)
            for carry in (300.0,):
                a2 = acct + carry * 30
                mix.setdefault(nm + f" + carry {carry:.0f}/d", []).append((w, a2, mdd))
print("MIXED PRIOR (B's base 0.5 / bear 0.3 / bull 0.2), %d paths per world" % N)
for nm, L in mix.items():
    f = lambda fn: sum(w * fn(a, d) for w, a, d in L)
    print("%-78s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% DD>20%% %5.1f%% med %6.1fk" % (
        nm, 100 * f(lambda a, d: (a >= 150000).mean()), 100 * f(lambda a, d: (a >= 200000).mean()),
        100 * f(lambda a, d: (a <= 85000).mean()), 100 * f(lambda a, d: (d > 0.2).mean()), f(lambda a, d: np.median(a)) / 1e3))

# capital split: basket cap vs carry from the capital left to market making / carousel (carry per day assumed proportional to the
# capital freed: 1-2 Oct MM earned ~1.0-1.6k/day at 1 h mark-out with most of the account; assume 40 per day per 1k of capital,
# i.e. 600/day on 15k, plus the carousel's ~300/day on 10k; C_mm.py / C_carousel.py)
print("\nCAPITAL SPLIT (CPPI m5 floor 86k/0.85 peak, exit T-7d), mixed prior")
mix2 = {}
rng = np.random.default_rng(12)
for wl, cp, ep, km, w in worlds:
    S = B.tilt_paths(rng, N, cp, ep, km)
    for cap, carry in ((90000.0, 0.0), (80000.0, 150.0), (70000.0, 450.0), (60000.0, 750.0), (50000.0, 1050.0)):
        acct, mdd, _ = B.run(S, rng, 'cppi_ratchet', exit_h=168, cap=cap, mult=5.0, floor=86000.0)
        for cf in ((1.0,) if carry == 0 else (1.0, 0.5)):
            mix2.setdefault((cap, carry * cf), []).append((w, acct + carry * cf * 30, mdd))
for (cap, carry), L in mix2.items():
    f = lambda fn: sum(w * fn(a, d) for w, a, d in L)
    print("basket cap %5.0fk + carry %5.0f/day: P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%%" % (cap / 1e3, carry,
          100 * f(lambda a, d: (a >= 150000).mean()), 100 * f(lambda a, d: (a >= 200000).mean()), 100 * f(lambda a, d: (a <= 85000).mean())))
