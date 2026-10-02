# START HERE (Team run, branch `claude/run-c-tournament-improvements-pycdet`)

Status: **in progress** (started 2026-10-02 08:50 UTC). **Package 1 READY at 10:10 UTC, Package 2 READY at 11:05 UTC, hot-fix Package 2.1 READY at 12:05 UTC, hot-fix Package 2.2 READY at 12:40 UTC** (deploy-ready, cumulative). Package 2 is LIVE since 11:21:57. Base: `claude/live-2026-10-02b` (5c0463a), the code live since 08:34.
The Builder's previous START_HERE is kept as `START_HERE_BUILDER.md`; Run A's notes are `ENGINEERING_NOTES.md`.
Plan: `PLAN.md`. Packages appear below as they become READY (commit messages start "READY: Package N").
Deploy only commits whose message starts "READY"; the branch is cumulative.

## Carried over from the Builder and Run A (open items)
- Owner decisions still open: R7 loosened reduce-only (live with the 69% backstop); rule questions for SIG (end valuation of
  unresolved positions, batch = 1 or N writes, Smart Score, position limits, wording on many resting levels).
- Not built yet: R3 depth ladder; rival analysis from recorder data; T2-T12, W1-W7; WebSocket prices; Polymarket refresh < 5 s.
- Run A: owner to confirm the Gamma rate limit before a 2 s refresh; confirm batch counting with SIG.

## Opening audit of the live code (Reviewer, 09:05 UTC): main...claude/live-2026-10-02b
No order-duplication or crash path found in the parallel-write machinery. Findings, being fixed for Package 1:
| # | Sev | Where | Finding |
|---|---|---|---|
| 1 | HIGH | Api pool (mm_bot.py ~625) | pool_maxsize 4 while up to 8 threads hit the host: fresh TLS connection per overflow, inflating write times that feed burst detection (the live "pool size 4" warning) |
| 2 | HIGH | positions 409 fallback (~1997) | unbounded: inventory frozen while fills land, so limits, skew, party delta and risk run on stale inventory |
| 3 | MED-HIGH | reduce-only (~2086, ~2781) | no hysteresis; each flip pulls ~72 orders bypassing the write budget, starving every other write; flip back re-places them |
| 4 | MED | burst mode (~2728) | the cycle-time trigger counts our own throttle sleeps: the first cycle after a restart (~21 s) trips burst for 2 min |
| 5 | MED | burst half-size (~2667/~1459) | placed size int(size*0.5) vs check size*0.5 <= qty: a 375 quote is cancelled and replaced every cycle in burst |
| 6 | MED | deploy/handover-restart.sh | if the old bot needs > 90 s to exit, the script exits 1 and never starts the service: no bot, orders rest unmanaged |
| 7-11 | LOW | various | duplicate thin_book_prices; selftest_eid never cleared; throttle window ordering; reprices counted when planned; on_cycle_error ignores a partial cancel-all |
Checked and found correct: thread-safety of shared state, order recovery vs duplicates, cancel-all failure path, handover adoption, overrides timing, R2 skew sign, R7 math, R5/R5b guards. Diff is Python 3.10-clean.

## Owner update, 09:45 UTC (live data; drives Packages 2-3)
- **No fees.** Realised gains (cash + cost basis - 100k) about +1,916; the exchange's P&L (+1,287) = realised + unrealised (-640) at its own
  valuation price. Positions are marked at an off-grid `currentPrice` (e.g. 0.7734, 0.4794): an average of recent trades, not the book.
  Our positions are worth +577 more at book mid and +2,657 more at our fair value than at the exchange's marks (largest: Rep Florida
  Senate 0.7734 vs mid 0.8575). Rank and Smart Score use the exchange's figure. Day one's 1,180 gap was this valuation.
  -> Analyst: pin down the rule (trade VWAP? last-N? window?) as far as the data allows; use it in risk and rank accounting only.
  Never trade to move a valuation price.
- **Inventory is held too long and too much.** 164 positions, 126,706 shares; median held share 7.2 h old; 54% bought > 3 h ago, 19% > 12 h
  ago. About 90.5k of 101k is tied up in positions (11k cash), which limits new quoting. Biggest: long 10,834 Dem U.S. House AND short
  9,396 Rep U.S. House (the same bet twice, ~20k shares on Dems winning the House, oldest lot 16.6 h); 7,335 each of Dem and Rep U.S.
  Senate (10-15 h); RI, NH, TN-05 at 2.8-3.8k shares (11-13 h).
  -> Top priority: inventory turnover and capital use. In flight: Engineer 6 (race-netted limits and sizing, age-based skew, a capital
  ceiling on positions, all behind settings), Engineer 5 (pair unwinder: sells a long pair when bids sum >= 1, frees ~17k), the
  Strategist's sweeps of R8 headline sizes, R2 strength, age skew, capital ceiling and fast unload with rival bots.

