# PLAN_POLY_BIAS: the Polymarket-convergence problem (for Finisher 2b, Opus executor)

Planner: Finisher 2a, 2 Oct 2026. Branch `claude/finisher-plan` (on Package 4, fd28b0a). No code changed here:
only `analysis/poly_bias/` (phase0.py, RESULTS.md) and this file.

## 0. Verdict: PARTLY CONFIRMED (snapshot 2 Oct 08:14 UTC; numbers in analysis/poly_bias/RESULTS.md)

| Claim | Data | Result |
|---|---|---|
| Positions lean toward Polymarket | 76% of open capital entered toward Polymarket, 11% against, 12% no gap; by the gap now 77% vs 3% | confirmed |
| Cannot exit those | exited so far: 65% of toward-entered capital vs 85% of against-entered; open capital median age 8.8 h, 69% older than 6 h; closed lots median 14 min | confirmed |
| Size follows the gap | mean capital 1,194 per position at 5c+ gaps vs 175-434 below; 2c+ entries = 52% of capital; 5 of the top 8 sit at ~2,000 = `kelly_max_market_frac` | confirmed |
| No convergence | gaps on the big positions widened since entry (Rep U.S. House -3.2c to -5.5c, NH Governor 6.7c to 7.2c) | confirmed so far (16 h) |
| Gain is mostly marks | +1,509 = realised +154, unrealised -42, spread capture and other +1,398; liquidation haircut 428 (top of book) | NOT confirmed |
| 90% of capital in positions | 51% at 08:14 UTC (after a reduce-only night); worst case 32% | later than the data: unchecked |

Reading: the bot does make markets (exits/entries 0.76, fills netted per 2.6 min), but what stays on the book is the
Polymarket bet. The open book is a +1,839 claim on the gaps closing; it is not a marked-up gain yet.
Also: 14% of position capital is the U.S. Senate pair (long both legs, 7,335 each), a pair-unwind matter, not bias.

## 1. Yardstick audit (read, not run)

| Metric | Where | How | Polymarket-marked? |
|---|---|---|---|
| pnl | strategy_sim.metrics ~495 | cash + inv x Polymarket end price | yes |
| pnl_mid | ~488-496 | cash + inv x mid of the OTHER traders' best bid/ask | no, but see the world note; computed, NOT in live_sim KEYS |
| pnl_lag | ~497-502 | cash + inv x VWAP of all prints in the last 30 min | partly (prints mix rivals and humans) |
| markout15_c, pick_cost | ~494, ~515 | fill price vs the Polymarket path 15 min later | yes |
| max_dd | ~475-481 | peak-to-trough of cash + inv x Polymarket, each minute | yes |
| worst, cap_frac | ~476 | settlement capital at Polymarket prices | yes (small effect) |
| live_sim start | live_sim ~135 | `m.cash = -m.inv * m.p0`: starting inventory booked at Polymarket | yes |
| live_sim equity, cap_end, freed, wc_* | live_sim ~240-250, ~376 | positions valued at Polymarket | yes |

- Decisions resting on the Polymarket mark: Round 2 (ranked on pnl_lag, partly), Round 3, Round 3b and Round 4,
  all of them. Suspect in particular: fast_unload -165+-110, reduce_join_best -48+-81, turnover_control +49+-62,
  capital_ceiling_adding_size_factor 0.5 vs 0.25 +262+-100.
- THE WORLD IS BIASED TOO, not only the mark. Sim rivals price from Polymarket (`rival_step` ~276:
  `f = seen + offset`, live_sim LIFT 0.3c) and take anything through that. Only the "humans" sit at the consensus
  (Polymarket + gap). So in the sim the bots agree with Polymarket and a liquidation mark at the rival book is
  nearly a Polymarket mark. Live says otherwise: 178 of 229 markets show a gap over 1c with rival bots present.
  A liquidation mark alone will not fix the yardstick; the rivals need an anchor knob (Phase 1).
- Where the designs live: live_sim calls the real `Bot.decide` (bot_strategy ~410-420, passing `book_fv=bfv`).
  A, B and the quoting half of C belong in `Bot.decide` (~4223-4330) / `compute_quote` (~1934) / `kelly_position`
  (~1789): live_sim picks them up with no mirror. The TAKING half of C is not in decide (takes run from the main
  loop, like `take_stale_quotes` ~5589): it needs a mirror in live_sim (next to `Sim.take` ~331 / `arbitrage` ~292)
  behind the same flag, with a pinning test. Nothing here touches plan_changes.

## 2. Phase 1: fix the yardstick first (no strategy change; about 60 CPU-min)

1. World knob, live_sim special key `"_rival_anchor"` (default 0 = today): rivals' fair = Polymarket +
   anchor x m.bias. Run everything at 0 and at 1. 1 is the world the live data describes.
