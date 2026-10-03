# Snapshot 2 Oct 20:37 UTC (ops-snapshot-2026-10-02b, a4ff283): tilt, exposure, reduce-only, exits, marks
Scripts: `analysis/poly_bias/snapshot02b.py DATA_DIR` (sections 1-7), `phase0.py DATA_DIR` (exits), `tier2.py DATA_DIR` (unchanged,
ran as is). Data staged from the snapshot branch with git show, not committed. Earlier numbers: PLAN_POLY_BIAS.md sections 0 and 2.

## 1. Tilt s (slope of gap = ref - mid on ref - 0.5, pooled live rows); ex-own drops rows where our quote is the best bid or ask
| bin (UTC) | s | R2 | n | s ex-own | R2 ex-own |
|---|---|---|---|---|---|
| 1 Oct 16-20 | 2.41% | 0.11 | 19,194 | 2.47% | 0.11 |
| 1 Oct 20-24 | 3.35% | 0.45 | 16,486 | 3.45% | 0.46 |
| 2 Oct 00-04 | 4.69% | 0.58 | 19,694 | 4.81% | 0.59 |
| 2 Oct 04-08 | 5.09% | 0.59 | 9,095 | 5.12% | 0.60 |
| 2 Oct 08-12 | 5.44% | 0.68 | 44,426 | 5.52% | 0.69 |
| 2 Oct 12-16 | 5.19% | 0.65 | 41,449 | 5.38% | 0.67 |
| 2 Oct 16-20:37 | 6.34% | 0.65 | 59,540 | 6.51% | 0.66 |
| 08:14-20:37 | 5.74% | 0.65 | 144,041 | 5.88% | 0.66 |

Hourly s on 2 Oct: 08h 5.5, 09h 5.6, 10h 5.4, 11h 5.2, 12h 5.1, 13h 5.2, 14h 5.0, 15h 5.5, 16h 6.2, 17h 6.6, 18h 6.6, 19h 6.1, 20h 6.1.
Reading: flat at 5.0-5.6% from 08:00 to 16:00, then a step up to 6.1-6.6% from 16:00. That is growth, though slower than overnight
(2.4 to 5.1 in 16 h). Own-best rows are 10-15% of rows. Dropping them raises s by 0.1-0.2 points, so our own quotes do not cause the tilt.
tier2.py's quarters (7.1 h each): 2.70% / 4.67% / 5.31% / 6.00%, R2 0.18 / 0.58 / 0.67 / 0.64. Mean |gap| 1.41c rises to 2.45c.

Gap by Polymarket bucket, mean over all rows in the last 4 h (16:37-20:37), with 04:14-08:14 in brackets:
| <5% | 5-15 | 15-35 | 35-65 | 65-85 | 85-95 | >95% |
|---|---|---|---|---|---|---|
| -3.35c (-2.81) | -3.11c (-2.28) | -2.17c (-1.73) | -0.69c (-0.61) | +1.39c (+1.00) | +2.22c (+1.83) | +2.52c (+2.05) |

Who closes the gap (tier2 section 9, whole window): the mid closes -0.01/-0.06/-0.14/-0.22 of the tilt part (0.5/2/4/8 h), so the tilt
part keeps widening, and +0.24/+0.26/+0.28/+0.35 of the residual. Pass-through of a 1c+ Polymarket move is 0.08 in the same snapshot and 0.21-0.23 after 4-8 snapshots.

## 2. Tilt exposure, sum pos x (ref - 0.5) over held positions
| time | exposure | gross | mark change per +1 point of tilt |
|---|---|---|---|
| 08:14 | +15,087 | 22,312 | -151 |
| 20:37 (positions table = snapshots.position, 166 held) | **+32,742** | 45,813 | **-327** |
| 20:37 excluding the U.S. House pair (owner's manual Dem +10,173 / bot's Rep -9,396) | +24,425 | | -244 |

