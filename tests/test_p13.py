"""
Offline tests for Package 13 part A (analysis/p12/SPEC_P13_AGGRESSIVE.md sections 1-3):
1 buckets_enabled: MM / VALUE / MOMENTUM bucket accounting, the classification (answer 2), the hourly rebalance, the
  VALUE sell-down lowest edge-held first, exempt from the value floor (answer 1).
2 aggressive_value: no reduce-only from the risk cap / the backstop below the tripwire; party / bloc caps do not block;
  the fraction caps replaced by aggr_max_market_usd / aggr_max_race_usd of collateral.
3 momentum_enabled: the sleeve's buys (depth, caps, cash gate, never the opposite side), the manual exit over
  mom_exit_hours, the kill on a -30% mark move and its latch, persistence across a restart.
Also: every setting validates with its range; flags off identical to the branch head before Package 13 A (663ede1)
on a grid (wire orders, quotes, alloc_plan, status keys); no duplicate orders across cycles; nothing crashes on empty
books, missing Polymarket references or unknown markets.

Run:  python tests/test_p13.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
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
BASE_REV = "663ede1"                              # the branch head before Package 13 A


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# A: Alpha  A1 favourite p 0.98 / A2 longshot p 0.02 (extreme: VALUE short)
# B: Beta   B1 p 0.88 / B2 p 0.12, B2 ask 0.15: short edge 3.5% -> MOMENTUM long (YES at 0.15)
# G: Gamma  G1 p 0.80 ask 0.70 (fav edge 14%: VALUE long) / G2 p 0.20 ask 0.32 (short edge 17.6%: VALUE short)
# D: Delta  D1 p 0.90 bid 0.89 / D2 p 0.10 ask 0.13: MOMENTUM, routed to D1's NO (1 - 0.89 = 0.11 < 0.13)
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate"}


def books_default():
    return {"A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08),
            "B1": bk(0.85, 0.87), "B2": bk(0.13, 0.15),
            "G1": bk(0.66, 0.70), "G2": bk(0.30, 0.32),
            "D1": bk(0.89, 0.91), "D2": bk(0.11, 0.13)}


def refs_default():
    return {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02,
            "Beta Senate|Republican": 0.88, "Beta Senate|Democratic": 0.12,
            "Gamma Senate|Republican": 0.80, "Gamma Senate|Democratic": 0.20,
            "Delta Senate|Republican": 0.90, "Delta Senate|Democratic": 0.10}


def mk_bot(inv=None, books=None, refs=None, cash=50000.0, live=True, races=None, extra_markets=(), equity=100000.0,
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
    c.take_enabled = False
    c.cash_gate_enabled = True
    c.cash_gate_reserve = 0.0
    c.reserved_cash_mode = "ignore"
    c.alloc_mm_reserve = 1000.0
    # (Package 13 C: momentum_enabled alone no longer buys - these tests exercise the sleeve's buys, so they force
    #  it on, which is exactly the old momentum_enabled behaviour)
    c.momentum_force = bool(cfg.get("momentum_enabled", False))
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update(inv or {})
    api.cash, api.equity = cash, equity
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
    """One quiet cycle with every Package 13 flag off (fair values, Polymarket, cash read), then a clean slate."""
    keep = {k: getattr(b.cfg, k) for k in ("buckets_enabled", "momentum_enabled", "aggressive_value")}
    for k in keep:
        setattr(b.cfg, k, False)
    quiet(b.cycle)
    b.drain_writes(5)
    for k, v in keep.items():
        setattr(b.cfg, k, v)
    quiet(b.cancel_everything)
    b.my_orders.clear()
    b.api.orders.clear()
    for x in b.ex.values():
        x.quote = None
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    fresh(b)
    read(b)


def fresh(b):
    now_m = time.monotonic()
    for x in b.ex.values():
        x.book = M.strip_own(b.api.full_book(x.eid), []) if x.eid in b.api.books else x.book
        x.verified = now_m


def tick(b, skip=(), at=None):
    time.sleep(0.002)
    fresh(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.p13_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def wire(api, eid=None):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in api.wire
            if eid is None or o["exchangeId"] == eid]


# ============================================================================================ settings
print("--- settings")
D = M.Config()
SPEC = {"buckets_enabled": (False, (False, True)), "bucket_mm_frac": (0.20, (0.0, 1.0)),
        "bucket_value_frac": (0.40, (0.0, 1.0)), "bucket_mom_frac": (0.40, (0.0, 1.0)),
        "bucket_band": (0.05, (0.0, 0.5)), "bucket_turnover_per_hour": (50000.0, (0.0, 500000.0)),
        "bucket_writes_frac": (0.6, (0.0, 1.0)), "value_extreme_p": (0.04, (0.0, 0.25)),
        "value_min_edge": (0.12, (0.0, 2.0)), "value_min_edge_fav": (0.08, (0.0, 2.0)),
        "mom_max_p": (0.25, (0.01, 0.5)), "aggressive_value": (False, (False, True)),
        "aggr_max_market_usd": (12000.0, (0.0, 100000.0)), "aggr_max_race_usd": (15000.0, (0.0, 200000.0)),
        "momentum_enabled": (False, (False, True)), "mom_min_depth": (200, (0, 100000)),
        "mom_headline": (False, (False, True)), "mom_max_markets": (40, (1, 300)),
        "momentum_exit": (False, (False, True)), "mom_exit_hours": (6.0, (0.25, 72.0)),
        "mom_exit_utc": ("", ("2026-10-01T00:00:00Z", "2026-11-07T00:00:00Z")), "mom_kill_frac": (0.75, (0.3, 0.99))}
for k, (dflt, rng) in SPEC.items():
    ok_d = getattr(D, k) == dflt and type(getattr(D, k)) is type(dflt)
    ok_r = M.OVERRIDABLE.get(k) == rng
    if k == "mom_exit_utc":
        g1, b1 = M.validate_overrides({k: "2026-10-20T12:00:00Z"}, D)
        g2, b2 = M.validate_overrides({k: ""}, D)
        g3, b3 = M.validate_overrides({k: "2026-11-08T00:00:00Z"}, D)
        g4, b4 = M.validate_overrides({k: "2026-10-20 12:00"}, D)
        ok_v = g1 == {k: "2026-10-20T12:00:00Z"} and g2 == {k: ""} and not g3 and b3 and not g4 and b4
    elif isinstance(dflt, bool):
        g1, b1 = M.validate_overrides({k: True}, D)
        g2, b2 = M.validate_overrides({k: 1}, D)
        ok_v = g1 == {k: True} and not b1 and not g2 and b2
    else:
        lo, hi = rng
        step = 1 if isinstance(dflt, int) else 1e-6
        g1, b1 = M.validate_overrides({k: dflt}, D)
        g2, b2 = M.validate_overrides({k: lo - step}, D)
        g3, b3 = M.validate_overrides({k: hi + step}, D)
        g4, b4 = M.validate_overrides({k: hi}, D)
        ok_v = g1 == {k: dflt} and not g2 and b2 and not g3 and b3 and g4 == {k: hi}
        if isinstance(dflt, int):
            g5, b5 = M.validate_overrides({k: float(dflt) + 0.5}, D)
            ok_v = ok_v and not g5 and b5
    check(f"{k}: default {dflt!r}, range {rng}, validates in range / refuses outside", ok_d and ok_r and ok_v,
          (getattr(D, k), M.OVERRIDABLE.get(k)))
check("mom_exit_utc is a DATE setting that takes '' (none)", "mom_exit_utc" in M.DATE_SETTINGS
      and "mom_exit_utc" in M.DATE_EMPTY_OK)
_f = list(M.Config.__dataclass_fields__)
check("one contiguous block after mm_risk_reserve_corr in Config",
      _f[_f.index("mm_risk_reserve_corr") + 1:][:len(SPEC)] == list(SPEC),
      _f[_f.index("mm_risk_reserve_corr") + 1:][:5])
check("worst_case_backstop_frac keeps its range (0.3, 1.5): 1.0 (the tripwire) is valid",
      M.OVERRIDABLE["worst_case_backstop_frac"] == (0.3, 1.5)
      and M.validate_overrides({"worst_case_backstop_frac": 1.0}, D)[0] == {"worst_case_backstop_frac": 1.0})
check("wire_order strips the Package 13 notes",
      M.wire_order({"exchangeId": "x", "side": "yes", "action": "sell", "price": 0.5, "quantity": 1,
                    "_bucket_sell": True, "_mom": True, "_mom_exit": True}) ==
      {"exchangeId": "x", "side": "yes", "action": "sell", "price": 0.5, "quantity": 1})

# ============================================================================================ flags off = base
print(f"--- flags off identical to the branch head before Package 13 A ({BASE_REV}) on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_base13.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_base13", path)
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
        setattr(c, k, getattr(bn.cfg, k))
    api_b = FakeApi(True)
    api_b.markets_list = list(api_n.markets_list)
    api_b.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                   for e, v in api_n.books.items()}
    api_b.inv, api_b.cash, api_b.equity = dict(api_n.inv), cash, api_n.equity
    api_b.pnl = lambda: (api_b.log("pnl"), {"totalAccountValue": api_b.equity, "cashBalance": api_b.cash})[1]
    c.status_file = bn.cfg.status_file + ".base"
    bb_ = base.Bot(api_b, c)
    bb_.t["endDate"] = bn.t["endDate"]
    for x in bb_.ex.values():
        x.close = CLOSE
    bb_.refs = FakeRefs(dict(bn.refs.prices))
    return api_n, bn, api_b, bb_


if base is not None:
    ok_w, ok_q, ok_s, ok_p, ok_h, diffs = True, True, True, True, True, []
    grid = [({}, 50000.0, None, {}),
            ({"A2": -3000, "G2": -10000, "B2": 2000}, 20000.0, None, {"value_mode": True}),
            ({"21": 60000, "22": -5000}, 5000.0, None, {"alloc_enabled": True, "alloc_min_edge_buy": 0.05}),
            ({"D1": -2000, "11": 900}, 0.0, None, {"bloc_delta_enabled": True, "max_bloc_delta_frac": 0.01,
                                                   "worst_case_backstop_frac": 1.0}),
            ({"G1": 5000, "B1": 4000}, 50000.0, {**books_default(), "B2": bk(0.13, None)},
             {"value_mode": True, "value_quote_hurdle": 0.05, "take_enabled": True})]
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
        ign = set(getattr(M.Bot, "EV_KEYS", ())) | {"ev_outcome_history", "updated", "seconds_since_cycle",
                                                     "last_cycle_seconds", "last_cycle_phases", "requests_last_min",
                                                     "alloc", "tilt_state"}   # (wall-time stamps; keys compared)
        if set(kn) != set(kb) or set(kn.get("alloc") or {}) != set(kb.get("alloc") or {}):
            ok_s = False
            diffs.append(("status keys", set(kn) ^ set(kb)))
        vn = {k: v for k, v in kn.items() if k not in ign}
        vb = {k: v for k, v in kb.items() if k not in ign}
        if json.dumps(vn, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
            ok_h = False
            diffs.append(("status values", [k for k in vn if vn.get(k) != vb.get(k)]))
    check("two full cycles send the same orders, any order (5 books / positions / settings)", ok_w, diffs[:1])
    check("every market's quote identical", ok_q, diffs[:1])
    check("alloc_plan identical", ok_p, [d for d in diffs if d[0] == "plan"][:1])
    check("status.json keys identical (no buckets / aggressive / momentum key while off)", ok_s,
          [d for d in diffs if d[0] == "status keys"][:1])
    check("status.json values identical (but timings and the ev / carry report keys)", ok_h,
          [d for d in diffs if d[0] == "status values"][:1])
check("Bot.P13_KEYS names the keys Package 13 A adds", tuple(M.Bot.P13_KEYS) == ("buckets", "aggressive", "momentum"))

# ============================================================================================ classification
print("--- section 1: classification (answer 2)")
api, b = mk_bot()
warm(b)
cls = quiet(b.p13_classify, dict(api.inv), time.monotonic())
bk_of = {e: (c["bucket"], c["dir"]) for e, c in cls.items()}
check("A2 (p 0.02 <= value_extreme_p 0.04): VALUE short whatever its gap", bk_of.get("A2") == ("value", "short"), bk_of)
check("G2 (p 0.20, short edge (0.32 - 0.20) / 0.68 = 17.6% >= 12%): VALUE short", bk_of.get("G2") == ("value", "short"))
check("B2 / D2 / Ohio Rep (p 0.10-0.12, gaps 3.4-7.3% < 12%): MOMENTUM longs",
      all(bk_of.get(e) == ("momentum", "long") for e in ("B2", "D2", "11")), bk_of)
check("G1 (favourite p 0.80, (0.80 - 0.70) / 0.70 = 14% >= 8%): VALUE long", bk_of.get("G1") == ("value", "long"))
check("A1 / B1 / D1 / Ohio Dem (favourites under 8%): no bucket; Utah (0.45-0.55): MM",
      all(bk_of.get(e)[0] == "none" for e in ("A1", "B1", "D1", "12")) and bk_of.get("21") == ("mm", None)
      and bk_of.get("22") == ("mm", None), bk_of)
cls2 = quiet(b.p13_classify, {"B2": 500.0}, time.monotonic())
check("a momentum candidate holding a position outside the sleeve is VALUE (one bucket per market)",
      (cls2["B2"]["bucket"], cls2["B2"]["dir"]) == ("value", "long"), cls2["B2"])
b.mom_legs = {"G2": {"q": 100.0, "cost": 32.0}}
cls3 = quiet(b.p13_classify, {"G2": 100.0}, time.monotonic())
check("a sleeve leg is MOMENTUM whatever its numbers",
      (cls3["G2"]["bucket"], cls3["G2"]["dir"]) == ("momentum", "long"))
b.mom_legs = {}
check("counts by bucket (status classification)", b.p13_count(cls) ==
      {"mm": 2, "momentum_long": 3, "none": 4, "value_long": 1, "value_short": 2}, b.p13_count(cls))
api, b = mk_bot(refs={})
warm(b)
b.cur_refs, b.cur_liquid = {}, set()
check("no Polymarket references: nothing classified (no crash)", quiet(b.p13_classify, {}, time.monotonic()) == {})

# ============================================================================================ buckets
print("--- section 1: bucket accounting and the hourly sell-down")
INV_V = {"A2": -3000, "G2": -10000, "21": 4000}
api, b = mk_bot(inv=INV_V, equity=20000.0, cash=6000.0, buckets_enabled=True, alloc_mm_reserve=5000.0,
                value_mode=True)
warm(b)
b.last_equity = 20000.0
mm, value, mom, cash = quiet(b.bucket_values, dict(api.inv), time.monotonic())
check("VALUE = A2 short 3000 x 0.98 + G2 short 10000 x 0.80 = 10,940 (tail positions at p)", abs(value - 10940) < 1e-6,
      value)
check("MM = the reserve 5000 (cash 6000 >= it) + Utah Rep 4000 x 0.55 = 7,200 (middle band at p)",
      abs(mm - 7200) < 1e-6, mm)
check("MOMENTUM = 0 (no sleeve); cash = the gate's 6000", mom == 0 and abs(cash - 6000) < 1e-6, (mom, cash))
n0 = len(api.wire)
tick(b)
st = b.bk_info
check("status buckets: mm / value / momentum / cash / targets / deviations / last_rebalance / actions / classification",
      all(k in st for k in ("mm", "value", "momentum", "cash", "targets", "deviations", "last_rebalance", "actions",
                            "classification")), sorted(st))
sold = wire(api)[n0:]
check("VALUE 54.7% > 45%: sold down, the LOWEST edge-held first: A2 (edge 6.5%) bought back, G2 (17.6%) untouched",
      sold and sold[0][0] == "A2" and all(x[0] != "G2" for x in sold), sold)
check("...as a covered sale at the best ask (sell NO @ 1 - 0.08 = 0.92), immediate-or-cancel",
      sold and sold[0][1:4] == ("no", "sell", 0.92), sold)
check("...sized to bring VALUE back to 45%: 1,940 at p / 0.98 = 1,979 shares (within depth 2000)",
      sold and sold[0][4] == 1979, sold)
check("...EXEMPT from the value floor (bought back at 0.08 > p 0.02 + margin) and tagged bucket in the notes",
      any(m.get("bucket") and m.get("bucket_sell") for m in b.order_meta.values()))
check("...the leftover cancelled at once (nothing of it rests)", not [o for o in api.orders.values()
                                                                      if o["exchangeId"] == "A2"])
check("actions: 1 sell order, proceeds $1,820.68 (the NO sold at 0.92)", b.bk_run.get("sell_orders") == 1
      and abs(b.bk_run.get("sold", 0) - 1979 * 0.92) < 0.01, b.bk_run)
check("deviations after: VALUE back to 0.45 (+0.05)", abs(b.bk_info["deviations"]["value"] - 0.05) < 0.002,
      b.bk_info["deviations"])
n1 = len(api.wire)
tick(b)
check("next cycle within the hour: no new rebalance, nothing more sold", len(api.wire) == n1)
untagged = {"exchangeId": "A2", "side": "yes", "action": "buy", "quantity": 10, "price": 0.08}
check("the floor path itself: an untagged buy-back above p + margin is refused (value_mode)",
      b.p13_floor_blocks(dict(untagged), 0.02, -100) is True)
check("...the same order tagged _bucket_sell / _mom_exit is exempt",
      not b.p13_floor_blocks({**untagged, "_bucket_sell": True}, 0.02, -100)
      and not b.p13_floor_blocks({**untagged, "_mom_exit": True}, 0.02, -100))
check("...a buy that ADDS (no short held) is never a floor matter", not b.p13_floor_blocks(dict(untagged), 0.02, 0))
check("...value_mode off: no floor", not mk_bot()[1].p13_floor_blocks(dict(untagged), 0.02, -100))
# bigger excess: A2 then G2, turnover cap
api, b = mk_bot(inv=INV_V, equity=12000.0, cash=6000.0, buckets_enabled=True, alloc_mm_reserve=5000.0)
warm(b)
b.last_equity = 12000.0
plan = quiet(b.bucket_sell_plan, dict(api.inv), time.monotonic(), 6000.0)
check("sell plan for $6,000: A2 whole (2,940), then G2 (3,060) - lowest edge-held first",
      [(x["eid"], round(x["usd_left"])) for x in plan] == [("A2", 2940), ("G2", 3060)], plan)
b.cfg.bucket_turnover_per_hour = 100.0
tick(b)
sold = [x for x in wire(api) if x[0] in ("A2", "G2")]
check("bucket_turnover_per_hour 100: the run's budget is $100 (102 shares of A2 at p 0.98; proceeds $93.84)",
      sold == [("A2", "no", "sell", 0.92, 102)] and b.bucket_turnover(time.time()) <= 100, sold)
tick(b)
check("...and the hour's turnover is spent: nothing more until it rolls off", len([x for x in wire(api)
                                                                                   if x[0] in ("A2", "G2")]) == 1)
b.cfg.alloc_pin = "Dem Gamma Senate"
plan = quiet(b.bucket_sell_plan, dict(api.inv), time.monotonic(), 20000.0)
check("a pinned label (alloc_pin) is never sold", all(x["eid"] != "G2" for x in plan), plan)

# ============================================================================================ momentum buys
print("--- section 3: momentum buys")
api, b = mk_bot(momentum_enabled=True, alloc_mm_reserve=1000.0)
warm(b)
n0 = len(api.wire)
tick(b)
bought = wire(api)[n0:]
check("buys the MOMENTUM longs, smallest gap first: B2 YES @ 0.15, D1 NO (sell YES @ 0.89), Ohio Rep YES @ 0.18",
      [x[:4] for x in bought] == [("B2", "yes", "buy", 0.15), ("D1", "yes", "sell", 0.89), ("11", "yes", "buy", 0.18)],
      bought)
check("...the largest size the level allows (2000 / 2000 / 1000)", [x[4] for x in bought] == [2000, 2000, 1000])
check("never a VALUE market (A2 / G2 / G1) nor the MM band", all(x[0] not in ("A2", "G2", "G1", "21", "22")
                                                                 for x in bought))
check("sleeve legs and cost basis: B2 +2000 ($300), D1 -2000 ($220), Ohio +1000 ($180)",
      {e: (v["q"], round(v["cost"], 2)) for e, v in b.mom_legs.items()} ==
      {"B2": (2000.0, 300.0), "D1": (-2000.0, 220.0), "11": (1000.0, 180.0)}, b.mom_legs)
st = b.mom_status()
check("status momentum {state, cost, value_mark, value_outcome, markets, legs, kill_level, exit_progress}",
      all(k in st for k in ("state", "cost", "value_mark", "value_outcome", "markets", "legs", "kill_level",
                            "exit_progress")) and st["state"] == "active" and st["cost"] == 700.0
      and st["markets"] == 3 and st["kill_level"] == 525.0, st)
check("...value_outcome at p: 2000 x 0.12 + 2000 x 0.10 + 1000 x 0.12 = 560", abs(st["value_outcome"] - 560) < 1e-6,
      st["value_outcome"])
check("notes tagged momentum (their own fill class)", sum(1 for m in b.order_meta.values() if m.get("momentum")) == 3)
check("the sleeve's markets are not quoted by the market maker (decide: no quote)",
      all(quiet(b.decide, b.ex[e], 0.12, dict(api.inv), b.effective_inventory(dict(api.inv)), False, 0.0,
                time.monotonic()) == M.NO_QUOTE for e in ("B2", "D1", "11")))
check("...and its shares are out of the race netting (effective_inventory)",
      abs(b.effective_inventory(dict(api.inv)).get("B1", 0.0)) < 1e-9)
check("...the allocator / bucket sell-down leave them alone (alloc_market_ok false)", not b.alloc_market_ok(b.ex["B2"]))
quiet(b.log_fills, {})
mc = b.mm_carry(time.time())
check("mm_carry_24h: the sleeve's fills in their own class 'momentum' (not takes)",
      mc["fills"].get("momentum") == 3 and mc["fills"]["take"] == 0, mc["fills"])
api0, b0 = mk_bot()
check("...the class key is absent while no such fill exists (counts unchanged)", "momentum" not in
      b0.mm_carry(time.time())["fills"])
# depth
api, b = mk_bot(momentum_enabled=True, books={**books_default(), "B2": bk(0.13, 0.15, q=150)})
warm(b)
n0 = len(api.wire)
tick(b)
check("a level of 150 shares (< mom_min_depth 200): not bought", all(x[0] != "B2" for x in wire(api)[n0:]))
# caps: market cap
api, b = mk_bot(momentum_enabled=True, aggr_max_market_usd=150.0)
warm(b)
n0 = len(api.wire)
tick(b)
got = {x[0]: x[4] for x in wire(api)[n0:]}
check("aggr_max_market_usd 150: B2 1000 shares ($150 at 0.15), Ohio 833 ($150 at 0.18)",
      got.get("B2") == 1000 and got.get("11") == 833, got)
api, b = mk_bot(inv={"B1": -200}, momentum_enabled=True, aggr_max_race_usd=50.0)
warm(b)
n0 = len(api.wire)
tick(b)
got = {x[0]: x[4] for x in wire(api)[n0:]}
check("race cap: Beta already holds B1 NO 200 x 0.12 = $24 -> B2 only $26 more (173 shares)", got.get("B2") == 173, got)
# cash gate
api, b = mk_bot(momentum_enabled=True, cash=1100.0, alloc_mm_reserve=1000.0)
warm(b)
n0 = len(api.wire)
tick(b)
got = wire(api)[n0:]
check("cash 1,100 with reserve 1,000: only $100 spent (B2 666 shares at 0.15), then nothing",
      got == [("B2", "yes", "buy", 0.15, 666)], got)
api, b = mk_bot(momentum_enabled=True, cash=900.0, alloc_mm_reserve=1000.0)
warm(b)
n0 = len(api.wire)
tick(b)
check("cash below the MM reserve: nothing bought", len(api.wire) == n0)
api, b = mk_bot(momentum_enabled=True, alloc_mm_reserve=0.0)
warm(b)
read(b, cash=30.0)
n0 = len(api.wire)
tick(b)
got = wire(api)[n0:]
check("the cash gate's own read decides (cash left $30: B2 200 shares, nothing beyond)",
      got == [("B2", "yes", "buy", 0.15, 200)], got)
# opposite side
api, b = mk_bot(inv={"B1": 500, "11": -300}, momentum_enabled=True)
warm(b)
n0 = len(api.wire)
tick(b)
got = [x[0] for x in wire(api)[n0:]]
check("never the opposite side: long the Beta favourite (= short B2) -> no B2; short Ohio Rep -> no Ohio buy",
      "B2" not in got and "11" not in got and "D1" in got, got)
check("...the value short on Ohio Rep is not touched (never closed by a momentum buy)", api.inv.get("11") == -300)
# headline / max markets
api, b = mk_bot(momentum_enabled=True, mom_max_markets=1)
warm(b)
n0 = len(api.wire)
tick(b)
check("mom_max_markets 1: one market only", len({x[0] for x in wire(api)[n0:]}) == 1, wire(api)[n0:])
HL = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate", "H": "U.S. Senate"}
api, b = mk_bot(momentum_enabled=True, races=HL, books={**books_default(), "H1": bk(0.86, 0.88), "H2": bk(0.12, 0.13)},
                refs={**refs_default(), "U.S. Senate|Republican": 0.88, "U.S. Senate|Democratic": 0.12})
warm(b)
n0 = len(api.wire)
tick(b)
check("headline race (U.S. Senate, gap 1c) never bought while mom_headline is False",
      all(x[0] not in ("H1", "H2") for x in wire(api)[n0:]), wire(api)[n0:])
api, b = mk_bot(momentum_enabled=True, races=HL, books={**books_default(), "H1": bk(0.86, 0.88), "H2": bk(0.12, 0.13)},
                refs={**refs_default(), "U.S. Senate|Republican": 0.88, "U.S. Senate|Democratic": 0.12},
                mom_headline=True)
warm(b)
n0 = len(api.wire)
tick(b)
check("...with mom_headline True it is (smallest gap first)", wire(api)[n0:] and wire(api)[n0][0] == "H2",
      wire(api)[n0:])
# target
api, b = mk_bot(momentum_enabled=True, bucket_mom_frac=0.004)
warm(b)
b.last_equity = 100000.0
n0 = len(api.wire)
tick(b)
check("the target (bucket_mom_frac 0.4% of 100k = $400): B2 $300, then D1 $100 (909 NO)",
      [(x[0], x[4]) for x in wire(api)[n0:]] == [("B2", 2000), ("D1", 909)], wire(api)[n0:])
n1 = len(api.wire)
tick(b)
check("...target reached: no more buys next cycle", len(api.wire) == n1)
api, b = mk_bot(momentum_enabled=True, aggressive_value=True, worst_case_backstop_frac=0.3,
                inv={"21": 60000})
warm(b)
b.global_reduce = True
n0 = len(api.wire)
tick(b)
check("the tripwire / reduce-only in force: no momentum buy",
      all(x[0] not in ("B2", "D1", "11") for x in wire(api)[n0:]))
# dry run
api, b = mk_bot(momentum_enabled=True, buckets_enabled=True, live=False, inv=INV_V)
warm(b)
b.last_equity = 20000.0
n0 = len(api.wire)
tick(b)
check("dry run: planned and logged, nothing sent, no leg booked", len(api.wire) == n0 and not b.mom_legs)

# ============================================================================================ duplicates
print("--- no duplicate orders across cycles")
api, b = mk_bot(momentum_enabled=True)
warm(b)
api.books["B2"]["asks"] = [lvl(0.15, 2000), lvl(0.16, 2000)]
api.books["D1"]["bids"], api.books["11"]["asks"] = [], []
tick(b)
b.ex["B2"].pending_until = time.monotonic() + 60
n0 = len(api.wire)
tick(b)
check("a write in flight on B2 (busy): no second order there", all(x[0] != "B2" for x in wire(api)[n0:]))
b.ex["B2"].pending_until = 0.0
b.my_orders[777] = M.Resting(777, "B2", True, 0.15, 50, M.utcnow() + timedelta(seconds=30))
b.order_meta[777] = {"momentum": True, "take": True, "t": time.time()}
n0 = len(api.wire)
tick(b)
check("a momentum IOC still listed as resting on B2 (leftover cancel unconfirmed): not re-sent",
      all(x[0] != "B2" for x in wire(api)[n0:]))
b.my_orders.pop(777)
n0 = len(api.wire)
tick(b)
check("...once it is gone, the next level (0.16) is bought: one order", [x[:4] for x in wire(api)[n0:]]
      == [("B2", "yes", "buy", 0.16)], wire(api)[n0:])
api, b = mk_bot(momentum_enabled=True, buckets_enabled=True)
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
per = {}
for o in api.wire:
    if (o["exchangeId"], o["price"]) in (("B2", 0.15), ("D1", 0.89), ("11", 0.18)) and o["quantity"] >= 1000:
        per[o["exchangeId"]] = per.get(o["exchangeId"], 0) + 1
check("three full cycles (live, buckets + momentum on): each momentum buy sent exactly once", per ==
      {"B2": 1, "D1": 1, "11": 1}, (per, wire(api)[-6:]))
check("...and nothing of ours rests on the sleeve's markets after them (the market maker leaves them alone)",
      not [o for o in api.orders.values() if o["exchangeId"] in ("B2", "D1", "11")], api.orders)
check("...status.json has buckets and momentum", (lambda d: "buckets" in d and "momentum" in d)(
    (b.write_status(True), json.load(open(b.cfg.status_file)))[1]))

# ============================================================================================ manual exit
print("--- section 3: the manual exit (momentum_exit) over mom_exit_hours")
api, b = mk_bot(momentum_enabled=True, value_mode=True)
warm(b)
tick(b)
check("sleeve built: B2 +2000, D1 -2000, Ohio Rep +1000", {e: v["q"] for e, v in b.mom_legs.items()} ==
      {"B2": 2000.0, "D1": -2000.0, "11": 1000.0}, b.mom_legs)
api.books["B2"]["bids"] = [lvl(0.10, 5000)]          # (below p 0.12 - margin: the value floor would refuse it)
api.books["D1"]["asks"] = [lvl(0.91, 5000)]
api.books["11"]["bids"] = [lvl(0.10, 5000)]
b.cfg.momentum_exit = True
T0 = M.utcnow()
n0 = len(api.wire)
tick(b, at=T0)
check("momentum_exit True: state exiting, nothing sold at its start (paced from 0)", b.mom_state == "exiting"
      and len(api.wire) == n0, (b.mom_state, wire(api)[n0:]))
tick(b, at=T0 + timedelta(hours=1))
got = sorted(wire(api)[n0:])
check("an hour in (1/6 of 6 h): 333 / 333 / 166 sold into the bids, IOC",
      got == sorted([("B2", "yes", "sell", 0.1, 333), ("D1", "no", "sell", 0.09, 333),
                     ("11", "yes", "sell", 0.1, 166)]),
      got)
check("...below the value floor and still sent (_mom_exit: momentum positions are exempt)",
      any(x[0] == "B2" and x[3] == 0.1 for x in got))
check("...the short leg bought back as a covered sale (sell NO @ 0.09)", ("D1", "no", "sell", 0.09, 333) in got)
check("...cost basis shrinks in proportion (B2 300 -> 250.05)",
      abs(b.mom_legs["B2"]["cost"] - 300 * 1667 / 2000) < 1e-6,
      b.mom_legs["B2"])
check("exit_progress in status (started, hours 6, shares left)", (b.mom_status()["exit_progress"] or {}).get("hours")
      == 6.0 and b.mom_status()["exit_progress"]["shares_left"] == 5000 - 832, b.mom_status()["exit_progress"])
n1 = len(api.wire)
tick(b, at=T0 + timedelta(hours=1, minutes=1))
check("a minute later: only what the schedule adds (B2 / D1 5 shares, Ohio 2), never a burst",
      sum(x[4] for x in wire(api)[n1:]) <= 15, wire(api)[n1:])
tick(b, at=T0 + timedelta(hours=6, seconds=1))
check("after 6 h: everything sold, the sleeve empty, state exited", not b.mom_legs and b.mom_state == "exited"
      and not api.inv.get("B2") and not api.inv.get("D1") and not api.inv.get("11"), (b.mom_legs, api.inv))
b.cfg.momentum_exit = False
api.books["B2"]["asks"] = [lvl(0.15, 2000)]
n2 = len(api.wire)
read(b)
tick(b, at=T0 + timedelta(hours=7))
check("momentum_exit back to false: the sleeve is NEVER re-bought (state exited)", b.mom_state == "exited"
      and len(api.wire) == n2)
b.cfg.momentum_enabled = False
tick(b, at=T0 + timedelta(hours=7, minutes=1))
b.cfg.momentum_enabled = True
tick(b, at=T0 + timedelta(hours=7, minutes=2))
check("momentum_enabled false then true: a new sleeve (state active, buys again)", b.mom_state == "active"
      and any(x[0] == "B2" for x in wire(api)[n2:]), (b.mom_state, wire(api)[n2:]))
api, b = mk_bot(momentum_enabled=True, mom_exit_utc="2026-10-02T00:00:00Z")
warm(b)
tick(b)
check("mom_exit_utc in the past: no sleeve is ever bought (exited at once)", not b.mom_legs
      and b.mom_state in ("exiting", "exited"), b.mom_state)
b.cfg.mom_exit_utc = "2026-11-06T00:00:00Z"
check("mom_exit_time parses a valid date; an out-of-range one is ignored",
      b.mom_exit_time() is not None and (setattr(b.cfg, "mom_exit_utc", "2027-01-01T00:00:00Z") or b.mom_exit_time())
      is None)

# ============================================================================================ kill
print("--- section 3: the kill switch (a -30% mark move) and its latch")
api, b = mk_bot(momentum_enabled=True)
warm(b)
tick(b)
b.cfg.bucket_mom_frac = 0.007                       # (the target = the $700 built: no more buys in this test)
cost = sum(v["cost"] for v in b.mom_legs.values())
for e, bb, aa in (("B2", 0.10, 0.11), ("D1", 0.92, 0.925), ("11", 0.12, 0.13)):
    api.books[e] = bk(bb, aa, q=5000)
fresh(b)
mark = sum(b.mom_mark(e, v) for e, v in b.mom_legs.items())
check("the mark after the move: 490 = 70% of the cost 700 (-30%)", abs(mark - 0.7 * cost) < 0.01 and cost == 700.0,
      (mark, cost))
T1 = M.utcnow()
ALERTS.clear()
n0 = len(api.wire)
tick(b, at=T1)
check("below 0.75 x cost: the 120 s clock starts, still active, nothing sold", b.mom_state == "active"
      and b.mom_below_since is not None and len(api.wire) == n0)
tick(b, at=T1 + timedelta(seconds=60))
check("60 s later: still active", b.mom_state == "active")
tick(b, at=T1 + timedelta(seconds=121))
check("121 s below: KILLED (exit started, latched)", b.mom_state == "killed" and b.mom_exit_wall is not None)
check("...one alert", len([a for a in ALERTS if "KILLED" in a]) == 1, ALERTS)
tick(b, at=T1 + timedelta(seconds=121 + 3600))
check("...the exit sells over mom_exit_hours (1/6 after an hour)", sorted(x[0] for x in wire(api)[n0:])
      == ["11", "B2", "D1"] and b.mom_legs["B2"]["q"] == 2000 - 333, (wire(api)[n0:], b.mom_legs))
tick(b, at=T1 + timedelta(seconds=121 + 7200))
check("...no second alert", len([a for a in ALERTS if "KILLED" in a]) == 1)
api.books["B2"]["asks"] = [lvl(0.15, 2000)]
n1 = len(api.wire)
tick(b, at=T1 + timedelta(seconds=121 + 7201))
check("killed with momentum_enabled still true: no buy (the latch), only the scheduled sales",
      all(x[2] != "buy" or x[1] == "no" for x in wire(api)[n1:]) and b.mom_state == "killed", wire(api)[n1:])
b.write_status(True)
with open(b.cfg.status_file) as f:
    saved = json.load(f).get("momentum") or {}
check("status momentum while killed: state killed, kill_level 525, legs and cost basis saved",
      saved.get("state") == "killed" and saved.get("kill_level") == round(0.75 * sum(v["cost"]
                                                                                    for v in b.mom_legs.values()), 2)
      and set(saved.get("leg_state") or {}) == set(b.mom_legs), saved)
b2 = quiet(M.Bot, api, b.cfg)
check("a restart restores the latch, the legs and their cost basis", b2.mom_state == "killed"
      and {e: (round(v["q"]), round(v["cost"], 2)) for e, v in b2.mom_legs.items()}
      == {e: (round(v["q"]), round(v["cost"], 2)) for e, v in b.mom_legs.items()}
      and b2.mom_exit_wall == b.mom_exit_wall)
b.cfg.momentum_enabled = False
tick(b, at=T1 + timedelta(seconds=121 + 7300))
b.cfg.momentum_enabled = True
tick(b, at=T1 + timedelta(seconds=121 + 7400))
check("reset only by momentum_enabled false then true: active again", b.mom_state == "active")
# the clock resets when the mark recovers
api, b = mk_bot(momentum_enabled=True)
warm(b)
tick(b)
b.cfg.bucket_mom_frac = 0.007
saved_books = {e: {"bids": [dict(x) for x in api.books[e]["bids"]], "asks": [dict(x) for x in api.books[e]["asks"]]}
               for e in ("B2", "D1", "11")}
for e, bb, aa in (("B2", 0.10, 0.11), ("D1", 0.92, 0.925), ("11", 0.12, 0.13)):
    api.books[e] = bk(bb, aa, q=5000)
T2 = M.utcnow()
tick(b, at=T2)
api.books.update(bk_ for bk_ in [("B2", bk(0.14, 0.16, q=5000)), ("D1", bk(0.88, 0.90, q=5000)),
                                 ("11", bk(0.17, 0.19, q=5000))])
tick(b, at=T2 + timedelta(seconds=60))
check("the mark recovers within 120 s: the clock resets", b.mom_below_since is None and b.mom_state == "active")
for e, bb, aa in (("B2", 0.10, 0.11), ("D1", 0.92, 0.925), ("11", 0.12, 0.13)):
    api.books[e] = bk(bb, aa, q=5000)
tick(b, at=T2 + timedelta(seconds=130))
check("...below again at 130 s: not killed at once (a fresh 120 s)", b.mom_state == "active")

# ============================================================================================ aggressive_value
print("--- section 2: aggressive_value")


def cyc(b, n=1):
    for _ in range(n):
        quiet(b.cycle)
        b.drain_writes(5)


api, b = mk_bot(inv={"21": 80000})
cyc(b)
check("off: the risk cap puts a 80,000-share Utah Rep long in reduce-only", b.health.get("reduce_only") is True
      and "aggressive" not in b.health, b.health.get("worst_case_loss"))
api, b = mk_bot(inv={"21": 80000}, aggressive_value=True, worst_case_backstop_frac=1.0)
cyc(b)
ag = b.health.get("aggressive") or {}
check("on (tripwire 1.0): no reduce-only from the risk cap", b.health.get("reduce_only") is False,
      b.health.get("reduce_only"))
check("...status aggressive {on, caps_ignored, would_reduce_corr True, would_reduce_backstop False}",
      ag.get("on") is True and ag.get("caps_ignored") and ag.get("would_reduce_corr") is True
      and ag.get("would_reduce_backstop") is False, ag)
api, b = mk_bot(inv={"21": 120000}, aggressive_value=True, worst_case_backstop_frac=0.5)
cyc(b)
check("the TRIPWIRE still forces reduce-only (worst case above worst_case_backstop_frac x account)",
      b.health.get("reduce_only") is True and (b.health.get("aggressive") or {}).get("would_reduce_backstop") is True,
      (b.health.get("worst_case_loss"), b.health.get("aggressive")))
api, b = mk_bot(inv={"21": 80000}, aggressive_value=True, worst_case_backstop_frac=1.0, backstop_soft_frac=0.3)
cyc(b)
check("...and the backstop's soft band does not shrink adds (factor 1)", b.backstop_adding_factor == 1.0)
# party / bloc
api, b = mk_bot(inv={"21": 80000})
b.cfg.max_party_delta_frac = 0.02
pb_off = b.party_blocks(b.ex["21"], 80000)
b.cfg.aggressive_value = True
pb_on = b.party_blocks(b.ex["21"], 80000)
check("party cap: off blocks the Rep bid (party delta 80k > cap 2k); aggressive never blocks",
      pb_off == (True, False) and pb_on == (False, False), (pb_off, pb_on))
b.cfg.bloc_delta_enabled = True
b.bloc_delta = 1e9
check("...nor the bloc-delta cap", b.party_blocks(b.ex["21"], 0) == (False, False))
b.cfg.aggressive_value = False
check("...which blocks with the flag off", b.party_blocks(b.ex["21"], 0) == (True, False))
# caps replaced
api, b = mk_bot(inv={"21": 4000})
cyc(b)
off_q = b.ex["21"].quote
api, b = mk_bot(inv={"21": 4000}, aggressive_value=True)
cyc(b)
on_q = b.ex["21"].quote
check("a 4,000-share long at the Kelly / position limit: off no more bid size, aggressive quotes the full 100",
      (off_q.bid_size or 0) < 100 and on_q.bid_size == 100, (off_q, on_q))
check("aggr_limits: 12,000 / p shares long (p 0.55: 21,818), 12,000 / (1 - p) short",
      [round(x) for x in b.aggr_limits(b.ex["21"], 0.55)] == [21818, 26667, 12000], b.aggr_limits(b.ex["21"], 0.55))
api, b = mk_bot(inv={"21": 21800}, aggressive_value=True, value_mode=True)   # (px = the liquid p 0.55)
cyc(b)
check("near the market cap (21,800 of 21,818 shares): the bid shrinks to what is left (<= 18)",
      (b.ex["21"].quote.bid_size or 0) <= 18, b.ex["21"].quote)
api, b = mk_bot(inv={"21": 7000, "22": -20000}, aggressive_value=True)
cyc(b)
cap21 = b.aggr_cap_usd(b.ex["21"])
check("race cap: Utah Dem short 20,000 holds ~$11k -> Utah Rep may hold min(12k, 15k - that)",
      3500 < cap21 < 4500, cap21)
check("...and its bid stops there (7,000 shares x p already ~ at it: <= 100, shrinking)",
      (b.ex["21"].quote.bid_size or 0) <= 100)
check("alloc_room: the collateral room instead of alloc_max_contract_usd",
      abs(b.alloc_room(b.ex["21"], 7000, 0.55, dict(api.inv)) - b.p13_room(b.ex["21"], b.p13_coll_snapshot(
          dict(api.inv)), own_orders=True)) < 1e-6)
b.cfg.aggressive_value = False
check("...off: alloc_max_contract_usd less what is held (10,000 - 7,000 x 0.55)",
      abs(b.alloc_room(b.ex["21"], 7000, 0.55, dict(api.inv)) - (b.cfg.alloc_max_contract_usd - 3850)) < 1e-6)
b.cfg.aggressive_value = True
line = b.summary_ops_line(100000.0) or ""
check("every 2-hourly summary line leads with 'WARNING aggressive_value on'",
      line.startswith("WARNING aggressive_value on"),
      line[:80])
b.cfg.aggressive_value = False
check("...absent while off", "aggressive_value" not in (b.summary_ops_line(100000.0) or ""))
check("value_mode's floor stays with aggressive on (the reducing ask of a long never below p - margin)",
      True if not hasattr(M, "value_floor_quote") else
      M.value_floor_quote(M.Quote(ask=0.40, ask_size=10), 0.55, 100, b.cfg).ask >= 0.545)

# ============================================================================================ robustness
print("--- nothing crashes: empty books, no Polymarket, unknown markets")
EMPTY = {e: {"bids": [], "asks": []} for e in books_default()}
api, b = mk_bot(books=EMPTY, momentum_enabled=True, buckets_enabled=True, aggressive_value=True,
                inv={"A2": -100, "ZZ": 50})
cyc(b, 2)
check("empty books, an unknown market held, every flag on: two cycles, no momentum order",
      b.failed_cycles == 0 and not [o for o in api.wire if o["exchangeId"] in books_default() and o["quantity"] >= 200],
      b.failed_cycles)
api, b = mk_bot(refs={}, momentum_enabled=True, buckets_enabled=True, aggressive_value=True)
b.refs = FakeRefs({})
cyc(b, 2)
check("no Polymarket references at all: cycles run, nothing classified, no Package 13 order",
      b.failed_cycles == 0 and not b.mom_legs and b.p13_counts == {}, b.p13_counts)
api, b = mk_bot(momentum_enabled=True)
b.p13_init({"state": "active", "leg_state": {"GONE": {"q": 500, "cost": 50.0}, "B2": {"q": "x", "cost": 1}}}, {})
check("restored legs: a malformed one dropped", set(b.mom_legs) == {"GONE"})
st = b.mom_status()
check("a leg on a market no longer listed: valued at its cost, labelled by id", st["legs"] == {"GONE": 500}
      and st["value_mark"] == 50.0)
warm(b)
tick(b)
check("...and dropped by the next reconcile (no position there)", "GONE" not in b.mom_legs)
t = quiet(b.p13_tick, M.utcnow(), {"NOPE": 10.0}, {}, None, set())
check("p13_tick with an unknown market in the positions: no crash", isinstance(t, set))

# ============================================================================================ py_compile 3.10
print("--- py_compile under Python 3.10")
import shutil                                                    # noqa: E402
py310 = shutil.which("python3.10")
exe = py310 or sys.executable
out = subprocess.run([exe, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py")], capture_output=True,
                     text=True)
check(f"{'python3.10' if py310 else 'this interpreter'} -m py_compile mm_bot.py", out.returncode == 0,
      out.stderr[-300:])

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
