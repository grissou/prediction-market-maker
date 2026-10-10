"""Package 14.1 (owner, 5 Oct 10:30 UTC): the MM funding refill and the allocator's swaps, both stuck live on
Package 14 (4ff7d91). Diagnosis: analysis/p14/DIAG_14_1.md. Every setting OFF by default:
  alloc_cancel_mm_first           stale MM shares are ordinary refill candidates (the refill's own price rule, not the
                                  concession gate): the IOC cancels our quotes there first and the REDUCING quote side
                                  is held until the positions read shows the sale ("mm_resting" is no blocker)
  alloc_rank_all_markets          every holding ranked (not only edge-held <= alloc_max_edge_sell), a blocked candidate
                                  skipped and counted, the refill every cycle (no 60 s gap) and also while an hourly
                                  run's pairs are in flight
  mm_recycle_sell_first           recycled sales sent before the rest, buy-backs after them and judged by their NET
                                  cash (gate need less the (1 - price) a fill frees), deferred below half the target
  alloc_refill_ignore_prefer_short  below half the target a refill plan scans no buy levels at all (no L2 count)
  alloc_swap_room_netting         swaps go on while value adds are paused: each pair is admitted only while both rooms
                                  stay >= min(the room now, the reserve) after its sale AND its buy; its buy may spend
                                  its own sale's proceeds below the cash reserve, never more
  alloc_refill_max_cost           > 0: a refill sale may go this far below p (not only value_sell_margin) below half
                                  the cash target; 0 = the value floor as before
  reporting                       mm_funding {refill_runs, refill_sold_usd, refill_ev_given_24h, deferred_buybacks,
                                  refill_holds, cash_locked}, alloc {swaps_planned / _24h, swaps_done_24h, swaps_usd_24h,
                                  ev_gain_est_24h, ev_gain_realised_24h, refill_blocked_by}, the summary piece
Sections: settings / ranges; flags off identical to 4ff7d91 on a grid (orders, quotes, notes, status less the new keys,
health, summary, alloc_plan, change_key); the refill with MM resting (cancel first, one write, no re-quote); the
cross-market ranking; the every-cycle refill under the caps; the recycler's order and the buy-back net cash; the
prefer-short skip; the swaps under the reserve; alloc_max_edge_sell / the min-gain hurdle; the reports; no duplicate
orders; empty books / no reference / unknown markets; the staged file; py_compile on 3.10.
Run:  python tests/test_mm_funding_14_1.py      (exit code 0 = all passed)"""
import importlib.util
import json
import logging
import os
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
BASE_REV = "4ff7d91"                              # the Package 14 head this package is built on (LIVE)
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
    """Ohio (tails: p 0.12 / 0.88) and Utah (middle: p 0.55 / 0.45), as tests/test_mm_funding.py."""
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


ON = {"alloc_cancel_mm_first": True, "alloc_rank_all_markets": True, "mm_recycle_sell_first": True,
      "alloc_refill_ignore_prefer_short": True, "alloc_swap_room_netting": True}

# ============================================================================================ settings
print("--- settings")
D = M.Config()
KEYS = ["alloc_cancel_mm_first", "alloc_rank_all_markets", "mm_recycle_sell_first",
        "alloc_refill_ignore_prefer_short", "alloc_swap_room_netting", "alloc_refill_max_cost"]
check("defaults: every 14.1 flag off, alloc_refill_max_cost 0 (= the value floor)",
      [getattr(D, k) for k in KEYS] == [False, False, False, False, False, 0.0])
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after mm_funding_alert_h, in Config and OVERRIDABLE",
      _f[_f.index("mm_funding_alert_h") + 1:][:len(KEYS)] == KEYS
      and _ov[_ov.index("mm_funding_alert_h") + 1:][:len(KEYS)] == KEYS)
check("ranges", [M.OVERRIDABLE[k] for k in KEYS] == [(False, True)] * 5 + [(0.0, 0.05)],
      [M.OVERRIDABLE[k] for k in KEYS])
good, bad = M.validate_overrides({k: getattr(D, k) for k in KEYS}, D)
check("every default inside its range", not bad and len(good) == len(KEYS), bad)
good, bad = M.validate_overrides({"alloc_refill_max_cost": 0.06, "alloc_cancel_mm_first": "yes",
                                  "alloc_swap_room_netting": 2}, D)
check("out of range / not a bool refused (3 of 3)", not good and len(bad) == 3, (good, bad))
src = open(os.path.join(HERE, "..", "mm_bot.py")).read()
check("Config comment block '# --- P14.1: the refill and the swaps unstuck' and Bot.P141_FLAGS (the five flags)",
      "# --- P14.1: the refill and the swaps unstuck" in src and set(M.Bot.P141_FLAGS) == set(KEYS[:5]))
_, b_ = mk()
check("p141_on(): False with every setting off, True with any one on (alloc_refill_max_cost counts)",
      not b_.p141_on() and all(b_.p141_on(type(b_.cfg)(**{k: v})) for k, v in
                               list({k: True for k in KEYS[:5]}.items()) + [("alloc_refill_max_cost", 0.02)]))
check("p141(name): a refill flag acts only with mm_refill_fast on (it changes the fast refill)",
      not b_.p141("alloc_rank_all_markets")
      and all(not setattr(b_.cfg, k, v) for k, v in (("alloc_rank_all_markets", True), ("mm_refill_fast", False)))
      and not b_.p141("alloc_rank_all_markets")
      and (setattr(b_.cfg, "mm_refill_fast", True) or b_.p141("alloc_rank_all_markets")))

