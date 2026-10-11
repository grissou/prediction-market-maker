# Why did the live bot read the tilt at 0.14 when a rebuild gave 0.09?
*2 October 2026; recorder snapshots to 20:37 UTC that day (no 3 October data).*

**In one paragraph.** The tilt estimate *s* (how far tournament prices lean from Polymarket toward 50/50) read 0.14 live but about 0.09 in offline rebuilds; on the 2 October data the bot's exact selection and formula give 0.067 against 0.062 for a plain mid, 8% higher, not 50%. No candidate mechanism (race normalisation, depth or gap filters) produces the over-read. What can is tail markets whose book price sits 8c or more from Polymarket: they are clipped at the winsor, read a ratio of about 0.17 each, carry about 60% of the slope's weight, and pinning half of them lifts the slope from 0.067 to 0.097 (all of them, to 0.128). The live bot prices from full-depth books the recorder cannot rebuild, so this was unverifiable; the fix was a choice of estimator (slope, median or weighted mean) plus a diagnostic that shows all three.

## Why it mattered
The tilt estimate shifts the bot's fair value for every market. At *s* = 0.14 the five biggest positions were priced at the mid; at 0.09 their fair value sat 1.3 to 2.4c inside the mid toward Polymarket. That is the difference between holding those positions and exiting them. The analysis also found that a "fresh" estimate after a restart is not independent evidence, because the saved estimate is restored and takes about 2 hours to fade.

## What was done
The estimate was rebuilt from the recorded books under six sampling rules, from a plain mid to the bot's own full-depth fair value, and with three estimators: the live slope (weights of *x*²), a weighted least-squares mean, and the median of per-market ratios.

```
x = r − 1/legs          (r = raw Polymarket mid; legs = outcomes in the race)
g = r − book price      (winsorised at 0.08)
s = slope of g on x     (live estimator)
```

## What it found

### Estimates at 2 October 20:37, by sample and estimator

| Sample | n | Slope (live) | WLS | Median |
|---|---|---|---|---|
| (a) plain mid, all two-sided non-headline markets | 225 | 0.062 | 0.060 | 0.056 |
| (b) fair value (200 shares, spread ≤ 0.30) on recorded books | 222 | 0.063 | 0.061 | 0.057 |
| (c) (b) plus per-race normalisation as the bot does it | 222 | 0.067 | 0.065 | 0.066 |
| (d) the bot's sample: (c) minus plausible-gap > 0.25 | 222 | 0.067 | 0.065 | 0.066 |
| (e) the bot's full-depth fair value, inverted from the recorded blend | 225 | 0.063 | 0.059 | 0.058 |
| (d) restricted to depth spread ≤ 0.02 | 198 | 0.070 | 0.068 | 0.069 |

Normalisation adds +0.004 (tail legs of races whose book sums differ from 1, such as the 3-leg Rhode Island Governor race). The depth filter drops 3 markets and moves *s* by +0.001. The gap and spread filters change nothing. The cross-section is close to linear: mean ratio 0.056 for |x| 0.1 to 0.3, 0.070 for 0.3 to 0.45, 0.067 above 0.45 (59% of the weight). Nothing is winsorised on this data.

### What can produce 0.09 → 0.14: pinned tails
A tail market with a book price 8c or more from Polymarket reads a ratio of about 0.17. Pinning a share of the |x| > 0.45 tails in sample (d):

| Share of tails pinned | Slope | Median |
|---|---|---|
| 0% | 0.067 | 0.066 |
| 50% | 0.097 | 0.079 |
| 100% | 0.128 | 0.119 |

From a 0.09 base, pinning most tails gives about 0.14. The live bot can see such tails where the recorder cannot: it prices from full-depth books (the recorder keeps 3 levels), and with the tilt on, the bot's own quotes sit at the top of those tail books, so the others-only book is wider. The median is more robust: 0.079 against 0.097 at 50% pinned.

### Stability over the day
Hourly from 09:00 to 20:00 UTC, sample (c): slope 0.053 to 0.072, median 0.051 to 0.071. The same noise in both; sample (e) is noisier because the inversion amplifies the final normalisation.

### The five biggest positions
| Market | Polymarket | Mid | Fair at s=0.09 | vs mid | Fair at s=0.14 | vs mid | Implied ratio |
|---|---|---|---|---|---|---|---|
| Dem U.S. House | 0.925 | 0.8725 | 0.8868 | +1.42c | 0.8655 | −0.70c | 0.124 |
| Rep U.S. House | 0.075 | 0.1375 | 0.1133 | −2.42c | 0.1345 | −0.30c | 0.147 |
| Rep Rhode Island Senate | 0.009 | 0.0725 | 0.0527 | −1.98c | 0.0773 | +0.48c | 0.129 |
| Rep New Hampshire Governor | 0.960 | 0.8975 | 0.9181 | +2.06c | 0.8952 | −0.23c | 0.136 |
| Rep Georgia Senate | 0.026 | 0.0825 | 0.0691 | −1.34c | 0.0928 | +1.03c | 0.119 |

These five sit at an implied tilt of 0.12 to 0.15, well above the cross-section (0.067). They are held because they are the most tilted markets.

## Caveats
- No 3 October data, so neither estimator can be shown to give 0.09 on 3 October.
- The recorder keeps 3 book levels; the live bot's full-depth view could not be rebuilt, so the pinned-tail mechanism is plausible but unverified.
- Spreads were not recorded; the liquidity filter was treated as passing everything.

## Details
The fix added a setting `ref_tilt_estimator` with exactly one of `slope`, `median` or `wls` (a comma list or any other value is refused). `median` is the median of per-market ratios over |x| > 0.1; `wls` is their mean. With fewer markets than `ref_tilt_min_markets`, the estimate holds. Winsor, smoothing and clip are unchanged. The status file gained a `tilt_diag` block: this cycle's raw slope, median and WLS, the counts, `pinned_weight` (the share of the slope's weight at the winsor) and the estimator in use. Reading it: slope 0.14 with median about 0.09 and pinned weight above 0.2 means the tail mechanism; all three near 0.14 means the tournament really moved.

Recommendation at the time: keep `slope` as the default. If live diagnostics show slope minus median above 0.02 with pinned weight above 0.2, switch to `median` and keep `ref_tilt_max` at 0.09 until they agree again. The restart note: the saved estimate is restored as ready and the smoothing needs about 2 hours (4 half-lives) to forget it. 21 tests were added. The check script has since been retired (see the git history).
