"""H_frontier: the edge-per-$ frontier from the 22:47 books (top-3 levels) and plans (a) / (b) / (c) at the outcome.
Uses H_outcome's model (rho 0.45 base, rho_ctrl 0.85, calibration k). Read-only.
Usage: python3 analysis/p10/H_frontier.py [N] [rho] [k]"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, simulate, settle, stats, fmt, calibrate

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
RHO = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45
K = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
d, cash, st = load()
p = calibrate(d, K)          # the probabilities the plans are valued (and chosen) at
d['p'] = p
KV = float(os.environ.get('H_KVAL', K))      # value the (k-chosen) plans under a different calibration
win, F = simulate(d, N, rho=RHO, k=KV)
MAXC = float(os.environ.get('H_MAXC', 10000))   # per-contract collateral cap (max_position_frac 0.10 x 100k)
SEATS = os.environ.get('H_SEATS', '0') == '1'   # Senate control from the simulated seat count (Ohio pD 0.40, Osborn half)
if SEATS:
    from scipy.stats import norm as _n
    _r = np.random.default_rng(99)
    sen = d[d.race.str.endswith('Senate') & ~d.race.str.startswith('U.S.')]
    seats = win[:, sen[sen.party == 'Dem'].index.values].sum(1) + 0.5 * win[:, d.index[d.label == 'Ind Nebraska Senate'][0]]
    seats = seats + ((np.sqrt(RHO) * F + np.sqrt(1 - RHO) * _r.standard_normal(len(F))) < _n.ppf(0.40))
    demc = seats >= 17
    win[:, d.index[d.label == 'Dem U.S. Senate'][0]] = demc; win[:, d.index[d.label == 'Rep U.S. Senate'][0]] = ~demc
    print(f"SEATS mode: P(Dem Senate control) {demc.mean():.3f} (Polymarket {d.p0[d.label=='Dem U.S. Senate'].iloc[0]:.3f})")


def capit(items, maxc=MAXC):
    """truncate items (sorted by edge desc) so that each contract's cumulative collateral <= maxc"""
    items = items.copy()
    cum = items.groupby('i').usd.cumsum()
    allowed = (maxc - (cum - items.usd)).clip(lower=0)
    newusd = np.minimum(items.usd, allowed)
    items['q'] = items.q * np.where(items.usd > 0, newusd / items.usd, 0)
    items['usd'] = newusd
    return items[items.usd > 0.5].reset_index(drop=True)
W_marks = st['account_value']

# ---- 1. opportunities from the books (top 3 levels) ----
opp = []
for i, r in d.iterrows():
    for px, q in r.asks:                                   # buy YES
        if r.p > px:
            opp.append(dict(i=i, side=+1, px=px, q=q, cost=px, edge=(r.p - px) / px, ev=(r.p - px) * q, lvl='ask'))
    for px, q in r.bids:                                   # sell YES = hold NO, collateral 1 - px
        if r.p < px:
            opp.append(dict(i=i, side=-1, px=px, q=q, cost=1 - px, edge=(px - r.p) / (1 - px), ev=(px - r.p) * q, lvl='bid'))
opp = pd.DataFrame(opp)
opp['usd'] = opp.q * opp.cost
opp['label'] = d.label.values[opp.i]
opp = opp.sort_values('edge', ascending=False).reset_index(drop=True)
opp_raw = opp
opp = capit(opp)
print(f"per-contract cap ${MAXC:,.0f}: capped opportunities ${opp.usd.sum():,.0f}, EV {(opp.ev*opp.usd/opp_raw.set_index(opp_raw.index).usd.reindex(opp.index).fillna(1)).sum() if False else 0:,.0f}")
print(f"model rho {RHO} k {K}. Opportunities (levels with p beyond the price): {len(opp)} levels, ${opp.usd.sum():,.0f} of collateral, "
      f"EV {opp.ev.sum():,.0f}")