# ============================================================================================ flags off = 4ff7d91
print(f"--- flags off identical to the Package 14 head ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p141base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p141base", path)
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
             {"cash_gate_enabled": True, "alloc_enabled": True, "alloc_mm_reserve": 15000.0,
              "mm_refill_fast": True, "mm_recycle_enabled": True},
             {"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0, "value_mode": True,
              "worst_case_backstop_frac": 0.05, "mm_room_guard": True, "alloc_enabled": True,
              "cash_gate_enabled": True})
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
    check("four cycles with fills send the same orders (3 books x plain / value / P14 flags on / room reserve)", ok_w,
          diffs[:1])
    check("every market's quote (and keep limits) identical", ok_q, [d for d in diffs if d[0] == "quote"][:1])
    check("order notes identical", ok_n, [d for d in diffs if d[0] == "notes"][:1])
    check("status.json identical in keys AND values less mm_funding (no new alloc key while off)", ok_s,
          [d for d in diffs if d[0] == "status"][:1])
    check("health keys and the status line identical", ok_h, [d for d in diffs if d[0] == "health"][:1])
    check("2-hourly summary line identical (no refill / swap piece while off)", ok_l,
          [d for d in diffs if d[0] == "summary"][:1])

    ok_p = True                                   # the pure planner, with MM lots and the rooms in play
    for inv, cash in (({"21": 1000, "11": 2000}, 0.0), ({"21": 1000}, 400.0), ({"22": -900, "12": 300}, 5000.0)):
        api_n, bn = mk(inv, books(**{"21": bk(0.545, 0.57), "11": bk(0.12, 0.18)}), alloc_enabled=True,
                       alloc_max_edge_sell=0.05, mm_refill_fast=True, mm_risk_reserve_wc=5000.0)
        quiet(bn.cycle)
        fresh(bn)
        bn.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
        bn.mmr_paused = True
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
    check("alloc_plan identical with MM lots and the pause on (refills, pairs, blocked_by; 3 positions / cash)", ok_p)
    api_n, bn = mk({"21": 1000}, alloc_enabled=True)
    quiet(bn.cycle)
    k_off = bn.change_key(bn.ex["21"], pull=False)
    k_head = bn.change_key(bn.ex["11"], pull=False)
    bn.mmf_recycling = {"21": {"side": "ask", "qty": 10, "price": 0.56, "why": "age"}}
    check("change_key: the key keeps 4ff7d91's exact shape while mm_recycle_sell_first is off (no recycle slot, the "
          "order of changes unchanged)",
          bn.change_key(bn.ex["21"], pull=False) == k_off and len(k_off) == 4
          and bn.change_key(bn.ex["11"], pull=False) == k_head, (k_off, k_head))

# ============================================================================================ the refill with MM resting
print("--- (1) alloc_cancel_mm_first: the refill with our own MM quotes resting")


def refill_bot(keep_orders=False, fair=0.58, **cfg):
    """1000 long in 21 (p 0.55, book 0.545 / 0.57: at the value floor, 3.5c from the fair value we set), 2000 in 11
    (p 0.12, bid 0.12: edge 0), 1000 in 12 (p 0.88, bid 0.86: below the floor); 600 stale MM shares in 21; cash 0
    against a 1000 reserve; the hourly run just happened (not due)."""
    kw = {"alloc_enabled": False, "alloc_max_edge_sell": 0.05, "alloc_min_edge_buy": 0.5, **cfg}
    a, bb = mk({"21": 1000, "11": 2000, "12": 1000}, books(**{"21": bk(0.545, 0.57), "11": bk(0.12, 0.18),
                                                              "12": bk(0.86, 0.90)}), **kw)
    quiet(bb.cycle)                               # (warm-up without the allocator: books, fair values, refs)
    bb.drain_writes(5)
    bb.cfg.alloc_enabled = True
    if not keep_orders:
        quiet(bb.cancel_everything)
        bb.my_orders.clear()
        a.orders.clear()
    a.wire = []
    a.calls = []
    fresh(bb)
    bb.ex["21"].last_fv = fair                    # the fair value the recycler / refill measure the gap from
    bb.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
    bb.alloc_last_run_wall = time.time()          # the hourly run just happened: not due
    read(bb, 0.0)
    return a, bb


api, b = refill_bot(mm_refill_fast=True)
atick(b)
check("4ff7d91: the stale MM bid is 3.5c from fair (> the 1c concession) -> the MM leg is refused (blocked_by "
      "mm_resting) and nothing is sold as stale MM",
      b.mmf_refill["blocked_by"].get("mm_resting") == 1 and b.mmf_refill["mm_sold_usd"] == 0.0, b.mmf_refill)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True)
g = grab()
time.sleep(0.002)
b.alloc_tick(M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set())
ungrab(g)
s = sells(api)
check("flag on: the stale MM shares go at the touch (600 @ 0.545, at the value floor), 'mm_resting' no longer "
      "counted", s[:1] == [("21", "yes", "sell", 0.545, 600)] and not b.mmf_refill["blocked_by"].get("mm_resting"),
      (s, b.mmf_refill))
