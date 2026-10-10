"""
Shared helpers with no opinion about trading: paths, the clock (utcnow / iso / parse_ts),
notifications (notify / alert / fatal), the logger, JSON writing, the exchange's fixed facts (TICK,
PMIN / PMAX, RACE_TITLE, PARTY_SIGN) and the exit codes. The HOW THE BOT WORKS block below is the
plain-English tour of a cycle.

Tests patch util.alert / notify / utcnow / time, so other modules call these through the module
(util.alert(...)), never by a bare imported name.

Must never import any other mmbot module, never hold bot state and never touch the exchange.
"""
import json
import logging.handlers
import math
import os
import re
import requests
import time
from datetime import datetime, timezone


# Folder this file lives in. Log and state files are kept here wherever the bot is started from.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo root


def load_env_file(path):
    """Read KEY=value lines from a .env file into the environment, skipping keys that are already
    set. On a server the API key lives in .env next to the bot (the venv trick is Mac-only)."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(os.path.join(HERE, ".env"))    # must run before SETTINGS reads the environment

# =============================================================================================
# FIXED FACTS - set by the exchange and the markets themselves. Not tuning knobs.
# =============================================================================================

TICK, PMIN, PMAX = 0.005, 0.005, 0.995    # prices must sit on a 0.5c grid between 0.5c and 99.5c
BULK_MAX_IDS = 100                        # /exchanges/prices accepts at most 100 exchange ids per request

# Every market title looks like "Will the Republican Party win the Ohio Senate?".
# Markets with the same race name exclude each other: exactly one party wins.
RACE_TITLE = re.compile(r"^Will the (\w+) Party win the (.+)\?$")
# How each party's YES moves with a national swing. Used to cap correlated exposure across races.
PARTY_SIGN = {"Republican": +1, "Democratic": -1}

# Exit codes, so a service manager (systemd) knows whether restarting could help.
# 0 = normal stop; 1 = crash (restart it); these two mean "don't restart, a human must look":
EXIT_FATAL = 3        # bad/revoked API key, missing scope, terms not accepted, missing settings
EXIT_KILLED = 4       # kill switch tripped
EXIT_WATCHDOG = 5     # no cycle completed for watchdog_exit_seconds: systemd restarts (Restart=on-failure)
# API error codes that retrying or restarting will never fix.
FATAL_API_CODES = {"MISSING_API_KEY", "INVALID_API_KEY", "API_KEY_REVOKED", "API_KEY_EXPIRED",
                   "INSUFFICIENT_SCOPES", "ACCOUNT_BANNED", "TERMS_NOT_ACKNOWLEDGED"}

log = logging.getLogger("mm")

# =============================================================================================
# HOW THE BOT WORKS
#
# Before trading opens (live mode): wait, checking the tournament status every start_check_seconds,
# and use the wait to download every order book and warm the Polymarket prices and the realtime feed,
# so the first quotes go out within ~1 s of the open; the start-up self-test follows.
#
# When cycles run: with the realtime feed, as soon as the exchange reports a change (at most one cycle
# per min_cycle_seconds) plus a full check every realtime_heartbeat_seconds; without it, every
# loop_seconds, and every cycle is a full check.
#
# One cycle (Bot.cycle, in this order):
#   1. Read exchange state: positions and our open orders (full checks only; in between the bot keeps
#      its own record), then the order books that changed (bulk best-price scan on full checks). Our
#      own orders are removed from each book. Downloads use the request budget left after orders.
#   2. Fair value (pricing.py): the middle of the book ignoring small orders, leaned toward Polymarket
#      where it is liquid; parties in one race are scaled to sum to 1. Then the per-market size plan
#      and the new fills (logged with the fair value we quoted at).
#   3. Arbitrage (arb.py): if other traders' bids on every party in a race sum to more than 1, sell
#      them all; NO+NO sets are unwound as pairs.
#   4. Risk (risk.py): drawdown kill switch, worst-case-loss and national-swing caps, the cash gate,
#      the value-adds pause, reduce-only decisions. Nothing below may add risk these rules refuse.
#   5. The allocator (value.py): decides which markets get capital - sells the edge-poor holdings,
#      reads the cash back, buys the edge-rich ones (immediate-or-cancel); refills the MM reserve.
#   6. The harvest ladder (ladder.py): resting maker levels that sell held inventory above fair value.
#   7. Market-making quotes (quoting.py, mm.py): per market, one tick better than the best other
#      trader but never closer to fair value than min_edge, skewed toward the target holding and away
#      from the swing exposure, one-sided or reduce-only where a guard says so, sized within the
#      cash the gate and the ladder leave. Stale MM inventory is recycled out through the reducing side.
#   8. Reconcile: compare the desired set with the orders resting on the book and cancel / replace
#      only what differs (an order a tick off target is kept while safe: that keeps our place in line),
#      then send. Every record_seconds a snapshot of every market goes to market_data.sqlite.
#
# Safety nets: every order expires after order_ttl (if the bot dies its quotes vanish); Ctrl+C, a
# crash or repeated errors cancel everything (SIGUSR1 hands the quotes over to a new version instead);
# a hard request budget below the API's rate limit, and a 429 pauses every request as instructed.
# =============================================================================================


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    """datetime -> '2026-10-01T16:00:00.000Z' (the format the API expects)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def numeric_fields(d, skip=()):
    """{key: float} of a reply's numeric scalar fields (numbers sent as strings too; not ids, flags or nesting)."""
    out = {}
    for k, v in d.items():
        if k in skip or isinstance(v, bool) or (k.lower().endswith("id") and k != "id"):
            continue
        if isinstance(v, (int, float)):
            x = float(v)
        elif isinstance(v, str):
            try:
                x = float(v)
            except ValueError:
                continue
        else:
            continue
        if math.isfinite(x):
            out[k] = x
    return out


