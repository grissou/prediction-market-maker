"""Package 10 follow-up (red team C-1): take_respect_reserve - a stale-quote take is skipped when its cash need would leave less
than alloc_mm_reserve free, so the market-making reserve the allocator builds is not spent by the takes first.
Run:  python tests/test_take_reserve.py      (exit code 0 = all passed)"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                 # noqa: E402
import mm_bot as M                                         # noqa: E402

logging.basicConfig(level=logging.ERROR)
RESULTS = []
M.alert = lambda msg: None


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def bot(inv, cash, respect, reserve=15000.0):
    api, bt = make_bot(live=True)
    c = bt.cfg
    c.reduce_no_as_sell = c.no_set_aware_bids = c.cash_gate_enabled = c.adding_factor_per_market = True
    c.selftest_enabled = False
    c.take_respect_reserve = respect
    c.alloc_mm_reserve = reserve
    api.inv, api.cash, api.set_collateral = dict(inv), cash, True
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    for e, q in inv.items():
        bt.ex[e].inv = float(q)
    bt.cg_cash, bt.cg_reserved, bt.cg_spent = float(cash), 0.0, 0.0
    bt.cg_read_at = time.monotonic()
    return api, bt


print("--- settings")
c = M.Config()
check("take_respect_reserve default False", c.take_respect_reserve is False)
check("overridable bool", M.OVERRIDABLE.get("take_respect_reserve") == (False, True))
good, bad = M.validate_overrides({"take_respect_reserve": True}, c)
check("override accepted", not bad and good.get("take_respect_reserve") is True, (good, bad))

print("--- the helper: need vs cash_left and the reserve")
api, bt = bot({"11": 0}, cash=20000.0, respect=True)
buy = {"exchangeId": "11", "side": "yes", "action": "buy", "quantity": 1000, "price": 0.40, "tournamentId": bt.tid}
check("20k cash, buy needs 400: 19.6k >= 15k reserve -> not blocked", not bt.take_blocked_by_reserve(buy))
bt.cg_cash = 15300.0
check("15.3k cash, needs 400 -> 14.9k < 15k -> blocked", bt.take_blocked_by_reserve(buy) and bt.take_reserve_blocked == 1)
bt.cfg.take_respect_reserve = False
check("flag off: never blocked", not bt.take_blocked_by_reserve(buy))
bt.cfg.take_respect_reserve = True
bt.cfg.alloc_mm_reserve = 0.0
check("no reserve: never blocked", not bt.take_blocked_by_reserve(buy))
bt.cfg.alloc_mm_reserve = 15000.0
bt.cfg.cash_gate_enabled = False
check("gate off: never blocked", not bt.take_blocked_by_reserve(buy))
bt.cfg.cash_gate_enabled = True
api, bt = bot({"11": -1000}, cash=100.0, respect=True)
covered = {"exchangeId": "11", "side": "yes", "action": "buy", "quantity": 500, "price": 0.40, "tournamentId": bt.tid,
           "_no_sell": True}
check("a covered sale (NO held, lone) needs 0 cash: never blocked", not bt.take_blocked_by_reserve(covered))
api, bt = bot({"11": 2000}, cash=100.0, respect=True)
sell = {"exchangeId": "11", "side": "yes", "action": "sell", "quantity": 1000, "price": 0.40, "tournamentId": bt.tid}
check("selling YES we hold needs 0 cash: never blocked", not bt.take_blocked_by_reserve(sell))
sell["quantity"] = 3000
check("selling 3000 on 2000 held: the 1000 beyond is a NO purchase (600) -> blocked at 100 cash",
      bt.take_blocked_by_reserve(sell))

print("--- execute_take honours it before pulling our quotes")
for respect in (False, True):
    api, bt = bot({"11": 0, "12": 0}, cash=15200.0, respect=respect)
    ex = bt.ex["11"]
    ex.book = api.full_book("11")
    ex.take_dir = 1
    n_cancel = len(api.sent("cancel_all"))
    did = bt.execute_take(ex, 0.40, 0.0, 0.14, time.monotonic())
    pulled = len(api.sent("cancel_all")) > n_cancel
    if respect:
        check("respect on, cash 15.2k vs reserve 15k: the take is skipped before any cancel",
              did is False and not pulled, (did, pulled))
    else:
        check("respect off: the take goes ahead (pulls the quote)", did and pulled, (did, pulled))
api, bt = bot({"11": 0, "12": 0}, cash=40000.0, respect=True)
ex = bt.ex["11"]
ex.book = api.full_book("11")
ex.take_dir = 1
check("respect on, 40k cash: the take goes ahead", bt.execute_take(ex, 0.40, 0.0, 0.14, time.monotonic()) is not False)
src = open(os.path.join(HERE, "..", "mm_bot.py")).read()
check("status.json carries take_reserve_blocked only when the flag is on or it fired",
      'self.health["take_reserve_blocked"] = getattr(self, "take_reserve_blocked", 0)' in src)

n_ok, n = sum(RESULTS), len(RESULTS)
print(f"{n_ok}/{n} passed")
sys.exit(0 if n_ok == n else 1)
