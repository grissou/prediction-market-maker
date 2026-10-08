# Texas Rep digital on the live book, and the "~1% a day" question (snap04, data to 4 Oct 15:57 UTC)

Read-only. Scripts (each runs in about 5 s):
- `Y_texas.py`: imports `analysis/p10/H_outcome.py` with `SNAP` set to snap04, and `mm_bot`'s `race_variance`,
  `worst_case_loss` and `bloc_sensitivities`. N = 20,000, rho 0.55 (control markets 0.85), seed 11.
- `Y_onepct.py`: book and snapshot replay from 3 Oct 22:47 to 4 Oct 15:57 (0.72 days, 922 snapshots), plus journal parsing.

## Q3. The Texas Rep digital, rho 0.55, on the live book

### Inputs

**Texas Senate today.** Race-scaled p is Dem 0.649 / Rep 0.351 (raw 0.655 / 0.355). We hold −100 Dem and +100 Rep, so
nothing yet.

**The two routes into "Rep wins Texas":**

| Route | Cost per $1 of payoff | Recorded depth (3 levels) |
|---|---|---|
| Short Dem TX YES at the bid | 0.345-0.355 (bids 0.655 / 0.65 / 0.645) | 20.8k shares for $7.2k |
| Long Rep TX YES at the ask | 0.350-0.360 (asks 0.35 / 0.355 / 0.36) | 15.1k shares for $5.4k |

- The short route is cheaper by about 0.5c per unit, so it fills first. The script interleaves the two by cost per unit.
- The visible book holds **only $12.6k**. For anything beyond that, the script assumes fills at 0.37 per $1
  (Dem bid 0.63). That is about 2c of impact, so the $15-25k rows are optimistic on entry cost.
- Both routes sit at roughly p (0.351), so the sleeve is a 0-edge digital: E[final] stays flat.

**The allocator's offline plan** (LIVE_0404 scenario D, $14.1k of cash):
- short Rep RI Senate 7,929 @ 0.155
- Dem CT Governor 7,251 @ 0.855
- Dem RI Senate 809 @ 0.865
- Dem RI Governor 588 @ 0.85

It takes E from 106.4k to 108.6k.

### Results

| Base | Sleeve | E[final] | P≥120k | P≥150k | P≤85k | P≤70k | P≥133k (top 50) |
|---|---|---|---|---|---|---|---|
| (i) book | $0 | 106.5k | 4.5% | 0.0% | 0.5% | 0.0% | 0.0% |
| (i) book | $10k | 106.5k | 29.2% | 0.0% | 5.3% | 0.1% | 8.1% |
| (i) book | $15k | 106.4k | 34.6% | 0.3% | 14.1% | 0.5% | 21.4% |
| (i) book | $20k | 106.1k | 35.2% | 8.3% | **32.9%** | 1.8% | 33.0% |
| (i) book | $25k | 105.9k | 35.2% | 21.4% | 45.8% | 5.3% | 35.0% |
| (ii) book + plan | $0 | 108.7k | 7.5% | 0.0% | 0.6% | 0.0% | 0.0% |
| (ii) book + plan | $10k | 108.7k | 30.8% | 0.0% | 3.2% | 0.0% | 11.0% |
| (ii) book + plan | $15k | 108.6k | 34.1% | 2.0% | 9.0% | 0.2% | 25.7% |
| (ii) book + plan | $20k | 108.3k | 34.9% | 11.5% | **22.9%** | 1.1% | 33.1% |
| (ii) book + plan | $25k | 108.1k | 35.1% | 25.7% | 39.8% | 3.2% | 34.5% |

**This differs from lit_portfolio's PORT-2/3** (P≥150k 30%, P≤85k 6.3%), which was computed on a 112k-EV plan core.
Today's core is worth about 106.5k, so a lost $20k sleeve lands its mean near 86.5k. Under PORT-3's floor rule,
X* ≈ q10(W | sleeve loses) − 85k, and that X* is now only about $10k.
- At $10k, P(top 50) is 8-11% and P(≤85k) is 3-5%.
- At $20k, P(top 50) is 33% and P(≥150k) is 8-11%, but there is a 23-33% chance of finishing at or below 85k.

