"""
The bot and the run loop on the fake exchange: one cycle end to end, a dry run sends nothing, a second cycle on an
unchanged world sends nothing new, the bot never crosses itself, the kill switch, live settings, saved state,
status.json, the handover file and the pull after failed cycles.
Run:  python3 tests2/test_bot.py      (exit code 0 = all passed)
"""
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import two_race_world                                     # noqa: E402
from mmbot2 import config, ops                                       # noqa: E402
from mmbot2.bot import Bot, KILL_FILE, STATE_FILE, STATUS_FILE       # noqa: E402
from mmbot2.state import Order                                       # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
RESULTS, ALERTS = [], []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def make(live=True, cash=None, run_dir=None):
    client, feed, refs = two_race_world(cash=cash, live=live)
    d = run_dir or tempfile.mkdtemp(prefix="mm2bot")
    env = config.Env("k", "t", "http://x", "", d, os.path.join(d, "settings_override.json"))
    bot = Bot(client, feed, refs, config.Settings(), env, live, alert=ALERTS.append)
    bot.load_markets(0.0)
    return client, feed, bot


def writes(client):
    return [c for c in client.calls if c in ("place", "cancel", "cancel_all")]


def test_cycle():
    client, feed, bot = make()
    bot.cycle()
    check("a live cycle places orders", len(client.orders) > 0, str(client.calls[-5:]))
    check("every resting order is tracked by the bot", set(client.orders) == set(bot.orders),
          f"{sorted(client.orders)} vs {sorted(bot.orders)}")
    check("every order carries its strategy tag", all(o.tag for o in bot.orders.values()))
    before = len(writes(client))
    bot.last_full = -1e9
    bot.cycle()
    check("a second cycle on an unchanged world sends nothing", len(writes(client)) == before,
          str(writes(client)[before:]))
    check("the bot never tried to cross itself", client.self_crosses == 0)
    st = json.load(open(os.path.join(bot.env.run_dir, STATUS_FILE)))
    for key in ("account_value", "ev_outcome", "realised_pnl", "reduce_only", "orders_resting", "mm_funding",
                "harvest", "state_caps", "alloc"):
        check(f"status.json has {key}", key in st)
    check("orders_resting matches", st["orders_resting"] == len(client.orders))


def test_dry_run():
    client, feed, bot = make(live=False)
    bot.cycle()
    check("a dry run sends no write", not writes(client), str(writes(client)))
    check("a dry run simulates its orders", len(bot.orders) > 0)
    n = len(bot.orders)
    bot.cycle()
    check("a dry run keeps its simulated orders", len(bot.orders) == n)


def test_fill_and_state():
    client, feed, bot = make()
    bot.cycle()
    eid, o = next((o.eid, o) for o in bot.orders.values() if o.tag == "mm")
    client.fill(eid, o.is_bid, 50)
    feed.push(account=True)
    bot.last_account = -1e9                        # account reads are spaced within the budget (ACCOUNT_SHARE)
    bot.cycle()
    check("a fill reaches the position", bot.positions.get(eid) == (50 if o.is_bid else -50), str(bot.positions))
    check("the fill is attributed to market making", bot.inventory.status() is not None)
    saved = json.load(open(os.path.join(bot.env.run_dir, STATE_FILE)))
    check("state.json keeps order tags", all(k in saved["tags"] for k in bot.orders))
    _, _, again = make(run_dir=bot.env.run_dir)
    check("a restart restores the tags", again.tags.keys() >= bot.orders.keys())


def test_kill_switch():
    client, feed, bot = make()
    bot.cycle()
    client.cash -= 40000                           # the account falls 40% below the start
    for _ in range(3):
        bot.last_full = -1e9
        bot.cycle()
    check("the kill switch fires after two bad readings", bot.killed and not bot.running)
    check("the kill switch cancels everything", not client.orders)
    check("the kill switch leaves its marker", os.path.exists(os.path.join(bot.env.run_dir, KILL_FILE)))
    check("ops refuses to run while the marker exists", ops.run(bot.env, live=True) == ops.EXIT_KILLED)


def test_settings_file():
    client, feed, bot = make()
    with open(bot.env.settings_path, "w") as f:
        json.dump({"mm_enabled": False, "value_quotes_enabled": False, "ladder_enabled": False, "bogus": 1}, f)
    bot.cycle()
    check("the settings file switches strategies off", not bot.s.mm_enabled and not bot.s.ladder_enabled)
    check("a strategy that is off places nothing", not any(o.tag in ("mm", "ladder", "value")
                                                           for o in bot.orders.values()))
    check("an unknown key is refused, in the log only (no phone alert)", not any("bogus" in a for a in ALERTS)
          and "bogus" not in config.asdict(bot.s))


def test_ops():
    client, feed, bot = make()
    bot.cycle()
    runner = ops.Runner(bot, bot.env)
    runner.handover, bot.running = True, False
    runner.finish()
    check("a handover leaves the orders resting", len(client.orders) > 0)
    check("a fresh handover file is adopted once", ops.adopted_handover(bot.env.run_dir)
          and not ops.adopted_handover(bot.env.run_dir))
    client, feed, bot = make()
    bot.cycle()
    runner = ops.Runner(bot, bot.env)
    client.fail_next(100, "read")
    for _ in range(ops.ERRORS_BEFORE_PULL):
        bot.last_full = -1e9
        runner.run_cycle()
    client.faults = []
    check("failed cycles in a row pull every quote", not client.orders, str(client.orders))
    runner = ops.Runner(bot, bot.env)
    bot.running = True
    runner.request_stop()
    runner.finish()
    check("a plain stop cancels everything", not client.orders)
    check("the self-test passes on a well-behaved exchange", ops.self_test(client, bot.markets) is None)
    check("no NO held: the covered 'sell NO' test has nothing to do", ops.covered_no_test(client, {}) is None)
    client.inv["11"] = -100
    check("the covered 'sell NO' test passes when the exchange takes it",
          ops.covered_no_test(client, dict(client.inv)) is None and not client.orders)
    client.refuse_no_sell = "Invalid side for this order"
    check("a refused covered 'sell NO' is reported", ops.covered_no_test(client, dict(client.inv)) is not None)
    client.refuse_no_sell, client.covered_no = None, False
    placed = client.place([Order("11", True, 0.10, 300, "mm")], dict(client.inv))
    check("with the fallback a buy-back goes out whole as 'buy YES'", placed and placed[0].order.size == 300, placed)


