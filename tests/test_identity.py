"""
Identity: the current mm_bot.py makes the SAME decisions as a pinned revision on the LIVE settings.

The pinned revision is the code the server runs (IDENTITY_BASE, default d9220c1 = Package 16). The live settings
are IDENTITY_SETTINGS (default deploy/package16/settings_override.momentum_armed.json). Both modules are given the
same fake exchange, the same books, positions, cash and fills, and run four cycles; their order streams, quotes,
order notes, status.json (minus wall-clock fields), health keys, status line and 2-hourly summary line must match.
Settings the pinned revision knows but the current code no longer has must be at their default in the live file
(else the comparison would be meaningless): that is checked and reported.

This is the proof used before every deploy of a simplified bot: with the retired layers' flags off in the live
file, the new code must send exactly the orders the old code sends.

Run:  python tests/test_identity.py             (exit code 0 = identical)
      IDENTITY_BASE=<rev> IDENTITY_SETTINGS=<file> python tests/test_identity.py
"""
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import astuple
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
logging.disable(logging.CRITICAL)
M.alert, M.notify = (lambda msg: None), (lambda *a, **k: False)
BASE_REV = os.environ.get("IDENTITY_BASE", "d9220c1")
SETTINGS = os.environ.get("IDENTITY_SETTINGS",
                          os.path.join(ROOT, "deploy", "package16", "settings_override.momentum_armed.json"))
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(extra)[:600]}]" if not cond else ""))
    RESULTS.append(bool(cond))


# ------------------------------------------------------------------------------------------ the pinned module
base = None
try:
    out = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if out.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_identity_base.py")
        with open(path, "w") as f:
            f.write(out.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_identity_base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = (lambda msg: None), (lambda *a, **k: False)
except Exception as e:
    print("    (base module unavailable:", e, ")")
check(f"pinned module loaded (git show {BASE_REV}:mm_bot.py)", base is not None)

# ------------------------------------------------------------------------------------------ the live settings
with open(SETTINGS) as f:
    LIVE = json.load(f)
good_new, bad_new = M.validate_overrides(LIVE, M.Config())
check(f"live settings accepted by the current code ({len(good_new)} of {len(LIVE)} keys)", not bad_new, bad_new)
if base is not None:
    good_old, bad_old = base.validate_overrides(LIVE, base.Config())
    check(f"live settings accepted by {BASE_REV} ({len(good_old)} of {len(LIVE)} keys)", not bad_old, bad_old)
    gone = [k for k in base.Config.__dataclass_fields__ if k not in M.Config.__dataclass_fields__]
    non_default = [k for k in gone if k in LIVE and LIVE[k] != getattr(base.Config(), k)]
    check(f"settings removed from the current code ({len(gone)}) are all at their default in the live file",
          not non_default, non_default)

# ------------------------------------------------------------------------------------------ a small world
NOW = M.utcnow()
CLOSE = NOW + timedelta(days=30)
RACES = {"A": "Alpha Senate", "B": "Beta Senate", "G": "Gamma Senate", "D": "Delta Senate",
         "R": "Rhode Island Senate"}
REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45,
        "Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02,
        "Beta Senate|Republican": 0.88, "Beta Senate|Democratic": 0.12,
        "Gamma Senate|Republican": 0.80, "Gamma Senate|Democratic": 0.20,
        "Delta Senate|Republican": 0.90, "Delta Senate|Democratic": 0.10,
        "Rhode Island Senate|Republican": 0.10, "Rhode Island Senate|Democratic": 0.90}


