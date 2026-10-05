# Package 14.1 diagnosis: the stuck fast refill and the stuck swaps (on 4ff7d91, before any fix)

Written 5 Oct ~11:20 UTC, BEFORE the 14.1 code. Reproduced on the fake exchange with the live snapshot `/home/claude/snap04`
(the tests/test_p9_dryrun.py harness: all 237 markets, other traders' books, Polymarket references, positions, marks),
the Package 14 file `deploy/package14/settings_override.mm_funding.json` + `risk_unheld_legs` "ref" applied through the
bot's own override path, full `Bot.cycle`s on a simulated clock (30 s a cycle). The state was moved toward the owner's
10:21 numbers (tests/p14_1_state.py, also used by the dry run):

| | owner, 10:21 | fake, after the adjustment |
|---|---|---|
| account | 102.0k | 102.0k (cash set so) |
| cash_free (cash gate, after our resting orders' locks) | 1,903 of 20,000 | 1.9-2.4k (a constant "held elsewhere" amount taken off the P&L cash read) |
| room_free.wc | 9,509 of 20,000 (value adds paused) | 8.6k (paused) |
| MM inventory / stale / oldest | 10.5k / 7.9k / 23.7 h | 10.5k / 7.9k / 23.7 h in 21 middle-band markets |
| book by edge-held (at the touch) | 4.1k <= 2% (14), 16.8k 2-5% (46), 44.2k 5-10% (38), 23.6k > 10% (14) | 4.2k (15), 14.1k (19), 42.1k (42), 28.6k (15) |

How: the snapshot's books converged 20% toward Polymarket (tilt 0.137 on 4 Oct -> 0.109 now); MM lots placed on the
middle-band (p 0.15-0.85), unpinned, low-edge (< the 5% hurdle) holdings whose touch is beyond the value floor (as live:
the stale MM that "rests"), aged 6.5-23.7 h for 7.9k and 2 h for 2.6k (positions grown where the snapshot held less:
the inventory grew since); the exchange's NO+NO set rule on. The fake does not model other traders filling our resting
quotes (the recycler's resting orders never fill here) and Polymarket is flat.

## What 4ff7d91 does on that state (reproduced)
- **Fast refill:** the first run sells $487 (four holdings at or above the floor: Rep Nebraska Senate NO, Dem SC-01,
  Dem MT-01, 1 share of Dem Michigan Senate NO); from then on EVERY plan finds nothing: `refill.runs` stays 1, cash_free
  2.4k -> 3.0k and flat, `refill.blocked_by` = {mm_resting 16, floor 4, set_bids_gt_1 1, prefer_short 24, mm_risk_reserve 1}
  (live: mm_resting 34, prefer_short 8, set_bids_gt_1 6, mm_risk_reserve 1). Same shape: mm_resting dominates.
- **Allocator:** a forced hourly run plans 4 pairs, all reserve refills (no buy), est. gain 0 (live: 4 / 2 / 0, gain 0).
- **Recycler:** 16 markets recycled (11 asks on longs, 5 buy-backs on shorts), every one resting at fair -+ 1c / the
  floor, above the other traders' best ask (longs) or below their best bid (shorts) - nothing fills in the fake, and live
  only 19 fills in 13 min.

## Root causes, by weight
1. **The refill is starved by PRICE, not by our orders.** `blocked_by["mm_resting"]` (mm_refill_held) means: the stale MM
   shares' best OTHER bid (book with our orders stripped) is more than `mm_recycle_concession` (1c) from fair, or below
   the value floor p - `value_sell_margin` (0.5c): the IOC is refused and the shares are left to the recycler. Our own
   MM quotes are NOT what blocks: the book is already stripped of them, and every allocator IOC (`alloc_send`) already
   cancels our orders on that exchange first (one whole-exchange cancel) and sends the sale in the same cycle. In the
   10:21 book (tilt ~0.11) the holdings sellable at the top level are, by cost below Polymarket:
   | sale price vs p | $ at the touch | markets | EV given up |
   |---|---|---|---|
   | at / above the floor (<= 0.5c below) | 786 | 11 | 6 |
   | 0.5-1c below | 1,466 | 4 | 31 |
   | 1-2c below | 2,583 | 8 | 102 |
   | 2-3c below | 2,576 | 9 | 98 |
   | more than 3c below | 43,036 | 56 | 3,616 |
   So with "never below the value floor" a refill can raise well under 1k here: the 18k deficit is NOT reachable by
   refill sales at the floor while the tilt is ~0.11, whatever the ordering or the blockers. (Up to 2c below p it would
   be ~4.8k for ~140 of EV; up to 3c ~7.4k for ~240.)
2. **Swaps are switched off by the risk-room pause.** room_free.wc 8.6k < `mm_risk_reserve_wc` 20k -> `mmr_paused`:
   `alloc_plan` drops EVERY buy level (`blocked_by["mm_risk_reserve"]` 1 = the whole level list), `alloc_sell` refuses
   paired sales and `alloc_buy` the buys. Hence "pairs planned, est. gain 0": only refills are left. Also, a paired buy
   spends only cash ABOVE `alloc_mm_reserve` (cash_left - 20k): with cash at 2k no swap's buy could go even unpaused (its
   own sale's proceeds go to the reserve first). On the same book, unpaused, at the touch (gain already net of both
   spreads; cash = the reserve, swaps only):
   | alloc_max_edge_sell | alloc_min_improvement | swaps | $ moved | est. EV gain |
   |---|---|---|---|---|
   | 0.02 (live) | 0.03 | 4 | 847 | 99 |
   | 0.05 | 0.03 | 24 | 10,066 | 760 |
   | 0.05 | 0.06 | 22 | 8,984 | 700 |
   | 0.08 | 0.03 | 37 | 15,000 (turnover cap) | 958 |
   | 0.08 | 0.06 | 22 | 8,984 | 700 |
   Paused: 0 at every setting.
3. **A run in flight blocks the fast refill.** `alloc_tick` runs `mm_refill_tick` only while `not self.alloc_pairs`. Pairs
   sold before the pause wait for their buy up to ALLOC_BUY_WAIT (900 s) and pending ones up to `alloc_interval_s`
   (3600 s) when their sale keeps being refused (busy, cash gate, paused): during the build, 3 sold pairs waited for buys
   the pause refused and `refill.runs` stayed 0 for the whole wait. `alloc_sells_stopped` (a paired level gone) also stops
   every later refill sale of the run.
4. **`blocked_by` mixes buy-side and sale-side counters.** `prefer_short` (L2: a buy level dropped because the other leg
   can be shorted) and `mm_risk_reserve` (the level list dropped) never block a refill SALE in 4ff7d91; they are counted
   in the same dict as the sale-side reasons (mm_resting, floor, set_bids_gt_1, in_flight), so the refill's blocked_by
   looks like prefer_short stops refills. It does not.
5. **Recycler buy-backs and cash.** On this seed every recycled short is a covered lone-NO sale ("sell NO"): the gate
   charges 0 and a fill FREES (1 - price) a share (5 buy-backs: 0 locked, 2,486 freed when filled). A buy-back locks cash
   only for its NO+NO set part (1.0 a share at the gate, as the exchange's set rule) or as an uncovered YES bid (price a
   share, the exchange locks it at placement). The live lock could not be reproduced without the live order list
   (status `locked_in_orders` / the open orders show which). What IS reproduced: the recycler's prices (fair -+ 1c, the
   floor) rest beyond the other traders' touch in a tilted book, so recycling is slow either way.
6. **Measurement note.** `cash_free` is the gate's cash AFTER our resting orders' locks, and the MM bids are most of those
   locks (11.8k here; live `locked_in_orders` ~12-19k): the 20k target counts the cash MM is already quoting with as
   "not funded". Not changed in 14.1 (the owner's definition); reported in mm_funding as `cash_locked` for context.

## What this means for the fixes
- (1) cancel-first: harmless and kept (one whole-exchange cancel, then the IOC, the quoting side held until the
  positions read shows the sale), but on its own it frees almost nothing: the floor binds. `mm_resting` stops being a
  blocker: stale MM shares become ordinary ranked candidates, judged by the same floor as every refill.
- (2) ranking / every cycle / a blocked candidate never stops a run: fixes cause 3.
- (5) room netting (+ the swap's buy may spend its OWN sale's proceeds below the cash reserve): fixes cause 2.
- (4) prefer_short: a reporting fix in practice (cause 4); the flag also keeps the L2 scan out of a short-cash refill.
- (3) sell-first / net-cash buy-backs: correct accounting; on this seed it changes little (cause 5).
- The cash target itself: only selling below the floor reaches it in this book. 14.1 adds `alloc_refill_max_cost`
  (default 0 = the floor, NOT in the staged file) so the owner can decide with the numbers above.

## Confirmed after the fix (tests/test_p14_1_dryrun.py, analysis/p14/DRYRUN_14_1.md)
On the same 10:21 state, the staged 14.1 file applied through `check_overrides` over the running 14.0 file: the first
allocator run plans 28 pairs (24 swaps, est. +680 of EV) instead of 4 refills at est. 0, and in 12 cycles 21 swaps
execute, $6.5k moved, EV +680 estimated / +382 realised, the worst-case room 8.9k -> 11.6k and never below the reserve
net of each swap's own sale; the refill still raises only $487 at the value floor (cause 1 is a price fact, not a bug),
$3.8k at `alloc_refill_max_cost` 2c and $7.1k at 3c. Cash_free does not pile up (it ends near 0 with $6.8k locked in
our own quotes): the market maker re-deploys what the refill frees, which is what the reserve is for - `room_free.wc`
and `cash_locked` are the honest funding figures, not `cash_free` alone.
