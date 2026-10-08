# IDEAS ROUND 1 (Explorer, 2026-10-02 ~10:00 UTC)

Read-only round. Numbers below come from the scratchpad copies of fills.csv (1,813 fills, 1 Oct 16:00 - 2 Oct 08:14),
market.db snapshots (68,730 live rows, 60 s, 229 labelled markets), status.json (08:14) and journal.log. The recorder's
`books`/`trades` tables are not in this snapshot (live since 08:34), so rival fingerprints below are inferred from
snapshots where we had no order on either side ("rival-only books"). Edge = c/share vs our fair value at quote;
markout = vs fair value later (fv_after). "Go" numbers are what would make an idea worth building/enabling.

## New facts this round (drive many of the ideas)

- F1 Side x price-level asymmetry is huge. ask (sell YES) at fv<20c: 36.9k sh, +2.25c edge, +2.03c markout.
  bid at fv>80c: 41.9k sh, +1.95c / +1.90c. The opposite sides lose: bid at <20c: 21.6k sh, -0.56 / -0.59;
  ask at >80c: 36.6k sh, -0.22 / -0.26. Mid (20-80c): ask +1.11 / +0.95 on 96k; bid -0.24 / -0.33 on 95k.
  Consistent with the tournament mid sitting +1.87c over Polymarket in decile 0 and -1.34c in decile 9 (17.8k / 17k snaps).
- F2 Rival-only half-spreads per market: 10/25/50/75/90th pct = 0.50 / 0.50 / 0.75 / 0.75 / 1.0c; a dozen markets at
  1.25-1.75c (Rep Oklahoma Sen, Rep Maine Gov, Dem Kentucky Sen, Dem/Rep SD, Rep PA Gov, Ind RI Gov). Best bid/ask changed
  in 45% of 60-s intervals (median market), 15-20% in the quietest (IA-03, Maryland Gov, TX-28), 73-82% in Alaska/RI/MN/SD/DE.
- F3 Overnight (00-08 UTC) we quoted 8-15% of markets at 04-07 UTC but rival books stayed <= 1c wide in 38-54% of snapshots:
  rivals are present all night. Yet |mid - Polymarket| doubled overnight (median 1.5-2.0c vs 0.75-1.0c by day) and
  hour 02 UTC had our best fills of the day: 13.5k sh at +2.48c, incl. one 10,000-share Rep U.S. House ask fill (+$302).
- F4 Race consistency: Dem+Rep mid sums median 1.002 (5-95%: 0.98-1.017). Ask-sum < 1.00 in 1,956 race-minutes (5.8%),
  < 0.98 in 853 (2.5%); bid-sum > 1.00 in 1,713 (5.1%), > 1.02 in only 43. The live arb (bids >= 1.03) fired 0 times.
- F5 Idle paired capital: we hold YES on BOTH U.S. Senate legs (7,335 + 7,335) and same-sign pairs in 10+ races,
  17,675 pair-shares in all (~17% of the account doing nothing); 159 positions, 78k gross shares.
- F6 Flow comes in cross-market clusters: 125 clusters of fills in >= 3 markets within 10 s (biggest: 25 fills, 10 markets,
  4,780 sh, bid-heavy, -$48); clusters hold $759 of the $2,287 gross edge. Single-market walks (>= 3 fills in 10 s) lost -$349.
- F7 Probe trades: 222 fills <= 10 shares (46 of exactly 1 share), clustered in markets 891, 841, 902, 905 - bots testing.
- F8 Headline: Rep U.S. Senate 78.7k sh at +0.10c; 82 fills with median gap 9 s (bursts). Rank swung 93 -> 299 -> 93
  between 20:00 and 00:00 (reduce-only + marks): rank is a mark-to-market quantity with big noise.
- F9 15 markets diverge > 2c from Polymarket in > 80% of snapshots (NH Gov Dem +5.4 / Rep -4.8; RI Sen Dem -5.5 / Rep +5.8;
  TX Gov Rep -3.6; SD Sen Dem +3.9); 89 of 229 markets diverge < 10% of the time. Persistence, not noise, in those 15.
- F10 Fill-size distribution: median 100, 90th pct 761, 99th 3,353; 517 of 1,813 fills are exactly 100 shares.

## Ideas

Format: mechanism / evidence / test (go) / risk + fair play / build (S < 50 lines, M 50-150, L > 150).

### A. Where to put the quote (microstructure, queue, fill probability)

