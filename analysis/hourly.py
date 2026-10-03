"""§1, §2, §4, §8: one row per UTC hour.
equity (real, end of hour), change, fills, shares, edge at quote, P&L at mid +60 min (all fills / sweeps >3c edge),
reduce-only share of summary lines, resting orders, markets priced, quote changes (journal quote lines = order
re-writes), 409 / 429 / deferred counts, and the per-market P&L table.
Run: python3 analysis/hourly.py"""
from collections import Counter, defaultdict
import data2 as d

snap, T = d.snapshots()
S = d.Series(snap, T)
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN]
sums = d.summaries()
ev = d.events()
qs = d.quotes()


def hour(t):
    return int(t // 3600)


def mid_after(lab, t, h):
    r = S.at(lab, t + h, after=True, tol=420)
    return d.mid(r)


H = defaultdict(lambda: defaultdict(float))
for f in fills:
    h = H[hour(f["t"])]
    h["fills"] += 1; h["sh"] += f["qty"]
    if f["fvq"] is not None and f["sgn"]:
        e = f["sgn"] * (f["fvq"] - f["yes_px"])
        h["edge"] += f["qty"] * e; h["esh"] += f["qty"]
        if e > 0.03:
            h["sweep_sh"] += f["qty"]
    m = mid_after(f["label"], f["t"], 3600)
    if m is not None and f["sgn"]:
        p = f["sgn"] * f["qty"] * (m - f["yes_px"])
        h["pnl60"] += p
        if f["fvq"] is not None and f["sgn"] * (f["fvq"] - f["yes_px"]) > 0.03:
            h["pnl60_sweep"] += p
for s in sums:
    h = H[hour(s["t"])]
    h["n_sum"] += 1; h["ro"] += s["ro"]; h["resting"] += s["resting"]; h["priced"] += s["priced"]; h["real_end"] = s["real"]
for t, k, _ in ev:
    H[hour(t)]["ev_" + k] += 1
prev = {}
for q in qs:
    h = H[hour(q["t"])]
    h["qlines"] += 1
    p = prev.get(q["label"])
    if p:
        h["side_changes"] += (p["bid"] != q["bid"]) + (p["ask"] != q["ask"])
    prev[q["label"]] = q

print("## Hourly (UTC)\n")
rows = []
last = 100000.0
for hk in sorted(H):
    h = H[hk]
    if hk * 3600 < d.OPEN - 3600:
        continue
    n = h["n_sum"] or 1
    eq = h.get("real_end", last)
    rows.append((d.hh(hk * 3600), "%.0f" % eq, "%+.0f" % (eq - last), int(h["fills"]), "%.1f" % (h["sh"] / 1e3),
                 "%+.2f" % (100 * h["edge"] / h["esh"]) if h["esh"] else "", "%+.0f" % h["edge"], "%+.0f" % h["pnl60"],
                 "%+.0f" % h["pnl60_sweep"], "%.0f%%" % (100 * h["ro"] / n), "%.0f" % (h["resting"] / n), "%.0f" % (h["priced"] / n),
                 int(h["side_changes"]), int(h["ev_409"]), int(h["ev_429"]), int(h["ev_deferred"]), int(h["ev_unexpected"] + h["ev_cycle_failed"])))
    last = eq
print(d.table(rows, ["hour", "real equity", "chg", "fills", "k sh", "edge c", "edge SUSQ", "P&L mid+60", "of which >3c",
                     "RO", "resting", "priced", "side chg", "409", "429", "deferred", "failed cyc"]))

# per-period summary
P = [("evening 16:00-22:48", d.OPEN, d.FIRST_RO), ("night RO on/off 22:48-05:12", d.FIRST_RO, d.ts("2026-10-02T05:12:00Z")),
     ("morning RO 05:12-08:08", d.ts("2026-10-02T05:12:00Z"), d.DEPLOY), ("new code 08:08-08:14", d.DEPLOY, d.ts("2026-10-02T08:15:00Z"))]
print("\n## Periods\n")
rows = []
for name, a, b in P:
    fl = [f for f in fills if a <= f["t"] < b]
    hrs = (b - a) / 3600
    ss = [s for s in sums if a <= s["t"] < b]
    e = sum(f["qty"] * f["sgn"] * (f["fvq"] - f["yes_px"]) for f in fl if f["fvq"] is not None and f["sgn"])
    esh = sum(f["qty"] for f in fl if f["fvq"] is not None and f["sgn"])
    p60 = 0.0
    for f in fl:
        m = mid_after(f["label"], f["t"], 3600) or mid_after(f["label"], f["t"], 900)
        if m is not None and f["sgn"]:
            p60 += f["sgn"] * f["qty"] * (m - f["yes_px"])
    eq0 = next((s["real"] for s in ss), None); eq1 = ss[-1]["real"] if ss else None
    rows.append((name, "%.1f" % hrs, "%.0f%%" % (100 * sum(s["ro"] for s in ss) / max(1, len(ss))),
                 "%.0f" % (sum(s["resting"] for s in ss) / max(1, len(ss))), "%.0f" % (len(fl) / hrs), "%.1f" % (sum(f["qty"] for f in fl) / hrs / 1e3),
                 "%+.2f" % (100 * e / esh) if esh else "", "%+.0f" % (e / hrs), "%+.0f" % (p60 / hrs),
                 "%+.0f" % ((eq1 - eq0) / hrs) if ss else ""))
print(d.table(rows, ["period", "hours", "RO", "resting", "fills/h", "k sh/h", "edge c", "edge/h", "P&L mid+60 /h", "real equity chg/h"]))

# per-market fill-based P&L at the last snapshot's mid (cash + position x mid)
print("\n## Per market: fill-based P&L at final mid (top/bottom 10)\n")
last_t = T[-1]
pm = defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0.0])   # cash, pos, fills, shares, edge
for f in fills:
    if not f["sgn"]:
        continue
    x = pm[f["label"]]
    x[0] -= f["sgn"] * f["qty"] * f["yes_px"]; x[1] += f["sgn"] * f["qty"]; x[2] += 1; x[3] += f["qty"]
    if f["fvq"] is not None:
        x[4] += f["sgn"] * f["qty"] * (f["fvq"] - f["yes_px"])
res = []
for lab, (c, p, n, sh, e) in pm.items():
    r = snap[last_t].get(lab)
    m = d.mid(r) if r else None
    if m is None and r:
        m = r["fv"] or r["ref"]
    if m is None:
        continue
    res.append((c + p * m, lab, n, sh, e, p, m))
res.sort()
tot = sum(r[0] for r in res)
print("total fill-based P&L at final mid: %+.0f over %d markets; real equity change %+.0f" % (tot, len(res), sums[-1]["real"] - 100000))
rows = [(r[1], r[2], "%.0f" % r[3], "%+.0f" % r[4], "%+.0f" % r[5], "%.3f" % r[6], "%+.0f" % r[0]) for r in res[:10] + res[-10:]]
print(d.table(rows, ["market", "fills", "shares", "edge", "final pos", "mid", "P&L"]))
