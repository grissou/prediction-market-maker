# Package 10, explorer F: how fast can a market maker make money here? Flow, capture, the write budget, and "quick MM"

Data: `/home/claude/snap03` only (to 3 Oct 22:47). Scripts: `analysis/p10/F_journal.py` (per-hour quote / defer / take / reject /
fill counts from the journal), `F_flow.py` (time at the top of the book, spreads, maker capture and mark-outs, placements; writes a
scratch pickle), `F_flow2.py` (the exchange's print rate from mark steps, quote age at fill, P&L concentration, per-market P&L vs time
at the top vs time quoted, top-of-book depth), `F_sides.py` (mark-outs by price bucket x side, fill sizes, hour of day). Builds on
C-13 (maker capture 0.49c/share at the fill, 0.45c after 1 h), C-15 (rivals step in front of us), C_rivals and IDEAS_ROUND3 W1-W4.

## Headline numbers (read these first)
1. **Exchange flow is about 3-5M shares a day (roughly $1.5-2M), and we took 8% of it on our best day.** Other players' prints move
   the exchange mark 4.6 (median) to 6.1 (mean) times per market-hour when our position does not change (F_flow2 A, 204 markets,
   35 h, polled every 129 s). That gives about 26-35k prints a day. At our average maker fill (130 shares) that is about 3.5-4.5M shares a day.
   The best price changes at least 10.7 times per market-hour (sampled every 70 s). Our maker volume was 279k shares on 1 Oct (8 h,
   the opening), 313k on 2 Oct (about 8% of the flow) and 33k on 3 Oct (under 1%, with no cash).
2. **We are at the top of the book only 4-6% of the time.** That is the share of market-side cycles where our quote is the best price
   (F_flow 1: bid 4.2-5.3%, ask 5.3-6.2%). We quote 27-46% of market-sides at all. The median spread is now **0.5c** (1c on 1-2 Oct),
   and 86% of markets are at 1c or tighter (51% on 1 Oct). So the half-spread is 0.25c, and our `min_edge` of 1c against fair value
   keeps us behind the best price unless the book has moved off fair value.
3. **Being at the top is not where the money is.** Across markets on 2 Oct, maker P&L at the 1-h mark-out is **-0.09 $ per side-hour
   at the top** against **+0.44 $ per side-hour quoted but not at the top** (OLS over 237 markets, F_flow2 D). The edge comes from
   resting quotes that the flow reaches, not from stepping in front. Fresh quotes earn less: 3 Oct fills on quotes under 30 s old
   earned 0.42c/share at 1 h and 0.38c at 6 h, against 0.55-0.57c at 2-30 min (F_flow2 B, n 533). In W3, headline quotes under 2 min
   old earned -0.20c. **Reacting faster is not the lever.**
4. **Today the binding constraint is cash, then wasted writes. The write limit itself is not.** In the 3 Oct journal (08:00-22:47):
   - 4,139 quote orders were accepted and **5,188 were refused for "Insufficient available funds"**.
   - There were 2,691 TAKE attempts. Only 256 take orders reached the notes, and 174 of them filled (24.8k shares).
   - There were 888 arbitrage orders.
   - Taking a reprice as one cancel plus a batch share, takes and refused orders used about 50-60% of the 28 writes/min.
   - In the take hours (09-14 and 18-19 UTC) the bot deferred a median of 50-60 of 52-61 planned changes per cycle. In the take-free
     hours (15-17 and 20-22) it deferred 1-9 of 1-18 (F_journal).
   - The "150 of 150 deferred" lines come from the 08:26 restart, when book priming had the writes.
   - A focused market maker does not need more writes than 28/min. Following every top change in the 60 markets that earn costs about
     13 writes/min.
5. **The flow is two-way in the middle and one-way in the tails.** Of our maker fills, 44% by count (52% by shares) were "toward
   Polymarket", the side that short-tilt flow trades against, and both directions earned at 1 h. At 6 h the tails split by the drift
   (F_sides):
   - The longshot (<5c) asks lost 2.03c/share and the bids made 1.66c.
   - The 85-95c bids lost 0.29c and the asks made 1.68c.
   - Fills on the with-drift side (longshot bids, favourite asks) made **+1,244 on 100k shares**. Fills on the against-drift side
     made **-117 on 117k shares**.
