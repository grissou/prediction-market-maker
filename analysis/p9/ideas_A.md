# Package 9 explorer A: how the leaders win, conviction bets, sizing for P(account >= 150k)

Data: /home/claude/snap03 (to 3 Oct 22:47 UTC). Scripts (all read-only, sqlite/pandas, < 15 s each): `analysis/p9/A_moves.py`
(moves since 28 Sep), `A_book.py` (latest book, gap x depth, settled EV of our book), `A_sets.py` (riskless sets history),
`A_tilt_path.py` (hourly tilt s), `A_catchup.py` (cross-section catch-up), `A_imbalance.py` (longshot book imbalance),
`A_top20.py` (top-20 disagreement table), `A_mc.py` (settled vs marked Monte Carlo over all 237 contracts, Gaussian copula with one
national D/R factor: rho 0.35 state races, 0.8 House/Senate control; partial information alpha 0.6 at the close), `A_path.py` (31-day
tilt paths, kill switch, exit before close), `A_cppi.py` (dynamic tilt-momentum sizing), `A_party.py` (party baskets at settlement).

## Headline findings (read these first)
1. **The +600% is the tilt itself.** Buying the cheap side of every lopsided race on 28 Sep and holding: equal-$ basket of longshot YES
   with Polymarket < 2c went x3.89 (median x3.55) by 3 Oct; 2-5c longshots x2.52; favourite NO (ref > 0.9) x2.14; the top-10 longshots
   x5.74; the best single contract x11.5 (Dem Montana Senate 0.010 -> 0.115, Polymarket 0.4c). Rotation/compounding on top of that gives
   +600%. Nothing else in the data comes close (riskless sets: <= 9.9% per set, sporadic; no contract resolves before 3 Nov).
2. **The tilt has not had a single down 6-hour block.** Hourly OLS s (|ref - c| > 0.1, spread <= 3c): 1.7% (1 Oct 12h) -> 5.5% (2 Oct
   12h) -> 10.0% (3 Oct 12h) -> 12.6% (3 Oct 22h). Mean +0.0020/h, hourly sd 0.0031, the smallest 6-hour change +0.0039. The live
   estimator is pinned at the 0.11 wall while its own diagnostics read 0.142 (slope) / 0.157 (median).
   Longshot books are bid-heavy: top-3 bid depth 3.28M shares ($282k) vs ask 0.87M ($86k) on the 48 contracts with ref < 5c.
3. **Under "settled at the outcome", no fully funded strategy reaches 150k without breaking P(<= 85k) < 10%.** Our current book settles
   at EV 106.1k at raw Polymarket (109.0k in the MC with race-normalised probabilities), P(>= 150k) 0.0%. Re-concentrating all capital
   into the 144 toward-Polymarket gaps: EV 112.7k, P(>= 150k) 0.0%. Party baskets that do reach 150k (60k into Rep tossups: 6.9%) carry
   P(<= 85k) 32%. The toward-Polymarket edge is ~7-14% per $ and capital is the ceiling: +50% is not available at settlement.
