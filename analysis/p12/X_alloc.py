"""Q4: reproduce Bot.alloc_plan offline on the 15:56 state: status.json positions, the latest recorder books (our own
price levels stripped whole: the recorder's our_bid / our_ask), the race-scaled snapshot reference as alloc_p.
Not reproducible offline: bloc_delta_now (no sensitivities recorded) and the exact lone-NO cover (|short| used)."""
import sys, json, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from X_common import *

TICK, MIN_USD = 0.005, 20.0
CFG = dict(min_edge_buy=0.05, min_impr=0.03, max_edge_sell=0.02, max_contract=10000.0, reserve=15000.0, turnover=15000.0)
HEADLINE = {"U.S. House", "U.S. Senate"}
PINS = {"Rep U.S. Senate", "Dem U.S. Senate"}
st = json.load(open(f"{SNAP}/status.json"))
pos = st["positions"]
S = Series(load_snapshots("2026-10-04T15:40"))
t_end, rows = S.at(ts_of(st["updated"]))
by_label = {r["label"]: r for r in rows.values()}
books = latest_books()

def book_of(r):
    b = books.get(r["eid"])
    if b is None: return None, None
    ts, bids, asks = b
    bids = [x for x in bids if r["ob"] is None or abs(x[0] - r["ob"]) > 1e-9]
    asks = [x for x in asks if r["oa"] is None or abs(x[0] - r["oa"]) > 1e-9]
    return ts, {"bids": bids, "asks": asks}

def plan(cash, reserve, reduce_only=False, verbose=False):
    held, levels, room, why = [], [], {}, {}
    inv = {by_label[l]["eid"]: q for l, q in pos.items() if l in by_label}
    for l, r in by_label.items():
        e, p = r["eid"], r["p"]; q = float(inv.get(e, 0.0))
        bts, book = book_of(r)
        if r["race"] in HEADLINE: why[l] = "headline"; continue
        if p is None: why[l] = "no liquid p"; continue
        if book is None: why[l] = "no book"; continue
        if abs(q) >= 1 and l not in PINS:
            if q > 0 and book["bids"]:
                px, d = book["bids"][0]; qty, edge, unit = min(q, d), (p - px) / px, px
            elif q < 0 and book["asks"]:
                px, d = book["asks"][0]; qty, edge, unit = min(-q, d), (px - p) / max(1 - px, TICK), 1 - px
            else:
                qty = 0; edge = None
            if edge is not None:
                held.append(dict(kind="long" if q > 0 else "short", label=l, q=q, px=px, p=p, edge=edge, unit=unit,
                                 avail=int(qty) * unit, depth=d,
                                 ok=int(qty) >= 1 and edge <= CFG["max_edge_sell"] + 1e-9 and int(qty) * unit >= MIN_USD))
        hu = q * p if q > 0 else -q * (1 - p)
        room[l] = max(0.0, CFG["max_contract"] - hu)
        if q >= 0:
            for px, d in book["asks"][:3]:
                levels.append(dict(label=l, short=False, px=px, p=p, edge=(p - px) / px, unit=px, avail=d * px, q=q))
        if q <= 0:
            for px, d in book["bids"][:3]:
                levels.append(dict(label=l, short=True, px=px, p=p, edge=(px - p) / max(1 - px, TICK), unit=1 - px,
                                   avail=d * (1 - px), q=q))
    sells = sorted([h for h in held if h["ok"]], key=lambda h: h["edge"])
    spare = cash - reserve
    if spare >= MIN_USD:
        sells.insert(0, dict(kind="cash", label="spare cash", edge=0.0, unit=1.0, avail=spare, px=1.0))
        sells.sort(key=lambda h: h["edge"])
    buys = sorted([o for o in levels if o["edge"] >= CFG["min_edge_buy"] - 1e-9], key=lambda o: -o["edge"])
    if reduce_only: buys = []
    left, pairs, refill, gain = CFG["turnover"], [], [], 0.0
    deficit = reserve - cash
    for h in sells:
        if deficit < MIN_USD or left < MIN_USD: break
        if h["kind"] == "cash": continue
        x = min(h["avail"], deficit, left); n = int(x / h["unit"] + 1e-9)
        if n < 1: continue
        refill.append((h["label"], h["kind"], n, h["px"], round(h["edge"], 4), round(n * h["unit"])))
        h["avail"] -= n * h["unit"]; deficit -= n * h["unit"]; left -= n * h["unit"]
    sq = [h for h in sells if h["avail"] >= MIN_USD]; si = bi = 0
    while si < len(sq) and bi < len(buys) and left >= MIN_USD:
        h, o = sq[si], buys[bi]
        if o["edge"] - h["edge"] < CFG["min_impr"] - 1e-9: break
        x = min(h["avail"], o["avail"], room.get(o["label"], 0.0), left)
        if x < MIN_USD:
            if room.get(o["label"], 0) < MIN_USD or o["avail"] < MIN_USD: bi += 1
            else: si += 1
            continue
        nb = int(x / o["unit"] + 1e-9); x = nb * o["unit"]
        if nb < 1: bi += 1; continue
        pairs.append((h["label"], round(h["edge"], 4), "short" if o["short"] else "buy", o["label"], nb, o["px"],
                      round(o["p"], 3), round(o["edge"], 4), round(x)))
        gain += x * (o["edge"] - h["edge"]); h["avail"] -= x; o["avail"] -= x; room[o["label"]] -= x; left -= x
        if h["avail"] < MIN_USD: si += 1
        if o["avail"] < MIN_USD or room[o["label"]] < MIN_USD: bi += 1
    return dict(held=held, levels=levels, buys=buys, sells=sells, refill=refill, pairs=pairs, gain=gain, why=why)

