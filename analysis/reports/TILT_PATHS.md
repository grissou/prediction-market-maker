# Where might the tilt go over the next 30 days, and how does the bot make money on each path?
*5 October 2026, about 15:30 UTC; recorder snapshots to 4 October 15:57, live readings of the bot's tilt estimate on 4 and 5 October, and a literature sweep. A judgement with numbers attached, not a model output.*

**In one paragraph.** The tilt *s* (how far tournament prices lean from Polymarket toward 50/50) rose from 0.026 on 1 October to a peak of about 0.12 on the evening of 4 October, then fell to about 0.10 by the morning of 5 October; four paths were given probabilities: range with waves (40%), a second leg up (25%), reversion (25%), collapse or intervention (10%). Because the tournament pays positions out at the outcome, selling the tilt (long favourites, short longshots) and holding earns the whole tilt on every path. Buying longshots to sell them later is marginal: about +7% expected with a 35% chance of losing a third or more. The recommendation was to sell collateral in tranches keyed to *s*, using the harvest ladder, and to keep 15 to 20k back for the final 72 hours and election night.

## Why it mattered
This set the order in which the bot's tilt-related rules went live: first the reallocation step and its swaps, then the harvest ladder (the tranche rule in order form), then the momentum sleeve armed but never forced. It also set the election-night holdback.

## What was done
The tilt was measured in 12-hour bins as the slope of (Polymarket − tournament mid) on (Polymarket − 1/legs), over non-headline markets, using other traders' touch, spread 6c or less. Edge per dollar of collateral was computed for longshot shorts at each level of *s*. The long-tilt bet was priced as a longshot bought at 10c (mid at *s* = 0.10 plus a 1c half-spread) and sold at the later mid less 1c.

## What it found

### Where the tilt is and how it got there
| 12-hour bin (UTC) | s | Sub-5c longshot best bid | Over-95c favourite best ask | Note |
|---|---|---|---|---|
| 1 Oct 12:00 | 0.026 | 3.1c | 0.968 | first week: about fair |
| 2 Oct 00:00 | 0.052 | 4.7c | 0.956 | |
| 2 Oct 12:00 | 0.061 | 5.0c | 0.952 | |
| 3 Oct 00:00 | 0.083 | 6.4c | 0.942 | |
| 3 Oct 12:00 | 0.108 | 8.3c | 0.927 | +2.5 points a day |
| 4 Oct 00:00 | 0.125 | 9.9c | 0.912 | |
| 4 Oct 12:00 | 0.128 | 10.1c | 0.907 | decelerating (+0.07 in 4 h) |
| 4 Oct 20:00 (live) | about 0.12 | | | peak so far |
| 5 Oct 10:00 (live) | about 0.10 | | | first sustained fall: −2 points in 14 h |

The bot's live reading runs about 0.9 points higher because it includes headline and wide markets. Behind the numbers: 1,087 accounts on 4 October, growing at 2.7 an hour on Sunday and 6 an hour on Friday; fewer than 20% had traded; about 870 sat untouched at exactly 100k. The marginal longshot price is set by new buyers. Longshot sellers are capital-bound: a 10c short locks 90c for 7c of edge, and the bot hit its own risk caps twice on 4 October. Longshot bids are deep (bid sums above 1 in 82 of 117 races), asks thin.

What could push it up: the 80% of accounts not yet active, a contest with 3 prizes and no downside, and the last 24 to 72 hours before resolution (the biggest mispricing jump in similar markets). What could push it down: profit-taking by longshot holders, freed sell-side capital, sophisticated new entrants, and Polymarket itself moving toward 0 or 1. Weekends brought the strongest rises (+4.2 points from 3 October noon to 4 October midnight); Monday morning the fall. Expect weekly waves of 2 to 4 points on top of the trend.

### Four paths to 4 November
| Path | Probability | s by mid-October | s late October | Last 72 hours |
|---|---|---|---|---|
| A. Range with waves | 40% | 0.08 to 0.13 | 0.08 to 0.13 | spike to 0.15 to 0.20 |
| B. Second leg up | 25% | 0.15 to 0.20 | 0.20 to 0.30 | 0.25 to 0.35 |
| C. Reversion | 25% | 0.06 to 0.09 | 0.03 to 0.06 | small bump to 0.06 to 0.08 |
| D. Collapse or intervention | 10% | under 0.05 | about 0.02 | |