1. **Favourite-longshot one-sided quoting.** Below 20c quote the ask at normal size and the bid only at min_edge + 1c
   (or only to reduce); above 80c the mirror. Mechanism: students buy longshots and sell favourites (F1); the mid reverts
   to Polymarket so the good side earns ~2c and the bad side is picked off by the reversion itself.
   Evidence: F1, 137k shares in the four cells, consistent in edge AND markout. Test: re-run the F1 query on each new day
   (3 days); sim variant with per-side edge (sim already has the FL bias). Go: bad-side markout < -0.2c on > 20k sh/day,
   est. +$200/day saved plus freed capital. Risk: one-way inventory (short longshots = long NO near 1; an upset costs ~1/sh)
   - keep Kelly caps; fair play fine. Build S (per-side min_edge in `decide`, setting `fl_bias_edge`).

2. **Fill-probability-priced quotes (not penny-or-join).** For each tick from fv-1c to fv-4c compute P(fill in 10 min)
   from the empirical trade-size distribution (F10) and the shares resting ahead (rival top-3 sizes), and pick the tick
   maximising edge x P(fill). Sweeps of 1k-14k clear whole levels, so being behind a 100-share rival costs ~nothing on
   the flow that pays (fills > 3c = 94% of profit) and saves the 1c penny. Evidence: F10, day-one 8%/94% split.
   Test: books+trades tables -> P(level cleared within 10 min | shares ahead); go if P > 50% for <= 500 sh ahead at
   fv-1.5c. Risk: fewer small fills; fair play fine. Build M (table lookup in compute_quote, nightly recalibration).

3. **Sweep fill -> fast unload at near-fair (the other half of R3).** After a fill at >= 3c edge, post the exit at
   fv -/+ 0.5c for 5 min (not at fv +/- 1c and skewed), sized to the fill. Mechanism: the tournament mid reverts with a
   5-min half-life; realising 2.5c in 5 min beats carrying marked inventory for hours. Evidence: 213/218 reversions;
   sweep fills were +$1,513 of +$1,613 and sat as inventory. Test: fills.csv - time to exit and exit edge of sweep
   inventory today vs simulated fast exit; go if realised >= 70% of entry edge within 10 min. Risk: exits inside
   min_edge give back 0.5c per share; fair play fine. Build S on top of R3.

4. **Same-side refill cooldown.** After 2 fills on one side of one market within 60 s with fv drifting against us,
   pause that side 5 min or widen it 1c. Mechanism: walks are informed or trend flow; re-posting re-feeds it.
   Evidence: F6 walks lost -$349 while cluster/sweep flow paid. Test: fills.csv - markout of the 3rd+ fill in a
   10-s same-market sequence vs the 1st; go if 3rd+ < -0.5c on >= 2k shares. Risk: missing a genuine sweep's tail;
   fair play fine. Build S (per-side `last_fills` deque in Ex).

5. **Rival-floor map -> per-market min_edge.** Measure each market's rival-only half-spread (F2) daily. Where rivals
   sit at 0.5c, pennying is hopeless: quote at 1.5-2c (sweep-only) and spend no writes; where the floor is >= 1.25c,
   quote at 1c and own the top. Evidence: F2 shows a 0.5-1.75c spread across markets; pennying ended at the floor on day one.
   Test: sim with per-market rival floors drawn from F2 vs uniform; variant min_edge_m = clip(floor_m + 0.25c, 1c, 2c).
   Go: >= +100/h over 24 seeds, or live A/B on market halves (+0.3c edge/sh). Risk: fewer fills in tight markets
   (they were ~0 edge anyway); fair play fine. Build S + a rival-stats loader (M).

6. **Writes-per-fill market selection.** 30 writes/min over 150 markets = 1 write per market per 5 min. Markets whose
   top changes 73-82% of minutes (Alaska Sen/Gov, RI Sen, MN Sen, DE Sen) eat writes; quiet tops (15-20%) are free.
   Quote actively where (realised edge x activity) / (writes needed) is highest; elsewhere rest a wide quote and don't chase.
   Evidence: F2 change rates; 409s 439 over 40 h. Test: journal writes per fill per market; go if the top quartile by
   writes/fill shows < 1 fill per 20 writes. Risk: coverage drop (already 78/237 median priced - the bigger problem).
   Build S (priority weights in send_changes).