### Package 2 (READY 11:05 UTC): inventory turnover, capital use, coverage. Code deploy (handover restart). Commit "READY: Package 2".
Strategy package, every change behind a setting (all live-overridable). Red-teamed by the Reviewer; its fixes are in.
Suites: test_mm_bot 463, test_ref_prices 37, test_strategy 101, test_recorder_refill 37, test_stress 20; green on Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`; `.gitignore` (position_lots.json). The bot creates
`position_lots.json` and the new recorder tables itself. Includes Package 1.

| Change (default) | Mechanism | Evidence | Settings |
|---|---|---|---|
| **Pair unwinder (ON)** | we hold YES on every leg of a race (n complete sets): when other traders' bids sum to >= 1.005, sell up to n sets at the bids (10 s orders); mirror for short sets at asks <= 0.995. Runs in reduce-only too (it only shrinks). | 17,675 pair-shares held (7,335 x 2 U.S. Senate control): ~17% of the account earning nothing; U.S. Senate bid-sum >= 1.000 in 16% of snapshots. Riskless by construction; worth ~+50 in profit and ~17k of freed capital over days | `pair_unwind_enabled`, `_min_profit` 0.005, `_max_frac` 0.01, `_cooldown_seconds` 30 |
| **Buy-side arbitrage (ON, flagged)** | asks of all legs sum to <= 0.985 AND our fair values sum to >= 0.9925 -> buy every leg (a set pays 1); the unwinder sells it back when bids reach 1.005 | ask-sum < 0.98 in 853 race-minutes/day (the live sell-side arb at 3c fired 0 times). Upper-end estimate +600-1,200/day; snapshot sums may include stale prices, so watch the fill logs | `arb_two_sided`, `arb_min_profit_buy` 0.015, `arb_buy_min_sum` 0.90 |
| **Race-netted limits (ON)** | position limits and the Kelly cap also apply to the race-netted exposure on the side that grows it (a short Rep leg counts toward the Dem leg) | long 10,834 Dem House AND short 9,396 Rep House = the same ~20k bet twice under per-market limits of ~10k | `limits_use_race_net` |
| **Age skew (ON)** | a position's share-weighted age (FIFO lots, persisted; rebuilt from fills.csv on first start) adds 0.25c of skew per hour held beyond 1 h, max 2c, toward unloading; never through fair | median held share 7.2 h old; 54% > 3 h; round trips were the whole realised profit, open lots gave their edge back | `skew_age_enabled`, `skew_age_after_hours` 1, `skew_age_per_hour` 0.0025, `skew_age_max` 0.02 |
| **Capital ceiling (ON, flagged)** | when capital in positions > 75% of the account (the API's totalMarketValue, else our own valuation), every ADDING side quotes at a quarter size; reducing sides unchanged; off again below 70% | 90.5k of 101k tied up, 11k cash. **The ceiling will be ON at deploy**: adding sides shrink to 25% until positions turn over. Deliberate (owner's priority); lower/raise `capital_ceiling_adding_size_factor` live if coverage or fills drop too far | `capital_in_positions_max_frac` 0.75, `capital_ceiling_adding_size_factor` 0.25 |
| **Thin-book pricing from bulk tops + start-up priming (ON)** | R5 may price from the bulk best bid/ask (3 requests for all markets) when no fresh book exists; our own order at the top blanks that side; on a (re)start books download first (60/cycle, 45/min) | after the 08:08 restart coverage rose 30 -> 101 in 6 min with 138 eligible markets waiting; scenario cold start: quoted at 2 min 17% -> 80-85% | `ref_only_use_tops`, `tops_max_age` 120, `startup_books_first`, `startup_prime_*` |
| **Same-side refill cooldown (ON)** | 2 adding fills (>= 200 sh) on one side within 60 s -> that side withheld 30 s; reducing side exempt | 3rd+ fills in same-side runs: -1.15c on 81k shares (-936); mostly day one's skew bug, so this is cheap insurance | `refill_cooldown_*` |
| **Mark recorder (ON)** | `positions` (quantity, currentPrice, all numeric fields) and `account_marks` tables, no extra requests; `analysis/mark_rule.py` fits the exchange's valuation rule | the exchange marks at a trade average (30-min VWAP or last ~5 trades fit); pins the rule for risk and rank accounting | `record_positions` |
| **Favourite-longshot bias (OFF)** | wider, smaller "bad side" below 20c / above 80c | the apparent -0.5c was day one's skew quoting through fair value, not longshot flow (DATA_REPORT_2 §10c) | `fl_bias_enabled` False |
| **Reviewer fixes** | capital ceiling factor 0 -> 0.25; buy-side arb fair-value-sum guard; unwinder margin/cap; zero-fill 4x cooldown; priming reserve; carried tops expire; positions rows only on quantity/mark changes | red-team of the merged diff | - |

Parameter-only change shipped with this package (optional, revertible in 30 s via settings_override.json): `{"ref_weight": 0.8}`.
Simulator (128 seeds, recalibrated to day two): ref_weight 0.85 +17 ± 6/h quiet, +20 ± 8/h news on a +499/h base, peak worst case +0.35k; the simulator
treats Polymarket as the truth, so this is an upper bound; 0.8 is the conservative step (medium confidence). Everything else the Strategist swept stays:
improve_ticks 0 loses -17 ± 6 (keep pennying), kelly 0.15 loses -22 ± 6, min_edge/max_half_spread/skew/size/write/churn changes are noise.

Deploy: `deploy/RUNBOOK.md` section B (handover restart). Rollback: section C, or switch any single feature off via settings_override.json.
Watch in the first 10 minutes: (1) status.json `capital_in_positions_frac` and `capital_ceiling_active`: active is expected; if resting orders fall by
more than 40%, set `capital_ceiling_adding_size_factor` 0.5 live; (2) "PAIR UNWIND" / "ARBITRAGE ... buy" lines and the following "every leg filled N":
N = 0 repeating on one race, or a buy in a two-leg race whose fair values do not sum to ~1 -> set `arb_two_sided` false; (3) `markets_priced_from_tops`
and `books_loaded` climbing during priming, `rate_limited_total` 0; (4) "refill cooldown" lines rare and never on a side whose position has the
opposite sign; (5) `position_lots.json` written, `portfolio_age_hours` plausible (not 0 with positions, not > 48 h), quote lines showing "age Nh".
Owner flags: pair unwinder, buy-side arbitrage and the capital ceiling are new behaviour ON by default (data evidence, riskless or strictly
risk-reducing by construction); ref_weight 0.8 is a parameter step on simulator evidence only.

### Package 2.1 (READY 12:05 UTC): HOT-FIX for the 11:22 reduce-only episode. Code deploy (handover restart). Commit "READY: Package 2.1".
Behaviour-identical to the live Package 2 except the two fixes below (the not-yet-reviewed fast unload and reduce-join features are
in the code but OFF: `fast_unload_enabled` False, `reduce_join_best` False; the per-market edge `market_edge_enabled` is OFF too).
Suites: test_mm_bot 503, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_stress 20; Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (rival_floor.py, new), `deploy/RUNBOOK.md`.

| Fix | What happened live | Change |
|---|---|---|
| Risk fair-value fallback (`Bot.risk_fv`) | Rep U.S. House (short 9,396) unpriced after the restart: `or 0.5` made it a coin flip, risk 20.6k -> 31k, reduce-only 11:22-11:31, Dem House sold at 0.915 vs 0.92 fair | a HELD market with no fair value uses, in order: the liquid Polymarket reference; 1 - the other leg's fair value (two-leg race); the exchange's own mark (currentPrice from the positions read); the last fair value; only then 0.5. Logged once per market and source. Used by settlement_risk, total_worst_case, the capital-in-positions valuation and the unwind safety check |
| Priming held markets first (Engineer 13) | priming ended at 192 s with 128/237 books; Rep House's adopted bid sat at the best, so the bulk top was blanked and R5 could not price it until its book arrived, behind ~100 others | missing books of HELD markets download first (biggest |position| x price), then markets with resting orders, then tops-priced, then by volume; priming never ends while a held market lacks a book (hard cap `startup_prime_held_max_seconds` 900); a held market unpriced for `unpriced_held_warn_cycles` (5) logs one "UNPRICED held market" WARNING with the reason |

**Stop-gap settings for the 11:31-11:50 slow cycles (2-5 min; 20 silent 429s)**, via settings_override.json, until Package 2.2 ships
(Engineer 14 is reproducing it; the lead's reading: the capital ceiling x0.25 and burst x0.5 shrank every adding quote, so every resting
adding order counted as "oversized" = unsafe, `never_defer_unsafe` gave all those reprices pull priority past the request budget, the
flood hit the exchange's real write limit, each 429 (Retry-After 60 s) paused EVERY request, and the 429 is only logged when no pause
was already in force):
`{"arb_two_sided": false, "never_defer_unsafe": false, "capital_ceiling_adding_size_factor": 0.5, "burst_protection": false, "writes_per_minute": 30, "writes_per_minute_max": 30}`
Watch: summary-line interval back to ~30 s; "request budget: N deferred" shrinking; orders_resting steady (not 70 -> 281 -> 84); status rate_limited_total not rising.
Re-enable `burst_protection` once cycles are back to ~30 s. `capital_ceiling_adding_size_factor` 0.5 is also the Strategist's recommendation
(factor 0 was a cliff: -131/h once the ceiling binds; 0.5 recovers ~70%); Package 3 makes 0.5 the default.

**Backstop recommendation** (`worst_case_backstop_frac`, owner set 0.8 at 11:46): keep 0.8 for now. The sum-of-maxima (69k) is dominated by the
doubled House legs and many small races and ignores the hedged sets; R7's correlated risk (20.6k vs the 30.5k cap) is the operative limit. The
pair unwinder and the capital ceiling should bring the sum down over the next days; revisit to 0.69 when it is below 60k. This loosens a safety
limit: the owner's call, flagged.

**ref_weight 0.8: withdrawn.** The Strategist's 3-hour runs reverse the 1-hour gain (ref_weight 0.85: -23 ± 24 / -46 ± 28 per 3 h at the lagged mark).
Keep 0.7.

### Package 2.2 (READY 12:40 UTC): HOT-FIX for the 2-5 minute cycles. Code deploy (handover restart). Commit "READY: Package 2.2".
Includes 2.1. Behaviour against the live Package 2 changes only by the fixes below; the Package 3 features present in the code stay OFF
(`fast_unload_enabled`, `reduce_join_best`, `turnover_control_enabled`, `ladder_enabled`, `mark_frag_enabled`, `market_edge_enabled` all False).
Suites: test_mm_bot 536, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_turnover 72, test_mark_frag 52,
test_stress 20; green on Python 3.11 and 3.10. Reviewer red-team in progress; anything it finds ships as 2.3.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`.

