"""
Offline tests for the Round 4 write savers (both OFF by default):
  (a) no-chase      no_chase_enabled / no_chase_tolerance_ticks / no_chase_fv_epsilon
  (b) TTL saver     ttl_tiers_enabled / order_ttl_busy / order_ttl_quiet / ttl_jitter_frac / ttl_busy_size_frac,
                    ttl_expire_as_cancel / ttl_expire_grace_seconds
in mm_bot (Bot.plan_change, Bot.new_order) and in the simulators' re-quote rule (strategy_sim.plan_changes, which
tests/live_sim.py inherits), plus a parity test pinning the two to the same keep / reprice decision. No network.

Run:  python tests/test_write_savers.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402
import strategy_sim as S                                  # noqa: E402
from mm_bot import Quote, TICK                            # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


SAVER_KEYS = ["no_chase_enabled", "no_chase_tolerance_ticks", "no_chase_fv_epsilon", "ttl_tiers_enabled",
              "order_ttl_busy", "order_ttl_quiet", "ttl_jitter_frac", "ttl_busy_size_frac", "ttl_expire_as_cancel",
              "ttl_expire_grace_seconds"]

print("--- settings and validation")
c = M.Config()
check("both savers OFF by default", not c.no_chase_enabled and not c.ttl_tiers_enabled and not c.ttl_expire_as_cancel)
check("defaults: no-chase 2 ticks / 0.25c fv epsilon; TTL busy 60 min, quiet 100 min, +-20%, grace 5 s",
      (c.no_chase_tolerance_ticks, c.no_chase_fv_epsilon, c.order_ttl_busy, c.order_ttl_quiet, c.ttl_jitter_frac,
       c.ttl_expire_grace_seconds) == (2, 0.0025, 3600.0, 6000.0, 0.2, 5.0))
fields = list(M.Config.__dataclass_fields__)
check("new settings are the LAST Config fields, in order", fields[-len(SAVER_KEYS):] == SAVER_KEYS, fields[-12:])
check("...and the last OVERRIDABLE entries, in order", list(M.OVERRIDABLE)[-len(SAVER_KEYS):] == SAVER_KEYS)
good, bad = M.validate_overrides({"no_chase_enabled": True, "no_chase_tolerance_ticks": 3, "ttl_tiers_enabled": True,
                                  "order_ttl_quiet": 7200, "ttl_expire_as_cancel": True, "ttl_jitter_frac": 0.3}, c)
check("valid saver overrides are accepted", not bad and good["order_ttl_quiet"] == 7200.0
      and isinstance(good["order_ttl_quiet"], float) and good["no_chase_tolerance_ticks"] == 3, bad)
for raw, why in (({"no_chase_tolerance_ticks": 0}, "tolerance 0"), ({"no_chase_tolerance_ticks": 11}, "tolerance 11"),
                 ({"no_chase_tolerance_ticks": 2.5}, "tolerance not whole"), ({"no_chase_enabled": 1}, "flag not bool"),
                 ({"no_chase_fv_epsilon": 0.05}, "epsilon 5c"), ({"order_ttl_quiet": 9000}, "quiet TTL 2.5 h"),
                 ({"order_ttl_busy": 200}, "busy TTL 200 s"), ({"ttl_jitter_frac": 0.6}, "jitter 60%"),
                 ({"ttl_expire_grace_seconds": 120}, "grace 2 min"), ({"ttl_busy_size_frac": -0.1}, "busy frac < 0"),
                 ({"order_ttl_busy": 400}, "busy 400 s x 0.8 < 2 x refresh 180"),
                 ({"order_ttl_busy": 1000, "refresh_before_expiry": 500, "order_ttl": 3600}, "refresh 500 vs busy 1000 x 0.8")):
    good, bad = M.validate_overrides(raw, c)
    check(f"refused: {why}", bad and not any(k in good for k in raw if k in SAVER_KEYS), (good, bad))

print("--- TTL tiers (order_ttl_for / ttl_tier)")
c = M.Config()
check("tiers off: exactly order_ttl whatever the tier or draw",
      all(M.order_ttl_for(c, t, u) == c.order_ttl for t in ("headline", "busy", "quiet") for u in (0.0, 0.5, 0.99)))
c.ttl_tiers_enabled = True
check("tiers on, middle draw: headline order_ttl, busy order_ttl_busy, quiet order_ttl_quiet",
      [M.order_ttl_for(c, t, 0.5) for t in ("headline", "busy", "quiet")] == [1800.0, 3600.0, 6000.0])
check("jitter +-20%: busy 2880..4320", abs(M.order_ttl_for(c, "busy", 0.0) - 2880) < 1e-6
      and abs(M.order_ttl_for(c, "busy", 1.0) - 4320) < 1e-6)
c.order_ttl_quiet, c.ttl_jitter_frac = 7200.0, 0.5
check("bounded: never above MAX_ORDER_TTL (2 h) whatever the settings", M.MAX_ORDER_TTL == 7200.0
      and max(M.order_ttl_for(c, t, u / 100) for t in ("headline", "busy", "quiet") for u in range(100)) <= 7200.0)
c.order_ttl_busy, c.refresh_before_expiry = 300.0, 180.0
check("...and never below 2 x refresh_before_expiry (no order 'about to expire' at birth)",
      M.order_ttl_for(c, "busy", 0.0) == 360.0)
c = M.Config()
check("ttl_tier: headline races, busy at >= ttl_busy_size_frac of the account, else quiet",
      (M.ttl_tier(True, 10, 1e5, c), M.ttl_tier(False, 1000, 1e5, c), M.ttl_tier(False, 999, 1e5, c))
      == ("headline", "busy", "quiet"))


def expiry_secs(order):
    return (M.parse_ts(order["expirationDate"]) - M.utcnow()).total_seconds()


a, b = make_bot()
b.cfg.headline_races, b.cfg.size_by_activity = ("Ohio Senate",), True
b.size_plan = {"21": 0.02 * b.bankroll(), "22": 0.001 * b.bankroll()}
now = M.utcnow()
offs = {e: expiry_secs(b.new_order(b.ex[e], True, 0.40, 100, 0.45, now)[0]) for e in ("11", "21", "22")}
check("Bot.new_order, tiers off: every order expires after order_ttl", all(abs(v - 1800) < 5 for v in offs.values()), offs)
b.cfg.ttl_tiers_enabled = True
offs = {e: [expiry_secs(b.new_order(b.ex[e], True, 0.40, 100, 0.45, M.utcnow())[0]) for _ in range(40)]
        for e in ("11", "21", "22")}
check("tiers on: headline 24-36 min, busy 48-72 min, quiet 80-120 min (jittered)",
      all(1440 - 5 <= x <= 2160 for x in offs["11"]) and all(2880 - 5 <= x <= 4320 for x in offs["21"])
      and all(4800 - 5 <= x <= 7200 for x in offs["22"]), {k: (min(v), max(v)) for k, v in offs.items()})
check("...the jitter spreads them (not all the same)", max(offs["22"]) - min(offs["22"]) > 600)
lad = expiry_secs(b.new_order(b.ex["22"], True, 0.40, 100, 0.45, M.utcnow(), level=2)[0])
check("ladder orders (level >= 1) keep order_ttl", abs(lad - 1800) < 5, lad)
_, meta = b.new_order(b.ex["22"], True, 0.40, 100, 0.45, M.utcnow())
check("no-chase off: order notes unchanged (no inv / qty)", "inv" not in meta and "qty" not in meta)

print("--- Bot.plan_change: no-chase")


def setup(no_chase=True):
    a, b = make_bot()
    b.cfg.no_chase_enabled = no_chase
    b.cycle()
    bid = [o for o in b.my_orders.values() if o.eid == "21" and o.is_bid][0]
    return a, b, bid


def decide(b, bid, ticks=2, fv=None, limit_ticks=None, size=None, now=None, max_size=None):
    """True = Bot.plan_change re-prices the bid (it's among the cancels). Target = bid + ticks; limit = target
    (or bid + limit_ticks: below the old price = the old order is unsafe)."""
    meta = b.order_meta[bid.order_id]
    tgt = M.rnd(bid.price + ticks * TICK)
    lim = M.rnd(bid.price + (limit_ticks if limit_ticks is not None else max(ticks, 0)) * TICK)
    q = Quote(tgt, size or int(bid.qty), None, 0, lim, None, bid_max=max_size)
    ch = b.plan_change(b.ex["21"], q, [bid], meta["fv"] if fv is None else fv, now or M.utcnow(), time.monotonic())
    return ch is not None and bid in ch.doomed, ch


a, b, bid = setup(no_chase=True)
meta = b.order_meta[bid.order_id]
check("no-chase on: placement notes hold fv, inventory and size", meta.get("inv") == b.ex["21"].inv
      and meta.get("qty") == int(bid.qty) and meta.get("fv") is not None, meta)
check("CHASE SUPPRESSED: target 2 ticks better, fv and inventory unchanged -> order kept", not decide(b, bid)[0])
check("...also when the target moved 2 ticks away (rival left)", not decide(b, bid, ticks=-2)[0])
check("...1 tick: kept (as before)", not decide(b, bid, ticks=1)[0])
check("3 ticks off: re-priced (beyond no_chase_tolerance_ticks)", decide(b, bid, ticks=3)[0])
check("FV MOVE: fair value 0.5c from placement -> re-priced at the normal 1 tick", decide(b, bid, fv=meta["fv"] + 0.005)[0])
check("...fv within no_chase_fv_epsilon (0.2c): still kept", not decide(b, bid, fv=meta["fv"] + 0.002)[0])
b.ex["21"].inv += 50
check("INVENTORY CHANGE: -> re-priced", decide(b, bid)[0])
b.ex["21"].inv -= 50
q0 = bid.qty
bid.qty = q0 - 10
check("FILL (partial, still above keep_fraction): -> re-priced", decide(b, bid)[0])
bid.qty = q0
ok, ch = decide(b, bid, ticks=-2, limit_ticks=-1)
check("UNSAFE (old order beyond its limit price): -> re-priced, urgent", ok and ch.unsafe and ch.key[0] == 0,
      ch and ch.key)
check("SIZE above the risk limit (max size below the order): -> re-priced",
      decide(b, bid, size=int(q0) - 50, max_size=int(q0) - 50)[0])
exp_now = bid.expires - M.timedelta(seconds=60)
check("EXPIRY (60 s left < refresh_before_expiry): -> re-priced", decide(b, bid, now=exp_now)[0])
del b.order_meta[bid.order_id]["inv"]
check("no placement notes (order from before the switch / adopted): normal 1-tick rule", decide(b, bid)[0])
a, b, bid = setup(no_chase=False)
check("FLAG OFF: the same 2-tick chase is re-priced (behaviour unchanged)", decide(b, bid)[0])
check("flag off: 1 tick kept, 3 ticks re-priced (as before)", not decide(b, bid, ticks=1)[0] and decide(b, bid, ticks=3)[0])

print("--- Bot.plan_change: expire-as-cancel")
a, b, bid = setup(no_chase=False)
near = bid.expires - M.timedelta(seconds=60)
check("flag off: an order 60 s from expiry is refreshed (cancel + new)", decide(b, bid, ticks=0, now=near)[0])
b.cfg.ttl_expire_as_cancel = True
check("ttl_expire_as_cancel: it is left to expire (no cancel)", not decide(b, bid, ticks=0, now=near)[0])
check("...but a real reprice (3 ticks) near expiry still goes", decide(b, bid, ticks=3, now=near)[0])
q = Quote(bid.price, int(bid.qty), None, 0, bid.price, None)
ex21 = b.ex["21"]
ch = b.plan_change(ex21, q, [], 0.5, bid.expires + M.timedelta(seconds=1), time.monotonic())
check("1 s after it expired: that side is NOT re-quoted yet (grace: clock skew)", ch is None or not ch.new, ch and ch.new)
ch = b.plan_change(ex21, q, [], 0.5, bid.expires + M.timedelta(seconds=6), time.monotonic())
check("...after ttl_expire_grace_seconds it is", ch is not None and len(ch.new) == 1, ch and ch.new)
b.last_expiry[("21", True)] = bid.expires
ch = b.plan_change(ex21, q, [], 0.5, bid.expires - M.timedelta(seconds=600), time.monotonic())
check("an order gone BEFORE its expiry (filled / cancelled) is re-quoted at once",
      ch is not None and len(ch.new) == 1 and ("21", True) not in b.last_expiry)

print("--- strategy_sim.plan_changes (the simulators' re-quote rule)")
BASE_CFG = dict(churn_control=False)


def sim_cfg(**kw):
    return S.make_cfg({**BASE_CFG, **kw})


def sim_mkt(kind="quiet", headline=False, inv=0.0, fv=0.50):
    return SimpleNamespace(state={"fv": fv}, inv=inv, orders=[], kind=kind, headline=headline, p0=0.5)


def sim_place(cfg, m, t, want):
    """Plan with nothing resting, then 'land' the place as Order at t (as Sim.land)."""
    cancels, places = S.plan_changes(cfg, [], want, t, m)
    for is_bid, px, q, lvl in places:
        m.orders.append(S.Order("us", is_bid, px, q, t, m.state["fv"], lvl))
    return places


def sim_reprices(cfg, m, t, want):
    mine = [o for o in m.orders if o.owner == "us"]
    cancels, places = S.plan_changes(cfg, mine, want, t, m)
    return bool(cancels), cancels, places


for flag in (False, True):
    cfg = sim_cfg(no_chase_enabled=flag)
    m = sim_mkt()
    sim_place(cfg, m, 0, [(True, 0.45, 100, 0, 0.45)])
    r2 = sim_reprices(cfg, m, 10, [(True, 0.46, 100, 0, 0.46)])[0]
    check(f"sim, no-chase {'ON' if flag else 'off'}: a 2-tick chase is {'kept' if flag else 're-priced'}", r2 != flag)
cfg = sim_cfg(no_chase_enabled=True)
m = sim_mkt()
sim_place(cfg, m, 0, [(True, 0.45, 100, 0, 0.45)])
m.state["fv"] = 0.51
check("sim no-chase: fv move -> re-priced", sim_reprices(cfg, m, 10, [(True, 0.46, 100, 0, 0.46)])[0])
m.state["fv"], m.inv = 0.50, 30
check("sim no-chase: inventory change -> re-priced", sim_reprices(cfg, m, 10, [(True, 0.46, 100, 0, 0.46)])[0])
m.inv = 0
m.orders[0].qty = 90
check("sim no-chase: partial fill -> re-priced", sim_reprices(cfg, m, 10, [(True, 0.46, 100, 0, 0.46)])[0])
m.orders[0].qty = 100
check("sim no-chase: unsafe (beyond the limit) -> re-priced", sim_reprices(cfg, m, 10, [(True, 0.44, 100, 0, 0.44)])[0])
check("sim no-chase: unchanged otherwise -> kept", not sim_reprices(cfg, m, 10, [(True, 0.46, 100, 0, 0.46)])[0])

for eac in (False, True):
    cfg = sim_cfg(ttl_expire_as_cancel=eac)
    m = sim_mkt()
    sim_place(cfg, m, 0, [(True, 0.45, 100, 0, 0.45)])
    w = [(True, 0.45, 100, 0, 0.45)]
    early = sim_reprices(cfg, m, 1600, w)[0]
    late, cancels, places = sim_reprices(cfg, m, 1650, w)
    if not eac:
        check("sim TTL (saver off): kept at 1600 s, refreshed (cancel + new) at 1650 s (< 180 s left of 1800)",
              not early and late and len(places) == 1)
    else:
        check("sim ttl_expire_as_cancel: no refresh before expiry", not early and not late)
        r, cancels, places = sim_reprices(cfg, m, 1801, w)
        check("...at 1801 s it has expired (gone from the book) and the side waits for the grace",
              not r and not places and not m.orders)
        r, cancels, places = sim_reprices(cfg, m, 1806, w)
        check("...re-quoted after ttl_expire_grace_seconds", not r and len(places) == 1)
cfg = sim_cfg()
m = sim_mkt()
sim_place(cfg, m, 0, [(True, 0.45, 100, 0, 0.45)])
r, cancels, places = sim_reprices(cfg, m, 1900, [(True, 0.45, 100, 0, 0.45)])
check("sim: an order whose refresh never went out expires at order_ttl (leaves the book), the side is re-quoted",
      not m.orders and not cancels and len(places) == 1)
cfg = sim_cfg(ttl_tiers_enabled=True)
for kind, head, lo, hi in (("quiet", False, 4800, 7200), ("busy", False, 2880, 4320), ("headline", True, 1440, 2160)):
    m = sim_mkt(kind, head)
    exps = []
    for k in range(30):
        S.plan_changes(cfg, [], [(True, 0.45, 100, 0, 0.45)], 100 * k, m)
        exps.append(m.state["pend"][(True, 0)][2] - 100 * k)
    check(f"sim tiers: {kind} orders get {lo}-{hi} s (jittered, <= 2 h)",
          all(lo - 1e-6 <= x <= hi + 1e-6 for x in exps) and max(exps) - min(exps) > 0.1 * lo, (min(exps), max(exps)))

print("--- parity: mm_bot.Bot.plan_change and strategy_sim.plan_changes take the same keep / reprice decision")
mismatch, n, seen = [], 0, set()
for flag in (False, True):
    a, b, bid = setup(no_chase=flag)
    meta = b.order_meta[bid.order_id]
    cfg = sim_cfg(no_chase_enabled=flag)
    for ticks in (-3, -2, -1, 1, 2, 3):
        for dfv in (0.0, 0.002, 0.006):
            for dinv in (0, 25):
                for filled in (0, 10):
                    for lim_ticks in (None, -4):
                        n += 1
                        b.ex["21"].inv = meta["inv"] if flag else b.ex["21"].inv
                        inv0 = b.ex["21"].inv
                        b.ex["21"].inv = inv0 + dinv
                        q0 = bid.qty
                        bid.qty = q0 - filled
                        bot_says = decide(b, bid, ticks=ticks, fv=meta["fv"] + dfv, limit_ticks=lim_ticks)[0]
                        bid.qty = q0
                        b.ex["21"].inv = inv0
                        m = sim_mkt(inv=inv0, fv=meta["fv"])
                        sim_place(cfg, m, 0, [(True, bid.price, int(q0), 0, bid.price)])
                        m.orders[0].qty -= filled
                        m.inv, m.state["fv"] = inv0 + dinv, meta["fv"] + dfv
                        tgt = M.rnd(bid.price + ticks * TICK)
                        lim = M.rnd(bid.price + (lim_ticks if lim_ticks is not None else max(ticks, 0)) * TICK)
                        sim_says = sim_reprices(cfg, m, 30, [(True, tgt, int(q0), 0, lim)])[0]
                        seen.add((flag, ticks, bot_says))
                        if bot_says != sim_says:
                            mismatch.append((flag, ticks, dfv, dinv, filled, lim_ticks, bot_says, sim_says))
check(f"same decision on all {n} cases (no-chase off and on: ticks x fv move x inventory x fill x unsafe)",
      not mismatch, mismatch[:5])
check("...the grid has both outcomes: a 2-tick chase kept only with no-chase on, re-priced in some on-cases too",
      (True, 2, False) in seen and (False, 2, False) not in seen and (True, 2, True) in seen and (True, 1, False) in seen)
mm_exp, sim_exp = [], []
for eac in (False, True):
    for left in (600, 170, 60):
        a, b, bid = setup(no_chase=False)
        b.cfg.ttl_expire_as_cancel = eac
        mm_exp.append(decide(b, bid, ticks=0, now=bid.expires - M.timedelta(seconds=left))[0])
        cfg = sim_cfg(ttl_expire_as_cancel=eac)
        m = sim_mkt()
        sim_place(cfg, m, 0, [(True, 0.45, 100, 0, 0.45)])
        sim_exp.append(sim_reprices(cfg, m, 1800 - left, [(True, 0.45, 100, 0, 0.45)])[0])
check("same expiry decision (refresh vs keep, expire-as-cancel off/on, 600/170/60 s left)", mm_exp == sim_exp,
      (mm_exp, sim_exp))

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
