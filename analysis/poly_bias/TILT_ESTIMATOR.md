# T2.1 tilt estimator check (live 0.14 vs rebuilds ~0.09)
Data: `ops-snapshot-2026-10-02b` (to 2 Oct 20:37 UTC; no 3 Oct data here). Script: `tilt_estimator_check.py DATA_DIR`.
r = RAW Polymarket mid (`reference_prices` never normalises it; `snapshots.reference` = the r the estimator gets).

## 1. s at 2 Oct 20:37, sample x estimator (raw, unsmoothed; x = r - 1/legs, g = r - book winsorised at 0.08)
| sample | n | slope (live) | wls | median |
|---|---|---|---|---|
| (a) plain mid, all two-sided non-headline | 225 | 0.062 | 0.060 | 0.056 |
| (b) `fair_value` (200 sh, spread <= 0.30) on recorded books | 222 | 0.063 | 0.061 | 0.057 |
| (c) (b) + per-race `normalise` as the cycle does (race with a None: untouched) | 222 | 0.067 | 0.065 | 0.066 |
| (d) bot sample: (c), minus plausible-gap > 0.25, liquid = all (spreads not recorded; status 222/229) | 222 | 0.067 | 0.065 | 0.066 |
| (e) bot's own full-depth book_fv, inverted from recorded fv = 0.3 book + 0.7 r | 225 | 0.063 | 0.059 | 0.058 |
| (d) restricted to depth spread <= 0.02 | 198 | 0.070 | 0.068 | 0.069 |

On 2 Oct the bot's exact selection and formula give 0.067 vs 0.062 plain mid: +8%, not +50%. Normalisation adds
+0.004 (tail legs of races whose book sums differ from 1, e.g. 3-leg RI Governor, x = +0.63 with c = 1/3); the depth
filter drops 3 markets and moves s by +0.001; the plausible-gap filter and spread filters change nothing. The
cross-section is close to linear: mean g/x 0.056 (|x| 0.1-0.3), 0.070 (0.3-0.45), 0.067 (> 0.45, 59% of the x^2
weight); nothing is winsorised. So no candidate mechanism produces a 50% over-read on this data. Biggest (d) vs (a)
per-market moves (contribution to s): Dem RI Governor +0.0006→+0.0013, Rep Montana Sen +0.0001→+0.0007, Rep Wyoming
Sen, Dem Hawaii Gov, Rep Oklahoma Sen, Dem Massachusetts Sen (+0.0003-0.0005 each, depth-filtered tail books further
from r than the mid), Dem Michigan Gov +0.0009→+0.0002, Dem Minnesota Gov (the other way); full table in the script.

## 2. What CAN produce 0.09 → 0.14: tail markets pinned at the winsor
Slope weights are x^2, and |x| > 0.45 markets carry ~60% of them. A tail market whose book_fv sits >= 8c from r reads
g/x ≈ 0.17. Pinning a share of the |x| > 0.45 tails in sample (d): 0% → slope 0.067 / median 0.066; 50% → 0.097 /
0.079; 100% → 0.128 / 0.119. From a 0.09 base, pinning most tails gives ~0.14. Where the live bot can see such tails
but the recorder rebuild cannot: the bot prices from FULL-depth books (recorder keeps 3 levels: a thin tail book that
needs deep levels to reach 200 shares is None in a rebuild but a far-out price in the bot), and with the tilt on our
own quotes sit at the top of those tail books, so the stripped (others-only) book is wider. Unverifiable here.
Note: "a fresh first estimate after a restart is also 0.14" is not independent evidence: `from_dict` restores s with
ready=True and the first update only sets the clock; the EMA then needs ~2 h (4 half-lives) to forget it.

## 3. Fix (in this commit, default unchanged)
- `ref_tilt_estimator: str = "slope"` (live-overridable, exactly one of `slope`, `median`, `wls`; a comma list or any
  other value is refused by `validate_overrides` via `ONE_OF_SETTINGS`). `median` = median of per-market g/x over
  |x| > 0.1 (`TILT_RATIO_MIN_X`); `wls` = their mean (= WLS with 1/x^2 weights). Fewer than `ref_tilt_min_markets`
  ratios: hold. Winsor, EMA, clip unchanged.
- status.json `tilt_diag`: this cycle's raw slope / median / wls, n, n_ratio, `pinned_weight` (share of the slope's
  x^2 weight at the winsor) and the estimator in use. This answers the 3 Oct question live in one look: slope 0.14
  with median ~0.09 and pinned_weight > 0.2 = the tail mechanism; all three ~0.14 = the tournament really moved.
- Tests: tests/test_tilt.py +21 checks (fail before: no setting; pass after); all 25 suites green.

Stability, hourly 2 Oct 09-20 UTC, sample (c): slope 0.053-0.072, median 0.051-0.071 (same noise; (e) is noisier
because the inversion amplifies the final normalise by 1/0.3). Recommended: keep `slope` as the default (identical
to `median` on clean data); if live `tilt_diag` shows slope − median > 0.02 with pinned_weight > 0.2, set
`ref_tilt_estimator: "median"` (more robust to pinned tails: 0.079 vs 0.097 at 50% pinned) and keep `ref_tilt_max`
0.09 until median and slope agree again. Neither estimator can be shown to give 0.09 on 3 Oct without 3 Oct data.

## 4. Biggest positions, 2 Oct 20:37 (headline legs tilt only with ref_tilt_headline)
| market | r | mid | r' s=0.09 | r' − mid | r' s=0.14 | r' − mid | implied g/x |
|---|---|---|---|---|---|---|---|
| Dem U.S. House | 0.925 | 0.8725 | 0.8868 | +1.42c | 0.8655 | -0.70c | 0.124 |
| Rep U.S. House | 0.075 | 0.1375 | 0.1133 | -2.42c | 0.1345 | -0.30c | 0.147 |
| Rep Rhode Island Senate | 0.009 | 0.0725 | 0.0527 | -1.98c | 0.0773 | +0.48c | 0.129 |
| Rep New Hampshire Governor | 0.960 | 0.8975 | 0.9181 | +2.06c | 0.8952 | -0.23c | 0.136 |
| Rep Georgia Senate | 0.026 | 0.0825 | 0.0691 | -1.34c | 0.0928 | +1.03c | 0.119 |
These five sit at 0.12-0.15 implied tilt, well above the 2 Oct cross-section (0.067): s = 0.14 prices them at the
mid, s = 0.09 leaves r' 1.3-2.4c inside the mid toward Polymarket. They are held BECAUSE they are the most tilted.
