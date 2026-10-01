# Day-one data report (Run B): SIG Predictions Cup, 2026-10-01 16:00-20:22 UTC

Data: branch `ops-snapshot-2026-10-01` (never merged). The scripts are in `analysis/`. Run them with `SNAP=<extracted snapshot dir>`. Each script's docstring says what it computes. Order: `attribute.py`, then `pnl.py`, `pnl_curve.py`, `worstcase.py`, and the rest.
Caveat: `market_data.sqlite` has only 90 live snapshots, about one every 2-3 minutes. Microstructure numbers that need finer timing use the journal's per-market quote lines (3,184 lines, logged only when the quote changes) and fills.csv (exact ms timestamps).

## 1. Where the "+26.5%" came from: it is almost all double-counted cash

**Headline: the real gain is about +475 SUSQies (+0.47%), not +26,529.**

The bot's `account_value` is `API totalAccountValue + locked_in_orders`, because `reserved_cash_mode` never left `detecting`. The API value already includes cash locked in resting orders:
- `account_value − locked_in_orders` sat between 99,219 and 101,256 across all 92 snapshots. It was 100,427 at 20:00 and 100,475 at 20:18.
- `corr(account_value, locked_in_orders) = 0.9993`.
- Example: from 17:33 to 17:38, locked rose by 24,955 and "account value" rose by 24,929 with no matching P&L.

The rank of 92 out of 447 is consistent with about +0.5%, not +26%.

Knock-on effects on live risk (for Run A and the owner):
- **Kill switch.** It trips when `API + locked < 70,000`. With 37k locked, the real drawdown needed to trip it is about 67k instead of 30k.
- **Worst-case cap.** It is `0.30 × (API + locked)`, which was 41.3k at 20:18 instead of 30.1k. It also moves with resting orders: cancelling quotes tightens it, posting quotes loosens it.
- **Phone summaries.** They report noise, for example "+27,447" and "-918 in 2h".
- **Fix:** set `reserved_cash_mode="ignore"`, or check the detector. The owner was notified.

**Reconstructing P&L from the 819 attributed fills** (`attribute.py` assigns 372 of the 381 unmatched fills to a logged quote by price; 112 of 124 final positions reconcile exactly with status.json):

| Component (to 20:18) | SUSQies |
|---|---|
| Edge at quote, Σ side·qty·(fv_at_quote − price) | +2,110 |
| Realised spread on closed round trips (average cost) | +1,518 |
| Unrealised on open positions at tournament mid | +140 (at liquidation prices, bid for longs and ask for shorts: −147) |
| Total reconstructed (mid marks) | +1,658 |
| Total if open positions were marked at Polymarket instead | +2,025 (**+367 more, not a reversal**) |
| API's own P&L | **+475** |
| Takes and arbitrage | 0 (status: `takes_total 0`, `arbitrages_total 0`; 32 "traded immediately" orders crossed on arrival) |

- **Unexplained gap: about −1,180.** It grows steadily with volume (≈0.3c per share traded). It was near 0 at 16:35 and −1,100 by 18:13.
- Candidate causes, in order:
  1. the API marks at a different price (last trade or a "valuation price": one cycle failed with `CONFLICT: holdings cannot be valued because one or more exchanges have no valuation price`);
  2. the 9 fills that could not be attributed (Rep Maine Senate 2,000 sh at 0.415, Rep U.S. Senate 1,547 sh at 0.39, …) and a few mis-sided ones;
  3. a per-trade fee.
- Run C should treat realised economics as "+0.5% in 4h20m, edge earned but mostly given back". Reproducing this needs the API's mark rule (a rule question for the owner).

**What drives the marks.** They are small. The largest open-position marks at tournament mid:

| Position | Shares | Mark P&L | Moving to Polymarket |
|---|---|---|---|
| Rep MI Senate | −940 | −55 | +71 |
| Dem KS Senate | −3,749 | −45 | +122 |
| Dem U.S. House | +832 | +17 | +17 |
| Dem MI Senate | −350 | +17 | −3 |
| Rep SD Senate | −478 | +16 | +4 |
| Dem TX-23 | +710 | +15 | −5 |
| Rep AR Senate | +1,200 | −14 | +16 |

- Moving all marks to Polymarket would add +367 net, so nothing material reverses.
- The money was made in Dem U.S. House: +577 realised on 38k shares, mostly the 16:07 stale bid at 0.88 sold at 0.91.
- The money was lost in Dem FL Governor (−41) and Rep Maine Senate (−8, plus the unattributed 2,000 sh).

## 2. Worst-case settlement loss: why it keeps rising

**The worst-case number is mostly the number of races with any position, not directional risk.** `worst_case_loss` adds up each race's own worst outcome, as if every race went wrong at once. Recomputed with the bot's formula from the snapshot positions: 23,297 vs the bot's 23,234 at 20:18.

- It covers 74 races with positions. The top 10 races are 57% of it, and 25 races are between 250 and 5,000 each.
- Growth from 18:00 to 20:18 was **+1,516 per hour**. Most of it comes from one-sided fills accumulating in new races as coverage widened (60 → 74 races). A few headline positions also grew.

Top drivers at 20:18:

| Race | Worst | Legs |
|---|---|---|
| U.S. Senate | 2,769 | Rep +7,585 @0.36 (not quoted 20:00-20:18: fair value missing) |
| Kansas Senate | 2,466 | Dem −3,749 @0.29, Rep −300 |
| Arkansas Senate | 1,941 | Rep +1,200 @0.95 **and** Dem −850: the same bet twice |
| New Hampshire Senate | 1,543 | Dem +1,862 @0.88 |
| Texas Senate | 1,207 | Rep −1,550, Dem +400: also a doubled-up Dem bet |
| CO-03, MN-02, VT Gov, WA-03, TX-15 | 626-769 each | |

Compare with the actual risk:
- The settlement P&L standard deviation, treating legs as independent, is about **4,400**.
- A national swing of 10c on every price moves P&L by only **±249**, because party delta is small.
- So the cap binds on gross size and breadth, not on directional risk.

When the cap binds at the current pace:

| Measure | Value |
|---|---|
| 30% of *real* equity | 30,142 |
| Hours to reach it | **≈4.6 h**, about 01:00 UTC on 2 Oct, overnight |
| Bot's own (inflated) cap | 41,325 → about 12 h |
| Bot's cap with 0 locked (for example right after a cancel-all) | flips reduce-only on at once |

Implications for Run C:
- Use a correlated risk measure: a national-swing factor plus independent residual (sd or CVaR) instead of sum-of-maxima.
- Net doubled-up race legs (long Rep plus short Dem in the same race).
- Skew harder to flatten dust positions in quiet races.
- Fix the equity base.
