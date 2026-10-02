"""§1 Level test of the exchange's valuation rule.
At every snapshot: equity_model(rule) = 100,000 + cash(fills up to t) + sum(position x mark(rule)).
cash: buying YES at p costs p per share; an ask fill (sell YES / buy NO) is booked as +p per share
with the position going negative (equivalent to buying NO at 1-p).
Positions come from the snapshot `position` column (the bot's view of exchange holdings) and are compared
with cumulative fills (reconciliation). The residual (real equity - model) should be flat over time for the
exchange's rule; a residual that falls with traded volume points to a fee.
Run: python3 analysis/valuation_level.py"""
import math
from collections import defaultdict
import os
import data2 as d
POS = os.environ.get("POS", "fills")   # positions from cumulative attributed fills (default) or the snapshot column ("snap")

snap, T = d.snapshots()
acct = {r[0]: r for r in d.account()}
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN]

# reconciliation: cumulative attributed fills vs snapshot positions
cum = defaultdict(float); cash = 0.0; vol = 0.0; notional = 0.0; unk = 0.0
i = 0
rules = ("mid", "liq", "bid", "ask", "fv", "ref", "fvmid", "last")
last = {}   # our last fill price per market: a proxy for a last-trade mark (we see only our own trades)
out = []
for t in T:
    while i < len(fills) and fills[i]["t"] <= t:
        f = fills[i]; i += 1
        if f["sgn"] == 0:
            unk += f["qty"]; continue
        cum[f["label"]] += f["sgn"] * f["qty"]
        last[f["label"]] = f["yes_px"]
        cash -= f["sgn"] * f["qty"] * f["yes_px"]
        vol += f["qty"]; notional += f["qty"] * (f["yes_px"] if f["sgn"] > 0 else 1 - f["yes_px"])
    if t not in acct:
        continue
    rows = snap[t]
    # cash implied by snapshot positions is unknown for unattributed fills: use fills' cash, snapshot positions
    mism = sum(abs(r["pos"] - cum.get(l, 0.0)) for l, r in rows.items())
    vals = {}
    for rule in rules:
        v = 0.0; miss = 0.0
        for l, r in rows.items():
            p = r["pos"] if POS == "snap" else cum.get(l, 0.0)
            if abs(p) < 1e-9:
                continue
            m = d.mid(r)
            if rule == "mid": mk = m
            elif rule == "liq": mk = r["bb"] if p > 0 else r["ba"]
            elif rule == "bid": mk = r["bb"]
            elif rule == "ask": mk = r["ba"]
            elif rule == "fv": mk = r["fv"]
            elif rule == "ref": mk = r["ref"]
            elif rule == "last": mk = last.get(l, m)
            else: mk = r["fv"] if r["fv"] is not None else m
            if mk is None:
                mk = m if m is not None else (r["ref"] if r["ref"] is not None else 0.5)
                miss += abs(p)
            v += p * mk
        vals[rule] = v
    gross = sum(abs(x) for x in cum.values())
    out.append((t, acct[t][6], cash, vals, mism, vol, notional, unk, gross))

print("t        real_eq  resid(mid) resid(liq) resid(bid) resid(ask) resid(fv) resid(ref) resid(last) |pos-fills|  vol(k)  gross_pos")
for k, (t, e, c, v, mism, vol, no, unk, g) in enumerate(out):
    if k % 12 == 0 or k == len(out) - 1:
        print(d.hh(t, "%d %H:%M"), "%8.0f" % e, *["%9.0f" % (e - 100000 - c - v[r]) for r in ("mid", "liq", "bid", "ask", "fv", "ref", "last")],
              "%9.0f %7.0f %8.0f" % (mism, vol / 1e3, g))

# Variation of residual across snapshots: lower = closer to the exchange rule
print("\nresidual sd over time (only snapshots where fills reconcile within 2,000 sh):")
for rule in rules:
    rs = [e - 100000 - c - v[rule] for (t, e, c, v, mism, *_ ) in out if POS == "fills" or mism < 2000]
    n = len(rs); m = sum(rs) / n
    dif = [b - a for a, b in zip(rs, rs[1:])]
    print("%-6s n=%d mean %.0f sd %.0f  step-sd %.1f" % (rule, n, m, math.sqrt(sum((x - m) ** 2 for x in rs) / n),
                                                    math.sqrt(sum(x * x for x in dif) / len(dif))))

# Where does the residual move? Steps on intervals with and without fills, and with big fills.
print("\nresidual(mid) steps by fills in the interval:")
ft = [(f["t"], f["qty"], f["sgn"]) for f in fills]
grp = {"no fills": [], "fills <2k sh": [], "fills >=2k sh": [], "unattributed fill": []}
for a, b in zip(out, out[1:]):
    r0 = a[1] - 100000 - a[2] - a[3]["mid"]; r1 = b[1] - 100000 - b[2] - b[3]["mid"]
    inside = [x for x in ft if a[0] < x[0] <= b[0]]
    q = sum(x[1] for x in inside)
    k = "unattributed fill" if any(x[2] == 0 for x in inside) else ("no fills" if not inside else ("fills <2k sh" if q < 2000 else "fills >=2k sh"))
    grp[k].append(r1 - r0)

