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
- **Quote sizes.** The README says "every size scales with the account", so sizes and the 60% capital lock scale with the inflated value, up to about 1.4× the intended size.
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

## 3. Edge, markout and competition

Scripts: `edge.py`, `competition.py`, `pm_moves.py`. All 819 attributed fills are used.
- **Edge** = side × (fair value at quote − price).
- **Markouts** are re-measured against the snapshots at +5, +15 and +60 min. The `fv_after` column in fills.csv holds the fair value when the fill was *detected* (usually the same cycle). Its markout is close to 0 by construction, so the logged "markout −0.09c" means little.

**Edge and markout (c/share, share-weighted):**

| Group | Fills | Shares | Edge | Markout vs fv +5m / +15m / +60m | Markout vs tournament mid +15m | P&L at mid +60m |
|---|---|---|---|---|---|---|
| All | 819 | 368,930 | **+0.58** | −0.08 / +0.03 / −0.04 | −0.22 | +1,613 |
| Headline (U.S. House/Senate) | 103 | 160,375 | +0.36 | +0.03 / +0.08 / −0.02 | −0.24 | +689 |
| Race fills ≥500 sh | 148 | 140,058 | +0.67 | −0.17 / +0.01 / −0.06 | −0.20 | +349 |
| Race fills <500 sh | 568 | 68,497 | +0.89 | −0.14 / −0.06 / −0.07 | −0.21 | +575 |
| Matched to a logged quote | 447 | 216,928 | +0.71 | | | +1,236 |
| Inferred (unmatched in fills.csv) | 372 | 152,002 | +0.39 | | | +376 |

- **Adverse selection is small against our own fair value** (about 0 to −0.1c), and about −0.2c against the tournament mid.
- **Where we are picked off:** fills at the 1c floor in contested markets, where the edge itself is thin. **Rep U.S. Senate is 33% of all shares traded (121,974) at +0.02c edge**, −74 after 60 min. That is pure churn against other bots.
- **Where we earn:** Dem U.S. House (+762, edge 1.45c, mostly the 16:07 stale bid), Rep WI Gov (+136), Rep MI Senate (+51), Rep VT Gov, Rep SC-01, Dem TX Gov, Rep SD Senate, Rep TX Senate (+35 to +48 each).
- **Where we lose:** Rep U.S. Senate −74, Dem FL Gov −66 (negative edge), Rep MN Senate −19.

Edge over time (30-min buckets):

| | 16:00 | 16:30 | 17:00 | 17:30 | 18:00 | 18:30 | 19:00 | 19:30 | 20:00 |
|---|---|---|---|---|---|---|---|---|---|
| Edge (c) | 0.94 | 0.02 | 0.64 | 0.48 | 0.61 | 0.80 | 0.19 | 0.22 | 1.22 (37 fills) |
| Shares (k) | 84 | 63 | 64 | 34 | 37 | 33 | 25 | 16 | 15 |

Volume per half hour fell five-fold as spreads closed.

**Inventory skew pays away the edge in big markets (an important finding).** Fills bucketed by edge at quote:

| Edge bucket (c) | Fills | Shares | P&L at mid +15m | P&L at mid +60m |
|---|---|---|---|---|
| ≤ −1 (through our own fair value) | 127 | 70,079 | **−913** | **−804** |
| −1 to 0 | 141 | 86,939 | −398 | −211 |
| 0 to 1 | 214 | 96,059 | +312 | +546 |
| 1 to 2 | 205 | 58,179 | +323 | +161 |
| 2 to 3 | 86 | 27,735 | +415 | +408 |
| > 3 (sweeps through our resting quotes) | 46 | 29,939 | **+1,425** | **+1,513** |

- **42% of shares were traded at or through our own fair value.** 89 of the 127 worst fills are matched to logged quotes, so they are not attribution errors.
- Cause: `skew_per_share = 0.00003` (0.3c per 100 shares) is the same for every market. After a single 4,000-8,000-share headline fill, the reservation price moves 12-24c, so the next quote crosses fair value by up to the 4c cap to dump inventory.
- Rep U.S. Senate contributed 30k of the 70k worst shares. Examples: bid 0.40 vs fair value 0.3795 for 4,438 + 8,333 sh at 16:24-16:25; ask 0.91 vs fair value 0.922 for 8,000 Dem House sh.
- **Fix for Run C:** scale the skew to the market's quote size (for example per 1% of the position limit), not per share.
- **All the profit comes from fills with >2c edge,** which are mostly other traders sweeping through our resting quotes. That supports quoting depth behind the touch (IDEAS.md).

**Competition at the top of book.** These are 2-3 min snapshots; best bid and ask include our own orders. Where we were quoting (8,668 quote-snapshots):

