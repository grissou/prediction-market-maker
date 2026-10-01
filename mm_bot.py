#!/usr/bin/env python3
"""
Market-making bot for The Super Market API (SIG Predictions Cup, Midterm Elections tournament).

GOAL
    Earn the bid/ask spread while staying close to flat. The bot has no opinion on who wins:
    it quotes around the price the market already shows and profits when other traders cross
    its spread (someone sells to us at our bid, someone else buys from us at our ask).

COMMANDS
    python mm_bot.py tournaments      list tournaments you can see (find TOURNAMENT_SLUG here)
    python mm_bot.py markets          list markets + exchange IDs
    python mm_bot.py status           balance, P&L, positions, open orders
    python mm_bot.py run              DRY RUN: reads live data, logs the orders it WOULD send
    python mm_bot.py run --live       trade for real
    python mm_bot.py cancel           cancel every open order in the tournament
    python mm_bot.py report           spread-capture stats from fills.csv
    python mm_bot.py summary          build the phone summary now and send it (test your ALERT_URL)

ENVIRONMENT (set by `source .venv/bin/activate` on the Mac, or by a .env file next to this script)
    SUPERMARKET_API_KEY, TOURNAMENT_SLUG
    optional: ONLY_EXCHANGES="id1,id2" to trade a subset
              ALERT_URL=https://ntfy.sh/<random-topic> for phone alerts (kill switch, crashes) and a
              summary every 2 hours: install the ntfy app and subscribe to that topic

FILES THE BOT WRITES (next to this script)
    fills.csv            every fill, for `report`
    mm_bot.log           the log (rotates, so it can't fill the disk)
    status.json          health snapshot after every cycle: `cat status.json` to check on it
    order_notes.json     what each open order was for, so fills still get attributed after a restart
    kill_switch.tripped  created when the kill switch fires; the bot refuses to trade until you delete it
    market_data.sqlite   books, fair values and our quotes over time, for tuning settings afterwards

OPTIONAL COMPANION FILES
    ref_prices.py + ref_map.json   reference prices from Polymarket/Kalshi (see ref_prices.py);
                                   used automatically when both exist, ignored otherwise

SPEED AND THE RATE LIMIT
    With the `realtime` package installed (pip install realtime), the exchange pushes book changes
    and our fills to the bot, so it reacts in ~1-2 s and sends no requests while nothing happens.
    Each new Polymarket reading (every 5 s) also starts a cycle straight away.
    Without it, or if the connection drops, it polls every 10 s instead, automatically.
    The API allows roughly 100 requests a minute (not published; measured). The bot never uses more
    than `requests_per_minute` (80), keeping part of it free for orders.

STARTING FOR REAL
    Start `run --live` at least 30 minutes before trading opens: while it waits for the open it
    downloads every order book, so it can quote the moment trading starts.

All the numbers you can tune (risk limits, spreads, sizes, timings) are in SETTINGS below.
"""

import asyncio
import csv
import email.utils
import json
import logging
import logging.handlers
import math
import os
import random
import re
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter

# Folder this file lives in. Log and state files are kept here wherever the bot is started from.
HERE = os.path.dirname(os.path.abspath(__file__))


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
# SETTINGS - every number you might want to change. Nothing below this block needs editing.
# Prices are probabilities: 0.01 = 1 cent = 1 percentage point. Times are in seconds unless named.
# =============================================================================================

@dataclass
class Config:
    # --- RISK LIMITS -------------------------------------------------------------------------
    max_drawdown_pct: float = 0.30        # kill switch: stop if account value falls this far below the initial balance (0.30 = 30%)
    kill_confirmations: int = 2           # ...on this many readings in a row (so one glitchy number can't trigger it)
    reserved_cash_mode: str = "auto"      # does the API's account value leave out cash locked in our open orders?
                                          #   "auto" = work it out from the first orders we post, "add" = yes, add
                                          #   it back, "ignore" = no, use the API's number as is
    reserved_calib_min: float = 1000.0    # "auto" only judges when locked cash changed by at least this much...
    reserved_calib_votes: int = 2         # ...and needs this many agreeing observations before deciding
    max_worst_case_frac: float = 0.30     # worst-case settlement loss > 30% of account value -> reduce-only everywhere
    # Sizes below are FRACTIONS OF ACCOUNT VALUE (0.01 = 1%), recalculated as the account changes, so the
    # bot sizes up after gains and down after losses. "shares" = contracts that each pay up to 1.
    sizing_step_frac: float = 0.05        # ...but the account value they're a fraction of only moves in steps: it's
                                          #   updated once the real value is 5% away. Otherwise a 10-SUSQie dip turns
                                          #   every 100-share order into a 99-share one and replaces all of them
    max_party_delta_frac: float = 0.15    # hard cap on |net Republican-minus-Democrat YES shares| over all races
                                          #   (national swing): 15% of account = 15,000 shares at 100k, room for
                                          #   the 10,000-share positions in the party-control markets. At the cap,
                                          #   the side that would add to it is blocked on every Rep/Dem market
    party_skew_at_cap: float = 0.015      # before that: as the net exposure builds, shade every Rep/Dem quote
                                          #   against it, growing to 1.5c at the cap, so the bot sheds it while
                                          #   still quoting both sides (0 = off, hard cap only)
    max_position_frac: float = 0.03       # limit on |net YES shares| per exchange WITHOUT a liquid Polymarket
                                          #   price: 3,000 shares at 100k. With one, Kelly sizing sets the limit
    max_order_cash_frac: float = 0.01     # max cash tied up in a single order: 1,000 at 100k
    tail_low: float = 0.05                # fair value below this: don't SELL YES (risks ~95c a share to earn ~1c)...
    tail_high: float = 0.95               # ...above this: don't BUY YES. Either side still allowed to shrink a position
    jump_threshold: float = 0.15         # fair value moved 15c in one cycle -> probably news -> stop quoting that market...
    jump_cooldown_seconds: float = 60.0   # ...for this long
    # Election night: every market closes 4 Nov 00:00 UTC; the first polls close ~23:00 UTC. Countdown:
    flatten_hours_before_close: float = 12.0   # only trade toward flat (a hedged Rep/Dem pair counts as flat)
    flatten_per_market_hours: float = 6.0      # flatten EACH market on its own (a hedged pair still settles as one
                                               #   win + one loss, which hurts Smart Score's win rate)
    exit_hours_before_close: float = 2.0       # exit hard: trade against other orders if needed to get flat...
    exit_max_slippage: float = 0.03            # ...but never more than 3c worse than fair value
    stop_minutes_before_close: float = 15.0    # nothing at all (orders cancelled)
    max_failed_cycles: int = 3            # this many API-error cycles in a row -> cancel everything until healthy

    # --- QUOTING -----------------------------------------------------------------------------
    min_edge: float = 0.01                # never quote closer than this to our reservation price
    max_half_spread: float = 0.04         # never quote further than this from it
    order_size_frac: float = 0.005        # shares per order when size_by_activity is off (and before the first
                                          #   plan): 500 shares at 100k. Normally QUOTE SIZES BY ACTIVITY decides
    skew_per_share: float = 0.00003       # reservation price moves 0.3c per 100 shares of race-adjusted inventory
    keep_fraction: float = 0.5            # keep a partly-filled order (and its queue spot) while >= 50% remains
    reprice_tolerance_ticks: int = 1      # leave an order alone if its target price moved by at most this many
                                          #   ticks (0.5c each) and it still keeps min_edge without crossing anyone:
                                          #   saves a cancel + replace and keeps our place in line (0 = always move)

    # --- QUOTE SIZES BY MARKET ACTIVITY -------------------------------------------------------
    # Trading is concentrated (9 of 113 races hold 80% of Polymarket's volume) and tournament trades are big,
    # so capital goes where the trades are: busy markets get big quotes, quiet ones small, and all resting
    # quotes together lock at most quote_capital_frac of the account (a two-sided quote locks ~1 a share).
    # Activity = Polymarket volume before the open, shifting toward the tournament's own trades as they come.
    size_by_activity: bool = True         # False = every market quotes order_size_frac
    quote_capital_frac: float = 0.60      # resting quotes lock at most 60% of account value; the rest stays free
                                          #   for positions, arbitrage and the election-night exit
    size_min_frac: float = 0.001          # quietest markets: 100 shares at 100k (still present, still earning)
    size_max_frac: float = 0.02           # busiest ordinary markets: up to 2,000 shares at 100k
    headline_races: tuple = ("U.S. House", "U.S. Senate")   # party control of Congress: by far the most traded
    headline_size_frac: float = 0.10      # quotes of 10,000 shares there (10% of account value; like every size,
                                          #   it grows and shrinks with the account)...
    headline_position_frac: float = 0.10  # ...and positions up to 10,000 shares there (a flat limit, not Kelly)
    size_plan_seconds: float = 1800.0     # re-plan every 30 min, as tournament trades come in
    size_plan_step: float = 0.25          # a market's size only changes when a new plan moves it by more than
                                          #   25%, so re-planning doesn't replace orders (and queue spots) for nothing
    live_activity_trades: int = 1000      # tournament trades seen before they count fully...
    live_activity_max_weight: float = 0.8 # ...which is 80%; Polymarket volume keeps at least 20%

    # --- FAIR VALUE --------------------------------------------------------------------------
    fv_min_depth: int = 200               # skip price levels until this many shares have accumulated (anti-spoofing)
    max_spread_for_fv: float = 0.30       # book wider than this -> no reliable price -> don't quote

    # --- REFERENCE PRICES (Polymarket via ref_prices.py; only active if ref_map_file exists) ---
    # Polymarket is treated as the better estimate of the true price: the tournament book is seeded
    # from it and lags it.
    ref_map_file: str = "ref_map.json"    # "" = off
    ref_refresh_seconds: float = 5.0      # download Polymarket prices this often. Its public API is live (checked
                                          #   against its order book) and separate from SIG's request budget, so
                                          #   the only delay is this interval: faster bots pick off stale quotes
    ref_weight: float = 0.7               # fair value = 70% Polymarket + 30% tournament book (0 = guard only)
    ref_guard_gap: float = 0.05           # Polymarket and the TOURNAMENT BOOK disagree by more than this -> don't
                                          #   quote the side Polymarket says is mispriced. Keep >= 0.05: Polymarket
                                          #   can have sudden unexplained moves
    ref_jump_threshold: float = 0.03      # Polymarket moved this much between two readings (5 s apart) ->
    ref_jump_cooldown_seconds: float = 60.0   #   pull that market's quotes for this long. A real move then shows
                                              #   up in the weighting; a spike that reverts never touched us
    ref_max_plausible_gap: float = 0.25   # a Polymarket price further than this from the tournament book is almost
                                          #   certainly a wrong match (e.g. a replaced candidate): ignore it + alert
    ref_liquid_spread: float = 0.03       # only lean on a Polymarket price (weighting + Kelly sizing) when its own
                                          #   bid/ask spread is at most this. Thin or last-trade-only prices (which
                                          #   can be days old) are used for the guards only

    # --- POSITION SIZING (fractional Kelly; markets with a liquid Polymarket price only) -------
    # For each side, the most we'd hold = kelly_fraction x (edge / (1 - cost)) x account value, where
    # edge = how far our price is on the right side of Polymarket's probability and cost = what one
    # contract costs us (bid for YES, 1 - ask for NO). It grows with the account and shrinks with it.
    kelly_fraction: float = 0.25          # quarter Kelly (0.5 = half). Full Kelly (1.0) is far too aggressive for
                                          #   estimated odds. Higher = bigger positions and bigger swings (lower Sharpe)
    kelly_max_market_frac: float = 0.02   # never more than 2% of account value at risk in one market
    kelly_no_edge_frac: float = 0.001     # on a side where Polymarket sees no edge, allow only this much: 100
                                          #   shares at 100k (keeps two-sided quoting without betting against it)

    # --- ARBITRAGE (guaranteed profit inside a race) ------------------------------------------
    arb_enabled: bool = True
    arb_min_profit: float = 0.03          # act when other traders' bids across a race add up to >= 1 + this. Not
                                          #   lower: 1c arbitrage adds lots of volume for little profit, which drags
                                          #   down Smart Score's ROI (P&L / volume)
    arb_max_frac: float = 0.005           # most sets sold per arbitrage: 0.5% of account = 500 at 100k
    arb_order_ttl: float = 10.0           # arbitrage orders expire after this (leftovers are also cancelled at once)
    arb_cooldown_seconds: float = 30.0    # after acting on a race, leave it alone for this long

    # --- TAKING STALE HOUSE QUOTES (liquid Polymarket prices only) -----------------------------
    # When Polymarket has clearly moved and the tournament's best quote hasn't followed, trade against
    # that quote directly (buy the stale ask / sell to the stale bid), sized with Kelly.
    take_enabled: bool = True
    take_edge: float = 0.05               # Polymarket must be at least this far past the quote. Keep >= 0.05:
                                          #   Polymarket can have sudden unexplained moves
    take_confirm_seconds: float = 30.0    # ...on every Polymarket reading for at least this long, so a spike that
                                          #   reverts is never traded
    take_cooldown_seconds: float = 60.0   # after taking in a market, leave it alone for this long
    take_order_ttl: float = 10.0          # take orders expire after this (leftovers are also cancelled at once)

    # --- ORDER LIFECYCLE ---------------------------------------------------------------------
    order_ttl: float = 1800.0             # every order expires after 30 min (dead-man's switch). Longer = fewer
                                          #   replacements (each costs requests and our place in line)
    refresh_before_expiry: float = 180.0  # replace an order once it has less than 3 min to live
    batch_size: int = 20                  # orders per POST /orders/batch
    parallel_writes: int = 4              # order writes (cancels, batches) in flight at once. Cancels go first, then
                                          #   new orders: party-control (headline) markets first, then the biggest
                                          #   quotes. 1 = the old way: one write at a time, the cycle waiting for each
    write_read_reserve: int = 3           # order writes leave this many requests/min of the budget free (positions,
                                          #   orders reads); book downloads already leave budget_reserve for writes
    write_wait_seconds: float = 3.0       # a cycle waits at most this long for its writes; slower ones (day one: 15-30 s)
                                          #   finish in the background and their exchanges are left alone until then
    pending_seconds: float = 90.0         # placement outcome unknown -> leave that exchange alone this long
    recent_order_grace_seconds: float = 15.0  # the open-orders list can lag the exchange: for this long, trust our
                                              #   own record of an order we just placed (or cancelled) over it,
                                              #   so the bot never places the same quote twice
    fail_pause_seconds: float = 30.0      # order rejected -> don't retry that exchange for this long
    shutdown_cancel_attempts: int = 3     # tries at cancelling everything when the bot exits
    selftest_enabled: bool = True         # live mode: before quoting, place + check + cancel two 1-share orders at
                                          #   extreme prices to confirm the API behaves as assumed; stop if not
    selftest_retry_seconds: float = 60.0  # exchange busy during the test (timeout, 409 in flight, 429, 5xx): not a
                                          #   failure - try again this much later, quoting meanwhile
    selftest_alert_after: float = 900.0   # ...and send one alert if it still hasn't managed after this long

    # --- ORDER BOOKS -------------------------------------------------------------------------
    book_depth: int = 10                  # price levels per side to download
    max_books_per_cycle: int = 30         # full-book downloads per cycle (still capped by the spare request budget)
    book_max_age: float = 600.0           # re-download each book at least this often, even if it looks unchanged.
                                          #   Changed books are caught much sooner by the bulk prices anyway
    book_stale: float = 900.0             # a book not confirmed current (downloaded, or its best prices matched a
                                          #   bulk check) for this long isn't trusted -> don't quote it. Before that
                                          #   happens the bot re-checks it (book_reverify_seconds), so this only
                                          #   bites when the exchange can't be read at all
    book_reverify_seconds: float = 120.0  # a book unconfirmed for this long gets a bulk best-price check (one request
                                          #   per 100 books) on the next cycle, even between full checks

    # --- TIMING / NETWORK --------------------------------------------------------------------
    loop_seconds: float = 10.0            # target time between cycle starts
    market_reload_seconds: float = 600.0  # re-read the market list (adds new markets, drops closed ones)
    start_check_seconds: float = 60.0     # before the tournament opens, check its status this often...
    open_quiet_seconds: float = 60.0      # ...until this long before the start: then stop downloading books (keeps
                                          #   the request budget free for the first quotes) and sleep until the start
    open_poll_seconds: float = 1.0        # from the start time, check every second until trading is open, so the
                                          #   first quotes go out within ~1 s (first in line at each price wins)
    # The API rate-limits and doesn't publish the limit. Measured 25 Sep: roughly 100 requests per
    # minute, then "429, retry after 60 s". So every request (all threads, all features) goes through
    # a hard per-minute budget. The docs warn that polling /orders and /portfolio/* hits it fastest.
    # NOTE: other commands (status, markets...) run while the bot is live use the same key's budget.
    requests_per_minute: int = 80         # hard budget, sliding 60 s window. Cut by 25% after a 429, then
                                          #   recovers slowly (+1 a minute), so it tunes itself
    budget_reserve: int = 20              # requests per minute kept free for orders, cancels and account
                                          #   reads; book downloads only use what's left
    parallel_requests: int = 2            # HTTP requests in flight at once (1 = one at a time)
    min_request_gap: float = 0.50         # min time between request STARTS, all threads together (2/s max);
                                          #   smooths bursts; doubles after a 429 and drifts back
    max_request_gap: float = 2.0          # ceiling for that automatic slow-down
    request_timeout: float = 15.0         # give up on one HTTP request after this long (it's then retried)
    max_clock_skew_seconds: float = 3.0   # alert at start-up if this computer's clock is further than this from the
                                          #   exchange's: order expiry and the timing of the open use our clock
    max_retries: int = 5                  # retries for network errors / 429 / 502 / 503 / 504
    slow_poll_seconds: float = 30.0       # read P&L (kill switch) and fills this often (plus at once after a fill)
    max_fill_pages: int = 5               # pages of 200 fills to read back per check (catches up to 1000 fills)

    # --- REALTIME FEED (pushed updates instead of polling; needs `pip install realtime`) -------
    realtime_enabled: bool = True         # False = always poll every loop_seconds
    min_cycle_seconds: float = 0.5        # at most one cycle per 0.5 s (the exchange already groups its pushed
                                          #   updates every 250 ms; a cycle with nothing to download costs no requests)
    bulk_check_over: int = 3              # more books than this reported changed at once -> one bulk-price check
                                          #   (3 requests for all 237) instead of downloading each book
    realtime_heartbeat_seconds: float = 30.0   # with the feed: full safety check (bulk prices, positions, orders)
                                               #   this often, because delivery is best-effort
    realtime_token_refresh_seconds: float = 600.0  # start a new session (fresh 3-hour login) this long before it expires
    realtime_session_max_seconds: float = 3600.0   # ...and at least this often anyway: a long-lived socket can die
                                                   #   without the library noticing (it happened on 29 Sep)

    # --- FILES (relative names are kept in the bot's own folder) -----------------------------
    fills_csv: str = "fills.csv"
    log_file: str = "mm_bot.log"          # "" = log to the terminal only
    log_max_mb: float = 10.0              # start a new log file at this size...
    log_backups: int = 5                  # ...keeping this many old ones (so the log can't fill the disk)
    status_file: str = "status.json"      # health snapshot rewritten after every cycle
    order_notes_file: str = "order_notes.json"  # survives restarts, so fills can still be attributed
    kill_file: str = "kill_switch.tripped"      # the kill switch creates it; delete it to allow trading again
    record_file: str = "market_data.sqlite"     # snapshots for tuning later ("" = off)
    record_seconds: float = 60.0          # one snapshot of every market this often (~15 MB a day)

    # --- CONNECTION / ALERTS (from the environment: see top of file) ------------------------
    summary_every_hours: int = 2          # phone summary every N hours, on the hour UTC (2 = 00:00, 02:00, 04:00...),
                                          #   covering what happened since the previous one. 0 = off. Live only
    alert_url: str = os.environ.get("ALERT_URL", "")   # e.g. https://ntfy.sh/some-long-random-name
    api_key: str = os.environ.get("SUPERMARKET_API_KEY", "")
    base_url: str = os.environ.get("SUPERMARKET_BASE_URL", "https://sig.thesuper.market/api/v1")
    slug: str = os.environ.get("TOURNAMENT_SLUG", "")
    only_exchanges: str = os.environ.get("ONLY_EXCHANGES", "")


