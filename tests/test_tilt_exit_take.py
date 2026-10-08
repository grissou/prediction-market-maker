"""
Offline tests for Package 9 F2 tilt_exit_take (analysis/p9/SPEC_F2_F5.md): the Package 8 tilt exits (the side that
shrinks a position ADDING to |tilt_exposure|) taken as immediate-or-cancel orders inside a cost cap measured from the
market's TILTED fair value tilted_ref_for(Polymarket), never raw Polymarket. Covers: settings and ranges; order
(longshot NO, favourite YES, the rest; marked below the exit first); the cost cap from the tilted reference (fail-
before: the raw reference would allow a worse price); the per-hour $ cap and the per-cycle cap; write budget refusal;
never flips, never more than the best level's depth, max_leg_frac; the jump guard; NO+NO set legs and basket legs
skipped; cash gate pre-check before our quotes are pulled; a NO buy-back is a covered "sell NO"; our own orders are
cancelled before the take and the leftover after; status.json tilt_exit_takes; flag off identical to the branch head
(git show e2e763d:mm_bot.py) on a grid.

Run:  python tests/test_tilt_exit_take.py      (exit code 0 = all passed)
"""
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
M.alert = lambda msg: None
M.notify = lambda *a, **k: False
BASE_REF = "e2e763d"
REFS = {"11": 0.05, "12": 0.95, "21": 0.55, "22": 0.45}     # Ohio: Rep a longshot, Dem the favourite
LIQUID = set(REFS)
S = 0.11


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def books_a():
    # tilted fv (s 0.11, 2 legs): 11 0.0995, 12 0.9005, 21 0.5445, 22 0.4555
    return {"11": {"bids": [lvl(0.09, 1000)], "asks": [lvl(0.105, 1000)]},
            "12": {"bids": [lvl(0.895, 1000)], "asks": [lvl(0.92, 1000)]},
            "21": {"bids": [lvl(0.54, 1000)], "asks": [lvl(0.58, 1000)]},
            "22": {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.47, 1000)]}}


def bot(inv, books=None, tilt=S, **cfg):
    api, b = make_bot(live=True, books=books or books_a())
    b.cfg.tilt_exit_take = True
    b.cfg.ref_tilt_enabled = True
    b.cfg.ref_tilt_rampin_min = 0.0
    b.cfg.selftest_enabled = False
    b.cfg.reduce_no_as_sell = True
    b.tilt_s = tilt
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    api.inv = dict(inv)
    for e in b.ex:
        b.ex[e].inv = float(inv.get(e, 0))
        b.ex[e].book = api.full_book(e)           # the cached books (a cycle's download)
    b.tilt_exposure = sum(q * (REFS[e] - 0.5) for e, q in inv.items())
    return api, b


def run(api, b, refs=REFS, liquid=LIQUID, now=None, mine=None, skip=()):
    inv = {e: float(q) for e, q in api.inv.items()}
    return b.tilt_exit_takes(refs, liquid, inv, mine or {}, time.monotonic() if now is None else now, skip=skip)


def sent(api):
    return [(o["exchangeId"], o["side"], o["action"], o["quantity"], o["price"]) for o in api.wire]


A = {"11": -1000, "12": 500, "21": 3000}          # contributions +450 / +225 / +150: all three are tilt exits

print("--- settings: defaults off, live-overridable with ranges")
c = M.Config()
check("defaults: tilt_exit_take False, max_cost 0.01, per_hour 15000, max_per_cycle 3, max_leg_frac 0.5",
      (c.tilt_exit_take, c.tilt_exit_take_max_cost, c.tilt_exit_take_per_hour, c.tilt_exit_take_max_per_cycle,
       c.tilt_exit_take_max_leg_frac) == (False, 0.01, 15000.0, 3, 0.5))
good, bad = M.validate_overrides({"tilt_exit_take": True, "tilt_exit_take_max_cost": 0.05,
                                  "tilt_exit_take_per_hour": 200000, "tilt_exit_take_max_per_cycle": 20,
                                  "tilt_exit_take_max_leg_frac": 1.0}, c)
check("all five accepted at their upper bounds", len(good) == 5 and not bad, (good, bad))
good, bad = M.validate_overrides({"tilt_exit_take_max_cost": 0.051, "tilt_exit_take_per_hour": -1,
                                  "tilt_exit_take_max_per_cycle": 0, "tilt_exit_take_max_leg_frac": 1.01}, c)
check("out-of-range values refused (4)", not good and len(bad) == 4, (good, bad))
good, bad = M.validate_overrides({"tilt_exit_take_max_per_cycle": 2.5, "tilt_exit_take": 1}, c)
check("a fraction per cycle / a non-bool flag refused", not good and len(bad) == 2, (good, bad))

