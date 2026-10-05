"""Package 14.2 (owner, 5 Oct 13:20 UTC): a SWAP-ONLY value-floor margin. Diagnosis: analysis/p14/DIAG_14_2.md
(value_sell_margin never floored a paired allocator sale; raising it only added refills and shrank the swaps).
Both settings OFF by default (alloc_swap_sell_margin 0):
  alloc_swap_sell_margin  > 0: a SWAP sale (long / short, paired with a buy in the same run) at the touch must be
                          >= p - it (a short's buy-back <= p + it): in alloc_plan's pairing (blocked_by "swap_floor")
                          and again on the fresh book before the IOC (alloc_sell). Refills, stale-MM IOCs, quotes,
                          reduce-only quotes, the recycler, takes keep value_sell_margin.
  alloc_swap_min_gain     with the margin on: buy edge - sale edge-held (at the touch) >= max(alloc_min_improvement,
                          it) per $ (blocked_by "swap_gain"); the sale's cost IS its edge-held at the sale price (once).
  reporting               "ALLOC SWAP sold ... -> buy ...: net EV gain" at plan and at fill, alloc.swaps in status,
                          a WARNING while value_sell_margin > 0.01.
Sections: settings / ranges; flags off identical to 124ce75 on a grid (orders, quotes, notes, status, health, summary,
alloc_plan); the floor on paired sales only (refill, quote, reduce-only quote, recycler, take keep value_sell_margin at
0.005 and 0.03); the min-gain rule at the boundary, the cost counted once; the re-check before the IOC; a swap whose
buy fails (the sale stands, nothing repriced; the buy-wait expiry); journal lines and status; no duplicate orders; no p
/ empty books; the warning; the staged file; py_compile.
Run:  python tests/test_mm_funding_14_2.py      (exit code 0 = all passed)"""
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import astuple
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot                # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "124ce75"                              # Package 14.1 (READY), the head this package is built on
H = 3600.0


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def quiet(fn, *a, **k):
    logging.disable(logging.CRITICAL)
    try:
        return fn(*a, **k)
    finally:
        logging.disable(logging.NOTSET)


class Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def grab():
    g = Grab()
    M.log.addHandler(g)
    M.log.setLevel(logging.INFO)
    M.log.propagate = False
    return g


def ungrab(g):
    M.log.removeHandler(g)
    M.log.setLevel(logging.CRITICAL)
    M.log.propagate = True


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)
REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}


