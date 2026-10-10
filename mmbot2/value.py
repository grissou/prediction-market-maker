"""
The value book: the floor every reducing order respects, the value quotes in the tails, and the allocator that
moves cash from the holdings with the least edge left to the levels on the book with the most.

OWNS     the value floor (apply_floor); the tail quotes behind the hurdle (tail_quotes); the Allocator: once an
         interval, swaps (sell the lowest edge-held, buy the highest-edge level, both IOC at the touch, the buy only
         after a cash read shows the sale's money), buys from spare cash above mm_reserve_usd, and the refill (sales
         with no buy, sized to what market making and the ladder were refused for cash last cycle).
NEVER    sells below the floor (a swap sale below p - alloc_swap_sell_margin), flips a position, sells a pinned
         label, trades the party-control races, or sells for cash no buyer has asked for.
ORIGIN   The 3.7k morning (selling below value to raise cash) gave the floor. RETURNS_ATTRIBUTION.md gave the rest:
         the allocator's buys earned ~11% per unit of cash, the refill sold 110k of value for 43k of buying; so
         here a sale happens only when a buy can use the cash. Old code: mmbot/value.py (alloc_plan, alloc_tick,
         alloc_buy, alloc_sell), mmbot/mm.py (swap_floor_ok, mm_floor_ok), mmbot/quoting.py (value_side_prices,
         the hurdle in compute_quote). Left behind: NO+NO set unwinds (the 4 Oct book holds one set, worth 1.0),
         the set ladder, the momentum sleeve.
OPEN     A swap in flight is not saved: after a restart its cash waits for the next run. The bot does not say if an
         IOC was sent or deferred, so each is offered once (a deferred one is lost, never sent twice).
"""
import math
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from mmbot2 import pricing
from mmbot2.risk import CASH_MARGIN
from mmbot2.state import PMAX, PMIN, TICK, Order, adds, ceil_tick, floor_tick, held_usd

MIN_USD = 20.0            # no pair or refill sale below this many dollars (old ALLOC_MIN_USD)
LEVELS_DEEP = 3           # the allocator buys from the top 3 levels of a book (old alloc_plan)
LEVEL_TOL = 0.005         # a planned level still counts while the touch is within a tick of it (old ALLOC_LEVEL_TOL)
BOOK_FRESH_S = 120.0      # the allocator trades only on a book read this recently (the bot re-reads every 120 s)
CASH_FRESH_S = 300.0      # no allocator action without a cash read this recent (the gate's own limit)
BUY_WAIT_S = 900.0        # a sold swap's buy waits at most this long for the money (old ALLOC_BUY_WAIT)
MAX_ORDERS = 4            # allocator orders per cycle (old alloc_max_orders_per_cycle): the writes are shared
MAX_ORDER_CASH_FRAC = 0.01   # one tail quote ties up at most 1% of the account (old max_order_cash_frac)
HOUR_S, DAY_S = 3600.0, 86400.0
EPS = 1e-9


# --------------------------------------------------------------------------- the floor (README §4.3)

def floor_prices(qty, p, margin):
    """(highest bid, lowest ask) an order reducing a position of qty may take: a long is never offered below
    p - margin, a short never bought back above p + margin (selling below p gives value away at the result).
    None = no limit on that side. A bid limit below the grid is returned as is (the order cannot exist)."""
    if qty >= 1:
        return None, ceil_tick(p - margin)
    if qty <= -1:
        return round(min(PMAX, math.floor(round((p + margin) / TICK, 6)) * TICK), 3), None
    return None, None


