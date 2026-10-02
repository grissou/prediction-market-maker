# PLAN_POLY_BIAS: the Polymarket-convergence problem (for Finisher 2b, Opus executor)

Planner: Finisher 2a, 2 Oct 2026; v2 by Finisher 2a-v2 (second planner, same day): sections 2-5 replaced by a
two-tier plan, corrections marked v2. Branch `claude/finisher-plan-v2`.
Original header: Planner Finisher 2a. Branch `claude/finisher-plan` (on Package 4, fd28b0a). No code changed here:
only `analysis/poly_bias/` (phase0.py, RESULTS.md) and this file.

## 0. Verdict: PARTLY CONFIRMED (snapshot 2 Oct 08:14 UTC; numbers in analysis/poly_bias/RESULTS.md)

| Claim | Data | Result |
|---|---|---|
| Positions lean toward Polymarket | 76% of open capital entered toward Polymarket, 11% against, 12% no gap; by the gap now 77% vs 3% | confirmed |
| Cannot exit those | exited so far: 65% of toward-entered capital vs 85% of against-entered; open capital median age 8.8 h, 69% older than 6 h; closed lots median 14 min | confirmed |
| Size follows the gap | mean capital 1,194 per position at 5c+ gaps vs 175-434 below; 2c+ entries = 52% of capital; 5 of the top 8 sit at ~2,000 = `kelly_max_market_frac` | confirmed |
| No convergence | gaps on the big positions widened since entry (Rep U.S. House -3.2c to -5.5c, NH Governor 6.7c to 7.2c) | confirmed so far (16 h) |
| Gain is mostly marks | +1,509 = realised +154, unrealised -42, spread capture and other +1,398; liquidation haircut 428 (top of book) | NOT confirmed |
| 90% of capital in positions | 51% at 08:14 UTC (after a reduce-only night); worst case 32% | later than the data: unchecked |

Reading: the bot does make markets (exits/entries 0.76, fills netted per 2.6 min), but what stays on the book is the
Polymarket bet. The open book is a +1,839 claim on the gaps closing; it is not a marked-up gain yet.
Also: 14% of position capital is the U.S. Senate pair (long both legs, 7,335 each), a pair-unwind matter, not bias.

v2 corrections to this section:
- "Polymarket-convergence bet" is too generous a name. 60% of the gap variance is ONE cross-sectional factor, a
  favourite-longshot tilt of 5.1% that doubled in 16 h and does not close (section 2). The book holds +15.1k of
  exposure to it (-151 of marks per point). The U.S. Senate pair: `pair_unwind` exists and is ON but its threshold
  (bids sum >= 1.005) is never met (T2.5).
- "60% of 1-5c-gap markets have a position": the rest are the tails; 63 of 76 no-position markets sit at
  Polymarket < 5% or > 95%, and the bot quotes neither side in 49.

## 1. Yardstick audit (read, not run)

| Metric | Where | How | Polymarket-marked? |
|---|---|---|---|
| pnl | strategy_sim.metrics ~495 | cash + inv x Polymarket end price | yes |
| pnl_mid | ~488-496 | cash + inv x mid of the OTHER traders' best bid/ask | no, but see the world note; computed, NOT in live_sim KEYS |
| pnl_lag | ~497-502 | cash + inv x VWAP of all prints in the last 30 min | partly (prints mix rivals and humans) |
| markout15_c, pick_cost | ~494, ~515 | fill price vs the Polymarket path 15 min later | yes |
| max_dd | ~475-481 | peak-to-trough of cash + inv x Polymarket, each minute | yes |
| worst, cap_frac | ~476 | settlement capital at Polymarket prices | yes (small effect) |
| live_sim start | live_sim ~135 | `m.cash = -m.inv * m.p0`: starting inventory booked at Polymarket | yes |
| live_sim equity, cap_end, freed, wc_* | live_sim ~240-250, ~376 | positions valued at Polymarket | yes |

- Decisions resting on the Polymarket mark: Round 2 (ranked on pnl_lag, partly), Round 3, Round 3b and Round 4,
  all of them. Suspect in particular: fast_unload -165+-110, reduce_join_best -48+-81, turnover_control +49+-62,
  capital_ceiling_adding_size_factor 0.5 vs 0.25 +262+-100.
