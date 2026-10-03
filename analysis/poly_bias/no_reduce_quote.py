"""Why does the bot rest no REDUCING quote in held dead/quiet markets? Snapshot 2 Oct 20:37 UTC (ops-snapshot-2026-10-02b).
Report only. Usage: python analysis/poly_bias/no_reduce_quote.py DATA_DIR (market_data.sql, fills.csv, journal_*.log,
status.json; staged with git show, never committed).
snapshots.our_bid / our_ask are ex.quote, i.e. what decide() WANTED this cycle (record(), live 439ac54 l.5770), not
the resting orders: a null there is a decide() outcome, never a write-budget deferral.
Causes are tested in decide()/compute_quote() order (line numbers: claude/finisher-package5 mm_bot.py; live 439ac54
in brackets): fv None 4681 [4147] -> ref guard 4701-02 [4165] -> reduce-only race-net clip 2362-64 [2041-43]."""
import sys, sqlite3, json, csv, re, collections, datetime as dt
D = sys.argv[1]
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
race = lambda lab: lab.split(" ", 1)[1] if lab.split(" ", 1)[0] in ("Dem", "Rep", "Ind") else lab
ts = db.execute("select max(ts) from snapshots where mode='live'").fetchone()[0]; END = T(ts)
R = {e: dict(lab=l, bb=bb, ba=ba, fv=fv, ref=rf, ob=ob, oa=oa, q=q or 0.0) for e, l, bb, ba, fv, rf, ob, oa, q in db.execute(
    "select eid,label,best_bid,best_ask,fair_value,reference,our_bid,our_ask,position from snapshots where mode='live' and ts=?", (ts,))}
G = collections.defaultdict(list)
for e, r in R.items(): G[race(r["lab"])].append(e)
for e, r in R.items():                                  # effective_inventory (l.4308): x_i - mean(other legs)
    o = [R[x]["q"] for x in G[race(r["lab"])] if x != e]; r["eff"] = r["q"] - (sum(o) / len(o) if o else 0.0)
# book's own price for the ref guard: others' book (books table, our resting size not in it), mid normalised per race
bk = {}
for t, e, b, a in db.execute("select ts,eid,bids,asks from books order by ts"): bk[e] = (json.loads(b), json.loads(a))
def bmid(e):
    b, a = bk.get(e, ([], []))
    return (b[0][0] + a[0][0]) / 2 if b and a else None
for g, m in G.items():
    v = {e: bmid(e) for e in m}; s = sum(x for x in v.values() if x is not None)
    for e in m: R[e]["bfv"] = v[e] / s if v[e] is not None and s and len(m) > 1 and None not in v.values() else v[e]
flow = collections.Counter()
for f in csv.DictReader(open(f"{D}/fills.csv")):
    if END - 6 * 3600 <= T(f["filled_at"]) <= END: flow[f["exchange_id"]] += abs(float(f["qty"])) / 6
cls = lambda e: "headline" if "U.S. " in R[e]["lab"] else "dead" if flow[e] < 50 else "quiet" if flow[e] < 200 else "busy"
# last journal quote line per market (logged by plan_change when a change is planned: the wanted quote)
QL = re.compile(r"INFO    (.{26}) fv (.{5})(?: \(ref [\d.]+\))? inv\s+([+-]\d+) race\s+([+-]\d+).*\| bid (.+?)\s+ask (.+)$")
last = {}
for line in open(f"{D}/journal_2026-10-02_0814_to_now.log"):
    m = QL.search(line)
    if m: last[m.group(1).strip()] = (line[:19], m.group(3), m.group(4), m.group(5).strip(), m.group(6).strip())
st = json.load(open(f"{D}/status.json"))
P = print
P(f"snapshot {ts}; status.json reduce_only={st['reduce_only']} capital_ceiling={st['capital_ceiling_active']} "
  f"priced {st['markets_priced']}/{st['markets_tracked']} resting {st['orders_resting']}")
held = [e for e, r in R.items() if abs(r["q"]) >= 1]
redq = lambda r: r["oa"] if r["q"] > 0 else r["ob"]
C = collections.defaultdict(collections.Counter); cap = collections.defaultdict(collections.Counter); rows = []
for e in held:
    r = R[e]; c = cls(e); long = r["q"] > 0
    m = r["fv"] if r["fv"] is not None else 0.5; k = r["q"] * m if long else -r["q"] * (1 - m)
    if redq(r) is not None: why = "quoted"
    elif r["fv"] is None: why = "1 fv None"
    elif r["ref"] is not None and r["bfv"] is not None and ((r["ref"] - r["bfv"] > 0.05) if long else (r["bfv"] - r["ref"] > 0.05)):
        why = "7 ref guard"
    elif (r["eff"] < 1) if long else (r["eff"] > -1):
        legs = [R[x]["q"] for x in G[race(r["lab"])] if x != e]
        why = "5 reduce-only race-net: set leg" if all((x > 0) == long and abs(x) >= 1 for x in legs) else "5 reduce-only race-net: other"
    else: why = "unexplained"
    C[c][why] += 1; cap[c][why] += k
    rows.append((c, why, r, k, e))
