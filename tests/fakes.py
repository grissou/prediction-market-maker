"""
The fake exchange and helpers shared by the offline test suites (test_mm_bot.py, test_stress.py).
FakeApi follows the API spec: sells you don't hold become NO buys, crossing orders trade at once,
fills are logged. FakeFeed stands in for the realtime feed, FakeRefs for Polymarket prices.
"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                  # the project folder, where mm_bot.py lives
os.environ.setdefault("SUPERMARKET_API_KEY", "test-key")
os.environ.setdefault("TOURNAMENT_SLUG", "test")

import mm_bot as M                                        # noqa: E402
from mm_bot import *                                      # noqa: E402,F401,F403

# Never send real phone notifications from tests, even in a terminal where ALERT_URL is set.
M.CFG.alert_url = ""
M.notify = lambda *a, **k: False

# =============================================================================================
# THE FAKE EXCHANGE
# =============================================================================================

class FakeApi(Api):
    def __init__(self, live=True):
        self.cfg, self.live, self.gap = CFG, live, 0
        self.books = {}                   # eid -> {"bids": [...], "asks": [...]} of OTHER traders
        self.orders = {}                  # our resting orders, in API format
        self.next_id = 100
        self.inv = {}                     # eid -> signed YES shares
        self.fills = []
        self.calls = []                   # log of requests, so tests can check what was sent
        self.fail_book = set()
        self.batch_error = None
        self.markets_list = []
        self.equity = 100000.0

    def log(self, *a): self.calls.append(a)
    def sent(self, kind): return [c for c in self.calls if c[0] == kind]
    def budget_left(self): return 10 ** 6         # unlimited unless a test overrides it
    def writes_left(self): return 10 ** 6
    def tournament(self): return {"id": "T", "initialBalance": 100000, "myBalance": self.equity, "status": "active",
                                  "endDate": "2026-11-04T17:00:00Z"}
    def markets(self): return self.markets_list
    def positions(self):
        self.log("positions")
        return {"positions": [{"exchangeId": e, "quantity": q, "settled": False} for e, q in self.inv.items()],
                "summary": {"totalMarketValue": 0}}
    def pnl(self):
        self.log("pnl")
        return {"totalAccountValue": self.equity}
    def leaderboard(self): return {"myRank": 3, "total": 50}
    def smart_score(self): return [{"marketType": "global", "smartScoreDecayed": 61.24, "rank": 150,
                                    "totalTraders": 900, "isElite": True}]
    def fills_page(self, cursor=None):
        self.log("fills")
        return {"data": list(reversed(self.fills)), "pagination": {"hasMore": False}}

    def yes_view(self, o):
        yes = o["side"] == "yes"
        return (yes == (o["action"] == "buy")), (o["priceLimit"] if yes else round(1 - o["priceLimit"], 3))

    def expire(self):
        """Like the engine: an order past its expirationDate is gone (can't trade, isn't listed).
        Uses M.utcnow so a test can run it on a simulated clock."""
        now = M.utcnow()
        for k in [k for k, o in self.orders.items() if o.get("expirationDate") and parse_ts(o["expirationDate"]) <= now]:
            del self.orders[k]

    def open_orders(self, tid, eid=None):
        self.expire()
        self.log("orders")
        return [dict(o) for o in self.orders.values() if eid is None or o["exchangeId"] == eid]

    def full_book(self, eid):
        self.expire()
        b = {"bids": [dict(l) for l in self.books[eid]["bids"]], "asks": [dict(l) for l in self.books[eid]["asks"]]}
        for o in self.orders.values():
            if o["exchangeId"] == eid:
                is_bid, p = self.yes_view(o)
                b["bids" if is_bid else "asks"].append({"price": p, "quantity": o["quantity"]})
        b["bids"].sort(key=lambda l: -l["price"])
        b["asks"].sort(key=lambda l: l["price"])
        return b

    def book(self, eid, tid):
        self.log("book", eid)
        if eid in self.fail_book:
            raise ApiError(503, "SERVICE_UNAVAILABLE", "boom")
        return self.full_book(eid)

    def bulk_prices(self, eids, tid):
        self.log("bulk")
        out = {}
        for e in eids:
            b = self.full_book(e)
            out[e] = (rnd(b["bids"][0]["price"]) if b["bids"] else None, rnd(b["asks"][0]["price"]) if b["asks"] else None)
        return out

    def cancel_all(self, tid, eid=None):
        self.log("cancel_all", eid)
        if self.live:
            for k in [k for k, o in self.orders.items() if eid is None or o["exchangeId"] == eid]:
                del self.orders[k]
        return True

    def cancel_order(self, oid):
        self.log("cancel_order", oid)
        if self.live:
            self.orders.pop(oid, None)
        return True

    def place_batch(self, orders):
        self.log("batch", len(orders))
        if self.batch_error:
            raise self.batch_error
        if not self.live:
            return [{"index": k, "ok": True, "status": 200, "data": {}} for k in range(len(orders))]
        res = []
        for k, o in enumerate(orders):
            oid, self.next_id = self.next_id, self.next_id + 1
            eid, qty, is_buy = o["exchangeId"], o["quantity"], o["action"] == "buy"
            # Trade immediately against other traders' orders at our price or better (taker).
            side = self.books[eid]["asks" if is_buy else "bids"]
            traded = 0
            while qty > 0 and side and (side[0]["price"] <= o["price"] + 1e-9 if is_buy else side[0]["price"] >= o["price"] - 1e-9):
                take = min(qty, side[0]["quantity"])
                side[0]["quantity"] -= take
                p = side[0]["price"]
                if side[0]["quantity"] <= 0:
                    side.pop(0)
                qty, traded = qty - take, traded + take
                self.inv[eid] = self.inv.get(eid, 0) + (take if is_buy else -take)
                self.fills.append({"id": len(self.fills) + 1, "orderId": oid, "exchangeId": eid, "price": p,
                                   "quantity": take if is_buy else -take, "side": "yes" if is_buy else "no",
                                   "filledAt": iso(utcnow())})
            if qty > 0:                   # the rest rests. Engine: uncovered sell -> buy NO @ 1-p
                held = self.inv.get(eid, 0)
                api_o = ({"side": "no", "action": "buy", "priceLimit": round(1 - o["price"], 3)}
                         if o["action"] == "sell" and held < qty else
                         {"side": o["side"], "action": o["action"], "priceLimit": o["price"]})
                self.orders[oid] = {"id": oid, "exchangeId": eid, "quantity": qty, "open": True,
                                    "expirationDate": o["expirationDate"], **api_o}
            res.append({"index": k, "ok": True, "status": 200, "data": {"orderId": oid, "quantityTraded": traded}})
        return res

    def fill(self, eid, is_bid, qty):
        """Another trader hits our resting bid (is_bid=True) or lifts our ask."""
        self.expire()
        for oid, o in list(self.orders.items()):
            b, p = self.yes_view(o)
            if o["exchangeId"] == eid and b == is_bid:
                o["quantity"] -= qty
                self.inv[eid] = self.inv.get(eid, 0) + (qty if is_bid else -qty)
                self.fills.append({"id": len(self.fills) + 1, "orderId": oid, "exchangeId": eid, "price": p,
                                   "quantity": qty if is_bid else -qty, "side": "yes" if is_bid else "no",
                                   "filledAt": iso(utcnow())})
                if o["quantity"] <= 0:
                    del self.orders[oid]
                return

    def ours(self, eid):
        """Our resting orders on one exchange as sorted (side, YES price, shares) tuples."""
        return sorted((("bid" if self.yes_view(o)[0] else "ask"), self.yes_view(o)[1], o["quantity"])
                      for o in self.orders.values() if o["exchangeId"] == eid)


def market(mid, eid, party, race):
    return {"id": mid, "title": f"Will the {party} Party win the {race}?", "isComposite": False,
            "settlementDate": "2026-11-04T00:00:00Z", "exchanges": [{"id": eid, "option": "YES"}]}


def lvl(price, qty):
    return {"price": price, "quantity": qty}


def pin_test_sizes(cfg):
    """The unit tests check exact sizes worked out with 100-share quotes, 1,000-share position limits and
    a 2,000-share national-swing cap. Pin those, so changing the live defaults doesn't rewrite every
    expected number (test_stress.py runs with the real defaults)."""
    cfg.order_size_frac, cfg.max_position_frac, cfg.max_party_delta_frac = 0.001, 0.01, 0.02
    cfg.size_by_activity = False                          # activity-based sizes have their own tests


def make_bot(live=True, only="", books=None, extra_markets=()):
    """Two races (Ohio, Utah), each Republican + Democrat, house quotes 8c wide. All files in a temp dir."""
    api = FakeApi(live)
    api.markets_list = [market("1", "11", "Republican", "Ohio Senate"), market("2", "12", "Democratic", "Ohio Senate"),
                        market("3", "21", "Republican", "Utah Senate"), market("4", "22", "Democratic", "Utah Senate"),
                        *extra_markets]
    api.books = books or {"11": {"bids": [lvl(0.10, 1000)], "asks": [lvl(0.18, 1000)]},
                          "12": {"bids": [lvl(0.82, 1000)], "asks": [lvl(0.90, 1000)]},
                          "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
                          "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}
    cfg = Config()
    cfg.only_exchanges = only
    d = tempfile.mkdtemp()
    cfg.fills_csv, cfg.status_file, cfg.order_notes_file, cfg.kill_file = (
        os.path.join(d, n) for n in ("fills.csv", "status.json", "notes.json", "kill.tripped"))
    cfg.record_file, cfg.ref_map_file, cfg.summary_every_hours = "", "", 0
    cfg.overrides_file = os.path.join(d, "overrides.json")
    cfg.handover_file = os.path.join(d, "handover.json")
    cfg.handover_exit_max_seconds = 0     # never os._exit the test process (the deadline has its own tests)
    pin_test_sizes(cfg)   # opt-in per test
    cfg.churn_control = False             # tests reprice straight after placing; churn control has its own tests
    cfg.slow_poll_seconds = 0             # read P&L and fills every cycle, so each test cycle sees them
    cfg.realtime_enabled = False          # no network in tests; realtime is tested with a FakeFeed
    return api, Bot(api, cfg)


def run_cycles(bot, n):
    """bot.run() for exactly n cycles (then a normal stop), with no sleeping in between."""
    count = {"k": 0}
    real_cycle = bot.cycle

    def limited():
        count["k"] += 1
        if count["k"] > n:
            bot.running = False
            return
        real_cycle()
    bot.cycle, bot.cfg.loop_seconds = limited, 0
    bot.run()


class FakeFeed:
    """Stands in for RealtimeFeed: tests push events by hand."""
    def __init__(self):
        self.wake, self.events, self.ok, self.queue = threading.Event(), 0, True, []
    def healthy(self): return self.ok
    def stop(self): pass
    def push(self, dirty=(), account=False, resync=False, settled=False):
        self.queue.append((set(dirty), account, resync, settled)); self.wake.set()
    def take(self):
        self.wake.clear()
        d, acc, rs, st = set(), False, False, False
        for q in self.queue:
            d |= q[0]; acc |= q[1]; rs |= q[2]; st |= q[3]
        self.queue = []
        return d, acc, rs, st


class FakeRefs:
    """Stands in for ReferencePrices. spread=0.01 = a liquid Polymarket market; None = last-trade only."""
    def __init__(self, prices, spread=0.01):
        self.prices, self.mapping, self.version, self.last_moves, self.spread = prices, prices, 1, {}, spread
    def get(self): return dict(self.prices)
    def spreads(self): return {k: self.spread for k in self.prices}
    def new_reading(self, prices, moves):
        self.prices, self.last_moves, self.version = prices, moves, self.version + 1
    def start(self): self.started = True
    def stop(self): pass
