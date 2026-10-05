# Package 14: MM funding (owner, 4 Oct 21:10 UTC: "keep market making FULLY FUNDED at all times")
Branch `claude/mm-funding`, built on the LIVE head c7c0107 (not on Package 13). Every new setting is OFF by default and the code with
the flags off is pinned byte-identical to c7c0107 on a grid (orders, quotes, order notes, status.json values, health, summary line;
tests/test_mm_funding.py). The only thing that changes with the flags off: status.json gains a read-only `mm_funding` key (and the
bot tracks MM inventory lots for it, logging one "MM inventory seeded from fills.csv ..." line at start).

## Files
- `settings_override.mm_funding.json` = the live file (snap04 `settings_override.json`) with your current live values
  (`alloc_mm_reserve` 20000, `mm_risk_reserve_wc` 20000, `mm_risk_reserve_corr` 4000, `worst_case_backstop_frac` 1.0) + the three
  flags ON and the proposed defaults written out (`mm_inv_max_age_h` 6, `mm_inv_max_usd` 3000, `mm_recycle_concession` 0.01,
  `mm_funding_alert_h` 2). `capital_ceiling_adding_size_factor` (0.5), `arb_enabled` (false) and `ref_tilt_headline` (true) are
  kept as live. Validated with `mm_bot.validate_overrides`: no problems.
- `settings_override.previous_live.json` = the same without the P14 keys (the rollback file). If your live file changed since
  snap04 in anything else, carry those changes into both files first.

## What each flag does
MM inventory = what the middle-band two-way book left us holding: fills of our RESTING quotes (not takes, arbitrage, allocator,
set ladder or basket orders) in markets whose Polymarket p at the fill (else the fair value when quoted) lies in
value_mid_low..value_mid_high (0.15-0.85). Kept per market as lots (shares, price, time), FIFO: an opposite MM fill closes the oldest
lot first (a round trip); a fill that only shrinks a non-MM holding opens nothing; the lots never exceed the position. Persisted in
status.json `mm_funding.lots`; on the first start without it they are seeded from fills.csv's last 24 h (the order notes' horizon).
A market's MM inventory is STALE when a lot is older than `mm_inv_max_age_h` (6 h) or its $ at p exceeds `mm_inv_max_usd` ($3000).
- `mm_recycle_enabled`: the stale shares go out FIRST, through the quoter itself: that market's reducing side (the ask on a long, the
  bid on a short) is moved in to fair - `mm_recycle_concession` (1c) and sized to the stale shares (never past the position, never
  crossing the best other bid/ask; in value mode never below the value floor p - value_sell_margin, so with the live margin 0.5c the
  effective concession is 0.5c). It is the same single order the quoter keeps there (no duplicate); our adding side stays a tick
  behind it and keeps quoting. A pinned label (`alloc_pin`) is never pushed. If the market's edge-held (the allocator's
  (p - best bid) / best bid) is >= `value_quote_hurdle` (0.05 live) the inventory is VALUE: it is handed to the value bucket (no
  longer MM, not recycled; journal "... handed to the value bucket") and the refill below sells the lowest-edge value positions instead.
- `mm_refill_fast` (needs `alloc_enabled`, on live): when the cash gate's free cash is below `alloc_mm_reserve`, the allocator's reserve
  refill runs on the NEXT cycle (at most once a minute) instead of waiting for the hourly run, inside `alloc_max_turnover_per_hour` and
  the allocator's write share: first the stale MM shares (an immediate-or-cancel sale at the best bid only when it is within the
  concession of fair and not below the floor; otherwise they keep resting through the recycler), then value positions lowest
  edge-held first; no refill sale ever below p - value_sell_margin (also the hourly refills while the flag is on). A market just sold
  this way is not sold again until the positions read shows the sale (or 2 min pass): no double sale on a lagging read (RT13-3).
- `mm_room_guard` (needs `mm_risk_reserve_wc` / `_corr`, on live): the risk-room pause counts the MM inventory's own worst-case /
  correlated contribution against the MM room first, so value buying pauses only when the VALUE book's share of the room is short
  (room + min(MM contribution, reserve) < reserve); it resumes at 1.1 x as before; the recycler and refills never pause.