check("the sale is the stale-MM leg ('stale MM shares sold by IOC'), counted in refill.mm_sold_usd",
      any("stale MM shares sold by IOC" in x for x in g.lines)
      and b.mmf_refill["mm_sold_usd"] == round(600 * 0.545, 2), (b.mmf_refill, g.lines[-4:]))
check("the floor still binds: 12 (bid 0.86 < p 0.88 - 0.5c) is not sold, counted 'floor'",
      all(x[0] != "12" for x in sells(api)) and b.mmf_refill["blocked_by"].get("floor", 0) >= 1,
      (sells(api), b.mmf_refill))
# our own resting quotes in the candidate market: cancelled first, in ONE write, before the sale
api, b = refill_bot(keep_orders=True, mm_refill_fast=True, alloc_cancel_mm_first=True)
own21 = [o for o in api.orders.values() if o["exchangeId"] == "21"]
atick(b)
seq = [c for c in api.calls if c[0] in ("cancel_all", "cancel_order", "batch")]
first = seq[:seq.index(("batch", 1)) + 1] if ("batch", 1) in seq else seq
check("our resting quotes in 21 are cancelled first: ONE whole-exchange cancel write, then the batch (the IOC's "
      "leftover is cancelled after it, as every allocator order)",
      own21 and first == [("cancel_all", "21"), ("batch", 1)]
      and not any(c[0] == "cancel_order" for c in api.calls), (own21, api.calls))
check("...and the sale goes out in the same cycle (600 @ 0.545)",
      sells(api)[:1] == [("21", "yes", "sell", 0.545, 600)] and not [o for o in api.orders.values()
                                                                     if o["exchangeId"] == "21"], sells(api))
# no re-quote before the sale is seen: the reducing side is held while the positions read lags
api.inv["21"] = 1000                              # a lagging positions read still shows the 600 shares
quiet(b.cycle)
b.drain_writes(5)
check("the positions read still lags: the REDUCING side (the ask) is held off this cycle - no re-quote of what was "
      "just sold", b.ex["21"].quote.ask is None and not [o for o in api.orders.values()
                                                         if o["exchangeId"] == "21" and o["action"] == "sell"],
      (b.ex["21"].quote, api.ours("21")))
check("the ADDING side keeps quoting (the market maker is not switched off there)",
      b.ex["21"].quote.bid is not None and b.mm_funding_fields()["refill_holds"] == 1, b.ex["21"].quote)
check("no recycle line / record while the sale is in flight", "21" not in b.mmf_recycling)
api.inv["21"] = 400                               # the read catches up (1000 - 600)
quiet(b.cycle)
b.drain_writes(5)
check("once the read shows the sale the reducing side comes back (the hold is cleared)",
      b.ex["21"].quote.ask is not None and b.mm_funding_fields()["refill_holds"] == 0, b.ex["21"].quote)

# ============================================================================================ cross-market ranking
print("--- (2) alloc_rank_all_markets: candidates ranked across all markets")
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    alloc_max_edge_sell=0.0)
atick(b)
s = sells(api)
check("edge-rich holdings are refill candidates too (alloc_max_edge_sell 0 is the SWAPS' rule): 21 and 11 sold",
      {x[0] for x in s} >= {"21", "11"}, s)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
eids = [p["sell"].get("eid") for p in pairs]
check("the plan ranks every market: the blocked one (12, below the floor) is skipped and counted, the run goes on "
      "to the next", eids and "12" not in eids and info["blocked_by"].get("floor", 0) >= 1, (eids, info))
check("...ranked lowest edge-held first, the stale MM shares first of all",
      eids[0] == "21" and pairs[0]["sell"].get("mm") is True, (eids, pairs[0]["sell"]))
# a blocked TOP candidate does not stop the run (the top one is in flight)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
b.mmf_sent["21"] = (time.monotonic(), 1000.0, 600.0)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("the top candidate blocked (in flight): the run still plans the next market",
      [p["sell"].get("eid") for p in pairs] and "21" not in [p["sell"].get("eid") for p in pairs]
      and info["blocked_by"].get("in_flight", 0) >= 1, (pairs, info))
# the cached book ranks a market whose fresh book has gone stale (the sale downloads a fresh one anyway)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
for x in b.ex.values():
    x.verified = time.monotonic() - b.cfg.book_stale - 1
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("a holding whose book is no longer fresh is still ranked (from the cached book)",
      [p["sell"].get("eid") for p in pairs][:1] == ["21"], pairs)
api, b = refill_bot(mm_refill_fast=True, alloc_rank_all_markets=True)
for x in b.ex.values():
    x.book, x.verified = None, time.monotonic()
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("a market whose book was never downloaded is not ranked (nothing to sell into)", pairs == [], pairs)

# ============================================================================================ every cycle, the caps
print("--- (2) the fast refill every cycle, inside the caps")
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True)
atick(b)
n0 = len(sells(api))
atick(b)
check(f"4ff7d91: a second plan inside MM_REFILL_GAP ({M.Bot.MM_REFILL_GAP:.0f} s) is skipped",
      len(sells(api)) == n0 and b.mmf_refill["runs"] == 1, (sells(api), b.mmf_refill))
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    alloc_max_orders_per_cycle=1)
atick(b)
api.inv["21"] = 400                               # the sale is read
read(b, 0.0)
fresh(b)
atick(b)
check("flag on: a refill runs EVERY cycle while cash < reserve (2 runs in 2 cycles, one order each)",
      b.mmf_refill["runs"] == 2 and len(sells(api)) == 2, (b.mmf_refill, sells(api)))
