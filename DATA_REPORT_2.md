# Data report 2 (Analyst): 2026-10-01 16:00 to 2026-10-02 08:14 UTC

Data: journal (16 h of trading), fills.csv (1,813 fills), market_data.sql (290 live snapshots, 2-4 min apart; only 4-8 per hour during 04-06h), status.json at 08:14:50.
Scripts are in `analysis/`. They use the standard library only (no pandas). `data2.py` loads the data; set `DATA2=<dir>` to point it elsewhere. Each script's docstring says what it computes.

Scripts by section: §1 `hourly.py`, `valuation.py`, `valuation_level.py`; §2 `night.py`; §3 `outage.py`; §4 `coverage2.py`; §5 `markout2.py`; §6 `competition2.py` (`AT_BEST=1`); §7 `deploy.py`; §8 `rates.py`; §9 `simparams2.py`.

**Attribution.** The bot never recorded the side of 827 fills (`our_side` = `?`). `data2.fills_reconciled()` gives 809 of them a side from the journal quote line in force at the time. It gives the other 18 a side from the snapshot `position` column. For an unrecorded fill, the YES price is whichever of `fill_price` and `1 − fill_price` is nearer the book: these fills come in at either price.
- Fill-based positions match the snapshot positions to within 1,500 shares in total at 08:13. Before, Run B's approach left 5,000 shares unmatched.
- Fill-based P&L at the final mid is **+1,618**. The exchange's figure is **+1,498**.

## Top findings
1. **The equity is real, and it is small: +1,498 (+1.5%) at 08:14. Rank was 102 of 711** (80-102 since 18:00; 299 of 524 during a valuation dip at 22:04).
   - The evening made +547 (16:00-22:48). The night made +980 while mostly in reduce-only, largely as the exchange's valuation caught up (finding 2).
2. **There is no fee. The exchange marks open positions at a sticky price, not the current tournament mid.**
   - The gap between fill-based P&L at mid and the exchange's value peaked at −1,100 to −2,400 in the evening.
   - It closed to −107 by 08:13, after 587k shares had traded, which bounds any fee at ≤0.02c per share.
   - On fill-free intervals the account moves only 0.17× what mid marks predict.
   - The pattern fits valuation at the last traded price (§1).
3. **Profit = sweeps − fills at or through our own fair value.**

   | Fills | Count | Shares | P&L at mid +60 min |
   |---|---|---|---|
   | Sweeps (>3c edge) | 109 | 57k | **+1,704** |
   | ≤ −1c edge | 365 | 119k | **−1,130** |
   | Everything else | | | about +1,500 |

   - Dem U.S. Senate alone lost −241, on 20.5k shares bought 1.5-2c above fair value at 23:12-23:17. The old race-inventory skew (race −5,930 from Rep U.S. Senate) put the Dem bid at 0.645-0.65 vs a fair value of 0.63, for 8.6k shares; R2 changes the skew.
   - We now hold **7,335 complete sets of U.S. Senate (Dem + Rep): 7.3k of dead capital.**
4. **Reduce-only cost about 0.7-1.7k.**
   - 6.8 h of reduce-only between 22:48 and 08:08 earned −6 edge/h, against +246/h in the 19:00-22:48 baseline.
   - The 2.4 night hours that were *not* in reduce-only earned +235 edge/h, the same flow as the evening.
5. **The exchange was degraded 04:20-06:30** (read timeouts, 500/503, 14 failed cycles). The 07:32-07:36 outage was a crash loop: the bot raised on a plain-text error body about once a second for 2.5 min. It cost nothing measurable: 1 fill, and no orders were resting at 07:35.
6. **Coverage is limited by book downloads, not by the markets.**
   - 83-179 unpriced markets per hour had two-sided books ≤30c wide with Polymarket within 3c ("R5-eligible", their spread 1.1-2.3c).
   - After the 08:08 restart, priced coverage rose only from 30 to 101 in 6 min (about 12 per minute). R5 needs a downloaded book (`ex.book`), and books are fetched at most 30 per cycle from the *spare* request budget, which the startup writes consumed (19 "deferred" lines in 6 min).
