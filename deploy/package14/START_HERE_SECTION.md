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