check("the hourly clock is still not moved by a fast refill", abs(b.alloc_last_run_wall - time.time()) < 10)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    alloc_max_turnover_per_hour=400.0)
atick(b)
usd = sum(x[3] * x[4] for x in sells(api))
check("alloc_max_turnover_per_hour 400 holds across the cycle's refills", usd <= 400.0 + 1e-6 and usd > 300, usd)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
api.writes_left = lambda: 2                       # the allocator's share: floor(0.3 x 2 / 3) = 0 orders
atick(b)
check("the allocator's write share still caps the orders (writes_left 2 -> none sent)", sells(api) == [], sells(api))
# a refill is added to a run already in flight (4ff7d91: it waits for the run to finish)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True)
b.alloc_pairs = [{"sell": {"kind": "long", "eid": "11", "label": "x", "qty": 10, "px": 0.12, "edge": 0.0,
                           "usd": 1.2},
                  "buy": {"eid": "12", "label": "y", "short": False, "px": 0.90, "qty": 1, "edge": 0.1, "usd": 0.9},
                  "usd": 1.2, "status": "sold", "proceeds": 1.2, "sold_at": time.monotonic(),
                  "planned_at": time.monotonic()}]
atick(b)
check("4ff7d91: a pair in flight blocks the fast refill altogether", b.mmf_refill["runs"] == 0, b.mmf_refill)
b.cfg.alloc_rank_all_markets = True
b.mmf_refill_last_m = -1e18
atick(b)
check("flag on: the refill is ADDED to the run in flight, the pair in flight kept, its markets skipped",
      b.mmf_refill["runs"] == 1 and any(p["status"] == "sold" for p in b.alloc_pairs)
      and all(x[0] != "11" for x in sells(api)) and any(x[0] == "21" for x in sells(api)),
      (b.mmf_refill, sells(api), [p["status"] for p in b.alloc_pairs]))

# ============================================================================================ the recycler
print("--- (3) mm_recycle_sell_first: sales first, buy-backs by net cash")
api, b = mk({"21": 1000, "22": -1000}, cash=100000.0, mm_recycle_enabled=True, mm_recycle_sell_first=True)
quiet(b.cycle)
read(b, 100000.0)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]], "22": [[-600.0, 0.47, time.time() - 7 * H]]}
quiet(b.cycle)
k_sell, k_buy = b.change_key(b.ex["21"], pull=False), b.change_key(b.ex["22"], pull=False)
check("a recycled SALE (the ask on a long) sorts before a recycled BUY-BACK (the bid on a short)",
      k_sell < k_buy and len(k_sell) == 5 and k_sell[2] == -1 and k_buy[2] == 1,
      (k_sell, k_buy, b.mmf_recycling))
check("...and before an ordinary change in the same tier", k_sell < b.change_key(b.ex["12"], pull=False))
check("cash well above half the target: the buy-back is quoted in full (600 shares)",
      b.mmf_recycling["22"]["qty"] == 600 and b.mmf_deferred == 0, (b.mmf_recycling, b.mmf_deferred))
b.cfg.reduce_no_as_sell = True
ok, need, frees = b.buyback_net("22", -1000.0, 0.48, 600)
check("buyback_net: a covered lone-NO buy-back needs 0 at the gate and frees (1 - price) a share",
      ok == 600 and need == 0.0 and abs(frees - 600 * 0.52) < 1e-6, (ok, need, frees))
api2, b2 = mk({"21": -200}, cash=1000.0, mm_recycle_enabled=True, mm_recycle_sell_first=True,
              reduce_no_as_sell=False)
quiet(b2.cycle)
ok2, need2, frees2 = b2.buyback_net("21", -200.0, 0.56, 200)
check("an UNCOVERED YES bid (reduce_no_as_sell off) locks its price a share, more than the 1 - price it frees: 0 "
      "shares pass", ok2 == 0 and abs(need2 - 200 * 0.56) < 1e-6 and abs(frees2 - 200 * 0.44) < 1e-6,
      (ok2, need2, frees2))
api2.cash = 100.0                                 # below half the 1000 cash target (the cycle reads it)
b2.cg_cash, b2.cg_reserved, b2.cg_spent, b2.cg_read_at = 100.0, 0.0, 0.0, time.monotonic()
b2.mm_lots = {"21": [[-200.0, 0.57, time.time() - 7 * H]]}
g = grab()
quiet(b2.cycle)
ungrab(g)
check("below half the cash target such a buy-back is DEFERRED (counted, the bid not pushed in)",
      b2.mmf_deferred >= 200 and "deferred" in (b2.mmf_recycling.get("21") or {}).get("blocked", ""),
      (b2.mmf_deferred, b2.mmf_recycling))
check("mm_funding.deferred_buybacks reports it", b2.mm_funding_fields()["deferred_buybacks"] >= 200)
api2.cash = b2.cg_cash = 1000.0                   # back above half the target
b2.mmf_deferred = 0
quiet(b2.cycle)
check("above half the target it is quoted again", not (b2.mmf_recycling.get("21") or {}).get("blocked")
      and b2.mmf_deferred == 0, b2.mmf_recycling)
