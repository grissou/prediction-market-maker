"""
The exchange: ApiError, Api (HTTP, retries, rate limiting, one method per endpoint),
_SocketErrorWatch and RealtimeFeed (the push feed on a background thread), plus the per-market state
record Ex, the order-change records Change / Write, state_of and busy.

Must never decide prices or sizes and must never import the bot or the mixins.
"""
import asyncio
import bisect
import json
import logging.handlers
import random
import re
import requests
import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter

from mmbot import util
from mmbot import pricing
from mmbot import quoting
from mmbot.util import iso, log, parse_ts, rnd
from mmbot.pricing import _num
from mmbot.quoting import NO_QUOTE, Quote, fl_side


# =============================================================================================
# API - HTTP, retries, rate limiting, one method per endpoint we use
# =============================================================================================

class ApiError(Exception):
    def __init__(self, status, code, msg, data=None):
        super().__init__(f"{status} {code}: {msg}")
        self.status, self.code, self.data = status, code, data


class Api:
    def __init__(self, cfg, live):
        if not cfg.api_key:
            util.fatal("SUPERMARKET_API_KEY is not set (venv activate script or .env file)")
        self.cfg, self.live = cfg, live
        self.gap = cfg.min_request_gap    # current min gap between request starts; widens after a 429
        self._next_start = 0.0            # earliest time the next request may start (shared by all threads)
        self._lock = threading.Lock()     # several threads send requests at once (parallel_requests)
        self._window = deque()            # start times of requests in the last BUDGET_WINDOW seconds
        self.budget = float(cfg.requests_per_minute)   # current per-minute budget (self-tuning)
        self.wbudget = float(cfg.writes_per_minute)    # ...and for order writes on their own (self-tuning)
        self._wwindow = deque()           # start times of writes in the last BUDGET_WINDOW seconds
        self.write_times = deque(maxlen=500)   # (time done, seconds on the wire, timed out) per write attempt
        self.rate_limited = 0             # how many 429s we've had (shown in status.json - should stay 0)
        self.paused_until = 0.0           # monotonic end of the exchange's 429 pause (Retry-After); 0 = none
        self.pauses_total = 0             # 429 pauses started (several 429s inside one pause extend it)
        self.write_budget_wait_total = 0  # main-thread writes not sent: they would have waited (WRITE_BUDGET_WAIT)
        self.tl = threading.local()       # per thread: max_write_wait (the main thread's cap on a write's wait)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"})
        # Keep one open connection per thread that can talk to the exchange at once, so none has to open (and
        # then throw away: "Connection pool is full, discarding connection", 2 Oct 08:08:57) a fresh TLS
        # connection, which costs 0.2-0.5 s and inflates the write times burst mode watches.
        self.s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=self.pool_size(cfg), pool_block=False))

    @staticmethod
    def pool_size(cfg):
        """Connections to keep: book downloads (parallel_requests) + order writes (parallel_writes) + the main
        thread + the self-test thread + the realtime token refresh + one spare."""
        return max(1, cfg.parallel_requests) + max(1, cfg.parallel_writes) + 4

    BUDGET_WINDOW = 60.0                  # seconds the per-minute budget is measured over

    def throttle(self, write=False):
        """Rate limiter for every request, across all threads:
          1. never more than `self.budget` requests in any BUDGET_WINDOW seconds (hard cap), and
          2. request starts at least `self.gap` apart (smooths bursts), and
          3. nothing starts during the exchange's 429 pause (paused_until).
        A request that would break a rule waits until it wouldn't (sleeping WITHOUT the lock). A write on a thread
        with tl.max_write_wait set (the main thread, during a cycle) that would wait longer raises ApiError 429
        WRITE_BUDGET_WAIT instead, unsent and unreserved: the main loop never blocks on the write budget."""
        with self._lock:
            now = util.time.monotonic()
            while self._window and now - self._window[0] >= self.BUDGET_WINDOW:
                self._window.popleft()
            start = max(now, self._next_start, self.paused_until)
            if len(self._window) >= int(self.budget):
                start = max(start, self._window[-int(self.budget)] + self.BUDGET_WINDOW)
            general = start
            if write:
                while self._wwindow and now - self._wwindow[0] >= self.BUDGET_WINDOW:
                    self._wwindow.popleft()
                if len(self._wwindow) >= int(self.wbudget):
                    start = max(start, self._wwindow[-int(self.wbudget)] + self.BUDGET_WINDOW)
                cap = getattr(self.tl, "max_write_wait", None)
                if cap is not None and start - now > cap:
                    self.write_budget_wait_total = getattr(self, "write_budget_wait_total", 0) + 1
                    raise ApiError(429, "WRITE_BUDGET_WAIT", f"a write would wait {start - now:.0f} s for the write "
                                   f"budget / rate-limit pause (main thread: at most {cap:.0f} s) - not sent")
                bisect.insort(self._wwindow, start)
            self._next_start = general + self.gap   # a write waiting on the WRITE budget never holds up reads
            # Kept sorted: a write held back by the write budget starts later than reads throttled after it, and
            # the clean-up above and the [-budget] lookup both assume oldest-first order.
            bisect.insort(self._window, start)
        while start > now:
            util.time.sleep(start - now)
            # A 429 pause that began while this request slept: wait it out too, instead of knocking during it
            # (each such request earned another 429 and, before, another silent 60 s pause).
            now = util.time.monotonic()
            with self._lock:
                start = self.paused_until

    def pause_left(self):
        """Seconds left of the exchange's 429 pause (0 = none)."""
        return max(0.0, getattr(self, "paused_until", 0.0) - util.time.monotonic())

    def write_wait(self):
        """Seconds a write sent now would wait (write budget full, or a 429 pause)."""
        if not hasattr(self, "_wwindow"):         # (test doubles without the limiter)
            return 0.0
        with self._lock:
            now = util.time.monotonic()
            wait_s = max(0.0, self.paused_until - now, self._next_start - now)
            live = [t for t in self._wwindow if now - t < self.BUDGET_WINDOW]
            if len(live) >= int(self.wbudget):
                wait_s = max(wait_s, live[-int(self.wbudget)] + self.BUDGET_WINDOW - now)
            return wait_s

    def pause_state(self):
        """For status.json: the 429 pause now, how many there were, the 429s."""
        left = self.pause_left()
        return {"paused_until": iso(util.utcnow() + timedelta(seconds=left)) if left > 0 else None,
                "pause_seconds_left": round(left, 1), "pauses_total": getattr(self, "pauses_total", 0),
                "rate_limited_total": getattr(self, "rate_limited", 0)}

    def budget_left(self):
        """How many more requests fit in the budget right now."""
        with self._lock:
            now = util.time.monotonic()
            used = sum(1 for t in self._window if now - t < self.BUDGET_WINDOW)
            return int(self.budget) - used

    def write_ceiling(self):
        """What the self-tuning write budget may grow to: writes_per_minute_max (never below the start value)."""
        return float(max(self.cfg.writes_per_minute, getattr(self.cfg, "writes_per_minute_max", 0)))

    def writes_left(self):
        """How many more order writes fit in the write budget right now."""
        with self._lock:
            now = util.time.monotonic()
            return int(self.wbudget) - sum(1 for t in self._wwindow if now - t < self.BUDGET_WINDOW)

    def call(self, method, path, params=None, body=None, ok=(200, 201, 207), retry_sent=True):
        """One HTTP request with rate limiting and retries.

        Retried automatically (same body -> same idempotencyKey -> the server never places twice):
          - network errors / timeouts
          - 429 RATE_LIMITED (also slows down every later request)
          - 502 ORDER_STATUS_UNKNOWN, 503 SERVICE_UNAVAILABLE / TX_CONFLICT, 504
        Anything else raises ApiError straight away: a 4xx means we broke a rule, so retrying won't help.
        409 REQUEST_IN_FLIGHT is deliberately NOT retried here (the API says wait 90 s); the bot
        handles it by leaving that exchange alone for `pending_seconds` instead of blocking.
        """
        retries = self.cfg.max_retries
        delay = 0.25                      # first back-off wait; doubles each retry up to 8 s
        for attempt in range(retries + 1):
            self.throttle(write=method != "GET")
            t_sent = util.time.monotonic()
            try:
                r = self.s.request(method, self.cfg.base_url + path, timeout=self.cfg.request_timeout,
                                   params={k: v for k, v in (params or {}).items() if v is not None},
                                   data=json.dumps(body) if body is not None else None)
                self.last_date = (r.headers.get("Date"), util.utcnow())   # server clock vs ours (see Bot.check_clock)
            except requests.RequestException as e:
                # A write that timed out AFTER it was sent is usually still running on the exchange: retrying it
                # at once only earns 409 REQUEST_IN_FLIGHT (day one, every time) and costs a request. Hand it
                # back as "outcome unknown" instead; the bot recovers it from the open-orders list.
                sent = isinstance(e, requests.exceptions.ReadTimeout)
                if method != "GET":
                    self.write_times.append((util.time.monotonic(), util.time.monotonic() - t_sent, sent))
                if attempt == retries or (sent and not retry_sent and self.cfg.write_fail_fast):
                    raise ApiError(0, "NETWORK", str(e))
                util.time.sleep(delay); delay = min(delay * 2, 8)
                continue

            if method != "GET":
                self.write_times.append((util.time.monotonic(), util.time.monotonic() - t_sent, False))
            # A proxy can answer with an HTML error page; never let that crash the bot.
            try:
                data = r.json() if r.content else {}
            except ValueError:
                data = {"error": {"code": "BAD_RESPONSE", "message": r.text[:200]}}

            if r.status_code in ok:
                self.gap = max(self.cfg.min_request_gap, self.gap * 0.99)   # drift back to normal speed
                self.budget = min(self.cfg.requests_per_minute, self.budget + 1 / 60)   # ~+1 per 60 successes
                if method != "GET":   # additive increase, up to writes_per_minute_max (AIMD; cut on a 429 below)
                    self.wbudget = min(self.write_ceiling(), self.wbudget + 1 / 60)
                return r.status_code, data

            err = data.get("error") if isinstance(data, dict) else None
            if not isinstance(err, dict):         # some errors are plain text: {"error": "Not found"} (2026-10-02 outage)
                err = {"message": str(err)} if err else {}
            ra = r.headers.get("Retry-After", "")
            retry_after = float(ra) if ra.replace(".", "", 1).isdigit() else None
            if r.status_code == 429:
                # Rate limited. Don't just retry this one request: pause EVERY thread for as long as the
                # server asks, and slow down for good, so we never keep knocking on a closed door.
                # A 429 inside a pause EXTENDS it to Retry-After from now (never added up); the budgets are cut
                # once per pause, not per 429. Every 429 is logged (2 Oct: 20 counted, none logged - the old
                # "already paused" test was fooled by ordinary queued requests).
                pause = retry_after if retry_after is not None else 5.0
                with self._lock:
                    now = util.time.monotonic()
                    already_paused = self.paused_until > now
                    left_before = max(0.0, self.paused_until - now)
                    self.paused_until = max(self.paused_until, now + pause)
                    self._next_start = max(self._next_start, self.paused_until)
                    if not already_paused:        # the budget was too generous: cut it by a quarter
                        self.pauses_total += 1
                        self.gap = min(self.cfg.max_request_gap, self.gap * 2)
                        self.budget = max(20.0, self.budget * 0.75)
                        if method != "GET" or self.wbudget > self.cfg.writes_per_minute:
                            # (a per-key limit 429s the more frequent reads first: a grown write budget is cut too)
                            self.wbudget = max(10.0, self.wbudget * self.cfg.write_budget_cut)
                    self.rate_limited += 1
                    until = self.paused_until - now
                log.warning("RATE LIMITED (429) %s %s: Retry-After %s; %s - all requests paused %.0f s; budget %.0f/min, "
                            "writes %.0f/min (429 #%d, pause #%d)", method, path.split("?")[0], ra or "-",
                            f"already paused ({left_before:.0f} s left): pause extended" if already_paused
                            else "new pause", until, self.budget, self.wbudget, self.rate_limited, self.pauses_total)
                if attempt < retries:
                    continue                  # throttle() makes the retry wait until the pause is over
            elif r.status_code in (502, 503, 504) and attempt < retries:
                util.time.sleep(retry_after if retry_after is not None else delay + random.random() * delay)
                delay = min(delay * 2, 8)
                continue
            raise ApiError(r.status_code, err.get("code", "?"), err.get("message", r.text[:200]), data)

    def get(self, path, **params):
        return self.call("GET", path, params=params)[1]

    def paged(self, path, **params):
        """GET every page of a cursor-paginated list endpoint."""
        out, cursor = [], None
        while True:
            page = self.get(path, cursor=cursor, **params)
            out += page.get("data", [])
            if not page.get("pagination", {}).get("hasMore"):
                return out
            cursor = page["pagination"]["nextCursor"]

    # --- reads ------------------------------------------------------------------------------
    def tournament(self):
        return self.get(f"/tournaments/{self.cfg.slug}")

    def markets(self):
        return self.paged(f"/tournaments/{self.cfg.slug}/markets", limit=100, status="open")

    def positions(self):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/positions")

    def pnl(self):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/pnl", period="all")

    def leaderboard(self):
        """Our rank: {"myRank": 3, "total": 120, ...} (period = all-time)."""
        return self.get(f"/tournaments/{self.cfg.slug}/leaderboard", limit=1)

    def smart_score(self):
        """Our Smart Score entries ([] until we've been scored): smartScoreDecayed, rank, totalTraders,
        isElite... per marketType. SIG's recruiting export ranks candidates by this score."""
        return self.get(f"/tournaments/{self.cfg.slug}/me/smart-score")

    def fills_page(self, cursor=None):
        return self.get(f"/tournaments/{self.cfg.slug}/portfolio/fills", limit=200, cursor=cursor)

    def open_orders(self, tid, eid=None):
        return self.paged("/orders", status="open", tournamentId=tid, exchangeId=eid, limit=200)

    def book(self, eid, tid):
        return self.get(f"/exchanges/{eid}/orderbook", depth=self.cfg.book_depth, tournamentId=tid)

    def bulk_prices(self, eids, tid):
        """Best bid / best ask for up to BULK_MAX_IDS exchanges in one request: {eid: (bid, ask)}.
        Includes our own orders. Exchanges the API doesn't recognise are simply missing."""
        res = self.get("/exchanges/prices", ids=",".join(eids), tournamentId=tid)
        return {str(p["exchangeId"]): (rnd(p.get("bestBid")), rnd(p.get("bestAsk"))) for p in res.get("data", [])}

    # --- writes (no-ops in dry run) ---------------------------------------------------------
    def cancel_all(self, tid, eid=None):
        """Cancel all our orders in the tournament (or on one exchange). True = confirmed gone."""
        if not self.live:
            return True
        body = {"tournamentId": tid, **({"exchangeId": eid} if eid else {})}
        status, res = self.call("POST", "/orders/cancel-all", body=body, ok=(200, 207, 422), retry_sent=False)
        if status == 200:
            return True
        log.warning("cancel-all partial: %s", res.get("errors"))
        return not self.open_orders(tid, eid)   # API docs: confirm nothing rests before re-quoting

    def cancel_order(self, order_id):
        """Cancel one order. 404/409 mean it's already gone (filled or cancelled) - that's fine too."""
        if not self.live:
            return True
        try:
            self.call("DELETE", f"/orders/{order_id}", ok=(200,), retry_sent=False)
        except ApiError as e:
            if e.status in (404, 409):
                return True
            raise
        return True

    def place_batch(self, orders):
        """POST /orders/batch -> one result per order: {"index", "ok", "status", "data"}.

        Batch (not multi-leg) on purpose: each order succeeds or fails on its own, so one bad
        exchange can't roll back the quotes for the other 19 in the request.
        """
        if not self.live:
            return [{"index": k, "ok": True, "status": 200, "data": {}} for k in range(len(orders))]
        body = {"idempotencyKey": str(uuid.uuid4()), "orders": orders}
        _, res = self.call("POST", "/orders/batch", body=body, ok=(200, 207, 422), retry_sent=False)
        return res.get("results", [])

