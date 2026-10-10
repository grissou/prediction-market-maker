"""
Fakes for the offline suites: an exchange, the realtime feed and Polymarket, with the new Client's interface.

FakeClient    the exchange as the API behaves, in YES terms: other traders' books, our resting orders, positions,
              cash. A placed order trades at once against other traders at its price or better (at their price),
              the rest rests (an ioc rest is cancelled); a bid while short NO is the covered "sell NO", trimmed to
              the NO held and needing no cash; with `cash` set, an order that needs more than the free cash is
              refused "Insufficient available funds" (3 Oct). An order that would cross one of our own resting
              orders is refused and counted in `self_crosses` (the stress test asserts it stays 0). `fill(eid,
              is_bid, qty)` is another trader hitting us. Faults: `fail_next(n, kind)` and `fail_rate` make a call raise
              ApiError (place() reports them as unknown outcomes instead, as Client.place does).
FakeFeed      push(...) by hand, take() as Feed.take.
FakeRefs      ref_prices-like: get() -> {key: price}, spreads() -> {key: spread}.
two_race_world()  Ohio Senate and Utah Senate, Republican and Democratic legs, house quotes 8c wide.
"""
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mmbot2.exchange import ApiError, Placed, parse_markets, strip_own     # noqa: E402
from mmbot2.state import Account, Fill, Order, held_usd                    # noqa: E402

UNLIMITED = 10 ** 6