| Our quote vs the best price | Bid | Ask |
|---|---|---|
| We are at the best | 23% | 25% |
| Someone exactly **1 tick inside** us | **19%** | **18%** |
| 2 ticks inside | 15% | 19% |
| 3+ ticks inside | 33% | 31% |
| Not yet in the book (just posted) | 10% | 9% |

- **We were behind the best price about two-thirds of the time.**
- Headline markets: at best 28%, but 3+ ticks behind 43% (the 4c cap or skew keeps us away).
- The share of time we are at the best fell from 27% (16h) to 21% (20h).
- **Every market we quoted was contested.** Of 220 markets quoted in ≥10 snapshots, 199 had someone ahead of us in >50% of snapshots, and none was never undercut. "Quiet race nobody prices" does not exist on day one at this resolution, though finer data might show gaps.
- **Spreads tightened fast.** The median two-sided spread in races went 2.0c (16h) → 1.0c (17h, 19h, 20h). Headline: 1.5c → 0.5-1.0c. The share of two-sided books with spread ≤1c went 24% → 51% → 49% → 57% → **71%** (16h→20h).
- **The other traders' best quotes sit inside our floor.** Where the best price is not ours, the other best bid is a median **0.5c** below our fair value (ask: 1.0c above). 62% of their bids and 51% of their asks are <1c from our fair value, so inside our `min_edge`. 30% of bids and 19% of asks are *through* our fair value, meaning their fair values differ from ours by ≥0.5c.
- This confirms the owner's picture: several quoters converge at 0.5-1c from a Polymarket-anchored fair value, and our 1c floor leaves us behind them.

**Undercut speed.**
- The bot changes its quote in a market every **282 s at the median** (p25 170 s, p75 746 s).
- 26% of quote changes are 1-tick tightenings (chasing) and 29% are 1-tick widenings.
- Exact undercut latency is not measurable: no tick-level book is stored. Run A/C should log book changes (realtime events) to measure it.

**Polymarket-move fills.**
- Only 38 of 727 fills with a known reference followed a ≥0.5c Polymarket move in the previous 10 min.
- Their markout was −0.15c (moved against us) and −0.67c (moved with us) vs −0.21c for the rest.
- On day one, **adverse markout did not come from Polymarket moves.** Polymarket barely moved, and the reference is logged only on quote changes. The thin edge comes from competition at the floor, not from being picked off.

**How the other bots behave (what can be seen):**
- They quote within 0.5-1c of a Polymarket-like fair value.
- They stay two-sided through the session; the share of tight books keeps rising, so they do not step back by 20h.
- They often sit through our fair value, so their fair values differ.
- Sizes and repricing latency are **not recorded** (snapshots have no sizes). This is the main data gap for Run C; recommended: record top-3 levels with sizes from realtime book pushes.

## 4. Coverage: which markets went unpriced, and why

Script: `coverage.py`. Average count of markets per snapshot in each half hour (237 markets):

| Half hour | Quoted 2-sided | Quoted 1-sided | Unpriced: 2-sided top book | Unpriced: one-sided/empty | Unpriced: spread >30c |
|---|---|---|---|---|---|
| 16:00 | 33 | 8 | 189 | 3 | 3 |
| 17:00 | 37 | 22 | 177 | 0 | 0 |
| 18:00 | 42 | 19 | 175 | 0 | 0 |
| 19:00 | 37 | 28 | 172 | 0 | 0 |
| 19:30 | 47 | 30 | 161 | 0 | 0 |
| 20:00 | 53 | 28 | 157 | 0 | 0 |

- **About 160-190 markets were unpriced while their books were two-sided and tight.** The median top-of-book spread of unpriced markets after 19:30 was **1.0c** (p25 0.5c, p75 1.5c).
- 97% of them had a Polymarket reference.
- So unpriced markets were **not** one-sided books or missing references.
- `fair_value()` returns None unless the *tournament* book has ≥200 shares at both best prices (`fv_min_depth`) and was verified in the last 300 s (`book_stale`). **Polymarket alone can never price a market.**
- The remaining causes are thin top levels (<200 sh) or stale verification under the read budget. The snapshots cannot separate them.
- Recommendation for Run A: log the reason per market.
- Telling example: **Rep and Dem U.S. Senate had no fair value in any snapshot after 19:30**, although the book was 0.36/0.375 and we held **+7,585 Rep U.S. Senate**. Dem U.S. House was priced 50% of the time and Rep U.S. House 36%.
- **72 of the positions held at 20:18 were in unpriced markets** and could not be quoted or reduced. Examples: Dem NH Senate +1,862, Dem AK Gov +200, Dem NH-01 +200.

**Error timeline (journal):**