def bk(bid, ask, q=5000):
    return {"bids": [lvl(bid, q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


def books(**over):
    d = {"11": bk(0.10, 0.18), "12": bk(0.82, 0.90), "21": bk(0.53, 0.57), "22": bk(0.43, 0.47)}
    d.update(over)
    return d


def mk(inv=None, bks=None, refs=None, cash=0.0, **cfg):
    """Ohio (tails: p 0.12 / 0.88) and Utah (middle: p 0.55 / 0.45), as tests/test_mm_funding_14_1.py."""
    api, b = make_bot(books=bks or books())
    for x in b.ex.values():
        x.close = CLOSE
    b.refs = FakeRefs(dict(REFS if refs is None else refs))
    c = b.cfg
    c.selftest_enabled = False
    c.arb_enabled = False
    c.cash_gate_enabled = True
    c.cash_gate_reserve = 0.0
    c.reserved_cash_mode = "ignore"
    c.alloc_mm_reserve = 1000.0
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update(inv or {})
    api.cash = cash
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def read(b, cash=None):
    b.cg_cash = b.api.cash if cash is None else cash
    b.cg_reserved, b.cg_spent = 0.0, 0.0
    b.cg_read_at = time.monotonic()


def fresh(b):
    now_m = time.monotonic()
    for x in b.ex.values():
        x.verified = now_m


def sells(a):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in a.wire]


def atick(bb):
    time.sleep(0.002)
    return quiet(bb.alloc_tick, M.utcnow(), dict(bb.api.inv), bb.orders_by_eid(M.utcnow()), None, set())


def swap_bot(bid=0.545, inv=None, extra_books=None, **cfg):
    """A swap on a quiet book: 1000 long in 21 (p 0.55, bid `bid`), a 20% buy level in 11 (ask 0.10 vs p 0.12); cash
    exactly the 1000 MM reserve (no refill, no spare cash); no risk reserve (no pause). The allocator is switched on
    after a warm-up cycle; our own warm-up quotes are cancelled."""
    kw = {"alloc_enabled": False, "alloc_max_edge_sell": 0.05, "alloc_min_edge_buy": 0.05,
          "alloc_min_improvement": 0.03, "alloc_mm_reserve": 1000.0, "mm_risk_reserve_wc": 0.0,
          "mm_risk_reserve_corr": 0.0, "value_mode": True, "ref_weight": 1.0, **cfg}
    bks = books(**{"21": bk(bid, 0.57), "11": bk(0.08, 0.10, 20000), **(extra_books or {})})
    a, bb = mk(dict(inv or {"21": 1000}), bks, cash=1000.0, **kw)
    quiet(bb.cycle)
    bb.drain_writes(5)
    quiet(bb.cancel_everything)
    bb.my_orders.clear()
    a.orders.clear()
    a.inv.clear()
    a.inv.update(dict(inv or {"21": 1000}))
    for e, v in bks.items():                      # (the warm-up's quotes may have traded: the books as set)
        a.books[e] = {s: [dict(x) for x in v[s]] for s in ("bids", "asks")}
        bb.ex[e].book = {s: [dict(x) for x in v[s]] for s in ("bids", "asks")}
    bb.cfg.alloc_enabled = True
    bb.alloc_pairs, bb.alloc_run, bb.alloc_sells_stopped = [], {}, False
    bb.alloc_last_run_wall = None
    bb.alloc_flows.clear()
    bb.p142_events.clear()
    bb.p142_last_run = []
    a.wire, a.calls = [], []
    fresh(bb)
    read(bb, 1000.0)
    return a, bb


def gtick(bb):
    """alloc_tick with the journal captured (atick keeps it quiet)."""
    time.sleep(0.002)
    return bb.alloc_tick(M.utcnow(), dict(bb.api.inv), bb.orders_by_eid(M.utcnow()), None, set())


def plan(bb, cash=1000.0, refill_only=False):
    return quiet(bb.alloc_plan, dict(bb.api.inv), time.monotonic(), cash, set(), None, refill_only)


def swaps(pairs):
    return [p for p in pairs if p["buy"] is not None and p["sell"].get("eid")]


ON = {"alloc_swap_sell_margin": 0.03, "alloc_swap_min_gain": 0.05}

# ============================================================================================ settings
print("--- settings")
D = M.Config()
KEYS = ["alloc_swap_sell_margin", "alloc_swap_min_gain"]
check("defaults: alloc_swap_sell_margin 0 (off), alloc_swap_min_gain 0.05",
      [getattr(D, k) for k in KEYS] == [0.0, 0.05])
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after alloc_refill_max_cost, in Config and OVERRIDABLE",
      _f[_f.index("alloc_refill_max_cost") + 1:][:2] == KEYS
      and _ov[_ov.index("alloc_refill_max_cost") + 1:][:2] == KEYS)
check("ranges: margin 0-0.10, min gain 0-0.5", [M.OVERRIDABLE[k] for k in KEYS] == [(0.0, 0.10), (0.0, 0.5)],
      [M.OVERRIDABLE[k] for k in KEYS])
good, bad = M.validate_overrides({"alloc_swap_sell_margin": 0.03, "alloc_swap_min_gain": 0.05}, D)
check("the staged values (0.03 / 0.05) and the defaults are inside their ranges",
      not bad and len(good) == 2 and not M.validate_overrides({k: getattr(D, k) for k in KEYS}, D)[1], bad)
good, bad = M.validate_overrides({"alloc_swap_sell_margin": 0.11, "alloc_swap_min_gain": 0.6}, D)
check("out of range refused (2 of 2)", not good and len(bad) == 2, (good, bad))
good, bad = M.validate_overrides({"alloc_swap_sell_margin": -0.01, "alloc_swap_min_gain": "x"}, D)
check("negative / not a number refused (2 of 2)", not good and len(bad) == 2, (good, bad))
src = open(os.path.join(HERE, "..", "mm_bot.py")).read()
check("Config comment block '# --- P14.2: a swap-only value-floor margin'",
      "# --- P14.2: a swap-only value-floor margin" in src)
_, b_ = mk()
check("p142_on(): off by default; alloc_swap_min_gain alone turns nothing on; the margin > 0 does",
      not b_.p142_on() and not b_.p142_on(M.Config(alloc_swap_min_gain=0.3))
      and b_.p142_on(M.Config(alloc_swap_sell_margin=0.01)))
b_.cfg.alloc_min_improvement, b_.cfg.alloc_swap_min_gain = 0.03, 0.05
h1 = b_.swap_hurdle()
b_.cfg.alloc_min_improvement = 0.08
check("swap_hurdle = the stricter of alloc_min_improvement and alloc_swap_min_gain (0.05, then 0.08)",
      abs(h1 - 0.05) < 1e-12 and abs(b_.swap_hurdle() - 0.08) < 1e-12)
b_.cfg.alloc_swap_sell_margin = 0.03
check("swap_floor_ok: a long's sale >= p - margin, a short's buy-back <= p + margin (at the boundary included)",
      b_.swap_floor_ok(True, 0.52, 0.55) and not b_.swap_floor_ok(True, 0.515, 0.55)
      and b_.swap_floor_ok(False, 0.48, 0.45) and not b_.swap_floor_ok(False, 0.485, 0.45))

# ============================================================================================ flags off = 124ce75
print(f"--- flags off identical to the Package 14.1 head ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p142base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p142base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = (lambda msg: None), (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase"}
P141_ON = {"alloc_cancel_mm_first": True, "alloc_rank_all_markets": True, "mm_recycle_sell_first": True,
           "alloc_refill_ignore_prefer_short": True, "alloc_swap_room_netting": True}

if base is not None:
    def twin(inv, overrides, bks=None):
        res = []
        for mod in (M, base):
            api_x, bn = make_bot(books=bks or books())
            if mod is base:
                cfg = base.Config()
                for k in base.Config.__dataclass_fields__:
                    setattr(cfg, k, getattr(bn.cfg, k))
                d = tempfile.mkdtemp()
                for k in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file",
                          "overrides_file", "market_edge_file", "handover_file"):
                    setattr(cfg, k, os.path.join(d, os.path.basename(getattr(cfg, k))))
                api_x = FakeApi(True)
                api_x.markets_list = list(bn.api.markets_list)
                api_x.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                               for e, v in bn.api.books.items()}
                bn = base.Bot(api_x, cfg)
            for x in bn.ex.values():
                x.close = CLOSE
            api_x.inv.update(inv)
            api_x.cash = 2000.0
            api_x.pnl = (lambda a: lambda: (a.log("pnl"), {"totalAccountValue": a.equity, "cashBalance": a.cash})[1])(
                api_x)
            bn.refs = FakeRefs(dict(REFS))
            bn.cfg.selftest_enabled = False
            for k, v in overrides.items():
                if k in bn.cfg.__dataclass_fields__:
                    setattr(bn.cfg, k, v)
            for n in range(4):                    # four cycles; other traders hit our quotes before cycles 3 and 4
                if n in (2, 3):
                    for e, side in (("21", True), ("22", False), ("11", True), ("21", False)):
                        api_x.fill(e, side, 40)
                quiet(bn.cycle)
                bn.drain_writes(5)
            res.append((api_x, bn))
        return res

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = ok_n = ok_h = ok_l = True
    diffs = []
    grids = ({}, {"value_mode": True, "value_quote_hurdle": 0.05, "ref_weight": 1.0},
             {"cash_gate_enabled": True, "alloc_enabled": True, "alloc_mm_reserve": 15000.0, "value_mode": True,
              "mm_refill_fast": True, "mm_recycle_enabled": True, "alloc_max_edge_sell": 0.05, **P141_ON},
             {"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0, "value_mode": True,
              "worst_case_backstop_frac": 0.05, "mm_room_guard": True, "alloc_enabled": True,
              "cash_gate_enabled": True, "alloc_swap_room_netting": True, "alloc_min_edge_buy": 0.02,
              "alloc_swap_min_gain": 0.3, "value_sell_margin": 0.03})   # (min gain alone: nothing)
    for inv in ({}, {"21": 600, "11": 300}, {"22": -800, "12": 2500}):
        for ov in grids:
            (an, bn), (ab, bb_) = twin(inv, ov)
            if sorted(map(json.dumps, strip(an.wire))) != sorted(map(json.dumps, strip(ab.wire))):   # (writer threads)
                ok_w = False
                diffs.append(("wire", inv, ov))
            if {e: astuple(x.quote) for e, x in bn.ex.items()} != {e: astuple(x.quote) for e, x in bb_.ex.items()}:
                ok_q = False
                diffs.append(("quote", inv, ov))
            strip_t = lambda m: sorted(json.dumps({a: v for a, v in x.items() if a != "t"}, sort_keys=True)  # noqa
                                       for x in m.values())
            if strip_t(bn.order_meta) != strip_t(bb_.order_meta):
                ok_n = False
                diffs.append(("notes", inv, ov))
            bn.write_status(True)
            bb_.write_status(True)
            with open(bn.cfg.status_file) as f:
                sn = json.load(f)
            with open(bb_.cfg.status_file) as f:
                sb = json.load(f)
            drop = VOLATILE | set(M.Bot.MM_FUNDING_KEYS)
            wall = {"last_run_wall", "last_run", "since"}    # (wall clocks: the allocator's run, the pause start)
            cut = lambda s: {k: ({a: x for a, x in v.items() if a not in wall} if k in ("alloc", "mm_risk_room")  # noqa
                                 else v)
                             for k, v in s.items() if k not in drop and not k.endswith("_seconds")}
            if cut(sn) != cut(sb) or set(sb) - set(sn) or set(sn) - set(sb):
                ok_s = False
                diffs.append(("status", inv, ov, {k for k in set(cut(sn)) | set(cut(sb))
                                                  if cut(sn).get(k) != cut(sb).get(k)}))
            if set(bn.health) != set(bb_.health) or bn.status_report() != bb_.status_report():
                ok_h = False
                diffs.append(("health", inv, ov))
            if bn.summary_ops_line(100000.0) != bb_.summary_ops_line(100000.0):
                ok_l = False
                diffs.append(("summary", inv, ov))
    check("four cycles with fills send the same orders (3 books x plain / value / P14+14.1 flags on / room reserve "
          "with alloc_swap_min_gain 0.3 and value_sell_margin 0.03)", ok_w, diffs[:1])
    check("every market's quote (and keep limits) identical", ok_q, [d for d in diffs if d[0] == "quote"][:1])
    check("order notes identical", ok_n, [d for d in diffs if d[0] == "notes"][:1])
    check("status.json identical in keys AND values less mm_funding (no alloc.swaps key while off)", ok_s,
          [d for d in diffs if d[0] == "status"][:1])
    check("health keys and the status line identical", ok_h, [d for d in diffs if d[0] == "health"][:1])
    check("2-hourly summary line identical", ok_l, [d for d in diffs if d[0] == "summary"][:1])

    ok_p = True                                   # the pure planner: swaps, refills, the pause, the netting
    for inv, cash, extra in (({"21": 1000, "11": 2000}, 0.0, {}), ({"21": 1000}, 1000.0, {}),
                             ({"22": -900, "12": 300}, 5000.0, {}), ({"21": 1000}, 1000.0, P141_ON),
                             ({"21": 1000, "12": 500}, 300.0, {**P141_ON, "alloc_swap_min_gain": 0.4})):
        api_n, bn = mk(inv, books(**{"21": bk(0.53, 0.57), "11": bk(0.08, 0.10)}), alloc_enabled=True,
                       alloc_max_edge_sell=0.05, mm_refill_fast=True, mm_risk_reserve_wc=5000.0, **extra)
        quiet(bn.cycle)
        fresh(bn)
        bn.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
        bn.mmr_paused = bool(extra)
        bn.last_equity = 100000.0
        cfg = base.Config()
        for k in base.Config.__dataclass_fields__:
            setattr(cfg, k, getattr(bn.cfg, k))
        bb_ = base.Bot.__new__(base.Bot)
        bb_.__dict__.update(bn.__dict__)
        bb_.cfg = cfg
        now_m = time.monotonic()
        pn = quiet(bn.alloc_plan, dict(api_n.inv), now_m, cash, set(), None)
        pb = quiet(base.Bot.alloc_plan, bb_, dict(api_n.inv), now_m, cash, set(), None)
        ok_p = ok_p and json.dumps(pn, sort_keys=True, default=str) == json.dumps(pb, sort_keys=True, default=str)
        ok_p = ok_p and not any("swap" in p for p in pn[0])
    check("alloc_plan identical to 124ce75 (5 positions / cash / 14.1 flags / the pause; no 'swap' record while off)",
          ok_p)