Root cause (Engineer 14, reproduced in `tests/scenario.py ceiling`: 236 markets, full-size orders resting, the capital ceiling switching on, 45 writes/min,
an exchange that answers 429 past 30 writes/min): (1) the ceiling's x0.25 (and burst's x0.5) shrank the WANTED size, so every resting full-size order
counted as "bigger than allowed" = unsafe; `never_defer_unsafe` gave those ~100-280 reprices per cycle pull priority past the request budget; (2) the
flood exceeded the exchange's real write limit; each 429 (Retry-After 60) paused EVERY request for 60 s, and the 429 was logged only when no pause was
already in force (20 silent 429s live); the main thread waited on the positions/book reads (0% CPU, futex_wait). Before the fix: 4 cycles in 6.5 min,
median 139 s, max 278 s. After: median 5.2 s, every 429 logged, the write budget settles at 26/min.

| Fix | Setting (default) |
|---|---|
| A resting order is never unsafe only because a size FACTOR (ceiling, fl bias, turnover) shrank the wanted size: `Quote.bid_max/ask_max` (the size before factors, within position/cash/risk limits) is the ceiling in plan_change; orders beyond the limit price, on unwanted sides or above a LIMIT stay urgent | - |
| Urgent writes capped per cycle, price-unsafe orders and pulls first | `urgent_writes_per_cycle` 20 |
| A main-thread write (take, arbitrage) that would wait longer than write_wait_seconds + margin is not sent ("429 WRITE_BUDGET_WAIT"); takes/arbitrage skipped while the budget is busy; cycles skipped during a 429 pause | `main_write_wait_margin` 2, `pause_skip_cycles` True |
| Every 429 logged (method, path, Retry-After, pause already on); a 429 during a pause moves its end to Retry-After from now (not additive); budgets cut once per pause; status.json `paused_until`, `pause_seconds_left`, `pauses_total`, `seconds_since_cycle`, `last_cycle_phases`; the summary line ends with the last cycle's phase times (reads, fair values, fills/risk/takes, decide, send, wait) | - |
| Watchdog thread: alert after 180 s without a completed cycle; at 600 s dump every thread's stack, cancel-all (at most 20 s), exit 5 (systemd restarts) | `watchdog_alert_seconds` 180, `watchdog_exit_seconds` 600 (0 = off), `watchdog_cancel_seconds` 20 |
| Buy-side arbitrage guard: every leg needs a LIQUID Polymarket price and the raw prices must add up to >= 0.99 (book fair values are normalised to 1 and cannot see an outsider). `analysis/outsider_races.py` lists the races: South Dakota Senate 0.917, Idaho Senate 0.945, Maryland Governor 0.979, a dozen House races 0.980-0.988, three 3-leg races (RI Gov, NE Sen, MT Sen), four with no complete reading (Alaska Sen/Gov, CA-22, CA Gov) | `arb_buy_min_ref_sum` 0.99. The owner may now set `arb_two_sided` true via settings_override |

