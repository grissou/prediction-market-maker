# Explorer Z: recycling mechanisms and fresh edge in snap04 (data to 4 Oct 15:57 UTC)

Read-only. Scripts in `analysis/p12/`, each runs in 2-3 s on the snapshot:

| Script | What it measures |
|---|---|
| `Z_flip.py` | Take-and-flip: reversion after the 452 journal TAKEs, by band |
| `Z_dutch.py [sync_s]` | Dutch-book capacity. A: books, 3 levels, own levels stripped, legs synchronous within `sync_s`. B: snapshot-synchronous bid sums. C/D: YES+YES carousel, with sizes |
| `Z_tilt_quotes.py` | Hourly s and richness by band; top 20 edge-per-$ at 4 times; value-mode maker fills; 0.5c undercuts; the 20-share bot |
| `Z_field_hedge.py` | Leaderboard path; control contracts against seats; longshot bid depth by hour; hedges that free backstop room |
| `Z_exitfair.py` | How often a held value position can be exited within x of p |

**Conventions:**
- p is the recorder reference, race-scaled (X_common).
- "Edge per $" is (p − ask)/ask for buys and (bid − p)/(1 − bid) for shorts.
- SIG pays at the outcome, so every number below is EV at the outcome unless it says otherwise.

**Headline:** every measurement points the same way. The tilt is persistent, and it widened again today, so nothing
bought at an edge comes back toward p before 3 Nov. Cash used for value turns over once. The recycling streams I
could measure add up to **under 0.3k a day**.

---

### Z-1. Take-and-flip: rest an exit at take price + half the edge after every stale-quote take
Mechanism: if the tournament touch reverts toward Polymarket after a take, a resting exit sells the position back
(take price + edge/2) and frees the cash for the next take.

Data check (`Z_flip.py`): 432 of the 452 TAKEs have edge > 0.5c against race-scaled p. They used **$197k of cash** for
**$18.0k of edge**.
- Takes at p > 0.85 (204, edge $7.9k) and p < 0.15 (183, $7.9k): a rival's opposite touch reached the half-edge
  target in **0 of 383 cases within 1 h and 0 of 349 within 6 h**.
- The gap between the stale side and p closed by a mean of **+1% / +6% (longshots)** and **−1% / −3%
  (favourites)** at 1 h / 6 h. So it stayed open or widened.
- Middle band (45 takes, $2.2k of edge): 0 of 45 flipped within 1 h, 7 of 35 within 6 h, mean gap closed 18% at 6 h.
- Cash that could be flipped within 6 h: $2.9k. Half-edge kept: $129 in 17 h, which is **about $180/day**.

Upside: about 0.2% a month (range 0-0.5%) | P(≥150k) effect: none | Ruin / drawdown: none. The exits simply never fill.
End-valuation: if positions settle at the outcome, keeping the takes is worth more than flipping them anyway.
Legitimacy: ordinary resting limit orders.
Build: about 40 lines (`take_flip_frac`). **Do not build.** The reversion it relies on does not happen.
Screen: data-only. Done; it fails.

### Z-2. Middle-band-only flip on a 6 h horizon
Mechanism: the only band that reverts at all is 0.15-0.85. Flip middle-band takes only, with a 6 h time-to-live.

Data check (`Z_flip.py`): 7 of 35 eligible middle-band takes reached take + edge/2 within 6 h. That is 20% of $25.5k
of cash, so about $5k recycled per 17 h, and about $0.1k/day of edge kept. The other 80% stay as value positions.

Upside: about 0.1% a month | P(≥150k): none | Ruin: none.
End-valuation: indifferent.
Legitimacy: ok.
Build: about 25 lines on Z-1's hook, middle band only.
Screen: data-only. Marginal, so leave it off the build list.

### Z-3. NO+NO sets as a backstop-free cash sink (not a carousel)
Mechanism: a set is short every leg of a race at bid sums ≥ 1.02. Its worst_case_loss is 0, so it uses no
sum-of-maxima room. It is the one riskless place to park cash while the 0.85 backstop blocks value adds.

Data check (`Z_dutch.py`):
- Snapshot-synchronous count: bids sum ≥ 1.02 in **82 of 117 races** today. **188 episodes lasted ≥ 30 min**, for
  example Alabama Senate: Rep bid 0.925 + Dem bid 0.10 = 1.025 all morning.
