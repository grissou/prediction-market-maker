# Package 8 item 3: tilt exits (measurement plan for the executor)
Live 3 Oct 19:56: tilt_s 0.110 (+0.0036/h), tilt_exposure +34.3k, ~356 lost per point; capital 100%, adding factor 0,
reduce-only most cycles, writes deferred. Code (all OFF; tests/test_tilt_exit.py 54): (a) `tilt_exit_priority`,
`tilt_exit_full_size`; (b) `adding_factor_capital_on` + `capital_ceiling_adding_size_factor_resume` 0.5;
(c) `ref_guard_tilted`, `ref_guard_exits`. A "tilt exit" = the side shrinking a position whose pos x (r - c) has
the sign of the total tilt_exposure (live: sign(pos) == sign(r - c)). Nothing here crosses: priority is ordering only;
full size changes the size only (race-net clip, position and cash caps, covered-NO cap still apply); the guard
flags only unblock a side, capped at the position.

## Simulator hooks (tests/live_sim.py, strategy_sim.py)
- metric `tilt_exposure_end` = sum over the sim's markets of pos x (ref_seen - 0.5) at the end (in KEYS).
- (a) the sim's write-budget key gets the 0.5 slot via `Bot.tilt_exit_side` (LiveSim.tilt_exit_head); the sim feeds
  `bot.tilt_exposure` when either (a) flag is on. (b) `bot.update_adding_resume(cap_frac)` each cycle (no-op at 0).
  (c) with `ref_guard_tilted` the sim's tilt estimate is copied into `bot.tilt_s` / `tilt_on_at` before decide.
- Smoke only (1 seed x 0.05 h, all six flags): runs; 3 min is too short to move anything but full size
  (d tilt_exposure_end -22, exit_ratio +49). The (c) feed was added after that smoke (not re-smoked).

## World + base (live overrides), 8 seeds x 3 h quiet, paired
BASE='{"_rival_anchor": 1, "_world_tilt": 0.11, "_world_tilt_growth": 0.0036, "_bg_wc": 39000, "_bg_wc_growth": 40000,
"_bg_wc_decay": 4000, "_start_cap": 1.0, "ref_tilt_enabled": true, "ref_tilt_max": 0.11, "ref_tilt_headline": true,
"reduce_no_as_sell": true, "capital_ceiling_adding_size_factor": 0.0}'
Run: `python tests/live_sim.py 8 3 quiet "$BASE" V1 V2 ...` (LIVE_SIM_CACHE set; SIM_PROCS per the CPU you have).

| # | variant | question |
|---|---|---|
| 1 | `{"tilt_exit_priority": true}` | do exits go out first when deferred (needs deferred_h > 0 in base) |
| 2 | `{"tilt_exit_priority": true, "tilt_exit_full_size": true}` | bigger exits: faster cut, at what pnl_liq |
| 3 | `{"ref_guard_tilted": true}` | House-type exits rest with the guard measured from r' |
| 4 | `{"ref_guard_exits": true}` | the guard never blocks an exit |
| 5 | all four above together | combined |
| 6-8 | `{"adding_factor_capital_on": X, "capital_ceiling_adding_size_factor_resume": 0.5}`, X = 0.90 / 0.95 / 0.98 | (b) threshold sweep |

## What to read (each d vs BASE, ± SE over the 8 paired seeds)
- **d tilt_exposure_end** (the target: more negative = less unpaired tilt; per point of tilt ~1% of it marks the book).
- **d pnl_lag** (exchange-style mark, the judge for rank) and **d pnl_liq** (liquidation both ends); d pnl_mid.
- **writes_pm** (<= live 28/min) and **deferred_h** (priority only helps when the base defers; if base deferred_h is ~0,
  re-run 1-2 with `"writes_per_minute": 20` added to BOTH base and variants to model the live squeeze).
- **mk15_mid** (markout vs the consensus: the guard flags must not turn it negative = informed flow picking off exits).
- Also: exit_ratio, shares, cap_end (capital share), ro_frac, cash_refused_sh (must stay 0), free_min.
- (b): cap_end and ro_frac per threshold, and whether pnl_liq falls as X rises (resuming adds risk at 100% capital).
Decision rule (suggested): ship a flag if d tilt_exposure_end < 0 beyond 2 SE with d pnl_lag and d pnl_liq not below
-1 SE, writes_pm <= 28, mk15_mid not worse by > 0.3c. For (b) pick the highest X meeting the same rule.
