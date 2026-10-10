"""
Tests for mmbot2/value.py: the value floor, the tail quotes' hurdle prices, and the allocator (swaps over two
steps, spare-cash buys, the refill bounded by demand, pins, no flip, the turnover cap, prefer-short).

Run:  python3 tests2/test_value.py      (exit code 0 = all passed)
"""
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mmbot2.config import Settings                                          # noqa: E402
from mmbot2.risk import RiskState                                           # noqa: E402
from mmbot2.state import Account, Book, Fill, Market, Order, View           # noqa: E402
from mmbot2 import value as V                                               # noqa: E402

RESULTS = []
NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   [{detail}]"))


def near(a, b, tol=1e-9):
    return a is not None and abs(a - b) <= tol


def mk(eid, race, party, state=None, label=None):
    return Market(eid, label or eid, race, party, state, None)


MARKETS = {m.eid: m for m in (
    mk("rOH", "Ohio Senate", "Republican", "OH"), mk("dOH", "Ohio Senate", "Democratic", "OH"),
    mk("rTX", "Texas Senate", "Republican", "TX"), mk("dTX", "Texas Senate", "Democratic", "TX"),
    mk("fav", "Iowa 1", "Republican", "IA"), mk("lng", "Maine 2", "Democratic", "ME"),
    mk("rUS", "U.S. Senate", "Republican", None, "Rep U.S. Senate"))}


def risk(**kw):
    base = dict(worst_case=0.0, correlated=0.0, bloc_delta=0.0, reduce_only=False, room_wc=1e5, room_corr=1e5,
                adds_paused=False)
    return RiskState(**{**base, **kw})


def view(p, books, positions=None, cash=10000.0, read_at=95.0, mono=100.0, now=NOW, value=100000.0):
    """books: eid -> (bids, asks); every book read at `mono` (fresh)."""
    return View(now=now, mono=mono, markets=MARKETS,
                books={e: Book(list(b), list(a), at=mono) for e, (b, a) in books.items()},
                positions=positions or {}, resting={},
                account=Account(value=value, cash=cash, start=100000.0, read_at=read_at), p=dict(p))


def fill(eid, is_bid, price, size, tag):
    return Fill(eid, is_bid, price, size, None, NOW, tag)


s = Settings()

# ---- the floor ----
check("floor_prices long", V.floor_prices(100, 0.5, 0.04) == (None, 0.46))
check("floor_prices short", V.floor_prices(-100, 0.5, 0.04) == (0.54, None))
check("floor_prices flat", V.floor_prices(0, 0.5, 0.04) == (None, None))
v = view({"rOH": 0.5, "dOH": 0.5}, {}, positions={"rOH": 100, "dOH": -100})
out = V.apply_floor([Order("rOH", False, 0.40, 50, "mm"), Order("rOH", False, 0.40, 50, "alloc"),
                     Order("rOH", True, 0.48, 50, "mm"), Order("dOH", True, 0.60, 50, "ladder"),
                     Order("dOH", True, 0.60, 50, "alloc"), Order("dOH", False, 0.45, 50, "mm"),
                     Order("rOH", False, 0.55, 50, "mm"), Order("rTX", False, 0.10, 50, "mm")], v, s)
check("long's ask moved up to p - value_sell_margin", near(out[0].price, 0.46), out[0])
check("swap sale (alloc) uses alloc_swap_sell_margin", near(out[1].price, 0.47), out[1])
check("adding bid on a long untouched", near(out[2].price, 0.48))
check("short's buy-back moved down to p + margin", near(out[3].price, 0.54), out[3])
check("short's swap buy-back uses the swap margin", near(out[4].price, 0.53), out[4])
check("adding ask on a short untouched", near(out[5].price, 0.45))
check("ask already above the floor untouched", near(out[6].price, 0.55))
check("unpriced market untouched", near(out[7].price, 0.10) and len(out) == 8)

