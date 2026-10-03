<!-- STATUS (Finisher 2b, updated on every push) -->
**STATUS 03:45 UTC 3 Oct (branch claude/finisher-package6):** phase = DONE. READY: **Package 5** (c14c92b on claude/finisher-package5, PR #7, frozen). Package 6: nothing clearly positive, nothing shipped (section below); its three candidate settings stay built and OFF on this branch.
Key numbers (Package 5, d vs base, exchange-style / liquidation): T2.1 `ref_tilt_enabled` pinned 16 x 6 +509 ± 110 / +601 ± 107; free 16 x 6 +330 ± 64 / +426 ± 63; news +670 ± 180 / +761 ± 160. Backstop 0.85 and hysteresis 0.01 with T2.1: not clearly positive.
Deploy: Package 5 code (all flags off) -> deploy/package5 stage1 (`ref_tilt_enabled`) -> stage2 (+ `take_tilted_ref`) -> stage3 (+ `ref_tilt_headline`); go/no-go rules in the Package 5 section. Live still Package 3 final.
Next (a later cycle): a simulator background worst case that grows like live, then re-screen the exits-in-reduce-only flags (the 44k capital lever).
ETA: none; the executor is finished (final commit "Executor complete" on this branch).

# START HERE (Team run, branch `claude/run-c-tournament-improvements-pycdet`)

Status: **complete** (Team complete at 14:45 UTC) (started 2026-10-02 08:50 UTC; credits nearly spent at 14:35 UTC). **Package 1 READY at 10:10 UTC, Package 2 READY at 11:05 UTC, hot-fix Package 2.1 READY at 12:05 UTC, hot-fix Package 2.2 READY at 12:40 UTC, Package 2.3 READY at 13:10 UTC, Package 3 READY at 14:45 UTC (deploy this one)** (deploy-ready, cumulative). Package 2 is LIVE since 11:21:57. Base: `claude/live-2026-10-02b` (5c0463a), the code live since 08:34.
The Builder's previous START_HERE is kept as `START_HERE_BUILDER.md`; Run A's notes are `ENGINEERING_NOTES.md`.
Plan: `PLAN.md`. Packages appear below as they become READY (commit messages start "READY: Package N").
Deploy only commits whose message starts "READY"; the branch is cumulative.

## Package 6 (NO CHANGE SHIPPED, Finisher 2b second cycle, 3 Oct 02:30-03:40 UTC; branch `claude/finisher-package6` from the Package 5 READY commit c14c92b)
Aim (owner): the live bottlenecks: (a) backstop pinning (reduce-only ~81% of cycles), (b) capital lock-up (positions 99.9k of a 101.0k account,
84 dead + 49 quiet held markets with 44k), (c) the exchange mark vs fair value. Evidence: `analysis/poly_bias/P6_RESEARCH.md`,
`analysis/poly_bias/NO_REDUCE_QUOTE.md`, `PLAN_P6.md`, `SIM_NOTES.md` "Round 6".
**What the 02b data says.** (a) The reduce-only churn is not a cost (-87 net over 7 episodes; 3% of reduced shares re-added within 30 min); 23k
of the 27k shares added in reduce-only were arbitrage sets; cash binds before the backstop; a 0.85 backstop would re-pin within 15-30 min as the
worst case climbs 280-570/min between episodes. (b) In 85 of the 133 dead/quiet held markets the bot rests NO reducing quote: 74 are complete-set
legs whose exit side the race-netted reduce-only clip zeroes (11.8k), 9 are blocked by the reference guard (13.8k; the guard is right); and in
reduce-only EVERY exit feature was switched off (reduce_join_best, hold_quote, fast unload). Every held market has other traders' quotes on the
exit side deep enough for 97-100% of the position at a 0.6-0.7c spread. (c) The exchange's mark lags the mid by ~2 h (1.5k below it; our fills
move it ~0.05c each): a reporting gap, no lever; trading to move a mark would be manipulation. (d) Pass-through after 16:26: 0.14 / 0.23 median
(15 min / 1 h), fat tail, n = 11. Also: `snapshots.our_bid/our_ask` are the WANTED quote, not the resting orders; 30% of sells in fills.csv carry
the YES price; the `trades` table is empty.
**Built (OFF, unit-tested, on this branch only):** `exit_quotes_in_reduce_only` (hold_quote and reduce_join_best keep working in reduce-only; the
reducing side only, size <= the race-netted position), `pair_passive_in_reduce_only` (a complete-set leg may rest its slice when only the
reduce-only clip emptied the side; `ex.ro_clip` records why a side is empty), `backstop_soft_frac` (adding size shrinks to 0 across a band under
the backstop). Tests: test_exit_in_ro 40, test_backstop_soft 35; every other suite green.
**Screened (pinned worlds, on top of T2.1, 8 x 3 quiet, d pnl_lag / d pnl_liq vs base; T2.1 alone +98 ± 138 / +129 ± 193 and +304 ± 96 / +299 ± 91):**
C 4 h + exits in reduce-only +201 ± 152 / +290 ± 169 and +253 ± 84 / +220 ± 124 (+100/+160 then -50/-80 vs T2.1: not clearly positive);
pair passive in reduce-only -26 ± 149 / +50 ± 166 and +146 ± 106 / +61 ± 144 (below T2.1, +4 writes/min: no); backstop soft band +97 ± 107 /
+153 ± 127 and +217 ± 128 / +197 ± 123 but **+7-8 writes/min and deferred changes +19-21k/h** (shrinking sizes re-price every cycle: FAIL).
**Why the sim is weak here:** its rivals bid for our exits, so the bot leaves reduce-only easily (42-47% of base cycles pinned vs 81% live); the
exit-in-reduce-only effect is under-weighted. Next step for a later cycle: a background worst case that GROWS between episodes as live does
(+280-570/min), then re-screen `exit_quotes_in_reduce_only` + `hold_target_hours`; if it passes there, it is the capital lever (44k).
**Decision:** nothing clearly positive -> no "READY: Package 6"; deploy Package 5 on its own evidence. Owner options without code: none that the
data supports tonight (0.85 and hysteresis 0.01 were judged in Package 5).

## Package 5 (READY, Finisher 2b executor, 3 Oct ~03:00 UTC; branch `claude/finisher-package5`, draft PR #7): the Polymarket-bias fix. Package 4 (c29f762) + a tilt-corrected reference (T2.1) and nine more settings, ALL OFF by default + an honest simulator yardstick + ops fields. Code deploy by handover restart, then flags by settings_override.json (deploy/package5/).
Plan: `PLAN_POLY_BIAS.md` (v2). Evidence: `SIM_NOTES.md` "Round 5" (every run, with error bars) and `analysis/poly_bias/` (RESULTS.md,
TIER2_RESULTS.txt, TAKES_RESULTS.md, SNAPSHOT02B_RESULTS.md, P6_RESEARCH.md). Raw simulator output: `tests/live_sim_round5_*.txt`.
Run plan and actuals: `PLAN_FINISHER.md` "Package 5". Staged override files and their order: `deploy/package5/`.

**Diagnosis (live snapshots to 08:14 and 20:37 UTC, 2 Oct).** The tournament prices every market with ONE favourite-longshot tilt:
tournament mid ~ c + (1 - s)(Polymarket - c), c = 1/legs. s by 4-hour bin since 1 Oct 16:00: 2.4, 3.4, 4.7, 5.1, 5.4, 5.2, **6.3%**
(R2 0.60-0.68; 0.1-0.2 points higher with our own best quotes excluded, so we do not cause it); growing ~0.16 points/h on average, flat
08-16 UTC, rising again after 16:00. That one number explains 60-68% of the Polymarket-tournament gap variance; the tilt part of a gap
WIDENS over time (-0.14 at 4 h, -0.22 at 8 h), only the residual closes (~0.3). The bot, anchored 70% on raw Polymarket, treats the tilt
as mispricing: 88% of open position capital was entered toward Polymarket (76% at 08:14); 56% of toward-entered capital has exited vs 96%
of against-entered; open capital median age 8.3 h; tilt exposure (sum pos x (Polymarket - 0.5)) **+32,742 at 20:37 vs +15,087 at 08:14**,
each point of tilt marking the book -327; position capital 100% of the account. Marks at 20:36: exchange 101,004 / liquidation 101,986 /
tournament mid 102,385 / Polymarket 107,316: the "+6.3k Polymarket gain" is a claim on gaps that have only widened; the exchange marks
~1.4k below the mid (a lagged/smoothed trade price; our fills move it ~0.05c each). Stale-quote takes since 08:14 (38, 24,958 shares):
+1,480 at Polymarket, **+154 at the tournament mid**. Since Package 3 (16:26) the bot is reduce-only in 81% of cycles (sum-of-maxima worst
case 77-82k cycling against the 0.80 backstop); cash binds too (positions 99.9k of a 101.0k account).

