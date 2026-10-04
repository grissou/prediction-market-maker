# Package 10, explorer I: the capital allocator, value market making, the two options, and the panic-sell audit

Data: `/home/claude/snap03` only (to 3 Oct 22:47 UTC). The 07:30 state (tilt 0.14, party delta -13.8k, value mode live) is **not** in
the snapshot. Today's tilt enters only as a sensitivity: every book price is moved a further 0.03·(p - c) away from p (`I_DS=0.03`).

Scripts (read-only, each under 15 s; they import H's `H_outcome.py` and reuse `H_board.py`'s field on the same worlds):

| Script | What it does |
|---|---|
| `I_alloc.py` | Edge per $ of every held position, every NO+NO set and every book level; the paired rotation list; the bloc-delta and share party-delta change. Env: `I_K` (calibration), `I_DS` (tilt shift), `I_CAP`, `I_TOP`. |
| `I_recycle.py [paths]` | Option 1: recycling on convergence under five tilt paths, on the current book and on H's plan (a). Env: `I_DISP` (per-contract tilt dispersion; H's 0.5 by default). |
| `I_senate.py [N]` | Option 2: the 34-race Senate seat count against the control market; the Rep-side instruments with depth; sleeves on the (a) core with E, P(>=150k), P(<=85/70k) and the leaderboard rank, under Polymarket odds and under the seat-count control. |
| `I_mmval.py` | Every fill valued AT THE OUTCOME (latest Polymarket): by day, price bucket x side, takes vs maker, the tail value side vs the against side, and the 22:00-22:47 journal hour. |
| `I_closeout.py` | What the election-night flatten / exit would give up at the outcome on today's book. |
| `I_carry.py` | What a funded value market maker adds to P(>=150k), with and without the Senate sleeve. |

Conventions:
- p = the race-normalised Polymarket reference (`H_outcome.load`). Calibration k = 1.0 unless stated.
- Edge per $:
  - long YES held: (p - bid) / bid;
  - long NO held: (ask - p) / (1 - ask);
  - buy YES: (p - ask) / ask;
  - short YES: (bid - p) / (1 - bid).
- Both legs of a rotation trade at the touch, so every gain below is **net of the spread**.

## Headline numbers
1. **Allocator, first action list:**
   - 82 paired moves rotate $35.4k for **+3.44k** at the outcome; the top 30 rotate $11.1k for +1.95k.
   - At today's tilt (+0.03): +4.18k for $42.6k. At k 1.1: +3.85k; at k 0.9: +4.68k.
   - Full rotation takes the share party delta from -15.35k to -35k, far past the 0.15 cap. The bloc delta goes from +2.55k to
     +0.56k per sd. **The share cap must be replaced by the bloc delta (H-2) before the allocator runs.**
   - Edge supply: **nothing at ≥ 15% per $** at 22:47. There is $177k at ≥ 10%, rising to $295k at tilt +0.03.
2. **The NO+NO sets cost more than 2c to unwind at the touch.**
   - All 15 races: ask sums 1.025-1.055, so 2.5-5.5c per set.
   - Unwinding all of them frees **$21.2k for $668 of outcome EV** (3.1% per $ freed).
   - That is the cheapest MM reserve available. The owner's "≤ 2c" rule would unwind none of them. Judge per $ instead: the cost per $
     freed must stay below the redeploy edge minus 5 points.
3. **Recycling (option 1) on top of plan (a):**

   | Dispersion | Probability-weighted gain | Range by scenario |
   |---|---|---|
   | H's ±50% per contract | **+1.6k (+1.4% of EV)** | +0.5k (tilt keeps growing) to +4.2k (linear convergence) |
   | ±25% per contract | **~0** (+60) | |

   - **+10-20% of the account is not plausible.** It is +10-20% of the *edge* (the +9-13k value gain).
   - The reason: uniform convergence also erases the edges you would redeploy into.
4. **Option 2: the Senate seat count is robustly below the control market.**
   - P(Dem control) is **0.49-0.59** across rho 0.25-0.65, Ohio 0.30-0.50, Alaska (no reference) at the mid or 0.45, and Osborn
     caucusing or not. Polymarket says 0.645.
   - Reaching 0.645 needs Ohio 0.6 + Alaska 0.85 + Osborn caucusing with the Dems, all at once.
   - The best entry is Dem U.S. Senate short at 0.33 (3.4k shares), then Rep YES at 0.35 ($65k of depth).
   - **New leaderboard finding.** When the sleeve loses, the account ends < 100k and falls **behind the ~582 never-traded accounts at
     100k** (median rank ~790). H_board's ranks left them out.
   - So a $20k sleeve turns P(top 100) **71% → 32-42%** and P(top 50) **5% → 23-32%**.
5. **Value market making, valued at the outcome** (3 Oct, 26 h, from fills.csv):

   | Fill group | Shares | EV at the outcome | Per share | Per $ of capital |
   |---|---|---|---|---|
   | Maker, tail value side (favourite bids, longshot asks) | 38k | **+1,948** | +5.1c | +5.6% |
   | Maker, tail against side (favourite asks, longshot bids) | 41k | **-1,920** | -4.7c | |
   | Maker, middle (15-85c) | 95k | +604 | +0.64c | |
   | Takes | 24.7k | **+1,862** | +7.5c | 8.5% |

   - **This reverses F-4.** F-4 favoured the with-drift sides on 6-h *marks*; at the outcome those are exactly the losing sides.
   - It also reverses F-1 (takes off): **at the outcome the takes are +EV**.
   - Funded MM at 0.33 / 0.67 / 1.0k a day of outcome EV gives P(>=150k) **0% / 6% / 29%** with P(<=85k) ≈ 0.1%. That is the
     cleanest road to 150k if the carry is real.
6. **Panic-sell audit.** Three live paths can sell value today, and one more waits for the close:
   - **(i) reduce-only quotes.** No `max_skew_through` clamp in reduce-only, and the inventory + age skew reach 4c, so the ask can
     rest at fv - 3c.
   - **(ii) the blend.** `ref_weight` 0.7 leaves fv 0.3·gap below p, so even two-way quotes sell up to 1.5c below p where the gap
     is under 5c.
   - **(iii) election night.** `flatten_hours_before_close` 12 / `exit_hours_before_close` 2 at their defaults. In the last 2 h
     the exit takes bids down to fv - 3c, before every guard. On today's book that gives up **+1.1-2.5k** if the tilt has half/three-quarters
     converged, and up to 4.6k in the flatten window.
   - **(iv) the Senate sleeve.** The same close-out would liquidate it before the results and set P(>=150k) to 0.
   - The ref guard (raw, 5c) blocks the worst cases while `ref_guard_exits` is off.