# ---- tail quotes ----
v = view({"fav": 0.92, "rOH": 0.5}, {"fav": ([(0.90, 500)], [(0.93, 500)]), "rOH": ([(0.45, 500)], [(0.55, 500)])})
q = {(o.eid, o.is_bid): o for o in V.tail_quotes(v, risk(), s)}
check("favourite bid capped at the hurdle price p/(1+h)", near(q[("fav", True)].price, 0.875), q.get(("fav", True)))
check("favourite ask never below 1-(1-p)/(1+h) nor within mm_min_edge", near(q[("fav", False)].price, 0.93),
      q.get(("fav", False)))
check("tail quotes are tagged value and rest", q[("fav", True)].tag == "value" and not q[("fav", True)].ioc)
check("bid sized by Kelly within the per-order cash cap", q[("fav", True)].size == int(1000 / 0.875),
      q[("fav", True)].size)
check("no tail quote inside the band", not any(e == "rOH" for e, _ in q))
v = view({"fav": 0.92}, {"fav": ([(0.80, 500)], [(0.95, 500)])})
q = {(o.eid, o.is_bid): o for o in V.tail_quotes(v, risk(), s)}
check("one tick better than the best other bid below the hurdle", near(q[("fav", True)].price, 0.805))
check("one tick better than the best other ask above the hurdle", near(q[("fav", False)].price, 0.945))
v = view({"fav": 0.92}, {"fav": ([(0.90, 500)], [(0.93, 500)])}, positions={"fav": 100})
q = V.tail_quotes(v, risk(), s)
check("long: only the adding bid", len(q) == 1 and q[0].is_bid, q)
v = view({"fav": 0.97}, {"fav": ([(0.94, 500)], [(0.99, 500)])})
check("above tail_high no YES bid", not any(o.is_bid for o in V.tail_quotes(v, risk(), s)))
v = view({"lng": 0.08}, {"lng": ([(0.15, 500)], [(0.17, 500)])})
q = V.tail_quotes(v, risk(), s)
check("ref guard: Polymarket far below the book -> no bid, the ask rests", len(q) == 1 and not q[0].is_bid
      and near(q[0].price, 0.165), q)
check("no tail adds while adds are paused", V.tail_quotes(v, risk(adds_paused=True), s) == [])
check("no tail adds in reduce-only", V.tail_quotes(v, risk(reduce_only=True), s) == [])

# ---- a swap over two steps ----
P = {"rOH": 0.50, "rTX": 0.60, "dTX": 0.40}
SWAP_BOOKS = {"rOH": ([(0.49, 2000)], [(0.52, 2000)]), "rTX": ([(0.40, 1000)], [(0.50, 1000)])}
a = V.Allocator()
o1 = a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(), 0.0, s)
check("swap step 1: the sale only, IOC at the touch", len(o1) == 1 and o1[0].eid == "rOH" and not o1[0].is_bid
      and near(o1[0].price, 0.49) and o1[0].ioc and o1[0].tag == "alloc", o1)
check("sale sized to the holding (1000 at 0.49 frees 490 for the 500 level)", o1 and o1[0].size == 1000, o1)
a.note_fills([fill("rOH", False, 0.49, 1000, "alloc")])
o2 = a.step(view(P, SWAP_BOOKS, {"rOH": 0}, read_at=95.0, mono=110.0), risk(), 0.0, s)
check("no buy before a cash read taken after the sale", o2 == [], o2)
o3 = a.step(view(P, SWAP_BOOKS, {"rOH": 0}, cash=10500.0, read_at=130.0, mono=131.0), risk(), 0.0, s)
check("swap step 2: the buy once a later cash read shows the money", len(o3) == 1 and o3[0].eid == "rTX"
      and o3[0].is_bid and near(o3[0].price, 0.50) and o3[0].size == 980, o3)
check("the run is finished after the buy", a.status()["pending"] == 0 and a.status()["state"] == "idle",
      a.status())

