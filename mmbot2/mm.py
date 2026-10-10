"""
Market making: small two-sided quotes in the middle band, and the recycler that works old inventory back out.

OWNS     the quotes in [band_low, band_high] (a tick inside the best other trader, never within mm_min_edge of p,
         never crossing, quarter Kelly, leaning on size), the ref guard, the MM lots (Inventory) and the recycler.
NEVER    checks cash, caps or reduce-only (the Gate does), applies the value floor (value.apply_floor runs after
         us), sells for the reserve (the allocator's refill does), or quotes a market without a p and a fresh book.
ORIGIN   Finding 3.3: market making earned little against faster bots and was demoted to a small sleeve kept to
         compare. Day one, skewing by shares put 42% of fills at or through our own fair value (-804), hence quotes
         never inside mm_min_edge of p and a price lean capped at mm_skew_max (0 live). Release 14 (P14): MM lots
         older than mm_inv_max_age_h or above mm_inv_max_usd are recycled 1c through p; lots worth holding (edge
         held >= value_quote_hurdle) are handed to the value book instead. Old code: mmbot/quoting.py
         (compute_quote, kelly_position), mmbot/mm.py (mm_lot_add, mm_lots_reconcile, mm_view, mm_recycle_quote).
OPEN     The quote size (now the setting mm_quote_frac) and the 2-quote inventory cap were the old order_size_frac / value_mid_*
         defaults under an activity size plan that is not carried over: should they be settings? The old deferral
         of short buy-backs while cash was low (mm_recycle_sell_first) and the hold while an allocator sale is in
         flight (alloc_cancel_mm_first) are not here: the Gate's cash check and the bot's reconcile cover them?
"""
import math
from dataclasses import replace

from mmbot2 import pricing
from mmbot2.state import PMAX, PMIN, TICK, Order, ceil_tick, floor_tick

MAX_INVENTORY_QUOTES = 2.0    # the side growing a position stops at 2 quotes held (old value_mid_inventory_quotes)
MAX_ORDER_CASH_FRAC = 0.01    # cash one quote may tie up, x account value: 1,000 at 100k (old max_order_cash_frac)
MAX_HALF_SPREAD = 0.04        # never rest further than 4c from the reservation price: no queue spot worth having
SKEW_PER_QUOTE = 0.005        # a quote's worth of inventory leans prices 0.5c (old skew_mode "quote"), capped
LOTS_MAX = 20                 # lots kept per market; beyond it the two oldest merge (older time, mean price)
MM_TAGS = ("mm", "recycle")   # fills that move the lots; a recycle fill only closes them


