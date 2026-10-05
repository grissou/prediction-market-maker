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
