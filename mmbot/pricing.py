"""
Pure pricing maths: from raw books and reference prices to a fair value and the risk measures.

Owns the shape of our resting orders (Resting, parse_order, wire_order), clean books (strip_own,
predicted_top, others_top, depth_price: our own orders removed, small orders ignored so nobody
moves the price with one share), fair_value, tilted_ref / blend_fv (leaning toward Polymarket),
TiltEstimator (the tournament's systematic lean against Polymarket) and the race / bloc risk maths
(bloc_slope, race_variance, worst_case_loss).

Pure functions only: no I/O, no clock, no bot state. It never sends an order, never decides a size
and never imports the bot or the mixins; everything it needs comes in as arguments.
"""
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from mmbot import util
from mmbot import config
from mmbot.util import PMAX, PMIN, TICK, parse_ts, rnd
from mmbot.config import CFG


# =============================================================================================
# PRICING - our resting orders, clean books, fair value, race risk maths
# =============================================================================================

def clamp(p): return min(PMAX, max(PMIN, p))
def floor_tick(p): return round(clamp(math.floor(round(p / TICK, 6)) * TICK), 3)   # round down onto the grid
def ceil_tick(p): return round(clamp(math.ceil(round(p / TICK, 6)) * TICK), 3)     # round up onto the grid


@dataclass
class Resting:
    """One of our orders on the book, described in YES terms."""
    order_id: int
    eid: str
    is_bid: bool              # True = buys YES (or sells NO). False = sells YES (or buys NO)
    price: float              # YES price
    qty: float                # shares still open
    expires: datetime | None


def parse_order(o):
    """API order -> Resting, or None if it isn't really resting any more.

    The engine turns "sell YES @ p" into "buy NO @ 1-p" when we don't hold the YES shares, and
    reports orders in that converted form, so a NO order is flipped back into YES terms here.
    """
    if not o.get("open", True) or o.get("priceLimit") is None:
        return None
    exp = parse_ts(o.get("expirationDate"))
    if exp and exp <= util.utcnow():
        return None           # past expiry: the engine ignores it even if it still says open
    yes = str(o["side"]).lower() == "yes"
    is_bid = yes == (str(o["action"]).lower() == "buy")
    price = rnd(o["priceLimit"] if yes else 1 - o["priceLimit"])
    return Resting(int(o["id"]), str(o["exchangeId"]), is_bid, price, float(o["quantity"]), exp)


WIRE_PRIVATE = ("_no_sell", "_cash_need", "_alloc_paired")   # never sent


def wire_order(o):
    """reduce_no_as_sell: an order as the bot builds and tracks it (YES terms: "buy"/"sell" YES at a YES
    price) -> the request body actually sent. Only an order marked "_no_sell" (a bid that buys back NO we hold, see
    Bot.cover_no_qty) changes: "buy YES @ p" goes out as the covered sale "sell NO @ 1-p", which needs no cash. It
    reads back through parse_order as our bid at p, so everything after the send keeps working in YES terms."""
    if not any(k in o for k in WIRE_PRIVATE):
        return o
    w = {k: v for k, v in o.items()                  # _cash_need: the cash gate's note; _alloc_paired: the allocator's
         if k not in WIRE_PRIVATE}
    if o.get("_no_sell"):
        w.update(side="no", action="sell", price=round(1 - o["price"], 3))
    return w


def reserved_cash(raw_orders):
    """Cash our open orders have locked up. A buy of `qty` shares at limit `p` can cost qty*p (p is
    in that order's own side, so a "buy NO @ 0.825" locks 0.825 a share). Selling shares we already
    hold locks no cash; the engine reports those as sells, so they're skipped."""
    total = 0.0
    for o in raw_orders:
        if parse_order(o) and str(o.get("action")).lower() == "buy":
            total += float(o["quantity"]) * float(o["priceLimit"])
    return total


