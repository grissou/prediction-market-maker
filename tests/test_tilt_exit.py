"""
Offline tests for Package 8 item 3 "tilt exits": cut UNPAIRED tilt exposure faster without crossing. Live 3 Oct 19:56:
tilt_s 0.110 and growing, tilt_exposure +34.3k (sum pos x (Polymarket - c)), ~356 lost per point of tilt; capital 100%,
the capital ceiling's adding factor 0, reduce-only most of the time, the write budget deferring changes. Covers:
  (a) tilt_exit_priority: change_key puts a change touching a tilt exit (the side shrinking a position that ADDS to
      |tilt_exposure|) between the headline markets and the other ordinary ones; deferred changes: tilt exits go first.
      tilt_exit_full_size: that side's size is the whole position (still capped by the race-net clip / cash caps).
  (b) adding_factor_capital_on + capital_ceiling_adding_size_factor_resume: the resume factor below the threshold,
      hysteresis 0.01, status.json capital_ceiling_adding_factor.
  (c) ref_guard_tilted: the reference guard measures from tilted_ref_for(r) (House pair of tests/test_house_quote.py:
      the exits rest at s 0.11; a genuine 8c jump still trips it). ref_guard_exits: the guard never blocks the side
      shrinking this market's own position (capped at the position), adding sides still blocked.
  Flags off identical to the base code (git show 2ef6d12:mm_bot.py) on a grid.

Run:  python tests/test_tilt_exit.py      (exit code 0 = all passed)
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
from fakes import FakeApi, FakeRefs, lvl, make_bot, market      # noqa: E402
import mm_bot as M                                                # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS = []
M.alert = lambda msg: None
M.notify = lambda *a, **k: False
BASE_REF = "2ef6d12"
FLAGS = ("tilt_exit_priority", "tilt_exit_full_size", "adding_factor_capital_on",
         "capital_ceiling_adding_size_factor_resume", "ref_guard_tilted", "ref_guard_exits")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def quiet_cycle(b, n=1):
    logging.disable(logging.CRITICAL)
    try:
        for _ in range(n):
            b.cycle()
    finally:
        logging.disable(logging.NOTSET)


def pin_tilt(b, s):
    b.tilt.update = lambda samples, now_m: s          # pin the tilt estimate (1-4 markets < ref_tilt_min_markets)
    b.tilt_s = s
    b.cfg.ref_tilt_rampin_min = 0.0                   # the full tilt at once (ramp-in: test_tilt_rampin.py)


REFS = {"Ohio Senate|Republican": 0.12, "Ohio Senate|Democratic": 0.88,
        "Utah Senate|Republican": 0.55, "Utah Senate|Democratic": 0.45}


def plain_bot(inv, refs=None, **cfg):
    a, b = make_bot()
    a.inv.update(inv)
    b.refs = FakeRefs(dict(REFS if refs is None else refs))
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    return a, b


print("--- config: defaults off, live-overridable")
c = M.Config()
check("defaults: priority / full size / tilted guard / exits False, capital_on 0, resume 0.5",
      (c.tilt_exit_priority, c.tilt_exit_full_size, c.ref_guard_tilted, c.ref_guard_exits,
       c.adding_factor_capital_on, c.capital_ceiling_adding_size_factor_resume) == (False, False, False, False, 0.0, 0.5))
good, bad = M.validate_overrides({k: (True if isinstance(getattr(c, k), bool) else 0.5) for k in FLAGS}, c)
check("all six in OVERRIDABLE and accepted", len(good) == 6 and not bad, (good, bad))
good, bad = M.validate_overrides({"adding_factor_capital_on": 1.5, "capital_ceiling_adding_size_factor_resume": -0.1}, c)
check("out-of-range values refused", not good and len(bad) == 2, (good, bad))

print("--- (a) tilt_exit_side: the side shrinking a position whose contribution has the sign of tilt_exposure")
a, b = plain_bot({"11": 300, "21": 3000, "22": -3000})
quiet_cycle(b)
# 11: +300 x (0.12 - 0.5) = -114; 21: +3000 x 0.05 = +150; 22: -3000 x -0.05 = +150 -> total +186
check("tilt_exposure +186 (update_tilt, raw Polymarket, c = 1/2)", round(b.tilt_exposure) == 186, b.tilt_exposure)
sides = {e: b.tilt_exit_side(b.ex[e]) for e in ("11", "12", "21", "22")}
check("21 long at r 0.55 -> ask; 22 short at r 0.45 -> bid; 11 long at r 0.12 (against the total) -> None; 12 flat -> None",
      sides == {"11": None, "12": None, "21": "ask", "22": "bid"}, sides)
b.tilt_exposure = -500.0
sides = {e: b.tilt_exit_side(b.ex[e]) for e in ("11", "21", "22")}
check("total negative: only the negative contributor (11) is a tilt exit", sides == {"11": "ask", "21": None, "22": None},
      sides)
b.tilt_exposure = 0.0
check("total 0: a positive contribution counts (sign(pos) == sign(r - c))",
      b.tilt_exit_side(b.ex["21"]) == "ask" and b.tilt_exit_side(b.ex["11"]) is None)
b.ex["21"].ref = None
check("no Polymarket price -> None", b.tilt_exit_side(b.ex["21"]) is None)

print("--- (a) change_key: flag off = the old key on a grid; on = 0.5 slot only for the tilt exit side")
a, b = plain_bot({"11": 300, "21": 3000, "22": -3000})
quiet_cycle(b)


def old_key(bot, ex, pull, reprice):
    urgent = ex.eid in bot.ref_moved or bot.unload_urgent(ex, time.monotonic())
    return (0 if pull else 0.5 if urgent else 1, 0 if ex.group in bot.cfg.headline_races else 1,
            1 if reprice else 0, -bot.size_plan.get(ex.eid, 0))


same = True
for e in ("11", "12", "21", "22"):
    for pull in (False, True):
        for rep in (False, True):
            for sd in (None, (True, False), (False, True), (True, True), (False, False)):
                if b.change_key(b.ex[e], pull, rep, sides=sd) != old_key(b, b.ex[e], pull, rep):
                    same = False
check("flag off: change_key identical to the old formula (4 markets x pull x reprice x sides)", same)
b.cfg.tilt_exit_priority = True
k = {e: b.change_key(b.ex[e], False, True, sides=(True, True))[1] for e in ("11", "12", "21", "22")}
check("flag on, both sides change: 21 and 22 in the 0.5 slot, 11 / 12 stay 1", k == {"11": 1, "12": 1, "21": 0.5, "22": 0.5}, k)
k21 = [b.change_key(b.ex["21"], False, False, sides=sd)[1] for sd in ((True, False), (False, True), None)]
k22 = [b.change_key(b.ex["22"], False, False, sides=sd)[1] for sd in ((True, False), (False, True), None)]
check("only a change touching the exit side: 21 (ask) [1, 0.5, 1], 22 (bid) [0.5, 1, 1]",
      k21 == [1, 0.5, 1] and k22 == [0.5, 1, 1], (k21, k22))
b.cfg.headline_races = ("Utah Senate",)
check("a headline market keeps slot 0 (headline first, then tilt exits)",
      b.change_key(b.ex["21"], False, False, sides=(False, True))[1] == 0)
b.cfg.headline_races = M.Config().headline_races
kh = (0, 0, 1, 0)
kt = b.change_key(b.ex["21"], False, True, sides=(False, True))
ko = b.change_key(b.ex["11"], False, False, sides=(False, True))
check("send order: headline < tilt exit (even a reprice) < other ordinary (even a cheap new quote)",
      kh < tuple(kt[:3]) + (0,) and kt < ko, (kt, ko))
check("pulls still first (slot 0 of the tier, untouched)", b.change_key(b.ex["21"], True, sides=(False, True))[0] == 0)

print("--- (a) deferred by the write budget: the tilt exits go first")


def budget_run(priority):
    a, b = plain_bot({"11": 300, "21": 3000, "22": -3000}, tilt_exit_priority=priority)
    quiet_cycle(b)                                          # everything placed (unlimited budget)
    far = {"11": 0.40, "12": 0.60, "21": 0.80, "22": 0.20}     # every resting quote far off target (safe side):
    for o in a.orders.values():                             # each needs a reprice (a cancel each)
        o["priceLimit"] = far[o["exchangeId"]] if o["side"] == "yes" else round(1 - far[o["exchangeId"]], 3)
    before = {o_id: o["exchangeId"] for o_id, o in a.orders.items()}
    a.writes_left = lambda: 2                               # room for one change (a cancel + one batch)
    a.calls.clear()
    quiet_cycle(b)
    touched = []
    for call in a.calls:
        if call[0] == "cancel_all" and call[1] is not None:
            touched.append(call[1])
        elif call[0] == "cancel_order" and call[1] in before:
            touched.append(before[call[1]])
    return touched, b


t_off, _ = budget_run(False)
t_on, b_on = budget_run(True)
check("flag off: the first ordinary market (11) is repriced, the tilt exits wait", t_off[:1] == ["11"], t_off)
check("flag on: a tilt-exit market (21 or 22) is repriced first, 11 waits",
      t_on[:1] in (["21"], ["22"]) and "11" not in t_on, t_on)

print("--- (a) tilt_exit_full_size: the exit's size is the whole position, within the existing caps")


def sizes(inv, reduce=False, **kw):
    a, b = plain_bot(inv, **kw)
    if reduce:
        b.cfg.max_worst_case_frac, b.cfg.worst_case_backstop_frac = 0.0001, 0.0002
    quiet_cycle(b)
    return {e: (b.ex[e].quote.bid_size if b.ex[e].quote.bid is not None else 0,
                b.ex[e].quote.ask_size if b.ex[e].quote.ask is not None else 0) for e in ("11", "12", "21", "22")}, b


s_off, _ = sizes({"11": 300, "21": 3000, "22": -3000})
s_on, b = sizes({"11": 300, "21": 3000, "22": -3000}, tilt_exit_full_size=True)
cash = b.cfg.max_order_cash_frac * b.bankroll()
q21, q22 = b.ex["21"].quote, b.ex["22"].quote
check("off: exits at the planned 100 shares", s_off["21"][1] == 100 and s_off["22"][0] == 100, s_off)
check("on: 21's ask = min(3000 held, cash cap 1000 / (1 - ask))", s_on["21"][1] == int(min(3000, cash / (1 - q21.ask)))
      or abs(s_on["21"][1] - min(3000, cash / (1 - q21.ask))) < 1, (s_on["21"], q21.ask))
check("on: 22's bid = min(3000 short, cash cap 1000 / bid)", abs(s_on["22"][0] - min(3000, cash / q22.bid)) < 1,
      (s_on["22"], q22.bid))
check("on: the adding sides and 11 (not a tilt exit) unchanged",
      s_on["21"][0] == s_off["21"][0] and s_on["22"][1] == s_off["22"][1] and s_on["11"] == s_off["11"]
      and s_on["12"] == s_off["12"], (s_on, s_off))
s_big, b = sizes({"21": 600, "22": -400}, tilt_exit_full_size=True, max_order_cash_frac=0.05)
check("cash cap loose: the exit is exactly the position (21 ask 600, 22 bid 400)",
      s_big["21"][1] == 600 and s_big["22"][0] == 400, s_big)
s_ro, b = sizes({"21": 600, "22": 400}, reduce=True, tilt_exit_full_size=True, max_order_cash_frac=0.05,
                refs=dict(REFS, **{"Utah Senate|Democratic": 0.55, "Utah Senate|Republican": 0.55}))
check("reduce-only: 21's exit clipped to the race-netted 200 (600 - 400), not 600", b.ex["21"].eff == 200
      and s_ro["21"][1] == 200, (s_ro, b.ex["21"].eff))
a, b = plain_bot({"21": 3000}, tilt_exit_full_size=True, tilt_exit_priority=True)
quiet_cycle(b, 2)
asks = [o for o in a.ours("21") if o[0] == "ask"]
bids = [o for o in a.ours("21") if o[0] == "bid"]
check("never crossing: our ask above the best other bid, above our own bid",
      asks and asks[0][1] > a.books["21"]["bids"][0]["price"] and (not bids or bids[0][1] < asks[0][1]), a.ours("21"))
check("prices identical with and without full size (only the size changes)",
      b.ex["21"].quote.ask == sizes({"21": 3000})[1].ex["21"].quote.ask)

print("--- (b) adding_factor_capital_on: the resume factor below the threshold, hysteresis 0.01")
a, b = make_bot()
b.capital_over = True
cfg = b.cfg
cfg.capital_ceiling_adding_size_factor = 0.0
cfg.adding_factor_capital_on = 0.95
seq = [(0.99, False), (0.955, False), (0.949, True), (0.955, True), (0.9599, True), (0.96, False), (0.951, False),
       (None, False), (0.94, True), (None, True)]
got = []
for frac, _ in seq:
    b.update_adding_resume(frac, cfg)
    got.append(b.adding_resume)
check("on below 0.95, off at >= 0.96, unknown account keeps the state", got == [w for _, w in seq], got)
check("factor in force: resume 0.5 while on", b.ceiling_adding_factor(cfg) == 0.5)
b.update_adding_resume(0.99, cfg)
check("factor in force: configured 0 above", b.ceiling_adding_factor(cfg) == 0.0)
b.capital_over = False
b.update_adding_resume(0.5, cfg)
check("ceiling off: never resumes, factor 1", not b.adding_resume and b.ceiling_adding_factor(cfg) == 1.0)
b.capital_over = True
cfg.adding_factor_capital_on = 0.0
b.update_adding_resume(0.5, cfg)
check("threshold 0 = off: configured factor", not b.adding_resume and b.ceiling_adding_factor(cfg) == 0.0)
cfg.adding_factor_capital_on, cfg.capital_ceiling_adding_size_factor = 0.95, 0.7
b.update_adding_resume(0.5, cfg)
check("resume never lowers a larger configured factor (max(0.7, 0.5))", b.ceiling_adding_factor(cfg) == 0.7)


def ceiling_bot(thr):
    a, b = make_bot()
    a.inv.update({"21": 500})                    # 500 x 0.52 = 260 of 100k: 0.26%
    b.refs = FakeRefs(dict(REFS))
    b.cfg.capital_in_positions_max_frac = 0.001  # ceiling on
    b.cfg.capital_ceiling_adding_size_factor = 0.0
    b.cfg.adding_factor_capital_on = thr
    quiet_cycle(b)
    return a, b


a0, b0 = ceiling_bot(0.0)
a1, b1 = ceiling_bot(0.95)
a2, b2 = ceiling_bot(0.002)                      # 0.26% > 0.2%: above the threshold
check("ceiling on in all three", b0.capital_over and b1.capital_over and b2.capital_over)
check("off: the adding bid on the long (21) is not quoted (factor 0)", b0.ex["21"].quote.bid is None
      or b0.ex["21"].quote.bid_size == 0, b0.ex["21"].quote)
check("below the threshold: the adding bid at x0.5 (50 of 100) and the flat markets' sides at 50",
      b1.ex["21"].quote.bid_size == 50 and b1.ex["12"].quote.bid_size == 50 and b1.ex["12"].quote.ask_size == 50,
      (b1.ex["21"].quote, b1.ex["12"].quote))
check("above the threshold: the configured factor 0", b2.ex["21"].quote.bid is None or b2.ex["21"].quote.bid_size == 0)
check("the reducing ask unchanged by the knob (100)", b0.ex["21"].quote.ask_size == b1.ex["21"].quote.ask_size == 100)
for bb, want_f, want_r in ((b0, 0.0, False), (b1, 0.5, True), (b2, 0.0, False)):
    bb.write_status(ok=True)
st = [json.load(open(M.bot_path(bb.cfg.status_file))) for bb in (b0, b1, b2)]
check("status.json: capital_ceiling_adding_factor 0 / 0.5 / 0 and capital_ceiling_adding_resume",
      [s.get("capital_ceiling_adding_factor") for s in st] == [0.0, 0.5, 0.0]
      and [s.get("capital_ceiling_adding_resume") for s in st] == [False, True, False],
      [(s.get("capital_ceiling_adding_factor"), s.get("capital_ceiling_adding_resume")) for s in st])

print("--- (c) the House pair (tests/test_house_quote.py setup): ref_guard_tilted / ref_guard_exits")
DEM, REP = "31", "32"


def house_bot(dem_ref=0.925, rep_ref=0.075, reduce_only=True, s=0.11, inv=None, **cfg):
    extra = (market("5", DEM, "Democratic", "U.S. House"), market("6", REP, "Republican", "U.S. House"))
    books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
             "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
             "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]},
             DEM: {"bids": [lvl(0.86, 5000)], "asks": [lvl(0.88, 5000)]},
             REP: {"bids": [lvl(0.13, 5000)], "asks": [lvl(0.15, 5000)]}}
    a, b = make_bot(books=books, extra_markets=extra)
    b.cfg.size_by_activity = True
    a.inv.update({DEM: 9876, REP: -9396} if inv is None else inv)
    b.refs = FakeRefs({"U.S. House|Democratic": dem_ref, "U.S. House|Republican": rep_ref})
    if reduce_only:
        b.cfg.max_worst_case_frac, b.cfg.worst_case_backstop_frac = 0.001, 0.002
    b.cfg.ref_tilt_enabled = b.cfg.ref_tilt_headline = True          # live: T2.1 on, headline on
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    pin_tilt(b, s)
    quiet_cycle(b)
    return a, b


def sides_of(q):
    return [x for x, p in (("bid", q.bid), ("ask", q.ask)) if p is not None]


a, b = house_bot()
check("tilt on, flags off: the guard still blocks both House exits (raw r 0.925 vs book 0.861)",
      sides_of(b.ex[DEM].quote) == [] and sides_of(b.ex[REP].quote) == [], (b.ex[DEM].quote, b.ex[REP].quote))
rt = b.tilted_ref_for(b.ex[DEM], 0.925)
check("tilted reference r' = 0.5 + 0.89 x 0.425 = 0.878", abs(rt - 0.87825) < 1e-9, rt)
a, b = house_bot(ref_guard_tilted=True)
dq, rq = b.ex[DEM].quote, b.ex[REP].quote
check("ref_guard_tilted: Dem ask and Rep bid rest (gap 1.7c < 5c), adding sides off in reduce-only",
      sides_of(dq) == ["ask"] and sides_of(rq) == ["bid"], (dq, rq))
check("...and both are on the exchange", [x for x, _, _ in a.ours(DEM)] == ["ask"]
      and [x for x, _, _ in a.ours(REP)] == ["bid"], (a.ours(DEM), a.ours(REP)))
check("...never crossing: Dem ask > best bid 0.86, Rep bid < best ask 0.15", dq.ask > 0.86 and rq.bid < 0.15, (dq, rq))
a, b = house_bot(ref_guard_tilted=True, s=0.0)
check("ref_guard_tilted with s 0 (= raw r): still blocked", sides_of(b.ex[DEM].quote) == [], b.ex[DEM].quote)
a, b = house_bot(ref_guard_tilted=True, ref_tilt_headline=False)
check("ref_guard_tilted, T2.1 off in headline markets: raw r, still blocked", sides_of(b.ex[DEM].quote) == [],
      b.ex[DEM].quote)
# a genuine jump: Polymarket 0.905 (r' 0.861 = the book) -> +8c to 0.985: r' 0.932, 7.1c past the book
a, b = house_bot(dem_ref=0.985, rep_ref=0.015, ref_guard_tilted=True)
check("genuine 8c Polymarket move: the tilted guard still blocks both exits",
      sides_of(b.ex[DEM].quote) == [] and sides_of(b.ex[REP].quote) == [], (b.ex[DEM].quote, b.ex[REP].quote))
a, b = house_bot(dem_ref=0.905, rep_ref=0.095, ref_guard_tilted=True)
check("...from 0.905 (no jump) the exits rest", sides_of(b.ex[DEM].quote) == ["ask"], b.ex[DEM].quote)

a, b = house_bot(ref_guard_exits=True)
dq, rq = b.ex[DEM].quote, b.ex[REP].quote
check("ref_guard_exits: Dem ask and Rep bid rest despite the 6.4c raw gap", sides_of(dq) == ["ask"]
      and sides_of(rq) == ["bid"], (dq, rq))
check("...capped at the position: Dem ask <= 9,876 held, Rep bid <= 9,396 (never flips it: no buy-NO part)",
      dq.ask_size <= 9876 and rq.bid_size <= 9396, (dq.ask_size, rq.bid_size))
check("...no 'buy NO' on Dem (the 10,000 ask of the guard-off case would have been one)",
      not any(o["exchangeId"] == DEM and o["side"] == "no" and o["action"] == "buy" for o in a.orders.values()),
      list(a.orders.values()))
a, b = house_bot(dem_ref=0.985, rep_ref=0.015, ref_guard_exits=True)
check("ref_guard_exits on a genuine jump: exits still rest (the exemption is about this market's own position)",
      sides_of(b.ex[DEM].quote) == ["ask"], b.ex[DEM].quote)
a, b = house_bot(reduce_only=False, ref_guard_exits=True, inv={DEM: -500, REP: 500})
check("adding sides still blocked: short Dem with Polymarket above the book -> no ask (it would add)",
      "ask" not in sides_of(b.ex[DEM].quote) and "bid" in sides_of(b.ex[DEM].quote), b.ex[DEM].quote)
check("...long Rep with Polymarket below the book -> no bid (adding)", "bid" not in sides_of(b.ex[REP].quote),
      b.ex[REP].quote)
a, b = house_bot(reduce_only=False, ref_guard_exits=True, inv={})
check("flat: both guarded sides blocked as before (Dem no ask, Rep no bid)",
      "ask" not in sides_of(b.ex[DEM].quote) and "bid" not in sides_of(b.ex[REP].quote),
      (b.ex[DEM].quote, b.ex[REP].quote))
a, b = house_bot(ref_guard_tilted=True, ref_guard_exits=True, dem_ref=0.985, rep_ref=0.015)
check("both flags, jump: exits rest (exits flag), sizes within the positions",
      sides_of(b.ex[DEM].quote) == ["ask"] and b.ex[DEM].quote.ask_size <= 9876 and b.ex[REP].quote.bid_size <= 9396)

print("--- flags off identical to the base code (git show %s:mm_bot.py) on a grid" % BASE_REF)
base = None
try:
    root = os.path.dirname(HERE)
    src = subprocess.run(["git", "-C", root, "show", f"{BASE_REF}:mm_bot.py"], capture_output=True, text=True, timeout=30)
    if src.returncode == 0:
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p8base.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p8base", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        base.alert = M.alert
        base.notify = lambda *a_, **k: False
except Exception as e:                          # no git here: the pin is reported as failed
    print("    (base module unavailable:", e, ")")


def twin(inv, refs, house, reduce, tilt_s, overrides):
    """The same scenario on the new Bot (flags off) and on a base-module Bot (own temp files)."""
    extra = (market("5", DEM, "Democratic", "U.S. House"), market("6", REP, "Republican", "U.S. House")) if house else ()
    books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
             "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
             "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    if house:
        books[DEM] = {"bids": [lvl(0.86, 5000)], "asks": [lvl(0.88, 5000)]}
        books[REP] = {"bids": [lvl(0.13, 5000)], "asks": [lvl(0.15, 5000)]}
    out = []
    for mod in (M, base):
        api_x, bn = make_bot(books={e: {"bids": list(v["bids"]), "asks": list(v["asks"])} for e, v in books.items()},
                             extra_markets=extra)
        if mod is base:
            cfg = base.Config()
            for k in base.Config.__dataclass_fields__:
                setattr(cfg, k, getattr(bn.cfg, k))
            d = tempfile.mkdtemp()
            for k in ("fills_csv", "status_file", "order_notes_file", "kill_file", "position_lots_file", "overrides_file",
                      "market_edge_file", "handover_file"):
                setattr(cfg, k, os.path.join(d, os.path.basename(getattr(cfg, k))))
            api_x = FakeApi(True)
            api_x.markets_list, api_x.books = list(bn.api.markets_list), bn.api.books
            bn = base.Bot(api_x, cfg)
        api_x.inv.update(inv)
        bn.refs = FakeRefs(dict(refs))
        bn.cfg.size_by_activity = house
        bn.cfg.selftest_enabled = False
        if reduce:
            bn.cfg.max_worst_case_frac, bn.cfg.worst_case_backstop_frac = 0.001, 0.002
        for k, v in overrides.items():
            setattr(bn.cfg, k, v)
        pin_tilt(bn, tilt_s)
        quiet_cycle(bn, 2)
        out.append((api_x, bn))
    return out


def strip(w):
    return [{k: v for k, v in o.items() if k != "expirationDate"} for o in w]


if base is None:
    check(f"base module (git show {BASE_REF}:mm_bot.py) loaded", False)
else:
    ok, diffs, n = True, [], 0
    house_refs = dict(REFS, **{"U.S. House|Democratic": 0.925, "U.S. House|Republican": 0.075})
    grid_inv = [{}, {"11": 300, "21": 3000, "22": -3000}, {"21": 600, "22": 400}, {DEM: 9876, REP: -9396},
                {DEM: -500, REP: 500, "12": -200}]
    for inv in grid_inv:
        for reduce in (False, True):
            for tilt in ({}, {"ref_tilt_enabled": True, "ref_tilt_headline": True}):
                for ceiling in ({}, {"capital_in_positions_max_frac": 0.001, "capital_ceiling_adding_size_factor": 0.0}):
                    (an, bn), (ab, bb) = twin(inv, house_refs, True, reduce, 0.11, {**tilt, **ceiling})
                    n += 1
                    qn = {e: repr(bn.ex[e].quote) for e in bn.ex}          # (two modules: two Quote classes)
                    qb = {e: repr(bb.ex[e].quote) for e in bb.ex}
                    keys_n = [bn.change_key(bn.ex[e], p, r) for e in bn.ex for p in (0, 1) for r in (0, 1)]
                    keys_b = [bb.change_key(bb.ex[e], p, r) for e in bb.ex for p in (0, 1) for r in (0, 1)]
                    hn = {k: v for k, v in bn.health.items() if not k.startswith("capital_ceiling_adding")}
                    if (strip(an.wire) != strip(ab.wire) or qn != qb or keys_n != keys_b or an.inv != ab.inv
                            or hn.keys() != bb.health.keys()):
                        ok = False
                        diffs.append((inv, reduce, tilt, ceiling))
    check(f"flags off: orders sent, quotes, change keys, health identical to the base on {n} scenarios "
          "(positions x reduce-only x T2.1 x capital ceiling)", ok, diffs[:3])

print()
print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
