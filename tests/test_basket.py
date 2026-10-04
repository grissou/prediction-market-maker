"""
Offline tests for Package 9 F1: the long-tilt basket (basket_*; analysis/p9/SPEC_F1_BASKET.md). The bet: hold longshot
YES / favourite NO (the cheaper route per race), sized on a cushion above a floor (CPPI), bought and sold only as
immediate-or-cancel takes, exited by a date (and a hard T-72h backstop), killed on the liquidation value.
Covers: settings (defaults, ranges, the exit-date validation), flags off identical to the branch head (f3851da) on a
grid (decide / plan, settlement_risk, total_worst_case, status keys, two full cycles on the fake exchange), the target
math (floor, peak, cushion net of own impact, caps, ramp), selection (route, exclusions, laggards first), sizing caps
(leg cap / min legs, ask share, cash, writes, orders per cycle), sending (IOC, our quotes cancelled first, leftovers
cancelled, refusal paths), the 36-h test cut, the kill (floor, drawdown, confirmation, latched across a restart, the
explicit off/on reset), the exit schedule and the backstop, the stress risk model, basket legs not quoted / not touched
by takes, arbitrage or tilt exits, release on switch-off, persistence, and py_compile under Python 3.10.

Run:  python tests/test_basket.py      (exit code 0 = all passed)
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

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "f3851da"                              # the branch head before this package (Package 9 specs)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


H = 3600.0
NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)                  # every market closes 30 days from now (tests never depend on today)
BASE_BOOKS = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
              "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
              "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
              "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
BASE_REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
             "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
# race k: Rep (favourite, eid k1) and Dem (longshot, eid k2), Polymarket 0.97 / 0.03; (Dem ask, Rep bid)
RACES = {3: (0.080, 0.900), 4: (0.085, 0.900), 5: (0.075, 0.900), 6: (0.120, 0.920)}   # race 6: the NO route is cheaper


def race_name(k):
    return f"Race{k} Senate"


def ls_book(ask, q=4000):
    return {"bids": [lvl(round(ask - 0.01, 3), 4000)],
            "asks": [lvl(ask, q), lvl(round(ask + 0.005, 3), q), lvl(round(ask + 0.01, 3), q)]}


def fav_book(bid, q=4000):
    return {"bids": [lvl(bid, q), lvl(round(bid - 0.005, 3), q), lvl(round(bid - 0.01, 3), q)],
            "asks": [lvl(round(bid + 0.02, 3), 4000)]}


def basket_bot(races=None, cash=50000.0, enabled=True, extra_markets=(), extra_books=None, extra_refs=None, live=True,
               status_dir=None, **cfg):
    """make_bot's Ohio / Utah plus the RACES (longshot Dem / favourite Rep), Polymarket on every leg (liquid), every
    market closing at CLOSE, the cash gate on with `cash` free (the fake's cash model and P&L read), self-tests off."""
    races = RACES if races is None else races
    books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]} for e, v in BASE_BOOKS.items()}
    mk, refs = [], dict(BASE_REFS)
    for k, (ask, bid) in races.items():
        mk += [market(f"m{k}1", f"{k}1", "Republican", race_name(k)), market(f"m{k}2", f"{k}2", "Democratic", race_name(k))]
        books[f"{k}1"], books[f"{k}2"] = fav_book(bid), ls_book(ask)
        refs[f"{race_name(k)}|Republican"], refs[f"{race_name(k)}|Democratic"] = 0.97, 0.03
    books.update(extra_books or {})
    refs.update(extra_refs or {})
    api, b = make_bot(live=live, books=books, extra_markets=tuple(mk) + tuple(extra_markets))
    if status_dir:
        b.cfg.status_file = os.path.join(status_dir, "status.json")
    b.t["endDate"] = M.iso(CLOSE)
    for x in b.ex.values():
        x.close = CLOSE
    b.refs = FakeRefs(refs)
    c = b.cfg
    c.selftest_enabled = False
    c.reduce_no_as_sell = True                    # (live: on; the favourite-NO route needs covered NO sales)
    c.arb_enabled = False                         # (live: off; race 6's bids add up to 1.03)
    c.cash_gate_enabled = True
    c.basket_enabled = enabled
    c.basket_exit_utc = M.iso(NOW + timedelta(days=20))
    c.reserved_cash_mode = "ignore"
    for k, v in cfg.items():
        setattr(c, k, v)
    api.cash = cash
    if cash is not None:
        api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def quiet(fn, *a, **k):
    logging.disable(logging.CRITICAL)
    try:
        return fn(*a, **k)
    finally:
        logging.disable(logging.NOTSET)


def cycle(b, n=1):
    for _ in range(n):
        quiet(b.cycle)
        b.drain_writes(5)


def fvs_of(b):
    return {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}


def warm(b):
    """One cycle with the basket off (books, Polymarket, fair values, the cash read), then the basket back on."""
    on = b.cfg.basket_enabled
    b.cfg.basket_enabled = False
    cycle(b)
    b.cfg.basket_enabled = on
    b.cg_read_at = time.monotonic()
    return fvs_of(b)


def tick(b, now=None, inv=None, equity=100000.0, fvs=None):
    fv = fvs if fvs is not None else fvs_of(b)
    return quiet(b.basket_tick, now or M.utcnow(), dict(b.api.inv) if inv is None else inv, fv, fv, equity, {})


# ============================================================================================ settings
print("--- settings")
c = M.Config()
KEYS = [k for k in M.Config.__dataclass_fields__ if k.startswith("basket_")]
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("defaults: off, mult 5, floor 86k / 0.85 peak, cap 80k / 0.85, stress 0.4, exit 2026-10-18T12:00Z",
      (c.basket_enabled, c.basket_mult, c.basket_floor, c.basket_floor_peak_frac, c.basket_cap, c.basket_cap_frac,
       c.basket_stress_frac, c.basket_exit_utc) == (False, 5.0, 86000.0, 0.85, 80000.0, 0.85, 0.4, "2026-10-18T12:00:00Z"))
check("defaults: impact 0.06 / 24 h, build 4 h, ask share 0.25 / first 0.75, max_ref 0.10, max_price 0.25",
      (c.basket_impact_frac, c.basket_impact_hours, c.basket_build_hours, c.basket_max_ask_share,
       c.basket_first_build_ask_share, c.basket_max_ref, c.basket_max_price) == (0.06, 24.0, 4.0, 0.25, 0.75, 0.10, 0.25))
check("defaults: legs 20 / 0.08, exclude headline, exit 24 h, no adds 7 d, test 36 h / 0.15 / 1.5, kill 0.15 / 2 h, "
      "writes 0.5 / 4, slip 0.005",
      (c.basket_min_legs, c.basket_max_leg_frac, c.basket_exclude_headline, c.basket_exit_hours, c.basket_no_add_days,
       c.basket_test_hours, c.basket_test_min_s, c.basket_mult_after_fail, c.basket_kill_dd, c.basket_kill_hours,
       c.basket_writes_frac, c.basket_max_orders_per_cycle, c.basket_slip)
      == (20, 0.08, True, 24.0, 7.0, 36.0, 0.15, 1.5, 0.15, 2.0, 0.5, 4, 0.005))
