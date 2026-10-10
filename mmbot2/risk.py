"""
Risk: how much the book can lose at the result, and the gate every order passes before it is sent.

OWNS     the worst case at settlement (per race, summed: the backstop) and the correlated measure (a national swing
         plus 3 sd of the rest) that decides reduce-only; the bloc delta (exposure to the national swing, $ per sd);
         the market-making risk reserve (value adds pause while the rooms are thin); the kill-switch check; the cash
         an order needs; the Gate (reduce-only, per-market, per-state and bloc caps, cash), in priority order.
NEVER    sends, cancels or stops anything (the bot and ops do; the kill switch here is only a test), and never
         prices: it values with the fair values in the View, falling back only for markets that have none.
ORIGIN   Day one the cap was the plain sum of every race's worst outcome (23.3k and growing 1.5k an hour with breadth
         while a 10c national swing cost 249): release 7 replaced it with the correlated measure, kept the sum as a
         backstop (69%, now 100% of the account: a tripwire), and the cap went 30% -> 35% -> 40% as the book became
         fully collateralised value. 2 Oct: an unpriced 9,396-share short was risked as a coin flip (20.6k -> 31k,
         reduce-only), hence the fallbacks in risk_prices. The state cap came after three Rhode Island shorts reached
         27k, a quarter of the account, in one night of selling to a persistent longshot buyer.
         Market making's own inventory is credited back against its reserve first (live mm_room_guard, release 14).
OPEN     The old kill switch needed 2 bad readings in a row (the bot counts them; this check is pure).
         Old stale-cash rule: after 5 minutes without a read the gate stopped gating; here it lets only cash-free orders.
"""
import math
from dataclasses import dataclass, field, replace
from statistics import NormalDist

from mmbot2.state import TICK, adds, held_usd

SWING_SHOCK = 0.15          # the national swing: every Republican price up 15c and Democratic down 15c (old risk_swing_shock)
RISK_Z = 3.0                # ...plus 3 sd of the races' outcomes, independent once the swing is out (old risk_z)
REDUCE_ONLY_HYSTERESIS = 0.03   # leave reduce-only 3 points below the cap: at the cap it flipped every cycle
RESERVE_RESUME = 1.1        # value adds resume once each room is 1.1x its reserve (old MM_RISK_HYST)
HEADLINE_RACES = ("U.S. House", "U.S. Senate")   # party control: these follow the swing at bloc_rho_control
CASH_STALE_S = 300.0        # an account read older than 5 minutes is not trusted for cash
CASH_MARGIN = 25.0          # kept back from the exchange's cash figure for rounding (old cash_gate_reserve)
MIN_SHARES = 1              # exchange fact: whole shares
VALUE_TAGS = ("alloc", "value")   # the adds the risk reserve pauses (market making keeps quoting)
PARTY_SIGN = {"Republican": 1, "Democratic": -1}
_NORMAL = NormalDist()


@dataclass
class RiskState:
    worst_case: float           # sum over races of each race's worst settlement loss (the backstop measure)
    correlated: float           # min(worst_case, swing shock + RISK_Z sd): what the reduce-only cap compares
    bloc_delta: float           # $ per sd of the national swing, + = Republican-leaning
    reduce_only: bool           # above the cap: only orders that shrink a position pass the gate
    room_wc: float | None       # dollars left below the backstop (None: no account value)
    room_corr: float | None     # dollars left below the correlated cap
    adds_paused: bool           # a room below its market-making reserve: allocator buys and tail adds pause
    market_usd: dict = field(default_factory=dict)   # eid -> collateral of the position, valued at p
    state_usd: dict = field(default_factory=dict)    # state -> collateral of the positions there
    p: dict = field(default_factory=dict)            # eid -> probability the risk model used (fair value or fallback)
    bloc_sens: dict = field(default_factory=dict)    # eid -> $ per sd of the swing per YES share


def risk_prices(view):
    """{eid: probability} for every market: the fair value; else the race's unpriced share of 1 - the priced legs;
    else the book's mid; else 0.5. Never a coin flip where something better is known (2 Oct)."""
    out = {}
    races = {m.race for m in view.markets.values()}
    for race in races:
        legs = view.race_legs(race)
        priced = [e for e in legs if e in view.p]
        residual = max(0.0, 1.0 - sum(view.p[e] for e in priced)) / max(1, len(legs) - len(priced))
        for e in legs:
            if e in view.p:
                out[e] = view.p[e]
            elif priced and len(legs) > 1:
                out[e] = residual
            else:
                out[e] = book_mid(view.books.get(e))
    return out


