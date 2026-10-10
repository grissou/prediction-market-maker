"""
Shared helpers: paths, clock (utcnow/iso/parse_ts), notifications (notify/alert/fatal), the logger,
JSON writing, the exchange's fixed facts (TICK, PMIN/PMAX, RACE_TITLE, PARTY_SIGN) and the exit
codes. Tests patch util.alert/notify/utcnow/time, so other modules call these through the module
(util.alert(...)), never by a bare imported name.

Must never import any other mmbot module and must never hold bot state.
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
# and use the wait to download every order book so quoting can start at the open. Polymarket prices and
# the realtime feed are started first, so both are warm. In the last minute: no requests, sleep until
# the start time, then check every second; the first quotes go out within ~1 s, then the self-test.
#
# When cycles run:
#   - Realtime feed connected: as soon as the exchange reports a change (at most one cycle per
#     min_cycle_seconds), plus a full safety check every realtime_heartbeat_seconds.
#   - No feed: every loop_seconds, and every cycle is a full check.
#
# One cycle (Bot.cycle):
#   1. Read positions (with the feed: after a fill, or on a full check) and our open orders (full
#      checks only; in between, the bot keeps its own record: placements add, cancels and fills remove).
#   2. Books: re-download the ones the feed says changed. On a full check (or when many books change
#      at once), ask for the best bid/ask of every book in one bulk request per 100 exchanges and
#      re-download only those whose best prices moved. Our own orders are removed from each book.
#      Downloads only use the request budget left after reserving some for orders.
#   3. Fair value = middle of the book, ignoring small orders (so nobody can move it with 1 share),
#      optionally leaned toward outside reference prices. Parties in one race are scaled to sum to 1.
#   4. Log new fills, together with the fair value at the moment we quoted.
#   5. Arbitrage: if other traders' bids on every party in a race add up to more than 1, sell them all.
#   6. Decide the quote: one tick better than the best other trader, but never closer to fair
#      value than min_edge, skewed away from our inventory and from the net national-swing exposure,
#      and one-sided where a guard says so.
#   7. Compare with the orders actually resting on the book; cancel/replace only what differs (an
#      order one tick off target is kept if it's still safe: that keeps our place in line).
#   8. Every record_seconds, save a snapshot of every market to market_data.sqlite.
#
# Safety nets:
#   - Every order expires after order_ttl: if the bot dies, its quotes vanish on their own.
#   - Ctrl+C, a crash, or repeated errors -> cancel everything.
#   - Kill switch on account drawdown; caps on worst-case loss and on national-swing exposure;
#     jump guard on sudden price moves; tail and outside-price guards; reduce-only before close.
#   - Hard request budget below the API's rate limit; a 429 pauses every request as instructed.
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