class FakeClient:
    def __init__(self, markets, books, cash=None, live=True, start=100000.0):
        self.market_list = list(markets)  # list[Market]
        self.others = {e: {"bids": list(b["bids"]), "asks": list(b["asks"])} for e, b in books.items()}
        self.orders = {}                  # oid -> Order resting (YES terms, shares left)
        self.locks = {}                   # oid -> cash locked per share left (0 for a covered sale)
        self.inv = {}                     # eid -> signed YES shares
        self.cash = start if cash is None else cash   # total cash; free = cash - locked
        self.check_cash = cash is not None            # None: every order is affordable
        self.start, self.live, self.tid = start, live, "T"
        self.fill_log = []                # every Fill, oldest first
        self.calls = []                   # (method, ...) log for tests
        self.marks, self.rate_limited, self.pauses = {}, 0, 0
        self.req_left = self.write_left = UNLIMITED   # what requests_left / writes_left report
        self.now = None                   # a datetime to run on a simulated clock; None = the real one
        self.faults, self.fail_rate, self.rng = [], 0.0, random.Random(7)
        self.self_crosses, self.next_id = 0, 100

    # --- faults ---
    def fail_next(self, n, kind="any", status=503):
        """The next n calls of `kind` ("read", "write" or "any") raise ApiError(status)."""
        self.faults += [(kind, status)] * n

    def call(self, name, kind):
        self.calls.append(name)
        self.expire()
        for i, (k, status) in enumerate(self.faults):
            if k in ("any", kind):
                del self.faults[i]
                raise ApiError(status, "INJECTED", f"injected fault on {name}")
        if self.rng.random() < self.fail_rate:
            raise ApiError(503, "INJECTED", f"random fault on {name}")

    def clock(self):
        return self.now or datetime.now(timezone.utc)

    def expire(self):
        for oid in [k for k, o in self.orders.items() if o.expires and o.expires <= self.clock()]:
            self.drop(oid)

    def drop(self, oid):
        self.orders.pop(oid, None)
        self.locks.pop(oid, None)

    # --- budgets ---
    def set_budgets(self, rpm, wpm): self.calls.append("set_budgets")
    def requests_left(self): return self.req_left
    def writes_left(self): return self.write_left
    def pause_left(self): return 0.0

    # --- reads ---
    def tournament(self):
        self.call("tournament", "read")
        return {"id": self.tid, "initialBalance": self.start, "status": "active", "endDate": "2026-11-04T17:00:00Z"}

    def markets(self):
        self.call("markets", "read")
        return list(self.market_list)

    def mid(self, eid):
        b = self.others[eid]
        if b["bids"] and b["asks"]:
            return round((b["bids"][0][0] + b["asks"][0][0]) / 2, 4)
        return None

    def positions(self):
        self.call("positions", "read")
        self.marks = {e: self.mid(e) for e, q in self.inv.items() if q and self.mid(e) is not None}
        return {e: q for e, q in self.inv.items() if q}

    def locked(self):
        return sum(self.locks[k] * o.size for k, o in self.orders.items())

    def account(self):
        self.call("account", "read")
        marks = {e: self.mid(e) or 0.5 for e in self.inv}
        value = self.cash + sum(held_usd(q, marks[e]) for e, q in self.inv.items())
        return Account(value=value, cash=self.cash - self.locked(), start=self.start, read_at=time.monotonic())

    def open_orders(self):
        self.call("open_orders", "read")
        return [Order(o.eid, o.is_bid, o.price, o.size, "", oid=k, expires=o.expires) for k, o in self.orders.items()]

    def full_book(self, eid):
        """The book as the API shows it: other traders and our own orders together, in the API's format."""
        book = {side: [{"price": p, "quantity": q} for p, q in self.others[eid][side]] for side in ("bids", "asks")}
        for o in self.orders.values():
            if o.eid == eid:
                book["bids" if o.is_bid else "asks"].append({"price": o.price, "quantity": o.size})
        return book

    def book(self, eid, mine):
        self.call("book", "read")
        return strip_own(self.full_book(eid), mine)

    def tops(self, eids):
        self.call("tops", "read")
        out = {}
        for e in eids:
            b = self.full_book(e)
            out[e] = (max((l["price"] for l in b["bids"]), default=None),
                      min((l["price"] for l in b["asks"]), default=None))
        return out

    def fills(self, after_id):
        self.call("fills", "read")
        return [f for f in self.fill_log if after_id is None or int(f.fid) > int(after_id)]

    # --- writes ---
    def place(self, orders, positions):
        try:
            self.call("place", "write")
        except ApiError as e:             # as Client.place: a failed batch comes back as unknown, never raised
            return [Placed(o, None, 0.0, str(e), unknown=True) for o in orders]
        if not self.live:
            return [Placed(o, None, 0.0, "dry run") for o in orders]
        return [p for p in (self.place_one(o) for o in orders) if p is not None]

    def need(self, o):
        """(cash per share, shares needing it): a sale of shares held needs none, the rest is a purchase."""
        held = self.inv.get(o.eid, 0.0)
        if o.is_bid:
            return o.price, (0.0 if held < 0 else o.size)
        offered = sum(r.size for r in self.orders.values() if r.eid == o.eid and not r.is_bid)
        return 1 - o.price, max(0.0, o.size - max(0.0, held - offered))

    def place_one(self, o):
        held = self.inv.get(o.eid, 0.0)
        size = min(int(o.size), int(-held)) if o.is_bid and held <= -1 else int(o.size)
        if size < 1:
            return None
        o = Order(o.eid, o.is_bid, o.price, size, o.tag, o.ioc, None,
                  o.expires or self.clock() + timedelta(seconds=1800))   # Settings.order_ttl_s
        if any(r.eid == o.eid and r.is_bid != o.is_bid and (r.price <= o.price if o.is_bid else r.price >= o.price)
               for r in self.orders.values()):
            self.self_crosses += 1
            return Placed(o, None, 0.0, "would cross our own order")
        rate, shares = self.need(o)
        if self.check_cash and rate * shares > self.cash - self.locked() + 1e-9:
            return Placed(o, None, 0.0, "Insufficient available funds")
        oid, self.next_id = str(self.next_id), self.next_id + 1
        traded = self.cross(o, oid)
        placed = Order(o.eid, o.is_bid, o.price, o.size, o.tag, o.ioc, oid, o.expires)
        if traded < o.size and not o.ioc:
            self.orders[oid] = Order(o.eid, o.is_bid, o.price, o.size - traded, o.tag, False, oid, o.expires)
            self.locks[oid] = rate if shares > 0 else 0.0
        return Placed(placed, oid, float(traded), None)

    def cross(self, o, oid):
        """Trade o against other traders at its price or better, at their prices; returns the shares traded."""
        side, traded = self.others[o.eid]["asks" if o.is_bid else "bids"], 0
        while traded < o.size and side and (side[0][0] <= o.price if o.is_bid else side[0][0] >= o.price):
            price, qty = side[0]
            take = min(o.size - traded, qty)
            side[0] = (price, qty - take)
            if side[0][1] <= 0:
                side.pop(0)
            self.trade(o.eid, o.is_bid, price, take, oid)
            traded += take
        return traded

    def trade(self, eid, is_bid, price, qty, oid):
        """Cash and position after a trade: closing a position receives its value, opening one pays for it."""
        held = self.inv.get(eid, 0.0)
        closing = min(qty, max(0.0, -held if is_bid else held))
        paid, got = (price, 1 - price) if is_bid else (1 - price, price)
        self.cash += closing * got - (qty - closing) * paid
        self.inv[eid] = held + (qty if is_bid else -qty)
        self.fill_log.append(Fill(eid, is_bid, price, qty, oid, self.clock(), fid=str(len(self.fill_log) + 1)))

    def fill(self, eid, is_bid, qty):
        """Another trader hits our best resting bid (is_bid=True) or lifts our best ask. Returns the Fill or None."""
        self.expire()
        ours = [(k, o) for k, o in self.orders.items() if o.eid == eid and o.is_bid == is_bid]
        if not ours:
            return None
        oid, o = max(ours, key=lambda ko: ko[1].price if is_bid else -ko[1].price)
        qty = min(qty, o.size)
        self.trade(eid, is_bid, o.price, qty, oid)
        o.size -= qty
        if o.size <= 0:
            self.drop(oid)
        return self.fill_log[-1]

    def cancel(self, oid):
        self.call("cancel", "write")
        if self.live:
            self.drop(oid)
        return True

    def cancel_all(self, eid=None):
        self.call("cancel_all", "write")
        if self.live:
            for k in [k for k, o in self.orders.items() if eid is None or o.eid == eid]:
                self.drop(k)
        return True


