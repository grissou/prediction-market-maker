# P11 literature sweep, reader 3: PORTFOLIO THEORY for correlated binary outcomes and rank / contest objectives (theme tag PORT)
Reader 3, 4 Oct 2026, 11:55-13:40 UTC. About 50 web searches and fetches; 31 sources read (abstract level or deeper); 8 could not be
read (listed at the end). Checks I ran myself: read-only Monte Carlo on H_outcome's worlds. I used the same loader, `H_pos_a.npy` (plan
(a)), rho 0.45 (control 0.85), k 1.0, 30-40k draws, and H_board's field for the ranks. The scripts sit in my scratchpad
(`.../scratchpad/{seat2,hedge,robust,scan,combo,rank,lagr}.py`). I did not run live_sim or any test suite, and I changed no repo file except
this one.

## 0. Four results from our data that change how the literature applies (read these first)
| Check (script) | Result |
|---|---|
| **A. The Senate "seat-count edge" mostly comes from one assumed number** (`seat2.py`; public odds from DeFi Rate 3 Oct, Covers 1 Oct, Kalshi/Polymarket Ohio pages) | I_senate set Ohio, which has no listed contract, at pDem **0.40**. Today's markets have Brown at **0.57-0.61**. With Ohio at 0.60 and our own p0 for the other 33 races, the seat count gives P(Dem control) **0.58-0.60**, or **0.59-0.63** if Osborn (p 0.267, correlated with the factor) caucuses with the Dems, across rho 0.25-0.65. Five venues have the control market at 0.626-0.657 (electionbettingodds, 4 Oct, $18.7M traded). So the seat count puts P(Rep) near **0.37-0.42**, not 0.45-0.51. Read the "PM" rows of I_senate, not the "seat" rows. The Rep U.S. Senate YES at 0.35 is roughly fair: +1.4% edge at Polymarket odds, at most +10-20% on the corrected seat count. The cheaper way into the same digital is **shorting Dem U.S. Senate at the 0.67 bid (+6.5% edge, simulated)**. |
| **B. The core value book is short a straddle on the national swing** (`hedge.py`) | Plan (a)'s E[final \| factor F] is **104.4k at a Dem wave (F = -1.65)**, 107.6k at -1.28, 115.5k at the centre, 116.8k at +1.27 and **111.6k at a Rep wave (+1.63)**. Every value position is "short the longshot", so a wave in either direction hurts. The sleeve's job is to pay in one tail. The Senate digital (rho 0.85) pays exactly in the Rep tail, where the core is already falling. |
| **C. A single-race digital beats the Senate-control digital for P(>= 150k)** (`scan.py`, `robust.py`, `rank.py`) | On the plan (a) core, a **$20k Texas Senate Rep sleeve (short Dem TX at the 0.67/0.665 bids, or Rep TX YES at 0.33-0.34)** gives E **112.0-112.2k**, P(>= 150k) **30%**, P(<= 85k) **6.3-6.6%**, P(top 50) **28%**, P(top 100) 35%. The **$20k Rep U.S. Senate sleeve** gives E 110.6k, P(>= 150k) **15.5-15.8%**, P(<= 85k) 5.0-5.3%, P(top 50) 23%, P(top 100) 33%. TX stays ahead across rho 0.25-0.65 and pTX 0.33-0.369 (P(>= 150k) 0.26-0.30 vs 0.13-0.18). At $15k, TX gives P(>= 150k) 20% at P(<= 85k) 5.0%. **Splitting the sleeve over 2-3 digitals is worse** (best pair 17%, best triple 11%: `combo.py`). Concentration wins, as in Browne and Haugh-Singal. |
| **D. Cheap Dem-wave "hedges" make P(<= 85k) worse, not better** (`hedge.py`) | Adding $5k of near-fair Dem-wave longshots to the $20k Senate sleeve (Osborn YES, Dem CO-04, Dem TX-23, Dem KS Gov) cuts P(<= 70k) from 2.4% to 0.4%. It also raises **P(<= 85k) from 5.3% to 14.3%** and cuts P(>= 150k) from 15.8% to 13.1%. The hedges lose in the common, mildly Dem states where the sleeve has already lost. **The floor is defended by sizing, not by tail hedges.** |

Caveats on C: depth was walked from the 22:47 book, and plan (a) already uses the 0.67 Dem-TX bid (14.2k shares). The pTX 0.33/0.35
rows stand in for that worse fill. Texas is one candidate race: Kalshi moved Paxton 51% -> 35% in a month. Public odds (Rep 0.35-0.36,
3 Oct) are close to our p0 of 0.369.

