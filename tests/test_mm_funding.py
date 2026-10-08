"""Package 14 (owner, 4 Oct 21:10 UTC): MM funding - "keep market making FULLY FUNDED at all times". Built on the live
head c7c0107, every flag OFF by default:
  mm_recycle_enabled  stale market-making inventory (middle-band maker lots older than mm_inv_max_age_h or above
                      mm_inv_max_usd) worked out through the quoter's own reducing side at fair - mm_recycle_concession
                      (never crossing, never below the value floor); +EV inventory handed to the value bucket instead
  mm_refill_fast      the allocator's reserve refill on the NEXT cycle when free cash < alloc_mm_reserve: MM inventory
                      first (IOC near fair), then the lowest edge-held, never below the value floor; RT13-3 guard
  mm_room_guard       mm_risk_reserve_*: the MM inventory's own risk counts against the MM room first (the pause is
                      decided on the value book's share), hysteresis as before, the recycler never paused
  monitoring          status.json mm_funding (always, read-only: Bot.MM_FUNDING_KEYS), the 2-h below-half alert
                      (once, re-armed), the summary piece
Sections: settings / ranges; flags off identical to c7c0107 on a grid (orders, quotes, notes, status minus the new key,
health, summary); lot tracking (classification, FIFO, reconcile, ages, persistence / restore, seeding from fills.csv);
staleness by age and $; the recycler (price, floor, never crossing, reduce side only, no duplicate, hand-over); the fast
refill (next cycle, order, turnover / write caps, floor, RT13-3); the room guard (accounting, pause / resume, recycler
not paused); monitoring and the alert; mm_carry_24h "recycle"; the staged file; py_compile on 3.10.
Run:  python tests/test_mm_funding.py      (exit code 0 = all passed)"""
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import astuple
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
BASE_REV = "c7c0107"                              # the live head this package is built on
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
    """Ohio (tails: p 0.12 / 0.88) and Utah (middle: 0.55 / 0.45, book 53/57 - edge-held of a long 3.8% < 5%)."""
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


def asks(api, eid):
    return [(p, n) for side, p, n in api.ours(eid) if side == "ask"]


def lot_q(b, e):
    return round(sum(x[0] for x in b.mm_lots.get(e, [])), 6)


# ============================================================================================ settings
print("--- settings")
D = M.Config()
KEYS = ["mm_recycle_enabled", "mm_inv_max_age_h", "mm_inv_max_usd", "mm_recycle_concession", "mm_refill_fast",
        "mm_room_guard", "mm_funding_alert_h"]
check("defaults: flags off, 6 h, $3000, 1c concession, alert after 2 h",
      [getattr(D, k) for k in KEYS] == [False, 6.0, 3000.0, 0.01, False, False, 2.0])
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after mm_risk_reserve_corr, in Config and OVERRIDABLE",
      _f[_f.index("mm_risk_reserve_corr") + 1:][:len(KEYS)] == KEYS
      and _ov[_ov.index("mm_risk_reserve_corr") + 1:][:len(KEYS)] == KEYS)
check("ranges", [M.OVERRIDABLE[k] for k in KEYS] == [(False, True), (0.5, 168.0), (0.0, 50000.0), (0.0, 0.05),
                                                     (False, True), (False, True), (0.25, 24.0)])
good, bad = M.validate_overrides({k: getattr(D, k) for k in KEYS}, D)
check("every default inside its range", not bad and len(good) == len(KEYS), bad)
good, bad = M.validate_overrides({"mm_inv_max_age_h": 0.4, "mm_inv_max_usd": 60000.0, "mm_recycle_concession": 0.06,
                                  "mm_funding_alert_h": 30.0, "mm_refill_fast": "yes"}, D)
check("out of range / not a bool refused (5 of 5)", not good and len(bad) == 5, (good, bad))
src = open(os.path.join(HERE, "..", "mm_bot.py")).read()
check("Config comment block '# --- P14: market-making funding' and MM_FUNDING_KEYS = ('mm_funding',)",
      "# --- P14: market-making funding (owner, 4 Oct 21:10 UTC" in src and M.Bot.MM_FUNDING_KEYS == ("mm_funding",))

# ============================================================================================ flags off = c7c0107
print(f"--- flags off identical to the live head ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p14base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p14base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = (lambda msg: None), (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase"}

