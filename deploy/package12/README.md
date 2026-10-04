# Package 12 staged settings_override.json files (owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = Package 10's stage3_value_mm file (value mode, bloc delta, allocator with the 15k reserve, value quoting at hurdle 0.05, takes
respecting the reserve) on top of the owner's live value-mode file. Nothing resets `arb_enabled` / `ref_tilt_headline`;
`capital_ceiling_adding_size_factor` 0.5 is inherited from Package 10 stage 3 (the owner's call). Every file validates against OVERRIDABLE.
Order: Package 10 stages 0-3 first (or at least stage 1); then Package 12 stage0 (code, handover) -> stage1 -> stage2 ONLY if SIG confirms.
Rollback = the previous file.
- stage0_code_only: the Package 12 code with every new flag off = Package 10 stage 3 behaviour (pinned byte-identical on a grid).
- stage1_sets_skew: `alloc_set_rich_leg` (the NO+NO sets' RICH leg - NO on the favourite, worth ~2c, priced 7-12c - sold as a resting
  ladder of covered sales at the favourite's best bid / -2c / -4c in YES terms, 1/3 of the set part each, re-quoted hourly, pulled when
  the favourite's p is unknown / moved or the race stops being a set; the longshot-NO leg, the value position, is never sold),
  `alloc_prefer_short` (sell the longshot YES instead of buying the favourite YES when the race's bids sum > 1: same exposure, less cash),
  `pair_no_unwind_asks_le1` (no NO+NO pair unwind at a cost while the bids sum > 1 or a ladder rests in the race), `skew_target_inventory`
  (the quote skew measured from the allocator's target holding, not from flat: a +EV favourite is no longer quoted as a position to unload;
  the adding side keeps its skew-from-flat; off in reduce-only), `bloc_rho` 0.55 (literature: race polling errors correlate ~0.5-0.6).
  Expect: "alloc.set_ladder" in status.json (races, shares_resting, filled); at ~0 cash the ladder sells only lone NO parts (the cash gate
  charges 1.0 a share to break a set: blocked_by cash) - it earns as the reserve builds; fewer reducing asks on +EV favourites; `pair_owed`
  quieter. Watch: no ladder level above Polymarket + 0.5c (the floor), none at/above our own ask, <= 15 ladder races, writes <= 28.
- stage2_close_override_ONLY_IF_SIG_CONFIRMS: + `close_override_utc` "2026-11-04T17:00:00Z" and `stop_minutes_before_close` 5: the bot
  keeps quoting and taking until 16:55 UTC on 4 Nov instead of stopping at 23:45 UTC on 3 Nov. ONLY after SIG confirms that orders are
  accepted after 00:00 UTC on 4 Nov (rules: "all trades must be received prior to 12:00pm EST on November 4"); the override can only
  extend a close, never shorten it; the basket keeps the API close. Election-night TAKING is not built (waits for SIG's answer).
Red team (analysis/p11/REDTEAM_P12.md): 1 high (ladder resting with the favourite's p unknown/moved: pulled), 4 medium, 2 low, all fixed
or warned. Caveats: at ~0 cash the ladder sells little; a take on the favourite cancels the ladder until the hourly re-quote;
`alloc_prefer_short` does not check the short leg's depth; keep `tilt_exit_take_split_sets` OFF with the ladder (it sells the opposite leg).
- stage1b_risk_reserve (P12 ops, owner 4 Oct 16:45 UTC) = the live file of 4 Oct (snap04: backstop 0.85, max_worst_case_frac 0.40)
  + `mm_risk_reserve_wc` 5000 and `mm_risk_reserve_corr` 4000: keep RISK room for market making, not just the 15k of cash. Each cycle
  room_wc = worst_case_backstop_frac x account - worst-case loss and room_corr = max_worst_case_frac x account - settlement risk; once
  either is below its reserve, "value adds paused": no stale-quote take that grows a position (shrinking ones still go), no allocator
  buy (refills and other sales go on), no basket buy, and in the tails (p outside value_mid_low..high) only the side shrinking a
  position quotes - a resting tail add is dropped at the next re-quote. Middle-band two-way quoting keeps both sides (within
  value_mid_inventory_quotes), every reducing side, aged take and arbitrage are unchanged, and reduce-only itself is not touched: the
  point is to stop value buying ~5k BEFORE the backstop binds. It lifts at 1.1 x each reserve (5.5k / 4.4k). Expect: journal
  "VALUE ADDS PAUSED ..." / "value adds resumed ..."; status.json `mm_risk_room` {room_wc, room_corr, paused, since, reserve_wc,
  reserve_corr, blocked {takes, alloc, basket, tail_quotes}}; summary " | risk room wc Xk corr Yk (paused)". If you raise the backstop
  (e.g. 0.90 as today), re-apply it in this file: the reserves are measured from whatever the backstop / cap are. Rollback: set both to
  0 (or the previous file); 0 = off, pinned byte-identical on a grid (tests/test_mm_risk_reserve.py).