## THE ALLOCATOR: first action list (22:47 books, k 1.0, tilt as of 22:47; `I_alloc.py`)

Read the list in three parts:
- **Rows 1-14** close dead or negative-edge positions. These are mostly NO on longshots that Polymarket prices at 0.1-2%, worth less
  than the cash they free. They hold $850 in total and gain +529.
- **Rows 15-30** are the real money. They sell one leg of a set, or a low-edge NO, and short the long-tilt crowd's rich bids.
- Buy Rep NH Governor and short Dem NH Governor are **the same bet**, the two legs of one race. Cap it per race ($15k), not per
  contract.

| # | Sell (edge held) | Buy (edge per $) | $ | Gain at the outcome | Cumulative |
|---|---|---|---|---|---|
| 1 | Rep South Dakota Senate NO (-97.4%) | buy Rep New Hampshire Governor @0.840 (14.3%) | 129 | +144 | 144 |
| 2 | Dem Rhode Island Governor NO (-77.8%) | buy Rep NH Governor @0.840 (14.3%) | 4 | +4 | 148 |
| 3 | Dem Illinois Governor NO (-55.2%) | buy Rep NH Governor @0.840 (14.3%) | 135 | +94 | 242 |
| 4 | Rep Maine Governor YES (-38.0%) | buy Rep NH Governor @0.840 (14.3%) | 2 | +1 | 243 |
| 5 | Rep Montana Senate NO (-36.0%) | buy Rep NH Governor @0.840 (14.3%) | 405 | +204 | 446 |
| 6 | Dem Minnesota Governor NO (-35.7%) | buy Rep NH Governor @0.840 (14.3%) | 130 | +65 | 511 |
| 7 | Rep Mississippi Senate NO (-35.4%) | buy Rep NH Governor @0.840 (14.3%) | 14 | +7 | 518 |
| 8 | Rep TX-28 YES (-35.0%) | buy Rep NH Governor @0.840 (14.3%) | 2 | +1 | 519 |
| 9 | Rep NE-02 YES (-32.7%) | buy Rep NH Governor @0.840 (14.3%) | 3 | +2 | 521 |
| 10 | Dem NJ-07 NO (-23.2%) | buy Rep NH Governor @0.840 (14.3%) | 6 | +2 | 523 |
| 11 | Dem Minnesota Senate NO (-15.7%) | buy Rep NH Governor @0.840 (14.3%) | 3 | +1 | 524 |
| 12 | Rep Nebraska Governor NO (-13.9%) | buy Rep NH Governor @0.840 (14.3%) | 4 | +1 | 525 |
| 13 | Dem AZ-06 NO (-8.8%) | buy Rep NH Governor @0.840 (14.3%) | 5 | +1 | 527 |
| 14 | Dem IA-03 NO (-7.2%) | buy Rep NH Governor @0.840 (14.3%) | 12 | +3 | 529 |
| 15 | Rep Nebraska Senate NO (-3.9%) | buy Rep NH Governor @0.840 (14.3%) | 195 | +35 | 564 |
| 16 | Rep Nebraska Senate NO (-3.9%) | short Dem NH Governor @0.155 (13.6%) | 645 | +113 | 677 |
| 17 | Dem Maine Senate NO (-3.6%) | short Dem NH Governor @0.155 (13.6%) | 42 | +7 | 685 |
| 18 | Rep VA-01 NO (-2.6%) | short Dem NH Governor @0.155 (13.6%) | 224 | +36 | 721 |
| 19 | Ind Nebraska Senate NO (-2.3%, set leg) | short Dem NH Governor @0.155 (13.6%) | 1,672 | +266 | 986 |
| 20 | Ind Montana Senate NO (-1.3%, set leg) | short Dem NH Governor @0.155 (13.6%) | 2,790 | +417 | 1,403 |
| 21 | Dem IA-02 YES (+1.0%) | short Dem NH Governor @0.155 (13.6%) | 1 | +0 | 1,403 |
| 22 | Rep Kansas Governor YES (+1.1%) | short Dem NH Governor @0.155 (13.6%) | 235 | +29 | 1,433 |
| 23 | Rep California Governor NO (+1.5%, no ref) | short Dem NH Governor @0.155 (13.6%) | 7 | +1 | 1,434 |
| 24 | Rep Alaska Governor NO (+1.5%, set leg, no ref) | short Dem NH Governor @0.155 (13.6%) | 1,864 | +226 | 1,659 |
| 25 | Rep Alaska Governor NO (cont.) | short Rep Rhode Island Governor @0.135 (13.6%) | 281 | +34 | 1,693 |
| 26 | Rep MI-07 NO (+1.7%, set leg) | short Rep RI Governor @0.135 (13.6%) | 368 | +44 | 1,737 |
| 27 | Ind Rhode Island Governor NO (+2.2%) | short Rep RI Governor @0.135 (13.6%) | 1,653 | +189 | 1,926 |
| 28 | Rep PA-01 NO (+2.5%) | short Rep RI Governor @0.135 (13.6%) | 33 | +4 | 1,929 |
| 29 | Dem Kansas Governor NO (+2.7%) | short Rep RI Governor @0.135 (13.6%) | 54 | +6 | 1,935 |
| 30 | Rep Michigan Senate NO (+2.8%) | short Rep RI Governor @0.135 (13.6%) | 170 | +18 | 1,953 |
| 31-82 | the rest of the held book with edge < ~10% | Dem CT Governor buy 0.86-0.87, Rep RI Senate short 0.125 (45.6k shares), Rep WA-03 short 0.175-0.185, Dem RI Senate buy 0.885, Rep WV Senate buy 0.885-0.89, Rep U.S. House short 0.165, Dem MT Senate short 0.11, Dem TX Senate short 0.67 / Rep TX Senate buy 0.33 | 24.3k | +1,487 | **3,440** |

Write cost:
- ~2 writes per move plus a cancel when our own quote rests there.
- Top 30 = ~60 writes (~2 min of the 28/min budget); all 82 = ~165 writes, spread over 2-3 hours under a 30% write share.
- About 20 of the 30 moves are under $50: set `alloc_min_move_usd` to 100. That keeps ~95% of the gain for a third of the writes.