if base is not None:
    def twin(inv, overrides, bks=None):
        res = []
        for mod in (M, base):
            api_x, bn = make_bot(books=bks or books())
            if mod is base:
                cfg = base.Config()
                for k in base.Config.__dataclass_fields__:
                    setattr(cfg, k, getattr(bn.cfg, k, getattr(cfg, k)))
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
             {"cash_gate_enabled": True, "alloc_enabled": True, "alloc_mm_reserve": 15000.0},
             {"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0, "value_mode": True,
              "worst_case_backstop_frac": 0.05})
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
            if cut(sn) != cut(sb) or set(sb) - set(sn):
                ok_s = False
                diffs.append(("status", inv, ov, {k for k in set(cut(sn)) | set(cut(sb))
                                                  if cut(sn).get(k) != cut(sb).get(k)}))
            if set(bn.health) != set(bb_.health) or bn.status_report() != bb_.status_report():
                ok_h = False
                diffs.append(("health", inv, ov))
            if bn.summary_ops_line(100000.0) != bb_.summary_ops_line(100000.0):
                ok_l = False
                diffs.append(("summary", inv, ov))
    check("four cycles with fills send the same orders (3 books x plain / value / allocator / room reserve)", ok_w,
          diffs[:1])
    check("every market's quote (and keep limits) identical", ok_q, [d for d in diffs if d[0] == "quote"][:1])
    check("order notes identical (no 'recycle' tag while off)", ok_n, [d for d in diffs if d[0] == "notes"][:1])
    check("status.json identical in keys AND values less mm_funding (and timing keys)", ok_s,
          [d for d in diffs if d[0] == "status"][:1])
    check("health keys and the status line identical", ok_h, [d for d in diffs if d[0] == "health"][:1])
    check("2-hourly summary line identical (no 'MM funding' piece while off)", ok_l,
          [d for d in diffs if d[0] == "summary"][:1])

    ok_p = ok_r = True                            # the pure parts: the allocator's plan and the room update
    for inv, cash in (({"21": 1000, "11": 2000}, 0.0), ({"21": 1000}, 400.0), ({"22": -900, "12": 300}, 5000.0)):
        api_n, bn = mk(inv, books(**{"21": bk(0.545, 0.57), "11": bk(0.12, 0.18)}), alloc_enabled=True,
                       alloc_max_edge_sell=0.05)
        quiet(bn.cycle)
        fresh(bn)
        bn.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
        cfg = base.Config()
        for k in base.Config.__dataclass_fields__:
            setattr(cfg, k, getattr(bn.cfg, k, getattr(cfg, k)))
        bb_ = base.Bot.__new__(base.Bot)
        bb_.__dict__.update(bn.__dict__)
        bb_.basket_legs = {}                # (the base revision's retired long-tilt basket: never any legs)
        bb_.cfg = cfg
        now_m = time.monotonic()
        pn = quiet(bn.alloc_plan, dict(api_n.inv), now_m, cash, set(), None)
        pb = quiet(base.Bot.alloc_plan, bb_, dict(api_n.inv), now_m, cash, set(), None)
        ok_p = ok_p and json.dumps(pn, sort_keys=True, default=str) == json.dumps(pb, sort_keys=True, default=str)
        for args in ((86000.0, 20000.0, 100000.0), (80000.0, 31100.0, 100000.0), (84000.0, 20000.0, None)):
            bn.cfg.mm_risk_reserve_wc, bn.cfg.mm_risk_reserve_corr = 5000.0, 4000.0
            bb_.cfg.mm_risk_reserve_wc, bb_.cfg.mm_risk_reserve_corr = 5000.0, 4000.0
            ok_r = ok_r and (quiet(bn.mm_risk_room_update, *args) == quiet(base.Bot.mm_risk_room_update, bb_, *args)
                             and bn.mmr_room == bb_.mmr_room)
    check("alloc_plan identical with MM lots present (refills, pairs, blocked_by; 3 positions / cash)", ok_p)
    check("mm_risk_room_update identical with the guard off (pause, rooms)", ok_r)

# ============================================================================================ lot tracking
print("--- MM lots: classification, FIFO, reconcile")
api, b = mk({"21": 300, "11": 500})
quiet(b.cycle)
b.mm_lots = {}
now = time.time()
b.order_meta.update({901: {"our_side": "bid", "price": 0.53, "fv": 0.55, "t": now},
                     902: {"our_side": "bid", "price": 0.11, "fv": 0.12, "t": now},
                     903: {"our_side": "bid", "price": 0.53, "fv": 0.55, "take": True, "t": now},
                     904: {"our_side": "ask", "price": 0.57, "fv": 0.55, "t": now},
                     905: {"our_side": "bid", "price": 0.52, "fv": 0.55, "alloc": True, "t": now},
                     906: {"our_side": "ask", "price": 0.58, "fv": 0.55, "recycle": True, "t": now}})


def fill(fid, oid, eid, qty, at=None):
    return {"id": fid, "orderId": oid, "exchangeId": eid, "quantity": qty, "price": 0.5,
            "filledAt": M.iso(M.utcnow() if at is None else at)}


b.mm_inv_step([fill(1, 901, "21", 200)], {"21": 300, "11": 500})
check("a middle-band maker bid fill opens a lot (+200 @ 0.53)", b.mm_lots.get("21") and lot_q(b, "21") == 200
      and b.mm_lots["21"][0][1] == 0.53, b.mm_lots)
