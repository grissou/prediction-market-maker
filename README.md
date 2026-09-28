# Prediction-market market-making bot

A Python bot that makes markets in the **SIG Predictions Cup**: US midterm elections, 237 contracts
across 117 races, traded through the exchange's REST API and realtime feed. It quotes two-sided
prices to earn the bid/ask spread while keeping its inventory close to flat. It takes no view on
who wins.

## How it works

**Event-driven.** The exchange pushes book changes and fills over a WebSocket (Supabase Realtime).
The bot reacts in about 1–2 s (each request takes ~0.4 s from the UK, less from a server in San Francisco)
and sends no requests while nothing changes. A new Polymarket price (every 5 s) also starts a cycle at once. A full check from the REST API
runs every 30 s, because pushed delivery is best-effort. If the feed drops, the bot falls back to
polling every 10 s on its own.

Each cycle the bot:

1. reads positions after a fill, and its open orders only on the 30 s full check: in between it keeps its
   own record of its orders, so its own placements and cancels cost no extra requests;
2. downloads the books reported as changed. When many change at once, one bulk request per 100
   contracts finds which best prices actually moved, and only those books are downloaded;
3. computes a **fair value**: 70% **Polymarket** (the real-money market the tournament is seeded
   from, refreshed every 5 s; used only where its own spread is 3c or less) and 30% the tournament
   book. For the book, price levels are skipped until 200 shares have accumulated, so a 1-share order
   can't move it. The parties in a race are scaled to sum to 1;
4. takes **risk-free arbitrage** when other traders' bids on every party of a race add up to more than 1
   by at least 3c, and
   **trades against stale house quotes** when Polymarket has been 5c+ past them on every reading for 30 s;
5. quotes **one tick inside the best other trader**, never closer than 1c to fair value, with prices
   skewed against its inventory (netted across the parties of each race) and against its net
   Republican-vs-Democrat exposure;
6. compares against the orders actually resting and changes only what differs. An order one tick off
   its target is kept while it's still safe, which saves requests and keeps its place in line.

## Risk controls

