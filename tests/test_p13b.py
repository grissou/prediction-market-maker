"""
Offline tests for Package 13 part B (analysis/p12/SPEC_P13_AGGRESSIVE.md sections 4-5):
4 tilt_harvest_ladder: resting YES asks on longshots (p <= 0.10) at the best ask + harvest_offsets, YES bids on
  favourites (p >= 0.90) at the best bid - offsets, only where the edge per $ clears harvest_min_edge;
  harvest_level_usd of collateral a level; the per-market / per-race collateral caps (every bucket); the cash gate (and
  the election holdback); never through the book or our own orders; re-quoted on a 1c move of a level or of p, else
  every harvest_requote_s; pulled on every trigger; the market maker leaves the ladder's side alone; fills are value
  positions, journal "HARVEST fill", fill class "harvest".
5 election_night: the holdback (reserved only from election_holdback_from_utc), calls (10-minute persistence, resolved,
  un-call), takes on called races only (both sides, immediate-or-cancel, caps, holdback, cash gate, batched), not
  blocked by the pre-close windows while close_override_utc keeps the market open, the quoter reduce-only on called
  races, status / restart, fill class "election".
Also: every setting validates with its range; flags off identical to the branch head with Package 13 A (efa7dfe) on a
grid (wire orders, quotes, alloc_plan, status / health keys and values); no duplicate orders across cycles; nothing
crashes on empty books, missing Polymarket references or unknown markets; py_compile under python3.10.

Run:  python tests/test_p13b.py      (exit code 0 = all passed)
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
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market, run_cycles      # noqa: E402
import mm_bot as M                                                            # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "efa7dfe"                              # the branch head with Package 13 A, before Package 13 B


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)
PAST = "2026-10-02T00:00:00Z"                     # (inside the date settings' range, before today)


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# A: Alpha  A1 favourite p 0.98 (book 0.92 / 0.93) / A2 longshot p 0.02 (book 0.06 / 0.08)
# B: Beta   B1 0.88 / B2 0.12 (the middle: no ladder)          G: Gamma  G1 0.80 / G2 0.20 (no ladder)
# D: Delta  D1 favourite p 0.90 (0.89 / 0.91) / D2 longshot p 0.10 (0.11 / 0.13)
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate"}
ALPHA = {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02}


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


def mk_bot(inv=None, books=None, refs=None, cash=50000.0, live=True, races=None, equity=100000.0, **cfg):
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
    api, b = make_bot(live=live, books=base, extra_markets=tuple(mk))
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


FLAGS = ("tilt_harvest_ladder", "election_night", "buckets_enabled", "momentum_enabled", "aggressive_value")


def warm(b):
    """One quiet cycle with every Package 13 flag off (fair values, Polymarket, cash read), then a clean slate."""
    keep = {k: getattr(b.cfg, k) for k in FLAGS}
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


def agg(book):
    """The fake's full book with its levels summed by price (as the exchange shows it)."""
    out = {}
    for key, rev in (("bids", True), ("asks", False)):
        tot = {}
        for x in book.get(key) or []:
            tot[M.rnd(x["price"])] = tot.get(M.rnd(x["price"]), 0) + x["quantity"]
        out[key] = [{"price": p, "quantity": q} for p, q in sorted(tot.items(), reverse=rev)]
    return out


def fresh(b):
    now_m = time.monotonic()
    mine = b.orders_by_eid(M.utcnow())
    for x in b.ex.values():
        x.book = M.strip_own(agg(b.api.full_book(x.eid)), mine.get(x.eid, [])) if x.eid in b.api.books else x.book
        x.verified = now_m