b.mm_inv_step([fill(2, 902, "11", 100)], {"21": 300, "11": 500})
check("a tail maker fill (p 0.12 < value_mid_low) is not MM inventory", "11" not in b.mm_lots)
b.mm_inv_step([fill(3, 903, "21", 50), fill(4, 905, "21", 50)], {"21": 300, "11": 500})
check("take / allocator fills are not MM inventory", lot_q(b, "21") == 200)
b.ev_fill_p["5"] = 0.90                           # p at fill (mm_carry_24h's) wins over the quote's fv
b.mm_inv_step([fill(5, 901, "21", 30)], {"21": 330, "11": 500})
check("p at fill outside the band -> not MM (p at fill preferred to fv at quote)", lot_q(b, "21") == 200)
b.mm_inv_step([fill(6, 901, "21", 100, M.utcnow() - timedelta(hours=1))], {"21": 300, "11": 500})
b.mm_inv_step([fill(7, 904, "21", -150)], {"21": 150, "11": 500})
check("an MM ask fill closes the OLDEST lot first (FIFO round trip): 200 + 100 - 150 = 150 left",
      lot_q(b, "21") == 150 and len(b.mm_lots["21"]) == 2 and b.mm_lots["21"][0][0] == 50, b.mm_lots["21"])
b.mm_lots = {}
b.mm_inv_step([fill(8, 904, "21", -100)], {"21": 900})
check("an MM ask that only shrinks a non-MM long opens nothing (direction of the position)", "21" not in b.mm_lots)
b.mm_inv_step([fill(9, 904, "21", -100)], {"21": -100})
check("...but one that leaves us short opens a short lot", lot_q(b, "21") == -100)
b.mm_lots = {"21": [[400.0, 0.53, now - 8 * H]]}
b.ev_fill_p["10"] = 0.95
b.mm_inv_step([fill(10, 906, "21", -100)], {"21": 300})
check("a recycled side's fill closes MM lots even out of the band, and never opens one", lot_q(b, "21") == 300)
b.mm_lots = {"21": [[100.0, 0.53, now - 9 * H], [200.0, 0.54, now - 2 * H]]}
b.mm_inv_step([], {"21": 250})
check("reconcile: a position shrunk by something else (250 < 300) shrinks the oldest lot first",
      b.mm_lots["21"] == [[50.0, 0.53, now - 9 * H], [200.0, 0.54, now - 2 * H]], b.mm_lots)
b.mm_inv_step([], {"21": -10})
check("reconcile: a flipped position drops the lots", "21" not in b.mm_lots)
b.mm_lots = {"21": [[1.0, 0.5, now - k] for k in range(30, 0, -1)]}
b.mm_lot_add("21", True, 5.0, 0.6, now, 100.0)
check(f"at most MM_LOTS_MAX ({M.Bot.MM_LOTS_MAX}) lots a market (oldest merged, the older time kept)",
      len(b.mm_lots["21"]) == M.Bot.MM_LOTS_MAX and lot_q(b, "21") == 35 and b.mm_lots["21"][0][2] == now - 30)

print("--- staleness by age and by $")
b.cfg.mm_inv_max_age_h, b.cfg.mm_inv_max_usd = 6.0, 3000.0
b.mm_lots = {"21": [[400.0, 0.53, now - 7 * H], [200.0, 0.54, now - 1 * H]]}
v = b.mm_view(now)["21"]
check("by age: the 400 shares older than 6 h are stale (not the 1-h lot)", v["stale"] == 400 and v["why"] == "age", v)
check("$ at p: 600 x 0.55 = 330, oldest 7 h", abs(v["usd"] - 330.0) < 1e-6 and abs(v["oldest_h"] - 7) < 1e-6, v)
b.cfg.mm_inv_max_usd = 200.0
v = b.mm_view(now)["21"]
check("by $: 330 over a 200 limit -> ceil(130 / 0.55) = 237 shares; max with the aged 400 -> 400, 'age+$'",
      v["stale"] == 400 and v["why"] == "age+$", v)
b.cfg.mm_inv_max_age_h = 168.0
v = b.mm_view(now)["21"]
check("by $ alone: 237 stale shares", v["stale"] == 237 and v["why"] == "$", v)
b.cfg.mm_inv_max_usd = 0.0
check("mm_inv_max_usd 0 = no $ limit; nothing aged -> 0 stale", b.mm_view(now)["21"]["stale"] == 0)
b.mm_lots = {"22": [[-1000.0, 0.47, now - 7 * H]]}
b.cfg.mm_inv_max_age_h = 6.0
v = b.mm_view(now)["22"]
check("a short's $ = |q| x (1 - p) = 1000 x 0.55", abs(v["usd"] - 550.0) < 1e-6 and v["stale"] == 1000, v)

