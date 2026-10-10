"""Package 14.1: the LIVE state of 5 Oct 10:21 UTC approximated on the snap04 seed (helper, not a test suite; used by
tests/test_dryrun_mm_refill.py and analysis/p14/DIAG_14_1.md). On top of the tests/dryrun_harness.py harness (all 237
markets, other traders' books, Polymarket references, positions, marks, lots): the books converged 20% toward Polymarket
(tilt 0.137
on 4 Oct -> 0.109), the account at 102.0k, MM lots of 10.5k (7.9k stale, oldest 23.7 h) on middle-band low-edge holdings
whose touch is beyond the value floor (the stale MM that "rests" live), the exchange's NO+NO set rule on, and free cash
~1.9k after our resting orders' locks (a constant amount "held elsewhere" taken off the P&L cash read)."""
import os
import random
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("P9_SNAP", "/home/claude/snap04")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import dryrun_harness as P                                      # noqa: E402  (the harness: snapshot, fake, clock)
import mm_bot as M                                                # noqa: E402

TICK = 0.005


def converge(api, b, c):
    """Every book with a Polymarket p moved toward it: each level shifted by -(1 - c) x (mid - p), on the grid.
    The fake's books are copied first: the snapshot's own dict is shared between builds and must not be changed."""
    api.base_books = {e: {s: [dict(lv) for lv in (v.get(s) or [])] for s in ("bids", "asks")}
                      for e, v in api.base_books.items()}
    for e, bk in api.base_books.items():
        ex = b.ex.get(e)
        if ex is None or not bk.get("bids") or not bk.get("asks"):
            continue
        r = b.refs.prices.get(f"{ex.group}|{ex.party}")
        if r is None:
            continue
        mid = (bk["bids"][0]["price"] + bk["asks"][0]["price"]) / 2
        sh = round(-(1 - c) * (mid - r) / TICK) * TICK
        for side in ("bids", "asks"):
            for lv in bk[side]:
                lv["price"] = round(min(0.995, max(0.005, lv["price"] + sh)), 3)
    api.refill()


def buckets(b, api, now_m):
    """The book by edge-held at the touch (alloc_edge_held): {bucket: ($ at the touch, markets)}."""
    out = defaultdict(lambda: [0.0, 0])
    for e, q in api.inv.items():
        ex = b.ex.get(e)
        if ex is None or abs(q) < 1:
            continue
        ed = b.alloc_edge_held(ex, q, now_m)
        if ed is None:
            continue
        bk = ex.book
        usd = q * bk["bids"][0]["price"] if q > 0 else -q * (1 - bk["asks"][0]["price"])
        k = "<=2%" if ed <= 0.02 else "2-5%" if ed <= 0.05 else "5-10%" if ed <= 0.10 else ">10%"
        out[k][0] += usd
        out[k][1] += 1
    return {k: (round(v[0]), v[1]) for k, v in sorted(out.items())}


def build(S, overrides, K=1.0, C=0.8, mm_usd=10500.0, stale_usd=7900.0, oldest_h=23.7, cash_free=1903.0,
          account=102000.0, warm=6, seed=7, per_market=500.0):
    """(api, bot) in the 10:21 state, after `warm` + 3 cycles with `overrides` (the Package 14 file: as live since 10:08)."""
    random.seed(seed)
    api, b = P.build(S, overrides=overrides)
    for e in list(api.inv):
        api.inv[e] = float(int(api.inv[e] * K))
    converge(api, b, C)
    api.cash = account - api.market_value()
    api.set_collateral = True                 # the exchange's NO+NO set rule (live: 1 cash a broken share)
    api.cash_hold = 0.0                       # cash the live bot had locked that the fake's own quotes do not lock
    pnl0 = api.pnl
    api.pnl = lambda: (lambda r: {**r, "cashBalance": r["cashBalance"] - api.cash_hold})(pnl0())
    P.cycles(b, warm, stage="warm")
    now_m, now_w = getattr(M, "util", M).time.monotonic(), getattr(M, "util", M).time.time()
    # MM lots: middle-band, unpinned, non-headline holdings with edge-held < the hurdle; floor-failing ones first (live:
    # mm_resting dominates), biggest first, at most per_market $ each
    pins, hurdle = b.alloc_pins(), b.mm_hurdle()
    cands = []
    for e, q in sorted(api.inv.items()):
        ex = b.ex.get(e)
        p = None if ex is None else b.alloc_p(ex, now_m)
        if p is None or abs(q) < 1 or ex.label in pins or ex.group in b.cfg.headline_races:
            continue
        ed = b.alloc_edge_held(ex, q, now_m)
        if not (b.cfg.value_mid_low <= p <= b.cfg.value_mid_high) or ed is None or ed >= hurdle:
            continue
        bk = ex.book or {}
        px = (bk.get("bids" if q > 0 else "asks") or [{}])[0].get("price")
        fl = px is not None and b.mm_floor_ok(q > 0, px, p)
        cands.append((fl, -abs(q) * (p if q > 0 else 1 - p), e, q, p))   # live: the stale MM rests (floor fails)
    cands.sort()
    cands = [(c[0], c[2], c[3], c[4]) for c in cands]
    lots, tot, st = {}, 0.0, 0.0
    for fl, e, q, p in cands:
        if tot >= mm_usd - 1:
            break
        unit = p if q > 0 else 1 - p
        n = min(int(per_market / unit), max(1, int((mm_usd - tot) / unit)))
        if n > abs(q):                            # the MM inventory grew since the snapshot: the position with it
            api.inv[e] = float(n if q > 0 else -n)
        q = api.inv[e]
        sgn = 1 if q > 0 else -1
        # the stale part first (aged 6.5 h .. oldest_h), then the fresh part (2 h)
        n_st = min(n, int(max(0.0, stale_usd - st) / unit)) if st < stale_usd else 0
        ll = []
        if n_st >= 1:
            age = 6.5 + (oldest_h - 6.5) * (1 - st / max(stale_usd, 1))
            ll.append([sgn * n_st, round(p, 3), now_w - age * 3600])
            st += n_st * unit
        if n - n_st >= 1:
            ll.append([sgn * (n - n_st), round(p, 3), now_w - 2.0 * 3600])
        lots[e] = ll
        tot += n * unit
    b.mm_lots, b.mm_lots_seeded = lots, True
    api.cash = account - api.market_value()   # (the grown inventory was paid for: the account unchanged)
    b.mmf_seed = "dry run: the 10:21 state"
    # Free cash (after our resting orders' locks) ~cash_free: a constant amount held elsewhere (cash_hold). The
    # allocator is off while that settles, so the state is the same every time; then it is on with its clock reset
    # (the first run of the measurement is the FIRST allocator run on this state).
    on, b.cfg.alloc_enabled = b.cfg.alloc_enabled, False
    for _ in range(3):
        api.cash_hold += b.cash_left() - cash_free
        P.cycles(b, 1, stage="warm")
    b.cfg.alloc_enabled = on
    b.alloc_pairs, b.alloc_last_run_wall, b.alloc_sells_stopped = [], None, False
    b.mmf_refill = {"runs": 0, "sold_usd": 0.0, "mm_sold_usd": 0.0, "last": None}
    b.mmf_refill_last_m, b.p141_events, b.mmf_deferred = -1e18, type(b.p141_events)(), 0
    b.alloc_totals = {k: 0.0 for k in b.alloc_totals}
    b.alloc_flows.clear()
    return api, b