print("--- flag off: nothing")
api, b = bot(A, tilt_exit_take=False)
check("flag off: no market acted on, no request", run(api, b) == set() and not api.calls, api.calls)

print("--- order and sizes")
api, b = bot(A)
done = run(api, b)
s = sent(api)
check("three takes: 11 (longshot NO) first, then 12 (favourite YES), then 21",
      [x[0] for x in s] == ["11", "12", "21"] and done == {"11", "12", "21"}, s)
check("11: a covered 'sell NO' 500 @ 0.895 (buy back the short at the 0.105 ask; 0.5 x 1000)",
      s[0] == ("11", "no", "sell", 500, 0.895), s[0])
check("12: sell YES 250 @ 0.895 (0.5 x 500); 21: sell YES 1000 @ 0.54 (depth 1000 < 0.5 x 3000)",
      s[1] == ("12", "yes", "sell", 250, 0.895) and s[2] == ("21", "yes", "sell", 1000, 0.54), s)
check("positions shrink, none flips: 11 -500, 12 250, 21 2000", api.inv == {"11": -500, "12": 250, "21": 2000},
      api.inv)
st = b.tet_stats
check("stats: 3 takes, 1750 shares, $ 447.5 + 223.75 + 540, cost 500 x 0.0055 + 250 x 0.0055 + 1000 x 0.0045",
      st["count"] == 3 and st["shares"] == 1750 and abs(st["usd"] - 1211.25) < 1e-6
      and abs(st["cost"] - (750 * 0.0055 + 1000 * 0.0045)) < 1e-6, st)
calls = [x[0] for x in api.calls if x[0] in ("cancel_all", "batch")]
check("each take: our orders cancelled, the batch, the leftover cancelled (IOC)",
      calls == ["cancel_all", "batch", "cancel_all"] * 3, calls)
ttl = [(M.parse_ts(o["expirationDate"]) - M.utcnow()).total_seconds() for o in api.wire]
check("expiry = take_order_ttl (10 s)", all(5 < t <= b.cfg.take_order_ttl + 0.5 for t in ttl), ttl)
def bk_utah():
    bk_ = books_a()
    bk_["22"]["asks"] = [lvl(0.46, 1000)]         # 22's tilted fv 0.4555: cost 0.45c
    return bk_


api, b = bot({"21": 3000, "22": -3000}, books=bk_utah())  # Utah: 21 r 0.55 > c and 22 r 0.45 < c -> rest group, both exits
b.pos_marks = {"22": 0.48}                        # 22's ask 0.46 is BELOW its mark: buying back gains vs the mark
run(api, b)
check("same group: the market marked below its exit price first (22, then 21)", [x[0] for x in sent(api)] == ["22", "21"],
      sent(api))
api, b = bot({"21": 3000, "22": -3000}, books=bk_utah())
b.pos_marks = {"21": 0.53}                        # 21's bid 0.54 above its mark -> 21 first
run(api, b)
check("... and the other way round (21 marked 0.53 < bid 0.54 first)", [x[0] for x in sent(api)] == ["21", "22"],
      sent(api))

print("--- per-cycle and per-hour caps")
api, b = bot(A, tilt_exit_take_max_per_cycle=1)
run(api, b)
check("max_per_cycle 1: only 11", [x[0] for x in sent(api)] == ["11"], sent(api))
api, b = bot(A, tilt_exit_take_per_hour=600.0)
t0 = time.monotonic()
run(api, b, now=t0)
check("per_hour 600: 11 ($447.5) then 12 cut to 170 ($152.15), 21 nothing",
      [(x[0], x[3]) for x in sent(api)] == [("11", 500), ("12", 170)], sent(api))
n = len(api.wire)
run(api, b, now=t0 + 120)
check("2 min later: the hour's cap is used up, nothing sent", len(api.wire) == n, sent(api)[n:])
run(api, b, now=t0 + 3601)
check("an hour later: takes again", len(api.wire) > n, sent(api)[n:])
api, b = bot(A, tilt_exit_take_per_hour=600.0)
real_place = api.place_batch
api.place_batch = lambda orders: [{**r, "data": {k_: v for k_, v in (r.get("data") or {}).items()
                                                 if k_ != "quantityTraded"}} for r in real_place(orders)]
run(api, b, now=t0)
check("fills not reported in the answer: the hour is charged the whole order (11 $447.5, 12 cut to 170)",
      [(x[0], x[3]) for x in sent(api)] == [("11", 500), ("12", 170)] and b.tet_stats["count"] == 0, sent(api))
