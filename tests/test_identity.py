"""
Identity: the current mm_bot.py makes the SAME decisions as a pinned revision on the LIVE settings.

The pinned revision is the code the server runs (IDENTITY_BASE, default d9220c1 = Package 16). The live settings
are IDENTITY_SETTINGS (default deploy/settings_override.live_minimal.json: the server's live file). Both modules are
given the same fake exchange, the same books, positions, cash and fills, and run four cycles; their order streams,
quotes, order notes, status.json (minus wall-clock fields), health keys, status line and 2-hourly summary line must
match. Settings the pinned revision knows but the current code no longer has (RETIRED_LIVE) are accepted in the live
file only at their pinned default, or at any value when the retired feature's master switch (RETIRED_MASTERS, e.g.
momentum_enabled for every momentum_* / mom_* knob) is absent from the live file or at its default there - the old
code never read the knob while the feature was off, so dropping it changes nothing live. What was accepted is
printed; anything else is a refusal (the comparison would be meaningless).

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
SETTINGS = os.environ.get("IDENTITY_SETTINGS", os.path.join(ROOT, "deploy", "settings_override.live_minimal.json"))
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
# Retired settings the live file still names (the file is kept as the server has it): a key the current Config no
# longer has is accepted when its live value is the pinned revision's default - the pinned code runs it at the value
# its default gives, so dropping the setting changes nothing live - or when the retired feature's MASTER switch
# (RETIRED_MASTERS: the knob's prefix -> the switch) is absent from the live file or at its default there: the old
# code never read the knob while the feature was off. Any other value is a refusal, as before. The keys so forgiven
# are printed, so a deploy sees what the live file still carries.
RETIRED_MASTERS = {                               # knob prefix -> the retired feature's master switch
    # P16 momentum sleeve (removed on simplify): every momentum_* / mom_* knob, the value_* classifier, the funding
    # and election-night hooks were read only while momentum_enabled was true (live: absent = false since 8 Oct)
    **{pre: "momentum_enabled" for pre in ("momentum_", "mom_", "value_extreme_p", "value_min_edge", "mm_carry_min",
                                             "election_holdback_")},
    # R3 depth ladder (removed on simplify): every ladder_* knob was read only while ladder_enabled was true
    "ladder_": "ladder_enabled",
    # turnover control (removed on simplify): the two quoting knobs were read only with turnover_control_enabled;
    # reduce_from_book_max_turnover only with reduce_from_book_dead_only (the dead-market diagnostic stays)
    "turnover_dead_adding_factor": "turnover_control_enabled",
    "turnover_dead_max_position_frac": "turnover_control_enabled",
    "reduce_from_book_max_turnover": "reduce_from_book_dead_only",
    # mark fragility's cap (removed on simplify): mark_frag_total_max_cash was read only with mark_frag_enabled
    "mark_frag_total_max_cash": "mark_frag_enabled",
    # ref-tilt application (T2.1 ramp-in, T2.3 carry ramp, X12 tilted takes, Package 8 guard variants; removed on
    # simplify): read only while ref_tilt_enabled was true (live: absent = false). The estimator and its knobs
    # (ref_tilt_max, _min_markets, _halflife_min, _winsor, _estimator) stay: they shape the live tilt_s reading.
    **{k: "ref_tilt_enabled" for k in ("ref_tilt_headline", "ref_tilt_rampin_min", "ref_tilt_carry_days",
                                       "take_tilted_ref", "ref_guard_tilted")},
    # last dead knobs (removed on simplify), each read only behind its own master switch (live: all absent = off)
    **{k: "fast_unload_enabled" for k in ("fast_unload_min_edge", "fast_unload_min_shares", "fast_unload_seconds",
                                          "fast_unload_edge", "fast_unload_size_mult")},
    **{k: "refill_cooldown_enabled" for k in ("refill_cooldown_fills", "refill_cooldown_window_seconds",
                                              "refill_cooldown_min_shares", "refill_cooldown_seconds")},
    "no_chase_tolerance_ticks": "no_chase_enabled", "no_chase_fv_epsilon": "no_chase_enabled",
    **{k: "ttl_tiers_enabled" for k in ("order_ttl_busy", "order_ttl_quiet", "ttl_jitter_frac", "ttl_busy_size_frac")},
    "ttl_expire_grace_seconds": "ttl_expire_as_cancel",
    "reduce_from_book_headline": "reduce_from_book", "reduce_from_book_pause_s": "reduce_from_book",
    "arb_sellback_min_sum": "arb_sellback",
    "behind_best_": "behind_best_size_enabled",
    "fl_bias_": "fl_bias_enabled",
}
# A retired master switch whose CODE default was on but which the live file turns off: the new code behaves as the
# old did at that live value (the age skew's branch is gone; the lots tracking that fed it stays for the status age
# fields), so the key is accepted at exactly that value - at any other the comparison would be meaningless.
RETIRED_FLIPPED = {"skew_age_enabled": False}
RETIRED_LIVE, RETIRED_OFF = {}, {}
if base is not None:
    def master_off(k):
        m = next((m for pre, m in RETIRED_MASTERS.items() if k.startswith(pre)), None)
        return m is not None and LIVE.get(m, getattr(base.Config(), m)) == getattr(base.Config(), m)
    gone_live = [k for k in LIVE if k in base.Config.__dataclass_fields__ and k not in M.Config.__dataclass_fields__]
    RETIRED_LIVE = {k: LIVE[k] for k in gone_live if LIVE[k] == getattr(base.Config(), k)}
    RETIRED_OFF = {k: LIVE[k] for k in gone_live if k not in RETIRED_LIVE
                   and (master_off(k) or (k in RETIRED_FLIPPED and LIVE[k] == RETIRED_FLIPPED[k]))}
    if RETIRED_LIVE:
        print(f"    retired settings in the live file at {BASE_REV}'s default (accepted): "
              + ", ".join(f"{k}={v!r}" for k, v in sorted(RETIRED_LIVE.items())))
    if RETIRED_OFF:
        print("    retired settings in the live file off their default but behind a master switch at its default "
              "(accepted): " + ", ".join(f"{k}={v!r}" for k, v in sorted(RETIRED_OFF.items())))
    RETIRED_LIVE.update(RETIRED_OFF)
bad_new = [p for p in bad_new if p.split(":")[0] not in RETIRED_LIVE]
check(f"live settings accepted by the current code ({len(good_new)} of {len(LIVE)} keys, "
      f"{len(RETIRED_LIVE)} retired at the pinned default or behind an off master switch)", not bad_new, bad_new)
LIVE_NEW = {k: v for k, v in LIVE.items() if k not in RETIRED_LIVE}   # the live file as the current code takes it
if base is not None:
    good_old, bad_old = base.validate_overrides(LIVE, base.Config())
    check(f"live settings accepted by {BASE_REV} ({len(good_old)} of {len(LIVE)} keys)", not bad_old, bad_old)
    gone = [k for k in base.Config.__dataclass_fields__ if k not in M.Config.__dataclass_fields__]
    non_default = [k for k in gone if k in LIVE and LIVE[k] != getattr(base.Config(), k) and k not in RETIRED_OFF]
    check(f"settings removed from the current code ({len(gone)}) are all at their default in the live file "
          f"or behind a master switch at its default", not non_default, non_default)

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
    good, bad = mod.validate_overrides(LIVE if mod is not M else LIVE_NEW, b.cfg)   # (the current code: filtered)
    assert not bad, bad
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


# Retired features: the status.json fields they owned, with the value the OLD code reports while the feature is off.
# The new code may drop the field; the old code must have reported it at that value (so nothing live changed).
RETIRED_STATUS = {                                # e.g. "basket": ({"state": "off", ...},) or a tuple of allowed values
    # P9 F1 long-tilt basket (removed on simplify): the old code writes "basket" only once it was used (legs, a
    # state other than "off", a kill or a tick's figures), so while off the key is absent; any value it does write
    # means the basket was doing something, and nothing below forgives that
    "basket": (),
    # P8/P9 F2 tilt exits (removed on simplify): "tilt_exit_takes" {count, shares, usd, cost[, splits]} was written
    # only while tilt_exit_take was on or a take had happened; off and unused, the key is absent. The hold target
    # (Package 5 C, removed with it) never wrote a status field of its own.
    "tilt_exit_takes": (),
    # P16 momentum sleeve (removed on simplify): "momentum" {state, legs, cost, ...} was written only while
    # momentum_enabled or legs were held (mom_persist_needed); off and unused, the key is absent
    "momentum": (),
    # mark fragility's sizing cap (removed on simplify): the health key mark_frag_total_cap_active was False while
    # mark_frag_enabled was off (the mark-noise estimate itself, mark_frag_total_cash / _top / _capped_markets /
    # _estimates, stays: it is written from the recorder's snapshots whatever the flag)
    "mark_frag_total_cap_active": (False,),
    # T2.5 passive pair unwind (removed on simplify): "pair_passive_open" / "pair_passive_sets_total" were written
    # only with pair_unwind_passive on or a slice open (self.pp, which only the opener filled); off, both are absent
    "pair_passive_open": (),
    "pair_passive_sets_total": (),
    # ref-tilt application (removed on simplify): the s APPLIED to quotes was 0 while ref_tilt_enabled was off (the
    # estimate tilt_s, tilt_exposure, tilt_state and tilt_diag stay: live readings used by the owner)
    "tilt_s_applied": (0.0, 0),
    "tilt_s_applied_headline": (0.0, 0),
    # last dead knobs (removed on simplify): the health key fast_unload_windows counted open fast-unload windows,
    # 0 while fast_unload_enabled was off; arb_cash_blocked was written only with arb_cash_rule on (absent);
    # fl_bias_markets counted the fl_bias markets per side, {"bid": 0, "ask": 0} while fl_bias_enabled was off
    "fast_unload_windows": (0,),
    "behind_best_markets": (0,),
    "arb_cash_blocked": (),
    "fl_bias_markets": ({"bid": 0, "ask": 0},),
}
RETIRED_SUBKEYS = {                               # sub-keys of a surviving top-level dict, dotted paths allowed
    # (mm_risk_room.blocked.basket and mm_carry_24h.fills.basket stay in the new code at 0: the older twin suites
    # compare status.json against pinned revisions; drop them here once those suites are retired)
}


def has_path(d, path):
    """The dotted path exists in the nested dict d."""
    head, _, rest = path.partition(".")
    return isinstance(d, dict) and head in d and (not rest or has_path(d[head], rest))


def drop_subkey(d, path, off):
    """d with the dotted path removed when it is there at its OFF value; d itself otherwise."""
    head, _, rest = path.partition(".")
    if not isinstance(d, dict) or head not in d:
        return d
    if rest:
        inner = drop_subkey(d[head], rest, off)
        return d if inner is d[head] else {**d, head: inner}
    return {k: v for k, v in d.items() if k != head} if d[head] == off else d

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
        # (Quote.behind, the behind-the-best flag removed on simplify, was always False while that sizing was off)
        qt = lambda x: astuple(x.quote)[:len(M.Quote.__dataclass_fields__)]      # noqa: E731
        if {e: qt(x) for e, x in bn.ex.items()} != {e: qt(x) for e, x in bo.ex.items()}:
            what.append("quotes")
        if notes(bn) != notes(bo):
            what.append("notes")
        cn, co = cut(sn), cut(so)
        for k, off in RETIRED_STATUS.items():     # a retired feature's own fields: gone from the new status, and
            if k in co and k not in cn:           # at their OFF value in the old one (else it was doing something)
                if co[k] in off if isinstance(off, tuple) else co[k] == off:
                    co.pop(k)
                    so = {a: v for a, v in so.items() if a != k}
        for k, sub in RETIRED_SUBKEYS.items():    # a retired feature's sub-keys of a surviving dict: gone from the
            if k in co and k in cn and isinstance(co[k], dict):   # new status, at their OFF value in the old one
                for a, off in sub.items():
                    if not has_path(cn[k], a):
                        co[k] = drop_subkey(co[k], a, off)
        if cn != co or set(sn) ^ set(so):
            what.append(("status", sorted(k for k in set(cn) | set(co) if cn.get(k) != co.get(k)) +
                         sorted(set(sn) ^ set(so))))
        ho = {k for k, v in bo.health.items()     # (a retired health key at its OFF value may be gone)
              if not (k in RETIRED_STATUS and k not in bn.health and v in RETIRED_STATUS[k])}
        if set(bn.health) != ho or bn.status_report() != bo.status_report():
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
