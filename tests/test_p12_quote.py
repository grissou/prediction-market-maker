"""
Offline tests for Package 12 Part M (analysis/p11/SPEC_P12.md): quoting items.
M1 close_override_utc: every market's effective close (hours_to_close -> stop_minutes_before_close, the pre-close
   windows, close_window's users, the phase line) = max(API close, the override); it only EXTENDS a close; an invalid
   value is ignored with one alert; validate_overrides takes it as a date (DATE_SETTINGS) or "" (off).
   stop_minutes_before_close is live-overridable (0-120).
M2 skew_target_inventory: compute_quote's inventory skew from (inv - target), target = Bot.alloc_target_for(eid) (the
   allocator's latest plan's intended holding, else the current holding when edge-held > 0 in value_mode, else 0),
   race-netted as eff_inv; age_skew off on +EV holdings in value_mode; never crossing; the value floor kept.
Also: flags off identical to the branch head (12e84c8) on a grid (compute_quote incl. reduce-only, hours_to_close,
the stop, close_window, full cycles: wire orders, quotes, status keys).

Run:  python tests/test_p12_quote.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
from dataclasses import astuple
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot      # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REV = "12e84c8"                              # the branch head before Part M
REAL_UTCNOW = M.utcnow


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def quiet_cycle(b, n=1):
    logging.disable(logging.CRITICAL)
    try:
        for _ in range(n):
            b.cycle()
    finally:
        logging.disable(logging.NOTSET)


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def set_clock(s, mods=(M,)):
    """Shift the bot's wall clock (module utcnow) so that 'now' is s (time keeps flowing)."""
    off = utc(s) - REAL_UTCNOW()
    for mod in mods:
        mod.utcnow = lambda off=off: REAL_UTCNOW() + off


def reset_clock(mods=(M,)):
    for mod in mods:
        mod.utcnow = REAL_UTCNOW


REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
API_CLOSE = utc("2026-11-04T00:00:00Z")           # the fake markets' settlementDate (= the live API's)
OVR = "2026-11-04T17:00:00Z"                      # 12:00 pm ET on 4 Nov


def plain_bot(inv, refs=None, **cfg):
    a, b = make_bot()
    a.inv.update(inv)
    b.refs = FakeRefs(dict(REFS if refs is None else refs))
    b.cfg.selftest_enabled = False
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    return a, b


def cq(inv, fv, bb, ba, ro=False, age=0.0, p=None, eff=None, mod=None, extra=None, **kw):
    mod = mod or M
    c = mod.Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return mod.compute_quote(fv, inv, inv if eff is None else eff, bb, ba, c, reduce_only=ro, bankroll=100000,
                             age_hours=age, value_p=p, **(extra or {}))


# ============================================================================================ settings
print("--- settings: defaults off, live ranges")
c = M.Config()
check("defaults: close_override_utc '' (off), stop_minutes_before_close 15, skew_target_inventory False",
      (c.close_override_utc, c.stop_minutes_before_close, c.skew_target_inventory) == ("", 15.0, False))
check("ranges: close_override_utc 2026-11-01..2026-11-07 (a date), stop 0..120, skew flag bool",
      M.OVERRIDABLE["close_override_utc"] == ("2026-11-01T00:00:00Z", "2026-11-07T00:00:00Z")
      and M.OVERRIDABLE["stop_minutes_before_close"] == (0.0, 120.0)
      and M.OVERRIDABLE["skew_target_inventory"] == (False, True) and "close_override_utc" in M.DATE_SETTINGS)
good, bad = M.validate_overrides({"close_override_utc": OVR, "stop_minutes_before_close": 0,
                                  "skew_target_inventory": True}, c)
check("validate: '2026-11-04T17:00:00Z', stop 0 and the skew flag accepted",
      good == {"close_override_utc": OVR, "stop_minutes_before_close": 0.0, "skew_target_inventory": True}
      and not bad, (good, bad))
good, bad = M.validate_overrides({"close_override_utc": "", "stop_minutes_before_close": 120}, c)
check("validate: '' accepted (switch the override off live), stop 120 accepted",
      good == {"close_override_utc": "", "stop_minutes_before_close": 120.0} and not bad, (good, bad))