check("one contiguous block right after ref_guard_exits in Config and in OVERRIDABLE (28 settings)",
      len(KEYS) == 28 and _f[_f.index("ref_guard_exits") + 1:_f.index("ref_guard_exits") + 29] == KEYS
      and _ov[_ov.index("ref_guard_exits") + 1:_ov.index("ref_guard_exits") + 29] == KEYS, KEYS)
good, bad = M.validate_overrides({k: getattr(c, k) for k in KEYS}, c)
check("every default is inside its live range", len(good) == 28 and not bad, bad)
good, bad = M.validate_overrides({"basket_mult": 9.0, "basket_exit_hours": 0.5, "basket_min_legs": 0,
                                  "basket_stress_frac": 1.5, "basket_kill_dd": 0.01}, c)
check("out of range refused (mult 9, exit 0.5 h, min legs 0, stress 1.5, kill_dd 0.01)", not good and len(bad) == 5, bad)
good, bad = M.validate_overrides({"basket_exit_utc": "2026-10-20T06:00:00Z"}, c)
check("exit date: an ISO UTC time in range is accepted", good == {"basket_exit_utc": "2026-10-20T06:00:00Z"}, bad)
bad_dates = ["2026-10-20", "2026-10-20T06:00:00", "tomorrow", "2026-12-01T00:00:00Z", "2026-09-01T00:00:00Z", 5, ""]
check("exit date: no time zone / not a date / outside 1 Oct..4 Nov / a number refused",
      all(not M.validate_overrides({"basket_exit_utc": v}, c)[0] for v in bad_dates))
check("no setting can move the backstop or the cushion measure (no basket_backstop* / basket_cushion_measure)",
      not any("backstop" in k or "cushion" in k for k in KEYS) and M.Bot.BASKET_BACKSTOP_HOURS == 72.0)

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


def pair(inv, cash):
    api_n, bn = basket_bot(cash=cash, enabled=False)
    api_n.inv.update(inv)
    cfg = base.Config()
    for k in base.Config.__dataclass_fields__:
        setattr(cfg, k, getattr(bn.cfg, k))
    api_b = FakeApi(True)
    api_b.markets_list = list(api_n.markets_list)
    api_b.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                   for e, v in api_n.books.items()}
    api_b.inv, api_b.cash = dict(api_n.inv), cash
    if cash is not None:
        api_b.pnl = lambda: (api_b.log("pnl"), {"totalAccountValue": api_b.equity, "cashBalance": api_b.cash})[1]
    cfg.status_file = bn.cfg.status_file + ".base"
    bb_ = base.Bot(api_b, cfg)
    bb_.t["endDate"] = bn.t["endDate"]
    for x in bb_.ex.values():
        x.close = CLOSE
    bb_.refs = FakeRefs(dict(bn.refs.prices))
    return api_n, bn, api_b, bb_


if base is not None:
    ok_w, ok_q, ok_r, ok_s, diffs = True, True, True, True, []
    for inv in ({}, {"32": 3000, "31": -500}, {"11": 400, "12": -300, "42": 1500}, {"21": -800, "22": -900}):
        for cash in (50000.0, 0.0):
            api_n, bn, api_b, bb_ = pair(inv, cash)
            for _ in range(2):
                quiet(bn.cycle)
                quiet(bb_.cycle)
                bn.drain_writes(5)
                bb_.drain_writes(5)
            sn = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_n.wire]
            sb = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_b.wire]
            if sn != sb:
                ok_w = False
                diffs.append(("wire", inv, cash, sn[:2], sb[:2]))
            qn = {e: tuple(x.quote.__dict__.values()) for e, x in bn.ex.items()}
            qb = {e: tuple(x.quote.__dict__.values()) for e, x in bb_.ex.items()}
            if qn != qb:
                ok_q = False
                diffs.append(("quote", inv, cash))
            fv = fvs_of(bn)
            pd = sum(M.PARTY_SIGN.get(x.party, 0) * api_n.inv.get(e, 0.0) for e, x in bn.ex.items())
            if (bn.settlement_risk(dict(api_n.inv), fv, pd) != bb_.settlement_risk(dict(api_b.inv), fv, pd)
                    or bn.total_worst_case(dict(api_n.inv), fv) != bb_.total_worst_case(dict(api_b.inv), fv)):
                ok_r = False
            bn.write_status(True)
            bb_.write_status(True)
            with open(bn.cfg.status_file) as f:
                kn = set(json.load(f))
            with open(bb_.cfg.status_file) as f:
                kb = set(json.load(f))
            if kn != kb or set(bn.health) != set(bb_.health):
                ok_s = False
                diffs.append(("status", kn ^ kb))
    check("two full cycles send the same orders (4 books x cash 50k / 0)", ok_w, diffs[:1])
    check("decide / plan: every market's quote identical", ok_q, diffs[:1])
    check("settlement_risk and total_worst_case identical", ok_r)
    check("status.json / health keys identical (no 'basket' key while never used)", ok_s, diffs[:1])
    _, bn = basket_bot(enabled=False)
    inv = {"32": 3000, "31": -500, "11": 200}
    check("effective_inventory identical with no basket legs",
          bn.effective_inventory(inv) == base.Bot.effective_inventory(bn, inv))

# ============================================================================================ target math
print("--- target math")
api, b = basket_bot()
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "building", 1000.0, 100000.0
fl, cu, im, mu, full, ramp = b.basket_target(1000.0 + 2 * H, 100000.0, 100000.0)
check("floor = max(86k, 0.85 x peak 100k) = 86k; cushion 14k; full = min(5 x 14k, 80k, 85k) = 70k",
      (fl, cu, round(full)) == (86000.0, 14000.0, 70000), (fl, cu, full))
check("ramp: half way through the 4-h build -> 35k", abs(ramp - 35000.0) < 1e-6, ramp)
check("ramp: 0 at the switch-on, full after the build",
      b.basket_target(1000.0, 1e5, 1e5)[5] == 0.0 and abs(b.basket_target(1000.0 + 9 * H, 1e5, 1e5)[5] - 70000) < 1e-6)
b.basket_peak = 120000.0
fl, cu, _, _, full, _ = b.basket_target(1000.0 + 5 * H, 110000.0, 110000.0)
check("peak 120k: floor 0.85 x 120k = 102k, cushion 8k -> 40k", (fl, cu, round(full)) == (102000.0, 8000.0, 40000),
      (fl, cu, full))
b.basket_peak = 100000.0
b.basket_flows.extend([(1000.0 + 4 * H, 10000.0), (1000.0 + 4.5 * H, -2000.0), (1000.0 - 30 * H, 50000.0)])
fl, cu, im, _, full, _ = b.basket_target(1000.0 + 5 * H, 100000.0, 100000.0)
check("cushion net of own impact: 0.06 x net 8k bought in 24 h (older flows ignored) = 480", (im, cu) == (480.0, 13520.0),
      (im, cu))