6. **The P&L is concentrated.** The top 10 markets gave 58% of the maker 1-h P&L (1-3 Oct), the top 30 gave 83% and the top 60 gave
   100%. 44 markets were net negative (-314) (F_flow2 C). Quoting all 237 markets adds writes, not money.
7. **The verdict on 1.6k/day from market making alone: not reachable as a central case.** Our best full day with capital (2 Oct)
   made 1.0k at the 1-h mark-out (1.27k at 6 h) on 313k shares. 1.6k/day needs about 460k maker shares a day at 0.35c. That is
   1.5 times our best day and about 11-13% of all exchange flow, on spreads that halved in 3 days as rivals arrived. The honest
   "quick MM" estimate is **1.0-1.4k/day central (range 0.5-2.2k)**. It needs **15-25k of rotating cash**, which the basket also wants.
   A day like the 1 Oct opening (1.6k in 8 h) is not the steady state.

## Ideas

### F-1. Turn the TAKE rule off (or make it cost-gated) to give back about 40% of the write budget to quoting
Mechanism: Each TAKE costs up to 3 writes (cancel ours + take + leftover cancel). On 3 Oct 2,691 attempts produced 256 take orders
and 174 fills, all of them toward Polymarket (C-1: short tilt, -406 at the mark). The freed writes go to quotes that earn
3.6c per accepted order (3 Oct) or about 11c per order (2 Oct).
Data check: F_journal: takes 230-370/h in 09-14 and 18-19 UTC, with a median of 50-60 changes deferred per cycle; takes 0 in 15-17 and
20-22 UTC, with a median of 1-9 deferred. Notes (3 Oct 08:00-22:47): 256 take orders, 174 with a fill, 24.8k shares.
Upside: +0.2-0.5k/day of maker capture (2-4x more productive quote writes in the top 60 markets) + stops ~15k/day of short-tilt build
| P(>= 150k) effect: +1-3 points (carry + no tilt-short drift) | Ruin: none (it removes a risk)
End-valuation: none; spread capture is cash.
Legitimacy: fewer orders.
Build: config: `take_enabled` false (or `take_tilted_ref` true + `take_edge` 0.08, which Package 9 stage0 already proposes).
Screen: live_sim writes_pm / pnl_lag with takes on vs off; the journal's per-hour defer count live (target: median deferred < 5).

### F-2. Never place an order the exchange will refuse: the cash gate must also cover the replace path
Mechanism: On 3 Oct, 5,188 quote orders were refused for funds, more than the 4,139 accepted. Each refused order had usually been
preceded by a cancel of the resting quote. So a reprice that could not be funded left the side empty and cost 1-2 writes. Package 8's
`cash_gate_enabled` (on since 22:44) checks new orders. The point is to keep a resting quote when its replacement cannot be funded:
"reprice only if affordable, otherwise hold".
Data check: F_journal rej_funds 167-564/h in every hour before 22:44; 22 UTC still 489. Order notes: 4,139 accepted quote orders,
310 filled (7.5%).
Upside: +10-20% quote coverage at 0 cash, ~0.1-0.2k/day | P(>= 150k): +0-1 | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: in `plan_change`: if the new order's cash need > cash_gate_left, do not doom the old one (hold_side analogue), ~20 lines.
Check first whether Package 8's gate already does this: count "Insufficient" lines after 22:44 in the next snapshot.
Screen: the next snapshot's journal (rejects/h should be ~0); live_sim has no cash model.

### F-3. Quick MM = fund the quoter, not speed it up: 15-25k of rotating cash, adding factor back on
Mechanism: The 3 Oct collapse (33k maker shares, +149) against 2 Oct (313k, +1,014) is cash: adding factor 0, reduce-only in 80% of
cycles, and refused bids. Funded both-sided quoting in the top 60 markets is the only way to the 2 Oct run rate.
Data check: F_flow 2: maker shares 279k / 313k / 33k on 1 / 2 / 3 Oct; quote presence 46% of market-sides on 2 Oct, 29% on 3 Oct.
C_alloc: basket 50k + 1,050/day carry gives P150 64.3% against 43.9% at 90k + 0; at half that carry it gives 43.9% (no gain).
Upside: +0.8-1.3k/day vs today | P(>= 150k): +0 to +15 points, and only if the carry is real; negative if it starves the basket
| Ruin: inventory drift. On 3 Oct 14:00-19:18 re-marking cost -655, and that was inventory, not spread.
End-valuation: capture is cash; any inventory left over is marked.
Legitimacy: ordinary quoting.
Build: config: `capital_ceiling_adding_size_factor` 0.5 (with `_resume`), a cash budget for quoting in the capital plan (C-12).
Screen: live_sim `_start_cap` 20k vs 0. Then live for 24 h: maker $/day at the 1-h mark-out (target >= 0.8k) before cutting the basket.

