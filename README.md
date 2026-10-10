# Automated trading in a play-money election prediction market

**A trading bot for the SIG Predictions Cup (US midterm elections, October–November 2026)**

Adam Zerouali, University of Bristol. This repository holds the code, the tests and the analysis. The
bot has traded live since the tournament opened on 1 October 2026.

## 1. Summary

The SIG Predictions Cup is a month-long trading tournament. Participants start with 100,000 play-money
units and trade 237 contracts on US midterm election races through an exchange with an order book and
an API. There are no fees. Every contract pays out at the election result.

This project is a Python bot that trades the whole tournament on its own. It began as a classical
market maker: quote both sides of every market, earn the gap between bid and ask, stay close to flat,
take no view on who wins. Within a week of live trading that approach had been overtaken by three
findings (§3):

- the tournament's prices are systematically biased: longshots are too dear and favourites too cheap;
- because every contract pays out at the result, that bias is a source of profit rather than noise;
- the order book is full of other trading bots, which leaves very little spread to earn.

The bot now runs four strategies side by side, each with its own capital:

| Strategy | In one sentence |
|---|---|
| **Value book** | Buy where the tournament price is far from the real-money market's probability, and hold to the result |
| **Allocator** | Once an hour, sell the holdings with the least edge left and buy the opportunities with the most |
| **Market making** | Quote both sides in the middle of the probability range, with a reserve of cash the allocator keeps topped up |
| **Tilt strategies** | Trade the bias itself: a passive ladder that sells it when it widens, and a momentum rule that buys it only while it is measurably rising |

On 7 October 2026, day 7 of 35, the account was worth 103.2k by the exchange's own prices, and 110.4k
if positions are valued at the real-money market's probabilities, which is what the payout at the result
is expected to be (§6). Both numbers move every hour. Neither is a final result.

### Key terms

- **Polymarket**: a real-money prediction market that trades the same races. Its price is used as the
  estimate of the true probability of each outcome, written *p* below. 229 of the 237 contracts have a
  usable match.
- **Mark**: the price the exchange uses to value a position on its leaderboard. It is based on recent
  trades, not on the order book, and it carries the tournament's bias.
- **Expected value at the result**: cash plus every position valued at *p*. This is what the account is
  expected to be worth when the races settle. The bot reports it as `ev_outcome`.
- **Edge**: the expected gain per unit of cash a position ties up, if held to the result (§4.2).
- **The tilt**: the tournament's favourite–longshot bias, measured by one number *s* (§3.1).

## 2. The setting

| | |
|---|---|
| Contracts | 237 YES/NO contracts across 117 races (Senate, Governor and House seats, plus which party controls the House and the Senate). A race's contracts are its candidates |
| Prices | Between 0 and 1, in steps of 0.005. A YES bought at 0.30 pays 1 if the candidate wins and 0 otherwise |
| Payout | At the election result. The only earlier way out is to sell into the order book |
| Fees | None |
| Leaderboard | Cash plus positions at the exchange's marks |
| API | REST calls for orders, books, positions and P&L, plus a live feed of book changes and fills. About 100 requests a minute are allowed per account; this was measured, not published. Going over it blocks all requests for 60 seconds |

## 3. What the first days showed

**3.1 The prices carry a favourite–longshot tilt.** Across all markets, the tournament's mid price *m*
relates to Polymarket's probability *r* roughly as

  *m* ≈ *c* + (1 − *s*)(*r* − *c*),  where *c* = 1 / (number of candidates in the race).

In words: prices are pulled toward "every candidate equally likely" by a factor *s*. A candidate with a
2% chance trades at 10–15c; one with a 97% chance trades at 85–88c. The bot estimates *s* every cycle
from the whole cross-section of markets. It rose from 0.046 on 2 October to 0.128 on 4 October, fell to
0.078 by 6 October, and was rising again at the time of writing (0.092). It moves over days, not minutes.

