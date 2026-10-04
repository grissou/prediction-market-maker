# P11 literature sweep, reader 2: MARKET MAKING, INVENTORY CONTROL and THIN-BOOK MICROSTRUCTURE (theme tag MM)
Reader 2, 4 Oct 2026, 11:36-12:10 UTC. About 45 web searches and fetches; 31 sources read (abstract-level or deeper), 8 could not be
accessed (listed at the end). Three small read-only checks on `/home/claude/snap03` (`fills.csv` and `md.sqlite` snapshots): fill edge and
post-fill drift by band, spread width in ticks, and how concentrated the fill edge is across markets. I read `mm_bot.py`'s `compute_quote`,
`plan_sizes`, `change_key`, `side_needs_change` and the live `settings_override.json`. I ran no sims and no tests.

## 0. Four facts from our own data that decide which parts of the literature apply
| check (snap03) | result | what it means for the literature |
|---|---|---|
| Post-fill fair-value drift. For each fill: (fv_after - fv_at_quote) x side, in c/share, by band and side, over all 5.8k fills | between **-0.01c and -0.08c** in every band and side | Measured against Polymarket, adverse selection is about **zero**. The tournament's flow is uninformed retail. The Glosten-Milgrom / VPIN spread component is ~0, so our spread should be set by capital cost and the queue, not by toxicity. |
| Quoted edge vs fv at fill, by band and side | value sides: longshot asks (<15) **+2.6c**, favourite bids (>85) **+2.0c**, middle asks +1.3c. Anti-value sides: longshot bids -0.65c, favourite asks -0.42c, middle bids -0.13c (mostly reducing exits) | Whelan / Bürgi-Deng-Whelan predict this pattern: makers win on the side retail overpays. |
| Spread of the others' book in ticks (3 Oct) | 1 tick in **69%** of snapshots in the middle and **56-57%** in the tails; 2 ticks 23-25% | At a 0.5c tick we are a **large-tick** market (Dayri-Rosenbaum). Queue position carries value, and spread capture versus the mid is at most 0.25c. |
| How concentrated the fill edge is across the 207 markets with fills | top 10 markets = **32%** of the positive edge, top 20 = 47%, top 40 = 68%, top 80 = 90%. Top 40 by fill count = 49% of fills | A write spent on market #150 is worth a small fraction of one spent on market #10. Expected value per write is very uneven (MM-3). |
Also: since 3 Oct 12:00, **88-94% of snapshots show no resting quote of ours** on a given side (no cash). Today, market making is
capital-bound, not write-bound. Ideas MM-3/4/5/13 matter only once the allocator frees cash; MM-1/2/11/12 matter now.

## 1. Sources (what each says that matters to us)
1. **Avellaneda & Stoikov, "High-frequency trading in a limit order book", Quant. Finance 2008** (https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf).
   The reservation price is r = s - q·γσ²(T-t). The total spread is γσ²(T-t) + (2/γ)ln(1+γ/k). Fill intensity is λ(δ) = A·e^(-kδ).
   In their simulation, inventory-skewed quotes give up ~7% of mean profit and cut its sd 56% (5.9 vs 13.4) and the final-inventory sd 3x.
   For us, the "(T-t)" risk term is the wrong clock: a binary's remaining variance is p(1-p) until the result arrives as a jump.