def parse_ts(s):
    """ISO timestamp -> datetime. Some API timestamps carry 7 decimal places ("13:56:47.7844565"), which
    Python before 3.11 can't read, so the fraction is cut to microseconds first."""
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def rnd(x):
    """Round a price to 3 dp so floats like 0.30000000004 compare equal. None stays None."""
    return None if x is None else round(float(x), 3)


def bot_path(name):
    """A settings file name -> full path in the bot's folder (absolute paths are left alone)."""
    return name if os.path.isabs(name) else os.path.join(HERE, name)


def write_json(path, data):
    """Write JSON atomically (temp file + rename), so a crash mid-write can't leave a broken file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def notify(message, title="mm_bot", priority="default", tags=""):
    """Push a notification to your phone through ntfy, if ALERT_URL is set.

    Setup: install the free ntfy app (iOS/Android), tap "+", and subscribe to the topic at the end of
    your ALERT_URL (e.g. https://ntfy.sh/mmbot-3f9a...). Anyone who knows the topic name can read the
    messages, so it should be long and random. Best-effort: never raises, never blocks for long.
    Returns True if the message was accepted."""
    if not config.CFG.alert_url:
        return False
    try:
        r = requests.post(config.CFG.alert_url, data=message.encode(), timeout=5,
                          headers={"Title": title, "Priority": priority, "Tags": tags})
        return r.ok
    except Exception:                   # network, or anything else: an alert must never crash the bot
        return False


def alert(msg):
    """Something needs attention (kill switch, crash, partial arbitrage...): log it and push it."""
    log.warning("ALERT: %s", msg)
    notify(msg, title="mm_bot alert", priority="high", tags="warning")


def fatal(msg):
    """Stop for good: restarting won't help, a human has to fix something. The exit code tells
    systemd not to restart. (SystemExit still runs the bot's shutdown, which cancels all orders.)"""
    log.critical(msg)
    alert(f"STOPPED: {msg}")
    raise SystemExit(EXIT_FATAL)


from mmbot import config  # noqa: E402  (at the end: config imports util first)
