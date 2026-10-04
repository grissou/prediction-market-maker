"""
Offline tests for Package 9 F5 arb_cash_rule (analysis/p9/SPEC_F2_F5.md): the race arbitrage back on, safely, at ~0
cash. Live 3 Oct: some legs filled, others were refused for cash (Maine Senate 158/0, South Dakota 13/0), and own
quotes made up 29% of the race-cycles with bids >= 1.04. Covers: settings and ranges; own quotes (resting, just
placed, unconfirmed) excluded from the bid-sum and the depth; thinnest-leg sizing; the cash rule (the cash gate's own
need rule, x arb_cash_mult + arb_cash_reserve) refuses below the threshold, shrinks to what fits and passes above,
refuses with no cash figure; the buy side's need; a refused leg is owed and followed up next cycle (completed, or the
extra reversed at once when it cannot be completed), no one-legged set left; write budget deferral; status.json
arb_cash_blocked; flag off identical to the branch head (git show e2e763d:mm_bot.py) on a grid.

Run:  python tests/test_arb_cash.py      (exit code 0 = all passed)
"""
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, lvl, make_bot      # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
BASE_REF = "e2e763d"
FVS = {"11": 0.17, "12": 0.83, "21": 0.5, "22": 0.5}
OHIO = ["11", "12"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def books(b11=(0.20, 100), b12=(0.85, 1000), a11=(0.24, 1000), a12=(0.88, 1000)):
    """Ohio: bids 0.20 + 0.85 = 1.05 (>= 1 + arb_min_profit 0.03); Utah: nothing."""
    return {"11": {"bids": [lvl(*b11)], "asks": [lvl(*a11)]},
            "12": {"bids": [lvl(*b12), lvl(0.80, 1000)], "asks": [lvl(*a12)]},
            "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
            "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}


def bot(cash=1e6, rule=True, bk=None, inv=None, **cfg):
    """cash: the bot's cash figure (cash gate); the fake exchange accepts everything unless api.cash is set."""
    api, b = make_bot(live=True, books=bk or books())
    b.cfg.selftest_enabled = False
    b.cfg.arb_cash_rule = rule
    b.cfg.cash_gate_enabled = True
    b.cfg.cash_gate_reserve = 0.0
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    b.cg_cash, b.cg_reserved, b.cg_spent, b.cg_read_at = cash, 0.0, 0.0, time.monotonic()
    api.inv = dict(inv or {})
    for e in b.ex:
        b.ex[e].inv = float(api.inv.get(e, 0))
        b.ex[e].book = api.full_book(e)
    return api, b


def invd(api, b):
    return {e: float(api.inv.get(e, 0)) for e in b.ex}


def sent(api):
    return [(o["exchangeId"], o["side"], o["action"], o["quantity"], o["price"]) for o in api.wire]


def arb(api, b):
    return b.take_arbitrage(invd(api, b), FVS, {}, time.monotonic())


print("--- settings: defaults off, live-overridable with ranges")
c = M.Config()
check("defaults: arb_cash_rule False, mult 1.25, reserve 2000, depth frac 0.8",
      (c.arb_cash_rule, c.arb_cash_mult, c.arb_cash_reserve, c.arb_leg_depth_frac) == (False, 1.25, 2000.0, 0.8))
good, bad = M.validate_overrides({"arb_cash_rule": True, "arb_cash_mult": 3.0, "arb_cash_reserve": 20000,
                                  "arb_leg_depth_frac": 0.1}, c)
check("all four accepted at their bounds", len(good) == 4 and not bad, (good, bad))
good, bad = M.validate_overrides({"arb_cash_mult": 0.99, "arb_cash_reserve": 20001, "arb_leg_depth_frac": 0.05,
                                  "arb_cash_rule": "yes"}, c)
check("out-of-range / wrong type refused (4)", not good and len(bad) == 4, (good, bad))

print("--- own quotes excluded from the bid-sum and the depth")
api, b = bot(rule=False)
check("rule off: sell-side arbitrage on Ohio, bids 1.05, 100 sets (11's depth)",
      b.arb_plan(OHIO, invd(api, b), FVS, False) == ("arb", "sell", {"11": (0.20, 100), "12": (0.85, 1000)}, 100),
      b.arb_plan(OHIO, invd(api, b), FVS, False))
api, b = bot()
plan = b.arb_plan(OHIO, invd(api, b), FVS, False)
check("rule on: thinnest leg 100 x arb_leg_depth_frac 0.8 = 80 sets", plan and plan[3] == 80, plan)
b.cfg.arb_leg_depth_frac = 1.0
check("depth frac 1.0: 100", (b.arb_plan(OHIO, invd(api, b), FVS, False) or (0,) * 4)[3] == 100)
b.cfg.arb_leg_depth_frac = 0.8
b.my_orders["o1"] = M.Resting("o1", "12", True, 0.85, 50, None)   # our bid on 12 at 0.85 (size in the book: not
check("an own bid at 12's 0.85 (strip_own missed some of it): the 0.85 level is skipped, 0.80 next: bids 1.00 -> "
      "no arbitrage", b.arb_plan(OHIO, invd(api, b), FVS, False) is None)   # stripped: the record knew 50 of more)
api_off, b_off = bot(rule=False)
b_off.my_orders["o1"] = M.Resting("o1", "12", True, 0.85, 50, None)
check("... with the rule off the same book is an arbitrage at 1.05 (fail-before)",
      b_off.arb_plan(OHIO, invd(api_off, b_off), FVS, False) is not None)
del b.my_orders["o1"]
b.my_orders["o2"] = M.Resting("o2", "12", False, 0.85, 50, None)  # an own ASK at 0.85 does not touch the bids
check("an own ask at the same price leaves the bid level alone", b.arb_plan(OHIO, invd(api, b), FVS, False) is not None)
del b.my_orders["o2"]
b.unconfirmed["12"] = [({"action": "buy", "price": 0.85, "quantity": 30}, {}, time.monotonic())]
check("a bid we sent with no answer yet (unconfirmed) at 0.85: skipped too", b.arb_plan(OHIO, invd(api, b), FVS, False)
      is None)
b.unconfirmed.clear()
o3 = M.Resting("o3", "11", True, 0.20, 10, None)
b.recent_orders["o3"] = (o3, time.monotonic())
lv = b.arb_levels(OHIO, "bids")
check("just placed (recent_orders) at 11's only bid: no level left on 11 -> None", lv is None, lv)
b.recent_orders.clear()
bk = books(b11=(0.20, 1000), b12=(0.85, 1000))
bk["12"]["bids"] = [lvl(0.86, 900), lvl(0.85, 40)]
api, b = bot(bk=bk)
b.my_orders["o4"] = M.Resting("o4", "12", True, 0.86, 100, None)
plan = b.arb_plan(OHIO, invd(api, b), FVS, False)
check("own level 0.86 skipped: 12's level is 0.85 x 40 -> bids 1.05, depth 0.8 x 40 = 32 sets",
      plan and plan[2]["12"] == (0.85, 40) and plan[3] == 32, plan)

print("--- the cash rule (the cash gate's need rule)")
api, b = bot()
check("need of 80 sell sets = (1 - 0.20) x 80 + (1 - 0.85) x 80 = 76",
      abs(b.arb_cash_need({"11": (0.20, 100), "12": (0.85, 1000)}, 80, "sell") - 76.0) < 1e-9)
api, b = bot(inv={"11": 50})
check("50 YES held on 11: its first 50 sold need nothing: 0.8 x 30 + 0.15 x 80 = 36",
      abs(b.arb_cash_need({"11": (0.20, 100), "12": (0.85, 1000)}, 80, "sell") - 36.0) < 1e-9)
api, b = bot()
check("buy side: need = ask x sets per leg (0.45 + 0.52) x 10 = 9.7",
      abs(b.arb_cash_need({"21": (0.45, 100), "22": (0.52, 100)}, 10, "buy") - 9.7) < 1e-9)
LV = {"11": (0.20, 100), "12": (0.85, 1000)}
api, b = bot(cash=1.25 * 76 + 2000)
check("cash 2095 = 1.25 x 76 + 2000: all 80 sets", b.arb_cash_fit(LV, 80, "sell")[0] == 80)
api, b = bot(cash=1.25 * 76 + 2000 - 0.5)
fit = b.arb_cash_fit(LV, 80, "sell")
check("0.5 less: 79 sets (fewer sets, each one fully funded)", fit[0] == 79, fit)
api, b = bot(cash=2000 + 1.25 * 0.95 - 0.01)
check("below 1.25 x 0.95 + 2000: not one set", b.arb_cash_fit(LV, 80, "sell")[0] == 0)
api, b = bot(cash=2000.0, arb_cash_reserve=0.0, arb_cash_mult=1.0)
check("reserve 0, mult 1: 2000 / 0.95 -> 80 (all)", b.arb_cash_fit(LV, 80, "sell")[0] == 80)
api, b = bot(cash=1e6, cash_gate_enabled=False)
check("no cash figure (cash gate off): refused", b.arb_cash_fit(LV, 80, "sell")[0] == 0)

print("--- take_arbitrage with the rule")
grab = []


class Grab(logging.Handler):
    def emit(self, rec):
        grab.append(rec.getMessage())


h = Grab()
M.log.addHandler(h)
old_level = M.log.level
M.log.setLevel(logging.INFO)
api, b = bot(cash=500.0)
done = arb(api, b)
check("cash 500 < 2095: no batch, no book download, counted", not done and not api.wire and b.arb_cash_blocked == 1
      and not [x for x in api.calls if x[0] == "book"], (done, api.calls, b.arb_cash_blocked))
check("journal 'ARB skipped: cash rule (need 76.00, left 500.00)'",
      any(m.startswith("ARB skipped: cash rule (need 76.00, left 500.00)") for m in grab), grab)
api, b = bot(cash=2095.0)
done = arb(api, b)
check("cash 2095: both legs sell 80 in one batch", done == {"Ohio Senate"}
      and sorted(sent(api)) == [("11", "yes", "sell", 80, 0.2), ("12", "yes", "sell", 80, 0.85)]
      and len([x for x in api.calls if x[0] == "batch"]) == 1, sent(api))
check("legs equal: a complete short set (-80 / -80), nothing owed", api.inv == {"11": -80, "12": -80}
      and not b.pair_owed, (api.inv, b.pair_owed))
api, b = bot(cash=2050.0)
arb(api, b)
check("cash 2050: (2050 - 2000) / 1.25 / 0.95 = 42 sets", [x[3] for x in sent(api)] == [42, 42], sent(api))
api, b = bot(rule=False)
b.cg_cash = 500.0
arb(api, b)
check("rule off: 100 sets go (the old sizing; the cash gate then trims to 500 cash: 526 per... all legs alike)",
      len(set(x[3] for x in sent(api))) == 1 and sent(api), sent(api))
M.log.removeHandler(h)
M.log.setLevel(old_level)

print("--- a refused leg is owed and followed up next cycle: no one-legged set left")
api, b = bot(cash=1e6)
api.cash = 50.0                                   # the exchange disagrees: 11 (needs 64) refused, 12 (12) goes
arb(api, b)
check("batch: 11 refused (cash), 12 filled 80 -> a one-legged short", api.inv == {"12": -80}, api.inv)
st = b.pair_owed.get("Ohio Senate")
check("owed: 11 owes 80 (kind arb)", st and st["kind"] == "arb" and st["legs"] == {"11": 80}, st)
check("status.json pair_owed shows it", b.pair_owed_status() == {"Ohio Senate": 80})
check("take_arbitrage leaves the owed race alone", arb(api, b) == set())
api.cash = None                                   # cash back
n = len(api.wire)
acted = b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("next cycle: 11 sells 80 at 0.20 (complete), the set is whole, owed cleared",
      sent(api)[n:] == [("11", "yes", "sell", 80, 0.2)] and api.inv == {"11": -80, "12": -80} and not b.pair_owed
      and acted == {"Ohio Senate"}, (sent(api)[n:], api.inv, b.pair_owed))
# completion impossible (11's bids gone below 0.19): the extra on 12 is bought back AT ONCE (same follow-up)
api, b = bot(cash=1e6, reduce_no_as_sell=True)
api.cash = 50.0
arb(api, b)
api.cash = None
api.books["11"]["bids"] = [lvl(0.15, 1000)]       # below the completion limit 0.20 - 0.01
api.books["12"]["asks"] = [lvl(0.88, 1000)]       # within the reversal limit 0.85 + 0.03 + 0.01
n = len(api.wire)
b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("cannot complete: 12's 80 bought back in the same follow-up as a covered 'sell NO' @ 0.12",
      sent(api)[n:] == [("12", "no", "sell", 80, 0.12)], sent(api)[n:])
check("flat again, owed cleared (no one-legged set left after one cycle)", api.inv.get("11", 0) == 0
      and api.inv.get("12", 0) == 0 and not b.pair_owed, (api.inv, b.pair_owed))
# reversal also out of reach: kept, tried again, alerted and dropped after the tries
api, b = bot(cash=1e6, pair_unwind_followup_tries=2)
api.cash = 50.0
arb(api, b)
api.cash = None
api.books["11"]["bids"] = [lvl(0.15, 1000)]
api.books["12"]["asks"] = [lvl(0.95, 1000)]       # beyond 0.89
ALERTS.clear()
b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("neither in reach: nothing sent, still owed after try 1", "Ohio Senate" in b.pair_owed
      and b.pair_owed["Ohio Senate"]["tries"] == 1 and len(api.wire) == 2, (b.pair_owed, sent(api)))
api.books["12"]["asks"] = [lvl(0.89, 30), lvl(0.90, 1000)]   # 30 within reach
b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("try 2 reverses what it can (30 of 80 at 0.89), the rest is alerted and dropped (tries 2)",
      sent(api)[-1] == ("12", "yes", "buy", 30, 0.89) and api.inv.get("12") == -50 and not b.pair_owed
      and any("still unequal" in a for a in ALERTS), (sent(api), api.inv, ALERTS))
# write budget: deferred, not a try
api, b = bot(cash=1e6)
api.cash = 50.0
arb(api, b)
api.cash = None
api.writes_left = lambda: 2
n = len(api.wire)
b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("write budget short: deferred, not a try, nothing sent", len(api.wire) == n
      and b.pair_owed["Ohio Senate"]["tries"] == 0)
api.writes_left = lambda: 10 ** 6
b.pair_followup_step(FVS, time.monotonic(), {}, inv=invd(api, b))
check("budget back: completed", not b.pair_owed and api.inv == {"11": -80, "12": -80}, api.inv)
check("the follow-up cancels our orders first and the leftovers after",
      [x[0] for x in api.calls if x[0] in ("cancel_all", "batch")][-3:] == ["cancel_all", "batch", "cancel_all"])
api, b = bot(cash=1e6, rule=False)
api.cash = 50.0
ALERTS.clear()
arb(api, b)
check("rule off: the old behaviour (alert, nothing owed)", not b.pair_owed and any("partly filled" in a for a in ALERTS),
      (b.pair_owed, ALERTS))

print("--- status.json arb_cash_blocked")


def cyc(rule):
    a, bb = make_bot(books=books())
    bb.cfg.selftest_enabled = False
    bb.cfg.arb_cash_rule = rule
    bb.refs = FakeRefs({"Ohio Senate|Republican": 0.17, "Ohio Senate|Democratic": 0.83})
    logging.disable(logging.CRITICAL)
    try:
        bb.cycle()
    finally:
        logging.disable(logging.NOTSET)
    return a, bb


a, bb = cyc(True)
check("cycle, rule on, cash gate off (no cash figure): no arbitrage, arb_cash_blocked >= 1",
      bb.health.get("arb_cash_blocked", 0) >= 1 and not [o for o in a.wire if o["exchangeId"] == "11"
                                                         and o["action"] == "sell" and o["price"] == 0.2], bb.health)
a, bb = cyc(False)
check("cycle, rule off: no arb_cash_blocked in status.json", "arb_cash_blocked" not in bb.health)

print("--- flag off identical to the branch head (git show %s:mm_bot.py) on a grid" % BASE_REF)
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", f"{BASE_REF}:mm_bot.py"], capture_output=True, text=True,
                         timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p9base_f5.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p9base_f5", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k_: False
except Exception as e:                          # no git here: the pin is reported as failed
    print("    (base module unavailable:", e, ")")


def twin(inv, bk, overrides, cash):
    out = []
    for mod in (M, base):
        api_x, bn = make_bot(books={e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                                    for e, v in bk.items()})
        if mod is base:
            cfg = base.Config()
            for k_ in base.Config.__dataclass_fields__:
                setattr(cfg, k_, getattr(bn.cfg, k_))
            d = tempfile.mkdtemp()
            for k_ in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file",
                       "overrides_file", "market_edge_file", "handover_file"):
                setattr(cfg, k_, os.path.join(d, os.path.basename(getattr(cfg, k_))))
            api_x = FakeApi(True)
            api_x.markets_list = list(bn.api.markets_list)
            api_x.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                           for e, v in bk.items()}
            bn = base.Bot(api_x, cfg)
        api_x.inv.update(inv)
        api_x.cash = cash
        bn.refs = FakeRefs({"Ohio Senate|Republican": 0.17, "Ohio Senate|Democratic": 0.83,
                            "Utah Senate|Republican": 0.5, "Utah Senate|Democratic": 0.5})
        bn.cfg.selftest_enabled = False
        for k_, v in overrides.items():
            setattr(bn.cfg, k_, v)
        logging.disable(logging.CRITICAL)
        try:
            bn.cycle()
            bn.cycle()
        finally:
            logging.disable(logging.NOTSET)
        out.append((api_x, bn))
    return out


def strip(w):
    return [{k_: v for k_, v in o.items() if k_ != "expirationDate"} for o in w]


if base is None:
    check(f"base module (git show {BASE_REF}:mm_bot.py) loaded", False)
else:
    ok, diffs, n = True, [], 0
    for inv in ({}, {"11": -500, "12": -500}, {"11": 300}):
        for over in ({}, {"cash_gate_enabled": True}, {"pair_unwind_followup": True, "reduce_no_as_sell": True},
                     {"arb_two_sided": False}):
            for cash in (None, 50.0):
                (an, bn), (ab, bb_) = twin(inv, books(b11=(0.20, 300)), over, cash)
                n += 1
                if (strip(an.wire) != strip(ab.wire) or an.inv != ab.inv or bn.health.keys() != bb_.health.keys()
                        or bn.pair_owed_status() != bb_.pair_owed_status()):
                    ok = False
                    diffs.append((inv, over, cash))
    check(f"flag off: orders sent, positions, owed, status keys identical to {BASE_REF} on {n} scenarios", ok, diffs[:3])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
