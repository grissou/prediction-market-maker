# Package 9 staged settings_override.json files (owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = the live overrides as of 3 Oct 22:47 UTC (Package 8 stages 1-3 on, `arb_enabled` false, `capital_ceiling_adding_size_factor` 0,
`ref_tilt_headline` true: none of these is reset by any file here; stage4 is the only file that turns `arb_enabled` back on, by design).
Every file validates against OVERRIDABLE (checked). Order: 0 -> 1 -> 2 -> 3 (or 3b) -> 4 (optional). Rollback = the previous stage's
file; `rollback_basket_off` = stage 3 with `basket_enabled` false (the basket legs are RELEASED to the ordinary book, not sold: to sell
them instead, leave the basket on and set `basket_exit_utc` to now + 1 h, then switch off after the exit).
- stage0_code_only: Package 9 code (handover restart), every new flag off = Package 8 behaviour + status fields.
- stage1_hygiene (config only; works on the Package 8 code too): `take_tilted_ref` true + `take_edge` 0.08 (the stale-quote takes measure
  their edge from the tilted reference: they added +15.0k of the +35.9k short-tilt exposure and lost ~400 at the mid), `ref_tilt_max` 0.20
  (the estimator reads 0.14-0.16 and is clipped at 0.11), `pair_no_unwind_max_cost` stays at the live 0.02: the 16.8k NO+NO sets
  (21.9k of capital) are the cheapest cash for the basket, 2c a set.
- stage2_flatten: + `tilt_exit_take` with `_max_cost` 0.02 (the tilt exits are TAKEN at the book within 2c of the tilted fair value, 15k$/h, longshot NO first,
  then favourite YES, lines marked below their exit price first) + `tilt_exit_take_split_sets` (F2b set split, C-9: the longshot-NO leg of
  each NO+NO set is sold too, FIRST, as a covered "sell NO" sized to the cash gate at 1.0 a set share; the favourite-NO legs stay = long
  tilt for free; a refusal pauses splits in that race 10 x take_cooldown_seconds; "TILT EXIT SPLIT" lines, status
  `tilt_exit_takes.splits`; dry run: +6.1k of cash from splits in hour 1, ~8.8k over 3 h, sets 16.8k -> 9.7k; roll back to F2 alone by
  setting it false; every later stage file carries it).
  Expect (dry run on the live books): "TILT EXIT SPLIT" then "TILT EXIT TAKE" lines, cash 0 -> ~15k in the first hour (the 15k$/h cap binds)
  and ~21k after 3 h, `tilt_exposure` 35.9k -> ~27k, `worst_case_loss` 79k -> ~67k, NO+NO sets 16.8k -> ~9.7k. Go to stage 3 as soon as
  `cash_gate_left` > ~15k (1-3 h): the basket refuses to buy by itself while the stressed worst case is above 0.9 x the account.
- stage3_basket: + `basket_enabled` with m 5, cap 80k, floor max(86k, 0.85 x peak liquidation value), kill at -15% from the peak or
  below the floor (latched), exit from 18 Oct 12:00 UTC over 24 h, 36-h test (tilt_s >= 0.15 and rising, else m 1.5), stress risk model
  0.4, `worst_case_backstop_frac` 0.9 as the last resort. stage3b = m 6, cap 90k. stage3c = floor 80k / peak frac 0.80 / kill -20%, m 6, cap 60k: the best achievable odds (~15-20% for 150k)
  at P(<= 85k) ~0.5-1.5%; see the catch-up plan's corrected table.
- stage4_carry (optional, after the basket is built): `arb_enabled` true + `arb_cash_rule` (sets only when the cash gate has 1.25 x the
  need + 2k, own quotes excluded, thinnest-leg sizing, unequal legs completed or reversed the same cycle) + `arb_sellback` (YES+YES sets
  sold back at bids >= 1.00).
Watch list, rollback triggers and the odds: START_HERE.md "Package 9" and analysis/p9/CATCHUP_PLAN.md.
Dry run on the live state (analysis/p9/DRYRUN.md, tests/test_p9_dryrun.py) - expect: stage 2 frees ~8-9k of cash and ~4k of
tilt exposure in the first hours (only the levels within 1c of the tilted fv; worst case 79k -> ~70k, NOT < 40k unless the tilt
estimate rises); stage 3 on that cash builds ~11k (26 legs) then waits ("no free cash (cash gate)"); with ample cash the m 5
build settles near ~40k (m 6 / 90k: ~45k), not 72-90k, because each $ bought cuts the cushion by ~15c (impact + spread).
