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
check("F8: a passed self-test clears selftest_eid (that exchange is ordinary again)", b.selftest_eid is None)
a, b = make_bot()
b.selftest_eid = test_eid = b.selftest_start()
b.selftest_finish(test_eid, ("passed", [], b.cfg.order_ttl))
gen = b.cancel_gen
b.cancel(test_eid, [], True)
check("F8: after a pass, a whole-exchange cancel on the old test exchange does not bump cancel_gen",
      b.selftest_eid is None and b.cancel_gen == gen, (b.selftest_eid, gen, b.cancel_gen))

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

print("--- pair unwind (sell a held complete set at bids adding up to >= 1)")
def unwind_books(b11, b12, a11=(0.70, 300), a12=(0.55, 200), extra=None):
    books = {"11": {"bids": [lvl(*b11)], "asks": [lvl(*a11)]}, "12": {"bids": [lvl(*b12)], "asks": [lvl(*a12)]},
             "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]}, "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    books.update(extra or {})
    return books
def unwind_bot(inv, books, extra_markets=(), **cfg_kw):
    a, b = make_bot(books=books, extra_markets=extra_markets)
    a.inv = dict(inv)
    for k, v in cfg_kw.items():
        setattr(b.cfg, k, v)
    return a, b

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
b.cycle()
check("long pair, bids add up to 1.005 -> both legs sold up to the bid size (200 of the 500 sets)",
      a.inv == {"11": 300, "12": 300}, a.inv)
check("counted as a pair unwind (status.json), not as an arbitrage",
      b.unwinds_total == 1 and b.arbs_total == 0 and b.health["pair_unwinds_total"] == 1, (b.unwinds_total, b.arbs_total))
check("no unwind leftovers resting on the Ohio legs", not a.ours("11") and not a.ours("12"), (a.ours("11"), a.ours("12")))
b.cycle()                                 # fills are logged on the next read
uw_rows = [r for r in read_fills(b.cfg.fills_csv) if r["exchange_id"] in ("11", "12")]
check("unwind fills logged as our asks", sorted(r["our_side"] for r in uw_rows) == ["ask", "ask"], uw_rows)

a, b = unwind_bot({"11": 150, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
b.cycle()
check("sells at most the number of complete sets (min over legs = 150)", a.inv == {"11": 0, "12": 350}, a.inv)

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.595, 300), (0.40, 200)))
b.cycle()
check("long pair, bids add up to 0.995 -> nothing sold", a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.60, 300), (0.40, 200)), pair_unwind_min_profit=0.0)
b.cycle()
check("bids add up to exactly 1.000 with pair_unwind_min_profit 0 -> unwound at fair", a.inv == {"11": 300, "12": 300}, a.inv)
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.60, 300), (0.40, 200)))
b.cycle()
check("...but the default pair_unwind_min_profit (0.5c) wants 1.005: a one-leg partial fill at 1.000 could be 2c offside",
      a.inv == {"11": 500, "12": 500} and Config().pair_unwind_min_profit == 0.005 and Config().pair_unwind_max_frac == 0.01,
      a.inv)
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)), pair_unwind_min_profit=0.01)
b.cycle()
check("pair_unwind_min_profit 0.01 -> 1.005 is not enough", a.inv == {"11": 500, "12": 500}, a.inv)

a, b = unwind_bot({"11": -500, "12": -500}, unwind_books((0.50, 300), (0.30, 200), a11=(0.60, 300), a12=(0.39, 400)))
b.cycle()
check("short pair, asks add up to 0.99 -> bought back up to the ask size (300)", a.inv == {"11": -200, "12": -200}, a.inv)
check("short-pair buy-back counted as an unwind", b.unwinds_total == 1 and b.arbs_total == 0, (b.unwinds_total, b.arbs_total))
a, b = unwind_bot({"11": -500, "12": -500}, unwind_books((0.50, 300), (0.30, 200), a11=(0.61, 300), a12=(0.40, 400)))
b.cycle()
check("short pair, asks add up to 1.01 -> nothing bought", a.inv == {"11": -500, "12": -500}, a.inv)

ind_market = market("9", "13", "Independent", "Ohio Senate")
ind_book = {"13": {"bids": [lvl(0.05, 1000)], "asks": [lvl(0.08, 1000)]}}
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.60, 300), (0.36, 200), extra=ind_book), extra_markets=[ind_market])
check("an independent's market joins its race's group", sorted(b.groups["Ohio Senate"]) == ["11", "12", "13"], dict(b.groups))
b.cycle()
check("race with an independent leg we don't hold: the Dem+Rep pair is not a set -> left alone",
      a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)
a, b = unwind_bot({"11": 500, "12": 500, "13": 400}, unwind_books((0.60, 300), (0.36, 200), extra=ind_book),
                  extra_markets=[ind_market])
b.cycle()
check("...but holding all three legs is a set: bids 0.60+0.36+0.05 = 1.01 -> all three sold",
      a.inv == {"11": 300, "12": 300, "13": 200}, a.inv)

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)), pair_unwind_max_frac=0.001)
b.cycle()
check("cash cap: 0.1% of 100k = 100 per order at 0.605 -> 165 sets", a.inv == {"11": 335, "12": 335}, a.inv)

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
b.cycle()
a.books.update(unwind_books((0.605, 300), (0.40, 200)))
b.cycle()
check("cooldown: no second unwind within pair_unwind_cooldown_seconds", a.inv == {"11": 300, "12": 300} and b.unwinds_total == 1,
      (a.inv, b.unwinds_total))
b.arb_cooldown.clear()
a.cancel_all("T")
b.cycle()
check("...and another once it has passed", a.inv == {"11": 100, "12": 100} and b.unwinds_total == 2, (a.inv, b.unwinds_total))

a, b = unwind_bot({"11": 500, "12": 500, "21": 3000}, unwind_books((0.605, 300), (0.40, 200)), max_worst_case_frac=0.01)
b.global_reduce = True
b.cycle()
check("reduce-only still unwinds (it only reduces positions)", b.health["reduce_only"] and a.inv["11"] == 300 and a.inv["12"] == 300,
      (b.health["reduce_only"], a.inv))
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
for e in ("11", "12"):
    b.ex[e].close = utcnow() + timedelta(hours=1)
b.cycle()
check("the pre-close window still unwinds", b.unwinds_total == 1 and b.arbs_total == 0, (b.unwinds_total, a.inv))

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)), pair_unwind_enabled=False, arb_two_sided=False)
b.cycle()
check("pair_unwind_enabled False -> today's behaviour: the pair is kept", a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.57, 300), (0.45, 200)), pair_unwind_enabled=False, arb_two_sided=False)
b.cycle()
check("...and the sell-side arbitrage threshold is unchanged (1.02 < 1.03 -> nothing)", not a.inv, a.inv)

a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
a.writes_left = lambda: 4                 # needs 2 cancels + 1 batch + 2 cancels
b.cycle()
check("write budget below the 5 writes an unwind needs -> deferred", a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
b.ex["12"].pending_until = time.monotonic() + 600
b.cycle()
check("a leg with a write in flight (busy) -> no unwind", a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
b.running = False
check("stopped (kill switch / Ctrl+C) -> no unwind", b.take_arbitrage(a.inv, {}, {}, time.monotonic()) == set() and not a.sent("batch"))

own_exp = iso(utcnow() + timedelta(hours=1))
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.59, 300), (0.40, 200)))
a.orders[999] = {"id": 999, "exchangeId": "11", "quantity": 50, "open": True, "expirationDate": own_exp,
                 "side": "yes", "action": "buy", "priceLimit": 0.62}   # OUR bid on top: with it the bids add up to 1.02
b.cycle()
check("our own bid at the top is excluded from the sum (others' 0.99 -> no unwind)",
      a.inv.get("11") == 500 and a.inv.get("12") == 500 and b.unwinds_total == 0, a.inv)

print("--- risk guard for pair unwinds")
a, b = unwind_bot({"11": 500, "12": 500}, unwind_books((0.605, 300), (0.40, 200)))
fv = {"11": 0.6, "12": 0.4, "21": 0.52, "22": 0.48}
check("selling a complete set never raises the risk or the party delta", b.unwind_is_safe(a.inv, fv, ["11", "12"], -200))
check("selling ONE leg of a hedged pair would (guard says no)", not b.unwind_is_safe(a.inv, fv, ["11"], -500))
check("race_variance and worst case treat a long pair as zero risk",
      race_variance([(500, 0.6), (500, 0.4)]) < 1e-9 and worst_case_loss([(500, 0.6), (500, 0.4)]) < 1e-9)
b.unwind_is_safe = lambda *args: False
b.cycle()
check("unwind skipped when the risk guard refuses it", a.inv == {"11": 500, "12": 500} and b.unwinds_total == 0, a.inv)

print("--- two-sided arbitrage (buy every leg when the asks add up to <= 0.985)")
# A set pays 1 only if a LISTED party wins: every leg needs a liquid Polymarket price and the RAW prices must add
# up to >= arb_buy_min_ref_sum (0.99). Book fair values are normalised to 1, so they cannot see an outsider.
ohio_refs = lambda r, d, spread=0.01: FakeRefs({"Ohio Senate|Republican": r, "Ohio Senate|Democratic": d,
                                                "Utah Senate|Republican": 0.52, "Utah Senate|Democratic": 0.48}, spread=spread)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.cycle()
check("asks add up to 0.98 but NO Polymarket prices -> nothing bought (the guard needs liquid references)",
      not a.inv and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.refs = ohio_refs(0.56, 0.39)                            # raw references add up to 0.95: an outsider is priced
b.cycle()
check("asks 0.98, references add up to 0.95 -> nothing bought (unlisted candidate)", not a.inv and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.refs = ohio_refs(0.56, 0.44, spread=None)               # last-trade-only Polymarket: not liquid
b.cycle()
check("asks 0.98, references 1.00 but one leg is not liquid -> nothing bought", not a.inv and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.refs = ohio_refs(0.56, 0.44)
b.cycle()
check("asks add up to 0.98, liquid references add up to 1.00 -> bought 200 YES on both legs (the ask size)",
      a.inv == {"11": 200, "12": 200}, a.inv)
check("counted as an arbitrage", b.arbs_total == 1 and b.unwinds_total == 0, (b.arbs_total, b.unwinds_total))
check("no buy leftovers resting on the Ohio legs", not a.ours("11") and not a.ours("12"), (a.ours("11"), a.ours("12")))
b.cycle()
arb_buy_rows = [r for r in read_fills(b.cfg.fills_csv) if r["exchange_id"] in ("11", "12")]
check("buy-side fills logged as our bids", sorted(r["our_side"] for r in arb_buy_rows) == ["bid", "bid"], arb_buy_rows)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.59, 300), a12=(0.40, 200)))
b.refs = ohio_refs(0.56, 0.44)
b.cycle()
check("asks add up to 0.99 -> nothing bought", not a.inv and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)), arb_two_sided=False)
b.cycle()
check("arb_two_sided False -> 0.98 is left alone", not a.inv and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.20, 200), a11=(0.58, 300), a12=(0.25, 200)))
b.cycle()
check("asks add up to 0.83 (< arb_buy_min_sum 0.90: an outsider is likely priced) -> nothing bought", not a.inv, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.global_reduce = True
b.cycle()
check("no buy-side arbitrage in reduce-only (it adds gross positions)", not a.inv.get("11") and not a.inv.get("12"), a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.60, 300), a12=(0.40, 200)))
a.orders[998] = {"id": 998, "exchangeId": "11", "quantity": 50, "open": True, "expirationDate": own_exp,
                 "side": "yes", "action": "sell", "priceLimit": 0.57}  # OUR ask on top: with it the asks add up to 0.97
b.cycle()
check("our own ask at the top is excluded from the sum (others' 1.00 -> no buy)",
      not a.inv.get("11") and not a.inv.get("12") and b.arbs_total == 0, a.inv)
a, b = unwind_bot({}, unwind_books((0.50, 300), (0.30, 200), a11=(0.58, 300), a12=(0.40, 200)))
b.refs = ohio_refs(0.56, 0.44)
b.cycle()
a.books.update(unwind_books((0.605, 300), (0.40, 200), a11=(0.70, 300), a12=(0.55, 200)))
b.arb_cooldown.clear()
a.cancel_all("T")
b.cycle()
check("the bought set is later unwound when the bids add up to 1.005 (round trip +0.025 per set)",
      a.inv == {"11": 0, "12": 0} and b.arbs_total == 1 and b.unwinds_total == 1, a.inv)

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
a, b = take_setup()
b.refs.ages = lambda: {k: 120.0 for k in b.refs.prices}   # downloads failing: the same old price, re-read
b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("a Polymarket price not re-downloaded for > take_ref_max_age_seconds never triggers a take", not a.inv, a.inv)
b.refs.ages = lambda: {k: 2.0 for k in b.refs.prices}     # fresh again: the 30 s confirmation starts over
b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("...fresh again: the confirmation starts over (nothing at once)", not a.inv, a.inv)
later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("...then trades once confirmed on fresh readings", a.inv.get("11") == 1000, a.inv)
a, b = take_setup(); b.cfg.take_ref_max_age_seconds = 0
b.refs.ages = lambda: {k: 120.0 for k in b.refs.prices}
b.cycle(); later(b); b.refs.new_reading(b.refs.prices, {}); b.cycle()
check("take_ref_max_age_seconds = 0: the old behaviour (age not checked)", a.inv.get("11") == 1000, a.inv)

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

# A reprice that removes an UNSAFE order (beyond its limit) is as urgent as a pull: never deferred by the budget.
a, b = make_bot(); b.cycle()
rest = lambda e: [o for o in b.my_orders.values() if o.eid == e]
bid11 = [o for o in rest("11") if o.is_bid][0]
ask11 = [o for o in rest("11") if not o.is_bid][0]
now, now_m = M.utcnow(), time.monotonic()
unsafe_q = Quote(bid11.price - 0.02, int(bid11.qty), ask11.price, int(ask11.qty), bid11.price - 0.01, ask11.price)
safe_q = Quote(bid11.price + 0.01, int(bid11.qty), ask11.price, int(ask11.qty), bid11.price + 0.01, ask11.price)
ch_unsafe = b.plan_change(b.ex["11"], unsafe_q, rest("11"), 0.14, now, now_m)
ch_safe = b.plan_change(b.ex["11"], safe_q, rest("11"), 0.14, now, now_m)
check("unsafe_order: beyond the limit, oversized or an unwanted side; a better-priced target is safe",
      unsafe_order(bid11, bid11.price - 0.02, bid11.qty, bid11.price - 0.01, True)
      and unsafe_order(bid11, bid11.price, bid11.qty - 1, None, True) and unsafe_order(bid11, None, 0, None, True)
      and not unsafe_order(bid11, bid11.price + 0.01, bid11.qty, bid11.price + 0.01, True))
check("a reprice whose old bid is now beyond its limit is keyed like a pull (never deferred)",
      ch_unsafe is not None and ch_unsafe.key[0] == 0 and ch_unsafe.new, ch_unsafe and ch_unsafe.key)
check("...a reprice of a still-safe order is not (it may wait for budget)", ch_safe is not None and ch_safe.key[0] != 0,
      ch_safe and ch_safe.key)
b.cfg.never_defer_unsafe = False
ch_off = b.plan_change(b.ex["11"], unsafe_q, rest("11"), 0.14, now, now_m)
check("never_defer_unsafe = False: the old keying (only pure pulls are exempt)", ch_off is not None and ch_off.key[0] != 0)
b.cfg.never_defer_unsafe = True
bid21 = [o for o in rest("21") if o.is_bid][0]
ch_safe21 = b.plan_change(b.ex["21"], Quote(bid21.price + 0.01, int(bid21.qty), None, 0, bid21.price + 0.01, None),
                          [bid21], 0.5, now, now_m)
a.budget_left = lambda: 1
b.send_changes([ch_unsafe, ch_safe21])
check("with the budget gone, the unsafe reprice goes out and the safe one waits",
      any(s == "bid" and abs(p - (bid11.price - 0.02)) < 1e-9 for s, p, _ in a.ours("11"))
      and bid21.order_id in a.orders, (a.ours("11"), a.ours("21")))
del a.budget_left

# While the first books are still loading after a start, writes stay at startup_writes_per_minute (30), so the
# bigger write budget doesn't take the request budget the book downloads need.
def safe_reprice(b, e):
    o = [x for x in b.my_orders.values() if x.eid == e and x.is_bid][0]
    return o, b.plan_change(b.ex[e], Quote(o.price + 0.01, int(o.qty), None, 0, o.price + 0.01, None), [o],
                            0.5, M.utcnow(), time.monotonic())
