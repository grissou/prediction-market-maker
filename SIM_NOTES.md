# Simulator notes (Strategist, Run C, 2026-10-02)

`tests/strategy_sim.py`. Every number below: paired seeds (same world for base and variant), mean ± standard error
over seeds, P&L per hour over the sim's 12 markets (2 headline, 4 busy, 6 quiet), marked at Polymarket at the end.
"worst" = peak worst-case loss during the run. Base = live settings. **Treat as rankings, not forecasts**: the sim
still earns ~1.4c/share against the live ~0.6c, so its absolute P&L is ~2x too high per share and most of it comes
from the 2 headline markets.

## 1. Critique (before my changes)

Models well:
1. Runs the bot's own `compute_quote`, `fair_value`, `side_needs_change`; one quote per side, as live.
2. Price-time priority: a quote behind a rival fills only after the rival's size is gone, so "fill probability
   when behind the best" emerges (our quoted sides at the best: 0.28-0.33 vs live 24-42%).
3. Rivals: lagged Polymarket, floors, pennying, picking off quotes >= 1.5c through their fair; humans; walking market
   orders; informed flow after jumps; slow writes; per-market tournament bias with the favourite-longshot pattern.

Missed, in order of how much it matters in the crowded book:
4. **Calibration**: day-one flow had 8% sweeps of 1,000-8,000 shares (x3 headline) -> 46k sh/h on 12 markets;
   rivals re-quoted every 2-10 s and pennied everyone (live: they sit at their own prices, undercut median 151 s).
5. **Write budget**: a per-market bucket, every order and cancel costing 1, unrelated to `writes_per_minute`; no
   priority (pulls, headline first); so `writes_per_minute` could not be tested.
6. **Churn control** (`min_quote_life_seconds`, `churn_max_reprices`) not modelled at all.
7. **News days**: jumps independent per market; real news moves many markets at once (write contention, correlated
   pick-offs).
8. **Rival inventory**: rivals could absorb any amount, never backing off after a sweep (no persistent dislocation).
9. Bug: Polymarket was refreshed only on cycle seconds divisible by 5 -> every 10 s, not 5 s.
10. Still missing: overshoot persistence beyond rivals' re-quote (5-13 min half-life is in the consensus path only);
    rival queue games and size changes; cross-market fill clusters; party netting/race shading; multi-day inventory,
    capital limits (`quote_capital_frac`) and the exchange's sticky trade-price mark; 1-3 h horizon.

## 2. What changed (commits e6778b7, 5c3c029)

- **One write budget** for the 12 markets = `writes_per_minute` x `WRITE_SHARE` (0.24) over a sliding 60 s window,
  costed as live (one cancel request per market, or per order when only some go; new orders 1/20 of a batch each),
  sent in `Bot.change_key` order (pulls always, then Polymarket-moved, headline, empty sides, reprices). The share:
  unconstrained the 12 markets need 2.4 writes/min; live sustains 9-12/min (DATA_REPORT_2 §8), so they are ~24% of
  demand. The budget therefore binds only in bursts, as live (no 429 for 13 h).
- **Churn control** = `Bot.hold_side` (young or re-quoted >= churn_max_reprices in the window, safe, no Polymarket move
  within 15 s).
