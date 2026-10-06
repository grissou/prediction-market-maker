# Package 15: the harvest ladder alone + a per-state collateral cap (owner, 6 Oct 11:00 UTC)
Branch `claude/package15` (the worktree could not check out `claude/mm-funding`, held by another worktree), built on the
LIVE head **64f27c8** (Package 14.2). NOT merged with Package 13: only Package 13's harvest ladder is ported, onto
14.2. Every new setting is OFF by default; with the flags off the code is pinned byte-identical to 64f27c8 on a grid with
fills (orders, quotes, order notes, status.json values, health, summary line, alloc_plan: `tests/test_p15.py`) and on
the live state (`tests/test_p15_dryrun.py` scenario 0). Status keys added only while on (`Bot.HARVEST_KEYS`: `harvest`,
`state_caps`; the split inside `mm_funding` only with the ladder on).

## The owner's brief (verbatim)
"Package 15 (build first): the harvest ladder ALONE, ported onto 64f27c8. Only `tilt_harvest_ladder` and its settings;
flags off = 64f27c8 byte-identical. Collateral: `harvest_total_usd` (default 10,000) carved from `alloc_mm_reserve` (MM
keeps the rest); funded by the refill/swaps like the reserve; split shown in `mm_funding` / `harvest`. Per-state
collateral cap `state_max_usd` (default 15,000) across ladder levels, value buys and takes (Rhode Island alone is ~23k
now); existing positions kept, adds stop at the cap. Levels +0/2/4/6c, asks above longshots and bids below favourites,
edge >= 8% per dollar, never crossing our own quotes. Tests, dry run on the live state, staged file in the live format
(ONLY the ladder + state cap on), READY as 'Package 15'."

## Files
- `settings_override.harvest.json` = the live file (`deploy/package14/settings_override.mm_funding_14_2.json` with
  `value_sell_margin` 0.01, as live) + `tilt_harvest_ladder` true, the harvest defaults written out (`harvest_offsets`
  [0, 0.02, 0.04, 0.06], `harvest_level_usd` 3000, `harvest_min_edge` 0.08, `harvest_max_markets` 237,
  `harvest_writes_frac` 0.4, `harvest_requote_s` 900), `harvest_total_usd` 10000, `state_max_usd` 15000. Nothing else
  changed (`capital_ceiling_adding_size_factor` 0.5, `arb_enabled` false, `ref_tilt_headline` true kept). Validated
  with `mm_bot.validate_overrides`: no problems. If your live file changed since 14.2 in anything else, carry it over.
- Rollback file: the 14.2 file (`deploy/package14/settings_override.mm_funding_14_2.json`, with `value_sell_margin`
  0.01 as you run it).

## The settings (Config, block "# --- P15", and OVERRIDABLE with ranges)
| setting | default | range | staged |
|---|---|---|---|
| `tilt_harvest_ladder` | false | bool | true |
| `harvest_offsets` | [0, 0.02, 0.04, 0.06] | list of 1-8 in 0-0.2 | same |
| `harvest_level_usd` | 3000 | 0-50000 | 3000 |
| `harvest_min_edge` | 0.08 | 0-2 | 0.08 |
| `harvest_max_markets` | 237 | 0-1000 | 237 |
| `harvest_writes_frac` | 0.4 | 0-1 | 0.4 |
| `harvest_requote_s` | 900 | 60-7200 | 900 |
| `harvest_total_usd` | 10000 | 0-100000 | 10000 |
| `state_max_usd` | **0 (off)** | 0-200000 | **15000** |
`state_max_usd` defaults to 0, not 15,000: every P15 setting is OFF by default (flags off = 64f27c8); the staged file
sets the brief's 15,000.

## What it does
**The ladder** (`Bot.hv_tick`, cycle step 6e, after the allocator, before quoting). Every market with a fresh liquid
race-scaled p (`alloc_p`) - not a basket leg, not a headline U.S. House / Senate market (as the allocator, unless
`alloc_headline`), not a P12 set-ladder market:
- LONGSHOT (p <= 0.10): resting YES ASKS at the best other ask + 0 / 2 / 4 / 6c; FAVOURITE (p >= 0.90): resting YES BIDS
  at the best other bid - 0 / 2 / 4 / 6c. A level only where its edge per $ of collateral is >= 8%: (ask - p) / (1 - ask),
  (p - bid) / bid. A level at / through the other side of the book, at / through our own orders there, or at the price
  of our own resting quote on that side (P13 C) is skipped, never clipped. A market whose cached touch the latest bulk
  prices show has moved waits a cycle (P13 DRYRUN finding 3: no level crossing).