- Monitoring (always): status.json `mm_funding` {cash_free, cash_target, room_free {wc, corr}, room_target {wc, corr}, inventory_usd,
  oldest_inventory_h, stale_markets, stale_usd, recycling {market: side / qty / price / why}, handed_to_value, below_half,
  below_half_since, refill {runs, sold_usd, mm_sold_usd, last, blocked_by}, lots_source, lots}. With any flag on: ONE alert when cash or
  a room has been below half its target for more than `mm_funding_alert_h` (2 h) - re-armed once everything is back above half - and a
  " | MM funding cash Xk/20.0k, room wc Yk/20.0k, room corr Zk/4.0k, inventory Nk (S stale, oldest H h)" piece on the 2-hourly summary.

## Deploy order
1. Code: handover restart (`deploy/handover-restart.sh`) to this branch's head (on top of c7c0107) with your live file unchanged.
   Behaviour is unchanged; check that status.json has `mm_funding` with `lots_source` "seeded from fills.csv (...)" and plausible
   `inventory_usd` / `stale_markets`.
2. File: write `settings_override.mm_funding.json` to a temp file next to `settings_override.json` and `mv` it over (read within
   30 s). To go flag by flag instead, add `mm_recycle_enabled` first, then `mm_refill_fast`, then `mm_room_guard`.

## What to watch in the first hour
- Journal "MM RECYCLE <market> sell N @ X (fair ..., stale by age/$)" lines: the seeded inventory older than 6 h is worked first.
  Exactly ONE ask of ours per such market (recorder / open orders), never below Polymarket - 0.5c, never at or through the best bid.
- "MM RECYCLE <market>: ... handed to the value bucket" for +EV inventory: expect many in wide middle markets (best bid >= 5% under p).
- "ALLOC fast refill (mm_refill_fast): N sale(s) planned (cash X < reserve 20000 ...)" while `mm_funding.cash_free` < 20000, then
  "ALLOC sold ... (reserve refill)" / "stale MM shares sold by IOC"; `alloc.turnover_hour` <= 15000; no market sold twice within 2 min;
  `mm_funding.refill.blocked_by` (floor / mm_resting / in_flight / cash / writes) says why a refill waits.
- `mm_risk_room.value_share_wc / _corr` and the pause lines ending "[mm_room_guard: MM inventory ... counted against the MM room first
  ...]": the pause now follows the value book's share.
- `mm_funding.cash_free` rising toward 20000 and `room_free.wc` toward 20000; an ALERT "MM funding below half for ..." after 2 h if not.
- Writes stay <= 28/min; no 429s.

---
# Package 14.1 (owner, 5 Oct 10:30 UTC: "fix the market-making funding and the allocator's swaps; both are stuck live")
Same branch, on top of Package 14 (4ff7d91, live since 10:06). Deployable on its own: nothing from Package 13 is in it.
Every 14.1 setting is OFF by default and the flags off are pinned byte-identical to 4ff7d91 on a grid (orders, quotes,
order notes, status.json values less `mm_funding`, health, summary line; tests/test_mm_funding_14_1.py, 100 checks).

## Diagnosis (analysis/p14/DIAG_14_1.md), in order of weight
1. The fast refill is starved by PRICE, not by our own orders: `blocked_by` "mm_resting" means the stale MM shares'
   best OTHER bid was more than `mm_recycle_concession` (1c) from fair or below the value floor. Our MM quotes never
   blocked it (the book is stripped of them, and every allocator IOC already cancels our orders there first). In the
   10:21 book only ~$0.8k of holdings sit at or above p - 0.5c; $4.8k within 2c, $7.4k within 3c, $43k deeper.
2. The swaps were switched off by the risk-room pause: `mmr_paused` (room_wc 9.5k < the 20k reserve) drops EVERY buy
   level in `alloc_plan`, so "pairs planned, est. gain 0" was only refills.
3. A run in flight locked the fast refill out (`not self.alloc_pairs`, plus the 60 s gap): pairs waiting for a buy the
   pause refused kept `refill.runs` at 0 for up to 15 min at a time.
4. `blocked_by` mixed buy-side with sale-side reasons: "prefer_short" and "mm_risk_reserve" never blocked a refill
   SALE in 4ff7d91 but sat in the refill's blockers, which is why the live counters read as if they did.
5. The recycler's buy-backs are covered lone-NO sales here: the gate charges 0 and a fill FREES (1 - price) a share.
   What is slow is the price: fair -+ 1c rests behind the touch in a tilted book.