- Books with legs synchronous within 20 s, 3 levels: 101 episodes a day and an upper bound of $10.6k a day of gap ×
  depth. That sweep holds about $274k of locked cash.
- **Dissolve (asks sum ≤ 0.99) followed a build in 0 of 67 episodes**, and 2 of 166 at a 90 s sync. The tilt keeps
  bids summing above 1, so a set stays locked until settlement.
- Return: **2-3% per $ of cash, once**, against about 10-14% per $ for value adds (top-20 median 0.12-0.14).

Upside: on 10k of cash about +0.25k in total, not per day | P(≥150k): none | Ruin: none (riskless at settlement).
End-valuation: it pays at the outcome either way.
Legitimacy: ok. These are standing rival orders.
Build: config only (`arb_enabled` true, `arb_min_profit` 0.02). **Use it only for cash the backstop leaves idle.**
Screen: data-only.

### Z-4. YES+YES carousel: buy every leg at asks sum ≤ 0.99, sell at bids sum ≥ 1.00
Mechanism: the reverse set, which does recycle. Bids sum > 1 is common, so the exit leg is easy to find.

Data check (`Z_dutch.py` C/D):
- 243 buy episodes in 39 races (TX-28 34, KS Gov 19, SC Gov 17). An exit at bids ≥ 1.00 followed for 103, after a
  median of 1.9 h (p75 4.4 h). Median gain is 1c per set.
- **Sized from the books, the cheap asks are tiny:** median 100 sets. 17 sized trips came to $33 in total, so about
  **$50/day at 100% capture** and $12/day at 25%.

Upside: ~0 | Ruin: none.
Build: none. **Dead.**
Screen: done.

### Z-5. Synthetic favourites: enter a favourite by shorting the longshot whenever bid_long + ask_fav > 1
Mechanism: in a two-leg race, shorting the longshot at its bid b gives the same payoff as buying the favourite at
1 − b. Because bid sums run 1.02-1.03 across 82 races, 1 − b is often 2-4c below the favourite's ask. The cash locked
is the same (1 − b per share).

Data check: Alabama Senate 05:00 has the favourite's ask at 0.935 and the synthetic at 1 − 0.10 = **0.900**, 3.5c
cheaper. The 188 persistent ≥ 1.02 episodes are all cases like this. At 15:55 the top-20 list already holds 4 such
shorts: Rep RI Sen 0.155/p 0.008, Rep MA Gov, Rep NC Sen, Dem NH Gov.

Upside: about +3c per share on every favourite add, so about +3% per $ on top of the 12-14%. On 20k of adds that is
+0.6k one-off.
P(≥150k): small + | Ruin: same as buying the favourite.
End-valuation: indifferent.
Legitimacy: ok.
Build: `alloc_prefer_short` (on since 15:46) does this for the allocator. **Extend it to the take path and the value
quotes** (`take_prefer_synthetic`, about 30 lines in the take loop: compare 1 − bid_other with ask_self).
Screen: data-only. Count `alloc_prefer_short` choices in the 5 Oct journal.

### Z-6. Time value adds to the tilt's intraday peak (07-09 UTC)
Mechanism: s is not monotone within the day. Buying favourites and shorting longshots at the peak gets the cheapest
entries.

Data check (`Z_tilt_quotes.py`, OLS through the origin on two-leg races):

| Time (UTC) | s |
|---|---|
| 3 Oct 22h | 0.132 |
| 4 Oct 00h | 0.136 |
| 04h | 0.147 |
| **07-09h** | **0.150** |
| 11h | 0.143 |
| 13h | 0.139 |
| 15h | 0.144 |

The 0.011 swing is worth about 0.5c on a 0.95 favourite. The Dutch-book gap × depth is also largest at 04-06h:
4.3-4.9k per hour at ≥ 1.02, against 0.3-1.5k at 10-13h.

Upside: +0.5c per share, about +0.5% per $ added; on 20k, +0.1k | Ruin: none.
End-valuation: indifferent.
Legitimacy: ok.
Build: config only. Allocator turnover cap by hour (`alloc_hours` 04-09), or manual.
Screen: data-only. One day only, so it may be noise.

### Z-7. Put the working cash into the favourite-bid value ladder, the best maker stream today
Mechanism: value-mode bids at p − hurdle on p > 0.85 favourites are hit quickly by sellers, at a solid edge.

