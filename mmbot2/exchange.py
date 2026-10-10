"""
The exchange: the REST client with its request and write budgets, the realtime feed, and the wire format.

OWNS     ApiError; Client (one HTTP session, a sliding-minute request budget and a separate write budget, the 429
         pause, retries under one idempotency key, one method per endpoint); Placed (what became of an order we
         sent); Feed (Supabase realtime on its own thread); the YES-terms translation of orders, books and fills;
         market parsing (race, party, state). Single-threaded but for the feed's thread.
NEVER    decides a price or a size, keeps strategy state, or sends a write in a dry run.
ORIGIN   Day one: the API's limit measured at about 100 requests a minute, and each 429 cost a silent pause; so every
         request goes through one budget below it (80/min, 28 writes/min since the first penalties), and a 429 stops
         EVERY request for Retry-After and cuts both budgets once per pause. The crowded book: other bots penny us,
         so our own orders are stripped from every book before anyone prices from it, or fair value would follow
         our quotes. The feed died silently for two days (29 Sep) while reporting "connected": hence the dead-socket
         checks and the session renewal.
OPEN     A NO-side fill's price is read as the NO price (day one: an ask at 0.62 filled "at 0.38"); the old bot also
         accepted the YES price there. The exchange's handling of a "sell NO" beyond the NO held is unknown, so a
         covered sale is trimmed to the NO held.
"""
import asyncio
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from mmbot2.state import Account, Book, Fill, Market, Order

log = logging.getLogger("mm2")

WINDOW_S = 60.0               # the budgets are per rolling minute, as the exchange measures them
MIN_GAP_S = 0.5               # request starts at least this far apart: bursts drew the first 429s (old min gap)
CUT_429 = 0.75                # a new 429 pause cuts each budget to three quarters...
FLOOR_RPM, FLOOR_WPM = 20, 10 # ...never below these, or the bot could not even reconcile
DEFAULT_PAUSE_S = 5.0         # a 429 without Retry-After pauses this long
MAX_WRITE_WAIT_S = 5.0        # a write that would wait longer is not sent (the cycle never blocks on the budget)
TIMEOUT_S = 15.0              # one HTTP request; day one's slow writes took 15-30 s
MAX_RETRIES = 5
RETRY_STATUS = (502, 503, 504)   # ORDER_STATUS_UNKNOWN, SERVICE_UNAVAILABLE / TX_CONFLICT, gateway: safe to resend
FATAL_CODES = {"MISSING_API_KEY", "INVALID_API_KEY", "API_KEY_REVOKED", "API_KEY_EXPIRED",   # retrying never helps
               "INSUFFICIENT_SCOPES", "ACCOUNT_BANNED", "TERMS_NOT_ACKNOWLEDGED"}
BULK_MAX_IDS = 100            # /exchanges/prices takes at most 100 ids a request
BOOK_DEPTH = 10               # levels per side; deeper levels never set a price
BATCH_MAX = 10                # orders per POST /orders/batch: full 20-order batches timed out (273 of 417, 1-2 Oct)
IOC_TTL_S = 10.0              # an immediate-or-cancel order lives this long even if its cancel is lost
FILL_PAGES = 5                # pages of 200 fills read back per call: catches up on 1,000 fills
CASH_NET_KEYS = ("availableBalance", "availableCash", "availableFunds", "available")   # net of what orders lock
CASH_GROSS_KEYS = ("cashBalance", "cash", "balance", "myBalance")                      # locks still inside
MARK_KEYS = ("currentPrice", "markPrice", "valuationPrice", "price")                   # a position's valuation
TITLE = re.compile(r"^Will the (\w+) Party win the (.+)\?$")   # every race market's title


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message

    @property
    def fatal(self):
        return self.code in FATAL_CODES