class Inventory:
    """What market making holds: per market, signed lots [shares, price, opened at (epoch s)], oldest first."""

    def __init__(self, saved=None):
        saved = saved or {}
        self.lots = {e: [list(x) for x in v] for e, v in saved.get("lots", {}).items()}
        self.handed = dict(saved.get("handed_to_value", {"count": 0, "shares": 0.0}))   # lots now held as value
        self.trips = [list(x) for x in saved.get("trips", [])]   # [epoch s, P&L] of each round trip closed (24 h)
        self.rows = {}               # eid -> the last stale() valuation, for status
        self.recycling = {}          # label -> the last cycle's recycle order, for status

    def shares(self):
        """{eid: signed shares market making holds}, for risk.assess (its own room comes first)."""
        return {e: sum(x[0] for x in lots) for e, lots in self.lots.items()}

    def note_fills(self, fills):
        for f in fills:
            if f.tag in MM_TAGS:
                self.add_fill(f.eid, f.size if f.is_bid else -f.size, f.price, f.at.timestamp(), f.tag == "mm")

    def add_fill(self, eid, signed, price, t, opens):
        """A fill first closes opposite lots, oldest first (a round trip); the rest opens a lot if it may."""
        lots = self.lots.setdefault(eid, [])
        rem = signed
        while abs(rem) > 1e-9 and lots and (lots[0][0] > 0) != (rem > 0):
            n = min(abs(rem), abs(lots[0][0]))
            self.trips.append([t, n * ((price - lots[0][1]) if lots[0][0] > 0 else (lots[0][1] - price))])
            lots[0][0] += n if lots[0][0] < 0 else -n
            rem += n if rem < 0 else -n
            if abs(lots[0][0]) <= 1e-9:
                lots.pop(0)
        if opens and abs(rem) > 1e-9:
            lots.append([rem, price, t])
            while len(lots) > LOTS_MAX:
                a, b = lots[0], lots[1]
                n = a[0] + b[0]
                lots[0:2] = [[n, (a[0] * a[1] + b[0] * b[1]) / n, min(a[2], b[2])]]
        if not lots:
            del self.lots[eid]

    def reconcile(self, view):
        """Lots never exceed the position: a sale by anything else (an allocator IOC) takes the oldest lots first;
        a flat or flipped position, or a market gone, drops them. A fill that only shrank a non-MM holding opened
        a lot against the position, so this drops it too (the old rule checked the position at fill time)."""
        for e in list(self.lots):
            q, lots = view.positions.get(e, 0.0), self.lots[e]
            held = sum(x[0] for x in lots)
            if abs(q) < 1 or q * held <= 0 or e not in view.markets:
                del self.lots[e]
                continue
            if abs(held) > abs(q):
                self.add_fill(e, q - held, 0.0, 0.0, False)   # (the opposite sign closes the oldest lots)

    def stale(self, view, s):
        """{eid: shares to work out}: the lots older than mm_inv_max_age_h, or the shares above mm_inv_max_usd
        valued at p (long q x p, short |q| x (1 - p); unpriced: at the lots' prices), whichever is more."""
        self.reconcile(view)
        now, out, self.rows = view.now.timestamp(), {}, {}
        for e, lots in self.lots.items():
            q, p = sum(x[0] for x in lots), view.p.get(e)
            unit = None if p is None else max(p if q > 0 else 1 - p, TICK)
            usd = abs(q) * unit if unit else sum(abs(x[0]) * (x[1] if x[0] > 0 else 1 - x[1]) for x in lots)
            aged = sum(abs(x[0]) for x in lots if now - x[2] > s.mm_inv_max_age_h * 3600)
            over = math.ceil((usd - s.mm_inv_max_usd) / unit - 1e-9) if unit and usd > s.mm_inv_max_usd else 0
            n = int(min(abs(q), max(aged, over)) + 1e-9)
            why = "+".join(w for w, hit in (("age", aged >= 1), ("$", over >= 1)) if hit)
            self.rows[e] = {"q": q, "usd": usd, "oldest_h": (now - min(x[2] for x in lots)) / 3600, "stale": n,
                            "why": why}
            if n >= 1:
                out[e] = n
        return out

    def hand_over(self, eid):
        """The lots are worth holding to the result: they leave market making (no longer recycled)."""
        lots = self.lots.pop(eid, [])
        self.handed["count"] += 1
        self.handed["shares"] = round(self.handed["shares"] + sum(abs(x[0]) for x in lots), 2)

    def to_dict(self):
        return {"lots": {e: [list(x) for x in v] for e, v in self.lots.items()}, "handed_to_value": self.handed,
                "trips": self.trips}

    def profit_24h(self, now):
        """Realised P&L of the round trips closed in the last 24 hours (the phone summary's MM profit)."""
        self.trips = [x for x in self.trips if now - x[0] <= 86400.0]
        return sum(x[1] for x in self.trips)

    def status(self):
        """The market-making part of status.json's mm_funding."""
        rows = self.rows.values()
        return {"inventory_usd": round(sum(r["usd"] for r in rows), 2), "inventory_markets": len(self.rows),
                "oldest_inventory_h": round(max(r["oldest_h"] for r in rows), 2) if rows else None,
                "stale_markets": sum(1 for r in rows if r["stale"] >= 1),
                "stale_usd": round(sum(r["usd"] * r["stale"] / max(1.0, abs(r["q"])) for r in rows), 2),
                "recycling": dict(self.recycling), "handed_to_value": dict(self.handed),
                "lots": {e: [[round(x[0], 2), round(x[1], 4), round(x[2], 1)] for x in v]
                         for e, v in sorted(self.lots.items())}}


# --------------------------------------------------------------------------- the quotes

def quotes(view, risk, s, inventory):
    """Every market-making order wanted now: two-sided quotes in the band, stale inventory recycled anywhere
    priced. risk is unused: reduce-only, caps and cash are the Gate's (the signature matches the other planners)."""
    stale, out = inventory.stale(view, s), []
    pins = {x.strip() for x in s.alloc_pin.split(",") if x.strip()}
    inventory.recycling = {}
    for eid, p in sorted(view.p.items()):
        book = view.books.get(eid)
        if book is None or eid not in view.markets:
            continue
        inv = view.positions.get(eid, 0.0)
        bid, ask = two_sided(eid, p, inv, book, view, s) if s.band_low <= p <= s.band_high else (None, None)
        if eid in stale and view.markets[eid].label not in pins:
            held = pricing.edge_held(inv, p, book)
            if held is not None and held >= s.value_quote_hurdle - 1e-9:
                inventory.hand_over(eid)
            else:
                bid, ask = recycled(bid, ask, eid, p, inv, stale[eid], book, s)
                rec = ask if inv > 0 else bid
                if rec is not None and rec.tag == "recycle":
                    inventory.recycling[view.markets[eid].label] = {
                        "side": "ask" if inv > 0 else "bid", "qty": rec.size, "price": rec.price,
                        "why": inventory.rows[eid]["why"]}
        out += [o for o in (bid, ask) if o is not None]
    return out


def two_sided(eid, p, inv, book, view, s):
    """(bid, ask) Orders tagged "mm", either None: priced by quote_prices, sized by quote_sizes, ref-guarded."""
    bankroll = view.account.value or view.account.start
    quote = s.mm_quote_frac * bankroll
    lean = max(-s.mm_skew_max, min(s.mm_skew_max, SKEW_PER_QUOTE * inv / quote)) if quote > 0 else 0.0
    bid_px, ask_px = quote_prices(p, p - lean, book, s)
    no_bid, no_ask = ref_guarded(p, book, s)
    bid_n = 0 if bid_px is None or no_bid else quote_size(p, bid_px, True, inv, bankroll, s)
    ask_n = 0 if ask_px is None or no_ask else quote_size(p, ask_px, False, inv, bankroll, s)
    return (Order(eid, True, bid_px, bid_n, "mm") if bid_n >= 1 else None,
            Order(eid, False, ask_px, ask_n, "mm") if ask_n >= 1 else None)


