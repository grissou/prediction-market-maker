# Day-one data snapshot (2026-10-01): read-only, do not merge

Copied from the live bot's server at 20:22 UTC on the tournament's first day (trading opened at 16:00 UTC), for analysis only. Earlier copies were taken at about 17:00 and 17:32 UTC; this one covers the first 4 hours 22 minutes of trading. This branch holds data, not code. Never merge it, and never copy its files into a code branch.

| File | What it is |
|---|---|
| `journal_2026-10-01.log` | The bot's systemd journal since 00:00 UTC (pre-open waiting, the open, the jammed writes, every quote line) with ISO timestamps |
| `fills.csv` | Every fill so far: fill_id, filled_at, exchange_id, order_id, our_side, qty, fill_price, quote_price, fv_at_quote, fv_after (`?` side = an order the bot never recorded because its confirmation timed out) |
| `status.json` | The bot's health snapshot at copy time |
| `order_notes.json` | What each order was for (fill attribution) |
| `market_data.sql` | SQL dump of `market_data.sqlite`: tables `snapshots` (books, fair values, our quotes over time) and `account`. Load with `sqlite3 market.db < market_data.sql` |
| `mmbot.service` | The systemd unit running the bot |
| `server_facts.txt` | Host facts and the names of the `.env` settings (values redacted) |

No secrets: API keys, alert URLs and tokens are not included.