@dataclass
class Placed:
    order: Order              # as sent (a covered NO sale is trimmed to the NO held)
    oid: str | None           # the exchange's id, None if refused or not sent
    traded: float             # shares that traded at once
    error: str | None         # why it was refused or not sent, None if accepted
    unknown: bool = False     # the outcome is unknown (timeout, 5xx): it may rest; read the open orders first


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(s):
    """Some API timestamps carry 7 decimals, which Python before 3.11 cannot read: cut them to 6."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))) if s else None


def utcnow():
    return datetime.now(timezone.utc)


# --- the wire format, in YES terms ---------------------------------------------------------------------------

def parse_order(raw, now=None):
    """API order -> Order in YES terms, or None if it no longer rests. The engine books "sell YES we do not hold"
    as "buy NO @ 1 - p" and lists it so, and our covered sales go out as "sell NO": a NO order flips back."""
    if not raw.get("open", True) or raw.get("priceLimit") is None:
        return None
    expires = parse_ts(raw.get("expirationDate"))
    if expires and expires <= (now or utcnow()):
        return None               # past expiry the engine ignores it even while it still says open
    yes = str(raw["side"]).lower() == "yes"
    is_bid = yes == (str(raw["action"]).lower() == "buy")
    price = round(float(raw["priceLimit"]) if yes else 1 - float(raw["priceLimit"]), 3)
    return Order(str(raw["exchangeId"]), is_bid, price, float(raw["quantity"]), "", oid=str(raw["id"]),
                 expires=expires)


def wire_order(order, covered, tid, expires):
    """Order -> request body. A covered bid (buying back NO we hold) goes out as "sell NO @ 1 - p": a sale of
    shares we hold needs no cash, where "buy YES" would lock the full price (release 6, 3 Oct refusals)."""
    body = {"exchangeId": order.eid, "side": "yes", "action": "buy" if order.is_bid else "sell",
            "quantity": int(order.size), "price": round(order.price, 3), "tournamentId": tid,
            "expirationDate": iso(expires)}
    if covered:
        body.update(side="no", action="sell", price=round(1 - order.price, 3))
    return body


def locked_cash(raw_orders):
    """Cash our open orders lock: a buy locks quantity x its own side's price; a sale of shares held locks none."""
    return sum(float(o["quantity"]) * float(o["priceLimit"]) for o in raw_orders
               if str(o.get("action")).lower() == "buy" and parse_order(o) is not None)


def levels(raw):
    return [(round(float(l["price"]), 3), float(l["quantity"])) for l in raw or []]


def strip_own(raw_book, mine):
    """Other traders' book: our own size taken off each level. Without this, once we are the touch, fair value
    and every 'one tick better' rule would follow our own quotes."""
    own = {}
    for o in mine:
        own[(o.is_bid, round(o.price, 3))] = own.get((o.is_bid, round(o.price, 3)), 0.0) + o.size
    sides = []
    for is_bid, key in ((True, "bids"), (False, "asks")):
        left = [(p, q - own.get((is_bid, p), 0.0)) for p, q in levels(raw_book.get(key))]
        sides.append(sorted([(p, q) for p, q in left if q > 1e-9], reverse=is_bid))
    return Book(sides[0], sides[1], time.monotonic())


def parse_fill(raw, known):
    """API fill -> Fill in YES terms. The API signs quantity by side (+ YES, - NO) and prices a NO fill in NO
    terms; the direction comes from our own order when we know it (a covered NO sale is a bid, a sale of YES held
    an ask), else from the side."""
    qty, price, oid = float(raw.get("quantity") or 0), float(raw.get("price") or 0), raw.get("orderId")
    oid = None if oid is None else str(oid)
    yes_price = round(price if qty > 0 else 1 - price, 3)
    is_bid = known.get(oid, qty > 0)
    return Fill(str(raw["exchangeId"]), is_bid, yes_price, abs(qty), oid,
                parse_ts(raw.get("filledAt")) or utcnow(), fid=str(raw["id"]))


# --- markets -------------------------------------------------------------------------------------------------

US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC"}
STATE_NAMES = sorted(US_STATES, key=len, reverse=True)    # longest first: "West Virginia" before "Virginia"
STATE_CODE = re.compile(r"^([A-Z]{2})(?:-(?:\d{1,2}|AL))?(?=\s|$)")   # "RI", "RI-01", "AK-AL"


