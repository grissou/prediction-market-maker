# SPEC F1: `basket_*` — the long-tilt basket planner (Package 9; everything OFF by default, in OVERRIDABLE with ranges)
Read PLAN_P9.md, analysis/p9/SYNTHESIS.md (sections 2-3), ideas_B.md (B-2, B-4, B-10, B-14), ideas_D.md (D-1, D-13, D-17, D-18, D-22),
ideas_C.md (C-10, C-11). The bot works in YES terms: "long the tilt" = long YES on a longshot leg (ref < basket_max_ref) OR short YES
(= hold NO) on the favourite leg of the same race; both are ordinary positions the bot already knows how to hold. Nothing here may
place an order to move a price, self-cross, trade with itself, or exceed the write budget. This is real money (play, but the rank):
every path that SENDS an order must be covered by a test on the fake exchange (tests/fakes.py) and must refuse when in doubt.

## Settings (Config block, after the Package 8 block; comments in the house style; all in OVERRIDABLE)
- `basket_enabled` False. Master switch. Turning it OFF while a basket is held: the basket legs are released to the ordinary book
  (no forced sale), the state says "off (N legs released)".
- `basket_mult` 5.0 (0.5-8): target basket $ = mult x cushion.
- `basket_floor` 86000.0 (50000-200000) and `basket_floor_peak_frac` 0.85 (0.5-0.99): floor = max(basket_floor, frac x peak), peak = the
  highest LIQUIDATION value seen since the basket was switched on (persisted; restored from status.json on restart).
- `basket_cap` 80000.0 (0-200000): basket $ never above this; also never above `basket_cap_frac` 0.85 (0-1) x account value.
- `basket_cushion_measure` "liquidation" (fixed; document): cushion = liquidation_value - floor - basket_impact_frac x own net $ bought
  in the last `basket_impact_hours` 24 (0-72) h (`basket_impact_frac` 0.06, 0-0.3; D-18: our buying raises the measured tilt).
- `basket_build_hours` 4.0 (0.5-48): the target is approached linearly over this many hours from switch-on (ramp), then tracked hourly.
- `basket_max_ask_share` 0.25 (0.05-1): per market, buys in any rolling hour <= this share of the visible ask depth (top 3 levels at
  switch-on of that hour); `basket_first_build_ask_share` 0.75 (0.1-1) applies during the first build.
- `basket_max_ref` 0.10 (0.01-0.5): a leg qualifies as "longshot" when its Polymarket probability (raw reference, scaled to the race)
  is below this; the favourite-NO route of the same race is the alternative; `basket_max_price` 0.25 (0.02-0.6): never pay more than
  this per YES share (or 1 - this for a short, i.e. never short a favourite below 0.75).
- `basket_min_legs` 20 (1-200), `basket_max_leg_frac` 0.08 (0.01-1): $ per contract <= this fraction of the target (diversify).
- `basket_exclude_headline` True, and legs whose label starts with "Ind" (independents) are never bought (D-11); a label list setting
  `basket_exclude_labels` is NOT needed (keep it simple): hard-code the independent rule, document it.
- `basket_exit_utc` "2026-10-18T12:00:00Z" (string): from this time the basket is sold down to 0 over `basket_exit_hours` 24 (1-168),
  and no buys from `basket_no_add_days` 7 (0-30) days before it. Hard backstop: never hold past 72 h before the close
  (`hours_to_close()` exists). Validate the string; an invalid one = feature refuses to start (alert once).
- `basket_test_hours` 36 (6-168), `basket_test_min_s` 0.15 (0-0.5), `basket_mult_after_fail` 1.5 (0-8): at switch-on + test_hours, if
  tilt_s (the live estimator, `self.tilt_s`) < test_min_s OR its 12-h slope <= 0, mult is cut to mult_after_fail for the rest of the run
  (latched, persisted; log + alert once).
- `basket_kill_dd` 0.15 (0.02-0.5): if liquidation value < (1 - kill_dd) x peak, OR < floor: KILL = sell the whole basket down over
  `basket_kill_hours` 2 (0.25-24) as a taker, then the feature latches OFF (`basket_state` "killed"; persisted; stays killed across
  restarts until the owner sets basket_enabled false then true again). Alert once.