def hv(b, skip=(), at=None):
    """One harvest tick on fresh books and the current positions."""
    time.sleep(0.002)
    fresh(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.hv_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def el(b, at=None, skip=()):
    time.sleep(0.002)
    fresh(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.el_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def ours(api, eid):
    return api.ours(eid)


def levels(b, e):
    pl = b.hv_plans.get(e) or {}
    return [(px, n) for _, px, n, _, _ in pl.get("levels", [])]


def wire(api, eid=None):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in api.wire
            if eid is None or o["exchangeId"] == eid]


def call(b, e, side="win", since=0.0):
    b.el_calls[e] = {"side": side, "since": since, "called": True, "resolved": False}


# ============================================================================================ settings
print("--- settings")
D = M.Config()
SPEC = {"tilt_harvest_ladder": (False, (False, True)), "harvest_offsets": ((0.0, 0.02, 0.04, 0.06), (0.0, 0.2)),
        "harvest_level_usd": (3000.0, (0.0, 50000.0)), "harvest_min_edge": (0.08, (0.0, 2.0)),
        "harvest_max_markets": (237, (0, 1000)), "harvest_writes_frac": (0.4, (0.0, 1.0)),
        "harvest_requote_s": (900.0, (60.0, 7200.0)), "election_night": (False, (False, True)),
        "election_holdback_usd": (20000.0, (0.0, 100000.0)),
        "election_holdback_from_utc": ("2026-11-03T12:00:00Z", ("2026-10-01T00:00:00Z", "2026-11-07T00:00:00Z")),
        "election_start_utc": ("2026-11-03T23:00:00Z", ("2026-10-01T00:00:00Z", "2026-11-07T00:00:00Z")),
        "election_called_p": (0.98, (0.90, 0.999)), "election_called_min": (10.0, (0.0, 120.0)),
        "election_take_max_price": (0.95, (0.5, 0.999))}
for k, (dflt, rng) in SPEC.items():
    ok_d = getattr(D, k) == dflt and type(getattr(D, k)) is type(dflt)
    ok_r = M.OVERRIDABLE.get(k) == rng
    if k.endswith("_utc"):
        g1, b1 = M.validate_overrides({k: "2026-11-03T20:00:00Z"}, D)
        g2, b2 = M.validate_overrides({k: ""}, D)
        g3, b3 = M.validate_overrides({k: "2026-11-08T00:00:00Z"}, D)
        g4, b4 = M.validate_overrides({k: "2026-11-03 20:00"}, D)
        ok_v = (g1 == {k: "2026-11-03T20:00:00Z"} and g2 == {k: ""} and not g3 and b3 and not g4 and b4
                and k in M.DATE_SETTINGS and k in M.DATE_EMPTY_OK)
    elif isinstance(dflt, bool):
        g1, b1 = M.validate_overrides({k: True}, D)
        g2, b2 = M.validate_overrides({k: 1}, D)
        ok_v = g1 == {k: True} and not b1 and not g2 and b2
    elif isinstance(dflt, tuple):
        g1, b1 = M.validate_overrides({k: [0, 0.01, 0.03]}, D)
        g2, b2 = M.validate_overrides({k: [0, 0.25]}, D)
        g3, b3 = M.validate_overrides({k: []}, D)
        g4, b4 = M.validate_overrides({k: "0,0.02"}, D)
        ok_v = g1 == {k: (0.0, 0.01, 0.03)} and not g2 and b2 and not g3 and b3 and not g4 and b4
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
_f = list(M.Config.__dataclass_fields__)
check("one contiguous block after mom_kill_frac in Config",
      _f[_f.index("mom_kill_frac") + 1:][:len(SPEC)] == list(SPEC), _f[_f.index("mom_kill_frac") + 1:][:4])
check("Bot.P13B_KEYS names the status keys Package 13 B adds", tuple(M.Bot.P13B_KEYS) == ("harvest", "election"))

# ============================================================================================ flags off = base
print(f"--- flags off identical to the branch head with Package 13 A ({BASE_REV}) on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_base13b.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_base13b", path)
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
            ({"D1": -2000, "11": 900}, 30000.0, None, {"momentum_enabled": True, "aggressive_value": True,
                                                       "worst_case_backstop_frac": 1.0}),
            ({"G1": 5000, "A1": -800}, 50000.0, {**books_default(), "B2": bk(0.13, None)},
             {"value_mode": True, "take_enabled": True, "take_respect_reserve": True,
              "election_holdback_from_utc": PAST, "election_start_utc": PAST})]
    for inv, cash, books, kw in grid:
        api_n, bn, api_b, bb_ = twin(inv, cash, books, **kw)
        for _ in range(2):
            quiet(bn.cycle)
            quiet(bb_.cycle)
            bn.drain_writes(5)
            bb_.drain_writes(5)
        sn = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_n.wire]
        sb = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_b.wire]
        if sorted(map(json.dumps, sn)) != sorted(map(json.dumps, sb)):
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
                                                     "alloc", "tilt_state", "momentum", "buckets"}   # (wall stamps)
        if (set(kn) != set(kb) or set(kn.get("alloc") or {}) != set(kb.get("alloc") or {})
                or set(bn.health) != set(bb_.health)):
            ok_s = False
            diffs.append(("status keys", set(kn) ^ set(kb), set(bn.health) ^ set(bb_.health)))
        vn = {k: v for k, v in kn.items() if k not in ign}
        vb = {k: v for k, v in kb.items() if k not in ign}
        if json.dumps(vn, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
            ok_h = False
            diffs.append(("status values", [k for k in vn if vn.get(k) != vb.get(k)]))
    check("two full cycles send the same orders, any order (5 books / positions / settings, P13 A flags in one)", ok_w,
          diffs[:1])
    check("every market's quote identical", ok_q, diffs[:1])
    check("alloc_plan identical", ok_p, [d for d in diffs if d[0] == "plan"][:1])
    check("status.json and health keys identical (no harvest / election key while off)", ok_s,
          [d for d in diffs if d[0] == "status keys"][:1])
    check("status.json values identical (but timings and wall-clock stamps)", ok_h,
          [d for d in diffs if d[0] == "status values"][:1])
api, b = mk_bot(election_holdback_from_utc=PAST)
check("election_night off: no holdback even past election_holdback_from_utc (cash_reserve = alloc_mm_reserve)",
      b.el_holdback_left() == 0.0 and b.cash_reserve() == 1000.0)

# ============================================================================================ harvest: levels
print("--- section 4: the harvest ladder's levels")
api, b = mk_bot(tilt_harvest_ladder=True)
warm(b)
plans, why = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("laddered: A2 / D2 (longshots, p 0.02 / 0.10) asks; A1 / D1 (favourites, p 0.98 / 0.90) bids",
      {e: pl["side"] for e, pl in plans.items()} == {"A2": "ask", "D2": "ask", "A1": "bid", "D1": "bid"},
      {e: pl["side"] for e, pl in plans.items()})
check("the middle band (Beta 0.88 / 0.12, Gamma, Ohio, Utah) gets none", all(why.get(e) == "middle" for e in
                                                                           ("B1", "B2", "G1", "G2", "11", "21")), why)
pl = plans["A2"]
check("A2 asks at the best ask 0.08 + 0.02 / 0.04 / 0.06; 0.08 itself skipped (edge (0.08 - 0.02) / 0.92 = 6.5% < 8%)",
      [px for _, px, _, _, _ in pl["levels"]] == [0.10, 0.12, 0.14], pl["levels"])
check("...edges per $ (ask - p) / (1 - ask): 8.9% / 11.4% / 14.0%",
      [round(ed, 3) for _, _, _, ed, _ in pl["levels"]] == [0.089, 0.114, 0.14])
check("...each level $3000 of collateral at 1 - ask: 3333 / 3409 / 3488 shares", [n for _, _, n, _, _ in pl["levels"]]
      == [3333, 3409, 3488])
check("D2 (p 0.10, ask 0.13): only 0.17 (8.4%) and 0.19 (11.1%) clear 8%", levels(b, "D2") if False else
      [(px, n) for _, px, n, _, _ in plans["D2"]["levels"]] == [(0.17, 3614), (0.19, 3703)], plans["D2"]["levels"])
check("D1 (p 0.90, bid 0.89): YES bids at the best bid - offsets: only 0.83 ((0.90 - 0.83) / 0.83 = 8.4%), "
      "$3000 / 0.83 = 3614 shares", [(px, n) for _, px, n, _, _ in plans["D1"]["levels"]] == [(0.83, 3614)],
      plans["D1"]["levels"])
check("A1 (p 0.98, bid 0.92): 0.90 / 0.88 / 0.86 clear, the race cap (15k: A2's 10.0k at 0.98 a share) trims them: "
      "3333 @ 0.90, 1743 @ 0.88", [(px, n) for _, px, n, _, _ in plans["A1"]["levels"]] == [(0.90, 3333), (0.88, 1743)],
      plans["A1"]["levels"])
race = sum(pl_["coll"] for e, pl_ in plans.items() if e in ("A1", "A2"))
check("Alpha's planned collateral (valued at max(lock, p)) stays within aggr_max_race_usd 15,000", race <= 15000 + 1e-6,
      race)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, aggr_max_market_usd=5000.0)
