# The catch-up plan (Package 9, executor, 4 Oct ~01:30 UTC) — one page for the owner

**Target:** P(account >= 150k by 4 Nov) first, P(>= 200k) second, P(<= 85k) < 10%, max drawdown < 20%. **Account 101.0k, rank 174/1039.**

## The one thing that can get there
The leader's +600% is the favourite-longshot tilt: longshot YES / favourite NO bought on 28 Sep went x3.9 (top 10 x5.7) as the tournament's
tilt s rose 1.7% -> 12.6% in 54 h (longshot mids 3.6c -> 9.4c, Polymarket flat). Our +35.9k toward-Polymarket book is the OTHER side of
it (-356 per point; +15k of it from the stale-quote takes). Market making earns hundreds a day. Nothing we run today reaches 150k.
**Switch on, in this order (files in `deploy/package9/`; the odds assume the switch-on at ~09:00 UTC 4 Oct; stage 1 keeps
`pair_no_unwind_max_cost` 0.02 so the sets keep draining into cash; stage 2 allows 2c vs tilted fv):**
1. **Flatten the short-tilt book** (`stage1_hygiene` then `stage2_flatten`, now): takes measured from the tilted reference, `ref_tilt_max`
   0.20; `tilt_exit_take` sells the toward-Polymarket positions at the book within 1c of
   tilted fair value, 15k$/h, longshot NO first. Frees cash and cuts the worst case (~79k -> < 40k). Cost ~0.7k. No ruin.
2. **The long-tilt basket** (`stage3_basket`, as soon as `worst_case_loss` < ~40k and `cash_gate_left` > ~20k; the earlier the better):
   longshot YES / favourite NO on ~20+ non-headline races (cheapest route per race, laggards first, no independents), basket $ =
   5 x (liquidation value - floor), floor = max(86k, 0.85 x peak), cap 80k, built over ~4 h as an IOC taker (<= 75% of visible asks at
   first, then <= 25%/h), exit to cash from **18 Oct 12:00 UTC** over 24 h (never held past T-72 h), **kill-switch**: liquidation value
   below the floor or 15% under its peak for 2 min -> the basket is sold down over 2 h and the feature latches off; 36-h test: tilt_s < 0.15
   or falling -> m 1.5. Risk model: basket legs at 40% stress loss; backstop 0.9 as the last resort.
3. **Carry** (`stage4_carry`, after the basket is built): arbitrage back on under the cash rule + YES-set sell-back (~300/day); market-making
   adds stay at factor 0 (the cash belongs to the basket).

## The odds, CORRECTED by the dry run (02:30 UTC; `analysis/p9/DRYRUN.md`, `PLAN_MC2.txt`)
The end-to-end dry run of the staged files on the fake exchange seeded with the live books found two things the first Monte Carlo
(`PLAN_MC.txt`, 01:10) did not model: (1) **funding is slow**: the taker exits only reach ~3-8k of cash within 1c of tilted fair value on
today's books (~8-9k in the first hours; the whole toward book is sellable only through all three levels at any price, ~114k); the NO+NO
sets are the cheapest cash (21.9k at 2c a set, `pair_no_unwind_max_cost` 0.02, already live); (2) **the basket is spread-bound**: each $
bought at the ask is valued at the bid and moves the tilt, cutting the cushion ~15c, so m 5 settles near **40k** and m 6 / 90k near **45k**,
not 72-90k. With those in the model (basket built over 24 h, switched on ~09:00 UTC):
| Plan | P(>= 150k) mixed / D prior | P(>= 200k) | P(<= 85k) | P(DD > 20%) | median |
|---|---|---|---|---|---|
| Do nothing (hold the book) | 0% | 0% | 6% (B) | 10% (B) | ~100k |
| stage3 (m 5, settles ~40k) | 0.3% / 0.2% | 0% | 0.7% | 1.9% | 110k |
| stage3b (m 6 / 90k, settles ~45k) | 1.8% / 1.3% | 0% | 0.9% | 2.9% | 112k |
| stage3b + carry 300/day | 8.7% / 6.4% | 0% | 0.0% | 2.9% | 121k |
| **stage3c: floor 80k, m 6, cap 60k** (`stage3c_basket_floor80k`) + carry | **20% / 15%** | 0% | 0.4% | 4.2% | 130k |
| floor 80k, m 5, cap 60k | 12% / 9% | 0% | 1.4% | 4.1% | - |
| floor 75k, m 6, cap 70k | 18% / 13% | 0% | 1.7% | 4.2% | - |
| floor 75k, m 8, cap 80k, ratchet 0.75 | 23% / 17% | 0.1% | 2.7% / **7.7%** | 6.6% / 7.8% | - |
| any basket built over 48 h instead of 24 h | ~0-2% | 0% | - | - | - |
**Plain answer:** no legitimate strategy available to us gets P(>= 150k) to ~40%. The best achievable inside the limits is **~15-20%**
(stage3c: floor 80k, m 6, cap 60k, with carry; P(<= 85k) ~0.4-1.5%, P(DD > 20%) ~4%), and only if ~60k of cash is raised within ~24 h
(set unwinds ~21k at 2c + taker exits at up to 2c vs tilted fv + selling favourites deeper); built over 48 h the odds fall to ~0-2%
because the modelled tilt move is front-loaded (the logistic fit) and the 10-h deployment delay already costs. Pushing the floor to
75k and m to 8 buys ~23% at P(<= 85k) 2.7% (7.7% if the tilt is a greater-fool plateau) and P(DD > 20%) 7%: at the owner's limit.
P(>= 200k) is ~0% in every fundable variant. The first table (29-40%) assumed an 80-90k basket bought in 4 h; it is NOT achievable.

## Kill-switch levels (live in the code; `rollback_basket_off` releases the legs without selling)
- Liquidation value < max(86k, 0.85 x peak) OR < 85% of its peak (stage3c: 80k / 0.80 / -20%), for 120 s -> basket sold down over 2 h (legs
  furthest behind schedule first; thin exit sides can take longer), feature latched off, alert.
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
