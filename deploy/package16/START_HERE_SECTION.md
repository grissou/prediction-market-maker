## Package 16 (READY, 6 Oct; branch `claude/package16` on ed65638 = Package 15): the ARMED momentum sleeve (OFF by default; staged file in `deploy/package16/`)
**The brief (11:00):** Package 13's sleeve + its automation on Package 15 - `momentum_auto` armed; trigger slope_24h >=
0.5 pt/day AND slope_6h > 0 held 4 h (other traders' prices only); ramp 10k steps to <= 40k; flip into the harvest ladder
on slope_24h <= 0 / the profit target / the date; kill at 75% of cost; funding (a) free cash above the kept-back cash ->
(b) the MM reserve only if mm_carry_24h < 300/day -> (c) value sales lowest edge first never below the floor; no fake
buying. Triggered, never forced (TILT_PATHS 4.2).
**Built (flags off byte-identical to ed65638):** `momentum_enabled` / `momentum_auto` / `momentum_force` /
`momentum_exit` / `momentum_max_usd` 40k + the Package 13 C settings; TiltSlope seeded from market_data.sqlite; the sleeve
first among the traders (step 6a'); funding (c) through the allocator's refill sale path, floor always kept; the ladder
leaves a leg's whole race; a round holds back the allocator / ladder / quotes only when it has cash to protect.
`election_holdback_usd` is a hook (0). Status `momentum`, journal "MOMENTUM eval ...", ALERTs on/off/re-arm/kill.
**Dry run** (`analysis/p16/DRYRUN_P16.md`, 37 checks): 4 Oct 15:57 seed -> slope_24h +2.29, slope_6h +0.42, ON after
4 h; on the live state as it is it buys $0 (free cash 1.6k < 20k kept back, nothing above the floor) and holds nothing
back; funded (+45k) it buys $10k in 15 markets, ramps 10k -> 40k target in 18 h of rising tilt, flips on slope <= 0, kills
on -30%.
**Suites:** `tests/test_p16.py` 233 + `tests/test_p16_dryrun.py` 37; all 56 suite files N/N + `STRESS_LADDER=1` 20/20.
**Deploy:** after Package 15 is live: handover to the branch head, then `mv` `settings_override.momentum_armed.json`
(= the Package 15 file + the P16 keys). **Watch:** the "MOMENTUM tilt series seeded ... slope24 / slope6" line vs your
tilt_s trend (expect slope24 < 0 today: armed, no buys), "MOMENTUM eval: armed ..." lines, no buy before the 4-h confirm
and its ALERT, `momentum.funded_from`, `harvest.paused_for_momentum` during a funded round, writes <= 28.
**Rollback:** the Package 15 file (legs then held: `momentum_exit` true first if they should go); code ed65638.
Details: `deploy/package16/README.md`.
