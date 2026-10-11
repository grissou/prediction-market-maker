# If positions pay out at the outcome, how should the bot run?
*4 October 2026, about 09:30 UTC (corrected at 11:55); the 3 October 22:47 snapshot of the book, a 20,000-draw outcome model, and a literature sweep.*

**In one paragraph.** The exchange pays positions out at the election outcome, so a position is worth its Polymarket probability *p* and interim marks do not matter. Held as it was, the book had an expected final value of 109.0k (standard deviation 7.5k) with no realistic chance of 150k. Reallocating to the highest edge per dollar and holding lifts that to 112.9k (114.9k with recycling on convergence), and the only way to reach 150k with meaningful odds was a single correlated bet of up to 20k; the first candidate (Republican Senate control at 0.35) turned out to be roughly fair once the Ohio price was fixed, and a Texas Senate bet replaced it (about 30% chance of 150k, expected 112.1k). Market making continued only as value market making: quote on the side that adds value at the outcome, and gate the reserve on 24 hours of live earnings.

## Why it mattered
This plan produced value mode. Its parts: no reducing quote below *p* − 0.5c; an hourly reallocation step; a national-swing cap instead of a share-count party cap. Risk limits were rewritten for a book that settles at the outcome rather than at marks.

## What was done
Each plan was run through an outcome model: Polymarket probabilities scaled within each race, one national factor with correlation 0.45 (0.85 for the control markets), independents separate, 20,000 outcomes. Each plan reports the expected final value and the chance of ending above 120k and 150k or below 85k and 70k.

## What it found