def test_recorder():
    import sqlite3
    client, feed, bot = make()
    bot.cycle()
    bot.cycle()
    db = sqlite3.connect(os.path.join(bot.env.run_dir, "market_data.sqlite"))
    rows = db.execute("select eid, best_bid, best_ask, fair_value, reference, our_bid, our_ask, position "
                      "from snapshots").fetchall()
    check("the recorder writes one row per priced market a minute", len(rows) == 4, rows)
    check("with the bulk top, Polymarket and our quotes", all(r[1] is not None and r[4] is not None for r in rows)
          and any(r[5] is not None for r in rows), rows)
    check("and an account row", db.execute("select count(*) from account").fetchone()[0] == 1)


def test_summary_and_alerts():
    from datetime import datetime, timezone
    from mmbot2 import notify
    st = {"account_value": 104812.0, "start_balance": 100000.0, "ev_outcome": 112703.0, "ev_change_24h": 2012.0,
          "mm_profit_24h": 83.4, "tilt_s": 0.082, "reduce_only": False, "rate_limits_period": 0, "errors_period": 0}
    lb, sc = {"myRank": 156, "total": 1396}, [{"marketType": "global", "smartScoreDecayed": 25.07, "rank": 174}]
    text = notify.summary_text(st, lb, sc)
    want = ("Balance 104.8k (+4.8%) · leaderboard 156 of 1,396 · Smart Score 25.1 (rank 174)\n"
            "EV at settlement 112.7k (+2.0k 24h)\nMM profit 24h +83 (realised) · tilt 8.2%\nOK")
    check("the summary is the brief's four lines", text == want, repr(text))
    bad = dict(st, reduce_only=True, reduce_only_since="13:10", rate_limits_period=3, errors_period=2,
               ev_change_24h=None, tilt_s=None)
    text = notify.summary_text(bad, None, None)
    check("unknown figures are left out; the status line names what is wrong",
          text == "Balance 104.8k (+4.8%)\nEV at settlement 112.7k\nMM profit 24h +83 (realised)\n"
                  "REDUCE-ONLY since 13:10 · 3 rate limits · 2 errors", repr(text))
    check("a summary is due on the even hour UTC, once",
          notify.due(datetime(2026, 10, 10, 14, 0, 5, tzinfo=timezone.utc), 2, None) == "2026-10-10 14"
          and notify.due(datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc), 2, "2026-10-10 14") is None
          and notify.due(datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc), 2, "2026-10-10 14") is None)
    from mmbot2 import mm
    inv, t0 = mm.Inventory(), 1.791e9
    inv.add_fill("21", 50, 0.40, t0, True)
    inv.add_fill("21", -50, 0.45, t0 + 60, False)
    inv.add_fill("22", -20, 0.60, t0 - 90000, True)
    inv.add_fill("22", 20, 0.50, t0 - 90000, False)
    check("MM profit 24h is the round trips closed in the last 24 h", abs(inv.profit_24h(t0 + 120) - 2.5) < 1e-9,
          inv.trips)
    check("and survives a restart", abs(mm.Inventory(inv.to_dict()).profit_24h(t0 + 120) - 2.5) < 1e-9)
    sent = []
    phone = notify.Notifier("")
    phone.send = lambda msg, title=None: sent.append(msg)
    phone.alert("RATE LIMITED: 429 pause #1")
    phone.alert("RATE LIMITED: 429 pause #2")
    phone.alert("REDUCE-ONLY ON: x")
    check("the same cause alerts once in 30 minutes", sent == ["RATE LIMITED: 429 pause #1", "REDUCE-ONLY ON: x"],
          sent)
    client, feed, bot = make()
    ALERTS.clear()
    bot.cycle()
    client.rate_limited, client.pauses = 2, 1
    feed.ok = False
    bot.cycle()
    check("a rate-limit penalty alerts", any(a.startswith("RATE LIMITED") for a in ALERTS), ALERTS)
    bot.feed_down -= 301
    bot.cycle()
    bot.cycle()
    check("the feed down for 5 minutes alerts once", sum(a.startswith("REALTIME FEED DOWN") for a in ALERTS) == 1,
          ALERTS)
    st = json.load(open(os.path.join(bot.env.run_dir, STATUS_FILE)))
    check("status.json carries the summary's figures", st["rate_limits_period"] == 2 and st["mm_profit_24h"] == 0
          and st["start_balance"] == 100000.0 and "ev_change_24h" in st, st)
    check("nothing else alerted", all(a.startswith(("RATE LIMITED", "REALTIME FEED DOWN")) for a in ALERTS),
          ALERTS)


if __name__ == "__main__":
    for t in (test_summary_and_alerts, test_recorder, test_cycle, test_dry_run, test_fill_and_state, test_kill_switch, test_settings_file, test_ops):
        try:
            t()
        except Exception as e:                     # a crash is a failure of that test, not of the suite
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran without an exception", False, repr(e))
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)
