# Why held dead/quiet markets rest no reducing quote (snapshot 2 Oct 20:36:49 UTC)
Script: `analysis/poly_bias/no_reduce_quote.py DATA_DIR` (ops-snapshot-2026-10-02b, staged, not committed). Line numbers:
`mm_bot.py` on claude/finisher-package5 (live 439ac54 in brackets; same logic). Report only.

**Key fact.** `snapshots.our_bid / our_ask` is `ex.quote`, i.e. what `decide()` *wanted* that cycle (`record`, l.6862
[5770]), not the resting orders. A null there is a decision, so the write budget (6) cannot cause it.

| cause (first blocking step in decide/compute_quote order) | dead+quiet markets (of 85) | capital | code |
|---|---|---|---|
| **reduce-only clips the reducing side by the RACE-NETTED position**: leg of a complete set (other leg held the same sign), eff <= 0 | **74** (46 with eff exactly 0) | 11.8k | `ask_size = min(ask_size, eff_inv)` 2362-64 [2041-43]; eff = x - mean(other legs) 4308-22; reduce_only 4712, global_reduce 3619-20 |
| same, eff of the wrong sign without a full set | 2 | ~0 | 2362-64 |
| (7) reference guard: Polymarket > 5c beyond the book's price on the exit side | 9 (6 directional Dem/Rep pairs: NH Gov, TX Gov, RI Sen...) | 13.8k | `no_ask/no_bid ... ref_guard_gap` 4699-4702 [4163-66] |
| (1) fv None (one-sided / thin / wide book) | 0 (priced 237/237) | - | 4681 |
| (2) ref_only, (3) size plan/min size, (4) turnover-dead, (5b) capital-ceiling factor, (6) write budget, (9) ladder | 0 each | - | ref_only only sizes/widens (4723-28); size_min 100 sh; `turnover_control_enabled` False; `adding_factor` hits the growing side only (2399-2403); ladder off |
| reducing quote present | 48 (39 behind the best other quote; 25 of those sit at the fv + min_edge/skew band floor) | 19.9k | band 2249-50, clamp 2265-66 |

Also: the journal's last wanted-quote line agrees in 79 of the 85 (3 show a reducing side, 3 never logged after 08:14).
14 of the 76 clipped set legs instead quote the per-market ADDING side (e.g. Rep CA-22 -323, race +50: ask 0.140 x50;
Ind Montana -1002, race +6: ask x6), because reduce-only follows race-net.
**Reduce-only is the switch.** Held markets with no reducing quote: 82 at 18:00 (reduce-only) vs 21 at 19:09 and 22 at
20:10 (the two short windows out of it). The backstop keeps the bot reduce-only ~81% of the time (P6 (a)), and every
reducing-join feature is gated off there: `reduce_join_best ... and not reduce_only` 2281, `hold_quote` only
`if ... not reduce_only` 4779, fast unload `not reduce_only` 2295-96/4756.
(8) is the same 74 markets seen from the pair side: `pair_passive_quote` (T2.5, `pair_unwind_passive`, default False)
returns q unchanged when decide() left the slice side empty (6155), so in reduce-only it can never rest a set-leg exit.

## Sample (20 largest by capital; full table from the script)
| market | pos | race-net | other leg | cause |
|---|---|---|---|---|
| Rep / Dem RI Senate | -2393 / +1965 | -4358 / +4358 | pair | ref guard (Poly 0.009 vs book ~0.07) |
| Rep / Dem NH Governor | +2272 / -2259 | +4531 / -4531 | pair | ref guard (Poly 0.96 vs book ~0.89) |
| Rep / Dem TX Governor | +2614 / -2597 | +5211 / -5211 | pair | ref guard |
| Dem RI Governor | +887 | +837 | Ind +100 | ref guard |
| Dem FL-20 | +1740 | 0 | Rep +1740 | set leg |
| Rep FL-22 | -1633 | +854 | Dem -2487 | set leg |
| Dem FL-16, Rep WI-03, Rep/Dem OR Gov, Dem FL-09, Rep/Dem NV Gov, Dem TX-23, Dem VA-01 | -500..-900 | 0 | equal short | set leg |
| Ind Montana Sen; Rep MI-07; Rep CA-22 | -1002; -658; -323 | +6; +175; +50 | short | set leg (adding side quoted) |

## Minimal design (two hooks, both existing paths)
1. **Directional positions (race-net same sign as the position): `hold_quote` (C, `hold_target_hours`).** It already does
   exactly the spec: reducing side joins the best other quote, size <= position, floor `price - HOLD_FLOOR` on the book's
   price, never crossing the best other bid/ask, own adding side pulled a tick behind, respects `ref_guard_gap`. Change:
   let it run in reduce-only (4779 drops `not reduce_only`). Safe: the input q's reducing size is already clipped to
   race-net by 2362-64, so it can only shrink risk. Fixes the 39 "behind" markets; with the age gate (try 4 h) most dead
   capital qualifies (55% > 12 h). Same idea, smaller: drop `not reduce_only` from `reduce_join_best` (2281).
2. **Complete-set legs (74 markets, 11.8k): T2.5 `pair_unwind_passive`.** A single-leg exit raises the worst case, so it
   must stay a pair unwind. Change: in `pair_passive_quote` (6155) treat a slice side left empty ONLY by the reduce-only
   race-net clip as allowed (keep the block for fv None / stop-before-close / ref guard), pricing from `pp_candidate`
   (join the best other ask/bid on x, set fetches >= 1 - `pair_unwind_max_cost`), other leg taken on fill. 2-leg races
   only (3-leg Montana excluded by 6012).
3. Leave the 9 ref-guard markets alone: the guard says the exit is the wrong trade (88% of capital leans toward Poly).
