"""
Running the bot: the command line, the run loop, the handover, the watchdog, the self-test and the exit codes.

OWNS     `main(argv)`: run (a dry run), run --live, status, cancel; logging; phone alerts; the loop that calls
         Bot.cycle between feed events; SIGINT/SIGTERM (stop and cancel), SIGUSR1 (handover: stop WITHOUT
         cancelling, the next process adopts the orders); the watchdog thread; the start-up self-test.
NEVER    decides a trade. Never starts trading while the kill-switch marker exists, and never exits on a
         handover without leaving the file the next process needs (or, past the handover deadline, at all).
ORIGIN   The 7 October outage: the exchange went silent for 20 minutes; the watchdog cancelled everything and
         exited, systemd restarted the bot, and the restart was clean (README §4.4). The refill retry loop of
         the same day, which spent the whole request budget for two hours, is why every write goes through an
         explicit budget (exchange.py) and a failing cycle is retried on the next one, not in a loop. Exit
         codes tell systemd whether a restart can help: 3 and 4 mean a human must look.
         The covered "sell NO" is probed too (release 6's fix for the 0-cash deadlock); refused, buy-backs fall
         back to "buy YES" for the run, as the old bot did. The NO+NO pair probe went with the pair unwinds.
"""
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

import requests

from mmbot2 import config
from mmbot2.bot import Bot, KILL_FILE, write_json, utcnow
from mmbot2.exchange import ApiError, Client, Feed
from mmbot2.state import Order

log = logging.getLogger("mm2")

EXIT_OK, EXIT_CRASH, EXIT_FATAL, EXIT_KILLED, EXIT_WATCHDOG = 0, 1, 3, 4, 5   # systemd: 3/4 are not restarted
HANDOVER_FILE = "handover.json"
HANDOVER_MAX_AGE_S = 300.0        # an older handover file is ignored: its orders may be gone
HANDOVER_EXIT_S = 150.0           # a handover that has not exited by then exits anyway (the script waits 240 s)
WATCHDOG_CANCEL_S = 20.0          # the watchdog's cancel-all gets this long, then the process exits regardless
MIN_CYCLE_GAP_S = 2.0             # feed events closer than this share one cycle (README §5: 1-2 s reaction)
ERRORS_BEFORE_PULL = 3            # failed cycles in a row before every quote is pulled
SELFTEST_TRIES = 3


def setup_logging(run_dir):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handlers = [logging.StreamHandler(sys.stderr),
                logging.handlers.RotatingFileHandler(os.path.join(run_dir, "mm_bot2.log"),
                                                     maxBytes=20_000_000, backupCount=5)]
    for h in handlers:
        h.setFormatter(fmt)
        logging.getLogger().addHandler(h)
    logging.getLogger().setLevel(logging.INFO)


def notifier(env):
    """A phone alert function (ntfy): never raises, never blocks the cycle for long."""
    def alert(msg):
        log.warning("ALERT %s", msg)
        if not env.alert_url:
            return
        try:
            requests.post(env.alert_url, data=msg.encode(), headers={"Title": "mm_bot2"}, timeout=5)
        except requests.RequestException as e:
            log.warning("alert not sent: %s", e)
    return alert


class NoRefs:
    """Stands in for ref_prices.ReferencePrices when there is no ref_map.json: every market is unpriced."""
    def get(self):
        return {}

    def spreads(self):
        return {}

    def start(self):
        pass

    def stop(self):
        pass


def reference_prices():
    """Polymarket prices through the unchanged companion ref_prices.py, if its mapping file exists."""
    path = os.path.join(config.HERE, "ref_map.json")
    if not os.path.exists(path):
        log.warning("no ref_map.json: every market is unpriced, nothing will be traded")
        return NoRefs()
    sys.path.insert(0, config.HERE)
    from ref_prices import ReferencePrices
    return ReferencePrices(path)