def _num(x):
    """float(x), or None if it isn't a number."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def strip_own(book, mine):
    """Remove our own size from a book so every later decision only sees OTHER traders.
    Without this, once we're top of book the fair value would follow our own quotes and drift."""
    if not mine:
        return book
    own = {"bids": defaultdict(float), "asks": defaultdict(float)}
    for o in mine:
        own["bids" if o.is_bid else "asks"][o.price] += o.qty
    out = dict(book)
    for key in ("bids", "asks"):
        out[key] = [{"price": l["price"], "quantity": q} for l in book.get(key) or []
                    if (q := l["quantity"] - own[key].get(rnd(l["price"]), 0.0)) > 1e-9]
    return out


def predicted_top(book, mine):
    """The (best bid, best ask) the bulk-price endpoint SHOULD show if nothing changed since we
    cached `book` (other traders only): the better of their best price and our own orders.
    If the real top differs, somebody else touched the book and we re-download it."""
    bids = [l["price"] for l in book.get("bids") or []] + [o.price for o in mine if o.is_bid]
    asks = [l["price"] for l in book.get("asks") or []] + [o.price for o in mine if not o.is_bid]
    return (rnd(max(bids)) if bids else None, rnd(min(asks)) if asks else None)


def others_top(top, mine):
    """Other traders' (best bid, best ask) from a bulk-price reading `top`, which includes our own orders.
    A side is None when it is empty, or when one of our orders sits at or better than that price: then the
    reading can't tell whether anybody else is there (and where), so nothing is assumed about it."""
    bid, ask = top
    if bid is not None and any(o.is_bid and o.price >= bid - 1e-9 for o in mine):
        bid = None
    if ask is not None and any(not o.is_bid and o.price <= ask + 1e-9 for o in mine):
        ask = None
    return bid, ask


def depth_price(levels, min_depth):
    """Price of the level where the running total of shares first reaches `min_depth`.

    This is the anti-spoofing step. A 1-share bid at a silly price is skipped, because moving
    this number requires someone to put real size at risk. None = not enough size to trust.
    """
    total = 0.0
    for l in levels:
        total += l["quantity"]
        if total >= min_depth:
            return l["price"]
    return None


def fair_value(book, cfg=CFG):
    """Mid-point of the depth-filtered best bid and ask, or None if the book is too thin or wide.
    (This replaced the size-weighted 'microprice', which one tiny order could move by 3.5c.)"""
    if not book:
        return None
    bid = depth_price(book.get("bids") or [], cfg.fv_min_depth)
    ask = depth_price(book.get("asks") or [], cfg.fv_min_depth)
    if bid is None or ask is None or ask <= bid or ask - bid > cfg.max_spread_for_fv:
        return None
    return (bid + ask) / 2


def tilted_ref(r, s, legs):
    """Polymarket price r as the tournament's favourite-longshot tilt s would price it: c + (1 - s)(r - c), with
    c = 1 / legs in the race (a lone market counts as two-sided: c = 0.5)."""
    c = 1.0 / legs if legs and legs > 1 else 0.5
    return c + (1 - s) * (r - c)


def blend_fv(book_fv, r, cfg):
    """The main loop's blend: book price leaned toward Polymarket by ref_weight."""
    return (1 - cfg.ref_weight) * book_fv + cfg.ref_weight * r


TILT_RATIO_MIN_X = 0.1   # "median"/"wls" estimators: only markets with |r - c| above this give a ratio g / x


