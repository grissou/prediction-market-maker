# Tests

Eight offline suites on a fake exchange (`fakes.py`): no network, no API key. Each file is a script that prints
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
| `test_stress.py` | long randomised sessions with faults on any request: never crashes, never crosses itself, keeps every cap. `python3 tests/test_stress.py [cycles_per_seed] [seeds]` (default 1000 x 4) |
