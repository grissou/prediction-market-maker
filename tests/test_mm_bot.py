"""
Offline tests for mm_bot.py. No network: a FakeApi plays the exchange, following the API spec
(sells you don't hold become NO buys, fills, expiry, crossing orders trade immediately).

Run:  python tests/test_mm_bot.py      (exit code 0 = all passed; GitHub Actions runs this on every push)
"""
import csv
from collections import deque
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                  # the project folder, where mm_bot.py lives
os.environ.setdefault("SUPERMARKET_API_KEY", "test-key")
os.environ.setdefault("TOURNAMENT_SLUG", "test")

import mm_bot as M                                        # noqa: E402
from mm_bot import *                                      # noqa: E402,F401,F403
from fakes import FakeApi, FakeFeed, FakeRefs, lvl, make_bot, market, pin_test_sizes, run_cycles   # noqa: E402

# Never send real phone notifications from tests, even in a terminal where ALERT_URL is set.
M.CFG.alert_url = ""
_live = Config()                                          # the real defaults, checked below
pin_test_sizes(M.CFG)                                     # exact expected numbers below use the old sizes
M.notify = lambda *a, **k: False

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))

# =============================================================================================
# PURE FUNCTIONS
# =============================================================================================
print("--- pricing and quoting")
house = {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]}
q = compute_quote(fair_value(house), 0, 0, 0.10, 0.18)
check("pennying: quote one tick inside the house", (q.bid, q.ask) == (0.105, 0.175), q)
spoof = {"bids": [lvl(0.175, 1)] + house["bids"], "asks": house["asks"]}
check("anti-spoof: a 1-share bid doesn't move fair value", abs(fair_value(spoof) - 0.14) < 1e-9, fair_value(spoof))
q = compute_quote(0.14, 0, 0, 0.135, 0.145)
check("penny war capped at min_edge", q.bid <= 0.13 and q.ask >= 0.15, q)
q = compute_quote(0.5, 950.5, 950.5, None, None)
check("sizes are whole numbers", isinstance(q.bid_size, int) and q.bid_size == 49, q)
q = compute_quote(0.14, 500, 500, 0.10, 0.18, reduce_only=True)
check("reduce-only while long: no bid, ask <= position", q.bid is None and 0 < q.ask_size <= 500, q)
q = compute_quote(0.14, 0, 0, 0.10, 0.18, no_bid=True)
check("no_bid blocks the bid", q.bid is None and q.ask is not None, q)
q = compute_quote(0.03, 0, 0, 0.02, 0.05, ask_cap=0)
check("ask_cap=0 removes the ask, keeps the bid", q.ask is None and q.bid is not None, q)
check("worst case: lone long 500 @0.14 = 70", abs(worst_case_loss([(500, 0.14)]) - 70) < 1e-9)
check("worst case: hedged race pair = 0", worst_case_loss([(500, 0.14), (500, 0.86)]) < 1e-9)
check("worst case: NO 500 @0.14 = 430", abs(worst_case_loss([(-500, 0.14)]) - 430) < 1e-9)
check("worst case: sold YES on both parties (arbitrage) = 0", worst_case_loss([(-200, 0.55), (-200, 0.45)]) < 1e-9)
r = parse_order({"id": 1, "exchangeId": "7", "side": "no", "action": "buy", "priceLimit": 0.825, "quantity": 100, "open": True})
check("buy NO @0.825 is read as a YES ask @0.175", (r.is_bid, r.price) == (False, 0.175), r)
r = parse_order({"id": 1, "exchangeId": "7", "side": "yes", "action": "buy", "priceLimit": 0.1, "quantity": 5,
                 "open": True, "expirationDate": "2000-01-01T00:00:00Z"})
check("expired order is ignored", r is None)
now = utcnow()
o = Resting(1, "7", True, 0.105, 70, now + timedelta(minutes=9))
check("keep partly-filled order (70/100)", not side_needs_change([o], 0.105, 100, CFG, now))
o.qty = 40
check("replace mostly-filled order (40/100)", side_needs_change([o], 0.105, 100, CFG, now))
o.qty, o.expires = 100, now + timedelta(seconds=60)
check("replace order about to expire", side_needs_change([o], 0.105, 100, CFG, now))

# =============================================================================================
# THE BOT AGAINST THE FAKE EXCHANGE
# =============================================================================================
print("--- core loop")
api, bot = make_bot()
check("races grouped from titles", dict(bot.groups) == {"Ohio Senate": ["11", "12"], "Utah Senate": ["21", "22"]}, dict(bot.groups))
bot.cycle()
check("cycle 1: quotes pennied inside the house", api.ours("11") == [("ask", 0.175, 100), ("bid", 0.105, 100)], api.ours("11"))
check("cycle 1: all 4 exchanges quoted in ONE batch call", api.sent("batch") == [("batch", 8)], api.sent("batch"))

api.calls.clear(); bot.cycle()
check("cycle 2 (nothing changed): no cancels, no new orders, no book downloads",
      not [c for c in api.calls if c[0] in ("batch", "cancel_all", "cancel_order", "book")], api.calls)

api.books["11"]["bids"].insert(0, lvl(0.11, 300))           # a rival pennies our bid
api.calls.clear(); bot.cycle()
check("rival bid spotted via bulk prices -> only book 11 re-downloaded", api.sent("book") == [("book", "11")], api.sent("book"))
check("re-penny to 0.115, only the bid replaced (ask keeps its queue spot)",
      api.ours("11") == [("ask", 0.175, 100), ("bid", 0.115, 100)] and not api.sent("cancel_all"), api.ours("11"))

api.fill("11", True, 30); api.calls.clear(); bot.cycle()
check("fill 30 of 100: order kept (70 left), fill logged",
      api.ours("11")[1] == ("bid", 0.115, 70) and len(read_fills(bot.cfg.fills_csv)) == 1, api.ours("11"))
row = read_fills(bot.cfg.fills_csv)[0]
check("fill attributed to our bid, with the fair value at quote time", row["our_side"] == "bid" and row["quote_price"] == "0.115", row)
check("race netting: long 30 Rep Ohio -> Dem Ohio effective inventory -30",
      bot.ex["12"].eff == -30 and bot.ex["11"].eff == 30, (bot.ex["11"].eff, bot.ex["12"].eff))

api.fill("11", True, 40); api.calls.clear(); bot.cycle()
check("filled down to 30 of 100: order replaced", api.ours("11")[1][2] == 100, api.ours("11"))

api.fail_book.add("21"); bot.ex["21"].book_time = bot.ex["22"].book_time = time.monotonic() - 1000
api.calls.clear(); bot.cycle()
check("book 21 failing doesn't stop book 22 being refreshed", ("book", "22") in api.calls, api.calls)
api.fail_book.clear()

for e in bot.ex.values():                                   # downloaded 400 s ago: past book_reverify_seconds (120 s)
    e.book_time -= 400; e.verified -= 400                   #   but before book_max_age (600 s)
api.calls.clear(); bot.cycle()
check("old download but the bulk check confirms it -> still trusted and quoted, not re-downloaded",
      len(api.ours("11")) == 2 and len(api.ours("12")) == 2 and not {("book", "11"), ("book", "12")} & set(api.sent("book")),
      (api.ours("11"), api.ours("12"), api.sent("book")))

api.markets_list = api.markets_list[:2]; bot.load_markets()
check("closed markets cancelled and dropped", set(bot.ex) == {"11", "12"} and not api.ours("21") and not api.ours("22"))

api.cancel_all("T"); api.batch_error = ApiError(502, "ORDER_STATUS_UNKNOWN", "?")
bot.cycle()
check("502 on placement marks exchanges pending", bot.ex["11"].pending_until > time.monotonic())
api.batch_error = None; api.calls.clear(); bot.cycle()
check("no re-placement while pending", not api.sent("batch"))
bot.ex["11"].pending_until = bot.ex["12"].pending_until = 0

api.cancel_all("T"); api.calls.clear()
orig = bot.refresh_books
bot.refresh_books = lambda *a: (orig(*a), setattr(bot, "running", False))
bot.cycle(); bot.refresh_books = orig
check("Ctrl+C mid-cycle: no orders placed afterwards", not api.sent("batch"))

print("--- errors, shutdown, dry run")
api2, bot2 = make_bot()
n = {"k": 0}
real_cycle2 = bot2.cycle
def flaky():
    n["k"] += 1
    if n["k"] == 1:
        return real_cycle2()
    if n["k"] == 2:
        raise ValueError("deliberate test bug")
    bot2.running = False
bot2.cycle, bot2.cfg.loop_seconds = flaky, 0
logging.disable(logging.CRITICAL); bot2.run(); logging.disable(logging.NOTSET)
check("unexpected error pulls all quotes; shutdown cancels on exit",
      ("cancel_all", None) in api2.calls and not api2.orders, api2.sent("cancel_all"))

api3, bot3 = make_bot(); api3.equity = 100000 * (1 - bot3.cfg.max_drawdown_pct) - 5000
bot3.cycle(); first = bot3.running; bot3.cycle()
check("kill switch needs two bad readings, then stops", first and not bot3.running)

api4, bot4 = make_bot(live=False)
bot4.cycle(); api4.calls.clear(); bot4.cycle()
check("dry run: nothing sent, second cycle quiet (simulated orders kept)",
      not api4.orders and len(bot4.sim) == 8 and not api4.sent("batch"))

api5, bot5 = make_bot(only="11,12")
api5.orders[999] = {"id": 999, "exchangeId": "22", "side": "yes", "action": "buy", "priceLimit": 0.3, "quantity": 5, "open": True}
run_cycles(bot5, 1)
check("ONLY_EXCHANGES: bot's orders cancelled, a manual order on 22 untouched", list(api5.orders) == [999], list(api5.orders))

print("--- kill switch and locked cash")
def locked(a): return reserved_cash(a.open_orders("T"))
for leaves_out in (True, False):
    a, b = make_bot()
    b.cfg.reserved_calib_min = 100
    b.reserved_mode = None                # "auto" (the live default is "ignore" since day one)
    a.pnl = (lambda a=a: {"totalAccountValue": 100000 - locked(a)}) if leaves_out else (lambda: {"totalAccountValue": 100000})
    b.cycle(); b.cycle()                  # orders go up -> first observation
    a.cancel_all("T"); b.cycle()          # orders vanish without fills -> second observation
    want = "add" if leaves_out else "ignore"
    check(f"auto-detects the API {'leaving out' if leaves_out else 'including'} locked cash -> '{want}'", b.reserved_mode == want, b.reserved_mode)

a, b = make_bot(); b.cfg.max_drawdown_pct = 0.002             # floor 99,800; our quotes lock ~370
b.reserved_mode = None                                         # "auto"
a.pnl = lambda: {"totalAccountValue": 100000 - locked(a)}
for _ in range(4):
    b.cycle()
check("quotes locking cash don't trip the kill switch (API leaves it out)", b.running, locked(a))
a.pnl = lambda: {"totalAccountValue": 99000 - locked(a)}     # a real 1,000 loss
b.cycle(); b.cycle()
check("...but a real loss still does", not b.running)

print("--- server features")
apiw, botw = make_bot()
status_seq = iter(["draft", "active"])
apiw.tournament = lambda: {"id": "T", "initialBalance": 100000, "status": next(status_seq), "startDate": "2099-01-01T00:00:00Z",
                           "endDate": "2026-11-04T17:00:00Z"}
botw.cfg.start_check_seconds = 0
botw.wait_for_trading()
check("while waiting for the open, books are downloaded in advance",
      all(e.book is not None for e in botw.ex.values()) and not apiw.sent("batch"), apiw.calls)

api7, bot7 = make_bot(); api7.equity = 1000
bot7.cycle(); bot7.cycle()
check("kill switch writes the marker file and sets exit code 4", os.path.exists(bot7.cfg.kill_file) and bot7.exit_code == EXIT_KILLED)
api7.equity = 100000
bot7b = Bot(api7, bot7.cfg); api7.calls.clear(); bot7b.run()
check("after a kill, a restart refuses to trade until the file is deleted",
      bot7b.exit_code == EXIT_KILLED and not api7.sent("batch"))