- **News regime**: 70% of jumps are common events hitting each market with prob. 0.5, direction x market orientation.
- **Rival inventory**: each rival holds at most 2-6 quote sizes and skews 0.25-1c per size held (`_rival_inv: 0` off).
- **Recalibrated to DATA_REPORT_2 §9** (`CAL` dict; `SIM_CAL=day1` restores the Builder's world): Polymarket >= 1c
  moves 0.04/market-hour; gap bias sd 1.6c + favourite-longshot, transient 0.95c, half-life 13.5 min; latency
  0.2-0.6 s, 10% slow; market orders lognormal (headline median 300, sd 1.6; races 100, 1.3), 38/26/4 per hour;
  rivals: fair error sd 2.4c, half-spread 0.25-1.25c, 30% penny, re-quote 20-120 s (fast) / 120-600 s, all react to
  a Polymarket move after their lag.
- Ref refresh bug fixed. New metrics: share of our sides at the best, capital locked in our orders, ladder fills and
  their P&L, pick-off cost (15-min markout of fills taken by rivals/informed), deferred changes.
- Prototypes: `_ladder` (R3), `_rival_aware` (see §4-5).

Calibration check (24 seeds, quiet, no budget limit; target from DATA_REPORT_2):

| | sim | live |
|---|---|---|
| fills / sh per market-hour, headline | 8.4 / 11.2k | 8.0 / 9.75k |
| busy race | 7.9 / 2.2k | 6.6 / 1.7k |
| quiet race | 1.2 / 92 | 1.2 / 246 (our quiet size is 100) |
| fv - best other bid, p10/25/50/75/90 (c) | -0.6/0.1/0.7/1.3/1.9 | -1.1/-0.3/0.4/1.2/2.2 |
| others through our fair value | 22-24% | 27-33% |
| others' spread median / <=1c / >3c | 1.0c / 51% / 3% | 1.0c / 50-70% / 2-13% |
| our sides at the best | 0.28-0.29 | 0.24-0.42 |
| edge / 15-min markout | 1.38c / 1.50c | 0.59c / 0.54c (not matched) |
| fills > 3c edge | ~0.8/h on 12 markets | 6.7/h on ~170 markets |

Re-baseline (live settings, 32 seeds x 1 h): quiet **+501 ± 37/h**, news **+432 ± 28/h**, slow +426; peak worst-case
9.4-9.5k; capital in our orders 20.6k; 33-36k shares/h. (Builder's world: +590/+468.)

## 3. Task-2 sweeps (each variant vs the live base, same seeds)

Seeds: 128 x 1 h where marked (*), else 32 x 1 h. Base at 128 seeds: quiet +499 ± 16, news +459 ± 21; worst 9.4-9.5k.
"Real" = |Δ| > 2 se in both regimes or > 3 se in one. Δworst = change of the peak worst-case loss (quiet / news).

| Variant | ΔP&L/h quiet | ΔP&L/h news | Δworst q / n | Verdict |
|---|---|---|---|---|
| (a) improve_ticks 0 (join)* | **-17 ± 6** | **-20 ± 10** | -420 / -655 | real loss: keep penny (1) |
| undercut_step_back 0.01 | 0 | 0 | 0 | no-op while min_edge = 1c (max(edge, step)) |
| undercut_step_back 0.015* | +4 ± 9 | +23 ± 11 | -225 / -299 | noise (news 2.1 se); less risk |
| undercut_step_back 0.02* | +8 ± 10 | +24 ± 14 | -554 / -741 | noise; less risk. Builder's -221 does not survive the recalibration (rivals no longer chase) |
| join + step back 1.5c / 2c | -19 ± 16 / -23 ± 23 | -29 ± 24 / -25 ± 28 | -1.2k / -1.3k | noise-to-negative |
| (b) skew_per_quote 0.0025* | +2 ± 5 | +15 ± 7 | +412 / +333 | noise; more inventory |
| skew_per_quote 0.0075 (32) | -11 ± 11 | -7 ± 9 | -750 / -523 | noise; less inventory |
| skew_max 0.01 / 0.03 | +2 ± 2 / 0 | 0 / 0 | ~0 | the 2c cap never binds within 1-3 h |
| 3 h runs (64 seeds, P&L per 3 h): skew 0.0075 | +39 ± 19 | -32 ± 20 | -501 / -744 | noise; 5-8% lower worst-case |
| 3 h: skew 0.0025 | -8 ± 20 | -28 ± 20 | +615 / +271 | noise; more risk |
| (c) min_edge 0.0075 | +10 ± 11 | -26 ± 22 | 0 / +870 | noise |
| min_edge 0.0125* | -1 ± 7 | +20 ± 9 | -737 / -885 | neutral P&L, **8-9% lower worst-case** |
| min_edge 0.015 | -2 ± 19 | -23 ± 21 | -1.6k / -1.9k | noise; volume -25% |
| max_half_spread 0.03* / 0.06 | +2 ± 4 / -1 ± 1 | -1 ± 3 / -1 ± 3 | ~0 | no effect (crowded books never let us sit 4c out) |
| min_edge x max_half_spread grid (9 cells) | follows min_edge | follows min_edge | | max_half_spread irrelevant |
| (d) kelly_fraction 0.15* | **-22 ± 6** | **-23 ± 9** | -381 / -521 | real loss: keep 0.25 |
| kelly_fraction 0.4 | -5 ± 9 | +2 ± 11 | -66 / -140 | noise |
| size_max_frac 0.01 | -14 ± 11 | -7 ± 14 | -633 / -615 | noise |
| size_max_frac 0.03 | -5 ± 11 | +12 ± 9 | -249 / +80 | noise |
| (e) ref_weight 0.6 | -8 ± 12 | -5 ± 14 | -701 / -409 | noise |
| ref_weight 0.85* | **+17 ± 6** | **+20 ± 8** | +358 / +325 | real gain (2.5-2.8 se), +4% worst-case |
| min_edge 0.0125 + ref_weight 0.85* | -1 ± 9 | +8 ± 8 | -374 / -615 | neutral; lower risk |
| (f) writes_per_minute 20* | -12 ± 6 | -5 ± 6 | -112 / -68 | budget starts to bind at 20 (2 se quiet) |
| writes_per_minute 45 / 60 | -2 ± 11 / -5 ± 10 | -9 ± 12 / -7 ± 11 | | no gain: 30 does not bind in steady state (deferrals ~60/h of ~1,000 changes) |
| (g) min_quote_life_seconds 2 / 10 | -1 ± 3 / -2 ± 5 | +6 ± 7 / +5 ± 7 | ~0 | no effect: reprice tolerance (1 tick) already absorbs pennying; rivals re-quote every 20-600 s |
| churn_control off (Builder world) | 0 ± 0 | 0 ± 0 | 0 | holds triggered 5 of ~1,600 side decisions |

## 4. R3 resting depth ladder (simulator prototype, `_ladder`)

Levels at anchor -/+ offs with sizes mults x the market's quote size; the anchor is our fair value when set, held
until fair value moves 1c; all ladder orders pulled for 30 s when Polymarket moves 1.5c from the anchor's reading;
never at/inside level 0, never crossing; each side's cumulative size (inventory + level 0 + ladder) clipped to the
Kelly limit (headline: headline_position_frac). Headline: 2/4/6/8c at 0.25/0.25/0.5/0.5 x 10,000.

| Variant | ΔP&L/h quiet | ΔP&L/h news | Ladder fills sh / P&L per h (q) | Capital in orders | Δworst q / n | Pick-off cost |
|---|---|---|---|---|---|---|
| base | (+499) | (+459) | - | 20.8k | - | -26 / +8 |
| ladder 2/3.5/5c, 1/2/3x* | **+46 ± 11** | **+33 ± 11** | 1.7k / +52 | 34.8k (+14k) | +414 / +126 | unchanged |
| ladder 1.5/2.5/3.5c* | **+51 ± 9** | **+34 ± 10** | 1.9k / +49 | 33.2k | +581 / +300 | unchanged |
| same, no pull on jumps* | +49 ± 9 | +40 ± 12 | 1.8k / +48 | 33.3k | +604 / +329 | unchanged: the pull is not worth anything here |
| ladder 3/5/7c (32) | +14 ± 12 | +70 ± 16 | 0.9k / +27 | 34.4k | -41 / +302 | |
| sizes 1/1/1x (32) | +12 ± 11 | +45 ± 20 | 1.2k / +29 | 31.5k | -72 / +302 | |
| re-anchor at 0.5c (32) | +28 ± 13 | +48 ± 19 | 1.3k / +31 | 34.0k | | holding is not what earns |
| headline 0.5/0.5/1/1x (32) | +20 ± 11 | +44 ± 18 | 1.5k / +32 | 33.9k | | |
| one level at 2c only (32) | +12 ± 9 | +19 ± 12 | 1.1k / +22 | 24.8k | +297 / +195 | |
| ladder + rival-aware 2c (32) | **-102 ± 31** | -19 ± 39 | 3.6k / +94 | 20.0k | -1.9k / -1.3k | dropping level 0 costs more than the ladder earns |

Ladder writes (8 seeds): 19.3 per market-hour vs 11.7 (base); deferred changes 36 -> 1,069 per hour at 30 writes/min.
3 h, 48 seeds (P&L per 3 h): ladder 1.5/2.5/3.5c **+160 ± 27** quiet, **+68 ± 32** news; capital 32k vs 18.5k; worst +590 / +327.

## 5. Rival-aware (`_rival_aware`: where another trader is inside our floor, drop level 0 on that side and rest
one held order at anchor -/+ step)

| Variant | ΔP&L/h quiet | ΔP&L/h news | Our shares/h | Edge | Δworst q / n |
|---|---|---|---|---|---|
| step 1c (32) | -7 ± 14 | -36 ± 17 | 39.6k | 1.27c | +274 / +435 |
| step 1.5c (32) | -32 ± 16 | -20 ± 17 | 31.5k | 1.48c | -585 / -526 |
| step 2c* | -2 ± 11 | +15 ± 12 | 27.2k (-19%) | 1.68c (+0.30c) | -669 / -509 |
| with the ladder (32) | -102 ± 31 | -19 ± 39 | 22.8k | 1.61c | -1.9k / -1.3k |

Fewer fills at better edge, same P&L: letting the rival take the touch flow is neutral, not a gain, because the touch
flow behind a rival is not toxic in this world (pick-off cost ~0; adverse selection ~0 live too). Same as
undercut_step_back 2c (+8 / +24).

## 6. Recommendations

| Setting | Live | Recommend | Confidence | Why |
|---|---|---|---|---|
| ref_weight | 0.7 | **0.85** | medium | +17 ± 6 / +20 ± 8 (128 seeds; the Builder saw +22 ± 13 too); +4% worst-case. The sim makes Polymarket the truth by construction, so this is an upper bound |
| min_edge | 0.01 | 0.0125 optional | low | P&L neutral (-1 ± 7 / +20 ± 9), worst-case -8-9%: a free risk cut if capital is tight |
| improve_ticks, kelly_fraction, max_half_spread, skew_*, size_max_frac, min_quote_life, writes_per_minute | live | **unchanged** | high for improve_ticks (join -17/-20) and kelly (0.15: -22/-23); others are noise | |
| undercut_step_back | 0 | 0 (0.015-0.02 is a neutral risk cut) | low | +4..+24, all < 2.2 se |
| R3 ladder | - | **build** (spec below), headline and busy markets first | medium | +46-51 ± 9-11 quiet, +33-34 ± 10-11 news per hour on 12 markets (+10% of sim P&L), 3 h: +160 ± 27 / +68 ± 32; costs +12-14k capital and ~65% more writes |
| Rival-aware | - | do not build | medium | neutral alone, -102 ± 31 with the ladder |

Capital caveat (owner update, 09:45): 90k of 101k is in positions and ~11k is cash. The ladder needs ~13k more in
orders on these 12 markets alone; it only makes sense once inventory turnover frees cash (round 2).

## 7. R3: what the Engineer must build (mm_bot.py)

The simulator expresses the ladder with per-(side, level) orders; `Bot.reconcile` / `plan_change` /
`side_needs_change` assume one order per side (`len(resting) != 1` -> replace). Needed:
1. **Level tagging**: every order we place carries (exchange, side, level) in the bot's order record (`order_meta`
   / `my_orders`); recovered orders without a tag get a level by price rank (closest to fair = 0).