for v in ("tomorrow", "2026-11-04T17:00:00", "2026-10-31T23:00:00Z", "2026-11-08T00:00:00Z", 5, None):
    good, bad = M.validate_overrides({"close_override_utc": v}, c)
    check(f"validate: close_override_utc {v!r} refused (not a UTC time in range)", not good and len(bad) == 1,
          (good, bad))
for v in (-1, 121, True, "15"):
    good, bad = M.validate_overrides({"stop_minutes_before_close": v}, c)
    check(f"validate: stop_minutes_before_close {v!r} refused", not good and len(bad) == 1, (good, bad))
good, bad = M.validate_overrides({"basket_exit_utc": ""}, c)
check("basket_exit_utc still refuses '' (only close_override_utc takes it)", not good and len(bad) == 1)

# ============================================================================================ M1
print("--- M1 close_override_utc: hours_to_close, the stop, the windows")
set_clock("2026-11-03T23:50:00Z")                 # 10 minutes before the API close
try:
    a, b = plain_bot({})
    ex = b.ex["21"]
    check("the fake markets' API close is 4 Nov 00:00 UTC (min(settlementDate, endDate))", ex.close == API_CLOSE,
          ex.close)
    h0 = b.hours_to_close(ex)
    check(f"no override: 10 min to close ({h0:.3f} h)", abs(h0 - 1 / 6) < 0.01, h0)
    b.cfg.close_override_utc = OVR
    h1 = b.hours_to_close(ex)
    check(f"override 4 Nov 17:00: 17 h 10 min to close ({h1:.3f} h)", abs(h1 - (17 + 1 / 6)) < 0.01, h1)
    check("effective_close = max(API close, override)", b.effective_close(ex) == utc(OVR))
    ex.close = utc("2026-11-05T00:00:00Z")        # an API close later than the override
    check("an override EARLIER than the API close never shortens it", b.effective_close(ex) == ex.close
          and abs(b.hours_to_close(ex) - (24 + 1 / 6)) < 0.01, b.hours_to_close(ex))
    ex.close = None
    check("no API close: still 'never closes' (inf), whatever the override", b.hours_to_close(ex) == float("inf"))
    ex.close = API_CLOSE
    ALERTS.clear()
    for bad_v in ("not a date", "2026-11-04T17:00:00", "2026-12-01T00:00:00Z"):
        ALERTS.clear()
        b.cfg.close_override_utc = bad_v
        hs = [b.hours_to_close(ex) for _ in range(5)]
        check(f"invalid override {bad_v!r} (set from the config file): ignored (API close), ONE alert",
              all(abs(h - 1 / 6) < 0.01 for h in hs) and len(ALERTS) == 1 and "IGNORED" in ALERTS[0], (hs, ALERTS))
    b.cfg.close_override_utc = OVR
    check("...a valid value afterwards applies again", abs(b.hours_to_close(ex) - (17 + 1 / 6)) < 0.01)

    # the stop
    a, b = plain_bot({"21": 600})
    quiet_cycle(b)
    check("no override, 10 min to close: the stop (15 min) -> nothing quoted, no order sent",
          all(x.quote == M.NO_QUOTE for x in b.ex.values()) and not a.orders, [x.quote for x in b.ex.values()])
    b.phase = "trading"
    st_off = b.status_report()[0]
    a, b = plain_bot({"21": 600}, close_override_utc=OVR)
    quiet_cycle(b)
    q21 = b.ex["21"].quote
    check("override 4 Nov 17:00: quoting goes on at 23:50 UTC on 3 Nov (two-sided: 17 h > the 12 h flatten window)",
          q21.bid is not None and q21.ask is not None and a.orders, q21)
    b.phase = "trading"
    st_on = b.status_report()[0]
    check("phase line: 'stopped for settlement' without, not with the override",
          "stopped for settlement" in st_off and "stopped for settlement" not in st_on, (st_off, st_on))
    check("close_window users: the allocator may trade (alloc_market_ok) only with the override",
          b.alloc_market_ok(b.ex["21"]) and not plain_bot({})[1].alloc_market_ok(b.ex["21"]))

    # the windows (pre-close): 6 h before the OVERRIDE = inside flatten (12 h) and per-market (6 h), outside exit (2 h)
    set_clock("2026-11-04T11:00:00Z")             # 11 h after the API close, 6 h before the override
    a, b = plain_bot({"21": 600}, close_override_utc=OVR)
    ex = b.ex["21"]
    hrs = b.hours_to_close(ex)
    check(f"after the API close, before the override: {hrs:.2f} h left, inside flatten (12 h), not the exit (2 h)",
          b.close_window("exit_hours_before_close") < hrs <= b.close_window("flatten_hours_before_close"), hrs)
    quiet_cycle(b)
    q = b.ex["21"].quote
    check("...reduce-only quoting there (long 600: an ask, no adding bid)", q.ask is not None and q.bid is None, q)
    set_clock("2026-11-04T16:00:00Z")             # 1 h before the override: the exit window
    a, b = plain_bot({"21": 600}, close_override_utc=OVR)
    check("1 h before the override: inside the exit window (2 h)",
          b.hours_to_close(b.ex["21"]) <= b.close_window("exit_hours_before_close"))
    set_clock("2026-11-04T16:50:00Z")             # 10 min before the override: the stop
    a, b = plain_bot({"21": 600}, close_override_utc=OVR)
    quiet_cycle(b)
    check("10 min before the override: the stop -> nothing quoted",
          all(x.quote == M.NO_QUOTE for x in b.ex.values()) and not a.orders)

    # stop_minutes_before_close live
    set_clock("2026-11-03T22:30:00Z")             # 90 min before the API close
    a, b = plain_bot({"21": 600}, stop_minutes_before_close=120.0)
    quiet_cycle(b)
    check("stop_minutes_before_close 120: 90 min before the close -> nothing quoted",
          all(x.quote == M.NO_QUOTE for x in b.ex.values()))
    check("...and every pre-close window is at least the stop (close_window >= 2 h, value_mode too)",
          b.close_window("exit_hours_before_close") >= 2.0 and b.close_window("flatten_per_market_hours") >= 2.0)
    b.cfg.value_mode = True
    check("...value_mode (windows -inf): close_window = the stop's 2 h", b.close_window("flatten_hours_before_close")
          == 2.0)
    a, b = plain_bot({"21": 600}, stop_minutes_before_close=60.0)
    quiet_cycle(b)
    check("stop 60: 90 min before the close -> quoting again (the exit window, 2 h: long 600 -> an exit ask)",
          b.ex["21"].quote.ask is not None, b.ex["21"].quote)
    a, b = plain_bot({}, close_override_utc=OVR)
    check("nothing else changes: the basket schedule still uses the API close",
          b.basket_schedule()["close"] == plain_bot({})[1].basket_schedule()["close"])