This matters in two opposite ways. Shorting longshots and holding favourites to the result earns the
tilt. But while those positions are held, a rising tilt marks them *down* on the leaderboard.

**3.2 Payout at the result changes what "profit" means.** A position bought at 85c in a candidate
with a 97% chance is expected to pay 97c, whatever the leaderboard says in the meantime. So the bot
tracks two numbers: account value at the marks (the leaderboard) and expected value at the result. Early
on, a rule that sold positions when their marks fell gave away about 3.7k of expected value in four hours
during a tilt rise. The value floor in §4.3 exists because of that.

**3.3 The book is crowded with bots.** Within hours of the open several participants were quoting
algorithmically: undercutting by one tick, re-quoting instantly, resting size at every level. Measured
fill by fill, pure market making earned tens of units a day on a 20k reserve, and the inventory it left
behind was held for a median of seven hours. Market making was kept, but as the smallest strategy.

**3.4 Cash is the binding constraint.** Every position locks up cash until the result. By day 3 more
than 90% of the account was in positions, and every strategy competed for the rest. Much of the design
is about which strategy gets the next unit of cash, and what the bot may sell, and at what price, to
raise it.

## 4. Method

### 4.1 Fair value

Each market's fair value is Polymarket's probability *p*, with the candidates of a race scaled so their
probabilities sum to 1. It is used only where Polymarket's own bid–ask spread is 3c or less. Markets
without a usable Polymarket price (typically 15–25 of the 237) fall back to the tournament's order book
and are reported as "unpriced".

### 4.2 Edge: expected gain per unit of cash

Every opportunity is scored the same way: the expected gain at the result divided by the cash it ties up.

| Action | Edge |
|---|---|
| Buy YES at the ask *a* | (*p* − *a*) / *a* |
| Sell YES short at the bid *b* | (*b* − *p*) / (1 − *b*) |
| Keep a long rather than sell it at the bid *b* | (*p* − *b*) / *b* |
| Keep a short rather than buy it back at the ask *a* | (*a* − *p*) / (1 − *a*) |

One scale for everything means the allocator can compare selling one position against buying another,
and each strategy can apply a simple hurdle: 8% for the ladder, 12% for the momentum rule's shortlist.

### 4.3 The four strategies

**The value book and the value floor.** The bot never rests an order that would reduce a position at a
worse price than its value: a long is never offered below *p* − *m*, a short is never bought back above
*p* + *m*. The margin *m* is a live setting (0.5c by default; 4c on the live bot at the time of writing,
to raise cash for the other strategies; §6.3 gives the cost). The floor applies in every mode, and it only
ever moves a price away from the other side of the book, so it can never hit another trader. The two
exceptions are deliberate and separately limited: allocator swaps and the momentum rule's exits.

**The allocator.** Once an hour, on fresh books and a fresh reading of cash, it pairs the holdings with
the least edge left against the levels on the book with the most, and executes each pair as two
immediate-or-cancel orders at the best price: the sale first, then the purchase once the cash has
arrived. A pair must improve edge by at least 5 percentage points, net of costs. For such a swap the
sale may go up to 3c below value, which the ordinary floor would not allow. Turnover is capped per hour,
no position is ever flipped from long to short, and the headline party-control markets are never sold.
The same machinery refills the market-making reserve when cash runs low, selling the lowest-edge holdings
first and never below the floor.

**Market making.** Two-sided quotes in markets priced between 15% and 85%, one tick inside the best
other trader and never within 1c of fair value, leaning away from whatever the bot already holds and
from its overall Republican-versus-Democrat exposure. Order sizes follow a quarter-Kelly rule against *p*,
with a cap per market, and scale with the account. Inventory older than six hours, or above 3k in one
market, is sold off at a 1c concession. Market making has a 20k reserve, in cash and in risk room, and
the value strategies pause their buying when the reserve is below target so they cannot starve it.

