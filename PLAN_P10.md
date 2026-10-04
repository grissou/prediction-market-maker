# PLAN_P10: "go crazier" (Finisher 2b, 4 Oct 07:10 -> ~17:00 UTC; branch `claude/finisher-package9` continues, READY: Package 10)

## The owner (4 Oct ~07:00 UTC)
"Clearly didn't go crazy enough with exploration. Go crazier and crazier. A market maker CAN make that money if it market-makes quickly
enough. Some alternative crazy ideas OUTSIDE the risk limit will be considered. 10 hours."
So: (1) the market-making velocity question, properly: what is the exchange's total flow, what share can a bot at the top of every book
with full size and the realtime feed capture, what does the write budget (28 batches/min x `batch_size` 10 orders) allow, what is the
P&L ceiling per day and what it takes (tighter, bigger, faster, all 237 markets, fewer deferrals) - with numbers from the snapshot;
(2) ideas outside the risk limits, priced on the P150 / P(<= 85k) frontier instead of being discarded; (3) wilder structures: the
cross-sectional tilt rotation (laggards vs leaders), the intraday tilt day-trade, funding the basket by selling the book at any price,
the settled-rule end-game, flow-following, and anything else with a mechanism and a number.

## Where we are
Package 9 READY (024d5a1, PR #11): basket planner, taker exits + set split, arb cash rule; corrected odds P150 ~15-20% (stage3c) at
~1% ruin, cash-bound (stage 2 frees ~21k in 3 h). Live: Package 8 stages 1-3 (unless the owner has moved). Data: /home/claude/snap03
(to 3 Oct 22:47) - no newer snapshot on origin.

## Phases
1. 07:10-09:30 explore (three opus explorers, unanchored, numbers from the snapshot): E = data backtests of the tilt rotation and the
   intraday day-trade (walk-forward, net of spread, capacity); F = market-making velocity ceiling (flow, capture, write budget, latency,
   deferrals; the "quick MM" design and its P&L/day); G = the wild card outside the limits (risk frontier m x floor x cap, funding at any
   price, settled end-game, flow-following, >= 15 new ideas, devil's advocate on E and F).
2. 09:30-11:30 screen: backtests on the snapshot, live_sim where a mirror exists (quoting-side changes DO have mirrors: sizes, spreads,
   write budget, batch size), the Monte Carlo for the frontier.
3. 11:30-16:00 build the passers behind OFF settings with tests + red team; 16:00-17:00 READY: Package 10 (START_HERE, deploy/package10,
   PR, the frontier table for the owner: P150 / P200 / P(<= 85k) / DD for every option inside AND outside the limits).
Rules unchanged: never touch the server / exchange, never merge, sub-agents never push, Python 3.10, commit + push every step, STATUS block.