CFG = Config()

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
    if not CFG.alert_url:
        return False
    try:
        r = requests.post(CFG.alert_url, data=message.encode(), timeout=5,
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

# =============================================================================================
# API - HTTP, retries, rate limiting, one method per endpoint we use
# =============================================================================================

class ApiError(Exception):
    def __init__(self, status, code, msg, data=None):
        super().__init__(f"{status} {code}: {msg}")
        self.status, self.code, self.data = status, code, data


class Api:
    def __init__(self, cfg, live):
        if not cfg.api_key:
            fatal("SUPERMARKET_API_KEY is not set (venv activate script or .env file)")
        self.cfg, self.live = cfg, live
        self.gap = cfg.min_request_gap    # current min gap between request starts; widens after a 429
        self._next_start = 0.0            # earliest time the next request may start (shared by all threads)
        self._lock = threading.Lock()     # several threads send requests at once (parallel_requests)
        self._window = deque()            # start times of requests in the last BUDGET_WINDOW seconds
        self.budget = float(cfg.requests_per_minute)   # current per-minute budget (self-tuning)
        self.rate_limited = 0             # how many 429s we've had (shown in status.json - should stay 0)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"})
        # Keep one open connection per thread, so parallel requests don't queue for a connection.
        self.s.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=cfg.parallel_requests + 2))

    BUDGET_WINDOW = 60.0                  # seconds the per-minute budget is measured over

    def throttle(self):
        """Rate limiter for every request, across all threads:
          1. never more than `self.budget` requests in any BUDGET_WINDOW seconds (hard cap), and
          2. request starts at least `self.gap` apart (smooths bursts).
        A request that would break either rule waits until it wouldn't."""
        with self._lock:
            now = time.monotonic()
            while self._window and now - self._window[0] >= self.BUDGET_WINDOW:
                self._window.popleft()
            start = max(now, self._next_start)
            if len(self._window) >= int(self.budget):
                start = max(start, self._window[-int(self.budget)] + self.BUDGET_WINDOW)
            self._next_start = start + self.gap
            self._window.append(start)
        if start > now:
            time.sleep(start - now)

    def budget_left(self):
        """How many more requests fit in the budget right now."""
        with self._lock:
            now = time.monotonic()
            used = sum(1 for t in self._window if now - t < self.BUDGET_WINDOW)
            return int(self.budget) - used

    def call(self, method, path, params=None, body=None, ok=(200, 201, 207)):
        """One HTTP request with rate limiting and retries.

        Retried automatically (same body -> same idempotencyKey -> the server never places twice):
          - network errors / timeouts
          - 429 RATE_LIMITED (also slows down every later request)
          - 502 ORDER_STATUS_UNKNOWN, 503 SERVICE_UNAVAILABLE / TX_CONFLICT, 504
        Anything else raises ApiError straight away: a 4xx means we broke a rule, so retrying won't help.
        409 REQUEST_IN_FLIGHT is deliberately NOT retried here (the API says wait 90 s); the bot
        handles it by leaving that exchange alone for `pending_seconds` instead of blocking.
        """
        retries = self.cfg.max_retries
        delay = 0.25                      # first back-off wait; doubles each retry up to 8 s
        for attempt in range(retries + 1):
            self.throttle()
            try:
                r = self.s.request(method, self.cfg.base_url + path, timeout=self.cfg.request_timeout,
                                   params={k: v for k, v in (params or {}).items() if v is not None},
                                   data=json.dumps(body) if body is not None else None)
                self.last_date = (r.headers.get("Date"), utcnow())   # server clock vs ours (see Bot.check_clock)
            except requests.RequestException as e:
                if attempt == retries:
                    raise ApiError(0, "NETWORK", str(e))
                time.sleep(delay); delay = min(delay * 2, 8)
                continue

            # A proxy can answer with an HTML error page; never let that crash the bot.
            try:
                data = r.json() if r.content else {}
            except ValueError:
                data = {"error": {"code": "BAD_RESPONSE", "message": r.text[:200]}}

            if r.status_code in ok:
                self.gap = max(self.cfg.min_request_gap, self.gap * 0.99)   # drift back to normal speed
                self.budget = min(self.cfg.requests_per_minute, self.budget + 1 / 60)   # ~+1 per 60 successes
                return r.status_code, data

            err = data.get("error", {}) if isinstance(data, dict) else {}
            ra = r.headers.get("Retry-After", "")
            retry_after = float(ra) if ra.replace(".", "", 1).isdigit() else None
            if r.status_code == 429:
                # Rate limited. Don't just retry this one request: pause EVERY thread for as long as the
                # server asks, and slow down for good, so we never keep knocking on a closed door.
                pause = retry_after if retry_after is not None else 5.0
                with self._lock:
                    already_paused = self._next_start > time.monotonic() + 1
                    self._next_start = max(self._next_start, time.monotonic() + pause)
                    self.gap = min(self.cfg.max_request_gap, self.gap * 2)
                    if not already_paused:        # the budget was too generous: cut it by a quarter
                        self.budget = max(20.0, self.budget * 0.75)
                    self.rate_limited += 1
                if not already_paused:
                    log.warning("RATE LIMITED (429): pausing all requests for %.0f s; budget now %.0f/min",
                                pause, self.budget)
                if attempt < retries:
                    continue                  # throttle() makes the retry wait until the pause is over
            elif r.status_code in (502, 503, 504) and attempt < retries:
                time.sleep(retry_after if retry_after is not None else delay + random.random() * delay)
                delay = min(delay * 2, 8)
                continue
            raise ApiError(r.status_code, err.get("code", "?"), err.get("message", r.text[:200]), data)

    def get(self, path, **params):
        return self.call("GET", path, params=params)[1]

    def paged(self, path, **params):
        """GET every page of a cursor-paginated list endpoint."""
        out, cursor = [], None
        while True:
            page = self.get(path, cursor=cursor, **params)
            out += page.get("data", [])
            if not page.get("pagination", {}).get("hasMore"):
                return out
            cursor = page["pagination"]["nextCursor"]

    # --- reads ------------------------------------------------------------------------------
    def tournament(self):
        return self.get(f"/tournaments/{self.cfg.slug}")

    def markets(self):
        return self.paged(f"/tournaments/{self.cfg.slug}/markets", limit=100, status="open")

    def positions(self):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/positions")

    def pnl(self):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/pnl", period="all")

    def leaderboard(self):
        """Our rank: {"myRank": 3, "total": 120, ...} (period = all-time)."""
        return self.get(f"/tournaments/{self.cfg.slug}/leaderboard", limit=1)

    def smart_score(self):
        """Our Smart Score entries ([] until we've been scored): smartScoreDecayed, rank, totalTraders,
        isElite... per marketType. SIG's recruiting export ranks candidates by this score."""
        return self.get(f"/tournaments/{self.cfg.slug}/me/smart-score")

    def fills_page(self, cursor=None):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/fills", limit=200, cursor=cursor)

    def open_orders(self, tid, eid=None):
        return self.paged("/orders", status="open", tournamentId=tid, exchangeId=eid, limit=200)

    def book(self, eid, tid):
        return self.get(f"/exchanges/{eid}/orderbook", depth=self.cfg.book_depth, tournamentId=tid)

    def bulk_prices(self, eids, tid):
        """Best bid / best ask for up to BULK_MAX_IDS exchanges in one request: {eid: (bid, ask)}.
        Includes our own orders. Exchanges the API doesn't recognise are simply missing."""
        res = self.get("/exchanges/prices", ids=",".join(eids), tournamentId=tid)
        return {str(p["exchangeId"]): (rnd(p.get("bestBid")), rnd(p.get("bestAsk"))) for p in res.get("data", [])}

    # --- writes (no-ops in dry run) ---------------------------------------------------------
    def cancel_all(self, tid, eid=None):
        """Cancel all our orders in the tournament (or on one exchange). True = confirmed gone."""
        if not self.live:
            return True
        body = {"tournamentId": tid, **({"exchangeId": eid} if eid else {})}
        status, res = self.call("POST", "/orders/cancel-all", body=body, ok=(200, 207, 422))
        if status == 200:
            return True
        log.warning("cancel-all partial: %s", res.get("errors"))
        return not self.open_orders(tid, eid)   # API docs: confirm nothing rests before re-quoting

    def cancel_order(self, order_id):
        """Cancel one order. 404/409 mean it's already gone (filled or cancelled) - that's fine too."""
        if not self.live:
            return True
        try:
            self.call("DELETE", f"/orders/{order_id}", ok=(200,))
        except ApiError as e:
            if e.status in (404, 409):
                return True
            raise
        return True

    def place_batch(self, orders):
        """POST /orders/batch -> one result per order: {"index", "ok", "status", "data"}.

        Batch (not multi-leg) on purpose: each order succeeds or fails on its own, so one bad
        exchange can't roll back the quotes for the other 19 in the request.
        """
        if not self.live:
            return [{"index": k, "ok": True, "status": 200, "data": {}} for k in range(len(orders))]
        body = {"idempotencyKey": str(uuid.uuid4()), "orders": orders}
        _, res = self.call("POST", "/orders/batch", body=body, ok=(200, 207, 422))
        return res.get("results", [])

# =============================================================================================
# REALTIME FEED - the exchange pushes changes to us (Supabase Realtime), on a background thread
# =============================================================================================

class _SocketErrorWatch(logging.Handler):
    """Flags the feed when the `realtime` library logs that its socket closed. Needed because, with its
    auto-reconnect off (we reconnect ourselves), the library only LOGS the closure: it keeps reporting
    is_connected=True and keeps heartbeating into the dead socket. Seen live on 29 Sep: dead for 2 days."""
    def __init__(self, feed):
        super().__init__(logging.ERROR)
        self.feed = feed

    def emit(self, record):
        text = record.getMessage().lower()
        if "connection closed" in text or "terminating connection" in text:
            self.feed.socket_error = True


class RealtimeFeed:
    """Listens to two private channels and tells the main loop what changed:

      tournament:{id}   market_batch  -> which books changed (bookDirty), trades, settled markets
      user:{profile}    account_batch -> our fills, order updates, settlements

    It never trades or makes decisions. The main loop calls take() to collect what happened, and
    `wake` lets it start a cycle straight away. The API says delivery is best-effort (no replay), so
    after connecting, reconnecting, a token refresh or a missed message (revision gap) it asks the
    main loop for a full resync from the REST API.
    """

    def __init__(self, api, tid, cfg):
        self.api, self.tid, self.cfg = api, tid, cfg
        self.lock = threading.Lock()      # the socket thread writes, the main loop reads
        self.wake = threading.Event()     # set when something happened -> main loop cycles early
        self.dirty = set()                # exchanges whose book changed since the last take()
        self.account_changed = False      # fills / settlements since the last take()
        self.settled = False              # a market settled -> the market list should be reloaded
        self.resync = True                # the main loop should do a full check from REST
        self.topics_joined = set()
        self.connected = False
        self.stopping = False
        self.last_revision = {}           # topic -> last revision number accepted
        self.events = 0                   # messages received (shown in status.json)
        self.trade_counts = defaultdict(int)   # exchange -> tournament trades seen (all traders)
        self.socket_error = False         # the library logged that the socket closed (see _SocketErrorWatch)
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), name="realtime", daemon=True)

    # --- used by the main loop -------------------------------------------------------------
    def start(self):
        logging.getLogger("realtime").addHandler(_SocketErrorWatch(self))   # the library logs under "realtime.*"
        self.thread.start()

    def stop(self):
        self.stopping = True

    def healthy(self):
        """Connected and joined to both channels, so pushed updates can be trusted to arrive."""
        return self.connected and len(self.topics_joined) >= 2

    def take(self):
        """Everything reported since the last call: (dirty exchange ids, account changed?, resync?, settled?)."""
        self.wake.clear()                 # cleared first: anything arriving after this wakes us again
        with self.lock:
            out = (self.dirty, self.account_changed, self.resync, self.settled)
            self.dirty, self.account_changed, self.resync, self.settled = set(), False, False, False
        return out

    # --- message handlers (run on the socket thread) ----------------------------------------
    @staticmethod
    def _data(message):
        """A broadcast arrives as {"event", "payload", "type"}; the useful part is the payload."""
        return message.get("payload", message) if isinstance(message, dict) else {}

    def _accept(self, topic, data):
        """Revision check. The same revision twice = a duplicate (ignore it). previousRevision that
        isn't the last one we saw = we missed a message, so ask for a full resync."""
        delivery = data.get("delivery") or {}
        rev, prev = delivery.get("revision"), delivery.get("previousRevision")
        last = self.last_revision.get(topic)
        if rev is not None:
            if last is not None and rev == last:
                return False
            if last is not None and prev is not None and prev != last:
                self._flag_resync(f"missed message(s) on {topic}")
            self.last_revision[topic] = rev
        return True

    def _on_market(self, topic, message):
        data = self._data(message)
        if not self._accept(topic, data):
            return
        with self.lock:
            self.events += 1
            for item in (data.get("bookDirty") or []) + (data.get("trades") or []):
                if item.get("exchangeId") is not None and item.get("tournamentId") in (None, self.tid):
                    self.dirty.add(str(item["exchangeId"]))
            for item in data.get("trades") or []:              # how busy each market is (sizes quotes by it)
                if item.get("exchangeId") is not None and item.get("tournamentId") in (None, self.tid):
                    self.trade_counts[str(item["exchangeId"])] += 1
            if data.get("marketSettled"):
                self.settled = True
        self.wake.set()

    def _on_account(self, topic, message):
        data = self._data(message)
        if not self._accept(topic, data):
            return
        with self.lock:
            self.events += 1
            # Only fills / settlements change our positions. orderUpdates are mostly echoes of our own
            # placements and cancels, which the bot already knows about: re-reading the account for each
            # of those would spend most of the request budget (the 30 s full check catches anything else).
            relevant = any(data.get(k) for k in ("fills", "settlements", "refunds"))
            if relevant:
                self.account_changed = True
            for f in data.get("fills") or []:          # a fill changed that book too
                if f.get("exchangeId") is not None:
                    self.dirty.add(str(f["exchangeId"]))
        if relevant:
            self.wake.set()

    def _on_state(self, topic, state, err):
        name = getattr(state, "name", str(state))
        with self.lock:
            if name == "SUBSCRIBED":
                self.topics_joined.add(topic)
                self.last_revision.pop(topic, None)      # new subscription: revisions start afresh
            else:
                self.topics_joined.discard(topic)
        if name == "SUBSCRIBED":
            self._flag_resync(f"subscribed to {topic.split(':')[0]}")
        else:
            log.warning("realtime: %s -> %s %s", topic.split(":")[0], name, err or "")

    def _flag_resync(self, why):
        with self.lock:
            self.resync = True
        log.info("realtime: %s - full resync next cycle", why)
        self.wake.set()

    # --- connection management (socket thread) ----------------------------------------------
    async def _run(self):
        """Stay connected: on any problem, mark the feed down (the bot polls meanwhile), wait with
        growing back-off, and connect again."""
        backoff = 1.0
        while not self.stopping:
            try:
                await self._session()
                backoff = 1.0
            except Exception as e:
                if not self.stopping:
                    log.warning("realtime feed down (%s) - polling meanwhile, reconnecting in %.0f s", e, backoff)
            with self.lock:
                self.connected = False
                self.topics_joined.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _mint_token(self):
        """A 3-hour realtime login from the REST API (one request, through our rate limiter)."""
        return (await asyncio.to_thread(self.api.call, "POST", "/realtime/token"))[1]

    def _socket_dead(self, client):
        """Is this session's socket gone? client.is_connected alone can't be trusted (see _SocketErrorWatch),
        so also check the library's error log, the socket's close code and whether its listener stopped."""
        if not client.is_connected or self.socket_error:
            return True
        ws = getattr(client, "_ws_connection", None)
        if ws is not None and getattr(ws, "close_code", None) is not None:
            return True
        task = getattr(client, "_listen_task", None)
        return bool(task is not None and task.done())

    async def _session(self):
        from realtime import AsyncRealtimeClient          # imported here: the package is optional
        self.socket_error = False
        tok = await self._mint_token()
        client = AsyncRealtimeClient(f"{tok['supabaseUrl']}/realtime/v1", token=tok["anonKey"],
                                     params={"apikey": tok["anonKey"]}, auto_reconnect=False)
        await client.connect()
        try:
            await client.set_auth(tok["token"])
            topics = [f"tournament:{self.tid}", tok["channels"]["user"]]
            for topic in topics:
                ch = client.channel(topic, {"config": {"private": True}})
                ch.on_broadcast("market_batch", lambda m, t=topic: self._on_market(t, m))
                ch.on_broadcast("account_batch", lambda m, t=topic: self._on_account(t, m))
                await ch.subscribe(lambda state, err, t=topic: self._on_state(t, state, err))
            self.connected = True
            log.info("realtime feed connected - reacting to pushed updates")
            # The session ends (and a new one starts, with a fresh login and a full resync) before the token
            # runs out, and at least every realtime_session_max_seconds anyway.
            started, expires = time.monotonic(), parse_ts(tok.get("expiresAt"))
            deadline = started + self.cfg.realtime_session_max_seconds
            if expires:
                deadline = min(deadline, started + (expires - utcnow()).total_seconds()
                               - self.cfg.realtime_token_refresh_seconds)
            while not self.stopping:
                await asyncio.sleep(1)
                if self._socket_dead(client):
                    raise ConnectionError("socket closed")
                if len(self.topics_joined) < len(topics) and time.monotonic() - started > 20:
                    raise ConnectionError("a channel subscription failed or dropped")
                if time.monotonic() >= deadline:
                    log.info("realtime: renewing the session (fresh login, full resync)")
                    return
        finally:
            self.connected = False
            await client.close()

# =============================================================================================
# PRICING - our resting orders, clean books, fair value, race risk maths
# =============================================================================================

def clamp(p): return min(PMAX, max(PMIN, p))
def floor_tick(p): return round(clamp(math.floor(round(p / TICK, 6)) * TICK), 3)   # round down onto the grid
def ceil_tick(p): return round(clamp(math.ceil(round(p / TICK, 6)) * TICK), 3)     # round up onto the grid


@dataclass
class Resting:
    """One of our orders on the book, described in YES terms."""
    order_id: int
    eid: str
    is_bid: bool              # True = buys YES (or sells NO). False = sells YES (or buys NO)
    price: float              # YES price
    qty: float                # shares still open
    expires: datetime | None


def parse_order(o):
    """API order -> Resting, or None if it isn't really resting any more.

    The engine turns "sell YES @ p" into "buy NO @ 1-p" when we don't hold the YES shares, and
    reports orders in that converted form, so a NO order is flipped back into YES terms here.
    """
    if not o.get("open", True) or o.get("priceLimit") is None:
        return None
    exp = parse_ts(o.get("expirationDate"))
    if exp and exp <= utcnow():
        return None           # past expiry: the engine ignores it even if it still says open
    yes = str(o["side"]).lower() == "yes"
    is_bid = yes == (str(o["action"]).lower() == "buy")
    price = rnd(o["priceLimit"] if yes else 1 - o["priceLimit"])
    return Resting(int(o["id"]), str(o["exchangeId"]), is_bid, price, float(o["quantity"]), exp)


def reserved_cash(raw_orders):
    """Cash our open orders have locked up. A buy of `qty` shares at limit `p` can cost qty*p (p is
    in that order's own side, so a "buy NO @ 0.825" locks 0.825 a share). Selling shares we already
    hold locks no cash; the engine reports those as sells, so they're skipped."""
    total = 0.0
    for o in raw_orders:
        if parse_order(o) and str(o.get("action")).lower() == "buy":
            total += float(o["quantity"]) * float(o["priceLimit"])
    return total


def strip_own(book, mine):
    """Remove our own size from a book so every later decision only sees OTHER traders.
    Without this, once we're top of book the fair value would follow our own quotes and drift."""
    if not mine:
        return book
    own = {"bids": defaultdict(float), "asks": defaultdict(float)}
    for o in mine:
        own["bids" if o.is_bid else "asks"][o.price] += o.qty
    out = dict(book)
    for key in ("bids", "asks"):
        out[key] = [{"price": l["price"], "quantity": q} for l in book.get(key) or []
                    if (q := l["quantity"] - own[key].get(rnd(l["price"]), 0.0)) > 1e-9]
    return out


