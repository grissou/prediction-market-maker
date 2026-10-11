"""
The bot's state: the few plain records every module reads, each defined once.

OWNS     Market, Book, Order, Fill, Account and View (one cycle's reading of the world), the price grid, the
         edge-per-unit-of-cash formulas' common inputs, and the collateral an order or a position ties up.
NEVER    talks to the exchange, reads a setting, or keeps anything between cycles. Everything here is data.
ORIGIN   The old bot kept per-market state in one 40-field object (`Ex`) plus some 150 attributes added to the bot
         over sixteen releases; several described the same thing twice (the positions alone lived in four places).
         README §5 "between reconciliations the bot tracks its own orders itself" is why Order carries its exchange
         id. Everything is in YES terms: a bid buys YES (or buys back NO we are short), an ask sells YES (or sells
         it short, which the exchange books as buying NO at 1 - price). A position is signed YES shares: +100 is
         100 YES, -100 is 100 NO.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime

TICK, PMIN, PMAX = 0.005, 0.005, 0.995    # exchange fact: prices sit on a 0.5c grid between 0.5c and 99.5c


def floor_tick(p):
    """The highest grid price at or below p (clamped to the grid)."""
    return round(min(PMAX, max(PMIN, math.floor(round(p / TICK, 6)) * TICK)), 3)


def ceil_tick(p):
    """The lowest grid price at or above p (clamped to the grid)."""
    return round(min(PMAX, max(PMIN, math.ceil(round(p / TICK, 6)) * TICK)), 3)


@dataclass(frozen=True)
class Market:
    eid: str                  # the exchange id: one tradable YES contract
    label: str                # short name for logs and status.json, e.g. "Rep Ohio Senate"
    race: str                 # "Ohio Senate": the contracts of one race exclude each other (exactly one wins)
    party: str | None         # "Republican", "Democratic", or None (independents, anything else)
    state: str | None         # postal code ("OH") for the per-state cap; None for the national markets
    close: datetime | None    # when the market stops trading (settlement), None if unknown


@dataclass
class Book:
    bids: list                # other traders' bids, best first, as (price, shares); our own orders removed
    asks: list                # other traders' asks, best first, as (price, shares)
    at: float = 0.0           # monotonic time it was read

    @property
    def best_bid(self):
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self):
        return self.asks[0][0] if self.asks else None


@dataclass
class Order:
    eid: str
    is_bid: bool              # YES terms: True buys YES (or buys back NO), False sells YES (or sells short)
    price: float              # YES price on the grid
    size: float               # shares
    tag: str                  # the strategy that wants it: alloc, refill, value, mm, recycle, ladder
    ioc: bool = False         # immediate-or-cancel: trade at the touch now, never rest (allocator orders)
    oid: str | None = None    # the exchange's order id once placed; None while it is only wanted
    expires: datetime | None = None   # every resting order expires on its own (README §4.4 order expiry)

    @property
    def usd(self):
        """Collateral if it fills from flat: a bid pays its price, an ask (a NO purchase) pays 1 - price."""
        return self.size * (self.price if self.is_bid else 1 - self.price)


@dataclass
class Fill:
    eid: str
    is_bid: bool              # True: we bought YES (or bought back NO)
    price: float              # YES price
    size: float               # shares, positive
    oid: str | None           # our order that filled, if known
    at: datetime              # when it traded
    tag: str = ""             # the strategy whose order filled ("" if the order is not ours or unknown)
    fid: str | None = None    # the exchange's fill id: fills are read "after the last one seen"


@dataclass
class Account:
    value: float | None = None    # cash + positions at the exchange's marks: the leaderboard number
    cash: float | None = None     # cash free for new orders, net of what our resting orders lock
    start: float = 100000.0       # the initial balance (the kill switch measures from it)
    read_at: float = -1e9         # monotonic time of the read; the cash gate trusts it for a few minutes


@dataclass
class View:
    """One cycle's reading of the world. Built by the bot, read by every strategy, never changed by them."""
    now: datetime                                   # wall clock (UTC)
    mono: float                                     # monotonic clock, for ages
    markets: dict                                   # eid -> Market
    books: dict                                     # eid -> Book (only markets with a fresh book)
    positions: dict                                 # eid -> signed YES shares held
    resting: dict                                   # eid -> list[Order] resting now (simulated in a dry run)
    account: Account
    p: dict = field(default_factory=dict)           # eid -> fair value; absent = unpriced (README §4.1)
    tilt: float | None = None                       # the favourite-longshot tilt s (README §3.1), None until known

    def race_legs(self, race):
        return [e for e, m in self.markets.items() if m.race == race]