def book_mid(book):
    """The book's mid when both sides are there, else 0.5 (nothing better)."""
    if book is None or book.best_bid is None or book.best_ask is None:
        return 0.5
    return (book.best_bid + book.best_ask) / 2


def payout(legs, winner):
    """What the race's holdings pay if leg `winner` wins (None: a lone market loses). YES pays on its leg, NO off it."""
    return sum((x if i == winner else 0.0) if x > 0 else (0.0 if i == winner else -x)
               for i, (x, _) in enumerate(legs))


def outcomes(legs):
    """[(probability, payout)] for one race: exactly one leg wins; a lone market wins or loses."""
    if len(legs) == 1:
        return [(legs[0][1], payout(legs, 0)), (1 - legs[0][1], payout(legs, None))]
    total = sum(p for _, p in legs) or 1.0
    return [(p / total, payout(legs, i)) for i, (_, p) in enumerate(legs)]


def race_worst_loss(legs):
    """How far the race's holdings, valued at p, fall in their worst outcome. legs = [(YES shares, p)].
    Long 500 Rep and long 500 Dem: worth 500, pays 500 either way, loses 0."""
    value = sum(held_usd(x, p) for x, p in legs)
    return max(0.0, value - min(pay for _, pay in outcomes(legs)))


def race_variance(legs):
    """Variance of the race's settlement payout."""
    outs = outcomes(legs)
    mean = sum(p * pay for p, pay in outs)
    return sum(p * (pay - mean) ** 2 for p, pay in outs)


def held_races(view, p):
    """{race: [(YES shares, p)]} for every race we hold something in."""
    out = {}
    for race in {view.markets[e].race for e, q in view.positions.items() if q and e in view.markets}:
        out[race] = [(view.positions.get(e, 0.0), p[e]) for e in view.race_legs(race)]
    return out


def correlated_risk(races, view, worst):
    """The national swing on the net Republican-minus-Democratic YES shares, plus RISK_Z sd of the races; never
    above the plain worst case, which is a hard bound (3 sd of a few big positions can exceed it)."""
    party_delta = sum(PARTY_SIGN.get(view.markets[e].party, 0) * q
                      for e, q in view.positions.items() if e in view.markets)
    sd = math.sqrt(sum(race_variance(legs) for legs in races.values()))
    return min(worst, SWING_SHOCK * abs(party_delta) + RISK_Z * sd)


def bloc_slope(p_dem, rho, p_ind):
    """$ per sd of the swing for one partisan share under a Gaussian copula: sqrt(rho) phi(Phi^-1(p_dem)) (1 - p_ind).
    A 50c share weighs 0.40, a 0.5c longshot 0.014 (a share count weighed them alike)."""
    x = min(max(p_dem, 1e-5), 1 - 1e-5)
    return math.sqrt(rho) * _NORMAL.pdf(_NORMAL.inv_cdf(x)) * (1 - p_ind)


def bloc_sensitivities(view, p, s):
    """{eid: $ per sd per YES share}, + on Republican YES, - on Democratic. A race needs a Dem and a Rep leg (or is
    one partisan market alone); independents weigh 0 and a race of one partisan leg and an independent counts 0."""
    out = {}
    for race in {m.race for m in view.markets.values()}:
        legs = view.race_legs(race)
        dem = [e for e in legs if view.markets[e].party == "Democratic"]
        rep = [e for e in legs if view.markets[e].party == "Republican"]
        if not ((dem and rep) or (len(legs) == 1 and (dem or rep))):
            continue
        p_ind = sum(p[e] for e in legs if view.markets[e].party not in PARTY_SIGN)
        if p_ind >= 1 - 1e-9:
            continue
        p_dem = p[dem[0]] if dem else 1 - p[rep[0]] - p_ind
        rho = s.bloc_rho_control if race in HEADLINE_RACES else s.bloc_rho
        slope = bloc_slope(p_dem / (1 - p_ind), rho, p_ind)
        for e in dem + rep:
            out[e] = slope * PARTY_SIGN[view.markets[e].party]
    return out


def rooms_paused(room_wc, room_corr, s, was_paused):
    """True while a room is below its market-making reserve; once paused, until both are RESERVE_RESUME x theirs."""
    res_wc, res_corr = max(0.0, s.mm_risk_reserve_wc), max(0.0, s.mm_risk_reserve_corr)
    short = (res_wc > 0 and room_wc < res_wc) or (res_corr > 0 and room_corr < res_corr)
    clear = room_wc >= RESERVE_RESUME * res_wc and room_corr >= RESERVE_RESUME * res_corr
    return short or (was_paused and not clear)