for loading in (True, False):
    a, b = make_bot(); b.cycle()
    a.wbudget, a.writes_left = 45, (lambda: 16)               # 29 writes used in the last minute
    b.trading_since = time.monotonic()
    if loading:
        b.ex["22"].book = None
    o21, ch21 = safe_reprice(b, "21")
    b.send_changes([ch21])
    check("first books still loading: 29 of the 30 start-up writes used -> a reprice waits" if loading else
          "...all books in: the full write budget (16 left) -> it goes",
          (o21.order_id in a.orders) == loading, (loading, a.ours("21")))
a, b = make_bot(); b.cycle(); b.cfg.startup_writes_per_minute = 0
a.wbudget, a.writes_left = 45, (lambda: 16)
b.trading_since = time.monotonic(); b.ex["22"].book = None
o21, ch21 = safe_reprice(b, "21"); b.send_changes([ch21])
check("startup_writes_per_minute = 0: no start-up cap", o21.order_id not in a.orders)

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

# Start-up: the first cycles are long because of our own throttled book downloads (2 Oct 08:08: 61 s -> burst for
# 2 min with a 0.4 s write median). The cycle-length trigger waits out burst_startup_grace_seconds and the first
# download of every book; slow writes and timeouts still count.
a, b = make_bot(); b.cycle()
now_m = time.monotonic()
b.trading_since, b.last_cycle_seconds = now_m - 5, 61.0
b.update_burst(now_m)
check("start-up: a 61 s first cycle doesn't trigger burst mode", not b.burst)
b.write_log.extend([(now_m, 18.0, True), (now_m, 16.0, True)])
b.update_burst(now_m)
check("...but 2 write timeouts during the start-up grace still do", b.burst)
a, b = make_bot(); b.cycle()
now_m = time.monotonic()
b.trading_since, b.last_cycle_seconds = now_m - 200, 61.0
b.ex["11"].book = None                                     # still downloading the first books
b.update_burst(now_m)
check("past the grace, while the first download of every book is still running: no burst from cycle length",
      not b.burst)
b.trading_since = now_m - 700
b.update_burst(now_m)
check("...that part of the grace ends after BURST_LOADING_MAX_SECONDS (a book that never loads can't block it)", b.burst)
a, b = make_bot(); b.cycle()
now_m = time.monotonic()
b.trading_since, b.last_cycle_seconds = now_m - 91, 61.0
b.update_burst(now_m)
check("past the grace with every book loaded: a 61 s cycle triggers burst mode as before", b.burst)
a, b = make_bot(); b.cycle(); b.cfg.burst_startup_grace_seconds = 0
now_m = time.monotonic()
b.trading_since, b.last_cycle_seconds = now_m - 1, 61.0
b.update_burst(now_m)
check("burst_startup_grace_seconds = 0: the old behaviour (a long first cycle triggers it)", b.burst)
a, b = make_bot(); b.cfg.selftest_enabled = False
run_cycles(b, 1)
check("run() starts the start-up grace clock when trading starts", b.trading_since is not None)

# Burst half size vs keep_fraction: a size-375 quote is placed as 187; that order must then count as matching
# (187.5 <= 187 was False: cancelled and replaced every cycle).
a, b = make_bot(); b.cycle()
b.burst, b.burst_set, b.burst_cfg = True, {"11"}, b.cfg
o11 = [o for o in b.my_orders.values() if o.eid == "11"]
bid11 = [o for o in o11 if o.is_bid][0]; ask11 = [o for o in o11 if not o.is_bid][0]
q375 = Quote(bid11.price, 375, ask11.price, 375, bid11.price, ask11.price)
now, now_m = M.utcnow(), time.monotonic()
ch = b.plan_change(b.ex["11"], q375, [o for o in o11 if not o.is_bid], 0.14, now, now_m)
check("burst: a 375-share quote is placed at 187 (half)", ch is not None and [o["quantity"] for o, _ in ch.new] == [187],
      ch and [o["quantity"] for o, _ in ch.new])
bid11.qty = ask11.qty = 187
ch = b.plan_change(b.ex["11"], q375, o11, 0.14, now, now_m)
check("burst: ...and a resting 187 then matches it (no cancel + replace every cycle)", ch is None,
      ch and (ch.doomed, ch.new))
b.burst = False
ch = b.plan_change(b.ex["11"], q375, o11, 0.14, now, now_m)
check("out of burst mode, the 187 is below keep_fraction of 375 and is replaced at full size",
      ch is not None and sorted(o["quantity"] for o, _ in ch.new) == [375, 375], ch and ch.new)
# Entering burst mode must NOT pull the full-size orders placed before it (they are not "oversized": the normal
# size stays the ceiling), in a top market and in a non-top market alike - or every resting quote would be
# cancelled and re-placed at half size exactly when the exchange is slow.
b.burst = True
bid11.qty = ask11.qty = 375
ch = b.plan_change(b.ex["11"], q375, o11, 0.14, now, now_m)
check("entering burst: a resting full-size (375) order in a top market stays (not cancelled for being 'oversized')",
      ch is None, ch and (ch.doomed, ch.new))
b.burst_set = set()                                   # market 11 is not a top market in this burst
ch = b.plan_change(b.ex["11"], q375, o11, 0.14, now, now_m)
check("entering burst: ...and in a non-top market too (safe orders stay)", ch is None, ch and (ch.doomed, ch.new))
q_big = Quote(bid11.price, 400, ask11.price, 400, bid11.price, ask11.price)
bid11.qty = ask11.qty = 450
ch = b.plan_change(b.ex["11"], q_big, o11, 0.14, now, now_m)
check("entering burst: an order bigger than the NORMAL size is still pulled", ch is not None and len(ch.doomed) == 2,
      ch and (ch.doomed, ch.new))
b.burst, b.burst_set = False, set()

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
# F10: a reprice counts toward churn_max_reprices only when its cancel is sent, not when the budget defers it.
for count_sent in (True, False):
    a, b = make_bot(); b.cfg.churn_control, b.cfg.churn_count_sent = True, count_sent; b.cycle()
    for o in list(b.recent_orders):
        b.recent_orders[o] = (b.recent_orders[o][0], b.recent_orders[o][1] - 10)
    start = a.ours("11")
    a.books["11"]["bids"] = [lvl(0.11, 1000)]            # target moves to 0.115: a reprice
    a.writes_left = lambda: 0                             # ...but no write budget left this cycle
    b.feed = FakeFeed(); b.feed.push(dirty={"11"})
    logging.disable(logging.CRITICAL); b.cycle(); logging.disable(logging.NOTSET)
    deferred = len(b.ex["11"].reprices.get("bid") or ())
    if count_sent:
        check("F10: a reprice deferred by the write budget is not counted (and nothing was sent)",
              deferred == 0 and a.ours("11") == start, (deferred, a.ours("11")))
        del a.writes_left
        b.feed.push(dirty={"11"}); b.cycle()
        check("F10: ...once it is actually sent, it counts once",
              len(b.ex["11"].reprices.get("bid") or ()) == 1 and ("bid", 0.115, 100) in a.ours("11"),
              (b.ex["11"].reprices, a.ours("11")))
    else:
        check("F10: churn_count_sent=False keeps the old count-when-planned behaviour", deferred == 1, deferred)
a, b = make_bot(); b.cfg.churn_control = True; b.cycle()
b.burst, b.burst_set, b.burst_cfg = True, set(), b.cfg
b.update_burst = lambda now_m: None
for o in list(b.recent_orders):
    b.recent_orders[o] = (b.recent_orders[o][0], b.recent_orders[o][1] - 10)
a.books["11"]["bids"] = [lvl(0.11, 1000)]                # off target but still safe: burst mode keeps it
b.feed = FakeFeed(); b.feed.push(dirty={"11"}); b.cycle()
check("F10: a reprice that burst mode drops (keeps the safe order) is not counted",
      not b.ex["11"].reprices.get("bid"), b.ex["11"].reprices)

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

print("--- live settings (settings_override.json, re-read every 30 s)")
a, b = make_bot()
alerts = []
real_alert, M.alert = M.alert, lambda m: alerts.append(m)
with open(b.cfg.overrides_file, "w") as f:
    json.dump({"min_edge": 0.015, "burst_markets": 25, "api_key": "x", "max_half_spread": 5, "churn_control": "yes"}, f)
logging.disable(logging.CRITICAL); b.check_overrides(force=True); logging.disable(logging.NOTSET)
check("valid overrides applied", b.cfg.min_edge == 0.015 and b.cfg.burst_markets == 25, (b.cfg.min_edge, b.cfg.burst_markets))
check("secrets, out-of-range values and wrong types refused (and reported), never applied",
      b.cfg.api_key == "test-key" and b.cfg.max_half_spread == 0.04 and b.cfg.churn_control is False
      and alerts and all(k in alerts[0] for k in ("api_key", "max_half_spread", "churn_control")), alerts)
with open(b.cfg.overrides_file, "w") as f:
    json.dump({"burst_markets": 25}, f)
os.utime(b.cfg.overrides_file, (time.time() + 5, time.time() + 5))
logging.disable(logging.CRITICAL); b.check_overrides(force=True); logging.disable(logging.NOTSET)
check("a setting removed from the file goes back to its default", b.cfg.min_edge == 0.01 and b.cfg.burst_markets == 25)
with open(b.cfg.overrides_file, "w") as f:
    f.write("{broken")
os.utime(b.cfg.overrides_file, (time.time() + 10, time.time() + 10))
alerts.clear(); logging.disable(logging.CRITICAL); b.check_overrides(force=True); logging.disable(logging.NOTSET)
check("an unreadable file keeps the current settings (and alerts)", b.cfg.burst_markets == 25 and alerts, alerts)
M.alert = real_alert
check("never overridable: secrets, URLs, files, the kill switch", not {"api_key", "base_url", "slug", "alert_url",
      "max_drawdown_pct", "fills_csv", "overrides_file"} & set(M.OVERRIDABLE))
check("every overridable name is a real setting", all(hasattr(Config(), k) for k in M.OVERRIDABLE))
_d = Config()
_ok, _bad = validate_overrides({k: getattr(_d, k) for k in M.OVERRIDABLE}, _d)
check("every default is inside its live range (the file can always restore it)", not _bad and len(_ok) == len(M.OVERRIDABLE),
      _bad)
_new = {"skew_per_quote": 0.01, "skew_max": 0.05, "max_skew_through": 0.01, "improve_ticks": 0, "undercut_step_back": 0.01,
        "ref_only_enabled": False, "ref_only_max_gap": 0.05, "ref_only_min_edge": 0.02, "ref_only_size_frac": 0.002,
        "ref_only_reduce_full": False, "risk_swing_shock": 0.2, "risk_z": 2.5, "worst_case_backstop_frac": 0.5,
        "order_ttl": 3600, "refresh_before_expiry": 300, "batch_size": 5, "kelly_no_edge_frac": 0.002,
        "writes_per_minute_max": 80, "write_budget_cut": 0.5, "never_defer_unsafe": False,
        "startup_writes_per_minute": 20, "burst_startup_grace_seconds": 120, "take_ref_max_age_seconds": 60}
_ok, _bad = validate_overrides(_new, _d)
check("the new live settings are accepted (quoting, thin-book, risk, lifecycle, write budget, burst, take)",
      not _bad and _ok == {k: (float(v) if isinstance(getattr(_d, k), float) else v) for k, v in _new.items()}, _bad)
_out = {"skew_per_quote": 0.06, "skew_max": 0.2, "max_skew_through": -0.01, "improve_ticks": 4, "undercut_step_back": 0.1,
        "ref_only_enabled": 1, "ref_only_max_gap": 0.001, "ref_only_min_edge": 0.2, "ref_only_size_frac": 0.05,
        "ref_only_reduce_full": "yes", "risk_swing_shock": 0.01, "risk_z": 7, "worst_case_backstop_frac": 0.95,
        "order_ttl": 100, "refresh_before_expiry": 1000, "batch_size": 0, "kelly_no_edge_frac": 0.02,
        "writes_per_minute_max": 200, "write_budget_cut": 0.1, "never_defer_unsafe": 0,
        "startup_writes_per_minute": -1, "burst_startup_grace_seconds": 601, "take_ref_max_age_seconds": 301}
_ok, _bad = validate_overrides(_out, _d)
check("...and refused out of range or of the wrong type (every one)", not _ok and len(_bad) == len(_out), (_ok, _bad))
_ok, _bad = validate_overrides({"order_ttl": 600, "refresh_before_expiry": 400}, _d)
check("refresh_before_expiry over half of order_ttl is refused (orders would be replaced every cycle)",
      not _ok and len(_bad) == 2, (_ok, _bad))
_ok, _bad = validate_overrides({"order_ttl": 300}, _d)                # default refresh 180 > 150
check("...also against the current value of the one not in the file", not _ok and _bad, (_ok, _bad))
a, b = make_bot(); b.cycle()
with open(b.cfg.overrides_file, "w") as f:
    json.dump({"batch_size": 3, "order_ttl": 3600, "writes_per_minute": 20, "writes_per_minute_max": 25}, f)
b.check_overrides()
check("live: batch_size / order_ttl apply, writes_per_minute resets the write budget, a lower ceiling caps it",
      b.cfg.batch_size == 3 and b.cfg.order_ttl == 3600 and b.api.wbudget <= 25, (b.cfg.batch_size, b.api.wbudget))

print("--- start-up clean slate only when needed; status during long cycles")
a, b = make_bot(); b.cfg.selftest_enabled = False
run_cycles(b, 1)
check("start-up: no orders resting -> no clean-slate cancel (one fewer write at a slow open)",
      a.calls.index(("cancel_all", None)) > min(i for i, c in enumerate(a.calls) if c[0] == "batch"), a.calls[:6])
a, b = make_bot(); b.cfg.selftest_enabled = False
a.orders[999] = {"id": 999, "exchangeId": "11", "side": "yes", "action": "buy", "priceLimit": 0.05, "quantity": 5,
                 "open": True, "expirationDate": iso(utcnow() + timedelta(minutes=20))}
run_cycles(b, 1)
check("start-up: orders left over -> cancelled first",
      a.calls.index(("cancel_all", None)) < min(i for i, c in enumerate(a.calls) if c[0] == "batch"), a.calls[:4])
a, b = make_bot()
alerts = []
real_alert, M.alert = M.alert, lambda m: alerts.append(m)
b.cfg.slow_cycle_alert_seconds = 10
b.cycle_started = time.monotonic() - 30
b.progress("sending orders")
st = json.load(open(b.cfg.status_file))
M.alert = real_alert
check("a long cycle keeps status.json fresh (running seconds + phase) and alerts once",
      st.get("cycle_running_seconds") == 30 and st.get("cycle_phase") == "sending orders" and len(alerts) == 1, (st.get("cycle_phase"), alerts))

print("--- analyze (local files only)")
d = tempfile.mkdtemp()
dbp, fp = os.path.join(d, "m.sqlite"), os.path.join(d, "f.csv")
db = sqlite3.connect(dbp)
db.execute("CREATE TABLE snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL, fair_value REAL, "
           "reference REAL, our_bid REAL, our_ask REAL, position REAL)")
t0 = utcnow() - timedelta(hours=1)
for k in range(40):                                        # fv 0.50 -> 0.54 over 40 min; our bid at the top half the time
    db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", (iso(t0 + timedelta(minutes=k)), "live", "11", "Rep Ohio",
               0.49, 0.52, 0.50 + 0.001 * k, None, 0.49 if k % 2 else 0.48, 0.52, 0))
db.commit(); db.close()
with open(fp, "w", newline="") as f:
    w = csv.writer(f); w.writerow(FillLogger.COLUMNS)
    w.writerow([1, iso(t0), "11", 5, "bid", 100, 0.49, 0.49, 0.50, ""])
    w.writerow([2, iso(t0), "11", 6, "?", 100, 0.49, "", "", ""])
lines = analyze(fp, dbp)
check("analyze: edge at quote (+1c), 5-min markout (+1.5c), P&L at latest fair value (+4.9), unmatched count",
      "1 matched, 1 not matched" in lines[0] and "+1.00" in lines[1] and "5m  +1.50c" in lines[1] and "+5" in lines[1], lines[:2])