def state_of(label):
    """Postal code of a market's state ("Rep Rhode Island Senate", "Dem RI-01 House" -> "RI"); None for the
    national markets ("U.S. Senate") and anything unrecognised. The per-state cap exists since Rhode Island alone
    reached a quarter of the account through four markets (release 15)."""
    words = " ".join((label or "").split())
    for text in (words, words.split(" ", 1)[-1]):           # with and without the party word
        if text.startswith(("U.S.", "US ")):
            return None
        m = STATE_CODE.match(text)
        if m and m.group(1) in US_STATES.values():
            return m.group(1)
        for name in STATE_NAMES:
            if text.lower() == name.lower() or text.lower().startswith(name.lower() + " "):
                return US_STATES[name]
    return None


def parse_markets(raw, t_end):
    """API market -> its Markets. A race market ("Will the X Party win the RACE?", one exchange) joins RACE; any
    other market is its own race, so no sum-to-one is forced on it. Composite markets are skipped: they are other
    markets combined, and trading them would double the exposure the caps count once."""
    if raw.get("isComposite"):
        return []
    closes = [d for d in (parse_ts(raw.get("settlementDate")), t_end) if d]
    close = min(closes) if closes else None
    exchanges, title = raw.get("exchanges") or [], raw.get("title", "")
    m = TITLE.match(title)
    out = []
    for e in exchanges:
        if m and len(exchanges) == 1:
            party, race = m.group(1), m.group(2)
            label = f"{party[:3]} {race}"
        else:
            party, race = None, f"market:{raw['id']}"
            label = f"{title[:24]} {e.get('option') or ''}".strip()
        out.append(Market(str(e["id"]), label, race, party, state_of(label), close))
    return out


# --- the client ----------------------------------------------------------------------------------------------