api, b = bot(A)
run(api, b, now=t0)
n = len(api.wire)
run(api, b, now=t0 + 10)
check("a market rests take_cooldown_seconds after its take", len(api.wire) == n, sent(api)[n:])

print("--- the cost cap is measured from the TILTED fair value")
bk = books_a()
bk["12"]["bids"] = [lvl(0.89, 1000)]             # cost 0.9005 - 0.89 = 0.0105 > 0.01
api, b = bot({"12": 500}, books=bk)
run(api, b)
check("12 bid 0.89: 1.05c below the tilted fv 0.9005 -> not taken", not api.wire, sent(api))
bk["12"]["bids"] = [lvl(0.891, 1000)]            # cost 0.0095
api, b = bot({"12": 500}, books=bk)
run(api, b)
check("12 bid 0.891: 0.95c -> taken (raw Polymarket 0.95 would refuse it: 5.9c)", sent(api) == [("12", "yes", "sell", 250, 0.891)],
      sent(api))
api, b = bot({"12": 500}, books=bk, tilt_exit_take_max_cost=0.0)
run(api, b)
check("max_cost 0: nothing below the tilted fv", not api.wire)
api, b = bot({"12": 500}, books=bk, ref_tilt_enabled=False)
run(api, b)
check("tilt off in the quotes (ref_tilt_enabled False): fv = raw 0.95 -> 0.891 not taken", not api.wire, sent(api))
# A long-tilt book (total exposure < 0): the exit of a LONG longshot (11 +1000 at r 0.05) sells at the bid; the
# tilted fv 0.0995 is ABOVE raw Polymarket: a bid of 0.085 is 4c above raw (raw would allow it) but 1.45c below
# the tilted fv -> refused (fail-before: measured from raw this sale would go).
bk = books_a()
bk["11"]["bids"] = [lvl(0.085, 1000)]
api, b = bot({"11": 1000}, books=bk)
check("long longshot in a long-tilt book is a tilt exit (ask side)", b.tilt_exit_side(b.ex["11"], 0.05, 1000) == "ask")
run(api, b)
check("fail-before: bid 0.085 (above raw 0.05) is 1.45c below the tilted fv 0.0995 -> NOT sold", not api.wire,
      sent(api))
bk["11"]["bids"] = [lvl(0.09, 1000)]
api, b = bot({"11": 1000}, books=bk)
run(api, b)
check("bid 0.09 (0.95c below the tilted fv) -> sold 500", sent(api) == [("11", "yes", "sell", 500, 0.09)], sent(api))
plan = b.tet_market(b.ex["11"], 0.05, 1000, {"11": 1000}, time.monotonic())
check("tet_market's fv is tilted_ref_for(r), not r", plan is None or abs(plan["fv"] - 0.0995) < 1e-9, plan)

print("--- never flips, never more than the best level, max_leg_frac")
bk = books_a()
bk["21"]["bids"] = [lvl(0.54, 100000)]
api, b = bot({"21": 3000}, books=bk, tilt_exit_take_max_leg_frac=1.0)
run(api, b)
check("frac 1, deep book: exactly the position (3000), flat after", sent(api) == [("21", "yes", "sell", 3000, 0.54)]
      and api.inv["21"] == 0, (sent(api), api.inv))
bk["21"]["bids"] = [lvl(0.54, 37), lvl(0.539, 5000)]
api, b = bot({"21": 3000}, books=bk, tilt_exit_take_max_leg_frac=1.0)
run(api, b)
check("best level 37 shares: 37 (never walks the book)", sent(api) == [("21", "yes", "sell", 37, 0.54)], sent(api))
api, b = bot({"21": 1}, tilt_exit_take_max_leg_frac=0.5)
run(api, b)
check("a 1-share position goes whole (1 share at least)", [x[3] for x in sent(api)] == [1], sent(api))
api, b = bot({"21": 3000}, tilt_exit_take_max_leg_frac=0.0)
run(api, b)
check("max_leg_frac 0: nothing", not api.wire)
api, b = bot({"21": -3000})                      # short where r > c: shrinks |tilt| the other way -> no exit
run(api, b)
check("a position against the tilt total is not an exit: nothing", not api.wire)

print("--- guards: jump, liquidity, set legs, skip")
api, b = bot(A)
now = time.monotonic()
b.ex["11"].ref_jump_at = now - 5
b.ex["12"].ref_moved_at = now - 5
b.ex["21"].cooldown_until = now + 30
run(api, b, now=now)
check("Polymarket jump / urgent move within ref_jump_cooldown_seconds / cooldown: none taken", not api.wire,
      sent(api))
