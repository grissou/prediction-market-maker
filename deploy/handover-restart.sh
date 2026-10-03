#!/bin/sh
# Deploy without pulling quotes: stop the bot WITHOUT cancelling (SIGUSR1), wait for it to exit, start the new
# version, which adopts the resting orders (mm_bot.py: handover). If anything looks wrong afterwards:
#   sudo systemctl stop mmbot      (a normal stop cancels everything, as always)
# A plain `systemctl restart mmbot` still cancels everything on stop and on start.
#
# The bot exits by itself within handover_exit_max_seconds (150 s) of the signal; this waits up to 240 s. Whatever
# happens, the service is started at the end: exiting without it (as before, after 90 s) left NO bot running -
# Restart=on-failure never restarts an exit-0 stop - and the resting orders unmanaged for up to 30 min.
sudo systemctl kill -s USR1 mmbot
for i in $(seq 1 480); do
  systemctl is-active -q mmbot || break
  sleep 0.5
done
if systemctl is-active -q mmbot; then
  # `start` would be a no-op on a unit that is still active, and when the old bot finally exits 0 nothing would
  # restart it (Restart=on-failure): fall back to a plain restart (SIGTERM = the normal cancel-everything stop).
  echo "WARNING: bot still running 240 s after the handover signal - falling back to a plain restart" >&2
  echo "         (this cancels all orders); check journalctl -u mmbot" >&2
  sudo systemctl restart mmbot || exit 1
else
  sudo systemctl start mmbot || exit 1
fi
echo "restarted; watch: journalctl -u mmbot -f | grep -i handover"