def book_risk(view, p):
    """(sum of worst cases, correlated risk) of the positions in the view."""
    races = held_races(view, p)
    worst = sum(race_worst_loss(legs) for legs in races.values())
    return worst, correlated_risk(races, view, worst)


def mm_share(view, p, mm_shares, worst, corr):
    """(worst case, correlated risk) market making's own inventory adds: the book's risk with it less without it."""
    if not mm_shares:
        return 0.0, 0.0
    rest = {e: q - mm_shares.get(e, 0.0) for e, q in view.positions.items()}
    w, c = book_risk(replace(view, positions=rest), p)
    return max(0.0, worst - w), max(0.0, corr - c)


def assess(view, s, was_reduce_only, was_paused=False, mm_shares=None):
    """This cycle's RiskState. Without an account value the reduce-only and pause states are kept as they were.
    The pause is judged on the value book's share (live mm_room_guard, release 14): market making's own inventory
    is credited back up to the size of its reserve, so market making filling its reserve does not stop value buying."""
    p = risk_prices(view)
    worst, corr = book_risk(view, p)
    sens = bloc_sensitivities(view, p, s)
    risk = RiskState(worst, corr, sum(q * sens.get(e, 0.0) for e, q in view.positions.items()),
                     was_reduce_only, None, None, was_paused, p=p, bloc_sens=sens)
    for e, q in view.positions.items():
        m = view.markets.get(e)
        if m is not None and q:
            risk.market_usd[e] = held_usd(q, p[e])
            if m.state is not None:
                risk.state_usd[m.state] = risk.state_usd.get(m.state, 0.0) + held_usd(q, p[e])
    value = view.account.value
    if value:
        hyst = min(REDUCE_ONLY_HYSTERESIS, s.max_worst_case_frac / 2) if was_reduce_only else 0.0
        risk.reduce_only = (corr > (s.max_worst_case_frac - hyst) * value
                            or worst > (s.worst_case_backstop_frac - hyst) * value)
        risk.room_wc = s.worst_case_backstop_frac * value - worst
        risk.room_corr = s.max_worst_case_frac * value - corr
        mm_wc, mm_corr = mm_share(view, p, mm_shares, worst, corr)
        risk.adds_paused = rooms_paused(risk.room_wc + min(mm_wc, s.mm_risk_reserve_wc),
                                        risk.room_corr + min(mm_corr, s.mm_risk_reserve_corr), s, was_paused)
    return risk


def kill_switch_hit(account, s):
    """True if the account is max_drawdown below the tournament's initial balance (not the value at start-up, so a
    restart cannot reset the limit)."""
    return account.value is not None and account.value < account.start * (1 - s.max_drawdown)


def cash_need(order, qty_held):
    """Cash the order ties up: the part that sells what we hold is free (an ask on YES held, or the covered "sell NO"
    a bid on NO held goes out as); the rest pays its price (a bid) or 1 - price (an ask: buying NO)."""
    covered = max(0.0, qty_held) if not order.is_bid else max(0.0, -qty_held)
    beyond = max(0.0, order.size - covered)
    return beyond * (order.price if order.is_bid else 1 - order.price)


