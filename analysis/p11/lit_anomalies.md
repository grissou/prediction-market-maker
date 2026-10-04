# P11 literature sweep, reader 1: PRICING ANOMALIES and the FAVOURITE-LONGSHOT BIAS (theme tag ANOM)
Reader 1, 4 Oct 2026, 11:20-12:10 UTC. About 45 web searches and fetches; 38 sources read (abstract-level or deeper), 9 could not be
accessed (listed at the end). One small read-only check on `/home/claude/snap03/md.sqlite`: the tilt by price bucket and per-race price
sums at 1, 2 and 3 Oct 22:xx. I ran no sims and no tests.

## 0. Three facts from our own data that frame the literature (snap03, last snapshot per market each evening, spread <= 10c)
| bucket (Polymarket p) | 1 Oct: tourn mid - p | 2 Oct | 3 Oct 22:47 |
|---|---|---|---|
| p < 3c (n 36) | +2.1c | +3.5c | **+7.0c** (1.8c -> 8.8c) |
| 3-6c (n 13) | +2.4c | +3.8c | **+7.1c** (4.2c -> 11.3c) |
| 6-10c | +1.4c | +2.1c | +4.9c |
| 90-94c | -0.9c | -1.7c | -4.6c |
| 94-97c (n 26) | -1.7c | -2.7c | **-5.2c** (95.8c -> 90.5c) |
| > 97c (n 20) | -1.9c | -2.6c | **-5.7c** (98.0c -> 92.3c) |
| logit slope, tournament on Polymarket | 0.87 | 0.82 | **0.70** |
* (a) The tilt is accelerating. The logit slope falls 0.87 -> 0.82 -> 0.70, which matches s 1.7% -> 12.6%. On the logit scale it is the
  same compression the literature measures (a "slope" below 1 means prices are pulled toward 50%).
* (b) It is asymmetric. The YES longshot is overpriced (+7.0c) more than the favourite is underpriced (-5.7c). The mean sum of mids per race
  is 1.009. This matches the "optimism tax" in the Kalshi data: takers buy the affirmative longshot (Becker 2025).
* (c) Race books are crossed. The best bids sum to more than 1 in many races (1.02-1.03: Michigan Senate, NH Senate, MN Gov/Senate, VA-05,
  VA-07, MS Senate, AK Gov, RI Gov ...). The best asks sum to less than 1 in others (0.975-0.98: Arkansas Senate, Louisiana Senate, AZ-01,
  CT Gov, MT-01, TX-28). Our own resting quotes may be part of these books. The race-level self-check is in ANOM-5.

