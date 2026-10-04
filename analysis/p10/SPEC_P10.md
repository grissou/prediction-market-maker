# SPEC Package 10 (value mode; everything OFF by default, in OVERRIDABLE with ranges; house comment style; tests fail-before/pass-after)
Read PLAN_P10.md, analysis/p10/ideas_H.md (H-2 bloc delta, risk limits), analysis/p10/ideas_I.md (I-1 allocator, I-2, I-3 sets, I-4 quoting
rule, I-5/I-11 the panic-sell audit and guard, the "what binds" list). SIG settles at the OUTCOME: a position's value is its Polymarket
probability p (race-scaled); selling below p (long) or buying back above p (short) destroys value unless the cash buys more edge.
Live base = Package 8 in value mode (`ref_tilt_enabled`, tilt exits, `ref_guard_*`, `take_tilted_ref` off; `pair_no_unwind_max_cost`
0.003; cash gate on; adding factor 0; arb off; backstop 0.8; and, recommended today, `ref_weight` 1.0, `skew_max` 0, `skew_age_enabled` false).

## Part A (builder J): the value-mode guard, bloc-delta risk, close windows, value quoting rule — Config block "# --- Package 10 A ---"
A1 `value_mode` (False): the master guard. With it: (i) in `compute_quote` the REDUCING side is never priced to give up value: a reducing
   ask (long) >= p - `value_sell_margin` (0.005, range 0-0.05), a reducing bid (short) <= p + margin, in normal AND reduce-only quoting
   (the skew clamp is skipped in reduce-only today: ~l.2709; fix that too under value_mode); where p = the race-scaled raw Polymarket
   reference when liquid, else the quote is unchanged; (ii) the pre-close branches `exit_hours_before_close` (~l.5250, ~l.5707) and
   `flatten_hours_before_close` / `flatten_per_market_hours` (~l.5305, ~l.5720) do nothing (no forced exit, no flatten reduce-only) —
   and make those three settings OVERRIDABLE anyway (ranges 0-48 / 0-48 / 0-12) so the owner can zero them; (iii) `exit_quote` floored
   the same way; (iv) `reduce_from_book`, `fast_unload_enabled`, `hold_target_hours` > 0, `tilt_exit_*`, `ref_tilt_enabled`,
   `take_tilted_ref`, `ref_guard_exits` are logged as WARNINGS at start-up / override time when on together with value_mode (not forced
   off: the owner decides) — except the pre-close exits, which value_mode disables outright. (v) Allocator sells (Part B) are exempt
   from (i) only when flagged `_alloc_paired`.
A2 `bloc_delta_enabled` (False): H-2's closed-form bloc delta per sd of the national factor: for every partisan contract (label starts
   "Dem " or "Rep "; independents excluded) with a liquid p: sensitivity per YES share = sqrt(rho) x phi(Phi^-1(p_dem)) with sign + for
   Dem YES / - for Rep YES (`bloc_rho` 0.45, range 0.1-0.9; House/Senate control markets `bloc_rho_control` 0.85); bloc_delta = sum pos x
   sensitivity ($ per sd). With the flag, the party cap used by `party_blocks` / `party_shift` (~l.5421-5440) is |bloc_delta| <=
   `max_bloc_delta_frac` 0.05 x account (range 0.01-0.5) instead of the share-count party delta; status.json `bloc_delta`,
   `bloc_delta_frac`; the 2-hourly summary " | bloc delta X/sd". Unit test: closed form vs a small Monte Carlo within 3% on a toy book.
A3 Ranges for value mode: `worst_case_backstop_frac` range extended to (0.3, 1.5) (1.5 = effectively off; document H's reasoning:
   the sum-of-maxima protects against every race failing at once); `max_worst_case_frac` range to (0.05, 0.60) is already there; the
   kill switch fraction setting (find it: `kill_switch`) range checked so 0.40 is allowed.
A4 `value_quote_hurdle` (0.0 = off; range 0-0.5): I-4's quoting rule for the ADDING side: a YES bid never above p / (1 + h) and a YES ask
   (adding a short) never below 1 - (1 - p) / (1 + h), h = the hurdle (per $ of collateral to the outcome); in the middle band
   (`value_mid_low` 0.15 .. `value_mid_high` 0.85 on p) the normal `min_edge` rule applies instead and the position is capped at
   `value_mid_inventory_quotes` 2 x the quote size (beyond it only the reducing side rests). Document the consequence: in the tails only
   favourite bids and longshot asks can rest. Tests: tails one-sided, middle two-way, caps, flag off identical.