b.basket_flows.clear()
b.cfg.basket_cap = 30000.0
check("cap 30k binds", abs(b.basket_target(1e9, 1e5, 1e5)[4] - 30000) < 1e-6)
b.cfg.basket_cap, b.cfg.basket_cap_frac = 80000.0, 0.5
check("cap_frac 0.5 x account 100k binds (50k)", abs(b.basket_target(1e9, 1e5, 1e5)[4] - 50000) < 1e-6)
b.cfg.basket_cap_frac = 0.85
check("cushion <= 0 -> target 0; no account value -> 0", b.basket_target(1e9, 85000.0, 85000.0)[4] == 0.0
      and b.basket_target(1e9, None, None)[4] == 0.0)
b.basket_test = "failed"
check("after a failed test: mult 1.5 -> 21k", abs(b.basket_target(1e9, 1e5, 1e5)[4] - 21000) < 1e-6)
b.basket_test = None
b.ex["32"].book = {"bids": [lvl(0.07, 100)], "asks": [lvl(0.09, 100)]}
liq = b.basket_liquidation({"32": 1000, "31": -1000}, 100000.0, {"32": 0.08, "31": 0.91})
check("liquidation = account - haircut (long at the bid, short at the ask): 1000 x 0.01 + 1000 x (0.92 - 0.91)",
      abs(liq - (100000 - 10 - 10)) < 1e-6, liq)

# ============================================================================================ selection
print("--- selection")
api, b = basket_bot()
fv = warm(b)
cands = b.basket_candidates(dict(api.inv), fv)
by = {c_["eid"]: c_ for c_ in cands}
check("races 3-5: YES on the longshot (ask 8c vs NO at 10c); race 6: NO on the favourite (8c vs YES at 12c)",
      set(by) == {"32", "42", "52", "61"} and by["32"]["yes"] and not by["61"]["yes"], sorted(by))
check("Ohio / Utah: nothing (no leg below max_ref)", not ({"11", "12", "21", "22"} & set(by)))
check("laggards first: own s_i ascending", [c_["s_i"] for c_ in cands] == sorted(c_["s_i"] for c_ in cands)
      and cands[0]["eid"] == "52", [(c_["eid"], round(c_["s_i"], 3)) for c_ in cands])
api, b = basket_bot(races={3: (0.080, 0.90), 4: (0.30, 0.70)})
fv = warm(b)
check("max_price: a longshot asked above 25c and a favourite bid below 75c are not bought",
      {c_["eid"] for c_ in b.basket_candidates({}, fv)} == {"32"})
api, b = basket_bot(races={3: (0.080, 0.90)}, extra_markets=(
    market("m71", "71", "Republican", "U.S. Senate"), market("m72", "72", "Democratic", "U.S. Senate"),
    market("m81", "81", "Republican", "Maine Senate"), market("m82", "82", "Independent", "Maine Senate")),
    extra_books={"71": fav_book(0.90), "72": ls_book(0.08), "81": fav_book(0.90), "82": ls_book(0.08)},
    extra_refs={"U.S. Senate|Republican": 0.97, "U.S. Senate|Democratic": 0.03,
                "Maine Senate|Republican": 0.97, "Maine Senate|Independent": 0.03})
for x in b.ex.values():
    x.close = CLOSE
fv = warm(b)
ids = {c_["eid"] for c_ in b.basket_candidates({}, fv)}
check("headline race (U.S. Senate) excluded; an independent longshot never bought (its race's favourite NO is the "
      "route only for a non-independent longshot)", ids == {"32"}, ids)
b.cfg.basket_exclude_headline = False
check("basket_exclude_headline False: the headline race qualifies", {"32", "72"} <= {c_["eid"] for c_ in
                                                                                      b.basket_candidates({}, fv)})
api, b = basket_bot(races={3: (0.080, 0.90), 4: (0.080, 0.90), 6: (0.120, 0.92)})
fv = warm(b)
check("a leg where we hold the OPPOSITE position (NO on the longshot): skipped (and so is that race's favourite NO)",
      {c_["eid"] for c_ in b.basket_candidates({"42": -500}, fv)} == {"32", "61"})
ids = {c_["eid"]: c_["yes"] for c_ in b.basket_candidates({"61": 200}, fv)}
check("YES held on a favourite: not shorted; that race falls back to YES on its longshot", "61" not in ids
      and ids.get("62") is True, ids)
b.cur_liquid.discard("32")
check("an illiquid Polymarket price: not a candidate", "32" not in {c_["eid"] for c_ in b.basket_candidates({}, fv)})
b.cur_refs.pop("41", None)
check("a race with a leg lacking Polymarket: skipped", "42" not in {c_["eid"] for c_ in b.basket_candidates({}, fv)})
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.ex["32"].book = {"bids": [lvl(0.07, 100)], "asks": []}
check("no ask on the longshot (and the favourite route dearer / absent): nothing", not b.basket_candidates({}, fv)
      or all(c_["eid"] != "32" for c_ in b.basket_candidates({}, fv)))
b.ex["32"].book = M.strip_own(api.full_book("32"), [])
b.ex["32"].verified = time.monotonic() - 10 * b.cfg.book_stale
check("a stale book: not a candidate", all(c_["eid"] != "32" for c_ in b.basket_candidates({}, fv)))

# ============================================================================================ sizing (pure planner)
print("--- sizing")
api, b = basket_bot()
fv = warm(b)
sched = b.basket_schedule()
nw = time.time()
b.basket_state, b.basket_on_wall, b.basket_peak = "building", nw - 2 * H, 100000.0
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("orders <= basket_max_orders_per_cycle (4), all adds", len(o) == 4 and all(x["add"] for x in o), o)
o32 = next(x for x in o if x["eid"] == "32")
lim = M.ceil_tick(0.080 + 0.005)
check("IOC limit = ask + slip on the grid (0.085)", o32["limit"] == lim and o32["buy"], o32)
check("first build: <= 0.75 x top-3 ask depth (12k) = 9k, <= depth within the limit (8k) -> 8k",
      o32["qty"] == 8000, o32)
o61 = next(x for x in o if x["eid"] == "61")
check("the NO route: a YES sale at bid - slip (0.915)", not o61["buy"] and o61["limit"] == 0.915, o61)
b.cfg.basket_min_legs = 200
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("leg cap = full x min(max_leg_frac, 1/min_legs): min_legs 200 -> 70k / 200 = 350 $ -> 4117 shares at 0.085",
      next(x for x in o if x["eid"] == "32")["qty"] == int(350 / 0.085 + 1e-9), o)
b.cfg.basket_min_legs, b.cfg.basket_max_leg_frac = 20, 0.004
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("max_leg_frac 0.004 x 70k = 280 $ per leg -> 3294 shares at 0.085",
      next(x for x in o if x["eid"] == "32")["qty"] == int(280 / 0.085 + 1e-9), o)