def apply_floor(orders, view, s):
    """The orders with every reducing one moved back to the floor. Only ever moves a price away from the other
    side, so it never makes an order trade. Swap sales (tag alloc) have their own, wider margin (release 14.2)."""
    out = []
    for o in orders:
        p, qty = view.p.get(o.eid), view.positions.get(o.eid, 0.0)
        if p is None or adds(o, qty):
            out.append(o)
            continue
        margin = s.alloc_swap_sell_margin if o.tag == "alloc" else s.value_sell_margin
        max_bid, min_ask = floor_prices(qty, p, margin)
        if o.is_bid and max_bid is not None and o.price > max_bid + EPS:
            if max_bid < PMIN - EPS:
                continue
            o = replace(o, price=max_bid)
        elif not o.is_bid and min_ask is not None and o.price < min_ask - EPS:
            o = replace(o, price=min_ask)
        out.append(o)
    return out


def floor_ok(long, px, p, margin):
    """True if selling a long at px (buying back a short at px) is at or beyond the floor."""
    return px >= p - margin - EPS if long else px <= p + margin + EPS


# --------------------------------------------------------------------------- tail quotes (README §4.3)

def hurdle_price(p, is_bid, h):
    """The worst price an adding quote may rest at: a YES bid b earns (p - b) / b >= h iff b <= p / (1 + h);
    an ask is the same rule in NO terms. In the tails this puts favourite bids and longshot asks near the book
    and the other side far out of reach, which is the point (the book is tilted)."""
    return p / (1 + h) if is_bid else 1 - (1 - p) / (1 + h)


def guard_blocks(p, book, s):
    """(no_bid, no_ask): Polymarket far above the book says don't sell here (they probably know); far below,
    don't buy. Compared with the book's own depth-checked price."""
    base = pricing.book_price(book)
    if base is None:
        return False, False
    return base - p > s.ref_guard_gap, p - base > s.ref_guard_gap


def tail_price(p, is_bid, book, s):
    """One tick better than the best other order, never beyond the hurdle price nor within mm_min_edge of p (the
    old quote engine's band), never crossing; None if no such price is on the grid."""
    h = s.value_quote_hurdle
    if is_bid:
        cap = min(hurdle_price(p, True, h), p - s.mm_min_edge)
        if cap < PMIN - EPS:
            return None
        price = floor_tick(cap)
        if book is not None and book.best_bid is not None:
            price = min(price, floor_tick(book.best_bid + TICK))
        ok = book is None or book.best_ask is None or price < book.best_ask - EPS
    else:
        flo = max(hurdle_price(p, False, h), p + s.mm_min_edge)
        if flo > PMAX + EPS:
            return None
        price = ceil_tick(flo)
        if book is not None and book.best_ask is not None:
            price = max(price, ceil_tick(book.best_ask - TICK))
        ok = book is None or book.best_bid is None or price > book.best_bid + EPS
    return price if ok else None


def tail_size(p, price, is_bid, qty, bankroll, s):
    """Shares: what quarter Kelly still allows beyond what is held on this side, at most MAX_ORDER_CASH_FRAC of
    the account in cash. The old activity-based quote size is not carried over (it never bound in the tails)."""
    held = max(0.0, qty if is_bid else -qty)
    unit = price if is_bid else 1 - price
    room = pricing.kelly_shares(p, price, is_bid, bankroll, s) - held
    return int(max(0.0, min(room, MAX_ORDER_CASH_FRAC * bankroll / max(unit, TICK))))


def tail_quotes(view, risk, s):
    """Resting value quotes (tag value) outside [band_low, band_high], on the side that adds to the position only:
    reducing there is the allocator's and the ladder's job. Nothing while risk says adds must wait."""
    if risk.reduce_only or risk.adds_paused:
        return []
    out, bankroll = [], view.account.value or view.account.start
    for eid in sorted(view.p):
        p = view.p[eid]
        if eid not in view.markets or s.band_low <= p <= s.band_high:
            continue
        qty, book = view.positions.get(eid, 0.0), view.books.get(eid)
        if book is None:
            continue                                   # no fresh book: a price could cross other traders unseen
        no_bid, no_ask = guard_blocks(p, book, s)
        # tail_high / tail_low: near 1 a YES bid (near 0 a YES ask) risks ~1 to earn ~1c
        for is_bid, allowed in ((True, qty > -1 and p <= s.tail_high and not no_bid),
                                (False, qty < 1 and p >= s.tail_low and not no_ask)):
            price = tail_price(p, is_bid, book, s) if allowed else None
            size = tail_size(p, price, is_bid, qty, bankroll, s) if price is not None else 0
            if size >= 1:
                out.append(Order(eid, is_bid, price, size, "value"))
    return out