finally:
    reset_clock()

# ============================================================================================ M2 compute_quote
print("--- M2 compute_quote: skew from (inv - target), age skew off, never crossing")
q_flat = cq(0, 0.60, 0.50, 0.70)
q_long = cq(3000, 0.60, 0.50, 0.70)
q_long_t0 = cq(3000, 0.60, 0.50, 0.70, extra={"skew_inv": 0.0})
check("skew_inv None = the old skew (identical quote)", astuple(cq(3000, 0.60, 0.50, 0.70, extra={"skew_inv": None}))
      == astuple(q_long))
check(f"long 3000 at its target (skew_inv 0): the ask as flat ({q_long_t0.ask} = {q_flat.ask}), while the old skew "
      f"lowers it ({q_long.ask}); the band (limits) unskewed too",
      q_long_t0.ask == q_flat.ask and q_long.ask < q_flat.ask
      and (q_long_t0.bid_limit, q_long_t0.ask_limit) == (q_flat.bid_limit, q_flat.ask_limit), (q_long, q_long_t0, q_flat))
q_under = cq(1000, 0.60, 0.50, 0.70, extra={"skew_inv": -2000.0})
check(f"below target (skew_inv -2000): the bid moves UP ({q_under.bid} > {q_flat.bid}) but never through fair",
      q_under.bid > q_flat.bid and q_under.bid <= 0.60, q_under)