check("analyze: time at the top of the book per market", "50%" in lines[3] and "Rep Ohio" in lines[3], lines[3:4])
check("analyze runs without a snapshot file", analyze(fp, "")[0].startswith("fills: 1 matched"))

print("--- handover restart (deploy without pulling quotes)")
a, b = make_bot(); b.cfg.selftest_enabled = False
real_cycle = b.cycle
n = {"k": 0}
def then_handover():
    n["k"] += 1
    real_cycle()
    if n["k"] == 2:
        b.request_handover()
b.cycle, b.cfg.loop_seconds = then_handover, 0
b.run()
resting = dict(a.orders)
check("SIGUSR1 handover: the bot exits WITHOUT cancelling, and leaves a handover note",
      len(resting) == 8 and os.path.exists(b.cfg.handover_file), (len(resting), a.calls[-3:]))
b2 = Bot(a, b.cfg)
a.calls.clear()
seen = []
real_c2 = b2.cycle
b2.cycle = lambda: (real_c2(), seen.append(set(b2.my_orders)))[0]
run_cycles(b2, 2)
calls_before_stop = a.calls[:a.calls.index(("cancel_all", None))] if ("cancel_all", None) in a.calls else a.calls
check("the next start adopts those orders: no clean-slate cancel, nothing placed twice, nothing replaced",
      not [c for c in calls_before_stop if c[0] in ("batch", "cancel_order")] and seen and set(resting) <= seen[-1]
      and not os.path.exists(b.cfg.handover_file), (calls_before_stop[:6], list(b2.my_orders)))
a, b = make_bot()
with open(b.cfg.handover_file, "w") as f:
    json.dump({"t": time.time() - 3600, "orders": 3}, f)
check("a handover note older than handover_max_age is ignored (clean slate as usual)", not b.adopt_handover())
a, b = make_bot(); b.cfg.selftest_enabled = False
n2 = {"k": 0}
real_cycle3 = b.cycle
def then_handover2():
    n2["k"] += 1
    real_cycle3()
    if n2["k"] == 1:
        b.request_handover()
b.cycle, b.cfg.loop_seconds = then_handover2, 0
b.run()
real_oo2 = a.open_orders
a.open_orders = lambda tid, eid=None: []                  # the list lags: shows none of them yet
b3 = Bot(a, b.cfg)
a.calls.clear()
b3.running = True
b3.adopt_handover()
b3.cycle()
a.open_orders = real_oo2
check("handover: orders the lagging list doesn't show yet are taken from the note, never placed twice",
      not a.sent("batch") and len(b3.my_orders) == 8, (a.sent("batch"), len(b3.my_orders)))
a, b = make_bot()
b.request_handover(); b.request_stop()
check("a normal stop after a handover request cancels as usual", b.handover is False)
a, b = make_bot()
b.handover, b.exit_code = True, EXIT_KILLED
a.orders[1] = {"id": 1, "exchangeId": "11", "side": "yes", "action": "buy", "priceLimit": 0.05, "quantity": 5, "open": True,
               "expirationDate": iso(utcnow() + timedelta(minutes=20))}
b.shutdown()
check("kill switch / fatal exits always cancel, even after a handover request", not a.orders)
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

# Hot-fix 2.1 (owner, 2 Oct 11:29): a HELD market with no fair value must not fall back to 0.5 in the risk model.
# Rep U.S. House (short 9,396) was unpriced after a restart: risk 20.6k -> 31k, reduce-only, no quotes there.
logging.disable(logging.CRITICAL)
a, b = make_bot(); b.cycle()
inv = {"11": -9396.0, "12": 8143.0}                       # short Rep Ohio, long Dem Ohio (the House shape)
priced = {"11": 0.08, "12": 0.92, "21": 0.52, "22": 0.48}
unpriced = {"11": None, "12": 0.92, "21": 0.52, "22": 0.48}
b.cur_refs, b.cur_liquid = {"11": 0.08}, {"11"}
r_full = b.settlement_risk(inv, priced, 0.0); w_full = b.total_worst_case(inv, priced)
check("risk_fv: an unpriced held leg uses its liquid Polymarket reference -> same risk as when priced at it",
      abs(b.settlement_risk(inv, unpriced, 0.0) - r_full) < 1e-6 and abs(b.total_worst_case(inv, unpriced) - w_full) < 1e-6,
      (b.settlement_risk(inv, unpriced, 0.0), r_full))
b.cur_refs, b.cur_liquid = {}, set()
check("risk_fv: ...else 1 - the other leg's fair value in a two-leg race (0.92 -> 0.08)",
      abs(b.settlement_risk(inv, unpriced, 0.0) - r_full) < 1e-6, (b.settlement_risk(inv, unpriced, 0.0), r_full))
both_unpriced = {"11": None, "12": None, "21": 0.52, "22": 0.48}
b.pos_marks = {"11": 0.0812, "12": 0.9188}
check("risk_fv: ...else the exchange's own mark of the position",
      abs(b.settlement_risk(inv, both_unpriced, 0.0) - b.settlement_risk(inv, {"11": 0.0812, "12": 0.9188, "21": 0.52, "22": 0.48}, 0.0)) < 1e-6)
b.pos_marks = {}; b.ex["11"].last_fv = b.ex["12"].last_fv = None
r_half = b.settlement_risk(inv, both_unpriced, 0.0)
check("risk_fv: ...and only then 0.5 (the old behaviour), which is far higher", r_half > 1.5 * r_full, (r_half, r_full))
check("risk_fv: the fallback is logged once per market and source", b.fv_fallback_logged.get("11", "").startswith("0.5"))
check("risk_fv: a market we do NOT hold keeps the plain fair value path (no fallback log)",
      "21" not in b.fv_fallback_logged and "22" not in b.fv_fallback_logged, b.fv_fallback_logged)
# positions read -> pos_marks
a.positions_extra = None
pos = {"positions": [{"exchangeId": "11", "quantity": -100, "currentPrice": 0.0734}, {"exchangeId": "12", "quantity": 50}]}
b.pos_marks = {}
marks = {str(p["exchangeId"]): float(next(p[k] for k in type(b).POS_PRICE_KEYS if p.get(k) is not None))
         for p in pos["positions"] if any(p.get(k) is not None for k in type(b).POS_PRICE_KEYS)} if hasattr(type(b), "POS_PRICE_KEYS") else None
check("positions read keeps each position's currentPrice as its mark", marks is None or marks == {"11": 0.0734}, marks)
logging.disable(logging.NOTSET)

# F2: the 409 fallback is bounded: positions_stale_max_cycles cycles in a row (or positions_stale_max_seconds), then
# the cycle fails (on_cycle_error then pulls quotes after max_failed_cycles); logged once at the start and the end.
class _Grab(logging.Handler):
    def __init__(s): super().__init__(); s.msgs = []
    def emit(s, r): s.msgs.append(r.getMessage())
def pos_409_run(b, n):
    """n cycles with positions answering 409 -> list of True (cycle went on) / False (cycle failed)."""
    out = []
    for _ in range(n):
        b.orders_stale = True
        try:
            b.cycle(); out.append(True)
        except ApiError as e:
            out.append(False if e.status == 409 else "other")
    return out
grab = _Grab()
real_level, real_prop = M.log.level, M.log.propagate
M.log.addHandler(grab); M.log.setLevel(logging.INFO); M.log.propagate = False
try:
    a, b = make_bot(); b.cfg.positions_stale_max_cycles = 3; b.cycle()   # (default 0: seconds only, since
    real_pos = a.positions                                                 # feed-woken cycles can be 0.5 s apart)
    a.positions = lambda: (_ for _ in ()).throw(ApiError(409, "CONFLICT", "Tournament holdings cannot be valued"))
    seq = pos_409_run(b, 6)
    check("F2: positions 409 -> the last read is reused 3 cycles in a row, then every cycle fails",
          seq == [True, True, True, False, False, False], seq)
    started = [m for m in grab.msgs if m.startswith("positions unavailable")]
    failing = [m for m in grab.msgs if m.startswith("positions still unavailable")]
    check("F2: logged once when the fallback starts and once when it starts failing (not every cycle)",
          len(started) == 1 and len(failing) == 1, grab.msgs)
    for _ in range(b.cfg.max_failed_cycles):
        b.on_cycle_error("failed cycles", pull_now=False)
    check("F2: ...and those failed cycles pull every quote (on_cycle_error)", not a.orders and b.pulled_after_errors,
          a.orders)
    a.positions = real_pos
    grab.msgs.clear()
    b.orders_stale = True; b.cycle()
    check("F2: positions readable again: the run resets and its end is logged once",
          b.pos_fallbacks == 0 and not b.pos_fallback_failing
          and len([m for m in grab.msgs if m.startswith("positions readable again")]) == 1, grab.msgs)
    a.positions = lambda: (_ for _ in ()).throw(ApiError(409, "CONFLICT", "Tournament holdings cannot be valued"))
    b.cfg.positions_stale_max_cycles = 100
    seq = pos_409_run(b, 1)
    b.pos_fallback_since -= 121                       # the run of 409s started 2 minutes ago
    seq += pos_409_run(b, 1)
    check("F2: ...or after positions_stale_max_seconds, whichever comes first", seq == [True, False], seq)
    a.positions = real_pos; b.orders_stale = True; b.cycle()
    a.positions = lambda: (_ for _ in ()).throw(ApiError(409, "CONFLICT", "Tournament holdings cannot be valued"))
    b.cfg.positions_stale_max_cycles, b.cfg.positions_stale_max_seconds = 0, 0
    seq = pos_409_run(b, 6)
    check("F2: both limits 0 = the old unbounded reuse", seq == [True] * 6, seq)
    a.positions = real_pos
finally:
    M.log.removeHandler(grab); M.log.setLevel(real_level); M.log.propagate = real_prop

# F6: a handover stop that hasn't exited within handover_exit_max_seconds exits at once (exit 0), so the deploy
# script can start the new bot; never when it stopped being a handover (plain stop, kill switch) since.
def handover_exits(setup):
    a, b = make_bot()
    exits = []
    b.hard_exit = exits.append
    b.cfg.handover_exit_max_seconds = 0.05
    setup(b)
    if b.handover_timer:
        b.handover_timer.join(2)
    return exits, b
logging.disable(logging.CRITICAL)
try:
    exits, b = handover_exits(lambda b: b.request_handover())
    check("F6: a handover still running after handover_exit_max_seconds exits at once with code 0", exits == [0], exits)
    exits, b = handover_exits(lambda b: (b.request_handover(), b.request_handover()))
    check("F6: ...one deadline per handover (a second SIGUSR1 doesn't start another)", exits == [0], exits)
    exits, b = handover_exits(lambda b: (b.request_handover(), b.request_stop()))
    check("F6: ...never after a plain stop (that one must cancel; systemd bounds it)", exits == [], exits)
    def killed(b):
        b.request_handover(); b.exit_code = M.EXIT_KILLED
    exits, b = handover_exits(killed)
    check("F6: ...never after the kill switch fired (it must cancel)", exits == [], exits)
    def no_cap(b):
        b.cfg.handover_exit_max_seconds = 0; b.request_handover()
    exits, b = handover_exits(no_cap)
    check("F6: handover_exit_max_seconds = 0: no deadline (the old behaviour)", exits == [] and b.handover_timer is None)
finally:
    logging.disable(logging.NOTSET)
check("F6: the handover deadline is a live setting with a range", "handover_exit_max_seconds" in OVERRIDABLE
      and _live.handover_exit_max_seconds == 150.0)

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
check("default write budget: 28/min flat (2 Oct live: 4 x 429 while the budget climbed to 36-50/min, 0 at 28), "
      "burst cycle trigger 60 s (normal cycles take 20-30 s)",
      Config().writes_per_minute == 28 and Config().writes_per_minute_max == 28 and Config().write_budget_cut == 0.75
      and Config().burst_cycle_seconds == 60.0)
check("the positions-409 fallback is bounded by time only by default (120 s): cycles can be 0.5 s apart",
      Config().positions_stale_max_cycles == 0 and Config().positions_stale_max_seconds == 120)
check("default batch: 10 orders (full 20-order batches were 273 of the 417 '409 in flight' failures)",
      Config().batch_size == 10)
from dataclasses import replace as _replace
for pr, pw in ((2, 4), (1, 1), (3, 8)):
    _c = _replace(CFG, parallel_requests=pr, parallel_writes=pw)
    _ad = Api(_c, False).s.get_adapter(_c.base_url)
    threads_max = pr + pw + 3                                 # + main thread, self-test, realtime token refresh
    check(f"HTTP pool covers every thread that can reach the exchange (parallel_requests {pr}, parallel_writes {pw})",
          _ad._pool_maxsize >= threads_max and _ad._pool_connections >= 2 and not _ad._pool_block,
          (_ad._pool_maxsize, threads_max))
order_api = Api(CFG, False)
order_api.gap, order_api.budget, order_api.wbudget, order_api.BUDGET_WINDOW = 0.0, 100, 1, 1.0
order_api.throttle(write=True)
held = threading.Thread(target=lambda: order_api.throttle(write=True)); held.start()   # waits ~1 s for the write budget
time.sleep(0.05)
t0 = time.monotonic(); order_api.throttle(); order_api.throttle(); read_wait = time.monotonic() - t0
check("a write held by the write budget keeps the request window sorted; reads behind it don't wait",
      list(order_api._window) == sorted(order_api._window) and read_wait < 0.2, (list(order_api._window), read_wait))
held.join()
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

# A 429 on a READ (GETs are the more frequent requests, so a per-key limit hits them first) also cuts a write
# budget that has grown above its start value; a budget at or below the start value is left alone.
for start_w, want in ((50.0, 37.5), (45.0, 45.0)):
    answers = [Resp(429, {"Retry-After": "0.05"}), Resp(200)]
    read_api = Api(__import__("dataclasses").replace(CFG, writes_per_minute=45, writes_per_minute_max=50), False); read_api.wbudget = start_w
    read_api.s.request = lambda *a, **k: answers.pop(0)
    read_api.call("GET", "/x")
    check(f"a read 429 with the write budget at {start_w:g}: budget now {want:g} (cut only above its start value)",
          abs(read_api.wbudget - want) < 1e-9, read_api.wbudget)

# Write budget AIMD: +1 per 60 successful writes up to writes_per_minute_max; a 429 on a write cuts it.
from dataclasses import replace as _replace
aimd_api = Api(_replace(CFG, writes_per_minute=45, writes_per_minute_max=60, write_budget_cut=0.75), False)
aimd_api.gap, aimd_api.BUDGET_WINDOW = 0.0, 0.001
aimd_api.s.request = lambda *a, **k: Resp(200)
for _ in range(60):
    aimd_api.call("POST", "/orders/batch", body={})
check("write budget grows +1 per 60 successful writes (45 -> 46)", abs(aimd_api.wbudget - 46) < 1e-6, aimd_api.wbudget)
aimd_api.wbudget = 59.99
for _ in range(5):
    aimd_api.call("POST", "/orders/batch", body={})
check("...never above writes_per_minute_max (60)", aimd_api.wbudget == 60, aimd_api.wbudget)
aimd_api.call("GET", "/x")
check("...reads don't grow the write budget", aimd_api.wbudget == 60)
answers = [Resp(429, {"Retry-After": "0"}), Resp(200)]
aimd_api.s.request = lambda *a, **k: answers.pop(0)
aimd_api._next_start = 0.0
aimd_api.call("POST", "/orders/batch", body={})
check("a 429 on a write cuts the write budget by write_budget_cut (60 -> 45, then +1/60 for the retry)",
      abs(aimd_api.wbudget - (45 + 1 / 60)) < 1e-6, aimd_api.wbudget)
flat_api = Api(_replace(CFG, writes_per_minute=30, writes_per_minute_max=30), False)
flat_api.gap, flat_api.BUDGET_WINDOW = 0.0, 0.001
flat_api.s.request = lambda *a, **k: Resp(200)
for _ in range(120):
    flat_api.call("DELETE", "/orders/1")
check("writes_per_minute_max = writes_per_minute: the budget never grows (old behaviour)", flat_api.wbudget == 30,
      flat_api.wbudget)

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

# Plain-text errors ({"error": "Not found"}, the 2026-10-02 outage): an ApiError carrying the text, never a crash.
class R2:
    def __init__(s, code, payload): s.status_code, s.payload, s.headers = code, payload, {}; s.text = json.dumps(payload); s.content = s.text.encode()
    def json(s): return s.payload
real_api.s.request = lambda *a, **k: R2(404, {"error": "Not found"})
try:
    real_api.call("GET", "/x"); ok = False
