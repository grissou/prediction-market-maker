"""Package 15 DRY RUN on the fake exchange: the harvest ladder (alone) + the per-state cap on the LIVE state.
Seed: the 4 Oct 15:56 ops snapshot (/home/claude/snap04, or $P9_SNAP; all 237 markets, other traders' books, Polymarket
references, positions, marks, lots) moved to the owner's live numbers by tests/p14_1_state.py (books 20% converged
toward Polymarket, account 102.0k, free cash ~1.9k of the 20k reserve, MM inventory 10.5k, value adds paused on the
worst-case room), built on the owner's LIVE file = 64f27c8 + alloc_swap_sell_margin 0.03, alloc_swap_min_gain 0.05,
value_sell_margin 0.01, alloc_mm_reserve 20000, mm_risk_reserve_wc 20000 (the staged file less its P15 keys). The staged
file deploy/package15/settings_override.harvest.json lands through the bot's own override path (check_overrides).
Scenarios:
  0  flags off: the live file on 64f27c8 and on this head, 12 cycles: identical orders and status (but HARVEST_KEYS /
     mm_funding); the staged file with tilt_harvest_ladder false and state_max_usd 0 = the live file;
  A  the live state as it is (free cash below the MM's own 10k, value adds paused), the staged file, 2 h of 60-s cycles
     (books refilled hourly): what the ladder does, the refill, the carve-out, the per-state collateral;
  B  the "funded" fixture: the same state + $25,000 cash (account 127k: free cash above the 20k reserve, the risk room
     above 1.1 x its 20k reserve -> value adds not paused), the staged file 2 h: the levels resting per market (count,
     collateral, edge per level), the carve-out used / free, the MM's effective reserve at every harvest send, the
     states vs the cap (Rhode Island: what was blocked), writes per cycle / per minute;
  C  a +3c tilt rise over 1 h on B (longshot books +1c, favourite books -1c at 0, 20 and 40 min; other traders'
     moved books fill our resting levels at our price): harvest fills, edge at p, collateral, EV.
The fake models a 28 writes / simulated minute limiter (writes_left / write_wait; it does not block: what is sent
beyond it is counted), aggregates book levels by price as the exchange does, and fills our RESTING orders only where a
scenario moves other traders' books through them (C). Writes analysis/p15/DRYRUN_P15.md (P15_DRYRUN_REPORT=0: none).
Run:  python tests/test_p15_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import importlib.util
import json
import os
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
SNAP = os.environ.get("P9_SNAP", "/home/claude/snap04")
os.environ.setdefault("P9_SNAP", SNAP)
import dryrun_harness as P                                      # noqa: E402  (the harness: snapshot, fake, clock)
RETIRED_KEYS = P.RETIRED_KEYS                     # removed from Config; off in every staged file
import p14_1_state as T                                          # noqa: E402  (the live state)
import mm_bot as M                                                # noqa: E402

STAGED = os.path.join(ROOT, "deploy", "package15", "settings_override.harvest.json")
LIVE_142 = os.path.join(ROOT, "deploy", "package14", "settings_override.mm_funding_14_2.json")
REPORT = os.path.join(ROOT, "analysis", "p15", "DRYRUN_P15.md")
BASE_REV = "64f27c8"
P15_KEYS = ("tilt_harvest_ladder", "harvest_offsets", "harvest_level_usd", "harvest_min_edge", "harvest_max_markets",
            "harvest_writes_frac", "harvest_requote_s", "harvest_total_usd", "state_max_usd")
WPM = 28
WRITE_KINDS = ("batch", "cancel_all", "cancel_order")
TOL = 1e-6
RESULTS, OUT, STATE = [], [], {}
P.TAGGED = P.TAGGED + ("alloc_tick", "hv_tick")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def out(s=""):
    OUT.append(s)


# ============================================================================================ the fake, extended
class Dry15(P.DryApi):
    """P.DryApi plus a 28-writes-per-simulated-minute limiter, book levels aggregated by price, a lock (the bot's
    parallel writes), p at send in the order log, and maker fills where a moved book crosses our resting orders."""

    def __init__(self, S_, cash=None):
        self._lk = threading.RLock()
        self.wt = deque()
        self.wall = []                            # (sim time, kind, feature)
        self.bot = None
        self.maker_fills = []
        self.limit = True
        super().__init__(S_, cash)
        self.wbudget = WPM

    def log(self, *a):
        if a and a[0] in WRITE_KINDS:
            with self._lk:
                self.wt.append(P.CLK.off)
                self.wall.append((P.CLK.off, a[0], self.tag))
        self.calls.append(a)

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
            ps = [pval(b, o["exchangeId"]) if b is not None else None for o in orders]
            n0 = len(self.log_orders)
            res = super().place_batch(orders)
            for k, rec in enumerate(self.log_orders[n0:]):
                rec["p"] = ps[k]
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
        """Other traders' (moved) book crossing our resting orders trades them at OUR price (we are the maker)."""
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
                                       "filledAt": M.iso(M.util.utcnow())})
                    self.maker_fills.append({"t": P.CLK.off, "oid": oid, "eid": e, "bid": is_bid, "px": px,
                                             "qty": take, "pos_before": q0})
                if o["quantity"] <= 0:
                    del self.orders[oid]