### Cap check (how the bot computes the caps, calibrated to status)

`settlement_risk` reproduces status exactly: **34,734**. The pieces:
- fv = the snapshot `fair_value`.
- A held market without a fair value uses `risk_fv`, which here is the liquid reference.
- An **unheld leg without a fair value counts as 0.5** (`fvs.get(e) or last_fv or 0.5`).
- risk = 0.15·|party_delta 10,620| + 3·sqrt(Σ race variance).

`total_worst_case` comes out at 81,024 (exact) and party_delta at −10,620 (exact). Bloc is 2,502 against 2,439 in
status (rho 0.55, all references treated as liquid).

**Side finding: about 7.9k of the 34.7k is that 0.5 fallback.** 106 contracts have no fair value this cycle. Their
phantom 0.5 legs dilute the race probabilities inside `race_variance`. With every leg at its reference, the number is
26.8k.

**Limits:**
- risk ≤ 0.40 × 99.6k = 39.9k
- worst ≤ 0.85 × 99.6k = 84.7k
- |bloc| ≤ 0.05 × 99.6k = 4.98k

**Room today:**
- **The backstop has only 3.7k of room.** Every dollar of cash turned into a near-certain position raises
  sum-of-maxima by about that dollar.
- The plan alone breaches the backstop: worst 97.3k, which is +12.6k over.

**Largest sleeve each cap allows on the book as is:**

| Cap | Largest sleeve |
|---|---|
| bloc | **$3.1k** |
| backstop | **$3.6k** |
| risk 0.40 | **$5.0k** |

**The $20k sleeve on the book as is:**

| Cap | Value with the sleeve | Over the cap by |
|---|---|---|
| settlement risk | 93.6k | **+53.8k** (the sleeve adds 0.15 × 45k to party_delta plus the race variance) |
| total_worst_case | 100.7k | **+16.0k** |
| bloc | +17.9k | **+12.9k** |

Each of these alone puts the bot in global reduce-only, or in the bloc case blocks Rep-adding quotes.

On book + plan: risk +51.5k, backstop +32.3k, bloc +12.4k.

**Settings needed per size (book as is): max_worst_case_frac / worst_case_backstop_frac / max_bloc_delta_frac**

| Size | Without carve-out | With PORT-8 carve-out (sleeve counted at its cost as a stress) |
|---|---|---|
| $10k | 0.56 / 0.91 / 0.10 | 0.45 / 0.91 / 0.025 |
| $15k | 0.74 / 0.96 / 0.14 | 0.50 / 0.96 / 0.025 |
| $20k | 0.94 / 1.01 / 0.18 | **0.55 / 1.01 / 0.025** |
| $25k | 1.06 / 1.06 / 0.22 | 0.60 / 1.06 / 0.025 |

So even with PORT-8, the $20k sleeve still needs two more settings:
- max_worst_case_frac of at least about **0.55**. The 0.60 range limit allows it.
- the backstop at about **1.01-1.05**. The range allows up to 1.5.

Without the carve-out, 0.94 is out of range (max 0.60). So the $20k sleeve **cannot get through with settings alone.
PORT-8 is required** (about 30 lines, a copy of `basket_risk_split` keyed on `alloc_pin`).

**Other gates:**
- **Pins.** Add "Dem Texas Senate,Rep Texas Senate" to `alloc_pin` so the allocator never sells the sleeve.
- **Per-market limits.** `alloc_max_contract_usd` is 10k and `kelly_max_market_frac` is 0.02 (about $2k at risk
  per quote or take). The bot will not build $20k in one market by itself, so the sleeve has to be entered by hand or
  by the pin path.
- **Cash.** A Dem TX short at 0.655 locks 0.345 per share, and a Rep TX long at 0.35 locks 0.35. The $20k sleeve
  needs $20k of cash. Free cash is $4.0k (cash_gate_left 3.7k), so **about 16k of the 19.5k in resting quotes must
  be pulled**. That ends market making and leaves no cash for any other stream.

### Recommendation on Texas

