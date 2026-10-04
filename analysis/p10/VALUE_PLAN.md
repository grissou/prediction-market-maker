# Package 10, the value-mode plan (executor, 4 Oct ~09:30 UTC) — one page for the owner

**Rule confirmed by the owner: SIG pays positions out at the OUTCOME.** A position is worth its Polymarket probability p; interim marks
(the tilt) do not matter for the payout; selling below p destroys value unless the cash buys more edge per $.

## Where the book stands (outcome model `analysis/p10/H_outcome.py`: Polymarket probabilities race-scaled, one national factor rho 0.45
## (0.85 for the control markets), independents separate, 20k outcomes; data = the 22:47 snapshot)
Current book held: **E[final] 109.0k** (the owner's 108.5k checks), sd 7.5k, P(>= 120k) 2.7%, **P(>= 150k) 0%**, P(<= 85k) 0.6%.
The party delta the bot reports (-15.4k shares) has the wrong SIGN for this purpose: the book's real sensitivity to a national swing is
Rep-leaning, +2.55k per sd. Tilt-long accounts (the leader) mark at a median ~183k today and settle at a median ~28k.

## The table (plan, E[final], P(>= 120k), P(>= 150k), P(<= 85k), P(<= 70k))
| Plan | E | P>=120k | P>=150k | P<=85k | P<=70k | What it takes |
|---|---|---|---|---|---|---|
| 0. Hold the book | 109.0k | 2.7% | 0% | 0.6% | 0% | nothing |
| (a') sell the 23 lowest-edge positions (< 2% edge-held, $8.8k), redeploy | 110.7k | 12% | 0% | 1.8% | 0% | 28 orders, now |
| (a) max-EV reallocation held to the outcome | 112.9k | 29% | 0% | 1.9% | 0.1% | ~111 orders, $127k turnover |
| (b) (a) + recycling on convergence (sell within 2c of p, redeploy) | 114.9k | 30% | 0% | 0.9% | 0.1% | + ~25 orders; depends on the tilt path |
| **(c1) (a) + Rep U.S. Senate YES $20k at 0.35** | 110.5k | 34% | **17%** | 4.3% | 0% | the only +EV correlated bet |
| (c1) $22.5k | 110.2k | 34% | 26% | 4-25% (cliff) | 0.1% | |
| (c1) $30k | 109.3k | 35% | 32% | **65%** | 3.9% | |
| (c1) for 40% | unreachable: capped by P(Rep Senate) ~35% | | | | | |
| (c2/c3) competitive-race party blocs ($35-97k) | 95-108k | 35% | 20% | 33-47% | 3-38% | lose EV at the ask: NO |
| (a) + funded value market making, carry 0.67k/day (I-6) | ~133k (= (a) + 30 d x carry) | - | 6% | ~0.1% | ~0% | 15-25k rotating cash; 24 h live proof first |
| (a) + value MM, carry 1.0k/day | ~143k | - | 29% | 0.1% | ~0% | same |
| (a) + value MM 0.67k/day + Rep Senate $15k | ~133k | - | 26% | 0.7% | - | |
| (a) + value MM 0.33k/day + Rep Senate $20k | ~123k | - | 28% | 3.4% | - | |
Under the Senate seat-count model (the 34 listed races imply P(Dem control) 0.49-0.59 vs the market's 0.645), the $20k sleeve is E 116.1k,
P(>= 150k) 24%, P(<= 85k) 4%. The cliff on P(<= 85k) sits between $20k and $27.5k: **$20k is the ceiling**. Before any sleeve: check SIG's
contract wording for Senate control (independents caucusing, vacancies, the VP tiebreak) and the Ohio / Alaska prices.
**Recycling (owner's option 1): +1.6k probability-weighted (+1.4%), range +0.5k (tilt keeps growing) to +4.2k (linear convergence);
"+10-20%" is not plausible as a share of the account (it is ~10-20% of the EDGE), because uniform convergence also erases the edges to
redeploy into. It is a rule inside the allocator, not a plan.** **Correlated party bet (option 2): the Rep Senate sleeve is the one
version that is +EV; competitive-race blocs at the ask are dominated (they pay the tilt). "+40-60% / -30-40%" matches a ~$40k sleeve,
where the bad night is the likelier one (P(<= 70k) 45-51%).**

## Market making continues (the owner's requirement), as VALUE market making
Valued at the outcome, 3 Oct's maker fills split: favourite bids + longshot asks **+1,948** (+5.1c/share), favourite asks + longshot bids
**-1,920**, middle two-way +604, takes +1,862 (+7.5c/share). Rule (`value_quote_hurdle`): fair value = raw Polymarket; the adding side
must clear a per-$ hurdle (8%: a YES bid <= p/1.08, an adding ask >= 1 - (1 - p)/1.08) so in the tails only favourite bids and longshot
asks rest; the middle (15-85c) is two-way at min_edge, capped at 2 quote sizes so the cash rotates. It needs 15-25k of rotating cash
(the sets, unwound by cost per $ freed, are the cheapest: 21.2k for $668 of EV); F's flow model says 0.9-2.0k/day at the mark, I's
outcome valuation ~1.4k/day on today's ~210 adding sides; UNMEASURED live in value mode: gate the reserve on 24 h of live data at
>= 0.6k/day. The stale-quote takes keep running (they buy value at +7.5c/share) but respect the reserve (`take_respect_reserve`).


## Carry check (`analysis/p10/CARRY.md`, 09:55): what "market-making carry" really is at the outcome
Maker fills by day, valued at the outcome: value sides (favourite bids, longshot asks) 1 Oct +1.4k on 39k of cash, 2 Oct +4.3k on 79k,
3 Oct +0.2k on 3.4k: **~4.9c per $, ONCE** (the positions are locked to the outcome) - so "1.0k/day of carry" from the value sides needs
20-35k of FRESH cash a day, which a 100k account does not have: that income is plan (a) itself (deploy the capital once at ~5-10%). The
repeatable part is the MIDDLE two-way book (15-85c): +1,976 over ~1.3 days (mostly 2 Oct, ~1.5k that day), largely netting out, so it
rotates the cash. The anti-value sides lost 1.9k (the value-mode rule removes them). k 0.9 calibration cuts the value sides ~35%.
**Corrected reading of the MM rows above:** the 6% / 29% P(>= 150k) at 0.67 / 1.0k per day stands only if the middle two-way book earns
that on the 15-20k reserve; the one funded day says ~1.5k, from a single day. Gate stage 3 on 24 h of live data: maker P&L at the
Polymarket mark in the middle band >= 0.6k/day before counting on it. Without it, the honest expectation is plan (a)/(b): E ~113-115k,
P(>= 150k) ~0% - and the Senate sleeve is the only lever for 150k (17-24% at P(<= 85k) 4%).

## What Package 10 built (all OFF; `deploy/package10/`; dry run on the live state in `analysis/p10/DRYRUN.md`)
1. `value_mode`: no reducing quote below p - 0.5c (above p + 0.5c for a short) in normal AND reduce-only quoting (the reduce-only skew
   clamp was skipped: today 4 of 6 resting reducing quotes sit below value); the pre-close windows (`exit_hours_before_close` 2,
   `flatten_*` 12/6: a forced exit at fv - 3c in the last 2 h and a flatten reduce-only in the last 12 h, which would have dumped the
   book and any sleeve before the results) are inert and now OVERRIDABLE; `exit_quote` floored; warnings for any value-selling flag.
2. `bloc_delta_enabled`: the closed-form national-factor sensitivity (sqrt(rho) phi(Phi^-1(p)) per share) replaces the share-count
   party cap (`max_bloc_delta_frac` 0.05 of the account per sd); ranges: `worst_case_backstop_frac` up to 1.5, `max_worst_case_frac` 0.35.
3. `alloc_*`: the hourly capital allocator: ranks holdings by edge-held and book levels by edge per $ of collateral to the outcome,
   sells first (IOC at the touch, only if the paired level is still there), reads the cash, then buys; refills a market-making reserve
   (`alloc_mm_reserve` 15k) from the lowest-edge holdings and NO+NO sets (`alloc_set_cost_per_usd` 0.06); pins; bloc check; caps.
4. `value_quote_hurdle` + `capital_ceiling_adding_size_factor` 0.5 (owner's call) = value market making; `take_respect_reserve`.
**Deploy order:** stage0 (code) -> stage1_value_guard (the guard, bloc delta, backstop 1.3, risk cap 0.35: leaves reduce-only, no
value sold) -> stage2_allocator (reserve refills: ~11k from sets in 4 h) -> stage3_value_mm (hurdle, adding factor 0.5, takes respect
the reserve). Rollback = the previous file. The Rep Senate sleeve, if wanted, is a manual buy (pin it with `alloc_pin`).

## Risk limits for an outcome-settled book (owner point 3)
`max_worst_case_frac` 0.30 -> 0.35 (plan (a) needs 31.7k vs today's 30.3k cap; P(<= 85k) ~1-2% for a diversified value book);
`worst_case_backstop_frac` 0.8 -> 1.3 (it protects against every race failing at once, impossible; at 0.8 it pins the value book
reduce-only: worst case 82k vs 80.8k); the share party cap -> the bloc-delta cap 0.05/sd (0.22 with a $20k sleeve); kill switch
`max_drawdown_pct` 0.30 on MARKS -> 0.40 or settlement-based (code default, not overridable): a mark drawdown is not a loss now.
Ruin numbers: value book P(<= 85k) 0.6-1.9%; + $20k sleeve 4-5%; $25k+ 49-65%.

## Point 5: markets close 4 Nov 00:00 UTC, before results; under outcome settlement nothing is valued at marks. If the rule were mixed,
## we are short ~7.7k at the median tilt (20k at p95) + ~535 on the sets; a ~$2.2k longshot-YES hedge would cancel that for ~1.8k of EV.