a = V.Allocator()
a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(), 0.0, s)
o = a.step(view(P, SWAP_BOOKS, {"rOH": 0}, cash=10500.0, read_at=130.0, mono=131.0), risk(), 0.0, s)
check("no buy when the sale never filled", o == [], o)

a = V.Allocator()
a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(), 0.0, s)
a.note_fills([fill("rOH", False, 0.49, 1000, "alloc")])
gone = dict(SWAP_BOOKS, rTX=([(0.40, 1000)], [(0.58, 1000)]))
o = a.step(view(P, gone, {"rOH": 0}, cash=10500.0, read_at=130.0, mono=131.0), risk(), 0.0, s)
check("no buy when the level vanished (cash stays, run ends)", o == [] and a.status()["pending"] == 0
      and a.sells_stopped, (o, a.status()))

a = V.Allocator()
o = a.step(view(P, gone, {"rOH": 1000}), risk(), 0.0, s)
check("no sale when no level pays", o == [], o)

a = V.Allocator()
o = a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(adds_paused=True), 0.0, s)
check("no swap sale while adds are paused (the gate would refuse its buy)", o == [], o)

# ---- spare cash ----
SPARE_BOOKS = {"rTX": ([(0.40, 1000)], [(0.50, 20000)])}
a = V.Allocator()
o = a.step(view(P, SPARE_BOOKS, cash=25000.0), risk(), 0.0, s)
check("spare cash above the reserve buys at once", len(o) == 1 and o[0].is_bid and o[0].eid == "rTX"
      and o[0].size == int((25000 - 25 - s.mm_reserve_usd) / 0.5) and o[0].ioc, o)
a = V.Allocator()
check("no spare-cash buy below the reserve", a.step(view(P, SPARE_BOOKS, cash=15000.0), risk(), 0.0, s) == [])
a = V.Allocator()
o = a.step(view(P, SPARE_BOOKS, cash=25000.0), risk(), 0.0, replace(s, alloc_max_turnover_usd=300.0))
check("turnover cap bounds the cash rotated in an hour", len(o) == 1 and o[0].size == 600, o)
o = a.step(view(P, SPARE_BOOKS, cash=25000.0, mono=200.0, now=NOW.replace(minute=30)), risk(), 0.0,
           replace(s, alloc_max_turnover_usd=300.0, alloc_interval_s=60.0))
check("...and the next run within the hour has none left", o == [] and a.turnover(NOW.timestamp() + 1800) == 300.0,
      o)
a = V.Allocator()
o = a.step(view(P, SPARE_BOOKS, cash=25000.0, read_at=-1000.0), risk(), 0.0, s)
check("nothing without a fresh cash read", o == [] and a.state.startswith("waiting"), a.state)

# ---- the refill ----
REFILL_BOOKS = {"rOH": ([(0.49, 2000)], [(0.52, 2000)])}
a = V.Allocator()
check("zero demand -> no refill sale", a.step(view(P, REFILL_BOOKS, {"rOH": 1000}, cash=0.0), risk(), 0.0, s) == [])
a = V.Allocator()
o = a.step(view(P, REFILL_BOOKS, {"rOH": 1000}, cash=0.0), risk(), 200.0, s)
check("refill sells only the demand (200 / 0.49 shares)", len(o) == 1 and o[0].tag == "refill"
      and not o[0].is_bid and o[0].size == 408 and o[0].ioc, o)