api8, bot8 = make_bot()
run_cycles(bot8, 2)
st = json.load(open(bot8.cfg.status_file))
check("status.json written with health info", st["last_cycle_ok"] and st["mode"] == "live" and "orders_resting" in st, st)
notes = json.load(open(bot8.cfg.order_notes_file))
bot8b = Bot(api8, bot8.cfg)
check("order notes survive a restart (fills stay attributed)", notes and bot8b.order_meta.keys() == {int(k) for k in notes})

api9, bot9 = make_bot()
api9.positions = lambda: (_ for _ in ()).throw(ApiError(401, "API_KEY_REVOKED", "revoked"))
bot9.cfg.loop_seconds = 0
try:
    logging.disable(logging.CRITICAL); bot9.run(); code = None
except SystemExit as e:
    code = e.code
finally:
    logging.disable(logging.NOTSET)
check("revoked API key -> exits with code 3 (systemd won't restart)", code == EXIT_FATAL, code)

envf = tempfile.mktemp()
open(envf, "w").write('# comment\nMM_TEST_A="hello"\nMM_TEST_B = x=y\n')
os.environ["MM_TEST_B"] = "keep"; load_env_file(envf)
check(".env loader: reads values, doesn't override existing ones", os.environ["MM_TEST_A"] == "hello" and os.environ["MM_TEST_B"] == "keep")

# =============================================================================================
# NEW FEATURES: arbitrage, tail guard, reference prices, recording, phone summary, parallel
# =============================================================================================
print("--- arbitrage")
arb_books = {"11": {"bids": [lvl(0.60, 300)], "asks": [lvl(0.70, 300)]},    # Rep Ohio bid 0.60
             "12": {"bids": [lvl(0.45, 200)], "asks": [lvl(0.55, 200)]},    # Dem Ohio bid 0.45 -> sum 1.05
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
             "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
a, b = make_bot(books=json.loads(json.dumps(arb_books)))
b.cycle()
check("bids summing to 1.05 -> sold 200 YES on both parties", a.inv == {"11": -200, "12": -200}, a.inv)
check("no arbitrage leftovers resting, Ohio not quoted this cycle", not a.ours("11") and not a.ours("12"), (a.ours("11"), a.ours("12")))
check("the other race is still quoted normally", len(a.ours("21")) == 2 and len(a.ours("22")) == 2)
b.cycle()
check("arbitrage position has zero worst-case loss", b.health["worst_case_loss"] < 1e-6, b.health["worst_case_loss"])
arb_rows = [r for r in read_fills(b.cfg.fills_csv) if r["exchange_id"] in ("11", "12")]
check("arbitrage fills logged as our asks at 0.60 / 0.45",
      sorted((r["our_side"], r["quote_price"]) for r in arb_rows) == [("ask", "0.45"), ("ask", "0.6")], arb_rows)
check("arbitrage counted once", b.arbs_total == 1, b.arbs_total)

a, b = make_bot(live=False, books=json.loads(json.dumps(arb_books)))
b.cycle(); b.cycle()
check("dry run: arbitrage logged but nothing sent, and not repeated during the cooldown",
      not a.inv and b.arbs_total == 1 and not a.orders)

print("--- tail guard")
tail_market = {"id": "5", "title": "Will turnout exceed 70%?", "isComposite": False,
               "settlementDate": "2026-11-04T00:00:00Z", "exchanges": [{"id": "51", "option": "YES"}]}
tail_books = {**json.loads(json.dumps(arb_books)), "51": {"bids": [lvl(0.02, 1000)], "asks": [lvl(0.05, 1000)]}}
tail_books["11"], tail_books["12"] = {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]}, {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]}
a, b = make_bot(books=tail_books, extra_markets=[tail_market])
b.cycle()
check("fair value 0.035: bid only, no ask (won't risk 0.97 to earn 1c)", [s for s, _, _ in a.ours("51")] == ["bid"], a.ours("51"))
a.inv["51"] = 50; a.cancel_all("T"); b.cycle()
check("...but an ask up to the 50 shares we hold is allowed (shrinks the position)",
      [(s, n) for s, _, n in a.ours("51") if s == "ask"] == [("ask", 50)], a.ours("51"))

print("--- reference prices")
a, b = make_bot()
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30})           # outside market: 0.30 vs our 0.14
b.cycle()
check("outside market says Rep Ohio is worth more -> we stop selling it (bid only)",
      [s for s, _, _ in a.ours("11")] == ["bid"], a.ours("11"))
check("markets without a reference quote both sides", len(a.ours("12")) == 2, a.ours("12"))
a, b = make_bot()
b.refs, b.cfg.ref_weight = FakeRefs({"Ohio Senate|Republican": 0.30}), 0.5
b.cycle()
check("ref_weight 0.5 leans fair value toward the outside market (0.14 -> ~0.20)",
      0.19 < b.ex["11"].last_fv < 0.22, b.ex["11"].last_fv)
a, b = make_bot()
b.refs, b.cfg.ref_weight = FakeRefs({"Ohio Senate|Republican": 0.30}, spread=None), 0.5   # last trade only
b.cycle()
check("thin / last-trade-only Polymarket price: NOT leaned on (fair value stays the book's 0.14)",
      abs(b.ex["11"].last_fv - 0.14) < 1e-9, b.ex["11"].last_fv)
check("...but it still drives the guard (bid only)", [s for s, _, _ in a.ours("11")] == ["bid"], a.ours("11"))

print("--- Kelly sizing and sizes as a share of the account")
QK = Config(); QK.kelly_fraction = 0.25       # quarter Kelly, for exact numbers
check("Kelly: Polymarket 0.20, buying YES at 0.14 -> quarter Kelly 1.74% of 100k -> 12,458 shares",
      kelly_position(0.20, 0.14, 100_000, QK, yes=True) == 12458, kelly_position(0.20, 0.14, 100_000, QK, yes=True))
HK = Config(); HK.kelly_fraction = 0.5
check("Kelly: half Kelly would want 3.5% there, so the 2% cap applies -> 14,285 shares",
      kelly_position(0.20, 0.14, 100_000, HK, yes=True) == 14285, kelly_position(0.20, 0.14, 100_000, HK, yes=True))
check("default is quarter Kelly", CFG.kelly_fraction == 0.25)
check("Kelly: small edge (fair 0.14, bid 0.13) -> half Kelly holds twice what quarter Kelly does",
      kelly_position(0.14, 0.13, 100_000, HK) in (2 * kelly_position(0.14, 0.13, 100_000, QK), 2 * kelly_position(0.14, 0.13, 100_000, QK) + 1))
check("Kelly: capped at 2% of the account in one market (selling YES at 0.14 vs Polymarket 0.10 -> 2,325)",
      kelly_position(0.10, 0.14, 100_000, CFG, yes=False) == 2325, kelly_position(0.10, 0.14, 100_000, CFG, yes=False))
check("Kelly: no edge -> only the 0.1% allowance (100 shares)", kelly_position(0.12, 0.14, 100_000, CFG, yes=True) == 100)
check("Kelly scales with the account (150k -> 1.5x the position)",
      abs(kelly_position(0.20, 0.14, 150_000, QK) / kelly_position(0.20, 0.14, 100_000, QK) - 1.5) < 0.001)
q = compute_quote(0.18, 1000, 1000, 0.10, 0.18, bankroll=100_000)
check("without Kelly: at the 1,000-share limit (1% of 100k), no more buying", q.bid is None, q)
q = compute_quote(0.18, 1000, 1000, 0.10, 0.18, kelly_p=0.20, bankroll=100_000)
check("with Kelly (Polymarket 0.20): still buying past 1,000 shares", q.bid_size == 100, q)
q = compute_quote(0.13, 150, 150, 0.10, 0.18, kelly_p=0.09, bankroll=100_000)
check("with Kelly: Polymarket 0.09 is below our 0.105 bid (no edge buying) and we hold 150 -> stop adding "
      "(allowance 100); selling still allowed", q.bid is None and q.ask is not None, q)
q = compute_quote(0.14, 0, 0, 0.10, 0.18, bankroll=200_000)
check("order size is a share of the account: 200k -> 200 shares per order", (q.bid_size, q.ask_size) == (200, 200), q)
q = compute_quote(0.14, 0, 0, 0.10, 0.18, bankroll=50_000)
check("...and shrinks after losses: 50k -> 50 shares", (q.bid_size, q.ask_size) == (50, 50), q)

both = lambda r, d: FakeRefs({"Ohio Senate|Republican": r, "Ohio Senate|Democratic": d})
a, b = make_bot()                                           # default ref_weight 0.7
b.refs = both(0.18, 0.82)                                   # Polymarket 4c above the book (0.14)
b.cycle()
check("with 70% weighting, a 4c gap vs the tournament book is under the 5c guard -> both sides quoted",
      len(a.ours("11")) == 2, a.ours("11"))
a, b = make_bot()
b.refs = both(0.20, 0.80)                                   # 6c above the book
b.cycle()
check("...a 6c gap vs the tournament book trips the guard (not shrunk by the weighting) -> bid only",
      [s for s, _, _ in a.ours("11")] == ["bid"], a.ours("11"))

a, b = make_bot()
b.refs = both(0.14, 0.86)
b.cycle()
b.refs.new_reading({"Ohio Senate|Republican": 0.18, "Ohio Senate|Democratic": 0.82},
                   {"Ohio Senate|Republican": 0.04, "Ohio Senate|Democratic": 0.04})   # Polymarket jumps 4c
a.calls.clear(); b.cycle()
check("Polymarket jumps 4c between readings -> that race's quotes pulled", not a.ours("11") and not a.ours("12"),
      (a.ours("11"), a.ours("12")))
check("...other races untouched", len(a.ours("21")) == 2)
b.refs.new_reading(b.refs.prices, {"Ohio Senate|Republican": 0.01})
for e in ("11", "12"):
    b.ex[e].cooldown_until = 0                              # cooldown over
b.cycle()
check("small Polymarket moves (1c) don't pull quotes; after the cooldown quoting resumes", len(a.ours("11")) == 2, a.ours("11"))

print("--- recording and phone summary")
a, b = make_bot()
b.cfg.record_file = os.path.join(tempfile.mkdtemp(), "data.sqlite")
b.db = b.open_recorder()
b.cycle(); b.cycle()
db = sqlite3.connect(b.cfg.record_file)
n_snap = db.execute("select count(*) from snapshots").fetchone()[0]
n_acct = db.execute("select count(*) from account").fetchone()[0]
row = db.execute("select best_bid, best_ask, fair_value, our_bid, our_ask from snapshots where eid='11'").fetchone()
check("one snapshot per market per record_seconds (2 quick cycles -> 1 set)", (n_snap, n_acct) == (4, 1), (n_snap, n_acct))
check("snapshot has book, fair value and our quote", row[2] and row[3] == 0.105 and row[4] == 0.175, row)

sent = []
real_notify, M.notify = M.notify, lambda message, title="", **k: sent.append((title, message)) or True
a, b = make_bot()
b.cfg.summary_every_hours = 1                               # every hour, so the current hour is always due
b.phase = "trading"                                         # as run() sets it once trading is open
a.equity = 101_234.0
b.cycle(); b.maybe_summary(); b.maybe_summary()
title, message = sent[0] if sent else ("", "")
check("summary sent once per slot (not every cycle)", len(sent) == 1, sent)
check("title shows the P&L", title == "mm_bot: +1,234 (+1.2%)", title)
check("message has account, rank, Smart Score, stats since the last update and bot health",
      all(x in message for x in ("Account 101,234", "Rank 3 of 50", "Smart Score 61.2 (rank 150 of 900, ELITE)",
                                 "Last 1h:", "orders resting")), message)
