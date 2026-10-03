# Package 9 explorer D: the wild card and the devil's advocate

Data: /home/claude/snap03 (to 3 Oct 22:47 UTC). Scripts (read-only, sqlite/pandas/numpy, each < 10 s): `analysis/p9/D_data.py` (tick
grid, level sizes, maker-short vs taker-long the tilt, laggard/leader pair, Polymarket lead/lag, own impact, exit capacity through
time, settled-rule loss, 3-leg and control markets, entrants, mark vs mid), `D_flow.py` (what moved in the fastest-entrant block,
hourly calendar), `D_spike.py` (single-name pumps and give-back), `D_corr.py` (are the cheap sides one bet?), `D_mc.py` (the
devil's-advocate Monte Carlo: imports `B_mc.tilt_paths` unchanged, adds a greater-fool world, own impact, crash liquidity, a
liquidation-value guard, exit failure under "settled", transient basket noise, deployment delay and build pace, and the staged and
hedged variants). Outputs are in the scratchpad (`D_data.out`, `D_mc.out`). A-1..A-19, B-1..B-18 and C-1..C-18 are not repeated;
where I touch them I add a mechanism or a number.

Priors used below: **mixed** = B's (base 0.5 / bear 0.3 / bull 0.2); **D-prior** = base 0.35 / bear 0.3 / bull 0.15 / **greater
fool 0.2** (K ~ U(0.12, 0.16): the plateau is now, then demand decays to 40-80% of s over 5-15 days; crash 20%).
"Realistic" in D_mc = own impact (our basket lifts the cross-section by ds 0.010 per 80k, paid on the way in and again on the way
out) + 10% extra slippage on any sale within 6 h of a crash hour + the floor guard on liquidation value (B-10), not on the mark.