warm(b)
plans, _ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("aggr_max_market_usd 5000: A2 3333 @ 0.10 ($3267 at 0.98), then 1769 @ 0.12, then nothing",
      [(px, n) for _, px, n, _, _ in plans["A2"]["levels"]] == [(0.10, 3333), (0.12, 1769)], plans["A2"]["levels"])
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, aggr_max_race_usd=4000.0)
warm(b)
plans, why = quiet(b.hv_plan, dict(api.inv), time.monotonic())
tot = sum(pl_["coll"] for pl_ in plans.values())
check("aggr_max_race_usd 4000: the race's ladders together <= 4000 and the second market gets none (cap)",
      tot <= 4000 + 1e-6 and len(plans) == 1 and "cap" in why.values(), (tot, list(plans), why))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": -5000})
warm(b)
plans, _ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("with every bucket combined: a 5000 NO short on A2 holds $4900 -> the ladder adds 7,100: 3333 / 3409 / 502",
      [n for _, _, n, _, _ in plans["A2"]["levels"]] == [3333, 3409, 502], plans["A2"]["levels"])
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A1": -1000})
warm(b)
n0 = len(api.wire)
hv(b)
w = [x for x in wire(api)[n0:] if x[0] == "A1"]
check("a favourite bid where we hold NO goes out as the covered 'sell NO' of what is held (cash-free)",
      w and w[0][1:3] == ("no", "sell") and w[0][4] == 1000 and abs(w[0][3] - 0.10) < 1e-9, w)

# never through the book or our own orders
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, books={**books_default(), "A2": bk(0.12, 0.08)})
warm(b)
plans, _ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("a level at / through the other side of the book is skipped, not clipped (bid 0.12: only 0.14 left)",
      [px for _, px, _, _, _ in plans["A2"]["levels"]] == [0.14], plans["A2"]["levels"])
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA)
warm(b)
b.my_orders[901] = M.Resting(901, "A2", True, 0.10, 50, M.utcnow() + timedelta(seconds=600))
b.my_orders[902] = M.Resting(902, "A1", False, 0.88, 50, M.utcnow() + timedelta(seconds=600))
plans, _ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("never at / through our own orders: our bid at 0.10 on A2 -> asks 0.12 / 0.14; our ask 0.88 on A1 -> bid 0.86",
      [px for _, px, _, _, _ in plans["A2"]["levels"]] == [0.12, 0.14]
      and [px for _, px, _, _, _ in plans["A1"]["levels"]] == [0.86], (plans["A2"]["levels"], plans["A1"]["levels"]))
b.my_orders.pop(901)
b.my_orders.pop(902)

# ============================================================================================ harvest: placement
print("--- section 4: placement, cash gate, writes, max markets")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, aggr_max_market_usd=100000.0, aggr_max_race_usd=100000.0)
warm(b)
n_b = len(api.sent("batch"))
touched = hv(b)
check("placed resting (not immediate-or-cancel): A2 asks 0.10 / 0.12 / 0.14, A1 bids 0.90 / 0.88 / 0.86",
      ours(api, "A2") == [("ask", 0.10, 3333), ("ask", 0.12, 3409), ("ask", 0.14, 3488)]
      and [x[1] for x in ours(api, "A1")] == [0.86, 0.88, 0.90] and not api.sent("cancel_order"), (ours(api, "A2"),
                                                                                                  ours(api, "A1")))
check("...both markets' six levels in ONE batch write (batch_size 10)", api.sent("batch")[n_b:] == [("batch", 6)],
      api.sent("batch")[n_b:])
check("...tagged harvest 2..4 in the notes, alive MAX_ORDER_TTL", sorted(b.order_meta[o.order_id]["harvest"]
                                                                          for o in b.hv_orders("A2")) == [2, 3, 4]
      and all(abs((o.expires - M.utcnow()).total_seconds() - M.MAX_ORDER_TTL) < 60 for o in b.hv_orders()))
check("...the markets are not quoted this cycle (touched)", touched == {"A1", "A2"}, touched)
st = b.hv_info
check("status harvest {markets 2, levels_resting 6, collateral_resting, filled_24h 0, edge_filled_24h 0}",
      st.get("markets") == 2 and st.get("levels_resting") == 6 and st.get("filled_24h") == 0
      and st.get("edge_filled_24h") == 0 and abs(st.get("collateral_resting") - sum(b.resting_lock(o) for o in
                                                                                    b.hv_orders())) < 0.01, st)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA)
warm(b)
read(b, cash=0.0)
n_b = len(api.sent("batch"))
hv(b)
check("cash gate refusal (no cash): nothing placed and no write spent", not b.hv_orders()
      and len(api.sent("batch")) == n_b and not api.orders, api.sent("batch")[n_b:])
read(b, cash=50000.0)
hv(b)
check("...the next cycle with cash: the ladder goes out", len(b.hv_orders()) >= 4, len(b.hv_orders()))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, election_night=True, election_holdback_from_utc=PAST,
                cash=21000.0)
warm(b)
hv(b)
lock = sum(b.resting_lock(o) for o in b.hv_orders())
check("the election holdback (20k reserved) is left alone: with $21,000 cash the ladder locks <= $1,000",
      0 < lock <= 1000 + 1e-6, lock)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, harvest_writes_frac=0.0)