class FakeFeed:
    """Stands in for Feed: tests push events by hand."""
    def __init__(self):
        self.wake, self.ok, self.queue = threading.Event(), True, []
    def start(self): pass
    def stop(self): pass
    def healthy(self): return self.ok

    def push(self, dirty=(), account=False, resync=False):
        self.queue.append((set(dirty), account, resync))
        self.wake.set()

    def take(self):
        self.wake.clear()
        dirty, account, resync = set(), False, False
        for d, a, r in self.queue:
            dirty, account, resync = dirty | d, account or a, resync or r
        self.queue = []
        return dirty, account, resync


class FakeRefs:
    """Stands in for the Polymarket reader. spread 0.01 = a liquid market (fair value uses it)."""
    def __init__(self, prices, spread=0.01):
        self.prices, self.spread = dict(prices), spread
    def start(self): pass
    def stop(self): pass
    def get(self): return dict(self.prices)
    def spreads(self): return {k: self.spread for k in self.prices}


def raw_market(mid, eid, party, race):
    return {"id": mid, "title": f"Will the {party} Party win the {race}?", "isComposite": False,
            "settlementDate": "2026-11-04T00:00:00Z", "exchanges": [{"id": eid, "option": "YES"}]}


def two_race_world(cash=None, live=True, books=None):
    """(client, feed, refs): Ohio Senate (Rep 11 / Dem 12) and Utah Senate (Rep 21 / Dem 22), books 8c wide,
    Polymarket at the books' mids."""
    raws = [raw_market("1", "11", "Republican", "Ohio Senate"), raw_market("2", "12", "Democratic", "Ohio Senate"),
            raw_market("3", "21", "Republican", "Utah Senate"), raw_market("4", "22", "Democratic", "Utah Senate")]
    markets = [m for r in raws for m in parse_markets(r, None)]
    books = books or {"11": {"bids": [(0.10, 1000)], "asks": [(0.18, 1000)]},
                      "12": {"bids": [(0.82, 1000)], "asks": [(0.90, 1000)]},
                      "21": {"bids": [(0.48, 1000)], "asks": [(0.56, 1000)]},
                      "22": {"bids": [(0.44, 1000)], "asks": [(0.52, 1000)]}}
    refs = FakeRefs({"Ohio Senate|Republican": 0.14, "Ohio Senate|Democratic": 0.86,
                     "Utah Senate|Republican": 0.52, "Utah Senate|Democratic": 0.48})
    return FakeClient(markets, books, cash=cash, live=live), FakeFeed(), refs
