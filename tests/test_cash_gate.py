"""
Offline tests for Package 8 item 1: the cash gate (cash_gate_enabled) and the per-market adding side
(adding_factor_per_market). Live 3 Oct 19:56 (capital 100%, free cash ~0, capital_ceiling_adding_size_factor 0):
299 refusals with 400 "Insufficient available funds" an hour - 85 on NO+NO races, 44 on FLAT markets, 5 on YES
holdings - each costing a write. Cause: the adding side for the size factors is picked by the race-netted position
(eff_i = x_i - mean(others)), so a NO+NO leg -817 / -975 reads "net long" (its ask = buying NO is "reducing"), a flat
market whose other leg is long reads "net short" (its bid = buying YES), a small YES holding the same.
Covers: the three categories on the fake exchange at 0 cash (flags off: sent and refused; flags on: none refused,
covered and lone-part sales still out), the pins of adding_factor_per_market, the gate's math (bid / ask / covered
need, reserve, decrement within a cycle, cancel credit, joint batches, the cash read), the pair batch and the
self-tests' 1-share orders still going out, takes pre-checked before pulling quotes, a market with cash unchanged,
and both flags off identical to the Package 7 code (2ef6d12) on a grid.

Run:  python tests/test_cash_gate.py      (exit code 0 = all passed)
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
from fakes import FakeApi, lvl, make_bot                  # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
M.alert = lambda msg: None
G, R, P = "cash_gate_enabled", "cash_gate_reserve", "adding_factor_per_market"
BASE_REV = "2ef6d12"                                       # Package 7 (START_HERE), the code before this package


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def bot(inv, gate=False, per_market=False, cash=0.0, factor=0.0, live=True):
    """make_bot's two races (Ohio 11 Rep / 12 Dem, Utah 21 Rep / 22 Dem) as live on 3 Oct: covered NO sales and
    set-aware bids on, the capital ceiling on with the adding factor `factor`, 200-share quotes; the fake exchange
    with `cash` free (None = no cash model) and its set-collateral rule; the P&L read reports that cash."""
    api, bt = make_bot(live=live)
    c = bt.cfg
    c.reduce_no_as_sell = c.no_set_aware_bids = True
    c.selftest_enabled = False
    c.capital_in_positions_max_frac, c.capital_ceiling_adding_size_factor = 0.001, factor
    c.order_size_frac = 0.002
    setattr(c, G, gate)
    setattr(c, P, per_market)
    api.inv, api.cash, api.set_collateral = dict(inv), cash, cash is not None
    if cash is not None:
        api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    api.res_log = []
    real = api.place_batch

    def pb(orders):
        res = real(orders)
        api.res_log += list(zip([dict(o) for o in orders], res))
        return res
    api.place_batch = pb
    for e, q in api.inv.items():
        bt.ex[e].inv = float(q)
    return api, bt


def cyc(bt, n=2):
    for _ in range(n):
        bt.cycle()
        bt.drain_writes(5)


def refused(api, eid=None):
    return [o for o, r in api.res_log if not r.get("ok") and "Insufficient" in str(r.get("data"))
            and (eid is None or o["exchangeId"] == eid)]


def sent(api, eid, side="yes", action=None):
    return [o for o, r in api.res_log if o["exchangeId"] == eid and o["side"] == side
            and (action is None or o["action"] == action)]


def ok(api, eid):
    return [(o["side"], o["action"], o["quantity"]) for o, r in api.res_log if o["exchangeId"] == eid and r.get("ok")]


NONO = {"21": -817, "22": -975}                 # Utah: NO on both legs (set 817, lone part 158 on 22)
FLAT = {"11": 0, "12": 500}                     # Ohio: Rep flat, Dem long (Rep's eff = -500)
SMALL = {"11": 100, "12": 500}                  # Ohio: a small YES holding next to a bigger one (eff -400)

print("--- settings")
c = M.Config()
_f, _ov = list(M.Config.__dataclass_fields__), list(M.OVERRIDABLE)
check("defaults: gate off, reserve 25, per-market off", (getattr(c, G), getattr(c, R), getattr(c, P)) == (False, 25.0, False))
check("one contiguous block in Config and OVERRIDABLE, in order", _f[_f.index(G):_f.index(G) + 3] == [G, R, P]
      and _ov[_ov.index(G):_ov.index(G) + 3] == [G, R, P], (_f[-3:], _ov[-3:]))
good, bad = M.validate_overrides({G: True, R: 10.0, P: True}, c)
check("live overrides accepted", good == {G: True, R: 10.0, P: True} and not bad, (good, bad))

print("--- the three live categories at 0 cash, flags OFF: sent and refused (reproduced)")
api, bt = bot({**NONO, **FLAT})
cyc(bt)
check("NO+NO: the smaller leg's ASK (sell YES not held = buy NO) is sent and refused", len(refused(api, "21")) >= 1
      and all(o["action"] == "sell" and o["side"] == "yes" for o in refused(api, "21")), refused(api))
check("NO+NO: the bigger leg's lone part still sells (sell NO x158, accepted)", ("no", "sell", 158) in ok(api, "22"), ok(api, "22"))
check("FLAT market next to a long leg: its BID (buy YES) is sent and refused", len(refused(api, "11")) >= 1
      and all(o["action"] == "buy" for o in refused(api, "11")), refused(api))
api2, bt2 = bot(SMALL)
cyc(bt2)
check("small YES holding: its BID (buy more YES) is sent and refused", len(refused(api2, "11")) >= 1
      and all(o["action"] == "buy" for o in refused(api2, "11")), refused(api2))
check("small YES holding: no ask selling the holding (the race-netted 'adding' side, factor 0)",
      not sent(api2, "11", action="sell"), sent(api2, "11"))
check("the long leg's covered ask goes out either way", ok(api, "12") and ok(api2, "12"))

print("--- flags ON (gate + per-market): nothing refused, covered sales still out")
api, bt = bot({**NONO, **FLAT}, gate=True, per_market=True)
cyc(bt)
check("no refusal at all", not refused(api) and all(r.get("ok") for _, r in api.res_log), [(o, r) for o, r in api.res_log if not r.get("ok")])
check("NO+NO: covered bid of 158 on the bigger leg (sell NO, accepted)", ("no", "sell", 158) in ok(api, "22"), ok(api, "22"))
check("NO+NO: nothing on the smaller leg, no ask on either", not sent(api, "21", "yes") and not sent(api, "21", "no")
      and not sent(api, "22", action="sell", side="yes"))
check("FLAT: no bid (and no ask)", not sent(api, "11") and not sent(api, "11", "no"))
check("long leg's covered ask still out", ("yes", "sell", 200) in ok(api, "12"), ok(api, "12"))
check("status.json fields: cash_gated / cash_trimmed / cash_capped_quotes / cash_gate_left",
      all(k in bt.health for k in ("cash_gated", "cash_trimmed", "cash_capped_quotes", "cash_gate_left"))
      and bt.health["cash_gate_left"] == 0.0, {k: bt.health.get(k) for k in ("cash_gated", "cash_gate_left")})
api2, bt2 = bot(SMALL, gate=True, per_market=True)
cyc(bt2)
check("small YES: ask sells the holding (100), no bid, nothing refused",
      ok(api2, "11") == [("yes", "sell", 100)] and not sent(api2, "11", action="buy") and not refused(api2), api2.res_log)

print("--- gate alone (per-market off): nothing refused")
for inv in ({**NONO, **FLAT}, SMALL):
    api, bt = bot(inv, gate=True)
    cyc(bt, 3)
    check(f"gate only {sorted(inv.items())}: no refusal, covered sales out", not refused(api) and ok(api, "12"),
          refused(api))
    check(f"gate only {sorted(inv.items())}: the plan capped the cash-needing quote sides (status counts them)",
          bt.health.get("cash_capped_quotes", 0) >= 1, bt.health)

print("--- adding_factor_per_market alone (the pins)")
api, bt = bot({**NONO, **FLAT}, per_market=True)
cyc(bt)
q21, q22, q11 = bt.ex["21"].quote, bt.ex["22"].quote, bt.ex["11"].quote
check("NO+NO -817 / -975, factor 0: no ask on either leg", q21.ask is None and q22.ask is None, (q21, q22))
check("...the bigger leg bids (covered, sent as 158 = its lone part), the smaller none",
      q22.bid is not None and ("no", "sell", 158) in ok(api, "22") and not sent(api, "21") and not sent(api, "21", "no"),
      (q22, ok(api, "22")))
check("flat market with the other leg long: no bid", q11.bid is None and not sent(api, "11"), q11)
check("no refusal (the per-market side alone removes all three categories here)", not refused(api), refused(api))
api2, bt2 = bot(SMALL, per_market=True)
cyc(bt2)
q = bt2.ex["11"].quote
check("small YES holding: ask sells the holding, no bid", q.bid is None and q.ask_size == 100, q)
check("compute_quote: a reducing side beyond the position is adding (factor 0 -> capped at the position)",
      M.compute_quote(0.5, 50, -400, None, None, bt.cfg, adding_factor=0.0, adding_per_market=True).ask_size == 50)
check("compute_quote: factor 0.5 per market -> half of the part beyond the position (50 + 150 / 2 of 200)",
      M.compute_quote(0.5, 50, -400, None, None, bt.cfg, adding_factor=0.5, adding_per_market=True).ask_size == 125)
q_net = M.compute_quote(0.5, -817, 158, None, None, bt.cfg, adding_factor=0.0, net_inv=158)
q_pm = M.compute_quote(0.5, -817, 158, None, None, bt.cfg, adding_factor=0.0, net_inv=158, adding_per_market=True)
check("compute_quote NO+NO leg: race-net -> ask out, bid 0; per market -> bid out, ask 0",
      q_net.ask_size > 0 and q_net.bid_size == 0 and q_pm.bid_size > 0 and q_pm.ask_size == 0, (q_net, q_pm))
q_ro = M.compute_quote(0.5, -817, 158, None, None, bt.cfg, reduce_only=True, net_inv=158)
check("(report) the reduce-only race-net clip alone also leaves the cash-needing ask on that leg",
      q_ro.ask_size == 158 and q_ro.bid_size == 0, q_ro)
q_ro2 = M.compute_quote(0.5, -817, 158, None, None, bt.cfg, reduce_only=True, net_inv=158, adding_factor=0.0,
                        adding_per_market=True)
check("...which the per-market factor 0 removes (no quote at all on that leg)", q_ro2.ask_size == 0 and q_ro2.bid_size == 0, q_ro2)
q_t = M.compute_quote(0.5, -817, 158, None, None, bt.cfg, adding_limit_factor=0.0, net_inv=158, adding_per_market=True)
check("turnover-dead limit factor picks the side by the market's own position too (ask held at 1 share)",
      q_t.ask_size == 1, q_t)

print("--- gate math")
api, bt = bot({**NONO, "11": 100}, gate=True, cash=None)
bt.cg_cash, bt.cg_reserved, bt.cg_spent = 1000.0, 0.0, 0.0
check("left = cash - reserved - spent - reserve (1000 - 25)", bt.cash_left() == 975.0, bt.cash_left())
bt.cg_reserved = 100.0
check("resting orders' lock counts (875)", bt.cash_left() == 875.0, bt.cash_left())
bt.cg_reserved = 0.0
o = {"exchangeId": "12", "side": "yes", "action": "buy", "quantity": 3000, "price": 0.5, "tournamentId": "T"}
keep, need = bt.cash_gate_orders([o])
check("bid: price a share -> 3000 @ 0.5 trimmed to 1950 (975 / 0.5)", keep == [True] and o["quantity"] == 1950
      and abs(need - 975) < 1e-6, (keep, o["quantity"], need))
o2 = dict(o, quantity=10)
keep, need = bt.cash_gate_orders([o2])
check("decrement within the cycle: the next bid is dropped", keep == [False] and need == 0 and bt.cash_left() == 0)
check("counts: 1 trimmed, 1 gated", (bt.cash_trimmed, bt.cash_gated) == (1, 1), (bt.cash_trimmed, bt.cash_gated))
bt.cg_spent = 975.0 - 40.0                      # 40 left
o = {"exchangeId": "11", "side": "yes", "action": "sell", "quantity": 300, "price": 0.6, "tournamentId": "T"}
bt.cash_gate_orders([o])
check("ask: the YES held is free, beyond it 1 - price a share -> 100 + 40 / 0.4 = 200", o["quantity"] == 200, o)
bt.cg_spent = 975.0                              # 0 left
o = {"exchangeId": "11", "side": "yes", "action": "sell", "quantity": 300, "price": 0.6, "tournamentId": "T"}
bt.cash_gate_orders([o])
check("ask at 0 cash: only the YES held (100)", o["quantity"] == 100, o)
o = {"exchangeId": "22", "side": "yes", "action": "buy", "quantity": 300, "price": 0.45, "_no_sell": True}
bt.cash_gate_orders([o])
check("covered sell NO at 0 cash: its lone part (158) only", o["quantity"] == 158, o)
bt.cg_spent = 975.0 - 50.0
o = {"exchangeId": "22", "side": "yes", "action": "buy", "quantity": 300, "price": 0.45, "_no_sell": True}
bt.cash_gate_orders([o])
check("...with 50 cash: + 50 set-breaking shares at 1 a share (208)", o["quantity"] == 208, o)
bt.cg_spent = 975.0
o = {"exchangeId": "21", "side": "yes", "action": "buy", "quantity": 50, "price": 0.5, "_no_sell": True}
check("covered sell NO with no lone part at 0 cash: dropped", bt.cash_gate_orders([o])[0] == [False])
legs = [{"exchangeId": e, "side": "yes", "action": "buy", "quantity": 500, "price": p, "_no_sell": True}
        for e, p in (("21", 0.5), ("22", 0.5))]
keep, need = bt.cash_gate_orders(legs, joint=True)
check("NO+NO pair batch (sell NO on both legs) at 0 cash: needs 0, sent whole", keep == [True, True] and need == 0
      and [x["quantity"] for x in legs] == [500, 500], (keep, need, legs))
bt.cg_spent = 975.0 - 9.0                        # 9 left
legs = [{"exchangeId": e, "side": "yes", "action": "buy", "quantity": 100, "price": p} for e, p in (("11", 0.4), ("12", 0.5))]
keep, need = bt.cash_gate_orders(legs, joint=True)
check("joint buy-side arbitrage: every leg shrinks alike (9 / 0.9 = 10 sets)", keep == [True, True]
      and [x["quantity"] for x in legs] == [10, 10] and abs(need - 9) < 1e-6, (legs, need))
legs = [{"exchangeId": e, "side": "yes", "action": "buy", "quantity": 100, "price": p} for e, p in (("11", 0.4), ("12", 0.5))]
check("joint with nothing left: all or none -> none", bt.cash_gate_orders(legs, joint=True)[0] == [False, False])

print("--- the cash read")
check("an 'available' field is used as is (net of locks)", bt.cash_figure({"availableBalance": "12.5", "cashBalance": 99}, {})
      == (12.5, True))
check("else a cash field (locks still in it)", bt.cash_figure({"cashBalance": 99, "totalAccountValue": 1}, {}) == (99.0, False))
check("else account value - market value", bt.cash_figure({"totalAccountValue": 1000}, {"summary": {"totalMarketValue": 900}})
      == (100.0, False))
check("nothing usable -> None", bt.cash_figure({"foo": 1}, {}) == (None, False))
api, bt = bot({}, gate=True, cash=500.0)
api.orders[1] = {"id": 1, "exchangeId": "21", "quantity": 100, "open": True, "side": "yes", "action": "buy",
                 "priceLimit": 0.5, "expirationDate": M.iso(M.utcnow() + M.timedelta(hours=1))}
bt.cfg.capital_in_positions_max_frac = 0.0
bt.cycle()
bt.drain_writes(5)
check("cycle read: cash 500 from the P&L reply, the resting bid's 50 locked", bt.cg_cash == 500.0 and bt.cg_reserved == 50.0,
      (getattr(bt, "cg_cash", None), getattr(bt, "cg_reserved", None)))
check("...and what that cycle sent is counted as spent (left = 500 - 50 - 25 - spent >= 0)",
      bt.cash_left() >= 0 and abs(bt.cash_left() - (500 - 50 - 25 - bt.cg_spent)) < 1e-6, bt.cash_left())
check("...no refusal", not refused(api), refused(api))
api, bt = bot({}, gate=True, cash=None)
bt.cg_cash, bt.cg_reserved, bt.cg_spent = 100.0, 0.0, 75.0
r = M.Resting(7, "21", True, 0.5, 40, None)
bt.my_orders[7] = r
bt.cancel("21", [r], whole_exchange=False)
check("a confirmed cancel gives its lock back (0.5 x 40 = 20)", abs(bt.cg_spent - 55.0) < 1e-9, bt.cg_spent)
api, bt = bot({}, gate=True, cash=None)
bt.cg_cash = None
o = {"exchangeId": "11", "side": "yes", "action": "buy", "quantity": 5, "price": 0.5}
check("no cash figure read yet: only cash-free orders (a bid is dropped)", bt.cash_gate_orders([o])[0] == [False])

print("--- the send point: dropped orders, no request, no back-off")
api, bt = bot({"11": 100}, gate=True, cash=None)
bt.cg_cash, bt.cg_reserved, bt.cg_spent = 25.0, 0.0, 0.0      # 0 left after the reserve
n0 = len(api.sent("batch"))
res = bt.place_orders([{"exchangeId": "12", "side": "yes", "action": "buy", "quantity": 5, "price": 0.5,
                        "tournamentId": "T", "expirationDate": M.iso(M.utcnow() + M.timedelta(minutes=5))}])
check("a batch dropped whole sends no request", len(api.sent("batch")) == n0 and res[0].get("cash_gated")
      and not res[0].get("ok"), res)
orders = [{"exchangeId": "12", "side": "yes", "action": "buy", "quantity": 5, "price": 0.5, "tournamentId": "T",
           "expirationDate": M.iso(M.utcnow() + M.timedelta(minutes=5))},
          {"exchangeId": "11", "side": "yes", "action": "sell", "quantity": 50, "price": 0.2, "tournamentId": "T",
           "expirationDate": M.iso(M.utcnow() + M.timedelta(minutes=5))}]
res = bt.place_orders(orders)
check("mixed batch: the covered ask goes (its result at its own index), the bid is gated",
      len(res) == 2 and res[0].get("cash_gated") and res[1].get("ok") and res[1]["index"] == 1
      and api.wire[-1]["exchangeId"] == "11", res)
now_m = time.monotonic()
bt.apply_batch([(o_, {"our_side": "x"}) for o_ in orders], res, now_m)
check("apply_batch: a gated order sets no back-off on its exchange", bt.ex["12"].pause_until <= now_m)

print("--- the pair batch and the self-tests still go out at 0 cash")
api, bt = bot(NONO, gate=True, per_market=True)
cyc(bt, 1)
fvs = {e: 0.5 for e in bt.ex}
api.books["21"]["asks"] = [lvl(0.50, 1000)]
api.books["22"]["asks"] = [lvl(0.50, 1000)]
for e in ("21", "22"):
    bt.ex[e].book = api.full_book(e)
n0 = len(api.res_log)
traded = bt.execute_arbitrage("Utah Senate", ["21", "22"], {"21": (0.50, 1000), "22": (0.50, 1000)}, 300, fvs,
                              time.monotonic(), action="buy", kind="unwind")
batch = api.res_log[n0:]
check("pair unwind batch: both legs as sell NO x300, accepted, no refusal",
      [(o["side"], o["action"], o["quantity"]) for o, _ in batch] == [("no", "sell", 300)] * 2
      and all(r.get("ok") for _, r in batch) and traded == [300.0, 300.0], (batch, traded))
api, bt = bot(NONO, gate=True)
bt.cg_cash, bt.cg_reserved, bt.cg_spent = 0.0, 0.0, 0.0
n0 = len(api.wire)
bt.selftest_attempt("11", 60)
check("main self-test: its two 1-share orders are sent (exempt)", [o["quantity"] for o in api.wire[n0:]] == [1, 1],
      api.wire[n0:])
n0 = len(api.wire)
verdict, _ = bt.nosell_run("22")
check("sell-NO check: its 1-share order is sent (exempt) and accepted", [o["quantity"] for o in api.wire[n0:]] == [1]
      and verdict == "ok", (api.wire[n0:], verdict))

print("--- takes: pre-checked before our quotes are pulled")
for gate in (False, True):
    api, bt = bot({"11": 0, "12": 0}, gate=gate)
    bt.cg_cash, bt.cg_reserved, bt.cg_spent = 0.0, 0.0, 0.0
    ex = bt.ex["11"]
    ex.book = api.full_book("11")
    ex.take_dir = 1
    n_cancel = len(api.sent("cancel_all"))
    did = bt.execute_take(ex, 0.40, 0.0, 0.14, time.monotonic())
    pulled = len(api.sent("cancel_all")) > n_cancel
    if gate:
        check("gate on, 0 cash: a buying take is skipped before the cancel (no write, no refusal)",
              did is False and not pulled and not refused(api), (did, pulled))
    else:
        check("gate off, 0 cash: the take pulls our quote and is refused (today)", did and pulled and refused(api, "11"),
              (did, pulled, refused(api)))

print("--- a market with cash available: unchanged")
same, diffs = True, []
for inv in ({**NONO, **FLAT}, SMALL, {"21": 300, "22": -200}):
    w = []
    for gate in (False, True):
        api, bt = bot(inv, gate=gate, cash=1e7, factor=0.5)
        cyc(bt, 2)
        w.append([{k: v for k, v in o.items() if k != "expirationDate"} for o in api.wire])
    if w[0] != w[1]:
        same = False
        diffs.append((inv, w[0][:3], w[1][:3]))
check("gate on with plenty of cash sends exactly what the gate off sends (2 cycles, 3 books)", same, diffs[:1])

print(f"--- flags off identical to Package 7 ({BASE_REV}) on a grid")
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p7.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p7", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k: False
except Exception as e:                            # no git here: the pin is skipped (reported)
    print("    (base module unavailable:", e, ")")

if base is None:
    check(f"base module (git show {BASE_REV}:mm_bot.py) loaded", False)
else:
    cfg_n, cfg_b = M.Config(), base.Config()
    same, diffs = True, []
    for inv in (-817, -100, 0, 100, 900):
        for eff in (-400, 0, 158, 500):
            for af in (1.0, 0.5, 0.0):
                for alf in (1.0, 0.3):
                    for ro in (False, True):
                        for bb, ba in ((0.40, 0.46), (None, None)):
                            kw = dict(reduce_only=ro, net_inv=eff, adding_factor=af, adding_limit_factor=alf)
                            qn = M.compute_quote(0.43, inv, eff, bb, ba, cfg_n, **kw)
                            qb = base.compute_quote(0.43, inv, eff, bb, ba, cfg_b, **kw)
                            if tuple(qn.__dict__.values()) != tuple(qb.__dict__.values()):
                                same = False
                                diffs.append((inv, eff, af, alf, ro, qn, qb))
    check("compute_quote identical with adding_per_market off (5 x 4 x 3 x 2 x 2 x 2 grid)", same, diffs[:2])

    def base_pair(inv, cash):
        api_n, bn = bot(inv, cash=cash, factor=0.0)
        cfg = base.Config()
        for k in base.Config.__dataclass_fields__:
            setattr(cfg, k, getattr(bn.cfg, k))
        api_b = FakeApi(True)
        api_b.markets_list = list(api_n.markets_list)
        api_b.books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                       for e, v in api_n.books.items()}
        api_b.inv, api_b.cash, api_b.set_collateral = dict(api_n.inv), cash, cash is not None
        if cash is not None:
            api_b.pnl = lambda: (api_b.log("pnl"), {"totalAccountValue": api_b.equity, "cashBalance": api_b.cash})[1]
        bb_ = base.Bot(api_b, cfg)
        for e, q in api_b.inv.items():
            if e in bb_.ex:
                bb_.ex[e].inv = float(q)
        return api_n, bn, api_b, bb_

    ok_c, diffs = True, []
    for inv in ({**NONO, **FLAT}, SMALL, {"21": 300, "22": -200}, {}):
        for cash in (0.0, None):
            api_n, bn, api_b, bb_ = base_pair(inv, cash)
            cyc(bn, 2)
            cyc(bb_, 2)
            sn = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_n.wire]
            sb = [{k: v for k, v in o.items() if k != "expirationDate"} for o in api_b.wire]
            if sn != sb:
                ok_c = False
                diffs.append((inv, cash, sn[:3], sb[:3]))
    check("two full cycles send the same orders with both flags off (4 books x cash 0 / no cash model)", ok_c, diffs[:1])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