for e in [0.02, 0.05, 0.10, 0.20, 0.50]:
    m = opp.edge >= e
    print(f"  edge >= {e:4.0%}: ${opp.usd[m].sum():9,.0f} collateral, EV {opp.ev[m].sum():8,.0f} ({opp.ev[m].sum()/max(opp.usd[m].sum(),1):.1%}/$), levels {m.sum()}")
print(opp.head(12)[['label', 'lvl', 'px', 'q', 'usd', 'edge', 'ev']].round(3).to_string())


def greedy(items, budget):
    """items sorted by edge desc with usd; take until budget -> fraction taken per row"""
    cum = items.usd.cumsum().values
    frac = np.clip((budget - (cum - items.usd.values)) / items.usd.values, 0, 1)
    return frac


def book_from(items, frac, base=None):
    pos = np.zeros(len(d)) if base is None else base.copy()
    for (i, side, q), f in zip(items[['i', 'side', 'q']].values, frac):
        pos[int(i)] += side * q * f
    return pos


rows = []
def report(name, pos, cash_, cap, turnover, writes, extra=None):
    v = settle(win, pos, cash_) + (0 if extra is None else extra)
    s = stats(v)
    rows.append(dict(plan=name, **s, capital=cap, turnover=turnover, writes=writes))
    print(f"{name:52s} {fmt(s)} | cap {cap/1e3:5.1f}k turn {turnover/1e3:5.1f}k writes {writes}")
    return v

print("\n---- the current book and the pure frontier from cash ----")
v_cur = report("0 current book held", d.pos.values, cash, 0, 0, 0)
Wliq = st['liquidation_value']
for C in [25e3, 50e3, Wliq]:
    fr = greedy(opp, C)
    pos = book_from(opp, fr)
    used = (opp.usd * fr).sum()
    report(f"frontier from ${C/1e3:.0f}k cash (greedy edge/$)", pos, Wliq - used, used, used, int((fr > 0).sum()))

# ---- 2. plan (a): keep-or-sell every held position against the frontier ----
keep = []
for i, r in d[d.pos != 0].iterrows():
    if r.pos > 0:      # long YES: liquidation sells at the bid levels (walk 3 levels, rest at last level - 1c)
        px = r.best_bid - 0.0025; usd = r.pos * px; edge = (r.p - px) / px
    else:              # long NO: liquidation buys YES back at the ask
        px = r.best_ask + 0.0025; usd = -r.pos * (1 - px); edge = (px - r.p) / (1 - px)
    keep.append(dict(i=i, side=np.sign(r.pos), q=abs(r.pos), px=px, usd=usd, edge=edge, keep=True, label=r.label))
keep = pd.DataFrame(keep)
liq_total = cash + keep.usd.sum()
print(f"\nheld positions: liquidation value at bid/ask -/+0.25c {liq_total:,.0f} (status liquidation {Wliq:,.0f}); "
      f"hold edge per $: median {keep.edge.median():.1%}, $-weighted {np.average(keep.edge, weights=keep.usd):.1%}")
for e in [0, 0.02, 0.05, 0.10]:
    m = keep.edge < e
    print(f"  held with hold-edge < {e:4.0%}: ${keep.usd[m].sum():8,.0f} in {m.sum()} positions")
allit = capit(pd.concat([keep, opp_raw.assign(keep=False)], ignore_index=True).sort_values('edge', ascending=False).reset_index(drop=True))
fr = greedy(allit, liq_total)
pos_a = np.zeros(len(d))
sold = 0.0; bought = 0.0; writes = 0
for (i, side, q, k_, usd), f in zip(allit[['i', 'side', 'q', 'keep', 'usd']].values, fr):
    pos_a[int(i)] += side * q * f
    if k_:
        sold += usd * (1 - f); writes += int(f < 1)
    else:
        bought += usd * f; writes += int(f > 0)
