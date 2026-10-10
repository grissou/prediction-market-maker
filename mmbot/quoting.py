"""
What we want resting on one exchange: Quote, Kelly sizing, plan_sizes, compute_quote, the value-
floor quotes, exit_quote, side_needs_change and the mark-fragility helpers.

Pure functions only: no I/O, no bot state, never imports the bot or the mixins.
"""
import math
from dataclasses import dataclass, field, replace

from mmbot import util
from mmbot import config
from mmbot import pricing
from mmbot.util import PMAX, PMIN, TICK
from mmbot.config import CFG
from mmbot.pricing import ceil_tick, floor_tick


# =============================================================================================
# QUOTING - what we want resting on one exchange
# =============================================================================================

@dataclass(frozen=True)
class Quote:
    bid: float | None = None
    bid_size: int = 0
    ask: float | None = None
    ask_size: int = 0
    # The most aggressive price an order ALREADY resting may keep (still min_edge from our reservation
    # price, not crossing anyone). Used to leave an order alone when the target moved by a tick.
    # None = no tolerance (e.g. election-night exits). Not part of comparing two quotes.
    bid_limit: float | None = field(default=None, compare=False)
    ask_limit: float | None = field(default=None, compare=False)
    # The size before size FACTORS (capital ceiling, favourite-longshot bad side) but within every position /
    # cash / risk limit: the biggest order already resting that may stay (None = the size itself).
    bid_max: int | None = field(default=None, compare=False)
    ask_max: int | None = field(default=None, compare=False)


NO_QUOTE = Quote()

# Account value assumed when none is given (examples, tests): the tournament's starting balance.
DEFAULT_BANKROLL = 100_000.0


def kelly_position(p, price, bankroll, cfg=CFG, yes=True):
    """The largest position (in shares) fractional Kelly (kelly_fraction) allows on one side of a market.

    p        what we believe the true YES probability is (Polymarket)
    price    our YES price for this side: the bid when buying YES, the ask when selling YES
    yes      True = buying YES at `price`; False = selling YES there, i.e. buying NO at 1 - price

    A contract costing `cost` that pays 1 with probability `win` has the Kelly stake
        f = (win - cost) / (1 - cost) = edge / (1 - cost)       (as a fraction of the bankroll)
    e.g. Polymarket 0.20, buying YES at 0.14: f = 0.06 / 0.86 = 7%; quarter Kelly = 1.7% of the account (capped at 2%).
    The stake is capped at kelly_max_market_frac of the account, then turned into shares (stake / cost).
    No edge (or a negative one) -> just the small kelly_no_edge_frac allowance. kelly_edge_cap > 0 caps the edge
    sized on (B: a persistent gap to Polymarket is not all edge).
    """
    allowance = int(cfg.kelly_no_edge_frac * bankroll)
    edge, cost = (p - price, price) if yes else (price - p, 1 - price)
    if edge <= 0 or cost <= 0:
        return allowance
    if cfg.kelly_edge_cap > 0:                    # B: size on the spread, not the gap (0 = off)
        edge = min(edge, cfg.kelly_edge_cap)
    stake = min(cfg.kelly_fraction * edge / (1 - cost), cfg.kelly_max_market_frac) * bankroll
    return max(allowance, int(stake / cost))


def quote_lock(fv, cfg=CFG):
    """Cash one share of our quotes on a market locks. Two-sided: a bid at fv - x locks fv - x and an ask
    at fv + x (really a NO buy at 1 - fv - x) locks 1 - fv - x: together 1 - 2x, at most 1 - 2 * min_edge.
    Near 0 or 1 the tail guard leaves one cheap side: a bid near 0 locks ~fv, an ask near 1 locks ~1 - fv."""
    if fv < cfg.tail_low:
        return max(fv, 0.01)
    if fv > cfg.tail_high:
        return max(1 - fv, 0.01)
    return 1 - 2 * cfg.min_edge


