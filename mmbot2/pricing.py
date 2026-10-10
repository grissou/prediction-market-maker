"""
Pricing: what a share is worth at the result, and what an action earns per unit of cash it ties up.

OWNS     fair value p from Polymarket (liquid prices only, wrong matches dropped, a race's legs scaled to sum to
         1), the four edge formulas of README §4.2 and the edge of keeping a holding, quarter-Kelly sizes, and
         the cross-sectional estimate of the favourite-longshot tilt s (README §3.1).
NEVER    talks to the network (the bot hands in ref_prices.ReferencePrices.get() and .spreads()), keeps state
         other than the tilt estimate, or prices a market from its own book: no usable Polymarket price = unpriced.
ORIGIN   Finding 3.2 (settlement at the result): a share is worth p whatever the leaderboard marks say, so every
         decision is scored as expected gain at the result per unit of cash. Finding 3.1 (the tilt): the book sits
         at m ~ c + (1 - s)(r - c); s is estimated from the whole cross-section every cycle and reported only
         (status, ladder context): quoting on a tilted reference was tried and never ran live. Old code:
         mmbot/pricing.py (TiltEstimator, normalise), mmbot/bot.py (reference_prices, scaled_ref, value_p,
         update_tilt), mmbot/value.py (alloc_edge_held), mmbot/quoting.py (kelly_position).
OPEN     The estimator compares RAW Polymarket r with the race-normalised book mid (as live); scaling one side
         and not the other moves s by ~0.004 (TILT_ESTIMATOR.md §1 row c). Kept as live; is that intended?
"""
from mmbot2.state import TICK

# The book price the wrong-match filter and the tilt estimator compare Polymarket with (old fair_value): a mid
# of the levels where 200 shares have accumulated, so a 1-share order at a silly price cannot move it.
BOOK_MIN_DEPTH = 200          # shares (old fv_min_depth)
BOOK_MAX_SPREAD = 0.30        # a book wider than this has no price (old max_spread_for_fv)

# The tilt estimator's knobs, at the old defaults (none was ever changed live).
TILT_WINSOR = 0.08            # each market's gap r - m is clipped to +-8c, so one stale tail book cannot drag s
TILT_MIN_MARKETS = 50         # fewer usable markets than this (of ~237): hold the last estimate
TILT_HALFLIFE_MIN = 30.0      # EMA half-life: s moves over days (0.046 -> 0.128 in two), not minutes
# The party-control races are priced by far the most actively and do not follow the cross-section's tilt.
HEADLINE_RACES = ("U.S. House", "U.S. Senate")


def ref_key(market):
    """The key ref_prices.py uses for this market: 'Ohio Senate|Republican'."""
    return f"{market.race}|{market.party}"


# --------------------------------------------------------------------------- fair value (README §4.1)

def depth_price(levels, min_depth):
    """The price of the level where the running total of shares first reaches min_depth; None if it never does."""
    total = 0.0
    for price, shares in levels:
        total += shares
        if total >= min_depth:
            return price
    return None


def book_price(book):
    """The book's own depth-checked mid (other traders only), or None if it is too thin or too wide."""
    if book is None:
        return None
    bid, ask = depth_price(book.bids, BOOK_MIN_DEPTH), depth_price(book.asks, BOOK_MIN_DEPTH)
    if bid is None or ask is None or ask <= bid or ask - bid > BOOK_MAX_SPREAD:
        return None
    return (bid + ask) / 2


def race_scaled(markets, prices):
    """{eid: price} with each race's legs divided by their sum, when every leg of the race has a price.
    A race with an unpriced leg is left as it is: we cannot scale what we cannot see."""
    races = {}
    for eid, m in markets.items():
        races.setdefault(m.race, []).append(eid)
    out = dict(prices)
    for legs in races.values():
        if len(legs) > 1 and all(prices.get(e) is not None for e in legs):
            total = sum(prices[e] for e in legs)
            if total > 0:
                out.update({e: prices[e] / total for e in legs})
    return out