b.basket_legs = {"32": 2000.0}
o = b.basket_orders(nw, fv, fv, {"32": 2000}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("...less what the basket already holds there (2000 shares at its fair value)",
      next(x for x in o if x["eid"] == "32")["qty"] == int((280 - b.basket_leg_value("32", 2000.0, fv)) / 0.085 + 1e-9), o)
b.basket_legs = {}
b.cfg.basket_max_leg_frac = 0.08
b.basket_state = "tracking"
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("after the build: <= 0.25 x 12k = 3000 an hour", next(x for x in o if x["eid"] == "32")["qty"] == 3000)
b.basket_hours[("32", "asks")] = [nw - 600, 12000.0, 2500.0]
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("rolling hour: 2500 already bought -> 500 more", next(x for x in o if x["eid"] == "32")["qty"] == 500)
b.basket_hours[("32", "asks")] = [nw - 3700, 12000.0, 2500.0]
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 10 ** 6, sched)
check("...a new hour (window older than 3600 s): 3000 again", next(x for x in o if x["eid"] == "32")["qty"] == 3000)
b.basket_hours.clear()
b.basket_state = "building"
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 500.0, True, 10 ** 6, sched)
check("cash 500: the first (laggard) order 500 / 0.08 limit... = int(500 / 0.08) shares, nothing after",
      len(o) == 1 and o[0]["qty"] == int(500 / o[0]["limit"]), o)
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 10000.0, True, 10 ** 6, sched)
shorts = [x for x in o if not x["buy"]]
check("a short's cash = (1 - limit) x qty (the plan's total within the cash)",
      sum(x["qty"] * (x["limit"] if x["buy"] else 1 - x["limit"]) for x in o) <= 10000.0 + 1e-6 and shorts, o)
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 9, sched)
check("writes: 0.5 x 9 writes left / 3 per order -> 1 order", len(o) == 1, o)
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, True, 5, sched)
check("writes: 0.5 x 5 / 3 < 1 -> no order", o == [], o)
o = b.basket_orders(nw, fv, fv, {}, 35000.0, 70000.0, 1e6, False, 10 ** 6, sched)
check("adding refused (no fresh cash read / no adds near the exit): no buy", o == [], o)
b.basket_legs = {"32": 1000.0}
held = b.basket_value(fv)
o = b.basket_orders(nw, fv, fv, {"32": 1000}, held * 1.03, 70000.0, 1e6, True, 10 ** 6, sched)
check("within the 5% band of the target: no trade", o == [], o)
o = b.basket_orders(nw, fv, fv, {"32": 1000}, held * 0.5, 70000.0, 1e6, True, 10 ** 6, sched)
check("held above target + band (tracking down): a sale at bid - slip, <= the excess",
      len(o) == 1 and not o[0]["buy"] and not o[0]["add"] and 1 <= o[0]["qty"] <= 600, o)

# ============================================================================================ sending (fake exchange)
print("--- sending on the fake exchange")
api, b = basket_bot(races={3: (0.080, 0.90), 6: (0.120, 0.92)})
fv = warm(b)
check("before: the market maker quotes the longshot (our orders rest there)", any(o_["exchangeId"] == "32"
                                                                                for o_ in api.orders.values()))
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
n_calls, n_wire, inv0 = len(api.calls), len(api.wire), dict(api.inv)
traded = tick(b)
calls = [c_ for c_ in api.calls[n_calls:] if c_[0] in ("book", "cancel_all", "batch")]
seq = [calls[i:i + 4] for i, c_ in enumerate(calls) if c_[0] == "book"]
check("each leg: fresh book, cancel ours there, the order, cancel the leftover (in that order)", len(seq) == 2 and all(
    [x[0] for x in s_] == ["book", "cancel_all", "batch", "cancel_all"] and s_[1][1] == s_[3][1] == s_[0][1] for s_ in seq),
      calls)
check("bought: 32 (YES) and sold YES on 61 (the NO route), booked from quantityTraded",
      b.basket_legs.get("32", 0) > 0 and b.basket_legs.get("61", 0) < 0 and {"32", "61"} <= traded, b.basket_legs)
check("the position change matches the booking", api.inv.get("32", 0) - inv0.get("32", 0) == b.basket_legs["32"]
      and api.inv.get("61", 0) - inv0.get("61", 0) == b.basket_legs["61"], (api.inv, inv0, b.basket_legs))
check("nothing of ours rests on a basket leg afterwards (leftovers cancelled at once)",
      not [o_ for o_ in api.orders.values() if o_["exchangeId"] in ("32", "61")], api.orders)
ws = [w for w in api.wire[n_wire:] if w["exchangeId"] in ("32", "61")]
check("IOC-style: ttl take_order_ttl (10 s), price at the limit", ws and all(
    (M.parse_ts(w["expirationDate"]) - M.utcnow()).total_seconds() <= b.cfg.take_order_ttl + 1 for w in ws)
      and {w["price"] for w in ws} == {0.085, 0.915}, ws)
check("status: held $, legs, state, target in the basket fields", b.basket_info.get("held", 0) > 0
      and b.basket_status()["legs_count"] == 2 and b.basket_status()["state"] == "tracking")
check("flows recorded for the own-impact measure", sum(x for _, x in b.basket_flows) > 0)
cycle(b)
check("next cycle: decide does not quote the basket legs (NO_QUOTE) and nothing rests there",
      b.ex["32"].quote.bid is None and b.ex["32"].quote.ask is None
      and not [o_ for o_ in api.orders.values() if o_["exchangeId"] in ("32", "61")])
check("...the market maker still quotes the other markets", any(o_["exchangeId"] == "11" for o_ in api.orders.values()))

print("--- refusal paths")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
api.fail_book.add("32")
n0 = len(api.wire)
tick(b)
check("fresh book download fails: no order", len(api.wire) == n0 and not b.basket_legs)
api.fail_book.clear()
real_cancel = api.cancel_all
api.cancel_all = lambda tid, eid=None: False if eid == "32" else real_cancel(tid, eid)
tick(b)
check("our orders there cannot be confirmed cancelled: no order (never a possible self-cross)", len(api.wire) == n0)
api.cancel_all = real_cancel
b.api.writes_left = lambda: 2
tick(b)
check("write budget for 3 writes not there: nothing sent", len(api.wire) == n0)
del b.api.writes_left
b.cg_cash, b.cg_spent, b.cg_reserved = 10.0, 0.0, 0.0
tick(b)
check("cash 10 (< the 25 reserve): no buy", len(api.wire) == n0 and b.basket_info.get("refused") is None)
b.cg_cash = 50000.0
b.cg_read_at = time.monotonic() - 1000
tick(b)
check("a stale cash read (> 5 min): refused, nothing sent", len(api.wire) == n0
      and "cash" in (b.basket_info.get("refused") or ""), b.basket_info.get("refused"))
b.cg_read_at = time.monotonic()
api.books["32"]["asks"] = [lvl(0.095, 4000)]
tick(b)
check("the ask moved above the planned limit on the fresh book: no order", len(api.wire) == n0, api.wire[n0:])
api.books["32"] = ls_book(0.08)
b.cfg.basket_enabled = True
b.running = False
tick(b)
check("stopping (Ctrl+C): nothing sent", len(api.wire) == n0)
b.running = True
api_d, bd = basket_bot(races={3: (0.080, 0.90)}, live=False)
fvd = warm(bd)
bd.basket_state, bd.basket_on_wall, bd.basket_peak = "tracking", time.time() - 5 * H, 100000.0
n0d = len(api_d.wire)
tick(bd)
check("dry run: no basket order sent", len(api_d.wire) == n0d and not bd.basket_legs)

