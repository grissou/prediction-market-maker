"""
Offline tests for Package 12 Part L (analysis/p11/SPEC_P12.md): allocator items.
L1 alloc_set_rich_leg / alloc_set_ladder: a NO+NO set's RICH leg (NO on the favourite) is sold as a resting ladder of
   covered sales - YES bids on the favourite at its best bid and 2c / 4c below (NO sold at 1 - bid, 2c / 4c higher),
   each 1/3 of the set part, through the covered-sale path and the cash gate; the longshot-NO leg is never sold;
   never crossing our own asks; re-quoted at most hourly; pulled when unsafe.
L2 alloc_prefer_short: in a 2-leg race whose best bids (own quotes excluded) sum > 1 the allocator shorts the other leg
   instead of buying this one.
L3 pair_no_unwind_asks_le1: the NO+NO pair unwind only while the asks sum <= 1 + max_cost and the bids do not sum > 1.
Also: settings (defaults, ranges, one block), flags off identical to the branch head (a1b3eea) on a grid (wire orders,
quotes, allocator plan, arb_plan, status keys), py_compile.

Run:  python tests/test_p12_alloc.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "a1b3eea"                              # the branch head before Part L


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# Race Alpha: A1 Rep = the favourite (p 0.98, book 0.92 / 0.93: NO_fav ~7c vs 2c fair), A2 Dem = the longshot (p 0.02,
# book 0.06 / 0.08). Race Beta: B1 Rep (p 0.70, 0.55 / 0.60), B2 Dem (p 0.30, 0.38 / 0.42). Gamma: G1 / G2.
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate"}


def books_default():
    return {"A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08),
            "B1": bk(0.55, 0.60), "B2": bk(0.38, 0.42),
            "G1": bk(0.62, 0.66), "G2": bk(0.26, 0.31)}


def refs_default():
    return {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02,
            "Beta Senate|Republican": 0.70, "Beta Senate|Democratic": 0.30,
            "Gamma Senate|Republican": 0.70, "Gamma Senate|Democratic": 0.30}


SET = {"A1": -1000, "A2": -1000}


def mk_bot(inv=None, books=None, refs=None, cash=5000.0, enabled=True, live=True, extra_markets=(), races=None,
           **cfg):
    bks = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
           for e, v in (books or books_default()).items()}
    base = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
            "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
            "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
            "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    base.update(bks)
    mk = []
    for k, race in (races or RACES).items():
        mk += [market(f"m{k}1", f"{k}1", "Republican", race), market(f"m{k}2", f"{k}2", "Democratic", race)]
    api, b = make_bot(live=live, books=base, extra_markets=tuple(mk) + tuple(extra_markets))
    b.t["endDate"] = M.iso(CLOSE)
    for x in b.ex.values():
        x.close = CLOSE
    r = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
         "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
    r.update(refs_default() if refs is None else refs)
    b.refs = FakeRefs(r)
    c = b.cfg
    c.selftest_enabled = False
    c.reduce_no_as_sell = True
    c.arb_enabled = False
    c.cash_gate_enabled = True
    c.cash_gate_reserve = 0.0
    c.reserved_cash_mode = "ignore"
    c.alloc_enabled = enabled
    c.alloc_mm_reserve = 1000.0
    c.alloc_max_contract_usd = 100000.0
    c.alloc_min_edge_buy = 0.5                    # (no buys in the ladder tests: the ladder alone)
    c.max_position_frac = 1.0
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update(dict(SET) if inv is None else inv)
    api.cash = cash
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def quiet(fn, *a, **k):
    logging.disable(logging.CRITICAL)
    try:
        return fn(*a, **k)
    finally:
        logging.disable(logging.NOTSET)


def read(b, cash=None):
    b.cg_cash = b.api.cash if cash is None else cash
    b.cg_reserved, b.cg_spent = 0.0, 0.0
    b.cg_read_at = time.monotonic()


def warm(b):
    on = b.cfg.alloc_enabled
    b.cfg.alloc_enabled = False
    quiet(b.cycle)
    b.drain_writes(5)
    b.cfg.alloc_enabled = on
    quiet(b.cancel_everything)
    b.my_orders.clear()
    b.api.orders.clear()
    for x in b.ex.values():
        x.quote = None
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    read(b)


def sync(b):
    """Our record of resting orders and positions from the fake (as a cycle's reads would)."""
    b.sync_orders(b.api.open_orders("T"), time.monotonic() + 1e6)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))


