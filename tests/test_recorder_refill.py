"""
Offline tests for the positions recorder (record_positions), analysis/mark_rule.py and the same-side refill
cooldown (refill_cooldown_*). No network: FakeApi plays the exchange.

Run:  python tests/test_recorder_refill.py      (exit code 0 = all passed)
"""
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis"))
os.environ.setdefault("SUPERMARKET_API_KEY", "test-key")
os.environ.setdefault("TOURNAMENT_SLUG", "test")

import mm_bot as M                                        # noqa: E402
from fakes import make_bot                                # noqa: E402
import mark_rule                                          # noqa: E402

M.CFG.alert_url = ""
M.notify = lambda *a, **k: False
logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: recorder on, cooldown on (2 fills / 60 s / 200 sh -> 30 s)",
      (c.record_positions, c.refill_cooldown_enabled, c.refill_cooldown_fills, c.refill_cooldown_window_seconds,
       c.refill_cooldown_min_shares, c.refill_cooldown_seconds) == (True, False, 2, 60.0, 200, 30.0))   # cooldown OFF by default (Package 3: simulator -120 +- 97)
good, bad = M.validate_overrides({"refill_cooldown_seconds": 45.0, "refill_cooldown_enabled": False,
                                  "refill_cooldown_fills": 3, "refill_cooldown_min_shares": 100,
                                  "refill_cooldown_window_seconds": 30.0}, c)
check("cooldown settings are live-overridable", len(good) == 5 and not bad, bad)

print("--- positions recorder")
MARKS = {"11": 0.1234, "12": 0.8766}


def rich_positions(api):
    """A positions reply shaped like the live one: off-grid currentPrice plus the other per-position numbers."""
    def positions():
        api.log("positions")
        return {"positions": [{"exchangeId": e, "quantity": q, "settled": False, "currentPrice": MARKS.get(e, 0.5),
                               "costBasis": f"{abs(q) * 0.11:.2f}", "realizedPnl": 1.5, "unrealizedPnl": -0.25,
                               "marketValue": q * MARKS.get(e, 0.5), "marketId": "m1", "title": "x"}
                              for e, q in api.inv.items()],
                "summary": {"totalMarketValue": sum(q * MARKS.get(e, 0.5) for e, q in api.inv.items())}}
    api.positions = positions
    api.pnl = lambda: (api.log("pnl"), {"totalAccountValue": api.equity, "cashBalance": "99000.5",
                                        "realizedPnl": 12.0, "unrealizedPnl": -3.0})[1]


def rec_bot(**cfg):
    a, b = make_bot()
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    b.cfg.record_file = os.path.join(tempfile.mkdtemp(), "data.sqlite")
    b.db = b.open_recorder()
    rich_positions(a)
    return a, b


a, b = rec_bot()
a.inv = {"11": 300.0}
b.cycle()
db = sqlite3.connect(b.cfg.record_file)
tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
check("tables positions and account_marks created", {"positions", "account_marks"} <= tables, tables)
cols = [r[1] for r in db.execute("pragma table_info(positions)")]
check("positions schema", cols == ["ts", "prev_ts", "eid", "quantity", "current_price", "fields"], cols)
cols = [r[1] for r in db.execute("pragma table_info(account_marks)")]
check("account_marks schema", cols == ["ts", "account_value", "market_value", "cash", "realized", "unrealized",
                                       "pnl_ts", "fields"], cols)
rows = db.execute("select ts, prev_ts, eid, quantity, current_price, fields from positions").fetchall()
f = json.loads(rows[0][5]) if rows else {}
check("one row per position: quantity, off-grid currentPrice, other numbers kept (string numbers too)",
      len(rows) == 1 and rows[0][2:5] == ("11", 300.0, 0.1234) and rows[0][1] is None
      and f == {"costBasis": 33.0, "realizedPnl": 1.5, "unrealizedPnl": -0.25, "marketValue": 300 * 0.1234}, rows)
acct = db.execute("select account_value, market_value, cash, realized, unrealized, pnl_ts, fields from account_marks").fetchall()
check("account_marks row: value, market value, cash, realised, unrealised, P&L read time",
      len(acct) == 1 and acct[0][:5] == (100000.0, 300 * 0.1234, 99000.5, 12.0, -3.0) and acct[0][5]
      and "pnl.totalAccountValue" in json.loads(acct[0][6]), acct)

