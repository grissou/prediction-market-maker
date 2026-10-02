"""
Offline tests for the mark-fragility cap (mark_frag_*): the estimator (sd of the 10-min mid change from the
recorder's snapshots), compute_quote's frag_limit on the adding side, the Bot wiring (refresh from the recorder,
decide, the total cap with hysteresis, status.json and the phone summary) and analysis/mark_fragility.py. No network.

Run:  python tests/test_mark_frag.py      (exit code 0 = all passed)
"""
import io
import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "analysis"))
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402
import mark_fragility as MF                               # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def near(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


print("--- settings")
c = M.Config()
check("defaults: off, 100 $/step, 24 h, 60 samples, 0.2c floor, 2,000 $ total",
      (c.mark_frag_enabled, c.mark_frag_max_step_cash, c.mark_frag_window_hours, c.mark_frag_min_samples,
       c.mark_frag_floor_sd, c.mark_frag_total_max_cash) == (True, 100.0, 24.0, 60, 0.002, 0.0))   # total cap off (Reviewer M-7)
good, bad = M.validate_overrides({"mark_frag_enabled": True, "mark_frag_max_step_cash": 150.0,
                                  "mark_frag_window_hours": 12.0, "mark_frag_min_samples": 30,
                                  "mark_frag_floor_sd": 0.003, "mark_frag_total_max_cash": 0.0}, c)
check("all six settings are live-overridable", len(good) == 6 and not bad, bad)

print("--- estimator")
alt = [(600.0 * i, 0.50 + (0.01 if i % 2 else 0.0)) for i in range(101)]    # +-1c every 10 min
ch = M.mark_step_changes(alt)
check("10-min spaced points: 100 changes of +-1c", len(ch) == 100 and all(near(abs(x), 0.01, 1e-12) for x in ch), ch[:4])
check("...sd = 1c (sample sd, n-1)", near(M.mark_step_sd(alt, 60, 0.002), 0.01 * (100 / 99) ** 0.5, 1e-9))
rng = random.Random(7)
walk, m = [], 0.5
for i in range(24 * 60):                                  # 60-s snapshots for 24 h, a random walk with
    walk.append((60.0 * i, m))                            # 10-min sd = 3c (per-minute sd 3c / sqrt 10)
    m += rng.gauss(0, 0.03 / 10 ** 0.5)
sd = M.mark_step_sd(walk, 60, 0.002)
check("60-s random walk, 10-min sd 3c: estimate within 10%", near(sd, 0.03, 0.003), sd)
check("...overlapping pairs: one per point 10 min before the end (1,430)", len(M.mark_step_changes(walk)) == 1430)
flat = [(60.0 * i, 0.40) for i in range(200)]
check("constant mid -> the floor (0.2c), never 0", M.mark_step_sd(flat, 60, 0.002) == 0.002)
check("...floor follows the setting", M.mark_step_sd(flat, 60, 0.005) == 0.005)
short = [(60.0 * i, 0.40 + 0.001 * i) for i in range(40)]  # 30 ten-minute changes
check("30 changes with min_samples 60 -> no estimate", M.mark_step_sd(short, 60, 0.002) is None)
check("...min_samples 30 -> an estimate", M.mark_step_sd(short, 30, 0.002) is not None)
gappy = [(1200.0 * i, 0.5 + 0.01 * (i % 2)) for i in range(200)]
check("points 20 min apart (gaps > 15 min) give no 10-min changes", M.mark_step_changes(gappy) == [])
odd = [(0.0, 0.5), (300.0, 0.6), (700.0, 0.52)]
check("irregular spacing: paired with the first point >= 10 min later", M.mark_step_changes(odd) == [0.52 - 0.5])
check("limit = max_step_cash / sd (sd 5c -> 2,000 sh)", near(M.mark_frag_limit(0.05, c, 100), 2000.0, 1e-9))
check("...never below one quote (sd 50c -> 200, quote 300 -> 300)", M.mark_frag_limit(0.5, c, 300) == 300)