def predicted_top(book, mine):
    """The (best bid, best ask) the bulk-price endpoint SHOULD show if nothing changed since we
    cached `book` (other traders only): the better of their best price and our own orders.
    If the real top differs, somebody else touched the book and we re-download it."""
    bids = [l["price"] for l in book.get("bids") or []] + [o.price for o in mine if o.is_bid]
    asks = [l["price"] for l in book.get("asks") or []] + [o.price for o in mine if not o.is_bid]
    return (rnd(max(bids)) if bids else None, rnd(min(asks)) if asks else None)


def depth_price(levels, min_depth):
    """Price of the level where the running total of shares first reaches `min_depth`.

    This is the anti-spoofing step. A 1-share bid at a silly price is skipped, because moving
    this number requires someone to put real size at risk. None = not enough size to trust.
    """
    total = 0.0
    for l in levels:
        total += l["quantity"]
        if total >= min_depth:
            return l["price"]
    return None


def fair_value(book, cfg=CFG):
    """Mid-point of the depth-filtered best bid and ask, or None if the book is too thin or wide.
    (This replaced the size-weighted 'microprice', which one tiny order could move by 3.5c.)"""
    if not book:
        return None
    bid = depth_price(book.get("bids") or [], cfg.fv_min_depth)
    ask = depth_price(book.get("asks") or [], cfg.fv_min_depth)
    if bid is None or ask is None or ask <= bid or ask - bid > cfg.max_spread_for_fv:
        return None
    return (bid + ask) / 2


def normalise(fvs):
    """Outcomes that exclude each other (one party wins a race) must add up to 1; scale them so
    they do. Left alone if any member has no price, since we can't scale what we can't see."""
    if not fvs or any(v is None for v in fvs.values()):
        return fvs
    total = sum(fvs.values())
    return {k: v / total for k, v in fvs.items()} if total > 0 else fvs


def worst_case_loss(legs):
    """How much the marked-to-market value of one race would fall in its worst outcome.

    legs = [(net YES shares, fair value), ...] for exchanges that exclude each other.
    Assumes exactly one leg wins (a race: one party wins). A lone market can win or lose.
    Example: long 500 Rep @0.14 AND long 500 Dem @0.86 -> worth 500 now, pays 500 either way -> 0.
    """
    value = sum(x * fv if x > 0 else -x * (1 - fv) for x, fv in legs)      # NO shares are worth 1-fv each
    if len(legs) == 1:
        scenarios = [[True], [False]]
    else:
        scenarios = [[j == i for j in range(len(legs))] for i in range(len(legs))]

    def payout(wins):   # YES shares pay 1 if their leg wins; NO shares pay 1 if it loses
        return sum((x if w else 0.0) if x > 0 else (0.0 if w else -x) for (x, _), w in zip(legs, wins))

    return max(0.0, value - min(payout(s) for s in scenarios))

# =============================================================================================
# QUOTING - what we want resting on one exchange
# =============================================================================================

@dataclass(frozen=True)
class Quote:
    bid: float | None = None
    bid_size: int = 0
    ask: float | None = None
    ask_size: int = 0
    # The most aggressive price an order ALREADY resting may keep (still min_edge from our reservation
    # price, not crossing anyone). Used to leave an order alone when the target moved by a tick.
    # None = no tolerance (e.g. election-night exits). Not part of comparing two quotes.
    bid_limit: float | None = field(default=None, compare=False)
    ask_limit: float | None = field(default=None, compare=False)


NO_QUOTE = Quote()

# Account value assumed when none is given (examples, tests): the tournament's starting balance.
DEFAULT_BANKROLL = 100_000.0


def kelly_position(p, price, bankroll, cfg=CFG, yes=True):
    """The largest position (in shares) fractional Kelly (kelly_fraction) allows on one side of a market.

    p        what we believe the true YES probability is (Polymarket)
    price    our YES price for this side: the bid when buying YES, the ask when selling YES
    yes      True = buying YES at `price`; False = selling YES there, i.e. buying NO at 1 - price

    A contract costing `cost` that pays 1 with probability `win` has the Kelly stake
        f = (win - cost) / (1 - cost) = edge / (1 - cost)       (as a fraction of the bankroll)
    e.g. Polymarket 0.20, buying YES at 0.14: f = 0.06 / 0.86 = 7%; quarter Kelly = 1.7% of the account (capped at 2%).
    The stake is capped at kelly_max_market_frac of the account, then turned into shares (stake / cost).
    No edge (or a negative one) -> just the small kelly_no_edge_frac allowance.
    """
    allowance = int(cfg.kelly_no_edge_frac * bankroll)
    edge, cost = (p - price, price) if yes else (price - p, 1 - price)
    if edge <= 0 or cost <= 0:
        return allowance
    stake = min(cfg.kelly_fraction * edge / (1 - cost), cfg.kelly_max_market_frac) * bankroll
    return max(allowance, int(stake / cost))


def quote_lock(fv, cfg=CFG):
    """Cash one share of our quotes on a market locks. Two-sided: a bid at fv - x locks fv - x and an ask
    at fv + x (really a NO buy at 1 - fv - x) locks 1 - fv - x: together 1 - 2x, at most 1 - 2 * min_edge.
    Near 0 or 1 the tail guard leaves one cheap side: a bid near 0 locks ~fv, an ask near 1 locks ~1 - fv."""
    if fv < cfg.tail_low:
        return max(fv, 0.01)
    if fv > cfg.tail_high:
        return max(1 - fv, 0.01)
    return 1 - 2 * cfg.min_edge


