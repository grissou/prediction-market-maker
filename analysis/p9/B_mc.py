"""B_mc: Monte Carlo of long-the-tilt positioning variants to 4 Nov 00:00 UTC (explorer B, Package 9).

TILT MODEL (hourly, s on the OLS scale of B_tilt.py, s0 = 0.12 at 3 Oct 23:00, horizon 721 h):
  logistic drift   ds = r s (1 - s/K) dt, K ~ lognormal(median 0.30, sd log 0.5) clipped [0.14, 0.6] (B_logistic: OLS profile
                   flat for K >= 0.25, K < 0.20 rejected; equal-weight series prefers 0.15-0.25), r ~ U(0.02, 0.045)/h (fit 0.033)
  diffusion        sd 0.02 s per sqrt(h) (0.0024/h at s0; B_tilt hourly ds sd 0.0032 includes measurement noise)
  crash            hazard CRASH_P over the horizon (default 12%): s *= U(0.35, 0.75) inside one hour, K scaled by the same factor
  endgame          with prob END_P (default 0.35) the tilt unwinds from T - U(24, 120) h toward s x U(0.2, 0.6), time constant 24 h
BASKET: cheap-side YES / favourite NO with price P(s) = 0.025 + 0.475 s (binary longshot at Polymarket 2.5c), times a basket
  noise factor (0.15%/h, Polymarket moves on ~30 contracts). MARK: EWMA of the basket price, half-life 1.5 h (B_mark: the
  exchange mark tracks the mid with a 0.5-4 h smoothing). COSTS: 3% of $ traded + 2% x ($ traded / 50k) impact; flattening the
  current toward book first costs 700 (status: mark 101,017 vs liquidation 100,310). CAP: basket <= 60k$ (ask depth ~86k$ on top-3).
END RULES: 'marked' = account at the lagged mark at the close; 'settled' = basket still held at the close pays its Polymarket
  probability (30 independent 2.5c longshots); exits before the close are cash under both.
Prints per variant: P(>=150k), P(>=200k), P(<=85k), median, P(maxDD>20%), median maxDD, for marked and settled; and the s
distribution at +1/+7/+14/+30 d. Run: python3 analysis/p9/B_mc.py [paths]"""
import sys
import numpy as np

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
H = 721
A0 = 101017.0 - 700
S0 = 0.12
CRASH_P, END_P = 0.12, 0.35


def tilt_paths(rng, n, crash_p=CRASH_P, end_p=END_P, kmed=0.30):
    K = np.clip(np.exp(np.log(kmed) + 0.5 * rng.standard_normal(n)), 0.14, 0.6)
    r = rng.uniform(0.02, 0.045, n)
    lam = -np.log(1 - crash_p) / H
    end_on = rng.random(n) < end_p
    end_start = H - rng.uniform(24, 120, n)
    end_frac = rng.uniform(0.2, 0.6, n)
    s = np.full(n, S0); out = np.empty((H + 1, n)); out[0] = s
    target = np.zeros(n); started = np.zeros(n, bool)
    for t in range(1, H + 1):
        crash = rng.random(n) < lam
        f = rng.uniform(0.35, 0.75, n)
        s = np.where(crash, s * f, s); K = np.where(crash, K * f, K)
        newly = end_on & (~started) & (t >= end_start)
        target = np.where(newly, s * end_frac, target); started |= newly
        drift = np.where(started, (target - s) / 24.0, r * s * (1 - s / K))
        s = np.clip(s + drift + 0.02 * s * rng.standard_normal(n), 0.0, 0.95)
        out[t] = s
    return out


