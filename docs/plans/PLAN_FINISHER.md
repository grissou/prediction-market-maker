# PLAN_FINISHER (Finisher run, 2 Oct 2026, branch `claude/finisher-package4`)

Base: team branch HEAD 58adcac (the brief named da1fcc2; since then the team merged Engineer 10's ladder fixes L1-L4 as
a77c8cb, so item 3 is review + finish + measure, not apply). Package 3 final = 439ac54.
All runs on `tests/live_sim.py`. Cost model (owner): ~5 CPU-s per seed-hour -> 8 seeds x 3 h = 2 CPU-min per
configuration; 16 seeds x 6 h = 8 CPU-min per configuration. Machine: 4 cores, SIM_PROCS=4.

Common BASE_JSON (the owner's live overrides): `{"arb_two_sided": false, "worst_case_backstop_frac": 0.8,
"capital_ceiling_adding_size_factor": 0.5, "writes_per_minute": 28, "writes_per_minute_max": 28, "burst_cycle_seconds": 60}`.

## Item 2: write savers (cap 20 CPU-min)
| Run | Seeds x h | Regime | Configs | CPU-min |
|---|---|---|---|---|
| S1 screen | 8 x 3 | quiet | base, (a) no-chase, (b) TTL saver | 6 |
| S1c screen (only if a and b merged, budget left) | 8 x 3 | quiet | base, (c) headline | 4 |
| S2 confirm (passing savers together) | 16 x 6 | quiet | base, combo | 16 |
| S2 news | 8 x 3 | news | base, combo | 4 |
Item 2 total 26 (6 over its cap, paid from item 3's unused 7): the brief asks for quiet AND news; news is cut to 8 x 3
because the savers re-quote on every fair-value move, so news is the lower-risk check. S1c is dropped if over budget.

## Item 3: ladder (cap 20 CPU-min)
| Run | Seeds x h | Configs | CPU-min |
|---|---|---|---|
| Gate diagnosis (which gate binds) | 2 x 0.5 | ladder on at _start_cap 0.90, 0.80, 0.70 | <1 |
| L-P&L @0.90 | 8 x 3 quiet | base(savers on) vs +ladder | 4 |
| L-P&L @0.80 | 8 x 3 quiet | base(savers on) vs +ladder | 4 |
| Spare (only if a gate fix is made after diagnosis) | 8 x 3 | 1 re-measure | 4 |
Total <= 13.

## Item 4: round 3b remainder, news (cap 30 CPU-min)
| Run | Seeds x h | Regime | Configs | CPU-min |
|---|---|---|---|---|
| N1 | 16 x 6 | news | base, turnover_control_enabled, ceiling factor 0.25 (vs 0.5) | 24 |
12 h runs only for a positive result within one standard error: not budgeted; would exceed the cap, so reported instead.

## Rules kept
Never re-run a configuration; every result into SIM_NOTES.md "Round 4"; runs in the background; at a cap, stop and write up.

## Actuals
- Real cost ~14.6 CPU-s per seed-hour (2.9x the estimate): 8 x 3 base + 2 variants = 17.5 CPU-min.
- Item 2: S1 run (17.5) + smoke (~0.5) = ~18 CPU-min; S2 not run (would be ~58 CPU-min at the real cost).
- Item 3: gate diagnosis ~2 CPU-min; P&L runs not run (ladder idle at 0.90 and 0.80, nothing to measure).
- Item 4: 0 CPU-min; the 16 x 6 news run was refused by the session's permission classifier ("Interfere With Workloads").
  At the real cost it would have been ~70 CPU-min, over the 30 cap anyway.

# Package 5 (Finisher 2b, executor, 2 Oct 18:25 UTC): costed run plan (PLAN_POLY_BIAS.md v2 section 5)
Machine: nproc = 1, so CPU-min = wall-min; SIM_PROCS=1. Cost 15 CPU-s per seed-hour: 8 x 3 quiet = 6 CPU-min per configuration.
Judging world ("tilt world"): `_rival_anchor` 1, `_world_tilt` 0.05, `_world_tilt_growth` 0.002; Package 4 defaults + live overrides as BASE.
Base results are cached per (seed, hours, regime, config) in the scratchpad (LIVE_SIM_CACHE), so no configuration runs twice.
| Block | Runs (8 x 3 quiet unless noted) | CPU-min |
|---|---|---|
| Tier 1 re-score, tilt world | base, fast_unload_enabled, reduce_join_best, turnover_control_enabled, ceiling factor 0.25 | 30 |
| Old world base (`_rival_anchor` 0, no tilt; brief: anchor 0 at least for base; also T2.1's old-world comparator) | base | 6 |
| T2.1 / T2.2 | ref_tilt; ref_tilt in old world; ref_tilt + ref_weight 0.5; + 0.35 | 24 |
| Tier 1 A / B | A; B; T2.1 + B; A + B (or T2.1 + A) | 24 |
| T2.4 | on top of the best so far | 6 |
| Confirm best ONE vs base | 6 x 6 news (9 each side) + 6 x 3 quiet tilt growth 0 (4.5 each side) | 27 |
| Reserve | | 3 |
| Total | | 120 |
Split: Tier 1 ~ 54 (45%), Tier 2 ~ 63 (55%). Early stop: a variant whose first 4 seeds put d pnl_liq below -3 SE stops.
Deviation from the brief (nproc 1): sub-agents build and unit-test only; all simulations run here, serially, under this one budget.

## Package 5 actuals (2 Oct 18:25 -> 3 Oct; the owner lifted the 120 CPU-min cap at 20:30 UTC and extended the session to ~08:00 UTC)
- Measured cost: ~7 CPU-min per 8 x 3 configuration (wall = CPU, 1 core), i.e. ~17.5 CPU-s per seed-hour.
- Spent before the cap was lifted: re-score 30 + old-world base 6 + T2.1/T2.2 24 + A/B 24 + T2.1+A, T2.1+T2.4 ~9 (early stop) = ~93.
- After: old-world T2.1 6, pinned world 36, news 6 x 6 x 2 = 18, flat 6 x 3 x 2 = 9, C/T2.5/T2.4@0.25 ~18, 16 x 6 confirm 48,
  ramp-in / first-2-hour / high-tilt / calibrated / X5 / X11 / ladder screens ~60, pinned 16 x 6 x 4 = 96 (see SIM_NOTES Round 5 for each).
- Every configuration ran once: results are cached per (seed, hours, regime, settings) in the scratchpad and re-read for every table.
- Final (3 Oct 03:00 UTC): 400 seed-runs, 1,500 seed-hours, ~440 CPU-minutes (17.5 s per seed-hour measured; 6-h runs ~21 s), plus ~40 lost to two sandbox
  reboots before per-seed caching. Blocks beyond the original plan: pinned world (live backstop) 36 + 16 x 6 x 4 = 108, ramp-in / first-2-hour /
  high-tilt / calibrated 60, X5 / X11 / ladder / ramp-20 28, 16 x 6 free 48. The owner lifted the 120 cap at 20:30 UTC.
