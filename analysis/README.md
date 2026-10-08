# Analysis

The research behind each release. Nothing here runs in the live bot; `tests/test_recorder_refill.py`
imports `mark_rule.py`, everything else is read or run by hand.

| Path | What it is |
|---|---|
| `p9/` … `p16/` | One folder per release: the spec, the red-team review, the dry run on a recorded snapshot, the diagnosis that led to it |
| `poly_bias/` | The study of where the tournament's prices sit relative to Polymarket's |
| `explorer_r2/` | The second exploration round's notes |
| `sim_results/` | Raw output of `tests/live_sim.py` and `tests/strategy_sim.py` runs, by round; `docs/notes/SIM_NOTES.md` summarises them |
| `mark_rule.py` | How the exchange values a position (a trade-based mark, not the book); used by the recorder tests |
| `mark_fragility.py` | How much a position's mark can move on a single trade |
| `rival_floor.py` | Per-market minimum edge from the recorder's books (writes `market_edge.json` for the bot) |
| `valuation.py`, `valuation_level.py`, `valuation_tape.py` | Account valuation at marks, at the book and at Polymarket, from the recorder |
| `markout2.py`, `rates.py`, `turnover.py`, `hourly.py` | Fill-by-fill edge and markouts, fill rates, capital turnover, hourly activity |
| `competition2.py`, `coverage2.py`, `data2.py` | Rival-bot behaviour, market coverage, the data report |
| `followups.py`, `night.py`, `outage.py`, `outsider_races.py`, `simparams2.py`, `deploy.py` | One-off investigations named for what they looked at |

Most scripts read the bot's local files (`status.json`, `fills.csv`, `market_data.sqlite`) or a snapshot
of them from an `ops-snapshot-*` branch; each has a docstring saying which.