sent.clear()
a2, b2 = make_bot()
b2.cfg.summary_every_hours = 2
b2.cycle(); b2.maybe_summary()
check("every 2 hours: sent on even UTC hours only", len(sent) == (1 if utcnow().hour % 2 == 0 else 0), (utcnow().hour, sent))
b.last_summary_slot, b.arbs_total, b.takes_total = None, 3, 1
sent.clear(); b.maybe_summary()
check("the next summary shows the change since the previous one (account, arbitrages, takes)",
      sent and "+0 in 1h" in sent[0][1] and "3 arbitrages" in sent[0][1] and "1 takes" in sent[0][1], sent)
check("status line first: OK, and what the bot is doing", message.split("\n")[0].startswith("Status: OK | trading"), message)
sent.clear(); calls_prio = []
M.notify = lambda message, title="", priority="default", **k: sent.append((title, message)) or calls_prio.append(priority) or True
b.last_summary_slot, b.pulled_after_errors, b.failed_cycles = None, True, 3
b.maybe_summary()
check("problems -> 'ISSUES' in the title, listed in the status line, sent at high priority",
      sent and sent[0][0].startswith("mm_bot ISSUES:") and "quotes PULLED" in sent[0][1] and "last 3 cycle(s) failed" in sent[0][1]
      and calls_prio == ["high"], (sent, calls_prio))
b.pulled_after_errors, b.failed_cycles = False, 0
sent.clear()
a3, b3 = make_bot()
b3.cfg.summary_every_hours, b3.cfg.start_check_seconds = 1, 0
seq = iter(["draft", "active"])
a3.tournament = lambda: {"id": "T", "initialBalance": 100000, "status": next(seq), "startDate": "2099-01-01T00:00:00Z"}
b3.sleep_until = lambda t: None
b3.wait_for_trading()
check("updates are sent while waiting for the open too (so you know it's alive)",
      sent and "waiting for the open" in sent[0][1] and "order books ready" in sent[0][1], sent)
M.notify = real_notify
empty = FakeApi(); empty.leaderboard = lambda: {"myRank": None, "total": 0}; empty.smart_score = lambda: []
t2, m2 = build_summary(empty, "/nonexistent/fills.csv", 100_000, value=100_000)
check("before any trading: 'not ranked yet' / 'not scored yet' instead of an error",
      "not ranked yet" in m2 and "not scored yet" in m2 and t2 == "mm_bot: +0 (+0.0%)", (t2, m2))
check("every line fits a phone notification", all(len(line) < 160 for line in message.split("\n")), message)

print("--- request budget")
a, b = make_bot()
b.cfg.slow_poll_seconds = 1000
for _ in range(6):
    b.cycle()
check("slow_poll_seconds: P&L read once in 6 quick cycles", len(a.sent("pnl")) == 1, a.sent("pnl"))
a.calls.clear(); b.cycle()
check("polling steady state with nothing changing: only bulk-price requests for books",
      not a.sent("book") and len(a.sent("bulk")) == 1, a.calls)

print("--- realtime feed (event-driven cycles)")

a, b = make_bot()
b.feed, b.cfg.slow_poll_seconds = FakeFeed(), 1000
b.cycle()                                                   # first cycle is always a full check
check("first cycle with the feed: full check, quotes placed", len(a.ours("11")) == 2 and a.sent("bulk"))
a.calls.clear(); b.cycle()
check("next cycle: no re-read of our orders (we keep our own record of what we placed)", a.calls == [], a.calls)
a.calls.clear(); b.cycle()
check("quiet cycle with no events: zero requests", a.calls == [], a.calls)
a.books["11"]["bids"].insert(0, lvl(0.11, 300))           # a rival pennies our bid...
b.feed.push(dirty={"11"})                                   # ...and the feed reports that book changed
a.calls.clear(); b.cycle()
check("pushed book change: only that book downloaded, no bulk poll", a.sent("book") == [("book", "11")] and not a.sent("bulk"), a.calls)
check("...and we re-penny straight away", ("bid", 0.115, 100) in a.ours("11"), a.ours("11"))
a.fill("11", True, 30); b.feed.push(dirty={"11"}, account=True)
a.calls.clear(); b.cycle()
check("pushed fill: positions and fills read at once (not the whole order list)",
      {"positions", "fills"} <= {c[0] for c in a.calls} and not a.sent("orders"), a.calls)
check("...and the fill is taken off our record of the order (100 -> 70 left)",
      [o.qty for o in b.my_orders.values() if o.eid == "11" and o.is_bid] == [70], b.my_orders)
b.last_full_check = time.monotonic() - 1000
a.calls.clear(); b.cycle()
check("heartbeat due: full check with bulk prices", a.sent("bulk") != [], a.calls)
b.feed.push(settled=True); b.cycle()
check("market settled: market list reloaded next cycle", b.last_reload < 0)
b.feed.ok = False
a.calls.clear(); b.cycle()
check("feed down: falls back to polling (full check every cycle)", a.sent("bulk") != [], a.calls)

b.feed.ok = True
b.last_full_check = time.monotonic()
a.books["21"]["bids"].insert(0, lvl(0.50, 300))           # only book 21's best price really moves...
b.feed.push(dirty={"11", "12", "21", "22"})                 # ...but the feed reports 4 books changed
a.budget_left = lambda: 21                                  # and there's budget for just 1 download
a.calls.clear(); b.cycle()
check("many books reported + tight budget -> bulk check, and the one whose price moved is fetched",
      a.sent("bulk") and a.sent("book") == [("book", "21")], a.calls)
del a.budget_left
a.budget_left = lambda: 22                                  # budget nearly used up (reserve is 20)
for e in b.ex.values():
    e.book = None                                           # pretend every book needs downloading
a.calls.clear(); b.cycle()
check("tight request budget: only the spare (22 - 20 reserve = 2) books downloaded", len(a.sent("book")) == 2, a.sent("book"))
del a.budget_left

b.feed.ok, b.cfg.min_cycle_seconds, b.cfg.loop_seconds = True, 0.05, 5
threading.Timer(0.3, lambda: b.feed.push(dirty={"12"})).start()
t0 = time.monotonic(); b.wait_for_next_cycle(t0); waited = time.monotonic() - t0
check("main loop wakes as soon as an event is pushed (not after loop_seconds)", 0.25 < waited < 1.5, f"{waited:.2f}s")

print("--- RealtimeFeed message handling (no network)")
from types import SimpleNamespace
feed = RealtimeFeed(None, "T", CFG)
check("starts by asking for a full resync", feed.take()[2] is True)
feed._on_market("tournament:T", {"event": "market_batch", "payload": {
    "bookDirty": [{"exchangeId": 11, "tournamentId": "T"}, {"exchangeId": 99, "tournamentId": "OTHER"}],
    "trades": [{"exchangeId": "12", "tournamentId": "T"}], "marketSettled": [],
    "delivery": {"revision": 5, "previousRevision": 4}}})
woke = feed.wake.is_set()
dirty, acc, rs, st = feed.take()
check("book changes and trades mark books dirty (other tournaments ignored) and wake the loop",
      woke and dirty == {"11", "12"} and not acc and not rs and not st, (woke, dirty, acc, rs, st))
feed._on_market("tournament:T", {"payload": {"bookDirty": [{"exchangeId": 21, "tournamentId": "T"}],
                                             "delivery": {"revision": 5, "previousRevision": 4}}})
check("duplicate revision ignored", feed.take()[0] == set())
feed._on_market("tournament:T", {"payload": {"bookDirty": [], "delivery": {"revision": 9, "previousRevision": 7}}})
check("revision gap (missed messages) -> full resync requested", feed.take()[2] is True)
feed._on_account("user:me", {"payload": {"fills": [{"exchangeId": "21"}], "orderUpdates": [], "delivery": {"revision": 1}}})
dirty, acc, _, _ = feed.take()
check("fill on our account -> account changed + that book dirty", acc and dirty == {"21"}, (dirty, acc))
feed._on_market("tournament:T", {"payload": {"marketSettled": [{"marketId": "1"}], "delivery": {"revision": 10, "previousRevision": 9}}})
check("settled market reported", feed.take()[3] is True)
feed.connected = True
feed._on_state("tournament:T", SimpleNamespace(name="SUBSCRIBED"), None)
check("one channel joined isn't enough to be healthy", not feed.healthy())
feed._on_state("user:me", SimpleNamespace(name="SUBSCRIBED"), None)
check("both channels joined -> healthy", feed.healthy())
logging.disable(logging.CRITICAL)
feed._on_state("user:me", SimpleNamespace(name="CHANNEL_ERROR"), "boom")
logging.disable(logging.NOTSET)
check("a channel error -> unhealthy (bot polls until it reconnects)", not feed.healthy())


print("--- dead realtime socket detection (seen live: the library kept saying 'connected')")
class FakeWS:
    close_code = None
class FakeTask:
    def __init__(self, done): self._done = done
    def done(self): return self._done
class FakeClient:
    def __init__(self, connected=True, close_code=None, listener_done=False):
        self._ws_connection = FakeWS() if connected else None
        if connected:
            self._ws_connection.close_code = close_code
        self._listen_task = FakeTask(listener_done)
    @property
    def is_connected(self): return self._ws_connection is not None
feed = RealtimeFeed(None, "T", CFG)
check("healthy socket: not dead", not feed._socket_dead(FakeClient()))
check("server closed the socket (close code 1006) while is_connected still says True: dead",
      feed._socket_dead(FakeClient(close_code=1006)))
check("the library's listener stopped: dead", feed._socket_dead(FakeClient(listener_done=True)))
handler = M._SocketErrorWatch(feed)
logging.getLogger("realtime").addHandler(handler)
logging.disable(logging.NOTSET); logging.getLogger("realtime._async.client").propagate = True
real_level = logging.getLogger("realtime").level
logging.getLogger("realtime").setLevel(logging.ERROR)
logging.getLogger("realtime._async.client").error("WebSocket connection closed with code: 1006, reason: ")
logging.getLogger("realtime").removeHandler(handler); logging.getLogger("realtime").setLevel(real_level)
check("the library logging 'connection closed' flags the feed for reconnection", feed.socket_error)
check("...and a flagged feed counts as dead even if everything else looks fine", feed._socket_dead(FakeClient()))
check("sessions renew at least hourly (fresh login) even when they look healthy", Config().realtime_session_max_seconds == 3600)

print("--- realtime reconnect back-off (day one: 2, 4, 8, 16 s over two hours of unrelated drops)")
import asyncio
def backoffs(session_lengths):
    """Run RealtimeFeed._run with sessions that drop after the given (simulated) lengths; return its waits."""
    feed = RealtimeFeed(None, "T", Config())
    waits, clock, runs = [], [1000.0], iter(session_lengths)
    async def fake_session():
        n = next(runs, None)
        if n is None:
            feed.stopping = True
            return
        if n > 0:
            feed.session_connected_at = clock[0]
        clock[0] += n
        raise ConnectionError("socket closed")
    async def fake_sleep(t):
        waits.append(t); clock[0] += t
    feed._session = fake_session
    real_sleep, real_mono = asyncio.sleep, M.time.monotonic
    asyncio.sleep, M.time.monotonic = fake_sleep, lambda: clock[0]
    try:
        logging.disable(logging.CRITICAL); asyncio.run(feed._run())
    finally:
        asyncio.sleep, M.time.monotonic = real_sleep, real_mono; logging.disable(logging.NOTSET)
    return waits
w = backoffs([3000, 2400, 1800, 600])
check("a drop after a healthy session reconnects after 1 s every time (not 2, 4, 8, 16 s)", w[:4] == [1, 1, 1, 1], w)
w = backoffs([0, 0, 0, 0, 5])
check("drops in quick succession (never connected) still back off: 1, 2, 4, 8 s", w[:4] == [1, 2, 4, 8], w)

print("--- startup self-test (live)")
a, b = make_bot()
ok = b.self_test()
check("self-test passes against an exchange that behaves as the spec says, and leaves nothing behind",
      ok and not a.orders and a.sent("batch") == [("batch", 2)], (a.orders, a.calls))
a, b = make_bot()
a.open_orders = lambda tid, eid=None: []                   # the orders "vanish": not what we expect
b.cfg.recent_order_grace_seconds = 0.05
logging.disable(logging.CRITICAL)
first = b.self_test()
check("self-test: accepted orders not listed once = maybe list lag on a busy exchange -> retry, no exit", first is False)
try:
    logging.disable(logging.CRITICAL); b.self_test(); code = None
