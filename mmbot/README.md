# mmbot: the package

Twelve files, about 4,400 lines. Each opens with the same header: what it **owns**, what it must **never** do,
where it came from (**origin**), and what is still **open**. Read them in this order and the bot reads as the
paper's method section.

| File | Lines | Owns |
|---|---|---|
| `state.py` | 185 | The plain records every module shares: `Market`, `Book`, `Order`, `Fill`, `Account`, `View` (one cycle's reading of the world), the price grid, and the bot's own state in six small dataclasses |
| `config.py` | 245 | `Settings` (58 fields, one line of reason each), the list of settings that may be changed while the bot runs, the settings-file reader |
| `exchange.py` | 740 | The REST client with its request and write budgets and 429 handling, the realtime feed with dead-socket detection, the wire format both ways |
| `pricing.py` | 229 | Fair value `p` from Polymarket (liquid prices only, a race's legs scaled to sum to 1), the tilt estimate, and the four edge-per-unit-of-cash formulas |
| `risk.py` | 344 | The worst case at settlement (correlated and simple), reduce-only, the market-making reserve pause, the kill switch, the per-market and per-state caps, the cash gate every order passes |
| `value.py` | 543 | The value floor, the tail quotes behind the hurdle, and the allocator: swaps, buys at the touch, the demand-sized refill |
| `mm.py` | 241 | Market making: small two-sided quotes in the 15–85% band and the recycler for old inventory |
| `ladder.py` | 189 | The harvest ladder: asks above longshots and bids under favourites at 0/2/4/6c, 8%+ edge, within its budget |
| `bot.py` | 718 | The `Bot`: its state and the twenty-line cycle (read, price, risk, decide, floor, gate, reconcile, send, report), order management, `status.json` |
| `record.py` | 58 | The recorder: one row per priced market and one for the account every minute, in `run/market_data.sqlite` |
| `notify.py` | 83 | The phone: alerts with a 30-minute silence per cause, and the hourly five-figure summary |
| `ops.py` | 394 | The command line (`run`, `run --live`, `status`, `summary`, `cancel`), the run loop, handover on SIGUSR1, the watchdog, the start-up self-test, the exit codes |

Rules the code keeps: one idea per function, none over forty lines; a strategy that is switched off is not
called; no attribute is added to the bot outside `__init__`; numbers that are decisions are settings with a
reason, numbers that are facts about the exchange are named constants beside their use; comments say why,
never what. Leaf modules (`state`, `config`, `exchange`, `pricing`) import nothing from the strategies; the
strategies (`risk`, `value`, `mm`, `ladder`) import only the leaves; `bot` imports the strategies; `ops`
imports `bot`.

Everything the bot writes goes under `run/` next to `mm_bot.py`: `settings_override.json` (read every 30 s),
`status.json`, `state.json`, `mm_bot.log`, `market_data.sqlite`, and `kill_switch.tripped` if the kill switch fired.
