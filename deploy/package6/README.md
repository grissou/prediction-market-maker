# Package 6 staged settings_override.json files (owner deploys; each read within overrides_seconds = 30 s; write a temp file and mv it)
Base = the live overrides as of 3 Oct 09:20 UTC (Package 5 stage 1 on: `ref_tilt_enabled` true, `ref_tilt_max` 0.09).
- stage0_code_only: Package 6 code with every new flag off = Package 5 behaviour + the self-test funds-refusal busy path (no more exit 3 at
  0 cash) + `tilt_diag` / `selftest_state` in status.json.
- stage1_reduce_no_as_sell: `reduce_no_as_sell` true. The deadlock fix: a bid that buys back a NO holding goes out as a covered "sell NO @ 1-p",
  which needs no cash. Takes effect one cycle after the start-up leg (one 1-share "sell NO @ 0.995" on a NO holding whose book cannot fill it,
  cancelled at once) has passed; if the exchange refuses the leg the bot alerts, keeps today's behaviour and keeps running.
- stage2_takes_tilted, stage3_tilt_headline: Package 5's stages 2 and 3, unchanged, for when the owner gets there.
Rollback = the previous stage's file. Switching `reduce_no_as_sell` off again replaces covered bids smaller than keep_fraction x the uncapped
size with "buy YES" orders, which are refused at 0 cash (the old deadlock): roll back only if the leg or the fills look wrong.