P.DryApi = Dry15


def tag_methods15(b):
    """P.tag_methods for the methods this bot has (64f27c8 has no hv_tick)."""
    for name in P.TAGGED:
        if not hasattr(b, name):
            continue
        orig = getattr(b, name)

        def wrapped(*a, _orig=orig, _name=name, **k):
            prev = b.api.tag
            b.api.tag = _name
            try:
                return _orig(*a, **k)
            finally:
                b.api.tag = prev
        setattr(b, name, wrapped)


P.tag_methods = tag_methods15


# ============================================================================================ helpers
def pval(b, e):
    """The value-mode p of a market (liquid, race-scaled Polymarket), as Bot.alloc_p sees it in a cycle."""
    ex = b.ex.get(e) if b is not None else None
    if ex is None or e not in (b.cur_liquid or ()) or (b.cur_refs or {}).get(e) is None:
        return None
    return b.scaled_ref(ex)


def touch(api, e):
    bk = api.books.get(e) or {}
    return (bk.get("bids") or [{}])[0].get("price"), (bk.get("asks") or [{}])[0].get("price")


def resting(api):
    """Our resting orders in YES terms: [(oid, eid, is_bid, price, qty)]."""
    res = []
    for oid, o in list(api.orders.items()):
        yb, yp = api.yes_view(o)
        res.append((oid, o["exchangeId"], yb, yp, o.get("quantity") or 0))
    return res


def write_file(d, name):
    path = os.path.join(tempfile.mkdtemp(prefix="p15dry"), name)
    with open(path, "w") as f:
        json.dump(d, f)
    return path


def live_file():
    """The owner's live file: the staged file less its P15 keys (= the 14.2 file with value_sell_margin 0.01)."""
    return write_file({k: v for k, v in json.load(open(STAGED)).items() if k not in P15_KEYS}, "live.json")


def build(path, cash_add=0.0, seed=7):
    api, b = T.build(STATE["S"], path, seed=seed)
    api.bot = b
    if cash_add:
        api.cash += cash_add
        P.cycles(b, 2, step=60.0, stage="fund")
    return api, b


