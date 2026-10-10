"""
Offline tests for the reduce-only "why" diagnostics of compute_quote (ex.ro_clip). The Package 6 candidates that
this file covered - exit_quotes_in_reduce_only and pair_passive_in_reduce_only - were removed on simplify (never
enabled live), so only the live diagnostics remain. No network.

Run:  python tests/test_reduce_only_diagnostics.py      (exit code 0 = all passed)
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- the why dict (reduce-only clip diagnostics)")
q = M.quoting.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True)
w = {}
q2 = M.quoting.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True, why=w)
check("the why dict never changes the quote (long 500, race-net 0: both sides clipped)",
      q == q2 and w == {"ro_clip": "bid ask"}, (q, q2, w))
w = {}
M.quoting.compute_quote(0.5, 500, 0, 0.45, 0.55, M.Config(), reduce_only=True, no_ask=True, why=w)
check("...a blocked side is not 'clipped'", w == {"ro_clip": "bid"}, w)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