class Gate:
    """One cycle's order checker. The bot offers orders in priority order (allocator, refill, ladder, value quotes,
    market making), so an earlier order takes the cash and room a later one then lacks."""

    def __init__(self, view, risk, s):
        self.view, self.risk, self.s = view, risk, s
        acct = view.account
        fresh = acct.cash is not None and view.mono - acct.read_at <= CASH_STALE_S
        self.cash = max(0.0, acct.cash - CASH_MARGIN) if fresh else 0.0   # stale: only cash-free orders
        size_base = acct.value or acct.start
        self.bloc_cap = s.max_bloc_delta_frac * size_base
        self.bloc = risk.bloc_delta
        self.covered = {}            # (eid, is_bid) -> shares still free to sell from what we hold
        self.side_adds = {}          # (eid, is_bid) -> collateral the admitted adds on that side tie up
        self.state_usd = dict(risk.state_usd)
        self.unclaimed = {e: list(rs) for e, rs in view.resting.items()}
        self.refused_usd = {}        # tag -> cash the strategy wanted and the gate could not give

    def admit(self, order):
        """The order, trimmed to what every rule allows, or None if less than a share is left. The part that sells
        what we hold always passes (reduce-only exists to let it); only the part beyond is checked."""
        qty = self.view.positions.get(order.eid, 0.0)
        key = (order.eid, order.is_bid)
        covered = self.covered.setdefault(key, max(0.0, -qty) if order.is_bid else max(0.0, qty))
        reduce = math.floor(min(order.size, covered) + 1e-9)
        self.covered[key] -= reduce
        self.bloc += reduce * self.bloc_step(order)
        add = self.add_allowed(order, math.floor(order.size - reduce + 1e-9), qty)
        self.commit(order, add)
        size = reduce + add
        if size < MIN_SHARES:
            return None
        return order if size == order.size else replace(order, size=size)

    def add_allowed(self, order, n, qty):
        """Shares (<= n) of the order's adding part every rule allows, in order: reduce-only, the reserve pause, the
        per-market, per-state and bloc caps, then cash (the only refusal counted in refused_usd)."""
        if n < MIN_SHARES or self.risk.reduce_only:
            return 0
        if self.risk.adds_paused and order.tag in VALUE_TAGS:
            return 0
        n = min(n, self.market_room_shares(order, qty), self.state_room_shares(order), self.bloc_room_shares(order))
        return self.cash_shares(order, max(0, n))

    def unit(self, order):
        """Collateral one added share ties up at the result's value: p for YES, 1 - p for NO (never below a tick)."""
        p = self.risk.p.get(order.eid, order.price)
        return max(p if order.is_bid else 1 - p, TICK)

    def market_room_shares(self, order, qty):
        """Shares that keep this side's collateral within market_max_usd if every such order fills: the holding counts
        on the side that grows it; on the other side it is sold first and frees its cash."""
        base = self.risk.market_usd.get(order.eid, 0.0) if adds(order, qty) and qty else 0.0
        used = base + self.side_adds.get((order.eid, order.is_bid), 0.0)
        return math.floor(max(0.0, self.s.market_max_usd - used) / self.unit(order) + 1e-9)

    def state_room_shares(self, order):
        """Shares the state's room allows: positions plus every add admitted there, both sides, as the old cap counted
        every resting order. National markets have no state and no cap."""
        state = self.view.markets[order.eid].state if order.eid in self.view.markets else None
        if state is None:
            return math.inf
        room = self.s.state_max_usd - self.state_usd.get(state, 0.0)
        return math.floor(max(0.0, room) / self.unit(order) + 1e-9)

    def bloc_step(self, order):
        """Bloc delta one share of the order adds (+ buys Republican YES or sells Democratic)."""
        return self.risk.bloc_sens.get(order.eid, 0.0) * (1 if order.is_bid else -1)

    def bloc_room_shares(self, order):
        """Shares before |bloc delta| passes its cap in the order's direction; already beyond, an order that grows it
        is refused and one that shrinks it may go as far as the other side of the same distance."""
        step = self.bloc_step(order)
        if step == 0:
            return math.inf
        limit = max(self.bloc_cap, abs(self.bloc))
        return math.floor(max(0.0, limit - self.bloc * math.copysign(1, step)) / abs(step) + 1e-9)

    def paid_usd(self, order):
        """Cash already locked for this order by a resting one the bot keeps (same market, side and price): the
        exchange's free cash is already net of it."""
        if order.oid is not None:
            return math.inf
        for r in self.unclaimed.get(order.eid, []):
            if r.is_bid == order.is_bid and abs(r.price - order.price) < TICK / 2:
                self.unclaimed[order.eid].remove(r)
                return cash_need(r, self.view.positions.get(order.eid, 0.0))
        return 0.0

    def cash_shares(self, order, n):
        """Shares (<= n) the free cash pays for, counting what a kept resting order already paid."""
        if n < MIN_SHARES:
            return 0
        paid = self.paid_usd(order)
        if paid == math.inf:
            return n
        per = order.price if order.is_bid else 1 - order.price
        ok = min(n, math.floor((self.cash + paid) / per + 1e-9))
        if ok < n:
            self.refused_usd[order.tag] = self.refused_usd.get(order.tag, 0.0) + (n - ok) * per
        self.cash -= max(0.0, ok * per - paid)
        return ok

    def commit(self, order, add):
        """Book what the admitted adding part uses: the side's and the state's collateral, and the bloc delta."""
        usd = add * self.unit(order)
        key = (order.eid, order.is_bid)
        self.side_adds[key] = self.side_adds.get(key, 0.0) + usd
        state = self.view.markets[order.eid].state if order.eid in self.view.markets else None
        if state is not None:
            self.state_usd[state] = self.state_usd.get(state, 0.0) + usd
        self.bloc += add * self.bloc_step(order)
