"""I_senate: option 2 in depth (Package 10, explorer I). Read-only; imports H_outcome; reuses H_board's field (same worlds).
1. The Senate seat-count check: 35 listed Senate contracts' races (34 seats + the control market) + the unlisted Ohio special.
   Dems hold 34 seats not up; with VP Vance (R) breaking ties they need 17 of the 35 up. Sensitivities: rho, Ohio pD, Alaska pD (no
   Polymarket reference: the tournament mid 0.643 is used by H), Osborn (Ind Nebraska) caucusing with the Dems.
2. Rep-side instruments priced at the ask / bid with depth: Rep U.S. Senate YES (ask), Dem U.S. Senate short (bid), Rep legs of the
   competitive Senate races; edge vs Polymarket; the bloc's payoff distribution.
3. Sleeves on top of H's plan (a) core (H_pos_a.npy scaled down by the sleeve $): E, P(>=120/150k), P(<=85/70k), and the rank on
   H_board's simulated field, under (A) Polymarket control odds and (B) the seat-count control (the control market settled by the seats).
Usage: python3 analysis/p10/I_senate.py [N]"""
import sys, os, io, contextlib
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, simulate, settle, stats

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash, st = load()
sen = d[d.race.str.endswith('Senate') & ~d.race.str.startswith('U.S.')]
races = sorted(sen.race.unique())
iD = {r: sen.index[(sen.race == r) & (sen.party == 'Dem')][0] for r in races}
pD = {r: float(d.p0[iD[r]]) for r in races}
print(f"listed Senate races {len(races)}; E[Dem seats] {sum(pD.values()):.2f}; Dem needs 17 of 35 (34 not up, VP R)")
print("competitive (0.05 < pDem < 0.95):", ", ".join(f"{r.replace(' Senate','')} {pD[r]:.2f}" for r in races if 0.05 < pD[r] < 0.95))
pc = float(d.p0[d.label == 'Dem U.S. Senate'].iloc[0])


def seat_p(rho=0.45, ohio=0.40, alaska=None, osborn=0.0, n=40000, seed=3, Fv=None):
    rng = np.random.default_rng(seed)
    F = rng.standard_normal(n) if Fv is None else Fv
    n = len(F)
    tot = np.zeros(n)
    for r in races:
        p = pD[r] if not (r == 'Alaska Senate' and alaska is not None) else alaska
        g = sen[sen.race == r]; pI = float(g.p0[g.party == 'Ind'].sum())
        z = np.sqrt(rho) * F + np.sqrt(1 - rho) * rng.standard_normal(n)
        dem = z < norm.ppf(np.clip(p / (1 - pI) if pI < 1 else 0, 1e-6, 1 - 1e-6))
        ind = rng.random(n) < pI
        tot += dem & ~ind
        if r == 'Nebraska Senate':
            tot += osborn * (ind)
    tot += (np.sqrt(rho) * F + np.sqrt(1 - rho) * rng.standard_normal(n)) < norm.ppf(ohio)
    return (tot >= 17).mean(), tot


print(f"\nP(Dem control) from the seats vs the control market {pc:.3f} (Polymarket; tournament mid {d.mid[d.label=='Dem U.S. Senate'].iloc[0]:.3f})")
print("rho   Ohio  Alaska  Osborn->D | P(Dem>=17)")
for rho in [0.25, 0.45, 0.65]:
    for oh in [0.30, 0.40, 0.50]:
        for ak in [None, 0.45]:
            for ob in [0.0, 1.0]:
                if (oh != 0.40 or ak is not None) and ob == 1.0:
                    continue
                pr, _ = seat_p(rho, oh, ak, ob)
                print(f"{rho:.2f}  {oh:.2f}  {('mid .643' if ak is None else f'{ak:.2f}'):8s} {ob:.0f}        | {pr:.3f}")
# what reconciles 0.645?
for oh in [0.4, 0.5, 0.6]:
    for ak in [0.643, 0.75, 0.85]:
        pr, _ = seat_p(0.45, oh, ak, 1.0)
        print(f"  reconcile try: Ohio {oh:.2f} Alaska {ak:.2f} Osborn D -> {pr:.3f}")

# ---- instruments ----
print("\nRep-side instruments (22:47 books, 3 levels):")
inst = []
for lab in ['Rep U.S. Senate', 'Dem U.S. Senate'] + [f"Rep {r}" for r in races if 0.2 < pD[r] < 0.8]:
    i = d.index[d.label == lab][0]; r = d.loc[i]
    if lab.startswith('Dem'):                 # short the Dem leg at the bids: collateral 1 - bid, pays if Dem loses
        lv = [(1 - px, q) for px, q in r.bids]; pw = 1 - r.p0; how = 'short at bid'
    else:
        lv = [(px, q) for px, q in r.asks]; pw = r.p0; how = 'buy at ask'
    usd = sum(c * q for c, q in lv)
    inst.append(dict(label=lab, how=how, cost0=lv[0][0], p_pay=pw, edge0=pw / lv[0][0] - 1, depth_sh=sum(q for _, q in lv), depth_usd=usd,
                     levels=" ".join(f"{c:.3f}x{q:.0f}" for c, q in lv), noref=r.noref))
