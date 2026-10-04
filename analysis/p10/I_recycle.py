"""I_recycle: option 1 (recycling on convergence) under five tilt paths (Package 10, explorer I). Read-only.
World: contract j's tournament mid = p_j - D_j(t), D_j(t) = D_j0 * s_j(t) / S0 (D_j0 = p - mid at 22:47, S0 = the tilt then 0.11),
s_j(t) = s(t) * exp(0.5 eta_j(t)), eta AR(1) daily phi 0.7 (H's dispersion). Books keep their 22:47 shape around the moving mid (3 levels,
each level's offset from the mid and its size fixed; the book refills daily). Day 0 = 4 Oct (s 0.14), day 31 = the close.
Arm HOLD: the book is held. Arm RECYCLE: each day, any value-side holding whose exit price (bid for YES / ask for NO) is within CONV (2c) of
p is sold at that touch; the cash buys the day's best edge levels (edge >= MIN_E, depth-limited, <= CAP $ per contract incl. holdings),
and those can themselves be recycled later (compounding). Value at the outcome = cash + sum shares x p (expectation; the outcome variance
is identical in both arms to first order). Books: the current book (status 22:47) and H's plan (a) (H_pos_a.npy).
Scenarios for s(t): (i) growth (B_mc: 0.14 -> 0.29 at day 7 -> 0.215 at day 30, lognormal sd to 0.55), (ii) plateau 0.14 (sd 0.15),
(iii) linear convergence to 0.03 by 2 Nov, (iv) step: flat 0.14 to 28 Oct, then 0.03 by 1 Nov, (v) early convergence to 0.03 by
14 Oct and back to 0.14 by 4 Nov (the owner's best case).
Usage: python3 analysis/p10/I_recycle.py [paths]"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load

P = int(sys.argv[1]) if len(sys.argv) > 1 else 300
CONV, MIN_E, CAP, S0, DAYS = 0.02, 0.03, 10000.0, 0.11, 31
DISP = float(os.environ.get("I_DISP", 0.5))
SCR = '/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/'
d, cash0, st = load()
ok = (~d.noref) & d.bids.map(len).gt(0) & d.asks.map(len).gt(0)
p = d.p0.values; mid0 = d.mid.values; D0 = p - mid0
vs = np.sign(D0); vs[vs == 0] = 1                     # value side: +1 buy YES, -1 hold NO
# level offsets from the mid, value side (buy side asks if vs>0, else bids), and exit touch offset
lv_off = np.full((len(d), 3), np.nan); lv_q = np.zeros((len(d), 3)); ex_off = np.zeros(len(d))
for i, r in d.iterrows():
    if not ok[i]:
        continue
    book = r.asks if vs[i] > 0 else r.bids
    for k, (px, q) in enumerate(book[:3]):
        lv_off[i, k] = px - mid0[i]; lv_q[i, k] = q
    ex_off[i] = (r.best_bid if vs[i] > 0 else r.best_ask) - mid0[i]
okm = ok.values


def paths(kind, rng):
    t = np.arange(DAYS + 1)
    if kind == 'i':
        med = np.interp(t, [0, 7, 14, 30, 31], [0.14, 0.29, 0.287, 0.215, 0.215]); sg = np.interp(t, [0, 7, 30], [0, 0.36, 0.55])
    elif kind == 'ii':
        med = np.full(DAYS + 1, 0.14); sg = np.interp(t, [0, 31], [0, 0.15])
    elif kind == 'iii':
        med = np.interp(t, [0, 29, 31], [0.14, 0.03, 0.03]); sg = np.interp(t, [0, 31], [0, 0.15])
    elif kind == 'iv':
        med = np.interp(t, [0, 24, 28, 31], [0.14, 0.14, 0.03, 0.03]); sg = np.interp(t, [0, 31], [0, 0.15])
    else:
        med = np.interp(t, [0, 10, 31], [0.14, 0.03, 0.14]); sg = np.interp(t, [0, 31], [0, 0.15])
    return med[None, :] * np.exp(sg[None, :] * rng.standard_normal((P, 1)))


def run(pos0, cash, kind, seed=1):
    rng = np.random.default_rng(seed)
    S = paths(kind, rng)
    hold_ev = cash + (np.where(pos0 > 0, pos0 * p, -pos0 * (1 - p))).sum()
    gains = np.zeros(P); recyc = np.zeros(P); gen2 = np.zeros(P)
    vpos0 = np.where(okm & (np.sign(pos0) == vs), np.abs(pos0), 0.0)          # value-side shares (toward p)
    psid = np.where(vs > 0, p, 1 - p)                                          # value per value-side share
    for k in range(P):
        eta = rng.standard_normal(len(d)); h = vpos0.copy(); cash_ = 0.0; born = np.zeros(len(d))
        sold = np.zeros(len(d), bool)
        for t in range(1, DAYS + 1):
            eta = 0.7 * eta + np.sqrt(1 - 0.49) * rng.standard_normal(len(d))
            sj = np.minimum(S[k, t] * np.exp(DISP * eta), 0.6)
            mid = np.clip(p - D0 * sj / S0, 0.003, 0.997)
            exit_px = mid + ex_off                                             # YES price at the exit touch
            exit_val = np.where(vs > 0, exit_px, 1 - exit_px)                  # $ per value-side share received on exit
            conv = (h > 0) & (np.abs(psid - exit_val) <= CONV) & okm
            if t < DAYS and conv.any():
                got = (h[conv] * exit_val[conv]).sum()
                gen2[k] += (h[conv] * born[conv]).sum() > 0
                recyc[k] += got; cash_ += got
                gains[k] += got - (h[conv] * psid[conv]).sum()                 # EV given up by selling (negative or ~0)
                h[conv] = 0; sold |= conv
            if cash_ > 1 and t < DAYS:
                # today's buy levels on the value side: price = mid + offset; cost per value-side share
                px = mid[:, None] + lv_off
                cost = np.clip(np.where(vs[:, None] > 0, px, 1 - px), 0.005, 1)
                edge = psid[:, None] / cost - 1
                edge[~okm] = np.nan
                cands = [(edge[j, l], j, l) for j, l in zip(*np.nonzero(edge >= MIN_E))]
                cands.sort(reverse=True)
                for e, j, l in cands:
                    room = CAP - h[j] * cost[j, l]
                    if room <= 1:
                        continue
                    usd = min(cash_, lv_q[j, l] * cost[j, l], room)
                    h[j] += usd / cost[j, l]; born[j] = 1; cash_ -= usd
                    gains[k] += usd * e
                    if cash_ <= 1:
                        break
    return hold_ev, gains, recyc


if __name__ == '__main__':
    books = [('current book', d.pos.values, cash0)]
    if os.path.exists(SCR + 'H_pos_a.npy'):
        pa = np.load(SCR + 'H_pos_a.npy')
        cost_a = (pa.clip(min=0) * d.best_ask.values + (-pa).clip(min=0) * (1 - d.best_bid.values)).sum()
        books.append(("H plan (a)", pa, st['liquidation_value'] - cost_a))
    W = {'i': 0.40, 'ii': 0.25, 'iii': 0.12, 'iv': 0.18, 'v': 0.05}
    names = {'i': '(i) growth (B_mc)', 'ii': '(ii) plateau 0.14', 'iii': '(iii) linear -> 0.03 by 2 Nov',
             'iv': '(iv) step: last week', 'v': '(v) early: 0.03 by 14 Oct, back to 0.14'}
    for bname, pos, cash in books:
        print(f"== {bname}")
        tot = 0
        for kd in W:
            hev, g, rc = run(pos, cash, kd)
            tot += W[kd] * g.mean()
            print(f"  {names[kd]:42s} w {W[kd]:.2f}: recycled $ mean {rc.mean():8,.0f} (p90 {np.percentile(rc,90):8,.0f}) | "
                  f"gain vs hold mean {g.mean():+7,.0f} p10 {np.percentile(g,10):+7,.0f} p90 {np.percentile(g,90):+7,.0f}  "
                  f"(= {g.mean()/hev:+.2%} of EV {hev/1e3:.1f}k)")
        print(f"  probability-weighted gain {tot:+,.0f}")