def book_prices(markets, books):
    """{eid: depth-checked mid} for the markets with a fresh, usable book, race-normalised as the live cycle did."""
    mids = {e: book_price(books.get(e)) for e in markets}
    return {e: v for e, v in race_scaled(markets, mids).items() if v is not None}


def wrong_matches(markets, refs, books, s):
    """{eid: (Polymarket, book mid)} where Polymarket is further than s.ref_max_book_gap from the book. That is
    almost always a wrong entry in ref_map.json (a replaced candidate); the bot alerts once per market."""
    mids, out = book_prices(markets, books), {}
    for eid, m in markets.items():
        r, mid = refs.get(ref_key(m)), mids.get(eid)
        if r is not None and mid is not None and abs(r - mid) > s.ref_max_book_gap:
            out[eid] = (r, mid)
    return out


def matched_refs(markets, refs, books, s):
    """{eid: raw Polymarket price} for every market with one that is not a wrong match (liquid or not)."""
    bad = wrong_matches(markets, refs, books, s)
    return {e: refs[ref_key(m)] for e, m in markets.items() if ref_key(m) in refs and e not in bad}


def liquid_refs(markets, refs, spreads, books, s):
    """{eid: raw Polymarket price} where Polymarket's own bid-ask is at most s.ref_max_spread: a thin or
    last-trade-only price can be days old, so it never sets a value."""
    return {e: r for e, r in matched_refs(markets, refs, books, s).items()
            if spreads.get(ref_key(markets[e])) is not None and spreads[ref_key(markets[e])] <= s.ref_max_spread}


def fair_values(markets, refs, spreads, books, s):
    """{eid: p}: the liquid markets' Polymarket price, scaled over its race. The scaling uses every leg's matched
    price, liquid or not (as live): an illiquid leg still says how much of the race is left. Unpriced = absent."""
    scaled = race_scaled(markets, matched_refs(markets, refs, books, s))
    return {e: scaled[e] for e in liquid_refs(markets, refs, spreads, books, s)}


# --------------------------------------------------------------------------- edge per unit of cash (README §4.2)
# Expected gain at the result divided by the cash the action ties up (or frees). A buy at a pays a; a short at b
# is a NO purchase at 1 - b. The TICK floor on a denominator only matters at the edge of the price grid.

def edge_buy(p, ask):
    """Buying YES at the ask: (p - a) / a."""
    return (p - ask) / max(ask, TICK)


def edge_short(p, bid):
    """Selling YES short at the bid: (b - p) / (1 - b)."""
    return (bid - p) / max(1 - bid, TICK)


def edge_keep_long(p, bid):
    """Keeping a long rather than selling it at the bid: (p - b) / b."""
    return (p - bid) / max(bid, TICK)


def edge_keep_short(p, ask):
    """Keeping a short rather than buying it back at the ask: (a - p) / (1 - a)."""
    return (ask - p) / max(1 - ask, TICK)


def edge_held(qty, p, book):
    """The edge left in a holding, against the touch it would be sold into; None for no position (under one
    share) or no other trader on that side. The allocator sells the holdings where this is lowest first."""
    if abs(qty) < 1 or book is None:
        return None
    if qty > 0:
        return None if book.best_bid is None else edge_keep_long(p, book.best_bid)
    return None if book.best_ask is None else edge_keep_short(p, book.best_ask)


# --------------------------------------------------------------------------- sizing

# On a side where p sees no edge, market making may still show this small a size (100 shares at 100k), so it
# stays two-sided without betting against Polymarket (old kelly_no_edge_frac, never changed live).
NO_EDGE_FRAC = 0.001


