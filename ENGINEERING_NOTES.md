# Engineering notes (Run A)

Branch `claude/pr1-safe-fixes` (deploy-first) and `claude/pr3-ops-analytics`. Base: main a49587c (live code).
Numbers are simulated seconds from `tests/scenario.py` (see "Scenarios"). Updated after every item.

## Status

| # | Item | State |
|---|---|---|
| 0 | Scenarios: slow writes + crowded book (`tests/scenario.py`) | done |
| 1 | Self-test: busy (409/timeout/429/5xx) = retry later, on its own thread | done |
| 2 | Parallel, time-boxed, prioritised order sending | done |
| 3 | Book freshness (bulk re-verify, book_stale 900, max_books 30) | done |
| 4 | Recover unconfirmed orders + metadata (fill attribution) | done |
| 5 | Realtime reconnect backoff resets after a healthy session | done |
| 6 | Faster Polymarket | done (no WebSocket, refresh stays 5 s) |
| 7 | Burst protection (+ writes fail fast, separate 30/min write budget) | done |
| 8 | Churn control for a crowded book | done |
| + | Positions 409 'cannot be valued' no longer fails the cycle; clean-slate cancel can't crash start-up | done |

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

## Item 2 - parallel order writes (done)

`send_changes`: plan every change, then up to `parallel_writes` (4) writes at once; cancels first (pulls first), empty
sides at once, a reprice's new orders only after its cancel is confirmed; order = pulls, House/Senate (own batch),
empty sides, reprices, biggest first; cut to the request budget minus `write_read_reserve` (pulls always go); the
cycle waits `write_wait_seconds` (3 s), slower writes finish in the background (`Ex.writes` blocks the exchange).
`parallel_writes = 1` = old serial path. Scenario (seeds 1-3; the harness now runs at 10x - main re-measured):

| | main slow | item 2 slow | main crowded | item 2 crowded | items 2+3 crowded |
|---|---|---|---|---|---|
| 80% quoted (s) | 339-361 | 123-132 | 42-53 | 30-32 | 42-47 |
| quoted at 2 min | 17-21% | 68-78% | 93-97% | 97-98% | 97-100% |
| quoted at 10 min | 44-75% | 31-39% | 36-59% | 0-6% | 86-95% |
| longest cycle (s) | 314-338 | 28-39 | 256-508 | 39-74 | 38-58 |
| duplicate quotes (max) | 0-1 | 0 (self-test orders excluded) | 1-2 | 0 | 0 |

Leftovers: in `slow`, coverage still decays after ~5 min (each timed-out batch holds 20 exchanges for 90 s:
item 4), and headline quotes can stay wrong for minutes (items 7-8). A re-added exchange (load_markets drops and
re-adds) starts with `writes = 0` while an old write may still run (rare; noted).

## Item 3 - book freshness (done)

`reverify_books`: books unconfirmed for `book_reverify_seconds` (120) get a bulk best-price check on event cycles too;
`book_stale` 300 -> 900, `max_books_per_cycle` 10 -> 30. Crowded coverage at 10 min 0-6% -> 86-95% (above).

## Item 4 - lost placement responses (done)

`apply_batch` keeps every order of an ambiguous batch in `self.unconfirmed`; the next list read adopts unknown listed
orders matching exchange/side/price/expiry (`adopt_unconfirmed`), fills of never-listed ones are matched by side and
price (NO-side fills at 1 - price, as day one reported them). Reviewer fixes: a fill match never lifts the hold;
only the hold these sends set is lifted (not a take's, arbitrage's, 502's or the self-test's); recovered orders'
fills counted. Slow scenario: unattributed fills 276-341 -> 0. Stress-test settle check now accepts exchanges the
bot decided not to quote (both sides blocked by risk limits), which a new trajectory hit (party delta over cap).

## Item 6 - Polymarket (done)

One shared `requests.Session` (connections kept open), the 5 batches sent side by side (`REF.parallel_fetches`), one
failed batch no longer discards the others, `polymarket_fetch_seconds` in status.json. Expected: a refresh takes one
round trip (~0.1-0.3 s from SF) instead of five new TLS connections (day one ~1-2 s), so prices are ~1-1.5 s fresher
on average. `ref_refresh_seconds` stays 5 s: docs.polymarket.com is blocked from this sandbox, so I could not confirm
the Gamma rate limit (I recall ~300 requests / 10 s for /markets; 2 s refresh = 25 / 10 s would fit). **Owner: confirm,
then try 2 s.** WebSocket (CLOB market channel, sub-second): not built - needs clobTokenIds per mapped market (a
ref_map change) and a second socket to keep alive; worth it only if markouts show we're picked off within 5 s of
Polymarket moves (Run B/C data). Estimate: ~150 lines + tests.

## Item 7 - burst protection (done)

Burst mode when the median write on the wire >= 5 s, >= 2 write timeouts in a minute, or a cycle >= 20 s; off after
120 s calm. Top 40 markets (House/Senate first) quote normally at half size and +0.5c; elsewhere no reprices
(safe orders stay, unsafe ones are pulled, empty sides get a half-size quote). Never in reduce-only / flatten /
exit windows. Writes timing out after being sent are not retried with the same key (that only ever got 409);
item 4 recovers them. Separate write budget `writes_per_minute` = 30 (Run B found a copy of the platform docs
saying "100 reads and 30 writes per minute"; day one peaked near 45 cancels/min with no 429, so a batch
probably counts once - **owner: confirm with SIG**). Reviewer fixes: exit window never blocked, oversized
orders never kept, write waits don't hold reads, timing on the wire, Polymarket-move changes respect the budget.
Known: entering burst widens the limit, so some resting orders become "unsafe" and are repriced once.

