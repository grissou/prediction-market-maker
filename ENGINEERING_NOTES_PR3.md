# PR3 notes (ops and analytics)

See ENGINEERING_NOTES.md (on claude/pr1-safe-fixes, also here) for the PR1 work. This branch is based on PR1.

| # | Item | State |
|---|---|---|
| 1 | Live settings overrides (`settings_override.json`, re-read every 30 s) | done |
| 2 | `python mm_bot.py analyze` (+ optional daily phone summary) | todo |
| 3 | status.json during long cycles + slow-cycle alert | todo |
| 4 | Skip the startup clean-slate cancel when no orders rest | todo |
| 5 | Handover restart (exit without cancelling, adopt on start) | todo |

## 1. Live settings
Write e.g. `{"min_edge": 0.015, "burst_markets": 25}` to `/opt/mmbot/settings_override.json`. Within 30 s the
bot applies it and logs `SETTING min_edge: 0.01 -> 0.015`. Only names in `OVERRIDABLE` (with ranges) are
accepted; anything else (secrets, URLs, files, the kill switch, wrong types, out of range) is refused with
one alert. Removing a key restores the default. A broken file keeps the current settings. status.json
shows the active overrides. Rollback: delete the file.
