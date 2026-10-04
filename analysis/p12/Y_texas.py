"""Y_texas: the Texas Rep digital on the LIVE book (snap04), rho 0.55, plus the bot's risk caps for each sleeve size.
Read-only. Reuses analysis/p10/H_outcome.py (load/simulate/settle) pointed at snap04 and mm_bot's risk helpers.
Usage: python3 analysis/p12/Y_texas.py [N]   (~30-60 s)"""
import sys, math
sys.path.insert(0, '/home/claude/prediction-market-maker'); sys.path.insert(0, '/home/claude/prediction-market-maker/analysis/p10')
sys.argv = sys.argv[:2]
import numpy as np
import H_outcome as H
H.SNAP = '/home/claude/snap04'
import mm_bot as M

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
RHO, RHOC = 0.55, 0.85
EXTRA_LOCK = 0.37
d, cash, st = H.load()
acct = st['account_value']
lab = {l: i for i, l in enumerate(d.label)}
print(f"contracts {len(d)} held {(d.pos!=0).sum()} cash(acct-marks) {cash:,.0f} acct {acct:,.0f}")


def ev(pos, c):
    return c + (pos.clip(min=0) * d.p0.values + (-pos).clip(min=0) * (1 - d.p0.values)).sum()


# ---- the allocator's offline plan (LIVE_0404 scenario D, $15k turnover, reserve 0): RI Sen short, CT Gov, RI Sen D, RI Gov D
def walk(label, side, usd, strip_own=True):
    """fill up to usd of cash at the book: side 'short' sells YES at bids (locks 1-b), 'buy' buys YES at asks (locks a).
    -> (shares (signed YES), cash spent, avg lock per share, levels used)"""
    r = d.loc[lab[label]]
    lv = r.bids if side == 'short' else r.asks
    lv = sorted(lv, key=lambda x: -float(x[0])) if side == 'short' else sorted(lv, key=lambda x: float(x[0]))
    sh = spent = 0.0
    for px, q in lv:
        px, q = float(px), float(q)
        lock = (1 - px) if side == 'short' else px
        take = min(q, (usd - spent) / lock)
        if take <= 0:
            break
        sh += take; spent += take * lock
    return (-sh if side == 'short' else sh), spent, (spent / sh if sh else float('nan'))


plan = [("Rep Rhode Island Senate", 'short', 6700), ("Dem Connecticut Governor", 'buy', 6200),
        ("Dem Rhode Island Senate", 'buy', 700), ("Dem Rhode Island Governor", 'buy', 500)]
pos0 = d.pos.values.astype(float).copy()
posP, cashP = pos0.copy(), cash
for l, s, u in plan:
    q, spent, avg = walk(l, s, u)
    posP[lab[l]] += q; cashP -= spent   # settle() pays NO shares at settlement, so a short costs its lock 1-b now
    p = d.p0.values[lab[l]]
    print(f"plan {l:28s} {s:5s} {q:+9.0f} sh  cash locked {spent:7.0f}  avg lock {avg:.3f}  p {p:.3f}")
print(f"EV now {ev(pos0, cash):,.0f}  EV with plan {ev(posP, cashP):,.0f}")

# ---- the two routes into the Texas Rep digital
iD, iR = lab['Dem Texas Senate'], lab['Rep Texas Senate']
print("\nTexas: p Dem %.3f Rep %.3f (raw refs %.3f / %.3f)" % (d.p0[iD], d.p0[iR], d.ref_raw[iD], d.ref_raw[iR]))
print(" Dem TX bids:", d.bids[iD][:8]); print(" Rep TX asks:", d.asks[iR][:8])
print(" held: Dem TX %+.0f, Rep TX %+.0f" % (pos0[iD], pos0[iR]))
for side, l in (('short', 'Dem Texas Senate'), ('buy', 'Rep Texas Senate')):
    tot = walk(l, side, 1e9)
    print(f" route {side} {l}: depth {abs(tot[0]):,.0f} sh for ${tot[1]:,.0f} locked, avg cost per $1 of payoff {tot[2]:.3f}")


