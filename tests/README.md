# Tests and simulators

Everything in this folder runs offline: no API key, no network. The bot's own functions are imported from
`mm_bot.py` and run against fakes, so a change to the bot is tested against the real code paths.

## The test suites

```bash
for t in tests/test_*.py; do python "$t" | tail -1; done      # all 56, about 3 minutes in parallel
python tests/test_mm_bot.py                                   # the core suite alone (600 checks)
```

Each suite is a plain script that prints `PASS` / `FAIL` per check and a final count, and exits non-zero
on any failure. `fakes.py` is the fake exchange they share: it follows the API specification, including
its error cases. Suites named `test_p<N>*.py` belong to release *N* (see the root README, §5) and pin that
release's behaviour with its switches off to the release before it.

The `test_p*_dryrun.py` suites replay a recorded live snapshot through the real `Bot` class for a dozen
cycles and check what it would have done. They need the snapshot: set `P9_SNAP` to a folder holding
`md.sqlite` (the recorder database, built with `gunzip -c market_data.sql.gz | sqlite3 md.sqlite` from an
`ops-snapshot-*` branch) plus that snapshot's `status.json`, `order_notes.json`, `position_lots.json`
and `settings_override.json`. Without it they print `SKIP` and pass with zero checks.

`test_stress.py` runs long randomised sessions against a deliberately faulty exchange (errors on any
request, lost responses, a lagging order list, a dropping feed) and checks after every cycle that the bot
never crashes, never trades against itself, keeps positions within limits and recovers once the faults
stop. `STRESS_LADDER=1` runs it with the harvest ladder on.

## The simulators

There are three, at three levels of realism. They were used to rank strategy ideas before building them,
and to size settings; the numbers they produced are in `docs/notes/SIM_NOTES.md` on the archive branch.

### `strategy_sim.py`: the quoting decisions in a crowded book

A fast, one-market-at-a-time simulation of the bot's *quoting* functions (`compute_quote`, `fair_value`,
`side_needs_change`, the real ones) in a world calibrated on day one's data:

- **Polymarket**: the true probability, with small diffusion and occasional jumps;
- **Consensus**: where tournament traders think the price is: Polymarket plus a per-market bias (the
  favourite–longshot pattern) plus slow noise;
- **Rival bots**: 2–4 market makers that see Polymarket with a lag, undercut the best quote by a tick down
  to their own floor, and pick off any quote left 1.5c or more through their fair value;
- **Humans**: resting limit orders around consensus, replaced every 5–30 minutes;
- **Noise and informed flow**: Poisson market orders leaning toward consensus, with occasional sweeps,
  and an informed order shortly after each Polymarket jump.

Our quotes go out on the bot's real write budget with realistic latencies. The run reports fills, edge
per share at the quote, markout 15 minutes later, P&L at Polymarket and at the tournament mid, drawdown,
worst-case loss, share of time quoted and writes per market-hour.

```bash
python tests/strategy_sim.py 8 6 quiet                      # 8 seeds, 6 hours, the quiet regime
python tests/strategy_sim.py 8 6 news min_edge=0.02         # with a setting overridden
python tests/strategy_sim.py sweep 8 6 quiet '{}' '{"min_edge": 0.02}' '{"min_edge": 0.03}'
```

`sweep` runs the base and each variant on the same seeds and reports the paired difference with its
standard error, which is the number to trust: the world is noisy and unpaired comparisons mislead.
Regimes: `quiet` (an ordinary day), `news` (a debate or poll day, many jumps at once), `slow` (day-one
write latencies).

### `live_sim.py`: the same world, started from the real book

Starts from a recorded state of the real account (`live_start.json`, built by `live_start_extract.py`
from `status.json`, the recorder's snapshots and `fills.csv`) and runs the real `Bot.decide` and the
allocation, arbitrage and lot-tracking code paths, not just the quoting functions. The markets are the
races of the N largest positions plus three synthetic outsider races; the rest of the account is a fixed
block so that capital in positions starts at a chosen fraction. It adds world knobs for the tilt
(`_world_tilt`, `_world_tilt_growth`), for what the rivals anchor on (`_rival_anchor`), and for a
background worst-case that grows while the bot is not reducing (`_bg_wc_growth`), each explained in the
file's docstring. It reports P&L at the mid and at liquidation, 15-minute markouts, the exit ratio and
the median holding time.

```bash
python tests/live_sim.py 8 3 quiet '{}' '{"alloc_mm_reserve": 10000}'      # paired seeds, 3 hours
LIVE_SIM_CACHE=sim_cache.jsonl python tests/live_sim.py 16 6 quiet '{}'    # cache results per seed
```

What `live_sim.py` does *not* model: the harvest ladder (measure that with `strategy_sim.py`'s `_ladder`
override) and the momentum sleeve.

### `scenario.py`: the whole bot against a fake HTTP exchange

Runs the complete bot (real `Api` with its throttle, retries and idempotency keys, real threads) against
a fake HTTP exchange on a scaled clock, to test behaviour under exchange faults rather than strategy:

- `slow`: the day-one open, where order writes took 14–30 s and retries got "request in flight";
- `crowded`: fast writes plus three rival bots that undercut and pick off stale quotes;
- `ceiling`: the 2 October incident, where the capital ceiling switched on under a 30-writes-a-minute
  rate limit.

```bash
python tests/scenario.py crowded 3 20            # 3 seeds, 20 simulated minutes
SCENARIO_SET="writes_per_minute=28" python tests/scenario.py slow 3 20
```

It reports how fast quotes get out, the longest cycle, how long a headline quote stayed stale after a
Polymarket move, pick-offs, unattributed fills, duplicate quotes, and whether the self-test killed the bot.

## Reading a result

The simulators are for *ranking* ideas, not predicting profit. Several ideas that sounded right ranked
negative with paired seeds and were dropped; a few that ranked positive did worse live than simulated,
because real rivals and real flow are harsher than the model in some markets. The live snapshot replay
(`test_p*_dryrun.py`) is the closer check, and the live recorder is the final one.