- Each level `harvest_level_usd` of collateral. An ask sells the YES we hold first (covered, no cash), then is a short
  (1 - price a share); a bid where we hold NO goes out as a covered "sell NO" of the NO held (cash-free; that level
  only), the next levels are YES buys.
- Caps: the market's position at p + the ladder's adds (valued at max(lock, p)) <= `alloc_max_contract_usd` (10,000: the
  per-market $ cap value buys keep; `kelly_max_market_frac` 2% ~ $2k at risk is the quoter's per-quote Kelly limit and
  would bar any $3k level); the state cap; the carve-out; the cash gate keeping the MM's reserve; NO adds while value
  adds are paused on the risk room (`mm_risk_reserve_*`: the ladder's levels are tail adds, as the quoter's - covered
  levels only); reduce-only (the tripwire) pulls everything; at most `harvest_max_markets` (markets already laddered
  first, then the best edge at the touch) and `harvest_writes_frac` of the writes left (pulls always), batch_size orders
  a write.
- Resting MAX_ORDER_TTL (2 h), tagged "harvest" in the order notes, hidden from the quote planner (plan_exchange; the
  quote's bid kept a tick below our harvest asks, its ask above our harvest bids). On a laddered market the market
  maker does not quote the ladder's side except what reduces a position, net of what the harvest levels there already
  offer (P13 RT13-2); the other side quotes as before.
- Re-quoted when a level is > 1c off its target or p moved > 1c, else at most every `harvest_requote_s` (an order
  exactly at its target keeps its queue spot); a level gone (filled) is re-placed at once. Pulled at once when p is
  unknown, the pre-close stop, reduce-only, the state over the cap, the pause, or the flag off; the kill switch cancels
  everything. A batch whose outcome is unknown (timeout / 409 / 5xx): its levels are registered as unconfirmed and
  ADOPTED as harvest levels from the next orders read - never placed twice (P13 RT13-1).
- Fills are VALUE positions (held to the outcome; the value floor protects them): journal "HARVEST fill <market>: sold
  N YES @ x (level k; p, edge $) - a VALUE position", fill class "harvest" in `mm_carry_24h` (key only once one
  exists), never MM inventory.

**The carve-out** (`harvest_total_usd`, a budget for RESTING collateral = the cash our resting harvest levels lock):
- The MM's own reserve = `alloc_mm_reserve` - `harvest_total_usd` = 20k - 10k = **10k** (`mm_reserve_effective`). The
  ladder places only through the cash gate with those 10k held back: it never takes free cash below 10k. The other way
  round, the quotes' plan budget leaves the ladder what it plans but has not placed yet (`hv_quote_hold`; 0 when the
  ladder plans nothing more - paused, capped, off): the MM never eats the ladder's part.
- The whole 20k is still refilled: the refill / fast refill, the swaps' and spare-cash buys' "cash above the reserve"
  and `take_respect_reserve` count the cash locked in resting harvest levels (up to 10k) as reserve: reserve cash = free
  cash + harvest resting; the refill target stays `alloc_mm_reserve` (20k; `mm_funding.cash_target`).
- A FILLED level is a value position: its collateral is value collateral (state cap, `alloc_max_contract_usd`) and it
  frees its budget slot - the ladder recycles its 10k into fills over time, the refill tops the reserve back up.
- Shown in `mm_funding` {cash_target 20000, mm_reserve_effective 10000, harvest_carve {budget, resting, free},
  reserve_cash} and `harvest.carve` {total, used, free} (+ `harvest.held_from_quotes`).

