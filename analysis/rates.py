"""§8 Write rate vs 409/429 per hour (old code until 08:08, new code after).
The journal has no line per request, so writes are estimated from the per-market quote lines (one line per
market whose quote changed): old code = one cancel write per changed market (DELETE of the one changed
side's order, or one cancel-all on the exchange when both sides change) + one POST /orders/batch per
<=20 new orders within a cycle (quote lines <5 s apart = one cycle). 409 = 'batch of N orders failed (409
REQUEST_IN_FLIGHT)', 429 = 'RATE LIMITED'. Peak minute = busiest minute's estimated writes in that hour.
Run: python3 analysis/rates.py"""
import math
from collections import defaultdict
import data2 as d

qs = d.quotes()
prev = {}
H = defaultdict(lambda: defaultdict(float))
M = defaultdict(float)
cyc_new, cyc_t = 0, None


def flush(t, n):
    if n:
        b = math.ceil(n / 20)
        H[int(t // 3600)]["batches"] += b
        M[int(t // 60)] += b


for q in qs:
    if cyc_t is None or q["t"] - cyc_t > 5:
        flush(cyc_t or q["t"], cyc_new); cyc_new = 0
    cyc_t = q["t"]
    p = prev.get(q["label"])
    h = H[int(q["t"] // 3600)]
    h["lines"] += 1
    gone = 0
    for side in ("bid", "ask"):
        old = p[side] if p else None
        if old != q[side]:
            if old is not None:
                gone += 1
            if q[side] is not None:
                cyc_new += 1; h["new"] += 1
    if gone:   # both sides changed -> one cancel-all on the exchange; one side -> one DELETE
        h["cancels"] += 1; M[int(q["t"] // 60)] += 1
    prev[q["label"]] = q
flush(cyc_t, cyc_new)
for t, k, txt in d.events():
    H[int(t // 3600)]["e_" + k] += 1
    if k == "batch_fail" and "409" in txt:
        H[int(t // 3600)]["b409"] += 1
rows = []
for hk in sorted(H):
    h = H[hk]
    if hk * 3600 < d.OPEN - 1:
        continue
    w = h["cancels"] + h["batches"]
    peak = max([M[m] for m in range(hk * 60, hk * 60 + 60)] or [0])
    rows.append((d.hh(hk * 3600), int(h["lines"]), int(h["new"]), int(h["cancels"]), int(h["batches"]), "%.1f" % (w / 60), int(peak),
                 int(h["b409"]), "%.0f%%" % (100 * h["b409"] / h["batches"]) if h["batches"] else "", int(h["e_429"]),
                 int(h["e_deferred"]), int(h["e_traded_imm"]), int(h["e_recovered"])))
print(d.table(rows, ["hour", "quote lines", "new orders", "cancel writes", "batch writes", "est writes/min", "peak min",
                     "409 batches", "409/batch", "429", "deferred (new code)", "traded immediately", "recovered lost orders"]))
