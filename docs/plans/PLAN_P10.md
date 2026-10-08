# PLAN_P10: VALUE MODE (Finisher 2b, 4 Oct 07:30 -> ~17:00 UTC; branch `claude/finisher-package9` continues; READY: Package 10)

## The owner (4 Oct ~07:00 and ~07:30 UTC)
07:00: "go crazier; a market maker CAN make the money if it market-makes quickly enough; ideas outside the risk limit will be considered; 10 h."
07:30: **STRATEGY CHANGE. SIG pays positions out at the OUTCOME.** This reverses Package 9's catch-up plan: the long-tilt basket is
negative-EV at the outcome (the leader's +600% is mark-to-market), our toward-Polymarket book is +EV (marks 99.9k, EV at the outcome
~108.5k, +8.6k / +9% of position value; 86 positions priced, 4 without a reference). Features that sell value at compressed prices are
wrong now: Package 8 tilt exits, T2.1 `ref_tilt_enabled`, `take_tilted_ref` (the raw-Polymarket takes were buying value: Rep Wyoming
Senate at 0.91 vs 0.975, 352 takes overnight), paying 2c to unwind riskless sets unless the cash goes into value. **Live = Package 8
(5bf3e45) in "value mode"**: `ref_tilt_enabled`, `tilt_exit_priority`, `tilt_exit_full_size`, `ref_guard_exits`, `ref_guard_tilted`,
`take_tilted_ref` all false; `pair_no_unwind_max_cost` 0.003; unchanged `reduce_no_as_sell`, cash gate, set-aware bids, adding factor 0,
`arb_enabled` false, backstop 0.8, writes 28. Package 9 is NOT deployed. "Make sure we can still market-make despite this."

## Package 10 = maximise expected value AT THE OUTCOME, subject to ruin limits; target still 150k+ if achievable
Rank everything by P(final >= 150k), P(final >= 120k), E[final], P(final <= 85k) at the outcome, with CORRELATED outcomes (Polymarket as
the probabilities, a party / region factor: a Dem or Rep wave moves many races together).
1. Capital is the constraint (~100% deployed, edge ~9% per dollar). Cheapest ways to turn low-edge capital into high-edge capital:
   riskless NO+NO sets (~0 edge; unwind at <= 2c only if the cash goes into a >= 5c-edge position the same hour), low-gap positions,
   market-making inventory. A capital allocator ranked by edge per dollar to the outcome.
2. Value accumulation done right: fair value = Polymarket (minus a measured Polymarket error margin), not the tilted price; size by edge
   and correlated risk (fractional Kelly at the portfolio level, by party bloc). When to buy favourites / sell longshots, min edge, size.
3. Correlated risk at settlement: party delta (-15.4k at 22:47 / -13.8k at 07:30), settlement risk 25.0k vs the 30% cap, the sum-of-maxima
   backstop (pins reduce-only at 0.8): which limits are right for an outcome-settled book; recommended values with ruin numbers.
4. The mark path to 4 Nov: the tilt grows (0.11 -> 0.14 overnight; ~350 per point on our exposure). Interim marks do not matter for the
   payout, but the bot must never panic-sell +EV positions into them: audit every reduce-only / backstop / kill / unload path.
5. Markets close 4 Nov 00:00 UTC (3 Nov 19:00 EST, before results): confirm nothing can be valued at marks; if it could, how to hedge.
7. (owner 07:40) Rank and the +50% question: model the final leaderboard under outcome settlement. If the top accounts hold long-tilt
   books (longshot YES / favourite NO, marked up 4-6x), what is their payout at the outcome, and what final account gets us into the
   top 10 / top 50? Then odds for three plans: (a) the max-EV reallocation held to the outcome; (b) (a) plus recycling on convergence
   (sell positions marked to within 2c of Polymarket and redeploy), with the tilt path's uncertainty; (c) a correlated party-bloc bet in
   competitive races, sized to reach P(final >= 150k) of 20 / 30 / 40%, with the matching P(final <= 70k / 85k). One table, then a
   recommendation.
6. Market making continues: in value mode it is value accumulation at the bid (favourite bids, longshot asks in the tails; two-way in the
   middle where flow is two-way), limited by cash -> the allocator keeps a market-making cash reserve (F: ~1.0-1.4k/day on 15-25k of
   rotating cash, and the inventory it acquires is itself +EV at the outcome).

## Phases
A 07:30-09:30 explore: H = the outcome model (probabilities, correlation, the current book's final-value distribution, the frontier of
  concentrated / wave variants inside AND outside the limits, the right risk limits with ruin numbers, Polymarket's calibration margin,
  Q5); I = the capital allocator + value-MM design with data (edge per $ of every position, sets, rotation gains net of spread, the
  MM reserve economics, the quoting rule, sizes by bloc) + the audit of every mark-driven selling path in the code. F (done) = MM ceiling.
B 09:30-11:00 screen: `tests/live_sim.py` judged on the Polymarket mark `pnl` (= settlement EV; the right judge again) and the outcome
  model's variance; data-only checks on the snapshot.
C 11:00-16:30 build behind OFF settings with tests + red team + dry run on the live state (tests/test_p9_dryrun.py harness): the
  allocator, value-MM quoting sides, bloc-correlated settlement risk limits, the no-panic-sell guard; "READY: Package 10" with
  START_HERE, deploy/package10 (owner's live value-mode file as the base), PR update, a one-page plan with the odds.
Rules unchanged: never touch the server / exchange, never merge, sub-agents never push, Python 3.10, commit + push every step, STATUS block.
