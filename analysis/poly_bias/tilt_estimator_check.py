"""Why does the live T2.1 tilt estimate read 0.14 when honest rebuilds give ~0.09? Rebuilds the estimator's
cross-section from the recorder at chosen timestamps, four (+1) ways, and compares estimators.
Usage: python analysis/poly_bias/tilt_estimator_check.py DATA_DIR   (DATA_DIR holds market_data.sql; staged, ignored)

Samples (all non-headline, r = RAW Polymarket mid as recorded in snapshots.reference = what update_tilt gets):
 (a) mid of the recorded best bid/ask, every two-sided market
 (b) mm_bot.fair_value on the recorded book (other traders only, top record_book_levels levels; fv None -> out)
 (c) (b) after per-race mm_bot.normalise, exactly as the cycle does to book_fvs (a race with any None: untouched)
 (d) the bot's sample: (c), minus |r - book_fv| > ref_max_plausible_gap (reference_prices drops those), minus
     headline; liquid assumed for all (Polymarket spreads are not recorded; status: 222 of 229 liquid)
 (e) as (d) but book_fv inverted from the recorded fair_value column: fv = (1-w) book_fv + w r (w = ref_weight),
     i.e. the bot's own FULL-depth normalised book price (ref_only markets, fv == r, dropped)
Estimators: slope = the live one, sum x g / sum x^2 (x = r - 1/legs, g = r - book winsorised); wls =
WLS with weights 1/x^2 = mean of per-market g/x over |x| > 0.1; median = median of g/x over |x| > 0.1 (both are
mm_bot ref_tilt_estimator values). Needs the ref_tilt_estimator code (mm_bot after this commit)."""
import sys, sqlite3, json, statistics as st, datetime as dt, collections, os, dataclasses
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import mm_bot
CFG = mm_bot.CFG
D = sys.argv[1]
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
EP = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
HEAD = set(CFG.headline_races)
W, MINX = CFG.ref_tilt_winsor, 0.1
P = print


def estimators(samples):
    """samples: [(r, book, legs)] -> {name: s} via mm_bot.TiltEstimator (fresh: = the raw estimate, unclipped)."""
    out = {}
    for name in ("slope", "wls", "median"):
        cfg = dataclasses.replace(CFG, ref_tilt_min_markets=5, ref_tilt_max=1.0, ref_tilt_estimator=name)
        e = mm_bot.TiltEstimator(cfg)
        e.update(samples, 0.0)
        out[name] = e.s if e.ready else float("nan")
    return out


def cross_section(ts):
    T = EP(ts)
    snap = db.execute("select eid,label,best_bid,best_ask,fair_value,reference from snapshots "
                      "where ts=? and mode='live'", (ts,)).fetchall()
    books = {}
    for eid, b, a in db.execute("select eid,bids,asks from books b where ts=(select max(ts) from books where "
                                "eid=b.eid and ts<=?)", (T,)):
        books[eid] = {"bids": [{"price": p, "quantity": q} for p, q in json.loads(b)],
                      "asks": [{"price": p, "quantity": q} for p, q in json.loads(a)]}
    group, lab, ref, mid, rec = {}, {}, {}, {}, {}
    for eid, label, bb, ba, fv, r in snap:
        group[eid], lab[eid], ref[eid], rec[eid] = label.split(" ", 1)[1], label, r, fv
        mid[eid] = (bb + ba) / 2 if bb is not None and ba is not None else None
    members = collections.defaultdict(list)
    for e, g in group.items():
        members[g].append(e)
    legs = {e: len(members[group[e]]) for e in group}
    bfv = {e: mm_bot.fair_value(books.get(e), CFG) for e in group}
    nfv = dict(bfv)
    for m in members.values():
        if len(m) > 1:
            nfv.update(mm_bot.normalise({e: nfv[e] for e in m}))
    w = CFG.ref_weight
    inv = {e: (rec[e] - w * ref[e]) / (1 - w) if rec[e] is not None and ref[e] is not None
           and abs(rec[e] - ref[e]) > 1e-12 else None for e in group}
    def samp(px, plaus=False):
        return {e: (ref[e], px[e], legs[e]) for e in group if group[e] not in HEAD and ref[e] is not None
                and px.get(e) is not None and not (plaus and abs(ref[e] - px[e]) > CFG.ref_max_plausible_gap)}
    S = {"a": samp(mid), "b": samp(bfv), "c": samp(nfv), "d": samp(nfv, True), "e": samp(inv, True)}
    sp = {}
    for e, bk in books.items():
        b_, a_ = (mm_bot.depth_price(bk[k], CFG.fv_min_depth) for k in ("bids", "asks"))
        sp[e] = a_ - b_ if a_ is not None and b_ is not None else None
    for lim in (0.05, 0.02):
        S[f"d sp<={lim}"] = {e: v for e, v in S["d"].items() if sp.get(e) is not None and sp[e] <= lim + 1e-9}
    return S, lab


def contrib(S):
    xs = {e: (r - (1 / l if l > 1 else .5), max(-W, min(W, r - b))) for e, (r, b, l) in S.items()}
    den = sum(x * x for x, _ in xs.values())
    return {e: (x, g, x * g / den) for e, (x, g) in xs.items()}


