"""
Tests for mmbot2/mm.py: the band, a tick inside the best other trader, the minimum edge, never crossing, the ref
guard, the size lean, the market-making lots (FIFO, trimmed to the position), stale by age and by size, the
recycler's prices and sizes, the hand-over to value, and the saved state.

Run:  python3 tests2/test_mm.py      (exit code 0 = all passed)
"""
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mmbot2.config import Settings                                          # noqa: E402
from mmbot2.state import Account, Book, Fill, Market, View                  # noqa: E402
from mmbot2 import mm                                                       # noqa: E402

RESULTS = []
NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
S = Settings(mm_quote_frac=0.005)   # the sizes below were worked out with 500-share quotes


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   [{detail}]"))


MARKETS = {e: Market(e, label, "Ohio Senate", "Republican", "OH", None)
           for e, label in (("a", "Rep Ohio Senate"), ("pin", "Rep U.S. Senate"))}


def book(bid, ask, depth=300):
    return Book(bids=[(bid, depth)] if bid else [], asks=[(ask, depth)] if ask else [])


def view(p, bk, qty=0.0, eid="a"):
    return View(now=NOW, mono=10.0, markets=MARKETS, books={eid: bk}, positions={eid: qty} if qty else {},
                resting={}, account=Account(value=100000.0, cash=50000.0, read_at=0.0), p={eid: p})


def sides(orders):
    bid = next((o for o in orders if o.is_bid), None)
    ask = next((o for o in orders if not o.is_bid), None)
    return bid, ask


def quote(p, bk, qty=0.0, inv=None, eid="a", s=S):
    return sides(mm.quotes(view(p, bk, qty, eid), None, s, inv or mm.Inventory()))


def fill(is_bid, price, size, hours_ago, tag="mm", eid="a"):
    return Fill(eid, is_bid, price, size, None, NOW - timedelta(hours=hours_ago), tag)


def near(a, b, tol=1e-9):
    return a is not None and abs(a - b) <= tol


# ---- one tick inside the best other trader, sized by quarter Kelly ----
b, a = quote(0.50, book(0.47, 0.53))
check("bid a tick above the best other bid", b is not None and near(b.price, 0.475), b)
check("ask a tick below the best other ask", a is not None and near(a.price, 0.525), a)
check("flat: both sides a full quote (500 at 100k)", b.size == 500 and a.size == 500, (b.size, a.size))
check("tags are mm", b.tag == "mm" and a.tag == "mm")

# ---- the band ----
check("below band_low: no quotes", quote(0.10, book(0.07, 0.13)) == (None, None))
check("above band_high: no quotes", quote(0.90, book(0.87, 0.93)) == (None, None))
b, a = quote(0.15, book(0.12, 0.18))
check("at band_low: quoted", b is not None and a is not None)
check("unpriced market: nothing", mm.quotes(View(NOW, 1.0, MARKETS, {"a": book(0.47, 0.53)}, {}, {}, Account(1e5, 5e4)),
                                            None, S, mm.Inventory()) == [])
check("no fresh book: nothing", mm.quotes(View(NOW, 1.0, MARKETS, {}, {}, {}, Account(1e5, 5e4), p={"a": 0.5}),
                                          None, S, mm.Inventory()) == [])

# ---- minimum edge and the widest spread ----
b, a = quote(0.50, book(0.495, 0.505))
check("a penny war stops at p - mm_min_edge", near(b.price, 0.49), b)
check("...and at p + mm_min_edge", near(a.price, 0.51), a)
b, a = quote(0.50, book(0.30, 0.70))
check("a wide book: rest at most 4c from p", near(b.price, 0.46) and near(a.price, 0.54), (b, a))
b, a = quote(0.50, book(None, None))
check("an empty book: both sides at the widest", near(b.price, 0.46) and near(a.price, 0.54), (b, a))