**The state cap** (`state_max_usd`): `state_of(label)` maps "Rep Rhode Island Senate", "Dem Rhode Island Governor",
"Ind RI Governor", "Dem RI-01 House race" to "RI"; the 4 headline U.S. House / Senate labels have no state (all 237
live labels checked: 233 in 45 states). A state's collateral = longs q x p, shorts |q| x (1 - p) (p the liquid
race-scaled Polymarket price, else the exchange's mark, else the fair value) + the cash our resting orders there lock.
Existing positions are KEPT (never a forced reduce); ADDS stop at the cap: the ladder, the allocator's buys (swaps,
spare cash; `blocked_by` "state_cap", re-checked before the IOC), the stale-quote takes (cut to the room) and the
quoter's adding side in the tails (outside 0.15-0.85; the R3 ladder too). The middle band (market making) is not capped.
`state_caps` {every state above 50% of the cap: {collateral, cap, blocked_adds}}; summary " | state caps RI 23.1k/15k".

## Rhode Island
On the 4 Oct snapshot moved to the live numbers RI is **$13.3k** (Rep RI Senate -8,945, Rep RI Governor -2,787, Ind RI
Governor -1,667 shares). You see ~23k live now: with the staged 15k cap every RI add stops at once (the positions stay),
`state_caps.RI.blocked_adds` counts what is held back.

## Deploy
1. Code: handover restart (`deploy/handover-restart.sh`) to this branch's head with the live 14.2 file unchanged.
   Nothing changes (flags off); status.json has no `harvest` / `state_caps` key.
2. File: write `settings_override.harvest.json` to a temp file next to `settings_override.json` and `mv` it over (read
   within 30 s).

## First-hour watch list
- `harvest.levels_resting` / `collateral_resting` <= 10,000 (`harvest.carve.used` <= `total`); `mm_funding
  .mm_reserve_effective` 10000, `cash_target` 20000.
- **Expect no adding levels at first if value adds are paused** (`mm_risk_room.paused`) or free cash is below 10k: the
  ladder then rests only covered levels (`harvest.blocked_by` "mm_risk_reserve"); it starts once the refill has free
  cash above 10k and the risk room is back above 1.1 x 20k. That is the dry run's live-state scenario A.
- "HARVEST <market>: ask/bid YES (p, touch, edge at touch): N@px ..." lines: every level >= 8% per $ at p; no level at or
  through the other side of the book, none at or through our own orders, none at our own quote's price (open orders /
  recorder: one order per market / side / price).
- Rhode Island: no new RI adds (`state_caps.RI.blocked_adds` rising, no RI harvest level that is not covered, no RI
  allocator buy / take); RI positions unchanged by the cap.
- Writes <= 28 a minute (the ladder <= 0.4 of what is left; `write_budget_wait_total` flat).
- "HARVEST fill ... a VALUE position" lines as levels fill; `harvest.filled_24h`, `edge_filled_24h`.

## Rollback
`mv` the 14.2 file (with `value_sell_margin` 0.01) back over `settings_override.json`: the levels are pulled on the next
cycle (flag off), the state cap is off. Code rollback: 64f27c8.

## Dry run (`analysis/p15/DRYRUN_P15.md`, `tests/test_p15_dryrun.py` 34 checks)
- Flags off: the live file on 64f27c8 and on this head, 12 cycles on the live state: identical orders and status; the
  staged file with the two switches off = the live file.
- A, the live state as it is (free cash ~1.6k, value adds paused on the worst-case room): the ladder rests only covered
  levels (3, $0 locked; blocked_by mm_risk_reserve) and waits for free cash above the MM's 10k and the room. The state cap
  works at once: the allocator's swap buys took RI 13.3k -> 15.0k and stopped there.
- B, funded (+25k cash with the file): 6 levels in 5 markets, $5.0k of the 10k carve-out resting, the MM's 10k held at
  every harvest send, every level >= 8% per $ at p, none through the book / our own orders / at our own price, the
  ladder <= 7 writes a minute.
- C, +3c tilt rise over 1 h on B: 8 harvest fills, 9,330 shares, $8.1k of collateral at $1.0k of edge at p (12.6% per
  $); EV outcome +1.8k.

## Tests
`tests/test_p15.py` 138 checks; `tests/test_p15_dryrun.py` 34; all 54 suite files N/N, `STRESS_LADDER=1` 20/20.