| Half hour | 409 REQUEST_IN_FLIGHT | 429 | Realtime missed messages (full resync) | Other |
|---|---|---|---|---|
| 16:00 | 19 | 2 (16:07:33, 16:10:35 → budget 46/min) | 12 | |
| 16:30 | 18 | 1 (16:43:43 → 42/min) | 18 | 4 cancel confirms failed, 1 failed cycle (valuation CONFLICT) |
| 17:00 | 16 | 0 | 9 | |
| 17:30 | 20 | 0 | 36 | |
| 18:00 | 14 | 0 | 28 | websocket down ×1 |
| 18:30 | 16 | 0 | 12 | |
| 19:00 | 23 | 1 (19:02:54 → 59/min) | 21 | websocket down ×1 |
| 19:30 | 18 | 0 | 20 | |
| 20:00 | 12 | 0 | 11 | websocket down ×1 |

- **409s did not fade after the open.** There were 12-23 every half hour all afternoon (156 total), with 20-order batches.
- A third-party copy of the platform docs says the limit is **"100 reads and 30 writes per minute per key"** (see §7). A 20-order batch may count as 20 writes, which would explain persistent slow and duplicate writes. This needs confirming.
- The bot's budget of about 76 requests/min is not split into reads and writes.
- Cycle durations are not logged apart from the first one (4.5 min, from the brief). The bot changed a market's quote every 282 s at the median.
- 32 orders "traded immediately", meaning they crossed on arrival because the book had moved during the slow write.

## 5. Divergence between tournament prices and Polymarket

Script: `divergence.py`. Tournament mid vs Polymarket reference, using two-sided books with spread ≤5c (20,220 observations from 16:05 to 20:18).

- **|gap| distribution:** median 0.8c, p90 2.2c, p99 3.6c. ≥3c: 3.1% of observations. ≥5c: 0.1%. ≥10c: none.
- **≥3c episodes:** 228 in 69 markets (≈54/h).
  - 96% closed within the data. Median duration ≤2.7 min (one snapshot interval), p75 7.8 min.
  - **213 of 218 closed because the tournament price moved back** and only 5 because Polymarket moved. Tournament prices converge to Polymarket, not the other way round.
- **≥5c episodes:** 10 in 8 markets (Rep TX Senate 8c, Dem U.S. House 6c, Dem TX Gov 6c, Rep RI Senate 5.6c …). All closed within ≤10 min.
- **The big day-one dislocations were sweeps between snapshots.** These include Maine Senate summing to 1.43, Dem House at 0.816 and WI Gov at 0.50. At every snapshot from 16:05 to 16:28 the books were back near Polymarket: Dem House 0.905/0.925 vs reference 0.925, Rep Maine 0.41/0.425 vs 0.405. So the big gaps are *traded prints* from market orders walking thin books, refilled within 1-3 minutes. The P&L of >3c-edge fills above is the money from these.
- **Persistent bias: favourite-longshot.** Mean (tournament − Polymarket):
  - +1.1c for contracts under 10c and +0.9c for 10-30c;
  - +0.25c for 30-70c;
  - −0.6c for 70-90c and −0.6c above 90c.
  - By party: Rep +0.33c, Dem +0.14c, so no meaningful partisan bias.
- **Race party sums.** Bid sums above 1.00 occurred in 2.9% of race-snapshots and never above 1.03 at snapshot times. Ask sums below 0.97 occurred in 0.3%. The bot's 3c arbitrage trigger would therefore rarely fire on resting books; dislocations exist only during sweeps.
- **What it implies for holding positions:**
  - Holding against a dislocation is right: it reverts in minutes, and toward Polymarket.
  - Rank is marked on tournament prices, so mark-to-market noise from a gap is ≤2-3c and short-lived.
  - The real holding risk is Polymarket itself moving (news), which did not happen on day one.
  - Run C's simulator should model the tournament mid as Polymarket + mean-reverting noise (half-life ≈2-3 min, sd ≈1.2c) plus rare sweep spikes (see §8).

## 6. Fill attribution

- **381 of 828 fills (46%), 156,049 of 372,977 shares (42%), from 260 orders, were not matched to a bot quote** (`our_side ?`). It was not only the open: 111 / 115 / 65 / 71 / 19 such fills in the 16h-20h hours.
- 253 of the 260 order ids are interleaved with noted ids, so these are the bot's own live orders whose confirmation never came back. The most likely reason is 20-order batches that hit **409 REQUEST_IN_FLIGHT** (156 batch failures ≈ up to 3,100 orders). The orders landed but the bot does not know about them.
- Consequences:
  - fill stats exclude 42% of volume;
  - "unknown" orders rest until the 30-min expiry, unmanaged, while their market moves. That is a stale-quote risk;
  - the inventory the bot thinks it has is right only because positions are read from the API.
