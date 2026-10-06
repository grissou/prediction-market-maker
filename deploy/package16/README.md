# Package 16: the ARMED momentum sleeve, ported onto Package 15 (owner, 6 Oct 11:00 UTC)
Branch `claude/package16`, built on **ed65638** (Package 15 READY on `claude/mm-funding` = the live 64f27c8 + the harvest
ladder alone + the state cap). Ported from Package 13 (`claude/finisher-package9` 1fdd467): Package 13 A's momentum
sleeve and Package 13 C's automation (the TiltSlope tracker, the trigger, the ramp, the funding order, the automatic flip,
rule 5). Every new setting is OFF by default; with the flags off the code is pinned byte-identical to ed65638 on a grid
with fills, the Package 15 ladder and state cap on in part of it (orders, quotes, order notes, status.json, health,
summary line, alloc_plan: `tests/test_p16.py`) and on the live state (`tests/test_p16_dryrun.py` scenario 0). Status key
added only while on: `Bot.MOMENTUM_KEYS` = `momentum`.

## The owner's brief (verbatim)
"Package 16 (after 15): the armed momentum sleeve, ported onto Package 15. From Package 13: `momentum_auto` armed;
trigger = 24-h tilt slope >= `momentum_on_slope` (~+0.5 pt/day) AND 6-h slope > 0, held >= 4 h, measured from other
traders' prices only; ramp 10k at a time to <= 40k while it keeps rising; flip into the harvest ladder when the 24-h slope
<= 0, at the profit target, or by the date; kill-switch at 75% of cost. Funding order: free cash above any election-night
holdback -> the MM reserve only if `mm_carry_24h` < 300/day -> value positions lowest edge first, never below the floor.
The one trading rule: no fake buying. Tests, dry run, staged file with `momentum_auto` true (sleeve otherwise off until
triggered), READY as 'Package 16'."

**The owner asked for the sleeve to be TRIGGERED, never forced** (analysis/p15/TILT_PATHS.md 4.2: the long-tilt bet is
marginal, +7% expected with a 35% chance of losing a third or more; only the armed, triggered, 10k-step version with an
automatic flip is the bet he wants). `momentum_force` exists only as a manual override and is false in the staged file.

## Files
- `settings_override.momentum_armed.json` = `deploy/package15/settings_override.harvest.json` (unchanged: the ladder, the
  15k state cap, `capital_ceiling_adding_size_factor` 0.5, `arb_enabled` false, `ref_tilt_headline` true kept) +
  `momentum_enabled` true (the machinery) + `momentum_auto` true + `momentum_force` false + the Package 13 C defaults
  written out: `momentum_slope_hours` 24, `momentum_slope_bin_h` 4, `momentum_slope_max_spread` 0.06,
  `momentum_on_slope` 0.5, `momentum_confirm_h` 4, `momentum_start_usd` 10000, `momentum_step_usd` 10000,
  `momentum_step_h` 6, `momentum_max_usd` 40000, `mm_carry_min` 300, `momentum_profit_target` 0.25, `mom_kill_frac` 0.75,
  `mom_exit_hours` 6, `momentum_fund_value` true, `momentum_rearm_h` 24. Validated with `mm_bot.validate_overrides`: no
  problems (88 keys). If your live file changed since Package 15 in anything else, carry it over.
- Rollback file: `deploy/package15/settings_override.harvest.json`.

## The switches
| you want | set |
|---|---|
| the sleeve OFF (no more buys from the trigger; legs held) | `momentum_auto` false (one ALERT if it was on) |
| the sleeve ON now, no trigger (manual override - discouraged) | `momentum_force` true (buys to `momentum_max_usd` at once; the slope flip is off while forced) |
| SELL the sleeve | `momentum_exit` true (into the bids over `mom_exit_hours` 6; set it back to false afterwards: it re-arms after 24 h of a fresh trigger) |
| start over after a kill | `momentum_enabled` false, wait one cycle, then true |
| an exit date | `mom_exit_utc` "2026-11-0xT..Z" (an automatic flip at that time) |
| keep cash for election night out of the sleeve's funding | `election_holdback_usd` (0-100000) from `election_holdback_from_utc` (default 3 Nov 12:00 UTC; "" = from now) |

