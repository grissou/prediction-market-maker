# Is the bot's capital leaning toward Polymarket, and is it stuck there?
*2 October 2026, 08:14 UTC; 290 live position snapshots (median gap 2.6 minutes) and 1,813 fills from the first 16 hours of the tournament.*

**In one paragraph.** Yes on both counts: 76% of the open position capital was entered on the Polymarket side of the gap (11% against it), and size followed the gap, with mean capital per position 1,194 at gaps of 5c or more against 175 to 434 below. Those positions stick: 65% of toward-Polymarket capital had exited against 85% of the against-Polymarket capital. Open capital had a median age of 8.8 hours while closed lots lasted a median 14 minutes. The claim that the gain was "mostly marks" was not confirmed: of the +1,509 account gain, +1,398 was spread capture and other residual, not unrealised marks.

## Why it mattered
This was the first check of how the bot behaved once the tournament price drifted away from Polymarket. It showed the bot building large, one-sided, slow-moving positions on the Polymarket side of every gap, sized up to its per-market cap. That fed the decision to value positions at the outcome and to manage capital with an explicit reallocation step rather than letting the quoting rules accumulate inventory.

## What was done
Position history was rebuilt from the recorder's position snapshots. Trades were priced at the tournament mid and matched into lots first-in, first-out. A trade counts as "toward Polymarket" if the bot bought where the mid was below the Polymarket reference, or sold where it was above. The gap had to exceed 0.5c at the snapshot before the trade. Trades inside one snapshot interval net out, so turnover is a lower bound; a cross-check against the fills file found the snapshot method captured 96% of fill volume.

## What it found

### Which side of the gap the capital is on
- Open positions: 159; position capital 52,189 (51% of the 101,509 account).
- Open capital by side at entry: toward Polymarket 39,614 (76%), against 5,962 (11%), neutral (gap under 0.5c) 6,390 (12%), no reference 222.
- All entries since the open: toward 114,752 (61%), against 38,981 (21%), neutral 30,986 (16%), no reference 3,183 (2%).
- Share of entered capital already exited: toward 65%, against 85%, neutral 79%.
- By the gap at the latest snapshot: toward 40,440 (77%), against 1,628 (3%).
- The 15 biggest positions held 30,162 (58% of all), 76% of it toward Polymarket at entry.

| Market | Position | Capital | Mid | Reference | Gap now (c) | Gap at entry (c) | Toward | Age (h) | Unrealised |
|---|---|---|---|---|---|---|---|---|---|
| Rep U.S. House | −8,246 | 7,174 | 0.130 | 0.075 | −5.5 | −3.2 | 100% | 5.6 | −103 |
| Dem U.S. Senate | 7,335 | 4,603 | 0.627 | 0.625 | −0.2 | −0.8 | 0% | 8.8 | +18 |
| Rep U.S. Senate | 7,335 | 2,696 | 0.367 | 0.365 | −0.3 | −0.0 | 0% | 13.7 | +0 |
| Rep New Hampshire Governor | 2,272 | 2,016 | 0.887 | 0.96 | 7.2 | 6.7 | 100% | 2.0 | −10 |
| Dem Rhode Island Senate | 2,185 | 2,005 | 0.917 | 0.992 | 7.5 | 7.3 | 100% | 8.8 | −3 |
| Dem MN-01 House | −2,666 | 2,000 | 0.250 | 0.215 | −3.5 | −2.6 | 100% | 4.7 | +34 |
| Dem New Hampshire Governor | −2,247 | 1,983 | 0.117 | 0.041 | −7.7 | −6.2 | 100% | 8.6 | +13 |
| Rep Rhode Island Senate | −2,173 | 1,983 | 0.087 | 0.009 | −7.9 | −7.4 | 100% | 8.5 | −14 |
| Dem North Carolina Senate | 1,204 | 1,081 | 0.897 | 0.955 | 5.8 | 5.0 | 100% | 1.2 | −0 |
| Rep WI-03 House | −1,407 | 1,010 | 0.282 | 0.255 | −2.7 | −1.7 | 100% | 12.3 | −11 |
| Dem TN-05 House | −979 | 884 | 0.098 | 0.074 | −2.3 | −1.5 | 100% | 10.1 | −7 |
| Dem Minnesota Senate | 836 | 721 | 0.863 | 0.895 | 3.2 | 3.5 | 100% | 5.8 | +19 |
| Dem FL-16 House | −850 | 710 | 0.165 | 0.155 | −1.0 | −1.7 | 100% | 9.6 | +9 |
| Rep Michigan Senate | −917 | 658 | 0.282 | 0.285 | 0.3 | −0.7 | 100% | 12.7 | +5 |
| Dem U.S. House | 722 | 641 | 0.887 | 0.925 | 3.8 | 1.6 | 100% | 13.7 | −20 |