### F-4. Quote only the with-drift side in the tails (longshot bids, favourite asks), both sides in the middle
Mechanism: In the tails the tilt runs one way. Takers lifting longshot asks and hitting favourite bids are tilt buyers, and they run
over our longshot asks and favourite bids. Quote longshot (< 15c) bids and favourite (> 85c) asks only, priced off the tilted fair
value. Quote both sides from 15c to 85c.
Data check: F_sides 6-h mark-outs. <5c ask -2.03c (7.3k shares) vs bid +1.66c (7.2k). 85-95c bid -0.29c vs ask +1.68c (47.5k shares,
+801). 15-85c ask +0.65c, bid -0.08c. With-drift sides +1,244 on 100k shares; against-drift sides -117 on 117k shares.
Upside: about +0.1-0.2k/day (removes the losing half of the tail fills), and it keeps the inventory long the tilt, alongside the
basket | P(>= 150k): +1 point | Ruin: a tilt reversal (never seen), then the with-drift inventory loses. It is small per market.
End-valuation: tails settle at 0/1. The with-drift side (longshot YES) loses at settlement if held: exit with the basket.
Legitimacy: one-sided quoting is ordinary.
Build: `fl_bias_enabled` exists with the OPPOSITE sense (its "bad side" is the longshot bid). New `drift_side_only` (bool), with
`drift_low` 0.15 / `drift_high` 0.85: in quote(), the against-drift side gets None. ~15 lines.
Screen: live_sim with `_world_tilt_growth` > 0: pnl_lag and tilt_exposure_end; data-only check done (F_sides).

### F-5. Quote the top 60 markets properly and drop the rest: writes go where the P&L is
Mechanism: 100% of the 1-3 Oct maker P&L came from 60 markets, and 44 markets lost money. Writes in the other 177 markets (TTL
refreshes, chases) cost budget for nothing (W1: the 64 zero-fill markets took 19% of changes).
Data check: F_flow2 C: top 10 = 1,613 of 2,777 (58%), top 30 = 2,310, top 60 = 2,791; 44 markets < 0 = -314.
Upside: about 30-40% of quote writes freed, worth +0.1-0.3k/day when re-spent in the top 60 | P(>= 150k): +0-1 | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: `market_edge_file` / a new `quote_markets_max` 60, ranked by trailing 24-h maker mark-out (not by Polymarket volume as
`update_size_plan` does today). ~30 lines.
Screen: data-only (done) + 24 h live A/B on writes/min and maker $/day.

### F-6. Size by the flow that actually reaches us: 500-1,000 shares in the top 30, not 10,000 / 2,000 / 100 by Polymarket volume
Mechanism: `update_size_plan` sizes by Polymarket volume (live trades weigh 0% in every size-plan line on 3 Oct), so the
headline markets get 10,000. Actual fills: median 75 shares, p90 395, p99 1,898. The best level holds a median 973 (bid) / 878 (ask)
shares. Quotes bigger than ~2x p90 lock cash without adding fills. Quotes at 50 (3 Oct) truncate the 10% of fills that run to 400+.
Data check: F_sides fill size quantiles; F_flow2 E top-level depth; journal "tournament trades weigh 0%".
Upside: the same fills on ~40% of the locked cash, so more markets funded at once: +0.1-0.2k/day | P(>= 150k): +0-1 | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: config `live_activity_max_weight` (raise) + a `size_cap_fill_p90_mult` 2.0 on plan_sizes (~10 lines); the size headroom is
then set by fills, not Polymarket.
Screen: live_sim (sizes have a mirror).

