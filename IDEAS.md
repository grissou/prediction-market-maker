# Alpha ideas: SIG Predictions Cup bot (Run B)

These ideas merge my own data-driven ideas with an 88-item unanchored brainstorm (raw list in the appendix and `analysis/brainstorm_raw.md`). Each entry was tested against day-one data where possible (`analysis/idea_tests.py` and the scripts behind DATA_REPORT.md).

**Effect estimates use the day-one scale**: over 4.2 h, fills earned **+1,613 at the 60-min mid** (+2,110 edge at quote), and the API P&L was +475. They are rough and assume the flow persists.

**Honest summary:** nothing here is a breakthrough. The strongest ideas are corrections that the data exposes:
1. the account-value double count;
2. inventory skew that pays away the edge in big markets;
3. a touch-quoting strategy that earns about 0 while resting depth earns almost everything.

Together they are worth more than any clever signal I found.

## Ready to build (strongest first)

### R1. Fix the equity base (`reserved_cash_mode="ignore"`)
- **Mechanism.** The bot's account value = API value + locked cash, and the API value already includes locked cash. This inflates equity by 8-48k, so the kill switch, the worst-case cap, sizes and the summaries are all wrong.
- **Evidence.** `account_value − locked` stays at 99.2-101.3k; correlation with locked is 0.9993 (DATA_REPORT §1).
- **Effect.** Correct risk limits and about 25% smaller (intended) sizes. Prevents a 30% cap that today moves with resting orders.
- **Sketch.** One setting. Better: make the detector require the "ignore" verdict when `dv≈0`, and log the verdict.
- **Risks.** None. **Confidence: very high.**

### R2. Liquidity-scaled inventory skew; never cross our own fair value except when deliberately flattening
- **Mechanism.** `skew_per_share=0.00003` is the same for 100-share races and 10,000-share headline markets. One 8k-share fill moves the reservation price 24c, so the next quotes sit up to 4c *through* fair value.
- **Evidence.** 127 fills / 70k shares at edge ≤ −1c lost **−804** at the 60-min mid. All fills with edge ≥0 earned **+2,689** vs +1,613 for all fills. Rep U.S. Senate: 122k shares at +0.02c edge.
- **Effect.** **+500 to +1,000 per 4 h at the day-one rate**, the largest single item.
- **Sketch.** `skew = k × inv / position_limit(market)`, for example 1.5c at 50% of the limit. Clamp the reservation price to fv ± `max_skew_through` = 0 unless in reduce-only or flatten mode. Add hedging via the opposite party first.
- **Risks.** Inventory lingers longer, so pair it with R7. **Confidence: high.**

### R3. Resting depth ladder behind the touch (sweep catcher) instead of fighting for the touch
- **Mechanism.** Students' market orders walk thin books (Dem House 0.816, Maine 1.43, WI Gov 0.50). Resting orders 2-5c from fair value catch the overshoot, which reverts in minutes.
- **Evidence.**
  - Fills with >3c edge were 8% of volume but earned **+1,513 of the +1,613** (60-min mid). Fills with 0-1c edge earned +546; negative edge lost.
  - Divergences close by the tournament moving back to Polymarket (213 of 218), with a half-life of about 5 min.
  - Brainstorm ideas #3, #4, #88.
- **Effect.** Keeps most of the P&L while cutting churn and writes. Possibly more if sizes behind the touch are larger: +300 to +800 per 4 h.
- **Sketch.**
  - Per market, 2-3 levels at fv ± {2c, 3.5c, 5c}, sizes 1× / 2× / 3× base, and they *stay resting* (no re-quote unless fair value moves ≥1c). This saves writes.
  - Pull all levels on a Polymarket jump ≥1.5c or the jump guard.
  - Headline markets: levels to 6-8c.
- **Risks.** Stale deep orders on real news (needs a fast pull); capital lock (measure against the 60% cap). **Confidence: medium-high.**

### R4. Join or step back, never penny at the floor
- **Mechanism.** Other quoters sit a median 0.5-1.0c from our fair value, inside our 1c floor. Improving by a tick buys the worst flow and burns writes.
- **Evidence.**
  - We were behind the best price 67-77% of the time.
  - 26% of quote changes are 1-tick chases.
  - Fills at 0-1c edge earned 0.57c/share and fills at −1-0c lost.
  - Brainstorm #1, #2, #19.
