# Package 12 red team (L: rich-leg set ladder, prefer-short, L3 unwind gate; M: close override, target skew)

Base 33d6bd4 (claude/finisher-package9), branch claude/p12-redteam. Each finding below was reproduced by a test that
failed on 33d6bd4 first (tests/test_p12_redteam.py, run against `git show 33d6bd4:mm_bot.py`: 6/13 before the
RT12-1..5 fixes, RT12-6 and RT12-7 failed the same way), then fixed minimally. Every fix sits behind the P12 flags
already there (all still OFF by default), so flags-off behaviour is unchanged (the p12 "identical to a1b3eea" grids
still pass).

## Findings

| id | sev | what | status |
|---|---|---|---|
| RT12-1 | HIGH | Ladder kept resting on a stale / unknown favourite price | fixed |
| RT12-6 | MEDIUM | Our own ladder defeated the L3 gate: NO+NO set bought back at a cost | fixed |
| RT12-2 | MEDIUM | L3 gate refused the B3 unwind the allocator had planned: allocator stalled | fixed |
| RT12-3 | MEDIUM | A ladder the exchange refuses was re-sent every cycle (write budget / 429) | fixed |
| RT12-7 | MEDIUM | Target skew ratchet: the holding-as-target unbraked the adding side everywhere | fixed |
| RT12-4 | LOW-MED | Target skew also applied in reduce-only (global_reduce / flatten window) | fixed |
| RT12-5 | LOW | alloc_set_rich_leg + tilt_exit_take_split_sets sell both legs of the same sets | warning added |