**The yardstick (Phase 1; `tests/live_sim.py`, pins `tests/test_live_sim_marks.py` 110).** Every earlier round marked P&L at Polymarket
in a world whose rival bots also priced from Polymarket. New world knobs: `_rival_anchor` (1 = rivals and informed takers price from the
tournament consensus), `_world_tilt` / `_world_tilt_growth` (the tilt's share of the real starting gap and its growth; the start book is
always the real one), `_world_tilt_add` (extra tilt), `_bg_wc` (the rest of the account's worst case so the live reduce-only backstop
binds). New fields: `pnl_lag` (VWAP of the last 30 min of prints: the exchange-style mark, THE JUDGE for rank), `pnl_liq` (start and end
at liquidation: longs at the best other bid, shorts at the best other ask), `pnl_mid`, `mk15_mid` (markout vs the consensus), `exit_ratio`,
`hold_med`, `ro_frac` (share of cycles reduce-only), `tilt_s_end`. Knobs at 0 = the old numbers to the digit. In the old world the
Polymarket mark overstated base P&L by ~1,440 per 3 h (pnl 1,590 vs pnl_liq 152). Worlds: "tilt" (anchor 1, tilt 0.05 + 0.002/h),
"pinned" (+ `_bg_wc` 38000: reduce-only 42% of base cycles), "high-tilt" (+ 8 points), "flat", "old", "news".
**Earlier conclusions re-scored (tilt world, 8 x 3 quiet, d pnl_liq):** none flipped outright. `fast_unload_enabled` -165 ± 110 -> -22 ± 100
(its loss was the mark; still no gain), `reduce_join_best` -48 ± 81 -> -23 ± 120, `turnover_control_enabled` +49 ± 62 -> +67 ± 120,
ceiling factor 0.25 vs 0.5: -262 ± 100 -> -162 ± 110 (keep 0.5). None passes the new rule (none frees 5 points of capital).