print("--- persistence and seeding")
api, b = mk({"21": 600})
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, round(now - 7 * H, 1)]]}
b.mmf_handed["count"] = 2
b.write_status(True)
st = json.load(open(b.cfg.status_file))
LOTS = {"21": [[600.0, 0.53, round(now - 7 * H, 1)]]}
check("status.json mm_funding.lots carries the lots", st.get("mm_funding", {}).get("lots") == LOTS,
      st.get("mm_funding", {}).get("lots"))
b2 = M.Bot(api, b.cfg)
check("restart: lots and the hand-over tally restored, no seeding", b2.mm_lots == LOTS
      and b2.mm_lots_seeded and b2.mmf_seed == "restored from status.json" and b2.mmf_handed["count"] == 2)
# seeding: a fresh status without mm_funding, fills.csv + order notes from a real run
api, b = mk({}, cash=100000.0, value_mode=True, ref_weight=1.0)
quiet(b.cycle)
b.drain_writes(5)
api.fill("21", True, 120)                          # other traders hit our middle-band bid and our tail bid
api.fill("11", True, 80)
quiet(b.cycle)
b.drain_writes(5)
quiet(b.cycle)
check("live run: the middle-band bid fill became a lot (+120), the tail fill did not",
      lot_q(b, "21") == 120 and "11" not in b.mm_lots, b.mm_lots)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
st.pop("mm_funding")
json.dump(st, open(b.cfg.status_file, "w"))
b3 = M.Bot(api, b.cfg)
b3.refs = b.refs
check("restart without mm_funding: not seeded yet", not b3.mm_lots_seeded and b3.mm_lots == {})
quiet(b3.cycle)
check("first cycle seeds from fills.csv + order notes (fv_at_quote in the band): +120 in 21, nothing in 11",
      lot_q(b3, "21") == 120 and "11" not in b3.mm_lots and "seeded from fills.csv" in (b3.mmf_seed or ""),
      (b3.mm_lots, b3.mmf_seed))