class TiltEstimator:
    """Cross-sectional estimate of the tilt s: least squares of the gap (r - book_fv, winsorised at
    +-ref_tilt_winsor) on (r - c), through the origin (ref_tilt_estimator "slope"); or the median ("median") or
    mean ("wls") of the per-market ratios gap / (r - c) over |r - c| > TILT_RATIO_MIN_X (fewer such markets than
    ref_tilt_min_markets: hold). Fewer than ref_tilt_min_markets samples: hold the last value.
    Smoothed by an EMA with half-life ref_tilt_halflife_min (time-based; the first estimate is taken as is) and
    clipped to [0, ref_tilt_max]."""

    def __init__(self, cfg=CFG):
        self.cfg = cfg
        self.s = 0.0          # current estimate
        self.n = 0            # samples in the last update
        self.ready = False    # a valid estimate has been taken (or restored)
        self.t = None         # time of the last valid estimate (seconds; None after a restore)
        self.diag = {}        # the last update's raw readings by every estimator (see update)

    def update(self, samples, now):
        """samples: [(r, book_fv, legs), ...]; now: seconds (any clock, as long as it is always the same one).
        Also leaves self.diag: this cycle's raw (unclipped, unsmoothed) reading by every estimator, the sample
        counts and the share of the slope's x^2 weight in markets whose gap is at the winsor (status tilt_diag)."""
        cfg = self.cfg
        self.n = len(samples)
        w = cfg.ref_tilt_winsor
        kind = getattr(cfg, "ref_tilt_estimator", "slope")
        num = den = pinned = 0.0
        ratios = []
        for r, bfv, legs in samples:
            x = r - (1.0 / legs if legs and legs > 1 else 0.5)
            g = max(-w, min(w, r - bfv))
            num += x * g
            den += x * x
            if abs(r - bfv) >= w - 1e-9:
                pinned += x * x
            if abs(x) > TILT_RATIO_MIN_X:
                ratios.append(g / x)              # this market's own implied tilt
        ratios.sort()
        h = len(ratios) // 2
        alt = {"slope": num / den if den > 1e-12 else None,
               "median": (ratios[h] if len(ratios) % 2 else 0.5 * (ratios[h - 1] + ratios[h])) if ratios else None,
               "wls": sum(ratios) / len(ratios) if ratios else None}
        self.diag = {"n": self.n, "n_ratio": len(ratios),
                     "pinned_weight": round(pinned / den, 3) if den > 1e-12 else None,
                     **{k: (round(v, 4) if v is not None else None) for k, v in alt.items()}}
        if self.n < max(1, cfg.ref_tilt_min_markets):
            return self.s
        if kind in ("median", "wls"):
            if len(ratios) < max(1, cfg.ref_tilt_min_markets):
                return self.s
            est = alt[kind]
        elif den <= 1e-12:
            return self.s
        else:                                     # "slope" (and any unknown value)
            est = num / den
        raw = max(0.0, min(cfg.ref_tilt_max, est))
        if not self.ready:
            self.s, self.ready = raw, True        # first valid estimate: taken as is
        else:                                     # after a restore (t None) the first update only sets the clock
            dt = max(0.0, now - self.t) if self.t is not None else 0.0
            self.s += (1 - 0.5 ** (dt / (60.0 * max(1e-9, cfg.ref_tilt_halflife_min)))) * (raw - self.s)
        self.s = max(0.0, min(cfg.ref_tilt_max, self.s))
        self.t = now
        return self.s

    def to_dict(self):
        return {"s": self.s, "ready": self.ready}

    def from_dict(self, d):
        """Restore a saved estimate (missing or bad = 0, not ready). The EMA continues from it."""
        try:
            d = d if isinstance(d, dict) else {}
            self.s = max(0.0, min(self.cfg.ref_tilt_max, float(d.get("s") or 0.0)))
            self.ready = bool(d.get("ready", self.s > 0))
        except (TypeError, ValueError):
            self.s, self.ready = 0.0, False
        self.t = None
        return self


def normalise(fvs):
    """Outcomes that exclude each other (one party wins a race) must add up to 1; scale them so
    they do. Left alone if any member has no price, since we can't scale what we can't see."""
    if not fvs or any(v is None for v in fvs.values()):
        return fvs
    total = sum(fvs.values())
    return {k: v / total for k, v in fvs.items()} if total > 0 else fvs


_STD_NORMAL = statistics.NormalDist()


