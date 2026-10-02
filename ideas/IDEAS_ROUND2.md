# IDEAS ROUND 2 (Explorer, 2026-10-02 ~10:45 UTC)

Read-only round. New numbers below come from the scratchpad fills.csv (1,813 fills, 1 Oct 16:00 - 2 Oct 08:14, sides
reconciled with the Analyst's `data2.fills_reconciled`), market.db snapshots (290 live snapshots, 2-4 min apart) and
journal quote lines; scripts in scratchpad `r2/a1-a6.py`. The owner's 09:45 facts (164 positions, 126.7k shares, 11k
cash, exchange marks) are taken as given. Edge = c/share vs our fair value (fv) at quote; "mid" = tournament mid.
Round 1 ideas are not repeated; where one is now built (fast unload #3, pair unwinder #16, age skew #23, refill
cooldown #4) the idea below extends it.

## New facts this round

- N1 **Round trips are the unit of profit; carrying earns nothing.** FIFO-matched round trips: 254k shares for +1,824
  (+0.72c/sh) = essentially all of the exchange's realised +1,916. The 79.5k shares still open at 08:14 had entry edge
  +1,117 but P&L at mid -206 (-0.26c/sh): by age, <1 h 8.0k sh (edge +199, mid -21); 3-8 h 18.5k (+410, -62);
  >8 h 53.0k (+507, -125). Time-to-unwind of round-tripped lots p10/25/50/75/90 = 5 / 11 / 80 / 226 / 382 min.
  Open lots' age p25/50/75 = 5.9 / 9.0 / 11.7 h (90% older than 3 h).
- N2 **Queue position is worth 5x in fill rate.** P(fill on our side before the next snapshot, per 10 min): at the
  best 33-34%; 1 tick behind 6-8%; 2 ticks 2-5%; 3+ ticks 3-6% (n = 4-4.4k market-snapshots per cell). Yet when we hold
  inventory the reducing side is at the best only 24% of the time (behind 51%, absent 25%; n = 13.4k). Quote lines with
  inventory: reducing side quoted 91% at mean 1.23c from fv, adding side 65% at 2.43c.
- N3 **Big positions do not turn.** Of the 12 positions >= 1,000 sh at 08:14, only Rep U.S. House had any reducing fill
  in the previous 6 h (28); the other 11 had zero. 52 markets have turnover (shares traded / shares open) < 2 and hold
  28.6k of the 53.2k (at mid) in positions; 13.4k sits in positions that would need > 24 h to flatten at half the
  observed two-way flow (NH Gov Dem, RI Sen both legs, NC Sen Dem, FL-16, TX-28, NJ-07, MS Sen ...).
- N4 **Exits are available after sweeps, not after ordinary fills.** Sweep fills (edge > 3c, 57k sh): hitting the
  opposite best realises +2.40c of the +4.75c immediately, +1.87c at +5 min, +2.56c at +30 min; posting at mid +3.2c.
  71% of sweep shares could exit at fill price + 1c within 30 min. Ordinary fills (1-3c edge, 553 fills): mid reaches
  fv within 30 min for only 32% of shares (46% within 60); an opposite best at fill + 1c appears for 43% (55%);
  posting at mid gives +0.5-0.8c vs +1.84c entry edge, vs +0.37c kept by carrying to mid+60.
- N5 **Reducing fills carried no edge.** Day: reducing fills 262k sh at +0.06c, adding fills 191k at +1.42c. Night:
  81k at +0.15c vs 53k at +0.75c. We entered with edge and left without (the old skew; partly fixed).
- N6 **Mark fragility by position (proxy: |pos| x sd of the mid step between snapshots x sqrt 3, i.e. $ per ~10 min):**
  Rep RI Sen $255 (sd 6.8c on an 8c contract), Dem RI Sen $222, Dem U.S. Senate $183, Dem NH Gov $150, Dem MN-01 $87,
  Rep WI-03 $73, Rep U.S. House $56, Rep MI Sen $54. Sum over positions $1,640 per step vs $440 total half-spread exit
  cost. The 22:04 dip: real equity 100,452 (22:03) -> 99,461 (22:08) -> 99,567 -> 100,408 (22:18): -1,000 for ~10 min
  = 200 rank places.
- N7 **Night gap is persistent, not reverting.** |mid - Polymarket| > 2c in 37-52% of market-snapshots at 00-08 UTC vs
  13-18% at 16-20. A gap > 2c is still > 1c with the same sign 10 min later in 95% (day) / 99% (night) of cases.
  Night "fade" fills (buy where tournament < Polymarket, sell where >): 31k sh at +2.95c vs fv, +4.22c vs Polymarket,
  but only +0.28c at mid+60. Day fade: 51.8k at +2.14c / +0.65c at mid+60. "Follow" fills (old skew): night 45.5k at
  -1.58c, day 51.0k at -1.08c.
- N8 **Pair sums.** U.S. Senate bid-sum >= 1.000 in 16% of snapshots (p50 0.990, p90 1.000, max 1.020); ask-sum <= 1.00
  in 33% (min 0.985). U.S. House: 15% / 33% (ask-sum min 0.965). All races: bid-sum >= 1.00 16.4%, ask-sum <= 0.995 5.4%.
- N9 **Sweeps are diffuse.** 56 markets had a > 3c fill; only 10 had one in both halves of the period. Breadth of
  coverage, not market selection, catches them (consistent with the Analyst's 0.18 markout persistence).
- N10 Flow breadth: 40-60 distinct markets fill per hour by day, 30-46 at 00-03 UTC, 2-10 at 04-06 (degraded exchange).
  Rank lines appear only in the 2-hourly summaries (18:01, 20:00, 22:04, 00:06, 02:00, 04:00, 08:08); the leaderboard's
  own cadence is unknown.

## Ideas

Format: mechanism / evidence / test (go) / risk + fair play / build (S < 50 lines, M 50-150, L > 150).

### A. Inventory turnover and realising P&L

1. **Round-trip quoting: reducing side joins the best, adding side stays at 1c.** Reducing quote = max(fv, best on our
   side) i.e. join (not penny) whenever the best is at or beyond fv; adding side unchanged. Mechanism: N2 - at the best
   the fill rate is 33%/10 min vs 6-8% one tick behind; today the reducing side is behind 51% of the time. Rivals sit at
   0.4-0.7c from fv, so "join" costs 0.3-0.6c of edge vs the 1c we ask now, on fills that currently take 80+ min.
   Test: A/B on market halves for 6 h; go if reducing-side fills/h >= 2x and inventory half-life (median FIFO
   time-to-unwind) < 30 min, realised P&L/h not lower. Risk: none to fair play (joining never moves the price);
   P&L risk is the 0.3-0.6c given back - bounded by N4 (carrying kept +0.37c of 1.84c anyway). Build S
   (`improve_ticks_reduce = 0`, `min_edge_reduce = 0.0`, in compute_quote).

2. **Post-sweep exit ladder sized to the fill (extends R3/#3).** After a fill with edge > 3c, place the exit for that
   quantity in two parts: half at fill + 1c (available for 71% of sweep shares within 30 min, N4), half at mid; drop
   to "join best" after 15 min. Mechanism: sweep inventory is 94% of profit and today sits as inventory for hours.
   Numbers: 57k sweep shares / 16 h; realising +2.5c instead of carrying (mid markout +3.26c but unrealised) converts
   ~+1,400/day of lagging mark into realised P&L and frees ~30k of capital-hours. Test: 24 h; go if >= 60% of sweep
   shares are closed within 30 min at >= 50% of entry edge. Risk: the 29% of sweeps that keep moving (news) - keep the
   age skew behind it. Fair play fine. Build S.

3. **Tick-based age schedule instead of 0.25c/h.** The age skew (0.25c/h after 1 h, cap 2c) moves the price 1 tick
   every 2 h; N2 says one tick is a 5x fill-rate change and N1 says positions sit 9 h. Replace with a schedule on the
   reducing side: 0-20 min at fv + 1c; 20-60 min join best; 1-3 h improve by 1 tick while >= fv; > 3 h accept fv - 0.5c
   (below our fv but never below mid - 0.5c). Evidence: open lots > 8 h: 53k sh, entry edge +507, mid -125.
   Test: replay fills.csv lots under the schedule with N2 fill rates; go if simulated half-life < 45 min at realised
   >= +0.4c/sh. Risk: giving back; cap the daily give-back at 10% of realised. Fair play fine. Build S (`skew_age_*`).

4. **Size to the expected time-to-exit.** adding size_m = reducing fills/h_m (last 24 h, at best) x median fill size_m
   x 0.5 h, floored at 100 and capped by the existing limits. Markets with < 1 reducing fill/h get <= 300 sh.
   Evidence: N3 (11 of 12 big positions: zero reducing fills in 6 h; 13.4k in > 24 h positions). Test: compute
   size_m for all markets from fills.csv; go if the sum stays >= 30k (enough to earn) while the > 24 h capital drops
   below 3k. Risk: smaller sweep catches in quiet markets (sweeps are diffuse, N9 - partially offsets). Build S
   (update_size_plan).

5. **Dead Senate pair -> cash by passive two-leg asks.** 7,335 x 2 complete sets. The unwinder waits for bid-sum >= 1.000
   (16% of snapshots, likely small size at the touch). Instead rest asks on both legs at prices summing to 1.005
   (each at its own best ask, 500 sh per leg, race-netted so one leg never gets > 1,000 ahead of the other), refilled
   on fill. Mechanism: flow lifts asks in the headline markets constantly (124k sh traded in Rep U.S. Senate alone);
   every pair sold returns 1.005 of cash per 1.000 of capital. Test: journal count of ask lifts at best in the two legs
   per hour; go if >= 500 sh/h per leg (then the pair is gone in ~15 h). Risk: temporary 1,000-share one-leg exposure
   (~$10 per 1c move). Fair play fine - ordinary passive sales. Build S (pair_unwind passive mode).

6. **House doubled legs: reduce the leg with the cheaper exit.** Long Dem House 10,834 + short Rep House 9,396 nets
   to eff = +20,230 Dem-equivalent (the netting is right: eff = inv - mean(others)). Both legs reduce the bet; choose
   per cycle the leg whose best-on-our-side is nearer fv (Dem ask vs Rep bid) and join it with 1,000 sh, hold the other
   leg's reducing quote at fv + 1c. Evidence: N3, N6 ($56/10-min mark noise on Rep House; the 10k positions carry the
   entire national-swing risk). Test: 12 h; go if net exposure < 8k with realised >= 0. Risk: nil beyond edge give-back.
   Build S (twin-aware reduce priority).

7. **Exit placement from the 13.5-min half-life.** After a fill at fv - 1.5c with the mid at fv - 0.5c, the gap closes
   0.25c per 13.5 min: an exit at fv has ~32% chance in 30 min (N4) and earns 0.5c/h of carry - below the 5x fill-rate
   loss of sitting 1-2 ticks off. Rule: reducing quote = mid + 0.25c (rounded to tick) when mid >= fv - 1c, join best
   otherwise. Test: fills.csv replay; go if realised per round trip >= +0.6c with half-life < 30 min. Risk: none new.
   Build S (same code path as 1).

8. **Reducing writes first in the 45/min budget.** Order the write queue: unsafe pulls, reducing-side reprices,
   adding-side reprices, new adding quotes. Evidence: adding quotes are behind 72% of the time with 2-8% fill rates;
   reducing reprices realise P&L. Test: journal deferred lines by class; go if any reducing reprice is ever deferred
   (today's "deferred" lines: 19 in 6 min at startup). Risk: nil. Build S (priority weight in send_changes).

9. **Give-back budget per lot.** Allow up to 40% of a lot's entry edge to be given back to realise it within 30 min,
   never more; after 30 min raise to 60%. Evidence: carrying keeps 20% (0.37 of 1.84c, N4) and ties capital for 9 h;
   round trips realised 0.72c/sh (N1). Test: compute realised/sh under 40% vs today's; go if >= +0.5c/sh at 3x turnover.
   Risk: formalises 3 and 7; fair play fine. Build S (parameter inside 3).

10. **Stop adding to a position that already has an unfilled reducing quote at the best for > 20 min.** Mechanism: if
    the best is not getting hit, there is no reducing flow in that market; adding more only lengthens the queue of
    capital. Evidence: N3. Test: count such market-states per hour; go if > 20 at any time. Risk: coverage loss in
    quiet markets (keep 100-share adding quotes). Build S.

### B. Capital efficiency

11. **Capital allocation by measured turnover, not by activity.** Weight_m = fills/h_m (both sides, last 24 h) /
    (|pos_m| + size_m); markets below the 25th percentile get the adding side withdrawn until |pos| < 300.
    Evidence: 52 markets with turnover < 2 hold 28.6k (54% of position capital at mid). Test: run the ranking on
    fills.csv; go if the withdrawn set earned < 10% of realised P&L (expected: ~0, N3). Risk: low. Build S.

12. **Per-market capital ceiling instead of the global 75% switch.** The global ceiling withdraws every adding side at
    once (coverage collapse: sweeps are diffuse, N9). Per market: capital_m <= max(2% of account, 1 h of reducing flow
    x price). Test: compute how many markets would be capped now (expect ~15) vs all 160. Risk: the global ceiling stays
    as a backstop at 85%. Build S.

13. **Resting-order collateral is dead when behind by >= 2 ticks.** 33k is locked in 152 resting orders; a quote 2+
    ticks behind fills 2-5%/10 min (N2). Rule: when behind by >= 2 ticks on the adding side, size 0.25x (keep presence,
    free 75% of that collateral) and move the freed collateral to reducing quotes at the best. Test: journal - share of
    adding quotes >= 2 ticks behind (snapshots: 7.4k of 16.4k our-side observations); go if > 30%. Risk: smaller sweep
    catches when a sweep clears 2 ticks (p90 fill 761 sh - keep 300 minimum). Build S.

14. **Cash reserve as a sweep option.** Sweeps earn +4.7c/sh but need free capital at the instant (a 10k-share lift
    at 0.8 needs 8k). 11k cash cannot absorb two. Hold >= 20% cash by lowering adding sizes in low-turnover markets
    (11, 13) rather than by the global ceiling. Numbers: 109 sweeps / 16 h = 7/h; expected sweep capital need ~5k/h.
    Test: count sweeps lost to insufficient size or the ceiling after 09:45 (journal "ceiling" lines). Build: settings.

15. **Night capital budget.** Night fade fills are +2.95c vs fv but +0.28c at mid+60 (N7): good positions whose mark
    arrives in daytime. Cap night-opened inventory at 10k shares total, 0.5x size, and quote the reducing side at the
    best from 08:00 UTC when flow returns (day fade markout +0.65c). Evidence: open lots > 8 h at 08:14 were mostly
    night-opened (53k sh). Test: tag lots by open hour and measure their realised P&L and age; go if night lots'
    age > 2x day lots'. Risk: forgoing +2.95c-edge fills - keep them, cap them. Build S.

16. **Flatten the never-reducing tail once, at the best.** For the 13.4k of capital in positions needing > 24 h
    (N3), rest the entire position at the best on the reducing side now (not through mid), accept ~0.5c give-back
    (~$130 total) to free 13k = +25% quoting capital. Test: measure fill rate at best in those markets for 6 h; go
    if >= 50% of the shares are gone. Risk: RI Senate pair (4.4k sh on 8c contracts) may need a day. Fair play: passive
    orders at the current best. Build: settings / one-off.

### C. The mark rule as accounting and risk (never trade to move a mark)

17. **Mark-fragility cap per position.** fragility_m = |pos_m| x sd(step of the exchange's currentPrice, from the
    recorder) in $ per 10 min; cap |pos_m| so fragility_m <= $100 (RI Senate legs -> <= 900 sh; Dem U.S. Senate
    -> <= 4,000). Evidence: N6 - today's sum is $1,640 per step vs ~$500/day earned. Test: recorder positions table
    after 24 h; go if the proxy (mid-step sd) correlates > 0.6 with currentPrice-step sd. Risk: smaller size in
    volatile thin markets (which also have the widest rival floors). Fair play: sizing only. Build S.

18. **Rank-stability objective = cap total fragility.** sum fragility <= $500 per 10 min (one day's P&L). Today's
    $1,640 explains the 92 -> 299 -> 93 swings. The leaderboard rank is a rolling mark; ~70% of our 10-min account
    noise comes from 4 positions (RI x2, Dem U.S. Senate, Dem NH Gov). Test: recorder - sd of account value vs own
    model per 10 min before/after 17; go if sd halves. Build: part of 17.

19. **Earned-but-unshown tracker.** Log every cycle: realised (cash + cost basis - 100k), exchange unrealised at
    currentPrice, unrealised at mid, at fv; and per position (mid - currentPrice) x pos. Evidence: +577 at mid, +2,657
    at fv above the exchange's marks; Rep FL Senate alone marked 8.4c under mid. Purpose: decide which exits are worth
    most per share to the score (a passive sale at mid in FL Senate realises +8c/sh vs the mark instantly) and detect
    marks that are "ours only" (last 5 prints ours) whose unrealised P&L is unreliable - weight those at cost in the
    risk model. Fair play: selling at the market's best bid is ordinary trading; we never place trades to print a
    price. Build S (recorder exists; add the derived columns).

20. **"Holdings cannot be valued" is a rank risk.** The CONFLICT error says a position in a market with no valuation
    price makes the whole account unvaluable. If the leaderboard snapshot hits that state, our rank entry may be stale
    or missing. Measure: recorder - positions whose market has had no trade in the last 30 min (window rule) and how
    often the account read fails. Mitigation: do not open positions in markets with < 3 trades/day by others; close
    100-share remnants there. Test: count of such markets now (expect 10-20 of 164 positions). Build S.

21. **Leaderboard cadence check before any timing logic.** Rank lines exist only 2-hourly; the owner saw rank at 09:45.
    Poll the leaderboard every 5 min for 24 h and record our value: if it updates continuously, timing is irrelevant;
    if 2-hourly, the only legitimate implication is the sizing one (be smaller in fragile markets, always) - no
    trading around snapshot times. Build S (measurement only).

22. **Kill switch on our mark, rank model on theirs.** Reduce-only cost 0.7-1.7k on day one, triggered partly by the
    exchange's lagging valuation. Keep worst-case loss and the kill switch on fv/mid; add a separate "rank drawdown"
    figure (exchange value - 10-min moving average) for logging and for 17, so a sticky sweep print never puts us in
    reduce-only again. Test: replay the 22:04 dip; go if the kill switch would not have tripped. Build S.

### D. Exploiting rival bots legitimately

23. **Rivals do not react to us -> joining the best is free.** 70% of our at-best quotes were never undercut before we
    moved (median 151 s when undercut); rivals sit at their own 0.4-0.7c prices. So joining the best on the reducing
    side (1) and on the fade side by day (N7: +2.14c edge, +0.65c at mid) costs no retaliation. Test: undercut rate
    after 1 is enabled (go if still < 35%). Build: none beyond 1.

24. **Be the best where the rival top is thin.** From the recorder's books table: when the best on our side shows
    <= 100 sh and the next level is >= 1 tick away, join at the best with full size; fills > 100 sh (median 100, p90
    761) take ours too. Evidence: N2 fill rates; F10. Test: share of fills at best where the rival's displayed size was
    < our size; go if > 50%. Risk: nil. Build S (needs books table sizes).

25. **Night: fade with the Polymarket anchor, exit by day.** Rivals stay two-sided all night but anchor to the
    tournament (gap doubles and persists, N7). Our night fade fills were +4.22c vs Polymarket, +2.95c vs fv. Keep
    them (0.5x size, 10k cap from 15); from 08:00 UTC put their reducing side at the best. Expected: 31k sh x
    (+0.65c day markout) realised by noon. Test: tag night lots; go if their realised P&L/sh >= +0.5c by 16:00.
    Risk: Polymarket itself thin at night - keep the liquidity flag. Fair play fine. Build S.

26. **Rival-anchor fingerprint.** From books + Polymarket feed: for each market, lag from a >= 1c Polymarket move to
    the rival top change (5-s resolution). Polymarket-anchored rivals (lag < 10 s) are the ones that pick us off;
    tournament-anchored ones leave 2c stale quotes (round 1 #8). Only 83 moves/16 h, so the value is small
    (<= $50/day); do it for the pick-off defence, not the offence. Build M.

27. **Headline markets: match the flow's cadence.** Rep U.S. Senate 124k sh at +0.03c (fills every 9 s in bursts):
    pure churn. Quote the headline legs only on the reducing side at the best and as pair asks (5); let rivals own
    the 0.5c touch. Expected: -124k sh of volume, +0 P&L, -$37/step mark noise, frees ~5k. Test: 12 h; go if realised
    unchanged. Build: settings (headline adding size 0).

### E. Other

28. **Breadth over selection.** 56 markets swept, 10 repeated; markout persistence 0.18. Priced coverage 106/237.
    Quote 237 markets at 300 sh (adding) rather than 106 at up to 2,000: 300 x 0.5 x 237 = 36k capital, same as now,
    and sweeps (+4.7c) arrive in markets we do not yet quote. Test: sweeps/day in markets with < 500 sh quoted vs > 500;
    go if per-market sweep rate is flat across size. Risk: write budget (quiet markets need few writes). Build S.

29. **Two KPIs: realised P&L/day and inventory half-life.** FIFO realised (+1,824/16 h) and median lot age are the
    numbers the leaderboard follows; log them hourly beside edge. Target: 400k sh/day x 0.7c = +2,800/day at half-life
    30 min (vs +500/day at 9 h). Build S (status.json fields).

30. **Degraded-exchange hours (04-06 UTC): reduce-only at the best, no adding.** Fills 3-12/h, 14 failed cycles; the
    only useful write is a reducing quote. Trigger on fills/h < 20 and failed cycles > 2. Cost: nil. Build S.

31. **Exit via the complete set when the twin's book is tighter.** Long Dem X at 0.40 with Dem's best bid 1.5c below
    fv but Rep's best ask 0.5c above 1 - fv: buy Rep YES at the best, creating a set at cost <= 1.00, then unwind the set
    through the pair unwinder. Converts a marked directional position into a locked (zero-risk) set at a 1c better
    price; capital stays until the set is sold. Test: books - share of our open positions where the twin exit is
    >= 0.5c better; go if > 20%. Build M.

32. **Lot-level accounting in the recorder.** Store each lot (open time, price, fv, edge, exit time, exit price) so
    half-life, give-back and realised/sh per market are a query, not a reconstruction (this round's N1-N5 took a FIFO
    rebuild from fills.csv with 827 unattributed sides). Build S.

## Top 10 by expected value (bot at ~+500/day with 90% of capital in positions)

1. **#1 Reducing side joins the best** - 5x fill rate on the side that realises P&L, 0.3-0.6c cost on edge we were
   not keeping anyway (carried lots: -0.26c/sh at mid); turns 9-h inventory into 30-min inventory. S build.
2. **#2 Post-sweep exit ladder** - 57k sweep shares/16 h at +4.75c; realising +2.5c within 30 min is ~+1,400/day moved
   from "lagging mark" to realised, with 71% exit availability measured.
3. **#4 + #11 Size and capital by measured turnover** - 28.6k (54% of position capital) sits in 52 markets that earned
   ~0 and had no reducing fills in 6 h; redeploying it at +0.7c/round trip is the single largest capital effect.
4. **#3 Tick-based age schedule** - the 0.25c/h skew is 1 tick per 2 h against a 5x-per-tick fill curve; 53k shares
   are > 8 h old with their edge already gone at mid.
5. **#17/#18 Mark-fragility cap** - four positions produce ~$900 of the $1,640 per-step account noise; capping them is
   the cheapest way to stop 200-place rank swings and the reduce-only false alarms (cost 0.7-1.7k on day one).
6. **#5 Passive pair asks for the dead Senate sets** - 7.3k cash at +0.5% with no market risk, in ~15 h at the headline
   lift rate; the trigger-based unwinder waits for a 16%-of-snapshots touch condition with unknown size.
7. **#13 Shrink adding quotes >= 2 ticks behind** - 33k locked in orders that fill 2-5%/10 min; free 75% of it for
   reducing quotes at the best. Settings-level build.
8. **#28 Breadth over selection** - sweeps are diffuse (10 of 56 markets repeat); 131 unquoted markets at 300 sh cost
   the same capital as today's concentration and catch the +4.7c flow we miss.
9. **#15/#25 Night budget + day exit** - night fades are the best entries (+2.95c) and the worst carriers (+0.28c at
   mid); cap them and exit them at the best when day flow returns.
10. **#19 Earned-but-unshown tracker** - +2,657 of fv edge is unshown; knowing per position which passive exits move
    the score most (FL Senate +8c/sh at mid) and which marks are unreliable costs 30 lines and informs 1-7.

Honourable: #27 (headline churn off: -124k sh of zero-edge volume), #30 (free), #21 (measure before any timing logic).

## Measurements to add to the recorder

- Lots: open/close time, price, fv, edge, exit price, give-back; half-life and realised/sh per market per day (#32).
- Per position per minute: exchange currentPrice, mid, fv, (mid - currentPrice) x pos, whether the last 5 prints were ours;
  account value vs own model, 10-min sd (#17-19).
- Per market-snapshot: our queue position (at best / ticks behind) per side with displayed size ahead, and whether a fill
  followed within 10 min (calibrates N2 at 1-min resolution).
- Leaderboard value and rank every 5 min for 24 h (#21).
- Capital: cash, locked in orders by (side, ticks behind), capital in positions by turnover quartile, hourly (#11-14).
- Night lots tagged by open hour with their realised P&L by 16:00 UTC (#15, #25).