def plan_sizes(activity, headline, bankroll, cfg=CFG, prev=None, lock=None, prev_bankroll=None):
    """Shares per quote for each exchange: {eid: shares}.

    activity   {eid: how busy the market is} for the markets being quoted (any non-negative scale)
    headline   eids of the party-control markets: they get headline_size_frac, the biggest by far
    prev       the current plan: a market keeps its size unless the new one differs by > size_plan_step
    prev_bankroll   the account value prev was planned for: prev is rescaled to today's account first,
                    so sizes always follow the account, and only activity shifts are held back
    lock       {eid: cash one share of quotes there locks} (see quote_lock); missing = 1 (the most it can be)

    Everything else shares what's left of the capital budget (quote_capital_frac of the account) in
    proportion to the SQUARE ROOT of activity, so busy markets get much more but no single one swallows
    everything, clamped to [size_min_frac, size_max_frac]. If the headline markets alone would leave
    the rest less than their minimum, they're scaled down to fit. Sizes are rounded down to 50 shares.
    """
    lock = lock or {}
    cost = {e: lock.get(e, 1.0) for e in activity}
    budget = cfg.quote_capital_frac * bankroll
    lo, hi = cfg.size_min_frac * bankroll, cfg.size_max_frac * bankroll
    others = [e for e in activity if e not in headline]
    plan = {e: cfg.headline_size_frac * bankroll for e in activity if e in headline}
    floor = sum(lo * cost[e] for e in others)
    left = budget - sum(s * cost[e] for e, s in plan.items())
    if plan and left < floor:                              # headline markets would starve the rest
        scale = max(0.0, budget - floor) / sum(s * cost[e] for e, s in plan.items())
        plan = {e: s * scale for e, s in plan.items()}
        left = budget - sum(s * cost[e] for e, s in plan.items())
    weight = {e: math.sqrt(max(float(activity.get(e) or 0.0), 0.0)) for e in others}
    if others and not any(weight.values()):
        weight = dict.fromkeys(others, 1.0)                # no activity data at all: share equally

    def total(k):
        return sum(min(hi, max(lo, k * weight[e])) * cost[e] for e in others)

    k_lo, k_hi = 0.0, 1.0                                  # find k so that the others use exactly what's left
    while total(k_hi) < left and k_hi < 1e15:
        k_hi *= 2
    for _ in range(80):
        mid = (k_lo + k_hi) / 2
        k_lo, k_hi = (mid, k_hi) if total(mid) <= left else (k_lo, mid)
    for e in others:
        plan[e] = min(hi, max(lo, k_lo * weight[e]))
    grow = bankroll / prev_bankroll if prev_bankroll else 1.0
    out = {}
    for e, size in plan.items():
        size = int(size // 50 * 50) if size >= 50 else int(size)
        old = (prev or {}).get(e)
        if old:
            old = int(old * grow // 50 * 50) if old * grow >= 50 else int(old * grow)   # the account moved: follow it
            if old and abs(size / old - 1) <= cfg.size_plan_step:
                size = old                                 # small activity shift: keep the size (and queue spots)
        out[e] = size
    return out


def compute_quote(fv, inv, eff_inv, best_bid, best_ask, cfg=CFG, reduce_only=False, no_bid=False, no_ask=False,
                  bid_cap=None, ask_cap=None, kelly_p=None, bankroll=None, shift=0.0, order_size=None,
                  position_limit=None):
    """
    fv         fair YES probability
    inv        our net YES shares on THIS exchange (negative = net NO); drives the hard position limit
    eff_inv    inventory after netting the other parties in the same race; drives skew and reduce-only
    best_bid   best bid from OTHER traders (our own orders already removed), or None
    best_ask   best ask from OTHER traders, or None
    reduce_only        only trade toward flat (market about to close, or worst-case-loss cap hit)
    no_bid / no_ask    block one side (national-swing cap, reference-price guard)
    bid_cap / ask_cap  max shares on one side, None = no cap (tail guard)
    kelly_p            a liquid Polymarket probability: position limits then come from Kelly sizing
                       instead of max_position_frac
    bankroll           account value; every size is a fraction of it (None = DEFAULT_BANKROLL)
    shift              extra amount to lower the reservation price by (national-swing shading; see decide)
    order_size         shares per quote for this market (from the activity-based size plan); None = order_size_frac
    position_limit     flat limit on |net shares| here instead of Kelly / max_position_frac (party-control markets)
    """
    bankroll = bankroll or DEFAULT_BANKROLL
    max_order_cash = cfg.max_order_cash_frac * bankroll
    if order_size is None:
        order_size = cfg.order_size_frac * bankroll
    else:
        max_order_cash = max(max_order_cash, order_size)   # a planned size has already been capital-checked
    # 1. Reservation price = fair value shifted against our inventory. Long -> lower r -> we bid
    #    less eagerly and offer more eagerly, which pushes the position back toward flat.
    r = fv - cfg.skew_per_share * eff_inv - shift

    # 2. Allowed band for each side: at least min_edge, at most max_half_spread away from r.
    bid_lo, bid_hi = floor_tick(r - cfg.max_half_spread), floor_tick(r - cfg.min_edge)
    ask_lo, ask_hi = ceil_tick(r + cfg.min_edge), ceil_tick(r + cfg.max_half_spread)

    # 3. Penny: one tick better than the best other trader, so we're first in the queue while
    #    keeping the widest spread possible. Then clamp into the band. That clamp is what stops a
    #    penny war with another bot from pushing us below min_edge. No other quote -> band edge.
    bid = floor_tick(best_bid + TICK) if best_bid is not None else bid_lo
    ask = ceil_tick(best_ask - TICK) if best_ask is not None else ask_hi
    bid = min(max(bid, bid_lo), bid_hi)
    ask = max(min(ask, ask_hi), ask_lo)

    # 4. Never cross another trader's order (that would trade instantly, as a taker).
    if best_ask is not None:
        bid = min(bid, floor_tick(best_ask - TICK))
    if best_bid is not None:
        ask = max(ask, ceil_tick(best_bid + TICK))

    # 5. Size: shrink toward the position limit on each side, and cap the cash tied up per order.
    #    Limits: Kelly sizing when we have a liquid Polymarket price, else max_position_frac of the account.
    long_limit = short_limit = cfg.max_position_frac * bankroll
    if position_limit is not None:
        long_limit = short_limit = position_limit
    elif kelly_p is not None:
        long_limit = kelly_position(kelly_p, bid, bankroll, cfg, yes=True)     # most YES we'd hold
        short_limit = kelly_position(kelly_p, ask, bankroll, cfg, yes=False)   # most NO we'd hold
    bid_size = min(order_size, long_limit - inv)
    ask_size = min(order_size, short_limit + inv)
    bid_size = min(bid_size, max_order_cash / bid)          # buying YES costs `bid` a share
    ask_size = min(ask_size, max_order_cash / (1 - ask))    # selling YES = buying NO at 1-ask

    # 6. Risk overrides.
    if reduce_only:
        bid_size = min(bid_size, -eff_inv)     # only buy back a short
        ask_size = min(ask_size, eff_inv)      # only sell down a long
    if no_bid:
        bid_size = 0
    if no_ask:
        ask_size = 0
    if bid_cap is not None:
        bid_size = min(bid_size, bid_cap)
    if ask_cap is not None:
        ask_size = min(ask_size, ask_cap)
    bid_size, ask_size = max(0, int(bid_size)), max(0, int(ask_size))   # the API only takes whole shares

    if bid >= ask:
        return NO_QUOTE
    # How far a resting order may sit from these prices and still be kept: never closer than min_edge
    # to r, never crossing the best other order.
    bid_limit = min(bid_hi, floor_tick(best_ask - TICK)) if best_ask is not None else bid_hi
    ask_limit = max(ask_lo, ceil_tick(best_bid + TICK)) if best_bid is not None else ask_lo
    return Quote(bid if bid_size else None, bid_size, ask if ask_size else None, ask_size, bid_limit, ask_limit)


def exit_quote(fv, inv, best_bid, best_ask, cfg=CFG, bankroll=None, max_size=None):
    """Election-night exit: get this market flat, trading against other orders if needed.

    Long -> sell at the best other bid (an immediate trade), but never below fv - exit_max_slippage;
    if the best bid is worse than that, the order rests at that floor instead. Short -> the mirror
    image. Only the side that reduces the position is quoted. Sized to the whole position, capped at
    max_order_cash_frac of the account per order (the rest goes on later cycles).
    """
    bankroll = bankroll or DEFAULT_BANKROLL
    if inv >= 1:
        price = max(best_bid if best_bid is not None else 0.0, fv - cfg.exit_max_slippage)
        price = ceil_tick(price)
        size = int(min(inv, max(cfg.max_order_cash_frac * bankroll / max(1 - price, TICK), max_size or 0)))
        return Quote(ask=price, ask_size=size) if size >= 1 else NO_QUOTE
    if inv <= -1:
        price = min(best_ask if best_ask is not None else 1.0, fv + cfg.exit_max_slippage)
        price = floor_tick(price)
        size = int(min(-inv, max(cfg.max_order_cash_frac * bankroll / max(price, TICK), max_size or 0)))
        return Quote(bid=price, bid_size=size) if size >= 1 else NO_QUOTE
    return NO_QUOTE


def side_needs_change(resting, price, size, cfg, now, limit=None, is_bid=True):
    """True if what's resting on ONE side of an exchange doesn't match what we want there.
    Leaving a good order alone keeps its place in the queue, which is worth money. So an order a tick
    (reprice_tolerance_ticks) off the target is kept, as long as it's inside `limit` (see Quote)."""
    if price is None:
        return bool(resting)                                    # want nothing: anything there must go
    if len(resting) != 1:
        return True                                             # missing, or duplicates
    o = resting[0]
    if abs(o.price - price) > 1e-9:
        close = abs(o.price - price) <= cfg.reprice_tolerance_ticks * TICK + 1e-9
        safe = limit is not None and (o.price <= limit + 1e-9 if is_bid else o.price >= limit - 1e-9)
        if not (close and safe):
            return True                                         # wrong price
    if not (size * cfg.keep_fraction <= o.qty <= size):
        return True                                             # mostly filled, or bigger than we now want
    if o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry:
        return True                                             # about to expire
    return False

# =============================================================================================
# MEASUREMENT - fills.csv and the `report` command
# =============================================================================================

class FillLogger:
    """Appends every new fill to a CSV, tagged with which of our quotes it hit and the fair value
    at the moment we placed that quote, so edge and adverse selection can be measured later."""
    COLUMNS = ["fill_id", "filled_at", "exchange_id", "order_id", "our_side", "qty",
               "fill_price", "quote_price", "fv_at_quote", "fv_after"]

    def __init__(self, path):
        self.path, self.seen = path, set()
        if os.path.exists(path):
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if reader.fieldnames != self.COLUMNS:          # file from an older version of the bot
                if rows:
                    os.replace(path, path + ".old")        # keep old data, start a fresh file
                else:
                    os.remove(path)
            else:
                self.seen = {r["fill_id"] for r in rows}
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(self.COLUMNS)

    def record(self, fills, order_meta, fvs):
        with open(self.path, "a", newline="") as fh:
            w = csv.writer(fh)
            for f in reversed(fills):                      # API gives newest first; write oldest first
                meta = order_meta.get(f.get("orderId")) or {}
                qty = abs(float(f.get("quantity") or 0))   # API signs quantity negative for NO-side fills
                w.writerow([f["id"], f.get("filledAt"), f.get("exchangeId"), f.get("orderId"),
                            meta.get("our_side", "?"), qty, f.get("price"), meta.get("price", ""),
                            meta.get("fv", ""), fvs.get(str(f.get("exchangeId")), "")])
                self.seen.add(str(f["id"]))
                log.info("FILL ex %s  our %s x%.0f @ %s  (fv when quoted %s)", f.get("exchangeId"),
                         meta.get("our_side", "?"), qty, meta.get("price", f.get("price")), meta.get("fv", "?"))


def read_fills(path):
    """All rows of fills.csv as dicts ([] if there's no file yet)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def fill_stats(rows):
    """Two numbers tell you whether the market making is working:

      edge     how far on the right side of fair value each fill was, using the fair value at the
               moment we QUOTED. Positive = we earned spread.
      markout  how fair value moved straight after the fill, measured in our direction. Negative =
               the people trading with us knew something (adverse selection).

    Profit per share is roughly edge + markout. Our fills are as a maker (or an arbitrage at a
    price we chose), so they happen at our own order price; that's the price used here.
    """
    q_tot = edge_tot = mk_tot = mk_q = 0.0
    unknown = 0
    for r in rows:
        if r["our_side"] not in ("bid", "ask") or not r["fv_at_quote"] or not r["quote_price"]:
            unknown += 1
            continue
        q, p, fvq = float(r["qty"]), float(r["quote_price"]), float(r["fv_at_quote"])
        d = 1 if r["our_side"] == "bid" else -1           # bid = we bought YES, ask = we sold YES
        q_tot += q
        edge_tot += d * (fvq - p) * q
        if r["fv_after"]:
            mk_tot += d * (float(r["fv_after"]) - fvq) * q
            mk_q += q
    return {"fills": len(rows), "unmatched": unknown, "shares": q_tot, "edge_total": edge_tot,
            "edge_c": 100 * edge_tot / max(q_tot, 1), "markout_c": 100 * mk_tot / max(mk_q, 1)}


def build_summary(api, fills_path, initial_balance, value=None, value_prev=None, arbs=None, health=None, takes=None,
                  hours=24, status=None):
    """The phone summary as (title, message). Reads account value (unless given), rank and Smart Score
    from the API, and the last `hours` of fills from fills.csv. value_prev = account value at the previous
    summary. status = the bot's status line, shown first. Missing pieces show as "?" (e.g. no rank before
    our first trade) instead of failing."""
    def safe(fn, default=None):
        try:
            return fn()
        except (ApiError, KeyError, TypeError, ValueError):
            return default

    if value is None:
        value = safe(lambda: float(api.pnl()["totalAccountValue"]))
    lb = safe(api.leaderboard, {}) or {}
    scores = safe(api.smart_score, []) or []
    score = next((s for s in scores if s.get("marketType") == "global"), scores[0] if scores else None)
    since = utcnow() - timedelta(hours=hours)
    day = fill_stats([r for r in read_fills(fills_path) if (parse_ts(r.get("filled_at")) or since) >= since])

    if value is None:
        title, account = "mm_bot: account ?", "Account: unavailable"
    else:
        total = value - initial_balance
        title = f"mm_bot: {total:+,.0f} ({100 * total / initial_balance:+.1f}%)"
        day_change = "" if value_prev is None else f", {value - value_prev:+,.0f} in {hours:g}h"
        account = f"Account {value:,.0f} ({total:+,.0f} total{day_change})"
    rank = (f"Rank {lb['myRank']} of {lb.get('total', '?')}" if lb.get("myRank") else "Rank: not ranked yet")
    smart = (f"Smart Score {score.get('smartScoreDecayed', 0):.1f} (rank {score.get('rank', '?')} of "
             f"{score.get('totalTraders', '?')}{', ELITE' if score.get('isElite') else ''})"
             if score else "Smart Score: not scored yet")
    lines = ([status] if status else []) + [account, f"{rank} | {smart}",
             f"Last {hours:g}h: {day['fills']} fills, {day['shares']:,.0f} shares, edge {day['edge_c']:+.2f}c, "
             f"markout {day['markout_c']:+.2f}c" + (f", {arbs} arbitrages" if arbs is not None else "")
             + (f", {takes} takes" if takes is not None else "")]
    if health:
        lines.append(f"Now: {health.get('orders_resting', '?')} orders resting, worst-case loss "
                     f"{health.get('worst_case_loss', 0):,.0f}, realtime {health.get('realtime', '?')}, "
                     f"rate limits {health.get('rate_limited_total', 0)}")
    return title, "\n".join(lines)


def report(path):
    """The `report` command: print fill_stats for every fill so far."""
    rows = read_fills(path)
    if not rows:
        return print("no fills yet")
    s = fill_stats(rows)
    print(f"{s['fills']} fills ({s['unmatched']} not matched to a bot quote), {s['shares']:.0f} shares")
    print(f"  edge    {s['edge_c']:+.2f} c/share   total {s['edge_total']:+.0f} SUSQies")
    print(f"  markout {s['markout_c']:+.2f} c/share   (fair value ~1 cycle after the fill)")

# =============================================================================================
# BOT - state, the cycle, risk, order management, main loop
# =============================================================================================

@dataclass
class Ex:
    """Everything the bot remembers about one exchange (one tradable YES contract)."""
    eid: str
    market_id: str
    title: str
    label: str                            # short name for logs, e.g. "Rep Ohio Senate"
    group: str                            # race name; exchanges in one group exclude each other
    party: str | None
    close: datetime | None
    book: dict | None = None              # latest order book with OUR orders removed
    book_time: float = 0.0                # time.monotonic() when it was downloaded
    verified: float = 0.0                 # when it was last confirmed current: downloaded, or a bulk check
                                          #   showed the same best prices. Staleness is measured from this
    last_fv: float | None = None
    cooldown_until: float = 0.0           # jump guard
    pending_until: float = 0.0            # placement outcome unknown: don't place again yet
    pause_until: float = 0.0              # order was rejected: back off
    writes: int = 0                       # our writes (cancels / batches) touching it still in flight
    cancelling: bool = False              # ...one of them is a cancel
    inv: float = 0.0                      # for logging / recording
    eff: float = 0.0                      # for logging
    ref: float | None = None              # outside reference price, if any (for logging / recording)
    quote: Quote = NO_QUOTE               # what we decided this cycle (for recording)
    take_dir: int = 0                     # +1 = Polymarket above the best ask, -1 = below the best bid, 0 = neither
    take_since: float = 0.0               # since when every Polymarket reading has shown that same gap
    take_until: float = 0.0               # after taking here, leave it alone until this time


def busy(ex, now_m):
    """Don't place on this exchange: an earlier placement's outcome is unknown, or a write is still running."""
    return now_m < ex.pending_until or ex.writes > 0


class Change:
    """What one exchange needs this cycle: orders to cancel first (all of them with whole=True), then new ones.
    key orders the work: pulls first, then party-control markets, then the biggest quotes."""
    __slots__ = ("ex", "doomed", "whole", "new", "key")

    def __init__(self, ex, doomed, whole, new, key):
        self.ex, self.doomed, self.whole, self.new, self.key = ex, doomed, whole, new, key


class Write:
    """One order write running on a writer thread: kind "cancel" (eid, orders, whole) or "batch" (chunk)."""
    __slots__ = ("kind", "eids", "payload", "future", "sent", "change", "payload_ok")

    def __init__(self, kind, eids, payload, sent, change=None):
        self.kind, self.eids, self.payload, self.sent, self.change = kind, eids, payload, sent, change
        self.future, self.payload_ok = None, False     # payload_ok: a cancel confirmed


def fmt(price, size):
    return f"{price:.3f} x{size:<4d}" if price is not None else "   -       "


class Bot:
    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.running = True
        self.t = api.tournament()
        self.check_clock()
        self.tid = self.t["id"]
        self.initial_balance = float(self.t.get("initialBalance") or 0)
        self.fills = FillLogger(bot_path(cfg.fills_csv))
        self.ex = {}                      # eid -> Ex
        self.groups = {}                  # race -> [eid, ...]
        self.sim = {}                     # DRY RUN ONLY: pretend resting orders, so dry runs behave like live
        self.next_sim_id = -1
        self.order_meta = self.load_order_notes()   # orderId -> what that order was for (attributes fills)
        self.notes_dirty = False
        self.exit_code = 0                # what the process exits with (see EXIT_* codes)
        self.health = {}                  # latest cycle summary, written to status.json
        self.kill_breaches = 0
        self.reserved_mode = None if cfg.reserved_cash_mode == "auto" else cfg.reserved_cash_mode
        self.calib_prev = None            # (account value, locked cash, positions) from the previous cycle
        self.calib_votes = []             # recent "add"/"ignore" verdicts while auto-detecting
        self.failed_cycles = 0
        self.pulled_after_errors = False
        self.last_reload = 0.0
        self.last_equity = None           # latest account value (read every slow_poll_seconds)
        self.size_bank = self.initial_balance or DEFAULT_BANKROLL   # account value sizes are based on (see bankroll)
        self.size_plan = {}               # eid -> shares per quote (see update_size_plan)
        self.size_plan_time = -1e9
        self.size_plan_bank = None        # account value the plan was made for
        self.last_slow_poll = -1e9        # when P&L and fills were last read
        self.last_full_check = -1e9       # when the last full check (bulk prices, positions, orders) ran
        self.feed = None                  # RealtimeFeed, started by run() (None = polling only)
        self.wake = threading.Event()     # set by the Polymarket thread: start the next cycle now
        self.cached_pos = None            # latest positions / open orders, reused on event cycles
        self.cached_orders = None         #   that don't need to re-read them
        self.orders_stale = True          # unsure what's resting (or positions changed) -> re-read both next cycle
        self.pending_dirty = set()        # books the feed reported but we haven't downloaded yet
        self.my_orders = {}               # LIVE: orderId -> Resting, our own record of our resting orders (see sync_orders)
        self.recent_orders = {}           # orderId -> (Resting, time placed): placed moments ago, maybe not listed yet
        self.recent_cancels = {}          # orderId -> time cancelled: cancelled moments ago, maybe still listed
        self.placed_qty = {}              # orderId -> shares we placed, and...
        self.filled_qty = defaultdict(float)   # ...shares filled so far (each fill counted once, from fills)
        self.ref_rejected = set()         # Polymarket keys currently ignored as implausible (alerted once)
        # Threads for sending several HTTP requests at once (downloads mostly wait on the network).
        self.pool = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_requests), thread_name_prefix="http")
        # ...and for order writes, so a slow one never holds up the others or the cycle (see send_changes).
        self.writer = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_writes), thread_name_prefix="write")
        self.writes = []                  # Write jobs in flight; only the main thread reads or changes this list
        self.write_done = threading.Event()   # set when one finishes (wakes the main loop to apply it)
        self.refs = self.load_reference_prices()
        self.ref_version_seen = 0         # last Polymarket reading the jump guard has looked at
        self.last_tops = {}               # latest bulk best bid/ask per exchange (for recording)
        self.arb_cooldown = {}            # race -> time.monotonic() until which we leave it alone
        self.arbs_total = 0               # arbitrages / takes since start (summaries report the change)
        self.takes_total = 0
        self.take_version_seen = 0        # last Polymarket reading the take logic has counted
        self.db = self.open_recorder()
        self.last_record = -1e9
        self.last_summary_slot = None     # (date, hour) of the last phone summary
        self.value_at_last_summary = None
        self.counts_at_last_summary = {"arbs": 0, "takes": 0, "errors": 0, "rate_limits": 0}
        self.errors_total = 0             # failed cycles since start (summaries report new ones)
        self.phase = "starting"           # what the bot is doing, for the status line
        self.selftest_passed = False
        self.selftest_future = None       # the self-test running in the background (see selftest_tick)
        self.selftest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selftest")
        self.selftest_eid = None
        self.selftest_next = 0.0          # when to try again after the exchange answered busy
        self.selftest_started = None
        self.selftest_alerted = False
        self.selftest_hold = 0.0          # the test exchange's pending_until before the test
        self.selftest_gen = 0             # cancel_gen when the test started
        self.selftest_unlisted = 0        # tests in a row whose orders were accepted but never listed
        self.selftest_errors = 0          # tests in a row that crashed (a bug in the test)
        self.cancel_gen = 0               # +1 on every cancel-everything (the self-test checks it)
        self.load_markets()

    def check_clock(self):
        """Order expiry and the timing of the open use this computer's clock. Compare it with the exchange's
        (the Date header of the last response, 1 s resolution) and alert if they're far apart."""
        hdr, received = getattr(self.api, "last_date", (None, None))
        try:
            server = email.utils.parsedate_to_datetime(hdr)
        except (TypeError, ValueError, IndexError):
            return None
        skew = (received - server).total_seconds() - 0.5          # the header is truncated to the second
        if abs(skew) > self.cfg.max_clock_skew_seconds:
            alert(f"this computer's clock is {skew:+.0f} s off the exchange's - turn on automatic time sync "
                  f"(order expiry and the timing of the open depend on it)")
        return skew

    def on_reference_prices(self, moves):
        """Called by the Polymarket thread after every reading. Wakes the main loop so quotes follow
        within ~0.5 s, instead of at the next realtime event or after loop_seconds (up to 10 s away).
        With a healthy feed, cycles that download nothing cost no requests, so any price change wakes
        it. Without one, every cycle is a full check (~7 requests), so only a jump does."""
        biggest = max(moves.values(), default=0.0)
        if biggest >= self.cfg.ref_jump_threshold or (biggest > 0 and self.feed and self.feed.healthy()):
            self.wake.set()

    def load_reference_prices(self):
        """Outside reference prices (ref_prices.py), only if a mapping file exists next to the bot."""
        path = bot_path(self.cfg.ref_map_file) if self.cfg.ref_map_file else None
        if not path or not os.path.exists(path):
            return None
        try:
            from ref_prices import REF, ReferencePrices
        except ImportError:
            log.warning("%s exists but ref_prices.py is missing - reference prices off", path)
            return None
        refs = ReferencePrices(path, replace(REF, refresh_seconds=self.cfg.ref_refresh_seconds))
        refs.on_refresh = self.on_reference_prices
        log.info("reference prices on: %d contracts mapped in %s", len(refs.mapping), path)
        return refs

    def in_parallel(self, fn, items):
        """Run fn(item) for every item on up to parallel_requests threads.
        Returns {item: result, or the exception it raised}: one failure never stops the others."""
        futures = {item: self.pool.submit(fn, item) for item in items}
        out = {}
        for item, f in futures.items():
            try:
                out[item] = f.result()
            except Exception as e:
                out[item] = e
        return out

    # ------------------------------------------------------------------------------ markets
    def load_markets(self):
        """(Re)read the tournament's open markets. New exchanges are added. Exchanges whose market
        has closed are cancelled and forgotten, so their failing requests can't clog every cycle."""
        only = {x.strip() for x in self.cfg.only_exchanges.split(",") if x.strip()}
        t_end = parse_ts(self.t.get("endDate"))
        seen = set()
        for m in self.api.markets():
            if m.get("isComposite"):
                continue
            close = min([d for d in (parse_ts(m.get("settlementDate")), t_end) if d], default=None)
            exchanges = m.get("exchanges", [])
            x = RACE_TITLE.match(m.get("title", ""))
            for e in exchanges:
                eid = str(e["id"])
                if only and eid not in only:
                    continue
                seen.add(eid)
                if eid in self.ex:
                    continue
                if x and len(exchanges) == 1:        # a party's YES in a race
                    party, group = x.group(1), x.group(2)
                    label = f"{party[:3]} {group}"
                else:                                # anything else: its own market is its group
                    party, group = None, f"market:{m['id']}"
                    label = f"{m.get('title', '')[:24]} {e.get('option') or ''}"
                self.ex[eid] = Ex(eid, str(m["id"]), m.get("title", ""), label, group, party, close)
        for eid in [e for e in self.ex if e not in seen]:
            log.info("market closed/removed: %s - cancelling and dropping it", self.ex[eid].label)
            self.cancel(eid, self.sim_orders(eid), whole_exchange=True, quiet=True)
            del self.ex[eid]
        self.groups = defaultdict(list)
        for ex in self.ex.values():
            self.groups[ex.group].append(ex.eid)
        self.last_reload = time.monotonic()
        log.info("Tracking %d exchanges in %d races", len(self.ex), len(self.groups))

    def hours_to_close(self, ex):
        return (ex.close - utcnow()).total_seconds() / 3600 if ex.close else float("inf")

    # ------------------------------------------------------------------------------ one cycle
    def cycle(self):
        cfg = self.cfg
        now, now_m = utcnow(), time.monotonic()

        # 0. What does this cycle need to read? ----------------------------------------------------
        # With a healthy realtime feed, most cycles are triggered by a pushed event and only read what
        # changed. A FULL check (bulk prices, positions, orders) runs every realtime_heartbeat_seconds,
        # after a resync request, and on every cycle when there is no feed (plain polling).
        realtime = bool(self.feed and self.feed.healthy())
        dirty, fill_event, resync, settled = self.feed.take() if self.feed else (set(), False, False, False)
        full = not realtime or resync or now_m - self.last_full_check >= cfg.realtime_heartbeat_seconds
        if full:
            self.last_full_check = now_m
        if settled:
            self.last_reload = -1e9               # a market settled: reload the market list next cycle
        self.pending_dirty |= dirty & set(self.ex)
        # Account value (kill switch) changes slowly: read it every slow_poll_seconds, always on a full
        # check, so it's read together with our open orders (the locked-cash correction compares the two).
        slow_poll = full and now_m - self.last_slow_poll >= cfg.slow_poll_seconds
        if slow_poll:
            self.last_slow_poll = now_m
        # Positions change only through fills. Our open orders change mostly through our own placements
        # and cancels, which we track ourselves (self.my_orders), so the list is only re-read on full
        # checks, or when we're unsure of it (orders_stale: an unclear placement, a take, an arbitrage).
        read_orders = full or self.orders_stale or self.cached_orders is None
        read_positions = read_orders or fill_event or self.cached_pos is None
        read_fills = fill_event or slow_poll

        # 0b. Results of order writes that finished since the last cycle (slow ones run in the background).
        self.harvest_writes()

        # 1. Account state: independent reads, sent at the same time ------------------------------
        f_pos = self.pool.submit(self.api.positions) if read_positions else None
        f_orders = self.pool.submit(self.api.open_orders, self.tid) if read_orders else None
        f_pnl = self.pool.submit(self.api.pnl) if slow_poll else None
        if read_positions:                        # if a read fails, the cycle fails
            self.cached_pos = f_pos.result()
        if read_orders:
            self.cached_orders = f_orders.result()
            self.orders_stale = False
        pos, raw_orders = self.cached_pos, self.cached_orders
        # position.quantity is already signed by the API: + YES shares, - NO shares.
        inv = {str(p["exchangeId"]): float(p.get("quantity") or 0)
               for p in pos.get("positions", []) if not p.get("settled")}
        reserved = reserved_cash(raw_orders)
        if f_pnl is not None:
            self.last_equity = self.checked_account_value(self.account_value(pos, f_pnl), reserved, inv)
            if self.kill_switch(self.last_equity):
                return
        equity = self.last_equity

        # Our REAL resting orders (used to clean the books), and the orders we compare against
        # when reconciling: the real ones when live, the simulated ones in a dry run.
        if self.api.live:
            if read_orders:
                self.sync_orders(raw_orders, now_m)
            mine_real = self.orders_by_eid(now)
        else:
            mine_real = defaultdict(list)
            for r in filter(None, map(parse_order, raw_orders)):
                mine_real[r.eid].append(r)
        resting = mine_real if self.api.live else self.sim_by_eid()

        # 2. Order books (only the ones that changed) ------------------------------------------
        if full or len(self.pending_dirty) > cfg.bulk_check_over:
            self.refresh_books(mine_real, now_m)  # bulk prices for everything, then only books that moved
        else:
            self.download_books(self.books_to_fetch([]), mine_real)   # just the few the feed reported
            self.reverify_books(mine_real, now_m)  # books unconfirmed for a while: cheap bulk check first

        # 3. Fair values ---------------------------------------------------------------------------
        # book_fvs: the tournament book's own price (parties in a race scaled to sum to 1).
        # fvs:      what we quote around - book_fvs leaned toward Polymarket by ref_weight.
        # The guard compares Polymarket with book_fvs: comparing it with the already-leaned fvs would
        # shrink every gap to (1 - ref_weight) of its real size and make the 5c guard a ~17c guard.
        book_fvs = {eid: fair_value(ex.book, cfg) if now_m - ex.verified < cfg.book_stale else None
                    for eid, ex in self.ex.items()}
        for members in self.groups.values():
            if len(members) > 1:
                book_fvs.update(normalise({e: book_fvs[e] for e in members}))
        refs, liquid = self.reference_prices(book_fvs)
        self.reference_jump_guard(now_m)
        fvs = dict(book_fvs)
        if cfg.ref_weight > 0 and refs:
            for eid, r in refs.items():
                if fvs.get(eid) is not None and eid in liquid:      # only lean on liquid Polymarket prices
                    fvs[eid] = (1 - cfg.ref_weight) * fvs[eid] + cfg.ref_weight * r
            for members in self.groups.values():
                if len(members) > 1:
                    fvs.update(normalise({e: fvs[e] for e in members}))

        # 3b. How many shares to quote in each market (every size_plan_seconds, or on an account step) ----
        if cfg.size_by_activity:
            self.update_size_plan(now_m, fvs)

        # 4. Fills (every slow_poll_seconds, and straight after a fill) --------------------------------
        if read_fills:
            self.log_fills(fvs)

        # 5. Guaranteed arbitrage inside races (takes liquidity; our quotes there are pulled first) --
        arb_races = self.take_arbitrage(inv, fvs, mine_real, now_m) if self.running else set()

        # 6. Portfolio-level risk ------------------------------------------------------------------
        eff = self.effective_inventory(inv)
        worst = self.total_worst_case(inv, fvs)
        global_reduce = bool(equity) and worst > cfg.max_worst_case_frac * equity
        party_delta = sum(PARTY_SIGN.get(ex.party, 0) * inv.get(eid, 0.0) for eid, ex in self.ex.items())
        if full:                                  # summary line on full checks only (event cycles can be every 2 s)
            log.info("%s | account %s (locked in orders %.0f, %s) | worst-case loss %.0f%s | party delta %+.0f | "
                     "priced %d/%d | resting %d",
                     "realtime" if realtime else ("polling (realtime connecting)" if self.feed else "polling"),
                     f"{equity:.0f}" if equity is not None else "?", reserved,
                     {"add": "added back", "ignore": "already included"}.get(self.reserved_mode, "detecting"), worst,
                     " -> REDUCE-ONLY" if global_reduce else "", party_delta,
                     sum(v is not None for v in fvs.values()), len(fvs), sum(len(v) for v in resting.values()))
        self.health = {"account_value": equity, "locked_in_orders": round(reserved, 2),
                       "reserved_cash_mode": self.reserved_mode or "detecting", "worst_case_loss": round(worst, 2),
                       "reduce_only": global_reduce, "party_delta": party_delta,
                       "markets_priced": sum(v is not None for v in fvs.values()), "markets_tracked": len(fvs),
                       "orders_resting": sum(len(v) for v in resting.values()),
                       "reference_prices": len(refs), "reference_prices_liquid": len(liquid),
                       "arbitrages_total": self.arbs_total,
                       "rate_limited_total": getattr(self.api, "rate_limited", 0),     # should stay 0
                       "request_budget_per_min": round(getattr(self.api, "budget", 0)),
                       "requests_last_min": round(getattr(self.api, "budget", 0)) - getattr(self.api, "budget_left", lambda: 0)(),
                       "realtime": "connected" if realtime else ("reconnecting" if self.feed else "off"),
                       "realtime_events": self.feed.events if self.feed else 0,
                       "takes_total": self.takes_total,
                       "quote_capital_planned": round(getattr(self, "plan_capital", 0.0)),
                       "biggest_quotes": {self.ex[e].label: s for e, s in sorted(self.size_plan.items(),
                                          key=lambda kv: -kv[1])[:6] if e in self.ex},
                       "positions": {self.ex[e].label: q for e, q in inv.items() if q and e in self.ex}}

        # 6b. Take tournament quotes that Polymarket says are clearly stale (confirmed over 2 readings) ---
        taken = (self.take_stale_quotes(refs, liquid, fvs, inv, mine_real, global_reduce, party_delta, now_m)
                 if self.running else set())

        # 7. Decide + reconcile each exchange. One write at a time (parallel_writes = 1): cancels happen now,
        #    new orders are batched after. Otherwise every change is planned first, then sent in parallel.
        new_orders, changes = [], []
        for eid, ex in list(self.ex.items()):
            if not self.running:          # Ctrl+C: stop touching the book immediately
                return
            if self.api.live and (ex.group in arb_races or eid in taken):
                continue                  # just traded here: positions changed, re-quote next cycle
            try:
                ex.quote = self.decide(ex, fvs.get(eid), inv, eff, global_reduce, party_delta, now_m,
                                       refs.get(eid), book_fvs.get(eid), eid in liquid)
                if cfg.parallel_writes > 1:
                    ch = self.plan_change(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
                    if ch:
                        changes.append(ch)
                else:
                    new_orders += self.reconcile(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
            except ApiError as e:         # one exchange failing must not stop the others
                log.error("exchange %s (%s): %s", eid, ex.label, e)
        if self.running:
            if cfg.parallel_writes > 1:
                self.send_changes(changes)
            else:
                self.place(new_orders, now_m)
        # Orders resting NOW, after this cycle's changes (the health snapshot above was taken before them).
        self.health["orders_resting"] = len(self.my_orders) if self.api.live else len(self.sim)

        # 8. Snapshot for later analysis (every record_seconds) -------------------------------------
        self.record(fvs, now_m)

    def reference_prices(self, book_fvs=None):
        """({eid: Polymarket probability}, {eids whose Polymarket price is liquid}), both empty if the
        feature is off. Liquid = a real two-sided Polymarket market with spread <= ref_liquid_spread.
        A price more than ref_max_plausible_gap from the tournament book's own price is dropped (and you
        get one alert): that's almost certainly a wrong match, e.g. a candidate who was replaced."""
        if not self.refs:
            return {}, set()
        try:
            by_key = self.refs.get()
            spreads = self.refs.spreads() if hasattr(self.refs, "spreads") else {}
        except Exception as e:            # ref_prices shouldn't raise, but never let it stop quoting
            log.warning("reference prices unavailable: %s", e)
            return {}, set()
        refs, liquid = {}, set()
        for eid, ex in self.ex.items():
            key = f"{ex.group}|{ex.party}"   # same key format as ref_prices.ref_key: "Ohio Senate|Republican"
            if key in by_key:
                book = (book_fvs or {}).get(eid)
                if book is not None and abs(by_key[key] - book) > self.cfg.ref_max_plausible_gap:
                    if key not in self.ref_rejected:
                        self.ref_rejected.add(key)
                        alert(f"Polymarket price for {ex.label} ({by_key[key]:.3f}) is {100 * abs(by_key[key] - book):.0f}c "
                              f"from the tournament book ({book:.3f}) - ignoring it. Probably a wrong match in "
                              f"ref_map.json (e.g. a replaced candidate): run `python ref_prices.py check`")
                    continue
                self.ref_rejected.discard(key)       # back in a plausible range: use it again
                refs[eid] = by_key[key]
                s = spreads.get(key)
                if s is not None and s <= self.cfg.ref_liquid_spread:
                    liquid.add(eid)
        return refs, liquid

    def reference_jump_guard(self, now_m):
        """After each new Polymarket reading, pull quotes on any market whose Polymarket price moved
        at least ref_jump_threshold since the previous reading. Polymarket usually moves first, so
        this steps back BEFORE the tournament book catches up. It also means a sudden unexplained
        spike can't drag our prices: if it reverts within the cooldown, we never quoted on it."""
        version = getattr(self.refs, "version", 0)
        if not self.refs or version == self.ref_version_seen:
            return
        self.ref_version_seen = version
        moved = {k: m for k, m in getattr(self.refs, "last_moves", {}).items() if m >= self.cfg.ref_jump_threshold}
        for ex in self.ex.values():
            key = f"{ex.group}|{ex.party}"
            if key in moved:
                ex.cooldown_until = max(ex.cooldown_until, now_m + self.cfg.ref_jump_cooldown_seconds)
                log.warning("POLYMARKET JUMP %s moved %.1fc - pulling quotes for %.0f s",
                            ex.label, 100 * moved[key], self.cfg.ref_jump_cooldown_seconds)

    # ------------------------------------------------------------------------------ books
    def refresh_books(self, mine_real, now_m):
        """FULL check of the books: bulk best bid/ask for every exchange, then re-download the books
        that need it (see books_to_fetch). The age rule exists because bulk prices can't show size
        changes or anything behind the best price."""
        # Bulk best bid/ask, 100 exchanges per request, all requests at once. A failed chunk just means
        # those exchanges fall back to the age rule this cycle.
        eids = list(self.ex)
        chunks = [tuple(eids[i:i + BULK_MAX_IDS]) for i in range(0, len(eids), BULK_MAX_IDS)]
        tops = {}
        for chunk, res in self.in_parallel(lambda c: self.api.bulk_prices(list(c), self.tid), chunks).items():
            if isinstance(res, Exception):
                log.warning("bulk prices failed for %d exchanges (%s) - using the age rule for them", len(chunk), res)
            else:
                tops.update(res)
        self.last_tops = tops

        extra = []
        for eid, ex in self.ex.items():
            mine = mine_real.get(eid, [])
            if tops.get(eid) == (None, None) and not mine:
                # Bulk data says the book is completely empty (e.g. not seeded yet): no need to download it.
                ex.book, ex.book_time, ex.verified = {"bids": [], "asks": []}, now_m, now_m
                self.pending_dirty.discard(eid)
                continue
            if ex.book is None:
                continue                          # books_to_fetch handles never-downloaded books
            if eid in tops and tops[eid] == predicted_top(ex.book, mine):
                ex.verified = now_m               # best prices unchanged: our copy still counts as current
            if eid in tops and tops[eid] != predicted_top(ex.book, mine):
                extra.append((1, ex.book_time, eid))
            elif eid in self.pending_dirty and eid in tops:
                # The feed said this book changed, but its best prices didn't: the change was deeper
                # in the book (sizes). Still worth a look, but after books whose best price moved.
                self.pending_dirty.discard(eid)
                extra.append((2, ex.book_time, eid))
            elif now_m - ex.book_time > self.cfg.book_max_age:
                extra.append((2, ex.book_time, eid))
        if self.running:
            self.download_books(self.books_to_fetch(extra), mine_real)

    def reverify_books(self, mine_real, now_m):
        """Books not confirmed current for book_reverify_seconds get one bulk best-price check (one request
        per 100), so a quiet book never goes stale (and gets its quotes pulled) just because nothing made the
        bot look at it. Unchanged best prices confirm it; a moved one is downloaded next cycle."""
        old = [eid for eid, ex in self.ex.items()
               if ex.book is not None and now_m - ex.verified >= self.cfg.book_reverify_seconds]
        if not old or not self.running or getattr(self.api, "budget_left", lambda: 10 ** 6)() <= 0:
            return
        chunks = [tuple(old[i:i + BULK_MAX_IDS]) for i in range(0, len(old), BULK_MAX_IDS)]
        for chunk, res in self.in_parallel(lambda c: self.api.bulk_prices(list(c), self.tid), chunks).items():
            if isinstance(res, Exception):
                log.warning("bulk re-check failed for %d books (%s)", len(chunk), res)
                continue
            self.last_tops.update(res)
            for eid in chunk:
                ex = self.ex.get(eid)
                if ex is None or eid not in res:
                    continue
                if res[eid] == predicted_top(ex.book, mine_real.get(eid, [])):
                    ex.verified = now_m
                else:
                    self.pending_dirty.add(eid)      # moved: download it next cycle

    def books_to_fetch(self, extra):
        """Which books to download this cycle, most urgent first:
             0  reported changed by the realtime feed
             1  never downloaded, or best price changed according to the bulk prices (`extra`)
             2  changed deeper in the book, or older than book_max_age (`extra`)
        At most max_books_per_cycle, and never more than the request budget has spare after keeping
        budget_reserve back for orders. The rest wait for a later cycle (reported books stay pending)."""
        candidates = [(0, self.ex[e].book_time, e) for e in self.pending_dirty if e in self.ex]
        candidates += [(1, 0.0, eid) for eid, ex in self.ex.items() if ex.book is None]
        candidates += extra
        todo = []
        for _, _, eid in sorted(candidates):
            if eid not in todo:
                todo.append(eid)
        spare = getattr(self.api, "budget_left", lambda: 10 ** 6)() - self.cfg.budget_reserve
        return todo[:max(0, min(self.cfg.max_books_per_cycle, spare))]

    def download_books(self, eids, mine_real):
        """Download books (in parallel), remove our own orders from them, and cache them on the Ex."""
        for eid, book in self.in_parallel(lambda e: self.api.book(e, self.tid), eids).items():
            if isinstance(book, Exception):   # keep the old copy; it stops being used after book_stale
                log.warning("book %s failed: %s", eid, book)
            else:
                self.ex[eid].book = strip_own(book, mine_real.get(eid, []))
                self.ex[eid].book_time = self.ex[eid].verified = time.monotonic()
                self.pending_dirty.discard(eid)

    # ------------------------------------------------------------------------------ risk
    def account_value(self, pos, f_pnl):
        """Cash + holdings at current prices, straight from the API's P&L endpoint (f_pnl is that
        request, already running in parallel). Falls back to balance + position value if it
        failed. None if both fail."""
        try:
            return float(f_pnl.result()["totalAccountValue"])
        except (ApiError, KeyError, TypeError, ValueError) as e:
            log.warning("P&L endpoint failed (%s) - using balance + positions", e)
        try:
            return float(self.api.tournament()["myBalance"]) + float(pos["summary"]["totalMarketValue"])
        except (ApiError, KeyError, TypeError, ValueError) as e:
            log.warning("account value unavailable (%s) - kill switch skipped this cycle", e)
            return None

    def checked_account_value(self, equity, reserved, inv):
        """Account value for the kill switch, corrected for cash locked in our open orders.

        Problem: if the API's account value leaves out cash reserved by resting orders, then simply
        posting ~350 quotes would look like a ~17k loss and trip the kill switch with nothing lost.
        With reserved_cash_mode="auto" we find out from the data: on a cycle where our positions
        didn't change (no fills) but the locked cash did, a real loss is impossible, so
          - account value fell by about the change in locked cash  -> it's left out -> add it back
          - account value stayed put                               -> it's included -> leave it
        While still undecided we add it back, so a false alarm can't stop the bot. That makes the
        switch a little late only until the first few batches of orders settle the question.
        """
        if equity is None:
            return None
        if self.reserved_mode is None:
            self.detect_reserved_mode(equity, reserved, inv)
        return equity if self.reserved_mode == "ignore" else equity + reserved

    def detect_reserved_mode(self, equity, reserved, inv):
        """One auto-detection step (see checked_account_value). Needs reserved_calib_votes agreeing
        observations in a row, because the API's account value may lag a moment behind new orders."""
        cfg = self.cfg
        positions = {k: round(v) for k, v in inv.items() if round(v)}
        prev, self.calib_prev = self.calib_prev, (equity, reserved, positions)
        if prev is None:
            return
        v0, r0, positions0 = prev
        dv, dr = equity - v0, reserved - r0
        if positions != positions0 or abs(dr) < cfg.reserved_calib_min:
            return                                            # fills happened, or too small a change to judge
        if abs(dv + dr) < 0.2 * abs(dr):
            verdict = "add"                                   # value moved opposite to locked cash: left out
        elif abs(dv) < 0.2 * abs(dr):
            verdict = "ignore"                                # value didn't move: already included
        else:
            self.calib_votes = []                             # unclear (e.g. prices moved): start over
            return
        self.calib_votes = (self.calib_votes + [verdict])[-cfg.reserved_calib_votes:]
        if len(self.calib_votes) == cfg.reserved_calib_votes and len(set(self.calib_votes)) == 1:
            self.reserved_mode = verdict
            log.warning("kill switch: the API's account value %s cash locked in open orders -> %s. "
                        "(Set reserved_cash_mode=\"%s\" in SETTINGS to skip this check next time.)",
                        "LEAVES OUT" if verdict == "add" else "already includes",
                        "adding it back" if verdict == "add" else "using it as is", verdict)

    def kill_switch(self, equity):
        """Stop the bot if the account is max_drawdown_pct below the tournament's INITIAL balance.
        The initial balance (not the value at start-up) is used so restarting the bot doesn't reset
        the limit. It needs kill_confirmations bad readings in a row, so one glitchy number can't
        shut us down."""
        if equity is None or not self.initial_balance:
            return False
        floor = self.initial_balance * (1 - self.cfg.max_drawdown_pct)
        self.kill_breaches = self.kill_breaches + 1 if equity < floor else 0
        if self.kill_breaches >= self.cfg.kill_confirmations:
            msg = f"KILL SWITCH: account value {equity:.0f} < {floor:.0f} - cancelling everything and stopping"
            log.critical(msg)
            # Leave a marker file so neither systemd nor a quick manual restart can resume trading.
            with open(bot_path(self.cfg.kill_file), "w") as f:
                f.write(f"{iso(utcnow())}  {msg}\nDelete this file to allow the bot to trade again.\n")
            alert(msg)
            self.running, self.exit_code = False, EXIT_KILLED
            return True
        return False

    def effective_inventory(self, inv):
        """Inventory after netting the other parties in the same race.

        Holding YES on every party in a race is (nearly) risk-free, because one of them pays 1.
        What matters is how far each position sits from the average of the others:
            eff_i = x_i - mean(x_j for the other parties j)
        Two-party race, long 500 Republican: eff_R = +500 and eff_D = -500, so the bot becomes keen
        to buy Democrat YES, which is the hedge. After buying 500 Democrat YES, both are 0.
        """
        eff = {}
        for members in self.groups.values():
            for e in members:
                others = [inv.get(o, 0.0) for o in members if o != e]
                eff[e] = inv.get(e, 0.0) - (sum(others) / len(others) if others else 0.0)
        return eff

    def total_worst_case(self, inv, fvs):
        """Sum over races of the worst-case settlement loss (see worst_case_loss)."""
        total = 0.0
        for members in self.groups.values():
            legs = [(inv.get(e, 0.0), fvs.get(e) or self.ex[e].last_fv or 0.5) for e in members]
            if any(x for x, _ in legs):
                total += worst_case_loss(legs)
        return total

    # ------------------------------------------------------------------------------ decide
    def decide(self, ex, fv, inv, eff, global_reduce, party_delta, now_m, ref=None, book_fv=None, ref_liquid=False):
        """What should be resting on this exchange right now? NO_QUOTE = nothing.
        fv is what we quote around; book_fv is the tournament book's own price (for the Polymarket guard);
        ref_liquid says whether the Polymarket price is reliable enough to size positions with Kelly."""
        cfg = self.cfg
        ex.inv, ex.eff, ex.ref = inv.get(ex.eid, 0.0), eff.get(ex.eid, 0.0), ref
        hrs = self.hours_to_close(ex)
        if hrs * 60 <= cfg.stop_minutes_before_close:
            return NO_QUOTE                                   # too close to settlement
        b = ex.book or {}
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        best_ask = b["asks"][0]["price"] if b.get("asks") else None

        # Election night, final hours: only get flat, even by trading against other orders (exit_quote).
        # This comes before every other guard on purpose: getting out must never be blocked. If the book
        # has gone too thin for a fair value (likely on election night), exit around our last known fair
        # value, or failing that Polymarket's price, instead of holding the position into settlement.
        if hrs <= cfg.exit_hours_before_close:
            anchor = next((x for x in (fv, ex.last_fv, ref) if x is not None), None)
            if fv is not None:
                ex.last_fv = fv
            planned = self.size_plan.get(ex.eid) if cfg.size_by_activity else None   # big positions leave in big pieces
            return (exit_quote(anchor, ex.inv, best_bid, best_ask, cfg, self.bankroll(), max_size=planned)
                    if anchor is not None else NO_QUOTE)
        if fv is None:
            return NO_QUOTE                                   # no trustworthy price

        # Jump guard: a big move in one cycle usually means news; informed traders will pick us off.
        if ex.last_fv is not None and abs(fv - ex.last_fv) >= cfg.jump_threshold:
            log.warning("JUMP %s %.3f -> %.3f - pausing %.0fs", ex.label, ex.last_fv, fv, cfg.jump_cooldown_seconds)
            ex.cooldown_until = now_m + cfg.jump_cooldown_seconds
        ex.last_fv = fv
        if now_m < ex.cooldown_until:
            return NO_QUOTE

        no_bid, no_ask = self.party_blocks(ex, party_delta)

        # Reference-price guard: if Polymarket says this contract is worth clearly MORE than the
        # tournament book does, don't sell it to anyone here (they probably know); clearly LESS -> don't
        # buy. Compared with the book's own price, not the leaned fair value (see cycle step 3).
        if ref is not None:
            base = book_fv if book_fv is not None else fv
            no_ask = no_ask or ref - base > cfg.ref_guard_gap
            no_bid = no_bid or base - ref > cfg.ref_guard_gap

        # Tail guard: near 0 or 1, one side risks ~1 a share to earn ~1c, and a single upset wipes out
        # many fills. Don't take that side, except to shrink a position we already hold.
        bid_cap = ask_cap = None
        if fv < cfg.tail_low:
            ask_cap = max(0, int(ex.inv))         # selling YES near 0 = buying NO near 1
        if fv > cfg.tail_high:
            bid_cap = max(0, int(-ex.inv))        # buying YES near 1

        reduce_only = global_reduce or hrs <= cfg.flatten_hours_before_close
        # From flatten_per_market_hours: flatten each market on its own, i.e. judge (and skew) by this
        # market's own position rather than the race-netted one.
        inv_for_quote = ex.inv if hrs <= cfg.flatten_per_market_hours else ex.eff
        # Kelly position limits, only with a liquid Polymarket price and a known account value.
        kelly_p = ref if (ref is not None and ref_liquid) else None
        # Size: this market's share of the capital plan; the party-control markets get a flat position limit.
        planned = self.size_plan.get(ex.eid, cfg.size_min_frac * self.bankroll()) if cfg.size_by_activity else None
        headline_limit = (cfg.headline_position_frac * self.bankroll()
                          if cfg.size_by_activity and ex.group in cfg.headline_races else None)
        return compute_quote(fv, ex.inv, inv_for_quote, best_bid, best_ask, cfg, reduce_only, no_bid, no_ask,
                             bid_cap, ask_cap, kelly_p=kelly_p, bankroll=self.bankroll(),
                             shift=self.party_shift(ex, party_delta), order_size=planned, position_limit=headline_limit)

    def update_size_plan(self, now_m, fvs):
        """Every size_plan_seconds: work out how many shares to quote in each market (see plan_sizes).
        Only markets that have a fair value (i.e. will be quoted) get capital, each at what its quotes
        really lock (quote_lock). Activity = each market's share of Polymarket's traded volume, blended
        with its share of the tournament trades the realtime feed has reported. The tournament's own
        trades count more as they accumulate: fully (live_activity_max_weight) after live_activity_trades.
        A market that gets a fair value between plans quotes the minimum until the next plan."""
        cfg = self.cfg
        if (self.size_plan and now_m - self.size_plan_time < cfg.size_plan_seconds
                and self.size_plan_bank == self.bankroll()):
            return                                         # (a 5% account step re-plans at once)
        quoted = [e for e in self.ex if fvs.get(e) is not None]
        vols = self.refs.volumes() if self.refs and hasattr(self.refs, "volumes") else {}
        poly = {e: float(vols.get(f"{self.ex[e].group}|{self.ex[e].party}") or 0.0) for e in quoted}
        live = dict(getattr(self.feed, "trade_counts", None) or {}) if self.feed else {}
        live = {e: live.get(e, 0) for e in quoted}
        tot_p, tot_l = sum(poly.values()), sum(live.values())
        w_live = min(cfg.live_activity_max_weight, tot_l / cfg.live_activity_trades) if tot_l else 0.0
        activity = {e: (1 - w_live) * (poly[e] / tot_p if tot_p else 0.0) + w_live * (live[e] / tot_l if tot_l else 0.0)
                    for e in quoted}
        headline = {e for e in quoted if self.ex[e].group in cfg.headline_races}
        lock = {e: quote_lock(fvs[e], cfg) for e in quoted}
        new = plan_sizes(activity, headline, self.bankroll(), cfg, prev=self.size_plan, lock=lock,
                         prev_bankroll=self.size_plan_bank)
        changed = sum(1 for e, s in new.items() if self.size_plan.get(e) != s)
        self.size_plan, self.size_plan_time, self.size_plan_bank = new, now_m, self.bankroll()
        self.plan_capital = sum(s * lock[e] for e, s in new.items())
        top = sorted(new.items(), key=lambda kv: -kv[1])[:6]
        log.info("size plan: %d markets quoted, locking up to %s (cap %.0f), %d changed, tournament trades weigh %.0f%% | "
                 "biggest: %s", len(new), f"{sum(s * lock[e] for e, s in new.items()):,.0f}",
                 cfg.quote_capital_frac * self.bankroll(), changed, 100 * w_live,
                 ", ".join(f"{self.ex[e].label} {s:,}" for e, s in top))

    def party_shift(self, ex, party_delta):
        """National-swing shading: how much to lower this market's reservation price. Net long Republican
        (party_delta > 0) -> Republican quotes shade down (we sell Rep more readily, buy it less) and
        Democratic ones up, so fills bring the net exposure back. Grows linearly to party_skew_at_cap at
        the hard cap (where party_blocks takes over). Zero for markets that aren't Rep/Dem."""
        sign = PARTY_SIGN.get(ex.party, 0)
        cap = self.cfg.max_party_delta_frac * self.bankroll()
        if not sign or cap <= 0:
            return 0.0
        return self.cfg.party_skew_at_cap * sign * max(-1.0, min(1.0, party_delta / cap))

    def party_blocks(self, ex, party_delta):
        """National-swing cap -> (no_bid, no_ask). Buying YES on a Republican market pushes the net
        Republican-minus-Democrat delta up, on a Democratic market down; selling does the opposite.
        Beyond the cap, block whichever side would make it worse."""
        sign = PARTY_SIGN.get(ex.party, 0)
        party_cap = self.cfg.max_party_delta_frac * self.bankroll()
        too_red, too_blue = party_delta > party_cap, party_delta < -party_cap
        return (sign > 0 and too_red) or (sign < 0 and too_blue), (sign > 0 and too_blue) or (sign < 0 and too_red)

    def bankroll(self):
        """Account value that every size is a fraction of. It follows the real value in steps: only once
        that has moved sizing_step_frac away. Sizes are whole shares, so following every wobble would
        turn 100-share orders into 99-share ones on a 10-SUSQie dip and replace every order at once.
        While "auto" hasn't yet worked out whether the API's value includes cash locked in our orders
        (see checked_account_value), the value may be off by that cash, so it isn't followed at all."""
        eq = self.last_equity
        if eq and self.reserved_mode is not None and abs(eq / self.size_bank - 1) >= self.cfg.sizing_step_frac:
            log.info("sizing: account value %.0f is %+.1f%% from %.0f - order sizes rescaled",
                     eq, 100 * (eq / self.size_bank - 1), self.size_bank)
            self.size_bank = eq
        return self.size_bank

    # ------------------------------------------------------------------------------ reconcile
    def plan_change(self, ex, q, resting, fv, now, now_m):
        """What has to change on this exchange so its resting orders match quote q: a Change, or None.
        Nothing new is ever planned while an earlier write there is unresolved (it could double up);
        pulling orders is always allowed, unless a cancel is already on its way."""
        if busy(ex, now_m):
            if q.bid is None and q.ask is None and resting and not ex.cancelling:
                return Change(ex, resting, True, [], self.change_key(ex, pull=True, reprice=True))
            return None

        bids = [o for o in resting if o.is_bid]
        asks = [o for o in resting if not o.is_bid]
        fix_bid = side_needs_change(bids, q.bid, q.bid_size, self.cfg, now, q.bid_limit, is_bid=True)
        fix_ask = side_needs_change(asks, q.ask, q.ask_size, self.cfg, now, q.ask_limit, is_bid=False)
        if not (fix_bid or fix_ask):
            return None                   # book already matches: keep our queue position

        # Cancel the wrong side(s). Both wrong -> one cancel-all for the exchange; else per order.
        doomed = (bids if fix_bid else []) + (asks if fix_ask else [])
        new = []
        if now_m >= ex.pause_until:
            if fix_bid and q.bid is not None:
                new.append(self.new_order(ex, True, q.bid, q.bid_size, fv, now))
            if fix_ask and q.ask is not None:
                new.append(self.new_order(ex, False, q.ask, q.ask_size, fv, now))
        log.info("%s%-26.26s fv %s%s inv %+5.0f race %+5.0f | bid %s ask %s", "" if self.api.live else "[dry] ",
                 ex.label, f"{fv:.3f}" if fv is not None else "  -  ",
                 f" (ref {ex.ref:.3f})" if ex.ref is not None else "", ex.inv, ex.eff,
                 fmt(q.bid, q.bid_size), fmt(q.ask, q.ask_size))
        if not doomed and not new:
            return None
        return Change(ex, doomed, fix_bid and fix_ask, new, self.change_key(ex, pull=not new, reprice=bool(doomed)))

    def change_key(self, ex, pull, reprice=False):
        """Sending order: pulls first, then the party-control markets, then quotes for empty sides (cheap: a
        share of one batch), then reprices (a cancel each), biggest quotes first within each."""
        return (0 if pull else 1, 0 if ex.group in self.cfg.headline_races else 1, 1 if reprice else 0,
                -self.size_plan.get(ex.eid, 0))

    def reconcile(self, ex, q, resting, fv, now, now_m):
        """One write at a time (parallel_writes = 1): make the orders resting on this exchange match quote q.
        Cancels happen right away. New orders are returned so they can be sent in batches."""
        ch = self.plan_change(ex, q, resting, fv, now, now_m)
        if ch is None:
            return []
        if ch.doomed and not self.cancel(ex.eid, ch.doomed, whole_exchange=ch.whole):
            log.warning("%s: could not confirm cancels - retrying next cycle", ex.label)
            return []                     # never stack new quotes on top of old ones
        return ch.new

    # ------------------------------------------------------------------------------ parallel order writes
    def send_changes(self, changes):
        """Send this cycle's order changes with up to parallel_writes requests in flight, most urgent first:
          1. every cancel at once (pulls first), plus the new orders on exchanges that need no cancel;
          2. as each cancel is confirmed, the new orders that were waiting for it.
        New orders on an exchange are only ever sent after its cancel is CONFIRMED (never two quotes on one
        side). The cycle waits at most write_wait_seconds; writes still running then carry on in the
        background, their exchanges are left alone (Ex.writes) and the results are applied next cycle."""
        cfg = self.cfg
        deadline = time.monotonic() + cfg.write_wait_seconds
        changes = sorted(changes, key=lambda c: c.key)
        # Within the request budget, keeping write_read_reserve back so reads (positions, orders, books) never
        # starve; the least urgent changes wait for the next cycle.
        spare = getattr(self.api, "budget_left", lambda: 10 ** 6)() - cfg.write_read_reserve
        kept, cost, orders = [], 0.0, 0
        for ch in changes:
            n = orders + len(ch.new)
            c = (0 if not ch.doomed else 1 if ch.whole else len(ch.doomed)) + (
                math.ceil(n / cfg.batch_size) - math.ceil(orders / cfg.batch_size))
            if cost + c > spare and ch.key[0] != 0:
                break                         # pulls always go; everything after the first misfit waits
            kept.append(ch)
            cost, orders = cost + c, n
        if len(kept) < len(changes):
            log.info("request budget: %d of %d order changes deferred to the next cycle",
                     len(changes) - len(kept), len(changes))
        changes = kept
        waiting = set()
        for ch in changes:
            if ch.doomed:
                waiting.add(self.submit_write("cancel", [ch.ex.eid], (ch.doomed, ch.whole), change=ch))
        self.send_orders([c for c in changes if not c.doomed and c.new])
        while self.running:
            # Only cancels sent by THIS call release their new orders: a late one from an earlier cycle carries
            # that cycle's prices (the next cycle re-plans that exchange instead).
            ready = [w.change for w in self.harvest_writes() if w in waiting and w.change.new and w.payload_ok]
            if ready:
                self.send_orders(sorted(ready, key=lambda c: c.key))
            left = deadline - time.monotonic()
            if left <= 0 or not self.writes:
                break
            wait([w.future for w in self.writes], timeout=left, return_when=FIRST_COMPLETED)
        self.harvest_writes()

    def send_orders(self, changes):
        """New orders of these changes in batches of batch_size, in the order given; party-control markets get
        a batch of their own, so it's never queued behind (or slowed by) a big one."""
        orders = [(c.key, o) for c in changes for o in c.new if c.ex.writes == 0]
        head = [o for k, o in orders if k[1] == 0]
        rest = [o for k, o in orders if k[1] != 0]
        for group in (head, rest):
            for i in range(0, len(group), self.cfg.batch_size):
                chunk = group[i:i + self.cfg.batch_size]
                self.submit_write("batch", [o["exchangeId"] for o, _ in chunk], chunk)

    def submit_write(self, kind, eids, payload, change=None):
        w = Write(kind, eids, payload, time.monotonic(), change)
        for eid in eids:
            ex = self.ex.get(eid)
            if ex:
                ex.writes += 1
                ex.cancelling = ex.cancelling or kind == "cancel"
        if kind == "cancel":
            eid, (orders, whole) = eids[0], payload
            if whole and eid == self.selftest_eid:
                self.cancel_gen += 1      # also removes any self-test orders there (see selftest_finish)
            w.future = self.writer.submit(self.cancel_request, eid, orders, whole)
        else:
            w.future = self.writer.submit(self.api.place_batch, [o for o, _ in payload])
        w.future.add_done_callback(lambda _f: self.write_done.set())   # main loop: apply it soon
        self.writes.append(w)
        return w

    def cancel_request(self, eid, orders, whole):
        """Writer thread: the cancel request(s) only. True = confirmed gone."""
        if not self.api.live:
            return True
        if whole:
            return self.api.cancel_all(self.tid, eid)
        return all([self.api.cancel_order(o.order_id) for o in orders])

    def harvest_writes(self):
        """Main thread: apply the results of writes that have finished; returns those Write jobs."""
        self.write_done.clear()
        done = [w for w in self.writes if w.future.done()]
        if not done:
            return []
        self.writes = [w for w in self.writes if w not in done]
        now_m = time.monotonic()
        for w in done:                    # counters first, so a failure below can't leave an exchange stuck
            for eid in w.eids:
                ex = self.ex.get(eid)
                if ex:
                    ex.writes = max(0, ex.writes - 1)
                    if w.kind == "cancel":
                        ex.cancelling = False
        for w in done:
            if w.future.cancelled():
                continue                  # never sent (dropped from the queue by stop_queued_writes)
            try:
                self.apply_write(w, now_m)
            except Exception:
                log.exception("applying a write result failed")
                self.orders_stale = True
        return done

    def apply_write(self, w, now_m):
        """Main thread: apply one finished write's result."""
        try:
            result = w.future.result()
        except Exception as e:
            result = e
        if w.kind == "cancel":
            eid, (orders, whole) = w.eids[0], w.payload
            if result is True:
                w.payload_ok = True
                if not self.api.live:
                    for o in orders:
                        self.sim.pop(o.order_id, None)
                self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid] if whole
                                   else [o.order_id for o in orders])
            else:
                if isinstance(result, Exception):
                    log.error("cancel on %s failed: %s", eid, result)
                label = self.ex[eid].label if eid in self.ex else eid
                log.warning("%s: could not confirm cancels - retrying next cycle", label)
                self.orders_stale = True
        else:
            self.apply_batch(w.payload, result, now_m)

    def stop_queued_writes(self):
        """Drop writes still waiting for a writer thread (not yet sent): before cancelling everything, so
        nothing queued earlier is placed after the cancel."""
        for w in self.writes:
            w.future.cancel()             # only succeeds for ones not started

    def drain_writes(self, timeout):
        """Wait (up to timeout) for writes still in flight and apply them: before cancelling everything at
        shutdown, so an order that lands late isn't left resting."""
        if self.writes:
            wait([w.future for w in self.writes], timeout=timeout)
        self.harvest_writes()

    def new_order(self, ex, is_bid, price, size, fv, now):
        """One order for POST /orders/batch, plus notes about why we placed it."""
        order = {"exchangeId": ex.eid, "side": "yes", "action": "buy" if is_bid else "sell",
                 "quantity": int(size), "price": round(price, 3), "tournamentId": self.tid,
                 # Dead-man's switch: the order dies on its own unless we keep refreshing it.
                 "expirationDate": iso(now + timedelta(seconds=self.cfg.order_ttl))}
        meta = {"our_side": "bid" if is_bid else "ask", "price": round(price, 3), "fv": fv, "t": time.time()}
        return order, meta

    def sync_orders(self, raw_orders, now_m):
        """Reset our record of our resting orders (self.my_orders) from the API's open-orders list.

        Between these reads (every full check) the bot keeps the record itself: placements add to it,
        cancels and fills take from it. That saves re-reading the whole list (several requests) after
        every change we make. The list is a reporting projection that can lag the exchange by a moment,
        so for recent_order_grace_seconds our own record wins: an order we just placed stays even if the
        list doesn't show it yet (else we'd place it twice), and one we just cancelled stays gone."""
        grace = self.cfg.recent_order_grace_seconds
        listed = {r.order_id: r for r in map(parse_order, raw_orders) if r}
        for oid, t in list(self.recent_cancels.items()):
            if now_m - t > grace:
                del self.recent_cancels[oid]
            else:
                listed.pop(oid, None)
        for oid, (order, placed) in list(self.recent_orders.items()):
            if oid in listed or now_m - placed > grace:
                del self.recent_orders[oid]           # the list shows it now (or it's old enough to trust the list)
            else:
                listed[oid] = order
        # Shares left: the list can lag a fill we've already read, and we may not have read a fill the list
        # already shows. Both only ever OVERstate what's left, so the smaller of the two is right.
        for oid, o in listed.items():
            if oid in self.placed_qty:
                o.qty = min(o.qty, self.placed_qty[oid] - self.filled_qty.get(oid, 0.0))
        self.my_orders = {oid: o for oid, o in listed.items() if o.qty > 0}
        for oid in [k for k in self.placed_qty if k not in listed]:
            self.placed_qty.pop(oid, None)            # no longer resting: stop tracking its size
            self.filled_qty.pop(oid, None)

    def orders_by_eid(self, now):
        """Our resting orders (live) grouped by exchange, dropping any that have expired."""
        out = defaultdict(list)
        for oid, o in list(self.my_orders.items()):
            if o.expires and o.expires <= now:
                del self.my_orders[oid]
            else:
                out[o.eid].append(o)
        return out

    def remember_order(self, order, data, now_m):
        """Add an order we just placed (API request format + its batch result) to our record of resting
        orders, unless it traded in full straight away. Every placement goes through here (quotes,
        arbitrage, takes), so the bot never quotes on top of an order it doesn't know about."""
        oid = data.get("orderId")
        traded = float(data.get("quantityTraded") or 0)
        left = order["quantity"] - traded
        if not self.api.live or oid is None or left <= 0:
            return
        o = Resting(oid, order["exchangeId"], order["action"] == "buy", order["price"], left,
                    parse_ts(order["expirationDate"]))
        self.my_orders[oid], self.recent_orders[oid] = o, (o, now_m)
        # filled_qty starts at 0 even if part traded at once: that trade also arrives as fills (log_fills),
        # and counting it here too would count it twice. o.qty already starts at what's left.
        self.placed_qty[oid], self.filled_qty[oid] = float(order["quantity"]), 0.0

    def forget_orders(self, oids):
        """These orders are gone (cancelled or fully filled): drop them from our record."""
        now_m = time.monotonic()
        for oid in oids:
            self.my_orders.pop(oid, None)
            self.recent_orders.pop(oid, None)
            self.recent_cancels[oid] = now_m

    def cancel_everything(self):
        """Cancel every order the bot is responsible for. With ONLY_EXCHANGES set, that's just those
        exchanges, so orders you placed by hand elsewhere survive. Otherwise it's the whole tournament."""
        self.sim.clear()
        self.cancel_gen += 1
        if hasattr(self, "writes"):
            self.stop_queued_writes()
        self.forget_orders(list(self.my_orders))
        self.orders_stale = True                  # confirm with a fresh read next cycle
        if not self.cfg.only_exchanges:
            return self.api.cancel_all(self.tid)
        return all([self.api.cancel_all(self.tid, eid) for eid in self.ex])   # list: try every one

    def cancel(self, eid, orders, whole_exchange, quiet=False):
        """Cancel orders on one exchange. Returns True only if we're sure they're gone."""
        if not self.api.live:
            for o in orders:
                self.sim.pop(o.order_id, None)
            return True
        if whole_exchange and eid == self.selftest_eid:
            self.cancel_gen += 1          # this also removes any self-test orders there (see selftest_finish)
        try:
            if whole_exchange:
                ok = self.api.cancel_all(self.tid, eid)
            else:
                ok = all(self.api.cancel_order(o.order_id) for o in orders)
        except ApiError as e:
            if not quiet:
                log.error("cancel on %s failed: %s", eid, e)
            ok = False
        if ok:                                # gone: update our record of what's resting
            self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid] if whole_exchange
                               else [o.order_id for o in orders])
        else:
            self.orders_stale = True          # not sure what's left: re-read the list next cycle
        return ok

    def place(self, new_orders, now_m):
        """One write at a time: send new orders in batches and record what each one was for."""
        cfg = self.cfg
        for i in range(0, len(new_orders), cfg.batch_size):
            chunk = new_orders[i:i + cfg.batch_size]
            try:
                results = self.api.place_batch([o for o, _ in chunk])
            except ApiError as e:
                results = e
            self.apply_batch(chunk, results, now_m)

    def apply_batch(self, chunk, results, now_m):
        """Main thread: record the outcome of one batch (its results, or the exception it raised): what each
        order was for (fill attribution), our record of resting orders, and back-offs."""
        cfg = self.cfg
        if isinstance(results, Exception):
            e = results if isinstance(results, ApiError) else ApiError(0, "NETWORK", str(results))
            # Unknown outcome (network, 409 in flight, 502/503 after retries): some orders may
            # exist, so leave these exchanges alone until they show up. Clear rejection: back off.
            ambiguous = e.status in (0, 409, 502, 503, 504)
            self.orders_stale = self.orders_stale or ambiguous   # some may exist: re-read the list
            for o, _ in chunk:
                ex = self.ex.get(o["exchangeId"])
                if ex:
                    if ambiguous:
                        ex.pending_until = now_m + cfg.pending_seconds
                    else:
                        ex.pause_until = now_m + cfg.fail_pause_seconds
            log.error("batch of %d orders failed (%s)", len(chunk), e)
            if e.code in FATAL_API_CODES:
                fatal(f"orders rejected with {e.code} - fix it (accept the terms in the web UI / check the API key) and restart")
            return

        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, (order, meta) in enumerate(chunk):
            r, ex = by_index.get(k, {}), self.ex.get(order["exchangeId"])
            data = r.get("data") or {}
            if r.get("ok"):
                oid = data.get("orderId")
                if not self.api.live:                     # dry run: remember it as if it were resting
                    oid, self.next_sim_id = self.next_sim_id, self.next_sim_id - 1
                    self.sim[oid] = Resting(oid, order["exchangeId"], order["action"] == "buy",
                                            order["price"], order["quantity"], parse_ts(order["expirationDate"]))
                if oid is not None:
                    self.order_meta[oid] = {**meta, "eid": order["exchangeId"]}
                    self.notes_dirty = True
                self.remember_order(order, data, now_m)       # our record of resting orders
                if data.get("quantityTraded"):
                    log.info("order on %s traded %s immediately", order["exchangeId"], data["quantityTraded"])
                    self.orders_stale = True          # positions changed: re-read them next cycle
                continue
            err = data.get("error") or {}
            code = err.get("code") or data.get("code") or r.get("status")
            log.warning("order rejected on %s (%s): %s", ex.label if ex else order["exchangeId"],
                        code, err.get("message") or data.get("error"))
            if ex:
                if r.get("status") == 502:                # ORDER_STATUS_UNKNOWN: it may exist
                    ex.pending_until = now_m + cfg.pending_seconds
                    self.orders_stale = True
                elif r.get("status") not in (429, 503):   # transient ones just retry next cycle
                    ex.pause_until = now_m + cfg.fail_pause_seconds
            if code in FATAL_API_CODES:
                fatal(f"orders rejected with {code} - fix it (accept the terms in the web UI / check the API key) and restart")

    # ------------------------------------------------------------------------------ arbitrage
    def take_arbitrage(self, inv, fvs, mine_real, now_m):
        """Guaranteed profit inside a race: if other traders' best bids on EVERY party add up to more
        than 1, sell YES to all of them.

        Why it can't lose: at most one party wins a race, so for each set sold we pay out at most 1,
        but we collected the sum of the bids (> 1). In the engine's terms we're buying NO on every
        party for sum(1 - bid) = n - sum(bids), and at least n - 1 of those NO shares pay 1.
        (The opposite trade, buying YES on every party when the asks add up to < 1, only works if one
        listed party is certain to win, so it isn't done.)
        Returns the set of races acted on, whose quoting is skipped until next cycle.
        """
        cfg = self.cfg
        done = set()
        if not cfg.arb_enabled:
            return done
        for race, members in self.groups.items():
            if (len(members) < 2 or not self.running or now_m < self.arb_cooldown.get(race, 0)
                    or any(busy(self.ex[e], now_m) for e in members)
                    # not in the pre-close window: the positions are hedged across the race, but the
                    # per-market flatten would then pay the spread to unwind each leg
                    or any(self.hours_to_close(self.ex[e]) <= cfg.flatten_hours_before_close for e in members)):
                continue
            if self.arb_bids(members) is None:            # quick check on the cached books
                continue
            # Cached books can be up to book_max_age old: re-download this race before acting on it.
            books = self.in_parallel(lambda e: self.api.book(e, self.tid), members)
            if any(isinstance(b, Exception) for b in books.values()):
                continue
            for e, b in books.items():
                self.ex[e].book = strip_own(b, mine_real.get(e, []))
                self.ex[e].book_time = self.ex[e].verified = time.monotonic()
            bids = self.arb_bids(members)
            if bids is None:
                continue
            bank = self.bankroll()
            qty = int(min([cfg.arb_max_frac * bank] +
                          [size for _, size in bids.values()] +                            # only what's bid at that price
                          [cfg.max_position_frac * bank + inv.get(e, 0.0) for e in members] +   # selling lowers position
                          [cfg.max_order_cash_frac * bank / max(1 - p, TICK) for p, _ in bids.values()]))  # cash per order
            self.arb_cooldown[race] = now_m + cfg.arb_cooldown_seconds
            if qty >= 1:
                self.execute_arbitrage(race, members, bids, qty, fvs, now_m)
                done.add(race)
        return done

    def arb_bids(self, members):
        """{eid: (price, size)} of the best OTHER-trader bid on each party of a race, if those bids
        add up to at least 1 + arb_min_profit. Otherwise None."""
        bids = {}
        for e in members:
            b = self.ex[e].book
            if not b or not b.get("bids"):
                return None
            bids[e] = (b["bids"][0]["price"], b["bids"][0]["quantity"])
        return bids if sum(p for p, _ in bids.values()) >= 1 + self.cfg.arb_min_profit - 1e-9 else None

    def execute_arbitrage(self, race, members, bids, qty, fvs, now_m):
        cfg = self.cfg
        total = sum(p for p, _ in bids.values())
        legs = ", ".join(f"{self.ex[e].label} @{p:.3f}" for e, (p, _) in bids.items())
        log.warning("%sARBITRAGE %s: bids add up to %.3f (%s) -> selling %d YES on each, locking in >= %.0f",
                    "" if self.api.live else "[dry] ", race, total, legs, qty, (total - 1) * qty)
        self.arbs_total += 1
        if not self.api.live:
            return
        # 1. Pull our own quotes in this race, so the arbitrage can't trade against ourselves.
        if not all([self.cancel(e, [], whole_exchange=True) for e in members]):
            log.warning("arbitrage on %s abandoned: could not clear our own quotes", race)
            return
        # 2. Sell at exactly those bids. The orders expire within seconds so leftovers can't rest.
        exp = iso(utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        orders = [{"exchangeId": e, "side": "yes", "action": "sell", "quantity": qty, "price": p,
                   "tournamentId": self.tid, "expirationDate": exp} for e, (p, _) in bids.items()]
        self.orders_stale = True                  # positions and orders change: re-read next cycle
        try:
            results = self.api.place_batch(orders)
        except ApiError as e:
            for m in members:                             # outcome unknown: don't pile in again
                self.ex[m].pending_until = now_m + cfg.pending_seconds
            alert(f"arbitrage on {race}: placement failed ({e}) - check positions")
            if e.code in FATAL_API_CODES:
                fatal(f"orders rejected with {e.code}")
            return
        # 3. Cancel whatever didn't fill straight away, then check each leg got the same amount. The
        #    orders go into our record first, so a leftover whose cancel fails is still known about.
        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, o in enumerate(orders):
            if (by_index.get(k) or {}).get("ok"):
                self.remember_order(o, (by_index.get(k) or {}).get("data") or {}, now_m)
        for e in members:
            self.cancel(e, [], whole_exchange=True, quiet=True)
        traded = []
        for k, o in enumerate(orders):
            data = (by_index.get(k) or {}).get("data") or {}
            traded.append(float(data.get("quantityTraded") or 0))
            if data.get("orderId") is not None:           # so these fills show up in `report`
                self.order_meta[data["orderId"]] = {"our_side": "ask", "price": o["price"], "arb": True,
                                                    "fv": fvs.get(o["exchangeId"]), "t": time.time(),
                                                    "eid": o["exchangeId"]}
                self.notes_dirty = True
        if len(set(traded)) > 1:
            alert(f"arbitrage on {race} only partly filled {traded}: the difference is now ordinary "
                  f"inventory, which the quoting will work off")
        else:
            log.info("arbitrage on %s: every leg filled %.0f", race, traded[0] if traded else 0)

    # ------------------------------------------------------------------------------ taking stale quotes
    def take_stale_quotes(self, refs, liquid, fvs, inv, mine_real, global_reduce, party_delta, now_m):
        """Trade against a tournament quote that Polymarket says is clearly wrong.

        On every new Polymarket reading, each market with a liquid Polymarket price gets a direction:
          +1  Polymarket is at least take_edge ABOVE the best other ask  -> that ask is cheap: buy it
          -1  Polymarket is at least take_edge BELOW the best other bid  -> that bid is rich: sell to it
        Only once every reading for take_confirm_seconds has shown the same direction (a spike that
        reverts never counts), the book is re-downloaded and, if the gap is still there, we trade.
        Size = quarter Kelly (see kelly_position), limited to what's offered at that price and to the
        cash-per-order cap. Never inside the pre-close window, when the worst-case cap is hit, or
        against the national-swing cap. Returns the exchanges traded (skipped by quoting this cycle).
        """
        cfg = self.cfg
        taken = set()
        version = getattr(self.refs, "version", 0)
        if not cfg.take_enabled or not self.refs or version == self.take_version_seen:
            return taken
        self.take_version_seen = version
        for eid, ex in self.ex.items():
            p = refs.get(eid)
            direction = self.take_direction(ex, p) if (p is not None and eid in liquid) else 0
            if direction != ex.take_dir:
                ex.take_since = now_m                     # new direction (or none): the clock starts again
            ex.take_dir = direction
        for eid, ex in self.ex.items():
            if (not self.running or not ex.take_dir or now_m - ex.take_since < cfg.take_confirm_seconds
                    or now_m < ex.take_until
                    or busy(ex, now_m) or global_reduce
                    or self.hours_to_close(ex) <= cfg.flatten_hours_before_close):
                continue
            no_bid, no_ask = self.party_blocks(ex, party_delta)
            if (ex.take_dir > 0 and no_bid) or (ex.take_dir < 0 and no_ask):
                continue
            try:                                          # the cached book may be old: check it's still there
                ex.book = strip_own(self.api.book(eid, self.tid), mine_real.get(eid, []))
                ex.book_time = ex.verified = time.monotonic()
            except ApiError as e:
                log.warning("take on %s skipped: book download failed (%s)", ex.label, e)
                continue
            if self.take_direction(ex, refs[eid]) != ex.take_dir:
                ex.take_dir = 0                           # the gap has closed: nothing to take
                continue
            if self.execute_take(ex, refs[eid], inv.get(eid, 0.0), fvs.get(eid), now_m):
                taken.add(eid)
        return taken

    def take_direction(self, ex, p):
        """+1 / -1 / 0: is Polymarket's probability p at least take_edge past the best other ask / bid?"""
        b = ex.book or {}
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        if best_ask is not None and p - best_ask >= self.cfg.take_edge - 1e-9:
            return 1
        if best_bid is not None and best_bid - p >= self.cfg.take_edge - 1e-9:
            return -1
        return 0

    def execute_take(self, ex, p, inv, fv, now_m):
        """Buy the stale best ask (direction +1) or sell to the stale best bid (-1). True if an order was sent."""
        cfg, bank = self.cfg, self.bankroll()
        buy = ex.take_dir > 0
        level = ex.book["asks" if buy else "bids"][0]
        price = level["price"]
        limit = kelly_position(p, price, bank, cfg, yes=buy)             # most we'd hold on this side
        room = limit - inv if buy else limit + inv                       # how much more that allows
        # Tail guard, same rule as for quotes: near 0 or 1 never take the side that risks ~1 a share to earn
        # a few cents (buying YES near 1, or selling YES = buying NO near 0), except to shrink a position.
        if fv is not None and ((buy and fv > cfg.tail_high) or (not buy and fv < cfg.tail_low)):
            room = min(room, max(0.0, -inv) if buy else max(0.0, inv))
        cost = price if buy else 1 - price
        qty = int(min(level["quantity"], room, cfg.max_order_cash_frac * bank / max(cost, TICK)))
        ex.take_until = now_m + cfg.take_cooldown_seconds
        ex.take_dir = 0                                                  # a new gap must be confirmed afresh
        if qty < 1:
            return False
        log.warning("%sTAKE %s: Polymarket %.3f vs stale %s %.3f -> %s %d YES at %.3f",
                    "" if self.api.live else "[dry] ", ex.label, p, "ask" if buy else "bid", price,
                    "buying" if buy else "selling", qty, price)
        self.takes_total += 1
        if not self.api.live:
            return True
        # Pull our own quotes here first (so we can't trade with ourselves), then take, then cancel leftovers.
        if not self.cancel(ex.eid, [], whole_exchange=True):
            return False
        order = {"exchangeId": ex.eid, "side": "yes", "action": "buy" if buy else "sell", "quantity": qty,
                 "price": price, "tournamentId": self.tid,
                 "expirationDate": iso(utcnow() + timedelta(seconds=cfg.take_order_ttl))}
        self.orders_stale = True
        try:
            results = self.api.place_batch([order])
        except ApiError as e:
            ex.pending_until = now_m + cfg.pending_seconds
            alert(f"take on {ex.label} failed ({e}) - check positions")
            if e.code in FATAL_API_CODES:
                fatal(f"orders rejected with {e.code}")
            return True
        data = (results[0] if results else {}).get("data") or {}
        if (results[0] if results else {}).get("ok"):
            self.remember_order(order, data, now_m)   # so a leftover whose cancel fails is still known about
        self.cancel(ex.eid, [], whole_exchange=True, quiet=True)
        if data.get("orderId") is not None:                # so these fills show up in `report`
            self.order_meta[data["orderId"]] = {"our_side": "bid" if buy else "ask", "price": price, "take": True,
                                                "fv": fv, "t": time.time(), "eid": ex.eid}
            self.notes_dirty = True
        log.info("take on %s: traded %s of %d", ex.label, data.get("quantityTraded", "?"), qty)
        return True

    # ------------------------------------------------------------------------------ fills
    def log_fills(self, fvs):
        """Fetch fills newer than the last one we logged (paging back up to max_fill_pages pages), and
        update our record of resting orders: an order's shares left = what we placed - what has filled
        (each fill is new exactly once, so nothing is counted twice)."""
        new, cursor = [], None
        for _ in range(self.cfg.max_fill_pages):
            page = self.api.fills_page(cursor)
            batch = page.get("data", [])
            fresh = [f for f in batch if str(f["id"]) not in self.fills.seen]
            new += fresh
            if len(fresh) < len(batch) or not page.get("pagination", {}).get("hasMore"):
                break
            cursor = page["pagination"]["nextCursor"]
        if new:
            self.fills.record(new, self.order_meta, fvs)
        for f in reversed(new):                               # oldest first
            oid = f.get("orderId")
            o = self.my_orders.get(oid)
            if o is None:
                continue
            if oid not in self.placed_qty:
                self.orders_stale = True                      # an order we didn't place this run: re-read the list
                continue
            self.filled_qty[oid] += abs(float(f.get("quantity") or 0))
            o.qty = min(o.qty, self.placed_qty[oid] - self.filled_qty[oid])
            if o.qty <= 0:                                    # fully filled: not resting any more
                self.forget_orders([oid])
        cutoff = time.time() - 86400                          # forget order notes older than a day
        kept = {k: v for k, v in self.order_meta.items() if v["t"] > cutoff}
        if len(kept) != len(self.order_meta):
            self.order_meta, self.notes_dirty = kept, True
        self.save_order_notes()

    def load_order_notes(self):
        """Order notes saved by a previous run (so fills that land around a restart get attributed)."""
        try:
            with open(bot_path(self.cfg.order_notes_file)) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            return {}

    def save_order_notes(self):
        if self.notes_dirty and self.api.live:                # dry-run notes are fake: don't save them
            write_json(bot_path(self.cfg.order_notes_file), {str(k): v for k, v in self.order_meta.items()})
            self.notes_dirty = False

    # ------------------------------------------------------------------------------ recording (#10)
    def open_recorder(self):
        """SQLite file of market snapshots: what the books, our fair value, the outside reference and
        our quotes looked like over time. For tuning settings afterwards; costs no extra requests.
        Open it with any SQLite viewer, or pandas: pd.read_sql("select * from snapshots", conn)."""
        if not self.cfg.record_file:
            return None
        db = sqlite3.connect(bot_path(self.cfg.record_file), check_same_thread=False)
        db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                          ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL,
                          fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS account (
                          ts TEXT, mode TEXT, account_value REAL, locked_in_orders REAL,
                          worst_case_loss REAL, party_delta REAL, orders_resting INTEGER)""")
        db.execute("CREATE INDEX IF NOT EXISTS snapshots_eid_ts ON snapshots (eid, ts)")
        db.commit()
        return db

    def record(self, fvs, now_m):
        """One row per market (best bid/ask incl. ours, fair value, reference, our quote, position)."""
        if not self.db or now_m - self.last_record < self.cfg.record_seconds:
            return
        self.last_record = now_m
        ts, mode, h = iso(utcnow()), "live" if self.api.live else "dry", self.health
        rows = [(ts, mode, eid, ex.label, *self.last_tops.get(eid, (None, None)), fvs.get(eid), ex.ref,
                 ex.quote.bid, ex.quote.ask, ex.inv) for eid, ex in self.ex.items()]
        try:
            self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.execute("INSERT INTO account VALUES (?,?,?,?,?,?,?)",
                            (ts, mode, h.get("account_value"), h.get("locked_in_orders"), h.get("worst_case_loss"),
                             h.get("party_delta"), h.get("orders_resting")))
            self.db.commit()
        except sqlite3.Error as e:
            log.warning("could not record snapshot: %s", e)

    # ------------------------------------------------------------------------------ phone summary (#12)
    def maybe_summary(self):
        """Every summary_every_hours, on the hour UTC (2 = 00:00, 02:00, 04:00...), push a summary to your
        phone (live only), also while waiting for the open. First line: the bot's status - "OK" or the
        ISSUES since the previous summary (then the title says ISSUES and it's sent at high priority).
        Then P&L, rank, Smart Score, fills since the previous summary and bot health. If an update
        doesn't arrive on time, the bot itself is down. Costs 2-3 requests (P&L, rank, Smart Score)."""
        every, now = self.cfg.summary_every_hours, utcnow()
        slot = (now.date(), now.hour)
        if every <= 0 or not self.api.live or now.hour % every or self.last_summary_slot == slot:
            return
        self.last_summary_slot = slot
        value = self.health.get("account_value")
        marks = self.counts_at_last_summary
        status, problems = self.status_report()
        try:                                  # a report must never be able to disturb trading
            title, message = build_summary(self.api, bot_path(self.cfg.fills_csv), self.initial_balance, value=value,
                                           value_prev=self.value_at_last_summary,
                                           arbs=self.arbs_total - marks["arbs"], takes=self.takes_total - marks["takes"],
                                           health=self.health, hours=every, status=status)
        except Exception as e:
            log.warning("phone summary failed (%s) - skipped", e)
            return
        if problems:
            title = title.replace("mm_bot:", "mm_bot ISSUES:", 1)
        log.info("%s\n%s", title, message)
        notify(message, title=title, priority="high" if problems else "default",
               tags="warning" if problems else "chart_with_upwards_trend")
        self.value_at_last_summary = value
        self.counts_at_last_summary = {"arbs": self.arbs_total, "takes": self.takes_total,
                                       "errors": self.errors_total, "rate_limits": getattr(self.api, "rate_limited", 0)}

    def status_report(self):
        """(status line, problems) for the phone summary: what the bot is doing right now, and anything
        that has gone wrong since the previous summary. Problems are things you may want to look at."""
        h, cfg, marks = self.health, self.cfg, self.counts_at_last_summary
        errors = self.errors_total - marks["errors"]
        rate_limits = getattr(self.api, "rate_limited", 0) - marks["rate_limits"]
        problems = []
        if self.pulled_after_errors:
            problems.append("all quotes PULLED after repeated errors")
        if self.failed_cycles:
            problems.append(f"the last {self.failed_cycles} cycle(s) failed")
        elif errors:
            problems.append(f"{errors} failed cycle(s) since the last update, recovered since")
        if rate_limits:
            problems.append(f"{rate_limits} rate limit(s) (429) since the last update")
        if h.get("realtime") == "reconnecting":
            problems.append("realtime feed down, polling meanwhile")
        if h.get("reduce_only"):
            problems.append("reduce-only: worst-case loss above the cap")
        mapped = len(getattr(self.refs, "mapping", {}) or {}) if self.refs else 0
        if mapped and h and h.get("reference_prices", 0) < 0.5 * mapped:
            problems.append(f"Polymarket prices missing ({h.get('reference_prices', 0)} of {mapped})")
        phase = self.phase
        if phase == "trading":
            hrs = min((self.hours_to_close(ex) for ex in self.ex.values()), default=float("inf"))
            if hrs * 60 <= cfg.stop_minutes_before_close:
                phase = "stopped for settlement"
            elif hrs <= cfg.exit_hours_before_close:
                phase = f"election night: exiting positions ({hrs:.1f} h to close)"
            elif hrs <= cfg.flatten_hours_before_close:
                phase = f"election night: reducing positions ({hrs:.1f} h to close)"
            elif h and not h.get("orders_resting"):
                problems.append("no orders resting")
            if self.selftest_passed:
                phase += ", self-test passed"
        return ("Status: " + ("OK" if not problems else "ISSUES - " + "; ".join(problems)) + f" | {phase}"), problems

    def write_status(self, ok):
        """status.json: a one-glance health check, e.g. `cat status.json` over ssh."""
        try:
            write_json(bot_path(self.cfg.status_file), {
                "updated": iso(utcnow()), "mode": "live" if self.api.live else "dry run",
                "last_cycle_ok": ok, "failed_cycles_in_a_row": self.failed_cycles,
                "quotes_pulled_after_errors": self.pulled_after_errors, **self.health})
        except OSError as e:
            log.warning("could not write status file: %s", e)

    # ------------------------------------------------------------------------------ dry-run helpers
    def sim_by_eid(self):
        out = defaultdict(list)
        for o in list(self.sim.values()):
            if o.expires and o.expires <= utcnow():
                del self.sim[o.order_id]                      # simulated expiry, like the real engine
            else:
                out[o.eid].append(o)
        return out

    def sim_orders(self, eid):
        return [o for o in self.sim.values() if o.eid == eid]

    # ------------------------------------------------------------------------------ main loop
    def request_stop(self, *_):
        """Ctrl+C / kill only raises a flag. The loop stops touching the book, then shutdown()
        cancels everything. Cancelling inside the handler would race the cycle, which could post
        fresh orders right after the cancel. A second Ctrl+C forces an immediate exit."""
        if not self.running:
            raise KeyboardInterrupt
        log.info("stop requested - finishing the current request, then cancelling all orders (Ctrl+C again to force)")
        self.running = False

    def sleep_until(self, t):
        while self.running and time.monotonic() < t:
            time.sleep(min(0.25, max(0.0, t - time.monotonic())))

    # Exchange answers that mean "busy, try again later" rather than "this API doesn't work the way we think":
    # a network error or timeout (0), the same request still in flight (409), rate limited, server trouble.
    SELFTEST_BUSY = (0, 409, 429, 500, 502, 503, 504)

    def self_test(self):
        """Live mode, straight after the first quotes go out (so they aren't delayed at the open): check the
        exchange behaves the way this bot assumes, using two 1-share orders at the most extreme prices
        allowed (a bid at 0.005 and an ask at 0.995), so they almost certainly won't trade, and if they do
        it costs about half a cent. Checks:
          1. the batch order is accepted and returns order ids,
          2. both show up in our open orders with side / action / priceLimit / quantity / expirationDate,
          3. "sell YES @ 0.995" reads back as an ask at 0.995 (the engine may store it as buy NO @ 0.005),
          4. the expiry time is kept, and cancel-all removes them.
        Anything unexpected -> alert + stop with exit code 3, so a human looks before real quoting.
        A BUSY exchange (timeout, 409 in flight, 429, 5xx: day one's open) is not a failure: the test is
        simply tried again selftest_retry_seconds later. Returns True = passed (or not needed), False = busy.
        This runs the test here and now (used by tests and tools); the main loop uses selftest_tick(),
        which runs the same test on a background thread so slow writes never hold up quoting."""
        if not self.selftest_needed():
            return True
        eid = self.selftest_start()
        return self.selftest_finish(eid, self.selftest_run(eid, self.cfg.order_ttl))

    def selftest_needed(self):
        return self.cfg.selftest_enabled and self.api.live and bool(self.ex) and not self.selftest_passed

    def selftest_start(self):
        """Pick the test exchange and keep the bot's own quoting off it until the test is over (the test's
        clean-up cancels everything there)."""
        # On the quietest ordinary market: the test's clean-up cancels our quotes there too, and that must not
        # cost a big market its place in line at the open.
        quiet = [e for e in sorted(self.ex) if self.ex[e].group not in self.cfg.headline_races] or sorted(self.ex)
        eid = min(quiet, key=lambda e: self.size_plan.get(e, 0))
        # Remember any earlier "outcome unknown" hold on it, so finishing the test doesn't lift it early.
        self.selftest_hold = self.ex[eid].pending_until
        self.ex[eid].pending_until = float("inf")
        self.selftest_started = self.selftest_started or time.monotonic()
        self.selftest_gen = self.cancel_gen
        return eid

    def selftest_run(self, eid, ttl):
        """One test (API calls only, no bot state touched, so it can run on any thread).
        Returns (verdict, problems, ttl): verdict "passed", "failed" or "busy"."""
        verdict, problems = self.selftest_attempt(eid, ttl)
        if verdict == "rejected" and ttl > 600:
            # The docs allow any future expiry, but if the exchange caps it, fall back rather than stop.
            log.warning("self-test: %.0f-min orders were rejected (%s) - trying 10-min ones", ttl / 60, problems[0])
            ttl = 600.0
            verdict, problems = self.selftest_attempt(eid, ttl)
        return ("failed" if verdict == "rejected" else verdict), problems, ttl

    def selftest_attempt(self, eid, ttl):
        problems, busy, rejected = [], False, False
        exp = iso(utcnow() + timedelta(seconds=ttl))              # the same expiry real quotes use
        orders = [{"exchangeId": eid, "side": "yes", "action": act, "quantity": 1, "price": px,
                   "tournamentId": self.tid, "expirationDate": exp} for act, px in (("buy", PMIN), ("sell", PMAX))]
        try:
            # Our own quotes there can't be repriced while the test runs: take them off first.
            self.api.cancel_all(self.tid, eid)
            results = self.api.place_batch(orders)
            if len(results) != 2 or not all(r.get("ok") and (r.get("data") or {}).get("orderId") is not None
                                            for r in results):
                problems.append(f"batch response not as expected: {str(results)[:300]}")
                rejected = len(results) == 2 and not any(r.get("ok") for r in results)
                busy = any(r.get("status") in self.SELFTEST_BUSY for r in results)
            else:
                # The open-orders list can lag the exchange (up to recent_order_grace_seconds): keep looking.
                # Test orders that never show up at all are reported as "not listed" (busy the first time).
                give_up = time.monotonic() + self.cfg.recent_order_grace_seconds
                while True:
                    found = self.selftest_check(self.api.open_orders(self.tid, eid))
                    if not found or time.monotonic() >= give_up:
                        break
                    time.sleep(min(2.0, max(0.0, give_up - time.monotonic())))
                problems += found
        except ApiError as e:
            problems.append(f"API error: {e}")
            busy = e.status in self.SELFTEST_BUSY
        finally:
            try:                                                  # also removes our quotes there (re-placed later)
                if not self.api.cancel_all(self.tid, eid):
                    problems.append("cancel-all didn't confirm the test orders were gone")
            except ApiError as e:
                problems.append(f"cancel-all failed: {e}")
                busy = busy or e.status in self.SELFTEST_BUSY
        if not problems:
            return "passed", []
        if not busy and not rejected and problems[0] == self.SELFTEST_NOT_LISTED:
            return "not listed", problems
        return ("busy" if busy else "rejected" if rejected else "failed"), problems

    SELFTEST_NOT_LISTED = "neither test order is in our open orders"

    @classmethod
    def selftest_check(cls, raw):
        problems = []
        missing = sorted({f for o in raw for f in ("id", "side", "action", "priceLimit", "quantity",
                                                   "expirationDate") if f not in o})
        if missing:
            problems.append(f"open orders are missing fields {missing}")
        mine = [o for o in map(parse_order, raw) if o]
        if not any(o.qty == 1 and (abs(o.price - PMIN) < 1e-9 or abs(o.price - PMAX) < 1e-9) for o in mine) \
                and not missing:
            return [cls.SELFTEST_NOT_LISTED]
        if not any(o.is_bid and abs(o.price - PMIN) < 1e-9 and o.qty == 1 for o in mine):
            problems.append("the test bid at 0.005 isn't in our open orders")
        if not any(not o.is_bid and abs(o.price - PMAX) < 1e-9 and o.qty == 1 for o in mine):
            problems.append("the test 'sell YES @ 0.995' didn't read back as an ask at 0.995")
        if mine and not all(o.expires for o in mine):
            problems.append("the orders' expiry time wasn't kept")
        return problems

    def selftest_finish(self, eid, outcome):
        """Main thread: act on a test's outcome. True = passed."""
        verdict, problems, ttl = outcome
        ex = self.ex.get(eid)
        if ex:
            ex.pending_until = self.selftest_hold if self.selftest_hold != float("inf") else 0.0
        if verdict == "failed" and self.cancel_gen != self.selftest_gen:
            # The bot itself cancelled everything while the test ran (error recovery, kill switch...):
            # its orders may have vanished for that reason, so this proves nothing. Try again.
            verdict, problems = "busy", ["our own cancel-all ran during the test"] + problems
        if verdict == "not listed":
            # Accepted, but never listed: the list may just be lagging a busy exchange. Twice in a row = real.
            self.selftest_unlisted += 1
            verdict = "busy" if self.selftest_unlisted < 2 else "failed"
        elif verdict == "error":
            self.selftest_errors += 1
            verdict = "busy" if self.selftest_errors < 3 else "failed"
        # The clean-up cancelled whatever we had resting there: drop it from our record and re-read the list.
        self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid])
        self.orders_stale = True
        if verdict == "passed":
            if ttl != self.cfg.order_ttl:
                alert(f"orders expiring in {self.cfg.order_ttl / 60:.0f} min were rejected but {ttl / 60:.0f}-min "
                      f"ones work - using {ttl / 60:.0f}-min orders from now on")
                self.cfg.order_ttl, self.cfg.refresh_before_expiry = ttl, ttl / 5
            log.info("self-test passed: orders, sell->NO conversion, expiry and cancel all behave as expected")
            self.selftest_passed = True
            return True
        if verdict == "busy":
            waited = time.monotonic() - (self.selftest_started or time.monotonic())
            self.selftest_next = time.monotonic() + self.cfg.selftest_retry_seconds
            log.warning("self-test: exchange busy (%s) - trying again in %.0f s, quoting meanwhile",
                        problems[0] if problems else "?", self.cfg.selftest_retry_seconds)
            if waited >= self.cfg.selftest_alert_after and not self.selftest_alerted:
                self.selftest_alerted = True
                alert(f"self-test still not done after {waited / 60:.0f} min: the exchange keeps answering busy "
                      f"({problems[0] if problems else '?'}). Quoting continues; it keeps retrying")
            return False
        fatal("self-test failed before trading - " + "; ".join(problems))

    def selftest_tick(self):
        """Main loop, after every cycle: run the self-test on a background thread, without ever waiting for
        it (on day one, order writes took 15-30 s at the open), and act on its result once it's in."""
        if not self.selftest_needed():
            return
        f = self.selftest_future
        if f is not None:
            if f.done():
                self.selftest_future = None
                try:
                    outcome = f.result()
                except Exception as e:            # a bug in the test itself: retried, fatal the 3rd time running
                    outcome = ("error", [f"self-test error: {e}"], self.cfg.order_ttl)
                self.selftest_finish(self.selftest_eid, outcome)
            return
        if time.monotonic() >= self.selftest_next:
            self.selftest_eid = self.selftest_start()
            self.selftest_future = self.selftest_pool.submit(self.selftest_run, self.selftest_eid, self.cfg.order_ttl)

    def start_feed(self):
        """Start the realtime feed if it's enabled and the `realtime` package is installed."""
        if not self.cfg.realtime_enabled:
            return None
        try:
            import realtime  # noqa: F401  (only checking it's installed)
        except ImportError:
            log.warning("realtime package not installed (pip install realtime) - polling every %.0f s instead",
                        self.cfg.loop_seconds)
            return None
        feed = RealtimeFeed(self.api, self.tid, self.cfg)
        feed.start()
        return feed

    def wait_for_next_cycle(self, t0):
        """Wait at least min_cycle_seconds (batches events), then until the realtime feed pushes something
        or a Polymarket reading wakes us (see on_reference_prices), or loop_seconds pass."""
        self.sleep_until(t0 + self.cfg.min_cycle_seconds)
        deadline = t0 + self.cfg.loop_seconds
        while self.running and time.monotonic() < deadline:
            if (self.wake.is_set() or (self.feed and self.feed.healthy() and self.feed.wake.is_set())
                    or (self.writes and self.write_done.is_set())):
                break
            self.wake.wait(timeout=0.05)
        self.wake.clear()

    def wait_for_trading(self):
        """Orders are rejected until the tournament is 'active' (it's 'draft' before the start date).

        Far from the start: check every start_check_seconds and use the wait to download the (house-
        seeded) books a few at a time, so quoting can start at once. In the last open_quiet_seconds:
        no requests at all (keeps the per-minute budget free for the first quotes), sleep until the
        start time, then check every open_poll_seconds, so the first quotes go out within ~1 s of the
        open. First in line at a price gets filled first, and every bot will want the same prices."""
        cfg = self.cfg
        while self.running:
            try:
                t = self.api.tournament()
            except ApiError as e:             # this loop may run for days; a blip mustn't kill it
                log.warning("tournament check failed (%s) - retrying in %.0f s", e, cfg.start_check_seconds)
                self.sleep_until(time.monotonic() + cfg.start_check_seconds)
                continue
            if t.get("status") == "active":
                return
            if t.get("status") == "ended":
                log.error("tournament has ended")
                self.running = False
                return
            try:
                start = parse_ts(t.get("startDate"))
            except (ValueError, AttributeError):
                start = None                      # unreadable start time: just keep checking slowly
            to_start = (start - utcnow()).total_seconds() if start else float("inf")
            if to_start > cfg.open_quiet_seconds:
                try:
                    self.refresh_books({}, time.monotonic())
                except Exception as e:
                    log.warning("pre-open book download failed: %s", e)
                ready = sum(e.book is not None for e in self.ex.values())
                self.phase = (f"waiting for the open ({start:%d %b %H:%M} UTC), {ready}/{len(self.ex)} order books ready"
                              if start else f"waiting for the open, {ready}/{len(self.ex)} order books ready")
                wait = min(cfg.start_check_seconds, to_start - cfg.open_quiet_seconds)
                log.info("tournament is '%s' (starts %s) - %d/%d books ready - checking again in %.0f s",
                         t.get("status"), t.get("startDate"), ready, len(self.ex), wait)
            elif to_start > 0:
                wait = to_start                   # quiet until the start time
                log.info("opening in %.0f s - quiet until then, then checking every %.0f s",
                         to_start, cfg.open_poll_seconds)
            elif to_start > -cfg.start_check_seconds:
                wait = cfg.open_poll_seconds      # start time reached: check every second
            else:
                wait = cfg.start_check_seconds    # well past the start and still not open: back to slow checks
                log.info("tournament still '%s' %.0f s after its start time - checking every %.0f s",
                         t.get("status"), -to_start, wait)
            self.maybe_summary()                  # updates while waiting too, so you know it's alive
            self.sleep_until(time.monotonic() + wait)

    def on_cycle_error(self, what, pull_now):
        """API errors are usually transient: tolerate a few, then pull every quote until healthy.
        Unexpected (non-API) errors may be a bug in our own logic, so those pull quotes at once."""
        self.failed_cycles += 1
        self.errors_total += 1
        if (pull_now or self.failed_cycles >= self.cfg.max_failed_cycles) and not self.pulled_after_errors:
            alert(f"{what} - pulling all quotes until cycles succeed again")
            try:
                self.cancel_everything()
                self.pulled_after_errors = True
            except Exception as e:        # never let the error handler itself crash the bot
                log.error("cancel-all failed too (%s) - orders expire within %.0f min anyway", e, self.cfg.order_ttl / 60)

    def run(self):
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)   # `systemctl stop` sends this
        kill_file = bot_path(self.cfg.kill_file)
        if self.api.live and os.path.exists(kill_file):
            log.critical("the kill switch fired earlier (%s). Check what happened, then delete that file to trade again.",
                         kill_file)
            self.exit_code = EXIT_KILLED
            return
        try:
            # Started before the open, so both are warm at the first cycle: Polymarket prices refresh in
            # the background from now on, and the realtime feed is connected.
            if self.refs:
                self.refs.start()
            self.feed = self.start_feed()
            if self.api.live:
                alert("bot starting (live)")
                self.wait_for_trading()
                if self.running:
                    log.info("clean slate: cancelling any orders left over from before")
                    self.cancel_everything()
            self.phase = "trading"
            while self.running:
                t0 = time.monotonic()
                try:
                    if t0 - self.last_reload > self.cfg.market_reload_seconds:
                        self.load_markets()
                    self.cycle()
                    if self.running:
                        self.selftest_tick()      # stops the bot (exit code 3) if the API surprises us
                    self.failed_cycles, self.pulled_after_errors = 0, False
                    self.write_status(ok=True)
                    self.maybe_summary()
                except ApiError as e:
                    if e.code in FATAL_API_CODES:          # e.g. key revoked: retrying forever won't help
                        fatal(f"API says {e.code}: {e}")
                    log.error("cycle failed: %s", e)
                    self.on_cycle_error(f"{self.failed_cycles + 1} failed cycles", pull_now=False)
                    self.write_status(ok=False)
                except Exception:
                    log.exception("unexpected error")
                    self.on_cycle_error("unexpected error", pull_now=True)
                    self.write_status(ok=False)
                self.wait_for_next_cycle(t0)
        finally:
            if self.feed:
                self.feed.stop()
            self.shutdown()
            self.close()

    def close(self):
        """Release the thread pool and the recording database (after shutdown has cancelled orders)."""
        if self.refs and hasattr(self.refs, "stop"):
            self.refs.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.writer.shutdown(wait=False, cancel_futures=True)
        self.selftest_pool.shutdown(wait=False, cancel_futures=True)
        if self.db:
            self.db.close()
            self.db = None

    def shutdown(self):
        """Always runs on exit (Ctrl+C, kill switch, crash): cancel every order we have."""
        if not self.api.live:
            log.info("dry run finished (no real orders to cancel)")
            return
        # An order write still in flight could land after the cancel-all and be left resting: drop the queued
        # ones, wait for those already sent, and cancel again below if any are still running after that.
        self.writer.shutdown(wait=False, cancel_futures=True)
        self.drain_writes(timeout=2 * self.cfg.request_timeout)
        self.notes_dirty = True
        self.save_order_notes()
        for attempt in range(self.cfg.shutdown_cancel_attempts):
            try:
                if self.cancel_everything():
                    if self.writes:               # a write was still running: once it ends, cancel once more
                        self.drain_writes(timeout=2 * self.cfg.request_timeout)
                        self.cancel_everything()
                    log.info("all orders cancelled")
                    alert("bot stopped, all orders cancelled")
                    return
            except Exception as e:
                log.error("cancel attempt %d failed: %s", attempt + 1, e)
            time.sleep(1)
        alert(f"bot stopped but COULD NOT CONFIRM CANCELLATION - check the web UI "
              f"(orders expire within {self.cfg.order_ttl / 60:.0f} min anyway)")

