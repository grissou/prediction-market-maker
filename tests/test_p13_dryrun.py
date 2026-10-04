"""
Package 13 END-TO-END DRY RUN on the fake exchange, seeded with the REAL live state of 4 Oct 15:56 UTC (the ops snapshot
ops-snapshot-2026-10-04 in /home/claude/snap04, or $P9_SNAP; the tests/test_p9_dryrun.py harness: all 237 markets, other
traders' books, raw Polymarket references, positions, the exchange's marks, cash, position lots). Overrides go through
the bot's own override path (check_overrides); full cycles (Bot.cycle) run on a simulated clock; every order the fake
receives is logged with the feature that sent it (Package 13: bucket_sell / momentum / mom_exit from Bot.p13_tick's
steps, harvest from Bot.hv_tick, election from Bot.el_takes).
Scenarios (analysis/p12/SPEC_P13_AGGRESSIVE.md section 6):
  0  base = the live file + alloc_mm_reserve 20000, backstop 0.90, mm_risk_reserve_wc 5000 / _corr 4000, every P13 flag
     off: identical orders and status to the Package 13 staged commit 3e635d7 (and to the pre-Package-13 code 663ede1);
  1  the staged file deploy/package13/settings_override.aggressive.json (everything on): 2 h, hourly rebalance twice;
  1F the "funded sleeve" fixture (the momentum bucket starves in scenario 1, see the report): the staged file with the
     harvest ladder, the allocator and the stale-quote takes off for 1 h and +45k of cash, then the staged file 1 h;
  2  a +3c tilt rise (in 3 steps of 1c over 1 h) on the fixture: harvest fills, the sleeve's mark vs cost, EV;
  3  the momentum kill (-30% mid move against the sleeve for > 120 s), the exit, the latch, harvest taking over;
  4  the manual exit (momentum_exit true): the 6-h linear schedule, never re-bought;
  5  election night (clock to 3 Nov, staged file): the holdback from 12:00, calls after 10 min, takes, stop at 16:55.
The fake here models a 28 writes / simulated minute limiter (writes_left / write_wait; it does not block: what the bot
sends beyond it is counted), aggregates book levels by price as the exchange does, and is thread-safe (parallel writes).
Fills on our RESTING orders happen only where a scenario rewrites other traders' books through them (scenario 2).
Writes the readable report to analysis/p13/DRYRUN.md (P13_DRYRUN_REPORT=0: no report).

Run:  python tests/test_p13_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time as _rt
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("P9_SNAP", "/home/claude/snap04")          # (the harness reads $P9_SNAP)
import test_p9_dryrun as P                                       # noqa: E402  (the harness: snapshot, fake, clock)
import mm_bot as M                                                # noqa: E402

STAGED = os.path.join(ROOT, "deploy", "package13", "settings_override.aggressive.json")
REPORT = os.path.join(ROOT, "analysis", "p13", "DRYRUN.md")
BASE_REVS = (("3e635d7", "the Package 13 staged commit"), ("663ede1", "the code before Package 13"))
BASE_EXTRA = {"alloc_mm_reserve": 20000.0, "worst_case_backstop_frac": 0.90, "mm_risk_reserve_wc": 5000.0,
              "mm_risk_reserve_corr": 4000.0}
P13_FLAGS = ("buckets_enabled", "aggressive_value", "momentum_enabled", "tilt_harvest_ladder", "election_night")
P13_STATUS = ("buckets", "aggressive", "momentum", "harvest", "election")
# feature tags: Package 9 / 10 methods as they are; Package 13 steps under the names of their order notes
FEATURES = {n: n for n in P.TAGGED + ("alloc_tick",)}
FEATURES.update(bucket_sell_step="bucket_sell", mom_buy_step="momentum", mom_exit_step="mom_exit", el_takes="election",
                hv_tick="harvest")
P13_TAGS = ("bucket_sell", "momentum", "mom_exit", "harvest", "election")
P.TAGGED = tuple(FEATURES)
WRITE_KINDS = ("batch", "cancel_all", "cancel_order")
WPM = 28
RESULTS, OUT, STATE = [], [], {}
TOL = 1e-6
S = None


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def out(s=""):
    OUT.append(s)


# ============================================================================================ the fake, extended
class Dry13(P.DryApi):
    """P.DryApi plus: a 28-writes-per-simulated-minute limiter (writes_left / write_wait: what the bot plans with; the
    fake does not block, so writes beyond it are counted, not refused), book levels aggregated by price (the exchange's
    view: our order at another trader's price is one level, which strip_own then splits correctly), a lock (the bot's
    parallel writes), p and the touch at send in the order log, and maker fills where a rewritten book crosses our
    resting orders (match_resting)."""

    def __init__(self, S_, cash=None):
        self._lk = threading.RLock()
        self.wt = deque()
        self.wall = []                            # (sim time, kind, feature)
        self.bot = None
        self.maker_fills = []
        super().__init__(S_, cash)
        self.wbudget = WPM

    def log(self, *a):
        if a and a[0] in WRITE_KINDS:
            with self._lk:
                self.wt.append(P.CLK.off)
                self.wall.append((P.CLK.off, a[0], self.tag))
        self.calls.append(a)

    limit = True                                  # False: unlimited writes (the P9 / P10 harness; scenario 0)

    def writes_left(self):
        if not self.limit:
            return 10 ** 6
        now = P.CLK.off
        with self._lk:
            while self.wt and now - self.wt[0] >= 60 - TOL:
                self.wt.popleft()
            return WPM - len(self.wt)

    def write_wait(self):
        return 0.0 if self.writes_left() > 0 else 60.0

    def place_batch(self, orders):
        with self._lk:
            b = self.bot
            ps, tops = [], []
            for o in orders:
                e = o["exchangeId"]
                ps.append(pval(b, e) if b is not None else None)
                yes_buy = (o["side"] == "yes") == (o["action"] == "buy")
                lv = (self.books.get(e) or {}).get("asks" if yes_buy else "bids") or []
                tops.append((lv[0]["price"], lv[0]["quantity"]) if lv else None)
            n0 = len(self.log_orders)
            res = super().place_batch(orders)
            for k, rec in enumerate(self.log_orders[n0:]):
                rec["p"], rec["top"] = ps[k], tops[k]
                if b is not None and rec["tag"] != "quote":     # (every race leg's p as the bot sees it at send)
                    rec["race_p"] = {m: next(x for x in (pval(b, m), b.ex[m].last_fv, 0.5) if x is not None)
                                     for m in rec["race_pos"] if m in b.ex}
            return res

    def cancel_all(self, tid, eid=None):
        with self._lk:
            return super().cancel_all(tid, eid)

    def cancel_order(self, oid):
        with self._lk:
            return super().cancel_order(oid)

    def open_orders(self, tid, eid=None):
        with self._lk:
            return super().open_orders(tid, eid)

    def full_book(self, eid):
        with self._lk:
            bk = super().full_book(eid)
        res = {}
        for k in ("bids", "asks"):
            agg = defaultdict(float)
            for lv in bk[k]:
                agg[round(lv["price"], 3)] += lv["quantity"]
            res[k] = [{"price": px, "quantity": q} for px, q in sorted(agg.items(), reverse=(k == "bids"))]
        return res

    def match_resting(self):
        """Other traders' (rewritten) book crossing our resting orders trades them at OUR price (we are the maker)."""
        with self._lk:
            self.expire()
            for oid, o in list(self.orders.items()):
                e = o["exchangeId"]
                is_bid, px = self.yes_view(o)
                side = (self.books.get(e) or {}).get("asks" if is_bid else "bids") or []
                while o["quantity"] > 0 and side and (side[0]["price"] <= px + TOL if is_bid
                                                      else side[0]["price"] >= px - TOL):
                    take = min(o["quantity"], side[0]["quantity"])
                    side[0]["quantity"] -= take
                    if side[0]["quantity"] <= 0:
                        side.pop(0)
                    o["quantity"] -= take
                    q0 = self.inv.get(e, 0.0)
                    if is_bid:
                        close = min(take, max(0.0, -q0))
                        self.cash += close * (1 - px) - (take - close) * px
                    else:
                        close = min(take, max(0.0, q0))
                        self.cash += close * px - (take - close) * (1 - px)
                    self.inv[e] = q0 + (take if is_bid else -take)
                    self.fills.append({"id": len(self.fills) + 1, "orderId": oid, "exchangeId": e, "price": px,
                                       "quantity": take if is_bid else -take, "side": "yes" if is_bid else "no",
                                       "filledAt": M.iso(M.utcnow())})
                    self.maker_fills.append({"t": P.CLK.off, "oid": oid, "eid": e, "bid": is_bid, "px": px,
                                             "qty": take, "pos_before": q0})
                if o["quantity"] <= 0:
                    del self.orders[oid]


def tag_methods13(b):
    """The harness's feature tags (P.tag_methods), with Package 13's names, plus per-tick write accounting for the
    budget shares: p13_tick / hv_tick / el_tick record the writes left at entry and the writes / orders they used."""
    api = b.api
    for name, feat in FEATURES.items():
        if not hasattr(b, name):
            continue
        orig = getattr(b, name)

        def wrapped(*a, _orig=orig, _feat=feat, **k):
            prev = b.api.tag
            b.api.tag = _feat
            try:
                return _orig(*a, **k)
            finally:
                b.api.tag = prev
        setattr(b, name, wrapped)
    api.ticks = []
    for name in ("p13_tick", "hv_tick", "el_tick"):
        if not hasattr(b, name):
            continue
        orig = getattr(b, name)

        def acct(*a, _orig=orig, _name=name, **k):
            w0, n0, o0 = api.writes_left(), len(api.wall), len(api.log_orders)
            pulled0 = (getattr(b, "hv_totals", None) or {}).get("pulled", 0)
            try:
                return _orig(*a, **k)
            finally:
                api.ticks.append({"tick": _name, "t": P.CLK.off, "cycle": api.cycle_no, "w0": w0,
                                  "writes": len(api.wall) - n0, "orders": len(api.log_orders) - o0,
                                  "pulled": (getattr(b, "hv_totals", None) or {}).get("pulled", 0) - pulled0,
                                  "kinds": Counter(k_ for _, k_, _ in api.wall[n0:])})
        setattr(b, name, acct)


P.DryApi = Dry13
P.tag_methods = tag_methods13


# ============================================================================================ helpers
def pval(b, e):
    """The value-mode p of a market (liquid, race-scaled Polymarket), as Bot.alloc_p sees it in a cycle."""
    ex = b.ex.get(e) if b is not None else None
    if ex is None or e not in (b.cur_liquid or ()) or (b.cur_refs or {}).get(e) is None:
        return None
    return b.scaled_ref(ex)


def touch(api, e):
    bk = api.books.get(e) or {}
    bb = (bk.get("bids") or [{}])[0].get("price")
    ba = (bk.get("asks") or [{}])[0].get("price")
    return bb, ba


