"""
Offline tests for the Package 5 ops fields (status.json, recorder, phone summary): liquidation_value,
realised/unrealised P&L (FIFO over fills.csv), toward_ref_capital_frac, capital_over_6h_frac, exit_ratio_24h,
the account table's new liquidation_value column (ALTER on an old file), the summary line and None-safety.

Run:  python tests/test_ops_liq.py      (exit code 0 = all passed)
"""
import csv
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot                           # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


NOW = time.time()
H = 3600.0


def scripted():
    """Rep Ohio (11) long 1,000 marked 0.15, best other bid 0.10; Dem Ohio (12) short 500 marked 0.85, best other ask
    0.90; Rep Utah (21) long 200 marked 0.50 with NO bid at all (unpriced for liquidation)."""
    books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
             "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
             "21": {"bids": [], "asks": [lvl(0.56, 1000)]},
             "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    api, b = make_bot(books=books)
    for e, bk in books.items():
        b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
    b.held = {"11": 1000.0, "12": -500.0, "21": 200.0}
    b.pos_marks = {"11": 0.15, "12": 0.85, "21": 0.50}
    b.health = {"account_value": 100000.0}
    b.cur_refs = {"11": 0.20, "12": 0.80}                  # Polymarket: Rep above the book (0.14), Dem below (0.86)
    b.lots = {"11": [[600.0, NOW - 7 * H], [400.0, NOW - 1 * H]], "12": [[-500.0, NOW - 10 * H]],
              "21": [[200.0, NOW]]}
    rows = [("11", "bid", 1500, 0.12, NOW - 30 * H), ("11", "ask", 500, 0.16, NOW - 2 * H),
            ("12", "ask", 800, 0.88, NOW - 3 * H), ("12", "bid", 300, 0.84, NOW - 1 * H),
            ("22", "bid", 200, 0.50, NOW - 1 * H), ("22", "ask", 100, 0.55, NOW - 0.5 * H),
            ("22", "?", 999, 0.50, NOW - 0.4 * H)]            # not matched to a quote of ours: skipped
    with open(b.cfg.fills_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(M.FillLogger.COLUMNS)
        for k, (e, side, q, p, t) in enumerate(rows):
            w.writerow([k + 1, M.iso(M.datetime.fromtimestamp(t, M.timezone.utc)), e, f"o{k}", side, q, p,
                        p if side != "?" else "", "", "", 0])
    return api, b


print("--- each field on a scripted book")
api, b = scripted()
o = b.ops_fields(NOW)
check("liquidation = 100,000 - (1,000 x (0.15 - 0.10) + 500 x (0.90 - 0.85)) = 99,925",
      close(o["liquidation_value"], 99925.0), o)
check("...Utah (no bid) keeps its mark and is counted unpriced", o["liquidation_unpriced"] == 1, o)
# capital: 11 = 150, 12 = 500 x 0.15 = 75, 21 = 100 -> 325
check("toward Polymarket: Rep long with ref 0.20 > book 0.14, Dem short with ref 0.80 < 0.86 -> 225 / 325",
      close(o["toward_ref_capital_frac"], round(225 / 325, 4)), o)
check("over 6 h: 600 of Rep's 1,000 (90) + all of Dem (75) -> 165 / 325",
      close(o["capital_over_6h_frac"], round(165 / 325, 4)), o)
check("realised FIFO: Rep 500 x (0.16 - 0.12) + Dem 300 x (0.88 - 0.84) + Utah-Dem 100 x 0.05 = 37",
      close(o["realised_pnl"], 37.0), o)
check("unrealised at the mark: Rep 1,000 x (0.15 - 0.12) + Dem -500 x (0.85 - 0.88) = 45 (reconciled markets only)",
      close(o["unrealised_pnl"], 45.0), o)
check("...2 markets whose replayed fills disagree with the position (21 held w/o fills, 22 flat with fills)",
      o["pnl_unreconciled"] == 2, o)
check("exit ratio 24 h: reduced 500 + 300 + 100 = 900 / added 800 + 200 = 1,000 (the 30-h-old fill excluded)",
      close(o["exit_ratio_24h"], 0.9), o)
check("passive pair fields absent while the feature is off", "pair_passive_open" not in o, o)

b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries every field", all(k in st for k in b.OPS_KEYS) and close(st["liquidation_value"], 99925.0),
      {k: st.get(k) for k in b.OPS_KEYS})

print("--- caching: fills.csv re-read only when it changed")
calls = []
real = M.read_fills
M.read_fills = lambda p: calls.append(p) or real(p)
b.ops_fields(NOW + 1)
check("unchanged file: not re-read", not calls, calls)
M.read_fills = real

print("--- None-safety")
api, b = make_bot()
o = b.ops_fields(NOW)
check("fresh bot (no account value, no positions, no fills): nullable fields None, never raises",
      o["liquidation_value"] is None and o["toward_ref_capital_frac"] is None and o["capital_over_6h_frac"] is None
      and o["exit_ratio_24h"] is None and o["realised_pnl"] == 0.0, o)
api, b = scripted()
b.ex["11"].book = {"bids": "garbage"}                        # anything unexpected: every field None, logged once
o = b.safe_ops_fields()
check("broken input: safe_ops_fields returns all None instead of raising",
      set(o) == set(b.OPS_KEYS) and all(v is None for v in o.values()), o)
