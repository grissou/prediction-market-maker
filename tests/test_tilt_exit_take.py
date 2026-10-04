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

print("--- guards: jump, liquidity, set legs, basket legs, skip")
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
b.basket_legs = {"11": 1000.0, "12": 500.0}
run(api, b)
check("basket legs never taken", [x[0] for x in sent(api)] == ["21"], sent(api))
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
                setattr(cfg, k_, getattr(bn.cfg, k_))
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