# ---- never crossing ----
b, a = quote(0.50, book(0.47, 0.475))          # one tick wide: a penny bid would hit the ask
check("one-tick book: the bid joins the best bid", near(b.price, 0.47), b)
check("one-tick book: the ask stays above p + edge", near(a.price, 0.51), a)
for bk in (book(0.47, 0.475), book(0.495, 0.505), book(0.38, 0.40)):
    for o in mm.quotes(view(0.50, bk), None, S, mm.Inventory()):
        ok = o.price < bk.best_ask if o.is_bid else o.price > bk.best_bid
        check(f"never crosses {bk.best_bid}/{bk.best_ask} ({'bid' if o.is_bid else 'ask'} {o.price})", ok)

# ---- ref guard ----
b, a = quote(0.50, book(0.38, 0.40))           # p 11c above the book: do not sell to it
check("p above the book by > gap: no ask", a is None, a)
check("...the bid still rests, below the best ask", b is not None and b.price < 0.40, b)
b, a = quote(0.50, book(0.60, 0.62))           # p 11c below the book: do not buy
check("p below the book by > gap: no bid", b is None, b)
check("...the ask still rests", a is not None and a.price >= 0.51, a)
b, a = quote(0.50, book(0.38, 0.40, depth=50))
check("thin book has no price: no guard", a is not None, a)

# ---- size lean ----
b, a = quote(0.50, book(0.47, 0.53), qty=800)
check("long 800: the bid shrinks to the 1,000 inventory cap", b.size == 200, b.size)
check("long 800: the reducing ask keeps a full quote", a.size == 500, a.size)
b, a = quote(0.50, book(0.47, 0.53), qty=-1200)
check("short beyond the cap: no ask", a is None, a)
check("short: the bid buys back a full quote", b.size == 500, b)
check("Kelly limit below the cap shrinks the adding side",
      mm.quote_size(0.50, 0.49, True, 0, 100000.0, S) == 500 and mm.quote_size(0.50, 0.49, True, 600, 100000.0, S)
      == int(mm.pricing.kelly_shares(0.50, 0.49, True, 100000.0, S)) - 600, mm.pricing.kelly_shares(0.5, 0.49, True,
                                                                                                    1e5, S))
check("mm_skew_max 0: no price lean", near(quote(0.50, book(0.47, 0.53), qty=800)[0].price, 0.475))

# ---- inventory lots from fills ----
inv = mm.Inventory()
inv.note_fills([fill(True, 0.48, 300, 3), fill(True, 0.49, 200, 2), fill(True, 0.30, 999, 1, tag="alloc")])
check("mm fills open lots; others ignored", [x[0] for x in inv.lots["a"]] == [300, 200], inv.lots)
inv.note_fills([fill(False, 0.52, 400, 1)])
check("a sale closes the oldest lot first (FIFO)", [(x[0], x[1]) for x in inv.lots["a"]] == [(100, 0.49)], inv.lots)
inv.note_fills([fill(False, 0.50, 60, 0.5, tag="recycle")])
check("a recycle fill closes lots", inv.lots["a"][0][0] == 40, inv.lots)
inv.note_fills([fill(False, 0.50, 100, 0.5, tag="recycle")])
check("a recycle fill never opens the other way", "a" not in inv.lots, inv.lots)
inv.note_fills([fill(False, 0.52, 300, 1)])
check("an mm sale from flat opens a short lot", inv.lots["a"][0][0] == -300, inv.lots)
inv = mm.Inventory()
inv.note_fills([fill(True, 0.48, 300, 3), fill(True, 0.49, 200, 2)])
inv.stale(view(0.5, book(0.485, 0.515), qty=350), S)
check("lots trimmed to the position, oldest first", [x[0] for x in inv.lots["a"]] == [150, 200], inv.lots)
inv.stale(view(0.5, book(0.485, 0.515), qty=-10), S)
check("a flipped position drops the lots", "a" not in inv.lots, inv.lots)
inv = mm.Inventory()
inv.note_fills([fill(False, 0.52, 100, 1)])
inv.stale(view(0.5, book(0.485, 0.515), qty=400), S)
check("an mm sale that only shrank a non-MM long keeps no lot", "a" not in inv.lots, inv.lots)
inv = mm.Inventory()
inv.note_fills([fill(True, 0.4 + 0.001 * i, 10, 5 - 0.1 * i) for i in range(25)])
check("at most 20 lots per market, all shares kept", len(inv.lots["a"]) == 20 and
      near(sum(x[0] for x in inv.lots["a"]), 250), len(inv.lots["a"]))