def run(S, rng, variant, exit_h=48, floor=88000.0, mult=2.5, cap=60000.0, stop=0.015, tc=0.03):
    n = S.shape[1]
    price = 0.025 + 0.475 * S
    noise = np.exp(np.cumsum(0.0015 * rng.standard_normal(S.shape), axis=0))
    price = price * noise
    mark = np.empty_like(price); mark[0] = price[0]
    a = 1 - 0.5 ** (1 / 1.5)
    for t in range(1, H + 1):
        mark[t] = mark[t - 1] + a * (price[t] - mark[t - 1])
    cash = np.full(n, A0); sh = np.zeros(n)
    peak = np.full(n, A0); mdd = np.zeros(n); runmax = np.full(n, S0); stopped = np.zeros(n, bool); entries = np.zeros(n)
    acct_hist = np.empty((H + 1, n)); acct_hist[0] = A0
    exit_t = H - exit_h
    rf = floor * np.ones(n)

    def trade(target_dollars, t):
        nonlocal cash, sh
        p = price[t]
        cur = sh * p
        d = target_dollars - cur
        cost = np.abs(d) * (tc + 0.02 * np.abs(d) / 50000.0)
        sh = sh + d / p
        cash = cash - d - cost

    for t in range(0, H + 1):
        acct = cash + sh * mark[t]
        peak = np.maximum(peak, acct); mdd = np.maximum(mdd, 1 - acct / peak)
        acct_hist[t] = acct
        if t >= exit_t:
            if t == exit_t or (sh != 0).any():
                trade(np.zeros(n), t)
            continue
        runmax = np.maximum(runmax, S[t])
        if variant == 'static':
            if t == 0:
                trade(np.full(n, 40000.0), t)
        elif variant in ('cppi', 'cppi_ratchet'):
            if variant == 'cppi_ratchet':
                rf = np.maximum(rf, 0.85 * peak)
            if t % 24 == 0:
                tgt = np.clip(mult * (acct - rf), 0, np.minimum(cap, 0.9 * acct))
                cur = sh * price[t]
                need = np.abs(tgt - cur) > 0.2 * np.maximum(cur, 1.0)
                trade(np.where(need, tgt, cur), t)
            else:   # intraday floor guard
                bad = acct < rf + 1000
                if bad.any():
                    trade(np.where(bad, 0.0, sh * price[t]), t)
        elif variant == 'trend':
            # full size while s is within `stop` of its running max; out on the stop; back in on a new high (max 3 entries);
            # account kill at 90k
            if t == 0:
                trade(np.full(n, min(cap, 50000.0)), t); entries += 1
            if t % 4 == 0:
                hit = (sh > 0) & ((S[t] < runmax - stop) | (acct < 90000))
                back = (sh == 0) & (S[t] >= runmax) & (entries < 3) & (acct >= 92000) & (t >= 24)
                if hit.any() or back.any():
                    tgt = np.where(hit, 0.0, np.where(back, np.minimum(cap, 0.5 * acct), sh * price[t]))
                    entries += back
                    trade(tgt, t)
                    runmax = np.where(hit, S[t], runmax)
        elif variant == 'hold_current':
            pass
    acct_marked = acct_hist[-1]
    return acct_marked, mdd, acct_hist


def summary(name, acct, mdd):
    return "%-34s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% med %6.1fk p5 %6.1fk | DD>20%% %5.1f%% medDD %4.1f%%" % (
        name, 100 * (acct >= 150000).mean(), 100 * (acct >= 200000).mean(), 100 * (acct <= 85000).mean(), np.median(acct) / 1e3,
        np.percentile(acct, 5) / 1e3, 100 * (mdd > 0.2).mean(), 100 * np.median(mdd))


if __name__ == '__main__':
    rng = np.random.default_rng(7)
    worlds = [('BASE', CRASH_P, END_P, 0.30, 0.5), ('BEAR (K 0.18, crash 25%, end 50%)', 0.25, 0.5, 0.18, 0.3),
              ('BULL (K 0.45, crash 6%, end 20%)', 0.06, 0.2, 0.45, 0.2)]
    variants = [('static', {}), ('static', {'exit_h': 168}), ('static', {'exit_h': 385}), ('cppi', {'mult': 4.0}),
                ('cppi_ratchet', {'mult': 3.0}), ('cppi_ratchet', {'mult': 4.0, 'floor': 86000.0}),
                ('cppi_ratchet', {'mult': 4.0, 'floor': 86000.0, 'exit_h': 168}),
                ('cppi_ratchet', {'mult': 5.0, 'floor': 86000.0, 'exit_h': 168, 'cap': 80000.0}),
                ('cppi_ratchet', {'mult': 6.0, 'floor': 86000.0, 'exit_h': 168, 'cap': 90000.0}),
                ('trend', {}), ('trend', {'exit_h': 168})]
    mix = {}
    for label, cp, ep, km, w in worlds:
        S = tilt_paths(rng, N, cp, ep, km)
        print("\n######## tilt world:", label, "weight", w)
        for d in [1, 7, 14, 30]:
            q = np.percentile(S[24 * d], [5, 25, 50, 75, 95])
            print("s at +%2d d: p5 %.3f p25 %.3f med %.3f p75 %.3f p95 %.3f | P(s<0.12) %.2f" % (d, *q, (S[24 * d] < 0.12).mean()))
        cur = A0 + 700 - 35900 * (S[-1] - S0)
        path = A0 + 700 - 35900 * (S - S0)
        dd_cur = np.max(1 - path / np.maximum.accumulate(path, axis=0), axis=0)
        print(summary("V0 current book held (marked)", cur, dd_cur))
        mix.setdefault('V0', []).append((w, cur, dd_cur))
        for v, kw in variants:
            acct, mdd, hist = run(S, rng, v, **kw)
            nm = "%s %s" % (v, kw if kw else '')
            print(summary(nm, acct, mdd))
            mix.setdefault(nm, []).append((w, acct, mdd))
    print("\n######## MIXED PRIOR (base 0.5 / bear 0.3 / bull 0.2)")
    for nm, L in mix.items():
        f = lambda fn: sum(w * fn(a, d) for w, a, d in L)
        print("%-80s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% DD>20%% %5.1f%%" % (nm, 100 * f(lambda a, d: (a >= 150000).mean()),
              100 * f(lambda a, d: (a >= 200000).mean()), 100 * f(lambda a, d: (a <= 85000).mean()), 100 * f(lambda a, d: (d > 0.2).mean())))