Tests: tests/test_value_mode.py (>= 60 checks): flags off byte-identical on a grid (quotes, reduce-only quotes, pre-close behaviour,
party blocks, status keys) vs the branch head; the guard in normal and reduce-only quoting with skew and age skew; pre-close branches
inert; exit_quote floor; warnings; bloc delta math and the cap replacing the share cap; ranges; A4 rule.

## Part B (builder K): the capital allocator with the market-making reserve — Config block "# --- Package 10 B ---"
B1 `alloc_enabled` (False): once per `alloc_interval_s` 3600 (range 300-86400), on a cycle with a fresh cash read and books: rank every
   HOLDING by edge-held (long: (p - bid)/bid; short: (ask - p)/(1 - ask); NO+NO set legs by the pair's cost per $ freed, see B3) and
   every book level (top 3, own quotes stripped, liquid p <= 30 s old, race-scaled) by edge per $ of collateral (buy YES: (p - ask)/ask;
   sell YES (short): (bid - p)/(1 - bid)); pair the lowest-edge holdings with the highest-edge levels while improvement >=
   `alloc_min_improvement` 0.03 (range 0.005-0.5, per $), buys only at edge >= `alloc_min_edge_buy` 0.05 (range 0-0.5), sells only
   holdings with edge-held <= `alloc_max_edge_sell` 0.02 (range 0-0.5), never a holding whose label is in `alloc_pin` (a comma-separated
   string of labels, default ""), never basket legs if any, never headline markets unless `alloc_headline` (False). Turnover <=
   `alloc_max_turnover_per_hour` 15000 $ (range 0-200000); orders <= `alloc_max_orders_per_cycle` 4; writes <= `alloc_writes_frac` 0.3.
   SELL FIRST as an IOC taker at the touch (flag `_alloc_paired` so A1's guard lets it through), READ the cash (next cycle's P&L read /
   cash gate), THEN BUY as an IOC taker at the touch, only if a fresh book still shows the paired level within 0.5c; if the level is
   gone the cash stays in the reserve and no more sells that hour. Bloc check: a pair is skipped if it would push |bloc_delta| past the
   cap (use A2's function if present on the bot: `getattr(self, "bloc_delta_now", None)`; else skip the check and log once).
B2 `alloc_mm_reserve` 15000 $ (range 0-100000): cash the allocator keeps free for market making; buys only above it; when cash < reserve
   the allocator may sell the lowest-edge holdings (edge-held <= alloc_max_edge_sell) to refill it, at most `alloc_max_turnover_per_hour`.
B3 `alloc_set_cost_per_usd` 0.0 (off; range 0-0.2): a NO+NO set race may be unwound (through the existing pair unwind: `arb_plan` /
   `execute_arbitrage` with the covered-sale legs, or by registering it for `pair_no_unwind` with a temporary cost) when its cost per $
   freed (asks sum - 1) / (1 - longshot ask ...; compute: cash freed per set = 1 - cost, cost = asks sum - 1) is <= this AND the reserve
   or a paired buy needs the cash. Reuse the pair-unwind plumbing; do not duplicate it. If this is too entangled in the time, make the
   allocator only RAISE `pair_no_unwind_max_cost` temporarily (documented) — or leave B3 out and say so.
B4 Bookkeeping: status.json "alloc" {state, last_run, pairs_planned, sold, bought, cash_before/after, ev_gain_est, blocked_by (bloc /
   cash / writes / depth), reserve}; journal "ALLOC sell <label> <qty> @ <px> (edge-held x%) -> buy <label> <qty> @ <px> (edge y%)".
Tests: tests/test_alloc.py (>= 60 checks): flag off identical; ranking math (long/short edge, race scaling, own quotes stripped, stale
p skipped); pairing and thresholds; pin list; headline exclusion; sell-then-read-then-buy sequencing across cycles; the buy skipped
when the level is gone; reserve logic; turnover / orders / writes caps; bloc check hook; IOC and leftover cancel; cash gate interplay
(a buy needs cash: use the gate's need); flags-off identity; validate_overrides ranges; py_compile 3.10.
Deliver: code, tests, a 10-line summary with counts and what was left out.
