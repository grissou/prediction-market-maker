# Live analysis, 4 Oct 2026 (snap04, data to 15:56 UTC)

Owner questions (1) and (4). Data: /home/claude/snap04 (fills.csv 7,965 rows, md.sqlite, order_notes.json,
journal 3 Oct 22:47 to 4 Oct 15:56, status.json). Read-only. Scripts (each runs in under 5 s):

| Script | What it does |
|---|---|
| `X_common.py` | loaders: recorder snapshots with a race-scaled reference `p` (r / sum over the race when every leg has one, else raw); nearest-snapshot lookup; fills; notes; journal |
| `X_carry.py` | Q1: `Bot.mm_carry` (39e4012) rebuilt offline, for three windows, p at the fill and p at 15:56; top-of-book counts |
| `X_ev_attr.py` | Q1b: the change in expected value at the outcome, 3 Oct 22:47 to 4 Oct 15:56, split by fill class/period plus reference moves |
| `X_alloc.py` | Q4: `alloc_plan` rebuilt on the 15:56 state, with candidate lists and five scenarios |
| `X_take_reserve.py` | Q4b: the takes skipped by `take_respect_reserve`, valued at the nearest snapshot/book |

Approximations: p is the recorder's `snapshots.reference` race-scaled, taken from the snapshot nearest the fill. Snapshots
are 60-80 s apart. The recorder has no liquidity flag, so every market with a reference counts as liquid. The live
`status.json` has 222 of 229 references liquid. The live bot is fc3d38d (Package 12) and does not report
`mm_carry_24h` yet, so the numbers below come from this rebuild. Only 2 self-test order ids are in the journal, and none
of them is a maker fill in the windows.

## Q1. Stage 3 market-making carry, measured live

Classification follows `mm_carry` exactly: order_notes basket > alloc/set_ladder > arb > take, else maker. The middle
band is p in [0.15, 0.85]. Mid-band maker buys and sells are FIFO-matched per market. A row's price is its quote price
(the YES price), as in `ops_replay`.

| Window | hours | realised | per_day | unmatched sh / ev | value_adds_ev | takes_ev | fills: mid / tail / unpriced / take / arb |
|---|---|---|---|---|---|---|---|
| Stage 3, 11:24:46-15:56 (p at fill) | 4.53 | **0.00** | **0** | 3,110 / +55 | +229 | +1,479 | 66 (18 buys, 48 sells) / 100 / 28 / 47 / 117 |
| same, p at 15:56 | 4.53 | 0.00 | 0 | 3,110 / +66 | +237 | +1,449 | same |
| Last 24 h, 3 Oct 15:56-4 Oct 15:56 (p at fill) | 24 | **+6.96** | **+7** | 35,262 / −859 | **−12,700** | **+13,268** | 270 (134 / 136) / 922 / 45 / 610 / 945 |
| same, p at 15:56 | 24 | +6.96 | +7 | 35,262 / −873 | −12,467 | +12,943 | same |
| Out of reduce-only, 15:39-15:56 | 0.29 | 0.00 | 0 | 623 / +22 | +38 | +103 | 7 / 6 / 0 / 5 / 2 |

- **Cash the maker fills used, stage 3 window:** $5,071 of YES bought, plus $2,207 of NO bought (asks above the long
  held). Only $94 came back from sales out of longs. Over 24 h: $37.7k of YES and $11.4k of NO bought, and $71.5k
  raised from long sales (mostly the overnight tilt exits).
- **Maker fills per hour in the stage 3 window:** 11h 51, 12h 73, 13h 26, 14h 17, 15h 27. Most of them are reduce-only
  exits, which are one-sided, so none of them could match.
- **Our quotes at the top of the book in the middle band** (snapshot our_bid or our_ask equal to the best price; on
  average about 90 markets are in the band):
  - Stage 3 window: 47 of them quoted and 10.4 at the top, per snapshot.
  - Last 24 h: 34.7 quoted and 7.9 at the top.
  - After reduce-only ended (15:39-15:56): 76 quoted and **40 at the top**.
- **Per-day carry estimate:** no measured number yet. The stage 3 window covers 4.5 h, and the bot was reduce-only for
  about 4.2 h of it (467 of 474 cycles). Reduce-only means sell-side quotes only, so no buy/sell pair can form, and
  realised is 0 by construction rather than by measurement. The two-way quoting sample is the 17 minutes since 15:39:
  7 mid-band fills, 0 matched, 40 markets at the top. The 24 h figure (+$7 per day) mostly reflects the P8 tilt
  regime, not stage 3. So the pre-estimate still stands: about 0 per day at 0 cash, about 0.7-1.5k per day when funded.
  Cash available for quoting is now about 4k (`cash_after` 4,012; cash gate left 3.7k), so the data points to the
  low end. The first real measurement needs at least 12-24 h of non-reduce-only quoting. Use `mm_carry_24h.per_day` at
  about 15:40 on 5 Oct, and treat any figure from less than 6 h of two-way quoting as ±100%.

### Q1b. What happened to expected value today