4. **Under "marked at the close", the only path to 150k is to be LONG the tilt** (the leaders' side: cheap-side YES), opposite to the
   +35.9k toward-Polymarket exposure the bot holds now. All capital in longshot YES: 161k at a +0.20 tilt rise, 208k at +0.35, 68k at
   -0.11. With CPPI sizing (floor 88k, exposure = cushion / (0.35 s), re-set daily) and an exit to cash one day before the close
   (which makes the result independent of the end-valuation rule): **P(>= 150k) 34% / P(>= 200k) 19% / P(<= 85k) 4% / P(DD > 20%) 11%**
   under my base prior for the tilt; 13% / 6% / 8% / 13% under a pessimistic prior; 50% / 30% / 2% / 8% under a trend prior.
   The priors are judgement, not data (A-2). This is the only idea family in my angle with P(>= 150k) above a few percent.
5. **The current book is a bet that the tilt reverses** (-358 marked per point of s; base-prior expected loss ~3k by 4 Nov marked) and
   reaches 150k in no scenario; it is the right book only if SIG settles at the outcome AND then it is still better bought later, at a
   higher tilt (A-7).

### Base prior on the tilt at the close (S_T; s0 = 0.12) used by A_path / A_cppi (JUDGEMENT)
reversal U(0, 0.06) 15% | stall U(0.08, 0.16) 25% | growth U(0.16, 0.30) 30% | high U(0.30, 0.55) 30%; median S_T 0.20; path
s0 + (S_T - s0)(1 - e^{-t/tau}) / (1 - e^{-T/tau}), tau U(3, 15) days, bridge noise 1c/day, plus a 10% chance of a crash that halves s
within the hour (whale liquidation / SIG intervention). Pessimistic: 30/35/25/10, crash 20%. Trend: 5/15/35/45, crash 5%.
Reasons for the base: 1039 players ranked on marks with convex rewards keep buying lottery tickets (the bid walls); the tilt rose for
5 days without a down block; but it cannot exceed ~0.5-0.6 without longshots trading at 0.25+, and a big holder exiting would crash it.

---

### A-1. Reverse-engineering the leaders: the day-one cheap-side basket
Mechanism: The leaders bought longshot YES (and favourite NO) at 1-3c in the first days and are now marked at 10-14c (exchange mark is a
trade average, so their own and other players' purchases re-mark them). x4-x6 on the basket plus rotation into laggards = +600%.
Diagnostic for us, not a trade: it says the mark-based leaderboard pays the tilt, and that whales are long it (2.3M-share bid at 0.04 on
Rep Rhode Island Governor at 2 Oct 14:13; 1.35M at 0.07 on Rep Rhode Island Senate at 11:05; ~$95k each = one whole account).
Data check: A_moves.py: equal-$ multiple 28 Sep -> 3 Oct: ref < 2c x3.89 (21 contracts), 2-5c x2.52 (27), 5-10c x1.49 (15), favourite
NO (ref > 0.9) x2.14 (59); top-10 x5.74; best x11.5. Mid - ref median for ref < 5c: +1.5c (28 Sep) -> +5.0c (3 Oct).
Upside: n/a (explains +600%; repeating it needs the tilt to rise another ~4x, i.e. s -> 0.5) | P(>= 150k) effect: frames A-2 | Ruin: n/a
End-valuation: the leaders' gain exists only under "marked"; at settlement they lose ~90% of it.
Legitimacy: analysis only.
Build: none.
Screen: data-only (done).

### A-2. Tilt-momentum CPPI: be long the tilt, sized by a cushion over an 88k floor (THE target idea)
Mechanism: Hold cheap-side YES (longshots ref < 5c, or favourite NO) with tilt exposure X < 0 (value change = -X x ds, exact for marks at
c + (1 - s)(r - c)). Size X = cushion / (0.35 x s), cushion = account - 88k, re-set daily; never above all capital. Gains raise the cushion
and the size (over-Kelly after gains, which a probability-of-target objective wants); losses shrink it. Exit to cash 1 day before close.
Data check: A_cppi.py (20k paths): floor 88k, gap 0.35, daily, 4% cost per $ traded: base P150 33.6%, P200 18.6%, P<=85 4.1%, DD>20%
10.8%, median 106.8k; pessimistic 12.8 / 5.5 / 8.3 / 13.2%; trend 50.4 / 29.7 / 1.9 / 8.2%. Gap 0.5 (more timid): base 24.1 / 9.8 / 1.0
/ 6.0%. Start size: X0 = 12.3k / 0.042 = 293k = ~54k of longshot YES at ~0.09 (X per $ = 0.47 / price = ~5.4). Visible ask depth now
0.87M sh / $86k, bid (exit) depth 3.28M sh / $282k. Static comparison (A_mc.py): all capital long longshots: marked 97.8k at ds 0, 129k
at +0.10, 161k at +0.20 (P150 99.9%), 208k at +0.35; 68k at -0.11; settled-and-held 50.0k.
Upside: +6% median, +50% at P 34% (range 13-50% by prior) per month | P(>= 150k) effect: 0% -> ~34% (base) | Ruin / drawdown: a tilt
crash (halving s in an hour gaps through the floor: P<=85k 4-8%); a slow reversal is cut by the floor; slippage on exit
End-valuation: independent IF the exit before close happens (positions are cash at the close); held to the close it needs "marked".
Legitimacy: ordinary position-taking at posted prices; we never place an order to move a price, never trade at the close to lift a mark;
cap our share of a market's volume (e.g. <= 25% of its 24h trades) so our own buying is not what marks us.
Build: settings `tilt_momentum_enabled`, `tilt_momentum_floor` (88000), `tilt_momentum_gap` (0.35), `tilt_momentum_exit_hours` (24);
a daily target X and the cheap-side basket in a new planner next to the tilt estimator (~150-250 lines near the ref_tilt code ~l.705 /
2061); everything else by A-4. Kill: X -> 0 when account < floor + 2k or when the tilt estimate drops 0.02 below its 24h max (A-15).
Screen: live_sim `_world_tilt_growth` sweep {-0.004, 0, +0.0036/h} + a crash knob (does not exist: add `_world_tilt_crash_at`); A_cppi.py.

### A-3. Flatten the toward-Polymarket tilt exposure now (the prerequisite)
Mechanism: The bot's +35.9k tilt exposure loses 358 per point of s and reaches 150k in no scenario (marked or settled). Selling it
removes the bleed and is the funding for A-2. Biggest single slice: the 23 longshot NO holdings (45.2k shares, 40.2k marked, tilt
exposure +19.9k) close for ~170 of half-spread (buy YES at the ask = sell NO).
Data check: A_book.py: book 119 positions, marked 101.0k, settled EV at Polymarket 106.1k; toward non-set positions 46, marked 68.7k,
tilt +34.0k, settled edge +6.6k; liquidation haircut whole book 708 (status.json 101,017 -> 100,309). A_path.py: current book held:
marked P150 0.0%, P<=85 4.1% (base prior), DD>20% 0.8%; flat book P<=85 0.0%.
Upside: +3k vs holding at the base prior's median ds +0.08 (358 x 8) | P(>= 150k) effect: 0 alone; enables A-2 | Ruin: none (gives up
+6.6k settled edge if SIG settles at the outcome and the tilt reverses)
End-valuation: under "settled" the toward book is worth +6.6k EV, but buying it back later at a higher tilt is worth more (A-7).
Legitimacy: selling positions.
Build: config only: `ref_tilt_max` raised (A-17) makes the bot's own fair values exit; `tilt_exit_priority`/`tilt_exit_full_size` already
on. A one-shot "target tilt exposure" setting (`tilt_exposure_target`, ~60 lines) makes it deliberate.
Screen: live_sim with `_world_tilt_growth` 0.0036 vs 0: d pnl_lag of a target 0 vs 35.9k.

### A-4. Tilt lead: quote around the FORWARD tilt so the market-maker buys the momentum book as a maker
Mechanism: Fair value = c + (1 - s_fwd)(r - c), s_fwd = s_est + slope_24h x lead_hours (capped). The bot then bids longshot YES / sells
favourite YES below the forward fair value and earns the spread instead of paying it (4% cost in A-2 -> ~0-1%), and its existing risk
limits (Kelly cap 2%/market, worst-case backstop) bound the size.
Data check: slope_24h = +0.061/day (A_tilt_path: 0.065 -> 0.126 over the last 24 h); lead 24 h -> s_fwd ~0.19; the cheap-side basket
returned +38.1% over 3 Oct 00:00 -> 20:45 (A_catchup.py) while s rose 0.061 -> 0.126 (~5.9% per point). A_cppi: tc 0.04 -> 0.02 adds
+2.7 points of P150 (24.1 -> 26.8% at gap 0.5).
Upside: same as A-2 with less cost | P(>= 150k) effect: +2-4 points over A-2 | Ruin: as A-2; the lead overshoots when the trend stalls
(forward fair value is wrong by slope x lead = 6 points if it stops dead).
End-valuation: as A-2.
Legitimacy: quoting at our own forecast fair value; same as every MM.
Build: `ref_tilt_lead_hours` (0 = off), `ref_tilt_max` bound raised 0.3 -> 0.6 in OVERRIDABLE; ~40 lines in the tilt estimator (l.2061).
Screen: live_sim with `_world_tilt_growth` 0.0036: d pnl_lag, tilt_exposure_end (should go negative), and the same at growth 0 / -0.002.

### A-5. Laggard catch-up: buy the cheap sides whose own tilt s_i is lowest
Mechanism: Contracts lag the common tilt and then catch up: buying the bottom quartile of s_i (cheap-side price relative to the cross
section) outperformed the top quartile. Concentrate the A-2 basket in laggards.
Data check: A_catchup.py, per-contract s_i (|ref - c| > 0.25, 169 contracts), slope of ds_i on (s_i - median): -0.17, +0.11, -0.07,
-0.16 over four windows; cheap-side return laggard vs leader quartile: 2 Oct 06 -> 20h +16.2% vs +3.6%; 3 Oct 00 -> 20:45 +55.6% vs
+24.4%; 2 Oct 06 -> 3 Oct 20:45 +90.9% vs +33.8% (all +61.6%). One window (2 Oct 12 -> 3 Oct 06) reversed: +25.7% vs +28.4%.
Upside: +1.5-2x the A-2 return per $ in 3 of 4 windows | P(>= 150k) effect: +5-10 points on A-2 (same X for less capital) | Ruin: laggards
can be structurally anchored (deep walls at Polymarket: Rep Illinois Senate ask 0.04 vs ref 0.022), concentrating idiosyncratic risk
End-valuation: as A-2.
Legitimacy: position-taking.
Build: rank inside the A-2 planner (~30 lines).
Screen: data-only on snapshot 02b + 03 (done above); a forward test on the next snapshot.

### A-6. Exit before the close into the longshot bid wall (makes the bet valuation-free)
Mechanism: Realised cash is worth the same under both end-valuation rules. Unwind the momentum book from T-48h to T-24h into the resting
cheap-side bids; the leaderboard's "marked" vs "settled" question then no longer matters for that capital.
Data check: longshot (ref < 5c) top-3 bid depth 3.28M shares / $282k now vs an A-2 book of ~0.6-1.1M shares; A_path.py: momentum f=1 held
to the close: marked P150 30.3%, settled 12.2% (P<=85 18.6% vs 58.5%); with exit T-1d both 29.0% / P<=85 23.9% (identical by design).
Upside: protects ~all of A-2's gain in the "settled" world (otherwise 18-48k lost) | P(>= 150k) effect: under settlement 12% -> 29%
(f=1), the biggest single valuation hedge | Ruin: the walls can vanish when everyone exits at once; our own selling lowers the marks
End-valuation: this IS the hedge.
Legitimacy: selling at posted bids; exit sized to the walls, spread over 24h, no prints aimed at marks.
Build: `tilt_momentum_exit_hours` 24-48 in the A-2 planner (~20 lines) + the existing flatten-before-close recommendation.
Screen: cannot be screened (no close in the data); live_sim at end-of-horizon with wall depth from books.

### A-7. Late conviction flip: buy toward Polymarket at the END, at the maximum tilt, only if SIG settles at the outcome
Mechanism: Under "settled", the toward-Polymarket edge per $ grows with the tilt: NO on a 2c longshot costs 1 - (c + (1 - s)(r - c)).
Holding it now earns s = 0.12 worth of edge and loses marks; buying it with the A-2 proceeds at T-24h earns s_T worth.
Data check: A_top20.py: settle return per $ now on the top-20 gaps 3-14% (capital-weighted 6.8%); at s 0.3 a 2c longshot NO costs 0.836
-> +17.2% per $; at s 0.4, 0.788 -> +24.4%. On a 150k account at s 0.3: +26k EV; A_mc toward_all book at current prices: EV +12.7k, p5
95.9k, P<=85 1.0%.
Upside: +10-25% in the settled world, 0 in the marked world (flip skipped) | P(>= 150k) effect: raises P200 in the settled world
(150k -> ~176k EV) | Ruin: correlated favourites losing (MC p5 -4%); a wrong read of the rule
End-valuation: only with "settled"; requires SIG's answer (A-8).
Legitimacy: position-taking.
Build: config (ref_tilt_max back to the estimate, adding factor on) at T-24h; ~0 lines.
Screen: A_mc.py with the book rebuilt at the tilt reached.

### A-8. Ask SIG the end-valuation rule today (value of information)
Mechanism: The best book flips sign with the answer: settled -> hold toward-Polymarket late (A-7); marked -> hold the tilt to the close
(A-2 without the exit cost). Markets close 4 Nov 00:00 UTC, one hour after the first polls close, before returns, so "settled" means
SIG resolves after the close and re-ranks.
Data check: current book settled EV 106.1k vs marked 101.0k (+5.1k); momentum f=1 held: settled 50.0k vs marked 97.8k (ds 0) -> 161k
(ds +0.2): a 48-110k swing on one rule.
Upside: removes 4% exit cost (A-2) or adds A-7's +10-25% | P(>= 150k) effect: +2-5 points (exit cost and timing) | Ruin: none
End-valuation: the point.
Legitimacy: asking the organiser.
Build: manual (owner email/Discord).
Screen: n/a.

### A-9. Dem House: the deepest single mispricing (conviction under settlement only)
Mechanism: Dem U.S. House mid 0.8375 vs Polymarket 0.935; its own s_i 0.224 is double the common tilt (deep market, 10k quotes). YES at
0.84 settles at +11.3% EV per $. We hold 8,876 YES + 4,396 Rep NO = 13.3k shares (tilt exposure +5.8k, 16% of ours).
Data check: A_top20.py; single-binary Kelly f* = (p - pi)/(1 - pi) = (0.935 - 0.84)/0.16 = 0.59 of the account; A_party.py: +15k / +30k /
+60k into Dem House + Dem Senate: settled EV 104.1-104.3k, P150 0.0%, P<=85 5.4 / 6.0 / 35.1%. Reaching 150k needs a 258k stake.
Upside: +1-3% at settlement | P(>= 150k) effect: 0 | Ruin: a Rep House (6.5%) costs the whole stake; marked: -5.5% per $ of the line at
+0.10 tilt, -15.8% at +0.30 (A_top20)
End-valuation: settled only.
Legitimacy: position-taking.
Build: config only (headline limits exist).
Screen: A_party.py (done).

### A-10. Under settlement, the probability-of-target optimum is "no bet": proof by search
Mechanism: Search the obvious correlated bets for any that gives P(>= 150k) > 0 with P(<= 85k) < 10% at settlement.
Data check: A_party.py / A_mc.py (20k draws): Dem tossups (21) +15k/30k/60k: P150 0/0/3.5%, P<=85 4.3/14.1/34.8%; Rep tossups (24):
P150 0/0/6.9%, P<=85 3.5/14.4/32.0%; Rep House YES +15k: P150 6.2%, P<=85 17.2%; underpriced Dem favourites (40) +60k: P150 0%, P<=85
2.0%; whole account toward Polymarket: P150 0%, EV 112.7k.
Upside: n/a | P(>= 150k) effect: shows the cap: under settlement the target is unreachable within the ruin limit (best ~0-1%) | Ruin: n/a
End-valuation: settled only.
Legitimacy: analysis.
Build: none. Screen: done.

### A-11. Funding at 0 cash: the order to sell things in
Mechanism: A-2 needs ~54k of cheap-side YES at the start. Cheapest sources first: (1) longshot NO holdings (selling them IS buying the
cheap side: closing 45.2k NO shares = half-spread ~170, frees ~40k marked and flips 19.9k of exposure); (2) favourite YES (19 lines, 23.6k
marked, tilt +12.2k; ~1-2% half-spread); (3) NO+NO sets last: 16.8k sets / 21.9k capital, sum of asks median 1.02 -> unwind cost ~0.02
per set = ~340, but each set is riskless and pays 1 at settlement.
Data check: A_book.py / pos.csv breakdown: toward non-set 46 lines 68.7k marked; inside NO+NO races: toward 11 lines 22.7k (tilt +9.0k),
against 8 lines 2.1k (tilt -6.4k); status turnover_dead_capital 70.7k in 55 markets (Dem House 7,726 and Rep House 3,826 dead).
Upside: enables A-2 at ~0.5-1% of the account in costs | P(>= 150k) effect: prerequisite | Ruin: none
End-valuation: under "settled" each sold NO+NO set gives up its locked sb - 1 (~0.5-3c).
Legitimacy: selling.
Build: config (A-3) + a priority order in the A-2 planner (~20 lines).
Screen: data-only.

### A-12. Riskless YES sets below 1, bought with the momentum book's spare cash
Mechanism: A race's YES legs sometimes sum below 1 at the asks: buy all legs, it pays exactly 1 at settlement and marks at ~sum of marks
(median 1.012 across our races). Small, riskless, valuation-neutral; uses idle cash between A-2 rebalances.
Data check: A_sets.py: 2,312 snapshots; 96 races showed a YES set < 0.995 at some time (15,682 race-snapshots); minimum 0.91 (South Dakota
Senate, 1,409 snapshots), Idaho Senate 0.96, FL-20 0.94, Michigan Governor 0.935. Now: median sum asks 1.02; cheapest 0.975 (Arkansas
Senate, Louisiana Senate).
Upside: +0.2-0.5% per month on 10-20k of cash | P(>= 150k) effect: ~0 | Ruin: none (one-legged fills at 0 cash, the 22:36 lesson)
End-valuation: independent.
Legitimacy: buying at posted asks.
Build: existing arbitrage path with a cash rule (`arb_enabled` true only when free cash >= both legs).
Screen: data-only (done).

### A-13. Leverage per $: concentrate the momentum book in the cheapest cheap sides
Mechanism: Tilt exposure per $ is (c - r)/price: a 4c longshot gives ~12 X per $, a 13c one ~3.6. Same X with a third of the capital,
leaving the rest flat or in A-7. Combine with A-5 (laggards are the cheap ones).
Data check: A_mc.py momentum basket (48 contracts, mean ask 0.098): X per $ = 316k / 100k = 3.16; cheapest asks now: Ind Rhode Island
Governor 0.03, Rep Illinois Senate 0.04, Rep New Mexico Senate 0.055, Dem Nebraska Senate 0.06 (A_mc debug list). Cross-section: price
leverage explains why 2 Oct 06 -> 3 Oct 20:45 returned +61.6% for +8.3 points of s (7.4% per point at ~6c entry) vs 3.2% per point now.
Upside: halves A-2's capital need | P(>= 150k) effect: +3-6 points via less forced selling and lower costs | Ruin: cheap contracts are
cheap because a wall anchors them (Polymarket-anchored rivals); idiosyncratic
End-valuation: as A-2.
Legitimacy: position-taking.
Build: a per-$ weight in the A-2 planner (~10 lines).
Screen: data-only forward test on the next snapshot.

### A-14. Verdict on the bot's current conviction bet (+35.9k toward Polymarket): wrong sign for the objective, about right for its own
Mechanism: It is a Kelly-capped book on the gap (2%/market caps bind on 5 of the 8 biggest), sized well for EV at settlement but it pays
only if the tilt reverses (marked) or at settlement (+5-8k). For P(>= 150k) it contributes 0 in every scenario.
Data check: A_mc.py: current book settled mean 109.0k, P150 0.0%, P<=85 0.2%; marked at ds -0.11/0/+0.10/+0.20/+0.35: 105.2 / 100.9 /
97.3 / 93.7 / 88.3k (P<=85 6.4% at +0.35). A_path.py base prior: P<=85 4.1%, P(DD > 20%) 0.8%.
Upside: +5-8% at settlement; -3% median marked | P(>= 150k) effect: 0 | Ruin: tilt +0.35 -> 88k
End-valuation: positive only under settlement.
Legitimacy: n/a. Build: none (A-3 changes it). Screen: A_mc.py (done).

### A-15. Kill switch from the tilt's own pillars: longshot bid/ask imbalance and the whale walls
Mechanism: The tilt is carried by bid walls on cheap sides. De-risk A-2 when (a) the cheap-side log(bid depth / ask depth) falls below 0
for 12 h, or (b) the tilt estimate falls 0.02 below its 24 h max, or (c) any >500k-share wall on a longshot disappears.
Data check: A_imbalance.py (6-hour blocks, walls capped 50k): log imbalance 1.03, 0.92, 1.18, 1.24, 0.93, 1.05, 0.74 (the latest is the
lowest); corr with the next block's ds +0.43 (n = 6, no power). Walls seen: 2.31M at 0.04 (Rep RI Governor), 1.35M at 0.07 (Rep RI
Senate), 0.92M at 0.06.
Upside: cuts A-2's crash loss (the main ruin path, P<=85 4-8%) | P(>= 150k) effect: -1 to -3 points (false alarms), P<=85 down ~half
(guess) | Ruin: lag of one cycle; the walls can be pulled faster than we exit
End-valuation: n/a.
Legitimacy: reading the public book.
Build: `tilt_momentum_kill_imbalance`, `tilt_momentum_kill_drop` (~40 lines; books already loaded each cycle).
Screen: data-only on the next snapshots; live_sim crash knob.

