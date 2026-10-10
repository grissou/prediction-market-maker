#!/usr/bin/env bash
# One-time setup of the bot on an Ubuntu/Debian server. Also safe to re-run to UPDATE the bot
# (copies the new code, keeps your .env, logs, fills and recorded data). Run from your Mac:
#
#   scp -r mm_bot.py mmbot ref_prices.py ref_map.json deploy root@SERVER_IP:/root/mmbot-src
#   ssh -t root@SERVER_IP 'bash /root/mmbot-src/deploy/setup.sh'
#
# On servers where you log in as your own user instead of root (Azure: azureuser), use sudo:
#
#   scp -r mm_bot.py mmbot ref_prices.py ref_map.json deploy azureuser@SERVER_IP:mmbot-src
#   ssh -t azureuser@SERVER_IP 'sudo bash mmbot-src/deploy/setup.sh'
#
# It does NOT start live trading. When you're ready: systemctl enable --now mmbot
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"     # folder holding mm_bot.py and deploy/
DEST=/opt/mmbot

echo "==> installing packages (Python venv, firewall, automatic security updates, fail2ban)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv ufw unattended-upgrades fail2ban >/dev/null

# ---------------------------------------------------------------- server hardening
echo "==> hardening the server"
# Firewall: the bot only makes outgoing connections, so the only way in we need is SSH.
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
# Install security updates automatically every day.
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades
# fail2ban bans IPs that keep guessing SSH passwords (its sshd protection is on by default).
systemctl enable --now fail2ban >/dev/null 2>&1 || true
# Log in with SSH keys only - but ONLY if a key is installed, otherwise you'd lock yourself out.
# The key is root's, or (run with sudo, e.g. Azure's azureuser) the logged-in user's.
USER_KEYS=/root/.ssh/authorized_keys
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  USER_KEYS="$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys"
fi
if [ -s /root/.ssh/authorized_keys ] || [ -s "$USER_KEYS" ]; then
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/50-mmbot-hardening.conf
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  echo "    SSH: password logins disabled (key-only)"
else
  echo "    WARNING: no SSH key found for root or ${SUDO_USER:-root}, so password logins are left ON."
  echo "    Add your key (ssh-copy-id USER@SERVER_IP from the Mac), then re-run this script."
fi
timedatectl set-timezone UTC 2>/dev/null || true      # server clock in UTC, like the bot's logs
timedatectl set-ntp true 2>/dev/null || true          # keep the clock exact: order expiry and the open use it
# A swap file, so a memory spike (e.g. automatic updates running) can't get the bot killed on a small
# 1 GB server. Skipped if the server already has swap.
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    swap: 2 GB swap file added"
fi

# ---------------------------------------------------------------- the bot
echo "==> creating a 'mmbot' user (the bot doesn't need to run as root)"
id mmbot &>/dev/null || useradd --system --home "$DEST" --shell /usr/sbin/nologin mmbot

echo "==> copying the bot to $DEST"
mkdir -p "$DEST"
install -m 644 "$SRC/mm_bot.py" "$DEST/mm_bot.py"
for f in ref_prices.py ref_map.json; do                 # optional reference-price feature
  [ -f "$SRC/$f" ] && install -m 644 "$SRC/$f" "$DEST/$f"
done
[ -d "$DEST/.venv" ] || python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip requests realtime

if [ ! -f "$DEST/.env" ]; then
  echo "==> settings (stored in $DEST/.env, readable only by the bot)"
  read -rsp "Super Market API key: " KEY; echo
  read -rp  "Tournament slug [midterm-elections]: " SLUG; SLUG=${SLUG:-midterm-elections}
  read -rp  "Phone alerts: ntfy.sh URL, e.g. https://ntfy.sh/long-random-name (Enter to skip): " ALERT
  printf 'SUPERMARKET_API_KEY=%s\nTOURNAMENT_SLUG=%s\nALERT_URL=%s\n' "$KEY" "$SLUG" "$ALERT" > "$DEST/.env"
fi
chmod 600 "$DEST/.env"
chown -R mmbot:mmbot "$DEST"

echo "==> installing the systemd service"
install -m 644 "$SRC/deploy/mmbot.service" /etc/systemd/system/mmbot.service
systemctl daemon-reload

echo "==> checking the connection (read-only)"
runuser -u mmbot -- "$DEST/.venv/bin/python" "$DEST/mm_bot.py" status | head -12
echo "==> request latency from this server (lower = faster bot; ~0.4 s from the UK):"
curl -s -o /dev/null -w "    %{time_total} s\n" https://sig.thesuper.market/api/v1/tournaments || true

cat <<'EOF'

Done. Next:
  systemctl enable --now mmbot        start trading (and restart on reboot). Start it at least 30 min
                                      before trading opens: it downloads the books while it waits
  journalctl -u mmbot -f              watch the log
  cat /opt/mmbot/status.json          quick health check: "realtime": "connected",
                                      "rate_limited_total": 0, "last_cycle_ok": true
  systemctl stop mmbot                stop (cancels all orders)
If `systemctl status mmbot` shows exit code 4, the kill switch fired: read the log, then
  rm /opt/mmbot/kill_switch.tripped && systemctl start mmbot
If you updated the bot by re-running this script:  systemctl restart mmbot
EOF