def tick(b, skip=()):
    time.sleep(0.002)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.alloc_tick, M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def ladder(api, eid="A1"):
    """Our resting orders on eid as (wire side, wire action, wire price, shares)."""
    return sorted(((o["side"], o["action"], o["priceLimit"], o["quantity"]) for o in api.orders.values()
                   if o["exchangeId"] == eid), key=lambda t: t[2])


def fresh(b):
    now_m = time.monotonic()
    for x in b.ex.values():
        x.verified = now_m


# ============================================================================================ settings
print("--- settings")
D = M.Config()
KEYS = ["alloc_set_rich_leg", "alloc_set_ladder", "alloc_prefer_short", "pair_no_unwind_asks_le1"]
check("defaults: all off, ladder 0 / -2c / -4c (YES terms)",
      (D.alloc_set_rich_leg, D.alloc_set_ladder, D.alloc_prefer_short, D.pair_no_unwind_asks_le1)
      == (False, (0.0, -0.02, -0.04), False, False))
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after take_respect_reserve, in Config and OVERRIDABLE",
      _f[_f.index("take_respect_reserve") + 1:][:len(KEYS)] == KEYS
      and _ov[_ov.index("take_respect_reserve") + 1:][:len(KEYS)] == KEYS)
check("ranges", [M.OVERRIDABLE[k] for k in KEYS] == [(False, True), (-0.2, 0.0), (False, True), (False, True)])
good, bad = M.validate_overrides({k: getattr(D, k) for k in KEYS}, D)
check("every default inside its range", not bad and len(good) == len(KEYS), bad)
good, bad = M.validate_overrides({"alloc_set_ladder": [0, -0.01]}, D)
check("a ladder list accepted (as floats)", good == {"alloc_set_ladder": (0.0, -0.01)} and not bad, (good, bad))
good, bad = M.validate_overrides({"alloc_set_ladder": [0.02]}, D)
check("an offset above the best bid refused", not good and bad)
good, bad = M.validate_overrides({"alloc_set_ladder": [-0.01] * 9}, D)
check("more than LADDER_MAX_LEVELS levels refused", not good and bad)
good, bad = M.validate_overrides({"alloc_set_ladder": "0,-0.02"}, D)
check("a string refused (a JSON list of numbers)", not good and bad)
good, bad = M.validate_overrides({"alloc_set_rich_leg": 1, "alloc_prefer_short": "yes"}, D)
check("flags must be true / false", not good and len(bad) == 2, bad)

# ============================================================================================ flags off = base
print(f"--- flags off identical to the branch head ({BASE_REV}) on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_base.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = M.alert, (lambda *a_, **k: False)
except Exception as e:
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)


def twin(inv, cash, books=None, **cfg):
    api_n, bn = mk_bot(inv=inv, cash=cash, books=books, **cfg)
    c = base.Config()
    for k in base.Config.__dataclass_fields__:
        setattr(c, k, getattr(bn.cfg, k, getattr(c, k)))
    api_b = FakeApi(True)
    api_b.markets_list = list(api_n.markets_list)
    api_b.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                   for e, v in api_n.books.items()}
    api_b.inv, api_b.cash = dict(api_n.inv), cash
    api_b.pnl = lambda: (api_b.log("pnl"), {"totalAccountValue": api_b.equity, "cashBalance": api_b.cash})[1]
    c.status_file = bn.cfg.status_file + ".base"
    bb_ = base.Bot(api_b, c)
    bb_.t["endDate"] = bn.t["endDate"]
    for x in bb_.ex.values():
        x.close = CLOSE
    bb_.refs = FakeRefs(dict(bn.refs.prices))
    return api_n, bn, api_b, bb_