# --------------------------------------------------------------------------- the allocator's candidates

@dataclass
class Holding:
    eid: str | None           # the market sold, or None for spare cash
    long: bool                # True: sell YES at the bid; False: buy back a short at the ask
    px: float                 # the touch it sells into
    edge: float               # edge left in it (pricing.edge_held); 0 for cash
    p: float                  # fair value when planned
    unit: float               # cash one share frees: px for a long, 1 - px for a short
    avail: float              # dollars it can still free at that touch


@dataclass
class Level:
    eid: str
    short: bool               # True: sell YES short at a bid; False: buy YES at an ask
    px: float
    edge: float               # edge of buying it (pricing.edge_buy / edge_short)
    unit: float               # cash one share costs: px, or 1 - px for a short
    avail: float              # dollars of depth at this level


@dataclass
class Pair:
    tag: str                  # "alloc" (a swap or a spare-cash buy) or "refill" (a sale with no buy)
    sell: Holding
    qty: int                  # shares to sell (0 for spare cash)
    buy: Level | None         # None for a refill
    buy_qty: int              # shares to buy as planned
    usd: float                # cash the pair moves
    planned_at: float         # monotonic
    status: str = "pending"   # pending -> sold -> done (or dropped / gone / expired)
    sent_at: float = -1e18    # monotonic: the sale was handed over; the buy needs a cash read after it
    proceeds: float = 0.0     # cash the sale's fills raised (spare cash: the cash itself)


def trade_book(view, eid):
    """The market's book if the allocator may trade it now: priced, fresh, not a party-control race (the old
    allocator never touched them), else None."""
    m, book = view.markets.get(eid), view.books.get(eid)
    if m is None or eid not in view.p or book is None or view.mono - book.at > BOOK_FRESH_S:
        return None
    return None if m.race in pricing.HEADLINE_RACES else book


def pinned(s):
    return {x.strip() for x in s.alloc_pin.split(",") if x.strip()}


def holdings(view, s):
    """Every holding the allocator may sell, lowest edge left first, sized to the depth at the touch."""
    out, pins = [], pinned(s)
    for eid, qty in sorted(view.positions.items()):
        book = trade_book(view, eid)
        if abs(qty) < 1 or book is None or view.markets[eid].label in pins:
            continue
        p = view.p[eid]
        edge = pricing.edge_held(qty, p, book)
        if edge is None:
            continue
        px, depth = (book.bids if qty > 0 else book.asks)[0]
        unit = px if qty > 0 else 1 - px
        out.append(Holding(eid, qty > 0, px, edge, p, unit, int(min(abs(qty), depth) + EPS) * unit))
    return sorted(out, key=lambda h: (h.edge, h.eid))


def prefer_short_blocked(view, s):
    """{eid}: two-leg race legs whose YES is not bought because the race's best bids sum above 1, so shorting the
    other leg at its bid gives the same exposure for less cash (old alloc_prefer_short_legs)."""
    races, out = {}, set()
    for e, m in view.markets.items():
        races.setdefault(m.race, []).append(e)
    for legs in races.values():
        books = [trade_book(view, e) for e in legs]
        if len(legs) != 2 or any(b is None or b.best_bid is None for b in books):
            continue
        if books[0].best_bid + books[1].best_bid <= 1 + EPS:
            continue
        for i in (0, 1):
            other, bid = legs[1 - i], books[1 - i].best_bid
            q, p = view.positions.get(other, 0.0), view.p[other]
            if (q <= 0 and pricing.edge_short(p, bid) >= s.alloc_min_edge_buy - EPS
                    and s.market_max_usd - held_usd(q, p) >= MIN_USD):
                out.add(legs[i])
    return out