warm(b)
hv(b)
check("harvest_writes_frac 0: no placement", not b.hv_orders() and not api.orders)
api, b = mk_bot(tilt_harvest_ladder=True, harvest_max_markets=1)
warm(b)
hv(b)
check("harvest_max_markets 1: one market only (the best edge at the touch)", len({o.eid for o in b.hv_orders()}) == 1,
      {o.eid for o in b.hv_orders()})
api, b = mk_bot(tilt_harvest_ladder=True, live=False)
warm(b)
n_w = len(api.wire)
hv(b)
check("dry run: planned (status planned_markets 4), nothing sent", len(api.wire) == n_w and b.hv_info.get(
    "planned_markets") == 4, b.hv_info)

# ============================================================================================ harvest: re-quotes
print("--- section 4: re-quote rules")
BIG = dict(aggr_max_market_usd=100000.0, aggr_max_race_usd=100000.0)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
hv(b)
n_b, n_c = len(api.sent("batch")), len(api.sent("cancel_order"))
hv(b)
check("next cycle, nothing moved: no write at all",
      len(api.sent("batch")) == n_b and len(api.sent("cancel_order")) == n_c)
api.books["A2"]["asks"] = [lvl(0.085, 2000)]
hv(b)
check("the best ask 0.5c higher (targets 0.105 / 0.125 / 0.145, within 1c): not re-quoted (queue position)",
      len(api.sent("batch")) == n_b and len(api.sent("cancel_order")) == n_c, ours(api, "A2"))
api.books["A2"]["asks"] = [lvl(0.10, 2000)]
hv(b)
check("the best ask 2c higher: the targets are 0.10 / 0.12 / 0.14 / 0.16 (the touch now clears 8%)",
      [x[1] for x in ours(api, "A2")] == [0.10, 0.12, 0.14, 0.16], ours(api, "A2"))
check("...the three orders exactly at a target keep their place (no cancel); only 0.16 added",
      len(api.sent("cancel_order")) == n_c and len(api.sent("batch")) == n_b + 1, api.sent("cancel_order")[n_c:])
api.books["A2"]["asks"] = [lvl(0.08, 2000)]
hv(b)
check("the best ask 2c lower: the 0.16 order is > 1c off its nearest target (0.14) -> re-quoted at once: it is "
      "cancelled, 0.10 / 0.12 / 0.14 keep their place", [x[1] for x in ours(api, "A2")] == [0.10, 0.12, 0.14]
      and len(api.sent("cancel_order")) == n_c + 1, (ours(api, "A2"), api.sent("cancel_order")[n_c:]))
n_b, n_c = len(api.sent("batch")), len(api.sent("cancel_order"))
b.refs.prices.update({"Alpha Senate|Republican": 0.965, "Alpha Senate|Democratic": 0.035})
b.cur_refs.update({"A1": 0.965, "A2": 0.035})
hv(b)
check("p moved 1.5c: re-quoted (state p 0.035; levels whose price and size still match are kept)",
      abs(b.hv_state["A2"]["p"] - 0.035) < 1e-9, b.hv_state.get("A2"))
b.cur_refs.update({"A1": 0.94, "A2": 0.06})
hv(b)
check("p up to 0.06: the 0.10 ask no longer clears 8% ((0.10 - 0.06) / 0.90 = 4.4%) -> pulled",
      0.10 not in [x[1] for x in ours(api, "A2")] and ours(api, "A2"), ours(api, "A2"))
b.cur_refs.update({"A1": 0.98, "A2": 0.02})
hv(b)
hv(b)


def fill_at(api_, eid, price, qty):
    """Another trader takes qty of our resting order at exactly this YES price (the fake's fill(), one order)."""
    for oid, o in list(api_.orders.items()):
        is_bid, p_ = api_.yes_view(o)
        if o["exchangeId"] == eid and abs(p_ - price) < 1e-9:
            o["quantity"] -= qty
            api_.inv[eid] = api_.inv.get(eid, 0) + (qty if is_bid else -qty)
            api_.fills.append({"id": len(api_.fills) + 1, "orderId": oid, "exchangeId": eid, "price": p_,
                               "quantity": qty if is_bid else -qty, "side": "yes" if is_bid else "no",
                               "filledAt": M.iso(M.utcnow())})
            if o["quantity"] <= 0:
                del api_.orders[oid]
            return


check("back at p 0.02: the ladder 0.10 / 0.12 / 0.14 again", [x[1] for x in ours(api, "A2")] == [0.10, 0.12, 0.14],
      ours(api, "A2"))
n_b, n_c = len(api.sent("batch")), len(api.sent("cancel_order"))
fill_at(api, "A2", 0.10, 1000)                    # (another trader lifts 1000 of our 0.10 ask)
quiet(b.log_fills, {})
hv(b)
check("a partial fill: nothing re-quoted before harvest_requote_s", len(api.sent("batch")) == n_b
      and len(api.sent("cancel_order")) == n_c, ours(api, "A2"))
b.hv_state["A2"]["at"] -= 901
hv(b)
check("after 900 s: the part-filled level is replaced at full size, the others keep their place",
      len(api.sent("cancel_order")) - n_c == 1 and ("ask", 0.10, 3333) in ours(api, "A2"),
      (ours(api, "A2"), api.sent("cancel_order")[n_c:]))
n_b, n_c = len(api.sent("batch")), len(api.sent("cancel_order"))
fill_at(api, "A2", 0.12, 3409)
quiet(b.log_fills, {})
hv(b)
check("a level filled whole: re-placed at once (no 900 s wait), nothing cancelled", ("ask", 0.12, 3409) in
      ours(api, "A2") and len(api.sent("batch")) == n_b + 1 and len(api.sent("cancel_order")) == n_c, ours(api, "A2"))

# ============================================================================================ harvest: fills
print("--- section 4: fills are value positions")
records = []


class Cap(logging.Handler):
    def emit(self, r):
        records.append(r.getMessage())


