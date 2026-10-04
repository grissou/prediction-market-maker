# Package 13 red team: crashes and duplicate orders only

Base: 3e635d7 (claude/finisher-package9, with the staged file deploy/package13/settings_override.aggressive.json).
Scope, set by the owner: **crashes and duplicate orders only**. Strategy, EV, risk appetite and spec choices were not
reviewed.

Method: the code was read adversarially and then run. `tests/test_p13_redteam.py` runs full `Bot.cycle`s on the fake
exchange, with the staged file applied through the bot's own override path (`check_overrides`) on a simulated clock.
Every cycle is audited. Each finding was first reproduced by a check that fails on 3e635d7. To confirm this, the test
file was run against `git show 3e635d7:mm_bot.py`: 79/90 checks passed, and every RT13-1..4 check failed, plus the
ApiError battery. Each finding was then fixed minimally. All four fixes run only on Package 13 paths (harvest ladder
on, election active, sleeve legs held), so with the flags off the bot is byte-identical. The identity grids still pass:
test_p13 146/146 and test_p13b 128/128.

## Findings

| id | severity | what | where (function) | how found | fix (commit) | test |
|---|---|---|---|---|---|---|
| RT13-1 | high | A harvest batch that timed out **after** its levels landed: the bot never learned about those levels. There was no unconfirmed-order recovery and no forced re-read. Once `pending_seconds` (90 s) passed, the same ladder was placed a second time on top of them, so two of our orders rested at one price ($3000 a level, twice). The orphaned levels also looked like ordinary quote orders to the quote planner. | `Bot.hv_tick` (the `except ApiError` branch of the batch loop) | Fake hook `batch_error_after`, armed for the first `hv_tick` batch. Two orders at 0.82 rested on one market at cycle 4. | ee71a8d. On an ambiguous error (status 0 / 409 / 502 / 503 / 504, as `apply_batch` treats it): set `orders_stale`, and register each order in `unconfirmed` with its harvest notes. The landed levels are then adopted as harvest levels (`match_unconfirmed`), hidden from the quote and re-quoted as the ladder. | RT13-1 (4 checks), and the battery's "ApiError on place / cancel / after landing" |
| RT13-2 | medium | On a laddered market, the quote's reduce-only side (`hv_sides`) was capped at the whole position, even though the harvest levels already offer it. The harvest planner subtracts the quote's resting asks from the YES it treats as covered, but the quote did not subtract the harvest's. So the same held shares were offered twice: harvest asks covering 2000 YES held, plus a quote ask of 100 more. If both fill, the extra is an unintended short (YES side), or a flip to long on the bid / NO side. | `Bot.decide` (the `hv_s` / `el_s` caps), `Bot.ladder_caps` | A synthetic "Hv1 House" market with 2000 YES held and the ladder on. The quote ask 100 @ 0.095 rested next to harvest asks of 3488 + 1461. | ee71a8d. The reduce-only cap is the position less what our resting harvest levels on that side already offer (new `hv_side_qty`, which returns 0 without a ladder). | RT13-2 (4 checks) |
| RT13-3 | high | **The momentum sleeve re-bought a leg after a stale positions read.** `mom_reconcile` cut each leg to the position read. When that read lagged the sleeve's own buy, or was the last good read on a 409 (`positions_fallback`), the leg was dropped as "gone" and the market became a fresh candidate. The same buy then went out again. The first purchase was left outside the sleeve, classed VALUE, where the bucket sell-down could sell it, and the sleeve's cost was undercounted, so it bought past its target. Package 9's basket already guards this case (`BASKET_TRADE_GRACE`); the sleeve did not. | `Bot.mom_reconcile` (with `mom_buy_step` / `mom_exit_step`) | Fake `positions()` returning the pre-buy read for one cycle (lag), or raising 409 with the pre-buy cache. Legs 320 / 120 were dropped and 320 was bought again. | c0d5f3a. A leg the sleeve traded within `BASKET_TRADE_GRACE` (120 s) is not reconciled (`mom_last_trade`, this run only). During the grace the candidate filter (position != leg) already blocks a re-buy. | RT13-3 (9 checks) |
| RT13-4 | high | **Election night sold a called loser's YES as "covered" again and again.** `el_take_plan` takes the covered amount from `cash_free(e)`, which reads `ex.inv`. That value is the last cycle's position before decide, and it is never refreshed on a market traded since, because the quote loop skips traded markets. So each cycle re-sold the 3000 YES it had already sold, flagged covered, which means no cash counted, no collateral room check and no holdback spend. On the fake that is 12,000 "covered" shares from 3000 held, and a 9000 short. | `Bot.el_take_plan` | Clock at 3 Nov 23:30 UTC, the Mo1 race called (0.99 / 0.01), 3000 YES held on the loser, takes and the ladder off. | c0d5f3a. `ex.inv = q` (this tick's position, already kept current with the tick's own trades) before `cash_free`, as `pair_followup_step` does. | RT13-4 (4 checks) |
| RT13-5 | low (not fixed: no duplicate, no crash) | A stop request in the middle of a Package 13 tick (SIGTERM / Ctrl+C sets `running` False) does not stop that tick. The remaining IOCs of the tick still go out (2 more in the probe), while the quote loop stops at once. They are immediate-or-cancel orders and `shutdown` cancels anything left, so nothing duplicates or rests. | `p13_tick` / `el_takes` / `hv_tick` loops | probe (b.running = False after the first p13 write) | none: a behaviour change outside the crash / duplicate scope. The battery's "stop mid-cycle" checks that nothing duplicates. | battery "a stop mid-cycle" |

