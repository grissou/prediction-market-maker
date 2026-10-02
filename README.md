# Data snapshot to 2026-10-02 20:37 UTC: read-only, never merge

Copied from the live bot's server at 20:37 UTC on 2 Oct, for analysis only. It continues `ops-snapshot-2026-10-02` (data to 08:14 UTC): the journal starts at 08:14, and fills.csv and market_data.sql cover the whole tournament so far. This branch holds data, not code. Never merge it, and never copy its files into a code branch.

| File | What it is |
|---|---|
| `journal_2026-10-02_0814_to_now.log` | The bot's systemd journal from 2026-10-02 08:14 to 20:37 UTC, ISO timestamps: quote lines, `realtime \|` summary lines (account, worst-case loss, risk, REDUCE-ONLY, priced, resting), FILL lines, SETTING lines, handover lines |
| `fills.csv` | Every fill so far (3,704 rows): fill_id, filled_at, exchange_id, order_id, our_side, qty, fill_price, quote_price, fv_at_quote, fv_after |
| `market_data.sql` | SQL dump of `market_data.sqlite` (load with sqlite3 or Python's `executescript`). Tables: `snapshots` (per-market best bid/ask, fair value, Polymarket reference, our quotes, position), `account`, `books` (full books), `trades` (tournament prints), `positions` (our positions with the exchange's `current_price`, one row per change: take the latest row per eid), `account_marks` (the exchange's P&L reply: account value, holdings value, cost basis, unrealized) |
| `status.json` | The bot's health snapshot at copy time (Package 3 final) |
| `order_notes.json` | What each order was for (fill attribution) |
| `position_lots.json` | Position lots (entry times and prices) behind the turnover/age numbers |
| `settings_override.json` | The live override file at copy time |
| `mmbot.service` | The systemd unit |

## Key events (UTC, 2 Oct)
- **08:34:** deploy of `claude/live-2026-10-02b` (settings_override.json, handover restart, duplicate-quote fix, recorder).
- **11:21:57:** Team Package 2 by handover restart (128 orders adopted); override `arb_two_sided: false`.
- **11:22-11:31:** reduce-only episode from the 0.5 fair-value fallback in the risk (fixed in 2.1).
- **About 11:30-14:20:** 2-5 minute cycles: the capital ceiling marked orders "unsafe" and the re-quotes flooded into 429 pauses (fixed in 2.2). `worst_case_backstop_frac` 0.8 by override.
- **14:22:36:** Package 2.3 by handover (351 orders adopted). 4 x 429 in its first hour while the write budget climbed to 36-50/min.
- **15:30:23:** override adds `writes_per_minute` 28, `writes_per_minute_max` 28, `burst_cycle_seconds` 60, `capital_ceiling_adding_size_factor` 0.5. 0 x 429 since.
- **16:21:** reduce-only from the sum-of-maxima backstop (worst case 81.7k > 0.8 x 101.4k).
- **16:26:14:** Package 3 final (439ac54) by handover (122 orders adopted). Every override applied before the first trade.
- **16:40-20:37:** the bot is pinned by the backstop: reduce-only in about 78% of cycles (248 of 317 to 19:20). Worst case cycles between ~77.7k and ~81.6k: entry at 0.8 x account, exit below 0.77 x account (`reduce_only_hysteresis` 0.03). R7 risk 23-27k of a ~30.3k cap.
- **All day:** the owner traded Dem U.S. House manually on the bot's account (position +10,173 at copy time; the bot also holds Rep U.S. House -9,396).

## Numbers at copy time
- Exchange account value ~100.9k; capital in positions 93-96%; 101 "dead" markets hold ~41k.
- The exchange's holdings value = sum of quantity x its `currentPrice`, which lags its own book: at 19:22, valuing the positions at its own best bid/ask gave +997 over its figure, at its own mid +1,466, at our fair value +4.9k, at Polymarket +6.3k.
- In the 3 h to 16:30 the bot added 25.5k shares and reduced 13.5k.

No secrets: every `.env` value of 8 or more characters was searched for in these files on the server, and none appears.
