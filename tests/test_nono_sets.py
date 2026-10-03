"""
Offline tests for Package 7 "NO+NO sets". Live 3 Oct 14:00: 47 of the 49 remaining 400 "Insufficient available
funds" refusals were covered NO sales (reduce_no_as_sell) on races where we hold NO on EVERY leg: the exchange
collateralises NO+NO as a set, so selling NO on ONE leg breaks the set and needs cash. Covers:
  A no_set_aware_bids: covered bids / takes capped at the lone part (2-leg, 3-leg, a long leg), no refusals at 0 cash;
  B pair_no_unwind_max_cost: arb_plan's short-set buy-back at asks <= 1 + max_cost, both legs covered in one batch,
    never half-converted;
  C the start-up paired-sale check (pairno_tick / pairno_run / pairno_finish), refusal, busy back-off, overlap guards;
  D T2.5 passive short-set branch skipped with reduce_no_as_sell;
  E status.json nono_sets and the summary part;
  the fake exchange's set-collateral rule; flags off identical to the Package 6 code (706e763) on a grid.

Run:  python tests/test_nono_sets.py      (exit code 0 = all passed)
"""
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
A, B = "no_set_aware_bids", "pair_no_unwind_max_cost"
THIRD = market("5", "13", "Independent", "Ohio Senate")   # makes Ohio a 3-leg race (11, 12, 13)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def no_sells(api, eid=None):
    return [o for o in api.wire if o["side"] == "no" and o["action"] == "sell" and (eid is None or o["exchangeId"] == eid)]


def bot(inv=None, cash=None, reduce=True, a=False, b=-1.0, three=False, live=True):
    api, bt = make_bot(live=live, extra_markets=(THIRD,) if three else ())
    if three:
        api.books["13"] = {"bids": [lvl(0.02, 1000)], "asks": [lvl(0.06, 1000)]}
    bt.cfg.reduce_no_as_sell = reduce
    bt.cfg.no_set_aware_bids = a
    bt.cfg.pair_no_unwind_max_cost = b
    bt.cfg.selftest_enabled = False           # the check leg has its own tests below
    api.inv = dict(inv or {})
    api.cash = cash
    api.set_collateral = cash is not None
    for e, q in api.inv.items():
        if e in bt.ex:
            bt.ex[e].inv = float(q)
    return api, bt


def with_books(bt):
    for e, x in bt.ex.items():
        x.book = bt.api.full_book(e) if e in bt.api.books else None
    return bt


def record_results(api):
    """Wrap place_batch: every (order, result) pair is kept in api.res_log."""
    api.res_log = []
    real = api.place_batch

    def pb(orders):
        res = real(orders)
        api.res_log += list(zip([dict(o) for o in orders], res))
        return res
    api.place_batch = pb
    return api


def refused_no_sells(api):
    return [(o, r) for o, r in api.res_log if o["side"] == "no" and o["action"] == "sell" and not r.get("ok")]


def cyc(bt, n=1):
    for _ in range(n):
        bt.cycle()
        bt.drain_writes(5)


now = M.utcnow()

print("--- settings")
c = M.Config()
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("defaults: no_set_aware_bids False, pair_no_unwind_max_cost -1 (off)",
      c.no_set_aware_bids is False and c.pair_no_unwind_max_cost == -1.0)
_p7b = ["pair_unwind_followup", "pair_unwind_followup_max_cost", "pair_unwind_followup_tries",   # Package 7 part 2
        "pair_no_unwind_max_per_cycle",                                                         # (red-team fix 3)
        "cash_gate_enabled", "cash_gate_reserve", "adding_factor_per_market"]                   # (then Package 8's)
check("one contiguous block in Config and OVERRIDABLE (later packages may follow)",
      _f[_f.index(A):_f.index(A) + 2] == [A, B] and _ov[_ov.index(A):_ov.index(A) + 2] == [A, B]
      and all(k in _f and k in _ov for k in _p7b), (_f[-5:], _ov[-5:]))
good, bad = M.validate_overrides({A: True, B: 0.003}, c)
check("live overrides accepted (True, 0.003)", good == {A: True, B: 0.003} and not bad, (good, bad))
good, bad = M.validate_overrides({B: -1.0}, c)
check("-1 (off) accepted live", good == {B: -1.0} and not bad, bad)
good, bad = M.validate_overrides({B: 0.2}, c)
check("0.2 refused (range -1..0.05)", not good and bad, bad)

print("--- A: covered bids capped at the lone part")
api, bt = bot({"21": -817, "22": -975}, a=True)
check("2-leg race -817 / -975: covered bids 0 and 158 (quote level)",
      (bt.cover_no_qty("21", -817, 0), bt.cover_no_qty("22", -975, 0)) == (0, 158),
      (bt.cover_no_qty("21", -817, 0), bt.cover_no_qty("22", -975, 0)))
check("...and for takes (level None) too", (bt.cover_no_qty("21", -817), bt.cover_no_qty("22", -975)) == (0, 158))
check("...a whole-race batch (sets_ok) still sells all the NO held",
      (bt.cover_no_qty("21", -817, sets_ok=True), bt.cover_no_qty("22", -975, sets_ok=True)) == (817, 975))