api, b = mk({"21": 1000, "22": -1000}, cash=100000.0, mm_recycle_enabled=True)
quiet(b.cycle)
b.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]], "22": [[-600.0, 0.47, time.time() - 7 * H]]}
quiet(b.cycle)
check("flag off: the recycler's own order and size are unchanged (no deferral, no re-ordering)",
      b.mmf_recycling["22"]["qty"] == 600 and b.mmf_deferred == 0
      and len(b.change_key(b.ex["21"], pull=False)) == 4, (b.mmf_recycling, b.mmf_deferred))

# ============================================================================================ prefer_short
print("--- (4) alloc_refill_ignore_prefer_short")
BIDS1 = books(**{"21": bk(0.545, 0.57), "22": bk(0.50, 0.54), "11": bk(0.12, 0.18), "12": bk(0.86, 0.90)})


def ps_bot(**cfg):
    kw = {"alloc_enabled": False, "alloc_max_edge_sell": 0.05, "alloc_prefer_short": True,
          "alloc_min_edge_buy": 0.02, **cfg}
    a, bb = mk({"21": 1000, "11": 2000, "12": 1000}, BIDS1, **kw)
    quiet(bb.cycle)
    bb.drain_writes(5)
    bb.cfg.alloc_enabled = True
    quiet(bb.cancel_everything)
    bb.my_orders.clear()
    a.orders.clear()
    a.wire = []
    fresh(bb)
    bb.ex["21"].last_fv = 0.58
    bb.mm_lots = {"21": [[600.0, 0.53, time.time() - 7 * H]]}
    read(bb, 0.0)
    return a, bb


api, b = ps_bot(mm_refill_fast=True, alloc_cancel_mm_first=True)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("4ff7d91: a refill plan still scans buy levels - the L2 prefer_short count lands in the refill's blocked_by",
      info["blocked_by"].get("prefer_short", 0) >= 1, info)
api, b = ps_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_refill_ignore_prefer_short=True)
pairs2, info2 = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("flag on, cash 0 < half the 1000 target: no buy-level scan at all - no prefer_short / mm_risk_reserve in the "
      "refill's blocked_by, the same sales planned",
      not info2["blocked_by"].get("prefer_short") and not info2["blocked_by"].get("mm_risk_reserve")
      and [p["sell"].get("eid") for p in pairs2] == [p["sell"].get("eid") for p in pairs], (info2, pairs2))
read(b, 900.0)                                    # above half the target (but still below it)
pairs3, info3 = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 900.0, set(), None, True)
check("above half the target the L2 scan is back (the flag is only for a short-cash refill)",
      info3["blocked_by"].get("prefer_short", 0) >= 1, info3)
read(b, 0.0)
pairs4, info4 = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("an hourly run (not refill_only) always scans its buy levels, flag or not",
      info4["blocked_by"].get("prefer_short", 0) >= 1, info4)

# ============================================================================================ the swaps
print("--- (5) alloc_swap_room_netting: swaps while value adds are paused")


def swap_bot(room=20000.0, **cfg):
    """1000 long in 21 (edge-held 1.9% at the 0.545 bid: a swap candidate) and a 10% buy level in 11 (ask 0.10 vs
    p 0.12); value adds paused (mm_risk_reserve_wc 5000) with `room` of worst-case room left in all."""
    kw = {"alloc_enabled": True, "alloc_max_edge_sell": 0.05, "alloc_min_edge_buy": 0.05,
          "alloc_min_improvement": 0.03, "alloc_mm_reserve": 1000.0, "mm_risk_reserve_wc": 5000.0,
          "mm_risk_reserve_corr": 0.0, "value_mode": True, "ref_weight": 1.0, "risk_model": "sum", **cfg}
    a, bb = mk({"21": 1000}, books(**{"21": bk(0.545, 0.57), "11": bk(0.08, 0.10, 20000)}), cash=1000.0, **kw)
    quiet(bb.cycle)
    bb.drain_writes(5)
    quiet(bb.cancel_everything)
    bb.my_orders.clear()
    a.orders.clear()
    a.inv.update({"21": 1000})                    # (the warm-up's own quotes / allocator run may have traded)
    bb.alloc_pairs, bb.alloc_run, bb.alloc_sells_stopped = [], {}, False
    bb.alloc_totals = {k: 0.0 for k in bb.alloc_totals}
    bb.alloc_flows.clear()
    bb.p141_events.clear()
    bb.mmf_sent = {}
    a.wire = []
    a.calls = []
    fresh(bb)
    read(bb, 1000.0)                              # exactly the MM cash reserve: no spare cash to buy with
    bb.last_equity = 100000.0
    bb.alloc_fvs = {e: x.last_fv for e, x in bb.ex.items()}
    worst = bb.total_worst_case(dict(a.inv), bb.alloc_fvs)
    bb.cfg.worst_case_backstop_frac = (worst + room) / bb.last_equity    # the room left now
    bb.mmr_room = bb.alloc_rooms(dict(a.inv))
    bb.mmr_paused = True
    return a, bb


api, b = swap_bot()
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("4ff7d91: value adds paused -> every buy level dropped, no swap planned (blocked_by mm_risk_reserve)",
      not any(p["buy"] for p in pairs) and info["blocked_by"].get("mm_risk_reserve") == 1, (pairs, info))
api, b = swap_bot(alloc_swap_room_netting=True)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
sw = [p for p in pairs if p["buy"] and p["sell"].get("eid")]
check("flag on: the swap is planned under the pause (the sale's freed room credited before the buy's check)",
      sw and sw[0]["sell"]["eid"] == "21" and sw[0]["buy"]["eid"] == "11" and sw[0].get("netting") is True,
      (pairs, info))
