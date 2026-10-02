"""§10(d) Which trade-average mark fits the account series? Our own fills are the only tape we have (partial).
For each candidate rule, mark(market, t) is computed from OUR fills up to t (falls back to the snapshot mid when the
rule has no trade); model equity = 100,000 + cash + sum(position x mark). Reports, per rule: residual (real - model)
sd and step sd over all 290 snapshots, the residual at 08:13, and the fit on intervals whose fills are >= 80%
headline shares (U.S. Senate/House, where we are a large part of the volume): slope and RMS of dE vs d(model).
Run: python3 analysis/valuation_tape.py"""
import math
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
acct = {r[0]: r for r in d.account()}
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN and f["sgn"]]


def rule_mark(hist, t, rule):
    if not hist:
        return None
    k, p = rule
    if k == "last":
        sel = hist[-p:]
        return sum(f["qty"] * f["yes_px"] for f in sel) / sum(f["qty"] for f in sel)
    if k == "avg":
        sel = hist[-p:]
        return sum(f["yes_px"] for f in sel) / len(sel)
    if k == "tw":          # time-window VWAP over p minutes; none in window -> last trade
        sel = [f for f in hist if f["t"] >= t - 60 * p] or hist[-1:]
        return sum(f["qty"] * f["yes_px"] for f in sel) / sum(f["qty"] for f in sel)
    if k == "ema":         # EMA over trades, weight p per trade
        m = hist[0]["yes_px"]
        for f in hist[1:]:
            m += p * (f["yes_px"] - m)
        return m


RULES = [("mid", None), ("last", 1), ("last", 5), ("last", 20), ("avg", 5), ("avg", 20), ("tw", 15), ("tw", 30), ("tw", 60), ("ema", 0.1), ("ema", 0.3)]
hist = defaultdict(list); pos = defaultdict(float); cash = 0.0; i = 0
out = []
for t in T:
    while i < len(fills) and fills[i]["t"] <= t:
        f = fills[i]; i += 1
        hist[f["label"]].append(f); pos[f["label"]] += f["sgn"] * f["qty"]; cash -= f["sgn"] * f["qty"] * f["yes_px"]
    if t not in acct:
        continue
    vals = {}
    for r in RULES:
        v = 0.0
        for lab, p in pos.items():
            if abs(p) < 1e-9:
                continue
            row = snap[t].get(lab)
            m = d.mid(row) if row else None
            mk = m if r[0] == "mid" else rule_mark(hist[lab], t, r)
            if mk is None:
                mk = m if m is not None else (hist[lab][-1]["yes_px"])
            v += p * mk
        vals[r] = v
    head = sum(f["qty"] for f in fills if out and out[-1][0] < f["t"] <= t and "U.S." in f["label"])
    allq = sum(f["qty"] for f in fills if out and out[-1][0] < f["t"] <= t)
    out.append((t, acct[t][6], cash, vals, head, allq))

print("| rule | resid mean | resid sd | step sd | resid 08:13 | headline-interval slope | headline RMS(dE-dmodel) |")
print("|---|---|---|---|---|---|---|")
for r in RULES:
    rs = [e - 100000 - c - v[r] for (t, e, c, v, *_ ) in out]
    n = len(rs); m = sum(rs) / n
    st = [b - a for a, b in zip(rs, rs[1:])]
    xs, ys = [], []
    for a, b in zip(out, out[1:]):
        if b[5] > 0 and b[4] >= 0.8 * b[5] and b[0] - a[0] < 400:
            xs.append((b[2] + b[3][r]) - (a[2] + a[3][r])); ys.append(b[1] - a[1])
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    name = r[0] if r[1] is None else "%s %s" % r
    print("| %s | %.0f | %.0f | %.0f | %.0f | %.2f (n=%d) | %.0f |" % (name, m, math.sqrt(sum((x - m) ** 2 for x in rs) / n),
          math.sqrt(sum(x * x for x in st) / len(st)), rs[-1], sxy / sxx if sxx else float("nan"), len(xs),
          math.sqrt(sum((y - x) ** 2 for x, y in zip(xs, ys)) / len(xs)) if xs else float("nan")))