def levels(view, s):
    """Every level worth buying (edge >= alloc_min_edge_buy), highest edge first. Never against a position: YES is
    bought only where we are not short, shorted only where we are not long (a position is never flipped)."""
    no_yes = prefer_short_blocked(view, s) if s.alloc_prefer_short else set()
    out = []
    for eid in sorted(view.markets):
        book = trade_book(view, eid)
        if book is None:
            continue
        p, qty = view.p[eid], view.positions.get(eid, 0.0)
        if qty >= 0 and eid not in no_yes:
            out += [Level(eid, False, px, pricing.edge_buy(p, px), px, n * px) for px, n in book.asks[:LEVELS_DEEP]]
        if qty <= 0:
            out += [Level(eid, True, px, pricing.edge_short(p, px), 1 - px, n * (1 - px))
                    for px, n in book.bids[:LEVELS_DEEP]]
    return sorted([o for o in out if o.edge >= s.alloc_min_edge_buy - EPS], key=lambda o: (-o.edge, o.eid, o.px))


def buy_room_usd(view, risk, o, used, s):
    """Cash the per-market and per-state caps leave for buying level o, counting what this plan already buys
    (used: eid or state -> collateral at p). Checked here so no sale is made for a buy the gate would refuse."""
    p, qty = view.p[o.eid], view.positions.get(o.eid, 0.0)
    per = max(1 - p if o.short else p, TICK)              # collateral at the result per share, as the gate counts
    room = s.market_max_usd - held_usd(qty, p) - used.get(o.eid, 0.0)
    state = view.markets[o.eid].state
    if state is not None:
        room = min(room, s.state_max_usd - risk.state_usd.get(state, 0.0) - used.get(state, 0.0))
    return max(0.0, room) / per * o.unit


# --------------------------------------------------------------------------- the allocator's plans

def plan_refill(held, need, left, s):
    """[(holding, shares)]: the lowest-edge holdings sold at the touch, never below the ordinary floor, until need
    (the cash the buyers were refused, less free cash) or the turnover left runs out."""
    out = []
    for h in held:
        usd = min(h.avail, need, left)
        if usd < MIN_USD or not floor_ok(h.long, h.px, h.p, s.value_sell_margin):
            continue
        n = int(usd / h.unit + EPS)
        if n >= 1:
            out.append((h, n))
            h.avail, need, left = h.avail - n * h.unit, need - n * h.unit, left - n * h.unit
    return out


def swap_sellers(held, spare, s):
    """What a swap may sell, lowest edge first: holdings with little edge left whose touch is within the swap
    margin, and the spare cash as a holding of edge 0."""
    out = [h for h in held if h.avail >= MIN_USD and h.edge <= s.alloc_max_edge_sell + EPS
           and floor_ok(h.long, h.px, h.p, s.alloc_swap_sell_margin)]
    if spare >= MIN_USD:
        out.append(Holding(None, True, 1.0, 0.0, 1.0, 1.0, spare))
    return sorted(out, key=lambda h: (h.edge, h.eid or ""))