if __name__ == "__main__":
    bt = [b[0] for b in books.values()]
    print(f"snapshot {iso(t_end)}; books: {len(books)} markets, newest {iso(max(bt))}, "
          f"{sum(1 for x in bt if t_end - x < 120)} within 2 min, {sum(1 for x in bt if t_end - x < 600)} within 10 min")
    r = plan(4012.41, CFG["reserve"])
    print("\nTOP 20 BUY CANDIDATES (all levels, top 3 per side, by edge per $):")
    allv = sorted(r["levels"], key=lambda o: -o["edge"])
    for o in allv[:20]:
        print(f"  {'SHORT@bid' if o['short'] else 'BUY@ask '} {o['label']:32s} px {o['px']:.3f} p {o['p']:.3f} "
              f"edge {o['edge']:+.3f} depth ${o['avail']:.0f} held {o['q']:+.0f}")
    print(f"  levels with edge >= 0.05: {len(r['buys'])}")
    print("\nTOP 20 SELL CANDIDATES (held, lowest edge-held first; ok = edge <= 0.02, >= $20 at the touch):")
    for h in sorted(r["held"], key=lambda h: h["edge"])[:20]:
        print(f"  {h['kind']:5s} {h['label']:32s} q {h['q']:+7.0f} touch {h['px']:.3f} p {h['p']:.3f} "
              f"edge-held {h['edge']:+.3f} avail ${h['avail']:.0f} ok={h['ok']}")
    print(f"  held positions ranked {len(r['held'])}, eligible to sell {sum(h['ok'] for h in r['held'])}")
    print("  excluded:", {k: sum(1 for v in r['why'].values() if v == k) for k in set(r['why'].values())})
    for name, cash, res, ro in (("A live 15:17 (cash 19837, reduce-only)", 19837, 15000, True),
                                ("B next run ~16:17 at 15:56 state (cash_left 4012, reserve 15k)", 4012.41, 15000, False),
                                ("C cash 19837 not reduce-only, reserve 15k (5k spare)", 19837, 15000, False),
                                ("D cash 19837, reserve 0", 19837, 0, False),
                                ("E cash 4012, reserve 0", 4012.41, 0, False)):
        x = plan(cash, res, ro)
        print(f"\n== {name}: refill sales {len(x['refill'])} (${sum(a[-1] for a in x['refill'])}), "
              f"pairs {len(x['pairs'])} (${sum(a[-1] for a in x['pairs'])}), est gain {x['gain']:.0f}")
        for a in x["refill"][:15]: print("   REFILL sell", a)
        for a in x["pairs"][:20]: print("   PAIR", a)
