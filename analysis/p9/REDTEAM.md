# P9 red team (F1 basket, F2 tilt_exit_take, F5 arb_cash_rule / arb_sellback) - 4 Oct 00:50-02:10 UTC

Base: 707a8e9 (claude/finisher-package9). Branch: claude/redteam-p9. Every finding below was first shown failing by a probe or
a check, then fixed. The checks are in tests/test_basket.py, section "P9 red team" (RT-n). Python 3.10. Nothing pushed.

## Findings

| # | Sev | Finding | Fix | Commit |
|---|-----|---------|-----|--------|
| RT-1 | HIGH | `tilt_exposure` counted the basket's shares. A long-tilt basket (say $70k of 8c longshots is about -400k of exposure, against the book's +34k) flips the total's sign. Then the Package 8 tilt exits, F2's taker exits and the T2.4 tilt cap all turn on the market maker's long-tilt positions: F2 pays up to 1c a share to sell longshot YES and stops exiting the short-tilt book. Probe: exposure 0 -> -9400 with one basket leg. | `update_tilt` leaves basket shares out of `tilt_exposure`, as `effective_inventory` already does for the skew. The basket's own exposure is in `tilt_exposure_basket` and status.json `basket.tilt_exposure`. No legs = identical. | d548397 |
| RT-2 | HIGH | The basket ran after the stale takes, aged takes, F2 tilt exits, arbitrage and pair follow-ups without skipping what they had just traded. It could BUY a market in the same cycle that a take or tilt exit had just SOLD there (sell at the bid, then buy at the ask: wash-like). It could also add a leg in a race with a passive pair unwind or owed legs running, and their orders would then trade the basket's leg. Probe: `tilt_exit_takes` -> {"32"}, then the basket bought 3000 on 32 in the same cycle. | `basket_tick` / `basket_orders` take `skip=taken | arb_races`. They make no add on a skipped market or race, nor in a race in `self.pp` or `self.pair_owed`. Forced sales are unaffected (no other feature touches basket legs). | d548397 |
| RT-3 | MEDIUM | A basket add on a leg that already held an ordinary position in the same direction booked only the basket's shares. `decide` then returns NO_QUOTE for the whole leg, so the ordinary part was never quoted, taken, tilt-exited or flattened. The exit, the kill and the T-72h backstop sell only the basket's part, so the ordinary part was held into the close. | The first add ADOPTS the position held in its direction (`basket_leg_basis`, logged). The leg cap counts that position, so a leg that is already big is not added to (or adopted). | d548397 |
| RT-4 | MEDIUM | The basket was sized at the risk model's fair value (70% Polymarket). For a longshot the tournament prices above Polymarket, that is below the price paid: with tilt off, about 0.037 against a 0.08 ask. `held`, the leg cap and the target therefore under-counted, and the basket over-bought up to ~2-2.7x in cost. With tilt on the gap is smaller but grows as s rises past `ref_tilt_max`. Probe: 10000 shares held (about $750 at the book) against an $800 target: 3000 more bought. | `basket_leg_value(..., book)` = the higher of the fair-value and book-price values. Used for held, the leg cap, the tracking sales, status and the stress term in the risk model (`cur_book_fvs`). | d548397 |
| RT-6 | MEDIUM | The kill's liquidation value skipped a position with no level on its exit side, so the position kept its (lagged) mark. Longshot bids vanishing is exactly the crash the kill exists for, and it would be hidden. | A BASKET leg whose FRESH book has no bid (long) / ask (short) counts as sold at 0 / bought back at 1. A stale book, and every ordinary position, keep the ops_fields rule. | 2df1cbc |
| RT-7 | MEDIUM | A forced basket sale (kill, exit, backstop) that cannot go out was only logged at info. Two causes: a short bought back as a YES purchase at about 0 cash, when `reduce_no_on()` is off (exchange refused covered NO sales this run, or live before the self-test passes), or NO locked in a NO+NO set. The kill then silently never completes. | `basket_exit_blocked`: one alert per leg. The remedy is the owner's (free cash / reduce_no_as_sell). | 966e96f |
| RT-10 | MEDIUM | The basket is exempt from reduce-only, and so also from the sum-of-maxima backstop (`worst_case_backstop_frac`). SYNTHESIS says the backstop stays the last resort, but nothing bounded the worst case while the basket bought. | Basket adds are refused (`refused: "worst-case backstop"`) while `total_worst_case` (basket at its stress loss) > `worst_case_backstop_frac` x account. Exits and the kill are unaffected; reduce-only alone still does not stop it. | c4250e6 |
| RT-5 | LOW | `basket_mult_after_fail` (range 0-8) above `basket_mult` RAISED the multiplier after a FAILED test. | mult = min(after_fail, mult) on a fail. | d548397 |
| RT-8 | LOW | The rolling-hour ask-share windows lived in memory only, so every restart re-opened the hour's share: a crash loop could take 0.75 x the top-3 depth on each restart. | Windows younger than 1 h are persisted in status.json `basket.hours` and restored. | 31423d3 |
| RT-9 | LOW | Live ranges allowed dangerous values. `basket_slip` 0.05 is up to +100% over a 5c ask, through the book. `basket_stress_frac` 0 makes the basket invisible to reduce-only and the backstop. | `basket_slip` (0, 0.02), `basket_stress_frac` (0.2, 1.0). | 2581846 |

