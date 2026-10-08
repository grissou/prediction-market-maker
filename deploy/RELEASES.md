# Releases

What went live, when, and what it switched on. Every release shipped with its new settings OFF; the files
under `deploy/package<N>/` are the staged `settings_override.json` that switched them on, in stages. A
"handover" is `deploy/handover-restart.sh`: the new code adopts the resting orders. Rollback copies live on
the server under `/opt/mmbot/backup-<date>/`. Times are UTC. The commit is the `mm_bot.py` that ran.

| # | Live from | Commit | What it added | Switched on |
|---|---|---|---|---|
| 1–2 | 2 Oct 11:21 | `83f6d45` | Realtime feed hardening, write-budget and burst control, the settings file re-read every 30 s, the handover restart, a recorder of the book | `arb_two_sided` off (buy-side arbitrage needs a liquid reference on every leg) |
| 2.3 | 2 Oct 14:22 | `c8e881f` | Hot-fixes: risk fair-value fallback, size factors no longer make orders "unsafe", every 429 logged, a watchdog that exits for systemd after 10 minutes without a cycle | writes 28/min, `burst_cycle_seconds` 60, backstop 0.8 |
| 3 | 2 Oct 16:26 | `439ac54` | Capital-ceiling factor 0.5, mark-fragility cap, a per-market edge map (inert without its file) | as 2.3 |
| 5 | 3 Oct 08:22 | `c14c92b` | The tilt estimator and the tilt-corrected reference (`ref_tilt_*`), takes from the tilted reference, the self-test's funds-refusal fix | stage 1 (`ref_tilt_enabled`, max 0.09) |
| 6 | 3 Oct 13:21 | `f4214f1` | Reducing a NO holding as a covered "sell NO" (no cash needed): the fix for the 0-cash deadlock | `reduce_no_as_sell`, `ref_tilt_headline` |
| 7 | 3 Oct 19:15 | `192280a` | NO+NO set awareness: capped covered bids, pair unwinds with follow-ups | `no_set_aware_bids`, `pair_unwind_followup`, `pair_no_unwind_max_cost` |
| 8 | 3 Oct 22:42 | `5bf3e45` | The cash gate, per-market adding factor, pair sizing, tilt exits | all three stages; `arb_enabled` off |
| 10 | 4 Oct 11:15 | `2abf782` | **Value mode**: the value floor, the bloc-delta cap, the capital allocator with a market-making reserve | `value_mode`, `bloc_delta_enabled`, `alloc_enabled` (reserve 15k), `alloc_pin` Senate |
| 12 | 4 Oct 15:44 | `fc3d38d` | Rich-leg set ladder, prefer-short, target-inventory skew, the value quote hurdle, takes respecting the reserve; later the same day `mm_risk_reserve_*` and `risk_unheld_legs` (`c7c0107`, 17:11) | stage 1; `max_worst_case_frac` 0.40; backstop 1.0 (a tripwire only) |
| 14 | 5 Oct 10:06 | `4ff7d91` | **Market-making funding**: the recycler for stale inventory, the fast refill, the room guard, `mm_funding` in status | `mm_recycle_enabled`, `mm_refill_fast`, `mm_room_guard`; reserve 20k |
| 14.1 | 5 Oct 13:08 | `124ce75` | The refill and the swaps unstuck: cancel own quotes first, rank all markets, recycler sell-first, swap room netting, `alloc_max_edge_sell` | all five flags |
| 14.2 | 5 Oct 18:25 | `64f27c8` | The swap-only value-floor margin: a swap may sell up to 3c below value only for a 5-point gain | `alloc_swap_sell_margin` 0.03, `alloc_swap_min_gain` 0.05 |
| 15 | 6 Oct 18:32 | `ed65638` | The **harvest ladder** (asks above longshots, bids under favourites, 0/2/4/6c, ≥ 8% edge) with a 10k budget carved from the reserve; the **per-state collateral cap** | `tilt_harvest_ladder`, `state_max_usd` 15,000 |
| 16 | 6 Oct 21:59 | `d9220c1` | The **armed momentum sleeve**: buys longshots only while the tilt's 24-h and 6-h slopes are rising, 10k steps, automatic flip, kill at −25% | `momentum_enabled`, `momentum_auto`, cap 10k (the Team staged 40k) |

Not deployed: 4 (superseded by 5), 9 (the long-tilt basket; built, dry-run, never switched on), 11 (the
literature review behind 12), 13 (the 20/40/40 rebalance and the first momentum sleeve; its sleeve was ported
as 16).

## Settings changed by hand after release 16

| When | Change | Why |
|---|---|---|
| 7 Oct 11:54 | `value_sell_margin` 0.01 → 0.04 | Nothing in the value book was sellable within 2c of fair; 4c raised about 14k of cash for the other layers at about 470 of expected value |
| 7 Oct 13:53 | `mm_refill_fast` → false | The fast refill retried unfilled sales every cycle and used the whole request budget; the market maker was down to 19 orders |
| 8 Oct 14:51 | `momentum_enabled`, `momentum_auto` → false | The sleeve sold 12.9k of value to fund itself and bought nothing: its candidate filter excludes every longshot the bot is short (`analysis/reports/RETURNS_ATTRIBUTION.md`) |

## Lessons the releases taught

- Deploy by handover, one release at a time, and read the first hour's `status.json` before the next.
- A feature that is OFF by default and pinned byte-identical to the previous release with the flag off can be
  deployed without fear; switching it on is the only decision.
- Mark-driven selling rules (the tilt exits of release 8) sold the value book at compressed prices: about 3.7k
  of expected value in four hours on 4 October. The value floor (release 10) is the answer.
- Anything that sells value positions to raise cash must say what it buys with the cash. The refill and the
  sleeve did not, and cost more than they earned.