Sensitivities (same script):

| Case | Gain | $ rotated | Note |
|---|---|---|---|
| Tilt +0.03 (today) | +4.18k | 42.6k | $-weighted held edge 9.8% |
| k 1.1 | +3.85k | | |
| k 0.9 | +4.68k | 53k | Choose at k 1.0 (H-9) |

## Recycling table (option 1; `I_recycle.py 300`)

Setup:
- Sell a value-side holding when its exit touch is within 2c of p, and redeploy the same day at the best edge (≥ 3%, depth-limited
  3 levels, the book refills daily, ≤ $10k per contract).
- New lots can recycle again (compounding).
- s(t) starts at 0.14 on 4 Oct. Per-contract s_j = s·exp(D·eta), with eta AR(1) and phi 0.7.

| Scenario for s(t) | Weight | Plan (a): $ recycled | Plan (a): gain vs hold (D 0.5) | Plan (a): gain (D 0.25) | Current book: gain (D 0.5)* |
|---|---|---|---|---|---|
| (i) keeps growing (B_mc: 0.29 at day 7, 0.215 at the close) | 0.40 | 0.9k | **+0.5k** (p90 +1.2k) | +10 | +3.6k |
| (ii) plateau 0.14 | 0.25 | 1.8k | +1.1k (p90 +3.5k) | +7 | +5.1k |
| (iii) linear convergence to 0.03 by 2 Nov | 0.12 | 45k | +4.2k (p10 +2.0k, p90 +6.6k) | +216 | +11.1k |
| (iv) step convergence in the last week | 0.18 | 34k | +2.6k | +119 | +8.1k |
| (v) early: 0.03 by 14 Oct, back to 0.14 at the close | 0.05 | 30k | +3.3k | +136 | +8.6k |
| **Probability-weighted** | | | **+1.6k (+1.4%)** | **+60** | +6.0k* |