q_over = cq(3000, 0.60, 0.50, 0.70, extra={"skew_inv": 1000.0})
check("above target by 1000: a smaller skew than from flat (ask between the two)",
      q_long.ask <= q_over.ask <= q_flat.ask and q_over.ask < q_flat.ask, (q_long.ask, q_over.ask, q_flat.ask))
qa = cq(3000, 0.60, 0.50, 0.70, age=200.0, extra={"skew_inv": 0.0})
qa_off = cq(3000, 0.60, 0.50, 0.70, age=200.0, extra={"skew_inv": 0.0, "age_off": True})
check(f"age 200 h: age skew lowers the ask ({qa.ask}); age_off removes it ({qa_off.ask} = flat {q_flat.ask})",
      qa.ask < qa_off.ask and qa_off.ask == q_flat.ask, (qa, qa_off))
check("age_off alone (skew_inv None) = no age skew, inventory skew unchanged",
      cq(3000, 0.60, 0.50, 0.70, age=200.0, extra={"age_off": True}).ask == q_long.ask)
rng = random.Random(12)
cross_ok, n = True, 0
for _ in range(3000):
    fv = round(rng.uniform(0.03, 0.97), 3)
    bb = round(max(0.01, fv - rng.choice((0.01, 0.02, 0.05))), 2)
    ba = round(min(0.99, bb + rng.choice((0.01, 0.02, 0.03))), 2)
    inv = rng.choice((-4000, -500, 0, 700, 4000))
    q = cq(inv, fv, bb, ba, ro=rng.random() < 0.3, age=rng.choice((0.0, 300.0)),
           p=rng.choice((None, fv)), value_mode=rng.random() < 0.5, skew_target_inventory=True,
           extra={"skew_inv": rng.uniform(-20000, 20000), "age_off": rng.random() < 0.5})
    n += 1
    if ((q.bid is not None and q.bid >= ba - 1e-9) or (q.ask is not None and q.ask <= bb + 1e-9)
            or (q.bid is not None and q.ask is not None and q.bid >= q.ask - 1e-9)):
        cross_ok = False
        break
check(f"never crosses another trader's order or itself on {n} random points (skew_inv up to +-20k)", cross_ok, q)
fl_ok = True
VM_CFG = M.Config()
VM_CFG.value_mode = True
for inv in (800, -800):
    for si in (-20000.0, 0.0, 20000.0):
        p = 0.50
        q = cq(inv, 0.45 if inv > 0 else 0.55, 0.40, 0.60, p=p, value_mode=True,
               extra={"skew_inv": si, "age_off": True})
        q = M.value_floor_quote(q, p, inv, VM_CFG)
        if inv > 0 and q.ask is not None and q.ask < p - M.Config().value_sell_margin - 1e-9:
            fl_ok = False
        if inv < 0 and q.bid is not None and q.bid > p + M.Config().value_sell_margin + 1e-9:
            fl_ok = False
check("value_mode: the reducing side keeps the value floor (p -+ margin) whatever the target skew", fl_ok)

# ============================================================================================ M2 Bot accessors
print("--- M2 Bot.alloc_target_for / alloc_plan_targets / skew_target_inputs")
# Ohio Dem (12): p 0.88, book 0.82 / 0.90 -> a long's edge-held (0.88 - 0.82) / 0.82 = +7%; Utah Rep (21): p 0.55,
# book 0.48 / 0.56 -> long +14.6%; Utah Dem (22): p 0.45, book 0.44 / 0.52 -> a short's (0.52 - 0.45) / 0.48 = +14.6%.
a, b = plain_bot({"12": 2500, "21": 600, "22": -400})
quiet_cycle(b)
inv = dict(a.inv)
check("unknown market -> None", b.alloc_target_for("nope", inv) is None)
check("value_mode off, allocator off: target 0 everywhere",
      all(b.alloc_target_for(e, inv) == 0.0 for e in b.ex))