a = V.Allocator()
o = a.step(view(P, REFILL_BOOKS, {"rOH": 1000}, cash=2900.0), risk(), 3000.0, s)
check("refill nets out free cash (3000 - 2900)", len(o) == 1 and o[0].size == 204, o)
a = V.Allocator()
o = a.step(view(P, REFILL_BOOKS, {"rOH": 1000}, cash=0.0), risk(), 1e6, s)
check("refill never sells more than is held", len(o) == 1 and o[0].size == 1000, o)
a.step(view(P, REFILL_BOOKS, {"rOH": 592}, cash=0.0, mono=120.0, read_at=115.0), risk(), 200.0, s)
o = a.step(view(P, REFILL_BOOKS, {"rOH": 592}, cash=0.0, mono=130.0, read_at=125.0), risk(), 200.0, s)
check("refill at most once per interval", o == [], o)
a = V.Allocator()
low = {"rOH": ([(0.45, 2000)], [(0.52, 2000)])}
check("refill never below the value floor", a.step(view(P, low, {"rOH": 1000}, cash=0.0), risk(), 500.0, s) == [])
a.note_fills([Fill("rOH", False, 0.49, 408, None, datetime.now(timezone.utc), "refill")])
check("funding counts refill sales", near(a.funding()["refill_sold_24h"], 408 * 0.49, 1e-6), a.funding())

# ---- pins, headline races, no flip ----
a = V.Allocator()
o = a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(), 0.0, replace(s, alloc_pin="rOH"))
check("a pinned label is never sold", o == [], o)
a = V.Allocator()
o = a.step(view({"rUS": 0.5}, {"rUS": ([(0.49, 2000)], [(0.52, 2000)])}, {"rUS": 1000}, cash=0.0), risk(), 500.0, s)
check("party-control races are never traded", o == [], o)
v = view({"rTX": 0.40, "dTX": 0.60}, {"rTX": ([(0.50, 1000)], [(0.52, 1000)])}, {"rTX": 100})
check("no short level where we are long (no flip)", not any(lv.eid == "rTX" and lv.short for lv in V.levels(v, s)))
a = V.Allocator()
o = a.step(v, risk(), 1e6, s)
check("a sale never exceeds the position", all(x.size <= 100 for x in o) and o, o)
a = V.Allocator()
a.step(view(P, SWAP_BOOKS, {"rOH": 1000}), risk(), 0.0, s)
a.note_fills([fill("rOH", False, 0.49, 1000, "alloc")])
o = a.step(view(P, SWAP_BOOKS, {"rOH": 0, "rTX": -50}, cash=10500.0, read_at=130.0, mono=131.0), risk(), 0.0, s)
check("no buy that would flip a position taken meanwhile", o == [] and a.status()["pending"] == 0, o)

# ---- prefer short ----
PS = {"rOH": 0.70, "dOH": 0.30}
PS_BOOKS = {"rOH": ([(0.55, 1000)], [(0.57, 1000)]), "dOH": ([(0.50, 1000)], [(0.53, 1000)])}
lv = V.levels(view(PS, PS_BOOKS), s)
check("prefer short: no YES buy on the leg whose other leg shorts for less cash",
      not any(x.eid == "rOH" and not x.short for x in lv) and any(x.eid == "dOH" and x.short for x in lv), lv)
lv = V.levels(view(PS, PS_BOOKS), replace(s, alloc_prefer_short=False))
check("prefer short off: the YES buy is a level", any(x.eid == "rOH" and not x.short for x in lv))

# ---- status and persistence ----
a = V.Allocator()
a.step(view(P, SPARE_BOOKS, cash=25000.0), risk(), 0.0, s)
st, d = a.status(), a.to_dict()
check("status has the old alloc fields", all(k in st for k in ("state", "last_run", "turnover_hour", "pending",
                                                                 "pairs_planned", "ev_gain_est")), st)
b = V.Allocator(d)
check("a restart keeps the run clock (no second run within the interval)",
      b.step(view(P, SPARE_BOOKS, cash=25000.0, mono=5.0, read_at=1.0), risk(), 0.0, s) == [])
check("funding has the refill fields", set(a.funding()) >= {"refill_sold_24h", "refill_demand", "refill_raised"})

n = len(RESULTS)
print(f"{sum(RESULTS)}/{n} passed")
sys.exit(0 if all(RESULTS) else 1)