7. **Rival size reflection: quote at the second level's price with our full size.** Rival top sizes (recorded) show
   caps (100/500). If a rival shows 100 at the touch and we show 2,000 one tick behind, any order > 100 trades mostly at
   our price: we are the effective touch without pennying. Evidence: F10 (median fill 100, 90th 761); needs the books table.
   Test: share of trade volume exceeding the top level's size; go if > 50%. Risk: none beyond fewer 100-lot fills;
   fair play fine. Build S (improve_ticks = 0 when top size < 0.25 x ours).

### B. Exploiting the other bots' rules

8. **Stale-rival taking at 2c after a confirmed Polymarket jump.** take_edge is 5c for 30 s. Rivals with 2-10 s lag
   leave quotes 1-2c stale after a >= 1.5c Polymarket move. Take at 2c when the move is confirmed on 2 consecutive
   5-s readings and that market's measured rival reprice lag > 5 s. Evidence: sim rivals pick us off at 1.5c; mirror it.
   Test: books table - per-market lag from Polymarket move to rival top change; count of >= 2c windows/day.
   Go: >= 20/day with 15-min markout > +1c. Risk: Polymarket noise moves that revert (hence 2 readings); fair play:
   hitting a posted quote at its price is ordinary trading. Build S (conditions in take_stale_quotes).

9. **One-sided-book detection = rival at its inventory cap.** When a market's rival top-3 is empty on one side
   > 5 min, (a) price from Polymarket only (the lopsided mid biases our 30% weight), (b) quote the empty side at 1.5c
   with 2x size: we are the only liquidity for that flow. Evidence: needed (books table); R5 ref-only logic reusable.
   Test: share of market-minutes one-sided and markout of our fills on the empty side; go if > 3% of minutes and
   markout > +1c. Risk: the empty side may be empty because everyone knows something -> keep the ref guard. Build S.

10. **Slow-exchange = competition vanishes: widen, don't shrink.** In 409-heavy minutes rivals' writes also fail, so
    their quotes go stale and ours are the fresh ones; students still send market orders. Burst mode now halves size
    and adds 0.5c; try full size and +1c instead. Evidence: 409 timestamps (439) x fills.csv - compare fill edge in
    slow vs calm minutes. Go: slow-minute edge > calm + 1c on >= 5k sh. Risk: a slow exchange also delays our pulls
    after Polymarket moves - keep pulls first. Build: settings only (burst_size_factor=1.0, burst_extra_edge=0.01).

11. **Overnight drift taking.** Overnight, |mid - Polymarket| doubles (F3) while rivals remain: their references go
    stale or they stop repricing. From 00-08 UTC run a 2.5c take with small size (200-500) and hold for the daytime
    reversion; keep two-sided quotes only on the F1 good sides. Evidence: F3; the day-one reduce-only night hid this.
    Test: snapshots 00-08 UTC - of gaps > 2.5c at t, share half-closed by t+60 min; go if > 65% (day: 213/218).
    Risk: Polymarket itself thin overnight (require Polymarket liquidity flag); fair play fine. Build S.

12. **Rival death/expiry detector.** If rivals use 30-min expiries like ours, a dead bot's levels vanish together at a
    predictable second; the book then widens for everyone. Detect simultaneous disappearance of >= 3 levels of one size
    fingerprint across markets -> set min_edge to 2c there for 30 min (earn the width while it lasts). Evidence: needed
    (books table). Test: count such events/day; go if >= 2/day across >= 10 markets. Risk: nil; fair play fine. Build M.

13. **Probe-trade census as a competition index.** 1-10 share trades (F7) are bots testing. Markets with many probes
    host active bots; markets with none are student-only -> wider quotes hold. Test: trades table - probe count per
    market vs rival top-change rate; go if correlation > 0.5, then feed idea 5 where the books table is thin. Build S.

14. **Twin-leg repricing on the realtime push.** A trade lifting Dem YES to 0.55 implies Rep YES <= 0.45 (F4 sums ~1).
    On the push, reprice the twin within 1 s, before rivals that watch legs independently. Evidence: F4; "edge_hunter"
    style bots poll every 2 s. Test: books+trades - lag from a leg trade to the twin's top change; go if median > 3 s
    and the twin moves >= 0.5c in 60% of cases. Risk: writes (cap to >= 1c implied moves); fair play fine. Build M.

### C. Structure and consistency