def kelly_shares(p, price, is_bid, bankroll, s):
    """The most shares fractional Kelly allows on one side. A contract costing `cost` that pays 1 with chance
    `win` has the Kelly stake f = (win - cost) / (1 - cost) of the bankroll; we bet s.kelly_fraction of it,
    capped at s.kelly_max_market_frac. E.g. p 0.20, buy at 0.14: f = 0.06 / 0.86 = 7%, quarter Kelly 1.7%.
    A bid buys YES at price; an ask sells YES, i.e. buys NO at 1 - price."""
    allowance = float(int(NO_EDGE_FRAC * bankroll))
    edge, cost = (p - price, price) if is_bid else (price - p, 1 - price)
    if edge <= 0 or cost <= 0:
        return allowance
    stake = min(s.kelly_fraction * edge / (1 - cost), s.kelly_max_market_frac) * bankroll
    return max(allowance, float(int(stake / cost)))


# --------------------------------------------------------------------------- the tilt (README §3.1)

class TiltEstimator:
    """s from the cross-section: least squares through the origin of the gap g = r - m (winsorised) on
    x = r - c, c = 1 / legs (a lone market counts as two-sided). Since m = c + (1 - s)(r - c), g = s x exactly.
    The per-cycle reading is clipped to [0, s.tilt_max], smoothed by a time-based EMA, and clipped again.
    The median and mean-ratio variants of the old code were never used live and are not carried over."""

    def __init__(self):
        self.s = 0.0              # current estimate
        self.ready = False        # a valid estimate has been taken or restored
        self.t = None             # monotonic seconds of the last valid estimate (None after a restore)
        self.n = 0                # markets in the last cross-section
        self.raw = None           # the last cross-section's unclipped, unsmoothed slope

    def samples(self, markets, books, r, paused):
        """[(r, m, legs)]: markets with a liquid Polymarket price and a book price, outside the headline races
        and any market paused after a jump (its book has not caught up yet)."""
        mids, legs = book_prices(markets, books), {}
        for m in markets.values():
            legs[m.race] = legs.get(m.race, 0) + 1
        return [(r[e], mids[e], legs[m.race]) for e, m in markets.items()
                if e in r and e in mids and e not in paused and m.race not in HEADLINE_RACES]

    @staticmethod
    def slope(samples):
        """sum x g / sum x^2 over the samples, or None if they carry no weight."""
        num = den = 0.0
        for r, m, legs in samples:
            x = r - (1.0 / legs if legs > 1 else 0.5)
            num += x * max(-TILT_WINSOR, min(TILT_WINSOR, r - m))
            den += x * x
        return num / den if den > 1e-12 else None

    def update(self, markets, books, r, now, s, paused=frozenset()):
        """The smoothed s after this cycle's cross-section, or None until there has been one. r is
        liquid_refs(...) (RAW Polymarket, not p); now is monotonic seconds. Too few markets: hold."""
        cross = self.samples(markets, books, r, paused)
        self.n, self.raw = len(cross), self.slope(cross)
        if self.n < TILT_MIN_MARKETS or self.raw is None:
            return self.s if self.ready else None
        reading = max(0.0, min(s.tilt_max, self.raw))
        if not self.ready:
            self.s, self.ready = reading, True
        else:                     # after a restore the first update only starts the clock
            dt = max(0.0, now - self.t) if self.t is not None else 0.0
            self.s += (1 - 0.5 ** (dt / (60.0 * TILT_HALFLIFE_MIN))) * (reading - self.s)
        self.s, self.t = max(0.0, min(s.tilt_max, self.s)), now
        return self.s

    def to_dict(self):
        return {"s": round(self.s, 4), "ready": self.ready, "n": self.n,
                "raw": None if self.raw is None else round(self.raw, 4)}

    @classmethod
    def from_dict(cls, d):
        """A saved estimate (missing or bad: 0, not ready). The EMA continues from it."""
        est = cls()
        try:
            est.s = max(0.0, float(d.get("s") or 0.0))
            est.ready = bool(d.get("ready", est.s > 0))
        except (AttributeError, TypeError, ValueError):
            est.s, est.ready = 0.0, False
        return est