7. **We post behind the best; we are not chased.**
   - 72% of the prices we post are behind someone at the next snapshot.
   - Of the 25% we post at or inside the best, only 17% are undercut by the next snapshot, after a median of 151 s, and 70% are never undercut before we move ourselves.
   - Other quoters sit a median 0.4c (bid) and 0.7c (ask) from our fair value. 27-33% sit *through* it.
8. **Books widened a little at night, with no "last bot standing" window.**
   - Spreads ≤1c fell from 65-70% (20-21h) to about 50% (22-03h) and 38-46% (04-07h, partly degraded data).
   - Books >3c wide rose from 2-3% to 8-18%. The median spread stayed at 1.0-1.5c.
9. **Tournament prices drifted away from Polymarket overnight.** The median |mid − Polymarket| was 0.8c at 16-19h and 1.5-2.0c at 23-08h. The gap's half-life is about 13.5 min over the whole period (5 min on day one), and the persistent per-market bias has sd 1.75c.
10. **409s are slow batches, not rate limiting.**
    - 23-50% of 20-order batches failed with 409 in *every* hour, including 05h at 0.4 writes/min.
    - 429s came only in bursts: the open, 19:02, and 08:00-08:01 after the old code posted about 255 reduce-only orders.
    - The estimated sustained write rate was 9-12 per minute, with peak minutes of 25-40, and drew no 429. The 30 writes/min budget is supported; there is no evidence it must be lower.
    - The new code recovered 17 orders whose responses were lost, and no fill went unrecorded after 08:08 (0 of 17, vs 21 of 112 in the hour before).

## 1. P&L, rank and the exchange's valuation

Equity by hour (real; `account_value − locked` before 05:03 while the kill switch was "detecting", and the value as is after that). Rows are hours, by the end-of-hour summary line.

| Hour | Real equity | Change | Fills | k sh | Edge c | Edge | P&L mid +60 | of which sweeps | Reduce-only | Resting | Priced |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 16 | 101,179 | +1,179 | 246 | 150 | +0.54 | +788 | +1,329 | +919 | 0% | 61 | 44 |
| 01 17 | 100,101 | −1,078 | 210 | 97 | +0.57 | +552 | +348 | +476 | 0% | 75 | 57 |
| 01 18 | 100,767 | +666 | 207 | 70 | +0.69 | +476 | +206 | +101 | 0% | 87 | 62 |
| 01 19 | 100,427 | −340 | 128 | 40 | +0.20 | +80 | +104 | +14 | 0% | 92 | 70 |
| 01 20 | 100,463 | +36 | 123 | 35 | +0.92 | +315 | +126 | 0 | 0% | 115 | 78 |
| 01 21 | 100,452 | −11 | 105 | 25 | +1.56 | +384 | +258 | +134 | 0% | 115 | 82 |
| 01 22 | 100,544 | +92 | 111 | 32 | +0.44 | +133 | +100 | +3 | 31% | 64 | 64 |
| 01 23 | 101,043 | +499 | 153 | 44 | −0.63 | −255 | −247 | +2 | 55% | 70 | 88 |
| 02 00 | 101,361 | +318 | 134 | 29 | +0.80 | +215 | +155 | +86 | 64% | 75 | 101 |
| 02 01 | 101,188 | −173 | 96 | 23 | +0.27 | +62 | +389 | 0 | 75% | 83 | 123 |
| 02 02 | 101,598 | +410 | 84 | 21 | +2.16 | +441 | −91 | −14 | 58% | 82 | 95 |
| 02 03 | 101,671 | +73 | 65 | 7 | −0.66 | −46 | +14 | 0 | 100% | 55 | 115 |
| 02 04 | 101,587 | −84 | 12 | 1 | −0.13 | −1 | +6 | 0 | 100% | 26 | 68 |
| 02 05 | 101,622 | +35 | 3 | 0.2 | | −1 | 0 | 0 | 100% | 8 | 44 |
| 02 06 | 101,589 | −33 | 7 | 0.6 | | −7 | +5 | 0 | 100% | 7 | 42 |
| 02 07 | 101,527 | −62 | 85 | 9 | +1.02 | +87 | n/a | | 100% | 50 | 109 |
| 02 08 | 101,498 | −29 | 44 | 4 | +2.39 | +91 | n/a | | 0% | 90 | 67 |