## Item 8 - churn control (done)

`hold_side`: a single safe resting order (price inside the limit, size not above wanted, not expiring) is kept
instead of repriced when younger than `min_quote_life_seconds` (5) or its side was repriced
`churn_max_reprices` (4) times in `churn_window_seconds` (60). Never within 15 s of a Polymarket move of
>= `urgent_ref_move` (0.5c); those markets' changes go right after pulls. `churn_control=False` = off. Unit
test fixtures pin it off (they reprice instantly); the stress test runs it on.

## Scenario summary (10 min; seeds 1-3 unless noted; final numbers in the PR body)

| | main slow | PR1 slow | main crowded | PR1 crowded |
|---|---|---|---|---|
| 80% of markets quoted (s) | 339-361 | ~104 | 42-53 | 42-50 |
| quoted at 2 min / 10 min | 17-21% / 44-75% | 88% / 54% | 93-97% / 36-59% | 97% / 58-87% |
| longest cycle (s) | 314-338 | ~51 | 256-508 | 57-105 |
| fills unattributed | 54-69% | <1% | 0-5% | <1% |
| duplicate quotes (max) | 0-1 | 0 | 1-2 | 0 |
| self-test exit 3 | 1-2 of 3 | 0 | 0 | 0 |
| write requests/min | 4.5-6.3 | ~19 | 28-34 | ~29 (cap 30) |

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
  the restart does the same). Seen in the slow scenario with all writes > 15 s. **Fixed** (item 7 commit; pr3 also
  skips it when no orders rest).
- M `mm_bot.py:1660` cycle: book_fvs is None once `now - ex.verified >= book_stale` (300 s): during a long cycle
  every book goes stale and quotes are pulled. Item 3.
- M `mm_bot.py:1768` books_to_fetch: at most 10 books per cycle (max_books_per_cycle). Item 3.
- M `mm_bot.py:778` RealtimeFeed._run: backoff only resets after a planned renewal; reconnects grew 2,4,8,16 s on
  day one. Item 5.
- M `ref_prices.py:122` fetch_polymarket: 5 sequential requests on new connections each refresh. Item 6.
- M `mm_bot.py:1805` account_value fallback: positions read failing with 409 "holdings cannot be valued"
  (16:30:05 on day one) fails the whole cycle (`cycle()` step 1 reads positions unguarded). **Fixed** (last read reused).
- M Rate limit: four 429s on day one with the bot's budget at 80/min (server says ~100/min). The server may count
  differently (in-flight retries, other endpoints) - the self-tuning cut handles it; noted for the owner.
- L `mm_bot.py:2502` load_order_notes keys are int(): fine today (order ids are integers on day one).
- L Pre-open the realtime socket closed every ~25 s from 00:00 to 10:00 (1,509 times): harmless (polling covers it)
  but noisy; the backoff fix keeps reconnects fast.

## Notes for Run C (strategy lane)

- Pennying to a 1c floor against bots that do the same is what the crowded scenario shows; item 8 adds churn
  control (engineering side). Strategy questions (floor width, join vs improve) are yours.
- PR1 makes the bot spend its writes better, but the crowded scenario still shows pick-offs of 1c-floor quotes
  after Polymarket moves (81-94 per 10 min vs 127-129 before churn/burst). The remaining lever is strategic:
  wider floors / join-don't-improve (Run B R4) and a resting ladder behind the touch (R3).
- Settings you'll likely touch: `min_edge`, `burst_*`, `churn_*`, `writes_per_minute`. On pr3 they are all
  live-overridable (settings_override.json), so the owner can tune without a restart.
- `tests/scenario.py` can serve as your simulator's day-one harness: `SCENARIO_SET="key=val,..."` overrides any
  setting; `crowded` has rival bots with configurable delay/floor (class Rival).
- The fake exchange's NO-side fill price is the YES price, but day one reported NO-side fills at 1 - price (fills.csv:
  an ask at 0.62 filled "at 0.38"); fill analysis must use quote_price or flip it (pr3 `analyze` uses quote_price).

## Ideas from Run B (IDEAS.md) - engineering view

| Idea | Buildable? | Status |
|---|---|---|
| R6 adopt orphaned orders after 409s; split write budget; cancels first | yes | **built** (items 2, 4, 7) |
| R1 equity base `reserved_cash_mode="ignore"` | one setting; verify with status.json first | Run C (risk); trivial |
| R2 liquidity-scaled skew, never cross own fair value | yes, ~30 lines in compute_quote | Run C |
| R3 resting ladder behind the touch | yes, but needs multi-order-per-side support in reconcile/side_needs_change and capital accounting (~150 lines + tests); writes: few if ladders stay put | Run C; I'd build it on top of plan_change |
| R4 join or step back, never penny at the floor | yes, small in compute_quote; complements item 8 | Run C |
| R5 price thin books from Polymarket | yes, in fair-value step (~20 lines) | Run C |
| R7 correlated risk measure | yes (math only) | Run C |
| R8 smaller headline quotes | one setting (`headline_size_frac`), live-overridable on pr3 | owner / Run C |
| T4 pull-first on Polymarket moves | built (item 8 urgent ordering + burst pulls) | done |
| T5 realtime book logger / competitor fingerprints | yes: log feed events (no extra reads) to sqlite, ~80 lines | not built (time) |
| T11 markout-driven auto-widen | yes once `analyze` markouts are trusted (pr3) | Run C |
| W1 election-night taking | risky, policy decision | owner |
