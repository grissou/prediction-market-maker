# Automated trading in a play-money election prediction market

**A market-making and value-allocation bot for the SIG Predictions Cup (US midterm elections, October–November 2026)**

Adam Zerouali, University of Bristol. Code, tests and analysis in this repository; the bot has traded
live since the tournament opened on 1 October 2026.

## 1. Summary

The SIG Predictions Cup is a month-long tournament on a play-money exchange: 237 binary contracts across
117 US midterm races (Senate, Governor and House seats, plus "which party controls the House / Senate"),
a continuous limit-order book, a REST API with a realtime feed, no trading fees, and every contract
settled at its election outcome. Each participant starts with 100,000 units.

This project is a Python bot that trades the whole tournament autonomously. It began as a classical
market maker (earn the bid–ask spread, stay close to flat, take no view) and evolved, over the first
week of live trading, into a system that combines four strategies with separate capital:

1. a **value book**: positions bought where the tournament price is far from the real-money market's
   probability, held to settlement;
2. a **capital allocator** that rotates money from the lowest-edge holdings into the highest-edge
   opportunities, under turnover and liquidity constraints;
3. **market making** in the middle of the probability range, funded from a reserve the allocator keeps
   topped up;
4. two **tilt strategies** that trade the tournament's systematic mispricing itself: a passive ladder that
   sells it when it widens, and an armed momentum sleeve that buys it only while it is measurably rising.

The design is driven by three empirical findings from the first days, described in §3: the exchange's
prices carry a stable favourite–longshot bias, settlement at the outcome makes that bias a source of
expected value rather than noise, and the book is crowded with other automated market makers, which
removes most of the spread a pure market maker could earn.

As of 7 October 2026 (day 7 of 35) the account stands at 103.2k by the exchange's own marks, and at
110.4k if positions are valued at the real-money market's probabilities, which is what settlement pays
(§6). Both figures move with the market every hour and neither is a final result.

## 2. The setting

| | |
|---|---|
| Contracts | 237 YES/NO contracts; a race's contracts are the candidates (or parties) in it |
| Prices | 0.005 tick, 0–1; a YES bought at price *q* pays 1 at settlement if the outcome occurs |
| Settlement | At the election result. No interim cash-out other than selling into the book |
| Fees | None |
| Leaderboard | The exchange's account value: cash plus positions at its own "current price", a trade-based mark, not the order book |
| API | REST (orders, books, positions, P&L) plus a WebSocket feed of book changes and fills; roughly 100 requests a minute per key (measured, not published); a 429 carries a 60 s penalty |
| Reference market | Polymarket trades the same races for real money; its mid is used as the estimate of the true probability, with the candidates of a race scaled to sum to 1 (229 of the 237 contracts have a liquid match) |

## 3. Empirical observations that shaped the design

**3.1 The tournament prices carry a favourite–longshot tilt.** Across the cross-section of markets the
tournament's mid price *m* relates to the reference probability *r* approximately as

  *m* ≈ *c* + (1 − *s*)(*r* − *c*),  *c* = 1 / (number of candidates in the race),

i.e. prices are shrunk toward the uniform prior by a factor *s*. The bot estimates *s* every cycle by
cross-sectional regression (`TiltEstimator`; `tilt_s` in `status.json`). It rose from 0.046 on 2 October
to 0.128 on 4 October, fell to 0.078 by 6 October and is rising again at the time of writing (0.092).
Longshots trade too dear and favourites too cheap; a 2% candidate can trade at 10–15c, a 97% candidate at
85–88c. The bias is persistent over days and moves slowly, which makes it both a source of expected
value (short the longshots, hold the favourites to settlement) and a source of mark-to-market drawdowns
(a rising *s* marks that very book down).

**3.2 Settlement at the outcome changes what "P&L" means.** The leaderboard values positions at the
exchange's marks; settlement values them at the outcome. A position bought at 85c in a candidate whose
real-money probability is 97% has an expected settlement value of 97c whatever the tournament mark does
in between. The bot therefore reports two numbers side by side: the account value at the exchange's
marks (what the leaderboard shows) and `ev_outcome`, cash plus positions valued at the reference
probabilities (what settlement is expected to pay). Early on, a mark-driven exit rule sold most of the
value book at compressed prices during a tilt rise and gave up about 3.7k of expected value in four
hours; the value floor in §4.3 exists because of that.

**3.3 The book is crowded with other bots.** Within hours of the open, several participants were
quoting algorithmically: one-tick undercutting, instant re-quotes, and resting size at every level.
A measured fill-by-fill edge analysis (`mm_bot.py analyze`, `analysis/markout2.py`) showed the realised
spread of pure market making to be small (tens of units a day on a 20k reserve) and the inventory it
left behind to be held for a median of seven hours. Market making is kept, but as the smallest layer.