# ============================================================================================ exempt from reduce-only
print("--- reduce-only and the risk model")
api, b = basket_bot(races={3: (0.080, 0.90)}, max_worst_case_frac=0.01)
api.inv.update({"21": 3000, "22": -3000})
fv = warm(b)
check("(the bot is in reduce-only)", b.global_reduce)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
tick(b)
check("the basket still buys in reduce-only (exempt, with the stress model)", b.basket_legs.get("32", 0) > 0)
inv = dict(api.inv)
fv = fvs_of(b)
b.basket_legs = {"32": 2000.0}
inv["32"] = 2500.0
pd = sum(M.PARTY_SIGN.get(x.party, 0) * inv.get(e, 0.0) for e, x in b.ex.items())
rest = dict(inv, **{"32": 500.0})
pd_rest = pd - M.PARTY_SIGN["Democratic"] * 2000
b.cfg.basket_enabled = False
plain_w, plain_r = b.total_worst_case(rest, fv), b.settlement_risk(rest, fv, pd_rest)
full_w = b.total_worst_case(inv, fv)
b.cfg.basket_enabled = True
val32 = max(fv["32"], b.cur_book_fvs["32"])     # (P9 red team: valued at the higher of fair value / book price)
stress = 0.4 * 2000 * val32
check("total_worst_case = the rest's + 0.4 x the basket's $ value", abs(b.total_worst_case(inv, fv) - (plain_w + stress)) < 1e-6,
      (b.total_worst_case(inv, fv), plain_w, stress))
check("settlement_risk = the rest's (party delta less the basket's) + the stress",
      abs(b.settlement_risk(inv, fv, pd) - (plain_r + stress)) < 1e-6)
b.cfg.basket_enabled = False
check("basket_enabled off: the full settlement counting (unchanged)", b.total_worst_case(inv, fv) == full_w)
b.cfg.basket_enabled = True
b.cfg.basket_stress_frac = 1.0
check("stress_frac 1: the basket at its full $ value", abs(b.total_worst_case(inv, fv) - (plain_w + 2000 * val32)) < 1e-6)
b.cfg.basket_stress_frac = 0.4
eff = b.effective_inventory({"31": 0, "32": 2500})
check("effective_inventory (skew) ignores the basket's shares: race 3 nets 500, not 2500", eff["31"] == -500
      and eff["32"] == 500, eff)

# ============================================================================================ untouched by others
print("--- basket legs untouched by takes, arbitrage, tilt exits")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_legs = {"32": 1000.0, "31": -10.0}
api.inv.update({"32": 1000, "31": -10})
x = b.ex["32"]
x.inv, x.ref = 1000.0, 0.03
b.tilt_exposure = -500.0
check("tilt_exit_side: None on a basket leg", b.tilt_exit_side(x) is None)
b.basket_legs = {}
check("(without the basket the same leg is a tilt exit)", b.tilt_exit_side(x) == "ask")
b.basket_legs = {"32": 1000.0}
b.refs.version += 1
for e_ in b.ex.values():
    e_.take_dir, e_.take_since = 0, 0.0
b.cfg.take_enabled, b.cfg.take_edge, b.cfg.take_confirm_seconds = True, 0.02, 0.0
api.books["32"]["bids"] = [lvl(0.30, 1000)]
x.book = M.strip_own(api.full_book("32"), [])
n0 = len(api.wire)
taken = quiet(b.take_stale_quotes, b.cur_refs, b.cur_liquid, fv, dict(api.inv), {}, False, 0.0, time.monotonic())
taken = quiet(b.take_stale_quotes, b.cur_refs, b.cur_liquid, fv, dict(api.inv), {}, False, 0.0, time.monotonic()
              ) if not taken else taken
check("take_stale_quotes: no take on a basket leg (a 30c bid vs Polymarket 3c)", "32" not in taken
      and not [w for w in api.wire[n0:] if w["exchangeId"] == "32"])
b.cfg.hold_target_hours, b.hold_open_at = 1.0, 0.0
b.lots["32"] = [[1000.0, time.time() - 10 * H]]
check("hold_take_plan: no aged take on a basket leg", all(p["eid"] != "32" for p in b.hold_take_plan(
    dict(api.inv), fv, time.monotonic())))
b.cfg.hold_target_hours = 0.0
b.cfg.pair_unwind_enabled = b.cfg.arb_enabled = True
api.inv.update({"31": 1000})
api.books["31"]["bids"], api.books["32"]["bids"] = [lvl(0.95, 1000)], [lvl(0.30, 1000)]
for e_ in ("31", "32"):
    b.ex[e_].book = M.strip_own(api.full_book(e_), [])
n0 = len(api.wire)
done = quiet(b.take_arbitrage, dict(api.inv), fv, {}, time.monotonic())
check("take_arbitrage / pair unwind: a race with a basket leg is left alone (bids sum 1.25)", not done
      and len(api.wire) == n0)
b.basket_legs = {}
done = quiet(b.take_arbitrage, dict(api.inv), fv, {}, time.monotonic())
check("(without the basket that race is traded)", done)

# ============================================================================================ 36-h test
print("--- the 36-h momentum test")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
on = time.time() - 40 * H
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", on, 100000.0
b.basket_s_hist.extend([(on + 20 * H, 0.10)])
b.tilt_s = 0.16
ALERTS.clear()
tick(b)
check("tilt_s 0.16 >= 0.15 and up 0.06 in 12 h: passed, mult 5", b.basket_test == "passed" and b.basket_info["mult"] == 5.0)
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", on, 100000.0
b.basket_s_hist.extend([(on + 20 * H, 0.10)])
b.tilt_s = 0.12
tick(b)
check("tilt_s 0.12 < 0.15: failed -> state cut, mult 1.5, alert", b.basket_test == "failed" and b.basket_state == "cut"
      and b.basket_info["mult"] == 1.5 and any("test FAILED" in a for a in ALERTS), (b.basket_state, ALERTS))
n_al = len(ALERTS)
tick(b)
check("latched (no second alert, still cut)", b.basket_state == "cut" and len(ALERTS) == n_al)
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", on, 100000.0
b.basket_s_hist.extend([(on + 20 * H, 0.20)])
b.tilt_s = 0.18
tick(b)
check("12-h change <= 0 (0.20 -> 0.18): failed", b.basket_test == "failed")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", on, 100000.0
b.tilt_s = 0.30
tick(b)
check("no 12-h history: failed (refuse in doubt)", b.basket_test == "failed")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 10 * H, 100000.0
b.tilt_s = 0.01
tick(b)
check("before 36 h: not judged", b.basket_test is None and b.basket_state == "tracking")
b.basket_legs = {"32": 400000.0}
api.inv["32"] = 400000
b.basket_test, b.basket_state, b.basket_target_frozen = "failed", "cut", None
o = b.basket_orders(time.time(), fv, fv, dict(api.inv), b.basket_target(time.time(), 1e5, 1e5)[5],
                    21000.0, 1e6, True, 10 ** 6, b.basket_schedule())
check("cut: a basket above the cut target is sold down (sales planned)", o and all(not x["add"] for x in o), o)

