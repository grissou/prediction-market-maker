"""I_closeout: what the bot's election-night flatten / exit would give up AT THE OUTCOME on today's book (Package 10, explorer I).
Defaults: flatten_hours_before_close 12 (reduce-only), exit_hours_before_close 2 (exit_quote: take the best bid / ask if within
exit_max_slippage 3c of fv), fv = 0.3 x book mid + 0.7 x Polymarket (ref_weight 0.7, value mode: raw Polymarket), ref guard 5c
(raw, value mode) on the reduce-only quotes, skew up to 2c inventory + 2c age. Book = 22:47 positions, books and refs; the tilt at the close
scaled by f (gap x f). Value given up = sum over fills of (p - sale price) for longs (mirror for shorts). Assumes the exit fills at
its price for the whole position (upper bound for the exit; flatten fills are not modelled, only priced).
Usage: python3 analysis/p10/I_closeout.py"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load
d, cash, st = load()
h = d[(d.pos != 0) & ~d.noref].copy()
for f in [0.25, 0.5, 1.0, 2.0]:
    lost_exit = 0.0; usd_exit = 0.0; n = 0; lost_fl = 0.0; n_fl = 0
    for _, r in h.iterrows():
        G = (r.p0 - r.mid) * f                     # signed gap at the close
        mid = r.p0 - G; hs = max(0.0025, (r.best_ask - r.best_bid) / 2)
        fv = 0.3 * mid + 0.7 * r.p0
        if r.pos > 0:                              # sell YES: exit at max(best bid, fv - 3c) if the bid is there
            bid = mid - hs; px = max(bid, fv - 0.03)
            if bid >= fv - 0.03 - 1e-9:            # fills against the bid
                lost_exit += r.pos * (r.p0 - px); usd_exit += r.pos * px; n += 1
            if r.p0 - mid <= 0.05:                 # flatten window: ask at fv - 4c + 1c may rest (ref guard 5c)
                lost_fl += r.pos * max(0, r.p0 - (fv - 0.03)); n_fl += 1
        else:
            ask = mid + hs; px = min(ask, fv + 0.03)
            if ask <= fv + 0.03 + 1e-9:
                lost_exit += -r.pos * (px - r.p0); usd_exit += -r.pos * (1 - px); n += 1
            if mid - r.p0 <= 0.05:
                lost_fl += -r.pos * max(0, (fv + 0.03) - r.p0); n_fl += 1
    print(f"tilt gap at the close x{f:4.2f}: exit (last 2 h) fills in {n:3d} markets, ${usd_exit:8,.0f} sold, value given up "
          f"{lost_exit:+8,.0f} | flatten (12 h) asks/bids that may rest in {n_fl:3d} markets, worst-case value given up {lost_fl:+8,.0f}")