# ============================================================================================ the recycler
print("--- the recycler (mm_recycle_enabled)")
api, b = mk({"21": 1000}, mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
g = grab()
b.cycle()
b.drain_writes(5)
ungrab(g)
ex = b.ex["21"]
fv = ex.last_fv
want = max(M.ceil_tick(fv - 0.01), M.ceil_tick(0.53 + M.TICK))
check(f"long 1000, 600 MM shares 7 h old: the reducing ask at fair - 1c on the grid ({want:.3f})",
      ex.quote.ask == want, (ex.quote, fv))
check("its size = max(the quoter's, the 600 stale shares) within the position", ex.quote.ask_size == 600,
      ex.quote.ask_size)
check("exactly ONE ask of ours rests there, the recycler's (the quoter's reduce side IS its order)",
      asks(api, "21") == [(want, 600)], api.ours("21"))
oid = next(o["id"] for o in api.orders.values() if o["exchangeId"] == "21")
check("its order note is tagged 'recycle'", b.mm_meta(oid).get("recycle") is True, b.mm_meta(oid))
check("our adding bid (if any) sits at least a tick behind it", ex.quote.bid is None or ex.quote.bid <= want - M.TICK
      + 1e-9, ex.quote)
check("journal 'MM RECYCLE Rep Utah Senate sell 600 @ ...' once", sum("MM RECYCLE Rep Utah Senate sell 600" in x
                                                                     for x in g.lines) == 1, g.lines[-5:])
st = b.mmf_recycling.get("21") or {}
check("mm_funding.recycling records side / qty / price / why", st == {"side": "ask", "qty": 600, "price": want,
                                                                       "why": "age"}, st)
g = grab()
b.cycle()
ungrab(g)
check("unchanged next cycle: no new line, no re-place", not any("MM RECYCLE" in x for x in g.lines)
      and asks(api, "21") == [(want, 600)])
check("other markets (no MM inventory) untouched: Ohio not in recycling", set(b.mmf_recycling) == {"21"})
api.fill("21", False, 100)                         # another trader lifts the recycled ask
quiet(b.cycle)
check("its fill closes MM lots (600 -> 500)", lot_q(b, "21") == 500, b.mm_lots)
car = b.mm_carry(time.time())
check("mm_carry_24h fills.recycle 1 and recycle_ev present", car["fills"].get("recycle") == 1
      and "recycle_ev" in car and car["fills"]["maker_mid"] == 0, car)

# never crossing: the best other bid one tick under fair
api, b = mk({"21": 1000}, bks=books(**{"21": bk(0.55, 0.60)}), mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
quiet(b.cycle)
ex = b.ex["21"]
check("never crossing: best bid 0.55 >= fair - 1c -> the ask at bid + tick 0.555",
      ex.quote.ask == M.ceil_tick(0.55 + M.TICK) and ex.quote.ask > 0.55, (ex.quote, ex.last_fv))
# value floor: concession 3c, value mode - the ask never below p - value_sell_margin
api, b = mk({"21": 1000}, mm_recycle_enabled=True, mm_recycle_concession=0.03, value_mode=True, ref_weight=1.0)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
quiet(b.cycle)
ex = b.ex["21"]
p = b.ev_p("21")
check(f"value mode: fair - 3c is below the floor -> the ask at ceil(p - 0.5c) ({M.ceil_tick(p - 0.005):.3f})",
      ex.quote.ask == M.ceil_tick(p - 0.005) and b.mmf_recycling["21"]["price"] == ex.quote.ask, (ex.quote, p))
# a short: the reducing bid at fair + concession
api, b = mk({"22": -1000}, cash=100000.0, mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"22": [[-600.0, 0.47, time.time() - 7 * H]]}
quiet(b.cycle)
ex = b.ex["22"]
want = min(M.floor_tick(ex.last_fv + 0.01), M.floor_tick(0.47 - M.TICK))
check(f"short 1000: the reducing bid at fair + 1c, below the best ask ({want:.3f}), 600 shares",
      ex.quote.bid == want and ex.quote.bid_size == 600 and (ex.quote.ask is None or ex.quote.ask >= want + M.TICK
                                                             - 1e-9), (ex.quote, ex.last_fv))
# not stale: nothing
api, b = mk({"21": 1000}, mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 1 * H]]}
q0 = None
quiet(b.cycle)
check("lots 1 h old, $330 < $3000: not stale, no override", "21" not in b.mmf_recycling)
# hand-over: edge-held >= hurdle
api, b = mk({"21": 1000}, refs={**REFS, "Utah Senate|Republican": 0.60, "Utah Senate|Democratic": 0.40},
            mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
g = grab()
b.cycle()
ungrab(g)
check("+EV (edge-held (0.60 - 0.53) / 0.53 = 13% >= 5%): handed to the value bucket, lots gone, not recycled",
      "21" not in b.mm_lots and "21" not in b.mmf_recycling and b.mmf_handed["count"] == 1
      and b.mmf_handed["shares"] == 600, (b.mm_lots, b.mmf_handed))
check("journal 'handed to the value bucket'", any("handed to the value bucket" in x for x in g.lines))
# a side the quoter left out stays out
api, b = mk({"21": 1000}, mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
ex = b.ex["21"]
q = quiet(b.mm_recycle_quote, ex, M.Quote(bid=0.50, bid_size=100), ex.last_fv, 0.53, 0.57, None, b.cfg,
          time.monotonic())
check("no reducing side from the quoter (a guard): nothing added, recorded as blocked",
      q == M.Quote(bid=0.50, bid_size=100) and b.mmf_recycling["21"].get("blocked") == "no reducing side")
b.cfg.alloc_pin = "Rep Utah Senate"
q0 = M.Quote(bid=0.50, bid_size=100, ask=0.60, ask_size=100)
q = quiet(b.mm_recycle_quote, ex, q0, ex.last_fv, 0.53, 0.57, None, b.cfg, time.monotonic())
check("a pinned label (alloc_pin) is never pushed out by the recycler", q == q0 and "21" not in b.mmf_recycling)

# ============================================================================================ the fast refill
print("--- the fast refill (mm_refill_fast)")


def refill_bot(**cfg):
    a, bb = mk({"21": 1000, "11": 2000, "12": 1000}, books(**{"21": bk(0.545, 0.57), "11": bk(0.12, 0.18),
                                                              "12": bk(0.86, 0.90)}),
               alloc_enabled=False, alloc_max_edge_sell=0.05, alloc_min_edge_buy=0.5, **cfg)
    quiet(bb.cycle)                               # (warm-up without the allocator: books, fair values, refs)
    bb.drain_writes(5)
    bb.cfg.alloc_enabled = True
    quiet(bb.cancel_everything)
    bb.my_orders.clear()
    a.orders.clear()
    a.wire = []
    fresh(bb)
    bb.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
    bb.alloc_last_run_wall = time.time()          # the hourly run just happened: not due
    read(bb, 0.0)
    return a, bb


def atick(bb):
    time.sleep(0.002)
    return quiet(bb.alloc_tick, M.utcnow(), dict(bb.api.inv), bb.orders_by_eid(M.utcnow()), None, set())


def sells(a):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in a.wire]


api, b = refill_bot()
atick(b)
check("flag off: cash 0 < reserve 1000 but the hourly run is not due -> nothing sold", sells(api) == [])
api, b = refill_bot(mm_refill_fast=True)
g = grab()
time.sleep(0.002)
b.alloc_tick(M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set())
ungrab(g)
s = sells(api)
check("flag on: refilled on this cycle - first the stale MM shares (21: 600 @ 0.545, IOC), then the lowest edge-held "
      "(11: edge 0 @ 0.12)", s[:2] == [("21", "yes", "sell", 0.545, 600), ("11", "yes", "sell", 0.12, 2000)], s)
check("12 (edge-held 2.3% but bid 0.86 < p 0.88 - 0.5c: below the value floor) not sold", all(x[0] != "12" for x in s))
check("blocked_by floor counted; journal 'ALLOC fast refill' and 'MM RECYCLE ... sold by IOC'",
      b.mmf_refill.get("blocked_by", {}).get("floor", 0) >= 1 and any("ALLOC fast refill" in x for x in g.lines)
      and any("stale MM shares sold by IOC" in x for x in g.lines), (b.mmf_refill, g.lines[-6:]))
check("the hourly clock is not moved by a fast refill", abs(b.alloc_last_run_wall - time.time()) < 5
      and b.mmf_refill["runs"] == 1 and b.mmf_refill["mm_sold_usd"] == round(600 * 0.545, 2), b.mmf_refill)
# turnover cap
api, b = refill_bot(mm_refill_fast=True, alloc_max_turnover_per_hour=400.0)
atick(b)
usd = sum(x[3] * x[4] for x in sells(api))
check("alloc_max_turnover_per_hour 400: MM 327 first, then only 73 of 11 (<= $400 in all)",
      sells(api)[0][:2] == ("21", "yes") and usd <= 400.0 + 1e-6 and usd > 380, (sells(api), usd))
# write cap: one order a cycle
api, b = refill_bot(mm_refill_fast=True, alloc_max_orders_per_cycle=1)
atick(b)
n1 = len(sells(api))
atick(b)
check("alloc_max_orders_per_cycle 1: one sale a cycle (MM first, the value position the next cycle)",
      n1 == 1 and [x[0] for x in sells(api)] == ["21", "11"], sells(api))
# the concession gate: bid 0.53 is 2c under fair -> no IOC, it rests through the recycler
api, b = refill_bot(mm_refill_fast=True)
api.books["21"] = bk(0.53, 0.57)
quiet(b.alloc_download, b.ex["21"], {})
read(b, 0.0)
atick(b)
check("MM bid 2c under fair (> 1c concession): no IOC on 21 (blocked_by mm_resting), 11 still sold",
      all(x[0] != "21" for x in sells(api)) and b.mmf_refill.get("blocked_by", {}).get("mm_resting") == 1
      and any(x[0] == "11" for x in sells(api)), (sells(api), b.mmf_refill))
# RT13-3: the positions read lags the sale -> not planned again
api, b = refill_bot(mm_refill_fast=True, alloc_max_turnover_per_hour=330.0)
atick(b)
check("first refill sold 21 (600)", sells(api)[:1] == [("21", "yes", "sell", 0.545, 600)], sells(api))
api.inv["21"] = 1000                               # a lagging positions read still shows the 600 shares
b.alloc_pairs, b.mmf_refill_last_m = [], -1e18
api.wire = []
read(b, 0.0)
fresh(b)
atick(b)
check("RT13-3: positions read still at 1000 -> 21 not sold again (blocked_by in_flight)",
      all(x[0] != "21" for x in sells(api)) and b.mmf_refill.get("blocked_by", {}).get("in_flight", 0) >= 1,
      (sells(api), b.mmf_refill))
b.mmf_sent["21"] = (time.monotonic() - M.Bot.MM_SENT_LAG - 1, 1000.0, 600.0)
b.alloc_flows.clear()                              # (the hour's turnover cap set aside)
b.alloc_pairs, b.mmf_refill_last_m = [], -1e18
api.wire = []
read(b, 0.0)
fresh(b)
atick(b)
check(f"...after MM_SENT_LAG ({M.Bot.MM_SENT_LAG:.0f} s) it may be planned again",
      any(x[0] == "21" for x in sells(api)), sells(api))
# MM_REFILL_GAP and the reserve: no refill while cash >= reserve
api, b = refill_bot(mm_refill_fast=True)
read(b, 5000.0)
atick(b)
check("cash 5000 >= reserve 1000: no refill", sells(api) == [])
read(b, 0.0)
b.mmf_refill_last_m = time.monotonic()
atick(b)
check(f"within MM_REFILL_GAP ({M.Bot.MM_REFILL_GAP:.0f} s) of the last plan: none", sells(api) == [])
# hourly run with the flag: refills MM first and respect the floor too
api, b = refill_bot(mm_refill_fast=True)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("alloc_plan (the hourly run too): refill legs MM first ('mm' leg), then 11; 12 blocked by the floor",
      [p_["sell"].get("eid") for p_ in pairs][:2] == ["21", "11"] and pairs[0]["sell"].get("mm") is True
      and info["blocked_by"].get("floor", 0) >= 1, (pairs, info))
check("the journal names it 'stale MM inventory'", "stale MM inventory" in b.alloc_journal(pairs[0]))

# ============================================================================================ the room guard
print("--- the room guard (mm_room_guard)")
api, b = mk({}, mm_risk_reserve_wc=5000.0, worst_case_backstop_frac=0.90, max_worst_case_frac=0.35)
check("guard off: room_wc 4k < 5k -> paused", quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0) is True)
b.mmr_paused = False
check("guard: MM inventory 2k of the worst case counted against the MM room -> value share 6k: not paused",
      quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0, (2000.0, 0.0)) is False
      and b.mmf_room_value[0] == 6000.0 and b.mmr_room[0] == 4000.0)