# ============================================================================================ kill
print("--- the kill: floor, drawdown, confirmation, latch, restart")
d = tempfile.mkdtemp()
api, b = basket_bot(races={3: (0.080, 0.90)}, status_dir=d)
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.basket_legs = {"32": 4000.0}
api.inv["32"] = 4000
t0 = M.utcnow()
ALERTS.clear()
tick(b, now=t0, equity=84000.0)
check("drawdown breach (liq < 0.85 x peak 100k / floor 86k): not killed at once", b.basket_state == "tracking"
      and b.basket_breach_since is not None)
tick(b, now=t0 + timedelta(seconds=30), equity=100000.0)
check("...recovered within the confirmation: the breach clears", b.basket_breach_since is None and not b.basket_killed)
tick(b, now=t0 + timedelta(seconds=40), equity=84000.0)
legs_before = dict(b.basket_legs)
tick(b, now=t0 + timedelta(seconds=40 + M.Bot.BASKET_KILL_CONFIRM_SECONDS), equity=84000.0)
check("breach lasting 120 s: KILLED, latched, alerted once", b.basket_state == "killed" and b.basket_killed
      and sum("KILLED" in a for a in ALERTS) == 1, (b.basket_state, ALERTS))
check("kill_start = the legs at the kill (the breach's target-0 tracking sales already started)",
      b.basket_kill_start == legs_before and 0 < legs_before.get("32", 0) <= 4000, (b.basket_kill_start, legs_before))
K0 = b.basket_kill_start["32"]
n0 = len(api.wire)
tick(b, now=t0 + timedelta(seconds=40 + 120 + 3600), equity=84000.0)
check("an hour into the 2-h kill sale: half sold as a taker (bid - slip), no buy",
      api.wire[n0:] and all(w["action"] == "sell" for w in api.wire[n0:])
      and K0 / 2 - 2 <= b.basket_legs.get("32", 0) <= K0 / 2 + 1,
      (api.wire[n0:], b.basket_legs))
b.write_status(True)
api2 = FakeApi(True)
api2.markets_list, api2.books, api2.inv = api.markets_list, api.books, dict(api.inv)
cfg2 = b.cfg
b2 = M.Bot(api2, cfg2)
check("restart: killed latch, legs, kill start and peak restored", b2.basket_killed and b2.basket_state == "killed"
      and b2.basket_legs == b.basket_legs and b2.basket_kill_start == {"32": K0} and b2.basket_peak == 100000.0,
      (b2.basket_state, b2.basket_legs))
b2.t["endDate"] = M.iso(CLOSE)
for x in b2.ex.values():
    x.close = CLOSE
b2.refs = b.refs
api2.cash = 50000.0
api2.pnl = lambda: (api2.log("pnl"), {"totalAccountValue": api2.equity, "cashBalance": 50000.0})[1]
cycle(b2)                                         # (basket on: a warm-up with it off would release the legs)
check("...first cycle after the restart: the killed legs are not quoted", not [o_ for o_ in api2.orders.values()
                                                                             if o_["exchangeId"] == "32"])
tick(b2, now=t0 + timedelta(seconds=40 + 120 + 2.1 * 3600), equity=100000.0)
check("after the restart (basket_enabled still true): stays killed, the rest sold by the end of the 2 h, nothing bought",
      b2.basket_state == "killed" and not b2.basket_legs and all(w["action"] == "sell" for w in api2.wire
                                                                  if w["exchangeId"] == "32"),
      (b2.basket_state, b2.basket_legs, [w for w in api2.wire if w["exchangeId"] == "32"]))
b2.cfg.basket_enabled = False
tick(b2)
b2.cfg.basket_enabled = True
tick(b2)
check("switched off then on WITHOUT the overrides file saying false (e.g. defaults after a bad read): still killed",
      b2.basket_killed and b2.basket_state == "killed")
b2.cfg.basket_enabled = False
b2.overrides = {"basket_enabled": False}
tick(b2)
b2.cfg.basket_enabled = True
b2.overrides = {"basket_enabled": True}
tick(b2)
check("the owner sets basket_enabled false, then true: the latch clears, a fresh basket builds",
      not b2.basket_killed and b2.basket_state == "building")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.cfg.basket_floor = 99000.0
t0 = M.utcnow()
tick(b, now=t0, equity=98000.0)
tick(b, now=t0 + timedelta(seconds=130), equity=98000.0)
check("kill on the floor (liq 98k < floor 99k, drawdown only 2%)", b.basket_state == "killed")

# ============================================================================================ exit schedule
print("--- the exit schedule and the T-72h backstop")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
sc = b.basket_schedule()
ex_w = M.parse_utc_setting(b.cfg.basket_exit_utc).timestamp()
check("schedule: start = the exit date, end + 24 h, no adds 7 days before, backstop = close - 72 h",
      abs(sc["start"] - ex_w) < 1 and abs(sc["end"] - ex_w - 24 * H) < 1 and abs(sc["no_add"] - ex_w + 7 * 86400) < 1
      and abs(sc["backstop"] - CLOSE.timestamp() + 72 * H) < 1 and sc["valid"], sc)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
n0 = len(api.wire)
tick(b, now=M.utcnow() + timedelta(days=14))
check("inside the no-add window: no buy (refused)", len(api.wire) == n0 and "no adds" in (b.basket_info.get("refused") or ""))
b.basket_legs = {"32": 4000.0}
api.inv["32"] = 4000
start = M.datetime.fromtimestamp(ex_w, M.timezone.utc)
tick(b, now=start + timedelta(seconds=1))
check("at the exit date: state exiting, exit_start = the legs", b.basket_state == "exiting"
      and b.basket_exit_start == {"32": 4000.0})
tick(b, now=start + timedelta(hours=12))
check("half way through 24 h: half sold", 1900 <= b.basket_legs.get("32", 0) <= 2000, b.basket_legs)
api.books["32"] = ls_book(0.08)
for _ in range(3):
    b.ex["32"].book = M.strip_own(api.full_book("32"), [])
    b.ex["32"].verified = time.monotonic()
    tick(b, now=start + timedelta(hours=24, seconds=5))
check("at the end: all sold, state done", not b.basket_legs and b.basket_state == "done", (b.basket_legs, b.basket_state))
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.cfg.basket_exit_utc = M.iso(CLOSE - timedelta(hours=10))       # (set in code: past the backstop)
sc = b.basket_schedule()
check("an exit date after the backstop: the sale starts exit_hours before it and ends at it",
      sc["end"] == sc["backstop"] and sc["start"] == sc["backstop"] - 24 * H, sc)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.basket_legs = {"32": 4000.0}
api.inv["32"] = 4000
for _ in range(3):
    b.ex["32"].book = M.strip_own(api.full_book("32"), [])
    b.ex["32"].verified = time.monotonic()
    tick(b, now=M.datetime.fromtimestamp(sc["backstop"] + 60, M.timezone.utc))
