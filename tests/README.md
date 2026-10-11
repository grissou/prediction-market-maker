# Tests

Ten offline suites on a fake exchange (`fakes.py`): no network, no API key. Each file is a script that prints
PASS/FAIL per check and `N/N passed` last; exit code 0 means all passed. Run them all from the repository root:

```sh
for t in tests/test_*.py; do python3 "$t" | tail -1; done
```

| File | Covers |
|---|---|
| `fakes.py` | the fakes: an exchange (`FakeClient`), the realtime feed and Polymarket, with the real `Client`'s interface |
| `test_pricing.py` | `mmbot/pricing.py`: the four edge formulas (README §4.2), `edge_held`, race scaling, the liquidity and tilt estimators |
| `test_risk.py` | `mmbot/risk.py`: the worst case, the correlated measure, reduce-only, the reserve pause, the kill switch, the cash gate |
| `test_value.py` | `mmbot/value.py`: the value floor, the tail quotes' hurdle prices, the allocator (swaps, buys at the touch, the refill) |
| `test_mm.py` | `mmbot/mm.py`: the band, a tick inside the best other trader, the minimum edge, never crossing, the reserve, skew |
| `test_ladder.py` | `mmbot/ladder.py`: level prices for longshots and favourites, the 8% edge filter, level sizes, the tranche rule |
| `test_exchange.py` | `mmbot/exchange.py`: request and write budgets, the 429 pause, the wire format both ways, order-change records |
| `test_bot.py` | the bot and the run loop end to end: one cycle, a dry run sends nothing, handover, status.json, the kill file |
| `test_shadow.py` | the 10 Oct shadow's conditions (foreign resting orders, slow books at 20 requests/min, the tilt): it must plan orders |
| `test_stress.py` | long randomised sessions with faults on any request: never crashes, never crosses itself, keeps every cap. `python3 tests/test_stress.py [cycles_per_seed] [seeds]` (default 1000 x 4) |
| `test_replay.py` | the old bot and the new one plan orders on the same recorded live state; same markets and sides, sizes within a factor of two for most markets |

## The replay's snapshot

`test_replay.py` needs two things from git and prints `SKIP` (exit 0) without either:

1. The 4 October 15:56 ops snapshot, branch `ops-snapshot-2026-10-04`: the test builds it in a temporary folder
   (`market_data.sql.gz` → `md.sqlite` with sqlite3, plus the branch's `status.json`, `order_notes.json`,
   `position_lots.json`, `settings_override.json`). Point `MM2_SNAP` at a folder already built to skip that step.
2. The old bot (retired 11 October 2026), tag `legacy-2026-10-11` or the ref in `MM2_OLD_REF`: it is extracted with
   `git archive` into a temporary folder and run there in a subprocess through its own `tests/dryrun_harness.py`
   with its live settings of 10 October.

The report (market, side, old shares, new shares, tags, verdict) is written to `REPLAY.md` in the snapshot folder.
