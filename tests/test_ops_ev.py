"""
Offline tests for the outcome-EV and market-maker carry reporting (read-only, no flag): ev_outcome (cash +
positions held to the outcome at the race-scaled liquid Polymarket price), ev_outcome_unpriced, ev_outcome_scope,
ev_outcome_delta_24h (48 h ring in status.json, restored on restart), mm_carry_24h (middle-band maker fills FIFO
realised, unmatched, value adds, takes, per-class counts, per_day), the summary and realtime lines, and that nothing
else changed against 859e751 (status keys / orders with the new fields removed).

Run:  python tests/test_ops_ev.py      (exit code 0 = all passed)
"""
import csv
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
from fakes import FakeApi, FakeRefs, lvl, make_bot, market, pin_test_sizes, run_cycles   # noqa: E402
import mm_bot as M                                        # noqa: E402

# retired on simplify: the old twin's status / health fields a removed feature owned, at the value it reported while
# the feature was off (the new code drops them); Quote lost its trailing `behind` flag (always False while that sizing
# was off) and the age skew its switch (skew_age_enabled: the old default True, live False)
RETIRED_STATUS = {"tilt_s_applied": (0.0, 0), "tilt_s_applied_headline": (0.0, 0), "fast_unload_windows": (0,),
                  "behind_best_markets": (0,), "mark_frag_total_cap_active": (False,),
                  "fl_bias_markets": ({"bid": 0, "ask": 0},)}
RETIRED_FLIPPED = {"skew_age_enabled": False}
NQ = len(M.Quote.__dataclass_fields__)


def retired(new, old):
    """The old twin's dict less a retired feature's own key at its OFF value when the new one dropped it."""
    return {k: v for k, v in old.items() if not (k in RETIRED_STATUS and k not in new and v in RETIRED_STATUS[k])}


logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


