# Tests and simulators

Everything in this folder runs offline: no API key, no network. The bot's own functions are imported from
`mm_bot.py` and run against fakes, so a change to the bot is tested against the real code paths.

## The test suites

Three commands run everything a reader needs, from the repository root:

```bash
pip install -r requirements.txt                               # once; Python 3.10+ (the server runs 3.10)
for t in tests/test_*.py; do python "$t" | tail -1; done      # all 35 suites, about 3 minutes
STRESS_LADDER=1 python tests/test_stress.py                   # the stress test with the harvest ladder on
```

Each suite is a plain script, named for what it tests, that prints `PASS` / `FAIL` per check and a final
count, and exits non-zero on any failure. `fakes.py` is the fake exchange they share: it follows the API
specification, including its error cases. `.github/workflows/tests.yml` runs the same loop on every push.

| Suite | What it pins |
|---|---|
| `test_mm_bot.py` | The core suite (600 checks): quoting, risk, the exchange wrapper, status, the main loop |
| `test_identity.py` | The deploy gate: the current code makes the same decisions as the revision the server runs |
| `test_quoting.py` | The pure quoting functions and their Bot wiring, plus a short `strategy_sim.py` run |
| `test_quote_target_skew.py` | Quote targets and inventory skew; the close override and pre-close windows |
| `test_reduce_quote_without_cash.py` | A reducing quote is never dropped for lack of cash (the Dem House case) |
| `test_reduce_only_diagnostics.py` | The "why" fields of a quote clipped in reduce-only |
| `test_reduce_no.py` | Reducing a NO holding as a covered NO sale |
| `test_no_no_sets.py` | NO+NO sets: the exchange's collateral rule and the bids it caps |
| `test_pair_sizing.py`, `test_pair_followup.py` | Sizing, ordering and follow-up of a NO+NO pair unwind |
| `test_cash_gate.py` | The cash gate and the per-market adding side at 100% capital |
| `test_kelly_caps.py` | The Kelly edge cap, the per-market fraction and the headline position fraction |
| `test_value_mode.py` | Value mode: a contract is worth its race-scaled Polymarket probability, not its mark |
| `test_tilt.py` | The tilt-corrected reference (the favourite-longshot bias estimator) |
| `test_risk_unheld.py`, `test_take_reserve.py`, `test_mm_risk_reserve.py` | Risk room: unheld legs, takes, MM reserve |
| `test_allocator.py` | The hourly capital allocator: sell low-edge holdings, read the cash, buy high-edge levels |
| `test_allocator_rich_leg.py` | The allocator's resting ladder for a NO+NO set's rich leg |
| `test_allocator_quote_regressions.py` | Red-team findings on the allocator and the quoter: failing case, then fixed |
| `test_mm_funding.py` | Recycling stale market-making inventory and funding the reserve |
| `test_mm_refill_and_swaps.py` | The reserve refill and the allocator's swaps when both were stuck live |
| `test_swap_value_margin.py` | The swap-only value-floor margin |
| `test_harvest_ladder.py` | The harvest ladder (resting asks on longshots, bids on favourites) and the per-state cap |
| `test_liquidation_fields.py`, `test_expected_value_fields.py` | Ops fields: liquidation value, P&L, EV, carry |
| `test_recorder_refill.py` | The positions recorder and `analysis/mark_rule.py` |
| `test_startup_selftest.py` | The start-up self-test when the exchange refuses a test order for funds |
| `test_ref_prices.py` | `ref_prices.py`: Polymarket prices, the mapping file, caching and the jump moves |
| `test_live_sim_marks.py` | `live_sim.py`'s world knobs and liquidation-marked fields |
| `test_stress.py` | Long randomised sessions against a faulty exchange (see below) |
| `test_dryrun_*.py` (4) | Replays of a recorded live snapshot through the real `Bot` (see below) |

`test_identity.py` runs the current code beside the revision the server runs (`git show
live-d9220c1:mm_bot.py`) on the live settings and requires identical orders, quotes, notes, status, health
and summaries in six worlds; it is the gate for every deploy.

### The dry runs

`test_dryrun_value_mode.py`, `test_dryrun_mm_refill.py`, `test_dryrun_swap_margin.py` and
`test_dryrun_harvest_ladder.py` replay a recorded live snapshot through the real `Bot` class for a dozen
cycles, on the `dryrun_harness.py` harness (all 237 markets, other traders' books, Polymarket references,
positions, marks, lots; `dryrun_state_mm_refill.py` moves that state to the 5 Oct live numbers for the
last three). They need the snapshot: set `P9_SNAP` to a folder holding `md.sqlite` (the recorder database,
built with `gunzip -c market_data.sql.gz | sqlite3 md.sqlite` from an `ops-snapshot-*` branch) plus that
snapshot's `status.json`, `order_notes.json`, `position_lots.json` and `settings_override.json`. Without
it they print `SKIP` and pass with zero checks.

```bash
P9_SNAP=/path/to/snap04 python tests/test_dryrun_mm_refill.py
```

Each dry run rewrites its report under `analysis/` (`p10/DRYRUN.md`, `p14/DRYRUN_14_*.md`,
`p15/DRYRUN_P15.md`); set `P10_DRYRUN_REPORT=0`, `P14_DRYRUN_REPORT=0` or `P15_DRYRUN_REPORT=0`
to keep the committed report, or `git checkout -- analysis` afterwards.

### The stress test

`test_stress.py` runs long randomised sessions against a deliberately faulty exchange (errors on any
request, lost responses, a lagging order list, a dropping feed) and checks after every cycle that the bot
never crashes, never trades against itself, keeps positions within limits and recovers once the faults
stop. `STRESS_LADDER=1` runs it with the harvest ladder on.

## The simulators

There are three, at three levels of realism. They were used to rank strategy ideas before building them,
and to size settings; the numbers they produced are in `docs/notes/SIM_NOTES.md` on the branch
`archive/build-notes-2026-10`.

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
(`test_dryrun_*.py`) is the closer check, and the live recorder is the final one.