**What is in the code (all OFF; each in OVERRIDABLE unless said; `_headline` = staging gate, False = not in `headline_races`):**
T2.1 `ref_tilt_enabled` (+ `ref_tilt_headline`, `ref_tilt_min_markets` 50, `ref_tilt_halflife_min` 30, `ref_tilt_max` **0.20**, `ref_tilt_winsor` 0.08,
`ref_tilt_rampin_min` **0**): quote around r' = c + (1 - s)(r - c); s estimated each cycle from the liquid non-headline cross-section
(`TiltEstimator`, runs read-only with the flag off; `tilt_s`, `tilt_s_applied`, `tilt_s_applied_headline`, `tilt_exposure`, `tilt_state`
in status.json; s and the ramp clocks restored from status.json on a restart if it is < 10 min old). X12 `take_tilted_ref`: the stale-quote
takes measure their 5c edge from r' (needs T2.1). A `reduce_from_book` (+ `_pause_s` 120, `_headline`; X11 `reduce_from_book_dead_only`,
`reduce_from_book_max_turnover`): the reducing side priced from the book, with the self-cross cap. B `kelly_edge_cap`. C `hold_target_hours`
(+ `hold_unload_budget_frac` 0.02, `hold_take_max_per_min` 2, `hold_target_headline`): aged lots join the best; older than 2T taken inside a
budget that starts fully used for an hour. T2.4 `tilt_exposure_max_frac` (+ `_headline`). T2.5 `pair_unwind_passive` (+ `pair_unwind_max_cost`
0.003). X5 `gap_size_shrink` (+ `gap_size_floor` 0.25). T2.3 `ref_tilt_carry_days` (NOT overridable: hard gate until SIG answers the
end-valuation question). Ops (no flag): status.json `liquidation_value`, `liquidation_unpriced`, `realised_pnl` (+ `_scope` "maker fills
only"), `unrealised_pnl`, `pnl_unreconciled`, `toward_ref_capital_frac`, `capital_over_6h_frac`, `exit_ratio_24h`; the 2-hourly summary line
"Liquidation ... realised ... toward Polymarket ... older than 6 h | tilt ..., exposure ..."; recorder `account.liquidation_value` (nullable,
ALTER on old files).

**Results (d vs BASE = Package 4 defaults + the live overrides, paired seeds, ± 1 SE; judge = `pnl_lag` then `pnl_liq`; full tables with every
column in SIM_NOTES "Round 5 summary tables"). Pass rule: d pnl_liq >= -1 SE, capital down 5 points, worst case down, writes <= 28 and
deferred not up > 20%, `mk15_mid` not worse by > 1 SE, exit_ratio up.**
| Flag (all OFF by default) | d pnl_lag / d pnl_liq, tilt world 8 x 3 quiet | pinned 16 x 6 (live-like) | news 6 x 6 | capital (8 x 3) | writes/min | Verdict |
|---|---|---|---|---|---|---|
| **T2.1 `ref_tilt_enabled`** | **+166 ± 72 / +163 ± 60** (ramp 20: +176 ± 110 / +199 ± 83) | **+509 ± 110 / +601 ± 107** (even the Polymarket mark +341 ± 94) | **+670 ± 180 / +761 ± 160** | -7.6 pts | -1.2 (pinned +2.4: it leaves reduce-only) | **PASS: the recommendation.** Old world +215 ± 74 / +244 ± 94, flat tilt -60 ± 59 / +100 ± 84, free 16 x 6 +330 ± 64 / +426 ± 63 |
| T2.1 + `worst_case_backstop_frac` 0.85 | - | +587 ± 112 / +649 ± 101; vs T2.1 alone **+78 ± 86 / +48 ± 81** | - | -6.0 pts | +3.1 | not clearly positive: stays 0.80 (stage 4 optional, later) |
| T2.1 + `reduce_only_hysteresis` 0.01 | - | +456 ± 123 / +523 ± 117; vs T2.1 alone **-57 ± 60 / -83 ± 66** | - | -5.7 pts | +2.7 | no: stays 0.03 |
| T2.1 + `ref_weight` 0.5 / 0.35 (T2.2) | +87 ± 82 / +176 ± 50; +139 ± 130 / +186 ± 103 | - | - | -9.7 / -13 pts | -0.0 / -0.4; deferred +18% / +22% | no-go: not >= 1 SE over T2.1, markout worse (-0.056 / -0.076c) |
| X12 `take_tilted_ref` | not simulable (no take path in live_sim) | | | | | data: the 38 live takes since 08:14 earned +154 at the tournament mid on 25k shares: recommended with T2.1 |
| A `reduce_from_book` (+ `_pause_s`, `_headline`) | +36 ± 150 / +91 ± 135; with T2.1 +42 ± 113 / +124 ± 90 | - | - | -12 / -14 pts | -0.5 / -1.5 | FAIL: markout -0.23 / -0.17c (sells to informed flow), no P&L over T2.1 |
| X11 `reduce_from_book_dead_only` | with T2.1 -45 ± 109 / -26 ± 109 | - | - | -9.2 pts | -0.8 | FAIL: below T2.1 alone |
| B `kelly_edge_cap` 0.01 + `kelly_max_market_frac` 0.01 + `headline_position_frac` 0.05 | **-312 ± 61 / -216 ± 19** (stopped at 4 seeds); with T2.1 -215 ± 119 / -89 ± 102 | - | - | -6.9 pts | -4.0 | FAIL: gives up spread income everywhere. Do NOT apply the planners' "tonight" overrides |
| X5 `gap_size_shrink` 0.03 | with T2.1 +110 ± 118 / +64 ± 85 | - | - | -7.8 pts | **+3.6**, deferred +4,300/h | FAIL: below T2.1, smaller orders re-price more |
| C `hold_target_hours` 4 (+ budget, 2 takes/min, `_headline`) | with T2.1 +136 ± 150 / +175 ± 130 (before takes were charged writes) | - | - | -11.2 pts | -0.9 | neutral vs T2.1 alone; the capital-freeing backstop; Package 6 candidate in reduce-only |
| T2.4 `tilt_exposure_max_frac` 0.10 / 0.25 | with T2.1 **-326 ± 96 / -331 ± 86** (stopped) / never binds in the sim (= T2.1) | - | - | | -12.3 / 0 | FAIL at 0.10 (the live book starts at 15% of the account); 0.25 untestable here |
| T2.5 `pair_unwind_passive` (+ `_max_cost` 0.003) | with T2.1 +95 ± 82 / +87 ± 74 (before takes were charged writes) | - | - | -7.9 pts | +0.2 | not positive in 3 h; frees the 7.3k Senate set; OFF, Package 6 candidate in reduce-only |
| `ladder_enabled` with T2.1 | +155 ± 77 / +135 ± 66 | - | - | -7.5 pts | -1.0 | no gain over T2.1 (cash still binds); OFF |
| T2.3 `ref_tilt_carry_days` | unit tests only | | | | | HARD GATE: 0, not overridable, until SIG answers the end-valuation question |
| re-scored old flags: `fast_unload_enabled` / `reduce_join_best` / `turnover_control_enabled` / ceiling factor 0.25 | +120 ± 95 / -22 ± 102; +40 ± 111 / -23 ± 122; +170 ± 106 / +67 ± 121; -100 ± 112 / -162 ± 108 | - | - | -0.6 / -2.0 / -1.2 / -0.4 pts | | none passes (no flip of an earlier verdict; fast_unload's old loss was the mark); all stay as they are |
Reading: removing the tilt from fair value (T2.1) is the one change that pays at the exchange-style mark AND at liquidation in every world
(free, pinned, news, flat, old), frees capital and saves writes. Everything that only makes the exit more aggressive (A, X11, lower
ref_weight) sells into informed flow for no extra P&L; everything that only shrinks size (B, X5, T2.4 at 0.10) gives up spread income; C,
T2.5 and T2.4 at 0.25 add nothing over T2.1 in 3 h. The "+163 / +166" is 3 h on the 74 biggest markets (~+1,300 per day on the sim's share
of the book) before anything the sim does not model (the other ~160 markets, the owner's manual trades, SIG's end valuation).

**Verdicts on the owner's three questions (each with T2.1, pinned world):** `worst_case_backstop_frac` 0.85: NOT NOW. Pinned 16 x 6 with T2.1: +587 ± 112 / +649 ± 101 vs T2.1 alone +509 ± 110 / +601 ± 107, paired difference +78 ± 86 (exchange-style) / +48 ± 81 (liquidation), +0.7 writes/min; the 8 x 3 "+180" was noise; the live data says cash binds before the backstop and the reduce-only churn cost -87 net (P6_RESEARCH). Not clearly positive = stays 0.80; revisit after a day of T2.1 (the stage-4 file exists for that).
`reduce_only_hysteresis` 0.01: NO. With T2.1, paired: -57 ± 60 / -83 ± 66, +0.3 writes/min; alone (8 x 3): +22 ± 140 with deferred changes +37%. Stays 0.03. `ref_tilt_max`: 0.12 binds at a 15% tilt (the estimate sits at the clip while the world is at
0.15) for the same 3-h P&L as 0.20 (+299 vs +302 at liquidation, +316 vs +351 exchange-style, within noise; 0.20 costs +2.7 writes/min);
default set to 0.20 so the estimate stays readable and the slow mark bleed of untracked tilt (-327/point/day live) is not accepted by design.
Live s is 6.3%: alarm, re-measure (analysis/poly_bias/snapshot02b.py) if `tilt_s` passes 0.12.
**Ramp-in:** measured and not needed (default 0): first 2 h, step-on vs 120-min ramp: exchange-style +70 ± 39 vs -33 ± 39, liquidation
+174 ± 55 vs +38 ± 71, Polymarket-marked cost the same (-107 ± 25 vs -112 ± 28); the step-on sells ~5k shares of inventory at tournament
prices in 2 h and re-prices ~157 markets once (~10 min of the write budget); the ramp keeps buying the tilt for 2 h and re-prices
continuously (+0.8 writes/min, deferred +1,070/h). The setting stays (20 min: +176 ± 110 / +199 ± 83 at 8 x 3, pick_cost +9 vs +38: the same P&L as the step-on with the re-price wave spread over ~10 cycles, so 20 is the default; 0 and 120 measured as above).
**Recommended settings and order (deploy/package5/*.json; each file read within `overrides_seconds` 30 s):** stage 0 code (all off) ->
stage 1 `ref_tilt_enabled` -> stage 2 + `take_tilted_ref` -> stage 3 + `ref_tilt_headline` -> stage 4 + `worst_case_backstop_frac` 0.85
(NOT now: see the verdict; the file is there for later). Final file after stage 3: `{"arb_two_sided": false, "worst_case_backstop_frac": 0.8, "capital_ceiling_adding_size_factor": 0.5,
"writes_per_minute": 28, "writes_per_minute_max": 28, "burst_cycle_seconds": 60, "ref_tilt_enabled": true, "take_tilted_ref": true,
"ref_tilt_headline": true}`. Leave `reduce_only_hysteresis` 0.03 and `ref_weight` 0.7. Do NOT apply the planners' "tonight" overrides
(`kelly_max_market_frac` 0.01 / `headline_position_frac` 0.05: design B, -216 ± 19). Everything else stays OFF.
**Rollout, go/no-go rules (owner deploys; every number below is checked in status.json / the journal; "start" = the value at the moment
of the toggle).** Do each toggle in a quiet hour (03:00-07:00 UTC or 20:00-23:00 UTC: Polymarket moves and fills are fewest there).
Stage 0, code (handover restart, all flags off = Package 4 behaviour): check for 10 min: `tilt_s` printed and between 0.03 and 0.10
(expect ~0.06-0.08; a value stuck at 0.0 or at the `ref_tilt_max` clip for 15 min = the estimator is wrong, do NOT go to stage 1),
`tilt_exposure` printed (expect ~+30k to +35k), `liquidation_value` within ~1.5k of `account_value`, writes <= 28/min, `rate_limited_total`
0, no "refused override" alert, orders adopted by the handover kept. Then stage 1 at once if green.
Stage 1, `ref_tilt_enabled` (deploy/package5/settings_override.stage1_tilt_nonheadline.json). First 20 minutes: the applied tilt ramps
from 0 to the estimate (`tilt_s_applied` rising to `tilt_s`), so ~150 markets re-price toward the book in 2-3 steps (the write budget may
run at 28/min for a few minutes; deferred changes rise then fall back within 30 min); no own bid >= own ask anywhere (the recorder's our_bid < our_ask for every quoted market; one violation = rollback); no fills on a reducing side
within 2 min of a Polymarket jump >= 3c (the jump guard still applies); `tilt_s_applied` = `tilt_s` after 20 min.
After 1 h (GO to keep it on): (a) buy/sell fill balance: reducing shares / adding shares over the hour >= 1.0 bot-wide (live 16:26-20:37
was 0.84; sim: +0.1 to +0.16 on the ratio in 2-3 h) and in the 10 biggest toward-Polymarket positions the position is flat or smaller,
none bigger by more than 5%; (b) position capital share not above start (100% today) and falling by >= 1 point; (c) reduce-only share
of cycles below its start (81%); (d) account on the exchange's number not below start - 300 (0.3%); (e) writes <= 28/min, 0 x 429.
After 3 h (GO to stage 2): (a) exit ratio over the 3 h >= 1.0; (b) capital share down >= 3 points from start (sim: -5 to -8 in 3 h);
(c) reduce-only share down >= 15 points (sim: -33); (d) account on the exchange's number not below start - 500; realised P&L (FIFO,
`realised_pnl` in status.json) not below start - 300; (e) `tilt_s` still inside 0.03-0.10 and `tilt_exposure` below start.
Stage 2, `take_tilted_ref` (stage2 file): no quote change; stale-quote takes should fall to near zero in tail markets (journal "TAKE" lines:
live 38 in 12 h). Check after 1 h: no take in a market whose tilted gap was not confirmed (there should be none); fewer takes than the
previous hour. If takes continue at the old rate in tails, the tilted reference is not reaching the take path: rollback to stage 1.
Stage 3, `ref_tilt_headline` (stage3 file; the U.S. House and Senate legs: ~14-20% of position capital): the 4 legs re-price once by
~1c; the same 1-h checks on those 4 markets (Dem House long and Rep House short should shrink, never grow); account not below start - 300.
Stage 4, `worst_case_backstop_frac` 0.85 (stage4 file; only after stages 1-3 have held for 3 h): the bot leaves reduce-only at once if
the backstop was holding it; adding sides return on every market, throttled by the write budget (expect 5-10 min at 28/min). After 1 h:
worst case (`worst_case_loss`) below 0.85 x account and not rising more than 2k/h; capital share not rising above its stage-1 trend by
more than 2 points; exit ratio still >= 0.9. Hysteresis stays 0.03.
ROLLBACK (any stage, copy the previous stage's file; the flag is read within 30 s) on ANY of: a "refused override" alert or an own bid >= own ask;
`tilt_s` at a clip (0 or `ref_tilt_max`) for 15 min; account on the exchange's number below start - 1,000 within 3 h of a toggle (the sim
never moved the exchange-style mark by more than ~-100 in a 3 h run); writes above 28/min for 10 min or any 429 after the first 15 min;
exit ratio below 0.7 for an hour; capital share rising 3+ points in an hour; any take in a market whose gap was not confirmed. Rolling
stage 1 back to 0 is itself a one-step fair-value move (~150 markets, ~10 min of writes) and raises the sum-of-maxima worst case by
~0.5 points of equity: in the pinned state that can put the bot back into reduce-only for an episode. Code rollback (a handover restart
to c29f762 or 439ac54) only if the process misbehaves (exceptions in the journal, status.json not updating).
**Risks:** (1) SIG settles open positions at the outcome at the close: the tilt was then a yield the bot now declines (~+770 on the 08:14
book, ~+1.6k on tonight's); T2.3 exists for that case, gated. (2) The sim's informed takers trade toward the anchored consensus; if real
Polymarket moves carry faster than the data shows (pass-through 0.1-0.25 typical, fat tail, n small), T2.1's news gain shrinks. (3) The
switch-on re-price wave (~10 min of writes): quiet hour. (4) The estimator is cross-sectional over ~150 liquid non-headline markets (winsor
8c, min 50 markets, clip 0.20): a market-wide shift of the tails would move it; watch `tilt_s`. (5) The 74-market sim world's effective tilt
is ~7% (the biggest positions sit where the gaps are biggest). (6) The exchange mark lags the mid by ~2 h: the leaderboard will show
T2.1's gain late. (7) fills.csv prices: 30% of sells carry the YES price, not the NO price (P6_RESEARCH): `realised_pnl` uses quote prices.
**Untested:** T2.5's and C's take legs against the real exchange (fake API only); X12 end to end (no take path in live_sim; data-based);
multi-leg (3+) races (every sim race has 2 legs); the ladder with T2.1 beyond one screen; a tilt that REVERSES (the sim never ran one).
**Tier 2 ideas not built (PLAN_POLY_BIAS 4, T2.6):** per-market gap half-life weights (per-market persistence weak: slope 0.46, 47 of 229
stable), an Avellaneda-Stoikov rewrite (T2.1 is the minimal version), rival-floor models (no depth data), locked-set arbitrage as a profit
centre (+39 from 2 sets while on), quoting the 49 untouched tail markets (capital), a realised-P&L lock-in schedule (rank marks open positions).
**Owner decisions (recommendation in brackets):** (a) deploy Package 5 code [yes, quiet hour]; (b) stage 1-3 as above [yes]; (c) stage 4
0.85 [not now: +78 ± 86 over T2.1 alone; revisit after a day]; (d) hysteresis 0.01 [no]; (e) `take_tilted_ref` vs `take_enabled` false [take_tilted_ref; `take_enabled` false is
the no-code alternative until SIG answers]; (f) T2.5 passive pair unwind [your call: neutral in 3 h, frees the 7.3k Senate set];
(g) ask SIG about end valuation [yes; T2.3 waits for it]; (h) a snapshot each morning, `python analysis/poly_bias/snapshot02b.py <dir>`
[yes: the one number to watch is the hourly tilt].
**Suites (23 files, all green):** test_mm_bot 600, test_live_sim_marks 110, test_strategy 114, test_turnover 94, test_write_savers 75,
test_hold_target 71, test_fast_unload 61, test_pair_passive 57, test_tilt_rampin 54, test_mark_frag 52, test_reduce_book 49, test_tilt 48,
test_ops_liq 39, test_recorder_refill 37, test_ref_prices 37, test_behind_best 36, test_tilt_limit 33, test_take_tilted 31, test_carry_ramp
29, test_reduce_book_scope 26, test_gap_shrink 25, test_stress 20 + STRESS_LADDER=1 20/20 (0 duplicates); py_compile under Python 3.10
(no except*, Self, tomllib). Two red-team passes (opus): first 0 high / 6 medium / 6 low, second 0 high / 2 medium / 5 low; every code
item fixed, the two documentation items are in the rollout rules.
**Simulation spent:** ~440 CPU-minutes (400 seed-runs, 1,500 seed-hours) (the 120 cap was lifted by the owner at 20:30 UTC); every configuration run once (cached per
seed; the cache is `tests/live_sim_round5_cache.jsonl`).

## Package 4 (READY, Finisher, 2 Oct evening; branch `claude/finisher-package4`, draft PR #6): Package 3 final + 28/60 defaults + ladder fixes + write savers (all new features OFF). Code deploy (handover restart).
**What changed against Package 3 final (439ac54):**
- Defaults `writes_per_minute` 28, `writes_per_minute_max` 28, `burst_cycle_seconds` 60 (were 45 / 50 / 20): the owner's live overrides become the code
  defaults (2 Oct live: 4 x 429 while the budget climbed to 36-50/min, 0 at 28; normal cycles take 20-30 s, so the 20 s trigger fired with 0.4 s writes).
- Ladder (`ladder_enabled`, still OFF): the Reviewer fixes L1-L4 (team commit a77c8cb) plus 4 bugs found in them, each with a test that failed before:
  R4a (high) a new level could exceed its keep limit when the position limit binds -> placed and pulled every cycle; R4b (high) the per-order cash cap
  was also a keep limit -> a small account-value dip pulled every cash-capped level on every ladder market at once; R4c (medium) min_quote_life could
  keep a duplicate at a kept level; R4d (medium) a ladder-only cancel of a whole exchange counted toward `pulls_cancel_all_over`.
- Write savers (new settings, all OFF): `no_chase_enabled` (+ `no_chase_tolerance_ticks` 2, `no_chase_fv_epsilon` 0.0025);
  `ttl_tiers_enabled` (+ `order_ttl_busy` 3600, `order_ttl_quiet` 6000, `ttl_jitter_frac` 0.2, `ttl_busy_size_frac` 0.01; hard cap 7200 s);
  `ttl_expire_as_cancel` (+ `ttl_expire_grace_seconds` 5). Mirrored in `tests/strategy_sim.plan_changes`; `tests/test_write_savers.py` pins the two to
  one rule (1,152-case grid).
- Red-team fixes (opus review of the merged diff: nothing high with every flag off; Package 4 with flags off = Package 3 + 28/60 defaults):
  no-chase never applies in reduce-only, the flatten window, right after a Polymarket move or in a new fast-unload window; the self-test
  checks the longest tier TTL when `ttl_tiers_enabled` is on and switches the tiers off if the exchange refuses it (falls back to order_ttl,
  then 10 min); the tier-TTL validation runs only with tiers on, and a bad tier set refuses `ttl_tiers_enabled` too. Note: `ladder_move` is 2c
  (was 1c; part of L3, no effect while the ladder is off). Known, unfixed (flags off: inert): expiry-as-cancel leaves a side empty ~20-30 s
  per expiry and its 5 s grace trusts our clock; tiered TTLs make the dead-man's switch up to 2 h on quiet markets.
- Simulator: `plan_changes` now models order expiry (base numbers not comparable with rounds <= 3b); `live_sim` prints per-gate ladder counters (`lg_*`).
**Numbers (SIM_NOTES.md "Round 4"):** savers, 8 seeds x 3 h quiet (base 22.1 writes/min, 6,760 deferred/h): no-chase dP&L -43 ± 62, writes -0.26 ± 0.40
(noise); TTL saver dP&L -43 ± 39, writes **-1.10 ± 0.35**/min. Neither passed clearly; the 16 x 6 confirmation was not run (the real cost is ~14.6
CPU-s per seed-hour, 2.9x the estimate). Ladder gates (2 seeds x 0.5 h): blocked by cash in 81-86% of ladder-market cycles at start capital 0.90/0.80/0.70,
write gate shut 65-76%; 0 ladder shares at 0.90 and 0.80. It needs capital in positions at ~78-80% or less before it places a single level.
**Switch on:** nothing new. Keep every saver and the ladder OFF. Candidate for the next A/B: `ttl_tiers_enabled` + `ttl_expire_as_cancel` (the only clear
write saving), after a 16 x 6 quiet confirmation and after checking the exchange accepts expirationDate up to 2 h.
**Deploy:** `deploy/handover-restart.sh` with this commit (see deploy/RUNBOOK.md). settings_override.json can drop `writes_per_minute`,
`writes_per_minute_max`, `burst_cycle_seconds` (now defaults) or keep them (same values). **Rollback:** handover restart to 439ac54 (Package 3 final);
the overrides keep 28/28/60 there.
**Watch in the first 10 minutes:** as Package 3; status.json write budget 28 and `rate_limited_total` 0; no burst entries on 20-30 s cycles; no
"refused override" alerts; orders' expirationDate still order_ttl (30 min) since the TTL saver is off.
**Suites (Package 4):** test_mm_bot 600, test_write_savers 75 (new), test_strategy 114, test_turnover 94, test_fast_unload 61,
test_mark_frag 52, test_recorder_refill 37, test_ref_prices 37, test_behind_best 36, test_stress 20, and STRESS_LADDER=1 20/20 (0 duplicates); Python 3.11, py_compile on 3.10.
**Not done:** item 4 (turnover control and ceiling 0.5 vs 0.25 in 16 x 6 news) was blocked: the session's permission classifier refused the run.
Ladder P&L runs skipped: the gate diagnosis shows 0 ladder shares at start capital 0.90 and 0.80, so a P&L run would measure an idle ladder.

## HANDOFF (read this if you are picking the work up)
**Package 5 handoff (3 Oct 03:00 UTC, Finisher 2b).** Live: Package 3 final (439ac54) since 2 Oct 16:26 with the six live overrides; Package 4 (c29f762) not
deployed. Deploy candidate: **Package 5 (the "READY: Package 5" commit on `claude/finisher-package5`, PR #7)**, which contains Package 4. Read the
"Package 5" section above (diagnosis, results, rollout rules) and `deploy/package5/README.md` (staged override files). The simulator yardstick is now
`pnl_lag` / `pnl_liq` in `tests/live_sim.py` with the world knobs `_rival_anchor`, `_world_tilt*`, `_bg_wc`; every Round 5 seed result is in
`tests/live_sim_round5_cache.jsonl`. The second cycle (Package 6: exits that keep quoting in reduce-only, a backstop soft band) is on
`claude/finisher-package6` with its own START_HERE section when it exists. Earlier handoffs follow.
**State at 14:35 UTC, 2 Oct.** Live: Package 2 (83f6d45) since 11:21:57 with settings_override `{"arb_two_sided": false, "worst_case_backstop_frac": 0.8}`
plus the stop-gap keys below. Deploy candidate: **Package 3 (HEAD, "READY: Package 3")**; it includes 2.1-2.3.
**Where everything is** (all on this branch, PR #5): `START_HERE.md` (this file: packages, parameter table, owner flags), `PLAN.md`, `deploy/RUNBOOK.md`
(parameter-only, handover code deploy, rollback, emergency), `DATA_REPORT_2.md` + `analysis/*.py` (day-two analysis, valuation rule, outsider races,
rival floors, turnover, mark fragility, mark rule, follow-ups), `ideas/IDEAS_ROUND1..3.md` + `analysis/explorer_r2/` (idea rounds with the data facts
behind them), `SIM_NOTES.md` (simulator calibration and every sweep), `tests/strategy_sim.py` (crowded-book simulator), `tests/live_sim.py` +
`tests/live_start.json` (simulator from the real book), `tests/scenario.py` (`ceiling` reproduces the 2-5 min cycle incident), the Builder's
`START_HERE_BUILDER.md` and Run A's `ENGINEERING_NOTES.md`.
**Stop-gap overrides to lift once 2.3 is live**: `never_defer_unsafe` true and `burst_protection` true are safe again; keep `writes_per_minute` 28-30 and
`writes_per_minute_max` 30 unless `rate_limited_total` stays 0 for an hour (the exchange's write limit looks like ~30/min for the whole bot).
**Owner decisions still open** (with the team's recommendation): (1) `worst_case_backstop_frac` 0.8 vs 0.69: keep 0.8 until the sum of maxima is below
60k; (2) `arb_two_sided`: switch on after 2.3 (guard: liquid Polymarket on every leg, raw sum >= 0.99; expect 0 fills in outsider races); (3) election
day (Run B W1): markets close 4 Nov 00:00 UTC, one hour after the first polls close and before returns, so there is no "take stale quotes on returns"
window inside trading; the team recommends flattening from 24 h before, exempting complete sets only if SIG confirms settlement at the outcome, and no
directional taking; (4) ask SIG: the write limit (30/min per account? a batch = 1?), Smart Score definition, end valuation of unresolved positions, whether a
429 pauses reads; (5) Gamma rate limit before a Polymarket refresh below 5 s (not needed on the data: only 83 moves >= 1c in 16 h and adverse selection ~0,
so the WebSocket idea is parked).
**What is left (in value order, from ideas/IDEAS_ROUND3.md and the Strategist)**: no 0.5c chase when fair value and inventory are unchanged (24% of
all quote changes were pure rival chases; fresh quotes earn less than resting ones); a quiet-tier cancel-less regime (wide, long-lived quotes: 68% of
decisions for ~650 of edge); TTL refresh is a ~20% write tax (order_ttl 1800 / refresh 180; tiered TTL and expiry-as-cancel); headline re-quote only on a
fair-value move or fill; a Gamma-outage guard for quoting; a daily scorecard from the recorder (positions' currentPrice, account marks: `analysis/mark_rule.py`
pins the valuation rule once a day of data exists); run `analysis/rival_floor.py --mode sweep_only` on the server's market_data.sqlite to produce
market_edge.json (the loader is ON and inert without the file); the ladder only after its write-churn fixes and a dry-run write count.
**How the suites run**: `for t in test_mm_bot test_ref_prices test_strategy test_recorder_refill test_fast_unload test_turnover test_mark_frag
test_behind_best test_stress; do python tests/$t.py | tail -1; done` (also `STRESS_LADDER=1 python tests/test_stress.py`); Python 3.10 is required on
the server, everything here was run on 3.11 and a 3.10 venv.

## Carried over from the Builder and Run A (open items)
- Owner decisions still open: R7 loosened reduce-only (live with the 69% backstop); rule questions for SIG (end valuation of
  unresolved positions, batch = 1 or N writes, Smart Score, position limits, wording on many resting levels).
- Not built yet: R3 depth ladder; rival analysis from recorder data; T2-T12, W1-W7; WebSocket prices; Polymarket refresh < 5 s.
- Run A: owner to confirm the Gamma rate limit before a 2 s refresh; confirm batch counting with SIG.

## Opening audit of the live code (Reviewer, 09:05 UTC): main...claude/live-2026-10-02b
No order-duplication or crash path found in the parallel-write machinery. Findings, being fixed for Package 1:
| # | Sev | Where | Finding |
|---|---|---|---|
| 1 | HIGH | Api pool (mm_bot.py ~625) | pool_maxsize 4 while up to 8 threads hit the host: fresh TLS connection per overflow, inflating write times that feed burst detection (the live "pool size 4" warning) |
| 2 | HIGH | positions 409 fallback (~1997) | unbounded: inventory frozen while fills land, so limits, skew, party delta and risk run on stale inventory |
| 3 | MED-HIGH | reduce-only (~2086, ~2781) | no hysteresis; each flip pulls ~72 orders bypassing the write budget, starving every other write; flip back re-places them |
| 4 | MED | burst mode (~2728) | the cycle-time trigger counts our own throttle sleeps: the first cycle after a restart (~21 s) trips burst for 2 min |
| 5 | MED | burst half-size (~2667/~1459) | placed size int(size*0.5) vs check size*0.5 <= qty: a 375 quote is cancelled and replaced every cycle in burst |
| 6 | MED | deploy/handover-restart.sh | if the old bot needs > 90 s to exit, the script exits 1 and never starts the service: no bot, orders rest unmanaged |
| 7-11 | LOW | various | duplicate thin_book_prices; selftest_eid never cleared; throttle window ordering; reprices counted when planned; on_cycle_error ignores a partial cancel-all |
Checked and found correct: thread-safety of shared state, order recovery vs duplicates, cancel-all failure path, handover adoption, overrides timing, R2 skew sign, R7 math, R5/R5b guards. Diff is Python 3.10-clean.

## Owner update, 09:45 UTC (live data; drives Packages 2-3)
- **No fees.** Realised gains (cash + cost basis - 100k) about +1,916; the exchange's P&L (+1,287) = realised + unrealised (-640) at its own
  valuation price. Positions are marked at an off-grid `currentPrice` (e.g. 0.7734, 0.4794): an average of recent trades, not the book.
  Our positions are worth +577 more at book mid and +2,657 more at our fair value than at the exchange's marks (largest: Rep Florida
  Senate 0.7734 vs mid 0.8575). Rank and Smart Score use the exchange's figure. Day one's 1,180 gap was this valuation.
  -> Analyst: pin down the rule (trade VWAP? last-N? window?) as far as the data allows; use it in risk and rank accounting only.
  Never trade to move a valuation price.
- **Inventory is held too long and too much.** 164 positions, 126,706 shares; median held share 7.2 h old; 54% bought > 3 h ago, 19% > 12 h
  ago. About 90.5k of 101k is tied up in positions (11k cash), which limits new quoting. Biggest: long 10,834 Dem U.S. House AND short
  9,396 Rep U.S. House (the same bet twice, ~20k shares on Dems winning the House, oldest lot 16.6 h); 7,335 each of Dem and Rep U.S.
  Senate (10-15 h); RI, NH, TN-05 at 2.8-3.8k shares (11-13 h).
  -> Top priority: inventory turnover and capital use. In flight: Engineer 6 (race-netted limits and sizing, age-based skew, a capital
  ceiling on positions, all behind settings), Engineer 5 (pair unwinder: sells a long pair when bids sum >= 1, frees ~17k), the
  Strategist's sweeps of R8 headline sizes, R2 strength, age skew, capital ceiling and fast unload with rival bots.

### Package 2 (READY 11:05 UTC): inventory turnover, capital use, coverage. Code deploy (handover restart). Commit "READY: Package 2".
Strategy package, every change behind a setting (all live-overridable). Red-teamed by the Reviewer; its fixes are in.
Suites: test_mm_bot 463, test_ref_prices 37, test_strategy 101, test_recorder_refill 37, test_stress 20; green on Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`; `.gitignore` (position_lots.json). The bot creates
`position_lots.json` and the new recorder tables itself. Includes Package 1.

| Change (default) | Mechanism | Evidence | Settings |
|---|---|---|---|
| **Pair unwinder (ON)** | we hold YES on every leg of a race (n complete sets): when other traders' bids sum to >= 1.005, sell up to n sets at the bids (10 s orders); mirror for short sets at asks <= 0.995. Runs in reduce-only too (it only shrinks). | 17,675 pair-shares held (7,335 x 2 U.S. Senate control): ~17% of the account earning nothing; U.S. Senate bid-sum >= 1.000 in 16% of snapshots. Riskless by construction; worth ~+50 in profit and ~17k of freed capital over days | `pair_unwind_enabled`, `_min_profit` 0.005, `_max_frac` 0.01, `_cooldown_seconds` 30 |
| **Buy-side arbitrage (ON, flagged)** | asks of all legs sum to <= 0.985 AND our fair values sum to >= 0.9925 -> buy every leg (a set pays 1); the unwinder sells it back when bids reach 1.005 | ask-sum < 0.98 in 853 race-minutes/day (the live sell-side arb at 3c fired 0 times). Upper-end estimate +600-1,200/day; snapshot sums may include stale prices, so watch the fill logs | `arb_two_sided`, `arb_min_profit_buy` 0.015, `arb_buy_min_sum` 0.90 |
| **Race-netted limits (ON)** | position limits and the Kelly cap also apply to the race-netted exposure on the side that grows it (a short Rep leg counts toward the Dem leg) | long 10,834 Dem House AND short 9,396 Rep House = the same ~20k bet twice under per-market limits of ~10k | `limits_use_race_net` |
| **Age skew (ON)** | a position's share-weighted age (FIFO lots, persisted; rebuilt from fills.csv on first start) adds 0.25c of skew per hour held beyond 1 h, max 2c, toward unloading; never through fair | median held share 7.2 h old; 54% > 3 h; round trips were the whole realised profit, open lots gave their edge back | `skew_age_enabled`, `skew_age_after_hours` 1, `skew_age_per_hour` 0.0025, `skew_age_max` 0.02 |
| **Capital ceiling (ON, flagged)** | when capital in positions > 75% of the account (the API's totalMarketValue, else our own valuation), every ADDING side quotes at a quarter size; reducing sides unchanged; off again below 70% | 90.5k of 101k tied up, 11k cash. **The ceiling will be ON at deploy**: adding sides shrink to 25% until positions turn over. Deliberate (owner's priority); lower/raise `capital_ceiling_adding_size_factor` live if coverage or fills drop too far | `capital_in_positions_max_frac` 0.75, `capital_ceiling_adding_size_factor` 0.25 |
| **Thin-book pricing from bulk tops + start-up priming (ON)** | R5 may price from the bulk best bid/ask (3 requests for all markets) when no fresh book exists; our own order at the top blanks that side; on a (re)start books download first (60/cycle, 45/min) | after the 08:08 restart coverage rose 30 -> 101 in 6 min with 138 eligible markets waiting; scenario cold start: quoted at 2 min 17% -> 80-85% | `ref_only_use_tops`, `tops_max_age` 120, `startup_books_first`, `startup_prime_*` |
| **Same-side refill cooldown (ON)** | 2 adding fills (>= 200 sh) on one side within 60 s -> that side withheld 30 s; reducing side exempt | 3rd+ fills in same-side runs: -1.15c on 81k shares (-936); mostly day one's skew bug, so this is cheap insurance | `refill_cooldown_*` |
| **Mark recorder (ON)** | `positions` (quantity, currentPrice, all numeric fields) and `account_marks` tables, no extra requests; `analysis/mark_rule.py` fits the exchange's valuation rule | the exchange marks at a trade average (30-min VWAP or last ~5 trades fit); pins the rule for risk and rank accounting | `record_positions` |
| **Favourite-longshot bias (OFF)** | wider, smaller "bad side" below 20c / above 80c | the apparent -0.5c was day one's skew quoting through fair value, not longshot flow (DATA_REPORT_2 §10c) | `fl_bias_enabled` False |
| **Reviewer fixes** | capital ceiling factor 0 -> 0.25; buy-side arb fair-value-sum guard; unwinder margin/cap; zero-fill 4x cooldown; priming reserve; carried tops expire; positions rows only on quantity/mark changes | red-team of the merged diff | - |

Parameter-only change shipped with this package (optional, revertible in 30 s via settings_override.json): `{"ref_weight": 0.8}`.
Simulator (128 seeds, recalibrated to day two): ref_weight 0.85 +17 ± 6/h quiet, +20 ± 8/h news on a +499/h base, peak worst case +0.35k; the simulator
treats Polymarket as the truth, so this is an upper bound; 0.8 is the conservative step (medium confidence). Everything else the Strategist swept stays:
improve_ticks 0 loses -17 ± 6 (keep pennying), kelly 0.15 loses -22 ± 6, min_edge/max_half_spread/skew/size/write/churn changes are noise.

Deploy: `deploy/RUNBOOK.md` section B (handover restart). Rollback: section C, or switch any single feature off via settings_override.json.
Watch in the first 10 minutes: (1) status.json `capital_in_positions_frac` and `capital_ceiling_active`: active is expected; if resting orders fall by
more than 40%, set `capital_ceiling_adding_size_factor` 0.5 live; (2) "PAIR UNWIND" / "ARBITRAGE ... buy" lines and the following "every leg filled N":
N = 0 repeating on one race, or a buy in a two-leg race whose fair values do not sum to ~1 -> set `arb_two_sided` false; (3) `markets_priced_from_tops`
and `books_loaded` climbing during priming, `rate_limited_total` 0; (4) "refill cooldown" lines rare and never on a side whose position has the
opposite sign; (5) `position_lots.json` written, `portfolio_age_hours` plausible (not 0 with positions, not > 48 h), quote lines showing "age Nh".
Owner flags: pair unwinder, buy-side arbitrage and the capital ceiling are new behaviour ON by default (data evidence, riskless or strictly
risk-reducing by construction); ref_weight 0.8 is a parameter step on simulator evidence only.

### Package 2.1 (READY 12:05 UTC): HOT-FIX for the 11:22 reduce-only episode. Code deploy (handover restart). Commit "READY: Package 2.1".
Behaviour-identical to the live Package 2 except the two fixes below (the not-yet-reviewed fast unload and reduce-join features are
in the code but OFF: `fast_unload_enabled` False, `reduce_join_best` False; the per-market edge `market_edge_enabled` is OFF too).
Suites: test_mm_bot 503, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_stress 20; Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (rival_floor.py, new), `deploy/RUNBOOK.md`.

| Fix | What happened live | Change |
|---|---|---|
| Risk fair-value fallback (`Bot.risk_fv`) | Rep U.S. House (short 9,396) unpriced after the restart: `or 0.5` made it a coin flip, risk 20.6k -> 31k, reduce-only 11:22-11:31, Dem House sold at 0.915 vs 0.92 fair | a HELD market with no fair value uses, in order: the liquid Polymarket reference; 1 - the other leg's fair value (two-leg race); the exchange's own mark (currentPrice from the positions read); the last fair value; only then 0.5. Logged once per market and source. Used by settlement_risk, total_worst_case, the capital-in-positions valuation and the unwind safety check |
| Priming held markets first (Engineer 13) | priming ended at 192 s with 128/237 books; Rep House's adopted bid sat at the best, so the bulk top was blanked and R5 could not price it until its book arrived, behind ~100 others | missing books of HELD markets download first (biggest |position| x price), then markets with resting orders, then tops-priced, then by volume; priming never ends while a held market lacks a book (hard cap `startup_prime_held_max_seconds` 900); a held market unpriced for `unpriced_held_warn_cycles` (5) logs one "UNPRICED held market" WARNING with the reason |

**Stop-gap settings for the 11:31-11:50 slow cycles (2-5 min; 20 silent 429s)**, via settings_override.json, until Package 2.2 ships
(Engineer 14 is reproducing it; the lead's reading: the capital ceiling x0.25 and burst x0.5 shrank every adding quote, so every resting
adding order counted as "oversized" = unsafe, `never_defer_unsafe` gave all those reprices pull priority past the request budget, the
flood hit the exchange's real write limit, each 429 (Retry-After 60 s) paused EVERY request, and the 429 is only logged when no pause
was already in force):
`{"arb_two_sided": false, "never_defer_unsafe": false, "capital_ceiling_adding_size_factor": 0.5, "burst_protection": false, "writes_per_minute": 30, "writes_per_minute_max": 30}`
Watch: summary-line interval back to ~30 s; "request budget: N deferred" shrinking; orders_resting steady (not 70 -> 281 -> 84); status rate_limited_total not rising.
Re-enable `burst_protection` once cycles are back to ~30 s. `capital_ceiling_adding_size_factor` 0.5 is also the Strategist's recommendation
(factor 0 was a cliff: -131/h once the ceiling binds; 0.5 recovers ~70%); Package 3 makes 0.5 the default.

**Backstop recommendation** (`worst_case_backstop_frac`, owner set 0.8 at 11:46): keep 0.8 for now. The sum-of-maxima (69k) is dominated by the
doubled House legs and many small races and ignores the hedged sets; R7's correlated risk (20.6k vs the 30.5k cap) is the operative limit. The
pair unwinder and the capital ceiling should bring the sum down over the next days; revisit to 0.69 when it is below 60k. This loosens a safety
limit: the owner's call, flagged.

**ref_weight 0.8: withdrawn.** The Strategist's 3-hour runs reverse the 1-hour gain (ref_weight 0.85: -23 ± 24 / -46 ± 28 per 3 h at the lagged mark).
Keep 0.7.

### Package 2.2 (READY 12:40 UTC): HOT-FIX for the 2-5 minute cycles. Code deploy (handover restart). Commit "READY: Package 2.2".
Includes 2.1. Behaviour against the live Package 2 changes only by the fixes below; the Package 3 features present in the code stay OFF
(`fast_unload_enabled`, `reduce_join_best`, `turnover_control_enabled`, `mark_frag_enabled`, `market_edge_enabled` all False; the R3 ladder is not in 2.2's code).
Suites: test_mm_bot 536, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_turnover 72, test_mark_frag 52,
test_stress 20; green on Python 3.11 and 3.10. Reviewer red-team in progress; anything it finds ships as 2.3.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`.

Root cause (Engineer 14, reproduced in `tests/scenario.py ceiling`: 236 markets, full-size orders resting, the capital ceiling switching on, 45 writes/min,
an exchange that answers 429 past 30 writes/min): (1) the ceiling's x0.25 (and burst's x0.5) shrank the WANTED size, so every resting full-size order
counted as "bigger than allowed" = unsafe; `never_defer_unsafe` gave those ~100-280 reprices per cycle pull priority past the request budget; (2) the
flood exceeded the exchange's real write limit; each 429 (Retry-After 60) paused EVERY request for 60 s, and the 429 was logged only when no pause was
already in force (20 silent 429s live); the main thread waited on the positions/book reads (0% CPU, futex_wait). Before the fix: 4 cycles in 6.5 min,
median 139 s, max 278 s. After: median 5.2 s, every 429 logged, the write budget settles at 26/min.

| Fix | Setting (default) |
|---|---|
| A resting order is never unsafe only because a size FACTOR (ceiling, fl bias, turnover) shrank the wanted size: `Quote.bid_max/ask_max` (the size before factors, within position/cash/risk limits) is the ceiling in plan_change; orders beyond the limit price, on unwanted sides or above a LIMIT stay urgent | - |
| Urgent writes capped per cycle, price-unsafe orders and pulls first | `urgent_writes_per_cycle` 20 |
| A main-thread write (take, arbitrage) that would wait longer than write_wait_seconds + margin is not sent ("429 WRITE_BUDGET_WAIT"); takes/arbitrage skipped while the budget is busy; cycles skipped during a 429 pause | `main_write_wait_margin` 2, `pause_skip_cycles` True |
| Every 429 logged (method, path, Retry-After, pause already on); a 429 during a pause moves its end to Retry-After from now (not additive); budgets cut once per pause; status.json `paused_until`, `pause_seconds_left`, `pauses_total`, `seconds_since_cycle`, `last_cycle_phases`; the summary line ends with the last cycle's phase times (reads, fair values, fills/risk/takes, decide, send, wait) | - |
| Watchdog thread: alert after 180 s without a completed cycle; at 600 s dump every thread's stack, cancel-all (at most 20 s), exit 5 (systemd restarts) | `watchdog_alert_seconds` 180, `watchdog_exit_seconds` 600 (0 = off), `watchdog_cancel_seconds` 20 |
| Buy-side arbitrage guard: every leg needs a LIQUID Polymarket price and the raw prices must add up to >= 0.99 (book fair values are normalised to 1 and cannot see an outsider). `analysis/outsider_races.py` lists the races: South Dakota Senate 0.917, Idaho Senate 0.945, Maryland Governor 0.979, a dozen House races 0.980-0.988, three 3-leg races (RI Gov, NE Sen, MT Sen), four with no complete reading (Alaska Sen/Gov, CA-22, CA Gov) | `arb_buy_min_ref_sum` 0.99. The owner may now set `arb_two_sided` true via settings_override |

After deploying 2.2 the stop-gap overrides can go back to defaults one at a time: `never_defer_unsafe` true (now safe), `burst_protection` true, `writes_per_minute`
45 / `writes_per_minute_max` 50 only if `rate_limited_total` stays 0 for an hour at 30 (the 429s were real: the exchange's write limit looks like ~30/min).
Keep `capital_ceiling_adding_size_factor` 0.5. Watch: summary-line gaps ~30 s; "request budget: N deferred" < 30; `rate_limited_total` flat; `orders_resting` steady;
"RATE LIMITED (429)" lines (now one per 429) absent; the watchdog never alerts.

### Package 2.3 (READY 13:10 UTC): Reviewer fixes on 2.2. Code deploy (handover restart). Commit "READY: Package 2.3". Supersedes 2.2.
Includes 2.1 and 2.2. Reviewer verdict on 2.2 was "ship with fixes"; the fixes:
| Fix | Why |
|---|---|
| The watchdog clock is NOT reset by a cycle skipped during a 429 pause | a chain of pauses (the incident's shape) never reached the 180 s alert / 600 s restart |
| The urgent-write cap skips further URGENT changes instead of deferring everything behind them | with `break`, a news move making > 20 orders price-unsafe stopped re-quoting the whole book for several cycles |
| A "WRITE_BUDGET_WAIT" on an arbitrage/unwind batch or a take is treated as "not sent": no 90 s pending hold, no "check positions" alert; takes check the budget BEFORE cancelling our own quote; arbitrage needs 2n+1 writes | silent loss of takes/arbs and a false alert |
| `mark_frag_total_max_cash` 2,000 -> 0 (per-position cap only, still OFF) | the total cap would withdraw every adding side at once when noise crossed 2,000 (1,186 today) |
| Package 3 features (still OFF) corrected per review: reduce-join joins only a rival INSIDE our normal quote, floor 1c; fast unload at fair ± 1c for 180 s, only the first placement urgent; turnover control cuts the wanted size only (resting orders within the normal limit stay), hysteresis 50/75 sh/h with a 30-min state life, outage gaps unobserved; behind-the-best sizing added (OFF) | so they can be switched on in Package 3 |
Suites: test_mm_bot 543, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 61, test_turnover 94, test_mark_frag 52,
test_behind_best 36, test_stress 20; Python 3.11 and 3.10. Watch after deploy (Reviewer): `rate_limited_total` / `pauses_total` flat at writes 30;
`last_cycle_seconds` median < 20 s; no run of "urgent writes capped" lines; `write_budget_wait_total` / `takes_skipped_budget` small; `orders_resting`
and `locked_in_orders` steady under the ceiling; no WATCHDOG alert.

### Package 3 (in preparation; merged on the branch, every feature OFF): inventory turnover and sweep capture
Candidates (all live-overridable): `fast_unload_enabled` (after a quote fill with >= 2c edge, the reducing side quotes at fair ± 1c for 180 s at the
filled size); `reduce_join_best` (the reducing side joins a rival resting inside our normal quote, never closer than 1c to fair);
`turnover_control_enabled` (a market with < 50 sh/h of flow over 6 h where we hold >= 100 shares: adding side x0.25 and half its position limit,
hysteresis 50/75, 30-min state life); `behind_best_size_enabled` (an adding quote 2+ ticks behind the best other price at half size: cuts cash
locked in orders ~14% in the simulator, P&L neutral-to-slightly-negative); `mark_frag_enabled` (per-position cap = 100 cash / sd of the 10-min
mid step; on the snapshot 3 positions would be capped: both RI Senate legs and Dem U.S. Senate; total noise 1,186 per step);
`market_edge_enabled` (per-market min_edge from `analysis/rival_floor.py --mode sweep_only`, needs live recorder data); `ladder_enabled`
(R3 resting depth ladder at 1.5/2.5/3.5c, sizes 1/2/3x, headline 2/4/6/8c; cash-gated at 10% free cash; writes gate 10; simulator +17 ± 15 quiet /
+37 ± 18 news per hour at 24 seeds, +46-51 ± 10 at 128 seeds in the prototype; writes 12 -> 27 per market-hour, so it needs the write budget
headroom the exchange may not have). Defaults are decided after the Strategist's real-inventory runs and the Reviewer's second pass.
Capital ceiling factor 0.25 -> 0.5 will be Package 3's default (Strategist: factor 0 was a cliff, 0.5 recovers ~70% of the lost P&L).

### Simulator from the real book (Strategist round 3, owner's request): `tests/live_sim.py`
Starts from the real positions and lot ages at 08:14 (`tests/live_start.json`, rebuilt by `tests/live_start_extract.py`; House legs set to the 09:45
figures), 68 real markets (the races of the 40 biggest positions, both legs) plus 3 made-up races with an unlisted outsider, the other 163 markets as a
fixed capital block (90% of the account in positions, ~10k free cash; adding orders need free cash), the real decide / arb_plan / fill hooks, a 30-min
trade-average mark. Usage: `python tests/live_sim.py SEEDS HOURS quiet|news '{settings}' ...`. 16 seeds, 6 h quiet, everything off: P&L +2,990, capital
0.90 -> 0.93 (peak 0.98), peak worst case 55k. Package 2 "all on" vs off: P&L -180 ± 160 (quiet) / -293 ± 110 (news) at Polymarket prices but
+219 ± 180 / +68 ± 97 at the trade-average mark (+597 ± 270 over 12 h), cash freed +8.5k / +9.7k (+13.8k over 12 h), worst case -7.3k / -8.1k
(-10.6k), positions 0.6-2 h younger. Alone: race-netted limits +185 ± 150 (+407 ± 180 at the mark, worst case -5.3k); capital ceiling +251 ± 120;
pair unwind +88 ± 76 (ages -0.85 h); age skew +5 ± 98; arbitrage -54 ± 90; refill cooldown -120 ± 97; fast unload (old 0.5c/300 s) -257 ± 94;
reduce-join (old) -58 ± 61. The outsider races got 0 arbitrage fills. Limits: 68 of 237 markets simulated; buy-side chances ~2x real.

### Package 3 (READY 14:45 UTC): defaults from the real-book simulator and the Reviewer; new features present, most OFF. Code deploy (handover restart). Commit "READY: Package 3".
Includes 2.1-2.3. Changes against 2.3: `capital_ceiling_adding_size_factor` 0.25 -> 0.5 (the ceiling binds live; factor 0 was a cliff of -131/h, 0.5 recovers
~70%); `mark_frag_enabled` OFF (round 3b: -29 ± 52, the cap rarely binds; switch on for rank stability if wanted); `market_edge_enabled` ON (inert until `market_edge.json` exists: run
`python analysis/rival_floor.py --mode sweep_only` on the server's market_data.sqlite and copy the file next to mm_bot.py; the loader refuses bad
entries); `refill_cooldown_enabled` OFF (real-book simulator -120 ± 97; the day-one losses it targeted were the old skew bug). OFF and awaiting evidence
(switch on via settings_override only after a simulator or live A/B result): `fast_unload_enabled`, `reduce_join_best`, `turnover_control_enabled`,
`behind_best_size_enabled`, `ladder_enabled` (the ladder also needs the Reviewer's write-churn fixes L1-L4, listed below, before any live use).
Suites: test_mm_bot 588, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 61, test_turnover 94, test_mark_frag 52,
test_behind_best 36, test_stress 20 (and with STRESS_LADDER=1); Python 3.11 and 3.10.
Watch in the first 10 minutes: as for 2.3, plus status.json `mark_frag_capped_markets` (expect ~3) and `mark_frag_top`; `market_edge_markets`
(0 until the file exists); adding-side sizes at half under the ceiling (not a quarter).
Round 3b (6 h quiet, 16 seeds, from the real book): ceiling factor 0.5 vs 0.25 +262 ± 100; arb_buy_min_ref_sum 0.99 vs 0.8: 0.8 would lose -898 ± 94
buying 23.8k outsider shares; fast unload -165 ± 110; reduce-join -48 ± 81; behind-best -99 ± 83; mark cap -29 ± 52; ladder -5 ± 49 (idle at 10k cash);
refill cooldown off -14 ± 94; turnover control untested. Engineer 10's ladder Reviewer fixes L1-L4 are now MERGED (596 tests; ladder still OFF; `ladder_move` default 2c). Unfinished at wrap-up: nothing of the ladder's Reviewer fixes L1-L4 (hair-trigger urgent pulls one tick
behind the touch -> stale not urgent; ladder-only pulls excluded from the cancel-all count; 1-tick tolerance, re-anchor hysteresis at 2c and
min_quote_life for ladder orders; per-order cash cap in ladder_caps) and the Strategist's round-3b runs (new fast unload, new reduce-join, turnover,
behind-best, mark cap, the real ladder at 10k free cash, refill cooldown off vs on, ceiling 0.25 vs 0.5 on `tests/live_sim.py`). Both are specified
above and in SIM_NOTES.md; a new session can redo them from this branch.

## Parameter changes (cumulative against live)
| Setting | Live | New | Evidence | Expected effect |
|---|---|---|---|---|
| writes_per_minute | 30 | 45 (max 50, AIMD) | DATA_REPORT_2 §8; Engineer 1's table: no 429 at >= 40 writes/min for 63 minutes | no deferrals in normal cycles; faster repricing |
| batch_size | 20 | 10 | 273 of 417 409s were 20-order batches | fewer 409s and lost replies |
| pool_maxsize (code) | 4 | 10 | pool warnings; 8 threads | no connection churn, truer write timings |
| positions_stale (new) | unbounded | 120 s | audit #2 | quotes pulled instead of trading on frozen inventory |
| reduce_only_hysteresis (new) | 0 | 0.03 | audit #3 | no reduce-only flapping |
| pulls_cancel_all_over (new) | - | 25 | audit #3 | one write instead of ~72 on a reduce-only entry |
| burst_startup_grace_seconds (new) | - | 90 | 08:08 restart tripped burst | normal sizes after a restart |
| take_ref_max_age_seconds (new) | - | 30 | Engineer 1 task 4 | no takes on a stale Polymarket price |
| handover_exit_max_seconds (new) | - | 150 | audit #6 | a handover never hangs |
| ref_weight | 0.7 | 0.7 (0.8 withdrawn) | 1 h: +17 ± 6; 3 h at the lagged mark: -23 ± 24 / -46 ± 28 | none |
| capital_in_positions_max_frac (new) | - | 0.75 (factor 0.25) | owner: 90% in positions | adding sides at quarter size until positions turn over |
| skew_age_* (new) | - | 0.25c/h after 1 h, max 2c | median age 7.2 h | faster unloading of old positions |
| pair_unwind_* / arb_two_sided (new) | - | on | 17.7k paired capital; ask-sum < 0.98 in 2.5% of race-minutes | capital freed; small riskless gains |
| ref_only_use_tops / startup_books_first (new) | - | on | coverage 30 -> 101 in 6 min after restart | ~190 priced within 2 min of a restart |
| refill_cooldown_* (new) | - | on | -936 on 3rd+ same-side fills | fewer walks against us |
| fl_bias_enabled (new) | - | off | §10c: a skew artefact | none |
| capital_ceiling_adding_size_factor | 0.25 (P2) | 0.5 | real-book simulator: factor 0 a cliff, 0.5 recovers ~70% | adding sides at half size under the ceiling |
| mark_frag_enabled (new) | - | off | round 3b -29 ± 52, rarely binds | none |
| market_edge_enabled (new) | - | on (inert without market_edge.json) | Reviewer: only widens, cheap | per-market floors once the file exists |
| refill_cooldown_enabled | on (P2) | off | real-book simulator -120 ± 97 | none expected |

## Packages

### Package 1 (READY 10:10 UTC): audit and ops fixes. Code deploy (handover restart). Commit "READY: Package 1".
Safe, no strategy change. Fixes the Reviewer's audit findings on the live code and the issues seen after the 08:08 deploy.
Suites: test_mm_bot 381, test_ref_prices 37, test_strategy 38, test_stress 20, green on Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `ref_prices.py`, `deploy/handover-restart.sh`, `deploy/RUNBOOK.md`, `tests/` (copy them all; nothing else).

| Change | Evidence | Setting (default) |
|---|---|---|
| HTTP pool 4 -> 10 connections (parallel_requests + parallel_writes + 4); Polymarket pool parallel_fetches + 2 | live "pool size 4" warnings: 8 threads on 4 connections, each overflow a fresh TLS handshake that inflated write times | - |
| Write budget 30 -> 45/min, growing +1 per 60 clean writes to 50; any 429 cuts it to 75% (a read 429 only when it has grown) | 80 req/min ran 13 h without a 429; 63 minutes at >= 40 writes had none; the 6 429s followed bursts of 33-53 writes; the 08:09-08:12 deferrals were the 30/min budget | `writes_per_minute` 45, `writes_per_minute_max` 50, `write_budget_cut` 0.75 |
| Start-up keeps writes at 30/min while the first books download | book downloads were starved by writes after the restart | `startup_writes_per_minute` 30 |
| Reprices that remove an UNSAFE order (beyond its limit, oversized, unwanted side) are as urgent as pulls, never deferred | budget deferrals could leave an unsafe order resting | `never_defer_unsafe` True |
| Order batches 20 -> 10 | 273 of 417 409s were full 20-order batches; batches under 20 failed ~5% | `batch_size` 10 |
| Burst mode: the cycle-length trigger is ignored for 90 s after start and while books still load; half-size orders compared at burst size; full-size orders placed before a burst are not "oversized" | the 21 s first cycle tripped burst for 2 min at 08:08; a 375 quote was cancelled and re-placed every cycle | `burst_startup_grace_seconds` 90 |
| Takes need a Polymarket price fresher than 30 s | a failed Polymarket fetch kept a 5-min-old price that counted toward the take confirmation | `take_ref_max_age_seconds` 30 |
| Positions-409 fallback bounded: after 120 s the cycle fails and quotes are pulled after max_failed_cycles | unbounded reuse froze inventory for limits, skew, party delta and risk while fills landed | `positions_stale_max_seconds` 120 (`positions_stale_max_cycles` 0) |
| Reduce-only hysteresis: enter above 30%, leave below 27%; backstop 69% leaves below 66% | risk oscillating at the cap flipped reduce-only, pulling ~72 orders each time past the write budget | `reduce_only_hysteresis` 0.03 |
| More than 25 pure pulls in one cycle -> ONE tournament-wide cancel-all, re-placement next cycle (never in burst mode, never during the self-test) | each reduce-only flip cost ~2.4 min of write budget | `pulls_cancel_all_over` 25 |
| Handover exit capped at 150 s (then logs flushed, exit 0; the next start re-reads orders); the script waits 240 s and falls back to a plain restart | a > 90 s exit left the script exiting 1 with no bot started | `handover_exit_max_seconds` 150 |
| Churn control counts a reprice when its cancel is sent, not when planned | deferred/dropped changes hit churn_max_reprices without a reprice | `churn_count_sent` True |
| on_cycle_error retries the pull-everything when the cancel-all was partial; selftest_eid cleared on pass; duplicate thin_book_prices removed; throttle window kept sorted | hygiene from the audit | - |
| Live-settings whitelist extended: skew_*, improve_ticks, undercut_step_back, ref_only_*, risk_swing_shock, risk_z, worst_case_backstop_frac, order_ttl, refresh_before_expiry, batch_size, kelly_no_edge_frac and all new settings | parameter packages without a restart | - |

Deploy: `deploy/RUNBOOK.md` section B (stage, test with the server venv, back up, copy, `deploy/handover-restart.sh`).
Rollback: section C (copy the backup back, handover restart). Every setting above can also be reverted alone via settings_override.json.
Watch in the first 10 minutes: (1) the "handover:" adopt line: adopted count = orders that were resting, no "clean slate";
(2) status.json: write budget 45/min, rate_limited_total stays 0, requests in the last minute < 80; (3) no "pulls planned ...
one cancel-all instead" unless reduce-only flipped; if it appears, orders_resting recovers next cycle; (4) no "BURST MODE" in
the first 90 s, "request budget: N deferred" small (< 10) after 2 min, no "pool is full"; (5) no "positions unavailable",
"ENTERING reduce-only" or "cycle failed" streaks; orders_resting back to the pre-deploy level within 3 min; priced rising.