b.cycle(); b.cycle()
n_pos = db.execute("select count(*) from positions").fetchone()[0]
n_acct = db.execute("select count(*) from account_marks").fetchone()[0]
check("cadence: quick cycles inside record_seconds write nothing more", (n_pos, n_acct) == (1, 1), (n_pos, n_acct))

b.last_pos_record -= 61
b.cycle()
n_pos = db.execute("select count(*) from positions").fetchone()[0]
n_acct = db.execute("select count(*) from account_marks").fetchone()[0]
check("after record_seconds: an account_marks row, but an unchanged position isn't rewritten", (n_pos, n_acct) == (1, 2),
      (n_pos, n_acct))

MARKS["11"] = 0.1301
first_ts = rows[0][0]
b.last_pos_record -= 61
b.cycle()
last = db.execute("select ts, prev_ts, current_price from positions order by rowid desc limit 1").fetchone()
check("a changed mark is written with prev_ts = the previous read that saw the old one",
      last[2] == 0.1301 and last[1] is not None and last[1] >= first_ts and last[0] >= last[1], last)

b.last_pos_full -= 3601
b.last_pos_record -= 61
before = db.execute("select count(*) from positions").fetchone()[0]
b.cycle()
check("hourly: every position rewritten even if unchanged",
      db.execute("select count(*) from positions").fetchone()[0] == before + 1)

# Right after a fill: recorded at the next positions read even inside record_seconds.
b.cycle()                                                 # places quotes on 11 again if needed
before = db.execute("select count(*) from account_marks").fetchone()[0]
a.fill("11", True, 20)
b.cycle()                                                 # sees the fill (positions read before fills: due next read)
b.cycle()
after = db.execute("select count(*) from account_marks").fetchone()[0]
q11 = db.execute("select quantity from positions where eid='11' order by rowid desc limit 1").fetchone()[0]
check("a fill triggers one extra record within record_seconds (new quantity written)",
      after == before + 1 and q11 == 320.0, (before, after, q11))

a.inv = {}
b.last_pos_record -= 61
b.cycle()
last = db.execute("select eid, quantity, current_price from positions order by rowid desc limit 1").fetchone()
check("a position that disappears gets one closing row (quantity 0)", last == ("11", 0.0, None), last)

a, b = rec_bot(record_positions=False)
a.inv = {"11": 300.0}
b.cycle()
db2 = sqlite3.connect(b.cfg.record_file)
tables = {r[0] for r in db2.execute("select name from sqlite_master where type='table'")}
check("record_positions off: no positions/account_marks tables, nothing written",
      not ({"positions", "account_marks"} & tables) and b.pos_seen == {}, tables)

a, b = rec_bot()
a.positions = lambda: a.log("positions") or {"positions": [{"exchangeId": "11", "quantity": 5, "settled": False}], "summary": {}}
a.inv = {"11": 5.0}
b.cycle()
row = sqlite3.connect(b.cfg.record_file).execute("select quantity, current_price, fields from positions").fetchone()
check("a reply without a price field still records (current_price NULL)", row == (5.0, None, "{}"), row)
check("recorder adds no requests: one positions and one P&L read per cycle",
      len(a.sent("positions")) == 1 and len(a.sent("pnl")) == 1, a.calls)

check("numeric_fields: numbers and numeric strings, not ids/flags/text",
      M.numeric_fields({"a": 1, "b": "0.5", "c": "x", "d": True, "exchangeId": "7", "marketId": "9", "e": None,
                        "f": "nan"}) == {"a": 1.0, "b": 0.5})

print("--- analysis/mark_rule.py")


