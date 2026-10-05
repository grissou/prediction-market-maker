# Package 14.2 diagnosis: where `value_sell_margin` limits an allocator SWAP sale (before any 14.2 code)

Written 5 Oct ~13:40 UTC on 124ce75 (Package 14.1 READY) and the LIVE head 4ff7d91 (Package 14), BEFORE the 14.2 code.
Question (owner, 13:20): the 14.1 builder said a paired allocator sale (a swap: `alloc_sell` with `b is not None`) is
governed only by the gain hurdle and is not floored, yet the owner raised `value_sell_margin` live "to get swaps
through". Which path applies the margin to a paired IOC sale?

## Short answer
**None.** In both 4ff7d91 and 124ce75 no code path floors a paired allocator sale at p - `value_sell_margin`. A swap's
sale price is limited only by `alloc_max_edge_sell` (edge-held at the touch, a RELATIVE floor: bid >= p / (1 +
max_edge_sell) for a long) and the pairing hurdle `alloc_min_improvement` (buy edge - sale edge-held, both at the
touch), at planning and again on the fresh book before the IOC. The margin reaches the swaps only INDIRECTLY, and in
the wrong direction: raising it makes the refill and the stale-MM IOCs sell MORE, which takes holdings and turnover away
from the swaps and, in 14.1, turns stale-MM holdings into refill-only legs. Live (4ff7d91, paused) no swap can happen
at any margin; the raise only produced more refills (sales with no buy), which the "ALLOC run: N pair(s) planned" line
counts as pairs.

## Every place the margin is read (124ce75 line numbers; 4ff7d91 in brackets)
| path | what it does with value_sell_margin | a paired (swap) sale? |
|---|---|---|
| `value_side_prices` 3369-3380 [3315-3326] | the reducing side's limit p -+ margin | quotes only |
| `compute_quote` 3221 [3167], `decide` -> `value_floor_quote` 6128 [6070] | a resting reducing ask >= p - m (bid <= p + m), normal AND reduce-only quoting | no (allocator orders never go through compute_quote, Config comment 1162-1164) |
| `exit_quote` value_floor 3414 / 3421, called 5973 [5917] | the election-night exit | no |
| recycler `mm_recycle_quote` 11663 / 11698 [11512 / 11536] | the recycled ask / buy-back never past the floor | no (a resting quote) |
| set ladder `alloc_ladder_tick` cap 11232, check 11267 [11091 / 11126] | the rich-leg NO ladder <= p + m | no (resting covered NO sales, no paired buy) |
| `mm_floor_ok` 11755-11764 [11578-11582] | refill price rule: long >= p - m, short <= p + m (14.1: or `alloc_refill_max_cost` below half the target) | called only from the refill paths below |
| `alloc_plan` held list 10495 [10407] | `floor_ok` stored on every holding (mm_refill_fast) | read ONLY by the refill loop 10607 [10511]; the pairing loop never reads it |
| `mm_refill_held` 11906 / 11909 [11610] | a stale-MM holding at or above the floor becomes an "mm" refill leg | indirectly (below) |
| `alloc_sell` mm leg 11040 [10903] | the stale-MM IOC: concession + floor | no: mm legs are never paired |
| `alloc_sell` 11051 [10914] | `fast and b is None and not mm_floor_ok(...)` -> "gone" | **no: refills only (`b is None`)** |
| `alloc_sell` paired check 11045-11050 [10908-10912] | `edge > alloc_max_edge_sell` or `bedge - edge < alloc_min_improvement` -> "gone" | **the only price rule on a swap sale: no margin** |
| `alloc_sell` 11077 [10940] `order["_alloc_paired"] = True` -> `wire_order` 2516-2526 [2463-2472] | the marker is only stripped before the request | no floor at send |
| `alloc_send` 11129 -> `place_orders` 7471 -> cash gate `cash_gate_orders` 7316 | whole-exchange cancel, the IOC at the checked touch, the gate caps by CASH only | no price rule at all |
| `alloc_plan` pairing filter 10490 / 10633-10640 [10404 / 10537-10543] | `rich = edge > alloc_max_edge_sell` (not a swap candidate), `sq` drops mm / refill_only legs, the hurdle `o.edge - h.edge < alloc_min_improvement` | the pairing uses edge-held, never p - margin |
| `alloc_edge_held` 10398-10412 | (p - bid) / bid for a long, (ask - p) / (1 - ask) for a short | the definition (below) |

## How the margin nevertheless changes the swaps (indirect, both make swaps FEWER when it is raised)
1. **The refill comes first and eats the swap candidates** (`alloc_plan` 10601-10631 [10506-10535]): while cash <
   `alloc_mm_reserve`, the B2 refill loop sells the lowest edge-held holdings with NO buy, skipping only those whose
   `floor_ok` is False. A bigger margin passes more of them: they are sold as refills (their `avail` and the hour's
   turnover `left` consumed) before the pairing loop sees them - the same low-edge holdings a swap would have sold.
   (Each refill is also applied to `hyp`, so with 14.1's room netting the swaps' room check 10659 sees the freed room
   - a small effect in the other direction.)
2. **Stale-MM holdings become refill-only** (`mm_refill_held` 11906-11909, merge 10523-10527 [10436-10439], `sq`
   filter 10633 [10537]): a stale-MM holding whose touch is at or above p - margin becomes an "mm" leg that REPLACES
   its value entry and is never paired ("P14: MM legs refill only"). A bigger margin withdraws more holdings from the
   swap pool even when no refill runs (cash at the reserve).