print("--- compute_quote: frag_limit on the adding side")
cfg = M.Config()
cfg.skew_per_quote = cfg.skew_per_share = 0.0
cfg.skew_age_enabled = False
cfg.reduce_join_best = False
kw = dict(bankroll=100000, order_size=100, position_limit=10000)
for inv in (800, -800):
    base = M.compute_quote(0.50, inv, inv, 0.45, 0.55, cfg, **kw)
    q = M.compute_quote(0.50, inv, inv, 0.45, 0.55, cfg, frag_limit=850, **kw)
    add, red = ("bid", "ask") if inv > 0 else ("ask", "bid")
    check(f"pos {inv:+d}, limit 850: the adding {add} shrinks to 50",
          getattr(q, add + "_size") == 50 and getattr(base, add + "_size") == 100, (q, base))
    check(f"...the reducing {red} is unchanged", (getattr(q, red), getattr(q, red + "_size"))
          == (getattr(base, red), getattr(base, red + "_size")), (q, base))
q = M.compute_quote(0.50, 900, 900, 0.45, 0.55, cfg, frag_limit=850, **kw)
check("above the limit: adding side 0, reducing side full", (q.bid_size, q.ask_size) == (0, 100), q)
q = M.compute_quote(0.50, 0, 0, 0.45, 0.55, cfg, frag_limit=100, **kw)
check("flat with a limit of one quote: both sides keep their full quote", (q.bid_size, q.ask_size) == (100, 100), q)
check("frag_limit None = today", M.compute_quote(0.50, 800, 800, 0.45, 0.55, cfg, frag_limit=None, **kw)
      == M.compute_quote(0.50, 800, 800, 0.45, 0.55, cfg, **kw))


def snapshot_db(bot, series_by_eid, mode="live"):
    """A recorder for the bot (temp file) with snapshots: {eid: [(seconds ago, best_bid, best_ask)]}."""
    bot.cfg.record_file = os.path.join(tempfile.mkdtemp(), "md.sqlite")
    bot.db = bot.open_recorder()
    now = M.utcnow()
    rows = [(M.iso(now - timedelta(seconds=ago)), mode, eid, eid, bb, ba, None, None, None, None, None)
            for eid, ser in series_by_eid.items() for ago, bb, ba in ser]
    bot.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    bot.db.commit()


print("--- Bot: estimate from the recorder")
a, b = make_bot()
rng = random.Random(11)
s11, m = [], 0.14
for i in range(6 * 60):                                   # 6 h of 60-s snapshots, 10-min sd 2c
    s11.append((60.0 * (6 * 60 - i), round(m - 0.04, 4), round(m + 0.04, 4)))
    m += rng.gauss(0, 0.02 / 10 ** 0.5)
old = [(86400 + 3600 + 60.0 * i, 0.10, 0.90 + 0.05 * (i % 2)) for i in range(300)]   # older than 24 h: ignored
snapshot_db(b, {"11": s11 + old, "12": [(60.0 * i, 0.82, 0.90) for i in range(30)],
                "21": [(60.0 * i, None, 0.56) for i in range(300)]})
b.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
             (M.iso(M.utcnow()), "dry", "22", "22", 0.4, 0.5, None, None, None, None, None))
b.refresh_mark_sd(time.monotonic())
series = sorted((-ago, (bb + ba) / 2) for ago, bb, ba in s11)
check("refresh: market 11 gets the estimator's sd of its mid (within the window)",
      near(b.mark_sd.get("11"), M.mark_step_sd(series, 60, 0.002), 1e-9) and near(b.mark_sd.get("11"), 0.02, 0.005),
      b.mark_sd)
check("...too few samples (12), no two-sided book (21), other mode (22): no estimate",
      set(b.mark_sd) == {"11"}, b.mark_sd)
before = dict(b.mark_sd)
b.mark_sd = {}
b.refresh_mark_sd(time.monotonic())
check("...refreshed only every 30 min", b.mark_sd == {})
b.refresh_mark_sd(time.monotonic(), force=True)
check("...force re-reads", b.mark_sd == before)
a2, b2 = make_bot()
b2.refresh_mark_sd(time.monotonic())
check("recorder off: no estimates, no error", b2.db is None and b2.mark_sd == {})

print("--- Bot: decide")


def quote(bot, inv):
    return bot.decide(bot.ex["11"], 0.14, {"11": float(inv)}, {"11": float(inv)}, False, 0.0, time.monotonic())