Expected value at the outcome is cash (account value minus market value, from account_marks) plus positions at p.
Snapshot positions are used: longs q·p, shorts |q|·(1−p). My figures run about 1k below the README's (the README
probably uses the cash-gate cash), but the shape matches: 108.97k at 22:47, 108.17k at 07:18, **103.78k at 11:08**,
104.92k at 11:24, 106.24k at 15:39 and 106.33k at 15:56.

Change 3 Oct 22:47 → 4 Oct 15:57: **−2,531**. Each fill's edge is measured against p at fill (buy q·(p−price),
sell q·(price−p)):

| Component | fills | traded | EV effect |
|---|---|---|---|
| (a1) P8 maker fills 22:47-07:19 (tilt-mean quotes + tilt exits) | 451 | $52k | **−10,014** (asks −3,612, bids −6,402: buying YES above p and buying back shorts above p) |
| (a2) P8 tilt exits 07:19-11:10 | 382 | $46k | **−3,995** (sales −2,317, short buy-backs −1,679) |
| (b) set unwinds and arb after 07:19 | 455 | $27k | **−1,072** (the README's cost of 918 plus spread) |
| (b0) arb before 07:19 | 27 | $2.7k | −47 |
| (c) raw-Polymarket takes 22:44-07:05 | 407 | $56k | **+9,896** |
| (c2) tilted-ref takes 07:05-11:10 | 72 | $52k | +452 (sales −650, buys +1,102) |
| (d) value-mode takes after 11:10 | 66 | $18k | **+2,356** |
| (d2) value-mode maker fills after 11:10 | 240 | $12k | +578 |
| (e) reference moves on held positions (Σ q·Δp) | | | **−542** (largest: Dem U.S. House −76, Dem Michigan Senate −56, Dem U.S. Senate −50) |
| Residual (sampling, 2 unpriced, 35 unsided rows) | | | −142 |

Reading: the overnight P8 book quoted toward the tilted mean, and that, together with the tilt exits, gave up about
**−14.0k** against Polymarket. The raw-Polymarket takes won back +9.9k. Between 07:19 and 11:08 the set unwinds
(−1.1k) and the tilt exits (−4.0k) caused the 108.2k → 103.8k drop. Value mode then recovered **+2.9k** (takes
+2.4k, quotes +0.6k). Reference moves were small, at −0.5k.

## Q4. Is the allocator planning swaps or blocked, and why

**It was blocked by reduce-only in all five runs.** At 11:17:08, 12:17:09, 13:17:25, 14:17:29 and 15:17:46, the
`realtime |` line right before every `ALLOC run` reads `-> REDUCE-ONLY`. The 11:17 run was also reduce-only (worst
case 63.8k, from before the 11:24 frac change). In `alloc_plan`, `global_reduce` empties the buy levels
(`blocked["risk"]`, red team RT-2). With no buy levels:

- The 4.8-5.2k of spare cash (20,224 or about 19.8k against the 15k reserve) had nothing to pair with.
- No reserve refill ran, because cash was above the reserve.

That gives 0 pairs and an estimated gain of 0. Other suspects, checked and cleared:

- **The 30 s freshness test is not failing.** `alloc_p` reads `refs.ages()` with the key `"race|Party"`, which is the
  same key format as `ref_map.json`. References download every 5 s (`ref_refresh_seconds`) on a background thread, and
  the journal has no reference download failures, so ages stay around 0-5 s. The recorder doesn't store ages; the
  snapshot reference is current to the cycle.
- **The bloc check, turnover cap (15k left) and set legs** were never reached.

### Rebuilt on the 15:56 state (`X_alloc.py`)

Inputs: status positions, the latest recorder books with our own price level stripped, and the snapshot p. Excluded:
4 headline markets and 8 with no p. The newest recorder books are 5 min old; the live bot's books are fresher.

**Buy candidates.** 338 levels have an edge of at least 0.05. The top 20 by edge per $:

| Side | Market | Price | p | Edge per $ | Depth |
|---|---|---|---|---|---|
| short | Rep Rhode Island Senate | 0.155 / 0.150 / 0.145 | 0.008 | 0.174 / 0.167 / 0.160 | $11.3k / $4.0k / $5.7k |
| buy | Dem Rhode Island Senate | 0.865 | 0.992 | 0.147 | $0.7k |
| buy | Dem Rhode Island Governor | 0.850 | 0.970 | 0.141 | |
| buy | Dem Connecticut Governor | 0.855 | 0.975 | 0.141 | $92k |
| short | Rep Massachusetts Governor | 0.150 | | 0.136 | |
| buy | Ind Montana Senate | 0.075 | | 0.135 | |
| short | Rep North Carolina Senate | 0.145 | | 0.128 | |
| short | Ind Rhode Island Governor | 0.120 | | 0.126 | $17.6k |
| short | Dem Arkansas Governor | 0.135 | | 0.125 | |
| short | Rep Georgia Senate | 0.135 | | 0.123 | $34.7k |
| buy | Dem Massachusetts Governor | 0.860 | | 0.123 | |

The full list of 20 is in the script output.

**Sell candidates.** Of 88 held positions, only 7 pass `alloc_max_edge_sell` 0.02 with at least $20 at the touch.
Three of those have an edge-held below 0, which makes them +EV closes:

| Position | Shares | Touch | p | Edge-held | Available |
|---|---|---|---|---|---|
| short Rep South Dakota Senate | −14,493 | ask 0.945 | 0.998 | −0.97 | $797 |
| short Rep Nebraska Senate | | | | −0.063 | |
| long Dem SC-01 | | | | −0.060 | |
| long Dem MT-01 | | | | −0.006 | |
| short Dem Alabama Governor | | | | +0.017 | |
| long Rep Texas Senate | | | | +0.019 | |
| short Dem Michigan Senate | 5,317 | 0.74 | | +0.019 | $1,382 |

Everything else is held at an edge-held above 0.02, so the gate correctly keeps it. Rep FL-16 is held at 0.020 with
$1 at the touch. The pinned Senate markets and the headline markets are excluded.

### Scenarios

| | Cash | Reserve | Reduce-only | Result |
|---|---|---|---|---|
| A (as live, 15:17) | 19,837 | 15k | yes | **0 pairs**, as in the journal |
| **B: next run about 16:17 on the 15:56 state** | **4,012** (cash_left after the 19.5k now locked in resting orders) | 15k | no | **Reserve refill: 7 sales, $2,446** (South Dakota buy-back $797, Michigan Senate buy-back 5,317 @ 0.74 $1,382, plus 5 small ones), **no buys**. The deficit is 11k but only 2.4k of it is eligible. Mostly +EV closes; the Michigan Senate sale costs about $26 of edge. |
| C: 5k spare used (cash 19.8k, not reduce-only) | 19,837 | 15k | no | 8 pairs, $7.3k, estimated gain **+$2.0k**. Everything goes into shorting Rep Rhode Island Senate at 0.155 (p 0.008): 5,724 shares from spare cash ($4.8k) plus the 7 sales. |
| D: reserve 0, cash 19.8k | 19,837 | 0 | no | $15k (turnover cap), gain **+$3.15k**: Rep Rhode Island Senate short $6.7k, Dem Connecticut Governor $6.2k (7,202 @ 0.855), Dem Rhode Island Senate $0.7k, Dem Rhode Island Governor $0.5k |
| E: reserve 0, cash 4k (today's figure) | 4,012 | 0 | no | $6.5k, gain +$1.9k |

Not modelled offline: the bloc check (no sensitivities recorded). Shorting Rep Rhode Island Senate moves bloc toward
Democratic; bloc is +2.4k now against a cap of about 5k. The P12 L2 prefer-short rule is also not modelled. Rhode
Island is one race, so a 10k-per-market cap concentrates the whole move there. With `alloc_max_contract_usd` at 10k,
the Rep Rhode Island Senate short already holds about 2.3k at (1−p).

### Settings that would make it trade

1. Stay out of reduce-only. This has been true since 15:39 with the backstop at 0.85, so this alone lets the 16:17 run
   act.
2. Lower `alloc_mm_reserve` so spare cash exists:
   - Now, with cash_left about 4k, the 15k reserve forces a refill (scenario B) instead of buys.
   - At 3-4k, the about 4k spare goes into the scenario E pairs (gain about 1.9k).
   - At 0 with about 20k free (after quotes are pulled), you get scenario D (gain about 3.2k, limited by the 15k
     turnover per hour).
3. The sell gate (0.02) is what limits swaps out of held positions: 81 of 88 holdings sit above it. Raising it would
   rotate +EV holdings into higher-edge ones. That is only worth doing for the gap above `alloc_min_improvement`
   0.03.

### alloc_set_rich_leg

Confirmed nothing to ladder: `nono_sets` shows 1 race with 1 set, and `alloc.set_ladder` shows races 0, shares 0 and
filled 0. The morning unwinds took out 21,606 sets.

### take_reserve_blocked (128 in status)

The journal has 50 "skipped: it would spend the market-making reserve" lines, all at 11:29 or between 15:38 and
15:56, across 28 distinct markets. The status counter of 128 also counts repeats that weren't logged. The best edge at
the touch per distinct market adds up to **$9.8k, but it would need $110k of cash**, so the real constraint is cash.

At the edges seen, about 8-12% per $:

| Take | Edge | Cash needed |
|---|---|---|
| Dem Arkansas Senate sell @ 0.16 (p 0.071) | $1,162 | $10.9k |
| Rep Arizona Governor sell @ 0.185 (p 0.106) | $1,376 | $14.2k (11:29) |
| Dem Louisiana Senate sell @ 0.155 (p 0.065) | $899 | $8.5k |
| Rep PA-07 sell @ 0.20 (p 0.139) | $852 | $11.2k |
| Rep New Hampshire Senate sell @ 0.23 (p 0.139) | $663 | $5.6k |
| Dem Mississippi Senate sell @ 0.17 (p 0.095) | $658 | $7.3k |

Taking these with the about 4-5k of spare cash would have added roughly **+$400-550**. With the full 20k, roughly
**+$2k**. The touch depth is from recorder books 0.5-11 min old, so treat these as upper bounds.