15. **Buy-both-legs arbitrage (ask-sum < 1 - 1.5c).** The live arb only sells when bids sum >= 1.03 (never fired).
    Ask sums < 0.98 occur in 2.5% of race-minutes (F4). Buy every leg at the asks (include Ind legs), lock >= 2c/pair at
    settlement, and unwind earlier by selling the pair when bids sum >= 1.00 (also 5% of minutes). If marks use mids
    (sum 1.002) the gain shows immediately. Test: books table - opportunities/day with >= 100 sh on every leg lasting
    > 3 s; go if >= 20/day (~$40/day risk-free, capital ~1k). Risk: capital tied until unwind; a race where no listed
    leg wins (write-in) - check contract wording. Fair play fine. Build S (mirror of execute_arbitrage).

16. **Pair unwinder (capital efficiency).** 17,675 pair-shares held same-sign (F5), incl. 7,335 of both U.S. Senate legs.
    Sell both legs when rivals' bid-sum >= 1.00 (both-long) or buy both when ask-sum <= 1.00 (both-short): zero P&L,
    frees ~17k (17% of the account) for quote sizes. If the exchange marks at the bid, it also lifts account value.
    Test: measure how the API values holdings (idea 25) and count unwind windows/day in the books table; go if >= 5/day.
    Risk: none; fair play fine. Build S (inventory condition in the arb path).

17. **Hedge, don't block, the national swing.** party_blocks removes one side in many races when party_delta is large
    (-8.3k Rep House now). Instead place a passive hedge in the 1c-wide headline market (mid - 0.5c) sized to bring
    delta back under the cap; cost ~0.5-1c/share once vs edge forgone every minute in blocked races. Test: journal -
    side-minutes blocked by party_blocks and their markets' edge/sh; go if blocked minutes > 10% in busy markets.
    Risk: headline inventory itself (counted in the correlated model); fair play fine. Build M.

18. **Cross-leg hedge on fill.** After a fill long Dem at 0.40, post an ask on Rep YES at 1 - 0.40 - 0.5c (pair cost
    0.995) for 5 min: the directional fill becomes a locked pair at 0.5c cost instead of marked inventory. Different
    from "quote the less-competed twin": it is a post-fill conversion. Test: books - how often the twin's best bid/ask
    is within 1c of 1 - fill price within 60 s; go if > 50%. Risk: double writes; fair play fine. Build M.

19. **Persistent-divergence regime per market.** In the 15 markets of F9 our 70/30 blend sits ~3c from the tournament
    consensus; we are the best quote for the consensus flow and build inventory marked against us. When |mid - ref| > 2c
    for > 2 h, stop quoting the side that buys against consensus, keep the side that sells toward it, size 0.5x.
    T1 (EMA gap) failed in a sim where Polymarket is truth; this is about marks, not fair value. Test: fills.csv per
    market in the F9 set - markout by side; go if the consensus-facing side < -0.5c. Also check Polymarket liquidity
    there (thin Polymarket is the likely cause). Risk: forgoing settlement edge that mostly lands after 4 Nov anyway. Build S.

20. **Mark at the rival mid, quote at the blend.** Compute inventory risk and skew from (rival-only mid - fv) x inventory:
    skew harder when the tournament marks a position against us, hold when it marks in our favour. Rank and Smart Score
    use tournament prices (F8 rank swings). Test: the sim already reports P&L at the tournament mid; go if drawdown at
    the mid falls 20% with P&L at Polymarket unchanged. Risk: more churn; fair play fine. Build S.

### D. Sizing, toxicity, capital

21. **Size by realised markout per market.** Weights = sqrt(activity) x clip(markout_m(3 days) / 1c, 0.3, 2).
    Rep U.S. Senate (+0.1c on 78k) shrinks, Rep U.S. House (+2.2c on 11.7k) and small Senate races grow.
    Test: replay fills.csv with yesterday's markout as today's weights; go if edge/sh +15% at equal volume. Risk:
    chasing one lucky day (3-day window, clip). Build S (update_size_plan).

22. **Headline markets: sweep-only, no touch.** 843 is 4 fast rivals at 1c; our 10,000-share touch is the sweep target
    for everyone at +0.1c. Replace with resting levels at +/-2, 3.5, 5c (3k/3k/4k) plus consistency quotes derived from
    the twin (Dem+Rep = 1). Test: sim headline kind, touch off/ladder on; go if >= +50/h and markout > +1c. Risk:
    10k positions marked through a 3c national swing (counted in the correlated model). Build: part of R3.