**3.4 Capital is the binding constraint.** Each short of a longshot locks (1 − price) per share of
collateral; a value book of favourites locks their price. By day 3 over 90% of the account sat in
positions, and every strategy competed for the remainder. Much of the design below is about which
strategy gets the next unit of cash, and at what price it may sell something to get it.

## 4. Method

### 4.1 Fair value

Each market's fair value is the race-scaled Polymarket probability *p*, used only where Polymarket's own
spread is tight (≤ 3c). Markets without a liquid reference fall back to the tournament book and are
reported as "unpriced" (typically 15–25 of 237). Earlier versions blended 70% reference and 30% book;
the blend weight is a setting (`ref_weight`, now 1.0).

### 4.2 Edge per unit of collateral

Every opportunity, held or on the book, is scored by the expected gain at settlement per unit of cash it
ties up:

| Position | Edge per unit of collateral |
|---|---|
| Buy YES at ask *a* | (*p* − *a*) / *a* |
| Sell YES short at bid *b* | (*b* − *p*) / (1 − *b*) |
| A long already held, best bid *b* | (*p* − *b*) / *b* (what is kept by not selling) |
| A short already held, best ask *a* | (*a* − *p*) / (1 − *a*) |

This single scale lets the allocator compare selling one thing against buying another, and lets every
adding strategy apply a common hurdle (8% for the ladder, 12% for the momentum sleeve's candidate set).

### 4.3 The layers

**Value book and the value floor.** The bot never rests an order that reduces a position below its
value: a long's ask is at least *p* − *m*, a short's buy-back at most *p* + *m*, with *m* =
`value_sell_margin` (0.5c by default; 4c on the live bot at the time of writing, chosen to fund the
other layers; §6.3 discusses the cost). The floor applies in normal and in reduce-only quoting and only
ever moves a price away from the other side, so it never crosses a trader. Exceptions are explicit and
separately bounded: allocator swaps (below) and the momentum sleeve's exits.

**Allocator.** Once an hour, on fresh books and a fresh cash read, the allocator pairs the lowest-edge
holdings with the highest-edge levels on the book and executes each pair as two immediate-or-cancel
orders at the touch: the sale first, then, after a cash read that shows the proceeds, the buy. A pair must
improve edge by at least 5 points net; a swap's sale may go up to 3c below value only when that hurdle is
met (`alloc_swap_sell_margin`, `alloc_swap_min_gain`). Rotated capital is capped per rolling hour; no
position is flipped; headline party-control markets and pinned markets are never sold. The same machinery
refills the market-making reserve when cash falls below it, selling lowest-edge holdings first, at or above
the floor. Two-candidate races use the cheaper of "buy A" and "short B" (`alloc_prefer_short`), which pay
the same at settlement but lock different collateral.

**Market making.** Two-sided quotes in the 15–85% range, one tick inside the best other trader and
never closer than 1c to fair value, skewed against inventory (netted across the candidates of a race)
and against net Republican-vs-Democrat exposure. Sizes are quarter-Kelly against *p*, capped per market,
and scale with the account. Inventory older than six hours or above 3k per market is recycled at a 1c
concession; inventory that has acquired value edge is handed to the value book instead. Market making is
funded by a 20k reserve: a cash target and a risk-room target below which value buying pauses, so the
value book cannot starve it (it has, in practice, been starved by price instead: §6.3).

**Harvest ladder.** For each longshot (*p* ≤ 0.10) resting asks at the best other ask + 0 / 2 / 4 / 6c,
for each favourite (*p* ≥ 0.90) resting bids at the best other bid − 0 / 2 / 4 / 6c, each level 3k of
collateral and only where its edge per unit is at least 8%. It sells the tilt only at prices better than
today's, so it costs nothing if the tilt does not move and earns in tranches if it widens. It has its own
10k budget carved out of the market-making reserve and never crosses the bot's own quotes.

**Momentum sleeve (armed).** The tilt moves slowly and with momentum, so a rising *s* is briefly
predictable. The sleeve estimates *s* every minute from other traders' prices only (own orders stripped,
spread ≤ 6c), in 4-hour bins, and fits a 24-hour slope and a 6-hour slope. It switches on only when the
24-hour slope is at least +0.5 points/day and the 6-hour slope positive, both held for four hours; it
then buys longshots in the 4–25% range whose short-edge is below 12% (those most exposed to a further
rise), 10k at a time while the rise continues, up to a cap. It exits over six hours when the 24-hour
slope turns non-positive, at +25% on cost, or at −25% (a kill switch), after which the ladder takes over
those markets. It never buys in a race where the bot has or plans a sale ("no fake buying"). Its expected
return is modest and uncertain (the analysis in `analysis/p15/TILT_PATHS.md` puts the unconditional
long-tilt bet at roughly +7% with a 35% chance of a loss of a third or more), which is why it is armed
rather than forced, and capped at 10k on the live bot.