# Does the residual track cash locked in orders? (would mean account_value - locked is not the true equity)
print("\nresidual(mid) vs locked_in_orders:")
for name, lo, hi in (("before 05:03 ('detecting')", 0, d.REAL_FROM), ("after 05:03 (value used as is)", d.REAL_FROM, 1e12)):
    pts = [(acct[t][2], e - 100000 - c - v["mid"]) for (t, e, c, v, *_ ) in out if lo <= t < hi]
    n = len(pts); mx = sum(p[0] for p in pts) / n; my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts); sxy = sum((p[0] - mx) * (p[1] - my) for p in pts); syy = sum((p[1] - my) ** 2 for p in pts)
    b = sxy / sxx if sxx else 0
    res = [p[1] - my - b * (p[0] - mx) for p in pts]
    print("  %s: n=%d slope %.4f per SUSQie locked, intercept(at 0 locked) %.0f, corr %.2f, resid sd before %.0f after fit %.0f"
          % (name, n, b, my - b * mx, sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan"), math.sqrt(syy / n), math.sqrt(sum(r * r for r in res) / n)))
nf = []
for a, b in zip(out, out[1:]):
    if not any(a[0] < x[0] <= b[0] for x in ft) and b[0] - a[0] < 400:
        nf.append((b[1] - a[1], {r: b[3][r] - a[3][r] for r in rules}))
print("no-fill intervals: n=%d  RMS(dE)=%.0f" % (len(nf), (sum(x[0] ** 2 for x in nf) / len(nf)) ** .5))
for r in rules:
    xs = [x[1][r] for x in nf]; ys = [x[0] for x in nf]
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    print("  %-6s RMS(pred)=%5.0f RMS(dE-pred)=%5.0f  slope(dE on pred)=%.2f" % (r, (sxx / len(xs)) ** .5,
          (sum((y - x) ** 2 for x, y in zip(xs, ys)) / len(xs)) ** .5, sxy / sxx if sxx else float("nan")))
# Intervals with fills: does the account book the fill's edge vs mid at once?
# split pred_mid into (a) revaluation of the old position and (b) fills' edge vs the mid at the end
print("\nintervals with fills (<400 s): regress dE on (a) old-position revaluation at mid, (b) fills' edge vs end mid")
X = []
for a, b in zip(out, out[1:]):
    if b[0] - a[0] > 400:
        continue
    inside = [f for f in fills if a[0] < f["t"] <= b[0] and f["sgn"] != 0]
    if not inside:
        continue
    pa = b[3]["mid"] - a[3]["mid"]                                      # total change in marked value at mid
    edge = sum(f["sgn"] * f["qty"] * ((d.mid(snap[b[0]].get(f["label"]) or {"bb": None, "ba": None}) or f["yes_px"]) - f["yes_px"]) for f in inside)
    cashd = b[2] - a[2]
    reval = pa + cashd - edge
    X.append((b[1] - a[1], reval, edge, sum(f["qty"] for f in inside)))
import itertools
def ols2(rows):
    # y = b1 x1 + b2 x2 (no intercept)
    s11 = sum(r[1] ** 2 for r in rows); s22 = sum(r[2] ** 2 for r in rows); s12 = sum(r[1] * r[2] for r in rows)
    s1y = sum(r[1] * r[0] for r in rows); s2y = sum(r[2] * r[0] for r in rows)
    det = s11 * s22 - s12 ** 2
    return ((s1y * s22 - s2y * s12) / det, (s2y * s11 - s1y * s12) / det)
b1, b2 = ols2(X)
print("  n=%d  coef(reval)=%.2f  coef(edge vs mid)=%.2f  sum dE=%.0f  sum reval=%.0f  sum edge=%.0f"
      % (len(X), b1, b2, sum(r[0] for r in X), sum(r[1] for r in X), sum(r[2] for r in X)))
big = [r for r in X if abs(r[2]) > 50]
b1, b2 = ols2(big)
print("  intervals whose fills carry >50 edge vs mid: n=%d coef(reval)=%.2f coef(edge)=%.2f  sum dE=%.0f sum edge=%.0f"
      % (len(big), b1, b2, sum(r[0] for r in big), sum(r[2] for r in big)))
for k, v in grp.items():
    if v:
        print("%-18s n=%3d  mean %+6.0f  rms %5.0f  sum %+6.0f" % (k, len(v), sum(v) / len(v), (sum(x * x for x in v) / len(v)) ** .5, sum(v)))
