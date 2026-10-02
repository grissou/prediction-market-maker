# IDEAS ROUND 3 (Explorer, 2026-10-02 ~14:30 UTC)

Read-only round. New numbers come from the scratchpad journal.log (8,953 quote-decision lines, 1 Oct 16:00 - 2 Oct 08:14),
fills.csv (1,813 fills), market.db (69k snapshots) and the live code's settings. Hard facts from today taken as given: the
exchange's real write limit is ~30 writes/min for the whole bot, a 429 pauses everything 60 s, ~10k cash free of 101.5k.
Caveat: a journal quote line = a quote decision that changed, not a write; ~70% of them moved a price or size (6,000 of
8,953, 6.2/min), at ~1.5 writes each = ~9/min, matching the Analyst's 9-12 writes/min. Tiers: headline = U.S. House/Senate;
busy = >= 5k shares filled in 16 h (23 markets); quiet = the rest with fills (142); nofill = 64 markets with 0 fills.

## New facts this round

- W1 **Writes are spent where the edge is not.** By edge per quote change: the top 10 markets took 5% of changes for 51% of the
  edge; top 50: 24% / 95%; the 64 zero-fill markets took 1,741 changes (19%) for nothing. Fills per 100 changes: headline 72,
  busy 47, quiet 17, nofill 0. Quiet markets alone: 6,054 changes (68% of all) for 1,038 fills and ~$650 of edge.
- W2 **Our own fair value almost never drives a re-quote.** In 8,738 consecutive decision pairs, fv moved >= 0.5c in 10%,
  inventory changed in 10%; **24% were a 0.5c move with neither (a pure chase of a rival); 45% moved no price at all**
  (31% changed nothing visible: tolerance-kept, probably not writes). Polymarket moved >= 0.5c in 0-3% of 3-min intervals
  (median market 0%), the book top changed in 45% (p10 29%, p90 66%). Median gap between decisions in a market: 362 s.
- W3 **Fresh quotes earn less than resting ones.** Edge at fill by the age of the quote at fill: headline < 2 min **-0.20c on
  132k sh**, 2-10 min +1.55c on 60k; busy < 2 min +0.07c (66k) vs 2-10 min +0.35c (98k); quiet flat 0.48-0.57c at every age
  and +1.55c on the 8k sh filled on quotes > 30 min old. Median quote age at fill: 58 s headline, 147 s busy, 170 s quiet.
  Only 12% of quiet shares filled on quotes > 10 min old - because we re-quote every ~6 min, not because old quotes die.
- W4 **TTL refresh is a fixed write tax.** order_ttl 1800 s, refresh_before_expiry 180 s: every resting order is rewritten
  every ~27 min. 150 resting orders -> ~5.5 orders/min -> ~6 writes/min if each is DELETE + batch share = **~20% of 30/min**.
  After a restart every order has the same birth time, so the refreshes arrive as a wave 27 min later (needs code check).
- W5 **The realtime feed cannot yet drive "react only on events".** 1,941 "missed message(s) -> full resync" in 32 h: 30-70/h
  by day, 310/589/255 per hour at 03-06 UTC (degraded exchange). Each resync costs REST reads at the worst moment.
- W6 **Rank ladder.** Participants 86 -> 337 -> 447 -> 524 -> 577 -> 612 -> 647 -> 711 over 16 h (+110 per 2 h falling to
  +35-64). Rank 80-103 at +0.4% to +1.5%; 299 of 524 at -0.5%. Around rank 100 **one place costs ~$10**; the median
  participant is at ~0. Smart Score: "not scored yet" on every summary; code expects smartScoreDecayed, rank, isElite per
  marketType and comments say ROI (P&L / volume) and win rate.
- W7 **Flow decays fast within a day.** Shares/h: 150k (open hour) -> 97k -> 70k -> 40k -> 35k -> 25k; 00-03 UTC 20-29k;
  04-06 UTC ~0 (degraded). Distinct markets filling/h: 60 -> 42 by 21 UTC. Day-one open was 25% of the 16-h volume.