## Headline findings (read these first)
1. **The consensus plan's 42% is mostly earned in the first 24-48 hours, and it assumes the full 80k basket is bought at 3 Oct
   23:00 at s 0.12.** That is the attack that hurts most. B_mc's logistic puts the inflection (s = K/2 ~0.15) now, so the drift is
   largest right now. In D_mc, same plan (reverse-staged, exit day 14), mixed prior: bought at once at t0 **P150 43.8%**; built over
   12 h **36.8%**; over 24 h **30.0%**; over 48 h (B-14's pace) **17.8%**; **deployed 10 h late (s ~0.14) and built over 12 h:
   26.7% / P200 0.5%** (D-prior 19.7%). P200 collapses from 12.5% to under 1% with any realistic build. A package that ships at
   07:00 and fills over a day is a ~25-30% plan, not a 42% plan. Speed of entry is the single biggest lever left (D-23).
2. **The downside is well-contained; the upside is the problem.** Even in the greater-fool world (K 0.12-0.16, decay), the ratchet
   floor holds: B-2 realistic P(<= 85k) 5.3%, median 90.9k; reverse-staged 2.4-2.6%. The floor gap when the guard fires is median
   5-6k and **p90 ~19-24k below the floor** (crash inside an hour); the worst bear path ended at 58k. So the plan is not a ruin
   machine; it is a ~1-in-3 shot whose odds are set by K and by how fast we get in.
3. **Reverse staging beats forward staging.** Starting at m 2 and scaling to m 5 after a 36 h test (B-17) gives up the front-loaded
   drift: P150 17% (mixed). Starting at m 5 and **cutting to m 1.5 if the 36 h test fails** (s >= 0.15 and rising) keeps P150
   (43.8% vs 40.6% for B-2 realistic) and **halves P(<= 85k) (1.0% vs 2.0%) and DD > 20% (4.3% vs 7.4%)**. The test passes in 83%
   of base paths, 51% of bear, 95% of bull, 7% of greater-fool paths: it is a good K detector.
4. **Hedging with the existing short-tilt book is a rounding error.** The toward book is 35.9k of tilt exposure; an 80k basket of
   8c longshots is ~460k. Keeping it as a hedge changes P150 by +0.5 points and DD by -0.3 (D_mc hedge runs): flatten it for the
   CAPITAL, not for risk. The only real hedges are size, the floor and time.
5. **Being the liquidity provider to the tilt loses ~27% of the price per day; being a taker long earns +19% per day after the
   round-trip spread** (D_data #3, 20.7k contract-bins on cheap sides): maker-short at the ask, valued at the mid 24 h later,
   -27.1%; taker-long ask -> bid +18.8% (-7.7% at +6 h: the spread). The maker-short side becomes right only on the plateau (D-6).
6. **Laggards beat leaders by +29 points over 24 h** (long bottom-quartile s_i, short top-quartile, 93% of 30 overlapping starts):
   build the basket from laggards, and if any short-tilt legs are kept, keep them in the leaders (D-8).
7. **The tilt is not universal: party-name longshots run, independents and the Senate control market do not.** In the fastest-entrant
   block (3 Oct 18:00-22:45, ~10 entrants/h) cheap sides < 3c rose +4.7%/h (vs +3.0%/h earlier), independents fell -2.1%/h, and
   Dem U.S. Senate trades ABOVE Polymarket (0.6725 vs 0.645, s_i -0.19) while Rep U.S. House is tilted hard (0.1675 vs 0.065, s_i
   +0.24). The crowd buys party names in red/blue states: Dem South Dakota Senate (Polymarket 0.15%) went 0.0325 -> 0.0925 in 4.75 h.
8. **The close is 7 pm Eastern (DST ends 1 Nov):** markets close at 00:00 UTC 4 Nov = 19:00 EST 3 Nov, the first big poll-closing
   wave (GA, VA, VT, SC, most of FL and IN/KY west). Only eastern Kentucky/Indiana counties report raw votes before the close
   (23:00-24:00 UTC): KY Senate and KY-04 are the only markets with real outcome information inside the tournament (D-12).

---

## Part 1: go wide

### D-1. Speed is the edge: a config-only day-0 tranche before the package ships
Mechanism: The model's drift is largest now (inflection near s 0.15) and the plan's P150 decays ~1 point per hour of delay in the
first day (43.8% at t0 -> 33.6% bought 10 h later, mixed). Whatever can be done with live hot-toggles should start the long-tilt book at the owner's first wake-up, before the planner
exists: stop the tilt-short takes (C-1), split the NO+NO sets by selling only the longshot-NO legs (C-9: frees 18.4k and leaves +6.8k
of favourite-NO long tilt), raise `ref_tilt_max` to the estimate (A-16) so the market maker stops leaning short.
Data check: D_mc.py: reverse-staged plan mixed P150 bought at t0 43.8% / +10 h at once 33.6% / +10 h built 12 h 26.7%; +4 h built 4 h
(m 6 cap 90k) 40.8%. C_carousel: set split frees 18,443.
Upside: +7 to +14 points of P150 vs a 24 h later start | P(>= 150k) effect: the largest single timing effect in my angle | Ruin: none
beyond the plan's own (the tranche is inside the CPPI budget at m 5 x a 15k cushion)
End-valuation: independent (the exit is the plan's).
Legitimacy: ordinary position changes at posted prices.
Build: config only (owner hot-toggles) + a manual set split with `reduce_no_as_sell`.
Screen: D_mc delay/ramp rows (done); nothing else can be screened in time.

### D-2. Weekend and US-afternoon entrants: buy before the US wakes, never during 15-22 UTC
Mechanism: The tilt is fed by arrivals. Entrants per hour on 3 Oct (Saturday): 963 -> 971 -> 983 -> 989 -> 999 -> 1,020 -> 1,033 ->
1,039: 4/h 10-12 UTC, 6/h 12-16, 5/h 16-18, **10.5/h 18-20, 6.5/h 20-22** (US lunch-to-evening). Cheap sides < 3c rose +4.7%/h in the
fast block vs +3.0%/h. Sunday 4 Oct (NFL day, US weekend) and the weekday US evenings should repeat; any build or top-up goes in
00-12 UTC, any de-risking sale into 18-22 UTC.
Data check: D_data #9 (rank lines), D_flow.py (group moves by block); B-5 session drift (06-12 UTC +0.0002/h, 12-18 +0.0033/h).
Upside: +3-10% of the basket per entry (same lever as B-5, a second data source) | P(>= 150k) effect: +1-3 points | Ruin: three days
of a pattern
End-valuation: n/a.
Legitimacy: choosing when to trade.
Build: B-5's `tilt_long_buy_hours_utc`; a `players_per_hour` parse (B-7).
Screen: data-only on each new snapshot.

### D-3. Polymarket lead/lag: the tournament ignores ~70% of Polymarket news, and leads nothing
Mechanism: If the tournament led Polymarket we could trade Polymarket's moves in advance; it does not. If it followed, Polymarket jumps
would be takes; C-2 found they are not under the mark. The useful fact is the size of the ignored part: under "settled at the
outcome", each Polymarket jump leaves ~70% of its value on the table in the tournament book for several hours.
Data check: D_data #5 (10-min bins, all contracts): corr(dMid_t, dRef_t-k) +0.028 / +0.013 / -0.003 / -0.005 / +0.001 at k 0/1/3/6/18;
reverse direction +0.028 / +0.010 / -0.003 / +0.011 / +0.012 (no lead either way). 23 Polymarket jumps >= 3c: tournament moved 19% of
the jump at once, 38% at +10 min, 25% at +1 h, 33% at +6 h.
Upside: under "settled" only: ~8 jumps/day x 0.7 x >= 3c on a few hundred shares = ~50-150/day | P(>= 150k) effect: ~0 | Ruin: none
End-valuation: settled only (marked: worth nothing, C-2).
Legitimacy: trading on public information.
Build: none now; part of A-7's late flip if SIG answers "settled".
Screen: data-only (done).

### D-4. Polymarket's own longshot bias: "settled at Polymarket odds" overstates what a held longshot is worth
Mechanism: Every settled-rule number in A, B, C and here values a held longshot at its Polymarket price. Prediction markets themselves
over-price longshots (the classic favourite-longshot bias: sub-5c contracts tend to win less often than priced). So the settled-rule
loss of a failed exit is WORSE than modelled, and toward-Polymarket NO on longshots (A-7) is better than modelled.
Data check: not checkable offline: needs resolved Polymarket history. Size: if true 2.5c contracts win ~1.5%, the settled value of the
D-17 basket falls from 0.356 to ~0.21 per $.
Upside: sharpens A-7 (+20-40% of its edge) | P(>= 150k) effect: ~0 | Ruin: raises the cost of D-17's failure mode
End-valuation: settled only.
Legitimacy: analysis.
Build: none.
Screen: cannot be screened offline.

### D-5. Tick size 0.5c: on a 2.5-6c longshot one tick is 8-20% of the price; buy the route, not the tick
Mechanism: 100% of quotes sit on a 0.5c grid, 50% on 1c; the floor is 0.005. On the cheapest sides the half spread alone is 4-10% of
the price, and the bid queue is 2.6x the ask queue in shares, so joining the bid to buy as a maker almost never fills in a rising tilt.
Implication for the build: take asks (the A-4 maker lead cannot fill fast enough, D-1) or use B-4's favourite-NO route, whose 0.5c tick
is ~0.6% of the price (it hits the favourite's bid, a deep book).
Data check: D_data #1: on 0.005 grid 1.000, on 0.01 grid 0.502, min 0.005; B_book: cheap-side bids 3.67M sh vs asks 1.00M sh.
Upside: avoids a maker build that would take days (D_mc: a 48 h build halves P150) | P(>= 150k) effect: +5-10 points vs a maker-only
build | Ruin: none
End-valuation: n/a.
Legitimacy: posted prices.
Build: route choice in the planner (B-4) + IOC takes; ~0 extra lines.
Screen: data-only.

### D-6. Be the liquidity provider to the tilt only AFTER the plateau test fails
Mechanism: Posting asks on longshot YES above the tilt (short the tilt as a maker at a premium) is a bet on the plateau. While the tilt
rises it loses ~27% of the price per day; on the plateau it earns the half spread (3-10% of the price) per turn from entrants who still
buy. So it is the natural SECOND phase, not the first: when the 36 h test fails (D-22) or B-8's depth signal flips, swap the basket
for resting asks on the same contracts at +1-2 ticks.
Data check: D_data #3 (cheap sides, 2-3 Oct entries): maker-short at the ask, valued at the mid later: -2.1% at +6 h, -27.1% at +24 h;
taker-long ask -> bid -7.7% at +6 h, +18.8% at +24 h; entry day 2 Oct (the slow day): long +6 h -2.0% (a plateau day pays the maker).
Upside: on a plateau: ~3-6% of the price per turn, maybe 0.3-1k/day on 20-40k of quotes | P(>= 150k) effect: ~0 (it protects the
greater-fool world: +1-2k) | Ruin: a renewed leg up after we flip (stop it at the next day's high)
End-valuation: short longshot YES is worth more under "settled" (A-7): this is also the late-flip vehicle.
Legitimacy: ordinary quoting.
Build: a `tilt_long_phase` state in the planner (`long` / `maker_short`) (~40 lines).
Screen: live_sim `_world_tilt_growth` 0 vs > 0.

### D-7. Spike harvest: resting take-profit asks on every basket line at ~1.5x entry
Mechanism: Single names pump: entrants pile into one lottery ticket at a time. Selling into the spike with a resting ask costs no writes
per cycle, catches the move we cannot see in a 60-s cycle, and recycles the line ~9% cheaper later. It is also a natural CPPI trim.
Data check: D_spike.py: 61 spikes (mid >= 1.4x within 6 h) on 56 of 64 cheap contracts in 55 h; median peak 1.58x; by +24 h the mid gave
back a median 27% of the rise (44% gave back > 30%); selling at the peak vs holding to +24 h: +9.1% of the peak price (median). Example:
Dem South Dakota Senate 0.0325 -> 0.0925 in 4.75 h.
Upside: +2-5% of the basket per month on top of the drift | P(>= 150k) effect: +1-3 points | Ruin: selling a line that keeps running
(capped by re-buy on the next rebalance)
End-valuation: n/a (realised).
Legitimacy: resting orders at prices we are willing to sell at; never placed to move a price.
Build: `tilt_long_tp_mult` (1.5), `tilt_long_tp_share` (0.5 of the line) (~30 lines in the planner).
Screen: data-only replay over snapshots (done for 1-3 Oct).

### D-8. Laggard-long / leader-short: choose WHICH short-tilt legs to keep
Mechanism: Cross-sectional catch-up is strong and persistent: contracts whose own s_i is lowest catch up. The pair (long laggards, short
leaders) is tilt-neutral and still won. New legs cost capital (a short longshot via NO costs ~0.9 per share), so use the book we
already hold: when flattening the 35.9k toward book (A-3), sell the longshot-NO legs in the LAGGARD contracts first and keep the
ones in the leaders; build the long basket from laggards (A-5).
Data check: D_data #4 (quartiles of s_i every 6 h, returns to +24 h, cheap sides): laggards +59.2%, leaders +30.5%, long-lag/short-lead
+28.7% (sd 24.7%, positive in 93% of 30 overlapping starts, ~3 independent days).
Upside: +3-8% of the account per month as an overlay | P(>= 150k) effect: +1-3 points | Ruin: the leaders keep leading (their s_i is
high because entrants love them: D-7's names)
End-valuation: n/a if out by the plan's exit.
Legitimacy: position-taking.
Build: sort keys in the flatten (A-11) and basket (A-5) paths (~15 lines).
Screen: data-only on snapshots 02b/03 (done for 03).

### D-9. The tilt is a party-name tilt: drop independents and control markets from the basket
Mechanism: Entrants buy "Dem in a red state / Rep in a blue state", not independents, and the deep control markets are anchored. A
basket that includes them carries dead weight (or anti-tilt). In 3-leg races the party longshot is the fat leg: Rep Rhode Island
Governor 0.14 vs Polymarket 0.0175 (s_i 0.39 on c 1/3); Dem Montana Senate 0.115 vs 0.004 (0.34); but Ind Rhode Island Governor 0.025
vs 0.009 (0.05) and Ind Montana Senate 0.0675 BELOW its Polymarket 0.0825.
Data check: D_flow.py, 3 Oct 18:00-22:45 vs 10:00-18:00: cheap < 3c +4.73 vs +3.03%/h (35 contracts); 3-10c +1.70 vs +0.70%/h;
independents -2.06 vs +2.53%/h (3); control +0.92 vs -0.28%/h; favourites -0.07 vs -0.12%/h. D_data #8: Dem U.S. Senate s_i -0.19,
Rep U.S. Senate -0.10, Rep U.S. House +0.24.
Upside: +5-10% basket efficiency | P(>= 150k) effect: +1-2 points | Ruin: none
End-valuation: n/a.
Legitimacy: choice of holdings.
Build: an exclusion list in the planner (~5 lines).
Screen: data-only.

### D-10. Leverage per dollar: the cheapest party longshots carry 3x the tilt per $ of the dearest
Mechanism: The CPPI budget is in dollars; the bet is in s. dP/ds per $ = (c - ref) / price: put the basket's dollars where that ratio
is highest (the ultra-cheap party longshots in binary races), subject to D-9 and the volume cap. Same cushion, ~2x the s exposure,
so the plan can run at a LOWER multiplier for the same P150 (less gap risk).
Data check: D_data #7: across 63 cheap sides dP/ds per $ median 4.3, top-10 mean 9.1, bottom-10 2.7 (A-13 found the same idea; the
new number is the 3.4x spread and its use to cut m rather than raise exposure).
Upside: same P150 at m ~3 instead of 5 (fewer floor gaps) | P(>= 150k) effect: +2-4 points at equal ruin | Ruin: thinner books (the
cheapest names have the smallest asks); single-name pumps reverse (D-7)
End-valuation: under "settled" the cheapest names lose the most (ref/price 0.01-0.1).
Legitimacy: position choice.
Build: weight by (c - ref)/price in the planner (~10 lines).
Screen: B_mc with a per-$ exposure multiplier (not done: the sim is single-contract).

### D-11. The write budget if quoting stopped: 40k writes/day for exits, refills and the carousel
Mechanism: At 0 cash the market maker earns ~150/day (C-13). Its ~24 writes/min would buy: (a) a crash exit of the whole basket in
~7 min (63 lines x ~3 levels = ~190 IOC writes; urgent writes are capped at 20/cycle); (b) ask-refill sweeps on the basket's names
during the build (every refill below the forward fair value is lifted within one cycle); (c) the C-4/C-5 carousel. Keep quoting only
on the 20-30 contracts where it is tilt-neutral and capital-free.
Data check: journal: 8 "urgent writes capped at 20/cycle" deferrals and 7 WRITE_BUDGET_WAIT refusals in 26 h (budget binds a few
times a day); C_rivals/C_mm: 3 Oct maker capture +181 at 0 cash.
Upside: protects the exit (worth ~10% slippage on a crash exit, D_mc crash-slip rows) | P(>= 150k) effect: +0.5-1 point | Ruin: none
End-valuation: n/a.
Legitimacy: the 28/min limit is respected by construction.
Build: `quote_enabled_eids` allow-list + writes reserved for the planner (`planner_writes_per_min` 12) (~30 lines).
Screen: live_sim writes_pm field.

### D-12. The close is at 19:00 EST: only eastern Kentucky reports before it
Mechanism: DST ends 1 Nov 2026, so 4 Nov 00:00 UTC = 3 Nov 19:00 EST: the market closes exactly at the first big poll-closing wave.
Between 23:00 and 24:00 UTC, eastern Kentucky (and Indiana, no markets) counties close at 18:00 local and report raw counts; nothing
else resolves. Under "settled", Kentucky Senate (Rep 0.95-0.965, Polymarket 0.965; Dem 0.0575 vs 0.027) and KY-04 (Rep 0.92 vs
0.963) are the only markets where real votes arrive inside the tournament; buy the called side on public counts. Under "marked",
the last hour is noise and the mark lag hides it.
Data check: D_data latest prices for KY markets (above); the time arithmetic is calendar fact, not data.
Upside: under "settled": ~5c on a few thousand shares (~200-500) | P(>= 150k) effect: 0 | Ruin: none
End-valuation: settled only.
Legitimacy: trading on public results is what a prediction market is for.
Build: manual (owner at the close) or nothing.
Screen: cannot be screened.

### D-13. End-game playbook under each rule (T-24 h to T-0)
Mechanism: MARKED: rank-chasers will lift their own longshots in the last day (a mark contest); we must not join (fair play) but can SELL
into it: resting asks on whatever basket remains (D-7) are our exit liquidity, and the exchange's 1-4 h mark lag means a final-hour
pump barely shows (B-9: 13% after an hour). SETTLED: settlement-aware players sell longshots from ~T-5 d (B-13), so we are out by day
14 (D-22) and, if A-7's flip is on, buy toward-Polymarket at the top of the tilt in the last 48 h. UNKNOWN (the likely case): be out
of the basket by day 14 and hold only sets + the A-7 sleeve sized to lose < 5k if the rule goes the other way.
Data check: not checkable offline (no close in the data). Mark-lag numbers from B_mark3 (13% at +60 min, 28% at +120 min).
Upside: avoids the 51-64k settled-rule loss (D-17) | P(>= 150k) effect: protects the paths that reach 150k by day 14 | Ruin: none
End-valuation: this is the end-valuation plan.
Legitimacy: no trade near the close is sized to or timed for a mark.
Build: config dates.
Screen: cannot be screened.

### D-14. Smart Score: worthless for rank, cheap to improve, and a recruiting signal
Mechanism: Only 457 of 1,039 players have a Smart Score; we are 334th (9.9) and flat all day. If it is win rate x ROI on closed
positions, every dissolved carousel set (C-4/C-5) and every take-profit (D-7) is a closed winning trade, and the basket's exit by day
14 closes ~60 lines at a profit in most paths. Nothing to build; just do not churn losers to "close" them.
Data check: rank lines 3 Oct 10:00-22:43: "Smart Score 9.9 (rank 334 of 457)" unchanged in all 8.
Upside: none in money | P(>= 150k) effect: 0 | Ruin: none
End-valuation: n/a.
Legitimacy: n/a.
Build: none.
Screen: n/a.

### D-15. The 200-share fair-value filter hides 22% of top-of-book levels: lift the small cheap asks
Mechanism: `fv_min_depth` 200 (mm_bot.py:286) skips levels until 200 shares accumulate (anti-spoofing for FAIR VALUE). For the basket
build the small top asks are real, cheap liquidity at the best price: lift them with IOC before the 200-share level behind them.
Data check: D_data #1: 19.3% of all book levels and 22.5% of level-0 levels are < 200 shares; 8.3% < 50 shares; median level 1,114.
Upside: ~1 tick (8-20% of the price on the cheapest sides) on ~5% of the basket's volume: ~0.5-1% of the basket | P(>= 150k) effect:
+0.5 point | Ruin: none (we lift, we do not post)
End-valuation: n/a.
Legitimacy: taking posted asks; we never post small orders to move anything.
Build: the planner's IOC ladder reads raw levels, not fv-filtered ones (~10 lines).
Screen: data-only.

## Part 2: devil's advocate on the consensus plan (B-2 ratchet CPPI, m 5, floor max(86k, 0.85 peak), cap 80k, exit T-7d)

### D-16. Attack: the greater fool (K 0.12-0.16, demand already running out)
Mechanism: Bid depth on cheap sides fell 6.5M -> 3.75M shares in 30 h (B-8), the equal-weight tilt already bends (K 0.176, B-1), and
the entrant rate is ~6/h out of a pool that will shrink. If the plateau is now, we are the last buyer at 80k.
Data check: D_mc.py greater-fool world: B-2 as B_mc P150 0.0%, P<=85 6.7%, median 88.1k; B-2 realistic (liquidation guard) 0.0 / 5.3% /
90.9k; reverse-staged (D-22) 0.0 / 2.6% / 94.5k (the 36 h test passes in only 7% of these paths and cuts m to 1.5). With a 20% weight
on this world (D-prior) B-2 drops from 42.1% to 31.4% P150.
Upside: n/a | P(>= 150k) effect: -10 points per 20% of belief in the plateau | Ruin: ~5-7% P(<= 85k) in that world without the test
End-valuation: n/a (the loss is realised before the exit).
Legitimacy: n/a.
Build: D-22's test.
Screen: D_mc (done).

### D-17. Attack: SIG settles at the outcome and we fail to exit
Mechanism: If the basket is still held at the close (bot outage, rule change, frozen book, a late close-date change), a settled
valuation pays Polymarket odds: the basket keeps ~1/3 of its cost now and ~1/5 if bought at s 0.3.
Data check: D_data #7: equal-$ basket of all 63 cheap sides, ref/mid 0.356 per $: **80k -> 28.5k (-51.5k)**; bought at s 0.2: 21.1k;
at s 0.3: **16.0k (-64k)**. D_mc 5% exit-failure rows: P150 -0.1 to -0.3 points (marked) / -1.8 to -2.5 points by world (settled) because most paths have exited
or shrunk by then; the account in a failed path is ~50k (the worst outcome in the whole analysis).
Upside: n/a | P(>= 150k) effect: -2 points at 5% failure | Ruin: a failed exit under "settled" is the one scenario that ends below 60k
End-valuation: the entire attack.
Legitimacy: n/a.
Build: a hard date stop that sells regardless of price (`tilt_long_exit_utc` day 14), a second stop at T-3 d that the bot cannot
override, and an owner check-in on day 13.
Screen: D_mc (done).

### D-18. Attack: our own buying moves the tilt, and CPPI can make that reflexive (where the fair-play line is)
Mechanism: Lifting 72% of the visible cheap-side asks moves the measured cross-section by ds +0.010 (8% of the tilt, ~4 h of recent
drift). That raises the mid, then the mark, then the CPPI cushion, which lets the planner buy more: a loop in which our own prints
fund our own adds. Buying for position is fair; adding because our own previous buying raised our value is the line.
Data check: D_data #6, latest books, 63 cheap contracts: buy 25% of top-3 asks = $27.4k, ds +0.004; 50% = $55.0k, +0.007; 75% = $83.1k,
**+0.010**; 100% = $111.6k, +0.026. Loop size: +0.010 x 0.475 / 0.082 = +5.8% on the basket = **+4.6k of self-made cushion = +23k of
further buying at m 5**. D_mc own-impact rows (impact paid twice): -0.1 points P150 at 0.010, -1 to -5 points at 0.020.
Upside: n/a | P(>= 150k) effect: -0 to -5 points | Ruin: the exit sells into our own vanished impact
End-valuation: under "marked" self-impact inflates the leaderboard value until the mark catches up: we must not count it.
Legitimacy: the rule: (1) the cushion is computed on liquidation value with our own last-24 h impact removed (value our lines at the
pre-trade mid for the shares we bought in the last 24 h); (2) per contract <= 25% of visible asks per hour (B-14) except the first
build; (3) no adds in the last 7 days; (4) no buying of a line that is in a take-profit (D-7).
Build: `tilt_long_self_impact_haircut` (~25 lines in the planner's cushion).
Screen: D_mc impact rows (done).

### D-19. Attack: we cannot get out on a bad day, and the lagged mark hides the crash
Mechanism: Exit capacity is cyclical: bids within 1c of the best on cheap sides were $49k in the worst 2-hour block (3 Oct 10:00 UTC,
European morning), vs a $198k median. A crash halves bids and the mark shows ~13% of the move after an hour, so a mark-based guard
fires late and a liquidation-based guard fires into thin bids.
Data check: D_data #6 per 2-h block (1-3 Oct, 20 blocks): min $49,417, p10 $73,515, median $198,312, max $485,110 (worst: 3 Oct 10:00,
2 Oct 12:00, 3 Oct 02:00). D_mc: the guard fires in 9-20% of paths; the account lands median 5-6k and **p90 18-24k below the floor**;
crash slippage 30% instead of 10% costs -0.2 points P150 and +0.5 P(<= 85k) (D-22i). The single worst bear path: 58k.
Upside: n/a | P(>= 150k) effect: ~-1 point | Ruin: the floor is a soft floor: plan for 20k under it
End-valuation: n/a.
Legitimacy: n/a.
Build: B-10 (liquidation guard) + D-11 (writes reserved) + a pre-placed resting sell ladder (D-7) so a part of the exit is already in
the book when the crash starts.
Screen: D_mc (done).

### D-20. Attack: all longshots are one bet (over days, not hours)
Mechanism: Diversifying across 60 longshots removes the single-name noise but not the bet: the drift is common, the hourly wiggles
are not. B_mc's basket noise (0.15%/h) is far too small; real basket noise is ~4.6%/h, mostly transient (tick bounce), which trips
floor guards in ordinary hours.
Data check: D_corr.py (63 cheap sides, 1-3 Oct): hourly pairwise corr 0.05, PC1 19% of variance; 6-hourly corr 0.06, PC1 36%;
single-name sd 14.3%/h, equal-weight basket sd 4.6%/h (mean +1.5%/h), 6-h basket sd 6.6% (a random walk would be 11%: mean-reverting).
D_mc with 3%/h transient noise: B-2 realistic P150 40.6 -> 37.9%, P<=85 2.0 -> 2.1%; reverse-staged 43.5 -> 40.9%.
Upside: n/a | P(>= 150k) effect: -2.5 points | Ruin: whipsaw: the guard sells on a tick bounce
End-valuation: n/a.
Legitimacy: n/a.
Build: the floor guard uses a 2-hour median of liquidation value, not one cycle (~5 lines).
Screen: D_mc (done).

### D-21. Attack: capital and writes to get in (0 cash today)
Mechanism: The account is 99.7% in positions; the basket needs 75-80k of cash within hours. Sources: set split (C-9) 18.4k; flattening
the toward book (A-3/A-11) ~35-45k; dissolving the rest of the NO+NO sets (C-8) ~3k; the remaining 15-20k only from selling the
middle-of-the-book inventory. Every source is a sale into a thin book on the same day as the buys. Writes: ~150-250 IOC writes to sell,
~200 to buy, ~10-15 min of the full 28/min budget, i.e. fine if quoting is paused (D-11), a day if it is not.
Data check: status.json: capital 99.7%, NO+NO sets 21.9k; C_carousel set split 18,443; D-11 write counts.
Upside: n/a | P(>= 150k) effect: the build pace numbers in D-22 (-7 to -26 points for a 12-48 h build) | Ruin: none
End-valuation: n/a.
Legitimacy: n/a.
Build: a one-shot funding sequence in the planner: split sets -> sell longshot NO (laggards first, D-8) -> buy basket (route B-4).
Screen: D_mc ramp rows (done).

## Part 3: the version that survives the attacks

### D-22. Reverse-staged CPPI: full size at once, cut on a failed 36 h test, liquidation guard, out by day 14
Mechanism: Keep B-2's sizing (m 5, floor max(86k, 0.85 x peak), cap 80k) but (1) buy the full target at once at the first possible
moment (D-1, D-23); (2) at +36 h test the ceiling: if the OLS s < 0.15 or lower than 12 h earlier, cut m to 1.5 for good (do NOT
start small and scale up: forward staging loses the front-loaded drift); (3) floor guard on a 2-h median of liquidation value
(B-10, D-20); (4) sell everything by day 14 (18 Oct) with a hard T-3 d backstop (D-17); (5) cushion net of our own impact (D-18);
(6) resting take-profit asks (D-7). Bolder option: m 6, cap 90k.
Data check: D_mc.py (4,000 paths per world; mixed / D-prior; P150 / P200 / P<=85 / DD>20):
| variant | mixed | D-prior |
|---|---|---|
| B-2 as B_mc (mark guard, no impact) | 42.1 / 12.4 / 2.0 / 6.6 | 31.4 / 9.1 / 3.2 / 6.3 |
| B-2 realistic (impact, crash slip, liq guard) | 40.6 / 11.1 / 2.0 / 7.4 | 30.2 / 8.1 / 2.9 / 7.1 |
| B-2 realistic + 3%/h transient noise | 37.9 / 9.4 / 2.1 / 6.8 | 28.1 / 6.9 / 3.5 / 6.8 |
| forward staged m 2 -> 5 (B-17-like), cap 60k, day 14 | 17.1 / 0.0 / 0.4 / 3.2 | 12.6 / 0.0 / 0.5 / 2.7 |
| half size m 2.5 cap 40k | 14.2 / 0.0 / 0.5 / 0.9 | 10.5 / 0.0 / 0.7 / 0.9 |
| **D-22 reverse staged, day 14, bought at t0** | **43.8 / 12.5 / 1.0 / 4.3** | **32.6 / 9.1 / 1.3 / 3.8** |
| D-22 + 3%/h transient noise | 40.9 / 12.0 / 1.3 / 4.6 | 30.5 / 8.8 / 2.3 / 4.4 |
| D-22 m 6 cap 90k, at t0 | 47.9 / 18.1 / 1.2 / 4.6 | 35.8 / 13.3 / 2.3 / 4.7 |
| D-22 built over 12 h | 36.8 / 5.7 / 1.0 / 4.1 | 27.4 / 4.2 / 1.1 / 3.6 |
| D-22 built over 24 h | 30.0 / 0.9 / 0.9 / 3.9 | 22.1 / 0.7 / 1.1 / 3.4 |
| D-22 built over 48 h (B-14 pace) | 17.8 / 0.0 / 0.9 / 3.5 | 13.1 / 0.0 / 0.8 / 2.9 |
| D-22 deployed +10 h, bought at once | 33.6 / 2.2 / 1.2 / 4.2 | 24.9 / 1.6 / 1.7 / 3.8 |
| **D-22 deployed +10 h, built over 12 h (the realistic case)** | **26.7 / 0.5 / 1.2 / 4.0** | **19.7 / 0.4 / 1.3 / 3.5** |
| D-22 m 6 cap 90k, +10 h, built 12 h | 31.1 / 3.1 / 1.4 / 4.1 | 23.0 / 2.3 / 1.9 / 3.7 |
| D-22 m 6 cap 90k, +10 h, built 4 h | 34.6 / 5.9 / 1.5 / 4.9 | 25.7 / 4.3 / 2.0 / 4.5 |
| D-22 m 6 cap 90k, +4 h, built 4 h | 40.8 / 13.1 / 1.3 / 4.9 | 30.4 / 9.7 / 1.8 / 4.5 |
| D-22 m 8 cap 100k, +10 h, built 4 h | 35.3 / 9.6 / 1.9 / 7.0 | 26.2 / 7.1 / 3.4 / 6.7 |
| D-22 + keep the 35.9k toward book as a hedge | +0.5 / +0.3 / 0 / -0.3 vs D-22 | same |
Upside: median ~+45% in the base world if in at once; ~+20-25% realistic | P(>= 150k) effect: 20-48% depending on entry speed and K |
Ruin: P(<= 85k) 1-2%, DD > 20% 4-5%; plan for a 20k gap under the floor in a fast crash
End-valuation: independent (out by day 14), except the D-17 failure mode, which the T-3 d backstop covers.
Legitimacy: position-taking at posted prices with D-18's self-impact rule; the first build exceeds B-14's 25%/h cap by design and must
stay below ~75% of the visible asks (ds +0.010); the rest of the month obeys B-14.
Build: B-2's planner + `tilt_long_test_h` 36, `tilt_long_test_min_s` 0.15, `tilt_long_mult_on_fail` 1.5, `tilt_long_exit_utc`
2026-10-18T00:00, `tilt_long_backstop_utc` 2026-11-01T00:00, guard on a 2-h median of liquidation value (~60 lines on top of B-2).
Screen: D_mc (done); live_sim needs a logistic world, a crash knob and a build-pace knob.

### D-23. Entry within 4 hours of deployment, at most 75% of visible asks, the rest via the favourite-NO route
Mechanism: The only lever that moves the realistic case back above 40% is a fast first build. Split the first 80k: ~40k lifting
longshot asks in laggard party names (<= 75% of each book's top-3 asks, ds +0.010), ~40k via favourite NO (selling favourite YES into
the deep favourite bids, B-4: 1.57c cheaper in 42 of 58 races), funded first by the set split (C-9: 18.4k of cash, leaving ~3k of
favourite-NO legs already in the basket) and then by selling the toward book (laggard longshot NO first, D-8). Do it
in the European morning (00-12 UTC, D-2), never at 18-22 UTC.
Data check: D_mc: m 6 cap 90k, deployed +4 h and built in 4 h: 40.8 / 13.1 / 1.3 / 4.9 (mixed); +10 h built in 4 h: 34.6 / 5.9 / 1.5 / 4.9;
built over 12 h: 31.1 / 3.1. D_data #6: 75% of the visible asks = $83k.
Upside: +7-14 points of P150 vs a 12-24 h build | P(>= 150k) effect: the difference between ~25% and ~40% | Ruin: entry slippage
~6-9% (modelled at 3% + 2% x $/50k + impact)
End-valuation: n/a.
Legitimacy: a large, one-time position purchase at posted prices. It moves the cross-section by ~8% of the tilt; that is a consequence,
not the purpose, and D-18's rule stops us from counting or compounding it. If the owner judges a one-shot 80k lift too close to the
line, the 12 h build is the fallback (26.7-31%).
Build: a `tilt_long_initial_build_hours` (4) and `tilt_long_initial_ask_share` (0.75) in the planner (~15 lines).
Screen: D_mc (done).

---

## TOP 5 (by P(>= 150k) per unit of ruin risk)
1. **D-22 Reverse-staged CPPI (full size at once, cut to m 1.5 on a failed 36 h test, liquidation guard, out by day 14)**: same P150 as
   B-2 (43.8% vs 40.6% realistic, mixed), half the ruin (P<=85 1.0% vs 2.0%, DD>20 4.3% vs 7.4%); m 6 cap 90k: 47.9 / 18.1 / 1.2 / 4.6.
2. **D-1 + D-23 Speed of entry**: hot-toggles at the first wake-up, the full first build inside 4 h in the European morning. Worth
   +7 to +14 points of P150 vs a 12-24 h build; a 48 h build halves P150 (17.8%).
3. **D-18 The self-impact rule** (cushion net of our own last-24 h impact, <= 75% of visible asks on the first build, B-14 after,
   no adds in the last 7 days): our 80k is ds +0.010 of the tilt and +4.6k of self-made cushion; this keeps the plan on the right
   side of the fair-play line at a cost of < 1 point.
4. **D-7 + D-8 Spike harvest and laggard selection**: 61 single-name spikes in 55 h, median 1.58x, give-back 27% (+9.1% of the peak
   price by selling at it); laggards +59% vs leaders +31% in 24 h. +2-6 points between them, no added ruin.
5. **D-17 + D-13 The hard exit stack**: sell by day 14, an unoverridable T-3 d backstop and an owner check on day 13; a failed exit under
   "settled" turns an 80k basket into 16-28k (the only path to ~50k).
Honourable mentions: D-6 (maker-short only after the test fails), D-9 (no independents, no control markets), D-20 (2-h median guard).

## Verdict on the consensus plan: GO STAGED (reverse-staged, fast)
- As specified (B-2) the plan survives my attacks on the DOWNSIDE: realistic frictions and a 20% greater-fool weight keep P(<= 85k) at
  2-3.5% and DD > 20% at ~7%. Nothing I found makes it a ruin bet; the gap through the floor (p90 ~20k) is the honest worst case.
- It does NOT survive on the UPSIDE as stated: the 42% assumes the full 80k at 3 Oct 23:00 at s 0.12. **Realistic numbers (deploy
  ~10 h later, build in 12 h): P150 26.7% / P200 0.5% / P<=85 1.2% / DD>20 4.0% (mixed); 19.7% / 0.4% / 1.3% / 3.5% (D-prior).**
  With m 6 / cap 90k and a 4 h build: **34.6 / 5.9 / 1.5 / 4.9 (mixed), 25.7 / 4.3 / 2.0 / 4.5 (D-prior)**. If in within 4 h of now-ish:
  40.8 / 13.1 / 1.3 / 4.9.
- So: GO with D-22's reverse staging (never forward staging: m 2 first costs 25 points), m 6 / cap 90k if the owner accepts P<=85
  ~1.5-2% and DD>20 ~5%, entry as fast as the fair-play rule allows (D-23), out by 18 Oct. Tell the owner plainly: **the best
  achievable odds of 150k are ~25-35% from a morning deployment, ~40% only if the basket is in before the US afternoon on 4 Oct, and
  P(>= 200k) is single digits unless the entry is that fast.** If the 36 h test fails (s < 0.15 at ~5 Oct 12:00), 150k is gone in
  every world I modelled; protect the account and switch to D-6 and the carousel.

## Assumptions I could not check
- Everything in D_mc inherits B_mc's tilt worlds (logistic K prior, r 0.02-0.045/h, crash 12%, endgame 35%); my greater-fool world (K
  0.12-0.16 plus decay) and its 20% weight are judgement. The front-loading result (finding 1) depends on the logistic putting the
  inflection near now; a slower linear rise (0.0020/h, A's mean) would make the delay cost smaller (not run).
- Own impact is a cross-section mid shift from consuming the visible top-3 levels; the true refill rate of asks (and so whether a 4 h
  build is feasible at <= 75% of visible asks) is unknown. The books table shows only 3 levels.
- Crash liquidity is a flat 10-30% extra on sales within 6 h of a crash hour; real bids could vanish entirely for longer.
- The "settled at Polymarket odds" values assume Polymarket is calibrated; D-4 says it probably is not for longshots (worse for us).
- Laggard/leader (D-8) and spike (D-7) statistics come from ~3 days of overlapping windows (~3 independent observations for D-8).
- Entrant-rate and session patterns (D-2) rest on one Saturday; Sunday and weekdays may differ.
- The close-time arithmetic (D-12) assumes 4 Nov 00:00 UTC is the close and US DST ends 1 Nov 2026 (first Sunday of November).
- Smart Score's formula and any prize attached to it are unknown (D-14).
- Hedge rows treat the toward book's 35.9k exposure as free of capital; in reality it holds capital the basket needs (so the hedge is
  slightly negative, which is why the advice is to flatten it).
- No SIG intervention, rule change or position limits beyond capital (all inside the crash hazard).