\* The current-book column includes the one-time reallocation of positions that are already within 2c (the allocator's job). It is not
recycling.

Verdict: **the owner's "+10-20% if convergence comes early" is not plausible as % of the account.**
- Even the best convergence path adds +3-4k (3-4%) on the reallocated book.
- If the convergence is uniform (dispersion 0.25), recycling adds almost nothing: when your positions converge, the frontier converges
  with them.
- Recycling is a small, free rule inside the allocator. Its value comes from contract-level dispersion, not from the market-wide tilt.

## Option 2 frontier (`I_senate.py 20000`)

Setup:
- Core = H's plan (a), scaled down by the sleeve.
- rho 0.45, k 1.0.
- Rank on H_board's field (same worlds), **plus the 582 never-traded accounts at 100k**.
- "PM" = the control market settles at Polymarket odds (P(Rep) 0.355); "seat" = it settles by the simulated seat count (P(Rep) 0.445).

| Plan | Model | E | P(>=120k) | P(>=150k) | P(<=85k) | P(<=70k) | Median rank | P(top 50) | P(top 100) |
|---|---|---|---|---|---|---|---|---|---|
| (a) core only | PM | 113.1k | 29% | 0% | 1.8% | 0.1% | 85 | 5.5% | **71.5%** |
| + Rep Senate $10k | PM / seat | 111.8 / 114.6k | 32 / 41% | 0 / 0% | 3.6 / 3.5% | 0.1% | 166 / 120 | 11 / 16% | 34 / 42% |
| + Dem Sen short $3.5k + Rep Sen to $15k | PM / seat | 111.4 / 115.5k | 34 / 44% | 9 / 12% | 3.9 / 3.7% | 0.3% | 766 / 142 | 19 / 28% | 31 / 41% |
| **+ Dem Sen short + Rep Sen to $20k** | PM / seat | 110.7 / 116.2k | 35 / 44% | **16 / 19%** | **5.0 / 4.7%** | 2.3% | 793 / 182 | 23 / 32% | 33 / 42% |
| + Rep Senate $22.5k | PM / seat | 110.2 / 116.3k | 35 / 44% | 25 / 32% | **25 / 23% (cliff)** | 3.4% | 803 / 776 | 25 / 34% | 33 / 43% |
| + Rep Senate $30k | PM / seat | 109.3 / 117.4k | 35 / 45% | 34 / 43% | 52 / 46% | 3.8% | 827 / 813 | 28 / 37% | 34 / 43% |
| + Rep Senate $40k (the owner's "+40-60% / -30-40%") | PM / seat | 108.0 / 118.8k | 35 / 45% | 35 / 44% | 65 / 56% | **51 / 45%** | 862 / 847 | 30 / 40% | 34 / 44% |
| + Mix50 $30k (half control, half 6 competitive Rep Senate legs) | PM / seat | 108.6 / 112.6k | 35 / 44% | 16 / 18% | 32 / 30% | 3.3% | 775 / 153 | 20 / 26% | 35 / 44% |
| + Mix50 $40k | PM / seat | 107.0 / 112.5k | 35 / 45% | 25 / 29% | 44 / 41% | 17% | 804 / 772 | 25 / 33% | 35 / 44% |
| + 6 competitive Rep Senate legs $40k | PM / seat | 105.9k | 34% | 10% | 31% | 15% | 158 | 16 / 15% | 40 / 39% |
| + 6 competitive Rep Senate legs $60k | PM / seat | 102.0k | 35% | 19% | 42% | 29% | 781 | 21 / 20% | 38% |
| Funded MM 1k/day (no sleeve; `I_carry.py`) | PM | 140.6k | 89% | **29%** | 0.1% | 0.0% | | | |
| Funded MM 0.67k/day + Rep Senate $15k | PM | 128.7k | 53% | 26% | 0.7% | 0.0% | | | |

**Honest answer to the owner on option 2.**
- "+40-60% on a good night, -30-40% on a bad one" describes a ~$40k Rep-Senate sleeve.
- The bad night is the more likely one: 55-65%. P(<=70k) is 45-51%.
- Competitive-race blocs at the ask are dominated (H-7 confirmed). Only Rep TX Senate at 0.33-0.34 has edge (+8-12%); Maine and
  Michigan Rep are -7% to -12%.
- The defensible version is **≤ $20k: the Dem Senate short at 0.33 first, then Rep YES at 0.35**:
  - P(>=150k) 16-19% at P(<=85k) ~5%;
  - +3.1k of EV vs (a) if the seat count is right, -2.4k if Polymarket's control price is;
  - it costs the top-100 finish (71% → 33-42%).
- 30% or 40% cannot be had without P(<=85k) going to 25-65%.
- A funded value market maker reaches P(>=150k) 26-29% at P(<=85k) < 1%, **if** it earns 0.67-1k/day at the outcome.
- **Recommendation:**
  1. Fund the MM first: the sets → a $20k reserve.
  2. Gate on 24 h of live outcome EV/day.
  3. Add the Senate sleeve at $10-15k only if the MM is below ~0.6k/day by about 8 Oct, or if the owner's objective is "top 50 or
     bust".
  4. Never go above $20k.

## Panic-sell audit (Package 8 live code; line numbers from HEAD's mm_bot.py, which is Package 8 plus the Package 9 additions)

| Path (where) | Setting today (value mode) | Can it sell a +EV position below Polymarket? | Guard |
|---|---|---|---|
| Quote blend `blend_fv` (cycle step 3, l.4062-4068) | `ref_weight` 0.7, `ref_tilt_enabled` false | **Yes, mildly.** fv = 0.3 book + 0.7 p, so it sits 0.3·gap below p for a value long. With `max_skew_through` 0 the ask floor is fv, so the ask sells up to 0.3·gap (≤ 1.5c while the ref guard lets it rest, gap ≤ 5c) below p. | In value mode the REDUCING side's floor is p - margin: `value_sell_floor` (below). Alternatively `ref_weight` 1.0 for liquid refs. |
| Reduce-only quoting: `decide` → `compute_quote(reduce_only=True)` (l.5305, l.2709) | Global reduce from the backstop (`worst_case_backstop_frac` 0.8: worst 79.2k vs 80.8k, reduce-only most cycles since 19:59) | **Yes.** The `max_skew_through` clamp is skipped when reduce_only (l.2709). Inventory skew (≤ 2c, `skew_max`) + age skew (≤ 2c after 9 h, `skew_age_*`) push the ask to fv - 3c, i.e. p - 0.3·gap - 3c (≤ ~4.5c below p). It rests passively and fills when a taker sweeps. | Apply the clamp in reduce-only too in value mode, with fv_ask = p. Better still: raise the backstop to 1.3 (H-10) so global reduce stops firing. |
| Ref guard (`decide` l.5280-5300) | `ref_guard_tilted` false, `ref_guard_exits` false, `ref_guard_gap` 0.05 | **No (protective).** With p - book > 5c the long's ask is blocked, in reduce-only too. Turning `ref_guard_exits` back on would exempt exits, and then value sells resume. | Keep `ref_guard_exits` false in value mode. Assert it in `warn_settings`. |
| `reduce_from_book` / `reduce_fv` (l.2675-2684, l.5362) | false | Yes if on: the reducing side prices from the tilted book (the deepest value sale). | Value mode forces it off. |
| Fast unload `unload_side` / `note_unloads` (l.9426) | `fast_unload_enabled` false | Yes if on: it unloads at fv - 1c. | Value mode floor (below). |
| `hold_quote` / `take_aged` / `hold_take_plan` (l.8386, l.8490) | `hold_target_hours` 0 (off) | Yes if on: aged lots join or take the book's price, which is the tilt. | Value mode forces it off. |
| `tilt_exit_side` + `tilt_exit_priority` / `tilt_exit_full_size` (P8, l.5622, l.5320) | Both false | Yes if on: it selects exactly the VALUE positions (those adding to tilt exposure) and sizes their exit to the full position. | Value mode forces them off. |
| Package 9 `tilt_exit_takes` (l.8242), basket `basket_send` / exits | Not deployed; `tilt_exit_take` false, `basket_enabled` false | Yes if on (by design: takes within 1c of the TILTED fv). | Not in a value-mode deploy. |
| Takes `take_stale_quotes` / `execute_take` (l.8000) | `take_enabled` true, `take_tilted_ref` false, `take_edge` 0.05 | **No.** A sell take needs p ≥ 5c below the bid. Takes are value buys: **+1,862 at the outcome on 24.7k shares on 3 Oct**. Blocked in reduce-only and in the flatten window (l.8039). | Keep them on in value mode (reverses F-1). Spend them through the allocator's write share. |
| Pair NO unwinds `pair_no_unwind_*` | `pair_no_unwind_max_cost` 0.003 (live; the 22:47 file has 0.02), `pair_unwind_followup` max cost 0.01 | **Small.** A set pays exactly 1, so the cost is ≤ 0.3c per set; but **every set's touch cost today is 2.5-5.5c**, so at 0.003 nothing unwinds (it is an empty path). The follow-up evens legs at ≤ 1c. | Unwinds belong in the allocator: release a set when (cost per $ freed) < (paired buy edge - 5 points) or to fund the MM reserve. |
| `arb_*` | `arb_enabled` false | No: a sell arb needs bids ≥ 1.03, a buy arb asks ≤ 0.985 (both +EV at the outcome). | Turning it back on is +EV, but it needs cash (`arb_cash_rule`). |
| `kill_switch` (l.4000, l.4817) | `max_drawdown_pct` 0.30 on MARKS (70k) | **No sale** (cancel and stop). It would end market making, though: tilt p95 marks -16k, plus a Senate sleeve marked down, approaches 70k with no loss at the outcome. | Value mode: kill on liquidation EV at Polymarket, or 0.40 (H-10). |
| Capital ceiling, `capital_ceiling_adding_size_factor` 0 | Adding factor 0 | No: it only blocks adding. **It is what stops value MM today.** | The allocator's reserve replaces it. |
| `mark_frag`, `turnover_dead`, `fl_bias` | Off / not applied (`turnover_control_enabled` false) / off | No (sizing only). `fl_bias`'s "bad side" (longshot bid) **is** the outcome-losing side: as a size cut it would be harmless. | None. |
| Self-tests (`selftest_attempt`, `nosell_test_order`, `pairno_test_orders`) | Run on start | No: 1 share at PMIN / PMAX, cancelled at once. | None. |
| **Flatten window**: `reduce_only = ... hrs <= flatten_hours_before_close` (l.5305), `flatten_per_market_hours` (l.5308) | **12 h / 6 h (defaults, not overridden)** | **Yes.** For the last 12 h every market is reduce-only (the second row's sale at up to ~4.5c below p where the gap is < 5c). **On today's book that is up to 4.6k of value** if the tilt has converged by 75%; it also sells the Senate sleeve. | `flatten_hours_before_close` 0 and `flatten_per_market_hours` 0 in value mode (owner to confirm SIG's rule). |
| **Exit window**: `decide` l.5250 → `exit_quote` (l.2890-2900), ladder l.5707, status l.9718 | **`exit_hours_before_close` 2, `exit_max_slippage` 0.03** | **Yes, as a taker, before every guard.** It sells at max(best bid, fv - 3c). On today's book at a quarter / half of today's tilt: 114 / 81 markets, **$103k / $40k sold, +2.5k / +1.1k of value given up**. It would also dump the Senate sleeve before the results. | `exit_hours_before_close` 0 in value mode. Keep `stop_minutes_before_close` 15. |

**The guard (Package 10 build, ~40 lines):** `value_mode` (bool, default False) and `value_sell_margin` (0.005).
- **compute_quote.** A new argument `value_floor` = the raw liquid Polymarket p (None without one). After step 4b (l.~2735):
  - if `inv >= 1`: `ask = max(ask, ceil_tick(value_floor - margin))`;
  - if `inv <= -1`: `bid = min(bid, floor_tick(value_floor + margin))`.
  - A side that then crosses the best other price stays out.
  - Also apply the `max_skew_through` clamp when reduce_only and value_mode (l.2709).
- **decide (l.5370).** Pass `value_floor = ref if ref_liquid and cfg.value_mode`.
- **In value mode, decide also:**
  - skips the exit branch (l.5250) and the flatten reduce_only (l.5305);
  - forces `reduce_fv` None and `u_side` None;
  - skips `hold_quote`.
- **exit_quote (l.2900).** Price floor `max(..., value_floor - margin)` when value_mode, so the floor also holds if the exit is ever
  re-enabled.
- **Allocator exemption.** Its sells go through their own IOC path. Each sell carries a `paired_buy` id and is only sent after a fresh
  book shows the paired buy level.
- **warn_settings.** In value mode, warn on any of: `ref_guard_exits`, `tilt_exit_*`, `reduce_from_book`, `fast_unload_enabled`,
  `hold_target_hours > 0`, `basket_enabled`, `flatten_hours_before_close > 0`, `exit_hours_before_close > 0`.

**Risk-limit mapping in value mode (what binds, at 22:47):**
1. Cash 0, with 489 "Insufficient funds" lines in 47 min.
2. `capital_ceiling_adding_size_factor` 0 (every adding side).
3. `worst_case_backstop_frac` 0.8 (79.2k vs 80.8k: reduce-only most cycles).
4. `max_party_delta_frac` 0.15 in shares (-15.35k vs 15.15k: Dem-YES adding blocked, although the bloc delta is +2.55k Rep-leaning).
5. R7 `max_worst_case_frac` 0.30 (25.0k / 30.3k = 82%).
6. Kill 0.30 on marks: far away, but tilt-sensitive.

So the first three are what stop market making. H-10's values (R7 0.35, backstop 1.3/off, bloc delta, kill 0.40 or EV-based) plus the
allocator's MM reserve unblock it.

## Ideas

### I-1. The capital allocator as a bot feature (hourly, paired sell → buy)
Mechanism: The bot holds one budget, and it should go to whatever earns the most per $ at the outcome. Once an hour the bot ranks every
holding by edge-held and every book level by edge per $ (own quotes stripped, liquid refs ≤ 30 s old). It pairs the lowest-edge
holdings with the highest-edge levels while the improvement is ≥ `alloc_min_improvement`.
- **Sell first** (IOC at the touch), **read the cash**, **then buy** (IOC at the touch) up to cash minus the MM reserve.
- A sell is sent only if a fresh book still shows its paired buy.
- If the buy level is gone, the cash stays in the reserve and nothing more is sold that hour.

Data check: I_alloc. 82 moves, $35.4k, +3.44k net of spread; top 30 +1.95k; tilt +0.03 gives +4.18k.

Upside: +3.4-4.2k (+3-4%) once, plus recycling +1.6k | P(>= 150k) effect: +0-1 point alone; it funds I-6 | Ruin / drawdown: none added (bloc
delta +2.55k → +0.56k per sd).
End-valuation: Outcome. If marked at the close, each move adds tilt exposure (about -s per $ of it).
Legitimacy: Ordinary trading at posted prices.
Build: Settings:
- `alloc_enabled` False, `alloc_interval_s` 3600;
- `alloc_min_buy_edge` 0.08, `alloc_max_sell_edge` 0.05, `alloc_min_improvement` 0.05;
- `alloc_min_move_usd` 100, `alloc_turnover_per_hour` 10000;
- `alloc_mm_reserve` 15000-20000, `alloc_max_contract_usd` 10000, `alloc_max_race_usd` 15000;
- `alloc_writes_frac` 0.3, `alloc_max_bloc_delta_frac` 0.05, `alloc_ref_margin` (I-10).

Code: ~250 lines, a new `allocator_tick` at cycle step 6b (after `take_stale_quotes`, before the basket) reusing `execute_take`'s IOC
plumbing, `cash_gate_cycle` for the cash read and H-2's `bloc_delta`.
Screen: Dry run on the live state (test_p9_dryrun harness): the action list must match I_alloc's ±10%; live_sim `pnl` (Polymarket mark).

### I-2. Replace the share party cap before the allocator runs
Mechanism: Rotation tilts the share delta by -20k (82 moves), far past the ±15.15k cap, while the outcome sensitivity (bloc delta) falls
toward 0. With the share cap, the allocator stalls after ~30 moves.
Data check: I_alloc's last lines.
- Top 30: share delta +14.6k (to -0.8k), bloc delta +1.3k.
- All 82: share delta -20.0k (to -35.4k), bloc delta -2.0k (to +0.56k).

Upside: lets I-1 finish (+1.5k of the +3.4k) | P(>= 150k) effect: indirect | Ruin / drawdown: none.
End-valuation: Outcome. Legitimacy: Risk model.
Build: H-2's `bloc_delta` with `max_bloc_delta_frac` 0.05 in `party_blocks` / `party_shift` (l.5421-5440) and in the allocator's
pairing check.
Screen: Unit test (closed form vs MC within 2%, done by H).

### I-3. The NO+NO sets fund the market-making reserve
Mechanism: 16.8k sets in 15 races hold $21.2k (at the touch) that earns 1.8-5.8% to the outcome. Unwinding them costs $668 of EV. The
cash then earns either the frontier's 12-14% once, or MM carry (I-6).
- Unwind passively: one tick inside the ask on each leg, paired via the existing pair_no_unwind plumbing. That saves ~1c per set.
- Use a per-$ cost cap, not the 2c-per-set rule.

Data check: I_alloc sets table.
- Ask sums 1.025 (MI-07) to 1.055 (Montana Senate, RI Governor, CA Governor).
- Biggest sets: Montana Senate 2,844 ($5.5k freed for $156), Nebraska Senate 2,229 ($4.4k / $78), Alaska Governor 3,000 ($2.9k / $105),
  Illinois Governor 3,000 ($2.9k / $105), SD Senate 2,146, MN Governor 1,859.

Upside: +1.9k if redeployed at 12% (net of $668), or the reserve for I-6 | P(>= 150k) effect: via I-6 | Ruin / drawdown: none (a set is
riskless; the cash keeps its value).
End-valuation: Outcome. If marked at the close, sets mark above 1 (C-17), so unwinding also removes a -535 mark drag.
Legitimacy: Trading.
Build: `pair_no_unwind_max_cost_per_usd` (cost / freed $) 0.04 in place of `pair_no_unwind_max_cost`, plus a passive mode, gated by
`alloc_enabled`. ~30 lines.
Screen: Dry run on the live state.

### I-4. Value market-making quoting rule (outcome book)
Mechanism: fair value = raw Polymarket p (liquid), minus the margin. Quote one tick inside the best other price, but:
- **adding side:** never beyond p/(1 + h) for a YES bid and 1 - (1 - p)/(1 + h) for an ask, where h is the per-$ hurdle;
- **reducing side:** never beyond p ∓ 0.5c (I-5);
- **in the middle (15-85c):** two-way at p ± `min_edge` (1c), with an inventory cap of about 2 quote sizes so the cash rotates.

Consequences:
- In the tails only favourite bids and longshot asks can rest, because the other side's floor sits far beyond the book.
- h = the allocator's current marginal edge x 0.7 (about 8% today). Tail inventory locks capital to the outcome, so it must beat what the
  allocator would buy with that $.
- Sizes: the quote size from fills (F-6: 500-1,000 in the top 30), times bloc headroom (1 - |bloc delta| / cap).

Data check: I_mmval, 3 Oct, maker fills valued at the outcome:

| Side | EV | Per share | Per $ |
|---|---|---|---|
| Tail value side | +1,948 | +5.1c | 5.6% |
| Tail against side | -1,920 | -4.7c | |
| Middle | +604 | +0.64c | |
| All days, toward-Polymarket fills | +16.7k | | |
| All days, away fills | -8.1k | | |

Upside: about +0.6-1.0k/day of outcome EV with a 15-20k reserve (middle 0.5-1.2k/day at 0.5-0.64c on F's volumes, tail value side when
it clears h) | P(>= 150k) effect: the largest no-ruin lever (I-6) | Ruin / drawdown: inventory news risk, about 0.5k/day sd; tail
inventory is bloc-capped.
End-valuation: Outcome. If marked, tail inventory marks against us by the tilt.
Legitimacy: Ordinary quoting.
Build: `value_mode`. With it on:
- `blend_fv` uses `ref_weight` 1.0 for liquid refs;
- a new `value_hurdle` (per $) in `compute_quote` (bid_hi = min(bid_hi, floor_tick(p/(1+h))) for the adding side when p is outside 15-85c);
- the per-market inventory cap `value_mid_max_inv`.

~40 lines.
Screen: live_sim judged on `pnl` (Polymarket mark = outcome EV), and the I_mmval split recomputed on 24 h of live fills.

### I-5. The no-sell-below-value guard
Mechanism: Covered in the audit table. In value mode no reducing order is placed below p - 0.5c (above p + 0.5c for shorts) unless the
allocator pairs it with a buy. The flatten window and the exit window are off.
Data check: I_closeout gives +1.1-2.5k of exit value given up, and up to 4.6k in the flatten window. The reduce-only ask can sit at
fv - 3c.
Upside: protects 1-5k, and the Senate sleeve's P(>=150k) | P(>= 150k) effect: a necessary condition for any sleeve | Ruin / drawdown: none.
End-valuation: **It depends on the rule.** If SIG marks unresolved positions at the close, the exit protects marks. Confirm with SIG
before 3 Nov.
Legitimacy: n/a.
Build: ~40 lines, as specified above. Settings: `value_mode`, `value_sell_margin`.
Screen: Unit tests (a reduce-only ask never < p - margin; exit skipped) plus the dry run.

### I-6. Funded value MM is the cleanest road to 150k
Mechanism: At the outcome, MM carry compounds additively with the value core without adding election variance.
Data check: I_carry (core (a), $20k reserve):

| MM outcome EV per day | P(>= 150k) | P(<= 85k) |
|---|---|---|
| 0.33k | 0% | |
| 0.67k | 6% | |
| 1.0k | 29% | 0.1% |
| 0.67k + Rep Senate $15k | 26% | 0.7% |
| 0.33k + Rep Senate $20k | 28% | 3.4% |

Upside: +10-30k over 30 days if F's 1.0-1.4k/day (1-h mark-out) holds at the outcome; outcome valuation of middle fills is 0.64c vs F's
0.35c | P(>= 150k) effect: 0-29 points | Ruin / drawdown: P(<= 85k) ~0.1%.
End-valuation: Outcome. If marked, the carry is cash.
Legitimacy: Ordinary market making.
Build: I-3 + I-4 + H-10's limits (backstop 1.3) + `capital_ceiling_adding_size_factor` > 0 within the reserve.
Screen: The gate is 24 h live at ≥ 0.6k/day of outcome EV (I_mmval on the next snapshot).

### I-7. Keep the Polymarket takes on in value mode (reverse F-1), but budget their writes
Mechanism: A take needs p ≥ 5c beyond the touch, so each one is a value buy. F-1 judged them on the tilt mark.
Data check: I_mmval. 188 take fills, 24.7k shares, **+1,862 at the outcome (7.5c/share, 8.5% per $)** on 3 Oct, against 2,691 attempts.
Upside: ~1.5-2k/day while cash exists | P(>= 150k) effect: +1-2 | Ruin / drawdown: none.
End-valuation: Outcome. If marked, the take is short the tilt (F-1's point).
Legitimacy: Trading at posted prices.
Build: Fold the takes into the allocator's ranking (a take is a high-edge buy); `take_edge` 0.05; `take_max_writes_frac` 0.25 (~15 lines).
Fix the 2,691 attempts → 256 orders waste: confirm on a fresh book before spending the cancel write.
Screen: The next snapshot's take outcome EV.

### I-8. Recycling as an allocator rule, not a strategy
Mechanism: When a holding's exit touch is within 2c of p and a paired buy has ≥ 5 points more edge, sell it and buy. This is I-1's rule
with `alloc_max_sell_edge` reached by convergence.
Data check: The I_recycle table: +1.6k probability-weighted on (a); ~0 with low dispersion; 3-4% at best.
Upside: +0-4k | P(>= 150k) effect: 0 | Ruin / drawdown: none.
End-valuation: Outcome.
Legitimacy: Trading.
Build: 0 extra lines (it is I-1).
Screen: The MC (done).

### I-9. Option 2, done right: Dem Senate short at 0.33 first, then Rep Senate YES at 0.35, ≤ $20k
Mechanism: Selling Dem U.S. Senate YES at its 0.67 bid locks 0.33 of collateral per share and pays 1 when the Dems do not control the
Senate. That is a cheaper ticket on the same event than Rep YES at 0.35: edge 7.6% vs 1.4% at Polymarket odds. Depth is 10.7k shares
(0.67/0.665/0.66, $3.6k).
- The seat count (I-11) says P(Rep) 0.41-0.51, so both carry +14-30% at the seat odds.
- Size to ≤ $20k: the cliff is at $20-22.5k.
- The Senate sleeve must be exempt from the flatten / exit windows (I-5).

Data check: I_senate. At $20k: P(>= 150k) 16% (PM) / 19% (seat); P(<= 85k) 5.0% / 4.7%; P(<= 70k) 2.3%; E 110.7k / 116.2k.
Upside: -2.4k (PM) to +3.1k (seat) of EV vs (a) | P(>= 150k) effect: 0 → 16-19% | Ruin / drawdown: P(<= 85k) 5%. P(top 100) 71% → 33-42%.
End-valuation: Outcome. If marked, it moves with the control market (symmetric).
Legitimacy: An ordinary directional position at a posted price.
Build: H-6's `sleeve_*` block (~60 lines) or manual orders. The sleeve is excluded from the allocator's sell list and from the bloc cap,
and counted in `settlement_risk` at its $ cost.
Screen: The MC (done).

### I-10. The calibration margin as the allocator's hurdle, not a re-optimisation
Mechanism: Choose at k 1.0, and require the edge to survive k 0.9.
- Value at k 0.9 is 1.3k lower on the book. The allocator's gain is +4.7k at k 0.9 and +3.9k at k 1.1: robust.
- The k < 1 risk sits in longshot YES buys, which become lottery tickets. So keep `alloc_min_buy_edge` 8% for p < 0.10 buys, or forbid
  YES buys under 0.10.

Data check: The I_alloc k sweep.
Upside: protects ±1k | P(>= 150k) effect: 0 | Ruin / drawdown: avoids H-9's P(<= 70k) 23% lottery book.
End-valuation: Outcome.
Legitimacy: n/a.
Build: `alloc_ref_margin`: edge computed at k 0.9 for YES buys under 0.10 and NO shorts over 0.90 (~10 lines).
Screen: Data-only (done).

### I-11. The Senate seat check: the control market is 5-15c rich on the Dem side under every variant tried
Mechanism: 34 listed races give E[Dem seats] 16.46; the Ohio special is unlisted. Dems hold 34 seats not up and need 17 of 35 (VP
Vance breaks a tie).

| Variant | P(Dem control) |
|---|---|
| Base (rho 0.45, Ohio 0.40, Alaska at its tournament mid 0.643 for lack of a reference) | 0.544 |
| Alaska 0.45 | 0.513 |
| Osborn caucusing with the Dems | 0.575 |
| rho 0.25-0.65 | ±0.02 |
| Needed to reach 0.645 | Ohio ≥ 0.6, Alaska ≥ 0.85, Osborn D, all together (0.638) |

Possible reasons the control market is right anyway:
- it prices a definition the races do not (check SIG's U.S. Senate contract text: "control" vs "majority of seats");
- the race refs are stale or illiquid in the competitive seats (Texas 0.63 D, Alaska no ref);
- partisan money concentrated in the control market.

Data check: I_senate (table printed).
Upside: decides whether I-9 is +EV | P(>= 150k) effect: +3 points between the two models | Ruin / drawdown: n/a.
End-valuation: Outcome.
Legitimacy: Relative-value analysis.
Build: Check Polymarket's own Ohio special, Alaska Senate and the SIG U.S. Senate rules text (manual, 10 min).
Screen: Data-only.

### I-12. The leaderboard has a 100k floor of 582 inactive accounts; any losing sleeve falls through it
Mechanism: Ranks are relative, and ~582 accounts sit at exactly 100k. An account that ends at 99k ranks ~790. One at 101k ranks ~120-170.
- Every plan whose losing branch ends below 100k has a bimodal rank.
- So for "top 100" the core (a) + MM dominates. For "top 50 or 150k" the sleeve buys a 23-32% shot, at the price of a ~60% chance of
  rank ~800.

Data check: I_senate ranks, H_board's field plus inactive_n. Median rank: (a) 85 → +$15k sleeve 766 (PM).
Upside: chooses the objective explicitly | P(>= 150k) effect: n/a | Ruin / drawdown: n/a.
End-valuation: Outcome. If marked, the tilt-long accounts sit on top.
Legitimacy: Analysis.
Build: None. The owner picks the objective.
Screen: Re-fit from the next "Rank N of M" lines.

### I-13. Turn off the election-night flatten and exit in value mode
Mechanism: Defaults flatten everything from 3 Nov 12:00 UTC and take bids down to fv - 3c from 22:00 UTC. At the outcome that is a
sale of value, and it kills any sleeve.
Data check: I_closeout. Exit: $103k sold, +2.5k given up if the gap is 25% of today's; $40k / +1.1k at 50%. Flatten window: up to
+4.6k.
Upside: +1-5k, and keeps option 2 alive | P(>= 150k) effect: necessary for I-9 | Ruin / drawdown: none at the outcome.
End-valuation: **Critical.** Only right if SIG really settles at the outcome. Under a mixed rule the exit is insurance.
Legitimacy: n/a.
Build: Config: `flatten_hours_before_close` 0, `flatten_per_market_hours` 0, `exit_hours_before_close` 0 (keep
`stop_minutes_before_close` 15). Plus I-5's code guard.
Screen: Unit test on `decide` with hrs = 1.

### I-14. Rotate within races first: the cheapest edge swaps are leg-for-leg
Mechanism: Several top buys are the other leg of a race we already hold (NH Governor both legs, TX Senate both legs, RI Senate). A swap
from a held leg to the other leg's rich level changes neither the race exposure nor the bloc delta. It is the risk-free part of the
rotation.
Data check: I_alloc. NH Governor offers buy Rep 0.84 (14.3%) and short Dem 0.155 (13.6%): $15.5k of the same bet. TX Senate offers short
Dem 0.67 and buy Rep 0.33 (11.7% each); we hold neither.
Upside: part of I-1 | P(>= 150k) effect: 0 | Ruin / drawdown: a race cap stops doubling.
End-valuation: Outcome.
Legitimacy: Trading.
Build: `alloc_max_race_usd` 15000 counts both legs (~10 lines).
Screen: Dry run.

### I-15. Hourly turnover cap and write share for the allocator, so market making is never starved
Mechanism: The allocator's writes come out of MM's 28/min. Capping it at 30% (~8/min) and $10k/h moves all of I-1 in 4 hours. MM keeps
70% of the writes, about 13/min, enough for the 60 markets that earn (F-5).
Data check: 82 moves ≈ 165 writes; at 8/min that is 21 min of writes, spread over 4 h.
Upside: protects MM's ~0.6-1k/day | P(>= 150k) effect: via I-6 | Ruin / drawdown: none.
End-valuation: n/a.
Legitimacy: Respecting the write limit.
Build: `alloc_writes_frac` 0.3, `alloc_turnover_per_hour` (~10 lines, reusing `writes_ready`).
Screen: live_sim writes_pm.

### I-16. Close the dead positions now, by hand: 14 lines, $850, +529
Mechanism: Rows 1-14 of the action list are NO on longshots that Polymarket prices at 0.1-2%, plus a few stray House legs. Their
liquidation value exceeds their outcome value (Rep SD Senate NO -97%, IL Governor NO -55%, Montana Senate Rep NO -36%).
Data check: I_alloc lowest-edge table.
Upside: +0.5k for about 28 writes | P(>= 150k) effect: 0 | Ruin / drawdown: none.
End-valuation: Outcome.
Legitimacy: Trading.
Build: Manual, or a one-shot allocator pass.
Screen: none needed.

### I-17. A kill switch on outcome value, not marks
Mechanism: The marks can fall 16k (tilt p95) plus a sleeve's mark with no loss at the outcome. The 70k mark kill would then stop market
making for no reason. Use liquidation EV at Polymarket (Σ pos x p + cash) with a 75k floor, and keep a 60k mark floor as a sanity
backstop.
Data check: H-10 / H_board arithmetic; status `liquidation_value` exists.
Upside: avoids a false stop | P(>= 150k) effect: protects I-6 | Ruin / drawdown: the EV floor is the true ruin measure.
End-valuation: Outcome.
Legitimacy: n/a.
Build: `kill_basis` "ev" | "marks", `kill_ev_floor` 75000 (~15 lines in `kill_switch`).
Screen: Unit test.

## TOP 5 (by P(>= 150k) per unit of ruin)
1. **I-5 + I-13: the no-sell-below-value guard, and the flatten / exit windows off.** Nothing else matters if the close-out sells the
   book. Config plus ~40 lines.
2. **I-3 + I-4 + I-6: the sets fund a $20k value-MM reserve.** Value quoting with a per-$ hurdle; gate on 24 h at ≥ 0.6k/day of outcome
   EV. P(>= 150k) 6-29% at P(<= 85k) ≈ 0.1%.
3. **I-1 + I-2 (+ I-8, I-14, I-15, I-16): the hourly paired allocator with the bloc delta.** +3.4-4.2k once, +1.6k recycling.
4. **I-7: keep the takes** (+1.9k/day at the outcome on 3 Oct), inside the allocator's write share.
5. **I-9 + I-11: the Senate sleeve, ≤ $20k, only after checking the contract text and the Ohio / Alaska references.** Size it with the
   MM's realised carry: a $10-15k sleeve plus MM reaches P(>= 150k) about 26% at P(<= 85k) under 1%.

## Assumptions I could not check
- **Snapshot age.** Every number uses the 22:47 books and refs. The 07:30 positions (party delta -13.8k), the tilt 0.14 and the value-mode
  quoting are not in the data. The journal's last hour (22:00-22:47) is tilt mode, not value mode:
  - **0 two-sided markets**, 49 bid-only, 42 ask-only, 24 none;
  - 77 reduce-only lines and 489 "Insufficient funds";
  - fills 6.1k shares, +76 at the outcome.

  **What value mode quotes today cannot be observed.**
- **fills.csv price convention.** 2,361 of 4,892 fills record the complementary (NO) price, where fill_price + quote_price = 1. I mapped
  them back by the quote price, or by today's mid when there is no quote price. A few early fills may still be mis-signed.
  Take/maker is matched from the TAKE lines (2 Oct 20:37 onward only).
- **Outcome value of fills uses today's Polymarket.** Later news moves are included as if they were edge.
- **Depth and own quotes.** The 3 book levels include our own resting orders (46 at 22:47). The rotation assumes those levels are still
  there an hour later.
- **Set collateral.** I assume closing one leg of a NO+NO set frees 1 - ask. The exchange's set collateral rule may free less (Package 8
  "set part breaks sets").
- **Recycling model.** A stylised world: B_mc's tilt quantiles, H's ±50% per-contract dispersion (the result is very sensitive to it),
  the book refilling daily at its 22:47 shape. The paths were not fitted to anything after 3 Oct.
- **MM carry.** I_carry's carry is a normal draw independent of the election. F's volumes, at the outcome's 0.5-0.64c, are not
  re-measured with value-mode quoting.
- **Senate.** The control contract's exact definition, the Ohio special (0.30-0.50 assumed), Alaska (no reference), Osborn's caucus,
  and whether a rival account can move the 0.35 ask (185k shares deep).
- **The leaderboard.** H's field mix, plus my assumption that the 582 inactive accounts stay at exactly 100k and are ranked.
- **SIG's rule.** All of this assumes payout at the outcome. The flatten / exit change (I-13) must not ship until that is confirmed.
