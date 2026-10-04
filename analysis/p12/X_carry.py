"""Q1: mm_carry_24h (Bot.mm_carry, 39e4012) measured offline from snap04, p = race-scaled recorder reference nearest
the fill (and at 15:56). Windows: since stage 3 (4 Oct 11:24:46) and the last 24 h (to 15:56)."""
import sys, re
from collections import defaultdict, deque, Counter
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from X_common import *

END = ts_of("2026-10-04T15:56:26Z")
WINS = {"stage3 11:24:46-15:56": ts_of("2026-10-04T11:24:46Z"), "24h 10-03 15:56-15:56": END - 86400,
        "post-reduce-only 15:39-15:56": ts_of("2026-10-04T15:39:00Z")}
S = Series(load_snapshots("2026-10-03T15:00"))
notes = load_notes()
fills = load_fills()
selftest = set()
for l in journal_lines(r"(?i)self.?test"):
    selftest.update(re.findall(r"order (\d+)", l))

def cls_of(meta):
    if meta.get("basket"): return "basket"
    if meta.get("alloc") or meta.get("set_ladder"): return "alloc"
    if meta.get("arb"): return "arb"
    if meta.get("take"): return "take"
    return "maker"

def carry(t0, p_mode):
    counts = Counter(); book = defaultdict(deque); realised = adds = takes = 0.0
    missing = take_unp = 0; cash_buy = cash_sell_proc = cash_no = 0.0; mid_buy = mid_sell = 0
    pos_cache = {}
    first = None
    for r in sorted(fills, key=lambda r: r["filled_at"]):
        side = r["our_side"]
        if side not in ("bid", "ask"): continue
        qty = abs(float(r["qty"] or 0)); price = float(r["quote_price"] or r["fill_price"] or 0)
        if qty <= 0 or not 0 < price < 1: continue
        ts = ts_of(r["filled_at"])
        if ts < t0 or ts > END: continue
        if r["order_id"] in selftest: counts["selftest_skipped"] += 1; continue
        first = ts if first is None else first
        meta = notes.get(r["order_id"])
        if meta is None: missing += 1; meta = {}
        c = cls_of(meta); e = r["exchange_id"]; buy = side == "bid"
        if c not in ("maker", "take"): counts[c] += 1; continue
        p = S.p(e, ts) if p_mode == "fill" else S.p(e, END)
        edge = None if p is None else qty * ((p - price) if buy else (price - p))
        if c == "take":
            counts["take"] += 1
            if edge is None: take_unp += 1
            else: takes += edge
            continue
        # cash the maker fill consumed: a buy pays price; a sell beyond the long held buys NO at 1 - price
        _, rows = S.at(ts - 30)
        held = (rows.get(e) or {}).get("pos") or 0.0
        if buy:
            cash_buy += qty * price
        else:
            from_long = min(qty, max(0.0, held)); cash_sell_proc += from_long * price; cash_no += (qty - from_long) * (1 - price)
        if p is None: counts["maker_unpriced"] += 1
        elif not LO <= p <= HI: counts["maker_tail"] += 1; adds += edge
        else:
            counts["maker_mid"] += 1
            if buy: mid_buy += 1
            else: mid_sell += 1
            lots, rem = book[e], qty if buy else -qty
            while abs(rem) > 1e-9 and lots and (lots[0][0] > 0) != (rem > 0):
                lot = lots[0]; n = min(abs(rem), abs(lot[0]))
                realised += n * (price - lot[1]) * (1 if lot[0] > 0 else -1)
                lot[0] += n if lot[0] < 0 else -n; rem += n if rem < 0 else -n
                if abs(lot[0]) <= 1e-9: lots.popleft()
            if abs(rem) > 1e-9: lots.append([rem, price, p, e])
    left = [x for v in book.values() for x in v]
    hours = (END - t0) / 3600
    return dict(realised=round(realised, 2), per_day=round(realised * 24 / hours, 2), hours=round(hours, 2),
                unmatched_shares=round(sum(abs(x[0]) for x in left)),
                unmatched_ev=round(sum(x[0] * (x[2] - x[1]) for x in left), 2),
                value_adds_ev=round(adds, 2), takes_ev=round(takes, 2), fills=dict(counts),
                mid_buys=mid_buy, mid_sells=mid_sell, takes_unpriced=take_unp, meta_missing=missing,
                cash_maker_buys=round(cash_buy), cash_maker_no_buys=round(cash_no), proceeds_maker_long_sales=round(cash_sell_proc),
                matched_markets=sum(1 for v in book.values()))

def top_of_book(t0):
    n_snap = 0; mid_mk = mid_top = mid_quoted = 0; tail_top = 0
    for t in S.ts:
        if t < t0 or t > END: continue
        n_snap += 1
        for r in S.s[t].values():
            p = r["p"]
            if p is None: continue
            top = (r["ob"] is not None and r["bb"] is not None and abs(r["ob"] - r["bb"]) < 1e-9) or \
                  (r["oa"] is not None and r["ba"] is not None and abs(r["oa"] - r["ba"]) < 1e-9)
            quoted = r["ob"] is not None or r["oa"] is not None
            if LO <= p <= HI:
                mid_mk += 1; mid_top += top; mid_quoted += quoted
            else:
                tail_top += top
    return dict(snapshots=n_snap, mid_markets_avg=round(mid_mk / max(1, n_snap), 1),
                mid_quoted_avg=round(mid_quoted / max(1, n_snap), 1), mid_at_top_avg=round(mid_top / max(1, n_snap), 1),
                tail_at_top_avg=round(tail_top / max(1, n_snap), 1))

if __name__ == "__main__":
    print("self-test order ids found in journal:", len(selftest))
    for name, t0 in WINS.items():
        for pm in ("fill", "now"):
            print(name, "p@" + pm, carry(t0, pm))
        print(name, "top-of-book", top_of_book(t0))
    # hourly realised in the stage-3 window (when did the mid band fill?)
    t0 = WINS["stage3 11:24:46-15:56"]
    hrs = Counter(); hq = Counter()
    for r in fills:
        if r["our_side"] in ("bid", "ask") and t0 <= ts_of(r["filled_at"]) <= END:
            m = notes.get(r["order_id"]) or {}
            if cls_of(m) == "maker":
                h = r["filled_at"][11:13]; hrs[h] += 1; hq[h] += float(r["qty"])
    print("maker fills by hour (n, shares):", sorted((h, hrs[h], round(hq[h])) for h in hrs))