class Client:
    """Single-threaded REST client; the feed's token request is the only call from another thread (hence the
    lock around the budgets)."""

    def __init__(self, env, s, live, session=None):
        self.base, self.slug, self.live = env.base_url, env.slug, live
        self.rpm_max, self.wpm_max = float(s.requests_per_minute), float(s.writes_per_minute)
        self.rpm, self.wpm = self.rpm_max, self.wpm_max
        self.reqs, self.writes = deque(), deque()     # start times inside the last minute
        self.next_start = 0.0
        self.paused_until = 0.0                       # monotonic end of the 429 pause
        self.rate_limited = 0                         # 429s received (status.json: should stay 0)
        self.pauses = 0                               # 429 pauses begun (a 429 inside a pause extends it)
        self.lock = threading.Lock()
        self.tid = None                               # the tournament id, read by tournament()
        self.t_end = None                             # the tournament's end: no market closes later
        self.start_balance = None                     # initialBalance, for Account.start
        self.marks = {}                               # eid -> the exchange's valuation price, from positions()
        self.market_value = None                      # the positions' total at those marks
        self.locked = 0.0                             # cash our open orders lock, from the last open_orders()
        self.directions = {}                          # oid -> is_bid of our orders, to read fills' direction
        self.ttl_s = s.order_ttl_s                    # an order sent without `expires` lives this long
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {env.api_key}", "Content-Type": "application/json"})

    # --- budgets ---
    def set_budgets(self, rpm, wpm):
        with self.lock:
            self.rpm_max, self.wpm_max = float(rpm), float(wpm)
            self.rpm, self.wpm = self.rpm_max, self.wpm_max

    def used(self, window, now):
        while window and now - window[0] >= WINDOW_S:
            window.popleft()
        return len(window)

    def requests_left(self):
        with self.lock:
            return int(self.rpm) - self.used(self.reqs, time.monotonic())

    def writes_left(self):
        """A write spends from both budgets, so the smaller room is what is left."""
        with self.lock:
            now = time.monotonic()
            return min(int(self.wpm) - self.used(self.writes, now), int(self.rpm) - self.used(self.reqs, now))

    def pause_left(self):
        return max(0.0, self.paused_until - time.monotonic())

    def start_at(self, write, wait):
        """When the next request may start: after the pause, the gap, and the oldest start that leaves the
        minute's budget room. A write too far off is refused unsent (WRITE_BUDGET_WAIT) unless it may wait: the
        cycle never blocks on the budget."""
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_start, self.paused_until)
            if self.used(self.reqs, now) >= int(self.rpm):
                start = max(start, self.reqs[-int(self.rpm)] + WINDOW_S)
            if write:
                if self.used(self.writes, now) >= int(self.wpm):
                    start = max(start, self.writes[-int(self.wpm)] + WINDOW_S)
                if start - now > MAX_WRITE_WAIT_S and not wait:
                    raise ApiError(429, "WRITE_BUDGET_WAIT", f"a write would wait {start - now:.0f} s: not sent")
                self.writes.append(start)
            self.reqs.append(start)
            self.next_start = start + MIN_GAP_S
            return start

    def throttle(self, write, wait):
        start = self.start_at(write, wait)
        while time.monotonic() < start:
            time.sleep(start - time.monotonic())
            start = max(start, self.paused_until)      # a pause begun while we slept: wait it out too

    def note_429(self, retry_after):
        """Pause every request for Retry-After: knocking during the pause only earns more 429s. The first 429 of
        a pause cuts both budgets; a later one in the same pause extends it to Retry-After from now, never adds up
        (2 Oct: twenty 429s, each of which had been costing another silent pause)."""
        with self.lock:
            now = time.monotonic()
            new_pause = self.paused_until <= now
            self.paused_until = max(self.paused_until, now + (retry_after or DEFAULT_PAUSE_S))
            self.rate_limited += 1
            if new_pause:
                self.pauses += 1
                self.rpm = max(FLOOR_RPM, self.rpm * CUT_429)
                self.wpm = max(FLOOR_WPM, self.wpm * CUT_429)
        log.warning("RATE LIMITED (429): all requests paused %.0f s; budget %.0f/min, writes %.0f/min (429 #%d)",
                    self.pause_left(), self.rpm, self.wpm, self.rate_limited)

    def note_ok(self, write):
        """Each success wins back 1/60 of a request a minute, up to the settings: an hour of calm undoes a cut."""
        with self.lock:
            self.rpm = min(self.rpm_max, self.rpm + 1 / 60)
            if write:
                self.wpm = min(self.wpm_max, self.wpm + 1 / 60)

    # --- one request ---
    def call(self, method, path, params=None, body=None, ok=(200, 201, 207), write=False, wait=False):
        """One request with budgets and retries. A write also spends the write budget and is refused rather than
        wait long, unless `wait`. A resend carries the same body, so the same idempotency key: the server never
        places twice. A write that timed out after it was sent is NOT resent (day one: every resend earned 409
        REQUEST_IN_FLIGHT, which is not retried either); the caller reads the outcome from the open orders."""
        delay = 0.25                  # back-off, doubling to 8 s
        for attempt in range(MAX_RETRIES + 1):
            last = attempt == MAX_RETRIES
            self.throttle(write, wait)
            try:
                r = self.session.request(method, self.base + path, timeout=TIMEOUT_S,
                                         params={k: v for k, v in (params or {}).items() if v is not None},
                                         data=json.dumps(body) if body is not None else None)
            except requests.RequestException as e:
                if last or (method != "GET" and isinstance(e, requests.exceptions.ReadTimeout)):
                    raise ApiError(0, "NETWORK", str(e))
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
                continue
            data = reply_json(r)
            if r.status_code in ok:
                self.note_ok(write)
                return r.status_code, data
            retry_after = seconds(r.headers.get("Retry-After", ""))
            if r.status_code == 429:
                self.note_429(retry_after)        # throttle() holds the resend until the pause is over
            elif r.status_code in RETRY_STATUS and not last:
                time.sleep(retry_after if retry_after is not None else delay)
                delay = min(delay * 2, 8.0)
            if last or r.status_code not in (429,) + RETRY_STATUS:
                raise error_of(r, data)

    def get(self, path, **params):
        return self.call("GET", path, params=params)[1]

    def paged(self, path, **params):
        out, cursor = [], None
        while True:
            page = self.get(path, cursor=cursor, **params)
            out += page.get("data", [])
            if not (page.get("pagination") or {}).get("hasMore"):
                return out
            cursor = page["pagination"]["nextCursor"]

    # --- reads ---
    def tournament(self):
        t = self.get(f"/tournaments/{self.slug}")
        self.tid, self.t_end = t.get("id"), parse_ts(t.get("endDate"))
        if t.get("initialBalance") is not None:
            self.start_balance = float(t["initialBalance"])
        return t

    def markets(self):
        if self.tid is None:
            self.tournament()
        raw = self.paged(f"/tournaments/{self.slug}/markets", limit=100, status="open")
        return [m for r in raw for m in parse_markets(r, self.t_end)]

    def positions(self):
        """{eid: signed YES shares} of unsettled positions (the API signs them: + YES, - NO); sets `marks`."""
        reply = self.get(f"/tournaments/{self.slug}/portfolio/positions")
        rows = [p for p in reply.get("positions", []) if not p.get("settled")]
        self.marks = {}
        for p in rows:
            mark = next((number(p[k]) for k in MARK_KEYS if number(p.get(k)) is not None), None)
            if mark is not None:
                self.marks[str(p["exchangeId"])] = mark
        self.market_value = number((reply.get("summary") or {}).get("totalMarketValue"))
        return {str(p["exchangeId"]): float(p.get("quantity") or 0) for p in rows if float(p.get("quantity") or 0)}

    def account(self):
        """Value and free cash from /portfolio/pnl. Value falls back to balance + positions; cash prefers a
        figure already net of our orders' locks, else takes them off (from the last open_orders read)."""
        try:
            reply = self.get(f"/tournaments/{self.slug}/portfolio/pnl", period="all")
        except ApiError as e:
            log.warning("P&L read failed (%s): balance + positions instead", e)
            reply = {}
        value, net, gross = (number(reply.get("totalAccountValue")), first_number(reply, CASH_NET_KEYS),
                             first_number(reply, CASH_GROSS_KEYS))
        cash = net if net is not None else (gross - self.locked if gross is not None else None)
        if value is None or cash is None:
            balance = number(self.tournament().get("myBalance"))
            if value is None and balance is not None and self.market_value is not None:
                value = balance + self.market_value
            if cash is None and balance is not None:
                cash = balance - self.locked
        account = Account(value=value, cash=cash, read_at=time.monotonic())
        if self.start_balance is not None:
            account.start = self.start_balance
        return account

    def open_orders(self):
        raw = self.paged("/orders", status="open", tournamentId=self.tid, limit=200)
        self.locked = locked_cash(raw)
        orders = [o for o in (parse_order(r) for r in raw) if o is not None]
        self.directions.update({o.oid: o.is_bid for o in orders})
        return orders

    def book(self, eid, mine):
        raw = self.get(f"/exchanges/{eid}/orderbook", depth=BOOK_DEPTH, tournamentId=self.tid)
        return strip_own(raw, mine)

    def tops(self, eids):
        """{eid: (best bid, best ask)} including our own orders; ids the API does not know are absent."""
        out, eids = {}, list(eids)
        for i in range(0, len(eids), BULK_MAX_IDS):
            res = self.get("/exchanges/prices", ids=",".join(eids[i:i + BULK_MAX_IDS]), tournamentId=self.tid)
            for p in res.get("data", []):
                out[str(p["exchangeId"])] = (number(p.get("bestBid")), number(p.get("bestAsk")))
        return out

    def fills(self, after_id):
        """Fills newer than after_id, oldest first, each with its `fid` (the API pages newest first). With after_id
        None: the newest page only, so a fresh start does not replay the history."""
        raw, cursor = [], None
        for _ in range(FILL_PAGES):
            page = self.get(f"/tournaments/{self.slug}/portfolio/fills", limit=200, cursor=cursor)
            batch = page.get("data", [])
            ids = [str(f["id"]) for f in batch]
            if after_id is not None and str(after_id) in ids:
                return [parse_fill(f, self.directions) for f in reversed(raw + batch[:ids.index(str(after_id))])]
            raw += batch
            if after_id is None or not (page.get("pagination") or {}).get("hasMore"):
                break
            cursor = page["pagination"]["nextCursor"]
        return [parse_fill(f, self.directions) for f in reversed(raw)]

    # --- writes ---
    def place(self, orders, positions):
        """Send orders in batches of BATCH_MAX (each order succeeds or fails alone: one bad market cannot roll
        back the rest). An ioc order's unfilled rest is cancelled at once."""
        if not self.live:
            return [Placed(o, None, 0.0, "dry run") for o in orders]
        out, covered_left = [], {e: max(0.0, -q) for e, q in positions.items()}
        for i in range(0, len(orders), BATCH_MAX):
            chunk = orders[i:i + BATCH_MAX]
            try:
                out += self.place_batch(chunk, covered_left)
            except ApiError as e:
                if e.fatal:
                    raise
                unsent = e.code == "WRITE_BUDGET_WAIT" or 400 <= e.status < 500 and e.status != 429
                out += [Placed(o, None, 0.0, str(e), unknown=not unsent) for o in chunk]
        for p in out:
            if p.order.ioc and p.oid and p.traded < p.order.size:
                self.cancel_rest(p)
        return out

    def cancel_rest(self, placed):
        try:
            self.cancel(placed.oid)
        except ApiError as e:             # it dies on its own within IOC_TTL_S
            log.warning("ioc rest of %s not cancelled (%s): it expires in %.0f s", placed.oid, e, IOC_TTL_S)

    def place_batch(self, orders, covered_left):
        """One POST /orders/batch. A bid while short NO goes out as a covered "sell NO", trimmed to the NO held
        (the old bot's rule: the add beyond it waits for a later cycle)."""
        sent, bodies, now = [], [], utcnow()
        for o in orders:
            cover = covered_left.get(o.eid, 0.0) if o.is_bid else 0.0
            size = min(int(o.size), int(cover)) if cover >= 1 else int(o.size)
            if size < 1:
                continue
            covered_left[o.eid] = cover - size if cover >= 1 else covered_left.get(o.eid, 0.0)
            if o.ioc:
                expires = now + timedelta(seconds=IOC_TTL_S)
            else:
                expires = o.expires or now + timedelta(seconds=self.ttl_s)
            sent.append(Order(o.eid, o.is_bid, o.price, size, o.tag, o.ioc, None, expires))
            bodies.append(wire_order(sent[-1], cover >= 1, self.tid, expires))
        if not bodies:
            return []
        body = {"idempotencyKey": str(uuid.uuid4()), "orders": bodies}
        _, res = self.call("POST", "/orders/batch", body=body, ok=(200, 207, 422), write=True)
        results = {r.get("index"): r for r in res.get("results", [])}
        return [self.placed(o, results.get(k, {})) for k, o in enumerate(sent)]

    def placed(self, order, result):
        data = result.get("data") or {}
        if not result:
            return Placed(order, None, 0.0, "no result for this order", unknown=True)
        if not result.get("ok"):
            err = data.get("error") or {}
            return Placed(order, None, 0.0, (err.get("message") if isinstance(err, dict) else str(err)) or "refused")
        oid = None if data.get("orderId") is None else str(data["orderId"])
        self.directions[oid] = order.is_bid
        return Placed(Order(order.eid, order.is_bid, order.price, order.size, order.tag, order.ioc, oid,
                            order.expires), oid, float(data.get("quantityTraded") or 0), None)

    def cancel(self, oid):
        """True once the order is gone; 404 / 409 mean it already is (filled, expired or cancelled)."""
        if not self.live:
            return True
        try:
            self.call("DELETE", f"/orders/{oid}", ok=(200,), write=True)
        except ApiError as e:
            if e.status not in (404, 409):
                raise
        return True

    def cancel_all(self, eid=None):
        """Every order of ours (or on one market) cancelled; True only when confirmed. It waits for the write
        budget rather than be refused: pulling everything is the one write that must go out."""
        if not self.live:
            return True
        body = {"tournamentId": self.tid, **({"exchangeId": eid} if eid else {})}
        status, res = self.call("POST", "/orders/cancel-all", body=body, ok=(200, 207, 422), write=True, wait=True)
        if status == 200:
            return True
        log.warning("cancel-all partial: %s", res.get("errors"))
        return not [o for o in self.open_orders() if eid is None or o.eid == eid]   # the API: confirm before re-quoting

    def realtime_token(self):
        return self.call("POST", "/realtime/token")[1]


