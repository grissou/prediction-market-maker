#!/usr/bin/env python3
"""The trading bot (package mmbot/). Command line:
    python3 mm_bot.py run           dry run: reads the real feed, logs each order it would send (DRY PLACE)
    python3 mm_bot.py run --live    trade for real
    python3 mm_bot.py status        account, positions, open orders
    python3 mm_bot.py summary       send the phone summary now
    python3 mm_bot.py cancel [--all]  cancel our open orders (--all: every order on the account)
Files go to run/ next to this script (MMBOT_DIR to change it); live settings in run/settings_override.json
(MMBOT_SETTINGS to change it). Start with deploy/RUNBOOK.md."""
import sys

from mmbot.ops import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
