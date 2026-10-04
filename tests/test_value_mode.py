"""
Offline tests for Package 10 Part A (analysis/p10/SPEC_P10.md): SIG settles at the OUTCOME, so a contract is worth its
Polymarket probability p (race-scaled, liquid), not its mark.
  A1 value_mode: the side reducing a position never rests below p - value_sell_margin (long) / above p + margin
     (short), in normal AND reduce-only quoting, after inventory and age skew, reduce_join_best, fast unload,
     reduce_from_book and hold_quote; the max_skew_through clamp also in reduce-only; never crossing, never resizing.
     The pre-close windows (exit / flatten / per-market flatten) do nothing; exit_quote takes the floor; warnings.
  A2 bloc_delta_enabled: the closed-form copula bloc delta (vs a Monte Carlo slope within 3%) replaces the share
     count in party_blocks / party_shift (and the ladder's room); status.json bloc_delta / bloc_delta_frac; summary.
  A3 ranges: worst_case_backstop_frac up to 1.5, max_worst_case_frac 0.60, the kill switch at 0.40.
  A4 value_quote_hurdle: tails one-sided (hurdle prices), middle two-way with an inventory cap.
  Flags off identical to the branch head (git show a67d9da:mm_bot.py) on a grid: quotes, reduce-only quotes, exit
  quotes, pre-close behaviour, party blocks, wire orders, status keys.

Run:  python tests/test_value_mode.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import math
import os
import random
import subprocess
import sys
import tempfile
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
BASE_REV = "a67d9da"
NEW_KEYS = ("value_mode", "value_sell_margin", "bloc_delta_enabled", "bloc_rho", "bloc_rho_control",
            "max_bloc_delta_frac", "value_quote_hurdle", "value_mid_low", "value_mid_high", "value_mid_inventory_quotes",
            "exit_hours_before_close", "flatten_hours_before_close", "flatten_per_market_hours")


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


def cfg_with(**kw):
    c = M.Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def cq(inv, fv, bb, ba, ro=False, age=0.0, p=None, eff=None, mod=None, **kw):
    """compute_quote at a 100k account with the live defaults (+ kw); value_p only on the new module."""
    mod = mod or M
    c = mod.Config()
    for k, v in kw.items():
        setattr(c, k, v)
    extra = {"value_p": p} if mod is M else {}
    return mod.compute_quote(fv, inv, inv if eff is None else eff, bb, ba, c, reduce_only=ro, bankroll=100000,
                             age_hours=age, **extra)


REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
NOW = M.utcnow()


def plain_bot(inv, refs=None, close_h=None, **cfg):
    a, b = make_bot()
    a.inv.update(inv)
    b.refs = FakeRefs(dict(REFS if refs is None else refs))
    b.cfg.selftest_enabled = False
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    if close_h is not None:
        for x in b.ex.values():
            x.close = NOW + timedelta(hours=close_h)
    return a, b


# ============================================================================================ config / ranges
print("--- config: defaults off, every new setting live-overridable with its range (A3 ranges)")
c = M.Config()
check("defaults: value_mode off, margin 0.005, bloc off (rho 0.45 / 0.85, cap 0.05), hurdle 0, band 0.15-0.85, 2 quotes",
      (c.value_mode, c.value_sell_margin, c.bloc_delta_enabled, c.bloc_rho, c.bloc_rho_control, c.max_bloc_delta_frac,
       c.value_quote_hurdle, c.value_mid_low, c.value_mid_high, c.value_mid_inventory_quotes)
      == (False, 0.005, False, 0.45, 0.85, 0.05, 0.0, 0.15, 0.85, 2.0))
good, bad = M.validate_overrides({k: getattr(c, k) for k in NEW_KEYS}, c)
check("all 13 (10 new + the 3 close windows) in OVERRIDABLE, defaults inside their ranges", len(good) == 13 and not bad,
      bad)
good, bad = M.validate_overrides({"value_sell_margin": 0.06, "bloc_rho": 0.95, "max_bloc_delta_frac": 0.005,
                                  "value_quote_hurdle": 0.6, "exit_hours_before_close": 49.0,
                                  "flatten_hours_before_close": -1.0, "flatten_per_market_hours": 13.0,
                                  "value_mid_inventory_quotes": 21.0, "value_mid_low": 0.6, "value_mid_high": 0.4}, c)
check("out of range refused (margin 6c, rho 0.95, cap 0.005, hurdle 0.6, exit 49 h, flatten -1, per-market 13 h, ...)",
      not good and len(bad) == 10, (good, bad))
good, bad = M.validate_overrides({"exit_hours_before_close": 0.0, "flatten_hours_before_close": 0.0,
                                  "flatten_per_market_hours": 0.0, "value_mode": True}, c)
check("the owner can zero the three close windows (and switch value_mode on) live", len(good) == 4 and not bad, bad)
good, bad = M.validate_overrides({"worst_case_backstop_frac": 1.3}, c)
good2, bad2 = M.validate_overrides({"worst_case_backstop_frac": 1.5}, c)
good3, bad3 = M.validate_overrides({"worst_case_backstop_frac": 1.51}, c)
check("A3 worst_case_backstop_frac: 1.3 and 1.5 accepted, 1.51 refused", good and good2 and not good3, (bad, bad2))
check("A3 max_worst_case_frac range is (0.05, 0.60) (0.35 for the value core accepted)",
      M.OVERRIDABLE["max_worst_case_frac"] == (0.05, 0.60)
      and M.validate_overrides({"max_worst_case_frac": 0.35}, c)[0] == {"max_worst_case_frac": 0.35})
check("A3 the kill switch fraction stays out of OVERRIDABLE (house rule)", "max_drawdown_pct" not in M.OVERRIDABLE)
a, b = plain_bot({})
b.cfg.max_drawdown_pct, b.initial_balance = 0.40, 100000
kills = [b.kill_switch(65000), b.kill_switch(61000), b.kill_switch(59000)]
b.running = True
kills.append(b.kill_switch(59000))
check("A3 max_drawdown_pct 0.40 works: no kill at 65k / 61k, kill at 60k floor after 2 readings < 60k",
      kills == [False, False, False, True], kills)

# ============================================================================================ A1 compute_quote
print("--- A1 value floor in compute_quote (normal and reduce-only, skew and age skew)")
P = 0.62
q0 = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12)
q1 = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12, p=P, value_mode=True)
check("fail-before: reduce-only long with skew + age skew rests its ask below p - margin (keep limit 0.57)",
      q0.ask < P - 0.005 and q0.ask_limit < P - 0.005, q0)
check("value_mode: reduce-only ask and its keep limit >= p - margin (0.615)", q1.ask >= 0.615 - 1e-9
      and q1.ask_limit >= 0.615 - 1e-9, q1)
check("value_mode: the ask size is unchanged (only the price moves; never flips)", q1.ask_size == q0.ask_size, (q0, q1))
qn0 = cq(2000, 0.60, 0.50, 0.70, age=12)
qn1 = cq(2000, 0.60, 0.50, 0.70, age=12, p=P, value_mode=True)
check("normal quoting: ask floored too (and the adding bid unchanged)", qn1.ask >= 0.615 - 1e-9 and qn1.bid == qn0.bid
      and qn1.bid_size == qn0.bid_size, (qn0, qn1))
qs0 = cq(-2000, 0.40, 0.30, 0.50, ro=True, age=12)
qs1 = cq(-2000, 0.40, 0.30, 0.50, ro=True, age=12, p=0.38, value_mode=True)
check("short mirror: base buys back above p + margin; value_mode bid and keep limit <= 0.385",
      qs0.bid > 0.385 and qs1.bid <= 0.385 + 1e-9 and qs1.bid_limit <= 0.385 + 1e-9 and qs1.bid_size == qs0.bid_size,
      (qs0, qs1))
q = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12, p=P, value_mode=True, value_sell_margin=0.02)
check("margin 2c: the floor is ceil(p - 0.02) = 0.60", abs(q.ask - 0.60) < 1e-9, q)
q = cq(2000, 0.60, 0.50, 0.58, ro=True, p=0.75, value_mode=True)
check("p far above the book: the ask rests at 0.745, beyond the best ask (never crosses the best bid 0.50)",
      abs(q.ask - 0.745) < 1e-9 and q.ask > 0.50, q)
q = cq(2000, 0.60, 0.66, 0.70, ro=True, p=0.50, value_mode=True)
check("floor below the best bid: step 4 keeps the ask above the best bid (0.665)", q.ask >= 0.665 - 1e-9, q)
q = cq(-2000, 0.40, 0.30, 0.34, ro=True, p=0.45, value_mode=True)
check("short, p above the book: the bid stays below the best ask (no crossing)", q.bid is None or q.bid < 0.34, q)
q0, q1 = cq(0, 0.60, 0.50, 0.70), cq(0, 0.60, 0.50, 0.70, p=P, value_mode=True)
check("flat: value_mode changes nothing (no reducing side)", q0 == q1 and q0.bid_limit == q1.bid_limit
      and q0.ask_limit == q1.ask_limit, (q0, q1))
q = cq(-2000, 0.003, None, 0.02, ro=True, p=0.0, value_mode=True, value_sell_margin=0.0)
check("short with p + margin below the grid: no bid (nothing to buy back at or below value)", q.bid is None, q)
q0 = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12, reduce_join_best=True, reduce_join_min_edge=-0.05)
q1 = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12, p=P, value_mode=True, reduce_join_best=True,
        reduce_join_min_edge=-0.05, exit_quotes_in_reduce_only=True)
check("reduce_join_best on: the joined ask is floored too", q1.ask >= 0.615 - 1e-9, (q0, q1))
c_ = cfg_with(value_mode=True)
q = M.compute_quote(0.60, 2000, 2000, 0.50, 0.70, c_, bankroll=100000, unload_side="ask", unload_edge=0.0,
                    unload_size=500, value_p=P)
check("fast unload window: the unload ask is floored (size still <= the position)", q.ask >= 0.615 - 1e-9
      and q.ask_size <= 2000, q)
q = M.compute_quote(0.60, 2000, 2000, 0.50, 0.70, c_, bankroll=100000, reduce_fv=0.52, value_p=P)
check("reduce_from_book: the book-priced ask is floored", q.ask >= 0.615 - 1e-9, q)
qa = cq(2000, 0.60, 0.50, 0.70, ro=True, age=12, value_mode=True)
check("value_mode without a liquid p: only the max_skew_through clamp now runs in reduce-only (keep limit >= fv 0.60)",
      qa.ask_limit >= 0.60 - 1e-9 and q0.ask_limit < 0.60, (qa, q0))
check("value_side_prices: long -> (None, ceil(p - m)); short -> (floor(p + m), None); flat -> (None, None)",
      M.value_side_prices(0.62, 5, c_) == (None, 0.615) and M.value_side_prices(0.38, -5, c_) == (0.385, None)
      and M.value_side_prices(0.5, 0, c_) == (None, None))
hq = M.Quote(bid=0.50, bid_size=100, ask=0.58, ask_size=300, bid_limit=0.52, ask_limit=0.57)
fq = M.value_floor_quote(hq, P, 300, c_)
check("value_floor_quote (after hold_quote): ask 0.58 -> 0.615, keep limit too, bid / sizes unchanged",
      (fq.ask, fq.ask_limit, fq.ask_size, fq.bid, fq.bid_size) == (0.615, 0.615, 300, 0.50, 100), fq)
check("value_floor_quote: an ask already above the floor is untouched; NO_QUOTE passes",
      M.value_floor_quote(hq, 0.55, 300, c_) == hq and M.value_floor_quote(M.NO_QUOTE, P, 300, c_) is M.NO_QUOTE)

print("--- A1 (iii) exit_quote floor")
e0 = M.exit_quote(0.60, 1000, 0.50, 0.70, c_)
e1 = M.exit_quote(0.60, 1000, 0.50, 0.70, c_, value_floor=P)
check("long exit: base sells at fv - 3c (0.57); with the floor at 0.615", abs(e0.ask - 0.57) < 1e-9
      and abs(e1.ask - 0.615) < 1e-9 and e1.ask_size == e0.ask_size, (e0, e1))
e0 = M.exit_quote(0.40, -1000, 0.30, 0.50, c_)
e1 = M.exit_quote(0.40, -1000, 0.30, 0.50, c_, value_floor=0.38)
check("short exit: base buys back at 0.43; with the floor at 0.385", abs(e0.bid - 0.43) < 1e-9
      and abs(e1.bid - 0.385) < 1e-9, (e0, e1))
check("exit_quote value_floor None = unchanged", M.exit_quote(0.60, 1000, 0.50, 0.70, c_, value_floor=None)
      == M.exit_quote(0.60, 1000, 0.50, 0.70, c_))

print("--- A1 + A4 property grid: never crossing, the floor and hurdle prices hold, reduce-only never flips")
ok_x = ok_f = ok_h = ok_r = True
bad_ = []
for inv in (-3000, -600, 0, 400, 2500):
    for fv, bb, ba in ((0.60, 0.50, 0.70), (0.08, 0.06, 0.10), (0.93, 0.90, 0.96), (0.50, 0.48, 0.52),
                       (0.30, None, 0.35), (0.70, 0.65, None), (0.60, 0.59, 0.60)):
        for ro in (False, True):
            for age in (0.0, 12.0):
                for dp in (-0.06, -0.01, 0.0, 0.03, 0.08):
                    p = min(0.99, max(0.01, fv + dp))
                    for h in (0.0, 0.08):
                        q = cq(inv, fv, bb, ba, ro=ro, age=age, p=p, value_mode=True, value_quote_hurdle=h)
                        if ((q.ask is not None and bb is not None and q.ask <= bb + 1e-9)
                                or (q.bid is not None and ba is not None and q.bid >= ba - 1e-9)):
                            ok_x = False
                            bad_.append(("cross", inv, fv, bb, ba, ro, p, h, q))
                        fb, fa = M.value_side_prices(p, inv, M.Config())
                        if ((fa is not None and q.ask is not None and (q.ask < fa - 1e-9 or q.ask_limit < fa - 1e-9))
                                or (fb is not None and q.bid is not None and (q.bid > fb + 1e-9
                                                                            or q.bid_limit > fb + 1e-9))):
                            ok_f = False
                            bad_.append(("floor", inv, fv, p, q))
                        if h and not 0.15 <= p <= 0.85 and (
                                (inv > -1 and q.bid is not None and q.bid > p / (1 + h) + 1e-9)
                                or (inv < 1 and q.ask is not None and q.ask < 1 - (1 - p) / (1 + h) - 1e-9)):
                            ok_h = False
                            bad_.append(("hurdle", inv, fv, p, q))
                        if ro and ((inv >= 0 and q.bid_size) or (inv <= 0 and q.ask_size)
                                   or q.ask_size > max(0, inv) or q.bid_size > max(0, -inv)):
                            ok_r = False
                            bad_.append(("flip", inv, fv, p, q))
check("value_mode + hurdle on a 2,800-point grid: no quote crosses the best other price", ok_x, bad_[:2])
check("...every reducing price and keep limit respects p -+ margin", ok_f, bad_[:2])
check("...every adding price in the tails respects the hurdle price", ok_h, bad_[:2])
check("...reduce-only sizes never exceed (never flip) the position", ok_r, bad_[:2])

# ============================================================================================ A1 in the bot
print("--- A1 in decide: race-scaled liquid p, reduce-only, final floor")
refs_ns = dict(REFS, **{"Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.50})   # sum 1.05 -> 0.5238
a, b = plain_bot({"21": 3000}, refs=refs_ns, max_worst_case_frac=0.001, worst_case_backstop_frac=0.002,
                 value_sell_margin=0.0)
quiet_cycle(b)
q = b.ex["21"].quote
ask_off = min(q.ask, q.ask_limit) if q.ask is not None else None   # (the lowest a resting ask is priced / kept at)
FL = M.ceil_tick(0.55 / 1.05)                                                    # 0.525 (margin 0 here)
check("value_p: race-scaled (0.55 / 1.05 = 0.5238), only when liquid",
      abs(b.value_p(b.ex["21"], 0.55, True) - 0.55 / 1.05) < 1e-9 and b.value_p(b.ex["21"], 0.55, False) is None)
check("fail-before: global reduce-only, the long Utah Rep ask may rest below the floor 0.525 (keep limit)", b.global_reduce
      and ask_off is not None and ask_off < FL, ask_off)
b.cfg.value_mode = True
quiet_cycle(b)
q = b.ex["21"].quote
check("value_mode: the same ask and keep limit >= 0.525 (scaled p, margin 0)", q.ask is not None
      and min(q.ask, q.ask_limit) >= FL - 1e-9, q)
b.refs.spread = 0.5                                   # Polymarket illiquid now
quiet_cycle(b)
q = b.ex["21"].quote
check("no liquid p: no floor (the keep limit is below 0.525 again: only the fv clamp)", q.ask is not None and min(q.ask, q.ask_limit) < FL,
      q)

print("--- A1 (ii) the pre-close windows do nothing in value mode")
for hrs, what in ((1.0, "exit window"), (5.0, "per-market flatten"), (10.0, "flatten window")):
    a, b = plain_bot({"21": 600}, close_h=hrs)
    quiet_cycle(b)
    q_off = b.ex["12"].quote, b.ex["21"].quote
    a2, b2 = plain_bot({"21": 600}, close_h=hrs, value_mode=True)
    quiet_cycle(b2)
    q_on = b2.ex["12"].quote, b2.ex["21"].quote
    check(f"{what} ({hrs:g} h): off -> flat Ohio Dem quotes nothing, Utah Rep only sells; on -> two-way quotes",
          q_off[0].bid is None and q_off[0].ask is None and q_off[1].bid is None and q_off[1].ask is not None
          and q_on[0].bid is not None and q_on[0].ask is not None and q_on[1].bid is not None, (q_off, q_on))
a, b = plain_bot({}, close_h=1.0)
check("close_window: the setting when off, -inf in value_mode",
      b.close_window("exit_hours_before_close") == 2.0 and b.close_window("flatten_per_market_hours") == 6.0)
b.cfg.value_mode = True
check("close_window in value_mode: -inf for all three",
      all(b.close_window(n) == float("-inf") for n in ("exit_hours_before_close", "flatten_hours_before_close",
                                                         "flatten_per_market_hours")))
b.cfg.value_mode = False
quiet_cycle(b)
b.phase = "trading"
st_off = b.status_report()[0]
b.cfg.value_mode = True
st_on = b.status_report()[0]
check("status phase: 'exiting positions' off, not in value mode", "exiting positions" in st_off
      and "exiting" not in st_on and "reducing" not in st_on, (st_off, st_on))
b.cfg.value_mode = False
b.ex["21"].lad_ctx = (1.0, 1.0, None)
caps_off = b.ladder_caps(b.ex["21"], 0.55, 100)
b.cfg.value_mode = True
caps_on = b.ladder_caps(b.ex["21"], 0.55, 100)
check("ladder caps: exit window zero when off, open in value mode", caps_off[True](0.5) == 0 and caps_on[True](0.5) > 0)
a, b = plain_bot({"21": 600}, close_h=1.0 / 6, value_mode=True)          # closes in 10 minutes
quiet_cycle(b)
check("stop_minutes_before_close (15) still holds in value mode: 10 min to close -> nothing quoted",
      all(x.quote == M.NO_QUOTE for x in b.ex.values()), [x.quote for x in b.ex.values()])

print("--- A1 (iv) warnings at start-up / override time")


class Grab(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


grab = Grab()
M.log.addHandler(grab)
old_level, old_prop = M.log.level, M.log.propagate
M.log.setLevel(logging.WARNING)
M.log.propagate = False
try:
    a, b = plain_bot({}, reduce_from_book=True, ref_guard_exits=True)
    b.warn_settings()
    check("value_mode off: no value warning", not any("value_mode" in m for m in grab.msgs), grab.msgs)
    b.cfg.value_mode = True
    b.warn_settings()
    w = [m for m in grab.msgs if "value_mode is on with" in m]
    check("value_mode on: one warning naming reduce_from_book and ref_guard_exits",
          len(w) == 1 and "reduce_from_book" in w[0] and "ref_guard_exits" in w[0], grab.msgs)
    check("...and one line saying the pre-close windows are off", sum("pre-close windows are OFF" in m
                                                                       for m in grab.msgs) == 1, grab.msgs)
    b.warn_settings()
    check("no repeat while nothing changes", sum("value_mode is on with" in m for m in grab.msgs) == 1)
    for k in ("fast_unload_enabled", "tilt_exit_priority", "tilt_exit_full_size", "tilt_exit_take", "ref_tilt_enabled",
              "take_tilted_ref"):
        setattr(b.cfg, k, True)
    b.cfg.hold_target_hours = 4.0
    b.warn_settings()
    w = [m for m in grab.msgs if "value_mode is on with" in m]
    check("a newly switched-on path warns again, listing all nine",
          len(w) == 2 and all(n in w[1] for n in ("fast_unload_enabled", "hold_target_hours", "tilt_exit_priority",
                                                   "tilt_exit_full_size", "tilt_exit_take", "ref_tilt_enabled",
                                                   "take_tilted_ref")), w)
    check("not forced off (the owner decides)", b.cfg.reduce_from_book and b.cfg.fast_unload_enabled)
    a, b = plain_bot({})
    n0 = len(grab.msgs)
    with open(b.cfg.overrides_file, "w") as f:
        json.dump({"value_mode": True, "ref_guard_exits": True}, f)
    b.check_overrides(force=True)
    check("override time: applying value_mode with ref_guard_exits logs the warning",
          b.cfg.value_mode and any("value_mode is on with" in m and "ref_guard_exits" in m for m in grab.msgs[n0:]),
          grab.msgs[n0:])
finally:
    M.log.removeHandler(grab)
    M.log.setLevel(old_level)
    M.log.propagate = old_prop

# ============================================================================================ A2 bloc delta
print("--- A2 bloc delta: closed form")
check("weights (rho 1): p 0.5 -> 0.399, 0.935 -> 0.127, 0.005 -> 0.0146 (H-2's table)",
      abs(M.bloc_slope(0.5, 1.0) - 0.3989) < 1e-3 and abs(M.bloc_slope(0.935, 1.0) - 0.1268) < 1e-3
      and abs(M.bloc_slope(0.005, 1.0) - 0.0146) < 1e-3)
check("sqrt(rho) and (1 - p_ind) scale it", abs(M.bloc_slope(0.5, 0.45, 0.2) - math.sqrt(0.45) * 0.3989 * 0.8) < 1e-3)
cB = cfg_with()
races = {"Ohio Senate": [("11", "Rep Ohio Senate", 0.30, True), ("12", "Dem Ohio Senate", 0.70, True)],
         "Utah Senate": [("21", "Rep Utah Senate", 0.55, True), ("22", "Dem Utah Senate", 0.45, False)],
         "U.S. House": [("31", "Dem U.S. House", 0.60, True), ("32", "Rep U.S. House", 0.40, True)],
         "Maine Senate": [("41", "Dem Maine Senate", 0.40, True), ("42", "Rep Maine Senate", 0.45, True),
                          ("43", "Ind Maine Senate", 0.15, True)],
         "Vermont Senate": [("51", "Dem Vermont Senate", 0.30, True), ("52", "Ind Vermont Senate", 0.70, True)],
         "Lone Gov": [("61", "Rep Lone Gov", 0.80, True)]}
S = M.bloc_sensitivities(races, cB)
check("sign: Rep YES +, Dem YES -, equal magnitude in a two-party race", S["11"] > 0 > S["12"]
      and abs(S["11"] + S["12"]) < 1e-12, S)
check("an illiquid leg gets none (Utah Dem); its liquid partner does", "22" not in S and "21" in S)
check("headline race uses bloc_rho_control 0.85", abs(S["32"] - math.sqrt(0.85) * M.bloc_slope(0.6, 1.0)) < 1e-9, S)
check("independents excluded; the Maine Dem uses p_dem = 0.40 / 0.85 and x (1 - 0.15)",
      "43" not in S and abs(-S["41"] - M.bloc_slope(0.40 / 0.85, 0.45, 0.15)) < 1e-9, S)
check("a race with one partisan leg + an independent counts 0 (H's model); a lone market uses its own p",
      "51" not in S and abs(S["61"] - M.bloc_slope(0.20, 0.45)) < 1e-9, S)

print("--- A2 bloc delta: closed form vs a Monte Carlo slope (toy book, Gaussian copula)")
toy = {"11": 1000, "12": -500, "31": 1500, "32": -700, "41": 800, "42": 300, "61": -900}
closed = sum(q * S.get(e, 0.0) for e, q in toy.items())
nd = M._STD_NORMAL
rng = random.Random(11)
N = 40000
sF = sV = sFF = sFV = 0.0
mc_races = [(r, legs) for r, legs in races.items() if r not in ("Vermont Senate", "Utah Senate")]
for _ in range(N):
    F = rng.gauss(0, 1)
    val = 0.0
    for race, legs in mc_races:
        rho = cB.bloc_rho_control if race in cB.headline_races else cB.bloc_rho
        p = {lab.split()[0]: (e, pp) for e, lab, pp, _ in legs}
        pI = p["Ind"][1] if "Ind" in p else 0.0
        if "Dem" in p:
            pdem = p["Dem"][1] / (1 - pI)
        else:
            pdem = 1 - p["Rep"][1]
        ind = rng.random() < pI
        z = math.sqrt(rho) * F + math.sqrt(1 - rho) * rng.gauss(0, 1)
        dem = (z < nd.inv_cdf(pdem)) and not ind
        for kind, (e, _) in p.items():
            win = dem if kind == "Dem" else ((not dem) and not ind) if kind == "Rep" else ind
            q = toy.get(e, 0.0)
            val += q * win if q > 0 else -q * (1 - win)
    sF += F
    sV += val
    sFF += F * F
    sFV += F * val
slope = (sFV - sF * sV / N) / (sFF - sF * sF / N)
check(f"closed form {closed:+.0f}/sd vs MC slope {slope:+.0f}/sd within 3%", abs(closed - slope) <= 0.03 * abs(slope),
      (closed, slope))

print("--- A2 bloc delta in the bot: the cap replaces the share count")
# pin_test_sizes: share cap 0.02 x 100k = 2,000 shares. Long 3,000 Ohio Dem YES at 0.88: share delta -3,000 (blocked),
# bloc delta 3,000 x -sqrt(0.45) x phi(Phi^-1(0.88)) = -403/sd (inside 0.05 x 100k = 5,000).
a, b = plain_bot({"12": 3000})
quiet_cycle(b)
blk_off = b.party_blocks(b.ex["12"], -3000.0)
check("fail-before: the share cap blocks buying more Dem (Ohio Dem bid)", blk_off == (True, False), blk_off)
b.cfg.bloc_delta_enabled = True
quiet_cycle(b)
check("bloc delta = 3000 x sensitivity (-403/sd)", abs(b.bloc_delta - 3000 * -math.sqrt(0.45) * M.bloc_slope(0.88, 1.0))
      < 0.5, b.bloc_delta)
check("flag on: nothing blocked (|-403| < 5,000)", b.party_blocks(b.ex["12"], -3000.0) == (False, False))
check("party_shift from bloc / cap: Dem quotes shade by 0.015 x -1 x (-403 / 5,000)",
      abs(b.party_shift(b.ex["12"], -3000.0) - 0.015 * -1 * (b.bloc_delta / 5000)) < 1e-12)
check("status health: bloc_delta and bloc_delta_frac (signed, / account)", b.health.get("bloc_delta") ==
      round(b.bloc_delta, 2) and abs(b.health.get("bloc_delta_frac") - b.bloc_delta / 100000) < 1e-4, b.health)
b.write_status(True)
with open(b.cfg.status_file) as f:
    st = json.load(f)
check("status.json carries bloc_delta / bloc_delta_frac", "bloc_delta" in json.dumps(st)
      and "bloc_delta_frac" in json.dumps(st))
line = b.summary_ops_line(100000) or ""
check("2-hourly summary: ' | bloc delta -403/sd'", "bloc delta -403/sd" in line, line)
check("bloc_delta_now on a hypothetical book (Part B's hook) and bloc_cap",
      abs(b.bloc_delta_now({"12": 1000}) - b.bloc_delta / 3) < 1e-6 and b.bloc_cap() == 5000.0)
b.cfg.max_bloc_delta_frac = 0.003                    # cap 300/sd
check("past the bloc cap: Dem bid and Rep ask blocked, the reducing sides open",
      b.party_blocks(b.ex["12"], 0.0) == (True, False) and b.party_blocks(b.ex["11"], 0.0) == (False, True))
b.ex["12"].lad_ctx = (1.0, 1.0, None)
cap12 = b.ladder_caps(b.ex["12"], 0.85, 100)
b.ex["21"].lad_ctx = (1.0, 1.0, None)
cap21 = b.ladder_caps(b.ex["21"], 0.55, 100)
check("ladder room in bloc units (cap 300, bloc -403): Dem bid -103 / 0.134 = -764 shares; Dem ask and Rep bid open; "
      "Rep ask (more Dem-leaning) shut", abs(cap12[True](0.85) - (300 - 402.574) / 0.134191) < 1 and cap12[False](0.85) > 0
      and cap21[True](0.55) > 0 and cap21[False](0.55) <= 0, (cap12[True](0.85), cap21[False](0.55)))
a, b = plain_bot({"12": 3000})
quiet_cycle(b)
check("flag off: no bloc keys in health / summary", "bloc_delta" not in b.health
      and "bloc delta" not in (b.summary_ops_line(100000) or ""))

# ============================================================================================ A4 hurdle
print("--- A4 value_quote_hurdle")
H8 = 0.08
qf = cq(0, 0.92, 0.85, 0.87, p=0.95, value_quote_hurdle=H8)
check("tails, favourite p 0.95 on a tilted book (0.85 / 0.87): the bid rests at the touch (0.865 <= p / 1.08)",
      abs(qf.bid - 0.865) < 1e-9 and qf.bid <= 0.95 / 1.08, qf)
check("...the adding ask sits at >= 1 - 0.05 / 1.08 = 0.9537 -> 0.955, beyond the book (out of reach): one-sided",
      abs(qf.ask - 0.955) < 1e-9 and qf.ask > 0.87 + 0.05, qf)
ql = cq(0, 0.10, 0.13, 0.15, p=0.05, value_quote_hurdle=H8)
check("tails, longshot p 0.05 on a tilted book (0.13 / 0.15): the ask rests inside the best ask (0.14, >= the hurdle "
      "price 0.1204)", 0.1204 <= ql.ask < 0.15, ql)
check("...the adding bid <= p / 1.08 = 0.046 -> 0.045, far behind the best bid", ql.bid is None or ql.bid <= 0.045 + 1e-9,
      ql)
qq = cq(0, 0.30, 0.20, 0.40, p=0.30, value_quote_hurdle=0.5, value_mid_low=0.35)
check("hurdle 0.5 at p 0.30 (band from 0.35): bid <= 0.20, ask >= 1 - 0.70 / 1.5 = 0.5334 -> 0.535",
      qq.bid <= 0.20 + 1e-9 and qq.ask >= 0.535 - 1e-9 and qq.bid_limit <= 0.20 + 1e-9, qq)
q = cq(0, 0.004, None, 0.02, p=0.004, value_quote_hurdle=H8)
check("hurdle price below the grid (p 0.004 / 1.08): no bid", q.bid is None, q)
q0, q1 = cq(0, 0.50, 0.45, 0.55), cq(0, 0.50, 0.45, 0.55, p=0.50, value_quote_hurdle=H8)
check("middle band (p 0.50): two-way at the normal min_edge prices (identical to off when flat)",
      q0 == q1 and q1.bid is not None and q1.ask is not None, (q0, q1))
qa = cq(1000, 0.50, 0.45, 0.55, p=0.50, value_quote_hurdle=H8)
qb = cq(600, 0.50, 0.45, 0.55, p=0.50, value_quote_hurdle=H8)
check("middle cap 2 x quote size (500): long 1,000 -> no adding bid, the ask rests; long 600 -> bid <= 400",
      qa.bid is None and qa.ask is not None and qb.bid_size <= 400 and qb.bid_size > 0, (qa, qb))
qs = cq(-1000, 0.50, 0.45, 0.55, p=0.50, value_quote_hurdle=H8)
check("middle cap, short 1,000: no adding ask, the bid rests", qs.ask is None and qs.bid is not None, qs)
q0 = cq(1000, 0.50, 0.45, 0.55, p=0.50, value_quote_hurdle=H8, value_mid_inventory_quotes=20.0)
check("middle cap 20 quotes: the long 1,000 bids again", q0.bid is not None, q0)
qr0 = cq(1000, 0.92, 0.85, 0.87, p=0.95)
qr1 = cq(1000, 0.92, 0.85, 0.87, p=0.95, value_quote_hurdle=H8)
check("tails: the REDUCING side (long's ask) is not moved by the hurdle", qr0.ask == qr1.ask and qr0.ask_size ==
      qr1.ask_size and qr1.bid <= 0.95 / 1.08, (qr0, qr1))
qr2 = cq(1000, 0.92, 0.85, 0.87, p=0.95, value_quote_hurdle=H8, value_mode=True)
check("...with value_mode it is A1's: ask >= 0.945", qr2.ask >= 0.945 - 1e-9, qr2)
q = cq(0, 0.92, 0.85, 0.87, p=None, value_quote_hurdle=H8)
check("no liquid p: the hurdle does nothing", q == cq(0, 0.92, 0.85, 0.87))
a, b = plain_bot({}, value_quote_hurdle=H8)
quiet_cycle(b)
q = b.ex["11"].quote                                   # Ohio Rep p 0.12 (tail? no: 0.12 < 0.15 -> tail)
check("in the bot: Ohio Rep (p 0.12, a tail) bid <= 0.12 / 1.08", q.bid is None or q.bid <= 0.12 / 1.08 + 1e-9, q)

# ============================================================================================ flags off = base
print(f"--- flags off identical to the branch head ({BASE_REV}) on a grid")
base = None
try:
    src = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", f"{BASE_REV}:mm_bot.py"], capture_output=True,
                         text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p10base.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p10base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert, base.notify = M.alert, (lambda *a_, **k: False)
except Exception as e:                            # no git here: reported as a failure
    print("    (base module unavailable:", e, ")")
check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)

if base is not None:
    same_q, same_e, n = True, True, 0
    for inv in (-3000, -600, 0, 400, 2500):
        for fv, bb, ba in ((0.60, 0.50, 0.70), (0.08, 0.06, 0.10), (0.93, 0.90, 0.96), (0.50, 0.48, 0.52),
                           (0.30, None, 0.35), (0.70, 0.65, None)):
            for ro in (False, True):
                for age in (0.0, 12.0):
                    for p in (None, fv - 0.04, fv + 0.04):
                        qn = cq(inv, fv, bb, ba, ro=ro, age=age, p=p)
                        qb = cq(inv, fv, bb, ba, ro=ro, age=age, mod=base)
                        n += 1
                        if astuple(qn) != astuple(qb):    # (every field, keep limits and max sizes too)
                            same_q = False
            if astuple(M.exit_quote(fv, inv, bb, ba, M.Config())) != astuple(base.exit_quote(fv, inv, bb, ba,
                                                                                              base.Config())):
                same_e = False
    check(f"compute_quote (normal + reduce-only, age skew, value_p given, flags off) identical on {n} points", same_q)
    check("exit_quote identical on the grid", same_e)

    def twin(inv, close_h, reduce, overrides=None):
        out = []
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
            for x in bn.ex.values():
                x.close = NOW + timedelta(hours=close_h)
            quiet_cycle(bn, 2)
            out.append((api_x, bn))
        return out

    def strip(w):
        return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]

    ok_w = ok_q = ok_s = ok_p = True
    diffs = []
    for inv in ({}, {"11": 300, "21": 3000, "22": -3000}, {"12": 2500}, {"21": -800, "22": -900}):
        for close_h in (1.0, 5.0, 10.0, 700.0):
            for reduce in (False, True):
                (an, bn), (ab, bb_) = twin(inv, close_h, reduce)
                if strip(an.wire) != strip(ab.wire):
                    ok_w = False
                    diffs.append(("wire", inv, close_h, reduce))
                qn = {e: astuple(x.quote) for e, x in bn.ex.items()}
                qb = {e: astuple(x.quote) for e, x in bb_.ex.items()}
                if qn != qb:
                    ok_q = False
                    diffs.append(("quote", inv, close_h, reduce, qn, qb))
                for e in bn.ex:
                    for pdv in (-3000.0, 0.0, 3000.0):
                        if (bn.party_blocks(bn.ex[e], pdv) != bb_.party_blocks(bb_.ex[e], pdv)
                                or bn.party_shift(bn.ex[e], pdv) != bb_.party_shift(bb_.ex[e], pdv)):
                            ok_p = False
                bn.write_status(True)
                bb_.write_status(True)
                with open(bn.cfg.status_file) as f:
                    kn = set(json.load(f))
                with open(bb_.cfg.status_file) as f:
                    kb = set(json.load(f))
                if kn != kb or set(bn.health) != set(bb_.health) or bn.status_report() != bb_.status_report():
                    ok_s = False
                    diffs.append(("status", kn ^ kb, set(bn.health) ^ set(bb_.health)))
    check("two full cycles send the same orders (4 books x close in 1 / 5 / 10 / 700 h x reduce-only)", ok_w, diffs[:1])
    check("every market's quote (and keep limits) identical, incl. exit / flatten windows", ok_q, diffs[:1])
    check("party_blocks / party_shift identical (share cap)", ok_p)
    check("status.json / health keys and the status line identical", ok_s, diffs[:1])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