- THE WORLD IS BIASED TOO, not only the mark. Sim rivals price from Polymarket (`rival_step` ~276:
  `f = seen + offset`, live_sim LIFT 0.3c) and take anything through that. Only the "humans" sit at the consensus
  (Polymarket + gap). So in the sim the bots agree with Polymarket and a liquidation mark at the rival book is
  nearly a Polymarket mark. Live says otherwise: 178 of 229 markets show a gap over 1c with rival bots present.
  A liquidation mark alone will not fix the yardstick; the rivals need an anchor knob (Phase 1).
- Where the designs live: live_sim calls the real `Bot.decide` (bot_strategy ~410-420, passing `book_fv=bfv`).
  A, B and the quoting half of C belong in `Bot.decide` (~4223-4330) / `compute_quote` (~1934) / `kelly_position`
  (~1789): live_sim picks them up with no mirror. The TAKING half of C is not in decide (takes run from the main
  loop, like `take_stale_quotes` ~5589): it needs a mirror in live_sim (next to `Sim.take` ~331 / `arbitrage` ~292)
  behind the same flag, with a pinning test. Nothing here touches plan_changes.

v2 corrections to this section:
- The sim world is wrong in a second way: its favourite-longshot bias is +1.1c / -0.6c and static; live is
  -3.2c / +2.5c at the tails and growing. `_rival_anchor` needs `_world_tilt` beside it (section 3).
- The news regime assumes a Polymarket move is fully informative for tournament prices (informed takers trade it,
  markout15_c scores it). Live pass-through is 0.14-0.29. Judge pick-offs on `mk15_mid`.
- The blend is in the main loop (~3249), not in `Bot.decide`; strategy_sim `bot_strategy` ~343-346 has its own
  blend line. Anything that changes the blend (T2.1) needs a mirror there. A per-market gap prototype already
  exists in strategy_sim (`_bias_hl`, ~347 / ~809).

## 2. v2 finding: the gap is one factor, a growing favourite-longshot TILT, not 178 separate disagreements

Evidence: `analysis/poly_bias/tier2.py` -> `TIER2_RESULTS.txt` (same snapshot, 229 markets, 290 live snapshots, 16.1 h).

| Measurement | Number | Meaning |
|---|---|---|
| Gap (Polymarket - tournament mid) by Polymarket price | <5%: -3.2c; 5-15: -2.3c; 15-35: -1.6c; 35-65: -0.7c; 65-85: +0.9c; 85-95: +1.9c; >95%: +2.5c (monotone, n = 23-47 each) | tournament mid ~ 0.5 + (1 - s)(Polymarket - 0.5): longshots dear, favourites cheap |
| Tilt s per quarter of the window (slope of gap on ref-0.5) | 2.4% -> 3.4% -> 4.7% -> 5.1%; R2 0.11 -> 0.45 -> 0.58 -> 0.60 | one number explains 60% of all gap variance and it DOUBLED in 16 h |
| Which part of the gap does the mid close? (2-variable regression) | tilt part: -0.03 (30 min), -0.09 (2 h), -0.29 (4 h), -0.49 (8 h): it widens. Residual part: +0.38, +0.30, +0.23, +0.30 | only the residual (gap minus tilt) converges, and only ~30% of it |
| Pass-through of a Polymarket move >= 1c into the tournament mid | 10% same snapshot, 19-26% after 5-20 min (n = 105); tails 14%, middle 29%; in the tails Polymarket itself gives back 35% | the tournament weights Polymarket news at ~0.2-0.3, not 0.7 |
| Book exposure to the tilt, sum pos x (ref - 0.5) | +15,087 (gross 22,312): each +1 point of tilt marks the book -151; 5.1% -> 10% = -739; tilt -> 0 = +770 | the "convergence bet" is a single-factor position, measurable and limitable |
| Per-market persistence | only 47 of 229 markets hold a same-sign gap >= 1c in >= 90% of snapshots; gap(end) on gap(start) slope 0.46; mean abs gap 1.41c -> 2.04c | per-market gap memory is weak; the cross-section is what persists |
| Who is best | we are the best bid in 16 markets and the best ask in 17 of 229; median spread 1.0c where we hold, 1.8c where we do not | the bot mostly rests behind the best quote |
| No-position markets | 76; 63 of them at Polymarket < 5% or > 95%; we quote neither side in 49 | the "other 40%" are the tails, where the tilt is largest |
| Locked sets (bid sum > 1 or ask sum < 1, top of book, third-candidate races dropped) | 2,546 of 31,321 race-snapshots (8.1%), median 0.5c (one tick), median run 1 snapshot, 108 races | real, one tick, gone within ~3 min; depth unknown |

