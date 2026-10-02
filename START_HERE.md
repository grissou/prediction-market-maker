# START HERE (Builder run, branch `claude/pr2-strategy-yamsgp`, PR #3)

Status: **stopped early**. The cloud session was suspended from about 22:00 to 07:17 UTC, so only the first hour of work exists. Everything listed here is committed, tested and switchable.

Branch note: the brief names `claude/pr2-strategy`; this session can only push to `claude/pr2-strategy-yamsgp`. The branch is rebased onto the latest `claude/pr1-safe-fixes` (36586e9). `claude/pr3-ops-analytics` did not exist at 07:20 UTC.

## 1. What Runs A and B found and built
- **Run B (PR #1)**: real day-one P&L was about +475; the +26.5% came from locked cash counted twice. Inventory skew paid the edge away. Profit came from fills with more than 2c edge. Every market was contested by bots 0.5-1c from fair value. 157-189 markets went unpriced. The worst-case measure was a sum of per-race maxima.
- **Run A (PR #2)**: self-test retries when the exchange is busy, test scenarios, parallel time-boxed writes, book freshness, recovery of lost orders, realtime backoff and faster Polymarket fetches. Burst protection and churn control (item 8) were not on the branch at 07:20 UTC.

## 2. What I built
Simulator: `tests/strategy_sim.py`. It runs the real quoting functions against 2-4 rival bots that penny and pick off stale quotes, plus human depth, noise trades with sweeps, informed trades after jumps, write latency and a write budget. Same seeds give the same world in both runs being compared. Run it with `python tests/strategy_sim.py [seeds] [hours] [quiet|news|slow] key=value...`.

| # | Change | Off switch | Key number (simulator: 8 seeds × 1 h × 12 markets, compared on the same seeds) |
|---|---|---|---|
| R1 | Equity = the API value (it already includes locked cash) | `reserved_cash_mode="auto"` | kill switch and cap on real equity (they were up to 48k too high) |
| R2 | Skew sized to each market's quote size; skewed quotes never go through fair value | `skew_mode="share"`, `skew_max=1`, `max_skew_through=1` | **P&L +468 ± 103 per hour (quiet), +515 ± 97 (news)**; edge 0.21c → 1.25c; shares picked off 3,775 → 203; max drawdown 93 → 21; peak worst-case 5.4k → 8.3k |
| R5 | Thin books priced from Polymarket (raw mid within 3c), 1.5c edge, 100 shares | `ref_only_enabled=False` | unit-tested only; the simulator has no thin-book markets yet |
| R7 | Reduce-only on min(sum of maxima, 15c swing + 3 sd), with sum of maxima as a 60% backstop | `risk_model="sum_max"` | day one at 20:18: 13.5k instead of 23.3k |

## 3. Parameter changes
| Setting | Old | New | Evidence | Expected effect |
|---|---|---|---|---|
| reserved_cash_mode | auto | ignore | §1: corr 0.9993 | correct limits |
| skew_mode / skew_per_quote / skew_max / max_skew_through | share (0.003c/sh) / – / none / 4c | quote / 0.5c / 2c / 0 | §3: −804 at ≤−1c edge; simulator +468/h | more edge, fewer quotes picked off; inventory lingers longer |
| ref_only_* | – | on, gap 3c, edge 1.5c, 100 sh | §4 | about 150 more markets quoted |
| risk_model / risk_swing_shock / risk_z / worst_case_backstop_frac | sum of maxima at 30% | correlated / 15c / 3 / 60% | §2: sd 4.4k vs 23k | no overnight freeze in reduce-only. **Loosens a safety limit** |

## 4. Ideas ledger
- Built: R1, R2, R5, R7.
- Not tested (no time): R3 ladder, R4 join or step back, R8 smaller headline sizes, T1-T12, W1-W7, the brainstorm rounds. R6 is Run A's work (done on PR #2).
- Original ideas: none. Everything here comes from IDEAS.md.

## 5. Deploy plan
1. Deploy PR #2 (pr1), then pr3 if it exists, then this branch: copy the files and restart.
2. First 10 minutes: check the log line `account X | worst-case loss Y (risk Z)`. X should be about 100k (no locked cash added) and Z below 30% of X. Check that markets priced rises from about 80 to more than 150, and that no quote sits through fair value in fills.csv.
3. Days 1-3: watch edge per share and the size of held positions. If inventory builds, raise `skew_per_quote` to 0.01.
4. Rollback: use the off switch for each item above (one setting each), or redeploy pr1.

## 6. Risks
- R2: positions take longer to shed; peak worst-case was about 50% higher in the simulator.
- R5: a wrong Polymarket match on a thin book. The 3c gap check limits this.
- R7: a correlated election-night swing larger than 15c. Keep the 60% backstop.

## 7. Owner decisions
1. R7 loosens the reduce-only trigger. **Recommend: yes**, with the 60% backstop.
2. Rule questions (IDEAS.md): how unresolved positions are valued at the end, writes/min, Smart Score. **Recommend: ask SIG.**
3. A new Builder run for R3, R4 and R8 and the competition rounds. **Recommend: yes**; the simulator is ready for it.