### F-7. Stop stepping in front: `min_edge` stays 1c, and the at-top penny war is not worth writes
Mechanism: P&L comes from quotes that are resting and get reached, not from being the best price. On 2 Oct the at-top side-hours
earned ~0, and the pennying rival (C-15: 6%/min, 80% by 0.5c) makes each top position cost a write a minute. Keep `churn_max_reprices`.
Instead of chasing, sit 1 tick behind the best in markets with a penny bot (`behind_best_size_enabled`).
Data check: F_flow2 D: OLS 1-h P&L per side-hour at the top -0.09 vs +0.44 quoted and not at the top. F_flow2 B: fills on quotes
under 30 s old earn 0.42c/0.38c at 1/6 h, against 0.57/0.18 at 2-10 min and 0.55/0.49 at 10-30 min.
Upside: saves ~2-5 writes/min; ~0 direct | P(>= 150k): 0 | Ruin: none
End-valuation: n/a. Legitimacy: ordinary.
Build: config: `behind_best_size_enabled` true, `behind_best_ticks` 1.
Screen: live_sim writes_pm and pnl_lag.

### F-8. Re-test the write limit: probe 28 -> 40/min with AIMD, with the 429 cut still in place
Mechanism: The Config history records 63 minutes at >= 40 writes/min (peaks ~60) with no 429 on 1-2 Oct. The six 429s came at 33-53 and
did not track the write rate. If the real limit is ~40-60, the self-tuning budget can find it, and a 429 already cuts the budget
to 75%.
Data check: Config comments at `writes_per_minute`; ENGINEERING_NOTES line 115 ("day one peaked near 45 cancels/min with no 429").
Upside: +40% write headroom; worth little until F-1/F-2 stop the waste; ~0-0.1k/day | P(>= 150k): 0 | Ruin: a 429 pauses everything
for 60 s (stale quotes). Mitigated by `write_budget_cut` 0.75.
End-valuation: n/a. Legitimacy: respecting the limit means backing off on a 429, which the AIMD does. Do not run it during
basket building.
Build: config: `writes_per_minute_max` 40 (`writes_per_minute` 28 start).
Screen: cannot be screened offline; live with a 429 alert.

### F-9. Two-side reprices as one per-exchange cancel-all, and refreshes left to expire
Mechanism: A reprice costs a full write per cancel, and the batch is shared. When both sides move, `whole=True` already uses one
cancel-all. Make TTL refreshes free: place the replacement (a different price, so no self-cross) and let the old order expire.
W4 measured ~6 writes/min of refresh tax at 150 resting orders.
Data check: send_changes cost = cancels + ceil(batch); IDEAS_ROUND3 W4 (~20% of 30/min). 3 Oct placements median 342/h (order
notes), so refreshes are ~1/3 of them.
Upside: ~5 writes/min | P(>= 150k): 0 | Ruin: two orders on one side for up to 3 min, which must count in the risk model's locked cash
End-valuation: n/a. Legitimacy: n/a.
Build: `order_ttl` 3600 (config, halves the refresh tax); ~25 lines for expiry-overlap.
Screen: live_sim writes_pm.

### F-10. Tilted reference + forward lead for the quoter's fair value: inventory that does not fight the drift
Mechanism: The maker's fair value should be the tilted reference moved forward by the expected drift over the holding time
(~+0.0036/h of s; 1-6 h horizon = +0.004-0.02 of s). Then the inventory the quoter builds is tilt-neutral to slightly long, and
the 6-h mark-out on toward fills (0.19-0.27c on 2-3 Oct, against 0.28-0.57c away) closes.
Data check: F_flow 2: toward fills 6-h mark-out 0.27c (2 Oct) / 0.19c (3 Oct) vs away 0.57 / 0.28c.
Upside: +0.05-0.15k/day | P(>= 150k): +0-1 | Ruin: a tilt stall leaves the quotes 1-2 ticks off (small)
End-valuation: n/a. Legitimacy: ordinary pricing.
Build: `ref_tilt_lead_hours` (A-4 in Package 9, if built), `ref_tilt_max` 0.20.
Screen: live_sim `_world_tilt_growth`.

### F-11. No quoting 00-05 UTC (or wider): the overnight mark-out is negative
Mechanism: Maker fills between 00 and 05 UTC earned 0.48c at the fill, 0.19c at 1 h and **-0.10c at 6 h** (52k shares). The flow
then is thin and informed, and the 6-h move runs against us. Widen by 1 tick or halve size in those hours.
Data check: F_sides by hour: 00-05 -54 USD at 6 h; 06-11 +463; 12-17 +1,133; 18-23 +915.
Upside: +0.05k/day; frees ~5 h of writes for basket building (B-5: build in the European morning) | P(>= 150k): 0 | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: cron'd override (`min_edge` 0.015 at 00-05) or an hour factor (~15 lines, C-14).
Screen: data-only (done); live A/B.