except ApiError as e:
    ok = e.status == 404 and "Not found" in str(e)
check("plain-text error {'error': 'Not found'} -> ApiError with its text, never a crash", ok)

# One phone alert per outage, however long (it used to repeat every cycle while cancel-all failed), one when it ends.
sent = []
real_alert, M.alert = M.alert, (lambda m: sent.append(m))
try:
    a, b = make_bot()
    def cancel_fails(): raise ApiError(0, "NETWORK", "down")
    b.cancel_everything = cancel_fails
    for _ in range(5):
        b.on_cycle_error("unexpected error", pull_now=True)
    n_outage = len(sent)
    b.on_cycle_ok()
    n_back = len(sent) - n_outage
    b.on_cycle_ok()
    n_quiet = len(sent) - n_outage - n_back
finally:
    M.alert = real_alert
check("an outage with a failing cancel-all alerts once, not every cycle", n_outage == 1, sent)
check("recovery alerts once, and only after an alerted outage", n_back == 1 and n_quiet == 0, sent)

# F3a: reduce-only hysteresis. Enter above max_worst_case_frac (30%), leave only below 30% - 3% (also the backstop).
def reduce_seq(b, values, model="correlated"):
    """Cycles with (settlement risk, sum-of-maxima worst case) forced -> b.global_reduce after each."""
    out = []
    b.cfg.risk_model = model
    for risk, worst in values:
        b.settlement_risk = lambda inv, fvs, pd, r=risk: r
        b.total_worst_case = lambda inv, fvs, w=worst: w
        b.cycle(); out.append(b.global_reduce)
    return out
logging.disable(logging.CRITICAL)
try:
    a, b = make_bot(); b.cycle()                          # account value 100,000
    seq = reduce_seq(b, [(31e3, 50e3), (28e3, 50e3), (27.5e3, 50e3), (26.9e3, 50e3), (29.9e3, 50e3), (30.1e3, 50e3)])
    check("F3: reduce-only enters above 30% and leaves only below 27% (no flapping around 30%)",
          seq == [True, True, True, False, False, True], seq)
    a, b = make_bot(); b.cycle()
    seq = reduce_seq(b, [(10e3, 70e3), (10e3, 67e3), (10e3, 65.9e3)])
    check("F3: ...the 69% backstop has the same hysteresis (leaves below 66%)", seq == [True, True, False], seq)
    a, b = make_bot(); b.cycle()
    seq = reduce_seq(b, [(31e3, 31e3), (28e3, 28e3), (26e3, 26e3)], model="sum_max")
    check("F3: ...and so does the sum_max risk model", seq == [True, True, False], seq)
    a, b = make_bot(); b.cycle(); b.cfg.reduce_only_hysteresis = 0.0
    seq = reduce_seq(b, [(31e3, 50e3), (29.9e3, 50e3)])
    check("F3: reduce_only_hysteresis = 0: the old single threshold", seq == [True, False], seq)
    a, b = make_bot(); b.cycle(); b.cfg.max_worst_case_frac = 0.05; b.cfg.reduce_only_hysteresis = 0.1
    seq = reduce_seq(b, [(6e3, 6e3), (2e3, 2e3)])
    check("F3: a hysteresis bigger than the cap can't trap the bot in reduce-only", seq == [True, False], seq)
finally:
    logging.disable(logging.NOTSET)

# F3b: many pulls in one cycle -> ONE tournament-wide cancel-all (1 write) instead of a DELETE each.
def pull_storm_bot(**cfg):
    a, b = make_bot(); b.cycle()                          # 8 quotes on 4 markets, no positions
    for k, v in cfg.items():
        setattr(b.cfg, k, v)
    a.calls.clear()
    b.settlement_risk = lambda inv, fvs, pd: 40e3         # reduce-only with no position: every quote is pulled
    b.total_worst_case = lambda inv, fvs: 50e3
    return a, b
logging.disable(logging.CRITICAL)
try:
    a, b = pull_storm_bot(pulls_cancel_all_over=2)
    b.cycle()
    check("F3: more pulls than pulls_cancel_all_over: one tournament-wide cancel-all, no per-exchange cancels",
          a.sent("cancel_all") == [("cancel_all", None)] and not a.sent("cancel_order") and not a.orders
          and not b.my_orders and not a.sent("batch"), a.calls)
    b.settlement_risk = lambda inv, fvs, pd: 0.0
    b.cycle()
    check("F3: ...and the next cycle re-places what should rest, in one batch", len(a.orders) == 8
          and len(a.sent("batch")) == 1, (len(a.orders), a.calls))
    a, b = pull_storm_bot(pulls_cancel_all_over=0)
    b.cycle()
    check("F3: pulls_cancel_all_over = 0: the pulls go one by one, as before",
          len(a.sent("cancel_all")) == 4 and all(c[1] for c in a.sent("cancel_all")) and not a.orders, a.calls)
    a, b = pull_storm_bot()
    a.writes_left = lambda: 2
    b.cycle()
    check("F3: a drained write budget alone never triggers the cancel-all (re-placement would wait on it too)",
          len(a.sent("cancel_all")) == 4 and all(c[1] for c in a.sent("cancel_all")), a.calls)
    a, b = pull_storm_bot(pulls_cancel_all_over=2)
    b.burst, b.burst_set, b.burst_cfg = True, set(), b.cfg
    b.update_burst = lambda now_m: None
    b.cycle()
    check("F3: never in burst mode (only the top markets would be re-quoted: the rest would stay empty)",
          len(a.sent("cancel_all")) == 4 and all(c[1] for c in a.sent("cancel_all")), a.calls)
    a, b = pull_storm_bot(pulls_cancel_all_over=2)
    b.selftest_future = object()                          # the self-test is running on its own thread
    b.cycle()
    check("F3: never while the self-test runs (its orders would vanish under it)",
          ("cancel_all", None) not in a.sent("cancel_all") and not a.orders, a.calls)
    a, b = pull_storm_bot(pulls_cancel_all_over=2)
    real_ca = a.cancel_all
    def ca_fails(tid, eid=None):
        if eid is None:
            a.log("cancel_all", None); raise ApiError(0, "NETWORK", "timeout")
        return real_ca(tid, eid)
    a.cancel_all = ca_fails
    b.cycle()
    check("F3: a cancel-all that fails falls back to the pulls themselves (they reduce risk)",
          not a.orders and len(a.sent("cancel_all")) == 5, a.calls)
    a, b = pull_storm_bot(pulls_cancel_all_over=2)
    gen = b.cancel_gen
    def ca_partial(tid, eid=None):
        a.log("cancel_all", eid); return False            # 207 and the list still shows orders
    a.cancel_all = ca_partial
    b.cycle()
    check("F3: a cancel-all not confirmed forgets nothing (re-read next cycle), never stacks quotes",
          len(b.my_orders) == 8 and b.orders_stale and not a.sent("batch") and b.cancel_gen == gen + 1, a.calls)
finally:
    logging.disable(logging.NOTSET)

# F11: a partial cancel-all (207, orders left) must not count as "pulled": the next failed cycle tries again.
a, b = make_bot()
answers, calls = [False, False, True], []
b.cancel_everything = lambda: (calls.append(1), answers.pop(0))[1]
real_alert, M.alert = M.alert, (lambda m: None)
logging.disable(logging.CRITICAL)
try:
    b.on_cycle_error("unexpected error", pull_now=True)
    first = b.pulled_after_errors
    b.on_cycle_error("unexpected error", pull_now=True)
    b.on_cycle_error("unexpected error", pull_now=True)
    b.on_cycle_error("unexpected error", pull_now=True)
finally:
    logging.disable(logging.NOTSET)
    M.alert = real_alert
check("F11: a cancel-all that left orders resting is retried each failed cycle until it reports none left",
      first is False and len(calls) == 3 and b.pulled_after_errors and not answers, (first, calls))

# ---------------------------------------------------------------------------------------------
# Inventory turnover (2 Oct): race-netted limits, age skew, capital ceiling
# ---------------------------------------------------------------------------------------------
print("--- inventory turnover: race-netted limits, age skew, capital ceiling")
_c = Config(); pin_test_sizes(_c)
# House on 2 Oct: long 10,834 Dem, short 9,396 Rep -> race-netted +20,230 Dem / -20,230 Rep; 10k per-leg limit.
_dem = compute_quote(0.55, 10834, 20230, None, None, _c, order_size=5000, position_limit=10000, net_inv=20230)
_rep = compute_quote(0.45, -9396, -20230, None, None, _c, order_size=5000, position_limit=10000, net_inv=-20230)
check("netting: House Dem leg (+10,834, race +20,230): no bid, the ask (reducing) still quotes",
      _dem.bid is None and _dem.ask_size > 0, _dem)
check("netting: House Rep leg (-9,396, race -20,230): no ask (was 604 shares per-leg), the bid still quotes",
      _rep.ask is None and _rep.bid_size > 0, _rep)
_c.limits_use_race_net = False
_old = compute_quote(0.45, -9396, -20230, None, None, _c, order_size=5000, position_limit=10000, net_inv=-20230)
check("netting off: the per-leg limit alone let the Rep ask add 604 more shares of the same bet",
      _old.ask_size == 604, _old)
_c.limits_use_race_net = True
_d2 = compute_quote(0.55, 8000, 17396, None, None, _c, order_size=5000, position_limit=10000, net_inv=17396)
check("netting: Dem +8,000 under its own 10k limit but race +17,396 -> no bid", _d2.bid is None and _d2.ask_size > 0, _d2)
_h = compute_quote(0.55, 2000, -6000, None, None, _c, order_size=5000, position_limit=10000, net_inv=-6000)
check("netting: a leg that hedges the race (+2,000, race -6,000) keeps buying; its ask (growing the race short) "
      "is capped at 4,000", _h.bid_size == 5000 and _h.ask_size == 4000, _h)
_k = compute_quote(0.50, 100, 5000, None, None, _c, order_size=500, kelly_p=0.52, net_inv=5000)
_k0 = compute_quote(0.50, 100, 100, None, None, _c, order_size=500, kelly_p=0.52, net_inv=100)
check("netting: the Kelly limit applies to the race-netted position too", _k.bid is None and _k0.bid_size > 0, (_k, _k0))

# Age skew: 0.25c per hour beyond 1 h, capped at 2c, toward unloading; never through fair value.
check("age skew: none up to skew_age_after_hours", age_skew(1.0, 500, _c) == 0.0 and age_skew(0.5, 500, _c) == 0.0)
check("age skew: 3 h long -> 0.5c lower; short -> 0.5c higher",
      abs(age_skew(3.0, 500, _c) - 0.005) < 1e-12 and abs(age_skew(3.0, -500, _c) + 0.005) < 1e-12)
check("age skew: capped at skew_age_max (2c) from 9 h", age_skew(9.0, 1, _c) == 0.02 and age_skew(40.0, 1, _c) == 0.02)
check("age skew: flat position -> none", age_skew(10.0, 0, _c) == 0.0)
_q0 = compute_quote(0.50, 50, 50, None, None, _c, order_size=100)
_q8 = compute_quote(0.50, 50, 50, None, None, _c, order_size=100, age_hours=5.0)
check("age skew: a 5 h-old long quotes 1c lower on both sides", (round(_q0.bid - _q8.bid, 6), round(_q0.ask - _q8.ask, 6)) == (0.01, 0.01),
      (_q0, _q8))
_qb = compute_quote(0.50, 400, 400, None, None, _c, order_size=100, age_hours=30.0)
check("age skew: added after the inventory cap (2c + 2c = 4c lower bid), ask never below fair value",
      _qb.bid == 0.42 and _qb.ask == 0.50, _qb)
_qs = compute_quote(0.50, -400, -400, 0.52, None, _c, order_size=100, age_hours=30.0)
check("age skew: an old short bids at most fair value (would penny 0.525; the joining reducing side sits 0.5c under fair)",
      _qs.bid <= 0.50 + 1e-9 and _qs.bid >= 0.495 - 1e-9, _qs)
_c.skew_age_enabled = False
check("age skew disabled = today's quote", compute_quote(0.50, 50, 50, None, None, _c, order_size=100, age_hours=30.0) == _q0)
_c.skew_age_enabled = True

# Capital ceiling in compute_quote: the side growing |race-netted| shrinks by the factor; reduce-only stays stricter.
_l = compute_quote(0.50, 300, 300, None, None, _c, order_size=100, adding_factor=0.0)
check("ceiling x0: long -> adding bid withdrawn, reducing ask kept", _l.bid is None and _l.ask_size == 100, _l)
_s = compute_quote(0.50, -300, -300, None, None, _c, order_size=100, adding_factor=0.5)
check("ceiling x0.5: short -> adding ask halved, reducing bid full", _s.ask_size == 50 and _s.bid_size == 100, _s)
_f = compute_quote(0.50, 0, 0, None, None, _c, order_size=100, adding_factor=0.0)
check("ceiling x0: a flat market quotes neither side (both would add)", _f == NO_QUOTE or (_f.bid is None and _f.ask is None), _f)
_r = compute_quote(0.50, 300, 300, None, None, _c, order_size=100, adding_factor=0.5, reduce_only=True)
check("ceiling with reduce-only: reduce-only stays stricter (no bid)", _r.bid is None and _r.ask_size == 100, _r)