def sleeve(pos, c, usd):
    """enter usd of cash the cheapest way per unit of payoff: interleave both routes level by level"""
    pos = pos.copy()
    levels = [(1 - float(px), float(q), 'short') for px, q in d.bids[iD]] + [(float(px), float(q), 'buy') for px, q in d.asks[iR]]
    levels.sort()
    levels.append((EXTRA_LOCK, 1e9, 'short'))   # beyond the 3 recorded levels: assume the rest fills at 0.37 per $1 (Dem TX bid 0.63)
    spent = 0.0; parts = {'short': 0.0, 'buy': 0.0}
    for lock, q, s in levels:
        if spent >= usd - 1e-6:
            break
        take = min(q, (usd - spent) / lock)
        spent += take * lock; parts[s] += take
        if s == 'short':
            pos[iD] -= take; c -= take * lock               # a short = NO bought at 1-b (settle() pays the NO at the end)
        else:
            pos[iR] += take; c -= take * lock
    if spent < usd - 1:
        print(f"  !! book exhausted at ${spent:,.0f}")
    return pos, c, parts, spent


# ---- risk caps as the bot computes them (fv = race-scaled reference, all liquid)
groups = d.groupby('race').groups
sign = d.party.map({'Rep': 1, 'Dem': -1}).fillna(0).values
races = {r: [(e, d.label[e], d.p0[e], True) for e in idx] for r, idx in groups.items()}
cfg = M.CFG
cfg.bloc_rho = 0.55
sens = M.bloc_sensitivities(races, cfg)


import sqlite3, pandas as pd
_s = pd.read_sql("select eid,fair_value from snapshots where ts=(select max(ts) from snapshots)",
                 sqlite3.connect(H.SNAP + '/md.sqlite')).set_index('eid')
d['fv'] = d.eid.map(_s.fair_value)


def caps(pos, clean=False):
    """as the bot: fv = this cycle's fair value; a held market without one -> liquid ref (risk_fv); an UNHELD leg
    without one -> last_fv or 0.5 (settlement_risk's fallback; 0.5 assumed: it reproduces status exactly).
    clean=True: every leg at the race-scaled reference."""
    var = worst = 0.0
    for r, idx in groups.items():
        legs = [(pos[e], d.p0[e] if clean else (d.fv[e] if d.fv[e] == d.fv[e] else (d.p0[e] if pos[e] else 0.5))) for e in idx]
        if any(x for x, _ in legs):
            var += M.race_variance(legs); worst += M.worst_case_loss(legs)
    pdel = float((sign * pos).sum())
    sr = 0.15 * abs(pdel) + 3 * math.sqrt(var)
    bloc = sum(pos[e] * sens.get(e, 0.0) for e in range(len(pos)))
    return dict(sr=sr, worst=worst, risk=min(sr, worst), pdel=pdel, bloc=bloc)


c0 = caps(pos0); cc = caps(pos0, clean=True)
print(f"clean (all legs at ref): settlement_risk {cc['sr']:,.0f} worst {cc['worst']:,.0f}  -> the 0.5 fallback on unpriced unheld legs adds {c0['sr']-cc['sr']:,.0f}")
print(f"\nCALIBRATION vs status: settlement_risk {c0['sr']:,.0f} (status {st['settlement_risk']:,.0f}); total_worst_case "
      f"{c0['worst']:,.0f} (status {st['worst_case_loss']:,.0f}); party_delta {c0['pdel']:,.0f} (status {st['party_delta']:,.0f}); "
      f"bloc {c0['bloc']:,.0f} (status {st['bloc_delta']:,.0f})")
print(f"caps: risk <= 0.40 x {acct:,.0f} = {0.40*acct:,.0f}; worst <= 0.85 x = {0.85*acct:,.0f}; |bloc| <= 0.05 x = {0.05*acct:,.0f}")

