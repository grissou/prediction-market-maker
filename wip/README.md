# Work in progress saved at wrap-up (2 Oct ~15:00 UTC), NOT applied, NOT tested by the lead

`ladder_reviewer_fixes_L1-L4.patch`: Engineer 10's uncommitted work on the R3 ladder's Reviewer findings, taken from its
worktree at wrap-up (based on commit 51893a1; the ladder itself is merged and OFF). Intended fixes: L1 a ladder level one tick
behind the touch is "stale" (tier 2), not an urgent budget-bypassing pull; L2 ladder-only pulls do not count toward
pulls_cancel_all_over; L3 1-tick tolerance, 2c re-anchor hysteresis and min_quote_life for ladder orders, the ladder leaves
ladder_min_writes free after its own changes; L4 per-order cash cap in ladder_caps. Status unknown (the engineer had not
reported); apply with `git apply --3way wip/ladder_reviewer_fixes_L1-L4.patch` on a branch from 439ac54, run every suite
(see START_HERE.md HANDOFF), and review before any use. The ladder must stay OFF until these land and a dry-run write count
shows it fits the ~30 writes/min limit.
