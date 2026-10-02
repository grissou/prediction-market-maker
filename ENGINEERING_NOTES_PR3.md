# PR3 notes (ops and analytics)

See ENGINEERING_NOTES.md (on claude/pr1-safe-fixes, also here) for the PR1 work. This branch is based on PR1.

| # | Item | State |
|---|---|---|
| 1 | Live settings overrides (`settings_override.json`, re-read every 30 s) | done |
| 2 | `python mm_bot.py analyze` (+ optional daily phone summary) | todo |
| 3 | status.json during long cycles + slow-cycle alert | done |
| 4 | Skip the startup clean-slate cancel when no orders rest | done |
| 5 | Handover restart (exit without cancelling, adopt on start) | todo |

## 1. Live settings
Write e.g. `{"min_edge": 0.015, "burst_markets": 25}` to `/opt/mmbot/settings_override.json`. Within 30 s the
bot applies it and logs `SETTING min_edge: 0.01 -> 0.015`. Only names in `OVERRIDABLE` (with ranges) are
accepted; anything else (secrets, URLs, files, the kill switch, wrong types, out of range) is refused with
one alert. Removing a key restores the default. A broken file keeps the current settings. status.json
shows the active overrides. Rollback: delete the file.

## 3-4. Long cycles, clean slate
status.json is rewritten every 5 s during a cycle that has run 10+ s (`cycle_running_seconds`, `cycle_phase`),
and one alert goes out when a cycle passes `slow_cycle_alert_seconds` (120 s; at most one per 15 min). At
start-up the bot reads its open orders (fast) and skips the clean-slate cancel-all (a slow write on day one)
when none rest; if the read fails it cancels as before.