- `basket_writes_frac` 0.5 (0-1): share of the write budget the basket may use per cycle; `basket_max_orders_per_cycle` 4 (1-20).
- `basket_slip` 0.005 (0-0.05): IOC buy limit = best ask + slip (sell: best bid - slip); orders are IOC-style (TTL as `take_order_ttl`),
  and leftovers cancelled at once (reuse the take/arbitrage machinery: execute_take / apply_batch / place_orders / order_ttl).
- `basket_stress_frac` 0.4 (0-1): RISK MODEL (C-10): in settlement_risk / total_worst_case the basket legs are counted at
  stress_frac x their $ value instead of their settlement loss (both measures), and the sum-of-maxima backstop ignores them the same
  way. Document that this is what makes the bet possible at all and that the CPPI floor + kill is the real control.

## Behaviour (one method group `basket_*` on Bot; one call per cycle after the cash gate and before quoting; the planner is pure and
## tested separately from the sender)
1. `basket_tick(now, books, inventory, cash_left)`: state machine OFF -> BUILDING -> TRACKING -> (CUT) -> EXITING -> DONE / KILLED.
   Compute floor, peak, cushion (net of impact), target = clip(mult x cushion, 0, cap, cap_frac x account); ramp during build_hours.
2. Selection (`basket_candidates`): for each non-headline race with a liquid Polymarket reference, the legs with ref < max_ref; for each,
   the cost per unit of tilt exposure via the two routes (buy YES on the longshot at its ask vs short YES on the favourite at its bid;
   B-4); choose the cheaper; skip legs with price > max_price, legs with no ask/bid, independents, legs where WE have a resting order
   on the opposite side (cancel our quotes on basket legs first: the market maker does NOT quote basket legs while the basket holds
   them — simplest rule: `decide` skips quoting on eids in `self.basket_legs`). Rank laggards first (D-8): leg's own s_i (from the
   estimator's per-market data if available, else (mid - ref) / (c - ref)) ascending.
3. Sizing: spread target across >= min_legs, <= max_leg_frac each, <= ask-share caps, <= cash_left (longs need cash: price x qty; shorts
   lock (1 - price) x qty: ask the cash gate's need functions), <= write budget share; per cycle at most max_orders_per_cycle orders.
4. Sending: taker orders only (IOC-like; cancel leftovers); never at a price that crosses our own resting order; never when the cash
   gate says no; never during the reduce-only state? NO: the basket is exempt from reduce-only (that is the point) BUT only when
   `basket_enabled` and the stress risk model is in force; document it.
5. Exits (EXITING / KILLED): sell basket legs as a taker down to 0 at bid - slip, spread over exit_hours / kill_hours, laggards last? No:
   richest (highest s_i) first; shorts are bought back the same way (cover_no_qty / covered sales apply to NO we hold: a short YES is
   NO held, so the buy-back of a short goes out as a covered sale when reduce_no_as_sell is on - reuse the existing path).
6. Bookkeeping: `self.basket_legs` {eid: shares (signed, YES terms)}, `self.basket_cost`, `self.basket_bought_24h` deque of (t, $),
   `basket_state`, peak, floor, cushion, target, held $, impact $, test result, legs count, last action, in status.json under
   "basket"; the 2-hourly summary gets " | basket $X (N legs, state)". Persist what a restart needs (peak, switched_on_at, test result,
   killed) in status.json and restore like the tilt state (see load_tilt/restore_rampin).
7. Interaction with the rest: effective_inventory for SKEW on non-basket markets excludes basket legs; the capital ceiling / adding
   factors ignore basket orders (they are takes, not quotes); the tilt exits (Package 8) never touch basket legs; takes (take_stale_quotes,
   take_aged, pair unwinds) never touch basket legs.

## Tests: tests/test_basket.py (>= 60 checks), fail-before/pass-after on the fake exchange:
flags off = byte-identical behaviour (grid of decide / settlement_risk / status against the branch head); target math (floor, peak,
cushion net of impact, cap, ramp); selection (route choice, exclusions, laggard order, min legs, leg cap); sizing caps (ask share,
cash, writes, orders per cycle); sending (IOC, no self-cross, leftovers cancelled, refusal paths); test-hours cut; kill on floor and on
drawdown, latched across a simulated restart; exit schedule by date and the T-72h backstop; risk model stress counting; basket legs
not quoted / not touched by takes and tilt exits; validate_overrides ranges; py_compile under Python 3.10 syntax.
Also add `basket` fields to the settings-contiguity checks if a test enumerates Config blocks.