2. New fields, in live_sim KEYS so every variant line prints the delta +- SE:
   - `pnl_mid`: start AND end marked at the other traders' mid (store inv0, mid0; do not use p0 for the start).
   - `pnl_liq`: start and end at liquidation (longs at the best other bid, shorts at the best other ask;
     no quote on that side -> the consensus path value minus 2c).
   - `mk15_mid`: markout of each fill against the consensus path `c` 15 min later (next to markout15_c).
   - `exit_ratio`: shares of our fills that reduced |inventory| / shares that added (replay fills per market from inv0).
   - `hold_med`: median holding time of closed lots, hours (FIFO, starting lots aged from `_start` ages).
   - `pick_cost`, `wc_end`: already computed, add to KEYS. Keep pnl, pnl_lag, cap_end, freed, writes_pm.
3. Pinning tests (tests/test_live_sim_marks.py):
   - same seed, `_rival_anchor` 0: pnl, cap_end, writes_pm, shares identical to before the change;
   - no-trade run: pnl_mid = sum inv0 x (mid_T - mid0), pnl_liq <= pnl_mid;
   - one long bought 3c under Polymarket with the gap held: pnl > 0, pnl_mid ~ 0, pnl_liq < 0;
   - exit_ratio = 1 and hold_med exact on a scripted buy-then-sell.
4. Re-score, 8 seeds x 3 h quiet, both anchors, Package 4 BASE_JSON with `_start_cap` 0.90 (6 CPU-min per
   config per anchor at 15 CPU-s per seed-hour): base, `fast_unload_enabled`, `reduce_join_best`,
   `turnover_control_enabled`, `capital_ceiling_adding_size_factor` 0.25, `ref_weight` 0.5, `ref_weight` 0.3.
   One command per anchor: 7 runs = 42 CPU-min; both anchors 84. If over time, run anchor 1 in full and at
   anchor 0 only base (the old numbers stand in). Write the table into SIM_NOTES.md "Round 5".

## 3. Phase 2: designs (each behind its own setting, OFF by default, all in OVERRIDABLE)

| | Setting (default) | Where | What | Specific risk |
|---|---|---|---|---|
| A | `reduce_from_book` (False), `reduce_from_book_pause_s` (120) | `Bot.decide` ~4290-4330 passes `reduce_fv=book_fv`; `compute_quote` band ~1992-2001 | The reducing side only (long -> ask, short -> bid, size <= position) measures min_edge from book_fv, never beyond the blend toward Polymarket; the adding side keeps the blend. Off for 120 s after a Polymarket move >= ref_jump_threshold in that market (reuse the jump test behind `lad_pull_until` ~4681), and off in ref_only markets and when book_fv is None | Self-cross: with a 5c gap the reducing ask (book+1c) is BELOW our own adding bid (blend-1c). Rule: while it applies, cap the adding side at the reducing price minus 2 x min_edge. Without it we sell low and rebuy high (and it would look like wash trading). Also sells to informed takers when Polymarket is right: watch pick_cost, news regime |
| B | `kelly_edge_cap` (0 = off; try 0.015, 0.01), plus existing `kelly_max_market_frac` 0.02 -> 0.01 and `headline_position_frac` 0.10 -> 0.03-0.05 | `kelly_position` ~1801-1806: `edge = min(edge, cap)`; headline limit `decide` ~4287 | Size on the spread, not the gap | The edge cap alone does not bind on favourites: at cost 0.90 quarter Kelly on 1.5c is 3.75% > the 2% cap. So B = edge cap AND kelly_max_market_frac. Fewer shares where Polymarket is right; headline cap below a held position must only stop adding (pin it) |
| C | `hold_target_hours` (0 = off; try 4), `hold_unload_budget_frac` (0.02 of account per hour) | quote half: `decide`, reuse `unload_side`/`unload_edge` (~4322) with age from `age_hours` ~3976; take half: new `take_aged` beside `take_stale_quotes` ~5589 + live_sim mirror | Lots older than T: reducing side joins the tournament best (floor book_fv). Older than 2T: take, inside the hourly budget | Crosses the spread at the worst moment in a thin book (cap per order, never through book_fv by more than 1c); takes cost writes; a budget reset bug dumps everything: pin the budget |
| D | no code: `ref_weight` 0.5 / 0.3 / 0, `ref_jump_threshold` 0.03 -> 0.015 | override | Blunt comparator | Fair value follows a noisy, thin book: more re-prices (writes), picked off on real Polymarket moves; both sides move, so it also stops the profitable adds |

Expected winner: A + B together. A opens the exit without giving up the Polymarket lean on entries; B removes the
"biggest where the tournament disagrees most" sizing and cuts the worst case. C is the backstop for what A does not
clear (expect it to cost P&L at any honest mark). D should lose in news runs (pick_cost) and on writes; it is the
comparator, not the candidate. Existing age skew (`skew_age_*`, max 2c from the blend) cannot bridge a 5c gap: that
is why it has not cleared these lots.

