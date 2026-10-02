"""
Offline tests for turnover control (turnover_*): the TurnoverTracker (deques, window, seeds, start-up grace),
compute_quote's adding-side limit, decide's dead-market rule, status.json / summary fields, the feed's tape copy,
and analysis/turnover.py on synthetic data. No network.

Run:  python tests/test_turnover.py      (exit code 0 = all passed)
"""
import csv
import importlib.util
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, make_bot                       # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []
H = 3600.0


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def iso_at(t):
    return M.iso(M.datetime.fromtimestamp(t, M.timezone.utc))


print("--- settings")
c = M.Config()
check("hysteresis settings: alive above 75 sh/h, 30 min minimum state life, live-overridable",
      (c.turnover_alive_shares_per_hour, c.turnover_min_state_minutes) == (75.0, 30.0)
      and not M.validate_overrides({"turnover_alive_shares_per_hour": 60.0, "turnover_min_state_minutes": 10.0}, c)[1])
check("defaults: OFF until reviewed (Package 3), 6 h window, 50 sh/h, adding x0.25, limit x0.5, tape on",
      (c.turnover_control_enabled, c.turnover_window_hours, c.turnover_min_shares_per_hour,
       c.turnover_dead_adding_factor, c.turnover_dead_max_position_frac, c.turnover_use_tape)
      == (False, 6.0, 50.0, 0.25, 0.5, True))
good, bad = M.validate_overrides({"turnover_control_enabled": False, "turnover_window_hours": 3.0,
                                  "turnover_min_shares_per_hour": 20.0, "turnover_dead_adding_factor": 0.0,
                                  "turnover_dead_max_position_frac": 0.3, "turnover_use_tape": False}, c)
check("all six settings are live-overridable", len(good) == 6 and not bad, bad)
keys = list(M.OVERRIDABLE)
check("...all six turnover settings are in OVERRIDABLE", all(k in M.OVERRIDABLE for k in ("turnover_control_enabled", "turnover_window_hours", "turnover_min_shares_per_hour", "turnover_dead_adding_factor", "turnover_dead_max_position_frac", "turnover_use_tape")))

print("--- TurnoverTracker: deques and the window")
now = 1_800_000_000.0
tr = M.TurnoverTracker(start=now - 10 * H)               # running 10 h: the 6 h window is fully observed
tr.add("1", now - 7 * H, 500)                            # outside the 6 h window
tr.add("1", now - 5 * H, 120)
tr.add("1", now - 1 * H, -60)                            # sign ignored (NO-side fills are negative)
tr.add("1", now - 0.5 * H, 0)                            # nothing
check("ours in the window: 180 (the 7 h-old 500 is outside)", tr.shares("1", now, 6.0) == 180, tr.shares("1", now, 6.0))
check("a 0-share trade is not stored", len(tr.ours["1"]) == 3, list(tr.ours["1"]))
check("a wider window counts it (8 h: 680)", tr.shares("1", now, 8.0) == 680)
tr.add("1", now - 2 * H, 400, tape=True)
check("tape bigger than ours: flow = tape (400; ours are in the tape, never added twice)",
      tr.shares("1", now, 6.0) == 400)
check("use_tape off: ours only (180)", tr.shares("1", now, 6.0, use_tape=False) == 180)
tr.add("2", now - 3 * H, 50, tape=True)
tr.add("2", now - 3 * H, 90)
check("tape smaller than ours (a gap in the feed): ours are the floor (90)", tr.shares("2", now, 6.0) == 90)
check("per hour = shares / 6 h observed", abs(tr.per_hour("1", now, 6.0) - 400 / 6) < 1e-9, tr.per_hour("1", now, 6.0))
check("unknown market: 0 sh/h (judged)", tr.per_hour("9", now, 6.0) == 0.0)
tr.prune(now + 46.5 * H)                                 # 48 h retention from now + 46.5 h: older than now - 1.5 h go
check("prune keeps KEEP_HOURS (48 h): only trades newer than the cut remain",
      [t for t, _ in tr.ours["1"]] == [now - 1 * H] and not tr.tape["1"], (list(tr.ours["1"]), list(tr.tape["1"])))