23. **Age-weighted skew (inventory half-life <= 4 h).** skew = 0.5c x inv/size x (1 + age_h/4), never through fair.
    A 33-day horizon means positions held days carry mark variance ~ sqrt(days) with no extra edge. Test: fills.csv -
    positions older than 6 h: MTM swing vs entry edge; go if swing > 2x edge. Risk: giving back edge on old inventory;
    fair play fine. Build S.

24. **Flow-imbalance size boost (fade the burst).** After one-way tournament flow lifts the mid >= 2c above Polymarket
    in 5 min, double the size on the fading side for 10 min (fair value already says it is cheap). Test: trades table -
    P(half-reversion within 10 min | imbalance > 500 sh); go if > 70%. Risk: the 5 of 218 non-reverting episodes are
    news - require no Polymarket move in the same direction. Build S.

### E. Rank, Smart Score, end of tournament

25. **Find the exchange's mark.** Regress API totalMarketValue on our positions x (mid | bid | last trade) from the
    snapshots. The "holdings cannot be valued" 409 hints at last-trade or non-empty-book marks. Everything in C/E
    depends on it. Measurement only (1 h of analysis); feed the answer to 16, 19, 20, 27.

26. **Leaderboard gap tracking.** Log the top-10 account values hourly. If #3 is +40% (a lucky directional bettor),
    steady +0.5%/day (+16% by 4 Nov) cannot reach the prize and the objective is Smart Score and rank-100 stability;
    if #3 is +8%, a measured variance plan matters. Measurement S (one leaderboard call/hour, already fetched).

27. **Smart Score as P&L / volume: drop zero-edge volume.** Code comments say the score uses ROI (P&L / volume) and win
    rate. One third of our volume (843) earns 0.02-0.1c/sh and dilutes ROI. Raising headline min_edge to 2c or going
    sweep-only (22) cuts volume 30% with ~0 P&L loss. Test: compute P&L/volume with and without 843 from fills.csv
    (expect ~1.5x). Go once SIG confirms the definition. Build: setting.

28. **Decay-aware scaling.** "smartScoreDecayed" implies recent weeks weigh more; students' volume also peaks late
    October. Schedule: modest sizes now, size_max_frac and quote_capital_frac up ~1.5x from ~20 Oct, back down for the
    election-night flatten. Test: trades table volume week-over-week; ask SIG for the decay. Build S (date multiplier).

29. **Election-night one-sided making in called races.** Once Polymarket has a race at >= 0.97 (called), make a
    one-sided market on the tournament side: sell the loser's YES at 3-5c, buy the winner's at 95-97c; students trade
    emotionally there. Needs the end-valuation answer (if unresolved positions are marked at the last tournament price,
    the 2-h hard exit is unnecessary and costs the widest spreads of the month). Test: SIG rules; dry-run plan.
    Risk: a mis-called race; cap at 2% of account per race. Owner decision. Build M.

30. **End-valuation hedging via pairs.** If SIG marks unresolved positions at mid or last price, hold only locked pairs
    and near-certain legs into the close; if at cost or zero, flatten everything 12 h out (current plan). Prepare
    `end_valuation_mode` = mark | flatten | zero so the switch is one setting. Build S; decision pending SIG.

### F. Robustness and ops

31. **Trade-push cancel within 1 s.** On a pushed trade in market X at >= 1c through our resting quote's side (a sweep
    toward us), reprice/pull within 1 s rather than at the next Polymarket refresh: the fastest bot picks off the slowest.
    Test: trades table - share of negative-markout fills within 5 s after a tournament trade >= 1c through our quote;
    go if > 30%. Risk: write budget (cap at 3/min for this trigger). Build M.

32. **Cluster-aware response.** When 2 fills arrive from a cross-market cluster (F6) within 2 s, the actor usually
    continues. Decide by data whether to widen the hit side 1c in not-yet-hit markets or to add size: fills.csv -
    markout of cluster fills vs singles (go either way if |diff| > 0.5c). Build M (needs realtime fill handling).

33. **Write budget as expected-value auction.** Score each candidate write by (edge lost if stale) x P(fill) x size and
    skip reprices in markets where rivals re-undercut within 10 s (measured). Evidence: 439 x 409, deferrals every
    cycle at 08:09-08:12. Test: books table - median time to re-undercut after our improvement; go if < 15 s in > 50%
    of markets (then stop improving there, saving ~30% of writes). Build S.

