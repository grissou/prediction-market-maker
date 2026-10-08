# Package 9, Phase 1 synthesis (executor, 4 Oct 00:10 UTC): can we reach 150k, and how

Inputs: `ideas_A.md` (19), `ideas_B.md` (18), `ideas_C.md` (18), `ideas_D.md` (23) = 78 ideas; every number below is from the
snapshot to 3 Oct 22:47 (`/home/claude/snap03`) or from the Monte Carlo `B_mc.py` / `D_mc.py` (judgement priors stated).

## 1. How +600% is possible here (A-1, B-1, D-16)
The leader bought the CHEAP side of lopsided races on 28 Sep and held: longshot YES with Polymarket under 2c went x3.89, favourite
NO x2.14, the top 10 contracts x5.74, the best single one x11.5 (Dem Montana Senate 0.010 -> 0.115). That is the favourite-longshot
tilt itself: the tournament prices longshots at ~c + (1 - s)(Polymarket - c) with s 1.7% (1 Oct) -> 12.6% (3 Oct 22:00), and the
longshot mids (ref < 5c, n 43) went 0.036 -> 0.094 while Polymarket stayed at 0.025. Rotating into the laggards (A-5, D-8: laggards
+59-91% vs leaders +31-34% per day) gets a concentrated account to +600%. Nothing early-settled; nothing illegitimate was needed.
**Our book is the other side of that trade:** +35.9k of exposure TOWARD Polymarket = short the tilt, -356 to -358 per point it rises;
+15.0k of it came from the stale-quote takes (C-1: 276 take fills since 2 Oct, 100% toward Polymarket, -406 at the final mid).
Market making as it runs earns hundreds a day (C-13: 1-2k/day on 1-2 Oct with capital, +149 on 3 Oct at 0 cash): it cannot reach 150k.

## 2. What CAN reach 150k: a long-tilt basket, sized on a cushion above a floor (A-2, B-2, D-22)
Hold longshot YES / favourite NO (the cheapest route per race, B-4; laggards first, D-8; no independents, no Dem U.S. Senate, D-11),
basket $ = m x (liquidation value - floor), floor = max(86k, 0.85 x peak), capped (60-90k), refreshed hourly, exited to cash by
~18 Oct (T-16 d; backstop T-3 d), stops on LIQUIDATION value not the lagged mark (B-10). Cash is worth the same under both end rules.

| Variant (B's mixed prior: base 0.5 / bear 0.3 / bull 0.2) | P(>= 150k) | P(>= 200k) | P(<= 85k) | P(max DD > 20%) |
|---|---|---|---|---|
| Hold the current book (nothing changes) | 0% | 0% | 6.2% | 9.7% |
| B-2: m 5, floor max(86k, 0.85 peak), cap 80k, exit T-7d, bought at once | 41.9% | 12.4% | 2.0% | 6.5% |
| m 6, cap 90k | 45.9% | 17.8% | 2.5% | 7.2% |
| D-22: full size now, cut to m 1.5 if the 36 h test fails (s < 0.15 or falling) | 43.8% | 12.5% | 1.0% | 4.3% |
| B-2 with D's frictions (own impact, post-crash slippage, exit failure risk) | 40.6% | 11.1% | 2.0% | 7.4% |
| **same, deployed 10 h late and built over 12 h (a realistic morning start)** | **26.7%** | 0.5% | 1.2% | 4.0% |
| built over 24 h / 48 h | 30.0% / 17.8% | - | - | - |
| forward staging (start at m 2, scale up on confirmation, B-17) | ~17% | - | - | - |
| static 40k basket to day 14 | 30.0% | 3.6% | 0.8% | 6.7% |
| maker-SHORT the tilt (sell longshots above the tilt as a maker, D-15) | loses 27% of price/day while the tilt rises: no |

What the number hinges on (in order): (i) the tilt's ceiling K: logistic fit K 0.35 (se 0.18), K < 0.20 rejected (dAIC +3.2 at 0.20,
+13.7 at 0.15) but 0.25-2 fit equally; P(>= 150k) is 7% at K 0.15, 19% at 0.20, 47% at 0.30 (B-1); D's "greater fool" world (K 0.12-0.16,
then decay; bid depth on the cheap sides halved in 30 h, 7.0M -> 3.75M shares, imbalance at its lowest) is given 20% and takes the odds to
~20%; (ii) entry speed: the modelled drift is largest now; every 12 h of delay costs ~7 points (D-1); (iii) the exit: $118k of cheap-side
bids within 1c now, $49k in the worst 2-h block (D-13); under "settled at the outcome" an 80k basket still held at the close is worth
28.5k (D-17): the hard date stop is not optional; (iv) our own buying: lifting 75% of the visible cheap-side asks (~$83k) raises the
measured tilt by ~0.010 (8%): the cushion must be computed net of our own last-24 h impact, the first build capped at 75% of visible
asks, then <= 25% of visible asks per hour, no adds in the last 7 days (D-18, B-14). Fair play: we buy and hold and sell; no order is
ever placed or timed for a print; no self-crosses; no buying our own holdings near the close (B-9).
**Honest answer to the owner's sizing question:** the smallest risk that gets P(>= 150k) above 40% is the FULL cushion-sized basket
(m 5-6, cap 80-90k) bought within ~4 h of the switch; it needs P(<= 85k) ~1-2.5% and P(DD > 20%) ~4-7%, inside the limits. Deployed
in the morning and built over 12 h the honest odds are 25-35%. P(>= 200k) stays in single digits unless entry is that fast. If the
36-h test fails (~5 Oct 12:00) 150k is gone in every world modelled. Everything smaller (half size, forward staging, carry only) gives
17-30%.

