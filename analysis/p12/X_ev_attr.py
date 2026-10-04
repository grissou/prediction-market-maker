"""Q1b: attribute the EV-at-outcome change 3 Oct 22:47 -> 4 Oct 15:56 (cash + positions at the race-scaled
Polymarket p) to fill edges vs p at fill, by class/period, plus reference moves on held positions (sum q x dp over
recorder snapshots). Unpriced markets (no ref) are valued at the book fair value in both EV and moves."""
import sys, sqlite3
from collections import Counter, defaultdict
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from X_common import *

T0, T1 = ts_of("2026-10-03T22:47:00Z"), ts_of("2026-10-04T15:56:30Z")
TAKE_TILT = ts_of("2026-10-04T07:05:21Z"); UNW = ts_of("2026-10-04T07:19:10Z"); VAL = ts_of("2026-10-04T11:10:58Z")
S = Series(load_snapshots("2026-10-03T22:30"))
notes = load_notes(); fills = load_fills()
db = sqlite3.connect(f"{SNAP}/md.sqlite")

def val(r):
    return r["p"] if r["p"] is not None else r["fv"]

def ev_at(t):
    ts, rows = S.at(t)
    am = db.execute("select ts,account_value,market_value from account_marks order by abs(ts-?) limit 1", (ts,)).fetchone()
    cash = am[1] - am[2]
    held = 0.0; unp = 0
    for r in rows.values():
        q = r["pos"] or 0.0
        if abs(q) < 1: continue
        v = val(r)
        if v is None: unp += 1; continue
        held += q * v if q > 0 else -q * (1 - v)   # (= q*v + const; the short's 1-v form as alloc_held_usd)
    return ts, cash, held, cash + held, unp

def period(ts, c):
    if c == "arb": return "b set unwinds / arb" if ts >= UNW else "b0 arb before 07:19"
    if c == "take":
        if ts < TAKE_TILT: return "c raw-PM takes 22:44-07:05"
        if ts < VAL: return "c2 tilted-ref takes 07:05-11:10"
        return "d value-mode takes after 11:10"
    if ts < UNW: return "a1 P8 maker fills 22:47-07:19"
    if ts < VAL: return "a2 P8 maker fills 07:19-11:10 (tilt exits)"
    return "d2 value-mode maker fills after 11:10"

def cls_of(m):
    if m.get("basket"): return "basket"
    if m.get("alloc") or m.get("set_ladder"): return "alloc"
    if m.get("arb"): return "arb"
    if m.get("take"): return "take"
    return "maker"

if __name__ == "__main__":
    e0, e1 = ev_at(T0), ev_at(T1)
    for t in ("2026-10-03T22:47:00Z", "2026-10-04T07:05:00Z", "2026-10-04T07:19:00Z", "2026-10-04T11:08:00Z",
              "2026-10-04T11:24:00Z", "2026-10-04T15:39:00Z", "2026-10-04T15:56:00Z"):
        ts, cash, held, ev, unp = ev_at(ts_of(t))
        print(f"EV at {iso(ts)}: cash {cash:.0f} + held@p {held:.0f} = {ev:.0f} (unpriced {unp})")
    edge = defaultdict(float); n = Counter(); sells_below = defaultdict(float); usd = defaultdict(float)
    for r in fills:
        ts = ts_of(r["filled_at"])
        if not T0 <= ts <= T1: continue
        side = r["our_side"]; qty = abs(float(r["qty"] or 0))
        price = float(r["quote_price"] or r["fill_price"] or 0)
        if side not in ("bid", "ask") or not 0 < price < 1: n["unsided"] += 1; continue
        m = notes.get(r["order_id"]); c = cls_of(m or {})
        _, rows = S.at(ts); row = rows.get(r["exchange_id"])
        p = val(row) if row else None
        if p is None: n["unpriced"] += 1; continue
        e = qty * ((p - price) if side == "bid" else (price - p))
        k = period(ts, c); edge[k] += e; n[k] += 1; usd[k] += qty * price
        edge[k + " [" + side + "s]"] += e; n[k + " [" + side + "s]"] += 1; usd[k + " [" + side + "s]"] += qty * price
        if side == "ask" and price < p: sells_below[k] += e
    # reference moves on held positions: sum over snapshot steps of q(t_i) x (v(t_{i+1}) - v(t_i))
    ts_list = [t for t in S.ts if T0 - 120 <= t <= T1 + 60]
    moves = 0.0; mv_by = defaultdict(float)
    for a, b in zip(ts_list, ts_list[1:]):
        ra, rb = S.s[a], S.s[b]
        for e, x in ra.items():
            y = rb.get(e); q = x["pos"] or 0.0
            if y is None or abs(q) < 1: continue
            va, vb = val(x), val(y)
            if va is None or vb is None: continue
            moves += q * (vb - va); mv_by[x["label"]] += q * (vb - va)
    tot = sum(v for k, v in edge.items() if '[' not in k)
    print(f"\nEV change {iso(e0[0])} -> {iso(e1[0])}: {e1[3]-e0[3]:+.0f}")
    for k in sorted(edge): print(f"  {k:52s} n={n[k]:5d}  traded ${usd[k]:9.0f}  edge vs p {edge[k]:+9.0f}  (of which sells below p {sells_below[k]:+.0f})")
    print(f"  {'e reference moves on held positions':52s} {moves:+.0f}")
    print(f"  fills total {tot:+.0f}; fills + moves {tot+moves:+.0f}; residual (snapshot sampling, unpriced, fees) {e1[3]-e0[3]-tot-moves:+.0f}")
    print("  skipped:", dict((k, v) for k, v in n.items() if k in ("unsided", "unpriced")))
    print("  top moves:", sorted(((round(v), k) for k, v in mv_by.items()), key=lambda x: abs(x[0]), reverse=True)[:8])
