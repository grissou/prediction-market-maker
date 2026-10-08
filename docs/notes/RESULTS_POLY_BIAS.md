# RESULTS_POLY_BIAS (Package 5, Finisher 2b, 3 Oct 2026): the short version. Full numbers: SIM_NOTES.md "Round 5"; the package: START_HERE.md "Package 5".
1. Diagnosis (live, 2 snapshots): one favourite-longshot tilt s (6.3% by 20:37 UTC 2 Oct, 2.4% a day earlier) explains 60-68% of the Polymarket-tournament
   gap and never closes; the bot buys it (tilt exposure +32.7k, -327 per point; 88% of open capital toward Polymarket; takes +154 at the tournament mid).
2. Yardstick: live_sim now marks at liquidation and at the exchange-style mark, with rivals anchored to the tournament and the live reduce-only backstop
   modelled. The old Polymarket mark overstated base P&L by ~1,440 per 3 h. No earlier verdict flipped outright.
3. The fix, T2.1 `ref_tilt_enabled` (quote around the tilt-corrected Polymarket price): d pnl_lag / d pnl_liq vs base, paired seeds, ± 1 SE:
   tilt world 8 x 3 quiet +166 ± 72 / +163 ± 60; news 6 x 6 +670 ± 180 / +761 ± 160; pinned (live-like) 16 x 6 **+509 ± 110 / +601 ± 107**;
   free 16 x 6 +330 ± 64 / +426 ± 63; old world +215 ± 74 / +244 ± 94; flat tilt -60 ± 59 / +100 ± 84. Capital -6 to -9 points, worst case -7 to -10k,
   writes down (up +2.4/min only where it frees the bot from reduce-only). Never loses in any world tried.
4. With T2.1: `take_tilted_ref` (data-based), ramp-in 20 min (default), `ref_tilt_max` 0.20. Not: backstop 0.85 (+78 ± 86 over T2.1), hysteresis 0.01
   (-57 ± 60), ref_weight 0.5/0.35, A, B, C, X5, X11, T2.4, T2.5, the ladder (all recorded, all OFF).
5. Rollout: code (all off) -> `ref_tilt_enabled` -> `take_tilted_ref` -> `ref_tilt_headline`; 1-h / 3-h go/no-go checks and rollback triggers in START_HERE.