Reading. The tilt is the tournament's price of locked capital: shorting a 5c longshot or buying a 95c favourite locks
~0.95 per share to earn ~3c, and capital-bound participants will not do it. The bot, anchored 70% on Polymarket, is the
one participant lending capital at that rate (median 2.0% return on capital to settlement, top decile 4%, section 7 of
the output) while the rate rises, so its marks bleed (-151 per point). The tilt only pays if positions settle at the
outcome; markets close 4 Nov 00:00 UTC, BEFORE returns, and SIG's end valuation is unknown (START_HERE ~56-58).
So the right fair value now is Polymarket pushed through the measured tilt; what is left over is a ~1c residual that
does revert, which is a market maker's signal rather than a convergence bet.

## 3. Tier 1: baseline (v1's Phase 1 yardstick + designs A-D), with v2 changes

v2 changes to Tier 1:
- World knob: `_rival_anchor` alone is not enough. The sim's favourite-longshot bias is +1.1c / -0.6c and static
  (strategy_sim `new_market` ~171: `fl`); live is -3.2c / +2.5c at the tails and growing. Add live_sim special keys
  `"_world_tilt"` (default 0 = today; test 0.05) and `"_world_tilt_growth"` (per hour, default 0; test 0.002):
  consensus = 0.5 + (1 - s_t)(p - 0.5) + the existing per-market bias, s_t = tilt + growth x hours; rivals at
  `_rival_anchor` 1 price from that consensus. THE JUDGING WORLD is `_rival_anchor` 1 + `_world_tilt` 0.05 +
  growth 0.002 ("tilt world"). Pin: both keys 0 = identical output to before.
- With T2.1 on, design A should be nearly redundant (the reducing side is then already priced near the book).
  Screen A alone and T2.1 alone; run A on top of T2.1 only if T2.1's exit_ratio is not above A's.
- B: `kelly_position` takes p = fair value, so T2.1 shrinks the perceived edge by itself. Keep B's
  `kelly_max_market_frac` 0.01 and headline 0.05; `kelly_edge_cap` becomes optional (screen it last).
- C stays the backstop; D (`ref_weight` 0.5 / 0.35) is now screened WITH T2.1 on (see T2.2), not only alone.
- `fl_bias_enabled` (OFF, `fl_side` ~1883) is not a fix: as read, it adds edge to BUYING longshots and SELLING
  favourites, the opposite sides from where the capital is stuck (short longshots, long favourites). Leave it off.

### 3.1 Phase 1: fix the yardstick first (v1 text, unchanged except the world above)
1. World knob, live_sim special key `"_rival_anchor"` (default 0 = today): rivals' fair = Polymarket +
   anchor x m.bias. Run everything at 0 and at 1. 1 is the world the live data describes.
2. New fields, in live_sim KEYS so every variant line prints the delta +- SE:
   - `pnl_mid`: start AND end marked at the other traders' mid (store inv0, mid0; do not use p0 for the start).
   - `pnl_liq`: start and end at liquidation (longs at the best other bid, shorts at the best other ask;
     no quote on that side -> the consensus path value minus 2c).
   - `mk15_mid`: markout of each fill against the consensus path `c` 15 min later (next to markout15_c).
   - `exit_ratio`: shares of our fills that reduced |inventory| / shares that added (replay fills per market from inv0).
   - `hold_med`: median holding time of closed lots, hours (FIFO, starting lots aged from `_start` ages).
   - `pick_cost`, `wc_end`: already computed, add to KEYS. Keep pnl, pnl_lag, cap_end, freed, writes_pm.
3. Pinning tests (tests/test_live_sim_marks.py):
   - same seed, `_rival_anchor` 0: pnl, cap_end, writes_pm, shares identical to before the change;
   - no-trade run: pnl_mid = sum inv0 x (mid_T - mid0), pnl_liq <= pnl_mid;
   - one long bought 3c under Polymarket with the gap held: pnl > 0, pnl_mid ~ 0, pnl_liq < 0;
   - exit_ratio = 1 and hold_med exact on a scripted buy-then-sell.
4. Re-score, 8 seeds x 3 h quiet, both anchors, Package 4 BASE_JSON with `_start_cap` 0.90 (6 CPU-min per
   config per anchor at 15 CPU-s per seed-hour): base, `fast_unload_enabled`, `reduce_join_best`,
   `turnover_control_enabled`, `capital_ceiling_adding_size_factor` 0.25, `ref_weight` 0.5, `ref_weight` 0.3.
   One command per anchor: 7 runs = 42 CPU-min; both anchors 84. If over time, run anchor 1 in full and at
   anchor 0 only base (the old numbers stand in). Write the table into SIM_NOTES.md "Round 5".