cap = Cap(level=logging.WARNING)
logging.getLogger("mm").addHandler(cap)
logging.getLogger("mm").setLevel(logging.WARNING)
logging.getLogger("mm").propagate = False
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, buckets_enabled=False, **BIG)
warm(b)
hv(b)
b.cur_refs, b.cur_liquid = {"A1": 0.98, "A2": 0.02}, {"A1", "A2"}
api.fill("A2", False, 100)
records.clear()
b.log_fills({})
check("journal: 'HARVEST fill Dem Alpha Senate: sold 100 YES @ 0.100 ...'", any(r.startswith("HARVEST fill")
                                                                                and "sold 100 YES @ 0.100" in r
                                                                                for r in records), records)
check("status filled_24h 100, edge_filled_24h = 100 x (0.10 - 0.02) = $8", (lambda s: s["filled_24h"] == 100
                                                                            and abs(s["edge_filled_24h"] - 8.0) < 1e-6)(
    b.hv_status()), b.hv_status())
mc = b.mm_carry(time.time())
check("mm_carry_24h: the fill in its own class 'harvest'", mc["fills"].get("harvest") == 1 and mc["fills"]["take"] == 0,
      mc["fills"])
api0, b0 = mk_bot()
check("...the class keys are absent while no such fill exists", "harvest" not in b0.mm_carry(time.time())["fills"]
      and "election" not in b0.mm_carry(time.time())["fills"])
b.last_equity = 100000.0
mm, value, mom, cash = quiet(b.bucket_values, {"A2": -100.0}, time.monotonic())
check("the filled short counts in the VALUE bucket (100 x (1 - 0.02) = 98)", abs(value - 98.0) < 1e-6, value)
logging.getLogger("mm").removeHandler(cap)
logging.getLogger("mm").setLevel(logging.NOTSET)
logging.getLogger("mm").propagate = True

# ============================================================================================ harvest: the MM
print("--- section 4: the market maker and the ladder")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
hv(b)
inv0 = dict(api.inv)
qa = quiet(b.decide, b.ex["A2"], 0.07, inv0, b.effective_inventory(inv0), False, 0.0, time.monotonic(), 0.02, 0.07,
           True)
qf = quiet(b.decide, b.ex["A1"], 0.93, inv0, b.effective_inventory(inv0), False, 0.0, time.monotonic(), 0.98, 0.93,
           True)
check("the market maker does not quote the adding side on a laddered market (A2: no ask; A1: no bid)",
      (qa.ask is None or qa.ask_size == 0) and (qf.bid is None or qf.bid_size == 0), (qa, qf))
check("...the other side is still quoted (A2 bid / A1 ask)", qa.bid is not None and qa.bid_size > 0
      and qf.ask is not None and qf.ask_size > 0, (qa, qf))
b.ex["A2"].inv = 300.0
inv1 = {**inv0, "A2": 300.0}
qa2 = quiet(b.decide, b.ex["A2"], 0.07, inv1, b.effective_inventory(inv1), False, 0.0, time.monotonic(), 0.02, 0.07,
            True)
check("...on the ladder's side only what reduces a position (a 300 long: ask <= 300)", (qa2.ask_size or 0) <= 300,
      qa2)
b.ex["A2"].inv = 0.0
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("full cycles: the ladder's orders survive the quote reconcile (hidden from the planner)",
      len([x for x in ours(api, "A2") if x[0] == "ask"]) == 3, ours(api, "A2"))
bids = [x[1] for x in ours(api, "A2") if x[0] == "bid"]
check("...our quote's bid stays below the lowest harvest ask (never a self-cross)", all(x < 0.10 - 1e-9 for x in bids),
      ours(api, "A2"))
api, b = mk_bot(tilt_harvest_ladder=True)
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
per = {}
for o in api.wire:
    if o.get("expirationDate") and o["exchangeId"] in ("A1", "A2", "D1", "D2") and o["quantity"] >= 1500:
        per[(o["exchangeId"], o["price"])] = per.get((o["exchangeId"], o["price"]), 0) + 1
check("three full cycles (live): each harvest level sent exactly once", per and all(v == 1 for v in per.values()), per)

# ============================================================================================ harvest: pulls
print("--- section 4: pulls")


def laddered(**kw):
    api_, b_ = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **{**BIG, **kw})
    warm(b_)
    hv(b_)
    return api_, b_


api, b = laddered()
n0 = len(b.hv_orders("A2"))
b.cur_liquid.discard("A2")
hv(b)
check(f"p unknown (no liquid price): A2's {n0} levels pulled at once, A1's stay", not b.hv_orders("A2")
      and b.hv_orders("A1"), (ours(api, "A2"), ours(api, "A1")))
api, b = laddered()
b.mom_legs["A2"] = {"q": 100.0, "cost": 8.0}
hv(b)
check("the market joins the momentum sleeve: pulled", not b.hv_orders("A2") and b.hv_orders("A1"))
api, b = laddered()
b.global_reduce = True
hv(b)
check("reduce-only (the backstop tripwire): every level pulled", not b.hv_orders() and not api.orders)
api, b = laddered()
b.ex["A2"].close = M.utcnow() + timedelta(minutes=5)
hv(b)
check("the pre-close stop (5 min to close < stop_minutes_before_close 15): pulled", not b.hv_orders("A2")
      and b.hv_orders("A1"))
api, b = laddered()
b.cfg.tilt_harvest_ladder = False
check("the flag goes off: the tick still runs while levels rest", b.hv_on())
hv(b)
check("...every level pulled", not b.hv_orders() and not api.orders)
hv(b)
check("...then nothing left: the tick stops, status harvest gone", not b.hv_on() and not b.hv_info, b.hv_info)
api, b = laddered()
check("kill switch set up: levels rest", len(api.orders) == 6)
api.equity = 50000.0
b.cfg.kill_confirmations = 1
quiet(run_cycles, b, 2)
check("the drawdown kill switch: the bot stops and every order (the ladder's too) is cancelled",
      not b.running and not api.orders and b.exit_code == M.EXIT_KILLED, api.orders)

# ============================================================================================ election: holdback
print("--- section 5: the holdback")
api, b = mk_bot(election_night=True)
before = datetime(2026, 11, 3, 11, 59, tzinfo=timezone.utc)
after = datetime(2026, 11, 3, 12, 0, 1, tzinfo=timezone.utc)
check("not reserved before 3 Nov 12:00 UTC, 20,000 after", b.el_holdback_left(before) == 0.0
      and b.el_holdback_left(after) == 20000.0)
