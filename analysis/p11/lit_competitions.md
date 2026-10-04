# P11 literature sweep, reader 6: TRADING COMPETITIONS, BOT-VS-BOT GAME THEORY, CONSISTENCY ARBITRAGE (theme tag CMP)
Reader 6, 4 Oct 2026, ~13:00-13:45 UTC.

**Access caveat (read first).** Every WebFetch in this session (the arXiv API, the Semantic Scholar API, Wikipedia) failed with
"permission request was not answered in time and was withdrawn". WebSearch was already out of budget. **I fetched no web source
this session.** The sources below are cited from my own knowledge of the literature (cutoff mid-2026). Each one is marked
`[not fetched]`. Authors, years and main results are ones I am confident of. URLs are given only where I am confident of them. No number
here is quoted from a page I read today. So the brief's "read at least 15 sources" is **not met by fetching**. 19 sources are summarised
from memory. Before any number below is used in a decision, check it against the paper.
To make up for this, the claims that matter most I tested on our own data: read-only Python sqlite queries on `/home/claude/snap03/md.sqlite`
(the `sqlite3` CLI is not installed). Scripts are in my scratchpad (`dutch.py`, `dutch2.py`, `q20.py`). They are not in the repo.

## 0. What our own data says on this theme (snap03, 1-3 Oct)
| check | result | meaning |
|---|---|---|
| Within-race Dutch book, **sell side** (sum of the other traders' best bids over the legs > 1; legs where the best bid is ours excluded) | **30%** of race-snapshots overall; **52%** on 3 Oct (73.6k of 141k); 2-leg races **48%** in the last 600 cycles; **3-leg races 99.8%** | The book is overround almost all the time. That is the tilt seen as a whole race: longshot bids are rich and favourite bids are not cheap enough to offset them. The gap is 0.5c in 43% of cases, ≥ 2c in ~20%, and up to 4c+. |
| Within-race Dutch book, **buy side** (sum of best asks < 1) | 9.6% of race-snapshots; mostly 0.5-1c | Buying a riskless set below 1 is rarer and smaller. |
| Persistence of a gap (runs of consecutive ~66 s snapshots) | sell gaps: 6,647 runs, median **2 snapshots (~2 min)**, p90 **24 (~25 min)**, max 674; buy gaps: median 2, p90 22 | Nobody runs a fast set-arbitrage bot here. A gap lasts minutes, not milliseconds as on Polymarket (CMP-5). |
| Hour of day (2-3 Oct) | share 21-49%; **highest 20:00-22:00 UTC (43-49%)**; size largest 04:00-07:00 UTC (1.8c avg vs 1.1-1.3c) | US evening retail raises how often the gaps appear. Thin overnight books make them larger. |
| Gap $ at top-of-book depth (the sparse `books` table: 545 full-race samples) | sell side ~2.4k $ of riskless profit summed over 185 samples (~13 $ per occurrence); buy side ~0.7k | Each gap is small, but they recur all the time. Edge per $ of collateral is (Σb-1)/(legs-Σb) ≈ **1-2% riskless**, against our 5-10% one-sided value edge. |
| The "20-share" quoter (the size seen in 230 of 237 markets, p9 C-15) against our fair value | 1,293 of its levels seen; **14% (185)** sit > 1c through our fv. Every one is on the tilt side: it bids longshots above fv (e.g. 3.5c vs fv 2.1c) and offers favourites below fv (e.g. 81.5c vs 84.3c). Its offsets are spread out (from -8c to +4.5c), not pegged to fv | It quotes around the tournament mid (tilt-following), not around Polymarket. Wherever the mid is through our fv, it hands us 20 shares on the value side. |

## 1. Sources (all `[not fetched]`, from memory)
1. **Gode & Sunder, "Allocative efficiency of markets with zero-intelligence traders", JPE 1993.** Random traders with only a budget
   constraint reach ~98% of allocative efficiency in a double auction. Without the budget constraint (ZI-U), prices wander and efficiency
   collapses. For us: in a play-money binary market, random flow drifts prices toward the centre of the range (1/legs). That gives a
   mechanical source for our tilt form mid = c + (1-s)(P - c).
2. **Das, "A learning market-maker in the Glosten-Milgrom model", Quant. Finance 2005; Das & Magdon-Ismail, "Adapting to a market shock:
   optimal sequential market-making", NIPS 2008.** An adaptive maker facing uninformed noise plus informed traders sets its spread from
   the informed share. When flow is mostly noise, the spread collapses to the tick. Our fills show ~0 adverse selection (lit_marketmaking
   §0), so in this model's terms our spread should be set by capital and the queue.
3. **Wah & Wellman, "Latency arbitrage, market fragmentation, and efficiency: a two-market model", EC 2013** (and Wah, Hurd & Wellman on
   market making in agent-based simulation). A faster agent picks off stale quotes. In their simulations, agent-based market makers gain
   from more noise flow and lose to latency arbitrage. With 28 writes/min and a ~66 s cycle, we are the slow agent. Speed races are
   not our game.
4. **Budish, Cramton & Shim, "The high-frequency trading arms race", QJE 2015.** In a continuous limit order book, sniping stale quotes is
   a race the fastest player wins. Spreads compensate makers for being sniped. For us: never post a quote that relies on cancelling fast.
5. **Chen, Fortnow, Nikolova & Pennock, "Betting on permutations", EC 2007; Chen, Fortnow, Lambert, Pennock & Wortman, "Complexity of
   combinatorial market makers", EC 2008.** Finding and pricing arbitrage across linked combinatorial markets is #P-hard in general. It is
   easy for structured families (pairs, one race's legs). So arbitrage between linked markets survives in practice because of
   computational limits and capital limits.
6. **Dudík, Lahaie & Pennock, "A tractable combinatorial market maker using constraint generation", EC 2012; Dudík, Lahaie, Rothschild &
   Pennock, "A combinatorial prediction market for the U.S. elections", EC 2013.** They price linked state and national contracts
   together. Constraint generation (e.g. "control = enough seats") removes cross-market arbitrage. They applied it to US election markets
   (PredictWise). This is the method for our Senate-control-versus-seats link.
7. **Saguillo, Ghafouri, Kiffer & Suarez-Tangil, "Unravelling the probabilistic forest: arbitrage in prediction markets", arXiv 2508.03474,
   2025.** On Polymarket (about Apr 2024-Apr 2025) they find two kinds of arbitrage. Within-market rebalancing (a negRisk market's outcomes
   not summing to 1) brought in most of the realised arbitrage profit, roughly tens of millions $. Combinatorial arbitrage across
   linked markets was rarer and smaller. A few addresses took most of it. My memory of the exact $ figure is uncertain.
8. **Polymarket docs: negRisk / "Neg Risk adapter"** (docs.polymarket.com). In a multi-outcome event, one NO share converts into YES on
   every other outcome. The conversion lets arbitrage bots close sum ≠ 1 gaps on chain, so gaps close in seconds. Our exchange has
   **no conversion**. A NO+NO set stays locked until the outcome. That explains why our gaps last minutes.
9. **Manifold: multiple-choice markets whose answers are linked to sum to 100%** (Manifold docs and changelog, 2023-24). The AMM arbitrages
   the answers automatically on every trade, so a "sum-to-1" bot has nothing to take. Before that, user bots (e.g. the "Arbitrageur" accounts)
   did it. Takeaway: where the venue does not enforce the sum, a one-man arbitrage desk keeps the sum honest only up to its capital.
10. **Rothschild & Sethi, "Trading strategies and market microstructure: evidence from a prediction market", J. Prediction Markets 2016**
    (Intrade 2012). One large trader kept Romney's price several points above other venues for weeks. Arbitrageurs did not close it,
    because of capital limits and fees. A sustained, money-losing tilt by one actor is documented, and it was not corrected.
11. **Hanson, Oprea & Porter, "Information aggregation and manipulation in an experimental market", JEBO 2006.** Manipulators trying to
    distort prices did not change price accuracy, because other traders traded against them. That needs unconstrained counter-capital.
    Our counterparties are capital-constrained, and so are we (0 free cash).
12. **Shleifer & Vishny, "The limits of arbitrage", JF 1997.** An arbitrageur judged on marks gets redemptions or losses exactly when a
    mispricing widens. So mispricings can persist and grow. Our tilt-exposure re-marks (-655 on 3 Oct afternoon) are this effect. The
    tournament's mark-to-market leaderboard gives everyone the same horizon problem.
13. **Brown, Harlow & Starks, "Of tournaments and temptations", JF 1996; Chevalier & Ellison, "Risk taking by mutual funds as a response
    to incentives", JPE 1997.** Funds behind at mid-year raise risk in the second half. Winners lock in. The effect is strongest near the
    evaluation date. **Taylor, JF 2003** adds the strategic answer: a leader can raise risk too if laggards are expected to gamble.
    **Busse, JFQA 2001** finds the effect weaker in daily data.
14. **Lichtendahl & Winkler, "Probability elicitation, scoring rules, and competition among forecasters", Management Science 2007;
    Lichtendahl, Grushka-Cockayne & Pfeifer, "The wisdom of competitive crowds", Operations Research 2013.** When forecasters compete on
    rank, they push reports away from consensus and toward extremes. This is a rank-incentive bias, not a belief bias.
15. **Witkowski, Freeman, Vaughan, Pennock & Krause, "Incentive-compatible forecasting competitions", AAAI 2018 / Management Science 2023.**
    A winner-take-all leaderboard on proper scores is not incentive compatible. Behind players should report extreme or contrarian
    numbers. Their mechanism (ELF) fixes this. For a top-k-prize trading contest, the same logic means **longshot YES is the rational
    buy for anyone behind**. It is the cheapest lottery on rank. This is the most direct explanation of a growing favourite-longshot
    tilt in a 1,000-player cup.
16. **Chapkovski, Khapko & Zoican, "Trading gamification and investor behavior", Management Science 2024** (experiment). Rank and
    leaderboard features make people trade more and concentrate in salient, high-variance assets. **Barber, Huang, Odean & Schwarz,
    "Attention-induced trading and returns: evidence from Robinhood users", JF 2022.** Attention-driven retail herds into the same
    names and loses afterwards. **Barber & Odean, "Trading is hazardous to your wealth", JF 2000.** Over-trading by over-confident
    retail.
17. **Jane Street Electronic Trading Challenge (ETC) write-ups** (many student GitHub repos named "etc-bot" / "janestreet-etc"; Jane Street's
    own description). Bots trade a bond, an ADR pair (VALBZ/VALE) and an ETF (XLF) with creation and redemption at a conversion fee.
    Repeated advice in the write-ups: the money is in **conversion arbitrage plus pennying a fixed-value bond**. Directional cleverness
    adds little. Teams that blew up mostly broke position limits or the message limit.
18. **IMC Prosperity write-ups** (e.g. "Linear Utility", 2nd in Prosperity 2, github.com/ericcccsliu/imc-prosperity-2; "Frankfurt
    Hedgehogs", Prosperity 3). The winners (a) market-make the stable products at fair value ± 1-2 ticks and take any quote through
    fair, (b) trade baskets against their components when the spread to synthetic fair is wide, (c) **reverse-engineer the simulated bots'
    deterministic rules**. One named counterparty was found to buy at the daily low and sell at the daily high; the top teams copied it.
    (d) They often cut risk in the final round to protect rank. A **public-vs-final shake-up** is common: teams overfit the visible rounds.
19. **Polymarket open-source maker bots: Polymarket/poly-market-maker** (official "bands" and AMM keeper) **and warproxxx/poly-maker**
    (community; its README warns that it stopped being profitable as competition grew, from memory). There are also many "sum-to-1 /
    negRisk arb" and "resolution sniper" repos. The common failure modes are being picked off on news, inventory piling up on one side,
    and rewards-farming quotes that are adversely selected. Polymarket's **liquidity rewards** score quotes quadratically by distance
    from the mid. That rewards tight two-sided size and supports the pennying culture.

## 2. Ideas

### CMP-1. Route every tilt trade through the overround: sell the longshot, do not buy the favourite   [source: Polymarket negRisk docs (#8); Saguillo et al. 2025 (#7); snap03 §0]
What the source says: On a venue with conversion, a NO in one outcome is the same as YES in the others, so arbitrageurs keep Σ = 1. On ours
there is no conversion, and the 2-leg sell-side overround is present 48% of the time (3-leg: ~100%).
Idea for us: when Σbids > 1, a toward-Polymarket bet (long the favourite) is strictly cheaper as **selling the longshot at its bid** than
as **buying the favourite at its ask**. The exposure is the same, the price is better by (Σbid-1) + the favourite's spread. Where the
allocator / `compute_quote` chooses between "favourite bid" and "longshot ask" for the same race, score each by its edge net of the other
leg's price and quote the better one only. With 0 free cash this is also the cash-light route: selling YES at b ties up 1-b collateral
against the a paid to buy.
Value: E[final] +0.3-0.6k (0.5-1.5c better on each re-deployed share, ~40-60k shares re-deployed by 4 Nov) | Cost: ~30 lines in the
allocator's edge-per-$ ranking (race-level choice of side); no new data
Check: snap03: for every value-side fill on a favourite bid, compute 1 - (longshot best bid) at the same snapshot against our fill price.
The share of fills that would have been cheaper as a longshot sale = the gain.
Fair play: ok (ordinary choice of instrument).

### CMP-2. Treat the overround as a riskless-set desk, priced by edge per $ against the value book   [source: Jane Street ETC write-ups (#17); Shleifer-Vishny (#12); snap03 §0]
What the source says: In ETC, conversion arbitrage is the steady money. Limits-to-arbitrage theory says capital-limited arbitrageurs leave
gaps open. Ours last a median of 2 min and a p90 of 25 min.
Idea for us: re-enable **sell-side only** `arb_*` (`arb_two_sided` stays false) with `arb_min_profit` set so that the yield beats the
allocator's marginal value edge. A set's yield is (Σb-1)/(legs-Σb), so ~1-2% at a 1-2c gap. Only gaps ≥ 2.5-3c (~10% of gaps, mostly
3-leg races and overnight 04-07 UTC) clear a 5% hurdle. Fix the one-legged leftovers from 3 Oct 22:36: send all legs in **one write**
(a write is a batch of 10), so a 2-3-leg set goes out as one atomic attempt. Take only when the free collateral ≥ the whole set's cost.
Value: P(<= 85k) down a little (riskless sets replace risky exposure); E[final] +0.1-0.3k | Cost: config plus a rule "all legs in the same
batch, or none"
Check: snap03 dutch.py: count the gaps ≥ 3c and their duration at the books' depth. Journal: ARBITRAGE lines showing partial legs.
Fair play: ok.

### CMP-3. Expect the tilt to grow into the close: hold a "Hail-Mary reserve" for the last 72 h   [source: Brown-Harlow-Starks (#13); Chevalier-Ellison (#13); Witkowski et al. (#15); Lichtendahl-Winkler (#14)]
What the source says: In rank tournaments, players who are behind raise risk as the evaluation date nears. Forecasters competing on rank push
reports to extremes. Winner-take-all leaderboards reward contrarian, extreme bets.
Idea for us: in a top-k cup with ~1,040 players, nearly everyone is behind and gains from buying longshot YES. Theory predicts **s keeps
rising toward the 4 Nov close** (1.7% → 12.6% → 14% already fits), with a final burst in the last 1-3 days. Plan: from ~31 Oct, the
allocator holds back R% of collateral (from pair unwinds and recycled sets) to **rest longshot asks / favourite bids at the then-wider
tilt**. Do not spend it all now at 14%. Add an hours-to-close term to the allocator's hurdle.
Value: E[final] +0.5-1.5k if s reaches 18-22% at the close (each $ re-deployed at a tilt 4-8 points wider earns 1-3c more per share);
P(>= 150k) ~unchanged | Cost: one allocator setting (`reserve_frac_by_hours_to_close`), ~20 lines
Check: snap03 has no close yet. Fit s by hour (p9 B_tilt) and check whether its slope is still positive. Recheck with each new snapshot.
Fair play: ok.

### CMP-4. Model the tilt as zero-intelligence flow plus capital limits, and forecast s from flow composition   [source: Gode-Sunder (#1); Hanson-Oprea-Porter (#11); Rothschild-Sethi (#10)]
What the source says: Budget-free random traders pull prices to the middle of the range. Manipulation fails only when counter-capital is
unconstrained. One persistent actor held Intrade off for weeks.
Idea for us: our form c + (1-s)(P - c) is exactly "share s of price-setting flow is ZI around 1/legs". Estimate s from the **share of
volume in non-round sizes / small tickets** (p9 C-15: 93% of sizes are non-round) per hour. If s rises with retail volume (evening UTC,
weekends), time the reserve in CMP-3 and the value quotes to those hours.
Value: modest; better timing of the ~5-10% edge, E[final] +0.2-0.5k | Cost: an analysis script; maybe a time-of-day multiplier in the allocator
Check: snap03: regress the hourly s on the hourly fill count and the share of odd lots.
Fair play: ok.

### CMP-5. Do not race: never chase a pennier, join or step back to the min-edge floor   [source: Budish-Cramton-Shim (#4); Wah-Wellman (#3); Das (#2); p9 C-15]
What the source says: In a continuous book the fastest agent wins every race. With uninformed flow, competing makers drive the spread to the
tick. A slow maker that pennies loses writes and queue position.
Idea for us: at a 0.5c tick with rivals stepping in front 6%/min (80% by exactly one tick), set `improve_ticks` 0 (join) on the value side
in the tails. Set `undercut_step_back` to the min-edge band (e.g. 0.005-0.01) so that a pennier inside our band does not pull us. Spend
writes on CMP-1/2 and pair unwinds, not on re-pricing. Our fills show ~0 adverse selection, so a joined quote fills from sweeps and the
queue ahead of us is small (median 2-3 levels).
Value: saves ~1-2 writes/min; E[final] +0.1-0.3k by moving those writes elsewhere | Cost: config only
Check: snap03 fills.csv: edge on fills of quotes at the best vs one tick behind (lit_marketmaking already has the bands).
Fair play: ok.

### CMP-6. Take the 20-share tilt-follower's quotes when they are through fv (ordinary takes)   [source: IMC Prosperity write-ups (#18): exploit bots' deterministic rules; snap03 q20.py]
What the source says: Prosperity winners profiled the simulated counterparties and traded against their predictable rules.
Idea for us: the bot that posts 20-share levels in ~230 markets quotes around the tournament mid. 14% of its levels are > 1c through our fv,
always on our value side. If it **refills at the same price after a fill** (to be tested), each take of 20 shares earns ~1.5-3c × 20 =
0.3-0.6 $ per write. That is small, but it is the one rival rule visible in our data. Fold it into the existing take rule: let a 20-lot
through fv count as a take candidate even below the usual size minimum.
Value: tiny (E +0.05-0.2k); its real value is a probe of how the rival reacts | Cost: take rule threshold, ~5 lines
Check: snap03 `books`: after a 20-lot level disappears, does a 20-lot reappear at the same price within 1-3 snapshots? (The books table
is sparse, ~1 market per timestamp, so expect a noisy answer; the live recorder would settle it.)
Fair play: ok, as long as these are takes we want at that price. Posting quotes to make it move is over the line (C-15).

### CMP-7. Senate-control and House-control against the seat markets: consistency trades   [source: Dudík-Lahaie-Rothschild-Pennock 2013 (#6); Chen et al. 2007/08 (#5)]
What the source says: Control contracts are functions of the seat contracts. Constraint generation finds the arbitrage-free joint. Cross-
market gaps survive because few traders compute them.
Idea for us: from the tournament's own seat mids (not Polymarket), compute the **implied P(Dem Senate control)** with our national factor
(rho 0.45) and the seats not on the board fixed. If the control contract's tilt is lower than the seats' tilt (players trade the
headline differently), the control contract is a cheap **bloc hedge**: trade it against the bloc-delta cap instead of unwinding seats one
at a time. One contract hedges the national factor that drives our P(<= 85k).
Value: P(<= 85k) down 1-3 points if the control contract is fairly priced against the seats; E ~0 to +0.3k | Cost: ~60 lines reusing
B_mc / I_senate.py; one allocator entry
Check: snap03: control mid against the seat-implied probability by hour (I_senate.py has most of this).
Fair play: ok.

### CMP-8. Our P(>= 150k) needs anti-field variance: one concentrated national-factor bet, not more diversification   [source: Taylor 2003 (#13); Witkowski et al. (#15); Prosperity end-game behaviour (#18)]
What the source says: Players behind in a rank contest should take correlated, contrarian variance. Leaders cut variance.
Idea for us: E[final] 109k with P(>= 150k) ≈ 0. The only route to 150k is a large, **correlated** position whose payoff is big in one
national scenario. The field (leader included) is long longshots, so the contrarian scenario is "favourites sweep", which we already
hold in small slices across races. Options: raise the bloc-delta cap on one side (e.g. favourites in the R-leaning bloc) so the book is
one national bet. Or buy the control contract on margin freed from NO+NO sets. This is the owner's "outside the risk limit" bucket. It
trades P(<= 85k) for P(>= 150k).
Value: P(>= 150k) 0 → maybe 2-5% at P(<= 85k) +5-10 points; E[final] roughly flat (same edge, more variance) | Cost: bloc cap settings;
B_mc run
Check: B_mc / H_frontier with the bloc cap raised; read off the frontier between P(>= 150k) and P(<= 85k).
Fair play: ok.

### CMP-9. Be the liquidity for Hail-Mary buyers in the last days, not a fellow gambler   [source: Chapkovski-Khapko-Zoican (#16); Barber et al. 2022 (#16); Lichtendahl et al. (#14)]
What the source says: Leaderboards and attention make retail herd into salient high-variance assets, and those buyers lose afterwards.
Idea for us: herding will focus on salient longshots: headline races, upset stories, the House/Senate control underdog. In the last
72 h, rest deeper **longshot asks in the ~20 most-traded markets** (the top 20 hold ~47% of fill edge, lit_marketmaking §0), sized from
the CMP-3 reserve. Use `adding_factor_per_market` to favour the salient names, and leave writes free for re-posting after sweeps.
Value: E[final] +0.3-1k (part of CMP-3's gain, aimed where flow is) | Cost: the allocator's market weights
Check: snap03: fill concentration by market against a salience proxy (fill count, headline race).
Fair play: ok.

### CMP-10. Do not trust the marks; the shake-up is at the outcome   [source: IMC Prosperity public-vs-final shake-up (#18); Blum & Hardt "The Ladder" 2015 (Kaggle leaderboard overfitting)]
What the source says: Public leaderboards reward overfitting to what is visible. The final ranking reshuffles.
Idea for us: the leader's +600% is on marks. At the outcome those books settle at ~28k (brief). Any rule that reacts to the marks
leaderboard (de-risking because "we're behind on marks") is chasing the public board. Keep `ref_guard_*` and value mode keyed to
Polymarket. Ignore the tilt re-mark P&L in risk triggers (`worst_case_backstop_frac` on liquidation value, not marks).
Value: protects E[final]; avoids forced exits worth ~0.5-2k in a tilt spike | Cost: check which risk triggers read `account_value`
(marks) against liquidation value
Check: code reading: list the triggers that read the marks; replay 3 Oct 14:00-19:18 (-655 re-mark) and see whether any fired.
Fair play: ok.

### CMP-11. Pair unwinds and sets: close NO+NO sets only when the gap pays more than the allocator's hurdle   [source: Polymarket negRisk (#8); Shleifer-Vishny (#12)]
What the source says: Without conversion, a set's capital is locked to the outcome. Arbitrage capital earns only what the gap gives it.
Idea for us: 21.9k sits in riskless NO+NO sets yielding 0. Dissolving a set (pair unwind) costs the overround. When Σbids > 1 (sell gap),
the NO side is cheap to buy and costly to sell, so **unwinding now is costly**. Unwind in the **buy-gap** moments (Σasks < 1, ~10%,
when NO bids are rich) and right after overnight sweeps. Gate `pair_no_unwind_max_cost` (0.02) on the race's current sum: unwind when
Σasks ≤ 1, wait otherwise.
Value: saves ~0.5-1c per set unwound on ~10-20k sets → E +0.1-0.2k, and frees cash for CMP-1/3 | Cost: ~10 lines in the pair-unwind gate
Check: snap03: for each PAIR UNWIND in the journal, the race's Σasks/Σbids at that time; the cost paid against waiting ≤ 25 min.
Fair play: ok.

### CMP-12. Battle-of-bots hygiene: message limit and position limit are where competition bots die   [source: Jane Street ETC write-ups (#17); poly-maker README (#19)]
What the source says: ETC bots were disqualified or crippled by message limits and position-limit breaches more than by bad pricing.
Polymarket makers lose money on news when one side's inventory piles up.
Idea for us: on election-adjacent news days (debates, polls), lower `writes_per_minute` usage from re-pricing to ≤ 20 and keep ≥ 8/min for
exits and arbitrage. Cap any single race's one-sided inventory with the bloc cap. Already mostly built; the gap is a **news-day mode**
switch.
Value: P(<= 85k) slightly down | Cost: a settings preset
Check: untestable offline beyond replaying the write log.
Fair play: ok.

## 3. TOP 5 for our situation
1. **CMP-1: sell the longshot instead of buying the favourite whenever Σbids > 1.** The overround is present half the time in 2-leg races
   and nearly always in 3-leg races. It is free execution edge on trades we make anyway.
2. **CMP-3 + CMP-9: a reserve for the last 72 h, aimed at salient longshots.** Tournament theory (BHS, Chevalier-Ellison, Witkowski)
   predicts that s rises into the close as the many players behind buy lottery tickets.
3. **CMP-7: the control contracts as a cheap bloc hedge / consistency trade against the seat-implied probability.**
4. **CMP-11: time pair unwinds to buy-gap moments** (unwind when Σasks ≤ 1, not in an overround).
5. **CMP-8: if the owner wants P(>= 150k) > 0, it comes only from one concentrated anti-field national bet.** Show the frontier first.

## 4. Sources I could not access
All of them: every WebFetch was refused (permission request withdrawn before an answer), and WebSearch was exhausted. Specifically
attempted: the arXiv API query on "prediction market" AND arbitrage, the Semantic Scholar API search on trading-competition risk taking,
and the Wikipedia "Prediction market" page. Not attempted after that: Polymarket docs (negRisk), Manifold docs/blog, Kalshi help, IMC
Prosperity and ETC GitHub repos, poly-maker README, SSRN pages. Everything in §1 comes from memory and should be checked before a number
from it is relied on. §0 (our data) is measured.