### A-16. Raise `ref_tilt_max` from the 0.11 wall to the estimate (0.14-0.16 now)
Mechanism: The estimator reads 0.142 (slope) / 0.157 (median) but is clipped to 0.11, so the bot's fair values sit 3-5 points too far
toward Polymarket: it keeps resting toward-Polymarket adds and prices its tilt exits too high. Uncapping alone stops adding to the bleed.
Data check: status.json tilt_diag slope 0.1422, median 0.1571, wls 0.1245, tilt_s 0.110 at the wall; A_tilt_path hourly OLS 0.126 at
22:00. Mark cost per point 358.
Upside: ~+1-1.5k (3-4 points x 358 not added again) | P(>= 150k) effect: ~0 alone; it is the first step of A-3/A-4 | Ruin: none (the
OVERRIDABLE bound is 0.3)
End-valuation: under "settled" it slightly reduces the toward edge kept.
Legitimacy: config.
Build: config only (`ref_tilt_max` 0.2).
Screen: live_sim `_world_tilt` 0.14 with ref_tilt_max 0.11 vs 0.2.

### A-17. Mark-spike awareness for a long-tilt book: hold through spikes, never sell below the mark late
Mechanism: The exchange mark is a trade average that spikes on thin cheap sides (Rep Rhode Island Governor mark 0.06 -> 0.163 overnight 2-3
Oct while the mid was ~0.07-0.10). A long cheap-side book gains from such spikes on the leaderboard; in the last hours before a marked
close, sell only where bid >= mark, hold elsewhere.
Data check: A_moves.py / positions: mark - mid on held longshots mean -0.2c, sd 1.5c, range -5.5c..+2.2c; mark max/first up to x4.5.
Sum of marks per race median 1.012 (0.949-1.057).
Upside: +0.5-1.5% at the close (marked only) | P(>= 150k) effect: +1 point near the threshold | Ruin: none
End-valuation: marked only.
Legitimacy: choosing when to sell; never trading to create a print.
Build: an exit filter in the A-6 path (~20 lines).
Screen: data-only (mark vs bid by hour).

