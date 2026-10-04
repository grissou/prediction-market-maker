# Data snapshot to 2026-10-04 15:56 UTC: read-only, never merge

Copied from the live bot's server at 15:56 UTC on 4 Oct, for analysis only. It continues `ops-snapshot-2026-10-03` (to 3 Oct 22:47): the journal starts at 3 Oct 22:47, and fills.csv and the market database cover the whole tournament. This branch holds data, not code. Never merge it, and never copy its files into a code branch.

| File | What it is |
|---|---|
| `journal_2026-10-03_2247_to_now.log.gz` | systemd journal 3 Oct 22:47 to 4 Oct 15:56 UTC (gzip): realtime summaries, FILL, TAKE, PAIR UNWIND, ALLOC, SETTING, self-test lines |
| `fills.csv` | every fill so far (7,965 rows) |
| `market_data.sql.gz` | gzip SQL dump of `market_data.sqlite` (copied with the sqlite backup API, then dumped): `snapshots`, `account`, `books`, `trades`, `positions` (exchange `current_price`; latest row per eid), `account_marks` |
| `status.json` | health snapshot at copy time: Package 12 with stage 1 on (alloc, bloc_delta, tilt diag, liquidation value, cash gate) |
| `order_notes.json`, `position_lots.json`, `settings_override.json`, `mmbot.service` | as before |

## Key events (UTC)
- **3 Oct 22:42 / 22:44:** Package 8 + all three stages (incl. tilt exits). **22:44 to 4 Oct 07:05:** 352 stale-quote TAKEs measured from RAW Polymarket built a new toward-Polymarket book (e.g. Rep Wyoming Senate at 0.91 vs 0.975) while the tilt exits sold the old one; account 101,013 → 99,896 (re-marking −483, trading −635). The tilt rose 0.11 → ~0.14-0.15; `ref_tilt_max` 0.15 at 04:01:51.
- **07:05:21:** `take_tilted_ref` true. **~07:15: owner learns SIG pays positions out AT THE OUTCOME** (Polymarket ≈ the probability; the book was ≈ +8.6k of expected value above the exchange marks).
- **07:19:10:** `pair_no_unwind_max_cost` 0.05, per cycle 4 (owner: "sell 10k"): 132 set unwinds, 21,606 sets, cost 918. **07:19-11:08:** with value mode not yet on, the P8 tilt exits sold most of the value book (tilt exposure +37k → −0.3k; expected value at the outcome 108.5k → 104.8k).
- **11:10:58:** value config (ref_tilt off, tilt exits and guards off, take_tilted_ref off, ref_weight 1.0, skew_max 0, skew_age off). **11:15:51:** Package 10 (2abf782); **11:17:05** stages 1-2 (value_mode, bloc_delta_enabled, alloc_*). **11:24:46:** `max_worst_case_frac` 0.40 + stage 3 (adding factor 0.5, value_quote_hurdle 0.08, take_respect_reserve).
- **11:2x-15:37:** reduce-only in 467 of 474 cycles: the sum-of-maxima backstop (0.8) pinned the bot after the 11:26-11:29 value takes. **15:37:50:** `worst_case_backstop_frac` 0.85; out of reduce-only 15:39.
- **15:44:43:** Package 12 (fc3d38d). **15:46:31:** stage 1 (hurdle 0.05, bloc_rho 0.55, alloc_set_rich_leg, alloc_prefer_short, pair_no_unwind_asks_le1, skew_target_inventory). Stage 2 (close override) NOT on: waiting for SIG.

## Numbers at copy time (15:56)
Account (exchange marks) 99,630; expected value at the outcome (Polymarket as probability) ≈ 107.4k (+7.8k); liquidation 99.4k; capital in positions 76%; cash gate left ~4k; settlement risk 34.7k (cap 40%); worst case 81.0k (backstop 0.85); party delta −10.6k; tilt_s 0.137; not reduce-only. Owner target: +50-100%; considering the Texas Rep digital (Team A2).

No secrets: every `.env` value of 8 or more characters was searched for in these files (inside the gzip files too) on the server, and none appears.