v2 trim of item 4 (budget): run the re-score ONLY in the tilt world, 8 x 3 quiet: base, `fast_unload_enabled`,
`reduce_join_best`, `turnover_control_enabled`, `capital_ceiling_adding_size_factor` 0.25 = 5 runs = 30 CPU-min.
The `ref_weight` rows move to T2.2. Old-world numbers stand in from SIM_NOTES.

### 3.2 Designs A-D (v1 text)

| | Setting (default) | Where | What | Specific risk |
|---|---|---|---|---|
| A | `reduce_from_book` (False), `reduce_from_book_pause_s` (120) | `Bot.decide` ~4290-4330 passes `reduce_fv=book_fv`; `compute_quote` band ~1992-2001 | The reducing side only (long -> ask, short -> bid, size <= position) measures min_edge from book_fv, never beyond the blend toward Polymarket; the adding side keeps the blend. Off for 120 s after a Polymarket move >= ref_jump_threshold in that market (reuse the jump test behind `lad_pull_until` ~4681), and off in ref_only markets and when book_fv is None | Self-cross: with a 5c gap the reducing ask (book+1c) is BELOW our own adding bid (blend-1c). Rule: while it applies, cap the adding side at the reducing price minus 2 x min_edge. Without it we sell low and rebuy high (and it would look like wash trading). Also sells to informed takers when Polymarket is right: watch pick_cost, news regime |
| B | `kelly_edge_cap` (0 = off; try 0.015, 0.01), plus existing `kelly_max_market_frac` 0.02 -> 0.01 and `headline_position_frac` 0.10 -> 0.03-0.05 | `kelly_position` ~1801-1806: `edge = min(edge, cap)`; headline limit `decide` ~4287 | Size on the spread, not the gap | The edge cap alone does not bind on favourites: at cost 0.90 quarter Kelly on 1.5c is 3.75% > the 2% cap. So B = edge cap AND kelly_max_market_frac. Fewer shares where Polymarket is right; headline cap below a held position must only stop adding (pin it) |
| C | `hold_target_hours` (0 = off; try 4), `hold_unload_budget_frac` (0.02 of account per hour) | quote half: `decide`, reuse `unload_side`/`unload_edge` (~4322) with age from `age_hours` ~3976; take half: new `take_aged` beside `take_stale_quotes` ~5589 + live_sim mirror | Lots older than T: reducing side joins the tournament best (floor book_fv). Older than 2T: take, inside the hourly budget | Crosses the spread at the worst moment in a thin book (cap per order, never through book_fv by more than 1c); takes cost writes; a budget reset bug dumps everything: pin the budget |
| D | no code: `ref_weight` 0.5 / 0.3 / 0, `ref_jump_threshold` 0.03 -> 0.015 | override | Blunt comparator | Fair value follows a noisy, thin book: more re-prices (writes), picked off on real Polymarket moves; both sides move, so it also stops the profitable adds |

Expected winner: A + B together. A opens the exit without giving up the Polymarket lean on entries; B removes the
"biggest where the tournament disagrees most" sizing and cuts the worst case. C is the backstop for what A does not
clear (expect it to cost P&L at any honest mark). D should lose in news runs (pick_cost) and on writes; it is the
comparator, not the candidate. Existing age skew (`skew_age_*`, max 2c from the blend) cannot bridge a 5c gap: that
is why it has not cleared these lots.

Write budget (28/min): A adds no orders but its quote follows the noisier book_fv: expect more re-prices, test with
and without `no_chase_enabled`. B costs nothing (fewer adds). C: 1 write per take; cap takes at 2/min. D at
ref_weight <= 0.3: every quote follows the book; expect the largest writes_pm rise. Any design with writes_pm
above 28 or deferred_h up by more than 20% fails.


### 3.3 Ops (v1 text, plus two fields)
- status.json and the 2-hourly summary: `liquidation_value` (account value minus the haircut: longs at best bid,
  shorts at best ask), `realised_pnl` and `unrealised_pnl` (FIFO lots from `update_lots`), `toward_ref_capital_frac`,
  `capital_over_6h_frac`, `exit_ratio_24h`. Summary line: "Liquidation 101.1k (account 101.5k), realised +154,
  76% of capital toward Polymarket, 69% older than 6 h".