check("past the backstop: everything sold (no setting keeps it)", not b.basket_legs and b.basket_state == "done")
b.cfg.basket_exit_hours = 0.0                                     # (below the live range, set in code)
check("exit hours can never be 0 (at least 1 h)", b.basket_schedule()["end"] - b.basket_schedule()["start"] == H)
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.cfg.basket_exit_utc = "18 Oct"
ALERTS.clear()
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
n0 = len(api.wire)
tick(b)
tick(b)
sc = b.basket_schedule()
check("invalid exit date: no buy, alert once; the backstop schedule still stands",
      len(api.wire) == n0 and sum("basket_exit_utc" in a for a in ALERTS) == 1 and not sc["valid"]
      and sc["start"] == sc["backstop"] - 24 * H, (ALERTS, sc))
api, b = basket_bot(races={3: (0.080, 0.90), 4: (0.080, 0.90)})
fv = warm(b)
b.ex["42"].close = b.ex["41"].close = M.utcnow() + timedelta(hours=72 + 12)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.basket_legs = {"42": 4000.0}
api.inv["42"] = 4000
check("a leg whose own market closes sooner: its own backstop schedule (half way: hold frac 0.5)",
      abs(b.basket_hold_frac("42", time.time(), b.basket_schedule()) - 0.5) < 0.01)
check("...and it is not a candidate to add", all(x["eid"] != "42" or not x["add"] for x in b.basket_orders(
    time.time(), fv, fv, dict(api.inv), 35000.0, 70000.0, 1e6, True, 10 ** 6, b.basket_schedule())))

# ============================================================================================ switch off / on, persistence
print("--- switch off (release), persistence, full cycles")
d = tempfile.mkdtemp()
api, b = basket_bot(races={3: (0.080, 0.90)}, status_dir=d)
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 101000.0
b.basket_test = "passed"
tick(b)
legs = dict(b.basket_legs)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)["basket"]
check("status.json basket: state, legs, peak, switch-on, test, held, target, floor, cushion, impact",
      all(k in st for k in ("state", "legs", "peak", "switched_on_wall", "test", "held", "target", "floor", "cushion",
                            "impact", "legs_count", "killed")) and st["legs"] == legs)
b3 = M.Bot(FakeApi(True), b.cfg)
check("restart: legs, state, peak, switch-on and test restored", b3.basket_legs == legs and b3.basket_state == "tracking"
      and b3.basket_peak == b.basket_peak and b3.basket_on_wall == b.basket_on_wall and b3.basket_test == "passed")
check("the 2-hourly summary line carries ' | basket $X (N legs, state)'", "basket $" in (b.summary_ops_line(1e5) or "")
      and "tracking" in b.summary_ops_line(1e5))
b.cfg.basket_enabled = False
cycle(b)
check("switched off: legs released (state 'off (N legs released)'), no forced sale",
      not b.basket_legs and b.basket_state == "off" and "released" in b.basket_last_action
      and api.inv.get("32") == legs.get("32"))
cycle(b)
check("...and the market maker quotes that market again", any(o_["exchangeId"] == "32" for o_ in api.orders.values()))
api, b = basket_bot(races={3: (0.080, 0.90), 4: (0.085, 0.90), 6: (0.12, 0.92)})
cycle(b)
check("full cycle, switched on: building, ramp 0 -> nothing bought yet", b.basket_state == "building" and not b.basket_legs)
b.basket_on_wall -= 2 * H
cycle(b)
check("full cycle 2 h later: basket bought (takes), its legs not quoted", b.basket_legs and all(
    o_["exchangeId"] not in b.basket_legs for o_ in api.orders.values()), (b.basket_legs, b.basket_info))
check("...within the ramped target (+ one order's slack)", b.basket_value(fvs_of(b)) <= b.basket_info["target"] * 1.05 + 1,
      b.basket_info)

# ============================================================================================ red team
print("--- red team")
api, b = basket_bot()
fv = warm(b)
b.cfg.reduce_no_as_sell = False
ids = {c_["eid"]: c_["yes"] for c_ in b.basket_candidates({}, fv)}
check("covered NO sales off: never the favourite-NO route (its exit would need cash); race 6 falls back to YES",
      "61" not in ids and ids.get("62") is True, ids)
b.cfg.reduce_no_as_sell = True
ids = {c_["eid"] for c_ in b.basket_candidates({"62": -100}, fv)}
check("NO held on the longshot: neither its YES (opposite) nor the favourite's NO (a NO+NO set) in that race",
      not ({"61", "62"} & ids), ids)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
for e_ in ("32", "42", "52", "61"):
    b.ex[e_].writes = 1
n0 = len(api.wire)
tick(b)
check("a write of ours in flight on the leg (busy): skipped (it could land after our cancel)", len(api.wire) == n0
      and not b.basket_legs)
for e_ in ("32", "42", "52", "61"):
    b.ex[e_].writes = 0
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.cfg.basket_cap = 0.0                            # (no trading: the peak alone)
b.basket_state, b.basket_on_wall = "tracking", time.time() - 5 * H
t0 = M.utcnow()
tick(b, now=t0, equity=100000.0)
tick(b, now=t0 + timedelta(seconds=10), equity=130000.0)
tick(b, now=t0 + timedelta(seconds=20), equity=100000.0)
check("a single glitchy high liquidation read does not raise the peak (no false kill after it)",
      b.basket_peak < 101000 and b.basket_breach_since is None, b.basket_peak)
for k in range(0, 200, 20):
    tick(b, now=t0 + timedelta(seconds=300 + k), equity=110000.0)
check("a value held for the confirmation time raises the peak", 109000 < b.basket_peak < 111000, b.basket_peak)
b.basket_killed, b.basket_state = True, "killed"
b.cfg.basket_enabled = False
b.overrides = {}
tick(b)
check("killed, then off by default (not the file): released, latch kept", b.basket_state == "off" and b.basket_killed)
cycle(b)
b.overrides = {"basket_enabled": False}
cycle(b)
b.cfg.basket_enabled, b.overrides = True, {"basket_enabled": True}
cycle(b)
check("...later the file says false (state already off: still observed by the cycle), then true: latch cleared",
      not b.basket_killed and b.basket_state == "building", (b.basket_killed, b.basket_state))
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.basket_legs = {"32": 3000.0}
api.inv["32"] = 3000
n0 = len(api.wire)
tick(b, equity=None)
check("no account value: no target, no tracking sale (never a sale to 0 on missing data), no buy",
      len(api.wire) == n0 and b.basket_legs == {"32": 3000.0} and b.basket_info["target"] is None, b.basket_info)
ALERTS.clear()
real_tick = b.basket_tick
b.basket_tick = lambda *a, **k: 1 / 0
n0 = len(api.wire)
cycle(b)
check("a crashing basket tick: the cycle completes, the market maker still quotes, one alert",
      any(o_["exchangeId"] == "11" for o_ in api.orders.values()) and "selftest_state" in b.health
      and sum("crashed" in a for a in ALERTS) == 1, ALERTS)
b.basket_tick = real_tick
api, b = basket_bot(races={6: (0.120, 0.92)}, cash=0.0)
api.inv["61"] = -3000
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "exiting", time.time() - 50 * H, 100000.0
b.basket_legs, b.basket_exit_start = {"61": -3000.0}, {"61": -3000.0}
b.cfg.basket_exit_utc = M.iso(M.utcnow() - timedelta(hours=30))
b.cg_cash, b.cg_spent, b.cg_reserved = 0.0, 0.0, 0.0
n0 = len(api.wire)
tick(b)
w61 = [w for w in api.wire[n0:] if w["exchangeId"] == "61"]
check("exit of a short (NO) leg at 0 cash: bought back as a covered 'sell NO' (no cash), state done",
      w61 and all(w["side"] == "no" and w["action"] == "sell" for w in w61) and not b.basket_legs
      and b.basket_state == "done" and api.inv.get("61") == 0, (w61, b.basket_legs, api.inv.get("61")))