## The settings (Config block "# --- P16", OVERRIDABLE with ranges; all OFF / inert by default)
| setting | default | range | staged |
|---|---|---|---|
| `momentum_enabled` | false | bool | true |
| `momentum_auto` | false | bool | **true** |
| `momentum_force` | false | bool | false |
| `momentum_exit` | false | bool | (not set) |
| `momentum_max_usd` | 40000 | 0-200000 | 40000 |
| `mom_exit_hours` / `mom_exit_utc` / `mom_kill_frac` | 6 / "" / 0.75 | 0.25-72 / date or "" / 0.3-0.99 | 6 / - / 0.75 |
| `mom_min_depth` / `mom_headline` / `mom_max_markets` | 200 / false / 40 | 0-100000 / bool / 1-300 | - |
| `momentum_writes_frac` | 0.6 | 0-1 | - |
| `mom_max_p` / `value_extreme_p` / `value_min_edge` / `value_min_edge_fav` | 0.25 / 0.04 / 0.12 / 0.08 | 0.01-0.5 / 0-0.25 / 0-2 / 0-2 | - |
| `momentum_slope_hours` / `_bin_h` / `_max_spread` | 24 / 4 / 0.06 | 6-72 / 1-12 / 0.01-0.2 | 24 / 4 / 0.06 |
| `momentum_on_slope` / `momentum_confirm_h` | 0.5 / 4 | 0-10 / 0-48 | 0.5 / 4 |
| `momentum_start_usd` / `_step_usd` / `_step_h` | 10000 / 10000 / 6 | 0-100000 / 0-100000 / 1-48 | same |
| `mm_carry_min` | 300 | 0-10000 | 300 |
| `momentum_profit_target` | 0.25 | 0-5 | 0.25 |
| `momentum_fund_value` | true | bool | true |
| `momentum_rearm_h` | 24 | 0-168 | 24 |
| `election_holdback_usd` / `election_holdback_from_utc` | 0 / "2026-11-03T12:00:00Z" | 0-100000 / date or "" | - |

## What it does
- **The tilt series** (`TiltSlope`, as Package 13 C): every minute the cross-section of NON-headline markets with a
  liquid Polymarket price and an OTHER-traders book spread <= 6c (our own orders stripped from every book, never our
  fills): the "slope" estimator per 4-h bin. Seeded on a cold start from market_data.sqlite (a background thread, its
  own read-only connection), kept 72 h in status.json (a restart restores it, never re-seeds). slope_24h = the regression
  of the bins over 24 h, slope_6h = the latest bin vs the bin ~6 h before; points a day.
- **Trigger** (armed -> on): slope_24h >= 0.5 AND slope_6h > 0 held continuously 4 h (a dip restarts the clock).
  "MOMENTUM eval: <state> - <reason> | slope24 ..., slope6 ..." every 10 min and at each change; ALERT on switch-on.
- **Ramp**: 10k at switch-on, +10k per further 6 h of slope_6h > 0 throughout (a slope_6h <= 0 stalls it), up to
  `momentum_max_usd` 40k.