# =============================================================================================
# REALTIME FEED - the exchange pushes changes to us (Supabase Realtime), on a background thread
# =============================================================================================

class _SocketErrorWatch(logging.Handler):
    """Flags the feed when the `realtime` library logs that its socket closed. Needed because, with its
    auto-reconnect off (we reconnect ourselves), the library only LOGS the closure: it keeps reporting
    is_connected=True and keeps heartbeating into the dead socket. Seen live on 29 Sep: dead for 2 days."""
    def __init__(self, feed):
        super().__init__(logging.ERROR)
        self.feed = feed

    def emit(self, record):
        text = record.getMessage().lower()
        if "connection closed" in text or "terminating connection" in text:
            self.feed.socket_error = True


class RealtimeFeed:
    """Listens to two private channels and tells the main loop what changed:

      tournament:{id}   market_batch  -> which books changed (bookDirty), trades, settled markets
      user:{profile}    account_batch -> our fills, order updates, settlements

    It never trades or makes decisions. The main loop calls take() to collect what happened, and
    `wake` lets it start a cycle straight away. The API says delivery is best-effort (no replay), so
    after connecting, reconnecting, a token refresh or a missed message (revision gap) it asks the
    main loop for a full resync from the REST API.
    """

    def __init__(self, api, tid, cfg):
        self.api, self.tid, self.cfg = api, tid, cfg
        self.lock = threading.Lock()      # the socket thread writes, the main loop reads
        self.wake = threading.Event()     # set when something happened -> main loop cycles early
        self.dirty = set()                # exchanges whose book changed since the last take()
        self.account_changed = False      # fills / settlements since the last take()
        self.settled = False              # a market settled -> the market list should be reloaded
        self.resync = True                # the main loop should do a full check from REST
        self.topics_joined = set()
        self.connected = False
        self.stopping = False
        self.last_revision = {}           # topic -> last revision number accepted
        self.events = 0                   # messages received (shown in status.json)
        self.trade_counts = defaultdict(int)   # exchange -> tournament trades seen (all traders)
        self.trade_log = deque(maxlen=20000)   # (unix time, trade item) for the recorder (see take_trades)
        self.flow_log = deque(maxlen=20000)    # (unix time, exchange id, quantity) for turnover control (take_flow)
        self.socket_error = False         # the library logged that the socket closed (see _SocketErrorWatch)
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), name="realtime", daemon=True)

    # --- used by the main loop -------------------------------------------------------------
    def start(self):
        logging.getLogger("realtime").addHandler(_SocketErrorWatch(self))   # the library logs under "realtime.*"
        self.thread.start()

    def stop(self):
        self.stopping = True

    def healthy(self):
        """Connected and joined to both channels, so pushed updates can be trusted to arrive."""
        return self.connected and len(self.topics_joined) >= 2

    def take_trades(self):
        """Tournament trades reported since the last call: [(unix time received, trade item)]."""
        with self.lock:
            out = list(self.trade_log)
            self.trade_log.clear()
        return out

    def take_flow(self):
        """Tournament trades since the last call, for turnover control: [(unix time, exchange id, quantity)]."""
        with self.lock:
            out = list(self.flow_log)
            self.flow_log.clear()
        return out

    def take(self):
        """Everything reported since the last call: (dirty exchange ids, account changed?, resync?, settled?)."""
        self.wake.clear()                 # cleared first: anything arriving after this wakes us again
        with self.lock:
            out = (self.dirty, self.account_changed, self.resync, self.settled)
            self.dirty, self.account_changed, self.resync, self.settled = set(), False, False, False
        return out

    # --- message handlers (run on the socket thread) ----------------------------------------
    @staticmethod
    def _data(message):
        """A broadcast arrives as {"event", "payload", "type"}; the useful part is the payload."""
        return message.get("payload", message) if isinstance(message, dict) else {}

    def _accept(self, topic, data):
        """Revision check. The same revision twice = a duplicate (ignore it). previousRevision that
        isn't the last one we saw = we missed a message, so ask for a full resync."""
        delivery = data.get("delivery") or {}
        rev, prev = delivery.get("revision"), delivery.get("previousRevision")
        last = self.last_revision.get(topic)
        if rev is not None:
            if last is not None and rev == last:
                return False
            if last is not None and prev is not None and prev != last:
                self._flag_resync(f"missed message(s) on {topic}")
            self.last_revision[topic] = rev
        return True

    def _on_market(self, topic, message):
        data = self._data(message)
        if not self._accept(topic, data):
            return
        with self.lock:
            self.events += 1
            for item in (data.get("bookDirty") or []) + (data.get("trades") or []):
                if item.get("exchangeId") is not None and item.get("tournamentId") in (None, self.tid):
                    self.dirty.add(str(item["exchangeId"]))
            for item in data.get("trades") or []:              # how busy each market is (sizes quotes by it)
                if item.get("exchangeId") is not None and item.get("tournamentId") in (None, self.tid):
                    self.trade_counts[str(item["exchangeId"])] += 1
                    self.trade_log.append((util.time.time(), item))
                    self.flow_log.append((util.time.time(), str(item["exchangeId"]), _num(item.get("quantity"))))
            if data.get("marketSettled"):
                self.settled = True
        self.wake.set()

    def _on_account(self, topic, message):
        data = self._data(message)
        if not self._accept(topic, data):
            return
        with self.lock:
            self.events += 1
            # Only fills / settlements change our positions. orderUpdates are mostly echoes of our own
            # placements and cancels, which the bot already knows about: re-reading the account for each
            # of those would spend most of the request budget (the 30 s full check catches anything else).
            relevant = any(data.get(k) for k in ("fills", "settlements", "refunds"))
            if relevant:
                self.account_changed = True
            for f in data.get("fills") or []:          # a fill changed that book too
                if f.get("exchangeId") is not None:
                    self.dirty.add(str(f["exchangeId"]))
        if relevant:
            self.wake.set()

    def _on_state(self, topic, state, err):
        name = getattr(state, "name", str(state))
        with self.lock:
            if name == "SUBSCRIBED":
                self.topics_joined.add(topic)
                self.last_revision.pop(topic, None)      # new subscription: revisions start afresh
            else:
                self.topics_joined.discard(topic)
        if name == "SUBSCRIBED":
            self._flag_resync(f"subscribed to {topic.split(':')[0]}")
        else:
            log.warning("realtime: %s -> %s %s", topic.split(":")[0], name, err or "")

    def _flag_resync(self, why):
        with self.lock:
            self.resync = True
        log.info("realtime: %s - full resync next cycle", why)
        self.wake.set()

    # --- connection management (socket thread) ----------------------------------------------
    async def _run(self):
        """Stay connected: on any problem, mark the feed down (the bot polls meanwhile), wait with
        growing back-off, and connect again."""
        backoff = 1.0
        while not self.stopping:
            self.session_connected_at = None
            try:
                await self._session()
                backoff = 1.0
            except Exception as e:
                # A session that was healthy for a while ended unexpectedly: that's a fresh drop, not a
                # repeat failure, so start again from the shortest wait (day one: 2, 4, 8, 16 s over 2 hours).
                up = self.session_connected_at
                if up is not None and util.time.monotonic() - up >= self.cfg.realtime_healthy_seconds:
                    backoff = 1.0
                if not self.stopping:
                    log.warning("realtime feed down (%s) - polling meanwhile, reconnecting in %.0f s", e, backoff)
            with self.lock:
                self.connected = False
                self.topics_joined.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _mint_token(self):
        """A 3-hour realtime login from the REST API (one request, through our rate limiter)."""
        return (await asyncio.to_thread(self.api.call, "POST", "/realtime/token"))[1]

    def _socket_dead(self, client):
        """Is this session's socket gone? client.is_connected alone can't be trusted (see _SocketErrorWatch),
        so also check the library's error log, the socket's close code and whether its listener stopped."""
        if not client.is_connected or self.socket_error:
            return True
        ws = getattr(client, "_ws_connection", None)
        if ws is not None and getattr(ws, "close_code", None) is not None:
            return True
        task = getattr(client, "_listen_task", None)
        return bool(task is not None and task.done())

    async def _session(self):
        from realtime import AsyncRealtimeClient          # imported here: the package is optional
        self.socket_error = False
        tok = await self._mint_token()
        client = AsyncRealtimeClient(f"{tok['supabaseUrl']}/realtime/v1", token=tok["anonKey"],
                                     params={"apikey": tok["anonKey"]}, auto_reconnect=False)
        await client.connect()
        try:
            await client.set_auth(tok["token"])
            topics = [f"tournament:{self.tid}", tok["channels"]["user"]]
            for topic in topics:
                ch = client.channel(topic, {"config": {"private": True}})
                ch.on_broadcast("market_batch", lambda m, t=topic: self._on_market(t, m))
                ch.on_broadcast("account_batch", lambda m, t=topic: self._on_account(t, m))
                await ch.subscribe(lambda state, err, t=topic: self._on_state(t, state, err))
            self.connected = True
            self.session_connected_at = util.time.monotonic()
            log.info("realtime feed connected - reacting to pushed updates")
            # The session ends (and a new one starts, with a fresh login and a full resync) before the token
            # runs out, and at least every realtime_session_max_seconds anyway.
            started, expires = util.time.monotonic(), parse_ts(tok.get("expiresAt"))
            deadline = started + self.cfg.realtime_session_max_seconds
            if expires:
                deadline = min(deadline, started + (expires - util.utcnow()).total_seconds()
                               - self.cfg.realtime_token_refresh_seconds)
            while not self.stopping:
                await asyncio.sleep(1)
                if self._socket_dead(client):
                    raise ConnectionError("socket closed")
                if len(self.topics_joined) < len(topics) and util.time.monotonic() - started > 20:
                    raise ConnectionError("a channel subscription failed or dropped")
                if util.time.monotonic() >= deadline:
                    log.info("realtime: renewing the session (fresh login, full resync)")
                    return
        finally:
            self.connected = False
            await client.close()