a, b = make_bot()
b.cfg.reduce_join_best = False
b.cfg.mark_frag_enabled = False                           # ON by default since Package 3: test the off path explicitly
b.ex["11"].book = a.books["11"]
b.mark_sd = {"11": 0.10}
b.cfg.mark_frag_max_step_cash = 95.0                      # -> limit 950 shares
base = quote(b, 900)
check("disabled: decide is today's (bid 100 at pos 900)", base.bid_size == 100, base)
b.cfg.mark_frag_enabled = True
q = quote(b, 900)
check("enabled, sd 10c, 95 $/step -> limit 950: the adding bid shrinks to 50", q.bid_size == 50, q)
check("...the reducing ask is unchanged", (q.ask, q.ask_size) == (base.ask, base.ask_size), (q, base))
qs = quote(b, -900)
check("short 900: the adding ask shrinks to 50, the reducing bid is unchanged",
      qs.ask_size == 50 and qs.bid_size == 100, qs)
b.cfg.mark_frag_max_step_cash = 1.0                       # limit 10 shares -> floored at one quote (100)
check("never below one quote: flat keeps both sides at 100", (lambda x: (x.bid_size, x.ask_size))(quote(b, 0))
      == (100, 100))
b.mark_sd = {}
check("no estimate for the market: no cap", quote(b, 900).bid_size == 100)

print("--- Bot: total cap with hysteresis")
a, b = make_bot()
b.cfg.reduce_join_best = False
b.ex["11"].book = a.books["11"]
b.mark_sd = {"11": 0.10, "21": 0.05}
inv = {"11": 900.0, "21": -100.0}                         # 90 + 5 = 95 $/step
b.cfg.mark_frag_total_max_cash = 90.0
h = b.update_mark_frag(inv, b.cfg)
check("disabled: total computed (95) but the cap never engages",
      near(h["mark_frag_total_cash"], 95.0, 1e-9) and not b.mark_frag_over, h)
b.cfg.mark_frag_enabled = True
b.update_mark_frag(inv, b.cfg)
check("enabled, 95 > 90: total cap on", b.mark_frag_over)
q = quote(b, 900)
check("...adding bid withdrawn, reducing ask still quoted", q.bid_size == 0 and q.ask_size == 100, q)
b.update_mark_frag({"11": 750.0, "21": -100.0}, b.cfg)    # 80 >= 72 (80%)
check("80 $/step (above 80% of 90): stays on", b.mark_frag_over)
b.update_mark_frag({"11": 650.0, "21": -100.0}, b.cfg)    # 70 < 72
check("70 $/step (below 80%): off again", not b.mark_frag_over)
b.update_mark_frag({"11": 850.0, "21": -100.0}, b.cfg)    # 90: not above 90
check("exactly at the cap: stays off", not b.mark_frag_over)
b.cfg.mark_frag_total_max_cash = 0.0
b.update_mark_frag({"11": 5000.0}, b.cfg)
check("total_max_cash 0: no total cap", not b.mark_frag_over)

print("--- status.json and summary")
a, b = make_bot()
b.mark_sd = {e: 0.01 * (k + 1) for k, e in enumerate(["11", "12", "21", "22"])}
inv = {"11": 1000.0, "12": -2000.0, "21": 3000.0, "22": 0.0}
h = b.update_mark_frag(inv, b.cfg)
check("total = sum |pos| x sd (10 + 40 + 90 = 140)", near(h["mark_frag_total_cash"], 140.0, 1e-9), h)
check("top positions by step cash, biggest first, by label",
      list(h["mark_frag_top"].values()) == [90.0, 40.0, 10.0]
      and list(h["mark_frag_top"])[0] == b.ex["21"].label, h["mark_frag_top"])
check("capped markets: step cash >= 100 -> 0; with 40 $/step -> 2",
      h["mark_frag_capped_markets"] == 0 and (b.cfg.__setattr__("mark_frag_max_step_cash", 40.0) or
                                             b.update_mark_frag(inv, b.cfg)["mark_frag_capped_markets"] == 2))