BIDS_OVER_1 = {**books_default(), "B2": bk(0.48, 0.52), "A1": bk(0.93, 0.95), "A2": bk(0.08, 0.10)}
if base is not None:
    ok_w, ok_q, ok_s, ok_a, ok_p, diffs = True, True, True, True, True, []
    grid = [(dict(SET), 5000.0, None, {}),
            (dict(SET), 5000.0, BIDS_OVER_1, {"alloc_min_edge_buy": 0.05}),
            ({"A1": -800, "A2": -900, "B1": 100}, 50000.0, BIDS_OVER_1,
             {"pair_no_unwind_max_cost": 0.05, "pair_unwind_enabled": True, "alloc_min_edge_buy": 0.05}),
            ({"B1": 300, "G1": -200}, 5000.0, None, {"alloc_set_cost_per_usd": 0.1, "alloc_mm_reserve": 0.0,
                                                     "alloc_enabled": False}),
            ({"A1": -500, "A2": -500}, 0.0, None, {"no_set_aware_bids": True, "value_mode": True})]
    for inv, cash, books, kw in grid:
        api_n, bn, api_b, bb_ = twin(inv, cash, books, **kw)
        for _ in range(2):
            quiet(bn.cycle)
            quiet(bb_.cycle)
            bn.drain_writes(5)
            bb_.drain_writes(5)
        sn = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_n.wire]
        sb = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_b.wire]
        if sorted(map(json.dumps, sn)) != sorted(map(json.dumps, sb)):   # (the writer threads' order may vary)
            ok_w = False
            diffs.append(("wire", inv, sn[:2], sb[:2]))
        if {e: tuple(x.quote.__dict__.values()) for e, x in bn.ex.items()} != \
                {e: tuple(x.quote.__dict__.values()) for e, x in bb_.ex.items()}:
            ok_q = False
            diffs.append(("quote", inv))
        for race, members in bn.groups.items():
            fv = {e: M.fair_value(x.book, bn.cfg) for e, x in bn.ex.items()}
            if (bn.arb_plan(members, dict(api_n.inv), fv, False)
                    != base.Bot.arb_plan(bb_, members, dict(api_b.inv), fv, False)):
                ok_a = False
        pn = quiet(bn.alloc_plan, dict(api_n.inv), time.monotonic(), 5000.0, set(), None)
        pb = quiet(base.Bot.alloc_plan, bb_, dict(api_b.inv), time.monotonic(), 5000.0, set(), None)
        if json.dumps(pn, sort_keys=True, default=str) != json.dumps(pb, sort_keys=True, default=str):
            ok_p = False
            diffs.append(("plan", inv, pn, pb))
        bn.write_status(True)
        bb_.write_status(True)
        with open(bn.cfg.status_file) as f:
            kn = json.load(f)
        with open(bb_.cfg.status_file) as f:
            kb = json.load(f)
        ev_ign = (set(getattr(M.Bot, "EV_KEYS", ())) | {"ev_outcome_history"}   # later read-only ev / carry report keys
                  | set(getattr(M.Bot, "MM_FUNDING_KEYS", ())))   # (and P14 mm_funding)
        if set(kn) - ev_ign != set(kb) - ev_ign or set(kn.get("alloc") or {}) != set(kb.get("alloc") or {}) \
                or set(bn.health) != set(bb_.health):
            ok_s = False
            diffs.append(("status", set(kn) ^ set(kb)))
    check("two full cycles send the same orders, any order (5 books / positions / settings)", ok_w, diffs[:1])
    check("every market's quote identical", ok_q, diffs[:1])
    check("arb_plan identical (also a NO+NO race with bids > 1 and pair_no_unwind_max_cost 0.05)", ok_a)
    check("alloc_plan identical (also bids sum > 1 in Beta)", ok_p, [d for d in diffs if d[0] == "plan"][:1])
    check("status.json / alloc / health keys identical", ok_s, diffs[:1])

# ============================================================================================ L1 ladder
print("--- L1: the rich leg's resting ladder")
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
plans, why = quiet(b.alloc_ladder_plan, dict(api.inv), time.monotonic())
pl = plans.get("Alpha Senate") or {}
check("the favourite (A1, p 0.98) is the rich leg", pl.get("eid") == "A1", (plans, why))
check("its edge-held (0.93 - 0.98) / 0.07 = -71% (strongly negative)", abs(pl.get("edge", 9) + 0.05 / 0.07) < 1e-9)
check("levels in YES terms: best bid 0.92, 0.90, 0.88, 333 shares each (1/3 of the 1000-share set part)",
      pl.get("levels") == [(0.92, 333), (0.90, 333), (0.88, 333)], pl.get("levels"))
check("no plan of any kind on the longshot leg A2", all(p_["eid"] != "A2" for p_ in plans.values()))
n_a0 = len(api.sent("cancel_all"))
touched = tick(b)
check("sent: three covered sales 'sell NO' at 0.08 / 0.10 / 0.12 on the favourite",
      ladder(api) == [("no", "sell", 0.08, 333), ("no", "sell", 0.10, 333), ("no", "sell", 0.12, 333)], ladder(api))
check("nothing ever sent on the longshot-NO leg", not ladder(api, "A2")
      and all(o["exchangeId"] != "A2" for o in api.wire))
check("resting (not immediate-or-cancel): no cancel after the placement", not api.sent("cancel_order")
      and len(api.sent("cancel_all")) == n_a0)
check("the favourite is not re-quoted this cycle (touched)", "A1" in touched)
metas = sorted((b.order_meta[o.order_id]["set_ladder"], b.order_meta[o.order_id]["sl_race"])
               for o in b.sl_orders())
