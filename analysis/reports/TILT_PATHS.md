# The tilt over the next 30 days: likely paths and how to make money on each (5 Oct 2026, ~15:30 UTC)

Owner: "betting on the tilt is the way to make money in this now: guess the likely paths of the tilt over the next 30 days and how to
make money off it." This is a judgment call with numbers attached, not a model output. Data: the recorder snapshots to 4 Oct 15:57
(/home/claude/snap04), the owner's live readings (tilt_s 0.137 at 15:56 and 0.128 at 20:00 on 4 Oct, 0.109 at 10:21 on 5 Oct), the
literature sweep (analysis/p11/LIT_REVIEW.md A3/A4/F1), FIELD_ACTIVATION.md. The settlement fact that governs everything below: SIG
pays positions out at the OUTCOME, so being SHORT the tilt (favourites long / longshots short) earns the whole tilt whatever path it
takes, and being LONG the tilt only pays if someone buys the position back at a higher mark before the close.

## 1. Where the tilt is and how it got here
s = the slope of (Polymarket - tournament mid) on (Polymarket - 1/legs), non-headline markets, other traders' touch, spread <= 6c
(the Package 13 C estimator; the bot's live `tilt_s` reads ~0.9 point higher because it includes headline / wide markets).

| 12-h bin (UTC) | s | sub-5c longshot best bid | >95c favourite best ask | note |
|---|---|---|---|---|
| 1 Oct 12 | 0.026 | 3.1c | 0.968 | first week: ~fair |
| 2 Oct 00 | 0.052 | 4.7c | 0.956 | |
| 2 Oct 12 | 0.061 | 5.0c | 0.952 | |
| 3 Oct 00 | 0.083 | 6.4c | 0.942 | |
| 3 Oct 12 | 0.108 | 8.3c | 0.927 | +2.5 pts/day |
| 4 Oct 00 | 0.125 | 9.9c | 0.912 | |
| 4 Oct 12 | 0.128 | 10.1c | 0.907 | decelerating (+0.07/4 h) |
| 4 Oct 20 (live) | ~0.12 | | | PEAK so far |
| 5 Oct 10 (live) | ~0.10 | | | first sustained fall: -2 pts in 14 h |

Facts behind it: 1,087 accounts on 4 Oct (+2.7/h on Sunday, +6/h on Friday); fewer than 20% have traded; ~870 untouched at exactly
100k; the marginal longshot price is set by NEW buyers (the lottery that produced the +600% leader); the sellers of longshots are
capital-bound (a 10c short locks 90c for 7c of edge; we hit our own risk caps twice on 4 Oct); longshot bid depth is deep (bid sums > 1
in 82 of 117 races), the ask side thin. The 5 Oct fall came with Package 14 freeing our refill and with Monday-morning activity;
whether it is profit-taking by the early longshot buyers, new value sellers, or just the weekend wave ending is not identifiable from
our data.

## 2. What moves it from here
- UP: activation of the 80% (the leaderboard as social proof, campus deadlines, the election getting close, SIG mailings); the
  contest structure (3 prizes, no downside) rewards the longshot lottery, and laggards buy longshots LATE (contest theory, A3); the
  last 24-72 h before resolution see the biggest mispricing jump in PredictIt-type markets (Restocchi 2019).
- DOWN: profit-taking by longshot holders who lead on marks (the leader hedging into our bids); capital freed on the sell side (us:
  Packages 14.1 / 14.2, the swaps; other value accounts); new entrants who are sophisticated (the SIG audience is quant students:
  some will arb); Polymarket itself moving toward 0/1 as races firm up (the tilt is measured against it, so this is neutral to s
  but shrinks the DOLLAR edge on legs that resolve early).
- EXOGENOUS: SIG changing the rules or intervening; the trading hours after 00:00 UTC on 4 Nov (SIG confirmed: open to 17:00 UTC).
- SEASONALITY in our 4 days of data: Friday evening and the weekend brought the strongest rises (3 Oct 12 -> 4 Oct 00: +4.2 pts),
  Monday morning the fall. Expect weekly waves of +-2-4 points on top of the trend.

## 3. Four paths to 4 Nov (my probabilities; they sum to 100)
| path | P | s by mid-Oct | s late Oct | last 72 h | story |
|---|---|---|---|---|---|
| A. Range with waves | 40% | 0.08-0.13 | 0.08-0.13 | spike to 0.15-0.20 | buyers and sellers roughly balanced; weekends up, weekdays down; the lottery returns in the final week |
| B. Second leg up | 25% | 0.15-0.20 | 0.20-0.30 | 0.25-0.35 | a real activation wave: a quarter of the inactive join and buy like the first 20%; sellers stay capital-bound |
| C. Reversion | 25% | 0.06-0.09 | 0.03-0.06 | small bump to 0.06-0.08 | profit-taking + freed sell-side capital + arb entrants; prices converge toward Polymarket |
| D. Collapse / intervention | 10% | < 0.05 | ~0.02 | - | SIG acts, or the whales dump; the tilt is gone and does not return |

Why not more weight on B: the 5 Oct fall is the first evidence that the sell side can win a day; the deceleration on 4 Oct was already
visible before it; and every account that has traded is already in. Why not more on C/D: nothing forces convergence before resolution
in a play-money contest with a lottery payoff, and 80% of the field has not acted yet.

## 4. How to make money on each path (settlement at the outcome)
### 4.1 The instrument that pays on EVERY path: sell the tilt and hold to the outcome
Edge per $ of collateral for a longshot short (or the mirror favourite long) in a 2-leg race, by s:

| s | 2.5c longshot priced at | edge per $ | 5c longshot | 10c longshot |
|---|---|---|---|---|
| 0.05 | 4.9c | 2.5% | 2.4% | 2.3% |
| 0.10 (now) | 7.3c | 5.1% | 5.0% | 4.7% |
| 0.15 | 9.6c | 7.9% | 7.6% | 7.1% |
| 0.20 | 12.0c | 10.8% | 10.5% | 9.8% |
| 0.30 | 16.8c | 17.1% | 16.6% | 15.4% |

So a dollar of collateral sold at s = 0.20 earns twice what it earns today, and the same dollar sold at s = 0.05 earns half. The whole
game for a value book is TIMING the sale of its collateral against the path, and it never loses the tilt it has already sold. The
30-day EV of the ~80k of collateral we can keep deployed, by path, if sold at that path's average s: A ~4.5-5.5k, B ~8-10k, C ~2.5-3k,
D ~1.5k (plus what is already locked: ev_outcome 108.3k today).

### 4.2 The bet the owner has in mind: LONG the tilt (buy longshot YES, sell it later)
A longshot at p = 0.05 bought today at 10.0c (mid at s 0.10 plus a 1c half-spread) and sold at the later mid less 1c:

| s later | 0.03 | 0.06 | 0.10 | 0.15 | 0.20 | 0.25 |
|---|---|---|---|---|---|---|
| return on the sleeve | -41% | -28% | -10% | +13% | +35% | +58% |

Path-weighted with the probabilities above (A: sold into the final-week spike at ~0.17: +20%; B: +45%; C: -30%; D: -45%):
0.40 x 20 + 0.25 x 45 - 0.25 x 30 - 0.10 x 45 = +8 - 7.5 + 11.25 - 4.5 = **+7% expected**, with a 35% chance of losing a third or
more, and TWO execution problems the table ignores: (1) the exit has to find a buyer at the top (longshot ASK depth is thin: we are
the natural sellers), (2) a position held into the outcome is worth p, i.e. -50% to -80% of cost - the sleeve must be sold, not
settled, so the kill-switch and the date switch are the real risk controls. Break-even P(B) is about 20% at these numbers; the
bet is marginal, and it is a bet on OTHER traders' behaviour, not on the election. Hence: the Package 13 momentum sleeve is right to
be ARMED and TRIGGERED (buy only while the 24-h slope is clearly positive, 10k steps, flip on slope <= 0 / profit target / date), not
forced. With the 24-h slope negative today (0.128 -> 0.109) it would not buy, and that is the correct call for a momentum rule.

### 4.3 The better way to be "long the tilt": keep the option to sell it higher
Unsold collateral is a free call on s. 40k of dry powder (cash + risk room) sold at s 0.10 earns ~2.0k; at 0.15 ~3.1k; at 0.20 ~4.3k.
Waiting costs the carry on 40k for the days it sits (MM carry today ~0-1.5k a day on the whole book, so ~0.2-0.5k a week on 40k) and
risks C (sold later at 0.06: ~1.0k). Under the path weights, selling in TRANCHES keyed to s beats selling everything today by roughly
+0.8-1.2k and beats holding everything for the final week by its variance. The mechanics already exist:
- the HARVEST LADDER (Package 13, `tilt_harvest_ladder`): asks at +0 / +2 / +4 / +6c above the touch on longshots and bids below on
  favourites, 3k a level, edge >= 8% per $: it sells into every wave automatically and sits idle when s falls - this is the tranche
  rule in order form; it should be the first Package 13 flag to go live, on its own, with everything else off;
- the allocator's swaps (14.1 / 14.2): on path C the converged positions are recycled into the remaining 10-13% edges (RI / CT / DE safe
  seats), ~+0.5-0.7k per run today, more as more converges;