check("smallest leg is set-blocked, the other is not", bt.set_blocked("21", -817.0) and not bt.set_blocked("22", -975.0))
api, bt = bot({"21": -817, "22": -975}, a=False)
check("flag off: 817 and 975 (Package 6)", (bt.cover_no_qty("21", -817, 0), bt.cover_no_qty("22", -975, 0)) == (817, 975))
api, bt = bot({"11": -300, "12": -500, "13": -200}, a=True, three=True)
check("3-leg race -300 / -500 / -200 (worst-case model, lone = n - max other): 0, 200, 0",
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12", "13")] == [0, 200, 0],
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12", "13")])
api, bt = bot({"11": -10, "12": -8, "13": -5}, a=True, three=True)
check("3-leg race [10, 8, 5]: lone [2, 0, 0] (the middle leg sells nothing)",
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12", "13")] == [2, 0, 0],
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12", "13")])
api, bt = bot({"11": -300, "12": -500, "13": 50}, a=True, three=True)
check("3-leg race, NO on 2 of 3 legs (the third long): capped too (0, 200)",
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12")] == [0, 200],
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12")])
api, bt = bot({"11": -300, "12": -500}, a=True, three=True)
check("3-leg race, NO on 2 of 3 legs (the third flat): capped (0, 200)",
      [bt.cover_no_qty(e, bt.ex[e].inv, 0) for e in ("11", "12")] == [0, 200]
      and bt.set_blocked("11", -300.0) and not bt.set_blocked("12", -500.0))
api, bt = bot({"21": -817, "22": 975}, a=True)
check("2-leg race with one long leg: unchanged (817)", bt.cover_no_qty("21", -817, 0) == 817)
api, bt = bot({"21": -817}, a=True)
check("other leg flat: unchanged (817)", bt.cover_no_qty("21", -817, 0) == 817)
api, bt = bot({"21": -817, "22": -975}, a=True, reduce=False)
check("reduce_no_as_sell off: nothing covered (0, 0)", (bt.cover_no_qty("21", -817, 0), bt.cover_no_qty("22", -975, 0)) == (0, 0))

api, bt = bot({"21": -817, "22": -975}, a=True)
o, m = bt.new_order(bt.ex["22"], True, 0.40, 500, 0.45, now)
check("new_order on the bigger leg: covered sale of 158", o.get("_no_sell") and o["quantity"] == 158, o)

print("--- A: quote path end to end, 0 free cash, fake with the set-collateral rule")
for a_on in (False, True):
    api, bt = bot({"21": -817, "22": -975}, cash=0.0, a=a_on)
    record_results(api)
    bt.cfg.fail_pause_seconds = 0
    cyc(bt, 3)
    ref = refused_no_sells(api)
    if a_on:
        check("on: no 'sell NO' refused in 3 cycles (the smaller leg gets no bid, the bigger <= 158)",
              not ref and not no_sells(api, "21") and all(o["quantity"] <= 158 for o in no_sells(api, "22"))
              and no_sells(api, "22"), (ref, no_sells(api)))
        check("on: no bid of ours rests on the smaller leg", not [x for x in api.ours("21") if x[0] == "bid"],
              api.ours("21"))
    else:
        check("off: no refusal when both legs' covered sales go out in the SAME batch (they close whole sets)",
              not ref, ref)
        api.orders.clear()
        o, _m = bt.new_order(bt.ex["21"], True, 0.40, 100, 0.45, now)
        res = bt.place_orders([o])
        check("off: the smaller leg's covered sale on its own breaks the set and is refused (the live finding)",
              o.get("_no_sell") and not res[0]["ok"] and "Insufficient" in res[0]["data"]["error"]["message"], res)

print("--- A: take paths")
api, bt = bot({"21": -817, "22": -975}, a=True)
ex = bt.ex["21"]
ex.book = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}
ex.take_dir = 1
r = bt.execute_take(ex, 0.70, -817.0, 0.6, time.monotonic())
check("execute_take buying back on the smaller (all-set) leg: nothing sent, no cancel", r is False and not api.wire
      and not api.sent("cancel_all"), (r, api.wire, api.calls[-3:]))
ex = bt.ex["22"]
ex.book = {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}
ex.take_dir = 1
bt.execute_take(ex, 0.70, -975.0, 0.6, time.monotonic())
t = api.wire[-1] if api.wire else {}
check("execute_take on the bigger leg: sell NO capped at the lone 158", t.get("side") == "no" and t.get("quantity") == 158
      if t.get("quantity", 0) >= 158 else (t.get("side") == "no" and 1 <= t.get("quantity", 0) <= 158), t)
api, bt = bot({"21": -817, "22": -975}, a=True)
bt.cfg.hold_target_hours, bt.hold_open_at = 1.0, -1e18
bt.hold_take_plan = lambda *a_, **k: [{"eid": "22", "buy": True, "qty": 500, "price": 0.52, "notional": 260.0}]
bt.take_aged({"21": -817.0, "22": -975.0}, {"22": 0.5}, {}, time.monotonic())
t = api.wire[-1] if api.wire else {}
check("take_aged on the bigger leg: sell NO x158", t.get("side") == "no" and t.get("quantity") == 158, t)
api, bt = bot({"21": -817, "22": -975}, a=True)
bt.cfg.hold_target_hours, bt.hold_open_at = 1.0, -1e18
bt.hold_take_plan = lambda *a_, **k: [{"eid": "21", "buy": True, "qty": 500, "price": 0.56, "notional": 280.0}]
bt.take_aged({"21": -817.0, "22": -975.0}, {"21": 0.5}, {}, time.monotonic())
check("take_aged on the smaller leg: nothing sent", not api.wire, api.wire)
check("no_sell_order on the smaller leg: None (not sent); whole=True: converted (a whole-race batch)",
      bt.no_sell_order({"exchangeId": "21", "action": "buy", "quantity": 10, "price": 0.5}, -817.0) is None
      and bt.no_sell_order({"exchangeId": "21", "action": "buy", "quantity": 817, "price": 0.5}, -817.0,
                           whole=True).get("_no_sell"))

print("--- B: arb_plan's short-set buy-back")


def arb_bot(asks, inv=None, b=0.003, reduce=True, a=False, live=True, bids=None):
    api, bt = bot(inv or {"21": -817, "22": -975}, a=a, b=b, reduce=reduce, live=live)
    for e, p in asks.items():
        bt.ex[e].book = {"bids": [lvl((bids or {}).get(e, round(p - 0.04, 3)), 1000)], "asks": [lvl(p, 1000)]}
    inv_d = {e: bt.ex[e].inv for e in bt.ex}
    return api, bt, inv_d


FVS = {"11": 0.15, "12": 0.85, "21": 0.5, "22": 0.5}
api, bt, inv = arb_bot({"21": 0.50, "22": 0.502})
plan = bt.arb_plan(["21", "22"], inv, FVS, False)
check("asks 1.002, max_cost 0.003: the buy-back, sized to the smaller leg (817)",
      plan is not None and plan[:2] == ("unwind", "buy") and plan[3] == 817, plan)
api, bt, inv = arb_bot({"21": 0.50, "22": 0.504})
check("asks 1.004: none", bt.arb_plan(["21", "22"], inv, FVS, False) is None)
api, bt, inv = arb_bot({"21": 0.50, "22": 0.502}, b=-1.0)
check("asks 1.002 with the setting -1: none (today's rule)", bt.arb_plan(["21", "22"], inv, FVS, False) is None)
api, bt, inv = arb_bot({"21": 0.50, "22": 0.502}, reduce=False)
check("asks 1.002 without reduce_no_as_sell: none (it could only be a cash purchase)",
      bt.arb_plan(["21", "22"], inv, FVS, False) is None)
api, bt, inv = arb_bot({"21": 0.49, "22": 0.50}, b=-1.0)
check("asks 0.99 with the setting -1: the buy-back as today", (bt.arb_plan(["21", "22"], inv, FVS, False) or ())[:2]
      == ("unwind", "buy"))
api, bt, inv = arb_bot({"21": 0.51, "22": 0.51}, inv={"21": 817, "22": 975}, bids={"21": 0.499, "22": 0.499})
check("long set (YES+YES), bids 0.998, max_cost 0.003: today's rule (none)",
      bt.arb_plan(["21", "22"], inv, FVS, False) is None)
api, bt, inv = arb_bot({"21": 0.50, "22": 0.502})
bt.cfg.selftest_enabled = True
check("live + self-test on, check not run yet: none", bt.arb_plan(["21", "22"], inv, FVS, False) is None)
bt.nosell_state, bt.pairno_state = "ok", "ok"
check("...after both checks passed: the buy-back", (bt.arb_plan(["21", "22"], inv, FVS, False) or ())[:2]
      == ("unwind", "buy"))
bt.pairno_state = "off"
check("...check refused: none", bt.arb_plan(["21", "22"], inv, FVS, False) is None)

print("--- B: execute_arbitrage sends both legs as covered sells in ONE batch")
for a_on in (False, True):
    api, bt = bot({"21": -817, "22": -975}, cash=0.0, b=0.003, a=a_on)
    record_results(api)
    api.books["21"]["asks"] = [lvl(0.50, 1000)]
    api.books["22"]["asks"] = [lvl(0.502, 1000)]
    traded = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.50, 1000), "22": (0.502, 1000)}, 817, FVS,
                                  time.monotonic(), action="buy", kind="unwind")
    batches = [c for c in api.calls if c[0] == "batch"]
    check(f"no_set_aware_bids {a_on}: one batch of 2, both sell NO x817 at 1 - ask, accepted at 0 cash, set closed",
          batches == [("batch", 2)] and sorted((o["exchangeId"], o["side"], o["action"], o["quantity"], o["price"])
                                               for o in api.wire)
          == [("21", "no", "sell", 817, 0.5), ("22", "no", "sell", 817, 0.498)]
          and all(r.get("ok") for _, r in api.res_log) and traded == [817.0, 817.0]
          and api.inv == {"21": 0, "22": -158}, (api.calls, api.wire, traded, api.inv))