marg = allit.edge.values[np.nonzero(fr > 0)[0].max()]
print(f"plan (a): marginal edge {marg:.1%}; sold ${sold:,.0f}, bought ${bought:,.0f}")
v_a = report("(a) max-EV reallocation held to the outcome", pos_a, liq_total - (allit.usd * fr).sum(), liq_total, sold + bought, writes)
# (a) with only edge >= 5c-equivalent: only swap when new edge exceeds hold edge by >= 3 points (friction)
# ---- (a') half-way: sell only positions with hold-edge < 2% and redeploy ----
low = keep[keep.edge < 0.02]
pos_h = d.pos.values.copy()
for i, side, q in low[['i', 'side', 'q']].values:
    pos_h[int(i)] -= side * q
freed = low.usd.sum() + cash
fr2 = greedy(opp[~opp.i.isin(low.i)], freed)
pos_h = book_from(opp[~opp.i.isin(low.i)], fr2, pos_h)
v_a2 = report("(a') sell hold-edge<2% only, redeploy greedily", pos_h, freed - (opp[~opp.i.isin(low.i)].usd * fr2).sum(), freed,
              2 * freed, len(low) + int((fr2 > 0).sum()))

# ---- 3. plan (b): (a) + recycling on convergence (tilt path B_mc: per-contract s_i(t)) ----
rng = np.random.default_rng(5)
held_a = np.nonzero(pos_a)[0]
cc = d.c.values; pp = d.p.values
P = 4000; days = 30
# market tilt path: log-s random walk with drift to B_mc quantiles (median 0.11 -> 0.29 at 7 d -> 0.215 at 30 d)
med = np.interp(np.arange(days + 1), [0, 1, 7, 14, 30], [0.11, 0.172, 0.289, 0.287, 0.215])
sig = np.interp(np.arange(days + 1), [0, 1, 7, 14, 30], [0.0, 0.13, 0.36, 0.38, 0.55])
Z = rng.standard_normal(P)
s_path = med[None, :] * np.exp(sig[None, :] * Z[:, None])          # (P, days+1), one comonotone draw per path
# contract dispersion: s_i = s * exp(0.5 * eta), eta AR(1) daily phi 0.7 (C_resid / D: idiosyncratic tilt noise ~ +-50%)
recyc_usd = np.zeros(P); gain = np.zeros(P)
dollars = np.abs(pos_a) * np.where(pos_a > 0, d.best_ask.values, 1 - d.best_bid.values)
gap0 = np.abs(d.mid.values - pp)
alive = np.ones((P, len(d)), bool)
eta = rng.standard_normal((P, len(d)))
for t in range(1, days + 1):
    eta = 0.7 * eta + np.sqrt(1 - 0.7 ** 2) * rng.standard_normal((P, len(d)))
    si = s_path[:, t:t + 1] * np.exp(0.5 * eta)
    mark = cc + (1 - si) * (pp - cc)
    conv = (np.abs(mark - pp) < 0.02) & alive & (pos_a != 0)[None, :] & (gap0 >= 0.02)[None, :]
    usd_t = (conv * dollars[None, :]).sum(1)
    # redeploy edge: (a)'s marginal edge scaled by s_t / s_0 (new edges scale with the tilt), minus 0.5c round trip on the sale
    e_new = marg * s_path[:, t] / 0.11
    remaining = (days - t) >= 1
    gain += usd_t * (e_new - 0.005) * remaining
    recyc_usd += usd_t
    alive &= ~conv
print(f"(b) recycling: $ recycled per path mean {recyc_usd.mean():,.0f} (p90 {np.percentile(recyc_usd,90):,.0f}); "
      f"gain mean {gain.mean():,.0f} p10 {np.percentile(gain,10):,.0f} p90 {np.percentile(gain,90):,.0f}")
g = rng.choice(gain, N)
v_b = report("(b) = (a) + recycle when marked within 2c of Polymarket", pos_a, liq_total - (allit.usd * fr).sum(), liq_total,
             sold + bought + recyc_usd.mean() * 2, writes, extra=g)

