"""P12 ops (owner, 4 Oct 16:45 UTC): mm_risk_reserve_wc / mm_risk_reserve_corr - keep RISK room for market making, not
just cash. Twice on 4 Oct value buying filled the worst-case backstop and the bot went reduce-only with the 15k cash
reserve idle. With a reserve set, once room_wc = worst_case_backstop_frac x account - worst case or room_corr =
max_worst_case_frac x account - settlement risk falls below it, value adds pause (takes that grow a position,
allocator buys, tail adding quotes) while middle-band two-way quoting and every reducing side go on;
it lifts at 1.1 x the reserve. Reduce-only itself is never changed.
  settings / ranges; room math on the fake exchange; pause / resume with hysteresis; takes; allocator (buys blocked,
  refills allowed); tail adds dropped, middle two-way kept, reducing sides unchanged; the ladder's caps;
  reduce-only unchanged; status.json / journal / summary; flags off identical to the branch head (git show
  34f5503:mm_bot.py) on a grid (quotes, wire orders, takes, allocator plan, status keys); py_compile on 3.10.
Run:  python tests/test_mm_risk_reserve.py      (exit code 0 = all passed)"""
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
M.alert = lambda msg: None
M.notify = lambda *a, **k: False
BASE_REV = "34f5503"                              # the branch head before this change (Z_board20)


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


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)
REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}


def plain_bot(inv=None, **cfg):
    api, b = make_bot()
    for x in b.ex.values():
        x.close = CLOSE
    b.refs = FakeRefs(dict(REFS))
    b.cfg.selftest_enabled = False
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    api.inv.update(inv or {})
    return api, b


# ============================================================================================ settings
print("--- settings")
c = M.Config()
check("mm_risk_reserve_wc / _corr default 0.0 (off)", c.mm_risk_reserve_wc == 0.0 and c.mm_risk_reserve_corr == 0.0)
check("both OVERRIDABLE, range 0..50000", M.OVERRIDABLE.get("mm_risk_reserve_wc") == (0.0, 50000.0)
      and M.OVERRIDABLE.get("mm_risk_reserve_corr") == (0.0, 50000.0))