Not more on B: the 5 October fall was the first sign the sell side can win a day, and every account that has traded is already in. Not more on C or D: nothing forces convergence before resolution in a play-money contest, and 80% of the field has not acted.

### Selling the tilt pays on every path
Edge per dollar of collateral for a longshot short in a 2-leg race:

| s | 2.5c longshot priced at | Edge per $ | 5c longshot | 10c longshot |
|---|---|---|---|---|
| 0.05 | 4.9c | 2.5% | 2.4% | 2.3% |
| 0.10 (now) | 7.3c | 5.1% | 5.0% | 4.7% |
| 0.15 | 9.6c | 7.9% | 7.6% | 7.1% |
| 0.20 | 12.0c | 10.8% | 10.5% | 9.8% |
| 0.30 | 16.8c | 17.1% | 16.6% | 15.4% |

A dollar sold at *s* = 0.20 earns twice what it earns at 0.10, and half at 0.05. The game for a value book is timing the sale of its collateral, and it never loses the tilt it has already sold. 30-day expected value of the roughly 80k of collateral the bot can keep deployed, by path: A 4.5 to 5.5k, B 8 to 10k, C 2.5 to 3k, D 1.5k, on top of the 108.3k already locked in.

### Buying the tilt is marginal
A 5% longshot bought at 10c and sold later:

Return on the sleeve by the later *s*: −41% at 0.03, −28% at 0.06, −10% at 0.10, +13% at 0.15, +35% at 0.20, +58% at 0.25.

Path-weighted (A sold into the final-week spike +20%, B +45%, C −30%, D −45%): about +7% expected, with a 35% chance of losing a third or more. Two execution problems: the exit needs a buyer at the top, where asks are thin; and a position held to the outcome is worth *p*, a 50 to 80% loss. Break-even probability of path B is about 20%. It is a bet on other traders, not the election. So the momentum sleeve should be armed and triggered only on a confirmed up-move, in 10k steps, never forced; with the 24-hour slope negative on 5 October it would not buy, which is right.

### The better way to be long the tilt: keep the option to sell higher
Unsold collateral is a free call on *s*. 40k of dry powder sold at 0.10 earns about 2.0k; at 0.15 about 3.1k; at 0.20 about 4.3k. Waiting costs about 0.2 to 0.5k a week of forgone market-making carry on 40k and risks path C (about 1.0k if sold at 0.06). Selling in tranches keyed to *s* beats selling everything now by roughly 0.8 to 1.2k, and beats holding for the final week on variance. The harvest ladder (`tilt_harvest_ladder`) does this automatically: asks at +0, +2, +4 and +6c above the touch on longshots, bids below on favourites, 3k a level, edge at least 8% per dollar. The reallocation swaps recycle converged positions into the remaining 10 to 13% edges (about +0.5 to 0.7k per run).

### Signals that tell the paths apart
The 24-hour and 6-hour slope of *s*: B is +1 point a day for two days; C is −1 point a day for two days; A changes sign within the week. New accounts per hour and the active share: above 8 an hour on a weekday, or a 5-point jump, means B is starting. Sub-5c longshot bids fattening with thin asks means B; asks thickening means C. More races with bid sums above 1 means B. A falling leader with rising *s* means early buyers are selling: the top of a wave.

### Recommendation, in order
1. Unstick the refill and swaps (maximum edge to sell 0.05, swap margin 0.03, selling floor back to 0.005).
2. The harvest ladder alone, with the other aggressive rules off: path-neutral.
3. The momentum sleeve armed (`momentum_auto` on, `momentum_force` off), never forced: the forced version is the negative-expectation end of the table.
4. Election-night rules on before 3 November with the 20k holdback; the close at 2026-11-04 17:00 UTC.
5. Keep 15 to 20k of risk room and cash for the final 72 hours on top of the holdback.

Expected 30-day result by path, on a book already at 108k: A +6 to 8k, B +10 to 14k, C +3 to 4k, D +2k, plus election night (worth +5 to 15k on its own). Weighted about +7k, with any loss confined to the sleeve.

## Caveats
- Four days of data; the weekly seasonality is a guess from one weekend.
- Whether the 5 October fall was profit-taking, new sellers or the weekend ending cannot be told from the bot's data.
- The path probabilities are judgement, not estimates.
- The exchange could change the rules or intervene; the trading hours after midnight on 4 November were confirmed as open to 17:00 UTC.
