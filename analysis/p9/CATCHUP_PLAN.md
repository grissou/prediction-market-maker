# The catch-up plan (Package 9, executor, 4 Oct ~01:30 UTC) — one page for the owner

**Target:** P(account >= 150k by 4 Nov) first, P(>= 200k) second, P(<= 85k) < 10%, max drawdown < 20%. **Account 101.0k, rank 174/1039.**

## The one thing that can get there
The leader's +600% is the favourite-longshot tilt: longshot YES / favourite NO bought on 28 Sep went x3.9 (top 10 x5.7) as the tournament's
tilt s rose 1.7% -> 12.6% in 54 h (longshot mids 3.6c -> 9.4c, Polymarket flat). Our +35.9k toward-Polymarket book is the OTHER side of
it (-356 per point; +15k of it from the stale-quote takes). Market making earns hundreds a day. Nothing we run today reaches 150k.
**Switch on, in this order (files in `deploy/package9/`; the odds assume the switch-on at ~09:00 UTC 4 Oct):**
1. **Flatten the short-tilt book** (`stage1_hygiene` then `stage2_flatten`, now): takes measured from the tilted reference, `ref_tilt_max`
   0.20, keep the NO+NO sets (they pay +594 at settlement); `tilt_exit_take` sells the toward-Polymarket positions at the book within 1c of
   tilted fair value, 15k$/h, longshot NO first. Frees cash and cuts the worst case (~79k -> < 40k). Cost ~0.7k. No ruin.
2. **The long-tilt basket** (`stage3_basket`, as soon as `worst_case_loss` < ~40k and `cash_gate_left` > ~20k; the earlier the better):
   longshot YES / favourite NO on ~20+ non-headline races (cheapest route per race, laggards first, no independents), basket $ =
   5 x (liquidation value - floor), floor = max(86k, 0.85 x peak), cap 80k, built over ~4 h as an IOC taker (<= 75% of visible asks at
   first, then <= 25%/h), exit to cash from **18 Oct 12:00 UTC** over 24 h (never held past T-72 h), **kill-switch**: liquidation value
   below the floor or 15% under its peak for 2 min -> the basket is sold down over 2 h and the feature latches off; 36-h test: tilt_s < 0.15
   or falling -> m 1.5. Risk model: basket legs at 40% stress loss; backstop 0.9 as the last resort.
3. **Carry** (`stage4_carry`, after the basket is built): arbitrage back on under the cash rule + YES-set sell-back (~300/day); market-making
   adds stay at factor 0 (the cash belongs to the basket).

## The odds (Monte Carlo `P9_plan_mc.py`, logistic tilt with judgement priors; mixed prior = base .5 / bear .3 / bull .2; the "D prior"
## adds a 20% greater-fool world where the tilt plateaus at 0.12-0.16 and decays)
| Plan | P(>= 150k) | P(>= 200k) | P(<= 85k) | P(DD > 20%) | median | p10 |
|---|---|---|---|---|---|---|
| Do nothing (hold the book) | 0% | 0% | 6% (B) | 10% (B) | ~100k | - |
| **Steps 1+2, m 5 / cap 80k, on at 09:00, built in 4 h** | **29%** (D prior 22%) | 1% | 1.2% | 4.2% | 130k | 99k |
| Steps 1+2+3 (carry 300/day) | **36%** (26%) | 4% | 0.5% | 4.2% | 138k | 108k |
| **Steps 1+2+3 with m 6 / cap 90k (`stage3b`)** | **40%** (29%) | **11%** | 0.7% | 4.5% | 141k | 108k |
| m 5, built over 12 h (if the ask-share caps bind) | 26% | 0.3% | 1.3% | 4.1% | - | - |
| kill at -12% instead of -15% | 23% | 0.1% | 0.8% | 2.9% | - | - |
| kill at -20% | 31% | 1.1% | 1.6% | 5.0% | - | - |
| half size (m 2.5 / cap 40k) | 3% | 0% | 0.2% | 0.7% | - | - |
| switched on 20 h later (evening) / 34 h later (5 Oct morning) | 20% / 7.5% | 0% | 1.4-1.8% | 3.6-4.0% | - | - |
| 5% chance the exit fails and SIG settles at the outcome | 28% | 1% | 1.6% | 5.1% | - | - |
**Plain answer to the sizing question:** the smallest risk that gets P(>= 150k) to ~40% is **m 6 / cap 90k with carry, switched on this
morning**; it keeps P(<= 85k) ~0.7% and P(DD > 20%) ~4.5%, inside the limits. With m 5 / 80k the honest odds are 29-36%. Half size is
worthless (3%). Every 10 h of delay costs ~10 points; by 5 Oct morning 150k is a 7% shot. P(>= 200k) is 1-11%: 200k needs m 6 AND
the bull tilt world. **What the odds rest on:** the tilt's ceiling K (fit: K >= 0.20; P(>= 150k) 7% at K 0.15, 47% at 0.30), exiting
before the close (if SIG settles at the outcome an 80k basket still held is worth ~28k: the dated exit and the T-72 h backstop are
hard-coded), and our own buying moving the tilt ~0.01 (counted against the cushion). Fair play: we buy, hold and sell as a taker;
nothing is placed or timed for a print; no self-crosses (the market maker does not quote basket legs).

## Kill-switch levels (live in the code; `rollback_basket_off` releases the legs without selling)
- Liquidation value < max(86k, 0.85 x peak) OR < 85% of its peak, for 120 s -> basket sold down over 2 h, feature latched off, alert.
- `basket_exit_utc` 18 Oct 12:00 UTC (exit over 24 h), no adds from 11 Oct; never held past 1 Nov 00:00 UTC (T-72 h), per market too.
- 36-h test at ~5 Oct 21:00 UTC (if on at 09:00): tilt_s < 0.15 or its 12-h slope <= 0 -> m cut to 1.5 (latched).
- Buys refused: no cash read < 5 min old, stressed worst case above 0.9 x account, write budget share used, ask-share caps, > 25c a share.
- Owner's manual kill: `rollback_basket_off` (release) or set `basket_exit_utc` to now + 1 h (sell).

## The three morning questions
1. **Resume adding?** Not while the basket is building: the cash is the basket's. After it is at target (`basket.state` tracking) and
   `cash_gate_left` > 10k, `adding_factor_capital_on` 0.95 (+ resume 0.5) as Package 8 recommended; market making at 0 cash is ~150/day.
2. **`arb_enabled` back?** Yes, only with `arb_cash_rule` (stage4): cash gate >= 1.25 x need + 2k, own quotes excluded, size <= 0.8 x the
   thinnest leg, unequal legs completed or reversed the same cycle. Without the rule, no (Maine Senate 158/0 again).
3. **0.8 backstop and 30% risk cap?** Both count a longshot basket at its full cost, which caps it at ~25k (P(>= 150k) 0%) or, at the
   override maximum 0.60, ~56k (31%). Package 9 counts basket legs at a 40% stress loss (`basket_stress_frac`) and uses the floor + kill
   as the real control, with the backstop raised to 0.9 as the last resort (stage3 file); `max_worst_case_frac` stays 0.30 for the rest of
   the book. Ruin at the kill levels: P(<= 85k) 0.8% / 1.2% / 1.6% for kill_dd 0.12 / 0.15 / 0.20, P(DD > 20%) 2.9% / 4.2% / 5.0%.