api, bt = bot({"21": -10, "22": -40}, b=0.003)
r = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.56, 1000), "22": (0.52, 1000)}, 30, FVS,
                         time.monotonic(), action="buy", kind="unwind")
check("a leg that can't be a whole covered sale: the batch is NOT sent (never half-converted), quotes untouched",
      r is None and not api.wire and not api.sent("cancel_all"), (r, api.wire, api.calls))
api, bt = bot({"21": -10, "22": -40}, a=True)
r = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.56, 1000), "22": (0.52, 1000)}, 30, FVS,
                         time.monotonic(), action="buy", kind="unwind")
check("...also with only no_set_aware_bids on", r is None and not api.wire)
api, bt = bot({"21": -10, "22": -40})
bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.56, 1000), "22": (0.52, 1000)}, 30, FVS,
                     time.monotonic(), action="buy", kind="unwind")
check("...both Package 7 flags off: Package 6 as before (half-converted batch sent)",
      sorted(o["side"] for o in api.wire) == ["no", "yes"], api.wire)
api, bt = bot({"21": 40, "22": 40}, b=0.003)
bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.51, 1000), "22": (0.50, 1000)}, 30, FVS,
                     time.monotonic(), action="sell", kind="unwind")
check("long-set unwind unchanged: sell YES on both", sorted((o["side"], o["action"]) for o in api.wire)
      == [("yes", "sell"), ("yes", "sell")], api.wire)