class Runner:
    """The run loop around one Bot."""

    def __init__(self, bot, env):
        self.bot, self.env = bot, env
        self.handover = False                     # set by SIGUSR1: exit without cancelling
        self.errors = 0                           # failed cycles in a row

    def request_stop(self, *_):
        self.handover = False                     # a plain stop always cancels, even after a handover request
        self.bot.running = False

    def request_handover(self, *_):
        log.info("handover requested: stopping without cancelling; the next start adopts the orders")
        self.handover, self.bot.running = True, False
        timer = threading.Timer(HANDOVER_EXIT_S, lambda: os._exit(EXIT_OK))
        timer.daemon = True
        timer.start()

    def watchdog(self):
        """No cycle for watchdog_s: cancel everything (bounded) and exit for systemd (the 7 Oct outage)."""
        while True:
            time.sleep(10)
            since = time.monotonic() - self.bot.last_cycle_mono
            if self.bot.running and since >= self.bot.s.watchdog_s:
                self.bot.alert(f"WATCHDOG: no cycle for {since:.0f} s: cancelling everything and exiting")
                t = threading.Thread(target=self.bot.cancel_everything, daemon=True)
                t.start()
                t.join(WATCHDOG_CANCEL_S)
                os._exit(EXIT_WATCHDOG)

    def run_cycle(self):
        """One cycle; a failure is retried on the next cycle, and after a few in a row the quotes are pulled."""
        try:
            self.bot.cycle()
            self.errors = 0
        except ApiError as e:
            if e.fatal:
                raise
            self.cycle_failed(e)
        except Exception as e:                    # a bug in one cycle must not leave quotes unmanaged
            log.exception("cycle failed")
            self.cycle_failed(e)

    def cycle_failed(self, e):
        self.errors += 1
        log.error("cycle failed (%d in a row): %s", self.errors, e)
        if self.errors == ERRORS_BEFORE_PULL:
            self.bot.alert(f"{self.errors} failed cycles in a row ({e}): pulling every quote")
            self.bot.cancel_everything()

    def wait(self, started):
        """Sleep until a feed event (at least MIN_CYCLE_GAP_S after the last start) or cycle_s."""
        time.sleep(max(0.0, started + MIN_CYCLE_GAP_S - time.monotonic()))
        wake = self.bot.feed.wake if self.bot.feed else threading.Event()
        wake.wait(max(0.0, started + self.bot.s.cycle_s - time.monotonic()))

    def loop(self):
        while self.bot.running:
            started = time.monotonic()
            self.run_cycle()
            if self.bot.running:
                self.wait(started)

    def finish(self):
        """Handover: write the file and leave the orders; otherwise cancel everything."""
        self.bot.save()
        if self.handover and self.bot.live:
            write_json(os.path.join(self.env.run_dir, HANDOVER_FILE),
                       {"t": time.time(), "orders": [o.oid for o in self.bot.orders.values()]})
            log.info("handover: %d orders left resting", len(self.bot.orders))
        elif not self.bot.killed:
            self.bot.cancel_everything()
        if self.bot.feed:
            self.bot.feed.stop()
        self.bot.refs.stop()


def adopted_handover(run_dir):
    """True if the previous process handed over recently: its orders are ours, not to be cancelled."""
    path = os.path.join(run_dir, HANDOVER_FILE)
    try:
        with open(path) as f:
            info = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return False
    return 0 <= time.time() - float(info.get("t", 0)) <= HANDOVER_MAX_AGE_S


def self_test(client, markets):
    """Live start-up check that the exchange behaves as the bot assumes: two 1-share orders at the extreme
    prices (they cannot trade at a loss of more than half a cent), read back in YES terms, then cancelled.
    Returns None if it passed, or the problem."""
    quiet = sorted(e for e, m in markets.items() if m.state is not None) or sorted(markets)
    eid = quiet[0]
    probe = [Order(eid, True, 0.005, 1, "selftest"), Order(eid, False, 0.995, 1, "selftest")]
    placed = client.place(probe, {})
    if any(p.error for p in placed):
        return f"self-test orders refused: {[p.error for p in placed]}"
    listed = {o.oid: o for o in client.open_orders() if o.eid == eid}
    problems = [f"order {p.oid} not listed as a {'bid' if p.order.is_bid else 'ask'} at {p.order.price}"
                for p in placed if p.oid not in listed or listed[p.oid].is_bid != p.order.is_bid
                or abs(listed[p.oid].price - p.order.price) > 1e-6]
    for p in placed:
        client.cancel(p.oid)
    return "; ".join(problems) or None


