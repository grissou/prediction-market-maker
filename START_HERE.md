<!-- STATUS (Finisher 2b, updated on every push) -->
**STATUS ~15:00 UTC 5 Oct:** **Package 14.2 READY** on `claude/mm-funding` (64f27c8, on 14.1 124ce75 on the LIVE 4ff7d91; nothing from Package 13). Staged file `deploy/package14/settings_override.mm_funding_14_2.json` = the 14.1 file + `alloc_swap_sell_margin` 0.03 + `alloc_swap_min_gain` 0.05 + `value_sell_margin` 0.005. Package 13 READY separately (claude/finisher-package9, PR #11), not deployed.
Finding (analysis/p14/DIAG_14_2.md): `value_sell_margin` NEVER floored a paired allocator sale (a swap) in 4ff7d91 or 124ce75 - the sale is governed by `alloc_max_edge_sell` + `alloc_min_improvement` only; raising the margin live only let the REFILL sell more low-edge holdings (which swaps would have used) and moved stale-MM holdings into the refill-only pool: on the 10:21 state 14.1 swaps fall 24 -> 5 at 0.03. The sale's "cost" (p - price per $) IS edge-held at the sale price, so it is counted once. 14.2 = a NEW, tighter swap floor (p -+ 0.03, checked at pairing and on the fresh book before the IOC) + the hurdle max(alloc_min_improvement, alloc_swap_min_gain); every other reducing path keeps value_sell_margin; "ALLOC SWAP" journal lines at plan and fill (sold edge, price vs p, bought edge, net EV gain), status alloc.swaps, a WARNING while value_sell_margin > 0.01.
Dry run (10:21 state, first run): 14.1 @0.005 24 swaps / $10.1k / +680; 14.2 staged 20 / $6.1k / +464 (7.6% per $; sales 0.7-3.0c below p), 19 done in 12 cycles, +318 realised at p; dropped: Rep Oklahoma Gov (0.89 vs p 0.92), two NO buy-backs 3.4-3.5c over p, one pair at 4.98%. Suites: 52 files N/N (test_mm_funding_14_2 79, test_p14_2_dryrun 25) + STRESS_LADDER 20/20.
Deploy: claude/mm-funding head 64f27c8 + the 14.2 file (the 14.1 file first if 14.1 is not live yet; rollback = the 14.1 file); watch list in deploy/package14/README.md (14.2 section).

# START HERE (Team run, branch `claude/run-c-tournament-improvements-pycdet`)

Status: **complete** (Team complete at 14:45 UTC) (started 2026-10-02 08:50 UTC; credits nearly spent at 14:35 UTC). **Package 1 READY at 10:10 UTC, Package 2 READY at 11:05 UTC, hot-fix Package 2.1 READY at 12:05 UTC, hot-fix Package 2.2 READY at 12:40 UTC, Package 2.3 READY at 13:10 UTC, Package 3 READY at 14:45 UTC (deploy this one)** (deploy-ready, cumulative). Package 2 is LIVE since 11:21:57. Base: `claude/live-2026-10-02b` (5c0463a), the code live since 08:34.
The Builder's previous START_HERE is kept as `START_HERE_BUILDER.md`; Run A's notes are `ENGINEERING_NOTES.md`.
Plan: `PLAN.md`. Packages appear below as they become READY (commit messages start "READY: Package N").
Deploy only commits whose message starts "READY"; the branch is cumulative.

## Package 14.2 (READY, 5 Oct ~14:30 UTC; branch `claude/mm-funding` on top of 124ce75 = Package 14.1, itself on the LIVE head 4ff7d91; NOT merged with Package 13): a swap-only value-floor margin (OFF by default; staged file in `deploy/package14/`)
**The owner's brief (13:20):** "a SWAP-ONLY value-floor margin ... `alloc_swap_sell_margin` (e.g. 0.03) ... when the
paired buy's edge-held exceeds the sale's edge-held + the sale's cost by >= `alloc_swap_min_gain` (e.g. 0.05). All
other reducing keeps `value_sell_margin` (the owner will return it to 0.005)."
**Diagnosis** (`analysis/p14/DIAG_14_2.md`): `value_sell_margin` never limited a swap. In 4ff7d91 and 124ce75 a paired
allocator sale is judged only by `alloc_max_edge_sell` and `alloc_min_improvement` (124ce75: `alloc_plan`, `alloc_sell`
11045-11050); the margin is read by quotes, reduce-only quotes, the recycler, refills (`fast and b is None`, 11051),
stale-MM IOCs, the set ladder and the exit - never by the pairing, the paired sale or the send path. Raising it makes
swaps FEWER: the refill sells the low-edge holdings first and stale-MM holdings at the floor become refill-only legs
(10:21 state, 14.1 file: 0.005 -> 24 swaps / est. +680; 0.03 -> 5 swaps / +274 and 26 refills). Live (4ff7d91) the
risk-room pause allows no swap at any margin: the raise only added refills. The sale's cost IS its edge-held at the
sale price ((p - price) / price for a long), so it is never added twice.
**Built (OFF; flags off byte-identical to 124ce75):** `alloc_swap_sell_margin` (0-0.10): a SWAP sale must be within it
of p (long >= p - m, short's buy-back <= p + m) in the pairing ("swap_floor") and on the fresh book right before the
IOC; `alloc_max_edge_sell` still applies. `alloc_swap_min_gain` (0-0.5): swaps need buy edge - sale edge-held >=
max(`alloc_min_improvement`, it) ("swap_gain"). Everything else keeps `value_sell_margin`. A buy that fails after its
sale leaves the sale as traded (an IOC: nothing to reprice), the cash in the reserve. Journal "ALLOC SWAP sold ... ->
buy ...: net EV gain +$x (per $ y%)" at plan and at fill; status `alloc.swaps` {last_run, counts_24h, usd_24h,
ev_gain_est_24h, ev_gain_realised_24h}; a WARNING while the swap margin is on and `value_sell_margin` > 0.01.
**Dry run** (`analysis/p14/DRYRUN_14_2.md`, 25 checks, the first allocator run on the 10:21 state): 14.1 at 0.005: 24
swaps / $10.1k / est. +680 (6.8% per $); 14.1 at 0.03: 5 / $3.6k / +274; **14.2 staged: 20 / $6.1k / +464 (7.6% per
$)**, every sale within 3c of p, every gain >= 5%; 12 cycles: 19 done, 0 buy failed, est. +464 / realised +318 at p,
room_wc 8.6k -> 11.0k. 14.2 is a STRICTER swap rule than 14.1, not a looser one (it drops Oklahoma 3.0c+ below p, two
NO buy-backs 3.4-3.5c above p and one 4.98% pair); a 5c margin with `alloc_max_edge_sell` 0.10 would move $8.1k for
+589.
**Suites:** `tests/test_mm_funding_14_2.py` 79 + `tests/test_p14_2_dryrun.py` 25; all 52 suite files N/N +
`STRESS_LADDER=1` 20/20.
**Deploy:** handover to this branch's head, then `mv` `settings_override.mm_funding_14_2.json` (= the 14.1 file +
`alloc_swap_sell_margin` 0.03 + `alloc_swap_min_gain` 0.05 + `value_sell_margin` 0.005) over the live file. **Watch:**
"ALLOC SWAP sold ... - planned / - done, realised at p", `alloc.swaps.counts_24h`, `alloc.blocked_by` swap_floor /
swap_gain, no WARNING line, plus the 14.1 list. **Rollback:** the 14.1 file (the margin off; `value_sell_margin` goes
back to its 0.005 default), then the 14.0 file; code 124ce75 / 4ff7d91.
**Caveats:** the swap margin is a NEW limit (swaps were never floored), so expect fewer, better swaps than 14.1, not
more; the fake never fills our resting quotes, Polymarket is flat, writes are unlimited, and the 10:21 state is
rebuilt from the 4 Oct snapshot; pairs that share one buy level can find it partly taken (the rest of the proceeds
stays cash, 14.1's pairing); `alloc.swaps` events persist across a restart but pairs in flight do not (as before).

## Package 14.1 (READY, 5 Oct ~12:00 UTC; branch `claude/mm-funding` on top of the LIVE head 4ff7d91 = Package 14, NOT merged with Package 13): the MM funding refill and the allocator's swaps unstuck (every setting OFF; staged file in `deploy/package14/`)
**The owner's brief (10:30):** "fix the market-making funding and the allocator's swaps; both are stuck live" (10:21:
cash_free 1,903 of 20,000, room_free.wc 9,509 of 20,000, refill runs 0, blocked_by {mm_resting 34, prefer_short 8,
set_bids_gt_1 6, mm_risk_reserve 1}, MM inventory 9.6k -> 10.5k with 7.9k stale (oldest 23.7 h), the last three
allocator runs 4 / 2 / 0 pairs at est. gain 0 against >= 12% seats in the books).
**Diagnosis** (`analysis/p14/DIAG_14_1.md`, reproduced on `/home/claude/snap04` moved to the 10:21 numbers): (1) the
fast refill is starved by PRICE, not by our own orders - "mm_resting" means the stale MM shares' best OTHER bid was
over `mm_recycle_concession` from fair or below the value floor, and in that book only ~$0.8k of holdings sit at or
above p - 0.5c ($4.8k within 2c, $7.4k within 3c, $43k deeper); every allocator IOC already cancelled our quotes
there first. (2) The swaps were switched off by the risk-room pause: `mmr_paused` drops EVERY buy level in
`alloc_plan`, hence "pairs planned, est. gain 0". (3) A run in flight (pairs waiting for a buy the pause refused) plus
the 60 s gap locked the fast refill out for up to 15 min at a time. (4) "prefer_short" / "mm_risk_reserve" never
blocked a refill SALE - they were buy-side counters sitting in the refill's blockers. (5) The recycler's buy-backs are
covered lone-NO sales: the gate charges 0 and a fill FREES (1 - price) a share; what is slow is the price.
**Built (each OFF, in OVERRIDABLE; flags off pinned byte-identical to 4ff7d91 on a grid):**
- `alloc_cancel_mm_first`: stale MM shares are ordinary refill candidates judged by the refill's own price rule (the
  value floor), not the concession gate; the sale is the usual IOC after ONE whole-exchange cancel, same cycle, and the
  market's REDUCING quote side is then held ("refill pending") until the positions read shows the sale or 120 s pass -
  the quoter never re-offers what was just sold; the adding side keeps quoting.
