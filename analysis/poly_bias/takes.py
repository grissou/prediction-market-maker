"""Stale-quote takes and sell-side arbitrage sets, marked at Polymarket vs the tournament mid; reduce-only share.
Usage: python analysis/poly_bias/takes.py DATA_DIR   (DATA_DIR holds fills.csv, order_notes.json, market_data.sql,
status.json, journal.log from an ops snapshot branch; stage it with git show, never commit it).
Fill convention: for our_side=ask, fills.csv fill_price is the NO price, so the YES sale price is 1 - fill_price."""
import sys, csv, json, sqlite3, re, bisect, collections, datetime as dt
D = sys.argv[1]
T = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d %H:%M")
db = sqlite3.connect(":memory:"); db.executescript(open(f"{D}/market_data.sql").read())
snap = collections.defaultdict(list)
for ts, eid, bb, ba, ref in db.execute("select ts,eid,best_bid,best_ask,reference from snapshots where mode='live' order by ts"):
    snap[eid].append((T(ts), None if bb is None or ba is None else (bb + ba) / 2, ref))
times = {e: [o[0] for o in s] for e, s in snap.items()}
END = max(t[-1] for t in times.values())
def at(eid, t, strict=False):          # last snapshot at (or strictly before) t
    i = (bisect.bisect_left if strict else bisect.bisect_right)(times[eid], t) - 1
    while i > 0 and (snap[eid][i][1] is None or snap[eid][i][2] is None): i -= 1   # skip one-sided books
    return snap[eid][i] if i >= 0 else None
notes = json.load(open(f"{D}/order_notes.json"))
labels = dict(db.execute("select eid,label from snapshots group by eid").fetchall())
rows = []
for f in csv.DictReader(open(f"{D}/fills.csv")):
    n = notes.get(f["order_id"])
    if not n or not (n.get("take") or n.get("arb")): continue
    side = f["our_side"]; q = float(f["qty"]); fp = float(f["fill_price"])
    p = fp if side == "bid" else 1 - fp; sg = 1 if side == "bid" else -1
    rows.append(dict(kind="arb" if n.get("arb") else "take", t=T(f["filled_at"]), eid=f["exchange_id"], sg=sg, q=q, p=p))
H = [("0", 0), ("15m", 900), ("1h", 3600), ("4h", 14400), ("end", None)]
def mark(r):
    o0 = at(r["eid"], r["t"], strict=True); r["ref0"], r["mid0"] = o0[2], o0[1]
    for h, dtm in H:
        o = o0 if dtm == 0 else at(r["eid"], END if dtm is None else min(END, r["t"] + dtm))
        r["mid" + h], r["ref" + h] = o[1], o[2]
        r["trunc" + h] = dtm is not None and r["t"] + dtm > END
for r in rows: mark(r)
tk = [r for r in rows if r["kind"] == "take"]
print(f"snapshot end {iso(END)} UTC; take fills {len(tk)}, arb legs {len(rows)-len(tk)}")
print("time     eid  label                       side    qty  price   poly0  mid0  | mid15m mid1h mid4h midEnd | refEnd")
for r in tk:
    print(f"{iso(r['t'])} {r['eid']:>5} {labels[r['eid']][:26]:26} {'BUY ' if r['sg']>0 else 'SELL'} {r['q']:6.0f} {r['p']:.3f}  {r['ref0']:.3f} {r['mid0']:.3f} | "
          + " ".join(f"{r['mid'+h]:.3f}{'*' if r['trunc'+h] else ' '}" for h, _ in H[1:]) + f" | {r['refend']:.3f}")