### 4.4 Risk model

| Control | Rule |
|---|---|
| Correlated worst case | Settlement risk = a national swing shock across all races plus a multiple of the standard deviation of the rest; above 40% of the account the bot is reduce-only everywhere. The naive sum of per-race maxima is kept as a backstop |
| Per-market and per-state caps | 10k per market; 15k of collateral per state across all adding paths (a cap introduced when three Rhode Island shorts reached 27k, 26% of the account, in one night of selling to a persistent longshot buyer) |
| Cash gate | Every order is checked against the cash the exchange reports free; reducing a NO holding is sent as a covered "sell NO", which needs none |
| Kill switch | Stop and cancel everything if account value falls a set fraction below the start; a marker file prevents automatic restarts |
| Guards | Pause after a sudden move; never risk ~1 to earn ~1c at the extremes; ignore a reference price far from the book (a mismatch); quote one side only where reference and book disagree |
| Order expiry | Every order expires on its own; quotes vanish if the bot dies |
| Watchdog | No completed cycle for 10 minutes: log every thread, cancel everything, exit for systemd to restart. It fired once, during a 20-minute exchange outage on 7 October, and recovered cleanly |

## 5. Engineering

**Event-driven loop.** Book changes and fills arrive over the WebSocket; a cycle runs within 1–2 s of an
event, or of a new reference price, and sends nothing while nothing changes. A full reconciliation from
the REST API runs every 30 s because pushed delivery is best-effort; a dead socket is detected and
reconnected. Orders are tracked locally between reconciliations so the bot's own placements and cancels
cost no extra requests.

**Request and write budgets.** At most 80 requests a minute, with a separate budget of 28 order writes a
minute (one per batch, cancel-all or delete), tuned down from 45 after the first 429s. An unplaceable
order is deferred, never dropped silently; a 429 pauses every request. Measuring the exchange's limit,
rather than assuming the published one, was itself a day-one task.

**Live settings without restarts.** `settings_override.json` next to the bot is re-read every 30 s. Only
names in an explicit whitelist (`OVERRIDABLE`) are accepted, each range-checked and logged
(`SETTING name: old -> new`); a removed key returns to its default. Every strategy above ships OFF by
default and is switched on through this file.

**Code deployment without pulling quotes.** A handover restart (SIGUSR1) makes the old process exit
without cancelling and the new one adopt the resting orders, so a code change costs no queue position.

**Release discipline.** New behaviour arrives as a numbered release ("package") with: every new setting
OFF by default; a test that pins the code with the flags off byte-identical in its orders, quotes and
status to the previous release; a dry run replaying a recorded live snapshot; a staged settings file; and
a note under `deploy/package<N>/` with the first-hour watch list and the rollback. Sixteen numbered releases were built between 2 and 6 October; twelve went live (with hot-fix revisions), one at a time, each
after the previous one had run for a few hours.

**Testing.** 56 offline suites (about 4,000 checks) run against a fake exchange that follows the API
specification, including a stress test of long randomised sessions against a deliberately faulty exchange
(errors on any request, lost responses, a lagging order list, a dropping feed) that checks after every
cycle that the bot never crashes, never crosses itself, keeps positions within limits and recovers once
the faults stop. Two simulators (`tests/strategy_sim.py`, a crowded book with rival bots;
`tests/live_sim.py`, starting from the real recorded book) were used to rank strategy ideas before
building them; several ideas that looked good by intuition ranked negative there and were dropped.

**Development process.** The code was written with AI coding assistants (Claude Code), including
parallel agents that explored ideas, built releases and reviewed each other's work, under the author's
direction; every design decision, deployment and risk setting was the author's, and nothing changed on
the live server without an explicit instruction.

## 6. Evaluation

### 6.1 What is measured

`status.json` is rewritten after every cycle and the recorder (`market_data.sqlite`) stores every book,
reference price and position over time, so every decision can be replayed. The key series:

- account value at the exchange's marks (the leaderboard);
- `ev_outcome`: cash plus positions at reference probabilities, with a 24-hour delta;
- realised P&L, and per-fill class (market making in the middle band, value adds, takes, swaps, refills,
  recycling, ladder, momentum) with edge at fill and 24-hour markouts;
- the tilt *s*, its 24-hour and 6-hour slopes, and the bot's exposure to it (units per point).

### 6.2 Results to date (7 October 2026, 14:00 UTC)

