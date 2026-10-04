# P11 literature sweep, reader 4: MIDTERM 2026 FORECASTING, CALIBRATION, POLLING ERROR, SETTLEMENT RULES (theme tag FC)
Reader 4, 4 Oct 2026, 12:15-13:40 UTC. About 70 web searches and fetches. I read 44 sources at least at summary level; 9 could not be read
(listed at the end). Checks I ran myself, read-only, from snap03 (22:47 UTC 3 Oct book and positions): `cmp.py` and `gov.py` in my scratchpad
(`/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-.../scratchpad/`). They price every held contract three ways: at the
tournament mid, at the Polymarket reference, and at the Decision Desk HQ (DDHQ) / The Hill model of 2 Oct. I did not run live_sim or any
test suite, and I changed no repo file except this one.

**What the other readers already cover, and I do not repeat:** the favourite-longshot bias papers (ANOM-9: Le 2026, Becker 2025,
Reichenbach-Walther), averaging Kalshi into the reference (ANOM-10), the partisan lean of 2022 markets (ANOM-11), and the Texas Rep digital
versus the Senate-control sleeve (PORT-1/2). This file adds four things. (1) The models' view on every race we hold. (2) Which source is
likelier to be right where models and markets disagree. (3) The rules text. (4) The settlement calendar.

## 0. Results from our data that change how the literature applies (read these first)

| Check | Result |
|---|---|
| **A. Where models and Polymarket disagree, the models are usually less confident** (`cmp.py`, `gov.py`, DDHQ / The Hill forecasts of 2 Oct) | Of the 97 races (one leg each) with both a Polymarket reference and a DDHQ number, **37 differ by 5 or more points**, and in 28 of those 37 Polymarket is the more extreme of the two. On DDHQ probabilities, the **expected value of our Senate, House and control positions falls from +5.5k (at Polymarket prices) to +1.1k**. On governors it falls **from +2.3k to -0.5k**. The book is "toward Polymarket", so its edge depends on Polymarket's confidence being right. |
| **B. The control markets show the largest disagreements** | **U.S. House:** Polymarket D 0.935 (0.94 on 4 Oct), Kalshi 0.91, DDHQ **0.75**, Silver Bulletin 0.85-0.87 (Aug-Sep), Economist ~0.95 (spring), G. Elliott Morris's bias-corrected race ratings 0.75-0.89. Tournament 0.837. **U.S. Senate:** Polymarket D 0.645 (0.63 on 4 Oct), Kalshi 0.63, DDHQ **0.54**, The Hill **0.55**, Silver "63-69%" (late Sep), Pollsmax 0.29 (outlier). Tournament 0.673. Our long Dem House (8,876) is +865 at Polymarket and **-777 at DDHQ**. Our short Dem Senate (-5,000) is +138 at Polymarket and **+663 at DDHQ**. |
| **C. The single largest disagreement in a held race is New Hampshire Governor** (`gov.py`) | Polymarket R **0.96**, DDHQ R **0.64**: a 32-point gap. Cook, Silver Bulletin and Race to the WH all rate it Likely R. The last polls were Ayotte +10 / +11 (UNH, Saint Anselm, August). We hold **long Rep 2,380 and short Dem -3,000**. That pays +0.86k if Ayotte wins and **-4.5k if she loses**, so the breakeven is p = 0.84. On the ratings and polls (about 0.88-0.92) the position is still +EV, but thin: the edge is ~1/3 of what Polymarket implies. |
| **D. Where models and polls agree against Polymarket, it is Texas and Kansas** | **TX Senate:** Dem 0.625 (Polymarket), 0.56 (DDHQ), 0.55 (The Hill), 0.673 (tournament). Every source puts Rep TX above the 0.33 tournament price, which supports reader 3's Texas digital. **KS Senate:** Dem 0.345 (Polymarket), 0.23 (DDHQ), Lean R (Cook), 0.362 (tournament). Kansas has the deepest history of polls overstating Democrats (2014: Orman led, Roberts won by 10.6). **Iowa** is the opposite case: DDHQ says Dem 0.52, Polymarket 0.435. Iowa polls missed toward the Democrats in 2020 and 2024, so I would not follow the model there. |
| **E. The House and the Senate trade as one factor** (Polymarket Balance of Power, 4 Oct) | The prices are D sweep 0.65, R Senate + D House 0.29, D Senate + R House 0.01, R sweep 0.07. With P(House D) 0.94 and P(Senate D) 0.66, that **R sweep price implies a tetrachoric correlation of 0.8-0.95** between the two control outcomes (independence would give 0.02). Our control rho (0.85) is consistent with that. |
| **F. The rules do not say when trading stops for each market, and the API and the rules disagree** | The rules say "All trades must be received prior to 12:00pm Eastern Standard Time on November 4, 2026". The API's market objects (as mirrored in tests/fakes.py) carry `settlementDate 2026-11-04T00:00:00Z`, and the tournament's `endDate` is 2026-11-04T17:00:00Z (= 12:00 EST). If the markets stay open after 00:00 UTC, returns come in during trading (see FC-1). |

## 1. Sources (what each says that matters to us)

