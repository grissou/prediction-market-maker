# Rewrite status

```
STATUS (2026-10-10 15:00 UTC, branch rewrite) - READY
done:     'Done means' 1-4; tests2/ all pass (exchange 70, pricing 40, risk 55, value 57, mm 58, ladder 28,
          bot+ops 38, stress 60, replay 5); owner decisions 1-3 built (risk-neutral swaps while paused,
          MM inventory credited to its own room, covered "sell NO" start-up probe with fallback)
left:     the owner's shadow run (`python3 mm_bot2.py run` against the real feed: not possible from here);
          decision 4 kept as is (no exit orders resting in the tails)
lines:    mmbot2/ + mm_bot2.py = 3,486 (target < 4,000; brief 3,000-3,500); tests2/ 10 files; 59 settings
```

## Line counts

| File | Lines |
|---|---|
| mmbot2/bot.py | 544 |
| mmbot2/config.py | 243 |
| mmbot2/exchange.py | 729 |
| mmbot2/ladder.py | 189 |
| mmbot2/mm.py | 233 |
| mmbot2/ops.py | 315 |
| mmbot2/pricing.py | 229 |
| mmbot2/risk.py | 330 |
| mmbot2/state.py | 117 |
| mmbot2/value.py | 543 |
| mm_bot2.py | 14 |
| total | 3486 |

exchange.py (728) is the largest: the realtime feed with its dead-socket and revision-gap handling (~190) and the
50-state table for the per-state cap. value.py (519) holds the floor, the tail quotes and the allocator.

Tests (not counted in the target):

| File | Lines |
|---|---|
| tests2/fakes.py | 282 |
| tests2/test_bot.py | 154 |
| tests2/test_exchange.py | 330 |
| tests2/test_ladder.py | 157 |
| tests2/test_mm.py | 199 |
| tests2/test_pricing.py | 144 |
| tests2/test_replay.py | 179 |
| tests2/test_risk.py | 205 |
| tests2/test_stress.py | 434 |
| tests2/test_value.py | 239 |
| total | 2323 |

## Replay differences (old bot vs new on the 4 Oct snapshot)

`tests2/test_replay.py` runs the old bot through `tests/dryrun_harness.py` (as `tests/test_p15_dryrun.py` does) with the
10 Oct live settings, and the new bot on `tests2/fakes.FakeClient` seeded with the same state, 6 cycles each; the table
is in `analysis/rewrite/REPLAY.md`. Of 186 (market, side) pairs either bot traded: 118 the same within a factor of two,
24 the same market and side at a different size, 15 the old bot only, 29 the new bot only. No side is ever reversed.

- **Sizes, 20 rows (old 2-50, new 199):** the old bot halved quotes while capital in positions was above 70% (the capital
  ceiling, `capital_ceiling_adding_size_factor` 0.5 on a 100-share quiet-market plan); the rewrite has no capital
  ceiling (the risk caps and the cash gate bound it) and quotes `mm_quote_frac` x account = 200.
- **Sizes, 2 rows (U.S. Senate, old 5,000-7,500, new 199):** the old bot gave the headline control markets a flat
  10,000-share limit (`headline_position_frac`); the rewrite quotes them like any market and the allocator never sells them.
- **Old only, 16 rows (Alaska, Nebraska, California, Alabama, Oklahoma: ~50-100 shares):** markets without a liquid
  race-scaled Polymarket price (three-candidate races with an unpriced independent leg); the old bot quoted them
  around the book's own price, the rewrite leaves unpriced markets alone (README §4.1 calls them unpriced).
- **New only, 23 market-making rows (~199 shares):** middle-band markets the old bot left unquoted that cycle, most
  likely through rules not carried over (the turnover-dead and churn controls, the fl-bias side, the capital ceiling).
- **New only, 5 ladder rows (Rhode Island, Connecticut, Montana, WA-03):** the old ladder paces its first placements
  over several cycles (harvest_writes_frac per cycle after the quotes); the rewrite places the best-edge levels at
  once within the 10k budget. Rhode Island stays inside the 15k state cap (6.7k held + ~6k of levels, at p).

## Open questions for the owner (from the module headers)

- config: skew_max 0 live while skew_target_inventory is on: was a price lean ever intended? (the rewrite leans by size)
- pricing: the tilt compares raw Polymarket with a race-normalised book mid, as live did: intended?
- risk: the stale-cash rule refuses cash orders rather than stopping the gate; unpriced markets are valued at the
  race remainder / book mid / 0.5. (mm_room_guard: built, owner decision 2.)
- value: a swap in flight is not saved across a restart; a write-budget-deferred allocator IOC is re-planned next
  interval rather than retried. (Risk-neutral swaps continue while adds are paused: built, owner decision 1.)
- mm: quote size and the 2-quote inventory cap; the dropped buy-back deferral while cash is low.
- ladder: the headline control markets are excluded (as the old bot); new ladder adds while value adds are paused
  are left to the gate.
- ops: the covered "sell NO" probe is back (owner decision 3); with the fallback on, the gate still counts a
  buy-back as cash-free, so the exchange may refuse a few for funds (harmless, logged).
- bot: no reducing-side value quotes in the tails (the allocator and the ladder exit those positions).
- exchange: is a NO-side fill's price always the NO price? a "sell NO" beyond the NO held is trimmed.

## Bugs the stress test found (fixed)

1. Fills were subtracted twice from partly filled orders on a full read (fills are now noted before the order list).
2. A kept order the gate trimmed stayed at full size; 3. an order the gate always trims re-quoted every cycle (the gate
now runs before reconcile, which compares with the admitted size); 4. after a failed cancel a new order could cross the
order still resting (every placement is checked against what really rests). Also: a buy-back while short is trimmed to
the NO held, as the exchange does, so it does not re-quote every cycle; value quotes need a fresh book.

## Not in the rewrite (as the brief says)

Arbitrage, takes, NO+NO pair unwinds (the 4 Oct snapshot holds 1 set worth 1.0: `status.json` nono_sets), the momentum
sleeve, the capital ceiling, mark fragility, activity size plans, fl-bias, churn control, burst mode, the recorder.

## Ideas not built

- Report write-budget-deferred allocator IOCs back to the allocator so it retries within the interval (value.py report).
