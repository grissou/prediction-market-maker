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

## Rollback
- Settings: `mv` `settings_override.previous_live.json` over the live file (every P14 flag off = c7c0107 behaviour; the
  `mm_funding` status key stays, read-only). A recycle order below Polymarket - 1c, two asks of ours in one recycled market, or one
  market sold twice by refill IOCs within a minute is a bug: roll back the file at once.
- Code: handover back to c7c0107 (status.json's `mm_funding` key is then simply ignored).