2. **Per-level reconcile**: `compute_ladder(fv, ref, quote, inv, limits, book)` returns wanted levels
   [(side, level, price, size)]; `plan_change` matches level by level with `side_needs_change` (level 0 unchanged,
   with churn control; ladder levels exact price, no tolerance). Cancel only the levels that change (DELETE per order),
   cancel-all of the exchange only when every level changes; new orders go in the shared batches.
3. **Anchor state per exchange**: `ladder_fv` and `ladder_ref`; re-anchor (reprice all levels) when fair value moves
   >= `ladder_move` (0.01) from it; pull all levels for `ladder_pull_seconds` (30) when Polymarket moves >=
   `ladder_pull_jump` (0.015) from `ladder_ref` (sim: worth ~0, keep it as cheap insurance for news nights).
4. **Rules**: a level is never at or inside level 0 and never crosses the best other order; sizes clipped
   cumulatively per side (inventory + level 0 + ladder <= Kelly limit, or headline_position_frac); ladder sides
   respect reduce-only, no_bid/no_ask, the ref guard, burst mode (no ladder in burst), the capital plan (count
   ladder cash in `quote_capital_frac`) and the duplicate-quote check (key = side + level).
5. **Write priority**: ladder changes after every level-0 change (`change_key` + 1), so the ladder never delays a
   pull or a level-0 reprice; skip ladder placement when `writes_left()` < 10.