dq = [x for x in rows if x[0] in ("dead", "quiet")]
P(f"\nheld {len(held)}; dead+quiet {len(dq)}; dead+quiet with no reducing quote {sum(1 for x in dq if x[1] != 'quoted')}")
P("| cause | dead | quiet | busy | headline | dead+quiet capital |\n|---|---|---|---|---|---|")
for w in sorted({x[1] for x in rows}):
    P(f"| {w} | " + " | ".join(str(C[c][w]) for c in ("dead", "quiet", "busy", "headline")) +
      f" | {cap['dead'][w] + cap['quiet'][w]:,.0f} |")
# journal agreement: does the last logged wanted quote also lack the reducing side, and does its race figure match eff?
agree = mism = nolog = 0
for c, w, r, k, e in dq:
    if w == "quoted": continue
    j = last.get(r["lab"])
    if not j: nolog += 1; continue
    side = j[4] if r["q"] > 0 else j[3]
    agree += side.startswith("-"); mism += not side.startswith("-")
P(f"journal last wanted quote also has no reducing side: {agree}; has one: {mism}; never logged since 08:14: {nolog}")
# where the reducing quote exists: distance to best other quote and to fv (band clamp ask >= r + min_edge, l.2250/2266)
qd = [(x[2], x[2]["oa"] - x[2]["ba"] if x[2]["q"] > 0 else x[2]["bb"] - x[2]["ob"]) for x in dq if x[1] == "quoted"]
inband = sum(1 for r, d in qd if d > 1e-9 and r["fv"] is not None and 0.004 <= abs(redq(r) - r["fv"]) <= 0.031)
P(f"quoted dead+quiet: {len(qd)}; behind the best top (incl. ours) {sum(1 for r, d in qd if d > 1e-9)}; of those within 0.5-3c of fv "
  f"(min_edge/skew band floor, not pennying) {inband}")
P("\nsample of 20 (dead/quiet, no reducing quote, largest capital first)")
P("| market | class | pos | race-net eff | other legs | fv | ref | our bid / ask (wanted) | journal last: time inv race bid / ask | cause |\n|---|---|---|---|---|---|---|---|---|---|")
for c, w, r, k, e in sorted([x for x in dq if x[1] != "quoted"], key=lambda x: -x[3])[:20]:
    legs = ", ".join(f"{R[x]['lab'].split()[0]} {R[x]['q']:+.0f}" for x in G[race(r["lab"])] if x != e)
    j = last.get(r["lab"]); js = f"{j[0][11:16]} {j[1]} {j[2]} {j[3]} / {j[4]}" if j else "-"
    P(f"| {r['lab']} | {c} | {r['q']:+.0f} | {r['eff']:+.0f} | {legs} | {r['fv']:.3f} | {r['ref'] if r['ref'] is None else round(r['ref'], 3)} | "
      f"{r['ob']} / {r['oa']} | {js} | {w} |")
rg = [x for x in dq if x[1] == "7 ref guard"]           # cross-check the guard with book = (fv - 0.7 ref) / 0.3 (ref_weight 0.7)
alt = sum(1 for c, w, r, k, e in rg if ((r["ref"] - (r["fv"] - 0.7 * r["ref"]) / 0.3) if r["q"] > 0
                                         else ((r["fv"] - 0.7 * r["ref"]) / 0.3 - r["ref"])) > 0.045)
P(f"ref-guard markets (dead+quiet) {len(rg)}: gap > 4.5c also by the blend inversion in {alt}; "
  f"of them with |race-net| >= |pos| (directional pair, not a set) {sum(1 for x in rg if abs(x[2]['eff']) >= abs(x[2]['q']))}")
sl = [x for x in dq if x[1].startswith("5")]
P(f"race-net clipped dead+quiet: {len(sl)}, eff exactly 0 (both legs equal) {sum(1 for x in sl if abs(x[2]['eff']) < 1)}; "
  f"wanted quote on the ADDING side instead (reduce-only follows race-net) {sum(1 for x in sl if (x[2]['ob'] if x[2]['q'] > 0 else x[2]['oa']) is not None)}")
# counterfactual: with the reducing size judged by THIS market's position (inv, as from flatten_per_market_hours)
P(f"\nif reduce-only clipped by |inv| instead of race-net eff: dead+quiet markets with a reducing side "
  f"{sum(1 for x in dq if x[1] != '7 ref guard' and x[1] != '1 fv None')}/{len(dq)}")