check("guard: MM 800 -> value share 4.8k < 5k: paused",
      quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0, (800.0, 0.0)) is True)
check("hysteresis: value share 5.3k (>= 5k, < 5.5k) still paused; 5.6k resumed",
      quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0, (1300.0, 0.0)) is True
      and quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0, (1600.0, 0.0)) is False)
quiet(b.mm_risk_room_update, 86000.0, 20000.0, 100000.0, (9000.0, 0.0))
check("the MM inventory counts only up to the reserve: MM 9k -> value share 4k + 5k = 9k",
      b.mmf_room_value[0] == 9000.0)
b.cfg.mm_risk_reserve_corr = 4000.0
check("corr too: room_corr 3.9k, MM 0 -> paused; MM 600 -> 4.5k resumed (>= 4.4k)",
      quiet(b.mm_risk_room_update, 80000.0, 31100.0, 100000.0, (0.0, 0.0)) is True
      and quiet(b.mm_risk_room_update, 80000.0, 31100.0, 100000.0, (0.0, 600.0)) is False)
# accounting on a cycle
api, b = mk({"21": 1000, "11": 3000}, mm_room_guard=True, mm_risk_reserve_wc=5000.0)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time()]]}
quiet(b.cycle)
inv = {e: float(q) for e, q in api.inv.items()}
fvs = {e: x.last_fv for e, x in b.ex.items()}
worst = b.total_worst_case(inv, fvs)
worst_v = b.total_worst_case({**inv, "21": 400.0}, fvs)
check("MM contribution = worst case - worst case without the 600 MM shares",
      abs(b.mmf_room_mm[0] - (worst - worst_v)) < 1e-6 and b.mmf_room_mm[0] > 0, (b.mmf_room_mm, worst, worst_v))
