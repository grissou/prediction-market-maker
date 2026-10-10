"""
Offline tests for the Package 12 red team (analysis/p11/REDTEAM_P12.md): each finding's failing case, then fixed.
RT12-1 the rich-leg ladder kept resting ("soft") while the favourite's OWN Polymarket price is unknown / has moved
       under the ladder: it must be pulled (the ladder is priced from p alone).
RT12-2 pair_no_unwind_asks_le1: the allocator planned a B3 set unwind that arb_plan then refuses (bids sum > 1):
       the pair waited ALLOC_SET_WAIT and the allocator stalled (no new plan while a pair is pending).
RT12-3 a ladder the EXCHANGE refuses (not the cash gate) was re-sent every cycle: one batch write a race a cycle.
RT12-4 skew_target_inventory removed the inventory skew in reduce-only (global_reduce / flatten window).
RT12-5 (alloc_set_rich_leg with tilt_exit_take_split_sets warned): gone with the tilt exits, removed on simplify.

Run:  python tests/test_p12_redteam.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                       # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
M.util.alert = lambda msg: None
M.util.notify = lambda *a, **k: False


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


CLOSE = M.util.utcnow() + timedelta(days=30)
RACES = {"A": "Alpha Senate", "B": "Beta Senate"}


def bk(bid, ask, q=2000):
    return {"bids": [lvl(bid, q)] if bid else [], "asks": [lvl(ask, q)] if ask else []}


def books_default():
    return {"A1": bk(0.92, 0.93), "A2": bk(0.06, 0.08), "B1": bk(0.55, 0.60), "B2": bk(0.38, 0.42)}


def refs_default():
    return {"Alpha Senate|Republican": 0.98, "Alpha Senate|Democratic": 0.02,
            "Beta Senate|Republican": 0.70, "Beta Senate|Democratic": 0.30}


def mk_bot(inv=None, books=None, refs=None, cash=5000.0, **cfg):
    base = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
            "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
            "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
            "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    base.update({e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                 for e, v in (books or books_default()).items()})
    mk = []
    for k, race in RACES.items():
        mk += [market(f"m{k}1", f"{k}1", "Republican", race), market(f"m{k}2", f"{k}2", "Democratic", race)]
    api, b = make_bot(live=True, books=base, extra_markets=tuple(mk))
    b.t["endDate"] = M.iso(CLOSE)
    for x in b.ex.values():
        x.close = CLOSE
    r = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
         "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}
    r.update(refs_default() if refs is None else refs)
    b.refs = FakeRefs(r)
    c = b.cfg
    c.selftest_enabled = False
    c.reduce_no_as_sell = True
    c.arb_enabled = False
    c.cash_gate_enabled = True
    c.cash_gate_reserve = 0.0
    c.reserved_cash_mode = "ignore"
    c.alloc_enabled = True
    c.alloc_mm_reserve = 1000.0
    c.alloc_max_contract_usd = 100000.0
    c.alloc_min_edge_buy = 0.5
    c.max_position_frac = 1.0
    for k, v in cfg.items():
        setattr(c, k, v)
    api.inv.update({"A1": -1000, "A2": -1000} if inv is None else inv)
    api.cash = cash
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    return api, b


def quiet(fn, *a, **k):
    logging.disable(logging.CRITICAL)
    try:
        return fn(*a, **k)
    finally:
        logging.disable(logging.NOTSET)


def read(b, cash=None):
    b.cg_cash = b.api.cash if cash is None else cash
    b.cg_reserved, b.cg_spent = 0.0, 0.0
    b.cg_read_at = time.monotonic()


def warm(b):
    on = b.cfg.alloc_enabled
    b.cfg.alloc_enabled = False
    quiet(b.cycle)
    b.drain_writes(5)
    b.cfg.alloc_enabled = on
    quiet(b.cancel_everything)
    b.my_orders.clear()
    b.api.orders.clear()
    for x in b.ex.values():
        x.quote = None
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    read(b)


def tick(b, skip=()):
    time.sleep(0.002)
    for x in b.ex.values():
        x.inv = float(b.api.inv.get(x.eid, 0.0))
    return quiet(b.alloc_tick, M.util.utcnow(), dict(b.api.inv), b.orders_by_eid(M.util.utcnow()), None, set(skip))


def ladder(api, eid="A1"):
    return sorted(((o["side"], o["action"], o["priceLimit"], o["quantity"]) for o in api.orders.values()
                   if o["exchangeId"] == eid), key=lambda t: t[2])


# ============================================================================================ RT12-1
print("--- RT12-1: the ladder never rests on an unknown / moved favourite price")
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
check("setup: the ladder rests (3 levels on A1)", len(ladder(api)) == 3, ladder(api))
b.cur_liquid = set(b.cur_liquid) - {"A2"}
read(b)
tick(b)
check("another leg's p not liquid ('soft'): the ladder stays (as designed)", len(ladder(api)) == 3)
b.cur_liquid = set(b.cur_liquid) - {"A1"}
read(b)
tick(b)
check("the FAVOURITE's own p unknown (feed stale / illiquid): pulled", not ladder(api), ladder(api))

api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
tick(b)
b.cur_liquid = set(b.cur_liquid) - {"A2"}     # (soft: A2 has no p)
b.cur_refs = dict(b.cur_refs)
b.cur_refs["A2"] = None
b.cur_refs["A1"] = 0.80                        # the favourite collapsed on Polymarket: NO_fav now worth 0.20
read(b)
tick(b)
lad = ladder(api)
check("soft race but the favourite's p fell to 0.80: no ladder bid above p + margin (YES 0.92 / 0.90 / 0.88 pulled)",
      all(1 - x[2] <= 0.80 + b.cfg.value_sell_margin + 1e-9 for x in lad), lad)

# ============================================================================================ RT12-2
print("--- RT12-2: L3 gate and the allocator's B3 set unwind")
TILT = {**books_default(), "A1": bk(0.93, 0.95), "A2": bk(0.08, 0.10)}   # bids 1.01, asks 1.05 (cost 0.053 / $)
api, b = mk_bot(books=TILT, cash=0.0, alloc_set_cost_per_usd=0.1, pair_unwind_enabled=True,
                pair_no_unwind_max_cost=0.0, pair_no_unwind_asks_le1=True)
warm(b)
read(b, cash=0.0)
members = b.groups["Alpha Senate"]
check("setup: arb_plan's gate refuses this race even for an allocator registration (bids sum 1.01 > 1)",
      b.nono_unwind_gated(members, 1.05, 0.06))
pairs, info = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("the allocator does not plan a B3 unwind arb_plan will refuse (no 'set' sale)",
      not any(p_["sell"]["kind"] == "set" for p_ in pairs), [p_["sell"] for p_ in pairs])
check("...counted in blocked_by", (info.get("blocked_by") or {}).get("set_bids_gt_1", 0) >= 1, info.get("blocked_by"))
api, b = mk_bot(books=TILT, cash=0.0, alloc_set_cost_per_usd=0.1, pair_unwind_enabled=True,
                pair_no_unwind_max_cost=0.0, pair_no_unwind_asks_le1=False)
warm(b)
pairs, _ = quiet(b.alloc_plan, dict(api.inv), time.monotonic(), 0.0, set(), None)
check("flag off: the B3 unwind is planned as before", any(p_["sell"]["kind"] == "set" for p_ in pairs))

print("--- RT12-6: L3 gate with our own set ladder resting at the favourite's best bid")
api, b = mk_bot(books=TILT, alloc_set_rich_leg=True, pair_unwind_enabled=True, pair_no_unwind_max_cost=0.05,
                pair_no_unwind_asks_le1=True, pair_unwind_race_order=True)
warm(b)
tick(b)
check("setup: the ladder rests on A1 at the best bid 0.93 / 0.91 / 0.89", [round(1 - x[2], 3) for x in ladder(api)]
      == [0.93, 0.91, 0.89], ladder(api))
b.sync_orders(api.open_orders("T"), time.monotonic() + 1e6)
for x in b.ex.values():
    x.book = M.strip_own(api.full_book(x.eid), b.orders_by_eid(M.util.utcnow()).get(x.eid, []))
fv = {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}
r = quiet(b.arb_plan, b.groups["Alpha Senate"], dict(api.inv), fv, False)
check("other traders' bids sum 1.01 (> 1): no NO+NO unwind at a cost (asks 1.05) while the ladder sells the set",
      r is None, r)
api, b = mk_bot(books={**books_default(), "A1": bk(0.89, 0.90), "A2": bk(0.06, 0.08)}, alloc_set_rich_leg=True,
                pair_unwind_enabled=True, pair_no_unwind_max_cost=0.05, pair_no_unwind_asks_le1=True,
                pair_unwind_race_order=True)
warm(b)
tick(b)
b.sync_orders(api.open_orders("T"), time.monotonic() + 1e6)
for x in b.ex.values():
    x.book = M.strip_own(api.full_book(x.eid), b.orders_by_eid(M.util.utcnow()).get(x.eid, []))
fv = {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}
r = quiet(b.arb_plan, b.groups["Alpha Senate"], dict(api.inv), fv, False)
check("...a PROFITABLE unwind (asks 0.98) still fires with the ladder resting", r is not None and r[0] == "unwind", r)

# ============================================================================================ RT12-3
print("--- RT12-3: an exchange refusal is not re-sent every cycle")
api, b = mk_bot(alloc_set_rich_leg=True)
warm(b)
api.refuse_no_sell = "Insufficient available funds"
n0 = len(api.sent("batch"))
for _ in range(5):
    read(b)
    tick(b)
n = len(api.sent("batch")) - n0
check(f"5 cycles, the exchange refusing every level: at most 1 batch write ({n})", n <= 1, n)
api.refuse_no_sell = None
b.alloc_ladder_refused["Alpha Senate"] -= b.ALLOC_LADDER_REFUSED_WAIT + 1
read(b)
tick(b)
check("...tried again once the wait is over (and placed)", len(ladder(api)) == 3, ladder(api))

# ============================================================================================ RT12-4
print("--- RT12-4: skew_target_inventory keeps the inventory skew in reduce-only")
seen = []
orig = M.quoting.compute_quote


def spy(*a, **k):
    seen.append(k.get("skew_inv"))
    return orig(*a, **k)


api, b = mk_bot(inv={"12": 2500}, value_mode=True, skew_target_inventory=True, alloc_enabled=False)
quiet(b.cycle)
b.drain_writes(5)
ex = b.ex["12"]
inv = {"12": 2500.0}
eff = b.effective_inventory(inv)
b0, ex0, inv0, eff0 = b, ex, inv, eff
M.quoting.compute_quote = spy
try:
    fv = fv0 = M.fair_value(ex.book, b.cfg)
    quiet(b.decide, ex, fv, inv, eff, False, 0.0, time.monotonic(), ref=0.88, ref_liquid=True)
    normal = seen[-1] if seen else "none"
    quiet(b.decide, ex, fv, inv, eff, True, 0.0, time.monotonic(), ref=0.88, ref_liquid=True)
    red = seen[-1] if seen else "none"
finally:
    M.quoting.compute_quote = orig
check(f"normal: the +EV holding is the target (skew_inv {normal} ~ 0)", normal is not None and normal != "none"
      and abs(normal) < 1e-6, normal)
check(f"global_reduce (risk over the cap): the skew is from flat again (skew_inv None: {red})", red is None, red)

print("--- RT12-7: a target that is just the holding does not unbrake the adding side")


def cq(inv, fv, bb, ba, **extra):
    c = M.Config()
    return M.quoting.compute_quote(fv, inv, inv, bb, ba, c, bankroll=100000, **extra)


flat = cq(600, 0.60, 0.585, 0.615)
at_t = cq(600, 0.60, 0.585, 0.615, skew_inv=0.0)
fixed = cq(600, 0.60, 0.585, 0.615, skew_inv=0.0, skew_add_flat=True)
check(f"setup: at target the bid is less braked than flat ({at_t.bid} > {flat.bid})", at_t.bid > flat.bid + 1e-9,
      (at_t, flat))
check(f"skew_add_flat: the adding bid keeps the flat skew ({fixed.bid} = {flat.bid}), the reducing ask the target's "
      f"({fixed.ask} = {at_t.ask})", fixed.bid == flat.bid and fixed.ask == at_t.ask, (fixed, flat, at_t))
s_flat = cq(-600, 0.40, 0.385, 0.415)
s_fix = cq(-600, 0.40, 0.385, 0.415, skew_inv=0.0, skew_add_flat=True)
s_t = cq(-600, 0.40, 0.385, 0.415, skew_inv=0.0)
check("short: the adding ask keeps the flat skew, the reducing bid the target's", s_fix.ask == s_flat.ask
      and s_fix.bid == s_t.bid and s_t.ask != s_flat.ask, (s_fix, s_flat, s_t))
below = cq(200, 0.60, 0.585, 0.615, skew_inv=-2000.0)
check("an allocator target ABOVE the holding (skew_add_flat False) still lifts the bid", below.bid >= flat.bid,
      (below, flat))
seen2 = []


def spy2(*a, **k):
    seen2.append(k.get("skew_add_flat"))
    return orig(*a, **k)


M.quoting.compute_quote = spy2
try:
    quiet(b0.decide, ex0, fv0, inv0, eff0, False, 0.0, time.monotonic(), ref=0.88, ref_liquid=True)
finally:
    M.quoting.compute_quote = orig
check("decide: the holding fallback target (no allocator plan) quotes with skew_add_flat", seen2[-1:] == [True], seen2)

# ============================================================================================ RT12-5
print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
