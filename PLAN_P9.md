# PLAN_P9: the catch-up package (Finisher 2b, overnight 3-4 Oct; branch `claude/finisher-package9` from Package 8 7b298a4)

## Objective (owner, 3 Oct 22:50 and 22:5x UTC; replaces "steady, low-variance P&L")
Reach **+50% to +100% (account 150k-200k) by 4 Nov 00:00 UTC** on a ~101k account (rank 174 of 1039 at +1%; the leader ~+600%).
Optimise **P(account >= 150k)** first, **P(>= 200k)** second, while keeping **P(account <= 85k) under ~10%** and **max drawdown
under ~20%**. Rank every strategy and every combination by these three numbers, not by mean P&L. Sizing: the smallest risk that gets
P(>= 150k) above ~40%; say plainly if no legitimate strategy can reach that, and what the best achievable odds are.
Fair play is absolute: no order placed to move a price, no self-trades, no prints made to move a mark, write limit respected.
"Aggressive" = bigger, more concentrated positive-EV bets and faster capital turnover.

## Where we are (3 Oct 22:47, snapshot `ops-snapshot-2026-10-03`, extracted to /home/claude/snap03 with `md.sqlite` built)
Package 8 live since 22:42:51, all three stages on since 22:44:54. Live overrides: tilt on at max 0.11 (tilt_s 0.110, diag slope
0.106 / median 0.100), headline on, reduce_no_as_sell, no_set_aware_bids, pair follow-up, pair_no_unwind_max_cost 0.02, arb_enabled
FALSE (one-legged leftovers at 0 cash), capital_ceiling_adding_size_factor 0, backstop 0.8, writes 28/min. Account 101,017, liquidation
100,310, capital 99.7%, tilt exposure 35.9k (~356 marked per point), NO+NO sets 15 races / 16.8k sets / 21.9k capital, worst case 79.2k,
realised +1,947. Afternoon: -661 re-marking as the tilt rose 0.092 -> 0.110. The leaderboard uses the exchange's mark (a lagged
trade-price average, ~1.4k below the book mid). SIG's end-valuation rule (settled at the outcome vs marked) is UNKNOWN: size both.

## Phases (targets; correctness first; something pushed by 07:00)
1. **22:55-02:30 explore.** 4 opus sub-agents, unanchored, >= 60 raw ideas in `analysis/p9/ideas_*.md`: mechanism, data check (a
   number from the snapshot), upside per month as % of the account, contribution to P(>= 150k), ruin risk, legitimacy, build cost,
   how to screen. Angles: (A) how +600% is possible here and conviction bets / sizing for a probability-of-target objective, with the
   end-valuation uncertainty; (B) the tilt as a trade and the exchange's mark (timing of exits vs the averaging); (C) rival bots, stale
   quotes, takes, riskless sets, arbitrage with a cash rule, capital allocation; (D) the wild card: calendar, write budget, Polymarket
   lead/lag, microstructure, anything.
2. **02:30-04:30 screen.** `tests/live_sim.py` in the live-pinned world (0 cash, tilt 0.11 + 0.0036/h), judge pnl_lag then pnl_liq, AND
   the distribution to 4 Nov: P(>= 150k / 200k / 400k), P(<= 85k), max drawdown. Data-only ideas against snapshots 02b / 03. Ranked
   table in `analysis/p9/RANKED.md`.
3. **04:30-07:00 build.** Top 3-5 behind their own OFF settings, tests, red team, a kill-switch per aggressive feature (off automatically
   when the account falls X% from its peak). "READY: Package 9": START_HERE section, draft PR, `deploy/package9/` staged files in the
   owner's live format (start from the live file; never reset `capital_ceiling_adding_size_factor`, `arb_enabled` or `ref_tilt_headline`),
   a one-page catch-up plan (what to switch on, in what order, combined odds for 150k / 200k / <= 85k, the kill-switch level, worst case).
   If nothing clearly passes: ship the research and the ranked table.

## Morning answers owed
(1) when to resume adding (factor 0.95 rule from Package 8); (2) whether `arb_enabled` comes back and under what cash rule; (3) whether
the 0.8 worst-case backstop and the 30% risk cap are right for this objective, what to set them to, with ruin numbers.

## Rules
Never touch the server or the exchange API; never merge; sub-agents never push; Python 3.10; commit and push after every step with the
STATUS block updated; no long sleeps; everything in the repo or the scratchpad. One CPU in this sandbox: data checks are sqlite / pandas
on the snapshot, simulations are budgeted and cached.