check("tagged set_ladder 1 / 2 / 3, race Alpha", metas == [(1, "Alpha Senate"), (2, "Alpha Senate"),
                                                          (3, "Alpha Senate")], metas)
check("expires after MAX_ORDER_TTL (2 h)", all(abs((o.expires - M.utcnow()).total_seconds() - M.MAX_ORDER_TTL) < 60
                                               for o in b.sl_orders()))
check("status alloc.set_ladder {races 1, shares_resting 999, filled 0}",
      b.alloc_status().get("set_ladder") == {"races": 1, "shares_resting": 999, "filled": 0},
      b.alloc_status().get("set_ladder"))
n_batches = len(api.sent("batch"))
read(b)
tick(b)
check("next cycle (within the hour): no write at all", len(api.sent("batch")) == n_batches
      and not api.sent("cancel_order"))
# the hour passes, book unchanged -> kept (queue position)
b.alloc_ladder["Alpha Senate"]["at"] -= 3600
read(b)
tick(b)
check("an hour later, at target with life left: kept (no cancel, no batch)", len(api.sent("batch")) == n_batches
      and not api.sent("cancel_order") and len(ladder(api)) == 3)
# a fill on the 0.92 level
api.fill("A1", True, 100)
sync(b)
read(b)
tick(b)
check("a fill (100 NO sold): status filled 100", b.alloc_status()["set_ladder"]["filled"] == 100,
      b.alloc_status()["set_ladder"])
check("...the position fell (A1 -900): no flip", api.inv["A1"] == -900)
# the hour passes, the best bid moved down -> re-quoted
api.books["A1"] = bk(0.915, 0.93)
b.ex["A1"].book = api.full_book("A1")
b.ex["A1"].book = M.strip_own(b.ex["A1"].book, b.orders_by_eid(M.utcnow()).get("A1", []))
fresh(b)
b.alloc_ladder["Alpha Senate"]["at"] -= 3600
n_c, n_a = len(api.sent("cancel_order")), len(api.sent("cancel_all"))
read(b)
tick(b)
lad = ladder(api)
check("an hour later with the best bid at 0.915: re-quoted at 0.915 / 0.895 / 0.875 (NO 0.085 / 0.105 / 0.125)",
      [x[2] for x in lad] == [0.085, 0.105, 0.125], lad)
check("...the old levels cancelled one by one (never the exchange's cancel-all)",
      len(api.sent("cancel_order")) - n_c == 3 and len(api.sent("cancel_all")) == n_a)
check("...sized from the set part left (900 NO / 1000 NO: set part 900 -> 300 each)",
      [x[3] for x in lad] == [300, 300, 300], lad)

# own ask: never crossed
print("--- L1: never crossing our own orders")
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
# (our ask at 0.905 resting: injected into our record - on these books it would trade with the other traders' 0.92)
b.my_orders[9999] = M.Resting(9999, "A1", False, 0.905, 10, M.utcnow() + timedelta(hours=1))
n_c = len(api.sent("cancel_order"))
read(b)
tick(b)
lad = [x for x in ladder(api) if x[0] == "no"]
check("our ask at 0.905: the ladder bids at 0.92 / 0.90 are pulled at once (unsafe), not after the hour",
      len(api.sent("cancel_order")) - n_c >= 2, api.sent("cancel_order"))
check("...re-planned below the ask: every ladder bid <= 0.900 (YES)", lad and all(1 - x[2] <= 0.900 + 1e-9 for x in lad),
      lad)
check("...the 0.88 level kept its place, the merged 0.90 level 666", sorted((round(1 - x[2], 3), x[3]) for x in lad)
      == [(0.88, 333), (0.90, 666)], lad)
# the quote guard
lad_o = b.sl_orders("A1")
q0 = M.Quote(bid=0.85, bid_size=10, ask=0.895, ask_size=10, ask_limit=0.89)
q1 = b.sl_guard_quote(b.ex["A1"], q0, lad_o)
check("the quote's ask at / below a ladder bid moves a tick above it (0.905)", q1.ask == 0.905 and q1.ask_limit == 0.905,
      q1)