- `alloc_rank_all_markets`: every holding ranked by edge-held across all markets (the `alloc_max_edge_sell` threshold
  stays the SWAPS' rule), a blocked candidate skipped and counted, the run going on; the fast refill EVERY cycle while
  free cash < `alloc_mm_reserve`, added to an hourly run in flight (its markets skipped), inside
  `alloc_max_turnover_per_hour` and the write share; a paired level gone no longer stops the refills.
- `mm_recycle_sell_first`: recycled sales before the rest, buy-backs after them and sized by their NET cash (gate need
  less the (1 - price) a share the fill frees); below half the cash target the shares that lock more than they free are
  deferred (`mm_funding.deferred_buybacks`).
- `alloc_refill_ignore_prefer_short`: below half the cash target a refill plan scans no buy levels at all (its
  blockers then only name reasons that stop a sale).
- `alloc_swap_room_netting`: swaps continue while value adds are paused - a pair is admitted only while both rooms stay
  >= min(the room now, the reserve) after its sale AND its buy (re-checked before each leg), so a swap never takes a
  room below the MM reserve net of its own sale; its buy may spend its own sale's proceeds below the cash reserve,
  never more. Refills, takes, basket buys, tail adds and reduce-only are untouched.
- `alloc_refill_max_cost` 0 (off, NOT in the staged file): below half the cash target a refill sale may go this far
  below p instead of `value_sell_margin`; the EV given up is reported.
- Reporting: `mm_funding` {refill_runs, refill_sales_24h, refill_sold_usd, refill_last, refill_ev_given_24h,
  deferred_buybacks, refill_holds, cash_locked}, `alloc` {swaps_planned, swaps_planned_24h, swaps_done_24h,
  swaps_usd_24h, ev_gain_est_24h, ev_gain_realised_24h (from the IOC fills at p, the sale's given-up edge counted
  against the buy's gain), refill_blocked_by}, and one added piece on the 2-hourly summary.
**`alloc_max_edge_sell`: 0.05 recommended** (one planning run on the 10:21 book, cash at the reserve, gain net of both
touch prices): 0.02 (live) 8 swaps / $847 / est. +99; **0.05 25 swaps / $10,068 / est. +650** (of which 354 is the EV
given up on the sale side); 0.08 32 swaps / $11,812 / est. +713 (454 given up) - the last volume at a worse gain per $
and deeper into positions that may simply be converging. The pairing hurdle stays `alloc_min_improvement` 0.03, which
IS a min gain per $ (at 0.06: $6.9k for +489), so no separate `alloc_swap_min_gain` was built. Refill sales keep the
value floor; swap sales are governed by the gain hurdle, as in 4ff7d91.
**Dry run** (`analysis/p14/DRYRUN_14_1.md`, `tests/test_p14_1_dryrun.py` 32 checks): on the 10:21 state the deadlock
reproduces ($487 freed, mm_resting 16 / floor 4, no swap, est. gain 0). With the staged file the FIRST allocator run
plans 28 pairs (24 swaps), est. +680 of EV, cancels our quotes in each market it trades and holds 4 of them quiet on
the reducing side; over 12 cycles 21 swaps are done, $6.5k moved, EV +680 estimated / +382 realised, room_wc
8.9k -> 11.6k (never below the reserve net of each sale), turnover $9.5k of 15k. The refill still raises only $487 at
the value floor ($3.8k with `alloc_refill_max_cost` 2c, $7.1k with 3c): cash_free ends near 0 with ~$6.8k locked in our
own quotes - the market maker re-deploys what the refill frees, so `room_free.wc` and `cash_locked` are the honest
funding figures, not `cash_free` alone.
**Suites:** `tests/test_mm_funding_14_1.py` 100 (settings / ranges; identity vs 4ff7d91 on a 3 x 4 grid with fills:
orders, quotes, notes, status values less `mm_funding`, health, summary, `alloc_plan` with the pause on, `change_key`;
the refill with MM resting - cancel first, one write, no re-quote, the hold cleared on the read; cross-market ranking,
a blocked top candidate, the cached book; every-cycle refill, the turnover / write caps, a run in flight; recycler
sell-first, `buyback_net`, the deferral; prefer_short below / above half; swaps under the reserve, the floor, the
proceeds rule, reduce-only; `alloc_max_edge_sell` / the min-gain hurdle; `alloc_refill_max_cost`; the reports and the
restart; no duplicate orders, RT13-3; empty books / no reference / unknown markets; the staged file; warnings;
py_compile 3.10) + `tests/test_p14_1_dryrun.py` 32; 50 suite files green, every one N/N (test_mm_bot 600, test_alloc
123, test_value_mode 101, test_mm_funding 100, test_p12_alloc 95, test_mm_risk_reserve 66, ...) + `STRESS_LADDER=1
python tests/test_stress.py` 20/20.
**Deploy (owner; `deploy/package14/README.md`, section 14.1):** code = handover to this branch's head (flags off =
4ff7d91), then `settings_override.mm_funding_14_1.json` (the 14.0 file + your live values + the five flags +
`alloc_max_edge_sell` 0.05). **Watch:** "ALLOC run ... est. gain" with pairs that have a BUY again and "ALLOC bought"
following its own sale; `mm_risk_room.room_wc` / `room_corr` never below 20000 / 4000; "mm_resting" gone from
`mm_funding.refill_blocked_by`; one cancel per IOC, no market sold twice within 120 s; turnover <= 15k/h.
**Rollback:** `settings_override.mm_funding.json` (14.0; swaps and the new refill off at once), then
`settings_override.previous_live.json`; code: 4ff7d91.
**Caveats / not built:** the refill cannot reach the 20k cash target on a converged book without selling below the
value floor - `alloc_refill_max_cost` is the switch for that and it is deliberately OFF (the owner's call, with the
numbers above); the fake exchange never trades with our RESTING quotes, so the recycler's own contribution to funding
is not measured (live it was 19 fills in 13 min) and `ev_gain_realised` is an EV figure at Polymarket, not P&L; a
holding can still fund two swap buys in one cycle (two IOCs in that market in a cycle, 4ff7d91's pairing behaviour, no
oversale); the 10:21 state in the dry run is rebuilt from the 4 Oct snapshot, not copied from the server, so the live
`locked_in_orders` and per-market depth are not reproduced; nothing in 14.1 changes the definition of `cash_free`
(cash after our resting orders' locks), which is why it does not rise even when the refill works.

## Package 13 (READY, 4 Oct ~23:30 UTC; branch `claude/p13-momentum-auto` = `claude/finisher-package9` f61495e + Package 13 C, on top of the live head c7c0107): the 20 / 40 / 40 AGGRESSIVE portfolio + the momentum sleeve's automation (all OFF; staged file in `deploy/package13/`)
**The owner's instructions:** 18:40 + 18:55 (analysis/p12/SPEC_P13_AGGRESSIVE.md: "no modelling, no comparisons: aggression"; MM 20% /
VALUE 40% / MOMENTUM 40%; limits that stay: the cash gate, the value floor for value positions, the backstop as a 1.0 bug tripwire, the
30% drawdown kill, 15k / race and 12k / market of collateral); 20:30 (the momentum slope over 24 h, not 12, as a setting); 21:20
(automate the sleeve's switch-on: trigger, ramp, funding order, automatic flip / exit, "no fake buying", status). The 21:20 message
supersedes the spec's "flip not modelled" and, for the sleeve's own funding sales, the "sell-down exempt from the floor" answer.
**Built (each OFF, in OVERRIDABLE with a range; flags off pinned byte-identical on grids: A + B to 663ede1, C to f61495e):**
- A1 `buckets_enabled`: MM = `alloc_mm_reserve` cash (capped by the gate's cash) + middle-band inventory at p; VALUE = every other
  position at p; MOMENTUM = the sleeve at cost; hourly (`alloc_interval_s`) VALUE above target + `bucket_band` sold down lowest
  edge-held first, IOC at the touch, exempt from the floor (answer 1), <= `bucket_turnover_per_hour` 50k; MOMENTUM below target opens
  a buy round. Classification (answer 2): longshots (p <= `mom_max_p` 0.25) with p <= `value_extreme_p` 0.04 or a short's edge per $
  >= `value_min_edge` 0.12 are VALUE shorts, the other longshots MOMENTUM longs (YES, or the favourite's NO in a 2-leg race when
  cheaper); favourites with (p - ask) / ask >= `value_min_edge_fav` 0.08 VALUE longs; one bucket a market. status `buckets`.
- A2 `aggressive_value`: reduce-only only from the backstop TRIPWIRE (`worst_case_backstop_frac` 1.0); party / bloc caps do not block
  adding; the fraction caps replaced by `aggr_max_market_usd` 12k / `aggr_max_race_usd` 15k of collateral (all buckets + resting
  orders); "WARNING aggressive_value on" leads every summary; status `aggressive`.
- A3 `momentum_enabled`: the sleeve (IOC takers at the touch, >= `mom_min_depth` 200, <= `mom_max_markets` 40, never headline /
  basket / set-ladder / the opposite side of a position; its markets never quoted or traded by other features); `momentum_exit` (manual,
  sold over `mom_exit_hours` 6, exempt from the floor), `mom_exit_utc`, the kill (mark < `mom_kill_frac` 0.75 x cost for 120 s:
  latched "killed"); legs / cost / state persisted. status `momentum`.
- B4 `tilt_harvest_ladder`: resting YES asks over longshots (p <= 0.10) / bids under favourites (p >= 0.90) at touch + `harvest_offsets`
  0 / 2 / 4 / 6c, `harvest_level_usd` 3k a level where edge per $ >= `harvest_min_edge` 8%, <= `harvest_writes_frac` 0.4 of the writes,
  re-quoted on a 1c move or every `harvest_requote_s` 900; hidden from the quote planner; fills are VALUE ("HARVEST fill"); status
  `harvest`. B5 `election_night`: `election_holdback_usd` 20k kept in cash from `election_holdback_from_utc` 3 Nov 12:00; calls from
  `election_start_utc` 23:00 (Polymarket >= `election_called_p` 0.98 / <= 0.02 for `election_called_min` 10 min, or resolved); stale
  quotes on called races TAKEN below `election_take_max_price` 0.95 (IOC, batched, caps, the holdback); with `close_override_utc`
  "2026-11-04T17:00:00Z" + `stop_minutes_before_close` 5 (SIG confirmed). status `election`.
- C (Package 13 C, this branch) the sleeve's automation. `momentum_enabled` = the machinery only: **alone it no longer buys**; buys need
  `momentum_force` (40% at once, no trigger; the slope flip off) or `momentum_auto` with the trigger ON. TiltSlope: every minute the
  cross-section of non-headline markets with a liquid Polymarket price and an other-traders spread <= `momentum_slope_max_spread` 0.06
  (own orders stripped, never our fills), the slope estimator (sum x g / sum x^2, g = r - mid winsorised 8c, x = r - 1/legs) per
  `momentum_slope_bin_h` 4-h bin, 72 h in status.json, SEEDED on the first start from market_data.sqlite (background thread, read-only;
  no file = "no history"); slope_24h (regression over `momentum_slope_hours` 24) and slope_6h in points a day. **snap04 (4 Oct 15:57):
  bins 10.69 .. 12.82, slope_24h +2.29, slope_6h +0.42** (= the owner's). Trigger: slope_24h >= `momentum_on_slope` 0.5 and slope_6h
  > 0 held `momentum_confirm_h` 4 h -> ON (ALERT); ramp `momentum_start_usd` 10k + `momentum_step_usd` 10k per `momentum_step_h` 6 h of
  slope_6h > 0 (stalls on <= 0), capped at `bucket_mom_frac` x account. Funding: (a) cash above reserve + holdback; (b) the MM reserve
  while `mm_carry_24h.per_day` < `mm_carry_min` 300 (None = no); (c) `momentum_fund_value`: VALUE sold lowest edge-held first, never
  below the floor (`momentum_fund_floor`), status funded_from / ev_given_up / ev_given_up_per_10k; while the round is short the
  allocator's spare-cash buys pause, the ladder keeps reserve + shortfall back and yields the candidates' races, the quoter leaves the
  shortfall. Flip (automatic): slope_24h <= 0 (not forced), mark >= (1 + `momentum_profit_target` 0.25) x cost for 120 s, the date,
  the manual exit, the kill -> the existing exit, the ladder takes over; "flipped" re-arms after `momentum_rearm_h` 24 of a fresh trigger
  (0 = never), "killed" only by `momentum_enabled` false then true. Rule 5: never a buy where we rest / plan a sale (harvest in the
  race, sell-down, allocator, other features' orders). status `momentum` {armed, on, size_usd, target_usd, slope_24h, slope_6h,
  reason, funded_from, ...}; journal "MOMENTUM eval ..."; summary " | momentum armed/on X/Yk, slope24 +a.b, slope6 +c.d".
**Red team** (analysis/p13/REDTEAM.md, crashes / duplicate orders only): RT13-1 a harvest batch timing out after landing placed a second
ladder (adopted now); RT13-2 the quote's reduce cap double-offered harvest-covered shares; RT13-3 a lagging / 409 positions read
dropped a sleeve leg and re-bought it (120-s grace); RT13-4 election "covered" sales from a stale ex.inv (this cycle's position);
RT13-5 low, not fixed (a stop mid-tick lets that tick's IOCs go). Package 13 C found and fixed one more: with the sleeve armed but not
buying, a harvest level could land at the price of our own resting quote on the same side (two of our orders at one price): `hv_plan`
skips that level until the quote is gone.
**Dry run** (analysis/p13/DRYRUN.md, the 4 Oct snapshot, 237 markets): six fixes (no momentum round trip within the hour; the exit
serves the leg furthest behind; no ladder from a stale touch; calls read a dropped reference's raw price; election covered sales bound
by this cycle's position; the quoter's budget less the holdback). With the staged file the sleeve is now ARMED and buys nothing in the
harness (no recorder history: "no history"); the 1F fixture forces it on for an hour (books refilled every 10 min). Findings: F1 the
sleeve starved (addressed by C's hold); F2 the ladder's resting collateral is in no bucket; F3 exits dribble ~5 small IOCs a cycle;
F4 every race already priced <= 0.02 / >= 0.98 is called 10 min after 23:00; F5 a called race's p jumps when the reference is first
dropped then accepted; F6 the "ignoring it (wrong match?)" alert fires on every call against a stale book.
**Suites:** tests/test_p13c.py 134, test_p13.py 146, test_p13b.py 128, test_p13_redteam.py 90, test_p13_dryrun.py 94 (~5 min, needs
/home/claude/snap04), every other suite N/N, STRESS_LADDER=1 test_stress 20/20. The P13 A / B / red-team harnesses set
`momentum_force` where they enable the sleeve (= the old momentum_enabled behaviour); the red team's kill scenario moves every leg
against the sleeve (a short leg's book up).
**Deploy (owner; `deploy/package13/README.md`):** handover to this branch's head (flags off = live), then
`settings_override.aggressive.json` (the live file + section 7's risk settings + every flag on, `momentum_auto` true, `momentum_force`
false, `momentum_fund_floor` true, the C defaults explicit; 98 keys, validated). **Switches:** `momentum_force` (on now), `momentum_auto`
false (off: no trigger buys), `momentum_exit` (sell the sleeve). **Watch, first hour:** "MOMENTUM tilt series seeded ... slope24 ~+2.3,
slope6 ~+0.4" (vs the snapshot), "MOMENTUM eval: armed - confirming ..." and **no "MOMENTUM bought" before the 4-h confirm**, the ALERT
on switch-on, the harvest ladder resting, "BUCKETS sold" / "MOMENTUM funding sold ... at / above the value floor" (never below p -
0.5c), writes <= 28 a minute, "WARNING aggressive_value on" + " | momentum armed ..." on the summary.
**Rollback:** `deploy/package12/settings_override.stage1b_risk_reserve.json` (sleeve legs are then held: `momentum_exit` first if
they should go); code f61495e / c7c0107.
**Caveats / not built:** funding (c) with the floor kept finds ~$1 on the 4 Oct book (every value position's touch is below its floor;
floor exempt: ~$700 of EV given up for the first $10k, ~$1,050 per 10k over all $41k) - in practice (a) and (b) fund the sleeve; the
seeding reads `reference` without its liquidity flag and keeps a snapshot only when our price is not at the touch (live samples see
the others behind our quote); the sleeve's mark falls back to the fair value when a book side is empty (a sleeve that swept a whole
side can read a mark far from its cost - the kill / profit target act on it after 120 s); the Package 9 basket's own 12-h change
(`basket_s_change`) is unchanged (the 24-h window is the sleeve's); the write budget and the realtime feed are not modelled by the fakes.

## Package 14 (READY, 4 Oct ~22:30 UTC; branch `claude/mm-funding` on top of the LIVE head c7c0107, not Package 13): MM funding - keep market making fully funded (all OFF; staged file in `deploy/package14/`)
**The owner's instruction (21:10):** "keep market making FULLY FUNDED at all times" (live: `alloc_mm_reserve` 20000,
`mm_risk_reserve_wc` 20000, `_corr` 4000, backstop 1.0). **MM inventory** = fills of our resting quotes (order notes: not take / arb /
alloc / set ladder / basket) with p at fill (mm_carry_24h's; else fv at quote) in value_mid_low..high, kept per market as FIFO lots
(an opposite MM fill closes the oldest; a fill that only shrinks a non-MM holding opens nothing; never more than the position) in
status.json `mm_funding.lots`, restored at start, seeded on the first start from fills.csv's last 24 h (the order notes' horizon,
p = fv_at_quote). STALE = a lot older than `mm_inv_max_age_h` 6 (0.5-168) or the market's MM $ at p above `mm_inv_max_usd` 3000
(0-50000; 0 = no $ limit); stale shares = max(aged, the shares over the $ limit).
**Built (each OFF, in OVERRIDABLE; flags off pinned byte-identical to c7c0107 on a grid):**
- `mm_recycle_enabled`: the stale shares go out through the quoter's OWN reducing side (decide, after hold_quote, before the value
  floor): price fair -+ `mm_recycle_concession` 0.01 (0-0.05), never crossing the best other bid / ask, never past the value floor
  (live: the 0.5c margin binds), size max(quoter's, stale) <= position, our adding side a tick behind; one order per side as ever (no
  duplicate), a side a guard left out stays out, pinned labels skipped. Edge-held >= `value_quote_hurdle` (0: alloc_min_edge_buy) ->
  VALUE: the lots are handed to the value bucket (not recycled). Journal "MM RECYCLE ..."; fills of a recycled side are class
  "recycle" (+ recycle_ev) in mm_carry_24h (keys only once one exists).
- `mm_refill_fast` (needs alloc_enabled): free cash < `alloc_mm_reserve` -> the allocator's B2 refill on the NEXT cycle (at most
  every 60 s, `alloc_max_turnover_per_hour`, the allocator's write share; the hourly clock untouched): stale MM shares first (IOC at
  the best bid only within the concession of fair and at / above the floor, else they rest via the recycler), then value positions
  lowest edge-held first; every refill sale >= p - value_sell_margin (shorts <= p + margin), hourly refills too while on; a market a
  refill IOC sold is not planned / sold again until the positions read shows it or 120 s pass (RT13-3). "ALLOC fast refill ..." lines.
- `mm_room_guard` (needs mm_risk_reserve_*): the MM lots' own worst-case / correlated contribution (risk with vs without them) counts
  against the MM room first: the pause is decided on the value book's share, room + min(contribution, reserve), hysteresis 1.1x as
  before; value buying (allocator buys, takes, basket buys, tail adds) stays paused until it is back; recycler / refills never paused.
  status mm_risk_room.value_share_wc / _corr while on.
- Monitoring (always written, read-only, `Bot.MM_FUNDING_KEYS` ignored by the identity checks like EV_KEYS): status.json `mm_funding`
  {cash_free, cash_target, room_free {wc, corr}, room_target, inventory_usd, oldest_inventory_h, stale_markets, stale_usd, recycling,
  handed_to_value, below_half, below_half_since, refill, lots_source, lots}; with a flag on, ONE alert when cash or a room stays below
  50% of target for > `mm_funding_alert_h` 2 (0.25-24; re-armed after recovery) and " | MM funding cash Xk/Yk, room ..., inventory ..."
  on the 2-hourly summary.
**Suites:** tests/test_mm_funding.py 100 (settings / ranges; identity vs c7c0107 on a 3 x 4 grid with fills: orders, quotes, notes,
status values less mm_funding, health, summary, alloc_plan, mm_risk_room_update; lots: classification, FIFO, reconcile, merge,
persistence / restore, seeding from fills.csv; staleness by age / $; recycler price, crossing, floor, short side, one order, note tag,
hand-over, pins; fast refill: next cycle, order, floor, turnover / write caps, concession gate, RT13-3; room guard accounting,
hysteresis, reserve cap, recycler not paused; status fields, 2-h alert once / re-armed / restart; warnings; staged file; py_compile
3.10); 48 suite files green, every one N/N (test_mm_bot 600, test_alloc 123, test_value_mode 101, test_mm_risk_reserve 66,
test_ops_ev 63, test_p12_alloc 95, ...; identity checks in eight suites ignore `mm_funding` as EV_KEYS) + STRESS_LADDER=1 20/20.
**Deploy (owner; `deploy/package14/README.md`):** code = handover to the branch head (flags off = c7c0107; check `mm_funding.lots_source`),
then `settings_override.mm_funding.json` (the live file + the three flags + the defaults explicit). **Watch, first hour:** "MM RECYCLE"
lines, one ask per recycled market never below Polymarket - 0.5c; "handed to the value bucket" (many in wide middle books); "ALLOC
fast refill" while cash_free < 20k; turnover <= 15k/h; no market sold twice within 2 min; cash_free / room_free rising.
**Rollback:** `settings_override.previous_live.json` (flags off); code: c7c0107.
**Caveats / not built:** the hand-over uses the value hurdle on the allocator's edge-held at the best bid, so in wide middle books
most MM inventory counts as value (no separate MM hurdle setting); the seeding sees only the last 24 h (older inventory is value);
the recycler cannot add a reducing side a guard removed (it waits; the refill IOC is the other path); the fast refill does not run in
a dry run; NO+NO set unwinds keep their own cost rule (alloc_set_cost_per_usd), not the value floor.

## Ops add-on (4 Oct 16:00 UTC, commit 39e4012; owner's request 11:35): expected value at the outcome and the market-making carry in status.json
`ev_outcome` = cash + sum_long q x r + sum_short |q| x (1 - r), r = the race-scaled liquid Polymarket reference (the value-mode `value_p`
helper); a market without one is valued at the exchange mark, else the book's fair value, else left out, and counted in `ev_outcome_unpriced`;
cash = the cash gate's figure when read, else account value - positions at marks; `ev_outcome_scope` says so. `ev_outcome_delta_24h` = ev
now - the newest sample at least 24 h old (one sample per 5 min, 48 h ring `ev_outcome_history`, restored across restarts; None until 24 h).
Lines: the cycle line ends " | EV outcome 101.2k (+1,234 24h, 2 unpriced)"; the 2-hourly summary adds that and " | MM carry 24h +12 (mid),
value adds +340, takes -25". `mm_carry_24h` {realised (FIFO-matched middle-band maker buy/sell pairs per market, sum matched qty x (sell -
buy)), per_day, hours_covered, unmatched_shares, unmatched_ev (at p at fill - price), value_adds_ev (tail maker fills: qty x (p - price) for
buys / (price - p) for sells), takes_ev, fills {maker_mid, maker_tail, maker_unpriced, take, arb, alloc, basket}, takes_unpriced, meta_missing,
p_at_fill, p_now, band}. Classification by the order notes (basket > alloc / set_ladder > arb > take; else maker; self-test rows skipped); p at
fill time is noted in memory from `log_fills` (fills before a restart use the current p: `p_now` counts them). Read-only, no flag;
tests/test_ops_ev.py 63 (formula, delta ring and restore, lines, classification, FIFO, a status/orders identity check vs 859e751 without the
new keys). 44 suites + STRESS_LADDER 20/20 green. Pre-estimate from the data (analysis/p10/CARRY.md): the middle two-way book made ~1.5k on
the one funded day (2 Oct) and ~0 at 0 cash; 0.67 / 1.0k per day of carry -> P(EV >= 150k) 6% / 29% on top of plan (a). The live figure is
`mm_carry_24h.per_day` after 24 h of stage 3 with the reserve funded.

## Package 12 (READY, Finisher 2b, 4 Oct ~14:30 UTC; branch `claude/finisher-package9` on top of Package 10; draft PR #11 updated): the literature sweep (Package 11, ~180 sources) and its five small builds — the rich-leg set ladder, prefer-short, the pair-unwind gate, the close override, skew toward the target inventory (all OFF; staged files in `deploy/package12/`)
**Package 11 (owner 11:15: "spend 5 hours reading papers and online resources for ideas"; `analysis/p11/LIT_REVIEW.md`, six readers,
lit_anomalies 34 sources / lit_marketmaking 31 / lit_portfolio 37 / lit_forecasting 44 / lit_electionnight 31 / lit_competitions 19 from
memory - the session's 200 WebSearch calls ran out and WebFetch then needed per-URL approval; 70 ideas).** What changed the plan:
(A1) the Rep U.S. Senate sleeve is roughly FAIR: the seat-count "edge" was Ohio at pDem 0.40 vs the market's ~0.6 (P(Dem control) 0.58-0.63
vs 0.645), and political race prices are themselves pulled toward 50% (Le 2026) - VALUE_PLAN corrected; (A2) a rank bet, if any, is "floor +
ONE digital" (Browne 1999; Gaba-Tsetlin-Winkler; Haugh-Singal): Texas Rep <= $20k (P150 ~30% vs 16%, E 112.1k vs 110.6k, P85 6.5%, cliff at
~$22k; TX/KS are the two races where every model and red-state polling history agree on an R shade); (A3) NO tilt reversion before the
close (Page & Clemen: the bias shrinks only as resolution approaches; Restocchi 2019: PredictIt mispricing JUMPS in the last 24 h; contest
theory: laggards buy longshots late) - keep 10-15k back for the last 72 h; (A4) the NO+NO sets are STOCK: the NO on the favourite is the
rich leg (~2c fair, 7-12c priced): sell it as a ladder, keep the longshot-NO (the P9 `tilt_exit_take_split_sets` sold the OPPOSITE leg:
off in value mode); (A5) stage-3 `value_quote_hurdle` 0.08 put favourite bids ~2.5c behind the book: 0.05 (file changed); (A6/F1) the
rules say "all trades must be received prior to 12:00pm EST on November 4" and "rank ... as of the resolution of all Markets" while the
API's markets close 00:00 UTC and the bot stops itself at 23:45 UTC (`stop_minutes_before_close`): ASK SIG; if orders are accepted
00:00-17:00 UTC, election-night taking on AP-called races (+5-15k, more if payouts arrive overnight) is the one lever that moves P150 by
tens of points; (B) market making: adverse selection on our fills ~0 (retail flow: size up on the value side, no toxicity spreads); the top
80 markets carry 90% of fill edge; 88-94% of snapshots show no resting quote of ours (capital-bound, not write-bound); skew toward a target
inventory, not toward flat; two-way only in the ~35-65c middle; spend writes by expected value per write; sell the longshot rather than buy
the favourite when a race's bids sum > 1 (48% of 2-leg snapshots); Dutch books last a median 2 min (nobody runs a fast set-arb bot);
(C) fair value: consensus band (add only where Polymarket and the forecast models agree on the side of the tournament price: our
Senate/House/control edge is +5.5k at Polymarket but +1.1k at Decision Desk's; NH Governor PM 0.96 vs DDHQ 0.64; Dem House +8,876 at 0.84 is
+865 at PM, -777 at DDHQ: hold, don't add), don't extremise Polymarket, rho 0.55, 3-5% of Senate control unresolved (independents);
(D) rules and settlement: no tiebreakers; SIG may change outcomes; Polymarket's control rule counts caucusing independents; GA runoff
1 Dec, AK/ME RCV +2 weeks delay the standings, not our balance; poll-closing timeline in `lit_electionnight.md`.
**Built (each OFF, in OVERRIDABLE; flags off pinned byte-identical on a grid):**
- L1 `alloc_set_rich_leg` (+ `alloc_set_ladder` "0,-0.02,-0.04" YES offsets; tests/test_p12_alloc.py 95): the NO+NO sets' rich leg (NO on the
  favourite = the race leg with the strictly highest liquid p, passing the allocator's edge-held test) is sold as a RESTING ladder of
  covered "sell NO" orders = YES bids on the favourite at the best bid, -2c, -4c (the lower levels fill in a late spike), 1/3 of the set
  part each, capped at p + `value_sell_margin`, a tick below the book's ask and below our own ask; through `place_orders` and the cash
  gate's set tier (1.0 a share: at ~0 cash only lone parts go out); hidden from the quote planner (`plan_exchange` wrapper), re-quoted
  hourly (queue position), pulled at once when the race stops being a set, the favourite is no longer rich, its p is unknown or every
  level is not <= p + margin (RT12-1), pinned, pre-close, or off; a refused race waits 900 s (RT12-3); the longshot-NO leg is never sold;
  warning when `tilt_exit_take_split_sets` is also on (RT12-5). status `alloc.set_ladder` {races, shares_resting, filled}.
  Data check (snap03, 14.1k sets with a reference; Alaska Governor's 3,000 have none): selling the rich leg at the favourite's best bid is
  +424 of outcome EV (Montana Senate +199 at 7c/share, MN Governor +84, IL Governor +63; two races negative and skipped by the edge test);
  the -2c / -4c levels add to that only in a late spike.
- L2 `alloc_prefer_short`: when the race's best bids sum > 1 (own quotes excluded, 2-leg races) the allocator sells the longshot YES
  instead of buying the favourite YES (same exposure, better price, less cash) if the short passes edge / caps; `blocked_by.prefer_short`.
- L3 `pair_no_unwind_asks_le1`: no NO+NO pair unwind at a cost while the race's bids sum > 1 (the set is worth more leg by leg) or a ladder
  rests there (RT12-6); the allocator's B3 refill skips such races instead of stalling (RT12-2); profitable unwinds (asks < 1) unaffected.
- M1 `close_override_utc` ("" = off; ISO UTC within 2026-11-01..07; tests/test_p12_quote.py 80): every market's effective close =
  max(API close, override) through `effective_close` -> `hours_to_close`, the 15-min stop, `close_window` (takes, arbitrage, allocator), the
  phase line and `carry_ramp`; can only EXTEND a close; invalid -> ignored with one alert; the basket keeps the API close.
  `stop_minutes_before_close` is OVERRIDABLE (0-120; keep >= 5). No election-night taking yet (waits for SIG's answer).
- M2 `skew_target_inventory`: `compute_quote`'s inventory skew is measured from (inv - target), target = the allocator's latest planned
  holding (`alloc_target_for`) or, in value mode, the current holding when its edge-held > 0; `age_skew` 0 on +EV holdings; the ADDING side
  keeps its skew-from-flat so the target cannot ratchet (RT12-7); no target skew in reduce-only (RT12-4); race-netted like `eff_inv`; the
  reduce side keeps the value_mode floor; never crosses (Bergault-Guéant / Fodra-Labadie "informed market maker").
**Simulation evidence (live_sim at the Polymarket mark `pnl`, tests/live_sim_round12_skew.txt, 8 x 3 h, value world with 20% cash and adding
factor 0.5):** `value_mode` +116 +- 67; + `skew_target_inventory` +125 +- 53 (neutral in the sim: without the allocator only the fallback target
applies; the mechanism matters live where the allocator sets targets); `value_mode` + `skew_max` 0 + age skew off +205 +- 46 (the config-only
hygiene stands). Items L1-L3 and M1 need cash / sets / a close: unit tests only.
**Red team (`analysis/p11/REDTEAM_P12.md`): 1 high, 4 medium, 2 low, all fixed or warned** (tests/test_p12_redteam.py 21). Caveats: at
~0 cash the ladder sells little (lone parts only) until the reserve builds; a take on the favourite cancels the ladder until the hourly
re-quote; `alloc_prefer_short` does not check the short leg's depth (a missed buy at worst); `close_override_utc` extends every market;
stale allocator targets last until the next run; over-cap +EV positions are blocked from adding but not pushed to unload.
**Suites:** 43 files green (test_p12_alloc 95, test_p12_quote 80, test_p12_redteam 21, test_alloc 123, test_value_mode 101, test_mm_bot 600,
the rest as Package 10) + STRESS_LADDER=1 20/20; py_compile under Python 3.10.
**Deploy (owner; `deploy/package12/README.md`; base = Package 10 stage 3):** Package 10 stages first; then stage0 (code) -> stage1_sets_skew
(`alloc_set_rich_leg`, `alloc_prefer_short`, `pair_no_unwind_asks_le1`, `skew_target_inventory`, `bloc_rho` 0.55) -> stage2 ONLY IF SIG
CONFIRMS trading after 00:00 UTC on 4 Nov (`close_override_utc` 2026-11-04T17:00:00Z, `stop_minutes_before_close` 5). **Watch, stage 1:**
`alloc.set_ladder` appearing (<= 15 races), no ladder level above Polymarket + 0.5c or at/above our own ask, writes <= 28, `pair_owed`
quiet, fewer reducing asks on +EV favourites. **Rollback:** the previous file; a ladder level above the floor = bug (stage 0 at once).
**Open with SIG (the owner):** the per-market trading close vs the 12:00 pm ET rule; whether trading on public returns is allowed; whether
called races pay out overnight; how control / independents / recounts / runoffs resolve. **Not built:** election-night mode (F1 part 2,
~150-200 lines: a "certain" class on AP-called + Polymarket >= 0.98 races, takes batched 10 per write, funded by selling called winners;
only on SIG's yes), writes by expected value per write (B3), the consensus-band buy filter and TX/KS shade (C1-C3), the factor-stress cap (C5),
the target-aware allocator score (C6).

## Package 10 (READY, Finisher 2b, 4 Oct ~09:45 UTC; branch `claude/finisher-package9` on top of Package 9; draft PR #11 updated): VALUE MODE — SIG pays at the outcome: the no-value-selling guard, bloc-correlated settlement risk, the capital allocator with a market-making reserve, value market making (all OFF; staged files in `deploy/package10/`; the plan in `analysis/p10/VALUE_PLAN.md`)
**The owner's strategy change (07:30):** SIG settles positions at the OUTCOME. Package 9's long-tilt basket is negative-EV there (the
leader's +600% is mark-to-market: tilt-long books settle at a median ~28k); our toward-Polymarket book is +EV (marks 99.9k, E[final]
109.0k by the outcome model). Live = Package 8 in value mode (`ref_tilt_enabled`, tilt exits, `ref_guard_*`, `take_tilted_ref` off,
`pair_no_unwind_max_cost` 0.003). Package 9 stays READY but is NOT to be deployed; its code is on the branch with every flag off.
**Research (`analysis/p10/`: ideas_F 17, ideas_H 15, ideas_I 17; scripts F_*, H_*, I_*):** F: exchange flow ~3-5M shares/day; our capture
0.45c/share at 1 h; best day 313k shares (+1.0-1.3k); market making alone 0.9-2.0k/day and needs 15-25k of rotating cash; cash binds, not
speed (5,188 quotes refused for funds vs 4,139 accepted on 3 Oct). H: the outcome model (Polymarket probabilities race-scaled, one
national factor rho 0.45 / 0.85 for control, 20k outcomes): hold 109.0k / P120 2.7% / P150 0% / P85 0.6%; max-EV reallocation (a) 112.9k /
29% / 0% / 1.9%; + recycling (b) 114.9k; the ONE +EV correlated bet is Rep U.S. Senate YES at 0.35 (the 34 races imply P(Dem control)
0.49-0.59 vs the market's 0.645): $20k sleeve P150 17% (24% under the seat count) at P85 4%, a CLIFF to 25-65% past $22-27k; competitive-
race party blocs lose EV at the ask (P85 33-47%). Leaderboard at the outcome: top 50 ~133k (124-167k), top 10 ~197k; 582 never-traded
accounts sit at 100k, so any sleeve that loses ranks ~790. I: 82 paired rotations rotate $35k for +3.4k of EV net of spread (top 30: +1.95k);
the NO+NO sets cost 2.5-5.5c each at the touch (a 2c rule unwinds none) and free 21.2k for $668 of EV; recycling adds +1.6k (+1.4%), not
+10-20%; valued at the outcome, 3 Oct maker fills: favourite bids + longshot asks +1,948, favourite asks + longshot bids -1,920, takes +1,862;
AUDIT: reduce-only quotes skip the skew clamp and sell up to ~4.5c below Polymarket (`ref_weight` 0.7 + skews), and the pre-close windows
(`exit_hours_before_close` 2, `flatten_*` 12/6, not overridable) would dump the book and any sleeve before the results.
**Built (each OFF, in OVERRIDABLE; flags off pinned byte-identical on a grid; `mm_bot.py` +~700 lines):**
- A1 `value_mode` (+ `value_sell_margin` 0.005): the reducing side is never priced below p - margin (long) / above p + margin (short), p = the
  race-scaled raw Polymarket reference when liquid, in normal AND reduce-only quoting (the clamp now runs there too); `exit_quote` floored;
  keep limits floored; a reducing quote never flips the position past its size at a price the adding rule refuses (RT-7); the pre-close
  windows are inert (never below the 15-min `stop_minutes_before_close`, which still stops everything: RT-3) and the three window settings
  are OVERRIDABLE (0-48 / 0-48 / 0-12 h); WARNINGS at start-up / override for any value-selling flag still on. tests/test_value_mode.py 101.
- A2 `bloc_delta_enabled` (+ `bloc_rho` 0.45, `bloc_rho_control` 0.85, `max_bloc_delta_frac` 0.05): the closed-form national-factor
  sensitivity sqrt(rho) phi(Phi^-1(p_dem)) per YES share (sign + = Rep-leaning, as `party_delta`), p_dem scaled for independents; the cap
  |bloc_delta| <= 5% of the account per sd replaces the share-count party cap in `party_blocks` / `party_shift` / the ladder; a contract
  keeps its last sensitivity through a Polymarket outage (RT-4); status `bloc_delta`, `bloc_delta_frac`; summary " | bloc delta X/sd".
  `Bot.bloc_delta_now()` / `bloc_cap()` for the allocator. Ranges: `worst_case_backstop_frac` to 1.5; `max_worst_case_frac` 0.35 allowed.
- A4 `value_quote_hurdle` (0 = off; + `value_mid_low` 0.15, `value_mid_high` 0.85, `value_mid_inventory_quotes` 2): the ADDING side must
  clear a per-$ hurdle to the outcome (a YES bid <= p/(1+h), an adding ask >= 1 - (1-p)/(1+h)), so in the tails only favourite bids and
  longshot asks reach the book; the middle is two-way at `min_edge`, capped at 2 quote sizes (the cash rotates).
- B `alloc_enabled` (+ `alloc_interval_s` 3600, `alloc_min_improvement` 0.03, `alloc_min_edge_buy` 0.05, `alloc_max_edge_sell` 0.02,
  `alloc_pin` "" (labels never sold), `alloc_headline` False, `alloc_max_turnover_per_hour` 15000, `alloc_max_orders_per_cycle` 4,
  `alloc_writes_frac` 0.3, `alloc_max_contract_usd` 10000, `alloc_mm_reserve` 15000, `alloc_set_cost_per_usd` 0 (off)): hourly, ranks every
  holding by edge-held ((p - bid)/bid; shorts (ask - p)/(1 - ask)) and every book level (own quotes stripped, p <= 30 s old) by edge per $
  of collateral; reserve refills first (lowest edge-held, no buy) when cash < reserve, then pairs: SELL as an IOC taker at the touch only
  if a fresh book still shows the paired level within 0.5c, READ the cash, THEN BUY (expires after 900 s; the cash stays); never flips,
  shorts bought back only as covered NO sales, never a pinned label, never a market we quote (our quotes cancelled first), never in
  global reduce-only except refills (RT-2), bloc check on pairs and refills (RT-5), NO+NO sets unwound through the short-set registration
  at <= `alloc_set_cost_per_usd` of EV per $ freed, capped at the planned sets (RT-1); a pin matching nothing warns (RT-6). status.json
  "alloc" {state, last_run, pairs_planned, sold, bought, cash_before/after, ev_gain_est, blocked_by, reserve, ...}; "ALLOC ..." lines.
  tests/test_alloc.py 123.
- `take_respect_reserve` (False): a stale-quote take is skipped when its cash need would leave less than `alloc_mm_reserve` free (red team
  C-1: the takes spent 12.7k of the 11.0k the sets freed in 4 h). tests/test_take_reserve.py 15.
**Dry run on the 3 Oct live state (`analysis/p10/DRYRUN.md`, tests/test_p10_dryrun.py 44):** stage 0 identical to the live value-mode file
(reduce-only: worst case 81.9k vs 80.8k; 4 of 6 resting reducing quotes below Polymarket - 0.5c); stage 1 leaves reduce-only (worst case
84.5k vs 131k; settlement risk 20.1k vs 35.4k), 40 reducing quotes rest, none below the floor, none crossing, bloc delta +2,516/sd (2.5% of
the account); stage 2: first plan 2 sales + 11 set unwinds, all reserve refills (cash ~0); over 4 h $11.3k freed ($11.0k from sets for ~$347
of EV), nothing pinned sold, no flips, every short bought back covered; the takes then spent 12.7k (hence `take_respect_reserve`; with
takes off the reserve is reached in ~5-6 h); stage 3 (with `take_respect_reserve`): 220 adding sides computed, 69 at the top, 62 middle markets two-way, ~$18.9k of cash if all filled,
~1.4k/day on F's model; with +20k of cash 201 adding sides REST (67 at the top, 48 middle markets two-way) and the takes spend 5.0k of it
(19.3k without the flag); tilt rise (+3c): no value sold; convergence (books to p +- 1c): the allocator frees ~20k into
the reserve and buys nothing (no level with >= 5% edge).
**Carry check (`analysis/p10/CARRY.md`):** maker value-side fills earned ~4.9c per $ ONCE (1 Oct +1.4k on 39k cash, 2 Oct +4.3k on 79k,
3 Oct +0.2k on 3.4k): that is plan (a)'s edge, not carry; the repeatable income is the middle two-way book (+1,976 over ~1.3 days, ~1.5k on
the one funded day, netting out); the anti-value sides lost 1.9k. So the MM odds (6-29%) rest on the middle band earning 0.67-1.0k/day on
the reserve: measure 24 h live before counting on it.
**Simulation evidence:** live_sim judged on the Polymarket mark `pnl` (= settlement EV; tests/live_sim_round10_value.txt, 8 x 3 h, live
world with tilt 0.14): `ref_weight` 1.0 +212 +- 66; + `skew_max` 0 + age skew off +381 +- 73; + adding factor 0.5 with 20% cash +497 +- 82
(worst case rises as value is held: hence the backstop at 1.3). The outcome model is the judge for everything else (no 30-day sim).
**Red team (`analysis/p10/REDTEAM.md`): 7 fixed** (RT-7 reducing quote flipping past the position; RT-1 set unwind of 1,000 sets for a $50
refill; RT-2 allocator trading in global reduce-only; RT-3 pre-close inertness disabling the 15-min stop; RT-4 outage zeroing the bloc
delta; RT-5/6). **Caveats:** C-2 in practice the allocator refills the reserve and plans no pairs until cash >= 15k (ev_gain_est 0 in
the dry run); C-3 the middle-band cap in headline markets is 2 x the headline size (~24k shares); C-4 ranges allow a 100k reserve +
0.5 max_edge_sell (keep 15k / 0.02); C-5 the floor uses a stale Polymarket price if the feed stops; C-6 live the allocator gets ~2 orders a
cycle, not 4; kill switch `max_drawdown_pct` 0.30 is on MARKS (not overridable; 0.40 or settlement-based is a code default change).
**Suites:** 40 files green (test_value_mode 101, test_alloc 123, test_p10_dryrun 44, test_take_reserve 15, test_basket 150, test_p9_dryrun 69,
test_mm_bot 600, test_cash_gate 83, the rest as Package 9) + STRESS_LADDER=1 20/20; py_compile under Python 3.10.
**Deploy (owner; `deploy/package10/README.md`; base = your live value-mode file; nothing resets `arb_enabled` / `ref_tilt_headline`; only
stage 3 moves `capital_ceiling_adding_size_factor` 0 -> 0.5, your call):** stage0 (code, handover) -> stage1_value_guard (`value_mode`,
`ref_weight` 1.0, `skew_max` 0, `skew_age_enabled` false, the three pre-close windows 0, `bloc_delta_enabled`, `worst_case_backstop_frac`
1.3, `max_worst_case_frac` 0.35) -> stage2_allocator (`alloc_enabled`, `alloc_set_cost_per_usd` 0.06, `alloc_mm_reserve` 15000, your
`alloc_pin`) -> stage3_value_mm (`value_quote_hurdle` 0.08, adding factor 0.5, `take_respect_reserve`). **Config only, today, on Package 8:**
`ref_weight` 1.0, `skew_max` 0, `skew_age_enabled` false (the live_sim +381; stops the reduce-only value selling). **Watch, stage 1 first
hour:** the bot leaves reduce-only; `bloc_delta_frac` ~0.025; no reducing quote below Polymarket - 0.5c (recorder our_ask >= reference -
0.005 on longs); the WARNING line lists nothing. **Stage 2 first 4 h:** "ALLOC" refill lines (sets first), `alloc.blocked_by` cash, NO+NO
sets falling, `cash_gate_left` rising toward 15k (with takes still spending some of it until stage 3). **Stage 3 first day:** adding bids on
favourites / asks on longshots at the top of the book, middle markets two-way; fills' edge at Polymarket >= 5% in the tails; maker P&L at
the Polymarket mark >= 0.6k/day before relying on the reserve. **Rollback triggers:** any reducing order below Polymarket - 1c (bug: stage 0);
an allocator sale of a pinned label; a buy before its sale's cash; account below start - 2k at the exchange mark within 3 h of a toggle
is NOT a trigger in value mode (marks lag the tilt) unless `liquidation_value` at Polymarket (the EV) falls; 429s; a refused override.
**Untested:** value-mode quoting against the real exchange (fills, write budget); the allocator's IOC pairs live; the outcome model's rho
and Polymarket calibration (judgement); SIG's exact settlement rules (ties, independents, vacancies) for the Senate sleeve.

## Package 9 (READY, Finisher 2b, 4 Oct ~01:30 UTC; branch `claude/finisher-package9` from Package 8 7b298a4; draft PR #11): the catch-up package — the long-tilt basket with kill-switches, taker exits for the short-tilt book, arbitrage with a cash rule (all OFF; staged files in `deploy/package9/`; the plan in `analysis/p9/CATCHUP_PLAN.md`)
**Objective (owner, 3 Oct 22:50-23:00):** P(account >= 150k by 4 Nov) first, P(>= 200k) second, P(<= 85k) < ~10%, max drawdown < ~20%;
rank strategies by those numbers, not by mean P&L; the smallest risk that gets P(>= 150k) above ~40%, or say plainly that none can.
**Research (78 ideas, `analysis/p9/ideas_A-D.md`; synthesis `analysis/p9/SYNTHESIS.md`; data = `ops-snapshot-2026-10-03`, to 22:47 UTC):**
the leader's +600% is the favourite-longshot tilt: longshot YES / favourite NO bought on 28 Sep went x3.89 (top 10 x5.74, best x11.5:
Dem Montana Senate 0.010 -> 0.115) as s rose 1.7% -> 12.6% in 54 h (longshot mids 3.6c -> 9.4c with Polymarket flat at 2.5c); it fits a
logistic (K 0.35 +- 0.18; K < 0.20 rejected, 0.25-2 equally likely; the equal-weight series bends at ~0.18); its fuel is new entrants
(~6/h) and bid-heavy cheap-side books whose depth halved in 30 h. Our +35.9k toward-Polymarket book is the OTHER side (-356/point); the
stale-quote takes, measured from RAW Polymarket, added +15.0k of it (276 fills, 100% toward Polymarket, -406 at the mid). Rivals' stale
quotes are the tilt (nothing to take after removing it: -0.6 to +0.3 c/share); riskless sets return ~3.4%/month held, a set carousel
0.3-0.5k/day; market making 1-2k/day with capital, +149/day at 0 cash. **Nothing we run reaches 150k (P 0%).** The one plan that does:
hold the long side of the tilt, sized on the cushion above a floor, exited before the close.
**Built (each OFF, own setting, in OVERRIDABLE; flags off pinned byte-identical to Package 8 on a grid; `mm_bot.py` +1,476 lines):**
- F1 `basket_*` (28 settings; tests/test_basket.py 149): basket $ = `basket_mult` 5 x (liquidation value - floor), floor = max(`basket_floor`
  86k, `basket_floor_peak_frac` 0.85 x confirmed peak), cushion net of our own last-24 h buying x `basket_impact_frac` 0.06, cap min(80k,
  0.85 x account); legs = longshot YES (Polymarket < `basket_max_ref` 0.10) or favourite NO (short YES, only with covered NO sales and no
  NO+NO set), cheapest route per race, laggards first, no headline races, no independents, <= 25c a share, >= 20 legs at <= 5% each;
  built over `basket_build_hours` 4 as an IOC taker at ask + 0.5c (first build <= 75% of visible asks, then <= 25%/h per market), <= 4
  orders and 50% of the write budget per cycle, cash-gated (needs `cash_gate_enabled` and a cash read < 5 min old), never on a market or
  race another feature traded this cycle, never where we rest an order (the market maker does not quote basket legs; takes, pair unwinds
  and tilt exits skip them; skew and `tilt_exposure` exclude them: status `basket.tilt_exposure`). **Kill:** liquidation value < floor or
  < 85% of peak for 120 s -> sold down over `basket_kill_hours` 2, latched off across restarts (reset only by an explicit false then true).
  **Exit:** from `basket_exit_utc` 2026-10-18T12:00:00Z over 24 h, no adds from 7 days before, never past T-72 h (hard-coded, per market
  too). **36-h test:** tilt_s < 0.15 or 12-h slope <= 0 -> m `basket_mult_after_fail` 1.5 (can only cut). **Risk model:** basket legs counted
  at `basket_stress_frac` 0.4 x value in both measures; exempt from reduce-only; buys refused while the stressed worst case exceeds
  `worst_case_backstop_frac` x account (the last resort). Legs valued at max(fair value, book) for sizing (no over-buying). A crash in the
  planner never stops the market maker. status.json "basket" {state, peak, floor, cushion, target, held, legs, impact, test, ...} and the
  2-hourly " | basket $X (N legs, state)".
- F2 `tilt_exit_take` (+ `_max_cost` 0.01, `_per_hour` 15000, `_max_per_cycle` 3, `_max_leg_frac` 0.5; F2b `tilt_exit_take_split_sets` +
  `tilt_exit_split_max_ref` 0.10: the longshot-NO leg of a NO+NO set is sold too, first, as a covered sale sized to the cash gate's 1.0 a
  share, leaving the favourite-NO leg = long tilt; a refusal pauses that race 10 x take_cooldown; tests/test_tilt_exit_take.py 87): the
  tilt exits (sides shrinking a position that adds to |tilt_exposure|) are TAKEN at the best other level when within 1c of the TILTED fair
  value (never raw Polymarket); longshot NO first, favourite YES next, lines marked below their exit price first; never flips, never beyond
  depth, never during a jump guard, never set legs beyond the lone part, never basket legs; IOC, cash-gated, covered sales for NO.
- F5 `arb_cash_rule` (+ `arb_cash_mult` 1.25, `arb_cash_reserve` 2000, `arb_leg_depth_frac` 0.8), `arb_sellback` (+ `_min_sum` 1.00;
  tests/test_arb_cash.py 53): arbitrage sets only when `cash_left` >= 1.25 x need + 2k (shrunk to what fits), own quotes excluded from
  bid-sums and depth, size <= 0.8 x the thinnest leg, unequal legs owed and completed or reversed the same cycle (never flips, never a
  one-legged set); YES+YES sets sold back at bids >= 1.00. status `arb_cash_blocked`, `tilt_exit_takes`.
**Simulation evidence:** live_sim screens of the config hygiene and `tilt_exit_take` in the live world are flat to the digit (tests/live_sim_round9_hygiene.txt: the sim has no takes and its estimate equals the world tilt, so neither `take_tilted_ref` nor `ref_tilt_max` 0.20 nor the taker exits are mirrored there); the Monte Carlo `analysis/p9/B_mc.py` / `D_mc.py` / `P9_plan_mc.py` (output `PLAN_MC.txt`) IS the screen for the
basket: live_sim's 3-h world cannot hold a 30-day position and has no cash or set collateral (items F1/F5 are unit-test evidence as
Packages 7-8). The tilt model is a logistic with judgement priors (K median 0.30 base / 0.18 bear / 0.45 bull; 12% crash hazard; 35%
chance of an endgame unwind; D's greater-fool world at 20% in the "D prior"); own impact 0.010 of tilt per 80k bought, 10% extra
slippage after a crash, stops on liquidation value. **Odds (switch-on ~09:00 UTC 4 Oct):** hold the book 0% / 0% / 6% / 10%; m 5 cap 80k
built in 4 h **29%** (D prior 22%) / 1% / 1.2% / 4.2%; + carry 300/day 36% / 4% / 0.5% / 4.2%; **m 6 cap 90k + carry 40% (29%) / 11% /
0.7% / 4.5%** (P150 / P200 / P<=85k / P(DD>20%)); built over 12 h 26%; half size 3%; 20 h later 20%, 34 h later 7.5%; 5% exit failure under
the settled rule 28%. **CORRECTED (02:30, after the dry run below; `PLAN_MC2.txt`):** those odds assumed an 80-90k basket bought in 4 h. On the live books the
taker exits free ~8k/day within 2c of tilted fv (the whole toward book only through all three levels at any price), the sets are the
cheapest cash (21.9k at 2c), and each $ bought at the ask is valued at the bid and moves the tilt (-15c of cushion per $), so m 5 settles
near 40k and m 6 near 45k. With the basket built over 24 h: stage3 0.3%, stage3b 1.8%, + carry 9%; **stage3c (floor 80k, m 6, cap 60k)
+ carry ~20% (D prior 15%), P(<= 85k) 0.4-1.5%, P(DD > 20%) ~4%**; floor 75k / m 8 / cap 80k 23% at P(<= 85k) 2.7% (7.7% greater-fool).
Built over 48 h: ~0-2%. Plain answer: **no legitimate strategy gets to ~40%; the best achievable is ~15-20% (stage3c) if ~60k of cash is
raised within ~24 h; P(>= 200k) ~0%.**
**Red team (opus, `analysis/p9/REDTEAM.md`): 2 high** (the basket's shares flipped the sign of `tilt_exposure`, turning the tilt exits
against it: excluded; same-cycle buy on a market another feature just sold: refused), **5 medium** (ordinary shares on a basket leg
unmanaged: adopted; sizing at fair value could over-buy 2-2.7x: valued at max(fv, book); a leg with no bids kept its lagged mark so the
kill could miss a crash: counts at 0/1; kill/exit sales blocked by the cash gate or a set only logged: alert; the backstop no longer
bounded the basket: buys refused above it), **3 low** (mult_after_fail could raise m; ask-share windows lost on restart; two ranges too
wide: `basket_slip` <= 0.02, `basket_stress_frac` >= 0.2): all fixed. Documented, not fixed: `basket_enabled` false during a kill releases
the legs (use the exit date to sell instead); no kill check while the account value is missing; a positions read lagging > 120 s can
leave part of a leg unmanaged; `basket_floor` 50k / `basket_kill_dd` 0.5 are allowed by the ranges (keep >= 86k / <= 0.15 live).
**Dry run (`analysis/p9/DRYRUN.md`, tests/test_p9_dryrun.py 65):** the staged files applied in order through `check_overrides` on the fake
exchange seeded with all 237 live books, references, positions, marks and lots, full cycles on a simulated clock. Stage 0: no new orders,
reduce-only as live (worst case 79.2k). Stage 1: out of reduce-only the raw rule would send 113 takes in 10 min, 112 without a 0.08 edge vs
the tilted reference; stage 1 sends 2. Stage 2: 158 TILT EXIT TAKEs in 3 h, all legal (shrinking, <= cap vs tilted fv, longshot NO first, no
set part, no own order, <= 3/cycle); cash 256 -> 9.0k, exposure 35.9k -> 31.6k, worst case -> 70k. Stage 3 at 0 cash: nothing, status
"refused: no free cash (cash gate)" (fixed: was silent); on 9k: 26 legs, 11.3k, then cash-bound; with 91k cash: 64-67 legs, every leg rule
held, settles ~40k (m 5) / 45k (m 6). Kill: latched after 120 s, sold 56% / 7% / 1% left at 1 / 2 / 3 h (fixed: richest-first starved the
other legs; now furthest-behind-schedule first); two thin favourite-NO legs remain (exit depth is not checked at purchase: caveat). Exit:
no adds from 11 Oct, sold ~1 h after the 24-h window. Stage 4: arbitrage only under the cash rule (60 blocks at 0 cash).
**Suites:** 36 files green (test_basket 150, test_p9_dryrun 69, test_tilt_exit_take 87, test_arb_cash 53, test_mm_bot 600, test_cash_gate 83, test_tilt_exit 62,
test_nono_sets 103, test_live_sim_marks 115, the rest as Package 8) + STRESS_LADDER=1 20/20;
py_compile under Python 3.10.
**Deploy (owner; `deploy/package9/README.md`):** code by handover restart (stage0) -> stage1_hygiene (config only: `take_tilted_ref`,
`take_edge` 0.08, `ref_tilt_max` 0.20; the sets keep draining at 0.02) -> stage2_flatten (`tilt_exit_take` at 2c + `tilt_exit_take_split_sets`: the
longshot-NO legs of the NO+NO sets sold first; dry run: cash ~15k in hour 1, ~21k in 3 h) -> when `cash_gate_left` > ~15k: stage3c_basket_floor80k
(the best odds; or stage3 / 3b at floor 86k; all with `worst_case_backstop_frac` 0.9) -> stage4_carry after the basket is at target. **Watch, stage 2 first hour:** "TILT EXIT TAKE" lines at <= 1c cost vs tilted fv, `tilt_exposure` falling, `cash_gate_left` rising,
`worst_case_loss` falling; no take against our own order. **Stage 3 first 4 h:** `basket.state` building -> tracking, `basket.held` rising to
the target in 2-4 h (if it stalls: `cash_gate_left` 0 or the stressed worst case above the backstop: wait for stage 2 to free more), legs
>= 20, each <= 5%, prices <= 25c, no basket order on a market we quote, writes <= 28, `basket.impact` ~1-5k; liquidation value within ~3%
of the account (the mark lags); `tilt_s` rising. **Days 1-2:** the 36-h test result in `basket.test`; peak and floor ratcheting.
**Rollback triggers:** the kill fires (it is the rollback: then `rollback_basket_off`); a basket order on a market with our own resting
order (bug: off at once); `basket.held` above target + 10%; 429s; a refused override. Code rollback = handover to 7b298a4.
**Untested:** the basket against the real exchange (IOC fills as `quantityTraded`, partial fills between placement and the leftover cancel
are not booked to the basket and stay ordinary inventory); `tilt_exit_take`'s hourly cap and the owed-arbitrage state are not saved across
restarts; nothing here ran in live_sim; the tilt model's ceiling K is a judgement prior (the whole bet).

## Package 8 (READY, Finisher 2b, 3 Oct ~21:45 UTC; branch `claude/finisher-package8` from Package 7 2ef6d12; draft PR #10): the cash gate (ends the 400 refusals), pair-unwind sizing in sets, and the tilt exits (all OFF; staged files in `deploy/package8/`)
Live at the time of writing: Package 7 since 19:15 UTC with tilt on at `ref_tilt_max` 0.11 (`tilt_s` 0.110, +0.0036/h), headline on,
`reduce_no_as_sell`, `no_set_aware_bids`, `pair_unwind_followup`, `pair_no_unwind_max_cost` 0.02, `capital_ceiling_adding_size_factor` 0;
capital 100%, free cash ~0, tilt_exposure +34.3k (~356 marked per point), reduce-only most cycles, ~132 of 150 changes deferred per cycle.
**The brief (owner, 19:56):** (1) 299 refusals with 400 "Insufficient available funds" an hour, each a write: 85 on NO+NO races despite the
set-aware bids, 44 on FLAT markets with the adding factor at 0, 5 on YES holdings; (2) is the 0.02 pair-unwind cap right (`unwind_is_safe`,
per-cycle limit 2, 30 s cooldown, follow-up; ~24 races qualify; PA-10 unwound 82 of 897 offered); (3) in the live-pinned world with 0 cash:
(a) the fastest non-crossing way to cut unpaired tilt exposure, (b) when to switch the adding factor back from 0 to 0.5, (c) whether the
reference guard should stop blocking reducing quotes when the tilt explains the gap.

**Item 1, root cause and fix (`cash_gate_enabled`, `adding_factor_per_market`; tests/test_cash_gate.py 83).** Every refused order came from
the same place: the "adding" side for the size factors (capital ceiling, turnover-dead, X5, backstop band, tail, mark-fragility, the ladder's
factor block) is picked by the RACE-NETTED position eff_i = x_i - mean(others), not by the market's own. A NO+NO leg -817 / -975 reads "net
long", so its bid is "reducing" and goes out at full size although buying YES there costs cash (the set-aware cap only limits COVERED bids);
a flat market whose other leg is long reads "net short", so its YES bid is "reducing" (that is the 44 flat markets, factor 0 notwithstanding);
a small YES holding the same. All three categories are reproduced on the fake exchange at 0 cash (flags off: sent and refused; flags on: none).
- `cash_gate_enabled` (+ `cash_gate_reserve` 25): no order needing more cash than the exchange's `cashBalance` (less the reserve) is SENT.
  One gate at the single send point (`place_orders`, computed on the main thread in `submit_write`: bid = price x qty, ask = (1 - price) x qty
  beyond the YES held, covered NO sale = 0), the same cap at plan time (`plan_exchange`, so quotes are not placed-then-refused), pre-checks in
  `execute_take` / `take_aged` / `pair_passive_take` / `execute_arbitrage` (every leg alike) / `pair_followup_take` BEFORE our quotes are
  pulled; orders capped to the part the cash allows, dropped below 1 share; the need of anything not placed (batch error, write-budget wait,
  refusal, cancelled future) is given back; the self-tests' 1-share orders are exempt. A failed P&L read / no cash figure alerts once; after
  5 min without a good read the gate stops gating (logged once) until one is read. status.json `cash_gated`, `cash_trimmed`, `cash_capped_quotes`,
  `cash_gate_left`, `cash_gate_read_age`.
- `adding_factor_per_market`: the adding side for the size factors is the side that grows THIS market's |position| (and the part of a reducing
  order beyond it), so your factor 0 means no new per-market positions, hedges included. Skew, the reduce-only clip and the risk limits still
  use the race-netted position. (Also needed by `tilt_exit_full_size`, below.)
Flags off: identical to 2ef6d12 on a grid (`cover_no_qty`, `plan_exchange`, two full cycles).

**Item 2, verdict on the 0.02 cap (tests/test_pair_sizing.py 29): stay at 0.02; set `pair_no_unwind_max_per_cycle` 1 while the 24 races drain.**
A set bought back at asks sum 1.02 costs 2c per set vs holding it to the close; 0.03 pays ~3.7c per MARGINAL set for cash that the factor-0 bot
cannot redeploy (adding is off), so the cap buys nothing until adding resumes; revisit 0.03 only with (b). PA-10's 82 of 897: the pair is sized
to the SMALLER leg's NO holding and to the joint depth (the caps allowed 1,262 by cash and 4,810 by `pair_unwind_max_frac`), so 82 is either
the smaller leg's holding or the partial fill the follow-up then owed (both legs sell NO x the same quantity in one batch; `pair_owed` shows
which). Three things found and fixed, each behind its own OFF flag (the live config already satisfies Package 7's gate, so nothing here changes
until switched on; identity test vs 2ef6d12 on the live config): `pair_no_unwind_max_sets` (0 = the old cash cap; 1000 staged: the cap was
cash-per-order measured on the YES ask while the legs are covered NO sales that RECEIVE 1 - ask and lock nothing); `pair_unwind_race_order`
(NO+NO races visited cheapest asks sum first, then most capital; and `unwind_is_safe` no longer refuses a NO+NO buy-back because the legs'
risk fair values add up to > 1, a worst-case MARK artefact of d x (s - 1): party delta and settlement risk still checked); `pair_unwind_followup_max_age`
(0 = off; 600 staged: an owed record the write budget defers every cycle is alerted and cleared instead of blocking its race for ever). A leg
REFUSED inside the batch is owed like a zero fill and followed up as a covered sale of its lone part.

**Item 3, the tilt exits (tests/test_tilt_exit.py 62; measured in SIM_NOTES "Round 8": the live state as the world, `_rival_anchor` 1, tilt 0.11
+ 0.0036/h, `_bg_wc` 39000 + 40000/h - 4000/h, 0 free cash, BASE = the live overrides, 8 seeds x 3 h quiet, paired, d vs BASE, judge `pnl_lag`
then `pnl_liq`).** A "tilt exit" = the side shrinking a position whose pos x (r - c) has the sign of tilt_exposure (live: sign(pos) = sign(r - c)).
Nothing crosses: priority is ordering only, full size changes size only (race-net clip, position / cash / covered-NO caps still apply), the guard
flags only unblock a side, capped at the position.
| Flag | d pnl_lag | d pnl_liq | d tilt_exposure_end | d cap_end | d writes_pm | Verdict |
|---|---|---|---|---|---|---|
| (a) `tilt_exit_priority` (tilt exits sort after headline, before other deferred changes) | +14 ± 21 | +1 ± 53 | +342 ± 530 | -0.003 | +0.18 | untestable in the sim (it defers 41/h, live 132 per CYCLE); no risk |
| (a) + `tilt_exit_full_size` (the exit sized at the whole position) | +64 ± 42 | +67 ± 69 | -638 ± 890 | -0.032 ± 0.015 | +0.37 | positive, frees 3 points |
| (c) `ref_guard_tilted` (guard gap from r', not raw Polymarket) | +110 ± 49 | +130 ± 100 | -615 ± 1,100 | -0.036 ± 0.025 | +0.65 | positive (2.2 SE at the exchange mark) |
| (c) `ref_guard_exits` (the guard never blocks this market's own exit) | **+139 ± 37** | **+177 ± 68** | -712 ± 1,000 | -0.038 ± 0.021 | +0.74 | positive (3.8 SE) |
| **all four together** | **+206 ± 55** | **+275 ± 91** | **-2,340 ± 920** (-30% of the sim's 7.9k) | **-0.071 ± 0.021** | +1.48 (4.2 -> 5.7) | **PASS: stage 3** |
| (b) `adding_factor_capital_on` 0.90 / 0.95 / 0.98 (+ resume 0.5) | +263 / +255 / +343 (± 60-110) | +214 / +242 / +283 | **+2,440 / +2,270 / +2,210** | +0.03 / +0.03 / +0.04 | +5.4 / +7.2 / +7.6 | earns spread, rebuilds exposure and +6k worst case |
mk15_mid not worse for any of the exit flags (+0.02 ± 0.16 together): the exits are not picked off. (a) `tilt_exit_full_size` must go with
`adding_factor_per_market` (without it the race-netted adding side can zero a tilt exit at factor 0; a start-up warning says so). (c)
`ref_guard_exits` keeps the guard within `ref_jump_cooldown_seconds` of a Polymarket move/jump and when the (tilted) gap exceeds 2 x
`ref_guard_gap`: Dem House (r 0.925, s 0.11 -> r' 0.878 vs book ~0.87) rests its exit; a real 8c move still trips it.
**Answer to (a):** the four flags together (stage 3). **Answer to (c):** yes, both ways (measure the gap from r' AND exempt the exit), with the
jump limits above. **Answer to (b), the recommendation:** keep the adding factor at 0 for the first 3 h of stage 3; then, if `tilt_exposure`
has fallen and `tilt_s` < `ref_tilt_max` (the estimator at the clip under-corrects a tilt that is above it, which is why adds rebuild exposure
here), resume at 0.95 (`adding_factor_capital_on` 0.95, `capital_ceiling_adding_size_factor_resume` 0.5, hysteresis 0.01: adds come back below
95% capital, stop again at 96%); the threshold itself hardly matters (0.90-0.98 within noise); the money says resume, the one-sided tilt
bleed (-356 per point) says not before the exits have cut it. `capital_ceiling_adding_size_factor` stays 0 in every staged file.

**Red team (opus) on the merged branch: 1 high (item 2's changes were not behind flags while the live config satisfied Package 7's gate -> own
OFF flags + identity test), 4 medium (cash gate bookkeeping: refunds for orders not placed, stale-read handling, log throttling; `ref_guard_exits`
without jump limits; `tilt_exit_full_size` without per-market), all fixed. 33 suites green: test_cash_gate 83, test_tilt_exit 62, test_pair_sizing
29, test_nono_sets 103, test_pair_followup 42, test_reduce_no 69, test_mm_bot 600, test_live_sim_marks 115, test_stress 20 + STRESS_LADDER=1
20/20, the rest as Package 7; settings-order checks are now contiguity checks; py_compile under Python 3.10.
**Deploy (owner; files in `deploy/package8/`, base = your live overrides, nothing resets the adding factor or drops `ref_tilt_headline`):** code by
handover restart (stage0: every new flag off = Package 7 + status fields) -> stage1_cash_gate (`cash_gate_enabled`, `adding_factor_per_market`)
-> stage2_pair_sizing (+ `pair_no_unwind_max_per_cycle` 1, `pair_unwind_race_order`, `pair_no_unwind_max_sets` 1000, `pair_unwind_followup_max_age`
600) -> stage3_tilt_exits (+ `tilt_exit_priority`, `tilt_exit_full_size`, `ref_guard_tilted`, `ref_guard_exits`) -> after 3 h, (b) as above by hand.
**Watch. Stage 1, first 10 min:** 400 refusals -> ~0 within a cycle (any that remain: the journal names the order; send it to the executor);
`cash_gated` / `cash_trimmed` counting, `cash_gate_read_age` < 60 s; covered sales and pair batches still going out; no quote lost on a market
whose order needs no cash (asks on YES holdings, covered NO sales); writes freed (~5/min). **Stage 2, first hour:** "PAIR UNWIND (short set)"
once per cycle, cheapest race first, sets up to the smaller leg; `pair_owed` clearing; an "owed record cleared" alert only on a stalled race.
**Stage 3, first hour:** Dem House / Rep House and the biggest toward-Polymarket positions have an exit RESTING at the book (recorder our_ask /
our_bid present); `tilt_exposure` falling (sim: -30% in 3 h on its share); capital share falling (sim -7 points); no own bid >= own ask; writes
<= 28; account on the exchange's number not below start - 300; `mk15`-type pick-off not visible (fills on exits within 2 min of a >= 3c
Polymarket move should stay rare: the jump limits hold). **Rollback (previous stage's file) on:** refusals not falling at stage 1 (then the
gate's cash read is wrong: `cash_gate_read_age` growing or the alert); a covered sale or pair leg blocked by the gate (it should never be:
need 0); an exit filled through a Polymarket jump repeatedly; account below start - 1,000 in 3 h; 429s.
**Untested:** the exchange's `cashBalance` as the gate's input against the real exchange (the fake API's cash model is the evidence; the gate reads availableBalance / cashBalance
/ cash / balance, else account value - positions' market value, less the cash our resting orders lock; no figure -> alert, cash-free orders
only, gating stops after 5 min); set collateral and cash are not in live_sim
(items 1-2 are unit-test evidence); `tilt_exit_priority` under the live deferral load; the (b) resume live.

## Package 7 (READY, Finisher 2b, 3 Oct ~15:30 UTC; branch `claude/finisher-package7` from Package 6 706e763; draft PR #9): NO+NO sets unwound as a pair, set-aware covered bids, pair-unwind follow-up (all OFF; staged files in `deploy/package7/`)
Live at the time of writing: Package 6 with `reduce_no_as_sell` on since 14:01:54 UTC (the covered-sale check accepted 14:02:24); `ref_tilt_max`
moving to 0.11 (`tilt_diag` live: slope 0.107 / median 0.100 / wls 0.091 / pinned_weight 0.083, so the 0.14 was the restored EMA, as suspected).
**The finding (owner, 14:0x):** 47 of the 49 remaining 400 "Insufficient available funds" refusals are on the 38 races where we hold NO on EVERY
leg (FL-16 -817 / -975, Alaska Senate, WI-03, MI-10, ...). The exchange collateralises NO+NO as a set ("collateralSavings"), so a covered sale of
one leg breaks the set and needs cash. Second finding (14:11:55): the existing short-set pair unwind on Hawaii Governor filled [314, 1069] and left
755 shares unpaired.
**Built (each OFF; `_max_per_cycle` 2 and `_followup_*` are companions):**
- A `no_set_aware_bids`: with `reduce_no_as_sell`, a covered bid on a leg of a race where every other leg also holds NO is capped at the leg's LONE
  part, lone_i = max(0, NO_i - max over the other legs of NO_j) (the exchange's worst-case collateral model; 2-leg: |inv| - min|inv|, so the
  smaller leg gets no bid and the bigger leg bids its excess). Take paths and the ladder follow the same cap; the sell-NO start-up check only tests
  legs with a lone part (a once-per-run warning if none exists for 10 min). Pinned: -817 / -975 -> 0 / 158; [10, 8, 5] -> [2, 0, 0]; NO on 2 of 3 legs -> [0, 200].
- B `pair_no_unwind_max_cost` (-1 = off; 0.003): NO+NO sets are unwound AS A PAIR through the existing short-set buy-back in `arb_plan` /
  `execute_arbitrage`: one batch of immediate-or-cancel covered "sell NO" orders on every leg (both legs converted or nothing is sent), sized
  to the smaller leg and to the joint depth within the limits, when the YES asks add up to <= 1 + max_cost (a cost of at most 0.3c per set vs
  holding the set to the close); `unwind_is_safe`, the 30 s cooldown and the write budget as before; at most `pair_no_unwind_max_per_cycle` (2)
  such unwinds per cycle (5 writes each). It fires only after the start-up pair check C has passed.
- C start-up pair check (`pairno_tick/run/finish`): after the sell-NO leg has passed and a NO+NO race is held, ONE batch of two 1-share
  "sell NO @ 0.995" (a YES bid at 0.005 on each leg; only where neither leg's book has an ask at or below 0.005, so neither can fill), both
  cancelled at once. Accepted -> B enabled for the run; refused -> alert "the exchange refuses a paired NO sale: NO+NO sets cannot be unwound
  without cash", B off for the run (`reduce_no_as_sell` stays on), bot keeps running; busy -> retry with back-off (60 s doubling to 30 min).
  Overlap guards with the main self-test and the sell-NO leg. `pairno_state` in status.json.
- D T2.5 `pair_unwind_passive`'s SHORT-set branch (rest a bid on one leg, take the other on fill) is a single-leg covered sale that breaks the
  set: skipped while covered sales are in effect (logged once). Its long-set (YES+YES) branch is untouched; whether the exchange also nets
  YES+YES sets is unverified.
- F `pair_unwind_followup` (+ `_max_cost` 0.01, `_tries` 6): every pair unwind sizes its legs to what can fill together (joint depth within the
  limit, the smaller leg, the planned sets); if the legs still fill unequally, the lagging leg(s) are owed and followed up on later cycles with
  one immediate-or-cancel order per cycle at <= planned + 1c (a covered sell NO where applicable, capped at the leg's post-fill lone part), for
  up to 6 tries, then one alert with what is left; `pair_owed` in status.json; `take_arbitrage` skips a race with owed legs; a restart drops the
  owed record (logged at shutdown). live_sim mirror `pair_followup` (the sim's legs fill equally, so it is pinned with a stub).
- E status.json `nono_sets` {races, sets, capital} and the 2-hourly summary " | NO+NO sets N (cap X)".
**Simulator evidence:** none for A/B/C (live_sim does not model the exchange's set collateral; the fake exchange does, opt-in, and the unit tests
are the evidence). The behaviour with every flag off is pinned byte-identical against Package 6 on a grid of `cover_no_qty`, `no_sell_order`,
`arb_plan`, `execute_arbitrage` and two full cycles.
**Red team (opus):** 0 high, 1 medium (the follow-up owed amount ignored the post-fill lone part: fixed), 5 low (3-leg collateral model, write
budget of B's first cycle -> `_max_per_cycle`, KeyError on a vanished leg, D keyed on the setting not the effective state, start-up check inert
when no lone part exists: all fixed/noted). 30 suites green: test_nono_sets 103, test_pair_followup 42, test_reduce_no 69, test_mm_bot 600,
test_stress 20 + STRESS_LADDER=1 20/20, the rest as Package 6; py_compile under Python 3.10.
**Deploy (owner):** code by handover restart (all new flags off = Package 6 + status fields), then `deploy/package7/settings_override.stage1_set_aware_bids.json`
(A + F), then stage2 (+ B). **Watch, first 10 minutes after stage 1:** 400 "Insufficient available funds" refusals on NO+NO races falling to ~0; the
smaller leg of each NO+NO race has no bid resting, the bigger leg bids its lone part; the journal's `pairno` check line (placed and cancelled, or
the refusal alert); `nono_sets` in status.json (expect ~38 races). **After stage 2, first hour:** "PAIR UNWIND (short set)" lines at asks sums
1.000-1.003, both legs as side no / action sell in one batch, fills equal (or `pair_owed` clearing within a few cycles); NO+NO races count and
capital falling; no leg left one-sided (compare the two legs' positions after each unwind); writes <= 28; account on the exchange's number
not below start - 300. **Rollback triggers:** a pair check refusal (B stays off by itself: nothing to roll back), a covered sale that increased a
NO holding, an unwind that leaves > 1 leg-imbalance after 6 tries repeatedly, 429s, account below start - 1,000 in 3 h.
**Untested:** the real exchange's set-collateral rule (the pair check is the guard; the worst-case model for 3-leg races is an inference);
whether resting covered sales on both legs in one QUOTE batch are accepted (A only sells lone parts, so it does not rely on it); YES+YES netting;
the live_sim mirrors do not model set collateral or joint sizing.

## Package 6 (READY, Finisher 2b, 3 Oct ~10:40 UTC; branch `claude/finisher-package6` from Package 5 c14c92b; draft PR #8): the live deadlock fix (`reduce_no_as_sell`), the self-test funds hot-fix, the tilt-estimator diagnosis, and five more flags OFF
Evidence: `SIM_NOTES.md` "Round 6" and "Round 6b", `analysis/poly_bias/` (P6_RESEARCH.md, NO_REDUCE_QUOTE.md, TILT_ESTIMATOR.md), `PLAN_P6.md`.
Staged override files: `deploy/package6/`. Live at the time of writing: Package 5 c14c92b + the owner's self-test hot-fix, stage 1 on (`ref_tilt_enabled`,
`ref_tilt_max` 0.09), capital 100%, 0 free cash.

**1. The deadlock (owner's item 1) and its fix.** Every order the bot built used `"side": "yes"`, so reducing a NO (short) holding was sent as
"buy YES @ p", a cash purchase, and at 0 free cash the exchange refused it (459 refusals with 400 "Insufficient available funds" in 30 min,
351 of them on NO holdings; the biggest shorts had no quote resting; the refusals also ate the write budget: 132 of 150 changes deferred per
cycle). `reduce_no_as_sell` (OFF by default; the owner switches it on): a bid that buys back a NO holding goes out as a covered
`{"side": "no", "action": "sell", "price": 1 - p, "quantity": min(qty, NO held)}` through one send point (`Bot.wire_order` / `place_orders`); the
part beyond the NO held waits (capped, not split); the take paths (`execute_take`, `take_aged`, pair-passive second leg) are capped the same way;
arbitrage / short-set legs become covered sales only if the whole leg fits. Orders stay in YES terms inside the bot: `parse_order` already read
"sell NO @ q" as our bid at 1-q, so order notes, adoption at handover, duplicate detection, keep/replace and the recorder see a bid (tested, not
changed); 1-p is exact on the 0.5c grid (no re-price churn); fills.csv keeps its columns (`our_side` bid, `fill_price` the NO price as before).
**The API does not document whether "sell NO" on a held NO position needs cash**: it is inferred by symmetry from "selling shares we already
hold locks no cash" and "the engine turns sell YES into buy NO when we don't hold the YES". So a start-up leg guards it: one 1-share
"sell NO @ 0.995" on the biggest NO holding whose book has no ask at or below 0.005 (it cannot fill), cancelled at once; refused -> alert,
feature off for this run, bot keeps running; busy -> retry. Covered sales start one cycle after the leg passes. The fake exchange models the
cash rule (a "sell NO" beyond the NO held is refused like a purchase). Simulator: `_short_reduce_locks_cash` 1 reproduces today's live cash
model (every earlier round's world is the fixed one, so Round 5's T2.1 numbers assume this fix). **Measured from the live start, capital 1.0,
0 free cash, T2.1 on at 0.09, 8 x 3 quiet (d vs the deadlocked bot): exchange-style +420 ± 90, liquidation +384 ± 90, Polymarket mark
+333 ± 38, shares +30k ± 5.5k, worst case -2.0k ± 1.1k, writes +2.1 ± 1.3 (16.2 -> 18.3), capital unchanged (freed cash is redeployed at once);
live-pinned world (reduce-only ~83%): +34 ± 78 / +66 ± 82, capital -2.1 ± 1.0 points, writes +0.3.** Red-teamed (opus): 0 high, 1 medium (the funds-refusal retry loop: fixed with back-off), 4 low (all fixed).
**2. The owner's live hot-fix, re-implemented** (`Bot.selftest_funds_refusal`): a self-test batch refusal whose message contains "insufficient"
and "fund" (string or dict error, top-level error) counts as busy, retry with back-off 60 s doubling to 30 min, one alert per doubling,
`selftest_state` "waiting_funds" in status.json; busy only if EVERY refused order is a funds refusal (a real rejection still exits 3).
tests/test_selftest_funds.py 49.
**3. The tilt estimator reads 0.14 live vs ~0.09 rebuilt (owner's item 3).** On the newest data here (2 Oct 20:37) the bot's exact sample and
formula read 0.067 vs 0.062 from the plain mid: +8%, not +50%; race normalisation adds +0.004 (the 3-leg Dem RI Governor the most), depth
filtering +0.001; `r` is the raw Polymarket mid. The ONE mechanism found that turns 0.09 into 0.14 is tail markets whose book price sits at
the ±8c winsor (each reads an implied tilt ~0.17, and markets with |x| > 0.45 carry 59% of the slope's weight; pinning half / all of them
gives 0.097 / 0.128). The bot prices from full-depth books while the recorder keeps 3 levels, so a thin tail book that is None in a rebuild
can be a far-out price in the bot. NOT proven: it needs 3 Oct books. The "fresh 0.14 after a restart" is the saved estimate restored from
status.json (the EMA takes ~2 h to forget it), not a new reading. Added: `ref_tilt_estimator` "slope" (default, unchanged) / "median" (median
per-market implied tilt over |x| > 0.1) / "wls"; status.json `tilt_diag` (raw slope / median / wls, n, `pinned_weight`). **Which is right for
quoting:** the five biggest live positions (Dem House 0.925, Rep House 0.075, Rep RI Senate, Rep NH Governor, Rep GA Senate) each imply a tilt
of 0.12-0.15, so s = 0.14 puts the bot's fair value roughly AT their mid, while 0.09 leaves it 1.3-2.4c on the Polymarket side (the exit
quote still behind the book). Recommendation: keep `ref_tilt_max` 0.09 until `tilt_diag` is read on live books; if it shows slope - median
> 0.02 with `pinned_weight` > 0.2, switch `ref_tilt_estimator` to "median" (live-overridable); if slope and median agree near 0.14, raise
`ref_tilt_max` to 0.15.
**4. Dem U.S. House with no quote on either side (owner's item 4): cause found, nothing changed.** The reference guard (`decide` ~4763-4766,
`ref_guard_gap` 0.05) compares Polymarket with the race-normalised book (0.861 / 0.139 vs 0.925 / 0.075: 6.4c on both legs) and sets `no_ask`
on Dem House and `no_bid` on Rep House, exactly the reducing sides; reduce-only blocks the adding sides, so nothing rests. Reproduced and
pinned (tests/test_house_quote.py 19). The guard blocking exits is documented design ("don't sell it to anyone here, they probably know").
Not the race-net clip (long Dem + short Rep is one bet: eff ±19,272), not the write budget (headline first), not the headline limits. Also:
with the guard off, the Dem ask would be 10,000 shares against a 9,876 position, so 124 would be a "buy NO" needing cash (refused at 0 cash).
Owner options: `ref_guard_gap` 0.07 (overridable) lets both House exits rest; or a flag exempting the side that shrinks THIS market's own position
from the guard in reduce-only (~3 lines, not built: it is a judgement about informed flow, not a bug).
**5. Also on the branch, all OFF, none clearly positive in the simulator (SIM_NOTES Round 6 / 6b):** `exit_quotes_in_reduce_only`,
`pair_passive_in_reduce_only`, `backstop_soft_frac` (fails on writes), `tail_adding_factor`. Live-pinned world (base reduce-only 83% of cycles):
T2.1 alone is neutral there (-163 ± 125 / -30 ± 111), so Package 5's gain depends on how pinned the live bot stays; the deadlock fix is what
lets it stop being pinned.

**Deploy (owner):** code by handover restart (all new flags off = Package 5 + the hot-fix), then `deploy/package6/settings_override.stage1_reduce_no_as_sell.json`.
**Watch, first 10 minutes:** the journal's self-test leg line ("sell NO" placed and cancelled, or the refusal alert: if refused, the feature is off
for the run and nothing else changes); `selftest_state` "passed"; then covered bids appearing on the NO holdings (orders listed as side no /
action sell at 1-p; the recorder's `our_bid` = p); 400 "Insufficient available funds" refusals falling toward zero; deferred changes per cycle
falling from ~130; writes <= 28; no own bid >= own ask. **First hour:** fills on NO holdings (the biggest shorts shrinking: Rep House -9,396,
NH Governor, GA Senate, HI Governor, NH Senate, NM Governor, RI Senate); free cash above 0; capital share below 100%; account on the exchange's
number not below start - 300. **After 3 h:** exit ratio >= 1.0 bot-wide, capital share down >= 3 points, reduce-only share down, account not
below start - 500. **Rollback triggers:** a covered sale that increased a NO holding (position more negative after a fill of ours on that side:
it must never happen), any 400 refusal of a covered sale with cash free, fills.csv `our_side` wrong for these fills, own bid >= own ask, 429s,
account below start - 1,000 in 3 h. Rollback = the stage0 file (note: that re-sends "buy YES" for the small covered bids, refused at 0 cash).
**Untested:** the real exchange's treatment of "sell NO" on a held NO position (the leg is the guard); the simulator's take and arbitrage mirrors
do not model cash; the ladder (off) with covered bids; a stale `ex.inv` within a cycle can over-size one covered sale (refused, re-placed).
**Suites (28 files, all green):** test_mm_bot 600, test_live_sim_marks 115, test_strategy 114, test_turnover 94, test_write_savers 75, test_hold_target
71, test_reduce_no 69, test_fast_unload 61, test_pair_passive 57, test_tilt_rampin 54, test_mark_frag 52, test_selftest_funds 49, test_reduce_book 49,
test_tail_factor 48, test_tilt 69, test_exit_in_ro 40, test_ops_liq 39, test_recorder_refill 37, test_ref_prices 37, test_behind_best 36,
test_backstop_soft 35, test_tilt_limit 33, test_take_tilted 31, test_carry_ramp 29, test_reduce_book_scope 26, test_gap_shrink 25, test_house_quote 19,
test_stress 20 + STRESS_LADDER=1 20/20; py_compile under Python 3.10.

## Package 5 (READY, Finisher 2b executor, 3 Oct ~03:00 UTC; branch `claude/finisher-package5`, draft PR #7): the Polymarket-bias fix. Package 4 (c29f762) + a tilt-corrected reference (T2.1) and nine more settings, ALL OFF by default + an honest simulator yardstick + ops fields. Code deploy by handover restart, then flags by settings_override.json (deploy/package5/).
Plan: `PLAN_POLY_BIAS.md` (v2). Evidence: `SIM_NOTES.md` "Round 5" (every run, with error bars) and `analysis/poly_bias/` (RESULTS.md,
TIER2_RESULTS.txt, TAKES_RESULTS.md, SNAPSHOT02B_RESULTS.md, P6_RESEARCH.md). Raw simulator output: `tests/live_sim_round5_*.txt`.
Run plan and actuals: `PLAN_FINISHER.md` "Package 5". Staged override files and their order: `deploy/package5/`.

**Diagnosis (live snapshots to 08:14 and 20:37 UTC, 2 Oct).** The tournament prices every market with ONE favourite-longshot tilt:
tournament mid ~ c + (1 - s)(Polymarket - c), c = 1/legs. s by 4-hour bin since 1 Oct 16:00: 2.4, 3.4, 4.7, 5.1, 5.4, 5.2, **6.3%**
(R2 0.60-0.68; 0.1-0.2 points higher with our own best quotes excluded, so we do not cause it); growing ~0.16 points/h on average, flat
08-16 UTC, rising again after 16:00. That one number explains 60-68% of the Polymarket-tournament gap variance; the tilt part of a gap
WIDENS over time (-0.14 at 4 h, -0.22 at 8 h), only the residual closes (~0.3). The bot, anchored 70% on raw Polymarket, treats the tilt
as mispricing: 88% of open position capital was entered toward Polymarket (76% at 08:14); 56% of toward-entered capital has exited vs 96%
of against-entered; open capital median age 8.3 h; tilt exposure (sum pos x (Polymarket - 0.5)) **+32,742 at 20:37 vs +15,087 at 08:14**,
each point of tilt marking the book -327; position capital 100% of the account. Marks at 20:36: exchange 101,004 / liquidation 101,986 /
tournament mid 102,385 / Polymarket 107,316: the "+6.3k Polymarket gain" is a claim on gaps that have only widened; the exchange marks
~1.4k below the mid (a lagged/smoothed trade price; our fills move it ~0.05c each). Stale-quote takes since 08:14 (38, 24,958 shares):
+1,480 at Polymarket, **+154 at the tournament mid**. Since Package 3 (16:26) the bot is reduce-only in 81% of cycles (sum-of-maxima worst
case 77-82k cycling against the 0.80 backstop); cash binds too (positions 99.9k of a 101.0k account).

**The yardstick (Phase 1; `tests/live_sim.py`, pins `tests/test_live_sim_marks.py` 110).** Every earlier round marked P&L at Polymarket
in a world whose rival bots also priced from Polymarket. New world knobs: `_rival_anchor` (1 = rivals and informed takers price from the
tournament consensus), `_world_tilt` / `_world_tilt_growth` (the tilt's share of the real starting gap and its growth; the start book is
always the real one), `_world_tilt_add` (extra tilt), `_bg_wc` (the rest of the account's worst case so the live reduce-only backstop
binds). New fields: `pnl_lag` (VWAP of the last 30 min of prints: the exchange-style mark, THE JUDGE for rank), `pnl_liq` (start and end
at liquidation: longs at the best other bid, shorts at the best other ask), `pnl_mid`, `mk15_mid` (markout vs the consensus), `exit_ratio`,
`hold_med`, `ro_frac` (share of cycles reduce-only), `tilt_s_end`. Knobs at 0 = the old numbers to the digit. In the old world the
Polymarket mark overstated base P&L by ~1,440 per 3 h (pnl 1,590 vs pnl_liq 152). Worlds: "tilt" (anchor 1, tilt 0.05 + 0.002/h),
"pinned" (+ `_bg_wc` 38000: reduce-only 42% of base cycles), "high-tilt" (+ 8 points), "flat", "old", "news".
**Earlier conclusions re-scored (tilt world, 8 x 3 quiet, d pnl_liq):** none flipped outright. `fast_unload_enabled` -165 ± 110 -> -22 ± 100
(its loss was the mark; still no gain), `reduce_join_best` -48 ± 81 -> -23 ± 120, `turnover_control_enabled` +49 ± 62 -> +67 ± 120,
ceiling factor 0.25 vs 0.5: -262 ± 100 -> -162 ± 110 (keep 0.5). None passes the new rule (none frees 5 points of capital).

**What is in the code (all OFF; each in OVERRIDABLE unless said; `_headline` = staging gate, False = not in `headline_races`):**
T2.1 `ref_tilt_enabled` (+ `ref_tilt_headline`, `ref_tilt_min_markets` 50, `ref_tilt_halflife_min` 30, `ref_tilt_max` **0.20**, `ref_tilt_winsor` 0.08,
`ref_tilt_rampin_min` **0**): quote around r' = c + (1 - s)(r - c); s estimated each cycle from the liquid non-headline cross-section
(`TiltEstimator`, runs read-only with the flag off; `tilt_s`, `tilt_s_applied`, `tilt_s_applied_headline`, `tilt_exposure`, `tilt_state`
in status.json; s and the ramp clocks restored from status.json on a restart if it is < 10 min old). X12 `take_tilted_ref`: the stale-quote
takes measure their 5c edge from r' (needs T2.1). A `reduce_from_book` (+ `_pause_s` 120, `_headline`; X11 `reduce_from_book_dead_only`,
`reduce_from_book_max_turnover`): the reducing side priced from the book, with the self-cross cap. B `kelly_edge_cap`. C `hold_target_hours`
(+ `hold_unload_budget_frac` 0.02, `hold_take_max_per_min` 2, `hold_target_headline`): aged lots join the best; older than 2T taken inside a
budget that starts fully used for an hour. T2.4 `tilt_exposure_max_frac` (+ `_headline`). T2.5 `pair_unwind_passive` (+ `pair_unwind_max_cost`
0.003). X5 `gap_size_shrink` (+ `gap_size_floor` 0.25). T2.3 `ref_tilt_carry_days` (NOT overridable: hard gate until SIG answers the
end-valuation question). Ops (no flag): status.json `liquidation_value`, `liquidation_unpriced`, `realised_pnl` (+ `_scope` "maker fills
only"), `unrealised_pnl`, `pnl_unreconciled`, `toward_ref_capital_frac`, `capital_over_6h_frac`, `exit_ratio_24h`; the 2-hourly summary line
"Liquidation ... realised ... toward Polymarket ... older than 6 h | tilt ..., exposure ..."; recorder `account.liquidation_value` (nullable,
ALTER on old files).

**Results (d vs BASE = Package 4 defaults + the live overrides, paired seeds, ± 1 SE; judge = `pnl_lag` then `pnl_liq`; full tables with every
column in SIM_NOTES "Round 5 summary tables"). Pass rule: d pnl_liq >= -1 SE, capital down 5 points, worst case down, writes <= 28 and
deferred not up > 20%, `mk15_mid` not worse by > 1 SE, exit_ratio up.**
| Flag (all OFF by default) | d pnl_lag / d pnl_liq, tilt world 8 x 3 quiet | pinned 16 x 6 (live-like) | news 6 x 6 | capital (8 x 3) | writes/min | Verdict |
|---|---|---|---|---|---|---|
| **T2.1 `ref_tilt_enabled`** | **+166 ± 72 / +163 ± 60** (ramp 20: +176 ± 110 / +199 ± 83) | **+509 ± 110 / +601 ± 107** (even the Polymarket mark +341 ± 94) | **+670 ± 180 / +761 ± 160** | -7.6 pts | -1.2 (pinned +2.4: it leaves reduce-only) | **PASS: the recommendation.** Old world +215 ± 74 / +244 ± 94, flat tilt -60 ± 59 / +100 ± 84, free 16 x 6 +330 ± 64 / +426 ± 63 |
| T2.1 + `worst_case_backstop_frac` 0.85 | - | +587 ± 112 / +649 ± 101; vs T2.1 alone **+78 ± 86 / +48 ± 81** | - | -6.0 pts | +3.1 | not clearly positive: stays 0.80 (stage 4 optional, later) |
| T2.1 + `reduce_only_hysteresis` 0.01 | - | +456 ± 123 / +523 ± 117; vs T2.1 alone **-57 ± 60 / -83 ± 66** | - | -5.7 pts | +2.7 | no: stays 0.03 |
| T2.1 + `ref_weight` 0.5 / 0.35 (T2.2) | +87 ± 82 / +176 ± 50; +139 ± 130 / +186 ± 103 | - | - | -9.7 / -13 pts | -0.0 / -0.4; deferred +18% / +22% | no-go: not >= 1 SE over T2.1, markout worse (-0.056 / -0.076c) |
| X12 `take_tilted_ref` | not simulable (no take path in live_sim) | | | | | data: the 38 live takes since 08:14 earned +154 at the tournament mid on 25k shares: recommended with T2.1 |
| A `reduce_from_book` (+ `_pause_s`, `_headline`) | +36 ± 150 / +91 ± 135; with T2.1 +42 ± 113 / +124 ± 90 | - | - | -12 / -14 pts | -0.5 / -1.5 | FAIL: markout -0.23 / -0.17c (sells to informed flow), no P&L over T2.1 |
| X11 `reduce_from_book_dead_only` | with T2.1 -45 ± 109 / -26 ± 109 | - | - | -9.2 pts | -0.8 | FAIL: below T2.1 alone |
| B `kelly_edge_cap` 0.01 + `kelly_max_market_frac` 0.01 + `headline_position_frac` 0.05 | **-312 ± 61 / -216 ± 19** (stopped at 4 seeds); with T2.1 -215 ± 119 / -89 ± 102 | - | - | -6.9 pts | -4.0 | FAIL: gives up spread income everywhere. Do NOT apply the planners' "tonight" overrides |
| X5 `gap_size_shrink` 0.03 | with T2.1 +110 ± 118 / +64 ± 85 | - | - | -7.8 pts | **+3.6**, deferred +4,300/h | FAIL: below T2.1, smaller orders re-price more |
| C `hold_target_hours` 4 (+ budget, 2 takes/min, `_headline`) | with T2.1 +136 ± 150 / +175 ± 130 (before takes were charged writes) | - | - | -11.2 pts | -0.9 | neutral vs T2.1 alone; the capital-freeing backstop; Package 6 candidate in reduce-only |
| T2.4 `tilt_exposure_max_frac` 0.10 / 0.25 | with T2.1 **-326 ± 96 / -331 ± 86** (stopped) / never binds in the sim (= T2.1) | - | - | | -12.3 / 0 | FAIL at 0.10 (the live book starts at 15% of the account); 0.25 untestable here |
| T2.5 `pair_unwind_passive` (+ `_max_cost` 0.003) | with T2.1 +95 ± 82 / +87 ± 74 (before takes were charged writes) | - | - | -7.9 pts | +0.2 | not positive in 3 h; frees the 7.3k Senate set; OFF, Package 6 candidate in reduce-only |
| `ladder_enabled` with T2.1 | +155 ± 77 / +135 ± 66 | - | - | -7.5 pts | -1.0 | no gain over T2.1 (cash still binds); OFF |
| T2.3 `ref_tilt_carry_days` | unit tests only | | | | | HARD GATE: 0, not overridable, until SIG answers the end-valuation question |
| re-scored old flags: `fast_unload_enabled` / `reduce_join_best` / `turnover_control_enabled` / ceiling factor 0.25 | +120 ± 95 / -22 ± 102; +40 ± 111 / -23 ± 122; +170 ± 106 / +67 ± 121; -100 ± 112 / -162 ± 108 | - | - | -0.6 / -2.0 / -1.2 / -0.4 pts | | none passes (no flip of an earlier verdict; fast_unload's old loss was the mark); all stay as they are |
Reading: removing the tilt from fair value (T2.1) is the one change that pays at the exchange-style mark AND at liquidation in every world
(free, pinned, news, flat, old), frees capital and saves writes. Everything that only makes the exit more aggressive (A, X11, lower
ref_weight) sells into informed flow for no extra P&L; everything that only shrinks size (B, X5, T2.4 at 0.10) gives up spread income; C,
T2.5 and T2.4 at 0.25 add nothing over T2.1 in 3 h. The "+163 / +166" is 3 h on the 74 biggest markets (~+1,300 per day on the sim's share
of the book) before anything the sim does not model (the other ~160 markets, the owner's manual trades, SIG's end valuation).

**Verdicts on the owner's three questions (each with T2.1, pinned world):** `worst_case_backstop_frac` 0.85: NOT NOW. Pinned 16 x 6 with T2.1: +587 ± 112 / +649 ± 101 vs T2.1 alone +509 ± 110 / +601 ± 107, paired difference +78 ± 86 (exchange-style) / +48 ± 81 (liquidation), +0.7 writes/min; the 8 x 3 "+180" was noise; the live data says cash binds before the backstop and the reduce-only churn cost -87 net (P6_RESEARCH). Not clearly positive = stays 0.80; revisit after a day of T2.1 (the stage-4 file exists for that).
`reduce_only_hysteresis` 0.01: NO. With T2.1, paired: -57 ± 60 / -83 ± 66, +0.3 writes/min; alone (8 x 3): +22 ± 140 with deferred changes +37%. Stays 0.03. `ref_tilt_max`: 0.12 binds at a 15% tilt (the estimate sits at the clip while the world is at
0.15) for the same 3-h P&L as 0.20 (+299 vs +302 at liquidation, +316 vs +351 exchange-style, within noise; 0.20 costs +2.7 writes/min);
default set to 0.20 so the estimate stays readable and the slow mark bleed of untracked tilt (-327/point/day live) is not accepted by design.
Live s is 6.3%: alarm, re-measure (analysis/poly_bias/snapshot02b.py) if `tilt_s` passes 0.12.
**Ramp-in:** measured and not needed (default 0): first 2 h, step-on vs 120-min ramp: exchange-style +70 ± 39 vs -33 ± 39, liquidation
+174 ± 55 vs +38 ± 71, Polymarket-marked cost the same (-107 ± 25 vs -112 ± 28); the step-on sells ~5k shares of inventory at tournament
prices in 2 h and re-prices ~157 markets once (~10 min of the write budget); the ramp keeps buying the tilt for 2 h and re-prices
continuously (+0.8 writes/min, deferred +1,070/h). The setting stays (20 min: +176 ± 110 / +199 ± 83 at 8 x 3, pick_cost +9 vs +38: the same P&L as the step-on with the re-price wave spread over ~10 cycles, so 20 is the default; 0 and 120 measured as above).
**Recommended settings and order (deploy/package5/*.json; each file read within `overrides_seconds` 30 s):** stage 0 code (all off) ->
stage 1 `ref_tilt_enabled` -> stage 2 + `take_tilted_ref` -> stage 3 + `ref_tilt_headline` -> stage 4 + `worst_case_backstop_frac` 0.85
(NOT now: see the verdict; the file is there for later). Final file after stage 3: `{"arb_two_sided": false, "worst_case_backstop_frac": 0.8, "capital_ceiling_adding_size_factor": 0.5,
"writes_per_minute": 28, "writes_per_minute_max": 28, "burst_cycle_seconds": 60, "ref_tilt_enabled": true, "take_tilted_ref": true,
"ref_tilt_headline": true}`. Leave `reduce_only_hysteresis` 0.03 and `ref_weight` 0.7. Do NOT apply the planners' "tonight" overrides
(`kelly_max_market_frac` 0.01 / `headline_position_frac` 0.05: design B, -216 ± 19). Everything else stays OFF.
**Rollout, go/no-go rules (owner deploys; every number below is checked in status.json / the journal; "start" = the value at the moment
of the toggle).** Do each toggle in a quiet hour (03:00-07:00 UTC or 20:00-23:00 UTC: Polymarket moves and fills are fewest there).
Stage 0, code (handover restart, all flags off = Package 4 behaviour): check for 10 min: `tilt_s` printed and between 0.03 and 0.10
(expect ~0.06-0.08; a value stuck at 0.0 or at the `ref_tilt_max` clip for 15 min = the estimator is wrong, do NOT go to stage 1),
`tilt_exposure` printed (expect ~+30k to +35k), `liquidation_value` within ~1.5k of `account_value`, writes <= 28/min, `rate_limited_total`
0, no "refused override" alert, orders adopted by the handover kept. Then stage 1 at once if green.
Stage 1, `ref_tilt_enabled` (deploy/package5/settings_override.stage1_tilt_nonheadline.json). First 20 minutes: the applied tilt ramps
from 0 to the estimate (`tilt_s_applied` rising to `tilt_s`), so ~150 markets re-price toward the book in 2-3 steps (the write budget may
run at 28/min for a few minutes; deferred changes rise then fall back within 30 min); no own bid >= own ask anywhere (the recorder's our_bid < our_ask for every quoted market; one violation = rollback); no fills on a reducing side
within 2 min of a Polymarket jump >= 3c (the jump guard still applies); `tilt_s_applied` = `tilt_s` after 20 min.
After 1 h (GO to keep it on): (a) buy/sell fill balance: reducing shares / adding shares over the hour >= 1.0 bot-wide (live 16:26-20:37
was 0.84; sim: +0.1 to +0.16 on the ratio in 2-3 h) and in the 10 biggest toward-Polymarket positions the position is flat or smaller,
none bigger by more than 5%; (b) position capital share not above start (100% today) and falling by >= 1 point; (c) reduce-only share
of cycles below its start (81%); (d) account on the exchange's number not below start - 300 (0.3%); (e) writes <= 28/min, 0 x 429.
After 3 h (GO to stage 2): (a) exit ratio over the 3 h >= 1.0; (b) capital share down >= 3 points from start (sim: -5 to -8 in 3 h);
(c) reduce-only share down >= 15 points (sim: -33); (d) account on the exchange's number not below start - 500; realised P&L (FIFO,
`realised_pnl` in status.json) not below start - 300; (e) `tilt_s` still inside 0.03-0.10 and `tilt_exposure` below start.
Stage 2, `take_tilted_ref` (stage2 file): no quote change; stale-quote takes should fall to near zero in tail markets (journal "TAKE" lines:
live 38 in 12 h). Check after 1 h: no take in a market whose tilted gap was not confirmed (there should be none); fewer takes than the
previous hour. If takes continue at the old rate in tails, the tilted reference is not reaching the take path: rollback to stage 1.
Stage 3, `ref_tilt_headline` (stage3 file; the U.S. House and Senate legs: ~14-20% of position capital): the 4 legs re-price once by
~1c; the same 1-h checks on those 4 markets (Dem House long and Rep House short should shrink, never grow); account not below start - 300.
Stage 4, `worst_case_backstop_frac` 0.85 (stage4 file; only after stages 1-3 have held for 3 h): the bot leaves reduce-only at once if
the backstop was holding it; adding sides return on every market, throttled by the write budget (expect 5-10 min at 28/min). After 1 h:
worst case (`worst_case_loss`) below 0.85 x account and not rising more than 2k/h; capital share not rising above its stage-1 trend by
more than 2 points; exit ratio still >= 0.9. Hysteresis stays 0.03.
ROLLBACK (any stage, copy the previous stage's file; the flag is read within 30 s) on ANY of: a "refused override" alert or an own bid >= own ask;
`tilt_s` at a clip (0 or `ref_tilt_max`) for 15 min; account on the exchange's number below start - 1,000 within 3 h of a toggle (the sim
never moved the exchange-style mark by more than ~-100 in a 3 h run); writes above 28/min for 10 min or any 429 after the first 15 min;
exit ratio below 0.7 for an hour; capital share rising 3+ points in an hour; any take in a market whose gap was not confirmed. Rolling
stage 1 back to 0 is itself a one-step fair-value move (~150 markets, ~10 min of writes) and raises the sum-of-maxima worst case by
~0.5 points of equity: in the pinned state that can put the bot back into reduce-only for an episode. Code rollback (a handover restart
to c29f762 or 439ac54) only if the process misbehaves (exceptions in the journal, status.json not updating).
**Risks:** (1) SIG settles open positions at the outcome at the close: the tilt was then a yield the bot now declines (~+770 on the 08:14
book, ~+1.6k on tonight's); T2.3 exists for that case, gated. (2) The sim's informed takers trade toward the anchored consensus; if real
Polymarket moves carry faster than the data shows (pass-through 0.1-0.25 typical, fat tail, n small), T2.1's news gain shrinks. (3) The
switch-on re-price wave (~10 min of writes): quiet hour. (4) The estimator is cross-sectional over ~150 liquid non-headline markets (winsor
8c, min 50 markets, clip 0.20): a market-wide shift of the tails would move it; watch `tilt_s`. (5) The 74-market sim world's effective tilt
is ~7% (the biggest positions sit where the gaps are biggest). (6) The exchange mark lags the mid by ~2 h: the leaderboard will show
T2.1's gain late. (7) fills.csv prices: 30% of sells carry the YES price, not the NO price (P6_RESEARCH): `realised_pnl` uses quote prices.
**Untested:** T2.5's and C's take legs against the real exchange (fake API only); X12 end to end (no take path in live_sim; data-based);
multi-leg (3+) races (every sim race has 2 legs); the ladder with T2.1 beyond one screen; a tilt that REVERSES (the sim never ran one).
**Tier 2 ideas not built (PLAN_POLY_BIAS 4, T2.6):** per-market gap half-life weights (per-market persistence weak: slope 0.46, 47 of 229
stable), an Avellaneda-Stoikov rewrite (T2.1 is the minimal version), rival-floor models (no depth data), locked-set arbitrage as a profit
centre (+39 from 2 sets while on), quoting the 49 untouched tail markets (capital), a realised-P&L lock-in schedule (rank marks open positions).
**Owner decisions (recommendation in brackets):** (a) deploy Package 5 code [yes, quiet hour]; (b) stage 1-3 as above [yes]; (c) stage 4
0.85 [not now: +78 ± 86 over T2.1 alone; revisit after a day]; (d) hysteresis 0.01 [no]; (e) `take_tilted_ref` vs `take_enabled` false [take_tilted_ref; `take_enabled` false is
the no-code alternative until SIG answers]; (f) T2.5 passive pair unwind [your call: neutral in 3 h, frees the 7.3k Senate set];
(g) ask SIG about end valuation [yes; T2.3 waits for it]; (h) a snapshot each morning, `python analysis/poly_bias/snapshot02b.py <dir>`
[yes: the one number to watch is the hourly tilt].
**Suites (23 files, all green):** test_mm_bot 600, test_live_sim_marks 110, test_strategy 114, test_turnover 94, test_write_savers 75,
test_hold_target 71, test_fast_unload 61, test_pair_passive 57, test_tilt_rampin 54, test_mark_frag 52, test_reduce_book 49, test_tilt 48,
test_ops_liq 39, test_recorder_refill 37, test_ref_prices 37, test_behind_best 36, test_tilt_limit 33, test_take_tilted 31, test_carry_ramp
29, test_reduce_book_scope 26, test_gap_shrink 25, test_stress 20 + STRESS_LADDER=1 20/20 (0 duplicates); py_compile under Python 3.10
(no except*, Self, tomllib). Two red-team passes (opus): first 0 high / 6 medium / 6 low, second 0 high / 2 medium / 5 low; every code
item fixed, the two documentation items are in the rollout rules.
**Simulation spent:** ~440 CPU-minutes (400 seed-runs, 1,500 seed-hours) (the 120 cap was lifted by the owner at 20:30 UTC); every configuration run once (cached per
seed; the cache is `tests/live_sim_round5_cache.jsonl`).

## Package 4 (READY, Finisher, 2 Oct evening; branch `claude/finisher-package4`, draft PR #6): Package 3 final + 28/60 defaults + ladder fixes + write savers (all new features OFF). Code deploy (handover restart).
**What changed against Package 3 final (439ac54):**
- Defaults `writes_per_minute` 28, `writes_per_minute_max` 28, `burst_cycle_seconds` 60 (were 45 / 50 / 20): the owner's live overrides become the code
  defaults (2 Oct live: 4 x 429 while the budget climbed to 36-50/min, 0 at 28; normal cycles take 20-30 s, so the 20 s trigger fired with 0.4 s writes).
- Ladder (`ladder_enabled`, still OFF): the Reviewer fixes L1-L4 (team commit a77c8cb) plus 4 bugs found in them, each with a test that failed before:
  R4a (high) a new level could exceed its keep limit when the position limit binds -> placed and pulled every cycle; R4b (high) the per-order cash cap
  was also a keep limit -> a small account-value dip pulled every cash-capped level on every ladder market at once; R4c (medium) min_quote_life could
  keep a duplicate at a kept level; R4d (medium) a ladder-only cancel of a whole exchange counted toward `pulls_cancel_all_over`.
- Write savers (new settings, all OFF): `no_chase_enabled` (+ `no_chase_tolerance_ticks` 2, `no_chase_fv_epsilon` 0.0025);
  `ttl_tiers_enabled` (+ `order_ttl_busy` 3600, `order_ttl_quiet` 6000, `ttl_jitter_frac` 0.2, `ttl_busy_size_frac` 0.01; hard cap 7200 s);
  `ttl_expire_as_cancel` (+ `ttl_expire_grace_seconds` 5). Mirrored in `tests/strategy_sim.plan_changes`; `tests/test_write_savers.py` pins the two to
  one rule (1,152-case grid).
- Red-team fixes (opus review of the merged diff: nothing high with every flag off; Package 4 with flags off = Package 3 + 28/60 defaults):
  no-chase never applies in reduce-only, the flatten window, right after a Polymarket move or in a new fast-unload window; the self-test
  checks the longest tier TTL when `ttl_tiers_enabled` is on and switches the tiers off if the exchange refuses it (falls back to order_ttl,
  then 10 min); the tier-TTL validation runs only with tiers on, and a bad tier set refuses `ttl_tiers_enabled` too. Note: `ladder_move` is 2c
  (was 1c; part of L3, no effect while the ladder is off). Known, unfixed (flags off: inert): expiry-as-cancel leaves a side empty ~20-30 s
  per expiry and its 5 s grace trusts our clock; tiered TTLs make the dead-man's switch up to 2 h on quiet markets.
- Simulator: `plan_changes` now models order expiry (base numbers not comparable with rounds <= 3b); `live_sim` prints per-gate ladder counters (`lg_*`).
**Numbers (SIM_NOTES.md "Round 4"):** savers, 8 seeds x 3 h quiet (base 22.1 writes/min, 6,760 deferred/h): no-chase dP&L -43 ± 62, writes -0.26 ± 0.40
(noise); TTL saver dP&L -43 ± 39, writes **-1.10 ± 0.35**/min. Neither passed clearly; the 16 x 6 confirmation was not run (the real cost is ~14.6
CPU-s per seed-hour, 2.9x the estimate). Ladder gates (2 seeds x 0.5 h): blocked by cash in 81-86% of ladder-market cycles at start capital 0.90/0.80/0.70,
write gate shut 65-76%; 0 ladder shares at 0.90 and 0.80. It needs capital in positions at ~78-80% or less before it places a single level.
**Switch on:** nothing new. Keep every saver and the ladder OFF. Candidate for the next A/B: `ttl_tiers_enabled` + `ttl_expire_as_cancel` (the only clear
write saving), after a 16 x 6 quiet confirmation and after checking the exchange accepts expirationDate up to 2 h.
**Deploy:** `deploy/handover-restart.sh` with this commit (see deploy/RUNBOOK.md). settings_override.json can drop `writes_per_minute`,
`writes_per_minute_max`, `burst_cycle_seconds` (now defaults) or keep them (same values). **Rollback:** handover restart to 439ac54 (Package 3 final);
the overrides keep 28/28/60 there.
**Watch in the first 10 minutes:** as Package 3; status.json write budget 28 and `rate_limited_total` 0; no burst entries on 20-30 s cycles; no
"refused override" alerts; orders' expirationDate still order_ttl (30 min) since the TTL saver is off.
**Suites (Package 4):** test_mm_bot 600, test_write_savers 75 (new), test_strategy 114, test_turnover 94, test_fast_unload 61,
test_mark_frag 52, test_recorder_refill 37, test_ref_prices 37, test_behind_best 36, test_stress 20, and STRESS_LADDER=1 20/20 (0 duplicates); Python 3.11, py_compile on 3.10.
**Not done:** item 4 (turnover control and ceiling 0.5 vs 0.25 in 16 x 6 news) was blocked: the session's permission classifier refused the run.
Ladder P&L runs skipped: the gate diagnosis shows 0 ladder shares at start capital 0.90 and 0.80, so a P&L run would measure an idle ladder.

## HANDOFF (read this if you are picking the work up)
**Package 5 handoff (3 Oct 03:00 UTC, Finisher 2b).** Live: Package 3 final (439ac54) since 2 Oct 16:26 with the six live overrides; Package 4 (c29f762) not
deployed. Deploy candidate: **Package 5 (the "READY: Package 5" commit on `claude/finisher-package5`, PR #7)**, which contains Package 4. Read the
"Package 5" section above (diagnosis, results, rollout rules) and `deploy/package5/README.md` (staged override files). The simulator yardstick is now
`pnl_lag` / `pnl_liq` in `tests/live_sim.py` with the world knobs `_rival_anchor`, `_world_tilt*`, `_bg_wc`; every Round 5 seed result is in
`tests/live_sim_round5_cache.jsonl`. The second cycle (Package 6: exits that keep quoting in reduce-only, a backstop soft band) is on
`claude/finisher-package6` with its own START_HERE section when it exists. Earlier handoffs follow.
**State at 14:35 UTC, 2 Oct.** Live: Package 2 (83f6d45) since 11:21:57 with settings_override `{"arb_two_sided": false, "worst_case_backstop_frac": 0.8}`
plus the stop-gap keys below. Deploy candidate: **Package 3 (HEAD, "READY: Package 3")**; it includes 2.1-2.3.
**Where everything is** (all on this branch, PR #5): `START_HERE.md` (this file: packages, parameter table, owner flags), `PLAN.md`, `deploy/RUNBOOK.md`
(parameter-only, handover code deploy, rollback, emergency), `DATA_REPORT_2.md` + `analysis/*.py` (day-two analysis, valuation rule, outsider races,
rival floors, turnover, mark fragility, mark rule, follow-ups), `ideas/IDEAS_ROUND1..3.md` + `analysis/explorer_r2/` (idea rounds with the data facts
behind them), `SIM_NOTES.md` (simulator calibration and every sweep), `tests/strategy_sim.py` (crowded-book simulator), `tests/live_sim.py` +
`tests/live_start.json` (simulator from the real book), `tests/scenario.py` (`ceiling` reproduces the 2-5 min cycle incident), the Builder's
`START_HERE_BUILDER.md` and Run A's `ENGINEERING_NOTES.md`.
**Stop-gap overrides to lift once 2.3 is live**: `never_defer_unsafe` true and `burst_protection` true are safe again; keep `writes_per_minute` 28-30 and
`writes_per_minute_max` 30 unless `rate_limited_total` stays 0 for an hour (the exchange's write limit looks like ~30/min for the whole bot).
**Owner decisions still open** (with the team's recommendation): (1) `worst_case_backstop_frac` 0.8 vs 0.69: keep 0.8 until the sum of maxima is below
60k; (2) `arb_two_sided`: switch on after 2.3 (guard: liquid Polymarket on every leg, raw sum >= 0.99; expect 0 fills in outsider races); (3) election
day (Run B W1): markets close 4 Nov 00:00 UTC, one hour after the first polls close and before returns, so there is no "take stale quotes on returns"
window inside trading; the team recommends flattening from 24 h before, exempting complete sets only if SIG confirms settlement at the outcome, and no
directional taking; (4) ask SIG: the write limit (30/min per account? a batch = 1?), Smart Score definition, end valuation of unresolved positions, whether a
429 pauses reads; (5) Gamma rate limit before a Polymarket refresh below 5 s (not needed on the data: only 83 moves >= 1c in 16 h and adverse selection ~0,
so the WebSocket idea is parked).
**What is left (in value order, from ideas/IDEAS_ROUND3.md and the Strategist)**: no 0.5c chase when fair value and inventory are unchanged (24% of
all quote changes were pure rival chases; fresh quotes earn less than resting ones); a quiet-tier cancel-less regime (wide, long-lived quotes: 68% of
decisions for ~650 of edge); TTL refresh is a ~20% write tax (order_ttl 1800 / refresh 180; tiered TTL and expiry-as-cancel); headline re-quote only on a
fair-value move or fill; a Gamma-outage guard for quoting; a daily scorecard from the recorder (positions' currentPrice, account marks: `analysis/mark_rule.py`
pins the valuation rule once a day of data exists); run `analysis/rival_floor.py --mode sweep_only` on the server's market_data.sqlite to produce
market_edge.json (the loader is ON and inert without the file); the ladder only after its write-churn fixes and a dry-run write count.
**How the suites run**: `for t in test_mm_bot test_ref_prices test_strategy test_recorder_refill test_fast_unload test_turnover test_mark_frag
test_behind_best test_stress; do python tests/$t.py | tail -1; done` (also `STRESS_LADDER=1 python tests/test_stress.py`); Python 3.10 is required on
the server, everything here was run on 3.11 and a 3.10 venv.

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

### Package 2 (READY 11:05 UTC): inventory turnover, capital use, coverage. Code deploy (handover restart). Commit "READY: Package 2".
Strategy package, every change behind a setting (all live-overridable). Red-teamed by the Reviewer; its fixes are in.
Suites: test_mm_bot 463, test_ref_prices 37, test_strategy 101, test_recorder_refill 37, test_stress 20; green on Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`; `.gitignore` (position_lots.json). The bot creates
`position_lots.json` and the new recorder tables itself. Includes Package 1.

| Change (default) | Mechanism | Evidence | Settings |
|---|---|---|---|
| **Pair unwinder (ON)** | we hold YES on every leg of a race (n complete sets): when other traders' bids sum to >= 1.005, sell up to n sets at the bids (10 s orders); mirror for short sets at asks <= 0.995. Runs in reduce-only too (it only shrinks). | 17,675 pair-shares held (7,335 x 2 U.S. Senate control): ~17% of the account earning nothing; U.S. Senate bid-sum >= 1.000 in 16% of snapshots. Riskless by construction; worth ~+50 in profit and ~17k of freed capital over days | `pair_unwind_enabled`, `_min_profit` 0.005, `_max_frac` 0.01, `_cooldown_seconds` 30 |
| **Buy-side arbitrage (ON, flagged)** | asks of all legs sum to <= 0.985 AND our fair values sum to >= 0.9925 -> buy every leg (a set pays 1); the unwinder sells it back when bids reach 1.005 | ask-sum < 0.98 in 853 race-minutes/day (the live sell-side arb at 3c fired 0 times). Upper-end estimate +600-1,200/day; snapshot sums may include stale prices, so watch the fill logs | `arb_two_sided`, `arb_min_profit_buy` 0.015, `arb_buy_min_sum` 0.90 |
| **Race-netted limits (ON)** | position limits and the Kelly cap also apply to the race-netted exposure on the side that grows it (a short Rep leg counts toward the Dem leg) | long 10,834 Dem House AND short 9,396 Rep House = the same ~20k bet twice under per-market limits of ~10k | `limits_use_race_net` |
| **Age skew (ON)** | a position's share-weighted age (FIFO lots, persisted; rebuilt from fills.csv on first start) adds 0.25c of skew per hour held beyond 1 h, max 2c, toward unloading; never through fair | median held share 7.2 h old; 54% > 3 h; round trips were the whole realised profit, open lots gave their edge back | `skew_age_enabled`, `skew_age_after_hours` 1, `skew_age_per_hour` 0.0025, `skew_age_max` 0.02 |
| **Capital ceiling (ON, flagged)** | when capital in positions > 75% of the account (the API's totalMarketValue, else our own valuation), every ADDING side quotes at a quarter size; reducing sides unchanged; off again below 70% | 90.5k of 101k tied up, 11k cash. **The ceiling will be ON at deploy**: adding sides shrink to 25% until positions turn over. Deliberate (owner's priority); lower/raise `capital_ceiling_adding_size_factor` live if coverage or fills drop too far | `capital_in_positions_max_frac` 0.75, `capital_ceiling_adding_size_factor` 0.25 |
| **Thin-book pricing from bulk tops + start-up priming (ON)** | R5 may price from the bulk best bid/ask (3 requests for all markets) when no fresh book exists; our own order at the top blanks that side; on a (re)start books download first (60/cycle, 45/min) | after the 08:08 restart coverage rose 30 -> 101 in 6 min with 138 eligible markets waiting; scenario cold start: quoted at 2 min 17% -> 80-85% | `ref_only_use_tops`, `tops_max_age` 120, `startup_books_first`, `startup_prime_*` |
| **Same-side refill cooldown (ON)** | 2 adding fills (>= 200 sh) on one side within 60 s -> that side withheld 30 s; reducing side exempt | 3rd+ fills in same-side runs: -1.15c on 81k shares (-936); mostly day one's skew bug, so this is cheap insurance | `refill_cooldown_*` |
| **Mark recorder (ON)** | `positions` (quantity, currentPrice, all numeric fields) and `account_marks` tables, no extra requests; `analysis/mark_rule.py` fits the exchange's valuation rule | the exchange marks at a trade average (30-min VWAP or last ~5 trades fit); pins the rule for risk and rank accounting | `record_positions` |
| **Favourite-longshot bias (OFF)** | wider, smaller "bad side" below 20c / above 80c | the apparent -0.5c was day one's skew quoting through fair value, not longshot flow (DATA_REPORT_2 §10c) | `fl_bias_enabled` False |
| **Reviewer fixes** | capital ceiling factor 0 -> 0.25; buy-side arb fair-value-sum guard; unwinder margin/cap; zero-fill 4x cooldown; priming reserve; carried tops expire; positions rows only on quantity/mark changes | red-team of the merged diff | - |

Parameter-only change shipped with this package (optional, revertible in 30 s via settings_override.json): `{"ref_weight": 0.8}`.
Simulator (128 seeds, recalibrated to day two): ref_weight 0.85 +17 ± 6/h quiet, +20 ± 8/h news on a +499/h base, peak worst case +0.35k; the simulator
treats Polymarket as the truth, so this is an upper bound; 0.8 is the conservative step (medium confidence). Everything else the Strategist swept stays:
improve_ticks 0 loses -17 ± 6 (keep pennying), kelly 0.15 loses -22 ± 6, min_edge/max_half_spread/skew/size/write/churn changes are noise.

Deploy: `deploy/RUNBOOK.md` section B (handover restart). Rollback: section C, or switch any single feature off via settings_override.json.
Watch in the first 10 minutes: (1) status.json `capital_in_positions_frac` and `capital_ceiling_active`: active is expected; if resting orders fall by
more than 40%, set `capital_ceiling_adding_size_factor` 0.5 live; (2) "PAIR UNWIND" / "ARBITRAGE ... buy" lines and the following "every leg filled N":
N = 0 repeating on one race, or a buy in a two-leg race whose fair values do not sum to ~1 -> set `arb_two_sided` false; (3) `markets_priced_from_tops`
and `books_loaded` climbing during priming, `rate_limited_total` 0; (4) "refill cooldown" lines rare and never on a side whose position has the
opposite sign; (5) `position_lots.json` written, `portfolio_age_hours` plausible (not 0 with positions, not > 48 h), quote lines showing "age Nh".
Owner flags: pair unwinder, buy-side arbitrage and the capital ceiling are new behaviour ON by default (data evidence, riskless or strictly
risk-reducing by construction); ref_weight 0.8 is a parameter step on simulator evidence only.

### Package 2.1 (READY 12:05 UTC): HOT-FIX for the 11:22 reduce-only episode. Code deploy (handover restart). Commit "READY: Package 2.1".
Behaviour-identical to the live Package 2 except the two fixes below (the not-yet-reviewed fast unload and reduce-join features are
in the code but OFF: `fast_unload_enabled` False, `reduce_join_best` False; the per-market edge `market_edge_enabled` is OFF too).
Suites: test_mm_bot 503, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_stress 20; Python 3.11 and 3.10.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (rival_floor.py, new), `deploy/RUNBOOK.md`.

| Fix | What happened live | Change |
|---|---|---|
| Risk fair-value fallback (`Bot.risk_fv`) | Rep U.S. House (short 9,396) unpriced after the restart: `or 0.5` made it a coin flip, risk 20.6k -> 31k, reduce-only 11:22-11:31, Dem House sold at 0.915 vs 0.92 fair | a HELD market with no fair value uses, in order: the liquid Polymarket reference; 1 - the other leg's fair value (two-leg race); the exchange's own mark (currentPrice from the positions read); the last fair value; only then 0.5. Logged once per market and source. Used by settlement_risk, total_worst_case, the capital-in-positions valuation and the unwind safety check |
| Priming held markets first (Engineer 13) | priming ended at 192 s with 128/237 books; Rep House's adopted bid sat at the best, so the bulk top was blanked and R5 could not price it until its book arrived, behind ~100 others | missing books of HELD markets download first (biggest |position| x price), then markets with resting orders, then tops-priced, then by volume; priming never ends while a held market lacks a book (hard cap `startup_prime_held_max_seconds` 900); a held market unpriced for `unpriced_held_warn_cycles` (5) logs one "UNPRICED held market" WARNING with the reason |

**Stop-gap settings for the 11:31-11:50 slow cycles (2-5 min; 20 silent 429s)**, via settings_override.json, until Package 2.2 ships
(Engineer 14 is reproducing it; the lead's reading: the capital ceiling x0.25 and burst x0.5 shrank every adding quote, so every resting
adding order counted as "oversized" = unsafe, `never_defer_unsafe` gave all those reprices pull priority past the request budget, the
flood hit the exchange's real write limit, each 429 (Retry-After 60 s) paused EVERY request, and the 429 is only logged when no pause
was already in force):
`{"arb_two_sided": false, "never_defer_unsafe": false, "capital_ceiling_adding_size_factor": 0.5, "burst_protection": false, "writes_per_minute": 30, "writes_per_minute_max": 30}`
Watch: summary-line interval back to ~30 s; "request budget: N deferred" shrinking; orders_resting steady (not 70 -> 281 -> 84); status rate_limited_total not rising.
Re-enable `burst_protection` once cycles are back to ~30 s. `capital_ceiling_adding_size_factor` 0.5 is also the Strategist's recommendation
(factor 0 was a cliff: -131/h once the ceiling binds; 0.5 recovers ~70%); Package 3 makes 0.5 the default.

**Backstop recommendation** (`worst_case_backstop_frac`, owner set 0.8 at 11:46): keep 0.8 for now. The sum-of-maxima (69k) is dominated by the
doubled House legs and many small races and ignores the hedged sets; R7's correlated risk (20.6k vs the 30.5k cap) is the operative limit. The
pair unwinder and the capital ceiling should bring the sum down over the next days; revisit to 0.69 when it is below 60k. This loosens a safety
limit: the owner's call, flagged.

**ref_weight 0.8: withdrawn.** The Strategist's 3-hour runs reverse the 1-hour gain (ref_weight 0.85: -23 ± 24 / -46 ± 28 per 3 h at the lagged mark).
Keep 0.7.

### Package 2.2 (READY 12:40 UTC): HOT-FIX for the 2-5 minute cycles. Code deploy (handover restart). Commit "READY: Package 2.2".
Includes 2.1. Behaviour against the live Package 2 changes only by the fixes below; the Package 3 features present in the code stay OFF
(`fast_unload_enabled`, `reduce_join_best`, `turnover_control_enabled`, `mark_frag_enabled`, `market_edge_enabled` all False; the R3 ladder is not in 2.2's code).
Suites: test_mm_bot 536, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 58, test_turnover 72, test_mark_frag 52,
test_stress 20; green on Python 3.11 and 3.10. Reviewer red-team in progress; anything it finds ships as 2.3.
Files that change: `mm_bot.py`, `tests/`, `analysis/` (new scripts), `deploy/RUNBOOK.md`.

Root cause (Engineer 14, reproduced in `tests/scenario.py ceiling`: 236 markets, full-size orders resting, the capital ceiling switching on, 45 writes/min,
an exchange that answers 429 past 30 writes/min): (1) the ceiling's x0.25 (and burst's x0.5) shrank the WANTED size, so every resting full-size order
counted as "bigger than allowed" = unsafe; `never_defer_unsafe` gave those ~100-280 reprices per cycle pull priority past the request budget; (2) the
flood exceeded the exchange's real write limit; each 429 (Retry-After 60) paused EVERY request for 60 s, and the 429 was logged only when no pause was
already in force (20 silent 429s live); the main thread waited on the positions/book reads (0% CPU, futex_wait). Before the fix: 4 cycles in 6.5 min,
median 139 s, max 278 s. After: median 5.2 s, every 429 logged, the write budget settles at 26/min.

| Fix | Setting (default) |
|---|---|
| A resting order is never unsafe only because a size FACTOR (ceiling, fl bias, turnover) shrank the wanted size: `Quote.bid_max/ask_max` (the size before factors, within position/cash/risk limits) is the ceiling in plan_change; orders beyond the limit price, on unwanted sides or above a LIMIT stay urgent | - |
| Urgent writes capped per cycle, price-unsafe orders and pulls first | `urgent_writes_per_cycle` 20 |
| A main-thread write (take, arbitrage) that would wait longer than write_wait_seconds + margin is not sent ("429 WRITE_BUDGET_WAIT"); takes/arbitrage skipped while the budget is busy; cycles skipped during a 429 pause | `main_write_wait_margin` 2, `pause_skip_cycles` True |
| Every 429 logged (method, path, Retry-After, pause already on); a 429 during a pause moves its end to Retry-After from now (not additive); budgets cut once per pause; status.json `paused_until`, `pause_seconds_left`, `pauses_total`, `seconds_since_cycle`, `last_cycle_phases`; the summary line ends with the last cycle's phase times (reads, fair values, fills/risk/takes, decide, send, wait) | - |
| Watchdog thread: alert after 180 s without a completed cycle; at 600 s dump every thread's stack, cancel-all (at most 20 s), exit 5 (systemd restarts) | `watchdog_alert_seconds` 180, `watchdog_exit_seconds` 600 (0 = off), `watchdog_cancel_seconds` 20 |
| Buy-side arbitrage guard: every leg needs a LIQUID Polymarket price and the raw prices must add up to >= 0.99 (book fair values are normalised to 1 and cannot see an outsider). `analysis/outsider_races.py` lists the races: South Dakota Senate 0.917, Idaho Senate 0.945, Maryland Governor 0.979, a dozen House races 0.980-0.988, three 3-leg races (RI Gov, NE Sen, MT Sen), four with no complete reading (Alaska Sen/Gov, CA-22, CA Gov) | `arb_buy_min_ref_sum` 0.99. The owner may now set `arb_two_sided` true via settings_override |

After deploying 2.2 the stop-gap overrides can go back to defaults one at a time: `never_defer_unsafe` true (now safe), `burst_protection` true, `writes_per_minute`
45 / `writes_per_minute_max` 50 only if `rate_limited_total` stays 0 for an hour at 30 (the 429s were real: the exchange's write limit looks like ~30/min).
Keep `capital_ceiling_adding_size_factor` 0.5. Watch: summary-line gaps ~30 s; "request budget: N deferred" < 30; `rate_limited_total` flat; `orders_resting` steady;
"RATE LIMITED (429)" lines (now one per 429) absent; the watchdog never alerts.

### Package 2.3 (READY 13:10 UTC): Reviewer fixes on 2.2. Code deploy (handover restart). Commit "READY: Package 2.3". Supersedes 2.2.
Includes 2.1 and 2.2. Reviewer verdict on 2.2 was "ship with fixes"; the fixes:
| Fix | Why |
|---|---|
| The watchdog clock is NOT reset by a cycle skipped during a 429 pause | a chain of pauses (the incident's shape) never reached the 180 s alert / 600 s restart |
| The urgent-write cap skips further URGENT changes instead of deferring everything behind them | with `break`, a news move making > 20 orders price-unsafe stopped re-quoting the whole book for several cycles |
| A "WRITE_BUDGET_WAIT" on an arbitrage/unwind batch or a take is treated as "not sent": no 90 s pending hold, no "check positions" alert; takes check the budget BEFORE cancelling our own quote; arbitrage needs 2n+1 writes | silent loss of takes/arbs and a false alert |
| `mark_frag_total_max_cash` 2,000 -> 0 (per-position cap only, still OFF) | the total cap would withdraw every adding side at once when noise crossed 2,000 (1,186 today) |
| Package 3 features (still OFF) corrected per review: reduce-join joins only a rival INSIDE our normal quote, floor 1c; fast unload at fair ± 1c for 180 s, only the first placement urgent; turnover control cuts the wanted size only (resting orders within the normal limit stay), hysteresis 50/75 sh/h with a 30-min state life, outage gaps unobserved; behind-the-best sizing added (OFF) | so they can be switched on in Package 3 |
Suites: test_mm_bot 543, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 61, test_turnover 94, test_mark_frag 52,
test_behind_best 36, test_stress 20; Python 3.11 and 3.10. Watch after deploy (Reviewer): `rate_limited_total` / `pauses_total` flat at writes 30;
`last_cycle_seconds` median < 20 s; no run of "urgent writes capped" lines; `write_budget_wait_total` / `takes_skipped_budget` small; `orders_resting`
and `locked_in_orders` steady under the ceiling; no WATCHDOG alert.

### Package 3 (in preparation; merged on the branch, every feature OFF): inventory turnover and sweep capture
Candidates (all live-overridable): `fast_unload_enabled` (after a quote fill with >= 2c edge, the reducing side quotes at fair ± 1c for 180 s at the
filled size); `reduce_join_best` (the reducing side joins a rival resting inside our normal quote, never closer than 1c to fair);
`turnover_control_enabled` (a market with < 50 sh/h of flow over 6 h where we hold >= 100 shares: adding side x0.25 and half its position limit,
hysteresis 50/75, 30-min state life); `behind_best_size_enabled` (an adding quote 2+ ticks behind the best other price at half size: cuts cash
locked in orders ~14% in the simulator, P&L neutral-to-slightly-negative); `mark_frag_enabled` (per-position cap = 100 cash / sd of the 10-min
mid step; on the snapshot 3 positions would be capped: both RI Senate legs and Dem U.S. Senate; total noise 1,186 per step);
`market_edge_enabled` (per-market min_edge from `analysis/rival_floor.py --mode sweep_only`, needs live recorder data); `ladder_enabled`
(R3 resting depth ladder at 1.5/2.5/3.5c, sizes 1/2/3x, headline 2/4/6/8c; cash-gated at 10% free cash; writes gate 10; simulator +17 ± 15 quiet /
+37 ± 18 news per hour at 24 seeds, +46-51 ± 10 at 128 seeds in the prototype; writes 12 -> 27 per market-hour, so it needs the write budget
headroom the exchange may not have). Defaults are decided after the Strategist's real-inventory runs and the Reviewer's second pass.
Capital ceiling factor 0.25 -> 0.5 will be Package 3's default (Strategist: factor 0 was a cliff, 0.5 recovers ~70% of the lost P&L).

### Simulator from the real book (Strategist round 3, owner's request): `tests/live_sim.py`
Starts from the real positions and lot ages at 08:14 (`tests/live_start.json`, rebuilt by `tests/live_start_extract.py`; House legs set to the 09:45
figures), 68 real markets (the races of the 40 biggest positions, both legs) plus 3 made-up races with an unlisted outsider, the other 163 markets as a
fixed capital block (90% of the account in positions, ~10k free cash; adding orders need free cash), the real decide / arb_plan / fill hooks, a 30-min
trade-average mark. Usage: `python tests/live_sim.py SEEDS HOURS quiet|news '{settings}' ...`. 16 seeds, 6 h quiet, everything off: P&L +2,990, capital
0.90 -> 0.93 (peak 0.98), peak worst case 55k. Package 2 "all on" vs off: P&L -180 ± 160 (quiet) / -293 ± 110 (news) at Polymarket prices but
+219 ± 180 / +68 ± 97 at the trade-average mark (+597 ± 270 over 12 h), cash freed +8.5k / +9.7k (+13.8k over 12 h), worst case -7.3k / -8.1k
(-10.6k), positions 0.6-2 h younger. Alone: race-netted limits +185 ± 150 (+407 ± 180 at the mark, worst case -5.3k); capital ceiling +251 ± 120;
pair unwind +88 ± 76 (ages -0.85 h); age skew +5 ± 98; arbitrage -54 ± 90; refill cooldown -120 ± 97; fast unload (old 0.5c/300 s) -257 ± 94;
reduce-join (old) -58 ± 61. The outsider races got 0 arbitrage fills. Limits: 68 of 237 markets simulated; buy-side chances ~2x real.

### Package 3 (READY 14:45 UTC): defaults from the real-book simulator and the Reviewer; new features present, most OFF. Code deploy (handover restart). Commit "READY: Package 3".
Includes 2.1-2.3. Changes against 2.3: `capital_ceiling_adding_size_factor` 0.25 -> 0.5 (the ceiling binds live; factor 0 was a cliff of -131/h, 0.5 recovers
~70%); `mark_frag_enabled` OFF (round 3b: -29 ± 52, the cap rarely binds; switch on for rank stability if wanted); `market_edge_enabled` ON (inert until `market_edge.json` exists: run
`python analysis/rival_floor.py --mode sweep_only` on the server's market_data.sqlite and copy the file next to mm_bot.py; the loader refuses bad
entries); `refill_cooldown_enabled` OFF (real-book simulator -120 ± 97; the day-one losses it targeted were the old skew bug). OFF and awaiting evidence
(switch on via settings_override only after a simulator or live A/B result): `fast_unload_enabled`, `reduce_join_best`, `turnover_control_enabled`,
`behind_best_size_enabled`, `ladder_enabled` (the ladder also needs the Reviewer's write-churn fixes L1-L4, listed below, before any live use).
Suites: test_mm_bot 588, test_strategy 114, test_ref_prices 37, test_recorder_refill 37, test_fast_unload 61, test_turnover 94, test_mark_frag 52,
test_behind_best 36, test_stress 20 (and with STRESS_LADDER=1); Python 3.11 and 3.10.
Watch in the first 10 minutes: as for 2.3, plus status.json `mark_frag_capped_markets` (expect ~3) and `mark_frag_top`; `market_edge_markets`
(0 until the file exists); adding-side sizes at half under the ceiling (not a quarter).
Round 3b (6 h quiet, 16 seeds, from the real book): ceiling factor 0.5 vs 0.25 +262 ± 100; arb_buy_min_ref_sum 0.99 vs 0.8: 0.8 would lose -898 ± 94
buying 23.8k outsider shares; fast unload -165 ± 110; reduce-join -48 ± 81; behind-best -99 ± 83; mark cap -29 ± 52; ladder -5 ± 49 (idle at 10k cash);
refill cooldown off -14 ± 94; turnover control untested. Engineer 10's ladder Reviewer fixes L1-L4 are now MERGED (596 tests; ladder still OFF; `ladder_move` default 2c). Unfinished at wrap-up: nothing of the ladder's Reviewer fixes L1-L4 (hair-trigger urgent pulls one tick
behind the touch -> stale not urgent; ladder-only pulls excluded from the cancel-all count; 1-tick tolerance, re-anchor hysteresis at 2c and
min_quote_life for ladder orders; per-order cash cap in ladder_caps) and the Strategist's round-3b runs (new fast unload, new reduce-join, turnover,
behind-best, mark cap, the real ladder at 10k free cash, refill cooldown off vs on, ceiling 0.25 vs 0.5 on `tests/live_sim.py`). Both are specified
above and in SIM_NOTES.md; a new session can redo them from this branch.

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
| ref_weight | 0.7 | 0.7 (0.8 withdrawn) | 1 h: +17 ± 6; 3 h at the lagged mark: -23 ± 24 / -46 ± 28 | none |
| capital_in_positions_max_frac (new) | - | 0.75 (factor 0.25) | owner: 90% in positions | adding sides at quarter size until positions turn over |
| skew_age_* (new) | - | 0.25c/h after 1 h, max 2c | median age 7.2 h | faster unloading of old positions |
| pair_unwind_* / arb_two_sided (new) | - | on | 17.7k paired capital; ask-sum < 0.98 in 2.5% of race-minutes | capital freed; small riskless gains |
| ref_only_use_tops / startup_books_first (new) | - | on | coverage 30 -> 101 in 6 min after restart | ~190 priced within 2 min of a restart |
| refill_cooldown_* (new) | - | on | -936 on 3rd+ same-side fills | fewer walks against us |
| fl_bias_enabled (new) | - | off | §10c: a skew artefact | none |
| capital_ceiling_adding_size_factor | 0.25 (P2) | 0.5 | real-book simulator: factor 0 a cliff, 0.5 recovers ~70% | adding sides at half size under the ceiling |
| mark_frag_enabled (new) | - | off | round 3b -29 ± 52, rarely binds | none |
| market_edge_enabled (new) | - | on (inert without market_edge.json) | Reviewer: only widens, cheap | per-market floors once the file exists |
| refill_cooldown_enabled | on (P2) | off | real-book simulator -120 ± 97 | none expected |

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