def error_of(r, data):
    """Some errors are plain text ({"error": "Not found"}, the 2 Oct outage): give them a message all the same."""
    err = data.get("error") if isinstance(data, dict) else None
    err = err if isinstance(err, dict) else {"message": str(err or r.text[:200])}
    return ApiError(r.status_code, err.get("code", "?"), err.get("message", ""))


def reply_json(r):
    """A proxy can answer with an HTML page: never let that crash the bot."""
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {"error": {"code": "BAD_RESPONSE", "message": r.text[:200]}}


def seconds(header):
    return float(header) if header.replace(".", "", 1).isdigit() else None


def first_number(d, keys):
    return next((number(d[k]) for k in keys if number(d.get(k)) is not None), None)


def number(x):
    try:
        return None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        return None


# --- the realtime feed ---------------------------------------------------------------------------------------

SESSION_MAX_S = 3600.0        # a long-lived socket can die quietly: renew the session at least hourly...
TOKEN_MARGIN_S = 600.0        # ...and 10 minutes before its 3-hour login runs out
HEALTHY_S = 60.0              # a session up this long that drops is a fresh drop: reconnect after 1 s again
JOIN_TIMEOUT_S = 20.0         # both channels not joined by then: start over
BACKOFF_MAX_S = 60.0


class SocketErrorWatch(logging.Handler):
    """With its own reconnect off, the `realtime` library only LOGS that its socket closed and keeps reporting
    is_connected (29 Sep: dead for two days). This handler turns that log line into a flag."""

    def __init__(self, feed):
        super().__init__(logging.ERROR)
        self.feed = feed

    def emit(self, record):
        text = record.getMessage().lower()
        if "connection closed" in text or "terminating connection" in text:
            self.feed.socket_error = True