- Recorder: add liquidation_value to the `account` table (new nullable column; old rows stay).
- analysis/poly_bias/phase0.py runs on any new ops snapshot: rerun it on the next one (the afternoon state).

- v2: add `tilt_s` (the live estimate, T2.1's estimator runs even with the flag OFF, read-only) and
  `tilt_exposure` (sum pos x (ref - 0.5); x 0.01 = mark P&L per point of tilt) to status.json and the summary line:
  "tilt 5.1%, exposure +15.1k (-151 per point)".

## 4. Tier 2: ambitious designs, ranked by expected value per CPU-hour and per line

All OFF by default, all in OVERRIDABLE. "Prior" = my estimate of d pnl_liq per day on the 100k account in the tilt
world, with a range; none is simulated. Go/no-go tests are 8 x 3 quiet in the tilt world (6 CPU-min) unless stated.

### T2.1 Tilt-corrected reference (rank 1: build first)
- Mechanism: estimate the tilt s each cycle from the whole cross-section and quote around
  r' = c + (1 - s)(r - c) instead of the raw Polymarket price r (c = 1 / legs in the race, 0.5 for two legs).
  The systematic gap leaves fair value; the residual gap stays as the signal, entry and exit both.
- Evidence: section 2 rows 1-3 and 5. s = 5.1%, R2 0.60, never closes; residual closes ~30%.
- Estimator: over markets that are liquid, not ref_only, not headline, with book_fv and r present:
  s_raw = sum((r - c)(r - book_fv)) / sum((r - c)^2), gaps winsorised at +-8c. Guards: need >= `ref_tilt_min_markets`
  (50) else hold the last value; EMA with half-life `ref_tilt_halflife_min` (30); clip to [0, `ref_tilt_max`] (0.12);
  skip the cycle while any reference jump guard is active; persist s in the state file so a restart does not start at 0.
- Code: settings `ref_tilt_enabled` (False), the three above. New pure helper `tilted_ref(r, s, legs)` and a small
  `TiltEstimator` near `fair_value`; applied ONLY in the blend loop (mm_bot ~3249-3252: `r` -> `tilted_ref(r, ...)`).
  The guard, jump test, ref_only pricing and risk_fv keep the raw r (decide separately later). About 50 lines + tests.
  NOT in `Bot.decide`: the blend is in the main loop, and strategy_sim's `bot_strategy` (~343-346) has its own
  blend line, so it needs a 3-line mirror calling the same helper, with a pinning test (flag off = identical).
- Can lose: (1) if SIG settles at the outcome the bot gives up the carry (+770 on today's book: small; T2.3 takes it
  back on a schedule). (2) Self-reference: our own quotes are in book_fv; we are best in ~7% of markets, and the
  estimator is cross-sectional, so one market cannot move s; the clip bounds it. (3) A rival could move thin tail
  books to shift s: winsorising and the 50-market minimum bound it. No rule issue: it changes only where we quote.
- Test: tilt world, variants `ref_tilt_enabled` alone; pass = d pnl_liq >= +1 SE, cap_end down 5+ points,
  exit_ratio up, hold_med down, writes_pm <= 28, pick_cost not up by more than 1 SE. Also run once in the OLD world
  (`_world_tilt` 0): must not lose more than 1 SE (s estimates ~0 there, so it should be neutral). 12 CPU-min.
- Prior: +0.2% of the account per day (range -0.1% to +0.5%): removes the -151/point mark drag (the tilt rose 2.7
  points in 16 h), and frees most of the 40k toward-Polymarket capital for the ladder.

### T2.2 Measured weights (rank 2: no code, 3 runs)
- Mechanism: with the tilt removed, the blend weight is the measured convergence of the residual, and the reaction
  to a Polymarket move is its measured pass-through. Both say ~0.3, not 0.7.
- Evidence: residual closure +0.38 (30 min), +0.23 to +0.30 (2-8 h); pass-through 0.14 tails, 0.29 middle.
  Caveat: mid noise in thin books inflates the residual number (errors in variables), so treat 0.3-0.5 as the band.
- Code: none. Variants `ref_tilt_enabled` + `ref_weight` 0.5 and 0.35. Optional 10 lines: `ref_weight_tails`
  (weight where r < 0.15 or > 0.85; 0 = use `ref_weight`), only if 0.35 wins in the tails and loses in the middle.
- Can lose: news regime, if real Polymarket moves DO carry into the tournament faster than 16 h of day-one data
  shows (n = 105 moves, 11 above 2c). More re-prices on a noisier anchor (writes).
- Test: 2 screens (12 CPU-min) + the winner in news 6 x 6 with `mk15_mid` as the judge of pick-offs (not markout15_c,
  which assumes Polymarket is truth). Go = beats T2.1 alone by >= 1 SE on pnl_liq and writes_pm <= 28.
- Prior: +0.05% per day over T2.1 (range -0.2% to +0.2%). Cheap to learn, modest to earn.

### T2.3 Carry on a schedule, only if SIG settles at the outcome (rank 3: 10 lines, no sim, owner question)
- Mechanism: the tilt is a yield on locked capital that is paid only at settlement: s / days left = 0.16% per day
  today, about 1.7% per day in the last 3 days if s holds at 5%. So be tilt-neutral now and take the carry late:
  `ref_tilt_carry_days` (0 = off): inside the last N days the applied s ramps linearly to 0, so quotes lean back to
  raw Polymarket and the book loads favourites / sheds longshots as the horizon shortens.
- Evidence: return on capital to settlement median 2.0%, top decile >= 4% (today, 32 days out); the yield per day
  rises as 1 / days left. No evidence yet that the tilt persists into late October, nor on SIG's end valuation.
- Can lose: if unresolved positions are marked at the last trades, the carry pays nothing and the late loading is
  the same stuck book as today, at the worst time for rank. This also conflicts with START_HERE's "flatten from 24 h
  before": it is one or the other, decided by SIG's answer. HARD GATE: default 0, and write in the setting's comment
  that it must stay 0 until the owner has SIG's answer in writing.
- Test: none possible in a 3-6 h sim. Unit tests on the ramp only. Decision by arithmetic and the SIG answer.
- Prior: if settlement is at the outcome and s is 5-10% in the last week: +1.5% to +4% of the account, once, on
  ~40-50k of capital. If not: 0 (flag stays off). The largest single number in this plan, and the least certain.

### T2.4 Tilt exposure as a risk limit (rank 4: 25 lines, rides on T2.1's estimator)
- Mechanism: treat sum pos x (r - c) like party delta: above `tilt_exposure_max_frac` (0 = off; try 0.10 of the
  account) the adding side that increases the exposure is switched off (never forces an exit).
- Evidence: +15,087 today = 15% of the account; -151 per point; the tilt moved 2.7 points on day one.
- Code: next to the party-delta limit feeding `decide` (grep `party_delta`); live_sim gets it through `decide` if
  the exposure is passed the same way as party_delta, otherwise a small mirror.
- Can lose: blocks adds that were good MM trades; with T2.1 on it should rarely bind (go/no-go: binds in < 10% of
  market-cycles with T2.1 on, and d pnl_liq not below -1 SE). 6 CPU-min. Prior: 0 on the mean, cuts the left tail;
  this is the "rank, not P&L" lever: rank is marked at tournament prices and the tilt is the factor that marks us.

### T2.5 Senate pair: unwind rule (rank 5: ~30 lines)
- Fact: `pair_unwind_enabled` is already ON, but needs other traders' bids to sum >= 1.005
  (`pair_unwind_min_profit`). The pair's bid sum is ~0.990 (haircut 55 + 18 on 7,335 sets), mid sum 0.994: it never
  fires. 7.3k of capital (14% of position capital) earns exactly 0 at settlement and adds mark noise.
- Rule: `pair_unwind_passive` (False), `pair_unwind_max_cost` (0.003). While a complete set is held, rest the ask of
  leg X at max(join best ask, 1 - best bid of the other leg - max_cost); when it fills, sell the same quantity of
  the other leg at its bid at once (one take). A set is then closed for >= 1 - max_cost. One slice at a time
  (`pair_unwind_max_frac`), so the unmatched leg is never more than one slice; the second leg is urgent.
  Tonight alternative, no code: none, the OVERRIDABLE floor of `pair_unwind_min_profit` is 0.0.
- Can lose: max_cost x sets (0.3c x 7,335 = 22) plus the second leg slipping if its bid vanishes between the fill
  and the take (bounded by one slice). Selling both legs of a set to others is not a self-trade.
- Test: scripted unit test (set held, bids 0.62 / 0.37 -> asks, fill, take). live_sim only if `_house` starts with
  a set; otherwise unit-test only. Prior: frees 7.3k; worth about +20 to +60 per day when capital-bound.

### T2.6 Ideas that did NOT survive the numbers (do not build)
- Per-market blend weight from each market's own gap half-life (the owner's question; a prototype exists:
  strategy_sim `_bias_hl` ~347/~809, fair = Polymarket + EMA gap clipped +-3c). Per-market persistence is weak
  (slope 0.46, 47 of 229 stable) while one cross-sectional number has R2 0.60. 229 noisy estimates lose to one.
  Possible later: a slow per-market EMA of the RESIDUAL (after the tilt), clipped +-2c; not now.
