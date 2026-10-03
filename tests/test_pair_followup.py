"""
Offline tests for Package 7 part 2 "pair unwind follow-up" (pair_unwind_followup). Live 3 Oct 14:11:55: the
short-set pair unwind on Hawaii Governor bought YES on both legs but filled [314, 1069] - 755 shares of a hedged set
became one-sided inventory. Covers:
  1 sizing every leg of a pair unwind to the joint depth at or better than the planned prices (several levels);
  2 unequal fills -> the lagging leg owes the difference, evened up on the next cycles by one immediate-or-cancel order
    at <= planned + max_cost (a covered "sell NO" with reduce_no_as_sell), tries exhausted -> one alert, write budget
    short -> waits, status.json pair_owed, the long-set mirror, through a full cycle;
  3 the live_sim mirror (scripted race with unequal depth);
  flag off identical to the Package 7 part 1 code (d22170b) on a grid.

Run:  python tests/test_pair_followup.py      (exit code 0 = all passed)
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
from fakes import FakeApi, lvl, make_bot      # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)
F, MC, TR = "pair_unwind_followup", "pair_unwind_followup_max_cost", "pair_unwind_followup_tries"
RACE = "Utah Senate"
FVS = {"11": 0.15, "12": 0.85, "21": 0.5, "22": 0.5}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def bot(inv=None, cash=None, reduce=True, on=True, live=True):
    api, bt = make_bot(live=live)
    bt.cfg.reduce_no_as_sell = reduce
    bt.cfg.pair_unwind_followup = on
    bt.cfg.selftest_enabled = False
    api.inv = dict(inv or {})
    api.cash = cash
    api.set_collateral = cash is not None
    for e, q in api.inv.items():
        if e in bt.ex:
            bt.ex[e].inv = float(q)
    return api, bt


def books(api, bt, cached, exchange):
    """cached: what the bot's last download showed (ex.book); exchange: the book the order meets (api.books)."""
    for e, b in cached.items():
        bt.ex[e].book = {"bids": [lvl(p, q) for p, q in b.get("bids", [])],
                         "asks": [lvl(p, q) for p, q in b.get("asks", [])]}
    for e, b in exchange.items():
        api.books[e] = {"bids": [lvl(p, q) for p, q in b.get("bids", [])],
                        "asks": [lvl(p, q) for p, q in b.get("asks", [])]}


def sync(api, bt):
    """A positions read: the bot's inventory from the exchange."""
    for e in bt.ex:
        bt.ex[e].inv = float(api.inv.get(e, 0))


def short_case(on=True, reduce=True, cash=0.0, exch21=None):
    """Short (NO+NO) set 1069 x 2, buy-back planned at asks 0.49 + 0.50 = 0.99. The cached book shows 1069 on both
    legs; the exchange has only 314 on leg 21 at 0.49 when the batch lands (exch21: what sits behind it)."""
    api, bt = bot({"21": -1069, "22": -1069}, cash=cash, reduce=reduce, on=on)
    books(api, bt, {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 1069)]},
                    "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 1069)]}},
          {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 314)] + (exch21 or [(0.495, 2000)])},
           "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 1069)]}})
    traded = bt.execute_arbitrage(RACE, ["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, FVS,
                                  time.monotonic(), action="buy", kind="unwind")
    return api, bt, traded


print("--- settings")
c = M.Config()
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("defaults: off, max_cost 0.01, 6 tries", (c.pair_unwind_followup, c.pair_unwind_followup_max_cost,
                                                c.pair_unwind_followup_tries) == (False, 0.01, 6))
_NC = "pair_no_unwind_max_per_cycle"            # red-team fix 3, after these
_P8 = ["pair_no_unwind_max_sets", "pair_unwind_followup_max_age"]   # Package 8 item 2, after those
check("last in Config and OVERRIDABLE (then pair_no_unwind_max_per_cycle, then Package 8's)",
      _f[-6:] == [F, MC, TR, _NC] + _P8 and _ov[-6:] == [F, MC, TR, _NC] + _P8, (_f[-6:], _ov[-6:]))
good, bad = M.validate_overrides({F: True, MC: 0.02, TR: 3}, c)
check("live overrides accepted", good == {F: True, MC: 0.02, TR: 3} and not bad, (good, bad))

print("--- 1. sizing to the joint depth")
api, bt = bot({"21": -1069, "22": -1069})
books(api, bt, {"21": {"asks": [(0.49, 314), (0.495, 5000)]}, "22": {"asks": [(0.50, 1069)]}},
      {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 314), (0.495, 5000)]},
       "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 1069)]}})