def run(b, n, step=60.0, stage=None, refill_s=3600.0, hook=None, per_cycle=None):
    api = b.api
    for _ in range(n):
        if hook:
            hook()
        t_prev = P.CLK.off
        P.cycles(b, 1, step=step, stage=stage)
        if per_cycle:
            per_cycle()
        if int(P.CLK.off // refill_s) != int(t_prev // refill_s):
            api.refill()


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
    cross, own_cross, same_px, low_edge, dup = [], [], [], [], []
    for e, lv in lad.items():
        bb, ba = touch(api, e)
        mine = [(is_bid, px, oid) for oid, ee, is_bid, px, q in resting(api) if ee == e]
        for oid, is_bid, px, q, edge, lock in lv:
            others = [x for x in mine if x[2] != oid]
            if (not is_bid and bb is not None and px <= bb + TOL) or (is_bid and ba is not None and px >= ba - TOL):
                cross.append((b.ex[e].label, "bid" if is_bid else "ask", px, bb, ba))
            if any(ib != is_bid and ((not is_bid and opx >= px - TOL) or (is_bid and opx <= px + TOL))
                   for ib, opx, _ in others):
                own_cross.append((b.ex[e].label, "bid" if is_bid else "ask", px))
            if any(ib == is_bid and abs(opx - px) < TOL for ib, opx, _ in others):
                same_px.append((b.ex[e].label, px))
            if edge is None or edge < cfg.harvest_min_edge - 1e-9:
                low_edge.append((b.ex[e].label, px, edge))
        prices = Counter((is_bid, round(px, 3)) for _, is_bid, px, _, _, _ in lv)
        dup += [(b.ex[e].label, k) for k, v in prices.items() if v > 1]
    check(f"{label}: no harvest level at / through other traders' book", not cross, cross[:4])
    check(f"{label}: no harvest level at / through our own orders, none at the price of our own order on that side",
          not own_cross and not same_px, (own_cross[:3], same_px[:3]))
    check(f"{label}: every resting harvest level clears harvest_min_edge {cfg.harvest_min_edge:g} at today's p",
          not low_edge, low_edge[:4])
    check(f"{label}: no duplicate harvest level (same market, side, price)", not dup, dup[:4])
    return lad


def minute_writes(api, t0=None, t1=None, feature=None):
    """Max writes in any 60-s simulated window ending at a write (all features, or one)."""
    ws = [(t, g) for t, _, g in api.wall if (t0 is None or t >= t0) and (t1 is None or t <= t1)]
    best = 0
    for t, _ in ws:
        n = sum(1 for tt, g in ws if t - 60 + TOL < tt <= t + TOL and (feature is None or g == feature))
        best = max(best, n)
    return best


def states(b, inv):
    coll, _ = b.st_snapshot(dict(inv))
    return coll


def fmt_k(x):
    return f"{x / 1000:.1f}k"


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
        getattr(mod, "util", mod).alert, getattr(mod, "util", mod).notify, getattr(mod, "util", mod).time = M.util.alert, (lambda *a, **k: False), P.CLK
        getattr(mod, "util", mod).utcnow = M.util.utcnow
        if P.CAP not in mod.log.handlers:
            mod.log.addHandler(P.CAP)
        mod.log.propagate = False
        return mod
    except Exception as e:                        # (reported as a failure)
        print("    (base module unavailable:", e, ")")
        return None


def s0_run(mod, tag, path):
    """12 cycles of the file at path on module mod (its orders, its status). parallel_writes 1 in the file: the
    writer threads' timing otherwise moves the cash read's last cents and with them an order now and then."""
    path = write_file({**json.load(open(path)), "parallel_writes": 1}, "s0.json")
    keep_m, keep_tm, keep_off = P.M, T.M, P.CLK.off
    P.M, T.M = mod, mod
    try:
        api, b = T.build(STATE["S"], path, seed=3)
        api.limit = False
        P.cycles(b, 12, stage=tag)
        sent = sorted((o["cycle"], o["eid"], o["yes_buy"], o["yes_price"], o["qty"], o["tag"])
                      for o in P.orders(api, tag))
        return sent, P.status_of(b)
    finally:
        P.M, T.M = keep_m, keep_tm
        P.CLK.off = keep_off


VOLATILE = {"updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "ev_outcome_history",
            "last_cycle_phases", "cycle_phase", "tilt_diag", "requests_last_min", "cash_gate_read_age"}


def norm_status(st):
    drop = VOLATILE | set(M.Bot.MM_FUNDING_KEYS) | set(M.Bot.HARVEST_KEYS)
    res = {}
    for k, v in st.items():
        if k in drop or k.endswith("_seconds"):
            continue
        if k in ("alloc", "mm_risk_room") and isinstance(v, dict):   # (wall clocks; the cash read's last cents follow
            v = {a: ([f[1:] for f in x] if a == "flows" else x) for a, x in v.items()   # the writer threads' timing)
                 if a not in ("last_run_wall", "last_run", "since", "cash_before", "cash_after")}
            if isinstance(v.get("swaps"), dict):
                v["swaps"] = {**v["swaps"], "events": [e[1:] for e in v["swaps"].get("events") or []]}
        res[k] = v
    return res


def scenario0():
    out("## Scenario 0: flags off = 64f27c8")
    live = live_file()
    base = base_module(BASE_REV)
    check(f"s0: base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)
    if base is None:
        return
    sb, stb = s0_run(base, "s0b", live)
    sn, stn = s0_run(M, "s0n", live)
    strip = lambda xs: [x[:5] for x in xs]          # noqa: E731  (the stage tag differs by name only)
    check("s0: the live file, 12 cycles on the live state: the same orders on 64f27c8 and on this head",
          strip(sn) == strip(sb) and len(sn) > 0, (len(sn), len(sb)))
    # (the blocked-takes counter once caught a real bug: the take loop pricing every take off the wrong market;
    #  it stays in the comparison on purpose)
    nn, nb = norm_status(stn), norm_status(P.retired(stn, stb))
    check("s0: ...and the same status.json (but timings, HARVEST_KEYS and mm_funding)",
          nn == nb and "harvest" not in stn and "state_caps" not in stn,
          [(k, nn.get(k), nb.get(k)) for k in set(nn) | set(nb) if nn.get(k) != nb.get(k)])
    off = {**json.load(open(STAGED)), "tilt_harvest_ladder": False, "state_max_usd": 0}
    so, _ = s0_run(M, "s0o", write_file(off, "staged_off.json"))
    check("s0: the staged file with tilt_harvest_ladder false and state_max_usd 0 = the live file (same orders)",
          strip(so) == strip(sn), (len(so), len(sn)))
    out(f"- the live file on 64f27c8 and on this head, 12 cycles: {len(sn)} orders each, identical; status identical "
        "less HARVEST_KEYS / mm_funding / timings")
    out(f"- the staged file with the two P15 switches off: {len(so)} orders, identical to the live file")
    out()


# ============================================================================================ scenario A
def snapshot_states(b, api):
    b.cfg.state_max_usd, keep = 15000.0, b.cfg.state_max_usd
    coll = states(b, api.inv)
    b.cfg.state_max_usd = keep
    pos = {}
    for e, q in sorted(api.inv.items()):
        ex = b.ex.get(e)
        if ex is not None and M.state_of(ex.label) == "RI" and abs(q) >= 1:
            pos[ex.label] = (q, b.st_px(ex))
    return coll, pos


def scenario_a():
    out("## Scenario A: the live state as it is, the staged file, 2 h")
    api, b = build(live_file())
    coll0, ri0 = snapshot_states(b, api)
    f0 = P.status_of(b)["mm_funding"]
    STATE["ri0"], STATE["coll0"], STATE["ri_pos"] = coll0.get("RI", 0.0), coll0, ri0
    out(f"- the seed: account {api.account:,.0f}, free cash {f0['cash_free']:,.0f} of {f0['cash_target']:,.0f}, "
        f"risk room wc {f0['room_free']['wc']:,.0f} (reserve 20,000), value adds paused: {b.mmr_paused}")
    out("- per-state collateral (positions at p + resting locks), top 10: "
        + ", ".join(f"{s} {fmt_k(c)}" for c, s in sorted(((c, s) for s, c in coll0.items()), reverse=True)[:10]))
    out("- Rhode Island on the snapshot: " + "; ".join(f"{k} {q:+,.0f} (p {p:.3f})" for k, (q, p) in ri0.items())
        + f" = {STATE['ri0']:,.0f} (the owner reports ~23k live on 6 Oct: the book grew since 4 Oct)")
    P.apply_stage(b, STAGED)
    n0, t0 = len(P.CAP.lines), P.CLK.off
    rows = []

    def per():
        if api.cycle_no % 15 == 0:
            st = P.status_of(b)
            h, mf = st.get("harvest") or {}, st["mm_funding"]
            rows.append((round((P.CLK.off - t0) / 60), h.get("levels_resting", 0), h.get("collateral_resting", 0),
                         mf.get("cash_free"), mf.get("reserve_cash"), mf.get("room_free", {}).get("wc"), b.mmr_paused,
                         mf.get("refill_sold_usd", 0), dict(h.get("blocked_by") or {})))
    run(b, 120, stage="A", per_cycle=per)
    st = P.status_of(b)
    h, mf = st.get("harvest") or {}, st["mm_funding"]
    crashes = [m for _, lv, m in P.CAP.lines[n0:] if lv in ("ERROR", "CRITICAL") or "tick failed" in m]
    check("A: 2 h of cycles with the staged file, no tick crashed / no ERROR", not crashes, crashes[:3])
    check("A: status harvest and state_caps present; mm_funding shows the split (cash_target 20,000 = the refill "
          "target, mm_reserve_effective 10,000, harvest_carve)", "harvest" in st and "state_caps" in st
          and mf["cash_target"] == 20000.0 and mf["mm_reserve_effective"] == 10000.0 and "harvest_carve" in mf,
          (mf.get("cash_target"), mf.get("mm_reserve_effective")))
    hv_o = P.orders(api, "A", "hv_tick")
    bad_send = [o for o in hv_o if o["need"] > 0 and o["free"] - o["need"] < 10000 - 1.0]
    check("A: no harvest level sent that would take the free cash below the MM's effective 10,000", not bad_send,
          [(o["label"], o["free"], o["need"]) for o in bad_send[:3]])
    check("A: the carve-out never exceeded: resting harvest collateral <= 10,000 at every sample",
          all(r[2] <= 10000 + 1 for r in rows), [r[2] for r in rows])
    ri_end = ((st.get("state_caps") or {}).get("RI") or {}).get("collateral", 0.0)
    check("A: Rhode Island's adds stop at the 15,000 cap (the swaps' buys trimmed to the room; positions kept)",
          ri_end <= 15000 + 600, ri_end)
    STATE["A"] = dict(rows=rows, h=h, mf=mf, orders=len(hv_o), traded=sum(1 for o in hv_o if o["traded"] > 0),
                      blocked=h.get("blocked_by") or {}, state_caps=st.get("state_caps") or {})
    lad = ladder_view(b, api)
    out(f"- after 2 h: {h.get('levels_resting', 0)} levels resting in {h.get('markets', 0)} markets, "
        f"${h.get('collateral_resting', 0):,.0f} of collateral (carve {h.get('carve')}); harvest orders sent "
        f"{len(hv_o)}; free cash {mf['cash_free']:,.0f}, reserve cash (free + carve) {mf.get('reserve_cash', 0):,.0f}"
        f"; refill sold ${mf.get('refill_sold_usd', 0):,.0f} (24 h); value adds paused {b.mmr_paused}")
    out(f"- blocked_by (markets without a ladder, by reason): {h.get('blocked_by')}")
    out(f"- state_caps: {st.get('state_caps')}")
    ri = (st.get("state_caps") or {}).get("RI") or {}
    out(f"- Rhode Island: {STATE['ri0']:,.0f} at the seed -> {ri.get('collateral', 0):,.0f} after 2 h (the allocator's "
        f"swap buys - short YES on Rep Rhode Island Senate - added up to the cap and stopped there: "
        f"{ri.get('blocked_adds', 0)} adds held back)")
    out("| min | levels | $ resting | free cash | reserve cash | room wc | paused | refill sold 24h | blocked_by |")
    out("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        out(f"| {r[0]} | {r[1]} | {r[2]:,.0f} | {r[3] or 0:,.0f} | {r[4] or 0:,.0f} | {r[5] or 0:,.0f} | {r[6]} | "
            f"{r[7]:,.0f} | {r[8]} |")
    out("- levels resting at the end: " + ("; ".join(f"{b.ex[e].label}: " + " ".join(
        f"{'bid' if x[1] else 'ask'} {x[3]:.0f}@{x[2]:.3f}" for x in lv) for e, lv in sorted(lad.items())) or "none"))
    out()
    return api, b


# ============================================================================================ scenario B
def scenario_b():
    out("## Scenario B: the funded fixture (+$25,000 cash), the staged file, 2 h")
    api, b = build(live_file())
    api.cash += 25000.0                           # (the cash arrives with the staged file: the next read sees it)
    free0 = api.cash - api.cash_locked() - api.cash_hold
    P.apply_stage(b, STAGED)
    P.cycles(b, 1, step=60.0, stage="fund")
    f0 = P.status_of(b)["mm_funding"]
    out(f"- the fixture: account {api.account:,.0f}, free cash before the first cycle {free0:,.0f}, after it "
        f"{f0['cash_free']:,.0f}; risk room wc {f0['room_free']['wc']:,.0f}, value adds paused: {b.mmr_paused}")
    check("B: the fixture is funded: free cash above the 20k reserve when the file lands, value adds not paused",
          free0 > 20000 and not b.mmr_paused, (free0, b.mmr_paused))
    n0, t0, w0 = len(P.CAP.lines), P.CLK.off, len(api.wall)
    rows, per_cycle_w, samples = [], [], []

    def per():
        n = sum(1 for t, _, g in api.wall if t >= P.CLK.off - 60 - TOL and g == "hv_tick")
        per_cycle_w.append(n)
        lad = ladder_view(b, api)
        samples.append((sum(x[5] for v in lad.values() for x in v), dict(b.st_coll), b.cash_left()))
        if api.cycle_no % 15 == 0:
            st = P.status_of(b)
            h, mf = st.get("harvest") or {}, st["mm_funding"]
            rows.append((round((P.CLK.off - t0) / 60), h.get("markets", 0), h.get("levels_resting", 0),
                         h.get("collateral_resting", 0), (h.get("carve") or {}).get("free"), mf.get("cash_free"),
                         mf.get("mm_reserve_effective"), b.mmr_paused, h.get("placed", 0), h.get("pulled", 0)))
    run(b, 120, stage="B", per_cycle=per)
    st = P.status_of(b)
    h, mf = st.get("harvest") or {}, st["mm_funding"]
    crashes = [m for _, lv, m in P.CAP.lines[n0:] if lv in ("ERROR", "CRITICAL") or "tick failed" in m]
    check("B: 2 h, no tick crashed / no ERROR", not crashes, crashes[:3])
    lad = ladder_checks(b, api, "B (after 2 h)")
    check("B: the ladder rests levels (markets laddered)", h.get("levels_resting", 0) > 0, h)
    check("B: the carve-out held at every cycle: resting harvest collateral <= 10,000",
          all(s[0] <= 10000 + 1 for s in samples), max(s[0] for s in samples))
    hv_o = P.orders(api, "B", "hv_tick")
    bad_send = [o for o in hv_o if o["need"] > 0 and o["free"] - o["need"] < 10000 - 1.0]
    check("B: the MM's effective reserve: no harvest send took the free cash below 10,000",
          not bad_send, [(o["label"], round(o["free"]), round(o["need"])) for o in bad_send[:3]])
    crossed = [o["label"] for o in hv_o if o["traded"] > 0]
    check("B: no harvest level crossed the book when placed (no taker fill at placement)", not crossed, crossed[:4])
    over = []
    coll0 = STATE.get("coll0") or {}
    for s_ in samples:
        for k, c in s_[1].items():
            if c > max(15000.0, coll0.get(k, 0.0) + 2000.0) + 1:
                over.append((k, round(c)))
    check("B: no state's collateral grew past max(15,000, its seed level) beyond a quote's size (adds stop at "
          "the cap)", not over, sorted(set(over))[:5])
    ri_lv = [(b.ex[e].label, x[2], x[3]) for e, lv in lad.items() for x in lv if b.st_key(e) == "RI"
             and x[5] > 0]
    ri_max = max((s_[1].get("RI", 0.0) for s_ in samples), default=0.0)
    ri_bl = (st.get("state_caps") or {}).get("RI", {}).get("blocked_adds", 0)
    check("B: Rhode Island: its adds stop at the cap - RI's collateral never above 15,000 (+ one quote) at any cycle, "
          "the adds held back are counted", ri_max <= 15000 + 600 and ri_bl > 0, (round(ri_max), ri_bl, ri_lv[:3]))
    STATE["ri_b"] = (ri_max, ri_bl, ri_lv)
    mw_all, mw_hv = minute_writes(api, t0), minute_writes(api, t0, feature="hv_tick")
    check(f"B: writes: the ladder's at most harvest_writes_frac 0.4 x 28 + a batch ~ 12 a minute ({mw_hv}); all "
          f"features {mw_all} a minute (the limiter: 28)", mw_hv <= 13, (mw_hv, mw_all))
    out(f"- after 2 h: {h.get('levels_resting', 0)} levels in {h.get('markets', 0)} markets, "
        f"${h.get('collateral_resting', 0):,.0f} resting of the $10,000 carve-out (free "
        f"${(h.get('carve') or {}).get('free', 0):,.0f}); placed {h.get('placed', 0)}, pulled {h.get('pulled', 0)}; "
        f"MM effective reserve {mf.get('mm_reserve_effective', 0):,.0f}; free cash {mf['cash_free']:,.0f}; "
        f"blocked_by {h.get('blocked_by')}")
    out(f"- writes: the ladder at most {mw_hv} in a minute, all features {mw_all} (fake limiter 28 / minute; the "
        f"ladder's per-cycle max {max(per_cycle_w) if per_cycle_w else 0})")
    out("| min | markets | levels | $ resting | carve free | free cash | MM reserve eff. | paused | placed | pulled |")
    out("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        out(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:,.0f} | {r[4] or 0:,.0f} | {r[5] or 0:,.0f} | {r[6] or 0:,.0f} | "
            f"{r[7]} | {r[8]} | {r[9]} |")
    out()
    out("### Levels resting after 2 h (per market: side, price x shares, edge per $ at p, collateral)")
    out("| market | state | p | levels | collateral $ |")
    out("|---|---|---|---|---|")
    for e, lv in sorted(lad.items(), key=lambda kv: -sum(x[5] for x in kv[1])):
        p = pval(b, e)
        out(f"| {b.ex[e].label} | {b.st_key(e) or '-'} | {p:.3f} | " + " ".join(
            f"{'bid' if x[1] else 'ask'} {x[2]:.3f}x{x[3]:.0f} ({100 * x[4]:.1f}%)"
            for x in sorted(lv, key=lambda x: x[2]))
            + f" | {sum(x[5] for x in lv):,.0f} |")
    out()
    caps = st.get("state_caps") or {}
    out("### States vs the 15,000 cap (status state_caps: the states above 50% of it)")
    out("| state | collateral | cap | adds blocked (events) |")
    out("|---|---|---|---|")
    for s_, v in sorted(caps.items(), key=lambda kv: -kv[1]["collateral"]):
        out(f"| {s_} | {v['collateral']:,.0f} | {v['cap']:,.0f} | {v['blocked_adds']} |")
    out()
    STATE["B"] = dict(h=h, mf=mf, lad={e: list(v) for e, v in lad.items()}, caps=caps, mw=(mw_hv, mw_all))
    return api, b


# ============================================================================================ scenario C
def shift_books(api, b, step):
    """Longshots (p < 0.5): every bid and ask +step; favourites: every bid and ask -step (the tilt rises)."""
    for e, bk in api.books.items():
        p = pval(b, e)
        if p is None:
            continue
        d = step if p < 0.5 else -step
        for side in ("bids", "asks"):
            for lv in bk.get(side) or []:
                lv["price"] = round(min(0.995, max(0.005, lv["price"] + d)), 3)
    api.base_books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                      for e, v in api.books.items()}


def scenario_c(api, b):
    out("## Scenario C: a +3c tilt rise over 1 h on B (longshot books +1c, favourite books -1c at 0, 20 and 40 min)")
    st0 = P.status_of(b)
    ev0 = st0.get("ev_outcome")
    n0, t0, f0, a0 = len(P.CAP.lines), P.CLK.off, len(api.maker_fills), len(api.fills)
    lad0 = ladder_view(b, api)
    for _ in range(3):
        shift_books(api, b, 0.01)
        api.match_resting()
        run(b, 20, stage="C", hook=api.match_resting, refill_s=1e9)
    st = P.status_of(b)
    ev1 = st.get("ev_outcome")
    crashes = [m for _, lv, m in P.CAP.lines[n0:] if lv in ("ERROR", "CRITICAL") or "tick failed" in m]
    check("C: no tick crashed / no ERROR", not crashes, crashes[:3])
    fills = api.maker_fills[f0:]
    hv_f = [f for f in fills if (b.order_meta.get(f["oid"]) or {}).get("harvest")]
    lines = [m for _, _, m in P.CAP.lines[n0:] if m.startswith("HARVEST fill")]
    check("C: the ladder's resting levels filled as the tilt rose (maker fills)", len(hv_f) > 0, len(hv_f))
    all_hv = [f for f in api.fills[a0:] if (b.order_meta.get(f["orderId"]) or {}).get("harvest")]
    check("C: every harvest fill logged 'HARVEST fill ...' (journal)", len(lines) == len(all_hv),
          (len(lines), len(all_hv)))
    low, coll, edge_usd = [], 0.0, 0.0
    for f in hv_f:
        m = b.order_meta.get(f["oid"]) or {}
        p = m.get("p")
        c = f["qty"] * (f["px"] if f["bid"] else 1 - f["px"])
        e = f["qty"] * ((p - f["px"]) if f["bid"] else (f["px"] - p)) if p is not None else 0.0
        coll += c
        edge_usd += e
        if p is None or e / max(c, TOL) < b.cfg.harvest_min_edge - 1e-6:
            low.append((b.ex[f["eid"]].label, f["px"], p))
    check("C: every harvest fill at >= harvest_min_edge per $ vs p at placement", not low, low[:4])
    check("C: harvest fills are VALUE positions (no MM lot opened by them)",
          not any(f["eid"] in b.mm_lots and (b.order_meta.get(f["oid"]) or {}).get("harvest")
                  and abs(sum(x[0] for x in b.mm_lots.get(f["eid"], []))) > abs(f["pos_before"]) + 1 for f in hv_f))
    mc = (st.get("mm_carry_24h") or {}).get("fills") or {}
    check("C: mm_carry_24h has the 'harvest' fill class", mc.get("harvest", 0) >= 1 or not all_hv, mc)
    ladder_checks(b, api, "C (after the rise)")
    lad = ladder_view(b, api)
    hs = st.get("harvest") or {}
    STATE["C"] = dict(n=len(hv_f), shares=sum(f["qty"] for f in hv_f), coll=coll, edge=edge_usd, ev0=ev0, ev1=ev1,
                      other=len(fills) - len(hv_f), lad0=lad0, lad=lad, hs=hs, lines=lines)
    out(f"- harvest levels resting before the rise: {sum(len(v) for v in lad0.values())} in {len(lad0)} markets; "
        f"after: {sum(len(v) for v in lad.values())} in {len(lad)} markets")
    out(f"- harvest fills: {len(hv_f)} fills, {sum(f['qty'] for f in hv_f):,.0f} shares, ${coll:,.0f} of collateral, "
        f"edge at p ${edge_usd:,.0f} ({100 * edge_usd / max(coll, 1):.1f}% per $); status filled_24h "
        f"{hs.get('filled_24h')}, edge_filled_24h {hs.get('edge_filled_24h')}; other maker fills (the quoter's "
        f"resting quotes crossed by the moved books) {len(fills) - len(hv_f)}")
    if ev0 is not None and ev1 is not None:
        out(f"- EV outcome {ev0:,.0f} -> {ev1:,.0f} ({ev1 - ev0:+,.0f}: the fills' edge at p; Polymarket is flat)")
    for m in lines[:10]:
        out(f"    {m}")
    out()


# ============================================================================================ main
def main():
    if not os.path.exists(os.path.join(SNAP, "md.sqlite")):
        print(f"SKIP: no snapshot at {SNAP}")
        return 0
    t_start = _rt.time()
    STATE["S"] = P.load_snapshot()
    raw = json.load(open(STAGED))
    good, bad = M.validate_overrides({k: v for k, v in raw.items() if k not in RETIRED_KEYS}, M.Config())
    check("files: the staged file validates with no problem", not bad, bad)
    base142 = json.load(open(LIVE_142))
    check("files: the staged file = the live 14.2 file with value_sell_margin 0.01 + the P15 keys (ladder on, its "
          "defaults explicit, harvest_total_usd 10,000, state_max_usd 15,000)",
          {k: v for k, v in raw.items() if k not in P15_KEYS} == {**base142, "value_sell_margin": 0.01}
          and raw["tilt_harvest_ladder"] is True and raw["harvest_total_usd"] == 10000
          and raw["state_max_usd"] == 15000)
    out("# Package 15 dry run: the harvest ladder alone + the per-state cap, on the live state")
    out()
    out(f"Generated by `tests/test_p15_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {SNAP} (the "
        "4 Oct 15:56 ops snapshot) moved to the owner's live numbers by `tests/p14_1_state.py`. Each scenario builds "
        "the bot on the owner's LIVE file (64f27c8 + alloc_swap_sell_margin 0.03, alloc_swap_min_gain 0.05, "
        "value_sell_margin 0.01, alloc_mm_reserve 20000, mm_risk_reserve_wc 20000), then the staged file "
        "`deploy/package15/settings_override.harvest.json` lands through `check_overrides`. 60-s cycles on a "
        "simulated clock; a 28-writes-a-minute limiter in the fake.")
    out()
    body_start = len(OUT)
    scenario0()
    scenario_a()
    api, b = scenario_b()
    scenario_c(api, b)
    summary = summarise()
    OUT[body_start:body_start] = summary
    caveats()
    print(f"(all done in {_rt.time() - t_start:.0f} s)")
    return 0


def summarise():
    A, B, C = STATE.get("A") or {}, STATE.get("B") or {}, STATE.get("C") or {}
    s = ["## Summary"]
    s.append(f"- **Rhode Island on the snapshot: ${STATE.get('ri0', 0):,.0f}** of collateral at p (positions + our "
             "resting locks; " + ", ".join(f"{k} {q:+,.0f}" for k, (q, _) in (STATE.get("ri_pos") or {}).items())
             + "). The owner reports ~23k live on 6 Oct (the RI shorts grew since the 4 Oct snapshot); either way "
             "RI is the state the 15k cap binds first - with ~23k every RI add stops, the positions are kept.")
    ha, mfa = A.get("h") or {}, A.get("mf") or {}
    s.append(f"- **A, the live state as it is:** free cash {mfa.get('cash_free', 0):,.0f} after 2 h (the MM's own "
             f"reserve is 10,000) and value adds paused on the worst-case room -> the ladder rests "
             f"{ha.get('levels_resting', 0)} levels (${ha.get('collateral_resting', 0):,.0f}); it starts once the "
             "refill has the free cash above 10,000 and the room is back above 1.1 x 20,000 (both existing limits). "
             f"blocked_by {ha.get('blocked_by')}. The state cap works at once: the allocator's swap buys took RI "
             f"from {STATE.get('ri0', 0):,.0f} to "
             f"{((A.get('state_caps') or {}).get('RI') or {}).get('collateral', 0):,.0f} and stopped at the cap.")
    hb = B.get("h") or {}
    s.append(f"- **B, funded (+25k):** {hb.get('levels_resting', 0)} levels in {hb.get('markets', 0)} markets, "
             f"${hb.get('collateral_resting', 0):,.0f} resting of the 10,000 carve-out "
             f"(free ${(hb.get('carve') or {}).get('free', 0):,.0f}), MM effective reserve 10,000 held at every send, "
             f"every level >= 8% per $ at p, none through the book / our own orders / at our own price; writes "
             f"{(B.get('mw') or (0, 0))[0]} a minute at most for the ladder.")
    if C:
        s.append(f"- **C, +3c tilt rise (1 h):** {C['n']} harvest fills, {C['shares']:,.0f} shares, "
                 f"${C['coll']:,.0f} of collateral at ${C['edge']:,.0f} of edge at p "
                 f"({100 * C['edge'] / max(C['coll'], 1):.1f}% per $)"
                 + (f"; EV outcome {C['ev0']:,.0f} -> {C['ev1']:,.0f} ({C['ev1'] - C['ev0']:+,.0f})"
                    if C.get("ev0") is not None and C.get("ev1") is not None else "") + ".")
    s.append("- **Flags off:** identical to 64f27c8 (orders and status) on the live state; the staged file with the "
             "two P15 switches off = the live file.")
    s.append("")
    return s


def caveats():
    out("## Caveats")
    out("- The live state is REBUILT from the 4 Oct 15:56 snapshot (tests/p14_1_state.py), not copied from the server: "
        "Rhode Island is ~13-14k here, the owner sees ~23k live; the live order list and per-market depth are not "
        "reproduced.")
    out("- Other traders never trade with our resting orders except where scenario C moves their books through them; "
        "Polymarket is flat (the rise is in the tournament books only), so 'edge at p' uses the p at placement; the "
        "+3c move is an upper-bound fill scenario (every crossed level fills whole).")
    out("- The fake's 28-writes limiter counts, it does not refuse; the bot plans with writes_left as live.")
    out("- B's +25k cash is a fixture to show the ladder working: on the live state (A) the ladder waits for the "
        "refill (free cash above the MM's 10k) and for the risk room (value adds not paused) - by design, both are "
        "existing 64f27c8 limits the ladder keeps.")


if __name__ == "__main__":
    rc = main()
    if RESULTS and os.environ.get("P15_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