mmr = b.health.get("mm_risk_room") or {}
check("status mm_risk_room carries value_share_wc / _corr while the guard is on", "value_share_wc" in mmr, mmr)
# the recycler is not paused by it
api, b = mk({"21": 1000, "11": 3000}, mm_recycle_enabled=True, mm_room_guard=True, mm_risk_reserve_wc=50000.0,
            worst_case_backstop_frac=0.3)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
quiet(b.cycle)
check("value adds paused (reserve 50k) and the recycler still works the stale MM shares",
      b.mmr_paused and b.mmf_recycling.get("21", {}).get("price") is not None, (b.mmr_paused, b.mmf_recycling))

# ============================================================================================ monitoring
print("--- monitoring: status.json mm_funding, the alert, the summary")
api, b = mk({"21": 1000}, mm_risk_reserve_wc=5000.0, mm_risk_reserve_corr=4000.0)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
read(b, 300.0)
b.write_status(True)
mf = json.load(open(b.cfg.status_file)).get("mm_funding") or {}
need = {"cash_free", "cash_target", "room_free", "room_target", "inventory_usd", "oldest_inventory_h", "stale_markets",
        "recycling", "handed_to_value", "below_half_since"}
check("status.json mm_funding has every owner field", need <= set(mf), sorted(need - set(mf)))
check("cash_free 300 / cash_target 1000 (alloc_mm_reserve)", mf.get("cash_free") == 300.0
      and mf.get("cash_target") == 1000.0, mf)
check("room_free / room_target (wc and corr)", set(mf.get("room_free", {})) == {"wc", "corr"}
      and mf.get("room_target") == {"wc": 5000.0, "corr": 4000.0}, mf.get("room_target"))
check("inventory $600 x p, 1 stale market, oldest ~7 h", abs(mf["inventory_usd"] - 600 * b.ev_p("21")) < 0.01
      and mf["stale_markets"] == 1 and abs(mf["oldest_inventory_h"] - 7) < 0.01, mf)
check("flags off: the key is written all the same (read-only), no summary piece",
      "mm_funding" in json.load(open(b.cfg.status_file)) and "MM funding" not in (b.summary_ops_line(1e5) or ""))
