# Project documents

Working documents from the build, kept for the record. The README at the repository root is the
write-up; these are the raw material behind it. Dates are UTC, October 2026.

| Folder | What is in it |
|---|---|
| `handoffs/` | `START_HERE.md`: the running hand-off between the build sessions (status blocks, package READY sections, findings), updated on every push until 6 October. `START_HERE_BUILDER.md`: the first builder session's hand-off (2 October) |
| `plans/` | The briefs each build phase worked from: `PLAN.md` (the original design), `PLAN_P6.md`, `PLAN_P9.md`, `PLAN_P10.md` (per release), `PLAN_FINISHER.md` (the finishing session), `PLAN_POLY_BIAS.md` (the Polymarket-bias study) |
| `notes/` | `ENGINEERING_NOTES.md` and `ENGINEERING_NOTES_PR3.md` (what was learned about the exchange and the API), `SIM_NOTES.md` (every simulator experiment with its numbers), `DATA_REPORT_2.md` (the first data report), `RESULTS_POLY_BIAS.md` |
| `ideas/` | The three rounds of strategy ideas, scored and ranked before anything was built |

Per-release research (specs, red-team reviews, dry-run reports) is under `analysis/p<N>/`; the
simulator's raw result files are under `analysis/sim_results/`; each release's staged settings and
deployment notes are under `deploy/package<N>/`.
