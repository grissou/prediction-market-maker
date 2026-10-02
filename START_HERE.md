# START HERE (Team run, branch `claude/run-c-tournament-improvements-pycdet`)

Status: **in progress** (started 2026-10-02 08:50 UTC). Base: `claude/live-2026-10-02b` (5c0463a), the code live since 08:34.
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
| (none yet) | | | | |

## Packages
(none READY yet)