# =============================================================================================
# CLI
# =============================================================================================

def setup_logging(to_file):
    """Log to the terminal (which systemd/journalctl also captures) and, for `run`, to a rotating
    file. Timestamps are UTC with the date, to match the API and make multi-day logs readable."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt.converter = time.gmtime
    handlers = [logging.StreamHandler()]
    if to_file and CFG.log_file:
        handlers.append(logging.handlers.RotatingFileHandler(
            bot_path(CFG.log_file), maxBytes=int(CFG.log_max_mb * 1e6), backupCount=CFG.log_backups))
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    setup_logging(to_file=(cmd == "run"))
    if cmd == "report":
        return report(bot_path(CFG.fills_csv))
    if not CFG.slug and cmd != "tournaments":
        fatal("TOURNAMENT_SLUG is not set (find it with: python mm_bot.py tournaments)")
    live = "--live" in sys.argv or cmd == "cancel"
    api = Api(CFG, live)

    if cmd == "tournaments":
        print(json.dumps(api.get("/tournaments", limit=100), indent=2))
    elif cmd == "markets":
        for m in api.markets():
            print(f"\n[{m['id']}] {m.get('title')}  multi={m.get('isMultiOutcome')} closes={m.get('settlementDate')}")
            for e in m.get("exchanges", []):
                print(f"   exchange {e['id']:>8}  {str(e.get('option')):<24} last={e.get('latestPrice')}")
    elif cmd == "status":
        t = api.tournament()
        print(json.dumps({k: t.get(k) for k in ("name", "status", "startDate", "endDate", "myBalance", "initialBalance")}, indent=2))
        try:
            print(json.dumps(api.pnl(), indent=2))
        except ApiError as e:
            print("P&L unavailable:", e)
        print(json.dumps(api.positions(), indent=2))
        orders = api.open_orders(t["id"])
        print(f"{len(orders)} open orders")
        print(json.dumps(orders[:20], indent=2))
    elif cmd == "cancel":
        print("done" if api.cancel_all(api.tournament()["id"]) else "some orders may remain - check UI")
    elif cmd == "summary":
        t = api.tournament()
        title, message = build_summary(api, bot_path(CFG.fills_csv), float(t.get("initialBalance") or DEFAULT_BANKROLL))
        print(title, message, sep="\n")
        if not CFG.alert_url:
            print("\n(not sent: ALERT_URL isn't set)")
        else:
            print("\nsent to your phone" if notify(message, title=title, tags="chart_with_upwards_trend")
                  else "\nSENDING FAILED - check ALERT_URL and your internet connection")
    elif cmd == "run":
        log.info("Mode: %s", "LIVE" if live else "DRY RUN (add --live to trade)")
        bot = Bot(api, CFG)
        bot.run()
        sys.exit(bot.exit_code)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
