# Package 9 explorer B: the tilt as a trade, and the exchange's mark

Data: /home/claude/snap03 (to 3 Oct 22:47 UTC). Scripts (read-only, sqlite/pandas, each < 30 s): `analysis/p9/B_tilt.py` (hourly tilt
by estimator and side, session profile, rolling slope, saturation table, cheap-side depth), `B_logistic.py` (logistic fit and
ceiling profile), `B_mark.py` (mark vs lagged mid / EWMA, mark changes vs our fills), `B_mark2.py` (mark update timing),
`B_mark3.py` (mark response to our fills over +1..120 min), `B_markgap.py` (q x (mark - mid) and q x (mark - liquidation) per full
poll, split by tilt side), `B_book.py` (two entry routes in binary races, unwind/entry capacity), `B_mc.py` (31-day hourly Monte Carlo of
positioning variants, 3 tilt worlds + mixed prior), `B_mc_sens.py` (sensitivity of the best variant). A's ideas (A-2 CPPI, A-3 flatten,
A-4 maker lead, A-5 laggards, A-6 exit, A-13 cheap leverage, A-15 kill switch) are not repeated; where I touch them I add a new
mechanism or a number.

## Headline findings
1. **The tilt is a logistic, not a line, and its ceiling K decides everything.** OLS s (|ref - c| > 0.1, spread <= 4c): 0.018 (1 Oct
   12h block) -> 0.048 -> 0.064 -> 0.076 -> 0.091 -> 0.100 -> **0.120** (3 Oct 18-24h). A logistic fits it with K = 0.35 (se 0.18),
   r = 0.033/h; **K < 0.20 is rejected** (dAIC +3.2 at 0.20, +13.7 at 0.15) but K 0.25-2.0 are indistinguishable. The equal-weight
   (winsorised) series is already bending: K 0.176 (se 0.035), slope at the end 0.0017/h, i.e. the median contract is near its plateau
   while the extreme ones (|ref - c| near 0.5) keep running. The longshot books are 35% more tilted than the favourite books
   (0.137 vs 0.102). The 12 h slope re-accelerated on 3 Oct (0.0006/h at 2 Oct 06h -> 0.0026/h at 3 Oct 18h).
2. **Forecast (base world: K median 0.30, 12% crash, 35% endgame unwind):** s at +1 d median 0.172 (p5 0.128, p95 0.232); +7 d 0.289
   (0.138-0.578); +14 d 0.287 (0.135-0.595); close 0.215 (0.062-0.561), P(s_close < 0.12) 20%. Most of the move is in the next 7 days;
   after day 14 the expected tilt FALLS (crashes and endgame unwinds accumulate). Bear world (K 0.18): close median 0.121, 50% below
   today. Valuation-relevant s at the close: "marked" = the market s above (through a ~1-4 h lagged mark); "settled" = 0 for anything
   held (a 2.5c longshot settles at 2.5c on average whatever it trades at). The market tilt does NOT have to collapse at the close
   (no race resolves before 4 Nov 00:00 UTC; nobody is forced to sell); only our valuation of a held position collapses under "settled".
3. **Best positioning variant: ratchet-CPPI long the tilt, exit by T-7d** (B-2): basket $ = 5 x (account - floor), floor = max(86k,
   0.85 x peak), cap 80k, daily rebalance, intraday floor guard: **mixed prior P(>= 150k) 41.9%, P(>= 200k) 12.4%, P(<= 85k) 2.0%,
   P(maxDD > 20%) 6.5%** (base world 45.6 / 10.9 / 0.9 / 5.5; bear 12.8 / 1.4 / 4.9 / 10.8; bull 76.1 / 32.7 / 0.2 / 2.6). Bolder
   m 6 / cap 90k: 45.9 / 17.8 / 2.5 / 7.2. Exiting at day 10 instead of T-7d: base 51.1 / 12.2 / 0.8 / 2.9. The current book: 0 / 0 /
   6.2 / 9.7. The answer is dominated by K: base-world P150 is 47% at K median 0.30, 19% at 0.20, 7% at 0.15.
4. **The exchange's mark is a slow average of OTHER players' trades, half-life ~1-4 h, and our fills barely touch it.** Mark vs the
   EWMA of the mid: median |gap| 0.34c at a 60-min half-life vs 0.64c vs the current mid. 97% of mark changes happen with no fill of
   ours; median step 0.016c every ~4 min. After our fill bursts (n 501, median 171 shares, 1.18c from the mark) the mark has moved
   0.1% of the gap at +1 min, 2.5% at +15, 13% at +60, 28% at +120 min: not last-N trades (a last-5 average would jump 20%).