def plan_swaps(sellers, lvls, view, risk, left, s):
    """[(holding, shares sold, level, shares bought, usd)]: the lowest-edge seller paired with the highest-edge
    level while the gain is at least alloc_swap_min_gain, within the caps and the turnover left."""
    out, used, si, bi = [], {}, 0, 0
    while si < len(sellers) and bi < len(lvls) and left >= MIN_USD:
        h, o = sellers[si], lvls[bi]
        if o.edge - h.edge < s.alloc_swap_min_gain - EPS:
            break                             # sellers only get richer and levels poorer from here
        sold, bought = {p[0].eid for p in out} | {h.eid}, {p[2].eid for p in out}
        if o.eid in sold or h.eid in bought:  # never buy and sell one market in one run
            si, bi = (si + 1, bi) if h.eid in bought else (si, bi + 1)
            continue
        room = buy_room_usd(view, risk, o, used, s)
        x = min(h.avail, o.avail, room, left)
        nb = int(x / o.unit + EPS)
        ns = 0 if h.eid is None else int(nb * o.unit / h.unit + EPS)
        if x < MIN_USD or nb < 1 or (h.eid is not None and ns < 1):
            si, bi = (si, bi + 1) if min(room, o.avail) < MIN_USD else (si + 1, bi)
            continue
        x = nb * o.unit
        out.append((h, ns, o, nb, x))
        p = view.p[o.eid]
        state, per = view.markets[o.eid].state, max(1 - p if o.short else p, TICK) / o.unit
        used[o.eid] = used.get(o.eid, 0.0) + x * per
        if state is not None:
            used[state] = used.get(state, 0.0) + x * per
        h.avail, o.avail, left = h.avail - x, o.avail - x, left - x
        si, bi = si + (h.avail < MIN_USD), bi + (o.avail < MIN_USD)
    return out


def level_now(view, lv, s):
    """(touch, edge, depth) of a planned level on the book now, or None if it has moved away by more than a tick
    or no longer pays alloc_min_edge_buy."""
    book, p = view.books[lv.eid], view.p[lv.eid]
    side = book.bids if lv.short else book.asks
    if not side:
        return None
    px, depth = side[0]
    if (px < lv.px - LEVEL_TOL - EPS) if lv.short else (px > lv.px + LEVEL_TOL + EPS):
        return None
    edge = pricing.edge_short(p, px) if lv.short else pricing.edge_buy(p, px)
    return (px, edge, depth) if edge >= s.alloc_min_edge_buy - EPS else None


def iso(wall):
    return None if wall is None else datetime.fromtimestamp(wall, timezone.utc).isoformat()


# --------------------------------------------------------------------------- the allocator

