"""Z-1: take-and-flip. After each journal TAKE, does the tournament touch revert toward Polymarket, and could a resting
exit at (take price + half the edge) have filled? Read-only, snap04. ~30 s."""
import re, sys, bisect
from collections import defaultdict
sys.path.insert(0, "/home/claude/prediction-market-maker/analysis/p12")
from X_common import load_snapshots, journal_lines, ts_of, iso

RX = re.compile(r"^(\S+) .*TAKE (.+?): Polymarket ([\d.]+) vs stale (bid|ask) ([\d.]+) -> (buying|selling) (\d+) YES at ([\d.]+)")
snaps = load_snapshots("2026-10-03T22:00")
T = sorted(snaps)
ser = defaultdict(list)                       # label -> [(t, bb, ba, ob, oa, p)]
for t in T:
    for r in snaps[t].values():
        ser[r["label"]].append((t, r["bb"], r["ba"], r["ob"], r["oa"], r["p"]))
tend = T[-1]
takes = []
for ln in journal_lines("TAKE "):
    m = RX.search(ln)
    if not m:
        continue
    t = ts_of(m.group(1).replace("+0000", "+00:00"))
    takes.append(dict(t=t, label=m.group(2), poly=float(m.group(3)), buy=m.group(6) == "buying", q=int(m.group(7)),
                      px=float(m.group(8))))
print("takes parsed", len(takes))

def band(p):
    return "p<0.15" if p < 0.15 else ("p>0.85" if p > 0.85 else "mid")

H = [1, 6, 24]
agg = defaultdict(lambda: defaultdict(float))
rows = []
for k in takes:
    s = ser.get(k["label"])
    if not s:
        continue
    ts = [x[0] for x in s]
    i = bisect.bisect_left(ts, k["t"])
    if i >= len(s):
        continue
    p0 = s[i][5] if s[i][5] is not None else k["poly"]
    sg = 1 if k["buy"] else -1
    edge = sg * (p0 - k["px"])                      # per share, vs race-scaled p
    if edge <= 0.005:
        continue
    tgt = k["px"] + sg * edge / 2                   # resting exit at half the edge
    b = band(p0)
    a = agg[b]
    a["n"] += 1; a["usd"] += k["q"] * (k["px"] if k["buy"] else 1 - k["px"]); a["edge_usd"] += k["q"] * edge
    flip_t = None; touch_t = None
    for j in range(i + 1, len(s)):
        t, bb, ba, ob, oa, p = s[j]
        if k["buy"]:
            other_bid = bb if (bb is not None and (ob is None or abs(ob - bb) > 1e-9)) else None
            if flip_t is None and other_bid is not None and other_bid >= tgt - 1e-9:
                flip_t = t
            if touch_t is None and ba is not None and ba <= tgt + 1e-9:
                touch_t = t
        else:
            other_ask = ba if (ba is not None and (oa is None or abs(oa - ba) > 1e-9)) else None
            if flip_t is None and other_ask is not None and other_ask <= tgt + 1e-9:
                flip_t = t
            if touch_t is None and bb is not None and bb >= tgt - 1e-9:
                touch_t = t
        if flip_t and touch_t:
            break
    avail = tend - k["t"]
    for h in H:
        if avail >= h * 3600:
            a[f"elig{h}"] += 1
            if flip_t and flip_t - k["t"] <= h * 3600:
                a[f"flip{h}"] += 1; a[f"flipusd{h}"] += k["q"] * (k["px"] if k["buy"] else 1 - k["px"])
            if touch_t and touch_t - k["t"] <= h * 3600:
                a[f"touch{h}"] += 1
    # reversion of the touch toward p0 after 1h / 6h: fraction of the gap closed (the side we took from)
    for h in (1, 6):
        if avail < h * 3600:
            continue
        jj = bisect.bisect_left(ts, k["t"] + h * 3600)
        if jj >= len(s):
            continue
        t, bb, ba, ob, oa, p = s[jj]
        side_px = ba if k["buy"] else bb            # where the stale side now is
        if side_px is None or p is None:
            continue
        gap0 = edge; gap1 = sg * (p - side_px)
        a[f"rev{h}_n"] += 1; a[f"rev{h}_sum"] += (gap0 - gap1) / gap0
        a[f"rev{h}_ge50"] += (gap0 - gap1) / gap0 >= 0.5
    rows.append((k, edge, flip_t))

hours = (tend - min(k["t"] for k in takes)) / 3600
print(f"window {hours:.1f} h, ends {iso(tend)}")
print("band   n  $cash  $edge | flip(other side reaches p_take+edge/2) 1h/6h/24h (eligible) | resting exit at/inside touch 1h/6h | mean gap closed 1h/6h, share >=50%")
tot_flip6 = 0
for b, a in sorted(agg.items()):
    def f(h):
        return f"{int(a[f'flip{h}'])}/{int(a[f'elig{h}'])}"
    def g(h):
        return f"{int(a[f'touch{h}'])}/{int(a[f'elig{h}'])}"
    r1 = a["rev1_sum"] / a["rev1_n"] if a["rev1_n"] else float('nan')
    r6 = a["rev6_sum"] / a["rev6_n"] if a["rev6_n"] else float('nan')
    print(f"{b:6s} {int(a['n']):4d} {a['usd']:8.0f} {a['edge_usd']:6.0f} | {f(1)} {f(6)} {f(24)} | {g(1)} {g(6)} | "
          f"{r1:+.2f}/{r6:+.2f}  {int(a['rev1_ge50'])}/{int(a['rev1_n'])}, {int(a['rev6_ge50'])}/{int(a['rev6_n'])}")
    tot_flip6 += a["flipusd6"]
print(f"$ of take cash flippable within 6h at half the edge: {tot_flip6:,.0f}")
# recyclable edge per day: half the edge on flipped takes, per day
half = sum(k["q"] * e / 2 for k, e, ft in rows if ft and ft - k["t"] <= 6 * 3600)
print(f"half-edge kept on 6h flips: ${half:,.0f} over {hours:.1f} h = ${half / hours * 24:,.0f}/day (if cash were re-used)")