| Control | What it does |
|---|---|
| Position sizing | **Quarter Kelly** per market and side, using Polymarket as the true probability, capped at 2% of the account per market. All other sizes are fractions of account value too, so the bot sizes up after gains and down after losses (in 5% steps, so small wobbles don't resize every order) |
| Quote sizes by activity | Capital goes where the trades are: party control of the House and Senate get 10,000-share quotes (10% of the starting balance), busy races up to 2,000, quiet ones 100, ranked by Polymarket volume and then by the tournament's own trades (re-planned every 30 min). All resting quotes together lock at most 60% of the account, and every size scales with the account |
| Kill switch | Stops and cancels everything if account value falls a set % below the starting balance. Needs 2 readings in a row, is corrected for cash locked in open orders, and leaves a marker file so nothing can auto-restart trading |
| Worst-case loss cap | Reduce-only everywhere if the worst settlement outcome would cost more than 30% of the account |
| National-swing cap | Net Republican-vs-Democrat exposure summed over all races: quotes are shaded against it (up to 1.5c), so it sheds while still quoting both sides; at 5% of the account the side that would add to it is blocked |
| Jump guard | Pauses a market after a sudden price move (likely news) |
| Tail guard | Near 0% or 100%, never takes the side that risks ~$1 to earn ~1c (for quotes and for taking stale quotes) |
| Wrong-match guard | Ignores a Polymarket price more than 25c from the tournament book (almost always a wrong match) and sends one alert |
| Outside-price guard | Quotes one side only where Polymarket disagrees with the tournament book by more than 5c |
| Polymarket jump guard | Pulls a market's quotes for 60 s when its Polymarket price jumps 3c+ between readings. Steps back before the tournament book catches up, and ignores spikes that revert |
| Startup self-test | Before quoting live, places and cancels two 1-share orders at extreme prices to check the API behaves as assumed; stops with an alert if not |
| Election night | 12 h before close: reduce only. 6 h: flatten every market on its own. 2 h: exit, trading against other orders if needed (at most 3c from fair). 15 min: nothing. The exit still works if the book gets too thin for a fair value (it uses the last known one, or Polymarket's) |
| Order expiry | Every order expires after 30 min, so quotes vanish even if the bot dies |
| Crash handling | Ctrl+C, errors or a crash cancel all orders; exit codes tell systemd whether a restart is safe |
| Rate-limit budget | Never more than 80 requests a minute (the API allows ~100; measured), with part kept free for orders. A 429 pauses every request and lowers the budget |

## Files

| File | Purpose |
|---|---|
| `mm_bot.py` | The bot. **All tunable settings are in the SETTINGS block at the top** |
| `ref_prices.py` | Outside reference prices from Polymarket/Kalshi, plus tools to match races to their markets |
| `ref_map.json` | Which Polymarket market each tournament contract matches (229 of 237) |
| `deploy/` | systemd service + one-command setup script for an Ubuntu server (firewall, key-only SSH, auto-updates) |
| `tests/` | Offline test suites: a fake exchange that follows the API spec. Run on every push by GitHub Actions |

Created while running (git-ignored): `fills.csv` (every fill), `mm_bot.log`, `status.json` (health),
`market_data.sqlite` (snapshots for tuning), `order_notes.json`, and `kill_switch.tripped` (only if the kill switch fired).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+
printf 'SUPERMARKET_API_KEY=...\nTOURNAMENT_SLUG=midterm-elections\n' > .env   # never commit this
source .venv/bin/activate
```

Optional: `ALERT_URL=https://ntfy.sh/<long-random-name>` in `.env` sends phone alerts (kill switch,
crashes) and a **summary every 2 hours**, on the hour UTC (P&L, rank, Smart Score, fills, edge, bot health) via the
free ntfy app: install it and subscribe to the same topic name.

## Commands

```bash
python mm_bot.py status        # balance, P&L, positions, open orders
python mm_bot.py markets       # every market and its exchange ids
python mm_bot.py run           # dry run: live data, orders only simulated
python mm_bot.py run --live    # trade
python mm_bot.py cancel        # cancel every open order
python mm_bot.py report        # edge and adverse selection from logged fills
python mm_bot.py summary       # the phone summary, now (tests your ALERT_URL)

python ref_prices.py check     # tournament price vs outside price for every contract
python ref_prices.py suggest   # match unmapped races to Polymarket (review the result)
python ref_prices.py search "Alaska Senate"   # find a market id to map by hand
```

## Going live

1. Start `python mm_bot.py run --live` **at least 30 minutes before trading opens**. It downloads every
   order book while it waits, goes quiet in the last minute (saving the request budget), checks every
   second from the start time and quotes within ~1 s of the open, then runs the self-test.
2. On day 1, trade a few markets first (`ONLY_EXCHANGES="1071,1070"` in `.env`), check the orders in
   `status` and the web UI, then remove it.
3. Keep an eye on `status.json`: `"realtime": "connected"`, `"rate_limited_total": 0`, `"last_cycle_ok": true`.
4. Don't run other commands in a loop while the bot is live: they share the same request budget.

## Running 24/7 on a server

```bash
scp -r mm_bot.py ref_prices.py ref_map.json deploy root@SERVER_IP:/root/mmbot-src
ssh -t root@SERVER_IP 'bash /root/mmbot-src/deploy/setup.sh'    # installs, hardens, asks for the key
ssh root@SERVER_IP 'systemctl enable --now mmbot'               # start (restarts on crashes and reboots)
ssh root@SERVER_IP 'journalctl -u mmbot -f'                     # live log
```

The API runs in San Francisco (Vercel `sfo1`), so a server in that region gets the fastest responses.

## Tests

```bash
python tests/test_mm_bot.py && python tests/test_ref_prices.py && python tests/test_stress.py
```

209 offline checks cover quoting, fair value, Kelly sizing, taking stale quotes, election night, the self-test, reconciling orders, fills, arbitrage, every risk guard,
the kill switch, crash and restart behaviour, the rate limiter and the realtime feed. The **stress test**
then runs long randomised sessions against a deliberately flaky exchange (errors on any request, lost
responses, a lagging order list, the feed dropping, election night) and checks after every cycle that the
bot never crashes, never double-quotes or crosses itself, keeps its positions within limits, and recovers
fully once the faults stop (100 seeds passed; CI runs 5). No API key or network needed. GitHub Actions
runs all three on every push.
