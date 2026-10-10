"""
Start-up self-test at 100% capital (3 Oct live, 08:06 and 08:17 UTC): the second 1-share test order was refused
with HTTP 400 "Insufficient available funds" and the bot stopped with exit code 3. A funds refusal now counts as
BUSY (retry selftest_retry_seconds later, quoting meanwhile); any other refusal still stops the bot.

Run:  python tests/test_startup_selftest.py      (exit code 0 = all passed)
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


M.util.alert = lambda *a, **k: None
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

print("--- one funds refusal + one real rejection = rejected (exit 3)")
a, b = make_bot()
a.place_batch = lambda orders: [{"index": 0, "ok": False, "status": 400, "data": {"error": MSG}},
                                {"index": 1, "ok": False, "status": 400, "data": {"error": {"message": "Invalid price"}}}]
ok, code = run(b)
check("funds refusal + 'Invalid price': exit code 3, not busy", code == EXIT_FATAL, (ok, code))
a, b = make_bot()
a.place_batch = lambda orders: [{"index": 0, "ok": False, "status": 400, "data": {"error": {"message": "Market closed"}}},
                                {"index": 1, "ok": False, "status": 400, "error": MSG}]
ok, code = run(b)
check("'Market closed' + funds refusal (other order): exit code 3", code == EXIT_FATAL, (ok, code))

print("--- funds refusals back off: 60 s, doubling to 30 min, one alert per doubling, reset otherwise")
ALERTS = []
M.util.alert = lambda msg, *a, **k: ALERTS.append(msg)
a, b = make_bot()
a.place_batch = refusing(a, SHAPES["data.error string"], 99, "both")
waits, alerts_per = [], []
for _ in range(9):
    n0 = len(ALERTS)
    ok, code = run(b)
    waits.append(round(b.selftest_next - time.monotonic()))
    alerts_per.append(len(ALERTS) - n0)
check("waits 60, 120, 240, 480, 960, 1800, 1800, 1800, 1800",
      waits == [60, 120, 240, 480, 960, 1800, 1800, 1800, 1800], waits)
check("one alert per new wait, none once at the cap", alerts_per == [1, 1, 1, 1, 1, 1, 0, 0, 0], alerts_per)
check("alert text", ALERTS and ALERTS[0] == "self-test waiting for free cash (insufficient funds), retrying in 60 s"
      and ALERTS[5].endswith("retrying in 1800 s"), ALERTS[:1])
check("status: selftest_state waiting_funds", b.selftest_state() == "waiting_funds", b.selftest_state())
a.place_batch = lambda orders: (_ for _ in ()).throw(M.ApiError(503, "SERVICE_UNAVAILABLE", "busy"))
ok, code = run(b)
check("a busy (503) attempt resets the back-off: next try in 60 s, state not waiting_funds",
      code is None and ok is False and 55 < b.selftest_next - time.monotonic() <= 60 and b.selftest_funds_wait == 0
      and b.selftest_state() != "waiting_funds", (b.selftest_next - time.monotonic(), b.selftest_state()))
a.place_batch = refusing(a, SHAPES["data.error string"], 99, "both")
ok, code = run(b)
check("funds again after the reset: back to 60 s, alerted again", round(b.selftest_next - time.monotonic()) == 60
      and ALERTS[-1].endswith("retrying in 60 s"), ALERTS[-1:])
a.place_batch = FakeApi.place_batch.__get__(a)
ok, code = run(b)
check("cash free: passes, selftest_state passed", ok is True and b.selftest_state() == "passed", b.selftest_state())

print("--- the main loop waits out the back-off (selftest_tick)")
a, b = make_bot()
a.place_batch = refusing(a, SHAPES["data.error string"], 99, "both")
b.selftest_funds_wait = 240.0
b.selftest_next = time.monotonic() + 240
n0 = len(a.sent("batch"))
for _ in range(5):
    b.selftest_tick()
check("no test is placed during a funds wait", len(a.sent("batch")) == n0 and b.selftest_future is None)
M.util.alert = lambda *a, **k: None

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