b.cfg.value_mode = True
e12 = b.alloc_edge_held(b.ex["12"], 2500)
check(f"edge-held of the long Ohio Dem favourite ({e12:.3f}) > 0", e12 is not None and e12 > 0, e12)
check("value_mode: a long with edge-held > 0 -> target = the holding (2500)", b.alloc_target_for("12", inv) == 2500.0)
check("value_mode: a short with edge-held > 0 -> target = the short (-400)", b.alloc_target_for("22", inv) == -400.0)
check("no position -> 0", b.alloc_target_for("11", inv) == 0.0)
b.refs.new_reading({**REFS, "Ohio Senate|Democratic": 0.80, "Ohio Senate|Republican": 0.20}, {})   # p < best bid
quiet_cycle(b)
check("edge-held < 0 (p 0.80 < bid 0.82) -> target 0 (the position is one to unload)",
      b.alloc_target_for("12", inv) == 0.0, b.alloc_edge_held(b.ex["12"], 2500))
b.refs.new_reading({k: v for k, v in REFS.items() if not k.startswith("Ohio")}, {})   # no Polymarket for Ohio
quiet_cycle(b)
check("no liquid p -> target 0", b.alloc_target_for("12", inv) == 0.0)
b.refs.new_reading(dict(REFS), {})
quiet_cycle(b)
b.ex["12"].verified = -1e18                        # a stale book
check("no fresh book -> target 0", b.alloc_target_for("12", inv) == 0.0)
quiet_cycle(b)
pairs = [{"sell": {"kind": "long", "eid": "12", "qty": 500}, "buy": {"eid": "21", "short": False, "qty": 300}},
         {"sell": {"kind": "short", "eid": "22", "qty": 100}, "buy": {"eid": "11", "short": True, "qty": 50}},
         {"sell": {"kind": "cash"}, "buy": {"eid": "21", "short": False, "qty": 100}}]
t = M.Bot.alloc_plan_targets(inv, pairs)
check("alloc_plan_targets: long sale -500, buys +300 +100, short buy-back +100, short sale -50",
      t == {"12": 2000.0, "21": 1000.0, "22": -300.0, "11": -50.0}, t)
t = M.Bot.alloc_plan_targets({"11": -1000, "12": -1000}, [{"sell": {"kind": "set", "qty": 400, "members":
                                                                     ["11", "12"]}, "buy": None}])
check("alloc_plan_targets: a NO+NO set unwind of 400 raises both legs", t == {"11": -600.0, "12": -600.0}, t)
b.alloc_targets = {"12": 2000.0, "11": 0.0}
b.cfg.alloc_enabled = True
check("alloc_enabled: the latest plan's target wins (2000, not the holding 2500)",
      b.alloc_target_for("12", inv) == 2000.0)
check("...a market the plan did not touch: the value_mode fallback (21 -> 600)", b.alloc_target_for("21", inv) == 600.0)
b.cfg.alloc_enabled = False
check("alloc_enabled off: the plan's targets are not used (12 -> 2500)", b.alloc_target_for("12", inv) == 2500.0)
b.alloc_targets = {}
# skew_target_inputs: race-netted like effective_inventory
eff = b.effective_inventory(inv)
si, age_off = b.skew_target_inputs(b.ex["12"], inv, eff["12"], False)
check(f"skew_target_inputs (race-netted): Ohio Dem long 2500 at target -> skew_inv 0 ({si})", abs(si) < 1e-9, si)
si21, ao21 = b.skew_target_inputs(b.ex["21"], inv, eff["21"], False)
check(f"Utah Rep 600 / Dem -400 both at target -> skew_inv 0 ({si21}); age skew off (+EV holding)",
      abs(si21) < 1e-9 and ao21 and age_off, (si21, ao21))
si_pm, _ = b.skew_target_inputs(b.ex["21"], inv, inv["21"], True)
check("per-market window: skew_inv = inv - target (600 - 600 = 0)", abs(si_pm) < 1e-9)
b.cfg.value_mode = False
si11, ao11 = b.skew_target_inputs(b.ex["21"], inv, eff["21"], False)
check("value_mode off (no target): skew_inv = eff_inv, age skew kept", si11 == eff["21"] and not ao11)

