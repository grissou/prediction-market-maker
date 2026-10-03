# Data snapshot to 2026-10-03 22:47 UTC: read-only, never merge

Copied from the live bot's server at 22:47 UTC on 3 Oct, for analysis only. It continues `ops-snapshot-2026-10-02b` (data to 2 Oct 20:37): the journal starts at 2 Oct 20:37, and fills.csv and the market database cover the whole tournament. This branch holds data, not code. Never merge it, and never copy its files into a code branch.

| File | What it is |
|---|---|
| `journal_2026-10-02_2037_to_now.log.gz` | The bot's systemd journal, 2 Oct 20:37 to 3 Oct 22:47 UTC, ISO timestamps (gzip): quote lines, `realtime \|` summaries, FILL, SETTING, PAIR UNWIND, ARBITRAGE, self-test and refusal lines |
| `fills.csv` | Every fill so far (5,832 rows): fill_id, filled_at, exchange_id, order_id, our_side, qty, fill_price, quote_price, fv_at_quote, fv_after |
| `market_data.sql.gz` | gzip SQL dump of `market_data.sqlite`: `gunzip -c market_data.sql.gz \| sqlite3 md.sqlite`. Tables: `snapshots`, `account` (8 columns since Package 5: + liquidation_value), `books` (3 levels, [price, qty] pairs), `trades`, `positions` (exchange `current_price`; one row per change, take the latest per eid), `account_marks` |
| `status.json` | Health snapshot at copy time (Package 8 with stages 1-3 on): includes tilt_s / tilt_diag, tilt_exposure, nono_sets, liquidation_value, cash-gate fields |
| `order_notes.json`, `position_lots.json` | Order purposes; position lots |
| `settings_override.json` | The live override file at copy time |
| `mmbot.service` | The systemd unit |

## Key events (UTC, 3 Oct)
- **08:03:** Package 5 (all flags off) by handover. 08:06:39 the self-test's second order was refused "Insufficient available funds" at 100% capital: exit 3, all orders cancelled. 08:17 the same on Package 3 after a rollback. **08:22:37** Package 5 + owner's fix (funds refusal = busy).
- **08:48:43:** stage 1 `ref_tilt_enabled`, `ref_tilt_max` 0.09 (the live estimator read 0.14; a rebuild from the recorder gave 0.09-0.095). ~09:2x owner sold part of Dem House by hand; the cash went at once to new longshot shorts. **09:24:47** `capital_ceiling_adding_size_factor` 0 and `ref_tilt_headline` true.
- **09:19:** 76% of 400 "Insufficient funds" refusals were bids reducing NO holdings ("buy YES" needs cash).
- **13:21:37:** Package 6. **14:01:54** `reduce_no_as_sell` (covered "sell NO" check accepted 14:02:24). Remaining refusals: races with NO on every leg (set collateral). **14:17:24** `ref_tilt_max` 0.11.
- **14:00-19:18:** account -661, of which -655 re-marking as the tilt rose 0.092 -> 0.110 (tilt exposure ~35k).
- **19:15:41:** Package 7 (owner ran the copy + handover). 19:20:36 `no_set_aware_bids` + `pair_unwind_followup`; 19:24:06 `pair_no_unwind_max_cost` 0.003 (paired NO sale check accepted 19:24:38); **19:55:59** cap 0.02: NO+NO set capital 22.5k -> 14.6k by 20:18, then sell-side arbitrage (bids 1.04, e.g. Illinois Governor) re-created sets.
- **~19:59 onward:** reduce-only most cycles (sum-of-maxima worst case ~79.5-81k vs 0.8 x account).
- **22:36:22:** `arb_enabled` false (at 0 cash arbitrage left one-legged leftovers: Maine Senate 158/0, South Dakota Senate 13/0, 3/0).
- **22:42:51:** Package 8 by handover. **22:44:54** all three stages on: `cash_gate_enabled`, `adding_factor_per_market`, pair sizing (`pair_no_unwind_max_per_cycle` 1, `pair_unwind_race_order`, `pair_no_unwind_max_sets` 1000, `pair_unwind_followup_max_age` 600), tilt exits (`tilt_exit_priority`, `tilt_exit_full_size`, `ref_guard_tilted`, `ref_guard_exits`).

## Numbers at copy time
Account ~101.0k on a 100k start (tournament leader ~+600%); liquidation ~100.3k; capital in positions ~99.7%; tilt ~0.110; tilt exposure ~35.8k; realised P&L ~+1.9k.

No secrets: every `.env` value of 8 or more characters was searched for in these files (inside the gzip files too) on the server, and none appears.
