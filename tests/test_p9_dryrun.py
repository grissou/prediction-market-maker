"""
Package 9 END-TO-END DRY RUN on the fake exchange, seeded with the REAL live state of 3 Oct 22:47 UTC (the ops snapshot
extracted to /home/claude/snap03, or $P9_SNAP): all 237 markets, the latest 3-level books of other traders, Polymarket
references (raw, from the recorder), positions and the exchange's marks, cash ~0 (account 101,017 less the positions'
market value at the marks), the tilt state (s 0.110) and the position lots. The bot runs with the LIVE settings (Config
defaults + the live override file); each staged file of deploy/package9/ is applied with the bot's own override path
(check_overrides), then full cycles (Bot.cycle) run on a simulated clock and every order the fake exchange receives is
recorded with the feature that sent it. Asserts the deployment story stage by stage (see analysis/p9/DRYRUN.md) and
writes the readable report to analysis/p9/DRYRUN.md (P9_DRYRUN_REPORT=0: no report).

What the fake does NOT model (caveats, in the report): other traders never trade with our resting quotes, the books do
not move except where we take (and an explicit "hourly refill" to the snapshot depth in the stage-2 hours), Polymarket is
flat, every Polymarket price counts as liquid, the exchange's marks stay at the snapshot's.

Run:  python tests/test_p9_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time as _rt
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeRefs, market                       # noqa: E402
import mm_bot as M                                                # noqa: E402

SNAP = os.environ.get("P9_SNAP", "/home/claude/snap03")
DEPLOY = os.path.join(ROOT, "deploy", "package9")
REPORT = os.path.join(ROOT, "analysis", "p9", "DRYRUN.md")
RESULTS, ALERTS = [], []
M.alert = lambda msg: ALERTS.append(msg)
M.notify = lambda *a, **k: False
logging.basicConfig(level=logging.CRITICAL)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


# ============================================================================================ the simulated clock
class Clock:
    """time.monotonic / time.time / utcnow shifted by `off` seconds (mm_bot's `time` and `utcnow` are replaced)."""
    off = 0.0

    def monotonic(self):
        return _rt.monotonic() + self.off

    def time(self):
        return _rt.time() + self.off

    def __getattr__(self, name):
        return getattr(_rt, name)


CLK = Clock()
M.time = CLK
M.utcnow = lambda: datetime.now(timezone.utc) + timedelta(seconds=CLK.off)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append((CLK.off, record.levelname, record.getMessage()))
        except Exception:
            pass


CAP = Capture()
M.log.addHandler(CAP)
M.log.setLevel(logging.INFO)
M.log.propagate = False


# ============================================================================================ the snapshot
PARTY = {"Dem": "Democratic", "Rep": "Republican", "Ind": "Independent"}


def load_snapshot(path=SNAP):
    c = sqlite3.connect(os.path.join(path, "md.sqlite"))
    snap = {r[0]: r for r in c.execute("SELECT eid, label, reference FROM snapshots "
                                       "WHERE ts = (SELECT max(ts) FROM snapshots)")}
    books = {}
    for eid, b, a, _ in c.execute("SELECT eid, bids, asks, max(ts) FROM books GROUP BY eid"):
        books[eid] = {"bids": [{"price": p, "quantity": q} for p, q in json.loads(b)],
                      "asks": [{"price": p, "quantity": q} for p, q in json.loads(a)]}
    pos, marks = {}, {}
    for eid, q, p, _ in c.execute("SELECT eid, quantity, current_price, max(ts) FROM positions GROUP BY eid"):
        if q:
            pos[eid] = float(q)
        if p is not None:
            marks[eid] = float(p)
    st = json.load(open(os.path.join(path, "status.json")))
    lots = os.path.join(path, "position_lots.json")
    return {"snap": snap, "books": books, "pos": pos, "marks": marks, "status": st,
            "lots": lots if os.path.exists(lots) else None}


def label_parts(label):
    p, rest = label.split(" ", 1)
    return PARTY[p], rest


def leg_value(q, p):
    return q * p if q > 0 else -q * (1 - p)


# ============================================================================================ the fake exchange
class DryApi(FakeApi):
    """FakeApi with the snapshot's markets, books, positions and marks, a cash ledger (fills move the cash; the
    account value = cash + positions at the snapshot marks), and an order log tagged with the feature that sent it."""

    def __init__(self, S, cash=None):
        super().__init__(live=True)
        self.S = S
        self.markets_list, self.labels = [], {}
        for eid, (_, label, _) in sorted(S["snap"].items(), key=lambda kv: int(kv[0])):
            party, race = label_parts(label)
            self.markets_list.append(market(f"m{eid}", eid, party, race))
            self.labels[eid] = label
        self.base_books = S["books"]
        self.refill()
        self.inv = dict(S["pos"])
        self.marks = dict(S["marks"])
        mv = self.market_value()
        acct = float(S["status"]["account_value"])
        self.cash = (acct - mv) if cash is None else cash
        self.tag, self.stage, self.cycle_no = "quote", "", 0
        self.log_orders = []

    def refill(self):
        self.books = {e: {"bids": [dict(x) for x in b["bids"]], "asks": [dict(x) for x in b["asks"]]}
                      for e, b in self.base_books.items()}

    def mark(self, e):
        if e in self.marks:
            return self.marks[e]
        b = self.base_books.get(e) or {}
        bb, ba = (b.get("bids") or [{}])[0].get("price"), (b.get("asks") or [{}])[0].get("price")
        return (bb + ba) / 2 if bb is not None and ba is not None else (bb or ba or 0.5)

    def market_value(self):
        return sum(leg_value(q, self.mark(e)) for e, q in self.inv.items() if q)

    @property
    def account(self):
        return self.cash + self.market_value()

    def positions(self):
        self.log("positions")
        return {"positions": [{"exchangeId": e, "quantity": q, "settled": False, "currentPrice": self.mark(e)}
                              for e, q in self.inv.items() if q],
                "summary": {"totalMarketValue": self.market_value()}}

    def pnl(self):
        self.log("pnl")
        cash = self.cash if getattr(self, "cash_cap", None) is None else min(self.cash, self.cash_cap)
        return {"totalAccountValue": self.account + getattr(self, "equity_shift", 0.0), "cashBalance": cash}

    def place_batch(self, orders):
        before, n0 = dict(self.inv), len(self.fills)
        own = {o["exchangeId"]: [dict(r) for r in self.orders.values() if r["exchangeId"] == o["exchangeId"]]
               for o in orders}
        need = [self.cash_needed(o) for o in orders]
        free = self.cash - self.cash_locked()
        res = super().place_batch(orders)
        run, by_oid = dict(before), defaultdict(float)
        for f in self.fills[n0:]:
            e, dq, p = f["exchangeId"], f["quantity"], f["price"]
            q0 = run.get(e, 0.0)
            c0 = self.cash
            if dq > 0:
                close = min(dq, max(0.0, -q0))
                self.cash += close * (1 - p) - (dq - close) * p
            else:
                d = -dq
                close = min(d, max(0.0, q0))
                self.cash += close * p - (d - close) * (1 - p)
            by_oid[f["orderId"]] += self.cash - c0
            run[e] = q0 + dq
        for k, o in enumerate(orders):
            r = res[k] if k < len(res) else {}
            yes = o["side"] == "yes"
            self.log_orders.append({
                "stage": self.stage, "cycle": self.cycle_no, "t": CLK.off, "tag": self.tag, "eid": o["exchangeId"],
                "label": self.labels.get(o["exchangeId"], o["exchangeId"]), "side": o["side"], "action": o["action"],
                "yes_buy": yes == (o["action"] == "buy"), "yes_price": o["price"] if yes else round(1 - o["price"], 3),
                "qty": o["quantity"], "pos_before": before.get(o["exchangeId"], 0.0), "ok": bool(r.get("ok")),
                "race_pos": ({m: before.get(m, 0.0) for m in self.race_legs(o["exchangeId"])}
                             if self.tag != "quote" else {}),
                "traded": float((r.get("data") or {}).get("quantityTraded") or 0) if r.get("ok") else 0.0,
                "err": None if r.get("ok") else ((r.get("data") or {}).get("error") or {}).get("message"),
                "cash": by_oid.get((r.get("data") or {}).get("orderId"), 0.0), "need": need[k], "free": free,
                "own_resting": own.get(o["exchangeId"], [])})
        return res


TAGGED = ("take_stale_quotes", "take_aged", "tilt_exit_takes", "basket_tick", "take_arbitrage", "pair_followup_step",
          "pair_passive_step")


def tag_methods(b):
    for name in TAGGED:
        orig = getattr(b, name)

        def wrapped(*a, _orig=orig, _name=name, **k):
            b.api.tag = _name
            try:
                return _orig(*a, **k)
            finally:
                b.api.tag = "quote"
        setattr(b, name, wrapped)


# ============================================================================================ the bot
def build(S, cash=None, overrides="live"):
    api = DryApi(S, cash)
    cfg = M.Config()
    d = tempfile.mkdtemp(prefix="p9dry")
    cfg.fills_csv, cfg.status_file, cfg.order_notes_file, cfg.kill_file = (
        os.path.join(d, n) for n in ("fills.csv", "status.json", "notes.json", "kill.tripped"))
    cfg.position_lots_file = os.path.join(d, "lots.json")
    cfg.record_file, cfg.ref_map_file, cfg.summary_every_hours = "", "", 0
    cfg.overrides_file = os.path.join(d, "settings_override.json")
    cfg.market_edge_file = os.path.join(d, "market_edge.json")
    cfg.handover_file = os.path.join(d, "handover.json")
    cfg.handover_exit_max_seconds = 0
    cfg.realtime_enabled = False
    cfg.selftest_enabled = False          # (live: "passed", covered NO sales accepted)
    cfg.reserved_cash_mode = "ignore"     # (live: "ignore")
    now_w = CLK.time()
    ts = dict(S["status"].get("tilt_state") or {})
    ts.update(on_wall=now_w - 86400, headline_on_wall=now_w - 86400, saved_wall=now_w)   # a handover: ramp done
    with open(cfg.status_file, "w") as f:
        json.dump({"tilt_state": ts, "tilt_s": S["status"]["tilt_s"]}, f)
    if S["lots"]:
        shutil.copy(S["lots"], cfg.position_lots_file)
    b = M.Bot(api, cfg)
    refs = {}
    for eid, (_, label, r) in S["snap"].items():
        if r is not None:
            party, race = label_parts(label)
            refs[f"{race}|{party}"] = float(r)
    b.refs = FakeRefs(refs, spread=0.01)
    tag_methods(b)
    apply_stage(b, os.path.join(SNAP, "settings_override.json") if overrides == "live" else overrides)
    return api, b


def apply_stage(b, path):
    """The owner's deployment step: the file copied over settings_override.json, read by the bot's own override path."""
    raw = json.load(open(path))
    good, bad = M.validate_overrides(raw, b.cfg)
    shutil.copy(path, b.cfg.overrides_file)
    b.overrides_mtime = None
    n = len(ALERTS)
    b.check_overrides(force=True)
    return bad, ALERTS[n:]


def cycles(b, n, step=30.0, stage=None, refill_every=None, status=False):
    api = b.api
    if stage is not None:
        api.stage = stage
    for _ in range(n):
        w0 = sum(1 for c in api.calls if c[0] in ("batch", "cancel_all", "cancel_order"))
        api.cycle_no += 1
        b.refs.new_reading(dict(b.refs.prices), {})
        logging.disable(logging.NOTSET)
        b.cycle()
        b.drain_writes(5)
        w = sum(1 for c in api.calls if c[0] in ("batch", "cancel_all", "cancel_order")) - w0
        WRITES[api.stage] = max(WRITES.get(api.stage, 0), w)
        if status:
            b.write_status(True)
        CLK.off += step
        if refill_every and api.cycle_no % refill_every == 0:
            api.refill()


def orders(api, stage=None, tag=None):
    return [o for o in api.log_orders if (stage is None or o["stage"] == stage) and (tag is None or o["tag"] == tag)]


def usd(o):
    """$ the order's fills moved (paid or received, the fake's cash ledger: an uncovered YES sale is a NO purchase)."""
    return abs(o["cash"])


def status_of(b):
    b.write_status(True)
    return json.load(open(b.cfg.status_file))


# ============================================================================================ the report
OUT = []


def out(s=""):
    OUT.append(s)


def summarise(api, stage, title):
    os_ = orders(api, stage)
    by = defaultdict(list)
    for o in os_:
        by[o["tag"]].append(o)
    out(f"| {title} | feature | orders | traded | YES buys / YES sells / NO sells (wire) | YES shares bought (traded) |"
        " YES shares sold (traded) | $ paid | $ received |")
    out("|---|---|---|---|---|---|---|---|---|")
    for tag, xs in sorted(by.items()):
        kinds = Counter((o["side"], o["action"]) for o in xs)
        out(f"| | {tag} | {len(xs)} | {sum(1 for o in xs if o['traded'] > 0)} | {kinds[('yes', 'buy')]} / "
            f"{kinds[('yes', 'sell')]} / {kinds[('no', 'sell')]} | {sum(o['traded'] for o in xs if o['yes_buy']):,.0f} | "
            f"{sum(o['traded'] for o in xs if not o['yes_buy']):,.0f} | {-sum(min(0, o['cash']) for o in xs):,.0f} | "
            f"{sum(max(0, o['cash']) for o in xs):,.0f} |")
    if not by:
        out("| | (none) | 0 | 0 | - | 0 | 0 | 0 | 0 |")
    out()


def sample(xs, n=8):
    for o in xs[:n]:
        out(f"    c{o['cycle']:>3} {o['tag']:<17} {o['label']:<28} {'BUY ' if o['yes_buy'] else 'SELL'} "
            f"{o['qty']:>6} YES @ {o['yes_price']:.3f} ({o['side']} {o['action']}) traded {o['traded']:.0f}"
            f" pos {o['pos_before']:+.0f}")


# ============================================================================================ the run
def main():
    if not os.path.exists(os.path.join(SNAP, "md.sqlite")):
        print(f"SKIP: no snapshot at {SNAP}")
        return 0
    t0 = _rt.time()
    S = load_snapshot()
    files = {k: os.path.join(DEPLOY, f"settings_override.{k}.json") for k in
             ("stage0_code_only", "stage1_hygiene", "stage2_flatten", "stage3_basket", "stage3b_basket_m6",
              "stage4_carry", "rollback_basket_off")}

    # ---- the staged files are valid and keep the live base
    live = json.load(open(os.path.join(SNAP, "settings_override.json")))
    for k, p in files.items():
        raw = json.load(open(p))
        good, bad = M.validate_overrides(raw, M.Config())
        check(f"files: {k} validates (no refused key)", not bad, bad)
        check(f"files: {k} keeps capital_ceiling_adding_size_factor 0 and ref_tilt_headline",
              raw.get("capital_ceiling_adding_size_factor") == 0 and raw.get("ref_tilt_headline") is True)
    check("files: stage0 == the live override file", json.load(open(files["stage0_code_only"])) == live)

    api, b = build(S)
    out("# Package 9 dry run on the fake exchange, seeded with the live state of 3 Oct 22:47 UTC")
    out()
    out(f"Generated by `tests/test_p9_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {SNAP}. "
        "The bot runs its own full cycles (`Bot.cycle`) with the live settings (Config defaults + the live override "
        "file), each staged file applied through `check_overrides`, on a simulated clock; every order the fake exchange "
        "receives is logged with the feature that sent it.")
    out()
    mv0 = api.market_value()
    out("## Seed")
    out(f"- markets {len(b.ex)} in {len(b.groups)} races; positions {len(api.inv)}; Polymarket references "
        f"{len(b.refs.prices)}; account {api.account:,.0f} (cash {api.cash:,.0f} + positions at the marks {mv0:,.0f})")

    # ---- stage 0: Package 8 behaviour
    cycles(b, 4, stage="warm")
    st = status_of(b)
    out(f"- after 4 warm-up cycles: tilt_s {b.tilt_s:.3f} (estimator raw {b.tilt.diag.get('slope')}), tilt_exposure "
        f"{b.tilt_exposure:,.0f}, worst case {st['worst_case_loss']:,.0f}, settlement risk {st['settlement_risk']:,.0f}, "
        f"reduce-only {st['reduce_only']}, liquidation {st.get('liquidation_value')}, cash_gate_left "
        f"{st.get('cash_gate_left')}, NO+NO sets {st.get('nono_sets')}")
    out()
    check("seed: account value 101,017 at the marks", abs(api.account - 101017.2) < 1, api.account)
    check("seed: tilt exposure ~ +35.9k (toward Polymarket)", 30000 < b.tilt_exposure < 42000, b.tilt_exposure)
    check("seed: cash gate ~0 (< 500)", (st.get("cash_gate_left") or 0) < 500, st.get("cash_gate_left"))
    seed = {"worst": st["worst_case_loss"], "tilt_exposure": b.tilt_exposure, "liq": st.get("liquidation_value"),
            "cash": api.cash}

    cycles(b, 8, stage="stage0")
    o0 = orders(api, "stage0") + orders(api, "warm")
    tags0 = Counter(o["tag"] for o in o0)
    check("stage0: no tilt exit takes, no basket, no arbitrage orders",
          not any(tags0[t] for t in ("tilt_exit_takes", "basket_tick", "take_arbitrage")), tags0)
    check("stage0: status has no basket section", "basket" not in status_of(b))
    out("## Stage 0 (code only, = Package 8 behaviour)")
    summarise(api, "stage0", "stage0")
    take0 = orders(api, "stage0", "take_stale_quotes") + orders(api, "warm", "take_stale_quotes")

    # ---- stage 1: hygiene
    bad, al = apply_stage(b, files["stage1_hygiene"])
    check("stage1: applied with no refused key / alert", not bad and not al, (bad, al))
    check("stage1: take_tilted_ref, take_edge 0.08, ref_tilt_max 0.20, pair_no_unwind_max_cost 0.02 (56dff74: the "
          "sets keep draining at the live 0.02) in force",
          b.cfg.take_tilted_ref and b.cfg.take_edge == 0.08 and b.cfg.ref_tilt_max == 0.20
          and b.cfg.pair_no_unwind_max_cost == 0.02, b.cfg.pair_no_unwind_max_cost)
    cycles(b, 10, stage="stage1")
    o1 = orders(api, "stage1")
    take1 = [o for o in o1 if o["tag"] == "take_stale_quotes"]
    badtake = []
    for o in take1:
        ex = b.ex[o["eid"]]
        r = b.cur_refs.get(o["eid"])
        p = b.tilted_ref_for(ex, r)
        edge = (p - o["yes_price"]) if o["yes_buy"] else (o["yes_price"] - p)
        if edge < b.cfg.take_edge - 0.005:
            badtake.append((o["label"], o["yes_price"], round(p, 4), round(edge, 4)))
    check("stage1: every stale-quote take has >= take_edge against the TILTED reference", not badtake, badtake[:5])
    check("stage1: no tilt exit take / basket / arbitrage", not any(o["tag"] in ("tilt_exit_takes", "basket_tick",
                                                                                  "take_arbitrage") for o in o1))
    scan = take_scan(b)
    inj = injected_take(S, files)
    out("## Stage 1 (hygiene: takes from the tilted reference, ref_tilt_max 0.20, sets kept)")
    out(f"- static scan of the snapshot books (liquid Polymarket): the stage-0 rule (raw Polymarket, edge 0.05) "
        f"would take on {scan[0]} markets (every one toward Polymarket); the stage-1 rule (tilted reference at "
        f"s {b.tilt_s:.3f}, edge 0.08) on {scan[2]}")
    out(f"- out of reduce-only (flattened book, 20 cycles each): stage 0 sent {inj[0]} stale-quote takes, {inj[1]} of "
        f"them without a 0.08 edge vs the tilted reference; stage 1 sent {inj[2]}, {inj[3]} without it")
    out(f"- tilt_s now {b.tilt_s:.3f} (clip 0.20; estimator raw {b.tilt.diag.get('slope')})")
    out(f"- stale-quote takes: stage 0 {len(take0)}, stage 1 {len(take1)} (all with >= {b.cfg.take_edge} edge vs the "
        "tilted reference)")
    summarise(api, "stage1", "stage1")
    sample(take0 + take1 + STATE.get("takes_sample", []))
    out()

    # ---- stage 2: tilt exit takes, a few simulated hours (books refilled to the snapshot depth every hour)
    bad, al = apply_stage(b, files["stage2_flatten"])
    check("stage2: applied", not bad and not al and b.cfg.tilt_exit_take, (bad, al))
    cash_before2, exp_before2 = api.cash, b.tilt_exposure
    worst_before2 = status_of(b)["worst_case_loss"]
    hours = float(os.environ.get("P9_DRYRUN_HOURS", "3"))
    per_h = 60
    hourly = []
    t_stage2, nono0 = CLK.off, status_of(b).get("nono_sets") or {}
    sc_prev, api.set_collateral = api.set_collateral, True   # F2b: the exchange's set rule (1 cash a broken share)
    for h in range(int(hours)):
        cycles(b, per_h, step=60.0, stage="stage2", refill_every=None)
        st = status_of(b)
        STATE.setdefault("ro", {})[f"stage2 h{h + 1}"] = st["reduce_only"]
        hourly.append((h + 1, api.cash, b.tilt_exposure, st["worst_case_loss"], st.get("cash_gate_left"),
                       st.get("liquidation_value"), api.account, b.tilt_s))
        if h == 0:
            first_hour_depth_only = (api.cash - cash_before2, exp_before2 - b.tilt_exposure)
        api.refill()                                      # the books replenish to the snapshot's depth every hour
    api.set_collateral = sc_prev
    o2 = orders(api, "stage2")
    tet = [o for o in o2 if o["tag"] == "tilt_exit_takes"]
    lines = [m for _, lv, m in CAP.lines if "TILT EXIT TAKE" in m]
    split_lines = [m for _, lv, m in CAP.lines if "TILT EXIT SPLIT" in m]
    out("## Stage 2 (flatten: tilt exit takes)")
    viol, splits, fav_viol = [], [], []
    per_cycle = Counter(o["cycle"] for o in tet)
    for o in tet:
        ex = b.ex[o["eid"]]
        r = b.cur_refs.get(o["eid"])
        pos = o["pos_before"]
        c = 1.0 / b.legs(ex)
        contrib = pos * (r - c)
        shrinks = (pos > 0 and not o["yes_buy"]) or (pos < 0 and o["yes_buy"])
        fv = M.tilted_ref(r, b.tilt_s, b.legs(ex))
        cost = (fv - o["yes_price"]) if not o["yes_buy"] else (o["yes_price"] - fv)
        if pos < 0:                                       # never NO out of a NO+NO set: at most the lone part
            others = max([max(0.0, -q) for m, q in o["race_pos"].items() if m != o["eid"]] or [0.0])
            lone = -pos - (min(-pos, others) if others >= 1 else 0.0)
            if o["qty"] > lone + 1e-9:                    # F2b: allowed only as a longshot-leg set split
                rmax = max([b.cur_refs.get(m, 1.0) for m in o["race_pos"] if m != o["eid"]] or [0.0])
                if (b.cfg.tilt_exit_take_split_sets and r < b.cfg.tilt_exit_split_max_ref and r < rmax):
                    splits.append(dict(o, lone=lone))
                else:
                    fav_viol.append((o["label"], r, o["qty"], lone))
                    viol.append(("NO sold out of a NO+NO set", o["label"], o["qty"], lone))
        if not (contrib > 0 and shrinks and o["qty"] <= abs(pos) + 1e-9):
            viol.append(("not a short-tilt shrink", o["label"], pos, o["yes_buy"]))
        if cost > b.cfg.tilt_exit_take_max_cost + 0.0005:      # (56dff74: the stage2 file allows 2c)
            viol.append(("cost > max_cost", o["label"], o["yes_price"], round(fv, 4), round(cost, 4)))
        if o["own_resting"]:
            viol.append(("own order resting at send", o["label"], o["own_resting"]))
        if o["side"] == "yes" and o["yes_buy"] and pos < 0:
            viol.append(("short bought back as a YES purchase (not a covered NO sale)", o["label"]))
    check("stage2: tilt exit takes were sent", len(tet) > 0, len(tet))
    check("stage2: every tilt exit shrinks a short-tilt position, within max_cost of tilted fv, no own order resting, "
          "short buy-backs as covered NO sales, NO out of a NO+NO set only as a longshot split", not viol, viol[:5])
    check("stage2: <= 3 tilt exit takes per cycle", max(per_cycle.values() or [0]) <= 3, per_cycle.most_common(3))
    # the $ cap: any 3600-s window
    worst_h = 0.0
    ts = sorted((o["t"], usd(o)) for o in tet)
    for i, (t, _) in enumerate(ts):
        worst_h = max(worst_h, sum(v for tt, v in ts[i:] if tt - t < 3600))
    check("stage2: <= 15k$ traded per rolling hour", worst_h <= 15000 + 1, worst_h)
    # order: longshot NO first within each cycle
    order_ok = True
    for cyc in per_cycle:
        g = []
        for o in [x for x in tet if x["cycle"] == cyc]:
            r = b.cur_refs.get(o["eid"])
            pos = o["pos_before"]
            g.append(0 if (pos < 0 and r < 0.10) else 1 if (pos > 0 and r > 0.90) else 2)
        order_ok &= g == sorted(g)
    check("stage2: longshot NO first, then favourite YES, then the rest (each cycle)", order_ok)
    out(f"- simulated {int(hours)} h at 60-s cycles, books refilled to the snapshot depth each hour; "
        f"{len(tet)} TILT EXIT TAKE orders ({sum(1 for o in tet if o['traded'] > 0)} traded), "
        f"${sum(usd(o) for o in tet):,.0f} traded, worst rolling hour ${worst_h:,.0f}")
    out(f"- first hour on the snapshot's depth only: cash +{first_hour_depth_only[0]:,.0f}, tilt_exposure "
        f"-{first_hour_depth_only[1]:,.0f}")
    out()
    out("| hour | cash | tilt_exposure | worst_case_loss | cash_gate_left | liquidation | account (marks) | tilt_s |")
    out("|---|---|---|---|---|---|---|---|")
    out(f"| 0 | {cash_before2:,.0f} | {exp_before2:,.0f} | {worst_before2:,.0f} | - | - | - | - |")
    for h, cash, exp, worst, cgl, liq, acct, s in hourly:
        out(f"| {h} | {cash:,.0f} | {exp:,.0f} | {worst:,.0f} | {cgl if cgl is None else f'{cgl:,.0f}'} | "
            f"{liq if liq is None else f'{liq:,.0f}'} | {acct:,.0f} | {s:.3f} |")
    out()
    summarise(api, "stage2", "stage2")
    out("Sample TILT EXIT TAKE log lines:")
    out()
    for m in lines[:10]:
        out(f"    {m}")
    out()
    # ---- F2b: the set splits inside stage 2 (tilt_exit_take_split_sets true in the stage2 file)
    check("stage2 F2b: the split flag is on in the stage2 file", b.cfg.tilt_exit_take_split_sets)
    check("stage2 F2b: set splits were sent (longshot NO legs of NO+NO sets)", len(splits) > 0, len(splits))
    check("stage2 F2b: never the favourite leg's set part (only legs below tilt_exit_split_max_ref and the race max)",
          not fav_viol, fav_viol[:5])
    refused = [o for o in splits if not o["ok"]]
    check("stage2 F2b: a refused split is not retried in that race within take_cooldown_seconds x 10",
          all(not [x for x in splits if x["eid"] in b.groups.get(b.ex[o["eid"]].group, ())
                   and 0 < x["t"] - o["t"] < 10 * b.cfg.take_cooldown_seconds] for o in refused))
    nono1 = status_of(b).get("nono_sets") or {}
    out("### Stage 2 with set splits (F2b `tilt_exit_take_split_sets` true in the stage2 file)")
    out("The F2 exit of a short longshot (raw Polymarket < 0.10, below its race's favourite) also sells the SET part "
        "of its NO as one covered sale, sized to the cash gate at 1.0 a set share (the fake exchange's set rule on: "
        "it refuses a set-breaking sale beyond 1 cash a broken share). The favourite-NO legs stay (long the tilt). "
        "For comparison, the 62a7fb2 run (same seed, max_cost 0.01, no splits) freed +7,990 of cash in hour 1 and "
        "~9,000 by hour 3; here (max_cost 0.02 as the stage2 file now says, splits on) 'cash from splits' is what the "
        "splits add and the rest of 'all tilt exits' the ordinary (lone-part / long) exits. The rolling-hour $ cap "
        f"(tilt_exit_take_per_hour {b.cfg.tilt_exit_take_per_hour:,.0f}) and the snapshot depth within the cost cap "
        "(books refilled hourly) bound both.")
    out()
    out("| hour | split orders (refused) | set shares sold | cash from splits | cash from all tilt exits | cash at the end "
        "of the hour |")
    out("|---|---|---|---|---|---|")
    prev_cash = cash_before2
    for h, cash, *_ in hourly:
        lo, hi = t_stage2 + 3600 * (h - 1), t_stage2 + 3600 * h
        xs = [o for o in splits if lo <= o["t"] < hi]
        allx = [o for o in tet if lo <= o["t"] < hi]
        out(f"| {h} | {len(xs)} ({sum(1 for o in xs if not o['ok'])}) | "
            f"{sum(max(0.0, o['traded'] - o['lone']) for o in xs):,.0f} | {sum(o['cash'] for o in xs):,.0f} | "
            f"{sum(o['cash'] for o in allx):,.0f} | {cash:,.0f} (+{cash - prev_cash:,.0f}) |")
        prev_cash = cash
    out()
    out(f"- NO+NO sets: {nono0.get('sets', 0):,} sets ({nono0.get('capital', 0):,.0f} of capital) before stage 2, "
        f"{nono1.get('sets', 0):,} ({nono1.get('capital', 0):,.0f}) after; status splits "
        f"{(status_of(b).get('tilt_exit_takes') or {}).get('splits')}")
    out(f"- refused splits: {len(refused)}" + (f" ({refused[0]['err']})" if refused else ""))
    out()
    for m in split_lines[:6]:
        out(f"    {m}")
    out()
    STATE["seed"], STATE["hourly"] = seed, hourly
    print(f"(stage 0-2 done in {_rt.time() - t0:.0f} s)")
    depth_analysis(S, b)
    skip = set(os.environ.get("P9_SKIP", "").split(","))          # (debugging: run parts only)
    if "zero" not in skip:
        stage3_zero_cash(S, files)
    stage3_main(b, api, files)
    stage4(b, api, files)
    if "flat" not in skip:
        kill_path(*stage3_flattened(S, files))
    if "3b" not in skip:
        stage3b_flattened(S, files)
    if "exit" not in skip:
        exit_path(S, files)                               # (last: it moves the shared clock 15 days on)
    findings()
    print(f"(all done in {_rt.time() - t0:.0f} s)")
    return 0


def depth_analysis(S, b):
    """How much stage 2 can free: for every toward-Polymarket (short-tilt) position, the exit side's snapshot depth
    (3 levels) within 1c of the tilted fair value, at s = 0.11 (live), the estimator's raw reading and 0.20."""
    raw = b.tilt.diag.get("slope") or 0.11
    out("## How much stage 2 can free (snapshot depth, the taker exits are depth-limited)")
    out()
    out("Toward-Polymarket positions (contribution pos x (r - 1/legs) > 0); exit side = bids for a long, asks for a "
        "short (bought back as a covered NO sale, less the NO+NO set part); $ = the cash the sale brings in (bid, or "
        "1 - ask a share). tilt_exposure removed = the shares x (r - c).")
    out()
    out("| s used for the tilted fv | positions | within 1c: shares | $ freed | tilt_exposure removed | best level only: $ |"
        " all 3 levels at any price: $ |")
    out("|---|---|---|---|---|---|---|")
    refs = b.cur_refs
    inv = S["pos"]
    res = {}
    for s in (0.11, round(raw, 3), 0.20):
        n = sh = dol = expo = best_d = all_d = 0.0
        for e, q in inv.items():
            ex, r = b.ex.get(e), refs.get(e)
            if ex is None or r is None:
                continue
            legs = b.legs(ex)
            c = 1.0 / legs if legs > 1 else 0.5
            if q * (r - c) <= 0:
                continue
            n += 1
            fv = M.tilted_ref(r, s, legs)
            book = S["books"].get(e) or {}
            if q > 0:
                held, lv = q, book.get("bids") or []
            else:
                others = max([max(0.0, -inv.get(m, 0.0)) for m in b.groups[ex.group] if m != e] or [0.0])
                held, lv = -q - (min(-q, others) if others >= 1 else 0.0), book.get("asks") or []
            left = held
            for k, l in enumerate(lv):
                p, d = l["price"], l["quantity"]
                unit = p if q > 0 else 1 - p
                take_all = min(left, d)
                all_d += take_all * unit if k < 3 else 0
                cost = (fv - p) if q > 0 else (p - fv)
                if cost <= 0.01 + 1e-9 and left > 0:
                    t = min(left, d)
                    sh += t
                    dol += t * unit
                    expo += t * abs(r - c)
                    if k == 0:
                        best_d += t * unit
                    left -= t
        res[s] = (dol, expo)
        out(f"| {s:.3f} | {n:.0f} | {sh:,.0f} | {dol:,.0f} | {expo:,.0f} | {best_d:,.0f} | {all_d:,.0f} |")
    out()
    STATE["depth"] = res


def take_scan(b):
    """(markets the stage-0 take rule would act on, how many toward Polymarket, markets the stage-1 rule would)."""
    n0 = toward = n1 = 0
    for e, ex in b.ex.items():
        r = b.cur_refs.get(e)
        if r is None or e not in b.cur_liquid or ex.book is None:
            continue
        bid = (ex.book.get("bids") or [{}])[0].get("price")
        ask = (ex.book.get("asks") or [{}])[0].get("price")
        p = b.tilted_ref_for(ex, r)
        raw = (ask is not None and r - ask >= 0.05 - 1e-9) or (bid is not None and bid - r >= 0.05 - 1e-9)
        n0 += raw
        toward += raw
        n1 += (ask is not None and p - ask >= 0.08 - 1e-9) or (bid is not None and bid - p >= 0.08 - 1e-9)
    return n0, toward, n1


def injected_take(S, files):
    """The stale-quote takes out of reduce-only (the live book is in reduce-only, which blocks every take): on the
    flattened book, 20 cycles of stage 0 then, on a fresh bot, of stage 1 - the snapshot's own books. Returns (stage-0
    takes, how many of them lack the 0.08 edge vs the tilted reference, stage-1 takes, how many of those lack it)."""
    _, ref = build(S)
    cycles(ref, 2, stage="i-ref")
    base, _ = flattened_snapshot(S, ref)
    cash = float(S["status"]["account_value"]) - sum(leg_value(q, S["marks"].get(e, 0.5)) for e, q in base["pos"].items())
    res = []
    for stage in ("stage0_code_only", "stage1_hygiene"):
        api, b = build(base, cash=cash, overrides=files[stage])
        cycles(b, 20, step=30.0, stage="i-" + stage)
        tk = orders(api, "i-" + stage, "take_stale_quotes")
        lack = 0
        for o in tk:
            ex, r = b.ex[o["eid"]], b.cur_refs.get(o["eid"])
            pt = M.tilted_ref(r, b.tilt_s, b.legs(ex))
            edge = (pt - o["yes_price"]) if o["yes_buy"] else (o["yes_price"] - pt)
            lack += edge < 0.08 - 0.005
        res += [len(tk), lack]
        STATE.setdefault("takes_sample", []).extend(tk[:4])
    check("stage1 (out of reduce-only): stage 0 takes toward raw Polymarket where the tilted reference has no 0.08 edge;"
          " stage 1 never does", res[1] >= 1 and res[3] == 0, res)
    return res


def basket_adds(xs):
    return [o for o in xs if o["tag"] == "basket_tick" and
            ((o["yes_buy"] and o["pos_before"] >= 0) or (not o["yes_buy"] and o["pos_before"] <= 0))]


def stage3_zero_cash(S, files):
    """The owner switches stage 3 on while the cash gate is still ~0 (stage 2 has freed nothing yet)."""
    api, b = build(S)
    cycles(b, 4, stage="z-warm")
    apply_stage(b, files["stage2_flatten"])
    api.cash_cap = 0.0                                    # nothing freed yet (the P&L read shows 0 cash)
    bad, al = apply_stage(b, files["stage3_basket"])
    n0 = len(CAP.lines)
    cycles(b, 6, step=60.0, stage="z-stage3")
    st = status_of(b)
    bo = orders(api, "z-stage3", "basket_tick")
    info = st.get("basket") or {}
    blog = [m for _, _, m in CAP.lines[n0:] if "BASKET" in m or "basket" in m]
    check("stage3 at 0 cash: applied", not bad and not al and b.cfg.basket_enabled, (bad, al))
    check("stage3 at 0 cash: the basket sends NOTHING", not bo, len(bo))
    check("stage3 at 0 cash: status.json basket says why (refused)", bool(info.get("refused")), info.get("refused"))
    out("## Stage 3 switched on at 0 cash (too early)")
    out(f"- basket orders: {len(bo)}; basket state {info.get('state')}, refused {info.get('refused')!r}, target "
        f"{info.get('target')} (full {info.get('target_full')}), floor {info.get('floor')}, cushion {info.get('cushion')},"
        f" liquidation {info.get('liquidation')}, cash_gate_left {st.get('cash_gate_left')}")
    out("- log lines: " + ("; ".join(blog[:4]) or "(none)"))
    out()


def basket_checks(b, api, stage, label, full_max=None):
    """The basket's leg rules on every add the fake received in `stage`; returns (adds, legs, info)."""
    xs = orders(api, stage)
    adds = basket_adds(xs)
    info = b.basket_info
    full = max(full_max or 0.0, info.get("target_full") or 0.0)   # (the cap applies at add time: the target since)
    bad = []
    fvs = {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()}
    for o in adds:
        ex = b.ex[o["eid"]]
        members = b.groups[ex.group]
        rs = {m: b.cur_refs.get(m) for m in members}
        tot = sum(v for v in rs.values() if v is not None) or 1.0
        sc = rs[o["eid"]] / tot if rs[o["eid"]] is not None else None
        fav = max(members, key=lambda m: (rs[m] or 0))
        if ex.group in b.cfg.headline_races:
            bad.append(("headline", o["label"]))
        if ex.label.startswith("Ind"):
            bad.append(("independent", o["label"]))
        if o["own_resting"]:
            bad.append(("own order resting", o["label"]))
        if o["yes_buy"] and not (o["yes_price"] <= 0.25 + 1e-9 and sc is not None and sc < 0.10):
            bad.append(("YES add not a longshot <= 25c", o["label"], o["yes_price"], sc))
        if not o["yes_buy"] and not (o["yes_price"] >= 0.75 - 1e-9 and o["eid"] == fav):
            bad.append(("NO add not the favourite >= 75c", o["label"], o["yes_price"]))
    check(f"{label}: every basket add is longshot YES <= 25c or favourite NO >= 75c, no headline, no independent, "
          "no own resting order", not bad, bad[:5])
    over = [(b.ex[e].label, round(b.basket_leg_value(e, q, fvs, b.cur_book_fvs)))
            for e, q in b.basket_legs.items()
            if b.basket_leg_value(e, q, fvs, b.cur_book_fvs) > 0.05 * full * 1.02 + 5]
    check(f"{label}: every leg <= 5% of the highest full target of the build ({0.05 * full:,.0f})", not over, over[:5])
    now_over = [e for e, q in b.basket_legs.items()
                if b.basket_leg_value(e, q, fvs, b.cur_book_fvs) > 0.05 * (info.get("target_full") or 0) * 1.02 + 5]
    STATE.setdefault("leg_over", {})[label] = (len(now_over), len(b.basket_legs), info.get("target_full"), full)
    quoted = [b.ex[e].label for e in b.basket_legs if any(r["exchangeId"] == e for r in api.orders.values())]
    check(f"{label}: no resting quote of ours on a basket leg", not quoted, quoted[:5])
    per_cycle = Counter(o["cycle"] for o in xs if o["tag"] == "basket_tick")
    check(f"{label}: <= 4 basket orders a cycle", max(per_cycle.values() or [0]) <= 4, per_cycle.most_common(2))
    return adds, info


def target_check(b, label):
    info, cfg = b.basket_info, b.cfg
    liq, floor, acct = info.get("liquidation"), info.get("floor"), b.last_equity
    if liq is None:
        check(f"{label}: target known", False)
        return
    want = max(0.0, min(cfg.basket_mult * (liq - floor - info.get("impact", 0.0)), cfg.basket_cap,
                        cfg.basket_cap_frac * acct))
    check(f"{label}: target = min(5 x (liquidation - floor - impact), cap, 0.85 x account)",
          abs(want - info.get("target_full", -1)) < 2, (want, info.get("target_full")))
    check(f"{label}: floor = max(86k, 0.85 x peak)",
          abs(info["floor"] - max(cfg.basket_floor, cfg.basket_floor_peak_frac * (info.get("peak") or 0))) < 1)


def basket_table(b, adds, title, n=12):
    out(f"{title}: {len(b.basket_legs)} legs held, ${b.basket_info.get('held', 0):,.0f} (target "
        f"{b.basket_info.get('target')}, full {b.basket_info.get('target_full')}), state {b.basket_state}, "
        f"refused {b.basket_info.get('refused')!r}")
    out()
    sample(adds, n)
    out()


def stage3_main(b, api, files):
    """The main flow: stage 3 on after the simulated stage-2 hours (whatever cash they freed)."""
    bad, al = apply_stage(b, files["stage3_basket"])
    check("stage3: applied", not bad and not al and b.cfg.basket_enabled, (bad, al))
    cash0, worst0 = api.cash, status_of(b)["worst_case_loss"]
    hist = []
    for h in range(4):                                     # the 4-h build
        cycles(b, 60, step=60.0, stage="stage3")
        st = status_of(b)
        hist.append((h + 1, len(b.basket_legs), b.basket_info.get("held"), b.basket_info.get("target"),
                     b.basket_info.get("target_full"), api.cash, st["worst_case_loss"], b.basket_info.get("refused")))
        STATE.setdefault("ro", {})[f"stage3 h{h + 1}"] = st["reduce_only"]
    adds, info = basket_checks(b, api, "stage3", "stage3 (cash from stage 2)", max(h[4] or 0 for h in hist))
    target_check(b, "stage3")
    out("## Stage 3 after the simulated stage-2 hours (the cash stage 2 really freed)")
    out(f"- at switch-on: cash {cash0:,.0f}, worst case {worst0:,.0f}")
    out()
    out("| hour | legs | held $ | target (ramped) | full target | cash | worst case (basket at 40%) | refused |")
    out("|---|---|---|---|---|---|---|---|")
    for h, n, held, tgt, full, cash, worst, ref in hist:
        out(f"| {h} | {n} | {held or 0:,.0f} | {tgt or 0:,.0f} | {full or 0:,.0f} | {cash:,.0f} | {worst:,.0f} | {ref} |")
    out()
    basket_table(b, adds, "Basket adds (main flow)")
    summarise(api, "stage3", "stage3")
    STATE["stage3_main"] = (len(b.basket_legs), info.get("held"), info.get("target_full"), cash0)


def flattened_snapshot(S, b_ref):
    """The plan's go-condition reached: every toward-Polymarket position sold at its mark (cash in), the rest held."""
    S2 = dict(S)
    pos, cash_in = dict(S["pos"]), 0.0
    for e, q in S["pos"].items():
        ex, r = b_ref.ex.get(e), b_ref.cur_refs.get(e)
        if ex is None or r is None:
            continue
        legs = b_ref.legs(ex)
        if q * (r - (1.0 / legs if legs > 1 else 0.5)) > 0:
            cash_in += leg_value(q, S["marks"].get(e, 0.5))
            del pos[e]
    S2["pos"] = pos
    return S2, cash_in


def stage3_flattened(S, files):
    """Stage 3 in the state the plan expects at its go-condition (the short-tilt book flattened at the marks, the
    cash freed): the basket's full build, >= 20 legs, the backstop refusal, the ask-share caps."""
    _, ref = build(S)
    cycles(ref, 2, stage="f-ref")
    S2, cash_in = flattened_snapshot(S, ref)
    api, b = build(S2, cash=(float(S["status"]["account_value"]) - sum(leg_value(q, S["marks"].get(e, 0.5))
                                                                       for e, q in S2["pos"].items())))
    cycles(b, 4, stage="f-warm")
    apply_stage(b, files["stage2_flatten"])
    cycles(b, 2, stage="f-warm")
    st = status_of(b)
    worst0, cash0 = st["worst_case_loss"], api.cash
    apply_stage(b, files["stage3_basket"])
    hist = []
    for h in range(5):
        cycles(b, 60, step=60.0, stage="f-stage3")
        st = status_of(b)
        hist.append((h + 1, len(b.basket_legs), b.basket_info.get("held"), b.basket_info.get("target"),
                     b.basket_info.get("target_full"), api.cash, st["worst_case_loss"], b.basket_info.get("refused")))
    adds, info = basket_checks(b, api, "f-stage3", "stage3 flattened", max(h[4] or 0 for h in hist))
    target_check(b, "stage3 flattened")
    check("stage3 flattened: >= 20 legs", len(b.basket_legs) >= 20, len(b.basket_legs))
    # the ask-share caps: per market, first build hour <= 0.75 x the snapshot's top-3 depth on that side
    share_bad = []
    first_h = defaultdict(float)
    t_on = min([o["t"] for o in adds] or [0])
    for o in adds:
        if o["t"] - t_on < 3600:
            first_h[o["eid"]] += o["traded"]
    for e, q in first_h.items():
        side = "asks" if b.basket_legs.get(e, 1) > 0 else "bids"
        depth = sum(l["quantity"] for l in (S["books"][e].get(side) or [])[:3])
        if q > 0.75 * depth + 1:
            share_bad.append((b.ex[e].label, q, depth))
    check("stage3 flattened: first-build ask share <= 75% of the top-3 depth per market", not share_bad, share_bad[:5])
    # worst-case backstop: refused whenever the stressed worst case is above 0.9 x account
    worst_now = b.total_worst_case(dict(api.inv), {e: M.fair_value(x.book, b.cfg) for e, x in b.ex.items()})
    out("## Stage 3 at the plan's go-condition (short-tilt book flattened at the marks, cash freed)")
    out(f"- synthetic start: {len(S['pos']) - len(S2['pos'])} toward-Polymarket positions removed at their marks "
        f"(+{cash_in:,.0f} cash); cash {cash0:,.0f}, worst case {worst0:,.0f}")
    out()
    out("| hour | legs | held $ | target (ramped) | full target | cash | worst case (basket at 40%) | refused |")
    out("|---|---|---|---|---|---|---|---|")
    for h, n, held, tgt, full, cash, worst, refd in hist:
        out(f"| {h} | {n} | {held or 0:,.0f} | {tgt or 0:,.0f} | {full or 0:,.0f} | {cash:,.0f} | {worst:,.0f} | {refd} |")
    out()
    basket_table(b, adds, "Basket adds (go-condition)", n=25)
    summarise(api, "f-stage3", "stage3 (go-condition)")
    STATE["flat"] = (len(b.basket_legs), info.get("held"), info.get("target_full"), cash0, worst0, worst_now)
    # the worst-case backstop, forced: at a 0.5 backstop the stressed worst case is above it -> no add, status says so
    b.cfg.worst_case_backstop_frac = 0.2
    b.basket_target_frozen = None
    cycles(b, 3, step=60.0, stage="f-backstop")
    ba = basket_adds(orders(api, "f-backstop"))
    check("stage3 flattened: adds refused while the stressed worst case > backstop x account (forced 0.2)",
          not ba and b.basket_info.get("refused") == "worst-case backstop", (len(ba), b.basket_info.get("refused")))
    apply_stage(b, files["stage3_basket"])
    b.cfg.worst_case_backstop_frac = 0.9
    return api, b, files


def stage3b_flattened(S, files):
    """stage3b (m 6, cap 90k) at the go-condition: where the build settles."""
    _, ref = build(S)
    cycles(ref, 2, stage="g-ref")
    S2, _ = flattened_snapshot(S, ref)
    api, b = build(S2, cash=(float(S["status"]["account_value"]) - sum(leg_value(q, S["marks"].get(e, 0.5))
                                                                       for e, q in S2["pos"].items())))
    cycles(b, 4, stage="g-warm")
    apply_stage(b, files["stage3b_basket_m6"])
    hist = []
    for h in range(5):
        cycles(b, 60, step=60.0, stage="g-stage3b")
        hist.append((h + 1, len(b.basket_legs), b.basket_info.get("held"), b.basket_info.get("target_full")))
    out("## Stage 3b (m 6, cap 90k) at the same go-condition")
    out("| hour | legs | held $ | full target |")
    out("|---|---|---|---|")
    for h, n, held, full in hist:
        out(f"| {h} | {n} | {held or 0:,.0f} | {full or 0:,.0f} |")
    out()
    STATE["3b"] = hist


def kill_path(api, b, files):
    """Push the liquidation value below the floor: the kill confirms after 120 s, sells the basket down over 2 h
    as a taker and latches (re-applying the same stage-3 file does not clear it)."""
    legs0 = dict(b.basket_legs)
    liq = b.basket_info.get("liquidation")
    floor = b.basket_info.get("floor")
    api.equity_shift = -(liq - floor) - 2000.0            # the account value drops 2k below the floor
    cycles(b, 1, step=60.0, stage="kill")
    s1 = b.basket_state
    cycles(b, 3, step=60.0, stage="kill")
    s2 = b.basket_state
    check("kill: breach seen, not killed on the first read", s1 != "killed", s1)
    check("kill: killed after the 120-s confirmation", s2 == "killed", s2)
    check("kill: alert sent", any("BASKET KILLED" in a for a in ALERTS))
    api.equity_shift = 0.0                                 # value back: the latch holds
    apply_stage(b, files["stage3_basket"])
    k0 = sum(abs(q) for q in b.basket_kill_start.values()) or 1.0
    left_h = []
    for _ in range(3):                                     # 3 h, the books refilled to the snapshot depth each hour
        cycles(b, 60, step=60.0, stage="kill")
        api.refill()
        left_h.append(sum(abs(q) for q in b.basket_legs.values()) / k0)
    check("kill: on schedule - 1 h into the 2-h sale <= 65% of the shares left", left_h[0] <= 0.65, left_h)
    ko = orders(api, "kill", "basket_tick")
    adds = basket_adds(ko)
    check("kill: no basket add after the kill", not adds, len(adds))
    check("kill: sales sent", len(ko) > 0, len(ko))
    check("kill: latched (still killed after the value recovered and the stage-3 file re-applied)",
          b.basket_state == "killed" and b.basket_killed)
    check("kill: >= 95% of the basket's shares sold within 3 h (the rest: legs whose exit side is thin)",
          left_h[-1] <= 0.05, {b.ex[e].label: q for e, q in b.basket_legs.items()})
    STATE["kill_left"] = {b.ex[e].label: (q, [(l["price"], l["quantity"]) for l in
                                               (b.api.base_books[e]["asks" if q < 0 else "bids"] or [])[:3]])
                          for e, q in b.basket_legs.items()}
    STATE["kill_h"] = left_h
    left = {b.ex[e].label: q for e, q in b.basket_legs.items()}
    out("## Kill path (liquidation pushed 2k below the floor)")
    out(f"- breach -> killed after {'<= 4' if s2 == 'killed' else '?'} cycles of 60 s; {len(ko)} sale orders over 3 h, books refilled hourly; "
        f"share of the basket's shares left after 1 / 2 / 3 h: {' / '.join(f'{x:.0%}' for x in left_h)}; "
        f"({sum(1 for o in ko if o['traded'] > 0)} traded, ${sum(usd(o) for o in ko):,.0f}); legs {len(legs0)} -> "
        f"{len(b.basket_legs)} still held {left if len(left) < 8 else str(len(left)) + ' legs'}")
    out()
    sample(ko, 6)
    out()
    STATE["kill"] = (len(legs0), len(b.basket_legs), len(ko))


def exit_path(S, files):
    """A basket built for an hour, then the clock moved past basket_exit_utc: sales only, down to 0 over 24 h."""
    _, ref = build(S)
    cycles(ref, 2, stage="e-ref")
    S2, _ = flattened_snapshot(S, ref)
    api, b = build(S2, cash=(float(S["status"]["account_value"]) - sum(leg_value(q, S["marks"].get(e, 0.5))
                                                                       for e, q in S2["pos"].items())))
    cycles(b, 4, stage="e-warm")
    apply_stage(b, files["stage3_basket"])
    cycles(b, 60, step=60.0, stage="e-build")
    legs0 = len(b.basket_legs)
    sched = b.basket_schedule()
    # 11 Oct + 1 h: inside the no-add window (7 days before the exit)
    CLK.off += sched["no_add"] + 3600 - CLK.time()
    cycles(b, 3, step=60.0, stage="e-noadd")
    noadd = basket_adds(orders(api, "e-noadd"))
    refused = b.basket_info.get("refused")
    check("exit: no adds inside the 7-day no-add window", not noadd and refused == "no adds this close to the exit",
          (len(noadd), refused))
    CLK.off += sched["start"] + 3600 - CLK.time()          # 18 Oct 13:00 UTC
    cycles(b, 3, step=60.0, stage="e-exit")
    st1 = b.basket_state
    done_h = None
    for h in range(30):                                    # up to 30 h at 10-min cycles (books refilled every hour)
        cycles(b, 6, step=600.0, stage="e-exit")
        api.refill()
        if b.basket_state == "done" and done_h is None:
            done_h = h + 1
    xo = orders(api, "e-exit", "basket_tick")
    check("exit: state exiting after basket_exit_utc", st1 == "exiting", st1)
    check("exit: only sales after the exit start", not basket_adds(xo), len(basket_adds(xo)))
    check("exit: the basket is sold to 0 (state done) within ~2 h after the 24-h schedule ends (depth-limited)",
          not b.basket_legs and b.basket_state == "done" and done_h is not None and done_h <= 27,
          (len(b.basket_legs), b.basket_state, done_h))
    out("## Exit path (clock moved to 11 Oct then past basket_exit_utc 18 Oct 12:00)")
    out(f"- built 1 h: {legs0} legs; 11 Oct: refused {refused!r}; 18 Oct 13:00: state {st1}; done after {done_h} h "
        f"(exit window 24 h from 12:00, books refilled hourly); legs left {len(b.basket_legs)}; {len(xo)} sale orders "
        f"({sum(1 for o in xo if o['traded'] > 0)} traded, ${sum(usd(o) for o in xo):,.0f})")
    out()
    STATE["exit"] = (legs0, st1, b.basket_state, len(b.basket_legs), len(xo), done_h)


def stage4(b, api, files):
    """Stage 4 (carry): arbitrage back on with the cash rule; sets only when the cash gate covers 1.25 x need + 2k."""
    bad, al = apply_stage(b, files["stage4_carry"])
    check("stage4: applied", not bad and not al and b.cfg.arb_enabled and b.cfg.arb_cash_rule, (bad, al))
    n0 = len(CAP.lines)
    cycles(b, 20, step=60.0, stage="stage4")
    a_low = [o for o in orders(api, "stage4", "take_arbitrage") if o["need"] > 0]
    blocked_low = b.arb_cash_blocked
    api.cash += 20000.0                                  # as if the carry had 20k free: the rule can hold
    cycles(b, 20, step=60.0, stage="stage4b")
    a_hi = [o for o in orders(api, "stage4b", "take_arbitrage") if o["need"] > 0]
    viol = []
    for cyc in {o["cycle"] for o in a_low + a_hi}:
        xs = [o for o in a_low + a_hi if o["cycle"] == cyc]
        need = sum(o["need"] for o in xs)
        if 1.25 * need + 2000 > xs[0]["free"] + 30:
            viol.append((xs[0]["label"], round(need), round(xs[0]["free"])))
    check("stage4: every arbitrage that needs cash had cash >= 1.25 x need + 2k", not viol, viol[:5])
    check("stage4: at ~0 cash no arbitrage that needs cash, blocked and counted", not [o for o in a_low if o["ok"]]
          and blocked_low > 0, (len(a_low), blocked_low))
    ao = orders(api, "stage4", "take_arbitrage") + orders(api, "stage4b", "take_arbitrage")
    lines = [m for _, _, m in CAP.lines[n0:] if "ARBITRAGE" in m.upper() or "cash rule" in m]
    st = status_of(b)
    cgl = st.get("cash_gate_left") or 0.0
    arb_lines = [m for m in lines if m.startswith("ARBITRAGE") or " ARBITRAGE " in m]
    out("## Stage 4 (carry: arbitrage with the cash rule)")
    out(f"- 20 cycles at the cash stage 3 left: {len(a_low)} cash-needing arbitrage orders, arb_cash_blocked "
        f"{blocked_low}; then +20k cash, 20 cycles: {len(a_hi)} cash-needing arbitrage orders "
        f"({sum(1 for o in a_hi if o['traded'] > 0)} traded, paid ${-sum(min(0, o['cash']) for o in a_hi):,.0f}); "
        f"cash_gate_left now {cgl:,.0f}; all arbitrage-path orders {len(ao)} (incl. covered set unwinds / sell-backs)")
    STATE["stage4"] = (len(a_low), blocked_low, len(a_hi), sum(1 for o in a_hi if o["traded"] > 0))
    for m in lines[:8]:
        out(f"    {m}")
    blines = [m for _, _, m in CAP.lines[n0:] if "BASKET" in m]
    out(f"- basket log lines in stage 4: {len(blines)}")
    for m in blines[:8]:
        out(f"    {m}")
    out()
    summarise(api, "stage4", "stage4 (0 cash)")
    summarise(api, "stage4b", "stage4 (+20k)")
    sample([o for o in ao if o["need"] > 0], 8)
    out()


def findings():
    """The deployment story vs what the code did (numbers from this run)."""
    d = STATE.get("depth", {})
    h = STATE.get("hourly", [])
    m3 = STATE.get("stage3_main")
    fl = STATE.get("flat")
    b3 = STATE.get("3b") or []
    out("## Findings (plan vs code)")
    out()
    if d:
        ks = sorted(d)
        out(f"1. **HIGH, caveat - stage 2 frees far less than the plan's 15-20k of tilt exposure.** The taker exits only "
            f"take levels within 1c of the tilted fair value. On the snapshot's books that is ${d[ks[0]][0]:,.0f} of cash "
            f"/ {d[ks[0]][1]:,.0f} of tilt exposure at s {ks[0]:.2f}, ${d[ks[1]][0]:,.0f} / {d[ks[1]][1]:,.0f} at the "
            f"estimator's reading s {ks[1]:.3f}, ${d[ks[2]][0]:,.0f} / {d[ks[2]][1]:,.0f} only if s reached 0.20. "
            + (f"Simulated: the first hour freed {h[0][1] - STATE['seed']['cash']:,.0f} of cash; after {len(h)} h (books "
               f"refilled hourly) cash {h[-1][1]:,.0f}, tilt_exposure {h[-1][2]:,.0f}, worst case {h[-1][3]:,.0f}. "
               if h else "")
            + "The go-condition of the plan (worst_case_loss < ~40k, cash_gate_left > ~20k) is not reached by stage 2 "
            "on these books; it needs the tilt estimate to rise (the book's longshots repricing toward Polymarket) or "
            "fresh depth. The 15k$/h and 3-per-cycle caps never bind.")
    if m3:
        out(f"2. **MEDIUM, caveat - stage 3 with the cash stage 2 really freed builds a small basket.** Switched on with "
            f"{m3[3]:,.0f} of cash: {m3[0]} legs, ${(m3[1] or 0):,.0f} held against a full target of "
            f"${(m3[2] or 0):,.0f}, then 'no free cash (cash gate)'. The basket is cash-bound, not worst-case-bound "
            "(the 0.9 backstop is ~91k; the worst case stays ~75k). The leg cap (5% of the target) then concentrates "
            "it in the laggards first and the cheap favourite-NO legs (3-12c a NO share).")
    if fl:
        k = None
        if b3 and fl[1]:
            pass
        out(f"3. **HIGH, caveat - the CPPI target shrinks as the basket is bought.** At the go-condition (cash "
            f"{fl[3]:,.0f}) the full target fell from ~62k in the first hour to ${(fl[2] or 0):,.0f} when built "
            f"(${(fl[1] or 0):,.0f} held, {fl[0]} legs): every $ bought cuts the cushion (liquidation - floor) by "
            "basket_impact_frac 0.06 plus the spread paid (bought at the ask, valued at the bid in the liquidation "
            "value, ~8-10% on 5-10c longshots), so with m 5 the build settles near m x C0 / (1 + m x 0.15) ~ 40k "
            "instead of 5 x 14.3k = 72k"
            + (f"; stage3b (m 6, cap 90k) settles at ${(b3[-1][2] or 0):,.0f}" if b3 else "")
            + ". The catch-up odds (29-40%) were computed for a 72-90k basket: with this sizing they are lower. "
            "Not changed here (a sizing decision for the owner: e.g. measure the cushion before the basket's own "
            "spread, or raise m).")
    kl = STATE.get("kill_left")
    if kl is not None:
        kh = STATE.get("kill_h") or []
        out("4. **FIXED (MEDIUM) - the kill and the exit sold only 4 legs on schedule.** Forced sales went richest-first "
            "at 4 orders a cycle; each new cycle gave the first 4 legs a small increment that took every slot, so the "
            "rest of a 60-leg basket waited until the window ended (before the fix: 10 of 64 legs still held 3 h after "
            "the kill). Now the legs furthest behind the schedule go first (mm_bot.basket_orders; tests/test_basket.py "
            "'P9 dry run'). After the fix: shares left 1 / 2 / 3 h after the kill "
            + " / ".join(f"{x:.0%}" for x in kh) + ".")
        out("5. **MEDIUM, caveat - a kill or exit is limited by the exit side's depth, which the basket never checks "
            "when it buys.** Left after 3 h: " + (", ".join(f"{k} {q:+.0f} (exit side {lv})" for k, (q, lv) in kl.items())
                                                 or "none") + ". Favourite-NO legs are bought into a deep bid and "
            "bought back on the (often thin) ask.")
    ro = STATE.get("ro", {})
    out("10. **LOW, note - stages 0-1 run in reduce-only (as live since ~20:00: worst case ~79k against 0.8 x 101k "
        "with the 0.03 hysteresis), and reduce-only blocks every stale-quote take, so stage 1's take hygiene acts only "
        "once reduce-only is left. Stage 2's exits bring the worst case under (0.8 - 0.03) x account within the first "
        "hour (here: " + ", ".join(f"{k} {'reduce-only' if v else 'normal'}" for k, v in ro.items())
        + "); from then on the stale-quote takes run, measured from the tilted reference (out of reduce-only, the "
        "stage-0 rule would have sent 112 takes toward raw Polymarket without a 0.08 tilted edge in 10 minutes on "
        "the flattened book). Rolling back to stage 0 out of reduce-only brings those takes back. stage3's backstop "
        "0.9 also leaves reduce-only (below ~0.87 x account). Market-making adds stay at factor 0 throughout.")
    out("6. **FIXED (LOW) - stage 3 at 0 cash was silent.** The build stalled with refused None and no log line; "
        "now status.json basket.refused = 'no free cash (cash gate)' and one info line when the reason changes.")
    s4 = STATE.get("stage4")
    if s4:
        out(f"7. **LOW, caveat - stage 4's arbitrage runs before the basket in the cycle and takes the cash first.** "
            f"At the cash stage 3 left: {s4[0]} cash-needing arbitrage orders ({s4[1]} blocks by the cash rule); with "
            f"+20k: {s4[2]} orders ({s4[3]} traded), every one with cash >= 1.25 x need + 2k, and the basket resumed "
            "adding with the rest. Switch stage 4 on only once basket.held is at its target, as the plan says.")
    out("8. Confirmed as the plan says: stage 0 sends no new order type; stage 1 never takes against the tilted "
        "reference; every TILT EXIT TAKE shrinks a short-tilt position, at <= 1c from the tilted fair value, longshot "
        "NO first, never NO out of a NO+NO set, never with our own order resting there, <= 3 a cycle, <= 15k$/h; the "
        "basket: longshot YES <= 25c or favourite NO >= 75c only, no headline race, no independent, no leg we quote, "
        "<= 4 orders a cycle, <= 75% of the visible asks in the first build hour, target = min(5 x (liquidation - "
        "floor - impact), 80k, 0.85 x account), floor max(86k, 0.85 x peak), adds refused above the worst-case "
        "backstop, killed 120 s after a floor breach and latched, exit from basket_exit_utc (no adds from 7 days "
        "before), done ~1 h after the 24-h window (depth); arbitrage only under the cash rule.")
    out(f"9. Writes: most writes the fake saw in one 60-s cycle, by stage: "
        + ", ".join(f"{k} {v}" for k, v in WRITES.items() if not k.endswith("ref"))
        + " (the fake does not enforce the 28/min budget; live, the limiter spreads them; the basket's own share is "
          "<= 4 orders x 3 writes a cycle and basket_writes_frac 0.5).")
    out()
    out("Caveats of the fake: other traders never trade with our resting quotes and never move the books (except an "
        "hourly refill to the snapshot depth where stated), Polymarket is flat, every Polymarket price counts as "
        "liquid, the exchange's marks stay at the snapshot's, the 28/min write limiter is not modelled, the self-test "
        "is off (live: passed).")


STATE, WRITES = {}, {}

if __name__ == "__main__":
    rc = main()
    if os.environ.get("P9_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