except SystemExit as e:
    code = e.code
finally:
    logging.disable(logging.NOTSET)
check("self-test stops the bot (exit code 3) when the exchange doesn't behave as assumed (twice in a row)", code == EXIT_FATAL, code)
a, b = make_bot(live=False)
check("self-test is skipped in dry runs (no real orders)", b.self_test() and not a.sent("batch"))

print("--- self-test: a busy exchange is not a failure (day one: writes > 15 s, 409 REQUEST_IN_FLIGHT)")
for err, name in ((ApiError(409, "REQUEST_IN_FLIGHT", "in flight"), "409 in flight"),
                  (ApiError(0, "NETWORK", "read timed out"), "network timeout")):
    a, b = make_bot()
    a.batch_error = err
    try:
        logging.disable(logging.CRITICAL); ok = b.self_test(); code = None
    except SystemExit as e:
        ok, code = None, e.code
    finally:
        logging.disable(logging.NOTSET)
    tex = b.ex[b.selftest_eid or min(b.ex)]
    check(f"self-test {name}: no exit, returns 'not yet' and retries ~60 s later",
          code is None and ok is False and 55 < b.selftest_next - time.monotonic() <= 60, (ok, code))
    check(f"self-test {name}: the test exchange isn't left blocked, and the order list is re-read",
          all(x.pending_until < time.monotonic() for x in b.ex.values()) and b.orders_stale)
    a.batch_error = None
    check(f"self-test {name}: once the exchange answers, the retry passes", b.self_test() and b.selftest_passed)
a, b = make_bot()
real_cancel = a.cancel_all
def cancel_times_out(tid, eid=None):
    real_cancel(tid, eid)                                  # it DID cancel; we just never heard back
    raise ApiError(0, "NETWORK", "read timed out")
a.cancel_all = cancel_times_out
try:
    logging.disable(logging.CRITICAL); ok = b.self_test(); code = None
except SystemExit as e:
    ok, code = None, e.code
finally:
    logging.disable(logging.NOTSET)
check("self-test: a clean-up cancel that times out is 'busy', not a failure", code is None and ok is False, (ok, code))

a, b = make_bot()
gate, entered = threading.Event(), threading.Event()
real_pb = a.place_batch
def slow_test_batch(orders):
    if all(o["quantity"] == 1 for o in orders):           # the self-test's two 1-share orders: hang like day one
        entered.set(); gate.wait(5)
    return real_pb(orders)
a.place_batch = slow_test_batch
alerts = []
real_alert, M.alert = M.alert, lambda m: alerts.append(m)
cycles = {"n": 0}
real_cycle = b.cycle
def counting():
    cycles["n"] += 1
    real_cycle()
    if cycles["n"] == 3:
        check("while the self-test's order write hangs, cycles keep running (it's on its own thread)",
              entered.is_set() and not b.selftest_passed, cycles)
        gate.set()
    if cycles["n"] >= 3 and b.selftest_passed:
        b.running = False
    if cycles["n"] > 200:
        b.running = False
b.cycle, b.cfg.loop_seconds, b.cfg.min_cycle_seconds = counting, 0.01, 0.01
b.run()
M.alert = real_alert
check("...and its result is picked up once it's in: passed, test orders gone", b.selftest_passed and
      not [o for o in a.orders.values() if o["quantity"] == 1], cycles)

a, b = make_bot()
a.batch_error = ApiError(409, "REQUEST_IN_FLIGHT", "in flight")
b.cfg.selftest_alert_after, b.cfg.selftest_retry_seconds = 0, 0
alerts = []
M.alert = lambda m: alerts.append(m)
logging.disable(logging.CRITICAL)
b.self_test(); b.self_test()
logging.disable(logging.NOTSET)
M.alert = real_alert
check("self-test still busy after selftest_alert_after: exactly one alert, still no exit",
      len([m for m in alerts if "self-test" in m]) == 1, alerts)
a, b = make_bot()
b.cycle()
def wrong_side(tid, eid=None):                            # listed, but the ask reads back wrongly
    return [dict(o, priceLimit=0.5) if o["side"] == "no" else o for o in FakeApi.open_orders(a, tid, eid)]
real_pb2 = a.place_batch
def batch_then_bot_cancels(orders):
    res = real_pb2(orders)
    if all(o["quantity"] == 1 for o in orders):
        b.cancel_everything()                              # e.g. error recovery pulling every quote mid-test
    return res
a.place_batch, a.open_orders = batch_then_bot_cancels, wrong_side
try:
    logging.disable(logging.CRITICAL); ok = b.self_test(); code = None
except SystemExit as e:
    ok, code = None, e.code
finally:
    logging.disable(logging.NOTSET)
check("self-test: a failure while the bot itself cancelled everything mid-test proves nothing -> retry",
      code is None and ok is False, (ok, code))
a, b = make_bot()
b.cycle()
tx = min([e for e in sorted(b.ex)], key=lambda e: b.size_plan.get(e, 0))
b.ex[tx].pending_until = time.monotonic() + 50                # an unclear placement there earlier
b.self_test()
check("self-test keeps an earlier 'outcome unknown' hold on its exchange (no double placement)",
      b.ex[tx].pending_until > time.monotonic() + 40, b.ex[tx].pending_until - time.monotonic())
check("self-test takes the bot's own quotes off its exchange before testing (they can't be repriced meanwhile)",
      ("cancel_all", tx) in a.calls, a.calls[-6:])

print("--- election night")
q = exit_quote(0.14, 300, 0.12, 0.18, CFG, 100_000)
check("exit, long 300: sell at the best bid (0.12, within 3c of fair 0.14) - an immediate trade", (q.ask, q.ask_size, q.bid) == (0.12, 300, None), q)
q = exit_quote(0.14, 300, 0.05, 0.18, CFG, 100_000)
check("exit, bid far below fair: rest at fair - 3c (0.11) instead of dumping", (q.ask, q.ask_size) == (0.11, 300), q)
q = exit_quote(0.14, -200, 0.10, 0.15, CFG, 100_000)
check("exit, short 200: buy at the best ask (0.15)", (q.bid, q.bid_size, q.ask) == (0.15, 200, None), q)
check("exit, already flat: nothing (no new positions)", exit_quote(0.14, 0, 0.10, 0.18) == NO_QUOTE)