print("--- B: through take_arbitrage (cooldown, write budget, re-downloaded books)")
api, bt = bot({"21": -817, "22": -975}, cash=0.0, b=0.003)
api.books["21"]["asks"] = [lvl(0.50, 1000)]
api.books["22"]["asks"] = [lvl(0.502, 1000)]
with_books(bt)
inv = {e: bt.ex[e].inv for e in bt.ex}
done = bt.take_arbitrage(inv, FVS, {}, time.monotonic())
check("take_arbitrage unwinds the NO+NO race as a pair", "Utah Senate" in done and api.inv["21"] == 0
      and api.inv["22"] == -158 and len(no_sells(api)) == 2, (done, api.inv, api.wire))
api, bt = bot({"21": -817, "22": -975}, cash=0.0, b=0.003)
api.books["21"]["asks"] = [lvl(0.50, 1000)]
api.books["22"]["asks"] = [lvl(0.502, 1000)]
with_books(bt)
api.writes_left = lambda: 3
done = bt.take_arbitrage({e: bt.ex[e].inv for e in bt.ex}, FVS, {}, time.monotonic())
check("write budget short: deferred, nothing sent", not done and not api.wire)

print("--- B: at most pair_no_unwind_max_per_cycle short-set unwinds (B only) per cycle")
EXTRA = (market("6", "31", "Republican", "Texas Senate"), market("7", "32", "Democratic", "Texas Senate"),
         market("8", "41", "Republican", "Iowa Senate"), market("9", "42", "Democratic", "Iowa Senate"))
FVS4 = dict(FVS, **{"31": 0.5, "32": 0.5, "41": 0.5, "42": 0.5})


def b_cap_bot(cap=None, b=0.003):
    api, bt = make_bot(live=True, extra_markets=EXTRA)
    bt.cfg.reduce_no_as_sell, bt.cfg.pair_no_unwind_max_cost, bt.cfg.selftest_enabled = True, b, False
    if cap is not None:
        bt.cfg.pair_no_unwind_max_per_cycle = cap
    api.inv = {"11": -100, "12": -100, "21": -100, "22": -100, "31": -100, "32": -100, "41": -100, "42": -100}
    api.cash, api.set_collateral = 0.0, True
    for e, q in api.inv.items():
        bt.ex[e].inv = float(q)
    for e, p in (("11", 0.50), ("12", 0.502), ("21", 0.50), ("22", 0.502), ("31", 0.50), ("32", 0.502)):
        api.books[e] = {"bids": [lvl(0.40, 1000)], "asks": [lvl(p, 1000)]}
    for e in ("41", "42"):                       # asks 0.90: a profitable buy-back, not B's: never counted
        api.books[e] = {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.45, 1000)]}
    return api, with_books(bt)


api, bt = b_cap_bot()
check("default pair_no_unwind_max_per_cycle 2", bt.cfg.pair_no_unwind_max_per_cycle == 2)
done1 = bt.take_arbitrage({e: bt.ex[e].inv for e in bt.ex}, FVS4, {}, time.monotonic())
b_races = {"Ohio Senate", "Utah Senate", "Texas Senate"}
check("cycle 1: 2 of the 3 B races unwound + the profitable one (not counted), 1 B race waits, no cooldown on it",
      len(done1 & b_races) == 2 and "Iowa Senate" in done1 and len(done1) == 3
      and next(iter(b_races - done1), None) not in bt.arb_cooldown, (done1, bt.arb_cooldown))