win, F = H.simulate(d, N, rho=RHO, rho_ctrl=RHOC, seed=11)
print(f"\nrho {RHO}, N {N}.  columns: E  P>=120  P>=150  P<=85  P<=70  P>=133(top50) | cap check")
for base, (pb, cb) in (("(i) book", (pos0, cash)), ("(ii) book+plan", (posP, cashP))):
    for usd in (0, 10000, 15000, 20000, 25000):
        p, c, parts, spent = sleeve(pb, cb, usd)
        v = H.settle(win, p, c)
        k = caps(p)
        flags = []
        if k['risk'] > 0.40 * acct: flags.append(f"risk +{k['risk']-0.40*acct:,.0f}")
        if k['worst'] > 0.85 * acct: flags.append(f"backstop +{k['worst']-0.85*acct:,.0f}")
        if abs(k['bloc']) > 0.05 * acct: flags.append(f"bloc +{abs(k['bloc'])-0.05*acct:,.0f}")
        print(f"{base:15s} ${usd/1e3:4.0f}k (short {parts['short']:6.0f} / long {parts['buy']:6.0f} sh): E {v.mean()/1e3:6.1f}k "
              f"{(v>=120e3).mean():5.1%} {(v>=150e3).mean():5.1%} {(v<=85e3).mean():5.1%} {(v<=70e3).mean():5.1%} "
              f"{(v>=133e3).mean():5.1%} | SR {k['sr']/1e3:5.1f}k worst {k['worst']/1e3:5.1f}k risk {k['risk']/1e3:5.1f}k "
              f"bloc {k['bloc']/1e3:+5.1f}k pdel {k['pdel']/1e3:+5.1f}k  {'BREACH: ' + ', '.join(flags) if flags else 'ok'}")
# what settings let the 20k sleeve through
p, c, _, _ = sleeve(posP, cashP, 20000); k = caps(p)
print(f"\n20k on book+plan needs max_worst_case_frac >= {k['risk']/acct:.3f}, worst_case_backstop_frac >= {k['worst']/acct:.3f}, "
      f"max_bloc_delta_frac >= {abs(k['bloc'])/acct:.3f}")
# carve-out: the sleeve counted at its cost (fixed stress) instead
q = pos0.copy(); kk = caps(q)
p, c, parts, spent = sleeve(pos0, cash, 20000)
pc = p.copy(); pc[iD] = pos0[iD]; pc[iR] = pos0[iR]; kc = caps(pc)
print(f"carve-out (TX sleeve out, + its cost {spent:,.0f} as stress): risk {(kc['risk']+spent)/1e3:.1f}k worst {(kc['worst']+spent)/1e3:.1f}k bloc {kc['bloc']/1e3:+.1f}k")

# largest sleeve each cap allows today (book as is), and the settings each size needs
print("\nmax sleeve under each cap (book as is):")
for name, f, lim in (("risk<=0.40", lambda k: k['risk'], 0.40 * acct), ("worst<=0.85", lambda k: k['worst'], 0.85 * acct),
                     ("|bloc|<=0.05", lambda k: abs(k['bloc']), 0.05 * acct)):
    lo, hi = 0.0, 25000.0
    for _ in range(30):
        m = (lo + hi) / 2
        if f(caps(sleeve(pos0, cash, m)[0])) <= lim: lo = m
        else: hi = m
    print(f"  {name}: ${lo:,.0f}")
print("settings needed (book as is): size -> max_worst_case_frac / worst_case_backstop_frac / max_bloc_delta_frac  [+carve-out: risk w/o sleeve + cost]")
for usd in (10000, 15000, 20000, 25000):
    p, c, parts, spent = sleeve(pos0, cash, usd); k = caps(p)
    pc = p.copy(); pc[iD] = pos0[iD]; pc[iR] = pos0[iR]; kc = caps(pc)
    print(f"  ${usd/1e3:.0f}k: {k['risk']/acct:.3f} / {k['worst']/acct:.3f} / {abs(k['bloc'])/acct:.3f}   carve-out at cost: "
          f"risk {(kc['risk']+spent)/acct:.3f} worst {(kc['worst']+spent)/acct:.3f} bloc {abs(kc['bloc'])/acct:.3f}")
