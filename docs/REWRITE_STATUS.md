# Rewrite status

```
STATUS (2026-10-10, branch rewrite) - IN PROGRESS: rewrite 2 (docs/REWRITE_FIX_BRIEF.md)
done:     fix 1 - the cause of "no orders": the request budget left the books nothing (below); foreign orders;
          DRY PLACE logging; books_fresh / books_dirty / orders_foreign / blocked_by in status.json and the line;
          mm_quote_frac 0.002 -> 0.0005 (the replay still runs at 0.002: it compares logic, see below)
left:     the recorder; the stripped phone summary and alerts; line count; READY
lines:    see the table (updated at READY)
```

## Rewrite 2: why the 10 Oct shadow planned nothing (fix 1)

Cause: at `requests_per_minute` 20 the full REST check ran every 30 s (`full_check_s`) and cost ~7 requests
(positions, fills, open orders, account, 3 bulk-price pages): ~14 a minute. Book reads stopped while 10 or fewer
requests were left in the minute (a fixed `REQUEST_SPARE` 10, sized for 80/min), so after the first cycle the bot
read almost no books. With no fresh book no strategy plans anything (market making, value quotes and the ladder
need the book; the allocator found "no level"), and the tilt needs 50 markets with a book mid, so it stayed null.
`priced 226/237` counted Polymarket prices, not books, so the status line hid it. The feed's dirty flags were
consumed every cycle even when the budget could not serve them (lost, not applied: the feed carries no levels).

Fix (`bot.py` header BOOKS, FOREIGN):
- the full check is spaced so it spends at most 40% of the budget (60 s at 20/min, 30 s at 80), account reads
  between full checks at most 20%; the spare is 10% of the budget (2 at 20/min); the books get the rest;