run(api, b, now=now + b.cfg.ref_jump_cooldown_seconds + 31)
check("after the cooldown: taken", len(api.wire) == 3, sent(api))
api, b = bot(A)
run(api, b, liquid={"12", "21"}, refs={"11": 0.05, "12": 0.95})
check("no liquid Polymarket (11) / no price (21): only 12", [x[0] for x in sent(api)] == ["12"], sent(api))
api, b = bot({"11": -1000, "12": -600, "21": 3000}, tilt_exit_take_max_leg_frac=1.0)
run(api, b)
check("NO+NO: 11 short 1000 with 12 short 600 -> only the lone 400 NO sold",
      [x for x in sent(api) if x[0] == "11"] == [("11", "no", "sell", 400, 0.895)], sent(api))
check("... 12 (short the favourite) is no tilt exit; the NO+NO set (600) stays", api.inv["11"] == -600
      and api.inv["12"] == -600, api.inv)
api, b = bot({"11": -1000, "12": -1000, "21": 3000}, tilt_exit_take_max_leg_frac=1.0)
run(api, b)
check("all of 11's NO in a set: 11 not touched", not [x for x in sent(api) if x[0] in ("11", "12")], sent(api))
api, b = bot(A)
run(api, b, skip={"11", "Ohio Senate"})
check("markets / races already acted on this cycle skipped", [x[0] for x in sent(api)] == ["21"], sent(api))

print("--- write budget, cash gate")
api, b = bot(A)
api.writes_left = lambda: 2
k = b.takes_skipped_budget
run(api, b)
check("2 writes left (< 3): nothing sent, no cancel, counted", not api.calls or all(c[0] == "book" for c in api.calls)
      and not api.wire and b.takes_skipped_budget == k + 1, api.calls)
api, b = bot({"11": -1000, "12": 500}, reduce_no_as_sell=False, cash_gate_enabled=True)
b.cg_cash, b.cg_reserved, b.cg_spent, b.cg_read_at = 0.0, 0.0, 0.0, time.monotonic()
run(api, b)
calls11 = [c for c in api.calls if len(c) > 1 and c[1] == "11" and c[0] == "cancel_all"]
check("cash gate, 0 cash, no covered NO sale: 11's buy-back (a purchase) refused BEFORE our quotes are pulled",
      not calls11 and [x[0] for x in sent(api)] == ["12"], (api.calls, sent(api)))
api, b = bot({"11": -1000, "12": 500}, cash_gate_enabled=True)
b.cg_cash, b.cg_reserved, b.cg_spent, b.cg_read_at = 0.0, 0.0, 0.0, time.monotonic()
run(api, b)
check("cash gate, 0 cash, reduce_no_as_sell: 11 goes as a covered 'sell NO' (no cash needed)",
      ("11", "no", "sell", 500, 0.895) in sent(api), sent(api))

print("--- our own orders: cancelled first (never a self-cross)")
api, b = bot({"21": 3000})
api.orders[999] = {"id": 999, "exchangeId": "21", "quantity": 100, "open": True, "side": "yes", "action": "buy",
                   "priceLimit": 0.55, "expirationDate": M.iso(M.utcnow() + M.timedelta(seconds=300))}
mine = {"21": [M.Resting("999", "21", True, 0.55, 100, None)]}
run(api, b, mine=mine)
check("own bid 0.55 above the others' 0.54: book stripped of it, cancelled before the sale at 0.54",
      sent(api) == [("21", "yes", "sell", 1000, 0.54)] and 999 not in api.orders
      and [c[0] for c in api.calls if c[0] in ("cancel_all", "batch")][:2] == ["cancel_all", "batch"],
      (sent(api), api.calls))
check("no fill against ourselves (no fill on our order 999)", not [f for f in api.fills if f["orderId"] == 999])

print("--- full cycle: status.json tilt_exit_takes, journal line")
grab = []


class Grab(logging.Handler):
    def emit(self, rec):
        grab.append(rec.getMessage())


def cycle_bot(on):
    a, bb = make_bot(books=books_a())
    a.inv.update(A)
    bb.refs = FakeRefs({"Ohio Senate|Republican": 0.05, "Ohio Senate|Democratic": 0.95,
                        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45})
    bb.cfg.selftest_enabled = False
    bb.cfg.reduce_no_as_sell = True
    bb.cfg.ref_tilt_enabled = True
    bb.cfg.ref_tilt_rampin_min = 0.0
    bb.tilt.update = lambda samples, now_m: S
    bb.tilt_s = S
    bb.cfg.tilt_exit_take = on
    return a, bb


