"""
Offline tests for Package 8 item 2 (NO+NO pair unwind sizing and ordering; live 3 Oct 19:56 with
pair_no_unwind_max_cost 0.02). Covers:
  1 pair_no_unwind_max_sets: a short-set buy-back with pair_no_unwind_max_cost in effect is capped in SETS per race,
    not by pair_unwind_max_frac x account / YES ask per leg; long sets and the flag-off buy-back keep the cash cap;
  2 arb_race_order: NO+NO races cheapest first (asks sum), then the most capital, then the name; the others wait with
    no cooldown; flag off = self.groups order;
  3 unwind_is_safe: a NO+NO buy-back no longer refused because the legs' risk fair values add up to > 1 (a mark
    artefact of the worst case); flag off unchanged;
  4 pair_unwind_followup_max_age: an owed record the write budget defers every cycle is alerted and cleared after
    the age; younger records wait;
  5 a leg REFUSED in the batch (ok False) is owed like a zero fill and followed up as a covered sale of its lone part.

Run:  python tests/test_pair_sizing.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot, market      # noqa: E402
import mm_bot as M                            # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
FVS = {"11": 0.15, "12": 0.85, "21": 0.5, "22": 0.5}
BANK = 101000.0


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def bot(inv, b=0.02, cash=None, extra=(), followup=False):
    api, bt = make_bot(live=True, extra_markets=extra)
    bt.cfg.reduce_no_as_sell = True
    bt.cfg.pair_no_unwind_max_cost = b
    bt.cfg.pair_unwind_followup = followup
    bt.cfg.selftest_enabled = False
    bt.size_bank = BANK
    api.inv = dict(inv)
    api.cash = cash
    api.set_collateral = cash is not None
    for e, q in api.inv.items():
        if e in bt.ex:
            bt.ex[e].inv = float(q)
    return api, bt


def set_books(bt, asks, size=3000, bids=None):
    for e, p in asks.items():
        bt.ex[e].book = {"bids": [lvl((bids or {}).get(e, round(p - 0.04, 3)), size)], "asks": [lvl(p, size)]}
        bt.api.books[e] = {"bids": [lvl((bids or {}).get(e, round(p - 0.04, 3)), size)], "asks": [lvl(p, size)]}


def inv_of(bt):
    return {e: bt.ex[e].inv for e in bt.ex}


print("--- 1 sizing: sets per race, not cash per order at the YES ask")
c = M.Config()
check("defaults: pair_no_unwind_max_sets 1000, pair_unwind_followup_max_age 600",
      c.pair_no_unwind_max_sets == 1000 and c.pair_unwind_followup_max_age == 600.0)
good, bad = M.validate_overrides({"pair_no_unwind_max_sets": 2500, "pair_unwind_followup_max_age": 900}, c)
check("live overrides accepted", not bad and good.get("pair_no_unwind_max_sets") == 2500, (good, bad))
PA = {"21": 0.80, "22": 0.21}                        # asks 1.01 (a PA-10-like race)
api, bt = bot({"21": -2000, "22": -2000})
set_books(bt, PA)
plan = bt.arb_plan(["21", "22"], inv_of(bt), FVS, False)
check("asks 1.01, 2,000 sets, 3,000 offered: 1,000 sets (pair_no_unwind_max_sets), not 1,262 (1,010 / 0.80)",
      plan is not None and plan[:2] == ("unwind", "buy") and plan[3] == 1000, plan)
bt.cfg.pair_no_unwind_max_sets = 5000
check("max_sets 5,000: the 2,000 sets", (bt.arb_plan(["21", "22"], inv_of(bt), FVS, False) or (0,) * 4)[3] == 2000)
set_books(bt, PA, size=897)
check("thinner leg offers 897: 897", (bt.arb_plan(["21", "22"], inv_of(bt), FVS, False) or (0,) * 4)[3] == 897)
api, bt = bot({"21": -82, "22": -1500})
set_books(bt, PA, size=897)
check("only 82 NO on the smaller leg: 82 (the sets bind, as at PA-10 if it held 82 sets)",
      (bt.arb_plan(["21", "22"], inv_of(bt), FVS, False) or (0,) * 4)[3] == 82)
api, bt = bot({"21": -2000, "22": -2000}, b=-1.0)
set_books(bt, {"21": 0.78, "22": 0.21})            # asks 0.99: profitable, the flag off: today's cash cap
check("flag off, asks 0.99: the cash cap as before (1,010 / 0.78 = 1,294)",
      (bt.arb_plan(["21", "22"], inv_of(bt), FVS, False) or (0,) * 4)[3] == int(0.01 * BANK / 0.78))
api, bt = bot({"21": 2000, "22": 2000})
set_books(bt, {"21": 0.85, "22": 0.25}, bids={"21": 0.80, "22": 0.22})   # bids 1.02: long set sale
check("long set (YES+YES) with the flag on: the cash cap as before (1,010 / 0.80 = 1,262)",
      (bt.arb_plan(["21", "22"], inv_of(bt), FVS, False) or (0,) * 4)[3] == int(0.01 * BANK / 0.80))
api, bt = bot({"21": -2000, "22": -2000}, cash=0.0)
set_books(bt, PA)
traded = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.80, 3000), "22": (0.21, 3000)}, 1000, FVS,
                              time.monotonic(), action="buy", kind="unwind")
check("execute: both legs sell NO x1000 in one batch at 0 cash, set closed equally",
      traded == [1000.0, 1000.0] and api.inv == {"21": -1000, "22": -1000}
      and sorted((o["side"], o["action"], o["quantity"]) for o in api.wire) == [("no", "sell", 1000)] * 2,
      (traded, api.inv, api.wire))

print("--- 2 order: cheapest first, then capital, then name; the others wait without a cooldown")
EXTRA = (market("6", "31", "Republican", "Texas Senate"), market("7", "32", "Democratic", "Texas Senate"),
         market("8", "41", "Republican", "Iowa Senate"), market("9", "42", "Democratic", "Iowa Senate"))
FVS4 = dict(FVS, **{"31": 0.5, "32": 0.5, "41": 0.5, "42": 0.5})


def order_bot(b=0.02, cap=1):
    api, bt = bot({"11": -100, "12": -100, "21": -100, "22": -300, "31": -500, "32": -500, "41": 50, "42": 50},
                  b=b, cash=0.0, extra=EXTRA)
    bt.cfg.pair_no_unwind_max_per_cycle = cap
    set_books(bt, {"11": 0.50, "12": 0.515,          # Ohio 1.015
                   "21": 0.50, "22": 0.505,          # Utah 1.005, 100 sets
                   "31": 0.50, "32": 0.505,          # Texas 1.005, 500 sets
                   "41": 0.60, "42": 0.45})          # Iowa long (not NO+NO)
    return api, bt


api, bt = order_bot()
names = [r for r, _ in bt.arb_race_order(inv_of(bt))]
check("order: Iowa (not NO+NO) first, then Texas (1.005, 500 sets), Utah (1.005, 100), Ohio (1.015)",
      names == ["Iowa Senate", "Texas Senate", "Utah Senate", "Ohio Senate"], names)
done = bt.take_arbitrage(inv_of(bt), FVS4, {}, time.monotonic())
check("cap 1: Texas goes first; Utah and Ohio wait with no cooldown", done == {"Texas Senate"}
      and "Utah Senate" not in bt.arb_cooldown and "Ohio Senate" not in bt.arb_cooldown, (done, bt.arb_cooldown))
done = bt.take_arbitrage({e: float(api.inv.get(e, 0)) for e in bt.ex}, FVS4, {}, time.monotonic())
check("next cycle: Utah (Texas cooling down)", done == {"Utah Senate"}, done)
api, bt = order_bot()
set_books(bt, {"21": 0.50, "22": 0.505, "31": 0.50, "32": 0.505})
bt.ex["31"].inv = bt.ex["32"].inv = -100.0
names = [r for r, _ in bt.arb_race_order(inv_of(bt))]
check("equal sum and capital: by name (Texas before Utah)", names[1:3] == ["Texas Senate", "Utah Senate"], names)
api, bt = order_bot(b=-1.0)
check("flag off: self.groups order", [r for r, _ in bt.arb_race_order(inv_of(bt))] == list(bt.groups), list(bt.groups))

print("--- 3 unwind_is_safe: a NO+NO buy-back is not refused for a worst-case mark artefact")
FV_HI = dict(FVS, **{"21": 0.53, "22": 0.52})       # risk fair values add up to 1.05
api, bt = bot({"21": -500, "22": -2000})           # unequal legs (as live): the worst case is not clamped at 0
set_books(bt, {"21": 0.50, "22": 0.51})
iv = inv_of(bt)
check("old check (no slack) refuses: worst case 695 -> 720 (+500 x 0.05)", not bt.unwind_is_safe(iv, FV_HI, ["21", "22"], 500))
check("with set_slack: allowed", bt.unwind_is_safe(iv, FV_HI, ["21", "22"], 500, set_slack=True))
check("arb_plan with the flag on: the buy-back", (bt.arb_plan(["21", "22"], iv, FV_HI, False) or (0,) * 4)[3] == 500)
api, bt = bot({"21": -500, "22": -2000}, b=-1.0)
set_books(bt, {"21": 0.48, "22": 0.51})
check("flag off: unchanged (refused at fair values 1.05)", bt.arb_plan(["21", "22"], inv_of(bt), FV_HI, False) is None)
api, bt = bot({"21": -500, "22": 300})
check("slack only for a NO+NO buy-back of a complete set (one leg long: no slack)",
      bt.unwind_is_safe(inv_of(bt), FV_HI, ["21", "22"], 500, set_slack=True)
      == bt.unwind_is_safe(inv_of(bt), FV_HI, ["21", "22"], 500))

print("--- 4 follow-up owed record bounded by age")
api, bt = bot({"21": -500, "22": -900}, followup=True)
now_m = time.monotonic()
bt.pair_owed = {"Utah Senate": {"action": "buy", "legs": {"22": 400}, "t": now_m - 30, "tries": 0,
                                "price": {"21": 0.5, "22": 0.505}}}
api.writes_left = lambda: 0
n0 = len(ALERTS)
bt.pair_followup_step(FVS, now_m)
check("write budget short, 30 s old: waits (not a try, no alert)", "Utah Senate" in bt.pair_owed
      and bt.pair_owed["Utah Senate"]["tries"] == 0 and len(ALERTS) == n0)
bt.pair_followup_step(FVS, now_m + 600)
check("...600 s old: alerted and cleared", "Utah Senate" not in bt.pair_owed and len(ALERTS) == n0 + 1
      and "not evened up within 600 s" in ALERTS[-1], ALERTS[-1:])
done = bt.take_arbitrage(inv_of(bt), FVS, {}, now_m + 600)
check("...take_arbitrage no longer skips the race for an owed record", "Utah Senate" not in bt.pair_owed)

print("--- 5 a leg REFUSED in the batch is owed like a zero fill")
api, bt = bot({"21": -1000, "22": -1000}, followup=True)
set_books(bt, {"21": 0.50, "22": 0.505})
real = api.place_batch


def pb(orders):
    if len(orders) == 2:                             # the pair batch: leg 22 refused, leg 21 placed
        r0 = real(orders[:1])
        return r0 + [{"index": 1, "ok": False, "status": 400,
                      "data": {"error": {"code": "BAD_REQUEST", "message": "Insufficient available funds"}}}]
    return real(orders)


api.place_batch = pb
traded = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.50, 3000), "22": (0.505, 3000)}, 600, FVS,
                              time.monotonic(), action="buy", kind="unwind")
st = bt.pair_owed.get("Utah Senate") or {}
check("traded [600, 0]: leg 22 owes 600 (its lone part after the fill: 1000 - 400)",
      traded == [600.0, 0.0] and st.get("legs") == {"22": 600}, (traded, st))
for e in ("21", "22"):
    bt.ex[e].inv = float(api.inv.get(e, 0))
api.place_batch = real
bt.pair_followup_step(FVS, time.monotonic())
check("follow-up: covered sell NO x600 on leg 22, legs even, owed cleared",
      "Utah Senate" not in bt.pair_owed and api.inv == {"21": -400, "22": -400}
      and any(o["exchangeId"] == "22" and o["side"] == "no" and o["action"] == "sell" and o["quantity"] == 600
              for o in api.wire), (bt.pair_owed, api.inv, api.wire[-2:]))

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