# =============================================================================================
# BOT - state, the cycle, risk, order management, main loop
# =============================================================================================

@dataclass
class Ex:
    """Everything the bot remembers about one exchange (one tradable YES contract)."""
    eid: str
    market_id: str
    title: str
    label: str                            # short name for logs, e.g. "Rep Ohio Senate"
    group: str                            # race name; exchanges in one group exclude each other
    party: str | None
    close: datetime | None
    book: dict | None = None              # latest order book with OUR orders removed
    book_time: float = 0.0                # util.time.monotonic() when it was downloaded
    verified: float = 0.0                 # when it was last confirmed current: downloaded, or a bulk check
                                          #   showed the same best prices. Staleness is measured from this
    last_fv: float | None = None
    cooldown_until: float = 0.0           # jump guard
    pending_until: float = 0.0            # placement outcome unknown: don't place again yet
    pause_until: float = 0.0              # order was rejected: back off
    unconfirmed_hold: float = -1.0        # the pending_until set for orders whose batch outcome was unknown
    writes: int = 0                       # our writes (cancels / batches) touching it still in flight
    reprices: dict = field(default_factory=dict)   # side -> times we repriced it lately (churn control)
    ref_moved_at: float = -1e9            # last time its Polymarket price moved >= urgent_ref_move
    ref_jump_at: float = -1e9             # last time its Polymarket price moved >= ref_jump_threshold (monotonic;
                                          #   reduce_from_book pauses after it)
    cancelling: bool = False              # ...one of them is a cancel
    inv: float = 0.0                      # for logging / recording
    eff: float = 0.0                      # for logging
    age: float = 0.0                      # share-weighted age of the position, hours (age skew; logging)
    ref: float | None = None              # outside reference price, if any (for logging / recording)
    quote: Quote = NO_QUOTE               # what we decided this cycle (for recording)
    take_dir: int = 0                     # +1 = Polymarket above the best ask, -1 = below the best bid, 0 = neither
    take_since: float = 0.0               # since when every Polymarket reading has shown that same gap
    take_until: float = 0.0               # after taking here, leave it alone until this time
    fl_side: str | None = None            # favourite-longshot bias side last cycle (for its hysteresis)
    fl_tag: str = ""                      # that bias for the quote log line ("" = none active)
    turnover_dead: bool = False           # holding a position in a market with too little flow (health only)
    ro_clip: str = ""                     # "bid" / "ask" / "bid ask": side(s) decide() left empty ONLY by the reduce-only
                                          #   race-net clip this cycle (diagnostic)
    mmr_tail: bool = False                # P12 ops: value adds paused and a tail market (decide; the ladder too)
    st_caps: tuple | None = None          # P15 state_max_usd: decide's (bid, ask) adding caps in shares (the ladder)


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
_STATE_CODES = set(US_STATES.values())
_STATE_NAMES = sorted(US_STATES, key=len, reverse=True)       # (longest first: "West Virginia" before "Virginia")
_STATE_CODE_RE = re.compile(r"^([A-Z]{2})(?:-(?:\d{1,2}|AL))?(?=\s|$)")


