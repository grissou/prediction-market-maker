#!/usr/bin/env python3
"""
Trading bot for the SIG Predictions Cup (The Super Market API, Midterm Elections tournament).

WHAT IT DOES
    Values every contract at Polymarket's probability p (scaled so a race sums to 1) and treats settlement at the
    election result as the payoff. Four strategies share the account: a VALUE BOOK of positions bought toward p and
    never sold below it (the value floor); an ALLOCATOR that, hourly, sells the holdings with the least edge per
    unit of cash and buys the levels with the most; MARKET MAKING in the 15-85% band with its own cash reserve; and
    a HARVEST LADDER of resting orders that sells the tournament's favourite-longshot tilt only at prices better
    than today's. A correlated worst-case cap, per-market and per-state caps and a cash gate bound the risk.
    Live settings come from settings_override.json (re-read every 30 s; see mmbot/config.py OVERRIDABLE).

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
    ref_prices.py + ref_map.json   reference prices from Polymarket (see ref_prices.py);
                                   used automatically when both exist, ignored otherwise

WHERE THE CODE IS (the mmbot/ package next to this file; this file only re-exports it and runs main)
    mmbot/config.py    SETTINGS: every number you might want to change (Config), the override whitelist
    mmbot/util.py      clock, paths, alerts, the logger, the exchange's fixed facts (TICK, exit codes)
    mmbot/exchange.py  Api (HTTP), RealtimeFeed (push), the per-market state (Ex) and order changes
    mmbot/pricing.py   fair value, clean books, tilt, race/bloc risk maths     mmbot/quoting.py  one quote
    mmbot/measure.py   fills.csv statistics, the phone summary, report / analyze
    mmbot/bot.py       the Bot: state, the cycle, decide, order management; its concerns are mixins:
    mmbot/risk.py (kill switch, lots, cash gate, state caps)  mmbot/value.py (allocator, set ladder)
    mmbot/mm.py (MM funding, swaps)  mmbot/ladder.py (harvest ladder)  mmbot/arb.py (arbitrage, takes)
    mmbot/status.py (status.json, recorder, overrides file)  mmbot/ops.py (handover, watchdog, self-tests, run)
    mmbot/cli.py       main: the commands above
"""

from mmbot import *  # noqa: F401,F403
from mmbot import util, config, exchange, pricing, quoting, measure  # noqa: F401
from mmbot import risk, value, mm, ladder, arb, status, ops, bot, cli  # noqa: F401
from mmbot.exchange import _SocketErrorWatch  # noqa: F401
from mmbot.pricing import _STD_NORMAL  # noqa: F401
from mmbot.cli import main

if __name__ == "__main__":
    main()
