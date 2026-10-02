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
| S2 news | 16 x 6 | news | combo arm rides item 4's news run (shared base) | 8 (counted under item 4) |
Item 2 total 22-26: S1c is dropped if S1 + S2 would exceed the cap; the news arm shares item 4's base to avoid a
second 8-min base.

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
| + savers combo arm (item 2) | 16 x 6 | news | added to N1 if item 2's combo is known in time | +8 |
12 h runs only for a positive result within one standard error: not budgeted; would exceed the cap, so reported instead.

## Rules kept
Never re-run a configuration; every result into SIM_NOTES.md "Round 4"; runs in the background; at a cap, stop and write up.