Rank (2-hourly summaries): 80/337 at 18:01; 92/447 at 20:00 (real 100,427); **299/524 at 22:04** (99,461); 93/577 at 00:06 (101,043); 98/612 at 02:00; 90/647 at 04:00; 102/711 at 08:08 (101,527).
The leaderboard uses the same valuation as the account, so the transient dip in real equity at 22:04 cost 200 places for 10 minutes.

**The gap question (+1,658 fill-based vs +475 on the exchange at 20:18).** `valuation_level.py` computes `residual = real equity − (100,000 + cash from fills + Σ position × mark)` at every snapshot.

| Time | 16:34 | 18:13 | 20:00 | 21:17 | 22:08 | 22:54 | 00:06 | 02:45 | 03:53 | 07:49 | 08:13 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Residual at mid | −92 | −1,022 | −1,087 | −1,293 | **−2,395** | −1,139 | −415 | −292 | +10 | +130 | **−107** |
| Shares traded (k) | 90 | 255 | 358 | 400 | 427 | 447 | 495 | 565 | 572 | 576 | 587 |

- **Not a fee.**
  - The residual recovered by +1,000 while another 140k shares traded.
  - The final −107 over 587k shares bounds any fee at ≤0.02c per share.
  - The residual is also flat after 05:03, when the value is used as is: corr 0.07 with locked cash, so there is no leftover locked-cash bug.
- **Not tournament mid, bid, ask or liquidation marks.**
  - On 25 fill-free intervals the account moved with RMS 44, while mid marks predicted RMS 82. The regression slope of the account change on the mid-based change is 0.17 (bid 0.06, ask 0.14, liquidation 0.05); under the exchange's own rule it would be 1.
  - On 242 intervals with fills, the account booked only 0.13-0.30 of the fills' edge against the end-of-interval mid.
  - Changing the mark rule shifts the level by at most ±400 and never removes the swings.
- **The fit is a sticky per-market valuation price, most likely the last traded price.**
  - One cycle failed with `CONFLICT: holdings cannot be valued because one or more exchanges have no valuation price`, which only makes sense for a last-trade-type price.
  - Edge sits unrecognised until positions close or the market trades near mid. That happened overnight as reduce-only closed positions.
  - Our own last fill as a stand-in for the last trade fits no better than mid, because we see only our own trades.
  - **Owner: confirm the rule with SIG.**
- **Consequences:**
  - Leaderboard equity lags our true mark by up to about 2.4k when inventory is large (64k gross shares at 22:08).
  - A 10-minute dip can cost 200 places. Small books' last prints, including sweeps through *our* quotes (the sweeper's price becomes the mark of our new position), move our score.
  - Flat inventory = score equals realised P&L.

Fill-based P&L by market at the 08:13 mid (`hourly.py`):

| Group | Markets |
|---|---|
| Best | Dem U.S. House +582 (38.6k sh, edge +1.43c), Rep Texas Senate +113, NH Senate Rep +76 / Dem +71, Rep MI Senate +70 |
| Worst | Dem U.S. Senate −323 (the skew trades), Rep U.S. House −127 (short 8,346 at a 0.13 mid), Rep U.S. Senate −83 (124k sh at +0.03c: churn), Dem FL Gov −50, Dem NH Gov −50 |

## 2. The night on the old code (`night.py`)

| Period | Hours | Avg resting | Fills/h | k sh/h | Edge c | Edge/h | P&L mid +60/h |
|---|---|---|---|---|---|---|---|
| Evening baseline 19:00-22:48 | 3.8 | 105 | 119 | 34.2 | +0.72 | +246 | +131 |
| Night, not reduce-only | 2.4 | 115 | 132 | 36.2 | +0.69 | +235 | −75 (Dem U.S. Senate skew trades at 23h) |
| Night, reduce-only | 4.7 | 53 | 78 | 11.2 | −0.06 | −6 | +34 |
| Reduce-only + exchange degraded 04:20-06:30 | 2.1 | 17 | 2 | 0.2 | −0.99 | −2 | +1 |
| Outage 07:32-07:46 | 0.2 | 21 | 16 | 1.2 | +0.46 | +5 | +10 |