Write budget (28/min): A adds no orders but its quote follows the noisier book_fv: expect more re-prices, test with
and without `no_chase_enabled`. B costs nothing (fewer adds). C: 1 write per take; cap takes at 2/min. D at
ref_weight <= 0.3: every quote follows the book; expect the largest writes_pm rise. Any design with writes_pm
above 28 or deferred_h up by more than 20% fails.

## 4. Phase 3: protocol

- Screen 8 x 3 quiet at `_rival_anchor` 1 (6 CPU-min per variant, paired with base): A; B (0.015 + frac 0.01;
  0.01 + frac 0.01 + headline 0.05); C (T=4); A+B. D comes from the Phase 1 re-score.
- Pass = d pnl_liq not below -1 SE; cap_end down 5+ points; wc_end down; writes_pm <= 28; pick_cost not up by
  more than 1 SE; exit_ratio up. Report d pnl (Polymarket) alongside, never as the judge.
- Confirm passers at 16 x 6 quiet AND news. Cost 24 CPU-min per config per regime, so 48 per design plus 48 for
  base: this breaks the 30 CPU-min per design cap. Within the caps: confirm the best TWO at 8 x 6 quiet + 8 x 6
  news (24 CPU-min each, base 24) = 72. Total: screen 36 + confirm 72 = 108 <= 120. The full 16 x 6 needs the
  owner to lift the cap; say so in the results rather than exceed it.
- Stop a design early if the first 4 seeds put d pnl_liq below -3 SE.

## 5. Phase 4: ops

- status.json and the 2-hourly summary: `liquidation_value` (account value minus the haircut: longs at best bid,
  shorts at best ask), `realised_pnl` and `unrealised_pnl` (FIFO lots from `update_lots`), `toward_ref_capital_frac`,
  `capital_over_6h_frac`, `exit_ratio_24h`. Summary line: "Liquidation 101.1k (account 101.5k), realised +154,
  76% of capital toward Polymarket, 69% older than 6 h".
- Recorder: add liquidation_value to the `account` table (new nullable column; old rows stay).
- analysis/poly_bias/phase0.py runs on any new ops snapshot: rerun it on the next one (the afternoon state).

## 6. Deploy staging (owner decides every step)

1. Nothing live until Phases 0-1 are in and the re-score table exists.
2. Then the winning flag ON for non-headline markets only (executor: add `<flag>_headline` False, or gate on
   `ex.group not in cfg.headline_races`). 2-hour watch list: capital in positions (down), exit_ratio (up),
   realised P&L (not negative beyond 0.3% of the account), writes/min (<= 28), deferred changes, fills on the
   reducing side within 2 min after a Polymarket jump (should be none), no own bid >= own ask in any market.
3. Then headline. Rollback at any point = the flag off in settings_override.json (read within overrides_seconds).

Tonight, no code, OWNER'S DECISION (not sim-tested at an honest mark):
- `"kelly_max_market_frac": 0.01`: halves the size the gap can reach (the top non-headline positions sit at the
  2% cap). Only the adding side shrinks; nothing is forced out. Risk: less capacity where Polymarket is right; low.
- `"headline_position_frac": 0.05`: Rep U.S. House is -8,246 (7.2k capital, gap 5.5c against the tournament).
  Risk: confirm in a test first that a limit below the held position only stops adding (I read it so in
  compute_quote `limited` ~2073-2077, did not run it).
- Keep `capital_ceiling_adding_size_factor` 0.5 for now: 0.25 "lost" only on the Polymarket mark; re-score first.

What NOT to do:
- Do not lower `ref_weight` live before the news runs: at 0-0.3 the bot stops pulling toward real Polymarket
  moves and gets picked off; the sim's informed takers trade exactly that.
- Do not turn on fast_unload / reduce_join_best on the strength of "the old test was biased": re-score first.
- Do not judge anything on pnl (Polymarket mark) alone again, nor on the liquidation mark at `_rival_anchor` 0.
- Do not hand-liquidate the gap positions at market: the haircut is small at the top of book (428) but depth is
  unmeasured, and dumping into a thin book moves the price against the rest of the position.
- No design may place an order to move a price, trade with itself, or exceed the write limit (tournament rules).

## 7. Not checked
- The afternoon of 2 Oct (90% in positions, 112 positions, 10-11k free): the snapshot ends 08:14 UTC.
- Book depth: liquidation is top-of-book only; snapshots hold no depth.
- Fill-level prices: fills.csv has 827 of 1,813 fills with an unknown side, so trades are priced at the mid from
  position changes; "spread capture" is a residual (+1,398), not measured directly.
- How the exchange marks account value (mid, last or other).
- No simulation was run; every Phase 2 expectation is reasoning from the code and the data.
- Line numbers are approximate (Package 4, fd28b0a).