### F-12. The P&L-per-write metric as the quoter's controller
Mechanism: The write budget is the scarce input once cash exists. Score each market by trailing 1-h mark-out $ per write
spent there, and give writes to the best scores (re-quote priority in `change_key`). Today `change_key` orders by planned size,
which follows Polymarket volume.
Data check: 3 Oct: 149 $ (1-h) / 4,139 accepted quote orders = 3.6c per order; 2 Oct ~1,014 $ / ~9k orders = ~11c per order (order count assumed from the W1/W2 rate;
counts). Top-60 concentration (F-5).
Upside: +10-30% maker P&L at a given budget | P(>= 150k): +0-1 | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: `change_key` last term = -score (~40 lines + a per-market rolling ledger).
Screen: live_sim (writes are mirrored).

### F-13. Inventory rule for quick MM: skew to the tilted reference, unload with-drift inventory last
Mechanism: The quoter's inventory should mean-revert toward zero around the tilted reference, at `skew_per_share` ~0.3c per 100
shares. Short-tilt inventory (toward fills) goes first through `tilt_exit_priority`. With-drift inventory (longshot bids, favourite
asks) is kept as basket overflow, up to the basket's per-leg cap.
Data check: toward 6-h mark-out lower (F-10); 3 Oct inventory re-mark -655 (README).
Upside: halves inventory drift losses, ~0.1-0.3k/day in a rising tilt | P(>= 150k): +0-1 | Ruin: the basket cap must count this overflow
End-valuation: overflow is marked like the basket.
Legitimacy: n/a.
Build: config `tilt_exit_priority` (on) + 20 lines to hand quoter inventory to the basket ledger.
Screen: live_sim tilt_exposure_end.

### F-14. Out-quote rivals legitimately: size at the second level and on the book side that thins
Mechanism: Rivals' best levels hold a median ~900 shares (p25 ~200). The pennying bot posts at the top at 20 shares in 230 markets.
A taker sweeping 400+ shares (p90 of our fills) goes through the top. A quote one tick behind the best, at 500-1,000 shares, gets
the size of the sweep at a better price for us (1 tick more edge) and costs no chase writes. This is the "quoted but not top"
coefficient (+0.44 $/side-hour) made deliberate.
Data check: F_flow2 E (top depth), C_rivals (20-share bot in 230 of 237 markets), F_flow2 D.
Upside: +0.1-0.3k/day | P(>= 150k): +0-1 | Ruin: none beyond normal inventory
End-valuation: n/a. Legitimacy: ordinary quoting; no order placed to move a price.
Build: `ladder_enabled` true with `ladder_offsets` (0.005, 0.01, 0.02) on the top 30 only (`ladder_markets` "busy"); needs
`ladder_min_cash_frac` met (cash).
Screen: live_sim with ladder on.

### F-15. Quick-MM P&L model (the number for the owner): 1.0-1.4k/day central with 15-25k cash; 1.6k/day is the 1 Oct opening pace
Mechanism: P&L/day = V x e, where V = maker shares and e = capture at the 1-6 h mark-out. Baseline 2 Oct: V 313k, e 0.32-0.40c,
giving 1.0-1.27k. With F-1/F-2/F-5/F-6/F-14, V can reach 350-500k (about 9-12% of a ~4M share/day exchange). e is 0.30-0.40c with
F-4/F-10, but spreads halved from 1c to 0.5c between 1 and 3 Oct, so e falls as rivals arrive. Range: 350k x 0.25c = 0.9k to 500k x
0.40c = 2.0k; central ~1.2k.
Data check: F_flow 2 (by day), F_flow2 A (prints/market-hour), F_flow 1 (spreads).
Upside: +0.9-1.3k/day vs today's ~0.15k | P(>= 150k): alone (on today's book, ~101k) ~1-5% (needs +49k: 1.2k x 31 = 37k); with the
Package 9 basket +2-15 points (C_alloc rows) if funded from the cushion, not the basket | Ruin: inventory in a news jump; daily sd
of maker P&L ~0.5k; P(<= 85k) ~unchanged
End-valuation: cash.
Legitimacy: ordinary market making.
Build: the bundle F-1 + F-2 + F-3 + F-4 + F-5 (+ F-6, F-14).
Screen: live_sim with `_start_cap` 20k and takes off (pnl_lag, writes_pm); the decision gate is 24 h live at >= 0.8k/day.