- **Effect.** About neutral on P&L, but frees roughly 25% of writes for R5/R3 and reduces 409s.
- **Sketch.** If the other best is ≥ min_edge from fair value, join it (same price) when the queue is <X shares, else improve by one tick. If the other best is inside min_edge, do not chase; rely on the R3 ladder.
- **Risks.** Fewer touch fills. **Confidence: medium-high.**

### R5. Price thin-book markets from Polymarket (coverage)
- **Mechanism.** `fair_value()` needs ≥200 shares on both sides of the *tournament* book, so 157-189 of 237 markets went unpriced, even with a two-sided book of median spread 1.0c and a Polymarket reference (97%).
- **Evidence.**
  - 72 positions sat in unpriced markets, including **Rep U.S. Senate +7,585 unquotable after 19:30** and Dem NH Senate +1,862.
  - Small race fills earned 0.8c/share at 60 min.
- **Effect.** More fills in about 150 markets, plus the ability to work off stuck inventory: +100 to +300 per 4 h.
- **Sketch.** If the book fair value is None but Polymarket is liquid (spread ≤3c) and the tournament top of book is within 3c of Polymarket, use fv = Polymarket (plus the R9 bias), quoting wider (min_edge 1.5c, size 100). Log the unpriced reason.
- **Risks.** A wrong Polymarket match (keep the wrong-match guard). **Confidence: medium-high.**

### R6. Adopt orphaned orders after 409s; split the write budget
- **Mechanism.** 42% of filled shares came from orders the bot did not know about. The platform reportedly allows **30 writes/min** but 100 reads.
- **Evidence.** DATA_REPORT §4 and §6.
- **Effect.** Fewer stale and unmanaged orders, and correct fill stats.
- **Sketch (Run A).**
  - Re-read open orders after any 409 or timeout and adopt them.
  - Use separate read and write budgets (write ≤ about 25/min).
  - Smaller batches, with cancels prioritised.
- **Confidence: high.**

### R7. Replace sum-of-maxima worst case with a correlated risk measure; net doubled-up race legs
- **Mechanism.** The worst case adds every race's worst outcome. At 23.3k it was growing about 1.5k/h and would hit 30% of *real* equity around 01:00 UTC, freezing the bot in reduce-only, although the settlement sd is about 4.4k and a 10c national swing costs only ±249.
- **Evidence.** DATA_REPORT §2. Arkansas Senate is long Rep and short Dem at the same time (the same bet twice).
- **Sketch.**
  - Risk = national-swing beta × 10c shock + 2.33 × residual sd (independent races) + headline worst case.
  - Cap each of these.
  - Skew toward netting within a race (sell the duplicate leg first).
  - Keep the sum-of-max as a soft alert only.
- **Risks.** Underestimating correlation on election night, so keep the swing shock large. **Confidence: medium-high.**

### R8. Scale headline quotes down
- **Mechanism.** At 10,000 shares the headline markets produce huge one-sided fills that the skew then dumps (R2).
- **Evidence.** Headline: 160k shares at 0.36c edge, and the U.S. Senate at 0.02c. Dem House earned on one lucky stale fill.
- **Sketch.** Headline size 2-3k at the touch with the R3 ladder behind it; position limit unchanged.
- **Confidence: medium.**

## Needs testing

