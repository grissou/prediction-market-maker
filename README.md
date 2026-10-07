# Prediction-market trading bot

A Python bot for the **SIG Predictions Cup**: US midterm elections, 237 contracts across 117 races,
traded through the exchange's REST API and realtime feed, play-money, 1 October to 4 November 2026.

It started as a pure market maker (earn the spread, stay flat). After the first days it became clear
that the tournament settles every contract at the **outcome**, that there are no fees, and that the
tournament's prices sit systematically away from the real-money market (a favourite-longshot "tilt":
longshots too dear, favourites too cheap). The bot now runs several strategies side by side, each with
its own capital, all priced off **Polymarket** as the estimate of the true probability:

| Layer | What it does | Capital |
|---|---|---|
| **Value book** | Holds positions bought toward Polymarket's price and never sells them below it. Pays off at the outcome | Most of the account |
| **Allocator** | Hourly: rotates capital from the lowest-edge holdings into the highest-edge levels on the book ("swaps"), and refills the market-making reserve | — |
| **Market making** | Two-sided quotes in the middle of the range (15–85%), inventory recycled when stale | A 20k reserve (cash + risk room) |
| **Harvest ladder** | Resting orders that sell the tilt *above* today's prices: asks over longshots, bids under favourites, 0/2/4/6c away, only at ≥ 8% edge per $ | 10k carved out of the reserve |
| **Momentum sleeve** | Armed, not on: buys longshots only if the tilt is measurably rising (24-h slope ≥ +0.5 pt/day and 6-h slope > 0, held 4 h), in 10k steps, and sells them back when the slope turns, at +25%, or at −25% | Up to `momentum_max_usd` |

The live state of each layer is in `status.json` (`alloc`, `mm_funding`, `mm_risk_room`, `harvest`,
`state_caps`, `momentum`, `ev_outcome`, `mm_carry_24h`) and on the 2-hourly phone summary.

## How a cycle works

**Event-driven.** The exchange pushes book changes and fills over a WebSocket (Supabase Realtime). The
bot reacts in about 1–2 s and sends nothing while nothing changes. A new Polymarket reading (every 5 s)
also starts a cycle. A full check from the REST API runs every 30 s because pushed delivery is
best-effort; if the feed drops, it polls every 10 s and reconnects itself.

Each cycle:

1. reads positions after a fill and its open orders on the 30 s full check; in between it keeps its own
   record of its orders, so placements and cancels cost no extra requests;
2. downloads the books reported as changed (one bulk request per 100 contracts finds which best prices
   actually moved);
3. computes each market's **fair value** from Polymarket, with the parties of a race scaled to sum to 1,
   used only where Polymarket's own spread is tight; markets without a liquid reference fall back to the
   tournament book and are reported as "unpriced";
4. runs the traders in order: the momentum sleeve (when on), stale-quote **takes** (Polymarket 5c+ past a
   resting house quote for 30 s), the allocator (its run spans cycles: each sale is an immediate-or-cancel
   at the touch, each buy waits for a cash read that shows the money), the harvest ladder;
5. **quotes** one tick inside the best other trader, never closer than 1c to fair value, skewed against
   inventory (netted across the parties of a race) and against the net Republican-vs-Democrat exposure.
   The side that *reduces* a position never rests below value (`value_sell_margin`), so the bot cannot be
   panicked out of a good position by the tilt;
6. compares with the orders actually resting and changes only what differs, within the write budget.

## Risk controls

| Control | What it does |
|---|---|
| Value floor | A reducing order never rests more than `value_sell_margin` below Polymarket's price (above it for a short). Allocator swaps have their own floor (`alloc_swap_sell_margin`) and must gain ≥ `alloc_swap_min_gain` per $ net |
| Correlated worst case | Settlement risk from a national swing shock plus the rest (`risk_model` "correlated"); above `max_worst_case_frac` of the account the bot is reduce-only everywhere. The old sum-of-maxima worst case is a backstop (`worst_case_backstop_frac`) |
| Market-making room | Value buying pauses while the risk room or cash left for market making is below its reserve (`mm_risk_reserve_wc`, `alloc_mm_reserve`), with hysteresis |
| Per-market and per-state caps | `alloc_max_contract_usd` per market; `state_max_usd` of collateral per state across every adding path (existing positions are kept, adds stop) |
| Cash gate | Every order is checked against the cash the exchange actually shows free; reducing a NO holding goes out as a covered "sell NO", which needs no cash |
| Position sizing | Quarter Kelly per market and side against Polymarket's probability, capped per market; sizes scale with the account; the adding side shrinks (`capital_ceiling_adding_size_factor`) when most capital is in positions |
| Kill switch | Stops and cancels everything if account value falls a set % below the start; leaves a marker file so nothing auto-restarts |
| Jump, tail, wrong-match and outside-price guards | Pause after sudden moves; never risk ~$1 to earn ~1c near 0 or 100%; ignore a Polymarket price far from the book; quote one side only where the two disagree |
| Startup self-test | Places and cancels 1-share orders (including a covered "sell NO") to check the API behaves as assumed; stops with an alert if not |
| Order expiry | Every order expires on its own, so quotes vanish even if the bot dies |
| Watchdog and crash handling | No cycle for 10 minutes → cancel everything and exit for systemd to restart; errors cancel all orders; exit codes tell systemd whether a restart is safe |
| Rate-limit budgets | Never more than 80 requests a minute, with a separate budget of 28 order writes a minute; a 429 pauses everything and lowers the budget |