h = Grab()
M.log.addHandler(h)
old = M.log.level
M.log.setLevel(logging.INFO)
a, bb = cycle_bot(True)
bb.cycle()
M.log.removeHandler(h)
M.log.setLevel(old)
tet = bb.health.get("tilt_exit_takes")
check("cycle, flag on: takes in the cycle (status count 3)", tet is not None and tet["count"] == 3, tet)
check("journal: 'TILT EXIT TAKE <label> <side> <qty> @ <px> (tilted fv <fv>, cost <c>)'",
      any(m.startswith("TILT EXIT TAKE") and "tilted fv 0.0995" in m and "cost 0.0055" in m for m in grab),
      [m for m in grab if "TILT" in m])
a, bb = cycle_bot(False)
bb.cycle()
check("cycle, flag off: no tilt_exit_takes in status.json", "tilt_exit_takes" not in bb.health)

print("--- F2b tilt_exit_take_split_sets: sell the LONGSHOT-NO leg of a NO+NO set (C-9)")
c = M.Config()
check("F2b defaults: tilt_exit_take_split_sets False, tilt_exit_split_max_ref 0.10",
      (c.tilt_exit_take_split_sets, c.tilt_exit_split_max_ref) == (False, 0.10))
good, bad = M.validate_overrides({"tilt_exit_take_split_sets": True, "tilt_exit_split_max_ref": 0.5}, c)
check("F2b: both overridable (upper bounds accepted)", len(good) == 2 and not bad, (good, bad))
good, bad = M.validate_overrides({"tilt_exit_split_max_ref": 0.005}, c)
good2, bad2 = M.validate_overrides({"tilt_exit_split_max_ref": 0.51, "tilt_exit_take_split_sets": 1}, c)
check("F2b: max_ref 0.005 / 0.51 / a non-bool flag refused", not good and len(bad) == 1 and not good2
      and len(bad2) == 2, (bad, bad2))

SET = {"11": -1000, "12": -1000, "21": 3000}     # Ohio: 1000 NO+NO sets; 21 makes tilt_exposure > 0 (+150)


def sbot(inv, cash, books=None, split=True, api_cash=None, **cfg):
    kw = dict(tilt_exit_take_max_leg_frac=1.0, cash_gate_enabled=True, cash_gate_reserve=0.0,
              tilt_exit_take_split_sets=split)
    kw.update(cfg)
    api, b = bot(inv, books=books, **kw)
    b.cg_cash, b.cg_reserved, b.cg_spent, b.cg_read_at = float(cash), 0.0, 0.0, time.monotonic()
    if api_cash is not None:
        api.cash, api.set_collateral = float(api_cash), True
    return api, b


def of(api, eid):
    return [x for x in sent(api) if x[0] == eid]


api, b = sbot(SET, 5000, split=False)
run(api, b)
check("F2b flag off: the set leg 11 never sold (all its NO is in a set), 12 untouched", not of(api, "11")
      and not of(api, "12") and api.inv["11"] == -1000, sent(api))
api, b = sbot(SET, 5000)
run(api, b)
check("F2b on, cash 5000: 11 (longshot NO) sells NO 1000 @ 0.895 (its whole set part), FIRST",
      sent(api)[0] == ("11", "no", "sell", 1000, 0.895), sent(api))
check("F2b: the favourite leg 12 keeps its 1000 NO (never sold); 11 flat", not of(api, "12")
      and api.inv["11"] == 0 and api.inv["12"] == -1000, (sent(api), api.inv))
check("F2b: the gate is charged 1.0 a set share (cg_spent 1000)", abs(b.cg_spent - 1000.0) < 1e-6, b.cg_spent)
sp = b.tet_splits
check("F2b stats: splits {count 1, shares 1000, cash_freed 895}", sp["count"] == 1 and sp["shares"] == 1000
      and abs(sp["cash_freed"] - 895.0) < 1e-6, sp)
check("F2b: the remaining favourite NO is no tilt exit while tilt_exposure > 0 (contribution < 0)",
      b.tilt_exposure > 0 and -1000 * (0.95 - 0.5) < 0 and b.tilt_exit_side(b.ex["12"], 0.95, -1000) is None
      and b.tet_market(b.ex["12"], 0.95, -1000, {"11": 0.0, "12": -1000.0}, time.monotonic()) is None)
api, b = sbot({"12": -1000, "21": 3000}, 5000)   # (after the split: 12 alone, lone NO now)
run(api, b)
check("F2b: ... and F2 does not take it either (lone favourite NO, total > 0)", not of(api, "12"), sent(api))
api, b = sbot(SET, 300)
run(api, b)
check("F2b: cash 300 -> only 300 of the set part (partial sizing to the cash)", of(api, "11") ==
      [("11", "no", "sell", 300, 0.895)], sent(api))
