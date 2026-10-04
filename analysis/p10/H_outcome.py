"""H_outcome: the outcome model (Package 10, explorer H). Read-only on /home/claude/snap03.
Every contract's probability = latest Polymarket reference per eid, scaled to sum to 1 in each race; no reference -> the tournament
mid (flagged). Outcomes: Gaussian copula with one national Dem/Rep factor F (F > 0 = Republican wave):
  state race: Dem wins if sqrt(rho) F + sqrt(1 - rho) e_race < Phi^-1(pDem | no Ind); Ind wins independently with p_Ind.
  U.S. Senate / U.S. House control: the same F with rho_ctrl.
Calibration margin k: p' = logistic(k * logit(p)) (k > 1: longshots resolve less often, favourites more often), re-normalised.
Importable: load(), simulate(d, N, rho, rho_ctrl, k, seed), settle(win, pos, cash).
Usage: python3 analysis/p10/H_outcome.py [N]"""
import sqlite3, json, sys
import numpy as np, pandas as pd
from scipy.stats import norm

SNAP = '/home/claude/snap03'


def load():
    c = sqlite3.connect(SNAP + '/md.sqlite')
    s = pd.read_sql("select eid,label,best_bid,best_ask,reference,ts from snapshots where ts=(select max(ts) from snapshots)", c)
    # latest non-null reference per eid (anywhere in the history)
    r = pd.read_sql("select eid,reference,ts from snapshots where reference is not null and ts>'2026-10-02T22:47'", c)
    r = r.sort_values('ts').groupby('eid').tail(1).set_index('eid')
    r['ts'] = pd.to_datetime(r.ts).map(lambda x: x.timestamp())
    d = s[['eid', 'label', 'best_bid', 'best_ask']].copy()
    d['ref_raw'] = d.eid.map(r.reference)
    d['ref_age_h'] = (pd.to_datetime(s.ts.max()).timestamp() - d.eid.map(r.ts)) / 3600
    b = pd.read_sql("select * from books", c).sort_values('ts').groupby('eid').tail(1).set_index('eid')
    d['bids'] = d.eid.map(lambda e: json.loads(b.loc[e, 'bids']) if e in b.index else [])
    d['asks'] = d.eid.map(lambda e: json.loads(b.loc[e, 'asks']) if e in b.index else [])
    pm = pd.read_sql("select eid,current_price,ts from positions", c).sort_values('ts').groupby('eid').tail(1).set_index('eid')
    d['mark'] = d.eid.map(pm.current_price)
    d['mid'] = (d.best_bid + d.best_ask) / 2
    d['party'] = d.label.str.split().str[0]
    d['race'] = d.label.str.split(n=1).str[1]
    d['legs'] = d.groupby('race').eid.transform('count')
    d['c'] = 1 / d.legs
    d['noref'] = d.ref_raw.isna()
    d['p0'] = d.ref_raw.fillna(d.mid).clip(0.0005, 0.9995)
    d['p0'] = d.p0 / d.groupby('race').p0.transform('sum')
    st = json.load(open(SNAP + '/status.json'))
    pos = st['positions']
    d['pos'] = d.label.map(pos).fillna(0.0)
    d['mark'] = d.mark.fillna(d.mid)
    d = d.reset_index(drop=True)
    mv = (d.pos.clip(lower=0) * d.mark + (-d.pos).clip(lower=0) * (1 - d.mark)).sum()
    cash = st['account_value'] - mv
    return d, cash, st


def calibrate(d, k):
    p = d.p0.values.clip(1e-4, 1 - 1e-4)
    q = 1 / (1 + np.exp(-k * np.log(p / (1 - p))))
    q = pd.Series(q, index=d.index)
    return (q / q.groupby(d.race).transform('sum')).values


def simulate(d, N=20000, rho=0.45, rho_ctrl=0.85, k=1.0, seed=7, F=None):
    """-> win (N x contracts, float32 0/1), F (N,)"""
    rng = np.random.default_rng(seed)
    p = calibrate(d, k)
    if F is None:
        F = rng.standard_normal(N)
    N = len(F)
    win = np.zeros((N, len(d)), dtype=np.float32)
    for race, g in d.groupby('race'):
        idx = g.index.values; parties = list(g.party); pp = p[idx]
        rr = rho_ctrl if race.startswith('U.S.') else rho
        pI = pp[parties.index('Ind')] if 'Ind' in parties else 0.0
        ind = rng.random(N) < pI
        if 'Dem' in parties and 'Rep' in parties:
            pdem = pp[parties.index('Dem')] / (1 - pI)
            z = np.sqrt(rr) * F + np.sqrt(1 - rr) * rng.standard_normal(N)
            dem = (z < norm.ppf(np.clip(pdem, 1e-5, 1 - 1e-5))) & ~ind
            for j, e in zip(parties, idx):
                win[:, e] = dem if j == 'Dem' else ((~dem) & ~ind if j == 'Rep' else ind)
        else:
            pick = np.searchsorted(np.cumsum(pp), rng.random(N))
            for jj, e in enumerate(idx):
                win[:, e] = pick == jj
    return win, F


def settle(win, pos, cash):
    pos = np.asarray(pos, dtype=float)
    yes, no = pos.clip(min=0), (-pos).clip(min=0)
    return cash + win @ yes + (1 - win) @ no


