"""
Offline tests for Package 13 part C (the owner's 4 Oct 20:30 + 21:20 instructions; Config "Package 13 C"): the momentum
sleeve's AUTOMATION -
  settings and ranges; flags off identical to f61495e (the Package 13 A + B + red team + dry run head) on a grid; the
  forced sleeve (momentum_force) = the old momentum_enabled orders;
  TiltSlope: bins, the winsorised slope estimator, non-headline / liquid / spread / fresh-book / other-traders filter,
  the regression (slope_24h) and the 6-h change, seeding from a recorder-like sqlite (and the snap04 reading),
  restart restore;
  the trigger (slope_24h >= momentum_on_slope AND slope_6h > 0 held momentum_confirm_h; one burst never triggers;
  momentum_force / momentum_auto off / momentum_enabled alone);
  the ramp (momentum_start_usd, + momentum_step_usd per momentum_step_h of rising tilt, stalls, the cap);
  the funding order ((a) free cash, (b) the MM reserve while mm_carry_24h.per_day < mm_carry_min, (c) VALUE sales
  lowest edge first never below the floor; funded_from / ev_given_up; the allocator's pause, the ladder's holdback,
  the quoter's budget);
  the flip (slope_24h <= 0, the profit target, mom_exit_utc, momentum_exit, the kill), the latch and the re-arm;
  rule 5 (no fake buying); no duplicate orders across cycles; nothing crashes with no history / no DB / empty books.

Run:  python tests/test_p13c.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
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
BASE_REV = "f61495e"                              # Package 13 A + B + red team + dry run (the head before 13 C)
STAGED = os.path.join(ROOT, "deploy", "package13", "settings_override.aggressive.json")
SNAP_DB = "/home/claude/snap04/md.sqlite"


class Cap(logging.Handler):
    """Every log record's message (the crash checks read "tick failed" / "unexpected error")."""

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
# D Delta (D2 p 0.10 ask 0.13 -> D1's NO at 1 - 0.89 = 0.11); Ohio Rep "11" p 0.12 ask 0.18: MOMENTUM YES
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


FLAGS = ("buckets_enabled", "momentum_enabled", "aggressive_value", "momentum_auto", "momentum_force",
         "tilt_harvest_ladder")


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
    b.api.wire = []                               # (the checks read the orders sent after the warm-up)


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
    return quiet(b.p13_tick, at or M.utcnow(), dict(b.api.inv), b.orders_by_eid(M.utcnow()), None, set(skip))


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


def at_h(h):
    return NOW + timedelta(hours=h)


# ============================================================================================ settings
print("--- settings")
D = M.Config()
SPEC = {"momentum_auto": (False, (False, True)), "momentum_force": (False, (False, True)),
        "momentum_slope_hours": (24.0, (6.0, 72.0)), "momentum_slope_bin_h": (4.0, (1.0, 12.0)),
        "momentum_slope_max_spread": (0.06, (0.01, 0.2)), "momentum_on_slope": (0.5, (0.0, 10.0)),
        "momentum_confirm_h": (4.0, (0.0, 48.0)), "momentum_start_usd": (10000.0, (0.0, 100000.0)),
        "momentum_step_usd": (10000.0, (0.0, 100000.0)), "momentum_step_h": (6.0, (1.0, 48.0)),
        "mm_carry_min": (300.0, (0.0, 10000.0)), "momentum_profit_target": (0.25, (0.0, 5.0)),
        "momentum_fund_value": (True, (False, True)), "momentum_fund_floor": (True, (False, True)),
        "momentum_rearm_h": (24.0, (0.0, 168.0))}