many = M.Config()
b.mark_sd = {str(i): 0.01 for i in range(15)}
check("at most 10 in the top list", len(b.update_mark_frag({str(i): 100.0 * (i + 1) for i in range(15)},
                                                           many)["mark_frag_top"]) == 10)
a, b = make_bot()
b.mark_sd = {"11": 0.02}
b.mark_sd_time = time.monotonic()
b.cycle()
a.fill("11", True, 500)
b.cycle()
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries mark_frag_total_cash / _top / _capped_markets after a cycle",
      near(st.get("mark_frag_total_cash"), 500 * 0.02, 1e-6) and st.get("mark_frag_capped_markets") == 0
      and list(st.get("mark_frag_top", {}).values()) == [10.0], {k: v for k, v in st.items() if "frag" in k})
_, msg = M.build_summary(a, b.cfg.fills_csv, 100000, value=100000, health=b.health)
line = [x for x in msg.split("\n") if x.startswith("Mark noise")]
check("phone summary: one 'Mark noise' line", len(line) == 1 and "10 $ per 10 min" in line[0], msg)
_, msg = M.build_summary(a, b.cfg.fills_csv, 100000, value=100000, health={**b.health, "mark_frag_estimates": 0})
check("...no line without estimates", "Mark noise" not in msg)

print("--- analysis/mark_fragility.py")
d = tempfile.mkdtemp()
path = os.path.join(d, "md.sqlite")
db = sqlite3.connect(path)
db.execute("""CREATE TABLE snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL,
              fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
rows = []
for i in range(101):
    ts = M.iso(M.utcnow() - timedelta(seconds=600 * (100 - i)))
    rows.append((ts, "live", "A", "Mkt A", 0.50 + 0.01 * (i % 2), 0.52 + 0.01 * (i % 2), None, None, None, None, 2000.0))
    rows.append((ts, "live", "B", "Mkt B", 0.30, 0.32, None, None, None, None, -500.0))
db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
db.commit()
series, labels, pos = MF.load(db, 24, "live")
rows_t, total, capped = MF.fragility_table(series, labels, pos, 60, 0.002, 100.0)
sd_a = 0.01 * (100 / 99) ** 0.5
check("script: sd 1c for A, the floor for B; |pos| x sd", [r[0] for r in rows_t] == ["Mkt A", "Mkt B"]
      and near(rows_t[0][3], 2000 * sd_a, 1e-6) and near(rows_t[1][3], 1.0, 1e-9), rows_t)
check("...total and capped count (A 20.1 $ < 100: 0 capped; limit 100 / sd)",
      near(total, 2000 * sd_a + 1.0, 1e-6) and capped == 0 and near(rows_t[0][4], 100 / sd_a, 1e-6))
check("...same estimator as the bot", near(MF.step_sd(series["A"]), M.mark_step_sd(series["A"]), 1e-12))
db.close()
out = io.StringIO()
with redirect_stdout(out):
    rc = MF.main([path, "--max-step-cash", "10"])
check("main on a market_data.sqlite: prints the total and the table, 1 capped at 10 $/step",
      rc == 0 and "total 21 $" in out.getvalue() and "1 above" in out.getvalue(), out.getvalue())
dump = os.path.join(d, "md.sql")                          # the SQL-dump route, without a position column
src = sqlite3.connect(path)
src.execute("CREATE TABLE s2 AS SELECT ts, mode, eid, label, best_bid, best_ask FROM snapshots")
src.execute("DROP TABLE snapshots")
src.execute("ALTER TABLE s2 RENAME TO snapshots")
src.commit()
with open(dump, "w") as f:
    f.write("\n".join(src.iterdump()))
src.close()
with open(os.path.join(d, "status.json"), "w") as f:
    json.dump({"positions": {"Mkt A": -1000.0}}, f)
out = io.StringIO()
with redirect_stdout(out):
    rc = MF.main([dump])
check("a .sql dump without positions: asks for --positions", rc == 1 and "--positions" in out.getvalue())
out = io.StringIO()
with redirect_stdout(out):
    rc = MF.main([dump, "--positions", os.path.join(d, "status.json")])
check("...with --positions status.json: A at 1,000 sh -> 10 $/step", rc == 0 and "total 10 $" in out.getvalue(),
      out.getvalue())

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
