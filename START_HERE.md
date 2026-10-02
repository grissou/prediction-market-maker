# START HERE (Builder run, branch `claude/pr2-strategy-yamsgp`, PR #3)

Status: **finished** (2026-10-02). Rebased onto Run A's final `claude/pr1-safe-fixes` (c754b51). All suites green: test_mm_bot 283/283, test_strategy 38/38, test_ref_prices 35/35, test_stress 20/20 in 4 parallel runs under load.

Branch note: the brief names `claude/pr2-strategy`, but this session can only push to `claude/pr2-strategy-yamsgp`. `claude/pr3-ops-analytics` was never created.

## 1. Runs A and B
- **Run B (PR #1).** Day-one P&L was really about +475; the reported +26.5% counted cash locked in orders twice. The inventory skew paid the edge away. Profit came from fills with more than 2c edge. Every market was contested by bots sitting 0.5-1c from fair value. Between 157 and 189 markets went unpriced.
- **Run A (PR #2), complete.** Self-test, parallel writes, book freshness, order recovery, backoff, Polymarket fetching, burst protection, a 30 writes/min budget, and churn control (which also reprices first after a Polymarket move of 0.5c or more).

## 2. What I built
Simulator: `tests/strategy_sim.py` runs the real quoting code against 2-4 rival bots that undercut each other and pick off stale quotes, plus sweeps, informed flow and slow writes. Runs being compared use the same seeds. To compare settings:

`python tests/strategy_sim.py sweep SEEDS HOURS quiet|news|slow '{base}' '{variant}' ...`

| Change | Off switch | Evidence |
|---|---|---|
| **R1** Equity = the API value (it already includes locked cash) | `reserved_cash_mode="auto"` | Data §1: correlation with locked cash 0.9993; the limits were up to 48k too high |
| **R2** Skew sized to the market's quote size; skewed quotes never go through fair value | `skew_mode="share"`, `skew_max=1`, `max_skew_through=1` | Simulator: **+468 ± 103 per hour (quiet), +515 ± 97 (news)**; edge 0.21c → 1.25c; picked-off shares 3,775 → 203; drawdown 93 → 21. Peak worst-case rises 5.4k → 8.3k |
| **R5** Thin books priced from Polymarket (book mid within 3c), 1.5c edge, 100 shares | `ref_only_enabled=False` | Data §4: 157-189 markets unpriced, 72 positions stuck. Unit tests only |
| **R5b** In those markets, the side that shrinks a held position quotes the normal size | `ref_only_reduce_full=False` | Rep U.S. Senate +7,585 would otherwise sell 100 shares at a time |
| **R7** Reduce-only on min(sum of maxima, 15c swing + 3 sd); the sum of maxima stays as a 60% backstop | `risk_model="sum_max"` | Day one at 20:18: 13.5k instead of 23.3k |
| **R4 settings** Join the best price, or step back when undercut (defaults unchanged) | `improve_ticks=1`, `undercut_step_back=0` | Tested; see the ledger |
| **Recorder** Other traders' top 3 levels with sizes, plus tournament trades, in `market_data.sqlite` | `record_books=False` | No extra requests, about 10-15 MB/day; needed to measure rival bots |
| **Bug fix** A failed cancel-everything no longer forgets live orders | — | Bug on main and pr1: after 3 failed cycles, a failed cancel-all doubled every quote for 15 s or more. Deterministic test; stress failures went from 4 in 12 runs to 0 |

## 3. Parameter changes
| Setting | Old | New | Evidence | Expected effect |
|---|---|---|---|---|
| reserved_cash_mode | auto | ignore | §1 | correct kill switch and cap |
| skew_mode / skew_per_quote / skew_max / max_skew_through | share (0.3c per 100 sh) / – / none / 4c | quote / 0.5c / 2c / 0 | §3; simulator | more edge, fewer quotes picked off; inventory lingers longer |
| ref_only_enabled / _max_gap / _min_edge / _size_frac / _reduce_full | – | on / 3c / 1.5c / 0.1% / on | §4 | about 150 more markets quoted |
| risk_model / risk_swing_shock / risk_z / worst_case_backstop_frac | sum of maxima at 30% | correlated / 15c / 3 / 60% | §2 | no overnight freeze in reduce-only. **Loosens a safety limit: owner decision** |
| record_books / record_book_levels | – | on / 3 | §3 data gap | rival data |
| Tested and **unchanged**: headline_size_frac 0.10, min_edge 0.01, ref_weight 0.7, skew_per_quote 0.005, improve_ticks 1 | | | see ledger | |

## 4. Ideas ledger
Simulator runs use 16-32 seeds × 1 h at 30 writes/min; ΔP&L is per hour over 12 markets. No idea here is original; all come from IDEAS.md.

| Idea | Outcome |
|---|---|
| R1, R2, R5, R7 | Built (above) |
| R3 depth ladder | **Not built.** It needs per-level order matching in `reconcile`, which Run A rewrote. Best next step, now that PR #2 is final |
| R4 join / step back | Built as settings, kept off. Join: −19 ± 34 (quiet), +33 ± 35 (news), neutral. Step back 2c: −221 / −149; 3c: −245 / −115 |
| R6 orphan orders | Run A (done) |
| R8 smaller headline quotes | **Rejected** with R2 in place: edge stays about 1.25c at every size, so P&L follows volume. 5%: −145 ± 38; 2%: −262 ± 50. Smaller sizes only reduce risk (worst-case 8.9k → 3.8k) |
| R2 strength | 0.25c: +28 ± 17 / +57 ± 28 (noise level); 0.75-1c: −48 to −113. Kept at 0.5c |
| min_edge 0.5c / 1.5c | −91 / −51 (quiet). Kept at 1c |
| ref_weight 0.85 | +22 ± 13 / +15 ± 18 / +8 ± 11 (32 seeds), worse bad-seed P&L on news days. Kept at 0.7 |
| T1 Polymarket + learned offset | **Rejected**: −76 ± 25 / −58 ± 44 (5-min half-life), −85 / −60 (30-min); also worse when marked at the tournament mid |
| T5 book logger | Built (Recorder) |
| Fast pulls after a Polymarket move | Done by Run A (churn control, `urgent_ref_move`) |
| T2-T4, T6-T12, W1-W7, brainstorm rounds | Not tested (time). W1 (election-night taking) and W2 (variance for the prize) need owner decisions first |

## 5. Deploy plan
1. **PR #2 (pr1) first.** pr3 doesn't exist.
2. **Then this branch.** Copy `mm_bot.py` and restart. The cancel fix also protects pr1, so deploy both together if possible.
3. **First 10 minutes.** In the log line `account X | worst-case loss Y (risk Z)`, X should be about 100k and Z below 30% of X. Markets priced should go from about 80 to more than 150. No fill in fills.csv should be through fair value.
4. **Days 1-3.** Watch edge per share and how large held positions get. If inventory builds, set `skew_per_quote=0.0075`. After a day of recorder data, measure rival repricing speed and floors from the `books` table.
5. **Rollback.** Change one setting per item (table above), or redeploy pr1.

## 6. Risks
- **R2:** positions are shed more slowly; peak worst-case was about 50% higher in the simulator.
- **R5:** a wrong Polymarket match on a thin book. The 3c gap check limits this.
- **R7:** an election-night swing larger than 15c. The 60% backstop remains.
- **Simulator limits:** Polymarket is the truth by construction, rivals follow simple rules, and a 1-hour horizon can't see multi-day inventory risk. Treat the numbers as rankings, not forecasts.

## 7. Owner decisions
1. **R7 loosens the reduce-only trigger.** Recommend: yes, with the backstop.
2. **Rule questions for SIG:** how unresolved positions are valued at the end, whether a batch counts as 1 write or N, and Smart Score. Recommend: ask; the answers change W1, W2 and T2.
3. **Next run:** R3 ladder, then rival analysis from recorder data. Recommend: yes.
