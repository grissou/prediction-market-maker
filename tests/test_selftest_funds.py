"""
Start-up self-test at 100% capital (3 Oct live, 08:06 and 08:17 UTC): the second 1-share test order was refused
with HTTP 400 "Insufficient available funds" and the bot stopped with exit code 3. A funds refusal now counts as
BUSY (retry selftest_retry_seconds later, quoting meanwhile); any other refusal still stops the bot.

Run:  python tests/test_selftest_funds.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import FakeApi, make_bot                      # noqa: E402
import mm_bot as M                                        # noqa: E402
from mm_bot import Bot, EXIT_FATAL                        # noqa: E402

failures = []
count = {"n": 0}


def check(name, cond, detail=None):
    count["n"] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {detail}"))
    if not cond:
        failures.append(name)


M.alert = lambda *a, **k: None
MSG = "Insufficient available funds"
SHAPES = {
    "data.error string": lambda: {"data": {"error": MSG}},
    "data.error dict (message)": lambda: {"data": {"error": {"code": "INSUFFICIENT_FUNDS", "message": MSG}}},
    "top-level error": lambda: {"error": MSG, "data": None},
}


def refusing(a, shape, times, which="second", message=MSG):
    """Wrap the fake's place_batch: the first `times` batches get the chosen order(s) refused with a 400."""
    real = FakeApi.place_batch.__get__(a)
    state = {"left": times}

    def pb(orders):
        if state["left"] <= 0:
            return real(orders)
        state["left"] -= 1
        a.log("batch", len(orders))
        res = []
        for k, o in enumerate(orders):
            refuse = which == "both" or k == 1
            if refuse:
                body = shape()
                if message != MSG:
                    body = {"data": {"error": {"code": "X", "message": message}}}
                res.append({"index": k, "ok": False, "status": 400, **body})
            else:                                          # the first one was accepted and rests
                res.extend(r for r in real([o]))
                res[-1]["index"] = k
        return res
    return pb


def run(b):
    try:
        logging.disable(logging.CRITICAL)
        return b.self_test(), None
    except SystemExit as e:
        return None, e.code
    finally:
        logging.disable(logging.NOTSET)


print("--- self-test refused for funds = busy, retried, passes once cash is free")
for name, shape in SHAPES.items():
    for which in ("second", "both"):
        a, b = make_bot()
        a.place_batch = refusing(a, shape, 1, which)
        ok, code = run(b)
        check(f"{name} ({which} refused): no exit, 'not yet', retry ~60 s later",
              code is None and ok is False and 55 < b.selftest_next - time.monotonic() <= 60, (ok, code))
        check(f"{name} ({which} refused): nothing left resting, test exchange not blocked",
              not a.orders and all(x.pending_until < time.monotonic() for x in b.ex.values()), a.orders)
        ok2, code2 = run(b)
        check(f"{name} ({which} refused): the retry passes once cash is free",
              ok2 is True and code2 is None and b.selftest_passed, (ok2, code2))

print("--- any other refusal still stops the bot")
a, b = make_bot()
a.place_batch = refusing(a, SHAPES["data.error dict (message)"], 99, "second", message="Invalid price")
ok, code = run(b)
check("400 'Invalid price' on one test order: exit code 3", code == EXIT_FATAL, (ok, code))
a, b = make_bot()
a.place_batch = refusing(a, SHAPES["data.error string"], 99, "both", message="Market closed")
ok, code = run(b)
check("400 'Market closed' on both test orders: exit code 3", code == EXIT_FATAL, (ok, code))
a, b = make_bot()
a.place_batch = lambda orders: [{"index": k, "ok": False, "status": 400, "data": {"error": {"code": "X"}}}
                                for k in range(len(orders))]
ok, code = run(b)
check("400 with no message at all: exit code 3 (unchanged)", code == EXIT_FATAL, (ok, code))
a, b = make_bot()
check("an ordinary self-test still passes first time", run(b) == (True, None) and b.selftest_passed)

print("--- Bot.selftest_funds_refusal")
f = Bot.selftest_funds_refusal
cases = [
    ("None", None, False),
    ("{}", {}, False),
    ("ok result with the words in it", {"ok": True, "data": {"error": MSG}}, False),
    ("data.error string", {"ok": False, "status": 400, "data": {"error": MSG}}, True),
    ("data.error.message", {"ok": False, "data": {"error": {"message": MSG}}}, True),
    ("data.error.error", {"ok": False, "data": {"error": {"error": MSG}}}, True),
    ("data.error.detail", {"ok": False, "data": {"error": {"detail": MSG}}}, True),
    ("data.error.error.message (nested twice)", {"ok": False, "data": {"error": {"error": {"message": MSG}}}}, True),
    ("top-level error string", {"ok": False, "error": MSG}, True),
    ("top-level error dict", {"ok": False, "error": {"message": MSG}}, True),
    ("case-insensitive", {"ok": False, "data": {"error": "INSUFFICIENT AVAILABLE FUNDS"}}, True),
    ("'insufficient' without 'fund'", {"ok": False, "data": {"error": "Insufficient liquidity"}}, False),
    ("'funds' without 'insufficient'", {"ok": False, "data": {"error": "Funds locked"}}, False),
    ("only a code, no message", {"ok": False, "data": {"error": {"code": "INSUFFICIENT_FUNDS"}}}, False),
    ("data is None", {"ok": False, "data": None}, False),
    ("data.error None", {"ok": False, "data": {"error": None}}, False),
    ("data is a string", {"ok": False, "data": MSG}, False),
]
for name, r, want in cases:
    check(f"helper: {name} -> {want}", f(r) is want, f(r))

print(f"\n{count['n'] - len(failures)}/{count['n']} passed")
sys.exit(1 if failures else 0)