## 1. Sources (what each says that matters to us)
1. **Browne, "Reaching goals by a deadline: digital options and continuous-time active portfolio management", Adv. Appl. Prob. 1999**
   (https://business.columbia.edu/faculty/research/reaching-goals-deadline-digital-options-and-continuous-time-active-portfolio; SSRN 703).
   - The policy that maximises P(wealth >= goal at T) is equivalent to **buying a European digital option**: on the stock with one asset,
     and on the log-optimal (growth) portfolio with many assets.
   - The maximal success probability is Phi(Phi^-1(x/b) + theta sqrt(T)), where theta is the growth portfolio's Sharpe ratio.
   - For us: the probability-maximising book is "floor + one digital", and we can buy a literal digital (a race or control contract).
2. **Browne, "Beating a moving target: optimal portfolio strategies for outperforming a stochastic benchmark", Finance & Stochastics
   1999** (https://ideas.repec.org/a/spr/finsto/v3y1999i3p275-294.html).
   - Covers the same problem against a stochastic benchmark: maximise P(beat the benchmark by a margin) while keeping a floor, or minimise
     the expected time to outperform.
   - The leaderboard cut (top-50 ~133k, top-10 ~197k) is that benchmark, and it moves with the same election outcome.
3. **Browne, "Survival and growth with a liability", Math. OR 1997** (https://pubsonline.informs.org/doi/10.1287/moor.22.2.468).
   - In this formulation, maximising P(survival) and maximising P(growth to a goal) give different policies.
   - Once wealth sits above the "safe" level, the investor can switch to growth.
   - For us: guarantee the 85k floor with the riskless part (NO+NO sets, near-certain favourites), then spend only the cushion on the bet.
4. **Bernard, De Vecchi, Vanduffel (?), "Hedging goals", arXiv 2105.07915 / FMPM 2023** (https://arxiv.org/html/2105.07915).
   - Restates Browne: the optimal goal-reaching wealth is the replication of a digital call on the growth portfolio,
     V_t = H Phi(...).
   - "A goal missed by a hair's breadth is still a goal missed", so a pure probability objective breeds gambling when far below the goal.
   - Their fix is to minimise expected shortfall (lower partial moments) instead of P(miss).
5. **Föllmer & Leukert, "Quantile hedging", Finance & Stochastics 1999** (https://link.springer.com/article/10.1007/s007800050062;
   only the abstract was readable).
   - With insufficient capital, the maximum-probability hedge pays the claim on a set chosen by the likelihood ratio dP/dQ.
   - That is the Neyman-Pearson lemma: pay in the states where real probability per $ of price is highest.
   - For us: dP/dQ is the Polymarket probability over the tournament price, contract by contract. This gives the "P/Q" screen in PORT-2.
6. **Whitrow, "Algorithms for optimal allocation of bets on many simultaneous events", JRSS C 2007**
   (https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9876.2007.00594.x; summary via academia.edu).
   - With up to 12 bets, the optimal simultaneous Kelly stakes are ~0.98 x the separate Kelly stakes.
   - With 37 events, stakes track the **edge p - q** (R² 0.99), not separate Kelly.
   - The total stake was ~38-40% of the bank.
   - For us: with 117 races, rank by edge rather than by single-bet Kelly. Correlation (multiples) would shift the stakes.
7. **Vegapit, "Numerically solve Kelly criterion for multiple simultaneous bets"**
   (https://vegapit.com/article/numerically_solve_kelly_criterion_multiple_simultaneous_bets/).
   - Simultaneous Kelly stakes are smaller than the sequential Kelly stakes, and are found by gradient ascent on E log W over the joint
     outcomes.
   - Kelly output should be used "as references only".
8. **MacLean, Thorp, Ziemba, "Good and bad properties of the Kelly criterion", 2010**
   (https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf).
   - Good: Kelly minimises the expected time to a distant goal, asymptotically.
   - Bad: large short-term risk.
   - Fractional Kelly equals negative power utility (half Kelly is delta = -1).
   - For us: "minimum expected time to a distant goal" does not help a one-shot deadline goal. Kelly is the wrong objective for 150k.
9. **Whelan, "On optimal betting strategies with multiple mutually exclusive outcomes", Bull. Econ. Res. 2025**
   (https://www.karlwhelan.com/Papers/BER.pdf).
   - For mutually exclusive outcomes (our race legs), the KKT solution sometimes **bets on negative-EV legs as hedges**: 17-18% of
     recommended bets in his simulations.
   - The "Kelly / risk aversion = fractional Kelly" shortcut is shown to be a poor approximation.
10. **Busseti, Ryu, Boyd, "Risk-constrained Kelly gambling", J. Investing 2016** (https://arxiv.org/pdf/1603.06183).
    - Bounds P(W_min < alpha) < beta with the convex constraint log E[(r'b)^(-lambda)] <= 0, where lambda = log beta / log alpha.
    - Works for simultaneous bets and horse races. At equal drawdown risk it beats fractional Kelly (growth 0.047 vs 0.035).
    - It is a direct template for "max E subject to P(<= 85k) <= 10%" as a convex program over our scenario matrix.
11. **Grossman & Zhou, "Optimal investment strategies for controlling drawdowns", Math. Finance 1993**
    (https://econpapers.repec.org/article/blamathfi/v_3a3_3ay_3a1993_3ai_3a3_3ap_3a241-276.htm).
    - Under a floor of alpha x the running maximum, risky exposure is proportional to the surplus W - alpha M.
    - For us the floor is fixed at 85k and there is no rebalancing across the single settlement instant, so only the static version applies:
      the money at risk on election night is at most W - 85k.
12. **CPPI (Black & Perold 1992; Wikipedia summary)** (https://en.wikipedia.org/wiki/Constant_proportion_portfolio_insurance).
    - Exposure = m x cushion with multiplier m 4-5. The weakness is **gap risk**: a jump through the floor before rebalancing.
    - Our book is all gap: every contract resolves at once after the markets close, so CPPI's multiplier must be **1** on settlement risk.
    - That makes it option-based portfolio insurance (floor + option), not CPPI.
13. **Vasicek, "The distribution of loan portfolio value", Risk 2002** (https://www.bankofgreece.gr/MediaAttachments/Vasicek.pdf).
    - One-factor Gaussian copula: P(L <= x) = Phi((sqrt(1-rho) Phi^-1(x) - Phi^-1(p)) / sqrt(rho)).
    - The conditional default probability is p(Y) = Phi((Phi^-1(p) - sqrt(rho) Y) / sqrt(1-rho)).
    - At rho 0.4 the 99.9% tail is 11 sd against 3.1 under a normal.
    - For us: a closed-form factor stress for the risk cap, and why P(<= 85k) is set by the factor rather than by the number of races.
14. **Stronger, "Gaussian copula and the 2020 presidential election prediction markets", 2020**
    (https://danstronger.wordpress.com/2020/07/04/gaussian-copula-and-the-2020-presidential-election-prediction-markets/).
    - Fitted one rho so that the state markets reproduce the national market. **No rho worked**: the states implied >= 68% against the
      national 64%, even at rho 0.99.
    - Explanations considered: resolution "shenanigans" and segmentation between markets.
    - For us: an implied-correlation gap between race and control markets can be real (rules, independents) and is not free money.
15. **DeFi Rate, "Individual prediction markets contracts show Democrats with path to Senate control", 10 Aug 2026**
    (https://defirate.com/news/prediction-markets-democrats-path-senate-control/).
    - In August the race markets summed to a narrow Dem majority while the control market had Rep at 55%. The gap was attributed to
      "cluster risk" (correlation).
    - The Dem path: hold GA and MI, flip NC and ME, then win 2 of OH, TX, AK.
16. **DeFi Rate midterm odds page, 3 Oct 2026** (https://defirate.com/prediction-markets/2026-midterms/).
    - Senate control D 64 / R 36; House control D 93.
    - Races: TX D 64, OH D 60, AK D 71, IA D 45, ME D 60, MI D 72, NH D 87, KS D 36, MN D 88, SC D 13, MS D 10.
    - Covers (1 Oct, https://www.covers.com/betting/prediction-sites/guides/midterm-election-odds-tracker) has control D 63, GA D 96-97,
      NC D 94-96.
    - electionbettingodds (4 Oct, https://electionbettingodds.com/Senate-Control-2026.html): **D 64.1% (range 62.6-65.7%) across Kalshi,
      Polymarket, PredictIt and Betfair**.
17. **Kalshi / Polymarket Ohio pages** (https://news.kalshi.com/p/ohio-senate-odds-brown-climbs-nearly-10-points-to-61;
    https://polyinsider.io/en/markets/ohio-senate-election-winner).
    - Brown (D) 57-61% in the Ohio special. Our tournament has no Ohio contract, so I_senate had to assume a value, and it assumed 0.40.
18. **Kalshi / DeFi Rate Texas pages** (https://defirate.com/prediction-markets/2026-midterms/texas-senate-odds/;
    https://x.com/KalshiPolitics/status/2106427995565219980).
    - Talarico (D) 65%, Paxton (R) 35-36% on 3 Oct. Paxton was 51% on 3 Sep, so a one-month move of 16 points: this race is volatile.
19. **Silver Bulletin, "How FLIPR forecasts the midterm elections"** (https://www.natesilver.net/p/flipr-midterms-model-methodology).
    - Correlation comes in layers: national drift plus Election-Day error ("shift every race by 3 points"), correlated
      demographic/regional error, a new statewide layer (races in one state move together), and race noise. Tails are fat but "dialed
      down".
    - Newsweek (https://www.newsweek.com/democrats-chances-winning-senate-shift-nate-silver-forecast-12442493) reports FLIPR's P(Dem
      Senate) at 58.6% on 14 Sep, with Kalshi 53% and Polymarket 54% that day. The current page is paywalled.
20. **Silver, "What if the polls are wrong again?"** (https://www.natesilver.net/p/what-if-the-polls-are-wrong-again).
    - Midterm errors are "somewhat less uniform" than presidential ones.
    - The two Trump-era midterms (2018, 2022) were "almost completely unbiased in the aggregate". The Trump presidential years
      underestimated Republicans by ~3.8 points.
21. **Shirani-Mehr, Rothschild, Goel, Gelman, "Disentangling bias and variance in election polls", JASA 2018**
    (https://sites.stat.columbia.edu/gelman/research/published/polling-errors.pdf).
    - Poll RMSE is 3.7 points for Senate and 3.9 for governor, about twice what the reported margins imply.
    - Election-level bias is ~2 points.
    - **The bias correlates 0.45 between Senate and governor races in the same state, and 0.50 between president and Senate.**
    - For us: rho 0.45 is the right order. Our Senate and Governor contracts in the same state are more correlated than our model assumes
      (FLIPR's statewide layer).
22. **Wang (Princeton Election Consortium), "Midterm national Senate polling error is five times larger than in presidential years"**
    (https://election.princeton.edu/?p=1012).
    - The median absolute error of the national Senate poll average is 2.9 points in midterms against 0.6 in presidential years.
    - The bias can run "in the same direction across-the-board".
    - Against race-level noise of ~3-5 points, a national share of variance of ~0.3-0.5 fits rho 0.45.
23. **Brown, Harlow, Starks, "Of tournaments and temptations", JF 1996** (https://econpapers.repec.org/RePEc:bla:jfinan:v:51:y:1996:i:1:p:85-110).
    - Of 334 growth funds, 1976-91, mid-year losers raise volatility in the second half more than mid-year winners do.
    - The effect grew as performance became more visible.
24. **Chevalier & Ellison, "Risk taking by mutual funds as a response to incentives", JPE 1997** (https://www.nber.org/papers/w5234).
    - The flow-performance relation is convex, and funds change risk between September and December according to their year-to-date
      return.
    - Laggards gamble. Funds that are far ahead lock in by mimicking the index.
25. **Taylor, "Risk-taking behavior in mutual fund tournaments", JEBO 2003** (ResearchGate abstract blocked, 429).
    - Known result: in the two-fund game the *winner* can gamble too, because it anticipates the loser's gamble.
    - Risk choices are strategic, and correlation with the rival decides who gambles.
26. **Gaba, Tsetlin, Winkler, "Modifying variability and correlations in winner-take-all contests", Operations Research 2004**
    (https://pubsonline.informs.org/doi/10.1287/opre.1030.0098).
    - When fewer than 50% of entrants win, **increase variability and decrease correlation with the other contestants**. When more than
      50% win, do the opposite.
    - Choosing the riskier distribution is a dominant best response.
    - Top 50 of 1,040 is 5%: be variable and uncorrelated with the field.
27. **Tsetlin, Gaba, Winkler, "Strategic choice of variability in multiround contests and contests with handicaps", JRU 2004**
    (https://link.springer.com/article/10.1023/B:RISK.0000038941.44379.82).
    - "A contestant should maximize variability in a weak position (low mean, high handicap, or low previous performance)" and minimise
      it in a strong one. Intermediate levels are optimal only near the middle.
28. **Basak & Makarov, "Strategic asset allocation in money management", JF 2014**
    (https://www.london.edu/think/executive-summary-strategic-asset-allocation-in-money-management).
    - With relative-performance incentives the laggard gambles. The two managers "gamble strategically in the opposite direction from
      each other in each individual stock".
    - Behaviour changes abruptly near the threshold.
29. **Gürtler / Kräkel-style experiments, "Risk-taking tournaments: theory and experimental evidence", IZA DP 3400**
    (https://ideas.repec.org/p/iza/izadps/dp3400.html).
    - Risk taking depends on the correlation between the rivals' risky outcomes and on the size of the lead. Subjects behave mostly as
      predicted.
30. **Hvide, "Tournament rewards and risk taking", J. Labor Econ. 2002** (https://www.journals.uchicago.edu/doi/10.1086/342041).
    - In a Lazear-Rosen tournament with chosen variance, the equilibrium has "excessive risk taking".
    - For us: expect many rivals to gamble on election night. The 133k / 197k cut is set by gamblers whose bets came in, so the cut is
      itself correlated with the outcome.
31. **Haugh & Singal, "How to play fantasy sports strategically (and win)", Management Science 2021**
    (https://spiral.imperial.ac.uk/server/api/core/bitstreams/a1aef748-a483-485e-b81c-59f06cdfc50c/content).
    - In top-heavy contests, maximise P(score > the opponents' quantile) ~ Phi((mu - G)/sigma). Below the target that means high mean
      *and* high variance.
    - "Stacking": correlate your own picks so the payoff concentrates in one state.
    - Model the opponents explicitly: ~5x the expected P&L of the non-opponent model in top-heavy contests.
32. **Shleifer & Vishny, "The limits of arbitrage", JF 1997** (https://web.stanford.edu/~piazzesi/Reading/ShleiferVishny1997.pdf).
    - Performance-based arbitrageurs are "most constrained when they have the best opportunities". A deepening mispricing forces sales.
    - Mispricings with high idiosyncratic volatility and slow resolution survive.
    - For us there are no outside investors, but the kill switch on *marks* and the leaderboard play the role of PBA.
33. **De Long, Shleifer, Summers, Waldmann, "Noise trader risk in financial markets", JPE 1990**
    (https://ms.mcmaster.ca/~grasselli/DeLongShleiferSummersWaldmann90.pdf; read via the JEP companion).
    - Short-horizon arbitrageurs bear the risk that noise traders get *more* wrong before the horizon.
    - When payoff is at the outcome (our case), that risk shows up only in marks and rank, not in the payout.
34. **Rhode-Strumpf-style PredictIt arbitrage study, "Arbitrage in political prediction markets", J. Prediction Markets 2020**
    (https://www.ubplj.org/index.php/jpm/article/download/1796/1605/5823).
    - On PredictIt, linked-market sums stayed above $1 by more than 20c for **271-433 days** in 2016. Causes: the 10% profit fee, the
      $850 cap, and capital lock-up until settlement.
    - There were zero such days on IEM, where bundles recycle capital.
    - For us: lock-up plus a capped arbitrageur base lets a tilt persist and widen to the close. Size for holding to the outcome, not for
      convergence.
35. **Rothschild & Sethi, "Trading strategies and market microstructure: evidence from a prediction market", JPM 2016**
    (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2322420).
    - On Intrade 2012 they find evidence suggestive of manipulation by a single large trader. "Most traders who make directional bets do
      so consistently in a single direction."
    - For us: the tilt-buyers are not going to reverse, so do not expect convergence before the close.
36. **Roy, "Safety first and the holding of assets", Econometrica 1952** (https://en.wikipedia.org/wiki/Roy%27s_safety-first_criterion).
    - Minimise P(R < R_L) by maximising (E - R_L)/sigma. Geometrically: the tangent from (0, R_L) to the frontier.
    - Holds only under normality. Our payoffs are binary and bimodal, so use the exact scenario count.
37. **Continuous (fractional) knapsack: Dantzig 1957** (standard result; the Wikipedia page was blocked by a permission prompt).
    - With a linear objective and one budget constraint, greedy by value per unit of weight is optimal.
    - Greedy stops being optimal once the objective is non-linear (log, a probability, a variance penalty) or a second constraint binds
      (factor exposure, worst case).

## 2. Ideas

### PORT-1. Re-base the Senate decision on the market's control price: the "seat-count edge" was mostly Ohio   [source: DeFi Rate 2026 (#15, #16), Kalshi Ohio (#17), electionbettingodds 4 Oct (#16); check A]
What the source says: the race markets and the control market disagreed in August (#15). On 3-4 Oct the public race odds sum to P(Dem)
~0.57-0.63 against a control price of 0.63-0.66 on five venues. Ohio (unlisted for us) trades at D 0.57-0.61, not the 0.40 I_senate
assumed.
Idea for us: treat the Rep U.S. Senate sleeve as a **near-fair digital** (edge +1-2% at 0.35; +6.5% if entered as a **Dem U.S. Senate
short at the 0.67 bid**), not a +25% mispricing. Drop the "seat" rows of the I_senate/VALUE_PLAN tables. The case for the sleeve then
rests on variance for rank, not on EV. If a control-market sleeve is used at all, fill it Dem-short first. This is already I_senate's
order: 3.4k shares at 0.33.
Value: no change in E under Polymarket odds. It removes a phantom +5.5k (E 116.1k "seat" vs 110.6k "PM"), and the honest P(>= 150k) for
the $20k Senate sleeve is **~16%**, not 19-24%. | Cost: none (analysis). Change the seat model's Ohio input to 0.60 (I_senate.seat_p).
Check: `I_senate.seat_p(rho, ohio=0.60, alaska=None, osborn=0/1)`. Mine gives 0.58-0.63. SIG's rule text for "control" (independents,
VP tiebreak) still decides the residual 0.02-0.05.
Fair play: ok.

### PORT-2. Browne/Neyman-Pearson: the laggard's optimal book is "floor + ONE digital", picked by P/Q x multiplier x where the core is strong   [source: Browne 1999 (#1), Föllmer-Leukert 1999 (#5), Hedging goals (#4)]
What the source says: maximising P(W_T >= goal) with too little capital means holding the claim only on the states with the highest
dP/dQ. In a complete market that is a digital option. The success probability falls when the bet is spread over many states.
Idea for us: the payoff we need is about **85k+ in the losing states and >= 150k in the winning states**, with nothing wasted in between.
Implement it as: the core value book (the floor, near-riskless once diversified), plus one digital of $X <= W(Dem/lose states, q10) - 85k
≈ $20k. Choose the digital by three tests:
(i) P/Q >= 1, i.e. Polymarket p / tournament price;
(ii) the multiplier (1/q - 1) x X reaches the 37-41k gap, which needs q <= ~0.34 at $20k;
(iii) its winning states are ones where the core is already above its mean ("stacking").
The scan (`scan.py`) ranks **Texas Rep** first (p 0.369, q 0.33-0.34, P/Q 1.09-1.12, and plan (a) is already short 14.2k Dem TX, so the
core is +8k richer when TX goes Rep). Next come Rep Michigan (Dem MI NO at 0.26) and Rep FL-09 / CO-04 NO. Rep U.S. Senate is far down:
its wins sit in the Rep-wave tail, where the core has fallen to ~111k.
Value: **P(>= 150k) 0% -> 30% (TX $20k) vs 16% (Senate $20k)**, E 112.0-112.2k vs 110.6k, P(<= 85k) 6.3-6.6% vs 5.0-5.3%.
P(top 50) 28% vs 23% on H_board's field. | Cost: one manual entry (~60k shares; depth 0.67 14k / 0.665 11.6k Dem TX bids, 0.34 14.7k Rep
TX asks), then `alloc_pin "Dem Texas Senate,Rep Texas Senate"`.
Check: `robust.py` (rho 0.25-0.65, pTX 0.33-0.369: TX P(>= 150k) 0.26-0.30 vs Senate 0.13-0.18). Re-walk the LIVE TX books net of plan
(a)'s own 14.2k short before sizing. If the average fill is worse than 0.35 a share, TX loses its edge (P/Q < 1.05) and its P(>= 150k)
falls toward 0.26.
Fair play: ok. It is an ordinary directional position held to the outcome.

### PORT-3. Size the digital to the floor, not to Kelly: X* = q10(W | sleeve loses) - 85k   [source: Grossman-Zhou 1993 (#11), CPPI/OBPI (#12), Browne "survival and growth" (#3)]
What the source says: risky exposure scales with the cushion above the floor. With jumps (gap risk) the multiplier must be ~1. Survival
first, growth with what is left.
Idea for us: everything resolves in one jump after the 4 Nov close, so the sleeve's whole cost is the gap. The rule: **X = (10th
percentile of the core in the sleeve-losing states) - 85k - margin**. Today that is ~106k - 85k ≈ 20-21k.
The cliff is steep: the core's conditional sd is only ~7.5k.
- TX: P(<= 85k) is 5.0% at $15k, 6.6% at $20k and 24% at $22k.
- Senate: 5.3% at $20k and 21% at $22k.
So **$20k is the ceiling for any single digital**, $15-18k if the owner wants margin. Every 1k of extra core EV (MM carry) raises the
ceiling by ~1k.
Value: keeps P(<= 85k) under the owner's 10%. A $25k sleeve takes it to 48-60% (`lp_sleeve.py`). | Cost: a one-line formula in the
sizing note. The allocator could compute it hourly from H_outcome (~40 lines).
Check: `lp_sleeve.py` / `hedge.py` rows at 15/18/20/22/25k.
Fair play: ok.

### PORT-4. Contest theory says: be variable and *decorrelated from the field*. The tournament's own prices show the field is long Dem in exactly these two markets   [source: Gaba-Tsetlin-Winkler 2004 (#26), Tsetlin et al. 2004 (#27), Haugh-Singal 2021 (#31), Basak-Makarov 2014 (#28)]
What the source says: with few winners (top 50 of 1,040 = 5%), increase variance and decrease correlation with rivals; a weak position
calls for maximum variability. In DFS, explicit opponent modelling is worth ~5x P&L in top-heavy contests. Competing managers gamble in
opposite directions.
Idea for us: our own data shows where the field leans. The tournament mid is **above** Polymarket for the favourite in **Dem U.S. Senate
(0.6725 vs 0.645)** and **Dem Texas Senate (0.6725 vs 0.631)**, against the general tilt that makes favourites cheap. So tournament money
is long Dem in these two markets, and a Rep TX / Rep Senate sleeve bets against the crowd.
In the states where it wins, the Dem-leaning rivals near the 133k cut lose, so the cut falls. H_board's directional field is 50/50 by
party, so it **understates** the rank value of a Rep-side sleeve.
Value: P(top 50) above the simulated 23-28%. The direction is certain; the size is unknown without a field model. | Cost: H_board
change: tilt the directional players' party choice to match the tournament-vs-Polymarket gaps (~20 lines).
Check: in snap03, compute (mid - p0) for every control market and competitive race. Re-run `rank.py` with a 60/40 Dem directional field.
Fair play: ok. We read public prices; we do not trade to move them.

### PORT-5. Don't buy "cheap" wave hedges for the floor; they move P(<= 85k) the wrong way   [source: CPPI gap risk (#12), Vasicek (#13); check D]
What the source says: under a one-factor model the loss tail is set by the factor. Portfolio insurance pays a premium for the deep tail.
Idea for us: the near-fair Dem-wave longshots (Osborn YES at 0.25, Dem CO-04 0.29, Dem TX-23 0.365, Dem KS Gov 0.27; P/Q 1.0-1.07) do
cut P(<= 70k), from 2.4% to 0.4%. They lose in the frequent mild-Dem states where the sleeve has already lost, so P(<= 85k) rises
**5.3% -> 14.3%**. Our 85k floor is a 10%-quantile constraint, not a deep-tail one, so **sizing (PORT-3) is the hedge**. Spend nothing on
wave insurance unless the owner adds a P(<= 70k) target.
Value: avoids a 2-9 point rise in P(<= 85k) and a 2.7-point loss of P(>= 150k). | Cost: none (a decision).
Check: `hedge.py` "RepSen + hedges(5k)" rows.
Fair play: ok.

### PORT-6. Replace "edge per $" with a Lagrangian allocator score: edge + lambda150 x E[pnl | W≈150k] - lambda85 x E[pnl | W≈85k]   [source: Dantzig knapsack (#37), Busseti-Ryu-Boyd RCK (#10), Browne (#1), Whitrow (#6)]
What the source says: greedy by value per $ is optimal only for a linear objective under one budget. With a probability objective or a
drawdown constraint, the optimum is a convex program (RCK) whose KKT conditions price each bet by its payoff in the binding states.
Idea for us: once the sleeve is on, the states near 150k are the TX-Rep states and the states near 85k are TX-Dem plus a Dem wave.
`lagr.py` gives E[pnl per $ | state] for every contract. Three groups matter:
- **Neutral favourites** (Rep NH Gov 0.84, Dem CT Gov 0.855, RI Gov/Senate NOs; edge 13-14%) pay ~equally near 150k and near 85k. They
  are the ideal floor, so keep the allocator buying them.
- **Some "high-edge" holdings hurt the target**: Rep CO-04 NO (edge 4.4%, -48% per $ near 150k), Rep FL-09 NO, Rep TX-23 NO, Dem/Rep
  Nevada Gov, Dem Maine Senate. These are Dem-wave payers.
- **Rep U.S. House NO** at 0.835 (edge 12%) and Rep WA-03 NO pay +20% near 85k and only +4-6% near 150k. They are good floor
  protectors.
Rank pairs by `score = edge_per_$ + l1 x at150 - l2 x at85`, with l1, l2 refreshed hourly from the H_outcome scenario matrix (20k worlds
x 237 contracts; one settle per hour). That is RCK in miniature.
Value: re-sorting ~$10-20k of the allocator's turnover toward target-neutral or target-positive contracts. Rough guess: +2-5 points of
P(>= 150k) at the same E and P(<= 85k). | Cost: ~80-120 lines (an `alloc_score_mode` setting, scenario cache, two multipliers); needs
H_outcome's simulator in-process or a nightly file.
Check: offline, re-run I_alloc's action list with the Lagrangian score and compare P150/P85 against the edge-only list on the same
worlds.
Fair play: ok.

### PORT-7. The value core is a short straddle on the national swing. Measure it as one, and use a factor-stress cap, not a share or linear bloc delta   [source: Vasicek (#13), Shirani-Mehr et al. (#21), FLIPR (#19); check B]
What the source says: under one factor, conditional default (here, win) probabilities are p(Y) = Phi((Phi^-1(p) - sqrt(rho) Y) /
sqrt(1-rho)). Portfolio value given the factor is near-deterministic for a granular book, so the tail is the factor tail.
Idea for us: the linear `bloc_delta` (sqrt(rho) phi(Phi^-1 p) per share) sees only the slope. Our core is concave: 104k at a -1.65 sd
Dem wave, 115.5k at centre, 112k at a +1.63 Rep wave. Add `bloc_stress`: E[V | Y = ±1.28] and E[V | Y = ±1.65] in closed form, the sum
over contracts of shares x p_i(Y) plus cash, cheap enough to run every cycle. Cap on the stressed value against the floor (for example
E[V | Y = -1.65] >= 85k + 3 x idiosyncratic sd) instead of `max_bloc_delta_frac` 0.05.
This replaces two settings (the bloc cap and `max_worst_case_frac`) with the quantity the owner cares about.
Value: P(<= 85k) is controlled by construction. It stops the bloc cap fighting a sleeve the owner chose (PORT-8). | Cost: ~60 lines
(Phi/Phi^-1 exist in `bloc_slope`). Config: `bloc_stress_enabled`, `bloc_stress_z` 1.65, `bloc_stress_floor` 85000.
Check: compare the closed form with H_outcome's MC conditional means at F = ±1.28/±1.65 (mine: 107.6k / 104.4k / 116.8k / 111.6k).
They should agree within ~1k.
Fair play: ok.

### PORT-8. Core-satellite risk budgeting: carve the pinned sleeve out of max_worst_case_frac and the bloc cap, or the bot goes reduce-only   [source: OBPI / CPPI (#12), Browne (#3); code reading of mm_bot.py l.4384-4395, 5417]
What the source says: portfolio insurance separates the floor asset from the option. The option's risk is its premium, known in
advance.
Idea for us:
- A $20k single-race sleeve adds ~$20k to `total_worst_case` and ~3 x 29k to `settlement_risk`. Risk is min(worst, settlement_risk), so
  plan (a)'s ~32k becomes ~56k, above 0.35 x ~110k = 38.5k. **Global reduce-only** follows: market making stops, which is exactly what
  the owner's MM plan needs to avoid.
- The bloc delta also jumps. TX adds ~15k $/sd and the Senate sleeve ~20k $/sd, against a 5.5k cap. Above the cap, quotes that add Rep
  exposure are blocked, so the MM only accumulates Dem-leaning inventory. That **silently hedges the sleeve away**.
Fix: `sleeve_labels` = alloc_pin. The risk model and the bloc cap would count the sleeve at its cost (a fixed stress), the way
`basket_risk_split` already does for the basket. Or, as a stop-gap, set max_worst_case_frac ~0.52 and max_bloc_delta_frac ~0.20 while
the sleeve is on, which loosens everything else too.
Value: keeps the MM carry (the only no-ruin road to 150k in VALUE_PLAN) alive alongside the sleeve. Otherwise the sleeve costs the MM
carry plus the mark-based kill risk. | Cost: ~30 lines, a copy of `basket_risk_split` keyed on the pin list.
Check: dry-run `decide()` on the 22:47 state with a synthetic 60k-share Dem TX short. Confirm whether `global_reduce` trips with and
without the carve-out.
Fair play: ok.

### PORT-9. Kelly is the wrong objective for the deadline goal, but simultaneous Kelly still sizes the core: stake ∝ edge, fully invested   [source: Whitrow 2007 (#6), Vegapit (#7), MacLean-Thorp-Ziemba (#8), Whelan 2025 (#9)]
What the source says: with many simultaneous bets, the stakes track the edge and total stake can be large. Kelly minimises the expected
time to a *distant* goal, not P(goal by a deadline). Within mutually exclusive legs, small negative-EV hedges can be optimal.
Idea for us:
- For the core (floor), single-bet Kelly on a favourite at 0.84 with p 0.88 is 25% of wealth, and the joint Kelly with 117 nearly
  independent races is near "all-in". So deploying the full ~100k in favourites and longshot shorts is consistent with growth theory
  **because of diversification**, and the binding limit is the factor (PORT-7), not Kelly.
- Do not use Kelly to size the sleeve (Kelly says ~1-2% of wealth at +2-10% edge). The sleeve is a goal-reaching device (PORT-2/3).
- Whelan's result means the allocator's "never sell below p" guard is right for EV, but a tiny negative-EV leg can be justified by the
  floor. That is the Lagrangian of PORT-6, not a separate rule.
Value: mostly prevents a wrong turn (fractional Kelly on the core would cut E by 3-6k for nothing). | Cost: none.
Check: none needed. Optionally compute the joint-Kelly stake on plan (a) with H_outcome's scenario matrix (gradient ascent, ~30 lines)
to see whether any position exceeds it.
Fair play: ok.

### PORT-10. Tournament timing: buy the digital now or later? Martingale prices say no timing edge, but the leaderboard update argues for "late but before the close"   [source: Chevalier-Ellison 1997 (#24), Brown-Harlow-Starks 1996 (#23), Taylor 2003 (#25), Hvide 2002 (#30)]
What the source says: managers adjust risk late in the assessment period, once they know their standing. Rivals anticipate this, and
leaders may gamble too.
Idea for us: if prices are martingales, entering the sleeve on 4 Oct or 3 Nov has the same expected cost. Waiting buys information on
**our own standing** (the MM carry realised, the core's EV) and **the field's leaning** (PORT-4). A laggard should gamble with exactly
the amount it still needs. So:
1. Keep the sleeve decision open until ~25-28 Oct.
2. Size it then to (150k - current E[final]) / multiplier, capped by PORT-3.
3. Buy a $5-10k first tranche now *only* where the edge is real today (TX Rep at 0.33-0.34 with P/Q 1.1). Depth and edge can vanish:
   the Texas market moved 16 points in 30 days.
Value: P(>= 150k) roughly unchanged. P(<= 85k) improves by not over-betting if the MM delivers. Avoids buying a lottery we may not
need. | Cost: none; a calendar item.
Check: untestable offline. Watch Polymarket TX and control prices daily, and the tournament book depth at 0.33-0.35.
Fair play: ok.

### PORT-11. Persistent, widening tilt: size the value core for hold-to-outcome; never let mark-based rules sell it   [source: Shleifer-Vishny 1997 (#32), De Long et al. 1990 (#33), PredictIt arbitrage study (#34), Rothschild-Sethi (#35)]
What the source says:
- Mispricings persist when arbitrage capital is locked up and capped (PredictIt: 271-433 days).
- Noise traders can get more wrong before the horizon.
- Arbitrageurs judged on interim marks are forced out when the opportunity is best.
- Directional traders hold one direction.
Idea for us: the tilt can widen to the close (s 1.7% -> 14% in 3 days). Our payoff is at the outcome, so the only channels for
noise-trader risk are:
(a) the `max_drawdown_pct` kill switch on marks;
(b) the leaderboard's interim rank;
(c) any rule that sells on marks.
Make (a) settlement-based. VALUE_PLAN suggested 0.40 or settlement-based; this literature says settlement-based. Treat every further
widening as a chance to add at a better edge, which the allocator does if cash exists. Hold back a small dry-powder reserve for the last
week, when PredictIt-style late herding is strongest (anomalies reader: the bias jumps in the final 24 h).
Value: protects the +9-13k value gain from a forced sale. P(<= 85k) unchanged. Adds ~0.5-1k if 5k is reserved for late widening. |
Cost: the kill switch is code default, not overridable (VALUE_PLAN). ~10 lines to base it on liquidation EV at Polymarket.
Check: replay the 1-4 Oct marks through the kill rule. Mark drawdown peak vs the 0.30 threshold, with a 20k sleeve marked at -10%.
Fair play: ok.

### PORT-12. Correlation model refinements that matter for the sleeve choice: a statewide layer and a fat-tailed factor   [source: FLIPR methodology (#19), Shirani-Mehr et al. (#21), Wang PEC (#22), Silver (#20)]
What the source says: correlated errors come at the national, demographic/regional and state levels. Within-state Senate-Governor bias
correlation is 0.45, and president-Senate 0.50. Midterm national error has a median of ~2.9 points and can run one way across the board.
2018 and 2022 were nearly unbiased in aggregate. Tails are fat but "dialed down".
Idea for us: add (i) a **state factor** (Senate and Governor in the same state share it, rho_state ~0.2 on top of the national 0.45) and
(ii) a **t(5) national factor** to H_outcome. Effects:
- The TX sleeve gets correlated with any Texas House/Governor holdings. Check what plan (a) holds in TX-23 / TX-28 and the Texas
  Governor.
- Fat tails raise P(<= 85k) for the short-straddle core (PORT-7) and the Senate digital more than for TX.
Value: shifts P(<= 85k) by ~1-3 points and changes which digital wins only if the TX book is concentrated. | Cost: ~25 lines in
H_outcome.simulate. Analysis only, no bot change.
Check: re-run `robust.py` with a state factor and t(5). Accept the TX sleeve if its P(>= 150k) stays > Senate's + 5 points and P(<= 85k)
<= 10%.
Fair play: ok.

### PORT-13. The control market as a hedge instrument: how much factor one contract removes   [source: Vasicek (#13), bloc_slope in mm_bot.py, VALUE_PLAN]
What the source says: under one factor, a contract's $ sensitivity per sd of the factor is sqrt(rho) x phi(Phi^-1(p)) per share.
Idea for us:
- One Dem U.S. Senate share at p 0.645 with rho_control 0.85 carries **0.343 $/sd**. A Dem/Rep House share at p 0.935 carries only
  0.922 x phi(1.51) = **0.117 $/sd**. A TX share at 0.631 with rho 0.45 carries 0.253 $/sd.
- To neutralise the core's +2.55k/sd Rep lean (VALUE_PLAN), you need ~7.4k Dem Senate shares at 0.675 (~$5k, cost ~-$220 EV at the ask
  vs p 0.645). Or ~22k Dem House shares at 0.84, which has +EV of +9.5c per share against p 0.935 (Dem House is a cheap favourite).
- **The House control contract is the cheap factor hedge: positive EV and low factor beta per $.** The Senate contract is the expensive
  one.
- Use the Dem House favourite (already held, 8.9k) to absorb factor exposure when the MM needs room under the bloc cap. Do not use Dem
  Senate (rich).
- With the Rep sleeve on, do NOT hedge with Dem House beyond what the floor requires (PORT-3/5): it pays in every Dem state and eats the
  sleeve's leverage.
Value: frees bloc-cap room for MM at +EV. ~0.5-1k of extra MM capacity, rough. | Cost: none (allocator already prefers Rep U.S. House NO
/ Dem House YES at 12% edge).
Check: `bloc_sensitivities` on the 22:47 inventory, then marginal bloc change per $ for Dem House vs Dem Senate.
Fair play: ok.

### PORT-14. When greedy-by-edge allocation is optimal, and when correlation breaks it (the lock-up knapsack)   [source: Dantzig (#37), Whitrow (#6), RCK (#10)]
What the source says: a linear objective under one budget with divisible items makes greedy by ratio optimal. Any concave or chance
constraint breaks it, and the KKT price of the extra constraint enters every item's score.
Idea for us: the hourly allocator (edge per $, sell lowest edge-held first) is EV-optimal **only while** neither `max_worst_case_frac`
nor the bloc cap binds and the objective is E[final]. With the owner's lexicographic objective (P(>= 150k), then P(>= 120k), then E, then
P(<= 85k) < 10%), it is optimal for the core's internal mix and wrong at the margin between the core and the sleeve.
Practical rule: keep greedy for the core, run PORT-6's score only on the top ~30 pairs, and hard-pin the sleeve. Add a "shadow price"
log line: how much edge per $ the allocator gave up because of the bloc or worst-case cap. That number tells the owner what the risk
limit is costing.
Value: diagnostic. Turns an implicit trade-off into a number. | Cost: ~15 lines of logging in the allocator.
Check: from I_alloc's action list, count pairs skipped by the bloc check and their summed edge.
Fair play: ok.

## 3. TOP 5 for our situation
1. **PORT-2 (+ PORT-3): if a sleeve is wanted, make it the Texas Rep digital (short Dem TX at 0.67/0.665, then Rep TX YES at 0.34),
   $15-20k, not the Senate control contract.** On the same worlds: P(>= 150k) 30% vs 16%, E +1.5k higher, P(<= 85k) 6.3-6.6% vs 5.0-5.3%,
   P(top 50) 28% vs 23%. Robust to rho 0.25-0.65 and to pTX 0.33-0.369. It stacks on plan (a)'s existing 14.2k Dem TX short. Re-walk the
   live depth first.
2. **PORT-1: drop the "seat-count" story for the Senate.** With Ohio at its market price (D 0.57-0.61), the seat count lands at P(Dem)
   0.58-0.63 against 0.63-0.66 on five venues. Rep Senate at 0.35 is ~fair. If the owner still prefers the control contract, enter via the
   **Dem Senate short at 0.67 (+6.5%)**.
3. **PORT-8: carve the pinned sleeve out of `max_worst_case_frac` and the bloc cap** (or raise them to ~0.52 / ~0.20 while it is on).
   Otherwise a $20k sleeve trips global reduce-only, and the bloc cap pushes the MM to hedge the sleeve away.
4. **PORT-3 + PORT-5: the floor is defended by sizing, and the ceiling is ~$20k for any single digital.** The cliff runs 6.6% at $20k,
   ~24% at $22k and ~50-60% at $25k. Cheap wave hedges *raise* P(<= 85k) (5.3% -> 14.3%), so buy none.
5. **PORT-6 / PORT-7: an hourly Lagrangian allocator score and a closed-form factor stress cap.** They price each contract by its payoff
   in the states near 150k and near 85k, and replace the linear bloc delta with E[V | Y = ±1.65]. Neutral favourites stay the backbone;
   Dem-wave "value" (CO-04, FL-09, TX-23 Rep NOs) gets deprioritised once a Rep-side sleeve exists.

**The one result that most changes the plan:** the Rep U.S. Senate sleeve is not the best rank bet. Its seat-count edge mostly came from
assuming Ohio at 0.40, while the market has it at ~0.60. A same-size Texas Rep digital stacks on what plan (a) already holds and roughly
doubles P(>= 150k): 30% vs 16%, at +1.5k E and +1.3 points of P(<= 85k).

## 4. Sources I could not access
- Taylor 2003 JEBO (ResearchGate 429; result from memory of the literature, flagged as such in #25).
- Yahoo / Polymarket article on Silver vs markets (429).
- Silver Bulletin current forecast numbers (paywalled; 2 Oct page).
- Polymarket "which party will win the Senate" rules text (not in fetched HTML; gamma-api blocked by the proxy). **SIG's own rule for
  control still needs reading.**
- Föllmer-Leukert full text (paywall; abstract only).
- "How to gamble if you're in a hurry" (T&F 403).
- Rothschild-Sethi full text and the DeLong blog note (403).
- Wikipedia continuous knapsack (a permission prompt timed out; standard result cited from memory).
- FiveThirtyEight 2022 polling-accuracy article (redirects to ABC politics).