- **Buys** (the sleeve, Bot.mom_tick, cycle step 6a' - FIRST among the traders, before the takes and the allocator):
  the MOMENTUM longs - longshots with 0.04 < p <= 0.25 whose short edge per $ is below 12% - YES at the best ask, or in a
  2-leg race the favourite's NO (sell YES at its best bid) when cheaper; smallest gap (ask - p) first; immediate-or-cancel
  after our own orders there are cancelled; per order min(the touch level >= 200 shares, the target left, the funding,
  the market's cap `alloc_max_contract_usd` at max(price, p), the state cap at p). Never a headline race, never a market
  holding a position outside the sleeve or its opposite side, at most 40 markets, momentum_writes_frac 0.6 of the writes
  left. The sleeve's legs are never quoted, never traded by takes / tilt exits / hold takes / arbitrage / pair unwinds /
  the allocator, and the harvest ladder leaves their WHOLE race while they are held.
- **Funding, in order**: (a) free cash above what is kept back = the MM's effective reserve (`mm_reserve_effective`,
  10k) + the ladder's carve-out not resting yet (`harvest_total_usd` - resting) + the election holdback (0 now). I.e.
  free cash + the ladder's resting carve-out above `alloc_mm_reserve` (20k) + the holdback. (b) The MM's effective
  reserve too while `mm_carry_24h.per_day` < 300 (unknown = not used); never the ladder's carve-out, never the holdback.
  (c) `momentum_fund_value`: VALUE positions sold lowest edge-held first through the allocator's refill sale path
  (`alloc_plan` refill-only with the shortfall as the deficit, sold by `alloc_sell` in the same cycle's `alloc_tick`),
  within `alloc_max_turnover_per_hour`, **never below the value floor** (p - `value_sell_margin` for a long, p + it for a
  short bought back; no exemption), never a middle-band (MM) holding, an edge-rich one (> `alloc_max_edge_sell`), a
  pinned / headline / basket / sleeve market; refused markets are not planned again for an hour; the proceeds are
  earmarked for the sleeve's next buys. `momentum.funded_from` {cash, reserve, value_sales, value_sales_spent},
  `ev_given_up` (shares x (p - price) of the funding sales) and `ev_given_up_per_10k`.
- **While a buy round is short and there is cash to protect** (cash (a) / (b) lets the sleeve spend now, or funding sales
  pending / proceeds unspent): the allocator's spare-cash buys pause (blocked_by "momentum"), the stale-quote takes keep
  it back (`take_respect_reserve`), the harvest ladder places no NEW level (it keeps its resting ones;
  `harvest.paused_for_momentum`) and yields the candidates' races, and the quotes' plan budget leaves the sleeve's
  kept-back cash + what it can spend. A round the sleeve cannot fund holds nothing back (`momentum.short_usd` shows it,
  `hold_usd` 0).
- **Flip** (automatic): slope_24h <= 0, the mark >= 1.25 x cost for 120 s, `mom_exit_utc`, the manual `momentum_exit`,
  or the kill -> the sleeve's YES sold into the bids over 6 h (EXEMPT from the value floor: these are momentum positions
  bought above p, not value); each market is laddered by the harvest ladder once its leg is sold. A flipped sleeve re-arms
  after 24 h of a fresh trigger once sold.
- **Kill**: the sleeve at the MARK (book mid) < 0.75 x cost for 120 s -> the exit, latched "killed", one ALERT; reset
  only by `momentum_enabled` false then true.
- **No fake buying (rule 5)**: never buys a market (or race) where we rest or plan a sale - harvest levels in the race,
  a refill / swap / funding sale pending there, another feature's resting order; our plain quotes there are cancelled
  before the buy; a sleeve market is never quoted after it.
- No duplicates: one order a market a tick; a sleeve IOC still listed as resting blocks a new one; a leg traded within
  120 s is not cut to the positions read (RT13-3); legs, cost and state persist in status.json.
- status.json `momentum` {state, cost, value_mark, value_outcome, markets, legs, kill_level, exit_progress, armed, on,
  auto_state, size_usd, target_usd, max_usd, slope_24h, slope_6h, reason, funded_from, ev_given_up,
  ev_given_up_per_10k, kept_back, holdback, reserve_usable, short_usd, hold_usd, quote_hold_usd, confirm_since, steps,
  next_step_at, series_bins, series_source, ...}; fill class "momentum" in `mm_carry_24h`; the 2-hourly summary
  " | momentum armed/on X/Yk, slope24 +a.b, slope6 +c.d".

## Deploy
1. Code: handover restart (`deploy/handover-restart.sh`) to this branch's head with the Package 15 file unchanged (after
   Package 15 is live). Nothing changes (flags off); status.json has no `momentum` key.
2. File: write `settings_override.momentum_armed.json` to a temp file next to `settings_override.json` and `mv` it over
   (read within 30 s).

## First-hour watch list
- "MOMENTUM tilt series seeded from the recorder: N bin(s) of 4 h; slope24 +x, slope6 +y pts/day" - compare with your
  own `tilt_s` trend (on 4 Oct 15:57 the code reads slope24 +2.29, slope6 +0.42; tilt_s fell to 0.109 by 5 Oct 10:21, so
  expect a NEGATIVE slope24 today). "no recorder history" = market_data.sqlite missing: the 24-h slope builds from live
  samples (>= 12 h before the trigger can fire).
- "MOMENTUM eval: armed - ..." lines (every 10 min): with slope24 < 0.5 or slope6 <= 0 the sleeve stays armed and buys
  nothing - the correct call for a momentum rule. **No "MOMENTUM bought" line before the 4-h confirm**, and before it
  the ALERT "MOMENTUM auto switched ON ...".
- Once on: `momentum.funded_from` - on today's live state (free cash below the 20k kept back, value positions beyond the
  floor) expect (a) 0 and (c) "nothing to sell ... at / above the value floor"; (b) only if `mm_carry_24h.per_day` < 300.
  `momentum.short_usd` > 0 with `hold_usd` 0 = a round it cannot fund: nothing else is held back.
