# Which layer earns? Attribution, 4–8 October 2026

Source: the bot's own log lines (every trade carries the Polymarket price *p* at the time), parsed by
`analysis/returns_attribution.py` over `mm_bot.log*` (4 Oct 02:12 to 8 Oct 14:38 UTC). Expected value (EV)
means value at settlement, positions at *p*. 4 October is the transition day (value mode went live at
11:15); the four days 5–8 October are the bot as it runs now.

## The table (5–8 October, four days)

| Layer | Activity | Expected value | Per day |
|---|---|---|---|
| **Allocator buys** (swaps and spare-cash buys at the touch) | 23.6k bought | **+2,676** | +670 |
| Allocator swap sales (the sale half of a swap) | | −1,534 | −380 |
| → Allocator net | | **+1,142** | +290 |
| **Market making** (middle band, FIFO-matched round trips) | 4,050 fills | +667 realised | +170 |
| Value positions acquired through tail quotes | | +650 | +160 |
| **Refill** (value sold to fund market making's 20k reserve) | **56k sold** | **−1,627** | −410 |
| **Momentum sleeve** (funding sales; it bought nothing) | 12.9k sold | **−521** | |
| Harvest ladder | 0 fills | 0 | |
| Takes | 0 since 5 Oct (paused by the risk room) | 0 | |
| Arbitrage / pair unwinds | 1 event | ≈0 | |
| Account at the marks | 100.8k → 103.6k | | +700 |
| EV at settlement | 108.1k → 110.5k | | +600 |

Three things stand out.

**1. The allocator is the only layer with a large, measured return, and it has been starved.** Its
buys earned about 11% of EV per unit of cash deployed; after paying for the sale half of each swap, about
5%. Almost all of it happened on 5 October (20.8k bought, +2,414). Since then it bought 1.3k on the 6th,
1.3k on the 7th and nothing on the 8th: every unit of cash the refill raised went to the market-making
reserve or the momentum sleeve instead.

**2. Market making costs more to fund than it earns.** It realised +667 in four days. The refill that
keeps its reserve topped up sold 56k of value positions and gave up 1,627 of EV doing so (the 4c floor
on 7–8 October accounts for 1,370 of that). Even adding the inventory market making still holds (about
+600 at *p* in `status.json`), the layer is roughly break-even at best, and it occupies 20k of cash
plus the bot's request budget.

**3. The momentum sleeve cannot buy.** It switched on at 16:00 on 7 October, sold 12.9k of value
positions over six hours to fund itself (−521 EV), and bought nothing before the tilt turned and it
switched off at 04:00. Its candidate filter excludes any longshot the bot is short or whose favourite it
is long (`mom_candidates`, `mm_bot.py:12951`), and the value book is short nearly every longshot. By
construction it is a seller only.

## What this says about the design

The bot's returns come from one idea: **buy the most edge per unit of cash, sell the least, never below
value.** That is the allocator. Everything that competes with it for cash (the market-making reserve, the
refill, the sleeve) has cost expected value over this window, and the ladder has done nothing.

Proposed keep / retire list for the owner's decision:

| Keep | Retire |
|---|---|
| Fair value from Polymarket (race-scaled) | Momentum sleeve (disarm now: `momentum_auto` false) |
| The value floor | Refill and the 20k market-making reserve (`alloc_mm_reserve` → small) |
| The allocator: swaps and spare-cash buys, hourly, with its swap margin and 5-point hurdle | Market making in the middle band (or keep a token reserve for a week to compare) |
| Correlated worst-case cap, per-market and per-state caps, cash gate | Harvest ladder (0 fills; the value book is already short the tilt) |
| API, feed, budgets, handover, watchdog, self-test, status and alerts | Arbitrage, pair unwinds, takes (redundant with the allocator's buys at the touch) |

Immediate settings changes that follow from the numbers, before any code is written:
1. `momentum_auto` false: stops the sleeve re-arming and selling value for nothing.
2. `value_sell_margin` 0.04 → 0.01: the wide floor existed to fund the sleeve and market making.
3. `alloc_mm_reserve` 20000 → 2000 (or 0): the refill stops draining value positions, and the 10k of
   free cash now sitting in the reserve goes to the allocator's buys at about 10% edge.

Expected effect, from the table: the refill's −400/day stops, and the allocator gets back to the
5 October pace (+290/day net, more with 10k of cash to deploy). The account's returns would then rest
on a single, explainable mechanism.

## Caveats

- Lines, not fills: the parser reads the bot's trade lines, which are written when a sale or buy is
  confirmed; market-making fills come from the `FILL` lines with the fair value at quote time.
- The 4 October column is excluded from the totals above: the `TAKE` lines that day (+16k by the naive
  formula) are the old tilt-exit sales, logged per attempt, and are not comparable.
- EV moves for reasons other than trading (Polymarket prices drift on held positions); the table counts
  trading only, which is why the layer sum (+0.4k) is below the EV change (+2.4k).
- Four days is a short window. The allocator's +2,414 came from one day with 20k of cash to deploy; its
  per-unit return (11% gross, 5% net) is the number to plan with, not the daily total.
