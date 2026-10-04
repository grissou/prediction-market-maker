# Package 10, explorer H: the outcome model, the frontier, the risk limits, the leaderboard

Data: `/home/claude/snap03` only (to 3 Oct 22:47 UTC; the 07:30 state of tilt 0.14 and party delta -13.8k is not in the
snapshot). Scripts (all read-only, each under 10 s):

- `analysis/p10/H_outcome.py`: the model, the current book, the sensitivities and the Senate seat check. It can be imported
  (`load`, `simulate`, `settle`).
- `H_frontier.py [N] [rho] [k]`: the frontier and plans (a), (a'), (b) and (c). Environment switches: `H_MAXC` (per-contract cap,
  default 10k), `H_KVAL` (value plans chosen at k under another k), `H_SEATS=1` (Senate control taken from the simulated seat count).
- `H_risk.py`: the bot's own risk numbers next to the ruin numbers, plus the closed-form bloc delta.
- `H_board.py [N] [n_tilt] [n_directional]`: the final leaderboard.

## The model (H-1 in short)

**Probabilities.** Each contract's probability is its latest Polymarket reference (all 229 are fresh at 22:47), scaled to sum to 1
within its race. Eight contracts have no reference and use the tournament mid instead: Alaska Senate, Alaska Governor, CA-22 and
California Governor. We hold 4 of them: Alaska Governor -3000 on each leg, which is a NO+NO set, and California Governor -8 on each leg.

**Correlation.** The model is a Gaussian copula with one national factor F, where F > 0 means a Republican wave. In a state race the
Democrat wins if sqrt(rho)·F + sqrt(1 - rho)·e < Phi^-1(pDem), with rho = 0.45. The U.S. Senate and U.S. House control markets use
rho = 0.85. Independents win independently with probability p_Ind.

**Why rho is 0.45.** With a month to go, a race's two-party vote share is uncertain by about 4-4.5 points (Senate polling error of
about 5 points on the margin, plus drift). The national component is about 2.5-3 points (midterm generic-ballot misses of about 1-4
points on the margin, plus drift). That gives rho = 2.75² / 4.25² ≈ 0.42.

- The owner's "3-4 points national" maps to rho 0.5-0.7. Sensitivities were run at 0.25 and 0.65.
- In logit terms, 1 sd of F shifts a 50/50 race's Democratic probability by sqrt(0.45)·phi(0) / sqrt(0.55) ≈ 0.36 sd. That is about
  ±0.26 logit, or ±6c at the money.
- No regional factor was added: D_corr found pairwise correlation 0.05-0.06 of the cheap sides' price moves, and the race pairs are
  too thin to fit one.

**Calibration margin k.** p' = logistic(k·logit(p)), then re-normalised.

- k = 1.1 is the stated longshot-bias margin: Polymarket 3c resolves 2.1%, 5c resolves 3.9% and 95c resolves 96.2%.
- k = 0.9 is the opposite case.
- The base case is k = 1.0.

**Samples.** 20,000 outcomes.

## THE TABLE

All plans are valued at the outcome with rho 0.45 and k 1.0. Capital is about 100k (liquidation value 99.9k at bid/ask ∓0.25c).