# ============================================================================================ the floor: swaps only
print("--- the swap floor applies to PAIRED sales only")
api, b = swap_bot(bid=0.53)                       # 2c below p: edge-held 3.8% <= alloc_max_edge_sell 0.05
sw_off = swaps(plan(b)[0])
check("off (124ce75): a swap sale 2c below p is planned - no value_sell_margin floor on swaps (the diagnosis)",
      sw_off and sw_off[0]["sell"]["eid"] == "21" and "swap" not in sw_off[0], sw_off)
for k, v in ON.items():
    setattr(b.cfg, k, v)
sw_on = swaps(plan(b)[0])
check("on, margin 0.03: the same swap is planned (within 3c of p), carrying its record (sold / bought / gain_est)",
      sw_on and sw_on[0]["sell"]["eid"] == "21" and set(sw_on[0]["swap"]) >= {"sold", "bought", "gain_est",
                                                                             "gain_realised", "status"}, sw_on)
b.cfg.alloc_swap_sell_margin = 0.01
pairs, info = plan(b)
check("on, margin 0.01: 2c below p is past the swap floor - no swap, blocked_by swap_floor",
      not swaps(pairs) and info["blocked_by"].get("swap_floor") == 1, (pairs, info))
api, b = swap_bot(bid=0.515, alloc_max_edge_sell=0.10)   # 3.5c below p (edge 6.8%, allowed by max_edge_sell 0.10)
check("off: 3.5c below p is swapped whenever alloc_max_edge_sell allows it (6.8% <= 0.10)", swaps(plan(b)[0]))
for k, v in ON.items():
    setattr(b.cfg, k, v)
