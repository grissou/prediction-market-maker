# START HERE (Team run, branch `claude/run-c-tournament-improvements-pycdet`)

Status: **in progress** (started 2026-10-02 08:50 UTC). **Package 1 READY at 10:10 UTC** (deploy-ready). Base: `claude/live-2026-10-02b` (5c0463a), the code live since 08:34.
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