check("the pair carries the room floor it was admitted against: min(the room now, the reserve)",
      abs(sw[0]["rfloor"][0] - 5000.0) < 1.0 and sw[0]["rfloor"][1] == 0.0, sw[0].get("rfloor"))
check("alloc_room_floor / alloc_room_ok: a room below the floor is refused, at it is allowed",
      b.alloc_room_floor((6000.0, None)) == (5000.0, 0.0) and b.alloc_room_ok((5000.0, 0.0), (5000.0, 0.0))
      and not b.alloc_room_ok((4999.0, 0.0), (5000.0, 0.0)))
# a buy that would eat into the reserve net of its own sale is refused
api, b = swap_bot(room=5000.0, alloc_swap_room_netting=True)   # at the reserve: any added risk goes below it
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("a swap that would take a room below the reserve net of its own sale is refused (counted mm_risk_reserve)",
      not any(p["buy"] for p in pairs) and info["blocked_by"].get("mm_risk_reserve", 0) >= 1, (pairs, info))
# the legs execute: the sale, then the buy out of its own proceeds although cash < the reserve
api, b = swap_bot(alloc_swap_room_netting=True)
b.alloc_last_run_wall = None
g = grab()
atick(b)
ungrab(g)
check("the sale goes out while paused (an IOC at the touch)", any(x[0] == "21" and x[2] == "sell"
                                                                  for x in sells(api)), sells(api))
pr = next((p for p in b.alloc_pairs if p.get("netting")), None)
read(b, 100.0)                                    # cash far below the 1000 reserve, but the proceeds are its own
api.wire = []
fresh(b)
if pr is not None:
    pr["sold_at"] = time.monotonic() - 1
    b.cg_read_at = time.monotonic()
atick(b)
check("its buy may spend the proceeds of its own sale even with cash below alloc_mm_reserve",
      any(x[0] == "11" and x[2] == "buy" for x in sells(api)), (sells(api), pr and pr["status"]))
usd = sum(x[3] * x[4] for x in sells(api) if x[0] == "11")
check("...and never more than those proceeds (the MM cash before the sale is untouched)",
      usd <= (pr["proceeds"] if pr else 0) + 1e-6, (usd, pr and pr["proceeds"]))
api, b = swap_bot(alloc_swap_room_netting=True)
b.global_reduce = True
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("reduce-only still stops every buy (RT-2), netting or not", not any(p["buy"] for p in pairs), pairs)
api, b = swap_bot(alloc_swap_room_netting=True)
b.mmr_paused = True
b.alloc_last_run_wall = None
atick(b)
check("the refill's own sales are unaffected by the pause (they only reduce)", b.mmr_paused and sells(api),
      sells(api))

# ============================================================================================ edges and the hurdle
print("--- (6) alloc_max_edge_sell and the min-gain hurdle")
api, b = swap_bot(alloc_swap_room_netting=True)
got = {}
for mes in (0.0, 0.02, 0.05):
    b.cfg.alloc_max_edge_sell = mes
    pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
    got[mes] = [p for p in pairs if p["buy"]]
check("alloc_max_edge_sell 0: the 1.9% holding is not a swap candidate; 0.02 and 0.05: it is",
      not got[0.0] and got[0.02] and got[0.05], {k: len(v) for k, v in got.items()})
b.cfg.alloc_max_edge_sell = 0.05
b.cfg.alloc_min_improvement = 0.30                # a min GAIN per $ far above this pair's 8.3%
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("alloc_min_improvement IS the min gain per $ between the buy's and the sale's edge: 0.30 refuses the pair",
      not any(p["buy"] for p in pairs), pairs)
b.cfg.alloc_min_improvement = 0.03
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("...0.03 admits it (so no separate alloc_swap_min_gain setting is needed)", any(p["buy"] for p in pairs))
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("a REFILL sale still keeps the value floor with alloc_refill_max_cost 0 (12 blocked at 2c below p)",
      "12" not in [p["sell"].get("eid") for p in pairs] and info["blocked_by"].get("floor", 0) >= 1, info)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    alloc_refill_max_cost=0.03)
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None, True)
check("alloc_refill_max_cost 0.03, cash below half the target: 12 may be sold 2c below p",
      "12" in [p["sell"].get("eid") for p in pairs], [p["sell"].get("eid") for p in pairs])
read(b, 900.0)                                    # above half the target: the floor is the value floor again
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 900.0, set(), None, True)
check("above half the target the value floor is back (alloc_refill_max_cost does not apply)",
      "12" not in [p["sell"].get("eid") for p in pairs], [p["sell"].get("eid") for p in pairs])
check("mm_floor_ok follows it: a long's bid 2c under p passes only below half the target with the cost set",
      b.mm_floor_ok(True, 0.86, 0.88) is False and (read(b, 0.0) or b.mm_floor_ok(True, 0.86, 0.88)) is True)

# ============================================================================================ the reports
print("--- (7) the reports")
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
atick(b)
f = b.mm_funding_fields()
check("mm_funding: refill_runs, refill_last, refill_sold_usd (24 h), deferred_buybacks, refill_holds, cash_locked",
      f["refill_runs"] == 1 and isinstance(f["refill_last"], str) and f["refill_sold_usd"] > 0
      and f["deferred_buybacks"] == 0 and f["refill_holds"] >= 1 and f["cash_locked"] >= 0,
      {k: f[k] for k in ("refill_runs", "refill_sold_usd", "deferred_buybacks", "refill_holds", "cash_locked")})