class Allocator:
    """Plans once an interval, then carries the plan out over the next cycles: sales first, each buy only after
    a cash read taken after its sale shows the money. Its orders are IOC: they trade at the touch or not at all."""

    def __init__(self, saved=None):
        d = saved if isinstance(saved, dict) else {}
        self.pairs = []                                  # the plan in flight
        self.last_run = d.get("last_run_wall")           # wall seconds of the last swap run (survives a restart)
        self.last_refill = d.get("last_refill_wall")     # wall seconds of the last refill plan
        self.flows = deque(tuple(x) for x in d.get("flows", []))      # (wall, usd) rotated: the hourly cap
        self.refills = deque(tuple(x) for x in d.get("refills", []))  # (wall, usd) refill sales filled: 24 h
        self.totals = {k: float(d.get("totals", {}).get(k, 0.0)) for k in ("runs_total", "sold_usd", "bought_usd")}
        self.state, self.info, self.sells_stopped = "idle", {}, False
        self.demand, self.raised, self.refill_runs = 0.0, 0.0, int(d.get("refill_runs", 0))

    def turnover(self, wall):
        """Dollars rotated in the last hour (sales and spare-cash buys)."""
        while self.flows and self.flows[0][0] < wall - HOUR_S:
            self.flows.popleft()
        return sum(u for _, u in self.flows)

    def step(self, view, risk, demand_usd, s):
        """This cycle's allocator orders (IOC, tags alloc and refill): a new plan when one is due, then the buys
        whose money has arrived, then the sales. Nothing at all without a fresh cash read."""
        acct, wall = view.account, view.now.timestamp()
        if acct.cash is None or view.mono - acct.read_at > CASH_FRESH_S:
            self.state = "waiting (no fresh cash read)"
            return []
        if not self.pairs:
            self.plan(view, risk, demand_usd, s, wall)
        cash, orders = acct.cash - CASH_MARGIN, []
        for pr in [p for p in self.pairs if p.status == "sold"] + [p for p in self.pairs if p.status == "pending"]:
            if len(orders) >= MAX_ORDERS:
                break
            was = pr.status
            o = self.buy_order(pr, view, risk, s, cash) if was == "sold" else self.sale_order(pr, view, s)
            if o is not None:
                orders.append(o)
                cash -= o.size * (o.price if o.is_bid else 1 - o.price) if was == "sold" else 0.0
                if was == "pending" or pr.sell.eid is None:      # what counts against the hourly turnover
                    self.flows.append((wall, o.size * (o.price if o.is_bid else 1 - o.price)))
        self.pairs = [p for p in self.pairs if p.status in ("pending", "sold")]
        self.state = "running" if self.pairs else "idle"
        return orders

    def plan(self, view, risk, demand_usd, s, wall):
        """Start a run if one is due: the refill (at most once an interval, only for demand free cash cannot meet),
        then the swaps and spare-cash buys (once an interval)."""
        need = demand_usd - max(0.0, view.account.cash)
        refill_due = need >= MIN_USD and (self.last_refill is None or wall - self.last_refill >= s.alloc_interval_s)
        swap_due = self.last_run is None or wall - self.last_run >= s.alloc_interval_s
        if not (refill_due or swap_due):
            return
        left, held, mono = s.alloc_max_turnover_usd - self.turnover(wall), holdings(view, s), view.mono
        refills = plan_refill(held, need, left, s) if refill_due else []
        self.pairs = [Pair("refill", h, n, None, 0, n * h.unit, mono) for h, n in refills]
        if refill_due:
            self.last_refill, self.demand, self.raised, self.refill_runs = wall, demand_usd, 0.0, self.refill_runs + 1
        swaps, why = [], "no level"
        if swap_due:
            self.last_run, self.sells_stopped = wall, False
            self.totals["runs_total"] += 1
            sold = {p.sell.eid for p in self.pairs}
            # the gate refuses value adds in reduce-only and while the reserve pauses them: no sale for such a buy
            lvls = [] if risk.reduce_only or risk.adds_paused else [o for o in levels(view, s) if o.eid not in sold]
            why = "risk" if risk.reduce_only or risk.adds_paused else why
            spare = view.account.cash - CASH_MARGIN - s.mm_reserve_usd
            swaps = plan_swaps(swap_sellers(held, spare, s), lvls, view, risk,
                               left - sum(p.usd for p in self.pairs), s)
        for h, ns, o, nb, x in swaps:
            pr = Pair("alloc", h, ns, o, nb, x, mono)
            if h.eid is None:                    # spare cash is already there: the buy may go now
                pr.status, pr.proceeds = "sold", x
            self.pairs.append(pr)
        self.info = {"pairs_planned": len(swaps), "refills_planned": len(refills), "cash_before": view.account.cash,
                     "demand_usd": round(demand_usd, 2), "blocked_by": why if swap_due and not swaps else None,
                     "ev_gain_est": round(sum(x * (o.edge - h.edge) for h, _, o, _, x in swaps), 2)}

    def sale_order(self, pr, view, s):
        """The IOC sale of a pending pair at the touch, if it still pays on a fresh book; for a swap, only while
        its buy level is still there. Sized to the planned shares, the position (never a flip) and the depth."""
        h, book = pr.sell, trade_book(view, pr.sell.eid)
        if view.mono - pr.planned_at > s.alloc_interval_s or (pr.buy is not None and self.sells_stopped):
            pr.status = "expired"
            return None
        if book is None or (pr.buy is not None and trade_book(view, pr.buy.eid) is None):
            return None                          # wait for fresh books
        qty, p = view.positions.get(h.eid, 0.0), view.p[h.eid]
        side = book.bids if h.long else book.asks
        still_held = qty >= 1 if h.long else qty <= -1
        if not still_held or not side:
            pr.status = "dropped"
            return None
        px, depth = side[0]
        margin = s.value_sell_margin if pr.buy is None else s.alloc_swap_sell_margin
        lv, edge = (None if pr.buy is None else level_now(view, pr.buy, s)), pricing.edge_held(qty, p, book)
        pays = pr.buy is None or (lv is not None and edge <= s.alloc_max_edge_sell + EPS
                                  and lv[1] - edge >= s.alloc_swap_min_gain - EPS)
        n = int(min(pr.qty, abs(qty), depth) + EPS)
        if not pays or not floor_ok(h.long, px, p, margin) or n < 1:
            pr.status = "gone"
            return None
        pr.status, pr.sent_at = "sold", max(view.mono, view.account.read_at)
        return Order(h.eid, not h.long, px, n, pr.tag, ioc=True)

    def buy_order(self, pr, view, risk, s, cash):
        """The IOC buy of a sold pair, once a cash read taken after the sale shows the money (a refill is done once
        sold). The level is re-checked on a fresh book; gone -> the cash stays and no more swap sales this run."""
        if pr.buy is None:
            pr.status = "done"
            return None
        if view.mono - max(pr.sent_at, pr.planned_at) > BUY_WAIT_S:
            pr.status = "expired"
            return None
        lv = pr.buy
        if (view.account.read_at <= pr.sent_at or pr.proceeds < lv.unit or risk.reduce_only or risk.adds_paused
                or trade_book(view, lv.eid) is None):
            return None                          # not yet: wait for the money, the room and a fresh book
        qty = view.positions.get(lv.eid, 0.0)
        if (qty > 0 and lv.short) or (qty < 0 and not lv.short):
            pr.status = "dropped"                # never flip a position: the cash stays
            return None
        now = level_now(view, lv, s)
        if now is None:
            pr.status, self.sells_stopped = "gone", True
            return None
        px, _, depth = now
        unit = 1 - px if lv.short else px
        n = int(min(pr.buy_qty, depth, min(pr.proceeds, cash) / unit) + EPS)
        if n < 1:
            return None                          # the cash read does not show the money (yet)
        pr.status = "done"
        return Order(lv.eid, not lv.short, px, n, "alloc", ioc=True)

    def note_fills(self, fills):
        """Credit the fills of our IOC orders: a swap's sale raises the cash its buy may spend; refill sales are
        counted for mm_funding."""
        for f in fills:
            usd = f.size * (f.price if not f.is_bid else 1 - f.price)
            if f.tag == "refill":
                self.refills.append((f.at.timestamp(), usd))
                self.raised += usd
                self.totals["sold_usd"] += usd
            elif f.tag == "alloc":
                sale = next((p for p in self.pairs if p.status == "sold" and p.sell.eid == f.eid
                             and f.is_bid != p.sell.long), None)
                if sale is not None:
                    sale.proceeds += usd
                    self.totals["sold_usd"] += usd
                else:
                    self.totals["bought_usd"] += f.size * (f.price if f.is_bid else 1 - f.price)

    def status(self):
        """status.json "alloc"."""
        return {"state": self.state, "last_run": iso(self.last_run), "last_run_wall": self.last_run,
                "pending": len(self.pairs), "turnover_hour": round(self.turnover(time.time()), 2),
                "sells_stopped": self.sells_stopped, **self.info, **{k: round(v, 2) for k, v in self.totals.items()}}

    def funding(self):
        """The refill's part of status.json "mm_funding": what it sold for the buyers and why."""
        now = time.time()
        while self.refills and self.refills[0][0] < now - DAY_S:
            self.refills.popleft()
        return {"refill_runs": self.refill_runs, "refill_last": iso(self.last_refill),
                "refill_sold_24h": round(sum(u for _, u in self.refills), 2),
                "refill_demand": round(self.demand, 2), "refill_raised": round(self.raised, 2)}

    def to_dict(self):
        """What survives a restart: the clocks, the hour's turnover, the day's refills, the totals."""
        return {"last_run_wall": self.last_run, "last_refill_wall": self.last_refill, "flows": list(self.flows),
                "refills": list(self.refills), "totals": dict(self.totals), "refill_runs": self.refill_runs}