## 1. Sources (what each says that matters to us)
1. **Reichenbach & Walther (?) "The Favorite-Longshot Bias in Prediction Markets: Evidence from Polymarket", arXiv 2609.12878, 2026**
   (https://arxiv.org/abs/2609.12878). 561M purchases, $22.5bn. Longshots (0-10c) return -6.3% equal-weighted and **-19.4%
   dollar-weighted**. Their probability error is **-4.5 pp**. Favourites (90-100c) return +0.3% / +0.8%, a probability error of only
   -0.13 pp. Politics shows the classic two-sided bias; sports does not. The losses persist across experience levels and fee regimes.
   For us: Polymarket's own longshots run slightly rich, while its favourites are about right.
2. **Bürgi, Deng & Whelan "Makers and Takers: the economics of the Kalshi prediction market", UCD WP2025_19 / GWU 2026-001**
   (https://www.karlwhelan.com/Papers/Kalshi.pdf). 314k prices, 46k contracts. Buyers of 1-10c contracts lose more than 60%; a 5c
   contract wins about 3%. Contracts above 70c earn small but significant positive returns. Makers return -12% and takers -31%. Forecast
   error falls toward the close, but the price is biased at every horizon from 0 to 10 days. **Politics shows a weaker, insignificant
   bias** (coefficient 0.022 vs 0.034 overall).
3. **Becker, "The Microstructure of Wealth Transfer in Prediction Markets", 2025** (https://www.jbecker.dev/research/prediction-market-microstructure).
   Kalshi, 72M trades. **1c contracts win 0.43%, 5c win 4.18%, 95c win 95.83%.** NO beats YES at 69 of 99 price levels; at 1c, YES has an
   EV of -41% and NO +23%. Makers earn +1.12% and takers -1.12%, and makers earn the premium by being passive, not by forecasting.
   Politics has a maker-taker gap of 1.02 pp.
4. **Le, "Decomposing Crowd Wisdom: Domain-Specific Calibration Dynamics in Prediction Markets", arXiv 2602.19520, 2026**
   (https://arxiv.org/pdf/2602.19520). 353M trades on Kalshi and Polymarket, logit recalibration logit P(y) = a + b·logit(p).
   **Politics is underconfident at almost every horizon: Kalshi b 1.32-1.83 (1.83 at 2d-1mo); Polymarket b 1.08-2.08 (2.08 at 2d-1w,
   1.67 at 1w-1mo).** Electoral-college contracts run 1.53-2.87. Large trades are more compressed (Kalshi politics b 1.74 vs 1.19 for single
   contracts). Overall, b rises from 0.99 at 0-1h to 1.32 beyond 1 month. Prices are filtered to 5-95c.
5. **Page & Clemen, "Do prediction markets produce well-calibrated probability forecasts?", Economic Journal 2013**
   (https://people.duke.edu/~clemen/bio/Published%20Papers/45.PredictionMarkets-Page&Clemen-EJ-2013.pdf). Intrade: well calibrated near
   expiry, an S-shaped favourite-longshot bias more than a month out (0.20 resolves 15.3%, 0.80 resolves 87.4%), strongest in politics. The
   explanation is time preference with no interest on locked capital. Exploiting it returns 5-10%, which vanishes at discount rates of
   15-25%. The bias shrinks as expiry approaches *when expiry = resolution*.
6. **Restocchi, McGroarty, Gerding, Johnson, "The temporal evolution of mispricing in prediction markets", Finance Research Letters 2019**
   (https://eprints.soton.ac.uk/423232/). 3,363 PredictIt political markets. The average favourite-longshot bias is 0.025. **In the final
   24 hours it jumps to 0.058 (short markets) and 0.151 (markets over 50 days)**. The authors attribute this to herding by late,
   unsophisticated, media-driven traders when informed traders lack the time to correct it.
7. **Whelan, "Agreeing to Disagree: The Economics of Betting Exchanges", UCD WP2025_22** (https://www.karlwhelan.com/Papers/Betfair.pdf).
   A model of heterogeneous beliefs: a near-constant gap between breakeven and true probability gives takers a proportional loss that
   explodes at low probability. **In-play, the bias intensifies as the match nears its end** (taker losses on longshots near 70% by minute
   105), the "Yogi Berra effect".
8. **Snowberg & Wolfers, "Explaining the favorite-longshot bias: is it risk-love or misperceptions?", JPE 2010** (NBER w15923). Racetrack:
   1/3 favourites return -5.5% and 100/1+ longshots -61%. Misperception (a Prelec weighting function) explains it better than risk-love:
   "systematically poor at discerning between small and tiny probabilities". The bias concentrates at the extremes and persists because it
   is too small to arbitrage after the take.
9. **Ottaviani & Sørensen, "Aggregation of information and beliefs: asset pricing lessons from prediction markets" / AER 2015 "Price
   reaction to information with heterogeneous beliefs and wealth effects"** (https://web.econ.ku.dk/sorensen/Papers/AggregationofInformationandBeliefs.pdf).
   With heterogeneous priors and limited wealth, prices underreact, which compresses them toward 1/2 (a favourite-longshot bias with no
   misperception). The bias is larger when beliefs are more dispersed. Dynamics: underreaction, then momentum, then reversal. Under CARA
   with no binding wealth limit there is no bias.
10. **Ottaviani & Sørensen, "The timing of bets and the favorite-longshot bias"** (https://web.econ.ku.dk/sorensen/Papers/tobaflb.pdf).
    Informed money arrives at the deadline, so late moves favour the eventual winner: favourites shorten and longshots lengthen in the final
    moments. **Green, Lee & Rothschild "The Favorite-Longshot Midas" (Wharton 2018)** confirms this at the track: in the last 15 minutes,
    favourites shorten as rebated arbitrageurs bet. This is the counter-force to source 6.
11. **Wolfers & Zitzewitz, "Interpreting prediction market prices as probabilities", NBER w12200** (https://www.stat.berkeley.edu/~aldous/157/Papers/InterpretingPredictionMarketPrices.pdf).
    The price equals the **wealth-weighted** mean belief (log utility). Risk-love (CRRA below 1) biases prices toward 50%. Deviations are
    usually within 1 pp in the 20-80c range and largest at the extremes and when beliefs are dispersed. Manski's worst case is the
    (1 - π) percentile of beliefs.
12. **Rothschild & Sethi, "Trading strategies and market microstructure: evidence from a prediction market", JPM 2016** (Intrade 2012).
    One trader ("B") held about 15% of volume and a third of Romney buying, and lost $6.9M. He kept Intrade 5-10 pp off Betfair for months,
    because cross-venue arbitrage was capital-locked, ran across currencies and needed separate deposits. Arbitrageurs (1% of traders, 16% of
    volume) made steady small profits on tiny margin.
13. **Rhode & Strumpf, "Manipulating political stock markets" (2008)**. Real-money manipulations revert: on IEM half the effect is gone in
    2.5 h and all of it in 4-24 h; on TradeSports, 5 minutes; in the 1880-1944 New York markets, 1-3 days. Reversion happens **when
    counter-traders have capital**.
14. **Forsythe, Rietz & Ross, "Wishes, expectations and actions: price formation in election stock markets", JEBO 1999**, and the
    marginal-trader hypothesis. Individual IEM traders show partisan wishful thinking. A small set of unbiased *marginal* traders with
    capital sets accurate prices.
15. **2022 midterms post-mortems**: Gelman blog (https://statmodeling.stat.columbia.edu/2022/11/16/how-good-are-election-prediction-markets/)
    and Bransfield (https://mickbransfield.com/2022/12/18/did-bookmakers-prediction-markets-fare-that-badly-in-the-2022-senate-races/).
    Markets priced R House about 90% and R Senate about 72% and over-predicted the red wave. PredictIt's crowd leans right (Rothschild).
    Senate Brier: Metaculus 0.166, 538 0.193, PredictIt 0.251. Smarkets was less confident than the models (Brier 0.046 vs 0.035-0.039).
16. **Sethi, Seager et al., "Models, markets, and the forecasting of elections", 2021** (https://arxiv.org/abs/2102.04936). 2020: PredictIt
    state markets vs the Economist model scored Brier 0.1539 vs 0.1523; **the simple average scored 0.1499**. The market was compressed
    (excess uncertainty in safe states such as MN and NH). A model-driven bot made +16%, but flipping 2 close states would have reversed it.
17. **Clinton & Huang, "Prediction markets? The accuracy and efficiency of $2.4 billion in the 2024 presidential election", SocArXiv 2025**.
    Identical contracts diverged across IEM, Kalshi, PredictIt and Polymarket. **Arbitrage opportunities surged in the final fortnight.**
18. **Torul et al., "Price discovery across political prediction markets"** (https://web.bogazici.edu.tr/torul/pridis.pdf). In 2024,
    Polymarket's information share was 47.5% and Betfair's 37.1%. Kalshi reached 40.9% within 32 days of launch. Cross-venue spreads
    averaged 7.2 pp before Kalshi and 4.2 pp after; the 95th percentile went 10.4 -> 6.1 pp. No mechanism enforces one price.
19. **"The anatomy of a blockchain prediction market: Polymarket in 2024", arXiv 2603.03136**. Kyle's lambda fell 0.53 -> 0.01 (Jul -> Oct).
    The half-life of a sum-to-$1 deviation fell from hours to under 1 minute. The "French whale" money was matched by Democratic money
    (heterogeneous beliefs, not steering).
20. **Saguillo et al., "Unravelling the probabilistic forest: arbitrage in prediction markets", AFT 2025 / arXiv 2508.03474**. On Polymarket,
    about $40M was extracted Apr 2024-Apr 2025 from market rebalancing (YES sums not equal to 1) and combinatorial arbitrage (state race vs
    national control). Of 1,576 dependent election pairs, 13 held strict combinatorial arbitrage.
21. **Zvi, "Free money at PredictIt?" (LessWrong)** and **"Arbitrage in political prediction markets" (JPM)**. PredictIt fields summed to
    112-114%. Prices were chronically off because of the 10% profit fee and the $850 cap. IEM, with no such frictions, was much cleaner.
    Mispricings live where the correcting capital is capped.
22. **Maresca, "Can interest-bearing positions solve the long-horizon problem?", arXiv 2602.21091, 2026**. Capital lockup compresses prices
    toward 50% because **a short at low prices needs 0.80 of collateral against 0.20 for the long**. Intrade biases were 4.7-10.9 pp at more
    than 100 days. Paying interest removes 83% of the effect, mainly by restoring participation.
23. **Berg & Rietz, "Longshots, overconfidence and efficiency on the IEM"** (https://www.biz.uiowa.edu/faculty/trietz/papers/longshots.pdf).
    The IEM's *financial* winner-take-all markets show **overconfidence** (the reverse bias) at 3-21-day horizons and are unbiased at 1-2
    days. The direction of the bias is not universal.
24. **Recent Polymarket calibration repos.** shirleyshen0106/polymarket-calibration (16.8k markets, 1-day quotes, mostly sports/esports):
    **reverse** bias (0-5c: 1.7% quoted vs 3.7% realised; 95-100c: 98.0% vs 95.9%). CH4RL3I/honest-odds (2.9k markets, 7-day): slope
    1.09-1.12 (slightly too moderate); its out-of-sample recalibration P&L CI includes 0. raultinajeroo/longshot (Manifold, 278 markets):
    slope 1.04-1.08, and Platt/isotonic corrections give **no reliable out-of-sample gain**.
25. **McCullough Polymarket accuracy study (predictionnews.com)**: "underdog bias"; a 67% price resolved about 63%.
26. **Rank-incentive / contest literature.** arXiv 2409.19477 (winner-take-all forecasting contests): laggards *extremise*, leaders *hedge*
    toward the middle. Oxford "Winning ways: how rank-based incentives shape risk-taking": convex prizes lead contestants to choose
    **positively skewed** outcomes, and dispersion triples (sd 3.4 -> 10.2) from elimination to winner-take-all. Brown-Harlow-Starks
    1996 (abstract-level): mid-period losers raise risk. JMU stock-game advice: "you have to take a lot of risk to win ... don't buy too
    many different stocks".
27. **Predictions Cup rules** (https://predictionscup.com/rules/). The rank is "based on ... total SUSQies balance as of the resolution of
    all Markets". The leaderboard is mark-to-market in real time. **Only 3 prizes ($30k / $5k / $2.5k)**. "All trades must be received
    prior to 12:00pm ET on November 4" (see the note at the end).
28. **Covers.com midterm tracker (1 Oct 2026)**. Senate control: Kalshi D 63 / R 37, Polymarket D 63 / R 38. House: D 91 vs 93. Battleground
    Senate races agree within 1-2 pts (TX 47/53 vs 49/52; GA 96 vs 97; NC 94 vs 96).
29. **Ziemba, "Pari-mutuel betting markets: racetracks and lotteries revisited", Annual Review 2023**, and **Thaler & Ziemba, JEP 1988**
    (abstract-level). The bias is persistent and exploited only by syndicates with Kelly sizing and the capital to wait.
30. **Glassman, "Intrade's favorite-longshot bias"**. The Jan 2012 nomination markets had Obama at 94.5 where he "should be about 99.5",
    and 0.1-5c candidates where they "should be" far lower. People cannot tell 1-in-500 from 1-in-5000.
31. **Restocchi et al. (2018 EJOR "It takes all sorts"; 2019 Physica A stylised facts; 2023 Entropy opinion dynamics)**. Heterogeneous
    agents, not a representative agent, are needed to get the bias. PredictIt price changes look like emerging-market returns (fat tails,
    clustering). Participation declines as expiry nears.
32. **Akey, Grégoire, Harvie, Martineau, "Who wins and who loses in prediction markets? Evidence from Polymarket", CEPR DP21615**
    (abstract). The top 1% of winners take 76.5% of profits. **Winners use limit orders, losers use market orders.** Persistence is modest.
33. **Robin (EA Forum), "Manifold markets isn't very good"**. Play-money markets run a YES bias (Brier 0.168 vs Metaculus 0.111).
    Wealth goes to non-forecasting activity (bonuses, "whalebait") and oligarchs pin prices. The time-value problem applies here too.
    Manifold's own newsletter "play-money efficacy" claims the top 4 forecasters of the 2022 midterms were play-money sites.
34. **Rosenbloom & Notz 2006 and Servan-Schreiber, Wolfers, Pennock & Galebach 2004 (abstract-level only; the PDFs were blocked)**.
    For NFL, play money (NewsFutures) was as accurate as real money (TradeSports), because the wealth of a play-money market goes to good
    forecasters. Real money beats play money on non-sports events. The iPredict replication (JPM): real money is more accurate on identical
    events.

## 2. Ideas

### ANOM-1. Stop planning on reversion before close: the tilt is a hold-to-outcome edge   [source: Page & Clemen 2013; Restocchi et al. 2019 FRL; Whelan 2025 Betfair; Le 2026 arXiv 2602.19520]
What the source says: The bias shrinks toward 0 only as a contract nears *resolution* (Page-Clemen; Kalshi MAE falls on the last day). In
the final 24 h before a market *closes*, mispricing jumps (PredictIt: 0.025 -> 0.151), and in-play longshot overpricing grows toward the
end (Betfair). The politics compression peaks 2d-1mo out.
Idea for us: Our markets close on 4 Nov before results, so no mechanism forces convergence before close. Every pressure the literature
names (herding late traders, a fading time to correct, contest convexity; see ANOM-7) pushes the tilt *up* into close. Treat the 5-10%
edge as realised only at the outcome. Set the "recycling on convergence" plan (b) to zero in the owner table (today +1.6k
probability-weighted). Keep the allocator's sell-within-2c rule, but expect it to fire mainly *after* close, i.e. never. Do not report
mark-to-market losses on the value book as risk; they are the tilt.
Value: E unchanged; it removes an optimistic +1.6k assumption (plan (b) -> plan (a), E 114.9k -> ~113k honest). P(<= 85k) unaffected. It
blocks the failure mode where a falling mark triggers selling below p. | Cost: documentation; the value-mode guard already blocks sales
below p.
Check: snap03 `snapshots`: fit s per hour, 28 Sep -> 3 Oct, and check that it is monotone (the logit slope already goes 0.87 -> 0.82 ->
0.70 over three evenings). Keep the fit running daily.
Fair play: ok.

### ANOM-2. Treat the NO+NO sets (21.9k) as tilt-harvest inventory: ladder the rich leg out, do not unwind at cost   [source: Restocchi et al. 2019; Whelan 2025; Becker 2025; Akey et al. 2026 (limit orders win)]
What the source says: Mispricing peaks in the final day (PredictIt 0.151). Makers earn the longshot premium passively, and winners use
limit orders rather than market orders.
Idea for us: A NO+NO set in a 2-leg race = NO-favourite + NO-longshot. When the tilt is high, the **NO on the favourite** is the rich leg:
on 3 Oct, >97c favourites mid at 92.3c, so NO-fav is about 7.7c vs a fair ~2c. Selling the NO-fav leg (a set-aware covered sale) leaves a
pure short-longshot position at an entry about 5.7c better than fair. Rest those sales as a **ladder**: one level at the current book, one
+2c and one +4c above. That captures today's tilt and late spikes without spending writes chasing. Do it instead of the plan's "unwind
sets by cost per $ freed". The set's only alternative use is cash at par, and the literature says the rich leg gets richer into close.
Value: on 21.9k of sets (about 10.9k sets of 2 legs, depending on composition), selling the rich leg at +5 to +8c over fair is +0.5-0.9k at
the outcome vs about +0.07k for unwinding at cost. P(<= 85k): it adds short-longshot exposure, so it falls under the bloc-delta cap.
| Cost: settings: `no_set_aware_bids` / covered-sale paths exist (Package 7). The ladder needs about 30 lines (2 extra price levels on the
covered-sale side, under the value hurdle `value_quote_hurdle`).
Check: snap03 `books` + `status.json nono_sets`: for each set race, compute (NO-fav bid - (1 - p_fav)) per share now. From the journal,
how often did the NO-fav best bid trade 2c and 4c above the 22:47 level in the last 48 h? If never, a single level is enough.
Fair play: ok (resting limit orders, no self-trade; the ladder must never cross our own bids).

### ANOM-3. Keep 10-15k of dry powder for T-48h and release it in tranches keyed to tilt_s   [source: Restocchi 2019; Clinton & Huang 2025 (arbitrage surged in the final fortnight); Ottaviani-Sørensen 2015 (momentum)]
What the source says: Political mispricing and cross-venue arbitrage peak in the last 1-2 weeks and the last day. With wealth-constrained
heterogeneous priors, prices show momentum before they reverse.
Idea for us: Free cash freed by the allocator's low-edge sales (a') or by set sales (ANOM-2) does not have to be spent at s = 14%. Hold
`alloc_mm_reserve` and release it in tranches: 25% at tilt_s >= 16%, 25% at 18%, 25% at 20%, the rest at T-48h whatever s is. Each
release raises the edge per $. A 3c-true longshot short at c 0.5 goes from 7.3% per $ at s 14% to 11.9% at s 22%. The released cash goes
to value asks/bids through the allocator with `alloc_min_edge_buy` stepped up with s (0.05 -> 0.08 -> 0.10).
Value: if s rises 14 -> 20% by close (the literature's direction), 15k deployed late earns about +1.7k instead of +1.1k now (+0.6k). If s
reverses (the leader exits, ANOM-7), the cost is about -0.5k of forgone edge. Probability-weighted about +0.2-0.4k. Nothing for
P(>= 150k). | Cost: one rule in `alloc_tick` (a reserve schedule from tilt_s and hours-to-close), about 25 lines plus 2 settings
(`alloc_reserve_release_s`, `alloc_reserve_release_hours`).
Check: H_outcome.py / I_alloc.py with s paths {flat 14%, linear to 20%, spike to 25% on the last day, reversal to 8%}, comparing E and
P(>= 120k) between deploy-now and tranches.
Fair play: ok.

### ANOM-4. On the same race exposure, prefer selling the YES longshot over buying the YES favourite   [source: Becker 2025 (NO beats YES at 69/99 levels; YES-longshot "optimism tax"); Reichenbach-Walther 2026; snap03]
What the source says: Takers overpay most for *affirmative* longshots. At 1c, YES has an EV of -41% and NO +23%. The bias is two-sided but
heavier on the longshot.
Idea for us: Our 3 Oct data shows the same asymmetry: longshots +7.0c and favourites -5.7c, a mean race sum of mids of 1.009. In a 2-leg
race, "buy fav YES" and "sell longshot YES" are the same outcome exposure, but the short leg is about 1c better on average. The allocator
already computes edge per $ for both (`alloc_plan`, mm_bot.py ~9857/9860). Make sure value MM in the tails rests the **longshot ask**
before the favourite bid when cash allows only one (`value_quote_hurdle` treats them symmetrically through p). Add a tie-break: when
both clear the hurdle in the same race, size the longshot ask at 1.5x and the favourite bid at 0.5x.
Value: about +1c per share on the tail turnover. On 40-60k of tail turnover that is about +0.4-0.6k at the outcome. Risk neutral (same
exposure). | Cost: about 15 lines in the value quoting size split; no new setting, or `value_tail_short_bias` 1.5.
Check: snap03 `books`: per 2-leg race, compare (1 - longshot best bid) with fav best ask over the last 48 h, and count how often the short
leg is cheaper and by how much.
Fair play: ok.

### ANOM-5. Riskless race Dutch books: switch the sell-side and buy-side arbitrage back on as cash parking, ranked behind value   [source: Saguillo et al. 2025 ($40M on Polymarket); Zvi / JPM PredictIt arbitrage; Polymarket 2024 anatomy (half-life < 1 min once arbitrageurs have capital)]
What the source says: Sum-of-prices deviations persist where correcting capital is capped (PredictIt fees and caps; early Polymarket) and
die in under 1 minute when arbitrage capital exists.
Idea for us: Tournament arbitrage capital is capped because everyone holds a fixed 100k, and ours is about 0 free. On 3 Oct, many races had
best bids summing to 1.02-1.03 and some had asks summing to 0.975-0.98. Selling YES on all legs at bids summing to 1.03 (collateral
2 - 1.03 = 0.97 for a sure 1.00) is +3.1% per $, riskless. Buying all YES at 0.975 is +2.6%. That is below the 5-10% value edge, so it
should not take cash from value trades. It does beat idle cash and the "set cost" `alloc_set_cost_per_usd` 0.06 view, and it *lowers*
P(<= 85k). Enable `arb_enabled` with `arb_min_profit` 0.02, only from cash above the value need. Exclude our own resting orders from the
sum, because a "crossed" book made of our own quotes is a wash-trade risk.
Value: small. With about 5k parked at 2.5-3% it is +0.15k. It is riskless, though, and it tightens the race books the leader trades
against. P(<= 85k) slightly down. | Cost: config (`arb_enabled`, `arb_min_profit`, `arb_two_sided`), already built (mm_bot.py ~7340).
Check: snap03 `books` for the last 48 h. Recompute race sums excluding `our_bid`/`our_ask` from `snapshots`, and count arbitrage of at
least 2% per hour, its size and its lifetime.
Fair play: ok if the opposite side is never our own order (the code must skip any race where one of our resting orders is part of the
sum).

### ANOM-6. The tilt is a wealth-weighted belief: watch the longshot buyers' cash, not the calendar   [source: Wolfers & Zitzewitz 2006; Ottaviani & Sørensen 2015; Forsythe et al. 1999 (marginal trader); Shleifer-Vishny 1997; Maresca 2026 (collateral asymmetry)]
What the source says: The price is the wealth-weighted mean belief. With binding wealth limits, prices underreact and compress toward 1/2.
Accurate prices need *marginal* traders with capital. A short at low prices needs about 4-10x the collateral of a long, so
capital-constrained correctors fade.
Idea for us: The tournament has exactly that structure. The longshot side needs 9c per contract; the correcting side (us, other bots)
needs 91c and is out of cash. s keeps rising until the tilt-long side runs out of cash, or until marginal (value) capital arrives. Add
two cheap monitors to `status.json`. (1) Resting depth on the longshot ask side vs the bid side across the 36 sub-3c markets: buying
pressure that the asks absorb is the leading indicator. (2) The tilt-long leader's book (from the leaderboard, if public), whose cash
exhaustion should cap s. Use them to time ANOM-3's tranches. Also, never treat the tilt as "too big to last". The literature's
corrections all required free capital.
Value: indirect (timing). Prevents the error of deploying everything early at s 14% when s heads for 20%+. | Cost: about 30 lines of
status diagnostics; no trading change.
Check: snap03 `books`: aggregate ask-side vs bid-side qty for p < 6c markets per hour, and test whether it leads changes in s (a
lead-lag correlation over 28 Sep -> 3 Oct).
Fair play: ok (observing public books).

### ANOM-7. Contest convexity is the engine: expect late Hail-Mary longshot buying, plus one reversal risk (the leader hedging)   [source: Predictions Cup rules (3 prizes, mark-to-market leaderboard); arXiv 2409.19477; Oxford "Winning ways"; Brown-Harlow-Starks 1996; JMU stock-game advice]
What the source says: In winner-take-all contests, laggards extremise and take positively skewed bets, and leaders hedge to lock their
lead. Dispersion triples under winner-take-all. Mid-period losers raise risk.
Idea for us: About 1,000 players chasing 3 prizes will, as days run out, buy cheap, positively skewed, correlated payoffs: longshots and
party sweeps. That supports ANOM-1/2/3 (sell into it late). The leader, who is long the tilt, has the opposite incentive. If they learn
that rank is decided "as of the resolution of all Markets" (rules), the rational move is to **hedge**: sell their rich longshots and buy
back favourites. A large exit would knock s down abruptly. We gain on marks and lose nothing at the outcome. The only cost is the
forgone late entries of ANOM-3. Hence deploy in tranches, not all at T-48h.
Value: explains the regime; protects ANOM-3 from all-or-nothing timing. | Cost: none beyond ANOM-3; add a "s dropped 3 pts in 1 h" alert
to the hourly summary.
Check: untestable offline (behavioural). Watch the journal "Rank N of M" lines and the top account's marks for a sudden drop.
Fair play: ok.

### ANOM-8. Our P(>= 150k) target is itself a convex contest payoff, so the 150k lever must stay concentrated and positively skewed   [source: Oxford "Winning ways"; arXiv 2409.19477; JMU]
What the source says: To maximise P(top) or P(above a far threshold), choose positively skewed, concentrated, high-variance positions.
Diversification pulls you to the "market rate".
Idea for us: Shorting longshots and buying favourites is *negatively* skewed per contract. Across 117 races with rho 0.45 it is roughly
normal (sd 7.5k), so it cannot reach 150k (E 109-115k needs a +5 sd move). The literature agrees with VALUE_PLAN that only a concentrated,
+EV, positively skewed sleeve moves P(>= 150k). Two consequences. (1) Do not "diversify" the sleeve across many competitive races: the
bloc options (c2/c3) pay the tilt *and* dilute the skew. (2) The value book is the floor that protects P(<= 85k). Its job is low variance,
so keep `bloc_delta_enabled` with `max_bloc_delta_frac` 0.05 on it and let the sleeve carry the variance, accounted separately
(`alloc_pin`).
Value: frames the decision; no new E. | Cost: none.
Check: H_outcome.py: P(>= 150k) for (value book + sleeve) vs (value book with the sleeve split across 5 competitive Senate races of the
same $).
Fair play: ok.

### ANOM-9. Political prices are underconfident even on Polymarket: extremise the value reference slightly in the tails, for ranking only   [source: Le 2026 (Polymarket politics b 1.08-2.08); Reichenbach-Walther 2026 (Polymarket longshots -4.5 pp); Becker 2025 (5c wins 4.18%); Page & Clemen 2013; counter-evidence: Whelan Kalshi politics insignificant; honest-odds / longshot repos (no out-of-sample gain)]
What the source says: Real-money political prices 1 week to 1 month out are compressed toward 50% (b > 1). Longshots resolve below their
price and favourites at or above it. Out-of-sample recalibration gains, though, are statistically fragile.
Idea for us: Our value reference is raw Polymarket p. Use p' = logistic(b·logit(p)) with **b 1.1** (a conservative fraction of the
1.2-2.0 measured) for p < 10c or p > 90c in the value hurdle and in the allocator's edge per $. This applies only to the outcome valuation
used for *ranking* levels, never to risk (H_outcome stays on raw p). At b 1.1: p 1.8c -> 1.2c, p 4.2c -> 3.1c, p 95.8c -> 96.9c. The
effect is to rank tail value trades (longshot shorts, favourite longs) a little higher than middle trades, and to let the 8% hurdle admit a
few more tail levels.
Value: +0.6-1.1c per share on tail positions at the outcome if the literature holds in 2026 midterms. On about 60-80k tail shares that is
+0.4-0.8k E. If it does not hold (Whelan: politics weak), the cost is about 0. | Cost: a setting `value_ref_extremize_b` (1.0 = off) and
about 15 lines near `tilted_ref` (mm_bot.py:2394) and the allocator edge.
Check: there is no labelled 2026 outcome data. Proxy: compare Polymarket vs Kalshi (ref_prices.py `fetch_kalshi`) on the tail markets. If
Kalshi is systematically more extreme, that supports b > 1. Also run H_outcome's E with b 1.0/1.1/1.2 to see how much ranking changes.
Fair play: ok.

### ANOM-10. Average Polymarket with Kalshi for the reference, and skip races where the venues disagree   [source: Sethi et al. 2021 (averaging beats both); Torul et al. (price discovery shared, spreads 4-7 pp); Rothschild & Sethi 2016 (a whale held a 5-10 pp gap for months); Le 2026 (large trades more compressed); Covers tracker 1 Oct 2026]
What the source says: Venues share price discovery, gaps persist without cross-venue capital, and single whales can hold a venue off for
months, especially thin ones. Averaging independent forecasts beats each one.
Idea for us: For each race where both venues list it, set p = liquidity-weighted mean of Polymarket and Kalshi. `ref_prices.py` already has
`fetch_kalshi` and the map supports `"source": "kalshi"`. Where |Poly - Kalshi| > 3 pp, mark the market "uncertain value": the allocator
does not add there, and value MM quotes only the middle-band two-way. The thin House races (where a single Polymarket whale can set p) are
where our "edge" is most likely a reference error.
Value: lowers the tail risk of a wrong-way 10k bet on a mis-referenced race. E +0.2-0.5k from avoided bad adds. P(<= 85k) slightly down.
| Cost: ref_map additions for Kalshi tickers (some hours of mapping; 117 races), about 40 lines of blending plus a
`ref_kalshi_weight` setting (0 = off).
Check: run `ref_prices.py` check mode on today's map with the Kalshi fetch. Report the per-race gap distribution and list our 20 largest
positions where the gap is above 3 pp.
Fair play: ok.

### ANOM-11. Partisan lean in election markets: shade the Republican side in the outcome model before sizing the Rep Senate sleeve   [source: 2022 midterms (Gelman blog; Bransfield: markets underpriced Dems in GA/NV/PA; PredictIt crowd "dominated by the right"); Forsythe et al. 1999 (wishful thinking); Clinton & Huang 2025]
What the source says: Real-money US election markets over-predicted Republicans in 2022 (R Senate about 72%). Individual traders show
wishful thinking, and accuracy depends on unbiased marginal capital.
Idea for us: Our book is Rep-leaning (+2.55k per sd) and the only 150k lever is Rep U.S. Senate YES. If Polymarket still leans right in
2026, both are overstated. Run H_outcome with a Rep shade of -1 and -2 pts per race (logit-consistent) and re-derive the sleeve's E and
P(>= 150k). Require an **independent** model (Silver Bulletin / the Economist / Race to the WH) to agree with the seat-count's
0.41-0.51 Rep before going to $20k. Otherwise $10-15k.
Value: protects E. A 2-pt Rep lean costs the $20k sleeve about 20k x 0.02/0.35 = -1.1k of E and lowers its P(>= 150k) a few points.
| Cost: analysis only (H_outcome parameter).
Check: H_outcome.py sensitivity with a Rep shade of {0, -1, -2} pts. Compare Polymarket Senate race prices with one public model per race
(Kalshi 37% vs Polymarket 38% for R control on 1 Oct, so the venues agree. The question is the venues vs the models.)
Fair play: ok.

### ANOM-12. Late informed money may *compress* the tilt in the last hours: keep the ladder, do not chase   [source: Ottaviani & Sørensen "Timing of bets"; Green-Lee-Rothschild "Favorite-Longshot Midas" (favourites shorten in the final 15 minutes)]
What the source says: Informed and arbitrage money bets at the deadline, so final odds move toward favourites.
Idea for us: The contest has other bots (other SIG teams' market makers) that may hold cash for the end. If they arrive in the final
hours, s falls just when ANOM-3 says to deploy. Therefore: (i) put ANOM-3's last tranche at T-48h, not T-2h; (ii) rest it as maker ladders
(ANOM-2) rather than takes; (iii) after 3 Nov 18:00 UTC stop adding and leave resting value orders. A fill there only happens if someone
else pays us the tilt.
Value: avoids the worst timing. Neutral E. | Cost: a tranche schedule parameter (ANOM-3).
Check: untestable offline. Log s every 10 minutes over the final 48 h for next time.
Fair play: ok.

### ANOM-13. Size each tail trade by fractional Kelly on the national factor, not by edge alone   [source: Ziemba 2023 review (syndicates' Kelly sizing); Snowberg-Wolfers (bias concentrated in tiny probabilities)]
What the source says: Exploiters of the bias size by Kelly on the edge and the joint risk. The edge is largest exactly where the
probability is tiny, which is where model error dominates (people "cannot tell small from tiny", and neither can a reference with a 0.5c
tick).
Idea for us: Single-contract Kelly for a 3c-true longshot shorted at 8.8c, or a 97c favourite bought at 92c, is 50-60% of bankroll. It is
meaningless without correlation. Two practical rules. (a) Cap any single sub-3c-p contract at `alloc_max_contract_usd` 5k (currently
10k). A 1.8c Polymarket price sits on a 0.5-1c tick and resolution errors there (Polymarket longshot bias, a stale feed) swamp the edge.
(b) Rank by edge / (sd contribution under rho 0.45), which `bloc_delta` partly does already.
Value: tail-risk control. P(<= 85k) slightly down. E about -0.1k. | Cost: config (`alloc_max_contract_usd`) or a per-p cap (about 10
lines).
Check: H_outcome.py: P(<= 85k) and E with the cap at 10k vs 5k on sub-3c-p contracts.
Fair play: ok.

## 3. TOP 5 for our situation
1. **ANOM-2** (sets as tilt-harvest inventory, rich leg laddered out): it turns 21.9k of zero-edge capital into a +0.5-0.9k short-longshot
   book at the best entries, using paths already built (covered sales, the value hurdle).
2. **ANOM-1 + ANOM-3** (no reversion before close; dry powder released in tranches keyed to tilt_s, ending at T-48h): this is the
   literature's clearest time-dynamics result. Mispricing in markets that close before resolution *peaks* in the last day.
3. **ANOM-4** (prefer longshot asks over favourite bids for the same exposure): it is in our own data (+7.0c vs -5.7c) and in Kalshi's
   (NO beats YES), and it costs a tie-break.
4. **ANOM-11** (partisan-lean sensitivity before the Rep Senate sleeve), together with **ANOM-9/10** (Polymarket's own compression;
   blending Kalshi): the reference is our whole edge, so test its known biases.
5. **ANOM-5** (riskless race Dutch books as cash parking, excluding our own orders): it lowers P(<= 85k) and is already coded.

## 4. What the literature says that CONTRADICTS the current plan
VALUE_PLAN justifies the Rep U.S. Senate sleeve (the only 150k lever) partly with a **seat-count model built from the 34 race prices**
(P(Dem control) 0.49-0.59 vs the market's 0.645). The literature says political race prices are *underconfident*: Le 2026 measures
Polymarket/Kalshi politics slopes of 1.2-2.1, and Page-Clemen, Sethi et al. and Glassman find safe seats priced too close to 50%. If so,
summing them overstates the variance of the seat total and mechanically pulls a favourite's control probability toward 50%, so the
seat-count model *overstates* P(Rep control). The 2022 evidence that election markets lean Republican pushes the same way. The sleeve's
edge may therefore be smaller than the 116.1k / 24% row implies. Re-run it with race probabilities extremised (b 1.2) and a 1-2 pt Rep
shade before committing $20k (ANOM-11).
A second, softer contradiction: plan (b) "recycling on convergence" expects the tilt to fall before close. The PredictIt, Betfair and
contest-incentive results (ANOM-1/7) all predict it rises into close.

## 5. Sources I could not access (or only at abstract level)
* Servan-Schreiber, Wolfers, Pennock & Galebach 2004 "Prediction markets: does money matter?" (NBER PDF: proxy 403): abstract-level only.
* Rosenbloom & Notz 2006 "Statistical tests of real-money versus play-money prediction markets" (electronicmarkets.org, robots-blocked).
* Tetlock 2004 "How efficient are information markets?" (Columbia PDF, redirect loop).
* Brown, Harlow & Starks 1996 "Of tournaments and temptations" (Wiley 403): abstract known only.
* Dudík, Pennock et al. "A combinatorial prediction market for the U.S. elections" (ACM 403).
* CNN 24 Sep 2026 "first prediction-market election" (robots).
* SIG platform docs: Settlement & Payouts and Portfolio & Standings (JS-rendered, no body text). **Note:** the public rules say trades are
  accepted until **12:00 pm ET on 4 Nov** and rank is by balance "as of the resolution of all Markets". The brief says markets close
  4 Nov 00:00 UTC. Someone should confirm per-market close times in the API (a market may close at 00:00 UTC while the contest window
  runs to noon ET).
* Akey et al. full text (dokumen.pub down): abstract only.
* Manifold's calibration chart (image only).