- a book stays fresh while the bulk tops at each full check still match it (net of the account's own orders), so
  3 requests confirm every book; a moved top or a feed flag marks it dirty until a REST read; dirty flags persist;
- reads go never-read and dirty first, then oldest; at 20/min all 240 books are fresh within 40 minutes (test);
- foreign orders (on the account, not placed by this process) are never cancelled, adopted or counted, stripped
  from books, never traded against (blocked_by self_trade), and never stop quoting; a live stop cancels only ours
  (cancel-all only when no foreign order rests). Live, the old code cancelled the live bot's orders on its first
  cycle and its cancel-all on stop pulled them all;
- dry run logs `DRY PLACE <market> <side> <qty>@<price> <tag>` (and `DRY CANCEL`) per order;
- status.json: `books_fresh`, `books_dirty`, `orders_foreign`, `blocked_by` {rule: orders refused this cycle, and
  no_book: priced markets without a fresh book}; the status line shows them.

Tests: `tests2/test_shadow.py` (24 checks) - 240 markets, 170 foreign orders, 20 requests/min on a sliding minute,
the feed churning 3 books a cycle, dry run: DRY PLACE within 10 minutes, all books fresh in 40, tilt 8% recovered,
never over budget; live with foreign orders: none cancelled or adopted, quotes beside them, a stop leaves them;
crossing a foreign order is refused and counted; a dirty flag without a book builds none and is remembered. On the
old code it fails the same way the shadow did (resting 0, tilt None, foreign orders cancelled).

`mm_quote_frac` default 0.0005 (~52 shares at 105k). `tests2/test_replay.py` runs the new bot at 0.002, the size
the old bot's plan used on the 4 Oct snapshot (mostly 200-share quotes), so its factor-of-two comparison still
measures the logic; at 0.0005 101 rows differ by size only. REPLAY.md is unchanged by fix 1.

## Line counts

| File | Lines |
|---|---|
| mmbot2/bot.py | 544 |
| mmbot2/config.py | 243 |
| mmbot2/exchange.py | 729 |
| mmbot2/ladder.py | 189 |
| mmbot2/mm.py | 233 |
| mmbot2/ops.py | 315 |
| mmbot2/pricing.py | 229 |
| mmbot2/risk.py | 330 |
| mmbot2/state.py | 117 |
| mmbot2/value.py | 543 |
| mm_bot2.py | 14 |
| total | 3486 |

exchange.py (728) is the largest: the realtime feed with its dead-socket and revision-gap handling (~190) and the
50-state table for the per-state cap. value.py (519) holds the floor, the tail quotes and the allocator.

Tests (not counted in the target):

| File | Lines |
|---|---|
| tests2/fakes.py | 282 |
| tests2/test_bot.py | 154 |
| tests2/test_exchange.py | 330 |
| tests2/test_ladder.py | 157 |
| tests2/test_mm.py | 199 |
| tests2/test_pricing.py | 144 |
| tests2/test_replay.py | 179 |
| tests2/test_risk.py | 205 |
| tests2/test_stress.py | 434 |
| tests2/test_value.py | 239 |
| total | 2323 |

## Replay differences (old bot vs new on the 4 Oct snapshot)

`tests2/test_replay.py` runs the old bot through `tests/dryrun_harness.py` (as `tests/test_p15_dryrun.py` does) with the
10 Oct live settings, and the new bot on `tests2/fakes.FakeClient` seeded with the same state, 6 cycles each; the table
is in `analysis/rewrite/REPLAY.md`. Of 186 (market, side) pairs either bot traded: 118 the same within a factor of two,
24 the same market and side at a different size, 15 the old bot only, 29 the new bot only. No side is ever reversed.

- **Sizes, 20 rows (old 2-50, new 199):** the old bot halved quotes while capital in positions was above 70% (the capital
  ceiling, `capital_ceiling_adding_size_factor` 0.5 on a 100-share quiet-market plan); the rewrite has no capital
  ceiling (the risk caps and the cash gate bound it) and quotes `mm_quote_frac` x account = 200.
- **Sizes, 2 rows (U.S. Senate, old 5,000-7,500, new 199):** the old bot gave the headline control markets a flat
  10,000-share limit (`headline_position_frac`); the rewrite quotes them like any market and the allocator never sells them.
- **Old only, 16 rows (Alaska, Nebraska, California, Alabama, Oklahoma: ~50-100 shares):** markets without a liquid
  race-scaled Polymarket price (three-candidate races with an unpriced independent leg); the old bot quoted them
  around the book's own price, the rewrite leaves unpriced markets alone (README §4.1 calls them unpriced).
- **New only, 23 market-making rows (~199 shares):** middle-band markets the old bot left unquoted that cycle, most
  likely through rules not carried over (the turnover-dead and churn controls, the fl-bias side, the capital ceiling).
- **New only, 5 ladder rows (Rhode Island, Connecticut, Montana, WA-03):** the old ladder paces its first placements
  over several cycles (harvest_writes_frac per cycle after the quotes); the rewrite places the best-edge levels at
  once within the 10k budget. Rhode Island stays inside the 15k state cap (6.7k held + ~6k of levels, at p).

## Open questions for the owner (from the module headers)

- config: skew_max 0 live while skew_target_inventory is on: was a price lean ever intended? (the rewrite leans by size)
- pricing: the tilt compares raw Polymarket with a race-normalised book mid, as live did: intended?
- risk: the stale-cash rule refuses cash orders rather than stopping the gate; unpriced markets are valued at the
  race remainder / book mid / 0.5. (mm_room_guard: built, owner decision 2.)
- value: a swap in flight is not saved across a restart; a write-budget-deferred allocator IOC is re-planned next
  interval rather than retried. (Risk-neutral swaps continue while adds are paused: built, owner decision 1.)
- mm: quote size and the 2-quote inventory cap; the dropped buy-back deferral while cash is low.
- ladder: the headline control markets are excluded (as the old bot); new ladder adds while value adds are paused
  are left to the gate.
- ops: the covered "sell NO" probe is back (owner decision 3); with the fallback on, the gate still counts a
  buy-back as cash-free, so the exchange may refuse a few for funds (harmless, logged).
- bot: no reducing-side value quotes in the tails (the allocator and the ladder exit those positions).
- exchange: is a NO-side fill's price always the NO price? a "sell NO" beyond the NO held is trimmed.

## Bugs the stress test found (fixed)

1. Fills were subtracted twice from partly filled orders on a full read (fills are now noted before the order list).
2. A kept order the gate trimmed stayed at full size; 3. an order the gate always trims re-quoted every cycle (the gate
now runs before reconcile, which compares with the admitted size); 4. after a failed cancel a new order could cross the
order still resting (every placement is checked against what really rests). Also: a buy-back while short is trimmed to
the NO held, as the exchange does, so it does not re-quote every cycle; value quotes need a fresh book.

## Not in the rewrite (as the brief says)

Arbitrage, takes, NO+NO pair unwinds (the 4 Oct snapshot holds 1 set worth 1.0: `status.json` nono_sets), the momentum
sleeve, the capital ceiling, mark fragility, activity size plans, fl-bias, churn control, burst mode, the recorder.

## Ideas not built

- Report write-budget-deferred allocator IOCs back to the allocator so it retries within the interval (value.py report).