print("--- start-up grace")
tr = M.TurnoverTracker(start=now - 1 * H)                # started an hour ago, no seed
check("1 h after a start without history: not judged (every market alive)", tr.per_hour("1", now, 6.0) is None
      and not tr.judged(now, 6.0))
check("observed 1 h", abs(tr.observed_hours(now, 6.0) - 1.0) < 1e-9)
check("5.3 h: still not (needs 90% of the window = 5.4 h)", not tr.judged(now + 4.3 * H, 6.0))
check("5.4 h: judged", tr.judged(now + 4.4 * H, 6.0))
check("judged after 6 h, 0 sh/h for a silent market", tr.per_hour("1", now + 5 * H, 6.0) == 0.0)

print("--- seeding from fills.csv")
d = tempfile.mkdtemp()
fpath = os.path.join(d, "fills.csv")


def write_fills(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(M.FillLogger.COLUMNS)
        for i, (t, eid, q) in enumerate(rows):
            w.writerow([i, iso_at(t), eid, i, "bid", q, 0.5, 0.5, 0.5, 0.5])


# a fill every 5 min over the last 7 h (market 1, 10 sh each), plus one 60 h old (beyond the 48 h kept)
rows = [(now - 60 * H, "1", 999)] + [(now - k * 300 - 30, "1", 10) for k in range(84)]
write_fills(fpath, rows)
tr = M.TurnoverTracker(start=now)
n = tr.seed_fills(M.read_fills(fpath), now)
check("84 rows used (the 60 h-old one is beyond the 48 h kept)", n == 84, n)
check("fills 5 min apart up to the restart: the whole window observed, judged at once",
      tr.judged(now, 6.0) and abs(tr.observed_hours(now, 6.0) - 6.0) < 1e-6, tr.observed_hours(now, 6.0))
check("market 1: 72 fills x 10 in 6 h = 120 sh/h", abs(tr.per_hour("1", now, 6.0) - 120) < 1e-6,
      tr.per_hour("1", now, 6.0))
# the same, but the bot was down for the last 2 h before this start (an outage): the gap is not zero flow
write_fills(fpath, [r for r in rows if r[0] < now - 2 * H])
tr = M.TurnoverTracker(start=now)
tr.seed_fills(M.read_fills(fpath), now)
check("a 2 h outage before the restart is unobserved: 4 h of 6 observed, not judged (alive)",
      abs(tr.observed_hours(now, 6.0) - (4 - 30 / 3600)) < 0.01 and not tr.judged(now, 6.0)
      and tr.per_hour("1", now, 6.0) is None, tr.observed_hours(now, 6.0))
check("...as the window moves the old seed leaves it: 3 h later 4.0 h observed, still not judged",
      not tr.judged(now + 3 * H, 6.0) and abs(tr.observed_hours(now + 3 * H, 6.0) - 4.0) < 0.01)
check("...judged once this run alone covers 90% (5.4 h)", tr.judged(now + 5.4 * H, 6.0))
# an outage INSIDE the window (35 min, no fills), then fills again until the restart
write_fills(fpath, [r for r in rows if not (now - 3 * H < r[0] < now - 3 * H + 35 * 60)])
tr = M.TurnoverTracker(start=now)
tr.seed_fills(M.read_fills(fpath), now)
check("a 35 min gap inside the window (> 10 min) is unobserved: ~5.5 h observed",
      5.3 < tr.observed_hours(now, 6.0) < 5.6, tr.observed_hours(now, 6.0))
write_fills(fpath, [r for r in rows if not (now - 3 * H < r[0] < now - 3 * H + 8 * 60)])
tr = M.TurnoverTracker(start=now)
tr.seed_fills(M.read_fills(fpath), now)
check("...a gap of <= 10 min is observed (quiet, not an outage)", abs(tr.observed_hours(now, 6.0) - 6.0) < 1e-6,
      tr.observed_hours(now, 6.0))
write_fills(fpath, [(now - 4 * H, "1", 100), (now - 3 * H, "1", 100)])
tr = M.TurnoverTracker(start=now)
tr.seed_fills(M.read_fills(fpath), now)
check("two isolated fills (1 h apart, 3 h before the start): nothing observed, not judged",
      tr.observed_hours(now, 6.0) == 0.0 and not tr.judged(now, 6.0))
check("an empty / missing fills.csv seeds nothing", M.TurnoverTracker(start=now).seed_fills([], now) == 0)

print("--- seeding from the recorder (trades + snapshot times)")


def make_db(path, trades, snap_times):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
    db.execute("""CREATE TABLE snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL,
                  fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
    db.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", [(t, e, 0.5, q, "{}") for t, e, q in trades])
    for t in snap_times:                                 # two markets per snapshot, as the bot writes them
        for e in ("1", "2"):
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (iso_at(t), "live", e, "X", 0.4, 0.5, 0.45, None, None, None, 0))
    db.commit()
    db.close()


trades = [(now - 50 * H, "1", 5), (now - 8 * H, "1", 70), (now - 2 * H, "1", 400), (now - 1 * H, "2", -30),
          (now - 0.9 * H, "2", None)]
dbp = os.path.join(d, "market_data.sqlite")
make_db(dbp, trades, [now - 7 * H + 60 * k for k in range(7 * 60)])     # snapshots every minute until the start
tr = M.TurnoverTracker(start=now)
n = tr.seed_tape(dbp, now)
check("3 tape rows used (50 h old: beyond 48 h; None quantity skipped)", n == 3, n)
check("snapshots every minute up to the start: judged at once", tr.judged(now, 6.0)
      and abs(tr.observed_hours(now, 6.0) - 6.0) < 1e-6, tr.observed_hours(now, 6.0))
check("market 1 flow from the tape: 400 in the window", tr.shares("1", now, 6.0) == 400)
check("|negative| quantity counts (market 2: 30)", tr.shares("2", now, 6.0) == 30)
dbp2 = os.path.join(d, "outage.sqlite")                 # recording stopped 45 min before this start (an outage)
make_db(dbp2, trades, [now - 7 * H + 60 * k for k in range(int(6.25 * 60))])
tr = M.TurnoverTracker(start=now)
tr.seed_tape(dbp2, now)
check("recording ended 45 min before the start: 5.25 h observed, not judged (no false dead after an outage)",
      abs(tr.observed_hours(now, 6.0) - 5.25) < 0.02 and not tr.judged(now, 6.0), tr.observed_hours(now, 6.0))
tr.seed_fills(M.read_fills(fpath), now)
check("fills.csv and the recorder combine (still not bridging the outage)", not tr.judged(now, 6.0))
check("no file: 0, nothing seeded", M.TurnoverTracker(start=now).seed_tape(os.path.join(d, "none.sqlite"), now) == 0)
db = sqlite3.connect(os.path.join(d, "empty.sqlite"))
db.execute("CREATE TABLE trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
db.execute("CREATE TABLE snapshots (ts TEXT)")
db.commit()
db.close()
tr = M.TurnoverTracker(start=now)
check("empty tables: 0, no coverage claimed", tr.seed_tape(os.path.join(d, "empty.sqlite"), now) == 0
      and not tr.seed_spans)

print("--- compute_quote: adding-side position limit (on the wanted size only)")
cfg = M.Config()
cfg.skew_per_quote = 0.0
cfg.skew_age_enabled = False
kw = dict(bankroll=100000, order_size=100, position_limit=1000)
base = M.compute_quote(0.50, 300, 300, 0.45, 0.55, cfg, **kw)
q = M.compute_quote(0.50, 300, 300, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
check("long 300, limit 1,000 x0.5 = 500: bid still 100 (room 200)", q.bid_size == 100 and q == base, (q, base))
q = M.compute_quote(0.50, 450, 450, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
check("long 450: wants 50 (room to 500), a resting 100 may stay (bid_max 100: normal limit)",
      q.bid_size == 50 and q.bid_max == 100, q)
q = M.compute_quote(0.50, 600, 600, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
b0 = M.compute_quote(0.50, 600, 600, 0.45, 0.55, cfg, **kw)
check("long 600 > 500: bid held at 1 share (nothing more wanted), bid_max 100, ask (reducing) unchanged",
      q.bid == b0.bid and q.bid_size == 1 and q.bid_max == 100 and (q.ask, q.ask_size) == (b0.ask, b0.ask_size),
      (q, b0))
check("...so a resting 100-share bid is kept (not unsafe), none bigger",
      not M.side_needs_change([M.Resting(1, "21", True, b0.bid, 100, None)], q.bid, q.bid_size, cfg, M.utcnow(), q.bid_limit,
                              True, max(q.bid_size, q.bid_max or 0))
      and M.side_needs_change([M.Resting(1, "21", True, b0.bid, 150, None)], q.bid, q.bid_size, cfg, M.utcnow(), q.bid_limit,
                              True, max(q.bid_size, q.bid_max or 0)))
q = M.compute_quote(0.50, 990, 990, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
check("long 990: bid_max = the normal limit's room (10)", q.bid_size == 1 and q.bid_max == 10, q)
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
check("at the normal limit: no bid (the normal limit pulls, as today)", q.bid is None, q)
q = M.compute_quote(0.50, -600, -600, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
b0 = M.compute_quote(0.50, -600, -600, 0.45, 0.55, cfg, **kw)
check("short 600: ask held at 1, bid unchanged", q.ask_size == 1 and q.ask_max == 100
      and (q.bid, q.bid_size) == (b0.bid, b0.bid_size), q)
q = M.compute_quote(0.50, 600, 600, 0.45, 0.55, cfg, adding_limit_factor=0.5, adding_factor=0.0, **kw)
check("with adding factor 0: withdrawn (as the capital ceiling at 0)", q.bid is None, q)
check("flat: no side is adding, unchanged", M.compute_quote(0.50, 0, 0, 0.45, 0.55, cfg, adding_limit_factor=0.5, **kw)
      == M.compute_quote(0.50, 0, 0, 0.45, 0.55, cfg, **kw))

print("--- decide: dead markets")
FV = 0.52                                                # market 21 (Rep Utah): book 0.48 / 0.56


def bot(**kw):
    a, b = make_bot(); b.cfg.turnover_control_enabled = True   # OFF by default until reviewed; tested on
    b.cfg.turnover_min_state_minutes = 0.0               # flips at once here; the hysteresis has its own tests
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    b.cycle()
    return a, b


def judged(b, start_hours_ago=7.0):
    b.turnover.start = time.time() - start_hours_ago * H
    b.refresh_turnover(time.monotonic(), force=True)


def dec(b, pos, eid="21"):
    return b.decide(b.ex[eid], FV, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic())


a, b = bot()
grace = dec(b, 300)
check("fresh start, no history: not judged -> unchanged quote", b.turnover_flow.get("21") is None
      and not b.ex["21"].turnover_dead and grace.bid_size == 100, (b.turnover_flow, grace))
a0, b0 = bot(turnover_control_enabled=False)
judged(b0)
ref300, ref600, ref_flat = dec(b0, 300), dec(b0, 600), dec(b0, 0)
check("disabled = today (no flow, 7 h observed): unchanged sizes", ref300 == grace and ref300.bid_size == 100, ref300)
dec(b0, 300)
check("...but still classified (counted in status.json), with no log tag", b0.ex["21"].turnover_dead is True
      and b0.ex["21"].turnover_tag == "", b0.ex["21"])
judged(b)
q = dec(b, 300)
check("dead (0 sh/h) holding 300: adding bid 100 x0.25 = 25, reducing ask unchanged",
      q.bid_size == 25 and (q.ask, q.ask_size) == (ref300.ask, ref300.ask_size) and q.bid == ref300.bid, (q, ref300))
check("...tagged dead for the quote log", b.ex["21"].turnover_dead and b.ex["21"].turnover_tag == " dead")
q = dec(b, 600)
check("dead holding 600 > 0.5 x 1,000 limit: adding bid held at 1 share (bid_max 100), ask unchanged",
      q.bid == ref600.bid and q.bid_size == 1 and q.bid_max == 100
      and (q.ask, q.ask_size) == (ref600.ask, ref600.ask_size) and ref600.bid_size == 100, (q, ref600))
q = dec(b, -300)
check("dead short 300: the ask (adding) at 25, bid unchanged", q.ask_size == 25, q)
q = dec(b, 0)
check("dead but flat: unchanged (coverage and the first fill)", q == ref_flat and not b.ex["21"].turnover_dead, q)
q = dec(b, 60)
check("a 60-share position (< min(quote 100, 100)): not 'holding', unchanged", q == dec(b0, 60), q)
b.cfg.turnover_dead_adding_factor = 0.0
check("adding factor 0: the adding side is withdrawn", dec(b, 300).bid is None)
b.cfg.turnover_dead_adding_factor = 0.25
b.capital_over = True
q = dec(b, 300)
check("with the capital ceiling (x0.5 since Package 3) too: factors multiply, 100 x 0.5 x 0.25 = 12", q.bid_size == 12, q)
b.capital_over = False
for k in range(6):                                       # 600 sh traded over the window = 100 sh/h
    b.turnover.add("21", time.time() - (k + 0.5) * H, 100, tape=True)
b.refresh_turnover(time.monotonic(), force=True)
q = dec(b, 300)
check("alive (100 sh/h on the tape): unchanged", q == ref300 and not b.ex["21"].turnover_dead, (q, b.turnover_flow))
b.cfg.turnover_use_tape = False
b.refresh_turnover(time.monotonic(), force=True)
check("...turnover_use_tape off: the tape is ignored -> dead again", dec(b, 300).bid_size == 25)
b.cfg.turnover_use_tape = True
b.cfg.turnover_min_shares_per_hour = 150
b.refresh_turnover(time.monotonic(), force=True)
check("threshold raised live to 150 sh/h: 100 sh/h is dead", dec(b, 300).bid_size == 25)

print("--- the bot: cycle, log line, status.json, summary")


class Grab(logging.Handler):
    def __init__(self):
        super().__init__(); self.msgs = []

    def emit(self, r): self.msgs.append(r.getMessage())


a, b = bot()
a.inv["21"] = 300.0
judged(b)
g = Grab()
M.log.addHandler(g)
old_level, old_prop = M.log.level, M.log.propagate
M.log.setLevel(logging.INFO)
M.log.propagate = False
b.cycle()
M.log.removeHandler(g)
M.log.setLevel(old_level)
M.log.propagate = old_prop
bids = [o for o in a.ours("21") if o[0] == "bid"]
check("cycle: the full-size bid already resting on the dead market STAYS (a size factor alone never makes a resting "
      "order unsafe: hot-fix 2.2), and the adding size wanted for a new quote is 25",
      bids and bids[0][2] == 100 and b.ex["21"].quote is not None and b.ex["21"].quote.bid_size == 25,
      (a.ours("21"), b.ex["21"].quote))
line = [m for m in g.msgs if "Utah" in m and "bid" in m and "fv" in m]
check("quote log line ends ' dead'", line and line[-1].endswith(" dead"), line[-1:] if line else g.msgs[:3])
other = [m for m in g.msgs if "fv" in m and "| bid" in m and "Utah" not in m]
check("...no other market's line is tagged", not any(m.endswith(" dead") for m in other), other[:2])
h = b.health
check("status: 1 dead market holding ~300 x price", h.get("turnover_dead_markets") == 1
      and 140 <= h.get("turnover_dead_capital", 0) <= 170 and h.get("turnover_judged") is True,
      {k: v for k, v in h.items() if k.startswith("turnover")})
check("...top list names it with [capital, sh/h]", list(h.get("turnover_dead_top", {}).values())[0][1] == 0.0,
      h.get("turnover_dead_top"))
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries turnover_dead_markets / _capital", st.get("turnover_dead_markets") == 1
      and "turnover_dead_capital" in st)
_t, msg = M.build_summary(FakeApi(), "/nonexistent/fills.csv", 100_000, value=100_000,
                          health={"turnover_dead_markets": 52, "turnover_dead_capital": 28600})
check("summary line: 'dead-turnover markets: 52 holding 28.6k'", "dead-turnover markets: 52 holding 28.6k" in msg, msg)
_t, msg = M.build_summary(FakeApi(), "/nonexistent/fills.csv", 100_000, value=100_000, health={"orders_resting": 3})
check("...absent without the field", "dead-turnover" not in msg)

print("--- fills and the tape reach the tracker")
a, b = bot()
b.note_turnover([{"exchangeId": 21, "quantity": -250, "filledAt": M.iso(M.utcnow())},
                 {"exchangeId": 21, "quantity": 40, "filledAt": None}])
check("note_turnover: our fills (|qty|, by exchange id as text)", b.turnover.shares("21", time.time() + 1, 6.0) == 290,
      list(b.turnover.ours["21"]))
a.fill("22", True, 100)                                  # a fill through the cycle (log_fills)
b.cycle()
check("a fill read by the cycle is counted", b.turnover.shares("22", time.time() + 1, 6.0, use_tape=False) == 100,
      list(b.turnover.ours.get("22", [])))


class TapeFeed:
    def __init__(self, rows): self.rows = rows
    def take_flow(self):
        out, self.rows = self.rows, []
        return out


b.feed = TapeFeed([(time.time(), "12", 500.0), (time.time(), "12", None)])
b.note_turnover(())
check("the feed's tape (take_flow) goes to the tape deque", b.turnover.shares("12", time.time() + 1, 6.0) == 500)
b.feed = None
feed = M.RealtimeFeed(None, "T", M.Config())
feed._on_market("tournament:T", {"payload": {"trades": [{"exchangeId": 21, "tournamentId": "T", "quantity": 75},
                                                        {"exchangeId": 22, "tournamentId": "X", "quantity": 9}]}})
fl = feed.take_flow()
check("RealtimeFeed keeps a tape copy for turnover (other tournaments ignored)", len(fl) == 1 and fl[0][1:] == ("21", 75.0),
      fl)
check("...take_flow drains it, the recorder's trade_log is untouched", feed.take_flow() == [] and len(feed.trade_log) == 1)

print("--- a restart seeds from fills.csv")
a, b = make_bot(); b.cfg.turnover_control_enabled = True   # OFF by default until reviewed; tested on
with open(b.cfg.fills_csv, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(M.FillLogger.COLUMNS)
    for k in range(84):                                  # a fill every 5 min for 7 h up to the restart
        w.writerow([k, iso_at(time.time() - 60 - 300 * k), "22", k, "bid", 10, 0.5, 0.5, 0.5, 0.5])
b2 = M.Bot(a, b.cfg)
b2.cycle()
check("seeded: judged at once, market 22 alive (~120 sh/h), 21 dead (0)", (b2.turnover_flow.get("22") or 0) >= 99
      and b2.turnover_flow.get("21") == 0.0 and b2.turnover_state["21"][0] and not b2.turnover_state["22"][0],
      (b2.turnover_flow, b2.turnover_state))

print("--- hysteresis: dead below 50, alive again only above 75 sh/h, 30 min minimum state life")
a, b = bot()
b.cfg.turnover_min_state_minutes = 30.0
judged(b)
t0 = time.monotonic()
flows = {"21": 0.0}
b.turnover.per_hour = lambda e, now, w, tape=True: flows.get(e, 0.0)   # drive the flow directly


def at(minutes, flow):
    flows["21"] = flow
    b.refresh_turnover(t0 + 60 * minutes, force=True)
    return b.turnover_state["21"][0]


check("first verdict at once: 0 sh/h -> dead", at(0, 0.0))
check("60 sh/h at 40 min: above 50 but not above 75 -> still dead", at(40, 60.0))
check("80 sh/h at 45 min: above 75 -> alive", not at(45, 80.0))
check("49 sh/h at 50 min: dead again? not yet (alive only 5 min < 30)", not at(50, 49.0))
check("60 sh/h at 70 min: between 50 and 75 while alive -> stays alive", not at(70, 60.0))
check("49 sh/h at 80 min (alive 35 min): dead", at(80, 49.0))
check("100 sh/h at 90 min: dead only 10 min -> stays dead", at(90, 100.0))
check("100 sh/h at 111 min: 31 min dead -> alive", not at(111, 100.0))
b.cfg.turnover_alive_shares_per_hour = 40.0              # set below the dead threshold: the dead one counts
check("alive threshold below the dead one: dead below 50 still flips back only above 50",
      at(150, 45.0) and at(190, 50.0) and not at(230, 50.5))
flows["21"] = None
b.refresh_turnover(t0 + 60 * 300, force=True)
check("not judged any more (None): state dropped, alive", "21" not in b.turnover_state)

print("--- analysis/turnover.py on synthetic data")
spec = importlib.util.spec_from_file_location("turnover_an", os.path.join(HERE, "..", "analysis", "turnover.py"))
an = importlib.util.module_from_spec(spec)
spec.loader.exec_module(an)
d = tempfile.mkdtemp()
fpath, dbp = os.path.join(d, "fills.csv"), os.path.join(d, "market_data.sqlite")
with open(fpath, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(M.FillLogger.COLUMNS)
    for i in range(12):                                  # market 21: 12 fills of 100 in the last 6 h = 2 fills/h
        w.writerow([i, iso_at(now - (i * 0.5 + 0.1) * H), "21", i, "bid", 100, 0.5, 0.5, 0.5, 0.5])
    w.writerow([99, iso_at(now - 7 * H), "11", 99, "bid", 900, 0.5, 0.5, 0.5, 0.5])   # outside the window
db = sqlite3.connect(dbp)
db.execute("CREATE TABLE trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
db.execute("""CREATE TABLE snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL,
              fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
db.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", [(now - 1 * H, "21", 0.5, 3000, "{}"),
                                                         (now - 1 * H, "12", 0.5, 120, "{}")])