### F-16. Settle the MM P&L daily into the basket (the carry funds the bet)
Mechanism: Maker capture is cash. Sweep each day's realised maker P&L above a 15k quoting float into the basket's cushion
(raising m x (liq - floor)). It compounds the carry into tilt exposure only after it is banked.
Data check: C_alloc split rows (the carry is worth +5-20 points only if real).
Upside: +1-3 points P150 over a flat carry | Ruin: none extra (cushion-sized)
End-valuation: basket rule applies.
Legitimacy: n/a.
Build: 10 lines in the capital plan.
Screen: MC (C_alloc with a carry path).

### F-17. Quote only while the realtime feed is healthy; otherwise pull to the with-drift side
Mechanism: The feed delivers best-effort (W5: 30-70 resyncs/h by day, 255-589/h overnight). A stale quote during a resync is the
adverse-selection case. On a feed gap, pull against-drift sides first (they are the ones that get run over, F-4).
Data check: W5 (IDEAS_ROUND3); F_sides overnight negative 6-h mark-out.
Upside: small, ~0.05k/day; protects the quick-MM bundle | Ruin: none
End-valuation: n/a. Legitimacy: n/a.
Build: ~20 lines in the feed health check.
Screen: cannot be screened offline.

## TOP 5 (by P(>= 150k) per unit of ruin risk)
1. **F-1 takes off.** Frees ~40% of writes, stops the short-tilt build. Config only, no risk.
2. **F-4 with-drift side only in the tails.** +1,244 vs -117 on the 6-h mark-out. It makes the quoter's inventory agree with the basket.
3. **F-2 never doom a quote we cannot replace.** 5,188 refused orders (more than the accepted ones); at 0 cash it is the coverage
   lever.
4. **F-5 + F-6 + F-12 focus.** 60 markets hold 100% of the P&L. Size by real fills. Spend writes by $ per write.
5. **F-3 / F-15 funded quick MM at 15-25k, gated on 24 h of live evidence** (>= 0.8k/day at the 1-h mark-out) before it takes capital
   from the basket.
Not top 5: F-8 (probe the write limit). The write limit is not the binding constraint until F-1/F-2 land.

## Answer to the owner
- **Flow:** ~3-5M shares/day exchange-wide (26-35k prints).
- **Our capture:** 8% of it on our best day (313k shares, +1.0k at the 1-h mark-out), <1% on 3 Oct (33k shares, +149).
- **Binding constraint:** cash (refused orders, reduce-only, adding factor 0). Next come writes wasted on takes and refused orders,
  about half the 28/min. The write limit itself and latency are not binding.
- **Speed:** stepping in front earns ~0, and fresh quotes earn less than resting ones.
- **Quick MM:** 1.0-1.4k/day central (0.5-2.2k range) with 15-25k of rotating cash, F-1..F-6 and F-14.
- **1.6k/day from market making alone:** no, not as a central case. It needs ~460k maker shares/day at 0.35c (1.5 times our best
  day, ~12% of all flow) while spreads tighten.
- **Best honest number:** ~1.2k/day, worth +2-15 points of P150 next to the basket, and only if 24 h live confirms it.

## Assumptions I could not check
- Print count: mark steps include ageing (B: the mark moves 55% of the time with a static book), so 26-35k prints/day may overstate
  prints. At a 129-s poll, several prints per step understate them. Print size is assumed to be ~our average fill (130 shares).
  The `trades` table is empty: ask the owner to record the tape.
- "At the top" is sampled every 70 s, so fills by quotes placed between snapshots are counted as "not at top". The OLS split is
  indicative, not causal (the at-top and quoted hours are correlated).
- 2 Oct is assumed to be a fair steady state with capital. The flow per player will fall as entrants rise (711 -> 1,039 players) and
  spreads tighten.
- Whether Package 8's cash gate (on since 22:44 3 Oct) already stopped the refused orders: the snapshot ends 5 min later.
- The real write limit (30 vs 40-60/min) and whether a batch of 10 costs 1 write or more.
- Write budget shares (takes ~25-30%, refused orders ~25%, arbitrage ~8%) are estimated from counts x writes per action, not from
  a write log.
- The 6-h mark-outs by side carry the tilt drift of 1-3 Oct. If the tilt stalls, F-4's edge shrinks to the 1-h numbers (still with-drift
  > against-drift for <5c: +0.60 vs -1.05c).
