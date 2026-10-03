"""
Offline tests for Package 6 "reduce NO holdings as covered NO sales" (reduce_no_as_sell). Live 3 Oct: every order
went out as side "yes", so a bid that buys back a short (we hold NO) was "buy YES @ p", a new cash purchase the
exchange refused at 0 free cash. With the flag on, the part of such a bid up to the NO held goes out as
"sell NO @ 1-p" (no cash), capped at the NO held; asks and adding bids are unchanged; it reads back as our bid at p.
Covers: settings, wire form, quote path (cap, no churn), every take path, parse_order, adoption, fill attribution,
the start-up self-test leg, flag off identical on a grid, the fake exchange's cash model, live_sim's cash model.

Run:  python tests/test_reduce_no.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot               # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEY = "reduce_no_as_sell"
ALERTS = []
M.alert = lambda msg: ALERTS.append(msg)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def no_sells(api, eid=None):
    return [o for o in api.wire if o["side"] == "no" and o["action"] == "sell" and (eid is None or o["exchangeId"] == eid)]


def bot(on=True, inv=None, cash=None):
    api, b = make_bot()
    b.cfg.reduce_no_as_sell = on
    b.cfg.selftest_enabled = False            # the self-test leg has its own tests below
    api.inv = dict(inv or {})
    api.cash = cash
    for e, q in api.inv.items():
        if e in b.ex:
            b.ex[e].inv = float(q)
    return api, b


print("--- settings")
c = M.Config()
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
check("default off", getattr(c, KEY, None) is False)
_p7 = ["no_set_aware_bids", "pair_no_unwind_max_cost",      # Package 7's settings come after it
       "pair_unwind_followup", "pair_unwind_followup_max_cost", "pair_unwind_followup_tries"]
check("last of Package 6 in Config and OVERRIDABLE (then Package 7's)",
      _f[_f.index(KEY) + 1:] == _p7 and _ov[_ov.index(KEY) + 1:] == _p7, (_f[-3:], _ov[-3:]))
check("bool override", M.OVERRIDABLE.get(KEY) == (False, True))
good, bad = M.validate_overrides({KEY: True}, c)
check("True accepted live", good == {KEY: True} and not bad, bad)

print("--- wire form")
plain = {"exchangeId": "21", "side": "yes", "action": "buy", "quantity": 30, "price": 0.4}
check("unmarked order sent unchanged (same object)", M.wire_order(plain) is plain)
w = M.wire_order({**plain, "_no_sell": True})
check("marked bid -> sell NO @ 1-p, same qty, marker stripped",
      w == {"exchangeId": "21", "side": "no", "action": "sell", "quantity": 30, "price": 0.6}, w)

print("--- parse_order round trip")
base = {"id": 7, "exchangeId": "21", "quantity": 30, "open": True, "expirationDate": None}
r = M.parse_order({**base, "side": "no", "action": "sell", "priceLimit": 0.6})
check("sell NO @ 0.6 -> our bid at 0.4", r.is_bid and abs(r.price - 0.4) < 1e-9 and r.qty == 30, r)
r = M.parse_order({**base, "side": "no", "action": "buy", "priceLimit": 0.6})
check("buy NO @ 0.6 -> our ask at 0.4", not r.is_bid and abs(r.price - 0.4) < 1e-9, r)
check("a sell NO locks no cash (reserved_cash)",
      M.reserved_cash([{**base, "side": "no", "action": "sell", "priceLimit": 0.6}]) == 0.0)

print("--- new_order")
now = M.utcnow()
api, b = bot(True, {"21": -50})
ex = b.ex["21"]
o, m = b.new_order(ex, True, 0.40, 30, 0.45, now)
check("reducing bid (30 <= 50 NO): marked, qty 30, note no_sell",
      o.get("_no_sell") and o["quantity"] == 30 and o["action"] == "buy" and o["price"] == 0.4 and m.get("no_sell")
      and m["our_side"] == "bid", (o, m))
check("wire: sell NO @ 0.6 x30", M.wire_order(o)["side"] == "no" and M.wire_order(o)["action"] == "sell"
      and M.wire_order(o)["price"] == 0.6 and M.wire_order(o)["quantity"] == 30)
b.cover_planned = {}
o, m = b.new_order(ex, True, 0.40, 100, 0.45, now)
check("bid bigger than the NO held: capped at 50 (the add part waits)", o.get("_no_sell") and o["quantity"] == 50, o)
b.cover_planned = {}
o, m = b.new_order(ex, False, 0.50, 30, 0.45, now)
check("ask unchanged (sell YES, unmarked)", "_no_sell" not in o and o["action"] == "sell" and o["side"] == "yes", o)
ex.inv = 50.0
o, m = b.new_order(ex, True, 0.40, 30, 0.45, now)
check("adding bid (long): buy YES, unmarked", "_no_sell" not in o and o["quantity"] == 30, o)
ex.inv = -0.5
o, m = b.new_order(ex, True, 0.40, 30, 0.45, now)
check("under 1 NO share: buy YES", "_no_sell" not in o, o)

print("--- quote path end to end (fake exchange with 0 free cash)")


def cyc(b, n=1):
    for _ in range(n):
        b.cycle()
        b.drain_writes(5)


for on in (False, True):
    api, b = bot(on, {"21": -50}, cash=0.0)
    b.cfg.fail_pause_seconds = 0              # a refused order doesn't pause the market: later cycles re-plan it
    cyc(b)
    sells = no_sells(api, "21")
    bids21 = [x for x in api.ours("21") if x[0] == "bid"]
    if on:
        check("on: the bid on the short market goes out as ONE sell NO, qty <= 50, and rests as our bid",
              len(sells) == 1 and sells[0]["quantity"] <= 50 and len(bids21) == 1
              and abs(bids21[0][1] - round(1 - sells[0]["price"], 3)) < 1e-9, (sells, bids21))
        api.cash = None                       # the ask (adding) is accepted from now on
        cyc(b, 3)
        check("on: no churn - three more cycles keep the same bid (the ask is placed meanwhile)",
              len(no_sells(api, "21")) == 1 and [x for x in api.ours("21") if x[0] == "bid"] == bids21
              and [x for x in api.ours("21") if x[0] == "ask"], (no_sells(api, "21"), api.ours("21")))
        my = [o for o in b.my_orders.values() if o.eid == "21" and o.is_bid]
        check("on: our record holds it as a bid at the YES price", len(my) == 1 and abs(my[0].price - bids21[0][1]) < 1e-9,
              my)
        check("on: order notes say bid / no_sell", any(v.get("no_sell") and v.get("our_side") == "bid"
                                                       for v in b.order_meta.values()), b.order_meta)
        api.fill("21", True, 20)                  # someone takes 20 of our covered sale
        cyc(b, 2)
        my = [o for o in b.my_orders.values() if o.eid == "21" and o.is_bid]
        check("on: after a 20-share fill the rest (30) still rests as our bid, NO held 30",
              api.inv["21"] == -30 and len(my) == 1 and my[0].qty == 30
              and all(o["quantity"] <= 30 for o in no_sells(api, "21")[1:]), (api.inv, my, no_sells(api, "21")))
    else:
        check("off: no sell NO is ever sent, and the reducing bid is refused at 0 cash (today's deadlock)",
              not sells and not bids21 and not any("_no_sell" in o for o in api.wire), api.wire)

print("--- take paths")
api, b = bot(True, {"21": -50})
ex = b.ex["21"]
ex.book = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}
ex.take_dir = 1
b.execute_take(ex, 0.70, -50.0, 0.6, time.monotonic())
t = api.wire[-1] if api.wire else {}
check("execute_take buying back the short: sell NO @ 0.44 capped at 50", t.get("side") == "no" and t.get("action") == "sell"
      and t.get("quantity") <= 50 and abs(t.get("price", 0) - 0.44) < 1e-9, t)
check("execute_take note: bid, take, no_sell", any(v.get("take") and v.get("no_sell") and v["our_side"] == "bid"
                                                   for v in b.order_meta.values()))
check("the fake traded it: NO held fell", api.inv["21"] > -50, api.inv)
api, b = bot(True, {"21": 50})
ex = b.ex["21"]
ex.book = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}
ex.take_dir = 1
b.execute_take(ex, 0.70, 50.0, 0.6, time.monotonic())
check("execute_take adding (long): buy YES", api.wire and api.wire[-1]["side"] == "yes" and api.wire[-1]["action"] == "buy",
      api.wire)

api, b = bot(True, {"21": -40})
b.cfg.hold_target_hours, b.hold_open_at = 1.0, -1e18
plan = {"eid": "21", "buy": True, "qty": 60, "price": 0.56, "notional": 33.6}
b.hold_take_plan = lambda *a, **k: [dict(plan)]
b.take_aged({"21": -40.0}, {"21": 0.5}, {}, time.monotonic())
t = api.wire[-1] if api.wire else {}
check("take_aged buying back: sell NO @ 0.44, capped at the 40 NO held", t.get("side") == "no" and t.get("quantity") == 40
      and abs(t.get("price", 0) - 0.44) < 1e-9, t)

api, b = bot(True, {"21": -40, "22": -40})
st = {"other": "21", "sign": -1, "price": 0.43, "leg": "22", "slice": 40}
b.pair_passive_take("Utah Senate", st, 40, {}, time.monotonic())
t = no_sells(api, "21")
check("pair-passive second leg (short set): sell NO x40 @ 1 - ask", len(t) == 1 and t[0]["quantity"] == 40
      and abs(t[0]["price"] - 0.44) < 1e-9, api.wire)

api, b = bot(True, {"21": -40, "22": -40})
b.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.56, 1000), "22": (0.52, 1000)}, 30, {}, time.monotonic(),
                    action="buy", kind="unwind")
t = no_sells(api)
check("arbitrage / short-set unwind legs: both sell NO x30 at 1 - ask", len(t) == 2
      and sorted((o["exchangeId"], o["quantity"], o["price"]) for o in t) == [("21", 30, 0.44), ("22", 30, 0.48)], api.wire)
api, b = bot(True, {"21": -10, "22": -40})
b.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.56, 1000), "22": (0.52, 1000)}, 30, {}, time.monotonic(),
                    action="buy", kind="unwind")
legs = {o["exchangeId"]: o for o in api.wire}
check("arbitrage: a leg bigger than its NO held stays buy YES (legs equal), the other is a sell NO",
      legs["21"]["side"] == "yes" and legs["21"]["quantity"] == 30 and legs["22"]["side"] == "no"
      and legs["22"]["quantity"] == 30, legs)

print("--- adoption, recovery and fills")
api, b = bot(True, {"21": -50})
ex = b.ex["21"]
nm = time.monotonic()
o_bid, m_bid = b.new_order(ex, True, 0.40, 30, 0.45, now)
o_ask, m_ask = b.new_order(ex, False, 0.60, 30, 0.45, now)
b.unconfirmed["21"] = [(o_ask, m_ask, nm), (o_bid, m_bid, nm)]
raw = {"id": 900, "exchangeId": "21", "side": "no", "action": "sell", "priceLimit": 0.6, "quantity": 30, "open": True,
       "expirationDate": o_bid["expirationDate"]}
b.sync_orders([raw], nm)
check("open-orders list: the sell NO is adopted as the lost bid (notes bid / no_sell)",
      b.order_meta.get(900, {}).get("our_side") == "bid" and b.order_meta[900].get("no_sell")
      and b.my_orders[900].is_bid and abs(b.my_orders[900].price - 0.4) < 1e-9, (b.order_meta.get(900), b.my_orders))
check("...and the ask at 0.60 is still unconfirmed", [x[0]["action"] for x in b.unconfirmed.get("21", [])] == ["sell"])

api, b = bot(True, {"21": -50})
ex = b.ex["21"]
o_bid, m_bid = b.new_order(ex, True, 0.40, 30, 0.45, now)
o_ask, m_ask = b.new_order(ex, False, 0.60, 30, 0.45, now)
b.unconfirmed["21"] = [(o_ask, m_ask, nm), (o_bid, m_bid, nm)]
api.fills = [{"id": 1, "orderId": 901, "exchangeId": "21", "price": 0.6, "quantity": -10, "side": "no",
              "filledAt": M.iso(M.utcnow())}]
b.log_fills({})
rows = M.read_fills(b.cfg.fills_csv)
check("a NO-side fill at 0.6 of the lost sell NO -> fills.csv our_side bid, quote_price 0.4, fill_price kept (NO price)",
      len(rows) == 1 and rows[0]["our_side"] == "bid" and float(rows[0]["quote_price"]) == 0.4
      and float(rows[0]["fill_price"]) == 0.6 and float(rows[0]["qty"]) == 10, rows)
check("fills.csv columns unchanged", list(rows[0]) == M.FillLogger.COLUMNS if rows else False)

api, b = bot(False, {"21": -50})
ex = b.ex["21"]
o_ask, m_ask = b.new_order(ex, False, 0.40, 30, 0.45, now)
b.unconfirmed["21"] = [(o_ask, m_ask, nm)]
api.fills = [{"id": 1, "orderId": 902, "exchangeId": "21", "price": 0.6, "quantity": -10, "side": "no",
              "filledAt": M.iso(M.utcnow())}]
b.log_fills({})
check("flag off: a converted ask's NO fill still matches the ask (unchanged)",
      b.order_meta.get(902, {}).get("our_side") == "ask", b.order_meta.get(902))

print("--- start-up self-test leg")


def with_books(b):
    """The leg only tests where a book is known (nosell_safe): give every market its fake book."""
    for e, x in b.ex.items():
        x.book = b.api.full_book(e) if e in b.api.books else None
    return b


def tick_until(b, n=200):
    for _ in range(n):
        b.nosell_tick()
        if b.nosell_future is None and (b.nosell_state is not None or b.nosell_next > time.monotonic()):
            return
        time.sleep(0.01)


api, b = bot(True, {"21": -50, "22": -10}, cash=0.0)
b.cfg.selftest_enabled = True
with_books(b)
check("live + self-test on: not in effect before the leg ran", not b.reduce_no_on())
o, _ = b.new_order(b.ex["21"], True, 0.4, 30, 0.45, now)
check("...so the bid is buy YES meanwhile (today's behaviour)", "_no_sell" not in o)
tick_until(b)
legs = no_sells(api)
check("leg: ONE 1-share sell NO @ 0.995 on the biggest NO holding", len(legs) == 1 and legs[0]["quantity"] == 1
      and legs[0]["price"] == 0.995 and legs[0]["exchangeId"] == "21", legs)
check("leg: cancelled at once", not api.orders and api.sent("cancel_order"), (api.orders, api.calls[-3:]))
check("leg: passed -> in effect", b.nosell_state == "ok" and b.reduce_no_on())
check("leg: the test market's hold is restored", b.ex["21"].pending_until != float("inf"))
b.nosell_tick()
check("leg runs once", len(no_sells(api)) == 1)

ALERTS.clear()
api, b = bot(True, {"21": -50}, cash=0.0)
b.cfg.selftest_enabled = True
api.refuse_no_sell = "Insufficient available funds"
with_books(b)
try:
    tick_until(b)
    exited = False
except SystemExit:
    exited = True
check("refused: no exit, alert sent, fallback (state off, not in effect)",
      not exited and b.nosell_state == "off" and not b.reduce_no_on() and ALERTS and "refused" in ALERTS[-1],
      (b.nosell_state, ALERTS))
o, _ = b.new_order(b.ex["21"], True, 0.4, 30, 0.45, now)
check("refused: bids buy YES again", "_no_sell" not in o)

api, b = bot(True, {"21": -50})
b.cfg.selftest_enabled = True
api.batch_error = M.ApiError(503, "SERVICE_UNAVAILABLE", "busy")
with_books(b)
tick_until(b)
check("busy: not decided, retried later", b.nosell_state is None and b.nosell_next > time.monotonic())
api, b = bot(True, {"21": 50})
b.cfg.selftest_enabled = True
b.nosell_tick()
check("no NO held anywhere: no leg placed", b.nosell_future is None and not api.wire)
api, b = bot(False, {"21": -50})
b.cfg.selftest_enabled = True
b.nosell_tick()
check("flag off: no leg placed", b.nosell_future is None and not api.wire and b.nosell_state is None)

print("--- self-test leg: never where it could fill (a YES ask at 0.005)")
api, b = bot(True, {"21": -50, "22": -10}, cash=0.0)
b.cfg.selftest_enabled = True
with_books(b)
b.ex["21"].book = {"bids": [lvl(0.48, 1000)], "asks": [lvl(M.PMIN, 5), lvl(0.56, 1000)]}
tick_until(b)
legs = no_sells(api)
check("biggest NO holding has an ask at 0.005: the leg goes to the next one (22), not 21",
      len(legs) == 1 and legs[0]["exchangeId"] == "22" and b.nosell_state == "ok", legs)
api, b = bot(True, {"21": -50}, cash=0.0)
b.cfg.selftest_enabled = True
with_books(b)
b.ex["21"].book = {"bids": [], "asks": [lvl(0.004, 5)]}
b.nosell_tick()
check("only NO holding has an ask at/below 0.005: leg skipped this tick, nothing sent, undecided",
      b.nosell_future is None and not api.wire and b.nosell_state is None
      and b.ex["21"].pending_until != float("inf"), (api.wire, b.nosell_state))
b.ex["21"].book = {"bids": [lvl(0.48, 1000)], "asks": []}
tick_until(b)
check("...once that ask is gone (book with no ask at all): the leg runs there",
      len(no_sells(api)) == 1 and no_sells(api)[0]["exchangeId"] == "21" and b.nosell_state == "ok", api.wire)
api, b = bot(True, {"21": -50}, cash=0.0)
b.cfg.selftest_enabled = True
b.ex["21"].book = None
b.nosell_tick()
check("no book downloaded yet: leg skipped (book unknown)", b.nosell_future is None and not api.wire)

print("--- self-test and sell-NO leg never overlap")
api, b = bot(True, {"21": -50}, cash=0.0)
b.cfg.selftest_enabled = True
with_books(b)
b.selftest_passed = False


class _Pending:
    def done(self):
        return False


b.nosell_future = _Pending()
b.selftest_next = 0.0
b.selftest_tick()
check("selftest_tick while the sell-NO leg is out: the main test doesn't start",
      b.selftest_future is None and not api.sent("batch"), api.calls[-3:])
b.nosell_future = None
b.selftest_future = _Pending()
b.nosell_next = 0.0
b.nosell_tick()
check("nosell_tick while the main test is out: the leg doesn't start", not no_sells(api))
b.selftest_future = None

print("--- lost batch: covered sale at b and ask at 1-b, YES-side fill at 1-b")
for on in (True, False):
    api, b = bot(on, {"21": -50})
    ex = b.ex["21"]
    o_bid, m_bid = b.new_order(ex, True, 0.40, 30, 0.45, now)
    o_ask, m_ask = b.new_order(ex, False, 0.60, 30, 0.45, now)
    if on:
        check("setup: the bid at 0.40 is a covered sale", o_bid.get("_no_sell"), o_bid)
    b.unconfirmed["21"] = [(o_ask, m_ask, nm), (o_bid, m_bid, nm)]
    api.fills = [{"id": 1, "orderId": 903, "exchangeId": "21", "price": 0.6, "quantity": 10, "side": "yes",
                  "filledAt": M.iso(M.utcnow())}]
    b.log_fills({})
    side = b.order_meta.get(903, {}).get("our_side")
    if on:
        check("on: the fill is adopted as the covered bid (not the ask at 0.60), the ask stays unconfirmed",
              side == "bid" and b.order_meta[903].get("no_sell")
              and [x[0]["action"] for x in b.unconfirmed.get("21", [])] == ["sell"], (b.order_meta.get(903),))
    else:
        check("off: unchanged order of tries (YES-side fill at 0.60 matches the ask at 0.60)", side == "ask",
              b.order_meta.get(903))

print("--- flag off identical on a grid")
same = True
for inv in (-500, -50, -1, 0, 1, 50):
    for is_bid in (True, False):
        for size in (1, 30, 100, 1000):
            api, b = bot(False, {"21": inv})
            o, m = b.new_order(b.ex["21"], is_bid, 0.4, size, 0.45, now)
            exp = {"exchangeId": "21", "side": "yes", "action": "buy" if is_bid else "sell", "quantity": size,
                   "price": 0.4, "tournamentId": b.tid}
            if any(o[k] != v for k, v in exp.items()) or "_no_sell" in o or "no_sell" in m or M.wire_order(o) is not o:
                same = False
            if b.cover_no_qty("21", inv, 0) != 0 or b.no_sell_order(dict(exp), inv) != exp:
                same = False
check("off: new_order / no_sell_order / wire unchanged for every inventory, side and size", same)
grid_ok = True
for inv in (-300, -40, 0, 40):
    api, b = bot(False, {"21": inv, "12": inv})
    cyc(b, 2)
    if no_sells(api) or any("_no_sell" in o for o in api.wire):
        grid_ok = False
check("off: two full cycles on an inventory grid send only side-yes orders", grid_ok)

print("--- fake exchange cash model")
api, b = bot(True, {"21": -50}, cash=0.0)
exp_ = M.iso(M.utcnow() + M.timedelta(seconds=600))
ords = [{"exchangeId": "21", "side": "yes", "action": "buy", "quantity": 10, "price": 0.40, "expirationDate": exp_},
        {"exchangeId": "21", "side": "no", "action": "sell", "quantity": 50, "price": 0.60, "expirationDate": exp_},
        {"exchangeId": "21", "side": "no", "action": "sell", "quantity": 1, "price": 0.60, "expirationDate": exp_}]
res = api.place_batch(ords)
check("0 cash: buy YES refused 'Insufficient available funds'", not res[0]["ok"]
      and "Insufficient" in res[0]["data"]["error"]["message"], res[0])
check("0 cash: covered sell NO (50 of 50 held) accepted, locks no cash", res[1]["ok"] and api.cash_locked() == 0.0, res[1])
check("0 cash: a sell NO beyond the NO held (51st) refused like a purchase", not res[2]["ok"], res[2])
api.inv["11"] = 20
r2 = api.place_batch([{"exchangeId": "11", "side": "yes", "action": "sell", "quantity": 20, "price": 0.30,
                       "expirationDate": exp_}])
check("0 cash: covered sell YES accepted", r2[0]["ok"], r2)
api.cash = 100.0
r3 = api.place_batch([{"exchangeId": "11", "side": "yes", "action": "buy", "quantity": 100, "price": 0.15,
                       "expirationDate": exp_}])
check("100 cash: buy YES 100 @ 0.15 accepted, locks 15", r3[0]["ok"] and abs(api.cash_locked() - 15.0) < 1e-9, r3)

print("--- live_sim cash model")
import live_sim as L                                      # noqa: E402


class _O:
    def __init__(self, is_bid, price, qty, level=0):
        self.is_bid, self.price, self.qty, self.level, self.owner = is_bid, price, qty, level, "us"


class _Cfg:
    def __init__(self, on):
        self.reduce_no_as_sell = on


class _Sim:
    def __init__(self, on):
        self.cfg, self.free, self.short_locks = _Cfg(on), 0.0, True   # the pre-fix live cash world

    free_short = L.LiveSim.free_short


class _Mk:
    def __init__(self, inv):
        self.inv, self.orders = inv, []


check("order_lock off: a bid buying back a short locks cash for ALL of it", L.order_lock(_O(True, 0.4, 30), -50, False) == 12.0)
check("order_lock on: free", L.order_lock(_O(True, 0.4, 30), -50, True) == 0.0)
check("order_lock: asks unchanged either way",
      L.order_lock(_O(False, 0.6, 30), 50, False) == L.order_lock(_O(False, 0.6, 30), 50, True) == 0.0)
s_off, s_on = _Sim(False), _Sim(True)
out_off = L.cash_clip(s_off, _Mk(-50), [(True, 0.4, 30, 0, None)])
out_on = L.cash_clip(s_on, _Mk(-50), [(True, 0.4, 30, 0, None)])
check("cash_clip off, 0 free cash: the reducing bid is clipped away, cash_refused_sh 30",
      out_off == [] and s_off.cash_refused_sh == 30, (out_off, getattr(s_off, "cash_refused_sh", None)))
check("cash_clip on: kept, nothing refused", out_on == [(True, 0.4, 30, 0, None)]
      and getattr(s_on, "cash_refused_sh", 0) == 0, out_on)
s_on2 = _Sim(True)
out = L.cash_clip(s_on2, _Mk(-50), [(True, 0.4, 100, 0, None)])
check("cash_clip on: a bid bigger than the short is capped at it (as Bot.plan_change)", out == [(True, 0.4, 50, 0, None)], out)
check("live_sim reports cash_refused_sh", "cash_refused_sh" in L.KEYS)

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