def stats(x):
    return dict(E=x.mean(), sd=x.std(), p120=(x >= 120e3).mean(), p150=(x >= 150e3).mean(), p85=(x <= 85e3).mean(),
                p70=(x <= 70e3).mean(), p5=np.percentile(x, 5), p50=np.median(x), p95=np.percentile(x, 95))


def fmt(s):
    return (f"E {s['E']/1e3:6.1f}k sd {s['sd']/1e3:5.1f}k | P>=120 {s['p120']:5.1%} P>=150 {s['p150']:5.1%} | "
            f"P<=85 {s['p85']:5.1%} P<=70 {s['p70']:5.1%} | p5 {s['p5']/1e3:5.1f}k med {s['p50']/1e3:5.1f}k p95 {s['p95']/1e3:5.1f}k")


if __name__ == '__main__':
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    d, cash, st = load()
    print(f"contracts {len(d)} races {d.race.nunique()} held {int((d.pos != 0).sum())} | cash (acct - marks) {cash:,.0f} | "
          f"account {st['account_value']:,.0f}")
    print("no reference (mid used):", d[d.noref][['label', 'mid', 'pos']].to_string(index=False))
    held = d[d.pos != 0]
    print("held without reference:", int(held.noref.sum()), " held ref older than 6 h:", int((held.ref_age_h > 6).sum()))
    # deterministic EV (no simulation)
    ev = cash + (d.pos.clip(lower=0) * d.p0 + (-d.pos).clip(lower=0) * (1 - d.p0)).sum()
    mv = st['account_value']
    print(f"EV at the outcome (Polymarket, normalised) {ev:,.0f}  vs marks {mv:,.0f}  (+{ev-mv:,.0f})")
    raw = cash + (d.pos.clip(lower=0) * d.ref_raw.fillna(d.mid) + (-d.pos).clip(lower=0) * (1 - d.ref_raw.fillna(d.mid))).sum()
    print(f"EV with raw (unnormalised) refs {raw:,.0f}")
    # party delta as the bot defines it: Rep YES shares - Dem YES shares
    sign = d.party.map({'Rep': 1, 'Dem': -1}).fillna(0)
    print(f"party delta (Rep - Dem YES shares) {float((sign*d.pos).sum()):,.0f}  (status {st['party_delta']})")
    # Senate seat-count consistency check for rho: Dems hold 34 seats not up; need 17 of 35 up (Ohio special not listed: pD 0.40)
    sen = d[d.race.str.endswith('Senate') & ~d.race.str.startswith('U.S.')]
    pctrl = float(d.loc[d.label == 'Dem U.S. Senate', 'p0'].iloc[0])
    print(f"Senate races listed {sen.race.nunique()}, E[Dem seats of them] {sen[sen.party=='Dem'].p0.sum():.2f}; Polymarket Dem control {pctrl:.3f}")
    rng = np.random.default_rng(3)
    for rho in [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75]:
        win, F = simulate(d, 20000, rho=rho, seed=11)
        seats = win[:, sen[sen.party == 'Dem'].index.values].sum(1)
        oh = (np.sqrt(rho) * F + np.sqrt(1 - rho) * rng.standard_normal(len(F))) < norm.ppf(0.40)
        for need_ind in [0, 0.5]:
            ne = sen[sen.party == 'Ind'].index.values
            tot = seats + oh + need_ind * win[:, ne].sum(1)
            pass
        tot = seats + oh
        totI = tot + win[:, d.index[d.label == 'Ind Nebraska Senate'][0]]
        print(f"  rho {rho:.2f}: seats sd {tot.std():.2f}  P(Dem >= 17 of 35) {np.mean(tot>=17):.3f}  (Osborn counted D {np.mean(totI>=17):.3f})")
    print()
    base = None
    for rho in [0.0, 0.25, 0.45, 0.65]:
        for k in [1.0, 1.1, 1.2, 0.9]:
            if rho != 0.45 and k != 1.0:
                continue
            win, F = simulate(d, N, rho=rho, k=k)
            v = settle(win, d.pos.values, cash)
            s = stats(v)
            print(f"current book rho {rho:.2f} k {k:.1f}: {fmt(s)}")
            if rho == 0.45 and k == 1.0:
                base = (win, F, v)
    win, F, v = base
    # the party factor's role
    for q in [0.05, 0.25, 0.5, 0.75, 0.95]:
        f = np.quantile(F, q); m = np.abs(F - f) < 0.1
        print(f"  F quantile {q:.2f} (F {f:+.2f}, + = Rep wave): E[final | F] {v[m].mean()/1e3:6.1f}k")
    print(f"  corr(final, F) {np.corrcoef(v, F)[0,1]:+.2f}; regression slope {np.polyfit(F, v, 1)[0]:,.0f} per 1 sd of F; "
          f"share of variance from F {np.corrcoef(v, F)[0,1]**2:.0%}")
    # contributions by position
    held = d[d.pos != 0].copy()
    yes = held.pos.clip(lower=0); no = (-held.pos).clip(lower=0)
    held['ev_gain'] = yes * (held.p0 - held.mark) + no * (held.mark - held.p0)
    held['max_loss'] = yes * held.mark + no * (1 - held.mark)
    print(held.sort_values('ev_gain', ascending=False)[['label', 'pos', 'mark', 'mid', 'p0', 'noref', 'ev_gain', 'max_loss']].head(15).round(3).to_string())
    print(held.sort_values('ev_gain')[['label', 'pos', 'mark', 'mid', 'p0', 'noref', 'ev_gain']].head(6).round(3).to_string())
    np.save('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/H_v_book.npy', v)
