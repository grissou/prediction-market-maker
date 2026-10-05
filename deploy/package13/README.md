# Package 13 - the 20 / 40 / 40 aggressive portfolio, with the momentum sleeve's automation (owner deploys)

Branch `claude/p13-momentum-auto` (on `claude/finisher-package9` f61495e = Package 13 A + B + red team + dry run).
**Deploy = this branch's head (handover restart) + `settings_override.aggressive.json` from this folder** (copy it over
`settings_override.json`: write a temp file and `mv` it; it is read within `overrides_seconds` 30 s). Every Package 13 flag is
OFF in the code: the head with the live file is the live behaviour (pinned byte-identical to f61495e, and that to the pre-Package-13
code, on grids). The file = the live file of 4 Oct (snap04) + the risk settings of the spec's section 7 (`worst_case_backstop_frac`
1.0 = the bug tripwire, `max_worst_case_frac` 0.60, `max_bloc_delta_frac` 0.5, `alloc_mm_reserve` 20000, `mm_risk_reserve_wc`
5000 / `_corr` 4000) + every flag below. It validates whole against OVERRIDABLE (98 keys).

**Rollback:** `deploy/package12/settings_override.stage1b_risk_reserve.json` (every Package 13 flag off). The momentum sleeve's
legs, if any, are then held (no exit runs with `momentum_enabled` off): set `momentum_exit` true first and let it sell, or keep
the sleeve. Code rollback: f61495e (or the live c7c0107).