pairs, info = plan(b)
check("on (0.03): refused - the swap floor binds where alloc_max_edge_sell would not",
      not swaps(pairs) and info["blocked_by"].get("swap_floor") == 1, info)
b.cfg.alloc_max_edge_sell = 0.02                  # and the other way round: the edge rule still applies
api, b = swap_bot(bid=0.53, alloc_max_edge_sell=0.02, **ON)
check("alloc_max_edge_sell still picks the candidates: 3.8% > 0.02 is no swap even within the 3c floor",
      not swaps(plan(b)[0]))
api, b = swap_bot(bid=0.53, inv={"22": -1000}, extra_books={"22": bk(0.43, 0.47)}, reduce_no_as_sell=True, **ON)
pairs, _ = plan(b)
check("a short's buy-back 2c above p (ask 0.47 vs p 0.45) is a swap within the 0.03 floor",
      [p["sell"]["eid"] for p in swaps(pairs)] == ["22"] and swaps(pairs)[0]["sell"]["kind"] == "short",
      [p["sell"] for p in pairs])
b.cfg.alloc_swap_sell_margin = 0.01
pairs, info = plan(b)
check("...and refused past p + 0.01 (blocked_by swap_floor)", not swaps(pairs) and info["blocked_by"].get("swap_floor"),
      info)


def refill_bot(vsm, **cfg):
    """Cash 0 against a 1000 reserve, no buy level anywhere (alloc_min_edge_buy 0.9): refills only. 12 is bid 0.86 vs
    p 0.88 (2c below), 21 bid 0.545 vs p 0.55, stale MM shares in 21."""
    kw = {"alloc_enabled": False, "alloc_max_edge_sell": 0.05, "alloc_min_edge_buy": 0.9, "mm_refill_fast": True,
          "alloc_cancel_mm_first": True, "alloc_rank_all_markets": True, "value_sell_margin": vsm, **cfg}
    a, bb = mk({"21": 1000, "11": 2000, "12": 1000}, books(**{"21": bk(0.545, 0.57), "11": bk(0.12, 0.18),
                                                              "12": bk(0.86, 0.90)}), **kw)
    quiet(bb.cycle)
    bb.drain_writes(5)
    bb.cfg.alloc_enabled = True
    quiet(bb.cancel_everything)
    bb.my_orders.clear()
    a.orders.clear()
    a.wire = []
    fresh(bb)
    bb.ex["21"].last_fv = 0.55
    bb.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
    bb.alloc_last_run_wall = time.time()
    read(bb, 0.0)
    return a, bb


