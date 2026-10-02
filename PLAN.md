# PLAN (Team run, 2026-10-02 08:50 UTC -> 2026-10-03 08:00 UTC)

Base: `claude/live-2026-10-02b` (5c0463a, live since 08:34). Branch: `claude/run-c-tournament-improvements-pycdet`.
Lead: Fable 5.1. Team: Reviewer (fable), Analyst (opus), Explorer (fable), Strategist (opus), Engineers (opus, worktrees).
Packages every 2-3 h, cumulative, commit "READY: Package N". Everything behaviour-changing behind a setting.

## Workstreams and first tasks

| # | Workstream | Owner | First tasks |
|---|---|---|---|
| A | Opening audit of the live diff (main...live) | Reviewer | bugs, races, trading risk; serious findings fixed first -> Package 1 |
| B | Post-deploy ops fixes | Engineer 1 | (1) write budget: what the journal shows the exchange tolerates (24 x 429 in 40 h, 439 x 409) and whether 30/min is right; deferrals every cycle at 08:09-08:12; (2) connection pool: size `HTTPAdapter(pool_maxsize=parallel_requests+2)` for parallel_writes 4 + reads + self-test; (3) burst mode must not count the slow first cycle after (re)start; (4) anything else in the 08:08-08:14 journal |
| C | Day-two analysis | Analyst | DATA_REPORT_2.md on ops-snapshot-2026-10-02: P&L/rank with corrected equity; cost of the reduce-only night; outage; coverage (priced 87/237 after R5: why not 150+?); edge/markout per market and hour; rival undercut speed and overnight behaviour; the +1,658 vs +475 gap; refreshed simulator parameters |
| D | Simulator and parameters | Strategist | recalibrate `tests/strategy_sim.py` to the Analyst's numbers; sweeps: R4 defaults (improve_ticks/undercut_step_back), R2 strength vs worst-case, min_edge, max_half_spread, headline size, kelly fraction, ref_weight; report with error bars (>= 24 seeds) |
| E | Strategy builds | Engineers 2-3 | R3 resting depth ladder (sweep catcher) behind a setting; then the best of the Strategist's and Explorer's tested ideas (join/step-back, rival-aware quoting, markout auto-widen, time-of-day sizing) |
| F | Idea rounds | Explorer | unanchored rounds focused on a market-maker-crowded book; each idea with mechanism and test; feeds D |
| G | Ops and runbooks | Lead / Engineer | extend the override whitelist where safe (ref_only_*, skew_*, size tiers, improve_ticks, undercut_step_back); settings_override.json snippets for parameter packages; code-deploy runbook around deploy/handover-restart.sh |
| H | Open questions | Analyst / Engineer | fee or valuation gap; Polymarket refresh below 5 s; WebSocket prices only if markouts show pick-offs within 5 s of a Polymarket move |

## Package plan (target)
1. **Package 1 (by ~11:00):** audit fixes + ops fixes (B) + whitelist extension + runbooks. Small, safe, high value.
2. **Package 2 (by ~14:00):** parameter retune from the calibrated simulator (settings_override snippet + defaults), first strategy build (R3 ladder, off by default unless the simulator is strong).
3. **Package 3+ (every 2-3 h):** rival-aware quoting, further ideas as they pass the simulator and the Reviewer; retune every package.
4. Final: START_HERE.md, draft PR into main, "Team complete".

## Rules carried to every sub-agent
Never touch the live bot or its server; never call the exchange API; never push (Engineers) or edit the lead's branch; Python 3.10-compatible code; tests for every change; no commands needing approval (no sudo, nothing outside the repo/worktree/scratchpad); short numbers-first reports.
