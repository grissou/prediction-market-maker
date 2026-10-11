"""
exchange.py offline: budgets and the 429 pause on a stubbed session and a fake clock, the wire format both ways,
own-order stripping, market parsing, ioc, fills, account, and a sanity run of the FakeClient.
Run: python3 tests2/test_exchange.py   (prints PASS/FAIL per check, then N/N passed; exit 0 iff all pass)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                                   # noqa: E402

from mmbot2 import exchange as X                                  # noqa: E402
from mmbot2.config import Env, Settings                           # noqa: E402
from mmbot2.state import Order                                    # noqa: E402
from tests2.fakes import two_race_world                           # noqa: E402

results = []


def check(name, ok):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)


class FakeTime:
    """Stands in for the time module inside exchange.py: sleeping only moves the clock."""
    def __init__(self):
        self.t = 1000.0
    def monotonic(self): return self.t
    def sleep(self, s): self.t += max(0.0, s)


class Reply:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.content = json.dumps(data or {}).encode()
        self.text = self.content.decode()
    def json(self): return json.loads(self.content)


class StubSession:
    """requests.Session stand-in: answers from `script` (a list of Reply or exceptions, then `default`)."""
    def __init__(self, script=(), default=None):
        self.headers, self.script, self.sent = {}, list(script), []
        self.default = default or Reply(200, {})
    def request(self, method, url, timeout=None, params=None, data=None):
        self.sent.append((method, url, params, json.loads(data) if data else None, X.time.monotonic()))
        r = self.script.pop(0) if self.script else self.default
        if isinstance(r, Exception):
            raise r
        return r


def client(script=(), default=None, live=True, rpm=80, wpm=28):
    X.time = FakeTime()
    s = Settings()
    s.requests_per_minute, s.writes_per_minute = rpm, wpm
    env = Env(api_key="k", slug="t", base_url="https://x", alert_url="", run_dir="/tmp", settings_path="/tmp/s")
    c = X.Client(env, s, live, session=StubSession(script, default))
    c.tid = "T"
    return c


def ok_batch(n, traded=0):
    return Reply(200, {"results": [{"index": k, "ok": True, "status": 200,
                                    "data": {"orderId": 500 + k, "quantityTraded": traded}} for k in range(n)]})


def test_budgets():
    c = client(rpm=5)
    for _ in range(5):
        c.get("/x")
    check("five requests use the 5/min budget", c.requests_left() == 0)
    c.get("/x")
    check("the sixth waits for the minute window", c.session.sent[5][4] >= c.session.sent[0][4] + X.WINDOW_S)
    c = client(wpm=3)
    for k in range(3):
        c.cancel(str(k))
    n = len(c.session.sent)
    try:
        c.cancel("9")
        refused = False
    except X.ApiError as e:
        refused = e.code == "WRITE_BUDGET_WAIT"
    check("a write beyond the write budget is refused unsent", refused and len(c.session.sent) == n)
    check("writes_left is 0 then", c.writes_left() == 0)
    c = client()
    c.get("/a"), c.get("/b")
    check("request starts are at least the minimum gap apart",
          c.session.sent[1][4] - c.session.sent[0][4] >= X.MIN_GAP_S - 1e-9)


def test_429():
    c = client([Reply(429, {"error": {"code": "RATE_LIMITED"}}, {"Retry-After": "7"})])
    t0 = X.time.t
    c.get("/x")
    check("429: the resend waits for Retry-After", c.session.sent[1][4] >= t0 + 7)
    check("429: counters", c.rate_limited == 1 and c.pauses == 1)
    check("429: both budgets cut by a quarter, once", c.rpm == 60 + 1 / 60 and c.wpm == 21)
    c = client()
    c.note_429(10.0)
    X.time.sleep(4)
    c.note_429(10.0)                  # another request (the feed's thread) knocked during the pause
    check("a 429 inside a pause extends it and cuts nothing more",
          c.rate_limited == 2 and c.pauses == 1 and c.wpm == 21 and abs(c.pause_left() - 10) < 1e-9)
    c = client([Reply(429, {}, {})])
    t0 = X.time.t
    c.get("/x")
    check("429 without Retry-After pauses the default", c.session.sent[1][4] >= t0 + X.DEFAULT_PAUSE_S)
    c.set_budgets(80, 28)
    check("set_budgets restores", c.rpm == 80 and c.wpm == 28)
    c = client([Reply(429, {}, {"Retry-After": "3"})] * (X.MAX_RETRIES + 1))
    try:
        c.get("/x")
        raised = False
    except X.ApiError as e:
        raised = e.status == 429
    check("persistent 429 raises after the retries", raised and len(c.session.sent) == X.MAX_RETRIES + 1)


def test_retries():
    c = client([Reply(503, {"error": {"code": "SERVICE_UNAVAILABLE"}}), ok_batch(1)])
    out = c.place([Order("11", True, 0.12, 10, "mm")], {})
    keys = [s[3]["idempotencyKey"] for s in c.session.sent]
    check("a 503 is resent with the same idempotency key", len(keys) == 2 and keys[0] == keys[1] and out[0].oid == "500")
    c = client([requests.exceptions.ReadTimeout("slow")])
    out = c.place([Order("11", True, 0.12, 10, "mm")], {})
    check("a write that timed out after sending is not resent, outcome unknown",
          out[0].unknown and out[0].oid is None and len(c.session.sent) == 1)
    c = client(wpm=1)
    c.cancel("1")
    out = c.place([Order("11", True, 0.12, 10, "mm")], {})
    check("a batch beyond the write budget is reported unsent, not unknown",
          not out[0].unknown and "WRITE_BUDGET_WAIT" in out[0].error and len(c.session.sent) == 1)
    c = client([Reply(401, {"error": {"code": "API_KEY_REVOKED"}})])
    try:
        c.place([Order("11", True, 0.12, 10, "mm")], {})
        raised = False
    except X.ApiError as e:
        raised = e.fatal
    check("a fatal code from a batch is raised", raised)
    c = client([requests.exceptions.ConnectionError("down"), Reply(200, {"a": 1})])
    check("a read is retried after a network error", c.get("/x") == {"a": 1})
    c = client([Reply(400, {"error": "Not found"})])
    try:
        c.get("/x")
        msg = None
    except X.ApiError as e:
        msg = e.message
    check("a plain-text error still raises with its message", msg == "Not found")
    check("fatal codes are flagged", X.ApiError(401, "API_KEY_REVOKED", "").fatal)


def test_wire():
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    raw = {"id": 7, "exchangeId": 11, "side": "yes", "action": "buy", "priceLimit": 0.12, "quantity": 10,
           "expirationDate": future}
    o = X.parse_order(raw)
    check("API yes/buy -> our bid", o.is_bid and o.price == 0.12 and o.oid == "7" and o.eid == "11")
    o = X.parse_order(dict(raw, side="no", action="buy", priceLimit=0.825))
    check("API no/buy -> our ask at 1 - p (the NO flip)", not o.is_bid and o.price == 0.175)
    o = X.parse_order(dict(raw, side="no", action="sell", priceLimit=0.3))
    check("API no/sell (covered NO sale) -> our bid at 1 - p", o.is_bid and o.price == 0.7)
    check("API yes/sell -> our ask", not X.parse_order(dict(raw, action="sell")).is_bid)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    check("expired and closed orders are not resting",
          X.parse_order(dict(raw, expirationDate=past)) is None and X.parse_order(dict(raw, open=False)) is None)
    exp = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    w = X.wire_order(Order("11", False, 0.62, 10.0, "mm"), False, "T", exp)
    check("our ask -> yes/sell at the YES price", (w["side"], w["action"], w["price"], w["quantity"]) ==
          ("yes", "sell", 0.62, 10) and w["expirationDate"] == "2026-10-10T12:00:00.000Z")
    w = X.wire_order(Order("11", True, 0.3, 10.0, "mm"), True, "T", exp)
    check("covered bid -> no/sell at 1 - p", (w["side"], w["action"], w["price"]) == ("no", "sell", 0.7))
    check("the wire round-trips through parse_order",
          X.parse_order(dict(w, id=1, priceLimit=w["price"], expirationDate=future)).price == 0.3)
    c = client([ok_batch(2)])
    out = c.place([Order("11", True, 0.3, 100, "ladder"), Order("12", True, 0.4, 5, "mm")], {"11": -40, "12": 3})
    sent = c.session.sent[0][3]["orders"]
    check("place: a bid while short NO goes out as a covered sale trimmed to the NO held",
          (sent[0]["side"], sent[0]["action"], sent[0]["quantity"]) == ("no", "sell", 40) and out[0].order.size == 40)
    check("place: a bid while long is a plain buy", (sent[1]["side"], sent[1]["action"]) == ("yes", "buy"))
    check("locked cash: buys lock their own-side price, sales none", abs(X.locked_cash(
        [dict(raw, quantity=10, priceLimit=0.2), dict(raw, side="no", priceLimit=0.8),
         dict(raw, action="sell")]) - 10.0) < 1e-9)


def test_strip_and_markets():
    raw = {"bids": [{"price": 0.40, "quantity": 100}, {"price": 0.41, "quantity": 30}],
           "asks": [{"price": 0.45, "quantity": 50}]}
    b = X.strip_own(raw, [Order("1", True, 0.41, 30, "mm"), Order("1", False, 0.45, 20, "mm")])
    check("strip_own removes our size, keeps others, best first", b.bids == [(0.40, 100)] and b.asks == [(0.45, 30)])
    t_end = datetime(2026, 11, 3, tzinfo=timezone.utc)
    m = X.parse_markets({"id": 9, "title": "Will the Republican Party win the Rhode Island Senate?",
                         "settlementDate": "2026-11-04T00:00:00Z", "exchanges": [{"id": 91}]}, t_end)[0]
    check("race market parsed", (m.eid, m.label, m.race, m.party, m.state) ==
          ("91", "Rep Rhode Island Senate", "Rhode Island Senate", "Republican", "RI"))
    check("close is the earlier of settlement and the tournament end", m.close == t_end)
    ms = X.parse_markets({"id": 5, "title": "Who wins?", "exchanges": [{"id": 51, "option": "A"},
                                                                       {"id": 52, "option": "B"}]}, None)
    check("other markets are their own race", [x.race for x in ms] == ["market:5", "market:5"] and ms[0].party is None)
    check("composite markets skipped", X.parse_markets({"id": 1, "isComposite": True, "exchanges": [{"id": 2}]},
                                                       None) == [])
    check("state_of", (X.state_of("Dem RI-01 House race"), X.state_of("Rep West Virginia Senate"),
                       X.state_of("Rep U.S. Senate"), X.state_of("Ind NY Governor")) == ("RI", "WV", None, "NY"))


def test_ioc_and_dry_run():
    c = client([ok_batch(1, traded=4), Reply(200, {})])
    out = c.place([Order("11", True, 0.18, 10, "alloc", ioc=True)], {})
    methods = [(s[0], s[1].split("x", 1)[1]) for s in c.session.sent]
    check("ioc: the unfilled rest is cancelled at once", methods == [("POST", "/orders/batch"),
                                                                       ("DELETE", "/orders/500")])
    exp = X.parse_ts(c.session.sent[0][3]["orders"][0]["expirationDate"])
    check("ioc: it expires within seconds anyway", exp - datetime.now(timezone.utc) <= timedelta(seconds=X.IOC_TTL_S))
    check("ioc: traded is reported", out[0].traded == 4 and out[0].error is None)
    c = client([ok_batch(1, traded=10)])
    c.place([Order("11", True, 0.18, 10, "alloc", ioc=True)], {})
    check("ioc fully traded: no cancel", len(c.session.sent) == 1)
    c = client([Reply(200, {"results": [{"index": 0, "ok": False, "status": 400,
                                         "data": {"error": {"message": "Insufficient available funds"}}}]})])
    out = c.place([Order("11", True, 0.18, 10, "alloc")], {})
    check("a refused order carries the reason", out[0].oid is None and "Insufficient" in out[0].error)
    c = client(live=False)
    out = c.place([Order("11", True, 0.18, 10, "mm")], {})
    check("dry run: nothing is sent", not c.session.sent and out[0].oid is None and c.cancel("1") and c.cancel_all())
    c = client([ok_batch(10), ok_batch(2)])
    c.place([Order("11", True, 0.1, 5, "mm")] * 12, {})
    check("orders go in batches of BATCH_MAX", [len(s[3]["orders"]) for s in c.session.sent] == [10, 2])


def test_reads():
    page1 = {"data": [{"id": 3, "exchangeId": 11, "orderId": 500, "price": 0.38, "quantity": -30,
                       "filledAt": "2026-10-10T10:00:00Z"},
                      {"id": 2, "exchangeId": 11, "orderId": 77, "price": 0.40, "quantity": 5}],
             "pagination": {"hasMore": True, "nextCursor": "c"}}
    page2 = {"data": [{"id": 1, "exchangeId": 11, "orderId": 76, "price": 0.3, "quantity": 5}],
             "pagination": {"hasMore": False}}
    c = client([Reply(200, page1), Reply(200, page2)])
    fs = c.fills("1")
    check("fills: newer than after_id, oldest first, with fid", [f.fid for f in fs] == ["2", "3"])
    check("fills: a NO-side fill is our ask at 1 - price", not fs[1].is_bid and fs[1].price == 0.62 and fs[1].size == 30)
    c = client([Reply(200, page1)])
    c.directions["500"] = True
    fs = c.fills("2")
    check("fills: a covered NO sale's fill is our bid (direction from our order)", fs[0].is_bid and fs[0].price == 0.62)
    c = client([Reply(200, {"positions": [{"exchangeId": 11, "quantity": -40, "currentPrice": 0.2},
                                          {"exchangeId": 12, "quantity": 5, "settled": True}],
                            "summary": {"totalMarketValue": 32}})])
    check("positions: unsettled, signed, with marks", c.positions() == {"11": -40.0} and c.marks == {"11": 0.2})
    c = client([Reply(200, {"totalAccountValue": 101000, "availableBalance": 50000})])
    c.start_balance = 100000.0
    a = c.account()
    check("account: value and net cash", (a.value, a.cash, a.start) == (101000, 50000, 100000))
    c = client([Reply(200, {"totalAccountValue": 101000, "cashBalance": 60000})])
    c.locked = 2500.0
    check("account: gross cash less what our orders lock", c.account().cash == 57500)
    c = client([Reply(500, {}), Reply(200, {"myBalance": 70000, "id": "T"})])
    c.market_value = 30000.0
    a = c.account()
    check("account: falls back to balance + positions", a.value == 100000 and a.cash == 70000)
    c = client([Reply(200, {"data": [{"exchangeId": 11, "bestBid": 0.4, "bestAsk": None}]})] * 2)
    tops = c.tops([str(i) for i in range(150)])
    check("tops: 100 ids a request", len(c.session.sent) == 2 and tops["11"] == (0.4, None))


def test_fake():
    fc, feed, refs = two_race_world(cash=1000.0)
    check("fake world: 4 markets, 2 races", len(fc.markets()) == 4 and len({m.race for m in fc.markets()}) == 2)
    p = fc.place([Order("11", True, 0.18, 300, "alloc", ioc=True)], {})[0]
    check("fake: a crossing bid trades at the ask", p.traded == 300 and fc.inv["11"] == 300 and fc.fill_log[-1].price == 0.18)
    check("fake: an ioc never rests", not fc.orders and abs(fc.cash - (1000 - 54)) < 1e-9)
    p = fc.place([Order("21", True, 0.50, 10000, "mm")], {})[0]
    check("fake: refused without the cash", p.error == "Insufficient available funds")
    bid = p = fc.place([Order("21", True, 0.50, 100, "mm")], {})[0]
    check("fake: a non-crossing bid rests, locks cash", p.oid in fc.orders and abs(fc.account().cash - 896) < 1e-6)
    p = fc.place([Order("21", False, 0.49, 10, "mm")], {})[0]
    check("fake: crossing our own order is refused and counted", p.error and fc.self_crosses == 1)
    f = fc.fill("21", True, 40)
    check("fake: another trader hits our bid", f.size == 40 and fc.inv["21"] == 40 and fc.orders[bid.oid].size == 60)
    fc.inv["12"] = -50
    p = fc.place([Order("12", True, 0.80, 80, "ladder")], fc.positions())[0]
    check("fake: a bid while short NO is a covered sale trimmed to the NO held", p.order.size == 50)
    check("fake: the book strips what we say is ours", fc.book("21", fc.open_orders()).bids == [(0.48, 1000)])
    check("fake: tops include our orders", fc.tops(["21"])["21"] == (0.50, 0.56))
    last = fc.fill_log[-1].fid
    fc.fill("21", True, 10)
    check("fake: fills after an id", [f.fid for f in fc.fills(last)] == [fc.fill_log[-1].fid])
    fc.fail_next(1, "write")
    try:
        fc.cancel_all()
        raised = False
    except X.ApiError:
        raised = True
    check("fake: injected fault raises once", raised and fc.cancel_all() and not fc.orders)
    check("fake: marks and refs", "11" in fc.positions() and fc.marks["11"] == 0.14 and
          refs.get()["Ohio Senate|Republican"] == 0.14)
    feed.push(dirty=["11"], account=True)
    feed.push(dirty=["12"], resync=True)
    check("fake feed: take merges and clears", feed.take() == ({"11", "12"}, True, True) and
          feed.take() == (set(), False, False))


def test_feed_logic():
    f = X.Feed(None, "T")
    f.take()
    f.on_market("t", {"payload": {"delivery": {"revision": 1}, "bookDirty": [{"exchangeId": 11}]}})
    f.on_market("t", {"payload": {"delivery": {"revision": 1}, "bookDirty": [{"exchangeId": 12}]}})
    check("feed: a duplicate revision is ignored", f.take() == ({"11"}, False, False))
    f.on_market("t", {"payload": {"delivery": {"revision": 5, "previousRevision": 3}}})
    check("feed: a revision gap flags a resync", f.take()[2])
    f.on_account("u", {"payload": {"orderUpdates": [{"id": 1}]}})
    check("feed: order echoes do not mark the account", f.take() == (set(), False, False))
    f.on_account("u", {"payload": {"fills": [{"exchangeId": 21}]}})
    check("feed: a fill marks the account and its book", f.take() == ({"21"}, True, False))
    check("feed: unhealthy until joined", not f.healthy())


for test in (test_budgets, test_429, test_retries, test_wire, test_strip_and_markets, test_ioc_and_dry_run,
             test_reads, test_fake, test_feed_logic):
    try:
        test()
    except Exception as e:                                         # a crash fails the group, not the run
        import traceback
        traceback.print_exc()
        check(f"{test.__name__} ran without crashing ({e!r})", False)
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
