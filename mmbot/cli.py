"""
The command line: argument parsing, logging set-up and the sub-commands (tournaments, markets,
status, run [--live], cancel, report, analyze, summary).

setup_logging configures the root logger once; main() builds the Api and the Bot and dispatches on
the sub-command. mm_bot.py is the one-line entry point that calls main().

This module only wires things together: it holds no trading logic, decides nothing about prices or
sizes, and is never imported by any other mmbot module.
"""
import json
import logging.handlers
import sys

from mmbot import util
from mmbot import config
from mmbot import exchange
from mmbot import quoting
from mmbot import measure
from mmbot import bot
from mmbot.util import bot_path, log
from mmbot.config import CFG
from mmbot.exchange import Api, ApiError, fmt
from mmbot.quoting import DEFAULT_BANKROLL
from mmbot.measure import analyze, report
from mmbot.bot import Bot


# =============================================================================================
# CLI
# =============================================================================================

def setup_logging(to_file):
    """Log to the terminal (which systemd/journalctl also captures) and, for `run`, to a rotating
    file. Timestamps are UTC with the date, to match the API and make multi-day logs readable."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt.converter = util.time.gmtime
    handlers = [logging.StreamHandler()]
    if to_file and CFG.log_file:
        handlers.append(logging.handlers.RotatingFileHandler(
            bot_path(CFG.log_file), maxBytes=int(CFG.log_max_mb * 1e6), backupCount=CFG.log_backups))
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    setup_logging(to_file=(cmd == "run"))
    if cmd == "report":
        return report(bot_path(CFG.fills_csv))
    if not CFG.slug and cmd != "tournaments":
        util.fatal("TOURNAMENT_SLUG is not set (find it with: python mm_bot.py tournaments)")
    live = "--live" in sys.argv or cmd == "cancel"
    api = Api(CFG, live)

    if cmd == "tournaments":
        print(json.dumps(api.get("/tournaments", limit=100), indent=2))
    elif cmd == "markets":
        for m in api.markets():
            print(f"\n[{m['id']}] {m.get('title')}  multi={m.get('isMultiOutcome')} closes={m.get('settlementDate')}")
            for e in m.get("exchanges", []):
                print(f"   exchange {e['id']:>8}  {str(e.get('option')):<24} last={e.get('latestPrice')}")
    elif cmd == "status":
        t = api.tournament()
        print(json.dumps({k: t.get(k) for k in ("name", "status", "startDate", "endDate", "myBalance", "initialBalance")}, indent=2))
        try:
            print(json.dumps(api.pnl(), indent=2))
        except ApiError as e:
            print("P&L unavailable:", e)
        print(json.dumps(api.positions(), indent=2))
        orders = api.open_orders(t["id"])
        print(f"{len(orders)} open orders")
        print(json.dumps(orders[:20], indent=2))
    elif cmd == "cancel":
        print("done" if api.cancel_all(api.tournament()["id"]) else "some orders may remain - check UI")
    elif cmd == "analyze":
        hours = float(sys.argv[2]) if len(sys.argv) > 2 else None
        print("\n".join(analyze(bot_path(CFG.fills_csv), bot_path(CFG.record_file), hours)))
        return
    elif cmd == "summary":
        t = api.tournament()
        title, message = measure.build_summary(api, bot_path(CFG.fills_csv), float(t.get("initialBalance") or DEFAULT_BANKROLL))
        print(title, message, sep="\n")
        if not CFG.alert_url:
            print("\n(not sent: ALERT_URL isn't set)")
        else:
            print("\nsent to your phone" if util.notify(message, title=title, tags="chart_with_upwards_trend")
                  else "\nSENDING FAILED - check ALERT_URL and your internet connection")
    elif cmd == "run":
        log.info("Mode: %s", "LIVE" if live else "DRY RUN (add --live to trade)")
        bot = Bot(api, CFG)
        bot.run()
        sys.exit(bot.exit_code)
    else:
        print(sys.modules["__main__"].__doc__)
