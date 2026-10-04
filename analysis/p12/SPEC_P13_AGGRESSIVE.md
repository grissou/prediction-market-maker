# SPEC Package 13: AGGRESSIVE VALUE (owner, 4 Oct 17:15 UTC: "work on going aggressive to make as much money as possible from this
# further tilt; ignore risk limits; commit to what we think is most likely")
What we think is most likely (analysis/p12/FIELD_ACTIVATION.md): the favourite-longshot tilt keeps growing for weeks (sub-5c longshots
2.4c -> 9.9c in 6 days, +2.5c/day; 80% of the field has not traded yet; nothing forces convergence before the close). SIG pays at the
OUTCOME, so the money from a further tilt is made by SELLING it at ever better prices with every dollar of collateral: short longshots
(YES asks above the market, the tilt lifts them to us), buy favourites (YES bids below the market), no caps, all collateral, and let the
tilt's growth fill us at rising edge. At the outcome a 10c longshot short pays ~+8c on 0.90 of collateral (~9%/$), a 20c one +18c on
0.80 (~22%/$); a favourite bought at 0.85 (p 0.975) pays +12.5c on 0.85 (~15%/$). Risk that remains: correlated election outcomes (a wave
flips many favourites); safe seats (p >= 0.95 / <= 0.05) flip rarely even in a wave, so the aggressive book concentrates in the tails.

## A. `aggressive_value` (False) — the master switch that removes the caps the owner wants ignored (document each loudly)
With it on: (1) the settlement-risk cap (`max_worst_case_frac`) and the sum-of-maxima backstop (`worst_case_backstop_frac`) never put the
bot into reduce-only (global_reduce from risk = False; the figures are still computed and reported); (2) the bloc-delta cap and the
share party cap do not block adding; (3) per-market caps (`kelly_max_market_frac`, `max_position_frac`, `headline_position_frac`,
`max_order_cash_frac`) are replaced by `aggr_max_market_usd` 25000 (0-100000) of collateral per market and `aggr_max_race_usd` 30000;
(4) the capital ceiling (`capital_in_positions_max_frac`) does not shrink adding sizes; (5) the cash gate still applies (cash is real);
(6) `value_mode`'s floor still applies (never sell value below p); (7) the pre-close windows stay inert; (8) the kill switch
`max_drawdown_pct` is NOT touched (code default; a mark drawdown of 30% means something is very wrong). status.json `aggressive` {on,
caps_ignored: [...], worst_case_would_reduce, risk_would_reduce}; a WARNING every 2-hourly summary while on.

## B. `tilt_harvest_ladder` (False) — the maker ladder that sells the rising tilt (generalises the P12 set ladder to any market)
For every market with a liquid race-scaled p: LONGSHOT side (p <= `harvest_max_p` 0.10): rest YES ASKS at best_ask + `harvest_offsets`
(0.00, 0.02, 0.04, 0.06 default; the 0.00 level = join the ask), each level `harvest_level_usd` 2000 (0-20000) of collateral (the short
locks (1 - price) per share), only where (ask - p)/(1 - ask) >= `harvest_min_edge` 0.08; FAVOURITE side (p >= 1 - harvest_max_p): rest YES
BIDS at best_bid - offsets, same sizing, edge (p - bid)/bid >= harvest_min_edge. Caps: `harvest_total_usd` 60000 of collateral across all
ladders, `aggr_max_market_usd` / `aggr_max_race_usd` per market/race, the cash gate (bids need cash; asks beyond YES held need 1 - price),
`harvest_max_markets` 60 ranked by edge per $; the ladder never crosses our own quotes or the book (asks >= best_ask, bids <= best_bid);
re-quoted at most every `harvest_requote_s` 1800 (queue position matters) unless a level is > 2c off its target or p moved > 1c; writes:
<= `harvest_writes_frac` 0.4 of the budget. The ordinary market maker does not quote the adding side on a laddered market (the ladder IS
the adding side); reducing sides unchanged. Fills are booked as ordinary inventory (value positions, held to the outcome). status
`harvest` {markets, levels_resting, collateral_resting, filled_24h, edge_filled_24h}; journal "HARVEST fill <label> <side> <qty> @ <px>
(p <p>, edge <e>%)".

## C. Allocator in aggressive mode (settings only, no code unless needed): `alloc_mm_reserve` 0, `alloc_min_edge_buy` 0.08,
`alloc_max_edge_sell` 0.04, `alloc_max_contract_usd` 25000, `alloc_max_turnover_per_hour` 50000, `alloc_prefer_short` on. Check that the
allocator respects `aggressive_value`'s per-market/race caps instead of the Kelly caps when on (small code if not).

## D. Staged file deploy/package13/settings_override.aggressive.json = the live file (/home/claude/snap04/settings_override.json, with
worst_case_backstop_frac 0.9) + {aggressive_value true, tilt_harvest_ladder true, the C settings, mm_risk_reserve_* 0, max_worst_case_frac
0.60, worst_case_backstop_frac 1.5, max_bloc_delta_frac 0.5} — validate it; and a README paragraph with the EXPECTED consequences: all
collateral deployed within days; P(final < 100k) rises (state the model's number from Y_texas-style runs if cheap: the aggressive book's
E, P(>= 120k), P(< 100k), P(<= 85k) with rho 0.55 — compute it on the 15:56 state with the ladders assumed filled at their levels).

## Tests: tests/test_aggressive.py (>= 60): flags off byte-identical on a grid (quotes, reduce-only decision, caps, status keys);
aggressive_value: no reduce-only from risk, caps replaced, cash gate and value floor still bind, status/summary warning; ladder: level
prices and sizes, edge filter, caps (per market / race / total / markets / writes), never crosses, re-quote rules, cash-gated bids, booking
of fills, interplay with the market maker's adding side, the set ladder and the allocator (no two features adding on one market in a
cycle), pre-close inert; validate_overrides ranges. Dry run: extend tests/test_p10_dryrun.py's harness in a new tests/test_p13_dryrun.py
(>= 20 checks) on /home/claude/snap04: apply the aggressive file, run 2 h of cycles, report what rests (ladders per market, collateral),
what fills under a simulated +3c tilt rise, the EV added, and that nothing sells below value.