done2 = bt.take_arbitrage({e: float(api.inv.get(e, 0)) for e in bt.ex}, FVS4, {}, time.monotonic())
check("cycle 2: the waiting B race goes", done2 == b_races - done1, done2)
api, bt = b_cap_bot(cap=10)
done = bt.take_arbitrage({e: bt.ex[e].inv for e in bt.ex}, FVS4, {}, time.monotonic())
check("cap 10: all 4 in one cycle", len(done) == 4, done)
good, bad = M.validate_overrides({"pair_no_unwind_max_per_cycle": 3}, c)
bad2 = M.validate_overrides({"pair_no_unwind_max_per_cycle": 11}, c)[1]
check("pair_no_unwind_max_per_cycle live override 3 accepted, 11 refused (1-10)",
      good == {"pair_no_unwind_max_per_cycle": 3} and not bad and bad2, (good, bad, bad2))

print("--- fake exchange: set-collateral rule")
api, bt = bot({"21": -817, "22": -975}, cash=0.0)
exp_ = M.iso(M.utcnow() + M.timedelta(seconds=600))


def sell_no(e, q, p=0.995):
    return {"exchangeId": e, "side": "no", "action": "sell", "quantity": q, "price": p, "expirationDate": exp_}


res = api.place_batch([sell_no("21", 1)])
check("one leg, the smaller (all in sets): refused 'Insufficient available funds'",
      not res[0]["ok"] and "Insufficient" in res[0]["data"]["error"]["message"], res)
res = api.place_batch([sell_no("22", 158)])
check("the lone part of the bigger leg (158): accepted", res[0]["ok"], res)
api.orders.clear()
res = api.place_batch([sell_no("22", 159)])
check("159 on the bigger leg (breaks one set): refused", not res[0]["ok"], res)
res = api.place_batch([sell_no("21", 1), sell_no("22", 1)])
check("both legs in one batch (closes a whole set): accepted", all(r["ok"] for r in res), res)
api.orders.clear()
api.cash = 10.0
res = api.place_batch([sell_no("21", 5)])
check("with cash: a set-breaking sale is accepted (it needs collateral, not refused)", res[0]["ok"], res)
api, bt = bot({"21": -817, "22": -975}, cash=0.0)
api.set_collateral = False
res = api.place_batch([sell_no("21", 1)])
check("rule off (default for older suites): accepted as in Package 6", res[0]["ok"], res)
api, bt = bot({"11": -10, "12": -8, "13": -5}, cash=0.0, three=True)
res = api.place_batch([sell_no("12", 3)])
check("worst-case model: [10, 8, 5] selling 3 on the middle leg: refused (lone [2, 0, 0])", not res[0]["ok"], res)
api.orders.clear()
res = api.place_batch([sell_no("11", 2)])
check("...2 on the biggest leg (its lone part): accepted", res[0]["ok"], res)
api.orders.clear()
res = api.place_batch([sell_no("11", 3)])
check("...3 on the biggest leg: refused", not res[0]["ok"], res)
api, bt = bot({"11": -300, "12": -500}, cash=0.0, three=True)
res = api.place_batch([sell_no("11", 1)])
check("NO on 2 of 3 legs: 1 on the smaller leg refused (no lone part)", not res[0]["ok"], res)
res = api.place_batch([sell_no("12", 200)])
check("...200 on the bigger leg (its lone part) accepted", res[0]["ok"], res)
api.orders.clear()
res = api.place_batch([sell_no("11", 50), sell_no("12", 50)])
check("...both NO legs in one batch (closes whole sets): accepted", all(r["ok"] for r in res), res)

print("--- C: start-up check (paired NO sale)")


def tick_until(bt, n=200):
    for _ in range(n):
        bt.pairno_tick()
        if bt.pairno_future is None and (bt.pairno_state is not None or bt.pairno_next > time.monotonic()):
            return
        time.sleep(0.01)


def c_bot(inv=None, cash=0.0, nosell="ok"):
    api, bt = bot(inv or {"21": -817, "22": -975}, cash=cash, b=0.003)
    bt.cfg.selftest_enabled = True
    bt.nosell_state = nosell
    with_books(bt)
    return api, bt


api, bt = c_bot()
check("before the check: B not in effect", not bt.pair_no_unwind_on())
tick_until(bt)
check("ONE batch of two 1-share sell NO @ 0.995, one per leg", [c for c in api.calls if c[0] == "batch"] == [("batch", 2)]
      and sorted((o["exchangeId"], o["quantity"], o["price"]) for o in no_sells(api)) == [("21", 1, 0.995), ("22", 1, 0.995)],
      (api.calls, api.wire))
check("both cancelled at once (two cancels, nothing resting)", len(api.sent("cancel_order")) == 2 and not api.orders,
      (api.calls[-4:], api.orders))
check("accepted -> B in effect", bt.pairno_state == "ok" and bt.pair_no_unwind_on())
check("holds restored on both legs", all(bt.ex[e].pending_until != float("inf") for e in ("21", "22")))
bt.pairno_tick()
check("runs once", len(no_sells(api)) == 2)