# alloc_tick records the plan's targets (dry run) and clears them when the allocator is off
a, b = plain_bot({"12": 2500}, alloc_enabled=True)
a.live = False
b.api.live = False
quiet_cycle(b)
b.alloc_last_run_wall = None
b.alloc_plan = lambda inv_, now_m, cash, skip=(), turnover_left=None: (
    [{"sell": {"kind": "long", "eid": "12", "qty": 700, "label": "x", "px": 0.8, "edge": 0.0, "usd": 1},
      "buy": None, "usd": 1, "status": "pending"}], {"blocked_by": {}, "ev_gain_est": 0.0})
b.alloc_journal = lambda pr: "pair"
b.alloc_tick(M.utcnow(), {"12": 2500}, {})
check("alloc_tick (dry run): the plan's intended holding recorded (2500 - 700 = 1800)", b.alloc_targets == {"12": 1800.0},
      b.alloc_targets)
b.cfg.alloc_enabled = False
b.alloc_tick(M.utcnow(), {"12": 2500}, {})
check("allocator off: the targets cleared", b.alloc_targets == {})

# ============================================================================================ M2 decide
print("--- M2 decide: a +EV favourite we hold is not quoted as a position to unload")


def ask_of(flag, age=None, **cfg):
    a_, b_ = plain_bot({"12": 2500}, value_mode=True, skew_target_inventory=flag, **cfg)
    if age is not None:
        b_.age_hours = lambda ex_: age if ex_.eid == "12" else 0.0
    quiet_cycle(b_, 2)
    return b_.ex["12"].quote, b_


q_off, _ = ask_of(False)
q_on, b_on = ask_of(True)
check(f"value_mode, long 2500 Ohio Dem (+EV): flag off ask {q_off.ask}, on {q_on.ask} (no unload skew: not lower)",
      q_on.ask is not None and q_off.ask is not None and q_on.ask >= q_off.ask, (q_off, q_on))
check("...flag on: the ask still keeps the value floor (>= p - margin)",
      q_on.ask is None or q_on.ask >= 0.88 - b_on.cfg.value_sell_margin - 1e-9, q_on)
check("...flag on: never crossing (bid < best ask, ask > best bid, bid < ask)",
      (q_on.bid is None or q_on.bid < 0.90) and (q_on.ask is None or q_on.ask > 0.82)
      and (q_on.bid is None or q_on.ask is None or q_on.bid < q_on.ask), q_on)
q_age_off, _ = ask_of(False, age=500.0, skew_age_enabled=True)
q_age_on, _ = ask_of(True, age=500.0, skew_age_enabled=True)
check(f"age 500 h, +EV holding: flag off the age skew lowers the ask ({q_age_off.ask}), on it does not "
      f"({q_age_on.ask})", q_age_on.ask is not None and q_age_off.ask is not None and q_age_on.ask >= q_age_off.ask,
      (q_age_off, q_age_on))
# -EV holding (p 0.80 below the best bid 0.82): the age skew stays on with the flag
refs_neg = {**REFS, "Ohio Senate|Democratic": 0.80, "Ohio Senate|Republican": 0.20}
a1, b1 = plain_bot({"12": 2500}, refs=refs_neg, value_mode=True, skew_target_inventory=True, skew_age_enabled=True)
inv1 = {"12": 2500}
quiet_cycle(b1)
_, ao = b1.skew_target_inputs(b1.ex["12"], inv1, b1.effective_inventory(inv1)["12"], False)
check("-EV holding (edge-held < 0): age skew NOT switched off, target 0", not ao and b1.alloc_target_for("12", inv1)
      == 0.0)
# flag on, no target anywhere (value_mode off, allocator off): identical to flag off
for inv_ in ({"12": 2500}, {"21": 600, "22": -900}, {}):
    a2, b2 = plain_bot(inv_, skew_target_inventory=True)
    a3, b3 = plain_bot(inv_)
    quiet_cycle(b2, 2)
    quiet_cycle(b3, 2)
    check(f"flag on but no target (value_mode / allocator off), positions {inv_}: quotes identical to flag off",
          {e: astuple(x.quote) for e, x in b2.ex.items()} == {e: astuple(x.quote) for e, x in b3.ex.items()})

