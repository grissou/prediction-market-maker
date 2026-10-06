"""
Package 16 (owner, 6 Oct 11:00 UTC): the ARMED MOMENTUM SLEEVE of Package 13 (A's sleeve + C's automation, 1fdd467)
ported onto Package 15 (ed65638) -
  settings and ranges; flags off identical to ed65638 on a grid with fills (orders, quotes, notes, status, health,
  summary, alloc_plan), the Package 15 ladder and state cap on in part of the grid;
  the classification (momentum longs); the sleeve's buys (forced: sizes, legs at cost, the market cap, the state cap,
  the funding (a), the opposite side, max markets, headline, the target momentum_max_usd, reduce-only, dry run);
  TiltSlope: bins, the winsorised slope estimator, non-headline / liquid / spread / fresh-book / other-traders filter,
  the regression (slope_24h) and the 6-h change, seeding from a recorder-like sqlite (and the snap04 reading), restart;
  the trigger (slope_24h >= momentum_on_slope AND slope_6h > 0 held momentum_confirm_h; one burst never triggers;
  force on / auto off / momentum_enabled alone); the ramp (start 10k, +10k per 6 h of rising tilt, stalls, the cap);
  the funding order ((a) free cash above the MM's effective reserve + the ladder's carve-out + the holdback hook, (b) the
  MM's effective reserve while mm_carry_24h.per_day < mm_carry_min, (c) VALUE sales lowest edge first through the
  allocator's refill sale path, never below the value floor; funded_from / ev_given_up; the allocator's spare-cash
  pause, the ladder's no-new-levels and yield, the quoter's budget);
  the flip (slope_24h <= 0, the profit target, mom_exit_utc, momentum_exit, the kill; the ladder takes over), the
  exit's pacing and floor exemption, the kill latch and the re-arm; rule 5 (no fake buying); no duplicate orders
  across cycles and restarts (RT13-3's 120-s grace); nothing crashes with no history / no DB / empty books.
Run:  python tests/test_p16.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import astuple
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "ed65638"                              # Package 15 READY (claude/mm-funding): the head P16 is built on
STAGED = os.path.join(ROOT, "deploy", "package16", "settings_override.momentum_armed.json")
P15_STAGED = os.path.join(ROOT, "deploy", "package15", "settings_override.harvest.json")
SNAP_DB = "/home/claude/snap04/md.sqlite"


class Cap(logging.Handler):
    """Every log record's message (the journal and crash checks read it)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, r):
        try:
            self.lines.append(r.getMessage())
        except Exception:
            pass


CAP = Cap()
M.log.addHandler(CAP)
M.log.setLevel(logging.INFO)                      # (the journal checks read it; nothing reaches the console)
M.log.propagate = False


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(extra)[:700]}]" if not cond else ""))
    RESULTS.append(bool(cond))


NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)
H = 3600.0
BIN = 4 * H