tss = [t for (t,) in db.execute("select distinct ts from snapshots where mode='live' and ts>='2026-10-02T08:30' "
                                "order by ts")]
last = tss[-1]
S, lab = cross_section(last)
P(f"## At {last}: s by sample x estimator (n)")
P("| sample | n | slope (live) | wls (1/x^2) | median |\n|---|---|---|---|---|")
for k, v in S.items():
    e = estimators(list(v.values()))
    P(f"| ({k}) | {len(v)} | {e['slope']:.3f} | {e['wls']:.3f} | {e['median']:.3f} |")
P("\n## Stress: sample (d) with a share of the |x| > 0.45 tails pinned at the winsor (gap +-0.08, g/x ~ 0.17)")
P("| pinned | slope | wls | median |\n|---|---|---|---|")
for share in (0.0, 0.5, 1.0):
    k, out = 0, []
    for r, b, l in S["d"].values():
        x = r - (1 / l if l > 1 else .5)
        if abs(x) > 0.45:
            k += 1
            if (k % 2 == 0 and share >= 0.5) or share >= 1.0:
                b = r - 0.08 * (1 if x > 0 else -1)
        out.append((r, b, l))
    e = estimators(out)
    P(f"| {share:.0%} | {e['slope']:.3f} | {e['wls']:.3f} | {e['median']:.3f} |")
ca, cd, ce = contrib(S["a"]), contrib(S["d"]), contrib(S["e"])
P("\n## 15 biggest |contribution (d) - contribution (a)| to the slope s; x = r - c, g = winsorised gap")
P("| market | r | x | g (a) mid | g (d) bot | g (e) | contrib (a) | contrib (d) | g/x (d) |\n|---|---|---|---|---|---|---|---|---|")
f = lambda v: "-" if v is None else f"{v:+.3f}"
for e in sorted(set(ca) | set(cd), key=lambda e: -abs(cd.get(e, (0, 0, 0))[2] - ca.get(e, (0, 0, 0))[2]))[:15]:
    xa, ga, qa = ca.get(e, (None, None, 0.0)); xd, gd, qd = cd.get(e, (None, None, 0.0))
    x = xd if xd is not None else xa
    P(f"| {lab[e]} | {S['a'].get(e, S['d'].get(e))[0]:.3f} | {x:+.3f} | {f(ga)} | {f(gd)} | {f(ce.get(e, (0, None))[1])} "
      f"| {qa:+.4f} | {qd:+.4f} | {f(gd / xd if xd and abs(xd) > 1e-9 and gd is not None else None)} |")
P("\n## Tail vs middle (sample d): share of sum x^2, mean g/x by |x| band")
xs = contrib(S["d"])
for lo, hi in ((0, .1), (.1, .3), (.3, .45), (.45, .51)):
    band = [(x, g) for x, g, _ in xs.values() if lo <= abs(x) < hi]
    if band:
        P(f"|x| {lo:.2f}-{hi:.2f}: n {len(band)}, x^2 share {sum(x*x for x, _ in band)/sum(x*x for x, _, _ in xs.values()):.0%}, "
          f"mean g/x {st.mean(g / x for x, g in band if abs(x) > 1e-9):.3f}, winsorised {sum(abs(g) >= W - 1e-12 for _, g in band)}")

P("\n## Stability: hourly (first snapshot of each hour), sample (e) and (c)")
P("| ts | n(e) | slope (e) | wls (e) | median (e) | slope (c) | median (c) |\n|---|---|---|---|---|---|---|")
seen = set()
for t in tss:
    if t[:13] in seen:
        continue
    seen.add(t[:13]); S2, _ = cross_section(t)
    if len(S2["c"]) < 50:
        continue
    e, c = estimators(list(S2["e"].values())), estimators(list(S2["c"].values()))
    P(f"| {t[5:16]} | {len(S2['e'])} | {e['slope']:.3f} | {e['wls']:.3f} | {e['median']:.3f} "
      f"| {c['slope']:.3f} | {c['median']:.3f} |")

P("\n## Big positions: r' = c + (1 - s)(r - c) at s = 0.09 / 0.14, gap r' - mid (headline legs tilt only with "
  "ref_tilt_headline)")
P("| market | r | legs | mid | r' 0.09 | gap 0.09 | r' 0.14 | gap 0.14 |\n|---|---|---|---|---|---|---|---|")
snap = {l: (bb, ba, r) for l, bb, ba, r in db.execute(
    "select label,best_bid,best_ask,reference from snapshots where ts=?", (last,))}
nlegs = collections.Counter(l.split(" ", 1)[1] for l in snap)
for l in ("Dem U.S. House", "Rep U.S. House", "Rep Rhode Island Senate", "Rep New Hampshire Governor",
          "Rep Georgia Senate"):
    bb, ba, r = snap[l]; n = nlegs[l.split(" ", 1)[1]]; m = (bb + ba) / 2
    a, b = mm_bot.tilted_ref(r, .09, n), mm_bot.tilted_ref(r, .14, n)
    P(f"| {l} | {r:.3f} | {n} | {m:.4f} | {a:.4f} | {100*(a-m):+.2f}c | {b:.4f} | {100*(b-m):+.2f}c |")
