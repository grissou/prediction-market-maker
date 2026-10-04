# SPEC Package 13 — the 20 / 40 / 40 AGGRESSIVE portfolio (owner, 4 Oct 18:40 + answers 18:55 UTC; replaces every earlier sleeve /
# Package 13 message). "No modelling, no comparisons: aggression." Everything behind its own setting, OFF by default; flags off =
# today's behaviour (pinned byte-identical on a grid); one staged file with everything on.
Facts: SIG pays at the OUTCOME; SIG confirmed by e-mail that orders are accepted until 12:00 ET (17:00 UTC) on 4 Nov and that trading
while public results come in is allowed. Live: c7c0107 (Package 12 stage 1 + risk-reserve code), `max_worst_case_frac` 0.40, backstop 0.90,
`alloc_mm_reserve` 20000. Data: /home/claude/snap04 (ops-snapshot-2026-10-04). Code base: cb493c2 / ae6c606 on claude/finisher-package9.
Limits that STAY (non-negotiable): the cash gate; the value floor (for VALUE positions: never sell below p - margin / buy back above);
a worst-case backstop as a bug TRIPWIRE at 1.0; the 30% drawdown kill switch; per-race 15k / per-market 12k collateral caps (all buckets
combined). Everything else goes to maximum.

## 1. Buckets (`buckets_enabled` False): MM 20% / VALUE 40% / MOMENTUM 40% of the account, rebalanced hourly within +-5 points
- Bucket value = collateral: MM = `alloc_mm_reserve` cash (20k) + middle-band inventory at p; VALUE = value positions' collateral (longs
  at p x q, shorts at (1 - p) x |q|) of markets tagged value; MOMENTUM = the sleeve's collateral at p (longshot YES at p, favourite NO at
  1 - p) PLUS its unrealised mark gain? No: at collateral cost (what was paid). Targets `bucket_mm_frac` 0.20, `bucket_value_frac` 0.40,
  `bucket_mom_frac` 0.40 (0-1 each), band `bucket_band` 0.05. status.json "buckets" {mm, value, momentum, cash, targets, deviations,
  last_rebalance, actions}.
- Rebalance (hourly, inside the allocator's run): if VALUE > target + band: SELL value positions down, lowest edge-held first, as IOC
  takers at the best bid (answer 1: this sell-down is EXEMPT from the value floor; flag `_bucket_sell`), until within the band, up to
  `bucket_turnover_per_hour` 50000; if MOMENTUM < target - band and cash is available above the MM reserve: BUY momentum (section 3) as
  fast as the books allow (no pace cap; per-cycle orders limited only by the write budget share `bucket_writes_frac` 0.6); if MM cash <
  reserve: refill from value sales (as today). Never re-buy momentum after `momentum_exit` (section 3).
- Classification of markets (answer 2; the bot decides): rank every longshot leg (race-scaled liquid p <= `mom_max_p` 0.25) by edge per $
  of a SHORT = (ask - p)/(1 - ask) and by extremeness (low p). VALUE shorts = the most extreme underdogs and the largest gaps: legs with
  p <= `value_extreme_p` 0.04 OR edge per $ >= `value_min_edge` 0.12 (the top of the list) — these are held/added as value (shorts,
  the longshot-NO legs) and never bought as momentum. MOMENTUM longs = the remaining longshots with 0.04 < p <= 0.25 whose gap is below
  the value threshold (the tilt still has room to grow there): longshot YES (or favourite NO in 2-leg races when cheaper per unit of
  payoff). Favourites: VALUE longs where (p - ask)/ask >= `value_min_edge_fav` 0.08. A market is never in two buckets; never the
  opposite side of an existing position (a momentum buy never closes a value short).

## 2. `aggressive_value` (False): the caps the owner wants ignored
With it: no reduce-only from the settlement-risk cap or the sum-of-maxima backstop EXCEPT the tripwire: backstop at `worst_case_backstop_frac`
1.0 still forces reduce-only (bug tripwire: worst case above the whole account); the bloc-delta and share party caps do not block adding;
`kelly_max_market_frac` / `max_position_frac` / `headline_position_frac` / `max_order_cash_frac` / the capital ceiling's adding factor are
replaced by `aggr_max_market_usd` 12000 and `aggr_max_race_usd` 15000 of collateral (all buckets combined); the cash gate, the value floor
(value positions only), `value_mode`'s pre-close inertness and the kill switch stay. status "aggressive" {on, caps_ignored, would_reduce_corr,
would_reduce_backstop}; a WARNING in every 2-hourly summary.

## 3. Momentum sleeve (`momentum_enabled` False): long the tilt; the flip is NOT modelled yet (answer 3)
- Buys: per section 1's classification, as IOC takers at the best ask (longshot YES) or best bid (favourite NO = short YES, covered rules
  as today), largest sizes the caps permit, as fast as books allow; `mom_min_depth` 200 shares at the level; never a market where we hold
  the opposite side; never headline races unless `mom_headline` False; `mom_max_markets` 40.