check("refill_ev_given_24h = the EV given up by the refill sales (0 at the value floor, > 0 only below p)",
      abs(f["refill_ev_given_24h"] - round(600 * (0.55 - 0.545), 2)) < 0.05, f["refill_ev_given_24h"])
r = b.p141_alloc_report()
check("alloc: swaps_planned / _24h, swaps_done_24h, swaps_usd_24h, ev_gain_est_24h, ev_gain_realised_24h, "
      "refill_blocked_by", set(r) == {"swaps_planned", "swaps_planned_24h", "swaps_usd_planned_24h", "swaps_done_24h",
                                      "swaps_usd_24h", "ev_gain_est_24h", "ev_gain_realised_24h",
                                      "refill_sold_usd_24h", "refill_ev_given_24h", "refill_blocked_by"}, set(r))
check("a refill run is no swap: swaps 0, the refill's blockers reported", r["swaps_planned_24h"] == 0
      and r["swaps_done_24h"] == 0 and r["refill_blocked_by"] == b.mmf_refill["blocked_by"], r)
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries both (alloc gains the swap keys, mm_funding the refill ones)",
      st["alloc"]["swaps_planned_24h"] == 0 and st["mm_funding"]["refill_runs"] == 1
      and st["mm_funding"]["flags"]["alloc_rank_all_markets"] is True, st["mm_funding"]["flags"])
b2 = M.Bot(api, b.cfg)
check("a restart restores the 24-h report log (status.json mm_funding.events)",
      len(b2.p141_events) == len(b.p141_events) and b2.mm_funding_fields()["refill_sold_usd"] > 0,
      (len(b2.p141_events), len(b.p141_events)))
api, b = swap_bot(alloc_swap_room_netting=True)
b.alloc_last_run_wall = None
atick(b)
r = b.p141_alloc_report()
check("a planned swap is counted at once (swaps_planned, swaps_planned_24h, ev_gain_est_24h > 0)",
      r["swaps_planned"] >= 1 and r["swaps_planned_24h"] >= 1 and r["ev_gain_est_24h"] > 0, r)
pr = next((p for p in b.alloc_pairs if p.get("netting")), None)
if pr is not None:
    pr["sold_at"] = time.monotonic() - 1
read(b, 100.0)
fresh(b)
atick(b)
r = b.p141_alloc_report()
check("once both legs traded: swaps_done_24h and the realised EV (the buy's gain less the sale's given-up edge)",
      r["swaps_done_24h"] >= 1 and r["swaps_usd_24h"] > 0 and r["ev_gain_realised_24h"] != 0, r)
line = b.mm_funding_summary()
check("the 2-hourly summary keeps one short piece with the refill and swap figures (also with only 14.1 settings "
      "on)", "MM funding" in line and "refill" in line and "swaps" in line and len(line) < 340, line)
api, b = refill_bot(mm_refill_fast=True)
check("every 14.1 setting off: no swap keys in the alloc status, no refill / swap piece on the summary",
      b.p141_alloc_report() == {} and "swaps" not in b.mm_funding_summary(), b.mm_funding_summary())

# ============================================================================================ no duplicate orders
print("--- no duplicate orders, RT13-3")
api, b = refill_bot(keep_orders=True, mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
atick(b)
api.inv["21"] = 1000                              # the positions read lags
b.mmf_refill_last_m = -1e18
read(b, 0.0)
fresh(b)
api.wire = []
atick(b)
check("RT13-3: a market just sold is not planned or sold again until the read shows it (blocked_by in_flight)",
      all(x[0] != "21" for x in sells(api)) and b.mmf_refill["blocked_by"].get("in_flight", 0) >= 1,
      (sells(api), b.mmf_refill))
quiet(b.cycle)
b.drain_writes(5)
check("...and no quote of ours re-offers those shares in the same cycle as the sale (one side only, the adding one)",
      len([o for o in api.orders.values() if o["exchangeId"] == "21" and o["action"] == "sell"]) == 0
      and len([o for o in api.orders.values() if o["exchangeId"] == "21"]) <= 1, api.ours("21"))
b.mmf_sent["21"] = (time.monotonic() - M.Bot.MM_SENT_LAG - 1, 1000.0, 600.0)
b.alloc_flows.clear()
b.alloc_pairs, b.mmf_refill_last_m = [], -1e18
api.wire = []
read(b, 0.0)
fresh(b)
atick(b)
check(f"after MM_SENT_LAG ({M.Bot.MM_SENT_LAG:.0f} s) it may be planned again", any(x[0] == "21" for x in sells(api)),
      sells(api))
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
atick(b)
n_21 = [x for x in sells(api) if x[0] == "21"]
check("exactly one order per market per refill run (no double sale within the run)", len(n_21) == 1, sells(api))

# ============================================================================================ robustness
print("--- empty books, no reference, unknown markets")
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    mm_recycle_sell_first=True, alloc_swap_room_netting=True, alloc_refill_max_cost=0.02)
for e in list(api.books):
    api.books[e] = {"bids": [], "asks": []}
for x in b.ex.values():
    x.book = {"bids": [], "asks": []}
