# Package 8 staged settings_override.json files (owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = the live overrides as of 3 Oct 19:56 UTC (Package 7 live: tilt on at max 0.11, headline on, reduce_no_as_sell, no_set_aware_bids,
pair_unwind_followup, pair_no_unwind_max_cost 0.02, capital_ceiling_adding_size_factor 0). Nothing here resets the adding factor or drops
ref_tilt_headline.
- stage0_code_only: Package 8 code, every new flag off = Package 7 behaviour (pinned on the live config) + status fields.
- stage1_cash_gate: `cash_gate_enabled` (no order that needs more cash than the exchange's available figure allows: ends the 400 refusals)
  + `adding_factor_per_market` (your factor 0 means no new per-market positions, hedges included). Expect: refusals -> 0 within a cycle,
  `cash_gated` / `cash_trimmed` counting in status.json, `cash_gate_read_age` small; covered sales and pair batches unaffected.
- stage2_pair_sizing: + `pair_no_unwind_max_per_cycle` 1 (the 24 qualifying races drain at 5 writes per cycle instead of 10),
  `pair_unwind_race_order` (cheapest asks sum first, then most capital; and `unwind_is_safe` no longer refuses a NO+NO buy-back on a mark
  artefact), `pair_no_unwind_max_sets` 1000 (a cap in sets instead of cash per order), `pair_unwind_followup_max_age` 600 (a stalled
  follow-up is alerted and cleared instead of blocking its race).
- stage3_tilt_exits: + `tilt_exit_priority`, `tilt_exit_full_size`, `ref_guard_tilted`, `ref_guard_exits` (the four together: the measured
  combination; SIM_NOTES Round 8). Expect: Dem House / Rep House exits resting (asks/bids at the book), tilt_exposure falling, capital share
  falling; a Polymarket move still blocks exits for ref_jump_cooldown_seconds and when the tilted gap exceeds 2 x ref_guard_gap.
Rollback = the previous stage's file.