q2 = b.sl_guard_quote(b.ex["A1"], M.Quote(ask=0.95, ask_size=5, ask_limit=0.89), lad_o)
check("an ask above it stays; its keep limit rises above the ladder", q2.ask == 0.95 and q2.ask_limit == 0.905, q2)
check("no ask: unchanged", b.sl_guard_quote(b.ex["A1"], M.Quote(bid=0.5, bid_size=1), lad_o).ask is None)
check("the ladder's top is never at / above the book's ask (0.93)", all(o.price < 0.93 for o in lad_o))
# a full cycle: the quoting engine leaves the ladder alone
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
before = sorted(o.order_id for o in b.sl_orders())
n_a0 = len([c for c in api.sent("cancel_all") if c[1] == "A1"])
for _ in range(2):
    read(b)
    quiet(b.cycle)
    b.drain_writes(5)
after = {k for k, o in api.orders.items() if o["exchangeId"] == "A1" and o["side"] == "no"}
check("two full cycles of quoting: the ladder orders still rest (not cancelled by the quotes)",
      set(before) <= after, (before, after))
ours = api.ours("A1")
top = max([p for s, p, _ in ours if s == "bid"] or [0])
check("...and none of our asks there at / below a ladder bid", all(p > top + 1e-9 for s, p, _ in ours if s == "ask"),
      ours)
check("...no cancel-all on the favourite since the ladder rests", len([c for c in api.sent("cancel_all")
                                                                         if c[1] == "A1"]) == n_a0)
check("covered quote bids sell only what the ladder does not (cover_no_qty level 0: 1000 - 999 = 1)",
      b.cover_no_qty("A1", -1000, 0) == 1, b.cover_no_qty("A1", -1000, 0))
b.cfg.no_set_aware_bids = True
check("...with no_set_aware_bids: the lone part only, the ladder (set part) not counted twice",
      b.cover_no_qty("A1", -1000, 0) == 0)
b.cfg.no_set_aware_bids = False

print("--- L1: the cash gate")
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
read(b, cash=0.0)
n_b = len(api.sent("batch"))
tick(b)
check("cash 0: the gate refuses the set part (1.0 a share) - nothing sent, the ladder waits",
      len(api.sent("batch")) == n_b and not ladder(api) and "Alpha Senate" not in b.alloc_ladder)
read(b, cash=500.0)
tick(b)
lad = ladder(api)
check("next cycle with cash 500 (not an hour later): sent, trimmed to what the gate allows (<= 500 shares)",
      lad and sum(x[3] for x in lad) <= 500 and sum(x[3] for x in lad) >= 499, lad)
check("...the best level first (0.92 full, 0.90 trimmed)", lad[0] == ("no", "sell", 0.08, 333), lad)
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
b.cg_read_at = time.monotonic() - 1000
n_b = len(api.sent("batch"))
tick(b)
check("no fresh cash read: nothing placed", len(api.sent("batch")) == n_b and not ladder(api))

print("--- L1: when there is no ladder (and pulls)")


def no_plan(name, **kw):
    inv = kw.pop("inv", None)
    books = kw.pop("books", None)
    refs = kw.pop("refs", None)
    after = kw.pop("after", None)
    api_, b_ = mk_bot(inv=inv, books=books, refs=refs, **{"alloc_set_rich_leg": True, **kw})
    warm(b_)
    if after:
        after(b_)
    tick(b_)
    check(name, not ladder(api_) and not ladder(api_, "A2"), ladder(api_))
    return api_, b_


no_plan("flag off: nothing", alloc_set_rich_leg=False)
no_plan("allocator off: nothing", alloc_enabled=False)
no_plan("not a set (A2 not held): nothing", inv={"A1": -1000})
no_plan("favourite not rich (ask 0.99 vs p 0.98: edge-held +100%): nothing",
        books={**books_default(), "A1": bk(0.97, 0.99)})
no_plan("a tie (p 0.50 / 0.50): no favourite, nothing",
        refs={**refs_default(), "Alpha Senate|Republican": 0.5, "Alpha Senate|Democratic": 0.5},
        books={**books_default(), "A1": bk(0.45, 0.47), "A2": bk(0.45, 0.47)})
no_plan("the favourite pinned: nothing", alloc_pin="Rep Alpha Senate",
        after=lambda b_: setattr(b_.cfg, "alloc_pin", b_.ex["A1"].label))
no_plan("covered NO sales not in effect (reduce_no_as_sell off): nothing", reduce_no_as_sell=False)
no_plan("too small (2 sets: < 1 share a level): nothing", inv={"A1": -2, "A2": -2})
no_plan("inside the pre-close window: nothing",
        after=lambda b_: [setattr(x, "close", M.utcnow() + timedelta(minutes=5)) for x in b_.ex.values()])
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
b.api.live = False
tick(b)
check("dry run: planned, nothing sent", not api.orders and "Alpha Senate" in b.alloc_ladder)
b.api.live = True
# the value floor: p 0.90 with a 0.92 best bid -> never above p + margin
api, b = mk_bot(alloc_set_rich_leg=True, alloc_max_edge_sell=0.5,
                refs={**refs_default(), "Alpha Senate|Republican": 0.90, "Alpha Senate|Democratic": 0.10})
