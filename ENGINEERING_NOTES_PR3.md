# PR3 notes (ops and analytics)

See ENGINEERING_NOTES.md (on claude/pr1-safe-fixes, also here) for the PR1 work. This branch is based on PR1.

| # | Item | State |
|---|---|---|
| 1 | Live settings overrides (`settings_override.json`, re-read every 30 s) | done |
| 2 | `python mm_bot.py analyze` (+ optional daily phone summary) | done |
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

## 2. analyze
`python mm_bot.py analyze [hours]` reads only fills.csv and market_data.sqlite. It reports, per market: fills,
shares, edge at the quote, markout at 1/5/30 min (fair value from the snapshots), P&L at the latest fair value,
share of quoted snapshots where our bid/ask was the best price, and undercuts per quoted hour (we were at the
top, then not, by the next snapshot). Worst and best markets by P&L are listed. On day one's data (447
attributable fills): edge +0.62c, markout +0.57 / +0.48 / +0.52c, P&L at latest fair value +1,128; Dem U.S.
House +447, Rep Maine Senate -28; at the top 7-52% of the time. Snapshots are 60 s apart, so undercut speed is
only resolved to a minute. `analyze_daily_hour` (default -1 = off) pushes the headline lines daily.
