"""
Offline tests for Package 5 C, the hold target (hold_target_hours): lots older than T -> the reducing side joins the
best other price (never > 1c through the book's price, never at/through our own other side); older than 2T -> taken
at the best other price inside a per-order, per-minute and rolling-hour budget that starts fully used (restart-safe);
the headline staging gate; flag off = untouched; the live_sim mirror runs the same plan. Fake exchange, no network.

Run:  python tests/test_hold_target.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot                           # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
KEYS = ["hold_target_hours", "hold_unload_budget_frac", "hold_take_max_per_min", "hold_target_headline"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: off (0 h), 2% of the account per hour, 2 takes a minute, headline gate closed",
      tuple(getattr(c, k, None) for k in KEYS) == (0.0, 0.02, 2, False))
good, bad = M.validate_overrides({"hold_target_hours": 4.0, "hold_unload_budget_frac": 0.03,
                                  "hold_take_max_per_min": 1, "hold_target_headline": True}, c)
check("all four live-overridable", len(good) == 4 and not bad, bad)
_, bad = M.validate_overrides({"hold_target_hours": 100.0, "hold_unload_budget_frac": 0.5,
                               "hold_take_max_per_min": 1.5}, c)
check("ranges / types checked (100 h, 50%, 1.5 refused)", len(bad) == 3, bad)
_ov, _f = list(M.OVERRIDABLE), list(M.Config.__dataclass_fields__)
_a, _b = _ov.index(KEYS[0]), _f.index(KEYS[0])
check("one contiguous block in OVERRIDABLE and in Config", _ov[_a:_a + 4] == KEYS and _f[_b:_b + 4] == KEYS)


def bot(**kw):
    api, b = make_bot()
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    b.cycle()
    return api, b


_T0 = time.time()                                      # frozen wall clock: ages never drift between two decide calls
_real_time = time.time
time.time = lambda: _T0                                # (the process ends with the test)


def age(b, eid, pos, hours):
    b.lots[eid] = [[float(pos), _T0 - hours * 3600]]


def dec(b, pos, fv, book, book_fv, eid="21", now_m=None):
    ex = b.ex[eid]
    ex.book = book
    ex.last_fv, ex.cooldown_until = None, -1e9          # (no jump guard between scripted cases)
    return b.decide(ex, fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0,
                    time.monotonic() if now_m is None else now_m, book_fv=book_fv)


BOOK = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}
print("--- quote half (long 500 in Utah Rep; Polymarket-leaned fv 0.62 over the book's 0.52)")
api, b = bot()
age(b, "21", 500, 5.0)
off = dec(b, 500, 0.62, BOOK, 0.52)
check("flag off: the ask sits above the best other ask 0.56 (the setup)", off.ask is not None and off.ask > 0.56, off)
b.cfg.hold_target_hours = 6.0
young = dec(b, 500, 0.62, BOOK, 0.52)
check("on, age 5 h < T 6 h: identical to off", young == off and young.ask_limit == off.ask_limit, (young, off))
b.cfg.hold_target_hours = 4.0
on = dec(b, 500, 0.62, BOOK, 0.52)
check("on, age 5 h >= T 4 h: the reducing ask joins the best other ask 0.56", on.ask == 0.56, on)
check("...the keep limit follows (<= 0.56)", on.ask_limit is None or on.ask_limit <= 0.56, on)
check("...size unchanged, adding bid unchanged (it is below 0.56)", on.ask_size == off.ask_size
      and (on.bid, on.bid_size) == (off.bid, off.bid_size), (on, off))
thin = {"bids": [lvl(0.40, 1000)], "asks": [lvl(0.45, 1000)]}
fl = dec(b, 500, 0.62, thin, 0.50)
check("best other ask 0.45 is 5c under the book's 0.50: floor at book - 1c = 0.49", fl.ask == 0.49, fl)
fl2 = dec(b, 500, 0.62, thin, 0.503)
check("...floor rounds up onto the grid (0.493 -> 0.495), never below book - 1c", fl2.ask == 0.495, fl2)
nofv = dec(b, 500, 0.62, thin, None)
check("...no book price: the floor uses fv (0.61 here), so the ask is never through it",
      nofv.ask is not None and nofv.ask >= 0.61 - 1e-9, nofv)
short_book = {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}
age(b, "21", -500, 5.0)
s_off = replace(b.cfg)
b.cfg.hold_target_hours = 0.0
so = dec(b, -500, 0.42, short_book, 0.52)
b.cfg.hold_target_hours = 4.0
sn = dec(b, -500, 0.42, short_book, 0.52)
check("short mirror: flag off the bid sits under 0.48; on it joins the best other bid 0.48",
      so.bid is not None and so.bid < 0.48 and sn.bid == 0.48, (so, sn))
sthin = {"bids": [lvl(0.55, 1000)], "asks": [lvl(0.60, 1000)]}
sf = dec(b, -500, 0.42, sthin, 0.50)
check("short floor: best other bid 0.55 is 5c over the book's 0.50 -> bid 0.51 (book + 1c)", sf.bid == 0.51, sf)

print("--- quote half: never our own bid >= our own ask (hold_quote on every grid case)")
age(b, "21", 500, 5.0)
bad, n = [], 0
for qb in (None, 0.40, 0.47, 0.50, 0.53, 0.555):
    for qa in (0.50, 0.53, 0.56, 0.60, 0.70):
        if qb is not None and qb >= qa:
            continue
        for bb, ba in ((0.48, 0.50), (0.45, 0.46), (0.30, 0.35), (None, 0.50), (0.52, 0.53)):
            for bfv in (0.40, 0.50, 0.55, 0.65):
                for inv in (500, -500):
                    b.ex["21"].inv, b.ex["21"].age = float(inv), 5.0
                    q0 = M.Quote(bid=qb, bid_size=100 if qb else 0, ask=qa, ask_size=100, bid_limit=qb, ask_limit=qa)
                    if inv < 0:
                        q0 = M.Quote(bid=1 - qa, bid_size=100, ask=None if qb is None else 1 - qb,
                                     ask_size=100 if qb else 0, bid_limit=1 - qa,
                                     ask_limit=None if qb is None else 1 - qb)
                    q = b.hold_quote(b.ex["21"], q0, bb, ba, bfv, b.cfg)
                    n += 1
                    if q.bid is not None and q.ask is not None and q.bid >= q.ask - 1e-9:
                        bad.append(("cross", q0, bb, ba, bfv, q))
                    if (q.bid_limit is not None and q.ask_limit is not None and q.bid_limit >= q.ask_limit - 1e-9):
                        bad.append(("limits cross", q0, bb, ba, bfv, q))
                    if inv > 0 and q.ask is not None and q.ask != q0.ask and (q.ask < round(bfv - 0.01, 3) - 1e-9 or q.ask > q0.ask + 1e-9
                                                          or (bb is not None and q.ask <= bb + 1e-9)):
                        bad.append(("long ask", q0, bb, ba, bfv, q))
                    if inv < 0 and q.bid is not None and q.bid != q0.bid and (q.bid > round(bfv + 0.01, 3) + 1e-9 or q.bid < q0.bid - 1e-9
                                                          or (ba is not None and q.bid >= ba - 1e-9)):
                        bad.append(("short bid", q0, bb, ba, bfv, q))
check(f"{n} cases: own bid < own ask (wanted and keep limits), reducing side never > 1c through the book, never "
      f"moves out, never crosses the best other price", not bad, bad[:2])
q0 = M.Quote(bid=0.555, bid_size=100, ask=0.60, ask_size=100, bid_limit=0.555, ask_limit=0.60)
b.ex["21"].inv = 500.0
q = b.hold_quote(b.ex["21"], q0, 0.50, 0.56, 0.52, b.cfg)
check("adding bid 0.555 meets the new ask 0.56 -> capped a tick under (0.555 stays; at 0.56 it would go)",
      q.ask == 0.56 and q.bid == 0.555, q)
q = b.hold_quote(b.ex["21"], replace(q0, ask=0.60), 0.50, 0.555, 0.52, b.cfg)
check("...best ask 0.555 -> our ask 0.555, adding bid pulled back to 0.55", q.ask == 0.555 and q.bid == 0.55
      and q.bid_limit == 0.55, q)
blocked = b.hold_quote(b.ex["21"], replace(q0, ask=None, ask_size=0), 0.50, 0.555, 0.52, b.cfg)
check("a reducing side the decision left out stays out", blocked.ask is None and blocked.bid == 0.555, blocked)

print("--- headline gate and ref-only")
api, b = bot(hold_target_hours=4.0, headline_races=("Utah Senate",))
age(b, "21", 500, 5.0)
h = dec(b, 500, 0.62, BOOK, 0.52)
check("headline market, gate closed: no join", h.ask > 0.56, h)
b.cfg.hold_target_headline = True
check("...gate open: joins 0.56", dec(b, 500, 0.62, BOOK, 0.52).ask == 0.56)
b.cfg.headline_races = ()
b.ref_only.add("21")
check("ref-only market: no join", not b.hold_gate(b.ex["21"], b.cfg))
b.ref_only.discard("21")

print("--- take half: the plan")
NOW = time.monotonic() + 1e5


def tbot(books=None, **kw):
    api, b = make_bot(books=books)
    for k, v in dict(hold_target_hours=2.0, **kw).items():
        setattr(b.cfg, k, v)
    b.cycle()
    b.hold_open_at = NOW - 1.0
    return api, b


def plan(b, inv, bfv, now=NOW):
    return [(p["eid"], p["buy"], p["qty"], p["price"]) for p in b.hold_take_plan(inv, bfv, now)]


BANK = 100_000.0
api, b = tbot()
age(b, "21", 5000, 3.9)
inv, BFV = {"21": 5000.0}, {"21": 0.50}
check("age 3.9 h < 2T 4 h: no take", plan(b, inv, BFV) == [])
age(b, "21", 5000, 4.1)
p = plan(b, inv, BFV)
check("age 4.1 h >= 2T: sell to the best other bid 0.48 (book 0.50, 2c through? no: 0.48 < 0.49 floor)", p == [], p)
BFV = {"21": 0.485}
p = plan(b, inv, BFV)
cap = int(min(1000, 0.01 * BANK / 0.48))
check(f"book price 0.485 (floor 0.475): sell at 0.48, qty = min(best level 1,000, 1% of the account / 0.48 = {cap})",
      p == [("21", False, cap, 0.48)], p)
check("the 2% hourly budget: 2,000 notional", abs(sum(x["notional"] for x in b.hold_take_plan(inv, BFV, NOW))
                                                 - cap * 0.48) < 1e-6)
age(b, "21", 300, 4.1)
check("...a smaller position: qty = the position (never flips)", plan(b, {"21": 300.0}, BFV) == [("21", False, 300, 0.48)])
age(b, "22", -5000, 4.1)
p = plan(b, {"22": -5000.0}, {"22": 0.515})
check("short: buy back at the best other ask 0.52 (book 0.515, ceiling 0.525); notional per share 0.48",
      p == [("22", True, int(min(1000, 1000 / 0.48)), 0.52)], p)
check("...ask 0.52 is 2c over a book of 0.50: no take", plan(b, {"22": -5000.0}, {"22": 0.50}) == [])
check("no book price: no take", plan(b, {"22": -5000.0}, {"22": None}) == [])

print("--- take half: caps (per minute, rolling hour, restart)")
books = {e: {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.52, 1000)]} for e in ("11", "12", "21", "22")}
api, b = tbot(books=books, hold_unload_budget_frac=0.05)
inv = {e: 3000.0 for e in books}
for k, e in enumerate(books):
    age(b, e, 3000, 5.0 + k)
BF = {e: 0.485 for e in books}
p = plan(b, inv, BF)
check("4 aged markets, 2 takes a minute: the 2 oldest", [x[0] for x in p] == ["22", "21"], p)
b.cfg.hold_take_max_per_min = 0
check("hold_take_max_per_min 0: nothing", plan(b, inv, BF) == [])
b.cfg.hold_take_max_per_min = 2
done = []
got = b.take_aged(inv, BF, {}, NOW, execute=lambda e, buy, q, px: done.append((e, buy, q, px)) or q)
check("take_aged executes exactly the plan", done == p and got == {"22", "21"}, (done, p))
check("...and the same minute allows no more", plan(b, inv, BF, NOW + 30) == [])
p2 = plan(b, inv, BF, NOW + 61)
check("a minute later: 2 more, the next oldest (1,000 shares each = the best level, 480 notional)",
      [(x[0], x[2]) for x in p2] == [("22", 1000), ("21", 1000)], p2)
for k in range(1, 30):                                   # a call a minute: 960 notional each until 5,000 is used
    b.take_aged(inv, BF, {}, NOW + 61 * k, execute=lambda *a: a[2])
used = sum(n for t, n in b.hold_takes if NOW + 61 * 29 - t < 3600)
check("rolling hour: notional taken reaches but never exceeds 5% of the account (5,000)",
      0.05 * BANK - 0.48 < used <= 0.05 * BANK + 1e-6, used)
check("...exhausted: no take within the hour", plan(b, inv, BF, NOW + 3000) == [])
check("...an hour after the first takes, budget frees again", len(plan(b, inv, BF, NOW + 3601)) >= 1)
# many calls: never above budget in any rolling hour
api, b = tbot(books=books, hold_unload_budget_frac=0.02)
for e in books:
    age(b, e, 100000, 9.0)
inv = {e: 100000.0 for e in books}
log_ = []
for s in range(0, 4 * 3600, 7):
    b.take_aged(inv, BF, {}, NOW + s, execute=lambda *a: a[2])
log_ = list(b.hold_takes)
worst = max(sum(n for t, n in log_ if t0 <= t < t0 + 3600) for t0, _ in log_)
mins = max(sum(1 for t, _ in log_ if t0 <= t < t0 + 60) for t0, _ in log_)
check(f"4 h of calls every 7 s: worst rolling hour {worst:.0f} <= 2,000; at most 2 takes in any minute ({mins})",
      worst <= 2000 + 1e-6 and mins <= 2, (worst, mins))
api, b = make_bot(books=books)
b.cycle()                                                # (started with the flag off; on from T0)
b.cfg.hold_target_hours = 2.0
for e in books:
    age(b, e, 3000, 9.0)
done = []
T0 = NOW
b.take_aged({e: 3000.0 for e in books}, BF, {}, T0, execute=lambda *a: done.append(a) or a[2])
b.take_aged({e: 3000.0 for e in books}, BF, {}, T0 + 3599, execute=lambda *a: done.append(a) or a[2])
check("restart (fresh bot, lots aged 9 h): the budget starts fully used - no take in the first hour", done == []
      and b.hold_open_at == T0 + 3600, b.hold_open_at)
b.take_aged({e: 3000.0 for e in books}, BF, {}, T0 + 3600, execute=lambda *a: done.append(a) or a[2])
check("...from the hour on, the normal caps (2 takes)", len(done) == 2, done)
b.cfg.hold_target_hours = 0.0
b.take_aged({e: 3000.0 for e in books}, BF, {}, T0 + 3700, execute=lambda *a: done.append(a) or a[2])
check("turning it off clears hold_open_at (takes still remembered)", b.hold_open_at is None and len(b.hold_takes) == 2,
      b.hold_open_at)
b.cfg.hold_target_hours = 2.0
n0 = len(done)
b.take_aged({e: 3000.0 for e in books}, BF, {}, T0 + 3800, execute=lambda *a: done.append(a) or a[2])
check("...back on: the 1-hour hold-off restarts (no take, opens at +1 h)", len(done) == n0
      and b.hold_open_at == T0 + 3800 + 3600, b.hold_open_at)
api, b = make_bot(books=books)                           # red team: execute -> None (refused for writes)
b.cycle()
b.cfg.hold_target_hours = 2.0
for e in books:
    age(b, e, 3000, 9.0)
b.hold_open_at = 0.0
refused = []
got = b.take_aged({e: 3000.0 for e in books}, BF, {}, NOW, execute=lambda *a: refused.append(a) or None)
check("execute returns None: not counted in hold_takes / hold_takes_total, not in the returned set, stops",
      got == set() and not b.hold_takes and b.hold_takes_total == 0 and len(refused) == 1,
      (got, list(b.hold_takes), b.hold_takes_total, len(refused)))
got = b.take_aged({e: 3000.0 for e in books}, BF, {}, NOW + 1, execute=lambda *a: a[2])
check("...the next call (writes back) takes and counts as usual", len(got) == 2 and len(b.hold_takes) == 2
      and b.hold_takes_total == 2, (got, list(b.hold_takes)))
api, b = make_bot(books=books)
b.cycle()
b.cfg.hold_target_hours = 2.0
b.hold_open_at = 123.0
b.cfg.hold_target_hours = 0.0
b.cycle()
check("a cycle with the flag off clears hold_open_at (take_aged not called)", b.hold_open_at is None, b.hold_open_at)

print("--- red team: no take / join during the jump guard, a Polymarket jump pause, or against Polymarket")
api, b = tbot()
age(b, "21", 5000, 4.1)
inv, BFV = {"21": 5000.0}, {"21": 0.485}
check("(setup) aged long: a take is planned", plan(b, inv, BFV) != [])
b.ex["21"].cooldown_until = NOW + 10
check("jump cooldown running: no take", plan(b, inv, BFV) == [])
b.ex["21"].cooldown_until = 0.0
b.ex["21"].ref_jump_at = NOW - 60                       # within reduce_from_book_pause_s (120 s)
check("60 s after a Polymarket jump: no take", plan(b, inv, BFV) == [])
check("...past the pause: taken again", plan(b, inv, BFV, NOW + 61) != [])
b.ex["21"].ref_jump_at = -1e9
b.cur_refs = {"21": 0.485 + b.cfg.ref_guard_gap + 0.01}
check("Polymarket > book + ref_guard_gap: selling the long is the wrong side, no take", plan(b, inv, BFV) == [])
b.cur_refs = {"21": 0.485 - 0.10}
check("...Polymarket below the book: the sell is fine", plan(b, inv, BFV) != [])
age(b, "22", -5000, 4.1)
b.cur_refs = {"22": 0.515 - b.cfg.ref_guard_gap - 0.01}
check("short: Polymarket < book - gap: buying back is the wrong side, no take",
      plan(b, {"22": -5000.0}, {"22": 0.515}) == [])
b.cur_refs = {}
api, b = bot(hold_target_hours=4.0)
age(b, "21", 500, 5.0)
b.ex["21"].inv, b.ex["21"].age = 500.0, 5.0
q0 = M.Quote(bid=0.45, bid_size=100, ask=0.60, ask_size=100, bid_limit=0.45, ask_limit=0.60)
T = time.monotonic()
check("(setup) hold_quote joins 0.56", b.hold_quote(b.ex["21"], q0, 0.48, 0.56, 0.52, b.cfg, None, T).ask == 0.56)
b.ex["21"].cooldown_until = T + 10
check("jump cooldown: no join", b.hold_quote(b.ex["21"], q0, 0.48, 0.56, 0.52, b.cfg, None, T) == q0)
b.ex["21"].cooldown_until, b.ex["21"].ref_jump_at = 0.0, T - 30
check("30 s after a Polymarket jump: no join", b.hold_quote(b.ex["21"], q0, 0.48, 0.56, 0.52, b.cfg, None, T) == q0)
b.ex["21"].ref_jump_at = -1e9
check("Polymarket 0.52 + gap + 1c over the book: no join",
      b.hold_quote(b.ex["21"], q0, 0.48, 0.56, 0.52, b.cfg, 0.52 + b.cfg.ref_guard_gap + 0.01, T) == q0)
b.ex["21"].inv = -500.0
s0 = M.Quote(bid=0.40, bid_size=100, ask=0.60, ask_size=100, bid_limit=0.40, ask_limit=0.60)
check("short: Polymarket under the book by > gap: no join",
      b.hold_quote(b.ex["21"], s0, 0.48, 0.56, 0.52, b.cfg, 0.52 - b.cfg.ref_guard_gap - 0.01, T) == s0)

print("--- red team: the joined reducing side never exceeds the position")
b.ex["21"].inv = 30.0
q = b.hold_quote(b.ex["21"], replace(q0, ask_size=100, ask_max=150), 0.48, 0.56, 0.52, b.cfg, None, T)
check("long 30, ask 100: joined ask sized 30 (never flips)", q.ask == 0.56 and q.ask_size == 30
      and (q.ask_max is None or q.ask_max <= 30), q)
b.ex["21"].inv = -30.0
q = b.hold_quote(b.ex["21"], replace(s0, bid_size=100, bid_max=150), 0.48, 0.56, 0.52, b.cfg, None, T)
check("short 30, bid 100: joined bid sized 30", q.bid == 0.48 and q.bid_size == 30
      and (q.bid_max is None or q.bid_max <= 30), q)

print("--- take half: headline gate")
api, b = tbot(books=books, headline_races=("Utah Senate",))
age(b, "21", 3000, 9.0)
check("headline market, gate closed: no take", plan(b, {"21": 3000.0}, {"21": 0.485}) == [])
b.cfg.hold_target_headline = True
check("...gate open: taken", plan(b, {"21": 3000.0}, {"21": 0.485}) != [])

print("--- take half on the fake exchange (live): IOC, our quotes pulled first, write budget")
api, b = make_bot(books=books)
b.cfg.hold_target_hours = 2.0
api.inv = {"21": 3000.0}
b.cycle()
age(b, "21", 3000, 9.0)
b.hold_open_at = 0.0
api.calls.clear()
before = api.inv.get("21")
got = b.take_aged({"21": 3000.0}, {"21": 0.485}, {}, time.monotonic())
kinds = [c[0] for c in api.calls]
check("sold at 0.48 (one take) and the leftover cancelled", got == {"21"} and api.inv["21"] < before
      and "cancel_all" in kinds, (got, api.inv, kinds))
check("...our quotes pulled before the take, leftover cancelled after", "batch" in kinds
      and kinds.index("cancel_all") < kinds.index("batch") and "cancel_all" in kinds[kinds.index("batch"):], kinds)
api2, b2 = make_bot(books=books)
b2.cfg.hold_target_hours = 2.0
b2.cycle()
age(b2, "21", 3000, 9.0)
b2.hold_open_at = 0.0
api2.writes_left = lambda: 2
got = b2.take_aged({"21": 3000.0}, {"21": 0.485}, {}, time.monotonic())
check("write budget short (2 < 3): no take, nothing counted", got == set() and not b2.hold_takes)

print("--- flag off: untouched")
api, b = make_bot(books=books)
api.inv = {"21": 3000.0, "12": -2000.0}


def boom(*a, **k):
    raise AssertionError("called with the flag off")


b.hold_quote, b.take_aged, b.hold_take_plan = boom, boom, boom
ok = True
try:
    for _ in range(3):
        for e in ("21", "12"):
            age(b, e, api.inv[e], 20.0)
        b.cycle()
except AssertionError:
    ok = False
check("3 cycles, lots aged 20 h, flag off: hold_quote / take_aged / hold_take_plan never called", ok)
check("...and the plan is empty off even when called directly",
      M.Bot.hold_take_plan(make_bot()[1], {"21": 3000.0}, {"21": 0.485}, NOW) == [])

print("--- live_sim mirror = bot plan")
try:
    import live_sim as L
    import strategy_sim as S
except Exception as exc:                                  # pragma: no cover
    L = None
    print(f"SKIP mirror ({exc})")
if L is not None:
    class Stub(L.LiveSim):
        def __init__(self):
            pass

    def world():
        api, b = make_bot(live=False)
        b.cfg.hold_target_hours = 2.0
        b.cycle()
        st = Stub()
        st.wcap, st.wlog, st.writes = 1e9, [], 0        # (the mirror charges a take's writes: unlimited here)
        st.bot, st.cfg, st.takes = b, b.cfg, []
        st.mkts = []
        for k, e in enumerate(("11", "12", "21", "22")):
            m = S.Mkt("busy", 0.5, 0.0, 1, False, [])
            m.eid = e
            m.orders = [S.Order("h", True, 0.48, 700, 0), S.Order("h", True, 0.47, 900, 0),
                        S.Order("h", False, 0.50, 800, 0), S.Order("us", False, 0.55, 100, 0)]
            m.inv = 3000.0 if k % 2 == 0 else -3000.0
            st.mkts.append(m)
            age(b, e, m.inv, 5.0 + k)
        b.hold_open_at = 0.0
        return st, b

    st, b = world()
    inv = {m.eid: m.inv for m in st.mkts}
    bfv = {m.eid: L.fair_value(st.book_dict(m), st.cfg) for m in st.mkts}
    for m in st.mkts:                                    # the same inputs: the sim's books in bot.ex
        b.ex[m.eid].book = st.book_dict(m)
    want = plan(b, inv, bfv, 100)
    seen = []
    real_take = st.take

    def spy(m, t, is_buy, qty, limit):
        seen.append((m.eid, is_buy, qty, limit, any(o.owner == "us" for o in m.orders)))
        return real_take(m, t, is_buy, qty, limit)
    st.take = spy
    st.hold_take(100, inv)
    check("mirror executes Bot.hold_take_plan's takes exactly (same inputs -> same plan)",
          want and [s[:4] for s in seen] == want, (seen, want))
    check("...with our own quotes pulled first", not any(s[4] for s in seen))
    check("...counts hold_take_sh and moves the positions", st.hold_take_sh > 0
          and all(inv[m.eid] == m.inv for m in st.mkts), st.hold_take_sh)
    check("our_cycle calls the mirror only behind cfg.hold_target_hours > 0",
          "if cfg.hold_target_hours > 0 and t % 5 == 0:" in open(os.path.join(HERE, "live_sim.py")).read())
    check("metrics report hold_take_sh", "hold_take_sh=" in open(os.path.join(HERE, "live_sim.py")).read())

n_ok = sum(RESULTS)
print(f"{n_ok} of {len(RESULTS)} passed")
sys.exit(0 if n_ok == len(RESULTS) else 1)
