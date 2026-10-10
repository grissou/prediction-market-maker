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
    check("an unknown key is refused with an alert", any("bogus" in a for a in ALERTS))


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


if __name__ == "__main__":
    for t in (test_cycle, test_dry_run, test_fill_and_state, test_kill_switch, test_settings_file, test_ops):
        try:
            t()
        except Exception as e:                     # a crash is a failure of that test, not of the suite
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran without an exception", False, repr(e))
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)