warm(b)
plans, _ = quiet(b.alloc_ladder_plan, dict(api.inv), time.monotonic())
lv = (plans.get("Alpha Senate") or {}).get("levels")
check("value floor: no level above p + value_sell_margin (0.905): 0.905 / 0.90 / 0.88", lv == [(0.905, 333),
                                                                                            (0.90, 333),
                                                                                            (0.88, 333)], lv)

# pulls
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
b.cfg.alloc_set_rich_leg = False
read(b)
tick(b)
check("flag switched off: every ladder order pulled", not ladder(api) and not b.alloc_ladder)
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
b.cfg.alloc_enabled = False
tick(b)
check("allocator switched off: every ladder order pulled", not ladder(api) and not b.sl_orders())
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
api.inv["A2"] = -500                              # (the longshot NO partly bought back elsewhere: set part 500)
b.ex["A2"].inv = -500
read(b)
tick(b)
lad = ladder(api)
check("set part shrinks to 500 (< 999 on sale): all pulled at once and re-planned at 166 a level",
      [x[3] for x in lad] == [166, 166, 166], lad)
api.inv["A2"] = 0
b.ex["A2"].inv = 0
read(b)
tick(b)
check("no longer a set: pulled", not ladder(api))
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
b.cur_liquid = set(b.cur_liquid) - {"A2"}
read(b)
tick(b)
check("p not liquid for a moment ('soft'): the resting ladder stays", len(ladder(api)) == 3)
b.alloc_ladder.clear()
tick(b)
check("...an orphan (no state, e.g. after a restart) is pulled", not ladder(api))
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b, skip={"Alpha Senate"})
check("a race traded by another feature this cycle: waits", not ladder(api))
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
b.api.writes_left = lambda: 0
tick(b)
check("no write budget: waits", not ladder(api))

# with our own covered quote bid already selling NO there
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
qb = {"exchangeId": "A1", "side": "yes", "action": "buy", "quantity": 400, "price": 0.85, "tournamentId": "T",
      "expirationDate": M.iso(M.utcnow() + timedelta(hours=1)), "_no_sell": True}
res = quiet(b.place_orders, [qb])
b.remember_order(qb, res[0]["data"], time.monotonic())
b.order_meta[res[0]["data"]["orderId"]] = {"our_side": "bid", "no_sell": True}
read(b)
tick(b)
lad = [x for x in ladder(api) if x[2] != 0.15]
check("our other covered NO sale (400) there: the ladder sells only the 600 left (200 a level)",
      [x[3] for x in lad] == [200, 200, 200], lad)

# B3 skips a laddered race
api, b = mk_bot(alloc_set_rich_leg=True, alloc_set_cost_per_usd=0.2, pair_unwind_enabled=True,
                pair_no_unwind_max_cost=0.0, books={**books_default(), "A1": bk(0.92, 0.93), "A2": bk(0.06, 0.07)})
warm(b)
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("B3 ranks the set before any ladder", any(p_["sell"]["kind"] == "set" for p_ in pairs))
tick(b)
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("...not once its rich leg is laddered", not any(p_["sell"]["kind"] == "set" for p_ in pairs), pairs)

# 3-leg race: only the favourite's NO
api, b = mk_bot(alloc_set_rich_leg=True, inv={"A1": -600, "A2": -600, "X3": -600},
                extra_markets=(market("mX3", "X3", "Libertarian", "Alpha Senate"),),
                books={**books_default(), "X3": bk(0.02, 0.04)},
                refs={**refs_default(), "Alpha Senate|Libertarian": 0.005, "Alpha Senate|Republican": 0.975})
api.books["X3"] = bk(0.02, 0.04)
warm(b)
plans, why = quiet(b.alloc_ladder_plan, dict(api.inv), time.monotonic())
check("3-leg set: only the favourite's NO is laddered", list(plans) == ["Alpha Senate"]
      and plans["Alpha Senate"]["eid"] == "A1", (plans, why))

# ============================================================================================ L2 prefer short
print("--- L2: prefer the short when the bids sum > 1")
BK2 = {**books_default(), "B2": bk(0.48, 0.52)}       # Beta bids 0.55 + 0.48 = 1.03