for k, (dflt, rng) in SPEC.items():
    ok_d = getattr(D, k) == dflt and type(getattr(D, k)) is type(dflt)
    ok_r = M.OVERRIDABLE.get(k) == rng
    if isinstance(dflt, bool):
        g1, b1 = M.validate_overrides({k: not dflt}, D)
        g2, b2 = M.validate_overrides({k: 1}, D)
        ok_v = g1 == {k: not dflt} and not b1 and not g2 and b2
    else:
        lo, hi = rng
        g1, b1 = M.validate_overrides({k: dflt}, D)
        g2, b2 = M.validate_overrides({k: lo - 1e-6}, D)
        g3, b3 = M.validate_overrides({k: hi + 1e-6}, D)
        g4, b4 = M.validate_overrides({k: hi}, D)
        ok_v = g1 == {k: dflt} and not g2 and b2 and not g3 and b3 and g4 == {k: hi}
    check(f"{k}: default {dflt!r}, range {rng}, validates in range / refuses outside", ok_d and ok_r and ok_v,
          (getattr(D, k), M.OVERRIDABLE.get(k)))
_f = list(M.Config.__dataclass_fields__)
check("one contiguous block after election_take_max_price (the end of Config)",
      _f[_f.index("election_take_max_price") + 1:] == list(SPEC), _f[_f.index("election_take_max_price") + 1:][:4])
raw = json.load(open(STAGED))
good, bad = M.validate_overrides(raw, M.Config())
check("staged file validates whole; momentum_auto on, momentum_force off, momentum_enabled on, fund floor kept",
      not bad and len(good) == len(raw) and raw.get("momentum_auto") is True and raw.get("momentum_force") is False
      and raw.get("momentum_enabled") is True and raw.get("momentum_fund_floor") is True, bad)
check("staged file: every Package 13 C setting explicit at its default (but momentum_auto)",
      all(raw.get(k) == (True if k == "momentum_auto" else d) for k, (d, _) in SPEC.items()),
      {k: raw.get(k) for k in SPEC})
check("staged file: harvest ladder, aggressive value, buckets, election night on; close override 17:00 4 Nov",
      all(raw.get(k) is True for k in ("tilt_harvest_ladder", "aggressive_value", "buckets_enabled", "election_night"))
      and raw.get("close_override_utc") == "2026-11-04T17:00:00Z")
check("wire_order strips the funding note _mom_fund",
      M.wire_order({"exchangeId": "x", "side": "yes", "action": "sell", "price": 0.5, "quantity": 1,
                    "_mom_fund": True}) == {"exchangeId": "x", "side": "yes", "action": "sell", "price": 0.5,
                                            "quantity": 1})

# ============================================================================================ flags off = base
print(f"--- flags off identical to {BASE_REV} on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_base13c.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_base13c", path)
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


def wires(api):
    return sorted(json.dumps({k: v for k, v in o.items() if k != "expirationDate"}, sort_keys=True)
                  for o in getattr(api, "wire", []))


