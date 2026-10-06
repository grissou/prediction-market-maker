"""
Package 15 (owner, 6 Oct 11:00 UTC): the Package 13 HARVEST LADDER ported ALONE onto 64f27c8 (Package 14.2), with
  tilt_harvest_ladder    resting YES asks on longshots (p <= 0.10) at the best other ask + harvest_offsets, YES bids
                         on favourites (p >= 0.90) at the best other bid - offsets, only where the edge per $ clears
                         harvest_min_edge; never through the book, our own orders or at our own quote's price; covered
                         rules; re-quotes / pulls; adoption after a timeout (RT13-1); the quoter off the ladder's side
                         and its reducing cap net of the harvest levels (RT13-2); fills = value positions.
  harvest_total_usd      the ladder's RESTING collateral budget carved from alloc_mm_reserve: the MM keeps
                         alloc_mm_reserve - harvest_total_usd (the gate holds it back from the ladder); the refill /
                         spare cash / take reserve count the resting carve-out as reserve (the target stays
                         alloc_mm_reserve); mm_funding / harvest show the split.
  state_max_usd          a per-STATE collateral cap (state_of(label)) on the ladder, the allocator's buys, the takes
                         and the quoter's adding side in the tails; existing positions kept.
Sections: settings / ranges; flags off identical to 64f27c8 on a grid with fills (orders, quotes, notes, status,
health, summary, alloc_plan); state_of on all 237 live labels; ladder levels / edges / covered rules / never crossing;
the caps (market, state, carve-out) and the MM effective reserve at the gate; carve-out accounting; the state cap on
the allocator / takes / quotes; re-quotes and pulls; adoption after a timeout; the quoter and the ladder; fills;
no duplicates; robustness; the staged file; py_compile.
Run:  python tests/test_p15.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import astuple
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market, run_cycles      # noqa: E402
import mm_bot as M                                                            # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "64f27c8"                              # Package 14.2 (READY, live), the head this package is built on
SNAP = os.environ.get("P9_SNAP", "/home/claude/snap04")
STAGED = os.path.join(ROOT, "deploy", "package15", "settings_override.harvest.json")
LIVE_142 = os.path.join(ROOT, "deploy", "package14", "settings_override.mm_funding_14_2.json")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# A: Alpha  A1 favourite p 0.98 (book 0.92 / 0.93) / A2 longshot p 0.02 (book 0.06 / 0.08)
# B: Beta   B1 0.88 / B2 0.12 (the middle: no ladder)          G: Gamma  G1 0.80 / G2 0.20 (no ladder)
# D: Delta  D1 favourite p 0.90 (0.89 / 0.91) / D2 longshot p 0.10 (0.11 / 0.13)
# R: Rhode Island Senate R1 (Rep) longshot p 0.02 / R2 (Dem) favourite p 0.98; Q: Rhode Island Governor Q1 / Q2
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate",
         "R": "Rhode Island Senate", "Q": "Rhode Island Governor"}
ALPHA = {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02}
RHODE = {"Rhode Island Senate|Republican": 0.02, "Rhode Island Senate|Democratic": 0.98,
         "Rhode Island Governor|Republican": 0.05, "Rhode Island Governor|Democratic": 0.95}
BIG = dict(alloc_max_contract_usd=100000.0, harvest_total_usd=100000.0)


def books_default():
    return {"A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08),
            "B1": bk(0.85, 0.87), "B2": bk(0.13, 0.15),
            "G1": bk(0.66, 0.70), "G2": bk(0.30, 0.32),
            "D1": bk(0.89, 0.91), "D2": bk(0.11, 0.13),
            "R1": bk(0.06, 0.08), "R2": bk(0.92, 0.93),
            "Q1": bk(0.10, 0.12), "Q2": bk(0.88, 0.89)}


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


def warm(b):
    """One quiet cycle with the ladder off (fair values, Polymarket, cash read), then a clean slate."""
    keep = b.cfg.tilt_harvest_ladder
    b.cfg.tilt_harvest_ladder = False
    quiet(b.cycle)
    b.drain_writes(5)
    b.cfg.tilt_harvest_ladder = keep
    quiet(b.cancel_everything)
    b.my_orders.clear()
    b.api.orders.clear()
    for x in b.ex.values():
        x.quote = None
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    fresh(b)
    read(b)


def hv(b, skip=(), at=None):
    """One harvest tick on fresh books and the current positions."""
    time.sleep(0.002)
    fresh(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.hv_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def plan(b):
    return quiet(b.hv_plan, dict(b.api.inv), time.monotonic())


def lv(plans, e):
    return [(px, n) for _, px, n, _, _ in (plans.get(e) or {}).get("levels", [])]


def ours(api, eid):
    return api.ours(eid)


def wire(api, eid=None):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in api.wire
            if eid is None or o["exchangeId"] == eid]


def laddered(**kw):
    api_, b_ = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **{**BIG, **kw})
    warm(b_)
    hv(b_)
    return api_, b_


# ============================================================================================ settings
print("--- settings")
D = M.Config()
SPEC = {"tilt_harvest_ladder": (False, (False, True)), "harvest_offsets": ((0.0, 0.02, 0.04, 0.06), (0.0, 0.2)),
        "harvest_level_usd": (3000.0, (0.0, 50000.0)), "harvest_min_edge": (0.08, (0.0, 2.0)),
        "harvest_max_markets": (237, (0, 1000)), "harvest_writes_frac": (0.4, (0.0, 1.0)),
        "harvest_requote_s": (900.0, (60.0, 7200.0)), "harvest_total_usd": (10000.0, (0.0, 100000.0)),
        "state_max_usd": (0.0, (0.0, 200000.0))}
for k, (dflt, rng) in SPEC.items():
    ok_d = getattr(D, k) == dflt and type(getattr(D, k)) is type(dflt)
    ok_r = M.OVERRIDABLE.get(k) == rng
    if isinstance(dflt, bool):
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
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after alloc_swap_min_gain, in Config and OVERRIDABLE",
      _f[_f.index("alloc_swap_min_gain") + 1:][:len(SPEC)] == list(SPEC)
      and _ov[_ov.index("alloc_swap_min_gain") + 1:][:len(SPEC)] == list(SPEC))
src = open(os.path.join(ROOT, "mm_bot.py")).read()
check("Config comment block '# --- P15: the harvest ladder alone + a per-state collateral cap'",
      "# --- P15: the harvest ladder alone + a per-state collateral cap" in src)
check("Bot.HARVEST_KEYS names the status keys P15 adds (harvest, state_caps)",
      tuple(M.Bot.HARVEST_KEYS) == ("harvest", "state_caps"))
_, b_ = mk_bot()
check("off by default: hv_on False, st_on False, carve 0, the MM's effective reserve = alloc_mm_reserve",
      not b_.hv_on() and not b_.st_on() and b_.hv_carve_used() == 0.0 and b_.mm_reserve_effective() == 1000.0)
b_.cfg.alloc_mm_reserve, b_.cfg.tilt_harvest_ladder = 20000.0, True
check("on: the MM's effective reserve = alloc_mm_reserve - harvest_total_usd (20k - 10k = 10k); never below 0",
      b_.mm_reserve_effective() == 10000.0
      and (setattr(b_.cfg, "harvest_total_usd", 30000.0) or b_.mm_reserve_effective() == 0.0))

# ============================================================================================ flags off = 64f27c8
print(f"--- flags off identical to the Package 14.2 head ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p15base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p15base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = (lambda msg: None), (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase"}
STATE_REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
              "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45,
              **RHODE, **ALPHA}

if base is not None:
    def twin(inv, overrides):
        res = []
        for mod in (M, base):
            api_x, bn = mk_bot(inv=inv, refs=STATE_REFS)
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
                api_x.inv.update(inv)
                api_x.cash, api_x.equity = 50000.0, 100000.0
                api_x.pnl = (lambda a: lambda: (a.log("pnl"), {"totalAccountValue": a.equity,
                                                               "cashBalance": a.cash})[1])(api_x)
                bn = base.Bot(api_x, cfg)
                bn.t["endDate"] = M.iso(CLOSE)
                for x in bn.ex.values():
                    x.close = CLOSE
                bn.refs = FakeRefs(dict(STATE_REFS))
            for k, v in overrides.items():
                if k in bn.cfg.__dataclass_fields__:
                    setattr(bn.cfg, k, v)
            for n in range(4):                    # four cycles; other traders hit our quotes before cycles 3 and 4
                if n in (2, 3):
                    for e, side in (("21", True), ("22", False), ("R1", False), ("A2", True), ("Q2", True)):
                        api_x.fill(e, side, 40)
                quiet(bn.cycle)
                bn.drain_writes(5)
            res.append((api_x, bn))
        return res

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = ok_n = ok_h = ok_l = True
    diffs = []
    OFF_KNOBS = {"harvest_total_usd": 50000.0, "harvest_level_usd": 9000.0, "harvest_offsets": (0.0, 0.01),
                 "harvest_min_edge": 0.01, "harvest_max_markets": 3}   # (the ladder's knobs with the flag off)
    grids = ({}, {"value_mode": True, "value_quote_hurdle": 0.05, "ref_weight": 1.0, **OFF_KNOBS},
             {"alloc_enabled": True, "alloc_mm_reserve": 15000.0, "value_mode": True, "mm_refill_fast": True,
              "mm_recycle_enabled": True, "alloc_max_edge_sell": 0.05, "take_enabled": True,
              "take_respect_reserve": True, "alloc_min_edge_buy": 0.05},
             {"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0, "value_mode": True,
              "worst_case_backstop_frac": 0.05, "mm_room_guard": True, "alloc_enabled": True,
              "alloc_swap_room_netting": True, "alloc_min_edge_buy": 0.02, "alloc_swap_sell_margin": 0.03})
    for inv in ({}, {"21": 600, "R1": -900, "Q2": 4000}, {"22": -800, "12": 2500, "A1": -500, "R2": 300}):
        for ov in grids:
            (an, bn), (ab, bb_) = twin(inv, ov)
            if sorted(map(json.dumps, strip(an.wire))) != sorted(map(json.dumps, strip(ab.wire))):
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
            drop = VOLATILE | set(M.Bot.MM_FUNDING_KEYS) | set(M.Bot.HARVEST_KEYS)
            wall = {"last_run_wall", "last_run", "since"}
            cut = lambda s: {k: ({a: ([f[1:] for f in x] if a == "flows" else x) for a, x in v.items()  # noqa
                                  if a not in wall} if k in ("alloc", "mm_risk_room") else v)
                             for k, v in s.items() if k not in drop and not k.endswith("_seconds")}
            if cut(sn) != cut(sb) or set(sb) - set(sn) or set(sn) - set(sb):
                ok_s = False
                diffs.append(("status", inv, ov, {k for k in set(cut(sn)) | set(cut(sb))
                                                  if cut(sn).get(k) != cut(sb).get(k)} | (set(sn) ^ set(sb))))
            if set(bn.health) != set(bb_.health) or bn.status_report() != bb_.status_report():
                ok_h = False
                diffs.append(("health", inv, ov))
            if bn.summary_ops_line(100000.0) != bb_.summary_ops_line(100000.0):
                ok_l = False
                diffs.append(("summary", inv, ov))
            if sn.get("mm_funding", {}).keys() != sb.get("mm_funding", {}).keys():
                ok_s = False
                diffs.append(("mm_funding keys", inv, ov))
    check("four cycles with fills send the same orders (3 positions x plain / value + the ladder's knobs / "
          "allocator + takes + refill / room reserve + swaps), state-named races included", ok_w, diffs[:1])
    check("every market's quote identical", ok_q, [d for d in diffs if d[0] == "quote"][:1])
    check("order notes identical", ok_n, [d for d in diffs if d[0] == "notes"][:1])
    check("status.json identical in keys AND values (no harvest / state_caps key while off; mm_funding keys too)",
          ok_s, [d for d in diffs if d[0] in ("status", "mm_funding keys")][:1])
    check("health keys and the status line identical", ok_h, [d for d in diffs if d[0] == "health"][:1])
    check("2-hourly summary line identical (no harvest piece while off)", ok_l,
          [d for d in diffs if d[0] == "summary"][:1])

    ok_p = True                                   # the pure planner: buys, refills, spare cash
    for inv, cash, extra in (({"21": 1000, "11": 2000}, 0.0, {}), ({"21": 1000}, 30000.0, {}),
                             ({"22": -900, "Q2": 3000}, 5000.0, {"harvest_total_usd": 50000.0}),
                             ({"R1": -2000, "12": 300}, 20000.0, {"alloc_prefer_short": True})):
        api_n, bn = mk_bot(inv, refs=STATE_REFS, alloc_enabled=True, alloc_max_edge_sell=0.05,
                           alloc_min_edge_buy=0.05, **extra)
        quiet(bn.cycle)
        fresh(bn)
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
        ok_p = ok_p and "state_cap" not in pn[1]["blocked_by"]
    check("alloc_plan identical to 64f27c8 (4 positions / cash; no 'state_cap' while off)", ok_p)

# ============================================================================================ state_of
print("--- state_of on the live labels")
labels = []
if os.path.exists(os.path.join(SNAP, "md.sqlite")):
    con = sqlite3.connect(os.path.join(SNAP, "md.sqlite"))
    labels = sorted({r[0] for r in con.execute("SELECT label FROM snapshots")})
check("the snapshot's 237 market labels read", len(labels) == 237, len(labels))
got = {lb: M.state_of(lb) for lb in labels}
check("every label maps to a state but the 4 headline U.S. House / U.S. Senate ones (None)",
      sorted(lb for lb, s in got.items() if s is None) == ["Dem U.S. House", "Dem U.S. Senate", "Rep U.S. House",
                                                           "Rep U.S. Senate"], [lb for lb, s in got.items() if s is None])
exp = {}
for lb in labels:
    rest = lb.split(" ", 1)[1]
    if rest.startswith("U.S."):
        exp[lb] = None
    elif rest[2:3] == "-":
        exp[lb] = rest[:2]
    else:
        name = rest.rsplit(" ", 1)[0]            # "Rhode Island Senate" -> "Rhode Island"
        exp[lb] = M.US_STATES.get(name)
bad = [(lb, got[lb], exp[lb]) for lb in labels if got[lb] != exp[lb]]
check("all 237: '<party> <State> Senate|Governor' -> the state's code, '<party> XX-NN House race' -> XX", not bad,
      bad[:5])
ri = sorted(lb for lb, s in got.items() if s == "RI")
check("Rhode Island: its 5 markets (Senate x2, Governor x3) are one state 'RI'",
      ri == ["Dem Rhode Island Governor", "Dem Rhode Island Senate", "Ind Rhode Island Governor",
             "Rep Rhode Island Governor", "Rep Rhode Island Senate"], ri)
check("45 distinct states on the live list (West Virginia / Virginia and Kansas / Arkansas kept apart)",
      len({s for s in got.values() if s}) == 45 and got.get("Rep West Virginia Senate") == "WV"
      and got.get("Dem Virginia Senate") == "VA" and got.get("Rep Kansas Senate") == "KS"
      and got.get("Rep Arkansas Senate") == "AR", len({s for s in got.values() if s}))
check("other label forms: 'Ind RI Governor', 'Dem RI-01', 'Rep RI-01 House race', 'Rhode Island Senate' -> RI; "
      "'Dem U.S. House', 'Rep Alpha Senate', '', None, 5 -> None",
      [M.state_of(x) for x in ("Ind RI Governor", "Dem RI-01", "Rep RI-01 House race", "Rhode Island Senate",
                               "Dem ME-AL House race")] == ["RI", "RI", "RI", "RI", "ME"]
      and [M.state_of(x) for x in ("Dem U.S. House", "Rep Alpha Senate", "", None, 5, "Rep ZZ-01")] == [None] * 6)

# ============================================================================================ harvest: levels
print("--- the ladder's levels")
api, b = mk_bot(tilt_harvest_ladder=True, **BIG)
warm(b)
plans, why = plan(b)
check("laddered: A2 / D2 (longshots p 0.02 / 0.10) asks; A1 / D1 (favourites p 0.98 / 0.90) bids (no state refs here)",
      {e: pl["side"] for e, pl in plans.items()} == {"A2": "ask", "D2": "ask", "A1": "bid", "D1": "bid"},
      {e: pl["side"] for e, pl in plans.items()})
check("the middle band (Beta 0.88 / 0.12, Gamma, Ohio, Utah) gets none", all(why.get(e) == "middle" for e in
                                                                           ("B1", "B2", "G1", "G2", "11", "21")), why)
pl = plans["A2"]
check("A2 asks at the best ask 0.08 + 0.02 / 0.04 / 0.06; 0.08 itself skipped (edge (0.08 - 0.02) / 0.92 = 6.5% < 8%)",
      [px for _, px, _, _, _ in pl["levels"]] == [0.10, 0.12, 0.14], pl["levels"])
check("...edges per $ (ask - p) / (1 - ask): 8.9% / 11.4% / 14.0%, every one >= 8%",
      [round(ed, 3) for _, _, _, ed, _ in pl["levels"]] == [0.089, 0.114, 0.14])
check("...each level $3000 of collateral at 1 - ask: 3333 / 3409 / 3488 shares", [n for _, _, n, _, _ in pl["levels"]]
      == [3333, 3409, 3488])
check("D2 (p 0.10, ask 0.13): only 0.17 (8.4%) and 0.19 (11.1%) clear 8%",
      lv(plans, "D2") == [(0.17, 3614), (0.19, 3703)], plans["D2"]["levels"])
check("D1 (p 0.90, bid 0.89): YES bids at the best bid - offsets: only 0.83 ((0.90 - 0.83) / 0.83 = 8.4%), "
      "$3000 / 0.83 = 3614 shares", lv(plans, "D1") == [(0.83, 3614)], plans["D1"]["levels"])
check("A1 (p 0.98, bid 0.92): 0.90 / 0.88 / 0.86 at (p - bid) / bid 8.9% / 11.4% / 14.0%",
      lv(plans, "A1") == [(0.90, 3333), (0.88, 3409), (0.86, 3488)]
      and [round(x[3], 3) for x in plans["A1"]["levels"]] == [0.089, 0.114, 0.14], plans["A1"]["levels"])
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, harvest_min_edge=0.12, **BIG)
warm(b)
plans, _ = plan(b)
check("harvest_min_edge 0.12: only the 14% levels stay (A2 0.14, A1 0.86)",
      lv(plans, "A2") == [(0.14, 3488)] and lv(plans, "A1") == [(0.86, 3488)], (lv(plans, "A2"), lv(plans, "A1")))

# never through the book, our own orders, our own quote's price
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, books={**books_default(), "A2": bk(0.12, 0.08)}, **BIG)
warm(b)
plans, _ = plan(b)
check("a level at / through the other side of the book is skipped, not clipped (bid 0.12: only 0.14 left)",
      [px for _, px, _, _, _ in plans["A2"]["levels"]] == [0.14], plans["A2"]["levels"])
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
b.my_orders[901] = M.Resting(901, "A2", True, 0.10, 50, M.utcnow() + timedelta(seconds=600))
b.my_orders[902] = M.Resting(902, "A1", False, 0.88, 50, M.utcnow() + timedelta(seconds=600))
plans, _ = plan(b)
check("never at / through our own orders: our bid at 0.10 on A2 -> asks 0.12 / 0.14; our ask 0.88 on A1 -> bid 0.86",
      [px for _, px, _, _, _ in plans["A2"]["levels"]] == [0.12, 0.14]
      and [px for _, px, _, _, _ in plans["A1"]["levels"]] == [0.86], (plans["A2"]["levels"], plans["A1"]["levels"]))
b.my_orders.pop(901)
b.my_orders.pop(902)
b.my_orders[903] = M.Resting(903, "A2", False, 0.12, 50, M.utcnow() + timedelta(seconds=600))
b.my_orders[904] = M.Resting(904, "A1", True, 0.88, 50, M.utcnow() + timedelta(seconds=600))
plans, _ = plan(b)
check("P13 C: never at the price of our own resting quote on that side (our ask 0.12 on A2: 0.10 / 0.14 only; our "
      "bid 0.88 on A1: 0.90 / 0.86 only)", [px for _, px, _, _, _ in plans["A2"]["levels"]] == [0.10, 0.14]
      and [px for _, px, _, _, _ in plans["A1"]["levels"]] == [0.90, 0.86], (plans["A2"]["levels"],
                                                                             plans["A1"]["levels"]))
b.my_orders.pop(903)
b.my_orders.pop(904)

# covered rules
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A1": -1000}, **BIG)
warm(b)
n0 = len(api.wire)
hv(b)
w = [x for x in wire(api)[n0:] if x[0] == "A1"]
check("a favourite bid where we hold NO: the first level goes out as the covered 'sell NO' of the 1000 held "
      "(cash-free), the next levels are YES buys (0.88 / 0.86)",
      w and w[0][1:3] == ("no", "sell") and w[0][4] == 1000 and abs(w[0][3] - 0.10) < 1e-9
      and [x[1:4] for x in w[1:]] == [("yes", "buy", 0.88), ("yes", "buy", 0.86)], w)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": 2000}, **BIG)
warm(b)
plans, _ = plan(b)
check("a longshot ask where we hold YES sells it first (covered 2000 of the 3333 at 0.10), then is a short",
      plans["A2"]["levels"][0][4] == 2000 and plans["A2"]["levels"][0][2] == 3333
      and [x[4] for x in plans["A2"]["levels"][1:]] == [0, 0], plans["A2"]["levels"])
check("...the covered part locks no cash: the plan's carve = (3333 - 2000) x 0.90 + 3409 x 0.88 + 3488 x 0.86",
      abs(plans["A2"]["carve"] - (1333 * 0.90 + 3409 * 0.88 + 3488 * 0.86)) < 0.01, plans["A2"]["carve"])

# ============================================================================================ caps
print("--- caps: the market (alloc_max_contract_usd), the carve-out, the state")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, harvest_total_usd=100000.0)
warm(b)
plans, why = plan(b)
check("alloc_max_contract_usd 10,000 per market (adds valued at max(lock, p)): A2 3333 / 3409 / 3462 (0.98 a share)",
      lv(plans, "A2") == [(0.10, 3333), (0.12, 3409), (0.14, 3462)], lv(plans, "A2"))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": -8000}, harvest_total_usd=100000.0)
warm(b)
plans, why = plan(b)
check("...with an 8000 NO short held there ($7,840 at p): $2,160 left -> 2204 @ 0.10 and nothing more",
      lv(plans, "A2") == [(0.10, 2204)], lv(plans, "A2"))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": -11000}, harvest_total_usd=100000.0)
warm(b)
plans, why = plan(b)
check("...a position already over the cap is KEPT (no reduce) and the ladder adds nothing there (why market_cap)",
      "A2" not in plans and why.get("A2") == "market_cap" and api.inv["A2"] == -11000, why.get("A2"))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, alloc_max_contract_usd=100000.0)
warm(b)
plans, why = plan(b)
_full = [[(0.90, 3333), (0.88, 3409), (0.86, 3488)], [(0.10, 3333), (0.12, 3409), (0.14, 3488)]]
check("the carve-out (harvest_total_usd 10,000 of cash locked) across markets: the first market (edge tie) gets its "
      "three levels ($8,999 locked), the other the last $1,000: 1111 at its first level",
      (lv(plans, "A1") == _full[0] and lv(plans, "A2") == [(0.10, 1111)])
      or (lv(plans, "A2") == _full[1] and lv(plans, "A1") == [(0.90, 1111)]), (lv(plans, "A1"), lv(plans, "A2")))
check("...the plans' carve <= 10,000", sum(p_["carve"] for p_ in plans.values()) <= 10000 + 1e-6,
      sum(p_["carve"] for p_ in plans.values()))
hv(b)
check("...placed: the resting levels lock <= 10,000 (carve used / free in status harvest.carve)",
      b.hv_resting_lock() <= 10000 + 1e-6 and b.hv_info["carve"]["total"] == 10000.0
      and abs(b.hv_info["carve"]["used"] + b.hv_info["carve"]["free"] - 10000.0) < 0.02,
      (b.hv_resting_lock(), b.hv_info.get("carve")))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, harvest_total_usd=0.0, **{"alloc_max_contract_usd": 1e5})
warm(b)
plans, why = plan(b)
check("harvest_total_usd 0: no level that locks cash (why carve)", not plans and why.get("A2") == "carve", why)

# the state cap
api, b = mk_bot(tilt_harvest_ladder=True, refs=RHODE, inv={"Q2": 10000}, state_max_usd=15000.0, **BIG)
warm(b)
plans, why = plan(b)
coll = 10000 * 0.95
r_add = sum(pl_["coll"] for e, pl_ in plans.items() if e in ("R1", "R2", "Q1", "Q2"))
check("state cap 15,000: Rhode Island holds 10,000 Q2 YES ($9,500 at p 0.95) -> the RI ladders add <= $5,500 at p",
      0 < r_add <= 15000 - coll + 1e-6 and plans, (r_add, {e: lv(plans, e) for e in plans}))
check("...the market(s) left without room say why 'state_cap'; status counts blocked adds for RI",
      "state_cap" in why.values() and b.st_blocked.get("RI", 0) >= 1, (why, dict(b.st_blocked)))
api, b = mk_bot(tilt_harvest_ladder=True, refs=RHODE, inv={"Q2": 17000}, state_max_usd=15000.0, **BIG)
warm(b)
plans, why = plan(b)
check("RI already over the cap ($16,150): no RI ladder at all, the position kept (no sale planned or sent)",
      not any(e in plans for e in ("R1", "R2", "Q1", "Q2")) and api.inv["Q2"] == 17000, (list(plans), why))
api, b = mk_bot(tilt_harvest_ladder=True, refs=RHODE, inv={"Q2": 17000}, state_max_usd=0.0, **BIG)
warm(b)
plans, why = plan(b)
check("state_max_usd 0 (off): the RI ladders are planned (only the market / carve caps)",
      any(e in plans for e in ("R1", "R2")), why)

# the P12 ops pause (value adds paused on the risk room): the ladder's levels are tail adds
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": 2000}, **BIG)
warm(b)
b.mmr_paused = True
plans, why = plan(b)
check("value adds paused (mm_risk_reserve_*): covered levels only - A2 sells its 2000 YES at 0.10, no short; A1 "
      "(nothing held) none, why mm_risk_reserve", lv(plans, "A2") == [(0.10, 2000)] and "A1" not in plans
      and why.get("A1") == "mm_risk_reserve", (lv(plans, "A2"), why.get("A1")))
api, b = laddered()
b.mmr_paused = True
hv(b)
check("...the pause starting while levels rest: every adding level is pulled", not b.hv_orders() and not api.orders,
      ours(api, "A2"))

# the MM's effective reserve at the gate
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, alloc_mm_reserve=20000.0, cash=12000.0,
                alloc_max_contract_usd=1e5)
warm(b)
read(b, cash=12000.0)
hv(b)
lock = b.hv_resting_lock()
check("the cash gate keeps the MM's effective reserve: cash 12,000, reserve 20,000 - carve 10,000 = 10,000 kept -> "
      "the ladder locks <= $2,000", 0 < lock <= 2000 + 1e-6, lock)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, alloc_mm_reserve=20000.0, cash=9000.0,
                alloc_max_contract_usd=1e5)
warm(b)
read(b, cash=9000.0)
n_b = len(api.sent("batch"))
hv(b)
check("...cash 9,000 (below the MM's 10,000): no level placed, no write spent", not b.hv_orders()
      and len(api.sent("batch")) == n_b, api.sent("batch")[n_b:])

# the other direction: the quotes leave the ladder what it plans but has not placed (hv_quote_hold)
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, alloc_mm_reserve=20000.0, cash=12000.0,
                alloc_max_contract_usd=1e5)
warm(b)
read(b, cash=12000.0)
hv(b)
want = min(10000.0, sum(pl_["carve"] for pl_ in b.hv_plans.values()))
check("hv_quote_hold = what the ladder plans (<= harvest_total_usd) less what its levels lock (~$8,000 here)",
      abs(b.hv_quote_hold() - (want - b.hv_resting_lock())) < 1e-6 and b.hv_quote_hold() > 7000, b.hv_quote_hold())
quiet(b.cycle)
b.drain_writes(5)
q_lock = sum(b.resting_lock(o) for o in b.my_orders.values() if not (b.order_meta.get(o.order_id) or {}).get("harvest"))
check("...a full cycle: the quotes lock at most the free cash less that hold (12,000 - ladder - hold ~ 2,000), not "
      "the ladder's part", q_lock <= max(0.0, 12000.0 - b.hv_resting_lock() - b.hv_quote_hold()) + 50, (
          round(q_lock), round(b.hv_resting_lock()), round(b.hv_quote_hold())))
b.cfg.tilt_harvest_ladder = False
check("...0 with the ladder off (the quotes as 64f27c8)", b.hv_quote_hold() == 0.0)

# ============================================================================================ carve-out accounting
print("--- the carve-out: budget, resting, free; the refill target")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, alloc_mm_reserve=20000.0, cash=50000.0,
                alloc_max_contract_usd=1e5)
warm(b)
hv(b)
res = b.hv_resting_lock()
cf = b.hv_carve_fields()
check("carve fields: budget 10,000, resting = the cash our harvest levels lock, free = budget - resting",
      cf["budget"] == 10000.0 and abs(cf["resting"] - res) < 0.01 and abs(cf["free"] - (10000 - res)) < 0.01, cf)
check("hv_carve_used = min(budget, resting) while on", abs(b.hv_carve_used() - min(10000.0, res)) < 1e-6)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
mf = st["mm_funding"]
check("status mm_funding: cash_target = alloc_mm_reserve (20,000, the refill target unchanged), mm_reserve_effective "
      "10,000, harvest_carve {budget, resting, free}, reserve_cash = cash_free + resting",
      mf["cash_target"] == 20000.0 and mf["mm_reserve_effective"] == 10000.0
      and set(mf["harvest_carve"]) == {"budget", "resting", "free"}
      and abs(mf["reserve_cash"] - (mf["cash_free"] + mf["harvest_carve"]["resting"])) < 0.02, mf.get("harvest_carve"))
check("status harvest: markets, levels_resting, collateral_resting, filled_24h, edge_filled_24h, carve {total, used, "
      "free}, blocked_by", {"markets", "levels_resting", "collateral_resting", "filled_24h", "edge_filled_24h",
                            "carve", "blocked_by"} <= set(st.get("harvest") or {})
      and set(st["harvest"]["carve"]) == {"total", "used", "free"}, st.get("harvest"))
# the refill / spare cash count the carve-out as reserve
b.cfg.alloc_enabled, b.cfg.alloc_max_edge_sell = True, 0.10
b.cfg.alloc_min_edge_buy = 5.0                    # (no buy level: refills and spare cash only)
read(b, cash=12000.0)
b.cg_spent = 0.0
free = b.cash_left()
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), free, set(), None)
refill = sum(p_["usd"] for p_ in pairs if p_["buy"] is None and p_["sell"]["kind"] != "cash")
check("free cash 12,000 with ~$9,000 resting in the ladder: reserve cash ~21,000 >= 20,000 -> NO refill sale "
      "(without the carve-out the refill would sell ~8,000)", refill == 0 and free + b.hv_carve_used() >= 20000,
      (refill, free, b.hv_carve_used()))
b.cfg.tilt_harvest_ladder = False                 # (the same state with the carve-out not counted)
check("...the ladder flag off: the carve-out is not counted (hv_carve_used 0)", b.hv_carve_used() == 0.0)
b.cfg.tilt_harvest_ladder = True
check("mm_cash_low / mm_funding_below use the reserve cash too: 12,000 + ~9,000 is not below half of 20,000",
      not b.mm_cash_low() and not any(x.startswith("cash") for x in b.mm_funding_below()), b.mm_funding_below())
_low = max(0.0, 10000.0 - b.hv_carve_used() - 500.0)
read(b, cash=_low)
check("...free cash + the ladder's resting carve-out 500 below half of 20,000: 'cash ... of 20,000' (the refill runs)",
      b.mm_cash_low() and any(x.startswith("cash") for x in b.mm_funding_below()), (_low, b.mm_funding_below()))
b.cfg.take_respect_reserve = True
read(b, cash=12000.0)
order = {"exchangeId": "B1", "side": "yes", "action": "buy", "quantity": 1000, "price": 0.80, "tournamentId": "T"}
check("take_respect_reserve: a take needing $800 with free 12,000 + carve ~9,000 - 800 >= 20,000 goes; needing "
      "$2,000 does not", not b.take_blocked_by_reserve(order)
      and b.take_blocked_by_reserve({**order, "quantity": 2500}), b.hv_carve_used())

# ============================================================================================ state cap elsewhere
print("--- the state cap on the allocator, the takes and the quoter")
api, b = mk_bot(refs=RHODE, inv={"Q2": 14000}, books={**books_default(), "R2": bk(0.80, 0.82, 20000)},
                alloc_min_edge_buy=0.05, alloc_max_contract_usd=10000.0)
warm(b)                                           # (the allocator off in the warm-up: nothing bought yet)
pairs_off, info_off = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 30000.0, set(), None)
b.cfg.state_max_usd = 15000.0
pairs_on, info_on = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 30000.0, set(), None)
buy_off = sum(p_["usd"] for p_ in pairs_off if p_["buy"] and p_["buy"]["eid"] == "R2")
buy_on = sum(p_["usd"] for p_ in pairs_on if p_["buy"] and p_["buy"]["eid"] == "R2")
check("allocator: RI holds $13,300 (14,000 Q2 at 0.95); the spare-cash buy of R2 (ask 0.82, p 0.98) is ~$10k off, "
      "and with state_max_usd 15,000 its collateral at p stays <= $1,700 (blocked_by state_cap)",
      buy_off > 5000 and 0 < buy_on * 0.98 / 0.82 <= 1700 + 1 and info_on["blocked_by"].get("state_cap", 0) >= 1,
      (buy_off, buy_on, info_on["blocked_by"]))
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000}, books={**books_default(), "R2": bk(0.80, 0.82, 20000)},
                alloc_min_edge_buy=0.05, state_max_usd=15000.0)
warm(b)
pairs_on, info_on = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 30000.0, set(), None)
check("...RI over the cap: no RI buy at all; the Q2 position is not sold (no sale leg planned on it)",
      not any(p_["buy"] and b.st_key(p_["buy"]["eid"]) == "RI" for p_ in pairs_on)
      and not any(p_["sell"].get("eid") == "Q2" for p_ in pairs_on), [(p_["sell"].get("eid"), p_["buy"])
                                                                         for p_ in pairs_on][:3])
# alloc_buy re-check
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000}, books={**books_default(), "R2": bk(0.80, 0.82, 20000)},
                alloc_min_edge_buy=0.05, state_max_usd=15000.0)
warm(b)
b.cfg.alloc_enabled = True
b.st_refresh(dict(api.inv))
pr = {"sell": {"kind": "cash", "label": "spare cash", "usd": 1000.0, "edge": 0.0, "qty": 0, "px": 1.0},
      "buy": {"eid": "R2", "label": "Dem Rhode Island Senate", "short": False, "px": 0.82, "qty": 1219, "edge": 0.19,
              "usd": 1000.0}, "usd": 1000.0, "status": "sold", "proceeds": 1000.0, "sold_at": -1e18}
b.alloc_run = {}
n0 = len(api.wire)
sent = quiet(b.alloc_buy, pr, dict(api.inv), b.orders_by_eid(M.utcnow()), time.monotonic(), time.time(), set())
check("alloc_buy re-checks the state cap before the IOC: RI over the cap -> no order (blocked_by state_cap)",
      not sent and len(api.wire) == n0 and b.alloc_run.get("blocked_by", {}).get("state_cap", 0) == 1,
      (sent, b.alloc_run))
# takes
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000}, state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
ex = b.ex["R2"]
ex.take_dir = 1
ex.book = {"bids": [lvl(0.80, 5000)], "asks": [lvl(0.82, 5000)]}
n0 = len(api.wire)
quiet(b.execute_take, ex, 0.98, dict(api.inv).get("R2", 0.0), 0.81, time.monotonic())
check("a stale-quote take that would ADD in RI over the cap ($15,200 of 15,000): not sent", len(api.wire) == n0,
      wire(api)[n0:])
api, b = mk_bot(refs=RHODE, inv={"Q2": 15500}, state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
ex = b.ex["R2"]
ex.take_dir = 1
ex.book = {"bids": [lvl(0.80, 5000)], "asks": [lvl(0.82, 5000)]}
n0 = len(api.wire)
quiet(b.execute_take, ex, 0.98, 0.0, 0.81, time.monotonic())
got = [x for x in wire(api)[n0:] if x[0] == "R2"]
room = 15000.0 - b.st_snapshot({"Q2": 15500})[0]["RI"]
check("...RI at $14,725 + our quotes' locks: the take is cut to the room left at p 0.98 (<= %.0f$)" % max(room, 0),
      got and got[0][4] * 0.98 <= max(room, 0) + 1 and got[0][4] < 5000, (got, room))
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000, "R2": -300}, state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
ex = b.ex["R2"]
ex.inv = -300.0
ex.take_dir = 1
ex.book = {"bids": [lvl(0.80, 5000)], "asks": [lvl(0.82, 5000)]}
n0 = len(api.wire)
quiet(b.execute_take, ex, 0.98, -300.0, 0.81, time.monotonic())
got = [x for x in wire(api)[n0:] if x[0] == "R2"]
check("...one that REDUCES a short there goes, cut to the reducing 300 (existing positions are never blocked from "
      "shrinking)", got and sum(x[4] for x in got) == 300, got)
# quotes in the tails
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000}, state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
inv0 = dict(api.inv)
q = quiet(b.decide, b.ex["R2"], 0.93, inv0, b.effective_inventory(inv0), False, 0.0, time.monotonic(), 0.98, 0.93,
          True)
check("the quoter in a TAIL market of a state over the cap: the adding side quotes nothing (R2 flat: no bid)",
      (q.bid is None or q.bid_size == 0) and b.ex["R2"].st_caps == (0, 0), (q, b.ex["R2"].st_caps))
api, b = mk_bot(refs=RHODE, inv={"Q2": 16000, "R2": -400}, state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
b.ex["R2"].inv = -400.0
inv0 = dict(api.inv)
q = quiet(b.decide, b.ex["R2"], 0.93, inv0, b.effective_inventory(inv0), False, 0.0, time.monotonic(), 0.98, 0.93,
          True)
check("...with a 400 short there the bid is capped to the 400 that reduce it", (q.bid_size or 0) <= 400
      and b.ex["R2"].st_caps[0] == 400, (q, b.ex["R2"].st_caps))
api, b = mk_bot(refs={**RHODE, "Rhode Island Senate|Republican": 0.40, "Rhode Island Senate|Democratic": 0.60},
                books={**books_default(), "R1": bk(0.38, 0.42), "R2": bk(0.58, 0.62)}, inv={"Q2": 15500},
                state_max_usd=15000.0)
warm(b)
b.st_refresh(dict(api.inv))
q = quiet(b.decide, b.ex["R2"], 0.60, dict(api.inv), b.effective_inventory(dict(api.inv)), False, 0.0,
          time.monotonic(), 0.60, 0.60, True)
check("...the MIDDLE band keeps its two-way quote (market making is not capped: st_caps None)",
      b.ex["R2"].st_caps is None, b.ex["R2"].st_caps)
api, b = mk_bot(refs=RHODE, inv={"Q2": 15500})
warm(b)
q_off = quiet(b.decide, b.ex["R2"], 0.93, dict(api.inv), b.effective_inventory(dict(api.inv)), False, 0.0,
              time.monotonic(), 0.98, 0.93, True)
check("...state_max_usd 0: no cap (st_caps None)", b.ex["R2"].st_caps is None)
api, b = mk_bot(refs=RHODE, inv={"Q2": 15500}, state_max_usd=15000.0)
warm(b)
quiet(b.cycle)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status state_caps: RI {collateral ~14,725, cap 15,000, blocked_adds} (over 50% of the cap); Ohio absent",
      "RI" in (st.get("state_caps") or {}) and abs(st["state_caps"]["RI"]["collateral"] - 15500 * 0.95) < 200
      and st["state_caps"]["RI"]["cap"] == 15000.0 and "OH" not in st["state_caps"], st.get("state_caps"))
check("summary piece ' | state caps ...' only once a state is over the cap", "state caps" not in
      (b.summary_ops_line(100000.0) or ""))
api.inv["Q2"] = 17000
quiet(b.cycle)
check("...RI over it: ' | state caps RI 16.xk/15k'", "state caps RI 16." in (b.summary_ops_line(100000.0) or ""),
      (b.summary_ops_line(100000.0) or "")[-120:])

# ============================================================================================ placement
print("--- placement, writes, max markets")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
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
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, harvest_writes_frac=0.0, **BIG)
warm(b)
hv(b)
check("harvest_writes_frac 0: no placement", not b.hv_orders() and not api.orders)
api, b = mk_bot(tilt_harvest_ladder=True, harvest_max_markets=1, **BIG)
warm(b)
hv(b)
check("harvest_max_markets 1: one market only", len({o.eid for o in b.hv_orders()}) == 1, {o.eid for o in b.hv_orders()})
api, b = mk_bot(tilt_harvest_ladder=True, live=False, **BIG)
warm(b)
n_w = len(api.wire)
hv(b)
check("dry run: planned (status planned_markets 4), nothing sent", len(api.wire) == n_w and b.hv_info.get(
    "planned_markets") == 4, b.hv_info)

# ============================================================================================ re-quotes
print("--- re-quote rules")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
hv(b)
n_b, n_c = len(api.sent("batch")), len(api.sent("cancel_order"))
hv(b)
check("next cycle, nothing moved: no write at all",
      len(api.sent("batch")) == n_b and len(api.sent("cancel_order")) == n_c)
api.books["A2"]["asks"] = [lvl(0.085, 2000)]
hv(b)
check("the best ask 0.5c higher (targets within 1c): not re-quoted (queue position)",
      len(api.sent("batch")) == n_b and len(api.sent("cancel_order")) == n_c, ours(api, "A2"))
api.books["A2"]["asks"] = [lvl(0.10, 2000)]
hv(b)
check("the best ask 2c higher: the targets are 0.10 / 0.12 / 0.14 / 0.16 (the touch now clears 8%)",
      [x[1] for x in ours(api, "A2")] == [0.10, 0.12, 0.14, 0.16], ours(api, "A2"))
check("...the three orders exactly at a target keep their place (no cancel); only 0.16 added",
      len(api.sent("cancel_order")) == n_c and len(api.sent("batch")) == n_b + 1, api.sent("cancel_order")[n_c:])
api.books["A2"]["asks"] = [lvl(0.08, 2000)]
hv(b)
check("the best ask 2c lower: the 0.16 order is > 1c off its nearest target -> re-quoted at once (cancelled), "
      "0.10 / 0.12 / 0.14 keep their place", [x[1] for x in ours(api, "A2")] == [0.10, 0.12, 0.14]
      and len(api.sent("cancel_order")) == n_c + 1, (ours(api, "A2"), api.sent("cancel_order")[n_c:]))
b.cur_refs.update({"A1": 0.965, "A2": 0.035})
hv(b)
check("p moved 1.5c: re-quoted (state p 0.035)", abs(b.hv_state["A2"]["p"] - 0.035) < 1e-9, b.hv_state.get("A2"))
b.cur_refs.update({"A1": 0.94, "A2": 0.06})
hv(b)
check("p up to 0.06: the 0.10 ask no longer clears 8% ((0.10 - 0.06) / 0.90 = 4.4%) -> pulled",
      0.10 not in [x[1] for x in ours(api, "A2")] and ours(api, "A2"), ours(api, "A2"))
b.cur_refs.update({"A1": 0.98, "A2": 0.02})
hv(b)
hv(b)


def fill_at(api_, eid, price, qty):
    """Another trader takes qty of our resting order at exactly this YES price."""
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
fill_at(api, "A2", 0.10, 1000)
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

# ============================================================================================ pulls
print("--- pulls")
api, b = laddered()
n0 = len(b.hv_orders("A2"))
b.cur_liquid.discard("A2")
hv(b)
check(f"p unknown (no liquid price): A2's {n0} levels pulled at once, A1's stay", not b.hv_orders("A2")
      and b.hv_orders("A1"), (ours(api, "A2"), ours(api, "A1")))
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
api, b = laddered(state_max_usd=15000.0)
api.inv["A2"] = 0
b.st_keys["A1"] = b.st_keys["A2"] = "RI"           # (Alpha as a Rhode Island race: state over the cap now)
api.inv["Q2"] = 17000
hv(b)
check("a state over the cap: its markets' adding levels are pulled (why state_cap), the position kept",
      not b.hv_orders("A1") and not b.hv_orders("A2") and api.inv["Q2"] == 17000, b.hv_why.get("A2"))
api, b = laddered()
check("kill switch set up: levels rest", len(api.orders) == 6)
api.equity = 50000.0
b.cfg.kill_confirmations = 1
quiet(run_cycles, b, 2)
check("the drawdown kill switch: the bot stops and every order (the ladder's too) is cancelled",
      not b.running and not api.orders and b.exit_code == M.EXIT_KILLED, api.orders)

# ============================================================================================ RT13-1 adoption
print("--- a batch whose outcome is unknown: the landed levels are adopted, never placed twice")
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
orig = api.place_batch


def lost_after_landing(orders):
    orig(orders)
    raise M.ApiError(504, "TIMEOUT", "gateway timeout")


api.place_batch = lost_after_landing
hv(b)
api.place_batch = orig
landed = len(api.orders)
check("the batch timed out AFTER landing: 6 levels rest at the exchange, the bot knows none of them yet, the "
      "orders list is marked stale", landed == 6 and not b.hv_orders() and b.orders_stale, (landed, b.orders_stale))
for x in b.ex.values():
    x.pending_until = 0.0
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
per = {}
for o in api.orders.values():
    yb, yp = api.yes_view(o)
    per[(o["exchangeId"], yb, yp)] = per.get((o["exchangeId"], yb, yp), 0) + 1
check("...after the next orders read they are ADOPTED as harvest levels (notes 'harvest', 'recovered')",
      len(b.hv_orders()) == 6 and all((b.order_meta.get(o.order_id) or {}).get("recovered") for o in b.hv_orders()),
      len(b.hv_orders()))
check("...and never placed a second time (one order per market / side / price)", all(v == 1 for v in per.values())
      and len(api.orders) >= 6, per)

# ============================================================================================ the market maker
print("--- the market maker and the ladder")
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
check("RT13-2: on the ladder's side the quote's reducing cap is the position LESS what the harvest asks offer "
      "(300 long, 10,230 offered: no ask)", (qa2.ask_size or 0) == 0, qa2)
check("...hv_side_qty = the shares our harvest asks offer (10,230)", b.hv_side_qty("A2", False) == 3333 + 3409 + 3488,
      b.hv_side_qty("A2", False))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, inv={"A2": 5000}, harvest_level_usd=900.0, **BIG)
warm(b)
hv(b)
b.ex["A2"].inv = 5000.0
inv1 = dict(api.inv)
qa3 = quiet(b.decide, b.ex["A2"], 0.07, inv1, b.effective_inventory(inv1), False, 0.0, time.monotonic(), 0.02, 0.07,
            True)
offered = b.hv_side_qty("A2", False)
check("...5000 long, the harvest asks offer %d (covered): the quote's ask <= 5000 - that" % offered,
      0 < offered < 5000 and (qa3.ask_size or 0) <= 5000 - offered, (offered, qa3))
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
hv(b)
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("full cycles: the ladder's orders survive the quote reconcile (hidden from the planner)",
      len([x for x in ours(api, "A2") if x[0] == "ask"]) == 3, ours(api, "A2"))
bids = [x[1] for x in ours(api, "A2") if x[0] == "bid"]
check("...our quote's bid stays below the lowest harvest ask (never a self-cross)", all(x < 0.10 - 1e-9 for x in bids),
      ours(api, "A2"))
api, b = mk_bot(tilt_harvest_ladder=True, **BIG)
for _ in range(3):
    quiet(b.cycle)
    b.drain_writes(5)
per = {}
for o in api.wire:
    if o.get("expirationDate") and o["exchangeId"] in ("A1", "A2", "D1", "D2") and o["quantity"] >= 1500:
        per[(o["exchangeId"], o["price"])] = per.get((o["exchangeId"], o["price"]), 0) + 1
check("three full cycles (live): each harvest level sent exactly once (no duplicate orders)",
      per and all(v == 1 for v in per.values()), per)
dup = {}
for o in api.orders.values():
    yb, yp = api.yes_view(o)
    dup[(o["exchangeId"], yb, yp)] = dup.get((o["exchangeId"], yb, yp), 0) + 1
check("...no two of our orders rest at one market / side / price", all(v == 1 for v in dup.values()),
      {k: v for k, v in dup.items() if v > 1})

# ============================================================================================ fills
print("--- fills are value positions")
records = []


class Cap(logging.Handler):
    def emit(self, r):
        records.append(r.getMessage())


cap = Cap(level=logging.WARNING)
logging.getLogger("mm").addHandler(cap)
logging.getLogger("mm").setLevel(logging.WARNING)
logging.getLogger("mm").propagate = False
api, b = mk_bot(tilt_harvest_ladder=True, refs=ALPHA, **BIG)
warm(b)
hv(b)
b.cur_refs, b.cur_liquid = {"A1": 0.98, "A2": 0.02}, {"A1", "A2"}
api.fill("A2", False, 100)
records.clear()
b.log_fills({})
check("journal: 'HARVEST fill Dem Alpha Senate: sold 100 YES @ 0.100 ... a VALUE position'",
      any(r.startswith("HARVEST fill") and "sold 100 YES @ 0.100" in r and "VALUE position" in r for r in records),
      records)
check("status filled_24h 100, edge_filled_24h = 100 x (0.10 - 0.02) = $8, collateral_filled_24h $90",
      (lambda s: s["filled_24h"] == 100 and abs(s["edge_filled_24h"] - 8.0) < 1e-6
       and abs(s["collateral_filled_24h"] - 90.0) < 1e-6)(b.hv_status()), b.hv_status())
mc = b.mm_carry(time.time())
check("mm_carry_24h: the fill in its own class 'harvest' (not maker / take)", mc["fills"].get("harvest") == 1
      and mc["fills"]["take"] == 0 and mc["fills"]["maker_tail"] == 0, mc["fills"])
api0, b0 = mk_bot()
check("...the class key is absent while no such fill exists", "harvest" not in b0.mm_carry(time.time())["fills"])
check("the filled short is not MM inventory (MM_MAKER_SKIP has 'harvest'): no MM lot on A2",
      "harvest" in M.Bot.MM_MAKER_SKIP and "A2" not in b.mm_lots)
logging.getLogger("mm").removeHandler(cap)
logging.getLogger("mm").setLevel(logging.NOTSET)
logging.getLogger("mm").propagate = True

# summary, status keys
api, b = laddered()
line = b.summary_ops_line(100000.0) or ""
check("the 2-hourly summary shows ' | harvest 2 mkts, 6 levels, $Xk resting of $100.0k, 0 filled 24h ($0 edge)'",
      "harvest 2 mkts, 6 levels" in line and "resting of $100.0k" in line, line[-160:])
api0, b0 = mk_bot()
quiet(b0.cycle)
b0.write_status(True)
st0 = json.load(open(b0.cfg.status_file))
check("...absent while off: no ' | harvest' piece, no harvest / state_caps key, no mm_funding split",
      "harvest" not in (b0.summary_ops_line(100000.0) or "") and "harvest" not in st0 and "state_caps" not in st0
      and "harvest_carve" not in st0["mm_funding"] and "mm_reserve_effective" not in st0["mm_funding"])

# ============================================================================================ robustness
print("--- nothing crashes: empty books, no Polymarket, unknown markets / labels")
EMPTY = {e: {"bids": [], "asks": []} for e in books_default()}
api, b = mk_bot(books=EMPTY, tilt_harvest_ladder=True, state_max_usd=15000.0, inv={"A2": -100, "ZZ": 50})
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("empty books, an unknown market held, ladder + state cap on: two cycles, no ladder order",
      b.failed_cycles == 0 and not b.hv_orders(), b.failed_cycles)
api, b = mk_bot(refs={}, tilt_harvest_ladder=True, state_max_usd=15000.0)
b.refs = FakeRefs({})
for _ in range(2):
    quiet(b.cycle)
    b.drain_writes(5)
check("no Polymarket references at all: cycles run, no ladder (why 'no p')", b.failed_cycles == 0 and not b.hv_plans
      and b.hv_why.get("A2") == "no p", b.hv_why.get("A2"))
t1 = quiet(b.hv_tick, M.utcnow(), {"NOPE": 10.0}, {}, None, set())
coll, own = b.st_snapshot({"NOPE": 10.0, "R1": 5.0})
check("hv_tick / st_snapshot with an unknown market in the positions: no crash (an unknown market has no state; "
      "R1's 5 YES count in RI)", isinstance(t1, set) and "NOPE" not in coll and coll.get("RI", 0) > 0
      and set(coll) <= {"RI", "OH", "UT"}, coll)
b.ex["R1"].label = "Something odd"
b.st_keys.clear()
check("an unrecognised label: no state (st_key None), so no state cap there", b.st_key("R1") is None)

# ============================================================================================ the staged file
print("--- the staged file")
if os.path.exists(STAGED):
    raw = json.load(open(STAGED))
    good, bad = M.validate_overrides(raw, M.Config())
    base142 = json.load(open(LIVE_142))
    p15k = {"tilt_harvest_ladder", "harvest_offsets", "harvest_level_usd", "harvest_min_edge", "harvest_max_markets",
            "harvest_writes_frac", "harvest_requote_s", "harvest_total_usd", "state_max_usd"}
    check("deploy/package15/settings_override.harvest.json validates with no problem", not bad, bad)
    check("...it is the live 14.2 file (value_sell_margin 0.01 as live) + the P15 keys, nothing else changed",
          {k: v for k, v in raw.items() if k not in p15k} == {**base142, "value_sell_margin": 0.01}, set(raw) ^ set(
              base142))
    check("...the ladder ON with every harvest default explicit, harvest_total_usd 10,000, state_max_usd 15,000",
          raw.get("tilt_harvest_ladder") is True and raw.get("harvest_offsets") == [0, 0.02, 0.04, 0.06]
          and raw.get("harvest_level_usd") == 3000 and raw.get("harvest_min_edge") == 0.08
          and raw.get("harvest_max_markets") == 237 and raw.get("harvest_writes_frac") == 0.4
          and raw.get("harvest_requote_s") == 900 and raw.get("harvest_total_usd") == 10000
          and raw.get("state_max_usd") == 15000, {k: raw.get(k) for k in p15k})
    check("...capital_ceiling_adding_size_factor 0.5, arb_enabled false, ref_tilt_headline true untouched; reserve "
          "20,000", raw.get("capital_ceiling_adding_size_factor") == 0.5 and raw.get("arb_enabled") is False
          and raw.get("ref_tilt_headline") is True and raw.get("alloc_mm_reserve") == 20000.0)
else:
    check("deploy/package15/settings_override.harvest.json exists", False, STAGED)

# ============================================================================================ py_compile 3.10
print("--- py_compile under Python 3.10")
py310 = shutil.which("python3.10")
exe = py310 or sys.executable
out = subprocess.run([exe, "-m", "py_compile", os.path.join(ROOT, "mm_bot.py"), os.path.abspath(__file__)],
                     capture_output=True, text=True)
check(f"{'python3.10' if py310 else 'this interpreter'} -m py_compile mm_bot.py and this test", out.returncode == 0,
      out.stderr[-300:])

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
