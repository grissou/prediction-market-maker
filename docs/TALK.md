# The ten-minute version

A talk track for explaining the project out loud. One line per beat; the numbers are from 8 October 2026
and should be refreshed from `status.json` and `analysis/reports/RETURNS_ATTRIBUTION.md` before use.

## 1. The setting (1 minute)

- A month-long tournament on a play-money exchange: 237 contracts on US midterm races, an order book, an
  API, no fees. Everyone starts with 100,000. Contracts pay out at the election result.
- Polymarket trades the same races for real money. I use its price as the best estimate of the true
  probability.
- I wrote a bot that trades the whole tournament on its own, and has done since the open on 1 October.

## 2. What I expected, and what I found (2 minutes)

- I expected to make markets: quote both sides, earn the spread, stay flat, take no view.
- Three things changed that within a week:
  1. **The prices are biased.** Longshots trade too dear and favourites too cheap, by a factor I can measure
     from the whole cross-section every minute (the "tilt": prices are pulled toward "everyone equally
     likely"). A 2% candidate trades at 10–15c.
  2. **Payout at the result makes that bias profit, not noise.** A favourite bought at 85c with a 97% chance
     is worth 97c at settlement whatever the leaderboard says in between.
  3. **The book is full of other bots.** Measured fill by fill, pure market making earned tens of units a
     day. Not nothing, but not the game.

## 3. The one idea (2 minutes)

- Score everything, held or on the book, by **expected gain per unit of cash it ties up**: buy YES at *a*
  earns (*p* − *a*) / *a*; a short at *b* earns (*b* − *p*) / (1 − *b*). One scale for everything.
- Then, every hour: **sell the holdings with the least edge left, buy the opportunities with the most, and
  never sell anything below its value.** That last rule is the value floor, and it exists because of the
  day I didn't have it (beat 5).
- Market making runs alongside, in the middle of the probability range, with its own reserve of cash.
- A passive ladder rests orders that sell the bias at prices better than today's, so it earns if the bias
  widens and costs nothing if it doesn't.

## 4. Keeping it safe (1 minute)

- A worst-case loss if a national swing hits every race the same way; above 40% of the account the bot only
  reduces.
- Caps per market and per state: 15k in one state, after three Rhode Island shorts reached a quarter of the
  account in one night.
- Every order checked against the cash the exchange says is free. Orders expire on their own. A watchdog
  restarts the bot if it stalls; it fired once, in an exchange outage, and came back clean.

## 5. What went wrong (1.5 minutes)

- **The 3.7k morning.** An early rule sold positions when their marks fell. During a tilt rise it sold most
  of the value book at compressed prices: about 3.7k of expected value in four hours. The value floor is
  the fix.
- **The refill loop.** A fast refill retried unfilled sales every cycle and ate the request budget; the
  market maker was down to 19 orders for two hours before I caught it. Lesson: measure the exchange's
  limits and budget for them explicitly.
- **The momentum sleeve.** A rule to buy longshots while the bias was rising. It sold 12.9k of value to fund
  itself and bought nothing, because its own filter excluded every longshot I was short. Measured, then
  switched off. Lesson: anything that sells to raise cash must say what it buys.

## 6. Results so far (1 minute)

- Account at the exchange's marks: about +3.6% in eight days. Expected value at settlement: about +10.8%.
  The gap is the bias itself: my book is short the longshots the leaderboard still marks at tilted prices.
- Attribution from the bot's own trade lines: the allocator earned about 11% per unit of cash it deployed;
  market making a few hundred; the sleeve and the refill cost more than they made.
- Rank: top 13% on P&L and on the exchange's "Smart Score".

## 7. How it was built, and what I'd do next (1.5 minutes)

- One Python file, run as a service on a small server; settings changed through a file the bot re-reads
  every 30 seconds; code changed through a handover restart that keeps the resting orders.
- Every change shipped off by default, with a test proving it did nothing until switched on, and a dry run on
  a recorded snapshot. Fifty-odd offline test suites against a fake exchange, plus a stress test.
- Built with AI coding assistants under my direction; every deployment and risk setting was mine.
- Next: strip the bot to the parts that earn (that's happening now), then, after 4 November, rewrite it small
  enough to read in an afternoon.

## Questions to expect

- *Why Polymarket as truth?* It's real money and liquid for these races; where it isn't, the bot leaves the
  market unpriced. The bot is trading the tournament's bias, not forecasting elections.
- *What if Polymarket is wrong?* Then so am I, in proportion. The caps bound how much.
- *Is this fair play?* Only orders I intend to fill, one account, within the rate limit. No spoofing, no
  wash trading, no exploiting bugs.
- *Would it work with real money?* The bias plausibly comes from play-money incentives (rank, not wealth).
  I wouldn't assume it transfers.