## 3. What funds it and what protects it
- Flatten the toward-Polymarket book (A-3, A-11, B-11): longshot NO first (45.2k shares, ~170 cost, removes 19.9k of the 35.9k), then
  favourite YES (23.6k), lines marked below their exit price first; Package 8's tilt exits do this passively; an aggressive version takes
  at the bid inside a cost cap. Holding it costs ~3k in expectation (central guess) and 0.5 points of P(>= 150k) as a "hedge" (D).
- NO+NO sets (16,807 sets, cost 21,323, pay 21,917 at settlement, marked 21,382): keep as cash-like collateral OR split them (C-9: sell
  only the longshot-NO legs: frees 18.4k and leaves +6.8k favourite-NO = long tilt). Set `pair_no_unwind_max_cost` 0 (sell back only
  at ask-sum <= 1.00).
- Risk model (C-10, C-11): both risk measures count a longshot basket at its full cost, so `max_worst_case_frac` 0.30 caps it at ~25k
  (P(>= 150k) 0%); 0.60 (the override's maximum) -> 31%; the basket needs its legs counted at a tilt-STRESS loss (price if s halves:
  -33% of a 10c leg at s 0.12) with the CPPI floor as the real control, the sum-of-maxima backstop at 0.9 as the last resort.
- Kill-switches: liquidation-value floor (basket cut to 0 over <= 2 h when liq < floor), hard exit date, 36-h momentum test, account
  drawdown from peak, own-impact cap, write budget share.
- Config-only, today: `take_tilted_ref` true (+ `take_edge` 0.08): stops the takes rebuilding ~15k/day of short-tilt exposure (C-1);
  `ref_tilt_max` 0.20: the estimator reads 0.142-0.157 and is clipped at 0.11 (A-16); the sets' `pair_no_unwind_max_cost` 0.

## 4. Carry that helps (small, positive, keeps capital liquid)
Set carousel (C-4/5/6): build NO-sets at bid-sum >= 1.02 and dissolve at ask-sum <= 1.01 (277 round trips, median 0.9 h, 1.71c/set),
YES-sets at ask-sum <= 0.985 sold at bid-sum >= 1.00 (131 trips, 2.26c/set): 0.3-0.5k/day after fill rates on ~10k rotating capital,
with the arbitrage cash rule (C-5): `cash_gate_left` >= 1.25 x cost + 2k, size <= 0.8 x thinnest leg, all legs in one IOC batch, own
quotes excluded, threshold 1.02-1.03. Held-to-close sets return ~3.4%/month: last in the capital order (C-12). Spike harvest (D-7: 61
single-name spikes in 55 h, median 1.58x then 27% give-back; take-profit asks earn ~9% of the peak). Laggard selection (D-8).

## 5. Phase 2/3 plan (00:15 -> 07:00)
Screen: the MC distributions above ARE the screen for the basket (live_sim's 3-h world cannot hold a 30-day position; its world has no
cash or set collateral). live_sim screens, live world, 8 x 3 h: `take_tilted_ref` + `take_edge` 0.08; `ref_tilt_max` 0.20; the tilt lead
(A-4, if built). Build (each OFF, own setting, kill-switch): F1 `tilt_basket_*` planner (the bet) + F4 stress risk model; F2 `tilt_exit_take`
(aggressive flattening); F5 set carousel + arb cash rule (if time). Config: `deploy/package9/` stage0 (config-only hygiene: takes, cap,
sets), stage1 (risk model + flatten), stage2 (basket at m 5 / cap 80k), stage2b (m 6 / cap 90k).