- W8 **Election timing.** Markets close 4 Nov 00:00 UTC = 19:00 EST 3 Nov: one hour after the first polls close (IN/KY,
  23:00 UTC), before any real returns. The bot flattens from 12:00 UTC, exits hard (up to 3c slippage) from 22:00, stops at
  23:45. Exit-poll leaks (~21-22 UTC) and early-vote reporting are the only "news" inside the trading window.

## Ideas

Format: mechanism / evidence / test (go) / risk + fair play / build (S < 50 lines, M 50-150, L > 150).

### A. Write efficiency under ~30 writes/min

1. **No 0.5c chase when fv and inventory are unchanged.** reprice_tolerance 2 ticks when the only trigger is a rival's move;
   1 tick after an fv move, a fill or an inventory change. Mechanism: W2 - 24% of decisions are chases; W3 - the quotes they
   create earn -0.2c (headline) / +0.07c (busy). Test: A/B by market halves, 6 h; go if writes -20% and edge/h >= -5%.
   Risk: fill rate at the best is 5x one tick behind (round 2 N2) - so keep 1 tick on the REDUCING side. Build S.
2. **Quiet-tier regime: wide, long-lived, cancel-less.** For the 142 quiet markets: min_edge 1.5c, tolerance 2 ticks, re-quote
   only on fv move >= 1c, a fill, unsafe, or expiry. W2: fv moves >= 0.5c in < 2% of intervals; W3: quiet edge does not decay
   with quote age (+1.55c on > 30-min quotes). Test: A/B on quiet halves 6 h; go: writes/market-h -50%, fills/market-h
   >= -20%, edge/sh +0.3c. Risk: pick-offs after Polymarket moves (today ~0 cost, Analyst §5); keep the urgent path. Build S.
3. **Per-market write allowance from edge per write.** Each market gets N changes/h in proportion to its trailing-24 h
   edge per change (floor 1/h so coverage and diffuse sweeps survive, round 2 N9); the 64 zero-fill markets get the floor.
   W1: top 50 markets = 24% of changes, 95% of edge. Test: replay the journal with the allowance; go if >= 90% of edge kept
   with <= 50% of changes. Risk: self-fulfilling (no writes -> no fills -> no edge): the floor and a weekly reset. Build M.
4. **Expiry as the cancel.** A refresh re-posts the order ~10 s before expiry and lets the old one die instead of a DELETE
   (saves ~1 write per refresh, W4: ~5 writes/min); non-urgent reprices in quiet tiers wait for expiry too. Risk: two orders
   on one side for ~10 s -> post at half size in the overlap, or confirm the exchange's expiry is exact. Test: write log by
   reason (idea 7) before/after; go if >= 4 writes/min saved. Build M (plan_change / side_needs_change). Needs a code check
   of how refresh is implemented today.