## Not fixed (LOW: owner's call or design, documented)
- **Switching off during a kill:** `basket_enabled` false while killed RELEASES the legs (spec: off = release, no forced sale), and
  that stops the kill's sale. If "off" is meant as "stop everything", the owner must know that it also stops the sale.
- **No account value:** no kill check (`liq` None). Exits and the backstop still run. A long P&L outage means no kill.
- **No close date and an invalid `basket_exit_utc`:** no exit schedule at all (no buys either). Only the kill would sell restored legs.
- **Positions read lagging past the 120-s grace:** this can shrink a basket leg. The part cut off is then ordinary but unquoted until the
  next add adopts it. It is not sold by the exit / backstop.
- **Writes:** `basket_writes_frac` up to 1.0 with `basket_max_orders_per_cycle` up to 20 lets the basket take most of the 28/min
  during a build, and the market maker's re-quotes and stale-quote cancels wait. Recommend staying at the defaults (0.5 / 4).
- **`basket_floor` / `basket_kill_dd` ranges:** `basket_floor` can go down to 50k and `basket_kill_dd` up to 0.5, so a 50% drawdown
  is allowed. That is inconsistent with the P(<= 85k) < 10% / 20% drawdown objective; recommend live values floor >= 86k, kill_dd
  <= 0.15.
- **F2 restart:** the F2 hourly $ cap (`tet_hour`) is not persisted, so a restart re-opens it.
- **F5 restart:** F5's owed arbitrage state is in memory only, so a restart leaves a one-legged set as ordinary inventory (alerted
  at the time). F5's reversal is capped at the position the arbitrage made. When an older opposite position absorbed part of it, the
  reversal stops at flat (risk-reducing) and alerts after the tries.

## Checked and found sound
- The basket's cash for shorts is (1 - limit) x qty. Every IOC limit stays inside `basket_max_price`.
- The leg cap uses the full (not ramped) target. One order a leg a cycle.
- `quantityTraded` is booked; an absent field books 0, but the fake and the API always return it.
- The kill latch survives a restart and is cleared only by an explicit false -> true. The peak rises only on a value held for 120 s.
- The exit date needs a time zone; one that is invalid or out of range is refused.
- The leg backstop and the global backstop both apply at hold_frac 0. Exits get at least one order a cycle whatever the write share.
- F2: cost signs (long: fv - bid; short: ask - fv), a covered sale's $ at 1 - ask, the cash pre-check before our quotes are
  pulled, our orders cancelled before the IOC, `skip` of markets / races traded this cycle, basket legs excluded.
- F5: own levels skipped whole, including just-sent and unconfirmed orders. Thinnest-leg sizing. The cash rule fits all legs
  together. Reversal never flips. Basket races are never arbitraged or sold back (`take_arbitrage` skips them).
- Flags off: all the changes above are no-ops with no basket legs. Each suite's flags-off grid against the branch head still passes.

## Test counts after the fixes
tests/test_basket.py 149/149 (133 + 16 red-team checks; 3 stress checks re-based on the max(fv, book) valuation),
tests/test_tilt_exit_take.py 50/50, tests/test_arb_cash.py 53/53, tests/test_mm_bot.py 600/600.
