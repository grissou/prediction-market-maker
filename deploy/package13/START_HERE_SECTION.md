## Package 13 (READY, 4 Oct ~23:30 UTC; branch `claude/p13-momentum-auto` = `claude/finisher-package9` f61495e + Package 13 C, on top of the live head c7c0107): the 20 / 40 / 40 AGGRESSIVE portfolio + the momentum sleeve's automation (all OFF; staged file in `deploy/package13/`)
**The owner's instructions:** 18:40 + 18:55 (analysis/p12/SPEC_P13_AGGRESSIVE.md: "no modelling, no comparisons: aggression"; MM 20% /
VALUE 40% / MOMENTUM 40%; limits that stay: the cash gate, the value floor for value positions, the backstop as a 1.0 bug tripwire, the
30% drawdown kill, 15k / race and 12k / market of collateral); 20:30 (the momentum slope over 24 h, not 12, as a setting); 21:20
(automate the sleeve's switch-on: trigger, ramp, funding order, automatic flip / exit, "no fake buying", status). The 21:20 message
supersedes the spec's "flip not modelled" and, for the sleeve's own funding sales, the "sell-down exempt from the floor" answer.
**Built (each OFF, in OVERRIDABLE with a range; flags off pinned byte-identical on grids: A + B to 663ede1, C to f61495e):**
- A1 `buckets_enabled`: MM = `alloc_mm_reserve` cash (capped by the gate's cash) + middle-band inventory at p; VALUE = every other
  position at p; MOMENTUM = the sleeve at cost; hourly (`alloc_interval_s`) VALUE above target + `bucket_band` sold down lowest
  edge-held first, IOC at the touch, exempt from the floor (answer 1), <= `bucket_turnover_per_hour` 50k; MOMENTUM below target opens
  a buy round. Classification (answer 2): longshots (p <= `mom_max_p` 0.25) with p <= `value_extreme_p` 0.04 or a short's edge per $
  >= `value_min_edge` 0.12 are VALUE shorts, the other longshots MOMENTUM longs (YES, or the favourite's NO in a 2-leg race when
  cheaper); favourites with (p - ask) / ask >= `value_min_edge_fav` 0.08 VALUE longs; one bucket a market. status `buckets`.
- A2 `aggressive_value`: reduce-only only from the backstop TRIPWIRE (`worst_case_backstop_frac` 1.0); party / bloc caps do not block
  adding; the fraction caps replaced by `aggr_max_market_usd` 12k / `aggr_max_race_usd` 15k of collateral (all buckets + resting
  orders); "WARNING aggressive_value on" leads every summary; status `aggressive`.
- A3 `momentum_enabled`: the sleeve (IOC takers at the touch, >= `mom_min_depth` 200, <= `mom_max_markets` 40, never headline /
  basket / set-ladder / the opposite side of a position; its markets never quoted or traded by other features); `momentum_exit` (manual,
  sold over `mom_exit_hours` 6, exempt from the floor), `mom_exit_utc`, the kill (mark < `mom_kill_frac` 0.75 x cost for 120 s:
  latched "killed"); legs / cost / state persisted. status `momentum`.
- B4 `tilt_harvest_ladder`: resting YES asks over longshots (p <= 0.10) / bids under favourites (p >= 0.90) at touch + `harvest_offsets`
  0 / 2 / 4 / 6c, `harvest_level_usd` 3k a level where edge per $ >= `harvest_min_edge` 8%, <= `harvest_writes_frac` 0.4 of the writes,
  re-quoted on a 1c move or every `harvest_requote_s` 900; hidden from the quote planner; fills are VALUE ("HARVEST fill"); status
  `harvest`. B5 `election_night`: `election_holdback_usd` 20k kept in cash from `election_holdback_from_utc` 3 Nov 12:00; calls from
  `election_start_utc` 23:00 (Polymarket >= `election_called_p` 0.98 / <= 0.02 for `election_called_min` 10 min, or resolved); stale
  quotes on called races TAKEN below `election_take_max_price` 0.95 (IOC, batched, caps, the holdback); with `close_override_utc`
  "2026-11-04T17:00:00Z" + `stop_minutes_before_close` 5 (SIG confirmed). status `election`.
- C (Package 13 C, this branch) the sleeve's automation. `momentum_enabled` = the machinery only: **alone it no longer buys**; buys need
  `momentum_force` (40% at once, no trigger; the slope flip off) or `momentum_auto` with the trigger ON. TiltSlope: every minute the
  cross-section of non-headline markets with a liquid Polymarket price and an other-traders spread <= `momentum_slope_max_spread` 0.06
  (own orders stripped, never our fills), the slope estimator (sum x g / sum x^2, g = r - mid winsorised 8c, x = r - 1/legs) per
  `momentum_slope_bin_h` 4-h bin, 72 h in status.json, SEEDED on the first start from market_data.sqlite (background thread, read-only;
  no file = "no history"); slope_24h (regression over `momentum_slope_hours` 24) and slope_6h in points a day. **snap04 (4 Oct 15:57):
  bins 10.69 .. 12.82, slope_24h +2.29, slope_6h +0.42** (= the owner's). Trigger: slope_24h >= `momentum_on_slope` 0.5 and slope_6h
  > 0 held `momentum_confirm_h` 4 h -> ON (ALERT); ramp `momentum_start_usd` 10k + `momentum_step_usd` 10k per `momentum_step_h` 6 h of
  slope_6h > 0 (stalls on <= 0), capped at `bucket_mom_frac` x account. Funding: (a) cash above reserve + holdback; (b) the MM reserve
  while `mm_carry_24h.per_day` < `mm_carry_min` 300 (None = no); (c) `momentum_fund_value`: VALUE sold lowest edge-held first, never
  below the floor (`momentum_fund_floor`), status funded_from / ev_given_up / ev_given_up_per_10k; while the round is short the
  allocator's spare-cash buys pause, the ladder keeps reserve + shortfall back and yields the candidates' races, the quoter leaves the
  shortfall. Flip (automatic): slope_24h <= 0 (not forced), mark >= (1 + `momentum_profit_target` 0.25) x cost for 120 s, the date,
  the manual exit, the kill -> the existing exit, the ladder takes over; "flipped" re-arms after `momentum_rearm_h` 24 of a fresh trigger
  (0 = never), "killed" only by `momentum_enabled` false then true. Rule 5: never a buy where we rest / plan a sale (harvest in the
  race, sell-down, allocator, other features' orders). status `momentum` {armed, on, size_usd, target_usd, slope_24h, slope_6h,
  reason, funded_from, ...}; journal "MOMENTUM eval ..."; summary " | momentum armed/on X/Yk, slope24 +a.b, slope6 +c.d".
**Red team** (analysis/p13/REDTEAM.md, crashes / duplicate orders only): RT13-1 a harvest batch timing out after landing placed a second
ladder (adopted now); RT13-2 the quote's reduce cap double-offered harvest-covered shares; RT13-3 a lagging / 409 positions read
dropped a sleeve leg and re-bought it (120-s grace); RT13-4 election "covered" sales from a stale ex.inv (this cycle's position);
RT13-5 low, not fixed (a stop mid-tick lets that tick's IOCs go). Package 13 C found and fixed one more: with the sleeve armed but not
buying, a harvest level could land at the price of our own resting quote on the same side (two of our orders at one price): `hv_plan`
skips that level until the quote is gone.
**Dry run** (analysis/p13/DRYRUN.md, the 4 Oct snapshot, 237 markets): six fixes (no momentum round trip within the hour; the exit
serves the leg furthest behind; no ladder from a stale touch; calls read a dropped reference's raw price; election covered sales bound
by this cycle's position; the quoter's budget less the holdback). With the staged file the sleeve is now ARMED and buys nothing in the
harness (no recorder history: "no history"); the 1F fixture forces it on for an hour (books refilled every 10 min). Findings: F1 the
sleeve starved (addressed by C's hold); F2 the ladder's resting collateral is in no bucket; F3 exits dribble ~5 small IOCs a cycle;
F4 every race already priced <= 0.02 / >= 0.98 is called 10 min after 23:00; F5 a called race's p jumps when the reference is first
dropped then accepted; F6 the "ignoring it (wrong match?)" alert fires on every call against a stale book.
**Suites:** tests/test_p13c.py 134, test_p13.py 146, test_p13b.py 128, test_p13_redteam.py 90, test_p13_dryrun.py 94 (~5 min, needs
/home/claude/snap04), every other suite N/N, STRESS_LADDER=1 test_stress 20/20. The P13 A / B / red-team harnesses set
`momentum_force` where they enable the sleeve (= the old momentum_enabled behaviour); the red team's kill scenario moves every leg
against the sleeve (a short leg's book up).
**Deploy (owner; `deploy/package13/README.md`):** handover to this branch's head (flags off = live), then
`settings_override.aggressive.json` (the live file + section 7's risk settings + every flag on, `momentum_auto` true, `momentum_force`
false, `momentum_fund_floor` true, the C defaults explicit; 98 keys, validated). **Switches:** `momentum_force` (on now), `momentum_auto`
false (off: no trigger buys), `momentum_exit` (sell the sleeve). **Watch, first hour:** "MOMENTUM tilt series seeded ... slope24 ~+2.3,
slope6 ~+0.4" (vs the snapshot), "MOMENTUM eval: armed - confirming ..." and **no "MOMENTUM bought" before the 4-h confirm**, the ALERT
on switch-on, the harvest ladder resting, "BUCKETS sold" / "MOMENTUM funding sold ... at / above the value floor" (never below p -
0.5c), writes <= 28 a minute, "WARNING aggressive_value on" + " | momentum armed ..." on the summary.
**Rollback:** `deploy/package12/settings_override.stage1b_risk_reserve.json` (sleeve legs are then held: `momentum_exit` first if
they should go); code f61495e / c7c0107.
**Caveats / not built:** funding (c) with the floor kept finds ~$1 on the 4 Oct book (every value position's touch is below its floor;
floor exempt: ~$700 of EV given up for the first $10k, ~$1,050 per 10k over all $41k) - in practice (a) and (b) fund the sleeve; the
seeding reads `reference` without its liquidity flag and keeps a snapshot only when our price is not at the touch (live samples see
the others behind our quote); the sleeve's mark falls back to the fair value when a book side is empty (a sleeve that swept a whole
side can read a mark far from its cost - the kill / profit target act on it after 120 s); the Package 9 basket's own 12-h change
(`basket_s_change`) is unchanged (the 24-h window is the sleeve's); the write budget and the realtime feed are not modelled by the fakes.
