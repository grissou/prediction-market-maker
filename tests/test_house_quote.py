"""
Owner's item 4 (3 Oct 09:19 UTC live): Dem U.S. House (long +9,876) showed NO quote on either side, although
its reducing ask needs no cash. Reproduced here with the fake exchange: Dem House long +9,876 / Rep House short
-9,396, books near 0.87 / 0.14, Polymarket 0.925 / 0.075, the bot in reduce-only.

Cause pinned by this file: the reference guard in Bot.decide (no_ask when Polymarket - book_fv > ref_guard_gap,
no_bid when book_fv - Polymarket > ref_guard_gap) switches off the REDUCING side of both legs (Dem: its ask; Rep: its
bid), and reduce-only switches off the adding sides -> nothing at all. Not the race-net clip: a long Dem / short Rep
pair is the SAME bet, so the race-netted positions are +/-19,272 (not +480) and the clip leaves the full size.
The guard on exits is a design choice (documented in Config: "it says the exit is the wrong trade"): not changed.

Run:  python tests/test_house_quote.py      (exit code 0 = all passed)
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeRefs, lvl, make_bot, market      # noqa: E402

failures = []
count = {"n": 0}


def check(name, cond, detail=None):
    count["n"] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    if not cond:
        failures.append(name)


DEM, REP = "31", "32"


def house_bot(dem_ref=0.925, rep_ref=0.075, reduce_only=True, guard=None):
    extra = (market("5", DEM, "Democratic", "U.S. House"), market("6", REP, "Republican", "U.S. House"))
    books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
             "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
             "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]},
             DEM: {"bids": [lvl(0.86, 5000)], "asks": [lvl(0.88, 5000)]},
             REP: {"bids": [lvl(0.13, 5000)], "asks": [lvl(0.15, 5000)]}}
    a, b = make_bot(books=books, extra_markets=extra)
    b.cfg.size_by_activity = True                    # live: headline_size_frac / headline_position_frac apply
    a.inv.update({DEM: 9876, REP: -9396})
    b.refs = FakeRefs({"U.S. House|Democratic": dem_ref, "U.S. House|Republican": rep_ref})
    if reduce_only:                                  # the live backstop: reduce-only most of the day
        b.cfg.max_worst_case_frac, b.cfg.worst_case_backstop_frac = 0.001, 0.002
    if guard is not None:
        b.cfg.ref_guard_gap = guard
    logging.disable(logging.CRITICAL)
    try:
        b.cycle()
    finally:
        logging.disable(logging.NOTSET)
    return a, b


def sides(q):
    return [s for s, p in (("bid", q.bid), ("ask", q.ask)) if p is not None]


print("--- the 09:19 case: reduce-only, Polymarket 0.925 / 0.075 vs books 0.87 / 0.14")
a, b = house_bot()
dem, rep = b.ex[DEM], b.ex[REP]
check("reduce-only is on", b.global_reduce)
check("Dem and Rep House are one race group", sorted(b.groups.get("U.S. House", [])) == [DEM, REP], b.groups)
check("race-netted positions are +19,272 / -19,272 (long Dem + short Rep is the same bet, not +480)",
      round(dem.eff) == 19272 and round(rep.eff) == -19272, (dem.eff, rep.eff))
check("Dem House: no quote on either side (reproduced)", sides(dem.quote) == [], dem.quote)
check("Rep House: no quote on either side", sides(rep.quote) == [], rep.quote)
check("nothing rests on either leg", not a.ours(DEM) and not a.ours(REP), (a.ours(DEM), a.ours(REP)))
check("the reduce-only race-net clip is NOT what emptied them (ro_clip empty)",
      dem.ro_clip == "" and rep.ro_clip == "", (dem.ro_clip, rep.ro_clip))
book_dem = 0.87 / (0.87 + 0.14)                     # the book's own price, the race normalised to sum to 1
book_rep = 0.14 / (0.87 + 0.14)
check("the reference guard fires on the REDUCING side of both legs (gap 6.4c > ref_guard_gap 5c)",
      0.925 - book_dem > b.cfg.ref_guard_gap and book_rep - 0.075 > b.cfg.ref_guard_gap,
      (0.925 - book_dem, book_rep - 0.075))

print("--- the guard alone is the cause: widen it just past the gap and the reducing sides rest")
a, b = house_bot(guard=0.07)
dem, rep = b.ex[DEM], b.ex[REP]
check("Dem House: the reducing ask is quoted (and no adding bid in reduce-only)", sides(dem.quote) == ["ask"], dem.quote)
check("Rep House: the reducing bid is quoted (and no adding ask)", sides(rep.quote) == ["bid"], rep.quote)
check("both rest on the exchange", [s for s, _, _ in a.ours(DEM)] == ["ask"] and [s for s, _, _ in a.ours(REP)] == ["bid"],
      (a.ours(DEM), a.ours(REP)))
check("full headline size, not clipped to 480: 10,000 shares each",
      dem.quote.ask_size == 10000 and rep.quote.bid_size == 10000, (dem.quote, rep.quote))
check("the Dem ask sits behind the book's best ask 0.88 (fair value leans on raw Polymarket 0.925): rests, rarely fills",
      dem.quote.ask > 0.88, dem.quote.ask)
check("secondary: the 10,000 ask exceeds the 9,876 held, so the engine books it as buy NO (needs cash: a 400 at cash 0)",
      any(o["exchangeId"] == DEM and o["side"] == "no" and o["action"] == "buy" for o in a.orders.values()),
      list(a.orders.values()))

print("--- a gap inside ref_guard_gap: the default guard lets the exit rest")
a, b = house_bot(dem_ref=0.905, rep_ref=0.095)
check("Polymarket 0.905 / 0.095 (gap ~4.4c): Dem ask and Rep bid rest",
      sides(b.ex[DEM].quote) == ["ask"] and sides(b.ex[REP].quote) == ["bid"], (b.ex[DEM].quote, b.ex[REP].quote))

print("--- outside reduce-only the guard still blocks the same exits (it is not a reduce-only effect)")
a, b = house_bot(reduce_only=False)
check("not reduce-only", not b.global_reduce)
check("Dem House: no ask (guard)", "ask" not in sides(b.ex[DEM].quote), b.ex[DEM].quote)
check("Rep House: no bid (guard)", "bid" not in sides(b.ex[REP].quote), b.ex[REP].quote)

print("--- write budget: headline markets are sent before ordinary ones, not starved")
k_head = b.change_key(b.ex[DEM], pull=False)
k_plain = b.change_key(b.ex["11"], pull=False)
check("change_key: a headline quote sorts before an ordinary one of the same kind", k_head < k_plain, (k_head, k_plain))

print(f"\n{count['n'] - len(failures)}/{count['n']} passed")
sys.exit(1 if failures else 0)