5. **The "~1.4k below the mid" gap is gone: it was the tilt's direction, not a constant.** Sum q x (mark - mid) was -1,583 at 2 Oct
   20:28 and is **+162** at 3 Oct 22:42; q x (mark - liquidation) is **+638** (87 lines marked above their exit price, +845; 32 below,
   -207). In a rising tilt a lagged mark favours short-tilt holders (our toward book: +133) and charges new long-tilt buyers ~X x slope
   x lag (~1.2k on a 300k-X book). The legitimate levers are exit ORDER and exit TIME, never prints (B-9..B-12).
6. **A route discount for entering the long-tilt book: favourite NO is cheaper than longshot YES in 42 of 58 binary races, by 1.57c
   (18% of the price)**: the longshot books are bid above 1 - favourite bid (sum of YES bids > 1: median round trip at the best routes
   -8.5%). Enter via favourite NO, exit via the longshot YES bid (that leaves a NO+NO set, riskless at settlement) (B-4).

---

### B-1. The tilt as a logistic with an unknown ceiling: a forecast to size against
Mechanism: Model s(t) = K / (1 + e^{-r(t - t0)}) plus noise, crashes and an endgame unwind; size every tilt bet on this distribution
(not on a straight-line 0.0036/h, which reaches 2.7 by 4 Nov). The logistic says: the next 7 days carry most of the remaining rise,
the plateau follows, and the value of being long decays after ~day 14.
Data check: B_tilt.py / B_logistic.py: OLS hourly s 0.018 -> 0.120 in 54 h; logistic OLS K 0.351 (se 0.180), r 0.0329/h, slope at end
0.0026/h, residual sd 0.0056; profile dAIC K 0.13 +25.7, 0.15 +13.7, 0.20 +3.2, 0.25 +0.8, 0.30-0.50 ~0, 1.0 +1.1; winsorised series
K 0.176 (se 0.035); longshot-side K 0.376. Hourly ds since 1 Oct: mean +0.00174, sd 0.0032, 74% of hours up. B_mc.py base forecast:
+1 d 0.172 / +7 d 0.289 / +14 d 0.287 / close 0.215 (medians).
Upside: n/a (the model every sizing idea below uses) | P(>= 150k) effect: decides it: K median 0.15 -> 7%, 0.20 -> 19%, 0.30 -> 47% for
B-2 (B_mc_sens) | Ruin / drawdown: a wrong K is the main model risk; the estimate will sharpen fast (B-17)
End-valuation: the market s path is the same under both rules; held-at-close valuation is s (marked) or 0 (settled).
Legitimacy: analysis.
Build: a `tilt_logistic_fit` diagnostic in the tilt estimator (~40 lines near the ref_tilt code); config none.
Screen: data-only: re-fit on every new snapshot; live_sim `_world_tilt_growth` should be replaced by a logistic world knob
(`_world_tilt_K`, `_world_tilt_r`).