### RT12-1 (HIGH): the ladder rested on an unknown or moved favourite price
`alloc_ladder_tick` kept a resting ladder whenever the race was "soft" (could not be judged), and an unknown `alloc_p`
counted as soft. A soft race kept its orders with **no price check at all**, so it kept them even when the unknown p
was the favourite's own (the Polymarket feed stale for more than 30 s, or the market illiquid), for up to MAX_ORDER_TTL
(2 h). The ladder is priced from p alone and is hidden from the quote engine's safety pulls (plan_exchange). So on
election night a dead feed would leave covered "sell NO" orders on the favourite at the old price, exactly when the
favourite can collapse. Second case: another leg's p is missing (soft) while the favourite's p is known and has
fallen (0.98 to 0.80). The YES bids at 0.92 / 0.90 / 0.88 stayed, which sells NO at 0.08 when it is worth 0.20.
Fix: a soft race keeps its ladder only while the rich leg's own p is known and every level is at or below
p + value_sell_margin. Otherwise it is pulled at once. A transient soft state (busy, traded this cycle, another leg's p)
still keeps it.

### RT12-6 (MEDIUM): our own ladder defeated the L3 gate
The ladder rests at the favourite's best bid, then -2c and -4c. `arb_levels` (used by `nono_unwind_gated`) skips a
whole price level where we have a bid. So with the ladder resting, the gate read the favourite's bid from below our
levels, or found none (`None`, which means "not gated"). The NO+NO pair unwind then fired at a cost. Reproduced: bids
from other traders summed to 1.01, asks to 1.05, with `pair_no_unwind_max_cost` 0.05. arb_plan returned
`unwind buy 1000 sets`, which buys back the whole set at 5c a set and cancels the ladder that was selling it at a
profit. That is exactly what L3 exists to stop. Fix: with L3 on, an unwind at a cost (asks sum > 1) waits while an L1
ladder order rests on any leg of the race. A profitable unwind (asks < 1) still fires (tested).

### RT12-2 (MEDIUM): the L3 gate blocked the B3 unwind the allocator had planned
The allocator ranks a NO+NO set as a B3 sale by its asks cost and registers it. `arb_plan` then refuses it under L3
whenever the bids sum > 1, and an allocator registration only bypasses the asks test. The pair sits in `set_wait` for
ALLOC_SET_WAIT (900 s). No new plan runs while a pair is pending, and the next run plans the same thing again, so the
allocator stalls for as long as the tilt lasts. (This happens with L3 on and L1 off, and also with L1 on at ~0 cash,
because the ladder is then not placed and B3's "laddered" skip does not apply.) Fix: with `pair_no_unwind_asks_le1`,
`alloc_plan` does not rank a set race the gate would refuse (blocked_by `set_bids_gt_1`).

### RT12-3 (MEDIUM): the exchange refusing a ladder caused a write every cycle
A ladder level the cash gate refuses costs no write, because the batch is dropped locally. A level the exchange
refuses (non-ok result) left the race unrecorded, so the next cycle sent the same batch again. Reproduced: 5 cycles
gave 5 batch writes. On 3 Oct the exchange refused set-breaking NO sales at 0 cash, so with 15 races this is up to 15
wasted writes per cycle, which risks the write budget / 429 and starves the quotes. Fix: a race whose whole ladder the
exchange refused (or whose batch raised) is not re-sent for ALLOC_LADDER_REFUSED_WAIT (900 s). The fix logs it once.

### RT12-7 (MEDIUM): the target skew became a ratchet
The M2 fallback target is "the current holding when its edge-held > 0 (value_mode)". For a long, edge-held is
(p - bid) / bid, which is positive whenever p is above the best bid, i.e. on almost every position. So skew_inv is
inv - inv = 0: no inventory skew on either side, wherever we hold. The ADDING side then bids unbraked up to the hard
limit, and the new holding becomes the target again. Fix (keeps the spec's intent that a +EV holding is not quoted as
something to unload): `compute_quote(skew_add_flat=True)`. With a skew_inv, the side that adds to the race-netted
position keeps the skew from flat when that is the more cautious price, and the reducing side keeps the target's skew.
`decide` sets it only when the market's target is the holding fallback (no allocator plan for it). An allocator target
above the holding still lifts the bid (tested).

### RT12-4 (LOW-MEDIUM): target skew in reduce-only
`decide` applied the target skew in reduce-only too: global_reduce (worst case over the risk cap) and the flatten
window. With value_mode and a +EV holding, the reducing quote lost its skew exactly when risk is over the cap.
Without value_mode, a stale allocator target could steer the flatten. Fix: no target skew in reduce-only; the skew is
from flat, as before P12.

### RT12-5 (LOW): the ladder together with F2b set splits
`tilt_exit_take_split_sets` (F2b) sells the LONGSHOT-NO leg of a set (the value leg, which the ladder never sells),
while the ladder sells the favourite-NO leg. Together they break the same sets from both sides and compete for the same
cash-gate budget. Both may stay allowed (each keeps its own price rules), but `warn_settings` now logs one warning while
both are on. The recommendation is to turn F2b off when the ladder is on.

## Checked and found sound (no change)
- **Wrong leg:** a strict-max p, so a tie gives no ladder. A missing p on any leg means no new ladder. In 3-leg races
  only the favourite is laddered (also tested by the builder). The longshot-NO is never touched by L1. p is alloc_p
  (race-scaled, liquid, <= 30 s old).
- **Size:** at most min(nono_set_part, the set part not already on sale by our other covered NO sales). If more is on
  sale, everything is pulled and re-planned. Whole shares. Levels are merged when capped to one price.
- **cover_no_qty:** the ladder carries no "level" meta (order_level 0), so it is subtracted exactly once at level 0, by
  the R3 "other levels" sum at level > 0, and not at all for the set part under no_set_aware_bids. cash_free skips the
  ladder only for the ladder's own sizing.
- **Self-cross:** the cap is at least a tick under the book ask and under every own ask (resting and the last quote).
  sl_guard_quote keeps the quote's ask (and its keep limit) above the ladder top. Takes, arbitrage and allocator IOCs
  cancel the ladder first.
- **Wrapper:** `plan_exchange` hides only orders tagged `set_ladder` and turns a cancel-all into per-order cancels.
  Cancel-alls outside plan_exchange (stop, kill, takes) still take the ladder, which is the safe direction.
- **Restart:** the orders' meta is persisted and `alloc_ladder` is not. An orphan whose race is soft is pulled. With a
  plan, orders exactly at target with >= 50 min of life keep their queue spot. With the allocator off, any tagged order
  left is pulled.
- **Stale book under L3:** take_arbitrage re-downloads the race and re-runs arb_plan (and so the gate) before sending.
- **close_override_utc:** max(API close, override) only; "" and whitespace mean off; an invalid value is refused by
  validate_overrides and, defensively, ignored with one alert per value. The basket keeps the API close and the
  tournament end. The 15-min stop, the windows, the phase line and carry_ramp all read hours_to_close. The cache is
  keyed by the string, so a changed override is never stale.
- **compute_quote's new arguments** all default to the old behaviour (flags-off grids identical).

## Caveats (not changed: for the owner)
1. **At ~0 cash the ladder sells (almost) nothing.** The cash gate prices a set-breaking covered sale at 1.0 a share
   (the 3 Oct refusals), so only a lone part goes. L1 frees cash only once some cash exists, or the sets' lone parts
   exist. Expect `blocked_by.cash` in status.
2. **A take, arbitrage or allocator IOC on the favourite cancels the ladder.** It is re-placed only at the next hourly
   re-quote (by design), so up to 1 h can pass with no ladder.
3. **A favourite flip-flopping near a tie** (p ~0.50 / 0.50, never exactly equal) moves the ladder between legs on each
   flip (3 cancels + 1 batch). The value floor still holds at every price; there is no hysteresis.
4. **alloc_prefer_short** drops a buy when the other leg can be shorted, but does not check that short's depth, the
   bloc cap or the turnover the planner later applies. The cost is a missed buy, never a bad trade. It applies to
   2-leg races only, never where we hold YES on the other leg.
5. **close_override_utc** extends every market, including one whose own settlementDate is earlier than the tournament
   end. It can also extend past a market's settlementDate when that is later than the tournament end. Today every
   market's close is the tournament end (4 Nov 00:00), so this is moot unless SIG's data changes. If the exchange stops
   trading at its own close anyway, orders are refused (FATAL_API_CODES apply as for any order).
6. **stop_minutes_before_close 0** is in the live range (spec: 0-120). It means quoting to the last second of the close,
   on election night. Use >= 5.
7. **The allocator's M2 targets persist** until its next run, even when the pair was dropped or expired. The MM leans
   toward a holding that will not come, within skew_max (2c) and the value floor.
8. **Per-market cap and the target:** a +EV holding over its per-market limit is still blocked on the adding side (hard
   limit). Its reducing side follows the target, i.e. no push to unload. global_reduce now restores the skew (RT12-4).

## Tests
tests/test_p12_redteam.py: 21 checks (RT12-1..7). Suites on this branch: test_p12_alloc 95/95, test_p12_quote 80/80,
test_alloc 123/123, test_value_mode 101/101, test_p12_redteam 21/21, test_mm_bot 600/600.
