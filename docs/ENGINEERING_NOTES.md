# Engineering notes: what the exchange taught us

Facts about The Super Market API learnt in the first days of trading (1–3 October 2026) that the bot is
built around. Everything here is measured, not read from documentation. The longer original notes, with the
scenarios and experiments behind each item, are at the tag `legacy-2026-10-11`.

## Rate limits

- About **100 requests a minute per API key**, all request types together. Not published; measured by
  counting until the first 429. The bot budgets 80 and keeps 10% spare.
- A 429 carries `Retry-After: 60`: every request pauses for a minute. Early on, 20 such pauses in a day
  were invisible because they were not logged; every 429 is now logged and alerted.
- Order writes are the expensive kind: a batch, a cancel-all or a single delete each count as one. The bot
  gives writes their own budget of 28 a minute (45 caused penalties) and defers what does not fit rather
  than dropping it.
- Anything else that uses the key (a status command, a shadow run) shares the same budget. A shadow bot at
  20 requests a minute starved itself of order books for four hours before this was understood.

## Latency and reliability

- Reads take about 0.4 s from a server in the exchange's region (San Francisco), 1–1.5 s from the UK.
- On day one, order writes took 14–30 s and a same-key retry got `409 REQUEST_IN_FLIGHT`. Every order
  carries an idempotency key, retries reuse it, and a lost response is reconciled from the order list.
- The realtime feed (Supabase WebSocket) flags that a book changed but carries no levels; the book is
  re-read over REST. The feed drops a few times a day with code 1006 and does not auto-reconnect: the bot
  detects a dead socket and reconnects; a full REST reconciliation every 30 s covers what the feed misses.
- The exchange went unresponsive for about 20 minutes on 7 October. The watchdog (no cycle for 10 minutes
  → cancel everything, exit, systemd restarts) handled it.

## Orders and positions

- Prices tick at 0.005. A YES bought at *q* pays 1 at settlement if the candidate wins.
- Reducing a NO holding can be sent as a covered "sell NO" at 1 − price, which locks no cash; sending it as
  "buy YES" needs cash and was refused hundreds of times a day at 100% capital. The start-up self-test
  checks the exchange still accepts the covered form.
- The exchange values positions on the leaderboard at a trade-based mark, not the book. A single trade in
  a thin longshot book moves the mark by cents and the account by hundreds, with no effect on settlement.
- No fees. Settlement is at the election result; the tournament ends on 4 November 2026 at 17:00 UTC and
  the exchange confirmed by email that trading continues until 12:00 ET that day.

## Operations

- Settings change through a JSON file re-read every 30 s; only whitelisted names, each range-checked.
- Code changes use a handover: SIGUSR1 makes the old process exit without cancelling, the new one adopts the
  resting orders, so a deploy costs no queue position. A plain `systemctl restart` cancels everything.
- Test suites take over 10 minutes on the server's single CPU and compete with the bot: run them locally.