| # | Idea (brainstorm #) | Mechanism | Day-one evidence | How to test / sketch | Confidence |
|---|---|---|---|---|---|
| T1 | Per-market bias in fair value (#38, #37) | The tournament − Polymarket gap has a **persistent** per-market part (sd 1.19c) and a transient part (0.58c, half-life 5 min). Fair value = Polymarket + EMA(gap), instead of a fixed 70/30, quotes where the market actually trades | Bias sd 1.19c, autocorrelation 0.94 at 2.8 min | Day 2-3 snapshots: does EMA(gap) predict the mid 15 min ahead better than the 70/30 fair value? | Medium |
| T2 | Favourite-longshot bias (#49, #55) | Tournament +1.1c above Polymarket under 10c and −0.6c above 70c; favourites earned 1.6c/share | `divergence.py`, `idea_tests.py` | Whether to fade it depends on the end-valuation rule: if final balance uses resolution, sell longshots and buy favourites vs Polymarket; if marks, the bias persists and fading costs mark-to-market | Medium; **rule Q1** |
| T3 | Time of day / overnight (#20, #51, #52) | US students are active 00-04 UTC; few bots overnight → wider spreads | No data yet (only 16-20 UTC) | Spread and edge by hour from day 2 snapshots; widen and size up in thin hours | Low-medium |
| T4 | Pull-first on Polymarket moves (#22, #8) | Cancel stale quotes before repricing; the fastest bot picks off the slowest | Not testable on day one (Polymarket moved ≥1c only 0.05 times per market-hour) | Log Polymarket tick time vs our cancel time; replay in Run C's simulator with a 2-5 s competitor taker | Medium |
| T5 | Realtime book logger → competitor fingerprints (#11, #15, #16, #23) | Measure each competitor's repricing latency, floor and size; trade around slow ones | Snapshots lack sizes and timing | Store top-3 levels with sizes from websocket events (no extra reads) | High value of information |
| T6 | Smaller sizes earn more per share (#76) | 1-100 sh fills earned 0.8c/share, 100-2,000 earned 0.3c | `idea_tests.py` | Size by measured markout per market rather than activity rank | Low-medium |
| T7 | Round-number avoidance (#9, #56) | Our fills at prices that are multiples of 5c earned 0.26c/share vs 0.51c in races | Weak; headline confounded by R2 | Re-test with more data; if it holds, avoid resting exactly on .x0/.x5 | Low |
| T8 | Party-sum arbitrage at 1.5c (#39, #46) | Rep + Dem bids above 1 | Bid sums >1.00 in 2.9% of race-snapshots, never >1.03; ask sums <0.97 in 0.3%. Competitors run "constraint_arb" bots | Only during sweeps, so R3 captures it better | Low |
| T9 | Control vs race basket (#34, #41) | Senate/House control implied by races via a correlated Monte Carlo | Not tested (needs a seat model) | Daily gap between the control contract and the model; trade only >5c gaps with a small size | Low-medium |
| T10 | Smart Score optimisation (#60) | If it rewards calibration or decayed recent performance, cut variance and avoid holding against Polymarket | Not scored after 800 fills | Ask SIG and read the docs; read our `smartScoreDecayed` daily vs actions | Unknown; **rule Q3** |
| T11 | Markout-driven auto-widen per market (#77, #78) | Widen by 1c where the rolling markout is negative | Markout small overall (−0.2c mid); losses concentrated in Dem FL Gov, Rep MN Senate | Replay day 1-3 | Medium |
| T12 | Dust cleanup (#75) | Small positions add worst case without spread value | 25 races with worst <100 | Skew harder on <100-share positions in quiet races | Medium |

## Wild cards

- **W1. Election-night information edge (#67, #68, #71).**
  - Markets close at 00:00 UTC on 4 Nov and the first polls close at about 23:00 UTC on 3 Nov. Today the bot goes reduce-only 12 h before, which throws away the last hours of the most informative trading.
  - Students' resting quotes will be stale while Polymarket moves 10-30c on early returns, exit polls and turnout reports.
  - Taking those quotes is ordinary taking at posted prices.
  - Test: replay 2022/2024 Polymarket paths on election night and check the tournament take rate from late October data.
  - Risk: large; size it small.
  - **Rule Q:** confirm close and settlement times.
- **W2. Variance strategy for the prize (#61, #62).** Only the top 3 win cash ($30k, $5k, $2.5k). Steady MM edge at about +0.5% per 4 h would reach roughly +60-100% over 34 days only if day-one flow persisted, which is unlikely. The **owner must choose**: maximise P(top 3), which needs variance and directional conviction late, or a steady Smart Score for recruiting.
- **W3. Competitor downtime windows (#18).** Measure when tight books suddenly widen (bots crashing on 429s, overnight hosting) and step in wider and bigger then. Needs the T5 logger.
- **W4. Be the reference price in neglected markets (#87).** Evidence **against**: every market we quoted was contested on day one. Revisit if T5 finds gaps.
- **W5. Kalshi / PredictIt / forecast models as second references (#26, #27, #28, #31).** Mainly for wrong-match protection and markets where Polymarket is thin. Low priority: Polymarket coverage was 97%.
- **W6. Catalyst calendar (#29, #30, #72).** Widen around debates, NYT/Siena releases and Cook rating changes, then ladder for the overreaction.
- **W7. End-of-tournament mark risk (#58, #59, #63).** If final balances use marks on unresolved markets, hold little in thin markets at the close (others could move marks). We must never move marks ourselves. **Rule Q1.**

## Rule questions for the owner

1. How is "final SUSQie balance" computed for positions unresolved at 4 Nov noon ET (resolution, last trade, mid or valuation price)?
2. Is a batch of N orders 1 write or N writes against the reported 30 writes/min?
3. What is Smart Score, its minimum to be scored, and does recruiting use it?
4. What are the numeric position limits?
5. Explicit wording on spoofing, wash trading and collusion. The ladder (R3) places real, fillable orders, so it is not spoofing, but confirm the platform's view on many resting levels.

## Appendix: raw brainstorm list (88 ideas, unfiltered)
A. Microstructure: 1 join don't improve; 2 penny only when touch queue small; 3 second-level sweep catcher; 4 ladders 0.5/1.5/3/5c; 5 asymmetric size by Polymarket drift; 6 never cancel a still-good quote; 7 write budget ∝ fills×edge per market; 8 volatility-dependent order lifetime; 9 round-number shading (0.50/0.60/0.75/0.90 clusters); 10 walls as information; 11 odd-lot size fingerprints (bot vs human); 12 specialise in tails (relative spread); 13 quote only the side flow comes from; 14 first to re-post after a level clears.
B. Competing bots: 15 fingerprint reaction latency per level; 16 infer bot floors/fair from their band, quote only where our fair differs; 17 go where bots aren't (no clean Polymarket match, PM spread >3c); 18 re-widen when competitor bots die; 19 when undercut within a second, step back 2-3c to catch overshoots; 20 overnight "last bot standing" window; 21 legally take from bots lagging a Polymarket move; 22 pull quotes on Polymarket move before repricing; 23 detect competitor inventory limits (side thins); 24 quote where bots anchored to different sources disagree; 25 mass-cancel = news, pull too.
C. Fair value: 26 Kalshi+Polymarket inverse-spread blend; 27 forecast models as slow prior for quiet races; 28 Cook/Sabato ratings → probabilities for the long tail; 29 rating-change events (Thursdays); 30 poll-release calendar; 31 PredictIt as retail-bias venue; 32 Polymarket depth imbalance; 33 Polymarket trade tape; 34 model-implied seat counts vs control; 35 generic ballot → swing factor; 36 early-vote overreaction fade; 37 shrink thin Polymarket toward 0.5 / weight by depth+age; 38 book EMA fair where no Polymarket.
D. Structure: 39 party sum to 1 (Maine 1.43); 40 independents (sum<1 by design); 41 control vs race basket Monte Carlo; 42 hedge headline with toss-up races; 43 monotonicity across rated races; 44 Gov/Senate same-state correlation; 45 PCA national-swing risk model; 46 arb trigger 1.5c not 3c; 47 quote the less-competed twin at 1−fair.
E. Student behaviour: 48 partisan (Dem) bias; 49 favourite-longshot bias; 50 home-state bias; 51 evening-ET activity peak; 52 weekend effect; 53 headline overreaction jump-fade with time stop; 54 leaderboard herding; 55 late-tournament lottery-ticket longshot buying; 56 0.50 anchoring in toss-ups; 57 fade unconfirmed rumour spikes.
F. Mechanics: 58 prefer positions with stable marks; 59 mark convexity in thin markets; 60 reverse-engineer Smart Score; 61 variance game by rank late; 62 lock in a lead in final week; 63 settlement/close at tournament prices before results; 64 capital efficiency; 65 fees/rebates; 66 per-market position limits.
G. Timing: 67 debate nights; 68 October-surprise protocol; 69 stale-Polymarket detection; 70 slow-exchange periods: pull rather than reprice; 71 last-48h exit liquidity; 72 catalyst calendar.
H. Risk: 73 inventory half-life target; 74 correlated-group limits; 75 close dust positions; 76 size by venue agreement; 77 markout-driven auto-widen; 78 toxic counterparty-size memory; 79 hedge inside the tournament (paired contract) not on Polymarket; 80 self-trade prevention across twins.
I. Ops: 81 write coalescing/cancel priority; 82 AIMD request budget; 83 fill-matching fix; 84 shorter expiry on headline markets; 85 shadow-fair logging; 86 A/B by market halves.
J. Outlandish: 87 become the reference price in quiet races; 88 liquidity provider of last resort in crashes (Dem House 0.816 rebound).

Mapping: R1 is data-only (not in the brainstorm). R2 ← #73, #77. R3 ← #3, #4, #88. R4 ← #1, #2, #19. R5 ← #17, #38. R6 ← #81, #82, #83. R7 ← #45, #74. R8 ← #5. The other brainstorm items are covered in T1-T12 and W1-W7 or remain untested above. #79 (hedge on Polymarket) is excluded: the tournament uses play money and the scope is the tournament only.