**Do not put $20k on today's book.** The reasons:
- At rho 0.55, P(≤85k) is 23-33% against 0.5% now.
- It is zero-edge.
- Without PORT-8 plus max_worst_case_frac 0.55 and backstop 1.05 it forces reduce-only.
- It uses all the working cash.

**If the owner wants a shot at the top 50, the most defensible size is $10k**, and only after PORT-8 ships. It gives
P(top 50) 8-11%, P(≤85k) 3-5%, and E unchanged. Enter it through the short Dem TX route first: $7.2k of depth at
0.345-0.355, then Rep TX YES at 0.35-0.36.

Better still, defer it per PORT-10, to "late but before the close". The core's EV may still grow, and the floor rule
then allows a bigger X.

## The owner's ask: "~1% a day" = about +1k per day of expected value at the outcome, for 30 days

| Stream | Measured on snap04 | Realistic $/day of EV | Cash it needs | Locks to outcome or recycles | Variance | Write cost | Fair play |
|---|---|---|---|---|---|---|---|
| (a) Rotation: sell low edge-held, buy ≥10%/$ levels | Holdings with edge-held under 2% / 4% / 6% / 10%: **$2.6k / $8.3k / $8.7k / $31.2k** (exit-able at the top level: 2.5k / 5.1k / 5.6k / 22.4k). Of these, 9 holdings worth $960 are below 0 edge-held (South Dakota buy-back $797). Supply at ≥10%/$ with own levels stripped: **$0.97-1.29M** of locked-cash depth at all four probes (06/10/13/15:55). The top 20 turns over 13-15 names per 3 h (CT Gov, RI Sen, DE Sen, RI Gov rotate in and out). | **One-off +1.5-2.5k** in total: about +0.5k from the <4% bucket, about +1.8k if the <10% bucket is used at about 8% net, after a 1-2c spread. Spread over 2-3 days by the 15k/h turnover cap and the 0.02 sell gate. | ~0 net; sales fund buys | Recycles holdings, but each buy then sits to the outcome | Low (diversified favourites and longshot shorts) | Half a write per $ of rotation; tens of writes per day | ok |
| (b1) Stale-quote takes (journal) | TAKE lines: 452 since 3 Oct 22:47. While cash existed (22h-07h), **$5-37k of cash per hour, $0.4-3.1k of raw-reference edge per hour**. 12h-14h: **0 takes** (cash). 15h: 4 takes against **47 reserve-blocked** lines. | **0.4-0.5k/day on 5k/day** of fresh cash (about 8-10% per $). Fresh cash runs out in about 4 days. | 1:1 | **Locks to the outcome**, so it is one-off per $ | Low per take, many races | 1 write per take | ok (stale quotes) |
| (b2) Tournament quote ≥8%/$ past Polymarket at the touch | 4 Oct 00:00-15:57: **325 episodes over 84 markets**, median life 12 min (p75 58 min). **$1.09M at the touch** and $103k of edge-times-$. Short-lived ones (≤10 min): 153 episodes, $413k, $35k edge. Busiest hours: 00h, 04-05h and 08h (UTC night and US late evening); 10-15h is quiet (about 1.6-3.6k of edge per hour at the touch). | Supply is **not** the limit; cash is (see b1) | 1:1 | Locks | | | ok |
| (c) Race Dutch books (top of book, own levels stripped) | bids sum >1 by ≥1.5c: **54 per day**, 36 races, median life about 3 h. Gap times top size is **1.9k per day** (DE Senate 3.5c × 17k sets for 11.5 h). asks sum <1 by ≥1.5c: 17 per day, 0.47k per day. | **0.3-0.5k/day**, assuming 20-30% capture of the top-of-book size, and **only if `arb_enabled` goes back to true** (the override has it **false**). The set carousel (build at ≥1.02, dissolve at ≤1.01) earns about 1c per set per cycle. | A NO+NO set locks 2 − bid sum ≈ 0.97 per set, so about 10-15k is needed to catch the large ones | **Recycles** with `arb_sellback` / dissolve. If not dissolved it locks until settlement, but riskless. | **None**. worst_case_loss of a full set is 0, so it uses **no backstop room**. | 2-3 writes per set | ok |
| (d) Middle-band two-way market making | 6,340 top-of-book changes per day in 15-85c markets (6% of market-snapshots). LIVE_0404: 0 matched round trips in 4.5 h (mostly reduce-only), and 40 top-of-book markets in the 17 min after. | **Unproven: 0-0.3k/day**. Measure `mm_carry_24h` on 5 Oct. | ~19.5k resting now | Recycles if matched; otherwise drifts into inventory | Medium (adverse selection on reference moves) | High (28 writes/min budget) | ok |
| (e) Tilt and the rival | The tilt went **11.0% (00-04h) → 13.9% (08h) → 13.3% (12h) → 13.7% (status)**. It is the source of the $1M of ≥10% supply, and it keeps refreshing the edge list. It does not pay unless cash recycles. | Nothing extra. A rising tilt marks the book down but leaves EV unchanged. | | | Mark risk only | | |