b.el_totals["cash_spent"] = 7500.0
check("...less what the election takes spent (12,500)", b.el_holdback_left(after) == 12500.0)
api, b = mk_bot(momentum_enabled=True, election_night=True, election_holdback_from_utc=PAST, cash=21100.0)
warm(b)
n0 = len(api.wire)
quiet(b.p13_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
got = wire(api)[n0:]
check("momentum buys keep it: cash 21,100, reserve 1,000 + holdback 20,000 -> $100 spent (B2 666 @ 0.15)",
      got == [("B2", "yes", "buy", 0.15, 666)], got)
api, b = mk_bot(momentum_enabled=True, election_night=True, cash=21100.0)
warm(b)
n0 = len(api.wire)
quiet(b.p13_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("...before election_holdback_from_utc nothing is reserved (the sleeve buys its full levels)",
      sum(x[4] for x in wire(api)[n0:]) > 3000, wire(api)[n0:])
api, b = mk_bot(alloc_min_edge_buy=0.05, election_night=True, election_holdback_from_utc=PAST)
warm(b)
pairs_on, _ = quiet(b.alloc_plan, {}, time.monotonic(), 20500.0, set(), None)
b.cfg.election_night = False
pairs_off, _ = quiet(b.alloc_plan, {}, time.monotonic(), 20500.0, set(), None)
check("the allocator: $20,500 cash, reserve 1,000 -> buys with spare cash; with the holdback reserved -> none",
      pairs_off and not pairs_on, (len(pairs_off), len(pairs_on)))

# ============================================================================================ election: calls
print("--- section 5: calls")
api, b = mk_bot(election_night=True, election_start_utc=PAST)
warm(b)
T0 = M.utcnow()
b.cur_refs = {"A1": 0.985, "A2": 0.015, "B1": 0.88, "B2": 0.12}
b.cur_liquid = {"A1", "A2", "B1", "B2"}
b.el_update_calls(T0)
check("Polymarket 0.985 / 0.015: pending, not called yet", set(b.el_calls) == {"A1", "A2"}
      and not any(c["called"] for c in b.el_calls.values()) and not b.el_races())
b.el_update_calls(T0 + timedelta(minutes=9))
check("9 minutes later: still not called", not any(c["called"] for c in b.el_calls.values()))
b.el_update_calls(T0 + timedelta(minutes=10))
check("10 minutes: CALLED - Alpha winner A1, loser A2", b.el_races() == {"Alpha Senate": {"winner": "A1",
                                                                                         "losers": ["A2"]}},
      b.el_races())
b.cur_liquid = {"B1", "B2"}
b.el_update_calls(T0 + timedelta(minutes=11))
check("no liquid reading (the market dropped out of the feed): the call stays", "Alpha Senate" in b.el_races())
b.cur_liquid = {"A1", "A2", "B1", "B2"}
b.cur_refs.update({"A1": 0.95, "A2": 0.05})
b.el_update_calls(T0 + timedelta(minutes=12))
check("the price drops back inside 0.02..0.98: UN-CALLED", not b.el_races() and not b.el_calls, b.el_calls)
b.cur_refs.update({"B1": 1.0})
b.el_update_calls(T0 + timedelta(minutes=13))
check("a reference of exactly 1.0 (resolved): called at once (B1 winner, B2 loser)",
      b.el_races().get("Beta Senate") == {"winner": "B1", "losers": ["B2"]} and b.el_calls["B1"]["resolved"],
      b.el_races())
b.cur_refs = {"D1": 0.5, "D2": 0.015}
b.cur_liquid = {"D1", "D2"}
b.el_calls = {}
b.el_update_calls(T0)
b.el_update_calls(T0 + timedelta(minutes=10))
check("a 3+-leg-safe loser call alone: D2 <= 0.02 for 10 min -> loser (no winner called)",
      b.el_races().get("Delta Senate") == {"winner": None, "losers": ["D2"]}, b.el_races())
api, b = mk_bot(election_night=True)
warm(b)
b.cur_refs, b.cur_liquid = {"A1": 0.99}, {"A1"}
b.el_update_calls(M.utcnow())
check("before election_start_utc: no calls tracked", not b.el_calls and not b.el_active())

# ============================================================================================ election: takes
print("--- section 5: takes on called races")
CALLED_REFS = {"Alpha Senate|Republican": 0.985, "Alpha Senate|Democratic": 0.015}


def el_bot(**kw):
    api_, b_ = mk_bot(election_night=True, election_start_utc=PAST, refs={**refs_default(), **CALLED_REFS}, **kw)
    warm(b_)
    b_.cur_refs.update({"A1": 0.985, "A2": 0.015})
    b_.cur_liquid |= {"A1", "A2"}
    call(b_, "A1", "win", time.time() - 3600)
    return api_, b_


api, b = el_bot()
n_b = len(api.sent("batch"))
n0 = len(api.wire)
traded = el(b)
got = sorted(wire(api)[n0:])
check("called Alpha: YES bought at the winner's 0.93 ask, YES sold into the loser's 0.06 bid (the whole levels)",
      got == [("A1", "yes", "buy", 0.93, 2000), ("A2", "yes", "sell", 0.06, 2000)], got)
check("...nothing on any race not called", all(x[0] in ("A1", "A2") for x in wire(api)[n0:]))
check("...both in ONE batch write", api.sent("batch")[n_b:] == [("batch", 2)], api.sent("batch")[n_b:])
check("...immediate-or-cancel: alive take_order_ttl, nothing of ours rests after",
      all(abs((M.parse_ts(o["expirationDate"]) - M.utcnow()).total_seconds() - b.cfg.take_order_ttl) < 5
          for o in api.wire[n0:]) and not api.orders)
check("...positions: A1 +2000, A2 -2000; cash spent 2000 x 0.93 + 2000 x 0.94 = 3740 (the holdback spends down)",
      api.inv.get("A1") == 2000 and api.inv.get("A2") == -2000 and abs(b.el_totals["cash_spent"] - 3740) < 1e-6,
      (api.inv, b.el_totals))
check("...status election {active, holdback_active, called_races, takes 2, cash_spent 3740}",
      (lambda s: s["active"] and "holdback_active" in s and s["called_races"] == {
          "Alpha Senate": {"winner": "Rep Alpha Senate", "losers": ["Dem Alpha Senate"]}} and s["takes"] == 2
       and s["cash_spent"] == 3740.0)(b.el_info), b.el_info)
check("...notes tagged election", sum(1 for m in b.order_meta.values() if m.get("election")) == 2)
quiet(b.log_fills, {})
mc = b.mm_carry(time.time())
check("mm_carry_24h: the takes' fills in their own class 'election'", mc["fills"].get("election") == 2, mc["fills"])
n1 = len(api.wire)
el(b)
check("the next cycle: no duplicate (the levels are gone, a cooldown runs)", len(api.wire) == n1, wire(api)[n1:])
b.write_status(True)
with open(b.cfg.status_file) as f:
    saved = json.load(f).get("election") or {}
b2 = quiet(M.Bot, api, b.cfg)
check("a restart restores the calls (with their start) and the cash spent", b2.el_calls.get("A1", {}).get("called")
      and abs(b2.el_totals["cash_spent"] - 3740) < 1e-6 and saved.get("takes") == 2, (b2.el_calls, b2.el_totals))
# caps, holdback budget, cash gate
api, b = el_bot(aggr_max_market_usd=985.0)
n0 = len(api.wire)
el(b)
got = {x[0]: x[4] for x in wire(api)[n0:]}
check("aggr_max_market_usd 985: 985 / max(0.93, p 0.985) = 1000 YES on A1; A2 985 / 0.985 = 1000 short",
      got == {"A1": 1000, "A2": 1000}, got)
api, b = el_bot(election_holdback_usd=500.0)
n0 = len(api.wire)
el(b)
got = wire(api)[n0:]
check("the holdback is the takes' budget: $500 -> 537 YES @ 0.93 and nothing more",
      got == [("A1", "yes", "buy", 0.93, 537)] and b.el_totals["cash_spent"] <= 500, got)
api, b = el_bot(cash=100.0)
read(b, cash=100.0)
n0 = len(api.wire)
el(b)
got = wire(api)[n0:]
check("the cash gate: $100 cash -> 107 YES @ 0.93 (the sale into the loser's bid dropped)",
      got == [("A1", "yes", "buy", 0.93, 107)], got)
api, b = el_bot(inv={"A1": -1000, "A2": 500})
n0 = len(api.wire)
el(b)
got = sorted(wire(api)[n0:])
check("where we hold NO on the winner: a covered 'sell NO' of what is held; YES held on the loser: sold first",
      got == [("A1", "no", "sell", 0.07, 1000), ("A2", "yes", "sell", 0.06, 500)], got)
api, b = el_bot()
b.global_reduce = True
n0 = len(api.wire)
el(b)
check("reduce-only (the tripwire): no take that adds", len(api.wire) == n0)
# batched
MANY = {"Alpha Senate|Republican": 0.985, "Alpha Senate|Democratic": 0.015,
        "Beta Senate|Republican": 0.99, "Beta Senate|Democratic": 0.01,
        "Gamma Senate|Republican": 0.99, "Gamma Senate|Democratic": 0.01,
        "Delta Senate|Republican": 0.99, "Delta Senate|Democratic": 0.01}
api, b = mk_bot(election_night=True, election_start_utc=PAST, batch_size=3)
warm(b)
b.cur_refs.update({"A1": 0.985, "A2": 0.015, "B1": 0.99, "B2": 0.01, "G1": 0.99, "G2": 0.01, "D1": 0.99, "D2": 0.01})
b.cur_liquid |= {"A1", "A2", "B1", "B2", "G1", "G2", "D1", "D2"}
for e in ("A1", "B1", "G1", "D1"):
    call(b, e, "win", time.time() - 3600)
n_b = len(api.sent("batch"))
n0 = len(api.wire)
el(b)
check("four called races: 8 takes (4 winners bought, 4 losers sold) sent batch_size 3 a write (3 + 3 + 2)",
      len(wire(api)[n0:]) == 8 and api.sent("batch")[n_b:] == [("batch", 3), ("batch", 3), ("batch", 2)],
      (wire(api)[n0:], api.sent("batch")[n_b:]))
# own orders, leftovers
api, b = el_bot()
res = b.place_orders([{"exchangeId": "A1", "side": "yes", "action": "sell", "quantity": 10, "price": 0.93,
                       "tournamentId": "T", "expirationDate": M.iso(M.utcnow() + timedelta(seconds=600))}])
b.remember_order({"exchangeId": "A1", "action": "sell", "quantity": 10, "price": 0.93,
                  "expirationDate": M.iso(M.utcnow() + timedelta(seconds=600))}, res[0]["data"], time.monotonic())
own_id = res[0]["data"]["orderId"]
n_c = len(api.sent("cancel_order"))
el(b)
check("our own ask at 0.93 on the winner is cancelled before the 0.93 buy (never crossing our own order)",
      ("cancel_order", own_id) in api.sent("cancel_order")[n_c:] and own_id not in api.orders)
api, b = el_bot()
fresh(b)
b.ex["A1"].book["asks"][0]["quantity"] = 3000       # (the cached book shows more than the exchange has)
b.ex["A1"].verified = time.monotonic()
n_c = len(api.sent("cancel_order"))
quiet(b.el_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("a take that does not fill whole: its leftover is cancelled at once (nothing of it rests)",
      len(api.sent("cancel_order")) > n_c and not [o for o in api.orders.values() if o["exchangeId"] == "A1"],
      api.orders)
# pre-close
api, b = el_bot()
b.ex["A1"].close = b.ex["A2"].close = M.utcnow() + timedelta(hours=1)
n0 = len(api.wire)
el(b)
check("an hour before the close (inside the 12 h flatten window, value_mode off): the takes still go",
      len(wire(api)[n0:]) == 2 and not b.alloc_market_ok(b.ex["A1"]), wire(api)[n0:])
api, b = el_bot(close_override_utc="2026-11-04T17:00:00Z")
b.ex["A1"].close = b.ex["A2"].close = M.utcnow() - timedelta(hours=1)
n0 = len(api.wire)
el(b)
check("the API close passed but close_override_utc keeps the market open: the takes go",
      len(wire(api)[n0:]) == 2, wire(api)[n0:])
api, b = el_bot()
b.ex["A1"].close = b.ex["A2"].close = M.utcnow() + timedelta(minutes=5)
n0 = len(api.wire)
el(b)
check("inside the stop window (5 min < 15): nothing", len(api.wire) == n0)
# the quoter
api, b = el_bot()
el(b)
inv0 = {"A1": 0.0, "A2": 0.0}
b.ex["A2"].inv = 0.0
q2 = quiet(b.decide, b.ex["A2"], 0.07, inv0, b.effective_inventory(inv0), False, 0.0, time.monotonic(), 0.015, 0.07,
           True)
check("the quoter on a called race's loser, flat: reduce-only (no bid, no ask)", (q2.bid_size or 0) == 0
      and (q2.ask_size or 0) == 0, q2)
b.ex["A1"].inv = 300.0
inv1 = {"A1": 300.0, "A2": 0.0}
q1 = quiet(b.decide, b.ex["A1"], 0.93, inv1, b.effective_inventory(inv1), False, 0.0, time.monotonic(), 0.985, 0.93,
           True)
check("...on the winner with a 300 long: no bid, the ask only reduces (<= 300)", (q1.bid_size or 0) == 0
      and (q1.ask_size or 0) <= 300, q1)
api, b = el_bot()
b.cfg.election_night = False
el(b)
check("election_night off: calls cleared, the quoter back to normal (no called sides)",
      not b.el_sides and not b.el_calls)
# full cycles: no duplicates
api, b = mk_bot(election_night=True, election_start_utc=PAST, refs={**refs_default(), **CALLED_REFS},
                books={**books_default(), "A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08)})
b.ref_max_plausible_gap = 1.0
b.cfg.ref_max_plausible_gap = 1.0
call(b, "A1", "win", time.time() - 3600)
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
n_take = sum(1 for o in api.wire if o["exchangeId"] == "A1" and o["price"] == 0.93 and o["action"] == "buy")
check("three full cycles (live): the winner's 0.93 ask taken exactly once", n_take == 1, wire(api, "A1"))
check("...status.json has election, not harvest", (lambda d: "election" in d and "harvest" not in d)(
    (b.write_status(True), json.load(open(b.cfg.status_file)))[1]))

# summary, dry run
api, b = el_bot()
el(b)
line = b.summary_ops_line(100000.0) or ""
check("the 2-hourly summary shows ' | election ACTIVE: 1 called, 2 takes, $3.7k spent (holdback ...)'",
      "election ACTIVE: 1 called, 2 takes, $3.7k spent" in line, line[-160:])
api, b = laddered()
line = b.summary_ops_line(100000.0) or ""
check("...and ' | harvest 2 mkts, 6 levels, $Xk resting, 0 filled 24h ($0 edge)'", "harvest 2 mkts, 6 levels" in line,
      line[-160:])
api0, b0 = mk_bot()
line = b0.summary_ops_line(100000.0) or ""
check("...absent while off", "harvest" not in line and "election" not in line)
api, b = mk_bot(election_night=True, election_start_utc=PAST, live=False, refs={**refs_default(), **CALLED_REFS})
warm(b)
b.cur_refs.update({"A1": 0.985, "A2": 0.015})
b.cur_liquid |= {"A1", "A2"}
call(b, "A1", "win", time.time() - 3600)
n0 = len(api.wire)
el(b)
check("dry run: the election takes are logged, nothing sent, nothing counted", len(api.wire) == n0
      and b.el_totals["takes"] == 0)

# ============================================================================================ robustness
print("--- nothing crashes: empty books, no Polymarket, unknown markets")
EMPTY = {e: {"bids": [], "asks": []} for e in books_default()}
api, b = mk_bot(books=EMPTY, tilt_harvest_ladder=True, election_night=True, election_start_utc=PAST,
                inv={"A2": -100, "ZZ": 50})
b.el_calls["ZZ"] = {"side": "win", "since": 0.0, "called": True, "resolved": False}
call(b, "A1", "win", 0.0)
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("empty books, an unknown market held / called, both flags on: two cycles, no Package 13 B order",
      b.failed_cycles == 0 and not b.hv_orders() and "ZZ" not in b.el_calls
      and not [o for o in api.wire if o["exchangeId"] in books_default() and o["quantity"] >= 500], b.failed_cycles)
api, b = mk_bot(refs={}, tilt_harvest_ladder=True, election_night=True, election_start_utc=PAST)
b.refs = FakeRefs({})
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("no Polymarket references at all: cycles run, no ladder, no calls", b.failed_cycles == 0 and not b.hv_plans
      and not b.el_calls)
t1 = quiet(b.hv_tick, M.utcnow(), {"NOPE": 10.0}, {}, None, set())
t2 = quiet(b.el_tick, M.utcnow(), {"NOPE": 10.0}, {}, None, set())
check("hv_tick / el_tick with an unknown market in the positions: no crash",
      isinstance(t1, set) and isinstance(t2, set))
b.p13b_init({"calls": {"X": {"side": "maybe", "since": 1}, "A1": {"side": "win", "since": "x"}}, "totals": "bad"})
check("a malformed saved election state is dropped (no crash)", b.el_calls == {} and b.el_totals["cash_spent"] == 0.0)

# ============================================================================================ py_compile 3.10
print("--- py_compile under Python 3.10")
py310 = shutil.which("python3.10")
exe = py310 or sys.executable
out = subprocess.run([exe, "-m", "py_compile", os.path.join(os.path.dirname(HERE), "mm_bot.py")], capture_output=True,
                     text=True)
check(f"{'python3.10' if py310 else 'this interpreter'} -m py_compile mm_bot.py", out.returncode == 0,
      out.stderr[-300:])

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