Liquidation haircuts on these rows (longs to best bid, shorts to best ask) ranged from 2 to 55; the top row's was 41.

### Realised versus unrealised; account versus liquidation value
- Account value 101,509 at the exchange mark, so +1,509 since the open.
- Realised on closed lots (at mid): +154. Unrealised on open lots: −42. Residual (spread captured against the mid, fees, mark differences): +1,398.
- Liquidation haircut (top of book only, no depth): 428, so liquidation value about 101,081.
- If every open leg moved to Polymarket the book would gain +1,839; if the gap never closes that is not earned.
- Worst-case settlement loss 32,418 (32% of the account); capital locked in resting orders 32,981.

### Turnover and holding time
- Shares entered 321,639, exited 243,439: exits/entries 0.76 (by capital, 187,902 entered, 135,671 exited, 0.72).
- Open lots: capital-weighted median age 8.8 h; 69% older than 6 h, 18% older than 12 h, none older than 24 h (the tournament was 16.2 h old).
- Closed lots: share-weighted median holding time 0.23 h.

### Does size follow the gap?
Across the 159 open positions, the correlation of signed position with gap at entry was +0.33, and of capital with the size of the gap +0.27. Using the gap now: +0.38 and +0.33.

| Gap at entry | Positions | Capital | Share | Mean capital |
|---|---|---|---|---|
| 0.0 to 0.5c | 36 | 6,288 | 12% | 175 |
| 0.5 to 1.0c | 30 | 9,535 | 18% | 318 |
| 1.0 to 2.0c | 44 | 9,013 | 17% | 205 |
| 2.0 to 5.0c | 41 | 17,804 | 34% | 434 |
| 5.0c and above | 8 | 9,549 | 18% | 1,194 |

By the gap now, the share of markets with a position: 0.0 to 0.5c, 20 of 23 (87%); 0.5 to 1.0c, 23 of 28 (82%); 1.0 to 2.0c, 40 of 67 (60%); 2.0 to 5.0c, 62 of 102 (61%); 5.0c and above, 8 of 9 (89%).

### Verdict: partly confirmed
- Confirmed, the lean: 76% of open capital entered toward Polymarket (11% against, 12% no clear gap); 77% against 3% by the gap now.
- Confirmed, size follows the gap: 49 positions entered at 2c or more hold 52% of capital. Five of the eight biggest sit at about 2,000, which is the per-market cap `kelly_max_market_frac` (2% of the account), so quarter-Kelly sizing is at its ceiling there.
- Confirmed, stuck: gaps on the big positions widened since entry (Rep U.S. House −3.2c → −5.5c). No convergence so far. Worst case 32% of the account.
- Not confirmed: "the gain is mostly marks". The +26% day-one figure is not in the data; the day-one account value double-counted locked cash.
- Not in this snapshot: "90% of capital in positions" (51% here, after a night of reduce-only quoting). 14% of position capital is the U.S. Senate pair, long both legs at 7,335 each: locked and neutral, not a Polymarket bet.

## Caveats
- Trades inside one snapshot interval net out, so turnover is a lower bound.
- The snapshot is from 08:14 UTC after a night of reduce-only quoting; the afternoon state that prompted the question is later than the data.
- The liquidation haircut uses the top of book only, with no depth.
- "Share exited" uses current-mid capital over entry capital and is approximate.