class Feed:
    """The two private channels (tournament:{id} market_batch: which books changed; user:{...} account_batch: our
    fills). Delivery is best-effort with no replay, so after any (re)connect or a revision gap it asks the bot for
    a full resync from REST. It never trades; `wake` lets the bot start a cycle at once."""

    def __init__(self, client, tournament_id):
        self.client, self.tid = client, tournament_id
        self.lock = threading.Lock()                  # the socket thread writes, the bot reads
        self.wake = threading.Event()
        self.dirty, self.account_changed, self.resync = set(), False, True
        self.joined, self.revisions = set(), {}       # topics joined; topic -> last revision accepted
        self.connected, self.stopping, self.socket_error = False, False, False
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), name="realtime", daemon=True)

    def start(self):
        logging.getLogger("realtime").addHandler(SocketErrorWatch(self))
        self.thread.start()

    def stop(self):
        self.stopping = True

    def healthy(self):
        return self.connected and len(self.joined) >= 2

    def take(self):
        """(dirty eids, account changed?, resync?) since the last take."""
        self.wake.clear()                             # first: anything arriving after this wakes us again
        with self.lock:
            out = (self.dirty, self.account_changed, self.resync)
            self.dirty, self.account_changed, self.resync = set(), False, False
        return out

    def accept(self, topic, data):
        """A repeated revision is a duplicate; a previousRevision we did not see means we missed a message."""
        delivery = data.get("delivery") or {}
        rev, prev, last = delivery.get("revision"), delivery.get("previousRevision"), self.revisions.get(topic)
        if rev is None:
            return True
        if last is not None and rev == last:
            return False
        if last is not None and prev is not None and prev != last:
            self.flag_resync(f"missed message(s) on {topic}")
        self.revisions[topic] = rev
        return True

    def on_market(self, topic, message):
        data = message.get("payload", message) if isinstance(message, dict) else {}
        if not self.accept(topic, data):
            return
        with self.lock:
            for item in (data.get("bookDirty") or []) + (data.get("trades") or []):
                if item.get("exchangeId") is not None and item.get("tournamentId") in (None, self.tid):
                    self.dirty.add(str(item["exchangeId"]))
            if data.get("marketSettled"):
                self.resync = True                    # the market list must be read again
        self.wake.set()

    def on_account(self, topic, message):
        """Only fills and settlements change positions; order updates are echoes of our own writes, and
        re-reading the account for each would spend most of the budget (the full check catches the rest)."""
        data = message.get("payload", message) if isinstance(message, dict) else {}
        if not self.accept(topic, data) or not any(data.get(k) for k in ("fills", "settlements", "refunds")):
            return
        with self.lock:
            self.account_changed = True
            self.dirty |= {str(f["exchangeId"]) for f in data.get("fills") or [] if f.get("exchangeId") is not None}
        self.wake.set()

    def on_state(self, topic, state, err):
        name = state.name if hasattr(state, "name") else str(state)   # the library passes an enum
        with self.lock:
            if name == "SUBSCRIBED":
                self.joined.add(topic)
                self.revisions.pop(topic, None)       # a new subscription numbers revisions afresh
            else:
                self.joined.discard(topic)
        if name == "SUBSCRIBED":
            self.flag_resync(f"subscribed to {topic.split(':')[0]}")
        else:
            log.warning("realtime: %s -> %s %s", topic.split(":")[0], name, err or "")

    def flag_resync(self, why):
        with self.lock:
            self.resync = True
        log.info("realtime: %s - full resync next cycle", why)
        self.wake.set()

    async def run(self):
        """Stay connected; while down the bot polls. Back-off 1 s doubling to 60 s, reset after a healthy session
        (day one: 2, 4, 8, 16 s waits stacked over two hours for drops that were each fresh)."""
        backoff = 1.0
        while not self.stopping:
            began = time.monotonic()
            try:
                await self.session()
                backoff = 1.0
            except Exception as e:                    # any library or network failure: reconnect, never crash
                if time.monotonic() - began >= HEALTHY_S:
                    backoff = 1.0
                if not self.stopping:
                    log.warning("realtime feed down (%s): polling, reconnecting in %.0f s", e, backoff)
            with self.lock:
                self.connected = False
                self.joined.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    def socket_dead(self, client):
        """is_connected alone cannot be trusted (see SocketErrorWatch): also the close code and the listener task,
        which are the library's private attributes, hence read defensively."""
        if not client.is_connected or self.socket_error:
            return True
        ws, task = getattr(client, "_ws_connection", None), getattr(client, "_listen_task", None)
        return (ws is not None and getattr(ws, "close_code", None) is not None) or bool(task and task.done())

    async def session(self):
        from realtime import AsyncRealtimeClient     # optional package: without it the bot polls
        self.socket_error = False
        tok = await asyncio.to_thread(self.client.realtime_token)
        client = AsyncRealtimeClient(f"{tok['supabaseUrl']}/realtime/v1", token=tok["anonKey"],
                                     params={"apikey": tok["anonKey"]}, auto_reconnect=False)
        await client.connect()
        try:
            await client.set_auth(tok["token"])
            topics = [f"tournament:{self.tid}", tok["channels"]["user"]]
            for topic in topics:
                ch = client.channel(topic, {"config": {"private": True}})
                ch.on_broadcast("market_batch", lambda m, t=topic: self.on_market(t, m))
                ch.on_broadcast("account_batch", lambda m, t=topic: self.on_account(t, m))
                await ch.subscribe(lambda state, err, t=topic: self.on_state(t, state, err))
            self.connected = True
            await self.hold(client, topics, parse_ts(tok.get("expiresAt")))
        finally:
            self.connected = False
            await client.close()

    async def hold(self, client, topics, expires):
        """Watch a live session until it dies (raise) or is due for renewal (return: fresh login, full resync)."""
        started = time.monotonic()
        deadline = started + SESSION_MAX_S
        if expires:
            deadline = min(deadline, started + (expires - utcnow()).total_seconds() - TOKEN_MARGIN_S)
        while not self.stopping:
            await asyncio.sleep(1)
            if self.socket_dead(client):
                raise ConnectionError("socket closed")
            if len(self.joined) < len(topics) and time.monotonic() - started > JOIN_TIMEOUT_S:
                raise ConnectionError("a channel subscription failed or dropped")
            if time.monotonic() >= deadline:
                log.info("realtime: renewing the session")
                return