# FIFO lots on a bot: buys stack, sells take the oldest first, a sign change starts afresh.
logging.disable(logging.CRITICAL)
try:
    a, b = make_bot()
    t0 = 1_700_000_000.0
    b.lots_seeded = True
    b.update_lots({"11": 100}, t0)
    b.update_lots({"11": 300}, t0 + 3600)
    check("lots: two buys -> two lots", b.lots["11"] == [[100.0, t0], [200.0, t0 + 3600]], b.lots)
    check("lots: share-weighted age (100 @ 2 h + 200 @ 1 h = 1.33 h)",
          abs(b.age_hours(b.ex["11"], t0 + 7200) - 4 / 3) < 1e-9, b.age_hours(b.ex["11"], t0 + 7200))
    b.update_lots({"11": 250}, t0 + 7200)
    check("lots: a partial sell takes the oldest lot first", b.lots["11"] == [[50.0, t0], [200.0, t0 + 3600]], b.lots)
    b.update_lots({"11": 150}, t0 + 7200)
    check("lots: ...then the next one", b.lots["11"] == [[150.0, t0 + 3600]], b.lots)
    b.update_lots({"11": -50}, t0 + 9000)
    check("lots: crossing to short starts one fresh lot", b.lots["11"] == [[-50.0, t0 + 9000]], b.lots)
    b.update_lots({"11": -80}, t0 + 9900)
    b.update_lots({"11": -60}, t0 + 9900)
    check("lots: shorts work the same way", b.lots["11"] == [[-30.0, t0 + 9000], [-30.0, t0 + 9900]], b.lots)
    b.update_lots({"11": 0}, t0 + 9999)
    check("lots: flat -> forgotten, age 0", "11" not in b.lots and b.age_hours(b.ex["11"]) == 0.0, b.lots)
    b.update_lots({"11": 400, "21": -200}, t0)
    b.update_lots({"11": 400, "21": -200}, t0)   # unchanged position: nothing new
    age, n3, n12 = b.portfolio_age(t0 + 5 * 3600)
    check("portfolio age: share-weighted 5 h, 2 positions over 3 h, none over 12 h",
          abs(age - 5) < 1e-9 and (n3, n12) == (2, 0), (age, n3, n12))
    b2 = Bot(a, b.cfg)
    check("lots persist across a restart (position_lots_file)", b2.lots == b.lots, (b2.lots, b.lots))
    b2.update_lots({"11": 400, "21": -200}, t0 + 60)
    check("...and a restart with unchanged positions keeps their ages", b2.lots["11"] == [[400.0, t0]], b2.lots)

    # No lots file (first deploy): rebuilt from fills.csv, latest fills in the position's direction first.
    a, b = make_bot()
    with open(b.cfg.fills_csv, "a", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([1, "2026-10-02T00:00:00Z", "11", 9, "bid", 300, 0.1, 0.1, 0.12, ""])
        w.writerow([2, "2026-10-02T02:00:00Z", "11", 9, "ask", 100, 0.2, 0.2, 0.12, ""])
        w.writerow([3, "2026-10-02T04:00:00Z", "11", 9, "bid", 200, 0.1, 0.1, 0.12, ""])
        w.writerow([4, "2026-10-02T05:00:00Z", "21", 9, "bid", 100, 0.5, 0.5, 0.5, ""])
    tnow = M.parse_ts("2026-10-02T06:00:00Z").timestamp()
    b.update_lots({"11": 350, "21": -40, "22": 70}, tnow)
    t4 = M.parse_ts("2026-10-02T04:00:00Z").timestamp(); t00 = M.parse_ts("2026-10-02T00:00:00Z").timestamp()
    check("seeding from fills.csv: 350 long = the latest 200 (04:00) + 150 of the 00:00 buy",
          b.lots["11"] == [[150.0, t00], [200.0, t4]], b.lots.get("11"))
    check("seeding: a position with no fills in its direction counts as bought now",
          b.lots["21"] == [[-40.0, tnow]] and b.lots["22"] == [[70.0, tnow]], (b.lots.get("21"), b.lots.get("22")))

    # Capital ceiling on/off with hysteresis (75% in, below 70% out), logged once each way.
    a, b = make_bot()
    seq = []
    for frac in (0.70, 0.76, 0.72, 0.705, 0.69, 0.751, None):
        b.update_capital_ceiling(frac, b.cfg); seq.append(b.capital_over)
    check("capital ceiling: on above 75%, off only below 70%, unknown keeps the state",
          seq == [False, True, True, True, False, True, True], seq)
    b.cfg.capital_in_positions_max_frac = 0.0
    b.update_capital_ceiling(0.99, b.cfg)
    check("capital ceiling: 0 = off", b.capital_over is False)
    check("capital in positions: our own valuation without the API's (long 1,000 @0.3 + short 1,000 @0.3 = 1,000)",
          abs(b.capital_in_positions({"summary": {}}, {"11": 1000, "21": -1000}, {"11": 0.3, "21": 0.3}) - 1000) < 1e-6)
    check("capital in positions: the positions read's totalMarketValue when given",
          b.capital_in_positions({"summary": {"totalMarketValue": 90500}}, {"11": 1}, {}) == 90500)

    # End to end: over the ceiling, adding sides go, reducing sides stay; status and summary show it.
    check("capital ceiling default: adding sides at a quarter size, not withdrawn (the account was 90% in positions on "
          "2 Oct, so the ceiling is on at deploy: factor 0 would have blacked out every flat market)",
          Config().capital_ceiling_adding_size_factor == 0.5)
    a, b = make_bot(); b.cfg.capital_ceiling_adding_size_factor = 0.0
    a.inv = {"11": 500}                                   # long 500 Rep Ohio -> race +500 Rep / -500 Dem
    b.cfg.capital_in_positions_max_frac = 0.0005          # 500 x ~0.14 = ~70 of 100,000 -> over
    b.cycle()
    q11, q12, q21 = b.ex["11"].quote, b.ex["12"].quote, b.ex["21"].quote
    check("ceiling live: Rep Ohio (long) keeps its ask, loses its bid", q11.bid is None and q11.ask is not None, q11)
    check("ceiling live: Dem Ohio (race-short) keeps its bid (the hedge), loses its ask", q12.ask is None and q12.bid is not None, q12)
    check("ceiling live: a flat market quotes nothing at factor 0", q21.bid is None and q21.ask is None, q21)
    b.write_status(True)
    st = json.load(open(b.cfg.status_file))
    check("status.json: capital in positions, ceiling flag, portfolio age and old-position counts",
          st.get("capital_ceiling_active") is True and st.get("capital_in_positions_frac", 0) > 0.0005
          and "portfolio_age_hours" in st and st.get("positions_over_3h") == 0 and "positions_over_12h" in st, st)
    _t, _m = build_summary(FakeApi(), "/nonexistent/fills.csv", 100_000, value=100_000, health=b.health)
    check("summary: a positions line with age, old counts and the ceiling",
          "Positions: avg age" in _m and "CEILING" in _m and all(len(x) < 160 for x in _m.split("\n")), _m)
    b.cfg.capital_ceiling_adding_size_factor = 0.5
    b.cycle()
    check("ceiling live x0.5: the flat market quotes both sides at half size",
          b.ex["21"].quote.bid_size == 50 and b.ex["21"].quote.ask_size == 50, b.ex["21"].quote)
    b.cfg.capital_in_positions_max_frac = 0.75
    b.cycle()
    check("ceiling left: full sizes again", b.ex["21"].quote.bid_size == 100 and not b.capital_over, b.ex["21"].quote)
finally:
    logging.disable(logging.NOTSET)

print("--- rival-floor map (market_edge.json: a per-market min_edge, off by default)")
check("market edge: off by default, 600 s reload, 2c cap, live-overridable",
      _live.market_edge_enabled is True and _live.market_edge_file == "market_edge.json"
      and _live.market_edge_reload_seconds == 600.0 and _live.market_edge_max == 0.02
      and "market_edge_enabled" in OVERRIDABLE and "market_edge_max" in OVERRIDABLE
      and "market_edge_file" not in OVERRIDABLE)
_known = {"11": 1, "12": 1, "21": 1, "22": 1}
_g, _b = validate_market_edge({"_meta": {"source": "books"}, "11": {"label": "x", "min_edge": 0.015}, "12": 0.02,
                               "99": {"min_edge": 0.015}, "21": {"min_edge": 0.2}, "22": {"min_edge": True}}, _known)
check("market edge file: entries and bare numbers accepted, metadata skipped",
      _g == {"11": 0.015, "12": 0.02}, _g)
check("market edge file: unknown ids, out-of-range and non-numeric values refused",
      len(_b) == 3 and any("99" in x for x in _b) and any("21" in x for x in _b) and any("22" in x for x in _b), _b)
check("market edge file: not an object -> nothing", validate_market_edge([1, 2], _known)[0] == {})
check("market edge range edges: 0.5c and 5c accepted, 0.4c and 5.5c not",
      validate_market_edge({"11": 0.005, "12": 0.05, "21": 0.004, "22": 0.055}, _known)[0] == {"11": 0.005, "12": 0.05})

a, b = make_bot()
_me = b.cfg.market_edge_file
check("tests keep market_edge.json in their temp dir", os.path.dirname(_me) == os.path.dirname(b.cfg.overrides_file), _me)
with open(_me, "w") as f:
    json.dump({"11": {"min_edge": 0.015}, "21": {"min_edge": 0.02}, "99": {"min_edge": 0.015}}, f)
_logs = []
_h = logging.Handler(); _h.emit = lambda r: _logs.append(r.getMessage())
M.log.addHandler(_h); _lvl = M.log.level; M.log.setLevel(logging.INFO); M.log.propagate = False
try:
    b.check_market_edge()
    check("market edge: loaded (unknown id dropped), one INFO line saying so",
          b.market_edge == {"11": 0.015, "21": 0.02} and len(_logs) == 1 and "loaded: 2 markets" in _logs[0]
          and "99" in _logs[0], (b.market_edge, _logs))
    with open(_me, "w") as f:
        json.dump({"11": {"min_edge": 0.01}}, f)
    os.utime(_me, (time.time() + 5, time.time() + 5))
    b.check_market_edge()
    check("market edge: not re-read before market_edge_reload_seconds", b.market_edge == {"11": 0.015, "21": 0.02})
    b.last_market_edge_check -= b.cfg.market_edge_reload_seconds + 1
    b.check_market_edge()
    check("market edge: re-read after market_edge_reload_seconds", b.market_edge == {"11": 0.01}, b.market_edge)
    _logs.clear()
    b.last_market_edge_check -= b.cfg.market_edge_reload_seconds + 1
    b.check_market_edge()
    check("market edge: an unchanged file is not re-read or logged", b.market_edge == {"11": 0.01} and not _logs, _logs)
    with open(_me, "w") as f:
        json.dump({"11": {"min_edge": 0.5}, "77": 0.01}, f)
    os.utime(_me, (time.time() + 10, time.time() + 10))
    b.check_market_edge(force=True)
    check("market edge: a file with nothing valid is refused (current map kept), one INFO line",
          b.market_edge == {"11": 0.01} and len(_logs) == 1 and "refused" in _logs[0], (b.market_edge, _logs))
    with open(_me, "w") as f:
        f.write("{broken")
    os.utime(_me, (time.time() + 15, time.time() + 15))
    _logs.clear(); b.check_market_edge(force=True)
    check("market edge: an unreadable file is refused (current map kept)",
          b.market_edge == {"11": 0.01} and len(_logs) == 1 and "refused" in _logs[0], _logs)
    os.remove(_me)
    _logs.clear(); b.check_market_edge(force=True)
    check("market edge: a removed file empties the map", b.market_edge == {} and len(_logs) == 1, _logs)
finally:
    M.log.removeHandler(_h); M.log.setLevel(_lvl); M.log.propagate = True

# decide: a tight house (0.51 / 0.53 around 0.52) makes min_edge the binding limit.
a, b = make_bot(books={"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
                       "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
                       "21": {"bids": [lvl(0.515, 1000)], "asks": [lvl(0.525, 1000)]},
                       "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}})
b.cycle()
ex21 = b.ex["21"]
dq = lambda: b.decide(ex21, 0.52, {}, {}, False, 0, time.monotonic())
b.cfg.market_edge_enabled = False                        # ON by default since Package 3: test both paths explicitly
q_base = dq()
b.market_edge = {"21": 0.015}
q_off = dq()
b.cfg.market_edge_enabled = True
q_on = dq()
check("decide: market edge ignored while market_edge_enabled is off", (q_off.bid, q_off.ask) == (q_base.bid, q_base.ask),
      (q_off, q_base))
check("decide: base quote 1c from fair value (min_edge binds)", (q_base.bid, q_base.ask) == (0.51, 0.53), q_base)
check("decide: enabled -> the market's own 1.5c edge", (q_on.bid, q_on.ask) == (0.505, 0.535), q_on)
b.market_edge = {"21": 0.05}
q_cap = dq()
check("decide: the market's edge is capped at market_edge_max (2c)", (q_cap.bid, q_cap.ask) == (0.50, 0.54), q_cap)
b.cfg.market_edge_max = 0.03
q_cap3 = dq()
check("decide: ...a higher cap lets the 5c entry through up to it", (q_cap3.bid, q_cap3.ask) == (0.49, 0.55), q_cap3)
b.cfg.market_edge_max = 0.02
b.market_edge = {"21": 0.005}
check("decide: an entry below min_edge never narrows it", (dq().bid, dq().ask) == (0.51, 0.53), dq())
b.market_edge = {"22": 0.02}
check("decide: a market with no entry keeps min_edge", (dq().bid, dq().ask) == (0.51, 0.53), dq())
b.ref_only = {"21"}
b.cfg.ref_only_min_edge = 0.015
b.market_edge = {"21": 0.02}
q_ro = dq()
check("decide: ref_only market, entry wider than ref_only_min_edge -> the entry", (q_ro.bid, q_ro.ask) == (0.50, 0.54), q_ro)
b.market_edge = {"21": 0.01}
q_ro2 = dq()
check("decide: ref_only market, narrower entry -> ref_only_min_edge kept", (q_ro2.bid, q_ro2.ask) == (0.505, 0.535), q_ro2)
b.ref_only = set()
b.market_edge = {"21": 0.015, "11": 0.015}
b.cycle()
b.write_status(True)
_st = json.load(open(b.cfg.status_file))
check("status.json: count of markets with their own edge", _st.get("market_edge_markets") == 2, _st.get("market_edge_markets"))
b.cfg.market_edge_enabled = False
b.cycle(); b.write_status(True)
check("status.json: 0 while disabled", json.load(open(b.cfg.status_file)).get("market_edge_markets") == 0)

# analysis/rival_floor.py on small synthetic databases (books and snapshots variants) + the analyze table
sys.path.insert(0, os.path.join(M.HERE, "analysis"))
import rival_floor as RF   # noqa: E402
check("rival floor: recommend clips to 1-2c and rounds half up to the 0.5c grid",
      [RF.recommend(x) for x in (0.0, 0.005, 0.0075, 0.01, 0.0125, 0.015, 0.03)]
      == [0.01, 0.01, 0.01, 0.015, 0.015, 0.02, 0.02])
_d = tempfile.mkdtemp()
_db = os.path.join(_d, "md.sqlite")
_c = sqlite3.connect(_db)
_c.execute("CREATE TABLE snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL, "
           "fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)")
_t0 = 1_790_000_000.0
_iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
for i in range(40):
    t = _iso(_t0 + 60 * i)
    # A: rivals 0.5c from 0.50 with the bid moving every other minute; we never quote
    _c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (t, "live", "A", "Mkt A", 0.495 - 0.005 * (i % 2), 0.505, 0.50, None, None, None, 0))
    # B: rivals 1.5c away, but our bid sits at the best (0.49 = ours): only the ask side counts
    _c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (t, "live", "B", "Mkt B", 0.49, 0.515, 0.50, None, 0.49, 0.53, 0))
    if i < 5:   # C: too few samples
        _c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (t, "live", "C", "Mkt C", 0.40, 0.60, 0.50, None, None, None, 0))
_c.commit()
_src, _st = RF.rival_floor(_c, min_samples=30)
check("rival floor (snapshots): source, half-spreads, change rate, min_edge, too-few-samples dropped",
      _src == "snapshots" and set(_st) == {"A", "B"} and _st["A"]["rival_half_spread_c"] == 0.5
      and _st["A"]["min_edge_follow"] == 0.01 and abs(_st["A"]["top_change_rate"] - 1.0) < 1e-9
      and _st["B"]["rival_half_spread_c"] == 1.5 and _st["B"]["min_edge_follow"] == 0.02 and _st["B"]["top_change_rate"] == 0
      and _st["A"]["label"] == "Mkt A" and _st["A"]["samples"] == 40, _st)
check("rival floor modes: sweep_only (default) is 2c on tight books, 1c on wide ones; follow the reverse",
      _st["A"]["min_edge_sweep_only"] == _st["A"]["min_edge"] == 0.02 and _st["B"]["min_edge_sweep_only"] == 0.01
      and RF.rival_floor(_c, min_samples=30, mode="follow")[1]["A"]["min_edge"] == 0.01
      and RF.rival_floor(_c, min_samples=30, mode="follow")[1]["B"]["min_edge"] == 0.02, _st)
check("rival floor sweep_only bands: <0.75c -> 2c, 0.75-1.25c -> 1.5c, >=1.25c -> 1c",
      [RF.recommend_sweep_only(x) for x in (0.0, 0.005, 0.0074, 0.0075, 0.01, 0.0124, 0.0125, 0.03)]
      == [0.02, 0.02, 0.02, 0.015, 0.015, 0.015, 0.01, 0.01])
_c.execute("CREATE TABLE books (ts REAL, eid TEXT, bids TEXT, asks TEXT)")
# A: one row per change: 0.49/0.51 for 40 min, then 0.48/0.52 for 20 min (one change in 60 intervals)
_c.execute("INSERT INTO books VALUES (?,?,?,?)", (_t0, "A", "[[0.49, 100]]", "[[0.51, 100]]"))
_c.execute("INSERT INTO books VALUES (?,?,?,?)", (_t0 + 2400, "A", "[[0.48, 100]]", "[[0.52, 100]]"))
_c.execute("INSERT INTO books VALUES (?,?,?,?)", (_t0 + 3600, "A", "[[0.48, 50]]", "[[0.52, 100]]"))
_c.commit()
_src, _st = RF.rival_floor(_c, min_samples=30)
check("rival floor (books): preferred over snapshots, sampled every 60 s, fair value from the snapshots",
      _src == "books" and set(_st) == {"A"} and _st["A"]["samples"] == 61 and _st["A"]["label"] == "Mkt A"
      and abs(_st["A"]["top_change_rate"] - 1 / 60) < 1e-3 and _st["A"]["min_edge"] == 0.015
      and _st["A"]["min_edge_follow"] == _st["A"]["min_edge_sweep_only"] == 0.015, _st)
_out = os.path.join(_d, "market_edge.json")
import io, contextlib   # noqa: E401,E402
with contextlib.redirect_stdout(io.StringIO()):
    RF.main([_db, "-o", _out, "--min-samples", "30"])
_j = json.load(open(_out))
check("rival floor: writes market_edge.json the bot accepts, mode in _meta, both recommendations kept",
      _j["_meta"]["source"] == "books" and _j["_meta"]["mode"] == "sweep_only"
      and validate_market_edge(_j, {"A": 1})[0] == {"A": 0.015}
      and {"min_edge_follow", "min_edge_sweep_only"} <= set(_j["A"]), _j)
with contextlib.redirect_stdout(io.StringIO()):
    RF.main([_db, "-o", _out, "--mode", "follow"])