# ============================================================================================ flags off identical
print(f"--- flags off identical to the branch head ({BASE_REV}) on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, check=True).stdout
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("mm_bot_p12m_base", path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.alert, base.notify = M.alert, (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

if base is not None:
    same, n = True, 0
    for inv in (-3000, -600, 0, 400, 2500):
        for fv, bb, ba in ((0.60, 0.50, 0.70), (0.08, 0.06, 0.10), (0.93, 0.90, 0.96), (0.50, 0.48, 0.52),
                           (0.30, None, 0.35), (0.70, 0.65, None)):
            for ro in (False, True):
                for age in (0.0, 12.0, 300.0):
                    for p in (None, fv - 0.04, fv + 0.04):
                        for vm in (False, True):
                            n += 1
                            if astuple(cq(inv, fv, bb, ba, ro=ro, age=age, p=p, value_mode=vm, skew_age_enabled=True)) \
                                    != astuple(cq(inv, fv, bb, ba, ro=ro, age=age, p=p, mod=base, value_mode=vm,
                                                  skew_age_enabled=True)):
                                same = False
    check(f"compute_quote (normal + reduce-only, age skew, value_mode, value_p) identical on {n} points", same)

    def twin(inv, clock, reduce, overrides=None):
        out = []
        set_clock(clock, (M, base))
        try:
            for mod in (M, base):
                api_x, bn = make_bot()
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
                    bn = base.Bot(api_x, cfg)
                api_x.inv.update(inv)
                bn.refs = FakeRefs(dict(REFS))
                bn.cfg.selftest_enabled = False
                if reduce:
                    bn.cfg.max_worst_case_frac, bn.cfg.worst_case_backstop_frac = 0.001, 0.002
                for k, v in (overrides or {}).items():
                    setattr(bn.cfg, k, v)
                quiet_cycle(bn, 2)
                out.append((api_x, bn))
            hrs = [{e: (round(x.hours_to_close(x.ex[e]), 3), x.close_window("flatten_hours_before_close"),
                        x.close_window("exit_hours_before_close")) for e in x.ex} for _, x in out]
            return out, hrs
        finally:
            reset_clock((M, base))

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = ok_h = True
    diffs = []
    clocks = ("2026-10-04T12:00:00Z", "2026-11-03T13:00:00Z", "2026-11-03T22:30:00Z", "2026-11-03T23:50:00Z")
    for inv in ({}, {"11": 300, "21": 3000, "22": -3000}, {"12": 2500}):
        for clock in clocks:
            for reduce in (False, True):
                for ovr in ({}, {"value_mode": True, "skew_age_enabled": True}):
                    ((an, bn), (ab, bb_)), (hn, hb) = twin(inv, clock, reduce, ovr)
                    if hn != hb:
                        ok_h = False
                        diffs.append(("hours", clock))
                    if strip(an.wire) != strip(ab.wire):
                        ok_w = False
                        diffs.append(("wire", inv, clock, reduce, ovr))
                    if {e: astuple(x.quote) for e, x in bn.ex.items()} != {e: astuple(x.quote)
                                                                          for e, x in bb_.ex.items()}:
                        ok_q = False
                        diffs.append(("quote", inv, clock, reduce, ovr))
                    bn.write_status(True)
                    bb_.write_status(True)
                    with open(bn.cfg.status_file) as f:
                        kn = set(json.load(f))
                    with open(bb_.cfg.status_file) as f:
                        kb = set(json.load(f))
                    if kn != kb or set(bn.health) != set(bb_.health):
                        ok_s = False
                        diffs.append(("status", kn ^ kb))
    check("hours_to_close / close_window identical (4 clocks incl. 90 and 10 min before the close)", ok_h, diffs[:1])
    check("two full cycles send the same orders (3 books x 4 clocks x reduce-only x value_mode)", ok_w, diffs[:1])
    check("every market's quote identical, incl. the stop, exit and flatten windows", ok_q, diffs[:1])
    check("status.json / health keys identical", ok_s, diffs[:1])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
