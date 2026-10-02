"""§9 Simulator parameters (Run B's simparams.py format) refreshed on 16 h of data (10-01 16:00 - 10-02 08:14).
Prints: fill arrival per quoted market-hour by tier, fill sizes, sweep rate/sizes, Polymarket move frequency and size,
tournament-Polymarket gap persistence (AR(1) half-life, bias sd), other quoters' distance from our fair value,
undercut delay, one-sided books at night, write latency/409 rate.
Run: python3 analysis/simparams2.py"""
import math
from collections import defaultdict
import data2 as d

snap, T = d.snapshots()
fills = [f for f in d.fills_reconciled() if f["t"] >= d.OPEN and f["sgn"]]


def pct(x, p):
    x = sorted(x)
    return x[min(len(x) - 1, int(p * len(x)))] if x else float("nan")


def tier(lab):
    return "headline" if "U.S." in lab else "race"


# quoted market-hours per tier (snapshot interval weights), busy = race market with >= 20 fills overall
nf = defaultdict(int)
for f in fills:
    nf[f["label"]] += 1
busy = {k for k, v in nf.items() if v >= 20 and tier(k) == "race"}
def tier2(lab):
    return "headline" if "U.S." in lab else ("busy race" if lab in busy else "quiet race")
qh = defaultdict(float)
for a, b in zip(T, T[1:]):
    w = min(b - a, 600) / 3600
    for lab, r in snap[a].items():
        if r["ob"] is not None or r["oa"] is not None:
            qh[tier2(lab)] += w
print("## fill arrival (fills on our quotes per quoted market-hour)\n")
rows = []
for tr in ("headline", "busy race", "quiet race"):
    fl = [f for f in fills if tier2(f["label"]) == tr]
    sz = [f["qty"] for f in fl]
    rows.append((tr, len({f["label"] for f in fl}), "%.0f" % qh[tr], "%.2f" % (len(fl) / qh[tr]), "%.0f" % (sum(sz) / qh[tr]),
                 "%.0f/%.0f/%.0f/%.0f" % (pct(sz, .25), pct(sz, .5), pct(sz, .75), pct(sz, .95))))
print(d.table(rows, ["tier", "markets", "quoted mkt-h", "fills / mkt-h", "shares / mkt-h", "size p25/50/75/95"]))
ev = [f for f in fills if f["t"] < d.FIRST_RO]; ng = [f for f in fills if f["t"] >= d.FIRST_RO]
print("\nevening vs night fills per quoted market-hour are in hourly.py; tier split here is over the whole period")

print("\n## sweeps (fills >3c through our fair value at quote)\n")
sw = [f for f in fills if f["fvq"] is not None and f["sgn"] * (f["fvq"] - f["yes_px"]) > 0.03]
hrs = (T[-1] - d.OPEN) / 3600
e = [100 * f["sgn"] * (f["fvq"] - f["yes_px"]) for f in sw]
print("count %d (%.1f/h over the whole period; %.1f/h 16-22h), size p50 %.0f p90 %.0f max %.0f, edge c p50 %.1f p90 %.1f"
      % (len(sw), len(sw) / hrs, sum(1 for f in sw if f["t"] < d.FIRST_RO) / ((d.FIRST_RO - d.OPEN) / 3600),
         pct([f["qty"] for f in sw], .5), pct([f["qty"] for f in sw], .9), max(f["qty"] for f in sw), pct(e, .5), pct(e, .9)))

print("\n## Polymarket reference moves (snapshot to snapshot, gaps <= 6 min)\n")
ch, mh = [], 0.0
for a, b in zip(T, T[1:]):
    if b - a > 360:
        continue
    for lab, r in snap[b].items():
        r0 = snap[a].get(lab)
        if r0 and r0["ref"] is not None and r["ref"] is not None:
            ch.append(r["ref"] - r0["ref"]); mh += (b - a) / 3600
ab = [abs(x) for x in ch]
print("intervals %d, unchanged %.0f%%, sd %.2fc; per market-hour: >=1c %.3f, >=2c %.3f, >=3c %.4f, >=5c %.4f"
      % (len(ch), 100 * sum(1 for x in ab if x < 1e-9) / len(ab), 100 * math.sqrt(sum(x * x for x in ch) / len(ch)),
         sum(1 for x in ab if x >= .00999) / mh, sum(1 for x in ab if x >= .01999) / mh, sum(1 for x in ab if x >= .02999) / mh,
         sum(1 for x in ab if x >= .04999) / mh))

print("\n## tournament mid - Polymarket gap (two-sided books, spread <= 5c)\n")
gaps = defaultdict(list)
for t in T:
    for lab, r in snap[t].items():
        m = d.mid(r)
        if m is not None and r["ref"] is not None and r["ba"] - r["bb"] <= 0.05:
            gaps[lab].append((t, m - r["ref"]))
bias, xs, ys, dts = [], [], [], []
for lab, g in gaps.items():
    if len(g) < 30:
        continue
    mu = sum(x for _, x in g) / len(g); bias.append(mu)
    for (t0, x0), (t1, x1) in zip(g, g[1:]):
        if t1 - t0 <= 360:
            xs.append(x0 - mu); ys.append(x1 - mu); dts.append(t1 - t0)
rho = sum(a * b for a, b in zip(xs, ys)) / sum(a * a for a in xs)
dt = sum(dts) / len(dts)
sd_tr = math.sqrt(sum(a * a for a in xs) / len(xs))
print("markets %d; persistent bias sd %.2fc; transient sd %.2fc; AR(1) rho %.2f per %.0f s -> half-life %.1f min"
      % (len(bias), 100 * math.sqrt(sum(b * b for b in bias) / len(bias)), 100 * sd_tr, rho, dt, dt * math.log(.5) / math.log(rho) / 60 if 0 < rho < 1 else float("nan")))
by_hr = defaultdict(list)
for lab, g in gaps.items():
    for t, x in g:
        by_hr[int(t // 3600)].append(abs(x))
print("median |gap| by hour (c): " + ", ".join("%s %.1f" % (d.hh(h * 3600, "%H"), 100 * pct(v, .5)) for h, v in sorted(by_hr.items())))

print("\n## other quoters relative to our fair value (snapshots where the best is not ours)\n")
db, da = [], []
for t in T:
    for lab, r in snap[t].items():
        fv = r["fv"]
        if fv is None:
            continue
        if r["bb"] is not None and (r["ob"] is None or r["bb"] > r["ob"] + 1e-9):
            db.append(100 * (fv - r["bb"]))
        if r["ba"] is not None and (r["oa"] is None or r["ba"] < r["oa"] - 1e-9):
            da.append(100 * (r["ba"] - fv))
for name, x in (("bid: fv - best other bid", db), ("ask: best other ask - fv", da)):
    print("%s: p10 %.1f p25 %.1f median %.1f p75 %.1f p90 %.1f c; through fv %.0f%%; within 1c %.0f%%"
          % (name, pct(x, .1), pct(x, .25), pct(x, .5), pct(x, .75), pct(x, .9), 100 * sum(1 for v in x if v < 0) / len(x),
             100 * sum(1 for v in x if 0 <= v < 1) / len(x)))
