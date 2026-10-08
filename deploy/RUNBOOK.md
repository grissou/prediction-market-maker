# Runbook: operating the live bot

Server: /opt/mmbot (Python 3.10 venv), systemd unit `mmbot`, live settings in /opt/mmbot/settings_override.json
(re-read every 30 s, whitelisted keys only: `OVERRIDABLE` in mm_bot.py), phone alerts via ntfy.

## A. Parameter-only package (no restart)
1. Open /opt/mmbot/settings_override.json (create it if missing; one JSON object).
2. Merge the package's snippet into it (the staged file under deploy/package<N>/). Keep existing keys you still want.
3. Within 30 s the journal shows `SETTING name: old -> new` for each key; a refused key is reported once
   (`journalctl -u mmbot -n 50 | grep -i 'SETTING\|override'`). status.json lists the active overrides.
4. Rollback: delete the key (the default returns) or the whole file. Takes effect within 30 s.

## B. Code package (handover restart: resting orders are kept)
1. Stage: `mkdir -p /opt/mmbot/staging-<date>` and copy the package's files there (usually just mm_bot.py).
2. Test on your own machine first (`for t in tests/test_*.py; do python "$t" | tail -1; done`, plus `python tests/test_identity.py`
   against the commit the server runs); on the server only compile: `/opt/mmbot/.venv/bin/python -m py_compile mm_bot.py`
   (the suites take over 10 minutes on the server's single CPU and compete with the bot).
3. Back up: `mkdir -p /opt/mmbot/backup-<date> && cp /opt/mmbot/mm_bot.py /opt/mmbot/ref_prices.py /opt/mmbot/ref_map.json /opt/mmbot/backup-<date>/`.
4. Copy the staged files into /opt/mmbot (not .env, not state files: fills.csv, order_notes.json, market_data.sqlite, settings_override.json).
5. Restart without cancelling: `sh /opt/mmbot/deploy/handover-restart.sh` (SIGUSR1, waits up to 240 s (the bot exits within 150 s), always starts the new version, which
   adopts the resting orders). Then `journalctl -u mmbot -f | grep -i 'handover\|ALERT\|BURST\|SETTING'`.
   - If the new version does not start within ~2 min, the resting orders are unmanaged until they expire (30 min): roll back at once.
   - A plain `systemctl restart mmbot` still cancels everything on stop and start; use it when the package section says so.
6. First 10 minutes: see the package section ("What to watch"). Generic checks: the summary line
   `account X (locked in orders L, already included) | worst-case loss Y (risk Z) | ... | priced a/237 | resting n`
   has X near the pre-deploy value, Z below 30% of X, a rising, no repeated ERROR, no 429, few 409s; fills.csv shows no fill through fair value.

## C. Rollback (code)
`cp /opt/mmbot/backup-<date>/* /opt/mmbot/ && sh /opt/mmbot/deploy/handover-restart.sh` (or `systemctl restart mmbot` to also cancel).
Settings overrides stay in force across a rollback: check settings_override.json for keys the old code does not know (they are refused with one alert, harmless).

## D. Emergency
- Stop and cancel everything: `sudo systemctl stop mmbot`.
- Kill switch file: /opt/mmbot/kill_switch.tripped (the bot exits 4 and systemd does not restart it).
