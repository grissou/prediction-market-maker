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
    python mm_bot.py analyze [hours]  per-market edge, markouts, P&L, time at the top, undercuts (local files only)
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
import bisect
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
import traceback
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
    reserved_cash_mode: str = "ignore"    # does the API's account value leave out cash locked in our open orders?
                                          #   "auto" = work it out from the first orders we post, "add" = yes, add
                                          #   it back, "ignore" = no, use the API's number as is. Day one settled it:
                                          #   "ignore" (API value - locked stayed 99.2-101.3k while locked moved
                                          #   0-48k, corr 0.9993; "auto" never decided and added it back, inflating
                                          #   the kill switch, the worst-case cap and the summaries by up to 48k)
    reserved_calib_min: float = 1000.0    # "auto" only judges when locked cash changed by at least this much...
    reserved_calib_votes: int = 2         # ...and needs this many agreeing observations before deciding
    max_worst_case_frac: float = 0.30     # settlement risk (risk_model) > 30% of account value -> reduce-only everywhere
    risk_model: str = "correlated"        # R7. "sum_max" = the old measure: add up every race's own worst outcome, as
                                          #   if all ~75 races went wrong at once (day one: 23.3k, growing 1.5k/h with
                                          #   breadth, while the settlement sd was ~4.4k and a 10c national swing cost
                                          #   +-249). "correlated" = national swing shock + risk_z x sd of the rest:
    risk_swing_shock: float = 0.15        #   every Republican price up 15c and Democratic down 15c (or the reverse)...
    risk_z: float = 3.0                   #   ...plus 3 standard deviations of the independent race outcomes
    worst_case_backstop_frac: float = 0.69  # with "correlated": the old sum-of-maxima still forces reduce-only above
                                            #   69% of account value (~70k on 2026-10-02, the owner's choice; a
                                            #   backstop that no longer binds at 30%)
    reduce_only_hysteresis: float = 0.03  # once in reduce-only, leave only below max_worst_case_frac - this (and the
                                          #   backstop - this): risk hovering at 30% used to flip reduce-only every
                                          #   cycle, each flip pulling (then re-placing) a side on every market held.
                                          #   0 = no hysteresis (the old behaviour)
    pulls_cancel_all_over: int = 25       # more pulls than this in one cycle (or more than the writes left this
                                          #   minute, when that's also more than re-placing every quote costs): one
                                          #   tournament-wide cancel-all (1 write) instead of one DELETE each; what
                                          #   should rest is re-placed next cycle in batches. 0 = off. Not with
                                          #   ONLY_EXCHANGES (its cancel-all is one write per exchange anyway)
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
    limits_use_race_net: bool = True      # the position limits (max_position_frac, Kelly, headline_position_frac)
                                          #   also apply to the RACE-NETTED position (effective_inventory), on the side
                                          #   that grows it: a short Rep leg counts toward the Dem leg's limit and vice
                                          #   versa. 2 Oct: long 10,834 Dem House AND short 9,396 Rep House = the same
                                          #   ~20k bet twice under a 10k per-leg limit. The per-market limit still
                                          #   applies; the side that shrinks the netted position is never limited by
                                          #   this. False = per-market only (the old behaviour)
    capital_in_positions_max_frac: float = 0.75  # capital ceiling: positions worth more than 75% of account value ->
                                          #   every market's ADDING side (the one growing |race-netted position|) is
                                          #   sized by capital_ceiling_adding_size_factor, the reducing side quotes as
                                          #   usual, until it is back below this - 0.05. 2 Oct: 90.5k of 101k sat in
                                          #   positions, 11k cash left to quote with. 0 = off
    capital_ceiling_adding_size_factor: float = 0.5   # ...0 = adding side not quoted at all, 0.5 = half size
    mark_frag_enabled: bool = False       # mark-fragility cap (sizing only): a position's mark noise = |position| x sd
                                          #   of the 10-min change of the tournament mid (recorder snapshots). The
                                          #   ADDING side's position limit = max(one quote, mark_frag_max_step_cash / sd).
                                          #   1 Oct snapshot: 1,186 $ per 10-min step over 159 positions, RI Senate legs
                                          #   200 and 181 alone (analysis/mark_fragility.py). Off until checked on live
                                          #   data; the numbers are in status.json either way (mark_frag_*)
    mark_frag_max_step_cash: float = 100.0  # ...at most this many $ of mark noise per 10-min step from one position
    mark_frag_window_hours: float = 24.0  # ...sd over the last this many hours of snapshots (refreshed every 30 min)
    mark_frag_min_samples: int = 60       # ...fewer 10-min changes than this in the window -> no estimate, no cap
    mark_frag_floor_sd: float = 0.002     # ...an sd below 0.2c counts as 0.2c, so the limit never explodes
    behind_best_size_enabled: bool = False   # behind-the-best sizing: an ADDING quote (the side growing |race-netted
                                          #   position|) resting behind_best_ticks or more behind the best OTHER
                                          #   trader's price on its side is sized x behind_best_size_factor (never
                                          #   below behind_best_min_size). Round 2: P(fill in 10 min) 33% at the best,
                                          #   6-8% one tick behind, 2-5% two+ behind, ~33k of cash locked in those.
                                          #   Reducing side, empty sides and ref-only markets unchanged; a resting
                                          #   full-size order stays (it is a size factor, see Quote.bid_max)
    behind_best_ticks: int = 2            # ...at least this many ticks behind the best other price
    behind_best_size_factor: float = 0.5  # ...the adding side's size factor then (times the other factors)
    behind_best_min_size: int = 100       # ...never shrunk below this many shares (coverage and the first fill stay)
    mark_frag_total_max_cash: float = 0.0     # ...sum over positions of |pos| x sd above this -> every adding side
                                          #   withdrawn (like the capital ceiling at factor 0) until below 80% of it.
                                          #   Only with mark_frag_enabled. 0 = no total cap
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
    positions_stale_max_cycles: int = 0   # positions answering 409 "holdings cannot be valued": the last read is
    positions_stale_max_seconds: float = 120.0  # reused for at most this many cycles in a row OR this long, whichever
                                          #   comes first; then the cycle fails (an API error: after max_failed_cycles
                                          #   of those every quote is pulled until positions read again), because
                                          #   fills keep landing while inventory, limits and reduce-only stay frozen.
                                          #   0 = no limit on that count (both 0 = reuse forever, the old behaviour)

    # --- QUOTING -----------------------------------------------------------------------------
    min_edge: float = 0.01                # never quote closer than this to our reservation price
    max_half_spread: float = 0.04         # never quote further than this from it
    order_size_frac: float = 0.005        # shares per order when size_by_activity is off (and before the first
                                          #   plan): 500 shares at 100k. Normally QUOTE SIZES BY ACTIVITY decides
    skew_per_share: float = 0.00003       # skew_mode "share": reservation price moves 0.3c per 100 shares of
                                          #   race-adjusted inventory, the same in every market (the old rule)
    skew_mode: str = "quote"              # "quote" = scale the skew to the market's own quote size: holding one
                                          #   full quote's worth of shares moves the reservation price skew_per_quote.
                                          #   Day one, "share" moved it 12-24c after one 4-8k headline fill, so the
                                          #   next quotes sat through fair value: 42% of shares traded at or through
                                          #   our own fair value, -804 at the 60-min mid. "share" = the old rule
    skew_per_quote: float = 0.005         # 0.5c per quote-size of inventory...
    skew_max: float = 0.02                # ...at most 2c in total (both modes; 1.0 = no cap)
    max_skew_through: float = 0.0         # a skewed quote may sit at most this far THROUGH fair value (0 = at fair
                                          #   value at worst). Not applied in reduce-only/flatten, which must get out.
                                          #   1.0 = off (the old behaviour: up to max_half_spread through it)
    skew_age_enabled: bool = True         # AGE SKEW: a position held longer than skew_age_after_hours (share-weighted,
    skew_age_after_hours: float = 1.0     #   oldest shares first out) skews both quotes further toward unloading:
    skew_age_per_hour: float = 0.0025     #   0.25c per hour beyond that...
    skew_age_max: float = 0.02            #   ...at most 2c, added on top of the (separately capped) inventory skew.
                                          #   Never through fair value (max_skew_through). 2 Oct: median held share
                                          #   7.2 h old, 19% > 12 h. False = off (the old behaviour)
    improve_ticks: int = 1                # R4: quote this many ticks better than the best other trader (1 = penny,
                                          #   0 = join their price)
    undercut_step_back: float = 0.0       # R4: another trader already inside our min_edge band -> quote this far
                                          #   from the reservation price instead of at min_edge (0 = off)
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

    # --- R3 RESTING DEPTH LADDER ("sweep catcher", SIM_NOTES.md sections 4 and 7) ---------------
    # Students' market orders walk thin books (fills > 3c edge: +1,704 on 109 fills, DATA_REPORT_2 §5) and the
    # tournament mid reverts within 5-13 min. Extra resting orders BEHIND our touch quote (level 0) catch those
    # sweeps: levels 1..n at anchor -/+ ladder_offsets, sizes ladder_size_mults x the market's quote size. The
    # anchor is fair value when the ladder was (re)placed; it moves only when fair value moves ladder_move from it.
    # A level never sits at or inside level 0 and never crosses another trader's best price. Each side's total
    # (position + level 0 + ladder) stays within the position limits (Kelly / headline / race-netted / party cap).
    # Sim (12 markets, 128 seeds): +51 ± 9/h quiet, +34 ± 10/h news on +499/h; +12-14k cash in orders, +65% writes.
    ladder_enabled: bool = False          # OFF: needs free cash (2 Oct: ~11k of 101k free) - see ladder_min_cash_frac
    ladder_offsets: tuple = (0.015, 0.025, 0.035)   # levels 1..3: this far from the anchor...
    ladder_size_mults: tuple = (1.0, 2.0, 3.0)      # ...at these multiples of the market's quote size
    ladder_headline_offsets: tuple = (0.02, 0.04, 0.06, 0.08)   # party-control markets: wider levels...
    ladder_headline_mults: tuple = (0.25, 0.25, 0.5, 0.5)       # ...at fractions of their 10,000-share quote
    ladder_move: float = 0.02             # re-price the ladder only when fair value moved this far from its anchor
                                          #   (was 1c: fair value wobbling 0.50/0.51 re-placed it every cycle; the
                                          #   sim found holding vs re-anchoring often makes no P&L difference)
    ladder_pull_jump: float = 0.015       # Polymarket moved this far from its reading at the anchor -> pull the
    ladder_pull_seconds: float = 30.0     #   ladder for this long (cheap insurance on news nights)
    ladder_markets: str = "headline,busy" # which markets get one: "headline" (headline_races), "busy" (planned quote
    ladder_busy_size_frac: float = 0.01   #   size >= this fraction of the account: 1,000 shares at 100k), "quiet"
    ladder_min_cash_frac: float = 0.1     # no ladder unless free cash (account - positions - cash locked in non-ladder
                                          #   orders) stays >= 10% of the account (0.2 turns it off at 2 Oct's free cash);
                                          #   ladder cash also counts toward quote_capital_frac (with other locked cash)
    ladder_min_writes: int = 10           # no ladder writes while fewer than this many writes are left this minute
                                          #   (after every level-0 change: the ladder never delays a pull or a reprice)
    ladder_max_inv_quotes: float = 3.0    # |race-netted position| above this many quote sizes: ladder only on the
                                          #   side that reduces it

    # --- FAIR VALUE --------------------------------------------------------------------------
    fv_min_depth: int = 200               # skip price levels until this many shares have accumulated (anti-spoofing)
    max_spread_for_fv: float = 0.30       # book wider than this -> no reliable price -> don't quote
    # Thin books (R5). Day one 157-189 of 237 markets went unpriced although their books were two-sided and
    # tight (median spread 1c) and 97% had a Polymarket price: fewer than fv_min_depth shares at the top.
    # 72 held positions sat there, unquotable (Rep U.S. Senate +7,585 after 19:30).
    ref_only_enabled: bool = True         # price such a market from Polymarket alone, when Polymarket is liquid
                                          #   and the tournament's own (raw) mid is within ref_only_max_gap of it
    ref_only_max_gap: float = 0.03        #   (guards against a wrong match: there is no depth-checked book price)
    ref_only_min_edge: float = 0.015      # ...quoting wider than usual there...
    ref_only_size_frac: float = 0.001     # ...and small: 100 shares at 100k on the side that adds to a position...
    ref_only_reduce_full: bool = True     # ...while the side that shrinks one quotes the market's normal size (day one:
                                          #   Rep U.S. Senate +7,585 would otherwise leave 100 shares at a time)
    # 2026-10-02 08:08 restart: every ex.book was None and the books came in at 12-14 a minute, so R5 priced
    # 30 -> 101 markets in 6 min (162-171 before the restart). The bulk best bid/ask (3 requests for all 237)
    # is enough for R5's checks, which only look at the top of the book. The depth-checked fair value never
    # uses it (it needs fv_min_depth shares, i.e. a downloaded book).
    ref_only_use_tops: bool = True        # R5 may use the bulk best bid/ask when the book is missing or stale...
    tops_max_age: float = 120.0           # ...if that bulk reading is at most this old (s) and two-sided (others')
    # Favourite-longshot side bias. Day one + night, 891 fills with a known side (edge = quote vs fair value,
    # markout = fair value 60 min later): asks at fv < 20c +2.26c edge / +2.03c markout on 36k shares, bids there
    # -0.53c / -0.57c on 20k; bids at fv > 80c +1.49c / +1.41c on 29k, asks there -0.44c / -0.49c on 32k. Students
    # buy longshots and sell favourites, and the tournament mid reverts toward Polymarket. So the side that buys
    # the longshot (our bid below fl_low) or sells the favourite (our ask above fl_high) quotes wider and smaller,
    # unless it shrinks a position on this exchange (then it quotes normally: unloading is good).
    fl_bias_enabled: bool = False         # OFF: the Analyst showed the "bad side" losses were day one's skew quoting
                                          #   through fair value, not longshot flow (DATA_REPORT_2 §10c); owner may switch on
    fl_low: float = 0.20                  # fair value below this: the bid is the "bad" side
    fl_high: float = 0.80                 # fair value above this: the ask is the "bad" side
    fl_hysteresis: float = 0.01           # once on, the bias stays until fair value is this far back across the line
    fl_bad_side_extra_edge: float = 0.01  # bad side quotes this much further from the reservation price (capped at
                                          #   max_half_spread); adds to ref_only_min_edge in thin-book markets
    fl_bad_side_size_factor: float = 0.5  # ...and at this fraction of its size (multiplies with burst / thin-book sizes)
    fl_mid_bid_extra_edge: float = 0.0    # optional extra edge on bids with fair value in [fl_low, fl_high] (mid-band
                                          #   bids lost -0.25c / -0.34c on 92k shares; may be day one's skew bug). 0 = off

    # --- REFERENCE PRICES (Polymarket via ref_prices.py; only active if ref_map_file exists) ---
    # Polymarket is treated as the better estimate of the true price: the tournament book is seeded
    # from it and lags it.
    ref_map_file: str = "ref_map.json"    # "" = off
    ref_refresh_seconds: float = 5.0      # download Polymarket prices this often (one round trip: 5 requests side by
                                          #   side on kept-open connections = 1 request/s). Going below 5 s: first
                                          #   confirm Polymarket's published Gamma /markets rate limit allows it.
                                          #   Its public API is live and separate from SIG's request budget, so the
                                          #   only delay is this interval: faster bots pick off stale quotes
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
    # Buy side (arb_two_sided): other traders' asks across ALL of a race's listed parties add up to
    # <= 1 - arb_min_profit_buy -> buy YES on every leg (a long set pays exactly 1 if a listed party wins).
    # 60-s snapshots: ask-sum < 0.98 in 853 race-minutes/day (2.5%). The pair unwinder sells the set later.
    arb_two_sided: bool = True
    arb_min_profit_buy: float = 0.015     # ask-sum <= 0.985. Not done in reduce-only or the pre-close window
    arb_buy_min_ref_sum: float = 0.99     # ...and only when every leg has a LIQUID Polymarket price and those raw prices
                                          #   add up to at least this (an unlisted outsider shows up as a shortfall)
    arb_buy_min_sum: float = 0.90         # ...nor when the asks add up to less than this: that more likely means
                                          #   the market prices a winner OUTSIDE the listed parties (e.g. an
                                          #   independent with no market of its own), when a set pays nothing

    # --- PAIR UNWIND (inventory-aware: turn a held complete set back into cash) ----------------
    # Long YES on EVERY listed party of a race (min over legs = n sets) pays exactly n at settlement: zero risk,
    # zero return, capital tied up and mark noise. When other traders' bids add up to >= 1 + pair_unwind_min_profit,
    # sell the sets (riskless gain of bids-1 per set). Mirror: short every leg and asks add up to <= 1 - this ->
    # buy back. Reduces positions, so it also runs in reduce-only and in the pre-close window. A race with an
    # independent leg is only a set if that leg is held too (min over ALL legs).
    pair_unwind_enabled: bool = True
    pair_unwind_min_profit: float = 0.005  # 0 = unwind at exactly fair (bids sum to 1.000)
    pair_unwind_max_frac: float = 0.01    # at most this much cash per unwind order: 2,000 at 100k
    pair_unwind_cooldown_seconds: float = 30.0

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
    take_ref_max_age_seconds: float = 30.0  # a Polymarket price not re-downloaded for this long never confirms or
                                          #   triggers a take (a failed download keeps the old price for 5 min and
                                          #   still counts as a reading). 0 = off

    # --- ORDER LIFECYCLE ---------------------------------------------------------------------
    order_ttl: float = 1800.0             # every order expires after 30 min (dead-man's switch). Longer = fewer
                                          #   replacements (each costs requests and our place in line)
    refresh_before_expiry: float = 180.0  # replace an order once it has less than 3 min to live
    batch_size: int = 10                  # orders per POST /orders/batch. Was 20: 1 Oct 16:00-2 Oct 08:08, 273 of
                                          #   the 417 "409 in flight" batches (each a batch that ran past the 15 s
                                          #   timeout, then retried) were full 20-order batches, while batches
                                          #   under 20 failed ~5% of the time. Smaller = faster writes, a bit more
                                          #   write budget on big re-quotes (a restart: ~6 batches instead of 3)
    parallel_writes: int = 4              # order writes (cancels, batches) in flight at once. Cancels go first, then
                                          #   new orders: party-control (headline) markets first, then the biggest
                                          #   quotes. 1 = the old way: one write at a time, the cycle waiting for each
    write_read_reserve: int = 3           # order writes leave this many requests/min of the budget free (positions,
                                          #   orders reads); book downloads already leave budget_reserve for writes
    write_fail_fast: bool = True          # an order write that times out after being sent isn't retried at once (it
                                          #   would only get 409 in flight); its orders are recovered from the list
    write_wait_seconds: float = 3.0       # a cycle waits at most this long for its writes; slower ones (day one: 15-30 s)
                                          #   finish in the background and their exchanges are left alone until then
    main_write_wait_margin: float = 2.0   # a write sent by the MAIN thread (take, arbitrage, cancel-all instead of
                                          #   pulls) that would wait longer than write_wait_seconds + this for the write
                                          #   budget or a 429 pause is not sent (429 WRITE_BUDGET_WAIT) - the loop never
                                          #   blocks on the budget (2 Oct 11:31: 2-5 min cycles)
    urgent_writes_per_cycle: int = 20     # at most this many writes per cycle for changes the request budget can't
                                          #   defer (pulls, unsafe orders); price-unsafe / unwanted sides first, the rest
                                          #   next cycle. 0 = no cap
    pause_skip_cycles: bool = True        # while the exchange's 429 pause lasts, skip cycles (reads would only wait
                                          #   for the pause inside the cycle) instead of blocking the loop
    watchdog_alert_seconds: float = 180.0  # no cycle completed for this long -> alert (watchdog thread). 0 = off
    watchdog_exit_seconds: float = 600.0  # ...for this long -> log every thread's stack, cancel everything (at most
                                          #   watchdog_cancel_seconds) and exit with code 5 (systemd restarts). 0 = off
    watchdog_cancel_seconds: float = 20.0
    pending_seconds: float = 90.0         # placement outcome unknown -> leave that exchange alone this long, unless
                                          #   the orders show up in the open-orders list first (recovered: see
                                          #   adopt_unconfirmed), which on a slow exchange takes seconds
    recover_unconfirmed: bool = True      # match orders that landed after their request timed out (day one: 55 of
                                          #   the first 82 fills) to what we sent, by exchange/side/price/expiry, so
                                          #   the bot knows them at once and their fills are attributed
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

    # --- CHURN CONTROL (a crowded book: other bots re-quote one tick inside us all day) ---------------
    # Every reprice costs a write (30/min budget) and our place in line. Only reprice when it matters.
    churn_control: bool = True
    min_quote_life_seconds: float = 5.0   # an order younger than this isn't repriced while it's still safe (inside
                                          #   our limit price); unsafe ones, pulls and expiries always go
    churn_max_reprices: int = 4           # a side repriced this many times within churn_window_seconds stops
    churn_window_seconds: float = 60.0    #   chasing: its order stays while safe (no ping-pong with another bot)
    churn_count_sent: bool = True         # count a reprice toward churn_max_reprices only when its cancel is actually
                                          #   sent (False = when planned, as before: a change deferred by the request
                                          #   budget or dropped by burst mode still counted, so a side could be held
                                          #   off target for up to churn_window_seconds without ever repricing)
    urgent_ref_move: float = 0.005        # Polymarket moved this much since the last reading: that market's changes
                                          #   are sent first (stale quotes get picked off by the fastest bot)

    # --- BURST PROTECTION (the exchange is slow: day one's open, writes 15-30 s) ------------------
    # While writes or cycles are slow, every quote can sit stale for long, and every change costs a slow write.
    # So: quote only the biggest markets, smaller and one tick wider; elsewhere keep what's resting while it's
    # safe and only pull, never place. Back to normal after burst_calm_seconds of normal speed.
    burst_protection: bool = True
    burst_write_seconds: float = 5.0      # enter when the median order write of the last minute takes this long...
    burst_cycle_seconds: float = 60.0     # ...or a cycle takes this long (2 Oct live: normal cycles take 20-30 s, so 20
                                          #   fired 4 times with writes at 0.4 s and no timeouts; 60 = owner override)...
    burst_timeouts: int = 2               # ...or this many writes timed out in the last minute
    burst_calm_seconds: float = 120.0     # leave after this long without any of that
    burst_startup_grace_seconds: float = 90.0   # after a (re)start or handover, the cycle-length trigger is ignored
                                          #   this long, and while the first download of every book is still running
                                          #   (at most BURST_LOADING_MAX_SECONDS): those cycles are long because of our
                                          #   own throttled reads (2 Oct 08:08: 21 s -> burst for 2 min, write median
                                          #   0.4 s). Slow writes and timeouts still trigger. 0 = no grace
    burst_markets: int = 40               # quote only this many markets: House/Senate, then by planned size
    burst_size_factor: float = 0.5        # new quotes at this fraction of their usual size
    burst_extra_edge: float = 0.005       # ...and this much further from fair value (one tick)

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
    # After a (re)start every book is missing. Order writes keep only write_read_reserve (3) requests free while
    # book downloads keep budget_reserve (20) free for writes, so writes win the shared per-minute budget and
    # books trickled in at 12-14 a minute (2026-10-02 08:08). While priming: more books per cycle, and books
    # (downloaded before this cycle's writes) only leave startup_prime_reserve requests for the writes, up to
    # startup_prime_books_per_min; Bot.write_reserve() is what order writes should then leave free for them.
    startup_books_first: bool = True      # prime the books after a start or handover restart...
    startup_prime_seconds: float = 180.0  # ...for at most this long after trading starts...
    startup_prime_missing_frac: float = 0.10   # ...and only while more than this share of books is missing
    startup_prime_books: int = 60         # book downloads per cycle while priming (instead of max_books_per_cycle)
    startup_prime_reserve: int = 8        # requests per minute book downloads leave free while priming
    startup_prime_books_per_min: int = 45 # ...but at most this many downloads in any 60 s, the rest for order writes
    startup_prime_held_max_seconds: float = 900.0  # ...except: priming never ends while a market we HOLD a position in
                                          #   has no downloaded book, up to this hard maximum (s) after the start
    unpriced_held_warn_cycles: int = 5    # a held market without a fair value this many cycles in a row: one WARNING
                                          #   with the reason (no book / tops blanked / gap / guard); 0 = off

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
    writes_per_minute: int = 28           # separate budget for order writes (each batch, cancel-all or DELETE = 1),
                                          #   2 Oct live (Package 2.3): 4 x 429 in the first hour while the budget
                                          #   climbed to 36-50/min, 0 since capped at 28: the limit is ~30/min per bot.
                                          #   the value it starts at. A copy of the platform docs says "100 reads and
                                          #   30 writes per minute per key", but 1-2 Oct (16:00-08:08) ran 63 minutes
                                          #   at >= 40 writes (peaks ~60) with no 429; the 6 429s came at 33-53 writes
                                          #   and didn't follow the write rate. 30 deferred changes on every cycle for
                                          #   3 minutes after the 2 Oct 08:08 restart
    writes_per_minute_max: int = 28       # ...it then grows slowly (+1 per 60 successful writes) up to this while no
                                          #   write is rate limited (= writes_per_minute: never grows)
    startup_writes_per_minute: int = 30   # while the first download of every book is still running after a (re)start,
                                          #   writes stay at most this (the old budget), so the bigger write budget
                                          #   doesn't slow the book downloads new quotes need. 0 = no cap
    write_budget_cut: float = 0.75        # a write answered 429 cuts the write budget to this fraction (min 10/min)
    never_defer_unsafe: bool = True       # a change that removes an UNSAFE order (beyond its limit price, above the
                                          #   position / cash limits, or a side we no longer want) is never deferred by
                                          #   the request budget, like a pull (False = only pure pulls are exempt).
                                          #   An order only bigger than a size FACTOR now wants (capital ceiling, burst,
                                          #   favourite-longshot) is not unsafe: it stays (see Quote.bid_max)
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
    realtime_healthy_seconds: float = 60.0         # a session up this long that then drops reconnects after 1 s again
                                                   #   (the back-off only grows over drops that come in quick succession)
    realtime_session_max_seconds: float = 3600.0   # ...and at least this often anyway: a long-lived socket can die
                                                   #   without the library noticing (it happened on 29 Sep)

    # --- LIVE SETTINGS (change settings without a restart) -----------------------------------
    overrides_file: str = "settings_override.json"   # {"min_edge": 0.015, ...}: re-read every overrides_seconds;
                                          #   only OVERRIDABLE settings, each checked; a removed key goes back to
                                          #   its default. Every change is logged. "" = off
    overrides_seconds: float = 30.0
    analyze_daily_hour: int = -1          # send the first lines of `analyze` (last 24 h) to your phone daily at this
                                          #   hour UTC (-1 = off)
    slow_cycle_alert_seconds: float = 120.0   # a cycle running this long: status.json says so and one alert is sent
    # Rival-floor map (analysis/rival_floor.py writes market_edge.json from the recorder's books): a per-market
    # min_edge, max(min_edge, the market's entry capped at market_edge_max); markets without an entry keep
    # min_edge. Off until calibrated on the live recorder data.
    market_edge_enabled: bool = True 
    market_edge_file: str = "market_edge.json"   # {"<exchange id>": {"min_edge": 0.015, ...}, ...}; "" = off
    market_edge_reload_seconds: float = 600.0    # re-read this often (when the file changed)
    market_edge_max: float = 0.02         # no market's own edge above this

    # --- FILES (relative names are kept in the bot's own folder) -----------------------------
    fills_csv: str = "fills.csv"
    log_file: str = "mm_bot.log"          # "" = log to the terminal only
    log_max_mb: float = 10.0              # start a new log file at this size...
    log_backups: int = 5                  # ...keeping this many old ones (so the log can't fill the disk)
    status_file: str = "status.json"      # health snapshot rewritten after every cycle
    order_notes_file: str = "order_notes.json"  # survives restarts, so fills can still be attributed
    position_lots_file: str = "position_lots.json"  # position ages (age skew) survive restarts; without it they are
                                          #   rebuilt from fills.csv at start (latest fills = what FIFO leaves held)
    kill_file: str = "kill_switch.tripped"      # the kill switch creates it; delete it to allow trading again
    handover_file: str = "handover.json"        # written by a handover stop (SIGUSR1): the next start adopts the
    handover_max_age: float = 300.0             #   orders left resting instead of cancelling them, if within this
                                                #   many seconds (else: clean slate as usual)
    handover_exit_max_seconds: float = 150.0    # a handover stop that hasn't exited this long after SIGUSR1 exits at
                                                #   once (exit 0): finishing the cycle, the 60 s write drain, then
                                                #   queued writes on the writer threads (each up to ~4 x 15 s plus the
                                                #   write-budget wait) had no bound. Writes still running are
                                                #   abandoned: the next run re-reads the open-orders list (and without
                                                #   a handover file starts from a clean slate). 0 = no cap
    record_file: str = "market_data.sqlite"     # snapshots for tuning later ("" = off)
    record_seconds: float = 60.0          # one snapshot of every market this often (~15 MB a day)
    record_books: bool = True             # also record OTHER traders' top book levels (with sizes) whenever a downloaded
                                          #   book's top changed, and every tournament trade the feed reports: the data
                                          #   to measure rival bots (repricing speed, floors, sizes, hours). No extra
                                          #   requests; roughly 10-15 MB a day
    record_book_levels: int = 3           # levels per side kept in those rows
    record_positions: bool = True         # also record the positions read (per position: quantity, the exchange's
                                          #   mark currentPrice and every other number it gives) and the account
                                          #   marks (account value, market value, cash, P&L) - the data to pin down
                                          #   how the exchange values positions (analysis/mark_rule.py). No extra
                                          #   requests: at most once per record_seconds plus once after a fill, and
                                          #   a position's row only when it changed (or hourly). ~1-3 MB a day
    record_positions_full_seconds: float = 3600.0   # ...but every position at least this often

    # --- SAME-SIDE REFILL COOLDOWN ------------------------------------------------------------
    # Data (Analyst 10b): the 3rd and later fills of a same-side run within 60 s lost -1.15c a share (81k shares,
    # -936), within 10 s -1.68c: re-posting the same side straight after it was hit kept buying through fair value.
    refill_cooldown_enabled: bool = False  # after a same-side run of fills that ADDED to the position, stop re-quoting
                                          #   that side for a while (a safe resting order there is left alone; an
                                          #   unsafe one is pulled as usual). A side that reduces the position is exempt
    refill_cooldown_fills: int = 2        # ...this many fills on one side of one market
    refill_cooldown_window_seconds: float = 60.0   # ...within this long
    refill_cooldown_min_shares: int = 200  # ...totalling at least this many shares (1-share probes don't count)
    refill_cooldown_seconds: float = 30.0  # how long that side stays withheld

    # --- FAST UNLOAD AFTER SWEEP FILLS ---------------------------------------------------------
    # Data: fills with > 3c edge made +1,704 on 109 fills (DATA_REPORT_2 s5); the mid reverts toward Polymarket with
    # a 5-13 min half-life, positions are held a median 7.2 h. After a sweep fill, offer the shares back near fair
    # value for a few minutes (realised P&L) instead of carrying them at the normal skewed quote.
    fast_unload_enabled: bool = False      # a quote fill that ADDED to a position opens an "unload window" there
    fast_unload_min_edge: float = 0.02    # ...if it had at least this edge at the quote (vs fv when quoted)...
    fast_unload_min_shares: int = 100     # ...and at least this many shares
    fast_unload_seconds: float = 180.0    # how long the window stays open (only its first placement is urgent)
    fast_unload_edge: float = 0.01        # the reducing side quotes this far from fair value (not min_edge / the
                                          #   ref_only edge, not pennying), never through fair; 0 = fv rounded away
    fast_unload_size_mult: float = 1.0    # reducing size = shares still to unload x this, capped by the position

    # --- REDUCING SIDE JOINS THE BEST -----------------------------------------------------------
    # Data (Explorer): P(fill in 10 min) 33-34% AT the best, 6-8% one tick behind; our reducing side was at the
    # best only 24% of the time; 11 of 12 positions >= 1,000 sh had no reducing fill in 6 h; round trips made all
    # the realised profit (+1,824 on 254k shares). So the side that shrinks |race-netted position| joins the best.
    reduce_join_best: bool = False         # when the best other price on that side is INSIDE our normal quote, that
                                          #   side joins it (AT the best, no pennying), but never closer than
                                          #   reduce_join_min_edge to fair (rounded away) and never crossing. A best
                                          #   outside our quote changes nothing (we stay the best). A fast unload
                                          #   window still wins when closer to fair. Off in reduce-only
    reduce_join_min_edge: float = 0.01    # closest the joining side may sit to fair value (0 = fv rounded away)
    reduce_join_min_shares: int = 100     # only while |race-netted position| is at least this

    # --- TURNOVER CONTROL (adding side in markets whose position cannot turn) -----------------
    # 11 of the 12 positions >= 1,000 sh had no reducing fill in 6 h; 52 markets trading < 2 fills/h held 28.6k of
    # the 53k in positions. A market whose observed flow (shares traded in the last turnover_window_hours: our fills,
    # or the realtime trade tape if larger - the tape includes ours) is below turnover_min_shares_per_hour is "dead";
    # there, while we hold a position (|race-netted| >= min(quote size, 100)), the ADDING side quotes at
    # turnover_dead_adding_factor of its size (times the capital ceiling's factor, if on) against a position limit
    # of turnover_dead_max_position_frac of the normal one. The reducing side and flat markets are unchanged. Until
    # ~turnover_window_hours of flow has been observed (this run plus what fills.csv / the recorder's trades table
    # cover), every market counts as alive.
    turnover_control_enabled: bool = False
    turnover_window_hours: float = 6.0
    turnover_min_shares_per_hour: float = 50.0
    turnover_dead_adding_factor: float = 0.25    # 0 = adding side withdrawn in dead markets
    turnover_dead_max_position_frac: float = 0.5
    turnover_use_tape: bool = True        # count other traders' trades from the realtime feed (and its recording)
    turnover_alive_shares_per_hour: float = 75.0   # hysteresis: a dead market is alive again only above this flow
    turnover_min_state_minutes: float = 30.0       # a market stays dead / alive at least this long before flipping

    # --- CONNECTION / ALERTS (from the environment: see top of file) ------------------------
    summary_every_hours: int = 2          # phone summary every N hours, on the hour UTC (2 = 00:00, 02:00, 04:00...),
                                          #   covering what happened since the previous one. 0 = off. Live only
    alert_url: str = os.environ.get("ALERT_URL", "")   # e.g. https://ntfy.sh/some-long-random-name
    api_key: str = os.environ.get("SUPERMARKET_API_KEY", "")
    base_url: str = os.environ.get("SUPERMARKET_BASE_URL", "https://sig.thesuper.market/api/v1")
    slug: str = os.environ.get("TOURNAMENT_SLUG", "")
    only_exchanges: str = os.environ.get("ONLY_EXCHANGES", "")

    # --- WRITE SAVERS (Round 4 item 2; IDEAS_ROUND3 A1/A10 and A4/A5) - both OFF by default ------------------
    # (a) No-chase. 24% of quote changes were a pure 0.5c chase of a rival with neither our fair value nor our
    # inventory changed, and quotes < 2 min old earn less than resting ones. When the only trigger for re-pricing a
    # level-0 side is that its target moved (a rival stepped in front / away) - fair value within no_chase_fv_epsilon
    # of its value at placement, inventory as at placement, the order not (partly) filled - the order stays while it
    # is within no_chase_tolerance_ticks of the new target (instead of reprice_tolerance_ticks). Everything else
    # still applies: outside its limit price (unsafe/crossing), bigger than now allowed, mostly filled, expiring.
    no_chase_enabled: bool = False
    no_chase_tolerance_ticks: int = 2     # 0.5c ticks; only used with no_chase_enabled
    no_chase_fv_epsilon: float = 0.0025   # fair value moved more than this since placement -> normal tolerance
    # (b) TTL saver. order_ttl refreshes rewrite every resting order every ~27 min (~20% of writes).
    # ttl_tiers_enabled: level-0 orders get a TTL by market tier - headline races order_ttl, "busy" (planned quote
    # size >= ttl_busy_size_frac of the account) order_ttl_busy, the rest order_ttl_quiet - times a +-ttl_jitter_frac
    # random factor (so a restart's orders don't all refresh in the same minute), never above MAX_ORDER_TTL (the
    # dead-man's switch stays bounded at 2 h). ttl_expire_as_cancel: a level-0 order is NOT refreshed (cancel + new)
    # before it expires; it is left to expire on the exchange (no DELETE), and the side is re-quoted only once it has
    # been gone ttl_expire_grace_seconds (clock-skew margin: never two of our orders resting on one side).
    ttl_tiers_enabled: bool = False
    order_ttl_busy: float = 3600.0
    order_ttl_quiet: float = 6000.0       # x(1 +- 0.2) = 80-120 min (capped at MAX_ORDER_TTL)
    ttl_jitter_frac: float = 0.2
    ttl_busy_size_frac: float = 0.01      # same split as the ladder's default "busy" (1,000 shares at 100k)
    ttl_expire_as_cancel: bool = False
    ttl_expire_grace_seconds: float = 5.0
    # --- Package 5: T2.1 tilt-corrected reference ---
    # The tournament prices every market with one favourite-longshot tilt: mid ~ c + (1 - s)(r - c), c = 1/legs.
    # On: the blend leans toward that tilted Polymarket price instead of the raw one (the tilt is not mispricing).
    # s is estimated every cycle from the cross-section (TiltEstimator), even with the flag off (read-only).
    ref_tilt_enabled: bool = False
    ref_tilt_headline: bool = False       # False: headline markets keep the raw Polymarket price (staging gate)
    ref_tilt_min_markets: int = 50        # fewer usable markets than this: hold the last estimate
    ref_tilt_halflife_min: float = 30.0   # EMA half-life of the estimate, minutes
    ref_tilt_max: float = 0.20            # estimate clipped to [0, this]. Live s was 6.3% on 2 Oct evening (2.4% a day
                                          #   earlier); in a 15%-tilt simulated world a 0.12 clip left the bot buying 3
                                          #   points of tilt for the same 3-h P&L (SIM_NOTES Round 5), so the cap is set
                                          #   where it does not bind and the estimate stays readable; alarm above 0.12
    ref_tilt_winsor: float = 0.08         # each market's gap (Polymarket - book) clipped to +-this
    ref_tilt_estimator: str = "slope"     # how s is read from the cross-section (x = r - c, g = winsorised gap):
                                          #   "slope": sum x g / sum x^2 (x^2 weights: the |x| > 0.45 tails carry ~60%
                                          #   of the weight); "median": median of per-market g / x over |x| > 0.1
                                          #   (a block of tail markets pinned at the winsor cannot drag it); "wls":
                                          #   their mean (= least squares weighted 1 / x^2). TILT_ESTIMATOR.md
    # --- Package 5: B kelly_edge_cap, A reduce_from_book ---
    # B: Kelly sizes on at most this much edge (0 = off; try 0.015 / 0.01): size on the spread, not on a persistent
    #   tournament-vs-Polymarket gap (otherwise the bet is biggest exactly where the tournament disagrees most).
    kelly_edge_cap: float = 0.0
    # A: the REDUCING side only (long -> ask, short -> bid, size <= this market's position) measures min_edge from the
    #   tournament book's own price instead of the Polymarket-leaned fair value, never further toward Polymarket than
    #   the blend (the more aggressive of the two). The adding side keeps the blend, capped 2 x min_edge behind the
    #   reducing price (we never meet our own order). Off in ref-only markets, without a book price, and for
    #   reduce_from_book_pause_s after a Polymarket jump (>= ref_jump_threshold) in that market.
    reduce_from_book: bool = False
    reduce_from_book_pause_s: float = 120.0
    reduce_from_book_headline: bool = False   # False = not in headline_races markets (staging gate)
    # --- Package 5: T2.5 passive pair unwind ---
    # The active pair unwind needs other traders' bids to add up to >= 1 + pair_unwind_min_profit, which a held set
    # rarely sees (U.S. Senate, 2 Oct: bid sum ~0.990, 7,335 sets = 7.3k of capital earning 0). Passive: while a
    # complete set is held in a 2-leg race, rest the ASK of one leg (the one whose ask sits best) at
    # max(join the best other ask, 1 - best other bid of the other leg - pair_unwind_max_cost), one slice
    # (pair_unwind_max_frac of the account in cash, at most the other leg's best bid size); when it fills, sell the
    # same quantity of the other leg at its best bid at once (one take, cooldowns bypassed, write budget not) if
    # that bid is >= 1 - the resting leg's price - pair_unwind_max_cost - 0.5c (else the take waits, retried each
    # cycle, one alert). A set is closed for >= 1 - max_cost - 0.5c, or the owed leg waits; the unmatched leg is
    # never more than one slice. Switched off live: owed second legs are still finished, nothing new rests.
    # Short sets mirror it (rest a BID, take the other leg's ask). Selling both legs to others is not a self-trade.
    pair_unwind_passive: bool = False
    pair_unwind_max_cost: float = 0.003   # at most 0.3c per set below 1 (22 on 7,335 sets)
    # --- Package 5: T2.4 tilt exposure limit ---
    # tilt_exposure (T2.1) = sum over held markets of position x (Polymarket - c): what one point of tournament
    # tilt marks the book by (x -0.01). Above tilt_exposure_max_frac of the account (0 = off; try 0.10), the side
    # that would grow |tilt_exposure| in a market (contribution sign = sign(Polymarket - c)) is switched off, like
    # the party-delta cap. Never forces an exit: the side that shrinks it always stays. No Polymarket or r == c:
    # unaffected. tilt_exposure_headline False = the limit is not applied in headline_races markets (staging gate).
    tilt_exposure_max_frac: float = 0.0
    tilt_exposure_headline: bool = False
    # --- Package 5: C hold target ---
    # Backstop for positions we cannot exit. A market whose held lots' share-weighted age (age_hours) is
    # >= hold_target_hours: its REDUCING side joins the best other price on that side (long -> ask at the best other
    # ask, short -> bid at the best other bid), never more than 1c through the book's own price on the losing side
    # and never at or through our own other side (hold_quote). >= 2 x hold_target_hours: take the best other bid
    # (long) / ask (short), immediate-or-cancel, one order <= max_order_cash_frac of the account and the best level,
    # never more than 1c through the book's price, at most hold_take_max_per_min takes a minute (bot-wide) and
    # hold_unload_budget_frac of the account (notional) per rolling hour (take_aged). The hourly budget starts
    # fully used: no take in the first hour after start or after turning it on (any cycle with hold_target_hours
    # <= 0 resets the hold-off), so a restart can never dump. Neither half acts in a market during its jump
    # cooldown, within reduce_from_book_pause_s of a Polymarket jump, or when Polymarket is more than
    # ref_guard_gap on the other side of the book's price (it says the exit is the wrong trade); the joined
    # reducing side is never larger than the position (a hold exit never flips it).
    hold_target_hours: float = 0.0        # 0 = off (try 4)
    hold_unload_budget_frac: float = 0.02  # notional of takes per rolling hour, fraction of the account value
    hold_take_max_per_min: int = 2        # takes per minute, bot-wide
    hold_target_headline: bool = False    # False = not in headline_races markets (staging gate)
    # --- Package 5: T2.3 carry ramp (HARD GATE) ---
    # MUST stay 0 until the owner has SIG's answer IN WRITING that positions open at the close are settled at the
    # outcome (not marked at the last trades). > 0 (with ref_tilt_enabled): inside the last N days before a market's
    # close the tilt s applied in blend_fv ramps linearly to 0 at the close (carry_ramp), so quotes lean back to raw
    # Polymarket and the book takes the favourite-longshot carry late. The estimator itself is untouched.
    ref_tilt_carry_days: float = 0.0      # NOT live-overridable (not in OVERRIDABLE): code change + restart
    # --- Package 5: X5 gap-size shrink ---
    # Where the quoted fv (Polymarket-blended, tilt-corrected if T2.1 is on) sits far from the book's own price, the
    # side that ADDS to the position shrinks: factor = max(gap_size_floor, 1 - |fv - book_fv| / gap_size_shrink),
    # multiplied into decide's adding factor (like the capital ceiling's). The reducing side is untouched. Not in
    # ref-only markets, not without a book price.
    gap_size_shrink: float = 0.0          # 0 = off; the gap (in price) at which the adding size reaches the floor (try 0.03)
    gap_size_floor: float = 0.25          # the smallest factor
    # --- Package 5: X11 reduce_from_book scope ---
    # reduce_from_book_dead_only True: A (reduce_from_book) applies only in markets flagged turnover-dead
    # (ex.turnover_dead, refresh_turnover's hysteresis verdict while holding a position) or, with
    # reduce_from_book_max_turnover > 0, whose observed flow (turnover_flow, shares/h over turnover_window_hours) is
    # below it. A not-yet-judged market (no flow figure) is not eligible by the threshold. Elsewhere the reducing
    # side keeps the plain quote: in active markets the book-priced exit is picked off by informed takers.
    reduce_from_book_dead_only: bool = False
    reduce_from_book_max_turnover: float = 0.0   # shares/h; 0 = only the turnover_dead flag
    # --- Package 5: X12 takes measured from the tilted reference ---
    # take_tilted_ref True (with ref_tilt_enabled): take_stale_quotes measures the gap, confirms it, re-checks it
    # and sizes the take (Kelly p) from the tilt-corrected Polymarket price (Bot.tilted_ref_for: same s, carry ramp
    # and headline gate as the blend) instead of the raw one. Arbitrage, the reference guard and ref_only pricing
    # stay on the raw price. Off (or ref_tilt_enabled off): unchanged.
    take_tilted_ref: bool = False
    # --- Package 5: T2.1 ramp-in ---
    # With ref_tilt_enabled, the tilt s APPLIED (blend and takes, via Bot.tilted_ref_for) rises linearly from 0 to
    # the estimate over this many minutes after the flag is switched on (hot toggle, or at start with the flag on),
    # so fair value does not jump in one step against inventory bought at tournament prices. 0 = no ramp.
    # SIM_NOTES Round 5 (8 x 3 quiet, tilt world, d pnl_liq / d pnl_lag): step-on +163 +- 60 / +166 +- 72; 20-min ramp
    # +199 +- 83 / +176 +- 110 with less pick-off (pick_cost +9 vs +38); 120-min ramp -70 +- 130 / -36 +- 150 with MORE
    # re-prices (+0.8 writes/min) and the same cost at the Polymarket mark. Default 20: the same P&L as the step-on,
    # the switch-on re-price wave (~157 markets) spread over ~10 cycles. Still: switch on in a quiet hour.
    ref_tilt_rampin_min: float = 20.0
    # --- Package 6 candidate: backstop soft band ---
    # With risk_model "correlated": in a band of this many x account value below worst_case_backstop_frac, the
    # ADDING side's size shrinks linearly to 0 as the sum-of-maxima worst case nears the backstop (see
    # backstop_soft_factor), so the worst case stops growing before the hard reduce-only cliff instead of flipping
    # every adding side off and on. The reducing side is never shrunk. 0 = off (unchanged quotes).
    backstop_soft_frac: float = 0.0
    # --- Package 6 candidate: exits keep quoting in reduce-only ---
    # The bot is reduce-only most of the time live (backstop), and there every exit feature was switched off.
    # exit_quotes_in_reduce_only True: hold_quote (C; still needs hold_target_hours > 0 and its age test) and
    # reduce_join_best (still needs its own flag) also run in reduce-only. Both only move the REDUCING side toward
    # the book and size it <= the (race-netted) position, so risk can only fall; every other guard stays (reference
    # guard, jump cooldown, headline gates, never meeting our own other side). Fast unload stays off in reduce-only.
    # pair_passive_in_reduce_only True (with pair_unwind_passive): pair_passive_quote may rest the slice side of a
    # complete-set leg when the ONLY thing that emptied that side in decide() was the reduce-only race-net clip
    # (ex.ro_clip; never after fv None, stop-before-close, cooldown, the reference guard or a block). Price
    # pp_candidate's, one slice, the other leg taken on fill as usual, cost capped by pair_unwind_max_cost.
    exit_quotes_in_reduce_only: bool = False
    pair_passive_in_reduce_only: bool = False
    # --- Package 6 candidate: tail adding-size factor ---
    # In tail markets (raw Polymarket reference r below tail_low or above tail_high) the Polymarket-vs-tournament gap
    # is mostly the systematic favourite-longshot tilt, so Kelly sizing on that "edge" buys most where it is least
    # real. There the ADDING side's size is multiplied by this factor (the shared adding factor in Bot.decide); the
    # reducing side is never shrunk. Uses the raw Polymarket price (decide's ref), not the tilted one; no ref ->
    # unchanged. 1.0 = off (unchanged quotes); candidate 0.25.
    tail_adding_factor: float = 1.0
    # --- Package 6: reduce NO holdings as covered NO sales ---
    # Live 3 Oct: every order was sent as side "yes", so a BID that buys back a short YES position (we hold NO) went
    # out as "buy YES @ p", which the exchange treats as a new cash purchase and refuses at 0 free cash ("Insufficient
    # available funds"): the short book could never shrink. True: the part of a bid that reduces the NO held in that
    # market (per market, not race-netted) is sent as a covered sale "sell NO @ 1-p" (needs no cash); any part
    # beyond the NO held stays "buy YES @ p" (see order_wire). Asks and adding bids unchanged. The start-up
    # self-test checks the exchange accepts it and falls back to False for the run if not. False = unchanged.
    reduce_no_as_sell: bool = False
    # --- Package 7: NO+NO sets ---
    # Live 3 Oct 14:00: 47 of the 49 remaining "Insufficient available funds" refusals were covered NO sales on races
    # where we hold NO on EVERY leg. The exchange collateralises NO+NO as a SET ("collateralSavings"): selling NO on
    # ONE leg breaks the set, the remaining lone NO then needs full collateral, i.e. cash. Selling the LONE part (NO
    # held beyond the race's smallest leg) is fine.
    # no_set_aware_bids True (with reduce_no_as_sell): a covered bid / take on such a leg is capped at its lone part,
    # max(0, NO held - the most NO held on any OTHER leg of the race; a leg without NO counts 0) - the worst-case
    # (sum - max) collateral model, so [10, 8, 5] -> [2, 0, 0] and NO on 2 of 3 legs is capped too (0 = no bid
    # there, it would need cash). The set part is only unwound as a pair (below). False = unchanged.
    no_set_aware_bids: bool = False
    # pair_no_unwind_max_cost >= 0 (with reduce_no_as_sell; live, after its own start-up check): the short-set
    # buy-back in arb_plan (buy YES on every leg = covered "sell NO" on every leg, ONE batch) also runs when the asks
    # add up to <= 1 + this (a cost of at most this per set to free the set's capital), not only <= 1 -
    # pair_unwind_min_profit. Both legs go out as covered sales in one batch or the batch is not sent. -1 = off.
    pair_no_unwind_max_cost: float = -1.0
    # --- Package 7: pair unwind follow-up ---
    # Live 3 Oct 14:11: a short-set pair unwind on Hawaii Governor filled [314, 1069] - 755 shares of a hedged set
    # became one-sided inventory. pair_unwind_followup True: (1) every leg of a pair unwind is sized to what can fill
    # together (the least, over legs, of the cached book's depth at or better than the planned price, and the sets);
    # (2) legs that still fill unequally leave the lagging leg(s) OWED the difference: on the next cycles (up to
    # pair_unwind_followup_tries) one immediate-or-cancel order per lagging leg for what is owed, at a limit up to
    # pair_unwind_followup_max_cost per share past the planned price (buy-back legs as a covered "sell NO" with
    # reduce_no_as_sell), then one alert with what is left. The owed state is in memory only (a restart drops it,
    # logged) and in status.json pair_owed. False = unchanged.
    pair_unwind_followup: bool = False
    pair_unwind_followup_max_cost: float = 0.01
    pair_unwind_followup_tries: int = 6
    # pair_no_unwind_max_per_cycle: at most this many short-set unwinds that run only because of the widened
    # pair_no_unwind_max_cost threshold per cycle (each costs 5 writes and a cooldown; up to 38 NO+NO races qualify
    # at once when it is switched on); the rest wait for the next cycles. No effect with pair_no_unwind_max_cost -1.
    pair_no_unwind_max_per_cycle: int = 2
    # --- Package 8: cut UNPAIRED tilt exposure faster, never crossing ---
    # A market's position "adds to |tilt_exposure|" when its contribution pos x (raw Polymarket - c), c = 1/legs,
    # has the sign of the total tilt_exposure (live 3 Oct: +34k, so sign(pos) == sign(r - c)); its tilt exit is the
    # side that shrinks THIS market's own position (long -> ask, short -> bid). NO+NO sets carry ~0 exposure.
    # tilt_exit_priority True: in the write budget (change_key) a change touching a tilt exit sorts after the
    # headline markets and before every other ordinary change (pure ordering: no price or size change).
    tilt_exit_priority: bool = False
    # tilt_exit_full_size True: on those markets the tilt exit's size is the whole position, not the planned order
    # size (compute_quote's reduce_size: still capped by the race-net clip, position / cash limits and, for a
    # covered NO sale, the NO free to sell / its lone part).
    tilt_exit_full_size: bool = False
    # adding_factor_capital_on > 0: while the capital ceiling is on, capital in positions / account below this ->
    # the adding side is sized by max(capital_ceiling_adding_size_factor, capital_ceiling_adding_size_factor_resume)
    # (owner live: the configured factor 0 = no adding at all); back to the configured factor at >= this + 0.01
    # (hysteresis). status.json capital_ceiling_adding_factor = the factor in force. 0 = off.
    adding_factor_capital_on: float = 0.0
    capital_ceiling_adding_size_factor_resume: float = 0.5
    # ref_guard_tilted True (with T2.1 on in that market): the reference guard measures its gap from the tilted
    # reference tilted_ref_for(r) instead of raw Polymarket (Dem House: r 0.925, s 0.11 -> 0.878 vs book ~0.87: the
    # exit rests; a real 8c Polymarket move still moves r' ~7c and trips it).
    ref_guard_tilted: bool = False
    # ref_guard_exits True: the guard never blocks the side that shrinks THIS market's own position; that side is then
    # capped at the position (never flips it); the adding side is still blocked.
    ref_guard_exits: bool = False


CFG = Config()

# Settings that may be changed while the bot runs (settings_override.json), with their allowed range. Never
# secrets, URLs, file names, the kill switch or anything read only at start-up.
OVERRIDABLE = {
    "min_edge": (0.0, 0.10), "max_half_spread": (0.005, 0.20), "skew_per_share": (0.0, 0.001),
    "reprice_tolerance_ticks": (0, 10), "keep_fraction": (0.0, 1.0),
    "order_size_frac": (0.0, 0.05), "size_min_frac": (0.0, 0.05), "size_max_frac": (0.0, 0.10),
    "headline_size_frac": (0.0, 0.20), "headline_position_frac": (0.0, 0.30), "quote_capital_frac": (0.0, 1.0),
    "max_position_frac": (0.0, 0.10), "max_party_delta_frac": (0.0, 0.50), "party_skew_at_cap": (0.0, 0.05),
    "max_worst_case_frac": (0.05, 0.60), "kelly_fraction": (0.0, 1.0), "kelly_max_market_frac": (0.0, 0.10),
    "ref_weight": (0.0, 1.0), "ref_guard_gap": (0.02, 0.30), "ref_jump_threshold": (0.005, 0.30),
    "ref_jump_cooldown_seconds": (0.0, 3600.0), "jump_threshold": (0.01, 0.50), "jump_cooldown_seconds": (0.0, 3600.0),
    "arb_enabled": (False, True), "arb_min_profit": (0.005, 0.20), "take_enabled": (False, True),
    "take_edge": (0.02, 0.30), "tail_low": (0.0, 0.20), "tail_high": (0.80, 1.0),
    "requests_per_minute": (10, 100), "writes_per_minute": (5, 100), "budget_reserve": (0, 60),
    "writes_per_minute_max": (5, 100), "startup_writes_per_minute": (0, 100), "write_budget_cut": (0.25, 1.0), "never_defer_unsafe": (False, True),
    "max_books_per_cycle": (1, 100), "book_stale": (60.0, 3600.0), "book_reverify_seconds": (10.0, 1800.0),
    "parallel_writes": (1, 8), "write_wait_seconds": (0.0, 30.0), "main_write_wait_margin": (0.0, 60.0),
    "urgent_writes_per_cycle": (0, 500), "pause_skip_cycles": (False, True),
    "watchdog_alert_seconds": (0.0, 3600.0), "watchdog_exit_seconds": (0.0, 7200.0), "watchdog_cancel_seconds": (1.0, 120.0),
    "churn_control": (False, True), "min_quote_life_seconds": (0.0, 120.0), "churn_max_reprices": (1, 100),
    "churn_window_seconds": (5.0, 3600.0), "urgent_ref_move": (0.0, 0.10),
    "burst_protection": (False, True), "burst_write_seconds": (0.5, 60.0), "burst_cycle_seconds": (2.0, 600.0),
    "burst_timeouts": (1, 100), "burst_calm_seconds": (0.0, 3600.0), "burst_markets": (1, 300),
    "burst_size_factor": (0.05, 1.0), "burst_extra_edge": (0.0, 0.05), "burst_startup_grace_seconds": (0.0, 600.0),
    "slow_cycle_alert_seconds": (10.0, 3600.0), "summary_every_hours": (0, 24),
    "churn_count_sent": (False, True),
    "positions_stale_max_cycles": (0, 100),
    "positions_stale_max_seconds": (0.0, 3600.0),
    "handover_exit_max_seconds": (0.0, 600.0),
    "reduce_only_hysteresis": (0.0, 0.1),
    "pulls_cancel_all_over": (0, 500),
    # Quoting (R4, skew), thin-book pricing (R5), risk (R7), order lifecycle. All read from cfg where used, every
    # cycle, so a change applies on the next cycle (refresh_before_expiry is checked against order_ttl below).
    "skew_per_quote": (0.0, 0.05), "skew_max": (0.0, 0.1), "max_skew_through": (0.0, 0.05),
    "improve_ticks": (0, 3), "undercut_step_back": (0.0, 0.05),
    "ref_only_enabled": (False, True), "ref_only_max_gap": (0.005, 0.2), "ref_only_min_edge": (0.0, 0.1),
    "ref_only_size_frac": (0.0, 0.02), "ref_only_reduce_full": (False, True),
    "risk_swing_shock": (0.05, 0.5), "risk_z": (1.0, 6.0), "worst_case_backstop_frac": (0.3, 0.9),
    "order_ttl": (300.0, 7200.0), "refresh_before_expiry": (30.0, 900.0), "batch_size": (1, 50),
    "kelly_no_edge_frac": (0.0, 0.01), "take_ref_max_age_seconds": (0.0, 300.0),
    "arb_two_sided": (False, True),
    "arb_min_profit_buy": (0.005, 0.20),
    "arb_buy_min_sum": (0.5, 1.0),
    "arb_buy_min_ref_sum": (0.8, 1.0),
    "pair_unwind_enabled": (False, True),
    "pair_unwind_min_profit": (0.0, 0.10),
    "pair_unwind_max_frac": (0.0, 0.10),
    "pair_unwind_cooldown_seconds": (0.0, 3600.0),
    "limits_use_race_net": (False, True),
    "skew_age_enabled": (False, True),
    "skew_age_after_hours": (0.0, 48.0),
    "skew_age_per_hour": (0.0, 0.02),
    "skew_age_max": (0.0, 0.1),
    "capital_in_positions_max_frac": (0.0, 1.0),
    "capital_ceiling_adding_size_factor": (0.0, 1.0),
    "ref_only_use_tops": (False, True),
    "tops_max_age": (5.0, 900.0),
    "startup_books_first": (False, True),
    "startup_prime_seconds": (0.0, 1800.0),
    "startup_prime_missing_frac": (0.0, 1.0),
    "startup_prime_books": (1, 100),
    "startup_prime_reserve": (0, 60),
    "startup_prime_books_per_min": (1, 100),
    "startup_prime_held_max_seconds": (0.0, 3600.0),
    "unpriced_held_warn_cycles": (0, 1000),
    "fl_bias_enabled": (False, True),
    "fl_low": (0.0, 0.50),
    "fl_high": (0.50, 1.0),
    "fl_hysteresis": (0.0, 0.05),
    "fl_bad_side_extra_edge": (0.0, 0.05),
    "fl_bad_side_size_factor": (0.0, 1.0),
    "fl_mid_bid_extra_edge": (0.0, 0.03),
    "refill_cooldown_enabled": (False, True),
    "refill_cooldown_fills": (1, 20),
    "refill_cooldown_window_seconds": (1.0, 600.0),
    "refill_cooldown_min_shares": (0, 100000),
    "refill_cooldown_seconds": (0.0, 600.0),
    # R3 ladder. Tuples take a JSON list of 1..LADDER_MAX_LEVELS numbers, each in the range; ladder_markets a
    # comma list of the names given.
    "ladder_enabled": (False, True),
    "ladder_offsets": (0.005, 0.20), "ladder_size_mults": (0.0, 10.0),
    "ladder_headline_offsets": (0.005, 0.20), "ladder_headline_mults": (0.0, 10.0),
    "ladder_move": (0.0, 0.10), "ladder_pull_jump": (0.0, 0.20), "ladder_pull_seconds": (0.0, 3600.0),
    "ladder_markets": ("headline", "busy", "quiet"), "ladder_busy_size_frac": (0.0, 0.20),
    "ladder_min_cash_frac": (0.0, 1.0), "ladder_min_writes": (0, 100), "ladder_max_inv_quotes": (0.0, 100.0),
    "fast_unload_enabled": (False, True),
    "fast_unload_min_edge": (0.0, 0.20),
    "fast_unload_min_shares": (0, 100000),
    "fast_unload_seconds": (0.0, 3600.0),
    "fast_unload_edge": (0.0, 0.05),
    "fast_unload_size_mult": (0.0, 10.0),
    "reduce_join_best": (False, True),
    "reduce_join_min_edge": (0.0, 0.05),
    "reduce_join_min_shares": (0, 100000),
    "market_edge_enabled": (False, True),
    "market_edge_max": (0.005, 0.05),
    "turnover_control_enabled": (False, True),
    "turnover_window_hours": (0.5, 48.0),
    "turnover_min_shares_per_hour": (0.0, 100000.0),
    "turnover_dead_adding_factor": (0.0, 1.0),
    "turnover_dead_max_position_frac": (0.0, 1.0),
    "turnover_use_tape": (False, True),
    "turnover_alive_shares_per_hour": (0.0, 100000.0),
    "turnover_min_state_minutes": (0.0, 1440.0),
    "mark_frag_enabled": (False, True),
    "mark_frag_max_step_cash": (1.0, 100000.0),
    "mark_frag_window_hours": (1.0, 168.0),
    "mark_frag_min_samples": (2, 100000),
    "mark_frag_floor_sd": (0.0005, 0.10),
    "mark_frag_total_max_cash": (0.0, 1000000.0),
    "behind_best_size_enabled": (False, True),
    "behind_best_ticks": (1, 20),
    "behind_best_size_factor": (0.0, 1.0),
    "behind_best_min_size": (0, 100000),
    "no_chase_enabled": (False, True),
    "no_chase_tolerance_ticks": (1, 10),
    "no_chase_fv_epsilon": (0.0, 0.02),
    "ttl_tiers_enabled": (False, True),
    "order_ttl_busy": (300.0, 7200.0),
    "order_ttl_quiet": (300.0, 7200.0),
    "ttl_jitter_frac": (0.0, 0.5),
    "ttl_busy_size_frac": (0.0, 0.20),
    "ttl_expire_as_cancel": (False, True),
    "ttl_expire_grace_seconds": (0.0, 60.0),
    # --- Package 5: T2.1 tilt-corrected reference ---
    "ref_tilt_enabled": (False, True),
    "ref_tilt_headline": (False, True),
    "ref_tilt_min_markets": (5, 1000),
    "ref_tilt_halflife_min": (0.5, 1440.0),
    "ref_tilt_max": (0.0, 0.3),
    "ref_tilt_winsor": (0.005, 0.3),
    "ref_tilt_estimator": ("slope", "median", "wls"),     # ONE of these (ONE_OF_SETTINGS)
    # --- Package 5: B kelly_edge_cap, A reduce_from_book ---
    "kelly_edge_cap": (0.0, 0.10),
    "reduce_from_book": (False, True),
    "reduce_from_book_pause_s": (0.0, 3600.0),
    "reduce_from_book_headline": (False, True),
    "pair_unwind_passive": (False, True),
    "pair_unwind_max_cost": (0.0, 0.02),
    # --- Package 5: T2.4 tilt exposure limit ---
    "tilt_exposure_max_frac": (0.0, 1.0),
    "tilt_exposure_headline": (False, True),
    # --- Package 5: C hold target ---
    "hold_target_hours": (0.0, 48.0),
    "hold_unload_budget_frac": (0.0, 0.10),
    "hold_take_max_per_min": (0, 10),
    "hold_target_headline": (False, True),
    # (Package 5 T2.3 ref_tilt_carry_days is a HARD GATE and deliberately NOT here: changing it from 0 needs a code
    #  change and a restart, never a live override.)
    # --- Package 5: X5 gap-size shrink ---
    "gap_size_shrink": (0.0, 0.2),
    "gap_size_floor": (0.0, 1.0),
    # --- Package 5: X11 reduce_from_book scope ---
    "reduce_from_book_dead_only": (False, True),
    "reduce_from_book_max_turnover": (0.0, 100000.0),
    # --- Package 5: X12 takes measured from the tilted reference ---
    "take_tilted_ref": (False, True),
    # --- Package 5: T2.1 ramp-in ---
    "ref_tilt_rampin_min": (0.0, 1440.0),
    # --- Package 6 candidate: backstop soft band ---
    "backstop_soft_frac": (0.0, 0.3),
    # --- Package 6 candidate: exits keep quoting in reduce-only ---
    "exit_quotes_in_reduce_only": (False, True),
    "pair_passive_in_reduce_only": (False, True),
    # --- Package 6 candidate: tail adding-size factor ---
    "tail_adding_factor": (0.0, 1.0),
    # --- Package 6: reduce NO holdings as covered NO sales ---
    "reduce_no_as_sell": (False, True),
    # --- Package 7: NO+NO sets ---
    "no_set_aware_bids": (False, True),
    "pair_no_unwind_max_cost": (-1.0, 0.05),
    # --- Package 7: pair unwind follow-up ---
    "pair_unwind_followup": (False, True),
    "pair_unwind_followup_max_cost": (0.0, 0.05),
    "pair_unwind_followup_tries": (1, 50),
    "pair_no_unwind_max_per_cycle": (1, 10),
    # --- Package 8: tilt exits ---
    "tilt_exit_priority": (False, True),
    "tilt_exit_full_size": (False, True),
    "adding_factor_capital_on": (0.0, 1.0),
    "capital_ceiling_adding_size_factor_resume": (0.0, 1.0),
    "ref_guard_tilted": (False, True),
    "ref_guard_exits": (False, True),
}
MAX_ORDER_TTL = 7200.0    # no order of ours lives longer than this (dead-man's switch), whatever the TTL settings
LADDER_MAX_LEVELS = 8
ONE_OF_SETTINGS = {"ref_tilt_estimator"}   # string settings that take exactly one of their OVERRIDABLE names


def validate_overrides(raw, cfg):
    """{name: value} from the overrides file -> ({name: checked value}, [problems]). Unknown names, wrong types
    and out-of-range values are refused (and reported), never applied."""
    good, bad = {}, []
    if not isinstance(raw, dict):
        return good, ["the file must hold one JSON object, e.g. {\"min_edge\": 0.015}"]
    for k, v in raw.items():
        if k not in OVERRIDABLE:
            bad.append(f"{k}: not a live setting")
            continue
        cur = getattr(cfg, k)
        if isinstance(cur, str):                           # a comma list of allowed names (ladder_markets)
            names = [x.strip() for x in v.split(",")] if isinstance(v, str) else None
            if k in ONE_OF_SETTINGS:                       # exactly one allowed name (ref_tilt_estimator)
                if names is None or len(names) != 1 or names[0] not in OVERRIDABLE[k]:
                    bad.append(f"{k}: must be one of {', '.join(OVERRIDABLE[k])}")
                    continue
            if names is None or any(x not in OVERRIDABLE[k] for x in names if x):
                bad.append(f"{k}: must be a comma list of {', '.join(OVERRIDABLE[k])}")
                continue
            good[k] = ",".join(x for x in names if x)
            continue
        lo, hi = OVERRIDABLE[k]
        if isinstance(cur, tuple):                         # a list of numbers (ladder offsets / size multiples)
            if (not isinstance(v, (list, tuple)) or not 1 <= len(v) <= LADDER_MAX_LEVELS
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not lo <= x <= hi for x in v)):
                bad.append(f"{k}: must be a list of 1..{LADDER_MAX_LEVELS} numbers in {lo}..{hi}")
                continue
            good[k] = tuple(float(x) for x in v)
            continue
        if isinstance(cur, bool):
            if not isinstance(v, bool):
                bad.append(f"{k}: must be true or false")
                continue
        elif isinstance(cur, int):
            if isinstance(v, bool) or not isinstance(v, int):
                bad.append(f"{k}: must be a whole number")
                continue
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            bad.append(f"{k}: must be a number")
            continue
        if not isinstance(cur, bool) and not (lo <= v <= hi):
            bad.append(f"{k}: {v} is outside {lo}..{hi}")
            continue
        good[k] = float(v) if isinstance(cur, float) else v
    # An order must live well past its refresh point, or every order is "about to expire" as soon as it's placed
    # and gets replaced every cycle. (A key not in the file is judged at its current value.)
    ttl, refresh = good.get("order_ttl", cfg.order_ttl), good.get("refresh_before_expiry", cfg.refresh_before_expiry)
    if refresh * 2 > ttl:
        for k in ("order_ttl", "refresh_before_expiry"):
            if k in good:
                del good[k]
                bad.append(f"{k}: refresh_before_expiry ({refresh:.0f}) must be at most half of order_ttl ({ttl:.0f})")
    # TTL saver: the same rule for the shortest tier TTL after jitter (order_ttl_for also clamps, this reports it).
    keys = ("order_ttl_busy", "order_ttl_quiet", "ttl_jitter_frac")
    tiers = [good.get(k, getattr(cfg, k)) for k in keys]
    refresh = good.get("refresh_before_expiry", cfg.refresh_before_expiry)
    if good.get("ttl_tiers_enabled", cfg.ttl_tiers_enabled) and min(tiers[0], tiers[1]) * (1 - tiers[2]) < 2 * refresh:
        for k in keys + ("refresh_before_expiry", "ttl_tiers_enabled"):
            if k in good:
                del good[k]
                bad.append(f"{k}: the shortest tier TTL after jitter ({min(tiers[0], tiers[1]) * (1 - tiers[2]):.0f}) "
                           f"must be at least twice refresh_before_expiry ({refresh:.0f})")
    return good, bad


MARKET_EDGE_RANGE = (0.005, 0.05)


def validate_market_edge(raw, known):
    """market_edge.json -> ({eid: min_edge}, [problems]). Entries are {"min_edge": x, ...} or a bare number x, x in
    MARKET_EDGE_RANGE (dollars); keys starting with "_" (metadata) are skipped; an unknown exchange id or a bad
    value is refused (that entry only)."""
    good, bad = {}, []
    if not isinstance(raw, dict):
        return good, ["the file must hold one JSON object {exchange id: {\"min_edge\": ...}}"]
    lo, hi = MARKET_EDGE_RANGE
    for k, v in raw.items():
        k = str(k)
        if k.startswith("_"):
            continue
        if k not in known:
            bad.append(f"{k}: unknown exchange id")
            continue
        x = v.get("min_edge") if isinstance(v, dict) else v
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not (lo <= x <= hi):
            bad.append(f"{k}: min_edge {x!r} is not a number in {lo}..{hi}")
            continue
        good[k] = float(x)
    return good, bad

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
        self.wbudget = float(cfg.writes_per_minute)    # ...and for order writes on their own (self-tuning)
        self._wwindow = deque()           # start times of writes in the last BUDGET_WINDOW seconds
        self.write_times = deque(maxlen=500)   # (time done, seconds on the wire, timed out) per write attempt
        self.rate_limited = 0             # how many 429s we've had (shown in status.json - should stay 0)
        self.paused_until = 0.0           # monotonic end of the exchange's 429 pause (Retry-After); 0 = none
        self.pauses_total = 0             # 429 pauses started (several 429s inside one pause extend it)
        self.write_budget_wait_total = 0  # main-thread writes not sent: they would have waited (WRITE_BUDGET_WAIT)
        self.tl = threading.local()       # per thread: max_write_wait (the main thread's cap on a write's wait)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"})
        # Keep one open connection per thread that can talk to the exchange at once, so none has to open (and
        # then throw away: "Connection pool is full, discarding connection", 2 Oct 08:08:57) a fresh TLS
        # connection, which costs 0.2-0.5 s and inflates the write times burst mode watches.
        self.s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=self.pool_size(cfg), pool_block=False))

    @staticmethod
    def pool_size(cfg):
        """Connections to keep: book downloads (parallel_requests) + order writes (parallel_writes) + the main
        thread + the self-test thread + the realtime token refresh + one spare."""
        return max(1, cfg.parallel_requests) + max(1, cfg.parallel_writes) + 4

    BUDGET_WINDOW = 60.0                  # seconds the per-minute budget is measured over

    def throttle(self, write=False):
        """Rate limiter for every request, across all threads:
          1. never more than `self.budget` requests in any BUDGET_WINDOW seconds (hard cap), and
          2. request starts at least `self.gap` apart (smooths bursts), and
          3. nothing starts during the exchange's 429 pause (paused_until).
        A request that would break a rule waits until it wouldn't (sleeping WITHOUT the lock). A write on a thread
        with tl.max_write_wait set (the main thread, during a cycle) that would wait longer raises ApiError 429
        WRITE_BUDGET_WAIT instead, unsent and unreserved: the main loop never blocks on the write budget."""
        with self._lock:
            now = time.monotonic()
            while self._window and now - self._window[0] >= self.BUDGET_WINDOW:
                self._window.popleft()
            start = max(now, self._next_start, self.paused_until)
            if len(self._window) >= int(self.budget):
                start = max(start, self._window[-int(self.budget)] + self.BUDGET_WINDOW)
            general = start
            if write:
                while self._wwindow and now - self._wwindow[0] >= self.BUDGET_WINDOW:
                    self._wwindow.popleft()
                if len(self._wwindow) >= int(self.wbudget):
                    start = max(start, self._wwindow[-int(self.wbudget)] + self.BUDGET_WINDOW)
                cap = getattr(self.tl, "max_write_wait", None)
                if cap is not None and start - now > cap:
                    self.write_budget_wait_total = getattr(self, "write_budget_wait_total", 0) + 1
                    raise ApiError(429, "WRITE_BUDGET_WAIT", f"a write would wait {start - now:.0f} s for the write "
                                   f"budget / rate-limit pause (main thread: at most {cap:.0f} s) - not sent")
                bisect.insort(self._wwindow, start)
            self._next_start = general + self.gap   # a write waiting on the WRITE budget never holds up reads
            # Kept sorted: a write held back by the write budget starts later than reads throttled after it, and
            # the clean-up above and the [-budget] lookup both assume oldest-first order.
            bisect.insort(self._window, start)
        while start > now:
            time.sleep(start - now)
            # A 429 pause that began while this request slept: wait it out too, instead of knocking during it
            # (each such request earned another 429 and, before, another silent 60 s pause).
            now = time.monotonic()
            with self._lock:
                start = self.paused_until

    def pause_left(self):
        """Seconds left of the exchange's 429 pause (0 = none)."""
        return max(0.0, getattr(self, "paused_until", 0.0) - time.monotonic())

    def write_wait(self):
        """Seconds a write sent now would wait (write budget full, or a 429 pause)."""
        if not hasattr(self, "_wwindow"):         # (test doubles without the limiter)
            return 0.0
        with self._lock:
            now = time.monotonic()
            wait_s = max(0.0, self.paused_until - now, self._next_start - now)
            live = [t for t in self._wwindow if now - t < self.BUDGET_WINDOW]
            if len(live) >= int(self.wbudget):
                wait_s = max(wait_s, live[-int(self.wbudget)] + self.BUDGET_WINDOW - now)
            return wait_s

    def pause_state(self):
        """For status.json: the 429 pause now, how many there were, the 429s."""
        left = self.pause_left()
        return {"paused_until": iso(utcnow() + timedelta(seconds=left)) if left > 0 else None,
                "pause_seconds_left": round(left, 1), "pauses_total": getattr(self, "pauses_total", 0),
                "rate_limited_total": getattr(self, "rate_limited", 0)}

    def budget_left(self):
        """How many more requests fit in the budget right now."""
        with self._lock:
            now = time.monotonic()
            used = sum(1 for t in self._window if now - t < self.BUDGET_WINDOW)
            return int(self.budget) - used

    def write_ceiling(self):
        """What the self-tuning write budget may grow to: writes_per_minute_max (never below the start value)."""
        return float(max(self.cfg.writes_per_minute, getattr(self.cfg, "writes_per_minute_max", 0)))

    def writes_left(self):
        """How many more order writes fit in the write budget right now."""
        with self._lock:
            now = time.monotonic()
            return int(self.wbudget) - sum(1 for t in self._wwindow if now - t < self.BUDGET_WINDOW)

    def call(self, method, path, params=None, body=None, ok=(200, 201, 207), retry_sent=True):
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
            self.throttle(write=method != "GET")
            t_sent = time.monotonic()
            try:
                r = self.s.request(method, self.cfg.base_url + path, timeout=self.cfg.request_timeout,
                                   params={k: v for k, v in (params or {}).items() if v is not None},
                                   data=json.dumps(body) if body is not None else None)
                self.last_date = (r.headers.get("Date"), utcnow())   # server clock vs ours (see Bot.check_clock)
            except requests.RequestException as e:
                # A write that timed out AFTER it was sent is usually still running on the exchange: retrying it
                # at once only earns 409 REQUEST_IN_FLIGHT (day one, every time) and costs a request. Hand it
                # back as "outcome unknown" instead; the bot recovers it from the open-orders list.
                sent = isinstance(e, requests.exceptions.ReadTimeout)
                if method != "GET":
                    self.write_times.append((time.monotonic(), time.monotonic() - t_sent, sent))
                if attempt == retries or (sent and not retry_sent and self.cfg.write_fail_fast):
                    raise ApiError(0, "NETWORK", str(e))
                time.sleep(delay); delay = min(delay * 2, 8)
                continue

            if method != "GET":
                self.write_times.append((time.monotonic(), time.monotonic() - t_sent, False))
            # A proxy can answer with an HTML error page; never let that crash the bot.
            try:
                data = r.json() if r.content else {}
            except ValueError:
                data = {"error": {"code": "BAD_RESPONSE", "message": r.text[:200]}}

            if r.status_code in ok:
                self.gap = max(self.cfg.min_request_gap, self.gap * 0.99)   # drift back to normal speed
                self.budget = min(self.cfg.requests_per_minute, self.budget + 1 / 60)   # ~+1 per 60 successes
                if method != "GET":   # additive increase, up to writes_per_minute_max (AIMD; cut on a 429 below)
                    self.wbudget = min(self.write_ceiling(), self.wbudget + 1 / 60)
                return r.status_code, data

            err = data.get("error") if isinstance(data, dict) else None
            if not isinstance(err, dict):         # some errors are plain text: {"error": "Not found"} (2026-10-02 outage)
                err = {"message": str(err)} if err else {}
            ra = r.headers.get("Retry-After", "")
            retry_after = float(ra) if ra.replace(".", "", 1).isdigit() else None
            if r.status_code == 429:
                # Rate limited. Don't just retry this one request: pause EVERY thread for as long as the
                # server asks, and slow down for good, so we never keep knocking on a closed door.
                # A 429 inside a pause EXTENDS it to Retry-After from now (never added up); the budgets are cut
                # once per pause, not per 429. Every 429 is logged (2 Oct: 20 counted, none logged - the old
                # "already paused" test was fooled by ordinary queued requests).
                pause = retry_after if retry_after is not None else 5.0
                with self._lock:
                    now = time.monotonic()
                    already_paused = self.paused_until > now
                    left_before = max(0.0, self.paused_until - now)
                    self.paused_until = max(self.paused_until, now + pause)
                    self._next_start = max(self._next_start, self.paused_until)
                    if not already_paused:        # the budget was too generous: cut it by a quarter
                        self.pauses_total += 1
                        self.gap = min(self.cfg.max_request_gap, self.gap * 2)
                        self.budget = max(20.0, self.budget * 0.75)
                        if method != "GET" or self.wbudget > self.cfg.writes_per_minute:
                            # (a per-key limit 429s the more frequent reads first: a grown write budget is cut too)
                            self.wbudget = max(10.0, self.wbudget * self.cfg.write_budget_cut)
                    self.rate_limited += 1
                    until = self.paused_until - now
                log.warning("RATE LIMITED (429) %s %s: Retry-After %s; %s - all requests paused %.0f s; budget %.0f/min, "
                            "writes %.0f/min (429 #%d, pause #%d)", method, path.split("?")[0], ra or "-",
                            f"already paused ({left_before:.0f} s left): pause extended" if already_paused
                            else "new pause", until, self.budget, self.wbudget, self.rate_limited, self.pauses_total)
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
        status, res = self.call("POST", "/orders/cancel-all", body=body, ok=(200, 207, 422), retry_sent=False)
        if status == 200:
            return True
        log.warning("cancel-all partial: %s", res.get("errors"))
        return not self.open_orders(tid, eid)   # API docs: confirm nothing rests before re-quoting

    def cancel_order(self, order_id):
        """Cancel one order. 404/409 mean it's already gone (filled or cancelled) - that's fine too."""
        if not self.live:
            return True
        try:
            self.call("DELETE", f"/orders/{order_id}", ok=(200,), retry_sent=False)
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
        _, res = self.call("POST", "/orders/batch", body=body, ok=(200, 207, 422), retry_sent=False)
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
        self.trade_log = deque(maxlen=20000)   # (unix time, trade item) for the recorder (see take_trades)
        self.flow_log = deque(maxlen=20000)    # (unix time, exchange id, quantity) for turnover control (take_flow)
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

    def take_trades(self):
        """Tournament trades reported since the last call: [(unix time received, trade item)]."""
        with self.lock:
            out = list(self.trade_log)
            self.trade_log.clear()
        return out

    def take_flow(self):
        """Tournament trades since the last call, for turnover control: [(unix time, exchange id, quantity)]."""
        with self.lock:
            out = list(self.flow_log)
            self.flow_log.clear()
        return out

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
                    self.trade_log.append((time.time(), item))
                    self.flow_log.append((time.time(), str(item["exchangeId"]), _num(item.get("quantity"))))
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
            self.session_connected_at = None
            try:
                await self._session()
                backoff = 1.0
            except Exception as e:
                # A session that was healthy for a while ended unexpectedly: that's a fresh drop, not a
                # repeat failure, so start again from the shortest wait (day one: 2, 4, 8, 16 s over 2 hours).
                up = self.session_connected_at
                if up is not None and time.monotonic() - up >= self.cfg.realtime_healthy_seconds:
                    backoff = 1.0
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
            self.session_connected_at = time.monotonic()
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


def wire_order(o):
    """Package 6 (reduce_no_as_sell): an order as the bot builds and tracks it (YES terms: "buy"/"sell" YES at a YES
    price) -> the request body actually sent. Only an order marked "_no_sell" (a bid that buys back NO we hold, see
    Bot.cover_no_qty) changes: "buy YES @ p" goes out as the covered sale "sell NO @ 1-p", which needs no cash. It
    reads back through parse_order as our bid at p, so everything after the send keeps working in YES terms."""
    if "_no_sell" not in o:
        return o
    w = {k: v for k, v in o.items() if k != "_no_sell"}
    if o["_no_sell"]:
        w.update(side="no", action="sell", price=round(1 - o["price"], 3))
    return w


def reserved_cash(raw_orders):
    """Cash our open orders have locked up. A buy of `qty` shares at limit `p` can cost qty*p (p is
    in that order's own side, so a "buy NO @ 0.825" locks 0.825 a share). Selling shares we already
    hold locks no cash; the engine reports those as sells, so they're skipped."""
    total = 0.0
    for o in raw_orders:
        if parse_order(o) and str(o.get("action")).lower() == "buy":
            total += float(o["quantity"]) * float(o["priceLimit"])
    return total


def _num(x):
    """float(x), or None if it isn't a number."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


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


def others_top(top, mine):
    """Other traders' (best bid, best ask) from a bulk-price reading `top`, which includes our own orders.
    A side is None when it is empty, or when one of our orders sits at or better than that price: then the
    reading can't tell whether anybody else is there (and where), so nothing is assumed about it."""
    bid, ask = top
    if bid is not None and any(o.is_bid and o.price >= bid - 1e-9 for o in mine):
        bid = None
    if ask is not None and any(not o.is_bid and o.price <= ask + 1e-9 for o in mine):
        ask = None
    return bid, ask


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


def tilted_ref(r, s, legs):
    """Polymarket price r as the tournament's favourite-longshot tilt s would price it: c + (1 - s)(r - c), with
    c = 1 / legs in the race (a lone market counts as two-sided: c = 0.5)."""
    c = 1.0 / legs if legs and legs > 1 else 0.5
    return c + (1 - s) * (r - c)


def carry_ramp(s, hours_to_close, days):
    """T2.3: the tilt applied in the blend inside the last `days` days before the close, s x min(1, hours / (24 days)),
    ramping linearly to 0 at the close. days <= 0 (off), or no known close: s unchanged. Never below 0, never above s."""
    if not days or days <= 0 or hours_to_close is None or hours_to_close == float("inf"):
        return s
    return s * max(0.0, min(1.0, hours_to_close / (24.0 * days)))


TILT_RAMP_RESUME_SECONDS = 600.0   # T2.1: a restart within this of the last status write resumes the ramp-in


def rampin_factor(now, on_at, minutes):
    """T2.1 ramp-in: the share of the tilt estimate applied `now`, min(1, (now - on_at) / (60 minutes)), rising
    linearly from 0 when ref_tilt_enabled was switched on (on_at, monotonic seconds). minutes <= 0 (no ramp) or
    on_at None (not started): 1.0. Never below 0, never above 1."""
    if not minutes or minutes <= 0 or on_at is None:
        return 1.0
    return max(0.0, min(1.0, (now - on_at) / (60.0 * minutes)))


def blend_fv(book_fv, r, cfg, s=0.0, legs=2, headline=False, hours_to_close=float("inf")):
    """The main loop's blend: book price leaned toward Polymarket by ref_weight. With ref_tilt_enabled (and, in a
    headline market, ref_tilt_headline) toward the tilt-corrected Polymarket price instead of the raw one; with
    ref_tilt_carry_days > 0 the tilt applied fades to 0 over the last N days before the close (carry_ramp)."""
    if cfg.ref_tilt_enabled and (cfg.ref_tilt_headline or not headline):
        s = carry_ramp(s, hours_to_close, getattr(cfg, "ref_tilt_carry_days", 0.0))
        if s:                                     # s 0 (or ramped to 0): the raw r exactly
            r = tilted_ref(r, s, legs)
    return (1 - cfg.ref_weight) * book_fv + cfg.ref_weight * r


TILT_RATIO_MIN_X = 0.1   # T2.1 "median"/"wls" estimators: only markets with |r - c| above this give a ratio g / x


class TiltEstimator:
    """Cross-sectional estimate of the tilt s: least squares of the gap (r - book_fv, winsorised at
    +-ref_tilt_winsor) on (r - c), through the origin (ref_tilt_estimator "slope"); or the median ("median") or
    mean ("wls") of the per-market ratios gap / (r - c) over |r - c| > TILT_RATIO_MIN_X (fewer such markets than
    ref_tilt_min_markets: hold). Fewer than ref_tilt_min_markets samples: hold the last value.
    Smoothed by an EMA with half-life ref_tilt_halflife_min (time-based; the first estimate is taken as is) and
    clipped to [0, ref_tilt_max]."""

    def __init__(self, cfg=CFG):
        self.cfg = cfg
        self.s = 0.0          # current estimate
        self.n = 0            # samples in the last update
        self.ready = False    # a valid estimate has been taken (or restored)
        self.t = None         # time of the last valid estimate (seconds; None after a restore)
        self.diag = {}        # the last update's raw readings by every estimator (see update)

    def update(self, samples, now):
        """samples: [(r, book_fv, legs), ...]; now: seconds (any clock, as long as it is always the same one).
        Also leaves self.diag: this cycle's raw (unclipped, unsmoothed) reading by every estimator, the sample
        counts and the share of the slope's x^2 weight in markets whose gap is at the winsor (status tilt_diag)."""
        cfg = self.cfg
        self.n = len(samples)
        w = cfg.ref_tilt_winsor
        kind = getattr(cfg, "ref_tilt_estimator", "slope")
        num = den = pinned = 0.0
        ratios = []
        for r, bfv, legs in samples:
            x = r - (1.0 / legs if legs and legs > 1 else 0.5)
            g = max(-w, min(w, r - bfv))
            num += x * g
            den += x * x
            if abs(r - bfv) >= w - 1e-9:
                pinned += x * x
            if abs(x) > TILT_RATIO_MIN_X:
                ratios.append(g / x)              # this market's own implied tilt
        ratios.sort()
        h = len(ratios) // 2
        alt = {"slope": num / den if den > 1e-12 else None,
               "median": (ratios[h] if len(ratios) % 2 else 0.5 * (ratios[h - 1] + ratios[h])) if ratios else None,
               "wls": sum(ratios) / len(ratios) if ratios else None}
        self.diag = {"n": self.n, "n_ratio": len(ratios),
                     "pinned_weight": round(pinned / den, 3) if den > 1e-12 else None,
                     **{k: (round(v, 4) if v is not None else None) for k, v in alt.items()}}
        if self.n < max(1, cfg.ref_tilt_min_markets):
            return self.s
        if kind in ("median", "wls"):
            if len(ratios) < max(1, cfg.ref_tilt_min_markets):
                return self.s
            est = alt[kind]
        elif den <= 1e-12:
            return self.s
        else:                                     # "slope" (and any unknown value)
            est = num / den
        raw = max(0.0, min(cfg.ref_tilt_max, est))
        if not self.ready:
            self.s, self.ready = raw, True        # first valid estimate: taken as is
        else:                                     # after a restore (t None) the first update only sets the clock
            dt = max(0.0, now - self.t) if self.t is not None else 0.0
            self.s += (1 - 0.5 ** (dt / (60.0 * max(1e-9, cfg.ref_tilt_halflife_min)))) * (raw - self.s)
        self.s = max(0.0, min(cfg.ref_tilt_max, self.s))
        self.t = now
        return self.s

    def to_dict(self):
        return {"s": self.s, "ready": self.ready}

    def from_dict(self, d):
        """Restore a saved estimate (missing or bad = 0, not ready). The EMA continues from it."""
        try:
            d = d if isinstance(d, dict) else {}
            self.s = max(0.0, min(self.cfg.ref_tilt_max, float(d.get("s") or 0.0)))
            self.ready = bool(d.get("ready", self.s > 0))
        except (TypeError, ValueError):
            self.s, self.ready = 0.0, False
        self.t = None
        return self


def normalise(fvs):
    """Outcomes that exclude each other (one party wins a race) must add up to 1; scale them so
    they do. Left alone if any member has no price, since we can't scale what we can't see."""
    if not fvs or any(v is None for v in fvs.values()):
        return fvs
    total = sum(fvs.values())
    return {k: v / total for k, v in fvs.items()} if total > 0 else fvs


def race_variance(legs):
    """Variance of one race's settlement payout. legs = [(net YES shares, probability), ...]; exactly one leg
    wins in a race (probabilities scaled to sum to 1); a lone market wins with its own probability."""
    if len(legs) == 1:
        (x, p), = legs
        return x * x * p * (1 - p)                   # YES or NO shares: payout differs by |x| between outcomes
    tot = sum(p for _, p in legs) or 1.0
    probs = [p / tot for _, p in legs]
    pays = []
    for i in range(len(legs)):
        pays.append(sum((x if j == i else 0.0) if x > 0 else (0.0 if j == i else -x) for j, (x, _) in enumerate(legs)))
    mean = sum(p * v for p, v in zip(probs, pays))
    return sum(p * (v - mean) ** 2 for p, v in zip(probs, pays))


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
    # The size before size FACTORS (capital ceiling, favourite-longshot bad side) but within every position /
    # cash / risk limit: the biggest order already resting that may stay (None = the size itself).
    bid_max: int | None = field(default=None, compare=False)
    ask_max: int | None = field(default=None, compare=False)
    behind: bool = field(default=False, compare=False)   # behind-the-best sizing shrank an adding side (log / status)


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
    No edge (or a negative one) -> just the small kelly_no_edge_frac allowance. kelly_edge_cap > 0 caps the edge
    sized on (B: a persistent gap to Polymarket is not all edge).
    """
    allowance = int(cfg.kelly_no_edge_frac * bankroll)
    edge, cost = (p - price, price) if yes else (price - p, 1 - price)
    if edge <= 0 or cost <= 0:
        return allowance
    if cfg.kelly_edge_cap > 0:                    # B: size on the spread, not the gap (0 = off)
        edge = min(edge, cfg.kelly_edge_cap)
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


def age_skew(age_hours, eff_inv, cfg=CFG):
    """Extra reservation-price shift for a position held long: skew_age_per_hour for every hour beyond
    skew_age_after_hours, capped at skew_age_max, in the direction that unloads eff_inv (long -> lower)."""
    if not cfg.skew_age_enabled or not eff_inv or not age_hours or age_hours <= cfg.skew_age_after_hours:
        return 0.0
    a = min(cfg.skew_age_max, cfg.skew_age_per_hour * (age_hours - cfg.skew_age_after_hours))
    return a if eff_inv > 0 else -a
def fl_side(fv, prev, cfg=CFG):
    """Favourite-longshot bias: which side of a market priced at fv is the "bad" one -> (side, extra edge,
    size factor), side None = no bias. Below fl_low our bid buys the longshot students overpay for; above
    fl_high our ask sells the favourite they undersell. prev = last cycle's side for this market: once on, a
    bias holds until fv is fl_hysteresis back across the line, so a market sitting at 20c doesn't flip
    (and get re-quoted) every cycle. Mid-band bids get fl_mid_bid_extra_edge (default 0 = no bias)."""
    if not cfg.fl_bias_enabled or fv is None:
        return None, 0.0, 1.0
    h = cfg.fl_hysteresis
    if fv < cfg.fl_low or (prev == "bid" and fv < cfg.fl_low + h):
        return "bid", cfg.fl_bad_side_extra_edge, cfg.fl_bad_side_size_factor
    if fv > cfg.fl_high or (prev == "ask" and fv > cfg.fl_high - h):
        return "ask", cfg.fl_bad_side_extra_edge, cfg.fl_bad_side_size_factor
    if cfg.fl_mid_bid_extra_edge > 0:
        return "mid", cfg.fl_mid_bid_extra_edge, 1.0
    return None, 0.0, 1.0


MARK_FRAG_STEP_SECONDS = 600.0       # the mark-fragility step: 10 minutes
MARK_FRAG_MAX_STEP_FACTOR = 1.5      # two snapshots further apart than 15 min are a gap, not a step


def mark_step_changes(series, step=MARK_FRAG_STEP_SECONDS, max_factor=MARK_FRAG_MAX_STEP_FACTOR):
    """series = [(seconds, mid)] oldest first -> the mid's changes over ~step seconds: each point paired with the
    first point at least `step` later, if that one is at most step x max_factor later (overlapping pairs)."""
    out, j, n = [], 0, len(series)
    for i in range(n):
        t0, m0 = series[i]
        j = max(j, i + 1)
        while j < n and series[j][0] < t0 + step:
            j += 1
        if j < n and series[j][0] <= t0 + step * max_factor:
            out.append(series[j][1] - m0)
    return out


def mark_step_sd(series, min_samples=60, floor=0.002, step=MARK_FRAG_STEP_SECONDS):
    """Mark-fragility estimator: sd of the 10-min change of one market's mid, at least `floor`; None with fewer
    than min_samples changes (no estimate -> no cap)."""
    ch = mark_step_changes(series, step)
    if len(ch) < max(2, min_samples):
        return None
    mean = sum(ch) / len(ch)
    return max(math.sqrt(sum((c - mean) ** 2 for c in ch) / (len(ch) - 1)), floor)


def mark_frag_limit(sd, cfg, quote_size):
    """The adding side's position limit from the mark-fragility cap: max_step_cash / sd, never below one quote."""
    return max(cfg.mark_frag_max_step_cash / sd, quote_size or 0.0)


def gap_size_factor(fv, book_fv, cfg=CFG):
    """X5: the adding side's size factor where the quoted fv and the book's own price disagree: 1 at no gap, falling
    linearly to cfg.gap_size_floor at a gap of cfg.gap_size_shrink (and staying there). 1 when off (shrink <= 0)."""
    if cfg.gap_size_shrink <= 0 or fv is None or book_fv is None:
        return 1.0
    return max(cfg.gap_size_floor, 1.0 - abs(fv - book_fv) / cfg.gap_size_shrink)


def backstop_soft_factor(worst, equity, cfg=CFG):
    """Package 6 candidate (backstop soft band): the adding side's size factor as the sum-of-maxima worst case
    nears the reduce-only backstop. 1 when off (backstop_soft_frac <= 0, risk_model not "correlated", no account
    value) or worst <= (backstop - soft) x equity; 0 at or above backstop x equity; linear in between."""
    soft = cfg.backstop_soft_frac
    if soft <= 0 or cfg.risk_model != "correlated" or not equity or worst is None:
        return 1.0
    hi = cfg.worst_case_backstop_frac * equity
    lo = (cfg.worst_case_backstop_frac - soft) * equity
    if worst <= lo:
        return 1.0
    if worst >= hi:
        return 0.0
    return (hi - worst) / (hi - lo)


def compute_quote(fv, inv, eff_inv, best_bid, best_ask, cfg=CFG, reduce_only=False, no_bid=False, no_ask=False,
                  bid_cap=None, ask_cap=None, kelly_p=None, bankroll=None, shift=0.0, order_size=None,
                  position_limit=None, min_edge=None, reduce_size=None, net_inv=None, age_hours=0.0,
                  adding_factor=1.0, bias_side=None, bias_edge=0.0, bias_size=1.0, unload_side=None,
                  unload_edge=0.0, unload_size=None, adding_limit_factor=1.0, frag_limit=None, behind_best=True,
                  reduce_fv=None, why=None):
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
    min_edge           overrides cfg.min_edge (e.g. wider in markets priced from Polymarket alone)
    reduce_size        bigger size allowed on the side that SHRINKS the position (up to the position itself),
                       e.g. a thin-book market quoting 100 shares that holds 7,585 from before
    net_inv            the race-netted position (None = eff_inv): with limits_use_race_net the position limit also
                       applies to it on the side that grows it; it also decides which side is "adding"
    age_hours          share-weighted age of this market's position: adds the age skew (skew_age_*)
    adding_factor      size factor for the side that grows |net_inv| (capital ceiling; 1 = no change)
    bias_side          "bid" / "ask" / None: the side that quotes bias_edge further from r (capped at
                       max_half_spread) and at bias_size times its size (favourite-longshot bias, see fl_side).
                       Ignored on a side that shrinks this exchange's position: that side quotes normally
                       (its size beyond the position itself still gets bias_size)
    unload_side        "bid" / "ask" / None: fast unload window (see Bot.note_unloads). That side, if it shrinks this
                       exchange's position, quotes unload_edge from fv (or closer, if the skews already put it
                       there), never crossing the best other order, at unload_size shares capped by the position
    adding_limit_factor  the side that grows |net_inv| WANTS at most this fraction of the normal position limit
                       (turnover control: a market whose position cannot turn), at least 1 share while the normal
                       limits would quote it; bid_max / ask_max keep the normal limits. 1 = no change
    frag_limit         mark-fragility cap (Bot.mark_frag_limit_for): limit on |this exchange's position| on the side
                       that GROWS it only (bid when inv >= 0, ask when inv <= 0); the shrinking side is untouched
    behind_best        False = no behind-the-best sizing here (ref-only markets: already small); see
                       cfg.behind_best_size_enabled
    reduce_fv          reduce_from_book (A): the tournament book's own price, or None = off. The side that shrinks
                       THIS exchange's position (long -> ask, short -> bid) measures its band from it (same skew and
                       shift) when that is more aggressive than fv, never less; that side then quotes at most the
                       position. The adding side is capped 2 x min_edge behind the lowest reducing price that may
                       rest (bid <= ask keep limit - 2 x min_edge when long, mirror when short), so we never meet
                       our own order
    why                optional dict, filled with why["ro_clip"] = "", "bid", "ask" or "bid ask": the side(s) that
                       ONLY the reduce-only race-net clip emptied (no no_bid / no_ask block, no zero cap on it)
    """
    bankroll = bankroll or DEFAULT_BANKROLL
    max_order_cash = cfg.max_order_cash_frac * bankroll
    if order_size is None:
        order_size = cfg.order_size_frac * bankroll
    else:
        max_order_cash = max(max_order_cash, order_size)   # a planned size has already been capital-checked
    # 1. Reservation price = fair value shifted against our inventory. Long -> lower r -> we bid
    #    less eagerly and offer more eagerly, which pushes the position back toward flat.
    if cfg.skew_mode == "quote" and order_size > 0:
        skew = cfg.skew_per_quote * eff_inv / order_size
    else:
        skew = cfg.skew_per_share * eff_inv
    skew = max(-cfg.skew_max, min(cfg.skew_max, skew))
    skew += age_skew(age_hours, eff_inv, cfg)              # capped on its own, so skew_max stays the inventory cap
    r = fv - skew - shift
    # 1a. reduce_from_book (A): the reducing side's own reservation price, from the book when that is closer to it.
    r_bid = r_ask = r
    fv_bid = fv_ask = fv
    a_bid = a_ask = False
    if reduce_fv is not None:
        r_book = reduce_fv - skew - shift
        if inv >= 1 and r_book < r:               # long: the ask sells down toward the book's price
            r_ask, fv_ask, a_ask = r_book, min(fv, reduce_fv), True
        elif inv <= -1 and r_book > r:            # short: the bid buys back toward it
            r_bid, fv_bid, a_bid = r_book, max(fv, reduce_fv), True

    # 2. Allowed band for each side: at least min_edge, at most max_half_spread away from r.
    edge = cfg.min_edge if min_edge is None else min_edge
    widest = max(cfg.max_half_spread, edge)
    bias_bid = bias_side == "bid" and inv > -1         # (a side that shrinks a position quotes normally)
    bias_ask = bias_side == "ask" and inv < 1
    bid_edge = min(widest, edge + bias_edge) if bias_bid else edge
    ask_edge = min(widest, edge + bias_edge) if bias_ask else edge
    bid_lo, bid_hi = floor_tick(r_bid - widest), floor_tick(r_bid - bid_edge)
    ask_lo, ask_hi = ceil_tick(r_ask + ask_edge), ceil_tick(r_ask + widest)

    # 3. Penny: one tick better than the best other trader, so we're first in the queue while
    #    keeping the widest spread possible. Then clamp into the band. That clamp is what stops a
    #    penny war with another bot from pushing us below min_edge. No other quote -> band edge.
    #    R4: improve_ticks = 0 joins the best price instead; undercut_step_back > 0 quotes that far from r
    #    (not at min_edge) when another trader already sits inside our min_edge band.
    imp = cfg.improve_ticks * TICK
    bid = floor_tick(best_bid + imp) if best_bid is not None else bid_lo
    ask = ceil_tick(best_ask - imp) if best_ask is not None else ask_hi
    if cfg.undercut_step_back > 0:
        if best_bid is not None and best_bid > bid_hi + 1e-9:
            bid = floor_tick(r_bid - max(bid_edge, cfg.undercut_step_back))
        if best_ask is not None and best_ask < ask_lo - 1e-9:
            ask = ceil_tick(r_ask + max(ask_edge, cfg.undercut_step_back))
    bid = min(max(bid, bid_lo), bid_hi)
    ask = max(min(ask, ask_hi), ask_lo)
    if not reduce_only and cfg.max_skew_through < 1.0:
        # Skew sheds inventory by quoting less greedily, never by paying through our own fair value
        # (day one: fills at <= -1c edge lost -804 at the 60-min mid; rival bots pick those quotes off).
        bid_hi = min(bid_hi, floor_tick(fv_bid + cfg.max_skew_through))   # (fv_bid / fv_ask = fv unless A)
        ask_lo = max(ask_lo, ceil_tick(fv_ask - cfg.max_skew_through))
        bid, ask = min(bid, bid_hi), max(ask, ask_lo)

    # 4. Never cross another trader's order (that would trade instantly, as a taker).
    if best_ask is not None:
        bid = min(bid, floor_tick(best_ask - TICK))
    if best_bid is not None:
        ask = max(ask, ceil_tick(best_bid + TICK))

    # 4a. Reducing side joins the best other price on its side (reduce_join_best): never through fair, never crossing.
    if (cfg.reduce_join_best and (not reduce_only or cfg.exit_quotes_in_reduce_only)
            and abs(eff_inv) >= max(1, cfg.reduce_join_min_shares)):
        if eff_inv > 0 and best_ask is not None:
            ask = min(ask, max(ceil_tick(best_ask), ceil_tick(fv + cfg.reduce_join_min_edge)))   # never moves out
            if best_bid is not None:
                ask = max(ask, ceil_tick(best_bid + TICK))
            ask_lo = min(ask_lo, ask)
            bid = min(bid, floor_tick(ask - TICK))
        elif eff_inv < 0 and best_bid is not None:
            bid = max(bid, min(floor_tick(best_bid), floor_tick(fv - cfg.reduce_join_min_edge)))
            if best_ask is not None:
                bid = min(bid, floor_tick(best_ask - TICK))
            bid_hi = max(bid_hi, bid)
            ask = max(ask, ceil_tick(bid + TICK))
    # 4b. Fast unload window: the reducing side quotes near fair value (no pennying, never through fair).
    unload_bid = unload_side == "bid" and inv <= -1 and not reduce_only
    unload_ask = unload_side == "ask" and inv >= 1 and not reduce_only
    if unload_bid:
        bid = max(bid, min(floor_tick(fv - unload_edge), floor_tick(best_ask - TICK) if best_ask is not None else 1.0))
        bid_hi = max(bid_hi, bid)                  # (the keep limit: an order there is safe)
        ask = max(ask, ceil_tick(bid + TICK))
    if unload_ask:
        ask = min(ask, max(ceil_tick(fv + unload_edge), ceil_tick(best_bid + TICK) if best_bid is not None else 0.0))
        ask_lo = min(ask_lo, ask)
        bid = min(bid, floor_tick(ask - TICK))
    # 4c. reduce_from_book self-cross rule: the adding side stays 2 x min_edge behind the lowest (highest) reducing
    #     price that may rest (the keep limit), so our own bid never meets our own ask.
    if a_ask:
        keep = max(ask_lo, ceil_tick(best_bid + TICK)) if best_bid is not None else ask_lo
        cap = min(ask, keep) - 2 * edge
        if cap < PMIN - 1e-9:
            no_bid = True                         # no room for an adding bid under the reducing ask
        else:
            bid, bid_hi = min(bid, floor_tick(cap)), min(bid_hi, floor_tick(cap))
    if a_bid:
        keep = min(bid_hi, floor_tick(best_ask - TICK)) if best_ask is not None else bid_hi
        cap = max(bid, keep) + 2 * edge
        if cap > PMAX + 1e-9:
            no_ask = True
        else:
            ask, ask_lo = max(ask, ceil_tick(cap)), max(ask_lo, ceil_tick(cap))

    # 5. Size: shrink toward the position limit on each side, and cap the cash tied up per order.
    #    Limits: Kelly sizing when we have a liquid Polymarket price, else max_position_frac of the account.
    long_limit = short_limit = cfg.max_position_frac * bankroll
    if position_limit is not None:
        long_limit = short_limit = position_limit
    elif kelly_p is not None:
        long_limit = kelly_position(kelly_p, bid, bankroll, cfg, yes=True)     # most YES we'd hold
        short_limit = kelly_position(kelly_p, ask, bankroll, cfg, yes=False)   # most NO we'd hold
    net = eff_inv if net_inv is None else net_inv
    clipped = [False, False]                  # (the reduce-only race-net clip emptied the bid / ask; see why)

    def limited(bid_size, ask_size):
        """Steps 5-6 after the size factors: position, cash and risk limits (applied to the scaled sizes and,
        for bid_max / ask_max, to the unscaled ones alike)."""
        if reduce_size is not None and reduce_size > order_size:
            if inv < 0:
                bid_size = max(bid_size, min(reduce_size, -inv))   # buying back a short
            elif inv > 0:
                ask_size = max(ask_size, min(reduce_size, inv))    # selling down a long
        if cfg.limits_use_race_net:               # the race-netted position counts too, on the side that grows it
            if net > 0:
                bid_size = min(bid_size, long_limit - net)
            elif net < 0:
                ask_size = min(ask_size, short_limit + net)
        if frag_limit is not None:                # mark-fragility cap: only the side growing |inv| here
            if inv >= 0:
                bid_size = min(bid_size, frag_limit - inv)
            if inv <= 0:
                ask_size = min(ask_size, frag_limit + inv)
        bid_size = min(bid_size, max_order_cash / bid)          # buying YES costs `bid` a share
        ask_size = min(ask_size, max_order_cash / (1 - ask))    # selling YES = buying NO at 1-ask
        if unload_bid and unload_size is not None:              # only what it holds: never flips the position
            bid_size = min(max(1, unload_size), -inv)
        if unload_ask and unload_size is not None:
            ask_size = min(max(1, unload_size), inv)
        if a_bid:                                 # reduce_from_book: the book-priced side never flips the position
            bid_size = min(bid_size, -inv)
        if a_ask:
            ask_size = min(ask_size, inv)

        # 6. Risk overrides.
        if reduce_only:
            clipped[:] = [bid_size >= 1 and -eff_inv < 1, ask_size >= 1 and eff_inv < 1]
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
        return bid_size, ask_size

    bid_size = min(order_size, long_limit - inv)
    ask_size = min(order_size, short_limit + inv)
    bid_max, ask_max = limited(bid_size, ask_size)     # the sizes no factor shrank: the most that may stay
    if bias_side == "bid" and bias_size != 1.0:        # bad side: smaller, except the part that only unloads
        bid_size = min(bid_size, max(order_size * bias_size, -inv))
    if bias_side == "ask" and bias_size != 1.0:
        ask_size = min(ask_size, max(order_size * bias_size, inv))
    bid_size, ask_size = limited(bid_size, ask_size)
    # Turnover control: the side growing |net| WANTS no more than the smaller limit allows, like a size factor (the
    # bid_max / ask_max above keep the normal limits, so an order already resting within them stays). A side the
    # normal limits would quote keeps at least 1 share, so its resting order is not pulled when a market turns dead.
    hold_bid = hold_ask = False
    if adding_limit_factor < 1.0:
        f = max(0.0, adding_limit_factor)
        if net > 0 and bid_size >= 1:
            room = long_limit * f - inv
            if cfg.limits_use_race_net:
                room = min(room, long_limit * f - net)
            bid_size, hold_bid = max(1, min(bid_size, room)), True
        elif net < 0 and ask_size >= 1:
            room = short_limit * f + inv
            if cfg.limits_use_race_net:
                room = min(room, short_limit * f + net)
            ask_size, hold_ask = max(1, min(ask_size, room)), True
    if adding_factor < 1.0:                   # capital ceiling: the side growing |net| shrinks (0 = not quoted)
        if net >= 0:
            bid_size = min(bid_size, bid_size * adding_factor)
        if net <= 0:
            ask_size = min(ask_size, ask_size * adding_factor)
    behind = False
    if behind_best and cfg.behind_best_size_enabled and cfg.behind_best_size_factor < 1.0:
        # Behind-the-best sizing: an adding quote resting behind_best_ticks+ behind the best other order rarely
        # fills and locks cash; shrink it (floor behind_best_min_size, never growing it). Like adding_factor this
        # leaves bid_max / ask_max alone, so a full-size order already resting is kept (hot-fix 2.2).
        gap = cfg.behind_best_ticks * TICK - 1e-9
        f, floor = max(0.0, cfg.behind_best_size_factor), cfg.behind_best_min_size
        if net >= 0 and bid_size > 0 and best_bid is not None and best_bid - bid >= gap:
            new = min(bid_size, max(bid_size * f, floor))
            behind, bid_size = behind or new < bid_size, new
        if net <= 0 and ask_size > 0 and best_ask is not None and ask - best_ask >= gap:
            new = min(ask_size, max(ask_size * f, floor))
            behind, ask_size = behind or new < ask_size, new
    bid_size, ask_size = max(0, int(bid_size)), max(0, int(ask_size))   # the API only takes whole shares
    if hold_bid and adding_factor > 0:
        bid_size = max(1, bid_size)
    if hold_ask and adding_factor > 0:
        ask_size = max(1, ask_size)
    bid_max, ask_max = max(bid_size, int(bid_max)), max(ask_size, int(ask_max))

    if bid >= ask:
        return NO_QUOTE
    if why is not None:                       # (sizes from the last limited() call: the quoted ones)
        why["ro_clip"] = " ".join(
            s for s, hit in (("bid", clipped[0] and not bid_size and not no_bid and (bid_cap is None or bid_cap >= 1)),
                             ("ask", clipped[1] and not ask_size and not no_ask and (ask_cap is None or ask_cap >= 1)))
            if hit)
    # How far a resting order may sit from these prices and still be kept: never closer than min_edge
    # to r, never crossing the best other order.
    bid_limit = min(bid_hi, floor_tick(best_ask - TICK)) if best_ask is not None else bid_hi
    ask_limit = max(ask_lo, ceil_tick(best_bid + TICK)) if best_bid is not None else ask_lo
    return Quote(bid if bid_size else None, bid_size, ask if ask_size else None, ask_size, bid_limit, ask_limit,
                 bid_max if bid_max != bid_size else None, ask_max if ask_max != ask_size else None, behind)


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


def unsafe_order(o, price, size, limit, is_bid):
    """A resting order that must not stay: we want nothing on its side, it's beyond its limit price (the price
    past which the quote loses money; the target price when there's no limit), or bigger than now allowed."""
    if price is None:
        return True
    lim = limit if limit is not None else price
    return (o.price > lim + 1e-9 if is_bid else o.price < lim - 1e-9) or o.qty > size + 1e-9


def side_needs_change(resting, price, size, cfg, now, limit=None, is_bid=True, max_size=None, tol_ticks=None,
                      expire_as_cancel=False):
    """True if what's resting on ONE side of an exchange doesn't match what we want there.
    Leaving a good order alone keeps its place in the queue, which is worth money. So an order a tick
    (reprice_tolerance_ticks) off the target is kept, as long as it's inside `limit` (see Quote).
    max_size: the biggest order still acceptable (default: size). Burst mode passes the normal size here while
    `size` is the burst size, so a full-size order placed before the burst stays and a half-size one placed
    during it stays too - neither is cancelled and re-placed when the exchange is slow.
    tol_ticks: the tolerance instead of reprice_tolerance_ticks (no-chase). expire_as_cancel: an order about to
    expire is kept (it is left to expire instead of being refreshed: ttl_expire_as_cancel)."""
    if price is None:
        return bool(resting)                                    # want nothing: anything there must go
    if len(resting) != 1:
        return True                                             # missing, or duplicates
    o = resting[0]
    if abs(o.price - price) > 1e-9:
        tol = cfg.reprice_tolerance_ticks if tol_ticks is None else tol_ticks
        close = abs(o.price - price) <= tol * TICK + 1e-9
        safe = limit is not None and (o.price <= limit + 1e-9 if is_bid else o.price >= limit - 1e-9)
        if not (close and safe):
            return True                                         # wrong price
    if not (size * cfg.keep_fraction <= o.qty <= (max_size if max_size is not None else size)):
        return True                                             # mostly filled, or bigger than we now want
    if not expire_as_cancel and o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry:
        return True                                             # about to expire
    return False


def chase_only(cfg, fv, placed_fv, inv, placed_inv, qty, placed_qty):
    """No-chase (no_chase_enabled): True if nothing of OURS changed since this order was placed - fair value within
    no_chase_fv_epsilon, the same inventory, not (partly) filled - so a re-price would only follow a rival's move.
    Unknown placement notes -> False (normal rules). Shared by Bot.plan_change and the simulators' plan_changes."""
    if not cfg.no_chase_enabled or None in (fv, placed_fv, inv, placed_inv, qty, placed_qty):
        return False
    return (abs(fv - placed_fv) <= cfg.no_chase_fv_epsilon + 1e-12 and abs(inv - placed_inv) < 1e-9
            and qty >= placed_qty - 1e-9)


def no_chase_needs_change(resting, price, size, cfg, now, limit, is_bid, max_size, fv, placed_fv, inv, placed_inv,
                          placed_qty, expire_as_cancel=False):
    """side_needs_change with the no-chase rule: when chase_only holds for the single resting order, judge it at
    no_chase_tolerance_ticks instead of reprice_tolerance_ticks (every other test - limit price, size, expiry -
    unchanged). The one decision both Bot.plan_change and strategy_sim.plan_changes use."""
    fix = side_needs_change(resting, price, size, cfg, now, limit, is_bid, max_size, expire_as_cancel=expire_as_cancel)
    if (fix and price is not None and len(resting) == 1
            and chase_only(cfg, fv, placed_fv, inv, placed_inv, resting[0].qty, placed_qty)):
        fix = side_needs_change(resting, price, size, cfg, now, limit, is_bid, max_size,
                                tol_ticks=max(cfg.no_chase_tolerance_ticks, cfg.reprice_tolerance_ticks),
                                expire_as_cancel=expire_as_cancel)
    return fix


def ttl_tier(headline, size, bank, cfg):
    """TTL saver tier of a market: "headline" (headline_races), "busy" (planned quote size >= ttl_busy_size_frac of
    the account) or "quiet"."""
    if headline:
        return "headline"
    return "busy" if size >= cfg.ttl_busy_size_frac * bank - 1e-9 else "quiet"


def order_ttl_for(cfg, tier, u):
    """Seconds a new level-0 order lives. u: a uniform [0, 1) draw (the jitter). Without ttl_tiers_enabled, exactly
    order_ttl. With it: the tier's TTL x (1 +- ttl_jitter_frac), kept within [2 x refresh_before_expiry,
    MAX_ORDER_TTL] (never "about to expire" at birth; the dead-man's switch stays bounded)."""
    if not cfg.ttl_tiers_enabled:
        return cfg.order_ttl
    base = {"headline": cfg.order_ttl, "busy": cfg.order_ttl_busy}.get(tier, cfg.order_ttl_quiet)
    ttl = base * (1 + cfg.ttl_jitter_frac * (2 * u - 1))
    return max(2 * cfg.refresh_before_expiry, min(MAX_ORDER_TTL, ttl))


LADDER_TIER = 2       # change_key tier of ladder work: after pulls (0), urgent reprices (0.5) and level-0 changes (1)


def ladder_lock(o):
    """Cash a resting order (Resting) locks at most: a bid buys YES at its price, an ask buys NO at 1 - price."""
    return o.qty * (o.price if o.is_bid else 1 - o.price)


def ladder_levels(anchor, q, best_bid, best_ask, offsets, mults, quote_size, cap, max_cash=None):
    """R3 resting depth ladder around `anchor` (fair value when the ladder was last placed).
    q          level 0 (the touch Quote): a side gets a ladder only where level 0 quotes it
    offsets    level i (1..n) rests at anchor - offsets[i-1] (bids) / + (asks), on the 0.5c grid away from fair value
    mults      ...for mults[i-1] x quote_size shares
    cap        {is_bid: f(price) -> the most shares that side may have in orders in total (level 0 + ladder)}: the
               position limits (Kelly at that price, headline, race-netted, party, reduce-only), already net of the
               position. Levels fill it in order (cumulative clip); a level that would get < 1 share is left out.
    max_cash   most cash one order may lock (bid: price a share, ask: 1 - price), like max_order_cash_frac
    Never at or inside level 0, never at or through the best OTHER order on the other side (that would trade).
    Returns (want, allowed): want {(is_bid, level): (price, size)}; allowed {(is_bid, level): most shares a resting
    order at that level may still hold (the clip alone)} for every level considered."""
    want, allowed = {}, {}
    for is_bid in (True, False):
        l0, l0_size = (q.bid, q.bid_size) if is_bid else (q.ask, q.ask_size)
        if l0 is None or l0_size <= 0:
            continue
        used = l0_size
        for lvl, (off, mult) in enumerate(zip(offsets, mults), start=1):
            px = floor_tick(anchor - off) if is_bid else ceil_tick(anchor + off)
            if (px >= l0 - 1e-9) if is_bid else (px <= l0 + 1e-9):
                continue                                        # at or inside level 0
            if is_bid and best_ask is not None and px >= best_ask - 1e-9:
                continue                                        # would trade with another order
            if not is_bid and best_bid is not None and px <= best_bid + 1e-9:
                continue
            if px <= PMIN + 1e-9 or px >= PMAX - 1e-9:
                continue                                        # nothing left to catch at the edge of the grid
            room = max(0, int(cap[is_bid](px) - used))
            if max_cash is not None:                            # per-order cash cap (level 3 = 3 x the quote)
                room = min(room, int(max_cash / (px if is_bid else 1 - px)))
            allowed[(is_bid, lvl)] = room
            size = min(int(mult * quote_size), room)
            if size >= 1:
                want[(is_bid, lvl)] = (px, size)
                used += size
    return want, allowed

# =============================================================================================
# MEASUREMENT - fills.csv and the `report` command
# =============================================================================================

class FillLogger:
    """Appends every new fill to a CSV, tagged with which of our quotes it hit and the fair value
    at the moment we placed that quote, so edge and adverse selection can be measured later."""
    COLUMNS = ["fill_id", "filled_at", "exchange_id", "order_id", "our_side", "qty",
               "fill_price", "quote_price", "fv_at_quote", "fv_after", "level"]   # level: 0 = touch, 1.. = R3 ladder

    def __init__(self, path):
        self.path, self.seen = path, set()
        if os.path.exists(path):
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if reader.fieldnames == self.COLUMNS[:-1]:     # before the ladder's level column: add it (blank)
                with open(path + ".tmp", "w", newline="") as f:
                    w = csv.DictWriter(f, self.COLUMNS)
                    w.writeheader()
                    w.writerows({**r, "level": ""} for r in rows)
                os.replace(path + ".tmp", path)
                reader.fieldnames = self.COLUMNS
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
                            meta.get("fv", ""), fvs.get(str(f.get("exchangeId")), ""),
                            meta.get("level", 0) if meta else ""])
                self.seen.add(str(f["id"]))
                log.info("FILL ex %s  our %s x%.0f @ %s  (fv when quoted %s)%s", f.get("exchangeId"),
                         meta.get("our_side", "?"), qty, meta.get("price", f.get("price")), meta.get("fv", "?"),
                         f" ladder L{meta['level']}" if meta.get("level") else "")


def read_fills(path):
    """All rows of fills.csv as dicts ([] if there's no file yet)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class TurnoverTracker:
    """Shares traded per market over a trailing window (turnover control, see Config). Two sources per market:
    our own fills and the realtime trade tape (every trader's trades, ours included), each a deque of
    (unix time, shares). Observed flow = the larger of the two (the tape includes our fills, so adding them would
    count ours twice; our fills are the floor when the tape has gaps), per hour of OBSERVED time in the window.

    Observed time = the part of the window covered by this run (since `start`) or by the seeds read at start-up.
    Seeds give time points (fills.csv fills; the recorder's trades and snapshot times, one a minute while it ran);
    points no more than GAP_SECONDS apart (and the last one to the start) join into observed intervals, and a longer
    gap - an outage, a restart after one - is NOT observed (it is not zero flow). Until the observed time reaches
    MIN_COVERAGE of the window nothing is judged (every market is alive): no false "dead" in the first hours after
    a start without history, nor after a restart that followed an outage."""
    KEEP_HOURS = 48.0                     # retention (the largest turnover_window_hours allowed live)
    MIN_COVERAGE = 0.9                    # share of the window that must be observed before judging
    GAP_SECONDS = 600.0                   # seed points further apart than this: the time between is unobserved

    def __init__(self, start=None):
        self.start = time.time() if start is None else float(start)
        self.seed_points = []             # unix times before `start` known to be observed
        self.seed_spans = []              # [(t0, t1)] observed intervals built from them (see _rebuild)
        self.ours, self.tape = defaultdict(deque), defaultdict(deque)

    def _cover(self, points):
        """Add observed time points (before start) and rebuild the observed intervals."""
        self.seed_points.extend(float(t) for t in points if t is not None and float(t) <= self.start)
        pts = sorted(set(self.seed_points))
        spans = []
        for t in pts + ([self.start] if pts else []):
            if spans and t - spans[-1][1] <= self.GAP_SECONDS:
                spans[-1][1] = t
            else:
                spans.append([t, t])
        self.seed_spans = [(a, b) for a, b in spans if b > a]

    def add(self, eid, t, qty, tape=False):
        """One trade of |qty| shares at unix time t (ours, or from the tape)."""
        q = abs(float(qty or 0))
        if q > 0:
            (self.tape if tape else self.ours)[str(eid)].append((float(t), q))

    def prune(self, now):
        cut = now - 3600 * self.KEEP_HOURS
        for book in (self.ours, self.tape):
            for dq in book.values():
                while dq and dq[0][0] < cut:
                    dq.popleft()

    def observed_hours(self, now, window_hours):
        """Hours of the window [now - window, now] covered by this run or by the seeds' intervals."""
        lo = now - 3600 * window_hours
        run = max(0.0, now - max(lo, self.start))
        seed = sum(max(0.0, min(b, self.start, now) - max(a, lo)) for a, b in self.seed_spans)
        return min(window_hours, (run + seed) / 3600)

    def judged(self, now, window_hours):
        return self.observed_hours(now, window_hours) >= self.MIN_COVERAGE * window_hours - 1e-9

    @staticmethod
    def _sum(dq, lo, now):
        return sum(q for t, q in dq if lo <= t <= now)

    def shares(self, eid, now, window_hours, use_tape=True):
        """Shares traded in the window: max(our fills, tape)."""
        lo = now - 3600 * window_hours
        ours = self._sum(self.ours.get(str(eid), ()), lo, now)
        return max(ours, self._sum(self.tape.get(str(eid), ()), lo, now)) if use_tape else ours

    def per_hour(self, eid, now, window_hours, use_tape=True):
        """Observed flow in shares per hour, None while too little of the window has been observed."""
        if not self.judged(now, window_hours):
            return None
        return self.shares(eid, now, window_hours, use_tape) / max(self.observed_hours(now, window_hours), 1e-9)

    def seed_fills(self, rows, now):
        """fills.csv rows (read_fills): those within KEEP_HOURS feed `ours` and count as observed time points.
        Returns how many rows were used."""
        cut, pts = now - 3600 * self.KEEP_HOURS, []
        for r in rows:
            try:
                t = parse_ts(r.get("filled_at"))
                q = float(r.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if t is not None and cut <= t.timestamp() <= self.start:
                pts.append((t.timestamp(), str(r.get("exchange_id")), q))
        pts.sort()
        for t, eid, q in pts:
            self.add(eid, t, q)
        self._cover(t for t, _, _ in pts)
        return len(pts)

    @staticmethod
    def _first_rowid_at(db, table, ts_min, to_ts):
        """Smallest rowid whose ts >= ts_min (rowid grows with time): a binary search, a few indexed reads."""
        top = db.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0]
        if top is None:
            return None
        lo, hi = 1, top + 1
        while lo < hi:
            mid = (lo + hi) // 2
            row = db.execute(f"SELECT ts FROM {table} WHERE rowid >= ? ORDER BY rowid LIMIT 1", (mid,)).fetchone()
            t = to_ts(row[0]) if row else None
            if row is None or (t is not None and t >= ts_min):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def seed_tape(self, db_path, now):
        """The recorder (read-only): its trades within KEEP_HOURS feed `tape`; their times and the snapshot times
        (one a minute while the bot ran) are observed time points. Returns trades used (0 = nothing to read)."""
        if not db_path or not os.path.exists(db_path):
            return 0
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return 0
        cut, pts, rows = now - 3600 * self.KEEP_HOURS, [], []

        def snap_ts(v):
            t = parse_ts(v) if isinstance(v, str) else None
            return t.timestamp() if t is not None else None
        try:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "trades" in names:
                first = self._first_rowid_at(db, "trades", cut, lambda v: float(v) if v is not None else None)
                if first is not None:
                    rows = db.execute("SELECT ts, eid, quantity FROM trades WHERE rowid >= ? AND ts <= ? "
                                      "ORDER BY ts", (first, self.start)).fetchall()
            if "snapshots" in names:
                first = self._first_rowid_at(db, "snapshots", cut, snap_ts)
                if first is not None:
                    pts = [snap_ts(r[0]) for r in db.execute("SELECT DISTINCT ts FROM snapshots WHERE rowid >= ?",
                                                             (first,))]
        except (sqlite3.Error, TypeError, ValueError):
            return 0
        finally:
            db.close()
        used = 0
        for t, eid, q in rows:
            if t is not None and q is not None and cut <= float(t):
                self.add(eid, t, q, tape=True)
                used += 1
        self._cover([float(t) for t, _, _ in rows if t is not None and float(t) >= cut]
                    + [t for t in pts if t is not None and t >= cut])
        return used


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


def ops_summary_line(ops, account=None, tilt_s=None, tilt_exposure=None):
    """The phone summary's ops line (Package 5), e.g. "Liquidation 101.1k (account 101.5k), realised +154, 76% of
    capital toward Polymarket, 69% older than 6 h | tilt 5.1%, exposure +15.1k (-151 per point)". The tilt part only
    when tilt_s is known (a fraction: 0.051 = 5.1%); per point = -exposure x 0.01. Unknown pieces show "?";
    None when there is nothing to show."""
    ops = ops or {}
    if account is None and all(ops.get(k) is None for k in ("liquidation_value", "realised_pnl",
                                                             "toward_ref_capital_frac", "capital_over_6h_frac")):
        line = None
    else:
        k = lambda v: f"{v / 1000:.1f}k" if v is not None else "?"
        pct = lambda v: f"{100 * v:.0f}%" if v is not None else "?"
        real = ops.get("realised_pnl")
        line = (f"Liquidation {k(ops.get('liquidation_value'))} (account {k(account)}), realised "
                + (f"{real:+,.0f}" if real is not None else "?")
                + f", {pct(ops.get('toward_ref_capital_frac'))} of capital toward Polymarket, "
                f"{pct(ops.get('capital_over_6h_frac'))} older than 6 h")
    if tilt_s is not None:
        tilt = f"tilt {100 * tilt_s:.1f}%" + (f", exposure {tilt_exposure / 1000:+.1f}k ({-0.01 * tilt_exposure:+,.0f} "
                                               f"per point)" if tilt_exposure is not None else "")
        line = f"{line} | {tilt}" if line else tilt
    return line


def build_summary(api, fills_path, initial_balance, value=None, value_prev=None, arbs=None, health=None, takes=None,
                  hours=24, status=None, ops_line=None):
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
    lines = ([status] if status else []) + [account] + ([ops_line] if ops_line else []) + [f"{rank} | {smart}",
             f"Last {hours:g}h: {day['fills']} fills, {day['shares']:,.0f} shares, edge {day['edge_c']:+.2f}c, "
             f"markout {day['markout_c']:+.2f}c" + (f", {arbs} arbitrages" if arbs is not None else "")
             + (f", {takes} takes" if takes is not None else "")]
    if health:
        lines.append(f"Now: {health.get('orders_resting', '?')} orders resting, worst-case loss "
                     f"{health.get('worst_case_loss', 0):,.0f}, realtime {health.get('realtime', '?')}, "
                     f"rate limits {health.get('rate_limited_total', 0)}")
        if health.get("portfolio_age_hours") is not None:
            frac = health.get("capital_in_positions_frac")
            lines.append(f"Positions: avg age {health['portfolio_age_hours']:.1f}h ({health.get('positions_over_3h', 0)} "
                         f"> 3h, {health.get('positions_over_12h', 0)} > 12h), capital in positions "
                         + (f"{100 * frac:.0f}%" if frac is not None else "?")
                         + (" - CEILING: adding sides cut" if health.get("capital_ceiling_active") else ""))
        if health.get("turnover_dead_markets") is not None:
            lines.append(f"dead-turnover markets: {health['turnover_dead_markets']} holding "
                         f"{health.get('turnover_dead_capital', 0) / 1000:.1f}k")
        if health.get("mark_frag_estimates"):
            top = next(iter((health.get("mark_frag_top") or {}).items()), None)
            lines.append(f"Mark noise: {health['mark_frag_total_cash']:,.0f} $ per 10 min, "
                         f"{health.get('mark_frag_capped_markets', 0)} positions at the cap"
                         + (f", biggest {top[0]} {top[1]:,.0f}" if top else "")
                         + (" - TOTAL CAP: adding sides withdrawn" if health.get("mark_frag_total_cap_active") else ""))
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

MARKOUT_MINUTES = (1, 5, 30)


def analyze(fills_path, db_path, hours=None, top=15):
    """The `analyze` command: from fills.csv and market_data.sqlite only (no API requests). Lines of text:
      - per market: fills, shares, edge at the quote (c/share: how far inside fair value we traded), markout
        after 1/5/30 min (fair value then vs our price, c/share: negative = picked off), P&L marked at the
        latest fair value;
      - per market from the snapshots: share of the time our bid/ask was the best price ("at top"), and how
        often a quote that was at the top was beaten by the next snapshot ("undercut", per hour quoted).
    hours: only the last N hours."""
    out = []
    since = (utcnow() - timedelta(hours=hours)) if hours else None
    fvs = defaultdict(list)                                  # eid -> [(time, fv)] from the snapshots
    tops = defaultdict(list)                                 # eid -> [(time, bid at top, ask at top, quoted)]
    labels = {}
    if db_path and os.path.exists(db_path):
        db = sqlite3.connect(db_path)
        try:
            for ts, eid, label, bb, ba, fv, ob, oa in db.execute(
                    "SELECT ts, eid, label, best_bid, best_ask, fair_value, our_bid, our_ask FROM snapshots ORDER BY ts"):
                t = parse_ts(ts)
                if t is None or (since and t < since):
                    continue
                labels[eid] = label
                if fv is not None:
                    fvs[eid].append((t, fv))
                tops[eid].append((t, ob is not None and bb is not None and abs(ob - bb) < 1e-9,
                                  oa is not None and ba is not None and abs(oa - ba) < 1e-9, ob is not None or oa is not None))
        except sqlite3.Error as e:
            out.append(f"(snapshots unreadable: {e})")
        finally:
            db.close()

    def fv_at(eid, t):
        series = fvs.get(eid) or []
        for tt, v in series:                                 # first snapshot at or after t
            if tt >= t:
                return v
        return None

    per = defaultdict(lambda: {"fills": 0, "shares": 0.0, "edge": 0.0, "edge_n": 0.0, "pnl": 0.0,
                               **{f"m{m}": 0.0 for m in MARKOUT_MINUTES}, **{f"m{m}_n": 0.0 for m in MARKOUT_MINUTES}})
    unmatched = 0
    by_level = defaultdict(lambda: {"fills": 0, "shares": 0.0, "edge": 0.0, "edge_n": 0.0})
    for r in read_fills(fills_path):
        t = parse_ts(r.get("filled_at"))
        if since and (t is None or t < since):
            continue
        eid, side = str(r.get("exchange_id")), r.get("our_side")
        try:
            qty = float(r.get("qty") or 0)
            price = float(r.get("quote_price") or r.get("fill_price") or 0)
        except ValueError:
            continue
        if side not in ("bid", "ask"):
            unmatched += 1
            continue
        sign = 1 if side == "bid" else -1                    # +1 = we bought YES
        lv = by_level[int(r["level"]) if str(r.get("level") or "").isdigit() else 0]   # R3 ladder level (0 = touch)
        lv["fills"] += 1
        lv["shares"] += qty
        if r.get("fv_at_quote") not in ("", None):
            lv["edge"] += sign * (float(r["fv_at_quote"]) - price) * qty
            lv["edge_n"] += qty
        m = per[eid]
        m["fills"] += 1
        m["shares"] += qty
        if r.get("fv_at_quote") not in ("", None):
            m["edge"] += sign * (float(r["fv_at_quote"]) - price) * qty
            m["edge_n"] += qty
        for mins in MARKOUT_MINUTES:
            v = fv_at(eid, t + timedelta(minutes=mins)) if t else None
            if v is not None:
                m[f"m{mins}"] += sign * (v - price) * qty
                m[f"m{mins}_n"] += qty
        last = fvs[eid][-1][1] if fvs.get(eid) else None
        if last is not None:
            m["pnl"] += sign * (last - price) * qty
    c = lambda m, k: f"{100 * m[k] / m[k + '_n']:+6.2f}" if m.get(k + "_n") else "    - "
    out.append(f"fills: {sum(m['fills'] for m in per.values())} matched, {unmatched} not matched to a bot quote"
               + (f" (last {hours:g} h)" if hours else ""))
    tot = {k: sum(m[k] for m in per.values()) for k in ("shares", "edge", "edge_n", "pnl",
                                                        *[f"m{x}" for x in MARKOUT_MINUTES], *[f"m{x}_n" for x in MARKOUT_MINUTES])}
    out.append(f"all markets: {tot['shares']:.0f} shares, edge {c(tot, 'edge')}c, markout "
               + " / ".join(f"{x}m {c(tot, f'm{x}')}c" for x in MARKOUT_MINUTES) + f", P&L at latest fair value {tot['pnl']:+.0f}")
    if any(k > 0 for k in by_level):                         # the R3 ladder filled: touch vs each ladder level
        out.append("by level: " + " | ".join(f"L{k} {v['fills']} fills {v['shares']:.0f} sh edge {c(v, 'edge').strip()}c"
                                             for k, v in sorted(by_level.items())))
    out.append(f"{'market':26} {'fills':>5} {'shares':>8} {'edge c':>7} " + " ".join(f"{f'mk{x}m':>7}" for x in MARKOUT_MINUTES)
               + f" {'P&L':>8} {'top bid':>7} {'top ask':>7} {'undercut/h':>10}")
    rows = []
    for eid in set(per) | set(tops):
        m, snaps = per[eid], tops.get(eid, [])
        quoted = [x for x in snaps if x[3]]
        top_b = sum(x[1] for x in quoted) / len(quoted) if quoted else None
        top_a = sum(x[2] for x in quoted) / len(quoted) if quoted else None
        under = sum(1 for p, q in zip(snaps, snaps[1:]) if (p[1] and q[3] and not q[1]) or (p[2] and q[3] and not q[2]))
        span_h = (quoted[-1][0] - quoted[0][0]).total_seconds() / 3600 if len(quoted) > 1 else 0
        rows.append((m["pnl"], eid, m, top_b, top_a, under / span_h if span_h else None))
    for pnl, eid, m, tb, ta, uh in sorted(rows, key=lambda r: r[0])[:top] + (
            sorted(rows, key=lambda r: r[0])[-top:] if len(rows) > 2 * top else sorted(rows, key=lambda r: r[0])[top:]):
        pct = lambda v: f"{100 * v:6.0f}%" if v is not None else "     - "
        out.append(f"{labels.get(eid, eid)[:26]:26} {m['fills']:5d} {m['shares']:8.0f} {c(m, 'edge'):>7} "
                   + " ".join(f"{c(m, f'm{x}'):>7}" for x in MARKOUT_MINUTES)
                   + f" {m['pnl']:+8.0f} {pct(tb)} {pct(ta)} {uh if uh is None else round(uh, 1)!s:>10}")
    out.extend(rival_floor_lines(db_path, since, top))
    return out


def rival_floor_lines(db_path, since=None, top=15):
    """`analyze`, last table: the other traders' half-spread around the fair value and how often the best price
    changes (60-s intervals), per market, from the recorder's books table, via rival_floor.py (next to mm_bot.py
    or in analysis/). The widest `top` markets plus the spread of the recommended min_edge."""
    if not (db_path and os.path.exists(db_path)):
        return []
    src = next((x for x in (os.path.join(HERE, "rival_floor.py"), os.path.join(HERE, "analysis", "rival_floor.py"))
                if os.path.exists(x)), None)
    if src is None:
        return ["(rival floor: copy analysis/rival_floor.py next to mm_bot.py for this table)"]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rival_floor", src)
        rf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rf)
        db = sqlite3.connect(db_path)
        try:
            if not rf.has_rows(db, "books"):
                return []
            st = rf.stats_from_books(db, since.timestamp() if since else None, min_samples=10)
        finally:
            db.close()
    except Exception as e:                    # an analysis aid: never let it break `analyze`
        return [f"(rival floor unavailable: {e})"]
    dist = defaultdict(int)
    for v in st.values():
        dist[v["min_edge"]] += 1
    out = [f"rival floor (books, {len(st)} markets): recommended min_edge "
           + ", ".join(f"{100 * k:g}c x{n}" for k, n in sorted(dist.items())),
           f"{'market':26} {'half c':>6} {'top chg':>7} {'n':>6} {'edge c':>6}"]
    for eid, v in sorted(st.items(), key=lambda kv: -kv[1]["rival_half_spread_c"])[:top]:
        rate = v["top_change_rate"]
        out.append(f"{v['label'][:26]:26} {v['rival_half_spread_c']:6.2f} {'-' if rate is None else f'{rate:.2f}':>7} "
                   f"{v['samples']:6d} {100 * v['min_edge']:6g}")
    return out

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
    unconfirmed_hold: float = -1.0        # the pending_until set for orders whose batch outcome was unknown
    writes: int = 0                       # our writes (cancels / batches) touching it still in flight
    reprices: dict = field(default_factory=dict)   # side -> times we repriced it lately (churn control)
    ref_moved_at: float = -1e9            # last time its Polymarket price moved >= urgent_ref_move
    ref_jump_at: float = -1e9             # last time its Polymarket price moved >= ref_jump_threshold (monotonic;
                                          #   reduce_from_book pauses after it)
    cancelling: bool = False              # ...one of them is a cancel
    inv: float = 0.0                      # for logging / recording
    eff: float = 0.0                      # for logging
    age: float = 0.0                      # share-weighted age of the position, hours (age skew; logging)
    ref: float | None = None              # outside reference price, if any (for logging / recording)
    quote: Quote = NO_QUOTE               # what we decided this cycle (for recording)
    take_dir: int = 0                     # +1 = Polymarket above the best ask, -1 = below the best bid, 0 = neither
    take_since: float = 0.0               # since when every Polymarket reading has shown that same gap
    take_until: float = 0.0               # after taking here, leave it alone until this time
    fl_side: str | None = None            # favourite-longshot bias side last cycle (for its hysteresis)
    fl_tag: str = ""                      # that bias for the quote log line ("" = none active)
    turnover_dead: bool = False           # turnover control: holding a position in a market with too little flow
    turnover_tag: str = ""                # " dead" on the quote log line while turnover control changes the quote
    bb_tag: str = ""                      # " bb" on the quote log line while behind-the-best sizing shrinks a side
    ro_clip: str = ""                     # "bid" / "ask" / "bid ask": side(s) decide() left empty ONLY by the reduce-only
                                          #   race-net clip this cycle (pair_passive_in_reduce_only)
    lad_fv: float | None = None           # R3 ladder: fair value the ladder is anchored at (None = not anchored)
    lad_ref: float | None = None          # ...Polymarket's price at that moment (ladder_pull_jump compares with it)
    lad_pull_until: float = 0.0           # ...ladder pulled until then after a Polymarket jump
    lad_tag: str = ""                     # " L3" on the quote log line while 3 ladder orders rest
    lad_ctx: tuple = (1.0, 1.0, None)     # ...decide's (adding_factor, adding_limit_factor, frag_limit) this cycle


def busy(ex, now_m):
    """Don't place on this exchange: an earlier placement's outcome is unknown, or a write is still running."""
    return now_m < ex.pending_until or ex.writes > 0


class Change:
    """What one exchange needs this cycle: orders to cancel first (all of them with whole=True), then new ones.
    key orders the work: pulls first, then party-control markets, then the biggest quotes."""
    __slots__ = ("ex", "doomed", "whole", "new", "key", "count", "unsafe", "ladder")

    def __init__(self, ex, doomed, whole, new, key, count=True, unsafe=False, ladder=()):
        self.ex, self.doomed, self.whole, self.new, self.key = ex, doomed, whole, new, key
        self.count = count            # its cancels count as reprices (churn control) once sent
        self.unsafe = unsafe          # removes an order that must not stay (urgent_writes_per_cycle sends these first)
        self.ladder = set(ladder)     # ids of ladder orders among doomed (never counted as level-0 reprices)

    def reprice_sides(self):
        """The sides this change cancels resting orders on (what churn control counts as a reprice)."""
        if not self.count:
            return []
        return [side for side, is_bid in (("bid", True), ("ask", False))
                if any(o.is_bid == is_bid and o.order_id not in self.ladder for o in self.doomed)]


class Write:
    """One order write running on a writer thread: kind "cancel" (eid, orders, whole) or "batch" (chunk)."""
    __slots__ = ("kind", "eids", "payload", "future", "sent", "change", "payload_ok", "done_at")

    def __init__(self, kind, eids, payload, sent, change=None):
        self.kind, self.eids, self.payload, self.sent, self.change = kind, eids, payload, sent, change
        self.future, self.payload_ok, self.done_at = None, False, None   # payload_ok: a cancel confirmed


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
        self.lots = self.load_lots()      # eid -> [[signed shares, epoch time bought], ...] oldest first (age skew)
        self.lots_seeded = False          # first reconcile rebuilds missing ones from fills.csv
        self.lots_dirty = False
        self.capital_over = False         # capital ceiling active (capital_in_positions_max_frac)
        self.adding_resume = False        # Package 8 adding_factor_capital_on: the resume factor is in force
        self.cur_refs, self.cur_liquid = {}, set()   # this cycle's Polymarket prices (for risk_fv)
        _tilt_saved = self.load_tilt()
        self.tilt = TiltEstimator(cfg).from_dict(_tilt_saved)   # T2.1: tournament tilt s (see update_tilt)
        self.tilt_s, self.tilt_exposure = self.tilt.s, 0.0
        # T2.1 ramp-in: monotonic time ref_tilt_enabled was (last) seen switched on, None while off; the s actually
        # applied = tilt_s x rampin_factor. Persisted as wall-clock times in status.json tilt_state (on_wall,
        # headline_on_wall): a restart within TILT_RAMP_RESUME_SECONDS of the last status write continues the ramp
        # where it was (restore_rampin); after a longer outage (or with no saved time) the ramp restarts from 0,
        # the safe choice (fair value never jumps by the full tilt after a long outage).
        self.tilt_on_at, self.tilt_s_applied = None, 0.0
        self.tilt_headline_on_at = None   # the headline gate's own ramp-in clock (ref_tilt_headline switched on later)
        self.tilt_s_applied_headline = 0.0   # the s applied in headline markets (status.json)
        self.restore_rampin(_tilt_saved)
        self.pos_marks = {}               # {eid: the exchange's own valuation price of the position (currentPrice)}
        self.fv_fallback_logged = {}      # {eid: source} - which fallback risk_fv used for a held position (logged once)
        self.mark_sd = {}                 # eid -> sd of the 10-min mid change (mark_frag_*; from the recorder)
        self.mark_sd_time = -1e9          # monotonic time of the last estimate (refreshed every MARK_SD_REFRESH_SECONDS)
        self.mark_frag_over = False       # mark-fragility total cap active (mark_frag_total_max_cash)
        self.notes_dirty = False
        self.exit_code = 0                # what the process exits with (see EXIT_* codes)
        self.health = {}                  # latest cycle summary, written to status.json
        self.last_cycle_done = None       # monotonic time the last cycle ended (watchdog, status.json)
        self.last_cycle_phases, self.pause_logged = {}, False
        self.watchdog_alerted, self.watchdog_thread = False, None
        self.kill_breaches = 0
        self.reserved_mode = None if cfg.reserved_cash_mode == "auto" else cfg.reserved_cash_mode
        self.calib_prev = None            # (account value, locked cash, positions) from the previous cycle
        self.calib_votes = []             # recent "add"/"ignore" verdicts while auto-detecting
        self.failed_cycles = 0
        self.pulled_after_errors = False
        self.pos_fallbacks = 0            # cycles in a row on the last positions read (409, see cycle)
        self.pos_fallback_since = None    # when that run of 409s started (monotonic)
        self.pos_fallback_failing = False # ...and it went on too long: cycles fail until positions read again
        self.error_alerted = False        # one phone alert per outage, plus one when it ends
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
        self.unconfirmed = {}             # eid -> [(order, meta, time sent)]: sent, outcome unknown (see adopt_unconfirmed)
        self.placed_qty = {}              # orderId -> shares we placed, and...
        self.filled_qty = defaultdict(float)   # ...shares filled so far (each fill counted once, from fills)
        self.last_expiry = {}             # ttl_expire_as_cancel: (eid, is_bid) -> expiry of the level-0 order seen there
        self.ref_rejected = set()         # Polymarket keys currently ignored as implausible (alerted once)
        self.ref_only = set()             # eids priced from Polymarket alone this cycle (thin book, R5)
        self.lad_cash_left = 0.0          # R3 ladder: cash the ladder may still lock this cycle (see ladder_setup)
        self.lad_keep = {}                # ...this exchange's levels a resting ladder order may keep (ladder_targets)
        self.lad_placed = {}              # ...ladder orderId -> monotonic time placed (churn control)
        self.lad_liquid, self.lad_party_delta = set(), 0.0   # ...this cycle's liquid Polymarket eids, party delta
        # Threads for sending several HTTP requests at once (downloads mostly wait on the network).
        self.pool = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_requests), thread_name_prefix="http")
        # ...and for order writes, so a slow one never holds up the others or the cycle (see send_changes).
        self.writer = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_writes), thread_name_prefix="write")
        self.writes = []                  # Write jobs in flight; only the main thread reads or changes this list
        self.write_done = threading.Event()   # set when one finishes (wakes the main loop to apply it)
        self.write_log = deque()          # (time done, seconds taken, timed out) of recent writes (burst detection)
        self.last_cycle_seconds = 0.0
        self.burst, self.burst_calm_since, self.burst_set = False, 0.0, set()
        self.trading_since = None         # monotonic time the trading loop started (burst_startup_grace_seconds)
        self.global_reduce = False
        self.backstop_adding_factor = 1.0     # Package 6 candidate: backstop soft band (cycle step 6 sets it)
        self.ref_moved = set()            # eids whose Polymarket price moved >= urgent_ref_move at the latest reading
        self.unloads = {}                 # eid -> {"until", "side", "left"}: fast unload windows (note_unloads)
        self.ref_version_urgent = 0
        self.refs = self.load_reference_prices()
        self.ref_version_seen = 0         # last Polymarket reading the jump guard has looked at
        self.last_tops = {}               # latest bulk best bid/ask per exchange (for recording)
        self.other_tops = {}              # eid -> (other traders' best bid, best ask, time read) from bulk prices
        self.ref_tops = {}                # eid -> (best bid, best ask): R5 priced it from other_tops this cycle
        self.trading_since = None         # time.monotonic() when the trading loop started (startup priming)
        self.book_reqs = deque()          # times of recent book downloads (startup priming's per-minute cap)
        self.held = {}                    # eid -> signed shares held (latest positions read; books_to_fetch, priming)
        self.unpriced_held = {}           # eid -> cycles in a row a held market had no fair value
        self.unpriced_warned = set()      # ...those already warned about (once per unpriced spell)
        self.arb_cooldown = {}            # race -> time.monotonic() until which we leave it alone
        self.arbs_total = 0               # arbitrages / takes since start (summaries report the change)
        self.unwinds_total = 0            # pair unwinds since start (status.json pair_unwinds_total)
        self.pp = {}                      # T2.5 passive pair unwind: race -> slice state (pair_passive_step)
        self.pair_owed = {}               # Package 7 pair_unwind_followup: race -> owed legs (pair_followup_step)
        self.pp_sets_total = 0            # ...complete sets closed by it since start (status.json)
        self.hold_takes = deque()         # C hold target: (now_m, notional) of takes in the last hour (take_aged)
        self.hold_open_at = None          # ...now_m from which takes may start (first enabled call + 1 h: restart-safe)
        self.hold_takes_total = 0         # ...takes sent since start
        self.ops_last = {}                # ops fields of the latest status write (ops_fields): recorder, summary
        self.ops_cache = {}               # ops_fields: fills.csv-derived numbers, recomputed when the file changes
        self.ops_warned = False           # ops_fields failed once (logged once)
        self.takes_total = 0
        self.takes_skipped_budget = self.arbs_skipped_budget = 0   # not sent: write budget busy (status.json)
        self.take_version_seen = 0        # last Polymarket reading the take logic has counted
        self.take_tilted_seen = bool(getattr(cfg, "take_tilted_ref", False))   # X12: a toggle resets take_since
        self.db = self.open_recorder()
        self.last_record = -1e9
        self.book_tops, self.book_rows = {}, []    # recorder: last top levels logged per eid, rows not yet written
        self.last_pos_record, self.last_pos_full = -1e9, -1e9   # recorder: positions table (record_positions)
        self.pos_seen = {}                # eid -> (values last written, wall time last read)
        self.pos_record_due = False       # a fill was seen: record the next positions read
        self.last_pnl_reply = (None, None)  # recorder: (latest P&L reply, wall time read)
        self.refills = defaultdict(deque)  # (eid, "bid"/"ask") -> (time, shares) of recent fills that added
        self.refill_until = {}            # (eid, side) -> monotonic time its refill cooldown ends
        self.turnover = TurnoverTracker() # turnover control: shares traded per market (ours + the tape)
        self.turnover_flow = {}           # eid -> observed shares/h (None = not judged yet), see refresh_turnover
        self.turnover_refreshed = -1e9    # monotonic time of the last refresh
        self.turnover_state = {}          # eid -> (dead?, monotonic time it became so): hysteresis (refresh_turnover)
        self.seed_turnover()
        self.last_summary_slot = None     # (date, hour) of the last phone summary
        self.value_at_last_summary = None
        self.counts_at_last_summary = {"arbs": 0, "takes": 0, "errors": 0, "rate_limits": 0}
        self.errors_total = 0             # failed cycles since start (summaries report new ones)
        self.phase = "starting"           # what the bot is doing, for the status line
        self.selftest_passed = False
        self.cycle_started, self.cycle_alerted = None, False
        self.last_analysis_day = None
        self.handover = False             # SIGUSR1: stop without cancelling (see request_handover)
        self.handover_timer = None        # ...and its exit deadline (see handover_deadline)
        self.last_progress_write, self.last_slow_alert = -1e9, -1e9
        self.defaults = {k: getattr(cfg, k) for k in OVERRIDABLE}   # what a removed override goes back to
        self.overrides, self.overrides_mtime, self.last_overrides_check = {}, None, -1e9
        self.market_edge, self.market_edge_mtime, self.last_market_edge_check = {}, None, -1e9   # rival-floor map
        self.selftest_future = None       # the self-test running in the background (see selftest_tick)
        self.selftest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selftest")
        self.selftest_eid = None
        self.selftest_next = 0.0          # when to try again after the exchange answered busy
        self.selftest_started = None
        self.selftest_alerted = False
        self.selftest_funds_wait = 0.0    # current funds-refusal back-off (s); 0 = not waiting for cash
        self.selftest_hold = 0.0          # the test exchange's pending_until before the test
        # Package 6 reduce_no_as_sell: its start-up self-test leg (None = not run yet, "ok", "off" = refused: today's
        # behaviour for this run), the background run, when to retry it, and this plan's covered NO per exchange/level
        self.nosell_state, self.nosell_future, self.nosell_next, self.cover_planned = None, None, 0.0, {}
        self.nosell_eid, self.nosell_hold = None, 0.0
        # Package 7 pair_no_unwind_max_cost: its start-up check leg (a paired covered NO sale), state as nosell's
        self.pairno_state, self.pairno_future, self.pairno_next, self.pairno_wait = None, None, 0.0, 0.0
        self.pairno_race, self.pairno_holds, self.pp_short_skip_logged = None, {}, False
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
        self.cycle_started, self.cycle_alerted = now_m, False
        self.cycle_phases, self.phase_t = {}, now_m
        left = self.api.pause_left() if hasattr(self.api, "pause_left") else 0.0
        if cfg.pause_skip_cycles and left > 0:
            # The exchange said wait (429): every read would only sleep out the pause inside the cycle, the loop
            # blocked for minutes. Apply finished writes and come back after it.
            self.harvest_writes()
            if not self.pause_logged:
                self.pause_logged = True
                log.warning("exchange rate-limit pause: %.0f s left - cycles skipped until it ends", left)
            self.cycle_started = None             # (last_cycle_done is NOT touched: a chain of pauses must still
            self.wake.wait(timeout=min(left, 1.0))    #  reach the watchdog's alert and exit: that is its purpose)
            return
        self.pause_logged = False
        tl = getattr(self.api, "tl", None)
        if tl is not None:                        # main-thread writes never wait long for the write budget
            tl.max_write_wait = cfg.write_wait_seconds + cfg.main_write_wait_margin
        try:
            self.cycle_body(cfg, now, now_m)
        finally:
            if tl is not None:
                tl.max_write_wait = None
            self.phase_mark("other")
            self.last_cycle_seconds = time.monotonic() - now_m
            self.last_cycle_phases = {k: round(v, 1) for k, v in self.cycle_phases.items()}
            self.health["last_cycle_seconds"] = round(self.last_cycle_seconds, 1)
            self.health["last_cycle_phases"] = self.last_cycle_phases
            self.cycle_started = None
            self.last_cycle_done = time.monotonic()

    def phase_mark(self, name):
        """Cycle timing: the time since the previous mark is added to phase `name` (summary line, status.json)."""
        t = time.monotonic()
        phases = getattr(self, "cycle_phases", None)
        if phases is not None:
            phases[name] = phases.get(name, 0.0) + t - getattr(self, "phase_t", t)
        self.phase_t = t

    def phases_text(self):
        """The last cycle's length and where it went, for the summary line: '31.2 s (reads 2.1, ...)'."""
        ph = getattr(self, "last_cycle_phases", None) or {}
        parts = ", ".join(f"{k} {v:.1f}" for k, v in ph.items() if v >= 0.05)
        return f"{self.last_cycle_seconds:.1f} s" + (f" ({parts})" if parts else "")

    def progress(self, phase):
        """Inside a cycle: if it's running long, keep status.json fresh (it's otherwise written only after a
        cycle, so a 4-minute cycle looked like a dead bot) and alert once when it passes slow_cycle_alert_seconds."""
        if self.cycle_started is None:
            return
        now_m = time.monotonic()
        running = now_m - self.cycle_started
        if running < 10 or now_m - self.last_progress_write < 5:
            return
        self.last_progress_write = now_m
        self.health["cycle_running_seconds"], self.health["cycle_phase"] = round(running), phase
        self.write_status(ok=True)
        if running >= self.cfg.slow_cycle_alert_seconds and not self.cycle_alerted \
                and now_m - self.last_slow_alert >= 900:
            self.cycle_alerted, self.last_slow_alert = True, now_m
            alert(f"slow cycle: {running:.0f} s so far ({phase}) - the exchange may be slow")

    def cycle_body(self, cfg, now, now_m):

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
        self.update_burst(now_m)

        # 1. Account state: independent reads, sent at the same time ------------------------------
        f_pos = self.pool.submit(self.api.positions) if read_positions else None
        f_orders = self.pool.submit(self.api.open_orders, self.tid) if read_orders else None
        f_pnl = self.pool.submit(self.api.pnl) if slow_poll else None
        pos_failed = False
        if read_positions:                        # if a read fails, the cycle fails...
            try:
                self.cached_pos = f_pos.result()
            except ApiError as e:
                # ...except day one's 409 "holdings cannot be valued" (a market without a valuation price):
                # positions only change through fills, so the last good read is fine for a cycle or two.
                if e.status != 409 or self.cached_pos is None:
                    raise
                self.positions_fallback(e, now_m)     # raises once the last read is too old to trade on
                pos_failed = True
            else:
                if self.pos_fallbacks:
                    log.warning("positions readable again after %d cycles (%.0f s) on an old read",
                                self.pos_fallbacks, now_m - self.pos_fallback_since)
                self.pos_fallbacks, self.pos_fallback_since, self.pos_fallback_failing = 0, None, False
        if read_orders:
            self.cached_orders = f_orders.result()
            self.orders_stale = False
        if pos_failed:
            self.orders_stale = True              # read positions (and orders) again next cycle
        pos, raw_orders = self.cached_pos, self.cached_orders
        # position.quantity is already signed by the API: + YES shares, - NO shares.
        self.pos_marks = {str(p["exchangeId"]): float(next(p[k] for k in Bot.POS_PRICE_KEYS if p.get(k) is not None))
                          for p in pos.get("positions", []) if not p.get("settled")
                          and any(p.get(k) is not None for k in Bot.POS_PRICE_KEYS)} if isinstance(pos, dict) else {}
        inv = {str(p["exchangeId"]): float(p.get("quantity") or 0)
               for p in pos.get("positions", []) if not p.get("settled")}
        self.held = {e: q for e, q in inv.items() if q and e in self.ex}
        reserved = reserved_cash(raw_orders)
        self.update_lots(inv, time.time())
        if self.db and cfg.record_positions and read_positions and not pos_failed:
            self.record_positions(pos, f_pnl, now_m, fill_event)
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

        self.log_priming()
        self.phase_mark("reads")
        self.progress("books")

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
        self.cur_refs, self.cur_liquid = refs, liquid
        self.mark_ref_moves()
        self.reference_jump_guard(now_m)
        fvs = dict(book_fvs)
        self.ref_only = self.thin_book_prices(fvs, refs, liquid, now_m) if cfg.ref_only_enabled else set()
        self.warn_unpriced_held(fvs, book_fvs, refs, liquid, now_m)
        self.update_tilt(book_fvs, refs, liquid, inv, now_m)      # T2.1: runs (read-only) with the flag off too
        if cfg.ref_tilt_enabled:                                  # T2.1 ramp-in clock (persisted: see __init__)
            if self.tilt_on_at is None:
                self.tilt_on_at = now_m
        else:
            self.tilt_on_at = None                                # re-enabling restarts the ramp
        if cfg.ref_tilt_enabled and cfg.ref_tilt_headline:        # the headline legs ramp from THEIR switch-on
            if self.tilt_headline_on_at is None:
                self.tilt_headline_on_at = now_m
        else:
            self.tilt_headline_on_at = None
        self.tilt_s_applied = (self.tilt_s * rampin_factor(now_m, self.tilt_on_at, cfg.ref_tilt_rampin_min)
                               if cfg.ref_tilt_enabled else 0.0)
        hl_on = [t for t in (self.tilt_on_at, self.tilt_headline_on_at) if t is not None]
        self.tilt_s_applied_headline = (                          # headline legs: ramped from the later clock
            self.tilt_s * rampin_factor(now_m, max(hl_on) if hl_on else None, cfg.ref_tilt_rampin_min)
            if cfg.ref_tilt_enabled and cfg.ref_tilt_headline else 0.0)
        if cfg.ref_weight > 0 and refs:
            for eid, r in refs.items():
                if fvs.get(eid) is not None and eid in liquid and eid not in self.ref_only:   # liquid only
                    ex = self.ex[eid]
                    fvs[eid] = blend_fv(fvs[eid], self.tilted_ref_for(ex, r, now_m), cfg)   # tilt applied once, there
            for members in self.groups.values():
                if len(members) > 1:
                    fvs.update(normalise({e: fvs[e] for e in members}))

        # 3b. How many shares to quote in each market (every size_plan_seconds, or on an account step) ----
        if cfg.size_by_activity:
            self.update_size_plan(now_m, fvs)

        self.phase_mark("fair_values")
        # 4. Fills (every slow_poll_seconds, and straight after a fill) --------------------------------
        if read_fills:
            new_fills = self.log_fills(fvs)
            self.note_refills(new_fills, inv, now_m)
            self.note_unloads(new_fills, inv, now_m)
        self.note_turnover(new_fills if read_fills else ())
        self.refresh_turnover(now_m)

        # 5. Guaranteed arbitrage inside races (takes liquidity; our quotes there are pulled first) --
        # Package 7 pair_unwind_followup: legs an earlier pair unwind left unequal are evened up first
        owed_races = self.pair_followup_step(fvs, now_m, mine_real) if getattr(self, "pair_owed", None) else set()
        arb_races = self.take_arbitrage(inv, fvs, mine_real, now_m) if self.running else set()
        arb_races |= owed_races
        if cfg.pair_unwind_passive or self.pp:    # T2.5: a passive slice filled -> the other leg is taken now
            arb_races |= self.pair_passive_step(inv, fvs, now_m, skip=arb_races, mine_real=mine_real)

        # 6. Portfolio-level risk ------------------------------------------------------------------
        eff = self.effective_inventory(inv)
        worst = self.total_worst_case(inv, fvs)
        party_delta = sum(PARTY_SIGN.get(ex.party, 0) * inv.get(eid, 0.0) for eid, ex in self.ex.items())
        # Hysteresis: once in reduce-only, both caps are reduce_only_hysteresis lower until it has been left.
        hyst = min(cfg.reduce_only_hysteresis, cfg.max_worst_case_frac / 2) if self.global_reduce else 0.0
        if cfg.risk_model == "correlated":
            # never above the sum of maxima, which is a hard bound (for a few big positions 3 sd exceeds it)
            risk = min(worst, self.settlement_risk(inv, fvs, party_delta))
            global_reduce = bool(equity) and (risk > (cfg.max_worst_case_frac - hyst) * equity
                                              or worst > (cfg.worst_case_backstop_frac - hyst) * equity)
        else:
            risk = worst
            global_reduce = bool(equity) and worst > (cfg.max_worst_case_frac - hyst) * equity
        if global_reduce != self.global_reduce:
            log.warning("%s reduce-only: risk %.0f, worst case %.0f, account %s", "ENTERING" if global_reduce
                        else "leaving", risk, worst, f"{equity:.0f}" if equity is not None else "?")
        self.global_reduce = global_reduce
        self.backstop_adding_factor = backstop_soft_factor(worst, equity, cfg)   # Package 6 candidate: soft band
        capital = self.capital_in_positions(pos, inv, fvs)
        cap_frac = capital / equity if equity else None
        self.update_capital_ceiling(cap_frac, cfg)
        self.update_adding_resume(cap_frac, cfg)
        self.refresh_mark_sd(now_m)
        frag = self.update_mark_frag(inv, cfg)
        ages = self.portfolio_age(time.time())
        if full:                                  # summary line on full checks only (event cycles can be every 2 s)
            log.info("%s | account %s (locked in orders %.0f, %s) | worst-case loss %.0f (risk %.0f)%s | party delta %+.0f | "
                     "priced %d/%d | resting %d | last cycle %s",
                     "realtime" if realtime else ("polling (realtime connecting)" if self.feed else "polling"),
                     f"{equity:.0f}" if equity is not None else "?", reserved,
                     {"add": "added back", "ignore": "already included"}.get(self.reserved_mode, "detecting"), worst, risk,
                     " -> REDUCE-ONLY" if global_reduce else "", party_delta,
                     sum(v is not None for v in fvs.values()), len(fvs), sum(len(v) for v in resting.values()),
                     self.phases_text())
        self.health = {"account_value": equity, "locked_in_orders": round(reserved, 2),
                       "reserved_cash_mode": self.reserved_mode or "detecting", "worst_case_loss": round(worst, 2),
                       "reduce_only": global_reduce, "party_delta": party_delta,
                       "settlement_risk": round(risk, 2), "risk_model": cfg.risk_model,
                       "markets_priced": sum(v is not None for v in fvs.values()), "markets_tracked": len(fvs),
                       "orders_resting": sum(len(v) for v in resting.values()),
                       "reference_prices": len(refs), "reference_prices_liquid": len(liquid),
                       "polymarket_fetch_seconds": getattr(self.refs, "fetch_seconds", None),
                       "burst_mode": self.burst,
                       "arbitrages_total": self.arbs_total,
                       "pair_unwinds_total": self.unwinds_total,
                       "rate_limited_total": getattr(self.api, "rate_limited", 0),     # should stay 0
                       "request_budget_per_min": round(getattr(self.api, "budget", 0)),
                       "write_budget_per_min": round(getattr(self.api, "wbudget", 0)),
                       "requests_last_min": round(getattr(self.api, "budget", 0)) - getattr(self.api, "budget_left", lambda: 0)(),
                       "realtime": "connected" if realtime else ("reconnecting" if self.feed else "off"),
                       "realtime_events": self.feed.events if self.feed else 0,
                       "takes_total": self.takes_total,
                       "takes_skipped_budget": self.takes_skipped_budget,
                       "arbs_skipped_budget": self.arbs_skipped_budget,
                       "write_budget_wait_total": getattr(self.api, "write_budget_wait_total", 0),
                       "quote_capital_planned": round(getattr(self, "plan_capital", 0.0)),
                       "biggest_quotes": {self.ex[e].label: s for e, s in sorted(self.size_plan.items(),
                                          key=lambda kv: -kv[1])[:6] if e in self.ex},
                       "capital_in_positions": round(capital),
                       "capital_in_positions_frac": round(cap_frac, 3) if cap_frac is not None else None,
                       "capital_ceiling_active": self.capital_over,
                       "capital_ceiling_adding_factor": self.ceiling_adding_factor(cfg),
                       "capital_ceiling_adding_resume": self.adding_resume,
                       "portfolio_age_hours": round(ages[0], 2), "positions_over_3h": ages[1],
                       "positions_over_12h": ages[2],
                       **frag,
                       "positions": {self.ex[e].label: q for e, q in inv.items() if q and e in self.ex}}

        self.health.update(books_loaded=self.books_loaded(), markets_priced_from_tops=len(self.ref_tops))

        # 6b. Take tournament quotes that Polymarket says are clearly stale (confirmed over 2 readings) ---
        taken = (self.take_stale_quotes(refs, liquid, fvs, inv, mine_real, global_reduce, party_delta, now_m)
                 if self.running else set())
        if cfg.hold_target_hours <= 0:                     # C off (or switched off live): turning it back on
            self.hold_open_at = None                        #   restarts the 1-hour hold-off
        if cfg.hold_target_hours > 0 and self.running:      # C hold target: aged lots taken within the hourly budget
            taken |= self.take_aged(inv, book_fvs, mine_real, now_m)

        self.phase_mark("fills_risk_takes")
        # 7. Decide + reconcile each exchange. One write at a time (parallel_writes = 1): cancels happen now,
        #    new orders are batched after. Otherwise every change is planned first, then sent in parallel.
        new_orders, changes = [], []
        self.ladder_setup(equity, capital, raw_orders, liquid, party_delta, resting)
        for eid, ex in list(self.ex.items()):
            if not self.running:          # Ctrl+C: stop touching the book immediately
                return
            if self.api.live and (ex.group in arb_races or eid in taken):
                continue                  # just traded here: positions changed, re-quote next cycle
            try:
                ex.quote = self.decide(ex, fvs.get(eid), inv, eff, global_reduce, party_delta, now_m,
                                       refs.get(eid), book_fvs.get(eid), eid in liquid)
                if self.pp:                       # T2.5: the passive pair-unwind slice replaces this leg's ask
                    ex.quote = self.pair_passive_quote(ex, ex.quote)
                if cfg.parallel_writes > 1:
                    ch = self.plan_exchange(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
                    if ch:
                        changes.append(ch)
                else:
                    new_orders += self.reconcile(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
            except ApiError as e:         # one exchange failing must not stop the others
                log.error("exchange %s (%s): %s", eid, ex.label, e)
        self.phase_mark("decide")
        self.progress("sending orders")
        if self.running:
            if cfg.parallel_writes > 1:
                self.send_changes(changes)
            else:
                self.place(new_orders, now_m)
                self.phase_mark("send")
        # Orders resting NOW, after this cycle's changes (the health snapshot above was taken before them).
        self.health["orders_resting"] = len(self.my_orders) if self.api.live else len(self.sim)
        self.health["selftest_state"] = self.selftest_state()
        self.health["fast_unload_windows"] = sum(1 for e in list(self.unloads)
                                                 if e in self.ex and self.unload_side(self.ex[e], now_m))
        self.health["fl_bias_markets"] = {k: sum(1 for x in self.ex.values() if x.fl_tag.startswith(f" fl:{k}"))
                                          for k in ("bid", "ask")}
        self.health["market_edge_markets"] = len(self.market_edge) if self.cfg.market_edge_enabled else 0
        self.health["behind_best_markets"] = sum(1 for x in self.ex.values() if x.bb_tag)
        self.health.update(self.turnover_health(inv, fvs))
        lad = self.ladder_orders()
        if cfg.ladder_enabled or lad:                 # (absent while the ladder is off and nothing of it rests)
            self.health["ladder_orders"] = len(lad)
            self.health["ladder_cash"] = round(sum(ladder_lock(o) for o in lad))

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

    def positions_fallback(self, e, now_m):
        """Positions answered 409: reuse the last read, but only for positions_stale_max_cycles cycles in a row or
        positions_stale_max_seconds, whichever first. Fills keep landing meanwhile, so inventory, position limits,
        skew, reduce-only and the risk model would all run on a frozen inventory; past the limit this re-raises,
        the cycle fails and on_cycle_error pulls every quote (after max_failed_cycles) until positions read again.
        Logged when the run of 409s starts and when it ends (not every cycle)."""
        cfg = self.cfg
        if not self.pos_fallbacks:
            self.pos_fallback_since = now_m
            log.warning("positions unavailable (%s) - using the last read (at most %d cycles / %.0f s)",
                        e, cfg.positions_stale_max_cycles, cfg.positions_stale_max_seconds)
        self.pos_fallbacks += 1
        age = now_m - self.pos_fallback_since
        if ((cfg.positions_stale_max_cycles and self.pos_fallbacks > cfg.positions_stale_max_cycles)
                or (cfg.positions_stale_max_seconds and age >= cfg.positions_stale_max_seconds)):
            if not self.pos_fallback_failing:
                self.pos_fallback_failing = True
                log.error("positions still unavailable after %d cycles (%.0f s) - failing cycles (quotes are "
                          "pulled) until they read again", self.pos_fallbacks - 1, age)
            raise e

    def thin_book_prices(self, fvs, refs, liquid, now_m):
        """R5: markets whose book is too thin for a depth-checked price (fair_value None) but that have a
        liquid Polymarket price get fv = Polymarket, if the tournament's raw best bid/ask (other traders,
        any size, verified recently) are two-sided, not wider than max_spread_for_fv, and their mid is
        within ref_only_max_gap of Polymarket. Fills fvs in place; returns the set of those eids.
        The best bid/ask come from the downloaded book, or (ref_only_use_tops) from a fresh bulk reading when
        there is no current book; those eids are also in self.ref_tops, for decide (see r5_top)."""
        cfg, out = self.cfg, set()
        self.ref_tops = {}
        for eid, ex in self.ex.items():
            if fvs.get(eid) is not None or eid not in liquid or eid not in refs:
                continue
            top = self.r5_top(ex, now_m)
            if top is None:
                continue
            bb, ba, from_tops = top
            if ba <= bb or ba - bb > cfg.max_spread_for_fv or abs((bb + ba) / 2 - refs[eid]) > cfg.ref_only_max_gap:
                continue
            fvs[eid] = refs[eid]
            out.add(eid)
            if from_tops:
                self.ref_tops[eid] = (bb, ba)
        return out

    def warn_unpriced_held(self, fvs, book_fvs, refs, liquid, now_m):
        """A market we hold a position in with no fair value for unpriced_held_warn_cycles cycles in a row gets
        ONE warning with the reason (it is then neither quoted nor reduced); again only after it was priced."""
        n = self.cfg.unpriced_held_warn_cycles
        for eid in list(self.unpriced_held):
            if eid not in self.held or fvs.get(eid) is not None:
                self.unpriced_held.pop(eid, None)
                self.unpriced_warned.discard(eid)
        if n <= 0:
            return
        for eid in self.held:
            if fvs.get(eid) is not None:
                continue
            k = self.unpriced_held[eid] = self.unpriced_held.get(eid, 0) + 1
            if k >= n and eid not in self.unpriced_warned:
                self.unpriced_warned.add(eid)
                ex = self.ex[eid]
                log.warning("UNPRICED held market %s (inv %+.0f) for %d cycles: %s", ex.label, self.held[eid], k,
                            self.unpriced_reason(ex, book_fvs, refs, liquid, now_m))

    def unpriced_reason(self, ex, book_fvs, refs, liquid, now_m):
        """Why a market has no fair value (for warn_unpriced_held)."""
        cfg, eid = self.cfg, ex.eid
        has_book = ex.book is not None and now_m - ex.verified < cfg.book_stale
        if has_book and fair_value(ex.book, cfg) is not None and book_fvs.get(eid) is None:
            return "a race leg has no price (normalise needs all legs)"
        if eid not in refs:
            return ("no book downloaded" if ex.book is None else "book stale" if not has_book
                    else "book too thin/wide") + ", no Polymarket price (or rejected by ref_max_plausible_gap)"
        if eid not in liquid:
            return "book thin/missing and Polymarket not liquid"
        if not cfg.ref_only_enabled:
            return "book thin/missing and ref_only_enabled off"
        top = self.r5_top(ex, now_m)
        if top is None:
            if has_book:
                return "book one-sided (other traders)"
            t = self.other_tops.get(eid)
            if not cfg.ref_only_use_tops:
                return "no book downloaded and ref_only_use_tops off"
            if t is None or now_m - t[2] > cfg.tops_max_age:
                return "no book downloaded, no fresh bulk tops"
            side = 0 if t[0] is None else 1
            raw = self.last_tops.get(eid, (None, None))[side]
            return "no book downloaded, bulk tops one-sided (%s)" % (
                ("bid", "ask")[side] + (" blanked by our own order at the best" if raw is not None else " empty"))
        bb, ba, _ = top
        if ba <= bb or ba - bb > cfg.max_spread_for_fv:
            return f"spread {bb:.3f}/{ba:.3f} wider than max_spread_for_fv"
        return f"mid {(bb + ba) / 2:.3f} more than ref_only_max_gap (Polymarket {refs[eid]:.3f})"

    def r5_top(self, ex, now_m):
        """(other traders' best bid, best ask, from_tops) for R5, or None (one-sided, or nothing current).
        A book confirmed within book_stale is used as before. Without one (after a restart every book is None;
        downloading 237 takes minutes of request budget) and with ref_only_use_tops: the bulk best bid/ask, if
        read within tops_max_age and two-sided once our own orders are discounted (see others_top)."""
        cfg = self.cfg
        if ex.book is not None and now_m - ex.verified < cfg.book_stale:
            b = ex.book
            if not b.get("bids") or not b.get("asks"):
                return None
            return b["bids"][0]["price"], b["asks"][0]["price"], False
        if cfg.ref_only_use_tops:
            t = self.other_tops.get(ex.eid)
            if t and t[0] is not None and t[1] is not None and now_m - t[2] <= cfg.tops_max_age:
                return t[0], t[1], True
        return None

    def note_other_tops(self, tops, mine_real, now_m):
        """Remember the bulk best bid/ask as OTHER traders' best prices (see others_top), with the time read.
        Once our own quote is the best price, a reading can't see the others behind it: that side keeps the
        others' price seen before (if not better than ours), as long as that one was still within tops_max_age.
        The fresh reading confirms nobody is better than us, so the entry's time moves on; a stale carried side
        can only be off for pennying (others are behind us: nothing to cross), and the downloaded book, which
        books_to_fetch fetches first for these markets, takes over soon anyway.
        """
        for eid, top in tops.items():
            bid, ask = others_top(top, mine_real.get(eid, []))
            prev = self.other_tops.get(eid)
            t = now_m
            if prev is not None and now_m - prev[2] <= self.cfg.tops_max_age:
                if bid is None and top[0] is not None and prev[0] is not None and prev[0] <= top[0] + 1e-9:
                    bid, t = prev[0], prev[2]             # carried: keeps the time it was really seen, so a
                if ask is None and top[1] is not None and prev[1] is not None and prev[1] >= top[1] - 1e-9:
                    ask, t = prev[1], prev[2]             #   side we can't see expires after tops_max_age
            self.other_tops[eid] = (bid, ask, t)

    def mark_ref_moves(self):
        """Which markets' Polymarket price just moved (urgent: see urgent_ref_move). Cleared once handled."""
        version = getattr(self.refs, "version", 0)
        if not self.refs or version == self.ref_version_urgent:
            self.ref_moved = set()
            return
        self.ref_version_urgent = version
        moved = {k for k, m in getattr(self.refs, "last_moves", {}).items() if m >= self.cfg.urgent_ref_move}
        self.ref_moved = {e for e, ex in self.ex.items() if f"{ex.group}|{ex.party}" in moved}
        for e in self.ref_moved:
            self.ex[e].ref_moved_at = time.monotonic()

    def tilted_ref_for(self, ex, r, now_m=None):
        """The Polymarket price r as this market's quotes see it: with ref_tilt_enabled (and, in a headline market,
        ref_tilt_headline) the tilt-corrected r' = tilted_ref(r, s, legs) with s = self.tilt_s x the ramp-in factor
        (rampin_factor since the flag went on; now_m None = time.monotonic()) faded by carry_ramp near the close;
        otherwise (or s 0) r unchanged. Shared by the blend and (take_tilted_ref) the takes."""
        cfg = self.cfg
        if r is None or not cfg.ref_tilt_enabled or (ex.group in cfg.headline_races and not cfg.ref_tilt_headline):
            return r
        now_m = time.monotonic() if now_m is None else now_m
        on_at = self.tilt_on_at
        if ex.group in cfg.headline_races and self.tilt_headline_on_at is not None:
            on_at = max(on_at or -1e18, self.tilt_headline_on_at)   # headline legs: the later of the two switch-ons
        s = self.tilt_s * rampin_factor(now_m, on_at, getattr(cfg, "ref_tilt_rampin_min", 0.0))
        s = carry_ramp(s, self.hours_to_close(ex), getattr(cfg, "ref_tilt_carry_days", 0.0))
        return tilted_ref(r, s, self.legs(ex)) if s else r

    def take_ref(self, ex, r, now_m=None):
        """X12: the Polymarket price the stale-quote takes compare with the book and size from: tilted_ref_for with
        take_tilted_ref (ramp-in included), the raw r otherwise."""
        return self.tilted_ref_for(ex, r, now_m) if getattr(self.cfg, "take_tilted_ref", False) else r

    def legs(self, ex):
        """Number of markets in this market's race (1 for a lone market)."""
        return max(1, len(self.groups.get(ex.group) or ()))

    def load_tilt(self):
        """T2.1: the tilt estimate the previous run left in status.json ({} if none: the estimate starts at 0).
        The dict also carries "saved_wall": tilt_state's own save time, else the file's mtime (restore_rampin)."""
        try:
            path = bot_path(self.cfg.status_file)
            with open(path) as f:
                st = json.load(f)
            d = st.get("tilt_state") or ({"s": st["tilt_s"], "ready": True} if "tilt_s" in st else {})
            if d and not isinstance(d.get("saved_wall"), (int, float)):
                d = dict(d, saved_wall=os.path.getmtime(path))
            return d
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return {}

    def restore_rampin(self, d, now_m=None, now_w=None):
        """T2.1 ramp-in across a restart: status.json saved less than TILT_RAMP_RESUME_SECONDS ago with a switch-on
        wall time (on_wall / headline_on_wall) -> tilt_on_at / tilt_headline_on_at = now_m - (now_w - on_wall): the
        ramp continues where it was. Otherwise None (the first cycle with the flag on starts the ramp from 0). The
        cycle still clears a clock whose flag is off now."""
        now_m = time.monotonic() if now_m is None else now_m
        now_w = time.time() if now_w is None else now_w
        d = d if isinstance(d, dict) else {}
        saved = d.get("saved_wall")
        fresh = (isinstance(saved, (int, float)) and not isinstance(saved, bool)
                 and 0 <= now_w - saved < TILT_RAMP_RESUME_SECONDS)

        def back(w):
            if not fresh or not isinstance(w, (int, float)) or isinstance(w, bool) or w > now_w:
                return None
            return now_m - (now_w - w)
        self.tilt_on_at = back(d.get("on_wall"))
        self.tilt_headline_on_at = back(d.get("headline_on_wall"))

    def tilt_state_dict(self):
        """status.json tilt_state: the estimate plus the ramp-in switch-on times as wall-clock seconds (None = off)
        and its own save time (saved_wall), so a quick restart resumes the ramp (restore_rampin)."""
        now_m, now_w = time.monotonic(), time.time()

        def wall(on_at):
            return None if on_at is None else round(now_w - (now_m - on_at), 3)
        return {**self.tilt.to_dict(), "on_wall": wall(self.tilt_on_at),
                "headline_on_wall": wall(self.tilt_headline_on_at), "saved_wall": round(now_w, 3)}

    def update_tilt(self, book_fvs, refs, liquid, inv, now_m):
        """T2.1, every cycle and whatever ref_tilt_enabled says: feed the tilt estimator from the markets that are
        liquid, not R5 (ref_only), not headline, with a book price and Polymarket, and not under a jump guard; and
        tilt_exposure = sum over held markets of position x (raw Polymarket - c), c = 1/legs."""
        cfg, samples = self.cfg, []
        for eid, r in refs.items():
            ex = self.ex.get(eid)
            if (ex is None or r is None or book_fvs.get(eid) is None or eid not in liquid or eid in self.ref_only
                    or ex.group in cfg.headline_races or now_m < ex.cooldown_until):
                continue
            samples.append((r, book_fvs[eid], self.legs(ex)))
        self.tilt_s = self.tilt.update(samples, now_m)
        exposure = 0.0
        for eid, q in (inv or {}).items():
            ex, r = self.ex.get(eid), refs.get(eid)
            if q and ex is not None and r is not None:
                exposure += q * (r - tilted_ref(r, 1.0, self.legs(ex)))    # tilted_ref(r, 1, legs) = c
        self.tilt_exposure = exposure

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
                ex.ref_jump_at = now_m
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
        self.note_other_tops(tops, mine_real, now_m)

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
            self.note_other_tops(res, mine_real, now_m)
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
        # Never-downloaded books of markets we HOLD a position in come before everything (even feed-reported
        # changes): without a book a held market can go unpriced (R5 can't see the side our own reducing order
        # tops, see others_top), and then it is neither quoted nor reduced. Biggest exposure first (held_weights).
        # Then never-downloaded books where we have orders resting (e.g. adopted at a handover restart), then
        # the ones we quote from the bulk tops, then the rest, busiest (Polymarket volume) first.
        live = {o.eid for o in self.my_orders.values()}
        held = self.held_weights()
        vols = {}
        if self.refs and hasattr(self.refs, "volumes"):
            try:
                vols = self.refs.volumes() or {}
            except Exception:             # ordering only: never let it stop the books
                vols = {}
        for eid, ex in self.ex.items():
            if ex.book is not None:
                continue
            if eid in held:
                candidates.append((-1, -held[eid], eid))
            else:
                v = float(vols.get(f"{ex.group}|{ex.party}") or 0.0)
                candidates.append((1, -2.0 if eid in live else -1.0 if eid in self.ref_tops
                                   else -0.5 * v / (v + 1e5), eid))     # -0.5..0: busiest first
        candidates += extra
        todo = []
        for _, _, eid in sorted(candidates):
            if eid not in todo:
                todo.append(eid)
        cap, reserve = self.cfg.max_books_per_cycle, self.cfg.budget_reserve
        if self.priming():                # after a (re)start: books first (see startup_books_first)
            cap = min(max(cap, min(self.cfg.startup_prime_books, self.prime_need())), self.prime_allowance())
            reserve = min(reserve, self.cfg.startup_prime_reserve)
        spare = getattr(self.api, "budget_left", lambda: 10 ** 6)() - reserve
        return todo[:max(0, min(cap, spare))]

    def held_weights(self):
        """{eid: shares held x the price of the shares held} (p for YES, 1 - p for NO; p = last fair value,
        else Polymarket, else 0.5) for every market we hold a position in: the priming order (books_to_fetch)."""
        out = {}
        for eid, q in self.held.items():
            ex = self.ex.get(eid)
            if ex is None or not q:
                continue
            p = next((x for x in (ex.last_fv, ex.ref) if x is not None), 0.5)
            out[eid] = abs(q) * (p if q > 0 else 1 - p)
        return out

    def held_missing(self):
        """Markets we hold a position in whose book has never been downloaded, biggest exposure first."""
        w = self.held_weights()
        return sorted((e for e in w if self.ex[e].book is None), key=lambda e: -w[e])

    def books_loaded(self):
        return sum(ex.book is not None for ex in self.ex.values())

    def books_last_minute(self):
        now = time.monotonic()
        while self.book_reqs and now - self.book_reqs[0] >= 60.0:
            self.book_reqs.popleft()
        return len(self.book_reqs)

    def prime_allowance(self):
        """Book downloads still allowed in the current 60 s while priming (startup_prime_books_per_min)."""
        return max(0, self.cfg.startup_prime_books_per_min - self.books_last_minute())

    def write_reserve(self):
        """Requests order writes should leave free in the shared budget: write_read_reserve, plus while priming
        what the book downloads may still use this minute (capped by the books still missing). Without it the
        writes, which keep only 3 free, take every slot the book downloads (which keep 20 free) would need.
        For send_changes' `spare` (Engineer 1's region): budget_left() - self.write_reserve()."""
        r = self.cfg.write_read_reserve
        if self.priming():
            r += min(self.prime_allowance(), self.prime_need())
        return r

    def priming(self):
        """Startup priming is on: within startup_prime_seconds of the trading loop starting (fresh start or
        handover restart alike), and more than startup_prime_missing_frac of the books not downloaded yet.
        It never ends while a market we hold a position in has no downloaded book (held_missing), up to
        startup_prime_held_max_seconds after the start (a hard cap: a book that keeps failing can't hold it on)."""
        cfg = self.cfg
        if not cfg.startup_books_first or self.trading_since is None or not self.ex:
            return False
        elapsed = time.monotonic() - self.trading_since
        if elapsed <= cfg.startup_prime_held_max_seconds and self.held_missing():
            return True
        return self.prime_full()

    def prime_full(self):
        """The ordinary priming condition (startup_prime_seconds, startup_prime_missing_frac), without the
        held-market extension. Assumes startup_books_first and a trading start (see priming)."""
        cfg = self.cfg
        if self.trading_since is None or time.monotonic() - self.trading_since > cfg.startup_prime_seconds:
            return False
        return len(self.ex) - self.books_loaded() > cfg.startup_prime_missing_frac * len(self.ex)

    def prime_need(self):
        """Books priming still wants: every missing one, or once only the held-market extension keeps it on,
        just the held markets' (so the extension doesn't take the order writes' budget for other books)."""
        return len(self.ex) - self.books_loaded() if self.prime_full() else len(self.held_missing())

    def log_priming(self):
        """One line per cycle while priming the books, and one when it's over."""
        if self.priming():
            self.primed_logged = False
            missing = self.held_missing()
            if missing and time.monotonic() - self.trading_since > self.cfg.startup_prime_seconds:
                log.info("priming books: %d of %d loaded - extended (max %.0f s) for %d held market(s) without a "
                         "book: %s", self.books_loaded(), len(self.ex), self.cfg.startup_prime_held_max_seconds,
                         len(missing), ", ".join(self.ex[e].label for e in missing[:8]))
            else:
                log.info("priming books: %d of %d loaded", self.books_loaded(), len(self.ex))
        elif self.trading_since is not None and not getattr(self, "primed_logged", True):
            self.primed_logged = True
            log.info("priming books done: %d of %d loaded after %.0f s", self.books_loaded(), len(self.ex),
                     time.monotonic() - self.trading_since)

    def download_books(self, eids, mine_real):
        """Download books (in parallel), remove our own orders from them, and cache them on the Ex."""
        self.book_reqs.extend([time.monotonic()] * len(eids))     # for startup priming's per-minute cap
        for eid, book in self.in_parallel(lambda e: self.api.book(e, self.tid), eids).items():
            if isinstance(book, Exception):   # keep the old copy; it stops being used after book_stale
                log.warning("book %s failed: %s", eid, book)
            else:
                self.ex[eid].book = strip_own(book, mine_real.get(eid, []))
                self.ex[eid].book_time = self.ex[eid].verified = time.monotonic()
                self.pending_dirty.discard(eid)
                self.note_book(eid, self.ex[eid].book)

    def note_book(self, eid, book):
        """Recorder: queue a row of other traders' top levels if they changed since the last row for eid."""
        if not self.db or not self.cfg.record_books:
            return
        n = self.cfg.record_book_levels
        top = tuple(tuple((rnd(l["price"]), round(float(l["quantity"]), 2)) for l in (book.get(k) or [])[:n])
                    for k in ("bids", "asks"))
        if self.book_tops.get(eid) == top:
            return
        self.book_tops[eid] = top
        self.book_rows.append((round(time.time(), 3), eid, json.dumps(top[0]), json.dumps(top[1])))

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

    # ------------------------------------------------------------------ position age (age skew)
    def load_lots(self):
        """Position lots saved by a previous run: {eid: [[signed shares, epoch time], ...]} ({} if none)."""
        if not self.cfg.position_lots_file:
            return {}
        try:
            with open(bot_path(self.cfg.position_lots_file)) as f:
                raw = json.load(f)
            return {str(e): [[float(q), float(t)] for q, t in v] for e, v in raw.items() if v}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def seed_lots(self, inv, now):
        """Lots for positions we have no record of, rebuilt from fills.csv: under FIFO what is still held is
        the LATEST fills in the position's direction, so walk back from the newest until they add up to it.
        Shares not covered (no fills logged) count as bought now."""
        need = {e: q for e, q in inv.items() if round(q) and e not in self.lots}
        if not need:
            return
        found = defaultdict(list)                       # eid -> [(shares, time)] newest first
        try:
            rows = read_fills(bot_path(self.cfg.fills_csv))
        except (OSError, ValueError, csv.Error):
            rows = []
        for r in reversed(rows):
            e, side = str(r.get("exchange_id")), r.get("our_side")
            if e not in need or side not in ("bid", "ask") or (side == "bid") != (need[e] > 0):
                continue
            left = abs(need[e]) - sum(x for x, _ in found[e])
            ts = parse_ts(r.get("filled_at"))
            if left <= 0 or ts is None:
                continue
            try:
                qty = abs(float(r.get("qty") or 0))
            except ValueError:
                continue
            found[e].append((min(left, qty), ts.timestamp()))
        for e, q in need.items():
            sign = 1.0 if q > 0 else -1.0
            got = [[sign * x, t] for x, t in reversed(found.get(e, [])) if x > 0]
            rest = abs(q) - sum(abs(x) for x, _ in got)
            if rest > 0.5:
                got.append([sign * rest, now])
            self.lots[e] = got

    def update_lots(self, inv, now):
        """Keep each market's FIFO lots in step with its position. The positions read is the truth (fills only
        ever reach it), so a cycle's net change is that cycle's fills netted: growing -> a new lot stamped now;
        shrinking -> the OLDEST lots go first; changing sign -> one fresh lot. Saved to position_lots_file (live)."""
        if not self.lots_seeded:
            self.lots_seeded, self.lots_dirty = True, True
            self.seed_lots(inv, now)
        changed = False
        for e in set(self.lots) | {e for e, q in inv.items() if round(q)}:
            q = round(inv.get(e, 0.0))
            lots = self.lots.get(e, [])
            held = round(sum(x for x, _ in lots))
            if q == held:
                continue
            changed = True
            if not q:
                self.lots.pop(e, None)
            elif not held or (q > 0) != (held > 0):
                self.lots[e] = [[float(q), now]]
            elif abs(q) > abs(held):
                self.lots[e] = lots + [[float(q - held), now]]
            else:
                drop = abs(held) - abs(q)               # sold: oldest lots first
                out = []
                for x, t in lots:
                    take = min(abs(x), drop)
                    drop -= take
                    if abs(x) - take > 1e-9:
                        out.append([x - take if x > 0 else x + take, t])
                self.lots[e] = out
        if changed or self.lots_dirty:
            self.lots_dirty = False
            if self.api.live and self.cfg.position_lots_file:
                try:
                    write_json(bot_path(self.cfg.position_lots_file), self.lots)
                except OSError as err:
                    log.warning("could not save position lots: %s", err)

    def age_hours(self, ex, now=None):
        """Share-weighted age of this market's position in hours (0 = flat or unknown)."""
        lots = self.lots.get(ex.eid)
        total = sum(abs(x) for x, _ in lots) if lots else 0.0
        if not total:
            return 0.0
        now = time.time() if now is None else now
        return sum(abs(x) * (now - t) for x, t in lots) / total / 3600

    def portfolio_age(self, now):
        """(share-weighted age of every held share in hours, positions older than 3 h, older than 12 h);
        a position's age is its own share-weighted age."""
        shares = weighted = 0.0
        over3 = over12 = 0
        for lots in self.lots.values():
            n = sum(abs(x) for x, _ in lots)
            if not n:
                continue
            age = sum(abs(x) * (now - t) for x, t in lots) / n / 3600
            shares, weighted = shares + n, weighted + n * age
            over3, over12 = over3 + (age > 3), over12 + (age > 12)
        return (weighted / shares if shares else 0.0), over3, over12

    # ------------------------------------------------------------------ capital ceiling
    def capital_in_positions(self, pos, inv, fvs):
        """Cash tied up in positions: the positions read's totalMarketValue when it gives one (the exchange's
        own valuation), else our sum of |shares| x price at fair value (a short YES = NO shares, 1 - price each)."""
        try:
            v = float(((pos or {}).get("summary") or {}).get("totalMarketValue") or 0)
        except (TypeError, ValueError, AttributeError):
            v = 0.0
        if v > 0:
            return v
        total = 0.0
        for e, q in inv.items():
            if not q:
                continue
            p = self.risk_fv(e, fvs) if e in self.ex else (fvs.get(e) or 0.5)
            total += abs(q) * (p if q > 0 else 1 - p)
        return total

    MARK_SD_REFRESH_SECONDS = 1800.0

    def refresh_mark_sd(self, now_m, force=False):
        """Mark-fragility estimator, every 30 min (and at the first cycle): per market, the sd of the 10-min change
        of the tournament mid over the last mark_frag_window_hours of the recorder's snapshots (one SQL). Main
        thread, like the recorder's writes. No recorder, or too few samples -> no estimate for that market."""
        if not self.db or (not force and now_m - self.mark_sd_time < self.MARK_SD_REFRESH_SECONDS):
            return
        self.mark_sd_time = now_m
        cfg = self.cfg
        cutoff = iso(utcnow() - timedelta(hours=cfg.mark_frag_window_hours))
        try:
            rows = self.db.execute(
                "SELECT eid, ts, best_bid, best_ask FROM snapshots WHERE mode = ? AND ts >= ? "
                "AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY eid, ts",
                ("live" if self.api.live else "dry", cutoff)).fetchall()
        except sqlite3.Error as e:
            log.warning("mark fragility: could not read snapshots (%s) - keeping the previous estimate", e)
            return
        secs, series = {}, defaultdict(list)
        for eid, ts, bb, ba in rows:
            t = secs.get(ts)
            if t is None:
                try:
                    t = secs[ts] = parse_ts(ts).timestamp()
                except (TypeError, ValueError):
                    continue
            series[str(eid)].append((t, (float(bb) + float(ba)) / 2))
        est = {}
        for eid, ser in series.items():
            sd = mark_step_sd(ser, cfg.mark_frag_min_samples, cfg.mark_frag_floor_sd)
            if sd is not None:
                est[eid] = sd
        self.mark_sd = est
        log.info("mark fragility: sd of the 10-min mid step for %d of %d markets (%d snapshot rows, %g h)",
                 len(est), len(series), len(rows), cfg.mark_frag_window_hours)

    def update_mark_frag(self, inv, cfg):
        """Sum over positions of |pos| x sd (mark noise in $ per 10-min step), the total cap with hysteresis (on above
        mark_frag_total_max_cash, off below 80% of it; only with mark_frag_enabled) and the status.json fields."""
        steps = {e: abs(q) * self.mark_sd[e] for e, q in inv.items() if q and e in self.mark_sd}
        total = sum(steps.values())
        cap = cfg.mark_frag_total_max_cash
        if not cfg.mark_frag_enabled or cap <= 0:
            on = False
        elif self.mark_frag_over:
            on = total >= 0.8 * cap
        else:
            on = total > cap
        if on != self.mark_frag_over:
            log.warning("%s mark-fragility cap: positions add %.0f $ of mark noise per 10 min (cap %.0f) - adding sides %s",
                        "ENTERING" if on else "leaving", total, cap, "withdrawn" if on else "back to normal")
        self.mark_frag_over = on
        top = sorted(steps.items(), key=lambda kv: -kv[1])[:10]
        return {"mark_frag_total_cash": round(total, 2),
                "mark_frag_top": {(self.ex[e].label if e in self.ex else e): round(c, 2) for e, c in top},
                "mark_frag_capped_markets": sum(1 for c in steps.values() if c >= cfg.mark_frag_max_step_cash),
                "mark_frag_estimates": len(self.mark_sd),
                "mark_frag_total_cap_active": on}

    def update_capital_ceiling(self, frac, cfg):
        """Capital ceiling on above capital_in_positions_max_frac, off again below it - 0.05; each change logged once.
        An unknown account value keeps the current state."""
        cap = cfg.capital_in_positions_max_frac
        if cap <= 0:
            on = False
        elif frac is None:
            on = self.capital_over
        elif self.capital_over:
            on = frac >= cap - 0.05
        else:
            on = frac > cap
        if on != self.capital_over:
            log.warning("%s capital ceiling: %s of account value in positions (ceiling %.0f%%) - adding sides %s",
                        "ENTERING" if on else "leaving", f"{100 * frac:.0f}%" if frac is not None else "?",
                        100 * cap, f"at x{cfg.capital_ceiling_adding_size_factor:g}" if on else "back to normal")
        self.capital_over = on

    def update_adding_resume(self, frac, cfg):
        """Package 8 adding_factor_capital_on (> 0): with the capital ceiling on, capital in positions / account
        below it -> the resume factor is in force (adding_resume); off again at >= it + 0.01, or when the ceiling
        or the setting is off. An unknown account value keeps the current state. Each change logged once."""
        thr = cfg.adding_factor_capital_on
        if thr <= 0 or not self.capital_over:
            on = False
        elif frac is None:
            on = self.adding_resume
        elif self.adding_resume:
            on = frac < thr + 0.01
        else:
            on = frac < thr
        if on != self.adding_resume:
            log.warning("capital ceiling adding factor: %s in positions (resume below %.0f%%) - adding sides at x%g",
                        f"{100 * frac:.1f}%" if frac is not None else "?", 100 * thr,
                        max(cfg.capital_ceiling_adding_size_factor, cfg.capital_ceiling_adding_size_factor_resume)
                        if on else cfg.capital_ceiling_adding_size_factor)
        self.adding_resume = on

    def ceiling_adding_factor(self, cfg):
        """The capital ceiling's adding-side factor in force: 1 (ceiling off), the configured factor, or (Package 8
        adding_factor_capital_on) the larger of it and capital_ceiling_adding_size_factor_resume."""
        if not self.capital_over:
            return 1.0
        if self.adding_resume:
            return max(cfg.capital_ceiling_adding_size_factor, cfg.capital_ceiling_adding_size_factor_resume)
        return cfg.capital_ceiling_adding_size_factor

    def risk_fv(self, e, fvs, members=None):
        """The probability the risk model uses for market e: this cycle's fair value when there is one; else, for
        a market we hold, in this order: the liquid Polymarket reference; 1 minus the other leg's fair value in a
        two-leg race; the exchange's own mark of the position (currentPrice); the last fair value we had; 0.5.
        (2 Oct 11:22: Rep U.S. House, short 9,396, was unpriced after a restart and the old `or 0.5` treated it as
        a coin flip: settlement risk 20.6k -> 31k, reduce-only, no quotes there.) The fallback used for a held
        position is logged once per market and source."""
        p = fvs.get(e)
        if p is not None:
            return p
        src = None
        ref = self.cur_refs.get(e)
        if ref is not None and e in self.cur_liquid:
            p, src = ref, f"Polymarket {ref:.3f}"
        else:
            others = [o for o in (members or self.groups.get(self.ex[e].group, ())) if o != e and fvs.get(o) is not None]
            if len(others) == 1 and len(members or self.groups.get(self.ex[e].group, ())) == 2:
                p, src = max(0.0, min(1.0, 1 - fvs[others[0]])), f"1 - {self.ex[others[0]].label} {fvs[others[0]]:.3f}"
            elif self.pos_marks.get(e) is not None:
                p, src = self.pos_marks[e], f"exchange mark {self.pos_marks[e]:.4f}"
            elif self.ex[e].last_fv is not None:
                p, src = self.ex[e].last_fv, f"last fair value {self.ex[e].last_fv:.3f}"
            else:
                p, src = 0.5, "0.5 (nothing better)"
        if self.fv_fallback_logged.get(e) != src:
            self.fv_fallback_logged[e] = src
            log.warning("%s unpriced: risk uses %s for its %+.0f-share position", self.ex[e].label, src,
                        self.ex[e].inv)
        return p

    def settlement_risk(self, inv, fvs, party_delta):
        """R7: national swing shock (risk_swing_shock x |net Rep-minus-Dem YES shares|) plus risk_z standard
        deviations of the settlement value of every race, races independent once the swing is taken out.
        The cycle uses min(this, sum of per-race maxima)."""
        var = 0.0
        for members in self.groups.values():
            legs = [(inv.get(e, 0.0), self.risk_fv(e, fvs, members) if inv.get(e) else (fvs.get(e) or self.ex[e].last_fv or 0.5))
                    for e in members]
            if any(x for x, _ in legs):
                var += race_variance(legs)
        return self.cfg.risk_swing_shock * abs(party_delta) + self.cfg.risk_z * math.sqrt(var)

    def total_worst_case(self, inv, fvs):
        """Sum over races of the worst-case settlement loss (see worst_case_loss)."""
        total = 0.0
        for members in self.groups.values():
            legs = [(inv.get(e, 0.0), self.risk_fv(e, fvs, members) if inv.get(e) else (fvs.get(e) or self.ex[e].last_fv or 0.5))
                    for e in members]
            if any(x for x, _ in legs):
                total += worst_case_loss(legs)
        return total

    # ------------------------------------------------------------------ turnover control
    def seed_turnover(self):
        """At start: the last hours of fills.csv and, if the recorder's file exists, its trades table, so a
        restart neither forgets the flow nor judges markets on minutes of it."""
        now = time.time()
        try:
            n_f = self.turnover.seed_fills(read_fills(bot_path(self.cfg.fills_csv)), now)
        except (OSError, csv.Error) as e:
            log.warning("turnover: could not read fills.csv: %s", e)
            n_f = 0
        n_t = self.turnover.seed_tape(bot_path(self.cfg.record_file) if self.cfg.record_file else "", now)
        if n_f or n_t:
            log.info("turnover: seeded %d fills and %d tape trades, %.1fh of the %gh window observed", n_f, n_t,
                     self.turnover.observed_hours(now, self.cfg.turnover_window_hours), self.cfg.turnover_window_hours)

    def note_turnover(self, new):
        """Our new fills (log_fills) and the trades the realtime feed reported since the last call."""
        wall = time.time()
        for f in new or ():
            try:
                t = parse_ts(f.get("filledAt"))
            except (TypeError, ValueError):
                t = None
            self.turnover.add(f.get("exchangeId"), min(t.timestamp(), wall) if t is not None else wall,
                              f.get("quantity"))
        if self.feed and hasattr(self.feed, "take_flow"):
            for t, eid, q in self.feed.take_flow():
                self.turnover.add(eid, t, q, tape=True)

    def refresh_turnover(self, now_m, force=False):
        """Each market's observed flow (shares/h over turnover_window_hours), at most once a minute."""
        if not force and now_m - self.turnover_refreshed < 60.0:
            return
        self.turnover_refreshed, now, cfg = now_m, time.time(), self.cfg
        self.turnover.prune(now)
        self.turnover_flow = {e: self.turnover.per_hour(e, now, cfg.turnover_window_hours, cfg.turnover_use_tape)
                              for e in self.ex}
        # Hysteresis: dead below turnover_min_shares_per_hour, alive again only above turnover_alive_shares_per_hour,
        # and no flip before turnover_min_state_minutes in the current state (a market's first verdict is at once).
        hold = 60 * cfg.turnover_min_state_minutes
        for e, flow in self.turnover_flow.items():
            st = self.turnover_state.get(e)
            if flow is None:
                self.turnover_state.pop(e, None)          # not judged (yet): alive, no state kept
                continue
            dead = st[0] if st else False
            want = (flow < cfg.turnover_min_shares_per_hour if not dead
                    else flow <= max(cfg.turnover_alive_shares_per_hour, cfg.turnover_min_shares_per_hour))
            if st is None:
                self.turnover_state[e] = (want, now_m)
            elif want != dead and now_m - st[1] >= hold:
                self.turnover_state[e] = (want, now_m)

    def turnover_dead(self, ex, size, cfg):
        """A dead market where we hold a position: dead per refresh_turnover (flow, hysteresis; once judged)
        and |race-netted position| at least min(one quote, 100 shares)."""
        st = self.turnover_state.get(ex.eid)
        if not st or not st[0]:
            return False
        return abs(ex.eff) >= max(1.0, min(size or 0.0, 100.0))

    def turnover_health(self, inv, fvs):
        """status.json: dead markets holding positions, the capital in them (at fair value) and the biggest."""
        rows = []
        for e, x in self.ex.items():
            q = inv.get(e, 0.0)
            if not x.turnover_dead or not q:
                continue
            p = fvs.get(e) or x.last_fv or 0.5
            rows.append((abs(q) * (p if q > 0 else 1 - p), x.label, self.turnover_flow.get(e)))
        rows.sort(reverse=True)
        return {"turnover_dead_markets": len(rows), "turnover_dead_capital": round(sum(r[0] for r in rows)),
                "turnover_dead_top": {lab: [round(c), round(f or 0.0, 1)] for c, lab, f in rows[:8]},
                "turnover_judged": self.turnover.judged(time.time(), self.cfg.turnover_window_hours)}

    # ------------------------------------------------------------------------------ decide
    def decide(self, ex, fv, inv, eff, global_reduce, party_delta, now_m, ref=None, book_fv=None, ref_liquid=False):
        """What should be resting on this exchange right now? NO_QUOTE = nothing.
        fv is what we quote around; book_fv is the tournament book's own price (for the Polymarket guard);
        ref_liquid says whether the Polymarket price is reliable enough to size positions with Kelly."""
        cfg = self.burst_cfg if self.burst else self.cfg
        ex.fl_tag, ex.turnover_tag, ex.turnover_dead, ex.bb_tag = "", "", False, ""
        ex.ro_clip = ""
        ex.inv, ex.eff, ex.ref = inv.get(ex.eid, 0.0), eff.get(ex.eid, 0.0), ref
        hrs = self.hours_to_close(ex)
        if hrs * 60 <= cfg.stop_minutes_before_close:
            return NO_QUOTE                                   # too close to settlement
        b = ex.book or {}
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        if ex.eid in self.ref_only and ex.eid in self.ref_tops:   # R5 from bulk tops: no current book
            best_bid, best_ask = self.ref_tops[ex.eid]           # (other traders' best prices, see r5_top)

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
        tb, ta = self.tilt_blocks(ex, ref)                    # T2.4 (off by default)
        no_bid, no_ask = no_bid or tb, no_ask or ta

        # Reference-price guard: if Polymarket says this contract is worth clearly MORE than the
        # tournament book does, don't sell it to anyone here (they probably know); clearly LESS -> don't
        # buy. Compared with the book's own price, not the leaned fair value (see cycle step 3).
        exempt_bid = exempt_ask = False
        if ref is not None:
            base = book_fv if book_fv is not None else fv
            # Package 8 ref_guard_tilted: the gap from the tilted reference (T2.1 on here), not raw Polymarket
            gref = self.tilted_ref_for(ex, ref, now_m) if cfg.ref_guard_tilted else ref
            g_ask, g_bid = gref - base > cfg.ref_guard_gap, base - gref > cfg.ref_guard_gap
            if cfg.ref_guard_exits:               # Package 8: never block the side shrinking THIS market's position
                exempt_ask, exempt_bid = g_ask and ex.inv >= 1, g_bid and ex.inv <= -1
                g_ask, g_bid = g_ask and not exempt_ask, g_bid and not exempt_bid
            no_ask = no_ask or g_ask
            no_bid = no_bid or g_bid

        # Tail guard: near 0 or 1, one side risks ~1 a share to earn ~1c, and a single upset wipes out
        # many fills. Don't take that side, except to shrink a position we already hold.
        bid_cap = ask_cap = None
        if fv < cfg.tail_low:
            ask_cap = max(0, int(ex.inv))         # selling YES near 0 = buying NO near 1
        if fv > cfg.tail_high:
            bid_cap = max(0, int(-ex.inv))        # buying YES near 1
        if exempt_ask:                            # (ref_guard_exits: the exempted exit never flips the position)
            ask_cap = int(ex.inv) if ask_cap is None else min(ask_cap, int(ex.inv))
        if exempt_bid:
            bid_cap = int(-ex.inv) if bid_cap is None else min(bid_cap, int(-ex.inv))

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
        edge = reduce_size = None
        if ex.eid in self.ref_only:               # R5: priced from Polymarket alone -> wider and small
            edge = max(cfg.min_edge, cfg.ref_only_min_edge)
            full = planned if planned is not None else cfg.order_size_frac * self.bankroll()
            planned = min(full, max(1.0, cfg.ref_only_size_frac * self.bankroll()))
            if cfg.ref_only_reduce_full:          # ...but a held position leaves at the market's normal size
                reduce_size = full
        if cfg.tilt_exit_full_size and self.tilt_exit_side(ex) is not None:   # Package 8: the whole position
            reduce_size = max(reduce_size or 0, abs(ex.inv))
        if cfg.market_edge_enabled and ex.eid in self.market_edge:   # rival-floor map: this market's own floor
            own = max(cfg.min_edge, min(self.market_edge[ex.eid], cfg.market_edge_max))
            edge = own if edge is None else max(edge, own)
        ex.age = self.age_hours(ex)
        adding = cfg.capital_ceiling_adding_size_factor if self.capital_over else 1.0
        if self.adding_resume:                    # Package 8 adding_factor_capital_on: the resume factor
            adding = self.ceiling_adding_factor(cfg)
        adding_limit = 1.0
        ex.turnover_dead = self.turnover_dead(ex, planned if planned is not None
                                              else cfg.order_size_frac * self.bankroll(), cfg)
        if ex.turnover_dead and cfg.turnover_control_enabled:   # factors multiply (both can apply)
            adding *= cfg.turnover_dead_adding_factor
            adding_limit = cfg.turnover_dead_max_position_frac
            ex.turnover_tag = " dead"
        if cfg.gap_size_shrink > 0 and book_fv is not None and ex.eid not in self.ref_only:   # X5 gap-size shrink
            adding *= gap_size_factor(fv, book_fv, cfg)
        if cfg.backstop_soft_frac > 0:            # Package 6 candidate: backstop soft band (set in cycle step 6)
            adding *= self.backstop_adding_factor
        if cfg.tail_adding_factor != 1.0 and ref is not None and (ref < cfg.tail_low or ref > cfg.tail_high):
            adding *= cfg.tail_adding_factor      # Package 6 candidate: tail adding-size factor (raw Polymarket r)
        frag_limit = None
        if cfg.mark_frag_enabled:                 # mark-fragility cap: the adding side's limit, and the total cap
            if self.mark_frag_over:
                adding = 0.0
            sd = self.mark_sd.get(ex.eid)
            if sd:
                frag_limit = mark_frag_limit(sd, cfg, planned if planned is not None
                                             else cfg.order_size_frac * self.bankroll())
        side, bias_edge, bias_size = fl_side(fv, ex.fl_side, cfg)
        ex.fl_side, tag = side, side
        side = "bid" if side == "mid" else side       # mid band: an optional extra edge on bids, full size
        ex.fl_tag = (f" fl:{tag}+{100 * bias_edge:g}c" if side and (ex.inv > -1 if side == "bid" else ex.inv < 1)
                     else "")                         # (shown only while it changes the quote: not when unloading)
        u_side = None if reduce_only else self.unload_side(ex, now_m)   # reduce-only / flatten: stricter anyway
        u_size = int(self.unloads[ex.eid]["left"] * cfg.fast_unload_size_mult) if u_side else None
        # reduce_from_book (A): the reducing side prices from the book's own price. Not in ref-only markets, not
        # without a book price, not for reduce_from_book_pause_s after a Polymarket jump here, and (staging gate)
        # not in headline markets unless reduce_from_book_headline.
        reduce_fv = (book_fv if cfg.reduce_from_book and book_fv is not None and ex.eid not in self.ref_only
                     and (cfg.reduce_from_book_headline or ex.group not in cfg.headline_races)
                     and now_m - ex.ref_jump_at >= cfg.reduce_from_book_pause_s else None)
        if reduce_fv is not None and cfg.reduce_from_book_dead_only:   # X11: only where little informed flow
            flow = self.turnover_flow.get(ex.eid)
            if not (ex.turnover_dead or (cfg.reduce_from_book_max_turnover > 0 and flow is not None
                                         and flow < cfg.reduce_from_book_max_turnover)):
                reduce_fv = None
        why = {}
        q = compute_quote(fv, ex.inv, inv_for_quote, best_bid, best_ask, cfg, reduce_only, no_bid, no_ask,
                             bid_cap, ask_cap, kelly_p=kelly_p, bankroll=self.bankroll(),
                             shift=self.party_shift(ex, party_delta), order_size=planned, position_limit=headline_limit,
                             min_edge=edge, reduce_size=reduce_size, net_inv=ex.eff, age_hours=ex.age,
                             adding_factor=adding, bias_side=side, bias_edge=bias_edge, bias_size=bias_size,
                             unload_side=u_side, unload_edge=cfg.fast_unload_edge, unload_size=u_size,
                             adding_limit_factor=adding_limit, frag_limit=frag_limit,
                             behind_best=ex.eid not in self.ref_only, reduce_fv=reduce_fv, why=why)
        ex.ro_clip = why.get("ro_clip", "")
        ex.bb_tag = " bb" if q.behind else ""
        ex.lad_ctx = (adding, adding_limit, frag_limit)   # (R3 ladder: the same factors and limits)
        if cfg.hold_target_hours > 0 and (not reduce_only or cfg.exit_quotes_in_reduce_only):   # C hold target:
            # aged lots' reducing side joins the best (Package 6: also in reduce-only, behind its flag)
            q = self.hold_quote(ex, q, best_bid, best_ask, book_fv if book_fv is not None else fv, cfg, ref, now_m)
        return q

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

    def tilt_blocks(self, ex, ref):
        """T2.4 tilt-exposure cap -> (no_bid, no_ask). A market's contribution to tilt_exposure is position x
        (r - c): buying where r > c (or selling where r < c) grows it, the other side shrinks it. Beyond
        tilt_exposure_max_frac x account, block whichever side would make |tilt_exposure| worse. Off (0), no
        Polymarket price, r == c, or a headline market without tilt_exposure_headline: nothing blocked."""
        cfg = self.cfg
        if cfg.tilt_exposure_max_frac <= 0 or ref is None or (ex.group in cfg.headline_races
                                                              and not cfg.tilt_exposure_headline):
            return False, False
        d = ref - tilted_ref(ref, 1.0, self.legs(ex))        # tilted_ref(r, 1, legs) = c
        cap, x = cfg.tilt_exposure_max_frac * self.bankroll(), getattr(self, "tilt_exposure", 0.0) or 0.0
        if d == 0 or abs(x) <= cap:
            return False, False
        grow_by_buying = (d > 0) == (x > 0)
        return grow_by_buying, not grow_by_buying

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
                return Change(ex, resting, True, [], self.change_key(ex, pull=True, reprice=True), count=False,
                              unsafe=True)
            return None

        # The normal sizes (no size factor: burst, capital ceiling, favourite-longshot), within every position /
        # cash limit: the most a resting order may hold and still stay. A full-size order placed before a factor
        # shrank the wanted size is kept, not pulled as "unsafe" (2 Oct 11:31: the capital ceiling's x0.25 made
        # ~200 resting orders urgent reprices at once, which bypassed the budget and drew the 429s).
        full_bid = max(q.bid_size, q.bid_max or 0) if q.bid is not None else q.bid_size
        full_ask = max(q.ask_size, q.ask_max or 0) if q.ask is not None else q.ask_size
        if self.burst and self.cfg.burst_size_factor != 1.0:
            # Burst mode places burst_size_factor of each size: compare what rests with THAT size, or a half-size
            # order fails keep_fraction (187 of 375 < 50%) and is cancelled and replaced every cycle. A full-size
            # order placed before the burst is NOT oversized though (ceiling = the normal size), or entering burst
            # mode would pull and re-place every resting quote while the exchange is slow.
            k = self.cfg.burst_size_factor
            q = replace(q, bid_size=max(1, int(q.bid_size * k)) if q.bid_size else 0,
                        ask_size=max(1, int(q.ask_size * k)) if q.ask_size else 0)
        if q.bid is not None and q.bid_size > 0 and ex.inv <= -1:
            # Package 6 reduce_no_as_sell: a bid buying back NO we hold goes out as a covered "sell NO", capped at
            # the NO free to sell; the part beyond it (going long) waits until the short is gone. Capping the WANTED
            # size (not only the order) keeps a resting capped order from looking too small and churning.
            cover = self.cover_no_qty(ex.eid, ex.inv, 0)
            if 1 <= cover < q.bid_size:
                q = replace(q, bid_size=cover)
        bids = [o for o in resting if o.is_bid]
        asks = [o for o in resting if not o.is_bid]
        fix_bid = self.side_fix(ex, bids, q.bid, q.bid_size, q.bid_limit, True, full_bid, fv, now, now_m)
        fix_ask = self.side_fix(ex, asks, q.ask, q.ask_size, q.ask_limit, False, full_ask, fv, now, now_m)
        if self.cfg.ttl_expire_as_cancel:  # an order just left to expire: re-quote that side only after the grace
            if self.expiry_wait(ex, bids, True, now):
                fix_bid = False
            if self.expiry_wait(ex, asks, False, now):
                fix_ask = False
        # Reduce-only, flatten and exit windows: always do exactly what the risk logic asks (no holding, no
        # burst-mode skipping), or a position could be left to grow or never be exited.
        critical = self.global_reduce or self.hours_to_close(ex) <= self.cfg.flatten_hours_before_close
        # Refill cooldown: no new order on a side that was just hit repeatedly; what rests there stays while safe.
        cool_bid = not critical and self.refill_cooling(ex, True, now_m)
        cool_ask = not critical and self.refill_cooling(ex, False, now_m)
        if cool_bid:
            fix_bid = fix_bid and any(unsafe_order(o, q.bid, full_bid, q.bid_limit, True) for o in bids)
        if cool_ask:
            fix_ask = fix_ask and any(unsafe_order(o, q.ask, full_ask, q.ask_limit, False) for o in asks)
        if self.cfg.churn_control and not critical:
            if fix_bid and self.hold_side(ex, bids, q.bid, q.bid_limit, full_bid, True, now, now_m):
                fix_bid = False                   # (full size: a full-size order is still safe in a burst)
            if fix_ask and self.hold_side(ex, asks, q.ask, q.ask_limit, full_ask, False, now, now_m):
                fix_ask = False
        if not (fix_bid or fix_ask):
            return None                   # book already matches: keep our queue position
        if not self.cfg.churn_count_sent:            # old behaviour: counted when planned (see count_reprices)
            for side, fix, rest in (("bid", fix_bid, bids), ("ask", fix_ask, asks)):
                if fix and rest:
                    ex.reprices.setdefault(side, deque()).append(now_m)

        if self.burst and ex.eid not in self.burst_set and not critical:
            # Burst mode, not a top market: no reprices. Keep what rests while it's still safe (price inside the
            # limit, size not above what's wanted) and pull what isn't; an EMPTY side still gets a (smaller) quote,
            # which costs only a share of one batch.
            fix_bid = fix_bid and (q.bid is None or not bids or any(
                o.price > (q.bid_limit if q.bid_limit is not None else q.bid) + 1e-9 or o.qty > full_bid + 1e-9 for o in bids))
            fix_ask = fix_ask and (q.ask is None or not asks or any(
                o.price < (q.ask_limit if q.ask_limit is not None else q.ask) - 1e-9 or o.qty > full_ask + 1e-9 for o in asks))
            doomed = (bids if fix_bid else []) + (asks if fix_ask else [])
            new = [] if now_m < ex.pause_until else (
                ([self.new_order(ex, True, q.bid, q.bid_size, fv, now)]
                 if fix_bid and q.bid is not None and not bids and not cool_bid else [])
                + ([self.new_order(ex, False, q.ask, q.ask_size, fv, now)]
                   if fix_ask and q.ask is not None and not asks and not cool_ask else []))
            if not doomed and not new:
                return None
            return Change(ex, doomed, False, new, self.change_key(ex, pull=bool(doomed) or not new, reprice=bool(doomed),
                                                                  sides=(fix_bid, fix_ask)),
                          unsafe=bool(doomed))
        # Cancel the wrong side(s). Both wrong -> one cancel-all for the exchange; else per order.
        doomed = (bids if fix_bid else []) + (asks if fix_ask else [])
        new = []
        if now_m >= ex.pause_until:       # (sizes already scaled for burst mode above)
            if fix_bid and q.bid is not None and not cool_bid:
                new.append(self.new_order(ex, True, q.bid, q.bid_size, fv, now))
            if fix_ask and q.ask is not None and not cool_ask:
                new.append(self.new_order(ex, False, q.ask, q.ask_size, fv, now))
        log.info("%s%-26.26s fv %s%s inv %+5.0f race %+5.0f%s | bid %s ask %s", "" if self.api.live else "[dry] ",
                 ex.label, f"{fv:.3f}" if fv is not None else "  -  ",
                 f" (ref {ex.ref:.3f})" if ex.ref is not None else "", ex.inv, ex.eff,
                 f" age {ex.age:.0f}h" if ex.inv and ex.age > self.cfg.skew_age_after_hours else "",
                 fmt(q.bid, q.bid_size), fmt(q.ask, q.ask_size) + ex.fl_tag + ex.turnover_tag + ex.bb_tag + ex.lad_tag)
        if not doomed and not new:
            return None
        # An order that must not stay (unsafe: beyond its limit price, a side we no longer want, or above the
        # position / cash limits - NOT merely above a size factor, see full_bid) makes this change as urgent as a
        # pull: never deferred by the budget (but capped by urgent_writes_per_cycle).
        unsafe = ((fix_bid and any(unsafe_order(o, q.bid, full_bid, q.bid_limit, True) for o in bids))
                  or (fix_ask and any(unsafe_order(o, q.ask, full_ask, q.ask_limit, False) for o in asks)))
        urgent = self.cfg.never_defer_unsafe and unsafe
        return Change(ex, doomed, fix_bid and fix_ask, new,
                      self.change_key(ex, pull=not new or urgent, reprice=bool(doomed), sides=(fix_bid, fix_ask)),
                      unsafe=unsafe or (not new and bool(doomed)))

    def hold_side(self, ex, resting, price, limit, size, is_bid, now, now_m):
        """Churn control: keep this side's single resting order although it's off target, because it's still safe
        (inside the limit price) and either young (min_quote_life_seconds) or this side keeps getting re-quoted
        (churn_max_reprices in churn_window_seconds: another bot is stepping in front of us each time)."""
        cfg = self.cfg
        if price is None or limit is None or len(resting) != 1:
            return False
        o = resting[0]
        if (o.price > limit + 1e-9) if is_bid else (o.price < limit - 1e-9):
            return False                                  # no longer safe: must move
        if o.qty > size + 1e-9:
            return False                                  # bigger than now allowed (limits tightened): must shrink
        if o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry:
            return False
        if ex.eid in self.ref_moved or now_m - ex.ref_moved_at < 15:
            return False                                  # Polymarket moved here lately: follow it now
        if self.unload_urgent(ex, now_m) == ("bid" if is_bid else "ask"):
            return False                                  # new fast unload window: place the unload quote now
        young = o.order_id in self.recent_orders and now_m - self.recent_orders[o.order_id][1] < cfg.min_quote_life_seconds
        hist = ex.reprices.get("bid" if is_bid else "ask") or deque()
        while hist and now_m - hist[0] > cfg.churn_window_seconds:
            hist.popleft()
        return young or len(hist) >= cfg.churn_max_reprices

    def count_reprices(self, ch, now_m):
        """Churn control: this change's cancels are being sent now - count them as reprices of their sides."""
        if self.cfg.churn_count_sent and ch is not None:
            for side in ch.reprice_sides():
                ch.ex.reprices.setdefault(side, deque()).append(now_m)

    def change_key(self, ex, pull, reprice=False, sides=None):
        """Sending order: pulls first, then the party-control markets, then quotes for empty sides (cheap: a
        share of one batch), then reprices (a cancel each), biggest quotes first within each. Package 8
        tilt_exit_priority: an ordinary market's change touching its tilt exit (sides = (bid changes, ask changes))
        sorts between the party-control markets and the other ordinary ones."""
        urgent = ex.eid in self.ref_moved or self.unload_urgent(ex, time.monotonic())   # (unload: first placement)
        head = 0 if ex.group in self.cfg.headline_races else 1
        if head and sides is not None and self.cfg.tilt_exit_priority:
            side = self.tilt_exit_side(ex)
            if side is not None and sides[0 if side == "bid" else 1]:
                head = 0.5
        return (0 if pull else 0.5 if urgent else 1, head,
                1 if reprice else 0,
                -self.size_plan.get(ex.eid, 0))

    def tilt_exit_side(self, ex):
        """Package 8: "bid" / "ask" = the side shrinking this market's own position when that position adds to
        |tilt_exposure| (its contribution pos x (raw Polymarket - c), c = 1/legs, has the sign of the total, or is
        positive while the total is 0); None otherwise. Reads ex.inv / ex.ref as decide() set them this cycle."""
        r, pos = getattr(ex, "ref", None), ex.inv
        if r is None or not pos:
            return None
        contrib = pos * (r - tilted_ref(r, 1.0, self.legs(ex)))     # tilted_ref(r, 1, legs) = c
        total = getattr(self, "tilt_exposure", 0.0) or 0.0
        if contrib == 0 or (contrib > 0) != (total > 0 if total else True):
            return None
        return "ask" if pos > 0 else "bid"

    def reconcile(self, ex, q, resting, fv, now, now_m):
        """One write at a time (parallel_writes = 1): make the orders resting on this exchange match quote q.
        Cancels happen right away. New orders are returned so they can be sent in batches."""
        ch = self.plan_exchange(ex, q, resting, fv, now, now_m)
        if ch is None:
            return []
        self.count_reprices(ch, now_m)
        if ch.doomed and not self.cancel(ex.eid, ch.doomed, whole_exchange=ch.whole):
            log.warning("%s: could not confirm cancels - retrying next cycle", ex.label)
            return []                     # never stack new quotes on top of old ones
        return ch.new

    # ------------------------------------------------------------------------------ R3 resting depth ladder
    # Orders are tagged (side, level) in our notes (order_meta "level"; absent = 0, the touch quote). Level 0 is
    # planned exactly as before (plan_change sees only level-0 orders); the ladder is matched level by level on
    # top of it (plan_exchange), so only the levels that change are cancelled and re-placed.

    def order_level(self, o):
        """An order's ladder level from our notes: 0 = the touch quote (also any order we have no notes for)."""
        try:
            return int((self.order_meta.get(o.order_id) or {}).get("level") or 0)
        except (TypeError, ValueError):
            return 0

    def ladder_orders(self):
        """Our resting ladder orders (level >= 1): the live record, or the pretend ones in a dry run."""
        return [o for o in (self.my_orders if self.api.live else self.sim).values() if self.order_level(o) > 0]

    def ladder_setup(self, equity, capital, raw_orders, liquid, party_delta, resting):
        """Once a cycle, before planning: what the ladder may lock (self.lad_cash_left, spent market by market in
        plan_exchange). Free cash = account value - capital in positions - cash locked in non-ladder orders; the
        ladder may use it down to ladder_min_cash_frac of the account, and all resting orders together stay within
        quote_capital_frac of it. 2 Oct (101k account, 90k in positions, 11k free) -> no ladder at all."""
        cfg = self.cfg
        self.lad_liquid, self.lad_party_delta, self.lad_cash_left = set(liquid), party_delta, 0.0
        if not cfg.ladder_enabled or not equity:
            return
        if self.api.live:                         # (the ladder's own orders left out, so its budget doesn't move with it)
            lad = {o.order_id for o in self.ladder_orders()}
            other = reserved_cash([o for o in raw_orders or [] if _num(o.get("id")) not in lad])
        else:                                     # dry run: the pretend orders lock nothing real
            other = sum(ladder_lock(o) for os_ in resting.values() for o in os_ if self.order_level(o) == 0)
        free = equity - capital - other
        self.lad_cash_left = max(0.0, min(free - cfg.ladder_min_cash_frac * equity,
                                          cfg.quote_capital_frac * equity - other))
        self.health["ladder_free_cash_frac"] = round(free / equity, 3)

    def ladder_market(self, ex):
        """(offsets, size multiples, quote size) of this market's ladder, or None if it gets none (ladder_markets:
        "headline" = headline_races, "busy" = planned quote >= ladder_busy_size_frac of the account, "quiet")."""
        cfg, bank = self.cfg, self.bankroll()
        tiers = {x.strip() for x in cfg.ladder_markets.split(",")}
        size = (self.size_plan.get(ex.eid, cfg.size_min_frac * bank) if cfg.size_by_activity
                else cfg.order_size_frac * bank)
        if ex.group in cfg.headline_races:
            return (cfg.ladder_headline_offsets, cfg.ladder_headline_mults, size) if "headline" in tiers else None
        tier = "busy" if size >= cfg.ladder_busy_size_frac * bank - 1e-9 else "quiet"
        return (cfg.ladder_offsets, cfg.ladder_size_mults, size) if tier in tiers else None

    def ladder_caps(self, ex, fv, quote, factors=True):
        """{is_bid: f(price) -> most shares that side may have in orders, level 0 + ladder}: the same position limits
        as compute_quote (headline flat limit, else Kelly at that price with a liquid Polymarket price, else
        max_position_frac; also on the race-netted position), plus: reduce-only / flatten -> only what reduces the
        position; |race-netted| > ladder_max_inv_quotes quotes -> nothing on the side that adds; the tail guard; the
        national-swing (party) cap; the mark-fragility limit. Exit window: nothing.
        factors=True also applies decide's size FACTORS (capital ceiling, turnover control): the side that adds then
        gets no ladder. factors=False is the limit alone: what a resting ladder order may keep (hot-fix 2.2 - a
        factor never makes a resting order unsafe, like Quote.bid_max for level 0)."""
        cfg, bank = self.cfg, self.bankroll()
        hrs = self.hours_to_close(ex)
        if hrs <= cfg.exit_hours_before_close:
            return {True: lambda px: 0, False: lambda px: 0}
        inv, net = ex.inv, ex.eff
        headline = cfg.size_by_activity and ex.group in cfg.headline_races
        kelly_p = ex.ref if ex.ref is not None and ex.eid in self.lad_liquid else None

        def limit(px, yes):
            if headline:
                return cfg.headline_position_frac * bank
            if kelly_p is not None:
                return kelly_position(kelly_p, px, bank, cfg, yes=yes)
            return cfg.max_position_frac * bank
        extra = {True: [], False: []}
        if self.global_reduce or hrs <= cfg.flatten_hours_before_close:
            pos = inv if hrs <= cfg.flatten_per_market_hours else net
            extra[True].append(-pos)                       # only buy back a short...
            extra[False].append(pos)                       # ...or sell down a long
        if quote > 0 and abs(net) > cfg.ladder_max_inv_quotes * quote:
            extra[net > 0].append(0)                       # long -> no bid ladder; short -> no ask ladder
        adding, adding_limit, frag_limit = ex.lad_ctx
        if factors and (adding < 1.0 or adding_limit < 1.0 or (self.capital_over
                                                               and cfg.capital_ceiling_adding_size_factor < 1.0)):
            if net >= 0:
                extra[True].append(0)                      # (the touch quote shrinks there; no ladder there)
            if net <= 0:
                extra[False].append(0)
        if frag_limit is not None:                         # mark-fragility: the side growing |inv| here
            if inv >= 0:
                extra[True].append(frag_limit - inv)
            if inv <= 0:
                extra[False].append(frag_limit + inv)
        if fv < cfg.tail_low:
            extra[False].append(max(0.0, inv))
        if fv > cfg.tail_high:
            extra[True].append(max(0.0, -inv))
        sign = PARTY_SIGN.get(ex.party, 0)
        if sign:                                           # buying YES moves the party delta by sign a share
            cap, d = cfg.max_party_delta_frac * bank, self.lad_party_delta
            extra[True].append(cap - sign * d)
            extra[False].append(cap + sign * d)

        def bid_cap(px):
            lim = limit(px, True)
            c = lim - inv
            if cfg.limits_use_race_net and net > 0:
                c = min(c, lim - net)
            return min([c] + extra[True])

        def ask_cap(px):
            lim = limit(px, False)
            c = lim + inv
            if cfg.limits_use_race_net and net < 0:
                c = min(c, lim + net)
            return min([c] + extra[False])
        return {True: bid_cap, False: ask_cap}

    def ladder_targets(self, ex, q, fv, now_m, touch=None):
        """The ladder wanted on this exchange now: (want, allowed, blocked) - see ladder_levels for want/allowed;
        blocked {is_bid: True} = whatever ladder rests on that side must go now (level 0 not quoted there, or the
        ladder is pulled after a Polymarket jump). Moves the anchor: fair value when the ladder is (re)placed, kept
        until fair value is ladder_move from it; a Polymarket move of ladder_pull_jump from its reading at the
        anchor pulls the ladder for ladder_pull_seconds. Spends self.lad_cash_left (levels that don't fit are left
        out). touch {is_bid: price}: our resting level-0 prices (a level stays behind those too, or it would be
        placed and then pulled as at/inside the touch, every cycle)."""
        cfg = self.cfg
        self.lad_keep = {}                        # levels a resting ladder order may keep (see plan_exchange)
        blocked = {True: q.bid is None or q.bid_size <= 0, False: q.ask is None or q.ask_size <= 0}
        if fv is None:
            return {}, {}, {True: True, False: True}
        if (cfg.ladder_pull_jump > 0 and ex.ref is not None and ex.lad_ref is not None
                and abs(ex.ref - ex.lad_ref) >= cfg.ladder_pull_jump - 1e-9):
            log.info("LADDER %s: Polymarket moved %.3f -> %.3f - ladder pulled for %.0f s", ex.label,
                     ex.lad_ref, ex.ref, cfg.ladder_pull_seconds)
            ex.lad_pull_until = now_m + cfg.ladder_pull_seconds
            ex.lad_fv = ex.lad_ref = None
        if now_m < ex.lad_pull_until:
            return {}, {}, {True: True, False: True}
        if ex.lad_fv is None or abs(fv - ex.lad_fv) >= cfg.ladder_move - 1e-9:
            ex.lad_fv, ex.lad_ref = fv, ex.ref
        mk = self.ladder_market(ex)
        if mk is None or ex.eid in self.ref_only:     # not a ladder market, or no depth-checked price (R5)
            return {}, {}, blocked
        offsets, mults, quote = mk
        t_bid, t_ask = (touch or {}).get(True), (touch or {}).get(False)
        if q.bid is not None and t_bid is not None:   # behind BOTH the wanted and the resting level 0
            q = replace(q, bid=min(q.bid, t_bid))
        if q.ask is not None and t_ask is not None:
            q = replace(q, ask=max(q.ask, t_ask))
        b = ex.book or {}
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        max_cash = max(cfg.max_order_cash_frac * self.bankroll(), quote)   # (as compute_quote: a planned size
        want, _ = ladder_levels(ex.lad_fv, q, best_bid, best_ask, offsets, mults, quote,   # is capital-checked)
                                self.ladder_caps(ex, fv, quote), max_cash)
        # Without the size factors (level 0 at its unscaled size): what a resting ladder order may keep. No cash
        # cap here: it is a sizing rule, not a limit (it moves with the account value and the quote size, and as a
        # keep limit a 0.1% dip in either made every cash-capped level an urgent pull on every market at once).
        q_max = replace(q, bid_size=max(q.bid_size, q.bid_max or 0), ask_size=max(q.ask_size, q.ask_max or 0))
        keep, allowed = ladder_levels(ex.lad_fv, q_max, best_bid, best_ask, offsets, mults, quote,
                                      self.ladder_caps(ex, fv, quote, factors=False))
        self.lad_keep = keep
        for key in list(want):                    # never place more than a resting order may keep: allowed counts
            px, size = want[key]                  # level 0 at its unscaled size, so with the position limit
            size = min(size, int(allowed.get(key, 0)))   # binding a level could be placed and pulled as too big
            if size >= 1:                         # next cycle, every cycle
                want[key] = (px, size)
            else:
                del want[key]
        for key in sorted(want, key=lambda k: (k[1], not k[0])):   # level 1 first: the cash goes to the closest
            px, size = want[key]
            cash = size * (px if key[0] else 1 - px)
            if cash > self.lad_cash_left + 1e-9:
                del want[key]
            else:
                self.lad_cash_left -= cash
        return want, allowed, blocked

    def plan_exchange(self, ex, q, resting, fv, now, now_m):
        """plan_change for level 0 plus the R3 ladder on top: a Change, or None.
        Ladder disabled and none resting: exactly plan_change. Otherwise plan_change sees only the level-0 orders,
        and the ladder orders are matched level by level with ladder_targets (exact price, keep_fraction, expiry):
          - an UNSAFE ladder order (its side blocked, at/inside level 0, at/through the other side's best order,
            bigger than the limits now allow) is pulled at once: added to the level-0 change, or a pull of its own;
          - other ladder work (new levels, re-anchored or refreshed ones, ones no longer wanted) is its own Change
            in tier LADDER_TIER, after every level-0 change, and only in a cycle where level 0 there needs no
            write, nothing is in flight there, it's not burst mode and ladder_min_writes writes are left.
        Ladder cancels never count as level-0 reprices (churn control), and the exchange's cancel-all is used only
        when every order resting there goes."""
        cfg = self.cfg
        self.__dict__.setdefault("cover_planned", {}).pop(ex.eid, None)   # Package 6: this plan's covered NO afresh
        if q.bid is not None and q.bid_size > 0 and self.set_blocked(ex.eid, ex.inv, 0):
            # Package 7 no_set_aware_bids: every NO share here is in a NO+NO set - a bid would be a covered sale that
            # breaks the set (or a cash purchase): refused at 0 cash. No bid (the ladder's bids are blocked too).
            q = replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        lad = [o for o in resting if self.order_level(o) > 0]
        if not lad and not cfg.ladder_enabled:
            ex.lad_tag = ""
            return self.plan_change(ex, q, resting, fv, now, now_m)
        ex.lad_tag = f" L{len(lad)}" if lad else ""
        ch = self.plan_change(ex, q, [o for o in resting if self.order_level(o) == 0], fv, now, now_m)
        # The touch a ladder order must stay behind: the RESTING level-0 order (kept by reprice tolerance a tick
        # off its target), else level 0's target. Inside the wanted level 0 but behind the resting touch and
        # within level 0's limit price is only stale (tier 2), not an urgent pull (a 0.5c dip did that on every
        # market at once).
        rest0 = {s_: [o.price for o in resting if o.is_bid == s_ and self.order_level(o) == 0] for s_ in (True, False)}
        rest0 = {True: max(rest0[True]) if rest0[True] else None, False: min(rest0[False]) if rest0[False] else None}
        touch = {True: rest0[True] if rest0[True] is not None else q.bid,
                 False: rest0[False] if rest0[False] is not None else q.ask}
        for s_ in {o.is_bid for o in (ch.doomed if ch is not None else [])}:
            touch[s_] = q.bid if s_ else q.ask         # level 0 is re-placed there now: its new price is the touch
                                                       # (pulls then ride with that change, not as pulls of their own)
        if cfg.ladder_enabled:
            want, allowed, blocked = self.ladder_targets(ex, q, fv, now_m, rest0)
        else:
            want, allowed = {}, {}
            blocked = {True: q.bid is None or q.bid_size <= 0, False: q.ask is None or q.ask_size <= 0}
        b = ex.book or {}
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        urgent, stale, kept = [], [], set()
        keep = self.lad_keep if cfg.ladder_enabled else {}
        tol = cfg.reprice_tolerance_ticks * TICK + 1e-9
        limit = {True: q.bid_limit, False: q.ask_limit}
        for o in lad:
            key, t0, lim = (o.is_bid, self.order_level(o)), touch[o.is_bid], limit[o.is_bid]
            if (blocked[o.is_bid]
                    or (t0 is not None and (o.price >= t0 - 1e-9 if o.is_bid else o.price <= t0 + 1e-9))
                    or (lim is not None and (o.price > lim + 1e-9 if o.is_bid else o.price < lim - 1e-9))
                    or (o.is_bid and best_ask is not None and o.price >= best_ask - 1e-9)
                    or (not o.is_bid and best_bid is not None and o.price <= best_bid + 1e-9)
                    or o.qty > allowed.get(key, o.qty) + 1e-9):
                urgent.append(o)
                continue
            w = want.get(key)
            if (w is not None and key not in kept and abs(o.price - w[0]) <= tol
                    and w[1] * cfg.keep_fraction <= o.qty <= w[1] + 1e-9
                    and not (o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry)):
                kept.add(key)                              # exactly right: leave it (and its queue spot) alone
            elif (key not in kept and key in keep and abs(o.price - keep[key][0]) <= tol
                  and (w[1] if w is not None else 0) * cfg.keep_fraction <= o.qty <= keep[key][1] + 1e-9
                  and not (o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry)):
                kept.add(key)                              # only a size FACTOR wants it smaller / gone: it stays
            else:
                stale.append(o)
        ids = {o.order_id for o in urgent}
        if ch is not None:                                 # level 0 changes here: only unsafe ladder pulls ride along
            ch.doomed = ch.doomed + urgent
            ch.ladder |= ids
            ch.unsafe = ch.unsafe or bool(urgent)
            if ch.whole and len(ch.doomed) < len(resting):
                ch.whole = False                           # a cancel-all would take ladder orders that stay
            return ch
        if urgent:
            if busy(ex, now_m) and ex.cancelling:
                return None
            return Change(ex, urgent, len(urgent) == len(resting) and len(urgent) > 1, [],
                          self.change_key(ex, pull=True, reprice=True), count=False, unsafe=True, ladder=ids)
        if (busy(ex, now_m) or self.burst or not cfg.ladder_enabled and not stale
                or getattr(self.api, "writes_left", lambda: 10 ** 6)() < cfg.ladder_min_writes):
            return None                                    # burst mode: keep what rests (it's safe), add nothing
        placing = now_m >= ex.pause_until
        new = []
        for key in sorted(want, key=lambda k: (k[1], not k[0])):
            if key not in kept and placing and not self.refill_cooling(ex, key[0], now_m):
                new.append(self.new_order(ex, key[0], want[key][0], want[key][1], fv, now, level=key[1]))
        if new and getattr(cfg, "no_set_aware_bids", False) and self.reduce_no_on() and self.nono_set_part(ex.eid) >= 1:
            # Package 7: on a NO+NO set leg a ladder bid that is not a covered (lone-part) sale would need cash: dropped
            new = [(o, m) for o, m in new if o["action"] != "buy" or o.get("_no_sell")]
        replaced = {(o["action"] == "buy", m["level"]) for o, m in new}
        # A stale order goes when its level is replaced, no longer wanted or a duplicate; one we can't replace now
        # (refill cooldown, back-off) stays while it's safe, like level 0.
        doomed = [o for o in stale if (o.is_bid, self.order_level(o)) in replaced | kept
                  or (o.is_bid, self.order_level(o)) not in want]
        if cfg.churn_control:                      # churn control: a safe ladder order younger than min_quote_life stays
            young, seen = set(), set()            # (never a duplicate of a kept level, one per level at most)
            for o in doomed:
                k = (o.is_bid, self.order_level(o))
                if (k not in kept and k not in seen
                        and now_m - self.lad_placed.get(o.order_id, -1e18) < cfg.min_quote_life_seconds):
                    young.add(o.order_id)
                    seen.add(k)
            if len(self.lad_placed) > 5000:        # (forget placement times older than an hour)
                self.lad_placed = {k: v for k, v in self.lad_placed.items() if now_m - v < 3600}
            if young:
                doomed = [o for o in doomed if o.order_id not in young]
                new = [(o, m) for o, m in new if (o["action"] == "buy", m["level"]) not in
                       {(x.is_bid, self.order_level(x)) for x in stale if x.order_id in young}]
        if not doomed and not new:
            return None
        log.info("%sLADDER %-26.26s anchor %s | bid %s | ask %s | cancel %d", "" if self.api.live else "[dry] ",
                 ex.label, f"{ex.lad_fv:.3f}" if ex.lad_fv is not None else "-",
                 " ".join(fmt(*want[k]) for k in sorted(want) if k[0]) or "-",
                 " ".join(fmt(*want[k]) for k in sorted(want) if not k[0]) or "-", len(doomed))
        key = (LADDER_TIER,) + self.change_key(ex, pull=False, reprice=bool(doomed))[1:]
        return Change(ex, doomed, len(doomed) == len(resting) and len(doomed) > 1, new, key, count=False,
                      ladder={o.order_id for o in doomed})

    # ------------------------------------------------------------------------------ parallel order writes
    BURST_LOADING_MAX_SECONDS = 600.0     # the "first book download still running" grace never lasts longer

    def starting_up(self, now_m):
        """Just (re)started: cycles are long because every book is being downloaded for the first time, under our
        own request budget - not because the exchange is slow. See burst_startup_grace_seconds."""
        grace = self.cfg.burst_startup_grace_seconds
        if self.trading_since is None or grace <= 0:
            return False
        return now_m - self.trading_since < grace or self.first_books_loading(now_m)

    def first_books_loading(self, now_m):
        """Since the trading loop started, some book has never been downloaded (at most BURST_LOADING_MAX_SECONDS)."""
        return (self.trading_since is not None and now_m - self.trading_since < self.BURST_LOADING_MAX_SECONDS
                and any(ex.book is None for ex in self.ex.values()))

    def update_burst(self, now_m):
        """Burst mode on/off (see BURST PROTECTION). Its effects are in decide() and plan_change()."""
        cfg = self.cfg
        times = getattr(self.api, "write_times", None)       # measured on the wire (no queueing / throttle wait)
        while times:
            self.write_log.append(times.popleft())
        while self.write_log and now_m - self.write_log[0][0] > 60:
            self.write_log.popleft()
        if not cfg.burst_protection:
            self.burst = False
            return
        secs = sorted(d for _, d, _ in self.write_log)
        slow = (sum(t for _, _, t in self.write_log) >= cfg.burst_timeouts
                or (secs and secs[len(secs) // 2] >= cfg.burst_write_seconds)
                or (self.last_cycle_seconds >= cfg.burst_cycle_seconds and not self.starting_up(now_m)))
        if slow:
            self.burst_calm_since = now_m
            if not self.burst:
                self.burst = True
                log.warning("BURST MODE: the exchange is slow (median write %.1f s, %d timeouts, last cycle %.0f s) - "
                            "quoting only the %d biggest markets, %.0f%% size, %.1fc wider",
                            secs[len(secs) // 2] if secs else 0, sum(t for _, _, t in self.write_log),
                            self.last_cycle_seconds, cfg.burst_markets, 100 * cfg.burst_size_factor,
                            100 * cfg.burst_extra_edge)
        elif self.burst and now_m - self.burst_calm_since >= cfg.burst_calm_seconds:
            self.burst = False
            log.warning("burst mode over: exchange back to normal speed")
        if self.burst:
            ranked = sorted(self.ex, key=lambda e: (self.ex[e].group not in cfg.headline_races,
                                                    -self.size_plan.get(e, 0), e))
            self.burst_set = set(ranked[:cfg.burst_markets])
            self.burst_cfg = replace(cfg, min_edge=cfg.min_edge + cfg.burst_extra_edge)

    def send_changes(self, changes):
        """Send this cycle's order changes with up to parallel_writes requests in flight, most urgent first:
          1. every cancel at once (pulls first), plus the new orders on exchanges that need no cancel;
          2. as each cancel is confirmed, the new orders that were waiting for it.
        New orders on an exchange are only ever sent after its cancel is CONFIRMED (never two quotes on one
        side). The cycle waits at most write_wait_seconds; writes still running then carry on in the
        background, their exchanges are left alone (Ex.writes) and the results are applied next cycle."""
        cfg = self.cfg
        if self.pull_storm(changes):
            return
        deadline = time.monotonic() + cfg.write_wait_seconds
        # Urgent changes (key[0] == 0) that remove an unsafe order go first among them (urgent_writes_per_cycle).
        changes = sorted(changes, key=lambda c: (c.key[0], 0 if c.key[0] == 0 and c.unsafe else 1) + tuple(c.key[1:]))
        # Within the request budget, keeping write_read_reserve back so reads (positions, orders, books) never
        # starve; the least urgent changes wait for the next cycle.
        writes_left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        if cfg.startup_writes_per_minute and self.first_books_loading(time.monotonic()):
            # Still downloading the first books after a (re)start: book downloads and writes share the request
            # budget, so writes stay at the old 30/min until every book is in (pulls and unsafe orders still go).
            used = int(getattr(self.api, "wbudget", 0)) - writes_left
            writes_left = min(writes_left, cfg.startup_writes_per_minute - used)
        spare = min(getattr(self.api, "budget_left", lambda: 10 ** 6)() - self.write_reserve(), writes_left)
        kept, cost, orders, urgent_cost, capped = [], 0.0, 0, 0, False
        cap = cfg.urgent_writes_per_cycle
        for ch in changes:
            n = orders + len(ch.new)
            c = (0 if not ch.doomed else 1 if ch.whole else len(ch.doomed)) + (
                math.ceil(n / cfg.batch_size) - math.ceil(orders / cfg.batch_size))
            if ch.key[0] == 0 and cap > 0 and urgent_cost + c > cap and urgent_cost > 0:
                capped = True                 # urgent writes capped: further URGENT changes wait for the next cycle,
                continue                      #   the ordinary reprices and new quotes behind them still go
            if cost + c > spare and ch.key[0] != 0:
                break                         # pulls always go; everything after the first misfit waits
            if ch.key[0] == LADDER_TIER and min(spare, writes_left) - cost - c < cfg.ladder_min_writes:
                break                         # R3 ladder (sorted last): only with ladder_min_writes to spare after it
            kept.append(ch)
            cost, orders = cost + c, n
            if ch.key[0] == 0:
                urgent_cost += c
        if len(kept) < len(changes):
            log.info("request budget: %d of %d order changes deferred to the next cycle%s",
                     len(changes) - len(kept), len(changes),
                     f" (urgent writes capped at {cap}/cycle)" if capped else "")
        changes = kept
        now_m = time.monotonic()
        for ch in changes:
            self.count_reprices(ch, now_m)        # only what is sent counts (not what the budget deferred)
        waiting = set()
        for ch in changes:
            if ch.doomed:
                waiting.add(self.submit_write("cancel", [ch.ex.eid], (ch.doomed, ch.whole), change=ch))
        self.send_orders([c for c in changes if not c.doomed and c.new])
        self.phase_mark("send")
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
            self.progress("waiting for order writes")
        self.harvest_writes()
        self.phase_mark("wait")

    def pull_storm(self, changes):
        """Many pulls at once (reduce-only switching on pulls a side on every market held; pulls bypass the write
        budget, so ~70 DELETEs used to take ~2.4 min of a 30/min budget, every write behind them waiting): send
        ONE tournament-wide cancel-all instead and plan again next cycle, which re-places what should rest in
        batches. True = done that (send nothing else this cycle). Only when it's clearly cheaper: more pulls
        than pulls_cancel_all_over, or more than the writes left this minute AND than re-placing every quote
        would cost. Never while the self-test runs (its orders would vanish under it) or with ONLY_EXCHANGES.
        cancel_everything forgets our orders only once the cancel-all is confirmed (else orders_stale: re-read)."""
        cfg = self.cfg
        if not (self.api.live and cfg.pulls_cancel_all_over > 0) or cfg.only_exchanges or self.selftest_future:
            return False
        if self.burst:                            # burst mode re-quotes only the top markets: a cancel-all would
            return False                          # leave ~200 safe resting orders empty until the burst ends
        # (R3 ladder-only pulls don't count: 9 markets x 3 levels must not become a tournament-wide cancel-all)
        # (a ladder-only cancel-all of one exchange, whole=True, is a ladder-only pull too)
        pulls = 0
        for c in changes:
            if c.key[0] == 0 and c.doomed and not c.new:
                n0 = len([o for o in c.doomed if o.order_id not in c.ladder])
                pulls += 1 if c.whole and n0 else n0
        if not pulls:                             # (urgent reprices carry new orders: they are not pulls)
            return False
        left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        if not pulls > cfg.pulls_cancel_all_over:  # (only the count decides: a drained write budget would also
            return False                          # defer the re-placement, leaving the whole book empty)
        log.warning("%d pulls planned (%d writes left this minute) - one cancel-all instead; what should rest "
                    "is re-placed next cycle", pulls, left)
        try:
            if not self.cancel_everything():
                log.warning("cancel-all instead of pulls: not confirmed - re-reading our orders next cycle")
        except ApiError as e:                     # fall back to the pulls themselves: they reduce risk
            log.error("cancel-all instead of %d pulls failed (%s) - sending the pulls", pulls, e)
            return False
        return True

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
            w.future = self.writer.submit(self.place_orders, [o for o, _ in payload])
        def done(_f, w=w):
            w.done_at = time.monotonic()
            self.write_done.set()         # main loop: apply it soon
        w.future.add_done_callback(done)
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

    def new_order(self, ex, is_bid, price, size, fv, now, level=0):
        """One order for POST /orders/batch, plus notes about why we placed it (level: R3 ladder level, 0 = touch)."""
        cover = self.cover_no_qty(ex.eid, ex.inv, level) if is_bid else 0
        if cover >= 1:                    # Package 6: a covered NO sale, capped at the NO free to sell (the add waits)
            size = min(int(size), cover)
            self.__dict__.setdefault("cover_planned", {}).setdefault(ex.eid, {})[level] = size
        order = {"exchangeId": ex.eid, "side": "yes", "action": "buy" if is_bid else "sell",
                 "quantity": int(size), "price": round(price, 3), "tournamentId": self.tid,
                 # Dead-man's switch: the order dies on its own unless we keep refreshing it.
                 "expirationDate": iso(now + timedelta(seconds=self.order_ttl(ex, level)))}
        meta = {"our_side": "bid" if is_bid else "ask", "price": round(price, 3), "fv": fv, "t": time.time()}
        if cover >= 1:
            order["_no_sell"] = meta["no_sell"] = True   # sent as "sell NO @ 1-p" (wire_order)
        if not level and self.cfg.no_chase_enabled:   # no-chase: what we placed it at (inventory, size)
            meta["inv"], meta["qty"] = ex.inv, int(size)
        if level:
            meta["level"] = level
        return order, meta

    # --- Package 6: reduce NO holdings as covered NO sales (reduce_no_as_sell) ---
    def reduce_no_on(self):
        """reduce_no_as_sell in effect: the setting, and live with the self-test on, its own start-up leg passed
        (nosell_state "ok"; "off" = the exchange refused it: today's behaviour for the rest of this run)."""
        state = getattr(self, "nosell_state", None)
        if not self.cfg.reduce_no_as_sell or state == "off":
            return False
        if self.api.live and self.cfg.selftest_enabled:
            return state == "ok"
        return True

    def cover_no_qty(self, eid, inv, level=None, sets_ok=False):
        """NO shares a bid on eid may sell as a covered "sell NO" now: the NO held in that market (per market, -inv;
        never race-netted) less what our OTHER resting / just-planned bids there may already be selling (other ladder
        levels; the order a re-quote replaces is cancelled first, so the same level never counts). level None = a
        take, sent after every order of ours there was cancelled: all the NO held. 0 = not in effect / no NO held.
        Package 7 no_set_aware_bids: less the part locked in NO+NO sets (nono_set_part), unless sets_ok (a batch
        that sells NO on every leg of the race at once: arbitrage / short-set unwind legs)."""
        if not self.reduce_no_on() or inv is None or inv > -1:
            return 0
        held = -float(inv)
        if getattr(self.cfg, "no_set_aware_bids", False) and not sets_ok:
            held -= self.nono_set_part(eid, inv)
        if level is not None:
            held -= sum(o.qty for o in self.my_orders.values()
                        if o.eid == eid and o.is_bid and self.order_level(o) != level)
            held -= sum(q for lv, q in (getattr(self, "cover_planned", {}).get(eid) or {}).items() if lv != level)
        return max(0, int(held + 1e-9))

    def no_sell_order(self, order, inv, whole=False):
        """A take / arbitrage order (YES terms) that buys back a short: marked to go out as a covered "sell NO",
        capped at the NO held (whole=True: only if ALL of it fits, else unchanged - arbitrage legs must stay equal).
        Returns the order (changed in place), or None (Package 7 no_set_aware_bids, not whole): all the NO held
        there is in a NO+NO set, so the buy-back can be neither a covered sale nor (at 0 cash) a purchase - not sent."""
        if order["action"] != "buy":
            return order
        if not whole and self.set_blocked(order["exchangeId"], inv):
            return None
        cover = self.cover_no_qty(order["exchangeId"], inv, sets_ok=whole)
        if cover < 1 or (whole and order["quantity"] > cover):
            return order
        order["quantity"] = min(int(order["quantity"]), cover)
        order["_no_sell"] = True
        return order

    # --- Package 7: NO+NO sets ---
    def nono_set_part(self, eid, inv=None):
        """NO shares of eid locked in NO+NO sets, worst-case collateral model (the exchange collateralises a race's NO
        at sum - max): eid's NO held n_i less its LONE part, lone_i = max(0, n_i - max over the race's OTHER legs of
        their NO held), a leg not holding NO counting 0 - i.e. min(n_i, max over the other legs). 0 if eid holds < 1
        NO or is not in a 2+-leg race. inv = this leg's position if given, else ex.inv. For a 2-leg race this is the
        smaller leg's NO when both hold NO, as before; with 3+ legs NO on only some legs is capped too."""
        ex = self.ex.get(eid)
        members = self.groups.get(ex.group) if ex is not None else None
        if not members or len(members) < 2 or any(m not in self.ex for m in members):
            return 0.0
        n = -float(inv if inv is not None else ex.inv)
        if n < 1:
            return 0.0
        others = max(max(0.0, -float(self.ex[m].inv)) for m in members if m != eid)
        return min(n, others) if others >= 1 else 0.0

    def set_blocked(self, eid, inv, level=None):
        """no_set_aware_bids in effect and a bid / take buying back NO on eid has no lone part left to sell as a
        covered sale (all of its NO is in a NO+NO set): any order there would need cash, so none is sent."""
        return (getattr(self.cfg, "no_set_aware_bids", False) and self.reduce_no_on() and inv is not None
                and inv <= -1 and self.nono_set_part(eid, inv) >= 1 and self.cover_no_qty(eid, inv, level) < 1)

    def pair_no_unwind_on(self):
        """pair_no_unwind_max_cost in effect: set (>= 0), reduce_no_as_sell in effect, and live with the self-test on,
        its own start-up check (pairno_tick) passed; "off" = the exchange refused a paired NO sale this run."""
        if getattr(self.cfg, "pair_no_unwind_max_cost", -1.0) < 0 or not self.reduce_no_on():
            return False
        state = getattr(self, "pairno_state", None)
        if state == "off":
            return False
        if self.api.live and self.cfg.selftest_enabled:
            return state == "ok"
        return True

    def nono_sets(self):
        """status.json nono_sets: races held NO on every leg (2+ legs), the sets in them (min over legs of NO held)
        and the capital they lock at the set's guaranteed payout (k - 1 per set of a k-leg race)."""
        races, sets, cap = 0, 0, 0.0
        for race, members in self.groups.items():
            if len(members) < 2 or any(m not in self.ex for m in members):
                continue
            n = min(-self.ex[m].inv for m in members)
            if n >= 1:
                races, sets, cap = races + 1, sets + int(n), cap + int(n) * (len(members) - 1)
        return {"races": races, "sets": sets, "capital": round(cap, 2)}

    def place_orders(self, orders):
        """POST /orders/batch with each order in its wire form (wire_order): every placement goes through here."""
        return self.api.place_batch([wire_order(o) for o in orders])

    def order_ttl(self, ex, level=0):
        """Seconds a new order lives: order_ttl, or with ttl_tiers_enabled its market tier's (level 0 only; the
        ladder keeps order_ttl). Always <= MAX_ORDER_TTL when tiered."""
        cfg = self.cfg
        if level or not cfg.ttl_tiers_enabled:
            return cfg.order_ttl
        bank = self.bankroll()
        size = (self.size_plan.get(ex.eid, cfg.size_min_frac * bank) if cfg.size_by_activity
                else cfg.order_size_frac * bank)
        return order_ttl_for(cfg, ttl_tier(ex.group in cfg.headline_races, size, bank, cfg), random.random())

    def side_fix(self, ex, resting, price, size, limit, is_bid, max_size, fv, now, now_m=None):
        """plan_change's per-side test: side_needs_change, plus the write savers when on (no_chase_needs_change,
        ttl_expire_as_cancel). No-chase never applies where the risk logic must be followed exactly (reduce-only,
        flatten window, Polymarket just moved, a new fast-unload window), as hold_side."""
        cfg = self.cfg
        meta = (self.order_meta.get(resting[0].order_id) or {}) if len(resting) == 1 else {}
        if cfg.no_chase_enabled and meta:
            now_m = time.monotonic() if now_m is None else now_m
            if (self.global_reduce or self.hours_to_close(ex) <= cfg.flatten_hours_before_close
                    or ex.eid in self.ref_moved or now_m - ex.ref_moved_at < 15
                    or self.unload_urgent(ex, now_m) == ("bid" if is_bid else "ask")):
                meta = {}                         # (unknown placement notes -> the normal rules)
        return no_chase_needs_change(resting, price, size, cfg, now, limit, is_bid, max_size, fv, meta.get("fv"),
                                     ex.inv, meta.get("inv"), meta.get("qty"), expire_as_cancel=cfg.ttl_expire_as_cancel)

    def expiry_wait(self, ex, resting, is_bid, now):
        """ttl_expire_as_cancel: True while this side's order expired less than ttl_expire_grace_seconds ago (our
        clock may run ahead of the exchange's: re-quoting at once could leave two orders resting there)."""
        key = (ex.eid, is_bid)
        if resting:
            exp = [o.expires for o in resting if o.expires]
            if exp:
                self.last_expiry[key] = max(exp)
            return False
        last = self.last_expiry.get(key)
        if last is None:
            return False
        if now < last:                    # gone before its expiry (filled / cancelled): nothing to wait for
            del self.last_expiry[key]
            return False
        if (now - last).total_seconds() < self.cfg.ttl_expire_grace_seconds:
            return True
        del self.last_expiry[key]
        return False

    def sync_orders(self, raw_orders, now_m):
        """Reset our record of our resting orders (self.my_orders) from the API's open-orders list.

        Between these reads (every full check) the bot keeps the record itself: placements add to it,
        cancels and fills take from it. That saves re-reading the whole list (several requests) after
        every change we make. The list is a reporting projection that can lag the exchange by a moment,
        so for recent_order_grace_seconds our own record wins: an order we just placed stays even if the
        list doesn't show it yet (else we'd place it twice), and one we just cancelled stays gone."""
        grace = self.cfg.recent_order_grace_seconds
        listed = {r.order_id: r for r in map(parse_order, raw_orders) if r}
        self.adopt_unconfirmed(listed, now_m)
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

    def match_unconfirmed(self, eid, is_bid, price, oid, expires=None, lift=True, no_sell=None):
        """An order (or fill) we have no record of: is it one we sent whose response never came back? Matched on
        exchange, side and price (and expiry when known: every batch has its own). Adopts it: its notes go to
        order_meta (fills get attributed), and the exchange's 'outcome unknown' hold lifts once every order
        sent there is accounted for. Returns the order we sent, or None. no_sell True: only an order sent as a covered
        "sell NO" (Package 6; orders are kept in YES terms, so side and price compare as for any other)."""
        cands = self.unconfirmed.get(eid) or []
        for k, (o, meta, _t) in enumerate(cands):
            if (o["action"] == "buy") != is_bid or abs(o["price"] - price) > 1e-6:
                continue
            if no_sell is not None and bool(o.get("_no_sell")) != no_sell:
                continue
            if expires is not None and abs((parse_ts(o["expirationDate"]) - expires).total_seconds()) > 1.5:
                continue
            del cands[k]
            self.order_meta[oid] = {**meta, "eid": eid, "recovered": True}
            self.notes_dirty = True
            self.placed_qty[oid], self.filled_qty[oid] = float(o["quantity"]), self.filled_qty.get(oid, 0.0)
            if not cands:
                self.unconfirmed.pop(eid, None)
                ex = self.ex.get(eid)
                # All accounted for: quote here again straight away - but only lift the hold these sends set
                # (not one from a take, an arbitrage, a 502 or the self-test), and only when the order was seen
                # in the open-orders list (a fill alone doesn't say whether the rest of it still rests).
                if (lift and ex and ex.pending_until == ex.unconfirmed_hold and eid != self.selftest_eid
                        and not any(eid in w.eids for w in self.writes)):
                    ex.pending_until = 0.0
            log.info("recovered order %s on %s (its placement response was lost)", oid, eid)
            return o
        return None

    def adopt_unconfirmed(self, listed, now_m):
        """Open-orders list just read: adopt listed orders we have no record of (see match_unconfirmed), and
        forget sends older than pending_seconds (never landed, or filled at once - fills still match them)."""
        for eid in list(self.unconfirmed):
            self.unconfirmed[eid] = [c for c in self.unconfirmed[eid] if now_m - c[2] <= 2 * self.cfg.pending_seconds]
            if not self.unconfirmed[eid]:
                del self.unconfirmed[eid]
        if not self.unconfirmed:
            return
        for oid, r in listed.items():
            if oid not in self.order_meta and r.eid in self.unconfirmed:
                self.match_unconfirmed(r.eid, r.is_bid, r.price, oid, r.expires)

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
        w = self.unloads.get(str(order.get("exchangeId")))
        if w is not None and w["side"] == ("bid" if order.get("action") == "buy" else "ask"):
            w["placed"] = True                            # fast unload: later reprices follow normal churn rules
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
        self.orders_stale = True                  # confirm with a fresh read next cycle
        # Forget our orders only once the cancel is confirmed. Forgetting them first (as before) meant a cancel
        # that failed (409, timeout) left every order resting while the bot believed - and for
        # recent_order_grace_seconds even hid from the open-orders list - that they were gone: the next cycle
        # quoted every market a second time.
        gone = list(self.my_orders)
        if not self.cfg.only_exchanges:
            ok = self.api.cancel_all(self.tid)
        else:
            ok = all([self.api.cancel_all(self.tid, eid) for eid in self.ex])   # list: try every one
        if ok:
            self.forget_orders(gone)
        return ok

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
                results = self.place_orders([o for o, _ in chunk])
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
            for o, meta in chunk:
                ex = self.ex.get(o["exchangeId"])
                if ex:
                    if ambiguous:
                        ex.pending_until = ex.unconfirmed_hold = now_m + cfg.pending_seconds
                        if cfg.recover_unconfirmed:
                            self.unconfirmed.setdefault(ex.eid, []).append((o, meta, now_m))
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
                    if meta.get("level"):
                        self.lad_placed[oid] = now_m      # R3 ladder: min_quote_life (monotonic, like level 0)
                    self.notes_dirty = True
                self.remember_order(order, data, now_m)       # our record of resting orders
                if data.get("quantityTraded"):
                    log.info("order on %s traded %s immediately", order["exchangeId"], data["quantityTraded"])
                    self.orders_stale = True          # positions changed: re-read them next cycle
                continue
            err = data.get("error") or {}
            if not isinstance(err, dict):             # plain-text error: {"error": "Not found"}
                err = {"message": str(err)}
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
        """Guaranteed profit inside a race, and turning held complete sets back into cash.

        A race's group holds every listed party's YES (one exchange each, grouped by race title), and at
        most one of them wins. Checked in this order, at most one action per race per cycle:

        1. Pair unwind (pair_unwind_enabled): long YES on EVERY leg (n = min over legs) and other traders'
           bids add up to >= 1 + pair_unwind_min_profit -> sell up to n sets at those bids. The set pays
           exactly n whatever happens, so selling for >= n is a riskless gain plus freed capital. Mirror: short
           every leg, asks add up to <= 1 - pair_unwind_min_profit -> buy back. Positions only shrink, so
           this also runs in reduce-only and in the pre-close window. A race with an independent leg is a
           set only if that leg is held too (the min is over ALL legs), so a Dem+Rep pair there is left alone.
        2. Sell-side arbitrage (arb_enabled): bids add up to >= 1 + arb_min_profit -> sell YES on all.
           At most one party wins, so for each set sold we pay out at most 1 but collected the bids (> 1).
        3. Buy-side arbitrage (arb_two_sided): asks add up to <= 1 - arb_min_profit_buy -> buy YES on all.
           Pays 1 per set if one LISTED party wins, so not when the asks add up to less than arb_buy_min_sum
           (the market then likely prices an outsider), not in reduce-only (it adds gross positions) and not
           in the pre-close window. The unwinder sells the set later when the bids reach 1.
        Prices are other traders' only (books are stripped of our orders; our quotes there are pulled first).
        Returns the set of races acted on, whose quoting is skipped until next cycle.
        """
        cfg = self.cfg
        done = set()
        if not (cfg.arb_enabled or cfg.pair_unwind_enabled):
            return done
        b_left = int(getattr(cfg, "pair_no_unwind_max_per_cycle", 2))   # Package 7: B-only short-set unwinds
        b_waiting = 0
        for race, members in self.groups.items():
            if (len(members) < 2 or not self.running or now_m < self.arb_cooldown.get(race, 0)
                    or race in getattr(self, "pair_owed", ())  # Package 7: its owed legs are evened up first
                    or any(busy(self.ex[e], now_m) for e in members)):
                continue
            # pre-close window: arbitrage would open positions the per-market flatten then pays to unwind;
            # unwinding a held set only reduces them, so it still runs
            closing = any(self.hours_to_close(self.ex[e]) <= cfg.flatten_hours_before_close for e in members)
            if self.arb_plan(members, inv, fvs, closing) is None:     # quick check on the cached books
                continue
            if getattr(self, "arb_plan_b", False) and b_left < 1:     # Package 7: B's per-cycle cap: waits
                b_waiting += 1
                continue
            # Write budget: cancel our quotes on each leg, one batch, cancel the leftovers on each leg.
            if getattr(self.api, "writes_left", lambda: 10 ** 6)() < 2 * len(members) + 1:
                log.info("arbitrage/unwind on %s deferred: write budget used up", race)
                continue
            # Cached books can be up to book_max_age old: re-download this race before acting on it.
            books = self.in_parallel(lambda e: self.api.book(e, self.tid), members)
            if any(isinstance(b, Exception) for b in books.values()):
                continue
            for e, b in books.items():
                self.ex[e].book = strip_own(b, mine_real.get(e, []))
                self.ex[e].book_time = self.ex[e].verified = time.monotonic()
            plan = self.arb_plan(members, inv, fvs, closing)
            if plan is None:
                continue
            if getattr(self, "arb_plan_b", False):
                if b_left < 1:
                    b_waiting += 1
                    continue
                b_left -= 1
            kind, action, levels, qty = plan
            self.arb_cooldown[race] = now_m + (cfg.pair_unwind_cooldown_seconds if kind == "unwind"
                                               else cfg.arb_cooldown_seconds)
            if qty >= 1:
                traded = self.execute_arbitrage(race, members, levels, qty, fvs, now_m, action=action, kind=kind)
                if traded is not None and not any(traded):      # nothing filled: the prices were gone. 4x cooldown
                    self.arb_cooldown[race] = now_m + 4 * (cfg.pair_unwind_cooldown_seconds if kind == "unwind"
                                                           else cfg.arb_cooldown_seconds)
                done.add(race)
        if b_waiting:
            log.info("short-set pair unwinds at a cost (pair_no_unwind_max_cost): %d more race(s) wait for the next "
                     "cycles (pair_no_unwind_max_per_cycle %d)", b_waiting, cfg.pair_no_unwind_max_per_cycle)
        return done

    def top_levels(self, members, key):
        """{eid: (price, size)} of the best OTHER-trader level ("bids"/"asks") on every leg, or None if a leg has none."""
        out = {}
        for e in members:
            b = self.ex[e].book
            if not b or not b.get(key):
                return None
            out[e] = (b[key][0]["price"], b[key][0]["quantity"])
        return out

    def arb_bids(self, members):
        """{eid: (price, size)} of the best OTHER-trader bid on each party of a race, if those bids
        add up to at least 1 + arb_min_profit. Otherwise None."""
        bids = self.top_levels(members, "bids")
        return bids if bids and sum(p for p, _ in bids.values()) >= 1 + self.cfg.arb_min_profit - 1e-9 else None

    def arb_plan(self, members, inv, fvs, closing):
        """What take_arbitrage would do in one race on the current books: (kind, action, {eid: (price, size)},
        sets) with kind "unwind" or "arb" and action "sell"/"buy", or None."""
        cfg = self.cfg
        bank = self.bankroll()
        bids, asks = self.top_levels(members, "bids"), self.top_levels(members, "asks")
        self.arb_plan_b = False           # Package 7: True = the plan is a short-set unwind only B's threshold allows
        if cfg.pair_unwind_enabled:
            held = [inv.get(e, 0.0) for e in members]
            for sign, levels, action in ((+1, bids, "sell"), (-1, asks, "buy")):
                sets = min(sign * h for h in held)
                if sets < 1 or not levels:
                    continue
                total = sum(p for p, _ in levels.values())
                edge = total - 1 if sign > 0 else 1 - total
                floor = cfg.pair_unwind_min_profit
                if sign < 0 and self.pair_no_unwind_on():
                    # Package 7: a NO+NO set is unwound as a pair even at a small cost (asks <= 1 + max_cost): the
                    # only way to free it without cash (selling one leg breaks the set's collateral)
                    floor = min(floor, -cfg.pair_no_unwind_max_cost)
                if edge < floor - 1e-9:   # (buying back a short set below 0.90 only cuts risk)
                    continue
                qty = int(min([sets] + [size for _, size in levels.values()] +
                              [cfg.pair_unwind_max_frac * bank / max(p, TICK) for p, _ in levels.values()]))
                if qty >= 1 and self.unwind_is_safe(inv, fvs, members, -sign * qty):
                    self.arb_plan_b = edge < cfg.pair_unwind_min_profit - 1e-9
                    return "unwind", action, levels, qty
        if not cfg.arb_enabled or closing:
            return None
        if bids and sum(p for p, _ in bids.values()) >= 1 + cfg.arb_min_profit - 1e-9:
            qty = int(min([cfg.arb_max_frac * bank] +
                          [size for _, size in bids.values()] +                            # only what's bid at that price
                          [cfg.max_position_frac * bank + inv.get(e, 0.0) for e in members] +   # selling lowers position
                          [cfg.max_order_cash_frac * bank / max(1 - p, TICK) for p, _ in bids.values()]))  # cash per order
            return "arb", "sell", bids, qty
        # The set pays 1 only if a LISTED party wins. Book fair values are normalised to sum to 1, so they cannot
        # see an unlisted outsider: every leg needs a LIQUID Polymarket price and those RAW prices must add up to
        # at least arb_buy_min_ref_sum (owner, 2 Oct 11:25; 110 of 113 races list Dem + Rep only).
        refs_ok = (all(e in self.cur_liquid and self.cur_refs.get(e) is not None for e in members)
                   and sum(self.cur_refs[e] for e in members) >= cfg.arb_buy_min_ref_sum - 1e-9)
        if (cfg.arb_two_sided and asks and not self.global_reduce and refs_ok
                and cfg.arb_buy_min_sum - 1e-9 <= sum(p for p, _ in asks.values()) <= 1 - cfg.arb_min_profit_buy + 1e-9):
            qty = int(min([cfg.arb_max_frac * bank] +
                          [size for _, size in asks.values()] +                            # only what's offered there
                          [cfg.max_position_frac * bank - inv.get(e, 0.0) for e in members] +   # buying raises position
                          [cfg.max_order_cash_frac * bank / max(p, TICK) for p, _ in asks.values()]))  # cash per order
            return "arb", "buy", asks, qty
        return None

    def unwind_is_safe(self, inv, fvs, members, delta):
        """Would adding `delta` YES shares on every leg of a race leave the party delta within its cap (or no
        further past it) and the settlement risk and the worst case no higher? A complete set is riskless,
        so this holds by construction; it guards against the set being part of a hedge the risk logic relies on."""
        after = dict(inv)
        for e in members:
            after[e] = after.get(e, 0.0) + delta

        def pdelta(pos):
            return sum(PARTY_SIGN.get(ex.party, 0) * pos.get(eid, 0.0) for eid, ex in self.ex.items())

        before_pd, after_pd = pdelta(inv), pdelta(after)
        cap = self.cfg.max_party_delta_frac * self.bankroll()
        if abs(after_pd) > cap and abs(after_pd) > abs(before_pd) + 1e-6:
            return False
        if self.total_worst_case(after, fvs) > self.total_worst_case(inv, fvs) + 1e-6:
            return False
        return self.settlement_risk(after, fvs, after_pd) <= self.settlement_risk(inv, fvs, before_pd) + 1e-6

    # ------------------------------------------------------------------------------ T2.5 passive pair unwind
    def pp_candidate(self, x, y, sign, sets, bank):
        """Passive pair unwind, leg x resting and leg y taken on a fill: (sort key, price, slice) or None.
        Long set (sign +1): ask x at max(join the best other ask, 1 - best other bid of y - pair_unwind_max_cost),
        never at/through the best other bid of x (a take, not a quote). Short set (-1): the mirror, a bid on x at
        min(join the best other bid, 1 + max_cost - best other ask of y). Slice = min(sets, the size on y's top level,
        pair_unwind_max_frac of the account in cash at that price). Key: how far behind the touch it sits, then
        what the set fetches (long: most) or costs (short: least)."""
        cfg = self.cfg
        bx, by = self.ex[x].book or {}, self.ex[y].book or {}
        side_y, side_x, opp_x = ("bids", "asks", "bids") if sign > 0 else ("asks", "bids", "asks")
        if not by.get(side_y):
            return None
        py, sy = by[side_y][0]["price"], by[side_y][0]["quantity"]
        touch = bx[side_x][0]["price"] if bx.get(side_x) else None
        opp = bx[opp_x][0]["price"] if bx.get(opp_x) else None
        if sign > 0:
            price = ceil_tick(1 - py - cfg.pair_unwind_max_cost)
            price = max(price, touch) if touch is not None else price
            if opp is not None:
                price = max(price, ceil_tick(opp + TICK))
            gap = price - touch if touch is not None else 0.0
        else:
            price = floor_tick(1 + cfg.pair_unwind_max_cost - py)
            price = min(price, touch) if touch is not None else price
            if opp is not None:
                price = min(price, floor_tick(opp - TICK))
            gap = touch - price if touch is not None else 0.0
        if not PMIN - 1e-9 <= price <= PMAX + 1e-9:
            return None
        qty = int(min(sets, sy, cfg.pair_unwind_max_frac * bank / max(price, TICK)))
        if qty < 1:
            return None
        return (round(gap, 6), round(-sign * (price + py), 6)), price, qty

    def reduce_no_effective(self):
        """reduce_no_as_sell as the bot runs it: reduce_no_on() (setting + start-up check) where the bot has it, else
        the setting (Package 7 D keys on this, so a run whose sell-NO check failed keeps T2.5's short sets)."""
        fn = getattr(self, "reduce_no_on", None)
        return bool(fn()) if callable(fn) else bool(self.cfg.reduce_no_as_sell)

    def pair_passive_plan(self, members, inv, fvs, prefer=None):
        """A new passive slice for a 2-leg race holding a complete set (long or short both legs), or None.
        prefer = the leg that rested last: kept on a tie of how far behind the touch (no needless leg swaps)."""
        if len(members) != 2 or any(e not in self.ex for e in members):
            return None
        a, b = members
        ha, hb = inv.get(a, 0.0), inv.get(b, 0.0)
        sign = 1 if min(ha, hb) >= 1 else -1 if max(ha, hb) <= -1 else 0
        if not sign:
            return None
        if sign < 0 and self.reduce_no_effective():
            # Package 7: with reduce_no_as_sell (in effect: reduce_no_on) the resting bid is a covered "sell NO" on ONE leg of a NO+NO set,
            # which breaks the set's collateral and is refused at 0 cash: short sets are unwound as a pair
            # (arb_plan, pair_no_unwind_max_cost), never passively. Long sets (YES+YES) as before.
            if not getattr(self, "pp_short_skip_logged", False):
                self.pp_short_skip_logged = True
                log.info("T2.5 passive pair unwind: short (NO+NO) sets skipped while reduce_no_as_sell is on")
            return None
        sets, bank = min(sign * ha, sign * hb), self.bankroll()
        cands = [(c, x, y) for x, y in ((a, b), (b, a)) if (c := self.pp_candidate(x, y, sign, sets, bank))]
        if not cands:
            return None
        (_, price, qty), x, y = min(cands, key=lambda c: (c[0][0][0], c[1] != prefer, c[0][0][1], c[1]))
        if not self.unwind_is_safe(inv, fvs, members, -sign * qty):
            return None
        return {"leg": x, "other": y, "sign": sign, "base_x": inv.get(x, 0.0), "base_y": inv.get(y, 0.0),
                "slice": qty, "left": qty, "price": price}

    def pair_passive_step(self, inv, fvs, now_m, skip=(), mine_real=None, execute=None):
        """T2.5 (pair_unwind_passive), once a cycle before quoting. Per 2-leg race at most one slice is open:
          - its resting leg sold (bought back) s shares since the slice began and the other leg has not followed:
            sell (buy back) the difference on the other leg at its best price NOW - one take, cooldowns bypassed,
            only the write budget can defer it (next cycle, still urgent). Unmatched is never more than one slice.
          - part filled: the rest keeps resting, re-priced on the current books;
          - done (the whole slice matched) or nothing filled: a new slice is planned on the current books.
        Fills are seen as position changes since the slice began (a fill of our ordinary bid on that leg in the
        same cycle can hide one: the set is then still whole, nothing is unmatched). Returns the races traded.
        execute(eid, buy, qty, price) -> shares done replaces the exchange take (simulator)."""
        cfg, acted = self.cfg, set()
        off = not cfg.pair_unwind_passive         # switched off live: only owed second legs are finished
        for race, members in self.groups.items():
            if len(members) != 2 or race in skip or not self.running:
                continue
            st = self.pp.get(race)
            if off and st is None:
                continue
            if st is not None:
                x, y, s = st["leg"], st["other"], st["sign"]
                sold_x = max(0.0, s * (st["base_x"] - inv.get(x, 0.0)))
                # (our own takes count even before a positions read shows them: never sold twice)
                sold_y = max(0.0, s * (st["base_y"] - inv.get(y, 0.0)), st.get("taken", 0.0))
                if off:                           # what the slice had sold when switched off: later fills of the
                    st.setdefault("off_cap", sold_x)  # normal ask on this leg are not the slice's
                owed = int(round(min(sold_x, st["slice"], st.get("off_cap", sold_x)) - sold_y))
                if owed >= 1:
                    acted.add(race)
                    st["left"] = max(0, int(st["slice"] - sold_x))
                    st["taken"] = sold_y + self.pair_passive_take(race, st, owed, fvs, now_m, mine_real, execute)
                    continue
                if off:                                           # nothing owed: the slice state is dropped
                    del self.pp[race]
                    continue
                if "off_cap" not in st and 0.5 <= sold_x < st["slice"] - 0.5:   # part filled and matched:
                    c = self.pp_candidate(x, y, s, st["slice"] - sold_x, self.bankroll())   # the rest keeps resting
                    st["left"] = int(st["slice"] - sold_x) if c else 0
                    if c:
                        st["price"] = c[1]
                    continue
                if sold_x >= st["slice"] - 0.5 or "off_cap" in st:   # (back on after a hot off: a fresh slice)
                    self.pp_sets_total += int(round(min(sold_x, sold_y)))
                    log.warning("PAIR UNWIND (passive) %s: slice of %d sets closed", race, st["slice"])
                prefer = st["leg"]
                del self.pp[race]
            else:
                prefer = None
            closing = any(self.hours_to_close(self.ex[e]) <= cfg.flatten_hours_before_close
                          for e in members if e in self.ex)
            plan = None if closing else self.pair_passive_plan(members, inv, fvs, prefer)
            if plan is not None:
                self.pp[race] = plan
        return acted

    def pair_passive_take(self, race, st, qty, fvs, now_m, mine_real=None, execute=None):
        """The urgent second leg: sell (long set) / buy back (short set) qty on the other leg at its best price."""
        y, buy = st["other"], st["sign"] < 0
        ex = self.ex[y]
        if execute is None and self.api.live:
            if not self.writes_ready(3):          # cancel + take + leftover cancel: deferred, still urgent
                self.arbs_skipped_budget += 1
                log.info("pair unwind second leg on %s deferred: write budget busy (next cycle)", race)
                return 0.0
            try:                                  # the cached book may be old
                ex.book = strip_own(self.api.book(y, self.tid), (mine_real or {}).get(y, []))
                ex.book_time = ex.verified = time.monotonic()
            except ApiError as e:
                log.warning("pair unwind second leg on %s: book download failed (%s) - cached book", race, e)
        side = (ex.book or {}).get("asks" if buy else "bids") or []
        if not side:
            log.warning("pair unwind second leg on %s: no %s on %s - retrying next cycle", race,
                        "ask" if buy else "bid", ex.label)
            return 0.0
        price = side[0]["price"]
        # Floor (long set) / ceiling (short set): the set closes for >= 1 - max_cost - 0.5c, or the owed leg waits
        px, slack = st["price"], self.cfg.pair_unwind_max_cost + 0.005
        if (price < 1 - px - slack - 1e-9) if not buy else (price > 1 + slack - px + 1e-9):
            log.warning("pair unwind second leg on %s deferred: best %s %.3f on %s is past the %s %.3f (retrying)",
                        race, "ask" if buy else "bid", price, ex.label, "ceiling" if buy else "floor",
                        (1 + slack - px) if buy else (1 - px - slack))
            if not st.get("floor_alerted"):
                st["floor_alerted"] = True
                alert(f"pair unwind {race}: {qty} of {ex.label} owed but its best {'ask' if buy else 'bid'} "
                      f"{price:.3f} is past the {'ceiling' if buy else 'floor'} - waiting (unmatched leg held)")
            return 0.0
        log.warning("%sPAIR UNWIND (passive) %s: %s filled -> %s %d YES on %s at %.3f", "" if self.api.live or execute
                    else "[dry] ", race, self.ex[st["leg"]].label, "buying" if buy else "selling", qty, ex.label, price)
        if execute is not None:
            return execute(y, buy, qty, price)
        if not self.api.live:
            return 0.0
        if not self.cancel(y, [], whole_exchange=True):   # our own quotes there first: never trade with ourselves
            return 0.0
        order = self.no_sell_order({"exchangeId": y, "side": "yes", "action": "buy" if buy else "sell",
                                    "quantity": int(qty), "price": price, "tournamentId": self.tid,
                                    "expirationDate": iso(utcnow() + timedelta(seconds=self.cfg.arb_order_ttl))}, ex.inv)
        self.orders_stale = True
        if order is None:                         # Package 7: all NO there is in a NO+NO set (no_set_aware_bids)
            log.warning("pair unwind second leg on %s not sent: the NO on %s is all in a NO+NO set", race, ex.label)
            return 0.0
        try:
            results = self.place_orders([order])
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT":
                self.arbs_skipped_budget += 1
                return 0.0
            ex.pending_until = now_m + self.cfg.pending_seconds
            alert(f"pair unwind second leg on {race} failed ({e}) - check positions")
            if e.code in FATAL_API_CODES:
                fatal(f"orders rejected with {e.code}")
            return 0.0
        res = results[0] if results else {}
        data = res.get("data") or {}
        if res.get("ok"):
            self.remember_order(order, data, now_m)
        self.cancel(y, [], whole_exchange=True, quiet=True)
        if data.get("orderId") is not None:
            self.order_meta[data["orderId"]] = {"our_side": "bid" if buy else "ask", "price": price, "arb": True,
                                                "fv": fvs.get(y), "t": time.time(), "eid": y,
                                                **({"no_sell": True} if order.get("_no_sell") else {})}
            self.notes_dirty = True
        return float(data.get("quantityTraded") or 0)

    def pair_passive_quote(self, ex, q):
        """The passive slice replaces the resting leg's ask (long set) or bid (short set) in the normal quote:
        exactly that price (a resting order of ours below/above it is replaced), the slice's open size, and our
        own other side kept at least a tick away. A leg the decision left unquoted (no fair value, stop before
        close...) stays unquoted, and so does a slice side decide() blocked (long set: no ask; short set: no bid),
        except, with pair_passive_in_reduce_only, a side emptied ONLY by the reduce-only race-net clip (ex.ro_clip:
        a set leg's race-netted position is ~0, but a pair unwind lowers the risk). Applied to the quote decide()
        returned, before reconciling."""
        st = next((v for v in self.pp.values() if v["leg"] == ex.eid), None)
        if st is None or not self.cfg.pair_unwind_passive:
            return q
        if st["sign"] < 0 and self.reduce_no_effective():
            return q                              # Package 7: a short-set slice never rests (see pair_passive_plan)
        side = "ask" if st["sign"] > 0 else "bid"
        if getattr(q, side) is None and not (self.cfg.pair_passive_in_reduce_only
                                             and side in getattr(ex, "ro_clip", "").split()):
            return q                              # decide() blocked the slice's side: it stays blocked
        left, price = int(st.get("left", st["slice"])), st["price"]
        if st["sign"] > 0:
            if left < 1:
                return replace(q, ask=None, ask_size=0, ask_limit=None, ask_max=None)
            bid, bid_size, bid_limit = q.bid, q.bid_size, q.bid_limit
            if bid_limit is not None:             # an older resting bid at/above the slice ask is never kept
                bid_limit = min(bid_limit, floor_tick(price - TICK))
            if bid is not None and bid >= price - 1e-9:
                bid = floor_tick(price - TICK)
                bid_limit = min(bid_limit, bid) if bid_limit is not None else bid
                if bid < PMIN - 1e-9 or bid >= price - 1e-9:
                    bid, bid_size, bid_limit = None, 0, None
            return replace(q, bid=bid, bid_size=bid_size if bid is not None else 0, bid_limit=bid_limit,
                           ask=price, ask_size=left, ask_limit=price, ask_max=left)
        if left < 1:
            return replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        ask, ask_size, ask_limit = q.ask, q.ask_size, q.ask_limit
        if ask_limit is not None:                 # an older resting ask at/below the slice bid is never kept
            ask_limit = max(ask_limit, ceil_tick(price + TICK))
        if ask is not None and ask <= price + 1e-9:
            ask = ceil_tick(price + TICK)
            ask_limit = max(ask_limit, ask) if ask_limit is not None else ask
            if ask > PMAX + 1e-9 or ask <= price + 1e-9:
                ask, ask_size, ask_limit = None, 0, None
        return replace(q, ask=ask, ask_size=ask_size if ask is not None else 0, ask_limit=ask_limit,
                       bid=price, bid_size=left, bid_limit=price, bid_max=left)

    def writes_ready(self, n):
        """Main thread: True if n writes can go now without waiting for the write budget or a 429 pause."""
        if not self.api.live:
            return True
        wait_s = getattr(self.api, "write_wait", lambda: 0.0)()
        left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        return wait_s <= self.cfg.write_wait_seconds and left >= n

    def execute_arbitrage(self, race, members, levels, qty, fvs, now_m, action="sell", kind="arb"):
        cfg = self.cfg
        # Writes: a cancel per leg, the batch, a leftover cancel per leg = 2n + 1.
        if not self.writes_ready(2 * len(members) + 1):
            self.arbs_skipped_budget += 1
            log.info("%s on %s skipped: write budget busy (next cycle)", "pair unwind" if kind == "unwind"
                     else "arbitrage", race)
            return
        followup = kind == "unwind" and getattr(cfg, "pair_unwind_followup", False)
        if followup:                              # Package 7: every leg sized to what can fill together
            joint = self.joint_unwind_qty(members, levels, qty, action)
            if joint < 1:
                log.info("pair unwind on %s not sent: the legs' depth at the planned prices does not fill one set "
                         "together", race)
                return
            if joint < qty:
                log.info("pair unwind on %s: sized %d -> %d sets (joint depth at the planned prices)", race, qty, joint)
            qty = joint
        # The legs (the batch), built first: a short-set unwind whose legs cannot ALL go out as covered "sell NO"
        # is not sent at all (Package 7: a half-converted batch breaks the NO+NO set and needs cash). The expiry is
        # set after our quotes are pulled (below).
        orders = [{"exchangeId": e, "side": "yes", "action": action, "quantity": qty, "price": p,
                   "tournamentId": self.tid} for e, (p, _) in levels.items()]
        for o in orders:                          # a leg buying back a short: covered "sell NO" if ALL of it fits
            self.no_sell_order(o, self.ex[o["exchangeId"]].inv, whole=True)
        if (kind == "unwind" and action == "buy" and self.reduce_no_on()
                and (getattr(cfg, "no_set_aware_bids", False) or getattr(cfg, "pair_no_unwind_max_cost", -1.0) >= 0)
                and not all(o.get("_no_sell") for o in orders)):
            self.pair_no_refused = getattr(self, "pair_no_refused", 0) + 1
            log.warning("pair unwind on %s (short set) not sent: not every leg fits as a covered 'sell NO' (%s)",
                        race, ", ".join(f"{self.ex[o['exchangeId']].label} x{o['quantity']} NO held "
                                        f"{-self.ex[o['exchangeId']].inv:.0f}" for o in orders))
            return
        total = sum(p for p, _ in levels.values())
        per_set = total - 1 if action == "sell" else 1 - total
        legs = ", ".join(f"{self.ex[e].label} @{p:.3f} x{s:.0f}" for e, (p, s) in levels.items())
        what = (("PAIR UNWIND", "long set" if action == "sell" else "short set") if kind == "unwind"
                else ("ARBITRAGE", "sell side" if action == "sell" else "buy side"))
        log.warning("%s%s %s (%s): %s add up to %.3f (%s) -> %s %d YES on each, %+.4f per set, locking in %+.2f",
                    "" if self.api.live else "[dry] ", what[0], race, what[1], "bids" if action == "sell" else "asks",
                    total, legs, "selling" if action == "sell" else "buying", qty, per_set, per_set * qty)
        if kind == "unwind":
            self.unwinds_total += 1
        else:
            self.arbs_total += 1
        if not self.api.live:
            return
        # 1. Pull our own quotes in this race, so the arbitrage can't trade against ourselves.
        if not all([self.cancel(e, [], whole_exchange=True) for e in members]):
            log.warning("arbitrage on %s abandoned: could not clear our own quotes", race)
            return
        # 2. Trade at exactly those prices. The orders expire within seconds so leftovers can't rest.
        exp = iso(utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        for o in orders:
            o["expirationDate"] = exp
        self.orders_stale = True                  # positions and orders change: re-read next cycle
        try:
            results = self.place_orders(orders)
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT":             # never sent: nothing can have traded, no hold, no alert
                self.arbs_skipped_budget += 1
                log.warning("%s on %s not sent: write budget busy (our quotes there are re-placed next cycle)",
                            what[0].lower(), race)
                return
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
                self.order_meta[data["orderId"]] = {"our_side": "ask" if action == "sell" else "bid",
                                                    "price": o["price"], "arb": True,
                                                    "fv": fvs.get(o["exchangeId"]), "t": time.time(),
                                                    "eid": o["exchangeId"],
                                                    **({"no_sell": True} if o.get("_no_sell") else {})}
                self.notes_dirty = True
        if len(set(traded)) > 1 and followup:     # Package 7: the lagging leg(s) owe the difference
            self.pair_owe(race, orders, traded, action, now_m)
        elif len(set(traded)) > 1:
            alert(f"arbitrage on {race} only partly filled {traded}: the difference is now ordinary "
                  f"inventory, which the quoting will work off")
        else:
            log.info("arbitrage on %s: every leg filled %.0f", race, traded[0] if traded else 0)
        return traded

    # --- Package 7: pair unwind follow-up (pair_unwind_followup) ---
    @staticmethod
    def depth_within(book, key, limit):
        """Shares on one side of a book ("bids"/"asks", several levels) at or better than limit: asks at <= limit,
        bids at >= limit. 0 without a book."""
        out = 0.0
        for lv in (book or {}).get(key) or []:
            p = lv["price"]
            if (p <= limit + 1e-9) if key == "asks" else (p >= limit - 1e-9):
                out += lv["quantity"]
        return out

    def joint_unwind_qty(self, members, levels, qty, action):
        """A pair unwind's sets sized to what can fill on every leg together: the least of the planned sets, each
        leg's cached book depth at or better than its planned price, and the smaller leg's position (YES held for a
        long-set sale, NO held for a short-set buy-back)."""
        key, sign = ("bids", 1) if action == "sell" else ("asks", -1)
        sizes = [qty]
        for e in members:
            ex = self.ex[e]
            sizes.append(self.depth_within(ex.book, key, levels[e][0]))
            sizes.append(sign * ex.inv)
        return max(0, int(min(sizes) + 1e-9))

    def pair_owe(self, race, orders, traded, action, now_m):
        """After a pair unwind batch whose legs filled unequally: the lagging leg(s) owe (most filled - their fill),
        at their planned price. Evened up by pair_followup_step on the next cycles.
        Short-set buy-back (action "buy"): each lagging leg's owed is capped at its LONE part AFTER the fills (its NO
        held then less the most NO held then on any other leg of the race; a leg without NO counts 0), and a leg whose
        lone part is < 1 is dropped: the rest of its NO is still in a NO+NO set with the others, so a covered sale
        there would break the set (refused at 0 cash) - nothing is owed. ex.inv is still the pre-batch position here
        (positions are re-read next cycle), so the fills are added to it."""
        top = max(traded)
        legs = {o["exchangeId"]: int(round(top - f)) for o, f in zip(orders, traded) if top - f >= 1 - 1e-9}
        if action == "buy" and legs:
            fill = {o["exchangeId"]: f for o, f in zip(orders, traded)}
            members = [m for m in (self.groups.get(self.ex[next(iter(legs))].group) or fill) if m in self.ex]
            no_after = {m: max(0.0, -(self.ex[m].inv + fill.get(m, 0.0))) for m in members}
            capped = {}
            for e, q in legs.items():
                lone = no_after.get(e, 0.0) - max([no_after[m] for m in members if m != e] or [0.0])
                q = int(min(q, lone + 1e-9))
                if q >= 1:
                    capped[e] = q
                else:
                    log.info("pair unwind on %s: %s owes nothing (NO after the fills %.0f, none of it lone: still in a "
                             "NO+NO set)", race, self.ex[e].label, no_after.get(e, 0.0))
            legs = capped
            if not legs:
                log.warning("pair unwind on %s filled %s unequally: nothing owed (no lagging leg has a lone NO part "
                            "after the fills; the rest is still held as NO+NO sets)", race, traded)
                return
        self.pair_owed[race] = {"action": action, "legs": legs, "t": now_m, "tries": 0,
                                "price": {o["exchangeId"]: o["price"] for o in orders}}
        log.warning("pair unwind on %s filled %s unequally: %s owed (follow-up up to %d cycles, at most %.3f a share "
                    "past the planned price; in memory only, a restart drops it)", race, traded,
                    ", ".join(f"{self.ex[e].label} {q}" for e, q in legs.items()),
                    self.cfg.pair_unwind_followup_tries, self.cfg.pair_unwind_followup_max_cost)

    def pair_owed_status(self):
        """status.json pair_owed: {race: shares still owed (summed over its lagging legs)}."""
        return {r: int(sum(st["legs"].values())) for r, st in getattr(self, "pair_owed", {}).items()}

    def pair_followup_step(self, fvs, now_m, mine_real=None):
        """Once a cycle, before take_arbitrage: per race with owed legs, one immediate-or-cancel order on each lagging
        leg (pair_followup_take). The owed shares shrink by what fills; the state is dropped once even, or after
        pair_unwind_followup_tries cycles that sent (or tried to send) the follow-up, with one alert of what is left.
        A cycle the write budget defers is not a try. Returns the races acted on (not quoted this cycle)."""
        cfg, acted = self.cfg, set()
        for race in list(self.pair_owed):
            st = self.pair_owed[race]
            for e in [e for e in st["legs"] if e not in self.ex]:   # a leg gone from the market list: dropped
                log.warning("pair unwind follow-up on %s: %s owed on a market no longer listed - dropped", race,
                            st["legs"].pop(e))
            lag = [e for e, q in st["legs"].items() if q >= 1]
            if not lag:
                del self.pair_owed[race]
                continue
            if not self.running or not self.api.live:
                continue
            if not self.writes_ready(2 * len(lag) + 1):   # pull our quotes + the orders + leftover cancels
                self.arbs_skipped_budget += 1
                log.info("pair unwind follow-up on %s deferred: write budget busy (next cycle)", race)
                continue
            st["tries"] += 1
            st["sent"], st["cleared"] = False, []
            got = self.pair_followup_take(race, st, lag, fvs, now_m, mine_real)
            sent, cleared = st.pop("sent", False), st.pop("cleared", [])
            if sent:                              # writes went out: the race is not quoted this cycle
                acted.add(race)
            if got is None:                       # never sent (write budget): not a try
                st["tries"] -= 1
                continue
            if cleared and not sent and len(cleared) == len(lag):
                st["tries"] -= 1                  # every lagging leg cleared (all its NO in a set): not a try
            for e, g in got.items():
                st["legs"][e] = max(0, int(round(st["legs"][e] - g)))
            left = sum(st["legs"].values())
            if left < 1 and cleared and not got:
                log.warning("pair unwind follow-up on %s: nothing left that can be sent (the lagging legs' NO is in "
                            "NO+NO sets) - owed state cleared", race)
                del self.pair_owed[race]
            elif left < 1:
                log.warning("pair unwind on %s: follow-up evened the legs after %d %s", race, st["tries"],
                            "try" if st["tries"] == 1 else "tries")
                del self.pair_owed[race]
            elif st["tries"] >= cfg.pair_unwind_followup_tries:
                alert(f"pair unwind on {race} left {left} shares unpaired after {st['tries']} tries "
                      f"({', '.join(f'{self.ex[e].label} {q}' for e, q in st['legs'].items() if q >= 1)}): "
                      f"now ordinary inventory, which the quoting will work off")
                del self.pair_owed[race]
        return acted

    def pair_followup_take(self, race, st, lag, fvs, now_m, mine_real=None):
        """One immediate-or-cancel order per lagging leg for what it owes, at a limit up to
        pair_unwind_followup_max_cost past its planned price (short-set buy-back: buy YES at the asks, a covered
        "sell NO" with reduce_no_as_sell; long-set sale: sell YES at the bids), sized to the fresh book's depth within
        that limit (never more than held). Our quotes on those legs are pulled first, leftovers cancelled after.
        Returns {eid: shares filled}, or None if the batch was never sent (write budget)."""
        cfg = self.cfg
        buy = st["action"] == "buy"
        key = "asks" if buy else "bids"
        orders = []
        for e in lag:
            ex = self.ex[e]
            try:                                  # the cached book may be old
                ex.book = strip_own(self.api.book(e, self.tid), (mine_real or {}).get(e, []))
                ex.book_time = ex.verified = time.monotonic()
            except ApiError as err:
                log.warning("pair unwind follow-up on %s: book download failed (%s) - cached book", race, err)
            planned = st["price"][e]
            limit = (floor_tick(planned + cfg.pair_unwind_followup_max_cost) if buy
                     else ceil_tick(planned - cfg.pair_unwind_followup_max_cost))
            held = -ex.inv if buy else ex.inv
            want = int(min(st["legs"][e], max(0.0, held) + 1e-9))
            # walk the book to the worst level needed (never past the limit): the order's price
            price, depth = None, 0.0
            for lv in (ex.book or {}).get(key) or []:
                if (lv["price"] > limit + 1e-9) if buy else (lv["price"] < limit - 1e-9):
                    break
                if depth >= want:
                    break
                price, depth = lv["price"], depth + lv["quantity"]
            qty = int(min(want, depth))
            if qty < 1 or price is None:
                log.warning("pair unwind follow-up on %s: nothing on %s within %.3f (planned %.3f) - %d owed, "
                            "try %d of %d", race, ex.label, limit, planned, st["legs"][e], st["tries"],
                            cfg.pair_unwind_followup_tries)
                continue
            order = {"exchangeId": e, "side": "yes", "action": "buy" if buy else "sell", "quantity": qty,
                     "price": price, "tournamentId": self.tid}
            if buy:                               # a covered "sell NO" of the lone NO left on this leg
                order = self.no_sell_order(order, ex.inv)
                if order is None:                 # no lone NO left there: nothing can ever be sent - leg cleared
                    log.warning("pair unwind follow-up on %s: the NO on %s is all in a NO+NO set - not sent, %d owed "
                                "there cleared", race, ex.label, st["legs"][e])
                    st["legs"][e] = 0
                    st.setdefault("cleared", []).append(e)
                    continue
            orders.append(order)
        if not orders:
            return {}
        st["sent"] = True                         # from here on writes go out (cancels, the batch)
        if not all([self.cancel(o["exchangeId"], [], whole_exchange=True) for o in orders]):
            log.warning("pair unwind follow-up on %s: could not clear our own quotes - next cycle", race)
            return {}
        exp = iso(utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        for o in orders:
            o["expirationDate"] = exp
        log.warning("PAIR UNWIND follow-up %s: %s", race, ", ".join(
            f"{'buying' if buy else 'selling'} {o['quantity']} YES on {self.ex[o['exchangeId']].label} at "
            f"{o['price']:.3f}{' (sell NO)' if o.get('_no_sell') else ''}" for o in orders))
        self.orders_stale = True
        try:
            results = self.place_orders(orders)
        except ApiError as err:
            if err.code == "WRITE_BUDGET_WAIT":
                self.arbs_skipped_budget += 1
                return None
            for o in orders:
                self.ex[o["exchangeId"]].pending_until = now_m + cfg.pending_seconds
            alert(f"pair unwind follow-up on {race}: placement failed ({err}) - check positions")
            if err.code in FATAL_API_CODES:
                fatal(f"orders rejected with {err.code}")
            return {}
        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, o in enumerate(orders):
            if (by_index.get(k) or {}).get("ok"):
                self.remember_order(o, (by_index.get(k) or {}).get("data") or {}, now_m)
        for o in orders:
            self.cancel(o["exchangeId"], [], whole_exchange=True, quiet=True)
        got = {}
        for k, o in enumerate(orders):
            res = by_index.get(k) or {}
            data = res.get("data") or {}
            if not res.get("ok"):
                log.warning("pair unwind follow-up on %s: %s refused (%s)", race, self.ex[o["exchangeId"]].label,
                            (data.get("error") or {}).get("message") or data.get("error"))
            got[o["exchangeId"]] = float(data.get("quantityTraded") or 0)
            if data.get("orderId") is not None:
                self.order_meta[data["orderId"]] = {"our_side": "bid" if buy else "ask", "price": o["price"],
                                                    "arb": True, "fv": fvs.get(o["exchangeId"]), "t": time.time(),
                                                    "eid": o["exchangeId"],
                                                    **({"no_sell": True} if o.get("_no_sell") else {})}
                self.notes_dirty = True
        return got

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
        tilted = bool(getattr(cfg, "take_tilted_ref", False))
        if tilted != getattr(self, "take_tilted_seen", tilted):   # X12 toggled: the price compared changed, so
            for ex in self.ex.values():                         # every gap must be confirmed afresh on it
                ex.take_dir, ex.take_since = 0, now_m
        self.take_tilted_seen = tilted
        version = getattr(self.refs, "version", 0)
        if not cfg.take_enabled or not self.refs or version == self.take_version_seen:
            return taken
        self.take_version_seen = version
        ages = self.refs.ages() if hasattr(self.refs, "ages") else {}
        for eid, ex in self.ex.items():
            p = refs.get(eid)
            if p is not None and 0 < cfg.take_ref_max_age_seconds < ages.get(f"{ex.group}|{ex.party}", 0.0):
                p = None                                  # an old price, kept through failed downloads: not evidence
            p = self.take_ref(ex, p, now_m)               # X12: the tilted r' with take_tilted_ref
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
            if not self.writes_ready(3):          # cancel + take + leftover cancel, on the main thread
                continue                          # (the direction stays confirmed: taken once the budget frees)
            no_bid, no_ask = self.party_blocks(ex, party_delta)
            tb, ta = self.tilt_blocks(ex, refs.get(eid))  # T2.4: no take that grows |tilt_exposure| past the cap
            if (ex.take_dir > 0 and (no_bid or tb)) or (ex.take_dir < 0 and (no_ask or ta)):
                continue
            try:                                          # the cached book may be old: check it's still there
                ex.book = strip_own(self.api.book(eid, self.tid), mine_real.get(eid, []))
                ex.book_time = ex.verified = time.monotonic()
            except ApiError as e:
                log.warning("take on %s skipped: book download failed (%s)", ex.label, e)
                continue
            p = self.take_ref(ex, refs[eid], now_m)       # X12: the tilted r' with take_tilted_ref
            if self.take_direction(ex, p) != ex.take_dir:
                ex.take_dir = 0                           # the gap has closed: nothing to take
                continue
            if self.execute_take(ex, p, inv.get(eid, 0.0), fvs.get(eid), now_m):
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
        if qty >= 1 and not self.writes_ready(3):
            # Checked BEFORE pulling our own quote (cancel + take + leftover cancel): a take that can't be sent
            # must not leave the market unquoted. The direction stays confirmed: taken once the budget frees.
            self.takes_skipped_budget += 1
            log.info("take on %s skipped: write budget busy (next cycle)", ex.label)
            return False
        ex.take_until = now_m + cfg.take_cooldown_seconds
        ex.take_dir = 0                                                  # a new gap must be confirmed afresh
        if qty < 1:
            return False
        if buy and self.set_blocked(ex.eid, ex.inv):       # Package 7: all NO here in a NO+NO set: nothing to send
            log.info("take on %s skipped: all its NO is in a NO+NO set (no_set_aware_bids)", ex.label)
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
        order = self.no_sell_order({"exchangeId": ex.eid, "side": "yes", "action": "buy" if buy else "sell",
                                    "quantity": qty, "price": price, "tournamentId": self.tid,
                                    "expirationDate": iso(utcnow() + timedelta(seconds=cfg.take_order_ttl))}, ex.inv)
        self.orders_stale = True
        if order is None:                         # Package 7: all NO here is in a NO+NO set (no_set_aware_bids)
            self.takes_total -= 1
            log.warning("take on %s not sent: all its NO is in a NO+NO set (our quote there is re-placed next cycle)",
                        ex.label)
            return True
        try:
            results = self.place_orders([order])
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT":             # never sent: no hold, no alert; quotes back next cycle
                self.takes_skipped_budget += 1
                self.takes_total -= 1
                log.warning("take on %s not sent: write budget busy (our quote there is re-placed next cycle)",
                            ex.label)
                return True
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
                                                "fv": fv, "t": time.time(), "eid": ex.eid,
                                                **({"no_sell": True} if order.get("_no_sell") else {})}
            self.notes_dirty = True
        log.info("take on %s: traded %s of %d", ex.label, data.get("quantityTraded", "?"), order["quantity"])
        return True

    # ------------------------------------------------------------------------------ C hold target (Package 5)
    HOLD_FLOOR = 0.01             # never more than 1c through the book's own price on the losing side

    def hold_gate(self, ex, cfg):
        """C: may this market use the hold target at all (book-priced, headline staging gate)?"""
        return (cfg.hold_target_hours > 0 and ex.eid not in self.ref_only
                and (cfg.hold_target_headline or ex.group not in cfg.headline_races))

    def hold_quote(self, ex, q, best_bid, best_ask, price, cfg, ref=None, now_m=None):
        """C quote half: lots aged >= hold_target_hours -> the reducing side joins the best other price on its side
        (long: ask = best other ask, short: bid = best other bid), never more than HOLD_FLOOR through `price` (the
        book's own price) and never crossing the best other bid / ask; only ever moves in, never out. Our own
        adding side is pulled back to a tick behind it (own bid < own ask). A side the decision left out (a guard,
        reduce-only...) stays out. The adding side is otherwise unchanged. Not during the jump cooldown, within
        reduce_from_book_pause_s of a Polymarket jump, or with Polymarket (ref) more than ref_guard_gap on the other
        side of `price`; the joined side is never larger than the position."""
        if (price is None or not self.hold_gate(ex, cfg) or getattr(ex, "age", 0.0) < cfg.hold_target_hours
                or abs(ex.inv) < 1):
            return q
        if now_m is not None and (now_m < ex.cooldown_until or now_m - ex.ref_jump_at < cfg.reduce_from_book_pause_s):
            return q
        if ref is not None and ((ref - price) if ex.inv > 0 else (price - ref)) > cfg.ref_guard_gap:
            return q
        if ex.inv >= 1 and q.ask is not None and q.ask_size >= 1 and best_ask is not None:
            ask = max(ceil_tick(best_ask), ceil_tick(price - self.HOLD_FLOOR - 1e-9))
            if best_bid is not None:
                ask = max(ask, ceil_tick(best_bid + TICK))
            if ask >= q.ask - 1e-9:
                return q                          # already at or inside the best (or the floor stops it)
            bid, bid_size, bid_limit = q.bid, q.bid_size, q.bid_limit
            top = floor_tick(ask - TICK)
            if bid is not None and bid > top + 1e-9:
                bid = top
            if bid_limit is not None and bid_limit > top + 1e-9:
                bid_limit = top
            if bid is not None and bid < PMIN - 1e-9:
                bid, bid_size, bid_limit = None, 0, None
            return replace(q, ask=ask, ask_limit=min(q.ask_limit, ask) if q.ask_limit is not None else None,
                           ask_size=min(q.ask_size, int(ex.inv)),
                           ask_max=min(q.ask_max, int(ex.inv)) if q.ask_max is not None else None,
                           bid=bid, bid_size=bid_size if bid is not None else 0, bid_limit=bid_limit)
        if ex.inv <= -1 and q.bid is not None and q.bid_size >= 1 and best_bid is not None:
            bid = min(floor_tick(best_bid), floor_tick(price + self.HOLD_FLOOR + 1e-9))
            if best_ask is not None:
                bid = min(bid, floor_tick(best_ask - TICK))
            if bid <= q.bid + 1e-9:
                return q
            ask, ask_size, ask_limit = q.ask, q.ask_size, q.ask_limit
            low = ceil_tick(bid + TICK)
            if ask is not None and ask < low - 1e-9:
                ask = low
            if ask_limit is not None and ask_limit < low - 1e-9:
                ask_limit = low
            if ask is not None and ask > PMAX + 1e-9:
                ask, ask_size, ask_limit = None, 0, None
            return replace(q, bid=bid, bid_limit=max(q.bid_limit, bid) if q.bid_limit is not None else None,
                           bid_size=min(q.bid_size, int(-ex.inv)),
                           bid_max=min(q.bid_max, int(-ex.inv)) if q.bid_max is not None else None,
                           ask=ask, ask_size=ask_size if ask is not None else 0, ask_limit=ask_limit)
        return q

    def hold_take_plan(self, inv, book_fvs, now_m, eids=None):
        """C take half, the pure plan (no state changed): for markets aged >= 2 x hold_target_hours, oldest first,
        [{"eid", "buy", "qty", "price", "notional"}]: sell a long to the best other bid / buy back a short at the
        best other ask, only if that price is at most HOLD_FLOOR through the book's price, qty <= the best level,
        the position and max_order_cash_frac of the account (notional = shares x price for a long, x (1 - price)
        for a short: what is unloaded), all within hold_take_max_per_min takes in the last 60 s and
        hold_unload_budget_frac x account of notional in the last 3600 s (self.hold_takes). Nothing before
        hold_open_at (the budget starts fully used). eids limits the markets looked at (live re-check)."""
        cfg = self.cfg
        if cfg.hold_target_hours <= 0 or self.hold_open_at is None or now_m < self.hold_open_at:
            return []
        bank = self.bankroll()
        used = sum(n for t, n in self.hold_takes if now_m - t < 3600)
        left = cfg.hold_unload_budget_frac * bank - used
        slots = cfg.hold_take_max_per_min - sum(1 for t, _ in self.hold_takes if now_m - t < 60)
        cands = []
        for eid in (self.ex if eids is None else eids):
            ex = self.ex.get(eid)
            pos = inv.get(eid, 0.0)
            if ex is None or abs(pos) < 1 or not self.hold_gate(ex, cfg) or busy(ex, now_m):
                continue
            if self.hours_to_close(ex) <= cfg.flatten_hours_before_close:
                continue                          # the flatten / exit windows have their own rules
            if now_m < ex.cooldown_until or now_m - ex.ref_jump_at < cfg.reduce_from_book_pause_s:
                continue                          # jump guard / just after a Polymarket jump: not now
            age = self.age_hours(ex)
            if age >= 2 * cfg.hold_target_hours:
                cands.append((-age, eid))
        plan = []
        for _, eid in sorted(cands):
            if slots < 1 or left <= 0:
                break
            ex, pos, bfv = self.ex[eid], inv.get(eid, 0.0), book_fvs.get(eid)
            buy = pos < 0
            level = ((ex.book or {}).get("asks" if buy else "bids") or [None])[0]
            if bfv is None or not level:
                continue
            price = level["price"]
            if (price > bfv + self.HOLD_FLOOR + 1e-9) if buy else (price < bfv - self.HOLD_FLOOR - 1e-9):
                continue                          # more than 1c through the book's price: not at any size
            ref = (self.cur_refs or {}).get(eid)
            if ref is not None and ((bfv - ref) if buy else (ref - bfv)) > cfg.ref_guard_gap:
                continue                          # Polymarket says the exit is the wrong side (ref guard)
            unit = max(1 - price if buy else price, TICK)
            qty = int(min(abs(pos), level["quantity"], cfg.max_order_cash_frac * bank / unit, left / unit))
            if qty < 1:
                continue
            plan.append({"eid": eid, "buy": buy, "qty": qty, "price": price, "notional": qty * unit})
            slots, left = slots - 1, left - qty * unit
        return plan

    def take_aged(self, inv, book_fvs, mine_real, now_m, execute=None):
        """C take half, run beside take_stale_quotes: execute hold_take_plan's takes (immediate-or-cancel: our
        quotes there pulled first, the take, its leftover cancelled), recording each in the rolling budget.
        Live: write budget first (3 writes, as execute_take), then a fresh book and the plan re-checked on it.
        execute(eid, buy, qty, price) -> shares done replaces the exchange (simulator; the plan is the same);
        None = refused (no writes): nothing counted and the rest waits for the next call, as the live path.
        Returns the exchanges traded (skipped by quoting this cycle)."""
        cfg, taken = self.cfg, set()
        if cfg.hold_target_hours <= 0:
            self.hold_open_at = None              # re-enabling restarts the 1-hour hold-off
            return taken
        if self.hold_open_at is None:             # restart-safe: the budget starts fully used for an hour
            self.hold_open_at = now_m + 3600
            log.info("hold target on: aged takes start in 60 min (budget %.1f%% of the account per hour)",
                     100 * cfg.hold_unload_budget_frac)
        while self.hold_takes and now_m - self.hold_takes[0][0] >= 3600:
            self.hold_takes.popleft()
        for p in self.hold_take_plan(inv, book_fvs, now_m):
            if not self.running:
                break
            eid, ex = p["eid"], self.ex[p["eid"]]
            if execute is not None:
                if execute(eid, p["buy"], p["qty"], p["price"]) is None:
                    break                         # refused (no writes): not sent, not counted - as the live path
                self.hold_takes.append((now_m, p["notional"]))
                self.hold_takes_total += 1
                taken.add(eid)
                continue
            if self.api.live:
                if not self.writes_ready(3):      # cancel + take + leftover cancel
                    self.takes_skipped_budget += 1
                    log.info("aged take on %s skipped: write budget busy (next cycle)", ex.label)
                    break
                try:                              # the cached book may be old: re-check the plan on a fresh one
                    ex.book = strip_own(self.api.book(eid, self.tid), mine_real.get(eid, []))
                    ex.book_time = ex.verified = time.monotonic()
                except ApiError as e:
                    log.warning("aged take on %s skipped: book download failed (%s)", ex.label, e)
                    continue
                again = self.hold_take_plan(inv, book_fvs, now_m, eids=[eid])
                if not again:
                    continue
                p = again[0]
            if p["buy"] and self.set_blocked(eid, ex.inv):     # Package 7: all NO here in a NO+NO set
                continue
            self.hold_takes.append((now_m, p["notional"]))     # counted when sent (an IOC may do less: safe side)
            self.hold_takes_total += 1
            taken.add(eid)
            log.warning("%sAGED TAKE %s: held %.1f h -> %s %d YES at %.3f (%.0f of the hourly budget)",
                        "" if self.api.live else "[dry] ", ex.label, self.age_hours(ex),
                        "buying" if p["buy"] else "selling", p["qty"], p["price"], p["notional"])
            if not self.api.live:
                continue
            if not self.cancel(eid, [], whole_exchange=True):  # never trade with ourselves
                continue
            order = self.no_sell_order({"exchangeId": eid, "side": "yes", "action": "buy" if p["buy"] else "sell",
                                        "quantity": p["qty"], "price": p["price"], "tournamentId": self.tid,
                                        "expirationDate": iso(utcnow() + timedelta(seconds=cfg.take_order_ttl))}, ex.inv)
            self.orders_stale = True
            if order is None:                     # Package 7: all NO here is in a NO+NO set (no_set_aware_bids)
                log.warning("aged take on %s not sent: all its NO is in a NO+NO set", ex.label)
                continue
            try:
                results = self.place_orders([order])
            except ApiError as e:
                if e.code == "WRITE_BUDGET_WAIT":             # never sent (still counted: the budget errs safe)
                    self.takes_skipped_budget += 1
                    break
                ex.pending_until = now_m + cfg.pending_seconds
                alert(f"aged take on {ex.label} failed ({e}) - check positions")
                if e.code in FATAL_API_CODES:
                    fatal(f"orders rejected with {e.code}")
                continue
            res = results[0] if results else {}
            data = res.get("data") or {}
            if res.get("ok"):
                self.remember_order(order, data, now_m)
            self.cancel(eid, [], whole_exchange=True, quiet=True)
            if data.get("orderId") is not None:
                self.order_meta[data["orderId"]] = {"our_side": "bid" if p["buy"] else "ask", "price": p["price"],
                                                    "take": True, "fv": book_fvs.get(eid), "t": time.time(),
                                                    "eid": eid, **({"no_sell": True} if order.get("_no_sell") else {})}
                self.notes_dirty = True
        return taken

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
        for f in new:
            oid = f.get("orderId")
            if oid is not None and oid not in self.order_meta and str(f.get("exchangeId")) in self.unconfirmed:
                # A fill's price is in the terms of its side: a NO-side fill (our converted ask) is 1 - the YES price.
                # quantity > 0 = a YES-side fill (our bid, or a sell of YES we held); < 0 = a NO-side fill (our
                # ask, converted to buy NO): day one reported those at the NO price (1 - our ask), but accept the
                # YES price too.
                qty, p = float(f.get("quantity") or 0), float(f.get("price") or 0)
                eid = str(f.get("exchangeId"))
                # Package 6: a covered "sell NO @ 1-b" (our bid at b) is a NO-side fill too, at the NO price: tried
                # (only against orders sent that way) before the YES-price fallback for asks.
                # With the flag on, the covered-bid reading of a YES-side fill goes before the ask one: in a lost batch
                # holding a covered sale at b and an ask at 1-b, a covered-sale fill must not be adopted as the ask.
                tries = ((((True, p, None), (True, rnd(1 - p), True), (False, p, None)) if self.cfg.reduce_no_as_sell
                          else ((True, p, None), (False, p, None), (True, rnd(1 - p), True))) if qty > 0 else
                         ((False, rnd(1 - p), None), (True, rnd(1 - p), True), (False, p, None), (True, p, True)))
                for is_bid, price, no_sell in tries:
                    if self.match_unconfirmed(eid, is_bid, price, oid, lift=False, no_sell=no_sell):
                        break
        if new:
            self.fills.record(new, self.order_meta, fvs)
        for f in reversed(new):                               # oldest first
            oid = f.get("orderId")
            o = self.my_orders.get(oid)
            if o is None:
                if oid in self.placed_qty:                    # recovered from its fill, not listed yet
                    self.filled_qty[oid] += abs(float(f.get("quantity") or 0))
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
        return new

    def note_refills(self, new, inv, now_m):
        """Same-side refill cooldown: count each new fill of one of our quotes (not takes or arbitrage legs) that
        ADDED to the position, per market and side; once refill_cooldown_fills of them, together at least
        refill_cooldown_min_shares, landed within refill_cooldown_window_seconds, that side is withheld for
        refill_cooldown_seconds (see plan_change)."""
        if new:
            self.pos_record_due = True            # recorder: note the positions straight after a fill
        cfg = self.cfg
        if not new or not cfg.refill_cooldown_enabled:
            return
        wall = utcnow().timestamp()
        for f in reversed(new):                   # oldest first
            meta = self.order_meta.get(f.get("orderId")) or {}
            side, eid = meta.get("our_side"), str(f.get("exchangeId"))
            if side not in ("bid", "ask") or meta.get("take") or meta.get("arb") or eid not in self.ex:
                continue
            try:
                t = parse_ts(f.get("filledAt"))
            except (TypeError, ValueError):
                t = None
            if t is not None and wall - t.timestamp() > cfg.refill_cooldown_window_seconds:
                continue                          # an old fill seen late (e.g. after a restart): not a run now
            pos = float(inv.get(eid, self.ex[eid].inv) if inv is not None else self.ex[eid].inv)
            if (pos <= 0) if side == "bid" else (pos >= 0):
                continue                          # it reduced (or closed) the position: unloading is good
            key, qty = (eid, side), abs(float(f.get("quantity") or 0))
            run = self.refills[key]
            run.append((now_m, qty))
            while run and now_m - run[0][0] > cfg.refill_cooldown_window_seconds:
                run.popleft()
            shares = sum(q for _, q in run)
            if len(run) >= cfg.refill_cooldown_fills and shares >= cfg.refill_cooldown_min_shares:
                if self.refill_until.get(key, -1e9) <= now_m:
                    log.info("refill cooldown %s %s %.0f s: %d fills / %s sh in %.0f s", self.ex[eid].label, side,
                             cfg.refill_cooldown_seconds, len(run), f"{shares:,.0f}", cfg.refill_cooldown_window_seconds)
                self.refill_until[key] = now_m + cfg.refill_cooldown_seconds

    def refill_cooling(self, ex, is_bid, now_m):
        """True while this side is in its refill cooldown and quoting it would add to the position."""
        if not self.cfg.refill_cooldown_enabled or self.refill_until.get((ex.eid, "bid" if is_bid else "ask"), -1e9) <= now_m:
            return False
        return ex.inv >= 0 if is_bid else ex.inv <= 0   # a side that would reduce the position is exempt

    def note_unloads(self, new, inv, now_m):
        """Fast unload: a fill of one of our quotes (not a take or arbitrage leg) that ADDED to the position, with at
        least fast_unload_min_edge at the quote (quote price vs fv when quoted) and fast_unload_min_shares, opens (or
        extends) a window of fast_unload_seconds in which the reducing side quotes near fair value (decide,
        compute_quote). Shares to unload = the position increase; fills on the reducing side count them down."""
        cfg = self.cfg
        if not new or not cfg.fast_unload_enabled:
            return
        wall = utcnow().timestamp()
        for f in reversed(new):                   # oldest first
            meta = self.order_meta.get(f.get("orderId")) or {}
            side, eid = meta.get("our_side"), str(f.get("exchangeId"))
            if side not in ("bid", "ask") or meta.get("take") or meta.get("arb") or eid not in self.ex:
                continue
            qty = abs(float(f.get("quantity") or 0))
            w = self.unloads.get(eid)
            if w and w["side"] == side:           # the reducing side traded: fewer shares left to unload
                w["left"] -= qty
                continue
            try:
                t = parse_ts(f.get("filledAt"))
            except (TypeError, ValueError):
                t = None
            if t is not None and wall - t.timestamp() > cfg.fast_unload_seconds:
                continue                          # an old fill seen late (e.g. after a restart)
            pos = float(inv.get(eid, self.ex[eid].inv) if inv is not None else self.ex[eid].inv)
            if (pos <= 0) if side == "bid" else (pos >= 0):
                continue                          # it reduced (or closed) the position
            price, fv = meta.get("price"), meta.get("fv")
            if fv is None or price is None:
                continue
            edge = (float(fv) - float(price)) if side == "bid" else (float(price) - float(fv))
            if edge < cfg.fast_unload_min_edge - 1e-9 or qty < cfg.fast_unload_min_shares:
                continue
            red = "ask" if side == "bid" else "bid"
            left = min(qty, abs(pos)) + (w["left"] if w and w["side"] == red else 0.0)
            self.unloads[eid] = {"until": now_m + cfg.fast_unload_seconds, "side": red, "left": min(left, abs(pos))}
            log.info("fast unload %s: %s %s at %+.1fc, %s %gc %s fair for %.0f s", self.ex[eid].label,
                     "bought" if side == "bid" else "sold", f"{qty:,.0f}", 100 * edge,
                     "offering" if red == "ask" else "bidding", 100 * cfg.fast_unload_edge,
                     "over" if red == "ask" else "under", cfg.fast_unload_seconds)

    def unload_side(self, ex, now_m):
        """The reducing side ("bid"/"ask") of this exchange's open fast unload window, or None. A window closes once
        it has expired, its shares are unloaded, or the position in that direction is gone."""
        w = self.unloads.get(ex.eid)
        if w is None:
            return None
        if (not self.cfg.fast_unload_enabled or now_m >= w["until"] or w["left"] < 1
                or (ex.inv < 1 if w["side"] == "ask" else ex.inv > -1)):
            del self.unloads[ex.eid]
            return None
        return w["side"]

    def unload_urgent(self, ex, now_m):
        """The side of an open fast unload window whose unload quote has not been placed yet (urgent), else None."""
        side = self.unload_side(ex, now_m)
        return side if side and not self.unloads[ex.eid].get("placed") else None

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
                          worst_case_loss REAL, party_delta REAL, orders_resting INTEGER,
                          liquidation_value REAL)""")
        # Older files: add the column (nullable; rows written before stay NULL).
        self.acct_liq = True
        try:
            if "liquidation_value" not in {r[1] for r in db.execute("PRAGMA table_info(account)")}:
                db.execute("ALTER TABLE account ADD COLUMN liquidation_value REAL")
        except sqlite3.Error as e:
            self.acct_liq = False
            log.warning("recorder: could not add account.liquidation_value (%s) - not recorded", e)
        db.execute("CREATE INDEX IF NOT EXISTS snapshots_eid_ts ON snapshots (eid, ts)")
        # Other traders' book tops (our own orders removed), one row per change: bids/asks = JSON [[price, size]...]
        db.execute("CREATE TABLE IF NOT EXISTS books (ts REAL, eid TEXT, bids TEXT, asks TEXT)")
        # Tournament trades from the realtime feed (all traders): price/quantity when the item carries them
        db.execute("CREATE TABLE IF NOT EXISTS trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS books_eid_ts ON books (eid, ts)")
        if self.cfg.record_positions:
            # The exchange's own valuation, per position (a row when anything in it changed, or hourly): ts = when
            # read, prev_ts = the previous read that saw it (so a change happened in (prev_ts, ts]), fields = JSON
            # of every other number the API gives for it (cost basis, P&L, market value...).
            db.execute("""CREATE TABLE IF NOT EXISTS positions (ts REAL, prev_ts REAL, eid TEXT, quantity REAL,
                              current_price REAL, fields TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS positions_eid_ts ON positions (eid, ts)")
            # Account totals at the same reads; P&L-endpoint numbers are from the read at pnl_ts (they're read
            # every slow_poll_seconds); fields = JSON of every number in the P&L reply and the positions summary.
            db.execute("""CREATE TABLE IF NOT EXISTS account_marks (ts REAL, account_value REAL, market_value REAL,
                              cash REAL, realized REAL, unrealized REAL, pnl_ts REAL, fields TEXT)""")
        db.commit()
        return db

    POS_PRICE_KEYS = ("currentPrice", "markPrice", "valuationPrice", "price")

    def record_positions(self, pos, f_pnl, now_m, fill_event):
        """Recorder (record_positions): the positions reply just read - no extra request. Written at most once per
        record_seconds, plus at the first read after a fill; a position's row only when one of its numbers changed
        since it was last written (or every record_positions_full_seconds), which keeps it to ~1-3 MB a day."""
        cfg = self.cfg
        if f_pnl is not None:
            try:
                self.last_pnl_reply = (f_pnl.result(), utcnow().timestamp())
            except Exception:                     # the P&L read failing is handled (and logged) by account_value
                pass
        if not (fill_event or self.pos_record_due or now_m - self.last_pos_record >= cfg.record_seconds):
            return
        self.last_pos_record, self.pos_record_due = now_m, False
        full = now_m - self.last_pos_full >= cfg.record_positions_full_seconds
        if full:
            self.last_pos_full = now_m
        ts = round(utcnow().timestamp(), 3)
        try:
            rows, seen = [], set()
            for p in pos.get("positions", []) or []:
                if not isinstance(p, dict) or p.get("settled") or p.get("exchangeId") is None:
                    continue
                eid = str(p["exchangeId"])
                seen.add(eid)
                nums = numeric_fields(p, skip=("exchangeId",))
                price = next((nums.pop(k) for k in self.POS_PRICE_KEYS if k in nums), None)
                qty = nums.pop("quantity", None)
                vals = (qty, price, json.dumps(nums, sort_keys=True, separators=(",", ":")))
                old = self.pos_seen.get(eid)
                if full or old is None or old[0][:2] != vals[:2]:   # quantity or the mark changed (P&L ticks
                    rows.append((ts, old[1] if old else None, eid, *vals))   # alone would write every position every minute)
                self.pos_seen[eid] = (vals, ts)
            for eid in [e for e in self.pos_seen if e not in seen]:   # closed (or settled): one row saying so
                rows.append((ts, self.pos_seen.pop(eid)[1], eid, 0.0, None, "{}"))
            pnl, pnl_ts = self.last_pnl_reply
            pnl_n = numeric_fields(pnl) if isinstance(pnl, dict) else {}
            summ = pos.get("summary")
            sum_n = numeric_fields(summ) if isinstance(summ, dict) else {}
            top_n = numeric_fields({k: v for k, v in pos.items() if k not in ("positions", "summary")})

            def pick(want, avoid=None):
                for src in (pnl_n, sum_n):
                    for k, v in src.items():
                        kl = k.lower()
                        if any(w in kl for w in want) and not (avoid and avoid in kl):
                            return v
                return None
            acct = (ts, pnl_n.get("totalAccountValue"), sum_n.get("totalMarketValue", pnl_n.get("totalMarketValue")),
                    pick(("cash", "balance")), pick(("realized", "realised"), "unreali"),
                    pick(("unrealized", "unrealised")), pnl_ts and round(pnl_ts, 3),
                    json.dumps({**{"pnl." + k: v for k, v in pnl_n.items()}, **{"sum." + k: v for k, v in sum_n.items()},
                                **top_n}, sort_keys=True, separators=(",", ":")))
            self.db.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?)", rows)
            self.db.execute("INSERT INTO account_marks VALUES (?,?,?,?,?,?,?,?)", acct)
            self.db.commit()
        except (sqlite3.Error, AttributeError, TypeError, ValueError) as e:
            log.warning("could not record positions: %s", e)

    def record(self, fvs, now_m):
        """One row per market (best bid/ask incl. ours, fair value, reference, our quote, position)."""
        if not self.db or now_m - self.last_record < self.cfg.record_seconds:
            return
        self.last_record = now_m
        ts, mode, h = iso(utcnow()), "live" if self.api.live else "dry", self.health
        rows = [(ts, mode, eid, ex.label, *self.last_tops.get(eid, (None, None)), fvs.get(eid), ex.ref,
                 ex.quote.bid, ex.quote.ask, ex.inv) for eid, ex in self.ex.items()]
        trades = []
        if self.cfg.record_books and self.feed and hasattr(self.feed, "take_trades"):
            for t, item in self.feed.take_trades():
                trades.append((round(t, 3), str(item.get("exchangeId")), _num(item.get("price")),
                               _num(item.get("quantity")), json.dumps(item, default=str)[:1000]))
        book_rows, self.book_rows = self.book_rows, []
        try:
            self.db.executemany("INSERT INTO books VALUES (?,?,?,?)", book_rows)
            self.db.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", trades)
            self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            acct = (ts, mode, h.get("account_value"), h.get("locked_in_orders"), h.get("worst_case_loss"),
                    h.get("party_delta"), h.get("orders_resting"))
            if getattr(self, "acct_liq", False):  # (the latest status write's value: None until there is one)
                self.db.execute("INSERT INTO account (ts, mode, account_value, locked_in_orders, worst_case_loss, "
                                "party_delta, orders_resting, liquidation_value) VALUES (?,?,?,?,?,?,?,?)",
                                acct + ((self.ops_last or {}).get("liquidation_value"),))
            else:
                self.db.execute("INSERT INTO account (ts, mode, account_value, locked_in_orders, worst_case_loss, "
                                "party_delta, orders_resting) VALUES (?,?,?,?,?,?,?)", acct)
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
                                           health=self.health, hours=every, status=status,
                                           ops_line=self.summary_ops_line(value))
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

    def summary_ops_line(self, value):
        """ops_summary_line for this bot (latest ops fields; tilt_s / tilt_exposure when T2.1 set them), plus
        " | NO+NO sets N (cap X)" (Package 7: N races held NO on every leg) when any is held. Never raises."""
        try:
            line = ops_summary_line(self.ops_last or self.safe_ops_fields(), value,
                                    getattr(self, "tilt_s", None), getattr(self, "tilt_exposure", None))
        except Exception as e:                    # a report must never disturb trading
            log.warning("summary ops line failed: %s", e)
            return None
        nn = self.safe_nono_sets()
        if nn and nn.get("races"):
            part = f"NO+NO sets {nn['races']} (cap {nn['capital'] / 1000:.1f}k)"
            line = f"{line} | {part}" if line else part
        return line

    def safe_nono_sets(self):
        """nono_sets that never raises (None on any error)."""
        try:
            return self.nono_sets()
        except Exception as e:                    # reporting must never disturb trading
            log.warning("nono_sets unavailable: %s", e)
            return None

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
            problems.append("reduce-only: settlement risk above the cap")
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

    # ------------------------------------------------------------------------------ ops fields (Package 5, 3.3)
    OPS_KEYS = ("liquidation_value", "liquidation_unpriced", "realised_pnl", "unrealised_pnl", "pnl_unreconciled",
                "toward_ref_capital_frac", "capital_over_6h_frac", "exit_ratio_24h")
    OPS_FILLS_MIN_SECONDS = 60.0          # fills.csv is re-read at most this often (and only when it changed)

    def ops_fills(self, now):
        """From fills.csv (cached; read when the file changed, at most every OPS_FILLS_MIN_SECONDS): FIFO lots with
        prices {eid: [[signed shares, YES price], ...]}, realised P&L, and over the last 24 h the shares that
        reduced |position| and the shares that added to it. Lots in position_lots_file carry no prices, so the fills
        are replayed: each fill's YES price is its quote price (fill price when absent), sign from our side (bid =
        bought YES); fills not matched to a quote of ours (our_side "?": arbitrage, takes) are skipped, so the
        realised figure covers maker fills only. Incremental: only the bytes appended since the saved offset are
        read (complete lines only); the replay restarts from 0 if the file shrank or was replaced."""
        path = bot_path(self.cfg.fills_csv)
        try:
            st = os.stat(path)
            sig = (st.st_mtime, st.st_size)
        except OSError:
            st, sig = None, None
        c = self.ops_cache
        if c and (c.get("sig") == sig or now - c.get("t", 0) < self.OPS_FILLS_MIN_SECONDS):
            return c
        if (not c or st is None or st.st_size < c.get("off", 0) or c.get("ino") != st.st_ino):
            c = {"off": 0, "ino": st.st_ino if st is not None else None, "fields": None, "book": defaultdict(deque),
                 "realised": 0.0, "events": deque()}
        if st is not None and st.st_size > c["off"]:
            with open(path, "rb") as f:
                f.seek(c["off"])
                data = f.read(st.st_size - c["off"])
            done = data.rfind(b"\n") + 1                # complete lines only: a half-written row waits
            self.ops_cache = c                          # progress kept even if a row raises something unexpected
            for raw in data[:done].split(b"\n")[:-1]:  # the offset advances over each row once it is consumed
                line = raw.decode("utf-8", "replace").rstrip("\r")
                try:
                    vals = next(csv.reader([line]), [])
                    if c["fields"] is None:
                        if vals:
                            c["fields"] = vals
                    elif vals:
                        self.ops_replay(c, dict(zip(c["fields"], vals)))
                except (ValueError, TypeError, KeyError, IndexError, OverflowError, csv.Error):
                    pass                                # a malformed row: skipped, the rest still replayed
                c["off"] += len(raw) + 1
        since = now - 24 * 3600
        ev = c["events"]
        while ev and ev[0][0] < since:
            ev.popleft()
        c.update(sig=sig, t=now, lots={e: [list(x) for x in v] for e, v in c["book"].items() if v},
                 reduced_24h=sum(r for t, r, _ in ev if t >= since), added_24h=sum(a for t, _, a in ev if t >= since))
        self.ops_cache = c
        return c

    @staticmethod
    def ops_replay(c, r):
        """One fills.csv row into the ops_fills state c (FIFO lots, realised, 24-h events)."""
        side = r.get("our_side")
        if side not in ("bid", "ask"):
            return
        try:
            qty = abs(float(r.get("qty") or 0))
            price = float(r.get("quote_price") or r.get("fill_price") or 0)
        except ValueError:
            return
        if qty <= 0 or not 0 < price < 1:
            return
        ts = parse_ts(r.get("filled_at"))           # a bad timestamp raises here, before any state changes
        ts = ts.timestamp() if ts is not None else None
        book, rem, red, add = c["book"][str(r.get("exchange_id"))], qty if side == "bid" else -qty, 0.0, 0.0
        while abs(rem) > 1e-9 and book and (book[0][0] > 0) != (rem > 0):
            lot = book[0]
            n = min(abs(rem), abs(lot[0]))
            c["realised"] += n * (price - lot[1]) * (1 if lot[0] > 0 else -1)
            lot[0] += n if lot[0] < 0 else -n
            rem += n if rem < 0 else -n
            red += n
            if abs(lot[0]) <= 1e-9:
                book.popleft()
        if abs(rem) > 1e-9:
            book.append([rem, price])
            add += abs(rem)
        if ts is not None:
            c["events"].append((ts, red, add))

    def ops_fields(self, now=None):
        """Read-only reporting for status.json, the recorder and the phone summary (no requests; None = unknown):
          liquidation_value       account value minus the haircut of selling every position to OTHER traders now:
                                  longs at the best other bid, shorts at the best other ask, instead of the mark
                                  (the exchange's valuation price, else the book's fair value). A market with no
                                  quote on that side keeps its mark and is counted in liquidation_unpriced.
          realised_pnl / unrealised_pnl   FIFO over fills.csv maker fills only (ops_fills; status.json says so in
                                  realised_pnl_scope); unrealised at the mark, only over markets
                                  whose replayed position equals the position held (the others: pnl_unreconciled)
          toward_ref_capital_frac share of position capital on the side Polymarket favours (long with Polymarket
                                  above the book's own fair value, or short with it below)
          capital_over_6h_frac    share of position capital in lots older than 6 h (update_lots)
          exit_ratio_24h          fill shares that reduced |position| / shares that added, last 24 h"""
        cfg = self.cfg
        now = time.time() if now is None else now
        out = dict.fromkeys(self.OPS_KEYS)
        inv = {e: float(q) for e, q in (self.held or {}).items() if e in self.ex and round(q)}
        book_fv = {}
        for members in self.groups.values():
            if any(e in inv for e in members):
                fv = {e: fair_value(self.ex[e].book, cfg) for e in members if e in self.ex}
                book_fv.update(normalise(fv) if len(fv) > 1 else fv)
        marks, cap, haircut, unpriced = {}, {}, 0.0, 0
        for e, q in inv.items():
            m = self.pos_marks.get(e)
            m = m if m is not None else book_fv.get(e)
            if m is None:
                unpriced += 1
                continue
            marks[e] = m
            cap[e] = abs(q) * (m if q > 0 else 1 - m)
            b = self.ex[e].book or {}
            lvl = b.get("bids" if q > 0 else "asks")
            if not lvl:
                unpriced += 1
                continue
            haircut += abs(q) * ((m - lvl[0]["price"]) if q > 0 else (lvl[0]["price"] - m))
        acct = self.health.get("account_value")
        out["liquidation_unpriced"] = unpriced
        if acct is not None:
            out["liquidation_value"] = round(float(acct) - haircut, 2)
        total = sum(cap.values())
        if total > 0:
            toward = 0.0
            for e, c in cap.items():
                r, f = (self.cur_refs or {}).get(e), book_fv.get(e)
                if r is not None and f is not None and ((inv[e] > 0 and r > f) or (inv[e] < 0 and r < f)):
                    toward += c
            out["toward_ref_capital_frac"] = round(toward / total, 4)
            old = 0.0
            for e, c in cap.items():
                lots = self.lots.get(e) or []
                n = sum(abs(x) for x, _ in lots)
                if n:
                    old += c * min(1.0, sum(abs(x) for x, t in lots if now - t > 6 * 3600) / n)
            out["capital_over_6h_frac"] = round(old / total, 4)
        fl = self.ops_fills(now)
        out["realised_pnl"] = round(fl["realised"], 2)
        unreal, bad = 0.0, 0
        for e in set(inv) | set(fl["lots"]):
            lots = fl["lots"].get(e, [])
            if round(sum(x for x, _ in lots)) != round(inv.get(e, 0.0)) or (e in inv and e not in marks):
                bad += 1
                continue
            unreal += sum(x * (marks[e] - p) for x, p in lots) if lots else 0.0
        out["unrealised_pnl"], out["pnl_unreconciled"] = round(unreal, 2), bad
        out["exit_ratio_24h"] = round(fl["reduced_24h"] / fl["added_24h"], 3) if fl["added_24h"] > 0 else None
        if cfg.pair_unwind_passive or self.pp:
            out["pair_passive_open"] = {r: {"leg": self.ex[v["leg"]].label, "price": v["price"], "left": v["left"]}
                                        for r, v in self.pp.items() if v["leg"] in self.ex}
            out["pair_passive_sets_total"] = self.pp_sets_total
        return out

    def safe_ops_fields(self):
        """ops_fields that never raises: on any error every field is None (logged once)."""
        try:
            return self.ops_fields()
        except Exception as e:                    # reporting must never disturb trading
            if not self.ops_warned:
                self.ops_warned = True
                log.warning("ops fields unavailable (%s: %s) - reported as null", type(e).__name__, e)
            return dict.fromkeys(self.OPS_KEYS)

    def write_status(self, ok):
        """status.json: a one-glance health check, e.g. `cat status.json` over ssh."""
        self.ops_last = self.safe_ops_fields()
        try:
            owed = ({"pair_owed": self.pair_owed_status()}
                    if getattr(self.cfg, "pair_unwind_followup", False) or getattr(self, "pair_owed", None) else {})
            write_json(bot_path(self.cfg.status_file), {
                "updated": iso(utcnow()), "mode": "live" if self.api.live else "dry run",
                "last_cycle_ok": ok, "failed_cycles_in_a_row": self.failed_cycles,
                "quotes_pulled_after_errors": self.pulled_after_errors, **self.health, **self.ops_last,
                "realised_pnl_scope": "maker fills only",   # arb / take fills (our_side '?') are not in realised_pnl
                **(self.api.pause_state() if hasattr(self.api, "pause_state") else {}),
                # T2.1: the tilt estimate (also restored from here at start) and the position's exposure to it
                "tilt_s": round(self.tilt_s, 4), "tilt_s_applied": round(self.tilt_s_applied, 4),
                "tilt_s_applied_headline": round(self.tilt_s_applied_headline, 4),
                "tilt_exposure": round(self.tilt_exposure),
                "tilt_state": self.tilt_state_dict(),
                "tilt_diag": {**getattr(self.tilt, "diag", {}),
                              "estimator": getattr(self.cfg, "ref_tilt_estimator", "slope")},
                # Package 7: NO+NO sets held (races, sets, capital at k - 1 per set) and the paired-unwind check
                "nono_sets": self.safe_nono_sets(), "pairno_state": getattr(self, "pairno_state", None),
                **owed,                                   # Package 7: pair unwind legs still owed {race: shares}
                "seconds_since_cycle": round(time.monotonic() - self.last_cycle_done, 1)
                if self.last_cycle_done is not None else None})
        except OSError as e:
            log.warning("could not write status file: %s", e)

    def maybe_daily_analysis(self):
        """Optional daily phone message: the headline lines of `analyze` over the last 24 h (local files only)."""
        hour, now = self.cfg.analyze_daily_hour, utcnow()
        if hour < 0 or not self.api.live or now.hour != hour or self.last_analysis_day == now.date():
            return
        self.last_analysis_day = now.date()
        try:
            lines = analyze(bot_path(self.cfg.fills_csv), bot_path(self.cfg.record_file) if self.cfg.record_file else "",
                            hours=24, top=3)
            notify("\n".join(lines[:2] + [l[:60] for l in lines[3:]]), title="mm_bot daily analysis", tags="bar_chart")
        except Exception as e:                # a report must never disturb trading
            log.warning("daily analysis failed: %s", e)

    # ------------------------------------------------------------------------------ live settings
    def check_overrides(self, force=False):
        """Every overrides_seconds: re-read settings_override.json if it changed, apply what's valid, put back
        the default of anything removed, log every change and alert on anything refused."""
        cfg = self.cfg
        now_m = time.monotonic()
        if not cfg.overrides_file or (not force and now_m - self.last_overrides_check < cfg.overrides_seconds):
            return
        self.last_overrides_check = now_m
        path = bot_path(cfg.overrides_file)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime == self.overrides_mtime:
            return
        self.overrides_mtime = mtime
        raw = {}
        if mtime is not None:
            try:
                with open(path) as f:
                    raw = json.load(f)
            except (OSError, ValueError) as e:
                alert(f"{cfg.overrides_file} unreadable ({e}) - keeping the current settings")
                return
        good, bad = validate_overrides(raw, cfg)
        if bad:
            alert(f"{cfg.overrides_file}: ignored " + "; ".join(bad))
        wanted = {**{k: self.defaults[k] for k in self.overrides if k not in good}, **good}
        for k, v in wanted.items():
            if getattr(cfg, k) != v:
                log.warning("SETTING %s: %s -> %s%s", k, getattr(cfg, k), v, "" if k in good else " (default again)")
                setattr(cfg, k, v)
                if k == "requests_per_minute":
                    self.api.budget = min(getattr(self.api, "budget", v), v)
                if k == "writes_per_minute":          # the owner asked for this rate: start from it now
                    self.api.wbudget = float(v)
                if k == "writes_per_minute_max":      # a lower ceiling applies at once; a higher one is grown into
                    self.api.wbudget = min(getattr(self.api, "wbudget", v), max(v, cfg.writes_per_minute))
                if k in ("size_min_frac", "size_max_frac", "headline_size_frac", "quote_capital_frac"):
                    self.size_plan_time = -1e9                 # re-plan sizes now
        self.overrides = good
        self.health["overrides"] = dict(good)

    def check_market_edge(self, force=False):
        """Every market_edge_reload_seconds: re-read market_edge.json (rival-floor map) if it changed. Bad entries
        are refused one by one (a file with nothing usable is refused whole); an unreadable file keeps the current
        map; a removed file empties it."""
        cfg = self.cfg
        now_m = time.monotonic()
        if not cfg.market_edge_file or (not force and now_m - self.last_market_edge_check < cfg.market_edge_reload_seconds):
            return
        self.last_market_edge_check = now_m
        path = bot_path(cfg.market_edge_file)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime == self.market_edge_mtime:
            return
        self.market_edge_mtime = mtime
        if mtime is None:
            if self.market_edge:
                log.info("MARKET EDGE %s gone - every market back to min_edge", cfg.market_edge_file)
            self.market_edge = {}
            return
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            log.info("MARKET EDGE %s refused (unreadable: %s) - keeping %d markets", cfg.market_edge_file, e,
                     len(self.market_edge))
            return
        good, bad = validate_market_edge(raw, self.ex)
        if bad and not good:
            log.info("MARKET EDGE %s refused: %s - keeping %d markets", cfg.market_edge_file, "; ".join(bad[:5]),
                     len(self.market_edge))
            return
        self.market_edge = good
        vals = sorted(good.values())
        log.info("MARKET EDGE %s loaded: %d markets%s%s%s", cfg.market_edge_file, len(good),
                 f", {100 * vals[0]:g}-{100 * vals[-1]:g}c" if vals else "",
                 "" if cfg.market_edge_enabled else " (market_edge_enabled is off: not used)",
                 f"; refused {len(bad)}: " + "; ".join(bad[:5]) if bad else "")

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
        if not self.running and not self.handover:
            raise KeyboardInterrupt
        self.handover = False             # a plain stop always cancels, even after a handover request
        log.info("stop requested - finishing the current request, then cancelling all orders (Ctrl+C again to force)")
        self.running = False

    def request_handover(self, *_):
        """SIGUSR1 (deploy): stop like Ctrl+C but leave our quotes resting, so the new version can adopt them
        (see adopt_handover) instead of the market going unquoted for the restart. They expire within order_ttl
        anyway, so a new version that never starts leaves nothing behind for long."""
        log.info("handover requested - stopping without cancelling; the next start adopts the resting orders")
        self.handover = True
        self.running = False
        cap = self.cfg.handover_exit_max_seconds
        if cap > 0 and self.handover_timer is None:
            self.handover_timer = threading.Timer(cap, self.handover_deadline, args=(cap,))
            self.handover_timer.daemon = True
            self.handover_timer.start()

    def handover_deadline(self, cap):
        """Timer thread, handover_exit_max_seconds after a handover request: if the process is still here, exit
        now, so the deploy script (which waits for the old bot to stop) can start the new one. Only while it is
        still a handover (a plain stop or the kill switch since then must cancel; they're never cut short)."""
        if not (self.handover and self.exit_code == 0):
            return
        log.error("handover: still running %.0f s after the request - exiting now (writes still in flight are "
                  "abandoned; the next run re-reads the open-orders list)", cap)
        for h in logging.getLogger().handlers + log.handlers:
            try:
                h.flush()
            except Exception:
                pass
        self.hard_exit(0)

    hard_exit = staticmethod(os._exit)    # no interpreter clean-up: it would wait for the writer threads

    # ------------------------------------------------------------------------------ watchdog
    def start_watchdog(self):
        if self.watchdog_thread is None:
            self.watchdog_thread = threading.Thread(target=self.watchdog_loop, name="watchdog", daemon=True)
            self.watchdog_thread.start()

    def watchdog_loop(self):
        while self.running:
            try:
                self.watchdog_check(time.monotonic())
            except Exception:                     # the watchdog itself must never die quietly
                log.exception("watchdog check failed")
            time.sleep(5.0)

    def watchdog_check(self, now_m):
        """Watchdog thread: alert when no cycle has completed for watchdog_alert_seconds; after
        watchdog_exit_seconds dump every thread's stack, cancel everything (bounded) and exit with EXIT_WATCHDOG
        so systemd restarts the bot. Returns "alert" / "exit" / None (tests)."""
        cfg = self.cfg
        ref = self.last_cycle_done if self.last_cycle_done is not None else self.trading_since
        if ref is None or not self.running:
            return None
        since = now_m - ref
        if since < 1:
            self.watchdog_alerted = False
        if cfg.watchdog_exit_seconds > 0 and since >= cfg.watchdog_exit_seconds:
            self.watchdog_exit(since)
            return "exit"
        if cfg.watchdog_alert_seconds > 0 and since >= cfg.watchdog_alert_seconds and not self.watchdog_alerted:
            self.watchdog_alerted = True
            alert(f"WATCHDOG: no cycle completed for {since:.0f} s (phase: {self.health.get('cycle_phase', '?')}, "
                  f"429 pause {self.api.pause_left() if hasattr(self.api, 'pause_left') else 0:.0f} s left)"
                  + (f" - exiting for a restart at {cfg.watchdog_exit_seconds:.0f} s" if cfg.watchdog_exit_seconds else ""))
            return "alert"
        if since < cfg.watchdog_alert_seconds:
            self.watchdog_alerted = False
        return None

    def watchdog_exit(self, since):
        log.critical("WATCHDOG: no cycle completed for %.0f s - thread stacks follow, then cancel-all and exit %d",
                     since, EXIT_WATCHDOG)
        try:
            frames = sys._current_frames()
            names = {t.ident: t.name for t in threading.enumerate()}
            for ident, frame in frames.items():
                log.critical("thread %s:\n%s", names.get(ident, ident), "".join(traceback.format_stack(frame)[-8:]))
        except Exception:
            pass
        alert(f"WATCHDOG: no cycle for {since:.0f} s - cancelling everything and exiting (exit {EXIT_WATCHDOG}, "
              f"systemd restarts the bot)")
        if self.api.live:
            done = threading.Event()

            def cancel():
                try:
                    self.api.cancel_all(self.tid)
                    log.critical("watchdog: cancel-all sent")
                except Exception as e:
                    log.critical("watchdog: cancel-all failed: %s", e)
                done.set()
            threading.Thread(target=cancel, name="watchdog-cancel", daemon=True).start()
            if not done.wait(self.cfg.watchdog_cancel_seconds):
                log.critical("watchdog: cancel-all still running after %g s - exiting anyway (orders expire "
                             "within %.0f min)", self.cfg.watchdog_cancel_seconds, self.cfg.order_ttl / 60)
        for h in logging.getLogger().handlers + log.handlers:
            try:
                h.flush()
            except Exception:
                pass
        self.hard_exit(EXIT_WATCHDOG)

    def adopt_handover(self):
        """At start: True if the previous run handed over recently (its orders are ours to manage, not cancel)."""
        path = bot_path(self.cfg.handover_file)
        try:
            with open(path) as f:
                info = json.load(f)
            os.remove(path)
        except (OSError, ValueError):
            return False
        age = time.time() - float(info.get("t", 0))
        if not 0 <= age <= self.cfg.handover_max_age:
            log.info("handover file is %.0f s old - too old, starting from a clean slate", age)
            return False
        log.info("handover: adopting the %s orders the previous run left resting", info.get("orders", "?"))
        now_m = time.monotonic()
        for r in info.get("resting") or []:
            try:
                o = Resting(int(r["id"]), str(r["eid"]), bool(r["bid"]), float(r["price"]), float(r["qty"]),
                            parse_ts(r.get("expires")))
            except (KeyError, TypeError, ValueError):
                continue
            self.my_orders[o.order_id] = o
            self.recent_orders[o.order_id] = (o, now_m)      # trusted over a lagging list for the grace period
            if r.get("level") and not (self.order_meta.get(o.order_id) or {}).get("level"):
                # An R3 ladder order whose notes didn't survive: keep its level (else it counts as level 0, and the
                # duplicate on that side is cancelled and re-placed once)
                self.order_meta[o.order_id] = {**(self.order_meta.get(o.order_id) or {
                    "our_side": "bid" if o.is_bid else "ask", "price": o.price, "fv": None, "eid": o.eid}),
                    "level": int(r["level"]), "t": time.time()}
                self.notes_dirty = True
            if r.get("placed"):
                self.placed_qty[o.order_id], self.filled_qty[o.order_id] = float(r["placed"]), float(r["placed"]) - o.qty
        for oid in info.get("recent_cancels") or []:
            self.recent_cancels[int(oid)] = now_m
        self.orders_stale = True                  # first cycle reads the list; sync_orders takes them over
        return True

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
        return self.selftest_finish(eid, self.selftest_run(eid, self.selftest_ttl()))

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

    def selftest_ttl(self):
        """The expiry the self-test checks: order_ttl, or with ttl_tiers_enabled the longest a tier can give."""
        cfg = self.cfg
        if not cfg.ttl_tiers_enabled:
            return cfg.order_ttl
        return max(cfg.order_ttl, min(MAX_ORDER_TTL, max(cfg.order_ttl_busy, cfg.order_ttl_quiet) * (1 + cfg.ttl_jitter_frac)))

    def selftest_run(self, eid, ttl):
        """One test (API calls only, no bot state touched, so it can run on any thread).
        Returns (verdict, problems, ttl): verdict "passed", "failed" or "busy"."""
        verdict, problems = self.selftest_attempt(eid, ttl)
        if verdict == "rejected" and ttl > self.cfg.order_ttl:
            # TTL saver: the longest tier TTL was refused -> the tiers go off (finish) and order_ttl is tried
            log.warning("self-test: %.0f-min orders (TTL tiers) were rejected (%s) - trying order_ttl", ttl / 60,
                        problems[0])
            ttl = self.cfg.order_ttl
            verdict, problems = self.selftest_attempt(eid, ttl)
        if verdict == "rejected" and ttl > 600:
            # The docs allow any future expiry, but if the exchange caps it, fall back rather than stop.
            log.warning("self-test: %.0f-min orders were rejected (%s) - trying 10-min ones", ttl / 60, problems[0])
            ttl = 600.0
            verdict, problems = self.selftest_attempt(eid, ttl)
        return ("failed" if verdict == "rejected" else verdict), problems, ttl

    def selftest_attempt(self, eid, ttl):
        problems, busy, rejected, funds = [], False, False, False
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
                # 3 Oct live: at 100% capital the 1-share test order was refused "Insufficient available funds"
                # (HTTP 400) and the bot stopped twice. No cash free is not an API surprise: retry later (backing
                # off, see selftest_finish) - but only if EVERY order that didn't go through was refused for cash:
                # a funds refusal next to a real rejection is still a rejection.
                bad = [r for r in results if not (r.get("ok") and (r.get("data") or {}).get("orderId") is not None)]
                funds = len(results) == 2 and bool(bad) and all(self.selftest_funds_refusal(r) for r in bad)
                busy = busy or funds
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
        if funds:
            return "funds", problems                      # busy, for lack of free cash (selftest_finish backs off)
        return ("busy" if busy else "rejected" if rejected else "failed"), problems

    SELFTEST_NOT_LISTED = "neither test order is in our open orders"

    @staticmethod
    def selftest_funds_refusal(r):
        """True if one batch result is a refusal for lack of cash ("Insufficient available funds"): the
        account is fully deployed, not an API that behaves differently from what we assume. The message may
        sit in r["data"]["error"] (a string, or a dict with it under message / error / detail) or r["error"]."""
        if not isinstance(r, dict) or r.get("ok"):
            return False
        texts = []

        def add(err):
            if isinstance(err, str):
                texts.append(err)
            elif isinstance(err, dict):
                for k in ("message", "error", "detail"):
                    v = err.get(k)
                    if isinstance(v, str):
                        texts.append(v)
                    elif isinstance(v, dict):
                        add(v)
        data = r.get("data")
        if isinstance(data, dict):
            add(data.get("error"))
        add(r.get("error"))
        return any("insufficient" in t.lower() and "fund" in t.lower() for t in texts)

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
        if verdict == "funds":
            # No free cash: retried after selftest_retry_seconds, doubling each time up to SELFTEST_FUNDS_MAX_WAIT
            # (each try costs writes and wipes our quotes on the test market); one alert per new wait.
            prev = self.selftest_funds_wait
            base = self.cfg.selftest_retry_seconds
            wait = min(prev * 2, max(self.SELFTEST_FUNDS_MAX_WAIT, base)) if prev else base
            self.selftest_funds_wait = wait
            self.selftest_next = time.monotonic() + wait
            log.warning("self-test: refused for lack of free cash (%s) - trying again in %.0f s, quoting meanwhile",
                        problems[0] if problems else "?", wait)
            if wait != prev:
                alert(f"self-test waiting for free cash (insufficient funds), retrying in {wait:.0f} s")
            return False
        self.selftest_funds_wait = 0.0                    # not a funds refusal: the back-off starts over
        if verdict == "passed":
            if self.cfg.ttl_tiers_enabled and ttl < self.selftest_ttl():
                alert(f"orders expiring in {self.selftest_ttl() / 60:.0f} min were rejected: TTL tiers switched off")
                self.cfg.ttl_tiers_enabled = False
            if ttl != self.cfg.order_ttl and ttl < self.cfg.order_ttl:
                alert(f"orders expiring in {self.cfg.order_ttl / 60:.0f} min were rejected but {ttl / 60:.0f}-min "
                      f"ones work - using {ttl / 60:.0f}-min orders from now on")
                self.cfg.order_ttl, self.cfg.refresh_before_expiry = ttl, ttl / 5
            log.info("self-test passed: orders, sell->NO conversion, expiry and cancel all behave as expected")
            self.selftest_passed = True
            # No test runs there any more: that exchange is an ordinary one again (lost-order recovery may lift
            # its hold early; a whole-exchange cancel there no longer bumps cancel_gen).
            self.selftest_eid = None
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

    SELFTEST_FUNDS_MAX_WAIT = 1800.0                      # funds-refusal back-off cap (s)

    def selftest_state(self):
        """For status.json: where the start-up self-test is."""
        if self.selftest_passed:
            return "passed"
        if not self.selftest_needed():
            return "off"
        if self.selftest_funds_wait:
            return "waiting_funds"
        if self.selftest_future is not None:
            return "running"
        return "waiting" if time.monotonic() < self.selftest_next else "pending"

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
        if self.nosell_future is not None or getattr(self, "pairno_future", None) is not None:
            return                                # a sell-NO leg is out: never two tests at once (holds, same market)
        if time.monotonic() >= self.selftest_next:
            self.selftest_eid = self.selftest_start()
            self.selftest_future = self.selftest_pool.submit(self.selftest_run, self.selftest_eid, self.selftest_ttl())

    # --- Package 6: the reduce_no_as_sell self-test leg ---
    def nosell_tick(self):
        """Main loop, after every cycle: with reduce_no_as_sell on (live, self-test on) and not checked yet, once some
        market holds NO, check on a background thread that the exchange takes a covered "sell NO" (nosell_run). Until
        it has passed, NO holdings are reduced as today ("buy YES"). Refused -> alert, and today's behaviour for the
        rest of this run (nosell_state "off"); busy -> tried again selftest_retry_seconds later. Never stops the bot."""
        cfg = self.cfg
        if not (cfg.reduce_no_as_sell and self.api.live and cfg.selftest_enabled) or self.nosell_state is not None:
            return
        f = self.nosell_future
        if f is not None:
            if f.done():
                self.nosell_future = None
                try:
                    verdict, msg = f.result()
                except Exception as e:            # a bug in the test itself: retried later, never fatal
                    verdict, msg = "busy", f"self-test error: {e}"
                self.nosell_finish(verdict, msg)
            return
        if (self.selftest_future is not None or getattr(self, "pairno_future", None) is not None
                or time.monotonic() < self.nosell_next):
            return
        held = [e for e in sorted(self.ex) if self.ex[e].inv <= -1]
        # Note (Package 7): with no_set_aware_bids on, only legs with a LONE NO part are tested. If no market has one
        # (e.g. after a restart every NO is held in NO+NO sets) this check never runs, so it never passes and
        # reduce_no_on() stays False: no_set_aware_bids, pair_no_unwind_max_cost and the follow-up's covered sales
        # stay inert for the run. Logged once (below) when that lasts more than 10 minutes.
        if getattr(cfg, "no_set_aware_bids", False):   # Package 7: a 1-share sale inside a NO+NO set breaks it and
            lone = [e for e in held if -self.ex[e].inv - self.nono_set_part(e) >= 1]   # is refused: lone parts only
            if held and not lone:
                now = time.monotonic()
                since = getattr(self, "nosell_nolone_since", None)
                if since is None:
                    self.nosell_nolone_since = now
                elif now - since > 600 and not getattr(self, "nosell_nolone_logged", False):
                    self.nosell_nolone_logged = True
                    log.warning("reduce_no_as_sell start-up check still waiting after %.0f min: NO is held in %d "
                                "market(s) but none has a lone NO part (all in NO+NO sets), so the check cannot run; "
                                "no_set_aware_bids / pair_no_unwind_max_cost / covered follow-ups stay inert until "
                                "one does", (now - since) / 60, len(held))
            else:
                self.nosell_nolone_since = None
            held = lone
        if not held:
            return                                # nothing to reduce yet: nothing to check
        # The test order is a YES bid at PMIN: only where no other trader's ask (cached book) is at PMIN or below, so
        # it can't fill. No such market (or no book yet) -> skip this tick and look again next time.
        safe = [e for e in held if self.nosell_safe(self.ex[e])]
        if not safe:
            return
        eid = min(safe, key=lambda e: self.ex[e].inv)   # the biggest NO holding that is safe to test on
        self.nosell_eid, self.nosell_hold = eid, self.ex[eid].pending_until
        self.ex[eid].pending_until = float("inf")       # no quoting there while the test order may rest
        self.nosell_future = self.selftest_pool.submit(self.nosell_run, eid)

    @staticmethod
    def nosell_safe(ex):
        """True if the cached book shows no other trader's ask at PMIN or below (an empty ask side counts as safe;
        no book downloaded yet does not)."""
        if ex.book is None:
            return False
        return all(float(l["price"]) > PMIN + 1e-9 for l in (ex.book.get("asks") or []))

    def nosell_test_order(self, eid):
        """ONE 1-share "sell NO @ 0.995" (= our bid at YES 0.005, PMIN): fills only if someone bids 0.995 for NO."""
        return {"exchangeId": eid, "side": "no", "action": "sell", "quantity": 1, "price": round(1 - PMIN, 3),
                "tournamentId": self.tid, "expirationDate": iso(utcnow() + timedelta(seconds=self.cfg.order_ttl))}

    def nosell_run(self, eid):
        """Background thread, API calls only: place the test order, cancel it at once. -> (verdict, message):
        "ok" (accepted), "refused" (the exchange said no) or "busy" (try again later)."""
        try:
            results = self.api.place_batch([self.nosell_test_order(eid)])
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT" or e.status in self.SELFTEST_BUSY:
                return "busy", str(e)
            return "refused", str(e)
        r = (results or [{}])[0]
        data = r.get("data") or {}
        oid = data.get("orderId")
        if oid is not None:
            try:
                if not self.api.cancel_order(oid):
                    self.api.cancel_all(self.tid, eid)
            except ApiError:
                try:
                    self.api.cancel_all(self.tid, eid)
                except ApiError as e:
                    log.warning("self-test (sell NO): could not cancel test order %s (%s) - it expires on its own",
                                oid, e)
        if r.get("ok") and oid is not None:
            return "ok", f"order {oid} accepted and cancelled"
        if r.get("status") in self.SELFTEST_BUSY:
            return "busy", str(r)[:300]
        err = data.get("error") or data
        return "refused", (err.get("message") if isinstance(err, dict) and err.get("message") else str(r))[:300]

    def nosell_finish(self, verdict, msg):
        """Main thread: act on the leg's outcome (see nosell_tick)."""
        ex = self.ex.get(getattr(self, "nosell_eid", None))
        if ex is not None:
            ex.pending_until = self.nosell_hold if self.nosell_hold != float("inf") else 0.0
        self.orders_stale = True                  # the clean-up may have touched our orders there: re-read the list
        if verdict == "ok":
            self.nosell_state = "ok"
            log.info("self-test (sell NO): a covered 'sell NO' was accepted (%s) - NO holdings are now reduced as "
                     "covered NO sales (reduce_no_as_sell)", msg)
        elif verdict == "refused":
            self.nosell_state = "off"
            log.error("self-test (sell NO) refused: %s - reduce_no_as_sell OFF for this run", msg)
            alert(f"reduce_no_as_sell: the exchange refused a covered 'sell NO' ({msg}). NO holdings are reduced as "
                  f"'buy YES' again (today's behaviour) for the rest of this run; the bot keeps running")
        else:
            self.nosell_next = time.monotonic() + self.cfg.selftest_retry_seconds
            log.warning("self-test (sell NO): exchange busy (%s) - trying again in %.0f s", msg,
                        self.cfg.selftest_retry_seconds)

    # --- Package 7: the pair_no_unwind_max_cost self-test leg (a paired covered NO sale) ---
    PAIRNO_MAX_WAIT = 1800.0                      # busy back-off cap (s)

    def pairno_tick(self):
        """Main loop, after every cycle: with pair_no_unwind_max_cost >= 0 and reduce_no_as_sell on (live, self-test
        on), once the sell-NO leg has passed and some race holds NO on every leg, check on a background thread that
        the exchange takes ONE batch of covered "sell NO" orders, 1 share on each leg of that race (pairno_run: the
        sale that closes a whole set). Until it has passed, short sets are bought back only at today's rule.
        Refused -> alert, the paired unwind off for the rest of this run (pairno_state "off"); busy -> tried again
        with a back-off (selftest_retry_seconds doubling up to PAIRNO_MAX_WAIT). Never stops the bot."""
        cfg = self.cfg
        if (getattr(cfg, "pair_no_unwind_max_cost", -1.0) < 0 or not (cfg.reduce_no_as_sell and self.api.live
                                                                       and cfg.selftest_enabled)
                or self.pairno_state is not None):
            return
        f = self.pairno_future
        if f is not None:
            if f.done():
                self.pairno_future = None
                try:
                    verdict, msg = f.result()
                except Exception as e:            # a bug in the test itself: retried later, never fatal
                    verdict, msg = "busy", f"self-test error: {e}"
                self.pairno_finish(verdict, msg)
            return
        if (self.nosell_state != "ok" or self.selftest_future is not None or self.nosell_future is not None
                or time.monotonic() < self.pairno_next):
            return
        cands = []
        for race, members in sorted(self.groups.items()):
            if len(members) < 2 or any(m not in self.ex for m in members):
                continue
            sets = min(-self.ex[m].inv for m in members)
            # every leg: NO held, and no other trader's ask at or below 0.005 (the YES bid at 0.005 cannot fill)
            if sets >= 1 and all(self.nosell_safe(self.ex[m]) for m in members):
                cands.append((len(members) != 2, -sets, race))
        if not cands:
            return                                # no NO+NO set held (or none safe to test on): look again next time
        race = min(cands)[2]                      # a 2-leg race first, the biggest set
        members = list(self.groups[race])
        self.pairno_race = race
        self.pairno_holds = {m: self.ex[m].pending_until for m in members}
        for m in members:
            self.ex[m].pending_until = float("inf")   # no quoting there while the test orders may rest
        self.pairno_future = self.selftest_pool.submit(self.pairno_run, members)

    def pairno_test_orders(self, members):
        """ONE 1-share "sell NO @ 0.995" (= our bid at YES 0.005) per leg: they close one whole NO+NO set."""
        exp = iso(utcnow() + timedelta(seconds=self.cfg.order_ttl))
        return [{"exchangeId": e, "side": "no", "action": "sell", "quantity": 1, "price": round(1 - PMIN, 3),
                 "tournamentId": self.tid, "expirationDate": exp} for e in members]

    def pairno_run(self, members):
        """Background thread, API calls only: place the paired test batch, cancel every accepted order at once.
        -> (verdict, message): "ok" (every leg accepted), "refused" (any leg refused, a funds refusal included:
        the set collateral rule does not let a paired sale go without cash) or "busy" (try again later)."""
        try:
            results = self.api.place_batch(self.pairno_test_orders(members))
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT" or e.status in self.SELFTEST_BUSY:
                return "busy", str(e)
            return "refused", str(e)
        results = list(results or [])
        oids = [((r or {}).get("data") or {}).get("orderId") for r in results]
        for k, oid in enumerate(oids):
            if oid is None:
                continue
            eid = members[k] if k < len(members) else None
            try:
                if not self.api.cancel_order(oid) and eid is not None:
                    self.api.cancel_all(self.tid, eid)
            except ApiError:
                try:
                    if eid is not None:
                        self.api.cancel_all(self.tid, eid)
                except ApiError as e:
                    log.warning("self-test (paired NO sale): could not cancel test order %s (%s) - it expires on "
                                "its own", oid, e)
        if len(results) == len(members) and all(r.get("ok") and o is not None for r, o in zip(results, oids)):
            return "ok", f"orders {oids} accepted and cancelled"
        bad = [r for r in results if not r.get("ok")] or [{"error": "no result for every leg"}]
        if any(self.selftest_funds_refusal(r) for r in bad):
            return "refused", "insufficient funds: " + str(bad[0])[:250]
        if all(r.get("status") in self.SELFTEST_BUSY for r in bad):
            return "busy", str(bad[0])[:300]
        err = ((bad[0].get("data") or {}).get("error") if isinstance(bad[0].get("data"), dict) else None) or bad[0]
        return "refused", (err.get("message") if isinstance(err, dict) and err.get("message") else str(bad[0]))[:300]

    def pairno_finish(self, verdict, msg):
        """Main thread: act on the leg's outcome (see pairno_tick)."""
        for m, hold in (getattr(self, "pairno_holds", None) or {}).items():
            if m in self.ex:
                self.ex[m].pending_until = hold if hold != float("inf") else 0.0
        self.pairno_holds = {}
        self.orders_stale = True                  # the clean-up may have touched our orders there: re-read the list
        if verdict == "ok":
            self.pairno_state, self.pairno_wait = "ok", 0.0
            log.info("self-test (paired NO sale) on %s: accepted (%s) - NO+NO sets are unwound as a pair "
                     "(pair_no_unwind_max_cost)", self.pairno_race, msg)
        elif verdict == "refused":
            self.pairno_state = "off"
            log.error("self-test (paired NO sale) on %s refused: %s - pair_no_unwind_max_cost OFF for this run",
                      self.pairno_race, msg)
            alert(f"the exchange refuses a paired NO sale: NO+NO sets cannot be unwound without cash ({msg}). "
                  f"pair_no_unwind_max_cost is off for the rest of this run; the bot keeps running")
        else:
            prev, base = self.pairno_wait, self.cfg.selftest_retry_seconds
            wait = min(prev * 2, max(self.PAIRNO_MAX_WAIT, base)) if prev else base
            self.pairno_wait = wait
            self.pairno_next = time.monotonic() + wait
            log.warning("self-test (paired NO sale): exchange busy (%s) - trying again in %.0f s", msg, wait)

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
            if not self.error_alerted:        # once per outage: a failed cancel-all used to re-alert every cycle
                alert(f"{what} - pulling all quotes until cycles succeed again")
                self.error_alerted = True
            try:
                # Only "pulled" once the cancel-all reports nothing left: a partial one (207 with orders still
                # resting) is tried again on the next failed cycle instead of giving up for the whole outage.
                if self.cancel_everything():
                    self.pulled_after_errors = True
                else:
                    log.error("cancel-all left orders resting - trying again on the next failed cycle")
            except Exception as e:        # never let the error handler itself crash the bot
                log.error("cancel-all failed too (%s) - orders expire within %.0f min anyway", e, self.cfg.order_ttl / 60)

    def on_cycle_ok(self):
        """A cycle succeeded: clear the error state, and say so once if the outage was alerted."""
        if self.error_alerted:
            alert(f"cycles succeeding again after {self.failed_cycles} failed - quoting resumes")
            self.error_alerted = False
        self.failed_cycles, self.pulled_after_errors = 0, False

    def run(self):
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)   # `systemctl stop` sends this
        if hasattr(signal, "SIGUSR1"):                     # handover restart (deploy): stop WITHOUT cancelling
            signal.signal(signal.SIGUSR1, self.request_handover)
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
                if self.running and self.adopt_handover():
                    pass                                  # keep the previous run's quotes (see request_handover)
                elif self.running:
                    try:
                        left = [o for o in self.api.open_orders(self.tid) if str(o.get("exchangeId")) in self.ex]
                    except ApiError as e:
                        left = None                       # can't tell: cancel to be safe
                        log.warning("open-orders read failed (%s) - cancelling to be safe", e)
                    try:
                        if left == []:
                            log.info("clean slate: no orders resting - nothing to cancel")
                        else:
                            log.info("clean slate: cancelling %s orders left over from before",
                                     len(left) if left is not None else "any")
                            self.cancel_everything()
                    except ApiError as e:
                        # Day one's open: every write timed out. Crashing here (exit 1, restart, same again) helps
                        # nobody: anything left over shows up in the first open-orders read and is managed (or
                        # cancelled) like any other order.
                        log.warning("clean-slate cancel failed (%s) - going on; leftovers get re-read and managed", e)
                        self.orders_stale = True
            self.phase = "trading"
            self.trading_since = time.monotonic()     # startup priming (books first) runs from here
            self.start_watchdog()
            while self.running:
                t0 = time.monotonic()
                try:
                    self.check_overrides()
                    self.check_market_edge()
                    self.maybe_daily_analysis()
                    if t0 - self.last_reload > self.cfg.market_reload_seconds:
                        self.load_markets()
                    self.cycle()
                    if self.running:
                        self.selftest_tick()      # stops the bot (exit code 3) if the API surprises us
                        self.nosell_tick()        # Package 6: covered "sell NO" leg (never stops the bot)
                        self.pairno_tick()        # Package 7: paired NO+NO sale leg (never stops the bot)
                    self.on_cycle_ok()
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
        if getattr(self, "pair_owed", None):          # Package 7: in memory only, never carried over
            log.warning("pair unwind follow-up: owed legs dropped at exit (a restart does not resume them): %s",
                        self.pair_owed_status())
        if not self.api.live:
            log.info("dry run finished (no real orders to cancel)")
            return
        # An order write still in flight could land after the cancel-all and be left resting: drop the queued
        # ones, wait for those already sent, and cancel again below if any are still running after that.
        handover = self.handover and self.exit_code == 0
        # On a handover queued writes still go out (a queued pull must not be lost: the quotes stay resting).
        self.writer.shutdown(wait=False, cancel_futures=not handover)
        self.drain_writes(timeout=(4 if handover else 2) * self.cfg.request_timeout)
        if handover:
            self.notes_dirty = True
            self.save_order_notes()                   # the next run attributes their fills
            now_m = time.monotonic()
            write_json(bot_path(self.cfg.handover_file), {
                "t": time.time(), "orders": len(self.my_orders),
                # Our own record: the new run trusts it like its own for recent_order_grace_seconds, so an order
                # placed moments ago that the open-orders list doesn't show yet is never placed twice.
                "resting": [{"id": o.order_id, "eid": o.eid, "bid": o.is_bid, "price": o.price, "qty": o.qty,
                             "placed": self.placed_qty.get(oid), "expires": iso(o.expires) if o.expires else None,
                             "level": self.order_level(o)}
                            for oid, o in self.my_orders.items()],
                "recent_cancels": [oid for oid, t in self.recent_cancels.items()
                                   if now_m - t <= self.cfg.recent_order_grace_seconds]})
            log.info("handover: %d orders left resting for the next run (they expire within %.0f min)",
                     len(self.my_orders), self.cfg.order_ttl / 60)
            return
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
    elif cmd == "analyze":
        hours = float(sys.argv[2]) if len(sys.argv) > 2 else None
        print("\n".join(analyze(bot_path(CFG.fills_csv), bot_path(CFG.record_file), hours)))
        return
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