- Reduce-only lines: 137 of 314 summary lines. The first was at 22:48, and every line from 03:00 to 08:08 was reduce-only.
- Resting orders fell to 7-26 in 04-06h, then rose to about 255 at 07:59: reduce-only orders in 160 markets.
- **Forgone: about 730 at mid +60, or about 1,700 at edge, over 6.8 h**, against the baseline.
- The flow was there: hours outside reduce-only matched the evening (+235 vs +246 edge/h).
- Reduce-only trades went *through* fair value (edge −0.06c; Rep U.S. House bought at 0.125 vs fair value 0.088 at 07:51-07:54). Reducing "worst case" was paid for in edge.

## 3. The outage (`outage.py`)

| Time (UTC) | What happened |
|---|---|
| 04:20-06:30 | The exchange was degraded: read timeouts (38 in total, 36 of them in 04-06h), 500/503 on books and bulk prices, 17 failed cycles. 2-8 snapshots per hour. Some books read as one-sided (12-43 per snapshot): treat those as a data artefact. |
| 07:32:28 | 500 on a cancel. Then `load_markets` got a non-JSON error body: `ApiError(... err.get ...)` raised AttributeError, giving 70 "unexpected error" plus failed cancel-alls at about 1/s until 07:34-07:36 (plus 6 failed cycles with `500 ?`). |
| 07:35 | Summary line shows 0 resting. One fill at 07:35:44 (Rep FL-16 ask 92 sh, +1.8c). Quoting resumed by 07:37: 16 resting, then 28-68 by 07:54. |
| 07:57 | Two Polymarket TAKEs after recovery: Rep NH Gov 1,111 @0.90 vs Polymarket 0.96, Dem NC Senate 1,104 @0.905 vs 0.955. |
| Cost | About 0: 1 fill, and real equity moved +34 between 07:27 and 07:37. |

What the new code should still handle:
- It now tolerates plain-text error bodies (`mm_live.py` line 715).
- **It still retries a failing cycle at the normal cadence, with no exponential backoff** (`wait_for_next_cycle` is unchanged), and cancel-all is retried every cycle until it succeeds. Add a backoff of 1→2→4→…30 s on consecutive failures, so an outage does not burn the request budget and invite 429s at recovery.
- After recovery, check the budget before mass re-posting. At 07:59 the old code posted about 255 orders and drew 429s at 08:00 and 08:01.

## 4. Coverage (`coverage2.py`)

Snapshot averages per hour. "R5-eligible" means: unpriced, two-sided, ≤30c wide, with Polymarket within 3c of the raw mid.

| Hour | Priced | Quoted | Unpriced, R5-eligible (their spread) | Unpriced, gap >3c | No reference | Held but unpriced |
|---|---|---|---|---|---|---|
| 16 | 44 | 44 | 179 (2.3c) | 4 | 6 | 24 |
| 18 | 61 | 61 | 164 (1.4c) | 7 | 4 | 62 |
| 20 | 77 | 77 | 146 (1.1c) | 8 | 5 | 74 |
| 22 | 64 | 53 | 144 (1.5c) | 23 | 6 | 98 |
| 00 | 98 | 68 | 108 (1.5c) | 26 | 5 | 89 |
| 01 | 123 | 82 | 86 (1.6c) | 24 | 4 | 73 |
| 03 | 116 | 62 | 83 (1.4c) | 33 | 6 | 79 |
| 05 | 44 | 19 | 110 (1.8c) | 43 | 6 | 128 |
| 07 | 92 (journal 109; 162-171 by 07:56) | 42 | 109 (1.5c) | 30 | 5 | 98 |
| 08 (new code) | 63 | 61 | 138 (1.2c) | 30 | 6 | 110 |

- **Why markets went unpriced:**
  - The journal logs no reasons.
  - One-sided books and spreads >30c are about 0, except in the degraded hours.
  - No reference: 4-6 markets.
  - Mid more than 3c from Polymarket: 4-46 markets, growing at night as prices drifted (§6).
  - Everything else, 83-179 markets, was two-sided and tight. These are the depth (<200 shares) or staleness cases.
- **How priced coverage behaved on the old code:**
  - It grew from 44 to 123 by 01h, then fell in the degraded hours. It reached 162-171 at 07:56, when reduce-only made few writes and books were current.
  - Hourly correlation of priced count with quote changes: 0.05, so the write load does not explain it hour by hour.