def resting(api):
    """Our resting orders in YES terms: [(oid, eid, is_bid, price, qty)]."""
    res = []
    for oid, o in list(api.orders.items()):
        yb, yp = api.yes_view(o)
        res.append((oid, o["exchangeId"], yb, yp, o.get("quantity") or 0))
    return res


def log_since(n0, needle):
    return [(t, m) for t, _, m in P.CAP.lines[n0:] if needle in m]


def crashes(n0):
    return [m for t, lv, m in P.CAP.lines[n0:] if lv in ("ERROR", "CRITICAL") or "tick failed" in m]


def write_file(d, name):
    path = os.path.join(tempfile.mkdtemp(prefix="p13dry"), name)
    with open(path, "w") as f:
        json.dump(d, f)
    return path


def staged():
    return json.load(open(STAGED))


def base_file():
    d = json.load(open(os.path.join(P.SNAP, "settings_override.json")))
    d.update(BASE_EXTRA)
    return d


def build(overrides, seed=7, cash_add=0.0):
    random.seed(seed)
    api, b = P.build(S, overrides=overrides)
    api.bot = b
    api.cash += cash_add
    return api, b


def run(b, n, step=60.0, stage=None, refill_hours=False, hook=None, refill_s=3600.0):
    """n cycles; the books refilled to their base depth at every refill_s boundary of simulated time (refill_hours)."""
    api = b.api
    for _ in range(n):
        if hook:
            hook()
        t_prev = P.CLK.off
        P.cycles(b, 1, step=step, stage=stage)
        if refill_hours and int((P.CLK.off - STATE["t_epoch"]) // refill_s) != \
                int((t_prev - STATE["t_epoch"]) // refill_s):
            api.refill()


def minute_writes(api, t0=None, t1=None, quote=True):
    """Max writes in any 60-s simulated window ending at a cycle time (all features, or without the quoter's)."""
    ts = sorted({t for t, _, _ in api.wall if (t0 is None or t >= t0) and (t1 is None or t <= t1)})
    best, at = 0, None
    for t in ts:
        n = sum(1 for tt, _, g in api.wall if t - 60 + TOL < tt <= t + TOL and (quote or g != "quote"))
        if n > best:
            best, at = n, t
    return best, at


def coll_snapshot(b):
    """{eid: $ of collateral of the position at p (the bot's own p13 valuation)}."""
    inv = dict(b.api.inv)
    return {e: (b.p13_pos_usd(float(inv.get(e, 0.0)), b.p13_px(ex)) if abs(float(inv.get(e, 0.0))) >= 1 else 0.0)
            for e, ex in b.ex.items()}


def orders_in(api, t0, t1, tag=None):
    return [o for o in api.log_orders if t0 - TOL <= o["t"] < t1 - TOL and (tag is None or o["tag"] == tag)]


def tick_share_violations(api, t0=None):
    """The write-budget shares: p13_tick orders <= floor(bucket_writes_frac x writes left / 3); hv_tick placements /
    re-quote cancels (pulls excepted) <= harvest_writes_frac x writes left + 1 (the last batch's rounding)."""
    cfg, bad = STATE["cfg"], []
    for r in api.ticks:
        if t0 is not None and r["t"] < t0:
            continue
        if r["tick"] == "p13_tick" and r["orders"] > int(cfg.bucket_writes_frac * max(0, r["w0"]) / 3 + 1e-9):
            bad.append(("p13", r["cycle"], r["w0"], r["orders"]))
        if r["tick"] == "hv_tick" and r["writes"] - r["pulled"] > cfg.harvest_writes_frac * max(0, r["w0"]) + 1 + TOL:
            bad.append(("harvest", r["cycle"], r["w0"], r["writes"], r["pulled"]))
    return bad


def ladder_view(b, api):
    """Our resting harvest levels: {eid: [(oid, is_bid, price, qty, edge at p, collateral locked)]}."""
    meta, res = b.order_meta, defaultdict(list)
    for oid, e, is_bid, px, q in resting(api):
        m = meta.get(oid) or {}
        if not m.get("harvest"):
            continue
        p = pval(b, e)
        edge = None if p is None else ((p - px) / max(px, M.TICK) if is_bid else (px - p) / max(1 - px, M.TICK))
        held = max(0.0, api.inv.get(e, 0.0))
        lock = (0.0 if m.get("no_sell") else px * q) if is_bid else (1 - px) * max(0.0, q - held)
        res[e].append((oid, is_bid, px, q, edge, lock))
    return res


def ladder_checks(b, api, label):
    lad = ladder_view(b, api)
    cfg = b.cfg
    cross, own_cross, low_edge, dup = [], [], [], []
    for e, lv in lad.items():
        bb, ba = touch(api, e)
        mine = [(is_bid, px) for oid, _, is_bid, px, q in resting(api) if _ == e]
        for oid, is_bid, px, q, edge, lock in lv:
            if (not is_bid and bb is not None and px <= bb + TOL) or (is_bid and ba is not None and px >= ba - TOL):
                cross.append((b.ex[e].label, "bid" if is_bid else "ask", px, bb, ba))
            if any(ib != is_bid and ((not is_bid and opx >= px - TOL) or (is_bid and opx <= px + TOL))
                   for ib, opx in mine):
                own_cross.append((b.ex[e].label, "bid" if is_bid else "ask", px))
            if edge is None or edge < cfg.harvest_min_edge - 1e-9:
                low_edge.append((b.ex[e].label, px, edge))
        prices = Counter((is_bid, round(px, 3)) for _, is_bid, px, _, _, _ in lv)
        dup += [(b.ex[e].label, k) for k, v in prices.items() if v > 1]
    coll = coll_snapshot(b)
    over_m, over_r = [], []
    locks = {e: sum(x[5] for x in lv) for e, lv in lad.items()}
    other_locks = defaultdict(float)              # our other resting orders (the bot's view of them)
    for o in list(b.my_orders.values()):
        if not (b.order_meta.get(o.order_id) or {}).get("harvest"):
            other_locks[o.eid] += b.resting_lock(o)
    for e in lad:
        tot = coll[e] + locks[e]
        if tot > max(cfg.aggr_max_market_usd, coll[e]) + 1.0:
            over_m.append((b.ex[e].label, round(coll[e]), round(locks[e])))
    races = {b.ex[e].group for e in lad}
    for r in races:
        tot = sum(coll[m] + locks.get(m, 0.0) + other_locks.get(m, 0.0) for m in b.groups[r])
        pos = sum(coll[m] for m in b.groups[r])
        if tot > max(cfg.aggr_max_race_usd, pos) + 1.0:
            over_r.append((r, round(pos), round(tot)))
    check(f"{label}: no harvest level at / through other traders' book", not cross, cross[:4])
    check(f"{label}: no harvest level at / through our own orders on that market", not own_cross, own_cross[:4])
    check(f"{label}: every resting harvest level clears harvest_min_edge {cfg.harvest_min_edge:g} at today's p",
          not low_edge, low_edge[:4])
    check(f"{label}: no duplicate harvest level (same market, side, price)", not dup, dup[:4])
    check(f"{label}: per market position + ladder collateral <= aggr_max_market_usd {cfg.aggr_max_market_usd:,.0f} (or "
          f"the position alone, held before)", not over_m, over_m[:4])
    check(f"{label}: per race positions + resting collateral <= aggr_max_race_usd {cfg.aggr_max_race_usd:,.0f}",
          not over_r, over_r[:4])
    return lad


def dup_resting(api):
    """Two resting orders of ours on one market, same side and price (a duplicate)."""
    c = Counter((e, is_bid, round(px, 3)) for _, e, is_bid, px, _ in resting(api))
    return [k for k, v in c.items() if v > 1]


# ============================================================================================ scenario 0
def base_module(rev):
    try:
        src = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:mm_bot.py"], capture_output=True, text=True,
                             timeout=30)
        if src.returncode != 0:
            return None
        path = os.path.join(tempfile.mkdtemp(), f"mm_bot_{rev}.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location(f"mm_bot_{rev}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.alert, mod.notify, mod.time = P.M.alert, (lambda *a, **k: False), P.CLK
        mod.utcnow = P.M.utcnow
        if P.CAP not in mod.log.handlers:
            mod.log.addHandler(P.CAP)
        mod.log.propagate = False
        return mod
    except Exception as e:                        # (reported as a failure)
        print("    (base module unavailable:", e, ")")
        return None


VOLATILE = re.compile(r"(^|_)(at|ts|time|updated|wall|started|since|last_.*|.*_utc|.*_wall|now|saved|uptime.*|"
                      r"generated|written|age.*|.*_age|.*seconds.*|.*_s)$")


def strip_volatile(x):
    if isinstance(x, dict):
        return {k: strip_volatile(v) for k, v in x.items() if not VOLATILE.search(str(k))}
    if isinstance(x, list):
        return [strip_volatile(v) for v in x]
    if isinstance(x, str) and re.match(r"\d{4}-\d\d-\d\dT", x):
        return "<time>"
    if isinstance(x, float):                      # (wall-clock epochs inside lists; the tilt estimator's last digits
        return "<time>" if 1.5e9 < x < 2.5e9 else round(x, 4)   # follow the real clock under the simulated one)
    return x


def s0_run(mod, tag, path):
    keep_m, keep_off = P.M, P.CLK.off
    P.M = mod
    try:
        api, b = build(path, seed=3)
        api.limit = False                         # (as P10's stage 0: the write limiter would add thread timing)
        P.cycles(b, 12, stage=tag)
        sent = sorted((o["cycle"], o["eid"], o["yes_buy"], o["yes_price"], o["qty"], o["tag"]) for o in
                      P.orders(api, tag))
        return sent, P.status_of(b), api
    finally:
        P.M = keep_m
        P.CLK.off = keep_off


def scenario0():
    out("## Scenario 0: base = the live file + reserve 20k, backstop 0.90, mm_risk_reserve 5k / 4k; every P13 flag off")
    path = write_file(base_file(), "base.json")
    raw = json.load(open(path))
    check("s0: the base file has every Package 13 flag off (absent = the default False)",
          not any(raw.get(k) for k in P13_FLAGS))
    new, st_new, api_new = s0_run(M, "s0-new", path)
    check("s0: no Package 13 order and no Package 13 status section with the flags off",
          not any(o[5] in P13_TAGS for o in new) and not any(k in st_new for k in P13_STATUS),
          ([o for o in new if o[5] in P13_TAGS][:3], [k for k in P13_STATUS if k in st_new]))
    ev_ign = set(getattr(M.Bot, "EV_KEYS", ())) | {"ev_outcome_history"}
    for rev, what in BASE_REVS:
        mod = base_module(rev)
        check(f"s0: {rev} ({what}) loaded for the comparison", mod is not None)
        if mod is None:
            continue
        old, st_old, _ = s0_run(mod, f"s0-{rev}", path)
        same = [x[:5] + (FEATURES.get(x[5], x[5]),) for x in new] == \
            [x[:5] + (FEATURES.get(x[5], x[5]),) for x in old]
        check(f"s0: identical orders to {rev} on the live seed (12 cycles: every order, price, size, feature)", same,
              (len(new), len(old), [x for x in new if x not in old][:3], [x for x in old if x not in new][:3]))
        a = {k: v for k, v in strip_volatile(st_new).items() if k not in ev_ign}
        c = {k: v for k, v in strip_volatile(st_old).items() if k not in ev_ign}
        diff = sorted(k for k in set(a) | set(c) if a.get(k) != c.get(k))
        check(f"s0: identical status.json to {rev} (every key and value, timestamps aside)", not diff, diff[:6])
        out(f"- vs {rev} ({what}): {len(new)} orders each over 12 cycles, identical: {same}; status.json identical "
            f"(timestamps aside): {not diff}")
    api_l, b_l = build(path, seed=3)              # the base with the write limiter: 1 h of 60-s cycles
    STATE["t_epoch"] = P.CLK.off
    t0 = P.CLK.off
    run(b_l, 60, stage="s0-limited", refill_hours=True)
    mw, _ = minute_writes(api_l, t0 + 300)
    mw_nq, _ = minute_writes(api_l, t0 + 300, quote=False)
    check(f"s0: base with the write limiter: writes of every feature but the quoter <= {WPM} a simulated minute",
          mw_nq <= WPM, mw_nq)
    STATE["s0_wpm"] = (mw, mw_nq)
    out(f"- state: reduce-only {st_new['reduce_only']}, worst case {st_new['worst_case_loss']:,.0f}, cash gate left "
        f"{st_new.get('cash_gate_left')}, EV outcome {st_new.get('ev_outcome'):,.0f}")
    out(f"- the base with the 28/min write limiter, 1 h of 60-s cycles (after the first 5 min): most writes in a "
        f"simulated minute {mw} (all) / {mw_nq} (without the quoter): the quoter's urgent pulls exceed the budget "
        "before Package 13 too")
    out()


# ============================================================================================ scenario 1
def bucket_row(b, st, h):
    bk = st.get("buckets") or {}
    eq = bk.get("account") or b.last_equity or 1
    tg = bk.get("targets") or {"mm": b.cfg.bucket_mm_frac, "value": b.cfg.bucket_value_frac,
                               "momentum": b.cfg.bucket_mom_frac}
    hv = st.get("harvest") or {}
    return dict(h=h, mm=bk.get("mm", 0.0), value=bk.get("value", 0.0), mom=bk.get("momentum", 0.0),
                cash=bk.get("cash"), eq=eq, tg=tg, cls=dict(bk.get("classification") or {}),
                hv_coll=hv.get("collateral_resting", 0.0), hv_lv=hv.get("levels_resting", 0), ev=st.get("ev_outcome"),
                api_cash=b.api.cash)


def hour0_row(b):
    """Hour 0 (the base file running, P13 flags off): the buckets and the classification as the staged file would see
    them now (the bot's own pure functions)."""
    now_m = M.time.monotonic()
    b.alloc_ages = {}
    inv = dict(b.api.inv)
    cls = b.p13_classify(inv, now_m)
    mm, value, mom, cash = b.bucket_values(inv, now_m, cls)
    st = P.status_of(b)
    return dict(h=0, mm=mm, value=value, mom=mom, cash=cash, eq=b.last_equity, cls=b.p13_count(cls),
                tg={"mm": 0.2, "value": 0.4, "momentum": 0.4}, hv_coll=0.0, hv_lv=0, ev=st.get("ev_outcome"),
                api_cash=b.api.cash)


def bucket_table(rows):
    out("| hour | MM $ (target) | VALUE $ (target) | MOMENTUM $ (target) | cash left (gate) | fake cash | harvest "
        "resting $ (levels) | account | EV outcome |")
    out("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        eq, tg = r["eq"] or 1, r["tg"]
        out(f"| {r['h']} | {r['mm']:,.0f} ({tg['mm'] * eq:,.0f}) | {r['value']:,.0f} ({tg['value'] * eq:,.0f}) | "
            f"{r['mom']:,.0f} ({tg['momentum'] * eq:,.0f}) | {fmt(r['cash'])} | {r['api_cash']:,.0f} | "
            f"{r['hv_coll']:,.0f} ({r['hv_lv']}) | {eq:,.0f} | {fmt(r['ev'])} |")
    out()


def fmt(x):
    return "-" if x is None else f"{x:,.0f}"


SOLD_RE = re.compile(r"BUCKETS sold (.+?) (NO )?(\d+) @ ([0-9.]+) \(edge-held (-?[0-9.]+)%, \$([0-9.]+)")
BOUGHT_RE = re.compile(r"MOMENTUM bought (.+?) (NO \(short YES\) )?(\d+) @ ([0-9.]+) \(\$([0-9.]+); gap (-?[0-9.]+)c")


def sales_and_buys(n0, t0, t1):
    sold, bought = [], []
    for t, lv, m in P.CAP.lines[n0:]:
        if not (t0 - TOL <= t < t1 - TOL):
            continue
        x = SOLD_RE.search(m)
        if x:
            sold.append(dict(label=x.group(1), no=bool(x.group(2)), qty=int(x.group(3)), px=float(x.group(4)),
                             edge=float(x.group(5)), usd=float(x.group(6))))
        x = BOUGHT_RE.search(m)
        if x:
            bought.append(dict(label=x.group(1), no=bool(x.group(2)), qty=int(x.group(3)), px=float(x.group(4)),
                               usd=float(x.group(5)), gap=float(x.group(6))))
    return sold, bought


def sell_checks(b, api, stage, label):
    bad = []
    pins = b.alloc_pins()
    for o in P.orders(api, stage, "bucket_sell"):
        pos = o["pos_before"]
        shrink = (pos >= 1 and not o["yes_buy"]) or (pos <= -1 and o["yes_buy"])
        ex = b.ex[o["eid"]]
        if not shrink or o["qty"] > abs(pos) + TOL:
            bad.append(("not a shrink / flips", o["label"], pos, o["qty"]))
        if ex.label in pins or ex.group in b.cfg.headline_races:
            bad.append(("pinned / headline", o["label"]))
        if o["eid"] in b.mom_legs:
            bad.append(("a sleeve leg", o["label"]))
        p = o.get("p")
        if p is not None and b.cfg.mom_max_p < p < 1 - b.cfg.mom_max_p:
            bad.append(("middle band (MM inventory)", o["label"], round(p, 3)))
        if pos <= -1 and o["side"] != "no":
            bad.append(("short bought back as a YES purchase", o["label"]))
    check(f"{label}: every sell-down order shrinks a VALUE position (no flip; never pinned / headline / sleeve / "
          "middle band; shorts as covered NO sales)", not bad, bad[:4])
    xs = sorted((o["t"], -o["cash"] if o["cash"] < 0 else o["cash"]) for o in P.orders(api, stage, "bucket_sell"))
    worst = max([sum(v for tt, v in xs[i:] if tt - t < 3600) for i, (t, _) in enumerate(xs)] or [0.0])
    check(f"{label}: sell-down proceeds <= bucket_turnover_per_hour {b.cfg.bucket_turnover_per_hour:,.0f} in any "
          "rolling hour", worst <= b.cfg.bucket_turnover_per_hour + 1, worst)
    return worst


def buy_checks(b, api, stage, label):
    """Every momentum buy: a MOMENTUM classification (never a VALUE short: p <= value_extreme_p or a short's edge per $
    >= value_min_edge), never the opposite side of a position (or of the 2-leg race's other leg), never headline, a
    touch level of >= mom_min_depth, <= mom_max_markets, never a market the sell-down sold in the hour before."""
    cfg, bad = b.cfg, []
    sold_at = defaultdict(list)
    for o in P.orders(api, None, "bucket_sell"):
        if o["traded"] >= 1:
            sold_at[o["eid"]].append(o["t"])
    legs = set()
    for o in P.orders(api, stage, "momentum"):
        ex = b.ex[o["eid"]]
        legs.add(o["eid"])
        if ex.group in cfg.headline_races and not cfg.mom_headline:
            bad.append(("headline", o["label"]))
        pos = o["pos_before"]
        others = {m: q for m, q in (o.get("race_pos") or {}).items() if m != o["eid"]}
        if o["yes_buy"]:
            lon, ask = o["eid"], (o["top"] or (None,))[0]
            if pos <= -1 or any(q >= 1 for q in others.values()):
                bad.append(("opposite side", o["label"], pos, others))
        else:                                     # the favourite-NO route: its longshot is the race's other leg
            lon = next(iter(others), None)
            ask = touch(api, lon)[1] if lon else None
            if pos >= 1 or any(q <= -1 for q in others.values()):
                bad.append(("opposite side", o["label"], pos, others))
        p = pval(b, lon) if lon else None
        if p is not None:
            edge = (ask - p) / max(1 - ask, M.TICK) if ask is not None else None
            if p <= cfg.value_extreme_p + 1e-12 or (edge is not None and edge >= cfg.value_min_edge - 1e-9):
                bad.append(("a VALUE short market", o["label"], round(p, 4), edge))
            if p > cfg.mom_max_p + 1e-12:
                bad.append(("not a longshot race", o["label"], round(p, 4)))
        if o["top"] is None or o["top"][1] < cfg.mom_min_depth:
            bad.append(("touch level < mom_min_depth", o["label"], o["top"]))
        if any(0 <= o["t"] - t < 3600 for t in sold_at.get(o["eid"], ())):
            bad.append(("bought back what the sell-down sold this hour", o["label"]))
    check(f"{label}: every momentum buy is a MOMENTUM market (never a VALUE short), never the opposite side of a "
          "position, never headline, a touch level >= mom_min_depth, never a market the sell-down sold this hour",
          not bad, bad[:4])
    check(f"{label}: sleeve markets <= mom_max_markets {cfg.mom_max_markets}", len(b.mom_legs) <= cfg.mom_max_markets,
          len(b.mom_legs))


def scenario1():
    out("## Scenario 1: the staged file (everything on), 2 h of 60-s cycles, books refilled to the snapshot depth "
        "hourly")
    api, b = build(write_file(base_file(), "base.json"))
    P.cycles(b, 4, step=60.0, stage="s1-warm")
    STATE["cfg"] = b.cfg
    rows = [hour0_row(b)]
    bad, al = P.apply_stage(b, STAGED)
    check("s1: the staged file applied through check_overrides with no refused key / alert", not bad and not al,
          (bad, al))
    check("s1: every Package 13 flag on", all(getattr(b.cfg, k) for k in P13_FLAGS))
    n0, t0 = len(P.CAP.lines), P.CLK.off
    STATE["t_epoch"] = t0
    sold_h, bought_h, feats_h, wpc_h = [], [], [], []
    ev0 = rows[0]["ev"]
    for h in (1, 2):
        th = P.CLK.off
        run(b, 60, stage="s1", refill_hours=True)
        st = P.status_of(b)
        rows.append(bucket_row(b, st, h))
        s, bo = sales_and_buys(n0, th, P.CLK.off)
        sold_h.append(s)
        bought_h.append(bo)
        feats_h.append(Counter(o["tag"] for o in orders_in(api, th, P.CLK.off)))
        wpc = Counter(t for t, _, _ in api.wall if th - TOL <= t < P.CLK.off - TOL)
        wpc_h.append((max(wpc.values() or [0]), sum(wpc.values()) / 60.0))
    st = P.status_of(b)
    cr = crashes(n0)
    check("s1: no Package 13 tick crashed, no ERROR in the log (2 h)", not cr, cr[:3])
    runs = log_since(n0, "BUCKETS run:")
    check("s1: the hourly rebalance ran twice in 2 h", len(runs) == 2, len(runs))
    check("s1: status.json has buckets / aggressive / momentum / harvest / election",
          all(isinstance(st.get(k), dict) for k in P13_STATUS), [k for k in P13_STATUS if not st.get(k)])
    eq = rows[-1]["eq"]
    d0 = abs(rows[0]["value"] / rows[0]["eq"] - 0.40)
    d2 = abs(rows[-1]["value"] / eq - 0.40)
    check("s1: VALUE moves toward its 40% target (|deviation| smaller after 2 h)", d2 < d0, (d0, d2))
    check("s1: VALUE within target + band after the second rebalance's sell-down (or the planned sales are done)",
          rows[-1]["value"] <= (0.40 + b.cfg.bucket_band) * eq + 1500 or not b.bk_sell,
          (rows[-1]["value"], (0.45) * eq))
    worst_turn = sell_checks(b, api, "s1", "s1")
    ag = st.get("aggressive") or {}
    check("s1: status aggressive reports on / would_reduce_corr / would_reduce_backstop",
          ag.get("on") is True and isinstance(ag.get("would_reduce_corr"), bool)
          and isinstance(ag.get("would_reduce_backstop"), bool), ag)
    cls = (st.get("buckets") or {}).get("classification") or {}
    check("s1: the classification covers every market with a fresh p and book (value / momentum / mm / none)",
          sum(cls.values()) > 150 and cls.get("value_short", 0) > 0 and cls.get("mm", 0) > 0, cls)
    shares = tick_share_violations(api, t0)
    check("s1: per cycle P13 orders <= bucket_writes_frac x writes left / 3; harvest writes <= harvest_writes_frac x "
          "writes left (+1, pulls aside)", not shares, shares[:4])
    mw_nq, at_nq = minute_writes(api, t0, quote=False)
    mw, at = minute_writes(api, t0)
    check(f"s1: writes of every feature but the quoter <= {WPM} in any simulated minute", mw_nq <= WPM, (mw_nq, at_nq))
    lad = ladder_checks(b, api, "s1 (end)")
    crossed_at_send = [o["label"] for o in P.orders(api, "s1", "harvest") if o["traded"] > 0]
    check("s1: no harvest level crossed the book when placed (no taker fill at placement)", not crossed_at_send,
          crossed_at_send[:4])
    dups = dup_resting(api)
    check("s1: no duplicate resting order of ours (market, side, price)", not dups, dups[:4])
    check("s1: EV outcome reported at the start and the end", rows[0]["ev"] is not None and st.get("ev_outcome")
          is not None, (rows[0]["ev"], st.get("ev_outcome")))
    STATE["s1"] = dict(rows=rows, sold=sold_h, bought=bought_h, feats=feats_h, wpc=wpc_h, mw=mw, mw_nq=mw_nq,
                       lad=lad, st=st, worst_turn=worst_turn, b=b, api=api, runs=[m for _, m in runs], ev0=ev0)
    report_s1()


def ladder_table(b, api, lad, n=25):
    out("| market | side | p | levels (price x shares, edge per $) | collateral $ |")
    out("|---|---|---|---|---|")
    rows = sorted(lad.items(), key=lambda kv: -sum(x[5] for x in kv[1]))
    for e, lv in rows[:n]:
        p = pval(b, e)
        out(f"| {b.ex[e].label} | {'bid' if lv[0][1] else 'ask'} | {p:.3f} | " +
            ", ".join(f"{px:.3f} x {q:,.0f} ({100 * ed:.1f}%)" for _, _, px, q, ed, _ in sorted(lv, key=lambda x: x[2]))
            + f" | {sum(x[5] for x in lv):,.0f} |")
    if len(rows) > n:
        out(f"| ... {len(rows) - n} more | | | | |")
    out()


def report_s1():
    s = STATE["s1"]
    b, api, st = s["b"], s["api"], s["st"]
    out("Collateral per bucket (status.json \"buckets\"; hour 0 = the base file running, the staged file's own "
        "valuation; MM = alloc_mm_reserve cash capped by the gate's cash left + middle-band inventory at p; VALUE = "
        "every other position at p; MOMENTUM = the sleeve at cost; harvest resting $ = cash the ladder's resting "
        "levels lock, counted in no bucket):")
    out()
    bucket_table(s["rows"])
    out("Classification (status buckets.classification; value_short = longshot shorts kept / added as value, "
        "momentum_long = momentum candidates, value_long = favourites with >= 8% edge, mm = the middle band, none = "
        "the rest): " + "; ".join(f"hour {r['h']}: " + ", ".join(f"{k} {v}" for k, v in sorted(r["cls"].items()))
                                  for r in s["rows"]))
    out()
    for m in s["runs"]:
        out(f"    {m}")
    out()
    for h in (1, 2):
        sold, bought = s["sold"][h - 1], s["bought"][h - 1]
        out(f"### Hour {h}")
        out(f"- sell-down: {len(sold)} sales, ${sum(x['usd'] for x in sold):,.0f} of proceeds; momentum: {len(bought)} "
            f"buys, ${sum(x['usd'] for x in bought):,.0f}")
        if sold:
            out()
            out("| sold | qty | price | edge-held | proceeds $ |")
            out("|---|---|---|---|---|")
            agg = {}
            for x in sold:
                k = (x["label"], x["no"])
                a = agg.setdefault(k, dict(qty=0, usd=0.0, px=[], edge=x["edge"]))
                a["qty"] += x["qty"]
                a["usd"] += x["usd"]
                a["px"].append(x["px"])
            for (lab, no), a in sorted(agg.items(), key=lambda kv: kv[1]["edge"]):
                out(f"| {lab}{' (NO bought back)' if no else ''} | {a['qty']:,} | "
                    f"{min(a['px']):.3f}-{max(a['px']):.3f} | {a['edge']:.1f}% | {a['usd']:,.0f} |")
            out()
        f = s["feats"][h - 1]
        out("- orders by feature: " + ", ".join(f"{k} {v}" for k, v in sorted(f.items())))
        out(f"- writes per 60-s cycle: max {s['wpc'][h - 1][0]}, mean {s['wpc'][h - 1][1]:.1f}")
        out()
    hv = st.get("harvest") or {}
    out(f"### The harvest ladder after 2 h: {len(s['lad'])} markets, {sum(len(v) for v in s['lad'].values())} levels "
        f"resting, ${sum(x[5] for v in s['lad'].values() for x in v):,.0f} of collateral (status: markets "
        f"{hv.get('markets')}, levels {hv.get('levels_resting')}, ${hv.get('collateral_resting', 0):,.0f}; planned "
        f"{hv.get('planned_markets')} markets / ${hv.get('planned_collateral', 0):,.0f} if the cash allowed; placed "
        f"{hv.get('placed')}, pulled {hv.get('pulled')})")
    out()
    ladder_table(b, api, s["lad"])
    ag = st.get("aggressive") or {}
    out(f"- aggressive: on {ag.get('on')}, would_reduce_corr {ag.get('would_reduce_corr')}, would_reduce_backstop "
        f"{ag.get('would_reduce_backstop')}; reduce-only {st.get('reduce_only')}; worst case "
        f"{st.get('worst_case_loss'):,.0f} vs the 1.0 tripwire x account {b.last_equity:,.0f}")
    out(f"- EV outcome: {s['ev0']:,.0f} at hour 0 -> {st.get('ev_outcome'):,.0f} after 2 h")
    out(f"- writes: most in a simulated minute {s['mw']} (every feature) / {s['mw_nq']} (without the quoter); "
        f"sell-down proceeds in the worst rolling hour ${s['worst_turn']:,.0f}")
    out()


# ============================================================================================ scenario 1F: the sleeve
def fixture(tag):
    """The funded-sleeve fixture: hour 1 = the staged file with the harvest ladder, the allocator and the stale-quote
    takes OFF and +45k of cash (momentum gets the cash first); hour 2 = the staged file as it is."""
    api, b = build(write_file(base_file(), "base.json"), cash_add=45000.0)
    P.cycles(b, 4, step=60.0, stage=f"{tag}-warm")
    d = staged()
    d.update(tilt_harvest_ladder=False, alloc_enabled=False, take_enabled=False)
    P.apply_stage(b, write_file(d, "momfirst.json"))
    STATE["t_epoch"] = P.CLK.off
    t0, n0 = P.CLK.off, len(P.CAP.lines)
    run(b, 60, stage=tag, refill_hours=True)
    P.apply_stage(b, STAGED)
    run(b, 60, stage=tag, refill_hours=True)
    return api, b, t0, n0


def scenario1f():
    out("## Scenario 1F: the funded-sleeve fixture (what the 40% momentum bucket buys when it gets the cash)")
    api, b, t0, n0 = fixture("s1f")
    st = P.status_of(b)
    cr = crashes(n0)
    check("s1F: no tick crashed / no ERROR", not cr, cr[:3])
    mo = st.get("momentum") or {}
    check("s1F: the sleeve bought (momentum legs held)", len(b.mom_legs) > 0 and mo.get("cost", 0) > 0,
          (len(b.mom_legs), mo.get("cost")))
    buy_checks(b, api, "s1f", "s1F")
    shares = tick_share_violations(api, t0)
    check("s1F: the write-budget shares hold every cycle", not shares, shares[:4])
    mw_nq, _ = minute_writes(api, t0, quote=False)
    check(f"s1F: writes of every feature but the quoter <= {WPM} in any simulated minute", mw_nq <= WPM, mw_nq)
    sold, bought = sales_and_buys(n0, t0, P.CLK.off)
    STATE["s1f"] = dict(st=st, bought=bought, sold=sold, mw_nq=mw_nq, mw=minute_writes(api, t0)[0])
    out(f"- hour 1: staged file with tilt_harvest_ladder / alloc_enabled / take_enabled false and +45,000 cash given "
        f"to the fake; hour 2: the staged file. Sleeve after 2 h: {mo.get('markets')} legs, cost "
        f"${mo.get('cost', 0):,.0f} (target ${mo.get('target', 0):,.0f}), mark ${mo.get('value_mark', 0):,.0f}, at p "
        f"${mo.get('value_outcome', 0):,.0f}; state {mo.get('state')}")
    out()
    out("| momentum buy | side | qty | price | p (longshot) | gap | $ |")
    out("|---|---|---|---|---|---|---|")
    for o in P.orders(api, "s1f", "momentum"):
        if o["traded"] < 1:
            continue
        lon = o["eid"] if o["yes_buy"] else next(iter(m for m in (o.get("race_pos") or {}) if m != o["eid"]), None)
        p = pval(b, lon) if lon else None
        gap = next((x["gap"] for x in bought if x["label"] == o["label"]), None)
        out(f"| {o['label']} | {'YES' if o['yes_buy'] else 'NO (sell YES)'} | {o['traded']:,.0f} | "
            f"{o['yes_price']:.3f} | {'-' if p is None else f'{p:.3f}'} | {'-' if gap is None else f'{gap:+.1f}c'} | "
            f"{-o['cash']:,.0f} |")
    out()
    out(f"- sell-down in these 2 h: {len(sold)} sales, ${sum(x['usd'] for x in sold):,.0f}; writes: most in a minute "
        f"{STATE['s1f']['mw']} (all) / {mw_nq} (without the quoter)")
    out()
    return api, b


# ============================================================================================ scenario 2: tilt rise
def shift_books(api, b, step):
    """Longshots (p < 0.5): every bid and ask +step; favourites: every bid and ask -step (the tilt rises)."""
    for e, bk in api.books.items():
        p = pval(b, e)
        if p is None:
            continue
        d = step if p < 0.5 else -step
        for side in ("bids", "asks"):
            for lv in bk.get(side) or []:
                lv["price"] = round(min(0.999, max(0.001, lv["price"] + d)), 3)
    api.base_books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                      for e, v in api.books.items()}


def scenario2(api, b):
    out("## Scenario 2: a +3c tilt rise over 1 h (longshot books +1c, favourite books -1c at 0, 20 and 40 min)")
    st0 = P.status_of(b)
    mo0, ev0 = st0.get("momentum") or {}, st0.get("ev_outcome")
    n0, t0, f0, a0 = len(P.CAP.lines), P.CLK.off, len(api.maker_fills), len(api.fills)
    lad0 = ladder_view(b, api)
    for k in range(3):
        shift_books(api, b, 0.01)
        api.match_resting()
        run(b, 20, stage="s2", hook=api.match_resting)
    st = P.status_of(b)
    mo, ev1 = st.get("momentum") or {}, st.get("ev_outcome")
    cr = crashes(n0)
    check("s2: no tick crashed / no ERROR", not cr, cr[:3])
    fills = api.maker_fills[f0:]
    hv_f = [f for f in fills if (b.order_meta.get(f["oid"]) or {}).get("harvest")]
    lines = log_since(n0, "HARVEST fill")
    check("s2: the ladder's resting levels filled as the tilt rose (maker fills)", len(hv_f) > 0, len(hv_f))
    all_hv = [f for f in api.fills[a0:] if (b.order_meta.get(f["orderId"]) or {}).get("harvest")]
    check("s2: every harvest fill logged 'HARVEST fill ...' (journal)", len(lines) == len(all_hv), (len(lines),
                                                                                                   len(all_hv)))
    crossed_at_send = [o["label"] for o in orders_in(api, t0, P.CLK.off + 1, "harvest") if o["traded"] > 0]
    check("s2: no harvest level crossed the book when placed (no taker fill at placement, books moving)",
          not crossed_at_send, crossed_at_send[:4])
    low = []
    coll = edge_usd = 0.0
    for f in hv_f:
        m = b.order_meta.get(f["oid"]) or {}
        p = m.get("p")
        c = f["qty"] * (f["px"] if f["bid"] else 1 - f["px"])
        e = f["qty"] * ((p - f["px"]) if f["bid"] else (f["px"] - p)) if p is not None else 0.0
        coll += c
        edge_usd += e
        if p is None or e / max(c, TOL) < b.cfg.harvest_min_edge - 1e-6:
            low.append((b.ex[f["eid"]].label, f["px"], p))
    check("s2: every harvest fill at >= harvest_min_edge per $ vs p at placement", not low, low[:4])
    check("s2: the sleeve's mark value ROSE with the tilt", mo.get("value_mark", 0) > mo0.get("value_mark", 0),
          (mo0.get("value_mark"), mo.get("value_mark")))
    check("s2: harvest fills are VALUE positions (never a sleeve leg)", not any(f["eid"] in b.mom_legs for f in hv_f))
    lad = ladder_checks(b, api, "s2 (after the rise)")
    hv_st = st.get("harvest") or {}
    STATE["s2"] = dict(n=len(hv_f), shares=sum(f["qty"] for f in hv_f), coll=coll, edge=edge_usd,
                       mark0=mo0.get("value_mark", 0), mark1=mo.get("value_mark", 0), cost0=mo0.get("cost", 0),
                       cost1=mo.get("cost", 0), ev0=ev0, ev1=ev1, other=len(fills) - len(hv_f))
    out(f"- harvest levels resting before the rise: {sum(len(v) for v in lad0.values())} in {len(lad0)} markets; "
        f"after: {sum(len(v) for v in lad.values())} in {len(lad)} markets")
    out(f"- harvest fills: {len(hv_f)} fills, {sum(f['qty'] for f in hv_f):,.0f} shares, ${coll:,.0f} of collateral, "
        f"edge at p ${edge_usd:,.0f} ({100 * edge_usd / max(coll, 1):.1f}% per $); status filled_24h "
        f"{hv_st.get('filled_24h')}, edge_filled_24h {hv_st.get('edge_filled_24h')}; other maker fills (the quoter's "
        f"resting quotes crossed by the moved books) {len(fills) - len(hv_f)}")
    out(f"- the sleeve: cost ${mo0.get('cost', 0):,.0f} -> ${mo.get('cost', 0):,.0f}; mark "
        f"${mo0.get('value_mark', 0):,.0f} -> ${mo.get('value_mark', 0):,.0f} "
        f"({mo.get('value_mark', 0) - mo0.get('value_mark', 0):+,.0f}); at p ${mo0.get('value_outcome', 0):,.0f} -> "
        f"${mo.get('value_outcome', 0):,.0f} (Polymarket flat)")
    out(f"- EV outcome {ev0:,.0f} -> {ev1:,.0f} ({ev1 - ev0:+,.0f}: the harvest fills' edge at p; Polymarket is flat, "
        "so the sleeve's mark gain is not in it)")
    for _, m in lines[:8]:
        out(f"    {m}")
    out()


# ============================================================================================ scenario 3: the kill
def depress_sleeve(api, b, frac=0.30):
    """The sleeve's books moved against it: a long YES leg's prices x (1 - frac), a short YES (NO) leg's 1 - price x
    (1 - frac)."""
    for e, leg in b.mom_legs.items():
        bk = api.books.get(e) or {}
        for side in ("bids", "asks"):
            for lv in bk.get(side) or []:
                px = lv["price"]
                lv["price"] = M.rnd(max(0.001, px * (1 - frac))) if leg["q"] > 0 else \
                    M.rnd(min(0.999, 1 - (1 - px) * (1 - frac)))
    api.base_books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                      for e, v in api.books.items()}


def scenario3():
    out("## Scenario 3: the momentum kill (the sleeve's mids -30% against it, held)")
    api, b, _, _ = fixture("s3f")
    legs0 = dict(b.mom_legs)
    st0 = P.status_of(b)
    mo0 = st0.get("momentum") or {}
    n0, t_move, a0 = len(P.CAP.lines), P.CLK.off, len(P.ALERTS)
    depress_sleeve(api, b)
    run(b, 20, step=30.0, stage="s3")             # 10 min at 30-s cycles
    st1 = P.status_of(b)
    mo1 = st1.get("momentum") or {}
    t_kill = next((t for t, m in log_since(n0, "MOMENTUM KILLED")), None)
    ex_o = P.orders(api, "s3", "mom_exit")
    check("s3: the sleeve is KILLED (state latched 'killed')", mo1.get("state") == "killed", mo1.get("state"))
    check("s3: killed only after >= 120 s below the level (not on the first cycle)",
          t_kill is not None and t_kill - t_move >= 120 - TOL, None if t_kill is None else t_kill - t_move)
    check("s3: exit orders go out (IOC into the "
          "bids)", len(ex_o) > 0 and any(o["traded"] >= 1 for o in ex_o), len(ex_o))
    kill_alerts = [a for a in P.ALERTS[a0:] if "KILLED" in a]
    t_r = P.CLK.off
    run(b, 120, stage="s3b", refill_hours=True, refill_s=600.0)    # the next 2 h (books replenished every 10 min)
    run(b, 300, stage="s3c", refill_hours=True, refill_s=600.0)    # until the 6-h exit is done (and beyond)
    st2 = P.status_of(b)
    mo2 = st2.get("momentum") or {}
    kill_alerts = [a for a in P.ALERTS[a0:] if "KILLED" in a]
    check("s3: exactly one kill alert", len(kill_alerts) == 1, kill_alerts)
    rebuys = [o for o in P.orders(api) if o["tag"] == "momentum" and o["t"] >= t_move - TOL]
    check("s3: no momentum buy after the kill (the next 2 h and the whole exit)", not rebuys, len(rebuys))
    check("s3: the latch holds ('killed' after 7 h)", mo2.get("state") == "killed", mo2.get("state"))
    check("s3: the sleeve is sold (no legs left)", not b.mom_legs, len(b.mom_legs))
    now_m = M.time.monotonic()
    b.alloc_ages = {}
    plans, why = b.hv_plan(dict(api.inv), now_m)
    excl = [b.ex[e].label for e in legs0 if why.get(e) == "momentum"]
    check("s3: the harvest ladder takes over the former sleeve markets (none excluded as 'momentum' any "
          "more)", not excl,
          excl)
    taken = [b.ex[e].label for e in legs0 if e in plans]
    lad = ladder_view(b, api)
    rest = [b.ex[e].label for e in legs0 if e in lad]
    why_not = Counter(why.get(e, "planned") for e in legs0 if e not in plans)
    exits = [(m) for _, m in log_since(n0, "MOMENTUM exit (killed) sold")]
    ex_usd = sum(-o["cash"] if o["cash"] < 0 else o["cash"] for o in P.orders(api) if o["tag"] == "mom_exit")
    STATE["s3"] = dict(legs=len(legs0), cost=mo0.get("cost", 0), mark0=mo0.get("value_mark", 0),
                       mark1=mo1.get("value_mark", 0), dt=None if t_kill is None else t_kill - t_move,
                       n_exit=len(exits),
                       usd=ex_usd, taken=taken, rest=rest, why=dict(why_not))
    out(f"- sleeve: {len(legs0)} legs, cost ${mo0.get('cost', 0):,.0f}, mark ${mo0.get('value_mark', 0):,.0f}; after "
        f"the move mark ${mo1.get('value_mark', 0):,.0f} (kill level ${mo0.get('kill_level', 0):,.0f})")
    out(f"- killed {STATE['s3']['dt']:.0f} s after the move; one alert: {kill_alerts[0] if kill_alerts else '-'}")
    out(f"- the exit: {len(exits)} sales, ${ex_usd:,.0f} received, done after "
        f"{(max([o['t'] for o in P.orders(api) if o['tag'] == 'mom_exit'] or [t_move]) - t_move) / 3600:.1f} h; no "
        f"re-buy in {(P.CLK.off - t_move) / 3600:.1f} h; state '{mo2.get('state')}'")
    out(f"- the harvest ladder afterwards: planned on {len(taken)} of the {len(legs0)} former sleeve markets, resting "
        f"on {len(rest)} ({', '.join(rest[:8]) or '-'}); the others: "
        f"" + ", ".join(f"{k} {v}" for k, v in why_not.items())
        + " (p between 0.10 and 0.90 = 'middle': not a harvest market; 'no edge': below 8% per $)")
    for m in exits[:5]:
        out(f"    {m}")
    out()


# ============================================================================================ scenario 4: manual exit
def scenario4():
    out("## Scenario 4: the manual exit (momentum_exit true through the override path)")
    api, b, _, _ = fixture("s4f")
    legs0 = {e: abs(v["q"]) for e, v in b.mom_legs.items()}
    d = staged()
    d["momentum_exit"] = True
    bad, al = P.apply_stage(b, write_file(d, "exit.json"))
    check("s4: momentum_exit true applied with no refused key / alert", not bad and not al, (bad, al))
    t0, n0 = P.CLK.off, len(P.CAP.lines)
    prog = {}
    for h in range(1, 7):
        run(b, 60, stage="s4", refill_hours=True, refill_s=600.0)
        mo = P.status_of(b).get("momentum") or {}
        prog[h] = ((mo.get("exit_progress") or {}).get("done_frac"), mo.get("state"), len(b.mom_legs))
    run(b, 5, stage="s4")
    mo = P.status_of(b).get("momentum") or {}
    prog["6+"] = ((mo.get("exit_progress") or {}).get("done_frac"), mo.get("state"), len(b.mom_legs))
    d["momentum_exit"] = False                    # the switch back off: still never re-bought
    P.apply_stage(b, write_file(d, "exit_off.json"))
    run(b, 60, stage="s4b", refill_hours=True, refill_s=600.0)
    mo_end = P.status_of(b).get("momentum") or {}
    for h, want in ((1, 1 / 6), (3, 0.5)):
        got = prog[h][0]
        check(f"s4: exit progress at {h} h on the linear schedule ({want:.2f} +- 0.08)",
              got is not None and abs(got - want) <= 0.08, got)
    check("s4: the sleeve sold by 6 h (done >= 0.97; state 'exited' once the last leg is gone)",
          (prog["6+"][0] or 0) >= 0.97 and prog["6+"][1] == "exited", prog["6+"])
    rebuys = [o for o in P.orders(api) if o["tag"] == "momentum" and o["t"] >= t0 - TOL]
    check("s4: never re-bought (6 h of exit + 1 h with momentum_exit back off)", not rebuys and mo_end.get("state")
          == "exited" and not b.mom_legs, (len(rebuys), mo_end.get("state"), len(b.mom_legs)))
    sells = P.orders(api, "s4", "mom_exit")
    flo = [o for o in sells if (o["pos_before"] > 0) == o["yes_buy"]]
    check("s4: every exit order shrinks a sleeve leg (sold into the bids / bought back as a covered NO sale)", not flo,
          flo[:3])
    usd = sum(o["cash"] for o in sells)
    STATE["s4"] = dict(prog=prog, n=len(sells), usd=usd, legs=len(legs0), cr=crashes(n0))
    check("s4: no tick crashed / no ERROR", not STATE["s4"]["cr"], STATE["s4"]["cr"][:3])
    out(f"- {len(legs0)} legs, {sum(legs0.values()):,.0f} shares; {len(sells)} exit orders, ${usd:,.0f} received")
    out("| hour | done (shares) | state | legs left |")
    out("|---|---|---|---|")
    for h, (dn, s_, n) in prog.items():
        out(f"| {h} | {'-' if dn is None else f'{dn:.3f}'} | {s_} | {n} |")
    out(f"- with momentum_exit back to false for 1 h: state '{mo_end.get('state')}', no buy")
    out()


# ============================================================================================ scenario 5: election
def set_clock(dt):
    P.CLK.off = (dt - datetime.now(timezone.utc)).total_seconds()


def pick_races(b, api):
    """3 winners and 2 losers: non-headline 2-leg races, the leg's p in 0.25-0.75, with asks below 0.95 (winner) / bids
    above 0.05 (loser) in the book; each in its own race."""
    win, lose, used = [], [], set()
    for e in sorted(b.ex, key=int):
        ex = b.ex[e]
        if ex.group in b.cfg.headline_races or ex.group in used or len(b.groups[ex.group]) != 2:
            continue
        r = (b.cur_refs or {}).get(e)
        bb, ba = touch(api, e)
        if r is None or not 0.25 <= r <= 0.75 or bb is None or ba is None:
            continue
        if len(win) < 3 and ba < 0.95:
            win.append(e)
        elif len(lose) < 2 and bb > 0.05:
            lose.append(e)
        else:
            continue
        used.add(ex.group)
        if len(win) == 3 and len(lose) == 2:
            break
    return win, lose


def election_run(clamp, until_close):
    tag = "s5a" if clamp else "s5b"
    set_clock(datetime(2026, 11, 3, 11, 40, tzinfo=timezone.utc))
    api, b = build(STAGED)
    STATE["t_epoch"] = P.CLK.off
    res = {}
    P.cycles(b, 1, step=300.0, stage=f"{tag}-am")
    res["reserve_before"] = (b.cash_reserve(), b.el_holdback_left(), P.status_of(b)["election"]["holdback_active"],
                             b.cash_left() - b.cash_reserve(), M.utcnow())
    P.cycles(b, 4, step=300.0, stage=f"{tag}-am")   # -> 12:05
    res["reserve_after"] = (b.cash_reserve(), b.el_holdback_left(), P.status_of(b)["election"]["holdback_active"],
                            b.cash_left() - b.cash_reserve(), M.utcnow())
    set_clock(datetime(2026, 11, 3, 23, 20, tzinfo=timezone.utc))
    P.cycles(b, 20, step=30.0, stage=f"{tag}-pre")  # -> 23:30
    res["free_2330"] = (b.cash_left(), api.cash, (P.status_of(b).get("harvest") or {}).get("collateral_resting"))
    win, lose = pick_races(b, api)
    keys = {e: f"{b.ex[e].group}|{b.ex[e].party}" for e in win + lose}
    pre_extreme = {}
    for k, v in list(b.refs.prices.items()):
        if v >= b.cfg.election_called_p - 1e-9 or v <= 1 - b.cfg.election_called_p + 1e-9:
            pre_extreme[k] = v
            if clamp:                             # (variant A: the others stay inside the band)
                b.refs.prices[k] = min(max(v, 1 - b.cfg.election_called_p + 0.001), b.cfg.election_called_p - 0.001)
    for e in win:
        b.refs.prices[keys[e]] = 0.99
    for e in lose:
        b.refs.prices[keys[e]] = 0.01
    t_set, n0, a0, al0 = P.CLK.off, len(P.CAP.lines), len(api.log_orders), len(P.ALERTS)
    coll0 = coll_snapshot(b)
    run(b, 40, step=30.0, stage=tag)              # 23:30 -> 23:50 (called at 23:40)
    run(b, 70, step=60.0, stage=tag)              # -> 01:00 4 Nov
    if until_close:
        run(b, 190, step=300.0, stage=tag)        # -> 16:50
        run(b, 20, step=30.0, stage=tag)          # -> 17:00
    res.update(api=api, b=b, win=win, lose=lose, t_set=t_set, n0=n0, a0=a0, coll0=coll0, pre_extreme=pre_extreme,
               st=P.status_of(b), alerts=list(P.ALERTS[al0:]))
    return res


def scenario5():
    out("## Scenario 5: election night (staged file; clock moved to 3 Nov; 3 Polymarket references to 0.99, 2 to 0.01)")
    A = election_run(clamp=True, until_close=False)
    B = election_run(clamp=False, until_close=True)
    cfg = A["b"].cfg
    rb, ra = A["reserve_before"], A["reserve_after"]
    check("s5: the holdback is NOT reserved before 3 Nov 12:00 (reserve = alloc_mm_reserve; status holdback off)",
          abs(rb[0] - cfg.alloc_mm_reserve) < TOL and rb[1] == 0 and rb[2] is False, rb[:3])
    check("s5: the holdback IS reserved from 12:00 (reserve = alloc_mm_reserve + 20k; the allocator's spare cash 20k "
          "lower)", abs(ra[0] - cfg.alloc_mm_reserve - cfg.election_holdback_usd) < TOL and ra[2] is True, ra[:3])
    check("s5: the holdback is in CASH at 23:30 (the gate's free cash >= election_holdback_usd)",
          A["free_2330"][0] >= cfg.election_holdback_usd - 1, A["free_2330"])
    for R, name in ((A, "s5 A (others inside the band)"), (B, "s5 B (the snapshot as it is)")):
        b, api = R["b"], R["api"]
        races5 = {b.ex[e].group for e in R["win"] + R["lose"]}
        el = [o for o in api.log_orders[R["a0"]:] if o["tag"] == "election"]
        called = set((R["st"].get("election") or {}).get("called_races") or {})
        early = [o for o in el if o["t"] < R["t_set"] + 600 - TOL and b.ex[o["eid"]].group in races5]
        check(f"{name}: no take on the 5 races before their 10 minutes (election_called_min)", not early, len(early))
        taken = {b.ex[o["eid"]].group for o in el if o["traded"] >= 1}
        spent_ = (R["st"].get("election") or {}).get("cash_spent", 0.0)
        if R is A:
            check(f"{name}: the 5 races are called and taken (every one, or until the holdback is spent)",
                  races5 <= called and (races5 <= taken or spent_ >= cfg.election_holdback_usd - 50),
                  (sorted(races5 - called), sorted(races5 - taken), spent_))
        else:                                     # (the holdback may be spent on the other called races first)
            check(f"{name}: the 5 races are called", races5 <= called, sorted(races5 - called))
        if R is A:
            off = sorted({b.ex[o["eid"]].group for o in el} - races5)
            check(f"{name}: takes ONLY on those 5 races", not off, off)
        else:
            off = sorted({b.ex[o["eid"]].group for o in el} - called)
            check(f"{name}: takes only on CALLED races (the 5 plus the ones the snapshot already prices <= 0.02 / >= "
                  f"0.98)", not off, off)
        px_bad = [(o["label"], o["yes_buy"], o["yes_price"]) for o in el
                  if (o["yes_buy"] and o["yes_price"] >= cfg.election_take_max_price - TOL)
                  or (not o["yes_buy"] and o["yes_price"] <= 1 - cfg.election_take_max_price + TOL)]
        check(f"{name}: winners bought only below 0.95, losers sold only above 0.05", not px_bad, px_bad[:4])
        spent = (R["st"].get("election") or {}).get("cash_spent", 0.0)
        paid = -sum(min(0.0, o["cash"]) for o in el)
        check(f"{name}: cash spent <= election_holdback_usd (status {spent:,.0f}; the fake's ledger {paid:,.0f})",
              spent <= cfg.election_holdback_usd + 1 and paid <= cfg.election_holdback_usd + 1, (spent, paid))
        over, batches = [], defaultdict(list)
        for o in el:                              # (valued at p as the bot saw it when the take went out)
            batches[(o["t"], b.ex[o["eid"]].group)].append(o)
        for (t, g), os_ in batches.items():
            before, ps = dict(os_[0]["race_pos"]), os_[0].get("race_p") or {}
            after = dict(before)
            for o in os_:
                after[o["eid"]] = after.get(o["eid"], 0.0) + (o["traded"] if o["yes_buy"] else -o["traded"])
            usd = {m: (b.p13_pos_usd(q, ps.get(m, 0.5)) if abs(q) >= 1 else 0.0) for m, q in after.items()}
            usd0 = {m: (b.p13_pos_usd(q, ps.get(m, 0.5)) if abs(q) >= 1 else 0.0) for m, q in before.items()}
            for o in os_:
                if usd[o["eid"]] > max(cfg.aggr_max_market_usd, usd0[o["eid"]]) + 1:
                    over.append((o["label"], round(usd0[o["eid"]]), round(usd[o["eid"]])))
            if sum(usd.values()) > max(cfg.aggr_max_race_usd, sum(usd0.values())) + 1:
                over.append((g, round(sum(usd0.values())), round(sum(usd.values()))))
        check(f"{name}: per-market / per-race collateral caps respected by the takes (at p as seen at send)", not over,
              over[:4])
        sides = set(b.el_sides)
        q_bad = [(o["label"], o["yes_buy"], o["qty"], o["pos_before"]) for o in api.log_orders[R["a0"]:]
                 if o["tag"] == "quote" and o["eid"] in sides and o["t"] >= R["t_set"] + 600 + 60
                 and not (((o["pos_before"] >= 1 and not o["yes_buy"]) or (o["pos_before"] <= -1 and o["yes_buy"]))
                          and o["qty"] <= abs(o["pos_before"]) + TOL)]
        check(f"{name}: the quoter is reduce-only on the called races", not q_bad, q_bad[:4])
        bs = Counter(t for t, k, g in api.wall if g == "election" and k == "batch")
        n_by_t = Counter(o["t"] for o in el)
        check(f"{name}: takes batched (<= batch_size {cfg.batch_size} a write)",
              all(n_by_t[t] <= cfg.batch_size * bs[t] for t in n_by_t), [(t, n_by_t[t], bs[t]) for t in n_by_t][:3])
        mw_nq, _ = minute_writes(api, R["t_set"], quote=False)
        check(f"{name}: writes of every feature but the quoter <= {WPM} in any simulated minute", mw_nq <= WPM, mw_nq)
        R.update(el=el, spent=spent, paid=paid, called=called, races5=races5, mw_nq=mw_nq,
                 mw=minute_writes(api, R["t_set"])[0])
        cr = crashes(R["n0"])
        check(f"{name}: no tick crashed / no ERROR", not cr, cr[:3])
    b, api = B["b"], B["api"]
    t_mid = (datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    t_stop = (datetime(2026, 11, 4, 16, 55, tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    after = [o for o in api.log_orders if t_mid + 60 <= o["t"] < t_stop - 60]
    check("s5 B: still cycling and sending orders after 00:00 UTC 4 Nov (the API close)", len(after) > 0 and b.running,
          (len(after), b.running))
    late = [o for o in api.log_orders if o["t"] >= t_stop + 1]
    check("s5 B: no order sent from 16:55 UTC (stop_minutes_before_close 5 before the 17:00 override)", not late,
          [(o["tag"], o["label"]) for o in late[:3]])
    check("s5 B: nothing of ours resting at 17:00", not api.orders, len(api.orders))
    STATE["s5"] = dict(A=A, B=B, after=Counter(o["tag"] for o in after))
    report_s5()


def report_s5():
    A, B = STATE["s5"]["A"], STATE["s5"]["B"]
    b = A["b"]
    rb, ra = A["reserve_before"], A["reserve_after"]
    out(f"- holdback: at {rb[4]:%d %b %H:%M} reserve ${rb[0]:,.0f} (holdback left ${rb[1]:,.0f}, active {rb[2]}), the "
        f"allocator's spare cash ${rb[3]:,.0f}; at {ra[4]:%d %b %H:%M} reserve ${ra[0]:,.0f} (holdback ${ra[1]:,.0f}, "
        f"active {ra[2]}), spare cash ${ra[3]:,.0f} (spare = the gate's free cash - the reserve; negative: none)")
    out("- the 5 references (23:30 UTC 3 Nov): winners " + ", ".join(b.ex[e].label for e in A["win"])
        + " -> 0.99; losers "
        + ", ".join(b.ex[e].label for e in A["lose"]) + " -> 0.01")
    out(f"- the snapshot already prices {len(B['pre_extreme'])} legs at <= 0.02 / >= 0.98 on Polymarket: variant A "
        "moves them just inside the band (only the 5 can be called); variant B leaves them (the realistic case: they "
        "are CALLED too after 10 min, by the spec's definition)")
    for R, name in ((A, "A (others inside the band), 23:30 -> 01:00"), (B, "B (snapshot as it is), 23:30 -> 17:00")):
        el = R["el"]
        tr = [o for o in el if o["traded"] >= 1]
        out(f"### Variant {name}")
        out(f"- called races {len(R['called'])}; election orders {len(el)}, {len(tr)} traded, "
            f"{sum(o['traded'] for o in tr):,.0f} shares; cash spent ${R['spent']:,.0f} (fake ledger "
            f"${R['paid']:,.0f}) of the ${b.cfg.election_holdback_usd:,.0f} holdback; first take "
            f"{(min([o['t'] for o in el] or [R['t_set']]) - R['t_set']) / 60:.1f} min after the references moved")
        by = defaultdict(lambda: [0, 0.0, 0.0])
        for o in tr:
            k = (o["label"], "buy" if o["yes_buy"] else "sell")
            by[k][0] += o["traded"]
            by[k][1] += o["traded"] * o["yes_price"]
            by[k][2] += o["cash"]
        out()
        out("| market | take | shares | avg YES price | cash $ |")
        out("|---|---|---|---|---|")
        for (lab, side), (n, v, c) in sorted(by.items(), key=lambda kv: -kv[1][0])[:16]:
            out(f"| {lab} | {side} | {n:,.0f} | {v / max(n, 1):.3f} | {c:+,.0f} |")
        out()
        out(f"- writes: most in a simulated minute {R['mw']} (all) / {R['mw_nq']} (without the quoter); alerts "
            f"{len(R['alerts'])}, of which {sum('ignoring it' in a for a in R['alerts'])} 'Polymarket price ... "
            "ignoring it' (ref_max_plausible_gap) on the called legs")
    out(f"- after 00:00 UTC 4 Nov (variant B): orders by feature until 16:54 " + ", ".join(
        f"{k} {v}" for k, v in sorted(STATE["s5"]["after"].items())) + "; none from 16:55; nothing resting at 17:00")
    out()


# ============================================================================================ the run
def main():
    global S
    if not os.path.exists(os.path.join(P.SNAP, "md.sqlite")):
        print(f"SKIP: no snapshot at {P.SNAP}")
        return 0
    t0 = _rt.time()
    S = P.load_snapshot()
    raw = staged()
    good, bad = M.validate_overrides(raw, M.Config())
    check("files: the staged file validates (no refused key)", not bad and len(good) == len(raw), bad)
    live = json.load(open(os.path.join(P.SNAP, "settings_override.json")))
    want = dict(live, worst_case_backstop_frac=1.0, max_worst_case_frac=0.60, max_bloc_delta_frac=0.5,
                alloc_mm_reserve=20000.0, mm_risk_reserve_wc=5000.0, mm_risk_reserve_corr=4000.0)
    diff = {k: (raw.get(k), v) for k, v in want.items() if raw.get(k) != v}
    check("files: the staged file = the live file + section 7's risk settings", not diff, diff)
    check("files: every Package 13 flag on, close_override_utc 17:00 4 Nov, stop 5 min",
          all(raw.get(k) is True for k in P13_FLAGS) and raw.get("close_override_utc") == "2026-11-04T17:00:00Z"
          and raw.get("stop_minutes_before_close") == 5, {k: raw.get(k) for k in P13_FLAGS})
    out("# Package 13 dry run on the fake exchange, seeded with the live state of 4 Oct 15:56 UTC")
    out()
    out(f"Generated by `tests/test_p13_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {P.SNAP} with "
        f"the tests/test_p9_dryrun.py harness: all 237 markets, other traders' books, raw Polymarket references, "
        f"positions, marks, cash, position lots; settings through the bot's own `check_overrides`; full `Bot.cycle`s "
        f"on a simulated clock; every order the fake receives logged with the feature that sent it. The fake models a "
        f"28-writes / simulated-minute limiter (the bot plans with it; the fake counts, never blocks), aggregates book "
        f"levels by price and is thread-safe.")
    out()
    out(f"Seed: {len(S['snap'])} markets, {len(S['pos'])} positions, account {S['status']['account_value']:,.0f}.")
    out()
    scenario0()
    print(f"(scenario 0 done in {_rt.time() - t0:.0f} s)")
    scenario1()
    print(f"(scenario 1 done in {_rt.time() - t0:.0f} s)")
    api, b = scenario1f()
    scenario2(api, b)
    print(f"(scenarios 1F-2 done in {_rt.time() - t0:.0f} s)")
    scenario3()
    scenario4()
    print(f"(scenarios 3-4 done in {_rt.time() - t0:.0f} s)")
    scenario5()
    findings()
    print(f"(all done in {_rt.time() - t0:.0f} s)")
    return 0


def findings():
    s1, s2, s3, s4 = STATE.get("s1", {}), STATE.get("s2", {}), STATE.get("s3", {}), STATE.get("s4", {})
    s5, s1f = STATE.get("s5", {}), STATE.get("s1f", {})
    rows = s1.get("rows") or []
    r0, r2 = (rows[0], rows[-1]) if rows else ({}, {})
    mo = (s1f.get("st") or {}).get("momentum") or {}
    A, B = s5.get("A", {}), s5.get("B", {})
    pct = (lambda r, k: 100 * r.get(k, 0) / (r.get("eq") or 1))
    summ = [
        f"## Summary ({sum(RESULTS)}/{len(RESULTS)} checks passed)",
        "- **Flags off = identical**: the base (live file + reserve 20k, backstop 0.90, mm_risk_reserve 5k / 4k) gives "
        "the same orders and status.json as 3e635d7 and as the pre-Package-13 code 663ede1 (12 cycles, every order).",
        f"- **Buckets (staged file, 2 h)**: MM / VALUE / MOMENTUM {pct(r0, 'mm'):.0f} / {pct(r0, 'value'):.0f} / "
        f"{pct(r0, 'mom'):.0f}% at hour 0 -> {pct(r2, 'mm'):.0f} / {pct(r2, 'value'):.0f} / {pct(r2, 'mom'):.0f}% "
        f"after 2 h (targets 20 / 40 / 40). VALUE converges (sell-down "
        f"${sum(x['usd'] for h in s1.get('sold', []) for x in h):,.0f} in {sum(len(h) for h in s1.get('sold', []))} "
        f"IOC sales, lowest edge-held first); **MOMENTUM buys nothing**: the sleeve only spends free cash ABOVE "
        f"alloc_mm_reserve, and every free dollar is locked first by the harvest ladder (${r2.get('hv_coll', 0):,.0f} "
        f"resting after 2 h, counted in no bucket), the quoter, the allocator's spare-cash buys and the stale-quote "
        f"takes (the gate's cash left is ~0 every cycle). Not changed (a strategy choice for the owner; see Findings).",
        f"- **Funded sleeve (fixture 1F: harvest / allocator / takes off for 1 h, +45k cash)**: {mo.get('markets')} "
        f"legs, ${mo.get('cost', 0):,.0f} at cost; every buy a momentum market, none opposite a position, none on a "
        f"VALUE short.",
        f"- **Harvest ladder**: {len(s1.get('lad') or {})} markets, "
        f"{sum(len(v) for v in (s1.get('lad') or {}).values())} levels, ${r2.get('hv_coll', 0):,.0f} resting after 2 h "
        f"(planned ~120 markets if the cash allowed); every level >= 8% per $ at p, none through the book or our "
        f"orders, within the 12k / 15k caps.",
        f"- **Tilt +3c (1 h, on the 1F fixture)**: {s2.get('n', 0)} harvest fills, {s2.get('shares', 0):,.0f} shares, "
        f"${s2.get('coll', 0):,.0f} of collateral at ${s2.get('edge', 0):,.0f} of edge at p "
        f"({100 * s2.get('edge', 0) / max(s2.get('coll', 1), 1):.1f}% per $); the sleeve's mark "
        f"${s2.get('mark0', 0):,.0f} -> ${s2.get('mark1', 0):,.0f}; EV outcome {s2.get('ev0') or 0:,.0f} -> "
        f"{s2.get('ev1') or 0:,.0f}.",
        f"- **Kill**: killed {s3.get('dt') or 0:.0f} s after a -30% move, one alert, the 6-h exit "
        f"({s3.get('n_exit', 0)} sales, ${s3.get('usd', 0):,.0f}), latched, never re-bought; the ladder then plans "
        f"{len(s3.get('taken') or [])} of the {s3.get('legs', 0)} former sleeve markets (the rest are middle-band / "
        "below 8%).",
        "- **Manual exit**: done " + ", ".join(f"{(s4.get('prog') or {}).get(h, (0,))[0] or 0:.2f} at {h} h"
                                              for h in (1, 3, 6)) + " (linear), 'exited', never re-bought.",
        f"- **Election night**: holdback reserved from 12:00 only; calls after 10 min; takes only on called races, "
        f"below 0.95 / above 0.05, within the caps; variant A ${A.get('spent', 0):,.0f} spent, variant B "
        f"${B.get('spent', 0):,.0f} (= the holdback; {len(B.get('called') or ())} races called because the snapshot "
        f"already prices {len(B.get('pre_extreme') or ())} legs <= 0.02 / >= 0.98); trading after 00:00 4 Nov, nothing "
        f"from 16:55.",
        f"- **Writes**: every feature but the quoter <= 28 a simulated minute in every scenario; the quoter's urgent "
        f"pulls take the all-feature minute to {s1.get('mw', 0)} (base alone: {STATE.get('s0_wpm', (0, 0))[0]}).",
        "",
        "## Fixes made in mm_bot.py (each only acts with its Package 13 flag on; the flags-off identity grids pass)",
        "1. **Momentum round trip** (`mom_buy_step`): the sleeve bought back, minutes later and 1c worse, what the "
        "bucket sell-down had just sold (Rep FL-16 House race NO, Rep Vermont Governor in fixture 1F). Now no momentum "
        "buy in a race the sell-down sold in within alloc_interval_s (`bk_sold_race`).",
        "2. **Exit schedule** (`mom_exit_step`): legs were served in a fixed eid order, ~5 orders a cycle (the write "
        "share): with 18 legs the last ones starved and the exit fell behind the linear schedule (6% at 1 h, 19% at 3 "
        "h, 64% at 6 h). Now the leg furthest behind its schedule goes first.",
        "3. **Ladder from a stale touch** (`hv_plan` / `hv_top_moved`): when the bulk best prices showed a book had "
        "moved but it was not re-downloaded yet (max_books_per_cycle 30), the ladder planned from the old touch and "
        "placed levels through the book (2 taker fills at placement in scenario 2). Now such a market is 'soft' for "
        "the cycle (no new levels; resting ones stay). (`last_tops_at` records when each bulk price was read.)",
        "4. **Election calls blind to the stale-book case** (`el_update_calls` / `el_rejected_refs`): a Polymarket "
        "price more than ref_max_plausible_gap (0.25) from the tournament book is dropped as a 'wrong match' - which "
        "is exactly a call against a stale book (0.99 vs a 0.45 book): such races were NEVER called and nothing was "
        "taken (the unit tests set the gap to 1.0). Now the calls read the raw price of a dropped reference (the "
        "quoter, allocator and fair values still ignore it).",
        "5. **Election 'covered' sales that were shorts** (`el_take_plan`): YES sold into a loser's bids counted as "
        "covered from `cash_free` (ex.inv: last cycle's position, stale on a market taken every cycle): 18 consecutive "
        "100-share shorts on Rep Nevada Governor after the holdback was spent, the fake's ledger $21,045 vs the bot's "
        "$20,000. Now bounded by this cycle's position.",
        "6. **Holdback not in cash** (cycle step 7, the quoter's plan budget): from 12:00 the allocator / momentum / "
        "takes kept the holdback but the quoter's budget did not, so at 23:30 the gate's free cash was ~0 and the "
        "election takes were refused by the cash gate. The quoter now plans with cash_left - el_holdback_left (the "
        f"gate's free cash at 23:30: ${(A.get('free_2330') or (0,))[0]:,.0f}).",
        "",
        "## Findings (not changed: strategy / owner's call)",
        "- F1 MOMENTUM starves under the staged file (above). Options: the harvest ladder keeps cash_reserve() plus "
        "the sleeve's shortfall while a buy round is open; the allocator's spare-cash buys pause while MOMENTUM is "
        "under target (they re-buy VALUE with the sell-down's proceeds); or the sleeve gets the free cash first.",
        "- F2 The harvest ladder's resting collateral is in no bucket (MM falls to ~7% because the gate's cash, which "
        "caps MM, is locked by the ladder).",
        f"- F3 The exits dribble: ~5 IOC orders a cycle of a few $ each (scenario 3: {s3.get('n_exit', 0)} orders for "
        f"${s3.get('usd', 0):,.0f}) - 15 of the 28 writes a minute for 6 h. A minimum chunk per order would free the "
        f"budget.",
        "- F4 Election night: every race Polymarket already prices <= 0.02 / >= 0.98 is CALLED 10 minutes after "
        f"election_start_utc ({len(B.get('pre_extreme') or ())} legs in this snapshot) and the holdback goes to the "
        "best edge first - in variant B most of it to those (Rhode Island etc.), not to the races called during the "
        "night.",
        "- F5 A called race's p jumps when the winner's reference is first dropped then accepted (the book catches up "
        "after our own take): the cap valuation at send (raw p of the loser) can be well below the valuation a cycle "
        "later (race-scaled), e.g. Rep Nevada Governor short 11.4k at send, 14.2k afterwards. The test checks the caps "
        "at the p the bot saw at send.",
        f"- F6 Every call against a stale book also raises the 'Polymarket price ... ignoring it (wrong match?)' alert "
        f"(ref_max_plausible_gap): {sum('ignoring it' in a for a in A.get('alerts', []))} in variant A, "
        f"{sum('ignoring it' in a for a in B.get('alerts', []))} in variant B - noise on election night (the calls now "
        f"read those prices; the quoter still ignores them, which is what makes it reduce-only-safe).",
        ""]
    OUT[6:6] = summ
    out("## Caveats of the fake")
    out("- Other traders never trade with our resting orders except where scenario 2 / 3 rewrite their books through "
        "them (scenario 2's maker fills are exactly those crossings, at our price); no market-making P&L is simulated.")
    out("- The books move only where we take, the scenario rewrites, and the refill to the snapshot depth (hourly; "
        "every 10 minutes in scenarios 3 and 4, so the 6-h exits are not limited by a one-level touch that never "
        "replenishes).")
    out("- Polymarket is flat (except the election scenario's 5 references); every Polymarket price counts as liquid "
        "and fresh (no ages: alloc_p never goes stale); the exchange's marks stay at the snapshot's (status / account "
        "value at the marks), while the sleeve's mark is the bot's own book mid.")
    out("- The write limiter is modelled for planning only: writes_left / write_wait follow a 28 / simulated-minute "
        "window, but the fake never blocks or 429s, so what the bot sends beyond it is counted (the live Api would "
        "make it wait). The quoter's urgent pulls go regardless of the budget by design (send_changes: 'pulls always "
        "go'), which is why the all-feature minute can exceed 28 in the base too; the request (read) budget is "
        "unlimited.")
    out("- The fake's cash rule: an order needing more than the free cash is refused (no set-collateral rule here); "
        "the self-test is off; no restarts; the realtime feed is off.")
    out("- Scenario 1F is a fixture, not the staged file's behaviour: see the findings for why the momentum bucket "
        "gets no cash under the staged file.")


if __name__ == "__main__":
    rc = main()
    if os.environ.get("P13_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