a, b = make_bot(books={"11": {"bids": [lvl(0.12, 1000)], "asks": [lvl(0.18, 1000)]},
                       "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.88, 1000)]},
                       "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
                       "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}})
a.inv = {"11": 300, "12": 300}                             # hedged pair: race-netted inventory is 0
for e in ("11", "12"):
    b.ex[e].close = utcnow() + timedelta(hours=4)          # inside the 6 h per-market window
b.cycle()
check("6 h before close: a hedged pair is flattened market by market (asks on both, no bids)",
      [s for s, _, _ in a.ours("11")] == ["ask"] and [s for s, _, _ in a.ours("12")] == ["ask"], (a.ours("11"), a.ours("12")))
for e in ("11", "12"):
    b.ex[e].close = utcnow() + timedelta(hours=1)          # inside the 2 h exit window
a.cancel_all("T"); b.cycle()
check("1 h before close: exits by trading against the best bids", a.inv.get("11") == 0 and a.inv.get("12") == 0, a.inv)

print("--- arbitrage threshold (fixed 3c)")
small_arb = {"11": {"bids": [lvl(0.57, 300)], "asks": [lvl(0.70, 300)]}, "12": {"bids": [lvl(0.45, 200)], "asks": [lvl(0.55, 200)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
a, b = make_bot(books=small_arb)
b.cycle()
check("bids summing to 1.02 are below the 3c threshold -> no arbitrage", not a.inv, a.inv)

print("--- taking stale house quotes")
def take_setup(**kw):
    a, b = make_bot(**kw)
    b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70})   # book says 0.14 / 0.86
    return a, b
def later(b, seconds=31):
    """Pretend the gaps first seen so far were seen `seconds` ago (take_confirm_seconds is 30)."""
    for e in b.ex.values():
        e.take_since -= seconds
a, b = take_setup()
b.cycle()
check("first reading showing the gap: nothing taken yet (it must hold for 30 s)", not a.inv, a.inv)
later(b, 10); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("...still nothing 10 s later", not a.inv, a.inv)
later(b, 21); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("gap on every reading for 30 s: buys the stale Rep ask (0.18) and sells to the stale Dem bid (0.82)",
      a.inv.get("11") == 1000 and a.inv.get("12") == -1000, a.inv)
check("takes counted, and logged as our fills", b.takes_total == 2 and b.cycle() is None)
a, b = take_setup()
b.cycle(); later(b)
b.refs.new_reading({"Ohio Senate|Republican": 0.14, "Ohio Senate|Democratic": 0.86}, {}); b.cycle()
check("a spike that reverts on the next reading is never traded", not a.inv, a.inv)
a, b = make_bot()
b.refs = FakeRefs({"Ohio Senate|Republican": 0.30, "Ohio Senate|Democratic": 0.70}, spread=None)
b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("thin / last-trade-only Polymarket price: no taking", not a.inv, a.inv)
a, b = take_setup()
for e in b.ex.values():
    e.close = utcnow() + timedelta(hours=10)               # inside the 12 h pre-close window
b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("no taking in the pre-close window (no new positions)", not a.inv, a.inv)

print("--- lagging open-orders list")
a, b = make_bot()
b.cycle()
placed, n_orders = len(a.sent("batch")), len(a.orders)
real_open = a.open_orders
a.open_orders = lambda tid, eid=None: []                   # the list hasn't caught up with the new orders yet
b.orders_stale = True; b.cycle()
check("just-placed quotes still count as resting -> nothing placed twice",
      len(a.sent("batch")) == placed and len(a.orders) == n_orders, (len(a.sent("batch")), placed, len(a.orders)))
a.open_orders = real_open
b.orders_stale = True; b.cycle()
check("...once the list shows them they're forgotten (the list is trusted again)", not b.recent_orders, len(b.recent_orders))
a2, b2 = make_bot()
b2.cfg.recent_order_grace_seconds = -1                     # the old behaviour, for comparison
b2.cycle(); a2.open_orders = lambda tid, eid=None: []; b2.orders_stale = True; b2.cycle()
check("...(without the grace period the same lag would have doubled the quotes)", len(a2.orders) > n_orders, len(a2.orders))
a, b = make_bot()
b.cycle()
bid = next(o for o in a.orders.values() if o["exchangeId"] == "11" and a.yes_view(o)[0])
a.open_orders = lambda tid, eid=None: []
a.fill("11", True, bid["quantity"])                        # fully filled before the list ever showed it
b.log_fills({})
check("a just-placed order that fully fills is no longer counted as resting", bid["id"] not in b.recent_orders)

print("--- election night without a fair value")
a, b = make_bot()
a.inv = {"11": 300}
b.cycle()                                                  # learns fair value 0.14
a.books["11"] = {"bids": [lvl(0.12, 1000)], "asks": []}    # asks vanish: too thin for a fair value
for e in b.ex.values():
    e.close = utcnow() + timedelta(hours=1)                # inside the 2 h exit window
a.cancel_all("T"); b.recent_orders.clear(); b.orders_stale = True
b.cycle()
check("exit window, no fair value any more: still exits around the last known one (sold 300 at 0.12)",
      a.inv.get("11") == 0, a.inv)
a, b = make_bot()
a.inv = {"11": 300}
a.books["11"] = {"bids": [lvl(0.12, 1000)], "asks": []}
b.refs = FakeRefs({"Ohio Senate|Republican": 0.14})
for e in b.ex.values():
    e.close = utcnow() + timedelta(hours=1)
b.cycle()
check("...and with no fair value ever seen, exits around Polymarket's price", a.inv.get("11") == 0, a.inv)

print("--- implausible Polymarket price (wrong match)")
alerts, real_alert = [], M.alert
M.alert = alerts.append
a, b = make_bot()
b.refs = FakeRefs({"Ohio Senate|Republican": 0.60})        # 46c from the book's 0.14: surely a wrong match
b.cycle(); b.cycle()
check("price > 25c from the book is ignored: both sides still quoted", len(a.ours("11")) == 2, a.ours("11"))
check("...fair value stays the book's", abs(b.ex["11"].last_fv - 0.14) < 1e-9, b.ex["11"].last_fv)
check("...one alert, not one per cycle", len([m for m in alerts if "Ohio" in m]) == 1, alerts)
b.refs.prices = {"Ohio Senate|Republican": 0.30}           # back in a plausible range (16c): used again
b.cycle()
check("...back within 25c: used again (guard -> bid only)", [s for s, _, _ in a.ours("11")] == ["bid"], a.ours("11"))
M.alert = real_alert

print("--- takes respect the tail guard")
tail_race = {"11": {"bids": [lvl(0.90, 1000)], "asks": [lvl(0.93, 1000)]}, "12": {"bids": [lvl(0.07, 1000)], "asks": [lvl(0.10, 1000)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
def tail_take(tail_high, tail_low):
    a, b = make_bot(books=json.loads(json.dumps(tail_race)))
    b.cfg.tail_high, b.cfg.tail_low = tail_high, tail_low
    b.refs = FakeRefs({"Ohio Senate|Republican": 0.99, "Ohio Senate|Democratic": 0.01})
    b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
    return a
a = tail_take(1.0, 0.0)
check("(control, tail guard off: the 6c-stale quotes near 0/1 would be taken)", a.inv.get("11", 0) > 0, a.inv)
a = tail_take(0.95, 0.05)
check("tail guard on: no buying YES at 0.93 near 1, no selling YES at 0.07 near 0",
      not a.inv.get("11") and not a.inv.get("12"), a.inv)

print("--- request load: our own order changes cost no re-reads")
feed = RealtimeFeed(None, "T", CFG); feed.take()
feed._on_account("user:me", {"payload": {"orderUpdates": [{"orderId": 5}], "delivery": {"revision": 1}}})
_, acc, _, _ = feed.take()
check("feed: an order update alone (our own placement/cancel echoed back) isn't an account change",
      not acc and not feed.wake.is_set(), (acc, feed.wake.is_set()))
a, b = make_bot()
b.feed, b.cfg.slow_poll_seconds = FakeFeed(), 1000
b.cycle()                                                   # full check, quotes placed
exp = min(parse_ts(o["expirationDate"]) for o in a.orders.values())
check("orders now live 30 min (fewer replacements)", 1790 < (exp - utcnow()).total_seconds() <= 1800, exp)
a.calls.clear()
for k in range(6):                                          # six rivals in a row penny our Ohio bid...
    a.books["11"]["bids"].insert(0, lvl(round(0.11 + 0.005 * k, 3), 300))
    b.feed.push(dirty={"11"}); b.cycle()                    # ...and we re-quote each time
check("6 re-quotes: the open-orders list is never re-read (our own record is kept up to date)",
      not a.sent("orders") and not a.sent("positions") and len(a.sent("batch")) >= 3, a.calls)
check("...and our record matches the exchange exactly",
      sorted((o.eid, o.is_bid, o.price, o.qty) for o in b.my_orders.values())
      == sorted((o["exchangeId"],) + (lambda v: (v[0], v[1]))(a.yes_view(o)) + (o["quantity"],) for o in a.orders.values()),
      (b.my_orders, a.orders))
old = dict(next(o for o in a.orders.values() if o["exchangeId"] == "21"))
b.cancel("21", [], whole_exchange=True)
real_open = a.open_orders
a.open_orders = lambda tid, eid=None: real_open(tid, eid) + [old]   # the list lags: still shows the cancelled order
b.orders_stale = True; b.cycle()
a.open_orders = real_open
check("a just-cancelled order that the lagging list still shows is ignored",
      old["id"] not in b.my_orders, [o.order_id for o in b.my_orders.values()])

print("--- sizes follow the account in 5% steps")
a, b = make_bot()
b.reserved_mode = "ignore"                                  # locked-cash question settled
b.cycle(); b.cycle()
a.calls.clear()
a.equity = 99_990; b.cycle()
check("account 100,000 -> 99,990: no orders replaced (was: every order)",
      not a.sent("cancel_all") and not a.sent("cancel_order") and not a.sent("batch"), a.calls)
a.equity = 106_000; b.cycle()
check("account +6%: sizing steps up to 106,000, but resting 100-share orders are kept (no churn)",
      b.bankroll() == 106_000 and {o["quantity"] for o in a.orders.values()} == {100}, (b.bankroll(), a.orders))
b.cancel_everything(); b.cycle()
check("...new orders are 106 shares", {o["quantity"] for o in a.orders.values()} == {106}, {o["quantity"] for o in a.orders.values()})
a, b = make_bot(); b.reserved_mode = None                    # "auto"
a.equity = 120_000; b.cycle()
check("locked-cash question not settled yet: sizes stay on the starting balance", b.bankroll() == 100_000, b.bankroll())

print("--- a tick of movement doesn't cost our place in line")
q = compute_quote(0.14, 0, 0, 0.10, 0.18)
check("quote carries how far a resting order may drift (bid <= 0.13, ask >= 0.15)",
      (q.bid, q.ask, q.bid_limit, q.ask_limit) == (0.105, 0.175, 0.13, 0.15), q)
now = utcnow()
o = Resting(1, "7", True, 0.105, 100, now + timedelta(minutes=20))
check("resting bid 1 tick off target (0.11 vs 0.105), still safe: kept",
      not side_needs_change([o], 0.11, 100, CFG, now, 0.13, is_bid=True))
check("2 ticks off: replaced", side_needs_change([o], 0.115, 100, CFG, now, 0.13, is_bid=True))
check("1 tick off but closer than min_edge to fair value: replaced",
      side_needs_change([o], 0.10, 100, CFG, now, 0.10, is_bid=True))
check("no limit given (election-night exits): exact prices only", side_needs_change([o], 0.11, 100, CFG, now))
a, b = make_bot()
b.cycle()
a.books["11"]["bids"][0]["price"] = 0.105                   # the house moves up a tick, level with our bid
a.calls.clear(); b.cycle()
check("house moves a tick: our bid stays (first in line at 0.105) - no cancel, no new order",
      not a.sent("cancel_all") and not a.sent("cancel_order") and not a.sent("batch") and ("bid", 0.105, 100) in a.ours("11"),
      (a.calls, a.ours("11")))

print("--- national-swing shading")
a, b = make_bot()
b.cycle()
rep_ex, dem_ex = b.ex["11"], b.ex["12"]
flat_r = b.decide(rep_ex, 0.14, {}, {}, False, 0, time.monotonic())
flat_d = b.decide(dem_ex, 0.86, {}, {}, False, 0, time.monotonic())
long_r = b.decide(rep_ex, 0.14, {}, {}, False, 1500, time.monotonic())       # net long 1,500 Rep (cap 2,000)
long_d = b.decide(dem_ex, 0.86, {}, {}, False, 1500, time.monotonic())
check("net long Republican: Rep ask shaded down (0.175 -> 0.17) so we sell Rep sooner, bid unchanged",
      (flat_r.ask, long_r.ask, long_r.bid) == (0.175, 0.17, flat_r.bid), (flat_r, long_r))
check("...and Dem bid shaded up (0.825 -> 0.83) so we buy Dem sooner; both sides still quoted",
      (flat_d.bid, long_d.bid) == (0.825, 0.83) and long_d.ask is not None and long_r.bid is not None, (flat_d, long_d))
check("shading grows to 1.5c at the cap and no further",
      abs(b.party_shift(rep_ex, 2000) - 0.015) < 1e-12 and abs(b.party_shift(rep_ex, 9999) - 0.015) < 1e-12
      and abs(b.party_shift(dem_ex, 2000) + 0.015) < 1e-12)
beyond = b.decide(rep_ex, 0.14, {}, {}, False, 2500, time.monotonic())
check("beyond the cap the hard block still applies (no Rep bid)", beyond.bid is None and beyond.ask is not None, beyond)

print("--- the open")
a, b = make_bot()
waits, answers = [], iter([("draft", 45), ("draft", -0.5), ("draft", -1.5), ("active", -2.5)])
def fake_tournament():
    status, to_start = next(answers)
    return {"id": "T", "status": status, "startDate": iso(utcnow() + timedelta(seconds=to_start))}
a.tournament = fake_tournament
b.sleep_until = lambda t: waits.append(round(t - time.monotonic()))
a.calls.clear(); b.wait_for_trading()
check("45 s before the start: no requests, sleeps until the start time", waits[0] == 45 and not a.sent("book") and not a.sent("bulk"),
      (waits, a.calls))
check("from the start time: checks every second until trading opens", waits[1:] == [1, 1], waits)
a, b = make_bot()
b.refs, started = FakeRefs({}), []
b.wait_for_trading = lambda: started.append(getattr(b.refs, "started", False))
run_cycles(b, 1)
check("Polymarket prices are started before waiting for the open", started == [True], started)
batches = [c for c in a.calls if c[0] == "batch"]
check("self-test runs straight after the first quotes (not before them)", batches[0][1] > 2 and ("batch", 2) in batches[1:], batches)
refs_cfg_dir = tempfile.mkdtemp()
json.dump({"Ohio Senate|Republican": {"source": "polymarket", "id": "1"}}, open(os.path.join(refs_cfg_dir, "m.json"), "w"))
b.cfg.ref_map_file = os.path.join(refs_cfg_dir, "m.json")
check("Polymarket refresh interval comes from SETTINGS (5 s)", b.load_reference_prices().cfg.refresh_seconds == 5.0)

print("--- Polymarket readings wake the main loop")
a, b = make_bot()
b.feed = FakeFeed()
b.on_reference_prices({"Ohio Senate|Republican": 0.005})
check("feed healthy: any Polymarket price change wakes the main loop", b.wake.is_set())
b.wake.clear(); b.feed.ok = False
b.on_reference_prices({"Ohio Senate|Republican": 0.005})
check("feed down (every cycle is a full check, ~7 requests): a small change doesn't wake it...", not b.wake.is_set())
b.on_reference_prices({"Ohio Senate|Republican": 0.04})
check("...but a Polymarket jump does", b.wake.is_set())
b.wake.clear(); b.feed.ok = True; b.cfg.loop_seconds = 5
threading.Timer(0.2, lambda: b.on_reference_prices({"Ohio Senate|Republican": 0.01})).start()
t0 = time.monotonic(); b.wait_for_next_cycle(t0); waited = time.monotonic() - t0
check("a new reading starts the next cycle after the 0.5 s minimum, not after loop_seconds (5 s)",
      0.45 <= waited < 1.0 and not b.wake.is_set(), f"{waited:.2f}s")
check("cycles may run every 0.5 s", Config().min_cycle_seconds == 0.5)

print("--- clock check")
alerts, real_alert = [], M.alert
M.alert = alerts.append
a, b = make_bot()
a.last_date = (M.email.utils.format_datetime(utcnow() - timedelta(seconds=10), usegmt=True), utcnow())
skew = b.check_clock()
check("our clock 10 s ahead of the exchange's -> alert", 8.5 <= skew <= 10.5 and len(alerts) == 1, (skew, alerts))
alerts.clear()
a.last_date = (M.email.utils.format_datetime(utcnow(), usegmt=True), utcnow())
check("clocks in sync -> no alert", abs(b.check_clock()) <= 1 and not alerts, alerts)

print("--- self-test uses the real order expiry, with a fallback")
a, b = make_bot()
sent_exp, real_pb = [], a.place_batch
def capped(orders):
    sent_exp.extend(parse_ts(o["expirationDate"]) for o in orders)
    if any(parse_ts(o["expirationDate"]) > utcnow() + timedelta(minutes=15) for o in orders):
        a.log("batch", len(orders))
        return [{"index": k, "ok": False, "status": 400,
                 "data": {"error": {"code": "VALIDATION_ERROR", "message": "expirationDate too far ahead"}}}
                for k in range(len(orders))]
    return real_pb(orders)
a.place_batch = capped
logging.disable(logging.CRITICAL)
ok = b.self_test()
logging.disable(logging.NOTSET)
check("self-test orders carry the real 30-min expiry", 1790 < (sent_exp[0] - utcnow()).total_seconds() <= 1800, sent_exp[:1])
check("if the exchange rejected 30-min orders: falls back to 10-min ones instead of stopping",
      ok and b.cfg.order_ttl == 600 and b.cfg.refresh_before_expiry == 120 and not a.orders, (ok, b.cfg.order_ttl))
check("...and sends an alert saying so", any("10-min" in m for m in alerts), alerts)
a, b = make_bot()
a.place_batch = lambda orders: [{"index": k, "ok": False, "status": 400, "data": {"error": {"code": "X"}}} for k in range(len(orders))]
try:
    logging.disable(logging.CRITICAL); b.self_test(); code = None
except SystemExit as e:
    code = e.code
finally:
    logging.disable(logging.NOTSET)
check("orders rejected at any expiry: still stops (exit code 3) for a human to look", code == EXIT_FATAL, code)
M.alert = real_alert

print("--- robustness")
check("timestamps with 7 decimal places parse (Python 3.10 can't read them as they come)",
      parse_ts("2026-09-26T13:56:47.7844565+00:00") == parse_ts("2026-09-26T13:56:47.784456Z"))
a, b = make_bot()
b.cfg.summary_every_hours, b.last_summary_slot = 1, None
real_bs, M.build_summary = M.build_summary, lambda *x, **k: 1 / 0
logging.disable(logging.CRITICAL)
try:
    b.maybe_summary(); ok = True
except Exception:
    ok = False
finally:
    logging.disable(logging.NOTSET); M.build_summary = real_bs
check("a failing phone summary is skipped, never an error that would pull the quotes", ok)

print("--- live size defaults")
q = compute_quote(0.14, 0, 0, 0.10, 0.18, _live, bankroll=100_000)
check("live defaults: 500-share quotes at 100k (sized for big trades)", (q.bid_size, q.ask_size) == (500, 500), q)
check("live defaults: 3,000-share position limit (no reliable Polymarket price), 15,000-share national-swing cap",
      (_live.max_position_frac * 100_000, _live.max_party_delta_frac * 100_000) == (3000, 15000))

print("--- quote sizes by market activity")
act = {"H1": 0.3, "H2": 0.3, "big": 0.2, "mid": 0.05, "small": 0.001, "dead": 0.0}
plan = plan_sizes(act, {"H1", "H2"}, 100_000, _live)
check("party-control markets get 10,000-share quotes", plan["H1"] == plan["H2"] == 10_000, plan)
check("few markets, capital to spare: active ones get the 2,000 maximum, a market with no activity the 100 minimum",
      plan["big"] == plan["mid"] == plan["small"] == 2000 and plan["dead"] == 100, plan)
check("sizes are whole multiples of 50 shares", all(v % 50 == 0 for v in plan.values()), plan)
many = {f"m{i}": 1.0 / (i + 1) for i in range(170)}
many.update(dict.fromkeys(("H1", "H2", "H3", "H4"), 1.0))
p2 = plan_sizes(many, {"H1", "H2", "H3", "H4"}, 100_000, _live)
check("~174 markets: all quotes together stay within 60% of the account", sum(p2.values()) <= 60_000, sum(p2.values()))
check("...busier markets get bigger quotes; the quiet ones still get the 100-share minimum",
      p2["m0"] > p2["m5"] > p2["m60"] and p2["m0"] > 100 and min(p2[f"m{i}"] for i in range(170)) == 100,
      (p2["m0"], p2["m5"], p2["m60"], min(p2.values())))
p3 = plan_sizes(dict(many, m5=many["m5"] * 1.2), {"H1", "H2", "H3", "H4"}, 100_000, _live, prev=p2)
check("a small shift in activity changes no sizes (so no orders are replaced)", p3 == p2,
      {e: (p2[e], p3[e]) for e in p2 if p2[e] != p3[e]})
tight = plan_sizes(dict.fromkeys([f"q{i}" for i in range(250)], 0.0) | dict.fromkeys(("H1", "H2", "H3", "H4"), 1.0),
                   {"H1", "H2", "H3", "H4"}, 100_000, _live)
check("if the 10,000s would starve everything else, they're scaled down to fit the cap",
      tight["H1"] < 10_000 and sum(tight.values()) <= 60_000 and tight["q0"] == 100, (tight["H1"], sum(tight.values())))
cheap = plan_sizes({"a": 1.0, "b": 1.0}, set(), 100_000, _live, lock={"a": 0.98, "b": 0.03})
check("capital is counted at what each quote really locks (a near-certain market's one cheap side costs little)",
      quote_lock(0.5, _live) == 0.98 and quote_lock(0.02, _live) == 0.02 and abs(quote_lock(0.98, _live) - 0.02) < 1e-9
      and cheap == {"a": 2000, "b": 2000}, cheap)
grown = plan_sizes(many, {"H1", "H2", "H3", "H4"}, 150_000, _live)
shrunk = plan_sizes(many, {"H1", "H2", "H3", "H4"}, 80_000, _live)
check("everything scales with the account, party-control quotes included (150k -> 15,000, 80k -> 8,000)",
      grown["H1"] == 15_000 and shrunk["H1"] == 8_000 and grown["m0"] > p2["m0"] > shrunk["m0"],
      (grown["H1"], shrunk["H1"], grown["m0"], p2["m0"], shrunk["m0"]))
up5 = plan_sizes(many, {"H1", "H2", "H3", "H4"}, 105_000, _live, prev=p2, prev_bankroll=100_000)
check("a 5% account step moves every size with it (the 25% rule only holds back activity shifts)",
      up5["H1"] == 10_500 and all(abs(up5[e] - p2[e] * 1.05) <= 50 for e in p2), {e: (p2[e], up5[e]) for e in list(p2)[:3]})
q = exit_quote(0.5, 8000, 0.49, 0.52, _live, 100_000)
q2 = exit_quote(0.5, 8000, 0.49, 0.52, _live, 100_000, max_size=10_000)
check("election-night exit: a big position leaves in planned-size pieces, not 1,000-SUSQie ones",
      q.ask_size == 1960 and q2.ask_size == 8000, (q.ask_size, q2.ask_size))
check("no activity data at all: markets share equally",
      len(set(plan_sizes({"a": 0, "b": 0, "c": 0}, set(), 100_000, _live).values())) == 1)

class VolRefs:
    """Polymarket volumes only (no prices), to test sizing on its own."""
    version, last_moves, mapping = 1, {}, {}
    def get(self): return {}
    def spreads(self): return {}
    def volumes(self): return {"U.S. House|Republican": 9e6, "U.S. House|Democratic": 3e6}
books6 = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]}, "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
          "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]},
          "91": {"bids": [lvl(0.44, 20000)], "asks": [lvl(0.52, 20000)]}, "92": {"bids": [lvl(0.48, 20000)], "asks": [lvl(0.56, 20000)]}}
a, b = make_bot(books=books6, extra_markets=[market("9", "91", "Republican", "U.S. House"), market("10", "92", "Democratic", "U.S. House")])
b.cfg.size_by_activity, b.refs = True, VolRefs()
b.cycle()
check("live: the U.S. House markets quote 10,000 shares a side",
      [n for _, _, n in a.ours("91")] == [10000, 10000] and [n for _, _, n in a.ours("92")] == [10000, 10000], (a.ours("91"), a.ours("92")))
check("...ordinary markets quote at most 2,000", all(n <= 2000 for e in ("11", "12", "21", "22") for _, _, n in a.ours(e)),
      [a.ours(e) for e in ("11", "21")])
b.feed = FakeFeed()
b.feed.trade_counts = {"21": 5000}                          # the tournament trades Utah Republican far more
b.size_plan_time = -1e9
b.update_size_plan(time.monotonic(), {e: 0.5 for e in b.ex})
check("tournament trades shift the plan: the market people actually trade gets the big quote",
      b.size_plan["21"] == 2000 and b.size_plan["11"] == 100, b.size_plan)

a, b = make_bot(books=books6, extra_markets=[market("9", "91", "Republican", "U.S. House"), market("10", "92", "Democratic", "U.S. House")])
b.cfg.size_by_activity, b.refs = True, VolRefs()
b.cycle()
b.self_test()
check("self-test runs on the quietest ordinary market, never a party-control one (their quotes stay put)",
      [n for _, _, n in a.ours("91")] == [10000, 10000] and [n for _, _, n in a.ours("92")] == [10000, 10000],
      (a.ours("91"), a.ours("92")))
check("status.json shows the size plan", b.health.get("biggest_quotes", {}).get("Rep U.S. House") == 10000, b.health.get("biggest_quotes"))

a, b = make_bot()
b.phase, b.refs = "trading", FakeRefs({"Ohio Senate|Republican": 0.14})
b.refs.mapping = {f"k{i}": {} for i in range(10)}         # 10 mapped, only 1 priced
b.cycle()
line, probs = b.status_report()
check("status flags missing Polymarket prices (a stalled price thread can't go unnoticed)",
      any("Polymarket prices missing" in p for p in probs), line)

print("--- parallel, time-boxed order writes (day one: writes 15-30 s, sent one at a time)")
def lock_api(a):
    """FakeApi isn't thread-safe; the real exchange is. Serialise its writes."""
    lk = threading.Lock()
    for name in ("place_batch", "cancel_all", "cancel_order"):
        f = getattr(a, name)
        setattr(a, name, (lambda f: lambda *x, **k: (lk.acquire(), f(*x, **k), lk.release())[1])(f))
    return a
house = [market("9", "91", "Republican", "U.S. House"), market("10", "92", "Democratic", "U.S. House")]
hbooks = {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]}, "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
          "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]},
          "91": {"bids": [lvl(0.06, 1000)], "asks": [lvl(0.12, 1000)]}, "92": {"bids": [lvl(0.88, 1000)], "asks": [lvl(0.94, 1000)]}}
a, b = make_bot(books={k: {"bids": [dict(l) for l in v["bids"]], "asks": [dict(l) for l in v["asks"]]} for k, v in hbooks.items()},
                extra_markets=house)
lock_api(a)
sent = []
real_pb = a.place_batch
a.place_batch = lambda orders: (sent.append([o["exchangeId"] for o in orders]), real_pb(orders))[1]
b.cfg.batch_size = 4
b.cycle()
check("party-control (headline) orders go out first, in a batch of their own", sent and set(sent[0]) == {"91", "92"}, sent)
check("every market quoted after one cycle, no duplicates",
      all(len(a.ours(e)) == 2 for e in ("11", "12", "21", "22", "91", "92")), {e: a.ours(e) for e in a.books})

# A cancel that hangs: the cycle doesn't wait past write_wait_seconds, and nothing is stacked on that exchange.
gate = threading.Event()
real_co, real_ca = a.cancel_order, a.cancel_all
slow_eids = {"21"}
def slow_cancel_order(oid):
    if a.orders.get(oid, {}).get("exchangeId") in slow_eids:
        gate.wait(10)
    return real_co(oid)
def slow_cancel_all(tid, eid=None):
    if eid in slow_eids:
        gate.wait(10)
    return real_ca(tid, eid)
a.cancel_order, a.cancel_all = slow_cancel_order, slow_cancel_all
b.cfg.write_wait_seconds = 0.3
for e in ("21", "22"):                                    # Utah moves 4c: both sides there need repricing
    a.books[e] = {"bids": [lvl(l["price"] + 0.04, 1000) for l in a.books[e]["bids"]],
                  "asks": [lvl(l["price"] + 0.04, 1000) for l in a.books[e]["asks"]]}
b.feed = FakeFeed(); b.feed.push(dirty={"21", "22"})
sent.clear(); t0 = time.monotonic(); b.cycle(); took = time.monotonic() - t0
check("a cancel that hangs doesn't hold up the cycle (time-boxed at write_wait_seconds)", took < 1.5, f"{took:.2f}s")
check("...the other exchange's reprice still went out", any("22" in x for x in sent), sent)
check("...nothing new is placed on the exchange whose cancel is unconfirmed (never two quotes on a side)",
      not any("21" in x for x in sent) and b.ex["21"].writes == 1, (sent, b.ex["21"].writes))
b.feed.push(dirty={"21"}); sent.clear(); b.cycle()
check("...and the next cycle leaves it alone too while the cancel is still running", not any("21" in x for x in sent), sent)
gate.set()
for w in list(b.writes):
    w.future.result(timeout=5)
b.feed.push(dirty={"21"}); sent.clear(); b.cycle()
check("once the cancel is confirmed, the new quote goes out", any("21" in x for x in sent) and len(a.ours("21")) == 2, (sent, a.ours("21")))
a.cancel_order, a.cancel_all = real_co, real_ca

# A placement that hangs: the cycle moves on; its result is applied later (fills attributable); no re-placement meanwhile.
gate2, entered = threading.Event(), threading.Event()
def slow_pb(orders):
    if any(o["exchangeId"] == "11" for o in orders):
        entered.set(); gate2.wait(10)
    return real_pb(orders)
a.place_batch = lambda orders: (sent.append([o["exchangeId"] for o in orders]), slow_pb(orders))[1]
b.cancel("11", [o for o in b.my_orders.values() if o.eid == "11"], whole_exchange=True)
sent.clear(); t0 = time.monotonic(); b.cycle(); took = time.monotonic() - t0
check("a placement that hangs doesn't hold up the cycle", took < 1.5 and entered.is_set(), f"{took:.2f}s")
b.feed.push(dirty={"11"}); n = len(sent); b.cycle()
check("...and isn't sent again while it's in flight", not any("11" in x for x in sent[n:]), sent)
gate2.set()
for w in list(b.writes):
    w.future.result(timeout=5)
b.cycle()
oids = [o["id"] for o in a.orders.values() if o["exchangeId"] == "11"]
check("...when it lands, the bot records the orders and what they were for", len(oids) == 2 and
      all(o in b.my_orders and o in b.order_meta for o in oids), (oids, list(b.my_orders)))

# Writes really run side by side.
a, b = make_bot(); lock_api(a)
live, peak, lk = [0], [0], threading.Lock()
real_pb = a.place_batch
def counting_pb(orders):
    with lk:
        live[0] += 1; peak[0] = max(peak[0], live[0])
    time.sleep(0.1)
    with lk:
        live[0] -= 1
    return real_pb(orders)
a.place_batch, b.cfg.batch_size = counting_pb, 2
b.cycle()
check("several order batches are in flight at once (parallel_writes)", peak[0] >= 2, peak)

a, b = make_bot()
b.cfg.parallel_writes = 1
b.cycle()
check("parallel_writes = 1: the old one-at-a-time path still quotes every market",
      all(len(a.ours(e)) == 2 for e in ("11", "12", "21", "22")) and not b.writes)

a, b = make_bot(); lock_api(a)
gate3 = threading.Event()
real_pb = a.place_batch
a.place_batch = lambda orders: (gate3.wait(0.5), real_pb(orders))[1]
b.cfg.write_wait_seconds = 0.05
b.cycle()
b.running = False
b.shutdown()
check("shutdown waits for writes in flight before cancelling everything (nothing left resting)", not a.orders, a.orders)

# Review fixes: late cancels don't release old orders; pulls always go; counters never leak; queued writes dropped.
a, b = make_bot(); lock_api(a)
b.cycle()
gate = threading.Event()
real_ca = a.cancel_all
a.cancel_all = lambda tid, eid=None: (gate.wait(5) if eid == "11" else None, real_ca(tid, eid))[1]
b.cfg.write_wait_seconds = 0.1
a.books["11"] = {"bids": [lvl(0.14, 1000)], "asks": [lvl(0.22, 1000)]}     # both sides need repricing
b.feed = FakeFeed(); b.feed.push(dirty={"11"}); b.cycle()
check("(setup) the cancel on 11 is still running after the cycle", b.ex["11"].cancelling)
sent = []
real_pb = a.place_batch
a.place_batch = lambda orders: (sent.append([o["exchangeId"] for o in orders]), real_pb(orders))[1]
threading.Timer(0.05, gate.set).start()
b.cfg.write_wait_seconds = 0.5
b.feed.push(dirty={"21"}); b.cycle()                      # the old cancel confirms DURING this cycle's wait
check("a cancel from an earlier cycle that confirms later doesn't send that cycle's (old-price) orders",
      not any("11" in x for x in sent), sent)
b.feed.push(dirty={"11"}); b.cycle()
check("...the next cycle re-plans that exchange and quotes it", len(a.ours("11")) == 2, a.ours("11"))

a, b = make_bot(); b.cycle()
from mm_bot import Change
pulls = [Change(b.ex[e], [o for o in b.my_orders.values() if o.eid == e], True, [], (0, 1, 1, 0)) for e in ("11", "12", "21")]
a.budget_left = lambda: 1
b.send_changes(pulls)
check("with the request budget nearly gone, every pull still goes out", all(not a.ours(e) for e in ("11", "12", "21")),
      {e: a.ours(e) for e in ("11", "12", "21")})
del a.budget_left

a, b = make_bot(); b.cycle()
real_apply = b.apply_batch
b.apply_batch = lambda *x: (_ for _ in ()).throw(RuntimeError("boom"))
for e in a.books:
    a.books[e] = {"bids": [lvl(l["price"] + 0.03, 1000) for l in a.books[e]["bids"]], "asks": [lvl(l["price"] + 0.03, 1000) for l in a.books[e]["asks"]]}
b.feed = FakeFeed(); b.feed.push(dirty=set(a.books))
logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
check("an error applying a write result never leaves an exchange stuck as 'write in flight'",
      all(x.writes == 0 and not x.cancelling for x in b.ex.values()) and b.orders_stale)
b.apply_batch = real_apply

a, b = make_bot(); lock_api(a)
gate = threading.Event()
real_pb = a.place_batch
a.place_batch = lambda orders: (gate.wait(5), real_pb(orders))[1]
b.cfg.parallel_writes, b.cfg.batch_size, b.cfg.write_wait_seconds = 2, 1, 0.05
b.writer = M.ThreadPoolExecutor(max_workers=2)
b.cycle()
queued = [w for w in b.writes if not w.future.running() and not w.future.done()]
b.cancel_everything(); gate.set()
for w in b.writes:
    try: w.future.result(timeout=5)
    except Exception: pass
b.harvest_writes()
check("cancel-everything drops order writes still queued (never sent after the cancel)",
      queued and all(w.future.cancelled() for w in queued) and len(a.orders) <= 2, (len(queued), len(a.orders)))

print("--- book freshness: re-check before calling a book stale (day one: 4.5-min cycles, then quotes pulled)")
check("defaults: book_stale 900 s, 30 books per cycle, bulk re-check after 120 s",
      (CFG.book_stale, CFG.max_books_per_cycle, CFG.book_reverify_seconds) == (900, 30, 120))
a, b = make_bot(); b.cycle()
b.feed = FakeFeed(); b.feed.ok = True; b.last_full_check = time.monotonic()      # event cycles only, no full check
for e in b.ex.values():
    e.verified -= 200
a.calls.clear(); b.feed.push(dirty=set()); b.cycle()
check("between full checks, books unconfirmed for 120 s get one bulk check (not a download each)",
      len(a.sent("bulk")) == 1 and not a.sent("book") and all(time.monotonic() - e.verified < 5 for e in b.ex.values()),
      a.calls)
for e in b.ex.values():
    e.verified -= 200
a.books["21"]["bids"][0]["price"] = 0.50                 # this one moved
a.calls.clear(); b.feed.push(dirty=set()); b.cycle()
check("...a book whose best price moved is downloaded the next cycle", "21" in b.pending_dirty or ("book", "21") in a.calls)
b.feed.push(dirty=set()); b.cycle()
check("...and then it's current again", time.monotonic() - b.ex["21"].verified < 5)
for e in b.ex.values():
    e.verified -= 1000
real_bulk = a.bulk_prices
a.bulk_prices = lambda *x: (_ for _ in ()).throw(ApiError(503, "SERVICE_UNAVAILABLE", "down"))
logging.disable(logging.CRITICAL); b.feed.push(dirty=set()); b.cycle(); logging.disable(logging.NOTSET)
check("if the exchange can't be read at all, a book past book_stale still isn't quoted", not any(a.ours(e) for e in a.books),
      {e: a.ours(e) for e in a.books})
a.bulk_prices = real_bulk

print("--- orders whose placement response was lost (day one: 55 of the first 82 fills unattributed)")
a, b = make_bot()
real_pb = a.place_batch
def lands_but_times_out(orders):
    real_pb(orders)                                       # the exchange places them...
    raise ApiError(409, "REQUEST_IN_FLIGHT", "still in flight")   # ...but we never hear back
a.place_batch = lands_but_times_out
logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
check("(setup) every exchange on hold, the bot doesn't know its orders", all(b.ex[e].pending_until > time.monotonic() for e in a.books)
      and not b.my_orders and len(a.orders) == 8)
a.place_batch = real_pb
n = len(a.sent("batch")); b.cycle()
oids = list(a.orders)
check("next cycle: the landed orders are recognised from the open-orders list, with their notes",
      all(o in b.my_orders and b.order_meta.get(o, {}).get("recovered") for o in oids), (oids, list(b.order_meta)))
check("...the hold lifts at once (not after pending_seconds) and nothing is placed twice",
      all(b.ex[e].pending_until == 0 for e in a.books) and len(a.orders) == 8 and not b.unconfirmed, (len(a.orders), b.unconfirmed))
a.fill("11", True, 40)
b.cycle()
rows = list(csv.DictReader(open(b.cfg.fills_csv)))
check("...and a fill on one is attributed to our bid with its quote price and fair value",
      rows and rows[-1]["our_side"] == "bid" and rows[-1]["fv_at_quote"] != "", rows[-1:])

a, b = make_bot()
a.books["11"]["asks"] = [lvl(0.12, 50), lvl(0.18, 1000)]   # our 0.105 bid would not trade; make the ask cross:
def fill_at_once(orders):
    res = real_pb2(orders)
    for o in list(a.orders.values()):                     # someone takes every order the moment it lands
        is_bid, _ = a.yes_view(o)
        a.fill(o["exchangeId"], is_bid, o["quantity"])
    raise ApiError(0, "NETWORK", "read timed out")
real_pb2 = a.place_batch
a.place_batch = fill_at_once
logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
a.place_batch = real_pb2
b.cycle()
rows = list(csv.DictReader(open(b.cfg.fills_csv)))
check("orders that filled before they were ever listed: their fills are still attributed (matched on side + price)",
      rows and all(r["our_side"] in ("bid", "ask") for r in rows), [r["our_side"] for r in rows])
b.cfg.recover_unconfirmed = False

# Review fixes: a fill alone never lifts the hold; other holds (takes, self-test) are never lifted by a recovery.
a, b = make_bot()
real_pb3 = a.place_batch
a.place_batch = lambda orders: (real_pb3(orders), (_ for _ in ()).throw(ApiError(0, "NETWORK", "timeout")))[1]
logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
a.place_batch = real_pb3
oid11 = next(o["id"] for o in a.orders.values() if o["exchangeId"] == "11" and a.yes_view(o)[0])
a.fill("11", True, 30)                                   # partly filled; the rest still rests
hide = {o for o in a.orders if a.orders[o]["exchangeId"] == "11"}
real_oo = a.open_orders
a.open_orders = lambda tid, eid=None: [o for o in real_oo(tid, eid) if o["id"] not in hide]   # list lags
b.log_fills({})
check("a fill matched to a lost order attributes it but does NOT lift the hold (the rest may still rest)",
      b.order_meta.get(oid11, {}).get("recovered") and b.ex["11"].pending_until > time.monotonic()
      and b.filled_qty.get(oid11) == 30, (b.order_meta.get(oid11), b.ex["11"].pending_until - time.monotonic()))
a.open_orders = real_oo
b.ex["12"].pending_until = time.monotonic() + 500        # e.g. a take's unclear outcome, set later
b.cycle()
check("a recovery doesn't lift a hold something else set", b.ex["12"].pending_until > time.monotonic() + 400)
check("...and the recovered part-filled order's shares left = placed - filled", b.my_orders.get(oid11) and
      b.my_orders[oid11].qty == b.placed_qty[oid11] - 30, b.my_orders.get(oid11))

a, b = make_bot()
b.cfg.recover_unconfirmed = False
a.place_batch = lambda orders: (real_pb_c(orders), (_ for _ in ()).throw(ApiError(409, "REQUEST_IN_FLIGHT", "x")))[1]
real_pb_c = FakeApi.place_batch.__get__(a)
logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
a.place_batch = real_pb_c
b.cycle()
check("recover_unconfirmed = False: the old behaviour (hold for pending_seconds, no notes)",
      all(b.ex[e].pending_until > time.monotonic() for e in a.books) and not b.unconfirmed)

print("--- burst protection (slow exchange: fewer, smaller, wider quotes)")
a, b = make_bot(); b.cycle()
check("normal speed: no burst mode", not b.burst and b.health.get("burst_mode") is False)
now_m = time.monotonic()
b.write_log.extend([(now_m, 18.0, True), (now_m, 16.0, True), (now_m, 20.0, False)])
b.cfg.burst_markets, b.size_plan = 2, {"11": 300, "12": 300, "21": 100, "22": 100}
b.update_burst(now_m)
check("2 write timeouts in a minute -> burst mode, top markets = the 2 biggest", b.burst and b.burst_set == {"11", "12"}, b.burst_set)
before = {e: a.ours(e) for e in a.books}
for e in a.books:                                          # every book moves 1.5c: all quotes want repricing
    a.books[e] = {"bids": [lvl(round(l["price"] + 0.015, 3), 1000) for l in a.books[e]["bids"]],
                  "asks": [lvl(round(l["price"] + 0.015, 3), 1000) for l in a.books[e]["asks"]]}
b.feed = FakeFeed(); b.feed.push(dirty=set(a.books)); b.last_cycle_seconds = 0
real_ub = b.update_burst
b.update_burst = lambda now_m: None                       # hold burst mode on for this cycle
a.calls.clear(); b.cycle()
new21 = [x for x in a.ours("21") if x not in before["21"]]
check("...e.g. Utah: no new orders there", not new21, (before["21"], a.ours("21")))
moved = [x for x in a.ours("11") if x not in before["11"]]
check("burst: top markets repriced at half size", moved and all(n == 50 for _, _, n in moved), (before["11"], a.ours("11")))
b.cancel("22", [o for o in b.my_orders.values() if o.eid == "22"], whole_exchange=True)
b.feed.push(dirty={"22"}); b.cycle()
check("burst: outside the top markets an EMPTY side still gets a quote, at reduced size",
      len(a.ours("22")) == 2 and all(n == 50 for _, _, n in a.ours("22")), a.ours("22"))
b.update_burst = real_ub
b.write_log.clear(); b.burst_calm_since = time.monotonic() - 121
b.update_burst(time.monotonic())
check("after burst_calm_seconds of normal speed: burst mode off", not b.burst)
a, b = make_bot(); b.cfg.burst_protection = False
b.write_log.extend([(time.monotonic(), 30.0, True)] * 5); b.update_burst(time.monotonic())
check("burst_protection = False: never enters burst mode", not b.burst)

a, b = make_bot()
a.cancel_all = lambda tid, eid=None: (_ for _ in ()).throw(ApiError(0, "NETWORK", "read timed out"))
b.cfg.selftest_enabled = False
try:
    logging.disable(logging.CRITICAL); run_cycles(b, 2); crashed = False
except ApiError:
    crashed = True
finally:
    logging.disable(logging.NOTSET)
check("startup: a clean-slate cancel that times out doesn't crash the bot; it goes on quoting", not crashed and a.orders)

print("--- churn control (crowded book: bots stepping in front of us all day)")
a, b = make_bot(); b.cfg.churn_control = True; b.cycle()
start = a.ours("11")
a.books["11"]["bids"] = [lvl(0.11, 1000)]                # a rival pennies our 0.105 bid: target becomes 0.115
b.feed = FakeFeed(); b.feed.push(dirty={"11"}); b.cycle()
check("an order younger than min_quote_life_seconds isn't chased while it's safe", a.ours("11") == start, a.ours("11"))
for o in list(b.recent_orders):
    b.recent_orders[o] = (b.recent_orders[o][0], b.recent_orders[o][1] - 10)
b.feed.push(dirty={"11"}); b.cycle()
check("...older than that: repriced as before", ("bid", 0.115, 100) in a.ours("11"), a.ours("11"))
b.ex["11"].reprices["bid"] = deque([time.monotonic()] * 4)
a.books["11"]["bids"] = [lvl(0.12, 1000)]
for o in list(b.recent_orders):
    b.recent_orders[o] = (b.recent_orders[o][0], b.recent_orders[o][1] - 10)
b.feed.push(dirty={"11"}); b.cycle()
check("4 reprices in a minute on one side: it stops chasing (keeps its safe order, no ping-pong)",
      ("bid", 0.115, 100) in a.ours("11"), a.ours("11"))
a.books["11"]["bids"] = [lvl(0.08, 1000)]; a.books["11"]["asks"] = [lvl(0.09, 1000)]   # fair value drops below our bid
b.feed.push(dirty={"11"}); b.cycle()
check("...but an order that's no longer safe always moves", ("bid", 0.115, 100) not in a.ours("11"), a.ours("11"))
a, b = make_bot(); b.cfg.churn_control = True; b.cycle()
b.refs = FakeRefs({"Ohio Senate|Republican": 0.14})
b.refs.last_moves, b.refs.version = {"Ohio Senate|Republican": 0.01}, 2
b.mark_ref_moves()
check("a Polymarket move of >= 0.5c makes that market's changes the most urgent",
      b.change_key(b.ex["11"], pull=False)[0] == 0.5 and b.change_key(b.ex["21"], pull=False)[0] == 1)
b.mark_ref_moves()
check("...for that reading only", not b.ref_moved)

# Review fixes (items 7-8)
a, b = make_bot(); b.cycle()
b.burst, b.burst_set, b.burst_cfg = True, set(), b.cfg
b.update_burst = lambda now_m: None
a.inv["21"] = 300                                          # long Utah, and the exit window has started
for e in b.ex.values():
    e.close = utcnow() + timedelta(hours=1)
b.cycle(); b.cycle()
check("burst mode never blocks the election-night exit (markets outside the top N still get exit orders)",
      any(side == "ask" for side, _, _ in a.ours("21")), a.ours("21"))
a, b = make_bot(); b.cfg.churn_control = True; b.cycle()
o = next(x for x in b.my_orders.values() if x.eid == "11" and x.is_bid)
check("churn hold never keeps an order bigger than the size now wanted",
      not b.hold_side(b.ex["11"], [o], o.price, o.price + 0.01, o.qty - 10, True, utcnow(), time.monotonic()))
wapi2 = Api(CFG, False)
wapi2.gap, wapi2.budget, wapi2.wbudget, wapi2.BUDGET_WINDOW = 0.001, 100, 1, 1.0
wapi2.throttle(write=True)
th = threading.Thread(target=lambda: wapi2.throttle(write=True)); th.start()   # waits ~1 s for the write window
time.sleep(0.05); t0 = time.monotonic(); wapi2.throttle(); waited = time.monotonic() - t0
th.join()
check("a write waiting on the write budget doesn't hold up reads", waited < 0.3, f"{waited:.2f}s")

a, b = make_bot(); b.cycle()
real_pos = a.positions
a.positions = lambda: (_ for _ in ()).throw(ApiError(409, "CONFLICT", "Tournament holdings cannot be valued"))
b.orders_stale = True
logging.disable(logging.CRITICAL)
try:
    b.cycle(); ok = True
except ApiError:
    ok = False
logging.disable(logging.NOTSET)
a.positions = real_pos
check("positions 409 'holdings cannot be valued' (day one 16:30): the cycle goes on with the last read", ok and b.orders_stale)

print("--- parallel requests")
a, b = make_bot()
res = b.in_parallel(lambda x: 1 / x, [1, 0, 2])
check("in_parallel: results per item, one failure doesn't stop the rest",
      res[1] == 1.0 and res[2] == 0.5 and isinstance(res[0], ZeroDivisionError), res)
real_api = Api(CFG, False)
real_api.gap = 0.05
t0 = time.monotonic()
threads = [threading.Thread(target=lambda: [real_api.throttle() for _ in range(3)]) for _ in range(4)]
[t.start() for t in threads]; [t.join() for t in threads]
elapsed = time.monotonic() - t0
check("rate limiter spaces 12 requests from 4 threads >= 0.05 s apart", 0.5 <= elapsed < 1.5, f"{elapsed:.2f}s")
wapi = Api(CFG, False)
wapi.gap, wapi.budget, wapi.wbudget, wapi.BUDGET_WINDOW = 0.001, 100, 3, 1.0     # 3 writes per 1 s, for speed
t0 = time.monotonic()
for _ in range(3):
    wapi.throttle(write=True)
for _ in range(5):
    wapi.throttle()                                         # reads aren't held back by the write budget
check("write budget: separate from reads (3 writes + 5 reads go at once)", time.monotonic() - t0 < 0.3 and wapi.writes_left() == 0,
      wapi.writes_left())
wapi.throttle(write=True)
check("...a 4th write in the window waits for it", time.monotonic() - t0 >= 0.9, time.monotonic() - t0)
check("default write budget: 30/min (conservative reading of the platform docs)", Config().writes_per_minute == 30)
budget_api = Api(CFG, False)
budget_api.gap, budget_api.budget, budget_api.BUDGET_WINDOW = 0.001, 5, 1.0   # 5 requests per 1 s, for speed
t0 = time.monotonic()
for _ in range(5):
    budget_api.throttle()
check("budget_left counts what's been used (5 of 5)", budget_api.budget_left() == 0, budget_api.budget_left())
budget_api.throttle()
elapsed = time.monotonic() - t0
check("hard budget: the 6th request in a 5-per-window budget waits for the window to roll", 0.95 <= elapsed < 1.6, f"{elapsed:.2f}s")

# 429: every thread pauses for Retry-After, the gap doubles, then the retry succeeds.
class Resp:
    def __init__(s, code, headers=None): s.status_code, s.headers, s.text = code, headers or {}, "{}"; s.content = b"{}"
    def json(s): return {}
answers = [Resp(429, {"Retry-After": "0.3"}), Resp(200)]
limited_api = Api(CFG, False)
limited_api.gap = 0.4                                       # above min_request_gap (0.30)
limited_api.s.request = lambda *a, **k: answers.pop(0)
t0 = time.monotonic(); status, _ = limited_api.call("GET", "/x"); waited = time.monotonic() - t0
check("429 -> waits Retry-After before retrying, then succeeds", status == 200 and waited >= 0.29, f"{waited:.2f}s")
check("429 -> request gap doubled (0.4 -> 0.8, then drifts down 1%) and counted",
      abs(limited_api.gap - 0.8 * 0.99) < 1e-9 and limited_api.rate_limited == 1, (limited_api.gap, limited_api.rate_limited))

# HTML error page: retried as transient, then an ApiError - never a crash. (Sleeping disabled.)
class R:
    def __init__(s, code, text): s.status_code, s.text, s.content, s.headers = code, text, text.encode(), {}
    def json(s): raise ValueError
real_api.s.request = lambda *a, **k: R(502, "<html>bad gateway</html>")
real_sleep, M.time.sleep = M.time.sleep, (lambda s: None)
try:
    real_api.call("GET", "/x"); ok = False
except ApiError as e:
    ok = e.code == "BAD_RESPONSE"
finally:
    M.time.sleep = real_sleep
check("HTML error page -> ApiError, never a crash", ok)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