- **Why only 87/237 were priced at 08:13 after the deploy:**
  - R5 (`thin_book_prices`) needs `ex.book` (a downloaded full book), `verified` within `book_stale`, and a liquid Polymarket price (225/229).
  - After a restart every `ex.book` is None. `books_to_fetch` downloads at most `min(30, budget_left − budget_reserve 20)` per cycle.
  - The startup writes took the shared 80/min budget: "request budget: 10-22 of 10-34 order changes deferred" on 19 lines in 6 min.
  - Burst mode (08:08:51-08:10:59) also limited quoting to the 40 biggest markets.
  - Result: priced went 30 → 32 → 52 → 74 → 87 → 101 (08:14:41); status.json shows 106 at 08:14:50. That is about 12-14 markets/min, which projects to 190-200 by about 08:20. 138 R5-eligible markets were still waiting in the 08h snapshots.
- **Fix:**
  - R5 needs only best bid/ask, which `bulk_prices` returns for 100 markets per request. Let R5 use `last_tops` when `ex.book` is None, or download all books before the first quotes on a restart (237 reads, about 3-4 min).
  - Rule out a permanent cause after 08:20 from the live status (`markets_priced`).

## 5. Edge and markout (`markout2.py`)

Markouts are in c/share and positive is good for us. The "fv" markouts use the bot's fair value from journal lines (logged on every quote change). The "mid" markouts use snapshots.

| Group | Fills | Shares | Edge | fv +1m | fv +5m | fv +15m | fv +60m | mid +5m | mid +15m | mid +60m | P&L mid +60 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| All | 1,813 | 587k | +0.59 | +0.61 | +0.54 | +0.54 | +0.54 | +0.20 | +0.21 | +0.37 | +2,099 |
| Headline | 168 | 204k | +0.26 | +0.24 | +0.25 | +0.27 | +0.30 | +0.16 | −0.11 | +0.19 | +382 |
| Race ≥500 sh | 249 | 233k | +0.80 | +0.88 | +0.77 | +0.76 | +0.73 | +0.08 | +0.29 | +0.36 | +787 |
| Race <500 sh | 1,396 | 151k | +0.70 | +0.69 | +0.57 | +0.56 | +0.59 | +0.42 | +0.52 | +0.64 | +930 |
| Edge ≤ −1c | 365 | 119k | −1.78 | −1.77 | −1.77 | −1.72 | −1.64 | −0.80 | −1.14 | −0.97 | −1,130 |
| Edge >3c (sweeps) | 109 | 57k | +4.69 | +4.71 | +4.59 | +4.63 | +4.68 | +2.67 | +2.82 | +3.26 | +1,704 |
| Side inferred | 809 | 249k | +0.36 | | | | +0.39 | +0.18 | +0.10 | +0.20 | +494 |

- **Adverse selection against our own fair value is about 0.** fv markouts stay at the edge out to 60 min.
- **Against the tournament mid we keep only about a third of the edge** (+0.2c of +0.59c). The book disagrees with our Polymarket-anchored fair value (persistent bias, §9) more than it moves against us.
- **By hour**, edge ran +0.2 to +1.6c in the evening, −0.57c at 23h (the Dem U.S. Senate skew trades), +2.1c at 02h, and was negative in 03-06h (reduce-only exits through fair value).
- **Markets that lose:**
  - Dem U.S. Senate: −1.05c edge, −241 (skew trades).
  - Rep U.S. Senate: +0.03c, −101.
  - Dem FL Gov: −53.
  - Dem MN-01: +1.4c edge, but −6.8c at mid +5m: picked off.
  - Rep U.S. House: +2.15c edge vs fair value, −0.44c at mid.
  - RI Senate takes: +5c against Polymarket, −1c against the tournament.
- **After Polymarket moves:**
  - Only 83 moves ≥1c appeared in the journal in 16 h (5/h over 45 markets).
  - 56 fills came within 15 min of one. 18 were on the stale side, filled a median 291 s after the move (p25 191 s), at +0.84c at mid +15m (16 fills, 2-15 min after the move). That is not adverse.
  - **Polymarket-driven pick-offs were not a cost in this period.**

## 6. Competition (`competition2.py`; best prices include our own orders)

