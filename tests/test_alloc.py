"""
Offline tests for Package 10 B: the capital allocator with the market-making reserve (alloc_*; analysis/p10/SPEC_P10.md
Part B). Once an hour the bot pairs its lowest-edge holdings (edge per $ held to the OUTCOME, p = the liquid race-scaled
Polymarket price) with the highest-edge book levels: SELL first (immediate-or-cancel at the touch, only while the paired
buy level is still on a fresh book), READ the cash, THEN BUY (immediate-or-cancel at the touch, only if the level is
still there within 0.5c). Cash below alloc_mm_reserve is refilled first; NO+NO sets are unwound through the existing
short-set unwind (B3).
Covers: settings (defaults, ranges, the free-text pin list, one contiguous block), flags off identical to the branch head
(c3e32b6) on a grid (two full cycles: wire orders, quotes, status keys; arb_plan), the ranking math (long / short edge,
race scaling, own quotes stripped, stale / illiquid p skipped), pairing and thresholds, pin list, headline exclusion,
basket legs and markets traded this cycle, sell -> cash read -> buy across cycles, the level gone (before and after the
sale), the reserve (refill sales, buys only above it, spare cash), turnover / orders / writes / per-market caps, the bloc
check hook, IOC and leftover cancel with our own quotes pulled first, cash gate interplay, never a flip, B3 set unwind
registration and expiry, status.json / journal, switch-off, dry run, py_compile under Python 3.10.

Run:  python tests/test_alloc.py      (exit code 0 = all passed)
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
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

# retired on simplify: the old twin's status / health fields a removed feature owned, at the value it reported while
# the feature was off (the new code drops them); Quote lost its trailing `behind` flag (always False while that sizing
# was off) and the age skew its switch (skew_age_enabled: the old default True, live False)
RETIRED_STATUS = {"tilt_s_applied": (0.0, 0), "tilt_s_applied_headline": (0.0, 0), "fast_unload_windows": (0,),
                  "behind_best_markets": (0,), "mark_frag_total_cap_active": (False,),
                  "fl_bias_markets": ({"bid": 0, "ask": 0},)}
RETIRED_FLIPPED = {"skew_age_enabled": False}
NQ = len(M.Quote.__dataclass_fields__)


def retired(new, old):
    """The old twin's dict less a retired feature's own key at its OFF value when the new one dropped it."""
    return {k: v for k, v in old.items() if not (k in RETIRED_STATUS and k not in new and v in RETIRED_STATUS[k])}


logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "c3e32b6"                              # the branch head before Part B (Part A built)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# Race Alpha: A1 Rep (held long, p 0.60 at bid 0.60: edge-held 0), A2 Dem.
# Race Beta:  B1 Rep (ask 0.60 vs p 0.70: buy edge 16.7%), B2 Dem.
# Race Gamma: G1 Rep, G2 Dem (bid 0.45 vs p 0.30: short edge (0.45-0.30)/0.55 = 27%).
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate"}


def books_default():
    return {"A1": bk(0.60, 0.62), "A2": bk(0.37, 0.40),
            "B1": bk(0.55, 0.60), "B2": bk(0.38, 0.42),
            "G1": bk(0.62, 0.66), "G2": bk(0.26, 0.31)}


def refs_default():
    return {"Alpha Senate|Republican": 0.60, "Alpha Senate|Democratic": 0.40,
            "Beta Senate|Republican": 0.70, "Beta Senate|Democratic": 0.30,
            "Gamma Senate|Republican": 0.70, "Gamma Senate|Democratic": 0.30}


def alloc_bot(inv=None, books=None, refs=None, cash=1000.0, enabled=True, live=True, extra_markets=(), **cfg):
    """make_bot's Ohio / Utah (no edge anywhere) plus Alpha / Beta / Gamma, Polymarket liquid on every leg, the cash gate
    on, covered NO sales on (self-tests off), quoting as usual."""
    bks = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
           for e, v in (books or books_default()).items()}
    base = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
            "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
            "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
            "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    base.update(bks)
    mk = []
    for k, race in RACES.items():
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
    c.max_position_frac = 1.0                     # (the market maker's own limits never in the way of these books)
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update(inv if inv is not None else {"A1": 1000})
    api.cash = cash
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def quiet(fn, *a, **k):
    logging.disable(logging.CRITICAL)
    try:
        return fn(*a, **k)
    finally:
        logging.disable(logging.NOTSET)


def warm(b):
    """One full cycle with the allocator off (books, Polymarket, the cash read, quotes), then it is back as it was;
    our resting quotes are cancelled so the fake's books are other traders' only."""
    on = b.cfg.alloc_enabled
    b.cfg.alloc_enabled = False
    quiet(b.cycle)
    b.drain_writes(5)
    b.cfg.alloc_enabled = on
    quiet(b.cancel_everything)
    b.my_orders.clear()
    b.api.orders.clear()
    read(b)


def read(b, cash=None):
    """A cash read now: the gate's figure = the fake's cash (or `cash`), nothing reserved or spent since."""
    b.cg_cash = b.api.cash if cash is None else cash
    b.cg_reserved, b.cg_spent = 0.0, 0.0
    b.cg_read_at = time.monotonic()


def tick(b, inv=None, skip=(), now=None):
    time.sleep(0.002)                             # (monotonic order: a sale is after the read before it)
    return quiet(b.alloc_tick, now or M.utcnow(), dict(b.api.inv) if inv is None else inv,
                 b.orders_by_eid(M.utcnow()), None, set(skip))


def plan(b, cash=None, inv=None, skip=(), left=None):
    return quiet(b.alloc_plan, dict(b.api.inv) if inv is None else inv, time.monotonic(),
                 b.cash_left() if cash is None else cash, set(skip), left)


def batches(api):
    return [w for w in api.wire]


# ============================================================================================ settings
print("--- settings")
D = M.Config()
KEYS = ["alloc_enabled", "alloc_interval_s", "alloc_min_improvement", "alloc_min_edge_buy", "alloc_max_edge_sell",
        "alloc_pin", "alloc_max_turnover_per_hour", "alloc_max_orders_per_cycle", "alloc_writes_frac",
        "alloc_max_contract_usd", "alloc_mm_reserve", "alloc_set_cost_per_usd"]
check("defaults: off, hourly, 3% improvement, buy >= 5%, sell <= 2%, no pins",
      (D.alloc_enabled, D.alloc_interval_s, D.alloc_min_improvement, D.alloc_min_edge_buy, D.alloc_max_edge_sell,
       D.alloc_pin) == (False, 3600.0, 0.03, 0.05, 0.02, ""))
check("defaults: turnover 15k/h, 4 orders, 30% of writes, 10k a market, reserve 15k, sets off",
      (D.alloc_max_turnover_per_hour, D.alloc_max_orders_per_cycle, D.alloc_writes_frac, D.alloc_max_contract_usd,
       D.alloc_mm_reserve, D.alloc_set_cost_per_usd) == (15000.0, 4, 0.3, 10000.0, 15000.0, 0.0))
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after Part A's value_mid_inventory_quotes, in Config and OVERRIDABLE",
      _f[_f.index("value_mid_inventory_quotes") + 1:][:len(KEYS)] == KEYS
      and _ov[_ov.index("value_mid_inventory_quotes") + 1:][:len(KEYS)] == KEYS)
check("ranges as the spec", [M.OVERRIDABLE[k] for k in KEYS if k not in ("alloc_pin", "alloc_max_contract_usd")] == [
    (False, True), (300.0, 86400.0), (0.005, 0.5), (0.0, 0.5), (0.0, 0.5), (0.0, 200000.0), (1, 20),
    (0.0, 1.0), (0.0, 100000.0), (0.0, 0.2)])
good, bad = M.validate_overrides({k: getattr(D, k) for k in KEYS}, D)
check("every default inside its range (the file can restore it)", not bad and len(good) == len(KEYS), bad)
good, bad = M.validate_overrides({"alloc_pin": "Rep Alpha Senate, Dem Beta Senate"}, D)
check("alloc_pin takes free text (labels)", good == {"alloc_pin": "Rep Alpha Senate, Dem Beta Senate"} and not bad)
good, bad = M.validate_overrides({"alloc_pin": 3}, D)
check("alloc_pin: a number refused", not good and bad)
good, bad = M.validate_overrides({"alloc_pin": "x" * 4001}, D)
check("alloc_pin: over 4000 characters refused", not good and bad)
good, bad = M.validate_overrides({"alloc_interval_s": 60.0, "alloc_min_improvement": 0.0, "alloc_set_cost_per_usd": 0.3,
                                  "alloc_max_orders_per_cycle": 0, "alloc_writes_frac": 1.5,
                                  "alloc_mm_reserve": 200000.0, "alloc_max_turnover_per_hour": -1.0}, D)
check("out-of-range values refused (7 of 7)", not good and len(bad) == 7, bad)
good, bad = M.validate_overrides({"alloc_enabled": True, "alloc_interval_s": 300.0, "alloc_max_orders_per_cycle": 20,
                                  "alloc_set_cost_per_usd": 0.2}, D)
check("edge values accepted", len(good) == 4 and not bad, bad)
check("wire_order strips _alloc_paired (never sent)",
      M.wire_order({"exchangeId": "1", "action": "sell", "price": 0.5, "quantity": 3, "_alloc_paired": True})
      == {"exchangeId": "1", "action": "sell", "price": 0.5, "quantity": 3})
check("wire_order: a covered sale keeps its rewrite and drops the flag",
      M.wire_order({"exchangeId": "1", "action": "buy", "side": "yes", "price": 0.4, "quantity": 3, "_no_sell": True,
                    "_alloc_paired": True}) == {"exchangeId": "1", "action": "sell", "side": "no", "price": 0.6,
                                                "quantity": 3})

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
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)


def twin(inv, cash, **cfg):
    api_n, bn = alloc_bot(inv=inv, cash=cash, enabled=False, **cfg)
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


if base is not None:
    ok_w, ok_q, ok_s, ok_a, diffs = True, True, True, True, []
    grid = [({"A1": 1000}, 20000.0, {}), ({"A1": 1000, "G2": -500}, 0.0, {}),
            ({"A1": -800, "A2": -900}, 50000.0, {"pair_no_unwind_max_cost": 0.05}),
            ({"B1": 300, "G1": -200}, 5000.0, {"alloc_set_cost_per_usd": 0.1, "alloc_mm_reserve": 0.0})]
    for inv, cash, kw in grid:
        api_n, bn, api_b, bb_ = twin(inv, cash, **kw)
        for _ in range(2):
            quiet(bn.cycle)
            quiet(bb_.cycle)
            bn.drain_writes(5)
            bb_.drain_writes(5)
        sn = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_n.wire]
        sb = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_b.wire]
        if sn != sb:
            ok_w = False
            diffs.append(("wire", inv, sn[:2], sb[:2]))
        if {e: tuple(x.quote.__dict__.values())[:NQ] for e, x in bn.ex.items()} != \
                {e: tuple(x.quote.__dict__.values())[:NQ] for e, x in bb_.ex.items()}:
            ok_q = False
            diffs.append(("quote", inv))
        for race, members in bn.groups.items():
            fv = {e: M.fair_value(x.book, bn.cfg) for e, x in bn.ex.items()}
            if (bn.arb_plan(members, dict(api_n.inv), fv, False)
                    != base.Bot.arb_plan(bb_, members, dict(api_b.inv), fv, False)):
                ok_a = False
        bn.write_status(True)
        bb_.write_status(True)
        with open(bn.cfg.status_file) as f:
            kn = (set(json.load(f)) - set(getattr(M.Bot, "EV_KEYS", ())) - {"ev_outcome_history"}
                  - set(getattr(M.Bot, "MM_FUNDING_KEYS", ())))   # (less the later ev / carry / P14 report keys)
        with open(bb_.cfg.status_file) as f:
            kb = set(retired(kn, json.load(f)))
        if kn != kb or set(bn.health) != set(retired(bn.health, bb_.health)):
            ok_s = False
            diffs.append(("status", kn ^ kb))
    check("two full cycles send the same orders (4 books / cash / settings)", ok_w, diffs[:1])
    check("every market's quote identical", ok_q, diffs[:1])
    check("arb_plan identical with no allocator registration (also pair_no_unwind_max_cost 0.05)", ok_a)
    check("status.json / health keys identical (no 'alloc' key while never used)", ok_s, diffs[:1])
    api_n, bn, _, _ = twin({"A1": 1000}, 20000.0)
    check("flag off: alloc_tick never called, no state", not bn.alloc_pairs and bn.alloc_last_run_wall is None
          and bn.alloc_state == "off" and not bn.alloc_persist_needed())

# ============================================================================================ ranking math
print("--- ranking math")
api, b = alloc_bot()
warm(b)
ex = b.ex
check("p = the race-scaled Polymarket (Alpha Rep 0.60)", abs(b.alloc_p(ex["A1"]) - 0.60) < 1e-9)
pairs, info = plan(b, cash=1000.0)
check("one pair: sell Rep Alpha (edge-held 0%) -> buy Rep Beta (16.7%), then the Gamma short",
      len(pairs) >= 1 and pairs[0]["sell"]["eid"] == "A1" and pairs[0]["buy"]["eid"] in ("G2", "B1"),
      [(p_["sell"].get("eid"), p_["buy"] and p_["buy"]["eid"]) for p_ in pairs])
sell = pairs[0]["sell"]
check("long edge-held = (p - bid) / bid = 0", abs(sell["edge"]) < 1e-9 and sell["px"] == 0.60)
by_buy = {p_["buy"]["eid"]: p_["buy"] for p_ in pairs if p_["buy"]}
check("buy YES edge = (p - ask) / ask = (0.70 - 0.60) / 0.60",
      "B1" in by_buy and abs(by_buy["B1"]["edge"] - 0.1 / 0.6) < 1e-9 and not by_buy["B1"]["short"])
check("no level below p is shorted (Gamma Dem bid 0.26 < p 0.30)", "G2" not in by_buy)
check("highest edge first: Beta Rep is the first buy", pairs[0]["buy"]["eid"] == "B1")
check("pair sized to the sale's depth / position: $ 600 = 1000 x 0.60 at most",
      sum(p_["usd"] for p_ in pairs if p_["sell"].get("eid") == "A1") <= 600.0 + 1e-6)
check("ev_gain_est = $ x (edge buy - edge held)", abs(info["ev_gain_est"] - round(pairs[0]["usd"] * 0.1 / 0.6, 2)) < 0.02)
# short holding edge and short level edge
api, b = alloc_bot(inv={"G1": -1000}, books={**books_default(), "G1": bk(0.62, 0.70), "B2": bk(0.40, 0.42)},
                   refs={**refs_default(), "Beta Senate|Republican": 0.70, "Beta Senate|Democratic": 0.30})
warm(b)
pairs, _ = plan(b, cash=1000.0)
s = pairs[0]["sell"] if pairs else {}
check("held short edge = (ask - p) / (1 - ask) = (0.70 - 0.70) / 0.30 = 0", s.get("kind") == "short"
      and abs(s.get("edge", 9)) < 1e-9, s)
bb2 = [p_["buy"] for p_ in pairs if p_["buy"] and p_["buy"]["eid"] == "B2"]
check("short level edge = (bid - p) / (1 - bid) = (0.40 - 0.30) / 0.60", bb2 and bb2[0]["short"]
      and abs(bb2[0]["edge"] - 0.1 / 0.6) < 1e-9, bb2)
check("short sale qty in NO terms, $ = qty x (1 - ask)", abs(s["usd"] - s["qty"] * 0.30) < 1e-6)
# race scaling: raw Polymarket sums to 1.25 -> scaled
api, b = alloc_bot(refs={**refs_default(), "Beta Senate|Republican": 0.735, "Beta Senate|Democratic": 0.315})
warm(b)
check("race scaling: Beta Rep raw 0.735 / 1.05 = 0.70", abs(b.alloc_p(b.ex["B1"]) - 0.70) < 1e-9)
pairs, _ = plan(b, cash=1000.0)
check("...and the edge uses the scaled p (16.7%, not 22.5%)",
      any(p_["buy"] and p_["buy"]["eid"] == "B1" and abs(p_["buy"]["edge"] - 0.1 / 0.6) < 1e-9 for p_ in pairs))
# stale and illiquid p
api, b = alloc_bot()
warm(b)
b.refs.ages = lambda: {"Beta Senate|Republican": 31.0}
b.alloc_ages = b.refs.ages()
check("a Polymarket price older than 30 s is not ranked", b.alloc_p(b.ex["B1"]) is None
      and all(p_["buy"]["eid"] != "B1" for p_ in plan(b, cash=1000.0)[0] if p_["buy"]))
b.alloc_ages = {"Beta Senate|Republican": 29.0}
check("...29 s is", b.alloc_p(b.ex["B1"]) is not None)
b.alloc_ages = {}
b.cur_liquid = set(b.cur_liquid) - {"B1"}
check("an illiquid Polymarket price is not ranked", b.alloc_p(b.ex["B1"]) is None
      and all(p_["buy"]["eid"] != "B1" for p_ in plan(b, cash=1000.0)[0] if p_["buy"]))
# own quotes stripped: a resting bid of ours at the touch on Alpha is not the sale price
api, b = alloc_bot()
warm(b)
api.orders[999] = {"id": 999, "exchangeId": "A1", "side": "yes", "action": "buy", "priceLimit": 0.61, "quantity": 50,
                   "open": True, "expirationDate": M.iso(M.utcnow() + timedelta(hours=1))}
quiet(b.sync_orders, api.open_orders("T"), time.monotonic())
fresh = b.alloc_download(b.ex["A1"], b.orders_by_eid(M.utcnow()))
check("own quotes stripped from the fresh book (our 0.61 bid is not the touch)", fresh["bids"][0]["price"] == 0.60,
      fresh["bids"][:2])

# ============================================================================================ thresholds / exclusions
print("--- thresholds, pins, headline, skip")
api, b = alloc_bot()
warm(b)
b.cfg.alloc_max_edge_sell = -0.01
check("alloc_max_edge_sell: a holding with edge-held above it is never sold", not plan(b, cash=1000.0)[0])
b.cfg.alloc_max_edge_sell = 0.02
b.cfg.alloc_min_edge_buy = 0.20
check("alloc_min_edge_buy: no level below it is bought", not plan(b, cash=1000.0)[0])
b.cfg.alloc_min_edge_buy = 0.05
b.cfg.alloc_min_improvement = 0.17
check("alloc_min_improvement: 16.7% gain < 17% -> no pair", not plan(b, cash=1000.0)[0])
b.cfg.alloc_min_improvement = 0.16
check("...16% -> the pair", len(plan(b, cash=1000.0)[0]) >= 1)
b.cfg.alloc_min_improvement = 0.03
b.cfg.alloc_pin = "Dem Zeta Senate, Rep Alpha Senate"
check("pin list: Rep Alpha Senate never sold", not plan(b, cash=1000.0)[0])
b.cfg.alloc_pin = ""
check("skip: a market another feature traded this cycle is neither sold...", not plan(b, cash=1000.0, skip={"A1"})[0])
check("...nor bought (its race in skip)", all(p_["buy"]["eid"] != "B1"
                                              for p_ in plan(b, cash=1000.0, skip={"Beta Senate"})[0] if p_["buy"]))
b.cfg.headline_races = ("Beta Senate",)
check("headline markets excluded",
      all(p_["buy"]["eid"] != "B1" for p_ in plan(b, cash=1000.0)[0] if p_["buy"]))
b.cfg.headline_races = ("U.S. House", "U.S. Senate")
b.cfg.alloc_max_contract_usd = 100.0
pairs, _ = plan(b, cash=1000.0)
check("alloc_max_contract_usd: at most $100 a market (with what is held there)",
      sum(p_["usd"] for p_ in pairs if p_["buy"] and p_["buy"]["eid"] == "B1") <= 100.0 + 1e-6 and pairs)
b.cfg.alloc_max_contract_usd = 100000.0
pairs, info = plan(b, cash=1000.0, left=200.0)
check("turnover left $200: at most $200 rotated", 0 < sum(p_["usd"] for p_ in pairs) <= 200.0 + 1e-6
      and info["blocked_by"].get("turnover") == 1, (pairs, info))
check("a level is never bought on a market held short (it would be a close)",
      all(p_["buy"]["eid"] != "A1" for p_ in plan(b, cash=1000.0, inv={"A1": 1000, "B1": -5})[0]
          if p_["buy"] and p_["buy"]["eid"] == "B1" and not p_["buy"]["short"]))

# ============================================================================================ reserve (B2)
print("--- the market-making reserve")
api, b = alloc_bot(alloc_mm_reserve=1000.0)
warm(b)
pairs, _ = plan(b, cash=800.0)
check("cash 800 < reserve 1000: a refill sale first (no buy), ~$200", pairs and pairs[0]["buy"] is None
      and 180 <= pairs[0]["usd"] <= 200.0 + 1e-6 and pairs[0]["sell"]["eid"] == "A1", pairs[:1])
check("...then pairs with what is left of the holding", any(p_["buy"] for p_ in pairs[1:]))
pairs, _ = plan(b, cash=1500.0)
check("cash 1500 > reserve 1000: $500 of spare cash buys directly (a 'cash' pair, already 'sold', no sale)",
      any(p_["sell"]["kind"] == "cash" and p_["status"] == "sold" and abs(p_["usd"] - 500) <= 1 for p_ in pairs), pairs)
check("spare cash ranks at edge 0, before a holding at the same edge (no sale needed)",
      pairs[0]["sell"]["kind"] == "cash" and pairs[1]["sell"].get("eid") == "A1", [p_["sell"]["kind"] for p_ in pairs])

# ============================================================================================ sequencing
print("--- sell -> cash read -> buy across cycles")
api, b = alloc_bot()
warm(b)
api.calls.clear()
n0 = len(api.wire)
api.orders[901] = {"id": 901, "exchangeId": "A1", "side": "yes", "action": "sell", "priceLimit": 0.70, "quantity": 5,
                   "open": True, "expirationDate": M.iso(M.utcnow() + timedelta(hours=1))}
quiet(b.sync_orders, api.open_orders("T"), time.monotonic())
traded = tick(b)
w = api.wire[n0:]
check("cycle 1: one sale of Rep Alpha at the touch 0.60, nothing bought",
      len(w) == 1 and w[0]["exchangeId"] == "A1" and w[0]["action"] == "sell" and w[0]["price"] == 0.60, w)
check("...sold 1000 (the whole position, the bid's depth)", api.inv.get("A1", 0) == 0)
check("...the paired buy market's book was downloaded BEFORE the sale",
      [c for c in api.calls if c[0] in ("book", "batch")][:2] == [("book", "B1"), ("book", "A1")]
      or ("book", "B1") in api.calls[:api.calls.index(("batch", 1))], api.calls[:6])
ci = api.calls.index(("batch", 1))
check("our own orders on Rep Alpha cancelled before the order, the leftover cancelled after",
      ("cancel_all", "A1") in api.calls[:ci] and ("cancel_all", "A1") in api.calls[ci:] and 901 not in api.orders)
check("traded set returned (no quoting there this cycle)", traded == {"A1"})
check("IOC: the order lives take_order_ttl", abs((M.parse_ts(w[0]["expirationDate"]) - M.utcnow()).total_seconds()
                                                  - b.cfg.take_order_ttl) < 3)
pr = b.alloc_pairs[0] if b.alloc_pairs else {}
check("pair state: sold, $600 proceeds", pr.get("status") == "sold" and abs(pr.get("proceeds", 0) - 600) < 1e-6, pr)
n1 = len(api.wire)
tick(b)
check("cycle 2 without a new cash read: no buy", len(api.wire) == n1)
api.cash += 600.0
read(b)
tick(b)
w = api.wire[n1:]
check("cycle 3 after the cash read: the buy of Rep Beta at the touch 0.60", len(w) == 1 and w[0]["exchangeId"] == "B1"
      and w[0]["action"] == "buy" and w[0]["price"] == 0.60, w)
check("...$600 / 0.60 = 1000 shares bought", api.inv.get("B1", 0) == 1000, api.inv)
check("run finished: idle, no pairs left", b.alloc_state == "idle" and not b.alloc_pairs)
n2 = len(api.wire)
tick(b)
check("no new run before alloc_interval_s", len(api.wire) == n2 and b.alloc_state == "idle")
b.alloc_last_run_wall -= 3601
api.inv["A1"] = 0
tick(b)
check("a new run after the interval (nothing left to rotate: 0 pairs)", b.alloc_info.get("pairs_planned") == 0)
st = dict(b.alloc_info)
check("status figures: sold / bought / cash_before / cash_after / ev_gain_est / blocked_by / reserve",
      all(k in b.alloc_status() for k in ("state", "last_run", "pairs_planned", "sold", "bought", "cash_before",
                                          "cash_after", "ev_gain_est", "blocked_by", "reserve")), b.alloc_status())
check("lifetime totals: $600 sold, $600 bought, 2 runs", abs(b.alloc_totals["sold_total"] - 600) < 1e-6
      and abs(b.alloc_totals["bought_total"] - 600) < 1e-6 and b.alloc_totals["runs_total"] == 2)

# the level gone after the sale
api, b = alloc_bot(inv={"A1": 1000, "G1": 1000}, books={**books_default(), "G1": bk(0.70, 0.72)},
                   refs={**refs_default(), "Gamma Senate|Republican": 0.70, "Gamma Senate|Democratic": 0.30},
                   alloc_max_orders_per_cycle=1)
warm(b)
n0 = len(api.wire)
tick(b)
check("orders cap 1: one sale this cycle", len([w_ for w_ in api.wire[n0:] if w_["action"] == "sell"]) == 1)
api.books["B1"] = bk(0.55, 0.70)                  # the 0.60 offer is gone
api.cash += 600
read(b)
n1 = len(api.wire)
tick(b)
check("level gone at buy time: no buy", len(api.wire) == n1, api.wire[n1:])
check("...the cash stays, no more sales this run (the other pending pair skipped)",
      b.alloc_sells_stopped and not b.alloc_pairs and b.alloc_info["blocked_by"].get("depth") == 1, b.alloc_info)
# within 0.5c still bought
api, b = alloc_bot()
warm(b)
tick(b)
api.books["B1"] = bk(0.55, 0.605, q=5000)
api.cash += 600
read(b)
n1 = len(api.wire)
tick(b)
check("touch moved 0.5c (0.605): still bought at the touch", len(api.wire) == n1 + 1 and api.wire[-1]["price"] == 0.605)
check("...sized to the proceeds: floor(600 / 0.605) = 991", api.inv.get("B1") == 991, api.inv)
# paired level gone before the sale
api, b = alloc_bot()
warm(b)
api.books["B1"] = bk(0.55, 0.70)
n1 = len(api.wire)
tick(b)
check("paired level gone before the sale: nothing sold", len(api.wire) == n1 and api.inv.get("A1") == 1000)
# a sale's edge moved (bid fell well below p): not sold
api, b = alloc_bot()
warm(b)
api.books["A1"] = bk(0.50, 0.62)                  # edge-held (0.60 - 0.50) / 0.50 = 20% > 2%
n1 = len(api.wire)
tick(b)
check("the fresh sale price gives up too much (edge-held 20%): not sold", len(api.wire) == n1)

# ============================================================================================ cash gate / fresh read
print("--- cash gate interplay")
api, b = alloc_bot()
warm(b)
b.cg_read_at = time.monotonic() - 301
n1 = len(api.wire)
tick(b)
check("no action without a cash read younger than 5 min", len(api.wire) == n1 and b.alloc_state.startswith("waiting"))
check("...blocked_by cash", b.alloc_info.get("blocked_by", {}).get("cash", 0) >= 1)
read(b)
b.cfg.cash_gate_enabled = False
tick(b)
check("no action with the cash gate off", len(api.wire) == n1)
b.cfg.cash_gate_enabled = True
read(b)
tick(b)
api.cash += 0                                     # the read shows NO new money (cash 20000 - 1000 reserve... see below)
read(b, cash=1000.0 + 0.3)                        # only 0.30 above the reserve: not one share at 0.60
n1 = len(api.wire)
tick(b)
check("buy waits while the cash read does not show the money above the reserve", len(api.wire) == n1
      and b.alloc_pairs and b.alloc_pairs[0]["status"] == "sold")
read(b, cash=1000.0 + 300.0)
tick(b)
check("...then buys what the cash allows: $300 / 0.60 = 500", api.inv.get("B1") == 500, api.inv)
# the gate's need: the buy is charged to the gate
api, b = alloc_bot()
warm(b)
tick(b)
read(b, cash=20600.0)
tick(b)
check("the buy's cash need is charged to the gate (cg_spent = 600)", abs(b.cg_spent - 600.0) < 1e-6, b.cg_spent)
# buy expiry
api, b = alloc_bot()
warm(b)
tick(b)
b.alloc_pairs[0]["sold_at"] -= 1000
read(b, cash=0.0)
tick(b)
check("a sold pair's buy expires after 15 min without the money (cash stays)", not b.alloc_pairs)

# ============================================================================================ never a flip / covered NO
print("--- never a flip; a short is bought back only as a covered NO sale")
api, b = alloc_bot(inv={"G1": -1000}, books={**books_default(), "G1": bk(0.62, 0.70), "B2": bk(0.40, 0.42)})
warm(b)
n0 = len(api.wire)
tick(b)
w = api.wire[n0:]
check("short buy-back goes out as 'sell NO' at 1 - ask", len(w) == 1 and w[0]["side"] == "no"
      and w[0]["action"] == "sell" and abs(w[0]["price"] - 0.30) < 1e-9, w)
check("...never past flat", api.inv.get("G1", 0) <= 0)
api, b = alloc_bot(inv={"G1": -1000, "G2": -600}, books={**books_default(), "G1": bk(0.62, 0.70)})
warm(b)
pairs, _ = plan(b, cash=1000.0)
s = [p_["sell"] for p_ in pairs if p_["sell"].get("eid") == "G1"]
check("a short's NO+NO set part is never sold alone (only 400 lone NO)", s and sum(x["qty"] for x in s) <= 400, s)
b.cfg.reduce_no_as_sell = False
check("covered NO sales off: no short is sold", all(p_["sell"].get("eid") != "G1" for p_ in plan(b, cash=1000.0)[0]))
api, b = alloc_bot()
warm(b)
tick(b)
api.cash += 600
read(b)
n1 = len(api.wire)
tick(b, inv={**api.inv, "B1": -10})              # the buy market went short meanwhile
check("a buy against a short position is dropped (never a flip, the cash stays)", len(api.wire) == n1
      and not b.alloc_pairs)

# ============================================================================================ writes
print("--- write budget")
api, b = alloc_bot()
warm(b)
api.writes_left = lambda: 9                       # 0.3 x 9 / 3 = 0.9 -> 0 orders
n1 = len(api.wire)
tick(b)
check("alloc_writes_frac: 0.3 x 9 writes left / 3 per order < 1 -> nothing sent", len(api.wire) == n1
      and b.alloc_info.get("blocked_by", {}).get("writes", 0) >= 1)
api.writes_left = lambda: 10 ** 6
tick(b)
check("...sent once the budget is back (same run)", len(api.wire) == n1 + 1)
api, b = alloc_bot()
warm(b)
api.writes_left = lambda: 0
tick(b)
b.alloc_pairs[0]["planned_at"] -= 3601
api.writes_left = lambda: 10 ** 6
n1 = len(api.wire)
tick(b)
check("a sale still not sent after a whole interval expires (re-planned on the next run, never sent stale)",
      len(api.wire) == n1 and not b.alloc_pairs)

# ============================================================================================ bloc check
print("--- bloc check hook (Part A)")
api, b = alloc_bot()
warm(b)
b.cfg.bloc_delta_enabled = True
b.bloc_refresh(dict(api.inv))
b.cfg.max_bloc_delta_frac = 0.0001                # cap $10/sd, the book at ~+259/sd (1000 Rep Alpha at p 0.60)
before = b.bloc_delta_now(dict(api.inv))
pairs, info = plan(b, cash=1000.0)
check("Rep Alpha -> Rep Beta lowers |bloc delta| (+259 -> +233): allowed although above the cap",
      before > b.bloc_cap() and any(p_["buy"] and p_["buy"]["eid"] == "B1" for p_ in pairs)
      and not info["blocked_by"].get("bloc"), (before, info))
api, b = alloc_bot(inv={"A1": 1000, "A2": 1000}, books={**books_default(), "A2": bk(0.40, 0.42)})
warm(b)
b.cfg.bloc_delta_enabled = True
b.bloc_refresh(dict(api.inv))
pairs, info = plan(b, cash=1000.0)
check("bloc cap wide (5%): the rotation out of the Alpha pair is planned", any(p_["buy"] for p_ in pairs))
b.cfg.max_bloc_delta_frac = 0.0001
pairs, info = plan(b, cash=1000.0)
check("bloc cap tiny ($10/sd), book at 0: every pair that moves |bloc delta| past it is skipped, blocked_by bloc",
      not pairs and info["blocked_by"].get("bloc", 0) >= 1, (pairs, info))
b.cfg.bloc_delta_enabled = False
check("bloc_delta_enabled off: no bloc check (the pair is planned)",
      any(p_["buy"] and p_["buy"]["eid"] == "B1" for p_ in plan(b, cash=1000.0)[0]))
called = []
real = b.bloc_delta_now
b.bloc_delta_now = lambda inv=None: (called.append(1), real(inv))[1]
b.cfg.bloc_delta_enabled = True
plan(b, cash=1000.0)
check("the check goes through Bot.bloc_delta_now on the hypothetical book", len(called) >= 2)
b.bloc_delta_now = real

# ============================================================================================ B3 sets
print("--- B3: NO+NO sets through the short-set unwind")
SB = {**books_default(), "A1": bk(0.58, 0.61), "A2": bk(0.37, 0.41)}    # asks sum 1.02: cost 0.02 / 0.98 freed
api, b = alloc_bot(inv={"A1": -1000, "A2": -1000}, books=SB, pair_no_unwind_max_cost=0.0, alloc_set_cost_per_usd=0.05)
warm(b)
pairs, _ = plan(b, cash=1000.0)
s = pairs[0]["sell"] if pairs else {}
check("a set race ranked at its cost per $ freed (0.02 / 0.98)", s.get("kind") == "set"
      and abs(s["edge"] - 0.02 / 0.98) < 1e-9, s)
b.cfg.alloc_set_cost_per_usd = 0.02
check("...above alloc_set_cost_per_usd: not ranked", not any(p_["sell"]["kind"] == "set" for p_ in plan(b, cash=1000.0)[0]))
b.cfg.alloc_set_cost_per_usd = 0.05
b.cfg.pair_no_unwind_max_cost = -1.0
check("...nor with the short-set unwind off (pair_no_unwind_max_cost -1)",
      not any(p_["sell"]["kind"] == "set" for p_ in plan(b, cash=1000.0)[0]))
b.cfg.pair_no_unwind_max_cost = 0.0
members = b.groups["Alpha Senate"]
fv = {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}
check("before registration arb_plan does NOT unwind at asks sum 1.02 (max cost 0)",
      b.arb_plan(members, dict(api.inv), fv, False) is None)
tick(b)
check("registered for the short-set unwind", "Alpha Senate" in b.alloc_set_races and b.alloc_pairs
      and b.alloc_pairs[0]["status"] == "set_wait")
pl = b.arb_plan(members, dict(api.inv), fv, False)
check("...arb_plan now unwinds it (buy back both legs)", pl is not None and pl[0] == "unwind" and pl[1] == "buy", pl)
api.calls.clear()
quiet(b.cycle)                                     # take_arbitrage runs the unwind (covered NO sales, one batch)
b.drain_writes(5)
check("the existing plumbing unwound sets", -api.inv.get("A1", 0) < 1000, api.inv)
inv_now = dict(api.inv)
tick(b)
check("sets seen falling: pair sold at ~ (2 - 1.02) a set freed", b.alloc_pairs and b.alloc_pairs[0]["status"] == "sold"
      and "Alpha Senate" not in b.alloc_set_races, b.alloc_pairs)
api.cash += 2000
read(b)
n1 = len(api.wire)
tick(b)
check("...then bought after the cash read", len(api.wire) > n1 and api.wire[-1]["exchangeId"] in ("B1", "G1", "G2"))
api, b = alloc_bot(inv={"A1": -1000, "A2": -1000}, books=SB, pair_no_unwind_max_cost=0.0, alloc_set_cost_per_usd=0.05)
warm(b)
tick(b)
b.alloc_set_races["Alpha Senate"]["until"] = time.monotonic() - 1
tick(b)
check("registration expires after ALLOC_SET_WAIT with no unwind (withdrawn)", not b.alloc_set_races
      and not b.alloc_pairs)

# ============================================================================================ switch off, dry run, status
print("--- switch-off, dry run, status / journal, full cycles")
api, b = alloc_bot(alloc_max_orders_per_cycle=1, inv={"A1": 1000, "G1": 1000},
                   books={**books_default(), "G1": bk(0.70, 0.72)},
                   refs={**refs_default(), "Gamma Senate|Republican": 0.70, "Gamma Senate|Democratic": 0.30})
warm(b)
tick(b)
b.cfg.alloc_enabled = False
n1 = len(api.wire)
tick(b)
check("switched off mid-run: pairs dropped, nothing forced", not b.alloc_pairs and b.alloc_state == "off"
      and len(api.wire) == n1)
api, b = alloc_bot(live=False)
warm(b)
recs = []


class Cap(logging.Handler):
    def emit(self, record):
        recs.append(record.getMessage())


hd = Cap()
logging.getLogger().addHandler(hd)
log_level = logging.getLogger().level
logging.getLogger().setLevel(logging.INFO)
M.log.setLevel(logging.INFO)
n0 = len(api.wire)
b.alloc_tick(M.utcnow(), dict(api.inv), {}, None, set())
check("dry run: the plan is logged ([dry] ALLOC ...), nothing sent", any(r.startswith("[dry] ALLOC sell Rep Alpha Senate")
                                                                         for r in recs) and len(api.wire) == n0, recs[:3])
api, b = alloc_bot()
warm(b)
recs.clear()
b.alloc_tick(M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("journal: 'ALLOC sell <label> <qty> @ <px> (edge-held x%) -> buy <label> <qty> @ <px> (edge y%)'",
      any(r.startswith("ALLOC sell Rep Alpha Senate 1000 @ 0.600 (edge-held 0.0%) -> buy Rep Beta Senate 1000 @ 0.600 "
                       "(edge 16.7%)") for r in recs), recs[:4])
recs.clear()
b.cfg.cash_gate_enabled = False
b.warn_settings()
check("start-up / override warning: alloc_enabled without cash_gate_enabled", any(
    r.startswith("alloc_enabled is on without cash_gate_enabled") for r in recs), recs[-3:])
b.cfg.cash_gate_enabled = True
logging.getLogger().removeHandler(hd)
logging.getLogger().setLevel(log_level)
b.write_status(True)
with open(b.cfg.status_file) as f:
    sj = json.load(f)
check("status.json 'alloc' written once used", "alloc" in sj and sj["alloc"]["state"] in ("running", "idle"), sj.get("alloc"))
api2, b2 = alloc_bot()
b2.cfg.status_file = b.cfg.status_file
b2.alloc_init(b2.load_status_key("alloc"))
check("restart restores the hourly clock and the hour's flows", b2.alloc_last_run_wall == b.alloc_last_run_wall
      and len(b2.alloc_flows) == len([x for x in b.alloc_flows]))
# full cycles on the fake exchange, with the cash moving with the trades (no cash, no reserve: the sale funds the buy)
api, b = alloc_bot(cash=0.0, alloc_mm_reserve=0.0)
real_place = api.place_batch


def place_and_settle(orders):
    n0, before = len(api.fills), dict(api.inv)
    out = real_place(orders)
    for fl in api.fills[n0:]:
        e, q, p = fl["exchangeId"], fl["quantity"], fl["price"]
        pos = before.get(e, 0)
        if q > 0:
            c = min(q, max(0, -pos))
            api.cash += c * (1 - p) - (q - c) * p
        else:
            c = min(-q, max(0, pos))
            api.cash += c * p - (-q - c) * (1 - p)
        before[e] = pos + q
    return out


api.place_batch = place_and_settle
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
check("full cycles: Rep Alpha sold, Rep Beta bought (sale and buy on different cycles)",
      api.inv.get("A1", 0) == 0 and api.inv.get("B1", 0) >= 900, api.inv)
sells = [i for i, w_ in enumerate(api.wire) if w_["exchangeId"] == "A1" and w_["action"] == "sell"
         and w_["price"] == 0.60 and w_["quantity"] == 1000]
buys = [i for i, w_ in enumerate(api.wire) if w_["exchangeId"] == "B1" and w_["action"] == "buy" and w_["price"] == 0.60]
check("...the sale came first", sells and buys and sells[0] < buys[0], (sells, buys))

# ============================================================================================ red team (REDTEAM.md)
print("--- red team")
# RT-1: a registered NO+NO set race was unwound by take_arbitrage up to pair_no_unwind_max_sets (1,000) a batch at the
# allocator's cost, however few sets the plan needed (a $50 refill could unwind 1,000 sets at up to 6% per $).
api, b = alloc_bot(inv={"A1": -1000, "A2": -1000}, books=SB, cash=950.0, pair_no_unwind_max_cost=0.0,
                   alloc_set_cost_per_usd=0.05, alloc_min_edge_buy=0.5, pair_no_unwind_max_sets=1000)
warm(b)
tick(b)
want = sum(p_["sell"]["qty"] for p_ in b.alloc_pairs if p_["sell"]["kind"] == "set")
pl = b.arb_plan(b.groups["Alpha Senate"], dict(api.inv), fv, False)
check("RT-1 a $50 refill plans ~51 sets; the registered unwind is capped at them (not 1,000)",
      0 < want <= 60 and pl is not None and pl[3] <= want, (want, pl and pl[3]))
b.cfg.pair_no_unwind_max_cost = 0.03                # the race's own threshold admits it: the normal (uncapped) unwind
pl = b.arb_plan(b.groups["Alpha Senate"], dict(api.inv), fv, False)
check("RT-1 ...a set the normal short-set threshold already admits keeps its normal size",
      pl is not None and pl[3] == 1000, pl and pl[3])
# RT-2: the allocator bought (spare cash, and the buy half of pairs) while the bot was in global reduce-only (worst
# case / settlement risk over its cap), the one state where every other adding path stands still.
api, b = alloc_bot()
warm(b)
b.global_reduce = True
pairs, info = plan(b, cash=1000.0)
check("RT-2 global reduce-only: no pair with a buy planned (refills only), blocked_by risk",
      not any(p_["buy"] for p_ in pairs) and info["blocked_by"].get("risk", 0) >= 1, (pairs, info))
b.global_reduce = False
pairs, _ = plan(b, cash=1000.0)
check("RT-2 ...out of reduce-only the same book plans the pair", any(p_["buy"] for p_ in pairs))
tick(b)                                             # sold; reduce-only before the buy -> the buy waits (cash stays)
api.cash += 1000
read(b)
b.global_reduce = True
n1 = len(api.wire)
tick(b)
check("RT-2 a sold pair's buy is held while in reduce-only (nothing sent, the pair waits)",
      len(api.wire) == n1 and any(p_["status"] == "sold" for p_ in b.alloc_pairs), b.alloc_pairs)
b.global_reduce = False
# RT-3: inside the stop window the allocator traded on (value_mode zeroes the pre-close windows; it had no close check).
api, b = alloc_bot(value_mode=True)
warm(b)
for x in b.ex.values():
    x.close = M.utcnow() + timedelta(minutes=10)
pairs, _ = plan(b, cash=1000.0)
check("RT-3 10 min to close (stop window 15): the allocator plans nothing", not pairs, pairs)
# RT-5: reserve refill sales had no bloc check (only pairs did): a refill could push |bloc delta| past the cap.
api, b = alloc_bot(inv={"A1": 1000}, cash=0.0, alloc_min_edge_buy=0.5,
                   books={**books_default(), "B2": bk(0.29, 0.42)})   # (Dem Beta held at edge 3.4%: not sold)
warm(b)
b.cfg.bloc_delta_enabled = True
b.bloc_refresh({"A1": 1000, "B2": 5000})            # (sensitivities; the bloc delta of the book below)
api.inv["B2"] = 5000                                # Dem Beta long 5,000 (p 0.30): the book is Dem-leaning
b.cfg.max_bloc_delta_frac = abs(b.bloc_delta_now(dict(api.inv))) / b.bankroll() + 1e-6   # at the cap
pairs, info = plan(b, cash=0.0)
check("RT-5 a refill selling Rep Alpha would push the Dem-leaning book past the bloc cap: skipped, blocked_by bloc",
      not any(p_["sell"].get("eid") == "A1" for p_ in pairs) and info["blocked_by"].get("bloc", 0) >= 1, (pairs, info))
b.cfg.max_bloc_delta_frac = 0.5
pairs, info = plan(b, cash=0.0)
check("RT-5 ...with room under the cap the refill goes ahead", any(p_["sell"].get("eid") == "A1" for p_ in pairs))
# RT-6: a pin label that matches no market (typo, other spelling) pinned nothing, silently.
recs.clear()
logging.getLogger().addHandler(hd)
logging.getLogger().setLevel(logging.INFO)
api, b = alloc_bot(alloc_pin="Rep Alpha Senate, Rep Atlantis Senate")
b.warn_settings()
logging.getLogger().removeHandler(hd)
logging.getLogger().setLevel(log_level)
check("RT-6 alloc_pin naming no market: a warning names it", any("alloc_pin" in r and "Rep Atlantis Senate" in r
                                                                  and "Rep Alpha Senate" not in r for r in recs), recs[-3:])

# ============================================================================================ py_compile 3.10
print("--- py_compile under Python 3.10")
py310 = shutil.which("python3.10")
if py310:
    r = subprocess.run([py310, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py")],
                       capture_output=True, text=True)
    check("mm_bot.py compiles under Python 3.10", r.returncode == 0, r.stderr[-300:])
    r = subprocess.run([py310, "-m", "py_compile", os.path.abspath(__file__)], capture_output=True, text=True)
    check("tests/test_alloc.py compiles under Python 3.10", r.returncode == 0, r.stderr[-300:])
else:
    check("python3.10 available for the compile check", False)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
