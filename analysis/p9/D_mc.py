"""D_mc: devil's-advocate Monte Carlo on the consensus plan (B-2 ratchet CPPI long the tilt) and explorer D's staged/hedged version.
Reuses B_mc.tilt_paths (imported, unchanged). Adds what B_mc leaves out:
  * GREATER-FOOL world: the plateau is now (K ~ U(0.12, 0.16)), demand then decays: s relaxes toward s x U(0.4, 0.8) with a 5-15 day
    time constant starting U(0, 5) days in; crash 20%.
  * OWN IMPACT: our buying lifts the price we pay and the mark we are valued at by ds_own = 0.010 x (basket / 80k) (D_data #6: lifting
    75% of the visible cheap-side asks moves the cross-section by ds +0.010); it reverses when we sell (we pay it twice).
  * CRASH LIQUIDITY: a sale within 6 h after a crash hour pays an extra 10% (worst 2-h block had $49k of bids within 1c vs $198k median).
  * GUARD ON THE MARK (B_mc, as coded) vs ON LIQUIDATION (B-10) and the floor gap (how far below the floor the guard fills).
  * EXIT FAILURE: with prob P_FAIL the T-7d exit does not happen (rule change, outage, frozen book) and the basket is held to the close;
    under "settled" it pays its Polymarket odds: 0.356 per $ at today's prices (D_data #7), scaled 0.356 x P(s0)/P(s_close).
  * STAGED / HEDGED variants (D-22): m 2 until a 36 h test (s >= 0.15 and still rising) then m 5; cap 60k; liquidation guard;
    exit day 14; optional hedge sleeve: keep short-tilt legs worth H$ of tilt exposure in the richest contracts (pays when s falls).
Run: python3 analysis/p9/D_mc.py [paths]"""
import sys
import numpy as np
sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p9')
import B_mc as M

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
H, A0, S0 = M.H, M.A0, M.S0
SETTLE_PER_USD = 0.356


def fool_paths(rng, n):
    K = rng.uniform(0.12, 0.16, n); r = rng.uniform(0.02, 0.045, n)
    start = rng.uniform(0, 120, n); tau = rng.uniform(120, 360, n); frac = rng.uniform(0.4, 0.8, n)
    lam = -np.log(1 - 0.20) / H
    s = np.full(n, S0); out = np.empty((H + 1, n)); out[0] = s; target = None
    for t in range(1, H + 1):
        crash = rng.random(n) < lam; f = rng.uniform(0.35, 0.75, n)
        s = np.where(crash, s * f, s)
        decay = t >= start
        tgt = np.where(decay, np.minimum(s, K) * frac, K)
        drift = np.where(decay, (S0 * frac - s) / tau, r * s * (1 - s / K))
        s = np.clip(s + drift + 0.02 * s * rng.standard_normal(n), 0.0, 0.95); out[t] = s
    return out