3. **Live (4ff7d91) the pause removes every swap anyway**: `mmr_paused` (room_wc < 20k) empties the buy-level list
   (10471 [`if getattr(self, "mmr_paused", False) and levels: ... levels = []`]); only refills are left, at any
   margin. 14.1's `alloc_swap_room_netting` (10566-10569) is what lets swaps run while paused.

Measured on the 10:21 state (`tests/p14_1_state.py` on /home/claude/snap04, the 14.1 dry-run seed; the first allocator
run, one cycle after the file lands, then a planning call at the cash read and at cash = the reserve):

| code + file | value_sell_margin | first run: pairs (swaps) | est. gain | plan at the cash read: refills / swaps | plan with cash = reserve: swaps / $ / est. | stale-MM refill legs |
|---|---|---|---|---|---|---|
| 4ff7d91 file (14.0, live) | 0.005 | 4 (0) | 0 | 0 / 0 | 0 / 0 / 0 (paused) | 0 (mm_resting 16) |
| 4ff7d91 file (14.0, live) | 0.03 | 10 (0) | 0 | 9 ($1.4k) / 0 | 0 / 0 / 0 (paused) | 3 ($483) |
| 14.1 file | 0.005 | 28 (24) | 680 | 0 / 24 ($10.1k) | 24 / $10,068 / 650 | 0 |
| 14.1 file | 0.03 | 31 (5) | 274 | 25 ($7.0k) / 5 ($3.6k) | 13 / $5,338 / 440 | 13 ($4.6k) |

So raising the margin to 0.03 on 14.1 cuts the first run's swaps from 24 to 5 (est. +680 -> +274) and, with no
refill at all, still from 24 to 13 (stale-MM holdings withdrawn); on the live 14.0 code it buys 9 refills and no swap.
**Returning it to 0.005 is right for the swaps as well as for the quotes.**

## What the live bot does with the margin at 0.005 vs raised
- At **0.005**: every resting reducing quote (normal and reduce-only), the recycler, every refill and stale-MM IOC, the
  set ladder and the exit stay within 0.5c of p. Swaps (14.0: none, paused; 14.1: netted) sell down to edge-held
  `alloc_max_edge_sell` at the touch (0.02 live: 1c below p at p 0.5; 0.05 in the 14.1 file: 2.4c at p 0.5, 3.8c at p
  0.8, 0.5c at p 0.1) when the buy beats the sale's edge-held by `alloc_min_improvement` 0.03.
- **Raised** (e.g. 0.03): the same swap limits (unchanged), but every reducing quote in the book may rest up to 3c
  below p (in value_mode `decide` re-floors the final quote at p - 0.03, so the skews may push asks that low), the
  recycler offers stale MM at up to 3c below p, refills and stale-MM IOCs sell at up to 3c below p: EV given up across
  the book, plus fewer swaps (1-2 above).

## The swap's "cost" is already inside edge-held
`alloc_edge_held` (10398) and the planner's own measure (10481-10486) are, at the sale price px:
long (p - px) / px; short (px - p) / (1 - px). The cost of the sale per $ it frees is exactly that: a long sells q
shares at px, freeing q x px and giving up q x (p - px) of outcome value -> (p - px) / px per $; a short's buy-back
(a covered NO sale at 1 - px) frees q x (1 - px) and gives up q x (px - p) -> (px - p) / (1 - px) per $. The planner's
gain `x * (o.edge - h.edge)` (10679) is therefore already NET of the sale's cost (and of the buy's spread: the buy edge
is at its ask). The owner's rule "buy edge-held >= sale edge-held + sale cost + min gain" counts the same quantity
twice if edge-held is taken at the sale price; 14.2 counts it once: buy edge - sale edge-held (at the sale price)
>= `alloc_swap_min_gain`, and since `alloc_min_improvement` measures exactly the same gap, 14.2's hurdle is the stricter
of the two: max(`alloc_min_improvement`, `alloc_swap_min_gain`).

## What 14.2 therefore builds (see the Config block "P14.2")
- `alloc_swap_sell_margin` > 0 gives a swap sale its OWN explicit price limit, p - margin (a short's buy-back p +
  margin), in the pairing filter (`alloc_plan`: a holding beyond it is no swap candidate, `blocked_by` "swap_floor")
  and again on the fresh book right before the IOC (`alloc_sell`, which is the send-time check: the IOC goes out at
  that very touch). This is a NEW limit, not a loosening: there was no margin on swaps to loosen. Together with
  `alloc_max_edge_sell` (still the candidate rule) the tighter one binds: the 3c floor is tighter than edge-held 5%
  where p > ~0.63 (long) and the 5% is tighter below.
- `alloc_swap_min_gain` raises the pairing hurdle to max(`alloc_min_improvement`, it) for swaps (long / short sales),
  at planning and before the sale (`blocked_by` "swap_gain").
- Refills, stale-MM IOCs, quotes, reduce-only quotes, the recycler, the set ladder, exits, NO+NO set and spare-cash
  pairs: untouched (they keep `value_sell_margin` / their own rules). Points 1-2 above are refill behaviour and stay
  as they are; they vanish once `value_sell_margin` is back at 0.005.
- Reporting: "ALLOC SWAP ..." journal lines at plan and at fill, `alloc.swaps` in status.json, and a WARNING while the
  swap margin is on with `value_sell_margin` > 0.01.
