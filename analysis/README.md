# Analysis

The research behind the bot. Nothing here runs in the live bot; the tests import `mark_rule.py` and `turnover.py`.

## Reports (`reports/`)

| Report | What it answers |
|---|---|
| `RETURNS_ATTRIBUTION.md` | Which layer of the bot earns, from the bot's own trade lines (`returns_attribution.py`) |
| `POLY_BIAS_RESULTS.md`, `TILT_ESTIMATOR.md` | Where the tournament's prices sit relative to Polymarket's, and how the tilt is measured |
| `TILT_PATHS.md` | The tilt's likely paths to 4 November and what each pays |
| `VALUE_PLAN.md` | The design of value mode: positions valued at settlement, the value floor, the allocator |
| `MM_CARRY.md` | What market making earns per day in the middle band |
| `LIVE_ANALYSIS_0404.md`, `FIELD_ACTIVATION.md`, `TEXAS_AND_1PCT.md` | Live analyses: the 4 October state, the field and leaderboard, a rank bet |
| `SYNTHESIS_150K.md` | Could the account reach 150k? A Monte Carlo and the ideas it ranked |
| `DIAG_REFILL_DEADLOCK.md` | An engineering case study: why the market-making refill was stuck |
| `LIT_REVIEW.md`, `LIT_MARKET_MAKING.md` | A literature review (about 150 sources) and its market-making chapter |

## Dry runs (`p9/` … `p16/`)

Reports written by the `tests/test_p*_dryrun.py` suites when they replay a recorded snapshot through a release.

## Tools

| Script | What it does |
|---|---|
| `returns_attribution.py` | The attribution table above, from `mm_bot.log*` |
| `mark_rule.py`, `mark_fragility.py` | How the exchange values a position, and how far one trade can move that mark |
| `rival_floor.py` | Per-market minimum edge from the recorder's books (writes `market_edge.json`) |
| `valuation.py`, `valuation_level.py`, `valuation_tape.py` | Account valuation at the marks, at the book and at Polymarket |
| `markout2.py`, `rates.py`, `turnover.py`, `hourly.py` | Fill-by-fill edge and markouts, fill rates, capital turnover, hourly activity |

Most scripts read the bot's files (`status.json`, `fills.csv`, `market_data.sqlite`) or a snapshot of them from
an `ops-snapshot-*` branch; each has a docstring saying which. The rest of the build's working material is on
the branch `archive/build-notes-2026-10`.