def synth_db(rule):
    """Trades in two markets, positions whose currentPrice follows `rule` (or a 30-min VWAP)."""
    path = os.path.join(tempfile.mkdtemp(), "m.sqlite")
    d = sqlite3.connect(path)
    d.execute("CREATE TABLE trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
    d.execute("CREATE TABLE positions (ts REAL, prev_ts REAL, eid TEXT, quantity REAL, current_price REAL, fields TEXT)")
    t0 = 1_700_000_000.0
    trades = []
    for i in range(40):
        for eid, base in (("11", 0.30), ("12", 0.70)):
            trades.append((t0 + 120 * i + (i * 37) % 50 + (7 if eid == "12" else 0), eid, round(base + 0.01 * ((i * 7) % 5), 3),
                           float(10 + (i * 13) % 50), "{}"))
    d.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", trades)
    prev = {}
    for k in range(1, 80):
        ts = t0 + 61 * k
        for eid in ("11", "12"):
            hist = [(t, p, q) for t, e, p, q, _ in trades if e == eid and t <= ts]
            if not hist:
                continue
            mark = rule(hist, ts)
            d.execute("INSERT INTO positions VALUES (?,?,?,?,?,?)",
                      (ts, prev.get(eid), eid, 100.0 if eid == "11" else -50.0, round(mark, 4), "{}"))
            prev[eid] = ts
    d.commit()
    return path


def vwap_last5(hist, ts):
    sel = hist[-5:]
    return sum(p * q for _, p, q in sel) / sum(q for _, _, q in sel)


def vwap_30m(hist, ts):
    sel = [h for h in hist if h[0] > ts - 1800] or hist[-1:]
    return sum(p * q for _, p, q in sel) / sum(q for _, _, q in sel)


for name, rule, want in (("VWAP last 5", vwap_last5, "vwap_last_5"), ("30-min VWAP", vwap_30m, "vwap_30min")):
    path = synth_db(rule)
    res = mark_rule.analyse(path)
    best = res["ranking"][0] if res["ranking"] else None
    check(f"mark_rule picks {name} as the best fit (RMS ~0)", best and best[0] == want and best[1] < 1e-4,
          res["ranking"][:3])
    if want == "vwap_30min":
        check("30-min VWAP: marks change with no new trade (trades ageing out) are found",
              res["changes"] > 0 and res["changes_without_trade"] > 0, (res["changes"], res["changes_without_trade"]))
    else:
        check("last-N VWAP: every mark change has a trade behind it",
              res["changes"] > 0 and res["changes_without_trade"] == 0, (res["changes"], res["changes_without_trade"]))
out = subprocess.run([sys.executable, os.path.join(ROOT, "analysis", "mark_rule.py"), path],
                     capture_output=True, text=True, timeout=60)
check("mark_rule.py runs from the command line and prints the ranking",
      out.returncode == 0 and "vwap_30min" in out.stdout and "RMS" in out.stdout, out.stderr[-300:])
empty = os.path.join(tempfile.mkdtemp(), "e.sqlite")
sqlite3.connect(empty).close()
buf = io.StringIO()
with redirect_stdout(buf):
    rc = mark_rule.main([empty])
check("mark_rule.py on a file without the tables says so (no crash)", rc == 1 and "positions" in buf.getvalue(),
      buf.getvalue())

print("--- same-side refill cooldown")


def big_bot(**cfg):
    """Quotes of 100 shares (the test sizes); runs count from 100 shares unless a test says otherwise."""
    a, b = make_bot()
    b.cfg.refill_cooldown_enabled = True            # OFF by default since Package 3; the tests exercise it on
    b.cfg.refill_cooldown_min_shares = 100
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    return a, b


def bid_of(a, eid="11"):
    return [o for o in a.ours(eid) if o[0] == "bid"]


a, b = big_bot()
b.cycle()
size = bid_of(a)[0][2] if bid_of(a) else 0
a.fill("11", True, size)                                  # 1st fill: whole bid taken
b.cycle()
check("1 fill: the bid is re-quoted", len(bid_of(a)) == 1, a.ours("11"))
a.fill("11", True, a.ours("11") and bid_of(a)[0][2])      # 2nd fill: whole bid taken again
b.cycle()
check("2 fills (>= min shares) in 60 s: the bid is withheld (not re-quoted)", not bid_of(a) and size >= 100, (size, a.ours("11")))
check("...the other side still quotes", any(o[0] == "ask" for o in a.ours("11")), a.ours("11"))
check("...other markets unaffected", len(a.ours("12")) == 2, a.ours("12"))
b.cycle()
check("...still withheld on the next cycle", not bid_of(a), a.ours("11"))
key = ("11", "bid")
b.refill_until[key] = time.monotonic() - 1                # 30 s later
b.cycle()
check("cooldown over: the bid is quoted again", len(bid_of(a)) == 1, a.ours("11"))

# Third fill on a resting (safe) order left alone during the cooldown.
a, b = big_bot(refill_cooldown_min_shares=50)
b.cycle()
a.fill("11", True, 40); b.cycle()
a.fill("11", True, 40); b.cycle()
check("partial fills: the 2nd fill starts the cooldown and the safe remainder stays resting (not topped up)",
      b.refill_until.get(key, 0) > time.monotonic() and len(bid_of(a)) == 1 and bid_of(a)[0][2] == size - 80,
      (a.ours("11"), size))
a.books["11"]["bids"].insert(0, M.Level(0.11, 300) if hasattr(M, "Level") else {"price": 0.11, "quantity": 300})
b.cycle()
check("...a rival pennying it doesn't bring a re-quote during the cooldown", len(bid_of(a)) == 1
      and bid_of(a)[0][1] == 0.105, a.ours("11"))

# Small probes don't count.
a, b = big_bot(refill_cooldown_min_shares=200)
b.cycle()
for _ in range(4):
    a.fill("11", True, 5); b.cycle()
check("1-5 share probes (20 sh in 60 s) don't trigger the cooldown", not b.refill_until and len(bid_of(a)) == 1,
      (b.refill_until, a.ours("11")))

# A side that reduces the position is exempt.
a, b = big_bot()
a.inv = {"11": -2000.0}                                   # short: bids reduce the position
b.cycle()
for _ in range(3):
    if bid_of(a):
        a.fill("11", True, bid_of(a)[0][2])
    b.cycle()
check("reducing side exempt: bids keep re-quoting while they unload a short", len(bid_of(a)) == 1 and not b.refill_until,
      (a.ours("11"), b.refill_until))

# Disabled = today's behaviour.
a, b = big_bot(refill_cooldown_enabled=False)
b.cycle()
for _ in range(3):
    if bid_of(a):
        a.fill("11", True, bid_of(a)[0][2])
    b.cycle()
check("disabled: the bid is re-quoted after every fill, nothing tracked", len(bid_of(a)) == 1 and not b.refill_until,
      a.ours("11"))

# Unit level: the log line once per cooldown start, takes/arbitrage ignored, old fills ignored.
a, b = big_bot()


class Grab(logging.Handler):
    def __init__(self):
        super().__init__(); self.msgs = []

    def emit(self, r): self.msgs.append(r.getMessage())


g = Grab()
M.log.addHandler(g)
old_level = M.log.level
M.log.setLevel(logging.INFO)
M.log.propagate = False
now_iso = M.iso(M.utcnow())
b.order_meta.update({1: {"our_side": "ask", "t": time.time()}, 2: {"our_side": "ask", "t": time.time()},
                     3: {"our_side": "ask", "t": time.time()}, 4: {"our_side": "ask", "take": True, "t": time.time()}})
fills = [{"orderId": i, "exchangeId": "21", "quantity": -400, "filledAt": now_iso} for i in (3, 2, 1)]
b.note_refills(fills, {"21": -1200.0}, 100.0)
b.note_refills([{"orderId": 3, "exchangeId": "21", "quantity": -400, "filledAt": now_iso}], {"21": -1600.0}, 101.0)
msgs = [m for m in g.msgs if "refill cooldown" in m]
check("logged once per cooldown start, with fills and shares", len(msgs) == 1 and "ask 30 s" in msgs[0]
      and "800 sh" in msgs[0] and "2 fills" in msgs[0], msgs)
check("a further fill extends the cooldown", b.refill_until[("21", "ask")] == 131.0, b.refill_until)
b.refill_until.clear(); b.refills.clear()
b.note_refills([{"orderId": 4, "exchangeId": "21", "quantity": -400, "filledAt": now_iso}] * 3, {"21": -1200.0}, 100.0)
old = M.iso(M.utcnow() - M.timedelta(minutes=10))
b.note_refills([{"orderId": 1, "exchangeId": "21", "quantity": -400, "filledAt": old}] * 3, {"21": -1200.0}, 100.0)
check("take fills and fills older than the window don't count", not b.refill_until, b.refill_until)
M.log.removeHandler(g)
M.log.setLevel(old_level)
M.log.propagate = True

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