| Plan | E[final] | P(>= 120k) | P(>= 150k) | P(<= 85k) | P(<= 70k) | Capital / turnover | Write cost (orders) |
|---|---|---|---|---|---|---|---|
| 0. Current book held (status 22:47) | **109.0k** | 2.7% | 0.0% | 0.6% | 0.0% | 0 / 0 | 0 |
| (a') Sell the 23 positions with hold-edge < 2% ($8.8k) and redeploy greedily | 110.7k | 12.1% | 0.0% | 1.8% | 0.0% | 9k / 18k | ~28 |
| (a) Max-EV reallocation held to the outcome (cap 10k per contract) | 112.9k | 29.3% | 0.0% | 1.9% | 0.1% | 100k / 127k | ~111 |
| (b) (a) + recycling at mark within 2c of Polymarket (B_mc tilt paths) | 114.9k | 29.8% | 0.0% | 0.9% | 0.1% | 100k / 153k | ~111 + ~25 |
| (c1) (a) core + Rep U.S. Senate YES $20k at 0.35 | 110.5k | 33.8% | 17.4% | 4.3% | 0.0% | 100k / 147k | ~113 |
| (c1) ... sized for P150 20%: Rep Senate $22.5k | 110.2k | 34.4% | 25.6% | 4.4% (cliff, see below) | 0.1% | 100k / 150k | ~113 |
| (c1) ... sized for P150 30%: Rep Senate $30k | 109.3k | 34.9% | 32.0% | **64.9%** | 3.9% | 100k / 157k | ~113 |
| (c1) ... P150 40% | unreachable: the cap is P(Rep Senate) = 35% | | | | | | |
| (c2) Half Rep Senate, half competitive Rep Senate/Governor legs: $35k (20%) | 108.2k | 34.9% | 20.1% | 33.4% | 3.3% | 100k / 162k | ~117 |
| (c2) ... $42.5k (30%) | 107.1k | 35.0% | 30.4% | 47.4% | 12.6% | 100k / 170k | ~117 |
| (c3) Rep bloc, competitive legs 0.28-0.72 (24 races): $95k (20%; 30/40% unreachable) | 97.9k | 36.0% | 20.1% | 43.4% | 33.9% | 100k / 222k | ~140 |
| (c3) Dem bloc, competitive legs: $97.5k (20%; 30/40% unreachable) | 94.9k | 34.2% | 20.1% | 46.9% | 38.1% | 100k / 225k | ~140 |
| (c1') No reallocation: current book - $20k NO+NO sets (2c) + Rep Senate $20k | 106.7k | n/c | 18.5% | 27.5% | 1.6% | 20k / 40k | ~35 |

**The P(<= 85k) cliff.** The cliff on the Rep Senate sleeve sits between $20k and $27.5k. Two constructions of the core give the same
answer at $20k (4.3% and 5.1%) and differ at $22.5k (4.4% and 24.7%). At $25k it is 49% and at $27.5-30k it is 59-65%. **Use $20k as
the ceiling.**

**Under the seat-count model** (`H_SEATS=1`, P(Rep Senate control) 0.448):

| Rep Senate sleeve + (a) | E[final] | P(>= 150k) | P(<= 85k) |
|---|---|---|---|
| $20k | 116.1k | 23.7% | 4.0% |
| $22.5k | 116.5k | 34.9% | 4.0% |
| $30k | 117.6k | 41.8% | 55% |

**Robustness.** I chose the plans at k = 1.0 and valued them at k = 0.8 / 0.9 / 1.1, and also re-ran at rho 0.25 and 0.65. E moves
by -2.5k to +1.0k and the ranking does not change.

## Recommendation (Package 10)

1. **Do (a') now, then turn it into the allocator (a) to (b).** Each step has the best EV per unit of risk.
   - (a') alone is +1.7k for 18k of turnover.
   - Full (a) is +3.9k. It needs:
     - the backstop raised (the sum of maxima reaches 1.12x the account);
     - the party-delta cap replaced (H-2);
     - closing 7 contracts beyond the top-3 depth.
   - Recycling (b) adds about 2k in the mean.
2. **The 150k target can only be reached with a correlated binary bet.** The only +EV one is **Rep U.S. Senate YES at 0.35**.
   - Polymarket has it at 0.355, and the race-level seat count says 0.42-0.46.
   - Size it at **≤ $20k**: P(>= 150k) 17-24%, P(<= 85k) 4-5%, P(<= 70k) ~0, and it costs no EV.
   - Above about $22k it falls off the cliff: P(<= 85k) goes to 50-65%.
   - P(>= 150k) 30% or 40% cannot be had with P(<= 85k) < 10%.
3. **Never** use competitive-race party blocs (c3). They are -EV at the ask and only partly correlated: P(<= 70k) 34-38% just to
   reach P150 20%.

## Ideas

### H-1. Outcome valuation of the current book: 109.0k, and the owner's 108.5k checks out
Mechanism: At the outcome the book is worth cash + Σ YES·win + Σ NO·(1 - win). Marks undervalue it by the tilt. Its spread is narrow,
so the book is a base, not a path to 150k.
Data check: H_outcome.py.
- Cash = account - marks = 256.
- EV = **108,959** with normalised references; 109,001 with raw references. The owner's figure is 108.5k. Marks are 101,017, so the
  gap is +7.9k.
- Distribution: sd 7.5k, p5 94.7k, median 110.3k, p95 119.3k.

| rho | k | E[final] | P(>= 120k) | P(<= 85k) | P(<= 70k) |
|---|---|---|---|---|---|
| 0 | 1.0 | | 0.4% | 0.0% | |
| 0.25 | 1.0 | | 1.3% | 0.1% | |
| 0.45 | 1.0 | | 2.7% | 0.6% | |
| 0.65 | 1.0 | | 5.3% | 1.9% | 0.1% |
| 0.45 | 1.1 | 110.0k | | | |
| 0.45 | 1.2 | 110.8k | | | |
| 0.45 | 0.9 | 107.7k | | | |

- E[final | F] by quantile of F:

| F quantile | 5% (Dem wave) | 25% | 50% | 75% | 95% (Rep wave) |
|---|---|---|---|---|---|
| E[final \| F] | 99.0k | 107.2k | 111.6k | 115.5k | 105.8k |

- The shape is non-monotone. F explains 12% of the variance.
- Biggest EV items: Dem U.S. House long 8876 (+859), Dem NH Governor NO (+370), Rep House NO (+342).
- Biggest negatives: Rep Montana Senate NO (-144), Rep SD Senate NO (-123).

Upside: +7.9k vs marks (+8%) | P(>= 150k) effect: 0% held alone | Ruin / drawdown: P(<= 85k) 0.6%, P(<= 70k) ~0.
End-valuation: Entirely outcome. Marked at the close, the book is worth ~101k - s·35.9k.
Legitimacy: Analysis.
Build: None. A `settlement_ev` status field is ~20 lines (Σ pos × Polymarket at the outcome).
Screen: Data-only (done).

### H-2. The party delta in shares has the wrong sign; replace it with the closed-form bloc delta
Mechanism: `party_delta` = Rep YES - Dem YES shares. It counts a 1c longshot share the same as a 50c share. Under a Gaussian copula,
dE[payout]/dF per Dem YES share is exactly -sqrt(rho)·phi(Phi^-1(pDem)), and the Rep leg is the negative. The weights are:

| p | phi weight |
|---|---|
| 0.5 | 0.40 |
| 0.935 | 0.13 |
| 0.005 | 0.014 |

So one share at 50c counts as 28 longshot shares. The bot blocks Dem-side adding because the share delta is -15.35k (at the 0.15 cap),
yet the book's real outcome sensitivity is Rep-leaning.
Data check: H_risk.py.

| Book | Closed-form bloc delta | MC regression slope | Share delta |
|---|---|---|---|
| Current | +2,553 per sd of F | +2,604 | -15,350 |
| (a) | +2,412 | +2,453 | -13,136 |
| Rep Senate sleeve $22.5k | +23,934 | +23,990 | +54,105 |

Upside: Unblocks the Dem-side value buys the party cap now refuses. It is worth part of (a)'s +3.9k | P(>= 150k) effect: indirect |
Ruin / drawdown: none added; it measures the risk correctly.
End-valuation: Outcome.
Legitimacy: Risk model.
Build: `bloc_delta` in mm_bot.py next to `party_delta` (cycle step 6, ~line 4097). Add `risk_bloc_model: "copula"`, `risk_rho` 0.45,
`risk_rho_ctrl` 0.85, `max_bloc_delta_frac` (cap per sd of F as a fraction of the account), and use them in `party_shift` /
`party_blocks` (~5427). ~40 lines.
Screen: Data-only (closed form vs MC, done); unit test in phase C.

### H-3. Plan (a): the max-EV reallocation held to the outcome
Mechanism: Rank every held position by its hold edge per $ of liquidation value:
- long: (p - bid) / bid;
- short: (ask - p) / (1 - ask).

Rank every book level the same way:
- buy YES at the ask: (p - ask) / ask;
- sell YES at the bid: (bid - p) / (1 - bid).

Fill greedily with ≤ $10k per contract until the 99.9k of capital is used.
Data check: H_frontier.py.
- Book levels where p is beyond the price: $6.6M of collateral, EV 340k. 512 levels have ≥ 2% edge and 63 have ≥ 10%. The deepest are
  the long-tilt crowd's bids: Rep RI Senate bid 0.125 x 45.6k shares (p 0.008, 13.4%) and Rep RI Governor bid 0.135 x 16.7k.
- Held hold-edge: median 7.7%, $-weighted 9.1%. 19 positions ($6.4k) are negative and 37 ($12.5k) are under 5%.
- (a): marginal edge 11.6%, sells $63k and buys $64k.
- Result: E 112.9k, P(>= 120k) 29%, P(<= 85k) 1.9%, P(<= 70k) 0.1%.
- 108 trades. Only 10.4k shares in 7 contracts exceed the top-3 depth; the largest is the Dem U.S. Senate -5000 close against 2,658
  of depth.

Upside: +3.9k (+3.6%) over holding | P(>= 150k) effect: 0 (sd 9.8k) | Ruin / drawdown: P(<= 85k) 1.9%.
End-valuation: Outcome only. Marked at the close, the reallocation is worth about -s x the added tilt exposure.
Legitimacy: Ordinary trading at posted prices.
Build: The capital allocator (explorer I) with this edge-per-$ ranking. It needs H-2 and H-10 first, or the party cap and the 0.8
backstop pin it.
Screen: live_sim `pnl` (Polymarket mark = settlement EV) plus this MC.

### H-4. Plan (a'): the cheap 80% of (a), swap only the dead positions
Mechanism: Sell only the 23 held positions whose hold edge is under 2% ($8.75k: Rep Montana Senate NO, Rep SD Senate NO, Dem Illinois
Governor NO, Dem Minnesota Governor NO, Rep/Ind Nebraska Senate NO, and others). Put the cash into the frontier's best levels (13-14%).
Data check: H_frontier.py (a'): E 110.7k (+1.7k), P(>= 120k) 12%, P(<= 85k) 1.8%, about 28 orders.
Upside: +1.7k for $18k of turnover (9.4% of the turnover) | P(>= 150k) effect: 0 | Ruin / drawdown: as now.
End-valuation: Outcome.
Legitimacy: Trading.
Build: Config plus a one-shot allocator pass (I). It is also a manual list for the owner.
Screen: Data-only (done).

### H-5. Plan (b): recycling on convergence is worth about +2k, not more
Mechanism: When a held position's mark comes within 2c of Polymarket, sell it and put the cash in at the then-current edge. The new
edge is (a)'s marginal edge x s(t) / 0.11.
Data check: H_frontier.py (b). Settings:
- 4,000 tilt paths fitted to B_mc's quantiles (median 0.11 → 0.29 at 7 d → 0.215 at 30 d);
- per-contract dispersion exp(0.5·eta), AR(1) with phi 0.7.

Results:
- Recycled $: mean 12.7k per path, p90 32.8k.
- Gain: mean +2.07k, p10 +0.05k, p90 +4.8k.
- E 114.9k, P(<= 85k) 0.9%.

The gain is small because the tilt mostly widens. B puts P(s < 0.12 at the close) at 0.2-0.5, so convergence is rare and comes late.
Upside: +2k | P(>= 150k) effect: 0 | Ruin / drawdown: none.
End-valuation: Outcome.
Legitimacy: Trading.
Build: The allocator's "sell when |mark - p| < 2c and the release buys > 2 points more edge" rule (~30 lines in I's allocator).
Screen: live_sim with `_world_tilt` paths; this MC.

### H-6. THE 150k LEVER: Rep U.S. Senate YES at 0.35, ≤ $20k on top of the value core
Mechanism: The cheapest exposure to the national factor is the control binary. It pays 2.86x and it is +EV:
- at Polymarket, 0.355 vs 0.35;
- the race-level seat count says P(Rep control) 0.42-0.46 (H-8).

The value core stays at a high level whichever way the Senate goes. Its E given a Rep loss is 112.1k (91.8k for the reduced core with
a $20k sleeve), so with a bounded sleeve the downside branch stays above 85k.
Data check: H_frontier.py / H_risk.py. The Rep U.S. Senate ask is 0.35 x 185,516 shares ($65k of depth).

| Sleeve | E | P(>= 150k) | P(<= 85k) | P(<= 70k) |
|---|---|---|---|---|
| $10k | 111.7k | 0% | 2.4% | |
| $20k | 110.5k | 17.4% | 4.3% | 0.0% |
| $22.5k | 110.2k | 25.6% | 4.4-24.7% | |
| $30k | 109.3k | 32.0% | 64.9% | |

- The maximum P(>= 150k) is 35% (= P(Rep)). 40% needs the seat model to be right.
- Robust across k 0.8-1.1 and rho 0.25-0.65: the $20-22.5k sleeve gives P150 22-26% and P(<= 85k) 3-9%.

Upside: Costs no EV (sleeve EV +$286 at Polymarket, about +$5k at seat-model odds) | P(>= 150k) effect: 0 → 17-24% | Ruin / drawdown:
P(<= 85k) 4-5% at $20k, a cliff above $22k. In a Rep-loss world the account is ~92k.
End-valuation: Outcome. If marked at the close, its mark follows Polymarket news, which is symmetric.
Legitimacy: An ordinary directional position at a posted price.
Build: A manual order or a `sleeve_*` block:
- `sleeve_eid`, `sleeve_usd` 20000, `sleeve_max_price` 0.36;
- counted in risk as `basket_risk_split` does (stress at its $ cost, outside the party / bloc caps);
- ~60 lines reusing the Package 9 basket plumbing.

Screen: Cannot be screened by live_sim (binary event). The MC here is the screen.

### H-7. Party blocs in competitive races are dominated: do not build them
Mechanism: Buying one party's legs across the 24 competitive races (0.28-0.72) gives correlated payoffs. The prices are near fair, so
every dollar pays the spread. With rho 0.45 the races are only half one bet.
Data check: H_frontier.py (c3).
- Dem sleeve EV -$5.9k per $100k; Rep -$2.5k.
- Reaching P150 20% takes $90-100k: E 95-98k, P(<= 70k) 33-39%.
- 30% and 40% cannot be reached with ≤ $100k.
- Even at rho 0.65 it takes $67.5k for 20%, with P(<= 70k) 29-33%.
- Mixing half control market and half Rep Senate/Governor legs (c2) is still worse than (c1).

Upside: Negative | P(>= 150k) effect: 20% only at ruin of a third | Ruin / drawdown: P(<= 70k) 33-39%.
End-valuation: Outcome.
Legitimacy: Fine, but bad.
Build: None (a NO-GO).
Screen: Done.

### H-8. The Senate control market disagrees with its own races: Dem control 0.645 vs 0.55-0.58 from the 34 races
Mechanism: Dems hold 34 seats that are not up, so they need 17 of the 35 up (VP Vance breaks a 50-50 tie):
- 34 races are listed, with E[Dem seats] 16.46;
- the Ohio special is not listed (assumed pD 0.40);
- Osborn counts as Ind, or as half.

At any rho from 0.15 to 0.75, P(Dem ≥ 17) is 0.54-0.56 (0.57-0.61 with Osborn counted D). Polymarket says 0.645, so the control
market is 7-10c rich on the Dem side.

Two consequences:
1. Plan (a) wants to close our **Dem U.S. Senate -5000** (hold edge negative at 0.645). Under the seat model it is +EV, so keep it.
2. Sell Dem Senate YES at the 0.67 bid. The collateral is 0.33, cheaper than the Rep ask at 0.35, but there are only 10.7k shares of
   depth.

Data check: H_outcome.py seat block; H_frontier `H_SEATS=1`. With $20k Rep Senate: E 116.1k, P150 23.7%, P(<= 85k) 4.0%.
Upside: +5k at the seat odds on a $20k position | P(>= 150k) effect: +6 points vs the Polymarket valuation | Ruin / drawdown: as H-6.
End-valuation: Outcome.
Legitimacy: Relative-value analysis.
Build: Check the Ohio assumption, Polymarket's own Ohio market and whether Osborn caucuses. 0 lines.
Screen: Data-only.

### H-9. Polymarket's calibration margin: use k 1.0 to pick, test at 0.8-1.1, and never re-optimise on k < 1
Mechanism: The longshot bias shrinks longshots further below their price. This is the margin I stated, from the literature on
prediction-market favourite-longshot bias (Intrade / Polymarket calibration), not measured here. If it holds, every NO-longshot and
YES-favourite position is worth more.
Data check: H_outcome / H_frontier (`H_KVAL`), plans chosen at k 1.0 and valued at another k:

| k | Current book | (a) | (c1) $22.5k P150 |
|---|---|---|---|
| 1.1 | +1.0k | +0.6k | 26.4% |
| 0.9 | -1.3k | -1.0k | 24.4% |
| 0.8 | -2.9k | -2.5k | 22.4% |

Re-optimising at k = 0.9 makes the greedy frontier buy cheap YES. That book has P(<= 70k) 23%: a k < 1 belief turns the optimiser into
a lottery buyer.
Upside: ±1k | P(>= 150k) effect: ±2 points | Ruin / drawdown: none, if the plans are chosen at k 1.0.
End-valuation: Outcome.
Legitimacy: Analysis.
Build: A `value_ref_margin` setting: min edge = max(1c, 0.15 x |p - c| for p < 0.05). ~10 lines in the allocator's edge.
Screen: Data-only.

### H-10. Risk limits for an outcome-settled book: what each setting protects, and the values

Data check: H_risk.py (the bot's formulas copied: `race_variance`, `worst_case_loss`, `settlement_risk`; fv = Polymarket).

**`max_worst_case_frac` 0.30.** This caps risk = min(R7, worst) at 30.3k. R7 = 0.15·|party_delta| + 3·sqrt(Σ race variance).
- Current book: R7 20.1k with Polymarket fvs; status shows 25.0k with the tilted fvs.
- MC loss at the 3-sd-equivalent tail (q0.13%): 21.0k below liquidation and 29.7k below EV.
- So R7 is a fair tail measure for a diversified value book, and **0.30 ≈ P(<= 85k) of 1-2%**.
- (a) has R7 31.7k, 1.05x the cap, with P(<= 85k) 1.9%.
- **Recommend 0.35 for the value core** (P(<= 85k) < 3%, P(<= 70k) ≈ 0.1%).

**`worst_case_backstop_frac` 0.8 (sum of per-race maxima).**
- It protects against every race going wrong at once, which has P ≈ 0 at the outcome (MC q0.1% loss is 22k).
- For a fully collateralised book it is a gross-capital cap, and it already binds at 79-84k.
- (a) has a worst case of 112.8k (1.12x the account), so the backstop pins (a) reduce-only.
- **Recommend 1.3 (or off) in value mode.** R7 and the bloc delta are the real limits.

**`max_party_delta_frac` 0.15 (shares).** The wrong measure (H-2); it sits at its cap now (-15.35k vs 15.15k). **Replace it with
`max_bloc_delta_frac`:**

| Book | Bloc-delta cap | P(<= 85k) |
|---|---|---|
| Value core | ±5k per sd (0.05) | < 2% |
| With a sleeve | 22k per sd (0.22) | 4-5% |
| (above) | 24-26k per sd | the cliff |

**`max_drawdown_pct` 0.30 (kill at 70k marks, `kill_switch`).**
- It does not sell; it cancels and stops. At the outcome it costs no EV, but it stops market making.
- Marks can fall without a loss at the outcome: s 0.11 → 0.56 (p95) marks the core about -16k.
- **Recommend 0.40 in value mode, or kill on settlement EV instead of marks.**

**Plan (c) under today's limits.** The sleeve breaks every limit: at $20k, R7 92k, worst 110k, share delta +46.6k. It needs H-6's
sleeve accounting:
- core R7 ≤ 0.35;
- sleeve $ ≤ 0.20 x the account;
- bloc delta ≤ 0.22.

Upside: Lets (a) run (+3.9k) | P(>= 150k) effect: enables H-6 | Ruin / drawdown: as tabled.
End-valuation: Outcome.
Legitimacy: Risk policy.
Build:
- Settings: `max_worst_case_frac` 0.35 and `worst_case_backstop_frac` 1.3 (config);
- the bloc delta (~40 lines);
- sleeve accounting (~30 lines in `settlement_risk` / `total_worst_case`, mirroring `basket_risk_split`).

Screen: Dry run on the live state (test_p9_dryrun harness).

### H-11. The final leaderboard under outcome settlement: top 10 needs ~200k, top 50 needs ~135k
Mechanism: Rivals are settled on the same simulated worlds as us. The field is 1,039 accounts:
- ~582 never traded (the Smart Score ranks only 457);
- 110 tilt-long: longshot YES bought at p + U(1-8%)·(c - p), the 2-5c fills; f ~ U(0.5, 1) of the account in 1 + Poisson(4)
  contracts;
- 150 directional: f ~ U(0.2, 1) in 1-3 competitive / control contracts, 70% partisan;
- 40 value, 20 MM, 137 noise traders.

The modelled tilt-long accounts mark today at a median of 183k (max 621k). That matches a +600% leader and 173 accounts above our +1%.
Data check: H_board.py.

At the outcome:

| Group | Median | Mean | P(<= 50k) |
|---|---|---|---|
| Tilt-long | 27.7k | 65.6k | 89% |
| Directional | 97.8k | | 21% |

- Tilt-long: 8% end above 150k, when a longshot hits. The 54-contract pool has 1.6 expected hits.
- Directional: 13% end above 150k.

| Place | p10 | median | p90 | Field-mix range of the median (tilt/directional 60/80 to 200/250) |
|---|---|---|---|---|
| 1st | 229k | 615k | 1.68M | |
| 10th | 166k | **197k** | 477k | 183-257k |
| 50th | 124k | **133k** | 167k | 118-148k |
| 100th | 108k | 111k | 124k | |

| Our final account | Median rank | Range across field mixes |
|---|---|---|
| 108k | 120 | 83-167 |
| 120k | 73 | 46-112 |
| 150k | 29 | 20-48; P(top 50) 54-95% |
| 200k | 10 | |

Upside: Sets the target. +50% (150k) is a solid top-50 and a coin-flip top-20. Top 10 needs about 2x.
P(>= 150k) effect: n/a | Ruin / drawdown: n/a.
End-valuation: Marks would invert this. The tilt-longs would then hold the top.
Legitimacy: Analysis.
Build: None.
Screen: Data-only. Re-fit the field mix from the next "Rank N of M" lines.

### H-12. Our plans' rank odds on the same worlds: (a) wins the top 100, the Senate sleeve buys the top-50 lottery
Mechanism: Rank is relative. Rivals' gambles hit in the same worlds as ours (a Rep Senate win lifts every Rep-partisan directional
account too).
Data check: H_board.py, base field:

| Plan | Median rank | P(top 100) | P(top 50) | P(top 10) |
|---|---|---|---|---|
| Current | 107 | 44% | 0% | |
| (a) | 85 | 72% | 5.5% | |
| (c) (a) x 0.775 + Rep Senate $22.5k | 222 | | 25% (bimodal) | 0-3% |

- With a small tilt/directional field: current P(top 50) 23%, (a) 49%, (c) 32%.
- With a large field: 0%, 0% and 16%.

Upside: Choose by objective: (a)/(b) for "a top-100 finish", plus the H-6 sleeve for "a shot at the top 50 / +50%" | P(>= 150k) effect:
as H-6 | Ruin / drawdown: as H-6.
End-valuation: Outcome.
Legitimacy: Analysis.
Build: None.
Screen: Done.

### H-13. Point 5: the close at 4 Nov 00:00 UTC, and the hedge if the rule were mixed
Mechanism: Markets close before results. Under outcome settlement nothing is valued at marks, and the interim marks are irrelevant to
the payout (owner, 07:30). If SIG instead marked unresolved positions at the close (mixed rule):
- our deficit vs EV = s_close x tilt exposure. That is 0.215 x 35.9k ≈ **7.7k** at B's median, 20k at the p95 s of 0.56;
- plus about -535 on the NO+NO sets, whose race mid-sums run above 1 (C-17).

The cheap hedge is a small long-tilt basket sized to cancel the tilt exposure:
- longshot YES at about 3c carries about -16.5 of tilt exposure per $ (-0.5 x 33 shares);
- so about **$2.2k** neutralises +35.9k of exposure;
- its cost at the outcome is about (1 - p/price) x 2.2k ≈ $1.8k of EV, insurance against a 7.7-20k mark deficit.

Also: do not unwind sets or sell value into the close unless the rule is confirmed as mixed.
Data check: Arithmetic on the status `tilt_exposure` 35,933 and B_mc's s quantiles. The hedge basket was not simulated.
Upside: Insurance only | P(>= 150k) effect: none under outcome | Ruin / drawdown: protects about 8-20k under a mixed rule.
End-valuation: The whole point.
Legitimacy: Trading.
Build: None unless SIG's rule changes. The Package 9 basket code (`basket_*`) can hold it with `basket_usd` about 2200.
Screen: Package 9 live_sim basket knobs.

### H-14. A settlement-EV status line and a ruin dashboard
Mechanism: Every decision in value mode is about the outcome distribution, but status.json shows only marks-based numbers. Add:
- `settlement_ev` (Σ at Polymarket);
- `settlement_q01` (closed-form normal approximation using race variances + bloc delta² + F);
- `bloc_delta`.

For the current book, the normal approximation EV - 2.33·sqrt(Σvar + bloc²) gives **93.9k**:
- sqrt(Σvar) = 5.9k, from R7 = 0.15 x 15,350 + 3·sd = 20,053;
- the bloc term is 2.55k;
- the MC q1% is **86.9k**, so the tail is fat (correlated plus lumpy);
- EV - 3 sd = 89.6k is closer, so use z = 3 for the status q1%.

Data check: H_risk.py: the bloc delta's closed form matches the MC slope within 2%. The normal-tail numbers are arithmetic on
H_risk's outputs.
Upside: Decisions on the right number | P(>= 150k) effect: indirect | Ruin / drawdown: shows the H-6 cliff before it is crossed.
End-valuation: Outcome.
Legitimacy: Monitoring.
Build: ~30 lines in the status writer (near `tilt_exposure`).
Screen: Dry run.

### H-15. MM capital vs value capital (input for I)
Mechanism: The frontier's marginal value edge is 11.6% once, to the outcome. F's market-making ceiling is 1.0-1.4k/day on 15-25k of
rotating cash, about 5-6%/day, and the inventory it buys is itself +EV. Even at a quarter of F's ceiling, a dollar in the MM reserve
out-earns a dollar of marginal value capital within 2-3 days. So the allocator should fund an MM reserve (15-25k) **before** the last
value tranche (edges 10-12%), not after.
Data check: H_frontier marginal edge 11.6% (a); F's ceiling (ideas_F). Not jointly simulated.
Upside: Up to F's number | P(>= 150k) effect: positive if F holds | Ruin / drawdown: low.
End-valuation: Outcome.
Legitimacy: Trading.
Build: The allocator reserve (I).
Screen: live_sim.

## TOP 5 (by P(>= 150k) per unit of ruin)
1. **H-6 + H-8**: Rep U.S. Senate YES ≤ $20k. P150 17-24%, P(<= 85k) 4-5%, no EV cost.
2. **H-10 + H-2**: limits fit for an outcome book (R7 0.35, backstop 1.3, bloc delta in place of shares, kill 0.40 or EV-based). Every
   plan needs these.
3. **H-4 → H-3 → H-5**: (a') now, (a) and (b) via the allocator. +1.7k → +3.9k → +5.9k EV, P(<= 85k) < 2%.
4. **H-15**: MM reserve funded before the last value tranche.
5. **H-11/12**: targets. 150k ≈ rank 20-48 (top 50); 200k ≈ top 10; 108k ≈ rank ~120.

## Assumptions I could not check
- **Snapshot age.** The 22:47 books and refs are used. By 07:30 the tilt is 0.14 (edges larger, about +25%) and the positions have
  changed (party delta -13.8k).
- **Depth.** Plan (a) assumes exits at the best bid/ask ∓0.25c. 10.4k shares in 7 contracts are beyond the top-3 depth. Our own
  resting quotes are counted in the books.
- **rho and regions.** rho 0.45 is argued from typical polling error, not fitted (the seat count cannot identify it). There are no
  regional factors. The Ind races are independent of F.
- **Control markets.** rho_ctrl 0.85 is assumed. House control is not derived from districts (only a few are listed).
- **Senate seat model.** Ohio special pD 0.40 (not listed), Osborn counted as non-Dem / half, Alaska Senate has no reference (mid
  0.6425 used), VP tie-break Republican.
- **The k margin.** The calibration margin k is a literature-based stated margin, not measured on Polymarket data here.
- **The leaderboard field.** The mix (110 tilt-long, 150 directional, 582 inactive) is a guess fitted only to "Rank 174 of 1039" and
  457 Smart-Score accounts. The top-10 threshold is driven by how concentrated the gamblers are.
- **Recycling (b).** Uses a stylised tilt path (B_mc's quantiles, log-normal) with ±50% per-contract dispersion. The recycling gain
  is added independently of the outcome.
- **SIG's rule.** Owner: payout at the outcome. H-13 covers the mixed case only arithmetically.