def run(S, rng, mult=5.0, floor=86000.0, ratchet=0.85, cap=80000.0, exit_h=168, guard='mark', impact=0.0, crash_slip=0.0,
        tc=0.03, staged=False, test_h=36, test_s=0.15, m0=2.0, hedge=0.0, p_fail=0.0, m_fail=None, ramp_h=0, delay_h=0, tnoise=0.0):
    n = S.shape[1]
    noise = np.exp(np.cumsum(0.0015 * rng.standard_normal(S.shape), axis=0))
    P = lambda s: 0.025 + 0.475 * s
    base = P(S) * noise * np.exp(tnoise * rng.standard_normal(S.shape))   # tnoise: transient (iid hourly) basket noise
    dS = np.vstack([np.zeros(n), np.diff(S, axis=0)])
    crash_recent = np.zeros_like(S, bool)
    ch = dS < -0.25 * np.maximum(np.vstack([S[:1], S[:-1]]), 1e-6)
    for k in range(6):
        crash_recent[k:] |= ch[:H + 1 - k]
    a = 1 - 0.5 ** (1 / 1.5)
    cash = np.full(n, A0); sh = np.zeros(n); peak = np.full(n, A0); mdd = np.zeros(n); rf = np.full(n, floor)
    mark = base[0].copy(); m = np.full(n, m0 if staged else mult); tested = False
    hedge_x = np.full(n, hedge)        # $ of tilt exposure short (marked -hedge_x per unit of s), held to exit, no capital modelled
    pass_rate = []
    gap_below = np.zeros(n); failed = rng.random(n) < p_fail
    s_entry = np.full(n, S0)
    for t in range(0, H + 1):
        own = impact * np.minimum(sh * base[t], cap) / 80000.0
        price = base[t] + 0.475 * own
        mark = mark + a * (price - mark) if t else price.copy()
        hedge_val = -hedge_x * (S[t] - S0)
        acct_m = cash + sh * mark + hedge_val
        liq = cash + sh * price * (1 - 0.03) + hedge_val
        peak = np.maximum(peak, acct_m); mdd = np.maximum(mdd, 1 - acct_m / peak)
        if t == H:
            break
        def trade(tgt):
            nonlocal cash, sh
            cur = sh * price; d = tgt - cur
            slip = np.where(crash_recent[t] & (d < 0), crash_slip, 0.0)
            # selling also removes our own impact from the price we get
            sell_px_adj = np.where(d < 0, 0.475 * impact * np.abs(d) / 80000.0 / np.maximum(price, 1e-6), 0.0)
            cost = np.abs(d) * (tc + 0.02 * np.abs(d) / 50000.0 + slip + sell_px_adj)
            sh = sh + d / price; cash = cash - d - cost
        if t >= H - exit_h:
            go = ~failed
            if (sh[go] != 0).any() or t == H - exit_h:
                trade(np.where(go, 0.0, sh * price))
            hedge_x = np.where(go, 0.0, hedge_x)
            continue
        rf = np.maximum(rf, ratchet * peak)
        if staged and not tested and t >= test_h:
            ok = (S[t] >= test_s) & (S[t] > S[max(0, t - 12)])
            m = np.where(ok, mult, m0 if m_fail is None else m_fail); tested = True
            pass_rate.append(ok.mean())
        g = acct_m if guard == 'mark' else liq
        if t < delay_h:
            continue
        tt = t - delay_h
        ramping = ramp_h and tt < ramp_h
        if tt % 24 == 0 or ramping:
            capt = cap * min(1.0, (tt + 1) / ramp_h) if ramp_h else cap
            tgt = np.clip(m * (g - rf), 0, np.minimum(capt, 0.9 * acct_m))
            if ramp_h:
                tgt = np.maximum(np.minimum(tgt, m * (g - rf) * min(1.0, (tt + 1) / ramp_h)), 0.0)
            cur = sh * price
            need = np.abs(tgt - cur) > 0.2 * np.maximum(cur, 1.0)
            trade(np.where(need, tgt, cur))
        else:
            bad = (g < rf + 1000) & (sh > 0)
            if bad.any():
                gap_below = np.where(bad, np.maximum(gap_below, rf - liq), gap_below)
                trade(np.where(bad, 0.0, sh * price))
    # close: marked = acct_m; settled = held basket pays Polymarket odds
    held_val_settled = sh * price * SETTLE_PER_USD * P(S0) / np.maximum(P(S[-1]), 1e-6)
    acct_set = cash + held_val_settled + 0.0
    run.pass_rate = pass_rate
    return acct_m, acct_set, mdd, gap_below


def row(nm, a, d):
    return "%-58s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% med %6.1fk p5 %6.1fk DD>20 %5.1f%%" % (
        nm, 100 * (a >= 150000).mean(), 100 * (a >= 200000).mean(), 100 * (a <= 85000).mean(), np.median(a) / 1e3,
        np.percentile(a, 5) / 1e3, 100 * (d > 0.2).mean())


