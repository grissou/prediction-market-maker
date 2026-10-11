# Which part of the bot makes the money?
*8 October 2026 (updated 10 October); the bot's own trade log, 4 October 02:12 to 8 October 14:38 UTC, then to 10 October 08:20 UTC.*

**In one paragraph.** Over 5 to 8 October the bot's expected value at settlement rose from 108.1k to 110.5k. Only one part earned a large, measured return: the hourly reallocation step that buys the cheapest edge per unit of cash (+2,676 on 23.6k bought, +1,142 net of the sales that paid for it). Market making in the middle of the price range earned +667 but cost 1,627 to fund (value positions sold to top up its 20k reserve), and the momentum sleeve sold 12.9k of value positions and bought nothing (−521). By 10 October the harvest ladder, once funded, had become the best earner: +2,885 on 19.4k sold (15% per unit of cash).

## Why it mattered
The bot had several competing layers that all drew on the same cash. This analysis decided which to keep: it led to turning the momentum sleeve off, narrowing the value floor, and shrinking the market-making reserve so that cash went to the reallocation step and the ladder instead.

## What was done
Every trade line in the bot's log carries the Polymarket probability *p* at the time. Each trade was valued at settlement, with positions valued at *p*. Trades were grouped by the layer that produced them. Market-making round trips were matched first-in, first-out. 4 October was the transition day (value mode went live at 11:15), so the totals cover the four days 5 to 8 October.

## What it found

### 5 to 8 October (four days)

| Layer | Activity | Expected value | Per day |
|---|---|---|---|
| Reallocation buys (swaps and spare-cash buys at the touch) | 23.6k bought | **+2,676** | +670 |
| Reallocation swap sales (the sale half of a swap) | | −1,534 | −380 |
| Reallocation, net | | **+1,142** | +290 |
| Market making (middle band, matched round trips) | 4,050 fills | +667 realised | +170 |
| Value positions acquired through tail quotes | | +650 | +160 |
| Refill (value sold to fund the 20k market-making reserve) | **56k sold** | **−1,627** | −410 |
| Momentum sleeve (funding sales; it bought nothing) | 12.9k sold | **−521** | |
| Harvest ladder | 0 fills | 0 | |
| Takes | 0 since 5 Oct (paused) | 0 | |
| Arbitrage and pair unwinds | 1 event | about 0 | |
| Account at the exchange's marks | 100.8k → 103.6k | | +700 |
| Expected value at settlement | 108.1k → 110.5k | | +600 |

Three things stand out.

**The reallocation step earned the most and was starved.** Its buys earned about 11% of expected value per unit of cash, about 5% after paying for the sale half of each swap. Almost all of it came on 5 October (20.8k bought, +2,414). It then bought 1.3k on the 6th, 1.3k on the 7th and nothing on the 8th. Every unit of cash the refill raised went to the market-making reserve or the momentum sleeve instead.

**Market making cost more to fund than it earned.** It realised +667 in four days. The refill sold 56k of value positions and gave up 1,627 of expected value doing so; the 4c selling floor on 7 and 8 October accounts for 1,370 of that. Even counting the inventory it still held (about +600 at *p*), the layer was roughly break-even at best. It also occupied 20k of cash and much of the bot's request budget.

**The momentum sleeve could not buy.** It switched on at 16:00 on 7 October, sold 12.9k of value positions over six hours to fund itself, and bought nothing before the tilt turned and it switched off at 04:00. Its candidate filter excludes any longshot the bot is short, or whose favourite it is long. The value book is short nearly every longshot, so by construction the sleeve could only sell.

### Update, 5 to 10 October (six days)

| Layer | Activity | Expected value |
|---|---|---|
| Harvest ladder (first fills 8 to 9 October, once the refill had raised cash) | 19.4k sold short | **+2,885** (15% per unit of cash) |
| Reallocation buys | nothing deployed since 7 October | +2,676 |
| Refill | **110.6k** sold | −3,905 (3.5%) |
| Momentum sleeve | | −521 |
| Market making | | +786 realised |
| Trading sum | | +1,261 |
| Expected value at settlement | 108.4k → 112.7k | +4.3k |

The difference between the trading sum and the change in expected value is drift in Polymarket prices on held positions. Of the 110k sold, about 43k was deployed by the reallocation step and the ladder; the rest funded the market-making reserve or was churned.

### What this says about the design
The bot's returns come from one idea: buy the most edge per unit of cash, sell the least, never below value. Everything that competed with that for cash cost expected value over this window.

| Keep | Retire |
|---|---|
| Fair value from Polymarket (scaled within each race) | Momentum sleeve |
| The value floor | Refill and the 20k market-making reserve |
| Reallocation: swaps and spare-cash buys, hourly, with its swap margin and 5-point hurdle | Market making in the middle band (or a token reserve for a week to compare) |
| Correlated worst-case cap, per-market and per-state caps, cash gate | Arbitrage, pair unwinds, takes (the reallocation buys at the touch do the same job) |
| API, feed, budgets, handover, watchdog, self-test, status and alerts | |

The 10 October update reversed one line: the harvest ladder, idle in the first window, became the best earner once funded. The next change was to size the refill to what the reallocation step and the ladder can actually place.

## Caveats
- The parser reads trade lines, written when a sale or buy is confirmed; market-making fills come from fill lines with the fair value at quote time.
- 4 October is excluded: that day's take lines (+16k by the naive formula) were the old tilt-exit sales, logged per attempt, and are not comparable.
- Expected value also moves when Polymarket prices drift on held positions; the table counts trading only. That is why the layer sum (+0.4k) is below the change in expected value (+2.4k).
- Four days is short. The +2,414 came from one day with 20k of cash to deploy; the per-unit return (11% gross, 5% net) is the number to plan with.

## Details
Settings changes proposed from the first window: `momentum_auto` false; `value_sell_margin` 0.04 → 0.01 (the wide floor existed to fund the sleeve and market making); `alloc_mm_reserve` 20000 → 2000 or 0 (the refill stops draining value positions and the 10k of free cash in the reserve goes to buys at about 10% edge). Expected effect: the refill's −400/day stops and the reallocation step returns to its 5 October pace of +290/day net.

Expected value here means value at settlement with positions at *p*, as opposed to the exchange's marks. The momentum candidate filter is the function that lists momentum candidates in the old bot (tag `legacy-2026-10-11`). The parser is `analysis/returns_attribution.py`.