### Plans compared
| Plan | Expected | P ≥ 120k | P ≥ 150k | P ≤ 85k | P ≤ 70k | What it takes |
|---|---|---|---|---|---|---|
| 0. Hold the book | 109.0k | 2.7% | 0% | 0.6% | 0% | nothing |
| (a') sell the 23 lowest-edge positions (under 2% edge, 8.8k), redeploy | 110.7k | 12% | 0% | 1.8% | 0% | 28 orders, now |
| (a) maximum-expectation reallocation, held to the outcome | 112.9k | 29% | 0% | 1.9% | 0.1% | about 111 orders, 127k turnover |
| (b) (a) plus recycling on convergence (sell within 2c of *p*, redeploy) | 114.9k | 30% | 0% | 0.9% | 0.1% | about 25 more orders; depends on the tilt path |
| (c1) (a) plus Rep U.S. Senate YES 20k at 0.35 | 110.5k | 34% | **17%** | 4.3% | 0% | the only positive-expectation correlated bet |
| (c1) at 22.5k | 110.2k | 34% | 26% | 4 to 25% (cliff) | 0.1% | |
| (c1) at 30k | 109.3k | 35% | 32% | **65%** | 3.9% | |
| (c2, c3) party blocs across competitive races (35 to 97k) | 95 to 108k | 35% | 20% | 33 to 47% | 3 to 38% | lose expectation at the ask: no |
| (a) plus value market making at 0.67k a day | about 133k | | 6% | about 0.1% | about 0% | 15 to 25k of rotating cash; 24 h live proof first |
| (a) plus value market making at 1.0k a day | about 143k | | 29% | 0.1% | about 0% | same |
| (a) plus market making 0.67k a day plus Rep Senate 15k | about 133k | | 26% | 0.7% | | |
| (a) plus market making 0.33k a day plus Rep Senate 20k | about 123k | | 28% | 3.4% | | |

Bets of 40% were unreachable: capped by the probability of Republican Senate control, about 35%. The cliff in the chance of ending below 85k sits between 20k and 27.5k, so 20k is the ceiling for any single correlated bet.

Recycling on convergence adds +1.6k probability-weighted (+1.4%), ranging from +0.5k if the tilt keeps growing to +4.2k with linear convergence. A claimed "+10 to 20%" is not plausible as a share of the account; it is 10 to 20% of the edge, because uniform convergence also erases the edges to redeploy into. A 40k correlated bet would match "+40 to 60% / −30 to 40%", but the bad night is the likelier one (45 to 51% chance of ending below 70k).

The party delta the bot reported (−15.4k shares) had the wrong sign for this purpose: the book's real sensitivity to a national swing was Republican-leaning, +2.55k per standard deviation. Accounts long the tilt (such as the leader) marked at a median of about 183k but would settle at a median of about 28k.

### Market making as value market making
Valued at the outcome, 3 October's maker fills split: favourite bids and longshot asks +1,948 (+5.1c a share); favourite asks and longshot bids −1,920; middle two-way +604; takes +1,862 (+7.5c a share). The rule: fair value is raw Polymarket, and the side that adds a position must clear a per-dollar hurdle, so in the tails only favourite bids and longshot asks rest. The middle (15 to 85c) stays two-way at the minimum edge, capped at two quote sizes so the cash rotates. Flow models put the earnings at 0.9 to 2.0k a day at the mark, or about 1.4k a day at the outcome. This was unmeasured live. The reserve was therefore gated on 24 hours of live data at 0.6k a day or more.

### Carry check (09:55)
Maker fills on the value sides earned +1.4k on 39k of cash on 1 October, +4.3k on 79k on 2 October and +0.2k on 3.4k on 3 October: about 4.9c per dollar, once, because the positions are locked to the outcome. So "1.0k a day of carry" from the value sides needs 20 to 35k of fresh cash a day, which a 100k account does not have; that income is plan (a) itself. The repeatable part is the middle two-way book: +1,976 over about 1.3 days, mostly on 2 October. The anti-value sides lost 1.9k. A calibration that shrinks probabilities toward 50% by 10% cuts the value-side figure by about 35%. So the 6% and 29% chances of 150k in the market-making rows stand only if the middle book earns that on a 15 to 20k reserve. One day's data (about 1.5k) cannot prove it. Without it the honest expectation is plan (a) or (b): 113 to 115k and no realistic chance of 150k.

### Correction (11:55)
The Republican Senate bet's edge came mostly from a seat-count model that put Ohio at 0.40 for the Democrat; the market had Ohio at about 0.57 to 0.61. With that fixed the races imply a 58 to 63% chance of Democratic control, so Republican control at 0.35 is roughly fair. The bet was not to be bought on the earlier numbers. If a rank bet is wanted, the literature (a floor plus one digital that pays the gap) and the re-run point to a 20k Texas Republican Senate bet: about 30% chance of 150k against 16%, expected 112.1k against 110.6k, 6.5% chance of ending below 85k against 5.2%, 28% chance of the top 50 against 23%. One volatile race; 20k stays the ceiling (24% chance below 85k at 22k). Also from the sweep: assume no reversion of the tilt before the close, drop the +1.6k recycling assumption and keep 10 to 15k back for the last days; an 8% hurdle puts favourite bids about 2.5c behind the book, so use 4 to 5%; the paired NO positions are stock to sell into the tilt rather than unwind at cost.

## Caveats
- The outcome model's correlation structure (one national factor) is an assumption.
- Market-making carry was unmeasured in value mode; the figures rest on one funded day.
- The seat-count model was shown to be sensitive to a single race price (Ohio).
- The close time (midnight or 17:00 UTC on 4 November) needed confirming against the API at the time.

## Details
What the plan built, all off by default in the old bot (tag `legacy-2026-10-11`): `value_mode` (no reducing quote below *p* − 0.5c, or above *p* + 0.5c for a short, in normal and reduce-only quoting; the pre-close exit and flatten windows made inert and overridable); `bloc_delta_enabled` (a closed-form national-factor sensitivity per share replacing the share-count party cap, `max_bloc_delta_frac` 0.05 of the account per standard deviation); the hourly allocator `alloc_*` (ranks holdings by edge held and book levels by edge per dollar of collateral, sells first at the touch, then buys; refills a market-making reserve `alloc_mm_reserve` 15k from the lowest-edge holdings and paired sets at `alloc_set_cost_per_usd` 0.06); `value_quote_hurdle` with `capital_ceiling_adding_size_factor` 0.5 and `take_respect_reserve`.

Risk limits for an outcome-settled book: `max_worst_case_frac` 0.30 → 0.35; `worst_case_backstop_frac` 0.8 → 1.3 (at 0.8 it pinned the book reduce-only: worst case 82k against 80.8k); the kill switch `max_drawdown_pct` 0.30 on marks → 0.40 or settlement-based. Ruin numbers: value book 0.6 to 1.9% chance below 85k; with a 20k bet 4 to 5%; at 25k or more 49 to 65%.

```
national-factor sensitivity per share = sqrt(rho) × phi(Phi⁻¹(p))
hurdle 8%: YES bid ≤ p / 1.08;  adding ask ≥ 1 − (1 − p) / 1.08
calibration: p' = logistic(0.9 × logit p), re-normalised per race
```

If settlement were at marks rather than the outcome, the book was short about 7.7k at the median tilt (20k at the 95th percentile) plus about 535 on the sets; a roughly 2.2k longshot-YES hedge would cancel that for about 1.8k of expected value. Deploy order at the time: code, then the value guard, then the allocator, then value market making; rollback was the previous file.