# ---------------------------------------------------------------- what the bot keeps between cycles
# Bot.__init__ creates one of each; the methods read and write through them, so every attribute is defined once.

@dataclass
class Orders:
    """Our resting orders and what each one is for (bot.py FOREIGN: ours = an id that came back when we placed it)."""
    ours: dict = field(default_factory=dict)         # oid -> Order resting (simulated in a dry run)
    tags: dict = field(default_factory=dict)         # oid -> strategy tag, saved across restarts for fill attribution
    unconfirmed: list = field(default_factory=list)  # orders sent whose outcome was lost: ours if they turn up resting
    account: dict = field(default_factory=dict)      # eid -> the account's real orders at the last full read
    sim_ids: int = 0                                 # dry-run order ids handed out


@dataclass
class Books:
    """Other traders' books and how current each one is (bot.py BOOKS)."""
    cached: dict = field(default_factory=dict)       # eid -> Book of other traders, from the last REST read
    tops: dict = field(default_factory=dict)         # eid -> (bid, ask) from the last bulk read, ours included
    confirmed: dict = field(default_factory=dict)    # eid -> monotonic time the book was last read or matched the tops
    dirty: set = field(default_factory=set)          # books the feed or the tops say changed, until they are re-read


@dataclass
class Budget:
    """When each kind of read last happened, so the requests stay within the budget (monotonic seconds)."""
    last_cycle: float = -1e9                         # the last completed cycle (the watchdog reads it)
    last_full: float = -1e9                          # the last full REST reconciliation (-1e9 forces one)
    last_account: float = -1e9                       # the last account read (positions, fills, account)
    account_pending: bool = False                    # a fill seen by the feed, not yet read back
    markets_read: float = -1e9                       # the last markets reload
    settings_read: float = -1e9                      # the last check of the live settings file


@dataclass
class Ledger:
    """The account as last read and the P&L book: cost per market, realised P&L, EV samples."""
    positions: dict = field(default_factory=dict)    # eid -> signed YES shares
    marks: dict = field(default_factory=dict)        # eid -> the exchange's valuation price (leaderboard)
    account: Account = field(default_factory=Account)  # value, cash, start balance
    cost: dict = field(default_factory=dict)         # eid -> average cost of the open position (realised P&L)
    realised: float = 0.0                            # realised P&L from our fills, saved across restarts
    last_fill_id: str | None = None                  # newest fill already processed
    ev_hist: list = field(default_factory=list)      # [epoch s, EV at the result] every EV_SAMPLE_S, 25 h (summary)


@dataclass
class Guard:
    """The jump guard, the reduce-only switch and the kill switch (README §4.4)."""
    last_p: dict = field(default_factory=dict)       # eid -> last fair value, for the jump guard
    paused: dict = field(default_factory=dict)       # eid -> monotonic time the jump guard's pause ends
    reduce_only: bool = False                        # reduce-only is on
    reduce_since: datetime | None = None             # wall time reduce-only began
    risk: object = None                              # the last RiskState, for status and the adds-paused hysteresis
    kill_hits: int = 0                               # account readings in a row below the kill line


@dataclass
class Diag:
    """What status.json and the phone summary say about this cycle and the day."""
    refused: dict = field(default_factory=dict)      # tag -> cash the gate refused last cycle (the refill's demand)
    blocked: dict = field(default_factory=dict)      # reason -> orders not sent this cycle (status.json blocked_by)
    deferred: int = 0                                # writes deferred to the next cycle by the budget (never dropped)
    events: list = field(default_factory=list)       # [epoch s, "rate"|"error"] for the summary's period counts
    rate_seen: int = 0                               # the client's 429 count already noted
    pauses_seen: int = 0                             # the client's 429 pauses already alerted
    feed_down: object = None                         # monotonic time the feed went unhealthy; True once alerted


def held_usd(qty, p):
    """What a position is worth at the result, valued at p: a long qty x p, a short |qty| x (1 - p)."""
    return qty * p if qty >= 0 else -qty * (1 - p)


def adds(order, qty):
    """True if the order would grow the position's size (or flip it) rather than shrink it."""
    return (qty >= 0) if order.is_bid else (qty <= 0)
