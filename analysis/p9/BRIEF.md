# Package 9 explorer brief (read PLAN_P9.md first: the objective is P(account >= 150k by 4 Nov), P(>= 200k), P(<= 85k) < 10%, max DD < 20%)

## What you are
One of four opus explorers (angles A-D). Unanchored: wander anywhere inside the tournament's rules. You NEVER push, never touch the
server or the exchange API, never switch git branches, never edit mm_bot.py or tests (you may READ them). Write only your own file
`analysis/p9/ideas_<angle>.md` (+ helper scripts `analysis/p9/<angle>_*.py`; scratch in the scratchpad dir). One CPU is shared by four
of you and the executor: keep data checks to sqlite / pandas on the snapshot; do NOT run tests/live_sim.py or the test suites.

## The data (read-only)
- `/home/claude/snap03/` = ops-snapshot-2026-10-03 (to 3 Oct 22:47 UTC): `md.sqlite` (built), `fills.csv` (5,832 fills: fill_id,
  filled_at, exchange_id, order_id, our_side, qty, fill_price, quote_price, fv_at_quote, fv_after), `journal_2026-10-02_2037_to_now.log.gz`
  (bot journal 2 Oct 20:37 -> 3 Oct 22:47: quote lines, `realtime |` summaries, FILL, SETTING, PAIR UNWIND, ARBITRAGE, self-test and
  refusal lines, 2-hourly "Rank N of M" lines), `status.json` (Package 8 health: tilt_s, tilt_diag, tilt_exposure, nono_sets,
  liquidation_value, cash gate, positions), `position_lots.json`, `order_notes.json`, `settings_override.json` (the live overrides).
- `md.sqlite` tables: `snapshots` (548k rows: ts, mode, eid, label, best_bid, best_ask, fair_value, reference [Polymarket], our_bid,
  our_ask, position; one row per market per cycle since 28 Sep), `books` (14k: ts, eid, bids, asks as JSON [price, qty] x 3 levels),
  `positions` (53k: ts, prev_ts, eid, quantity, current_price [the EXCHANGE's mark], fields), `account` (2.3k: ts, mode, account_value,
  locked_in_orders, worst_case_loss, party_delta, orders_resting, liquidation_value), `account_marks` (3k: ts, account_value,
  market_value, cash, realized, unrealized, pnl_ts, fields), `trades` (empty). Market labels: "<Party> <State> <Race>" (e.g. "Dem North
  Carolina Senate", "Rep House", "Dem House"); 237 contracts / 117 races; a race's legs are the parties of one state-race.
- Earlier research (repo): `PLAN_POLY_BIAS.md` (the tilt diagnosis), `analysis/poly_bias/*.md` (RESULTS, TIER2, TAKES, SNAPSHOT02B,
  P6_RESEARCH, NO_REDUCE_QUOTE, TILT_ESTIMATOR, TILT_EXIT), `DATA_REPORT_2.md`, `ideas/IDEAS_ROUND1-3.md` (older idea rounds: do not
  repeat them unless you add a new mechanism or a number), `SIM_NOTES.md` (every simulator round), `START_HERE.md` (Packages 4-8),
  `README.md` (how the bot works, risk controls, rules).
- The bot: `mm_bot.py` (~9.3k lines; Config block ~line 300-940 with every setting documented; OVERRIDABLE after it); the simulator
  `tests/live_sim.py` (world knobs `_rival_anchor`, `_world_tilt`, `_world_tilt_growth`, `_bg_wc*`, `_start_cap`; fields pnl_lag,
  pnl_liq, tilt_exposure_end, cap_end, writes_pm ...).

## Facts to build on
- Tournament: SIG Predictions Cup, US midterms, play money, 100k start, ends 4 Nov 00:00 UTC (31 days). The leaderboard ranks by the
  exchange's account value = cash + positions at the exchange's `current_price` (a lagged trade-price average; ~1.4k below the book mid
  for us; our own fills move it ~0.05c each). Leader ~+600%; we are +1% (rank 174 of 1039). Smart Score (win rate, ROI) is separate.
- The favourite-longshot tilt: tournament mid ~ c + (1 - s)(Polymarket - c), c = 1/legs; s 2.4% (1 Oct) -> 6.3% (2 Oct) -> 11.0% (3 Oct),
  +0.0036/h lately; it has never reversed. We hold +35.9k of tilt exposure (sum pos x (Polymarket - c)): every point of s marks us -356.
- Live constraints: capital 99.7% in positions, free cash ~0, reduce-only most cycles (sum-of-maxima worst case 79k vs 0.8 x account),
  adding factor 0, write budget 28/min, arb off, NO+NO sets 21.9k capital (16.8k sets across 15 races; a set returns 1 at the close).
- Rules: no order placed to move a price, no self-trades, no wash prints, respect the write limit. Anything else legal is in play.
- SIG's end valuation (settled at the outcome vs marked at the close) is unknown: give both scenarios where it matters.

## Deliverable: `analysis/p9/ideas_<angle>.md`, at least 15 ideas, each in this exact shape
```
### <angle>-<n>. <name>
Mechanism: <how it makes money / raises P(>= 150k), in 2-4 lines>
Data check: <the number(s) you computed from the snapshot, with the query/script name; or "not checkable offline: <why>">
Upside: <% of account per month, point estimate and range> | P(>= 150k) effect: <guess, with reasoning> | Ruin / drawdown: <what kills it, how far>
End-valuation: <settled-at-outcome vs marked: does the idea depend on it?>
Legitimacy: <why it is fair play>
Build: <setting name(s), ~lines, where in mm_bot.py; or "config only" / "manual">
Screen: <live_sim knob / data-only test / cannot be screened before live>
```
End with a ranked TOP 5 for your angle (by P(>= 150k) per unit of ruin risk) and a list of the assumptions you could not check.
Be concrete and numerical; a wild idea with a mechanism and a number beats a safe idea without one. Finish writing by 01:45 UTC.