- `alloc_max_edge_sell` 0.05 + `alloc_swap_sell_margin` 0.03: sells the converged (path C's gift) and keeps the floor on the rest.

### 4.4 Per path, what to do
- A (range with waves): the ladder sells the weekend waves; the allocator swaps the Monday convergences; keep 15-20k of room for the
  final 72 h (the spike) and the election-night holdback. Momentum sleeve: triggered buys on the up-waves if the 24-h slope confirms,
  flipped on the slope turning - small, bounded; expect it to be a wash.
- B (second leg up): the ladder fills at +2/+4/+6c as s runs (that IS the edge doubling); the sleeve triggers and ramps 10k per
  confirmed 6 h and flips at the profit target or slope <= 0 - this is the only path where the sleeve pays (+35-60% on 10-40k);
  the rank falls on marks for weeks (say so in the summary; do not sell value to marks); release dry powder in tranches at s 0.15 /
  0.20 / 0.25 (`FIELD_ACTIVATION`), the last tranche for the final 72 h.
- C (reversion): the sleeve never triggers (or flips at once); the ladder rests unfilled; the swaps and the MM middle book are the
  earners; move `alloc_max_edge_sell` to 0.05-0.08 as positions converge; the value book's EV is unchanged (108k) - the money was
  made on the way in.
- D (collapse): everything already sold is kept to the outcome (ev_outcome unchanged); stop adding (the hurdle does that); the rank
  improves at once on marks. Nothing to do.
- ALL paths, election week: the 20k holdback and the called-race takes (Package 13 `election_night`, SIG confirmed to 17:00 UTC) are
  worth +5-15k on their own; nothing else in this document is as large or as path-independent.

## 5. Daily signals that tell the paths apart (watch these, in this order)
1. slope_24h and slope_6h of s (status `momentum` once Package 13 C is live; until then `tilt_s` in status.json vs 24 h ago): B =
   slope_24h >= +1 pt/day for two days; C = <= -1 pt/day for two days; A = sign changes within the week.
2. New accounts per hour ("of M" in the rank line) and the Smart Score denominator (active share): activation above ~8/h on a weekday
   or the active share jumping 5 points = B is starting.
3. Sub-5c longshot best-bid average and longshot ASK depth: bids fattening with thin asks = B; asks thickening = C.
4. Bid sums > 1 per race (riskless set selling available): more Dutch books = B.
5. The leader's mark P&L: a falling leader with a rising s means early buyers are selling = the top of a wave.

## 6. Recommendation (what to turn on, in order)
1. Packages 14.1 + 14.2 (refill and swaps unstuck; `alloc_max_edge_sell` 0.05, swap margin 0.03, `value_sell_margin` back to 0.005).
2. The harvest ladder alone from Package 13 (`tilt_harvest_ladder` on, `aggressive_value` and the buckets OFF at first): it is the
   tranche rule and it is path-neutral.
3. Package 13 C's momentum sleeve ARMED (`momentum_auto` on, `momentum_force` off) - it buys only on a confirmed up-move and bounds
   the long-tilt bet to 10k steps with an automatic flip; never force it: the forced 40% version is the -EV end of the table above.
4. `election_night` on before 3 Nov with the 20k holdback; `close_override_utc` 2026-11-04T17:00:00Z.
5. Keep 15-20k of risk room and cash for the final 72 h on top of the holdback.
Expected 30-day result by path (value book already at 108k EV): A ~+6-8k, B ~+10-14k, C ~+3-4k, D ~+2k, plus the election night;
weighted ~+7k, with the chance of a loss confined to the sleeve (bounded by its kill at 75% of a 10-40k cost).