Data check (`Z_tilt_quotes.py`), maker fills 11:10-15:56, takes and arb excluded:

| Band, side | Fills | Shares | Edge | Rest before fill |
|---|---|---|---|---|
| **Favourite bids** | **117** | **10.7k** | **+4.1c, +$444** | 1 min |
| Middle asks | 58 | 3.4k | +1.9c, +$65 | 3 min |
| Middle bids | 23 | 1.2k | +3.4c, +$42 | 3 min |
| Tail asks | 18 | 1.4k | +6.6c, +$92 | 4 min |

The favourite bids ran at about $2.2k of EV a day per **$48k of cash a day** (4.6% per $). It is also locked to the
outcome.

Upside: one-off, about +4.6% per $ committed. On 15k, +0.7k in total | Ruin: concentrated favourites, bloc.
End-valuation: needs the outcome valuation.
Legitimacy: ok.
Build: config only. Keep `value_quote_hurdle` 0.05 and point `alloc_mm_reserve` cash at favourite bids.
Screen: done on 4.75 h; re-measure on 5 Oct.

### Z-8. A larger longshot ask ladder (maker shorts of rich longshots)
Mechanism: longshots are now **+7.9 to +8.6c rich** (p < 3c band, up from +7.0c at 22h). Tail asks filled at
+6.6c per share. Resting larger short ladders above the ask, with the value floor, collects that.

Data check (`Z_tilt_quotes.py`):
- The 0-3c band went **+7.0 → +8.6 → +8.1c** and the 3-8c band +6.4 → +7.6 → +7.1c (mid − p).
- Rival longshot bid depth (p < 0.10, `Z_field_hedge.py`) is **$6.0-10.5k per book sample**: highest at 09-11h and 14h
  (9.8-10.5k), lowest at 04h, 06h and 15h (6.0-6.5k).
- Only 1.4k shares filled in 4.75 h.

Upside: per $ of NO cash about 7-9%. Capped by backstop room (a short longshot carries worst ≈ 0.9 per share) |
Ruin: an upset in a single race, small.
End-valuation: needs the outcome valuation.
Legitimacy: ok.
Build: config only (ask-side size in value mode).
Screen: data-only.

### Z-9. Stop funding middle-band two-way quoting with scarce cash
Mechanism: the carry's prerequisite is both sides of the same market filling. That almost never happens, so cash in
middle quotes earns as single-sided value at 2-3c.

Data check (`Z_tilt_quotes.py`): **1 of 30** middle-band markets with maker fills had both sides filled in
11:10-15:56. Middle fills were +1.9c (asks) and +3.4c (bids) per share, on 4.6k shares in total, against +4.1c on 10.7k
favourite shares.