for vsm in (0.005, 0.03):
    got = {}
    for on in (False, True):
        a_r, b_r = refill_bot(vsm, **(ON if on else {}))
        pr_, inf_ = plan(b_r, 0.0, True)
        atick(b_r)
        got[on] = ([(p["sell"]["eid"], p["sell"]["qty"], p["sell"]["px"]) for p in pr_], sells(a_r))
    sold12 = any(x[0] == "12" for x in got[True][1])
    check(f"value_sell_margin {vsm}: a REFILL (no buy) is identical with the swap margin on or off, and 12 (2c below "
          f"p) is {'sold' if vsm > 0.02 else 'refused'} by value_sell_margin alone",
          got[True] == got[False] and sold12 == (vsm > 0.02) and got[True][1], got)


def quote_bot(vsm, ro=False, recycle=False, take=False, **cfg):
    """A full cycle in value_mode with longs in 21 and a short in 22 (the allocator off): the quoter's reducing
    sides, reduce-only (tiny risk limits), the recycler (stale MM lots), stale-quote takes."""
    kw = {"value_mode": True, "value_sell_margin": vsm, "ref_weight": 1.0, "value_quote_hurdle": 0.05, **cfg}
    if ro:
        kw.update(max_worst_case_frac=0.001, worst_case_backstop_frac=0.002)
    if recycle:
        kw.update(mm_recycle_enabled=True, mm_inv_max_age_h=1.0)
    if take:                                      # (11 offered at 0.05 against p 0.12: a stale-quote take)
        kw.update(take_enabled=True, take_confirm_seconds=0.0)
    extra = {"11": bk(0.03, 0.05)} if take else {}
    if recycle:                                   # (low edge-held: MM inventory, not handed to the value bucket)
        extra = {"21": bk(0.545, 0.60), "22": bk(0.40, 0.455)}
    a, bb = mk({"21": 800, "22": -600, "12": 300}, books(**{"21": bk(0.50, 0.60), "22": bk(0.40, 0.50), **extra}),
               cash=50000.0, **kw)
    for n in range(2):
        quiet(bb.cycle)
        bb.drain_writes(5)
        if recycle and n == 0:                    # (after the first cycle's seeding)
            bb.mm_lots = {"21": [[800.0, 0.55, time.time() - 9 * H]], "22": [[-600.0, 0.45, time.time() - 9 * H]]}
    return a, bb, {e: astuple(x.quote) for e, x in bb.ex.items()}


for vsm in (0.005, 0.03):
    for what, kw in (("quotes", {}), ("reduce-only quotes", {"ro": True}), ("the recycler", {"recycle": True}),
                     ("takes", {"take": True})):
        a0, b0, q0 = quote_bot(vsm, **kw)
        a1, b1, q1 = quote_bot(vsm, **kw, **ON)
        same = q0 == q1 and sorted(map(json.dumps, sells(a0))) == sorted(map(json.dumps, sells(a1)))
        if what == "reduce-only quotes":
            same = same and b1.global_reduce
        if what == "the recycler":
            same = same and bool(b1.mmf_recycling) and b0.mmf_recycling == b1.mmf_recycling
        if what == "takes":
            same = same and any(m.get("take") for m in b1.order_meta.values())
        q21 = b1.ex["21"].quote
        floor_ok = q21.ask is None or q21.ask >= M.ceil_tick(0.55 - vsm) - 1e-9
        check(f"value_sell_margin {vsm}: {what} identical with the swap margin on, the long's ask >= p - "
              f"value_sell_margin", same and floor_ok, (what, q21, b1.global_reduce))

# ============================================================================================ the min-gain rule
print("--- alloc_swap_min_gain: the boundary, the cost counted once")
api, b = swap_bot(bid=0.545, **ON)                # edge-held (0.55 - 0.545) / 0.545 = 0.917%; buy edge 20%
pairs = swaps(plan(b)[0])
gap = pairs[0]["buy"]["edge"] - pairs[0]["sell"]["edge"] if pairs else 0.0
check("the plan's gap: buy edge 20% - sale edge-held 0.92% = 19.08% per $", pairs and abs(gap - (0.2 - 0.005 / 0.545))
      < 1e-9, gap)
b.cfg.alloc_swap_min_gain = gap - 0.0005
check("alloc_swap_min_gain just below the gap: admitted", swaps(plan(b)[0]))
b.cfg.alloc_swap_min_gain = gap + 0.0005
pairs, info = plan(b)
check("just above it: refused (blocked_by swap_gain), while alloc_min_improvement 0.03 alone would admit it",
      not swaps(pairs) and info["blocked_by"].get("swap_gain") == 1, info)
b.cfg.alloc_swap_min_gain, b.cfg.alloc_min_improvement = 0.0, gap + 0.0005
check("alloc_min_improvement above the gap refuses it with alloc_swap_min_gain 0 (the stricter of the two)",
      not swaps(plan(b)[0]))
api, b = swap_bot(bid=0.53, **ON)                 # edge-held = cost = 0.02 / 0.53 = 3.77%; buy edge 20%
b.cfg.alloc_swap_min_gain = 0.15
pairs = swaps(plan(b)[0])
r = pairs[0]["swap"] if pairs else {}
check("cost counted once: 20% - 3.77% = 16.2% >= 0.15 admitted (counting the cost twice, 12.5%, would refuse it)",
      pairs and abs(r["sold"]["cost"] - r["sold"]["edge"]) < 1e-9 and abs(r["sold"]["edge"] - 0.02 / 0.53) < 1e-4, r)