check("rival floor: --mode follow is written to _meta", json.load(open(_out))["_meta"]["mode"] == "follow")
_c.close()
_lines = rival_floor_lines(_db, None, 5)
check("analyze: a rival-floor table from the books table",
      len(_lines) == 3 and "rival floor (books, 1 markets)" in _lines[0] and _lines[2].startswith("Mkt A"), _lines)
check("analyze: no rival-floor table without a database", rival_floor_lines("", None) == [])

# =============================================================================================
# R3 RESTING DEPTH LADDER
# =============================================================================================
print("--- R3 resting depth ladder")
logging.disable(logging.WARNING)
try:
    L3 = (0.015, 0.025, 0.035)
    _q = Quote(0.51, 100, 0.53, 100)
    _big = {True: lambda p: 10 ** 6, False: lambda p: 10 ** 6}
    want, allowed = ladder_levels(0.52, _q, 0.51, 0.53, L3, (1, 2, 3), 100, _big)
    check("ladder: levels at anchor -/+ 1.5/2.5/3.5c with 1/2/3 x the quote size",
          want == {(True, 1): (0.505, 100), (True, 2): (0.495, 200), (True, 3): (0.485, 300),
                   (False, 1): (0.535, 100), (False, 2): (0.545, 200), (False, 3): (0.555, 300)}, want)
    want, _ = ladder_levels(0.52, Quote(0.50, 100, 0.545, 100), 0.49, 0.55, L3, (1, 2, 3), 100, _big)
    check("ladder: never at or inside level 0 (bid L1 0.505 > level 0 0.50; ask L2 0.545 = level 0)",
          set(want) == {(True, 2), (True, 3), (False, 3)}, want)
    want, _ = ladder_levels(0.52, Quote(0.60, 100, 0.65, 100), 0.45, 0.50, L3, (1, 2, 3), 100, _big)
    check("ladder: never at or through the other side's best order (bid L1 0.505 >= 0.50 left out)",
          {k for k in want if k[0]} == {(True, 2), (True, 3)} and want[(True, 3)][0] == 0.485, want)
    want, allowed = ladder_levels(0.52, _q, 0.51, 0.53, L3, (1, 2, 3), 100, {True: lambda p: 500, False: lambda p: 100})
    check("ladder: sizes clipped cumulatively (500 cap: level 0 100 + 100 + 200 + 100; a full side gets none)",
          [want.get((True, k), (0, 0))[1] for k in (1, 2, 3)] == [100, 200, 100] and allowed[(True, 3)] == 100
          and not any(not k[0] for k in want) and allowed[(False, 1)] == 0, (want, allowed))
    want, _ = ladder_levels(0.52, Quote(None, 0, 0.53, 100), 0.51, 0.53, L3, (1, 2, 3), 100, _big)
    check("ladder: no ladder on a side level 0 doesn't quote", want and all(not k[0] for k in want), want)

    good, bad = validate_overrides({"ladder_offsets": [0.02, 0.03], "ladder_markets": "headline, quiet",
                                    "ladder_enabled": True, "ladder_min_writes": 5}, Config())
    check("overrides: ladder lists of numbers and the market list are live settings",
          good == {"ladder_offsets": (0.02, 0.03), "ladder_markets": "headline,quiet", "ladder_enabled": True,
                   "ladder_min_writes": 5} and not bad, (good, bad))
    good, bad = validate_overrides({"ladder_offsets": [0.5], "ladder_size_mults": "1,2", "ladder_markets": "all",
                                    "ladder_headline_mults": [1] * 9}, Config())
    check("ladder: off by default; free-cash gate 10% (0.2 would keep it off at 2 Oct's 11k of 101k)",
          _live.ladder_enabled is False and _live.ladder_min_cash_frac == 0.1)
    check("overrides: out-of-range, non-list, unknown-name and too-long ladder values are refused",
          not good and len(bad) == 4, (good, bad))

    _d = tempfile.mkdtemp()
    _fp = os.path.join(_d, "fills.csv")
    with open(_fp, "w", newline="") as f:
        w = csv.writer(f); w.writerow(FillLogger.COLUMNS[:-1]); w.writerow([7, "t", "11", 5, "bid", 100, 0.49, 0.49, 0.5, ""])
    _fl = FillLogger(_fp)
    _rows = read_fills(_fp)
    check("fills.csv from before the level column: the column is added, every row kept (no .old rotation)",
          len(_rows) == 1 and _rows[0]["level"] == "" and _rows[0]["fill_id"] == "7" and "7" in _fl.seen
          and not os.path.exists(_fp + ".old"), _rows)

    def tight():
        return {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
                "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
                "21": {"bids": [lvl(0.51, 1000)], "asks": [lvl(0.53, 1000)]},
                "22": {"bids": [lvl(0.47, 1000)], "asks": [lvl(0.49, 1000)]}}

    def ladder_bot(enabled=True, **kw):
        a, b = make_bot(books=tight())
        b.cfg.selftest_enabled = False
        b.cfg.ladder_enabled, b.cfg.ladder_markets = enabled, "headline,busy,quiet"
        for k, v in kw.items():
            setattr(b.cfg, k, v)
        return a, b

    def lad(a, b, eid):
        """Our resting ladder orders on eid: sorted (side, YES price, shares, level)."""
        return sorted((("bid" if a.yes_view(o)[0] else "ask"), a.yes_view(o)[1], o["quantity"],
                       (b.order_meta.get(oid) or {}).get("level", 0))
                      for oid, o in a.orders.items() if o["exchangeId"] == eid and (b.order_meta.get(oid) or {}).get("level"))

    def touch(a, b, eid):
        return sorted(x for x in a.ours(eid) if x not in [y[:3] for y in lad(a, b, eid)])

    def lad_ids(a, b):
        return {oid for oid in a.orders if (b.order_meta.get(oid) or {}).get("level")}

    def fill_order(a, oid, qty):
        o = a.orders[oid]
        is_bid, p = a.yes_view(o)
        o["quantity"] -= qty
        a.inv[o["exchangeId"]] = a.inv.get(o["exchangeId"], 0) + (qty if is_bid else -qty)
        a.fills.append({"id": len(a.fills) + 1, "orderId": oid, "exchangeId": o["exchangeId"], "price": p,
                        "quantity": qty if is_bid else -qty, "side": "yes" if is_bid else "no", "filledAt": iso(utcnow())})
        if o["quantity"] <= 0:
            del a.orders[oid]

    # Disabled: exactly today's planner (plan_change with every resting order), no level notes, no status keys.
    a0, b0 = ladder_bot(enabled=False)
    seen_pc = []
    _real_pc = b0.plan_change
    b0.plan_change = lambda *args: seen_pc.append(args) or _real_pc(*args)
    _real_or = b0.orders_by_eid
    _all_rest = []
    b0.orders_by_eid = lambda now: _all_rest.append(_real_or(now)) or _all_rest[-1]
    for _ in range(3):
        b0.cycle()
    b0.write_status(True)
    st0 = json.load(open(b0.cfg.status_file))
    check("ladder off: plan_change sees every resting order (as before), no ladder orders, no level notes, "
          "no ladder keys in status.json",
          seen_pc and all(list(args[2]) == list(_all_rest[-1].get(args[0].eid, [])) for args in seen_pc[-4:])
          and not lad_ids(a0, b0) and not any("level" in m for m in b0.order_meta.values())
          and "ladder_orders" not in st0 and "ladder_cash" not in st0, (list(st0), b0.order_meta))

    a, b = ladder_bot()
    b.cycle()
    check("ladder on, cycle 1: level 0 goes first, the ladder waits (level 0 still needs a write there)",
          not lad_ids(a, b) and len(a.ours("21")) == 2, a.ours("21"))
    b.cycle()
    check("ladder on, cycle 2: 3 levels per side on the tight market at 1.5/2.5/3.5c, 1/2/3 x 100 shares",
          lad(a, b, "21") == [("ask", 0.535, 100, 1), ("ask", 0.545, 200, 2), ("ask", 0.555, 300, 3),
                              ("bid", 0.485, 300, 3), ("bid", 0.495, 200, 2), ("bid", 0.505, 100, 1)], lad(a, b, "21"))
    check("ladder on: none in the 8c-wide market (every level would sit at or inside level 0)",
          not lad(a, b, "11"), lad(a, b, "11"))
    check("ladder on: level 0 identical to the ladder-off bot", all(touch(a, b, e) == a0.ours(e) for e in b.ex),
          [(touch(a, b, e), a0.ours(e)) for e in b.ex])
    ids2 = lad_ids(a, b)
    a.calls.clear()
    b.cycle()
    check("ladder on: nothing changes -> no write at all (queue spots kept); log tag L6",
          not a.sent("batch") and not a.sent("cancel_order") and not a.sent("cancel_all") and lad_ids(a, b) == ids2
          and b.ex["21"].lad_tag == " L6", (a.calls, b.ex["21"].lad_tag))
    b.write_status(True)
    st = json.load(open(b.cfg.status_file))
    check("status.json: ladder_orders and ladder_cash", st.get("ladder_orders") == 12 and st.get("ladder_cash", 0) > 1000,
          {k: v for k, v in st.items() if "ladder" in k})

    # Only the level that changed is cancelled and re-placed; churn control doesn't count it.
    oid2 = next(oid for oid, o in a.orders.items() if o["exchangeId"] == "21" and b.order_meta[oid].get("level") == 2
                and a.yes_view(o)[0])
    a.orders[oid2]["quantity"] = 50                     # level 2 bid mostly gone (below keep_fraction)
    b.ex["21"].reprices.clear()
    a.calls.clear()
    b.cycle()
    check("only the changed level is cancelled (one DELETE, no cancel-all) and re-placed",
          a.sent("cancel_order") == [("cancel_order", oid2)] and not a.sent("cancel_all") and len(a.sent("batch")) == 1
          and ("bid", 0.495, 200, 2) in lad(a, b, "21") and len(lad_ids(a, b) - ids2) == 1, a.calls)
    check("churn control: a ladder re-price is not a level-0 reprice", not any(b.ex["21"].reprices.values()),
          b.ex["21"].reprices)

    # Fills carry their level into fills.csv and analyze.
    oid3 = next(oid for oid, o in a.orders.items() if o["exchangeId"] == "21" and b.order_meta[oid].get("level") == 3
                and a.yes_view(o)[0])
    fill_order(a, oid3, 300)
    b.cycle()
    row = [r for r in read_fills(b.cfg.fills_csv) if r["order_id"] == str(oid3)]
    check("a ladder fill is attributed with its level (fills.csv level 3, quote price 0.485)",
          row and row[0]["level"] == "3" and row[0]["our_side"] == "bid" and row[0]["quote_price"] == "0.485", row)
    lines = analyze(b.cfg.fills_csv, "")
    check("analyze: a by-level line once the ladder has filled",
          any(x.startswith("by level:") and "L3 1 fills" in x for x in lines), lines[:3])

    # Unsafe ladder orders go at once; no exchange cancel-all while ladder orders stay.
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    ex21 = b.ex["21"]
    rest21 = b.orders_by_eid(utcnow())["21"]
    ch = b.plan_exchange(ex21, replace(ex21.quote, bid=None, bid_size=0), rest21, 0.52, utcnow(), time.monotonic())
    lad_bids = {o.order_id for o in rest21 if o.is_bid and b.order_level(o)}
    check("level-0 bid pulled -> the bid ladder is pulled in the same change, as a pull; asks untouched",
          ch is not None and ch.key[0] == 0 and len(lad_bids) == 3 and lad_bids <= {o.order_id for o in ch.doomed}
          and not ch.whole and not any(not o.is_bid for o in ch.doomed), ch and (ch.key, [(o.is_bid, o.price) for o in ch.doomed]))
    check("...and churn control counts only the level-0 side", ch.reprice_sides() == ["bid"], ch.reprice_sides())
    b.cfg.reprice_tolerance_ticks = 0
    ch = b.plan_exchange(ex21, replace(ex21.quote, bid_size=90, ask_size=90), rest21, 0.52, utcnow(), time.monotonic())
    check("both level-0 sides change while the ladder rests: per-order cancels, never the exchange cancel-all",
          ch is not None and not ch.whole and len(ch.doomed) == 2 and all(b.order_level(o) == 0 for o in ch.doomed),
          ch and (ch.whole, len(ch.doomed)))
    b.cfg.reprice_tolerance_ticks = 1

    # Write priority and the writes_left gate.
    a, b = ladder_bot()
    b.cycle()
    a.writes_left = lambda: 9
    b.cycle()
    check("fewer than ladder_min_writes writes left: no ladder placed", not lad_ids(a, b), lad(a, b, "21"))
    a.writes_left = lambda: 10 ** 6
    ex21 = b.ex["21"]
    ch = b.plan_exchange(ex21, ex21.quote, b.orders_by_eid(utcnow())["21"], 0.52, utcnow(), time.monotonic())
    check("a ladder change sorts after every level-0 change (tier 2 vs 0 / 0.5 / 1)",
          ch is not None and ch.key[0] == LADDER_TIER and ch.key > b.change_key(ex21, pull=False, reprice=True)
          and len(ch.new) == 6, ch and ch.key)
    b.cfg.batch_size = 1
    ex11 = b.ex["11"]
    ch0 = Change(ex11, [], False, [b.new_order(ex11, True, 0.06, 100, 0.14, utcnow())], b.change_key(ex11, pull=False))
    chl = Change(ex21, [], False, [b.new_order(ex21, True, 0.45, 100, 0.52, utcnow(), level=2)],
                 (LADDER_TIER,) + b.change_key(ex21, pull=False)[1:], count=False)
    a.calls.clear()
    a.writes_left = lambda: 11
    b.send_changes([chl, ch0])
    placed = sorted(a.yes_view(o)[1] for o in a.orders.values() if a.yes_view(o)[1] in (0.06, 0.45))
    check("send_changes: level 0 first; the ladder only with ladder_min_writes to spare after it",
          a.sent("batch") == [("batch", 1)] and placed == [0.06], (a.calls, placed))
    a.writes_left = lambda: 10 ** 6

    # Anchor: re-priced only when fair value moves >= ladder_move (2c); a 1-tick difference is kept (tolerance).
    a, b = ladder_bot(ladder_offsets=(0.02, 0.03, 0.04))
    b.cycle(); b.cycle()
    ids = lad_ids(a, b)
    ids21 = {oid for oid in ids if a.orders[oid]["exchangeId"] == "21"}
    a.books["21"] = {"bids": [lvl(0.51, 1000)], "asks": [lvl(0.535, 1000)]}     # fair value +0.25c
    a.books["22"] = {"bids": [lvl(0.465, 1000)], "asks": [lvl(0.49, 1000)]}
    b.cycle(); b.cycle()
    check("anchor: a 0.25c fair-value move leaves the ladder where it is",
          len(ids) == 16 and lad_ids(a, b) == ids and abs(b.ex["21"].lad_fv - 0.52) < 1e-9, (b.ex["21"].lad_fv, lad(a, b, "21")))
    a.books["21"] = {"bids": [lvl(0.53, 1000)], "asks": [lvl(0.55, 1000)]}      # fair value +2c
    a.books["22"] = {"bids": [lvl(0.45, 1000)], "asks": [lvl(0.47, 1000)]}
    b.cycle(); b.cycle(); b.cycle()
    check("anchor: a 2c move re-prices every level around the new anchor (0.54)",
          abs(b.ex["21"].lad_fv - 0.54) < 1e-9 and [x[1] for x in lad(a, b, "21")] == [0.56, 0.57, 0.58, 0.5, 0.51, 0.52]
          and not (lad_ids(a, b) & ids21), lad(a, b, "21"))
    a, b = ladder_bot()
    ex21, q21 = b.ex["21"], Quote(0.50, 100, 0.53, 100)
    b.lad_cash_left = 10 ** 9
    anchors = []
    for k in range(20):                                  # fair value wobbling 0.50 / 0.51 / 0.515
        b.ladder_targets(ex21, q21, (0.50, 0.51, 0.515)[k % 3], 1000.0 + k)
        anchors.append(ex21.lad_fv)
    check("anchor: fair value oscillating within 1.5c never re-anchors (at most once per 2c)",
          len(set(anchors)) == 1, sorted(set(anchors)))

    # Pull on a Polymarket jump, back after ladder_pull_seconds.
    a, b = ladder_bot()
    ex21, t0, q21 = b.ex["21"], 1000.0, Quote(0.51, 100, 0.53, 100)
    b.lad_cash_left = 10 ** 9
    ex21.ref = 0.52
    w1, _, bl1 = b.ladder_targets(ex21, q21, 0.52, t0)
    ex21.ref = 0.536                                     # Polymarket +1.6c
    w2, _, bl2 = b.ladder_targets(ex21, q21, 0.52, t0 + 5)
    w3, _, bl3 = b.ladder_targets(ex21, q21, 0.52, t0 + 34)
    w4, _, bl4 = b.ladder_targets(ex21, q21, 0.52, t0 + 36)
    check("Polymarket jump >= 1.5c: ladder pulled (both sides blocked) for 30 s, then back",
          len(w1) == 6 and not w2 and bl2 == {True: True, False: True} and not w3 and len(w4) == 6
          and not any(bl4.values()) and ex21.lad_ref == 0.536, (w1, w2, w3, w4))
    ex21.ref = 0.546                                     # +1c from the new anchor's reading: below the threshold
    w5, _, _ = b.ladder_targets(ex21, q21, 0.52, t0 + 40)
    check("...a smaller Polymarket move keeps it", len(w5) == 6, w5)

    # Cash gate (ladder_min_cash_frac 0.1): 101k account, 92k in positions -> ~9k free -> no ladder.
    a, b = ladder_bot(capital_in_positions_max_frac=0.0)   # (the capital ceiling would pull level 0 too)
    a.equity = 101_000.0
    _pos = a.positions
    a.positions = lambda: {**_pos(), "summary": {"totalMarketValue": 92_000}}
    for _ in range(3):
        b.cycle()
    check("cash gate: 9k free of 101k (< 10%) -> no ladder orders at all; level 0 as usual",
          not lad_ids(a, b) and b.health.get("ladder_orders") == 0 and b.health["ladder_free_cash_frac"] < 0.1
          and len(a.ours("21")) == 2, (b.health.get("ladder_free_cash_frac"), lad(a, b, "21")))
    a.positions = lambda: {**_pos(), "summary": {"totalMarketValue": 60_000}}
    b.cycle(); b.cycle()
    check("cash gate: 41k free -> ladder placed, within the free cash above 10%",
          len(lad_ids(a, b)) == 12 and b.health["ladder_cash"] <= 101_000 - 60_000 - 0.1 * 101_000,
          b.health.get("ladder_cash"))
    _raw = [{"id": 1, "exchangeId": "11", "side": "yes", "action": "buy", "priceLimit": 0.5, "quantity": 120_000,
             "open": True, "expirationDate": None}] + [dict(o) for o in a.orders.values()]   # 60k in other orders
    _lad = {o.order_id for o in b.ladder_orders()}
    b.ladder_setup(100_000, 0, [o for o in _raw if o["id"] == 1 or o["id"] in _lad], set(), 0.0, {})
    check("cash gate: ladder cash counts toward quote_capital_frac with the cash in other orders (60k of 60%)",
          b.lad_cash_left == 0.0, b.lad_cash_left)

    # Inventory rules.
    a, b = ladder_bot()
    a.inv["21"] = 400                                    # > ladder_max_inv_quotes (3) x 100 shares
    for _ in range(3):
        b.cycle()
    check("|race position| > 3 quote sizes: ladder only on the side that reduces it (asks when long)",
          lad(a, b, "21") and {x[0] for x in lad(a, b, "21")} == {"ask"}, lad(a, b, "21"))
    a, b = ladder_bot(max_worst_case_frac=0.001, risk_model="sum_max")
    a.inv["21"] = 250                                    # worst case 250 x 0.52 > 0.1% of 100k -> reduce-only
    for _ in range(3):
        b.cycle()
    check("reduce-only: no adding-side ladder; the reducing side up to the position (250 - level 0 100 = 150)",
          b.global_reduce and [x[:3] for x in lad(a, b, "21")] == [("ask", 0.535, 100), ("ask", 0.545, 50)]
          and not lad(a, b, "11"), (b.global_reduce, lad(a, b, "21"), a.ours("21")))

    # Burst mode: no ladder changes; what rests stays while it's safe.
    a, b = ladder_bot(burst_extra_edge=0.0, burst_size_factor=1.0)
    b.cycle(); b.cycle()
    ids = lad_ids(a, b)
    gone = min(ids)
    del a.orders[gone]
    b.burst, b.burst_calm_since, b.burst_set, b.burst_cfg = True, time.monotonic(), set(b.ex), b.cfg
    a.calls.clear()
    b.cycle(); b.cycle()
    check("burst mode: the ladder isn't touched (a missing level isn't re-placed, the rest stays)",
          b.burst and lad_ids(a, b) == ids - {gone} and not a.sent("batch") and not a.sent("cancel_order"), a.calls)

    # Handover: adopted ladder orders keep their level, even when the order notes are lost.
    a, b = ladder_bot()
    n3 = {"k": 0}
    _rc = b.cycle
    def _then_handover():
        n3["k"] += 1
        _rc()
        if n3["k"] == 3:
            b.request_handover()
    b.cycle, b.cfg.loop_seconds = _then_handover, 0
    b.run()
    ids = lad_ids(a, b)
    os.remove(b.cfg.order_notes_file)                    # notes lost: only the handover note knows the levels
    b2 = Bot(a, b.cfg)
    a.calls.clear()
    run_cycles(b2, 2)
    before = a.calls[:a.calls.index(("cancel_all", None))] if ("cancel_all", None) in a.calls else a.calls
    check("handover: adopted ladder orders are recognised by level - nothing cancelled or placed again",
          len(ids) == 12 and not [c for c in before if c[0] in ("batch", "cancel_order", "cancel_all")]
          and all((b2.order_meta.get(oid) or {}).get("level") for oid in ids), before[:6])
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    ids = lad_ids(a, b)
    for oid in ids:
        b.order_meta[oid].pop("level")                   # no notes at all: they look like level-0 duplicates
    b.cycle()
    check("unknown ladder orders (no level anywhere): cancelled once, as level-0 duplicates",
          not (set(a.orders) & ids), a.calls[-6:])
    b.cycle(); b.cycle()
    check("...and the ladder is re-placed after that", len(lad_ids(a, b)) == 12, lad(a, b, "21"))

    # A failed cancel never leaves two orders on one level.
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    oid2 = next(oid for oid, o in a.orders.items() if o["exchangeId"] == "21" and b.order_meta[oid].get("level") == 2
                and a.yes_view(o)[0])
    a.orders[oid2]["quantity"] = 50
    _real_cancel = a.cancel_order
    def _fail_cancel(oid):
        raise ApiError(503, "SERVICE_UNAVAILABLE", "flaky")
    a.cancel_order = _fail_cancel
    a.calls.clear()
    b.cycle()
    lvl2 = [x for x in lad(a, b, "21") if x[0] == "bid" and x[3] == 2]
    check("ladder: a cancel that fails -> nothing placed on that level (no duplicate)",
          len(lvl2) == 1 and lvl2[0][2] == 50 and not a.sent("batch"), (lvl2, a.calls[-4:]))
    a.cancel_order = _real_cancel
    b.cycle(); b.cycle()
    lvl2 = [x for x in lad(a, b, "21") if x[0] == "bid" and x[3] == 2]
    check("...and once the cancel goes through, the level is re-placed once", lvl2 == [("bid", 0.495, 200, 2)], lvl2)

    # Kelly: with a liquid Polymarket price, each level's cap is the Kelly limit at that level's price.
    a, b = ladder_bot()
    ex21 = b.ex["21"]
    ex21.ref, ex21.inv, ex21.eff, b.lad_liquid = 0.52, 30.0, 30.0, {"21"}
    caps = b.ladder_caps(ex21, 0.52, 100)
    check("ladder caps: Kelly at the level's price, net of the position (deeper = bigger limit)",
          caps[True](0.485) == min(kelly_position(0.52, 0.485, b.bankroll(), b.cfg, yes=True) - 30,
                                   b.cfg.max_party_delta_frac * b.bankroll())
          and caps[True](0.485) > caps[True](0.505), (caps[True](0.485), caps[True](0.505)))

    # Hot-fix 2.2 semantics: a size FACTOR never makes a resting ladder order unsafe; a tighter LIMIT does.
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    ids = {oid for oid in lad_ids(a, b) if a.orders[oid]["exchangeId"] == "21"}
    b.cfg.capital_in_positions_max_frac, b.cfg.capital_ceiling_adding_size_factor = 1e-9, 0.5
    a.inv["11"] = 1                                      # capital in positions > 0 -> ceiling on (flat 21: both sides adding)
    b.cycle(); b.cycle()
    q21 = b.ex["21"].quote
    ids_now = {oid for oid in lad_ids(a, b) if a.orders[oid]["exchangeId"] == "21"}
    pulled = [(b.order_meta[oid]["our_side"], b.order_meta[oid]["price"]) for oid in ids - ids_now]
    check("capital ceiling (a size factor): resting ladder orders stay (unless level 0 moved onto them), none added",
          b.capital_over and ids_now <= ids and len(ids_now) >= 5
          and all((s_ == "bid" and p_ >= q21.bid - 1e-9) or (s_ == "ask" and p_ <= q21.ask + 1e-9) for s_, p_ in pulled),
          (b.capital_over, pulled, q21))
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    b.cfg.max_position_frac = 0.003                      # limit 300: level 0 100 + L1 100 + 100 left for L2 (200 rests)
    b.cycle()
    l21 = lad(a, b, "21")
    check("a tighter limit: the ladder level now too big is pulled at once, the one within it stays",
          ("bid", 0.495, 200, 2) not in l21 and ("bid", 0.505, 100, 1) in l21 and ("bid", 0.485, 300, 3) not in l21,
          l21)

    # Review fixes (L1-L4).
    want, _ = ladder_levels(0.52, Quote(0.51, 1000, 0.53, 1000), 0.51, 0.53, L3, (1, 2, 3), 1000, _big, max_cash=1000)
    check("ladder: per-order cash cap (3,000 sh at 0.485 = 1,455 > 1,000 -> 2,061 sh)",
          want[(True, 3)] == (0.485, int(1000 / 0.485)) and want[(True, 1)] == (0.505, 1000)
          and want[(False, 3)][1] == int(1000 / (1 - 0.555)), want)
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    ex21 = b.ex["21"]
    rest21 = b.orders_by_eid(utcnow())["21"]
    q_dip = replace(ex21.quote, bid=0.505)               # wanted level 0 one tick lower; the resting 0.51 is kept
    a.calls.clear()
    ch = b.plan_exchange(ex21, q_dip, rest21, 0.515, utcnow(), time.monotonic())
    check("hair trigger: level 1 == the wanted level 0 but behind the RESTING touch -> no urgent pull",
          ch is None or (ch.key[0] == LADDER_TIER), ch and (ch.key, [(o.is_bid, o.price) for o in ch.doomed]))
    b.lad_cash_left = 10 ** 9
    _w, _, _ = b.ladder_targets(ex21, Quote(0.505, 100, 0.53, 100), 0.52, time.monotonic(), {True: 0.51, False: 0.535})
    check("...and no level is placed at a resting touch kept a tick off its target either (it'd be pulled next cycle)",
          (True, 1) not in _w and _w[(True, 2)][0] == 0.495 and (False, 1) not in _w and _w[(False, 2)][0] == 0.545, _w)
    b.cfg.reprice_tolerance_ticks = 0
    ch = b.plan_exchange(ex21, replace(ex21.quote, bid=0.50, bid_limit=0.50), rest21, 0.515, utcnow(), time.monotonic())
    check("...but a ladder order beyond level 0's limit price IS urgent (rides with the level-0 change)",
          ch is not None and ch.key[0] < LADDER_TIER and any(o.price == 0.505 and b.order_level(o) == 1 for o in ch.doomed),
          ch and ch.key)
    b.cfg.reprice_tolerance_ticks = 1
    _lad_pulls = []
    for k in range(9):                                   # 9 markets x 3 ladder levels = 27 ladder-only pulls
        _os = [Resting(9000 + 3 * k + j, "21", True, 0.4 - 0.01 * j, 100, None) for j in range(3)]
        _lad_pulls.append(Change(ex21, _os, False, [], b.change_key(ex21, pull=True), count=False, unsafe=True,
                                 ladder={o.order_id for o in _os}))
    a.calls.clear()
    check("pull storm: 27 ladder-only pulls never become a tournament-wide cancel-all",
          b.pull_storm(_lad_pulls) is False and not a.sent("cancel_all"), a.calls)
    # min_quote_life: a stale, safe ladder order younger than it stays.
    a, b = ladder_bot(churn_control=True, min_quote_life_seconds=1e9)
    b.cycle(); b.cycle()
    oid2 = next(oid for oid, o in a.orders.items() if o["exchangeId"] == "21" and b.order_meta[oid].get("level") == 2
                and a.yes_view(o)[0])
    a.orders[oid2]["quantity"] = 50
    a.calls.clear()
    b.cycle()
    check("churn control: a young ladder order isn't re-priced (min_quote_life_seconds)",
          oid2 in a.orders and not a.sent("cancel_order") and not a.sent("batch"), a.calls)
    # The writes gate counts the ladder's own writes: 12 writes left, a 6-order ladder (1 batch) -> 11 >= 10 goes;
    # with a 1-order batch size it costs 6 -> 6 < 10 left after it -> deferred.
    a, b = ladder_bot(batch_size=1)
    b.cycle()
    a.writes_left = lambda: 12
    b.cycle()
    check("writes gate: ladder_min_writes stay free AFTER the ladder's own writes",
          not {oid for oid in lad_ids(a, b) if a.orders[oid]["exchangeId"] == "21"}, lad(a, b, "21"))
    a.writes_left = lambda: 10 ** 6

    # Turning the ladder off pulls it; level 0 stays.
    a, b = ladder_bot()
    b.cycle(); b.cycle()
    touch0 = touch(a, b, "21")
    b.cfg.ladder_enabled = False
    b.cycle()
    check("ladder switched off: its orders are pulled, level 0 untouched", not lad_ids(a, b) and a.ours("21") == touch0,
          a.ours("21"))
