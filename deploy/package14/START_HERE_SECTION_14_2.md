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
