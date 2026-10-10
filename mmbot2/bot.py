"""
The bot: its state and the cycle, read top to bottom as README §4 tells it.

OWNS     the cycle (read -> price -> risk -> decide: allocator, ladder, quotes -> floor -> reconcile -> gate ->
         send -> report), our resting orders and what each one is for, the realised P&L, status.json, the phone
         summary line and the saved state that survives a restart.
NEVER    decides a price or a size (the strategy modules do), sends anything in a dry run (it logs WOULD and
         keeps the order as simulated), sends an order that would trade against one of our own, or lets one
         market's failure stop the others.
ORIGIN   The old bot was one class of seven mixins and ~150 attributes, its cycle a 300-line function with a flag
         check every few lines. What survived sixteen releases is a short story: score every holding and every
         level by edge per unit of cash (finding 3.2), sell the least, buy the most, never below value (the 3.7k
         morning), keep market making small (finding 3.3), and spend cash only where a buyer can place it (the
         refill that sold 110k of value for 43k of buying, RETURNS_ATTRIBUTION.md). A strategy that is off is
         simply not called (config.strategies).
OPEN     Value quotes on the reducing side in the tails are left to the allocator and the ladder; the old bot
         also rested them at fair value plus 1c, where they almost never filled. Owner: keep it that way?
"""
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from mmbot2 import config, ladder, mm, pricing, risk, value
from mmbot2.exchange import ApiError
from mmbot2.state import Account, Order, View, held_usd

log = logging.getLogger("mm2")

BOOK_STALE_S = 300.0          # a book older than this is not traded on (the old book_stale)
BOOK_REFRESH_S = 120.0        # a book is re-read at least this often when the request budget allows
REQUEST_SPARE = 10            # requests left unspent each minute, for the writes' own reads and a resync
PRICE_TOL = 1e-6
SIZE_KEEP_FRAC = 0.5          # a resting order at the wanted price is kept while it is at least half the wanted size...
SIZE_TOL = 1.0                # ...and no larger than the gate now allows (a cap that tightened replaces it)
EXPIRY_MARGIN_S = 120.0       # an order this close to its expiry is replaced, so a quote never lapses
BATCH_MAX = 20                # orders per batch request (the API's limit)
PRIORITY = ("alloc", "refill", "recycle", "ladder", "value", "mm")   # who gets cash and writes first


def by_priority(orders):
    """Orders in the order they get cash and writes (stable within a strategy)."""
    return sorted(orders, key=lambda o: PRIORITY.index(o.tag) if o.tag in PRIORITY else len(PRIORITY))


def covered(order, qty):
    """A bid while we are short goes out as the covered "sell NO", which the exchange trims to the NO held
    (exchange.place); trim it here too, so reconcile compares with what can actually rest."""
    if order.is_bid and qty < 0 and order.size > -qty:
        return replace(order, size=-qty)
    return order


def utcnow():
    return datetime.now(timezone.utc)