**Rules and the exchange**
1. **SIG Predictions Cup Official Rules** (https://predictionscup.com/rules/, read 4 Oct). Quoted verbatim:
   - "The Competition begins on October 1, 2026 at 12:00pm and ends on November 4, 2026 at 12:00pm" (ET), and "All trades must be received
     prior to 12:00pm Eastern Standard Time on November 4, 2026."
   - "Each Participant's final score/rank shall be based on his or her verified total SUSQies balance as of the resolution of all Markets."
   - "No tiebreakers will be used in the Competition. Tied participants will receive the same rank", and the tied prizes are pooled and
     split. Prizes: $30,000 / $5,000 / $2,500.
   - "Platform Operator is responsible for resolving the Markets in accordance with the objective resolution criteria set forth on the
     Platform." "Sponsor may, in its sole discretion, update the rules, outcomes, or resolution of a Market at any time, including after the
     resolution date or end date."
   - "Participants may not trade in any Market while in possession of material non-public information concerning the outcome of the
     underlying event." Disqualification covers multiple accounts, MNPI, tampering and "unsportsmanlike conduct".
   - The Leaderboards tab shows "real-time ranking" under "Current Standings", and "Final Standings" are published after market
     resolution.
   - The rules say **nothing** about independents, how party control is defined, runoffs, recounts, uncalled races, withdrawals, void
     markets or mark-to-market.
2. **SIG platform docs** (https://sig.thesuper.market/docs, /changelog). The Settlement & Payouts and Portfolio pages are JS-rendered and
   showed no mechanics. The changelog (1-2 Oct) says:
   - "API rate limits now apply per account" (100 reads / 30 writes per minute).
   - "API market data may be up to two seconds old".
   - "Live updates that can't be delivered promptly are now dropped".
   - "Analytics opens on the competition standings ... trader rankings by account value".
   The API reference (/api/v1/docs) is blocked by robots.txt.
3. **Market titles in our own client** (tests/fakes.py mirrors the API): "Will the {party} Party win the {race}?", with `settlementDate`
   "2026-11-04T00:00:00Z", plus a tournament `endDate` "2026-11-04T17:00:00Z".
   - The contract is about **a party winning a race**, so an independent's win is "Ind ... YES" and both party legs settle NO.
   - "Will the Democratic Party win the U.S. Senate?" has no stated rule for independents.
4. **BusinessWire launch release, 22 Sep 2026.** Confirms the dates, 100,000 SUSQies, a "continuously updated leaderboard", and the ties
   rule. Nothing on settlement mechanics.
5. **Polymarket "Which party will win the Senate in 2026?" rules** (via polymarketanalytics.com; Polymarket's own page hides the text).
   Quoted:
   - "A party wins control of the United States Senate if it wins a majority of the chamber's voting seats."
   - "If no party wins a majority of voting seats, the party that wins control of the chamber is the party that wins half of the voting
     seats and holds the Vice Presidency."
   - "Senate members elected as independents who formally caucus with, or, for newly elected members, who announce their intent to caucus
     with, a party are included in that party's count." If they have not announced by **4 January 11:59 pm ET**, "the member will not count
     towards the seat count of any party."
   - If the outcome is still ambiguous, it resolves by the party of "the first selected President Pro Tempore". Listed resolution date:
     4 Nov 2026.
   - Since this is our fair value, SIG's control contract is priced as if these rules applied.
6. **Kalshi 2026 Senate guide** (news.kalshi.com). Democrats need net +4, "since Vice President J.D. Vance would break 50-50 ties".
   Alaska uses ranked-choice voting.
7. **georgia.gov: "Election Day - General Election Runoff", 1 Dec 2026** (early voting from 23 Nov). Any Georgia race below 50% +1 settles on
   1 Dec.
8. **Wikipedia, 2026 Louisiana Senate election.** Louisiana now has closed party primaries (May 16, runoff June 27) and a 3 Nov general
   between Letlow (R) and Davis (D), with **no December runoff**. Louisiana is therefore not a late-settlement risk this year.
9. **Ballotpedia News, 14 Sep 2026: Alaska's second ranked-choice Senate general.** Four candidates: Sullivan (R), Peltola (D), Heikes (R),
   and a second Dan Sullivan (R). Peltola took 49.5% in the top-four primary. In 2022 the RCV tabulation ran 15 days after the election,
   so expect about **18 Nov** this year. A repeal initiative is on the 2026 ballot too.

**Forecasts and ratings (state of play, 23 Sep - 4 Oct)**
10. **DDHQ / The Hill 2026 Senate, House and Governor forecasts** (votes.decisiondeskhq.com, elections2026.thehill.com, 30 Sep - 2 Oct).
    - Senate: D 54%, 51-49. TX 56 D, OH 51 D, ME 53 D, IA 52 D, AK 60 D, MI 71 D, NH / NC 82 D, MN 86, GA 87; SC 72 R, FL / KS 77 R, NE 82 R,
      LA 89 R, MT 91 R, MS 92 R.
    - House: D 75%. Governors: NH 64 R, MI 74 D, AZ 71 D, IA 69 D, SC 75 R, VT 97 R.
    - The Hill's model also uses prediction-market data. This is my main model benchmark (all 435 + 35 + 36 races, one date).
11. **Cook Political Report Senate ratings, 23 Sep.**
    - Toss-up: AK, IA, ME, MI, NH, OH, TX. Lean D: NC. Likely D: GA, MN. Lean R: KS. Likely R: NE, SC. Solid R: FL, MT, LA, MS and the rest.
    - **NH is a toss-up for Cook, while Polymarket has it at 0.875.**
12. **Silver Bulletin (natesilver.net): FLIPR launch, "Midterm waves are the rule" (29 Sep) and "What if the polls are wrong again" (3 Oct).**
    - Launch odds (11 Aug): Senate D 55%, House D 85%. Late-September Senate odds were reported at 63-69% (Forbes, 23 Sep); Newsweek had
      58.6% on 14 Sep.
    - Generic ballot D+8.9, likely-voter adjusted D+9.6. The post-Labor-Day swing is ~2.5 points.
    - FLIPR uses four error layers: national, demographic, state, race. It builds in no party-specific poll bias.
    - The current numbers are paywalled.
13. **Polymarket / Kalshi tracker pages** (Covers 1 Oct, Bodog 29 Sep, Forbes 23 Sep, polymarket.com 4 Oct).
    - House D 91-94, Senate D 62-66.
    - Kalshi late September: Texas Talarico 60%, Ohio Brown 59%, Maine Jackson 65%, Michigan El-Sayed 62%, Iowa Turek 42%.
    - Market volume: Kalshi House $73.8M, Senate $42.4M, Texas Senate $16.6M, Maine $10.9M.
14. **Polymarket "Balance of Power 2026"** (4 Oct): D sweep 0.65, R-Sen / D-House 0.29, D-Sen / R-House 0.01, R sweep 0.07. This implies the
    near-perfect chamber correlation in check E.
15. **Forbes "Hottest midterm races" (2 Oct)**, with the latest polls by race:
    - TX Talarico 51-49 (Fox). IA Turek 49-47 (Fox). MI El-Sayed 50-49 (Fox). OH Brown 47-43 (Suffolk).
    - **ME Collins 48-44 (NYT/Siena)**. NH Pappas 50-45 (NYT/Siena). AK Peltola 46-41 (AARP 50+). FL tied 45-45 (St Pete Polls).
      KS Hamilton 45-43 (Emerson).
16. **NC polls, September** (Carolina Journal, Elon, ECU, The Hill): **Cooper +10 to +15** over Whatley. This supports Polymarket's 0.96
    against DDHQ's 0.82.
17. **NH Governor** (Wikipedia poll table; UNH 28 Aug): Ayotte +10 (UNH, 20-24 Aug) and +11 (Saint Anselm). Rated Likely R by Cook,
    Sabato, Silver Bulletin and Race to the WH; Solid R by Inside Elections.
18. **Vermont Governor** (Vermont Public / VTDigger, 25-28 Sep): "tightest race in a decade", Scott narrowly ahead of Janoo. DDHQ's 97% R
    is plainly a structural prior that lags the polls. Here Polymarket (0.78) is the better number.
19. **Generic ballot and approval** (Silver Bulletin, RCP via Forbes 28 Sep; Quartz 1 Oct; Newsweek 2 Oct).
    - Generic ballot D+7.9 (Silver), D+8.4 (RCP). Individual polls D+8 to D+15.
    - Trump approval in the averages ~37-39.5%; AP-NORC has 31%.
    - In the same window of 2018 the generic was D+7-8; the result was D+8.6 and a 41-seat gain.
20. **Special elections** (Brookings; uspollingdata tracker): Democrats average **D+12** over the 2024 baseline in 2025-26 specials (2017-18
    was D+8). Thirty state-legislative flips R->D and none the other way.

**Calibration, model-versus-market evidence, polling error**
21. **G. Elliott Morris, "The race raters are lowballing the Democrats" (18 Sep 2026).**
    - 45 days out in 2010-2022, Cook toss-ups went **67% to the opposition party**, and Lean seats of the president's party held only 78%.
    - Rater-implied House odds, bias-corrected: Cook 47->75%, Inside 49->87%, Sabato 57->89%.
    - **In a midterm, ratings and structural models lag toward the president's party.** That is the main argument for trusting
      Polymarket's more extreme prices this cycle.
22. **Split Ticket, "What's in a rating?" (2024).** Cook's "Lean" wins 95%+ (they are conservative), Sabato's Lean wins 77%, and 538's
    "Likely" won ~92%. Human raters shift months after the data.
23. **Gelman, Hullman, Wlezien & Morris, "Information, incentives, and goals in election forecasts", JDM 2020.**
    - Silver aimed for "20%s really mean 20%", but in his record they meant **14%**: the models were underconfident.
    - State errors are correlated, and too-low correlation forces too-wide state intervals.
24. **Rothschild 2009, "Forecasting elections: comparing prediction markets, polls and their biases", POQ 73(5)** (researchdmr.com PDF).
    - Debiases Intrade with **Pr = Phi(1.64 x Phi^-1(price))** (Leigh et al. 2007, 1880-2004). The best-fitting coefficient for 2008 was
      2.72.
    - "A mean probability of 95 may translate into a price of 85."
    - Debiased markets beat debiased polls, especially early and in competitive races.
25. **Le 2026, arXiv 2602.19520** (429k contracts on Kalshi and Polymarket). Political calibration slope **1.83 at 1 week and 1.73 at 1 month+**:
    political prices are compressed toward 50%. A 70c contract at 1 week recalibrates to ~83%. This is pooled across all political markets,
    not elections only.
26. **Cardozo & Rivero-Wildemauwe, arXiv 2609.12878 (Sep 2026)**, 588M Polymarket trades, Nov 2022 - Mar 2026. **Politics shows a two-sided
    FLB**:
    - Buys below 10c lose 6.3% (equal-weighted) or 19.4% (dollar-weighted).
    - Buys at 90c or more earn +0.28% / +0.83%.
27. **Becker, "The microstructure of wealth transfer in prediction markets" (jbecker.dev, 2025, Kalshi).**
    - Longshots under 20c underperform. 95c contracts win 95.83%. NO beats YES at 1-10c and 91-99c.
    - Politics' maker-taker gap is 1.02 pp (moderate).
28. **Polymarket accuracy page.** Platform accuracy is 90.1% at 1 month and 94.2% at 1 week; Brier 0.0627. All categories; not
    election-specific.
29. **firstsigma (Substack), 2022 midterm forecast comparison.** By log score: Metaculus > 538 > Manifold > Polymarket / PredictIt /
    Election Betting Odds. "The prediction markets all forecasted substantially more of a right lean than 538."
30. **Bransfield 2022, "Did bookmakers & prediction markets fare that badly in the 2022 Senate races?"**
    - Every venue and model missed GA, NV and PA. The models' lower Brier came mostly from being more extreme ("greater certainty").
    - Smarkets beat PredictIt.
31. **Rajiv Sethi, "The lesson for markets" (7 Nov 2024).** In Iowa 2024, Polymarket fell to 73% Trump after the Selzer poll while 538
    never went below 93%, and the model was right. Markets overreact to single polls. On popular vote, the markets (Kalshi 76% / PM 73%
    Harris) did worse than 538 (71%).
32. **Cutting et al., arXiv 2507.08921 (2025).** Polymarket beat the polls in 2024 (5 of 7 swing states), mainly because it reacts faster.
33. **Nate Silver, "Actually, sometimes polls underestimate Democrats" (2026).**
    - Midterm poll bias: **2022 D+0.8, 2018 R+0.5** (small). Presidential years: 2016 D+3.0, 2020 D+4.7, 2024 D+2.9. Average since 1998
      D+1.1.
    - "When Trump is on the ballot ... we underestimate[d] him." In midterms the polls "did a pretty decent job".
34. **Silver, "What if the polls are wrong again" (3 Oct 2026).** Since 2008: four cycles biased against Republicans (2014 and three Trump
    presidential years), one against Democrats (2012), four unbiased. Off-year Trump-era elections were biased **toward Republicans** by
    1.3 points.
35. **G. Elliott Morris, "Are the polls overestimating Democrats again?" (21 Aug 2026).**
    - The lagged bias predicts the next cycle's sign only 10 of 14 times, with a correlation SE of 0.38.
    - "Unskewing" by past bias **worsened** forecasts in 7 of 10 cycles since 2002. RCP's 3-point R shift in 2022 increased the error in
      8 of 8 key Senate races.
36. **Patrick Ruffini, "Why Senate polling is always worse in summer" (2026).** Across 3,000+ polls (2018-24), Senate polls overstated
    Democrats by D+5.9 in late July, shrinking to **D+2.6 by election day**. The cases were red-state Democrats: Graham-Harrison 2020,
    Ryan OH 2022, Brown OH 2024. Midterm polls converge by election day; presidential-year polls do not.
37. **Slate, Oct 2026, "Why the polls are so good for Democrats".** The risks Lake and Ruffini name are youth turnout and depressed GOP
    response. The "missing white non-college" error matters most in NE, IA and KS. Polls in 2018 were roughly accurate.
38. **Newsweek, 2 Oct 2026, "GOP within striking distance of flipping 7 states".** Republican targets: Senate GA, MI, MN, NH; Governor KS,
    OR, WI.
39. **The Economist midterm model (Off the Charts Substack, spring 2026).** House D "19 in 20". Inputs: primaries, polls, fundraising,
    specials, approval. The president's party has lost 1.3 points in the final 190 days on average since 1994.
40. **Pollsmax Senate forecast (3 Oct).** D 28.7%. A structural outlier: it shows how far the model spread runs (0.29-0.69).
41. **Newsweek / uspollingdata on Silver Bulletin (April / Sept).** The April forecast was House D 72%, Senate D 54%. Its 90% House interval
    was "D+5 to D+45 seats". The model has since moved toward the Democrats as the generic ballot widened.
42. **Inside Elections via 270toWin (1 Oct).** The safe list is AL, AR, CO, DE, ID, IL, KY, LA, MA, MS, MT, NJ, NM, OK, OR, RI, SD, TN, VA,
    WV, WY. Those are the "ultra-favourite" contracts our longshot shorts sit on.
43. **Montana Senate (Inside Elections "Daines' drop inserts uncertainty"; Daily Montanan Feb 2026).** There is an independent in the race.
    The Ind leg trades at 0.08 (PM 0.0825) and DDHQ has R at 91%.
44. **Atlanta News First, 17 Sep (Rasmussen).** GA governor Jackson 48 - Bottoms 45, 7% undecided. A Georgia governor runoff on 1 Dec is
    plausible.

## 2. IDEAS

### FC-1. Find out NOW whether trading continues after 00:00 UTC on 4 Nov. If it does, election night is the biggest edge we have; if not, pull every resting quote by 23:30 UTC   [source: Predictions Cup rules ("All trades must be received prior to 12:00pm Eastern Standard Time on November 4, 2026"); our API objects (`settlementDate` 2026-11-04T00:00:00Z, tournament `endDate` 17:00Z); Polymarket 2024 reaction speed (Cutting et al. 2025)]
What the source says: The rules allow trades until 12:00 pm ET on 4 Nov, which is 17:00 UTC. The market objects carry a 00:00 UTC
`settlementDate`. Nobody has checked whether the order endpoint still accepts orders between 00:00 and 17:00 UTC. The rules forbid only
*non-public* information, and election returns are public.
Idea for us: (a) Ask SIG support (info@thesuper.market), or read the API docs as a logged-in user, for the exact per-market trading close.
`mm_bot.py:4121` already takes min(settlementDate, endDate). (b) **If markets close at 00:00 UTC**, the danger is resting quotes from
23:00 UTC, when IN/KY polls close and Polymarket starts moving on bellwether returns. The bot follows Polymarket, so keep the reference
refresh at full speed 22:00-00:00 UTC and never let a quote rest stale for more than one cycle. (c) **If markets stay open**, build an
"election-night mode": take any tournament quote that is more than X c through the live Polymarket price, and drop the tilt-correction
(the tilt is meaningless once results arrive). With ~1,040 players' resting orders and races being called (AP calls on FL, GA, NC, OH,
TX by ~03:00-05:00 UTC), this could be the single largest P(>= 150k) lever available.
Value: (b) protects against losing perhaps 1-5k to faster players in the last hour. (c), if open, is plausibly **+10-40k**: walk any
resting book on called races, the way the arbitrage on PredictIt / Betfair works in-play. That moves P(>= 150k) materially. | Cost:
one support e-mail; (c) is ~150 lines (a results-driven reference override plus a take loop), but the existing take path can be reused
with the reference = the live Polymarket mid.
Check: untestable offline. Look at how the exchange treats the 00:00 UTC settlementDate: does `/tournaments/{slug}/markets?status=open`
still list the markets after it? Ask SIG directly.
Fair play: ok. Results are public. It is not MNPI and it is not manipulation, provided we only *take* existing quotes and never quote to
move a mark.

### FC-2. Re-centre the two control contracts on a market + model consensus, not Polymarket alone   [source: DDHQ / The Hill (2 Oct); Silver Bulletin; Economist; Morris 2026 (raters' bias); Sethi et al. 2021 via ANOM-10; Bransfield 2022]
What the source says: For the House, Polymarket (0.935-0.94) is more extreme than every model except the Economist (DDHQ 0.75, Silver
0.85-0.87, Morris-corrected ratings 0.75-0.89). For the Senate, Polymarket (0.63-0.645) sits above DDHQ (0.54) and The Hill (0.55), close to
Silver (0.63-0.69), and far above Pollsmax (0.29). Averaging forecasters beats any single one (Sethi; Bransfield; firstsigma).
Idea for us: Set `ref(control) = 0.5 x Polymarket/Kalshi average + 0.5 x model median` in logit space. That gives **House D ~0.88-0.89**
and **Senate D ~0.59-0.60**. Consequences:
- **Long Dem House (8,876 at ~0.84):** keep it, but **stop adding above 0.86**. The consensus edge is +4-5c, not the +10c Polymarket
  implies.
- **Short Dem Senate / long Rep Senate:** the consensus edge rises to 7-8c at the 0.67 bid, which makes **the short Dem Senate the
  best-supported control trade**. Reader 3 (PORT-1) found Rep Senate at 0.35 "about fair" on Polymarket; on the consensus it is +5c
  (+15%).
Value: E[final] +0.3-0.6k from re-sizing (more short-Dem-Senate, less long-Dem-House at the margin). P(<= 85k) slightly lower, because
the House long is our largest single position and the models' 25% R-House mass is a tail the Polymarket reference ignores. | Cost: a
per-eid model reference in settings (two numbers, refreshed by hand daily), 10-20 lines.
Check: re-run H_outcome / I_senate with pHouse 0.88 and pSenate 0.60 in place of 0.935 / 0.645, and compare plan (a)'s E and P(>= 150k).
Fair play: ok.

### FC-3. A "consensus band": add only where Polymarket AND the models are on the same side of the tournament price; hold, don't add, where they straddle it   [source: DDHQ race tables; Cook 23 Sep; Morris 2026; Rothschild 2009; Sethi 2024 (Iowa); `cmp.py`]
What the source says: Models and markets have each been right and wrong. In 2024 Iowa the model was right and the market overreacted to
one poll. In 2022 the markets leaned R and the models won. In midterm waves the raters lag (Morris), and markets beat polls early
(Rothschild).
Idea for us: For each race, set `lo = min(PM, model)` and `hi = max(PM, model)`. Sell YES only if bid > hi + edge. Buy YES only if
ask < lo - edge. Inside the band, the allocator may **hold** an existing position but not add. On today's book (from `cmp.py` / `gov.py`):
- **Adds that pass:** short Dem TX Senate (0.67 > hi 0.625); short Dem KS Senate (0.36 > 0.345); long Rep NH Senate short side (sell
  Rep NH at 0.22 > hi 0.18); short Dem MI Senate at 0.745 (> 0.725, small); short Dem Senate control; the safe-seat longshot shorts
  (MA, RI, TN, OK, SD, WY, MN-02, etc.: both sources below 0.04).
- **Hold, don't add:** short Dem SC (0.21 between PM 0.125 and DDHQ 0.28); short Dem FL (0.17 vs 0.09 / 0.23); short Rep NC (0.12 vs
  0.036 / 0.18); short Rep GA (0.13 vs 0.028 / 0.13); short Ind NE (0.245 vs 0.18 / 0.265); long Dem House above 0.86; long Rep NH Gov;
  short Rep AZ Gov; long Dem MI Gov.
- **Positions the band says are wrong-side on BOTH:** none of size. The House races FL-22 / VA-01 / MI-07 / PA-07 / CO-04 differ by 13-19
  points, probably because of the new maps. Leave those to Polymarket and do not add, since DDHQ may be on a different map (see FC-10).
Value: lowers the chance that our edge is a Polymarket artefact. It gives up perhaps 10-20% of the allocator's nominal edge, but on
races where that edge is least certain. E roughly flat to +0.3k; P(<= 85k) down a little. | Cost: one model-probability table (~120
numbers, typed in daily from DDHQ; FC-11) and ~30 lines in the allocator hurdle.
Check: replay the allocator decisions of 3 Oct with the band and count how many $ it would have refused.
Fair play: ok.

### FC-4. Do NOT extremise Polymarket for the safe-seat and favourite contracts beyond b ~1.1; the literature's b 1.6-1.8 does not apply to these midterm race markets   [source: Le 2026 (b 1.73-1.83); Rothschild 2009 (1.64, 2.72 in 2008); Becker 2025; Cardozo-Rivero 2026; counter-evidence: DDHQ and Cook far LESS extreme than Polymarket, Gelman et al. 2020 (models underconfident)]
What the source says: Pooled across political markets, prices are compressed (b > 1), and favourites earn small positive returns while
longshots lose. But our race markets on Polymarket are *already* more extreme than the quantitative models in 28 of 37 disagreements.
Polymarket Rep RI Senate 0.008, Rep MA Gov 0.034, Dem SD Senate 0.0015: these are not compressed prices.
Idea for us: Use b = 1.0 (no extremising) for contracts where Polymarket is already beyond the model, and at most b = 1.1 on the
remaining favourites. In logit terms: 0.035 -> 0.025. This also answers ANOM-9: extremising for ranking is fine, but it should not raise
the edge we *believe* on safe-seat shorts. Those are already 5-10c rich against both sources, so the extra 1c changes nothing.
Value: neutral on E. It avoids an over-confident allocator stacking more capital on tail shorts whose edge is already counted. |
Cost: config (b per class).
Check: on snap03, count how many allocator decisions change between b 1.0 and 1.2. I expect only the 85-92c favourites to move.
Fair play: ok.

### FC-5. Calibration margin k: require an edge of `k = 0.015 + 0.4 x |PM - model|` per share before adding   [source: synthesis of FC-2/3; Le 2026 horizon slopes; Silver (FLIPR) four error layers; Ruffini (Senate polls converge by election day in midterms)]
What the source says: At 1 month out, the cross-source dispersion (PM vs DDHQ) on our races has a median ~6 points and a 90th percentile
~15 points. Midterm Senate polls still move ~2.5-3 points in the last month (Ruffini; Silver's post-Labor-Day swing of 2.5).
Idea for us: Replace the flat value hurdle with a per-market `k`:
- **Agreeing markets (|PM - model| < 3 pts):** k ~2.7c. Safe seats, MI Senate, MN Senate.
- **TX:** k ~4.1c. The 0.67 short clears it at 4.5c on PM and 11c on DDHQ.
- **SC / FL / NC / GA:** k 5-8c. Do not add.
- **NH Gov:** k 14c. Do not add.
As the election approaches and the models converge on the polls, |PM - model| shrinks and the hurdle falls by itself.
Value: E roughly neutral. It mainly cuts the size of the left tail (P(<= 85k)) from correlated "PM-was-overconfident" losses.
| Cost: same table as FC-3 plus one formula.
Check: on snap03, compute the hurdle for every held eid and list the positions above it (they are fine) and below it (hold-only).
Fair play: ok.

### FC-6. Shade Texas and Kansas Senate 2-4 points toward the Republicans in the outcome model (not Iowa, not via a general R shade)   [source: Ruffini 2026 (red-state Senate Dems overstated: Graham 2020, Ryan 2022, Brown 2024); Kansas 2014 Orman; DDHQ / The Hill (TX D 0.55-0.56; KS D 0.23); Morris (unskewing generally hurts); Silver (midterm bias small, 2018 R+0.5, 2022 D+0.8)]
What the source says: A blanket "polls overstate Dems" shift has hurt forecasts in most cycles (Morris; RCP 2022). But a specific
pattern holds in red states: high-profile Democrats who lead in red-state polls tend to finish below them (TX, OH, SC, KS). In midterms that
gap shrinks to ~D+2.6 by election day (Ruffini). Here the models and Kalshi's older pricing agree.
Idea for us: Set pTX(D) = 0.60 (vs 0.625) and pKS(D) = 0.31 (vs 0.345) in H_outcome / the allocator. Leave Iowa at Polymarket (0.435),
which already sits below DDHQ and reflects Iowa's known miss. Leave Maine at Polymarket, even though Collins outran her 2020 polls by ~9:
NYT/Siena has her +4 and the market is at 0.595 D. The band in FC-3 keeps us out of Maine anyway.
Value: with reader 3's TX digital (PORT-2), the shade raises its edge from 4c to ~6c at the 0.67 Dem bid. On a $15-20k sleeve that is
**E +0.6-0.9k**, and it adds ~1-2 points to P(>= 150k) through the higher P(Rep TX). | Cost: config.
Check: re-run reader 3's `scan.py` / `robust.py` with pTX 0.60 (inside his tested range of 0.33-0.369 for Rep). His result already holds
at that end.
Fair play: ok.

### FC-7. Model the national factor as a 1-month forecast error of ~3.5-4 points with house-race rho ~0.5-0.6, and keep control rho at 0.85-0.9   [source: Polymarket Balance of Power (check E: implied chamber correlation 0.8-0.95); Gelman et al. 2020 (state errors correlated; too-low correlation is a known model failure); Silver FLIPR four layers; Shirani-Mehr et al. 2018 via PORT-12; Ruffini (bias SD); Morris (bias unpredictable, SE 0.38)]
What the source says: Chamber outcomes trade as near-perfectly correlated. Pairwise state error correlation in published models is
~0.5-0.7 once the national term (poll bias plus late national movement) is included. Midterm poll bias alone is small (|bias| ~1-3
points) but unpredictable in sign.
Idea for us: Re-run the outcome Monte Carlo with rho 0.55 (vs 0.45) and the control legs at 0.85-0.9. Our book is a short straddle on
the national swing (PORT-B), so a higher rho fattens both tails. That is the honest tail for a D+8 environment, where the generic ballot
moved 2.5 points in September alone.
Value: no direct E change. It moves P(<= 85k) up by perhaps 1-2 points in the simulation, which is the true risk and matters for sizing
the TX sleeve (reader 3's cliff at ~$20-22k may move down to ~$17-19k). | Cost: config in the analysis scripts.
Check: H_outcome / I_senate at rho 0.55, then compare P(<= 85k) at sleeve sizes 15k / 20k.
Fair play: ok.

### FC-8. Senate-control ambiguity: Osborn (NE) and the Montana independent can leave the U.S. Senate contract unresolved until January, or resolved by SIG's discretion   [source: Polymarket Senate rules (independents counted only on caucus intent by 4 Jan; else ambiguity -> President Pro Tempore); SIG rules (no definition; "Sponsor may ... update ... resolution ... at any time"); DDHQ NE 18% / Polymarket 26.5% Osborn]
What the source says: If Osborn wins and does not announce a caucus, a 50 D + Osborn + 49 R Senate has no majority. Democrats would hold
half the seats but not the vice presidency, so under Polymarket's rule control goes to the party of the first President Pro Tempore,
chosen in January. SIG's contract ("Will the Democratic Party win the U.S. Senate?") has no stated rule at all.
Idea for us: Treat about **3-5%** of the Senate control outcome as "resolution unknown": P(Osborn wins) ~0.2-0.26 times P(the other 99
split exactly 50-49 D) ~0.15-0.2. In that state our short Dem Senate (-5,000) and long Rep Senate (491) could settle either way, late.
(1) Do not size the control sleeve as if that mass were cleanly Rep. (2) Our short Ind NE (-2,229) pays exactly when this risk is
absent; it is a natural partial hedge. Keep it. (3) Ask SIG how independents count (same e-mail as FC-1).
Value: avoids overstating P(Rep control) by ~2-4 points when sizing a Senate sleeve. | Cost: none (judgement), or one parameter in
I_senate.
Check: in I_senate's simulation, count the share of draws with Osborn winning and D = 50 among the rest.
Fair play: ok.

### FC-9. Settlement calendar: rank is "balance ... as of the resolution of all Markets", so late-settling races delay the final standings but do not change our balance; plan for no cash recycling after 4 Nov   [source: SIG rules; georgia.gov runoff 1 Dec; Alaska RCV (2022 tabulation 15 days after the election); Maine RCV (ME-02 2018 took ~9 days); California certification ~30+ days; Polymarket Senate rule (4 Jan)]
What the source says: Markets close before results. Payouts come at resolution, and the final rank waits for the last market.
Idea for us: Nothing is marked at close for rank purposes. Our late-settling holdings are:
- **Georgia** Senate (+1,625 Dem / -2,757 Rep) and Governor (-170 Dem): runoff on 1 Dec if nobody reaches 50%. The Governor race (Jackson
  48 - Bottoms 45) has a real runoff chance.
- **Alaska** Governor (-3,000 / -3,000 NO+NO set) and Senate: RCV tabulation ~18 Nov.
- **Maine** Senate / ME-02 (-1,279 Dem): RCV takes ~1-2 weeks if nobody gets 50%.
- **CA-22 / CA-48** (none held), the AZ and WA-03 counts (-811 Rep WA-03): days to weeks.
- **U.S. Senate** control: if FC-8 bites, January.
None of this costs us anything, since there is no cash to recycle after the close. The one real risk is SIG's discretion clause on an
**unresolved or disputed** market (a recount, a runoff, a withdrawn candidate). SIG may void it or resolve it by a rule we cannot see.
Keep the sum of positions in markets with a plausible ambiguity (GA, AK, ME, NE-Ind, Senate control) inside ~15% of the account.
Value: P(<= 85k) protection against a discretionary void; small. | Cost: a settlement-risk tag on ~8 eids (config list).
Check: list the current exposure on those eids from status.json: about 23k shares notional, most of it in sets.
Fair play: ok.

### FC-10. House races: suspect map mismatch, not mispricing, where DDHQ and Polymarket differ by 13+ points (FL-09, FL-22, VA-01, MI-07, PA-07, CO-04, PA-10)   [source: DDHQ House forecast (raw lines verified); our snap03 refs; Economist caveat ("assumes current district boundaries"); mid-decade redistricting in 2025-26]
What the source says: The Economist warned that models built on old lines miss new gerrymanders. Several states redrew in 2025-26.
DDHQ's FL-09 (62% D) against Polymarket's 0.295 and its VA-01 (28% D) against 0.47 look like a different map or a different candidate
field, not a different opinion.
Idea for us: Exclude these 7 districts from any model-based shading, and keep them out of value adds unless Polymarket and Kalshi agree.
We hold small shorts there (Dem FL-22 -904, Dem / Rep VA-01 -565 / -466, Dem / Rep MI-07 -833 / -658, Rep PA-07 -878). These are mostly
sets or near-sets, so leave them.
Value: avoids a few hundred $ of model-driven mistakes. | Cost: none.
Check: compare each district's candidate names on Polymarket and on DDHQ by hand.
Fair play: ok.

### FC-11. A daily "forecast table" input: DDHQ / The Hill race probabilities + Cook rating, typed into settings, used only as a second opinion   [source: DDHQ (single page, all races, one date, public); Cook ratings; Split Ticket rating-to-probability maps]
What the source says: DDHQ publishes all 35 + 435 + 36 races with probabilities on public pages (verified readable with WebFetch). Cook
ratings map to roughly Likely 90-95%, Lean 78-95% depending on the rater, toss-up 50% (67% to the opposition in midterms).
Idea for us: A JSON file `model_probs.json` {eid: p}, refreshed by hand once a day (5 minutes), with the date in the file. It feeds
FC-2/3/5 and nothing else. The bot never trades *toward* the model alone.
Value: the enabling step for FC-2/3/5/6. | Cost: ~30 lines plus a daily manual step.
Check: N/A.
Fair play: ok.

### FC-12. Expect the Democratic environment to drift a bit further, not mean-revert, before 3 Nov, so don't bet on R reversion in the 15-85c middle   [source: Silver "Midterm waves are the rule" (D lead 6.5 -> 8.9 in September; waves usually grow); Economist (president's party loses 1.3 points in the final 190 days, 1994+); Morris (toss-ups 67% to the opposition); special elections D+12]
What the source says: In midterms the president's party typically loses ground late, and the opposition wins most toss-ups.
Idea for us: In two-way market making in the middle, skew inventory acceptance slightly toward Dem-side fills (e.g. +0.5c of skew), not
toward R. Combined with FC-6 (TX, KS), the net is: the national D drift is real, but the red-state Democratic candidates are
overstated. Those are two separate effects, and the model should hold both.
Value: small (+0.1-0.3k) on middle MM. It mainly avoids being run over by a trend on 15-85c Dem legs. | Cost: config skew.
Check: on snap03, look at the Dem-leg vs Rep-leg markouts over 1-3 Oct (D drifted up). Were our MM fills on Rep legs worse?
Fair play: ok.

### FC-13. Bellwether hour: keep the reference live and quotes tight-to-reference 23:00-00:00 UTC on 3 Nov, when IN/KY returns move Polymarket   [source: poll-closing times (IN/KY eastern 6 pm ET = 23:00 UTC; GA/VA/SC/VT/FL-most 7 pm ET = 00:00 UTC); Cutting et al. 2025 (Polymarket's reaction speed); 2018 precedent (KY-06 moved markets early)]
What the source says: Polymarket repriced within minutes on early information in 2024. The tournament crowd is slower.
Idea for us: Treat the final hour as normal value-mode operation with a faster reference, not as a flattening window. The bot already
never sells below Polymarket (value-mode guard). It needs: no pre-close flatten (MM-10 agrees), reference poll interval of 1-2 s, and
cancel-on-reference-move (pull a quote within one cycle if Polymarket moves more than 1c). The edge comes from tournament players whose
quotes go stale.
Value: +0.5-2k if Polymarket moves 3-10c on KY / IN bellwethers before the 00:00 close. Defensive value too: no stale quotes picked off.
| Cost: settings plus a check that the realtime and REST paths stay within budget (28 writes/min).
Check: replay a Polymarket jump in live_sim's fake feed (by the executor; I did not run it).
Fair play: ok. It uses public prices only.

## 3. TOP 5 for our situation
1. **FC-1 (trading-close check plus an election-night mode if open).** One e-mail decides between "pull quotes at 23:30 UTC" and the
   biggest P(>= 150k) lever in the tournament: taking stale quotes on called races until 17:00 UTC. The rules text ("trades ... prior to
   12:00pm Eastern Standard Time on November 4") and the API (`settlementDate` 00:00Z) disagree.
2. **FC-2 + FC-3 (a consensus reference for the control contracts, and the add/hold band).** Our edge is +5.5k at Polymarket and +1.1k at
   DDHQ. The band keeps the adds where both agree: short Dem TX, short Dem KS, short Dem Senate, the safe-seat shorts. It freezes the rest:
   no more Dem House above 0.86, no adds to SC / FL / NC / GA / NH-Gov.
3. **FC-6 (Texas and Kansas shaded 2-4 points to R; nothing else).** This supports and strengthens reader 3's Texas Rep digital. It is
   the only race where every model, the recent Kalshi history and the red-state polling-error literature all point the same way.
4. **FC-7 + FC-8 (rho 0.55 with control 0.85-0.9; an Osborn ambiguity mass of 3-5% on Senate control).** These are the honest tails for
   sizing any sleeve. They probably move reader 3's cliff from ~$20-22k down to ~$17-19k.
5. **FC-13 (the bellwether hour).** It is cheap. It turns the last hour from a risk (stale quotes) into a small edge, and it holds
   whichever answer FC-1 gets.

**The biggest disagreement between the models and Polymarket among our markets:** **New Hampshire Governor**. Polymarket has Rep 0.96,
DDHQ 0.64. Ratings say Likely R and polls say Ayotte +10. We hold long Rep 2,380 plus short Dem 3,000, a combined Ayotte bet that loses
4.5k if she loses and has a breakeven of p 0.84. Among the control contracts it is the **U.S. House**: Polymarket 0.935-0.94 vs DDHQ 0.75
(Silver ~0.86). That is our largest position (+8,876 Dem at ~0.84), +865 on Polymarket and -777 on DDHQ.

**What contradicts the current plan:** The plan's fair value is Polymarket, and the literature on market *underconfidence* (Le, Rothschild,
Becker) is used to justify extremising it. For *this* cycle's race markets that is backwards. Polymarket is already more extreme than the
models in 28 of 37 disagreements. In a midterm wave the models and raters lag (Morris), which argues for Polymarket where fresh polls
agree (NC, VT, NH Gov). But it does not license adding more on top. The 2022 lesson (markets leaned R, models won) and the 2024 Iowa
lesson (the market overreacted, the model won) both say to blend.

## 4. Sources I could not access (or only partly)
- SIG docs: Settlement & Payouts and Portfolio & Leaderboards. They are JS-rendered with no body text; Playwright was blocked
  (ERR_BLOCKED_BY_CLIENT). The API reference /api/v1/docs is blocked by robots.txt, and the proxy refuses direct curl to
  sig.thesuper.market (CONNECT 403). **The per-market trading close and the resolution criteria are still unread.**
- Polymarket's own rules text on polymarket.com (not in the HTML). Read via polymarketanalytics.com instead.
- Silver Bulletin current numbers (paywall). Only the 11 Aug launch odds and reported late-September ranges are available.
- X / Twitter posts (PollTracker: Silver 57% / 87%, date unknown; Economist model posts): robots-blocked.
- FiveThirtyEight 2018 / 2022 "how our forecasts did" (redirects to ABC; the archive.org permission prompt timed out).
- Kenric Nelson, "Accuracy of 538's 2022 House forecasts" (LinkedIn, robots-blocked).
- FiftyPlusOne Senate forecast (no body text), Split Ticket's 2026 model (not found as a page), Race to the WH forecast page (not
  fetched; only its NH Gov rating via Wikipedia).
- politicalwire 3 Oct "Forecasters favor Democrats" (redirect loop); Yahoo Silver House piece (429).
- ahasignals 2024 election-market accuracy (404).