ALERTS.clear()
api, bt = c_bot()
api.refuse_no_sell = "Insufficient available funds"
try:
    tick_until(bt)
    exited = False
except SystemExit:
    exited = True
check("refused (funds): no exit, alert, B off for the run", not exited and bt.pairno_state == "off"
      and not bt.pair_no_unwind_on() and ALERTS
      and "the exchange refuses a paired NO sale: NO+NO sets cannot be unwound without cash" in ALERTS[-1],
      (bt.pairno_state, ALERTS))
api.books["21"]["asks"] = [lvl(0.50, 1000)]
api.books["22"]["asks"] = [lvl(0.502, 1000)]
with_books(bt)
check("...and arb_plan keeps today's rule (no buy-back at 1.002)",
      bt.arb_plan(["21", "22"], {e: bt.ex[e].inv for e in bt.ex}, FVS, False) is None)
check("...reduce_no_as_sell itself stays on", bt.reduce_no_on())


api, bt = c_bot()
api.set_collateral = False                    # the first leg alone is accepted here; the second is refused by hand
real_pb = api.place_batch
api.place_batch = lambda orders: [real_pb(orders[:1])[0],
                                  {"index": 1, "ok": False, "status": 400,
                                   "data": {"error": {"code": "BAD_REQUEST", "message": "Insufficient available funds"}}}]
tick_until(bt)
check("one leg accepted, the other refused for funds: refused, the accepted one cancelled",
      bt.pairno_state == "off" and len(api.sent("cancel_order")) == 1 and not api.orders, (bt.pairno_state, api.calls))

api, bt = c_bot()
api.batch_error = M.ApiError(503, "SERVICE_UNAVAILABLE", "busy")
tick_until(bt)
w1 = bt.pairno_wait
check("busy: undecided, retried after selftest_retry_seconds", bt.pairno_state is None and bt.pairno_next > time.monotonic()
      and w1 == bt.cfg.selftest_retry_seconds, (bt.pairno_state, w1))
bt.pairno_next = 0.0
tick_until(bt)
check("busy again: the wait doubles", bt.pairno_wait == 2 * w1 and bt.pairno_state is None, bt.pairno_wait)
api.batch_error = None
bt.pairno_next = 0.0
tick_until(bt)
check("then accepted: ok, back-off reset", bt.pairno_state == "ok" and bt.pairno_wait == 0.0)

api, bt = c_bot(nosell=None)
bt.pairno_tick()
check("sell-NO leg not passed yet: nothing sent", bt.pairno_future is None and not api.wire)
api, bt = c_bot(inv={"21": -817, "22": 975})
bt.pairno_tick()
check("no NO+NO race held: nothing sent", bt.pairno_future is None and not api.wire)
api, bt = c_bot()
bt.ex["22"].book = {"bids": [], "asks": [lvl(M.PMIN, 5)]}
bt.pairno_tick()
check("a leg with an ask at 0.005 (the test could fill): skipped", bt.pairno_future is None and not api.wire)
api, bt = c_bot()
bt.cfg.pair_no_unwind_max_cost = -1.0
bt.pairno_tick()
check("setting -1: no check", bt.pairno_future is None and not api.wire and bt.pairno_state is None)
api, bt = c_bot()
bt.cfg.reduce_no_as_sell = False
bt.pairno_tick()
check("reduce_no_as_sell off: no check", bt.pairno_future is None and not api.wire)
api, bt = bot({"11": -50, "12": -60, "21": -400, "22": -300}, cash=0.0, b=0.003)
bt.cfg.selftest_enabled, bt.nosell_state = True, "ok"
with_books(bt)
tick_until(bt)
check("two NO+NO races: the check goes to the biggest set (Utah, 300)",
      sorted(o["exchangeId"] for o in no_sells(api)) == ["21", "22"], api.wire)


class _Pending:
    def done(self):
        return False


print("--- C: overlap guards")
api, bt = c_bot()
bt.selftest_passed = False
bt.pairno_future = _Pending()
bt.selftest_next = 0.0
bt.selftest_tick()
check("selftest_tick while the paired leg is out: the main test doesn't start",
      bt.selftest_future is None and not api.sent("batch"))
bt.nosell_state, bt.nosell_next = None, 0.0
bt.nosell_tick()
check("nosell_tick while the paired leg is out: the sell-NO leg doesn't start", bt.nosell_future is None and not api.wire)
bt.pairno_future, bt.nosell_state = None, "ok"
bt.selftest_future = _Pending()
bt.pairno_tick()
check("pairno_tick while the main test is out: doesn't start", bt.pairno_future is None and not api.wire)
bt.selftest_future, bt.nosell_future = None, _Pending()
bt.pairno_tick()
check("pairno_tick while the sell-NO leg is out: doesn't start", bt.pairno_future is None and not api.wire)

