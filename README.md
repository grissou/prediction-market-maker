# Data snapshot to 2026-10-02 08:14 UTC: read-only, never merge

Copied from the live bot's server at about 08:15 UTC on 2 Oct, for analysis only. It covers the tournament so far: day one from the 16:00 UTC open, the night on the original code, the exchange outage of 2 Oct, and the first minutes after the 08:08 UTC deploy. This branch holds data, not code. Never merge it, and never copy its files into a code branch.

| File | What it is |
|---|---|
| `journal_2026-10-01_to_02.log` | The bot's systemd journal from 2026-10-01 00:00 to 2026-10-02 08:14 UTC, with ISO timestamps. It includes the per-market quote lines, the summary lines (account, worst-case loss, risk, priced, resting) and the 2-hourly summaries |
| `fills.csv` | Every fill so far (1,813 rows): fill_id, filled_at, exchange_id, order_id, our_side, qty, fill_price, quote_price, fv_at_quote, fv_after. A side of `?` means an order the bot never recorded |
| `status.json` | The bot's health snapshot at copy time, running the 08:08 deploy |
| `order_notes.json` | What each order was for (fill attribution) |
| `market_data.sql` | SQL dump of `market_data.sqlite`, with tables `snapshots` and `account`. Load it with sqlite3 or Python's `executescript` |
| `mmbot.service` | The systemd unit |
| `server_facts.txt` | Host facts and the `.env` key names, with values redacted |

## Key events (UTC)
- **10-01 16:00:** open. Order writes are slow and return 409s; the first cycle took 4.5 min.
- **10-01, all evening:** `reserved_cash_mode` stayed on "detecting", so the account value double-counted cash locked in orders. Real equity was about 100.5k at 21:00.
- **10-01 22:48:** first REDUCE-ONLY: the sum-of-maxima worst case passed 30% of the account. From then on it was reduce-only on and off, and continuously from 05:12.
- **10-02 05:12:** the detector settled on "already included", so the account value is real from here (101.6k). With the cap now honest, the bot stays reduce-only with 0-26 orders resting.
- **10-02 07:32-07:45:** exchange outage. First `{"error": "Not found"}` replies, then timeouts. The old code crashed parsing the plain-text error and alerted about once a second.
- **10-02 08:08:** deploy of `claude/live-2026-10-02`: Run A's safe fixes plus the Builder's R1, R2, R5 and R7, outage fixes, and a sum-of-maxima backstop at 69%. Self-test passed. Burst mode came on (21 s startup cycle) and went off at 08:10:59. At 08:13: risk 10.5k against a 30.5k cap, priced 87/237, about 130 orders resting, 0 409s, some order changes deferred by the 30 writes/min budget.

No secrets: every `.env` value of 8 or more characters was searched for in these files on the server, and none appears.