- Full Avellaneda-Stoikov rewrite of `decide`. With T2.1 the stack already is one: mark = book, drift = tilted
  Polymarket x weight, inventory term = skew + age skew. A rewrite would re-open ~1,100 pinned tests for the same
  prices. The minimal version IS T2.1 + T2.2.
- Rivals "one tick behind / one tick inside". The snapshots hold top of book only, no depth, no identities; rival
  floors cannot be measured. What can be said: rivals do not chase Polymarket (pass-through 0.10 in the same
  snapshot), so "stale after a Polymarket move" is not stale at tournament marks: a 2c move is worth ~0.5c at the
  mark, less than the cost of crossing. Executor: read how `take_stale_quotes` (~5589) values its edge; if it
  assumes full pass-through, report it (a `stale_take_passthrough` factor is the fix; do not build it unscreened).
- Locked-set arbitrage as a profit centre. 8.1% of race-snapshots, but one tick, one snapshot long, depth unknown,
  the two legs are not read at the same instant, and every rival bot sees it too. `arb_two_sided` was switched OFF
  live on purpose. Executor: only report from the journal what the arbitrage path earned while it was on.
- Quoting the 49 untouched tail markets. Spread is wider there (1.8c) but two-sided quoting needs ~0.95 of capital
  per share on one side, the scarce thing. Revisit after T2.1 frees capital; not before.