### B-2. Ratchet-CPPI long the tilt, 5x cushion, cap 80k, exit T-7d (THE variant)
Mechanism: Hold a cheap-side basket (longshot YES / favourite NO) of $ = m x (account - floor), floor = max(86k, 0.85 x peak account),
m = 5, cap 80k, re-set daily (only when off target by > 20%), plus an hourly guard that sells everything if the account is within 1k of
the floor. Unlike A-2's fixed 88k floor, the ratchet locks 85% of every new peak (bounds max DD near 15% + gap risk) and a 5x
multiplier with a cap reaches 200k in the bull world. Exit to cash by T-7d (B-3).
Data check: B_mc.py (20k paths per world, costs 3% + 2% x $/50k per trade, 700 to flatten the current book, mark = EWMA 1.5 h):
mixed prior (base 0.5 / bear 0.3 / bull 0.2) P150 41.9%, P200 12.4%, P<=85 2.0%, DD>20% 6.5%; base 45.6 / 10.9 / 0.9 / 5.5, median 145.3k,
p5 93.6k; bear 12.8 / 1.4 / 4.9 / 10.8; bull 76.1 / 32.7 / 0.2 / 2.6. m 6, cap 90k: mixed 45.9 / 17.8 / 2.5 / 7.2. m 8 cap 100k (base):
54.8 / 21.8 / 1.7 / 8.3. Fixed-floor CPPI m 4 (A-2-like): mixed 28.2 / 0.0 / 3.0 / 25.1 (DD fails). Costs 5%: base 42.6 / 7.9 / 1.2 / 5.7.
Upside: median +45% (base), +6% (bear), +80% (bull) per month | P(>= 150k) effect: 0 -> ~42% (mixed), 7-76% by world | Ruin /
drawdown: a crash inside one hour gaps through the guard (crash odds 25%: P<=85 2.2%, DD>20% 11.8%); capacity (entry 80k vs $59k of
asks within 1c); our own buying is part of the tilt we ride
End-valuation: independent (cash at T-7d); see B-12 for holding to the close if "marked" is confirmed.
Legitimacy: position-taking at posted prices; volume-share cap (B-14); no trade timed to move a mark.
Build: `tilt_long_enabled`, `tilt_long_mult` (5), `tilt_long_floor` (86000), `tilt_long_ratchet` (0.85), `tilt_long_cap` (80000),
`tilt_long_exit_utc` (2026-10-28T00:00); a planner beside the tilt estimator (~200 lines, shares A-2's), guard on LIQUIDATION value
(B-10).
Screen: B_mc.py / B_mc_sens.py (done); live_sim needs a logistic tilt world + crash knob; forward test on the next snapshot.

### B-3. Front-loaded time stop: be out by day 10-14, not at T-1d
Mechanism: In the logistic world the expected s peaks around day 7-14 and then falls (crash hazard and the endgame unwind accumulate,
the drift is ~0 on the plateau). Holding the long-tilt book past day 14 adds risk with no drift; exiting early also avoids the last
week when settlement-aware players may sell (and when everyone else's exit competes for the same bids).
Data check: B_mc.py base medians s +7 d 0.289, +14 d 0.287, +30 d 0.215. B_mc_sens (best variant, base): exit T-1d P150 42.9% / P200
6.9% / DD>20% 6.0%; T-7d 46.7 / 11.7 / 5.4; T-14d 49.4 / 12.4 / 4.4; day 10 (T-21d) 51.1 / 12.2 / 2.9. Static 40k basket: exit T-2d
mixed 23.8 / 2.7 / P<=85 5.3 / DD>20% 36.4%; exit day 14: 30.0 / 3.6 / 0.8 / 6.7.
Upside: +4-8 points of P150, DD>20% halved | P(>= 150k) effect: +4-8 points | Ruin: missing a late leg up in the bull world (bull medians
s +14 d 0.438 vs +30 d 0.366: the late leg is rare in the model, but the model's K cap 0.6 limits it)
End-valuation: makes the bet fully independent of it (out 2-3 weeks before the close).
Legitimacy: timing of our own exits.
Build: `tilt_long_exit_utc` (config) or `tilt_long_max_days` (~5 lines).
Screen: B_mc_sens.py (done); revisit after K is pinned down (B-17).

### B-4. Route discount: enter the long-tilt book via favourite NO, exit via the longshot YES bid
Mechanism: In a binary race, long the tilt = long longshot YES = long favourite NO. The longshot book is bid richer than the favourite
book (longshot-side s 0.137 vs favourite-side 0.102), so buying favourite NO (sell favourite YES at its bid) is usually cheaper than
lifting the longshot ask, and selling the longshot YES bid is the better exit. Doing both in one race leaves a NO+NO set locked below 1
(riskless at settlement, marks at ~2 - sum of marks): the round trip earns the books' disagreement instead of paying two spreads.
Data check: B_book.py, latest snapshot, 58 binary races with a longshot ref < 10c: favourite route cheaper in 42 (longshot 13, equal 3),
mean saving 1.57c = 18% of the entry price; exit via longshot bid better in 42 of 58; median round trip at the best routes -8.5% of the
price (sum of the two YES bids > 1); examples Minnesota Governor (longshot ask 0.125 vs 1 - fav bid 0.09), South Dakota Senate (0.095 vs
0.065), Illinois Governor (0.08 vs 0.05). Spread on the longshot books alone: median 6.5% of mid.
Upside: saves ~15% of the entry price on ~70% of the basket (~8-10k on an 80k basket over 2-3 turns) | P(>= 150k) effect: +5-8 points
for B-2 (cost 3% -> ~0 and better exits; B_mc_sens tc 5% vs 3% moved P150 4 points) | Ruin: the favourite book's mark rises slower
(favourite-side tilt lags), so favourite NO earns ~25% less tilt per $; exits that create NO+NO sets lock capital until settlement
End-valuation: exits that end in a NO+NO set pay 1 at settlement (+2.5c locked per set) and mark at ~sum of marks under "marked".
Legitimacy: buying and selling at posted prices; no self-trade (different books, other counterparties).
Build: a route choice in the B-2 planner: per race pick min(ask_L, 1 - bid_F) to enter, max(bid_L, 1 - ask_F) to exit (~40 lines);
set accounting already exists (nono_sets).
Screen: data-only over snapshots 02b/03 (route spread through time); live_sim cannot model two books per race yet.

### B-5. Session timing: buy in the European morning, rebalance up into the US afternoon
Mechanism: The tilt rises unevenly over the day; the cheapest time to buy cheap sides is when the drift is ~0 and the most expensive is
15-17 UTC. Schedule the B-2 entries and the A-3 flatten (buying back longshot YES = closing longshot NO) for 06-12 UTC, and any
de-risking sales for 15-18 UTC.
Data check: B_tilt.py, hourly ds since 1 Oct by UTC session: 06-12 +0.00016/h (n 12), 12-18 +0.00327/h (n 13), 18-24 +0.00121/h (n 17),
00-06 +0.00241/h (n 12); hours 15/16 +0.0056 / +0.0053 per h, hours 5-6 and 10-11 negative. Buying at 11 UTC vs 18 UTC is ~0.02 of s
on 3 Oct = 0.0095 on a 2.5c-ref longshot at 0.082 = ~11% of its price (two days of data: weak).
Upside: +3-10% of the basket per entry | P(>= 150k) effect: +2-4 points on B-2 (same as halving costs) | Ruin: none (timing only);
three days of a pattern can be noise
End-valuation: n/a.
Legitimacy: choosing when to trade.
Build: `tilt_long_buy_hours_utc` "6-12", `tilt_long_sell_hours_utc` "15-18" (~15 lines in the planner).
Screen: data-only: recompute the session profile on each new snapshot (needs >= 7 days to trust).

### B-6. Race-internal hedge: the NO+NO set as the long-tilt exit, and the tilt-neutral dispersion trade
Mechanism: The requested hedged version. Longshot YES and favourite YES of one race are a set; holding longshot YES and selling
favourite YES is 2x long the tilt, not a hedge. The real hedge is inside the race between the two BOOKS: favourite NO (lagging book) +
longshot NO (leading book) = a NO+NO set, zero tilt exposure, locked below 1 when the longshot is over-bid. As the tilt rises the
longshot book outruns the favourite book, so the set gets cheaper to build: a dispersion carry with no directional tilt risk.
Data check: B_book.py: sum of the two YES bids > 1 in most of the 58 binary longshot races (median best-route round trip -8.5% of the
longshot price, ~-0.9c per set); B_tilt.py longshot-side vs favourite-side s: 0.035/0.022 (1 Oct 12h) -> 0.137/0.102 (3 Oct 18h): the
gap widened from 0.009 to 0.035. Status: 16.8k NO+NO sets already held in 15 races (21.9k capital).
Upside: ~1-2.5% per set at settlement, ~+0.3-0.6k/month on 20-30k capital | P(>= 150k) effect: ~0 (small, but zero drawdown) | Ruin:
none (capital locked to 4 Nov)
End-valuation: pays at settlement; marked: ~flat.
Legitimacy: buying at posted prices.
Build: existing set logic; as the B-4 exit leg (~10 lines).
Screen: data-only.

### B-7. New entrants are the tilt's fuel: watch the player count
Mechanism: Each new player gets 100k of play cash and, if the leaders' path is visible, buys lottery tickets. The tilt rose fastest
when entrants arrived fastest. A falling entrant rate is the most direct sign that the demand side is drying up: cut the B-2 multiplier
when it falls.
Data check: journal "Rank N of M": M 963 (3 Oct 10:00) -> 971 -> 983 -> 989 -> 999 -> 1,020 -> 1,033 -> 1,039 (22:43): 6.0/h, 10.5/h
between 18:00 and 22:00 when the OLS tilt block rose most (+0.020 in 6 h, the largest of the 10 blocks). 76 new players x 100k = 7.6M of new
play cash in 12.7 h vs $112k of top-3 asks on all cheap sides: a 1.5% allocation by entrants would absorb all visible supply.
Upside: protects B-2's gains at the plateau | P(>= 150k) effect: +1-3 points (earlier exit in bear paths) | Ruin: none; one day of data
End-valuation: n/a.
Legitimacy: public leaderboard data.
Build: parse the existing rank line (already in the journal every 2 h) into a `players_per_hour` diagnostic; `tilt_long_min_entrants_ph`
(2) halves m after 12 h below it (~25 lines).
Screen: data-only on later snapshots (need >= 3 days of M to correlate with ds).

### B-8. Depth exhaustion as the ceiling signal
Mechanism: The tilt is carried by resting cheap-side bids. Those bids halved in shares in 30 h while the tilt kept rising: they are
being lifted (converted into positions) and not replaced at higher prices. When the visible bid stack stops refilling, the ceiling is
near; when it flips to ask-heavy, sell.
Data check: B_tilt.py, contracts with ref < 10c, top-3 levels per 6 h block: bid depth 6.54M (2 Oct 06h) -> 7.04M -> 5.21M -> 4.91M ->
4.35M -> 6.32M -> 3.75M shares (3 Oct 18h); ask 0.63-1.72M; log imbalance (50k cap) 1.14 -> 1.35 (3 Oct 06h) -> 0.76 now (lowest);
corr(imbalance, next block ds) +0.42 (n 6). B_book.py now: bids $295k / asks $112k.
Upside: protects B-2 at the turn | P(>= 150k) effect: +1-2 points; false alarms cost ~1 point | Ruin: walls can be pulled faster than
we read them (one cycle)
End-valuation: n/a.
Legitimacy: reading the public book.
Build: `tilt_long_imbalance_floor` (0.4): m x 0.5 below it, 0 below 0 (~30 lines; overlaps A-15's trigger but acts on size, not on/off).
Screen: data-only on each new snapshot.

### B-9. The mark's rule, fitted: a slow trade average (half-life ~1-4 h) that our fills barely move; where the fair-play line is
Mechanism: Knowing the rule tells us what we can legitimately do: since the mark follows OTHER players' trades and the mid with hours of
lag, our account's reported value is the book's value from ~1-2 h ago. We should never trade to move it (and cannot cheaply: our
171-share bursts move it 0.1% of the gap in a minute). The line: we may choose WHAT we hold and WHEN we sell; we never place an order,
or time a trade, whose purpose is the print (no buying our own holdings near the close, no crossing two of our own orders, no small
lifts in thin books to drag an average, no trades in the final hours of the tournament sized to the mark).
Data check: B_mark.py: lag scan, mark vs the mid L minutes earlier: median |gap| 0.637c (L 0), 0.560 (30), 0.543 (60), 0.564 (120),
0.722c (240); EWMA of the mid, half-life 60 min: 0.344c (best; 30 min 0.411, 120 min 0.372). 37,667 poll pairs: mark changed in 37%;
97.0% of changes had no fill of ours; with no fill and a static book the mark still moved 55% of the time (ageing or others' trades),
toward the mid in 77%. B_mark3.py: after our bursts (n 501, median 171 sh, 1.18c from the mark): dm / gap +1 min 0.001, +5 0.008, +15
0.025, +30 0.062, +60 0.127, +120 0.279. B_mark2.py: median step 0.016c, median 3.9 min between steps, off-grid (0% on the 0.5c grid).
Upside: n/a (it bounds what B-10..B-12 can do) | P(>= 150k) effect: n/a | Ruin: none
End-valuation: under "marked" the close value is this lagged average.
Legitimacy: this idea IS the line.
Build: none; `analysis/mark_rule.py` needs the trades table (empty in this snapshot) to pin N vs W: ask the owner to keep `trades`
recording on.
Screen: data-only (done).

### B-10. Stops and floors on liquidation value, never on the mark (the lag hides crashes)
Mechanism: A mark with a 1-4 h smoothing reports a crash late: a one-hour halving of s shows in the mark at ~30-40% after an hour.
Any kill switch (A-15, B-2's floor guard, the 0.8 backstop) computed on marks fires hours late, after the bids are gone. Conversely, in a
rising tilt the lag charges a long-tilt book about X x slope x lag on the leaderboard (accounting only, it reverses).
Data check: B_mark.py: 60-min half-life fits best; B_mark3: 13% of a move shows after 60 min, 28% after 120. Mark-lag P&L now: toward
lines +133 = 35.9k x ~0.0026/h x ~1.4 h. For a -300k X long-tilt book: -1.2k of mark while the tilt rises 0.0026/h; a crash of -0.06 s =
-18k at the bid shows as only ~-2k (B_mark3 rate, 13% at 60 min) to ~-7k (EWMA 1.5 h) in the mark after 1 h.
Upside: avoids 10k+ of slippage on a crash exit | P(>= 150k) effect: keeps B-2's P<=85 near 1-2% (the sim's guard uses prices, not the
mark) | Ruin: none
End-valuation: n/a.
Legitimacy: internal risk accounting.
Build: B-2 guard and the tilt-long kill switch read `liquidation_value` (already in status.json) (~5 lines).
Screen: data-only.

### B-11. Exit order by (bid - mark): sell the lines marked below their exit price first, hold those marked above
Mechanism: Selling a line converts its mark into cash at the bid. Lines whose mark is above the exit price lose that difference on the
leaderboard when sold; lines whose mark is below gain it. When flattening the toward book (A-3) or rotating, sell the "liquidation >
mark" lines first and sell the "mark > liquidation" lines last, when the lag has closed (hours later). Purely an ordering choice.
Data check: B_markgap.py, latest full poll (3 Oct 22:42, 119 lines): 32 lines with liquidation above the mark (selling books +207 vs the
mark: e.g. Rep Illinois Governor short 3,000, mark 0.093 vs ask 0.070: +70), 87 lines with the mark above liquidation (+845: Rep U.S.
House short 4,396: mark 0.143 vs ask 0.165: -97; Dem South Dakota Senate short 2,146: -60). Sum q x (mark - liquidation) +638 (status:
account 101,017 vs liquidation 100,310).
Upside: +0.2-0.8k per full flatten | P(>= 150k) effect: ~0.5 point | Ruin: none
End-valuation: marked only (at settlement the mark is irrelevant).
Legitimacy: choosing which of our positions to sell first; no prints.
Build: sort key in the tilt-exit / flatten path (~10 lines).
Screen: data-only.

### B-12. Under a confirmed "marked" close, hold to the close instead of selling: a free TWAP exit at the lagged mark
Mechanism: If SIG marks at the close, a position held to 00:00 is valued at the last 1-4 h average of others' trades: no spread, no
impact, no competition for the bid wall. Selling instead costs the half spread (6.5% of the price on cheap sides) plus impact. Under
"settled" the same held basket is worth only its Polymarket probability, so it must be sold (B-3).
Data check: B_book.py: spread median 6.5% of mid on ref < 10c; $118k sellable within 1c of the best bid, $289k within 2c (top-3
levels): exiting 80-150k$ costs ~4-8%. B_mc_sens: exit T-1d vs held changes nothing for "marked" except costs (2.4-9k on 80-150k);
"settled" and held: a 40k basket bought at 0.082 is ~488k shares x 0.025 = 12.2k at settlement (-28k).
Upside: +2.4-9k in the "marked" world | P(>= 150k) effect: +2-4 points if SIG answers "marked" | Ruin: catastrophic if the rule is
misread (-70% of the basket)
End-valuation: entirely dependent: only with a written answer from SIG.
Legitimacy: holding; we do not trade near the close to move the average.
Build: config (`tilt_long_exit_utc` = close) after SIG's answer.
Screen: cannot be screened (no close in the data).

### B-13. The close does not force the tilt to collapse: model the endgame as optional selling, and leave before it
Mechanism: The brief's "the tilt MUST collapse at the close" is true for the VALUE of held longshots under "settled", not for the
market price. Markets close at 00:00 UTC on 4 Nov (one hour after the first polls close); no race resolves before; the mark-chasers
have no reason to sell. A collapse needs sellers: settlement-aware players and Polymarket-anchored bots (like ours was). They can unwind
only into the bids ($118k within 1c, $289k within 2c now); if many try in the last week the cheap sides gap down. Modelled as a 35%
chance (50% bear) of an unwind toward 20-60% of s, starting 1-5 days before the close with a 24 h time constant.
Data check: B_book.py depth; B_mc.py: base s at close p5 0.062 / median 0.215 / p95 0.561, P(s_close < 0.12) 20% (bear 50%); the
endgame unwind at 70% instead of 35% moves B-2's P150 only 46.7 -> 45.6% because B-2 exits at T-7d.
Upside: n/a (it sets B-3's exit date) | P(>= 150k) effect: through B-3 | Ruin: being in the long-tilt book when settlement-aware
holders run for the same bids
End-valuation: the point: valuation-relevant s at the close = market s (marked) or 0 (settled).
Legitimacy: analysis.
Build: none.
Screen: cannot be screened.

### B-14. Capacity plan: build the 80k basket over ~48 h at <= 25% of visible asks per hour, mostly as maker
Mechanism: Visible supply on cheap sides is small: lifting 80k at once would pay 5-10% and feed the tilt we then mark ourselves against.
Build it in tranches (EU morning, B-5), favourite-NO route first (B-4), maker bids at the forward fair value (A-4) for the rest; cap our
share of each contract's visible asks. This is also the legitimacy guard: our purchases must not be what lifts the prices we are marked
at.
Data check: B_book.py: cheap sides (ref < 10c, 63 contracts) top-3 asks 1.00M sh / $112k, $59k within 1c of the best ask; bids 3.67M sh /
$295k. At 25% of visible asks per hour and refills every few hours: ~$15-30k/h feasible, 80k in 3-6 h of active buying spread over two
mornings.
Upside: keeps B-2's cost near the modelled 3% (5% costs 4 points of P150) | P(>= 150k) effect: +2-4 points vs an impatient fill |
Ruin: the plateau arrives while we are half in (that is fine: smaller loss)
End-valuation: n/a.
Legitimacy: the volume-share cap is the fair-play rule for a large book.
Build: `tilt_long_max_ask_share` (0.25), `tilt_long_build_hours` (48) (~20 lines).
Screen: live_sim with book depth from `books`.

### B-15. Static fallback: a 40k cheap-side basket held to day 14 (no planner needed)
Mechanism: If the planner cannot be built and red-teamed by 07:00, a fixed basket bought by hand-config and sold at day 14 captures most
of the logistic's rise with a simple risk profile: the basket's own leverage falls as it wins (X per $ of a 2.5c-ref longshot: 5.8 at s
0.12, 2.9 at s 0.30), which is a natural de-risking.
Data check: B_mc.py static 40k, exit T-385 h (day 14): mixed P150 30.0%, P200 3.6%, P<=85 0.8%, DD>20% 6.7% (base 31.1 / 3.1 / 0.3 /
6.0; bear 6.7 / 0.3 / 2.2 / 7.5; bull 62.6 / 9.6 / 0.0 / 7.1). Held to T-2d instead: 23.8 / 2.7 / 5.3 / 36.4.
Upside: median +31% (base) | P(>= 150k) effect: 0 -> ~30% | Ruin: no floor: a crash or a bear K takes it to ~80-90k (p5 88-90k)
End-valuation: independent (out at day 14).
Legitimacy: position-taking.
Build: config: the bot's tilt fair value set ABOVE the estimate (ref_tilt_max raised + a forward lead, A-4/A-16) and the adding factor
on for cheap sides only; or a one-shot `tilt_basket_target_usd` (~60 lines).
Screen: B_mc.py (done).

### B-16. Trend-following with a trailing stop is dominated: do not build it
Mechanism: The obvious momentum rule (full size while s is within 0.015 of its running max, out on the stop, back in on a new high, max 3
entries) whipsaws: hourly s noise (~2% of s per hour) trips a 1.5-point stop in ordinary hours, and each round trip costs ~6% of the
basket. Listed to close the door.
Data check: B_mc.py mixed: trend P150 21.1%, P200 0.7%, P<=85 1.1%, DD>20% 10.4% vs ratchet CPPI 41.9 / 12.4 / 2.0 / 6.5; a 2.5-point stop:
base 23.9% P150 but DD>20% 32.6%.
Upside: below B-2 at every risk level | P(>= 150k) effect: negative vs B-2 | Ruin: whipsaw cost
End-valuation: n/a.
Legitimacy: n/a.
Build: none.
Screen: done.

### B-17. Stage the bet on K: 1/3 now, the rest only after a 24-36 h ceiling test
Mechanism: P(>= 150k) swings 7% -> 47% on K alone, and K is learnable within a day: if the equal-weight tilt's 24 h slope is still >=
0.001/h and the OLS s reaches >= 0.15 by 5 Oct 00:00, K >= 0.25 is likely; if the slope is < 0.0005/h, it is a plateau (K ~ 0.15) and
B-2 should stay at m <= 2. Start at m = 2 (1/3 of the target), step to m = 5 on the test.
Data check: B_logistic.py: winsorised slope at the end 0.0017/h (K 0.176) vs OLS 0.0026/h (K 0.35); B_mc_sens base: K median 0.15 ->
P150 7.3%, P<=85 2.8%; 0.20 -> 19.1 / 2.1%; 0.30 -> 46.7 / 0.9%. The base forecast puts s at +1 d at 0.128-0.232 (p5-p95): the test
separates K 0.15 from 0.30 in ~24 h.
Upside: keeps most of B-2's upside, cuts the bear loss | P(>= 150k) effect: -2 to -4 points in the bull world (late scale-up), +1-2
points of P<=85 saved in the bear world | Ruin: none added
End-valuation: n/a.
Legitimacy: n/a.
Build: `tilt_long_mult_initial` (2), `tilt_long_test_utc`, `tilt_long_test_min_s` (0.15) (~20 lines).
Screen: data-only on the 4-5 Oct snapshots.

### B-18. The current toward book under the logistic model: flatten it in the European morning
Mechanism: Complement to A-3 (which says flatten): WHEN. Closing the +35.9k toward exposure means buying back cheap sides; doing it at
06-12 UTC (drift ~0) rather than 15-17 UTC (+0.0055/h) and in B-11's order saves both drift and mark.
Data check: B_mc.py V0 (current book held, marked): mixed P150 0.0%, P<=85 6.2%, DD>20% 9.7%; bull world P<=85 18.4%, median 92.2k;
base median 97.6k. Each hour of 15-17 UTC drift costs 35.9k x 0.0055 = ~200; the session gap over a day ~0.02 of s = ~700.
Upside: +0.5-1k vs flattening in the US afternoon; removes a 6% P<=85 | P(>= 150k) effect: prerequisite for B-2 | Ruin: none
End-valuation: under "settled" the toward book is worth +5-8k EV (A-14) and is given up.
Legitimacy: selling our positions.
Build: config + B-5's hours.
Screen: live_sim `_world_tilt_growth` sweep with a session profile.

---

## TOP 5 (by P(>= 150k) per unit of ruin risk)
1. **B-2 Ratchet-CPPI long the tilt (m 5, floor max(86k, 0.85 peak), cap 80k)** with **B-3 exit by day 10-14 / T-7d**: mixed prior
   P150 41.9%, P200 12.4%, P<=85 2.0%, DD>20% 6.5% (exit day 10, base world: 51.1 / 12.2 / 0.8 / 2.9). The only variant that clears the
   owner's ~40% while keeping both ruin limits.
2. **B-4 Route discount (enter via favourite NO, exit via longshot YES bid)**: 1.57c (18% of the price) cheaper in 42 of 58 races; turns
   B-2's 3-5% cost into ~0 and leaves riskless NO+NO sets on exit.
3. **B-17 Stage on K (1/3 now, scale after a 24-36 h ceiling test)**: K is the swing factor (P150 7% at K 0.15, 47% at 0.30) and a day of
   data separates the cases.
4. **B-10 Floors and kill switches on liquidation value, not the mark**: the mark shows 13% of a move after an hour; any mark-based stop
   fires after the bids are gone.
5. **B-5 + B-14 Session timing and capacity**: buy 06-12 UTC (drift +0.0002/h) not 15-17 UTC (+0.0055/h), <= 25% of visible asks per
   hour ($59k within 1c of the best ask on all cheap sides).
Free and immediate: B-11 (exit order by bid - mark, +0.2-0.8k), B-9 (the fair-play line, written down).

## Assumptions I could not check
- The tilt-world priors (K median 0.30 / 0.18 / 0.45, weights 0.5 / 0.3 / 0.2, crash 12% halving-ish in an hour, endgame 35%) are
  judgement anchored on a 54-hour series; the logistic cannot distinguish K 0.25 from 2.0 yet (but rejects K < 0.20 on the OLS scale; the
  equal-weight scale prefers 0.15-0.25). The result moves 7% -> 47% on K.
- The basket is modelled as one binary longshot at Polymarket 2.5c (price 0.025 + 0.475 s); real baskets mix multi-leg races
  (c = 1/3), favourite NO (whose book is ~25% less tilted) and idiosyncratic Polymarket moves (0.15%/h noise used).
- The mark is modelled as an EWMA (half-life 1.5 h); the true rule (last-N vs time window) needs the `trades` table, empty here.
- Costs: 3% + 2% x ($ / 50k) per trade; the visible book is top-3 levels only, so deeper walls (the 2.3M-share 0.04 wall) are not seen
  and the crash floor they provide is unmodelled.
- Our own buying (80k on $112k of visible asks) is part of the tilt: the sim treats the tilt as exogenous. The real effect is a higher
  entry price (cost) and a share of the rise that is our own (which the volume cap in B-14 is meant to keep small).
- Entrant-flow (B-7) and session (B-5) patterns rest on 13 hours and 3 days respectively.
- No position limits per account other than capital; SIG does not intervene (that is in the crash hazard).
- The rest of the account (not in the basket) is modelled at zero return and zero tilt exposure (flattened, or in NO+NO sets).
