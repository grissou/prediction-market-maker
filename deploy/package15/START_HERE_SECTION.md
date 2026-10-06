## Package 15 (READY, 6 Oct; branch `claude/package15` on the LIVE head 64f27c8 = Package 14.2; NOT merged with Package 13): the harvest ladder alone + a per-state collateral cap (OFF by default; staged file in `deploy/package15/`)
**The brief (11:00):** the Package 13 harvest ladder ALONE on 64f27c8; `harvest_total_usd` (10k) carved from
`alloc_mm_reserve`; a per-state cap `state_max_usd` (15k) on ladder levels, value buys and takes; levels +0/2/4/6c, asks
above longshots, bids below favourites, edge >= 8% per $, never crossing our own quotes.
**Built (flags off byte-identical to 64f27c8):** `tilt_harvest_ladder` + `harvest_offsets` / `_level_usd` / `_min_edge`
/ `_max_markets` / `_writes_frac` / `_requote_s` (P13's ladder: covered rules, re-quotes, pulls, RT13-1 adoption, RT13-2
reduce cap, the P13 C own-price rule, the stale-touch wait), capped per market by `alloc_max_contract_usd`, by the state
cap and by the carve-out, no adds while value adds are paused. `harvest_total_usd`: a budget for RESTING collateral;
the MM keeps `alloc_mm_reserve` - it (10k) - the gate holds it back from the ladder, and the quotes leave the ladder its
planned-but-unplaced part; the refill / swaps / spare cash / take reserve count the ladder's resting collateral as
reserve, so the refill target stays 20k; a filled level is a value position and frees its slot. `state_max_usd` (0 =
off; staged 15k): `state_of(label)` (233 of the 237 live labels in 45 states, the 4 U.S. headline ones none); positions
kept, adds stop at the cap (ladder, allocator buys, takes, the quoter's tail adds). Status `harvest`, `state_caps`,
`mm_funding` {mm_reserve_effective, harvest_carve, reserve_cash}; journal "HARVEST ..." / "HARVEST fill ..."; fill
class "harvest"; summary " | harvest ... | state caps ...".
**Dry run** (`analysis/p15/DRYRUN_P15.md`, 34 checks): the live state as it is (free cash ~1.6k, value adds paused):
the ladder rests only covered levels (it waits for free cash > 10k and the risk room) - RI 13.3k on the snapshot, the
swaps took it to the cap 15.0k and stopped; funded (+25k): 6 levels / 5 markets / $5.0k of the 10k, every level >= 8%,
none crossing; +3c tilt hour: 8 fills, $8.1k collateral, $1.0k edge at p (12.6% per $), EV +1.8k.
**Suites:** `tests/test_p15.py` 138 + `tests/test_p15_dryrun.py` 34; all 54 suite files N/N + `STRESS_LADDER=1` 20/20.
**Deploy:** handover to the branch head, then `mv` `settings_override.harvest.json` (= the 14.2 file with
`value_sell_margin` 0.01 + the P15 keys) over the live file. **Watch:** levels resting <= 10k, no level crossing or at
our own quote's price, edge >= 8% at every level, MM reserve effective 10k, Rhode Island adds blocked, writes <= 28.
**Rollback:** the 14.2 file; code 64f27c8. Details: `deploy/package15/README.md`.