| | |
|---|---|
| Account value (exchange marks) | 103.2k, +3.2% on 100k; range so far 100.2k–103.8k |
| Expected value at settlement (`ev_outcome`) | 110.4k, +10.4%; up from 104.8k on 4 October, when the value floor went live |
| Realised P&L | +0.8k |
| Capital in positions | 89% |
| Tilt exposure | about 38k, i.e. the marks move about −380 per +1 point of *s* |

The gap between the two valuations is the tilt: the book is short longshots and long favourites, which
the exchange marks at tilted prices. Which number the final ranking rewards depends on whether the
tournament settles before the leaderboard closes; the tournament ends on 4 November, the election day.

### 6.3 Costs and failure modes observed

- Funding is paid for in expected value. Raising the value floor's margin from 1c to 4c on 7 October
  freed about 14k of cash at a cost of about 470 of expected value (3.4%), measured directly from the
  fills. With the margin at 1c, nothing in the value book was sellable near fair value.
- The fast refill, enabled with that wider margin, retried unfilled immediate-or-cancel sales every cycle
  and saturated the request budget, starving the market maker of quotes for two hours. It was turned off
  and the retry needs a per-market cool-down.
- Mark-to-market drawdowns during tilt rises (−0.8k overnight on 5–6 October, two-thirds of it in one
  state) are expected under the value strategy; the state cap limits their concentration.
- Market making earned 4–80 units a day depending on how much cash it had, which confirms §3.3.

## 7. Limitations

- The reference probability is Polymarket's price, assumed calibrated. Where it is thin or the match is
  wrong (eight contracts have no match), the bot is trading its own model error.
- `ev_outcome` is an expectation, not a distribution. A settlement-time loss distribution requires the
  correlated model in §4.4 run across the full book, which exists for the risk cap but is not reported as
  a confidence interval.
- The tilt strategies are calibrated on six days of tilt history. The momentum rule has not yet fired.
- The simulators model rival bots crudely (rule-based undercutters); live behaviour has been more
  adversarial than simulated in some markets (persistent longshot buyers) and less in others.
- The tournament is play money. Participants' incentives (rank, not wealth) plausibly cause the tilt
  (longshots are lottery tickets for rank); the result may not transfer to real-money markets.

## 8. Fair play

The bot places only orders it intends to fill, never trades with itself, runs on one account, and stays
within the exchange's measured rate limit. Fair-play constraints (no spoofing, wash trading, collusion,
multiple accounts or exploitation of exchange bugs) were part of every design brief.

## 9. Repository guide

| Path | Contents |
|---|---|
| `mm_bot.py` | The bot. Every tunable is in the `Config` block with its documentation; `OVERRIDABLE` lists the live-changeable ones |
| `ref_prices.py`, `ref_map.json` | Reference prices from Polymarket (and Kalshi) and the race-to-market mapping |
| `tests/` | The 56 suites, the two simulators and the stress test (`python tests/test_*.py`; `STRESS_LADDER=1 python tests/test_stress.py`) |
| `analysis/` | The research behind each release: valuation, tilt paths, turnover, markouts, rival floors, dry-run reports (`analysis/p<N>/`) |
| `deploy/` | systemd unit, server setup, handover restart, `RUNBOOK.md`, and each release's staged settings and notes |

Dry-run suites (`tests/test_p*_dryrun.py`) replay a recorded snapshot: set `P9_SNAP` to a folder holding
`md.sqlite` (from an `ops-snapshot-*` branch's `market_data.sql.gz`) and the snapshot's `status.json`,
`order_notes.json`, `position_lots.json` and `settings_override.json`; without it they skip.

### Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+
printf 'SUPERMARKET_API_KEY=...\nTOURNAMENT_SLUG=midterm-elections\n' > .env
source .venv/bin/activate
python mm_bot.py status        # balance, P&L, positions, open orders
python mm_bot.py run           # dry run: live data, orders only simulated
python mm_bot.py run --live    # trade
python mm_bot.py analyze 24    # per-market edge, markouts, P&L from local files
python ref_prices.py check     # tournament price vs reference for every contract
```

On a server: `deploy/setup.sh` installs and hardens an Ubuntu host, `systemctl enable --now mmbot` runs
the bot under systemd (restarting on crashes and reboots), `journalctl -u mmbot -f` tails the log. An
optional `ALERT_URL` (ntfy) sends phone alerts and a summary every two hours. Files the bot writes
(`fills.csv`, `status.json`, `market_data.sqlite`, `order_notes.json`, `position_lots.json`, the log)
are git-ignored; `settings_override.json` lives only on the server, with the staged copies under
`deploy/package<N>/` as the record of what was switched on.