| Hour | Median spread | ≤1c | >3c | Median spread of books we don't quote | ...of which >3c | Our quoted sides at best |
|---|---|---|---|---|---|---|
| 16 | 2.0 | 24% | 21% | 2.0 | 22% | 42% |
| 18 | 1.5 | 49% | 5% | 1.5 | 6% | 30% |
| 20 | 1.0 | 70% | 2% | 1.0 | 2% | 24% |
| 22 | 1.5 | 50% | 5% | 1.5 | 6% | 36% |
| 00 | 1.0 | 52% | 8% | 1.5 | 11% | 36% |
| 01 | 1.0 | 54% | 9% | 1.5 | 13% | 32% |
| 03 | 1.5 | 49% | 5% | 1.5 | 6% | 35% |
| 05 (degraded) | 1.5 | 38% | 18% | 2.0 | 19% | 42% |
| 07 | 1.5 | 46% | 6% | 1.5 | 7% | 25% |
| 08 | 1.0 | 59% | 5% | 1.0 | 5% | 24% |

- **Undercut speed.** Of 8,355 posted prices seen in a later snapshot, 72% had someone better at the first sight, mostly because we post 1c from fair value while others sit at 0.4-0.7c.
- For the 2,103 prices posted *at or inside* the previous best:

  | Measure | Value |
  |---|---|
  | Undercut by the next snapshot | 17% |
  | Undercut at all before we moved | 30% |
  | Delay to undercut, p10 / p25 / median / p75 / p90 | 46 / 79 / 151 / 344 / 679 s |

  Delays under about 150 s are below the snapshot resolution.
- **Contested markets:**
  - 221 markets have ≥10 posts, and in none of them were we undercut at first sight less than 25% of the time.
  - Counting only posts at the best (103 markets), 79 markets were undercut <25% of the time. The median is 15%.
  - Rivals do not chase us tick for tick. They sit at their own (tighter) prices.
- **Night.** Books widened modestly: the ≤1c share fell from 70% to about 50%, and >3c rose from 2% to 8-13% at 00-01h. Books we did not quote were wider (median 1.5c).
- **No "last bot standing" window.** Even at 03-07h, with us at 8-55 resting orders, the median spread was 1.5c, so other bots stayed two-sided all night.
- Polymarket anchoring loosened at night: the median |mid − Polymarket| went from 0.8c to 1.5-2.0c.

## 7. Before and after the 08:08 deploy (`deploy.py`)

| | Old code 07:08-08:08 (reduce-only) | New code 08:08-08:15 |
|---|---|---|
| Fills/min | 1.9 (112) | 2.4 (17) |
| Edge | +1.00c (24 fills < −1c) | +2.76c (6 of 17 >3c, 0 < −1c) |
| Fills the bot did not record | 21 / 112 | **0 / 17** |
| Resting (average) | 50 | 90 → 148 |
| Priced | 35 → 162 | 30 → 101 |
| Our quoted sides at best | 25% | 24% |
| Risk | worst case 33.8k, reduce-only | worst case 21.3k → 32.6k; correlated risk 17.1k → **10.6k**, no reduce-only |
| Party delta | −2.6k to −5.5k | −2,237 → −509 |

**What can be seen:**
- R7 lifted reduce-only and R2 flattened the party delta.
- Recovered orders show the lost-response handling working: 17 recovered, 0 unrecorded fills.
- The write budget binds at startup (19 deferred lines).
- Burst mode triggered on a 21-s first cycle, with a median write of 0.4 s.

**What cannot be seen yet:** steady-state coverage, R5 fill quality, skew behaviour after large fills, the P&L effect of any change, and 409 rates (0 in 6 min).

## 8. Write rate, 409 and 429 (`rates.py`)

Writes are estimated from the journal quote lines: one cancel per changed market (cancel-all when both sides change) plus one batch POST per ≤20 new orders in a cycle.