finally:
    logging.disable(logging.NOTSET)

# F7: a method defined twice in a class silently shadows the first (thin_book_prices was): none may be.
import ast
_dups = []
for _node in ast.walk(ast.parse(open(M.__file__).read())):
    if isinstance(_node, ast.ClassDef):
        _names = [f.name for f in _node.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
        _dups += [f"{_node.name}.{n}" for n in set(_names) if _names.count(n) > 1]
check("no method is defined twice in one class (thin_book_prices was)", not _dups, _dups)


# =============================================================================================
# 2 Oct 11:31 INCIDENT: 2-5 min cycles after leaving reduce-only (capital ceiling x0.25, burst x0.5)
# =============================================================================================
print("--- incident 2 Oct: size factors, urgent cap, 429 pauses, main-loop blocking, watchdog")
from mm_bot import Change, ApiError, compute_quote, Api as _Api
from dataclasses import replace as _rp

# (a) A size factor (capital ceiling) shrinks the wanted size; the unscaled size stays the most that may rest.
_cq = _rp(M.CFG, fl_bias_enabled=False)
q_full = compute_quote(0.5, 0, 0, 0.45, 0.55, _cq, order_size=400)
q_ceil = compute_quote(0.5, 0, 0, 0.45, 0.55, _cq, order_size=400, adding_factor=0.25)
check("ceiling x0.25: wanted sizes shrink, bid_max/ask_max keep the unscaled sizes (within the limits)",
      q_ceil.bid_size == int(q_full.bid_size * 0.25) and q_ceil.bid_max == q_full.bid_size
      and q_ceil.ask_max == q_full.ask_size and q_full.bid_max is None and q_full.ask_max is None,
      (q_full, q_ceil))
q_ro = compute_quote(0.5, 100, 100, 0.45, 0.55, _cq, reduce_only=True, order_size=400, adding_factor=0.25)
check("...a limit (reduce-only) still caps bid_max/ask_max: no bid allowed, ask at most the position",
      q_ro.bid is None and (q_ro.ask_max or q_ro.ask_size) <= 100, q_ro)

a, b = make_bot(); b.cycle()
rest = lambda e: [o for o in b.my_orders.values() if o.eid == e]
bid11 = [o for o in rest("11") if o.is_bid][0]
ask11 = [o for o in rest("11") if not o.is_bid][0]
now, now_m = M.utcnow(), time.monotonic()
Q, QA = int(bid11.qty), int(ask11.qty)
shrunk = Quote(bid11.price, max(1, Q // 4), ask11.price, max(1, QA // 4), bid11.price, ask11.price, Q, QA)
check("a full-size order under a shrunk size FACTOR (ceiling x0.25, bid_max = its size) stays: no change",
      b.plan_change(b.ex["11"], shrunk, rest("11"), 0.14, now, now_m) is None)
b.burst = True; b.burst_set = set()
check("...also in burst mode outside the top markets (burst x0.5 on top of the ceiling)",
      b.plan_change(b.ex["11"], shrunk, rest("11"), 0.14, now, now_m) is None)
b.burst = False
old = Quote(bid11.price, max(1, Q // 4), ask11.price, max(1, QA // 4), bid11.price, ask11.price)
ch_old = b.plan_change(b.ex["11"], old, rest("11"), 0.14, now, now_m)
check("without bid_max (a real limit shrank it) the oversized order is unsafe: urgent (key 0)",
      ch_old is not None and ch_old.key[0] == 0 and ch_old.unsafe, ch_old and ch_old.key)
lim = Quote(bid11.price, max(1, Q // 4), ask11.price, QA, bid11.price, ask11.price, Q - 10, None)
ch_lim = b.plan_change(b.ex["11"], lim, rest("11"), 0.14, now, now_m)
check("an order above the POSITION limit (bid_max below its size) is still urgent",
      ch_lim is not None and ch_lim.key[0] == 0 and ch_lim.unsafe, ch_lim and ch_lim.key)

# (b) urgent writes per cycle are capped; price-unsafe / pulls go first.
a, b = make_bot(); b.cycle()
rest = lambda e: [o for o in b.my_orders.values() if o.eid == e]
b.cfg.urgent_writes_per_cycle = 2
reprices = [Change(b.ex[e], rest(e)[:1], False, [], (0, 1, 1, -100), unsafe=False) for e in ("11", "12")]
pulls = [Change(b.ex[e], rest(e)[:1], False, [], (0, 1, 1, 0), unsafe=True) for e in ("21", "22")]
pulled = {o.order_id for c in pulls for o in c.doomed}
n0 = len(a.sent("cancel_order"))
b.send_changes(reprices + pulls)
sent = [c[1] for c in a.sent("cancel_order")[n0:]]
check("urgent_writes_per_cycle 2: only 2 urgent cancels this cycle, the unsafe pulls first",
      len(sent) == 2 and set(sent) == pulled, sent)
# Reviewer HIGH-2 (2.3): the cap skips further URGENT changes; ordinary changes behind them still go.
a, b = make_bot(); b.cycle()
b.cfg.urgent_writes_per_cycle = 1
urgent = [Change(b.ex[e], rest(e)[:1], False, [], (0, 1, 1, 0), unsafe=True) for e in ("21", "22")]
normal = [Change(b.ex["11"], rest("11")[:1], False, [], (1, 1, 1, 0), unsafe=False)]
normal_id = rest("11")[0].order_id
n0 = len(a.sent("cancel_order"))
b.send_changes(urgent + normal)
sent = [c[1] for c in a.sent("cancel_order")[n0:]]
check("urgent cap reached: the ordinary reprice behind the capped urgent changes is still sent (no book starvation)",
      len(sent) == 2 and normal_id in sent, sent)
b.cfg.urgent_writes_per_cycle = 0
n0 = len(a.sent("cancel_order"))
b.send_changes([Change(b.ex[e], rest(e)[:1], False, [], (0, 1, 1, 0), unsafe=True) for e in ("11", "12")])
check("...0 = no cap", len(a.sent("cancel_order")) - n0 == 2)

# (c) The main loop never blocks on the write budget.
cap_api = _Api(M.CFG, False)
cap_api.gap, cap_api.wbudget, cap_api.BUDGET_WINDOW = 0.0, 1, 30.0
cap_api.throttle(write=True)
cap_api.tl.max_write_wait = 0.2
t0 = time.monotonic()
try:
    cap_api.throttle(write=True); raised = None
except ApiError as e:
    raised = e
check("a main-thread write that would wait 30 s for the write budget raises 429 WRITE_BUDGET_WAIT at once, unreserved",
      raised is not None and raised.status == 429 and raised.code == "WRITE_BUDGET_WAIT"
      and time.monotonic() - t0 < 0.1 and len(cap_api._wwindow) == 1, raised)
other = {}
th = threading.Thread(target=lambda: other.setdefault("tl", getattr(cap_api.tl, "max_write_wait", None)))
th.start(); th.join()
check("...the cap is per thread (writer threads still wait their turn)", other["tl"] is None)
cap_api.tl.max_write_wait = None
check("write_wait() reports the wait a write would have (~30 s)", 29 < cap_api.write_wait() <= 30.01, cap_api.write_wait())

a, b = make_bot(); b.cycle()
b.cfg.write_wait_seconds = 0.3
def slow_batch(orders, _real=a.place_batch):
    time.sleep(2.0); return _real(orders)
a.place_batch = slow_batch
rest = lambda e: [o for o in b.my_orders.values() if o.eid == e]
o11 = rest("11")[0]
new = [b.new_order(b.ex["11"], o11.is_bid, o11.price, int(o11.qty), 0.14, M.utcnow())]
a.cancel_all(None, "11"); b.forget_orders([o.order_id for o in rest("11")])
t0 = time.monotonic()
b.send_changes([Change(b.ex["11"], [], False, new, (1, 1, 0, 0))])
took = time.monotonic() - t0
check("send_changes returns after write_wait_seconds (0.3 s) while a 2 s write runs on: no join", took < 0.8, took)
check("...the next cycle doesn't wait for it either (the exchange is just left alone while busy)",
      M.busy(b.ex["11"], time.monotonic()))
t0 = time.monotonic(); b.cycle(); took = time.monotonic() - t0
check("...a cycle with that write in flight takes well under its 2 s", took < 1.5, took)
b.drain_writes(3)

# A sleeping request that a 429 pause overtakes waits the pause out instead of firing into it.
p_api = _Api(M.CFG, False)
p_api.gap, p_api.BUDGET_WINDOW = 0.3, 60.0
p_api.throttle()
box = {}
def late():
    t = time.monotonic(); p_api.throttle(); box["w"] = time.monotonic() - t
th = threading.Thread(target=late); th.start()
time.sleep(0.05)
with p_api._lock:
    p_api.paused_until = time.monotonic() + 0.6
th.join()
check("a request already sleeping for its slot when a 429 pause starts waits until the pause ends", box["w"] >= 0.6, box)

# (d) Every 429 logged; a 429 during a pause extends it to Retry-After from now (not added); one cut per pause.
class _Resp:
    def __init__(s, code, headers=None): s.status_code, s.headers, s.text = code, headers or {}, "{}"; s.content = b"{}"
    def json(s): return {}
class _Grab(logging.Handler):
    def __init__(s): super().__init__(); s.msgs = []
    def emit(s, r): s.msgs.append(r.getMessage())
grab = _Grab(); M.log.addHandler(grab); _lvl = M.log.level; M.log.setLevel(logging.WARNING); M.log.propagate = False
r_api = _Api(_rp(M.CFG, writes_per_minute=45, writes_per_minute_max=50, write_budget_cut=0.75), False)
r_api.gap = 0.0
def inside_pause(*a, **k):
    # another thread's 429 started a pause just before this request's answer arrives
    with r_api._lock:
        r_api.paused_until = time.monotonic() + 0.4
        r_api.pauses_total += 1
    r_api.s.request = lambda *a, **k: _Resp(200)
    return _Resp(429, {"Retry-After": "0.2"})
r_api.s.request = inside_pause
w0 = r_api.wbudget
t0 = time.monotonic()
r_api.call("POST", "/orders/batch", body={})
waited = time.monotonic() - t0
lines = [m for m in grab.msgs if "RATE LIMITED (429)" in m]
check("a 429 inside a pause is logged (method, path, Retry-After, already paused)",
      len(lines) == 1 and "POST /orders/batch" in lines[0] and "Retry-After 0.2" in lines[0]
      and "already paused" in lines[0], lines)
check("...the pause is NOT added up (0.4 s, not 0.6 s) and the budgets are not cut again",
      0.38 <= waited < 0.58 and r_api.pauses_total == 1 and abs(r_api.wbudget - (w0 + 1 / 60)) < 1e-6,
      (round(waited, 2), r_api.pauses_total, r_api.wbudget))
grab.msgs.clear()
answers = [_Resp(429, {"Retry-After": "0.1"}), _Resp(429, {"Retry-After": "0.1"}), _Resp(200)]
r_api.s.request = lambda *a, **k: answers.pop(0)
r_api.call("GET", "/x")
lines = [m for m in grab.msgs if "RATE LIMITED (429)" in m]
check("two 429s one after another: both logged, each a new pause (the first had ended)",
      len(lines) == 2 and all("new pause" in m for m in lines) and r_api.pauses_total == 3 and r_api.rate_limited == 3,
      (lines, r_api.pauses_total))
M.log.removeHandler(grab); M.log.setLevel(_lvl); M.log.propagate = True
st = r_api.pause_state()
check("pause_state for status.json: paused_until / pause_seconds_left / pauses_total / rate_limited_total",
      set(st) == {"paused_until", "pause_seconds_left", "pauses_total", "rate_limited_total"} and st["pauses_total"] == 3)

a, b = make_bot(); b.cycle()
b.write_status(ok=True)
with open(b.cfg.status_file) as f:
    stj = json.load(f)
check("status.json: last cycle seconds + per-phase timings, pause state, seconds since the last cycle",
      "reads" in stj.get("last_cycle_phases", {}) and "decide" in stj["last_cycle_phases"]
      and "pauses_total" in stj and "paused_until" in stj and stj.get("seconds_since_cycle") is not None, stj.get("last_cycle_phases"))
check("summary-line text: '<s> s (reads .., decide ..)'", b.phases_text().endswith(")") or b.phases_text().endswith(" s"),
      b.phases_text())
a.paused_until = time.monotonic() + 5
n0 = len(a.calls)
t0 = time.monotonic(); b.cycle(); took = time.monotonic() - t0
check("during a 429 pause the cycle is skipped (no reads that would only sleep out the pause), loop stays responsive",
      not [c for c in a.calls[n0:] if c[0] in ("positions", "pnl")] and took < 1.5, (a.calls[n0:], took))
a.paused_until = 0.0
b.cfg.pause_skip_cycles = False

# (b2) takes / arbitrage on the main thread only when the budget has room.
a, b = make_bot(); b.cycle()
a.write_wait = lambda: 30.0
check("writes_ready: False while a write would wait 30 s (takes and arbitrage skip, the loop doesn't block)",
      not b.writes_ready(3))
a.write_wait = lambda: 0.0
check("...True when the budget has room", b.writes_ready(3))

# (e) Watchdog.
a, b = make_bot(); b.cycle()
alerts, exits = [], []
M.log.disabled = True                     # (the watchdog's stack dump is long)
_real_alert = M.alert
M.alert = lambda msg: alerts.append(msg)
b.hard_exit = lambda code: exits.append(code)
b.cfg.watchdog_alert_seconds, b.cfg.watchdog_exit_seconds = 180, 600
t = b.last_cycle_done
# Reviewer HIGH-1 (2.3): a cycle skipped during a 429 pause does NOT reset the watchdog's clock.
b.api.pause_left = lambda: 30.0
b.cycle()
check("a cycle skipped during a rate-limit pause leaves last_cycle_done alone (a pause chain still reaches the watchdog)",
      b.last_cycle_done == t, (b.last_cycle_done, t))
del b.api.pause_left
check("watchdog: quiet while cycles complete", b.watchdog_check(t + 10) is None and not alerts)
check("...alerts once at 180 s without a completed cycle", b.watchdog_check(t + 181) == "alert"
      and b.watchdog_check(t + 200) is None and len(alerts) == 1, alerts)
n0 = len(a.sent("cancel_all"))
check("...at 600 s: stacks logged, cancel-all, exit code 5 (systemd Restart=on-failure restarts; 3/4 are not)",
      b.watchdog_check(t + 601) == "exit" and exits == [M.EXIT_WATCHDOG] and M.EXIT_WATCHDOG == 5
      and len(a.sent("cancel_all")) == n0 + 1, (exits, a.sent("cancel_all")[n0:]))
hang = threading.Event()
a.cancel_all = lambda *x, **k: hang.wait(5)
b.cfg.watchdog_cancel_seconds = 0.2
t0 = time.monotonic(); b.watchdog_check(t + 601); took = time.monotonic() - t0
check("...a hanging cancel-all is cut off after watchdog_cancel_seconds, then the exit anyway", took < 1.0
      and exits == [5, 5], (took, exits))
hang.set()
b.cfg.watchdog_exit_seconds = 0
exits.clear()
check("...watchdog_exit_seconds 0 = never exits", b.watchdog_check(t + 5000) in (None, "alert") and not exits)
M.alert = _real_alert
M.log.disabled = False
check("new settings are live-overridable", all(k in M.OVERRIDABLE for k in (
    "urgent_writes_per_cycle", "main_write_wait_margin", "watchdog_alert_seconds", "watchdog_exit_seconds",
    "pause_skip_cycles")))



# LOW-8: a main-thread write refused for the budget (429 WRITE_BUDGET_WAIT) was never sent: no pending hold, no alert.
print("--- WRITE_BUDGET_WAIT = not sent (takes, arbitrage)")
_alerts = []
_real_alert2 = M.alert
M.alert = lambda msg: _alerts.append(msg)
a, b = take_setup(); b.cycle()
ex = b.ex["11"]
ex.book = {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]}
ex.take_dir, now_m = 1, time.monotonic()
a.writes_left = lambda: 2
n_cancel = len(a.sent("cancel_all"))
r = b.execute_take(ex, 0.30, 0.0, 0.14, now_m)
check("take with < 3 writes left: skipped BEFORE pulling our own quote (no cancel), direction kept, counted",
      r is False and len(a.sent("cancel_all")) == n_cancel and ex.take_dir == 1 and b.takes_skipped_budget == 1
      and b.takes_total == 0, (r, a.sent("cancel_all")[n_cancel:], ex.take_dir))
del a.writes_left
def _bw(*x, **k):
    raise ApiError(429, "WRITE_BUDGET_WAIT", "would wait")
a.place_batch = _bw
r = b.execute_take(ex, 0.30, 0.0, 0.14, now_m)
check("take whose order is refused WRITE_BUDGET_WAIT: no 90 s pending hold, no 'check positions' alert, counted",
      r is True and ex.pending_until <= now_m and not _alerts and b.takes_skipped_budget == 2 and b.takes_total == 0,
      (ex.pending_until - now_m, _alerts, b.takes_total))
a, b = make_bot(); b.cycle()
levels = {"11": (0.50, 100), "12": (0.60, 100)}
a.writes_left = lambda: 4
n_cancel = len(a.sent("cancel_all"))
b.execute_arbitrage("Ohio Senate", ["11", "12"], levels, 10, {}, time.monotonic())
check("arbitrage over 2 legs needs 2n+1 = 5 writes: with 4 left it's skipped before any cancel, counted",
      len(a.sent("cancel_all")) == n_cancel and b.arbs_skipped_budget == 1 and b.arbs_total == 0)
a.writes_left = lambda: 5
a.place_batch = _bw
now_m = time.monotonic()
b.execute_arbitrage("Ohio Senate", ["11", "12"], levels, 10, {}, now_m)
check("...with 5 left it runs; a batch refused WRITE_BUDGET_WAIT sets no pending hold and no alert",
      all(b.ex[e].pending_until <= now_m for e in ("11", "12")) and not _alerts and b.arbs_skipped_budget == 2,
      _alerts)
del a.writes_left
M.alert = _real_alert2
cap2 = _Api(M.CFG, False)
cap2.gap, cap2.wbudget, cap2.BUDGET_WINDOW = 0.0, 1, 30.0
cap2.throttle(write=True); cap2.tl.max_write_wait = 0.1
try:
    cap2.throttle(write=True)
except ApiError:
    pass
b.cycle(); b.write_status(ok=True)
with open(b.cfg.status_file) as f:
    stj = json.load(f)
check("WRITE_BUDGET_WAIT counted (write_budget_wait_total); status.json: it plus takes/arbs skipped for budget",
      cap2.write_budget_wait_total == 1 and stj.get("arbs_skipped_budget") == 2 and "takes_skipped_budget" in stj
      and "write_budget_wait_total" in stj, (cap2.write_budget_wait_total, stj.get("arbs_skipped_budget")))

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