good, bad = M.validate_overrides({"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0}, c)
check("5000 / 4000 accepted", not bad and good == {"mm_risk_reserve_wc": 5000.0, "mm_risk_reserve_corr": 4000.0},
      (good, bad))
good, bad = M.validate_overrides({"mm_risk_reserve_wc": 60000.0, "mm_risk_reserve_corr": -1.0}, c)
check("60000 / -1 refused", len(bad) == 2 and not good, (good, bad))
src = open(os.path.join(HERE, "..", "mm_bot.py")).read()
check("Config block '# --- P12 ops: market-making risk reserve ---' after skew_target_inventory",
      src.index("# --- P12 ops: market-making risk reserve ---") > src.index("    skew_target_inventory: bool = False"))
check("hysteresis 1.1", M.MM_RISK_HYST == 1.1)

# ============================================================================================ hysteresis (pure)
print("--- pause / resume with hysteresis")
api, b = plain_bot(mm_risk_reserve_wc=5000.0, worst_case_backstop_frac=0.90, max_worst_case_frac=0.35)
g = Grab()
M.log.addHandler(g)
lvl0 = M.log.level
M.log.setLevel(logging.INFO)
# account 100k: wc limit 90k, corr limit 35k
check("room 10k wc / 15k corr: not paused", b.mm_risk_room_update(80000.0, 20000.0, 100000.0) is False
      and b.mmr_room == (10000.0, 15000.0), b.mmr_room)
check("room_wc 4k < 5k: PAUSED", b.mm_risk_room_update(86000.0, 20000.0, 100000.0) is True and b.mmr_since)
check("journal line when the pause starts", any("VALUE ADDS PAUSED" in x for x in g.lines), g.lines)
check("room_wc 5.2k (>= 5k, < 5.5k): still paused (hysteresis)", b.mm_risk_room_update(84800.0, 20000.0, 100000.0))
check("no account value: state kept, rooms unknown", b.mm_risk_room_update(84800.0, 20000.0, None) is True
      and b.mmr_room == (None, None))
check("room_wc 5.5k (= 1.1 x 5k): resumed", b.mm_risk_room_update(84500.0, 20000.0, 100000.0) is False
      and b.mmr_since is None)
check("journal line when it ends", any("value adds resumed" in x for x in g.lines), g.lines)
b.cfg.mm_risk_reserve_wc, b.cfg.mm_risk_reserve_corr = 0.0, 4000.0
check("corr only: wc room 1k ignored (wc reserve 0)", b.mm_risk_room_update(89000.0, 20000.0, 100000.0) is False)
check("corr room 3.9k < 4k: paused", b.mm_risk_room_update(80000.0, 31100.0, 100000.0) is True)
check("corr room 4.3k: still paused; 4.4k: resumed", b.mm_risk_room_update(80000.0, 30700.0, 100000.0) is True
      and b.mm_risk_room_update(80000.0, 30600.0, 100000.0) is False)
b.cfg.mm_risk_reserve_wc = 5000.0
b.mm_risk_room_update(86000.0, 20000.0, 100000.0)       # paused on wc
check("both set: wc back above 1.1x but corr below 1.1x -> still paused",
      b.mm_risk_room_update(80000.0, 31000.0, 100000.0) is True)      # corr room 4.0k: >= 4k, < 4.4k
check("both above 1.1x: resumed", b.mm_risk_room_update(80000.0, 30000.0, 100000.0) is False)
b.mm_risk_room_update(86000.0, 20000.0, 100000.0)
b.cfg.mm_risk_reserve_wc = b.cfg.mm_risk_reserve_corr = 0.0
check("settings switched off while paused: resumes on the next update", b.mm_risk_room_update(86000.0, 20000.0,
                                                                                               100000.0) is False)
M.log.removeHandler(g)
M.log.setLevel(lvl0)

# ============================================================================================ room math in the cycle
print("--- room math on the fake exchange (full cycle)")
api, b = plain_bot({"11": 3000, "21": 4000, "22": -2000}, mm_risk_reserve_wc=5000.0, mm_risk_reserve_corr=4000.0)
quiet(b.cycle)
inv = {e: float(q) for e, q in api.inv.items()}
fvs = {e: x.last_fv for e, x in b.ex.items()}
eq = b.health["account_value"]
worst = b.health["worst_case_loss"]
risk = b.health["settlement_risk"]
rw, rc = b.mmr_room
check("room_wc = backstop_frac x account - total_worst_case", abs(rw - (b.cfg.worst_case_backstop_frac * eq - worst))
      < 0.01, (rw, eq, worst))
check("room_corr = max_worst_case_frac x account - the risk the cap compares (min(worst, settlement_risk))",
      abs(rc - (b.cfg.max_worst_case_frac * eq - risk)) < 0.01 and risk <= worst + 1e-9, (rc, risk))
check("not paused with plenty of room", not b.mmr_paused and not b.health["reduce_only"])
mr = b.health.get("mm_risk_room")
check("status mm_risk_room has every field", mr is not None and set(mr) == {
    "room_wc", "room_corr", "paused", "since", "reserve_wc", "reserve_corr", "blocked"}
    and set(mr["blocked"]) == {"takes", "alloc", "basket", "tail_quotes"}, mr)
b.cfg.mm_risk_reserve_wc = b.cfg.worst_case_backstop_frac * eq - worst + 1000.0   # 1k more than the room
quiet(b.cycle)
check("reserve 1k above the room: paused, and NOT reduce-only (the reduce-only decision is unchanged)",
      b.mmr_paused and not b.health["reduce_only"] and not b.global_reduce)

# ============================================================================================ reduce-only unchanged
print("--- reduce-only itself never changes")
same = True
for pos in ({}, {"11": 3000}, {"21": 9000, "22": -9000}, {"12": 20000, "21": 20000}):
    for backstop, cap in ((0.69, 0.30), (0.10, 0.05), (0.90, 0.35)):
        out = []
        for res in (0.0, 50000.0):
            _, bx = plain_bot(pos, worst_case_backstop_frac=backstop, max_worst_case_frac=cap,
                              mm_risk_reserve_wc=res, mm_risk_reserve_corr=res)
            quiet(bx.cycle, )
            quiet(bx.cycle)
            out.append((bx.global_reduce, bx.health["reduce_only"], bx.health["worst_case_loss"]))
        same = same and out[0] == out[1]
check("global_reduce identical with reserves 0 vs 50k (4 books x 3 cap pairs, 2 cycles each)", same)

# ============================================================================================ quotes
print("--- tail adds dropped, middle two-way kept, reducing sides unchanged")


def decide(bx, eid, pos, paused):
    bx.mmr_paused = paused
    ex = bx.ex[eid]
    inv = {e: 0.0 for e in bx.ex}
    inv[eid] = float(pos)
    return quiet(bx.decide, ex, ex.last_fv, inv, inv, False, 0.0, time.monotonic(), bx.cur_refs.get(eid),
                 ex.last_fv, True)


api, b = plain_bot(value_mode=True, value_quote_hurdle=0.08, mm_risk_reserve_wc=5000.0)
quiet(b.cycle)
for e in b.ex.values():
    e.cooldown_until = 0.0
q0 = decide(b, "11", 0, False)                    # Ohio Rep, p 0.12: a tail
q1 = decide(b, "11", 0, True)
check("tail, flat, not paused: an adding side rests (the hurdle lets it through)",
      (q0.bid is not None and q0.bid_size >= 1) or (q0.ask is not None and q0.ask_size >= 1), q0)
check("tail, flat, paused: no adding side at all (both sides would add)",
      (q1.bid is None or q1.bid_size < 1) and (q1.ask is None or q1.ask_size < 1)
      , q1)
check("...a resting tail add is dropped by the next re-quote (max sizes 0: nothing kept)",
      (q1.bid_max or 0) < 1 and (q1.ask_max or 0) < 1, q1)
q0 = decide(b, "11", 300, False)
q1 = decide(b, "11", 300, True)
check("tail, long 300, paused: the reducing ask unchanged (price and size)", (q1.ask, q1.ask_size) == (q0.ask, q0.ask_size)
      and q1.ask_size >= 1, (q0, q1))
check("tail, long 300, paused: the adding bid gone", q1.bid is None or q1.bid_size < 1, q1)
q0 = decide(b, "12", -300, False)                 # Ohio Dem, p 0.88: a tail, short
q1 = decide(b, "12", -300, True)
check("tail, short 300, paused: the reducing bid unchanged, at most the position",
      (q1.bid, q1.bid_size) == (q0.bid, q0.bid_size) and q1.bid_size <= 300, (q0, q1))
check("tail, short 300, paused: the adding ask gone", q1.ask is None or q1.ask_size < 1, q1)
same_mid = True
for pos in (0, 150, -150):
    for eid in ("21", "22"):                      # Utah, p 0.55 / 0.45: the middle band
        same_mid = same_mid and astuple(decide(b, eid, pos, False)) == astuple(decide(b, eid, pos, True))
check("middle band (p 0.45 / 0.55): two-way quotes identical paused or not (flat, long, short)", same_mid)
qm = decide(b, "21", 0, True)
check("middle band paused, flat: both sides rest (adds inside the room)", qm.bid_size >= 1 and qm.ask_size >= 1, qm)
b.mmr_paused = True
b.mmr_tail_now = 0
decide(b, "11", 0, True)
check("tail_quotes counts the adding sides held back this cycle", b.mmr_tail_now == 2, b.mmr_tail_now)
# no liquid p: the fair value classifies
api, b2 = plain_bot(mm_risk_reserve_wc=5000.0)
quiet(b2.cycle)
for e in b2.ex.values():
    e.cooldown_until = 0.0
b2.mmr_paused = True
check("no value p (illiquid): the fair value decides the tail (Ohio Rep fv ~0.14)",
      b2.mm_tail_adds_off(b2.ex["11"], 0.14, None, False) and not b2.mm_tail_adds_off(b2.ex["21"], 0.52, None, False))
b2.mmr_paused = False
check("not paused: nothing is a paused tail", not b2.mm_tail_adds_off(b2.ex["11"], 0.14, None, False))
b.mmr_paused = True
b.ex["11"].mmr_tail, b.ex["11"].inv, b.ex["11"].eff = True, 0.0, 0.0
b.lad_liquid, b.lad_party_delta = set(), 0.0
caps = b.ladder_caps(b.ex["11"], 0.14, 100)
check("the R3 ladder's caps: a paused tail adds nothing on either side", caps[True](0.10) <= 0 and caps[False](0.20) <= 0,
      (caps[True](0.10), caps[False](0.20)))
b.mmr_paused = False

# ============================================================================================ takes
print("--- stale-quote takes")


def take_bot(pos, paused):
    api_, bx = make_bot(live=True)
    bx.cfg.selftest_enabled = False
    api_.inv.update({"11": pos})
    bx.ex["11"].inv = float(pos)
    bx.mmr_paused = paused
    ex = bx.ex["11"]
    ex.book = api_.full_book("11")
    return api_, bx, ex


for paused in (False, True):
    api, b, ex = take_bot(0, paused)
    ex.take_dir = 1
    n0 = len(api.sent("cancel_all"))
    did = quiet(b.execute_take, ex, 0.40, 0.0, 0.14, time.monotonic())
    pulled = len(api.sent("cancel_all")) > n0
    if paused:
        check("paused: a take that adds is skipped before any cancel, counted",
              did is False and not pulled and b.mmr_blocked["takes"] == 1 and b.takes_total == 0, (did, pulled))
    else:
        check("not paused: the same take goes ahead", did and pulled and b.takes_total == 1, (did, pulled))
api, b, ex = take_bot(500, True)
ex.take_dir = -1                                  # sell to a rich bid while long 500: shrinks the position
n0 = len(api.wire)
did = quiet(b.execute_take, ex, 0.02, 500.0, 0.14, time.monotonic())
sent = api.wire[n0:]
check("paused: a take that shrinks a long goes ahead, capped at the position",
      did and sent and sent[-1]["quantity"] <= 500 and b.mmr_blocked["takes"] == 0, sent)
api, b, ex = take_bot(-200, True)
ex.take_dir = 1                                   # buy back a short: shrinks it
n0 = len(api.wire)
did = quiet(b.execute_take, ex, 0.40, -200.0, 0.14, time.monotonic())
sent = api.wire[n0:]
check("paused: buying back a short of 200 goes ahead with at most 200",
      did and sent and sent[-1]["quantity"] <= 200, sent)
# ============================================================================================ allocator
print("--- allocator: buys blocked, sales / refills allowed")
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate"}


def bk(bid, ask, q=2000):
    return {"bids": [lvl(bid, q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


def alloc_bot(inv, cash):
    base = {"11": bk(0.10, 0.18, 1000), "12": bk(0.82, 0.90, 1000), "21": bk(0.48, 0.56, 1000),
            "22": bk(0.44, 0.52, 1000), "A1": bk(0.60, 0.62), "A2": bk(0.37, 0.40), "B1": bk(0.55, 0.60),
            "B2": bk(0.38, 0.42), "G1": bk(0.62, 0.66), "G2": bk(0.26, 0.31)}
    mk = []
    for k, race in RACES.items():
        mk += [market(f"m{k}1", f"{k}1", "Republican", race), market(f"m{k}2", f"{k}2", "Democratic", race)]
    api_, bx = make_bot(live=True, books=base, extra_markets=tuple(mk))
    bx.t["endDate"] = M.iso(CLOSE)
    for x in bx.ex.values():
        x.close = CLOSE
    r = dict(REFS)
    r.update({"Alpha Senate|Republican": 0.60, "Alpha Senate|Democratic": 0.40, "Beta Senate|Republican": 0.70,
              "Beta Senate|Democratic": 0.30, "Gamma Senate|Republican": 0.70, "Gamma Senate|Democratic": 0.30})
    bx.refs = FakeRefs(r)
    cf = bx.cfg
    cf.selftest_enabled, cf.reduce_no_as_sell, cf.arb_enabled, cf.cash_gate_enabled = False, True, False, True
    cf.cash_gate_reserve, cf.reserved_cash_mode, cf.alloc_enabled = 0.0, "ignore", False
    cf.alloc_mm_reserve, cf.alloc_max_contract_usd, cf.max_position_frac = 1000.0, 100000.0, 1.0
    api_.inv.update(inv)
    api_.cash = cash
    api_.pnl = lambda: (api_.log("pnl"), {"totalAccountValue": api_.equity, "cashBalance": api_.cash})[1]
    quiet(bx.cycle)
    bx.drain_writes(5)
    quiet(bx.cancel_everything)
    bx.my_orders.clear()
    api_.orders.clear()
    bx.cg_cash, bx.cg_reserved, bx.cg_spent, bx.cg_read_at = api_.cash, 0.0, 0.0, time.monotonic()
    cf.alloc_enabled = True
    return api_, bx


def plan(bx, cash):
    return quiet(bx.alloc_plan, dict(bx.api.inv), time.monotonic(), cash, set(), None)


api, b = alloc_bot({"A1": 1000}, 1000.0)
p0, i0 = plan(b, 1000.0)
check("not paused: a pair with a buy is planned", any(x["buy"] for x in p0), p0)
b.mmr_paused = True
p1, i1 = plan(b, 1000.0)
check("paused: no buy planned, blocked_by mm_risk_reserve", not any(x["buy"] for x in p1)
      and i1["blocked_by"].get("mm_risk_reserve") == 1, (p1, i1))
p2, i2 = plan(b, 0.0)
check("paused, cash below the reserve: the refill sale is still planned (no buy)",
      p2 and all(x["buy"] is None for x in p2) and p2[0]["sell"]["eid"] == "A1", p2)
b.cfg.alloc_mm_reserve = 0.0
pr = {"sell": {"kind": "cash", "label": "spare cash", "usd": 600.0, "edge": 0.0, "qty": 0, "px": 1.0},
      "buy": {"eid": "B1", "label": "B1", "short": False, "px": 0.60, "qty": 1000, "edge": 0.16, "usd": 600.0},
      "usd": 600.0, "status": "sold", "proceeds": 600.0, "sold_at": -1e18}
n0 = len(api.wire)
b.alloc_run = {}
check("paused: alloc_buy refuses (counted), nothing sent",
      quiet(b.alloc_buy, pr, dict(api.inv), {}, time.monotonic(), time.time(), set()) is False
      and len(api.wire) == n0 and b.alloc_run["blocked_by"].get("mm_risk_reserve") == 1, b.alloc_run)
pr2 = {"sell": {"kind": "long", "eid": "A1", "label": "A1", "qty": 500, "px": 0.60, "edge": 0.0, "usd": 300.0},
       "buy": pr["buy"], "usd": 300.0, "status": "pending", "proceeds": 0.0, "sold_at": None}
check("paused: a paired sale (its buy could not follow) is not sent",
      quiet(b.alloc_sell, pr2, dict(api.inv), {}, time.monotonic(), time.time(), set()) is False
      and len(api.wire) == n0)
pr3 = dict(pr2, buy=None)
check("paused: a refill sale (no buy) is sent",
      quiet(b.alloc_sell, pr3, dict(api.inv), {}, time.monotonic(), time.time(), set()) is True and len(api.wire) > n0)
check("allocator blocks counted in mm_risk_room", b.mmr_blocked["alloc"] >= 2, b.mmr_blocked)

# ============================================================================================ status / summary
print("--- status.json, summary")
api, b = plain_bot({"11": 3000}, mm_risk_reserve_wc=50000.0, mm_risk_reserve_corr=4000.0)
quiet(b.cycle)
line = b.summary_ops_line(100000.0) or ""
check("summary while on and not paused: ' | risk room wc Xk corr Yk' without (paused)",
      " | risk room wc " in line and "(paused)" not in line, line)
b.cfg.worst_case_backstop_frac = 0.30            # room_wc ~ 30k - worst < 50k
quiet(b.cycle)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
mr = st.get("mm_risk_room") or {}
check("status.json mm_risk_room: paused (wc room < 50k), since set, reserves echoed",
      mr.get("paused") is True and mr.get("since") and mr.get("reserve_wc") == 50000.0
      and mr.get("reserve_corr") == 4000.0 and isinstance(mr.get("room_wc"), float), mr)
line = b.summary_ops_line(100000.0) or ""
rw, rc = b.mmr_room
check("summary ' | risk room wc Xk corr Yk (paused)'",
      f" | risk room wc {rw / 1000:.1f}k corr {rc / 1000:.1f}k (paused)" in line, line)
b.cfg.mm_risk_reserve_wc = b.cfg.mm_risk_reserve_corr = 0.0
quiet(b.cycle)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("settings back to 0: resumed, the status key and summary part gone",
      not b.mmr_paused and "mm_risk_room" not in st and "risk room" not in (b.summary_ops_line(100000.0) or ""))
api, b = plain_bot()
quiet(b.cycle)
check("off: no mm_risk_room in health, empty summary part", "mm_risk_room" not in b.health and b.mm_risk_summary() == "")

# ============================================================================================ flags off = base
print(f"--- flags off identical to the branch head ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_mmrbase.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_mmrbase", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = M.alert, (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

if base is not None:
    def twin(inv, overrides):
        res = []
        for mod in (M, base):
            api_x, bn = make_bot()
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
            bn.refs = FakeRefs(dict(REFS))
            bn.cfg.selftest_enabled = False
            for k, v in overrides.items():
                setattr(bn.cfg, k, v)
            quiet(bn.cycle)
            quiet(bn.cycle)
            res.append((api_x, bn))
        return res

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = True
    diffs = []
    grids = ({}, {"value_mode": True, "value_quote_hurdle": 0.08}, {"max_worst_case_frac": 0.001,
                                                                   "worst_case_backstop_frac": 0.002})
    for inv in ({}, {"11": 300, "21": 3000, "22": -3000}, {"12": 2500}, {"21": -800, "22": -900}):
        for ov in grids:
            (an, bn), (ab, bb_) = twin(inv, ov)
            if strip(an.wire) != strip(ab.wire):
                ok_w = False
                diffs.append(("wire", inv, ov))
            if {e: astuple(x.quote) for e, x in bn.ex.items()} != {e: astuple(x.quote) for e, x in bb_.ex.items()}:
                ok_q = False
                diffs.append(("quote", inv, ov))
            bn.write_status(True)
            bb_.write_status(True)
            with open(bn.cfg.status_file) as f:
                kn = set(json.load(f)) - set(getattr(M.Bot, "MM_FUNDING_KEYS", ()))   # (less the later P14 report key)
            with open(bb_.cfg.status_file) as f:
                kb = set(json.load(f))
            if kn != kb or set(bn.health) != set(bb_.health) or bn.status_report() != bb_.status_report():
                ok_s = False
                diffs.append(("status", inv, ov, kn ^ kb))
    check("two full cycles send the same orders (4 books x plain / value+hurdle / reduce-only / ladder)", ok_w, diffs[:1])
    check("every market's quote (and keep limits) identical", ok_q, diffs[:1])
    check("status.json / health keys and the status line identical", ok_s, diffs[:1])

    ok_t = True
    for pos, d, p in ((0, 1, 0.40), (500, -1, 0.02), (-200, 1, 0.40), (0, -1, 0.02)):
        res = []
        for mod in (M, base):
            api_x = FakeApi(True)
            api_x.markets_list = [market("1", "11", "Republican", "Ohio Senate"),
                                  market("2", "12", "Democratic", "Ohio Senate")]
            api_x.books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
                           "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]}}
            cfg = mod.Config()
            d_ = tempfile.mkdtemp()
            for k in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file",
                      "overrides_file", "market_edge_file", "handover_file"):
                setattr(cfg, k, os.path.join(d_, os.path.basename(getattr(cfg, k))))
            cfg.record_file, cfg.ref_map_file, cfg.summary_every_hours = "", "", 0
            cfg.handover_exit_max_seconds, cfg.realtime_enabled, cfg.selftest_enabled = 0, False, False
            bx = mod.Bot(api_x, cfg)
            api_x.inv["11"] = pos
            ex = bx.ex["11"]
            ex.inv, ex.book, ex.take_dir = float(pos), api_x.full_book("11"), d
            did = quiet(bx.execute_take, ex, p, float(pos), 0.14, time.monotonic())
            res.append((did, strip(api_x.wire), bx.takes_total))
        ok_t = ok_t and res[0] == res[1]
    check("execute_take identical (buy / sell, flat / long / short)", ok_t)

    ok_a = True
    for inv, cash in (({"A1": 1000}, 1000.0), ({"A1": 1000}, 0.0), ({"A1": 1000, "G2": -500}, 3000.0)):
        api_n, bn = alloc_bot(inv, cash)
        pn = plan(bn, cash)
        cfg = base.Config()
        for k in base.Config.__dataclass_fields__:
            setattr(cfg, k, getattr(bn.cfg, k, getattr(cfg, k)))
        bb_ = base.Bot.__new__(base.Bot)
        bb_.__dict__.update({k: v for k, v in bn.__dict__.items() if not k.startswith("mmr_")})
        bb_.basket_legs = {}                # (the base revision's retired long-tilt basket: never any legs)
        bb_.cfg = cfg
        pb = quiet(base.Bot.alloc_plan, bb_, dict(api_n.inv), time.monotonic(), cash, set(), None)
        ok_a = ok_a and json.dumps(pn, sort_keys=True, default=str) == json.dumps(pb, sort_keys=True, default=str)
    check("allocator plan identical (pairs, refills, blocked_by)", ok_a)

# ============================================================================================ deploy file, py_compile
print("--- deploy file, py_compile 3.10")
dep = os.path.join(HERE, "..", "deploy", "package12", "settings_override.stage1b_risk_reserve.json")
try:
    with open(dep) as f:
        ov = json.load(f)
except (OSError, ValueError) as e:
    ov = {"_error": str(e)}
RETIRED = ("tilt_exit_priority", "tilt_exit_full_size")   # (retired on simplify: the staged files pin tilt_exit_priority / tilt_exit_full_size at their default False)
good, bad = M.validate_overrides({k: v for k, v in ov.items() if not k.startswith("_") and k not in RETIRED},
                                 M.Config())
check("stage1b file: mm_risk_reserve_wc 5000 / _corr 4000, every key valid",
      ov.get("mm_risk_reserve_wc") == 5000.0 and ov.get("mm_risk_reserve_corr") == 4000.0 and not bad, bad)
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