ALERTS.clear()
b.mm_funding_tick()
check("cash 300 < half of 1000: the below-half clock starts", b.mmf_below_since is not None)
b.mmf_below_since -= 3 * H
b.mm_funding_tick()
check("flags off: no alert even after 3 h", ALERTS == [])
b.cfg.mm_recycle_enabled = True
b.mmf_below_since = time.time() - 1.5 * H
b.mm_funding_tick()
check("a flag on, 1.5 h below (< 2 h): no alert yet", ALERTS == [])
b.mmf_below_since = time.time() - 2.1 * H
b.mm_funding_tick()
b.mm_funding_tick()
check("2.1 h below: ONE alert naming the cash", len(ALERTS) == 1 and "cash 300 of 1,000" in ALERTS[0], ALERTS)
read(b, 900.0)
b.mm_funding_tick()
check("cash back above half: the clock cleared and the alert re-armed", b.mmf_below_since is None and not b.mmf_alerted)
read(b, 100.0)
b.mm_funding_tick()
b.mmf_below_since = time.time() - 2.5 * H
b.mm_funding_tick()
check("below again for > 2 h: a second alert", len(ALERTS) == 2, ALERTS)
b.write_status(True)
mf = json.load(open(b.cfg.status_file))["mm_funding"]
b4 = M.Bot(api, b.cfg)
check("the alert clock survives a restart (below_half_since, alerted)", b4.mmf_alerted and
      abs(b4.mmf_below_since - b.mmf_below_since) < 1 and mf["below_half_since"], (b4.mmf_below_since, mf))
line = b.summary_ops_line(1e5) or ""
check("summary piece 'MM funding cash 0.1k/1.0k, room wc ..., inventory ... BELOW HALF since' with a flag on",
      "MM funding cash 0.1k/1.0k" in line and "room wc" in line and "inventory" in line and "BELOW HALF" in line,
      line)
b.mmr_room = (1000.0, 3000.0)
read(b, 5000.0)
check("a room below half also counts (wc 1000 of 5000)", b.mm_funding_below() == ["risk room wc 1,000 of 5,000"],
      b.mm_funding_below())

# ============================================================================================ warnings
print("--- warnings")
api, b = mk({}, mm_refill_fast=True, mm_room_guard=True)
g = grab()
b.warn_settings()
ungrab(g)
check("mm_refill_fast without alloc_enabled / mm_room_guard without a reserve: one warning line",
      sum("MM funding:" in x for x in g.lines) == 1 and "alloc_enabled" in " ".join(g.lines)
      and "mm_room_guard" in " ".join(g.lines), g.lines)

# ============================================================================================ staged file, py_compile
print("--- staged file, py_compile 3.10")
dep = os.path.join(HERE, "..", "deploy", "package14", "settings_override.mm_funding.json")
try:
    with open(dep) as f:
        ov = json.load(f)
except (OSError, ValueError) as e:
    ov = {"_error": str(e)}
good, bad = M.validate_overrides({k: v for k, v in ov.items() if not k.startswith("_")}, M.Config())
check("staged file: every key valid", not bad and good, bad)
check("owner's live values: alloc_mm_reserve 20000, mm_risk_reserve_wc 20000 / _corr 4000, backstop 1.0",
      (ov.get("alloc_mm_reserve"), ov.get("mm_risk_reserve_wc"), ov.get("mm_risk_reserve_corr"),
       ov.get("worst_case_backstop_frac")) == (20000.0, 20000.0, 4000.0, 1.0), ov)
check("the three flags on, the proposed defaults explicit",
      all(ov.get(k) is True for k in ("mm_recycle_enabled", "mm_refill_fast", "mm_room_guard"))
      and (ov.get("mm_inv_max_age_h"), ov.get("mm_inv_max_usd"), ov.get("mm_recycle_concession"),
           ov.get("mm_funding_alert_h")) == (6.0, 3000.0, 0.01, 2.0))
check("never resets capital_ceiling_adding_size_factor / arb_enabled / ref_tilt_headline (live 0.5 / false / true)",
      (ov.get("capital_ceiling_adding_size_factor"), ov.get("arb_enabled"), ov.get("ref_tilt_headline"))
      == (0.5, False, True), ov)
py310 = shutil.which("python3.10")
if py310:
    r = subprocess.run([py310, "-m", "py_compile", os.path.join(HERE, "..", "mm_bot.py"), os.path.abspath(__file__)],
                       capture_output=True, text=True, timeout=120)
    check("py_compile on python3.10 (mm_bot.py and this test)", r.returncode == 0, r.stderr[-300:])
else:
    check("python3.10 available for py_compile", False)

n_ok, n = sum(RESULTS), len(RESULTS)
print(f"{n_ok}/{n} passed")
sys.exit(0 if n_ok == n else 1)