- **Recovered by price matching** (`attribute.py`): 372 of 381 fills match a logged quote of the same market within 35 min (price = our bid, or ask / 1 − ask).
  - The recovered fills had **+585 total edge (+0.39c/share), +350 at the 15-min mid and +376 at the 60-min mid.** They carried positive edge, but less than matched fills (+0.71c), as expected for older and staler orders.
  - By size: headline 57.6k sh, busy races 58.6k, small 35.7k.
- **9 fills (4,947 sh) cannot be attributed.** They are mostly Rep Maine Senate 2,000 sh @0.415 (16:55) and Rep U.S. Senate 1,547 sh @0.39 (16:24). They explain most of the 12 position mismatches against status.json.
- Fix (Run A): on a 409 or timeout, re-read open orders and adopt unknown orders into `order_notes` with the quote they came from. Also log `eid` and price on every fill so attribution never needs inference. fills.csv also loses the NO/YES side: the API's signed quantity is stored as an absolute value.
- Note: for NO-side fills, `fill_price` is the NO price. Verified: 158 of 202 matched ask fills have fill_price = 1 − quote_price; 34 equal the quote, which are sells of YES already held.

## 7. Research: rules, Smart Score, rate limits

**Caveat.** The cloud proxy blocks predictionscup.com, sig.thesuper.market, sig.com and docs.polymarket.com. The rule quotes below come from search-engine extracts of the official pages and **must be checked by the owner on the live pages**. Full notes are in `analysis/research_notes.md`.

**Official rules** (https://predictionscup.com/rules/, search extracts):
- *Dates:* "runs from October 1, 2026 noon ET to November 4, 2026 noon ET."
- *Bots are allowed:* participants "may connect an unlimited number of automated software programs ('Bots') to their Account, provided that each Participant maintains only one (1) Account." The API may be used to "submit and cancel simulated orders … subject to applicable authentication requirements, **rate limits, position limits, technical controls**, these Official Rules, and the Platform's Terms of Service." The bot's activity is "deemed the activity of the participant."
- *Ranking:* "You must complete at least one trade to receive a rank. … the ranking at the end of the competition is fully determined by your **final SUSQie balance**." Prizes: $30,000 / $5,000 / $2,500 for the top 3.
- *Settlement:* "Upon the official resolution of each Market, the Platform will adjust each participant's SUSQies balance by crediting or debiting SUSQies as applicable."
- *Enforcement:* the Sponsor may "disqualify any participant, void or reverse any trade, adjust any SUSQies balance or leaderboard standing" for rule violations or "unsportsmanlike or disruptive manner". Multiple accounts and identities lead to disqualification.

Not found; these are **rule questions for the owner**:
1. Whether "final SUSQie balance" counts open positions, and at what price (last trade, mid or valuation price). Election day is 3 Nov and the competition ends 4 Nov at noon ET, so most races will be **unresolved at the end**.
2. Explicit wash, spoof or collusion wording.
3. The numeric position limits.
4. Tie-breaks.

**Smart Score.**
- There is no public definition. The bot's API wrapper reads `/tournaments/{slug}/me/smart-score`, with fields `smartScoreDecayed`, `rank`, `totalTraders`, `isElite` per `marketType`. The code comment says "SIG's recruiting export ranks candidates by this score". The *decayed* field suggests recent performance weighs more.
- We were "not scored yet" at 20:00 despite 800 fills, so there is probably a minimum (time, markets or resolved markets).
- SIG's press release: competitions are "part of how Susquehanna identifies students with an aptitude for probabilistic thinking", which hints at a calibration or accuracy component rather than raw P&L. This is unverified. **Ask SIG or check the docs.**

**Platform API limits** (third-party repo quoting the platform docs, fetched: https://github.com/nullif1ed/sigprediction):
- "**100 reads and 30 writes per minute per key**; a 429 response carries `Retry-After: 60`."
- Bulk `/exchanges/prices` covers 237 markets in 3 reads.
- The realtime websocket "pushes full books without using the read budget".
- **Implication:** our 20-order batches, about 140 per hour that fail with 409, probably exceed a 30-writes/min budget. Run A should confirm in the docs whether a batch counts as one write or as N.

**Polymarket gamma-api** (third-party summaries; the official page https://docs.polymarket.com/quickstart/introduction/rate-limits is blocked here):
- General about 4,000 req/10 s, `/markets` 300/10 s, `/events` 500/10 s.
- Throttled through Cloudflare (requests queued, not rejected).
- Our 5 s refresh is far below these limits.

**Competitor intel (public).** Other participants publish their bots:
- `github.com/ZVogel1/SIG-PM-Challenge-F2026`: "edge_hunter", "constraint_arb" and "risk_manager" bots.
- `nullif1ed/sigprediction`: blends SIG mid, other venues and complement markets, polls hot markets every 2 s.

So structural (constraint) arbitrage and Polymarket anchoring are already competed for.
