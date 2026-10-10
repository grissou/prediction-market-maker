"""
Tests for mmbot2/ladder.py: level prices for longshots and favourites, the 8% edge filter, level sizes, the
carve-out (best edges first, covered sales free), never crossing the book or our own orders, the re-quote
throttle, fills into status, and the to_dict round trip.

Run:  python3 tests2/test_ladder.py      (exit code 0 = all passed)
"""
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mmbot2.config import Settings                                          # noqa: E402
from mmbot2.state import Account, Book, Fill, Market, Order, View           # noqa: E402
from mmbot2.ladder import Ladder                                            # noqa: E402

RESULTS = []
T0 = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
S = Settings()
BIG = replace(S, harvest_total_usd=1e6)      # a budget that never binds, to test prices and sizes alone


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   [{detail}]"))


MARKETS = {e: Market(e, e, "Race " + e, None, None, None) for e in ("ls", "fav", "mid", "ls2")}


def view(p, books, resting=None, positions=None, now=T0):
    return View(now=now, mono=0.0, markets={e: MARKETS[e] for e in p}, books=books, positions=positions or {},
                resting=resting or {}, account=Account(value=100000.0, cash=50000.0), p=p)


def ls_book(bid=0.08, ask=0.12):
    return Book([(bid, 500)], [(ask, 500)])


def fav_book(bid=0.88, ask=0.92):
    return Book([(bid, 500)], [(ask, 500)])


def levels(orders, eid=None):
    return sorted((o.price, o.size, o.is_bid) for o in orders if eid is None or o.eid == eid)


def as_resting(orders):
    out = {}
    for k, o in enumerate(orders):
        out.setdefault(o.eid, []).append(replace(o, oid=f"o{k}"))
    return out


# 1. a longshot: asks at the best other ask + 0, 2, 4, 6c, each 3k of collateral (1 - price a share)
got = Ladder().plan(view({"ls": 0.03}, {"ls": ls_book()}), None, BIG)
check("longshot: asks at 12, 14, 16, 18c", [x[0] for x in levels(got)] == [0.12, 0.14, 0.16, 0.18], levels(got))
check("longshot: all asks, tag ladder, resting", all(not o.is_bid and o.tag == "ladder" and not o.ioc for o in got))
check("longshot: size = 3000 / (1 - price)", [x[1] for x in levels(got)] == [3409, 3488, 3571, 3658], levels(got))

# 2. a favourite: bids at the best other bid - 0, 2, 4, 6c, each 3k (price a share)
got = Ladder().plan(view({"fav": 0.97}, {"fav": fav_book()}), None, BIG)
check("favourite: bids at 82, 84, 86, 88c", [x[0] for x in levels(got)] == [0.82, 0.84, 0.86, 0.88], levels(got))
check("favourite: size = 3000 / price", [x[1] for x in levels(got)] == [3658, 3571, 3488, 3409], levels(got))
check("favourite: all bids", all(o.is_bid for o in got))

# 3. the 8% edge filter: p = 7c makes the 12c level 5.7% (out) and the 14c level 8.1% (in)
got = Ladder().plan(view({"ls": 0.07}, {"ls": ls_book()}), None, BIG)
check("edge filter: 12c level dropped, 14c kept", [x[0] for x in levels(got)] == [0.14, 0.16, 0.18], levels(got))
got = Ladder().plan(view({"ls": 0.11}, {"ls": ls_book()}), None, BIG)
check("edge filter: no level clears at p = 11c", got == [], levels(got))
got = Ladder().plan(view({"mid": 0.5}, {"mid": Book([(0.48, 10)], [(0.52, 10)])}), None, BIG)
check("middle band: no ladder", got == [])
got = Ladder().plan(view({}, {"ls": ls_book()}), None, BIG)
check("unpriced: no ladder", got == [])

# 4. the carve-out: 10k of cash, the best edges first, the last level trimmed to what is left
got = Ladder().plan(view({"ls": 0.03}, {"ls": ls_book()}), None, S)
cash = sum(o.usd for o in got)
check("budget: total collateral <= 10k", cash <= 10000.0 + 1e-6, cash)
check("budget: 18, 16, 14c full, 12c trimmed to the ~1k left", levels(got) == [(0.12, 1137, False), (0.14, 3488, False),
                                                                   (0.16, 3571, False), (0.18, 3658, False)],
      levels(got))
two = view({"ls": 0.03, "fav": 0.95}, {"ls": ls_book(), "fav": fav_book()})
got = Ladder().plan(two, None, S)
fav_edges = sorted(((0.95 - o.price) / o.price) for o in got if o.eid == "fav")
check("budget across markets: the 18c ask (18.3%) beats the 88c bid (8.0%)",
      (0.18, 3658, False) in levels(got, "ls") and all(e > 0.08 for e in fav_edges)
      and sum(o.usd for o in got) <= 10000.0 + 1e-6, levels(got))
