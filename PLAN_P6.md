# PLAN_P6: second cycle (Finisher 2b, after Package 5 READY), branch `claude/finisher-package6` from the Package 5 READY commit
Targets (shift by READY's slip): research to READY + 2.5 h, implementation to READY + 4.75 h. Only what clearly passes goes in; an empty
Package 6 is fine. Worlds: pinned (live backstop), calibrated growth, news; judge = pnl_lag then pnl_liq.

## Live bottlenecks and what the 02b data says (analysis/poly_bias/P6_RESEARCH.md)
(a) Backstop pinning: reduce-only 81% of cycles since 16:26, but the churn cost -87 net (not a cost), 23k of the 27k shares added in
    reduce-only were arbitrage sets, and CASH binds first (positions 99.9k of 101.0k). The sim's pinned world over-weights the backstop.
    Candidates: `backstop_soft_frac` (built, unscreened: adding size shrinks to 0 across a band under the backstop, so the worst case stops
    growing before the cliff); the 0.85 backstop (Package 5 verdict: optional).
(b) Capital lock-up: 84 dead + 49 quiet held markets, 44k of capital, 55% of dead capital older than 12 h; every one has other traders'
    quotes on the exit side deep enough for 97-100% of the position at 0.6-0.7c spread; exiting all dead positions costs ~0.2k vs the mid.
    BUT in 85 of 133 of these markets the bot rests NO reducing quote at all, and where it does it sits 0.6-0.8c behind the best other quote.
    Research first: WHY no reducing quote (fair value None from a thin/wide book? ref_only? size plan 0? the turnover-dead path? cash clip?).
    Candidate: `reduce_always_quote` (OFF): a held market with no reducing quote from decide gets one at the better of (join the best other
    quote on the exit side, the book-based floor), size <= position, never crossing our own other side. Screen in the pinned world.
(c) Exchange mark vs fair value: a reporting gap (mark lags the mid by ~2 h, our fills move it ~0.05c each). No lever; nothing that trades
    to move a mark is acceptable. Report `liquidation_value` and `pnl_lag` as the leaderboard proxies.
(d) Pass-through after 16:26: 0.14 (15 min) / 0.23 (1 h) median, fat tail; n = 11 moves >= 2c. Keeps `ref_weight` 0.7 and `take_tilted_ref`.

## Screens (8 x 3 quiet first; passers 16 x 6 and news 6 x 6)
1. pinned world: T2.1 + `backstop_soft_frac` 0.05 vs T2.1 (the pinned base already has backstop 0.80).
2. pinned world: T2.1 + `reduce_always_quote` vs T2.1 (after the research says it is a design gap).
3. pinned world: T2.1 + both.
Pass rule as Package 5. Build only passers; red-team; `READY: Package 6`; START_HERE section; draft PR.