34. **Coverage before cleverness.** Median priced 78/237, 90th pct 127, with 229 references: ~100 markets earn nothing.
    Find why (book staleness, ref-only 3c gap, missing book fetches) - the F1 good sides alone in 100 extra markets at
    100 shares are worth more than most ideas here. Test: snapshot rows with fair_value null and reference not null,
    by cause; go if > 50 markets are recoverable. Build S-M (diagnostic first).

### G. Wild

35. **Be the reference for the student bots.** Several published bots blend "SIG mid" into their fair value. A stable,
    tight two-sided quote that never moves except with Polymarket makes OUR quote their input; they then sit 0.5c inside
    us and absorb the toxic flow first while we take the sweeps behind them. Test: books table - do rival quotes move
    after ours more than after Polymarket? (lagged correlation); go if our-move -> their-move lag < their Polymarket lag.
    Fair play: genuine two-sided quotes at our fair value, no spoofing. Build: none (measurement of an existing effect).

36. **Liquidity of last resort pricing.** Students' market orders walk books to 0.5-1.4 past the mid (!). A single
    deep level per side at fv +/- 10c with 1,000 shares in the 40 busiest markets costs 80k x (lock) - too much capital
    unless sized by overshoot frequency per market. Test: trades table - trades >= 8c through the pre-trade mid per
    market per day; go for any market with >= 1/day (one such fill = $80-100). Build S inside R3's ladder.

37. **Rate-limit-free observation account? No.** Multiple accounts are banned; noted only to close the door.

38. **Random reprice jitter.** Rivals that reprice on fixed 2-s polls can be measured and then systematically beaten
    by repricing 0.3 s after their poll; our own fixed cadence can be read the same way. Add +/-30% jitter to our
    cycle timing and to the churn window. Test: autocorrelation of our top changes in the books table (we appear too).
    Build S; risk nil.

## Top 10 by expected value (bot earning ~+$500/day)

1. **#1 Favourite-longshot one-sided quoting** - the cleanest signal in the data: +2c on the good sides, -0.2 to -0.6c on
   the bad ones, 137k shares; est. +$200-400/day and lower adverse selection. S build.
2. **#34 Coverage diagnostic** - 100 markets earn nothing; even 100 sh at +1.5c on the good side each is +$100-300/day.
3. **#3 Sweep fill -> fast unload** - turns the only reliable profit (sweeps) into realised cash within the 5-min
   reversion, cutting MTM drawdown; multiplies R3's value.
4. **#5 Rival-floor map -> per-market min_edge** - stops pennying where it is hopeless and owns the top where rivals
   are wide (a dozen markets at >= 1.25c); saves writes too.
5. **#21 Size by realised markout** - moves capital from the +0.1c headline churn to +1-2c markets; S build.
6. **#11 Overnight drift taking** - the gap doubles at night and one night fill made +$302; ~8 idle hours/day today.
7. **#16 Pair unwinder** - 17k of dead capital back into quotes at zero P&L; plus possible mark uplift.
8. **#19 Persistent-divergence regime** - 15 markets where our blend is structurally wrong for the MTM objective.
9. **#8 Stale-rival taking at 2c** - a measured lag turns the pick-off game around; needs the books table.
10. **#15 Buy-both-legs arbitrage** - small (~$40/day) but risk-free and 20x more frequent than the live sell-side arb.

Honourable: #10 (settings only, test today), #4 (S, stops the -$349 walks), #27/#26 (decide the objective).

## Measurements to add to the recorder

- Per fill: tournament best bid/ask and rival top-3 sizes at fill time, seconds since the last tournament trade and since
  the last Polymarket move in that market, cluster id (fills within 10 s), position age; markout at 5/15/60 min.
- Per market per day: rival-only half-spread, top-change rate, probe-trade count, one-sided-book minutes, median time to
  re-undercut after our improvement, Polymarket liquidity/volume (Gamma fields) for the F9 persistent-divergence set.
- Hourly: leaderboard top-10 values and our rank; API totalMarketValue alongside sum(pos x mid), sum(pos x bid),
  sum(pos x last trade) to identify the mark; Smart Score fields once scored.
- Pair opportunities: per race-minute, bid-sum and ask-sum with the sizes at those levels (not just prices).
- Our own writes: per market, writes sent, reprices skipped by churn control, 409/429 counts, and fills per write.
- Realtime feed: timestamp of each pushed trade vs our next top change (reaction time), and rivals' reaction time.