**The harvest ladder.** For every longshot (*p* ≤ 10%) it rests sell orders at the best other offer
plus 0, 2, 4 and 6c; for every favourite (*p* ≥ 90%) it rests buy orders at the best other bid minus 0,
2, 4 and 6c. Each level is 3k of cash and is placed only where its edge is at least 8%. The ladder sells
the tilt only at prices better than today's: it costs nothing if the tilt stays put and earns in steps
if it widens. It has its own 10k budget, carved out of the market-making reserve.

**The momentum rule (armed).** The tilt moves slowly and tends to keep moving, so a rise is briefly
predictable. Every minute the bot re-estimates *s* from other traders' prices only, in 4-hour bins, and
fits a 24-hour slope and a 6-hour slope. The rule switches on only when the 24-hour slope is at least
+0.5 points a day and the 6-hour slope is positive, and both have held for four hours. It then buys the
longshots most exposed to a further rise (priced 4–25%, with less than 12% edge against them), 10k at a
time while the rise continues, up to a cap. It sells them again, over six hours, when the 24-hour slope
stops rising, when they are up 25%, or when they are down 25%; after that the ladder takes over those
markets. It never buys in a race where the bot is selling. The expected return of this bet is modest and
uncertain: the analysis in `analysis/reports/TILT_PATHS.md` puts an unconditional long-tilt bet at about +7%
with a 35% chance of losing a third or more. That is why it waits for a trigger rather than running
always, and why it is capped at 10k on the live bot.

### 4.4 Risk controls

| Control | Rule |
|---|---|
| Worst case across all races | The loss if a national swing hit every race the same way, plus a margin for the rest. Above 40% of the account the bot only reduces positions. A simpler sum-of-worst-cases is kept as a backstop |
| Caps per market and per state | 10k per market. 15k of cash per state across every buying path; added after three Rhode Island shorts reached 27k, a quarter of the account, in one night of selling to a persistent longshot buyer |
| Cash check | Every order is checked against the cash the exchange says is free. Reducing a NO holding is sent as a covered sale, which needs none |
| Kill switch | If account value falls a set fraction below the start: stop, cancel everything, and leave a marker file so nothing restarts trading automatically |
| Guards | Pause a market after a sudden move. Never risk about 1 to earn about 1c at the extremes. Ignore a Polymarket price far from the book, which usually means a wrong match. Quote one side only where Polymarket and the book disagree |
| Order expiry | Every order expires on its own, so quotes disappear if the bot dies |
| Watchdog | If no cycle completes for 10 minutes: cancel everything and exit so the service manager restarts the bot. It fired once, during a 20-minute exchange outage on 7 October, and the restart was clean |

## 5. Engineering

**Event-driven.** The exchange pushes book changes and fills over a WebSocket. The bot reacts within
1–2 seconds, and sends nothing while nothing changes. Every 30 seconds it reconciles against the REST
API, because pushed delivery is best-effort. A dead socket is noticed and reconnected. Between
reconciliations the bot tracks its own orders itself, so placing and cancelling costs no extra requests.

**Request budgets.** At most 80 requests a minute overall, and a separate budget of 28 order writes a
minute, lowered from 45 after the first rate-limit penalties. An order that does not fit the budget is
deferred to the next cycle, never dropped silently. Measuring the exchange's real limit, instead of
trusting the published one, was itself a first-day task.

**Settings change without a restart.** A JSON file next to the bot is re-read every 30 seconds. Only an
explicit list of settings may be changed this way; each value is range-checked and every change is
logged. Every strategy above ships switched off and is switched on through this file.

**Code changes without losing queue position.** A "handover" restart makes the old process exit without
cancelling its orders and the new process adopt them.