# ---- 4. plan (c): correlated party bets sized to P(final >= 150k) targets, core = (a) with the rest ----
comp = d[(d.p > 0.28) & (d.p < 0.72) & d.party.isin(['Dem', 'Rep'])]
print(f"\ncompetitive contracts: {len(comp)} ({comp.race.nunique()} races) ; visible ask depth (3 lv) Dem ${sum(sum(px*q for px,q in a) for a in comp[comp.party=='Dem'].asks):,.0f} "
      f"Rep ${sum(sum(px*q for px,q in a) for a in comp[comp.party=='Rep'].asks):,.0f}")


def buy(pos, i, usd):
    """walk the asks of contract i for usd dollars (beyond 3 levels at +1c); returns EV at p"""
    r = d.loc[i]; left = usd; ev = 0
    for px, q in r.asks + [[r.asks[-1][0] + 0.01, 1e12]]:
        take = min(q, left / px); pos[i] += take; left -= take * px; ev += take * (r.p - px)
        if left <= 1e-6:
            break
    return ev


def sleeve(kind, X):
    pos = np.zeros(len(d)); ev = 0
    if kind in ('Dem', 'Rep'):            # equal $ per competitive race on that party's leg
        g_ = comp[comp.party == kind]
        for i in g_.index:
            ev += buy(pos, i, X / len(g_))
    elif kind == 'RepSenate':             # Rep U.S. Senate control YES only
        ev += buy(pos, d.index[d.label == 'Rep U.S. Senate'][0], X)
    elif kind == 'RepSenate+bloc':        # half control market, half competitive Rep Senate/Governor legs
        ev += buy(pos, d.index[d.label == 'Rep U.S. Senate'][0], X / 2)
        g_ = comp[(comp.party == 'Rep') & ~comp.race.str.contains('House')]
        for i in g_.index:
            ev += buy(pos, i, X / 2 / len(g_))
    return pos, ev


def core_minus(X):
    f = greedy(allit, liq_total - X)
    pa = np.zeros(len(d))
    for (i, side, q), ff in zip(allit[['i', 'side', 'q']].values, f):
        pa[int(i)] += side * q * ff
    return pa, liq_total - X - (allit.usd * f).sum()


for kind in ['RepSenate', 'RepSenate+bloc', 'Rep', 'Dem']:
    print(f"-- sleeve {kind} (core = (a) with the rest) --")
    out = []
    for X in np.arange(0, 100001, 2500):
        ps, ev = sleeve(kind, X)
        pa, cleft = core_minus(X)
        v = settle(win, pa + ps, cleft)
        out.append((X, v, ev))
        if X % 10000 == 0:
            print(f"  sleeve ${X/1e3:5.1f}k (EV of the sleeve {ev:+7,.0f}): {fmt(stats(v))}")
    for target in [0.20, 0.30, 0.40]:
        hit = [(X, v, ev) for X, v, ev in out if (v >= 150e3).mean() >= target]
        if hit:
            X, v, ev = hit[0]
            s_ = stats(v); rows.append(dict(plan=f"(c) {kind} ${X/1e3:.1f}k + core (a); target P150 {target:.0%}", **s_,
                                            capital=liq_total, turnover=sold + bought + X, writes=writes + 6))
            print(f"  target {target:.0%}: sleeve ${X/1e3:.1f}k (sleeve EV {ev:+,.0f}) -> {fmt(s_)}")
        else:
            best = max(out, key=lambda o: (o[1] >= 150e3).mean())
            print(f"  target {target:.0%}: not reached with <= $100k (best P150 {(best[1]>=150e3).mean():.1%} at ${best[0]/1e3:.0f}k)")

pd.DataFrame(rows).to_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/H_table.csv', index=False)
np.save('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/H_pos_a.npy', pos_a)
print("\nTABLE")
for r in rows:
    print(f"| {r['plan']} | {r['E']/1e3:.1f}k | {r['p120']:.1%} | {r['p150']:.1%} | {r['p85']:.1%} | {r['p70']:.1%} | "
          f"{r['capital']/1e3:.0f}k / {r['turnover']/1e3:.0f}k | {r['writes']} |")
