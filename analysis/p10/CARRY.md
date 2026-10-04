# CARRY: value market making at the OUTCOME, per day (Package 10, analyst J)

Script: `analysis/p10/J_carry_mm.py` (read-only on `/home/claude/snap03`, about 8 s). The full output is reproduced by running it.

## Data and method

- **Coverage.** `fills.csv` starts at **1 Oct 16:02 UTC** and ends at 3 Oct 22:47. There are no fills for 28-30 Sep: md.sqlite has only 2 snapshot cycles on 28 Sep and none on 29-30 Sep. **1 Oct is a partial day of about 8 h.** 2 Oct is the only full day on which the bot had cash.
- **Valuation.** A bid (bought YES) earns `qty x (p - price)` and an ask (sold YES) earns `qty x (price - p)`.
  - `p_final` is the latest Polymarket reference for the eid, race-scaled. It comes from `H_outcome.load().p0`.
  - `p_fill` is the race-scaled reference in the last snapshot at or before the fill.
  - `p_24` is the reference at fill time + 24 h. When that falls after 3 Oct 22:47, the latest reference is used, so `p_24` equals `p_final` for 3 Oct fills.
  - `_k` versions apply H's calibration from `H_outcome.calibrate`: `p' = logistic(0.9 * logit p)`, re-normalised per race, so favourites resolve less often than priced.
- **NO-price rows** are mapped back to YES terms with I_mmval's rule, against `quote_price`, or against the snapshot mid when `quote_price` is missing. 2,679 rows were flipped.
- **Classification (fills.csv has no purpose column):**
  - `order_notes.json` flags `take` and `arb` mark takes and arbitrage. These flags exist only from 2 Oct 22:52 on.
  - `our_side = '?'` with no `quote_price` marks an order the bot did not track as a quote. These are labelled **untracked**: takes, arbitrage, unwinds or pre-restart orders. Their side is inferred from position-quantity changes in the `positions` table where possible, but that table starts on 2 Oct 11:22. Only 96 of 940 sides could be inferred. **844 untracked fills (259k shares, 214k of them on 1 Oct) have no side and are excluded.**
  - Everything else (`our_side` is bid or ask and a `quote_price` exists) is counted as **maker**. Before 2 Oct 22:52, a small number of takes may be counted as maker fills, because the journal TAKE lines only start on 3 Oct 08:27.
- **Side classes (price after mapping to YES):**
  - value: bid at 85c or more, or ask at 15c or less
  - anti: ask at 85c or more, or bid at 15c or less
  - middle: 15-85c, both sides
- **Cash** is the gross cash a fill commits: `price x qty` for a buy and `(1 - price) x qty` for a short. It is not netted against fills that close an existing position, so it is an upper bound on new capital.

## (1) Maker fills at the outcome (`p_final`), by day and side class

| day | value sides EV | value cash | value shares | anti sides EV | middle EV | middle cash | all maker EV |
|---|---|---|---|---|---|---|---|
| 1 Oct (16:02-24:00) | **+1,413** | 39.4k | 43.6k | -449 | +630 | 101.3k | +1,594 |
| 2 Oct | **+4,324** | 78.8k | 87.7k | -1,022 | +1,430 | 93.8k | +4,733 |
| 3 Oct (tilt mode, cash-gated) | +179 | 3.4k | 3.7k | -392 | -84 | 8.4k | -297 |
| **total** | **+5,916** | 121.6k | 135.0k | -1,863 | +1,976 | 203.4k | +6,029 |

## (2) Takes, arbitrage and untracked fills (`p_final`)

| class | value | anti | middle | total |
|---|---|---|---|---|
| take (2-3 Oct) | +2,014 | 0 | +403 | **+2,417** (7.6c/share, 8% per $) |
| arb (2-3 Oct) | +1,344 | -1,470 | +275 | +149 (the set-level arbitrage nets out at the outcome) |
| untracked with a known side (96 fills) | +119 | -618 | -206 | -705 (mostly 3 Oct: unwinds against Polymarket) |

## (3)-(5) VALUE-MODE rule: keep only the maker value sides in the tails and the middle two-way

| day | EV `p_final` | EV `p_fill` | EV `p_24` | EV `p_final` k0.9 | gross cash | shares | c/share | EV per $ |
|---|---|---|---|---|---|---|---|---|
| 1 Oct (~8 h) | +2,043 | +2,392 | +2,152 | +1,317 | 140.7k | 246k | 0.83 | 1.5% |
| 2 Oct | **+5,754** | +5,933 | +6,159 | +4,194 | 172.6k | 272k | 2.12 | 3.3% |
| 3 Oct | +95 | +99 | +95 | +168 | 11.8k | 23k | 0.41 | 0.8% |

The value-side fills alone earn 3.2-4.9c per share and 3.6-5.5c per $ of cash (all days: 4.38c per share, 4.9% per $). Middle fills earn 0.3-0.8c per share and 0.6-1.5% per $ of gross cash. The middle's gross cash greatly overstates its capital, because two-way fills largely net. The rule would have dropped the anti sides, which lost -1,863: -2.2c per share and -25% per $.

## (6) Concentration of maker value-side EV (+5,916)

- **Top 10 markets: +3,318 (56%)** of the 102 markets with value-side fills.
  - Dem U.S. House +1,166 and Rep U.S. House +574.
  - Hawaii Gov (Rep) +252, Michigan Gov (Dem) +226, New Mexico Gov (Rep) +209, MN-02 (Dem) +206, Georgia Sen (Rep) +193, North Carolina Sen (Rep) +173, South Dakota Sen (Rep) +161, MN-02 (Rep) +157.
- **Headline legs (U.S. House or Senate control): +1,741 (29%)** on 37.6k shares and 33.4k cash. All of it is in the U.S. House legs: no U.S. Senate value fills.
  - 1 Oct: +671 of +1,413.
  - 2 Oct: +1,069 of +4,324.
  - At k 0.9 the headline legs keep only 386 on 1 Oct and 671 on 2 Oct.
- **Rest, excluding the headline legs and the top 10: 1 Oct +641, 2 Oct +1,826, 3 Oct +131.**

## (7) Sensitivity to the probability used (maker, c/share, all days)

| side class | p_final | p_fill | p_24 | p_final k0.9 | p_fill k0.9 | p_24 k0.9 |
|---|---|---|---|---|---|---|
| value | 4.38 | 3.94 | 4.35 | 2.84 | 2.33 | 2.80 |
| middle | 0.49 | 0.77 | 0.62 | 0.45 | 0.72 | 0.58 |
| anti | -2.18 | -1.99 | -2.26 | -0.54 | -0.32 | -0.62 |

- Which reference is used barely matters for the value sides: the edge existed at fill time and was not later drift.
- Calibration k 0.9 removes about 35% of the value-side edge (about 40% at fill time).
- The middle is more favourable at fill time than at the outcome. Polymarket moved against our middle fills by about 0.3c per share afterwards.
- Under k 0.9, the value-mode EV is 1 Oct +1.3k (about 8 h), 2 Oct +4.2k and 3 Oct +0.2k. The maker value sides alone are 1 Oct +0.70k, 2 Oct +3.0k and 3 Oct +0.14k.

## Exchange flow proxies by day

| day | snapshot cycles | best bid/ask changes | position rows | mark steps (current_price changes) | mark steps of 1c or more |
|---|---|---|---|---|---|
| 28 Sep | 2 | 0 | - | - | - |
| 29-30 Sep | 0 | - | - | - | - |
| 1 Oct | 156 | 18,190 | - (positions table starts 2 Oct 11:22) | - | - |
| 2 Oct | 948 | 63,011 | 20,728 | 18,044 | 159 |
| 3 Oct | 1,208 | 57,684 | 31,737 | 27,260 | 189 |

The 3 Oct book-change and mark-step counts are as high as or higher than 2 Oct. **The flow did not dry up on 3 Oct. Our value-side fills collapsed (3.7k shares against 87.7k) because the bot had no cash and was in tilt mode.**

## (8) Verdict

- **Yes: 0.6-1.0k a day of value-MM carry is supported, and the data show more than that when the bot had cash.**
  - 2 Oct, the full day with cash: the maker value sides earned **+4.3k at the outcome**, +3.0k at k 0.9. Keeping the middle (value-mode rule) gives +5.8k, or +4.2k at k 0.9.
  - 1 Oct, about 8 h: +1.4k on the value sides (+0.7k at k 0.9) and +2.0k under the value-mode rule.
- **The cost is cash locked until the outcome.**
  - The value sides earned about **4-5c per $ committed**, or about 3c at k 0.9. So **each 1k of carry needs about 20-25k of new cash**, or about 30-35k at k 0.9.
  - On 2 Oct the value sides committed **79k of gross cash** across 88k shares. The value-mode rule including the middle committed 173k gross, but much of the middle nets.
- **The 1.0k/day assumption (the base of the 29% P(at least 150k) claim) holds only if about 20-35k of fresh cash a day is available for the value sides.** With the cash-gated 3 Oct book, the same rule earned only +0.1-0.2k.
- **About 30% of the value-side EV came from the two U.S. House legs** (Dem bid at about 0.90, Rep ask at about 0.13), which can be filled in size only a few times. **The ex-headline value sides earned 0.7k on 1 Oct (about 8 h) and 3.3k on 2 Oct**, still above 1k a day at k 1.0.
- **Caveats:**
  - Only about 1.3 days are in the sample.
  - Before 2 Oct 22:52, some fills classed as maker may be takes.
  - 844 untracked fills (259k shares, mostly 1 Oct) have no known side and are excluded.
  - Gross cash overstates the capital where fills closed earlier positions.