**Releases.** New behaviour arrives as a numbered release. Each one: ships every new setting switched
off; carries a test proving that with the switches off it behaves identically to the previous release,
order for order; replays a recorded live snapshot as a dry run; and comes with a staged settings file, a
first-hour watch list and a rollback. Sixteen releases were built between 2 and 6 October; twelve went
live, one at a time, each after the previous one had run for a few hours.

**Tests.** 56 offline test suites, about 4,000 checks, run against a fake exchange that follows the API
specification. A stress test runs long randomised sessions against a deliberately faulty exchange
(failed requests, lost responses, a lagging order list, a dropping feed) and checks after every cycle
that the bot never crashes, never trades against itself, keeps positions within limits, and recovers
once the faults stop. Two simulators, one with rival bots and one starting from the real recorded book,
were used to rank ideas before building them; several that sounded good ranked negative and were dropped.

**How it was built.** The code was written with AI coding assistants (Claude Code), including parallel
agents that explored ideas, built releases and reviewed each other's work, under the author's direction.
Every design decision, deployment and risk setting was the author's, and nothing changed on the live
server without an explicit instruction.

## 6. Evaluation

### 6.1 What is recorded

After every cycle the bot writes its full state to `status.json`, and a recorder stores every order
book, Polymarket price and position over time, so any decision can be replayed. The main series are:

- account value at the exchange's marks (the leaderboard);
- expected value at the result, with its change over 24 hours;
- realised profit, broken down by what produced each fill (market making, value buys, swaps, refills,
  the ladder, the momentum rule), with the edge at the time of the fill and the price move afterwards;
- the tilt *s*, its slopes, and how much the account's marks move per point of *s*.

### 6.2 Results so far (7 October 2026, 14:00 UTC)

| | |
|---|---|
| Account value at the marks | 103.2k, +3.2% on 100k. Range so far 100.2k–103.8k |
| Expected value at the result | 110.4k, +10.4%. Up from 104.8k on 4 October, when the value floor went live |
| Realised profit | +0.8k |
| Cash in positions | 89% |
| Tilt exposure | about 38k: the marks move about −380 for every +1 point of *s* |

The gap between the two valuations *is* the tilt. The book is short longshots and long favourites, and the
exchange marks both at tilted prices. Which valuation the final ranking rewards depends on whether the
races settle before the leaderboard closes. The tournament ends on 4 November, election day.

### 6.3 Costs and failures seen

- **Raising cash costs expected value.** Widening the value floor from 1c to 4c on 7 October freed about
  14k of cash at a cost of about 470 of expected value (3.4%), measured from the fills. At 1c, nothing in
  the value book could be sold anywhere near fair value.
- **A retry loop starved the market maker.** The fast refill, switched on with the wider floor, retried
  unfilled sales every cycle and used up the request budget; for two hours the market maker could place
  almost no quotes. It was switched off; the retry needs a cool-down per market.
- **Drawdowns in the marks during tilt rises** (−0.8k overnight on 5–6 October, two-thirds of it in one
  state) are expected under the value strategy. The per-state cap limits how concentrated they can be.
- **Market making earned between 4 and 80 units a day**, depending on how much cash it had. This
  confirms §3.3.

## 7. Limitations

- Polymarket's price is taken as the true probability. Where it is thin, or matched to the wrong market
  (eight contracts have no match), the bot is trading its own error.
- Expected value at the result is an average, not a distribution. The correlated risk model exists for
  the risk cap but is not yet reported as a range of outcomes.
- The tilt strategies rest on six days of tilt history. The momentum rule has not yet fired.
- The simulators model rival bots crudely. Live markets have been more adversarial in some places
  (persistent longshot buyers) and less in others.
- This is play money. Participants chase rank, not wealth, which plausibly causes the tilt in the first
  place (longshots are lottery tickets for rank). The results may not carry over to real-money markets.

## 8. Fair play

The bot only places orders it intends to fill, never trades with itself, runs on one account, and stays
within the exchange's measured rate limit. No spoofing, wash trading, collusion, multiple accounts or
exploitation of exchange bugs; these constraints were part of every design brief.