print("--- A: the sell-NO check leg avoids legs with no lone part")
for a_on, want in ((False, "21"), (True, "11")):
    api, bt = bot({"21": -50, "22": -50, "11": -5}, cash=0.0, a=a_on)
    bt.cfg.selftest_enabled = True
    with_books(bt)
    for _ in range(200):
        bt.nosell_tick()
        if bt.nosell_future is None and bt.nosell_state is not None:
            break
        time.sleep(0.01)
    legs = no_sells(api)
    check(f"no_set_aware_bids {a_on}: the leg goes to {want}" + (" (21/22 are all set, 11 is a lone NO)" if a_on else
                                                                 " (the biggest NO, as Package 6)"),
          len(legs) == 1 and legs[0]["exchangeId"] == want, legs)

print("--- A: no lone NO part anywhere for > 10 min: logged once")


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def emit(self, r):
        self.msgs.append(r.getMessage())


cap_ = _Cap()
M.log.addHandler(cap_)
M.log.setLevel(logging.WARNING)
M.log.propagate = False
api, bt = bot({"21": -50, "22": -50}, cash=0.0, a=True)
bt.cfg.selftest_enabled = True
with_books(bt)
bt.nosell_tick()
t0 = getattr(bt, "nosell_nolone_since", None)
bt.nosell_nolone_since = (t0 or time.monotonic()) - 601
for _ in range(3):
    bt.nosell_tick()
waits = [m for m in cap_.msgs if "start-up check still waiting" in m]
check("all NO in sets: one log line after 10 min (not repeated), nothing sent",
      t0 is not None and len(waits) == 1 and not api.wire and bt.nosell_state is None, (waits, api.wire))
api, bt = bot({"21": -50, "22": -50}, cash=0.0, a=True)
bt.cfg.selftest_enabled = True
bt.nosell_tick()
bt.nosell_nolone_since = (getattr(bt, "nosell_nolone_since", None) or time.monotonic()) - 300
bt.nosell_tick()
check("...not before 10 min", len([m for m in cap_.msgs if "start-up check still waiting" in m]) == 1)
M.log.removeHandler(cap_)
M.log.setLevel(logging.NOTSET)
M.log.propagate = True

print("--- D: T2.5 passive short sets skipped with reduce_no_as_sell")
for reduce in (False, True):
    api, bt = bot({"21": -40, "22": -40}, reduce=reduce)
    bt.cfg.pair_unwind_passive = True
    with_books(bt)
    inv = {e: bt.ex[e].inv for e in bt.ex}
    plan = bt.pair_passive_plan(["21", "22"], inv, FVS)
    if reduce:
        check("reduce_no_as_sell on: no short-set slice planned", plan is None, plan)
    else:
        check("reduce_no_as_sell off: the short-set slice as before", plan is not None and plan["sign"] == -1, plan)
api, bt = bot({"21": 40, "22": 40})
bt.cfg.pair_unwind_passive = True
with_books(bt)
plan = bt.pair_passive_plan(["21", "22"], {e: bt.ex[e].inv for e in bt.ex}, FVS)
check("reduce_no_as_sell on: long sets (YES+YES) still planned", plan is not None and plan["sign"] == 1, plan)
api, bt = bot({"21": -40, "22": -40})
bt.cfg.pair_unwind_passive = True
bt.pp = {"Utah Senate": {"leg": "21", "other": "22", "sign": -1, "price": 0.47, "slice": 40, "left": 40,
                         "base_x": -40, "base_y": -40}}
q = M.Quote(0.45, 10, 0.55, 10)
check("an old short-set slice never rests its bid (quote unchanged)", bt.pair_passive_quote(bt.ex["21"], q) == q)
for state, want in (("off", "planned"), ("ok", "skipped"), (None, "planned")):
    api, bt = bot({"21": -40, "22": -40})
    bt.cfg.pair_unwind_passive, bt.cfg.selftest_enabled, bt.nosell_state = True, True, state
    with_books(bt)
    plan = bt.pair_passive_plan(["21", "22"], {e: bt.ex[e].inv for e in bt.ex}, FVS)
    check(f"D keys on the effective state: setting on, sell-NO check {state!r} -> short-set slice {want}",
          (plan is None) == (want == "skipped"), plan)
api, bt = bot({"21": -40, "22": -40})
bt.cfg.pair_unwind_passive, bt.cfg.selftest_enabled, bt.nosell_state = True, True, "off"
bt.pp = {"Utah Senate": {"leg": "21", "other": "22", "sign": -1, "price": 0.47, "slice": 40, "left": 40,
                         "base_x": -40, "base_y": -40}}
check("...and the slice rests its bid when the sell-NO check failed",
      bt.pair_passive_quote(bt.ex["21"], M.Quote(0.45, 10, 0.55, 10)).bid == 0.47)

print("--- E: status")
api, bt = bot({"21": -817, "22": -975, "11": -5})
check("nono_sets: 1 race, 817 sets, capital 817 (k - 1 per set)",
      bt.nono_sets() == {"races": 1, "sets": 817, "capital": 817.0}, bt.nono_sets())
api, bt = bot({"11": -300, "12": -500, "13": -200, "21": -1, "22": -2}, three=True)
check("3-leg race 200 sets (cap 400) + Utah 1 set: 2 races, 201 sets, cap 401",
      bt.nono_sets() == {"races": 2, "sets": 201, "capital": 401.0}, bt.nono_sets())