got = Ladder().plan(view({"ls": 0.03}, {"ls": ls_book()}, positions={"ls": 20000}), None, S)
check("budget: a sale of YES held needs no cash (all four levels full)",
      [x[1] for x in levels(got)] == [3409, 3488, 3571, 3658], levels(got))

# 5. never at or through the book or our own orders on the other side
own = {"ls": [Order("ls", True, 0.15, 100, "mm", oid="m1")]}
got = Ladder().plan(view({"ls": 0.03}, {"ls": ls_book()}, resting=own), None, BIG)
check("no crossing: asks above our own 15c bid only", [x[0] for x in levels(got)] == [0.16, 0.18], levels(got))
own = {"fav": [Order("fav", False, 0.86, 100, "value", oid="v1")]}
got = Ladder().plan(view({"fav": 0.97}, {"fav": fav_book()}, resting=own), None, BIG)
check("no crossing: bids below our own 86c ask only", [x[0] for x in levels(got)] == [0.82, 0.84], levels(got))
got = Ladder().plan(view({"ls": 0.03}, {"ls": Book([(0.12, 10)], [(0.12, 10)])}), None, BIG)
check("no crossing: never at the other side's best", 0.12 not in [x[0] for x in levels(got)], levels(got))

# 6. the re-quote throttle
lad = Ladder()
first = lad.plan(view({"ls": 0.03}, {"ls": ls_book()}), None, S)
rest = as_resting(first)
rest["ls"][0] = replace(rest["ls"][0], size=rest["ls"][0].size - 400)          # one level partly filled
dropped = rest["ls"].pop()                                                     # one level filled (gone)
later = T0 + timedelta(seconds=300)
got = lad.plan(view({"ls": 0.03}, {"ls": ls_book(ask=0.125)}, resting=rest, now=later), None, S)
want = levels(rest["ls"]) + levels([dropped])
check("throttle: touch moved 0.5c within 15 min, levels kept at resting price and size", levels(got) == sorted(want),
      (levels(got), sorted(want)))
got = lad.plan(view({"ls": 0.03}, {"ls": ls_book(ask=0.14)}, resting=rest, now=later), None, S)
check("throttle: touch moved 2c, levels re-placed from it", min(o.price for o in got) == 0.14, levels(got))
lad = Ladder()
lad.plan(view({"ls": 0.03}, {"ls": ls_book()}), None, S)
got = lad.plan(view({"ls": 0.05}, {"ls": ls_book()}, now=later), None, S)
check("throttle: p moved 2c, re-planned (12c level now under 8%)", 0.12 not in [x[0] for x in levels(got)],
      levels(got))
lad = Ladder()
lad.plan(view({"ls": 0.03}, {"ls": ls_book()}), None, S)
got = lad.plan(view({"ls": 0.03}, {"ls": ls_book(ask=0.13)}, now=T0 + timedelta(seconds=901)), None, S)
check("throttle: after harvest_requote_s, re-placed from today's touch", min(o.price for o in got) == 0.13,
      levels(got))
got = lad.plan(view({"ls": 0.03}, {}, resting=as_resting(got), now=T0 + timedelta(seconds=950)), None, S)
check("no fresh book: resting levels held", min(o.price for o in got) == 0.13 and len(got) == 4, levels(got))

# 7. fills into status
lad = Ladder()
first = lad.plan(view({"ls": 0.03}, {"ls": ls_book()}), None, S)
lad.plan(view({"ls": 0.03}, {"ls": ls_book()}, resting=as_resting(first), now=T0 + timedelta(seconds=60)), None, S)
lad.note_fills([Fill("ls", False, 0.18, 1000, "o3", T0, tag="ladder"),
                Fill("ls", True, 0.10, 500, "x", T0, tag="mm"),
                Fill("ls", False, 0.16, 100, "o2", T0 - timedelta(hours=25), tag="ladder")])
st = lad.status()
check("status: 1000 filled in 24 h (the mm fill and the old one left out)", st["filled_24h"] == 1000, st)
check("status: edge at p = 1000 x (18 - 3)c = 150", abs(st["edge_filled_24h"] - 150.0) < 1e-6, st)
check("status: resting levels, markets and collateral", st["levels_resting"] == 4 and st["markets"] == 1
      and abs(st["collateral_resting"] - sum(o.usd for o in first)) < 0.01, st)
check("status: carve-out used + free = 10k", abs(st["carve"]["used"] + st["carve"]["free"] - 10000.0) < 0.02
      and st["carve"]["total"] == 10000.0, st)

# 8. to_dict round trip: the anchors and the fills survive a handover
d = lad.to_dict()
again = Ladder(d)
check("to_dict: round trip", again.to_dict() == d, (again.to_dict(), d))
got = again.plan(view({"ls": 0.03}, {"ls": ls_book()}, resting=as_resting(first),
                      now=T0 + timedelta(seconds=120)), None, S)
check("to_dict: the restored ladder keeps the levels within the window", levels(got) == levels(first),
      (levels(got), levels(first)))

n, ok = len(RESULTS), sum(RESULTS)
print(f"{ok}/{n} passed")
sys.exit(0 if ok == n else 1)
