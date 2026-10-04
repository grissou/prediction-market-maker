# Package 10 staged settings_override.json files (FIRST DRAFT; owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = the owner's LIVE value-mode file (Package 8 in value mode, 4 Oct): `ref_tilt_enabled`, tilt exits, `ref_guard_*` and
`take_tilted_ref` off; `pair_no_unwind_max_cost` 0.003; cash gate on; `arb_enabled` false; `capital_ceiling_adding_size_factor` 0;
`ref_tilt_headline` true; backstop 0.8; writes 28. No file resets `arb_enabled` or `ref_tilt_headline`. Only stage 3 changes
`capital_ceiling_adding_size_factor` (0 -> 0.5), and that is **the owner's call**: it lets the market maker add while the capital
ceiling is active, within the cash gate. Every file validates against OVERRIDABLE (checked by tests/test_p10_dryrun.py).
Order: 0 -> 1 -> 2 -> 3. **Rollback = the previous stage's file** (stage 0 = the live file as it is today).
Numbers below are from the dry run on the 3 Oct 22:47 live state (analysis/p10/DRYRUN.md); red-team findings in analysis/p10/REDTEAM.md.

- **stage0_code_only**: the Package 10 code (handover restart) with today's live file, byte-identical to it. Every new flag is off.
  Expect: identical behaviour. The dry run sends the same orders as the Package 9 head on the live seed. The book stays in
  global reduce-only (worst case ~82k vs the 0.8 backstop 80.8k), and 4 of its 6 resting reducing quotes sit below
  Polymarket - 0.5c (the reduce-only skew sells value).
- **stage1_value_guard**: + `value_mode` with `ref_weight` 1.0, `skew_max` 0, `skew_age_enabled` false, the three pre-close windows
  0 (`exit_hours_before_close`, `flatten_hours_before_close`, `flatten_per_market_hours`; `stop_minutes_before_close` 15 still stops
  everything, takes and arbitrage included), `bloc_delta_enabled` (cap 5% of the account per sd of the national factor),
  `worst_case_backstop_frac` 1.3, `max_worst_case_frac` 0.35.
  - What it does: no reducing quote below Polymarket - 0.5c (above + 0.5c for a short), in normal and reduce-only quoting,
    and no flip of a position at a price below value.
  - Expect: out of reduce-only (worst case ~84.5k vs 131k; settlement risk ~20k vs 35k). ~40 reducing quotes resting, 0 below
    the floor, 0 crossing; most within 1c above p, 3-14 ticks behind the touch (they rest at value, not at the tilted book).
  - status.json: `bloc_delta` ~+2.5k/sd (`bloc_delta_frac` ~+0.025), against the share party delta of -15.6k shares, which no
    longer blocks anything. The 2-hourly summary shows " | bloc delta +X/sd".
  - Log: a WARNING at the override listing any mark-driven selling path still on (none in this file), and one line saying the
    pre-close windows are off.
- **stage2_allocator**: + `alloc_enabled`, `alloc_set_cost_per_usd` 0.06 (NO+NO sets may be unwound at <= 6c of EV per $ freed),
  `alloc_mm_reserve` 15000, `alloc_pin` "Rep U.S. Senate,Dem U.S. Senate".
  - The pin is an EXAMPLE. Those two are headline races, already excluded while `alloc_headline` is false. Pin the holdings
    you want kept, by exact label; a label matching no market logs a WARNING.
  - Expect hourly "ALLOC run" lines. At ~0 cash every line is a reserve REFILL: the lowest-edge holdings (edge-held <= 2%)
    and NO+NO sets, ~13 lines in the first run. No pair (sell -> buy) is planned while cash is below 15k, so
    `alloc.ev_gain_est` stays 0.
  - The sets free ~4.6k in hour 1 and ~2k/h after (depth). That is ~11k in 4 h, at ~3% per $ of EV (~$350).
  - **The stale-quote takes spend that cash as it arrives**: 12.7k in 4 h, at +7.7% per $ at the outcome. So the reserve is
    NOT reached and `cash_gate_left` stays ~0. With `take_enabled` false it would be ~6k / 8k / 10k / 12k after hours 1-4.
  - Decide which you want before stage 3 (REDTEAM.md C-1).
  - Watch: `alloc.state`, `alloc.blocked_by` (cash / writes / depth / bloc / risk), `alloc.set_unwinds_registered`, and the
    journal's "ALLOC sold / unwound / bought" lines. No allocator order or set unwind should ever touch a pinned label.
- **stage3_value_mm**: + `value_quote_hurdle` 0.08 and `capital_ceiling_adding_size_factor` 0.5 (the owner's call).
  - Rule: in the tails (p < 15c or > 85c) an adding bid is <= p / 1.08 and an adding ask >= 1 - (1 - p) / 1.08, so only
    favourite bids and longshot asks reach the top of the book. The middle is two-way at the normal min_edge, capped at
    2 x the quote size.
  - Expect: decide computes ~210 adding sides in ~125 markets (~64 at the top; 62 middle markets two-way). That needs ~18k
    of cash if all filled, worth ~1.4k/day on F's side-hour model.
  - But they rest only as far as the cash gate allows: in the dry run the takes took 19.3k of a +20k in 10 cycles, and 5
    adding sides rested.
  - Headline caveat: the middle-band cap is 2 x the headline quote size (~24k shares). Stage 3 rests an adding Dem U.S.
    Senate ask of ~3.3k on a 5k short.
- **Scenarios** (dry run):
  - TILT RISE (longshot asks +3c, favourite bids -3c): nothing is sold below value; only set unwinds within their cost.
  - CONVERGENCE (books to Polymarket +-1c): the allocator frees ~20k into the reserve (sales at edge-held ~1%, plus sets),
    reaching it in ~3 h, and buys nothing (no level with >= 5% edge).
- **Rollback**: copy the previous stage's file. Stage 3 -> 2 stops the hurdle and the adding factor. Stage 2 -> 1 switches the
  allocator off: pairs in flight are dropped and set registrations withdrawn, nothing is forced, the cash stays. Stage 1 -> 0
  restores the Package 8 value-mode behaviour (and the 0.8 backstop, i.e. reduce-only again).
- Not in these files: the kill switch `max_drawdown_pct` stays 0.30 (H-10 suggests 0.40: a code / deploy default change,
  outside OVERRIDABLE by house rule).