old = [(iso_at(now - 600), "live", e, lab, 0.1, 0.2, 0.15, None, None, None, 99) for e, lab in (("11", "Rep Ohio"),)]
cur = [(iso_at(now), "live", "11", "Rep Ohio", 0.10, 0.18, 0.14, None, None, None, 2000.0),
       (iso_at(now), "live", "12", "Dem Ohio", 0.82, 0.90, 0.86, None, None, None, -500.0),
       (iso_at(now), "live", "21", "Rep Utah", 0.48, 0.56, 0.52, None, None, None, 1000.0),
       (iso_at(now), "live", "22", "Dem Utah", 0.44, 0.52, 0.48, None, None, None, 0.0)]
db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", old + cur)
db.commit()
db.close()
rows, hi = an.table(fpath, dbp, hours=6.0, dead=50.0)
by = {r["eid"]: r for r in rows}
check("window ends at the latest snapshot", abs(hi - now) < 1e-3, hi - now)
check("3 markets (22: flat, no flow -> left out)", sorted(by) == ["11", "12", "21"], sorted(by))
r = by["11"]
check("Rep Ohio: 2,000 long at mid 0.14 = 280 capital, no flow in 6 h -> dead, h-flat inf",
      abs(r["capital"] - 280) < 1e-6 and r["dead"] and r["h_flat"] == float("inf") and r["ours_h"] == 0, r)