def close(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


NOW = time.time()
H = 3600.0
NEW_KEYS = set(M.Bot.EV_KEYS) | {"ev_outcome_history"}


def scripted():
    """Ohio (11 Rep / 12 Dem) both on Polymarket 0.30 / 0.60 (race-scaled 1/3, 2/3), liquid; Utah Rep (21) held but
    with no Polymarket price. Rep Ohio long 1,000, Dem Ohio short 600, Utah Rep long 200 marked 0.50."""
    api, b = make_bot()
    for e, bk in api.books.items():
        b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
    b.held = {"11": 1000.0, "12": -600.0, "21": 200.0}
    b.pos_marks = {"11": 0.25, "12": 0.70, "21": 0.50}
    b.health = {"account_value": 100000.0}
    b.cur_refs = {"11": 0.30, "12": 0.60}
    b.cur_liquid = {"11", "12"}
    return api, b


def write_fills(b, rows):
    """rows: (exchange, side, qty, YES price, time, order id) -> fills.csv (fill ids 1..n)."""
    with open(b.cfg.fills_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(M.FillLogger.COLUMNS)
        for k, (e, side, q, p, t, oid) in enumerate(rows):
            w.writerow([k + 1, M.iso(M.datetime.fromtimestamp(t, M.timezone.utc)), e, oid, side, q, p,
                        p if side != "?" else "", "", "", 0])
    b.ops_cache = {}


print("--- ev_outcome: the formula")
api, b = scripted()
o = b.ev_fields(NOW)
pos_marks_value = 1000 * 0.25 + 600 * 0.30 + 200 * 0.50                     # 250 + 180 + 100 = 530
want = (100000 - pos_marks_value) + 1000 / 3 + 600 * (1 - 2 / 3) + 200 * 0.50   # Utah at its mark
check("cash fallback (account - positions at marks) + long q x r + short |q| x (1 - r), r race-scaled",
      close(o["ev_outcome"], round(want, 2), 0.01), (o["ev_outcome"], want))
check("race scaling: 0.30 / 0.90 = 1/3 used, not the raw 0.30", close(b.ev_p("11"), 1 / 3) and close(b.ev_p("12"), 2 / 3))
check("unpriced: Utah (no Polymarket) counted", o["ev_outcome_unpriced"] == 1, o["ev_outcome_unpriced"])
check("scope text present", isinstance(o["ev_outcome_scope"], str) and "Polymarket" in o["ev_outcome_scope"])
check("delta None with no history", o["ev_outcome_delta_24h"] is None)
b.cg_cash = 50000.0
o = b.ev_fields(NOW)
check("cash from the cash gate when read (cg_cash): 50,000 + 333.33 + 200 + 100",
      close(o["ev_outcome"], round(50000 + 1000 / 3 + 200 + 100, 2), 0.01), o["ev_outcome"])
b.cur_liquid = {"11"}                                                         # Dem Ohio no longer liquid
o = b.ev_fields(NOW)
check("an illiquid reference: valued at the exchange mark (600 x (1 - 0.70) = 180), counted unpriced",
      close(o["ev_outcome"], round(50000 + 1000 / 3 + 180 + 100, 2), 0.01) and o["ev_outcome_unpriced"] == 2, o)
b.pos_marks = {"11": 0.25, "12": 0.70}                                        # Utah: no mark -> book fair value
fv21 = M.normalise({e: M.fair_value(b.ex[e].book, b.cfg) for e in ("21", "22")})["21"]
o = b.ev_fields(NOW)
check("no mark: the book's fair value (Utah 200 x fv)", close(o["ev_outcome"], round(50000 + 1000 / 3 + 180 + 200 * fv21, 2),
                                                              0.01), (o["ev_outcome"], fv21))
b.ex["21"].book = b.ex["22"].book = {}
o = b.ev_fields(NOW)
check("no mark and no book: left out, still unpriced", close(o["ev_outcome"], round(50000 + 1000 / 3 + 180, 2), 0.01)
      and o["ev_outcome_unpriced"] == 2, o)
b.cg_cash = None
o = b.ev_fields(NOW)
check("...and without the gate's cash the fallback cash is unknown -> ev None", o["ev_outcome"] is None, o)
api, b = scripted()
b.held = {}
o = b.ev_fields(NOW)
check("no positions: ev = account value", close(o["ev_outcome"], 100000.0) and o["ev_outcome_unpriced"] == 0, o)
api, b = make_bot()
o = b.ev_fields(NOW)
check("fresh bot: ev None, never raises", o["ev_outcome"] is None and o["ev_outcome_delta_24h"] is None, o)
api, b = scripted()
b.held = {"11": "garbage"}
o = b.safe_ev_fields()
check("broken input: safe_ev_fields all None", set(o) == set(b.EV_KEYS) and all(v is None for v in o.values()), o)
api, b = scripted()
b.held = {"11": 1000.0}
b.cur_refs = {"11": 0.30}                                    # one leg priced only: the raw price (no scaling)
b.cur_liquid = {"11"}
check("one leg priced: the raw 0.30", close(b.ev_p("11"), 0.30), b.ev_p("11"))

print("--- ev_outcome_delta_24h: the ring")
api, b = scripted()
b.held = {}
for k in range(0, 30):                                       # 30 h of samples, one an hour (account rising 10/h)
    b.health = {"account_value": 100000.0 + 10 * k}
    b.ev_fields(NOW - (29 - k) * H, record=True)
check("samples appended (EV_HIST_SECONDS apart)", len(b.ev_hist) == 30, len(b.ev_hist))
o = b.ev_fields(NOW)
check("delta = ev now - the latest sample at least 24 h old: 100,290 - 100,050 = 240",
      close(o["ev_outcome_delta_24h"], 240.0), o["ev_outcome_delta_24h"])
n = len(b.ev_hist)
b.ev_fields(NOW + 10, record=True)
check("a second sample within EV_HIST_SECONDS is not added", len(b.ev_hist) == n, len(b.ev_hist))
b.ev_fields(NOW + 50 * H, record=True)
check("samples older than 48 h dropped", all(t >= NOW + 2 * H - 1 for t, _ in b.ev_hist), b.ev_hist[:2])
api, b = scripted()
b.held = {}
for k in range(0, 20):
    b.ev_fields(NOW - (19 - k) * H, record=True)
check("under 24 h of history: delta None", b.ev_fields(NOW)["ev_outcome_delta_24h"] is None)
api, b = scripted()
b.held = {}
b.ev_hist = [[NOW - 25 * H, 99000.0], [NOW - 1 * H, 99900.0]]
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("status.json carries ev_outcome_history", isinstance(st.get("ev_outcome_history"), list)
      and st["ev_outcome_history"][0] == [NOW - 25 * H, 99000.0], st.get("ev_outcome_history"))
check("write_status recorded a sample now", len(st["ev_outcome_history"]) == 3, st["ev_outcome_history"])
check("status delta = 100,000 - 99,000", close(st["ev_outcome_delta_24h"], 1000.0), st["ev_outcome_delta_24h"])
cfg = b.cfg
api2 = FakeApi()
api2.markets_list = api.markets_list
api2.books = api.books
b2 = M.Bot(api2, cfg)
check("restart: the ring restored from status.json", b2.ev_hist == st["ev_outcome_history"], b2.ev_hist)
b2.health, b2.held = {"account_value": 100500.0}, {}
check("...and the delta is live straight away (100,500 - 99,000)",
      close(b2.ev_fields()["ev_outcome_delta_24h"], 1500.0), b2.ev_fields())
with open(cfg.status_file, "w") as f:
    f.write("{not json")
check("unreadable status.json: empty ring, no error", M.Bot(api2, cfg).ev_hist == [])

print("--- status.json keys")
api, b = scripted()
b.write_status(True)
st = json.load(open(b.cfg.status_file))
check("every new key in status.json", all(k in st for k in NEW_KEYS), sorted(NEW_KEYS - set(st)))
check("the old ops keys still there", all(k in st for k in b.OPS_KEYS))
check("mm_carry_24h a dict with the fields", isinstance(st["mm_carry_24h"], dict) and all(
    k in st["mm_carry_24h"] for k in ("realised", "per_day", "unmatched_shares", "unmatched_ev", "value_adds_ev",
                                      "takes_ev", "fills", "hours_covered")), st["mm_carry_24h"])

print("--- mm_carry_24h: classification, FIFO, unmatched, per_day")
api, b = scripted()
b.cur_refs = {"11": 0.30, "12": 0.60, "21": 0.05, "22": 0.95}     # Ohio middle (1/3, 2/3), Utah tail (0.05, 0.95)
b.cur_liquid = {"11", "12", "21", "22"}
b.order_meta = {1: {"our_side": "bid"}, 2: {"our_side": "ask"}, 3: {"our_side": "bid"}, 4: {"our_side": "ask"},
                5: {"our_side": "bid"}, 6: {"our_side": "bid", "take": True}, 7: {"our_side": "ask", "take": True},
                8: {"our_side": "bid", "take": True, "alloc": True}, 9: {"our_side": "bid", "alloc": True,
                                                                          "set_ladder": 1},
                11: {"our_side": "bid", "arb": True},
                12: {"our_side": "ask", "take": True, "tilt_exit": True}, 13: {"our_side": "bid"},
                14: {"our_side": "ask"}}
rows = [("11", "bid", 100, 0.30, NOW - 10 * H, 1),       # middle: buy 100 @ .30
        ("11", "ask", 60, 0.34, NOW - 9 * H, 2),          # sell 60 @ .34 -> +2.40
        ("11", "bid", 50, 0.31, NOW - 8 * H, 3),          # buy 50 @ .31 (lots: 40 @ .30, 50 @ .31)
        ("11", "ask", 70, 0.35, NOW - 7 * H, 4),          # sell 70: 40 x .05 + 30 x .04 = 3.20
        ("12", "bid", 20, 0.64, NOW - 6 * H, 5),          # Dem middle: unmatched 20 @ .64 (p 2/3)
        ("11", "bid", 30, 0.32, NOW - 5 * H, 6),          # take: 30 x (1/3 - .32)
        ("12", "ask", 10, 0.70, NOW - 5 * H, 7),          # take: 10 x (.70 - 2/3)
        ("11", "bid", 500, 0.29, NOW - 4 * H, 8),         # alloc take
        ("21", "bid", 500, 0.04, NOW - 4 * H, 9),         # set ladder (alloc)
        ("11", "bid", 500, 0.29, NOW - 4 * H, 11),        # arb / pair
        ("11", "ask", 10, 0.30, NOW - 3 * H, 12),         # tilt-exit take: 10 x (.30 - 1/3)
        ("21", "bid", 100, 0.03, NOW - 2 * H, 13),        # tail maker buy: 100 x (.05 - .03) = 2
        ("22", "ask", 100, 0.97, NOW - 2 * H, 14),        # tail maker sell: 100 x (.97 - .95) = 2
        ("11", "bid", 999, 0.30, NOW - 1 * H, 15),        # our_side "?" in the file: never ours, skipped
        ("11", "bid", 40, 0.20, NOW - 30 * H, 1)]         # older than 24 h: out of the window
rows[-2] = ("11", "?", 999, 0.30, NOW - 1 * H, 15)
rows = sorted(rows, key=lambda r: r[4])
write_fills(b, rows)
FID = {oid: str(k + 1) for k, (*_, t, oid) in enumerate(rows) if t > NOW - 24 * H}   # order id -> fill id
mc = b.mm_carry(NOW)
check("realised = 60 x .04 + 40 x .05 + 30 x .04 = 5.60", close(mc["realised"], 5.6), mc)
check("unmatched middle shares: Ohio 20 @ .31 + Dem 20 @ .64 = 40", close(mc["unmatched_shares"], 40.0), mc)
check("unmatched_ev: 20 x (1/3 - .31) + 20 x (2/3 - .64)",
      close(mc["unmatched_ev"], round(20 * (1 / 3 - .31) + 20 * (2 / 3 - .64), 2), 0.006), mc["unmatched_ev"])
check("value_adds_ev: tail maker buy 2 + sell 2 = 4", close(mc["value_adds_ev"], 4.0), mc["value_adds_ev"])
check("takes_ev: 30 x (1/3 - .32) + 10 x (.70 - 2/3) + 10 x (.30 - 1/3) (tilt exit counted a take)",
      close(mc["takes_ev"], round(30 * (1 / 3 - .32) + 10 * (.70 - 2 / 3) + 10 * (.30 - 1 / 3), 2), 0.006),
      mc["takes_ev"])
f = mc["fills"]
check("counts: 5 middle maker, 2 tail maker", f["maker_mid"] == 5 and f["maker_tail"] == 2, f)
check("counts: 3 takes (alloc takes not)", f["take"] == 3, f)
check("counts: alloc 2 (alloc take + set ladder), arb 1", f["alloc"] == 2 and f["arb"] == 1, f)
check("the 30-h-old fill and the '?' row excluded", sum(f.values()) == 13, f)
check("hours covered capped at 24 (file starts 30 h ago)", close(mc["hours_covered"], 24.0), mc["hours_covered"])
check("per_day = realised x 24 / 24", close(mc["per_day"], 5.6), mc["per_day"])
check("band = the settings", mc["band"] == [b.cfg.value_mid_low, b.cfg.value_mid_high], mc["band"])
check("p for fills not seen this run: the price now (p_now)", mc["p_now"] == 10 and mc["p_at_fill"] == 0, mc)
b.cfg.value_mid_low, b.cfg.value_mid_high = 0.40, 0.85                # Ohio Rep (1/3) now outside the band
mc2 = b.mm_carry(NOW)
check("band from the settings: Ohio Rep's maker fills become tail (value adds), realised 0",
      close(mc2["realised"], 0.0) and mc2["fills"]["maker_tail"] == 6 and mc2["fills"]["maker_mid"] == 1, mc2)
b.cfg.value_mid_low, b.cfg.value_mid_high = 0.15, 0.85
b.ev_fill_p = {FID[k]: 0.50 for k in (1, 2, 3, 4, 13)}   # p logged at fill time wins
mc3 = b.mm_carry(NOW)
check("p at fill time used when logged: Utah fill 13 becomes middle (unmatched), value adds 2",
      mc3["fills"]["maker_mid"] == 6 and close(mc3["value_adds_ev"], 2.0) and mc3["p_at_fill"] == 5, mc3)
b.ev_fill_p = {}
b.order_meta = {}
mc4 = b.mm_carry(NOW)
check("notes gone: every fill counted maker, meta_missing says so", mc4["meta_missing"] == 13
      and mc4["fills"]["take"] == 0, mc4)
api, b = scripted()
write_fills(b, [("11", "bid", 100, 0.30, NOW - 6 * H, 1), ("11", "ask", 100, 0.36, NOW - 5 * H, 2)])
b.order_meta = {1: {"our_side": "bid"}, 2: {"our_side": "ask"}}
mc = b.mm_carry(NOW)
check("6 h of fills: per_day = 6 x 24 / 6 = 24", close(mc["realised"], 6.0) and close(mc["per_day"], 24.0)
      and close(mc["hours_covered"], 6.0), mc)
write_fills(b, [("12", "ask", 100, 0.70, NOW - 6 * H, 1), ("12", "bid", 40, 0.62, NOW - 5 * H, 2)])
b.order_meta = {1: {"our_side": "ask"}, 2: {"our_side": "bid"}}
mc = b.mm_carry(NOW)
check("short first then buy back: 40 x (.70 - .62) = 3.20, 60 unmatched short",
      close(mc["realised"], 3.2) and close(mc["unmatched_shares"], 60.0)
      and close(mc["unmatched_ev"], round(-60 * (2 / 3 - 0.70), 2), 0.006), mc)
api, b = make_bot()
mc = b.mm_carry(NOW)
check("no fills: zeros, per_day None", mc["realised"] == 0 and mc["per_day"] is None and mc["hours_covered"] is None, mc)
api, b = scripted()
api.fills = [{"id": 77, "orderId": 1, "exchangeId": "11", "price": 0.3, "quantity": 5, "filledAt": M.iso(M.util.utcnow())}]
b.log_fills({})
check("log_fills notes the Polymarket p of each new fill", close(b.ev_fill_p.get("77"), 1 / 3), b.ev_fill_p)

print("--- summary and realtime lines")
ops = {"ev_outcome": 101234.0, "ev_outcome_delta_24h": 1234.4, "ev_outcome_unpriced": 2,
       "mm_carry_24h": {"realised": 12.3, "value_adds_ev": 340.2, "takes_ev": -25.1}}
check("EV part format", M.ev_outcome_part(ops) == "EV outcome 101.2k (+1,234 24h, 2 unpriced)", M.ev_outcome_part(ops))
check("unknown delta shows '?'", M.ev_outcome_part({**ops, "ev_outcome_delta_24h": None})
      == "EV outcome 101.2k (? 24h, 2 unpriced)")
check("no ev: no part", M.ev_outcome_part({}) is None and M.ev_outcome_part(None) is None)
check("carry part format", M.mm_carry_part(ops) == "MM carry 24h +12 (mid), value adds +340, takes -25",
      M.mm_carry_part(ops))
api, b = scripted()
b.write_status(True)
line = b.summary_ops_line(100000.0)
check("summary_ops_line carries ' | EV outcome ... unpriced)'", " | EV outcome " in line and "unpriced)" in line, line)
check("...and ' | MM carry 24h X (mid), value adds Y, takes Z'", " | MM carry 24h " in line and ", value adds " in line
      and ", takes " in line, line)
check("...the EV part from the status write", M.ev_outcome_part(b.ops_last) in line, line)


class Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, r):
        self.lines.append(r.getMessage())


g = Grab()
M.log.addHandler(g)
M.log.setLevel(logging.INFO)
api, b = make_bot()
b.refs = FakeRefs({"11": 0.12, "12": 0.88, "21": 0.50, "22": 0.50})
run_cycles(b, 2)
M.log.removeHandler(g)
M.log.setLevel(logging.ERROR)
cyc = [x for x in g.lines if x.startswith(("realtime |", "polling |", "polling (realtime"))]
check("the cycle (realtime / polling |) line ends with ' | EV outcome ... unpriced)' once known",
      bool(cyc) and " | EV outcome " in cyc[-1] and cyc[-1].endswith("unpriced)"), cyc[-1:] or g.lines[:3])
b.ops_last = {}
check("...and nothing appended while unknown", b.ev_line_part() == "")

print("--- nothing else changed (vs 859e751, new fields removed)")
old_src = None
try:
    old_src = subprocess.run(["git", "show", "859e751:mm_bot.py"], cwd=os.path.dirname(HERE), capture_output=True,
                             text=True, check=True).stdout
except (OSError, subprocess.CalledProcessError) as e:
    print(f"SKIP (no git history: {e})")
if old_src:
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "mm_old.py"), "w") as fh:
        fh.write(old_src)
    spec = importlib.util.spec_from_file_location("mm_old", os.path.join(d, "mm_old.py"))
    OLD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(OLD)
    OLD.CFG.alert_url, OLD.notify = "", (lambda *a, **k: False)

    def grid(mod, scenario):
        api = FakeApi(True)
        api.markets_list = [market("1", "11", "Republican", "Ohio Senate"),
                            market("2", "12", "Democratic", "Ohio Senate"),
                            market("3", "21", "Republican", "Utah Senate"), market("4", "22", "Democratic", "Utah Senate")]
        api.books = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
                     "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
                     "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
                     "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
        cfg = mod.Config()
        t = tempfile.mkdtemp()
        cfg.fills_csv, cfg.status_file, cfg.order_notes_file, cfg.kill_file = (
            os.path.join(t, n) for n in ("fills.csv", "status.json", "notes.json", "kill.tripped"))
        cfg.position_lots_file = os.path.join(t, "lots.json")
        cfg.record_file, cfg.ref_map_file, cfg.summary_every_hours = "", "", 0
        cfg.overrides_file = os.path.join(t, "overrides.json")
        cfg.market_edge_file = os.path.join(t, "market_edge.json")
        cfg.handover_file = os.path.join(t, "handover.json")
        cfg.handover_exit_max_seconds = 0
        pin_test_sizes(cfg)
        cfg.churn_control, cfg.fl_bias_enabled, cfg.slow_poll_seconds, cfg.realtime_enabled = False, False, 0, False
        for k, v in scenario.items():
            setattr(cfg, k, v)
        bot = mod.Bot(api, cfg)
        bot.refs = FakeRefs({"11": 0.12, "12": 0.88, "21": 0.50, "22": 0.50})
        real, n = bot.cycle, {"k": 0}

        def cycle():                              # other traders hit our quotes before cycles 3 and 4
            n["k"] += 1
            if n["k"] in (3, 4):
                for e, side in (("11", True), ("12", False), ("21", True)):
                    api.fill(e, side, 30)
            real()
        bot.cycle = cycle
        run_cycles(bot, 5)
        fills = len(api.fills)
        st = json.load(open(cfg.status_file))
        drop = NEW_KEYS | set(getattr(M.Bot, "MM_FUNDING_KEYS", ())) | {  # (and the later P14 report key)
            "updated", "seconds_since_cycle", "tilt_state", "polymarket_fetch_seconds", "cycle_seconds", "phases"}
        st = {k: v for k, v in st.items() if k not in drop and not k.endswith("_seconds")}
        orders = sorted((o["exchangeId"], o.get("action"), o.get("side"), o["price"], o["quantity"])
                        for o in api.wire)
        return st, orders, fills, sorted(api.orders and [(o["exchangeId"], o["price"], o["quantity"])
                                                 for o in api.orders.values()])

    for name, sc in (("default", {}), ("value_mode", {"value_mode": True}), ("cash gate", {"cash_gate_enabled": True})):
        try:
            new, old = grid(M, sc), grid(OLD, sc)
        except Exception as e:                    # a setting this head lacks: report, not crash
            check(f"grid {name}: ran", False, repr(e))
            continue
        old = (retired(new[0], old[0]),) + old[1:]
        diff = {k for k in set(new[0]) | set(old[0]) if new[0].get(k) != old[0].get(k)}
        check(f"grid {name}: status.json without the new fields equals 859e751's", not diff,
              {k: (new[0].get(k), old[0].get(k)) for k in sorted(diff)})
        check(f"grid {name}: fills landed ({new[2]}) and orders sent equal 859e751's",
              new[2] > 0 and new[1] == old[1] and new[2] == old[2] and new[3] == old[3],
              (new[1][:4], old[1][:4]))

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