## What each 14.1 flag does
- `alloc_cancel_mm_first`: a stale-MM refill candidate is judged by the refill's own price rule (the value floor) and
  no longer by the concession gate ("mm_resting" stops being a blocker; a refused one counts "floor"). The sale is the
  allocator's usual IOC - ONE whole-exchange cancel of our orders there, then the order, same cycle - and after ANY
  allocator sale that market's REDUCING quote side is held off ("refill pending") until the positions read shows the
  sale or 120 s pass (`MM_SENT_LAG`), so the quoter never re-offers what was just sold. The adding side keeps quoting.
- `alloc_rank_all_markets`: refill candidates are ALL holdings ranked by edge-held (not only edge-held <=
  `alloc_max_edge_sell`, which stays the SWAPS' rule), on the cached book when the fresh one has aged out; a refused
  candidate is skipped, counted and the run goes on. The fast refill runs EVERY cycle while free cash <
  `alloc_mm_reserve` (no 60 s gap) and is ADDED to an hourly run already in flight (its markets skipped), inside
  `alloc_max_turnover_per_hour` and the allocator's write share. A paired level gone no longer stops the refills.
- `mm_recycle_sell_first`: recycled SALES (the ask on a long: they free cash) are sent before the other changes and
  recycled BUY-BACKS after them; a buy-back is sized by its NET cash (the gate's need less the (1 - price) a share its
  fill frees), and below half the cash target the shares that would lock more than they free are deferred (counted in
  `mm_funding.deferred_buybacks`).
- `alloc_refill_ignore_prefer_short`: below half the cash target a refill plan scans no buy levels at all, so its
  `blocked_by` holds only reasons that stop a sale (no "prefer_short", no "mm_risk_reserve").
- `alloc_swap_room_netting`: while value adds are paused, SWAPS go on - each pair is admitted only while both rooms
  stay at or above min(the room now, the reserve) after its sale AND its buy (re-checked before each leg), so a swap
  can never take a room below the MM reserve net of its own sale; its buy may spend its own sale's proceeds even with
  cash below `alloc_mm_reserve`, never more (the MM cash before the sale is untouched). Refills, takes, basket buys and
  tail adds keep the pause exactly as before, and reduce-only still stops every buy.
- `alloc_refill_max_cost` (0 = off, NOT in the staged file): below half the cash target a REFILL sale may go this far
  below p instead of `value_sell_margin`. The EV given up is reported (`mm_funding.refill_ev_given_24h`).
- Reporting (always, read-only): `mm_funding` gains {refill_runs, refill_sales_24h, refill_sold_usd, refill_last,
  refill_ev_given_24h, deferred_buybacks, refill_holds, cash_locked, events}; with any 14.1 setting on, `alloc` gains
  {swaps_planned, swaps_planned_24h, swaps_done_24h, swaps_usd_24h, ev_gain_est_24h, ev_gain_realised_24h,
  refill_blocked_by} and the 2-hourly summary's MM funding piece gains "refill N runs, Xk sold, swaps A planned / B
  done, Yk moved, EV +est / +realised".

## `alloc_max_edge_sell`: 0.05 recommended (and `alloc_min_improvement` stays 0.03)
One planning run on the 10:21 book (cash at the reserve, so swaps only; gain already net of both touch prices):

| alloc_max_edge_sell | swaps | $ moved | est. EV gain | EV given up on the sale side |
|---|---|---|---|---|
| 0.02 (live) | 8 | 847 | 99 | 15 |
| **0.05** | **25** | **10,068** | **650** | **354** |
| 0.08 | 32 | 11,812 | 713 | 454 |

0.05 is where the converged 2-5% bucket ($16.8k live) becomes reachable; 0.08 adds $1.7k of volume for +63 of estimated
gain while giving up ~100 more on the sale side - a worse gain per $ and a deeper cut into positions that may simply be
converging. 0.02 leaves the whole point of the package untouched. The pairing hurdle stays `alloc_min_improvement`
(already a MIN GAIN per $ between the buy's and the sale's edge): at 0.06 the same book moves $6.9k for +489, so no
separate `alloc_swap_min_gain` setting was built. Swap sales are not bound by the value floor (as in 4ff7d91 - the
hurdle is the gain per $); every REFILL sale is.

## Dry run (analysis/p14/DRYRUN_14_1.md, tests/test_p14_1_dryrun.py, 32 checks)
The 10:21 state on the snapshot: the deadlock reproduces ($487 freed, blockers mm_resting 16 / floor 4, no swap). With
the staged file the FIRST allocator run plans 28 pairs (24 swaps), est. +680 of EV; in 12 cycles 21 swaps are done,
$6.5k moved, EV +680 estimated / +382 realised, room_wc 8.9k -> 11.6k (never below the reserve net of each sale),
turnover $9.5k of 15k. The refill still raises only $487 at the value floor on this book ($3.8k at
`alloc_refill_max_cost` 2c, $7.1k at 3c): cash_free stays near 0 with ~$6.8k locked in our own quotes, because the
market maker re-deploys what the refill frees.

## Suites (14.1)
`tests/test_mm_funding_14_1.py` 100 and `tests/test_p14_1_dryrun.py` 32; all 50 suite files green, every one N/N
(test_mm_bot 600, test_alloc 123, test_live_sim_marks 115, test_strategy 114, test_value_mode 101, test_mm_funding 100,
test_p12_alloc 95, test_turnover 94, test_cash_gate 83, test_p12_quote 80, test_write_savers 75, test_hold_target 71,
test_tilt 69, test_reduce_no 69, test_p9_dryrun 69, ...) plus `STRESS_LADDER=1 python tests/test_stress.py` 20/20.

## Deploy (14.1)
1. Code: handover restart to THIS branch's head (on top of 4ff7d91) with the live 14.0 file unchanged - behaviour is
   byte-identical to 4ff7d91 (the only new thing while the flags are off: the read-only `mm_funding` report keys).
2. File: write `settings_override.mm_funding_14_1.json` to a temp file beside `settings_override.json` and `mv` it
   over (read within 30 s). It is the 14.0 file + your live values (`alloc_mm_reserve` 20000, `mm_risk_reserve_wc`
   20000, `_corr` 4000, `worst_case_backstop_frac` 1.0, `max_worst_case_frac` 0.40, `risk_unheld_legs` "ref") + the
   five 14.1 flags + `alloc_max_edge_sell` 0.05. Validated with `mm_bot.validate_overrides`: no problems.
   Flag by flag instead: `alloc_swap_room_netting` first (it is where the EV is), then `alloc_cancel_mm_first` +
   `alloc_rank_all_markets`, then `mm_recycle_sell_first` + `alloc_refill_ignore_prefer_short`, then
   `alloc_max_edge_sell` 0.05.

## What to watch in the first hour (14.1)
- "ALLOC run: N pair(s) planned ... est. gain X" with pairs that have a BUY again, and "ALLOC sold ... -> buy ..." /
  "ALLOC bought ..." pairs completing; `alloc.swaps_done_24h` rising with `alloc.ev_gain_realised_24h`.
- `mm_risk_room.room_wc` / `room_corr` NEVER below 20000 / 4000 net of a swap's own sale: a swap that would is logged
  "held back: the swap's room check (mm_risk_reserve net of the sale) no longer passes". If room_wc falls while swaps
  run, roll the file back at once.
- `mm_funding.refill_blocked_by` should no longer show "mm_resting"; "floor" is the honest one (the book is above the
  value floor almost nowhere). `mm_funding.refill_runs` rises every cycle while cash_free < 20000.
- One whole-exchange cancel per allocator IOC, never two orders of ours in a market, and no market sold twice within
  120 s; `mm_funding.refill_holds` > 0 only briefly (a market whose sale the positions read has not shown yet).
- `alloc.turnover_hour` <= 15000, writes <= 28/min, no 429s. `mm_funding.cash_locked` says where the freed cash went.

## Rollback (14.1)
- Settings: `mv` `settings_override.mm_funding.json` (the 14.0 file) over the live file: every 14.1 flag off =
  Package 14 behaviour, `alloc_max_edge_sell` back to 0.02 (the report keys stay, read-only). Then, if the trouble is
  Package 14 itself, `settings_override.previous_live.json` (all flags off).
- Code: handover back to 4ff7d91 (the new `mm_funding` / `alloc` report keys are then simply ignored).

---
## Rollback (Package 14)
- Settings: `mv` `settings_override.previous_live.json` over the live file (every P14 flag off = c7c0107 behaviour; the
  `mm_funding` status key stays, read-only). A recycle order below Polymarket - 1c, two asks of ours in one recycled market, or one
  market sold twice by refill IOCs within a minute is a bug: roll back the file at once.
- Code: handover back to c7c0107 (status.json's `mm_funding` key is then simply ignored).
