# Stale-quote takes, arb sets, reduce-only (ops-snapshot-2026-10-02, to 08:13 UTC)
Script: `python analysis/poly_bias/takes.py DATA_DIR` (data staged from the snapshot branch, not committed).
Takes identified by `take: true` in order_notes.json joined to fills.csv (for asks, fill_price is the NO price; YES price = 1 - fill_price).
Journal: 25 `TAKE` attempts, 14 filled (15 fills; 08:11 Rep NH Gov split 563@0.89 + 148@0.90), 6 rejected
(400 VALIDATION / 409 IN_FLIGHT), 3 "traded ?" with no fill in fills.csv (23:13 Dem RI Sen, 23:44 Rep RI Sen, 01:33 Dem SD Sen).
The "23 takes" in the 2-h summaries count attempts (11+8+2+2).

## 1. Takes: 9,231 shares, notional q*price 4,997, capital at risk (cost per side) 8,442
Marks: mid0 = last two-sided snapshot before the fill; Polymarket = snapshot `reference`. Pass-through = q-weighted
(mid_h - mid0) / (poly0 - mid0). Initial gap ~ 7.1c/share in the direction of the trade. Horizons past 08:13 are truncated to the end (n tr.).

| horizon | P&L @Polymarket | per sh | P&L @tournament mid | per sh | pass-through | n tr. |
|---|---|---|---|---|---|---|
| at take | +553.1 | +5.99c | -98.5 | -1.07c | 0% | 0 |
| +15 min | +553.1 | +5.99c | -96.2 | -1.04c | +0.4% | 3 |
| +1 h | +552.0 | +5.98c | -48.6 | -0.53c | +7.7% | 5 |
| +4 h | +547.2 | +5.93c | -90.4 | -0.98c | +1.2% | 5 |
| end 08:13 | +552.0 | +5.98c | -128.1 | -1.39c | -4.5% | 0 |

Per market (net YES, P&L@mid end, P&L@poly end): Dem RI Sen +2185 / -21.5 / +142.4; Rep RI Sen -2173 / -37.9 / +133.8;
Dem NH Gov -1947 / -43.4 / +106.6; Rep NH Gov +1822 / -17.1 / +114.0; Dem NC Sen +1104 / -8.3 / +55.2.
All takes are in the tails (Polymarket 0.009-0.04 or 0.955-0.992); the tournament mid did not move toward Polymarket
at any horizon (pass-through 0-8%, -4.5% at end). The +553 is entirely a settlement bet that Polymarket is right;
marked at the tournament book the takes are -128 (they cross the spread into a tilt that does not close intraday).

## 2. Arbitrage sell sets: +39.39 locked, confirmed
- 01 21:58 Maine Senate: sell 500 Rep @0.490 + 500 Dem @0.575 = 1.065 -> +32.50 (mid-marked now +30.0)
- 02 07:54 Oklahoma Senate: sell 197 Dem @0.090 + 197 Rep @0.945 = 1.035 -> +6.89 (mid-marked now +2.5)

## 3. Reduce-only
- snapshots.mode holds only 'dry'/'live': no reduce-only flag there. status.json at 08:14: reduce_only False,
  worst_case_loss 32,712.69, settlement_risk 10,589.78 (risk_model correlated), account_value 101,498.41, party_delta -509.
- Journal `realtime |` lines carry "-> REDUCE-ONLY": 137 of 313 lines since live start (44%), time-weighted 43% of 16.1 h;
  first 01 22:48, last 02 07:59. Worst-case loss peaked 38,824 = 0.37 of account.
- account table (290 live rows): worst_case_loss / account_value mean 0.24, max 0.37; 43% of rows > 0.30.

## 4. Tilt (tier2.py section 8, rerun on the 02 snapshot, same as TIER2_RESULTS.txt)
slope of gap on (ref-0.5) by quarter (~4 h each): Q1 0.0240 (R2 0.11), Q2 0.0341 (0.45), Q3 0.0472 (0.58), Q4 0.0511 (0.60).