6. **Settings** (off by default): `ladder_enabled=False`, `ladder_offsets=(0.015, 0.025, 0.035)`,
   `ladder_size_mults=(1, 2, 3)`, `ladder_headline_offsets=(0.02, 0.04, 0.06, 0.08)`,
   `ladder_headline_mults=(0.25, 0.25, 0.5, 0.5)`, `ladder_move=0.01`, `ladder_pull_jump=0.015`,
   `ladder_pull_seconds=30`, `ladder_markets="headline,busy"` (quiet markets add writes and capital for little), and a
   `ladder_min_cash_frac` (e.g. 0.2: no ladder while free cash is below 20% of the account).
7. **Tests**: per-level matching keeps untouched levels (no write), re-anchor on a 1c move, pull on a 1.5c jump,
   never inside level 0 / never crossing, Kelly clipping, no duplicates after a failed cancel, write priority.

Simulator-suggested parameters: offsets 1.5/2.5/3.5c (2/3.5/5c is as good: +46 vs +51, within noise), sizes
1/2/3x, headline 2-8c at 0.25-0.5x; held vs re-anchor at 0.5c makes no difference to P&L but saves writes.

## 8. How to run

`python tests/strategy_sim.py sweep SEEDS HOURS quiet|news|slow '{base}' '{variant}' ...` as before. Extra keys:
`_share` (share of writes_per_minute), `_rival_inv` (0 = rivals without inventory caps), `_ladder` (1 or a dict of
LADDER changes), `_rival_aware` (step, price units). `SIM_CAL=day1` = the Builder's calibration.
Runtime: ~1.5 s per seed-hour per core. Use >= 32 seeds; 128 when |Δ| < 2 se.