api, b = sbot(SET, 0)
run(api, b)
check("F2b: cash 0 -> no split (no lone part: 11 untouched)", not of(api, "11"), sent(api))
api, b = sbot({"11": -1000, "12": -600, "21": 3000}, 250)
run(api, b)
check("F2b: lone 400 + set 600, cash 250 -> 650 (the lone part free, 250 of the set part)",
      of(api, "11") == [("11", "no", "sell", 650, 0.895)] and api.inv["12"] == -600, (sent(api), api.inv))
check("F2b: split stats count the set part only (250 shares, 223.75)", b.tet_splits["shares"] == 250
      and abs(b.tet_splits["cash_freed"] - 250 * 0.895) < 1e-6, b.tet_splits)
api, b = sbot(SET, 5000, cash_gate_enabled=False)
run(api, b)
check("F2b: cash gate off -> no split (the set part cannot be sized to the cash)", not of(api, "11"), sent(api))
api, b = sbot(SET, 5000, reduce_no_as_sell=False)
run(api, b)
check("F2b: reduce_no_as_sell off -> no split", not of(api, "11"), sent(api))
api, b = sbot(SET, 5000, tilt_exit_split_max_ref=0.05)
run(api, b)
check("F2b: max_ref 0.05 (11's raw 0.05 not below) -> no split", not of(api, "11"), sent(api))

print("--- F2b: the favourite leg, cost cap, hourly cap, order")
api, b = sbot({"11": -1000, "12": -1000}, 5000)
b.tilt_exposure = -500.0                          # a long-tilt total: 12 (short the favourite) is the exit side
run(api, b)
check("F2b: long-tilt total -> 12 is an exit but its set part is never sold; 11 is no exit: nothing",
      b.tilt_exit_side(b.ex["12"], 0.95, -1000) == "bid" and not api.wire, sent(api))
check("F2b: tet_split_ok never on the race's highest-priced leg (even with max_ref 0.5 and the gate on)",
      not M.Bot.tet_split_ok(b, b.ex["12"], 0.95, time.monotonic()))
bk = books_a()
bk["11"]["asks"] = [lvl(0.11, 1000)]             # cost 0.11 - 0.0995 = 1.05c > 1c
api, b = sbot(SET, 5000, books=bk)
run(api, b)
check("F2b: ask 0.11 is 1.05c above the TILTED fv 0.0995 -> no split", not of(api, "11"), sent(api))
bk["11"]["asks"] = [lvl(0.109, 1000)]            # 0.95c
api, b = sbot(SET, 5000, books=bk)
run(api, b)
check("F2b: ask 0.109 (0.95c; raw Polymarket 0.05 would say 5.9c) -> split 1000 @ 0.891",
      of(api, "11") == [("11", "no", "sell", 1000, 0.891)], sent(api))
api, b = sbot(SET, 5000, tilt_exit_take_per_hour=300.0)
run(api, b)
check("F2b: per_hour 300 -> 335 shares (300 / 0.895), nothing else this hour",
      sent(api) == [("11", "no", "sell", 335, 0.895)], sent(api))
REFS_U = {"11": 0.05, "12": 0.95, "21": 0.97, "22": 0.03}
bk = books_a()
bk["22"] = {"bids": [lvl(0.07, 1000)], "asks": [lvl(0.085, 1000)]}   # 22 tilted fv 0.0817: cost 0.33c
api, b = sbot({"11": -1000, "12": -1000, "22": -1000}, 5000, books=bk)
b.tilt_exposure = 450 - 450 + 470.0
run(api, b, refs=REFS_U, liquid=set(REFS_U))
check("F2b order: the split (11, contribution 450) BEFORE the lone longshot NO (22, contribution 470)",
      [x[0] for x in sent(api)] == ["11", "22"], sent(api))
bk = books_a()                                    # (the run above consumed the books)
bk["22"] = {"bids": [lvl(0.07, 1000)], "asks": [lvl(0.085, 1000)]}
api, b = sbot({"11": -1000, "12": -1000, "22": -1000}, 5000, books=bk, split=False)
b.tilt_exposure = 470.0
run(api, b, refs=REFS_U, liquid=set(REFS_U))
check("... flag off: only the lone 22", [x[0] for x in sent(api)] == ["22"], sent(api))

print("--- F2b: exchange refusal -> split cooldown (no loop); the fake exchange's set rule")
grab2 = []


class Grab2(logging.Handler):
    def emit(self, rec):
        grab2.append(rec.getMessage())