## 3. Reduce-only (journal `realtime |` lines)
| window | lines | REDUCE-ONLY lines | time-weighted | worst case | account | risk (R7) | episodes | median / max length |
|---|---|---|---|---|---|---|---|---|
| 08:14-20:37 | 1,188 | 46% | 41% | 32.2k-81.7k | 100.6k-101.9k | 10.5k-31.1k | 13 closed + 1 open | 16.8 / 72 min |
| 16:26-20:37 | 459 | 83% | 81% | 77.4k-81.6k | 100.6k-101.4k | 23.1k-27.6k | 7 closed + 1 open | 18.4 / 55 min |
After 16:26: worst case / account >= 0.770 on every REDUCE-ONLY line and <= 0.800 on every normal line, so the bot cycles between the 0.8 entry and the 0.77 exit.

## 4. Exits
phase0.py (positions from snapshots.position, FIFO lots), with the 08:14 values in brackets:
| measure | 20:37 |
|---|---|
| open position capital entered toward Polymarket | 88% (76%); against 2% (11%); neutral 4%; no reference 6% |
| by the gap now: toward / against | 88% / 3% (77% / 3%) |
| exited share of entered capital: toward / against | 56% / 96% (65% / 85%) |
| open capital: weighted median age; share older than 6 h / 12 h | 8.3 h (8.8 h); 55% (69%) / 30% |
| position capital / account | 101.4k / 101.0k = 100% (51%) |
| closed lots median holding time | 0.55 h (0.23 h) |
| 15 biggest: capital share, toward share at entry | 46%, 95% |

Exit ratio from fills.csv, replayed from snapshot positions at 08:14 (side from the order note when fills.csv has "?"; sells use NO price):
| window | reducing shares | adding shares | ratio |
|---|---|---|---|
| 08:14-20:37 | 88,589 | 154,865 | 0.57 (ex U.S. House 0.59; U.S. House alone 0.35) |
| 16:26-20:37 | 52,288 | 62,264 | 0.84 (no U.S. House fills) |
7 fills after 08:14 have no side and no note. They fit the owner's manual Dem U.S. House trades: the replay ends at 7,309 against 10,173 in the snapshot. 3 other markets differ by at most 150.

## 5. Marks at 20:36 (positions table, latest row per eid; cash = account_marks value - holdings = 1,107)
| mark | account value | vs exchange |
|---|---|---|
| (a) exchange current_price | 101,004 (reproduces the reply exactly) | 0 |
| (b) tournament best bid/ask (liquidation, top of book) | 101,986 | +982 |
| (c) tournament mid | 102,385 | +1,381 |
| (d) Polymarket | 107,316 | +6,311 |
(b) includes our own quotes. In 4 held markets the best quote on the liquidating side is ours, which flatters (b) a little.
account_marks (first row 11:22): exchange 101,691 vs mid 102,636 (+945) at 11:22; 101,480 vs 101,878 (+398) at 16:05; 101,004 vs 102,393 (+1,389) at 20:36.

## 6. Takes since 08:14 (order_notes take=true, joined to fills.csv; marked at the last snapshot)
39 take orders, 38 filled (42 fills, 24,958 shares). P&L at the take (tournament mid): -122. At the end: Polymarket **+1,480** (+5.93c/sh),
tournament mid **+154** (+0.62c/sh). All takes, whole tournament (takes.py): 57 fills, 34,189 sh; at the end Polymarket +2,031, mid +150.
Arb orders since 08:14: 164 (178 filled legs); takes.py's arb locked total, whole tournament: +3,354. takes.py's reduce-only section finds 0 lines because its regex expects the old journal timestamp format. snapshot02b.py section 3 parses this journal correctly.

## 7. Writes
Window 08:14-15:30: 16 x 429 (last at 15:11:44), 24 burst episodes, 20.2 placements/min. Window 15:30-16:26: 0 x 429, 1 burst, 32.4/min. Window 16:26-20:37: 0 x 429,
4 bursts (median write 5-8 s, the exchange was slow), 16.3 placements/min (peak 180 in one minute). Cancels are not logged. Status: write budget 28/min, rate_limited_total 0.
