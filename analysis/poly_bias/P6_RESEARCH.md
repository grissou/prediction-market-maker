# P6 research: backstop pinning, capital lock-up, exchange mark, pass-through (snapshot 2 Oct 20:37 UTC)
Script: `analysis/poly_bias/p6_research.py DATA_DIR` (data from ops-snapshot-2026-10-02b staged with git show, not committed). Window 16:26:14-20:37 unless stated. Report only: nothing here proposes trading to move a mark.

**Data caveats.** (1) `trades` has 0 rows, so the only tape is our own fills; "flow" below = our filled shares/h over 14:37-20:37 (the bot's turnover definition with an empty tape). (2) The NO-price convention for sells does not hold everywhere: `quote_price` is always a YES price, and on 452 of 1,514 sided sells `fill_price == quote_price` (YES). The script takes the YES price as whichever of fp / 1 - fp is nearer `quote_price` (or the mid). Earlier P&L scripts that apply 1 - fp to every sell misprice those 452 fills. Exit-ratio share counts do not depend on price and are unaffected.

## (a) Backstop pinning
| episode | min | reduced sh | re-added in 30 min | in 60 min | churn cost, 60 min (c/sh) | adds during episode |
|---|---|---|---|---|---|---|
| 16:37-16:54 | 17 | 4,103 | 800 | 1,205 | -5 (-0.45) | 450 |
| 16:58-17:20 | 22 | 5,168 | 403 | 778 | -5 (-0.59) | 1,397 |
| 17:31-17:45 | 14 | 6,107 | 275 | 375 | -6 (-1.53) | 313 |
| 17:52-18:28 | 37 | 19,077 | 98 | 5,098 | -69 (-1.35) | 5,583 |
| 18:29-18:46 | 17 | 4,225 | 0 | 0 | 0 | 5,310 |
| 18:47-19:05 | 19 | 3,638 | 100 | 331 | -2 (-0.64) | 858 |
| 19:12-20:07 | 55 | 5,913 | 0 | 0 | 0 | 9,945 |
| 20:12-20:37 (still open) | 25 | 827 | - | - | - | 3,106 |
Closed episodes: 48,231 shares reduced; 1,676 (3%) re-added within 30 min and 7,787 (16%) within 60 min, at a churn cost of **-87 in total** (negative = bought back cheaper). 5,000 of the 60-min re-adds are one U.S. Senate fill at 19:12 that followed the 18:19-18:23 pair unwinds. The churn is not a cost. Of the 27k shares "added" while reduce-only, about 23k are arbitrage sets (Alaska and Hawaii pairs etc.): they are worst-case neutral but lock capital.
Worst case recomputed from snapshots (per-race sum of maxima at fair value) matches the journal to within 21 on average (max 293). At 20:37 the total is 80,145 over 89 races; the largest are U.S. House 17,742, then MN-02, NH Governor, RI Senate, TX Governor and GA Senate at about 4.2k each.
Biggest increases across the 7 gaps between leaving and re-entering: GA Senate +3,129, MI Governor +2,005, MN Senate +947, WA-03 +929 (all races +8,192). Biggest decreases inside episodes: WI Governor -2,776, NC Senate -1,294, MN Senate -1,214 (all -9,447). Between episodes the worst case climbs 280-570 per minute: about 3k in 4-10 minutes, from 77.7k back to 81k.

| backstop (hysteresis 0.03) | 0.80 (live) | 0.83 | 0.85 |
|---|---|---|---|
| reduce-only minutes on the observed path (share) | 204 (81%) | 0 (0%) | 0 (0%) |
The maximum observed worst case / account was 0.807. The risk cap never binds (max risk / account 0.273 < 0.30). The counterfactual is not causal. The path is endogenous: at 280-570 per minute, the worst case would reach 0.83 (+6k) in about 11-21 minutes and 0.85 (+8k) in about 15-30 minutes, and the bot would then cycle there.
**Lever: none from the backstop.** Raising it moves the pinning level up, and the churn it would remove costs nothing. Cash is the binding limit: positions are 99.9k of a 101.0k account (capital ceiling active, cash about 1.1k), so adds are funded only by exits. Raising the backstop adds about 3-5k of worst case (U.S. House alone is 17.7k) and frees no capital.

## (b) Capital lock-up (held markets at 20:37; dead = flow < 50 sh/h, headline = U.S. Senate / U.S. House)
| class | held | capital (mid) | no bid/ask on exit side | cost to exit vs mid, walking others' book | book depth covers | mean spread | our reducing quote vs best other |
|---|---|---|---|---|---|---|---|
| dead | 84 | 26,081 | 0 | 201 | 100% | 0.7c | behind +0.8c in 22/28; no reducing quote in 56 |
| quiet (50-200) | 49 | 18,151 | 0 | 127 | 98% | 0.6c | +0.6c in 15/20; none in 29 |
| busy (>= 200) | 29 | 36,787 | 0 | 275 | 97% | 0.6c | +0.3c in 6/13; none in 16 |
| headline | 4 | 20,367 | 0 | 160 | 97% | 0.6c | +2.3c in 2/2; none in 2 |
The figure of 101 dead markets was not reproduced. Here: 84 held markets below 50 sh/h, 25 with 0 fills in 6 h, and the status.json flag, which needs a race-netted size, says 41 (41.8k at fair value).
Every held market has resting liquidity from others on the exit side, deep enough for the whole position. Dead markets have no buyers, but they do have bids and asks. Exiting all dead positions at others' quotes would cost 201 against the mid (0.8% of their capital). Measured against Polymarket it would cost +1,254 more (4.8% of capital): book-wide, 88% of capital leans toward Polymarket.
Adds / reduces (shares): dead 2,252 / 4,902; quiet 8,181 / 13,429; busy 40,548 / 16,548 (ratio 0.41, arb sets included); headline 11,295 / 17,397. Capital by lot age: dead 55% older than 12 h and 40% at 6-12 h. Busy 57% younger than 3 h. Quiet is spread out (19 / 28 / 33 / 20%). Headline: 39% older than 12 h.
**Lever:** in dead and quiet markets, put the reducing side at or next to the best other quote. Today 85 of 133 markets have no reducing quote at all, and the quoted ones sit 0.6-0.8c behind. This is the only way to free cash without paying the book.
The trade-off is explicit: freeing the 26k of dead capital gives up about 1.25k of Polymarket edge to settlement (4.8%) plus up to 0.2k of spread. It pays only if the redeployed capital earns more than about 5% by 4 Nov. Busy-market capital turns over in under 3 h, but these data do not measure its edge per turn.

## (c) Exchange mark vs book (positions.current_price, 20:36, 166 held)
Sum of q x (mark - mid) = **-1,478**; mean |mark - mid| 0.77c; sum of q x (mark - liquidation) -1,047. The mark sits on the far side of the mid from Polymarket in 100/166 markets. The mark fits the mid from about 2 h earlier best (mean gap 0.67c, against 0.77c now and 1.05c at 4 h), so it looks like a lagged or smoothed price, not the last trade.
| label | pos | mark | mid | liquidation side | Polymarket | q x (mark - mid) |
|---|---|---|---|---|---|---|
| Rep U.S. House | -9,396 | 0.185 | 0.138 | 0.14 | 0.075 | -447 |
| Dem FL-13 House | -3,000 | 0.311 | 0.270 | 0.275 | 0.235 | -123 |
| Dem MN-02 House | +2,325 | 0.843 | 0.887 | 0.885 | 0.94 | -103 |
| Dem Michigan Governor | +2,339 | 0.811 | 0.853 | 0.85 | 0.915 | -97 |
| Rep New Hampshire Senate | -3,000 | 0.210 | 0.182 | 0.185 | 0.135 | -82 |
| Rep Georgia Senate | -2,757 | 0.110 | 0.083 | 0.085 | 0.026 | -75 |
| Rep New Mexico Governor | -2,973 | 0.152 | 0.128 | 0.13 | 0.045 | -73 |
| Rep Alaska Senate | -3,000 | 0.391 | 0.367 | 0.37 | - | -71 |
| Dem Alaska Governor | -3,000 | 0.759 | 0.778 | 0.78 | - | +57 |
| Rep North Carolina Senate | -2,238 | 0.120 | 0.095 | 0.10 | 0.045 | -55 |
Mark after our fills (fills at least 0.5c from the prior mark; share of the gap fill - mark closed, median / mean):
| horizon | < 500 sh (n about 805) | >= 500 sh (n about 47) | book mid over the same gap (< 500 / >= 500), median |
|---|---|---|---|
| +2 min | 0.00 / 0.09 | 0.01 / 0.12 | 0.00 / 0.00 |
| +15 min | 0.02 / 0.14 | 0.06 / 0.18 | 0.00 / 0.00 |
| +60 min | 0.12 / 0.37 | 0.13 / 0.23 | 0.00 / 0.05 |
The mean mark move per fill is +0.02 to +0.06c at every horizon and size, so a single fill barely moves the mark. The drift over 60 min matches a slow average catching up. The gap is a reporting difference (about 1.0-1.5k below book value). It is not a bottleneck, and any lever that acts on the mark through trading would be manipulation: **none**.

## (d) Pass-through after 16:26 (Polymarket move between consecutive snapshots, tournament mid from before the move)
| move | horizon | moves (markets) | pass-through: median / mean / regression | Polymarket kept (median) |
|---|---|---|---|---|
| >= 2c | +15 min | 11 (4) | 0.14 / 0.53 / 0.48 | 1.01 |
| >= 2c | +60 min | 10 (3) | 0.23 / 0.61 / 0.49 | 1.12 |
| >= 1c | +15 min | 30 (16) | 0.11 / 0.31 / 0.46 | 1.00 |
| >= 1c | +60 min | 28 (14) | 0.12 / 0.45 / 0.51 | 1.00 |
After 16:26 there were only 11 moves of 2c or more, in 4 markets. The medians (0.14 at 15 min, 0.23 at 1 h) stay inside the 0.14-0.29 estimate. Regression and mean (about 0.5) are pulled up by a few large moves that passed through fully. Updated estimate: typical 0.1-0.25, with a fat tail of full pass-through. The sample is too small to tighten it.
