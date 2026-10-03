# Package 7 staged settings_override.json files (owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = the live overrides as of 3 Oct 14:30 UTC (Package 6 live: `ref_tilt_enabled`, `ref_tilt_max` 0.11, `reduce_no_as_sell` true).
- stage0_code_only: Package 7 code with every new flag off = Package 6 behaviour + status fields (`nono_sets`, `pairno_state`, `pair_owed`).
- stage1_set_aware_bids: `no_set_aware_bids` (covered bids on NO+NO race legs capped at the lone part: stops the 400 refusals on the 38
  NO+NO races) + `pair_unwind_followup` (a pair unwind that fills unequally follows up the lagging leg at <= planned + 1c for 6 cycles, so a
  set never becomes one-sided inventory; the Hawaii Governor case).
- stage2_pair_no_unwind: + `pair_no_unwind_max_cost` 0.003: NO+NO sets are unwound as a pair (one batch of covered "sell NO" on both legs,
  sized to the smaller leg and to the joint depth) when the YES asks add up to <= 1.003, at most `pair_no_unwind_max_per_cycle` 2 per cycle.
  It only fires after the start-up pair check has been accepted (one batch of two 1-share "sell NO @ 0.995", one per leg, cancelled at once;
  `pairno_state` in status.json: "ok" / "off" / "waiting_funds"). If the exchange refuses the pair check, B stays off for the run and the bot
  alerts: NO+NO sets then cannot be unwound without cash.
Rollback = the previous stage's file.
