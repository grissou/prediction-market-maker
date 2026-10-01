# Engineering notes (Run A)

Branch `claude/pr1-safe-fixes` (deploy-first) and `claude/pr3-ops-analytics`. Base: main a49587c (live code).
Numbers are simulated seconds from `tests/scenario.py` (see "Scenarios"). Updated after every item.

## Status

| # | Item | State |
|---|---|---|
| 0 | Scenarios: slow writes + crowded book (`tests/scenario.py`) | done |
| 1 | Self-test: busy (409/timeout/429/5xx) = retry later, on its own thread | done |
| 2 | Parallel, time-boxed, prioritised order sending | todo |
| 3 | Book freshness (bulk re-verify, book_stale 900, max_books 30) | todo |
| 4 | Recover unconfirmed orders + metadata (fill attribution) | todo |
| 5 | Realtime reconnect backoff resets after a healthy session | todo |
| 6 | Faster Polymarket | todo |
| 7 | Burst protection | todo |
| 8 | Churn control for a crowded book | todo |

## Scenarios (`python tests/scenario.py [slow|crowded|both] [seeds] [minutes]`)

The real `Api.call` (throttle, retries, idempotency keys) and real threads run against a fake HTTP exchange
on a scaled clock (20x). `slow` = day one's open: 70% of writes take 15-30 s (past the 15 s timeout) but land;
same-key retries get 409 REQUEST_IN_FLIGHT; reads 0.5-1.5 s; 236 markets, books loaded before the open; one
taker picks off quotes left >= 1c wrong 3 s after a Polymarket move. `crowded` = writes 0.5-2 s (5% slow) and
three rival bots (re-check every 1 / 2.5 / 5 s, floors 1 / 1 / 1.5c) that penny us down to their floor and pick off
quotes left >= 1c wrong after a Polymarket move. Polymarket: every 5 s, small drift + 3% chance of a 1-2.5c move.

Baseline, main a49587c, 10 min, seeds 1-3:

| metric | slow | crowded |
|---|---|---|
| time to 50% / 80% of markets quoted (s) | 218-253 / 340-363 (one never) | 38-44 / 87-105 |
| share of markets quoted at 1 / 2 / 5 / 10 min | 0-8% / 17-21% / 63-75% / 45-72% | 57-65% / 88-96% / 98% / 93-97% |
| longest cycle (s) | 315-339 | 437-464 |
| worst time a headline quote sat >= 0.5c wrong after a Polymarket move (s) | 0-253 | 178-270 |
| write requests / min | 4.5-6.3 | 6.0-6.6 |
| quotes picked off after a move (count / shares / cost) | 30-52 / 2.9k-22k / 51-560 | 54-93 / 6.8k-44k / 127-635 |
| fills not attributable to a quote (unmatched) | 170-303 of 313-453 (54-69%) | 0-6 |
| duplicate quotes on one side (max at once) | 0-1 | 1-2 |
| self-test killed the bot (exit 3) | 1 of 3 runs (2 of 3 in an earlier calibration) | 0 |

Day one for comparison: first cycle 4.5 min, 28-37 of 237 priced at 16:20, 381 of 828 fills unattributed (46%),
self-test passed by luck. Crowded: the cycle is serial per-market cancels (~1.3 s each) after every rival penny,
so one cycle takes the whole session; that is the churn trap.

## Item 1 - self-test (done)

Busy answers (timeout, 409, 429, 5xx, cancel-all timing out) retry after `selftest_retry_seconds` (60) with one alert
after `selftest_alert_after` (900 s); the test runs on its own thread (`selftest_tick`), results applied on the main
thread. Reviewer fixes: a failure while the bot itself cancelled everything = retry; accepted-but-never-listed
orders = retry once (list lag), fatal the 2nd time; earlier pending hold kept; own quotes cancelled first; a crash
in the test is fatal on the 3rd in a row. Slow scenario, 3 seeds: self-test exits 0/3 (main: 1-2/3).
Known leftover: a test write that lands after shutdown's cancel-all leaves two 1-share orders at 0.005/0.995 until
they expire (30 min, cost <= 1c); interpreter exit may wait for the test thread's retries (systemd kills after 90 s).

## Code review findings (main a49587c)

Severity: H = costs money or can stop the bot, M = degrades quoting, L = minor.

- H `mm_bot.py:2645-2700` self_test: 409 REQUEST_IN_FLIGHT and a timeout on the clean-up cancel are fatal (exit 3,
  no auto-restart). Blocks the loop for up to 6 x request_timeout per call. **Fixed (item 1).**
- H `mm_bot.py:2176` Bot.place: batches sent one after another; each slow write blocks the cycle for
  timeout + 409 (~16 s); headline orders are last (dict order). Cancels in `reconcile` (`:2035`) are serial too.
  Item 2.
- H `mm_bot.py:489` Api.call retries a timed-out write with the same key at once; that retry always gets 409
  (in flight) and the batch is given up: orders land unknown to the bot (pending 90 s), fills unattributed. Item 4.
- H `mm_bot.py:2806-2810` run(): the clean-slate `cancel_everything()` at the open is outside the loop's try: if
  every write times out (6 tries x 15 s) the ApiError crashes the bot (exit 1; systemd restarts it 60 s later and
  the restart does the same). Seen in the slow scenario with all writes > 15 s. Item 4/pr3 (skip when no orders).
- M `mm_bot.py:1660` cycle: book_fvs is None once `now - ex.verified >= book_stale` (300 s): during a long cycle
  every book goes stale and quotes are pulled. Item 3.
- M `mm_bot.py:1768` books_to_fetch: at most 10 books per cycle (max_books_per_cycle). Item 3.
- M `mm_bot.py:778` RealtimeFeed._run: backoff only resets after a planned renewal; reconnects grew 2,4,8,16 s on
  day one. Item 5.
- M `ref_prices.py:122` fetch_polymarket: 5 sequential requests on new connections each refresh. Item 6.
- M `mm_bot.py:1805` account_value fallback: positions read failing with 409 "holdings cannot be valued"
  (16:30:05 on day one) fails the whole cycle (`cycle()` step 1 reads positions unguarded). Not yet fixed.
- M Rate limit: four 429s on day one with the bot's budget at 80/min (server says ~100/min). The server may count
  differently (in-flight retries, other endpoints) - the self-tuning cut handles it; noted for the owner.
- L `mm_bot.py:2502` load_order_notes keys are int(): fine today (order ids are integers on day one).
- L Pre-open the realtime socket closed every ~25 s from 00:00 to 10:00 (1,509 times): harmless (polling covers it)
  but noisy; the backoff fix keeps reconnects fast.

## Notes for Run C (strategy lane)

- Pennying to a 1c floor against bots that do the same is what the crowded scenario shows; item 8 adds churn
  control (engineering side). Strategy questions (floor width, join vs improve) are yours.