- A realised-P&L "lock-in schedule". The leaderboard marks open positions (trade-average rule), so realised and
  unrealised count the same until the close; what matters is factor exposure (T2.4) and the end valuation (T2.3).
  Smart Score is undefined ("not scored yet"); if it is P&L / volume, T2.1's shorter holds and fewer gap adds help,
  but nothing here can be tuned to it.

## 5. Executor budget and build order (120 CPU-min of simulation; up to 3 sub-agents)

Build order (a cut-off after any step leaves the best expected value on the branch):
1. Yardstick: `_rival_anchor`, `_world_tilt`, `_world_tilt_growth`, new fields, pinning tests (section 3.1).
2. T2.1 tilt (helper + estimator + blend + strategy_sim mirror + tests) and the status fields `tilt_s`,
   `tilt_exposure`.
3. B (5 lines) then A. 4. T2.5 pair. 5. T2.4 exposure limit. 6. T2.3 ramp (unit tests only). 7. C. Ops last.

Sub-agents (worktrees): #1 yardstick (tests/live_sim.py, tests/strategy_sim.py only); #2 T2.1 (mm_bot blend
region ~3230-3260 and helpers near `fair_value`); #3 A + B (`decide` ~4223-4330, `compute_quote` ~1934,
`kelly_position` ~1789). Different regions of mm_bot.py: merge #2 then #3. All ten suites green before any run.

| Simulation (8 seeds x 3 h quiet = 6 CPU-min unless noted) | CPU-min |
|---|---|
| Re-score in the tilt world: base + 4 old flags | 30 |
| T2.1; T2.1 in the old world; T2.1 + `ref_weight` 0.5; T2.1 + 0.35 | 24 |
| A; B; T2.1 + B; (A + B, or T2.1 + A if T2.1 fails its exit_ratio test) | 24 |
| T2.4 on top of the best so far | 6 |
| Confirm the best ONE combination vs base: 6 x 6 news (9 CPU-min each side) + 6 x 3 quiet tilt growth 0 | 18 + 9 |
| Reserve | 9 |
| Total | 120 |

Split: Tier 1 about 45%, Tier 2 about 55% of simulation; of build time, yardstick and T2.1 first, each ~1 h.
Pass rule everywhere: v1's (d pnl_liq not below -1 SE, cap_end down 5+ points, wc_end down, writes_pm <= 28,
pick_cost not up by more than 1 SE, exit_ratio up); judge in the tilt world, report the Polymarket mark alongside.
Stop a variant early at -3 SE after 4 seeds. The full 16 x 6 confirm needs the owner to lift the cap: say so.
Results go to SIM_NOTES.md "Round 5" and a short RESULTS_POLY_BIAS.md.