h2 = Grab2()
M.log.addHandler(h2)
old2 = M.log.level
M.log.setLevel(logging.INFO)
api, b = sbot(SET, 5000, api_cash=100)           # the gate thinks 5000, the exchange has 100 (< 1 a share x 1000)
t0 = time.monotonic()
run(api, b, now=t0)
check("F2b refused by the exchange (set_collateral, cash 100 < 1000): 11 unchanged, the gate refunded",
      of(api, "11") == [("11", "no", "sell", 1000, 0.895)] and api.inv["11"] == -1000 and abs(b.cg_spent) < 1e-6
      and b.tet_splits["count"] == 0, (sent(api), api.inv, b.cg_spent))
cd = b.cfg.take_cooldown_seconds
check("F2b: the race is blocked for take_cooldown_seconds x 10", abs(b.tet_split_block.get("Ohio Senate", 0)
                                                                     - (t0 + 10 * cd)) < 1e-6, b.tet_split_block)
n = len(of(api, "11"))
run(api, b, now=t0 + cd + 1)
check("F2b: past the market's take cooldown but inside the split cooldown: no new split", len(of(api, "11")) == n,
      sent(api))
run(api, b, now=t0 + 10 * cd + 1)
check("F2b: after the split cooldown it is tried again (and refused again)", len(of(api, "11")) == n + 1,
      sent(api))
check("F2b: the refusal is logged once per race an hour", sum("tilt exit split on" in m and "refused" in m
                                                              for m in grab2) == 1,
      [m for m in grab2 if "refused" in m])
api, b = sbot(SET, 5000, api_cash=1000)          # exactly 1 a share: the exchange accepts
run(api, b)
fills11 = [f for f in api.fills if f["exchangeId"] == "11"]
check("F2b fake exchange, cash 1000 >= 1 x 1000: split accepted, 1000 NO sold at the 0.105 ask (proceeds 895)",
      api.inv["11"] == 0 and sum(f["quantity"] for f in fills11) == 1000
      and all(abs(f["price"] - 0.105) < 1e-9 for f in fills11) and abs(b.tet_splits["cash_freed"] - 895) < 1e-6,
      (api.inv, fills11, b.tet_splits))
check("F2b journal: 'TILT EXIT SPLIT <label> sells NO 1000 @ 0.895 (set part; frees ~$895)'",
      any(m.startswith("TILT EXIT SPLIT") and "sells NO 1000 @ 0.895 (set part; frees ~$895)" in m for m in grab2),
      [m for m in grab2 if "SPLIT" in m])
M.log.removeHandler(h2)
M.log.setLevel(old2)

print("--- F2b: a 3-leg race with NO on all three splits only the longshot legs")
lib = market("5", "13", "Libertarian", "Ohio Senate")
bk3 = books_a()
bk3["11"] = {"bids": [lvl(0.07, 1000)], "asks": [lvl(0.085, 1000)]}   # 3 legs: tilted fv 0.0812 (cost 0.38c)
bk3["13"] = {"bids": [lvl(0.09, 1000)], "asks": [lvl(0.11, 1000)]}    # tilted fv 0.1079 (cost 0.21c)
bk3["12"] = {"bids": [lvl(0.80, 1000)], "asks": [lvl(0.82, 1000)]}    # tilted fv 0.8110
api3, b3 = make_bot(live=True, books=bk3, extra_markets=(lib,))
for k_, v in dict(tilt_exit_take=True, ref_tilt_enabled=True, ref_tilt_rampin_min=0.0, selftest_enabled=False,
                  reduce_no_as_sell=True, tilt_exit_take_max_leg_frac=1.0, cash_gate_enabled=True,
                  cash_gate_reserve=0.0, tilt_exit_take_split_sets=True).items():
    setattr(b3.cfg, k_, v)
b3.tilt_s = S
inv3 = {"11": -1000, "12": -1000, "13": -1000, "21": 3000}
api3.inv = dict(inv3)
for e in b3.ex:
    b3.ex[e].inv = float(inv3.get(e, 0))
    b3.ex[e].book = api3.full_book(e)
b3.cg_cash, b3.cg_reserved, b3.cg_spent, b3.cg_read_at = 5000.0, 0.0, 0.0, time.monotonic()
refs3 = {"11": 0.05, "12": 0.87, "13": 0.08, "21": 0.55, "22": 0.45}
b3.tilt_exposure = sum(q * (refs3[e] - (1 / 3 if e in ("11", "12", "13") else 0.5)) for e, q in inv3.items())
check("3-leg setup: Ohio has 3 legs, total tilt exposure > 0", b3.legs(b3.ex["11"]) == 3 and b3.tilt_exposure > 0,
      (b3.legs(b3.ex["11"]), b3.tilt_exposure))