# Round 2: inventory turnover and capital (on 21523e8: age skew, capital ceiling, race-net limits, fl-bias)

## What changed in the simulator
- `compute_quote` now gets `net_inv` (= inv), `age_hours` (share-weighted age of FIFO lots of our fills, as
  `Bot.age_hours`) and `adding_factor` (`capital_ceiling_adding_size_factor` while the capital fraction is over
  `capital_in_positions_max_frac`).
- Capital fraction = (`_bg_cap` + our positions here) / 100k. `_bg_cap` stands for the other ~225 markets'
  positions (default 60k). At 60k, free cash = 100k − 60k − positions (~9k) − cash in level-0 orders (~20k) ≈ 11k,
  the live figure. Live positions are 90k, but at bg 70k+ the 0.75 ceiling already binds, see (d).
- **Lagged mark** (`pnl_lag`) = cash + inventory × VWAP of every trade in that market over the last 30 min (last trade
  if none): a proxy for the exchange's `currentPrice`. **Position age**: share-weighted median age of held shares
  at the end. "cap" = peak cash in our positions here / peak capital fraction.
- Prototypes: `_fast_unload` (after our fill with >= 2c edge, the reducing side at fv −/+ 0.5c for 300 s, size = the
  fill, only if better than the normal quote), `_lad_gate` (ladder only while free cash >= x × account).
- Note: the age skew cannot act in 1 h runs (it starts after 1 h): use the 3 h table for (c).

## 1 h, 32 seeds (ΔP&L/h vs live defaults; base quiet +501 ± 39 (lagged mark +329), news +419 ± 27 (+221); cap 9.2k / 0.69; age 0.5 h)

