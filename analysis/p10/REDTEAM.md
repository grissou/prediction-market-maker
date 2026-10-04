# Package 10 red team (branch claude/p10-redteam, on 37f08b6)

Scope: `git diff a67d9da 37f08b6 -- mm_bot.py` (Part A value_mode guard, bloc delta, close windows, ranges, hurdle; Part B
allocator, reserve, NO+NO set registration), run against the owner's switch-on plan: ~101k account, ~0 cash, 28 writes/min,
live file = Package 8 in value mode (deploy/package10/settings_override.stage0_code_only.json).
Method: each finding confirmed with a test that fails on 37f08b6 and passes after the fix (red-team sections at the end of
tests/test_value_mode.py and tests/test_alloc.py), plus the dry run on the live snapshot (tests/test_p10_dryrun.py,
analysis/p10/DRYRUN.md), which found RT-7. Every fix is flag-gated or identical at the defaults: stage 0 sends the same
orders as the Package 9 head on the live seed, and the flags-off grids of both suites still pass.

## Fixed

| # | Severity | What was wrong | Fix |
|---|---|---|---|
| RT-7 | MEDIUM | **A reducing quote flipped the position at a price the adding rule refuses.** In value mode the reducing side has no size cap at the position. Its part beyond the position opens the other side, and that part ignored the A4 hurdle (tails) and A1's "never sell below p". Found by the dry run on the live book: a short of 41 Rep Nebraska Governor (p 0.91) bid 120 @ 0.89, so 79 shares open a long at a 2% edge under the 8% hurdle; 4 such quotes in stage 3. With value_mode alone, a long 30 at p 0.50 asked 500 @ 0.495, selling 470 short below p. | `compute_quote`: when the reducing price is beyond the adding rule's price (the hurdle price in the tails, else p), that side's size and keep size stop at the position. |
| RT-1 | MEDIUM | **A registered NO+NO set race was unwound beyond what the plan needed.** `arb_plan` sized the unwind at `min(sets, depth, pair_no_unwind_max_sets 1000)` at the allocator's cost (up to 6% per $), whatever the plan asked. A $50 refill could unwind 1,000 sets at up to 6c of EV each, about $57 per occurrence, and the extra proceeds then fed one pair's buy. | The registration carries `sets`, the planned sets, summed over the pairs of one race. `arb_plan` caps the unwind at that number only when the allocator's cost is what admits it. An unwind the race's own `pair_no_unwind_max_cost` admits keeps its normal size. |
| RT-2 | MEDIUM | **The allocator bought while the bot was in global reduce-only** (worst case or settlement risk over its cap). Every other adding path stands still in that state. Spare-cash buys and the buy half of pairs went on. | No levels are planned in reduce-only, so only reserve refills run (`blocked_by: risk`). A paired sale whose buy could not follow is held, and a sold pair's buy waits. If reduce-only lasts past `ALLOC_BUY_WAIT`, the pair expires and the cash stays. |
| RT-3 | MEDIUM | **Inside the stop window, value_mode let takes, arbitrage and the allocator keep trading.** value_mode made every pre-close window -inf. The checks keyed on `flatten_hours_before_close` (stale-quote and hold takes, arbitrage's "closing", no-chase, `critical`) then never saw a window, so they ran up to the close, inside `stop_minutes_before_close`, whose rule is "nothing at all". The stage-1 file (windows 0) had the same gap even with value_mode off. The allocator had no close check at all. | `close_window` returns at least the stop window (15 min): in value_mode, or when a window is set below it. The defaults (2/12/6 h) are unchanged. `alloc_market_ok` refuses markets inside that window. Checked: resting quotes are still cancelled at the stop window. |
| RT-4 | MEDIUM | **A Polymarket outage zeroed the bloc delta and opened the party cap.** `bloc_refresh` rebuilt the sensitivities from this cycle's liquid prices only. A missing or illiquid price dropped the contract from the sum, so the bloc delta drifted toward 0 and the cap stopped binding. The share count it replaces never needed Polymarket. | A contract with no liquid price this cycle keeps its last sensitivity. A fresh price replaces it. |
| RT-5 | LOW | **Reserve refill sales had no bloc check.** Only pairs had one, so a refill could push \|bloc delta\| past the cap. | The refill loop applies the pairs' rule: skip a sale that would leave \|bloc\| above the cap and larger than before (`blocked_by: bloc`). |
| RT-6 | LOW | **A pin label matching no market (a typo, another spelling) pinned nothing, silently.** | `warn_settings` (start-up and override time) logs a WARNING naming each `alloc_pin` label that matches no market. |

Tests: tests/test_value_mode.py 92 -> 101 checks (RT-3 x4, RT-7 x3, RT-4 x2; one builder check now expects the 0.25-h
stop window instead of -inf). tests/test_alloc.py 114 -> 123 (RT-1 x2, RT-2 x3, RT-3, RT-5 x2, RT-6). Fail-before was
shown on 37f08b6: RT-1 unwound 1000 vs 51 planned; RT-2 planned the buy; RT-3 had window -inf and planned the pair at 10 min
to close; RT-4 gave a bloc delta of 0.0; RT-5 sold Rep Alpha past the cap; RT-6 gave no warning; RT-7 sized 500 vs 41 and
500 vs 30.

## Caveats (not code bugs; for the owner / lead)

- **C-1 (HIGH impact, design): the market-making reserve does not protect against the stale-quote takes.** `alloc_mm_reserve`
  binds only the allocator. The takes (cycle step 6b) run before both the allocator and quoting, and spend whatever the gate shows.
  - Dry run stage 2: the sets freed $11.0k in 4 h, the takes spent $12.7k, and the 15k reserve was never reached. The allocator
    re-plans refills every hour.
  - Stage 3: of +20k given to the fake, the takes spent $19.3k in 10 cycles. decide computed 209 adding sides, but only 5 rest.
  - The takes are value buys: +7.7% per $ at the outcome, against the sets' 3.2% per $ given up to fund them. So the chain
    "sets -> takes" is net +EV (~+$600 over the 4 h), but it is not the market-making reserve the plan describes.
  - Owner's choice: (a) accept it: the allocator becomes a set-unwinder feeding the takes, and market making gets what is left;
    (b) `take_enabled` false (or a higher `take_edge`) at stage 3: the reserve fills at ~2k/h after the first hour (6.2k / 8.2k /
    10.2k / 12.1k by hour, takes off); (c) a follow-up code change: takes keep `alloc_mm_reserve` while `alloc_enabled`
    (~10 lines, not done here: it changes the take path the owner relies on).
- **C-2 (MEDIUM): in practice the allocator only refills the reserve.** No pair is planned while cash is below the reserve, and
  at ~0 cash with the takes spending, that is always (`ev_gain_est` 0 in every run). In the convergence scenario (books at
  Polymarket +-1c) it frees $19.9k (sales at edge-held 1.0-1.2%, plus sets) and buys nothing: no level clears the 5% edge.
  "Recycling" happens only through the reserve and whoever spends it.
- **C-3 (LOW): the middle-band inventory cap scales with the market's quote size.** In the headline markets that is 12.1k
  shares, so `value_mid_inventory_quotes` 2 allows ~24k. Stage 3 rests an ADDING ask of 3,296 Dem U.S. Senate on a 5,000 short.
  The allocator never trades headline markets, but the market maker does.
- **C-4 (LOW): refills give up to `alloc_max_edge_sell` per $ with no buy.** That is by design, ~1% in the convergence scenario
  (~$150 on $14k). The ranges allow `alloc_mm_reserve` up to 100k and `alloc_max_edge_sell` up to 0.5. Together those would let
  refills sell the book at 15k$/h at up to 50% edge-held, every hour, since the reserve can never be reached. Keep 0.02 / 15k;
  narrowing the ranges (0.1 / 50k) is a one-line follow-up if wanted.
- **C-5 (LOW): stale p.** The value floor and the hurdle use the cycle's reference even when ref_prices kept an old price
  through failed downloads. Only the allocator checks the age (30 s). The floor can only make a reducing quote less aggressive,
  never worse than without it (with `ref_weight` 1 the fair value is the same p). The hurdle on a stale-high p could add at
  under 8% of real edge. The allocator itself is safe.
- **C-6 (LOW): writes.** The fake reports unlimited writes, so the allocator ran its 4 orders a cycle. Live it gets
  floor(0.3 x writes left / 3), about 2 orders a cycle at 28/min, so plans take proportionally more cycles. Most writes the
  fake saw in one cycle: 29 (stage 2), i.e. live the limiter spreads them.
- **C-7 (LOW, checked): no churn.** The allocator sells a middle-band long at <= 2% below p, and the market maker may bid it back
  1c below p. That round trip is positive (sold above the rebuy), not a loss. In the tails the hurdle keeps the rebuy >= 8% below
  p. No adding quote resting at or through an allocator sale price was found in either scenario (checks in the dry run).
- **C-8 (note): the stage-2 pin example is redundant.** "Rep U.S. Senate,Dem U.S. Senate" are headline races, already excluded
  while `alloc_headline` is false. Pin the core holdings you want kept, by exact label.
- **C-9 (note): stage 3's adding factor 0.5 is the owner's call.** It lets the market maker add while the capital ceiling is
  active (capital in positions 0.79-0.91 in the dry run), within the cash gate.

## Checked, no finding

- Edge math and signs: held long (p - bid)/bid; held short (ask - p)/(1 - ask) (= NO sold at 1 - ask); level buy
  (p - ask)/ask; level short (bid - p)/(1 - bid). Race scaling. The paired level is re-read on a fresh book before the sale and
  again before the buy (within 0.5c).
- A short is bought back only as a covered "sell NO" of its lone part (never the NO+NO set part). Never a flip: sales are capped
  at the position, and no buy goes against a short or the reverse. The buy waits for a cash read taken after the sale and goes
  through the cash gate (`cg_spent` counts it).
- IOC orders cancel our own orders on that exchange first, refused if that is not confirmed, so there is no self-cross. The
  leftover is cancelled at once. The exchange the allocator traded is not quoted that cycle.
- The value floor holds in normal and reduce-only quoting and after `hold_quote` (`value_floor_quote`), with the keep limits
  moved, so a resting order below the floor is replaced. It only moves a price away from the other side, and step 4 re-runs, so
  nothing crosses. Dry run stage 1: 40 reducing quotes, 0 below p - 0.5c, 0 crossing. Before it, 4 of 6 sat below p - 0.5c
  (the reduce-only skew).
- The bloc delta matches H (+2,516/sd vs H's +2,553). The sign follows party_delta (+ = Republican), and the cap does not bind on
  the live book.
- Exceptions: `alloc_tick` is wrapped (an allocator bug skips the allocator, never the cycle). The bloc maths clamps p into
  (0, 1) and skips p_ind >= 1.
- Pre-close: `stop_minutes_before_close` still returns NO_QUOTE and the reconcile cancels what rests (probed with the old and
  the new `close_window`). Cancel-all at shutdown is not keyed on any window.
- Flags off: stage 0 on the live seed sends identical orders with identical status keys to the Package 9 head (a67d9da), and
  both suites' grids are unchanged.