def plan_sizes(activity, headline, bankroll, cfg=CFG, prev=None, lock=None, prev_bankroll=None):
    """Shares per quote for each exchange: {eid: shares}.

    activity   {eid: how busy the market is} for the markets being quoted (any non-negative scale)
    headline   eids of the party-control markets: they get headline_size_frac, the biggest by far
    prev       the current plan: a market keeps its size unless the new one differs by > size_plan_step
    prev_bankroll   the account value prev was planned for: prev is rescaled to today's account first,
                    so sizes always follow the account, and only activity shifts are held back
    lock       {eid: cash one share of quotes there locks} (see quote_lock); missing = 1 (the most it can be)

    Everything else shares what's left of the capital budget (quote_capital_frac of the account) in
    proportion to the SQUARE ROOT of activity, so busy markets get much more but no single one swallows
    everything, clamped to [size_min_frac, size_max_frac]. If the headline markets alone would leave
    the rest less than their minimum, they're scaled down to fit. Sizes are rounded down to 50 shares.
    """
    lock = lock or {}
    cost = {e: lock.get(e, 1.0) for e in activity}
    budget = cfg.quote_capital_frac * bankroll
    lo, hi = cfg.size_min_frac * bankroll, cfg.size_max_frac * bankroll
    others = [e for e in activity if e not in headline]
    plan = {e: cfg.headline_size_frac * bankroll for e in activity if e in headline}
    floor = sum(lo * cost[e] for e in others)
    left = budget - sum(s * cost[e] for e, s in plan.items())
    if plan and left < floor:                              # headline markets would starve the rest
        scale = max(0.0, budget - floor) / sum(s * cost[e] for e, s in plan.items())
        plan = {e: s * scale for e, s in plan.items()}
        left = budget - sum(s * cost[e] for e, s in plan.items())
    weight = {e: math.sqrt(max(float(activity.get(e) or 0.0), 0.0)) for e in others}
    if others and not any(weight.values()):
        weight = dict.fromkeys(others, 1.0)                # no activity data at all: share equally

    def total(k):
        return sum(min(hi, max(lo, k * weight[e])) * cost[e] for e in others)

    k_lo, k_hi = 0.0, 1.0                                  # find k so that the others use exactly what's left
    while total(k_hi) < left and k_hi < 1e15:
        k_hi *= 2
    for _ in range(80):
        mid = (k_lo + k_hi) / 2
        k_lo, k_hi = (mid, k_hi) if total(mid) <= left else (k_lo, mid)
    for e in others:
        plan[e] = min(hi, max(lo, k_lo * weight[e]))
    grow = bankroll / prev_bankroll if prev_bankroll else 1.0
    out = {}
    for e, size in plan.items():
        size = int(size // 50 * 50) if size >= 50 else int(size)
        old = (prev or {}).get(e)
        if old:
            old = int(old * grow // 50 * 50) if old * grow >= 50 else int(old * grow)   # the account moved: follow it
            if old and abs(size / old - 1) <= cfg.size_plan_step:
                size = old                                 # small activity shift: keep the size (and queue spots)
        out[e] = size
    return out


def fl_side(fv, prev, cfg=CFG):
    """Favourite-longshot bias: which side of a market priced at fv is the "bad" one -> (side, extra edge,
    size factor), side None = no bias. Below fl_low our bid buys the longshot students overpay for; above
    fl_high our ask sells the favourite they undersell. prev = last cycle's side for this market: once on, a
    bias holds until fv is fl_hysteresis back across the line, so a market sitting at 20c doesn't flip
    (and get re-quoted) every cycle. Mid-band bids get fl_mid_bid_extra_edge (default 0 = no bias)."""
    if not cfg.fl_bias_enabled or fv is None:
        return None, 0.0, 1.0
    h = cfg.fl_hysteresis
    if fv < cfg.fl_low or (prev == "bid" and fv < cfg.fl_low + h):
        return "bid", cfg.fl_bad_side_extra_edge, cfg.fl_bad_side_size_factor
    if fv > cfg.fl_high or (prev == "ask" and fv > cfg.fl_high - h):
        return "ask", cfg.fl_bad_side_extra_edge, cfg.fl_bad_side_size_factor
    if cfg.fl_mid_bid_extra_edge > 0:
        return "mid", cfg.fl_mid_bid_extra_edge, 1.0
    return None, 0.0, 1.0


MARK_FRAG_STEP_SECONDS = 600.0       # the mark-fragility step: 10 minutes
MARK_FRAG_MAX_STEP_FACTOR = 1.5      # two snapshots further apart than 15 min are a gap, not a step


def mark_step_changes(series, step=MARK_FRAG_STEP_SECONDS, max_factor=MARK_FRAG_MAX_STEP_FACTOR):
    """series = [(seconds, mid)] oldest first -> the mid's changes over ~step seconds: each point paired with the
    first point at least `step` later, if that one is at most step x max_factor later (overlapping pairs)."""
    out, j, n = [], 0, len(series)
    for i in range(n):
        t0, m0 = series[i]
        j = max(j, i + 1)
        while j < n and series[j][0] < t0 + step:
            j += 1
        if j < n and series[j][0] <= t0 + step * max_factor:
            out.append(series[j][1] - m0)
    return out


def mark_step_sd(series, min_samples=60, floor=0.002, step=MARK_FRAG_STEP_SECONDS):
    """Mark-fragility estimator: sd of the 10-min change of one market's mid, at least `floor`; None with fewer
    than min_samples changes (no estimate -> no cap)."""
    ch = mark_step_changes(series, step)
    if len(ch) < max(2, min_samples):
        return None
    mean = sum(ch) / len(ch)
    return max(math.sqrt(sum((c - mean) ** 2 for c in ch) / (len(ch) - 1)), floor)


def compute_quote(fv, inv, eff_inv, best_bid, best_ask, cfg=CFG, reduce_only=False, no_bid=False, no_ask=False,
                  bid_cap=None, ask_cap=None, kelly_p=None, bankroll=None, shift=0.0, order_size=None,
                  position_limit=None, min_edge=None, reduce_size=None, net_inv=None, age_hours=0.0,
                  adding_factor=1.0, bias_side=None, bias_edge=0.0, bias_size=1.0, adding_limit_factor=1.0, frag_limit=None,
                  why=None, adding_per_market=False, value_p=None, skew_inv=None, age_off=False,
                  skew_add_flat=False):
    """
    fv         fair YES probability
    inv        our net YES shares on THIS exchange (negative = net NO); drives the hard position limit
    eff_inv    inventory after netting the other parties in the same race; drives skew and reduce-only
    best_bid   best bid from OTHER traders (our own orders already removed), or None
    best_ask   best ask from OTHER traders, or None
    reduce_only        only trade toward flat (market about to close, or worst-case-loss cap hit)
    no_bid / no_ask    block one side (national-swing cap, reference-price guard)
    bid_cap / ask_cap  max shares on one side, None = no cap (tail guard)
    kelly_p            a liquid Polymarket probability: position limits then come from Kelly sizing
                       instead of max_position_frac
    bankroll           account value; every size is a fraction of it (None = DEFAULT_BANKROLL)
    shift              extra amount to lower the reservation price by (national-swing shading; see decide)
    order_size         shares per quote for this market (from the activity-based size plan); None = order_size_frac
    position_limit     flat limit on |net shares| here instead of Kelly / max_position_frac (party-control markets)
    min_edge           overrides cfg.min_edge (e.g. wider in markets priced from Polymarket alone)
    reduce_size        bigger size allowed on the side that SHRINKS the position (up to the position itself),
                       e.g. a thin-book market quoting 100 shares that holds 7,585 from before
    net_inv            the race-netted position (None = eff_inv): with limits_use_race_net the position limit also
                       applies to it on the side that grows it; it also decides which side is "adding"
    age_hours          share-weighted age of this market's position: adds the age skew (skew_age_*)
    adding_factor      size factor for the side that grows |net_inv| (capital ceiling; 1 = no change)
    bias_side          "bid" / "ask" / None: the side that quotes bias_edge further from r (capped at
                       max_half_spread) and at bias_size times its size (favourite-longshot bias, see fl_side).
                       Ignored on a side that shrinks this exchange's position: that side quotes normally
                       (its size beyond the position itself still gets bias_size)
    adding_limit_factor  the side that grows |net_inv| WANTS at most this fraction of the normal position limit
                       (turnover control: a market whose position cannot turn), at least 1 share while the normal
                       limits would quote it; bid_max / ask_max keep the normal limits. 1 = no change
    frag_limit         a cap (today only the mid limit, below) on |this exchange's position| on the side
                       that GROWS it only (bid when inv >= 0, ask when inv <= 0); the shrinking side is untouched
    behind_best        False = no behind-the-best sizing here (ref-only markets: already small); see
                       cfg.behind_best_size_enabled
    reduce_fv          reduce_from_book (A): the tournament book's own price, or None = off. The side that shrinks
                       THIS exchange's position (long -> ask, short -> bid) measures its band from it (same skew and
                       shift) when that is more aggressive than fv, never less; that side then quotes at most the
                       position. The adding side is capped 2 x min_edge behind the lowest reducing price that may
                       rest (bid <= ask keep limit - 2 x min_edge when long, mirror when short), so we never meet
                       our own order
    why                optional dict, filled with why["ro_clip"] = "", "bid", "ask" or "bid ask": the side(s) that
                       ONLY the reduce-only race-net clip emptied (no no_bid / no_ask block, no zero cap on it)
    adding_per_market  Package 8 (adding_factor_per_market): adding_factor and adding_limit_factor pick the adding side
                       by THIS exchange's position inv (the side growing |inv|; of a side that shrinks it, the part
                       beyond the position is adding too), not by net_inv. False = race-netted, as before
    value_p            Package 10 A: this market's liquid, race-scaled Polymarket probability (Bot.value_p), or None.
                       With cfg.value_mode the side reducing THIS exchange's position never rests beyond
                       p -+ value_sell_margin (value_side_prices); with cfg.value_quote_hurdle > 0 the adding side
                       follows the hurdle / middle-band rule (A4). None = neither (and value_mode alone still runs
                       the max_skew_through clamp in reduce-only)
    skew_inv           Package 12 M2 (skew_target_inventory): the inventory the skew is measured from, i.e. the
                       race-netted (inv - target) (Bot.skew_target_inputs); None = eff_inv (as before). Only the
                       reservation-price skew changes: limits, reduce-only and reduce_join_best still use inv / eff_inv
    age_off            Package 12 M2: no age skew (a +EV holding in value_mode); False = age_skew as before
    skew_add_flat      P12 red team RT12-7: with skew_inv, the side that ADDS to the (race-netted) position keeps the
                       skew from flat when that is the more cautious price (a target that is just the current holding
                       must not unbrake buying more of it); False = skew_inv on both sides
    """
    bankroll = bankroll or DEFAULT_BANKROLL
    max_order_cash = cfg.max_order_cash_frac * bankroll
    if order_size is None:
        order_size = cfg.order_size_frac * bankroll
    else:
        max_order_cash = max(max_order_cash, order_size)   # a planned size has already been capital-checked
    vmode = bool(getattr(cfg, "value_mode", False))         # Package 10 A1
    hurdle = getattr(cfg, "value_quote_hurdle", 0.0) if value_p is not None else 0.0   # Package 10 A4
    v_mid = hurdle > 0 and cfg.value_mid_low <= value_p <= cfg.value_mid_high
    if v_mid:                                 # A4 middle band: the side growing |inv| stops at N quote sizes
        mid_limit = max(0.0, cfg.value_mid_inventory_quotes) * order_size
        frag_limit = mid_limit if frag_limit is None else min(frag_limit, mid_limit)
    # 1. Reservation price = fair value shifted against our inventory. Long -> lower r -> we bid
    #    less eagerly and offer more eagerly, which pushes the position back toward flat.
    #    Package 12 M2: from the distance to a target holding instead (skew_inv), the informed market maker.
    s_inv = eff_inv if skew_inv is None else skew_inv
    if cfg.skew_mode == "quote" and order_size > 0:
        skew = cfg.skew_per_quote * s_inv / order_size
    else:
        skew = cfg.skew_per_share * s_inv
    skew = max(-cfg.skew_max, min(cfg.skew_max, skew))
    r = fv - skew - shift
    # (1a: the reduce_from_book reservation price that was computed here was removed on simplify: off live)
    r_bid = r_ask = r
    if skew_add_flat and skew_inv is not None and abs(eff_inv) >= 1:   # (P12 red team RT12-7: adding side braked)
        flat = (cfg.skew_per_quote * eff_inv / order_size if cfg.skew_mode == "quote" and order_size > 0
                else cfg.skew_per_share * eff_inv)
        flat = max(-cfg.skew_max, min(cfg.skew_max, flat))
        if eff_inv > 0:
            r_bid = min(r_bid, fv - flat - shift)
        else:
            r_ask = max(r_ask, fv - flat - shift)
    fv_bid = fv_ask = fv

    # 2. Allowed band for each side: at least min_edge, at most max_half_spread away from r.
    edge = cfg.min_edge if min_edge is None else min_edge
    widest = max(cfg.max_half_spread, edge)
    bias_bid = bias_side == "bid" and inv > -1         # (a side that shrinks a position quotes normally)
    bias_ask = bias_side == "ask" and inv < 1
    bid_edge = min(widest, edge + bias_edge) if bias_bid else edge
    ask_edge = min(widest, edge + bias_edge) if bias_ask else edge
    bid_lo, bid_hi = floor_tick(r_bid - widest), floor_tick(r_bid - bid_edge)
    ask_lo, ask_hi = ceil_tick(r_ask + ask_edge), ceil_tick(r_ask + widest)

    # 3. Penny: one tick better than the best other trader, so we're first in the queue while
    #    keeping the widest spread possible. Then clamp into the band. That clamp is what stops a
    #    penny war with another bot from pushing us below min_edge. No other quote -> band edge.
    #    R4: improve_ticks = 0 joins the best price instead; undercut_step_back > 0 quotes that far from r
    #    (not at min_edge) when another trader already sits inside our min_edge band.
    imp = cfg.improve_ticks * TICK
    bid = floor_tick(best_bid + imp) if best_bid is not None else bid_lo
    ask = ceil_tick(best_ask - imp) if best_ask is not None else ask_hi
    if cfg.undercut_step_back > 0:
        if best_bid is not None and best_bid > bid_hi + 1e-9:
            bid = floor_tick(r_bid - max(bid_edge, cfg.undercut_step_back))
        if best_ask is not None and best_ask < ask_lo - 1e-9:
            ask = ceil_tick(r_ask + max(ask_edge, cfg.undercut_step_back))
    bid = min(max(bid, bid_lo), bid_hi)
    ask = max(min(ask, ask_hi), ask_lo)
    if (not reduce_only or vmode) and cfg.max_skew_through < 1.0:   # (P10 A1: in reduce-only too)
        # Skew sheds inventory by quoting less greedily, never by paying through our own fair value
        # (day one: fills at <= -1c edge lost -804 at the 60-min mid; rival bots pick those quotes off).
        bid_hi = min(bid_hi, floor_tick(fv_bid + cfg.max_skew_through))   # (fv_bid / fv_ask = fv unless A)
        ask_lo = max(ask_lo, ceil_tick(fv_ask - cfg.max_skew_through))
        bid, ask = min(bid, bid_hi), max(ask, ask_lo)

    # 4. Never cross another trader's order (that would trade instantly, as a taker).
    if best_ask is not None:
        bid = min(bid, floor_tick(best_ask - TICK))
    if best_bid is not None:
        ask = max(ask, ceil_tick(best_bid + TICK))

    # 4a. Reducing side joins the best other price on its side (reduce_join_best): never through fair, never crossing.
    if (cfg.reduce_join_best and not reduce_only
            and abs(eff_inv) >= max(1, cfg.reduce_join_min_shares)):
        if eff_inv > 0 and best_ask is not None:
            ask = min(ask, max(ceil_tick(best_ask), ceil_tick(fv + cfg.reduce_join_min_edge)))   # never moves out
            if best_bid is not None:
                ask = max(ask, ceil_tick(best_bid + TICK))
            ask_lo = min(ask_lo, ask)
            bid = min(bid, floor_tick(ask - TICK))
        elif eff_inv < 0 and best_bid is not None:
            bid = max(bid, min(floor_tick(best_bid), floor_tick(fv - cfg.reduce_join_min_edge)))
            if best_ask is not None:
                bid = min(bid, floor_tick(best_ask - TICK))
            bid_hi = max(bid_hi, bid)
            ask = max(ask, ceil_tick(bid + TICK))
    # 4d. Package 10: A4 the adding side's hurdle price (tails), A1 the reducing side's value floor. Each only moves a
    #     price AWAY from the other side (and the keep limits with it); step 4 runs again after them.
    if hurdle > 0 and not v_mid:
        if inv > -1:                              # the bid adds (not buying back a short)
            cap = value_p / (1 + hurdle)
            if cap < PMIN - 1e-9:
                no_bid = True
            else:
                bid, bid_hi = min(bid, floor_tick(cap)), min(bid_hi, floor_tick(cap))
        if inv < 1:                               # the ask adds (not selling down a long)
            flo = 1 - (1 - value_p) / (1 + hurdle)
            if flo > PMAX + 1e-9:
                no_ask = True
            else:
                ask, ask_lo = max(ask, ceil_tick(flo)), max(ask_lo, ceil_tick(flo))
    if vmode and value_p is not None:
        fb, fa = value_side_prices(value_p, inv, cfg)
        if fa is not None:
            ask, ask_lo = max(ask, fa), max(ask_lo, fa)
        if fb is not None:
            if fb < PMIN - 1e-9:
                no_bid = True
            else:
                bid, bid_hi = min(bid, fb), min(bid_hi, fb)
    if (hurdle > 0 and not v_mid) or (vmode and value_p is not None):   # (step 4 again: never cross another trader)
        if best_ask is not None:
            bid = min(bid, floor_tick(best_ask - TICK))
        if best_bid is not None:
            ask = max(ask, ceil_tick(best_bid + TICK))

    # 5. Size: shrink toward the position limit on each side, and cap the cash tied up per order.
    #    Limits: Kelly sizing when we have a liquid Polymarket price, else max_position_frac of the account.
    long_limit = short_limit = cfg.max_position_frac * bankroll
    if position_limit is not None:
        long_limit = short_limit = position_limit
    elif kelly_p is not None:
        long_limit = kelly_position(kelly_p, bid, bankroll, cfg, yes=True)     # most YES we'd hold
        short_limit = kelly_position(kelly_p, ask, bankroll, cfg, yes=False)   # most NO we'd hold
    net = eff_inv if net_inv is None else net_inv
    clipped = [False, False]                  # (the reduce-only race-net clip emptied the bid / ask; see why)

    def limited(bid_size, ask_size):
        """Steps 5-6 after the size factors: position, cash and risk limits (applied to the scaled sizes and,
        for bid_max / ask_max, to the unscaled ones alike)."""
        if reduce_size is not None and reduce_size > order_size:
            if inv < 0:
                bid_size = max(bid_size, min(reduce_size, -inv))   # buying back a short
            elif inv > 0:
                ask_size = max(ask_size, min(reduce_size, inv))    # selling down a long
        if cfg.limits_use_race_net:               # the race-netted position counts too, on the side that grows it
            if net > 0:
                bid_size = min(bid_size, long_limit - net)
            elif net < 0:
                ask_size = min(ask_size, short_limit + net)
        if frag_limit is not None:                # mark-fragility cap: only the side growing |inv| here
            if inv >= 0:
                bid_size = min(bid_size, frag_limit - inv)
            if inv <= 0:
                ask_size = min(ask_size, frag_limit + inv)
        bid_size = min(bid_size, max_order_cash / bid)          # buying YES costs `bid` a share
        ask_size = min(ask_size, max_order_cash / (1 - ask))    # selling YES = buying NO at 1-ask

        # 6. Risk overrides.
        if reduce_only:
            clipped[:] = [bid_size >= 1 and -eff_inv < 1, ask_size >= 1 and eff_inv < 1]
            bid_size = min(bid_size, -eff_inv)     # only buy back a short
            ask_size = min(ask_size, eff_inv)      # only sell down a long
        if no_bid:
            bid_size = 0
        if no_ask:
            ask_size = 0
        if bid_cap is not None:
            bid_size = min(bid_size, bid_cap)
        if ask_cap is not None:
            ask_size = min(ask_size, ask_cap)
        return bid_size, ask_size

    bid_size = min(order_size, long_limit - inv)
    ask_size = min(order_size, short_limit + inv)
    bid_max, ask_max = limited(bid_size, ask_size)     # the sizes no factor shrank: the most that may stay
    if bias_side == "bid" and bias_size != 1.0:        # bad side: smaller, except the part that only unloads
        bid_size = min(bid_size, max(order_size * bias_size, -inv))
    if bias_side == "ask" and bias_size != 1.0:
        ask_size = min(ask_size, max(order_size * bias_size, inv))
    bid_size, ask_size = limited(bid_size, ask_size)
    # Turnover control: the side growing |net| WANTS no more than the smaller limit allows, like a size factor (the
    # bid_max / ask_max above keep the normal limits, so an order already resting within them stays). A side the
    # normal limits would quote keeps at least 1 share, so its resting order is not pulled when a market turns dead.
    hold_bid = hold_ask = False
    sel = inv if adding_per_market else net       # Package 8: which position decides the adding side
    if adding_limit_factor < 1.0:
        f = max(0.0, adding_limit_factor)
        if sel > 0 and bid_size >= 1:
            room = long_limit * f - inv
            if cfg.limits_use_race_net:
                room = min(room, long_limit * f - net)
            bid_size, hold_bid = max(1, min(bid_size, room)), True
        elif sel < 0 and ask_size >= 1:
            room = short_limit * f + inv
            if cfg.limits_use_race_net:
                room = min(room, short_limit * f + net)
            ask_size, hold_ask = max(1, min(ask_size, room)), True
    if adding_factor < 1.0 and adding_per_market:   # Package 8: by this market's own position; the part of a
        f = max(0.0, adding_factor)                 # reducing side beyond the position (it flips it) shrinks too
        red_bid, red_ask = max(0.0, -inv), max(0.0, inv)
        bid_size = min(bid_size, red_bid + max(0.0, bid_size - red_bid) * f)
        ask_size = min(ask_size, red_ask + max(0.0, ask_size - red_ask) * f)
    elif adding_factor < 1.0:                 # capital ceiling: the side growing |net| shrinks (0 = not quoted)
        if net >= 0:
            bid_size = min(bid_size, bid_size * adding_factor)
        if net <= 0:
            ask_size = min(ask_size, ask_size * adding_factor)
    bid_size, ask_size = max(0, int(bid_size)), max(0, int(ask_size))   # the API only takes whole shares
    if hold_bid and adding_factor > 0:
        bid_size = max(1, bid_size)
    if hold_ask and adding_factor > 0:
        ask_size = max(1, ask_size)
    bid_max, ask_max = max(bid_size, int(bid_max)), max(ask_size, int(ask_max))
    if (hurdle > 0 and not v_mid) or (vmode and value_p is not None):
        # Package 10 (red team RT-7): the part of a REDUCING quote beyond this exchange's position opens the other
        # side, i.e. it adds: never at a price the adding rule refuses (A4: the hurdle price in the tails; A1: p, so
        # the flip never sells below / buys above value). Such a quote stops at the position.
        tail = hurdle > 0 and not v_mid
        if inv <= -1 and bid > (value_p / (1 + hurdle) if tail else value_p) + 1e-9:
            bid_size, bid_max = min(bid_size, int(-inv)), min(bid_max, int(-inv))
        if inv >= 1 and ask < (1 - (1 - value_p) / (1 + hurdle) if tail else value_p) - 1e-9:
            ask_size, ask_max = min(ask_size, int(inv)), min(ask_max, int(inv))

    if bid >= ask:
        return NO_QUOTE
    if why is not None:                       # (sizes from the last limited() call: the quoted ones)
        why["ro_clip"] = " ".join(
            s for s, hit in (("bid", clipped[0] and not bid_size and not no_bid and (bid_cap is None or bid_cap >= 1)),
                             ("ask", clipped[1] and not ask_size and not no_ask and (ask_cap is None or ask_cap >= 1)))
            if hit)
    # How far a resting order may sit from these prices and still be kept: never closer than min_edge
    # to r, never crossing the best other order.
    bid_limit = min(bid_hi, floor_tick(best_ask - TICK)) if best_ask is not None else bid_hi
    ask_limit = max(ask_lo, ceil_tick(best_bid + TICK)) if best_bid is not None else ask_lo
    return Quote(bid if bid_size else None, bid_size, ask if ask_size else None, ask_size, bid_limit, ask_limit,
                 bid_max if bid_max != bid_size else None, ask_max if ask_max != ask_size else None)


def value_side_prices(p, inv, cfg=CFG):
    """Package 10 A1: (highest bid, lowest ask) the side REDUCING this exchange's position inv may rest at, given the
    market's liquid race-scaled Polymarket p: long (inv >= 1) -> (None, ceil_tick(p - value_sell_margin)); short
    (inv <= -1) -> (p + margin without the grid clamp, floored to the tick (< PMIN = no bid), None); flat -> (None,
    None). Selling below p (buying back above p) gives value away at the outcome."""
    m = cfg.value_sell_margin
    if inv >= 1:
        return None, ceil_tick(p - m)
    if inv <= -1:
        x = math.floor(round((p + m) / TICK, 6)) * TICK
        return (round(min(x, PMAX), 3) if x >= PMIN - 1e-9 else 0.0), None
    return None, None


def value_floor_quote(q, p, inv, cfg=CFG):
    """Package 10 A1 on a finished Quote (decide): the reducing side moved back to the value floor
    (value_side_prices) if anything priced it beyond; its keep limit too. Only moves away from the other side; sizes
    unchanged. A bid that would have to go below the grid is dropped."""
    if p is None or q is NO_QUOTE:
        return q
    fb, fa = value_side_prices(p, inv, cfg)
    if fa is not None and q.ask is not None and q.ask < fa - 1e-9:
        q = replace(q, ask=fa, ask_limit=max(q.ask_limit, fa) if q.ask_limit is not None else None)
    if fb is not None and q.bid is not None and q.bid > fb + 1e-9:
        if fb < PMIN - 1e-9:
            q = replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        else:
            q = replace(q, bid=fb, bid_limit=min(q.bid_limit, fb) if q.bid_limit is not None else None)
    return q


def exit_quote(fv, inv, best_bid, best_ask, cfg=CFG, bankroll=None, max_size=None, value_floor=None):
    """Election-night exit: get this market flat, trading against other orders if needed.

    Long -> sell at the best other bid (an immediate trade), but never below fv - exit_max_slippage;
    if the best bid is worse than that, the order rests at that floor instead. Short -> the mirror
    image. Only the side that reduces the position is quoted. Sized to the whole position, capped at
    max_order_cash_frac of the account per order (the rest goes on later cycles).
    value_floor (Package 10 A1 iii): a liquid Polymarket p -> never sell below p - value_sell_margin (buy back above
    p + margin) either; None = unchanged.
    """
    bankroll = bankroll or DEFAULT_BANKROLL
    if inv >= 1:
        price = max(best_bid if best_bid is not None else 0.0, fv - cfg.exit_max_slippage)
        if value_floor is not None:
            price = max(price, value_floor - cfg.value_sell_margin)
        price = ceil_tick(price)
        size = int(min(inv, max(cfg.max_order_cash_frac * bankroll / max(1 - price, TICK), max_size or 0)))
        return Quote(ask=price, ask_size=size) if size >= 1 else NO_QUOTE
    if inv <= -1:
        price = min(best_ask if best_ask is not None else 1.0, fv + cfg.exit_max_slippage)
        if value_floor is not None:
            price = min(price, value_floor + cfg.value_sell_margin)
            if price < PMIN - 1e-9:
                return NO_QUOTE                   # (no grid price at or below the value floor)
        price = floor_tick(price)
        size = int(min(-inv, max(cfg.max_order_cash_frac * bankroll / max(price, TICK), max_size or 0)))
        return Quote(bid=price, bid_size=size) if size >= 1 else NO_QUOTE
    return NO_QUOTE


def unsafe_order(o, price, size, limit, is_bid):
    """A resting order that must not stay: we want nothing on its side, it's beyond its limit price (the price
    past which the quote loses money; the target price when there's no limit), or bigger than now allowed."""
    if price is None:
        return True
    lim = limit if limit is not None else price
    return (o.price > lim + 1e-9 if is_bid else o.price < lim - 1e-9) or o.qty > size + 1e-9


def side_needs_change(resting, price, size, cfg, now, limit=None, is_bid=True, max_size=None):
    """True if what's resting on ONE side of an exchange doesn't match what we want there.
    Leaving a good order alone keeps its place in the queue, which is worth money. So an order a tick
    (reprice_tolerance_ticks) off the target is kept, as long as it's inside `limit` (see Quote).
    max_size: the biggest order still acceptable (default: size). Burst mode passes the normal size here while
    `size` is the burst size, so a full-size order placed before the burst stays and a half-size one placed
    during it stays too - neither is cancelled and re-placed when the exchange is slow.
    """
    if price is None:
        return bool(resting)                                    # want nothing: anything there must go
    if len(resting) != 1:
        return True                                             # missing, or duplicates
    o = resting[0]
    if abs(o.price - price) > 1e-9:
        tol = cfg.reprice_tolerance_ticks
        close = abs(o.price - price) <= tol * TICK + 1e-9
        safe = limit is not None and (o.price <= limit + 1e-9 if is_bid else o.price >= limit - 1e-9)
        if not (close and safe):
            return True                                         # wrong price
    if not (size * cfg.keep_fraction <= o.qty <= (max_size if max_size is not None else size)):
        return True                                             # mostly filled, or bigger than we now want
    if o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry:
        return True                                             # about to expire
    return False