def covered_no_test(client, positions):
    """Live start-up check of the covered "sell NO" (release 6, the fix for the 0-cash deadlock): one share of a NO
    holding offered at a NO price of 0.995, which cannot trade, read back as our bid at 0.005, then cancelled.
    None if it passed or there is no NO to test with; else the problem."""
    held = sorted(e for e, q in positions.items() if q <= -1)
    if not held:
        return None
    probe = Order(held[0], True, 0.005, 1, "selftest")
    placed = client.place([probe], positions)[0]
    if placed.error:
        return f"refused: {placed.error}"
    listed = {o.oid: o for o in client.open_orders()}
    client.cancel(placed.oid)
    o = listed.get(placed.oid)
    return None if o is not None and o.is_bid and abs(o.price - 0.005) < 1e-6 else "not listed as our bid at 0.005"


def start(env, live):
    """The Bot wired to the exchange, the feed and Polymarket; None after a fatal start-up problem."""
    s = config.SettingsFile(env.settings_path).load(config.Settings(), notifier(env)) or config.Settings()
    client = Client(env, s, live)
    t = client.tournament()
    refs = reference_prices()
    refs.start()
    feed = Feed(client, t["id"])
    feed.start()
    bot = Bot(client, feed, refs, s, env, live, alert=notifier(env))
    bot.tournament_id = t["id"]
    bot.load_markets(time.monotonic())
    return bot


def run(env, live):
    if os.path.exists(os.path.join(env.run_dir, KILL_FILE)):
        log.critical("%s exists: the kill switch fired; delete it to trade again", KILL_FILE)
        return EXIT_KILLED
    bot = start(env, live)
    if live and not adopted_handover(env.run_dir):
        bot.cancel_everything()                   # a plain start begins from a clean slate
    if live:
        for attempt in range(SELFTEST_TRIES):
            try:
                problem = self_test(bot.client, bot.markets)
                break
            except ApiError as e:                 # a busy exchange is not a failure: try again shortly
                problem = f"exchange busy ({e})"
                time.sleep(30)
        if problem:
            bot.alert(f"SELF-TEST FAILED: {problem}: not trading")
            return EXIT_FATAL
        no_problem = covered_no_test(bot.client, bot.client.positions())
        if no_problem:                            # as the old bot: alert, buy back as "buy YES", keep running
            bot.client.covered_no = False
            bot.alert(f"covered 'sell NO' {no_problem}: NO holdings are bought back as 'buy YES' this run")
    runner = Runner(bot, env)
    signal.signal(signal.SIGINT, runner.request_stop)
    signal.signal(signal.SIGTERM, runner.request_stop)
    signal.signal(signal.SIGUSR1, runner.request_handover)
    threading.Thread(target=runner.watchdog, name="watchdog", daemon=True).start()
    log.info("mm_bot2 %s: %d markets", "LIVE" if live else "DRY RUN (logs the orders it would send)",
             len(bot.markets))
    try:
        runner.loop()
    finally:
        runner.finish()
    return EXIT_KILLED if bot.killed else EXIT_OK


def status(env):
    """Print the account, the positions and our open orders (read only)."""
    client = Client(env, config.Settings(), live=False)
    client.tournament()
    markets = {m.eid: m for m in client.markets()}
    positions = client.positions()
    orders = client.open_orders()
    acct = client.account()
    print(f"account value {acct.value}  free cash {acct.cash}  start {acct.start}")
    for e, q in sorted(positions.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {markets[e].label if e in markets else e:32s} {q:+10.0f}")
    print(f"{len(orders)} open orders")
    return EXIT_OK


def cancel(env):
    client = Client(env, config.Settings(), live=True)
    client.tournament()
    ok = client.cancel_all()
    print("cancelled everything" if ok else "cancel-all incomplete: run again")
    return EXIT_OK if ok else EXIT_CRASH


def main(argv):
    env = config.env()
    if not env.api_key or not env.slug:
        print("SUPERMARKET_API_KEY and TOURNAMENT_SLUG must be set (.env next to mm_bot2.py)", file=sys.stderr)
        return EXIT_FATAL
    command = argv[0] if argv else "run"
    setup_logging(env.run_dir)
    try:
        if command == "run":
            return run(env, live="--live" in argv)
        if command == "status":
            return status(env)
        if command == "cancel":
            return cancel(env)
    except ApiError as e:
        if e.fatal:
            log.critical("fatal API error: %s", e)
            return EXIT_FATAL
        raise
    print("usage: mm_bot2.py run [--live] | status | cancel", file=sys.stderr)
    return EXIT_FATAL
