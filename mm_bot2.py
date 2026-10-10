#!/usr/bin/env python3
"""The rewritten trading bot (mmbot2/). Same command line as mm_bot.py:
    python3 mm_bot2.py run           dry run: reads the real feed, logs the orders it WOULD send
    python3 mm_bot2.py run --live    trade for real
    python3 mm_bot2.py status        account, positions, open orders
    python3 mm_bot2.py cancel        cancel every open order
Files go to run2/ next to this script (MMBOT2_DIR to change it); live settings in run2/settings_override.json
(MMBOT2_SETTINGS to change it). Start with deploy/RUNBOOK.md, section E."""
import sys

from mmbot2.ops import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