check("joint_unwind_qty: 314 at the limit on one leg, 1069 on the other -> 314",
      bt.joint_unwind_qty(["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, "buy") == 314)
traded = bt.execute_arbitrage(RACE, ["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, FVS,
                              time.monotonic(), action="buy", kind="unwind")
check("execute_arbitrage: both legs sent 314, both filled 314, nothing owed",
      sorted((o["exchangeId"], o["quantity"]) for o in api.wire) == [("21", 314), ("22", 314)]
      and traded == [314.0, 314.0] and not bt.pair_owed, (api.wire, traded, bt.pair_owed))
api, bt = bot({"21": -1069, "22": -1069})
books(api, bt, {"21": {"asks": [(0.48, 200), (0.49, 114), (0.495, 5000)]}, "22": {"asks": [(0.50, 1069)]}}, {})
check("better-priced levels count: 200 @0.48 + 114 @0.49 at a 0.49 limit -> 314 (top level alone: 200)",
      bt.joint_unwind_qty(["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, "buy") == 314)
api, bt = bot({"21": -300, "22": -1069})
books(api, bt, {"21": {"asks": [(0.49, 5000)]}, "22": {"asks": [(0.50, 5000)]}}, {})
check("never more than the smaller leg's position (300)",
      bt.joint_unwind_qty(["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, "buy") == 300)
api, bt = bot({"21": 900, "22": 1069})
books(api, bt, {"21": {"bids": [(0.52, 400), (0.51, 600), (0.50, 5000)]}, "22": {"bids": [(0.50, 5000)]}}, {})
check("long set: bids at >= the planned 0.51 on leg 21 = 1000, YES held 900 -> 900",
      bt.joint_unwind_qty(["21", "22"], {"21": (0.51, 1069), "22": (0.50, 1069)}, 1069, "sell") == 900)
api, bt = bot({"21": -1069, "22": -1069}, on=False)
books(api, bt, {"21": {"asks": [(0.49, 314), (0.495, 5000)]}, "22": {"asks": [(0.50, 1069)]}},
      {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 314), (0.495, 5000)]},
       "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 1069)]}})
bt.execute_arbitrage(RACE, ["21", "22"], {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069, FVS,
                     time.monotonic(), action="buy", kind="unwind")
check("flag off: sent 1069 on each leg as before", [o["quantity"] for o in api.wire] == [1069, 1069], api.wire)

print("--- 2. unequal fills [314, 1069] -> owed, follow-up")
ALERTS.clear()
api, bt, traded = short_case()
check("batch filled [314, 1069] (the live finding), both as covered sell NO",
      traded == [314.0, 1069.0] and all(o["side"] == "no" for o in api.wire), (traded, api.wire))
check("owed: 755 on the lagging leg 21 at its planned 0.49; no alert yet",
      bt.pair_owed.get(RACE, {}).get("legs") == {"21": 755} and bt.pair_owed[RACE]["price"]["21"] == 0.49
      and not ALERTS, (bt.pair_owed, ALERTS))
check("status pair_owed {race: 755}", bt.pair_owed_status() == {RACE: 755})
bt.health = {}
bt.write_status(ok=True)
st = json.load(open(M.bot_path(bt.cfg.status_file)))
check("status.json pair_owed", st.get("pair_owed") == {RACE: 755}, st.get("pair_owed"))
inv = {e: bt.ex[e].inv for e in bt.ex}
bt.arb_cooldown.clear()
n_wire = len(api.wire)
check("take_arbitrage leaves the owed race alone", RACE not in bt.take_arbitrage(inv, FVS, {}, time.monotonic())
      and len(api.wire) == n_wire)
sync(api, bt)
calls0 = len(api.calls)
acted = bt.pair_followup_step(FVS, time.monotonic(), {})
fu = api.wire[n_wire:]
later = api.calls[calls0:]
check("follow-up: ONE order on leg 21, a covered sell NO x755 at YES <= 0.49 + 0.01",
      len(fu) == 1 and fu[0]["exchangeId"] == "21" and fu[0]["side"] == "no" and fu[0]["action"] == "sell"
      and fu[0]["quantity"] == 755 and 1 - fu[0]["price"] <= 0.50 + 1e-9, fu)
check("our quotes on leg 21 pulled before it, leftovers cancelled after",
      [c[0] for c in later if c[0] in ("cancel_all", "batch")] == ["cancel_all", "batch", "cancel_all"]
      and all(c[1] == "21" for c in later if c[0] == "cancel_all"), later)
check("owed reaches 0, state cleared, set fully closed (both legs flat), accepted at 0 cash",
      not bt.pair_owed and api.inv == {"21": 0, "22": 0} and RACE in acted and not ALERTS,
      (bt.pair_owed, api.inv, ALERTS))
check("status pair_owed now {}", bt.pair_owed_status() == {})

ALERTS.clear()
api, bt, traded = short_case(reduce=False, cash=None)
sync(api, bt)
n_wire = len(api.wire)
bt.pair_followup_step(FVS, time.monotonic(), {})
fu = api.wire[n_wire:]
check("reduce_no_as_sell off: the follow-up is a plain buy YES x755 at <= 0.50",
      len(fu) == 1 and (fu[0]["side"], fu[0]["action"], fu[0]["quantity"]) == ("yes", "buy", 755)
      and fu[0]["price"] <= 0.50 + 1e-9 and not bt.pair_owed, fu)

print("--- 2. partial follow-up, tries exhausted")
ALERTS.clear()
api, bt, traded = short_case(exch21=[(0.495, 300), (0.52, 5000)])
sync(api, bt)
n_wire = len(api.wire)
bt.pair_followup_step(FVS, time.monotonic(), {})
check("try 1: 300 within 0.50 taken, 455 still owed",
      bt.pair_owed[RACE]["legs"] == {"21": 455} and api.wire[n_wire]["quantity"] == 300, bt.pair_owed)
for _ in range(10):
    sync(api, bt)
    bt.pair_followup_step(FVS, time.monotonic(), {})
check("only try 1 sent an order (nothing within the limit after: never an order not meant to fill)",
      len(api.wire) == n_wire + 1, api.wire[n_wire:])
check("after 6 tries: ONE alert 'left 455 shares unpaired after 6 tries', state cleared",
      len(ALERTS) == 1 and f"pair unwind on {RACE} left 455 shares unpaired after 6 tries" in ALERTS[0]
      and not bt.pair_owed, (ALERTS, bt.pair_owed))

print("--- 2. write budget short")
ALERTS.clear()
api, bt, traded = short_case()
sync(api, bt)
n_wire, n_calls = len(api.wire), len(api.calls)
api.writes_left = lambda: 2
for _ in range(10):
    bt.pair_followup_step(FVS, time.monotonic(), {})
check("budget short: waits - no order, no cancel, no try counted, owed kept",
      len(api.wire) == n_wire and not [c for c in api.calls[n_calls:] if c[0] in ("batch", "cancel_all")]
      and bt.pair_owed[RACE]["tries"] == 0 and bt.pair_owed[RACE]["legs"] == {"21": 755} and not ALERTS)
api.writes_left = lambda: 10 ** 6
bt.pair_followup_step(FVS, time.monotonic(), {})
check("...budget back: sent, owed 0", not bt.pair_owed and api.inv == {"21": 0, "22": 0}, (bt.pair_owed, api.inv))

print("--- 2. long set mirrored")
ALERTS.clear()
api, bt = bot({"21": 1069, "22": 1069})
books(api, bt, {"21": {"bids": [(0.51, 1069)], "asks": [(0.56, 1000)]},
                "22": {"bids": [(0.50, 1069)], "asks": [(0.55, 1000)]}},
      {"21": {"bids": [(0.51, 314), (0.505, 2000)], "asks": [(0.56, 1000)]},
       "22": {"bids": [(0.50, 1069)], "asks": [(0.55, 1000)]}})
traded = bt.execute_arbitrage(RACE, ["21", "22"], {"21": (0.51, 1069), "22": (0.50, 1069)}, 1069, FVS,
                              time.monotonic(), action="sell", kind="unwind")
check("long set filled [314, 1069] -> 755 owed on 21", traded == [314.0, 1069.0]
      and bt.pair_owed[RACE]["legs"] == {"21": 755}, (traded, bt.pair_owed))
sync(api, bt)
n_wire = len(api.wire)
bt.pair_followup_step(FVS, time.monotonic(), {})
fu = api.wire[n_wire:]
check("follow-up: sell YES x755 on 21 at >= 0.51 - 0.01, owed 0, both legs flat",
      len(fu) == 1 and (fu[0]["side"], fu[0]["action"], fu[0]["quantity"]) == ("yes", "sell", 755)
      and fu[0]["price"] >= 0.50 - 1e-9 and not bt.pair_owed and api.inv == {"21": 0, "22": 0}, (fu, api.inv))

print("--- 2. through a full cycle")
ALERTS.clear()
api, bt, traded = short_case()
bt.cycle()
bt.drain_writes(5)
check("the next cycle reads positions and evens the legs (owed 0, both flat)",
      not bt.pair_owed and api.inv.get("21") == 0 and api.inv.get("22") == 0 and not ALERTS,
      (bt.pair_owed, api.inv, ALERTS))

print("--- flag off: the old alert, no owed state, no status key")
ALERTS.clear()
api, bt, traded = short_case(on=False)
check("flag off: [314, 1069] alerts 'only partly filled' as before, nothing owed",
      traded == [314.0, 1069.0] and len(ALERTS) == 1 and "only partly filled" in ALERTS[0] and not bt.pair_owed,
      (traded, ALERTS))
bt.health = {}
bt.write_status(ok=True)
st = json.load(open(M.bot_path(bt.cfg.status_file)))
check("flag off: no pair_owed in status.json", "pair_owed" not in st)

print("--- red-team 1: owed capped at the lagging leg's post-fill LONE part")


def probe(n21, n22, a_on):
    """Short set NO held n21 / n22, buy-back of 1000 sets at 0.49 + 0.50; the exchange has only 300 on leg 21."""
    api, bt = bot({"21": -n21, "22": -n22}, cash=0.0)
    bt.cfg.no_set_aware_bids = a_on
    books(api, bt, {"21": {"asks": [(0.49, 5000)]}, "22": {"asks": [(0.50, 5000)]}},
          {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 300), (0.495, 5000)]},
           "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 5000)]}})
    traded = bt.execute_arbitrage(RACE, ["21", "22"], {"21": (0.49, 1000), "22": (0.50, 1000)}, 1000, FVS,
                                  time.monotonic(), action="buy", kind="unwind")
    return api, bt, traded


for a_on in (False, True):
    ALERTS.clear()
    api, bt, traded = probe(1000, 2000, a_on)
    check(f"probe NO 1000 / 2000 filled [300, 1000] -> -700 / -1000 (A {a_on}): leg 21 has no lone part, nothing owed",
          traded == [300.0, 1000.0] and api.inv == {"21": -700, "22": -1000} and not bt.pair_owed,
          (traded, api.inv, bt.pair_owed))
    n_wire, n_calls = len(api.wire), len(api.calls)
    for _ in range(8):
        sync(api, bt)
        acted = bt.pair_followup_step(FVS, time.monotonic(), {})
    check(f"...8 cycles (A {a_on}): no follow-up order, no write, no alert, nothing acted",
          len(api.wire) == n_wire and not [c for c in api.calls[n_calls:] if c[0] in ("batch", "cancel_all")]
          and not ALERTS and not acted, (api.wire[n_wire:], ALERTS))
ALERTS.clear()
api, bt, traded = probe(1000, 1200, False)
check("NO 1000 / 1200 filled [300, 1000] -> -700 / -200: owed capped 700 -> 500 (its lone part)",
      traded == [300.0, 1000.0] and bt.pair_owed.get(RACE, {}).get("legs") == {"21": 500}, bt.pair_owed)
sync(api, bt)
n_wire = len(api.wire)
bt.pair_followup_step(FVS, time.monotonic(), {})
fu = api.wire[n_wire:]
check("...follow-up: one covered sell NO x500, accepted at 0 cash, legs -200 / -200, state cleared",
      len(fu) == 1 and (fu[0]["side"], fu[0]["quantity"]) == ("no", 500) and api.inv == {"21": -200, "22": -200}
      and not bt.pair_owed and not ALERTS, (fu, api.inv, bt.pair_owed, ALERTS))

print("--- red-team 1: follow-up leg whose NO is all in a set: cleared, not a try, race not acted")
ALERTS.clear()
api, bt = bot({"21": -700, "22": -1000}, cash=0.0)
bt.cfg.no_set_aware_bids = True
books(api, bt, {}, {"21": {"bids": [(0.45, 1000)], "asks": [(0.49, 5000)]},
                    "22": {"bids": [(0.46, 1000)], "asks": [(0.50, 5000)]}})
bt.pair_owed[RACE] = {"action": "buy", "legs": {"21": 700}, "t": time.monotonic(), "tries": 0,
                      "price": {"21": 0.49, "22": 0.50}}
n_calls = len(api.calls)
acted = bt.pair_followup_step(FVS, time.monotonic(), {})
check("no_sell_order None -> leg cleared at once: state dropped, no write, not acted, no alert",
      not bt.pair_owed and not api.wire and RACE not in acted and not ALERTS
      and not [c for c in api.calls[n_calls:] if c[0] in ("batch", "cancel_all")],
      (bt.pair_owed, api.wire, acted, ALERTS))

print("--- red-team 4: a lagging leg leaves the market list while another stays")
ALERTS.clear()
api, bt = bot({"21": 100, "22": 100})
books(api, bt, {}, {"21": {"bids": [(0.30, 1000)], "asks": [(0.60, 1000)]},
                    "22": {"bids": [(0.30, 1000)], "asks": [(0.60, 1000)]}})
bt.pair_owed[RACE] = {"action": "sell", "legs": {"21": 40, "22": 30}, "t": time.monotonic(), "tries": 0,
                      "price": {"21": 0.51, "22": 0.50}}
del bt.ex["22"]
err = None
try:
    for _ in range(8):
        bt.pair_followup_step(FVS, time.monotonic(), {})
except Exception as e:                            # the old code: KeyError in the alert every cycle
    err = e
check("no error; the gone leg dropped; after 6 tries ONE alert naming 40 on leg 21 only, state cleared",
      err is None and len(ALERTS) == 1 and "left 40 shares unpaired after 6 tries" in ALERTS[0]
      and not bt.pair_owed, (repr(err), ALERTS, bt.pair_owed))

print("--- 3. live_sim mirror (scripted race with unequal depth)")
import live_sim as L                                    # noqa: E402
import strategy_sim as S                                # noqa: E402


class Stub(L.LiveSim):
    def __init__(self):            # no world: fields set by hand
        pass


def sim_stub(on, wcap=1000.0):
    st = Stub()
    _api, b = make_bot(live=False)
    b.cfg.pair_unwind_followup = on
    st.cfg, st.bot = b.cfg, b
    ms = []
    for eid, asks in (("21", [(0.49, 314), (0.495, 2000)]), ("22", [(0.50, 1069)])):
        m = S.Mkt("busy", 0.5, 0.0, 1, False, [])
        m.eid, m.inv, m.cash = eid, -1069.0, 0.0
        m.orders = [S.Order("h", True, 0.45, 1000, 0)] + [S.Order("h", False, p, q, 0) for p, q in asks]
        b.ex[eid].inv = -1069.0
        ms.append(m)
    st.races = {RACE: ms}
    st.takes, st.wlog, st.writes, st.wcap, st.free = [], [], 0, wcap, 1e9
    st.unwinds = st.unwind_sh = st.arbs = st.arb_sh = st.outsider_arb_sh = 0
    st.arb_pnl = 0.0
    # a stale plan: 1069 sets at the top asks, which only 314 on leg 21 meet when the takes land
    b.arb_plan = lambda *a, **k: ("unwind", "buy", {"21": (0.49, 1069), "22": (0.50, 1069)}, 1069)
    st.arbitrage(10.0, {"21": -1069.0, "22": -1069.0}, dict(FVS))
    return st, ms


st, ms = sim_stub(False)
check("sim flag off: the old imbalance (-755 / 0), no follow-up", (ms[0].inv, ms[1].inv) == (-755.0, 0.0)
      and getattr(st, "pair_followup_sh", 0) == 0, (ms[0].inv, ms[1].inv))
st, ms = sim_stub(True)
check("sim flag on: 0 unpaired (both legs flat), pair_followup_sh 755, its take's 3 writes charged",
      (ms[0].inv, ms[1].inv) == (0.0, 0.0) and st.pair_followup_sh == 755 and st.writes == 3,
      (ms[0].inv, ms[1].inv, getattr(st, "pair_followup_sh", None), st.writes))
st2, ms2 = sim_stub(True, wcap=0.0)
check("sim: no write room -> refused, the imbalance stays", (ms2[0].inv, ms2[1].inv) == (-755.0, 0.0)
      and getattr(st2, "pair_followup_sh", 0) == 0, (ms2[0].inv, ms2[1].inv))

print("--- flag off identical to Package 7 part 1 (d22170b) on a grid")
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", "d22170b:mm_bot.py"], capture_output=True, text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p7a.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p7a", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k: False
except Exception as e:                            # no git here: the pin is skipped (reported)
    print("    (base module unavailable:", e, ")")


def base_bot(inv, reduce):
    """A d22170b Bot, set up exactly as bot(on=False) above, plus that one."""
    api_n, bn = bot(inv, reduce=reduce, on=False)
    cfg = base.Config()
    for k in base.Config.__dataclass_fields__:
        setattr(cfg, k, getattr(bn.cfg, k))
    api_b = FakeApi(True)
    api_b.markets_list = list(api_n.markets_list)
    api_b.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
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
    check("base module (git show d22170b:mm_bot.py) loaded", False)
else:
    same, diffs = True, []
    for reduce in (False, True):
        for i21, i22 in ((-1069, -1069), (-300, -1069), (1069, 1069), (400, 900), (-40, 40)):
            for ex21 in ((0.49, 314), (0.49, 1069)):
                api_n, bn, api_b, bb = base_bot({"21": i21, "22": i22}, reduce)
                for x, a in ((bn, api_n), (bb, api_b)):
                    a.books["21"] = {"bids": [lvl(0.51, ex21[1]), lvl(0.505, 2000)],
                                     "asks": [lvl(ex21[0], ex21[1]), lvl(0.495, 2000)]}
                    a.books["22"] = {"bids": [lvl(0.50, 1069)], "asks": [lvl(0.50, 1069)]}
                    x.ex["21"].book = {"bids": [lvl(0.51, 1069)], "asks": [lvl(0.49, 1069)]}
                    x.ex["22"].book = {"bids": [lvl(0.50, 1069)], "asks": [lvl(0.50, 1069)]}
                iv = {e: bn.ex[e].inv for e in bn.ex}
                if bn.arb_plan(["21", "22"], iv, FVS, False) != bb.arb_plan(["21", "22"], iv, FVS, False):
                    same = False
                    diffs.append(("arb_plan", reduce, i21, i22, ex21))
                n_al = len(ALERTS)
                for action, lv in (("buy", {"21": (0.49, 1069), "22": (0.50, 1069)}),
                                   ("sell", {"21": (0.51, 1069), "22": (0.50, 1069)})):
                    for kind in ("unwind", "arb"):
                        rn = bn.execute_arbitrage(RACE, ["21", "22"], lv, 1069, FVS, 0.0, action=action, kind=kind)
                        al_n = ALERTS[n_al:]
                        n_al = len(ALERTS)
                        rb = bb.execute_arbitrage(RACE, ["21", "22"], lv, 1069, FVS, 0.0, action=action, kind=kind)
                        al_b = ALERTS[n_al:]
                        n_al = len(ALERTS)
                        if rn != rb or al_n != al_b:
                            same = False
                            diffs.append(("execute_arbitrage", reduce, i21, i22, ex21, action, kind, rn, rb))
                if strip(api_n.wire) != strip(api_b.wire) or api_n.inv != api_b.inv or bn.pair_owed:
                    same = False
                    diffs.append(("wire", reduce, i21, i22, ex21))
    check("arb_plan / execute_arbitrage (results, alerts, orders, positions) identical with the flag off", same,
          diffs[:4])
    ok_c, diffs = True, []
    for reduce in (False, True):
        for i21, i22 in ((-817, -975), (40, 40), (0, 0)):
            api_n, bn, api_b, bb = base_bot({"21": i21, "22": i22}, reduce)
            for x in (bn, bb):
                for _ in range(2):
                    x.cycle()
                    x.drain_writes(5)
            if strip(api_n.wire) != strip(api_b.wire):
                ok_c = False
                diffs.append((reduce, i21, i22))
    check("two full cycles send the same orders with the flag off", ok_c, diffs[:2])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
