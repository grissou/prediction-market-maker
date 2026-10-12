# Analysis

The research the bot's design rests on, and the four tools that read its recorder. Everything else from the
build (dry runs, idea rounds, the old log-format tools, eight further reports including a literature review) is at the tag `legacy-2026-10-11`
and on the branch `archive/build-notes-2026-10`.

| Report | What it answers |
|---|---|
| `reports/POLY_BIAS_RESULTS.md` | Where the tournament's prices sit relative to Polymarket's: the bias the strategy trades |
| `reports/TILT_ESTIMATOR.md` | How the tilt is measured every cycle, and how the estimators were checked |
| `reports/TILT_PATHS.md` | The tilt's likely paths to 4 November and what each pays: the case for the ladder |
| `reports/VALUE_PLAN.md` | Positions valued at settlement, the value floor, the allocator: the design of the value book |
| `reports/RETURNS_ATTRIBUTION.md` | Which layer earned, from the bot's own trade lines, 4–10 October |

| Tool | What it does |
|---|---|
| `report.py` | The results table of README §6 and `docs/equity.png`: return, lowest reading, largest fall, daily closes, edge per fill |
| `valuation.py`, `valuation_level.py`, `valuation_tape.py` | The account valued at the marks, at the book and at Polymarket, from `run/market_data.sqlite` |