if base is not None:
    ok_w, ok_q, ok_s, ok_h, diffs = True, True, True, True, []
    grid = [({}, 50000.0, None, {}),
            ({"A2": -3000, "G2": -10000, "B2": 2000}, 20000.0, None, {"value_mode": True}),
            ({"21": 60000, "22": -5000}, 5000.0, None, {"alloc_enabled": True, "alloc_min_edge_buy": 0.05}),
            ({"D1": -2000, "11": 900}, 0.0, None, {"bloc_delta_enabled": True, "worst_case_backstop_frac": 1.0}),
            ({"G1": 5000, "B1": 4000}, 50000.0, {**books_default(), "B2": bk(0.13, None)},
             {"value_mode": True, "value_quote_hurdle": 0.05, "take_enabled": True, "tilt_harvest_ladder": True,
              "election_night": True})]
    for inv, cash, books, kw in grid:
        api_n, bn, api_b, bb_ = twin(inv, cash, books, **kw)
        for _ in range(2):
            quiet(bn.cycle)
            quiet(bb_.cycle)
            bn.drain_writes(5)
            bb_.drain_writes(5)
        if wires(api_n) != wires(api_b):
            ok_w = False
            diffs.append(("wire", inv))
        if {e: tuple(x.quote.__dict__.values()) for e, x in bn.ex.items()} != \
                {e: tuple(x.quote.__dict__.values()) for e, x in bb_.ex.items()}:
            ok_q = False
            diffs.append(("quote", inv))
        bn.write_status(True)
        bb_.write_status(True)
        with open(bn.cfg.status_file) as f:
            kn = json.load(f)
        with open(bb_.cfg.status_file) as f:
            kb = json.load(f)
        ign = set(getattr(M.Bot, "EV_KEYS", ())) | {"ev_outcome_history", "updated", "seconds_since_cycle",
                                                     "last_cycle_seconds", "last_cycle_phases", "requests_last_min",
                                                     "alloc", "tilt_state", "harvest", "election"}
        if set(kn) != set(kb):
            ok_s = False
            diffs.append(("status keys", set(kn) ^ set(kb)))
        vn = {k: v for k, v in kn.items() if k not in ign}
        vb = {k: v for k, v in kb.items() if k not in ign}
        if json.dumps(vn, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
            ok_h = False
            diffs.append(("status values", [k for k in vn if vn.get(k) != vb.get(k)]))
    check("flags off: two full cycles send the same orders (5 books / positions / settings, P13 B flags in one)", ok_w,
          diffs[:1])
    check("flags off: every market's quote identical", ok_q, diffs[:1])
    check("flags off: status.json keys identical (no momentum key while every momentum flag is off)", ok_s,
          [d for d in diffs if d[0] == "status keys"][:1])
    check("flags off: status.json values identical (but timings and the ev / carry report keys)", ok_h,
          [d for d in diffs if d[0] == "status values"][:1])
    # the forced sleeve = the old momentum_enabled sleeve, order for order
    api_n, bn, api_b, bb_ = twin({}, 50000.0, momentum_enabled=True, momentum_force=True)
    for _ in range(3):
        quiet(bn.cycle)
        quiet(bb_.cycle)
        bn.drain_writes(5)
        bb_.drain_writes(5)
    check(f"momentum_force + momentum_enabled = {BASE_REV}'s momentum_enabled: the same orders over 3 cycles",
          wires(api_n) == wires(api_b) and any('"price": 0.15' in w and '"B2"' in w for w in wires(api_n)),
          (len(wires(api_n)), len(wires(api_b))))

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
check("slope_24h = the regression slope of the 6 bin values over 24 h in points a day (the owner's reading: +2.29)",
      s24 is not None and abs(s24 - 2.2920) < 0.001, s24)
check("slope_6h = (latest - the bin 6 h back; a 4 / 8 h tie goes to the later one) / distance: (12.82 - 12.75) / 4 h = "
      "+0.42", s6 is not None and abs(s6 - 0.42) < 0.001, s6)
check("slope_24h None with 2 bins (needs >= 3 spanning half the window)",
      M.TiltSlope.from_dict(None, BIN).slope_24h(t0, 24.0) is None)
ts6 = M.TiltSlope(BIN)
for k in (t0 - BIN, t0):
    ts6.bins[k] = [0.1, 1.0, 50, k, k + 0.9 * BIN]
check("slope_24h None with too few bins; slope_6h with a 4-h neighbour", ts6.slope_24h(t0 + H, 24.0) is None
      and ts6.slope_6h(t0 + H) is not None and abs(ts6.slope_6h(t0 + H)) < 1e-9)
check("slope_6h None when the latest bin is older than two bins (a stale series)", ts5.slope_6h(t0 + 3 * BIN) is None)
d = ts5.to_dict()
r5 = M.TiltSlope.from_dict(json.loads(json.dumps(d)), BIN)
check("to_dict / from_dict round trip (status.json): the same readings", abs(r5.slope_24h(t0 + 3 * H, 24.0) - s24)
      < 1e-9 and abs(r5.slope_6h(t0 + 3 * H) - s6) < 1e-9)
check("from_dict with another bin width or garbage: an empty series",
      not M.TiltSlope.from_dict(d, 2 * H).bins and not M.TiltSlope.from_dict({"bin_s": BIN, "bins": [["x"]]}, BIN).bins)

# ============================================================================================ the live feed
print("--- TiltSlope's live feed: liquid, non-headline, other traders' touch, spread, fresh book")
HL = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate", "H": "U.S. Senate"}
api, b = mk_bot(races=HL, books={**books_default(), "H1": bk(0.50, 0.52), "H2": bk(0.48, 0.50),
                                 "G1": bk(0.60, 0.70)},
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
check("headline legs (U.S. Senate) never sampled", not any(abs(r - 0.60) < 1e-9 or abs(r - 0.40) < 1e-9
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
own = {o.eid: o for o in b.my_orders.values()}
e_q = next((e for e, o in own.items() if e in b.ex and b.ex[e].book and b.ex[e].book.get("bids")), None)
check("strip-own: the books the feed reads are other traders' only (our resting orders stripped on download)",
      e_q is not None and all(not any(abs(l["price"] - o.price) < 1e-9 and abs(l["quantity"] - o.qty) < 1e-9
                                      and l["price"] > b.api.books[o.eid]["bids"][0]["price"] + 1e-9
                                      for l in (b.ex[o.eid].book or {}).get("bids") or [])
                              for o in b.my_orders.values() if o.is_bid), e_q)
n0 = len(b.tslope.bins)
b.tslope.last_t = time.time()
quiet(b.ts_feed, time.time() + 5, time.monotonic())
check("one sample a minute at most (TILT_SLOPE_SAMPLE_S)", b.tslope.last_t < time.time() + 1)

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
seed_db(dbp, cur - 5 * BIN, [10.0, 10.5, 11.0, 11.5, 12.0, 12.5])
bins = M.tilt_slope_seed(dbp, "live", now_w - 72 * H, now_w + 1, BIN, ("U.S. House", "U.S. Senate"), 0.06)
tsx = M.TiltSlope(BIN)
tsx.merge(bins)
vv = [round(100 * v, 6) for _, v in tsx.values(cur + 3 * H)]
check("seeded bins = the rows' tilt (10.0 .. 12.5): headline, own-at-the-touch, wide-spread and dry rows ignored",
      vv == [10.0, 10.5, 11.0, 11.5, 12.0, 12.5], vv)
check("...slope_24h +3.00, slope_6h +3.00 points a day", abs(tsx.slope_24h(cur + 3 * H, 24.0) - 3.0) < 1e-6
      and abs(tsx.slope_6h(cur + 3 * H) - 3.0) < 1e-6, (tsx.slope_24h(cur + 3 * H, 24.0), tsx.slope_6h(cur + 3 * H)))
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
tick(b)
st = b.mom_status()
check("status momentum.series_bins / series_source after the seed", len(st.get("series_bins") or []) >= 6
      and st.get("series_source") in ("seed", "seed+live"), (st.get("series_source"), len(st.get("series_bins") or [])))
api, b = mk_bot(momentum_enabled=True, momentum_auto=True)
warm(b)
tick(b)
check("no recorder (record_file ''): no seed, the reason says 'no history' (no crash)",
      (b.ma_seed or {}).get("note") == "no recorder file" and b.ma["reason"] == "no history", (b.ma_seed, b.ma["reason"]))
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
    check("snap04 (md.sqlite, 4 Oct 15:57 UTC): bins 10.69 / 11.92 / 12.25 / 12.48 / 12.75 / 12.82 (the owner's)",
          last6 == [10.69, 11.92, 12.25, 12.48, 12.75, 12.82], last6)
    check("snap04: slope_24h ~ +2.3, slope_6h ~ +0.4 points a day (the owner's reading)",
          abs(s24s - 2.3) < 0.05 and abs(s6s - 0.42) < 0.03, (s24s, s6s))
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
check("momentum_force: buys with no trigger at all (falling tilt), to bucket_mom_frac x account",
      wire(api) and b.mom_buy_target(b.last_equity) == 0.4 * b.last_equity
      and b.ma["reason"] == "forced on (momentum_force)", (wire(api), b.ma["reason"]))
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
check("forced OFF (momentum_auto false) while on: 'off', ALERT, no further buys", on0 == "on" and b.ma["state"] == "off"
      and any("switched OFF" in a for a in ALERTS) and not b.mom_may_buy(), (on0, b.ma["state"], ALERTS))

# ============================================================================================ the ramp
print("--- the ramp: start 10k, +10k per 6 h of rising tilt, stalls on slope_6h <= 0, capped")
DEEP = {**books_default(), "B2": bk(0.13, 0.15, q=200000), "D2": bk(0.11, 0.13, q=200000),
        "D1": bk(0.89, 0.91, q=200000)}
api, b = auto_bot(momentum_confirm_h=0.0, books=DEEP, aggr_max_market_usd=100000.0, aggr_max_race_usd=100000.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
cost = sum(v["cost"] for v in b.mom_legs.values())
check("on: buys up to the 10k target, not 40% at once", b.ma["state"] == "on" and 9000 <= cost <= 10000 + 1, cost)
for h in (1, 2, 3, 4, 5):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("5 h of rising tilt: no step yet (target 10k), no buy beyond it",
      abs(b.ma["target_usd"] - 10000) < 1e-6 and sum(v["cost"] for v in b.mom_legs.values()) <= 10000 + 1)
set_series(b, at_h(6), RISING)
tick(b, at_h(6))
cost = sum(v["cost"] for v in b.mom_legs.values())
check("6 h of rising tilt: +10k (target 20k, step 1), bought up to it", abs(b.ma["target_usd"] - 20000) < 1e-6
      and b.ma["steps"] == 1 and 19000 <= cost <= 20000 + 1, (b.ma["target_usd"], cost))
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
check("capped at bucket_mom_frac x account (40% of 100k = 40k)", abs(b.ma["target_usd"] - 40000) < 1e-6,
      b.ma["target_usd"])
api, b = auto_bot(momentum_confirm_h=0.0, bucket_mom_frac=0.05)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
check("a cap below momentum_start_usd (5% of 100k): the target starts at the cap", abs(b.ma["target_usd"] - 5000)
      < 1e-6, b.ma["target_usd"])

# ============================================================================================ funding
print("--- funding: (a) free cash above the reserve, (b) the MM reserve if mm_carry_24h is low, (c) VALUE sales")


def fund_bot(cash, per_day="none", **kw):
    args = dict(momentum_confirm_h=0.0, books=DEEP, cash=cash, aggr_max_market_usd=100000.0,
                aggr_max_race_usd=100000.0)
    args.update(kw)
    api_, b_ = auto_bot(**args)
    if per_day != "none":
        b_.ops_last = {"mm_carry_24h": {"per_day": per_day}}
    set_series(b_, at_h(0), RISING)
    tick(b_, at_h(0))
    return api_, b_


api, b = fund_bot(5000.0)
cost = sum(v["cost"] for v in b.mom_legs.values())
check("(a) cash 5,000, reserve 1,000: the sleeve spends the 4,000 above it, no more", 3900 <= cost <= 4000 + 1, cost)
check("...funded_from cash ~4,000, reserve 0", abs(b.ma_funded["cash"] - cost) < 1 and b.ma_funded["reserve"] == 0,
      b.ma_funded)
api, b = fund_bot(5000.0, per_day=100.0)
cost = sum(v["cost"] for v in b.mom_legs.values())
check("(b) mm_carry_24h 100/day < mm_carry_min 300: the MM reserve funds it too (~5,000 spent)",
      4900 <= cost <= 5000 + 1, cost)
check("...funded_from: cash ~4,000 then reserve ~1,000", abs(b.ma_funded["cash"] - 4000) < 160
      and abs(b.ma_funded["reserve"] - (cost - b.ma_funded["cash"])) < 1e-6 and b.ma_funded["reserve"] > 800,
      b.ma_funded)
api, b = fund_bot(5000.0, per_day=500.0)
check("(b) not when mm_carry_24h 500/day >= 300", sum(v["cost"] for v in b.mom_legs.values()) <= 4000 + 1)
api, b = fund_bot(5000.0, per_day=None)
check("(b) not when mm_carry_24h is unknown (None)", sum(v["cost"] for v in b.mom_legs.values()) <= 4000 + 1)
# (c) VALUE sales for the shortfall: G1 long 3000 (p 0.80, bid 0.81: at / above the floor), A2 short 3000 (p 0.02:
# the buy-back at 0.08 is above p + margin: the floor refuses it)
VB = {**DEEP, "G1": bk(0.81, 0.83)}
api, b = fund_bot(1500.0, inv={"G1": 3000, "A2": -3000}, books=VB)
sold = [x for x in wire(api) if x[0] in ("G1", "A2")]
check("(c) cash short: VALUE sold for the shortfall, lowest edge-held first (G1 at its bid 0.81)",
      sold and sold[0][:4] == ("G1", "yes", "sell", 0.81), sold)
check("...never below the value floor: A2's buy-back at 0.08 (p 0.02 + 0.005) refused, not sent",
      not any(x[0] == "A2" for x in sold) and "A2" in b.ma_fund_refused, (sold, b.ma_fund_refused))
g_sold = sum(x[4] for x in sold if x[0] == "G1")
check("...funded_from.value_sales = the proceeds (shares x 0.81)", abs(b.ma_funded["value_sales"] - g_sold * 0.81) < 0.01,
      (b.ma_funded, g_sold))
check("...ev_given_up = shares x (p - price) = shares x (0.80 - 0.81) (a gain here)",
      abs(b.ma_ev_given_up - g_sold * (0.80 - 0.81)) < 1e-6, b.ma_ev_given_up)
st = b.mom_status()
check("...status ev_given_up_per_10k = ev / (value_sales / 10,000)", st["ev_given_up_per_10k"] is not None
      and abs(st["ev_given_up_per_10k"] - b.ma_ev_given_up / (b.ma_funded["value_sales"] / 10000)) < 0.01, st)
check("...the funding order is tagged (notes mom_fund) and goes out as an IOC", any(m.get("mom_fund")
                                                                                    for m in b.order_meta.values()))
n_w = len(wire(api))
set_series(b, at_h(0.02), RISING)
tick(b, at_h(0.02))
check("...the next cycle (proceeds not yet in a cash read): no second funding plan within 10 min, no re-sale",
      not [x for x in wire(api)[n_w:] if x[0] in ("G1", "A2")], wire(api)[n_w:])
api.cash += b.ma_funded["value_sales"]
n_w = len(wire(api))
c0 = sum(v["cost"] for v in b.mom_legs.values())
set_series(b, at_h(0.05), RISING)
tick(b, at_h(0.05))
spent = sum(v["cost"] for v in b.mom_legs.values()) - c0
check("...the proceeds in cash: the sleeve buys with them, attributed to value_sales_spent first",
      spent > 1000 and b.ma_funded.get("value_sales_spent", 0) >= min(spent, b.ma_funded["value_sales"]) - 1,
      (spent, b.ma_funded))
api, b = fund_bot(1500.0, inv={"G1": 3000, "A2": -3000}, books=VB, momentum_fund_floor=False)
check("momentum_fund_floor False: the floor exemption applies (A2 bought back too, tagged _bucket_sell)",
      any(x[0] == "A2" for x in wire(api)) and any(m.get("mom_fund") and m.get("bucket_sell")
                                                   for m in b.order_meta.values()), wire(api))
api, b = fund_bot(1500.0, inv={"G1": 3000}, books=VB, momentum_fund_value=False)
check("momentum_fund_value False: no VALUE sale at all", not any(x[0] == "G1" for x in wire(api)))
# the others leave the shortfall alone while the round is open
api, b = fund_bot(1500.0)
hold = b.ma_hold
check("a round open with a shortfall and candidates: ma_hold = the shortfall (status hold_usd)",
      hold > 5000 and abs(b.mom_status()["hold_usd"] - hold) < 0.01, hold)
pr = {"buy": {"eid": "21", "short": False, "px": 0.56, "label": "Rep Utah Senate"}, "sold_at": -1e18,
      "status": "sold"}
b.alloc_run = {}
r_ = quiet(b.alloc_buy, pr, dict(api.inv), b.orders_by_eid(M.utcnow()), time.monotonic(), time.time(), set())
check("...the allocator's spare-cash buy pauses (blocked_by momentum)", r_ is False
      and b.alloc_run.get("blocked_by", {}).get("momentum") == 1, b.alloc_run)
pr2 = dict(pr, sold_at=time.monotonic() - 5)
b.alloc_run = {}
quiet(b.alloc_buy, pr2, dict(api.inv), b.orders_by_eid(M.utcnow()), time.monotonic(), time.time(), set())
check("...a sold pair's buy (its own sale's cash) is not paused", not b.alloc_run.get("blocked_by", {}).get("momentum"))
keeps = []
b.place_orders_keep = lambda orders, keep: keeps.append(keep) or [{"index": k, "ok": False, "cash_gated": True}
                                                                    for k in range(len(orders))]
b.cfg.tilt_harvest_ladder = True
b.cfg.harvest_min_edge = 0.0
quiet(b.hv_tick, M.utcnow(), dict(api.inv), b.orders_by_eid(M.utcnow()), None, set())
check("...the harvest ladder keeps alloc_mm_reserve + the shortfall back (place_orders_keep)",
      keeps and all(abs(k - (b.cfg.alloc_mm_reserve + hold)) < 1e-6 for k in keeps), (keeps[:2], hold))
check("...and yields the candidates' races (B / D / Ohio: 'momentum')",
      all(b.hv_plan(dict(api.inv), time.monotonic())[1].get(e) == "momentum" for e in ("B1", "B2", "D1", "12")),
      {e: b.hv_plan(dict(api.inv), time.monotonic())[1].get(e) for e in ("B1", "B2", "D1", "12")})
api, b = fund_bot(1500.0)
seen = {}
orig_setup = b.ladder_setup


def spy(*a, **k):
    seen.update(plan=b.cg_plan_left, cash=b.cash_left(), hold=b.ma_hold, hb=b.el_holdback_left())
    return orig_setup(*a, **k)


b.ladder_setup = spy
set_series(b, M.utcnow(), RISING)
quiet(b.cycle)
b.drain_writes(5)
check("...the quoter's plan budget leaves the shortfall (cash left - holdback - ma_hold)",
      seen and seen["hold"] > 0 and abs(seen["plan"] - max(0.0, seen["cash"] - seen["hb"] - seen["hold"])) < 1e-6,
      seen)

# ============================================================================================ flips
print("--- the flip: slope_24h <= 0, the profit target, the date, the manual exit, the kill")
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
      and b.mom_status()["auto_state"] == "flipped" and "24-h slope" in b.mom_status()["reason"], b.mom_status()["reason"])
api, b = auto_bot(momentum_confirm_h=0.0, momentum_force=True)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
set_series(b, at_h(1), FALLING)
tick(b, at_h(1))
check("momentum_force: a slope_24h <= 0 does not flip it (the owner forced it on)", b.mom_state == "active"
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
      and any(x[2] == "buy" or x[2] == "sell" for x in wire(api)[n_w:]), (b.ma["state"], ALERTS))
api, b = auto_bot(momentum_confirm_h=0.0, mom_exit_hours=0.25, momentum_rearm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
for h in (1, 1.2, 1.4, 1.6):
    set_series(b, at_h(h), FALLING)
    tick(b, at_h(h))
for h in (2, 30, 80):
    set_series(b, at_h(h), RISING)
    tick(b, at_h(h))
check("momentum_rearm_h 0: a flipped sleeve never re-arms by itself", b.ma["state"] == "flipped"
      and b.mom_state == "exited", b.ma)
b.cfg.momentum_enabled = False
tick(b, at_h(81))
b.cfg.momentum_enabled = True
set_series(b, at_h(82), RISING)
tick(b, at_h(82))
check("...only the manual latch reset (momentum_enabled false then true)", b.ma["state"] in ("armed", "on")
      and b.mom_state == "active", (b.ma, b.mom_state))

# ============================================================================================ rule 5
print("--- rule 5: no fake buying")
api, b = auto_bot(momentum_confirm_h=0.0)
b.hv_sides = {"B1": "bid"}
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
check("harvest levels in the race (B1 bids): B2 is NOT bought (the candidate counted before rule 5)",
      not any(x[0] in ("B1", "B2") for x in wire(api)) and "B2" in b.ma_cand_raw, (wire(api), b.ma_cand_raw))
api, b = auto_bot(momentum_confirm_h=0.0)
b.bk_sell = [{"eid": "11", "label": "Rep Ohio Senate", "kind": "long", "edge": 0.0, "usd_left": 100.0}]
check("a sell-down planned on the market: not bought (mom_sell_planned)", b.mom_sell_planned(b.ex["11"]) == "sell-down")
b.bk_sell = []
b.alloc_pairs = [{"status": "pending", "sell": {"eid": "B2"}, "buy": None}]
check("an allocator sale pending there: not bought", b.mom_sell_planned(b.ex["B2"]) == "allocator")
b.alloc_pairs = []
check("nothing planned / resting: may buy", b.mom_sell_planned(b.ex["B2"]) is None)
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, M.utcnow(), RISING)
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

# ============================================================================================ restart
print("--- restart: the automation, the series and the funding restored")
api, b = auto_bot(momentum_confirm_h=0.0)
set_series(b, at_h(0), RISING)
tick(b, at_h(0))
b.write_status(True)
b2 = M.Bot(api, b.cfg)
check("restart: auto state 'on', target, steps restored", b2.ma["state"] == "on"
      and abs(b2.ma["target_usd"] - b.ma["target_usd"]) < 1e-6 and b2.ma["steps"] == b.ma["steps"], b2.ma)
check("...the tilt series restored (no re-seed): the same bins and readings",
      sorted(b2.tslope.bins) == sorted(b.tslope.bins)
      and abs(b2.tslope.slope_24h(at_h(0).timestamp(), 24.0) - b.tslope.slope_24h(at_h(0).timestamp(), 24.0)) < 1e-9)
check("...funded_from restored", all(abs(b2.ma_funded[k] - b.ma_funded[k]) < 0.01 for k in ("cash", "reserve",
                                                                                             "value_sales")))
quiet(b2.ts_seed_step, time.time())
check("...a restored series is never re-seeded", (b2.ma_seed or {}).get("note") == "restored")

# ============================================================================================ status, summary, robustness
print("--- status, summary, robustness")
st = b.mom_status()
check("status momentum {armed, on, size_usd, target_usd, slope_24h, slope_6h, reason, funded_from, ev_given_up, "
      "ev_given_up_per_10k, confirm_since, steps, next_step_at, series_bins, series_source}",
      all(k in st for k in ("armed", "on", "size_usd", "target_usd", "slope_24h", "slope_6h", "reason", "funded_from",
                            "ev_given_up", "ev_given_up_per_10k", "confirm_since", "steps", "next_step_at",
                            "series_bins", "series_source")) and st["on"] is True and st["armed"] is True, st)
summ = b.p13_summary()
check("2-hourly summary: ' | momentum on X/Yk, slope24 +a.b, slope6 +c.d'", "momentum on" in summ
      and "slope24 +3.0" in summ and "slope6 +3.0" in summ, summ)
api, b = auto_bot()
check("summary while armed: 'momentum armed 0.0/0.0k, slope24 n/a'", "momentum armed 0.0/0.0k, slope24 n/a"
      in (tick(b) or b.p13_summary()), b.p13_summary())
n0 = len(CAP.lines)
for kw in ({"books": {k: {"bids": [], "asks": []} for k in books_default()}}, {"refs": {}}, {}):
    api, b = mk_bot(momentum_enabled=True, momentum_auto=True, momentum_confirm_h=0.0, buckets_enabled=True,
                    tilt_harvest_ladder=True, aggressive_value=True, **kw)
    for _ in range(3):
        quiet(b.cycle)
        b.drain_writes(5)
        b.write_status(True)
crash = [m for m in CAP.lines[n0:] if "tick failed" in m or "unexpected error" in m or "Traceback" in m]
check("nothing crashes with no history / no DB / empty books / no Polymarket (3 cycles each, auto on)", not crash,
      crash[:2])
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

n, ok = len(RESULTS), sum(RESULTS)
print(f"\n{ok}/{n} passed")
sys.exit(0 if ok == n else 1)