5. **TTL by tier and staggered.** Quiet 2 h, busy 1 h, headline 30 min; jitter +-20% at placement so a restart's orders do
   not all refresh in one minute (W4). Cost of a long TTL is only when the bot dies (dead-man's switch): with the watchdog
   and systemd that is minutes. Build S (settings + jitter).
6. **Two-sided changes as one cancel-all(eid).** When both sides of a market change, one per-exchange cancel-all plus the
   batch slot instead of two DELETEs. ~half the decisions are one-sided (4,345 of 8,953), so ~1,500 two-sided changes/16 h
   = ~1.5 writes/min saved. Check plan_change does not already do it. Risk nil. Build S.
7. **Write accounting by reason** (chase / fv move / fill refill / TTL / unsafe / pull / arb) per market per hour in
   status.json and a recorder table; fills and edge per write per market per day. Prerequisite for 1-6 and 10. Build S.
8. **Budget priority by expected value**: pulls and unsafe; then refilling a side that just filled (it had edge and flow is
   there now); then fv-move reprices on markets we hold; then empty sides; chases last and only with headroom. Today:
   pulls, Polymarket-moved, headline, empty sides, reprices. Build S (change_key).
9. **Reprice storm -> one cancel-all.** pull_storm exists for > 25 pulls. Extend: when > 25 markets need a price-unsafe
   reprice in one cycle (news cluster, ceiling flip, limit change), send one tournament cancel-all (1 write) and re-place in
   batches (~15 writes) rather than 50-280 DELETEs (today's incident 2 shape; 429 at 30). Test: scenario news burst with 60
   markets moving; go if 429s = 0 and the book is back within 2 cycles. Risk: ~30 s with no quotes at all. Build S-M.
10. **Headline: re-quote on fv move or fill only.** Rep U.S. Senate 124k sh at +0.03c; headline quotes < 2 min old earned
    -0.20c on 132k sh (W3). Tolerance 2 ticks, chase off, min_edge 1.25c on headline adding sides. Saves ~0.5 writes/min and
    ~-$260 of churn. Test: markout by quote age on the next 24 h; go if the < 2-min bucket stays negative. Build S.
11. **Event-driven re-quote with a REST fallback.** Re-quote a market only when its book was flagged dirty by the feed, fv
    moved, a fill landed, or TTL; otherwise leave it. W5: the feed loses messages, so keep the 3-request bulk tops every 30 s
    as the fallback trigger. Test: replay - share of today's changes that would have been triggered; go if writes -30% with
    the same fills. Build M. Depends on 7.
12. **Tiers instead of "all 237 thinly"**: A (top ~50 by edge/write) two-sided at 1-tick tolerance; B (~120) regime 2;
    C (64 zero-fill: Rep SC Gov, Rep MT-01, Rep MI-10, ...) one order per side per hour at 2c. W1. Test: fills in C over
    48 h; go for C if < 0.2 fills/market-day. Risk: sweeps are diffuse (10 of 56 sweep markets repeat): C keeps a wide order.
    Build S-M (size plan already tiers by volume).

### B. The 33-day horizon

13. **Phase plan driven by a daily volume curve.** W7: flow decayed 6x within day one; participants' growth is slowing (W6).
    Measure daily: shares, fills, distinct markets filling, participants, rival spread <= 1c share, our edge/sh. Phases: (i)
    now-mid Oct: student bots multiply, spreads tighten -> write discipline (A) and breadth; (ii) debates/news weeks: cluster
    moves -> idea 9, wider adding quotes; (iii) last week: volume returns, flattening bots sell at bids -> pair/buy-side arb
    and reducing-side priority; (iv) election day: idea 15. Go-numbers: if week-2 shares < 50% of week 1, cut quiet sizes 50%
    and widen 0.5c; if rival spreads > 2c in > 30% of markets, widen ours to just inside. Build S (analysis + override bundles).
14. **Rival census** from the books table daily: distinct rival price levels and sizes per market, share pennying at 0.25c,
    re-quote cadence (round 1 F2 method). Feeds per-market min_edge (market_edge_enabled, OFF) and the phase plan. Build S.
15. **Election-day plan (owner decision W1).** W8: trading ends before returns; the "10-30c Polymarket moves on returns" happen
    after our close, so taking stale student quotes on returns is not available; the only in-window moves are exit-poll leaks
    at 21-22 UTC, when we are in hard exit. Proposals: (a) start passive flatten 24 h before close (not 12): 126k shares at
    the observed reducing fill rate (~11k sh/h in reduce-only night, 0 edge) needs ~12 h even with flow; (b) exempt complete
    sets (Dem+Rep) from the hard exit if SIG confirms unresolved positions settle at the outcome (a set pays 1, exiting costs
    the spread); (c) do not take directional positions at the end: a 1/sh tail on a mis-called race for +3-7c. Risk: rule
    risk (SIG wording on positions at close). Build S (settings) + M (pair-exempt exit).
16. **End-valuation modes ready as one setting**: mark (final leaderboard = 00:00 UTC trade-average marks: thin markets'
    last prints decide; be flat, and let no sweep print be the mark of a position we hold) / settle (hold sets, be flat net)
    / zero. Decision pending SIG; both branches share "flat net". Build S.
17. **Stop adding inventory by turnover, not by clock.** Open lots have a 9-h median age vs 80 min for round-tripped ones
    (round 2 N1): a share added in a market with < 50 sh/h flow in the last 72 h will still be there at close. From 1 Nov
    adding sides x0.25 in low-turnover markets (turnover_control exists, OFF), from 3 Nov 00:00 everywhere. Build S.
18. **Last-week flatten flood.** Other bots flatten too -> bids sum < 1 and ask-sum <= 0.985 more often, wide spreads: keep
    buy-side arb and the unwinder on with the ref-sum guard, raise arb max frac for the last 24 h, reducing side joins the best
    (reduce_join_best, OFF, retest). Test: ask-sum <= 0.985 minutes/day trend over the last 7 days; go if 3x the baseline 2.5%.
    Risk: outsider races (guard holds in the sim). Build S.

### C. Rank and Smart Score

19. **Rank arithmetic.** ~1 place per $10 near rank 100, median at 0 (W6): only realised P&L and the trade-average marks move
    it. Daily: unshown edge = Σ pos x (mid - currentPrice) from the positions table (+577 at 09:45; Rep FL Senate +8c x 592);
    where the spread is <= 1c, let the reducing side JOIN the best for those positions (passive, never a take, never to move a
    mark). Go: unshown edge > $300 for > 6 h. Build S (analysis) + reuse reduce_join_best per market.
20. **Smart Score hypothesis test.** If ROI = P&L / volume: headline churn (Rep U.S. Senate 21% of shares for 4% of edge)
    dilutes it ~1.3x; if win rate counts, flattening a set = one win + one loss (code comment), favouring per-market
    flattening (flatten_per_market_hours 6 exists). Measure daily P&L/volume with and without headline fills; decide after
    SIG answers. Build: settings only (idea 10 helps either way).
21. **Daily scorecard** (one script over the recorder): realised, unrealised at mark / mid / fv, shares, edge/10k sh, writes
    and fills per write, rank + participants, sd of 10-min account steps (rank noise), coverage, 429/409/pause counts, feed
    resyncs. The team's north star; S.
22. **Decay-aware ramp.** "Decayed" means recent weeks weigh more: sizes x1.5 from ~27 Oct if cash allows (Package 2 frees
    9-14k), clean flatten per idea 15. Owner/SIG. S.

### D. Operations for an unattended night

23. **Global non-pull write cap + TTL jitter.** Whatever the cause (news, ceiling, limit change, TTL wave after a restart),
    never send more than ~22 non-pull writes per minute; the rest waits. Today the cap is per cycle (urgent 20) and the
    budget is 45 with max 50 - above the exchange's 30. Set writes_per_minute 28, max 30, until SIG confirms more. Build S.
24. **Polymarket (Gamma) outage guard for quoting.** Only takes check ref age (30 s). With ref_weight 0.7 a frozen reference
    anchors fv for as long as the outage lasts while books move. Guard: ref age > 120 s -> that market quotes from the book
    fv widened 1c; all refs > 300 s -> wide adding quotes, reducing sides only, one alert. Test: scenario with Gamma
    unreachable 10 min. Build S-M.
25. **Stale ref_map after a candidate change.** ref_map.json names the candidate (229 entries); a withdrawal closes the
    Polymarket market, fetch_polymarket skips "closed", the market goes unpriced and a held position falls to fallbacks.
    Daily script: diff ref_map notes vs Gamma titles, flag closed/unmapped ids with a position; alert. Build S.
26. **External liveness check.** The watchdog lives inside the process; the 2-hourly summary is the only outside signal.
    Cron every 5 min: status.json `updated` older than 300 s, `paused_until` in the future for > 10 min, `markets_priced`
    < 50% of held positions, or orders_resting = 0 while not reduce-only -> ntfy. If the handover's plain restart fails, the
    30-min TTL already kills the orders; the gap is nobody knowing. Build S (20 lines + cron).
27. **Degraded-exchange mode instead of cancel-everything.** 03-06 UTC: 310-589 feed resyncs/h, read timeouts, 17 failed
    cycles; max_failed_cycles 3 -> cancel-all and re-place later (writes at recovery drew 429s at 08:00). Keep SAFE resting
    orders (inside limits, young) when the failure is reads timing out, cancel only unsafe ones; exponential backoff
    1->2->4->30 s on failed cycles (Analyst §3 asked; verify whether built). Build S-M.
28. **Start-up invariant for the risk model.** Incident 1 class: no reduce-only on the risk measure in the first 10 min
    after a start unless held positions priced by value >= 95%; log the unpriced held list instead. Build S.
29. **Night bundle via scheduled overrides (00-08 UTC)**: quiet-tier cadence halved (A), headline min_edge 1.25c, takes off
    while ref age > 30 s, arb max frac halved. Night fades hold (round 2 N7) and nothing needs speed. Build S.

## Top 10 by expected value over the remaining 33 days (bot at ~+$500/day, writes the binding resource)

1. **#1 No 0.5c chase** - 24% of writes buy quotes that earn -0.2 to +0.07c; frees ~2 writes/min for refills and pulls. S.
2. **#2 Quiet-tier wide/long-lived regime** - 68% of all decisions for ~$650 of edge; quiet edge does not decay with age.
3. **#23 Write cap 28/30 + TTL jitter** - one 429 storm costs 20-30 min of quoting (~$100-200) plus pick-off exposure; the
   next trigger is already in the code (TTL wave after a restart).
4. **#15/#16 Election-day plan** - the single largest P&L event: 126k shares to flatten, rule unknown; decide now, build S.
5. **#9 Reprice storm -> cancel-all** - turns a news cluster from 100-280 writes into ~16.
6. **#10 Headline fv-only re-quote** - removes -$260 of churn on 132k shares and 21% of volume that may dilute Smart Score.
7. **#4/#5 Expiry as cancel + tiered TTL** - ~4-5 writes/min (15% of the budget) for nothing lost while the watchdog runs.
8. **#24 Gamma outage guard** - the one failure mode with no guard today and unbounded cost (fv anchored to a frozen price).
9. **#19/#21 Daily scorecard + unshown-edge joins** - +$300-600 of earned edge shown on the board; 1 place per $10.
10. **#3 Per-market write allowance** - the systematic version of 1-2 and 12; needs #7 first.

Honourable: #26 (cheapest insurance of the night), #13 (the phase plan), #6 (free write per two-sided change).

## Owner decisions needed

- **Election night (W1):** are unresolved positions at 00:00 UTC 4 Nov settled at the outcome or marked (and at what)? Is
  the final leaderboard taken at close or after settlement? Then: hard exit for complete sets yes/no; flatten start 24 h vs
  12 h; no directional taking at the end (recommended).
- **Smart Score:** definition (ROI? win rate? decay window?); when scoring starts; whether a hedged set counts as a win
  and a loss; whether volume with ~0 edge hurts.
- **Write limit with SIG:** is it 30/min per account, do batches count once or per order, do DELETEs and cancel-all count
  the same, is there an amend/replace endpoint, does a 429 pause reads too, and is the limit per minute sliding or fixed.
- **Gamma rate limit** before any refresh below 5 s; **end-valuation mode** setting default (idea 16).

## Measurements to add

- Writes by reason per market per hour (idea 7); fills and edge per write per market per day; TTL refresh count.
- Daily: participants, rank, unshown edge, P&L/volume with and without headline, feed resyncs, 429/pauses (idea 21).
- Rival census per market per day (idea 14); ask-sum <= 0.985 and bid-sum >= 1.005 minutes per day (idea 18).
- ref_map health: closed / unmapped Polymarket ids with a position (idea 25).
