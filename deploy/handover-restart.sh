#!/bin/sh
# Deploy without pulling quotes: stop the bot WITHOUT cancelling (SIGUSR1), wait for it to exit, start the new
# version, which adopts the resting orders (mm_bot.py: handover). If anything looks wrong afterwards:
#   sudo systemctl stop mmbot      (a normal stop cancels everything, as always)
# A plain `systemctl restart mmbot` still cancels everything on stop and on start.
set -e
sudo systemctl kill -s USR1 mmbot
for i in $(seq 1 180); do
  systemctl is-active -q mmbot || break
  sleep 0.5
done
if systemctl is-active -q mmbot; then
  echo "bot did not stop within 90 s - check journalctl -u mmbot" >&2
  exit 1
fi
sudo systemctl start mmbot
echo "restarted; watch: journalctl -u mmbot -f | grep -i handover"