if __name__ == '__main__':
    rng = np.random.default_rng(23)
    worlds = [('BASE', lambda: M.tilt_paths(rng, N), 0.5), ('BEAR', lambda: M.tilt_paths(rng, N, 0.25, 0.5, 0.18), 0.3),
              ('BULL', lambda: M.tilt_paths(rng, N, 0.06, 0.2, 0.45), 0.2), ('GREATER FOOL (K 0.12-0.16, decay)', lambda: fool_paths(rng, N), 0.0)]
    B2 = dict()
    real = dict(guard='liq', impact=0.010, crash_slip=0.10)
    variants = [
        ('B-2 as B_mc (mark guard, no impact)', B2),
        ('B-2 + own impact 0.010 + crash slip 10%', dict(impact=0.010, crash_slip=0.10)),
        ('B-2 realistic + liquidation guard (D baseline)', real),
        ('B-2 realistic + 5% exit failure (settled)', dict(real, p_fail=0.05)),
        ('D-22a half size: m 2.5 cap 40k, liq guard', dict(real, mult=2.5, cap=40000.0)),
        ('D-22b staged: m2 -> m5 on 36h test, cap 60k, exit d14', dict(real, staged=True, cap=60000.0, exit_h=385)),
        ('D-22c staged m2->m5 cap 80k exit T-7d', dict(real, staged=True)),
        ('D-22d staged + ratchet 0.88 + exit d14 cap 70k', dict(real, staged=True, cap=70000.0, exit_h=385, ratchet=0.88)),
        ('D-22e staged cap 70k exit d14 + 35.9k-X short hedge (keep toward book)', dict(real, staged=True, cap=70000.0, exit_h=385, hedge=35900.0)),
        ('D-22f staged m2->m6 cap 90k exit d14', dict(real, staged=True, mult=6.0, cap=90000.0, exit_h=385)),
        ('D-22g REVERSE stage: m5 now, cut to m1.5 if 36h test fails', dict(real, staged=True, m0=5.0, m_fail=1.5)),
        ('D-22h reverse stage + exit day 14', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385)),
        ('D-22i reverse stage + exit d14 + crash slip 30%', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, crash_slip=0.30)),
        ('D-22j reverse stage m6 cap 90k + exit d14', dict(real, staged=True, m0=6.0, mult=6.0, cap=90000.0, m_fail=1.5, exit_h=385)),
        ('B-2 realistic + exit d14', dict(real, exit_h=385)),
        ('D-22h + transient basket noise 3%/h (D_corr)', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, tnoise=0.03)),
        ('B-2 realistic + transient noise 3%/h', dict(real, tnoise=0.03)),
        ('D-22h built over 12 h', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, ramp_h=12)),
        ('D-22h built over 24 h', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, ramp_h=24)),
        ('D-22h built over 48 h (B-14 pace)', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, ramp_h=48)),
        ('D-22h deployed +10 h (s ~0.14), bought at once', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, delay_h=10)),
        ('D-22h deployed +10 h, built over 12 h', dict(real, staged=True, m0=5.0, m_fail=1.5, exit_h=385, delay_h=10, ramp_h=12)),
        ('D-22j m6 cap 90k deployed +10 h, built over 12 h', dict(real, staged=True, m0=6.0, mult=6.0, cap=90000.0, m_fail=1.5, exit_h=385, delay_h=10, ramp_h=12)),
        ('D-22j m6 cap 90k built over 24 h', dict(real, staged=True, m0=6.0, mult=6.0, cap=90000.0, m_fail=1.5, exit_h=385, ramp_h=24)),
    ]
    mix = {}
    for wn, gen, w in worlds:
        S = gen()
        print('\n#### world', wn, 'weight', w, '| s +1d med %.3f +7d %.3f close %.3f' % tuple(np.median(S[[24, 168, H]], axis=1)))
        for nm, kw in variants:
            am, aset, d, gap = run(S, rng, **kw)
            print(row(nm + ' [marked]', am, d) + (' test pass %.0f%%' % (100 * run.pass_rate[0]) if run.pass_rate else ''))
            if kw.get('p_fail'):
                print(row(nm + ' [settled]', aset, d))
            if nm.startswith('B-2 realistic + liq'):
                gb = gap[gap > 0]
                print('   floor gap when the guard fired: P(fired) %.1f%%, median %.1fk, p90 %.1fk below the floor'
                      % (100 * (gap > 0).mean(), np.median(gb) / 1e3 if len(gb) else 0, np.percentile(gb, 90) / 1e3 if len(gb) else 0))
            mix.setdefault(nm, []).append((w, am, d))
    print('\n#### MIXED PRIOR (base 0.5 / bear 0.3 / bull 0.2) and D-PRIOR (base 0.35 / bear 0.3 / bull 0.15 / greater fool 0.2)')
    for nm, L in mix.items():
        for pri, ws in [('mixed', [0.5, 0.3, 0.2, 0.0]), ('D', [0.35, 0.3, 0.15, 0.2])]:
            f = lambda fn: sum(wi * fn(a, d) for wi, (_, a, d) in zip(ws, L))
            print("%-52s %-5s P150 %5.1f%% P200 %5.1f%% P<=85 %5.1f%% DD>20 %5.1f%%" % (nm, pri, 100 * f(lambda a, d: (a >= 150000).mean()),
                  100 * f(lambda a, d: (a >= 200000).mean()), 100 * f(lambda a, d: (a <= 85000).mean()), 100 * f(lambda a, d: (d > 0.2).mean())))
