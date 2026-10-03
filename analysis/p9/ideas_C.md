# Package 9 explorer C: rival bots and takes, riskless sets and arbitrage, capital allocation

Data: /home/claude/snap03 (to 3 Oct 22:47 UTC). Scripts (read-only, sqlite/pandas, each < 20 s):
`analysis/p9/C_sets.py` (bid-sum / ask-sum frequency per race-cycle, episodes, thin-leg sizes, $ per hour; the NO-sets we hold),
`C_carousel.py` (build-then-dissolve round trips for NO-sets and YES-sets; held sets at the exchange marks; set split),
`C_resid.py` (hourly tilt, rival residual after the tilt, residual take rule, Polymarket pass-through, the bot's take fills),
`C_fatfinger.py` (gross rival errors vs the tilted model), `C_rivals.py` (size signatures, time of day, pennying),
`C_mm.py` (market-making capture and mark-outs by day), `C_alloc.py` (ruin numbers for the risk settings and the capital split,
on explorer B's tilt Monte Carlo `B_mc.py`, imported unchanged; mixed prior base 0.5 / bear 0.3 / bull 0.2, 3,000 paths per world).
Ideas A-1..A-19 and B-1..B-18 are not repeated; where I touch A-12 (YES sets) or B-4/B-6 (sets as the tilt route) I add a mechanism
or a number.

## Headline findings (read these first)
1. **The take rule is a tilt-short machine.** 3 Oct: 2,691 TAKE attempts (~3.2 writes/min, 11% of the 28/min budget), 276 take fills
   (31.8k shares) since 2 Oct, **100% of them toward Polymarket** (selling longshots / buying favourites). They added **+15.0k of the
   +35.9k tilt exposure** (42%). P&L at the final mid **-406**; at the final Polymarket price +2,388 (a settlement bet). Rival
   "staleness" is the tilt: after removing the common tilt the residual is persistent (24 h beta 0.76) and a residual take rule earns
   -0.6c to +0.3c per share at +1/6/24 h (C_resid). **Stale-quote $ per day available at the mark: ~0** (gross errors >= 10c past
   the tilted model: 1.3 episodes/day, none >= 15c).
2. **Riskless sets are plentiful but capital-hungry.** Race-cycles (42 cycles/h, own quotes excluded) with bids summing >= 1.02:
   10,524 (3.9%), >= 1.03: 4,357, >= 1.04: 1,757; asks <= 0.985: ~4.7% in the last 6 h. $ available (one capture per episode, thin
   leg capped at 2,000 sets): **bid-sum >= 1.00: 504/h, >= 1.02: 501/h, >= 1.03: 360/h, >= 1.04: 199/h** (cap 500 sets: 226 / 206 /
   141 / 78 per h). But each $ locks ~30x its edge in capital to 4 Nov (3.4% per month at >= 1.03): at 0 cash, 10k of free cash is
   used up in about an hour. Held to settlement, a set returns ~3%/month, far below the long-tilt basket's break-even (~2%/day).
3. **The money in sets is the carousel, not the hold.** Build a NO-set at bid-sum >= 1.02, dissolve it at ask-sum <= 1.01: 277 closed
   round trips in 54.7 h, **median 0.9 h**, 1.71c per set -> ~1.0k/day at 500 sets per trip. Mirror (YES-set: buy at ask-sum <= 0.985,
   sell at bid-sum >= 1.00): 131 closed trips, median 2.3 h, 2.26c -> ~0.65k/day. Together ~1.0-1.7k/day upper bound on ~10-15k of
   rotating capital; ~0.3-0.5k/day after the live fill rate (146 of 716 arbitrage attempts filled every leg at 0 cash).
4. **Our 16,807 NO-sets** (15 races) cost **21,323**, pay **21,917** at settlement (+594, 2.8%), unwind now for 21,249 (-74), and are
   marked by the exchange at **21,382**: the leaderboard will only see the remaining +535 if SIG settles at the outcome or we dissolve
   them below the mid-sum. Keep them; dissolve only into ask-sums <= 1.00 when the basket needs the cash (C-8, C-9).
5. **The 30% risk cap forbids the long-tilt plan outright.** For a basket of longshot YES / favourite NO, both risk measures equal its
   cost (sum-of-maxima: every leg can go to 0; "correlated": 3 sd of N independent 10c legs = 9X/sqrt(N) > X for N < 81, and the cycle
   takes the min), so `max_worst_case_frac` 0.30 caps the basket at ~25k: **P(>= 150k) 0.0%**. The override bound is 0.60 (basket
   ~55k: P150 31%, P200 0%). Backstop 0.8 / 0.9 / off with B-2's CPPI: **P150 41 / 44 / 44%, P200 10 / 15 / 17%, P<=85 1.9% each,
   DD>20% 6.6 / 6.7 / 6.8%**; the setting alone (no floor): P150 45 / 50 / 52%, P<=85 3.6 / 3.9 / 4.2%, DD>20% 13 / 17 / 19%.
   The risk caps must count the basket by a tilt-stress loss, not by settlement (C-10, C-11).
6. **Capital split:** carry from market making / the carousel beats the last 10-20k of basket only if it yields >= ~2% per day on the
   capital it uses (C_alloc: cap 70k + 450/day P150 50.8% vs cap 90k + 0 43.9%; at half that carry it is a wash). The carousel
   (~3-10%/day on its capital) clears that bar; holding sets to the close (0.1%/day) does not; market making (~0.45c/share at +1 h,
   1-2k/day on 1-2 Oct with capital, +149 on 3 Oct at 0 cash) is near the bar.

---

### C-1. Stop the raw-Polymarket takes: `take_tilted_ref` true (or `take_enabled` false) before the long-tilt switch
Mechanism: `take_stale_quotes` compares the RAW Polymarket price with rival quotes, so in a tilted world every longshot bid and every
favourite ask looks "stale": the rule sells longshots and buys favourites, i.e. it adds short-tilt exposure every hour, against the
basket A-2/B-2 want. Switching to the tilt-corrected reference (already built, Package 5 X12) leaves only real residual errors.
Data check: C_resid.py: 276 take fills / 31,816 shares since 2 Oct 22:53, 100% toward Polymarket; tilt exposure added **+15,048**
(of +35,933); P&L at final mid **-406** (3 Oct: -385), at final Polymarket +2,388. Journal: 2,691 `TAKE` attempts on 3 Oct, 2,508
"traded ?" (unconfirmed), peak 370/h at 12h (~6 writes/min). By market: Rep Massachusetts Senate -82, Rep Rhode Island Senate -30,
Dem Oklahoma Senate -17 at the mid (all longshot sells into a rising tilt).
Upside: +0.4% to +1.2% per month (the -385/day marked loss at the 3 Oct tilt slope; more at the long-tilt basket's size, where every
take fights the basket) | P(>= 150k) effect: +1-3 points (a precondition: un-cancelled, the takes rebuild ~15k/day of short-tilt
exposure that the CPPI then has to buy back at 3% cost) | Ruin: none (removes risk)
End-valuation: under "settled", the raw takes are +EV (+2.4k at Polymarket); under "marked" -EV. With the long-tilt plan the sign must
match the basket: tilted.
Legitimacy: fewer orders; nothing changes in kind.
Build: config only: `take_tilted_ref` true (with `ref_tilt_enabled`); or `take_enabled` false. Hot-toggle.
Screen: data-only (done); live_sim `take_tilted_ref` on/off in the tilt world (pnl_lag).

### C-2. Do NOT build residual takes or Polymarket-move takes (negative result)
Mechanism: The obvious "take rivals wrong by more than the tilt explains" does not pay at the mark: rival residuals are persistent
(market-specific tilt, not noise) and only ~40% of a Polymarket move ever reaches the tournament book.
Data check: C_resid.py: residual r = mid - [c + (1 - s_h)(ref - c)], hourly s_h 0.022 (1 Oct 12h) -> 0.128 (3 Oct 18h); |r| median
1.2c, p95 4.0c, p99 5.8c. Persistence beta 0.92 (1 h), 0.79 (6 h), 0.76 (24 h). Take rule (buy ask <= model - e / sell bid >= model
+ e), P&L at the mid after H: e 2c: -0.15 / +0.07 / -0.60 c/share (H 1 / 6 / 24 h, win 30-47%); e 3c: -0.05 / +0.30 / -1.15; e 5c:
+0.26 / -0.16 / (n 3). Pass-through of a >= 3c hourly Polymarket move: 0.38 in the same hour, 0.33 / 0.49 / 0.40 at +1 / 3 / 6 h
(n 24: thin). Biggest residuals now are longshots the tilt model under-tilts (Rep RI Governor +8.2c, Dem MT Senate +6.9c): that is
B-1's "longshots 35% more tilted", not an error to take.
Upside: 0 (avoid ~-0.5c/share on whatever volume would be taken) | P(>= 150k) effect: 0, saves build time | Ruin: n/a
End-valuation: under "settled" the raw-Polymarket version is +EV (C-1); the tilt-residual version is ~0 under both.
Legitimacy: n/a.
Build: none.
Screen: data-only (done).

### C-3. Keep one take: the gross-error sweeper (>= 8-10c past the TILTED model, both directions)
Mechanism: Rare fat-finger or dead quotes far past even the tilted fair value are the only stale quotes worth taking at the mark.
With `take_tilted_ref` true, set `take_edge` 0.08: it fires a few times a day, costs a few writes, and never fights the basket's sign
(it is measured from the tilted reference).
Data check: C_fatfinger.py (all 2,312 cycles, own quotes excluded): >= 8c past the tilted model: 13 episodes in 54.7 h (5.7/day,
8 markets); >= 10c: 3 (1.3/day); >= 15c: 0. Sizes at those prices were not in the 3-level books (unsized). Journal: the biggest real
take-rule wins were also tilt-tail trades, not errors.
Upside: +0.0-0.1% per month (5/day x ~200 shares x 9c = ~90/day if each fills; likely 20-50/day) | P(>= 150k) effect: ~0 |
Ruin: one bad Polymarket print (take_confirm_seconds 30 s guards it)
End-valuation: independent (the edge is vs both the tilted mark and Polymarket).
Legitimacy: trading at posted prices.
Build: config only: `take_tilted_ref` true, `take_edge` 0.08 (bounds allow it).
Screen: data-only; live log count of TAKE lines after the switch.

### C-4. NO-set carousel: sell-side arbitrage at bid-sum >= 1.02, dissolve at ask-sum <= 1.01 (or <= 1.00)
Mechanism: When a race's bids add up to more than 1 (longshot bidders over-pay; B-6), sell YES on every leg (= buy a NO-set for k minus
the bid-sum). Instead of holding to 4 Nov, sell the set back (sell NO on every leg = buy the YES set at the asks) the next time the asks
add up to <= 1.01: profit = bid-sum - ask-sum, capital back within hours. The recycled capital is what makes it worth more than the
basket's marginal dollar.
Data check: C_carousel.py (own quotes excluded): build >= 1.02 / dissolve <= 1.01: 336 trips, **277 closed, median 0.9 h (p75
3.0 h), 1.71c/set**; build >= 1.03 / dissolve <= 1.00: 63 closed of 99, median 5.5 h, 4.36c; build >= 1.02 / <= 1.00: 133 of 207,
3.6 h, 2.88c. Thin-leg median 491 sets (C_sets). 277 x 1.71c x 500 / 54.7 h = **~1.0k/day** upper bound; capital ~5-10k rotating plus
the unclosed trips (59 of 336), which become held sets.
Upside: +3% to +30% per month (0.1-1.0k/day: the live full-fill rate was 20% of attempts at 0 cash, partial 33%) | P(>= 150k) effect:
+3-8 points at 0.3-0.5k/day (C_alloc carry 300/day: +7 points on the 0.8 backstop row) | Ruin: one-legged leftovers (C-6 rule); each is
an ordinary position of <= 500 shares at a price someone just bid.
End-valuation: dissolved trips are cash under both; unclosed sets pay k-1 under "settled" and are marked ~0.5c/set below it (C-18).
Legitimacy: trading at other traders' posted bids and asks; never against our own quotes (excluded); no order placed to move a price.
Build: `arb_min_profit` 0.02 + a dissolve threshold: the existing pair unwinder with `pair_unwind_min_profit` -0.01 is not allowed
(bound 0.0); new setting `arb_dissolve_max_sum` 1.01 in the pair-unwind path (~20 lines near the pair unwind, mm_bot.py ~6040 / ~9035),
or config only with `pair_unwind_min_profit` 0.0 (dissolve at ask-sum <= 1.00: 2.88c, median 3.6 h).
Screen: data-only (done); live_sim cannot (no multi-leg rival books); paper-count from the journal for 24 h first.

### C-5. YES-set carousel: `arb_two_sided` back on, buy at ask-sum <= 0.985, sell at bid-sum >= 1.00
Mechanism: The mirror of C-4: buy YES on every leg when asks add to <= 0.985, sell the set when bids add to >= 1.00. Unlike a NO-set,
a YES-set is marked ABOVE its cost the moment it is bought, because race mid-sums run above 1 (the leaderboard sees the profit at once).
Data check: C_carousel.py: buy <= 0.985 / sell >= 1.00: 152 trips (67/day), **131 closed, median 2.3 h (p75 6.2 h), 2.26c/set,
~650/day at 500 sets**; <= 0.99 / >= 1.00: 245 closed, 1.9 h, 1.62c, ~870/day. Race mid-sum median **1.005** (p25 0.998, p75 1.013):
a YES-set bought at 0.985 is marked ~+2c immediately. Last 6 h: 4.7% of race-cycles have ask-sum <= 0.985 (C_sets). The built-in
guards (`arb_buy_min_ref_sum` 0.99: every leg has a liquid Polymarket price; `arb_buy_min_sum` 0.90: no missing outsider) stay.
Upside: +2% to +20% per month (0.06-0.65k/day after fill-rate haircuts) | P(>= 150k) effect: +2-5 points | Ruin: a race with an
unlisted winner (guards above), one-legged buys (C-6 rule)
End-valuation: dissolved trips are cash; unsold sets pay 1 at settlement and are marked ~1.005.
Legitimacy: buying at posted asks, selling at posted bids.
Build: config only: `arb_two_sided` true (it is false live), with the C-6 cash rule (code).
Screen: data-only (done).

### C-6. Arbitrage back on, with a cash rule (owner's question 2)
Mechanism: `arb_enabled` went false at 22:36 because at 0 cash one leg filled and the other was refused, leaving one-legged leftovers.
The fix is a pre-trade cash check and sizing, not "off". Rule: act only when `cash_gate_left` >= 1.25 x (set cost x size) + 2,000
reserve, where set cost = k - bid-sum (sell side) or ask-sum (buy side); size = min(0.8 x thinnest leg's size at that price,
`arb_max_frac` x account, (cash_gate_left - 2,000) / (1.25 x cost)); all legs in ONE batch, IOC (ttl 10 s), thinnest leg first in the
batch; a leftover is offered at once to the other legs' current bids/asks by the pair follow-up (already built) and, failing that,
kept only if its tilt sign matches the basket; never when the race has our own best quote on a leg (self-trade). Threshold 1.02 when
the race dissolved within the last 24 h (C-4), else 1.03. Total set capital <= 15% of account.
Data check: journal 3 Oct: 860 `ARBITRAGE` lines claiming +9,628 "locked in"; of 716 resolved attempts **146 filled every leg (34,969
sets), 239 ALERT "only partly filled", ~331 filled 0** (funds refusals at 0 cash and vanished bids). Own-quote contamination: 8% of
bid-sum >= 1.00 race-cycles and **29% of >= 1.04** involve our own best quote (C_sets: 2,460 -> 1,757 excluding own). $ per hour
available at >= 1.02 / 1.03 / 1.04 (cap 2,000 sets): 501 / 360 / 199; cap 500: 206 / 141 / 78.
Upside: enables C-4/C-5 (their numbers) | P(>= 150k) effect: through C-4/C-5 | Ruin: leftovers <= 0.5% of account each, ~1/3 of
attempts at 0 cash; expected ~0 with the cash check
End-valuation: as C-4/C-5.
Legitimacy: as C-4; the own-quote exclusion is the self-trade rule.
Build: new `arb_cash_rule` (bool) + `arb_cash_margin` 1.25 + `arb_cash_reserve` 2000 in the arbitrage path (~30 lines, reuse
`cash_left()` from the Package 8 gate); `arb_enabled` true only after it ships. Answer to Q2: **yes, back on, only with this rule, at
1.02-1.03, and only with a dissolve rule (C-4) or with set capital capped at 15%.**
Screen: unit test of the sizing; then live with `arb_max_frac` 0.002 for 6 h and count leftovers.

### C-7. Do not hold new arbitrage sets to the close when the basket needs capital
Mechanism: A held set is a 31-day bond: its whole edge arrives at settlement. The long-tilt basket's marginal dollar is worth more
whenever its expected return exceeds ~2% per day per $ (C_alloc). Sets enter only as carousel trips (C-4/C-5) or as overflow cash.
Data check: C_sets: bid-sum >= 1.03 captures 3.44% of capital (locked to 4 Nov), >= 1.04 4.05%: 0.11-0.13% per day. C_alloc capital
split: basket cap 70k + 450/day carry P150 50.8% vs 90k + 0 43.9%; at 225/day 44.7% (a wash); the break-even is ~20-30 per day per
1k of capital moved out of the basket.
Upside: avoids locking up to 10k/h of capital at 0.1%/day | P(>= 150k) effect: +1-3 points vs "arb on, hold to close" | Ruin: none
End-valuation: held sets favour "settled" (+535 hidden from the mark, C-18).
Legitimacy: n/a.
Build: config: total set capital cap (C-6 rule's 15%).
Screen: data-only.

### C-8. The 16.8k NO-sets we hold: keep, dissolve only into ask-sums <= 1.00, and stop paying 2c to unwind them
Mechanism: The live `pair_no_unwind_max_cost` 0.02 lets the bot pay up to 2c per set (of the 2.8c locked) to turn sets into cash; at
19:55 it unwound 22.5k -> 14.6k of set capital in 23 min, then sell-side arbitrage re-created them (a round trip that only costs).
Dissolve only when the asks sum <= 1.00 (free) or when the long-tilt basket's CPPI target cannot be funded otherwise.
Data check: C_sets: 16,807 sets / 15 races: cost **21,323**, payout 21,917 (+594, 2.79%), unwind now 21,249 (-74 vs cost, -668 vs
payout); per set cost 0.949 (PA-01) to 0.997 (Mississippi Senate; ~0 return), Montana / Nebraska Senate 3-leg sets 1.970 / 1.956 for 2.
Ask-sum <= 1.00 happens in ~10% of race-cycles (25,643 of 270,504), so free dissolves come along within hours.
Upside: +0.6k (the locked +594) kept; avoid ~0.1-0.3k of 2c unwind costs | P(>= 150k) effect: ~0 (+0.5 point) | Ruin: none
End-valuation: under "marked", the sets show 21,382; dissolving them at ask-sum <= 1.00 before the close realises the +535 the mark
hides (C-18).
Legitimacy: trading at posted prices.
Build: config: `pair_no_unwind_max_cost` 0.0 (bound -1..0.05), `pair_unwind_min_profit` 0.0; raise to 0.02 only on a basket-funding
signal (new `pair_unwind_cost_when_funding`, ~10 lines).
Screen: data-only.

### C-9. Set split: turn the held sets into the long-tilt basket's favourite-NO leg (sell only the longshot-NO legs)
Mechanism: A NO-set in a binary race = favourite NO (long the tilt) + longshot NO (short the tilt). Selling only the longshot-NO legs
(buy longshot YES / sell NO at the asks) frees most of the set's capital and leaves favourite NO, which is B-4's cheap long-tilt route
already bought. One trade both funds the basket and moves the book's sign.
Data check: C_carousel.py: selling the non-favourite NO legs of all 15 races frees **18,443** cash (vs 21,249 for unwinding whole sets)
and adds **+6,761 long-tilt exposure** (68 marked per point of s) from the favourite-NO legs left. The 18.4k then buys ~83k of
longshot-YES tilt exposure at a 10c longshot (~830/point).
Upside: funds ~18k of the basket on day 1 without selling into the toward book's thin bids | P(>= 150k) effect: +1-3 points vs
waiting for free dissolves (the basket starts ~day 0 instead of after A-3's flattening) | Ruin: the favourite-NO legs left are part
of the basket (its risk); giving up the locked +594
End-valuation: under "settled" the sets' +594 is forfeited and the favourite NO settles at Polymarket odds (expected loss ~ the tilt
premium), so exit by T-7d as B-3.
Legitimacy: trading at posted prices.
Build: manual (owner sells the longshot-NO legs with `reduce_no_as_sell`), or a `set_split_for_basket` step in the basket builder
(~25 lines).
Screen: data-only; live_sim with `_world_tilt_growth` for the basket.

### C-10. Count the long-tilt basket by a tilt-stress loss, not by settlement (the risk model that allows the bet)
Mechanism: Both risk measures assume we hold to settlement, where a longshot-YES basket loses its whole cost. The plan exits by T-7d
(A-6, B-3), so the relevant loss is a tilt crash before the exit: basket value x (1 - P(s x f) / P(s)), f ~ 0.5 (a crash that halves
s). Rule: for legs tagged "basket", the reduce-only test uses stress_loss = sum pos x [P(s) - P(0.5 s)] (P(s) = c + (1 - s)(ref - c)),
and the CPPI budget is stress_loss <= account - floor; the sum-of-maxima backstop stays as a last resort at 0.9. Before T-7d - 1 day,
the basket legs return to the settlement measure, which forces the exit.
Data check: C_alloc.py (on B_mc's tilt world): for a 10c longshot at ref 0.03 and s 0.12, halving s cuts the price from 0.086 to
0.058 (-33%), so a 75k basket (CPPI m 5 on a 15k cushion) has a 25k stress loss vs a 75k settlement loss. Risk measures for an N-leg
basket of 10c legs: correlated 3 sd = 9X/sqrt(N) (1.18X at N 58) > X, so the min() picks X: both measures = cost.
Upside: makes A-2/B-2 possible at all (P150 0% -> 41-44%) | P(>= 150k) effect: the whole bet (+41-44 points vs the 0.30 cap) |
Ruin: P(<= 85k) 1.9%, DD > 20% 6.6% (CPPI m5, floor max(86k, 0.85 peak), exit T-7d); a model error in the crash depth (f 0.35 instead
of 0.5) loses ~1.3x the stress budget: the intraday floor guard (B-10, on liquidation value) is the backstop.
End-valuation: designed for the exit-before-close plan; under "settled", holding past T-7d is the disaster (expected basket loss ~70%
of cost at Polymarket odds), which is why the measure reverts to settlement before the end.
Legitimacy: internal risk model.
Build: new `risk_basket_stress` (bool), `risk_basket_crash_frac` 0.5, `risk_basket_revert_days` 8; ~40 lines in `settlement_risk`
/ `total_worst_case` (mm_bot.py ~4872-4895) with a basket tag per eid.
Screen: live_sim with `_world_tilt` / `_world_tilt_growth` and a crash knob; C_alloc numbers (done).

### C-11. The settings: answer to the owner's question 3, with ruin numbers
Mechanism: The 0.30 cap and 0.8 backstop were sized for a market maker holding to settlement. For "P(>= 150k) first" they block the
only strategy that reaches it; for ruin they are not the binding control once a CPPI floor exists.
Data check: C_alloc.py, mixed prior, basket capped by the setting (W_other 5k after the toward book is flattened):

| setting (what caps the basket) | with B-2 CPPI m5, floor max(86k, 0.85 peak), exit T-7d | setting alone (no floor, at the cap) |
|---|---|---|
| max_worst_case_frac 0.30 (now): ~25k | P150 0.0% / P200 0.0% / P<=85 0.0% / DD>20% 0.0% | 0.0 / 0.0 / 0.2 / 0.3 |
| 0.60 (the override's max): ~56k | 31.4 / 0.0 / 1.5 / 5.4 | 31.7 / 0.0 / 2.5 / 8.4 |
| backstop 0.8 (cap raised past it): ~76k | 41.3 / 10.2 / 1.9 / 6.6 | 45.2 / 11.7 / 3.6 / 13.0 |
| backstop 0.9: ~86k | 43.7 / 14.9 / 1.9 / 6.7 | 49.8 / 17.5 / 3.9 / 17.0 |
| off (cash only, 0.9 x account) | 44.1 / 16.5 / 1.9 / 6.8 | 51.7 / 19.7 / 4.2 / 19.4 |

Answer: **max_worst_case_frac 0.30 is wrong for this objective (P150 0%); 0.8 backstop costs ~3 points of P150 and ~5-6 of P200 vs
0.9; with the CPPI floor, 0.9 and "off" are the same (the floor binds, not the backstop).** Set: basket legs on C-10's stress measure,
`max_worst_case_frac` 0.60 for the rest (the bound), `worst_case_backstop_frac` 0.9 (the bound), plus the CPPI floor and a
liquidation-value kill at 86k / 0.85 x peak. Without C-10 (config only): 0.60 + 0.9 gives P150 ~31%, P<=85 1.5%.
Upside: as the table | P(>= 150k) effect: +31 (config only) to +44 points (with C-10) | Ruin: P(<= 85k) 1.5-2.1% with the floor;
3.6-4.2% without (B's world; B's bear world alone ~5%)
End-valuation: all rows exit at T-7d, so valuation-independent; holding to the close under "settled" is not in the table and is ruinous.
Legitimacy: internal limits.
Build: config (0.60 / 0.9) now; C-10 for the rest.
Screen: C_alloc (done); live_sim tilt world.

### C-12. Capital allocation waterfall (the rule), with the write budget
Mechanism: Every cycle, allocate in this order: (1) cushion check: account (liquidation) - floor, floor = max(86k, 0.85 x peak); (2)
long-tilt basket target = min(5 x cushion, 80k) (B-2), funded first from cash, then C-9 split, then C-8 free dissolves; (3) carousel
(C-4/C-5) up to 10k of capital, only from cash the basket does not need this day (its trips return within hours, so it is the
basket's buffer, refilled daily); (4) market making with what is left (C-13), adding factor on; (5) held sets to the close only as
overflow (C-7); (6) takes: gross-error sweeper only (C-3). Writes (28/min): basket building 2/min (B-14: <= 25% of visible asks per
hour), carousel/arbitrage <= 1/min (5-10 trips/h x 2-3 legs + dissolves), sweeper < 0.1/min, the rest (~24/min) to quotes; the
take rule's ~3.2/min (C-1) is the budget that pays for it.
Data check: C_alloc capital split (mixed prior, CPPI m5): cap 90k + 0 carry P150 43.9% / P<=85 2.1%; 80k + 150/day 45.4 / 1.0;
70k + 450/day 50.8 / 0.3; 60k + 750/day 56.4 / 0.0 (P200 falls 16.1 -> 13.5); 50k + 1,050/day 64.3 / 0.0 (P200 10.4). At half
those carries the rows are 43.6 / 44.7 / 44.8 / 43.9: the split pays only if the carry is real.
Upside: +2 to +12 points P150 depending on realised carry | P(>= 150k) effect: as stated | Ruin: lower P(<= 85k) (the carry is a
cushion), P200 falls when the basket cap goes below ~70k
End-valuation: carry is cash under both.
Legitimacy: n/a.
Build: a `capital_plan` block in the cycle (~60 lines): basket target first, then `cash_gate` budgets per purpose.
Screen: C_alloc (done); live_sim cannot model the carousel; measure carousel $/day live for 24 h before lowering the basket cap.

### C-13. Keep market making alive with 10-15k: it was the best carry when it had capital
Mechanism: The maker spread capture survives the 1-h mark-out (rivals rarely pick us off): with capital it earned 1-2k/day; at 0 cash
it earns ~150/day. With tilted fair values (`ref_tilt_enabled`) its inventory is tilt-neutral on average, so it does not fight the basket.
Data check: C_mm.py (maker fills only, takes/arbitrage excluded): 1 Oct 691 fills / 278k shares: capture at fill +2,039, mark-out
+1 h +1,623, +6 h +1,123; 2 Oct 2,418 / 313k: +853 / +1,014 / +1,269; 3 Oct (0 cash) 508 / 33k: +181 / +149 / +86. Overall 0.49c/share
at fill, 0.45c at +1 h, 0.40c at +6 h; toward-Polymarket vs away fills split 1,595 / 2,022 (both positive).
Upside: +5% to +30% per month at 0.15-1.0k/day (capital-dependent) | P(>= 150k) effect: +2-8 points (C-12 carry rows) | Ruin: the
inventory's tilt drift (the 14:00-19:18 -655 re-marking was inventory, not spread); keep `tilt_exposure_max_frac` on
End-valuation: spread capture is cash; inventory is marked.
Legitimacy: ordinary quoting.
Build: config: `capital_ceiling_adding_size_factor` > 0 once cash exists (Package 8's 0.95 rule), `tilt_exposure_max_frac` 0.10.
Screen: live_sim (pnl_lag) with the basket's capital removed (`_start_cap`).

### C-14. Use the thin hours: 03-05 UTC and 16-17 UTC (wide spreads), 05-07 and 23 UTC (thin tops)
Mechanism: Rivals quote less overnight US time: spreads widen and top-of-book depth halves. Market making earns more per fill then
(widen the minimum edge less, keep size), and the basket buys as a maker into wide spreads (B-5 found the tilt drift lowest in the
European morning: build then).
Data check: C_rivals.py: share of markets with spread <= 1c by UTC hour: 0.55-0.58 at 03-04 h, 0.66 at 16 h vs 0.91-0.92 at 09-11 h;
median top-of-book shares (bid + ask): 1,326 at 05 h, 1,350 at 07 h, 1,395 at 23 h vs 4,464-5,326 at 19-21 h.
Upside: +0.2% to +1% per month (more capture per fill at night) | P(>= 150k) effect: +0-1 point | Ruin: thin books = bigger
mark-outs on news; Polymarket still guards fair value
End-valuation: n/a.
Legitimacy: ordinary quoting.
Build: an hour-of-day factor on `min_edge` / size (~15 lines) or a cron'd override.
Screen: live_sim cannot (no time-of-day rivals); A/B by hour live.

### C-15. The pennying bot: do not chase it, and the fair-play line
Mechanism: Someone improves our best quote by exactly one 0.5c tick within a minute about 6% of the time. Chasing it (re-pricing up)
costs writes and edge. The tempting exploit, posting a quote to provoke the penny and then trading against the penny, is an order
placed to move a price: over the line. The fair response: leave our size one tick behind when the penny is inside our minimum edge
(`behind_best` exists), and if a pennied price is ever past our fair value, trading with it is an ordinary take (C-3 covers it).
Data check: C_rivals.py: when we are alone at the best and keep the price, next cycle (median 66 s) someone is better 6.10% (bid) /
6.59% (ask); of those, 80% / 83% by exactly 0.5c. Sizes: 16,267 distinct sizes in 86,872 book levels, round sizes only 6.9%; the 20-share
quote appears in 230 of 237 markets (1 bot?), 100 / 200 / 1,000 in 204-218 markets.
Upside: ~0 directly; saves ~1-2 writes/min of chasing | P(>= 150k) effect: ~0 | Ruin: n/a
End-valuation: n/a.
Legitimacy: the line: every order we place must be one we are happy to have filled at that price; no order whose purpose is to make
another trader move.
Build: none (document the line in README's rules).
Screen: n/a.

### C-16. Spend the take rule's write budget on the basket and the carousel
Mechanism: The 28/min write budget is shared. The take rule used ~3.2 writes/min on average on 3 Oct (peak ~6), for a marked loss.
Re-assigned, it builds the basket as a maker (B-14) at ~2 writes/min and runs the carousel (< 1/min).
Data check: journal: 2,691 TAKE attempts on 3 Oct, by hour 129-370 (08-19 h), 2,508 "traded ?"; status at copy: `takes_total` 8 since
the 22:42 restart, write budget waits 1.
Upside: indirect (enables C-4/C-12 without starving quotes) | P(>= 150k) effect: +0-1 point | Ruin: none
End-valuation: n/a.
Legitimacy: write limit respected (fewer writes).
Build: config (C-1).
Screen: live `requests_last_min` / `write_budget_wait_total` after the switch.

### C-17. Dissolve NO-sets before the close if SIG marks (the +535 the leaderboard cannot see)
Mechanism: The exchange marks a NO-set at k - sum of marks; race mid-sums run above 1 (median 1.005), so our sets are marked below
their payout. Under a "marked at the close" rule that gap is never realised; dissolving at ask-sum <= 1.00 in the last week turns it into
cash. Conversely, YES-sets are marked above cost: prefer YES-set trips (C-5) late in the month.
Data check: C_carousel.py: held sets marked **21,382** vs payout 21,917: **-535** on the leaderboard; race mid-sum median 1.005, p75
1.013; ask-sum <= 1.00 in ~10% of race-cycles.
Upside: +0.5% (535) once, under "marked" | P(>= 150k) effect: ~+0.5 point | Ruin: none
End-valuation: only matters under "marked"; under "settled" holding is equal or better (no spread paid).
Legitimacy: trading at posted prices.
Build: a dated switch: from T-7d, `pair_unwind_min_profit` 0.0 on every set race (config).
Screen: data-only.

### C-18. Arbitrage edge as a tilt gauge: rising bid-sums mean longshot bidders are paying up
Mechanism: Bid-sums above 1 are the tilt's bid pressure in a race (longshot bids above 1 - favourite bid). The count of race-cycles over
1.03 tripled from 2 Oct to 3 Oct; a fall in it is an early sign the longshot bid walls are thinning (A-15/B-8's ceiling signal), before the
mid tilt turns. Cheap to log, one more input to the basket's kill switch.
Data check: C_sets: bid-sum >= 1.03 episodes per day 8 (1 Oct, partial) / 133 (2 Oct) / 477 (3 Oct); $ at >= 1.03 (thin leg uncapped) 13.0k
(2 Oct) -> 48.7k (3 Oct); last 6 h 2.8% of race-cycles >= 1.03.
Upside: indirect (earlier exit: B-17's ceiling test gets a second signal) | P(>= 150k) effect: +0-2 points via fewer late exits |
Ruin: none
End-valuation: n/a.
Legitimacy: observation only.
Build: a status field `bidsum_over_103_per_h` (~10 lines).
Screen: data-only (backtest vs the tilt series once a reversal exists).

---

## TOP 5 (by P(>= 150k) per unit of ruin risk)
1. **C-10 + C-11 Risk model and settings for the basket**: the 0.30 cap makes the plan impossible (P150 0.0%). Config-only now:
   `max_worst_case_frac` 0.60, `worst_case_backstop_frac` 0.9 -> P150 31%, P<=85 1.5%; with the basket's stress measure (C-10) and the
   CPPI floor: P150 44%, P200 15%, P<=85 1.9%, DD>20% 6.7% (0.8 backstop: 41 / 10 / 1.9 / 6.6).
2. **C-1 (+ C-16) Stop the raw-Polymarket takes**: 100% short-tilt, +15.0k of the 35.9k exposure, -406 at the mark, ~3.2 writes/min.
   `take_tilted_ref` true (+ C-3 `take_edge` 0.08). Free, hot-toggle, removes risk.
3. **C-4 + C-5 + C-6 The set carousel with a cash rule**: NO-sets built at bid-sum >= 1.02, dissolved at <= 1.01 (277 trips, median
   0.9 h, 1.71c); YES-sets at <= 0.985 / >= 1.00 (131 trips, 2.3 h, 2.26c, marked up at once). ~1.0-1.7k/day upper bound, ~0.3-0.5k
   after fill rates: +5-8 points of P150 as carry, on ~10k of rotating capital.
4. **C-9 Set split to fund the basket**: selling the longshot-NO legs frees 18.4k and leaves +6.8k of long-tilt favourite NO, so the
   basket starts on day 0 without dumping the toward book first.
5. **C-12 Capital waterfall**: basket first (CPPI), carousel second (<= 10k), market making third, held sets last; the split is worth
   +2 to +12 points only if carry >= ~20-30/day per 1k moved out of the basket: measure the carousel for 24 h before lowering the basket cap.

Answers owed: **Q2 (arb_enabled)**: back on only with the C-6 cash rule (cash_gate_left >= 1.25 x set cost x size + 2k; size <= 0.8 x
thinnest leg; one batch IOC; own quotes excluded; threshold 1.02-1.03) and with a dissolve rule; held-to-close sets capped at 15%.
**Q3 (backstop / risk cap)**: the 0.30 cap is wrong for this objective (P150 0%); set 0.60 / 0.9 now (P150 ~31%, P<=85 1.5%), then
move the basket to the stress measure (C-10) for P150 ~44% at P<=85 ~2%; 0.8 vs 0.9 vs off differ by <= 3 points of P150 once a CPPI
floor exists; without a floor, "off" pushes DD > 20% to 19%.

## Assumptions I could not check
- Carousel and set dollars assume one capture per episode at the thinnest leg's displayed size (median ~500; sizes come from the
  nearest `books` row within 2 h, level 0 when the price moved): the live full-fill rate was 20% of attempts at 0 cash; with cash it is
  unknown. Rival arbitrageurs may already take the best episodes between our 60-s snapshots.
- Snapshot best bids/asks may include stale bulk tops (`markets_priced_from_tops` 46); a race-sum built from a stale leg is not
  tradable.
- Ruin numbers are B_mc's tilt world (logistic, K median 0.30, crash 12%, endgame unwind 35%) and B's basket price model; I did not
  re-fit them. The B world's P(<= 85k) is optimistic in a bear world (~5% alone).
- Carry rows in C_alloc add carry linearly and riskless and do not take the capital out of the basket a second time beyond the stated cap.
- The NO-set split assumes the favourite leg is the one with the higher Polymarket price; 3-leg races (Montana, Nebraska, RI Governor)
  keep the favourite NO only.
- The residual analysis uses a single cross-sectional tilt per hour; market-specific tilts (longshots 35% more tilted, B-1) make some
  "residuals" structural, which is why they persist.
- fills.csv `fill_price` is the YES price on some rows and the NO price on others; I took the reading closest to `quote_price`.
- SIG's end-valuation rule is unknown (C-17 matters only under "marked").