### Why 1% a day is not sustained

1. **Cash, not supply, binds.** There is about $1M of ≥10%-per-$ depth on the board, against about 23.5k of working
   cash (4.0k free + 19.5k in quotes). At ~10% per $, 1k a day needs **about 10k of new cash every day**. Anything
   bought at the outcome stays locked until settlement (about 30 days). With 20-25k, that is 2-3 days of value buying,
   then nothing.
2. **The backstop binds even sooner.** total_worst_case is 81.0k against 84.7k, so **only 3.7k** of cash can be
   turned into new outcome positions before reduce-only. Without raising worst_case_backstop_frac to about 0.95-1.0,
   (b) and the allocator's plan are capped at about 3.7k in total.
3. The only recycling streams are rotation (one-off), the Dutch book carousel and market making. Together those come
   to about 0.3-0.8k per day, and the market-making part is unproven.

### The best combination for 20-25k of working cash

| Days | Streams | EV per day |
|---|---|---|
| 1-3 | Rotation (+1.5-2.5k one-off) + takes on 5k per day (0.4-0.5k) + Dutch books (0.3-0.5k) | **about 0.9-1.4k per day** |
| 4-30 | Dutch carousel (0.3-0.5k) + market-making carry (0-0.3k) + rotation into new tilt mispricings as edge-held decays (about 0.1-0.2k) | **about 0.4-0.9k per day** |

Over 30 days that is about **+10-18k of EV**, so E[final] is about 117-125k. **1% a day for 30 days (about +30k) is
not realistic.** It is plausible for the first 2-3 days, and about 0.3-0.6% a day after that.

**Settings for this combination (all existing):**

| Setting | Change | Why |
|---|---|---|
| `worst_case_backstop_frac` | **0.85 → 0.95** | about 10k more room; worst stays far below the account |
| `alloc_mm_reserve` | 15k → 5k | frees cash for rotation and takes |
| `take_respect_reserve` | keep | |
| `arb_enabled` | **false → true** | |
| `arb_cash_rule` | true | |
| `arb_sellback` | true, `arb_sellback_min_sum` 1.01 | the dissolve leg of the carousel |
| `arb_min_profit` | 0.02 | build at ≥1.02 |
| `value_quote_hurdle` | keep 0.05 | |
| `take_edge` | keep 0.05 | |
| `alloc_max_edge_sell` | 0.02 → 0.04 | releases the $8.3k in the <4% bucket |
| `alloc_min_improvement` | keep 0.03 | |

**Code (new, optional):**
- **Take-and-flip.** Rest an exit at p − value_quote_hurdle on every take, so a stale quote that reverts recycles the
  cash. The data cannot show reversion rates yet: the 153 short episodes ended, but the replay cannot tell whether
  each one ended through a reversion or a fill. Measure this before building.
- **Fix the 0.5 fallback** in `settlement_risk` for unheld legs without a fair value. Use the reference, as
  `risk_fv` does. That frees about 7.9k of phantom risk.

**Caveats.**
- The recorder keeps only 3 levels of book.
- "Own levels stripped" removes the whole price level we quote at, which is conservative.
- p is the race-scaled Polymarket reference. If the tilt is information rather than noise, every "edge" here is
  overstated (PORT-11).
- The Dutch book $/day is the top-of-book size times the gap, which is an upper bound. Capture against the rival bots
  is unmeasured.