## 9. The repository

The bot is a small package. Each file owns one concern and says at its top what it must never do.

| File | Owns |
|---|---|
| `mm_bot.py` | The entry point (`python mm_bot.py run --live`); it only imports the package and runs `main` |
| `mmbot/config.py` | Every setting, with its documentation, and the list of settings that may be changed live |
| `mmbot/util.py` | The clock, paths, alerts, the logger, the exchange's fixed facts |
| `mmbot/exchange.py` | The API client, the realtime feed, the per-market state, order-change records |
| `mmbot/pricing.py` | Fair value from Polymarket, race scaling, the tilt estimator, race and bloc risk maths |
| `mmbot/quoting.py` | One quote: prices, Kelly sizes, the value floor applied to a quote |
| `mmbot/measure.py` | Fill statistics, the phone summary, `report` and `analyze` |
| `mmbot/bot.py` | The `Bot`: its state, the cycle (read, decide, reconcile, send), order management |
| `mmbot/risk.py` | Kill switch, lots and ages, the worst-case cap, the cash gate, the per-state caps |
| `mmbot/value.py` | The value book's allocator: swaps, buys at the touch, the refill, the set ladder |
| `mmbot/mm.py` | Market making's funding: the reserve, the recycler, the room guard |
| `mmbot/ladder.py` | The harvest ladder |
| `mmbot/arb.py` | Pair unwinds of NO+NO sets, their follow-ups, the stale-quote takes |
| `mmbot/status.py` | `status.json`, health, the 2-hourly summary, the live settings file, the recorder |
| `mmbot/ops.py`, `mmbot/cli.py` | Handover, watchdog, the start-up self-test, the run loop; the command line |
| `ref_prices.py`, `ref_map.json` | Polymarket prices and the mapping from races to their markets |
| `tests/` | 35 suites, the three simulators and the stress test (`tests/README.md`); `test_identity.py` proves a new version sends the same orders as the one the server runs |
| `analysis/` | The reports behind each design decision and the tools that produced them |
| `deploy/` | Service unit, server setup, the handover script, `RUNBOOK.md`, `RELEASES.md`, the staged settings of each release |

The code was 16,600 lines in one file on 8 October; the features that were never switched on were removed
(about 4,500 lines and 21 test suites) and the rest split into the package above, each step checked against
the live code by `tests/test_identity.py`. The build's working material is on the branch
`archive/build-notes-2026-10`.

### Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.10+
printf 'SUPERMARKET_API_KEY=...\nTOURNAMENT_SLUG=midterm-elections\n' > .env
source .venv/bin/activate
python mm_bot.py status        # balance, P&L, positions, open orders
python mm_bot.py run           # dry run: live data, orders only simulated
python mm_bot.py run --live    # trade
python mm_bot.py analyze 24    # per-market edge and profit from local files
python ref_prices.py check     # tournament price vs Polymarket for every contract

for t in tests/test_*.py; do python "$t" | tail -1; done      # the suites
STRESS_LADDER=1 python tests/test_stress.py                    # the stress test
```

The dry-run suites (`tests/test_p*_dryrun.py`) replay a recorded snapshot. Point `P9_SNAP` at a folder
holding `md.sqlite` (built from an `ops-snapshot-*` branch's `market_data.sql.gz`) and the snapshot's
`status.json`, `order_notes.json`, `position_lots.json` and `settings_override.json`; without it they
skip.

On a server: `deploy/setup.sh` installs and hardens an Ubuntu host; `systemctl enable --now mmbot` runs
the bot as a service that restarts on crashes and reboots; `journalctl -u mmbot -f` follows the log. An
optional `ALERT_URL` (ntfy) sends phone alerts and a summary every two hours. The files the bot writes
while running are git-ignored. The live settings file exists only on the server; the staged copies under
`deploy/package<N>/` record what was switched on, and when.