Upside: reallocating about 19.5k of resting-quote cash from the middle to favourite/tail ladders adds about
+1.5-2c per share. That is about +0.3k one-off | Ruin: lower adverse selection.
End-valuation: indifferent.
Legitimacy: ok.
Build: config only (narrow the quoting band or set the middle band's size to 0 in value mode).
Screen: the `mm_carry_24h` reading on 5 Oct decides.

### Z-10. Sell held middle-band inventory into through-p undercutters (the only recycling that is not riskless-set)
Mechanism: when a rival posts a bid above p on something we hold long (or an ask below p on something we hold short),
exiting there frees cash at a gain. That cash then funds a 12% level.

Data check (`Z_tilt_quotes.py`):
- The touch is exactly 0.5c better than our quote in **4.4-7.0%** of our quote-cycles per hour: 320 of 4,669 at 11h,
  379 of 5,430 at 15h.
- The undercutter is through p in **151 / 68 / 29 / 84 / 98** cycles at 11-15h.
- The old 20-share bot is **gone**: at most 5 of 2-3.5k top levels per hour hold exactly 20 shares.

Upside: unknown size, probably 0.1-0.2k/day | Ruin: none (exit at ≥ p).
End-valuation: indifferent.
Legitimacy: ok.
Build: about 20 lines. A reduce-only take with its own lower hurdle (`take_exit_edge` 0.0-0.01) when the opposite touch
is through p on held inventory.
Screen: data-only, by joining positions with this list (not done here).

### Z-11. Exiting value positions near p is not possible: plan them as held to the outcome
Mechanism (a constraint, not a profit idea): any "rotate and recycle" plan needs exits near fair.

Data check (`Z_exitfair.py`, 4 Oct, own levels skipped):
- Favourites (p > 0.85): a rival bid ≥ p − 1c in **0.58%** of 56k market-cycles, ≥ p − 2c in 3.4% (only 8 markets
  ever).
- Longshots (p < 0.15): a rival ask ≤ p + 2c in 3.7% (6 markets).

So every value position costs 3-7c to exit. Rotation is only worth it when the new level beats the old by more than
that (`alloc_min_improvement` 0.03 is about right).

Upside: avoids churning losses | Build: none | Screen: done.

### Z-12. Dem U.S. House control at 0.83 against Polymarket 0.935
Mechanism: the House control contract is the most mispriced liquid favourite on the board: 10.5c, **12.7% per $**. We
hold 0 Dem House and −4,268 Rep House (Rep at 0.175 against 0.075).

Data check (`Z_field_hedge.py`, 15:57): Dem U.S. House bid 0.825 / ask 0.83, reference 0.935. In LIVE_0404,
Dem U.S. House was the largest reference-move loss (−76).

Upside: 5k at 12.7% is +0.6k one-off | Ruin: large Dem bloc delta. It is national-wave correlated with every Dem seat,
so `bloc_delta` will cap it, and the 0.05 frac is only 3.1k of room.
End-valuation: needs the outcome valuation.
Legitimacy: ok.
Build: config. Pin it in the allocator, or swap Rep House short → Dem House long, which costs the same cash and is
cheaper by the bid-sum gap.
Screen: data-only.

### Z-13. Senate control against the seat races: consistent, nothing to trade
Mechanism: test whether the control contract and the seats disagree.

Data check (`Z_field_hedge.py`):
- Dem U.S. Senate 0.67/0.675 against reference 0.655. The tournament is 1.75c rich on Dem.
- Across 33 Senate races, expected Dem seats are **15.88 at Polymarket and 16.41 at tournament mids**. That +0.53
  seats is the tilt (Rep favourites underpriced), not a control/seat inconsistency.
- We hold −5,000 Dem / +5,000 Rep Senate control, which agrees with the 1.75c.

Upside: ~0 | Build: none.

### Z-14. Hedges that free sum-of-maxima room: cost more EV than they unlock
Mechanism: buying the losing-scenario leg of a race cuts its worst case by about (1 − ask) per $ of ask. It frees
backstop room for value adds without touching the 0.85 setting.

Data check (`Z_field_hedge.py`, `mm_bot.worst_case_loss`, 15:57 positions):
- The sum of race worst cases is 81.1k (status 81.0k).
- The best pure hedges, one per race with room per cash ≥ 1.5, free **+11.5k of room for 2.5k of cash and −1.05k of
  EV** across 40 races. Example: Rep Virginia Senate +1,000 at 0.10 frees 978 for −79 EV.
- That 11.5k of room filled with value adds at about 10% earns +1.15k, against the hedges' −1.05k.
- **Raising `worst_case_backstop_frac` 0.85 → 0.95 frees about 10k for free.**

Upside: ~0 net | Build: none. Use the setting.

### Z-15. Ignore the leaderboard slide; it is the mark gap, not losses
Data check (journal Rank lines): **181 (00h) → 214 (04h) → 220 (06h) → 459 (08h) → 293 (10h) → 561 (12h) → 573
(14h)**, out of 1,044-1,087. Over the same hours EV at the outcome went 108.2k → 103.8k → 106.3k (LIVE_0404). The rank
follows exchange marks. The value book is marked about 7.8k below its EV, and every +0.01 of s marks it lower.

Upside: none, unless SIG ranks by marks at the close (then see the stage 2 close override).
Build: none.

### Z-16. Top-20 turnover is slow, so the edge list does not refresh fast enough to recycle into
Data check (`Z_tilt_quotes.py`): the top 20 edge-per-$ levels (touch, own levels skipped) have median 0.13 / 0.14 /
0.13 / 0.12 at 04:30 / 08:30 / 12:30 / 15:55.
- New names against the previous probe: **9, 5 and 6** per 4 h.
- The same names persist: CT Gov 0.82-0.855 vs 0.975, RI Sen 0.84-0.865 vs 0.992, RI Gov, AR Gov, Rep RI Sen short,
  Dem NH Gov short.
- With exits at 3-7c (Z-11), and the tilt neither reverting nor rising fast enough to create new names, there is no
  fresh flow to recycle into. Supply is a stock (the $1M of Y's table), not a flow.

Implication: buy the stock once, early, at the 07-09h peak (Z-6), through the synthetic route (Z-5).

### Z-17. Spend the remaining backstop room only on ≥ 12% levels, shorts first
Mechanism: with 3.7k of room (10k after 0.95), room is the scarcest resource. A short longshot at 0.155/p 0.008 uses
0.845 of room and earns 0.147 (17% per $ of room). A favourite at 0.855/p 0.975 uses 0.855 and earns 0.12 (14%).
Ranking by edge per $ of room, rather than per $ of cash, picks shorts.

Data check: on the 15:55 top-20, **4 of the top 8 are shorts**, at 13-17% per $ of room.

Upside: on 10k of room +1.4-1.7k one-off, against about +1.2k ranked by cash | Build: about 10 lines in the
allocator sort key (`alloc_rank_by_room`) | Screen: data-only.

---

## TOP 5 (by EV per unit of risk, and buildable now)
1. **Z-17 + backstop 0.95:** spend about 10k of room on the ≥ 12% levels ranked per $ of room. **+1.4-1.7k one-off.**
2. **Z-5, synthetic favourites** in the take path and the value quotes: **+3c per share** on every favourite add,
   about +0.6k on 20k.
3. **Z-7 / Z-9:** move the resting-quote cash from middle-band two-way quoting (1 of 30 markets both-sided) to favourite
   bids (+4.1c) and longshot asks (+6.6c). **About +0.3-0.7k.**
4. **Z-6:** concentrate adds in 04-09 UTC (s 0.147-0.150 against 0.139 at 13h; Dutch gaps ×3-10). About +0.5c per share.
5. **Z-3:** NO+NO sets at ≥ 1.02 for cash the backstop leaves idle. Riskless, 2-3% to the outcome, zero room used.

**Not to build:** Z-1 and Z-2 (take-and-flip: 0 of 349 tail takes flip in 6 h), Z-4 (YES+YES carousel: $50/day at 100%
capture), Z-14 (hedges: break-even; use the setting instead).

## Can any combination deliver ~1%/day (~1k of EV a day) consistently? No.

| Recycling stream (cash comes back before 3 Nov) | Measured $/day |
|---|---|
| Take-and-flip (Z-1, Z-2) | ≤ 0.18k (upper bound) + 0.1k |
| YES+YES carousel (Z-4) | 0.05k at 100% capture |
| NO+NO dissolve (Z-3) | 0 (0 of 67 dissolve) |
| Middle-band carry (Z-9) | ~0 (1 of 30 both-sided) |
| Through-p exits (Z-10) | 0.1-0.2k, not yet sized |
| **Total recycling** | **≈ 0.2-0.4k/day ≈ 0.2-0.4%/day** |

**One-off (locks to the outcome):**
- Z-17 room-ranked adds: +1.4-1.7k.
- Z-5 / Z-7 / Z-9 improvements: +1.0-1.5k.
- Y's rotation: +1.5-2.5k.
- Remaining cash at about 10%: +2k.
- **Total: about +6-8k.**

Over 30 days that is about **+12-18k** (E[final] about 118-125k), **about 0.4-0.55% a day on average**, front-loaded
in the first 3-4 days.

Why: the tilt widened today, and it does not revert after takes, so no edge is realised before the outcome. Value
positions cost 3-7c to exit. Supply is a slow-turning stock. With about 24k of working cash and about 10k of room,
the edge is harvested once. A consistent 1% a day would need about 10k of fresh cash a day, and nothing on the board
supplies it. Only variance (the Texas digital) reaches +50%, and that is not "consistent".

## Assumptions I could not check
- Rival refill rates after we hit a level (capture fractions are guesses; the books table is sampled per eid and not
  synchronous; the 20 s sync filter still leaves flicker).
- Whether the 07-09h tilt peak repeats (one day of data).
- Z-10 size (needs positions joined with the undercut events).
- Whether p (Polymarket, race-scaled) is the true probability. If the tilt is partly information, every edge here is
  overstated (PORT-11).