def quote_prices(p, r, book, s):
    """(bid, ask) a tick inside the best other trader, clamped between mm_min_edge and MAX_HALF_SPREAD of the
    reservation price r (the clamp is what stops a penny war pushing us inside the edge), never closer than
    mm_min_edge to p itself, and never crossing: a bid that would reach the best ask drops to a tick below it
    (the best bid's own price when the book is a tick wide). A side with no price left on the grid is None."""
    edge = s.mm_min_edge
    bid_lo, bid_hi = floor_tick(r - MAX_HALF_SPREAD), floor_tick(min(p, r) - edge)
    ask_lo, ask_hi = ceil_tick(max(p, r) + edge), ceil_tick(r + MAX_HALF_SPREAD)
    bid = floor_tick(book.best_bid + TICK) if book.best_bid is not None else bid_lo
    ask = ceil_tick(book.best_ask - TICK) if book.best_ask is not None else ask_hi
    bid, ask = min(max(bid, bid_lo), bid_hi), max(min(ask, ask_hi), ask_lo)
    if book.best_ask is not None:
        bid = min(bid, floor_tick(book.best_ask - TICK))
    if book.best_bid is not None:
        ask = max(ask, ceil_tick(book.best_bid + TICK))
    bid_ok = bid <= p - edge + 1e-9 and (book.best_ask is None or bid < book.best_ask - 1e-9)
    ask_ok = ask >= p + edge - 1e-9 and (book.best_bid is None or ask > book.best_bid + 1e-9)
    return (bid if bid_ok else None), (ask if ask_ok else None)


def ref_guarded(p, book, s):
    """(no_bid, no_ask): Polymarket clearly above the book's own price says do not sell it here (the seller's
    counterparty probably knows), clearly below says do not buy. No book price, no guard."""
    base = pricing.book_price(book)
    if base is None:
        return False, False
    return base - p > s.ref_guard_gap, p - base > s.ref_guard_gap


def quote_size(p, price, is_bid, inv, bankroll, s):
    """Whole shares for one side: a quote's size, less as the position nears its limit on the side that grows
    it (quarter Kelly against p, and MAX_INVENTORY_QUOTES quotes), and within the per-order cash cap. The side
    that shrinks the position gets the full quote (its limit is measured from the far side of flat)."""
    quote = s.mm_quote_frac * bankroll
    limit = pricing.kelly_shares(p, price, is_bid, bankroll, s)
    grows = inv >= 0 if is_bid else inv <= 0
    if grows:
        limit = min(limit, MAX_INVENTORY_QUOTES * quote)
    room = limit - inv if is_bid else limit + inv
    cost = price if is_bid else 1 - price
    return max(0, int(min(quote, room, MAX_ORDER_CASH_FRAC * bankroll / max(cost, TICK))))


def recycled(bid, ask, eid, p, inv, stale_n, book, s):
    """(bid, ask) with the side that reduces stale inventory moved in to p -+ mm_recycle_concession (a tick
    inside the best other order on its far side, so it rests rather than takes), sized to at least the stale
    shares and at most the position, tagged "recycle"; our adding side pulled a tick behind it. The concession
    (1c) is inside value_sell_margin (4c), so the value floor never moves it. A side the ref guard blocks stays
    out. Unlike the old quoter, the size stops at the position: the part beyond would open a short below value."""
    no_bid, no_ask = ref_guarded(p, book, s)
    conc = s.mm_recycle_concession
    if inv >= 1 and not no_ask and (book.best_bid is None or book.best_bid + TICK <= PMAX + 1e-9):
        px = ceil_tick(p - conc)
        if book.best_bid is not None:
            px = max(px, ceil_tick(book.best_bid + TICK))
        size = int(min(max(stale_n, ask.size if ask else 0), inv))
        if bid is not None and bid.price > px - TICK + 1e-9:
            bid = replace(bid, price=floor_tick(px - TICK)) if px - TICK >= PMIN - 1e-9 else None
        return bid, Order(eid, False, px, size, "recycle")
    if inv <= -1 and not no_bid and (book.best_ask is None or book.best_ask - TICK >= PMIN - 1e-9):
        px = floor_tick(p + conc)
        if book.best_ask is not None:
            px = min(px, floor_tick(book.best_ask - TICK))
        size = int(min(max(stale_n, bid.size if bid else 0), -inv))
        if ask is not None and ask.price < px + TICK - 1e-9:
            ask = replace(ask, price=ceil_tick(px + TICK)) if px + TICK <= PMAX + 1e-9 else None
        return Order(eid, True, px, size, "recycle"), ask
    return bid, ask