## 6. Deploy staging (owner decides every step)

1. Nothing live until Phases 0-1 are in and the re-score table exists.
2. Then the winning flag ON for non-headline markets only (executor: add `<flag>_headline` False, or gate on
   `ex.group not in cfg.headline_races`). 2-hour watch list: capital in positions (down), exit_ratio (up),
   realised P&L (not negative beyond 0.3% of the account), writes/min (<= 28), deferred changes, fills on the
   reducing side within 2 min after a Polymarket jump (should be none), no own bid >= own ask in any market.
3. Then headline. Rollback at any point = the flag off in settings_override.json (read within overrides_seconds).

Tonight, no code, OWNER'S DECISION (not sim-tested at an honest mark):
- `"kelly_max_market_frac": 0.01`: halves the size the gap can reach (the top non-headline positions sit at the
  2% cap). Only the adding side shrinks; nothing is forced out. Risk: less capacity where Polymarket is right; low.
- `"headline_position_frac": 0.05`: Rep U.S. House is -8,246 (7.2k capital, gap 5.5c against the tournament).
  Risk: confirm in a test first that a limit below the held position only stops adding (I read it so in
  compute_quote `limited` ~2073-2077, did not run it).
- Keep `capital_ceiling_adding_size_factor` 0.5 for now: 0.25 "lost" only on the Polymarket mark; re-score first.

v2, tonight:
- The two overrides above stand; they also cut the tilt exposure's growth. With T2.1 not built, nothing no-code
  removes the tilt from fair value: `ref_weight` 0.5 would still lean 1.3c at the tails (above `min_edge`).
- ASK SIG NOW (owner): are positions open at the 4 Nov 00:00 UTC close settled at the outcome, or marked, and at
  what? It decides whether the +770 tilt claim is ever paid, T2.3, and the end-of-tournament plan.
- Deploy order once tested: T2.1 (`ref_tilt_enabled`) is the first flag to stage, non-headline first, same watch
  list plus `tilt_s` (expect 3-8%; alarm if it sits at the 0 or 0.12 clip) and `tilt_exposure` (should fall).
- `ref_weight`: v1's "do not lower before the news runs" stands, with a changed reason: the live data says the
  tournament follows only 14-29% of a Polymarket move, so 0.5 is the first candidate once T2.2's runs are in.
- Do not switch on `fl_bias_enabled` as a fix (wrong sides, section 3).

What NOT to do:
- Do not lower `ref_weight` live before the news runs: at 0-0.3 the bot stops pulling toward real Polymarket
  moves and gets picked off; the sim's informed takers trade exactly that.
- Do not turn on fast_unload / reduce_join_best on the strength of "the old test was biased": re-score first.
- Do not judge anything on pnl (Polymarket mark) alone again, nor on the liquidation mark at `_rival_anchor` 0.
- Do not hand-liquidate the gap positions at market: the haircut is small at the top of book (428) but depth is
  unmeasured, and dumping into a thin book moves the price against the rest of the position.
- No design may place an order to move a price, trade with itself, or exceed the write limit (tournament rules).

## 7. Not checked
- The afternoon of 2 Oct (90% in positions, 112 positions, 10-11k free): the snapshot ends 08:14 UTC.
- Book depth: liquidation is top-of-book only; snapshots hold no depth.
- Fill-level prices: fills.csv has 827 of 1,813 fills with an unknown side, so trades are priced at the mid from
  position changes; "spread capture" is a residual (+1,398), not measured directly.
- How the exchange marks account value (mid, last or other).
- No simulation was run; every Phase 2 expectation is reasoning from the code and the data.
- Line numbers are approximate (Package 4, fd28b0a).
- v2: no simulation was run by the second planner either; every Tier 2 prior is an estimate from 16 h of day-one data
  (tilt, pass-through n = 105 moves, 11 above 2c). Day one of a tournament may not be representative.
- v2: whether the tilt keeps growing, flattens, or reverses after day one (rerun tier2.py on the next snapshot:
  section 8 of its output is the one number to watch).
- v2: locked sets are measured from snapshots whose two legs are not read at the same instant; no depth.
- v2: `best_bid` / `best_ask` in snapshots include our own quotes (we are best in ~7% of markets); the tilt
  estimate was not re-run without them.
- v2: how `take_stale_quotes` values its edge, why `arb_two_sided` was switched off, whether live_sim's `_house`
  start can hold a complete set: not read.
- v2: SIG's end valuation and Smart Score definition: unknown; T2.3 is gated on the first.