### A-18. "Half-and-half" across valuations: momentum to T-1d, then split by SIG's answer
Mechanism: The combined plan: A-3 (flatten) -> A-2 + A-4 + A-5 (long the tilt, CPPI, maker entries, laggards) -> A-6 (exit T-48..24h) ->
A-7 (if settled: toward-Polymarket at the maximum tilt) or hold the cheap side to the close (if marked, A-17).
Data check: A_cppi base: P150 33.6%, P200 18.6%, P<=85 4.1% before A-7; A-7 adds ~+15% EV in the settled world on the exit value
(at S_T 0.3), so P200 rises roughly from 19% to ~30% there (estimate: 150k x 1.17 = 176k; 171k x 1.17 = 200k).
Upside: median +6%, 34% chance of +50% | P(>= 150k) effect: ~34% (base), 13-50% across priors | Ruin: P<=85 4-8%, DD > 20% 11-13%
End-valuation: hedged by A-6, exploited by A-7.
Legitimacy: as the parts.
Build: the parts.
Screen: live_sim tilt-growth sweep + crash knob; A_cppi.py.

### A-19. What it would take with the current (toward) sign: tilt reversal lottery
Mechanism: The only way the existing book reaches 150k marked: the tilt collapses AND we lever the toward side. Max toward book (all
capital, X +45.3k) makes +4.5k per -0.1 of s; s can fall at most 0.12. Ceiling ~106k.
Data check: A_mc.py toward_all at ds -0.11: mean 106.0k, P150 0.0%.
Upside: <= +6% | P(>= 150k) effect: 0 | Ruin: tilt +0.35 -> 84.6k mean, P<=85 57%
End-valuation: marked.
Legitimacy: position-taking. Build: none. Screen: done. (Listed to close the door: the toward side cannot reach the target.)

