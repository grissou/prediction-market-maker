"""
The harvest ladder: sell the tilt in tranches, at prices better than today's (README §4.3).

OWNS     the ladder's levels: for a longshot (p <= harvest_longshot_p) asks at the best other ask + each offset, for
         a favourite (p >= harvest_favourite_p) bids at the best other bid - each offset; each level
         harvest_level_usd of collateral and only at harvest_min_edge per unit of cash or better; the carve-out
         (harvest_total_usd of cash locked, kept levels first, then the best edges); the re-quote throttle; the 24-h
         tally of its fills for status.json's `harvest` field.
NEVER    sends or cancels (the bot does), applies the caps, reduce-only or the cash gate (risk.Gate does), or prices a
         sale of a holding below its value (value.apply_floor runs after this). Ladder fills are value positions,
         held to the result. Never rests at or through the book, or through one of our own orders on the other side.
ORIGIN   The owner's rule "sell the tilt in tranches": it costs nothing if the tilt stays put and earns in steps if it
         widens. 0 fills while the value book was already short the tilt; first fills on 8-9 Oct once the refill had
         raised cash: +2,885 of expected value on 19.4k sold short, 15% per unit of cash, the best layer
         (RETURNS_ATTRIBUTION, 10 Oct). The re-quote throttle keeps a level's queue spot and the ladder's writes
         inside the 28-a-minute budget; a move of more than 1c re-quotes at once.
OPEN     The old ladder held a market's levels while its book was stale ("soft"); kept here. The old bot also
         skipped the headline races (alloc_pin); here only the bot's close cut-off and the gate apply.
"""
from dataclasses import replace
from datetime import datetime

from mmbot2 import risk
from mmbot2.pricing import edge_buy, edge_short
from mmbot2.state import PMAX, PMIN, Order

MOVE_TOL = 0.01        # the touch or p moved more than 1c since a market's levels were placed: re-quote at once
DAY_S = 86400.0        # status.json reports the last 24 hours of fills
EPS = 1e-9


def ladder_side(p, s):
    """True (bids) for a favourite, False (asks) for a longshot, None in the middle band (not laddered)."""
    if p >= s.harvest_favourite_p - EPS:
        return True
    if p <= s.harvest_longshot_p + EPS:
        return False
    return None


def level_prices(touch, is_bid, s):
    """The level prices beyond the touch, nearest first, on the grid, without duplicates."""
    out = []
    for off in sorted(set(s.harvest_offsets)):
        price = round(touch - off if is_bid else touch + off, 3)
        if PMIN - EPS <= price <= PMAX + EPS and price not in out:
            out.append(price)
    return out


def level_edge(p, price, is_bid):
    """Edge per unit of cash of a level that fills: a bid buys YES, an ask sells it short."""
    return edge_buy(p, price) if is_bid else edge_short(p, price)


def level_shares(price, is_bid, s):
    """harvest_level_usd of collateral: a bid pays its price a share, an ask (buying NO) pays 1 - price."""
    return int(s.harvest_level_usd / (price if is_bid else 1 - price) + EPS)


def limit_price(view, eid, is_bid):
    """The price a level must stay strictly beyond: the other side's best (other traders' or our own non-ladder
    orders'), so a level never trades at once or against ourselves. None if that side is empty."""
    book = view.books.get(eid)
    theirs = [] if book is None else [book.best_ask if is_bid else book.best_bid]
    ours = [o.price for o in view.resting.get(eid, []) if o.is_bid != is_bid and o.tag != "ladder"]
    prices = [x for x in theirs + ours if x is not None]
    if not prices:
        return None
    return min(prices) if is_bid else max(prices)


def clear_of(price, is_bid, limit):
    return limit is None or (price < limit - EPS if is_bid else price > limit + EPS)