inst = pd.DataFrame(inst)
print(inst.round(3).to_string(index=False))

# ---- sleeves on the (a) core, same worlds as H_board ----
win, F = simulate(d, N, rho=0.45, k=1.0)           # seed 7 = H_board's worlds
pr_seat, seats = seat_p(0.45, 0.40, None, 0.5, Fv=F, seed=11)
demc = seats >= 17
iDs, iRs = d.index[d.label == 'Dem U.S. Senate'][0], d.index[d.label == 'Rep U.S. Senate'][0]
win_seat = win.copy(); win_seat[:, iDs] = demc; win_seat[:, iRs] = ~demc
print(f"\nseat-model worlds: P(Rep control) {1-demc.mean():.3f} (Polymarket {1-pc:.3f}); corr with the race legs is built in")
pa = np.load(SCR + 'H_pos_a.npy')
cost_a = (pa.clip(min=0) * d.best_ask.values + (-pa).clip(min=0) * (1 - d.best_bid.values)).sum()
liq = st['liquidation_value']
# H_board's rivals on the same worlds
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'H_board.py')).read().split('# our plans')[0]
g = {'__file__': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'H_board.py')}
argv = sys.argv; sys.argv = ['H_board.py', str(N)]
with contextlib.redirect_stdout(io.StringIO()):
    exec(src, g)
sys.argv = argv
final_field = g['final']                      # N x rivals (inactive at 100k are below 108k: they never outrank us above 100k)
# the field settled under the seat model too (rivals holding the control market)
S_, C_ = g['S'], g['C']
final_field_seat = np.hstack([C_[None, :] + win_seat @ S_.clip(min=0).T + (1 - win_seat) @ (-S_).clip(min=0).T, final_field[:, -20:]])


def walk(i, usd, side):
    """$ into contract i: side +1 buys YES at the asks, -1 sells YES at the bids (collateral 1 - bid); beyond 3 levels +1c"""
    r = d.loc[i]; lv = r.asks if side > 0 else r.bids
    lv = lv + [[lv[-1][0] + 0.01 * side, 1e12]]
    sh = 0.0; left = usd
    for px, q in lv:
        c = px if side > 0 else 1 - px
        t = min(q, left / c); sh += t; left -= t * c
        if left < 1e-6:
            break
    return side * sh


def sleeve(kind, X):
    pos = np.zeros(len(d))
    if kind == 'RepSen':
        pos[iRs] += walk(iRs, X, +1)
    elif kind == 'DemSenShort+RepSen':        # cheapest first: Dem Senate short at the bids (cost 0.33) then Rep YES at 0.345+
        x1 = min(X, 3500.0); pos[iDs] += walk(iDs, x1, -1); pos[iRs] += walk(iRs, X - x1, +1)
    elif kind == 'RepSenRaces':               # Rep legs of the 6 competitive Senate races, equal $
        legs = [d.index[d.label == f"Rep {r}"][0] for r in races if 0.2 < pD[r] < 0.8]
        for i in legs:
            pos[i] += walk(i, X / len(legs), +1)
    elif kind == 'Mix50':                     # half control, half competitive Rep Senate legs
        pos += sleeve('RepSen', X / 2); pos += sleeve('RepSenRaces', X / 2)
    return pos


def ranks(v, field):
    rk = 1 + (field > v[:, None]).sum(1) + g["inactive_n"] * (v < 100e3)
    return np.median(rk), np.mean(rk <= 10), np.mean(rk <= 50), np.mean(rk <= 100)


rows = []
for kind in ['RepSen', 'DemSenShort+RepSen', 'RepSenRaces', 'Mix50']:
    for X in [0, 10e3, 15e3, 20e3, 22.5e3, 25e3, 30e3, 40e3, 60e3]:
        if kind != 'RepSen' and X == 0:
            continue
        f = max(0.0, 1 - X / cost_a)
        pos = pa * f + sleeve(kind, X)
        cs = liq - cost_a * f - X
        for model, W, fld in [('PM', win, final_field), ('seat', win_seat, final_field_seat)]:
            v = settle(W, pos, cs); s = stats(v); mr, t10, t50, t100 = ranks(v, fld)
            rows.append(dict(sleeve=kind, usd=X, model=model, E=s['E'], p120=s['p120'], p150=s['p150'], p85=s['p85'], p70=s['p70'],
                             rank_med=mr, top10=t10, top50=t50, top100=t100))
R = pd.DataFrame(rows)
pd.set_option('display.width', 200)
for m in ['PM', 'seat']:
    print(f"\n== control settled by: {'Polymarket odds (H base)' if m=='PM' else 'the simulated seat count (Ohio .40, Osborn half)'}")
    x = R[R.model == m].copy()
    x['E'] = (x.E / 1e3).round(1)
    for c in ['p120', 'p150', 'p85', 'p70', 'top10', 'top50', 'top100']:
        x[c] = (100 * x[c]).round(1)
    print(x.drop(columns='model').to_string(index=False))
R.to_csv(SCR + 'I_senate_rows.csv', index=False)