Severity: high = a crash or a duplicate money order; medium = a duplicate the exchange would reject, or wasted writes;
low = other.

## The checks (tests/test_p13_redteam.py, 90 checks)

Every scenario runs full `Bot.cycle`s plus `write_status`, as `run()` does. A restart is a new `Bot` built from the
same files: status.json, notes, the overrides file, then the clean slate. Each cycle is audited for:
- no exception, and no "tick failed" / "unexpected error" in the log (the Package 13 ticks catch their own crashes);
- no two IOCs of one intent on one market and side (`p13_tick`, `el_tick`);
- no two Package 13 orders on one market in a cycle (a sell-down plus a momentum buy or exit);
- no harvest level sent twice at one price;
- no take plus a quote or harvest level on the same side of the same market in the same cycle;
- no two of our orders resting at one price on one market, after every cycle;
- no duplicate order ids;
- after each restart, the sleeve restored exactly (legs, cost, state), and the exit's total shares sold never more
  than the leg held when the exit started.

The battery ran:
- the staged file with everything on, 60 cycles, with and without a restart every 9;
- each of `buckets_enabled`, `aggressive_value`, `momentum_enabled`, `tilt_harvest_ladder`, `election_night` off
  (with restarts);
- `close_override_utc` "";
- empty, one-sided and crossed books;
- 0 to 14 Polymarket references missing;
- a position on an unlisted market, and a laddered market removed from the market list;
- ApiError on place, on cancel, and after landing;
- partial fills (`take_cap`) with failing cancels, so leftovers rest;
- cash at the reserve (gate refusals);
- the kill switch (nothing sent after it);
- the backstop tripwire (no momentum buy after it);
- a stop mid-cycle;
- the flips `momentum_exit` false->true and `momentum_enabled` true->false->true->false->true, with a restart every 7
  cycles (no buy reopens the sleeve before the latch reset);
- the sleeve's kill at -30% with restarts;
- election night: holdback from 3 Nov 12:00, calls from 23:00, a resolved race, an un-call, the stop window at 4 Nov
  16:55 and past the 17:00 close, also with restarts, with `close_override_utc` "" and with `momentum_exit` on;
- clock jumps across 12:00 / 23:00 / 16:55 / 17:00 and back to 2 Nov;
- a 3 h random walk (Polymarket drifting 0 to 1c a cycle, other traders filling our resting orders, a restart every
  25 cycles).