def bloc_slope(p_dem, rho, p_ind=0.0):
    """(ideas_H.md H-2): d E[payout of one Dem-win share] / d F is -sqrt(rho) x phi(Phi^-1(p_dem)) x
    (1 - p_ind) under the Gaussian copula (F = the national factor, + = Republican; p_dem conditional on no
    independent winning). Returns the magnitude sqrt(rho) x phi(Phi^-1(p_dem)) x (1 - p_ind)."""
    x = min(max(p_dem, 1e-5), 1 - 1e-5)
    return math.sqrt(rho) * _STD_NORMAL.pdf(_STD_NORMAL.inv_cdf(x)) * (1 - p_ind)


def bloc_sensitivities(races, cfg=CFG):
    """{eid: $ per sd of the national factor per YES share} (+ Rep YES, - Dem YES, so a Republican-
    leaning book is +, like party_delta). races = {race: [(eid, label, p or None, liquid), ...]}, p race-scaled.
    A race needs a "Dem " and a "Rep " leg (labels), or is one partisan market alone; p_ind = the other legs' p;
    p_dem = the Dem leg's p (else 1 - Rep p - p_ind) / (1 - p_ind). Only contracts with a liquid p get one; the
    headline (control) races use bloc_rho_control."""
    out = {}
    for race, legs in races.items():
        kind = {e: ("D" if (lab or "").startswith("Dem ") else "R" if (lab or "").startswith("Rep ") else "I")
                for e, lab, _, _ in legs}
        ps = {e: p for e, _, p, _ in legs}
        dem = [e for e in kind if kind[e] == "D"]
        rep = [e for e in kind if kind[e] == "R"]
        if not ((dem and rep) or (len(legs) == 1 and (dem or rep))):
            continue
        p_ind = sum(ps[e] or 0.0 for e in kind if kind[e] == "I")
        if dem and ps[dem[0]] is not None:
            pd = ps[dem[0]]
        elif rep and ps[rep[0]] is not None:
            pd = 1 - ps[rep[0]] - p_ind
        else:
            continue
        if p_ind >= 1 - 1e-9:
            continue
        rho = cfg.bloc_rho_control if race in cfg.headline_races else cfg.bloc_rho
        slope = bloc_slope(pd / (1 - p_ind), rho, p_ind)
        for e, _, p, liquid in legs:
            if liquid and p is not None and kind[e] != "I":
                out[e] = slope if kind[e] == "R" else -slope
    return out


def race_variance(legs):
    """Variance of one race's settlement payout. legs = [(net YES shares, probability), ...]; exactly one leg
    wins in a race (probabilities scaled to sum to 1); a lone market wins with its own probability."""
    if len(legs) == 1:
        (x, p), = legs
        return x * x * p * (1 - p)                   # YES or NO shares: payout differs by |x| between outcomes
    tot = sum(p for _, p in legs) or 1.0
    probs = [p / tot for _, p in legs]
    pays = []
    for i in range(len(legs)):
        pays.append(sum((x if j == i else 0.0) if x > 0 else (0.0 if j == i else -x) for j, (x, _) in enumerate(legs)))
    mean = sum(p * v for p, v in zip(probs, pays))
    return sum(p * (v - mean) ** 2 for p, v in zip(probs, pays))


def worst_case_loss(legs):
    """How much the marked-to-market value of one race would fall in its worst outcome.

    legs = [(net YES shares, fair value), ...] for exchanges that exclude each other.
    Assumes exactly one leg wins (a race: one party wins). A lone market can win or lose.
    Example: long 500 Rep @0.14 AND long 500 Dem @0.86 -> worth 500 now, pays 500 either way -> 0.
    """
    value = sum(x * fv if x > 0 else -x * (1 - fv) for x, fv in legs)      # NO shares are worth 1-fv each
    if len(legs) == 1:
        scenarios = [[True], [False]]
    else:
        scenarios = [[j == i for j in range(len(legs))] for i in range(len(legs))]

    def payout(wins):   # YES shares pay 1 if their leg wins; NO shares pay 1 if it loses
        return sum((x if w else 0.0) if x > 0 else (0.0 if w else -x) for (x, _), w in zip(legs, wins))

    return max(0.0, value - min(payout(s) for s in scenarios))