fresh(b)
atick(b)
check("empty books everywhere: nothing sold, no crash", sells(api) == [])
quiet(b.cycle)
check("...and a full cycle still runs", b.health.get("last_cycle_ok") is not False)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True,
                    alloc_swap_room_netting=True)
b.refs = FakeRefs({})
b.cur_refs, b.cur_liquid = {}, set()
atick(b)
check("no Polymarket reference at all: no refill (no p), no crash", sells(api) == [])
quiet(b.cycle)
check("...a full cycle still runs with no reference", b.health.get("last_cycle_ok") is not False)
api, b = refill_bot(mm_refill_fast=True, alloc_cancel_mm_first=True, alloc_rank_all_markets=True)
b.mm_lots["zz"] = [[100.0, 0.5, time.time() - 9 * H]]
b.mmf_sent["zz"] = (time.monotonic(), 100.0, 10.0)
atick(b)
check("an unknown market in the lots / the in-flight map is ignored", any(x[0] == "21" for x in sells(api)))
api, b = swap_bot(alloc_swap_room_netting=True)
b.last_equity = None
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0, set(), None)
check("no account value: the rooms are unknown -> no netted swap (the pause holds as before)",
      not any(p["buy"] for p in pairs) and b.alloc_rooms(dict(api.inv)) is None)
api, b = swap_bot(alloc_swap_room_netting=True)
b.alloc_fvs = None
check("alloc_rooms before this cycle's fair values: the last fair values are used (no crash)",
      b.alloc_rooms(dict(api.inv)) is not None)

# ============================================================================================ the staged file
print("--- the staged file and the warnings")
DEPLOY = os.path.join(HERE, "..", "deploy", "package14")
P141 = os.path.join(DEPLOY, "settings_override.mm_funding_14_1.json")
P140 = os.path.join(DEPLOY, "settings_override.mm_funding.json")
raw = json.load(open(P141))
RETIRED = ("tilt_exit_priority", "tilt_exit_full_size")   # (retired on simplify: the staged files pin tilt_exit_priority / tilt_exit_full_size at their default False)
good, bad = M.validate_overrides({k: v for k, v in raw.items() if k not in RETIRED}, M.Config())
check("settings_override.mm_funding_14_1.json validates (no refused key)", not bad, bad)
check("it is the 14.0 file plus the owner's live values and the 14.1 keys (nothing dropped)",
      set(json.load(open(P140))) <= set(raw), set(json.load(open(P140))) - set(raw))
check("the owner's live values: alloc_mm_reserve 20000, mm_risk_reserve_wc 20000 / _corr 4000, backstop 1.0, "
      "max_worst_case_frac 0.40, risk_unheld_legs 'ref'",
      [raw.get(k) for k in ("alloc_mm_reserve", "mm_risk_reserve_wc", "mm_risk_reserve_corr",
                            "worst_case_backstop_frac", "max_worst_case_frac", "risk_unheld_legs")]
      == [20000.0, 20000.0, 4000.0, 1.0, 0.4, "ref"], {k: raw.get(k) for k in ("alloc_mm_reserve", "max_worst_case_frac",
                                                                               "risk_unheld_legs")})
check("the three Package 14 flags stay on and the five 14.1 flags are on",
      all(raw.get(k) is True for k in ("mm_recycle_enabled", "mm_refill_fast", "mm_room_guard"))
      and all(raw.get(k) is True for k in M.Bot.P141_FLAGS), raw)
check("the recommended alloc_max_edge_sell 0.05 is in it, alloc_refill_max_cost is NOT (the floor stays)",
      raw.get("alloc_max_edge_sell") == 0.05 and "alloc_refill_max_cost" not in raw, raw.get("alloc_max_edge_sell"))
check("never resets capital_ceiling_adding_size_factor / arb_enabled / ref_tilt_headline (live 0.5 / false / true)",
      raw.get("capital_ceiling_adding_size_factor") == 0.5 and raw.get("arb_enabled") is False
      and raw.get("ref_tilt_headline") is True)
_, bw = mk(alloc_enabled=True, mm_refill_fast=False, alloc_rank_all_markets=True,
           alloc_cancel_mm_first=True, alloc_swap_room_netting=True, mm_risk_reserve_wc=0.0,
           mm_risk_reserve_corr=0.0, alloc_refill_max_cost=0.02)
g = grab()
bw.warn_settings()
ungrab(g)
w = " ".join(g.lines)
check("warnings: the refill flags without mm_refill_fast, the netting with no reserve, and a non-zero "
      "alloc_refill_max_cost are each named once",
      "alloc_cancel_mm_first, alloc_rank_all_markets without mm_refill_fast" in w
      and "alloc_swap_room_netting with mm_risk_reserve_wc and _corr both 0" in w
      and "alloc_refill_max_cost > 0" in w, g.lines)
_, bw2 = mk(alloc_enabled=True, mm_refill_fast=True, **ON)
bw2.cfg.mm_risk_reserve_wc = 20000.0
g = grab()
bw2.warn_settings()
ungrab(g)
check("...and the staged combination (every flag on with the live reserves) warns about none of them",
      not any("MM funding" in x for x in g.lines), g.lines)
check("py_compile on python3.10 (mm_bot.py and this test)",
      subprocess.run([sys.executable, "-m", "py_compile", os.path.join(HERE, "..", "mm_bot.py"), __file__],
                     capture_output=True).returncode == 0)

print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