- While a funded round is short: `harvest.paused_for_momentum` true (no NEW harvest levels), alloc `blocked_by.momentum`.
- Never a "MOMENTUM bought" on a market with harvest levels in its race; nothing of ours resting on a sleeve market.
- Writes <= 28 a minute (`write_budget_wait_total` flat; the sleeve <= 0.6 of the writes left).

## Rollback
`mv` the Package 15 file back over `settings_override.json`. The sleeve's legs, if any, are then HELD (no exit runs with
`momentum_enabled` off) and the harvest ladder ladders their races again: set `momentum_exit` true first and let it sell
(watch `momentum.exit_progress`) if they should go. Code rollback: ed65638.

## Dry run (`analysis/p16/DRYRUN_P16.md`, `tests/test_p16_dryrun.py` 37 checks)
- Flags off: the Package 15 file on ed65638 and on this head, 12 cycles on the live state: identical orders and status;
  the Package 16 file with its two switches off = the Package 15 file.
- Armed with no history: no buys, reason "no history".
- Seeded from md.sqlite (4 Oct 15:57): bins 10.69 / 11.92 / 12.25 / 12.48 / 12.75 / 12.82, **slope_24h +2.29,
  slope_6h +0.42** -> "armed - confirming"; ON after 4.00 h, no buy before, the ALERT. The live state as it is: free cash
  1.6k < the 20k kept back, (b) usable (carry 0/day) but the MM's 10k is not there either, (c) nothing at / above the floor
  -> $0 bought; the round is short but unfundable, so nothing is held back (ladder, allocator, quotes as in Package 15).
- Funded fixture (+45k arriving at the switch-on): the first round $10,000 in 15 markets from cash (a) - Ind Montana
  Senate YES 11,440 @ 0.080 (p 0.085), Rep TX-15 YES 3,449 @ 0.215 (p 0.207), Dem VA-02 NO 264 @ 0.80 (p 0.815), ...;
  the ladder placed no new level while the round was short; RI held at 15.0k; the ramp: 20k at +6 h, 30k at +12 h, 40k
  at +18 h of a simulated rising tilt, the sleeve growing as cash allows (to ~$15-20k, the rest from (b)).
- Flip: a -2-point bin -> slope_24h <= 0 -> flipped, the exit over 6 h, the ladder takes the markets over.
- Kill: a -30% mark move -> killed 120 s later, one alert, latched exit, no buy.

## Tests
`tests/test_p16.py` 233 checks; `tests/test_p16_dryrun.py` 37; all 56 suite files N/N, `STRESS_LADDER=1` 20/20.

## Decisions (what changed from Package 13 in the port)
- Not ported: `buckets_enabled` (the 20/40/40 rebalance and its floor-exempt sell-down), `aggressive_value` and the
  `aggr_*` caps, `election_night` (calls, takes), `momentum_fund_floor` (the floor is always kept: no exempt path).
- `bucket_mom_frac` x account -> `momentum_max_usd` 40,000 (the ramp's cap and the forced target).
- The market cap is `alloc_max_contract_usd` (P15's per-market $ cap, valued at max(price, p)) and the state cap
  `state_max_usd` (the sleeve's collateral at p counts); no per-race cap (aggr_max_race_usd is gone).
- Funding (a) without an election holdback = free cash above the MM's effective reserve + the ladder's carve-out not
  resting (= alloc_mm_reserve - the ladder's resting part); `election_holdback_usd` / `_from_utc` ported minimally as a
  hook (0 by default; only the sleeve's funding keeps it back).
- Funding (c) reuses the allocator's refill sale path (14.1): `alloc_plan(..., refill_only=True, fund_usd=shortfall)`
  -> pairs tagged "fund" sold by `alloc_sell` (the strict floor re-checked on the fresh book, the refill's
  alloc_max_edge_sell rule, the RT13-3 lag rule, alloc_max_turnover_per_hour), proceeds earmarked (ma_pool); not the
  bucket sell-down.
- The sleeve runs FIRST among the traders (step 6a', Package 13 ran it after the allocator): in the dry run the takes and
  the allocator otherwise spent the free cash and the write budget before it.
- The hold is only for a round with cash to protect, and the quotes leave the kept-back cash + what the sleeve can spend
  (Package 13 left the whole shortfall from the quotes: an unfundable round would have starved the market maker
  indefinitely); the takes keep the hold back too.
- The ladder leaves the whole race of a sleeve leg while it is held (Package 13: the leg only) - rule 5 both ways.