## What each flag does (all in the staged file)
| setting | file | what it does |
|---|---|---|
| `buckets_enabled` | true | MM 20% / VALUE 40% / MOMENTUM 40% of the account, rebalanced hourly within +-5 pts (`bucket_*_frac`, `bucket_band`): VALUE above 45% is SOLD down lowest edge-held first (IOC at the touch, `bucket_turnover_per_hour` 50k; the hourly sell-down is exempt from the value floor - the spec's answer 1); the momentum target is the sleeve's (with `momentum_auto`: the ramp's target, see below). status.json `buckets`. |
| `aggressive_value` | true | No reduce-only from the risk cap / the backstop below the 1.0 tripwire; party / bloc caps do not block adding; the position fractions replaced by `aggr_max_market_usd` 12k / `aggr_max_race_usd` 15k of collateral (all buckets). The cash gate, the value floor, the kill switch stay. "WARNING aggressive_value on" leads every 2-hourly summary; status `aggressive`. |
| `momentum_enabled` | true | The sleeve's MACHINERY (status, exits, the kill). **Alone it no longer buys** (Package 13 C). |
| `momentum_auto` | **true** | The sleeve is ARMED: it buys only while the tilt trigger is ON (below). |
| `momentum_force` | false | The owner's force-ON: buys to `bucket_mom_frac` x account at once, no trigger, no ramp; the slope flip is suppressed (profit target, date, manual exit and kill still apply). |
| `momentum_exit` | false | The owner's manual exit: the sleeve sold into the bids over `mom_exit_hours` 6, never re-bought (until `momentum_enabled` false then true, or the auto re-arm after a fresh 24 h trigger with the switch back off). |
| `mom_exit_utc` | "" | Optional exit date (an automatic flip). |
| `mom_kill_frac` | 0.75 | Kill: the sleeve's MARK (book mid) < 0.75 x cost for 120 s -> exit, latched "killed" (one alert; reset only by `momentum_enabled` false then true). |
| `tilt_harvest_ladder` | true | Sells the tilt as a MAKER: resting YES asks over longshots (p <= 0.10) / bids under favourites (p >= 0.90) at touch + 0 / 2 / 4 / 6c, $3k a level where the edge per $ >= 8%; never a sleeve market; takes the sleeve's markets over after an exit. status `harvest`, journal "HARVEST fill". |
| `election_night` | true | From 3 Nov 12:00 UTC $20k of cash held back; from 23:00 a race is CALLED when its Polymarket price stays >= 0.98 / <= 0.02 for 10 min; stale tournament quotes on called races are TAKEN (IOC, batched, within the holdback and caps). With `close_override_utc` "2026-11-04T17:00:00Z" and `stop_minutes_before_close` 5 (SIG confirmed). status `election`. |

### The momentum automation (Package 13 C, the owner's 20:30 and 21:20 instructions)
- **The tilt series** (`TiltSlope`): every minute, the cross-section of NON-headline markets with a liquid Polymarket price and an
  OTHER-traders book spread <= `momentum_slope_max_spread` 0.06 (our own orders stripped from every downloaded book; never our
  fills): the slope estimator sum x g / sum x^2 (g = Polymarket - mid winsorised at 8c, x = Polymarket - 1/legs), aggregated per
  `momentum_slope_bin_h` 4-h bin. Kept 72 h in status.json (restored at a restart). **On the first start it is SEEDED from
  market_data.sqlite** (the recorder's snapshots of the last 72 h; headline labels, a spread > 6c and a snapshot with our own price
  at the touch are skipped; read in a background thread with its own read-only connection - never blocks a cycle), so the 24-h
  slope is there at once. slope_24h = the regression slope of the bin values over `momentum_slope_hours` 24 (>= 3 bins over >= 12 h),
  slope_6h = (latest bin - the bin ~6 h before) / their distance; both in POINTS a day. **On snap04 (4 Oct 15:57 UTC) the code reads
  bins 10.69 / 11.92 / 12.25 / 12.48 / 12.75 / 12.82, slope_24h +2.29, slope_6h +0.42** (the owner's own figures).
- **Trigger** (armed -> ON): slope_24h >= `momentum_on_slope` 0.5 AND slope_6h > 0, held continuously `momentum_confirm_h` 4 h (a dip
  restarts the clock: one burst never triggers). Journal "MOMENTUM eval: <state> - <reason> | slope24 ..., slope6 ..." every 10 min
  and at every change; ALERT "MOMENTUM auto switched ON ..." / "... switched OFF (why)" / "RE-ARMED".
- **Ramp**: the target starts at `momentum_start_usd` 10k; + `momentum_step_usd` 10k per further `momentum_step_h` 6 h of slope_6h > 0
  throughout (a slope_6h <= 0 stalls it and restarts that clock), up to `bucket_mom_frac` x account (~40k). The bucket rebalance's
  momentum target is this ramp target. Buys as built: IOC takers at the touch, the momentum longs only.
- **Funding, in order**: (a) free cash above `alloc_mm_reserve` + the election holdback; (b) the MM reserve too while
  `mm_carry_24h.per_day` < `mm_carry_min` 300 (unknown = not used); (c) `momentum_fund_value`: VALUE positions sold lowest edge-held
  first for the shortfall, within `bucket_turnover_per_hour`, **never below the value floor** (`momentum_fund_floor` true: p - 0.5c for
  a long, p + 0.5c for a short bought back; refused markets are skipped for an hour). While the round is open with a shortfall the
  allocator's spare-cash buys pause, the harvest ladder keeps `alloc_mm_reserve` + the shortfall back and leaves the candidates' races
  to the sleeve, and the quoter's plan budget leaves the shortfall (status `momentum.hold_usd`). status `momentum.funded_from` {cash,
  reserve, value_sales, value_sales_spent}, `ev_given_up` (sum of shares x (p - price) of the funding sales) and `ev_given_up_per_10k`.
  **On the 4 Oct snapshot (c) with the floor kept finds ~$1 to sell** (every value position's touch is below its floor); with the floor
  exempt the first $10k (lowest edge first) would give up ~$700 of EV and all $41k ~$1,050 per 10k. So in practice the sleeve is
  funded by (a) and, when the market maker earns less than $300/day, (b).
- **Flip (automatic)**: slope_24h <= 0 (not while forced), the sleeve's mark >= (1 + `momentum_profit_target` 0.25) x cost held 120 s,
  `mom_exit_utc`, the manual `momentum_exit`, or the kill: the sleeve's YES is sold into the bids over `mom_exit_hours` 6 and the harvest
  ladder (short the tilt to the outcome) takes those markets over. A "flipped" sleeve RE-ARMS after `momentum_rearm_h` 24 of a fresh
  trigger once the exit is done (0 = never; not while `momentum_exit` is true or past `mom_exit_utc`); a killed one only by
  `momentum_enabled` false then true.
- **No fake buying (rule 5)**: the sleeve never buys where we rest or plan a sale - harvest levels anywhere in the race, a sell-down /
  funding sale or an allocator sale planned there, a resting order of ours from another feature; our plain quotes there are cancelled
  before the buy and a sleeve market is never quoted after it (no ask over a YES leg, no bid under a NO leg until the exit).
- status.json `momentum` adds {armed, on, auto_state, size_usd, target_usd, slope_24h, slope_6h, reason, funded_from, ev_given_up,
  ev_given_up_per_10k, confirm_since, steps, next_step_at, series_bins, series_source, hold_usd}; the 2-hourly summary
  " | momentum armed/on X/Yk, slope24 +a.b, slope6 +c.d".

## The manual switches
- Force the sleeve ON now: `momentum_force` true (buys to 40% of the account; the slope flip is off while forced).
- Force it OFF (no more buys from the trigger; the sleeve is held): `momentum_auto` false (one alert if it was on).
- Sell it: `momentum_exit` true (over `mom_exit_hours`; set it back to false afterwards - it re-arms after 24 h of a fresh trigger).
- Start over after a kill: `momentum_enabled` false, wait one cycle, then true.

## First hour: what to watch
1. `momentum.series_source` "seed" (or "seed+live") and `series_bins` ~18 rows; the first "MOMENTUM tilt series seeded from the
   recorder ... slope24 +x, slope6 +y" line. **slope24 / slope6 should be close to the snapshot's reading** (about +2.3 / +0.4 on
   4 Oct afternoon; whatever the tilt did since). "no history" = the recorder file is missing: the 24-h slope builds up from live
   samples (>= 12 h before the trigger can fire).
2. "MOMENTUM eval: armed - confirming: held x h of 4 h" (or "slope6 ... <= 0"): **no "MOMENTUM bought" line before the 4-h confirm**
   (from the first start, even with the trigger met, the earliest buy is 4 h later) and the "switched ON" alert.
3. The harvest ladder resting ("HARVEST ... ask YES ..." lines; status `harvest.levels_resting`), never on a sleeve market.
4. Sell-down sales ("BUCKETS sold ...", exempt) and any "MOMENTUM funding sold ... at / above the value floor" (funding sales never
   below p - 0.5c; "not sold - ... beyond the value floor" INFO lines are expected).
5. Writes <= 28 a minute (the Package 13 features: <= 60% / 40% shares of the writes left).
6. "WARNING aggressive_value on ..." leading the 2-hourly summary, with " | momentum armed 0.0/0.0k, slope24 +2.3, slope6 +0.4".

## Tests (this branch)
tests/test_p13c.py 134 (settings; flags off = f61495e on a grid; forced = the old orders; TiltSlope bins / estimator / filters /
regression / 6-h; seeding and the snap04 reading; restart; trigger; ramp; funding (a) / (b) / (c), floor, ev_given_up, the allocator
pause, the ladder holdback, the quoter budget; flips; latch / re-arm; rule 5; no duplicates; no crash without history / DB / books),
tests/test_p13.py 146, tests/test_p13b.py 128, tests/test_p13_redteam.py 90, tests/test_p13_dryrun.py 94 (all suites green; see
deploy/package13/START_HERE_SECTION.md).
