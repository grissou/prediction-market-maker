#!/usr/bin/env python3
"""The rewritten trading bot (mmbot2/). Same command line as mm_bot.py:
    python3 mm_bot2.py run           dry run: reads the real feed, logs each order it would send (DRY PLACE)
    python3 mm_bot2.py run --live    trade for real
    python3 mm_bot2.py status        account, positions, open orders
    python3 mm_bot2.py summary       send the phone summary now
    python3 mm_bot2.py cancel [--all]  cancel our open orders (--all: every order on the account)
Files go to run2/ next to this script (MMBOT2_DIR to change it); live settings in run2/settings_override.json
(MMBOT2_SETTINGS to change it). Start with deploy/RUNBOOK.md, section E."""
import sys

from mmbot2.ops import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