def bk(bid, ask, q=2000):
    return {"bids": [lvl(bid, q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


def books():
    return {"11": bk(0.10, 0.18, 1000), "12": bk(0.82, 0.90, 1000), "21": bk(0.48, 0.56, 1000),
            "22": bk(0.44, 0.52, 1000), "A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08), "B1": bk(0.85, 0.87),
            "B2": bk(0.13, 0.15), "G1": bk(0.66, 0.70), "G2": bk(0.30, 0.32), "D1": bk(0.89, 0.91),
            "D2": bk(0.11, 0.13), "R1": bk(0.11, 0.12), "R2": bk(0.86, 0.88)}


def mk(mod, inv, cash, overrides):
    """A bot of module `mod` on the shared world, the live file applied, then `overrides` (test knobs)."""
    mkts = []
    for k, race in RACES.items():
        mkts += [market(f"m{k}1", f"{k}1", "Republican", race), market(f"m{k}2", f"{k}2", "Democratic", race)]
    api, b = make_bot(live=True, books=books(), extra_markets=tuple(mkts))
    if mod is not M:                              # the pinned module gets its own Bot on the same fake exchange
        cfg = mod.Config()
        for k in cfg.__dataclass_fields__:
            if k in b.cfg.__dataclass_fields__:
                setattr(cfg, k, getattr(b.cfg, k))
        d = tempfile.mkdtemp()
        for k in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file",
                  "overrides_file", "market_edge_file", "handover_file"):
            if k in cfg.__dataclass_fields__:
                setattr(cfg, k, os.path.join(d, os.path.basename(getattr(cfg, k))))
        api2 = FakeApi(True)
        api2.markets_list = list(api.markets_list)
        api2.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                      for e, v in api.books.items()}
        api, b = api2, mod.Bot(api2, cfg)
    b.t["endDate"] = M.iso(CLOSE)
    for x in b.ex.values():
        x.close = CLOSE
    b.refs = FakeRefs(dict(REFS))
    good, _ = mod.validate_overrides(LIVE, b.cfg)
    for k, v in good.items():
        setattr(b.cfg, k, v)
    c = b.cfg
    c.selftest_enabled = False
    c.reserved_cash_mode = "ignore"
    for k, v in overrides.items():
        if k in c.__dataclass_fields__:
            setattr(c, k, v)
    api.inv.update(inv)
    api.cash, api.equity = cash, 100000.0
    api.pnl = (lambda a: lambda: (a.log("pnl"), {"totalAccountValue": a.equity, "cashBalance": a.cash})[1])(api)
    return api, b


def run(mod, inv, cash, overrides, fills):
    api, b = mk(mod, inv, cash, overrides)
    for n in range(4):
        if fills and n in (2, 3):
            for e, side in (("21", True), ("22", False), ("B2", False), ("A2", True), ("R1", True)):
                api.fill(e, side, 40)
        b.cycle()
        b.drain_writes(5)
    b.write_status(True)
    with open(b.cfg.status_file) as f:
        status = json.load(f)
    return api, b, status


VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase"}
WALL = {"last_run_wall", "last_run", "since", "on_since", "confirm_since", "exit_started_wall", "last_t",
        "below_half_since", "below_half_since_wall"}


def cut(s):
    res = {}
    for k, v in s.items():
        if k in VOLATILE or k.endswith("_seconds"):
            continue
        if isinstance(v, dict):
            v = {a: ([f[1:] for f in x] if a == "flows" else x) for a, x in v.items() if a not in WALL}
            if k == "mm_funding" and isinstance(v.get("lots"), dict):
                v = {**v, "lots": {e: [x[:2] for x in ls] for e, ls in v["lots"].items()}}
            if k == "momentum":
                v = {a: x for a, x in v.items() if a not in ("slope", "series_bins", "auto")}
            if "events" in v:                     # [[wall, kind, ...], ...]: drop the wall-clock stamp
                v = {**v, "events": [x[1:] for x in v["events"]]}
            if k == "alloc" and isinstance(v.get("swaps"), dict):
                v = {**v, "swaps": {a: x for a, x in v["swaps"].items() if a != "events"}}
        res[k] = v
    return res


def strip(w):
    return sorted(json.dumps({k: v for k, v in o.items() if k != "expirationDate"}, sort_keys=True) for o in w)


def notes(b):
    return sorted(json.dumps({a: v for a, v in x.items() if a != "t"}, sort_keys=True)
                  for x in b.order_meta.values())


# ------------------------------------------------------------------------------------------ the comparison
if base is not None:
    WORLDS = (({}, 50000.0, {}, False),
              ({"21": 600, "R1": -900, "G1": 3000}, 20000.0, {}, True),
              ({"22": -800, "12": 2500, "A2": -500, "B2": 300}, 3000.0, {}, True),
              ({"21": 600, "R1": -900, "G1": 3000, "A2": -2000}, 60000.0, {"alloc_min_edge_buy": 0.02}, True))
    diffs = []
    for i, (inv, cash, ov, fills) in enumerate(WORLDS):
        an, bn, sn = run(M, dict(inv), cash, ov, fills)
        ao, bo, so = run(base, dict(inv), cash, ov, fills)
        what = []
        if strip(an.wire) != strip(ao.wire):
            what.append("orders")
        if {e: astuple(x.quote) for e, x in bn.ex.items()} != {e: astuple(x.quote) for e, x in bo.ex.items()}:
            what.append("quotes")
        if notes(bn) != notes(bo):
            what.append("notes")
        cn, co = cut(sn), cut(so)
        if cn != co or set(sn) ^ set(so):
            what.append(("status", sorted(k for k in set(cn) | set(co) if cn.get(k) != co.get(k)) +
                         sorted(set(sn) ^ set(so))))
        if set(bn.health) != set(bo.health) or bn.status_report() != bo.status_report():
            what.append("health")
        if bn.summary_ops_line(100000.0) != bo.summary_ops_line(100000.0):
            what.append("summary")
        check(f"world {i}: positions {inv or 'flat'}, cash {cash:,.0f}{', fills' if fills else ''}: "
              f"{len(an.wire)} orders identical, quotes, notes, status, health and summary identical",
              not what, what)
        diffs += what
    check(f"ALL identical to {BASE_REV} on the live settings", not diffs)

n_ok, n = sum(RESULTS), len(RESULTS)
print(f"\n{n_ok}/{n} passed")
sys.exit(0 if n_ok == n else 1)