Then the live snapshot (/home/claude/snap04, 237 markets, the tests/test_p9_dryrun harness): 3 h with the staged file
and a restart every 45 minutes, then 1 h with each flag off. Every scenario passed after the fixes.

## Looked at and found clean

- `p13_send`: our orders on the market are cancelled first (refused if not confirmed), and the leftover is cancelled at
  once. On an ambiguous error `orders_stale` is set before the send and `pending_until` holds the market for 90 s,
  longer than the IOC's 10 s life. `p13_own_ioc_resting` blocks a second IOC of the same intent while a leftover is
  listed.
- `mom_exit_step`: paced from `mom_exit_start` and the restored `exit_started_wall`. A restart, or an exit sale whose
  response was lost, never re-sells: the leg follows the position. The exit is never followed by a buy (state
  machine), and the latch reset needs false-then-true.
- `bucket_sell_step`: `usd_left` is decremented per fill, the market is dropped when sold out, and `skip | traded`
  keeps the momentum buy off a market sold this tick. The plan is not persisted and the hourly clock is, so a restart
  does not re-run the sell-down early.
- `mom_candidates` / `mom_buy_step`: never the opposite side of a position, never a market holding a position outside
  the sleeve, never a set-ladder market, never twice in one tick (`traded`), `mom_max_markets` counted with this
  tick's new legs, and nothing in reduce-only.
- `el_takes`: one order per market per write (a race's winner and losers are distinct markets), a 5 s cooldown and
  `el_own_resting`, our crossing orders cancelled first, and leftovers cancelled.
- `el_update_calls` / `el_races` with missing references, resolved feeds, un-calls, and markets dropped from the list.
- `hv_plan` / `hv_tick`: distinct level prices, re-quotes cancel before placing (never new levels on unconfirmed
  cancels), refused markets wait `HV_REFUSED_WAIT`, and the ladder is pulled on reduce-only, when the flag is off, and
  on removed markets. Set-ladder and momentum markets are excluded. `alloc_ladder_plan` counts the harvest's covered
  NO sales through `cash_free`'s no_sell orders.
- `plan_exchange`: harvest orders are hidden from the quote, and the quote's bid is kept below our harvest asks
  (`hv_guard_quote`).
- Handover restarts: `shutdown` saves the order notes, so harvest levels keep their tags.
- Persistence: momentum legs, cost, state, latch and exit start; the buckets' clock, flows and buy round; the election
  calls and totals. All are restored, and the restart scenarios found no difference.

## Residual caveats (not exercised in the fake)

- Write budget: the fake reports unlimited writes, so the per-cycle shares (`bucket_writes_frac`, `harvest_writes_frac`)
  and `writes_ready` were never binding. Live it is 28 a minute.
- The realtime feed: realtime is off in the tests, so every cycle is a full check that re-reads orders and positions.
  Live, most cycles read only what the feed reports. RT13-1's fix forces the re-read (`orders_stale`), but the timing
  of real listings is not modelled.
- The exchange's handling of two sells of the same held shares (RT13-2's case) is unknown: whether a converted sell or
  a rejection follows. The fake converts a whole order to "buy NO" when the shares held are fewer than its size.
- Positions lag: RT13-3's grace (120 s) covers a lagging read or a 409 fallback. A lag longer than that is not
  covered, and neither is a restart inside the grace (`mom_last_trade` is not persisted).
- Election takes whose batch times out after filling: `el_totals` (the holdback spend) misses those fills, so the
  holdback can be overspent by that batch. This is not a duplicate, because the next plan re-reads positions and the
  book, but the holdback count is off.
- Other `cash_free` callers outside Package 13 (the P12 set ladder) also read `ex.inv`. They are bounded by
  `nono_set_part` on the fresh position, so they are not the same bug. Not changed (out of scope).
- Polymarket and our marks: the walk moves Polymarket by at most 1c a cycle. A jump guard trip or a feed outage longer
  than 30 s is covered only as "references missing".
