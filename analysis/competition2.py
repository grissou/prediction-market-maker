"""§6 Competition from snapshots (2-4 min resolution; best bid/ask INCLUDE our own orders).
 - per hour: two-sided spread of all books (median, share <=1c), of books where we had no quote, one-sided books,
   our share of quoted sides at the best, and 'others only' books wider than 3c (a 'last bot standing' window)
 - undercut delay: for each new price we post (journal quote line changes a side), the first snapshot that shows
   that price still ours and someone strictly better: time from posting; and the share never undercut before
   we moved
 - contested vs uncontested markets (share of snapshots someone else is ahead)
Run: python3 analysis/competition2.py"""
import bisect
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
S = d.Series(snap, T)


def med(x):
    x = sorted(x)
    return x[len(x) // 2] if x else None


H = defaultdict(lambda: defaultdict(list))
for t in T:
    h = H[int(t // 3600)]
    for lab, r in snap[t].items():
        bb, ba, ob, oa = r["bb"], r["ba"], r["ob"], r["oa"]
        if bb is None or ba is None:
            h["onesided"].append(1); continue
        h["onesided"].append(0)
        sp = ba - bb
        h["spread"].append(sp)
        if ob is None and oa is None:
            h["sp_noq"].append(sp)
        if ob is not None:
            h["at_best"].append(ob >= bb - 1e-9)
        if oa is not None:
            h["at_best"].append(oa <= ba + 1e-9)
        h["head" if "U.S." in lab else "race"].append(sp)
rows = []
for hk in sorted(H):
    h = H[hk]
    sp = h["spread"]
    rows.append((d.hh(hk * 3600), "%.1f" % (100 * med(sp)), "%.0f%%" % (100 * sum(1 for x in sp if x <= 0.0100001) / len(sp)),
                 "%.0f%%" % (100 * sum(1 for x in sp if x > 0.0300001) / len(sp)),
                 "%.1f" % (100 * med(h["race"])), "%.1f" % (100 * med(h["head"])) if h["head"] else "",
                 len(h["sp_noq"]) // max(1, len(set())) and "%.1f" % (100 * med(h["sp_noq"])),
                 "%.0f%%" % (100 * sum(1 for x in h["sp_noq"] if x > 0.0300001) / len(h["sp_noq"])) if h["sp_noq"] else "",
                 "%.1f" % (sum(h["onesided"]) / (len(h["onesided"]) / 237)),
                 "%.0f%%" % (100 * sum(h["at_best"]) / len(h["at_best"])) if h["at_best"] else ""))
print(d.table(rows, ["hour", "median spread c", "<=1c", ">3c", "races", "headline", "books we don't quote: median c",
                     "...>3c", "one-sided books / snapshot", "our quoted sides at best"]))

# undercut delay
qs = d.quotes()
posts = []       # (label, side, price, t)
prev = {}
for q in qs:
    p = prev.get(q["label"])
    for side in ("bid", "ask"):
        if q[side] is not None and (p is None or p[side] != q[side]):
            posts.append((q["label"], side, q[side], q["t"]))
    prev[q["label"]] = q
# when does each posted price stop being ours (next quote line for that label that changes the side)?
nxt = {}
by = defaultdict(list)
for q in qs:
    by[q["label"]].append(q)
res_delay, never, behind_first, at_first, n = [], 0, 0, 0, 0
per_mkt = defaultdict(lambda: [0, 0])
ONLY_AT_BEST = __import__("os").environ.get("AT_BEST") == "1"   # only prices posted at or inside the previous best
for lab, side, px, t in posts:
    if ONLY_AT_BEST:
        r0 = S.at(lab, t, after=False, tol=300)
        b0 = r0 and (r0["bb"] if side == "bid" else r0["ba"])
        if b0 is None or (side == "bid" and px < b0 - 1e-9) or (side == "ask" and px > b0 + 1e-9):
            continue
    L = by[lab]
    i = bisect.bisect_right([x["t"] for x in L], t)
    end = next((x["t"] for x in L[i:] if x[side] != px), T[-1])
    ts_ = [u for u in S.t.get(lab, []) if t < u <= end]
    seen = False
    for k, u in enumerate(ts_):
        r = S.r[lab][S.t[lab].index(u)]
        ours = r["ob"] if side == "bid" else r["oa"]
        if ours is None or abs(ours - px) > 1e-9:
            continue
        best = r["bb"] if side == "bid" else r["ba"]
        if best is None:
            continue
        ahead = best > px + 1e-9 if side == "bid" else best < px - 1e-9
        if not seen:
            n += 1
            per_mkt[lab][0] += 1
            if ahead:
                behind_first += 1; per_mkt[lab][1] += 1
        seen = True
        if ahead:
            res_delay.append(u - t); break
    else:
        if seen:
            never += 1
res_delay.sort()
q = lambda p: res_delay[int(p * (len(res_delay) - 1))]
print("\nposted prices observed in a later snapshot: %d; someone better at the first snapshot: %.0f%%; undercut before we moved: %d; never undercut before we moved: %d"
      % (n, 100 * behind_first / max(1, n), len(res_delay), never))
print("time from posting to the first snapshot showing someone better: p10 %.0f s, p25 %.0f s, median %.0f s, p75 %.0f s, p90 %.0f s"
      % (q(.1), q(.25), q(.5), q(.75), q(.9)))
print("(snapshots are 2-4 min apart, so delays under ~150 s are not resolved)")

# contested vs uncontested markets
share = sorted((v[1] / v[0], k, v[0]) for k, v in per_mkt.items() if v[0] >= 10)
lo = [x for x in share if x[0] < 0.25]
print("\nmarkets with >=10 observed posts: %d; undercut at first sight <25%% of the time: %d (%s)"
      % (len(share), len(lo), ", ".join("%s %.0f%%" % (x[1], 100 * x[0]) for x in lo[:8])))
print("median share undercut at first sight: %.0f%%" % (100 * share[len(share) // 2][0]))
