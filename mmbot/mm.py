"""
MarketMakingMixin: MM funding (mmf_*, mm_*: lots, hurdle, recycler, hand-over, refill), the swaps
(swap_*), P141 / P142 reporting and the MM rooms (alloc_room*, buyback_net).

Methods only: all state lives on the Bot instance (self); no __init__ here. Never imports bot.py.
"""
import csv
import math
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone

from mmbot import util
from mmbot import pricing
from mmbot import quoting
from mmbot import measure
from mmbot.util import PARTY_SIGN, PMAX, PMIN, TICK, bot_path, iso, log, parse_ts
from mmbot.pricing import ceil_tick, floor_tick
from mmbot.quoting import value_side_prices


class MarketMakingMixin:

    # ------------------------------------------------------------------------------ P14: market-making funding
    MM_FUNDING_KEYS = ("mm_funding",)   # read-only status.json key(s): identity checks ignore them (as EV_KEYS)
    MM_LOTS_MAX = 20              # MM lots kept per market (beyond it the two oldest merge: older time, mean price)
    MM_SEED_HOURS = 24.0          # no status.json mm_funding at start: fills.csv this far back (order notes: 1 day)
    MM_REFILL_GAP = 60.0          # mm_refill_fast: a refill is planned at most this often (s)
    MM_FAST_EXPIRE = 300.0        # ...a fast refill sale still not sent after this long is dropped (re-planned) (s)
    MM_SENT_LAG = 120.0           # ...a market a refill IOC sold is not planned again until the positions read
                                  #    shows the sale, or this long after it (red team RT13-3: a lagging read) (s)
    MM_MAKER_SKIP = ("alloc", "set_ladder", "arb", "take", "harvest")   # notes that are not resting quotes

    def mmf_init(self, d=None):
        """P14 state, restored from status.json "mm_funding" (d): the MM lots {eid: [[signed shares, YES price, wall
        time], ...]} oldest first, the hand-over tally, the alert clock. With no "lots" there, the first cycle seeds
        the lots from fills.csv (mm_lots_seed)."""
        d = d if isinstance(d, dict) else {}

        def num(x, default=None):
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else default
        self.mm_lots = {}
        raw = d.get("lots")
        for e, v in (raw.items() if isinstance(raw, dict) else ()):
            lots = [[num(x[0]), num(x[1]), num(x[2])] for x in (v if isinstance(v, list) else ())
                    if isinstance(x, (list, tuple)) and len(x) == 3 and None not in (num(x[0]), num(x[1]), num(x[2]))
                    and abs(num(x[0])) > 1e-9]
            if lots and (all(x[0] > 0 for x in lots) or all(x[0] < 0 for x in lots)):   # (mixed signs: unusable)
                self.mm_lots[str(e)] = lots
        self.mm_lots_seeded = isinstance(raw, dict)
        self.mmf_seed = "restored from status.json" if self.mm_lots_seeded else None
        h = d.get("handed_to_value") if isinstance(d.get("handed_to_value"), dict) else {}
        self.mmf_handed = {"count": int(num(h.get("count"), 0)), "shares": num(h.get("shares"), 0.0),
                           "usd": num(h.get("usd"), 0.0)}
        r = d.get("refill") if isinstance(d.get("refill"), dict) else {}
        self.mmf_refill = {"runs": int(num(r.get("runs"), 0)), "sold_usd": num(r.get("sold_usd"), 0.0),
                           "mm_sold_usd": num(r.get("mm_sold_usd"), 0.0),
                           "last": r.get("last") if isinstance(r.get("last"), str) else None}
        self.mmf_below_since = num(d.get("below_half_since_wall"))   # wall time cash / a room fell below half
        self.mmf_alerted = bool(d.get("alerted")) and self.mmf_below_since is not None
        self.mmf_refill_last_m = -1e18            # monotonic time of the latest fast refill plan
        self.mmf_recycling = {}                   # eid -> {"side", "qty", "price", "why"}: this cycle's recycler
        self.mmf_logged = {}                      # eid -> the latest "MM RECYCLE" line's (side, price, qty)
        self.mmf_sent = {}                        # eid -> (now_m, |q| before, shares sold): refill IOC sales (RT13-3)
        self.mmf_deferred = 0                     # P14.1 3: buy-back shares deferred below half the cash target
        self.mmf_hold = {}                        # P14.1 1: eid -> when an allocator sale there cancelled our quotes
        #                                           (the reducing side stays off until the positions read shows it)
        self.alloc_fvs = None                     # P14.1 5: this cycle's fair values (the swap room check)
        self.p141_events = deque()                # P14.1 7: the 24-h report log (p141_log), restored at start
        cut = util.time.time() - self.P141_LOG_H * 3600
        for x in (d.get("events") if isinstance(d.get("events"), list) else ()):
            if (isinstance(x, (list, tuple)) and len(x) == 5 and num(x[0]) is not None and num(x[0]) >= cut
                    and isinstance(x[1], str)):
                self.p141_events.append((num(x[0]), x[1], num(x[2], 0.0), num(x[3], 0.0), num(x[4], 0.0)))
        self.mmf_room_mm = (None, None)           # mm_room_guard: the MM lots' worst-case / correlated contribution
        self.mmf_room_value = (None, None)        # ...and the value book's share of the rooms the pause is decided on
        self.mmf_warned = False

    def mmf_on(self, cfg=None):
        """Any P14 flag on (the alert and the summary piece follow them; the status key is always written)."""
        cfg = cfg or self.cfg
        return bool(getattr(cfg, "mm_recycle_enabled", False) or getattr(cfg, "mm_refill_fast", False)
                    or getattr(cfg, "mm_room_guard", False))

    def mmf_warn(self, what, err):
        if not self.mmf_warned:
            self.mmf_warned = True
            log.warning("MM funding %s failed (%s: %s) - skipped (logged once)", what, type(err).__name__, err)

    def mm_meta(self, oid):
        """The order note of order oid (keys are ints in a run, strings after a restart)."""
        meta = self.order_meta or {}
        m = meta.get(oid)
        if m is None:
            m = meta.get(str(oid))
        if m is None and str(oid).isdigit():
            m = meta.get(int(str(oid)))
        return m

    def mm_is_maker(self, meta):
        """A fill of one of our RESTING quotes (mm_carry_24h's maker class): a note with our side, none of the
        take / arbitrage / allocator / set ladder tags."""
        return (isinstance(meta, dict) and meta.get("our_side") in ("bid", "ask")
                and not any(meta.get(k) for k in self.MM_MAKER_SKIP))

    def mm_in_band(self, p):
        return p is not None and self.cfg.value_mid_low <= p <= self.cfg.value_mid_high

    def mm_lot_add(self, e, buy, qty, price, t, q_now, opens=True):
        """One MM fill into market e's lots: it first closes opposite lots (FIFO, a round trip), the rest opens a lot
        only in the direction the position q_now has (a fill that only shrank a non-MM holding opens nothing)."""
        lots = self.mm_lots.setdefault(e, [])
        rem = qty if buy else -qty
        while abs(rem) > 1e-9 and lots and (lots[0][0] > 0) != (rem > 0):
            lot = lots[0]
            n = min(abs(rem), abs(lot[0]))
            lot[0] += n if lot[0] < 0 else -n
            rem += n if rem < 0 else -n
            if abs(lot[0]) <= 1e-9:
                lots.pop(0)
        if opens and abs(rem) > 1e-9 and q_now * rem > 0:
            lots.append([rem, price, t])
            while len(lots) > self.MM_LOTS_MAX:    # the two oldest merged: their total, mean price, the older time
                a, b = lots[0], lots[1]
                n = a[0] + b[0]
                lots[0:2] = [[n, (a[0] * a[1] + b[0] * b[1]) / n, min(a[2], b[2])]]
        if not lots:
            self.mm_lots.pop(e, None)

    def mm_lots_reconcile(self, inv):
        """MM lots never exceed the position: flat / flipped / a market gone -> dropped; shrunk (a sale by anything
        else: an allocator IOC, a take) -> the oldest lots go first."""
        for e in list(self.mm_lots):
            q, lots = float(inv.get(e, 0.0)), self.mm_lots[e]
            s = sum(x[0] for x in lots)
            if abs(q) < 1 or not lots or q * s <= 0 or e not in self.ex:
                self.mm_lots.pop(e, None)
                continue
            drop = abs(s) - abs(q)
            while drop > 1e-9 and lots:
                n = min(drop, abs(lots[0][0]))
                lots[0][0] += n if lots[0][0] < 0 else -n
                drop -= n
                if abs(lots[0][0]) <= 1e-9:
                    lots.pop(0)
            if not lots:
                self.mm_lots.pop(e, None)

    def mm_lots_seed(self, inv, now):
        """First start without status.json mm_funding.lots: rebuild the MM lots from fills.csv's last MM_SEED_HOURS
        (the order notes, which tell a resting quote from a take / arbitrage / allocator order, are kept a day): each
        row of a resting quote with p = its fv_at_quote (else the market's p now) in the middle band, through
        mm_lot_add against today's positions, then reconciled. Older inventory is not MM (left to the allocator)."""
        self.mm_lots_seeded = True
        try:
            rows = measure.read_fills(bot_path(self.cfg.fills_csv))
        except (OSError, ValueError, csv.Error):
            rows = []
        n = 0
        for r in rows:
            ts = parse_ts(r.get("filled_at"))
            side = r.get("our_side")
            if ts is None or ts.timestamp() < now - self.MM_SEED_HOURS * 3600 or side not in ("bid", "ask"):
                continue
            if not self.mm_is_maker(self.mm_meta(r.get("order_id"))):
                continue
            e = str(r.get("exchange_id"))
            try:
                p = float(r.get("fv_at_quote"))
            except (TypeError, ValueError):
                p = self.ev_p(e)
            try:
                qty, price = abs(float(r.get("qty") or 0)), float(r.get("quote_price") or r.get("fill_price") or 0)
            except ValueError:
                continue
            if qty <= 0 or not self.mm_in_band(p) or e not in self.ex:
                continue
            self.mm_lot_add(e, side == "bid", qty, price, ts.timestamp(), float(inv.get(e, 0.0)))
            n += 1
        self.mm_lots_reconcile(inv)
        self.mmf_seed = (f"seeded from fills.csv ({n} middle-band maker fills of the last {self.MM_SEED_HOURS:.0f} h "
                         f"-> {len(self.mm_lots)} markets)")
        log.info("MM inventory %s", self.mmf_seed)

    def mm_inv_step(self, new, inv):
        """P14, cycle step 4 (always; read-only): the MM lots kept in step with this cycle's new fills and the
        positions read (see Config, P14). Never raises."""
        try:
            now = util.time.time()
            if not self.mm_lots_seeded:
                self.mm_lots_seed(inv, now)
            for f in reversed(list(new or ())):            # (the API lists newest first)
                meta = self.mm_meta(f.get("orderId"))
                if not self.mm_is_maker(meta):
                    continue
                e = str(f.get("exchangeId"))
                p = self.ev_fill_p.get(str(f.get("id")))   # (mm_carry_24h's p at fill; else the fair value when quoted)
                p = meta.get("fv") if p is None else p
                rec = bool(meta.get("recycle"))
                if (not rec and not self.mm_in_band(p)) or e not in self.ex:
                    continue                                # (a recycled side's fill always closes MM lots)
                ts = parse_ts(f.get("filledAt"))
                price = meta.get("price") if meta.get("price") is not None else f.get("price")
                self.mm_lot_add(e, meta["our_side"] == "bid", abs(float(f.get("quantity") or 0)), float(price or 0),
                                ts.timestamp() if ts is not None else now, float(inv.get(e, 0.0)), opens=not rec)
            self.mm_lots_reconcile(inv)
        except Exception as err:                          # reporting must never disturb trading
            self.mmf_warn("lot tracking", err)

    def mm_p(self, e):
        """$ value per YES share for MM inventory: the race-scaled liquid Polymarket p, else the last fair value."""
        p = self.ev_p(e)
        if p is None and e in self.ex:
            p = self.ex[e].last_fv
        return p

    def mm_view(self, now=None, eids=None):
        """{eid: {"q", "usd", "oldest_h", "stale", "why"}} for every market holding MM inventory: q its MM shares
        (signed), usd at p (long q x p, short |q| x (1 - p); no p: at the lots' prices), stale = shares to work out:
        max(the lots older than mm_inv_max_age_h, the shares above mm_inv_max_usd (0 = no $ limit)) <= |q|."""
        cfg = self.cfg
        now = util.time.time() if now is None else now
        out = {}
        for e in (self.mm_lots if eids is None else [x for x in eids if x in self.mm_lots]):
            lots = self.mm_lots[e]
            q = sum(x[0] for x in lots)
            if abs(q) < 1:
                continue
            p = self.mm_p(e)
            unit = None if p is None else (p if q > 0 else 1 - p)
            usd = (abs(q) * unit if unit is not None
                   else sum(abs(x[0]) * (x[1] if x[0] > 0 else 1 - x[1]) for x in lots))
            aged = sum(abs(x[0]) for x in lots if now - x[2] > cfg.mm_inv_max_age_h * 3600)
            over = 0.0
            if cfg.mm_inv_max_usd > 0 and usd > cfg.mm_inv_max_usd + 1e-9 and unit:
                over = math.ceil((usd - cfg.mm_inv_max_usd) / unit - 1e-9)
            stale = int(min(abs(q), max(aged, over)) + 1e-9)
            why = "+".join(w for w, hit in (("age", aged >= 1), ("$", over >= 1)) if hit)
            out[e] = {"q": q, "usd": usd, "oldest_h": (now - min(x[2] for x in lots)) / 3600, "stale": stale,
                      "why": why}
        return out

    def mm_hurdle(self, cfg=None):
        """The edge-held from which MM inventory is VALUE: value_quote_hurdle, or alloc_min_edge_buy while it is 0."""
        cfg = cfg or self.cfg
        return cfg.value_quote_hurdle if cfg.value_quote_hurdle > 0 else cfg.alloc_min_edge_buy

    def mm_hand_over(self, ex, edge, v):
        """P14 1: market ex's MM inventory is value (edge-held >= the hurdle): its lots leave the MM book (no longer
        recycled; the reserve refill sells the lowest-edge value positions instead)."""
        lots = self.mm_lots.pop(ex.eid, [])
        sh = sum(abs(x[0]) for x in lots)
        self.mmf_handed["count"] += 1
        self.mmf_handed["shares"] = round(self.mmf_handed["shares"] + sh, 2)
        self.mmf_handed["usd"] = round(self.mmf_handed["usd"] + v["usd"], 2)
        self.mmf_logged.pop(ex.eid, None)
        log.warning("MM RECYCLE %s: %+.0f MM shares ($%.0f, stale by %s) handed to the value bucket - edge-held "
                    "%.1f%% >= hurdle %.1f%%: held, not recycled (the reserve refills from the lowest-edge value "
                    "positions)", ex.label, sum(x[0] for x in lots), v["usd"], v["why"] or "-", 100 * edge,
                    100 * self.mm_hurdle())

    def mm_recycle_quote(self, ex, q, fv, best_bid, best_ask, vp, cfg, now_m):
        """P14 1 (mm_recycle_enabled; decide, before the value floor): a market with stale MM
        inventory gets its REDUCING side moved in to fair -+ mm_recycle_concession (never crossing the best other
        bid / ask; at or beyond the value floor in value mode), sized max(the quoter's size, the stale shares) within
        the position; our adding side a tick behind it. A side the quoter left out stays out (recorded as blocked).
        +EV inventory (edge-held >= mm_hurdle) is handed over instead (mm_hand_over)."""
        self.mmf_recycling.pop(ex.eid, None)
        if fv is None or abs(ex.inv) < 1 or ex.eid not in self.mm_lots or ex.label in self.alloc_pins():
            return q                              # (a pinned label is never pushed out: alloc_pin, as the allocator)
        v = self.mm_view(util.time.time(), [ex.eid]).get(ex.eid)
        if v is None or v["stale"] < 1 or v["q"] * ex.inv <= 0:
            return q
        edge = self.alloc_edge_held(ex, ex.inv, now_m)
        if edge is not None and edge >= self.mm_hurdle(cfg) - 1e-9:
            self.mm_hand_over(ex, edge, v)
            return q
        n, conc, value = v["stale"], cfg.mm_recycle_concession, getattr(cfg, "value_mode", False) and vp is not None
        rec = {"side": "ask" if ex.inv > 0 else "bid", "qty": n, "price": None, "why": v["why"]}
        if ex.inv >= 1:
            if q.ask is None or q.ask_size < 1:
                self.mmf_recycling[ex.eid] = {**rec, "blocked": "no reducing side"}
                return q
            px = ceil_tick(fv - conc)
            if best_bid is not None:
                px = max(px, ceil_tick(best_bid + TICK))            # never crossing the best other bid
            if value:
                px = max(px, value_side_prices(vp, ex.inv, cfg)[1])  # never below the value floor
            ask = min(q.ask, px)
            size = max(q.ask_size, min(n, int(ex.inv)))
            bid, bid_size, bid_limit = q.bid, q.bid_size, q.bid_limit
            top = floor_tick(ask - TICK)                            # our adding bid a tick behind it
            if bid is not None and bid > top + 1e-9:
                bid = top
            if bid_limit is not None and bid_limit > top + 1e-9:
                bid_limit = top
            if bid is not None and bid < PMIN - 1e-9:
                bid, bid_size, bid_limit = None, 0, None
            q = replace(q, ask=ask, ask_size=size, ask_limit=min(q.ask_limit, ask) if q.ask_limit is not None else None,
                        ask_max=max(q.ask_max, size) if q.ask_max is not None else None,
                        bid=bid, bid_size=bid_size if bid is not None else 0, bid_limit=bid_limit,
                        bid_max=q.bid_max if bid is not None else None)
            price = ask
        else:
            if q.bid is None or q.bid_size < 1:
                self.mmf_recycling[ex.eid] = {**rec, "blocked": "no reducing side"}
                return q
            if getattr(cfg, "mm_recycle_sell_first", False) and self.mm_cash_low():   # P14.1 3: below half the cash
                px0 = floor_tick(fv + conc)                                           #  target a buy-back that locks
                ok, need, frees = self.buyback_net(ex.eid, ex.inv, px0, n)            #  more than it frees waits
                if ok < 1:
                    self.mmf_deferred += n
                    self.mmf_recycling[ex.eid] = {**rec, "blocked": "buy-back deferred (locks %.0f, frees %.0f, cash "
                                                                    "below half the target)" % (need, frees)}
                    return q
                if ok < n:
                    self.mmf_deferred += n - ok
                    n, rec["qty"] = ok, ok
            px = floor_tick(fv + conc)
            if best_ask is not None:
                px = min(px, floor_tick(best_ask - TICK))           # never crossing the best other ask
            if value:
                fb = value_side_prices(vp, ex.inv, cfg)[0]
                px = min(px, fb if fb is not None else px)          # never above the value floor (a short's)
            if px < PMIN - 1e-9:
                self.mmf_recycling[ex.eid] = {**rec, "blocked": "no grid price"}
                return q
            bid = max(q.bid, px)
            size = max(q.bid_size, min(n, int(-ex.inv)))
            ask, ask_size, ask_limit = q.ask, q.ask_size, q.ask_limit
            low = ceil_tick(bid + TICK)                             # our adding ask a tick behind it
            if ask is not None and ask < low - 1e-9:
                ask = low
            if ask_limit is not None and ask_limit < low - 1e-9:
                ask_limit = low
            if ask is not None and ask > PMAX + 1e-9:
                ask, ask_size, ask_limit = None, 0, None
            q = replace(q, bid=bid, bid_size=size, bid_limit=max(q.bid_limit, bid) if q.bid_limit is not None else None,
                        bid_max=max(q.bid_max, size) if q.bid_max is not None else None,
                        ask=ask, ask_size=ask_size if ask is not None else 0, ask_limit=ask_limit,
                        ask_max=q.ask_max if ask is not None else None)
            price = bid
        rec["price"] = round(price, 3)
        self.mmf_recycling[ex.eid] = rec
        key = (rec["side"], rec["price"], n)
        if self.mmf_logged.get(ex.eid) != key:
            self.mmf_logged[ex.eid] = key
            log.warning("MM RECYCLE %s %s %d @ %.3f (fair %.3f %s %.3f concession%s; MM inventory %+.0f, oldest "
                        "%.1f h, $%.0f; stale by %s)", ex.label, "sell" if ex.inv > 0 else "buy back", n, price, fv,
                        "-" if ex.inv > 0 else "+", conc, ", value floor" if value else "", v["q"], v["oldest_h"],
                        v["usd"], v["why"])
        return q

    def mm_hold_quote(self, ex, q, now_m):
        """P14.1 1 (alloc_cancel_mm_first; decide, after the recycler): while an allocator sale in this market is not
        in the positions read yet (mm_sale_lagging: the same guard the planner uses), the side that REDUCES the
        position stays off - the quoter must not re-offer shares the IOC just sold (an ask beyond the YES held is a NO
        purchase; a bid on a short re-buys what was bought back). The adding side is untouched."""
        if not self.mm_sale_lagging(ex.eid, ex.inv, now_m):
            self.mmf_hold.pop(ex.eid, None)
            return q
        self.mmf_recycling.pop(ex.eid, None)      # (no recycle line for a market whose sale is still in flight)
        if ex.inv >= 1 and q.ask is not None:
            return replace(q, ask=None, ask_size=0, ask_limit=None, ask_max=None)
        if ex.inv <= -1 and q.bid is not None:
            return replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        return q

    def mm_sale_lagging(self, e, q, now_m):
        """RT13-3: a refill IOC sold in e and the positions read q does not show it yet (within MM_SENT_LAG)."""
        x = self.mmf_sent.get(e)
        if x is None:
            return False
        t, before, sold = x
        if now_m - t > self.MM_SENT_LAG or abs(q) <= before - sold + 0.5:
            self.mmf_sent.pop(e, None)
            return False
        return True

    def mm_floor_ok(self, long, px, p):
        """A refill sale at px at or beyond the value floor: a long's >= p - value_sell_margin, a short's buy-back
        <= p + margin. P14.1 alloc_refill_max_cost > 0: while free cash is below half alloc_mm_reserve, the margin is
        max(value_sell_margin, alloc_refill_max_cost) (refill sales only: this check is theirs alone)."""
        m = self.cfg.value_sell_margin
        cost = float(getattr(self.cfg, "alloc_refill_max_cost", 0.0) or 0.0)
        if cost > m and self.mm_cash_low():
            m = cost
        return px >= p - m - 1e-9 if long else px <= p + m + 1e-9

    # ------------------------------------------------------------------------------ P14.2 helpers
    P142_LOG_H = 24.0             # the 24-h window of alloc.swaps (counts, $ and EV)
    P142_FINAL = ("done", "buy_failed", "not_sold")

    def p142_on(self, cfg=None):
        """P14.2 in effect: alloc_swap_sell_margin > 0 (the swap floor, the swap hurdle and the alloc.swaps report)."""
        cfg = cfg or self.cfg
        return float(getattr(cfg, "alloc_swap_sell_margin", 0.0) or 0.0) > 0

    def swap_floor_ok(self, long, px, p):
        """P14.2: a SWAP sale at px within alloc_swap_sell_margin of p - a long's >= p - it, a short's buy-back <= p +
        it (the swaps' own floor; every other reducing path keeps value_sell_margin)."""
        m = float(getattr(self.cfg, "alloc_swap_sell_margin", 0.0) or 0.0)
        return px >= p - m - 1e-9 if long else px <= p + m + 1e-9

    def swap_hurdle(self):
        """P14.2: a swap's min gain per $ (buy edge - sale edge-held, both at the touch): the stricter of
        alloc_min_improvement and alloc_swap_min_gain. The sale's cost IS its edge-held at the sale price: not added
        again."""
        cfg = self.cfg
        return max(cfg.alloc_min_improvement, float(getattr(cfg, "alloc_swap_min_gain", 0.0) or 0.0))

    @staticmethod
    def swap_cost(kind, qty, px, p):
        """(cost per $ freed, $ of EV given up) of selling qty of a holding at px: a long (p - px) / px and qty x
        (p - px); a short's buy-back (px - p) / (1 - px) and qty x (px - p). Negative = sold above p."""
        if kind == "long":
            return (p - px) / max(px, TICK), qty * (p - px)
        return (px - p) / max(1 - px, TICK), qty * (px - p)

    def swap_record(self, s, p_s, b, p_b, usd):
        """P14.2: the record of a planned swap (alloc.swaps.last_run, the journal): sold {label, kind, qty, price, p,
        edge, cost, cost_usd}, bought {label, short, qty, price, p, edge, usd}, gain_est (= usd x (buy edge - sale
        edge-held), net of both touches), gain_realised (None until the legs trade), status."""
        c, cu = self.swap_cost(s["kind"], s["qty"], s["px"], p_s)
        return {"sold": {"label": s["label"], "kind": s["kind"], "qty": s["qty"], "price": round(s["px"], 3),
                         "p": round(p_s, 4), "edge": round(s["edge"], 4), "cost": round(c, 4),
                         "cost_usd": round(cu, 2)},
                "bought": {"label": b["label"], "short": b["short"], "qty": b["qty"], "price": round(b["px"], 3),
                           "p": round(p_b, 4), "edge": round(b["edge"], 4), "usd": round(usd, 2)},
                "gain_est": round(usd * (b["edge"] - s["edge"]), 2), "gain_realised": None, "status": "planned"}

    def swap_line(self, r, when):
        """"ALLOC SWAP sold <label> q @ price (p, edge-held s%, cost c%) -> buy <label> q @ price (p, edge b%): net EV
        gain +$x (per $ y%)" - when "planned" (the estimate) or "done" (the fills, realised at p)."""
        sd, bt = r["sold"], r["bought"]
        g = r["gain_est"] if when == "planned" else (r["gain_realised"] or 0.0)
        per = g / bt["usd"] if bt["usd"] else 0.0
        return (f"ALLOC SWAP sold {sd['label']} {'NO ' if sd['kind'] == 'short' else ''}{sd['qty']:.0f} @ "
                f"{sd['price']:.3f} (p {sd['p']:.3f}, edge-held {100 * sd['edge']:.1f}%, cost {100 * sd['cost']:.1f}% "
                f"= ${sd['cost_usd']:,.2f}) -> buy {bt['label']} {'short YES ' if bt['short'] else ''}{bt['qty']:.0f} "
                f"@ {bt['price']:.3f} (p {bt['p']:.3f}, edge {100 * bt['edge']:.1f}%): net EV gain "
                f"{'+' if g >= 0 else '-'}${abs(g):,.2f} (per $ {100 * per:.1f}%)"
                + (" - planned" if when == "planned" else " - done, realised at p"))

    def swap_log(self, kind, usd, est=0.0, real=0.0, now_w=None):
        """One event in alloc.swaps' 24-h log: "plan" (usd, est), "done" / "buy_failed" (usd, real), "not_sold"."""
        now_w = util.time.time() if now_w is None else now_w
        lg = self.__dict__.setdefault("p142_events", deque())
        lg.append((now_w, kind, round(float(usd), 2), round(float(est), 2), round(float(real), 2)))
        while lg and lg[0][0] < now_w - self.P142_LOG_H * 3600:
            lg.popleft()

    def swap_plan_log(self, pairs, now_w):
        """P14.2 (alloc_tick, a new run): this run's swaps become alloc.swaps.last_run, each journaled."""
        self.p142_last_run = [pr["swap"] for pr in pairs if pr.get("swap") is not None]
        for r in self.p142_last_run:
            log.warning("%s", self.swap_line(r, "planned"))
            self.swap_log("plan", r["bought"]["usd"], est=r["gain_est"], now_w=now_w)

    def swap_sold(self, r, done, px, p, usd):
        """P14.2: the swap's sale traded (done shares at px; p at the fill): the record holds the sale as traded."""
        c, cu = self.swap_cost(r["sold"]["kind"], done, px, p)
        r["sold"].update(qty=float(done), price=round(px, 3), p=round(p, 4), cost=round(c, 4), cost_usd=round(cu, 2),
                         edge=round(c, 4), usd=round(usd, 2))
        r["real_sell"] = round(-cu, 2)
        r["status"] = "sold"

    def swap_bought(self, r, done, px, p, edge, usd, now_w):
        """P14.2: the swap's buy traded: realised gain = the buy's (p - price) x shares less the sale's given-up EV,
        both at p at their fills; journaled again."""
        short = r["bought"]["short"]
        got = done * ((px - p) if short else (p - px))
        r["bought"].update(qty=float(done), price=round(px, 3), p=round(p, 4), edge=round(edge, 4), usd=round(usd, 2))
        r["gain_realised"] = round(got + r.get("real_sell", 0.0), 2)
        r["status"] = "done"
        log.warning("%s", self.swap_line(r, "done"))
        self.swap_log("done", usd, real=r["gain_realised"], now_w=now_w)

    def swap_close(self, pr, now_w, why=None):
        """P14.2: a swap's pair has finished: its buy not done after its sale -> "buy_failed" (the sale stands as
        traded: an IOC leaves nothing to reprice, the cash stays in the reserve, the quotes keep value_sell_margin);
        never sold -> "not_sold"."""
        r = pr.get("swap")
        if r is None or r["status"] in self.P142_FINAL:
            return
        why = why or pr["status"]
        if r["status"] == "sold":
            r["status"], r["gain_realised"] = "buy_failed", r.get("real_sell", 0.0)
            log.warning("ALLOC SWAP sold %s %.0f @ %.3f (p %.3f) -> buy %s NOT bought (%s): the sale stands as traded "
                        "(nothing to reprice), its cash stays; EV given up %+.2f", r["sold"]["label"], r["sold"]["qty"],
                        r["sold"]["price"], r["sold"]["p"], r["bought"]["label"], why, r["gain_realised"])
            self.swap_log("buy_failed", r["sold"].get("usd", 0.0), real=r["gain_realised"], now_w=now_w)
        else:
            r["status"], r["gain_realised"] = "not_sold", 0.0
            log.info("ALLOC SWAP %s -> %s not done: the sale never went (%s)", r["sold"]["label"],
                     r["bought"]["label"], why)
            self.swap_log("not_sold", 0.0, now_w=now_w)
        r["why"] = why

    def swap_report(self, now_w=None):
        """status.json alloc.swaps (P14.2 on): the latest run's swaps, the 24-h counts, $ moved and EV gain estimated
        (planned) vs realised (done + buy-failed legs, at p), and the restart log."""
        now_w = util.time.time() if now_w is None else now_w
        ev = [x for x in getattr(self, "p142_events", ()) if x[0] >= now_w - self.P142_LOG_H * 3600]
        cnt = {k: sum(1 for x in ev if x[1] == k) for k in ("plan", "done", "buy_failed", "not_sold")}
        return {"last_run": [dict(r) for r in getattr(self, "p142_last_run", [])],
                "counts_24h": {"planned": cnt["plan"], "done": cnt["done"], "buy_failed": cnt["buy_failed"],
                               "not_sold": cnt["not_sold"]},
                "usd_24h": round(sum(x[2] for x in ev if x[1] == "done"), 2),
                "ev_gain_est_24h": round(sum(x[3] for x in ev if x[1] == "plan"), 2),
                "ev_gain_realised_24h": round(sum(x[4] for x in ev if x[1] in ("done", "buy_failed")), 2),
                "margin": self.cfg.alloc_swap_sell_margin, "hurdle": round(self.swap_hurdle(), 4),
                "events": [list(x) for x in ev]}

    # ------------------------------------------------------------------------------ P14.1 helpers
    P141_FLAGS = ("alloc_cancel_mm_first", "alloc_rank_all_markets", "mm_recycle_sell_first",
                  "alloc_refill_ignore_prefer_short", "alloc_swap_room_netting")
    P141_LOG_H = 24.0             # the 24-h report window (refill $ / EV given up, swaps planned / done / EV gain)

    def p141_on(self, cfg=None):
        """Any P14.1 setting on (the alloc report keys and the summary piece follow it)."""
        cfg = cfg or self.cfg
        return (any(bool(getattr(cfg, k, False)) for k in self.P141_FLAGS)
                or float(getattr(cfg, "alloc_refill_max_cost", 0.0) or 0.0) > 0)

    def p141(self, name):
        """A P14.1 refill flag in effect: the flag AND mm_refill_fast (they change the fast refill; alone: nothing)."""
        return bool(getattr(self.cfg, name, False)) and bool(getattr(self.cfg, "mm_refill_fast", False))

    def mm_cash_low(self):
        """Free cash (the gate's, read) below half of alloc_mm_reserve (> 0)."""
        res = float(getattr(self.cfg, "alloc_mm_reserve", 0.0) or 0.0)
        return (res > 0 and getattr(self, "cg_cash", None) is not None
                and self.cash_left() + self.hv_carve_used() < 0.5 * res - 1e-9)   # (P15: the carve-out counts)

    def p141_log(self, kind, usd, est=0.0, real=0.0, now_w=None):
        """One event in the 24-h report log: kind "refill" (a refill sale: usd, real = the EV given up, <= 0 when below
        p), "swap_plan" (a planned pair: usd, est), "swap_sell" / "swap_buy" (a pair's legs filled: usd, real).
        Nothing is logged while every P14.1 setting is off (the report keys are absent then)."""
        if not self.p141_on():
            return
        now_w = util.time.time() if now_w is None else now_w
        lg = self.__dict__.setdefault("p141_events", deque())
        lg.append((now_w, kind, round(float(usd), 2), round(float(est), 2), round(float(real), 2)))
        while lg and lg[0][0] < now_w - self.P141_LOG_H * 3600:
            lg.popleft()

    def p141_sums(self, now_w=None):
        """{kind: [count, usd, est, real]} over the last 24 h of p141_log."""
        now_w = util.time.time() if now_w is None else now_w
        out = {}
        for t, kind, usd, est, real in getattr(self, "p141_events", ()):
            if t >= now_w - self.P141_LOG_H * 3600:
                s = out.setdefault(kind, [0, 0.0, 0.0, 0.0])
                s[0], s[1], s[2], s[3] = s[0] + 1, s[1] + usd, s[2] + est, s[3] + real
        return out

    def alloc_rooms(self, inv):
        """P14.1 alloc_swap_room_netting: (room_wc, room_corr) the positions inv would leave, measured as the cycle
        measures them (mm_risk_room_update; this cycle's fair values alloc_fvs and account), or None if unknown."""
        fvs, eq = getattr(self, "alloc_fvs", None), getattr(self, "last_equity", None)
        if fvs is None:                           # before this cycle's step 6 (the file just landed): last values
            fvs = {e: ex.last_fv for e, ex in self.ex.items()}
        if not eq:
            return None
        cfg = self.cfg
        worst = self.total_worst_case(inv, fvs)
        if cfg.risk_model == "correlated":
            pd = sum(PARTY_SIGN.get(ex.party, 0) * inv.get(eid, 0.0) for eid, ex in self.ex.items())
            risk = min(worst, self.settlement_risk(inv, fvs, pd))
        else:
            risk = worst
        return cfg.worst_case_backstop_frac * eq - worst, cfg.max_worst_case_frac * eq - risk

    def alloc_room_ok(self, after, floor):
        """after (room_wc, room_corr) >= floor (per room, only where its reserve > 0)."""
        cfg = self.cfg
        return all(res <= 0 or a >= f - 1e-6 for a, f, res in zip(after, floor, (cfg.mm_risk_reserve_wc,
                                                                                 cfg.mm_risk_reserve_corr)))

    def alloc_room_floor(self, now=None):
        """The rooms a swap may not go below: min(the room now, its reserve) each (now = self.mmr_room)."""
        cfg = self.cfg
        rw, rc = self.mmr_room if now is None else now
        return (min(rw if rw is not None else 0.0, cfg.mm_risk_reserve_wc),
                min(rc if rc is not None else 0.0, cfg.mm_risk_reserve_corr))

    def alloc_buy_room_ok(self, b, q, inv, pr):
        """P14.1 5: the rooms this netted swap's buy would leave are still at or above the pair's floor."""
        hyp = {e: float(v) for e, v in inv.items()}
        hyp[b["eid"]] = q + (-b["qty"] if b["short"] else b["qty"])
        after = self.alloc_rooms(hyp)
        if after is not None and self.alloc_room_ok(after, tuple(pr.get("rfloor") or self.alloc_room_floor())):
            return True
        self.alloc_block("mm_risk_reserve")
        self.mm_risk_count("alloc")
        log.info("ALLOC buy %s held back: the swap's room check (mm_risk_reserve net of its own sale) no longer "
                 "passes", b["label"])
        return False

    def alloc_netting(self):
        """P14.1 5 in effect now: the flag, value adds paused, rooms measurable, not in reduce-only."""
        return (bool(getattr(self.cfg, "alloc_swap_room_netting", False)) and getattr(self, "mmr_paused", False)
                and not getattr(self, "global_reduce", False)
                and bool(getattr(self, "last_equity", None)) and None not in tuple(self.mmr_room))

    def buyback_net(self, e, q, px, n):
        """P14.1 3: (shares of a buy-back of n at YES px on a short q whose gate need is <= the cash its fill frees,
        gross need of all n, cash all n free). Need per share by the gate's own tiers (covered lone NO 0, NO+NO set
        part 1.0, an uncovered YES bid px), freed (1 - px) a share."""
        free = self.cash_free(e, skip=lambda o: True)           # (the recycler's bid replaces ours there)
        tiers = self.cash_tiers(free, True, px, bool(getattr(self.cfg, "reduce_no_as_sell", False)) and q <= -1)
        ok, left = 0.0, float(n)
        for amt, cost in tiers:
            take = min(left, amt)
            if cost <= (1 - px) + 1e-9:
                ok += take
            else:
                break
            left -= take
            if left <= 1e-9:
                break
        return int(ok + 1e-9), self.tier_need(tiers, n), n * (1 - px)

    def mm_refill_held(self, inv, now_m, skip, pins, blocked):
        """P14 2 (mm_refill_fast, alloc_plan): the stale MM inventory as refill holdings sold FIRST ("mm": True):
        an IOC at the best bid (a short: the best ask, as a covered NO sale) when the gap to fair (ex.last_fv, else
        p) is <= mm_recycle_concession and the price is at or beyond the value floor; else it rests through the
        recycler (blocked_by "mm_resting"). Value inventory (edge-held >= mm_hurdle) is not MM here.
        P14.1 1 (alloc_cancel_mm_first): the concession gate goes - a stale MM holding is a candidate whenever the
        refill's own price rule (mm_floor_ok) allows it, since the sale cancels our quotes there first (alloc_send) and
        the quoting side is then held until the positions read shows it (mm_hold_side); a refused one counts "floor"."""
        cfg, out = self.cfg, []
        first = self.p141("alloc_cancel_mm_first")
        for e, v in sorted(self.mm_view(util.time.time()).items()):
            ex, q = self.ex.get(e), float(inv.get(e, 0.0))
            if (ex is None or abs(q) < 1 or v["stale"] < 1 or v["q"] * q <= 0 or not self.alloc_market_ok(ex, skip)
                    or ex.label in pins):
                continue
            if self.mm_sale_lagging(e, q, now_m):
                blocked["in_flight"] += 1
                continue
            p = self.alloc_p(ex, now_m)
            book = self.alloc_fresh_book(ex, now_m) or (ex.book if self.p141("alloc_rank_all_markets") else None)
            fair = ex.last_fv if ex.last_fv is not None else p
            if p is None or book is None or fair is None:
                continue
            edge = self.alloc_edge_held(ex, q, now_m)
            if edge is not None and edge >= self.mm_hurdle() - 1e-9:
                continue                                  # value: the lowest-edge value positions refill instead
            key = "bids" if q > 0 else "asks"
            if not book.get(key):
                continue
            px, depth = book[key][0]["price"], book[key][0]["quantity"]
            gap = fair - px if q > 0 else px - fair
            if not first:                             # 4ff7d91: near fair AND the floor, else it rests (mm_resting)
                if gap > cfg.mm_recycle_concession + 1e-9 or not self.mm_floor_ok(q > 0, px, p):
                    blocked["mm_resting"] += 1
                    continue
            elif not self.mm_floor_ok(q > 0, px, p):  # P14.1 1: the refill's own price rule alone
                blocked["floor"] += 1
                continue
            n = min(v["stale"], depth, abs(q) if q > 0 else self.alloc_lone_no(e, q))
            unit = px if q > 0 else 1 - px
            n = int(n + 1e-9)
            if n < 1 or n * unit < self.ALLOC_MIN_USD:
                continue
            out.append({"kind": "long" if q > 0 else "short", "eid": e, "label": ex.label, "px": px,
                        "edge": edge if edge is not None else 0.0, "unit": unit, "avail": n * unit, "mm": True,
                        "floor_ok": True})
        return out

    def mm_refill_tick(self, inv, now_m, now, skip, turnover):
        """P14 2 (alloc_tick, live, no run in flight, cash read fresh): free cash below alloc_mm_reserve -> a
        refill-only plan now (alloc_plan refill_only: MM inventory first, then the lowest edge-held, the floor),
        executed by alloc_tick's own sale step this cycle (writes / turnover as any run). The hourly clock is not
        moved; the plan's pairs expire after MM_FAST_EXPIRE if never sent."""
        cfg = self.cfg
        cash = self.cash_left()
        self.mmf_refill_last_m = now_m
        more = self.p141("alloc_rank_all_markets") and bool(self.alloc_pairs)   # P14.1 2: added to a run in flight
        if more:                                  # (never a second order in a market a pending pair already uses)
            skip = set(skip) | {pr["sell"].get("eid") for pr in self.alloc_pairs if pr["sell"].get("eid")}
            skip |= {pr["buy"]["eid"] for pr in self.alloc_pairs if pr.get("buy")}
            skip |= {m for pr in self.alloc_pairs for m in (pr["sell"].get("members") or ())}
        pairs, info = self.alloc_plan(inv, now_m, cash, skip, cfg.alloc_max_turnover_per_hour - turnover,
                                      refill_only=True)
        if not pairs:
            self.mmf_refill["blocked_by"] = dict(info["blocked_by"])
            return
        for pr in pairs:
            pr["planned_at"], pr["fast"] = now_m, True
        if more:
            self.alloc_pairs = self.alloc_pairs + pairs
            self.alloc_run["blocked_by"] = dict(info["blocked_by"])
            self.alloc_run["pairs_planned"] = self.alloc_run.get("pairs_planned", 0) + len(pairs)
        else:
            self.alloc_pairs, self.alloc_sells_stopped = pairs, False
            self.alloc_run = {"blocked_by": dict(info["blocked_by"]), "pairs_planned": len(pairs), "sold": 0.0,
                              "bought": 0.0, "cash_before": round(cash, 2), "ev_gain_est": 0.0, "fast_refill": True}
        self.mmf_refill["runs"] += 1
        self.mmf_refill["last"] = iso(now)
        self.mmf_refill["blocked_by"] = dict(info["blocked_by"])
        log.warning("ALLOC fast refill (mm_refill_fast): %d sale(s) planned (cash %.0f < reserve %.0f, turnover left "
                    "%.0f)", len(pairs), cash, cfg.alloc_mm_reserve, cfg.alloc_max_turnover_per_hour - turnover)
        for pr in pairs:
            log.warning("%s", self.alloc_journal(pr))

    def mm_room_part(self, inv, fvs, worst, risk):
        """P14 3 (mm_room_guard): (worst-case, correlated) contribution of the MM lots = the cycle's risk measures
        minus the same measures on the positions without the MM shares (>= 0), or None on an error."""
        try:
            mmq = {e: sum(x[0] for x in lots) for e, lots in self.mm_lots.items()}
            if not any(abs(v) >= 1 for v in mmq.values()):
                return 0.0, 0.0
            inv_v = {e: float(q) - mmq.get(e, 0.0) for e, q in inv.items()}
            worst_v = self.total_worst_case(inv_v, fvs)
            if self.cfg.risk_model == "correlated":
                pd_v = sum(PARTY_SIGN.get(ex.party, 0) * inv_v.get(eid, 0.0) for eid, ex in self.ex.items())
                risk_v = min(worst_v, self.settlement_risk(inv_v, fvs, pd_v))
            else:
                risk_v = worst_v
            return max(0.0, worst - worst_v), max(0.0, risk - risk_v)
        except Exception as err:
            self.mmf_warn("room guard", err)
            return None

    def mm_funding_below(self):
        """What is below half its target now: ["cash 4,100 of 20,000", "risk room wc ..."] ([] = funded)."""
        cfg, out = self.cfg, []
        cash = self.cash_left() if getattr(self, "cg_cash", None) is not None else None
        if cash is not None:
            cash += self.hv_carve_used()          # P15: the ladder's resting carve-out is part of the reserve
        if cfg.alloc_mm_reserve > 0 and cash is not None and cash < 0.5 * cfg.alloc_mm_reserve - 1e-9:
            out.append(f"cash {cash:,.0f} of {cfg.alloc_mm_reserve:,.0f}")
        rw, rc = self.mmr_room
        for name, room, res in (("wc", rw, cfg.mm_risk_reserve_wc), ("corr", rc, cfg.mm_risk_reserve_corr)):
            if res > 0 and room is not None and room < 0.5 * res - 1e-9:
                out.append(f"risk room {name} {room:,.0f} of {res:,.0f}")
        return out

    def mm_funding_tick(self):
        """P14 4 (end of every cycle): the below-half clock (since when cash or a room has been below half its
        target; cleared, and the alert re-armed, once all are back) and, with a P14 flag on, ONE alert after
        mm_funding_alert_h hours below. Never raises."""
        try:
            now, below = util.time.time(), self.mm_funding_below()
            if below:
                if self.mmf_below_since is None:
                    self.mmf_below_since = now
            elif self.mmf_below_since is not None:
                if self.mmf_alerted:
                    log.warning("MM funding back above half of its targets (alert re-armed)")
                self.mmf_below_since, self.mmf_alerted = None, False
            hours = (now - self.mmf_below_since) / 3600 if self.mmf_below_since is not None else 0.0
            if below and self.mmf_on() and not self.mmf_alerted and hours > self.cfg.mm_funding_alert_h:
                self.mmf_alerted = True
                util.alert(f"MM funding below half for {hours:.1f} h: {'; '.join(below)} (market making is short of "
                      f"cash / risk room)")
        except Exception as err:
            self.mmf_warn("alert check", err)

    def mm_funding_fields(self):
        """status.json mm_funding (read-only; also what a restart restores: lots, handed_to_value, refill, the alert
        clock)."""
        cfg, now = self.cfg, util.time.time()
        view = self.mm_view(now)
        rnd2 = lambda x: None if x is None else round(x, 2)   # noqa: E731
        cash = self.cash_left() if getattr(self, "cg_cash", None) is not None else None
        rw, rc = self.mmr_room
        out = {"cash_free": rnd2(cash), "cash_target": cfg.alloc_mm_reserve,
               "room_free": {"wc": rnd2(rw), "corr": rnd2(rc)},
               "room_target": {"wc": cfg.mm_risk_reserve_wc, "corr": cfg.mm_risk_reserve_corr},
               "inventory_usd": round(sum(v["usd"] for v in view.values()), 2),
               "inventory_markets": len(view),
               "oldest_inventory_h": round(max(v["oldest_h"] for v in view.values()), 2) if view else None,
               "stale_markets": sum(1 for v in view.values() if v["stale"] >= 1),
               "stale_usd": round(sum(v["usd"] * v["stale"] / max(1.0, abs(v["q"])) for v in view.values()), 2),
               "recycling": {self.ex[e].label: dict(r) for e, r in sorted(self.mmf_recycling.items()) if e in self.ex},
               "handed_to_value": dict(self.mmf_handed),
               "below_half": self.mm_funding_below(),
               "below_half_since": (iso(datetime.fromtimestamp(self.mmf_below_since, timezone.utc))
                                    if self.mmf_below_since is not None else None),
               "below_half_since_wall": self.mmf_below_since, "alerted": self.mmf_alerted,
               "refill": dict(self.mmf_refill),
               # P14.1 7: the refill report (flat keys beside "refill", which a restart restores)
               "refill_runs": self.mmf_refill["runs"], "refill_last": self.mmf_refill["last"],
               "refill_sales_24h": (self.p141_sums(now).get("refill") or [0])[0],   # (hourly refills too)
               "refill_sold_usd": round((self.p141_sums(now).get("refill") or [0, 0.0])[1], 2),
               "refill_ev_given_24h": round(-(self.p141_sums(now).get("refill") or [0, 0.0, 0.0, 0.0])[3], 2),
               "deferred_buybacks": self.mmf_deferred, "refill_holds": len(self.mmf_hold),
               "cash_locked": round(sum(self.resting_lock(o) for o in list(self.my_orders.values())), 2),
               "events": [list(x) for x in self.p141_events],
               "flags": {"recycle": bool(getattr(cfg, "mm_recycle_enabled", False)),
                         "refill_fast": bool(getattr(cfg, "mm_refill_fast", False)),
                         "room_guard": bool(getattr(cfg, "mm_room_guard", False)),
                         **({k: bool(getattr(cfg, k, False)) for k in self.P141_FLAGS}   # P14.1 (absent while off)
                            if self.p141_on() else {})},
               "lots_source": self.mmf_seed,
               "lots": {e: [[round(x[0], 2), round(x[1], 4), round(x[2], 1)] for x in v]
                        for e, v in sorted(self.mm_lots.items())}}
        if getattr(cfg, "tilt_harvest_ladder", False):   # P15: the reserve's split (absent while the ladder is off)
            out["mm_reserve_effective"] = round(self.mm_reserve_effective(), 2)
            out["harvest_carve"] = self.hv_carve_fields()
            out["reserve_cash"] = rnd2(None if cash is None else cash + self.hv_carve_used())
        if getattr(cfg, "mm_room_guard", False):
            mw, mc = self.mmf_room_mm
            vw, vc = self.mmf_room_value
            out["room_guard"] = {"mm_inventory": {"wc": rnd2(mw), "corr": rnd2(mc)},
                                 "value_share": {"wc": rnd2(vw), "corr": rnd2(vc)}}
        return out

    def safe_mm_funding(self):
        """mm_funding_fields that never raises: on an error the lots alone (so a restart still restores them)."""
        try:
            return self.mm_funding_fields()
        except Exception as err:                          # reporting must never disturb trading
            self.mmf_warn("status fields", err)
            return {"lots": {e: [list(x) for x in v] for e, v in getattr(self, "mm_lots", {}).items()},
                    "lots_source": getattr(self, "mmf_seed", None)}

    def mm_funding_summary(self):
        """P14: "MM funding cash Xk/Yk, room wc ..., inventory Zk (N stale, oldest H h)" for the 2-hourly summary
        while a P14 flag is on (P14.1 adds the refill / swap figures); "" otherwise. Never raises."""
        if not (self.mmf_on() or self.p141_on()):
            return ""
        try:
            f, cfg = self.mm_funding_fields(), self.cfg
            k = lambda x: "?" if x is None else f"{x / 1000:.1f}k"   # noqa: E731
            parts = [f"cash {k(f['cash_free'])}/{k(f['cash_target'])}"]
            for name in ("wc", "corr"):
                if f["room_target"][name] > 0:
                    parts.append(f"room {name} {k(f['room_free'][name])}/{k(f['room_target'][name])}")
            oldest = f["oldest_inventory_h"]
            parts.append(f"inventory {k(f['inventory_usd'])} ({f['stale_markets']} stale"
                         + (f", oldest {oldest:.1f} h" if oldest is not None else "") + ")")
            if f["handed_to_value"]["count"]:
                parts.append(f"{f['handed_to_value']['count']} handed to value")
            if self.p141_on():                    # P14.1 7: the refill / swap work of the last 24 h, one piece
                r = self.p141_alloc_report()
                parts.append(f"refill {f['refill_runs']} runs, {k(f['refill_sold_usd'])} sold"
                             + (f" ({f['refill_ev_given_24h']:.0f} EV given up)" if f["refill_ev_given_24h"] else "")
                             + (f", {f['deferred_buybacks']} buy-back shares deferred" if f["deferred_buybacks"]
                                else ""))
                parts.append(f"swaps {r['swaps_planned_24h']} planned / {r['swaps_done_24h']} done, "
                             f"{k(r['swaps_usd_24h'])} moved, EV +{r['ev_gain_est_24h']:.0f} est / "
                             f"{r['ev_gain_realised_24h']:+.0f} realised")
            line = "MM funding " + ", ".join(parts)
            if f["below_half_since"]:
                line += f" BELOW HALF since {f['below_half_since'][11:16]}"
            return line
        except Exception as err:
            self.mmf_warn("summary", err)
            return ""