# ============================================================================================ P9 red team (REDTEAM.md)
print("--- P9 red team (analysis/p9/REDTEAM.md)")
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
inv = dict(api.inv)
b.update_tilt(fv, b.cur_refs, b.cur_liquid, inv, time.monotonic())
te0 = b.tilt_exposure
b.basket_legs = {"32": 20000.0}
b.update_tilt(fv, b.cur_refs, b.cur_liquid, dict(inv, **{"32": inv.get("32", 0) + 20000}), time.monotonic())
check("RT-1: tilt_exposure leaves the basket's shares out (a long-tilt basket never flips the Package 8 exits)",
      b.tilt_exposure == te0 and b.tilt_exposure_basket < -9000, (te0, b.tilt_exposure, b.tilt_exposure_basket))
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
b.cfg.tilt_exit_take = True
b.tilt_exit_takes = lambda *a, **k: {"32"}       # (a tilt exit / take traded the longshot this cycle)
n0 = len(api.wire)
cycle(b)
check("RT-2: no basket add on a market another feature traded this cycle (never a sell then a buy there)",
      not [w for w in api.wire[n0:] if w["exchangeId"] == "32"] and "32" not in b.basket_legs, api.wire[n0:])
b.pp[race_name(3)] = {"leg": "31", "other": "32", "sign": 1, "base_x": 0}   # (a passive pair unwind running there)
o = b.basket_orders(time.time(), fv, fv, dict(api.inv), 10000.0, 70000.0, 1e6, True, 10 ** 6, b.basket_schedule())
check("RT-2: ...nor in a race with a passive pair unwind running", not [x for x in o if x["eid"] == "32"], o)
b.pp.clear()
o = b.basket_orders(time.time(), fv, fv, dict(api.inv), 10000.0, 70000.0, 1e6, True, 10 ** 6, b.basket_schedule(),
                    skip={race_name(3)})
check("RT-2: ...nor in a race arbitrage / a follow-up acted on (skip by race)", not o, o)
api, b = basket_bot(races={3: (0.080, 0.90)})
api.inv["32"] = 500
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
n0 = len(api.wire)
tick(b)
check("RT-3: an ordinary long on the leg is ADOPTED by the first add (a basket leg is never quoted: it would sit "
      "unmanaged past the exit / kill / backstop)", b.basket_legs.get("32") == api.inv.get("32") and api.inv["32"] > 500,
      (b.basket_legs, api.inv.get("32")))
o = b.basket_orders(time.time(), fv, fv, {"32": 4000}, 1e6, 10000.0, 1e6, True, 10 ** 6, b.basket_schedule())
b.basket_legs = {}
o2 = b.basket_orders(time.time(), fv, fv, {"32": 4000}, 1e6, 10000.0, 1e6, True, 10 ** 6, b.basket_schedule())
check("RT-3: ...and the leg cap counts the position held in the add's direction (4000 held: room 500 $ - 4000 x 0.07)",
      not [x for x in o2 if x["eid"] == "32"] or next(x for x in o2 if x["eid"] == "32")["qty"] * 0.085 <= 500 - 4000 * 0.07 + 1,
      o2)
api, b = basket_bot(races={3: (0.080, 0.90)})
fv = warm(b)
b.basket_state, b.basket_on_wall, b.basket_peak = "tracking", time.time() - 5 * H, 100000.0
low = dict(fv, **{"32": 0.03, "31": 0.97})       # fair value leaned to Polymarket far under the price paid (~0.08)
b.basket_legs = {"32": 10000.0}
o = b.basket_orders(time.time(), low, fv, {"32": 10000}, 600.0, 70000.0, 1e6, True, 10 ** 6, b.basket_schedule())
check("RT-4: held valued at the higher of fair value / book price: 10000 x ~0.07 >= a 600 $ target, no more bought",
      o and not [x for x in o if x["add"]], o)
b.basket_legs = {"32": 10000.0}
b.pos_marks.update({"32": 0.08, "22": 0.5})
for e_ in ("32", "22"):
    b.ex[e_].book = {"bids": [], "asks": [lvl(0.09, 100)]}
    b.ex[e_].verified = time.monotonic()
liq = b.basket_liquidation({"32": 10000, "22": 1000}, 100000.0, fv)
check("RT-6: a basket leg whose fresh book lost its bids counts as sold at 0 in the kill's liquidation value (not "
      "at its mark); an ordinary position keeps its mark (as ops_fields)", abs(liq - (100000.0 - 800.0)) < 1e-6, liq)
b.ex["32"].verified = time.monotonic() - 10 * b.cfg.book_stale
check("RT-6: ...a stale book proves nothing: the mark kept", b.basket_liquidation({"32": 10000}, 1e5, fv) == 1e5)
api7, b7 = basket_bot(races={6: (0.120, 0.92)}, cash=0.0)
api7.inv["61"] = -3000
warm(b7)
b7.cfg.reduce_no_as_sell = False                 # (the exchange refused covered NO sales this run, or before the test)
b7.basket_state, b7.basket_on_wall, b7.basket_peak = "killed", time.time() - 50 * H, 100000.0
b7.basket_killed, b7.basket_killed_wall = True, time.time() - 3 * H
b7.basket_legs, b7.basket_kill_start = {"61": -3000.0}, {"61": -3000.0}
b7.cg_cash, b7.cg_spent, b7.cg_reserved, b7.cg_read_at = 0.0, 0.0, 0.0, time.monotonic()
ALERTS.clear()
n0 = len(api7.wire)
tick(b7)
tick(b7)
check("RT-7: a kill sale the cash gate blocks (a short bought back as a YES purchase at 0 cash) is ALERTED once",
      len(api7.wire) == n0 and sum("cannot go out" in a for a in ALERTS) == 1 and b7.basket_legs, ALERTS)
b.cfg.basket_mult, b.cfg.basket_mult_after_fail = 2.0, 8.0
b.basket_test = "failed"
check("RT-5: a failed test never RAISES the multiplier (mult_after_fail 8 > mult 2 -> 2)",
      b.basket_target(1e9, 1e5, 1e5)[3] == 2.0, b.basket_target(1e9, 1e5, 1e5))

# ============================================================================================ py_compile 3.10
print("--- Python 3.10 syntax")
py310 = shutil.which("python3.10") or (os.path.exists("/root/.local/bin/python3.10") and "/root/.local/bin/python3.10")
if py310:
    r = subprocess.run([py310, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py"),
                        os.path.abspath(__file__)], capture_output=True, text=True, timeout=120)
    check("py_compile under Python 3.10 (mm_bot.py and this file)", r.returncode == 0, r.stderr[-300:])
else:
    print("    (no python3.10 here: skipped)")

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