Q = sum(r["q"] for r in tk); N = sum(r["q"] * r["p"] for r in tk)
risk = sum(r["q"] * (r["p"] if r["sg"] > 0 else 1 - r["p"]) for r in tk)
print(f"\ntotal shares {Q:.0f}; notional (q*price) {N:.0f}; capital at risk (q*cost per side) {risk:.0f}")
gap0 = sum(r["q"] * r["sg"] * (r["ref0"] - r["mid0"]) for r in tk)
print("horizon | P&L @Polymarket  per sh | P&L @tourn. mid  per sh | pass-through (mid move / initial gap, q-wtd) | n truncated")
for h, _ in H:
    pp = sum(r["q"] * r["sg"] * (r["ref" + h] - r["p"]) for r in tk)
    pm = sum(r["q"] * r["sg"] * (r["mid" + h] - r["p"]) for r in tk)
    pt = sum(r["q"] * r["sg"] * (r["mid" + h] - r["mid0"]) for r in tk) / gap0
    print(f"{h:>7} | {pp:+9.1f} {100*pp/Q:+6.2f}c | {pm:+9.1f} {100*pm/Q:+6.2f}c | {100*pt:+6.1f}% | {sum(r['trunc'+h] for r in tk)}")
by = collections.defaultdict(lambda: [0, 0, 0])
for r in tk:
    b = by[labels[r["eid"]]]; b[0] += r["q"] * r["sg"]; b[1] += r["q"] * r["sg"] * (r["midend"] - r["p"]); b[2] += r["q"] * r["sg"] * (r["refend"] - r["p"])
print("per market: net YES, P&L@mid end, P&L@poly end:", {k: (round(v[0]), round(v[1], 1), round(v[2], 1)) for k, v in by.items()})
print("\n## arbitrage sell-side sets")
arb = collections.defaultdict(list)
for r in rows:
    if r["kind"] == "arb": arb[next((k for k in arb if abs(k - r["t"]) < 30), r["t"])].append(r)
tot = 0
for legs in arb.values():
    q = min(l["q"] for l in legs); s = sum(l["p"] for l in legs); lock = q * (s - 1); tot += lock
    print(f"{iso(legs[0]['t'])} " + ", ".join(f"{labels[l['eid']]} sell {l['q']:.0f}@{l['p']:.3f}" for l in legs)
          + f" -> sum {s:.3f}, locked {lock:+.2f}; mid-marked now {sum(l['q']*l['sg']*(l['midend']-l['p']) for l in legs):+.1f}")
print(f"arb locked total {tot:+.2f}")
print("\n## reduce-only")
st = json.load(open(f"{D}/status.json"))
print({k: v for k, v in st.items() if any(w in k.lower() for w in ("worst", "risk", "reduce", "account_value", "party"))})
print("snapshot modes:", db.execute("select mode,count(*) from snapshots group by mode").fetchall())
rx = re.compile(r"^(\S+) .*realtime \| account (\d+).*worst-case loss (\d+)( -> REDUCE-ONLY)?")
ev = [(T(m.group(1)), int(m.group(2)), int(m.group(3)), bool(m.group(4))) for m in map(rx.match, open(f"{D}/journal.log")) if m]
live0 = T(db.execute("select min(ts) from snapshots where mode='live'").fetchone()[0])
ev = [e for e in ev if e[0] >= live0 - 3600]
dur = ro = 0.0
for a, b in zip(ev, ev[1:] + [(END, 0, 0, False)]):
    d = max(0.0, min(b[0], END) - max(a[0], live0)); dur += d; ro += d * a[3]
print(f"journal realtime lines since live start: {len(ev)}, REDUCE-ONLY in {sum(e[3] for e in ev)} ({100*sum(e[3] for e in ev)/len(ev):.0f}%); "
      f"time-weighted {100*ro/dur:.0f}% of {dur/3600:.1f} h")
fl = [e for e in ev if e[3]]
if fl: print(f"first REDUCE-ONLY {iso(fl[0][0])}, last {iso(fl[-1][0])}; worst-case loss max {max(e[2] for e in ev)}, "
             f"max frac of account {max(e[2]/e[1] for e in ev if e[1]):.2f}")
acc = db.execute("select min(worst_case_loss),max(worst_case_loss),avg(worst_case_loss/account_value),max(worst_case_loss/account_value),count(*) from account where mode='live'").fetchone()
print("account table (live): wc min/max %.0f/%.0f, wc/account mean %.2f max %.2f, rows %d" % acc)
print("account rows with wc/account > 0.30:", db.execute("select avg(worst_case_loss/account_value>0.30) from account where mode='live'").fetchone()[0])
