# Runbook: operating the live bot

Server: /opt/mmbot (Python 3.10 venv), systemd unit `mmbot` (`deploy/mmbot.service`: `mm_bot.py run --live`,
WorkingDirectory /opt/mmbot), phone alerts via ntfy (ALERT_URL in /opt/mmbot/.env). Everything the bot writes is
under /opt/mmbot/run/: `settings_override.json`, `status.json` (health: account_value, resting, ev_outcome, alloc...),
`state.json`, `handover.json`, `kill_switch.tripped`, `mm_bot.log` (rotated), the recorder's `market_data.sqlite`.

## 0. Running it (locally or on a server)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+
printf 'SUPERMARKET_API_KEY=...\nTOURNAMENT_SLUG=midterm-elections\n' > .env
source .venv/bin/activate
python mm_bot.py status        # account, positions, open orders
python mm_bot.py run           # dry run: live data, logs every order it would send, sends nothing
python mm_bot.py run --live    # trade
python mm_bot.py cancel        # cancel our open orders (--all: every order on the account)
python ref_prices.py check     # tournament price vs Polymarket for every contract

for t in tests/test_*.py; do python "$t" | tail -1; done      # the suites
python tests/test_stress.py 5000 8                             # a longer stress run
```

On a server: `deploy/setup.sh` installs and hardens an Ubuntu host; `systemctl enable --now mmbot` runs
the bot as a service that restarts on crashes and reboots; `journalctl -u mmbot -f` follows the log. An
optional `ALERT_URL` (ntfy) in `.env` sends phone alerts and an hourly summary. Everything the bot writes
while running goes under `run/` next to it (git-ignored). The live settings file exists only on the server;
`deploy/settings_override.json.example` is its copy at the values of 11 October.

## A. Settings (no restart)
1. Edit /opt/mmbot/run/settings_override.json (one JSON object; start from `deploy/settings_override.json.example`).
   Only the names in `LIVE` in mmbot/config.py are accepted, each within its range; anything else is refused with
   one alert and ignored.
2. The file is re-read every 30 s; the journal shows `SETTING name: old -> new` per key
   (`journalctl -u mmbot -n 50 | grep SETTING`). status.json lists the active settings.
3. Rollback: delete the key (the default in mmbot/config.py returns) or the whole file; takes effect within 30 s.

## B. Code deploy
1. Test on your own machine: `for t in tests/test_*.py; do python3 "$t" | tail -1; done` (all `N/N passed`).
2. Back up: `mkdir -p /opt/mmbot/backup-<date> && cp -r /opt/mmbot/mm_bot.py /opt/mmbot/mmbot /opt/mmbot/backup-<date>/`.
3. Copy `mm_bot.py` and the whole `mmbot/` directory into /opt/mmbot (`scp -r mm_bot.py mmbot mmbot@SERVER:/opt/mmbot/`,
   or re-run `deploy/setup.sh`, which also keeps .env and run/). Remove `mmbot/__pycache__`. Never copy .env or run/.
4. Compile on the server (`/opt/mmbot/.venv/bin/python -m py_compile /opt/mmbot/mm_bot.py /opt/mmbot/mmbot/*.py`),
   then restart, one of:
   - handover, resting orders kept: `systemctl kill -s USR1 mmbot && sleep 5 && systemctl start mmbot` (the old process writes
     run/handover.json and exits within 150 s, the new one adopts the orders);
   - plain: `sudo systemctl restart mmbot` (SIGINT: cancels every order on stop, starts clean).
5. First 10 minutes: `journalctl -u mmbot -f | grep -i 'handover\|ALERT\|SETTING\|429'`, and run/status.json:
   account_value near the pre-deploy value, worst case below 40% of it, `priced` near 237, orders resting, no 429s.

## C. Rollback
`cp -r /opt/mmbot/backup-<date>/* /opt/mmbot/ && systemctl restart mmbot` (`systemctl restart mmbot`
to also cancel). Settings stay in force across a rollback; a key the old code does not know is refused, harmless.

## D. Emergency
- Stop and cancel everything: `sudo systemctl stop mmbot` (exit codes 3 fatal API / 4 kill switch stay stopped).
- Kill switch: the bot stops itself 30% below the start value, cancels, leaves run/kill_switch.tripped and exits 4;
  read the log, then `rm /opt/mmbot/run/kill_switch.tripped && sudo systemctl start mmbot`.
- Cancel without the service: `cd /opt/mmbot && .venv/bin/python mm_bot.py cancel` (`--all` for every order on the account).
- Look without touching: `.venv/bin/python mm_bot.py status`; a dry run (`mm_bot.py run`, no `--live`) sends nothing.