def bk(bid, ask, q=2000, bq=None):
    return {"bids": [lvl(bid, bq or q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


# the test_p13 world: A Alpha (A2 p 0.02 VALUE short), B Beta (B2 p 0.12 ask 0.15: MOMENTUM YES), G Gamma (VALUE),
# D Delta (D2 p 0.10 ask 0.13 -> D1's NO at 1 - 0.89 = 0.11); Ohio Rep "11" p 0.12 ask 0.18: MOMENTUM YES;
# R Rhode Island Senate (a state: priced only where a test gives it a Polymarket price)
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate",
         "R": "Rhode Island Senate"}


def books_default():
    return {"A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08),
            "B1": bk(0.85, 0.87), "B2": bk(0.13, 0.15),
            "G1": bk(0.66, 0.70), "G2": bk(0.30, 0.32),
            "D1": bk(0.89, 0.91), "D2": bk(0.11, 0.13),
            "R1": bk(0.11, 0.12), "R2": bk(0.86, 0.88)}


def refs_default():
    return {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02,
            "Beta Senate|Republican": 0.88, "Beta Senate|Democratic": 0.12,
            "Gamma Senate|Republican": 0.80, "Gamma Senate|Democratic": 0.20,
            "Delta Senate|Republican": 0.90, "Delta Senate|Democratic": 0.10}


RHODE = {"Rhode Island Senate|Republican": 0.10, "Rhode Island Senate|Democratic": 0.90}


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
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update(inv or {})
    api.cash, api.equity = cash, equity
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def quiet(fn, *a, **k):
    return fn(*a, **k)                            # (the bot's log goes to CAP only)


def read(b, cash=None):
    b.cg_cash = b.api.cash if cash is None else cash
    b.cg_reserved, b.cg_spent = 0.0, 0.0
    b.cg_read_at = time.monotonic()


FLAGS = ("momentum_enabled", "momentum_auto", "momentum_force", "tilt_harvest_ladder", "alloc_enabled")


def warm(b):
    """One quiet cycle with every P16 / P15 flag and the allocator off (fair values, Polymarket, cash read), then a
    clean slate."""
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
    b.api.wire = []                               # (the checks read the orders sent after the warm-up)
    b.alloc_last_run_wall = time.time()           # (no hourly allocator run in the unit ticks)


def fresh(b):
    now_m = time.monotonic()
    for x in b.ex.values():
        x.book = M.strip_own(b.api.full_book(x.eid), []) if x.eid in b.api.books else x.book
        x.verified = now_m


def tick(b, at=None, skip=(), cash=True):
    time.sleep(0.002)
    fresh(b)
    if cash:
        read(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.mom_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


def atick(b, at=None):
    """One allocator tick (the funding sales are sold there)."""
    time.sleep(0.002)
    fresh(b)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.alloc_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set())


def wire(api, eid=None):
    return [(o["exchangeId"], o["side"], o["action"], o["price"], o["quantity"]) for o in getattr(api, "wire", [])
            if eid is None or o["exchangeId"] == eid]


def stub_series(b):
    """The trigger tests drive the slope series directly: no live sampling, no seeding."""
    b.ts_feed = lambda *a, **k: None
    b.ma_seed = {"done": True, "merged": True, "note": "test"}


def set_series(b, at, vals, bin_h=4.0):
    """Bins (points) oldest first, the last one the bin `at` falls in (covered: counts at once)."""
    bs = bin_h * H
    now_w = at.timestamp()
    cur = math.floor(now_w / bs) * bs
    ts = M.TiltSlope(bs)
    for i, v in enumerate(vals):
        k = cur - (len(vals) - 1 - i) * bs
        ts.bins[k] = [v / 100.0, 1.0, 100, k, k + 0.5 * bs]
    ts.source = "live"
    b.tslope = ts


RISING = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5]       # slope_24h +3.0, slope_6h +3.0 points a day
FLAT_UP = [10.0, 10.0, 10.0, 10.0, 10.0, 10.05]      # slope_24h ~+0.09 (< 0.5)
FALLING = [12.5, 12.0, 11.5, 11.0, 10.5, 10.0]       # slope_24h -3.0
TURNING = [10.0, 10.5, 11.0, 11.5, 12.0, 11.9]       # slope_24h > 0.5 but slope_6h < 0 (the move is not current)


def auto_bot(**kw):
    args = dict(momentum_enabled=True, momentum_auto=True)
    args.update(kw)
    api, b = mk_bot(**args)
    warm(b)
    stub_series(b)
    return api, b


def forced_bot(**kw):
    args = dict(momentum_enabled=True, momentum_force=True)
    args.update(kw)
    api, b = mk_bot(**args)
    warm(b)
    stub_series(b)
    return api, b


def at_h(h):
    return NOW + timedelta(hours=h)


def cost(b):
    return sum(v["cost"] for v in b.mom_legs.values())


# ============================================================================================ settings
print("--- settings")
D = M.Config()
SPEC = {"momentum_enabled": (False, (False, True)), "momentum_auto": (False, (False, True)),
        "momentum_force": (False, (False, True)), "momentum_exit": (False, (False, True)),
        "momentum_max_usd": (40000.0, (0.0, 200000.0)), "mom_exit_hours": (6.0, (0.25, 72.0)),
        "mom_exit_utc": ("", ("2026-10-01T00:00:00Z", "2026-11-07T00:00:00Z")), "mom_kill_frac": (0.75, (0.3, 0.99)),
        "mom_min_depth": (200, (0, 100000)), "mom_headline": (False, (False, True)),
        "mom_max_markets": (40, (1, 300)), "momentum_writes_frac": (0.6, (0.0, 1.0)),
        "mom_max_p": (0.25, (0.01, 0.5)), "value_extreme_p": (0.04, (0.0, 0.25)),
        "value_min_edge": (0.12, (0.0, 2.0)), "value_min_edge_fav": (0.08, (0.0, 2.0)),
        "momentum_slope_hours": (24.0, (6.0, 72.0)), "momentum_slope_bin_h": (4.0, (1.0, 12.0)),
        "momentum_slope_max_spread": (0.06, (0.01, 0.2)), "momentum_on_slope": (0.5, (0.0, 10.0)),
        "momentum_confirm_h": (4.0, (0.0, 48.0)), "momentum_start_usd": (10000.0, (0.0, 100000.0)),
        "momentum_step_usd": (10000.0, (0.0, 100000.0)), "momentum_step_h": (6.0, (1.0, 48.0)),
        "mm_carry_min": (300.0, (0.0, 10000.0)), "momentum_profit_target": (0.25, (0.0, 5.0)),
        "momentum_fund_value": (True, (False, True)), "momentum_rearm_h": (24.0, (0.0, 168.0)),
        "election_holdback_usd": (0.0, (0.0, 100000.0)),
        "election_holdback_from_utc": ("2026-11-03T12:00:00Z", ("2026-10-01T00:00:00Z", "2026-11-07T00:00:00Z"))}
for k, (dflt, rng) in SPEC.items():
    ok_d = getattr(D, k) == dflt and type(getattr(D, k)) is type(dflt)
    ok_r = M.OVERRIDABLE.get(k) == rng
    if isinstance(dflt, bool):
        g1, b1 = M.validate_overrides({k: not dflt}, D)
        g2, b2 = M.validate_overrides({k: 1}, D)
        ok_v = g1 == {k: not dflt} and not b1 and not g2 and b2
    elif isinstance(dflt, str):
        g1, b1 = M.validate_overrides({k: "2026-11-01T00:00:00Z"}, D)
        g2, b2 = M.validate_overrides({k: "2027-01-01T00:00:00Z"}, D)
        g3, b3 = M.validate_overrides({k: ""}, D)
        g4, b4 = M.validate_overrides({k: "2026-11-01 00:00"}, D)
        ok_v = g1 == {k: "2026-11-01T00:00:00Z"} and not g2 and b2 and g3 == {k: ""} and not g4 and b4
    else:
        lo, hi = rng
        step = 1 if isinstance(dflt, int) else 1e-6
        g1, b1 = M.validate_overrides({k: dflt}, D)
        g2, b2 = M.validate_overrides({k: lo - step}, D)
        g3, b3 = M.validate_overrides({k: hi + step}, D)
        g4, b4 = M.validate_overrides({k: hi}, D)
        ok_v = g1 == {k: dflt} and not g2 and b2 and not g3 and b3 and g4 == {k: hi}
    check(f"{k}: default {dflt!r}, range {rng}, validates in range / refuses outside", ok_d and ok_r and ok_v,
          (getattr(D, k), M.OVERRIDABLE.get(k)))
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("one contiguous block right after state_max_usd (the end of Config and of OVERRIDABLE)",
      _f[_f.index("state_max_usd") + 1:] == list(SPEC) and _ov[_ov.index("state_max_usd") + 1:] == list(SPEC),
      _f[_f.index("state_max_usd") + 1:][:4])
src = open(os.path.join(ROOT, "mm_bot.py")).read()
check("Config comment block '# --- P16: the ARMED momentum sleeve'", "# --- P16: the ARMED momentum sleeve" in src)
check("not ported: buckets_enabled, aggressive_value, election_night, momentum_fund_floor (the floor is always kept)",
      not any(k in _f for k in ("buckets_enabled", "aggressive_value", "election_night", "momentum_fund_floor",
                                "bucket_mom_frac")))
check("Bot.MOMENTUM_KEYS names the status key P16 adds (momentum)", tuple(M.Bot.MOMENTUM_KEYS) == ("momentum",))
check("wire_order strips the sleeve's notes _mom / _mom_exit (and keeps the covered NO sale)",
      M.wire_order({"exchangeId": "x", "side": "yes", "action": "buy", "price": 0.5, "quantity": 1, "_mom": True})
      == {"exchangeId": "x", "side": "yes", "action": "buy", "price": 0.5, "quantity": 1}
      and M.wire_order({"exchangeId": "x", "side": "yes", "action": "buy", "price": 0.4, "quantity": 1,
                        "_mom_exit": True, "_no_sell": True})
      == {"exchangeId": "x", "side": "no", "action": "sell", "price": 0.6, "quantity": 1})
_, b_ = mk_bot()
check("off by default: mom_on False, no momentum status, may not buy", not b_.mom_on() and not b_.mom_persist_needed()
      and not b_.mom_may_buy() and b_.ma["state"] == "off")
if os.path.exists(STAGED):
    raw = json.load(open(STAGED))
    good, bad = M.validate_overrides(raw, M.Config())
    p15 = json.load(open(P15_STAGED))
    p16k = set(raw) - set(p15)
    check("staged file validates whole (no problem)", not bad and len(good) == len(raw), bad)
    check("staged file = the Package 15 file + the P16 keys only (nothing of Package 15 changed)",
          {k: v for k, v in raw.items() if k in p15} == p15 and p16k <= set(SPEC), sorted(p16k - set(SPEC)))
    check("staged: momentum_enabled true, momentum_auto true, momentum_force false, momentum_exit not set",
          raw.get("momentum_enabled") is True and raw.get("momentum_auto") is True
          and raw.get("momentum_force") is False and "momentum_exit" not in raw)
    EXPL = {"momentum_slope_hours": 24, "momentum_on_slope": 0.5, "momentum_confirm_h": 4,
            "momentum_start_usd": 10000, "momentum_step_usd": 10000, "momentum_step_h": 6,
            "momentum_max_usd": 40000, "mm_carry_min": 300, "momentum_profit_target": 0.25, "mom_kill_frac": 0.75,
            "mom_exit_hours": 6, "momentum_fund_value": True, "momentum_rearm_h": 24}
    check("staged: the Package 13 C defaults written out (slope 24 h / 0.5 / confirm 4 h / 10k + 10k per 6 h / 40k "
          "max / carry 300 / profit 0.25 / kill 0.75 / exit 6 h / fund value / re-arm 24 h)",
          all(raw.get(k) == v for k, v in EXPL.items()), {k: raw.get(k) for k in EXPL})
    check("staged: capital_ceiling_adding_size_factor 0.5, arb_enabled false, ref_tilt_headline true kept; the "
          "harvest ladder and the 15k state cap on", raw.get("capital_ceiling_adding_size_factor") == 0.5
          and raw.get("arb_enabled") is False and raw.get("ref_tilt_headline") is True
          and raw.get("tilt_harvest_ladder") is True and raw.get("state_max_usd") == 15000)
else:
    check("staged file present", False, STAGED)

# ============================================================================================ flags off = ed65638
print(f"--- flags off identical to Package 15 ({BASE_REV}) on a grid")
base = None
try:
    out = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p16base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p16base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = (lambda msg: None), (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase"}
STATE_REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
              "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45, **refs_default(), **RHODE}

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
                    for e, side in (("21", True), ("22", False), ("B2", False), ("A2", True), ("R1", True)):
                        api_x.fill(e, side, 40)
                quiet(bn.cycle)
                bn.drain_writes(5)
            res.append((api_x, bn))
        return res

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = ok_n = ok_h = ok_l = True
    diffs = []
    OFF_KNOBS = {"momentum_max_usd": 1000.0, "mom_min_depth": 1, "mom_max_markets": 2, "mom_kill_frac": 0.9,
                 "momentum_confirm_h": 0.0, "momentum_on_slope": 0.0, "mm_carry_min": 10000.0,
                 "election_holdback_usd": 50000.0, "election_holdback_from_utc": "",
                 "momentum_writes_frac": 1.0, "mom_exit_utc": "2026-10-02T00:00:00Z"}   # (the sleeve's knobs, off)
    grids = ({}, {"value_mode": True, "value_quote_hurdle": 0.05, **OFF_KNOBS},
             {"alloc_enabled": True, "alloc_mm_reserve": 15000.0, "value_mode": True, "mm_refill_fast": True,
              "mm_recycle_enabled": True, "alloc_max_edge_sell": 0.05, "take_enabled": True,
              "take_respect_reserve": True, "alloc_min_edge_buy": 0.05, **OFF_KNOBS},
             {"tilt_harvest_ladder": True, "harvest_total_usd": 10000.0, "alloc_mm_reserve": 20000.0,
              "state_max_usd": 15000.0, "value_mode": True, "alloc_enabled": True, "alloc_min_edge_buy": 0.02,
              "alloc_max_contract_usd": 100000.0, **OFF_KNOBS})
    for inv in ({}, {"21": 600, "R1": -900, "G1": 3000}, {"22": -800, "12": 2500, "A2": -500, "B2": 300}):
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
            wall = {"last_run_wall", "last_run", "since"}

            def cut(s):
                res = {}
                for k, v in s.items():
                    if k in VOLATILE or k.endswith("_seconds"):
                        continue
                    if k in ("alloc", "mm_risk_room"):
                        v = {a: ([f[1:] for f in x] if a == "flows" else x) for a, x in v.items() if a not in wall}
                    if k == "mm_funding" and isinstance(v.get("lots"), dict):   # (the lots' wall-clock times)
                        v = {**v, "lots": {e: [x[:2] for x in ls] for e, ls in v["lots"].items()}}
                    res[k] = v
                return res
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
    check("four cycles with fills send the same orders (3 positions x plain / value + the sleeve's knobs / allocator "
          "+ takes + refill / the P15 ladder + state cap + allocator)", ok_w, diffs[:1])
    check("every market's quote identical", ok_q, [d for d in diffs if d[0] == "quote"][:1])
    check("order notes identical", ok_n, [d for d in diffs if d[0] == "notes"][:1])
    check("status.json identical in keys AND values, harvest / mm_funding / state_caps included (no momentum key "
          "while off)", ok_s, [d for d in diffs if d[0] == "status"][:1])
    check("health keys and the status line identical", ok_h, [d for d in diffs if d[0] == "health"][:1])
    check("2-hourly summary line identical (no momentum piece while off)", ok_l,
          [d for d in diffs if d[0] == "summary"][:1])

    ok_p = True                                   # the pure planner: buys, refills, spare cash
    for inv, cash, extra in (({"21": 1000, "11": 2000}, 0.0, {}), ({"21": 1000}, 30000.0, {}),
                             ({"22": -900, "G1": 3000}, 5000.0, {"mm_refill_fast": True,
                                                                 "alloc_rank_all_markets": True}),
                             ({"R1": -2000, "12": 300}, 20000.0, {"tilt_harvest_ladder": True})):
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
        for ro in (False, True):
            pn = quiet(bn.alloc_plan, dict(api_n.inv), now_m, cash, set(), None, ro)
            pb = quiet(base.Bot.alloc_plan, bb_, dict(api_n.inv), now_m, cash, set(), None, ro)
            ok_p = ok_p and json.dumps(pn, sort_keys=True, default=str) == json.dumps(pb, sort_keys=True,
                                                                                      default=str)
    check("alloc_plan identical to ed65638 (4 positions / cash, refill-only and not; fund_usd None)", ok_p)

# ============================================================================================ classification
print("--- the classification: momentum longs")
api, b = mk_bot()
warm(b)
cls = quiet(b.mom_classify, dict(api.inv), time.monotonic())
bk_of = {e: (c["bucket"], c["dir"]) for e, c in cls.items()}
check("A2 (p 0.02 <= value_extreme_p 0.04): VALUE short whatever its gap", bk_of.get("A2") == ("value", "short"), bk_of)
check("G2 (p 0.20, short edge (0.32 - 0.20) / 0.68 = 17.6% >= 12%): VALUE short", bk_of.get("G2") == ("value", "short"))
check("B2 / D2 / Ohio Rep (p 0.10-0.12, gaps 3.4-7.3% < 12%): MOMENTUM longs",
      all(bk_of.get(e) == ("momentum", "long") for e in ("B2", "D2", "11")), bk_of)
check("G1 (favourite p 0.80, 14% >= 8%): VALUE long; Utah (0.45-0.55): the middle band",
      bk_of.get("G1") == ("value", "long") and bk_of.get("21") == ("mm", None), bk_of)
cls2 = quiet(b.mom_classify, {"B2": 500.0}, time.monotonic())
check("a momentum candidate holding a position outside the sleeve is VALUE (never bought over)",
      (cls2["B2"]["bucket"], cls2["B2"]["dir"]) == ("value", "long"), cls2["B2"])
b.mom_legs = {"G2": {"q": 100.0, "cost": 32.0}}
cls3 = quiet(b.mom_classify, {"G2": 100.0}, time.monotonic())
check("a sleeve leg is MOMENTUM whatever its numbers", (cls3["G2"]["bucket"], cls3["G2"]["dir"]) == ("momentum", "long"))
b.mom_legs = {}

# ============================================================================================ forced buys
print("--- the sleeve's buys (momentum_force: the manual override)")
api, b = forced_bot()
tick(b)
bought = wire(api)
check("buys the MOMENTUM longs, smallest gap first: B2 YES @ 0.15, D1 NO (sell YES @ 0.89), Ohio Rep YES @ 0.18",
      [x[:4] for x in bought] == [("B2", "yes", "buy", 0.15), ("D1", "yes", "sell", 0.89), ("11", "yes", "buy", 0.18)],
      bought)
check("...the largest size the level allows (2000 / 2000 / 1000)", [x[4] for x in bought] == [2000, 2000, 1000])
check("sleeve legs and cost basis: B2 +2000 ($300), D1 -2000 ($220), Ohio +1000 ($180)",
      {e: (v["q"], round(v["cost"], 2)) for e, v in b.mom_legs.items()} ==
      {"B2": (2000.0, 300.0), "D1": (-2000.0, 220.0), "11": (1000.0, 180.0)}, b.mom_legs)
st = b.mom_status()
check("status momentum {state active, cost 700, value_mark, value_outcome 560 at p, markets 3, kill_level 525}",
      st["state"] == "active" and st["cost"] == 700.0 and st["markets"] == 3 and st["kill_level"] == 525.0
      and abs(st["value_outcome"] - 560) < 1e-6, st)
check("notes tagged momentum (+ take): their own fill class", sum(1 for m in b.order_meta.values()
                                                                  if m.get("momentum") and m.get("take")) == 3)
check("the sleeve's markets are not quoted by the market maker (decide: NO_QUOTE)",
      all(quiet(b.decide, b.ex[e], 0.12, dict(api.inv), b.effective_inventory(dict(api.inv)), False, 0.0,
                time.monotonic()) == M.NO_QUOTE for e in ("B2", "D1", "11")))
check("...its shares are out of the race netting (effective_inventory)",
      abs(b.effective_inventory(dict(api.inv)).get("B1", 0.0)) < 1e-9)
check("...the allocator (refill / swaps / funding sales) leaves them alone (alloc_market_ok false)",
      not b.alloc_market_ok(b.ex["B2"]) and not b.alloc_market_ok(b.ex["D1"]))
b.cfg.tilt_harvest_ladder = True
_, why_ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("...the harvest ladder never ladders a sleeve market while it is held (why 'momentum')",
      all(why_.get(e) == "momentum" for e in ("B2", "D1", "11")), {e: why_.get(e) for e in ("B2", "D1", "11")})
b.cfg.tilt_harvest_ladder = False
check("...tilt exits leave them alone (tilt_exit_side None)", b.tilt_exit_side(b.ex["B2"], 0.12, 2000) is None)
quiet(b.log_fills, {})
mc = b.mm_carry(time.time())
check("mm_carry_24h: the sleeve's fills in their own class 'momentum' (not takes)",
      mc["fills"].get("momentum") == 3 and mc["fills"]["take"] == 0, mc["fills"])
check("...the class key is absent while no such fill exists", "momentum" not in mk_bot()[1].mm_carry(time.time())["fills"])
api, b = forced_bot(books={**books_default(), "B2": bk(0.13, 0.15, q=150)})
tick(b)
check("a level of 150 shares (< mom_min_depth 200): not bought", all(x[0] != "B2" for x in wire(api)))
api, b = forced_bot(alloc_max_contract_usd=150.0)
tick(b)
got = {x[0]: x[4] for x in wire(api)}
check("the market's collateral cap (alloc_max_contract_usd 150): B2 1000 shares ($150 at 0.15), Ohio 833 ($150 at "
      "0.18)", got.get("B2") == 1000 and got.get("11") == 833, got)
api, b = forced_bot(refs={**refs_default(), **RHODE}, state_max_usd=100.0)
quiet(b.st_refresh, dict(api.inv), time.monotonic())
tick(b)
got = {x[0]: x[4] for x in wire(api)}
check("the state cap (state_max_usd 100, the sleeve's collateral at p): RI's R1 YES (ask 0.12, p 0.10) stops at 1,000 "
      "shares, Ohio's at 833 (p 0.12); a market without a state (Beta) is not capped", got.get("R1") == 1000
      and got.get("11") == 833 and got.get("B2") == 2000, got)
check("...the blocked add is counted (state_caps.RI.blocked_adds)", b.st_blocked.get("RI", 0) >= 1, dict(b.st_blocked))
api, b = forced_bot(cash=1100.0)
tick(b)
check("funding (a): cash 1,100 with the MM reserve 1,000: only $100 spent (B2 666 shares at 0.15), then nothing",
      wire(api) == [("B2", "yes", "buy", 0.15, 666)], wire(api))
api, b = forced_bot(cash=900.0)
tick(b)
check("cash below the MM reserve: nothing bought (forced: (b) never applies)", not wire(api))
api, b = forced_bot(inv={"B1": 500, "11": -300})
tick(b)
got = [x[0] for x in wire(api)]
check("never the opposite side: long the Beta favourite (= short B2) -> no B2; short Ohio Rep -> no Ohio buy",
      "B2" not in got and "11" not in got and "D1" in got and api.inv.get("11") == -300, got)
api, b = forced_bot(mom_max_markets=1)
tick(b)
check("mom_max_markets 1: one market only", len({x[0] for x in wire(api)}) == 1, wire(api))
HL = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate", "H": "U.S. Senate"}
api, b = forced_bot(races=HL, books={**books_default(), "H1": bk(0.86, 0.88), "H2": bk(0.12, 0.13)},
                    refs={**refs_default(), "U.S. Senate|Republican": 0.88, "U.S. Senate|Democratic": 0.12})
tick(b)
check("a headline race (U.S. Senate, gap 1c) never bought while mom_headline is False",
      all(x[0] not in ("H1", "H2") for x in wire(api)), wire(api))
api, b = forced_bot(momentum_max_usd=400.0)
tick(b)
check("the target momentum_max_usd 400 (forced): B2 $300, then D1 $100 (909 NO)",
      [(x[0], x[4]) for x in wire(api)] == [("B2", 2000), ("D1", 909)], wire(api))
n1 = len(api.wire)
tick(b)
check("...target reached: no more buys next cycle", len(api.wire) == n1)
api, b = forced_bot()
b.global_reduce = True
tick(b)
check("reduce-only in force: no momentum buy", not wire(api))
api, b = forced_bot(live=False)
tick(b)
check("dry run: planned and logged, nothing sent, no leg booked", not wire(api) and not b.mom_legs
      and any(m.startswith("[dry] MOMENTUM buy") for m in CAP.lines))

# ============================================================================================ TiltSlope
print("--- TiltSlope: bins, the estimator, slope_24h, slope_6h, persistence")
ts = M.TiltSlope(BIN)
t0 = math.floor(NOW.timestamp() / BIN) * BIN
n = ts.add([(0.8, 0.75, 2), (0.2, 0.25, 2)] * 10, t0 + 60)
b0 = ts.bins.get(t0)
check("a 2-leg race: x = r - 1/2, g = r - mid; the bin keeps sum x g / sum x^2 / n / first and last time",
      n == 20 and b0 is not None and abs(b0[0] - 20 * 0.3 * 0.05) < 1e-9 and abs(b0[1] - 20 * 0.09) < 1e-9
      and b0[2] == 20, b0)
ts2 = M.TiltSlope(BIN)
ts2.add([(0.9, 0.5, 1)] * 25, t0 + 60)
check("a lone market: x = r - 0.5; the gap winsorised at 0.08 (0.40 -> 0.08)",
      abs(ts2.bins[t0][0] / ts2.bins[t0][1] - 0.08 / 0.4) < 1e-9, ts2.bins[t0])
ts3 = M.TiltSlope(BIN)
ts3.add([(0.8, 0.75, 2)] * 10, t0 + 60)
check("a bin with fewer than 20 samples has no value", ts3.values(t0 + BIN) == [])
ts4 = M.TiltSlope(BIN)
ts4.add([(0.8, 0.75, 2), (0.2, 0.25, 2)] * 15, t0 + 60)
check("the CURRENT bin counts only once its samples span a quarter of a bin", ts4.values(t0 + 600) == [])
ts4.add([(0.8, 0.75, 2), (0.2, 0.25, 2)] * 15, t0 + 0.3 * BIN)
check("...and then it does (value 1/6 = 16.7 points)", len(ts4.values(t0 + 0.3 * BIN)) == 1
      and abs(ts4.values(t0 + 0.3 * BIN)[0][1] - 1 / 6) < 1e-9)
ts5 = M.TiltSlope(BIN)
vals = [10.69, 11.92, 12.25, 12.48, 12.75, 12.82]
for i, v in enumerate(vals):
    k = t0 - (5 - i) * BIN
    ts5.bins[k] = [v / 100, 1.0, 50, k, k + 0.9 * BIN]
s24, s6 = ts5.slope_24h(t0 + 3 * H, 24.0), ts5.slope_6h(t0 + 3 * H)
check("slope_24h = the regression slope of the 6 bin values over 24 h in points a day (+2.29)",
      s24 is not None and abs(s24 - 2.2920) < 0.001, s24)
check("slope_6h = (latest - the bin 6 h back; a 4 / 8 h tie goes to the later one) / distance = +0.42",
      s6 is not None and abs(s6 - 0.42) < 0.001, s6)
ts6 = M.TiltSlope(BIN)
for k in (t0 - BIN, t0):
    ts6.bins[k] = [0.1, 1.0, 50, k, k + 0.9 * BIN]
check("slope_24h None with too few bins (needs >= 3 spanning half the window); slope_6h with a 4-h neighbour",
      ts6.slope_24h(t0 + H, 24.0) is None and ts6.slope_6h(t0 + H) is not None and abs(ts6.slope_6h(t0 + H)) < 1e-9)
check("slope_6h None when the latest bin is older than two bins (a stale series)", ts5.slope_6h(t0 + 3 * BIN) is None)
d = ts5.to_dict()
r5 = M.TiltSlope.from_dict(json.loads(json.dumps(d)), BIN)
check("to_dict / from_dict round trip (status.json): the same readings", abs(r5.slope_24h(t0 + 3 * H, 24.0) - s24)
      < 1e-9 and abs(r5.slope_6h(t0 + 3 * H) - s6) < 1e-9)
check("from_dict with another bin width or garbage: an empty series",
      not M.TiltSlope.from_dict(d, 2 * H).bins and not M.TiltSlope.from_dict({"bin_s": BIN, "bins": [["x"]]}, BIN).bins)

# ============================================================================================ the live feed
print("--- TiltSlope's live feed: liquid, non-headline, other traders' touch, spread, fresh book")
api, b = mk_bot(races=HL, books={**books_default(), "H1": bk(0.50, 0.52), "H2": bk(0.48, 0.50), "G1": bk(0.60, 0.70)},
                refs={**refs_default(), "U.S. Senate|Republican": 0.60, "U.S. Senate|Democratic": 0.40},
                momentum_enabled=True, momentum_auto=True)
warm(b)
got = []
b.tslope.add = lambda samples, t: got.extend(samples) or len(samples)
b.ma_seed = {"done": True, "merged": True}
b.ex["B2"].book = {"bids": [{"price": 0.13, "quantity": 500}], "asks": [{"price": 0.15, "quantity": 500}]}
b.cur_liquid = set(b.cur_liquid) - {"21"}
b.ex["22"].verified = time.monotonic() - 10 * b.cfg.book_stale
quiet(b.ts_feed, time.time(), time.monotonic())
mids = {round(m, 4) for _, m, _ in got}
check("headline legs (U.S. Senate) never sampled", got and not any(abs(r - 0.60) < 1e-9 or abs(r - 0.40) < 1e-9
                                                                   for r, _, _ in got), got)
check("a spread above momentum_slope_max_spread (G1 0.60 / 0.70) is not sampled", not any(abs(m - 0.65) < 1e-9
                                                                                          for m in mids), mids)
check("a market without a liquid Polymarket price (Utah Rep) and a stale book (Utah Dem) are not sampled",
      not any(abs(r - 0.55) < 1e-9 or abs(r - 0.45) < 1e-9 for r, _, _ in got), got)
check("the mid is the cached book's (OTHER traders: B2 0.13 / 0.15 -> 0.14), legs from the race",
      any(abs(m - 0.14) < 1e-9 and lg == 2 and abs(r - 0.12) < 1e-9 for r, m, lg in got), got)
api, b = mk_bot(momentum_enabled=True, momentum_auto=True)
warm(b)
quiet(b.cycle)                                    # (quotes of ours rest at the touch now)
b.drain_writes(5)
check("strip-own: the books the feed reads are other traders' only (our resting orders stripped on download)",
      b.my_orders and all(not any(abs(lv_["price"] - o.price) < 1e-9 and abs(lv_["quantity"] - o.qty) < 1e-9
                                  for lv_ in (b.ex[o.eid].book or {}).get("bids" if o.is_bid else "asks") or [])
                          for o in b.my_orders.values()), len(b.my_orders))
b.tslope.last_t = time.time()
nb = sum(v[2] for v in b.tslope.bins.values())
quiet(b.ts_feed, time.time() + 5, time.monotonic())
check("one sample a minute at most (TILT_SLOPE_SAMPLE_S)", sum(v[2] for v in b.tslope.bins.values()) == nb)

# ============================================================================================ seeding
print("--- seeding from the recorder (market_data.sqlite snapshots)")


def seed_db(path, base_t, vals, junk=True):
    """A recorder-like file (the bot's own schema): 2-leg race 'Alpha Gov', 10 snapshots a 4-h bin; bin i's tilt
    value = vals[i] points: r 0.8 / 0.2, mid = r - g for the Rep leg (g = 0.3 x value)."""
    _api, _b = mk_bot()
    _b.cfg.record_file = path
    db = _b.open_recorder()
    rows = []
    for i, v in enumerate(vals):
        for j in range(10):
            t = base_t + i * BIN + j * 600
            ts_ = M.iso(M.datetime.fromtimestamp(t, M.timezone.utc))
            g = 0.3 * v / 100
            rows.append((ts_, "live", "1", "Rep Alpha Gov", round(0.8 - g - 0.01, 6), round(0.8 - g + 0.01, 6), None,
                         0.8, None, None, 0.0))
            rows.append((ts_, "live", "2", "Dem Alpha Gov", round(0.2 + g - 0.01, 6), round(0.2 + g + 0.01, 6), None,
                         0.2, None, None, 0.0))
            if junk:                              # headline / own at the touch / wide spread / dry mode: all ignored
                rows.append((ts_, "live", "9", "Rep U.S. Senate", 0.10, 0.12, None, 0.9, None, None, 0.0))
                rows.append((ts_, "live", "1", "Rep Alpha Gov", 0.40, 0.42, None, 0.8, 0.40, None, 0.0))
                rows.append((ts_, "live", "2", "Dem Alpha Gov", 0.05, 0.30, None, 0.2, None, None, 0.0))
                rows.append((ts_, "dry", "1", "Rep Alpha Gov", 0.40, 0.42, None, 0.8, None, None, 0.0))
    db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    _b.db = None


tmpd = tempfile.mkdtemp()
dbp = os.path.join(tmpd, "md.sqlite")
now_w = time.time()
cur = math.floor(now_w / BIN) * BIN
seed_db(dbp, cur - 6 * BIN, [10.0, 10.5, 11.0, 11.5, 12.0, 12.5])   # (every bin done: no row after now)
bins = M.tilt_slope_seed(dbp, "live", now_w - 72 * H, now_w + 1, BIN, ("U.S. House", "U.S. Senate"), 0.06)
tsx = M.TiltSlope(BIN)
tsx.merge(bins)
vv = [round(100 * v, 6) for _, v in tsx.values(now_w)]
check("seeded bins = the rows' tilt (10.0 .. 12.5): headline, own-at-the-touch, wide-spread and dry rows ignored",
      vv == [10.0, 10.5, 11.0, 11.5, 12.0, 12.5], vv)
check("...slope_24h +3.00, slope_6h +3.00 points a day", abs(tsx.slope_24h(now_w, 24.0) - 3.0) < 1e-6
      and abs(tsx.slope_6h(now_w) - 3.0) < 1e-6, (tsx.slope_24h(now_w, 24.0), tsx.slope_6h(now_w)))
check("a missing file / a file without the table: no bins (best effort)",
      M.tilt_slope_seed(os.path.join(tmpd, "none.sqlite"), "live", 0, now_w, BIN) == {})
api, b = mk_bot(momentum_enabled=True, momentum_auto=True)
warm(b)
b.cfg.record_file = dbp
b.tslope, b.ma_seed = M.TiltSlope(BIN), None
quiet(b.ts_seed_step, now_w)
th = (b.ma_seed or {}).get("thread")
check("cold start: the seeding runs in a background thread (never blocks the cycle)",
      th is not None and b.ma_seed.get("note") == "reading")
if th is not None:
    th.join(30)
quiet(b.ts_seed_step, now_w)
check("...merged at the next cycle: the series source 'seed', the readings as the file's",
      b.tslope.source == "seed" and b.ma_seed.get("note") == "seeded"
      and abs((b.tslope.slope_24h(now_w, 24.0) or 0) - 3.0) < 0.2, (b.tslope.source, b.ma_seed))
check("...journal 'MOMENTUM tilt series seeded from the recorder ... slope24 +3.00, slope6 +3.00'",
      any(m.startswith("MOMENTUM tilt series seeded") and "slope24 +3.00" in m for m in CAP.lines))
tick(b)
st = b.mom_status()
check("status momentum.series_bins / series_source after the seed", len(st.get("series_bins") or []) >= 6
      and st.get("series_source") in ("seed", "seed+live"), (st.get("series_source"), len(st.get("series_bins") or [])))
api, b = mk_bot(momentum_enabled=True, momentum_auto=True)
warm(b)
tick(b)
check("no recorder (record_file ''): no seed, the reason says 'no history', armed, nothing bought (no crash)",
      (b.ma_seed or {}).get("note") == "no recorder file" and b.ma["reason"] == "no history"
      and b.ma["state"] == "armed" and not wire(api), (b.ma_seed, b.ma["reason"]))
bad_p = os.path.join(tmpd, "bad.sqlite")
with open(bad_p, "w") as f:
    f.write("not a database")
api, b = mk_bot(momentum_enabled=True, momentum_auto=True)
warm(b)
b.cfg.record_file = bad_p
b.tslope, b.ma_seed = M.TiltSlope(BIN), None
quiet(b.ts_seed_step, now_w)
if (b.ma_seed or {}).get("thread") is not None:
    b.ma_seed["thread"].join(30)
quiet(b.ts_seed_step, now_w)
check("a corrupt recorder file: logged, the series starts empty (no crash)", b.ma_seed.get("note") == "error"
      and not b.tslope.bins, b.ma_seed)
if os.path.exists(SNAP_DB):
    t_snap = M.parse_ts("2026-10-04T15:57:01Z").timestamp()
    sb = M.tilt_slope_seed(SNAP_DB, "live", t_snap - 72 * H, t_snap, BIN, ("U.S. House", "U.S. Senate"), 0.06)
    tss = M.TiltSlope(BIN)
    tss.merge(sb)
    last6 = [round(100 * v, 2) for _, v in tss.values(t_snap)][-6:]
    s24s, s6s = tss.slope_24h(t_snap, 24.0), tss.slope_6h(t_snap)
    print(f"    snap04 at 15:57 UTC 4 Oct: bins {last6}, slope_24h {s24s:+.3f}, slope_6h {s6s:+.3f} pts/day")
    check("snap04 (md.sqlite, 4 Oct 15:57 UTC): bins 10.69 / 11.92 / 12.25 / 12.48 / 12.75 / 12.82",
          last6 == [10.69, 11.92, 12.25, 12.48, 12.75, 12.82], last6)
    check("snap04: slope_24h ~ +2.29, slope_6h ~ +0.42 points a day (the owner's reading)",
          abs(s24s - 2.29) < 0.05 and abs(s6s - 0.42) < 0.03, (s24s, s6s))
else:
    print("    (snap04 not present: its reading is skipped)")

# ============================================================================================ the trigger
print("--- the trigger: slope_24h >= on AND slope_6h > 0, held momentum_confirm_h; one burst never triggers")
api, b = auto_bot()
set_series(b, at_h(0), [])
tick(b, at_h(0))
check("no history: armed, reason 'no history', nothing bought", b.ma["state"] == "armed"
      and b.ma["reason"] == "no history" and not wire(api), (b.ma, wire(api)))
set_series(b, at_h(0), [10.0, 11.0])
tick(b, at_h(0))
check("2 bins: slope_24h n/a -> armed", b.ma["state"] == "armed" and b.ma_s24 is None
      and b.ma["reason"].startswith("slope24 n/a"), b.ma["reason"])
set_series(b, at_h(0), FLAT_UP)
tick(b, at_h(0))
check("slope_24h below momentum_on_slope (+0.09 < 0.5): armed, no confirm clock", b.ma["state"] == "armed"
      and b.ma["confirm_since"] is None and "< 0.5" in b.ma["reason"], b.ma)
set_series(b, at_h(0), TURNING)
tick(b, at_h(0))
check("slope_24h above but slope_6h <= 0 (a stale average): armed, no confirm clock", b.ma["state"] == "armed"
      and b.ma["confirm_since"] is None and b.ma_s24 > 0.5 and b.ma_s6 < 0, (b.ma_s24, b.ma_s6))
ALERTS.clear()
for h in (0, 1, 2, 3):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("conditions held 3 h < momentum_confirm_h 4: still armed (confirming), nothing bought",
      b.ma["state"] == "armed" and b.ma["reason"].startswith("confirming") and not wire(api), b.ma["reason"])
set_series(b, at_h(4), RISING)
tick(b, at_h(4))
check("held 4 h: ON, the ramp's target momentum_start_usd 10,000", b.ma["state"] == "on"
      and abs(b.ma["target_usd"] - 10000) < 1e-6, b.ma)
check("...ALERT on switch-on (with the slopes)", any("switched ON" in a and "slope24 +3.00" in a for a in ALERTS),
      ALERTS)
check("...and it buys in that cycle (B2 YES at its ask first)", wire(api) and wire(api)[0][:4] == ("B2", "yes", "buy",
                                                                                                   0.15), wire(api))
check("journal: 'MOMENTUM eval' lines with the state, the slopes and the reason",
      any(m.startswith("MOMENTUM eval: on") and "slope24 +3.00" in m for m in CAP.lines)
      and any(m.startswith("MOMENTUM eval: armed") for m in CAP.lines))
api, b = auto_bot()
for h, vals in ((0, RISING), (1, RISING), (2, TURNING), (3, RISING), (4, RISING), (5, RISING)):
    set_series(b, at_h(h), vals)
    tick(b, at_h(h))
check("one burst never triggers: 2 h held, a dip (slope_6h < 0), then 3 h held -> the clock restarted, still armed",
      b.ma["state"] == "armed" and b.ma["confirm_since"] == at_h(3).timestamp() and not wire(api), b.ma)
set_series(b, at_h(7), RISING)
tick(b, at_h(7))
check("...4 h after the dip: on", b.ma["state"] == "on")
api, b = auto_bot(momentum_force=True, momentum_auto=False)
set_series(b, at_h(0), FALLING)
tick(b, at_h(0))
check("momentum_force: buys with no trigger at all (falling tilt), to momentum_max_usd",
      wire(api) and b.mom_buy_target() == 40000.0 and b.ma["reason"] == "forced on (momentum_force)",
      (wire(api), b.ma["reason"]))
api, b = auto_bot(momentum_auto=False)
set_series(b, at_h(0), RISING)
for h in (0, 5):
    tick(b, at_h(h))
check("momentum_auto off (and no force): momentum_enabled alone never buys", not wire(api) and b.ma["state"] == "off"
      and b.ma["reason"].startswith("momentum_auto off"), (wire(api), b.ma))
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
on0 = b.ma["state"]
ALERTS.clear()
b.cfg.momentum_auto = False
tick(b, at_h(1))
check("switched OFF (momentum_auto false) while on: 'off', ALERT, no further buys", on0 == "on"
      and b.ma["state"] == "off" and any("switched OFF" in a for a in ALERTS) and not b.mom_may_buy(),
      (on0, b.ma["state"], ALERTS))

# ============================================================================================ the ramp
print("--- the ramp: start 10k, +10k per 6 h of rising tilt, stalls on slope_6h <= 0, capped at momentum_max_usd")
DEEP = {**books_default(), "B2": bk(0.13, 0.15, q=200000), "D2": bk(0.11, 0.13, q=200000),
        "D1": bk(0.89, 0.91, q=200000)}
api, b = auto_bot(momentum_confirm_h=0.0, books=DEEP, alloc_max_contract_usd=100000.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
c0 = cost(b)
check("on: buys up to the 10k target, not 40k at once", b.ma["state"] == "on" and 9000 <= c0 <= 10000 + 1, c0)
for h in (1, 2, 3, 4, 5):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("5 h of rising tilt: no step yet (target 10k), no buy beyond it",
      abs(b.ma["target_usd"] - 10000) < 1e-6 and cost(b) <= 10000 + 1)
set_series(b, at_h(6), RISING)
tick(b, at_h(6))
check("6 h of rising tilt: +10k (target 20k, step 1), bought up to it", abs(b.ma["target_usd"] - 20000) < 1e-6
      and b.ma["steps"] == 1 and 19000 <= cost(b) <= 20000 + 1, (b.ma["target_usd"], cost(b)))
st = b.mom_status()
check("status: target_usd 20,000, steps 1, next_step_at 6 h later", st["target_usd"] == 20000 and st["steps"] == 1
      and st["next_step_at"] == M.iso(at_h(12)), (st["target_usd"], st["steps"], st["next_step_at"]))
set_series(b, at_h(8), TURNING)
tick(b, at_h(8))
check("slope_6h <= 0: the ramp stalls (no step, reason 'ramp stalled', no next step)", b.ma["rise_since"] is None
      and "stalled" in b.ma["reason"] and b.mom_status()["next_step_at"] is None, b.ma)
for h in (9, 12, 14):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("rising again from 9 h: no step at 12 h (only 3 h of rise), target still 20k",
      abs(b.ma["target_usd"] - 20000) < 1e-6, b.ma["target_usd"])
set_series(b, at_h(15), RISING)
tick(b, at_h(15))
check("...the next step 6 h after the rise restarted (15 h): 30k", abs(b.ma["target_usd"] - 30000) < 1e-6,
      b.ma["target_usd"])
for h in (21, 27, 33):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("capped at momentum_max_usd 40,000 (whatever the account)", abs(b.ma["target_usd"] - 40000) < 1e-6
      and cost(b) <= 40000 + 1, (b.ma["target_usd"], cost(b)))
api, b = auto_bot(momentum_confirm_h=0.0, momentum_max_usd=5000.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
check("momentum_max_usd below momentum_start_usd (5k): the target starts at the cap", abs(b.ma["target_usd"] - 5000)
      < 1e-6, b.ma["target_usd"])

# ============================================================================================ funding
print("--- funding: (a) free cash above what is kept back, (b) the MM reserve if mm_carry_24h is low, (c) VALUE sales")


def fund_bot(cash, per_day="none", **kw):
    args = dict(momentum_confirm_h=0.0, books=DEEP, cash=cash)
    args.update(kw)
    api_, b_ = auto_bot(**args)
    if per_day != "none":
        b_.ops_last = {"mm_carry_24h": {"per_day": per_day}}
    set_series(b_, at_h(0), RISING)
    tick(b_, at_h(0))
    return api_, b_


api, b = fund_bot(5000.0)
check("(a) cash 5,000, the MM reserve 1,000 (ladder off): the sleeve spends the 4,000 above it, no more",
      3900 <= cost(b) <= 4000 + 1, cost(b))
check("...funded_from cash ~4,000, reserve 0", abs(b.ma_funded["cash"] - cost(b)) < 1 and b.ma_funded["reserve"] == 0,
      b.ma_funded)
api, b = fund_bot(5000.0, per_day=100.0)
check("(b) mm_carry_24h 100/day < mm_carry_min 300: the MM reserve funds it too (~5,000 spent)",
      4900 <= cost(b) <= 5000 + 1, cost(b))
check("...funded_from: cash ~4,000 then reserve ~1,000", abs(b.ma_funded["cash"] - 4000) < 160
      and b.ma_funded["reserve"] > 800 and abs(b.ma_funded["cash"] + b.ma_funded["reserve"] - cost(b)) < 1e-6,
      b.ma_funded)
api, b = fund_bot(5000.0, per_day=500.0)
check("(b) not when mm_carry_24h 500/day >= 300", cost(b) <= 4000 + 1, cost(b))
api, b = fund_bot(5000.0, per_day=None)
check("(b) not when mm_carry_24h is unknown (None)", cost(b) <= 4000 + 1, cost(b))
# the ladder's carve-out: alloc_mm_reserve 20k, harvest_total_usd 10k -> the MM keeps 10k, the ladder's 10k kept too
api, b = fund_bot(25000.0, alloc_mm_reserve=20000.0, tilt_harvest_ladder=True, harvest_total_usd=10000.0)
check("(a) with the harvest ladder on (reserve 20k = MM 10k + carve-out 10k, nothing resting): cash 25,000 -> only "
      "the 5,000 above both", 4900 <= cost(b) <= 5000 + 1 and abs(b.cash_reserve() - 20000) < 1e-6,
      (cost(b), b.cash_reserve()))
api, b = fund_bot(18000.0, alloc_mm_reserve=20000.0, tilt_harvest_ladder=True, harvest_total_usd=10000.0)
check("...cash 18,000 < the 20,000 kept back: nothing bought without (b)", cost(b) == 0, cost(b))
api, b = fund_bot(18000.0, per_day=100.0, alloc_mm_reserve=20000.0, tilt_harvest_ladder=True,
                  harvest_total_usd=10000.0)
check("(b) with the ladder on: the MM's effective reserve (10k) is spent, NEVER the ladder's carve-out (8,000 spent, "
      "10,000 left)", 7900 <= cost(b) <= 8000 + 1 and b.cash_left() >= 10000 - 1, (cost(b), b.cash_left()))
api, b = fund_bot(25000.0, election_holdback_usd=3000.0, election_holdback_from_utc="")
check("the election holdback hook (3,000 from now): kept back too - 25,000 - 1,000 - 3,000 = 21,000 at most",
      abs(b.cash_reserve() - 4000) < 1e-6 and cost(b) <= 21000 + 1 and b.el_holdback_left() == 3000.0,
      (b.cash_reserve(), cost(b)))
_, b2_ = mk_bot(election_holdback_usd=3000.0)
check("...from election_holdback_from_utc (default 3 Nov 12:00 UTC): 0 before it", b2_.el_holdback_left() == 0.0)
# (c) VALUE sales for the shortfall through the allocator: G1 long 3000 (p 0.80, bid 0.81: at / above the floor);
# A2 short 3000 (p 0.02, buy-back at 0.08: edge-held 6.5% > alloc_max_edge_sell and beyond the floor: never sold)
VB = {**DEEP, "G1": bk(0.81, 0.83)}
api, b = fund_bot(1500.0, inv={"G1": 3000, "A2": -3000}, books=VB, alloc_enabled=True)
fp = [pr for pr in b.alloc_pairs if pr.get("fund")]
check("(c) cash short: a funding sale planned for the allocator (alloc_pairs, 'fund'), lowest edge-held first: G1 "
      "2,000 @ the bid 0.81 (the level's depth)", len(fp) == 1 and fp[0]["sell"]["eid"] == "G1"
      and fp[0]["sell"]["qty"] == 2000 and fp[0]["buy"] is None, [pr["sell"] for pr in fp])
check("...A2 (edge-held 6.5% > alloc_max_edge_sell, its buy-back beyond the floor) not planned", not any(
    pr["sell"].get("eid") == "A2" for pr in b.alloc_pairs))
check("...journal 'MOMENTUM funding (c): selling VALUE ...'", any(m.startswith("MOMENTUM funding (c): selling VALUE")
                                                                  for m in CAP.lines))
check("rule 5: the sleeve never buys where that sale is pending (mom_sell_planned 'allocator')",
      b.mom_sell_planned(b.ex["G1"]) == "allocator")
n_w = len(api.wire)
atick(b)
sold = [x for x in wire(api)[n_w:] if x[0] == "G1"]
check("...sold by the allocator's sale step (alloc_sell): G1 2,000 @ 0.81, IOC", sold == [("G1", "yes", "sell", 0.81,
                                                                                            2000)], wire(api)[n_w:])
check("...funded_from.value_sales = the proceeds (2,000 x 0.81 = 1,620), earmarked (pool)",
      abs(b.ma_funded["value_sales"] - 1620) < 0.01 and abs(b.ma_pool - 1620) < 0.01, b.ma_funded)
check("...ev_given_up = shares x (p - price) = 2,000 x (0.80 - 0.81) = -20 (a gain here)",
      abs(b.ma_ev_given_up + 20) < 1e-6, b.ma_ev_given_up)
st = b.mom_status()
check("...status ev_given_up_per_10k = ev / (value_sales / 10,000)", st["ev_given_up_per_10k"] is not None
      and abs(st["ev_given_up_per_10k"] - (-20 / 0.162)) < 0.01, st["ev_given_up_per_10k"])
check("...journal 'MOMENTUM funding sold ... at / above the value floor' and 'ALLOC sold ... (momentum funding)'",
      any(m.startswith("MOMENTUM funding sold Rep Gamma Senate 2000 @ 0.810") for m in CAP.lines)
      and any("(momentum funding)" in m for m in CAP.lines))
n_w = len(api.wire)
set_series(b, at_h(0.02), RISING)
tick(b, at_h(0.02))
atick(b)
check("...the next cycle (within 10 min): no second funding plan, no re-sale", not [x for x in wire(api)[n_w:]
                                                                                  if x[0] in ("G1", "A2")],
      wire(api)[n_w:])
api.cash += 1620.0
c0 = cost(b)
set_series(b, at_h(0.05), RISING)
tick(b, at_h(0.05))
spent = cost(b) - c0
check("...the proceeds in cash: the sleeve buys with them, attributed to value_sales_spent first",
      spent > 1000 and b.ma_funded.get("value_sales_spent", 0) >= min(spent, 1620) - 1, (spent, b.ma_funded))
api, b = fund_bot(1500.0, inv={"G1": 3000}, books=VB, alloc_enabled=True)
api.books["G1"] = bk(0.75, 0.83)                  # (the bid falls below p 0.80 - value_sell_margin before the sale)
atick(b)
check("the floor at the sale (alloc_sell, the fresh book 0.75 < p - margin): NOT sold, the market not planned again "
      "for an hour", not any(x[0] == "G1" for x in wire(api)) and (tick(b, at_h(0.01)) is not None)
      and "G1" in b.ma_fund_refused, (wire(api), b.ma_fund_refused))
api, b = fund_bot(1500.0, inv={"G1": 3000}, books=VB, alloc_enabled=True, momentum_fund_value=False)
check("momentum_fund_value False: no VALUE sale at all", not any(pr.get("fund") for pr in b.alloc_pairs))
api, b = fund_bot(1500.0, inv={"G1": 3000}, books=VB, alloc_enabled=False)
check("the allocator off: no funding sale (its sale machinery sells them)", not b.alloc_pairs)
api, b = fund_bot(1500.0, inv={"21": 3000}, books=VB, alloc_enabled=True)
check("a middle-band holding (MM inventory, Utah p 0.55) is never sold to fund the sleeve",
      not any(pr.get("fund") for pr in b.alloc_pairs))
api, b = fund_bot(1500.0, inv={"G1": 3000}, books=VB, alloc_enabled=True, alloc_max_turnover_per_hour=500.0)
fp = [pr for pr in b.alloc_pairs if pr.get("fund")]
check("within alloc_max_turnover_per_hour (500): the funding sale is cut to it (617 shares at 0.81)",
      len(fp) == 1 and fp[0]["sell"]["qty"] == int(500 / 0.81), [pr["sell"] for pr in fp])
# the others leave the shortfall alone while the round is open
api, b = fund_bot(1500.0)
hold = b.ma_hold
check("a round open with a shortfall and candidates: ma_hold = the shortfall (status hold_usd)",
      hold > 5000 and abs(b.mom_status()["hold_usd"] - hold) < 0.01, hold)
pr = {"buy": {"eid": "21", "short": False, "px": 0.56, "label": "Rep Utah Senate"}, "sold_at": -1e18,
      "status": "sold", "sell": {"kind": "cash"}}
b.alloc_run = {}
r_ = quiet(b.alloc_buy, pr, dict(api.inv), b.orders_by_eid(M.utcnow()), time.monotonic(), time.time(), set())
check("...the allocator's spare-cash buy pauses (blocked_by momentum)", r_ is False
      and b.alloc_run.get("blocked_by", {}).get("momentum") == 1, b.alloc_run)
pr2 = dict(pr, sold_at=time.monotonic() - 5)
b.alloc_run = {}
quiet(b.alloc_buy, pr2, dict(api.inv), b.orders_by_eid(M.utcnow()), time.monotonic(), time.time(), set())
check("...a sold pair's buy (its own sale's cash) is not paused", not b.alloc_run.get("blocked_by", {}).get("momentum"))
pl, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0 + hold, set(), None)
b.ma_hold = 0.0
pl0, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 1000.0 + hold, set(), None)
b.ma_hold = hold
check("...the allocator's spare cash leaves the shortfall (no 'spare cash' pair out of it; with no round: one)",
      not any(p_["sell"]["kind"] == "cash" for p_ in pl) and any(p_["sell"]["kind"] == "cash" for p_ in pl0),
      ([p_["sell"] for p_ in pl], [p_["sell"] for p_ in pl0]))
b.cfg.tilt_harvest_ladder = True
b.cfg.harvest_min_edge = 0.0
b.cfg.harvest_total_usd = 100000.0
sent = []
b.place_orders_keep = lambda orders, keep: sent.extend(orders) or [{"index": k, "ok": True, "data": {}}
                                                                   for k in range(len(orders))]
quiet(b.hv_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("...the harvest ladder places NO new level while the round is short (status harvest.paused_for_momentum)",
      not sent and b.hv_mom_paused and b.hv_status().get("paused_for_momentum") is True and b.hv_quote_hold() == 0.0,
      (sent[:2], b.hv_mom_paused))
_, why_ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("...and yields the candidates' races (B / D / Ohio: 'momentum')",
      all(why_.get(e) == "momentum" for e in ("B1", "B2", "D1", "12")), {e: why_.get(e) for e in ("B1", "B2", "D1")})
b.ma_hold = 0.0
quiet(b.hv_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("...the round funded (no shortfall): the ladder places again", sent and not b.hv_mom_paused, len(sent))
api, b = fund_bot(1500.0)
seen = {}
orig_setup = b.ladder_setup


def spy(*a, **k):
    seen.update(plan=b.cg_plan_left, cash=b.cash_left(), hold=b.ma_hold)
    return orig_setup(*a, **k)


b.ladder_setup = spy
set_series(b, M.utcnow(), RISING)
quiet(b.cycle)
b.drain_writes(5)
check("...the quoter's plan budget leaves the shortfall (cash left - ma_hold)",
      seen and seen["hold"] > 0 and abs(seen["plan"] - max(0.0, seen["cash"] - seen["hold"])) < 1e-6, seen)

# ============================================================================================ flips
print("--- the flip: slope_24h <= 0, the profit target, the date, the manual exit, the kill; the ladder takes over")
api, b = auto_bot(momentum_confirm_h=0.0, mom_exit_hours=0.25)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
legs0 = dict(b.mom_legs)
ALERTS.clear()
n_w = len(wire(api))
set_series(b, at_h(1), FALLING)
tick(b, at_h(1))
check("slope_24h <= 0: flipped, the exit started (state exiting), ALERT 'switched OFF (24-h slope ...)'",
      legs0 and b.ma["state"] == "flipped" and b.mom_state == "exiting"
      and any("switched OFF" in a and "24-h slope" in a for a in ALERTS), (b.ma["state"], b.mom_state, ALERTS))
set_series(b, at_h(1.1), FALLING)
tick(b, at_h(1.1))                                # (the exit is paced over mom_exit_hours from the flip)
check("...the sleeve's YES sold into the bids (B2 sold at its bid 0.13)", any(x[:4] == ("B2", "yes", "sell", 0.13)
                                                                             for x in wire(api)[n_w:]), wire(api)[n_w:])
check("...status: on false, auto_state flipped, the reason", not b.mom_status()["on"]
      and b.mom_status()["auto_state"] == "flipped" and "24-h slope" in b.mom_status()["reason"],
      b.mom_status()["reason"])
for h in (1.2, 1.4):
    set_series(b, at_h(h), FALLING)
    tick(b, at_h(h))
b.cfg.tilt_harvest_ladder = True
_, why_ = quiet(b.hv_plan, dict(api.inv), time.monotonic())
check("...sold out (exited): the harvest ladder takes those markets over (no longer 'momentum')",
      b.mom_state == "exited" and not b.mom_legs and all(why_.get(e) != "momentum" for e in legs0),
      (b.mom_state, {e: why_.get(e) for e in legs0}))
b.cfg.tilt_harvest_ladder = False
api, b = auto_bot(momentum_confirm_h=0.0, momentum_force=True)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
set_series(b, at_h(1), FALLING)
tick(b, at_h(1))
check("momentum_force: a slope_24h <= 0 does not flip it (the manual override)", b.mom_state == "active"
      and b.ma["state"] != "flipped", (b.mom_state, b.ma["state"]))
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
api.books["B2"] = bk(0.30, 0.32)
api.books["D1"] = bk(0.70, 0.72)
api.books["11"] = {"bids": [lvl(0.30, 1000)], "asks": [lvl(0.32, 1000)]}
set_series(b, at_h(1), RISING)
tick(b, at_h(1))
first = b.mom_state
set_series(b, at_h(1) + timedelta(seconds=130), RISING)
tick(b, at_h(1) + timedelta(seconds=130))
check("profit target (mark >= 1.25 x cost): not on the first reading, flipped after 120 s held",
      first == "active" and b.mom_state == "exiting" and b.ma["state"] == "flipped"
      and "profit target" in b.ma["reason"], (first, b.mom_state, b.ma["reason"]))
api, b = auto_bot(momentum_confirm_h=0.0, momentum_profit_target=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
api.books["B2"] = bk(0.30, 0.32)
for s in (0, 200):
    set_series(b, at_h(1) + timedelta(seconds=s), RISING)
    tick(b, at_h(1) + timedelta(seconds=s))
check("momentum_profit_target 0: no profit flip", b.mom_state == "active" and b.ma["state"] == "on")
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
ALERTS.clear()
b.cfg.mom_exit_utc = "2026-10-01T00:00:00Z"
tick(b, at_h(0.1))
check("the date (mom_exit_utc passed): flipped, reason mom_exit_utc, ALERT", b.mom_state == "exiting"
      and b.ma["state"] == "flipped" and "mom_exit_utc" in b.ma["reason"]
      and any("switched OFF" in a for a in ALERTS), (b.mom_state, b.ma))
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
b.cfg.momentum_exit = True
tick(b, at_h(0.1))
check("the manual momentum_exit: flipped, reason 'manual momentum_exit'", b.mom_state == "exiting"
      and b.ma["state"] == "flipped" and "manual" in b.ma["reason"], b.ma)
api, b = auto_bot(momentum_confirm_h=0.0, momentum_start_usd=700.0)   # (the target reached: a pure mark move)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
for e, v in b.mom_legs.items():
    api.books[e] = bk(0.01, 0.03, q=200000) if v["q"] > 0 else bk(0.97, 0.99, q=200000)
ALERTS.clear()
for s in (0, 60, 130):
    set_series(b, at_h(1) + timedelta(seconds=s), RISING)
    tick(b, at_h(1) + timedelta(seconds=s))
check("the kill (mark < 0.75 x cost for 120 s): killed, one kill alert", b.mom_state == "killed"
      and b.ma["state"] == "killed" and sum("KILLED" in a for a in ALERTS) == 1, (b.mom_state, b.ma["state"], ALERTS))
n_w = len(wire(api))
for h in (2, 30, 60):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("...killed never re-arms by itself (the trigger held 58 h): no buy", b.ma["state"] == "killed"
      and not [x for x in wire(api)[n_w:] if x[2] == "buy" and x[1] == "yes" and x[0] == "B2"], b.ma)
b.cfg.momentum_enabled = False
tick(b, at_h(61))
b.cfg.momentum_enabled = True
set_series(b, at_h(62), RISING)
tick(b, at_h(62))
check("...the manual latch reset (momentum_enabled false then true): armed again (then on with the trigger)",
      b.mom_state == "active" and b.ma["state"] in ("armed", "on"), (b.mom_state, b.ma["state"]))

# ============================================================================================ the exit
print("--- the exit over mom_exit_hours (floor exempt), never re-bought")
api, b = forced_bot(value_mode=True)
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
                     ("11", "yes", "sell", 0.1, 166)]), got)
check("...below the value floor and still sent (_mom_exit: momentum positions are exempt); the short leg bought "
      "back as a covered sale (sell NO @ 0.09)", any(x[0] == "B2" and x[3] == 0.1 for x in got)
      and ("D1", "no", "sell", 0.09, 333) in got)
check("...cost basis shrinks in proportion (B2 300 -> 250.05)", abs(b.mom_legs["B2"]["cost"] - 300 * 1667 / 2000) < 1e-6,
      b.mom_legs["B2"])
check("exit_progress in status (started, hours 6, shares left)", (b.mom_status()["exit_progress"] or {}).get("hours")
      == 6.0 and b.mom_status()["exit_progress"]["shares_left"] == 5000 - 832, b.mom_status()["exit_progress"])
n1 = len(api.wire)
tick(b, at=T0 + timedelta(hours=1, minutes=1))
check("a minute later: only what the schedule adds, never a burst", sum(x[4] for x in wire(api)[n1:]) <= 15,
      wire(api)[n1:])
tick(b, at=T0 + timedelta(hours=6, seconds=1))
check("after 6 h: everything sold, the sleeve empty, state exited", not b.mom_legs and b.mom_state == "exited"
      and not api.inv.get("B2") and not api.inv.get("D1") and not api.inv.get("11"), (b.mom_legs, api.inv))
b.cfg.momentum_exit = False
api.books["B2"]["asks"] = [lvl(0.15, 2000)]
n2 = len(api.wire)
tick(b, at=T0 + timedelta(hours=7))
check("momentum_exit back to false: the sleeve is NEVER re-bought (state exited)", b.mom_state == "exited"
      and len(api.wire) == n2)
api, b = forced_bot(mom_exit_utc="2026-10-02T00:00:00Z")
tick(b)
check("mom_exit_utc in the past: no sleeve is ever bought (exited at once)", not b.mom_legs
      and b.mom_state in ("exiting", "exited"), b.mom_state)

# ============================================================================================ the kill
print("--- the kill switch (a -30% mark move) and its latch")
api, b = forced_bot(momentum_max_usd=700.0)
tick(b)
c0 = cost(b)
for e, bb, aa in (("B2", 0.10, 0.11), ("D1", 0.92, 0.925), ("11", 0.12, 0.13)):
    api.books[e] = bk(bb, aa, q=5000)
fresh(b)
mark = sum(b.mom_mark(e, v) for e, v in b.mom_legs.items())
check("the mark after the move: 490 = 70% of the cost 700 (-30%)", abs(mark - 0.7 * c0) < 0.01 and c0 == 700.0,
      (mark, c0))
T1 = M.utcnow()
ALERTS.clear()
n0 = len(api.wire)
tick(b, at=T1)
check("below 0.75 x cost: the 120 s clock starts, still active, nothing sold", b.mom_state == "active"
      and b.mom_below_since is not None and len(api.wire) == n0)
tick(b, at=T1 + timedelta(seconds=60))
check("60 s later: still active", b.mom_state == "active")
tick(b, at=T1 + timedelta(seconds=121))
check("121 s below: KILLED (exit started, latched), one alert", b.mom_state == "killed"
      and b.mom_exit_wall is not None and len([a for a in ALERTS if "KILLED" in a]) == 1, ALERTS)
tick(b, at=T1 + timedelta(seconds=121 + 3600))
check("...the exit sells over mom_exit_hours (1/6 after an hour)", sorted(x[0] for x in wire(api)[n0:])
      == ["11", "B2", "D1"] and b.mom_legs["B2"]["q"] == 2000 - 333, (wire(api)[n0:], b.mom_legs))
api.books["B2"]["asks"] = [lvl(0.15, 2000)]
n1 = len(api.wire)
tick(b, at=T1 + timedelta(seconds=121 + 7201))
check("killed with momentum_enabled still true: no buy (the latch), no second alert",
      all(x[2] != "buy" or x[1] == "no" for x in wire(api)[n1:]) and b.mom_state == "killed"
      and len([a for a in ALERTS if "KILLED" in a]) == 1, wire(api)[n1:])
b.write_status(True)
with open(b.cfg.status_file) as f:
    saved = json.load(f).get("momentum") or {}
check("status momentum while killed: state killed, kill_level, legs and cost basis saved",
      saved.get("state") == "killed" and abs(saved.get("kill_level", 0) - 0.75 * cost(b)) < 0.01
      and set(saved.get("leg_state") or {}) == set(b.mom_legs), saved.get("state"))
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
api, b = forced_bot(momentum_max_usd=700.0)
tick(b)
for e, bb, aa in (("B2", 0.10, 0.11), ("D1", 0.92, 0.925), ("11", 0.12, 0.13)):
    api.books[e] = bk(bb, aa, q=5000)
T2 = M.utcnow()
tick(b, at=T2)
for e, bk_ in (("B2", bk(0.14, 0.16, q=5000)), ("D1", bk(0.88, 0.90, q=5000)), ("11", bk(0.17, 0.19, q=5000))):
    api.books[e] = bk_
tick(b, at=T2 + timedelta(seconds=60))
check("the mark recovers within 120 s: the clock resets", b.mom_below_since is None and b.mom_state == "active")

# ============================================================================================ latch, re-arm
print("--- the latch and the re-arm")
api, b = auto_bot(momentum_confirm_h=0.0, mom_exit_hours=0.25)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
set_series(b, at_h(1), FALLING)
tick(b, at_h(1))
for h in (1.2, 1.4, 1.6):
    set_series(b, at_h(h), FALLING)
    tick(b, at_h(h))
check("flipped: the exit sells everything (exited), no buy while falling", b.mom_state == "exited"
      and not b.mom_legs and b.ma["state"] == "flipped", (b.mom_state, b.mom_legs))
n_w = len(wire(api))
for h in (2, 14, 25):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("a fresh trigger for 23 h < momentum_rearm_h 24: still flipped, nothing bought ('re-arm in ...')",
      b.ma["state"] == "flipped" and not wire(api)[n_w:] and "re-arm in" in b.ma["reason"], b.ma)
ALERTS.clear()
set_series(b, at_h(26), RISING)
tick(b, at_h(26))
check("24 h of a fresh trigger: RE-ARMED (ALERT) and on again (the trigger held), buying",
      b.ma["state"] == "on" and b.mom_state == "active" and any("RE-ARMED" in a for a in ALERTS)
      and any(x[2] in ("buy", "sell") for x in wire(api)[n_w:]), (b.ma["state"], ALERTS))
api, b = auto_bot(momentum_confirm_h=0.0, mom_exit_hours=0.25, momentum_rearm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
for h in (1, 1.2, 1.4, 1.6):
    set_series(b, at_h(h), FALLING)
    tick(b, at_h(h))
for h in (2, 30, 80):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("momentum_rearm_h 0: a flipped sleeve never re-arms by itself; only the manual latch reset",
      b.ma["state"] == "flipped" and b.mom_state == "exited"
      and (setattr(b.cfg, "momentum_enabled", False) or tick(b, at_h(81)) is not None)
      and (setattr(b.cfg, "momentum_enabled", True) or set_series(b, at_h(82), RISING) or tick(b, at_h(82)) is not None)
      and b.ma["state"] in ("armed", "on") and b.mom_state == "active", b.ma)

# ============================================================================================ rule 5
print("--- rule 5: no fake buying")
api, b = auto_bot(momentum_confirm_h=0.0)
b.hv_sides = {"B1": "bid"}
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
check("harvest levels in the race (B1 bids): B2 is NOT bought (the candidate counted before rule 5)",
      not any(x[0] in ("B1", "B2") for x in wire(api)) and "B2" in b.ma_cand_raw, (wire(api), b.ma_cand_raw))
api, b = auto_bot(momentum_confirm_h=0.0)
b.alloc_pairs = [{"status": "pending", "sell": {"eid": "B2"}, "buy": None}]
check("an allocator sale (refill / swap / funding) pending there: not bought", b.mom_sell_planned(b.ex["B2"])
      == "allocator")
b.alloc_pairs = []
b.my_orders[901] = M.Resting(901, "B1", False, 0.88, 50, M.utcnow() + timedelta(seconds=600))
b.order_meta[901] = {"alloc": True, "set_ladder": 1, "t": time.time()}
check("another feature's order resting in the race (a set-ladder ask): not bought",
      b.mom_sell_planned(b.ex["B2"]) == "resting")
b.my_orders.pop(901)
check("nothing planned / resting: may buy", b.mom_sell_planned(b.ex["B2"]) is None)
api, b = auto_bot(momentum_confirm_h=0.0)
legs_seen, bad_rest, nq = set(), [], []
for i in range(4):
    set_series(b, M.utcnow(), RISING)
    quiet(b.cycle)
    b.drain_writes(5)
    legs_seen |= set(b.mom_legs)
    for o in api.orders.values():
        if o["exchangeId"] in b.mom_legs:
            is_bid, _ = api.yes_view(o)
            if is_bid != (b.mom_legs[o["exchangeId"]]["q"] > 0):
                bad_rest.append((i, o["exchangeId"], is_bid))
    for e in b.mom_legs:
        if quiet(b.decide, b.ex[e], 0.12, dict(api.inv), b.effective_inventory(dict(api.inv)), False, 0.0,
                 time.monotonic()) != M.NO_QUOTE:
            nq.append(e)
check("full cycles (auto on): the sleeve bought", len(legs_seen) >= 2, legs_seen)
check("...no order of ours ever rests on a sleeve leg's reducing side (no ask over a YES leg, no bid under a NO leg)",
      not bad_rest, bad_rest[:4])
check("...the quoter never quotes a sleeve leg (decide: NO_QUOTE)", not nq, nq)
per = {}
for o in api.wire:
    if o.get("expirationDate") and o["exchangeId"] in legs_seen and o["action"] == "buy" and o["side"] == "yes":
        per[(o["exchangeId"], o["price"])] = per.get((o["exchangeId"], o["price"]), 0) + 1
check("...no duplicate orders across cycles: each momentum buy level sent once", per and all(v == 1 for v in
                                                                                             per.values()), per)
dup = {}
for o in api.orders.values():
    k = (o["exchangeId"],) + tuple(api.yes_view(o))
    dup[k] = dup.get(k, 0) + 1
check("...never two of our orders resting at one price", all(v == 1 for v in dup.values()),
      [k for k, v in dup.items() if v > 1])

# ============================================================================================ duplicates, RT13-3
print("--- no duplicate orders: a write in flight, an IOC still listed, a lagging positions read (RT13-3)")
api, b = forced_bot()
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
api, b = forced_bot(momentum_max_usd=300.0)
tick(b)
legs1 = {e: dict(v) for e, v in b.mom_legs.items()}
lagging = {e: q for e, q in api.inv.items() if e not in b.mom_legs}   # (the read before the buy: no B2)
n0 = len(api.wire)
fresh(b)
read(b)
quiet(b.mom_tick, M.utcnow(), lagging, b.orders_by_eid(M.utcnow()), None, set())
check("RT13-3: a positions read that lags the buy (no B2) within 120 s: the leg is kept, nothing re-bought",
      b.mom_legs == legs1 and len(api.wire) == n0, (b.mom_legs, wire(api)[n0:]))
for e in b.mom_legs:
    b.mom_last_trade[e] -= 200.0
quiet(b.mom_tick, M.utcnow(), lagging, b.orders_by_eid(M.utcnow()), None, set())
check("...after the 120-s grace a position really gone drops the leg (logged 'MOMENTUM leg ... gone')",
      "B2" not in b.mom_legs and any(m.startswith("MOMENTUM leg Dem Beta Senate gone") for m in CAP.lines), b.mom_legs)

# ============================================================================================ restart
print("--- restart: the automation, the series and the funding restored")
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
b.write_status(True)
b2 = M.Bot(api, b.cfg)
check("restart: auto state 'on', target, steps restored", b2.ma["state"] == "on"
      and abs(b2.ma["target_usd"] - b.ma["target_usd"]) < 1e-6 and b2.ma["steps"] == b.ma["steps"], b2.ma)
check("...the legs and their cost basis restored", {e: round(v["cost"], 2) for e, v in b2.mom_legs.items()}
      == {e: round(v["cost"], 2) for e, v in b.mom_legs.items()} and b2.mom_state == "active")
check("...the tilt series restored (no re-seed): the same bins and readings",
      sorted(b2.tslope.bins) == sorted(b.tslope.bins)
      and abs(b2.tslope.slope_24h(at_h(0).timestamp(), 24.0) - b.tslope.slope_24h(at_h(0).timestamp(), 24.0)) < 1e-9)
check("...funded_from restored", all(abs(b2.ma_funded[k] - b.ma_funded[k]) < 0.01 for k in ("cash", "reserve",
                                                                                             "value_sales")))
quiet(b2.ts_seed_step, time.time())
check("...a restored series is never re-seeded", (b2.ma_seed or {}).get("note") == "restored")
n_w = len(api.wire)
b2.ts_feed = lambda *a, **k: None
b2.ma_seed = {"done": True, "merged": True}
b2.tslope = b.tslope
fresh(b2)
read(b2)
for x in b2.ex.values():
    x.inv = float(api.inv.get(x.eid, 0.0))
quiet(b2.mom_tick, at_h(0) + timedelta(seconds=30), dict(api.inv), b2.orders_by_eid(M.utcnow()), None, set())
check("...the restarted bot at its target: no order sent again (no duplicate across the restart)",
      len(api.wire) == n_w and cost(b2) <= 10000 + 1, wire(api)[n_w:])

# ============================================================================================ status, summary, robust
print("--- status, summary, robustness")
st = b.mom_status()
check("status momentum {armed, on, size_usd, target_usd, max_usd, slope_24h, slope_6h, reason, funded_from, "
      "ev_given_up, ev_given_up_per_10k, kept_back, confirm_since, steps, next_step_at, series_bins, series_source}",
      all(k in st for k in ("armed", "on", "size_usd", "target_usd", "max_usd", "slope_24h", "slope_6h", "reason",
                            "funded_from", "ev_given_up", "ev_given_up_per_10k", "kept_back", "confirm_since",
                            "steps", "next_step_at", "series_bins", "series_source")) and st["on"] is True
      and st["armed"] is True, st)
summ = b.mom_summary()
check("2-hourly summary: ' | momentum on X/Yk, slope24 +a.b, slope6 +c.d'", "momentum on" in summ
      and "slope24 +3.0" in summ and "slope6 +3.0" in summ and "momentum" in b.summary_ops_line(100000.0), summ)
api, b = auto_bot()
tick(b)
check("summary while armed: 'momentum armed 0.0/0.0k, slope24 n/a'", "momentum armed 0.0/0.0k, slope24 n/a"
      in b.mom_summary(), b.mom_summary())
n0 = len(CAP.lines)
for kw in ({"books": {k: {"bids": [], "asks": []} for k in books_default()}}, {"refs": {}}, {}):
    api, b = mk_bot(momentum_enabled=True, momentum_auto=True, momentum_confirm_h=0.0, tilt_harvest_ladder=True,
                    alloc_enabled=True, state_max_usd=15000.0, **kw)
    for _ in range(3):
        quiet(b.cycle)
        b.drain_writes(5)
        b.write_status(True)
crash = [m for m in CAP.lines[n0:] if "tick failed" in m or "unexpected error" in m or "Traceback" in m]
check("nothing crashes with no history / no DB / empty books / no Polymarket (3 cycles each, auto + ladder + "
      "allocator + state cap on)", not crash, crash[:2])
with open(b.cfg.status_file) as f:
    sj = json.load(f)
check("...status.json momentum present, reason 'no history', armed", (sj.get("momentum") or {}).get("reason")
      == "no history" and (sj.get("momentum") or {}).get("armed") is True, (sj.get("momentum") or {}).get("reason"))
api, b = auto_bot(momentum_confirm_h=0.0)
b.cfg.momentum_slope_bin_h = 2.0
set_series(b, at_h(0), RISING)
b.ts_feed = M.Bot.ts_feed.__get__(b)
b.cfg.record_file = ""
quiet(b.ts_feed, at_h(0).timestamp(), time.monotonic())
check("momentum_slope_bin_h changed live: a new series of 2-h bins (re-seeded / restarted)", b.tslope.bin_s == 2 * H)
py310 = shutil.which("python3.10")
out = subprocess.run([py310 or sys.executable, "-m", "py_compile", os.path.join(ROOT, "mm_bot.py")],
                     capture_output=True, text=True)
check(f"{'python3.10' if py310 else 'this interpreter'} -m py_compile mm_bot.py", out.returncode == 0, out.stderr[-300:])

n, ok = len(RESULTS), sum(RESULTS)
print(f"\n{ok}/{n} passed")
sys.exit(0 if ok == n else 1)
