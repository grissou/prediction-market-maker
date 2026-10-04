"""Z-exit: how often can a held value position be exited near p (recycling the cash at ~0 loss)? For p > 0.85 legs:
share of market-cycles with a rival best bid >= p - x; for p < 0.15 legs (held as shorts): rival best ask <= p + x.
4 Oct 00:00-15:57, own levels skipped. Read-only. ~10 s."""
import sys
from collections import defaultdict
sys.path.insert(0, "/home/claude/prediction-market-maker/analysis/p12")
from X_common import load_snapshots

snaps = load_snapshots("2026-10-04T00:00")
X = (0.0, 0.01, 0.02, 0.03)
cnt = defaultdict(lambda: [0] * (len(X) + 1)); mk = defaultdict(set)
for t, rows in snaps.items():
    for r in rows.values():
        p = r["p"]
        if p is None:
            continue
        if p > 0.85 and r["bb"] is not None and (r["ob"] is None or abs(r["ob"] - r["bb"]) > 1e-9):
            k = "fav: bid >= p - x"; gap = p - r["bb"]
        elif p < 0.15 and r["ba"] is not None and (r["oa"] is None or abs(r["oa"] - r["ba"]) > 1e-9):
            k = "longshot: ask <= p + x"; gap = r["ba"] - p
        else:
            continue
        cnt[k][0] += 1
        for i, x in enumerate(X):
            if gap <= x + 1e-9:
                cnt[k][i + 1] += 1
                if i == 2:
                    mk[k].add(r["label"])
for k, v in cnt.items():
    print(f"{k}: {v[0]} market-cycles; " + ", ".join(f"x={x:.2f}: {100 * v[i + 1] / v[0]:.2f}%" for i, x in enumerate(X))
          + f"; markets ever within 2c: {len(mk[k])}")