def state_of(label):
    """P15: the US state a market belongs to, as its postal code ("Rep Rhode Island Senate", "Dem Rhode Island
    Governor", "Ind RI Governor", "Dem RI-01 House race" -> "RI"), or None (the headline "U.S. House" / "U.S. Senate"
    markets, anything unrecognised). The label's first word (the party: "Rep", "Dem", "Ind", ...) is skipped when the
    whole label does not start with a state."""
    if not isinstance(label, str) or not label.strip():
        return None
    s = " ".join(label.split())
    tries = [s]
    if " " in s:
        tries.append(s.split(" ", 1)[1])
    for t in tries:
        if t.startswith("U.S.") or t.startswith("US "):
            return None
        m = _STATE_CODE_RE.match(t)
        if m and m.group(1) in _STATE_CODES:
            return m.group(1)
        low = t.lower()
        for name in _STATE_NAMES:
            n = name.lower()
            if low == n or low.startswith(n + " "):
                return US_STATES[name]
    return None


def busy(ex, now_m):
    """Don't place on this exchange: an earlier placement's outcome is unknown, or a write is still running."""
    return now_m < ex.pending_until or ex.writes > 0


class Change:
    """What one exchange needs this cycle: orders to cancel first (all of them with whole=True), then new ones.
    key orders the work: pulls first, then party-control markets, then the biggest quotes."""
    __slots__ = ("ex", "doomed", "whole", "new", "key", "count", "unsafe")

    def __init__(self, ex, doomed, whole, new, key, count=True, unsafe=False):
        self.ex, self.doomed, self.whole, self.new, self.key = ex, doomed, whole, new, key
        self.count = count            # its cancels count as reprices (churn control) once sent
        self.unsafe = unsafe          # removes an order that must not stay (urgent_writes_per_cycle sends these first)

    def reprice_sides(self):
        """The sides this change cancels resting orders on (what churn control counts as a reprice)."""
        if not self.count:
            return []
        return [side for side, is_bid in (("bid", True), ("ask", False))
                if any(o.is_bid == is_bid for o in self.doomed)]


class Write:
    """One order write running on a writer thread: kind "cancel" (eid, orders, whole) or "batch" (chunk)."""
    __slots__ = ("kind", "eids", "payload", "future", "sent", "change", "payload_ok", "done_at", "cash_need")

    def __init__(self, kind, eids, payload, sent, change=None):
        self.kind, self.eids, self.payload, self.sent, self.change = kind, eids, payload, sent, change
        self.future, self.payload_ok, self.done_at = None, False, None   # payload_ok: a cancel confirmed
        self.cash_need = 0.0          # Package 8 cash gate: what this batch's orders need (counted as sent)


def fmt(price, size):
    return f"{price:.3f} x{size:<4d}" if price is not None else "   -       "
