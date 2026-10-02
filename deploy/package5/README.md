# Package 5 staged settings_override.json files (owner deploys; each stage read within overrides_seconds = 30 s)
Copy one file over the live settings_override.json; never edit the live bot any other way. Order and go/no-go rules: START_HERE.md "Package 5".
- stage0_code_only: the live overrides unchanged (Package 5 code with every new flag off = Package 4 behaviour + status fields).
- stage1_tilt_nonheadline: `ref_tilt_enabled` (ramp-in 120 min by default; non-headline markets only).
- stage2_takes_tilted: + `take_tilted_ref` (stale-quote takes measured from the tilt-corrected reference).
- stage3_tilt_headline: + `ref_tilt_headline` (the U.S. House / Senate legs).
- stage4_backstop_085: + `worst_case_backstop_frac` 0.85 (only after stages 1-3 have held for 3 h).
Rollback at any stage = copy the previous stage's file. Switching a flag OFF removes the tilt in one step (the ramp-in applies only
when switching ON; the headline gate has its own 120-min ramp from its own switch-on). Code rollback: handover restart to c29f762 (Package 4) or 439ac54 (Package 3).