class Ladder:
    def __init__(self, saved=None):
        saved = saved or {}
        # eid -> {"at": when placed, "p", "touch", "is_bid", "levels": [[price, shares]]}: the levels as last quoted
        self.anchors = {e: dict(a, at=datetime.fromisoformat(a["at"])) for e, a in saved.get("anchors", {}).items()}
        # (when, shares, edge $ at p, collateral $) of every ladder fill in the last 24 hours
        self.fills = [(datetime.fromisoformat(f[0]), f[1], f[2], f[3]) for f in saved.get("fills", [])]
        self.p_seen = {}       # eid -> p at the last plan: the fills' edge is measured against it
        self.now = None        # the last plan's wall clock (the 24-h window ends there)
        self.resting = []      # the ladder's orders resting at the last plan
        self.budget = 0.0      # harvest_total_usd at the last plan

    # ------------------------------------------------------------------ plan
    def plan(self, view, rk, s):
        """The ladder's wanted orders this cycle (tag "ladder"), within the carve-out. `rk` is unused: the gate
        applies the caps and reduce-only to these orders after the floor."""
        self.now, self.budget = view.now, s.harvest_total_usd
        self.resting = [o for os in view.resting.values() for o in os if o.tag == "ladder"]
        cands, fresh = [], set()
        for eid in sorted(view.markets):
            held, levels = self.market_levels(view, eid, s)
            cands += [(held, level_edge(view.p[eid], o.price, o.is_bid), o) for o in levels]
            if levels and not held:
                fresh.add(eid)
        wanted = within_budget(cands, view, s)
        for eid in fresh:      # a level the budget left out is not re-placed as "missing" within the window
            self.anchors[eid]["levels"] = [[o.price, o.size] for o in wanted if o.eid == eid]
        return wanted

    def market_levels(self, view, eid, s):
        """(held, [Order]): one market's levels that clear the edge and the book; held = the anchored levels kept
        (requote window not over, touch and p within 1c), else freshly placed from today's touch."""
        p = view.p.get(eid)
        is_bid = None if p is None else ladder_side(p, s)
        if is_bid is None:     # unpriced or in the middle band: its levels are pulled
            self.anchors.pop(eid, None)
            return False, []
        self.p_seen[eid] = p
        book, a = view.books.get(eid), self.anchors.get(eid)
        touch = None if book is None else (book.best_bid if is_bid else book.best_ask)
        if a is not None and a["is_bid"] != is_bid:
            del self.anchors[eid]
            a = None
        if touch is None:      # no fresh book: the levels as they were (a stale touch must not move them)
            return True, [] if a is None else self.held_orders(view, eid, a)
        held = (a is not None and (view.now - a["at"]).total_seconds() < s.harvest_requote_s
                and abs(touch - a["touch"]) <= MOVE_TOL + EPS and abs(p - a["p"]) <= MOVE_TOL + EPS)
        if held:
            levels = self.held_orders(view, eid, a)
        else:
            levels = [Order(eid, is_bid, px, level_shares(px, is_bid, s), "ladder")
                      for px in level_prices(touch, is_bid, s)]
            self.anchors[eid] = {"at": view.now, "p": p, "touch": touch, "is_bid": is_bid, "levels": []}
        limit = limit_price(view, eid, is_bid)
        return held, [o for o in levels if o.size >= 1 and clear_of(o.price, is_bid, limit)
                      and level_edge(p, o.price, is_bid) >= s.harvest_min_edge - EPS]

    @staticmethod
    def held_orders(view, eid, a):
        """The anchored levels: a resting one at its resting price and size (so reconcile keeps it and its queue
        spot), a missing one (filled or expired) at its anchored size."""
        rest = {o.price: o for o in view.resting.get(eid, []) if o.tag == "ladder" and o.is_bid == a["is_bid"]}
        return [Order(eid, a["is_bid"], px, rest[px].size if px in rest else n, "ladder") for px, n in a["levels"]]

    # ------------------------------------------------------------------ fills and status
    def note_fills(self, fills):
        """Tally the ladder's fills: shares, edge at the last p, and the collateral they now hold to the result."""
        for f in fills:
            if f.tag != "ladder":
                continue
            p = self.p_seen.get(f.eid)
            edge = 0.0 if p is None else f.size * ((p - f.price) if f.is_bid else (f.price - p))
            self.fills.append((f.at, f.size, edge, f.size * (f.price if f.is_bid else 1 - f.price)))

    def recent_fills(self):
        if self.now is None:
            return list(self.fills)
        self.fills = [f for f in self.fills if (self.now - f[0]).total_seconds() < DAY_S]
        return self.fills

    def status(self):
        """status.json `harvest`: what rests, what filled in 24 h, and the carve-out's use (cash locked)."""
        recent = self.recent_fills()
        used = sum(o.usd for o in self.resting)
        return {"markets": len({o.eid for o in self.resting}), "levels_resting": len(self.resting),
                "collateral_resting": round(used, 2), "filled_24h": int(sum(f[1] for f in recent) + EPS),
                "edge_filled_24h": round(sum(f[2] for f in recent), 2),
                "collateral_filled_24h": round(sum(f[3] for f in recent), 2),
                "carve": {"total": round(self.budget, 2), "used": round(min(used, self.budget), 2),
                          "free": round(max(0.0, self.budget - used), 2)}}

    def to_dict(self):
        return {"anchors": {e: dict(a, at=a["at"].isoformat()) for e, a in self.anchors.items()},
                "fills": [[f[0].isoformat(), f[1], f[2], f[3]] for f in self.recent_fills()]}


def within_budget(cands, view, s):
    """The candidate levels that fit in harvest_total_usd of cash: kept levels first (they hold queue spots), then
    the best edge per unit of cash. A sale of what we hold needs no cash (risk.cash_need); the last level that fits
    only in part is trimmed."""
    left, cover, out = s.harvest_total_usd, dict(view.positions), []
    for _, _, o in sorted(cands, key=lambda c: (not c[0], -c[1], c[2].eid, c[2].price)):
        q = cover.get(o.eid, 0.0)
        need = risk.cash_need(o, q)
        if need > left + EPS:
            unit = o.price if o.is_bid else 1 - o.price
            o = replace(o, size=int(o.size - (need - left) / unit + EPS))
            if o.size < 1:
                continue
            need = risk.cash_need(o, q)
        left -= need
        covered = min(o.size, max(0.0, -q) if o.is_bid else max(0.0, q))
        cover[o.eid] = q + covered if o.is_bid else q - covered
        out.append(o)
    return out