check("the record: sold {qty, price 0.53, p 0.55, cost_usd = qty x 2c}, bought {price 0.10, p 0.12, edge 20%}, "
      "gain_est = $ x (buy edge - sale edge-held)",
      r and r["sold"]["price"] == 0.53 and abs(r["sold"]["p"] - 0.55) < 1e-9
      and abs(r["sold"]["cost_usd"] - round(r["sold"]["qty"] * 0.02, 2)) < 0.01 and r["bought"]["price"] == 0.10
      and abs(r["bought"]["edge"] - 0.2) < 1e-9
      and abs(r["gain_est"] - round(pairs[0]["usd"] * (0.2 - 0.02 / 0.53), 2)) < 0.01, r)
b.cfg.alloc_swap_min_gain = 0.17
check("...and 0.17 > 16.2% refuses it", not swaps(plan(b)[0]))
api, b = swap_bot(bid=0.53, **{**ON, "alloc_swap_min_gain": 0.5})
api.inv["11"] = 0
pairs = plan(b, 3000.0)[0]                        # 2000 of spare cash: a cash pair (edge 0) keeps the plain hurdle
check("a spare-cash pair is not a swap (no floor, alloc_min_improvement only) while the swap is refused at 0.5",
      any(p["sell"]["kind"] == "cash" and "swap" not in p for p in pairs) and not swaps(pairs),
      [(p["sell"]["kind"], "swap" in p) for p in pairs])

# ============================================================================================ before the IOC
print("--- the re-check on the fresh book before the IOC")
api, b = swap_bot(bid=0.53, alloc_max_edge_sell=0.10, **ON)
atick(b)                                          # the run plans the swap and sells 21 at once
check("the sale goes out at the checked touch (21 @ 0.53, an IOC)", any(x[0] == "21" and x[3] == 0.53
                                                                          for x in sells(api)), sells(api))
api, b = swap_bot(bid=0.53, alloc_max_edge_sell=0.10, **ON)
pairs, info = plan(b)
for pr in pairs:
    pr["planned_at"] = time.monotonic()
b.alloc_pairs, b.alloc_last_run_wall, b.alloc_run = pairs, time.time(), {}
api.books["21"] = bk(0.51, 0.57)                  # the touch fell to 4c below p before the sale
g = grab()
gtick(b)
ungrab(g)
check("the touch fell past the swap floor: nothing is sold (blocked swap_floor), the pair is gone, logged",
      not any(x[0] == "21" for x in sells(api)) and b.alloc_run.get("blocked_by", {}).get("swap_floor") == 1
      and any("past the swap floor" in x for x in g.lines), (sells(api), b.alloc_run, g.lines[-3:]))
check("...its record closes 'not_sold' (alloc.swaps counts it)", pairs[0]["swap"]["status"] == "not_sold"
      and b.swap_report()["counts_24h"]["not_sold"] == 1, pairs[0]["swap"])
api, b = swap_bot(bid=0.545, **ON)
pairs, _ = plan(b)
for pr in pairs:
    pr["planned_at"] = time.monotonic()
b.alloc_pairs, b.alloc_last_run_wall, b.alloc_run = pairs, time.time(), {}
api.books["11"] = bk(0.08, 0.105, 20000)          # the buy level is still there (within 0.5c), 14.3% now: the gap
b.cfg.alloc_swap_min_gain = 0.15                  # 13.4% < 0.15 but >= alloc_min_improvement (the 14.1 check passes)
atick(b)
check("the buy's fresh edge no longer clears the swap hurdle: the sale is not sent (blocked swap_gain)",
      not any(x[0] == "21" for x in sells(api)) and b.alloc_run.get("blocked_by", {}).get("swap_gain") == 1,
      (sells(api), b.alloc_run))

# ============================================================================================ a buy that fails
print("--- a swap whose buy fails after its sale")
api, b = swap_bot(bid=0.53, **ON)
g = grab()
gtick(b)
pr = next((p for p in b.alloc_pairs if p.get("swap")), None)
check("the sale traded, the pair waits for its buy (record 'sold', realised at p: -qty x 2c)",
      pr is not None and pr["status"] == "sold" and pr["swap"]["status"] == "sold"
      and abs(pr["swap"]["real_sell"] + pr["swap"]["sold"]["qty"] * 0.02) < 0.01, pr and pr["swap"])
api.books["11"] = bk(0.08, 0.13, 20000)           # the buy level is gone (p 0.12 < the ask: no edge)
pr["sold_at"] = time.monotonic() - 1
read(b, 1000.0 + pr["proceeds"])
fresh(b)
api.wire = []
gtick(b)
ungrab(g)
r = pr["swap"]
check("the buy level is gone: no buy, the pair ends, its record 'buy_failed' with the sale's given-up EV as realised",
      not any(x[0] == "11" for x in sells(api)) and r["status"] == "buy_failed"
      and abs(r["gain_realised"] - r["real_sell"]) < 1e-9 and r["gain_realised"] < 0, (sells(api), r))
check("the sale is not repriced or sent again: no order in 21 after it, the run's sales stopped",
      not any(x[0] == "21" for x in sells(api)) and b.alloc_sells_stopped, sells(api))
check("journaled: 'ALLOC SWAP sold ... NOT bought (gone): the sale stands as traded'",
      any(x.startswith("ALLOC SWAP sold Rep Utah Senate") and "NOT bought (gone)" in x for x in g.lines),
      [x for x in g.lines if "SWAP" in x])
quiet(b.cycle)
b.drain_writes(5)
q21 = b.ex["21"].quote
check("the market's quote keeps value_sell_margin after the failed swap (the long's ask >= p - 0.005)",
      q21.ask is None or q21.ask >= M.ceil_tick(0.55 - 0.005) - 1e-9, q21)