After deploying 2.2 the stop-gap overrides can go back to defaults one at a time: `never_defer_unsafe` true (now safe), `burst_protection` true, `writes_per_minute`
45 / `writes_per_minute_max` 50 only if `rate_limited_total` stays 0 for an hour at 30 (the 429s were real: the exchange's write limit looks like ~30/min).
Keep `capital_ceiling_adding_size_factor` 0.5. Watch: summary-line gaps ~30 s; "request budget: N deferred" < 30; `rate_limited_total` flat; `orders_resting` steady;
"RATE LIMITED (429)" lines (now one per 429) absent; the watchdog never alerts.

## Parameter changes (cumulative against live)
| Setting | Live | New | Evidence | Expected effect |
|---|---|---|---|---|
| writes_per_minute | 30 | 45 (max 50, AIMD) | DATA_REPORT_2 §8; Engineer 1's table: no 429 at >= 40 writes/min for 63 minutes | no deferrals in normal cycles; faster repricing |
| batch_size | 20 | 10 | 273 of 417 409s were 20-order batches | fewer 409s and lost replies |
| pool_maxsize (code) | 4 | 10 | pool warnings; 8 threads | no connection churn, truer write timings |
| positions_stale (new) | unbounded | 120 s | audit #2 | quotes pulled instead of trading on frozen inventory |
| reduce_only_hysteresis (new) | 0 | 0.03 | audit #3 | no reduce-only flapping |
| pulls_cancel_all_over (new) | - | 25 | audit #3 | one write instead of ~72 on a reduce-only entry |
| burst_startup_grace_seconds (new) | - | 90 | 08:08 restart tripped burst | normal sizes after a restart |
| take_ref_max_age_seconds (new) | - | 30 | Engineer 1 task 4 | no takes on a stale Polymarket price |
| handover_exit_max_seconds (new) | - | 150 | audit #6 | a handover never hangs |
| ref_weight | 0.7 | 0.7 (0.8 withdrawn) | 1 h: +17 ± 6; 3 h at the lagged mark: -23 ± 24 / -46 ± 28 | none |
| capital_in_positions_max_frac (new) | - | 0.75 (factor 0.25) | owner: 90% in positions | adding sides at quarter size until positions turn over |
| skew_age_* (new) | - | 0.25c/h after 1 h, max 2c | median age 7.2 h | faster unloading of old positions |
| pair_unwind_* / arb_two_sided (new) | - | on | 17.7k paired capital; ask-sum < 0.98 in 2.5% of race-minutes | capital freed; small riskless gains |
| ref_only_use_tops / startup_books_first (new) | - | on | coverage 30 -> 101 in 6 min after restart | ~190 priced within 2 min of a restart |
| refill_cooldown_* (new) | - | on | -936 on 3rd+ same-side fills | fewer walks against us |
| fl_bias_enabled (new) | - | off | §10c: a skew artefact | none |

## Packages

### Package 1 (READY 10:10 UTC): audit and ops fixes. Code deploy (handover restart). Commit "READY: Package 1".
Safe, no strategy change. Fixes the Reviewer's audit findings on the live code and the issues seen after the 08:08 deploy.
Suites: test_mm_bot 381, test_ref_prices 37, test_strategy 38, test_stress 20, green on Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `ref_prices.py`, `deploy/handover-restart.sh`, `deploy/RUNBOOK.md`, `tests/` (copy them all; nothing else).

| Change | Evidence | Setting (default) |
|---|---|---|
| HTTP pool 4 -> 10 connections (parallel_requests + parallel_writes + 4); Polymarket pool parallel_fetches + 2 | live "pool size 4" warnings: 8 threads on 4 connections, each overflow a fresh TLS handshake that inflated write times | - |
| Write budget 30 -> 45/min, growing +1 per 60 clean writes to 50; any 429 cuts it to 75% (a read 429 only when it has grown) | 80 req/min ran 13 h without a 429; 63 minutes at >= 40 writes had none; the 6 429s followed bursts of 33-53 writes; the 08:09-08:12 deferrals were the 30/min budget | `writes_per_minute` 45, `writes_per_minute_max` 50, `write_budget_cut` 0.75 |
| Start-up keeps writes at 30/min while the first books download | book downloads were starved by writes after the restart | `startup_writes_per_minute` 30 |
| Reprices that remove an UNSAFE order (beyond its limit, oversized, unwanted side) are as urgent as pulls, never deferred | budget deferrals could leave an unsafe order resting | `never_defer_unsafe` True |
| Order batches 20 -> 10 | 273 of 417 409s were full 20-order batches; batches under 20 failed ~5% | `batch_size` 10 |
| Burst mode: the cycle-length trigger is ignored for 90 s after start and while books still load; half-size orders compared at burst size; full-size orders placed before a burst are not "oversized" | the 21 s first cycle tripped burst for 2 min at 08:08; a 375 quote was cancelled and re-placed every cycle | `burst_startup_grace_seconds` 90 |
| Takes need a Polymarket price fresher than 30 s | a failed Polymarket fetch kept a 5-min-old price that counted toward the take confirmation | `take_ref_max_age_seconds` 30 |
| Positions-409 fallback bounded: after 120 s the cycle fails and quotes are pulled after max_failed_cycles | unbounded reuse froze inventory for limits, skew, party delta and risk while fills landed | `positions_stale_max_seconds` 120 (`positions_stale_max_cycles` 0) |
| Reduce-only hysteresis: enter above 30%, leave below 27%; backstop 69% leaves below 66% | risk oscillating at the cap flipped reduce-only, pulling ~72 orders each time past the write budget | `reduce_only_hysteresis` 0.03 |
| More than 25 pure pulls in one cycle -> ONE tournament-wide cancel-all, re-placement next cycle (never in burst mode, never during the self-test) | each reduce-only flip cost ~2.4 min of write budget | `pulls_cancel_all_over` 25 |
| Handover exit capped at 150 s (then logs flushed, exit 0; the next start re-reads orders); the script waits 240 s and falls back to a plain restart | a > 90 s exit left the script exiting 1 with no bot started | `handover_exit_max_seconds` 150 |
| Churn control counts a reprice when its cancel is sent, not when planned | deferred/dropped changes hit churn_max_reprices without a reprice | `churn_count_sent` True |
| on_cycle_error retries the pull-everything when the cancel-all was partial; selftest_eid cleared on pass; duplicate thin_book_prices removed; throttle window kept sorted | hygiene from the audit | - |
| Live-settings whitelist extended: skew_*, improve_ticks, undercut_step_back, ref_only_*, risk_swing_shock, risk_z, worst_case_backstop_frac, order_ttl, refresh_before_expiry, batch_size, kelly_no_edge_frac and all new settings | parameter packages without a restart | - |

Deploy: `deploy/RUNBOOK.md` section B (stage, test with the server venv, back up, copy, `deploy/handover-restart.sh`).
Rollback: section C (copy the backup back, handover restart). Every setting above can also be reverted alone via settings_override.json.
Watch in the first 10 minutes: (1) the "handover:" adopt line: adopted count = orders that were resting, no "clean slate";
(2) status.json: write budget 45/min, rate_limited_total stays 0, requests in the last minute < 80; (3) no "pulls planned ...
one cancel-all instead" unless reduce-only flipped; if it appears, orders_resting recovers next cycle; (4) no "BURST MODE" in
the first 90 s, "request budget: N deferred" small (< 10) after 2 min, no "pool is full"; (5) no "positions unavailable",
"ENTERING reduce-only" or "cycle failed" streaks; orders_resting back to the pre-deploy level within 3 min; priced rising.
