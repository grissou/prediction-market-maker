# SPEC F2 + F5 (Package 9; everything OFF by default, in OVERRIDABLE with ranges; house comment style; tests fail-before/pass-after)
Read PLAN_P9.md, analysis/p9/SYNTHESIS.md (sections 3-4), ideas_A.md (A-3, A-11), ideas_B.md (B-11), ideas_C.md (C-4, C-5, C-6, C-9,
C-12). The bot works in YES terms (a NO holding = a short YES position; with `reduce_no_as_sell` a bid that buys back NO goes out as a
covered "sell NO"). Nothing may place an order to move a price, self-cross, trade with itself or exceed the write budget.

## F2 `tilt_exit_take` — flatten the tilt-short book as a taker inside a cost cap
Package 8's tilt exits (`tilt_exit_side`, `ref_guard_exits`, `tilt_exit_full_size`) REST the exit at the book; with ~132 of 150 changes
deferred per cycle and the tilt still rising they take hours. F2 takes them.
- `tilt_exit_take` False; `tilt_exit_take_max_cost` 0.01 (0-0.05): take the exit (sell a long at the best other bid / buy back a short
  at the best other ask: for NO held, a covered sale) when the price is within max_cost of the market's TILTED fair value
  (`tilted_ref_for` / blend as the quote path uses) — i.e. we give up at most 1c vs the tilted fair value, never vs raw Polymarket.
- `tilt_exit_take_per_hour` 15000.0 (0-200000) $ per hour bot-wide; `tilt_exit_take_max_per_cycle` 3 (1-20) orders; writes charged
  as takes (3 each) and only with budget room (`take_writes_ok` pattern / writes_ready).
- Order (A-3/A-11/B-11): longshot NO first (shorts on legs with ref < 0.10: cheapest to exit), then favourite YES (longs with ref > 0.9),
  then the rest; within a group, lines marked BELOW their exit price first (positions.current_price from the exchange, if the bot has
  it in `self.positions` fields, else skip that ordering). Only sides that SHRINK a position that adds to |tilt_exposure| (reuse
  `tilt_exit_side`); size <= position (never flips), <= depth at the best level, <= `tilt_exit_take_max_leg_frac` 0.5 (0-1) of the
  position per take; never while a Polymarket jump guard is active for the market (ref_jump_cooldown_seconds; same limits as
  `ref_guard_exits`); never NO+NO set legs (those are the pair unwind's: `nono_set_part` / set logic) and never basket legs if a
  `basket_legs` attribute exists on the bot (F1 may land in the same branch).
- IOC-like (TTL as take_order_ttl, leftovers cancelled), cash-gated (a buy-back of a short that is not covered needs cash), pre-checked
  before our quotes are pulled (pattern of execute_take / pair_followup_take). status.json `tilt_exit_takes` {count, shares, $, cost}
  and a journal line "TILT EXIT TAKE <label> <side> <qty> @ <px> (tilted fv <fv>, cost <c>)".
- Tests: tests/test_tilt_exit_take.py >= 35 checks: flags off identical to the branch head on a grid; ordering; the cost cap measured
  from the tilted reference (fail-before: the raw reference would allow a worse price); per-hour $ cap and per-cycle cap; writes
  budget refusal; never flips, never exceeds depth; jump guard; set legs / basket legs skipped; cash gate pre-check; covered sale for a
  NO buy-back; validate_overrides ranges.

## F5 `arb_cash_rule` — arbitrage back on, safely, at ~0 cash (owner question 2)
Live `arb_enabled` is false because at 0 cash the race arbitrage left one-legged leftovers (Maine Senate 158/0, South Dakota 13/0):
some legs filled, others were refused for cash. Rule (C-5):
- `arb_cash_rule` False: with it, `take_arbitrage` / `arb_plan` / `execute_arbitrage` for a SELL-side set (bids summing >= 1 + arb_min_profit:
  we sell YES on every leg = short every leg; the shorts lock (1 - price) x qty of cash per leg ... check the exchange's rule in
  cash_gate's need functions and use THEM) only proceeds when `cash_left()` >= `arb_cash_mult` 1.25 (1-3) x total cash need + `arb_cash_reserve`
  2000.0 (0-20000); size <= `arb_leg_depth_frac` 0.8 (0.1-1) x the thinnest leg's depth at the price; OUR OWN resting bids are excluded
  from the bid-sum and the depth (own quotes were 29% of race-cycles at >= 1.04: `orders_by_eid` / self.orders know our prices); all legs
  in ONE batch (already so) with IOC TTL; if any leg of the batch is refused/unfilled, the leftover legs are closed at once as the
  pair follow-up does (reuse `pair_owe` / `pair_followup_*` by registering the owed legs) — never leave a one-legged set for more than
  one cycle. The same rule for the BUY-side set (ask-sum <= 1 - arb_min_profit_buy): cash need = price x qty per leg.
- `arb_sellback` False (C-4 carousel, optional if time allows: >= 90 min left before the red team): a held YES+YES set (long every leg of a
  race) is sold back when the bids sum >= `arb_sellback_min_sum` 1.00 (0.95-1.1) as one IOC batch sized to the smallest leg; this is the
  mirror of `pair_no_unwind_max_cost` for long sets. If time is short, skip it and say so.
- status.json `arb_cash_blocked` count, journal "ARB skipped: cash rule (need X, left Y)".
- Tests: tests/test_arb_cash.py >= 30 checks: flags off identical; the cash rule refuses below the threshold and passes above; own quotes
  excluded from bid-sum and depth; thinnest-leg sizing; a refused leg is owed and followed up next cycle (no one-legged set left);
  validate_overrides ranges.

## Deliver
Code in mm_bot.py (new settings appended after the Package 8 block, in the existing Config order style: a "# --- Package 9 ---" comment),
tests as above, both suites and test_mm_bot / test_cash_gate / test_tilt_exit / test_nono_sets / test_pair_followup / test_pair_sizing
still green; py_compile under Python 3.10 (no match statements? match is fine in 3.10; no except*, no Self, no tomllib). A 10-line
summary of what was built, the numbers from the tests, and anything left undone.