api, b = swap_bot(bid=0.53, **ON)
atick(b)
pr = next((p for p in b.alloc_pairs if p.get("swap")), None)
pr["sold_at"] = time.monotonic() - M.Bot.ALLOC_BUY_WAIT - 1  # no cash read showed the money for 15 min
b.cg_read_at = time.monotonic()
atick(b)
check(f"the buy-wait path: no cash within ALLOC_BUY_WAIT ({M.Bot.ALLOC_BUY_WAIT:.0f} s) -> 'expired', the record "
      "'buy_failed' (why expired)", pr["status"] == "expired" and pr["swap"]["status"] == "buy_failed"
      and pr["swap"].get("why") == "expired", (pr["status"], pr["swap"]))

# ============================================================================================ journal and status
print("--- the journal lines and alloc.swaps")
api, b = swap_bot(bid=0.53, **ON)
g = grab()
gtick(b)
pr = next((p for p in b.alloc_pairs if p.get("swap")), None)
pr["sold_at"] = time.monotonic() - 1
read(b, 1000.0 + pr["proceeds"])
fresh(b)
gtick(b)
ungrab(g)
PAT = re.compile(r"^ALLOC SWAP sold (.+?) (\d+) @ (0\.\d{3}) \(p (0\.\d{3}), edge-held (-?\d+\.\d)%, cost (-?\d+\.\d)% "
                 r"= \$[\d,.]+\) -> buy (.+?) (\d+) @ (0\.\d{3}) \(p (0\.\d{3}), edge (-?\d+\.\d)%\): net EV gain "
                 r"([+-])\$([\d,.]+) \(per \$ (-?\d+\.\d)%\) - (planned|done, realised at p)$")
lines = [x for x in g.lines if x.startswith("ALLOC SWAP")]
m_pl = [PAT.match(x) for x in lines if x.endswith("planned")]
m_dn = [PAT.match(x) for x in lines if x.endswith("realised at p")]
check("at plan: 'ALLOC SWAP sold Rep Utah Senate 1000 @ 0.530 (p 0.550, edge-held 3.8%, cost 3.8% = $20.00) -> buy "
      "Rep Ohio Senate ... (p 0.120, edge 20.0%): net EV gain +$x (per $ 16.2%) - planned'",
      len(m_pl) == 1 and m_pl[0] and m_pl[0].group(3) == "0.530" and m_pl[0].group(4) == "0.550"
      and m_pl[0].group(5) == "3.8" and m_pl[0].group(6) == "3.8" and m_pl[0].group(11) == "20.0"
      and m_pl[0].group(12) == "+" and m_pl[0].group(14) == "16.2", lines)
check("at fill: the same line with the traded quantities and prices, '- done, realised at p'",
      len(m_dn) == 1 and m_dn[0] and m_dn[0].group(1) == "Rep Utah Senate" and m_dn[0].group(9) == "0.100", lines)
r = pr["swap"]
check("realised gain = the buy's qty x (p - price) less the sale's qty x (p - price), at p",
      r["status"] == "done" and abs(r["gain_realised"] - round(r["bought"]["qty"] * 0.02 - r["sold"]["qty"] * 0.02, 2))
      < 0.02, r)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
sw = st["alloc"].get("swaps") or {}
check("status alloc.swaps: last_run (sold {label, qty, price, p, edge, cost}, bought {label, qty, price, p, edge}, "
      "gain_est, gain_realised, status), counts_24h, ev_gain_est_24h, ev_gain_realised_24h",
      set(sw) >= {"last_run", "counts_24h", "ev_gain_est_24h", "ev_gain_realised_24h", "usd_24h"}
      and len(sw["last_run"]) == 1 and set(sw["last_run"][0]["sold"]) >= {"label", "qty", "price", "p", "edge", "cost"}
      and set(sw["last_run"][0]["bought"]) >= {"label", "qty", "price", "p", "edge"}
      and sw["last_run"][0]["status"] == "done", sw)
check("counts_24h planned 1 / done 1, ev_gain_est_24h = the plan's, ev_gain_realised_24h = the fills'",
      sw["counts_24h"] == {"planned": 1, "done": 1, "buy_failed": 0, "not_sold": 0}
      and abs(sw["ev_gain_est_24h"] - r["gain_est"]) < 0.01 and abs(sw["ev_gain_realised_24h"] - r["gain_realised"])
      < 0.01, sw["counts_24h"])
b2 = M.Bot(api, b.cfg)
check("a restart restores the 24-h swap log (alloc.swaps.events)",
      b2.swap_report()["counts_24h"] == sw["counts_24h"], b2.swap_report()["counts_24h"])
api, b = swap_bot(bid=0.53)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("off: no alloc.swaps key in status.json", "swaps" not in st.get("alloc", {}))
g = grab()
gtick(b)
ungrab(g)
check("...and an off run journals no ALLOC SWAP line (the plain ALLOC lines only)",
      not any(x.startswith("ALLOC SWAP") for x in g.lines) and any(x.startswith("ALLOC run") for x in g.lines),
      g.lines[:3])
api, b = swap_bot(bid=0.53, **ON)
b.api.live = False                                # dry run: the swap planned and journaled, nothing sent
g = grab()
gtick(b)
ungrab(g)
check("dry run: the planned swap is journaled, nothing is sent",
      any(x.startswith("ALLOC SWAP sold") and x.endswith("planned") for x in g.lines) and not api.wire, g.lines)