---

## TOP 5 (by P(>= 150k) per unit of ruin risk)
1. **A-2 Tilt-momentum CPPI** (floor 88k, exposure = cushion/(0.35 s), daily): P150 34% (13-50% by prior), P200 19%, P<=85 4% (2-8%),
   DD>20% 11%. The only idea in this angle that clears ~30%.
2. **A-6 Exit before the close into the bid wall**: makes A-2 valuation-free (settled-world P150 12% -> 29% at f = 1); 3.28M shares /
   $282k of cheap-side bids today.
3. **A-3 + A-11 Flatten the toward book, longshot NO first**: 45.2k NO shares close for ~170 and flip 19.9k of the 35.9k exposure; the
   book reaches 150k in no scenario and bleeds 358/point (base-prior expected -3k).
4. **A-4 Tilt lead (+ A-16 uncap)**: buy the momentum book as a maker at forward fair value; halves A-2's 4% cost (+2-4 points P150).
5. **A-5 Laggard catch-up (+ A-13 cheap-side leverage)**: laggard quartile +90.9% vs +33.8% over 2 Oct 06 -> 3 Oct 20:45; more X per $.
Also do at once, free: **A-8 ask SIG the end-valuation rule** (it decides between A-7 and holding the cheap side to the close).

## Assumptions I could not check
- The tilt prior (S_T distribution, 10% crash chance) is judgement; the data has only ~54 hours of rising tilt and no reversal to calibrate
  a crash. P(>= 150k) for A-2 moves from 13% to 50% across the three priors I tried.
- The exchange's mark of a large long-cheap-side position: modelled as c + (1 - s)(r - c) at the book's own tilt; the real mark is a
  lagged trade average and our own buying feeds it (legitimacy: cap our volume share; never buy late to lift marks).
- Exit liquidity: the 3.28M-share bid walls stay until we exit; if the whales leave first, slippage is far above the 4% modelled.
- Linear value in s holds per contract only with Polymarket fixed; election news moves r (modelled as 4k sd on the book, uncorrelated
  with the tilt).
- Race outcomes: Gaussian copula with one national factor, rho 0.35 / 0.8 by judgement; a few races' Polymarket legs do not sum to 1 and
  were normalised (settled EV 106.1k raw vs 109.0k normalised).
- No position limits per account beyond capital (the bot caps itself at 2%/market and 10k on headline markets; A-2 needs ~40-60 lines of
  1-3k each, inside those caps; overriding Kelly caps needs the new planner).
- That SIG does not change rules or intervene with Polymarket-anchored liquidity before 4 Nov (that is the crash scenario).
- live_sim was not run (brief); its world has no crash knob and no long-tilt planner yet.