api, bt = bot({"21": -817, "22": -975})
bt.health = {}
bt.write_status(ok=True)
st = json.load(open(M.bot_path(bt.cfg.status_file)))
check("status.json: nono_sets and pairno_state", st.get("nono_sets") == {"races": 1, "sets": 817, "capital": 817.0}
      and "pairno_state" in st, {k: st.get(k) for k in ("nono_sets", "pairno_state")})
line = bt.summary_ops_line(101000.0)
check("summary: ' | NO+NO sets 1 (cap 0.8k)'", line is not None and line.endswith(" | NO+NO sets 1 (cap 0.8k)"), line)
api, bt = bot({"21": -817, "22": 975})
line = bt.summary_ops_line(101000.0)
check("summary: no NO+NO race -> no part", line is None or "NO+NO" not in line, line)

print("--- flags off identical to Package 6 (706e763) on a grid")
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", "706e763:mm_bot.py"], capture_output=True, text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p6.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p6", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k: False
except Exception as e:                            # no git here: the pin is skipped (reported)
    print("    (base module unavailable:", e, ")")


def base_bot(inv, reduce, three=False):
    """A Package 6 Bot, set up exactly as make_bot + bot() above."""
    api_n, bn = bot(inv, reduce=reduce, three=three)
    cfg = base.Config()
    for k in base.Config.__dataclass_fields__:
        setattr(cfg, k, getattr(bn.cfg, k))
    api_b = FakeApi(True)
    api_b.markets_list, api_b.books = list(api_n.markets_list), {e: {"bids": [dict(x) for x in v["bids"]],
                                                                     "asks": [dict(x) for x in v["asks"]]}
                                                                 for e, v in api_n.books.items()}
    api_b.inv, api_b.cash = dict(api_n.inv), None
    bb = base.Bot(api_b, cfg)
    for e, q in api_b.inv.items():
        if e in bb.ex:
            bb.ex[e].inv = float(q)
    return api_n, bn, api_b, bb


def strip(w):
    return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]


if base is None:
    check("base module (git show 706e763:mm_bot.py) loaded", False)
else:
    same, diffs = True, []
    invs = [(-817, -975), (-975, -817), (-40, -40), (-1, -300), (-300, 0), (-300, 300), (0, 0), (50, 60)]
    for reduce in (False, True):
        for i21, i22 in invs:
            inv = {"21": i21, "22": i22}
            api_n, bn, api_b, bb = base_bot(inv, reduce)
            for eid, iv in inv.items():
                for level in (None, 0, 1):
                    if bn.cover_no_qty(eid, iv, level) != bb.cover_no_qty(eid, iv, level):
                        same = False
                        diffs.append(("cover", reduce, inv, eid, level))
                for whole in (False, True):
                    o = {"exchangeId": eid, "side": "yes", "action": "buy", "quantity": 30, "price": 0.5}
                    if bn.no_sell_order(dict(o), iv, whole) != bb.no_sell_order(dict(o), iv, whole):
                        same = False
                        diffs.append(("no_sell_order", reduce, inv, eid, whole))
            for p21, p22 in ((0.49, 0.50), (0.50, 0.502), (0.50, 0.504), (0.51, 0.51)):
                for x in (bn, bb):
                    x.ex["21"].book = {"bids": [lvl(round(p21 - 0.03, 3), 1000)], "asks": [lvl(p21, 1000)]}
                    x.ex["22"].book = {"bids": [lvl(round(p22 - 0.03, 3), 1000)], "asks": [lvl(p22, 1000)]}
                iv = {e: bn.ex[e].inv for e in bn.ex}
                if bn.arb_plan(["21", "22"], iv, FVS, False) != bb.arb_plan(["21", "22"], iv, FVS, False):
                    same = False
                    diffs.append(("arb_plan", reduce, inv, p21, p22))
            o_n = bn.new_order(bn.ex["21"], True, 0.4, 100, 0.45, now)[0]
            o_b = bb.new_order(bb.ex["21"], True, 0.4, 100, 0.45, now)[0]
            if strip([o_n]) != strip([o_b]):
                same = False
                diffs.append(("new_order", reduce, inv))
    check("cover_no_qty / no_sell_order / arb_plan / new_order identical with both flags off", same, diffs[:5])
    ok_c, diffs = True, []
    for reduce in (False, True):
        for i21, i22 in ((-817, -975), (-40, -40), (-300, 0), (40, 40)):
            api_n, bn, api_b, bb = base_bot({"21": i21, "22": i22}, reduce)
            for x in (bn, bb):
                x.cfg.selftest_enabled = False
                cyc(x, 2)
            if strip(api_n.wire) != strip(api_b.wire):
                ok_c = False
                diffs.append((reduce, i21, i22, strip(api_n.wire)[:3], strip(api_b.wire)[:3]))
    check("two full cycles send the same orders with both flags off (reduce_no_as_sell off and on)", ok_c, diffs[:2])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