class Bot:
    def __init__(self, client, feed, refs, s, env, live, alert=lambda msg: None):
        self.client, self.feed, self.refs, self.s, self.env, self.live = client, feed, refs, s, env, live
        self.alert = alert                     # phone alert (ops.notify), injected so tests stay silent
        self.settings_file = config.SettingsFile(env.settings_path)
        self.settings_read = -1e9              # monotonic time the settings file was last checked
        self.tournament_id = None              # the tournament's id, for the feed and order requests
        self.markets = {}                      # eid -> Market, reloaded every MARKETS_RELOAD_S
        self.markets_read = -1e9
        self.books = {}                        # eid -> Book of other traders
        self.tops = {}                         # eid -> (bid, ask) from the last bulk read, ours included
        self.positions = {}                    # eid -> signed YES shares
        self.marks = {}                        # eid -> the exchange's valuation price (leaderboard)
        self.account = Account()
        self.orders = {}                       # oid -> Order resting (simulated in a dry run)
        self.account_orders = {}               # eid -> the account's real orders at the last full read
        self.tags = {}                         # oid -> strategy tag, kept across restarts for fill attribution
        self.last_full = -1e9                  # monotonic time of the last full REST reconciliation
        self.last_fill_id = None               # newest fill already processed
        self.cost = {}                         # eid -> average cost of the open position (realised P&L)
        self.realised = 0.0                    # realised P&L from our fills
        self.last_p, self.paused = {}, {}      # jump guard: last fair value, and eid -> paused until (monotonic)
        self.refused = {}                      # tag -> cash the gate refused last cycle (the refill's demand)
        self.reduce_only = False
        self.risk = None                       # the last RiskState, for status
        self.deferred = 0                      # writes deferred to the next cycle by the budget (never dropped)
        self.sim_ids = 0                       # dry-run order ids
        self.last_cycle_mono = time.monotonic()
        self.running, self.killed = True, False
        self.kill_hits = 0                     # account readings in a row below the kill line
        self.tilt = pricing.TiltEstimator()
        self.allocator, self.inventory, self.ladder = value.Allocator(), mm.Inventory(), ladder.Ladder()
        self.load()

    # ------------------------------------------------------------------ the cycle (README §4)
    def cycle(self):
        now, mono = utcnow(), time.monotonic()
        self.reload_settings(mono)
        view = self.read(now, mono)
        view.p, view.tilt = self.price(view)
        state = self.assess(view)
        if state is None:
            return                              # the kill switch fired: everything is cancelled
        wanted = [covered(o, view.positions.get(o.eid, 0.0))
                  for o in value.apply_floor(self.decide(view, state), view, self.s)]
        gate = risk.Gate(view, state, self.s)          # gate first, so reconcile compares with what may rest
        admitted = [o for o in (gate.admit(o) for o in by_priority(wanted)) if o is not None]
        self.refused = dict(gate.refused_usd)
        keep, cancels, new = self.reconcile(view, admitted)
        self.send(view, cancels, new)
        self.report(view, state)
        self.save()
        self.last_cycle_mono = time.monotonic()

    def reload_settings(self, mono):
        """Re-read the live settings file every RELOAD_SECONDS (README §5: a change is a file edit)."""
        if mono - self.settings_read < config.RELOAD_SECONDS:
            return
        self.settings_read = mono
        new = self.settings_file.load(self.s, self.alert)
        if new is not None:
            self.s = new
            self.client.set_budgets(new.requests_per_minute, new.writes_per_minute)

    # ------------------------------------------------------------------ read
    def read(self, now, mono):
        """One cycle's View. A full REST reconciliation every full_check_s (the feed is best-effort), otherwise
        only what the feed says changed."""
        dirty, account_changed, resync = self.feed.take() if self.feed else (set(), True, True)
        if mono - self.markets_read >= MARKETS_RELOAD_S or (resync and self.feed):
            self.load_markets(mono)             # (the feed flags a resync when a market settles)
        full = resync or not (self.feed and self.feed.healthy()) or mono - self.last_full >= self.s.full_check_s
        if full or account_changed:
            self.read_account(full)
        if full:
            self.last_full = mono
            self.tops = self.client.tops(list(self.markets))
        self.read_books(dirty, mono)
        live_books = {e: b for e, b in self.books.items() if mono - b.at < BOOK_STALE_S and e in self.markets}
        return View(now=now, mono=mono, markets=self.tradable(now), books=live_books,
                    positions=dict(self.positions), resting=self.resting_by_eid(now), account=self.account)

    def load_markets(self, mono):
        self.markets = {m.eid: m for m in self.client.markets()}
        self.markets_read = mono
        log.info("tracking %d markets in %d races", len(self.markets), len({m.race for m in self.markets.values()}))

    def tradable(self, now):
        """Markets still open for at least stop_minutes_before_close; the rest get no orders (and lose theirs)."""
        cutoff = now + timedelta(minutes=self.s.stop_minutes_before_close)
        return {e: m for e, m in self.markets.items() if m.close is None or m.close > cutoff}

    def read_account(self, full):
        """Positions, the account and new fills; on a full check also our open orders (live only)."""
        self.positions = self.client.positions()
        self.marks = dict(self.client.marks)
        self.note_fills(self.client.fills(self.last_fill_id))   # before the list, which already shows them
        if full:                                # before account(): free cash is net of these orders' locks
            self.account_orders = defaultdict(list)
            for o in self.client.open_orders():
                self.account_orders[o.eid].append(o)
            if self.live:
                self.orders = {o.oid: replace(o, tag=self.tags.get(o.oid, ""))
                               for orders in self.account_orders.values() for o in orders}
        self.account = self.client.account()

    def read_books(self, dirty, mono):
        """Books the feed flagged, then books whose bulk top moved or that are getting old, within the budget."""
        wanted = [e for e in dirty if e in self.markets]
        for e in self.markets:
            b = self.books.get(e)
            if b is None or mono - b.at >= BOOK_REFRESH_S or self.top_moved(e, b):
                wanted.append(e)
        held = {e for e, q in self.positions.items() if q}
        wanted = sorted(dict.fromkeys(wanted), key=lambda e: (e not in held, e not in dirty))
        for e in wanted:
            if self.client.requests_left() <= REQUEST_SPARE:
                break
            try:
                self.books[e] = self.client.book(e, self.mine(e))
            except ApiError as err:
                log.warning("book %s: %s", self.markets[e].label, err)

    def top_moved(self, eid, book):
        """True if the bulk top (ours included) no longer matches the cached book plus our own orders."""
        top = self.tops.get(eid)
        if top is None:
            return False
        mine = self.mine(eid)
        bids = [p for p, _ in book.bids[:1]] + [o.price for o in mine if o.is_bid]
        asks = [p for p, _ in book.asks[:1]] + [o.price for o in mine if not o.is_bid]
        return top != (max(bids, default=None), min(asks, default=None))

    def mine(self, eid):
        """The account's real resting orders on one market, stripped from its book. Live they are ours; in a
        dry run (the shadow) they are the live bot's, and still not other traders' liquidity."""
        if self.live:
            return [o for o in self.orders.values() if o.eid == eid]
        return self.account_orders.get(eid, [])

    def resting_by_eid(self, now):
        out = defaultdict(list)
        for oid, o in list(self.orders.items()):
            if o.expires is not None and o.expires <= now:
                del self.orders[oid]            # expired on the exchange by itself
            else:
                out[o.eid].append(o)
        return dict(out)

    # ------------------------------------------------------------------ price and risk
    def price(self, view):
        """Fair values from Polymarket (README §4.1), less markets paused by the jump guard; and the tilt."""
        p = pricing.fair_values(view.markets, self.refs.get(), self.refs.spreads(), view.books, self.s)
        for e, x in p.items():
            if e in self.last_p and abs(x - self.last_p[e]) >= self.s.jump_threshold:
                log.warning("JUMP %s %.3f -> %.3f: paused %.0f s", view.markets[e].label, self.last_p[e], x,
                            self.s.jump_cooldown_s)
                self.paused[e] = view.mono + self.s.jump_cooldown_s
        self.last_p.update(p)
        paused = {e for e, until in self.paused.items() if until > view.mono}
        p = {e: x for e, x in p.items() if e not in paused}
        raw = pricing.liquid_refs(view.markets, self.refs.get(), self.refs.spreads(), view.books, self.s)
        return p, self.tilt.update(view.markets, view.books, raw, view.mono, self.s, paused=paused)

    def assess(self, view):
        """The RiskState; None after the kill switch fired (README §4.4)."""
        self.kill_hits = self.kill_hits + 1 if risk.kill_switch_hit(view.account, self.s) else 0
        if self.kill_hits >= KILL_READINGS:
            self.kill(view.account)
            return None
        was_paused = self.risk.adds_paused if self.risk else False
        state = risk.assess(view, self.s, self.reduce_only, was_paused)
        if state.reduce_only != self.reduce_only:
            log.warning("%s reduce-only: correlated %.0f, worst case %.0f, account %s",
                        "ENTERING" if state.reduce_only else "leaving", state.correlated, state.worst_case,
                        view.account.value)
        self.reduce_only, self.risk = state.reduce_only, state
        return state

    def kill(self, account):
        msg = f"KILL SWITCH: account {account.value} is {self.s.max_drawdown:.0%} below {account.start}"
        log.critical(msg)
        self.alert(msg)
        self.cancel_everything()
        with open(os.path.join(self.env.run_dir, KILL_FILE), "w") as f:
            f.write(f"{utcnow().isoformat()} {msg}\n")
        self.running, self.killed = False, True

    # ------------------------------------------------------------------ decide
    def decide(self, view, state):
        """Every running strategy's wanted orders, in the order README §4 gives: allocator, ladder, quotes."""
        s, wanted = self.s, []
        for name in config.strategies(s):
            if name == "alloc":
                wanted += self.allocator.step(view, state, self.demand_usd(), s)
            elif name == "ladder":
                wanted += self.ladder.plan(view, state, s)
            elif name == "value_quotes":
                wanted += value.tail_quotes(view, state, s)
            elif name == "mm":
                wanted += mm.quotes(view, state, s, self.inventory)
        return wanted

    def demand_usd(self):
        """Cash market making and the ladder wanted last cycle and could not get: all the refill may raise."""
        return sum(self.refused.get(tag, 0.0) for tag in ("mm", "ladder"))

    # ------------------------------------------------------------------ reconcile
    def reconcile(self, view, wanted):
        """(kept, to cancel, to place): a resting order is kept if a wanted order matches it; every other
        resting order is cancelled; wanted orders that cross one of our own remaining orders are dropped."""
        keep, cancels, new = [], [], []
        by_eid = defaultdict(list)
        for o in wanted:
            by_eid[o.eid].append(o)
        for e in sorted(set(by_eid) | set(view.resting)):
            k, c, n = self.reconcile_market(view, view.resting.get(e, []), by_eid.get(e, []))
            keep, cancels, new = keep + k, cancels + c, new + n
        return keep, cancels, by_priority(new)

    def reconcile_market(self, view, resting, wanted):
        horizon = view.now + timedelta(seconds=EXPIRY_MARGIN_S)
        keep, new, left = [], [], list(resting)
        for w in wanted:
            match = next((r for r in left if not w.ioc and r.is_bid == w.is_bid and r.tag == w.tag
                          and abs(r.price - w.price) < PRICE_TOL
                          and SIZE_KEEP_FRAC * w.size <= r.size <= w.size + SIZE_TOL
                          and (r.expires is None or r.expires > horizon)), None)
            if match is not None:
                left.remove(match)
                keep.append(match)
            else:
                new.append(w)
        cancels = left
        new = [w for w in new if not self.crosses_own(w, keep)]
        cancels += [r for w in new if w.ioc for r in keep if self.crosses_own(w, [r])]
        keep = [r for r in keep if r not in cancels]
        return keep, cancels, new

    @staticmethod
    def crosses_own(order, ours):
        """True if the order would trade against one of ours (never trade with ourselves, README §8)."""
        for r in ours:
            if r.is_bid != order.is_bid and (order.price >= r.price - PRICE_TOL if order.is_bid
                                             else order.price <= r.price + PRICE_TOL):
                return True
        return False

    # ------------------------------------------------------------------ send
    def send(self, view, cancels, places):
        """Cancels first (they are the safety side), then the placements in priority order, all within the
        write budget. What does not fit is wanted again next cycle: deferred, never dropped."""
        self.deferred = 0
        ladder_cancels = int(self.s.harvest_writes_frac * self.client.writes_left())
        for o in cancels:
            if o.tag == "ladder":
                if ladder_cancels <= 0:
                    self.deferred += 1
                    continue
                ladder_cancels -= 1
            self.cancel_order(o)
        resting = defaultdict(list)                    # what is really still resting after the cancels
        for o in self.orders.values():
            resting[o.eid].append(o)
        places = [p for p in places if not self.crosses_own(p, resting[p.eid])]   # a cancel failed: wait for it
        for k in range(0, len(places), BATCH_MAX):
            if not self.place_batch(view, places[k:k + BATCH_MAX]):
                self.deferred += len(places) - k
                break
        if self.deferred:
            log.info("write budget: %d writes deferred to the next cycle", self.deferred)

    def cancel_order(self, o):
        """Cancel one order; it stays in self.orders (and blocks crossing placements) unless it is gone."""
        if not self.live:
            log.info("WOULD cancel %s %s %.3f x%d [%s]", self.label(o.eid), "bid" if o.is_bid else "ask",
                     o.price, o.size, o.tag)
            self.orders.pop(o.oid, None)
            return True
        if self.client.writes_left() < 1:
            self.deferred += 1
            return False
        try:
            gone = self.client.cancel(o.oid)
        except ApiError as err:
            log.warning("cancel %s: %s", o.oid, err)
            return False
        if gone:
            self.orders.pop(o.oid, None)
        return gone

    def place_batch(self, view, batch):
        """Send one batch (or log it in a dry run). False if the write budget is spent."""
        expires = view.now + timedelta(seconds=self.s.order_ttl_s)
        batch = [replace(o, expires=None if o.ioc else expires) for o in batch]
        if not self.live:
            for o in batch:
                log.info("WOULD %s %s %s %.3f x%d [%s]", "take" if o.ioc else "place", self.label(o.eid),
                         "bid" if o.is_bid else "ask", o.price, o.size, o.tag)
                if not o.ioc:
                    self.sim_ids += 1
                    self.orders[f"dry-{self.sim_ids}"] = replace(o, oid=f"dry-{self.sim_ids}")
            return True
        if self.client.writes_left() < 1:
            return False
        try:
            results = self.client.place(batch, self.positions)
        except ApiError as err:
            log.warning("batch of %d: %s", len(batch), err)
            self.last_full = -1e9               # outcome unknown: re-read our orders next cycle
            return True
        for r in results:
            self.note_placed(r)
        return True

    def note_placed(self, r):
        if r.unknown:
            self.last_full = -1e9               # it may be resting: read our orders before placing it again
        if r.error:
            log.warning("refused %s %s %.3f x%d [%s]: %s", self.label(r.order.eid),
                        "bid" if r.order.is_bid else "ask", r.order.price, r.order.size, r.order.tag, r.error)
            return
        log.info("%s %s %s %.3f x%d [%s] traded %d", "TAKE" if r.order.ioc else "PLACE", self.label(r.order.eid),
                 "bid" if r.order.is_bid else "ask", r.order.price, r.order.size, r.order.tag, r.traded)
        if r.oid is not None:
            self.tags[r.oid] = r.order.tag
            if not r.order.ioc and r.traded < r.order.size:
                self.orders[r.oid] = replace(r.order, oid=r.oid, size=r.order.size - r.traded)
        if r.traded:
            self.last_full = -1e9               # positions changed: read them next cycle

    def cancel_everything(self):
        """Cancel every order we have (the kill switch, a stop, the watchdog)."""
        if self.live:
            try:
                self.client.cancel_all()
            except ApiError as err:
                log.error("cancel-all failed: %s", err)
        self.orders = {}

    def adopt(self, orders):
        """A handover: the previous process left these orders resting; keep managing them."""
        for o in orders:
            self.orders[o.oid] = replace(o, tag=self.tags.get(o.oid, o.tag))

    # ------------------------------------------------------------------ fills and P&L
    def note_fills(self, fills):
        """Attribute new fills to the strategy whose order filled; realised P&L at average cost."""
        fills = [replace(f, tag=self.tags.get(f.oid, "")) for f in fills]
        for f in fills:
            self.realised += self.realise(f)
            log.info("FILL %s %s %.3f x%d [%s]", self.label(f.eid), "bought" if f.is_bid else "sold", f.price,
                     f.size, f.tag or "?")
            o = self.orders.get(f.oid)
            if o is not None:
                left = o.size - f.size
                self.orders[f.oid] = replace(o, size=left)
                if left <= 0:
                    del self.orders[f.oid]
        if fills:
            self.last_fill_id = fills[-1].fid
            self.inventory.note_fills(fills)
            self.allocator.note_fills(fills)
            self.ladder.note_fills(fills)

    def realise(self, f):
        """P&L a fill realises against the position's average cost (0 when it adds to the position)."""
        before = self.positions_before(f)
        signed = f.size if f.is_bid else -f.size
        cost = self.cost.get(f.eid, f.price)
        if before == 0 or (before > 0) == (signed > 0):
            total = before + signed
            self.cost[f.eid] = (cost * abs(before) + f.price * abs(signed)) / abs(total) if total else f.price
            return 0.0
        closed = min(abs(signed), abs(before))
        pnl = closed * ((f.price - cost) if before > 0 else (cost - f.price))
        if abs(signed) > abs(before):
            self.cost[f.eid] = f.price          # flipped: the rest opens at this fill's price
        return pnl

    def positions_before(self, f):
        """The position before this fill: positions are read after the fills they include, so undo it."""
        signed = f.size if f.is_bid else -f.size
        return self.positions.get(f.eid, 0.0) - signed

    # ------------------------------------------------------------------ report
    def ev_outcome(self, view):
        """(expected value at the result, held markets without a fair value): the account with every priced
        position revalued from its mark to p (finding 3.2)."""
        ev, unpriced = view.account.value, 0
        if ev is None:
            return None, 0
        for e, q in view.positions.items():
            if not q:
                continue
            if e in view.p and e in self.marks:
                ev += q * (view.p[e] - self.marks[e])
            else:
                unpriced += 1
        return round(ev, 2), unpriced

    def report(self, view, state):
        """status.json: the fields the owner reads keep their old names."""
        ev, unpriced = self.ev_outcome(view)
        status = {
            "updated": view.now.isoformat(), "mode": "live" if self.live else "dry-run",
            "account_value": view.account.value, "cash": view.account.cash,
            "ev_outcome": ev, "ev_outcome_unpriced": unpriced, "realised_pnl": round(self.realised, 2),
            "reduce_only": state.reduce_only, "worst_case_loss": round(state.worst_case, 2),
            "settlement_risk": round(state.correlated, 2), "bloc_delta": round(state.bloc_delta, 2),
            "orders_resting": len(self.orders), "markets_priced": len(view.p), "markets_tracked": len(view.markets),
            "tilt_s": view.tilt, "writes_deferred": self.deferred,
            "rate_limited_total": self.client.rate_limited, "realtime": self.feed_state(),
            "alloc": self.allocator.status(), "mm_funding": self.mm_funding(view),
            "harvest": self.ladder.status(), "state_caps": self.state_caps(state),
            "positions": {self.label(e): q for e, q in view.positions.items() if q},
            "seconds_since_cycle": 0.0,
        }
        write_json(os.path.join(self.env.run_dir, STATUS_FILE), status)
        if view.mono - self.last_full < 1.0:
            log.info(self.summary_line(status))

    def mm_funding(self, view):
        """The old mm_funding field: the reserve, what market making holds, and what the refill may raise."""
        locked = sum(o.usd for o in self.orders.values() if o.tag in ("mm", "ladder"))
        return {**self.allocator.funding(), **self.inventory.status(), "reserve": self.s.mm_reserve_usd,
                "resting_collateral": round(locked, 2), "demand_usd": round(self.demand_usd(), 2)}

    def state_caps(self, state):
        top = sorted(state.state_usd.items(), key=lambda kv: -kv[1])[:6]
        return {"cap": self.s.state_max_usd, "top": {k: round(v) for k, v in top}}

    def feed_state(self):
        if not self.feed:
            return "off"
        return "connected" if self.feed.healthy() else "reconnecting"

    def summary_line(self, st):
        """The one line the journal and the phone summary show."""
        return (f"account {st['account_value']} | EV outcome {st['ev_outcome']} | realised {st['realised_pnl']} | "
                f"worst case {st['worst_case_loss']:.0f} (corr {st['settlement_risk']:.0f})"
                f"{' REDUCE-ONLY' if st['reduce_only'] else ''} | priced {st['markets_priced']}/"
                f"{st['markets_tracked']} | resting {st['orders_resting']} | tilt {st['tilt_s']}")

    def label(self, eid):
        m = self.markets.get(eid)
        return m.label if m else eid

    # ------------------------------------------------------------------ saved state
    def save(self):
        """What must survive a restart: order tags, the strategies' memories, the P&L book."""
        tags = {oid: t for oid, t in self.tags.items() if oid in self.orders or len(self.tags) < TAGS_KEEP}
        write_json(os.path.join(self.env.run_dir, STATE_FILE), {
            "tags": tags, "last_fill_id": self.last_fill_id, "cost": self.cost, "realised": self.realised,
            "allocator": self.allocator.to_dict(), "inventory": self.inventory.to_dict(),
            "ladder": self.ladder.to_dict(), "tilt": self.tilt.to_dict()})
        self.tags = tags

    def load(self):
        try:
            with open(os.path.join(self.env.run_dir, STATE_FILE)) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.tags, self.last_fill_id = d.get("tags", {}), d.get("last_fill_id")
        self.cost, self.realised = d.get("cost", {}), d.get("realised", 0.0)
        self.allocator = value.Allocator(d.get("allocator"))
        self.inventory = mm.Inventory(d.get("inventory"))
        self.ladder = ladder.Ladder(d.get("ladder"))
        self.tilt = pricing.TiltEstimator.from_dict(d.get("tilt") or {})


KILL_READINGS = 2            # two bad account readings in a row, so one glitched read cannot stop the bot
MARKETS_RELOAD_S = 3600.0     # new or settled markets are picked up hourly
STATUS_FILE, STATE_FILE, KILL_FILE = "status.json", "state.json", "kill_switch.tripped"
TAGS_KEEP = 5000              # order tags remembered for fill attribution, beyond the resting ones


def write_json(path, data):
    """Write atomically, so a reader (the owner's `cat status.json`) never sees half a file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)