b3.tilt_exit_takes(refs3, set(refs3), {e: float(q) for e, q in inv3.items()}, {}, time.monotonic())
s3 = sent(api3)
check("3-leg: 11 and 13 (longshots) each sell their 1000 set NO; 12 (the favourite) keeps its 1000",
      ("11", "no", "sell", 1000, 0.915) in s3 and ("13", "no", "sell", 1000, 0.89) in s3
      and not [x for x in s3 if x[0] == "12"] and api3.inv["12"] == -1000, (s3, api3.inv))
check("3-leg: the two splits come first, then 21", [x[0] for x in s3][:2] in (["11", "13"], ["13", "11"])
      and [x[0] for x in s3][2:] == ["21"], s3)

print("--- F2b: full cycle, status.json tilt_exit_takes.splits")
a, bb = cycle_bot(True)
a.inv.clear()
a.inv.update(SET)
bb.cfg.tilt_exit_take_split_sets = True
bb.cfg.cash_gate_enabled = True
bb.cycle()
spl = (bb.health.get("tilt_exit_takes") or {}).get("splits")
check("cycle, split flag on: status tilt_exit_takes.splits {count 1, shares 500 (max_leg_frac 0.5), cash_freed}",
      spl is not None and spl["count"] == 1 and spl["shares"] == 500 and abs(spl["cash_freed"] - 447.5) < 0.01, spl)
a, bb = cycle_bot(True)
bb.cycle()
check("cycle, split flag off: no splits key", "splits" not in (bb.health.get("tilt_exit_takes") or {}),
      bb.health.get("tilt_exit_takes"))

print("--- flag off identical to the branch head (git show %s:mm_bot.py) on a grid" % BASE_REF)
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", f"{BASE_REF}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p9base_f2.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p9base_f2", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k_: False
except Exception as e:                          # no git here: the pin is reported as failed
    print("    (base module unavailable:", e, ")")


def twin(inv, refs, overrides, books):
    out = []
    for mod in (M, base):
        api_x, bn = make_bot(books={e: {"bids": list(v["bids"]), "asks": list(v["asks"])} for e, v in books.items()})
        if mod is base:
            cfg = base.Config()
            for k_ in base.Config.__dataclass_fields__:
                setattr(cfg, k_, getattr(bn.cfg, k_, getattr(cfg, k_)))
            d = tempfile.mkdtemp()
            for k_ in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file",
                       "overrides_file", "market_edge_file", "handover_file"):
                setattr(cfg, k_, os.path.join(d, os.path.basename(getattr(cfg, k_))))
            api_x = FakeApi(True)
            api_x.markets_list, api_x.books = list(bn.api.markets_list), bn.api.books
            bn = base.Bot(api_x, cfg)
        api_x.inv.update(inv)
        bn.refs = FakeRefs(dict(refs))
        bn.cfg.selftest_enabled = False
        for k_, v in overrides.items():
            setattr(bn.cfg, k_, v)
        bn.tilt.update = lambda samples, now_m: S
        bn.tilt_s = S
        bn.cfg.ref_tilt_rampin_min = 0.0
        logging.disable(logging.CRITICAL)
        try:
            bn.cycle()
            bn.cycle()
        finally:
            logging.disable(logging.NOTSET)
        out.append((api_x, bn))
    return out


def strip(w):
    return [{k_: v for k_, v in o.items() if k_ != "expirationDate"} for o in w]


if base is None:
    check(f"base module (git show {BASE_REF}:mm_bot.py) loaded", False)
else:
    ok, diffs, n = True, [], 0
    refs = {"Ohio Senate|Republican": 0.05, "Ohio Senate|Democratic": 0.95,
            "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
    for inv in ({}, A, {"11": -1000, "12": -600, "21": 3000}, {"21": 3000, "22": -3000}):
        for over in ({}, {"ref_tilt_enabled": True}, {"reduce_no_as_sell": True, "tilt_exit_full_size": True},
                     {"ref_guard_exits": True, "tilt_exit_priority": True, "ref_tilt_enabled": True}):
            (an, bn), (ab, bb_) = twin(inv, refs, over, books_a())
            n += 1
            qn = {e: repr(bn.ex[e].quote) for e in bn.ex}
            qb = {e: repr(bb_.ex[e].quote) for e in bb_.ex}
            if (strip(an.wire) != strip(ab.wire) or qn != qb or an.inv != ab.inv
                    or bn.health.keys() != bb_.health.keys()):
                ok = False
                diffs.append((inv, over))
    check(f"flag off: orders sent, quotes, positions, status keys identical to {BASE_REF} on {n} scenarios", ok,
          diffs[:3])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
