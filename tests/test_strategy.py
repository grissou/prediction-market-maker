"""
Offline tests for the strategy changes on claude/pr2-strategy (Builder run): pure quoting functions, the
Bot wiring, and a short run of the strategy simulator (tests/strategy_sim.py).

Run:  python tests/test_strategy.py      (exit code 0 = all passed)
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, FakeFeed, FakeRefs, lvl, make_bot, market, run_cycles   # noqa: E402,F401
import mm_bot as M                                        # noqa: E402
from mm_bot import *                                      # noqa: E402,F401,F403
import strategy_sim as S                                  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


# =============================================================================================
# SIMULATOR SANITY

agg, rows = S.run_many(1, 0.5, "quiet")
check("simulator: runs, fills happen, no duplicate quotes", agg["fills"] > 0 and agg["dups_max"] == 0, agg)
a1, _ = S.run_many(1, 0.5, "quiet")
check("simulator: deterministic for a seed", a1 == agg)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