| Variant | ΔP&L q | Δlag q | ΔP&L n | Δlag n | cap q | age q | Verdict |
|---|---|---|---|---|---|---|---|
| (a) headline 0.05 | **-56 ± 18** | **-56 ± 16** | -33 ± 17 | -19 ± 15 | 7.8k | 0.49 | real loss; -1.4k capital |
| headline 0.03 | **-115 ± 22** | **-96 ± 21** | **-70 ± 22** | -40 ± 18 | 6.7k | 0.46 | real loss |
| size_max 0.01 | -14 ± 13 | -12 ± 14 | +22 ± 19 | +20 ± 16 | 8.7k | 0.46 | noise |
| headline 0.05 + size_max 0.01 | -68 ± 18 | -57 ± 19 | +1 ± 19 | +5 ± 15 | 7.5k | 0.44 | loss (quiet) |
| headline 0.03 + size_max 0.01 | -121 ± 20 | -99 ± 22 | -58 ± 21 | -26 ± 19 | 6.2k | 0.41 | real loss |
| (b) skew_per_quote 0.0075 | -16 ± 11 | -22 ± 13 | -1 ± 14 | -2 ± 10 | 8.7k | 0.46 | noise |
| skew_per_quote 0.01 | -35 ± 12 | -25 ± 15 | -3 ± 13 | -4 ± 13 | 8.1k | 0.48 | slight loss (quiet) |
| (c) age skew off / 0.005 per hour | 0 | 0 | 0 | 0 | | | no positions older than 1 h in 1 h runs |
| (e) fast unload | -10 ± 9 | -9 ± 12 | +1 ± 8 | 0 ± 9 | 9.2k | 0.48 | noise |
| (f) ref_weight 0.8 | +17 ± 13 | +15 ± 15 | **+52 ± 19** | **+41 ± 15** | 9.9k | 0.49 | gain in news only (1 h) |
| ref_weight 0.85 | +22 ± 17 | +16 ± 21 | +31 ± 15 | +21 ± 11 | 9.5k | 0.50 | weak gain (1 h), but see 3 h |
| (d) bg 60k: ceiling off / 0.90 | 0 | 0 | 0 | +1 ± 1 | 9.2k / 0.69 | | 0.75 never binds at bg 60k |
| bg 60k: ceiling 0.60 (factor 0) | **-459 ± 38** | **-301 ± 38** | **-356 ± 26** | **-175 ± 30** | 0.8k / 0.61 | 0.74 | stops all adding quotes: -90% volume |
| bg 60k: ceiling 0.60, factor 0.5 | -69 ± 15 | -51 ± 14 | -17 ± 13 | -7 ± 14 | 7.9k / 0.68 | 0.50 | half size on the adding side costs far less |
| bg 70k base (ceiling 0.75 binds) | (+370 ± 33, lag +248) | | (+286 ± 22, lag +155) | | 6.6k / 0.77 | | the live default is a cliff |
| bg 70k: ceiling off / 0.90 | **+131 ± 21** | **+80 ± 21** | **+134 ± 21** | **+67 ± 18** | 9.2k / 0.79 | | the 0.75 ceiling costs ~26% of P&L when it binds |
| bg 70k: factor 0.5 | **+92 ± 17** | **+49 ± 15** | **+102 ± 19** | **+60 ± 16** | 8.4k / 0.78 | | recovers ~70% of that |
| bg 70k: ceiling 0.60 | -370 | -248 | -286 | -155 | 0 | | no quoting at all |
| (g) ladder 1.5/2.5/3.5c, no gate | +17 ± 14 | +9 ± 18 | **+43 ± 15** | **+29 ± 14** | 9.7k | 0.51 | gain in news; ~+14k in orders |
| ladder, gate 10% free cash | +18 ± 15 | +9 ± 18 | +42 ± 15 | +29 ± 14 | 9.7k | 0.51 | gate rarely shuts (free cash ~11k) |
| ladder, gate 20% free cash | 0 ± 14 | +5 ± 16 | +19 ± 13 | +20 ± 13 | 9.4k | 0.48 | ladder mostly off at 11k free cash |

## 3 h, 32 seeds (Δ per 3 h; base quiet +1,205 ± 55 (lag +887), news +1,008 ± 83 (lag +672); cap 13.5k / 0.73; age 1.2 h)