def plan2(**kw):
    inv = kw.pop("inv", {"A1": 0})
    books = kw.pop("books", BK2)
    api_, b_ = mk_bot(inv=inv, books=books, **{"alloc_min_edge_buy": 0.05, **kw})
    warm(b_)
    return api_, b_, quiet(b_.alloc_plan, dict(api_.inv), time.monotonic(), 50000.0, set(), None)


_, b, (pairs, info) = plan2()
buys = [(p_["buy"]["eid"], p_["buy"]["short"]) for p_ in pairs if p_["buy"]]
check("flag off: Beta Rep YES is bought too (edge 16.7%)", ("B1", False) in buys, buys)
_, b, (pairs, info) = plan2(alloc_prefer_short=True)
buys = [(p_["buy"]["eid"], p_["buy"]["short"]) for p_ in pairs if p_["buy"]]
check("flag on: no buy of Beta Rep YES", ("B1", False) not in buys, buys)
check("...the Beta Dem short is the route (edge (0.48 - 0.30) / 0.52)", ("B2", True) in buys and any(
    abs(p_["buy"]["edge"] - 0.18 / 0.52) < 1e-9 for p_ in pairs if p_["buy"] and p_["buy"]["eid"] == "B2"), buys)
check("...blocked_by prefer_short counted", info["blocked_by"].get("prefer_short") == 1, info)
check("...same exposure for less cash: 1 - 0.48 = 0.52 < 0.60 a share", 1 - 0.48 < 0.60)
check("legs map: B1 -> B2", b.alloc_prefer_short_legs({}, time.monotonic()) == {"B1": "B2"},
      b.alloc_prefer_short_legs({}, time.monotonic()))
_, b, (pairs, info) = plan2(alloc_prefer_short=True, books=books_default())
buys = [(p_["buy"]["eid"], p_["buy"]["short"]) for p_ in pairs if p_["buy"]]
check("bids sum 0.93 (<= 1): unchanged, Beta Rep bought", ("B1", False) in buys and "prefer_short" not in
      info["blocked_by"], buys)
_, b, (pairs, info) = plan2(alloc_prefer_short=True, inv={"B2": 100})
buys = [(p_["buy"]["eid"], p_["buy"]["short"]) for p_ in pairs if p_["buy"]]
check("long Beta Dem (cannot short it without a flip): Beta Rep still bought", ("B1", False) in buys, buys)
_, b, (pairs, info) = plan2(alloc_prefer_short=True, alloc_min_edge_buy=0.36)
check("the short's edge below alloc_min_edge_buy: not preferred (the buy rule decides alone)",
      b.alloc_prefer_short_legs({}, time.monotonic()) == {}, b.alloc_prefer_short_legs({}, time.monotonic()))
_, b, (pairs, info) = plan2(alloc_prefer_short=True, alloc_max_contract_usd=10.0)
check("no room left on Beta Dem (alloc_max_contract_usd): not preferred",
      b.alloc_prefer_short_legs({}, time.monotonic()) == {})
api, b, _ = plan2(alloc_prefer_short=True)
own = {"exchangeId": "B2", "side": "yes", "action": "buy", "quantity": 5, "price": 0.48, "tournamentId": "T",
       "expirationDate": M.iso(M.utcnow() + timedelta(hours=1))}
res = quiet(b.place_orders, [own])
b.remember_order(own, res[0]["data"], time.monotonic())
check("own quotes excluded: our bid at 0.48 is not counted (bids sum 0.55 + no other bid)",
      b.alloc_prefer_short_legs({}, time.monotonic()) == {}, b.alloc_prefer_short_legs({}, time.monotonic()))
_, b, _ = plan2(alloc_prefer_short=True)
check("a race traded this cycle (skip): unchanged", b.alloc_prefer_short_legs({}, time.monotonic(),
                                                                              {"Beta Senate"}) == {})
api, b = mk_bot(alloc_prefer_short=True, inv={"A1": 0}, extra_markets=(market("mB3", "B3", "Green", "Beta Senate"),),
                books={**BK2, "B3": bk(0.02, 0.04)}, refs={**refs_default(), "Beta Senate|Green": 0.01},
                alloc_min_edge_buy=0.05)
api.books["B3"] = bk(0.02, 0.04)
warm(b)
check("3-leg race: unchanged (no single-leg equivalent)", b.alloc_prefer_short_legs({}, time.monotonic()) == {})

# ============================================================================================ L3 unwind gate
print("--- L3: the pair unwind only while the asks sum <= 1 + cost and the bids do not sum > 1")