| Hour | Est. writes/min | Peak minute | Batches | 409 batches | 409/batch | 429 |
|---|---|---|---|---|---|---|
| 16 | 11.2 | 33 | 106 | 37 | 35% | 3 (16:07, 16:10, 16:43) |
| 17 | 12.1 | 37 | 124 | 36 | 29% | 0 |
| 18 | 10.2 | 39 | 118 | 30 | 25% | 0 |
| 19 | 9.4 | 29 | 98 | 41 | 42% | 1 (19:02) |
| 20-22 | 7.4-9.7 | 25-28 | 98-113 | 27-32 | 24-33% | 0 |
| 23-02 | 8.3-8.9 | 27-40 | 84-110 | 31-37 | 30-44% | 0 |
| 03-06 | 0.4-5.3 | 4-14 | 6-75 | 3-20 | 27-50% | 0 |
| 07 | 4.5 | 45 | 61 | 14 | 23% | 2 (08:00, 08:01, after the 07:59 burst) |
| 08 (new code, about 6 min) | 4.8 | 50 | 31 | 0 | 0% | 0 |

- **409 is a latency signal, not a rate signal.** About 30% of 20-order batches return 409 REQUEST_IN_FLIGHT at every rate, down to 0.4 writes/min. The client times out at 15 s and retries the same idempotency key while the first request is still being processed. Smaller batches (5-10) or a longer timeout would cut 409s and lost orders. 273 of 419 failed batches had 20 orders.
- **429s came only after bursts.** The open came with about 237 books downloaded plus first quotes. At 07:59, about 255 orders were posted. The general budget was 60-80 requests/min at those times.
- Sustained 9-12 estimated writes/min, with peak minutes of 25-40 and the old 42-60/min total budget, drew **no 429 for 13 hours**.
- **Verdict:** 30 writes/min is consistent with the data. There is no evidence for a lower limit, and weak evidence that short peaks to about 40/min are tolerated when reads are light. Keep 30 sustained, and spend headroom on reads at restart (§4) rather than raising writes.

## 9. Simulator parameters (`simparams2.py`; for tests/strategy_sim.py and tests/scenario.py)

| Parameter | Value (16 h) | Day one (Run B) |
|---|---|---|
| Fill arrival per quoted market-hour | headline 8.0 fills (9,750 sh); busy race (≥20 fills, 23 markets) 6.6 (1,720 sh); quiet race (146 markets) 1.2 (246 sh) | 191 fills/h in total |
| Fill size | headline p25/50/75/95 100/500/1,075/8,000; race 41-50/100/209-257/850-1,250 | median 100 |
| Night vs evening flow | not reduce-only at night: 132 fills/h, edge +0.69c (evening 119/h, +0.72c); flow does not drop at night | |
| Sweeps (>3c through our fair value) | 6.7/h overall, 9.7/h in 16-22h; size p50 100, p90 1,100, max 10,000; edge p50 3.9c, p90 5.5c | about 11/h |
| Polymarket moves (per market-hour) | ≥1c 0.040, ≥2c 0.005, ≥3c 0.0027, ≥5c 0.0004; 96% of 2.6-min intervals unchanged, sd 0.08c | ≥1c 0.05 |
| Tournament − Polymarket gap | bias sd **1.75c** + transient sd 0.95c, AR(1) ρ 0.87 per 157 s → **half-life 13.5 min**; median \|gap\| 0.8c (day) → 1.5-2.0c (night) | 1.19c / 0.58c / 5 min |
| Other quoters vs our fair value | bid: fair value − best other p10/25/50/75/90 = −1.1/−0.3/0.4/1.2/2.2c; ask: −0.9/−0.1/0.7/1.6/2.5c; 27-33% through our fair value | 0.5 / 1.0c |
| Rival undercut delay (when we are at the best) | P(undercut before our next requote) 0.30; delay p25 79 s, median 151 s, p75 344 s; 70% never undercut | not measured |
| Spreads | median 1.0c (day) / 1.5c (night); ≤1c 50-70%; >3c 2-13% (18% in degraded hours) | median 1.5c |
| Write latency | median 0.4 s (new code log); P(20-order batch exceeds the 15 s timeout → 409) ≈ 0.30 at any rate; 429 after bursts of more than about 250 orders or the book download at the open | 1-3 s, 15+ s bursts |
| Lost orders | old code: 18% of fills unrecorded (21/112 in 07h); new code 0/17 with recovery | 42% of shares |
| Outages | degraded 2 h/night (timeouts, 500/503) + 4-min hard outage with non-JSON 500 bodies | |
| Valuation (scoring) | marks follow tournament mid at only about 0.17× on fill-free intervals; model the score as cash + positions at the last trade price | |