2. **Guéant, Lehalle & Fernandez-Tapia, "Dealing with the inventory risk", arXiv 1105.3115, 2012/13** (https://arxiv.org/abs/1105.3115).
   Closed form far from T: δ_b(q) ≈ (1/γ)ln(1+γ/k) + (2q+1)/2·sqrt(σ²γ/(2kA))·(1+γ/k)^(1+k/γ), with the ask mirrored. At the
   inventory bound Q the maker **stops quoting the adding side** rather than skewing further. Quotes are almost independent of t far from T.
3. **Guéant / Bergault, Evangelista, Guéant, Vieira, "Closed-form approximations in multi-asset market making", arXiv 1810.04383, 2021**
   (https://arxiv.org/pdf/1810.04383). With correlated assets, the skew of asset i is driven by (Σq)_i, the covariance-weighted
   **portfolio** inventory, not by q_i alone. Long one asset therefore means quoting a positively correlated asset lower too.
4. **Feil & Nendel, "Optimal market making in prediction markets", arXiv 2607.17991, 2026** (https://arxiv.org/pdf/2607.17991). A binary
   contract on a logit-diffusion belief, with a running penalty γq²ς² and a terminal settlement penalty γ_T·q²·p(1-p). Spreads are widest at
   p = 0.5 and narrow in the tails. Even at zero inventory there is an endogenous skew in the tails. Near settlement the spread widens
   only for p near 0.5. Against a myopic maker: same mean P&L (12.39 vs 12.47), P&L sd -63%, mean |terminal inventory| 49 -> 15, 5% VaR
   32 -> 4. The model has **no own view**: the maker takes the market price as the belief.
5. **"Toward Black-Scholes for prediction markets: a unified kernel and market-maker's handbook", arXiv 2510.15205, 2025**
   (https://arxiv.org/html/2510.15205). Quote in logit space: a symmetric logit spread maps to tighter absolute cents in the tails.
   It gives a cross-event hedge ratio β·p_A(1-p_A)/(p_B(1-p_B)) and models co-jumps between related events. "Anti pick-off": when only
   one side keeps trading with you, widen or pause that side. It warns that gamma explodes near the boundaries close to resolution.
6. **Cont, Kukanov & Stoikov, "The price impact of order book events", J. Fin. Econometrics 2014** (https://arxiv.org/pdf/1011.6402).
   OFI = signed changes of the best-bid and best-ask queues. ΔP = β·OFI with **β ∝ 1/depth** (exponent 0.98). R² is 65% for OFI vs 32%
   for trade imbalance. Thin books move most per unit of flow; the horizon is seconds.
7. **Stoikov, "The micro-price", Quant. Finance 2018** (SSRN 2970694; summary via arXiv 2507.09734). For large-tick assets the
   martingale price sits between the mid and the imbalance-weighted mid, P ≈ (1-β/2)·mid + (β/2)·weighted mid.
8. **Cartea & Wang, "Market making with alpha signals", IJTAF 2020** (https://ora.ox.ac.uk/objects/uuid:c2ba6656-8eab-4b2e-a24a-e9e842d1378f/files/s41687h481).
   With a drift signal α: near α ≈ 0 quote both sides. With large positive α, post only bids. Beyond a threshold, **take** with market
   orders. A higher inventory tolerance gives more profit and more variance.
9. **Fodra & Labadie, "High-frequency market-making with inventory constraints and directional bets", arXiv 1206.4810, 2012**
   (https://ar5iv.labs.arxiv.org/html/1206.4810). Drift enters as a non-zero **target inventory** ∝ drift/(γσ²). Quotes skew toward the
   target rather than toward flat. They report +15% mean P&L for more risk, or Sharpe x2 for -5% of P&L.
10. **Guilbaud & Pham, "Optimal high-frequency trading with limit and market orders", arXiv 1106.5040, 2013** (https://arxiv.org/pdf/1106.5040).
   The optimal strategy has a make zone and a take zone, switching by inventory and spread. Improve by one tick only when the spread
   exceeds one tick. Against a constant best-bid/ask strategy: information ratio 2.1 vs 0.47, max |inventory| 241 vs 608.
11. **Law & Viens, "Market making under a weakly consistent limit order book model", arXiv 1903.07222, 2019** (https://arxiv.org/pdf/1903.07222).
   "When one uses a market order, one does not really pay the spread, one just does not earn it." Cancelling to control inventory
   destroys queue priority. Refresh quotes only on threshold breaches. Price-consistent backtests cut the profit that naive models
   claim by 50-63%.
12. **Moallemi & Yuan, "A model for queue position valuation in a limit order book", 2016** (https://moallemi.com/ciamac/papers/queue-value-2016.pdf).
   Order value = P(fill) x (spread to the efficient price - adverse selection), plus the option value of queue advancement. For
   large-tick assets the **queue value is of the same order as the half-spread**, so do not cancel and re-post without need.
13. **Dayri & Rosenbaum, "Large tick assets: implicit spread and optimal tick size", arXiv 1207.6325, 2015** (https://arxiv.org/pdf/1207.6325).
   When the spread is pinned at one tick α, the market maker's ex-post profit per trade is ≈ α/2 - ηα, where η is small for large ticks.
   In a large-tick market makers earn a positive rent, but it is bounded by half a tick: **0.25c for us**.
14. **Gao & Wang, "Optimal market making in the presence of latency", arXiv 1806.05849, 2020** (https://arxiv.org/pdf/1806.05849).
   With latency, optimal quotes sit further back, and outstanding orders become liabilities when the price jumps before you can
   cancel. Making markets is profitable only if the uninformed arrival rate exceeds half the jump rate. The value of an order is
   spread capture minus adverse-selection cost, times the fill probability. For us, the write budget is the latency.
15. **Glosten & Milgrom 1985** (https://www.edegan.com/pdfs/Glosten%20Milgrom%20(1985)%20-%20Bid%20Ask%20and%20Transaction%20Prices%20in%20a%20Specialist%20Market%20with%20Heterogeneously%20Informed%20Trades.pdf; tradermath summary).
   ask = E[V|buy], bid = E[V|sell]. For a binary V with prior p and informed share μ, my derivation gives
   **ask - p = 2μ·p(1-p)/(1+μ(2p-1))**, so the adverse-selection spread scales with p(1-p). Our measured μ ≈ 0 makes this term negligible.
16. **Kyle 1985 (Econometrica; slides https://www.kahnfrance.com/cmk/fin590/0326Kyle%2085%20Slides.pdf)** and **Easley, López de Prado &
   O'Hara, "Flow toxicity and liquidity in a high-frequency world", RFS 2012 (https://www.quantresearch.org/VPIN.pdf)**. Kyle's λ is the
   price impact per unit of net flow. VPIN is mean |V_buy - V_sell|/V over volume buckets: makers widen or withdraw when it is high, and
   withdrawal feeds back into thinner books. For us, one-sided flow is **retail**, not informed, so high imbalance means opportunity
   on our value side, not toxicity.
17. **Ho & Stoll, "Optimal dealer pricing under transactions and return uncertainty", JFE 1981** (EconPapers abstract
   https://econpapers.repec.org/RePEc:eee:jfinec:v:9:y:1981:i:1:p:47-73). The dealer sets a reservation price shifted by inventory x
   risk and a spread set by arrival elasticity. The spread does not depend on inventory; the **location** of the quotes does. It is
   the ancestor of A-S.
18. **Chakraborty & Kearns, "Market making and mean reversion", EC 2011** (https://www.cis.upenn.edu/~mkearns/papers/marketmaking.pdf).
   A ladder market maker earns **(K - z²)/2**, where K is the total absolute price movement and z the net drift. It is profitable on
   mean-reverting paths and zero or negative on trending ones. **The tilt is a trend** (s 1.7% -> 14% in 3 days).
19. **Othman, Pennock, Reeves & Sandholm, "A practical liquidity-sensitive automated market maker", EC 2010**
   (https://www.cs.cmu.edu/~sandholm/liquidity-sensitive%20market%20maker.EC10.pdf). In LS-LMSR, b = α·Σq, so liquidity grows with
   volume. Prices sum to between 1 and 1 + α·n·log n: **a built-in overround** that makes the maker profitable. Our race books show
   sums of 1.009 on average and crossed bids at 1.02-1.03 (reader 1), the same overround made by retail.
20. **Hanson LMSR (oddhead blog / Chen et al. https://lance.fortnow.com/papers/files/LMSR.pdf)**. Bounded loss b·ln n; price impact grows
   with q/b. Little operational use for us beyond intuition for the impact of our own size.
21. **Brahma, Chakraborty, Das, Lavoie & Magdon-Ismail, "A Bayesian market maker", EC 2012** (https://www.cs.rpi.edu/~magdon/ps/conference/predMarkets.pdf).
   Quotes come from a Bayesian belief and its uncertainty. A "consistency index" **doubles the variance** after a run of one-sided
   trades, to adapt to jumps. It beats LMSR on profit (+2,081 vs -2,457) but its loss is unbounded (worst 689k vs LMSR bound ~10k).
22. **Whelan, "Agreeing to disagree: the economics of betting exchanges", UCD WP2025_22** (https://www.karlwhelan.com/Papers/Betfair.pdf).
   214k Betfair soccer matches. Makers +0.6%, takers -2.5%; taker losses grow nonlinearly as win probability falls (bottom decile
   about -5% pre-play). **In-play near the end, longshots become hugely overpriced** (takers -70% at 90+ min) and makers lose too.
   Laying longshots early often goes unmatched, which is adverse selection on queue.
23. **Bürgi, Deng & Whelan, "Makers and takers: the economics of the Kalshi prediction market", 2025/26** (https://www.karlwhelan.com/Papers/Kalshi.pdf).
   Makers average -9.6%, takers -31.5%. Buyers of contracts under 10c lose more than 60%, makers included. **Makers who buy at 50c or
   more earn about +2.6%.** The bias is slowly shrinking. This is our value MM rule exactly: favourite bids, never longshot bids.
24. **"The anatomy of a decentralized prediction market: Polymarket order book", arXiv 2604.24366, 2026** (https://arxiv.org/html/2604.24366v1).
   Half-spreads are ~400 bps in the middle and **1,300-1,800 bps in the lowest-probability decile**. Depth sits on a geometric grid,
   not at the top. The adverse-selection component is about zero in active markets. Depth decays toward the close. Trade direction
   inferred from the feed is right only 59% of the time, so build flow signals from **our own fills**, not from inferred feed signs.
25. **"Opening wide, moving fast: Polymarket spread dynamics", IMDEA 2025** (https://dspace.networks.imdea.org/bitstream/handle/20.500.12761/2065/Polymarket_Initial_Liquidity-3.pdf).
   Spreads in politics open at 8-14c and compress 62-84% within an hour. Elections and politics have **low realized/effective ratios
   (0.25-0.35)**: most of the taker's cost returns as price impact. Makers in politics books face real information flow; in our
   tournament (measured drift ~0) they do not.
26. **Rothschild & Sethi, "Trading strategies and market microstructure: evidence from a prediction market", 2016** (Intrade 2012;
   https://www.csc2.ncsu.edu/faculty/mpsingh/local/Social/f25/wrap/readings/Rothschild+Sethi-prediction-markets-2016.pdf).
   87% of traders never reversed direction. One market maker earned **$11.9k on $737 of margin** by quoting linked contracts
   (margin linkage, i.e. sets). A whale's limit-order walls pinned Romney at ~30c for hours while Betfair moved. Arbitrageurs held
   positions for under 10 minutes.
27. **Ottaviani & Sørensen, FLB survey** (https://web.econ.ku.dk/sorensen/papers/FLBsurvey.pdf) and **Green, Lee & Rothschild, "The
   favorite-longshot midas"** (https://www.stat.berkeley.edu/~aldous/157/Papers/Green.pdf). Heterogeneous-belief and noise-money
   explanations predict a **larger** bias as more retail money enters. In racing, sophisticated money buys favourites in the final
   minutes and moderates but never removes the bias. Taken with Whelan's in-play result, the tilt can still grow into the close.
28. **Obizhaeva & Wang, "Optimal trading strategy and supply/demand dynamics", JFM 2013** (https://web.mit.edu/wangj/www/pap/ObizhaevaWang13.pdf).
   In a finite-depth book with resilience ρ, the optimal plan is one block of X/(ρT+2) at the start, a continuous trickle at rate
   ρX/(ρT+2), and a block of X/(ρT+2) at the end. **Resilience, not spread, sets the cost.** Savings vs constant-rate selling are 4-12%
   at intermediate ρ.
29. **Almgren & Chriss, "Optimal execution of portfolio transactions", 2000** (https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf).
   x(t) = X·sinh(κ(T-t))/sinh(κT), with κ² ≈ λσ²/η. A risk-neutral seller (λ = 0) sells **at a constant rate**; a risk-averse one
   front-loads. For our capital recycling (selling low-edge holdings at the outcome valuation, where mark risk does not matter) λ ≈ 0:
   no rush, spread it out.
30. **Lo, MacKinlay & Zhang, "Econometric models of limit-order executions", JFE 2002** (summary
   https://allthingsphi.com/blog/2016/11/06/econometric-models-of-limit-order-executions.html). Time-to-fill is very sensitive to the
   distance from the mid, depth and volatility, and barely to size. First-passage (Brownian) models **vastly underestimate** fill
   times. Estimate fill probability empirically with survival models.
31. **Practitioner and program material.** Polymarket "Automated market making" (https://news.polymarket.com/p/automated-market-making-on-polymarket):
   choose low-volatility markets ranked by risk-adjusted reward; most of the MM profit came from rewards. Polymarket rewards docs
   (https://docs.polymarket.com/market-makers/liquidity-rewards): score ((v-s)/v)² x size, two-sided required outside 10-90c,
   one-sided worth 1/3. Kalshi MM program (CFTC filing https://www.cftc.gov/filings/orgrules/rules02262412176.pdf): maximum-spread and
   minimum-size obligations, in exchange for fee cuts and 40x position limits. Kalshi rate limits (https://docs.kalshi.com/getting_started/rate_limits):
   a batch costs per order (10 tokens), a cancel costs 2. Turbine "How to market-make on Kalshi and Polymarket" (https://www.turbinefi.com/blog/how-to-market-make-prediction-markets-2026):
   "don't hold inventory into settlement"; pull quotes when flow turns one-directional. That is **real-money advice that is wrong for
   us** (see the top-5 contradiction). Medium post-mortem (wanguolin): you cannot know your queue position, so treat rewards as a bonus.
   Polymarket negRisk docs (https://docs.polymarket.com/developers/neg-risk/overview): NO on one outcome converts to YES on all the
   others, which ties the legs of a set together.

## 2. Ideas

### MM-1. Skew by portfolio risk, not by per-market share count   [source: Bergault-Evangelista-Guéant-Vieira 2021, https://arxiv.org/pdf/1810.04383; Feil & Nendel 2026, https://arxiv.org/pdf/2607.17991]
What the source says: With correlated assets the optimal skew of asset i is proportional to (Σq)_i, the portfolio's covariance-weighted
inventory. For a binary, the terminal risk per share² is p(1-p), so the same share inventory costs ~5x less at 95c (0.0475) than at
50c (0.25).
Idea for us: Today `compute_quote` skews by `skew_per_quote x eff_inv / order_size`, the race-netted share count, capped at 2c.
Replace this, behind a flag, with skew_i = k x bloc_delta_i x (portfolio bloc delta) + k2 x p(1-p) x inv_i / quote. Package 10 already
computes the national-factor bloc delta, sqrt(rho)·φ(Φ⁻¹(p)) per share. Effect: a Dem favourite bid backs off when the WHOLE Dem bloc
is long, even if this market is flat. A tail market with a big but low-variance position stops being pushed to unload.
Value: P(<= 85k) down a little, because correlated favourite accumulation is slowed where it matters, and E[final] +0.2-0.5k, because
tail positions are not skewed away. It changes only how capital is placed, not how much. | Cost: ~40 lines in `compute_quote` / `decide`
(pass `risk_skew`); new settings `skew_mode="risk"`, `skew_risk_k`.
Check: Over the snap03 fills, compute each fill's bloc delta before and after, and the share of value-side fills made while the bloc
delta was above 0.8x its cap. In strategy_sim, compare a quote mode against a risk mode on the same days (the executor runs it).
Fair play: ok.

### MM-2. Quote toward a target inventory, not toward flat (the "informed market maker")   [source: Fodra & Labadie 2012, https://ar5iv.labs.arxiv.org/html/1206.4810; Cartea & Wang 2020, ORA link above]
What the source says: When the maker has a drift or alpha view, the optimal reservation price is r = s - γσ²(q - q*), with a target q*
∝ drift/(γσ²). With strong alpha it posts only the side that builds q*, and takes once the signal passes a threshold.
Idea for us: Our "drift" is the value gap (raw Polymarket p minus the tournament price) held to the outcome. Set q*_i = the hourly
allocator's target holding for market i. Skew by (eff_inv - q*_i) instead of eff_inv, and turn `age_skew` off for a position that is
below its target and still +EV. The adding side then rests at the touch until q* is reached and backs off only beyond it. The reducing
side rests only above p + margin (value_mode). This unifies the allocator (which decides how much) and the quoter (which decides how)
and stops the quoter from fighting the allocator.
Value: E[final] +0.3-1.0k. More value fills land at the maker price (+2-2.6c/share measured) instead of being taken by the allocator at
the touch. With the same capital, P(>= 150k) is unchanged. | Cost: ~60 lines. The allocator publishes `target[eid]`; `compute_quote`
gets `target_inv`; `skew_age_enabled` is false for value holdings.
Check: In snap03, count the value-side quotes whose skew pushed them off the touch while the position was below the allocator's target.
Dry-run the allocator plus quoter on the 3 Oct state (DRYRUN style).
Fair play: ok.

### MM-3. Spend writes by expected value per write, not by quote size   [source: Moallemi & Yuan 2016, https://moallemi.com/ciamac/papers/queue-value-2016.pdf; Gao & Wang 2020, https://arxiv.org/pdf/1806.05849]
What the source says: An order's value = P(fill) x (edge to the efficient price - adverse selection) plus its queue-option value. With
latency (our write budget), stale orders become liabilities and new ones are worth less.
Idea for us: Today `change_key` orders by pull, then urgent, then headline, then reprice, then -size_plan. Replace the last key with
EVW = Σ over orders in the change of P(fill in the next 30 min | market, distance to touch) x edge_to_outcome x size. The edge is the
gap to raw Polymarket on value sides, or the allocator's edge per share. P(fill) comes from a per-market fill-rate table built from our
own fills (MM-14). Also skip re-quoting markets whose EVW is below a floor: in snap03 the top 40 markets carried 68% of the edge and the
top 80 carried 90%. Quote ~80 markets well rather than 237 thinly. `plan_sizes` already sizes by sqrt(activity); this does the same
for writes.
Value: Same writes, more edge. If the reserve is funded, about +10-25% of maker edge/day (~+0.1-0.3k/day at the 3 Oct rates), from
fewer deferred value changes. | Cost: ~50 lines (`change_key`, plus an EVW table refreshed hourly from fills.csv); a setting
`write_priority="evw"`.
Check: Offline: rank markets by realized edge on 1-2 Oct and test whether that ranking predicts the 3 Oct ranking (Spearman). If the
rank correlation is > 0.5 the prioritization works. Also count the deferrals in the journal ("deferred: write budget").
Fair play: ok.

### MM-4. Keep any resting value order while it still clears its hurdle (queue first)   [source: Dayri & Rosenbaum 2015, https://arxiv.org/pdf/1207.6325; Law & Viens 2019, https://arxiv.org/pdf/1903.07222]
What the source says: Large-tick books, where the spread is pinned at 1 tick (ours: 56-69% of the time), pay a bounded rent of half a
tick, and the queue position is worth as much as that rent. Re-quote only on threshold breaches.
Idea for us: For a value-side order (favourite bid, longshot ask), replace the "within reprice_tolerance_ticks of the target" test in
`side_needs_change` with "still inside the value limit": bid <= p/(1+hurdle), ask >= 1-(1-p)/(1+hurdle). Leave it until Polymarket
moves enough to break the limit or the order ages out (`ttl_expire_as_cancel`). The target can wander by 2-3 ticks without a write.
`no_chase` already covers rival moves; this covers fv-only moves on value sides.
Value: Frees maybe 20-30% of the writes for MM-3 and keeps queue priority, where the retail fills come from. Small but positive
E[final] (+0.1-0.2k). | Cost: ~15 lines; a setting `value_keep_inside_limit`.
Check: Replay the journal: count value-side reprices where the old price was still inside the hurdle limit (the "wasted" writes).
Fair play: ok.

### MM-5. In tails quote the value side only; in the middle quote two-way only where the tilt is not trending   [source: Chakraborty & Kearns 2011, https://www.cis.upenn.edu/~mkearns/papers/marketmaking.pdf]
What the source says: A two-sided ladder earns (K - z²)/2. Oscillation pays and net drift costs quadratically.
Idea for us: The tilt drifts the tournament price by s·(c - p) away from Polymarket. At p = 20c and c = 50c that is a 4.2c drift at
s = 14%; at p = 45-55c it is <1c. The middle two-way band should therefore be defined by |predicted tilt drift| < 2 x min_edge, i.e.
|p - c| < ~0.1 for 2-leg races (35-65c), not by fixed `value_mid_low/high` = 0.15/0.85. Between 15-35c and 65-85c, quote one-sided
(value side), as in the tails.
Value: Avoids the z² loss on the middle book's anti-value side. The 3 Oct carry check found the anti-value sides lost 1.9k when the tilt
moved. At the outcome valuation, E[final] +0.2-0.5k over a month of tilt drift. | Cost: config only (`value_mid_low` 0.35 /
`value_mid_high` 0.65) or ~10 lines to make the band tilt-aware.
Check: In fills.csv, split middle-band fills by |p - c| < 0.1 vs >= 0.1 and compute the edge to Polymarket on each side. If the
0.15-0.35 band's bids are negative-edge, the band is too wide.
Fair play: ok.

### MM-6. Spreads in logit (risk) space: tighter absolute edges in the tails on the value side   [source: arXiv 2510.15205 handbook; Feil & Nendel 2026; Glosten-Milgrom binary]
What the source says: The adverse-selection spread for a binary is 2μ·p(1-p)/(1+μ(2p-1)). The risk spread also scales with p(1-p), so
a fixed spread in cents is far wider, in risk terms, in the tails.
Idea for us: We measured μ ≈ 0 (post-fill drift ≤ 0.08c). Our tail min_edge is 1c plus `fl_bad_side_extra_edge` 1c. The PLANNED
stage-3 value hurdle (`value_quote_hurdle` 0.08; default 0 and not in the live override yet) is 8% per $ of collateral. At p = 95c that
puts a favourite bid at ≤ 87.96c, while the others' book mid is ~90.5c (reader 1, 3 Oct), so **under stage 3 our favourite bids would sit
2.5c behind the market and almost never fill**. Likewise a longshot ask at p = 5c must be ≥ 12.0c with the
book at ~8.8c. The hurdle should be capital-cost-based and compared with the alternative use of the cash (the allocator's marginal edge
per $), not a fixed 8%. The literature's maker edge on favourites is +1-3% per $ (Kalshi +2.6%).
Value: Large if the tilt persists. A 5% hurdle at p = 95 puts the bid at 90.5c (at the market), worth ~5c/share x retail favourite sales.
Allocator takes do the same at the touch, so the gain is the maker's 0.25-0.5c over the take plus volume. E[final] +0.5-1.5k if cash
exists. | Cost: config (`value_quote_hurdle` 0.08 -> tie it to `alloc` marginal edge, e.g. 0.04-0.05).
Check: In snap03 books, for every value-side market, compute the fraction of time the others' best bid (ask) was above our hurdle-limit
bid (below our ask). That is our "never fills" share.
Fair play: ok.

### MM-7. Use one-directional retail flow as the signal for size, not as toxicity   [source: Easley-López de Prado-O'Hara 2012 VPIN, https://www.quantresearch.org/VPIN.pdf; Whelan 2025 Betfair; Rothschild & Sethi 2016]
What the source says: VPIN-style imbalance flags informed flow, and makers withdraw. On exchanges with uninformed, unidirectional
bettors (87% of Intrade traders never reversed), makers profit precisely from that one-way flow.
Idea for us: Keep a rolling signed imbalance of OUR fills per market (the feed's inferred signs are unreliable, 59% per arXiv 2604.24366).
When fills arrive on the value side and Polymarket has not moved (drift ≈ 0), size that side up (`gap_size_factor` analogue, up to 2x)
and refresh it first. When fills arrive and Polymarket also moves the same way, that is real information: apply the Brahma-style
"double the variance": pull the side for N minutes.
Value: More fills where the edge is, and a guard against the rare informed move. E[final] +0.1-0.4k. | Cost: ~50 lines (an
imbalance tracker in Bot, size factor in `compute_quote`).
Check: In fills.csv, test whether value-side fills cluster (autocorrelation of fill side within a market over 10-30 min) and whether
fv_after moves after clusters. Today's data says it does not.
Fair play: ok. This responds to flow; it does not create it.

### MM-8. Book imbalance / micro-price from the realtime feed for the middle book only   [source: Cont-Kukanov-Stoikov 2014, https://arxiv.org/pdf/1011.6402; Stoikov micro-price 2018]
What the source says: OFI explains 65% of short-horizon price moves, and the impact coefficient is ∝ 1/depth, which is large in thin
books. In large-tick books the micro-price, built from the bid/ask queue imbalance, is a better short-term price than the mid.
Idea for us: For the two-way middle book only (where we round-trip at marks), shade the reservation price by
β·(Q_b - Q_a)/(Q_b + Q_a)·tick, taking Q from the realtime feed's top level. When the queue against our resting side is thin (about to
tick through), do not re-post that side this cycle. Tails do not need it: there we hold to the outcome, so a short-term move is noise.
Value: Small (+0.05-0.2k), and only once middle MM is funded. | Cost: ~30 lines; the feed's book is already cached.
Check: md.sqlite `books` (3 levels): regress the 1-5 min mid change on the top-level imbalance by band. If R² < 5%, drop it.
Fair play: ok.

### MM-9. Make vs take: a lower take edge on value sides, and taking instead of cancelling for inventory   [source: Guilbaud & Pham 2013, https://arxiv.org/pdf/1106.5040; Law & Viens 2019; Cartea & Wang 2020]
What the source says: Use market orders when the signal or inventory is past a threshold; with a 1-tick spread, improving by a tick
equals taking. "A market order doesn't pay the spread, it just doesn't earn it."
Idea for us: Takes earned +7.5c/share on 3 Oct, far above the maker value fills (+2-2.6c). With adverse selection ≈ 0, the threshold
only needs to cover the half-spread (0.25c) plus the capital hurdle. Two options: (i) lower `take_edge` from 5c toward 3c on value sides
only (favourite asks below p, longshot bids above p are value takes), keeping the 30 s confirmation; (ii) in the allocator, take at the
touch instead of resting when the value gap is ≥ 2x the maker hurdle.
Value: E[final] +0.3-0.8k if cash is available; a take uses one write for a certain fill, so it is efficient on writes too. | Cost:
config (`take_edge` per band) or ~10 lines for a band-specific take edge. The code comment says keep it ≥ 0.05; first check why
(stale Polymarket risk).
Check: Over snap03, list the moments when the others' best quote was 3-5c through Polymarket on a value side for ≥ 30 s, and check that
Polymarket had not moved in the next 10 min.
Fair play: ok. Taking a standing order is ordinary trading.

### MM-10. The inventory penalty does not decay toward the close: no pre-close flattening, ever   [source: Avellaneda-Stoikov 2008; Feil & Nendel 2026; Turbine 2026 (the opposite advice)]
What the source says: In A-S the inventory term γσ²(T-t) vanishes as t -> T, because the price stops moving. In Feil & Nendel the
binary's terminal settlement penalty γ_T·q²·p(1-p) persists to settlement. Practitioner guides say "never hold inventory into settlement".
Idea for us: For us T is the election result, not the 4 Nov close. The variance p(1-p) is fully alive at the close, and the mark is
irrelevant under outcome settlement. Keep `flatten_*` / `exit_hours_before_close` inert. Size for the outcome distribution (bloc
delta), and do not let any "near close" logic in `compute_quote` (reduce_only windows) re-arm. A pre-close flatten would sell 100k of
value at retail-tilted prices, the worst trade available.
Value: Protects 5-10k of E[final] from an accidental pre-close dump. | Cost: config audit plus a self-test assertion.
Check: grep settings for `flatten_`, `exit_hours_before_close`, `reduce_only` windows on deploy.
Fair play: ok.

### MM-11. Treat the NO+NO sets as creation units: quote legs off the set   [source: Rothschild & Sethi 2016 (the $737-margin market maker); BIS 2021 ETF arbitrage, https://www.bis.org/publ/qtrpdf/r_qt2103d.pdf; Polymarket negRisk docs]
What the source says: ETF market makers price the ETF off the basket and use creation/redemption units as their inventory. On Intrade
the best market maker quoted linked contracts on almost no margin, because the exchange margined linked positions together.
Idea for us: We hold 21.9k of riskless NO+NO sets. Selling NO on the FAVOURITE leg of a set is the trade retail longshot-buyers want:
NO_fav ≈ YES_longshot, and it is rich. It leaves us holding NO on the longshot, the value position. So rest asks on NO_fav out of the
set at the rich price (≥ 1 - p_fav + margin) rather than unwinding the set by pair. This is the ETF analogy: quote the leg off the
basket, keep the hedge you want. The catch, from Package 7's notes: breaking a set makes the lone NO need collateral. The net cash
effect = sale proceeds - collateral released or required under the exchange's set model must be ≥ 0, or else the order is cash-gated.
Value: Turns dead set capital into value inventory plus cash at retail prices. If a 2-3c premium is sold over p on even 5k of sets,
that is +0.1-0.15k of edge, plus the long-longshot-NO position gets +EV held to the outcome. | Cost: ~40 lines in the set-aware
covered-sale path (`no_set_aware_bids` logic inverted for this case), plus a collateral check.
Check: Query the exchange collateral rule on one small test order (or Package 7's start-up check). In snap03, check how often
NO_fav's bid was ≥ 1 - p_fav + 2c.
Fair play: ok. These are ordinary sales of owned contracts.

### MM-12. Reserve capacity for the final days: the bias can grow into the close   [source: Whelan 2025 Betfair in-play, https://www.karlwhelan.com/Papers/Betfair.pdf; Ottaviani-Sørensen survey; Comerton-Forde et al. 2010, JF 65:295]
What the source says: Betfair longshots become extremely overpriced late, under time pressure. Noise-money explanations predict a
bigger bias as retail enters. Market-maker liquidity dries up when makers' inventories are full and they have taken losses
(Comerton-Forde et al.). Green-Lee-Rothschild show the opposite force: smart money arriving late compresses the bias.
Idea for us: Every capital-constrained maker in the tournament is full, as we are (~0 cash), so liquidity provision in the last week
will be scarce and the tilt may be largest when we have no cash to sell into it. Keep a slice of the reserve, e.g. 5-10k from sets and
low-edge holdings, **uncommitted until the last 3-5 days**, for longshot asks and favourite bids at the then-larger gap. This is an
option on s widening further. If s instead compresses (smart money arrives), we lose only the carry on that slice.
Value: If s goes from 14% to 25% in the final days, the edge per $ roughly doubles on that slice: +0.5-1.0k E[final]; ~0 downside.
| Cost: config in the allocator (`alloc_mm_reserve` with a time schedule) or a manual step.
Check: Track s(t) daily (reader 1 / tilt_diag); this is untestable offline beyond that.
Fair play: ok.

### MM-13. Sell low-edge holdings at the book's refill rate (OW / AC for recycling capital)   [source: Obizhaeva & Wang 2013, https://web.mit.edu/wangj/www/pap/ObizhaevaWang13.pdf; Almgren & Chriss 2000]
What the source says: With a finite-depth book of resilience ρ, sell X/(ρT+2) at once and then trickle at the refill rate. A
risk-neutral seller (we are one, at the outcome valuation) spreads sales evenly.
Idea for us: The allocator's sells (IOC at the touch) should be capped per market per hour at the book's measured refill rate: the
volume that comes back to the best bid within 10-30 min after being hit. Rest the remainder as a reducing ask at p - margin
(value_mode). Do not sweep down the book: each extra level costs a tick (0.5c) on a thin 3-level book.
Value: Saves ~0.25-1c/share on recycled holdings. On 20k recycled that is +0.05-0.2k. Small, but it also stops the leaderboard-visible
mark from dropping on our own sales. | Cost: ~20 lines in the allocator (`alloc_max_sell_per_hour` from a resilience table).
Check: md.sqlite `books`: after a best-bid level is depleted, measure the median time and size for it to refill, by band.
Fair play: ok. This is execution of our own sales; it is not marking.

### MM-14. Empirical fill-probability table from our own fills (survival by distance to touch)   [source: Lo, MacKinlay & Zhang 2002; Moallemi deep-LOB fill probabilities 2021, https://moallemi.com/ciamac/papers/deep-lob-2021.pdf]
What the source says: Fill times depend strongly on distance to the touch and depth, and theoretical first-passage models
underestimate them badly. Fit survival models to your own orders.
Idea for us: Build P(fill within 30/60 min | band, side, distance to the others' best in ticks, our queue rank if joining) from
order_notes, fills and snapshots. Feed it to MM-3 (EVW) and MM-6 (where the hurdle limit can still fill). Replace A-S's A·e^(-kδ) with
this table if any closed form is used.
Value: An enabler for MM-3/6; no direct value. | Cost: an offline script plus a JSON table loaded at start.
Check: Hold out 3 Oct and test calibration of 1-2 Oct fits.
Fair play: ok.

### MM-15. Two-way middle quotes only at the touch, and only where the touch is on the right side of Polymarket   [source: Dayri & Rosenbaum 2015; Chakraborty & Kearns 2011; Polymarket MM post 2024]
What the source says: In a pinned-1-tick book the maker's rent is ≤ half a tick. Polymarket's own MMs made their money from rewards,
not spread. Without rewards, the two-way middle book's P&L is thin and comes from oscillation.
Idea for us: Since 3 Oct our middle quotes were "behind 2+" ticks 5-7% of the time and at the touch 3-4% (when we quote at all). A
quote 2 ticks back in a 1-tick book almost never fills. If middle MM is funded, join the touch (or improve by 1 tick when the spread is
≥ 2 ticks, per Guilbaud-Pham) whenever the touch is still ≥ min_edge from raw p; otherwise do not quote that side at all. Do not rest
2+ ticks back, which locks capital for nothing.
Value: The middle book's fill rate per $ of locked cash rises; with a 15k reserve this is the difference between ~0.6k/day and less.
| Cost: ~15 lines (a `behind_best` rule in `compute_quote`).
Check: snap03: the fill rate of middle quotes by ticks-behind-touch (snapshots joined to fills by eid and time).
Fair play: ok.

### MM-16. Per-market inventory cap at the GLFT bound: stop the adding side, don't skew further   [source: Guéant-Lehalle-Fernandez-Tapia 2013]
What the source says: At the inventory bound Q the optimal policy simply stops quoting the adding side; below it, skew is smooth and
nearly linear in q.
Idea for us: Our caps come from many layers (frag_limit, adding_limit_factor, Kelly, bloc delta, capital ceiling, gap_size_factor).
GLFT says to use one bound per market, from the outcome risk budget (bloc-delta share x p(1-p)), with a smooth skew below it (MM-1)
and a hard stop at it. Collapsing the stack of size factors into "skew + one bound" makes the quoting predictable and auditable.
Value: Mostly operational (fewer surprises such as the 3 Oct "4 of 6 reducing quotes below value"); small E effect. | Cost: a
refactor; flag-guarded; medium.
Check: Over one day of journal quote lines, list which limiter bound each quote; if more than 3 distinct limiters bind regularly, simplify.
Fair play: ok.

## 3. TOP 5 for our situation
1. **MM-6 (re-base the planned stage-3 value hurdle on capital cost, not a fixed 8%)**: at 8% per $ our favourite bids would sit ~2.5c
   behind the market and our longshot asks ~3c above it, so value MM may simply never fill. Config only, and the check is one query.
2. **MM-2 (target inventory = the allocator's target; no age skew on +EV holdings)**: the informed-market-maker result. It aligns the
   quoter with the hold-to-outcome thesis.
3. **MM-3 + MM-14 (expected value per write, from an empirical fill table)**: 28 writes/min should go to the ~80 markets that carry 90%
   of the edge.
4. **MM-11 (quote legs off the NO+NO sets)**: turns the 21.9k of dead set capital into rich-priced sales to longshot buyers while
   keeping the value leg. Check the exchange's set collateral first.
5. **MM-12 (hold a slice of reserve for the last days)**: an option on the bias growing into the close (Betfair in-play, noise-money
   FLB theories, capital-constrained makers).

**The one thing in the literature that contradicts how the bot quotes today:** every inventory model with a view (Fodra-Labadie,
Cartea-Wang) or with correlated assets (Guéant multi-asset) skews toward a **target portfolio risk**. Our `compute_quote` skews each
market toward **zero shares** (`skew_per_quote x eff_inv / order_size`, plus an `age_skew` that sells old positions more cheaply), with
no weight for p(1-p) or the national bloc. A +EV favourite we want to hold to the outcome is therefore quoted, by the skew, as a
position to get rid of. Only the value_mode floor (never below p - 0.5c) stops that from becoming a loss; it still spends edge and writes
on unwinding what the allocator just bought.

## 4. Sources I could not access (or only by abstract)
- Comerton-Forde, Hendershott, Jones, Moulton & Seasholes, JF 2010: the full text (permission timeout). Cited from its known abstract.
- Cartea, Jaimungal & Penalva, "Algorithmic and High-Frequency Trading" (CUP 2015), and Guéant's "The Financial Mathematics of Market
  Liquidity" (2016): no public chapters found. Their content is represented here via the papers above.
- Glosten-Milgrom (tradermath): formula behind a paywall; the binary-case formula above is my own derivation from the model.
- Hanson / oddhead blog "Implementing Hanson's market maker": redirect loop.
- jdsemrau "Automated market making on Kalshi" (Substack): paywalled after the intro.
- SIG Predictions Cup docs (sig.thesuper.market/docs, markets-and-trading): navigation only, no mechanics. Note: predictionscup.com/rules
  says "all trades must be received prior to 12:00pm ET on November 4, 2026", while the brief says the markets close 4 Nov 00:00 UTC.
  Worth confirming which one governs.
- Lo-MacKinlay-Zhang JFE 2002 PDF: redirect; used a detailed summary instead.
- Kalshi MM agreement numeric obligations (sealed; only the program frame is public).