## Operating the live bot

**Settings without a restart.** `settings_override.json` next to the bot (one JSON object) is re-read
every 30 s. Only the names in `OVERRIDABLE` are accepted, each range-checked; a refused key is reported
once; every change is logged as `SETTING name: old -> new`. Removing a key returns the default. The live
file is the Team's staged file for the current package (`deploy/package16/`) with the owner's changes.

**Code without pulling quotes.** `deploy/handover-restart.sh` sends SIGUSR1: the old bot exits without
cancelling and the new one adopts the resting orders. A plain `systemctl restart` cancels everything.
The owner's recipe is in `deploy/RUNBOOK.md`: back up, stage, compile with the server's Python, copy in,
handover, watch `journalctl -u mmbot -f` for the self-test, then switch the new settings on.

**Packages.** New behaviour arrives as a numbered package: every new setting is OFF by default, the
code with the flags off is pinned byte-identical to the previous package by tests, and the package ships
with a staged settings file, a dry run on a live snapshot and a README under `deploy/package<N>/`.
Packages 5–16 are on the branch history; 14 (market-making funding), 15 (harvest ladder and state cap)
and 16 (armed momentum sleeve) are the ones that shape the bot today.

## Files

| File | Purpose |
|---|---|
| `mm_bot.py` | The bot. Every tunable is in the `Config` block, with its documentation; `OVERRIDABLE` lists the live-changeable ones |
| `ref_prices.py`, `ref_map.json` | Polymarket/Kalshi reference prices and the race-to-market mapping (229 of 237 matched) |
| `deploy/` | systemd unit, server setup script, handover restart, `RUNBOOK.md`, and each package's staged settings and notes |
| `tests/` | 56 offline suites against a fake exchange that follows the API spec, plus two simulators (`strategy_sim.py`, `live_sim.py`) and a stress test |
| `analysis/` | The research behind each package: valuation, tilt paths, turnover, markouts, rival floors, dry-run reports |

Created while running (git-ignored): `fills.csv`, `mm_bot.log`, `status.json`, `order_notes.json`,
`position_lots.json`, `market_data.sqlite` (books, prices and positions over time: the seed for every
dry run), and `kill_switch.tripped` if the kill switch fired. `settings_override.json` lives only on the
server; the staged copies under `deploy/package<N>/` are the record of what was switched on.

## Setup and commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+
printf 'SUPERMARKET_API_KEY=...\nTOURNAMENT_SLUG=midterm-elections\n' > .env   # never commit this
source .venv/bin/activate

python mm_bot.py status        # balance, P&L, positions, open orders
python mm_bot.py run           # dry run: live data, orders only simulated
python mm_bot.py run --live    # trade
python mm_bot.py cancel        # cancel every open order
python mm_bot.py report        # edge and adverse selection from logged fills
python mm_bot.py analyze 24    # per-market edge, markouts, P&L from local files
python mm_bot.py summary       # the phone summary, now
python ref_prices.py check     # tournament price vs Polymarket for every contract
```

Optional: `ALERT_URL=https://ntfy.sh/<long-random-name>` in `.env` sends phone alerts and a summary
every 2 hours. Don't run other commands in a loop while the bot is live: they share its request budget.

## Running 24/7 on a server

```bash
scp -r mm_bot.py ref_prices.py ref_map.json deploy root@SERVER_IP:/root/mmbot-src
ssh -t root@SERVER_IP 'bash /root/mmbot-src/deploy/setup.sh'    # installs, hardens, asks for the key
ssh root@SERVER_IP 'systemctl enable --now mmbot'               # restarts on crashes and reboots
ssh root@SERVER_IP 'journalctl -u mmbot -f'                     # live log
```

The API runs in San Francisco (Vercel `sfo1`); a server in that region gets the fastest responses.

## Tests

```bash
for t in tests/test_*.py; do python "$t" | tail -1; done      # 56 suites, about 3 minutes in parallel
STRESS_LADDER=1 python tests/test_stress.py                    # long randomised sessions against a flaky exchange
```

The suites cover quoting, fair value, sizing, takes, arbitrage, every guard, the kill switch, crash and
restart behaviour, the rate limiter, the realtime feed, the value floor, the allocator, market-making
funding, the ladder, the state cap and the momentum sleeve, and pin each package's flags-off behaviour to
the previous one. The dry-run suites (`test_p*_dryrun.py`) replay a recorded live snapshot: point
`P9_SNAP` at a folder holding `md.sqlite` (built from an `ops-snapshot-*` branch's `market_data.sql.gz`)
and the snapshot's `status.json`, `order_notes.json`, `position_lots.json` and `settings_override.json`;
without it they skip. Three checks look for a `python3.10` binary and fail on a machine without one.