def arb(books, inv=None, **kw):
    api_, b_ = mk_bot(inv=inv or {"A1": -800, "A2": -900}, books=books,
                      **{"pair_unwind_enabled": True, "pair_no_unwind_max_cost": 0.05, "alloc_enabled": False,
                         "pair_unwind_race_order": True, **kw})
    warm(b_)
    for x in b_.ex.values():
        x.book = api_.full_book(x.eid)
    fv = {e: M.fair_value(x.book, b_.cfg) for e, x in b_.ex.items()}
    return b_, quiet(b_.arb_plan, b_.groups["Alpha Senate"], dict(api_.inv), fv, False)


BID_OVER = {**books_default(), "A1": bk(0.61, 0.62), "A2": bk(0.40, 0.41)}   # bids 1.01, asks 1.03
_, r = arb(BID_OVER)
check("flag off: asks 1.03 <= 1.05 -> the NO+NO unwind fires (bids 1.01 ignored)", r is not None and r[0] == "unwind", r)
_, r = arb(BID_OVER, pair_no_unwind_asks_le1=True)
check("flag on: the bids sum 1.01 > 1 -> no unwind (sold leg by leg instead)", r is None, r)
BID_UNDER = {**books_default(), "A1": bk(0.59, 0.62), "A2": bk(0.40, 0.41)}  # bids 0.99
_, r = arb(BID_UNDER, pair_no_unwind_asks_le1=True)
check("flag on: bids 0.99, asks 1.03 <= 1.05 -> fires", r is not None and r[0] == "unwind" and r[1] == "buy", r)
_, r = arb({**books_default(), "A1": bk(0.59, 0.66), "A2": bk(0.40, 0.41)}, pair_no_unwind_asks_le1=True)
check("flag on: asks 1.07 > 1.05 -> no unwind", r is None, r)
_, r = arb({**books_default(), "A1": bk(0.55, 0.57), "A2": bk(0.38, 0.40)}, pair_no_unwind_asks_le1=True)
check("a profitable unwind (asks 0.97) still fires", r is not None and r[0] == "unwind", r)
b, _ = arb(BID_OVER, pair_no_unwind_asks_le1=True)
own = {"exchangeId": "A1", "side": "yes", "action": "buy", "quantity": 5, "price": 0.61, "tournamentId": "T",
       "expirationDate": M.iso(M.utcnow() + timedelta(hours=1))}
res = quiet(b.place_orders, [own])
b.remember_order(own, res[0]["data"], time.monotonic())
b.ex["A1"].book = M.strip_own(b.api.full_book("A1"), b.orders_by_eid(M.utcnow()).get("A1", []))
check("own quotes excluded: our bid at 0.61 is not counted (no other bid there: bids not > 1)",
      not b.nono_unwind_gated(b.groups["Alpha Senate"], 1.03))
LONG = {**books_default(), "A1": bk(0.61, 0.62), "A2": bk(0.40, 0.41)}
_, r_on = arb(LONG, inv={"A1": 800, "A2": 900}, pair_no_unwind_asks_le1=True, arb_sellback=True)
_, r_off = arb(LONG, inv={"A1": 800, "A2": 900}, arb_sellback=True)
check("a long YES+YES set's sell-back is not gated (same plan with the flag on and off)", r_on == r_off, (r_on, r_off))
b, _ = arb({**books_default(), "A1": bk(0.59, 0.67), "A2": bk(0.40, 0.41)}, pair_no_unwind_asks_le1=True)
b.alloc_set_races = {"Alpha Senate": {"cost": 0.1, "until": time.monotonic() + 900, "sets_before": 800, "free": 0,
                                      "sets": 100}}
fv = {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}
r = quiet(b.arb_plan, b.groups["Alpha Senate"], dict(b.api.inv), fv, False)
check("an allocator B3 registration (cost 0.10) keeps its own cost: asks 1.08 fires (bids 0.99)",
      r is not None and r[0] == "unwind", r)
check("nono_unwind_gated: asks above 1 + max cost without a registration -> gated",
      b.nono_unwind_gated(b.groups["Alpha Senate"], 1.06) and not b.nono_unwind_gated(b.groups["Alpha Senate"], 1.04))

# ============================================================================================ py_compile 3.10
print("--- py_compile under Python 3.10")
py310 = shutil.which("python3.10")
if py310:
    out = subprocess.run([py310, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py")],
                         capture_output=True, text=True)
    check("python3.10 -m py_compile mm_bot.py", out.returncode == 0, out.stderr[-300:])
else:
    print("    (python3.10 not installed here: compiled with this interpreter instead)")
    out = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py")],
                         capture_output=True, text=True)
    check("py_compile mm_bot.py", out.returncode == 0, out.stderr[-300:])

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