- Hold. NO automatic flip: `momentum_exit` (False) is the owner's manual switch: when set true the sleeve is sold into the bids as IOC
  takers over `mom_exit_hours` 6 (exempt from the value floor: these are momentum positions), and from then on the sleeve is never
  re-bought; after the exit the HARVEST LADDER (section 4) takes over on those markets. Also `mom_exit_utc` ("" = none): an optional
  date switch the owner may set later. (Slope / profit-target triggers: NOT built; leave hooks/comments.)
- Kill-switch: when the sleeve's value at the MARK (mid) falls below `mom_kill_frac` 0.75 of its cost for 120 s: exit as above, latch
  off (`momentum_enabled` stays true but the sleeve state is "killed"; reset only by false-then-true), alert. status "momentum"
  {state, cost, value_mark, value_outcome (at p), markets, legs, kill_level, exit_progress}.

## 4. Harvest ladder (`tilt_harvest_ladder` False): sell the tilt as a maker at full size wherever the edge clears 8% per $
For every market with a liquid race-scaled p that is NOT a momentum market (while the sleeve holds it) and not already a value position at
its cap: LONGSHOT side (p <= 0.10): rest YES ASKS at best_ask + `harvest_offsets` (0.00, 0.02, 0.04, 0.06), each level `harvest_level_usd`
3000 of collateral, where (ask - p)/(1 - ask) >= `harvest_min_edge` 0.08; FAVOURITE side (p >= 0.90): YES BIDS at best_bid - offsets, edge
(p - bid)/bid >= 0.08. Caps: `aggr_max_market_usd` / `aggr_max_race_usd`; the cash gate; `harvest_max_markets` 237 (every market that
clears); writes <= `harvest_writes_frac` 0.4; never crosses the book or our own orders; refreshed promptly: re-quoted when a level is
> 1c off its target or p moved > 1c, else at most every `harvest_requote_s` 900 (queue position). Reuse the P12 set ladder's resting-order
machinery (hidden from the quote planner via the plan_exchange wrapper, order_meta tags, pulls). Fills are value positions (held to the
outcome; the value floor protects them). The ordinary market maker does not quote the adding side on a laddered market. status "harvest"
{markets, levels_resting, collateral_resting, filled_24h, edge_filled_24h}; journal "HARVEST fill ...".

## 5. Election-night mode (`election_night` False; SIG confirmed): trade past 23:45 UTC on 3 Nov until 17:00 UTC on 4 Nov
- `close_override_utc` "2026-11-04T17:00:00Z" and `stop_minutes_before_close` 5 are in the staged file (built in P12).
- Holdback (answer 4): `election_holdback_usd` 20000 kept in CASH by the allocator from `election_holdback_from_utc` "2026-11-03T12:00:00Z"
  (sourced from the MM reserve and, if the sleeve was exited, its proceeds); before that date it is not reserved.
- From `election_start_utc` "2026-11-03T23:00:00Z": a race counts as CALLED when its liquid Polymarket price has been >= `election_called_p`
  0.98 (or <= 0.02) for `election_called_min` 10 minutes, or the Polymarket market is resolved (reference 1.0 / 0.0 if the feed shows it).
  On called races only: TAKE tournament quotes stale against the call (buy YES below `election_take_max_price` 0.95 on the winner / sell
  YES above 0.05 on the loser as covered sales where we hold NO, else shorts), IOC, cash-gated, up to the per-race / per-market caps,
  spending the whole holdback; takes batched 10 per write (reuse the batch path) so the budget allows 50-100 takes a minute; the tilt
  correction is off anyway (value mode). No quoting changes otherwise. status "election" {active, called_races, takes, cash_spent}.

## 6. Tests (tests/test_p13.py >= 80) and a dry run (tests/test_p13_dryrun.py >= 25 on /home/claude/snap04 with the staged file applied:
2 h of cycles; buckets converging toward 20/40/40 (what is sold, what is bought, collateral per bucket per hour); the ladder resting per
market; a simulated +3c tilt rise (fills, EV); the momentum kill path with a simulated -30% mark move; a simulated election night (clock
moved to 3 Nov 23:30 UTC, a few Polymarket references set to 0.99 / 0.01 for 10 min: the takes fire only on those, within the holdback and
caps); flags off byte-identical to the base). House style, Python 3.10, every setting in OVERRIDABLE with a range.
## 7. Deliver: ONE staged file deploy/package13/settings_override.aggressive.json = the live file (/home/claude/snap04/settings_override.json
with worst_case_backstop_frac 1.0, max_worst_case_frac 0.60, max_bloc_delta_frac 0.5, alloc_mm_reserve 20000, mm_risk_reserve_wc 5000,
mm_risk_reserve_corr 4000) + every Package 13 flag on (buckets, aggressive_value, momentum, harvest ladder, election_night, close override,
stop 5) — validated; a README; a START_HERE note (what it does, the flip = manual `momentum_exit`, what to watch in the first hour).