r = by["12"]
check("Dem Ohio: short 500 (NO at 1 - 0.86 = 0.14): capital 70, tape 20 sh/h -> dead, h-flat 500/10 = 50",
      abs(r["capital"] - 70) < 1e-6 and r["tape_h"] == 20 and r["dead"] and abs(r["h_flat"] - 50) < 1e-9, r)
r = by["21"]
check("Rep Utah: 2 fills/h, 200 sh/h ours, 500 sh/h tape -> flow 500, alive, h-flat 1000/250 = 4",
      abs(r["fills_h"] - 2) < 1e-9 and abs(r["ours_h"] - 200) < 1e-9 and r["flow_h"] == 500 and not r["dead"]
      and abs(r["h_flat"] - 4) < 1e-9, r)
check("sorted by capital (Rep Utah 520 first)", rows[0]["eid"] == "21", [x["eid"] for x in rows])
lines = an.summary(rows, 50.0)
check("summary: dead markets 2 holding 0.4k (280 + 70)", "dead-turnover markets (< 50 sh/h): 2 holding 0.3k" in lines[1]
      or "2 holding 0.4k" in lines[1], lines[1])
check("summary: > 24 h capital 0.4k in 2 markets", "0.3k in 2 markets" in lines[3] or "0.4k in 2 markets" in lines[3],
      lines[3])
rows2, _ = an.table(fpath, os.path.join(d, "missing.sqlite"), hours=6.0, now=now)
check("no sqlite: fills only (Rep Utah 200 sh/h, no positions known)", len(rows2) == 1 and rows2[0]["ours_h"] == 200,
      rows2)
check("parse_ts: 7-digit and 2-digit fractions (Python 3.10 safe)",
      an.parse_ts("2026-10-02T10:00:00.7844565Z") is not None and an.parse_ts("2026-10-02T10:00:00.12+00:00") is not None)
import io                                                 # noqa: E402
import contextlib                                         # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = an.main(["--dir", d, "--top", "5"])
out = buf.getvalue()
check("main() prints the table and totals", rc == 0 and "Rep Utah" in out and "dead-turnover markets" in out
      and out.count("\n") < 20, out[:300])

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