| Variant | ΔP&L q | Δlag q | ΔP&L n | Δlag n | cap q / frac | age q / n | Verdict |
|---|---|---|---|---|---|---|---|
| age skew off | -5 ± 11 | -13 ± 11 | -6 ± 9 | -6 ± 15 | 13.6k / 0.74 | 1.28 / 1.25 | age skew: noise in P&L, ages ~6% lower |
| skew_age_per_hour 0.005 | +2 ± 11 | -3 ± 14 | -12 ± 18 | -4 ± 19 | 13.5k / 0.73 | 1.16 / 1.18 | noise |
| skew_per_quote 0.0075 | +6 ± 22 | -2 ± 27 | +36 ± 29 | +46 ± 34 | 12.8k / 0.73 | 1.17 / 0.99 | noise; -5% capital, -16% age (news) |
| skew_per_quote 0.01 | -11 ± 29 | -8 ± 29 | -11 ± 35 | +1 ± 37 | 12.5k / 0.73 | 1.08 / 0.96 | noise; -7% capital |
| 0.0075 + age 0.005 | -1 ± 23 | -8 ± 26 | +11 ± 28 | +37 ± 30 | 12.6k / 0.73 | 1.10 / 0.93 | noise; youngest inventory |
| fast unload | -18 ± 17 | -24 ± 22 | -2 ± 22 | -8 ± 22 | 13.7k / 0.74 | 1.18 / 1.14 | noise-to-negative |
| headline 0.05 | **-94 ± 31** | **-117 ± 28** | -28 ± 35 | -26 ± 43 | 12.4k / 0.72 | 1.17 / 0.99 | real loss |
| ceiling off (bg 60k) | **+21 ± 9** | +11 ± 9 | +9 ± 7 | +4 ± 8 | 13.7k / 0.74 | | the ceiling starts to bind after 2-3 h |
| ref_weight 0.85 | -23 ± 24 | **-46 ± 28** | -15 ± 27 | +5 ± 26 | 13.9k / 0.74 | 1.30 / 1.33 | **the 1 h gain does not hold over 3 h**; older inventory |

## Ranking: Δ P&L at the lagged mark (quiet + news, per hour), capital fraction <= 0.75 (bg 60k)
1. ref_weight 0.8: +15 / +41 (1 h), but ref_weight 0.85 is -46 / +5 per 3 h. Unproven.
2. R3 ladder ungated or gated at 10%: +9 / +29 (1 h), with ~14k more cash in orders.
3. Live defaults: 0.
4. Ceiling off: +11 / +4 per 3 h. Peak fraction 0.74 here; it would exceed 0.75 live.
5. Fast unload: -9 / 0. Age skew 0.005: -3 / -4 per 3 h. Skew 0.0075: -22 / -2 (1 h), -2 / +46 per 3 h.
6. Last: headline 0.05 (-56 / -19), headline 0.03 (-96 / -40), any ceiling at 0.60 with factor 0 (-301 / -175).

None of the inventory levers (skew strength, age skew, fast unload) changes P&L by more than about 2 se. The only
large effects are the size of the headline quotes and whether the capital ceiling shuts the adding side.

## Recommended defaults (round 2)
| Setting | Recommend | Confidence | Why |
|---|---|---|---|
| headline_size_frac | 0.10 (keep) | high | 0.05 costs -56 ± 18 / -94 ± 31 per 3 h, and only saves ~1.4k of capital here |
| size_max_frac | 0.02 (keep) | medium | 0.01 is noise |
| skew_per_quote | 0.005, or 0.0075 if capital is the binding problem | low | P&L neutral; 0.0075 gives -5% capital and -16% age over 3 h in news |
| skew_age_* | on, 0.0025 per hour (as merged) | low | P&L neutral; ages ~6% lower than with it off |
| capital_in_positions_max_frac | 0.75 with **capital_ceiling_adding_size_factor 0.5** (not 0) | medium | factor 0 is a cliff: -131 / -134 per hour once the ceiling binds; 0.5 keeps ~70% of that P&L |
| fast unload | do not ship it as tested | low | noise-to-negative; in the sim the normal skewed quote already sits near fair |
| kelly_fraction | 0.25 | high | round 1: 0.15 is -22 / -23 |
| ref_weight | 0.7 (keep) | medium | the 1 h gain (+15 to +52) does not hold over 3 h (-23 / -15; lagged mark -46) |
| ladder | build; gate at ladder_min_cash_frac 0.1 | low-medium | +9 / +29 at the lagged mark; a 20% gate turns it off at today's 11k free cash |

Limits: the sim does not value freed cash (no opportunity cost of capital), so it understates what a ceiling or a
faster unload is worth to a capital-starved account. `_bg_cap` is an assumption: say which background level you
believe and rerun (d).