# ---- stale by age and by size ----
inv = mm.Inventory()
inv.note_fills([fill(True, 0.48, 300, 7), fill(True, 0.49, 200, 1)])
check("lots older than 6 h are stale", inv.stale(view(0.5, book(0.485, 0.515), qty=500), S) == {"a": 300})
inv = mm.Inventory()
inv.note_fills([fill(True, 0.48, 8000, 1)])
check("above 3,000 at p: the excess is stale", inv.stale(view(0.5, book(0.485, 0.515), qty=8000), S) == {"a": 2000})
inv = mm.Inventory()
inv.note_fills([fill(False, 0.52, 300, 1)])
check("young and small: nothing stale", inv.stale(view(0.5, book(0.485, 0.515), qty=-300), S) == {})
st = inv.status()
check("status reports the inventory", st["inventory_markets"] == 1 and near(st["inventory_usd"], 150.0), st)

# ---- the recycler ----
def stale_inv(qty, eid="a"):
    inv = mm.Inventory()
    inv.note_fills([fill(qty > 0, 0.50, abs(qty), 7, eid=eid)])
    return inv


inv = stale_inv(600)
b, a = quote(0.50, book(0.485, 0.515), qty=600, inv=inv)
check("long stale: the ask moves to p - concession", a is not None and near(a.price, 0.49) and a.tag == "recycle", a)
check("...sized to the stale shares, at most the position", a.size == 600, a.size)
check("...our adding bid a tick behind it", b is not None and near(b.price, 0.485) and b.tag == "mm", b)
check("status lists the recycle", inv.status()["recycling"].get("Rep Ohio Senate", {}).get("price") == 0.49,
      inv.status()["recycling"])
b, a = quote(0.50, book(0.495, 0.525), qty=600, inv=stale_inv(600))
check("never at or through the best other bid", near(a.price, 0.50), a)
b, a = quote(0.50, book(0.485, 0.515), qty=-600, inv=stale_inv(-600))
check("short stale: the bid moves to p + concession", near(b.price, 0.51) and b.tag == "recycle" and b.size == 600, b)
check("...our adding ask a tick behind it", a is not None and near(a.price, 0.515), a)
b, a = quote(0.50, book(0.40, 0.515), qty=600, inv=stale_inv(600))
check("edge held >= the hurdle: no recycle, no ask below value", a is None or a.tag == "mm", a)
inv = stale_inv(600)
quote(0.50, book(0.40, 0.515), qty=600, inv=inv)
check("...the lots are handed to value", "a" not in inv.lots and inv.handed["count"] == 1, inv.handed)
b, a = quote(0.50, book(0.485, 0.515), qty=600, inv=stale_inv(600, "pin"), eid="pin")
check("a pinned label is never recycled", a is not None and a.tag == "mm", a)
b, a = quote(0.102, book(0.10, 0.12), qty=600, inv=stale_inv(600))
check("out of the band: only the recycle order", b is None and a is not None and a.tag == "recycle"
      and near(a.price, 0.105), (b, a))
inv = stale_inv(600)      # (at the live hurdle a ref-guarded long is always handed over first: raise it)
b, a = quote(0.50, book(0.385, 0.40), qty=600, inv=inv, s=replace(S, value_quote_hurdle=1.0))
check("the ref guard blocks the recycle side too", a is None and "a" in inv.lots, (a, inv.lots))

# ---- saved state ----
inv = stale_inv(600)
inv.hand_over("zzz")
back = mm.Inventory(inv.to_dict())
check("to_dict round trip", back.to_dict() == inv.to_dict() and back.lots == inv.lots, back.to_dict())
check("an empty save starts empty", mm.Inventory(None).lots == {} and mm.Inventory({}).status()["lots"] == {})

passed = sum(RESULTS)
print(f"{passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
