"""
Offline tests for the Package 13 red team (analysis/p13/REDTEAM.md), scope CRASH and DUPLICATE ORDER only. Full
Bot.cycle runs on the fake exchange with the staged file deploy/package13/settings_override.aggressive.json applied
through the bot's own override path (check_overrides), on a simulated clock. Every finding's failing case, then fixed:
RT13-1 a harvest batch that timed out AFTER its levels landed: the levels were unknown to the bot (no recovery, no
       re-read): after pending_seconds the ladder was placed a second time on top of them (two resting at one price).
RT13-2 on a laddered market the quote's reduce-only side still offered the shares the harvest levels already offer
       (the same held YES / NO sold twice).
RT13-3 a positions read that lags the sleeve's buy (or the last good read on a 409) dropped the leg: re-bought as new.
RT13-4 election takes: a called loser's YES "covered" sale used ex.inv (last cycle's, never refreshed on a market
       traded since): the same shares sold again every cycle, as "covered" (no cash, no cap, no holdback).
Then the battery: the staged file and each flag off, restarts, empty / one-sided / crossed books, missing Polymarket,
unknown markets, ApiError on place / cancel (also after the orders landed), partial fills, cash-gate refusals, the kill
switch and the tripwire, a stop mid-cycle, election-night clock jumps, the flag flips; each cycle audited: no exception,
no P13 tick crash, no two IOCs of one intent on a market, no take + quote on one side, no ladder level twice, no two of
our orders resting at one price; restarts restore the sleeve and never re-sell an exit. Then 3 h on the live snapshot
(/home/claude/snap04, skipped without it).

Run:  python tests/test_p13_redteam.py      (exit code 0 = all passed)
"""
import json
import os
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ["P9_SNAP"] = os.environ.get("P13_SNAP", "/home/claude/snap04")
import test_p9_dryrun as P                                       # noqa: E402  (simulated clock, log capture, snapshot)
from fakes import FakeRefs, lvl, make_bot, market               # noqa: E402
import mm_bot as M                                                # noqa: E402

RESULTS = []
M.notify = lambda *a, **k: False
CLK, CAP = P.CLK, P.CAP
STAGED = os.path.join(ROOT, "deploy", "package13", "settings_override.aggressive.json")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(extra)[:600]}]" if not cond else ""))
    RESULTS.append(bool(cond))