b.write_status(True)
check("...and status.json is still written", json.load(open(b.cfg.status_file)).get("liquidation_value", 1) is None)
api, b = scripted()
b.pos_marks = {}
b.ex["11"].book = b.ex["12"].book = {}                       # no mark and no book: unpriced, not a crash
o = b.ops_fields(NOW)
check("no mark and no book fair value: unpriced, capital skips them", o["liquidation_unpriced"] >= 2, o)

print("--- recorder: ALTER on an old-schema account table")
d = tempfile.mkdtemp()
path = os.path.join(d, "old.sqlite")
db = sqlite3.connect(path)
db.execute("""CREATE TABLE account (ts TEXT, mode TEXT, account_value REAL, locked_in_orders REAL,
              worst_case_loss REAL, party_delta REAL, orders_resting INTEGER)""")
db.executemany("INSERT INTO account VALUES (?,?,?,?,?,?,?)",
               [("2026-10-01T00:00:00Z", "live", 100000.0, 10.0, 500.0, 1.0, 5),
                ("2026-10-01T00:01:00Z", "live", 100010.0, 11.0, 501.0, 2.0, 6)])
db.commit()
db.close()
api, b = scripted()
b.cfg.record_file = path
b.db = b.open_recorder()
cols = [r[1] for r in b.db.execute("PRAGMA table_info(account)")]
rows = b.db.execute("SELECT account_value, liquidation_value FROM account ORDER BY ts").fetchall()
check("column added, the old rows kept with NULL", cols[-1] == "liquidation_value"
      and rows == [(100000.0, None), (100010.0, None)], (cols, rows))
b.ops_last = b.ops_fields(NOW)
b.health.update(locked_in_orders=1.0, worst_case_loss=2.0, party_delta=3.0, orders_resting=4)
b.last_record = -1e18
b.record({}, time.monotonic())
last = b.db.execute("SELECT account_value, liquidation_value FROM account ORDER BY rowid DESC LIMIT 1").fetchone()
check("a new row carries liquidation_value", last is not None and close(last[1], 99925.0), last)
b.db.close()
b.db = b.open_recorder()                                     # opening it again: no second ALTER, no error
check("re-open: idempotent", [r[1] for r in b.db.execute("PRAGMA table_info(account)")].count("liquidation_value") == 1)
b.ops_last = {}
b.last_record = -1e18
b.record({}, time.monotonic())
last = b.db.execute("SELECT liquidation_value FROM account ORDER BY rowid DESC LIMIT 1").fetchone()
check("no value yet: NULL row, no error", last == (None,), last)
api, b = scripted()
b.cfg.record_file = os.path.join(d, "new.sqlite")
b.db = b.open_recorder()
check("a new file has the column from the start",
      "liquidation_value" in [r[1] for r in b.db.execute("PRAGMA table_info(account)")])

print("--- summary line")
ops = {"liquidation_value": 101100.0, "realised_pnl": 154.0, "toward_ref_capital_frac": 0.76,
       "capital_over_6h_frac": 0.69}
line = M.ops_summary_line(ops, 101500.0)
want = "Liquidation 101.1k (account 101.5k), realised +154, 76% of capital toward Polymarket, 69% older than 6 h"
check("format", line == want, line)
line = M.ops_summary_line(ops, 101500.0, 0.051, 15100.0)
check("with the tilt: ' | tilt 5.1%, exposure +15.1k (-151 per point)'",
      line == want + " | tilt 5.1%, exposure +15.1k (-151 per point)", line)
check("tilt without exposure", M.ops_summary_line(ops, 101500.0, 0.051) == want + " | tilt 5.1%")
check("unknown pieces show '?'", M.ops_summary_line({"realised_pnl": None}, 101500.0) ==
      "Liquidation ? (account 101.5k), realised ?, ? of capital toward Polymarket, ? older than 6 h")
check("nothing known: no line", M.ops_summary_line({}, None) is None and M.ops_summary_line(None) is None)
title, msg = M.build_summary(api, b.cfg.fills_csv, 100000.0, value=101500.0, ops_line=want)
check("build_summary: the line follows the account line", msg.splitlines()[1] == want, msg)
title, msg2 = M.build_summary(api, b.cfg.fills_csv, 100000.0, value=101500.0)
check("...and is absent without one", want not in msg2)
api, b = scripted()
b.ops_last = b.ops_fields(NOW)
for _a in ("tilt_s", "tilt_exposure"):           # Bot.__init__ sets them since T2.1: remove for this case
    b.__dict__.pop(_a, None)
check("bot: no tilt attributes -> no tilt part", "tilt" not in (b.summary_ops_line(100000.0) or "x tilt"),
      b.summary_ops_line(100000.0))
b.tilt_s, b.tilt_exposure = 0.051, 15100.0
check("bot: tilt_s set -> tilt part", (b.summary_ops_line(100000.0) or "").endswith(
    "| tilt 5.1%, exposure +15.1k (-151 per point)"), b.summary_ops_line(100000.0))
b.tilt_s = "bad"
check("bot: a broken tilt value never raises (no line)", b.summary_ops_line(100000.0) is None)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