# ============================================================================================ no duplicate orders
print("--- no duplicate orders")
api, b = swap_bot(bid=0.53, inv={"21": 1000, "22": -1000}, extra_books={"22": bk(0.43, 0.47)},
                  alloc_max_orders_per_cycle=4, reduce_no_as_sell=True, **ON)
atick(b)
w = sells(api)
check("two swap sales planned: one IOC per market, never more than the position (21 <= 1000, 22 NO <= 1000)",
      len([x for x in w if x[0] == "21"]) == 1 and len([x for x in w if x[0] == "22"]) == 1
      and all(x[4] <= 1000 for x in w), w)
seq = [c for c in api.calls if c[0] in ("cancel_all", "batch")]
check("each sale is preceded by a whole-exchange cancel of our orders in its market (as every allocator IOC)",
      all(seq[i - 1][0] == "cancel_all" for i, c in enumerate(seq) if c[0] == "batch" and i > 0)
      and ("cancel_all", "21") in seq and ("cancel_all", "22") in seq, seq)
api.wire = []
atick(b)
check("the next cycle sends no second sale in 21 or 22 (the pairs wait for their buys)",
      not any(x[0] in ("21", "22") for x in sells(api)), sells(api))

# ============================================================================================ robustness
print("--- no p, empty books")
api, b = swap_bot(bid=0.53, **ON)
b.refs = FakeRefs({})
b.cur_refs, b.cur_liquid = {}, set()
pairs, info = plan(b)
atick(b)
check("no Polymarket reference: no swap, nothing sent, no crash", not swaps(pairs) and sells(api) == [])
check("...and alloc.swaps still reports (empty last run)", b.swap_report()["last_run"] == [])
api, b = swap_bot(bid=0.53, **ON)
for e in list(api.books):
    api.books[e] = {"bids": [], "asks": []}
for x in b.ex.values():
    x.book = {"bids": [], "asks": []}
fresh(b)
atick(b)
quiet(b.cycle)
check("empty books everywhere: nothing sold, a full cycle still runs", sells(api) == []
      and b.health.get("last_cycle_ok") is not False)
api, b = swap_bot(bid=0.53, **ON)
atick(b)
b.cfg.alloc_enabled = False
g = grab()
gtick(b)
ungrab(g)
check("the allocator switched off with a swap in flight: its record closes (buy_failed: the sale stands), no crash",
      b.p142_last_run and b.p142_last_run[0]["status"] == "buy_failed"
      and b.p142_last_run[0].get("why") == "allocator off", b.p142_last_run)
check("swap_cost: a long sold above p costs a negative amount (a gain), a short's buy-back below p too",
      M.Bot.swap_cost("long", 100, 0.56, 0.55)[1] < 0 and M.Bot.swap_cost("short", 100, 0.44, 0.45)[1] < 0)

# ============================================================================================ the warning
print("--- the warning")
for vsm, margin, want in ((0.03, 0.03, True), (0.005, 0.03, False), (0.03, 0.0, False)):
    _, bw = mk(value_sell_margin=vsm, alloc_swap_sell_margin=margin)
    g = grab()
    bw.warn_settings()
    bw.warn_settings()
    ungrab(g)
    hits = [x for x in g.lines if x.startswith("WARNING alloc_swap_sell_margin")]
    check(f"value_sell_margin {vsm}, alloc_swap_sell_margin {margin}: "
          + ("ONE warning (not repeated)" if want else "no warning"), len(hits) == (1 if want else 0), g.lines)

# ============================================================================================ the staged file
print("--- the staged file")
DEPLOY = os.path.join(HERE, "..", "deploy", "package14")
P142 = os.path.join(DEPLOY, "settings_override.mm_funding_14_2.json")
P141 = os.path.join(DEPLOY, "settings_override.mm_funding_14_1.json")
raw = json.load(open(P142))
good, bad = M.validate_overrides(raw, M.Config())
check("settings_override.mm_funding_14_2.json validates (no refused key)", not bad, bad)
r141 = json.load(open(P141))
check("it is the 14.1 file + alloc_swap_sell_margin 0.03, alloc_swap_min_gain 0.05, value_sell_margin 0.005 "
      "(nothing else changed)",
      {k: v for k, v in raw.items() if k not in ("alloc_swap_sell_margin", "alloc_swap_min_gain",
                                                  "value_sell_margin")} == r141
      and raw["alloc_swap_sell_margin"] == 0.03 and raw["alloc_swap_min_gain"] == 0.05
      and raw["value_sell_margin"] == 0.005, set(raw) ^ set(r141))
check("never resets capital_ceiling_adding_size_factor / arb_enabled / ref_tilt_headline (0.5 / false / true)",
      raw.get("capital_ceiling_adding_size_factor") == 0.5 and raw.get("arb_enabled") is False
      and raw.get("ref_tilt_headline") is True)
check("the staged file in the live format (one key per line, 2-space indent, as the 14.1 file)",
      open(P142).read() == json.dumps(raw, indent=2) + "\n" or open(P142).read() == json.dumps(raw, indent=2),
      open(P142).read()[:80])
check("py_compile on python3.10 (mm_bot.py and this test)",
      subprocess.run([sys.executable, "-m", "py_compile", os.path.join(HERE, "..", "mm_bot.py"), __file__],
                     capture_output=True).returncode == 0)

print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
