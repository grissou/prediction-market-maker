"""
Dry-run HARNESS for the tests/test_p*_dryrun.py suites (first built for Package 9, whose own dry run - the tilt exits
and the long-tilt basket, neither ever enabled live - was removed on simplify with those features): the fake exchange
seeded with a REAL ops snapshot (extracted to /home/claude/snap03, or $P9_SNAP): all 237 markets, the latest 3-level
books of other traders, Polymarket references (raw, from the recorder), positions and the exchange's marks, cash, the
tilt state and the position lots. The bot runs with the LIVE settings (Config defaults + the live override file); each
staged file is applied with the bot's own override path (check_overrides), then full cycles (Bot.cycle) run on a
simulated clock and every order the fake exchange receives is recorded with the feature that sent it (tag_methods).

What the fake does NOT model: other traders never trade with our resting quotes, the books do not move except where we
take (and an explicit "hourly refill" to the snapshot depth), Polymarket is frozen.
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


TAGGED = ("take_stale_quotes", "take_arbitrage", "pair_followup_step")


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


RETIRED_PREFIXES = ("basket_", "momentum_", "mom_", "value_extreme_p", "value_min_edge", "mm_carry_min",
                    "election_holdback_")          # the momentum sleeve's keys (switched off live 8 Oct)
RETIRED_KEYS = ("tilt_exit_priority", "tilt_exit_full_size")


def staged(path):
    """A staged file less the keys of features removed on simplify (never enabled live, or switched off live): the
    basket_* keys, the tilt exits' tilt_exit_* (the staged files pin tilt_exit_priority / tilt_exit_full_size at
    their default False) and the momentum sleeve's keys (deploy/settings_override.live_minimal.json still carries
    momentum_max_usd behind momentum_enabled false)."""
    return {k: v for k, v in json.load(open(path)).items()
            if not k.startswith(RETIRED_PREFIXES) and k not in RETIRED_KEYS}


def apply_stage(b, path):
    """The owner's deployment step: the file copied over settings_override.json, read by the bot's own override path."""
    raw = staged(path)
    good, bad = M.validate_overrides(raw, b.cfg)
    with open(b.cfg.overrides_file, "w") as f:
        json.dump(raw, f)
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
STATE, WRITES = {}, {}

if __name__ == "__main__":
    print("tests/dryrun_harness.py is a harness (snapshot, fake exchange, clock) for the test_p*_dryrun.py suites;"
          " it has no checks of its own")