def at(s):
    """CLK.off that puts the simulated clock at the UTC time s."""
    return (datetime.fromisoformat(s.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds() + 0.0


# ============================================================================================ the synthetic world
# (race, Republican (p, bid, ask), Democratic (p, bid, ask)); every level 3000 shares deep, a second level 1c behind
RACES = [("Mo1 Senate", (0.85, 0.84, 0.86), (0.15, 0.14, 0.16)),     # Dem: momentum YES
         ("Mo2 Senate", (0.80, 0.82, 0.83), (0.20, 0.17, 0.21)),     # momentum through the favourite's NO
         ("Mo3 Governor", (0.90, 0.88, 0.90), (0.10, 0.09, 0.11)),   # Rep: harvest bids
         ("Val1 Senate", (0.98, 0.86, 0.90), (0.02, 0.01, 0.12)),    # value short held; harvest both sides
         ("Val2 Governor", (0.95, 0.93, 0.94), (0.05, 0.04, 0.07)),  # value long held (the sell-down)
         ("Mid House", (0.50, 0.48, 0.52), (0.50, 0.47, 0.51)),
         ("Hv1 House", (0.95, 0.85, 0.97), (0.05, 0.04, 0.10))]      # Dem: harvest asks over YES held


def eid(k, dem):
    return f"{k + 1}{2 if dem else 1}0"


def books():
    out = {}
    for k, (_, r, d) in enumerate(RACES):
        for j, (_, bid, ask) in enumerate((r, d)):
            out[eid(k, j == 1)] = {"bids": [lvl(bid, 3000), lvl(round(bid - 0.01, 2), 3000)],
                                   "asks": [lvl(ask, 3000), lvl(round(ask + 0.01, 2), 3000)]}
    return out


def refs():
    out = {}
    for race, r, d in RACES:
        out[f"{race}|Republican"], out[f"{race}|Democratic"] = r[0], d[0]
    return out


INV0 = {eid(3, True): -30000, eid(4, False): 20000}
SENT = []                                         # [cycle, feature, order (wire), result]


def mk(cash=50000.0, equity=100000.0, inv=None, tweak=None):
    mkts = []
    for k, (race, _, _) in enumerate(RACES):
        mkts += [market(f"m{k}1", eid(k, False), "Republican", race), market(f"m{k}2", eid(k, True), "Democratic", race)]
    bks = books()
    api, b0 = make_bot(live=True, books=bks, extra_markets=tuple(mkts))
    b0.close()
    api.markets_list, api.books = mkts, bks
    cfg = b0.cfg
    cfg.selftest_enabled = False
    cfg.reserved_cash_mode = "ignore"
    api.inv.update(INV0 if inv is None else inv)
    api.cash, api.equity = cash, equity
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": api.cash})[1]
    raw = json.load(open(STAGED))
    raw.update(tweak or {})
    with open(cfg.overrides_file, "w") as f:
        json.dump(raw, f)
    api.cycle_no, api.tag = 0, "quote"
    real = api.place_batch

    def place_batch(orders):
        n0 = len(SENT)
        for o in orders:
            SENT.append([api.cycle_no, api.tag, dict(o), None])
        res = real(orders)
        for k, r in enumerate(res or []):
            SENT[n0 + k][3] = r
        return res
    api.place_batch = place_batch
    return api, boot(api, cfg)


FEATURES = ("take_stale_quotes", "take_aged", "tilt_exit_takes", "basket_tick", "take_arbitrage", "pair_followup_step",
            "pair_passive_step", "alloc_tick", "p13_tick", "el_tick", "hv_tick")


def boot(api, cfg, prices=None):
    """A (re)start: a new Bot from the files (status.json, notes), the overrides file read by check_overrides, the
    clean slate run() does."""
    b = M.Bot(api, cfg)
    b.refs = FakeRefs(prices or refs())
    b.overrides_mtime = None
    b.check_overrides(force=True)
    for name in FEATURES:
        orig = getattr(b, name)

        def wrapped(*a, _o=orig, _n=name, **k):
            api.tag = _n
            try:
                return _o(*a, **k)
            finally:
                api.tag = "quote"
        setattr(b, name, wrapped)
    try:
        b.cancel_everything()
    except M.ApiError:                            # (as run(): leftovers get re-read and managed)
        b.orders_stale = True
    return b


def set_flags(b, **kw):
    raw = json.load(open(b.cfg.overrides_file))
    raw.update(kw)
    with open(b.cfg.overrides_file, "w") as f:
        json.dump(raw, f)
    b.overrides_mtime = None
    b.check_overrides(force=True)


def cyc(b, step=60.0):
    """One cycle as run() runs it (cycle, then status.json); the exception, if any."""
    api = b.api
    api.cycle_no += 1
    b.refs.new_reading(dict(b.refs.prices), {})
    err = None
    try:
        b.cycle()
        b.drain_writes(5)
        b.write_status(True)
    except Exception as e:                        # (reported by the caller)
        err = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-800:]
    CLK.off += step
    return err


def yes(o):
    y = o["side"] == "yes"
    return (y == (o["action"] == "buy")), (o["price"] if y else round(1 - o["price"], 3))


def audit(api, n):
    """Cycle n's orders and what rests after it: [(problem, ...)]."""
    bad = []
    xs = [s for s in SENT if s[0] == n]
    by = defaultdict(list)
    for _, tag, o, _ in xs:
        buy, px = yes(o)
        by[(o["exchangeId"], buy)].append((tag, px, o["quantity"]))
    for (e, buy), lst in by.items():
        tags = Counter(t for t, _, _ in lst)
        if tags["p13_tick"] > 1 or tags["el_tick"] > 1:
            bad.append(("IOC of one intent twice", e, buy, lst))
        pxs = [px for t, px, _ in lst if t == "hv_tick"]
        if len(set(pxs)) < len(pxs):
            bad.append(("ladder level twice", e, buy, lst))
        if (tags["p13_tick"] or tags["el_tick"]) and (tags["quote"] or tags["hv_tick"]):
            bad.append(("take + quote on one side", e, buy, lst))
    for e, k in Counter(o["exchangeId"] for _, t, o, _ in xs if t == "p13_tick").items():
        if k > 1:                                 # a sell-down and a momentum buy / exit on one market
            bad.append(("two Package 13 orders on one market", e))
    for k, v in Counter((o["exchangeId"],) + api.yes_view(o) for o in api.orders.values()).items():
        if v > 1:
            bad.append(("two of our orders resting at one price", k, v))
    ids = [(r or {}).get("data", {}).get("orderId") for _, _, _, r in xs]
    ids = [i for i in ids if i is not None]
    if len(ids) != len(set(ids)):
        bad.append(("duplicate order ids", ids))
    return bad


def tick_crashes(n0):
    return [m for _, lv, m in CAP.lines[n0:] if "tick failed" in m or "unexpected error" in m]


def same_legs(a, b):
    return set(a) == set(b) and all(abs(a[e]["q"] - b[e]["q"]) < 1e-6 and abs(a[e]["cost"] - b[e]["cost"]) < 1e-3
                                    for e in a)


def scenario(name, n, perturb=None, tweak=None, restart_every=0, inv=None, cash=50000.0, step=60.0, start=None):
    """n cycles; each audited. Returns (api, bot, errors, problems)."""
    CLK.off = at(start) if start else 0.0
    del SENT[:]
    n0 = len(CAP.lines)
    api, b = mk(tweak=tweak, inv=inv, cash=cash)
    errs, probs = [], []
    exit_sold, exit_start = defaultdict(float), {}
    for i in range(n):
        if perturb:
            b = perturb(i, api, b) or b
        if restart_every and i and i % restart_every == 0:
            legs, st = {e: dict(v) for e, v in b.mom_legs.items()}, b.mom_state
            b.close()
            b = boot(api, b.cfg, dict(b.refs.prices))
            if not same_legs(legs, b.mom_legs) or b.mom_state != st:
                probs.append(("restart: sleeve not restored", i, legs, dict(b.mom_legs), st, b.mom_state))
        k0 = len(SENT)
        err = cyc(b, step)
        if err:
            errs.append((i, err))
        probs += [(i,) + p for p in audit(api, api.cycle_no)]
        if b.mom_state in ("exiting", "killed", "exited"):
            for e, q in b.mom_exit_start.items():
                exit_start.setdefault(e, abs(q))
        for _, t, o, r in SENT[k0:]:
            e = o["exchangeId"]
            if t == "p13_tick" and e in exit_start and (r or {}).get("ok"):
                exit_sold[e] += float((r.get("data") or {}).get("quantityTraded") or 0)
    over = {e: (s, exit_start[e]) for e, s in exit_sold.items() if s > exit_start[e] + 1e-6}
    if over:
        probs.append(("exit sold more than the leg", over))
    cr = tick_crashes(n0)
    check(f"{name}: {n} cycles, no exception, no Package 13 tick crash", not errs and not cr, (errs[:1], cr[:2]))
    check(f"{name}: no duplicate order (one cycle / resting / restart / exit)", not probs, probs[:3])
    return api, b, errs, probs


# ============================================================================================ RT13-1
print("--- RT13-1: a harvest batch that timed out after its levels landed: adopted, never placed twice")
api, b = mk(tweak={"momentum_enabled": False, "buckets_enabled": False})
CLK.off = 0.0
del SENT[:]
armed = {"on": True}
real_pb = api.place_batch


LANDED = []


def pb_timeout(orders):
    if api.tag == "hv_tick" and armed["on"]:
        armed["on"] = False
        LANDED.extend((api.cycle_no, o["exchangeId"]) + yes(o) for o in orders)
        api.batch_error_after = M.ApiError(504, "TIMEOUT", "gateway timeout")
        try:
            return real_pb(orders)
        finally:
            api.batch_error_after = None
    return real_pb(orders)


api.place_batch = pb_timeout
probs = []
for i in range(8):
    cyc(b, 30.0)
    probs += audit(api, api.cycle_no)
hv = [(c, o["exchangeId"]) + yes(o) for c, t, o, _ in SENT if t == "hv_tick"]
again = [x for x in hv if any(x[0] > c and x[1:] == y for c, *y in [(z[0], *z[1:]) for z in LANDED])]
check("setup: the timed-out batch landed on the book (its levels rest)", LANDED and all(
    any(o["exchangeId"] == x[1] and api.yes_view(o) == x[2:] for o in api.orders.values()) for x in LANDED[:1]), LANDED)
check("none of the landed levels placed again while they rest (adopted)", not again, again)
check("no two of our orders resting at one price over 8 cycles (4 min)", not probs, probs[:2])
ad = [o for o in b.my_orders.values() if (b.order_meta.get(o.order_id) or {}).get("harvest")
      and (b.order_meta.get(o.order_id) or {}).get("recovered")]
check("the landed levels carry their harvest notes (hidden from the quote, re-quoted as the ladder)", len(ad) >= 1)

# ============================================================================================ RT13-2
print("--- RT13-2: the quote's reduce-only side leaves the shares the harvest levels offer")
e_hv = eid(6, True)
api, b = mk(inv={**INV0, e_hv: 2000}, tweak={"momentum_enabled": False, "buckets_enabled": False})
CLK.off = 0.0
for i in range(4):
    cyc(b)
asks = [(px, q) for side, px, q in api.ours(e_hv) if side == "ask"]
hv_asks = sum(o.qty for o in b.hv_orders(e_hv) if not o.is_bid)
check("setup: harvest asks rest over the 2000 YES held", hv_asks >= 2000, api.ours(e_hv))
check("no quote ask on top of them (the held YES offered once)", sum(q for _, q in asks) == hv_asks, (asks, hv_asks))
hsq = getattr(b, "hv_side_qty", lambda *a: None)
check("hv_side_qty: the harvest asks there; 0 with the ladder off", hsq(e_hv, False) == hv_asks and hsq(e_hv, True) == 0)
b.hv_sides = {}
check("hv_side_qty: 0 without a ladder side (flags off: unchanged)", hsq(e_hv, False) == 0)

# ============================================================================================ RT13-3
print("--- RT13-3: a lagging / 409 positions read never drops a leg just bought (no re-buy)")
for mode in ("lag", "409"):
    api, b = mk()
    CLK.off = 0.0
    del SENT[:]
    real_pos = api.positions
    hold = {"on": False, "snap": dict(api.inv)}

    def positions(_r=real_pos, _h=hold, _m=mode):
        if _h["on"]:
            if _m == "409":
                raise M.ApiError(409, "CONFLICT", "holdings cannot be valued")
            return {"positions": [{"exchangeId": e, "quantity": q, "settled": False} for e, q in _h["snap"].items()],
                    "summary": {"totalMarketValue": 0}}
        return _r()
    api.positions = positions
    cyc(b)
    legs1 = {e: dict(v) for e, v in b.mom_legs.items()}
    hold["on"] = True
    if mode == "409":                             # (the last good read: before the buy)
        b.cached_pos = {"positions": [{"exchangeId": e, "quantity": q, "settled": False}
                                      for e, q in hold["snap"].items()], "summary": {"totalMarketValue": 0}}
    cyc(b, 5.0)
    rebuy = [o["exchangeId"] for c, t, o, _ in SENT if c == api.cycle_no and t == "p13_tick"
             and o["exchangeId"] in legs1]
    check(f"{mode}: setup: the first cycle bought legs", len(legs1) >= 2, legs1)
    check(f"{mode}: the legs survive the stale read", all(e in b.mom_legs and b.mom_legs[e]["q"] >= legs1[e]["q"]
                                                         for e in legs1), (legs1, b.mom_legs))
    check(f"{mode}: no leg bought again on the stale read", not rebuy, rebuy)
    hold["on"] = False
    CLK.off += M.Bot.BASKET_TRADE_GRACE
    q_before = {e: api.inv.get(e, 0) for e in legs1}
    cyc(b, 5.0)
    check(f"{mode}: after the grace a fresh read: the legs follow the positions again",
          all(abs(b.mom_legs[e]["q"]) <= abs(api.inv.get(e, 0)) + 1e-9 for e in b.mom_legs), (b.mom_legs, q_before))
b.mom_last_trade = {}
inv_t = {e: 0.0 for e in b.mom_legs}
b.mom_reconcile(inv_t)
check("a leg not traded lately still follows a smaller position (dropped when flat)", not b.mom_legs)

# ============================================================================================ RT13-4
print("--- RT13-4: election night: a called loser's YES sold once as covered, never again from a stale ex.inv")
CLK.off = at("2026-11-03T23:30:00Z")
del SENT[:]
e_l = eid(0, True)
api, b = mk(inv={**INV0, e_l: 3000}, tweak={"momentum_enabled": False, "buckets_enabled": False,
                                            "tilt_harvest_ladder": False})
b.cfg.take_enabled = False
r = refs()
r["Mo1 Senate|Republican"], r["Mo1 Senate|Democratic"] = 0.99, 0.01
b.refs.prices = r
for i in range(16):
    if i == 13:
        api.books = books()
    cyc(b, 60.0)
el = [(o, rr) for _, t, o, rr in SENT if t == "el_tick" and o["exchangeId"] == e_l]
cov = [m for _, _, m in CAP.lines if "ELECTION take Dem Mo1 Senate" in m and "(covered)" in m]
covered_sold = sum(float(m.split("traded ")[1].split(" ")[0]) for m in cov)
check("setup: the loser's YES were sold by an election take", len(el) >= 1 and api.inv.get(e_l, 0) < 3000, el[:1])
check("the covered sales never exceed the 3000 YES held", covered_sold <= 3000 + 1e-9, (covered_sold, cov))
spent = b.el_totals["cash_spent"]
short = max(0.0, -api.inv.get(e_l, 0))
check("every share sold beyond them was a cash sale (counted against the holdback)",
      short < 1 or spent >= short * (1 - 0.13) * 0.5, (short, spent))
room = b.cfg.aggr_max_race_usd
check("the loser's short stays inside the race cap", short * (1 - 0.01) <= room + 1e-6, short)

# ============================================================================================ the battery
print("--- the battery: the staged file, each flag off, restarts, books, references, errors, the clock, the flips")


def refill(i, api, b):
    if i % 7 == 0:
        api.books = books()


scenario("staged file (all on)", 60, refill)
scenario("staged file, a restart every 9 cycles", 60, refill, restart_every=9)
for flag in ("buckets_enabled", "aggressive_value", "momentum_enabled", "tilt_harvest_ladder", "election_night"):
    scenario(f"{flag} off", 40, refill, tweak={flag: False}, restart_every=13)
scenario("close_override_utc empty", 30, refill, tweak={"close_override_utc": ""})


def weird(i, api, b):
    refill(i, api, b)
    e = eid(i % len(RACES), i % 2 == 0)
    api.books[e] = [{"bids": [], "asks": []}, {"bids": [lvl(0.30, 500)], "asks": []},
                    {"bids": [lvl(0.60, 500)], "asks": [lvl(0.40, 500)]},
                    {"bids": [], "asks": [lvl(0.05, 500)]}, api.books[e]][i % 5]


scenario("empty / one-sided / crossed books", 50, weird)


def norefs(i, api, b):
    refill(i, api, b)
    r = refs()
    for k in list(r)[:(i % 15)]:
        r.pop(k)
    b.refs.prices = r


scenario("Polymarket references missing", 40, norefs)


def unknown(i, api, b):
    refill(i, api, b)
    if i == 3:
        api.inv["999"] = 500                      # a position on a market we do not list
        api.books["999"] = {"bids": [lvl(0.1, 10)], "asks": [lvl(0.2, 10)]}
    if i == 8:
        api.markets_list = api.markets_list[:-2]  # a market (with a harvest ladder) disappears
        b.last_reload = -1e9
        b.load_markets()


scenario("unknown / removed markets", 30, unknown)


def apierr(i, api, b):
    refill(i, api, b)
    api.batch_error = M.ApiError(500, "INTERNAL", "boom") if i % 4 == 1 else None
    api.cancel_error = M.ApiError(503, "SERVICE_UNAVAILABLE", "nope") if i % 5 == 2 else None
    api.batch_error_after = M.ApiError(504, "TIMEOUT", "late") if i % 6 == 3 else None


scenario("ApiError on place / cancel / after landing", 60, apierr, restart_every=17)


def partial(i, api, b):
    refill(i, api, b)
    api.take_cap = 700 if i % 3 else None
    api.cancel_error = M.ApiError(503, "SERVICE_UNAVAILABLE", "nope") if i % 4 == 2 else None


scenario("partial fills (leftovers resting, cancels failing)", 40, partial, step=4.0)
scenario("cash-gate refusals (cash at the reserve)", 30, refill, cash=21000.0)


def kill(i, api, b):
    refill(i, api, b)
    if i == 10:
        api.equity = 60000.0


api, b, _, _ = scenario("kill switch", 20, kill)
check("kill switch: the bot stopped; nothing sent after it", not b.running
      and not [s for s in SENT if s[0] > 12], [s[:2] for s in SENT if s[0] > 12][:3])


def trip(i, api, b):
    refill(i, api, b)
    if i == 6:
        api.inv[eid(5, False)] = -150000


api, b, _, _ = scenario("backstop tripwire", 20, trip)
mb = [s for s in SENT if s[0] > 7 and s[1] == "p13_tick" and yes(s[2])[0]
      and s[2]["exchangeId"] in b.mom_legs and b.mom_legs[s[2]["exchangeId"]]["q"] > 0]
check("tripwire: reduce-only, no momentum buy after it", b.global_reduce and not mb, mb[:2])


def stop_mid(i, api, b):
    refill(i, api, b)
    if i == 2:
        real = api.place_batch

        def pb(orders, _r=real):
            res = _r(orders)
            if api.tag in ("p13_tick", "el_tick", "hv_tick"):
                b.running = False                 # (Ctrl+C / the kill: the rest of the cycle stops)
            return res
        api.place_batch = pb


scenario("a stop mid-cycle (after a Package 13 write)", 6, stop_mid)


def flips(i, api, b):
    refill(i, api, b)
    for k, kw in {5: {"momentum_exit": True}, 15: {"momentum_enabled": False}, 20: {"momentum_enabled": True},
                  25: {"momentum_exit": False}, 30: {"momentum_enabled": False}, 32: {"momentum_enabled": True}}.items():
        if i == k:
            set_flags(b, **kw)


api, b, _, _ = scenario("flips: momentum_exit false->true, momentum_enabled true->false->true", 45, flips,
                        restart_every=7)
buys_after = [s for s in SENT if s[0] > 6 and s[1] == "p13_tick" and "momentum buy" in str(s)]
check("flips: the exit sold the sleeve; no buy re-opened it before the latch reset", b.mom_state in
      ("exiting", "exited", "active") and all(s[0] > 26 for s in buys_after), [s[:2] for s in buys_after][:3])


def kill_sleeve(i, api, b):
    refill(i, api, b)
    if i >= 4:
        for e in list(b.mom_legs):
            api.books[e] = {"bids": [lvl(0.01, 3000)], "asks": [lvl(0.03, 3000)]}


api, b, _, _ = scenario("the sleeve's kill (-30% at the mark), restarts", 30, kill_sleeve, restart_every=11)
check("the kill latched (killed / exited)", b.mom_state in ("killed", "exited"), b.mom_state)


def election(jumps):
    def pert(i, api, b):
        if i in jumps:
            CLK.off = at(jumps[i])
            api.books = books()
        if i == 10:
            r = refs()
            r["Mo1 Senate|Republican"], r["Mo1 Senate|Democratic"] = 0.99, 0.01
            r["Val2 Governor|Republican"], r["Val2 Governor|Democratic"] = 1.0, 0.0      # resolved
            r["Mo3 Governor|Republican"] = 0.985
            b.refs.prices = r
        if i == 30:
            r = dict(b.refs.prices)
            r["Mo1 Senate|Republican"], r["Mo1 Senate|Democratic"] = 0.90, 0.10           # un-called
            b.refs.prices = r
    return pert


NIGHT = {0: "2026-11-03T11:55:00Z", 8: "2026-11-03T22:50:00Z", 40: "2026-11-04T16:50:00Z",
         50: "2026-11-04T17:01:00Z"}
api, b, _, _ = scenario("election night (holdback, calls, takes, stop window, past the close)", 60, election(NIGHT))
check("election night: takes fired on the called races", b.el_totals["orders"] >= 1, b.el_totals)
check("election night: the holdback never overspent", b.el_totals["cash_spent"] <= b.cfg.election_holdback_usd + 1e-6,
      b.el_totals)
scenario("election night, a restart every 5 cycles", 60, election(NIGHT), restart_every=5)
scenario("election night with close_override_utc empty", 60, election(NIGHT), tweak={"close_override_utc": ""},
         restart_every=6)
scenario("election night with momentum_exit on", 60, election(NIGHT), tweak={"momentum_exit": True})
scenario("clock jumps: 3 Nov 12:00 holdback, 23:00 start, 4 Nov 17:00 close, back", 30, election(
    {0: "2026-11-03T11:59:30Z", 3: "2026-11-03T12:00:30Z", 6: "2026-11-03T22:59:30Z", 9: "2026-11-03T23:00:30Z",
     15: "2026-11-04T16:54:30Z", 20: "2026-11-04T17:00:30Z", 25: "2026-11-02T12:00:00Z"}), restart_every=8)


def walk(seed):
    import random
    rnd = random.Random(seed)

    def pert(i, api, b):
        if i % 10 == 0:
            api.books = books()
        r = dict(b.refs.prices)
        for k in r:                               # Polymarket drifts 0-1c a cycle
            r[k] = min(0.995, max(0.005, round(r[k] + rnd.choice((-0.01, 0, 0, 0.01)), 3)))
        b.refs.prices = r
        for o in list(api.orders.values()):       # other traders hit some of our resting orders
            if rnd.random() < 0.1:
                isb, _ = api.yes_view(o)
                api.fill(o["exchangeId"], isb, max(1, int(o["quantity"] * rnd.choice((0.3, 1.0)))))
    return pert


api, b, _, _ = scenario("3 h random walk (Polymarket drift, our orders filled), restarts", 180, walk(5),
                        restart_every=25)

# ============================================================================================ the live snapshot
print("--- 3 h on the live snapshot (237 markets) with the staged file, then 1 h with each flag off")
if not os.path.exists(os.path.join(P.SNAP, "md.sqlite")):
    print(f"SKIP: no snapshot at {P.SNAP}")
else:
    P.TAGGED = P.TAGGED + ("alloc_tick", "p13_tick", "el_tick", "hv_tick")
    S = P.load_snapshot()

    def snap_run(name, n, tweak=None, restart_every=0):
        CLK.off = 0.0
        n0 = len(CAP.lines)
        path = STAGED
        if tweak:
            raw = json.load(open(STAGED))
            raw.update(tweak)
            path = os.path.join(P.tempfile.mkdtemp(), "staged.json")
            json.dump(raw, open(path, "w"))
        api, b = P.build(S, cash=60000.0, overrides=path)
        errs, probs = [], []
        for i in range(n):
            if restart_every and i and i % restart_every == 0:
                legs = {e: dict(v) for e, v in b.mom_legs.items()}
                b.write_status(True)
                b.close()
                b2 = M.Bot(api, b.cfg)
                b2.refs = b.refs
                P.tag_methods(b2)
                P.apply_stage(b2, path)
                b2.cancel_everything()
                if not same_legs(legs, b2.mom_legs):
                    probs.append(("restart: sleeve not restored", i))
                b = b2
            k0 = len(api.log_orders)
            try:
                P.cycles(b, 1, step=60.0, stage=name, refill_every=20, status=True)
            except Exception as e:
                errs.append((i, repr(e)))
            new = api.log_orders[k0:]
            c = Counter((o["eid"], o["yes_buy"], o["tag"]) for o in new if o["tag"] in ("p13_tick", "el_tick"))
            probs += [("IOC twice", k) for k, v in c.items() if v > 1]
            c = Counter((o["eid"], o["yes_buy"], o["yes_price"]) for o in new if o["tag"] == "hv_tick")
            probs += [("ladder level twice", k) for k, v in c.items() if v > 1]
            sides = defaultdict(set)
            for o in new:
                sides[(o["eid"], o["yes_buy"])].add(o["tag"])
            probs += [("take + quote", k, v) for k, v in sides.items()
                      if v & {"p13_tick", "el_tick"} and v & {"quote", "hv_tick"}]
            c = Counter(o["eid"] for o in new if o["tag"] == "p13_tick")
            probs += [("two P13 orders on one market", k) for k, v in c.items() if v > 1]
            rest = Counter((o["exchangeId"],) + api.yes_view(o) for o in api.orders.values())
            probs += [("two resting at one price", k) for k, v in rest.items() if v > 1]
        cr = tick_crashes(n0)
        tags = Counter(o["tag"] for o in api.log_orders)
        check(f"snapshot {name}: {n} cycles, no exception, no Package 13 tick crash", not errs and not cr,
              (errs[:1], cr[:2]))
        check(f"snapshot {name}: no duplicate order ({sum(tags.values())} orders: {dict(tags)})", not probs, probs[:3])
        return api, b

    api, b = snap_run("staged", 180, restart_every=45)
    check("snapshot staged: the Package 13 features traded (sell-down / harvest)",
          any(o["tag"] in ("p13_tick", "hv_tick") for o in api.log_orders))
    for flag in ("buckets_enabled", "aggressive_value", "momentum_enabled", "tilt_harvest_ladder", "election_night"):
        snap_run(f"{flag} off", 60, tweak={flag: False})

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
