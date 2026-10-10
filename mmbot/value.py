"""
ValueMixin: the allocator (alloc_*: plan, buy, sell, tick, journal), the set ladder (sl_*) and the
value-floor helpers.

Methods only: all state lives on the Bot instance (self); no __init__ here. Never imports bot.py.
"""
import json
import math
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mmbot import util
from mmbot import config
from mmbot import exchange
from mmbot import pricing
from mmbot.util import FATAL_API_CODES, PMAX, PMIN, TICK, bot_path, iso, log, rnd
from mmbot.config import LADDER_MAX_LEVELS, MAX_ORDER_TTL
from mmbot.exchange import ApiError, busy
from mmbot.pricing import floor_tick, strip_own


class ValueMixin:

    ALLOC_REF_MAX_AGE = 30.0      # a Polymarket price downloaded longer ago than this (s) is not ranked
    ALLOC_LEVEL_TOL = 0.005       # a paired level still counts while the fresh touch is within this of it
    ALLOC_BUY_WAIT = 900.0        # a sold pair's buy waits at most this long (s) for a cash read showing the money
    ALLOC_SET_WAIT = 900.0        # a registered NO+NO set unwind waits at most this long (s) for take_arbitrage
    ALLOC_MIN_USD = 20.0          # no pair or reserve refill below this many $
    ALLOC_CASH_FRESH = 300.0      # no allocator action without a cash read younger than this (the gate's own limit)
    ALLOC_DONE = ("bought", "done", "gone", "dropped", "expired", "skipped")

    def load_status_key(self, key):
        """Package 10 B: status.json[key] the previous run left ({} if none)."""
        try:
            with open(bot_path(self.cfg.status_file)) as f:
                d = json.load(f).get(key)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def alloc_init(self, d=None):
        """Package 10 B state, restored from status.json "alloc" (d): the last run's wall time (the hourly clock) and
        the $ rotated in the last hour (turnover cap). Pairs in flight are NOT restored: after a restart their cash
        simply stays (never a buy without its own fresh sale and cash read)."""
        d = d if isinstance(d, dict) else {}

        def num(x, default=None):
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else default
        self.alloc_last_run_wall = num(d.get("last_run_wall"))
        self.alloc_flows = deque()                # (wall, $ rotated) of the last hour
        for x in (d.get("flows") if isinstance(d.get("flows"), list) else ()):
            if isinstance(x, (list, tuple)) and len(x) == 2 and num(x[0]) is not None and num(x[1]) is not None:
                self.alloc_flows.append((num(x[0]), num(x[1])))
        self.alloc_totals = {k: num(d.get(k), 0.0) for k in ("sold_total", "bought_total", "runs_total")}
        self.alloc_pairs = []                     # this run's pairs (dicts, see alloc_plan), until each is finished
        self.alloc_set_races = {}                 # race -> {"cost", "until", "sets_before", "free"} (B3; arb_plan)
        self.alloc_sells_stopped = False          # a sold pair's level was gone: no more sales this run
        self.alloc_state = "off"
        self.alloc_info = {}                      # the latest tick's figures (status.json "alloc")
        self.alloc_run = {}                       # this run's tallies
        self.alloc_bloc_logged = False
        self.alloc_ages = {}
        self.alloc_ladder = {}                    # Package 12 L1: race -> the resting rich-leg ladder's state
        self.alloc_ladder_info = {}               # its status (alloc.set_ladder), absent while never used
        self.alloc_ladder_refused = {}            # race -> when the exchange last refused its whole ladder (RT12-3)
        self.alloc_targets = {}                   # Package 12 M2: the latest plan's intended holdings {eid: shares}
        self.p142_last_run = []                   # P14.2: the latest run's swap records (alloc.swaps.last_run)
        self.p142_events = deque()                # P14.2: (wall, kind, $, est, realised) of the last 24 h
        sw = d.get("swaps") if isinstance(d.get("swaps"), dict) else {}
        for x in (sw.get("events") if isinstance(sw.get("events"), list) else ()):   # (restored after a restart)
            if (isinstance(x, (list, tuple)) and len(x) == 5 and isinstance(x[1], str)
                    and all(num(x[i]) is not None for i in (0, 2, 3, 4))):
                self.p142_events.append((num(x[0]), x[1], num(x[2]), num(x[3]), num(x[4])))

    def alloc_persist_needed(self):
        return bool(self.alloc_info) or bool(self.alloc_pairs) or self.alloc_last_run_wall is not None

    def p141_alloc_report(self, now_w=None):
        """P14.1 7: the allocator's swap report (absent while every P14.1 setting is off): swaps planned / done in the
        last 24 h, the $ moved, the EV gain estimated when planned vs realised from the IOC fills (a sale's (price - p)
        given up counts against the buy's (p - price) gained), and the latest run's refill blockers."""
        if not self.p141_on():
            return {}
        s = self.p141_sums(now_w)
        pl, sl, bl, rf = (s.get(k) or [0, 0.0, 0.0, 0.0] for k in ("swap_plan", "swap_sell", "swap_buy", "refill"))
        return {"swaps_planned": self.alloc_run.get("swaps_planned", 0),
                "swaps_planned_24h": pl[0], "swaps_usd_planned_24h": round(pl[1], 2),
                "swaps_done_24h": bl[0], "swaps_usd_24h": round(bl[1], 2),
                "ev_gain_est_24h": round(pl[2], 2), "ev_gain_realised_24h": round(bl[3] + sl[3], 2),
                "refill_sold_usd_24h": round(rf[1], 2), "refill_ev_given_24h": round(-rf[3], 2),
                "refill_blocked_by": dict(self.mmf_refill.get("blocked_by") or {})}

    def alloc_status(self):
        """status.json "alloc": the latest figures plus what a restart restores (last_run_wall, flows). P14.1: the
        swap report (p141_alloc_report) while a P14.1 setting is on."""
        now_w = util.time.time()
        return {**self.alloc_info, **self.p141_alloc_report(now_w),
                **({"swaps": self.swap_report(now_w)} if self.p142_on() else {}),   # P14.2
                "state": self.alloc_state, "last_run_wall": self.alloc_last_run_wall,
                "last_run": (iso(datetime.fromtimestamp(self.alloc_last_run_wall, timezone.utc))
                             if self.alloc_last_run_wall is not None else None),
                **({"set_ladder": dict(self.alloc_ladder_info)} if self.alloc_ladder_info else {}),   # P12 L1
                "pending": len(self.alloc_pairs), "turnover_hour": round(self.alloc_turnover(now_w), 2),
                "flows": [list(x) for x in self.alloc_flows if now_w - x[0] < 3600],
                **{k: round(v, 2) for k, v in self.alloc_totals.items()}}

    def alloc_turnover(self, now_w):
        """$ rotated in the last hour (sales, buys from spare cash, NO+NO sets freed)."""
        while self.alloc_flows and self.alloc_flows[0][0] < now_w - 3600:
            self.alloc_flows.popleft()
        return sum(u for _, u in self.alloc_flows)

    def alloc_pins(self):
        return {x.strip() for x in str(self.cfg.alloc_pin or "").split(",") if x.strip()}

    def alloc_p(self, ex, now_m=None):
        """The outcome value of one YES share for the allocator: the race-scaled Polymarket price, only when liquid
        and downloaded within ALLOC_REF_MAX_AGE seconds (refs.ages, where the reference feed reports it); else None."""
        if ex.eid not in (self.cur_liquid or ()) or (self.cur_refs or {}).get(ex.eid) is None:
            return None
        age = (self.alloc_ages or {}).get(f"{ex.group}|{ex.party}")
        if age is not None and age > self.ALLOC_REF_MAX_AGE:
            return None
        return self.scaled_ref(ex)

    def alloc_market_ok(self, ex, skip=()):
        """A market the allocator may trade at all: not headline (unless alloc_headline), not one
        another feature traded this cycle (skip: exchanges and races), not inside the pre-close window that stops
        takes and arbitrage (close_window: at least the stop window, P10 red team RT-3)."""
        return not (ex.eid in skip or ex.group in skip
                    or ex.group in self.cfg.headline_races
                    or self.hours_to_close(ex) <= self.close_window("flatten_hours_before_close"))

    def alloc_fresh_book(self, ex, now_m):
        """The cached book (our own orders already stripped) if confirmed within book_stale, else None."""
        return ex.book if ex.book is not None and now_m - ex.verified < self.cfg.book_stale else None

    @staticmethod
    def alloc_held_usd(q, p):
        """$ at p held to the outcome in one market (a long q x p, a short |q| x (1 - p))."""
        return q * p if q > 0 else -q * (1 - p)

    def alloc_lone_no(self, e, q):
        """NO shares a short's buy-back may sell as a covered "sell NO": the NO held less its NO+NO set part (always:
        a set is only ever unwound whole, B3), within cover_no_qty (0 when covered NO sales are not in effect)."""
        if q > -1:
            return 0
        return int(max(0.0, min(-q - self.nono_set_part(e, q), self.cover_no_qty(e, q, sets_ok=True))) + 1e-9)

    def alloc_best_other_bid(self, ex, book):
        """(price, size) of the best bid on a fresh book that is another trader's (a price we bid at skipped whole,
        as arb_levels), or None."""
        mine = self.own_prices(ex.eid)
        return next(((x["price"], x["quantity"]) for x in (book or {}).get("bids") or []
                     if (True, rnd(x["price"])) not in mine), None)

    def alloc_prefer_short_legs(self, inv, now_m, skip=()):
        """Package 12 L2 (alloc_prefer_short): {eid: the other leg} - the 2-leg races' legs whose YES the allocator
        does not BUY because the race's best bids (fresh books, other traders only) sum above 1 and the other leg can
        be shorted instead at an edge >= alloc_min_edge_buy (we hold no YES there, it may be traded, p liquid, room
        under alloc_max_contract_usd): 1 - bid_other < bid_this <= ask_this, the same exposure for less cash."""
        cfg, out = self.cfg, {}
        for race, members in sorted(self.groups.items()):
            if len(members) != 2 or any(m not in self.ex for m in members) or race in skip:
                continue
            exs = [self.ex[m] for m in members]
            bids = [self.alloc_best_other_bid(x, self.alloc_fresh_book(x, now_m)) for x in exs]
            if any(b_ is None for b_ in bids) or bids[0][0] + bids[1][0] <= 1 + 1e-9:
                continue
            for i in (0, 1):
                a, o, bid = exs[i], exs[1 - i], bids[1 - i][0]
                q_o, p_o = float(inv.get(o.eid, 0.0)), self.alloc_p(o, now_m)
                if q_o > 0 or p_o is None or not self.alloc_market_ok(o, skip):
                    continue
                room = cfg.alloc_max_contract_usd - self.alloc_held_usd(q_o, p_o)
                if (bid - p_o) / max(1 - bid, TICK) >= cfg.alloc_min_edge_buy - 1e-9 and room >= self.ALLOC_MIN_USD:
                    out[a.eid] = o.eid
        return out

    @staticmethod
    def alloc_plan_targets(inv, pairs):
        """Package 12 M2: {eid: the holding the plan intends} for every market a planned pair sells or buys (a long
        sale lowers it, a short's buy-back / a set unwind raises it, a buy raises it, a short sale lowers it)."""
        out = {}
        for pr in pairs:
            s, b = pr.get("sell"), pr.get("buy")
            moves = []
            if s is not None and s.get("kind") in ("long", "short"):
                moves.append((s["eid"], -s["qty"] if s["kind"] == "long" else s["qty"]))
            if s is not None and s.get("kind") == "set":
                moves += [(m, s["qty"]) for m in s.get("members") or ()]
            if b is not None:
                moves.append((b["eid"], -b["qty"] if b["short"] else b["qty"]))
            for e, d in moves:
                out[e] = out.get(e, float(inv.get(e, 0.0))) + d
        return out

    def alloc_edge_held(self, ex, q, now_m=None):
        """Package 12 M2: the edge-held of a holding of q shares here, alloc_plan's own measure - a long (p - bid) /
        bid at the best bid, a short (ask - p) / (1 - ask) at the best ask (p = alloc_p, the book fresh, our own
        orders stripped) - or None (no position, no liquid p, no fresh book or no level on that side)."""
        now_m = util.time.monotonic() if now_m is None else now_m
        if abs(q) < 1:
            return None
        p, book = self.alloc_p(ex, now_m), self.alloc_fresh_book(ex, now_m)
        if p is None or book is None:
            return None
        if q > 0:
            lv = (book.get("bids") or [None])[0]
            return None if lv is None or lv["price"] <= 0 else (p - lv["price"]) / lv["price"]
        lv = (book.get("asks") or [None])[0]
        return None if lv is None else (lv["price"] - p) / max(1 - lv["price"], TICK)

    def alloc_target_for(self, eid, inv=None, now_m=None):
        """Package 12 M2: the holding (signed YES shares) the bot WANTS in this market, for the target-inventory
        skew: the allocator's latest plan's intended holding (alloc_enabled and the plan touched the market), else
        the current holding when its edge-held > 0 in value_mode (a +EV position held to the outcome), else 0.
        None = no such market. inv = the positions dict (None: the market's last known ex.inv)."""
        ex = self.ex.get(eid)
        if ex is None:
            return None
        cfg = self.cfg
        if cfg.alloc_enabled and eid in (getattr(self, "alloc_targets", None) or {}):
            return float(self.alloc_targets[eid])
        q = float(inv.get(eid, 0.0)) if inv is not None else float(getattr(ex, "inv", 0.0) or 0.0)
        if getattr(cfg, "value_mode", False) and abs(q) >= 1:
            edge = self.alloc_edge_held(ex, q, now_m)
            if edge is not None and edge > 0:
                return q
        return 0.0

    def skew_target_inputs(self, ex, inv, inv_for_quote, per_market, cfg=None, now_m=None):
        """Package 12 M2 (skew_target_inventory): compute_quote's (skew_inv, age_off). skew_inv = the quoted
        inventory less the target netted the same way: per market (flatten_per_market window) inv - target, else
        eff_inv - (target - mean of the race's other targets), i.e. effective_inventory of (inv - target). age_off =
        value_mode on and this holding's edge-held > 0 (no age skew on a +EV holding)."""
        cfg = cfg or self.cfg
        t = self.alloc_target_for(ex.eid, inv, now_m) or 0.0
        if not per_market:
            others = [self.alloc_target_for(o, inv, now_m) or 0.0 for o in self.groups.get(ex.group, ())
                      if o != ex.eid]
            t = t - (sum(others) / len(others) if others else 0.0)
        age_off = False
        if getattr(cfg, "value_mode", False):
            q = float(inv.get(ex.eid, 0.0))
            edge = self.alloc_edge_held(ex, q, now_m)
            age_off = edge is not None and edge > 0
        return inv_for_quote - t, age_off

    def alloc_plan(self, inv, now_m, cash, skip=(), turnover_left=None, refill_only=False):
        """THE PURE PLANNER (no request, no state change but the once-only bloc log): ([pair], {"blocked_by",
        "ev_gain_est"}). pair = {"sell": {"kind": "long" | "short" | "set" | "cash", "eid" / "race", "label", "qty",
        "px", "edge", "usd"}, "buy": None | {"eid", "label", "short", "px", "qty", "edge", "usd"}, "usd", "status"}.
        Reserve refills first (cash below alloc_mm_reserve: sales with no buy, lowest edge first), then pairs: lowest
        edge-held with highest-edge level while the gain >= alloc_min_improvement, inside alloc_max_contract_usd per
        market, the turnover left and (bloc_delta_enabled) the bloc cap.
        P14 mm_refill_fast: the refills sell the stale MM inventory first (mm_refill_held; its legs carry "mm"),
        then the lowest edge-held, every refill sale at or beyond the value floor (mm_floor_ok); a market a refill
        IOC sold whose sale the positions read does not show yet is skipped (RT13-3). refill_only: no pairs."""
        cfg = self.cfg
        blocked = defaultdict(int)
        pins = self.alloc_pins()
        cash = cash + self.hv_carve_used()        # P15: the ladder's resting carve-out is part of the reserve
        fast = bool(getattr(cfg, "mm_refill_fast", False))
        rank_all = self.p141("alloc_rank_all_markets")   # P14.1 2: every holding ranked for the refill
        swap_m = self.p142_on()                   # P14.2: swaps have their own floor and the stricter hurdle
        held, levels = [], []
        for e, q in sorted(inv.items()):
            ex = self.ex.get(e)
            if ex is None or abs(q) < 1 or not self.alloc_market_ok(ex, skip) or ex.label in pins:
                continue
            if fast and self.mm_sale_lagging(e, q, now_m):   # P14 (RT13-3): its sale not in the positions read yet
                blocked["in_flight"] += 1
                continue
            p = self.alloc_p(ex, now_m)
            book = self.alloc_fresh_book(ex, now_m)
            if book is None and rank_all:         # P14.1 2: the cached book ranks it (alloc_sell downloads a fresh
                book = ex.book                    #  one before the sale anyway); None = never downloaded
            if p is None or book is None:
                continue
            if q > 0 and book.get("bids"):
                px, depth = book["bids"][0]["price"], book["bids"][0]["quantity"]
                qty, edge, unit = min(q, depth), (p - px) / px, px
            elif q < 0 and book.get("asks"):
                px, depth = book["asks"][0]["price"], book["asks"][0]["quantity"]
                qty, edge, unit = min(self.alloc_lone_no(e, q), depth), (px - p) / max(1 - px, TICK), 1 - px
            else:
                continue
            qty = int(qty + 1e-9)
            rich = edge > cfg.alloc_max_edge_sell + 1e-9   # P14.1 2: a REFILL may sell it, a swap may not
            if qty >= 1 and (not rich or rank_all) and qty * unit >= self.ALLOC_MIN_USD:
                held.append({"kind": "long" if q > 0 else "short", "eid": e, "label": ex.label, "px": px, "edge": edge,
                             "unit": unit, "avail": qty * unit,
                             **({"refill_only": True} if rich else {}),                           # P14.1 2
                             **({"p": p} if swap_m else {}),                                      # P14.2
                             **({"floor_ok": self.mm_floor_ok(q > 0, px, p)} if fast else {})})      # P14
        if cfg.alloc_set_cost_per_usd > 0 and cfg.pair_unwind_enabled and self.pair_no_unwind_on():
            for race, members in sorted(self.groups.items()):
                if len(members) < 2 or any(m not in self.ex for m in members):
                    continue
                sets = min(-float(inv.get(m, 0.0)) for m in members)
                exs = [self.ex[m] for m in members]
                books = [self.alloc_fresh_book(x, now_m) for x in exs]
                if (sets < 1 or race in skip or any(not self.alloc_market_ok(x, skip) or x.label in pins for x in exs)
                        or any(b is None or not b.get("asks") for b in books)
                        or (cfg.alloc_set_rich_leg and race in self.alloc_ladder)):   # (P12 L1: laddered instead)
                    continue
                asks = [b["asks"][0]["price"] for b in books]
                free = len(members) - sum(asks)           # cash freed per set (covered NO sales at 1 - ask)
                if free <= 0:
                    continue
                if getattr(cfg, "pair_no_unwind_asks_le1", False) and self.nono_unwind_gated(members, sum(asks),
                                                                                           sum(asks) - 1):
                    blocked["set_bids_gt_1"] += 1         # (red team RT12-2: arb_plan's L3 gate would refuse it,
                    continue                              #  the pair waiting ALLOC_SET_WAIT with the allocator stalled)
                cpu = (sum(asks) - 1) / free             # the set's EV given up per $ freed
                n = int(min([sets] + [b["asks"][0]["quantity"] for b in books]) + 1e-9)
                if cpu <= cfg.alloc_set_cost_per_usd + 1e-9 and n * free >= self.ALLOC_MIN_USD:
                    held.append({"kind": "set", "race": race, "label": f"{race} NO+NO set", "px": sum(asks),
                                 "edge": max(0.0, cpu), "unit": free, "avail": n * free, "members": list(members)})
        spare = cash - cfg.alloc_mm_reserve
        if spare >= self.ALLOC_MIN_USD:
            held.append({"kind": "cash", "label": "spare cash", "px": 1.0, "edge": 0.0, "unit": 1.0, "avail": spare})
        if fast:                                  # P14 2: the stale MM inventory is sold first (its value entry out)
            mm = self.mm_refill_held(inv, now_m, skip, pins, blocked)
            mm_e = {h["eid"] for h in mm}
            held = mm + [h for h in held if h.get("eid") not in mm_e]
            held.sort(key=lambda h: (0 if h.get("mm") else 1, h["edge"], h.get("eid") or h.get("race") or ""))
        else:
            held.sort(key=lambda h: (h["edge"], h.get("eid") or h.get("race") or ""))
        room = {}
        # P14.1 4: a refill plan while cash is below half the target does not scan buy levels at all (nothing it
        # plans has a buy): the L2 prefer-short count and the pause count then stay out of the refill's blocked_by.
        no_levels = refill_only and self.p141("alloc_refill_ignore_prefer_short") and self.mm_cash_low()
        no_buy = ({} if no_levels or not getattr(cfg, "alloc_prefer_short", False)
                  else self.alloc_prefer_short_legs(inv, now_m, skip))
        for e, ex in sorted(self.ex.items()):
            if not self.alloc_market_ok(ex, skip):
                continue
            p, book = self.alloc_p(ex, now_m), self.alloc_fresh_book(ex, now_m)
            if p is None or book is None:
                continue
            q = float(inv.get(e, 0.0))
            room[e] = max(0.0, cfg.alloc_max_contract_usd - self.alloc_held_usd(q, p))
            if no_levels:                         # P14.1 4: a short-cash refill plan needs no buy level
                continue
            if q >= 0 and e in no_buy:            # Package 12 L2: the other leg is shorted instead (bids sum > 1)
                blocked["prefer_short"] += 1
            elif q >= 0:                          # (buying YES on a short would be a close: the held list's job)
                for lv in (book.get("asks") or [])[:3]:
                    px = lv["price"]
                    edge = (p - px) / px
                    if edge >= cfg.alloc_min_edge_buy - 1e-9:
                        levels.append({"eid": e, "label": ex.label, "short": False, "px": px, "edge": edge, "unit": px,
                                       "avail": lv["quantity"] * px, **({"p": p} if swap_m else {}), "pv": p})
            if q <= 0:
                for lv in (book.get("bids") or [])[:3]:
                    px = lv["price"]
                    edge = (px - p) / max(1 - px, TICK)
                    if edge >= cfg.alloc_min_edge_buy - 1e-9:
                        levels.append({"eid": e, "label": ex.label, "short": True, "px": px, "edge": edge,
                                       "unit": 1 - px, "avail": lv["quantity"] * (1 - px),
                                       **({"p": p} if swap_m else {}), "pv": p})
        levels.sort(key=lambda o: (-o["edge"], o["eid"], o["px"]))
        if getattr(self, "global_reduce", False) and levels:   # (red team RT-2: no buy while in reduce-only;
            blocked["risk"] += 1                                #  reserve refills, which only reduce, still run)
            levels = []
        net = self.alloc_netting()                             # P14.1 5: swaps go on, each checked against the rooms
        if getattr(self, "mmr_paused", False) and levels and not net:   # P12 ops mm_risk_reserve_*: value adds paused
            blocked["mm_risk_reserve"] += 1                     #  (the same: no buy; reserve refills still run)
            levels = []
        rfloor = self.alloc_room_floor() if net else None       # a swap may not take a room below this
        st_room = self.st_rooms(inv, now_m) if self.st_on() else None   # P15 state_max_usd: {state: $ room}
        left = float("inf") if turnover_left is None else max(0.0, turnover_left)
        pairs, gain = [], 0.0
        hyp = {e: float(q) for e, q in inv.items()}
        bloc_fn = getattr(self, "bloc_delta_now", None) if getattr(cfg, "bloc_delta_enabled", False) else None
        cap = self.bloc_cap() if bloc_fn is not None else None

        def sell_leg(h, usd):
            if h["kind"] == "cash":
                return {"kind": "cash", "label": h["label"], "usd": usd, "edge": 0.0, "qty": 0, "px": 1.0}
            n = int(usd / h["unit"] + 1e-9)
            if n < 1:
                return None
            leg = {"kind": h["kind"], "label": h["label"], "qty": n, "px": h["px"], "edge": h["edge"],
                   "usd": n * h["unit"]}
            if h["kind"] == "set":
                leg.update(race=h["race"], members=h["members"])
            else:
                leg["eid"] = h["eid"]
            if h.get("mm"):
                leg["mm"] = True                  # P14 2: stale MM inventory (alloc_sell checks fair / floor instead)
            return leg

        def apply(inv_, s, b, sign=1):
            if s is not None and s["kind"] in ("long", "short"):
                inv_[s["eid"]] = inv_.get(s["eid"], 0.0) + sign * (-s["qty"] if s["kind"] == "long" else s["qty"])
            if s is not None and s["kind"] == "set":
                for m in s["members"]:
                    inv_[m] = inv_.get(m, 0.0) + sign * s["qty"]
            if b is not None:
                inv_[b["eid"]] = inv_.get(b["eid"], 0.0) + sign * (-b["qty"] if b["short"] else b["qty"])
        # B2: refill the reserve first (sales with no buy; the bloc check as the pairs', red team RT-5)
        deficit = cfg.alloc_mm_reserve - cash
        hi = 0
        bloc = bloc_fn(hyp) if bloc_fn is not None else 0.0
        while deficit >= self.ALLOC_MIN_USD and hi < len(held) and left >= self.ALLOC_MIN_USD:
            h = held[hi]
            if not h.get("floor_ok", True):       # P14 mm_refill_fast: no refill sale below the value floor
                blocked["floor"] += 1
                hi += 1
                continue
            s = sell_leg(h, min(h["avail"], deficit, left)) if h["kind"] != "cash" else None
            if s is None:
                hi += 1
                continue
            apply(hyp, s, None)
            if bloc_fn is not None:
                new = bloc_fn(hyp)
                if abs(new) > cap + 1e-9 and abs(new) > abs(bloc) + 1e-9:
                    apply(hyp, s, None, sign=-1)
                    blocked["bloc"] += 1
                    hi += 1                       # this sale would push the bloc delta past the cap: the next holding
                    continue
                bloc = new
            pairs.append({"sell": s, "buy": None, "usd": s["usd"], "status": "pending", "proceeds": 0.0,
                          "sold_at": None})
            h["avail"] -= s["usd"]
            deficit -= s["usd"]
            left -= s["usd"]
            if h["avail"] < self.ALLOC_MIN_USD:
                hi += 1
        bloc = bloc_fn(hyp) if bloc_fn is not None else 0.0
        # B1: pairs (sell lowest edge-held, buy highest edge)
        sq = [h for h in held if h["avail"] >= self.ALLOC_MIN_USD and not h.get("mm")   # (P14: MM legs refill only;
              and not h.get("refill_only")]                                             # P14.1: and edge-rich ones)
        if refill_only:                           # P14 mm_refill_fast: the fast refill plans no pairs
            sq = []
        hurdle = cfg.alloc_min_improvement
        if swap_m:                                # P14.2: a long / short beyond p -+ alloc_swap_sell_margin is no swap
            hurdle = self.swap_hurdle()           #  candidate; swaps need the stricter of the two hurdles
            keep = [h for h in sq if h["kind"] not in ("long", "short")
                    or self.swap_floor_ok(h["kind"] == "long", h["px"], h["p"])]
            if len(keep) < len(sq):
                blocked["swap_floor"] += len(sq) - len(keep)
            sq = keep
        si = bi = 0
        while si < len(sq) and bi < len(levels) and left >= self.ALLOC_MIN_USD and len(pairs) < 200:
            h, o = sq[si], levels[bi]
            if o["edge"] - h["edge"] < cfg.alloc_min_improvement - 1e-9:
                break
            swap = swap_m and h["kind"] in ("long", "short")
            if swap and o["edge"] - h["edge"] < hurdle - 1e-9:   # P14.2: below the swap hurdle against the best
                blocked["swap_gain"] += 1                         #  level left - so against every level left
                si += 1
                continue
            x = min(h["avail"], o["avail"], room.get(o["eid"], 0.0), left)
            sk = per = None
            if st_room is not None:               # P15 state_max_usd: the buy's collateral at p within its state's room
                sk = self.st_key(o["eid"])
                if sk is not None:
                    per = ((1 - o["pv"]) / max(1 - o["px"], TICK)) if o["short"] else o["pv"] / max(o["px"], TICK)
                    lim = max(0.0, st_room.get(sk, cfg.state_max_usd)) / max(per, 1e-9)
                    if lim < x - 1e-9:
                        x = lim
                        blocked["state_cap"] += 1
                        self.st_blocked[sk] += 1
                        if x < self.ALLOC_MIN_USD:
                            bi += 1
                            continue
            if x < self.ALLOC_MIN_USD:
                if room.get(o["eid"], 0.0) < self.ALLOC_MIN_USD or o["avail"] < self.ALLOC_MIN_USD:
                    bi += 1
                else:
                    si += 1
                continue
            nb = int(x / o["unit"] + 1e-9)
            x = nb * o["unit"]
            s = sell_leg(h, x)
            if s is None or nb < 1:
                bi += 1
                continue
            b = {"eid": o["eid"], "label": o["label"], "short": o["short"], "px": o["px"], "qty": nb,
                 "edge": o["edge"], "usd": x}
            apply(hyp, s, b)
            if net:                               # P14.1 5: ~risk-neutral, but never a room below its own floor
                after = self.alloc_rooms(hyp)
                if after is None or not self.alloc_room_ok(after, rfloor):
                    apply(hyp, s, b, sign=-1)
                    blocked["mm_risk_reserve"] += 1
                    self.mm_risk_count("alloc")
                    bi += 1                       # this pair would eat into the MM room: the next level
                    continue
            if bloc_fn is not None:
                new = bloc_fn(hyp)
                if abs(new) > cap + 1e-9 and abs(new) > abs(bloc) + 1e-9:
                    apply(hyp, s, b, sign=-1)
                    blocked["bloc"] += 1
                    bi += 1                       # this level would push the bloc delta past the cap: the next one
                    continue
                bloc = new
            cash_sell = h["kind"] == "cash"
            pairs.append({"sell": s, "buy": b, "usd": x, "status": "sold" if cash_sell else "pending",
                          "proceeds": x if cash_sell else 0.0, "sold_at": -1e18 if cash_sell else None,
                          **({"netting": True, "rfloor": tuple(rfloor)} if net else {}),   # P14.1 5 (re-checked
                          #                                                                  before each leg)
                          **({"swap": self.swap_record(s, h["p"], b, o["p"], x)} if swap else {})})   # P14.2
            gain += x * (o["edge"] - h["edge"])
            if sk is not None:                    # P15: the state's room shrinks by this buy's collateral at p
                st_room[sk] = st_room.get(sk, cfg.state_max_usd) - x * per
            h["avail"] -= x if cash_sell else s["usd"]
            o["avail"] -= x
            room[o["eid"]] = room.get(o["eid"], 0.0) - x
            left -= x
            if h["avail"] < self.ALLOC_MIN_USD:
                si += 1
            if o["avail"] < self.ALLOC_MIN_USD or room[o["eid"]] < self.ALLOC_MIN_USD:
                bi += 1
        if bloc_fn is None and pairs and not self.alloc_bloc_logged:
            self.alloc_bloc_logged = True
            log.info("ALLOC bloc check skipped: bloc_delta_enabled is off (Part A's bloc_delta_now / bloc_cap)")
        if left < self.ALLOC_MIN_USD and si < len(sq) and bi < len(levels):
            blocked["turnover"] += 1
        return pairs, {"blocked_by": dict(blocked), "ev_gain_est": round(gain, 2)}

    def alloc_journal(self, pr):
        """The journal line of a planned pair: "ALLOC sell <label> <qty> @ <px> (edge-held x%) -> buy <label> <qty>
        @ <px> (edge y%)"."""
        s, b = pr["sell"], pr["buy"]
        if s["kind"] == "cash":
            left = f"spare cash ${pr['usd']:.0f}"
        elif s["kind"] == "set":
            left = f"unwind {s['label']} {s['qty']} sets @ asks sum {s['px']:.3f} (cost {100 * s['edge']:.1f}% per $)"
        elif s.get("mm"):                         # P14 2: stale market-making inventory
            left = (f"sell {s['label']} {'NO ' if s['kind'] == 'short' else ''}{s['qty']} @ {s['px']:.3f} "
                    f"(stale MM inventory, edge-held {100 * s['edge']:.1f}%)")
        else:
            left = (f"sell {s['label']} {'NO ' if s['kind'] == 'short' else ''}{s['qty']} @ {s['px']:.3f} "
                    f"(edge-held {100 * s['edge']:.1f}%)")
        right = (f"buy {b['label']} {'short YES ' if b['short'] else ''}{b['qty']} @ {b['px']:.3f} "
                 f"(edge {100 * b['edge']:.1f}%)" if b else "the reserve")
        return f"ALLOC {left} -> {right}"

    def alloc_block(self, why):
        bb = self.alloc_run.setdefault("blocked_by", {})
        bb[why] = bb.get(why, 0) + 1

    def alloc_tick(self, now, inv, mine_real, now_m=None, skip=()):
        """Package 10 B, once a cycle (see Config): finish the run in flight (set unwinds seen, buys after a cash
        read, sales), or start a new run once alloc_interval_s has passed since the last. Returns the exchanges
        traded (not quoted this cycle)."""
        cfg = self.cfg
        now_w = now.timestamp()
        now_m = util.time.monotonic() if now_m is None else now_m
        if not cfg.alloc_enabled:
            touched = set()
            if self.alloc_ladder or (self.api.live and self.sl_orders()):   # Package 12 L1: every ladder pulled
                touched = self.alloc_ladder_tick(inv, now_m, now, skip)
            for pr in self.alloc_pairs:           # P14.2: swaps in flight end here (the cash stays)
                if pr.get("swap") is not None:
                    self.swap_close(pr, now_w, "allocator off")
            if self.alloc_pairs or self.alloc_set_races:
                log.warning("ALLOC off: %d pair(s) dropped, %d set unwind registration(s) withdrawn (nothing forced; "
                            "the cash stays)", len(self.alloc_pairs), len(self.alloc_set_races))
            self.alloc_pairs, self.alloc_set_races, self.alloc_state = [], {}, "off"
            self.alloc_targets = {}
            if self.alloc_info:
                self.alloc_info["state"] = "off"
            return touched
        self.alloc_ages = self.refs.ages() if self.refs is not None and hasattr(self.refs, "ages") else {}
        inv = dict(inv)                           # (kept current with this tick's own trades: never an oversale)
        turnover = self.alloc_turnover(now_w)
        due = not self.alloc_pairs and (self.alloc_last_run_wall is None
                                         or now_w - self.alloc_last_run_wall >= cfg.alloc_interval_s)
        lad_touched = set()
        if cfg.alloc_set_rich_leg or self.alloc_ladder or (self.api.live and self.sl_orders()):   # Package 12 L1
            fresh = (not self.api.live or (self.cash_gate_on() and getattr(self, "cg_cash", None) is not None
                                           and self.cash_read_age() is not None
                                           and self.cash_read_age() < self.ALLOC_CASH_FRESH))
            lad_touched = self.alloc_ladder_tick(inv, now_m, now, skip, place=fresh)
        if not self.api.live:                     # dry run: the plan is logged, nothing sent
            if due:
                pairs, info = self.alloc_plan(inv, now_m, cfg.alloc_mm_reserve, skip,
                                              cfg.alloc_max_turnover_per_hour - turnover)
                self.alloc_last_run_wall = now_w
                self.alloc_targets = self.alloc_plan_targets(inv, pairs)   # Package 12 M2
                self.mm_risk_count("alloc", info["blocked_by"].get("mm_risk_reserve", 0))   # P12 ops
                for pr in pairs:
                    log.info("[dry] %s", self.alloc_journal(pr))
                if self.p142_on():                # P14.2: the swaps planned, one line each
                    self.swap_plan_log(pairs, now_w)
                self.alloc_state = "dry run"
                self.alloc_info = {"pairs_planned": len(pairs), **info, "reserve": cfg.alloc_mm_reserve}
            return lad_touched
        age = self.cash_read_age()
        if not (self.cash_gate_on() and getattr(self, "cg_cash", None) is not None and age is not None
                and age < self.ALLOC_CASH_FRESH):        # no action at all without a fresh cash read
            self.alloc_state = "waiting (no fresh cash read)"
            self.alloc_block("cash")
            self.alloc_info = {**self.alloc_info, "state": self.alloc_state,
                               "blocked_by": dict(self.alloc_run.get("blocked_by", {}))}
            return lad_touched
        traded = set(lad_touched)
        if due:
            cash = self.cash_left()
            pairs, info = self.alloc_plan(inv, now_m, cash, skip, cfg.alloc_max_turnover_per_hour - turnover)
            self.alloc_last_run_wall = now_w
            self.alloc_totals["runs_total"] += 1
            self.alloc_pairs, self.alloc_sells_stopped = pairs, False
            self.alloc_targets = self.alloc_plan_targets(inv, pairs)       # Package 12 M2
            self.mm_risk_count("alloc", info["blocked_by"].get("mm_risk_reserve", 0))   # P12 ops
            for pr in pairs:
                pr["planned_at"] = now_m
            self.alloc_run = {"blocked_by": dict(info["blocked_by"]), "pairs_planned": len(pairs), "sold": 0.0,
                              "bought": 0.0, "cash_before": round(cash, 2), "ev_gain_est": info["ev_gain_est"]}
            for pr in (pairs if self.p141_on() else ()):   # P14.1 7: the swaps planned this run (the 24-h report)
                if pr.get("buy") is not None:
                    self.alloc_run["swaps_planned"] = self.alloc_run.get("swaps_planned", 0) + 1
                    self.p141_log("swap_plan", pr["usd"],
                                  est=pr["usd"] * (pr["buy"]["edge"] - pr["sell"]["edge"]), now_w=now_w)
            log.warning("ALLOC run: %d pair(s) planned (cash %.0f, reserve %.0f, turnover left %.0f, est. gain %.0f)",
                        len(pairs), cash, cfg.alloc_mm_reserve, cfg.alloc_max_turnover_per_hour - turnover,
                        info["ev_gain_est"])
            for pr in pairs:
                log.warning("%s", self.alloc_journal(pr))
            if self.p142_on():                    # P14.2: the swaps planned, one line each
                self.swap_plan_log(pairs, now_w)
        elif (getattr(cfg, "mm_refill_fast", False)                            # P14 2: the reserve refilled NOW
              # P14.1 2: every cycle while the cash is short, and also while an hourly run's pairs are still in
              # flight (their markets are skipped); 4ff7d91: only with no pairs in flight, at most every 60 s
              and (self.p141("alloc_rank_all_markets")
                   or (not self.alloc_pairs and now_m - self.mmf_refill_last_m >= self.MM_REFILL_GAP))
              and self.cash_left() + self.hv_carve_used() < cfg.alloc_mm_reserve - self.ALLOC_MIN_USD):
            self.mm_refill_tick(inv, now_m, now, skip, turnover)
        writes = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        n_left = int(min(cfg.alloc_max_orders_per_cycle, math.floor(cfg.alloc_writes_frac * max(0, writes) / 3 + 1e-9)))
        # 1. registered NO+NO set unwinds (B3): done by take_arbitrage, or expired
        for pr in self.alloc_pairs:
            if pr["status"] == "set_wait":
                self.alloc_set_check(pr, inv, now_m, now_w)
        # 2. buys: pairs sold BEFORE the latest cash read (spare-cash pairs: the run's own fresh read)
        read_at = getattr(self, "cg_read_at", None)
        for pr in self.alloc_pairs:
            if pr["status"] != "sold" or pr["buy"] is None or read_at is None or pr["sold_at"] >= read_at:
                continue
            if pr["sold_at"] > -1e17 and now_m - pr["sold_at"] > self.ALLOC_BUY_WAIT:
                pr["status"] = "expired"
                log.warning("ALLOC buy %s expired: no cash read showed the money within %.0f s (the cash stays)",
                            pr["buy"]["label"], self.ALLOC_BUY_WAIT)
                continue
            if n_left < 1:
                self.alloc_block("writes")
                break
            if self.alloc_buy(pr, inv, mine_real, now_m, now_w, skip):
                n_left -= 1
                traded.add(pr["buy"]["eid"])
        # 3. sales (and set registrations)
        for pr in self.alloc_pairs:
            if pr["status"] != "pending":
                continue
            if self.alloc_sells_stopped and not (pr.get("fast")       # P14.1 2: a paired level gone stops the PAIRED
                                                 and self.p141("alloc_rank_all_markets")):   # sales, not the refills
                pr["status"] = "skipped"
                continue
            if now_m - pr.get("planned_at", now_m) > (self.MM_FAST_EXPIRE if pr.get("fast") else cfg.alloc_interval_s):
                pr["status"] = "expired"          # a sale still not sent after a whole interval: re-planned afresh
                continue
            if pr["sell"]["kind"] != "set" and n_left < 1:
                self.alloc_block("writes")
                break
            if self.alloc_sell(pr, inv, mine_real, now_m, now_w, skip):
                n_left -= 1
                traded.add(pr["sell"]["eid"])
        for pr in self.alloc_pairs:               # a refill (no buy) is finished once sold
            if pr["status"] == "sold" and pr["buy"] is None:
                pr["status"] = "done"
        for pr in self.alloc_pairs:               # P14.2: a finished swap's outcome (done / buy failed / not sold)
            if pr.get("swap") is not None and pr["status"] in self.ALLOC_DONE:
                self.swap_close(pr, now_w)
        self.alloc_pairs = [pr for pr in self.alloc_pairs if pr["status"] not in self.ALLOC_DONE]
        self.alloc_state = "running" if self.alloc_pairs else "idle"
        self.alloc_info = {**self.alloc_run, "state": self.alloc_state, "cash_after": round(self.cash_left(), 2),
                           "reserve": cfg.alloc_mm_reserve, "sells_stopped": self.alloc_sells_stopped,
                           "set_unwinds_registered": sorted(self.alloc_set_races)}
        return traded

    def alloc_level_ok(self, b, book, p):
        """(ok, touch, edge): the fresh book still shows the planned buy level (within ALLOC_LEVEL_TOL) at an edge >=
        alloc_min_edge_buy."""
        key = "bids" if b["short"] else "asks"
        lv = (book or {}).get(key)
        if not lv or p is None:
            return False, None, None
        best = lv[0]["price"]
        if b["short"]:
            near, edge = best >= b["px"] - self.ALLOC_LEVEL_TOL - 1e-9, (best - p) / max(1 - best, TICK)
        else:
            near, edge = best <= b["px"] + self.ALLOC_LEVEL_TOL + 1e-9, (p - best) / best
        return near and edge >= self.cfg.alloc_min_edge_buy - 1e-9, best, edge

    def alloc_download(self, ex, mine_real):
        """A fresh book of ex, our own orders stripped (cached on ex), or None on a failed download."""
        try:
            ex.book = strip_own(self.api.book(ex.eid, self.tid), mine_real.get(ex.eid, []))
            ex.book_time = ex.verified = util.time.monotonic()
            return ex.book
        except ApiError as err:
            log.warning("ALLOC %s: book download failed (%s)", ex.label, err)
            return None

    def alloc_buy(self, pr, inv, mine_real, now_m, now_w, skip):
        """The buy of a sold pair (after a cash read showing the money above the reserve): True if an order was
        sent. The level gone -> the pair ends, its cash stays, no more sales this run."""
        cfg, b = self.cfg, pr["buy"]
        ex = self.ex.get(b["eid"])
        if getattr(self, "global_reduce", False):     # (red team RT-2: no buy in reduce-only; the pair waits, then
            self.alloc_block("risk")                  #  expires after ALLOC_BUY_WAIT with its cash kept)
            return False
        if getattr(self, "mmr_paused", False) and not pr.get("netting"):   # P12 ops: value adds paused (as RT-2;
            self.alloc_block("mm_risk_reserve")                             #  P14.1 5: a netted swap goes on)
            self.mm_risk_count("alloc")
            return False
        if ex is None or not self.alloc_market_ok(ex, skip) or busy(ex, now_m):
            return False                          # (traded by another feature / a write in flight: next cycle)
        q = float(inv.get(b["eid"], 0.0))
        if (q < 0 and not b["short"]) or (q > 0 and b["short"]):
            pr["status"] = "dropped"              # never flip a position: the cash stays
            return False
        if pr.get("netting") and not self.alloc_buy_room_ok(b, q, inv, pr):   # P14.1 5: re-checked before the buy
            return False
        # P14.1 5: a netted swap's buy may spend the proceeds of its OWN sale even below the reserve (the cash the MM
        # reserve held before that sale is never touched); every other buy keeps to the cash above alloc_mm_reserve.
        avail = self.cash_left() + self.hv_carve_used() - cfg.alloc_mm_reserve   # (P15: the ladder's carve-out
        if pr.get("netting"):                                                    #  resting counts as reserve)
            avail = max(avail, min(pr.get("proceeds", 0.0), self.cash_left()))
        if avail < (b["px"] if not b["short"] else 1 - b["px"]):
            self.alloc_block("cash")              # the cash read does not show the money (yet)
            return False
        if not self.writes_ready(3):
            self.alloc_block("writes")
            return False
        book = self.alloc_download(ex, mine_real)
        if book is None:
            return False
        p = self.alloc_p(ex, now_m)
        ok, best, edge = self.alloc_level_ok(b, book, p)
        if not ok:
            pr["status"] = "gone"
            self.alloc_sells_stopped = True
            self.alloc_block("depth")
            log.warning("ALLOC buy %s skipped: the level is gone (touch %s, planned %.3f) - the cash stays, no more "
                        "sales this run", b["label"], "none" if best is None else f"{best:.3f}", b["px"])
            return False
        unit = best if not b["short"] else 1 - best
        key = "bids" if b["short"] else "asks"
        room = cfg.alloc_max_contract_usd - self.alloc_held_usd(q, p)
        qty = int(min(self.depth_within({key: book[key][:1]}, key, best), min(pr["proceeds"], avail, room) / unit)
                  + 1e-9)
        if qty < 1:
            pr["status"] = "dropped"
            return False
        if self.st_on():                          # P15 state_max_usd: the buy only within its state's room (it waits,
            n_ok = self.st_add_room(ex, not b["short"], qty, p=p, commit=False)   # then expires: the cash stays)
            if n_ok < qty:
                self.alloc_block("state_cap")
                self.st_count(ex, "alloc")
                if n_ok < 1:
                    return False
                qty = n_ok
        order = {"exchangeId": b["eid"], "side": "yes", "action": "sell" if b["short"] else "buy", "quantity": qty,
                 "price": best, "tournamentId": self.tid,
                 "expirationDate": iso(util.utcnow() + timedelta(seconds=cfg.take_order_ttl))}
        if self.cash_gate_blocks([order]):
            self.alloc_block("cash")
            return False
        done = self.alloc_send(ex, order, now_m, "buy")
        if done is None:
            return False
        usd = done * unit
        self.alloc_run["bought"] = round(self.alloc_run.get("bought", 0.0) + usd, 2)
        self.alloc_totals["bought_total"] += usd
        if pr["sell"]["kind"] == "cash" and usd > 0:
            self.alloc_flows.append((now_w, usd))
        inv[b["eid"]] = q + (-done if b["short"] else done)
        if done >= 1 and self.st_on():            # P15: the state's collateral grows by this buy (this cycle)
            self.st_add_room(ex, not b["short"], done, p=p, commit=True, force=True)
        if done >= 1:
            pr["status"] = "bought"
            got = done * ((p - best) if not b["short"] else (best - p))   # P14.1 7: EV bought (realised, at p)
            self.p141_log("swap_buy", usd, real=got, now_w=now_w)
            if pr.get("swap") is not None:        # P14.2: the swap complete, journaled realised at p
                self.swap_bought(pr["swap"], done, best, p, edge, usd, now_w)
            log.warning("ALLOC bought %s %s%.0f @ %.3f (edge %.1f%%, $%.0f)", b["label"],
                        "short YES " if b["short"] else "", done, best, 100 * edge, usd)
        else:
            pr["status"] = "gone"
            self.alloc_sells_stopped = True
            log.warning("ALLOC buy %s: nothing traded - the cash stays, no more sales this run", b["label"])
        return True

    def alloc_sell(self, pr, inv, mine_real, now_m, now_w, skip):
        """A pending pair's sale (or B3 set registration): True if an order was sent. Paired: only while a fresh book
        of the buy market still shows its level, and the edges still pass on both fresh books."""
        cfg, s, b = self.cfg, pr["sell"], pr["buy"]
        pins = self.alloc_pins()
        if s["kind"] == "set":
            exs = [self.ex.get(m) for m in s["members"]]
            if any(x is None or x.label in pins for x in exs):
                pr["status"] = "dropped"
                return False
            if any(not self.alloc_market_ok(x, skip) or busy(x, now_m) for x in exs):
                return False
        else:
            ex = self.ex.get(s["eid"])
            if ex is None or ex.label in pins:
                pr["status"] = "dropped"
                return False
            if not self.alloc_market_ok(ex, skip) or busy(ex, now_m):
                return False
            q = float(inv.get(s["eid"], 0.0))
            if (s["kind"] == "long" and q < 1) or (s["kind"] == "short" and q > -1):
                pr["status"] = "dropped"
                return False
        if not self.writes_ready(3):
            self.alloc_block("writes")
            return False
        bedge = None
        if b is not None and getattr(self, "global_reduce", False):   # (red team RT-2: its buy could not follow)
            self.alloc_block("risk")
            return False
        if b is not None and getattr(self, "mmr_paused", False) and not pr.get("netting"):   # P12 ops: nor while
            self.alloc_block("mm_risk_reserve")                      #  paused (P14.1 5: a netted swap goes on)
            self.mm_risk_count("alloc")
            return False
        if b is not None and pr.get("netting"):   # P14.1 5: the rooms the whole pair would leave, re-checked now
            hyp = {e: float(v) for e, v in inv.items()}
            if s["kind"] in ("long", "short"):
                hyp[s["eid"]] = float(inv.get(s["eid"], 0.0)) + (-s["qty"] if s["kind"] == "long" else s["qty"])
            hyp[b["eid"]] = float(inv.get(b["eid"], 0.0)) + (-b["qty"] if b["short"] else b["qty"])
            after = self.alloc_rooms(hyp)
            if after is None or not self.alloc_room_ok(after, tuple(pr.get("rfloor") or self.alloc_room_floor())):
                self.alloc_block("mm_risk_reserve")
                self.mm_risk_count("alloc")
                log.info("ALLOC %s not sold: the swap's room check (mm_risk_reserve net of the sale) no longer passes",
                         s["label"])
                return False
        if b is not None:                         # the paired level first: no sale without it on a fresh book
            bx = self.ex.get(b["eid"])
            if bx is None or not self.alloc_market_ok(bx, skip):
                return False
            bbook = self.alloc_download(bx, mine_real)
            if bbook is None:
                return False
            ok, _, bedge = self.alloc_level_ok(b, bbook, self.alloc_p(bx, now_m))
            if not ok:
                pr["status"] = "gone"
                self.alloc_block("depth")
                log.info("ALLOC %s not sold: its paired level (%s @ %.3f) is gone", s["label"], b["label"], b["px"])
                return False
        if s["kind"] == "set":
            sets = min(-float(inv.get(m, 0.0)) for m in s["members"])
            if sets < 1:
                pr["status"] = "dropped"
                return False
            # "sets": what the plan needs - take_arbitrage unwinds no more at the allocator's cost (red team RT-1;
            # several pairs of one race add up)
            reg = self.alloc_set_races.get(s["race"]) or {}
            self.alloc_set_races[s["race"]] = {"cost": max(s["px"] - 1 + 1e-9, reg.get("cost", -1.0)),
                                               "until": now_m + self.ALLOC_SET_WAIT,
                                               "sets_before": reg.get("sets_before", sets),
                                               "free": len(s["members"]) - s["px"],
                                               "sets": reg.get("sets", 0) + s["qty"]}
            pr["status"] = "set_wait"
            log.warning("ALLOC %s: registered for the short-set unwind at asks sum <= %.3f (%.0f sets held)",
                        s["label"], s["px"], sets)
            return False
        book = self.alloc_download(ex, mine_real)
        if book is None:
            return False
        key = "bids" if s["kind"] == "long" else "asks"
        lv = book.get(key)
        p = self.alloc_p(ex, now_m)
        if not lv or p is None:
            pr["status"] = "gone"
            return False
        best = lv[0]["price"]
        edge = (p - best) / best if s["kind"] == "long" else (best - p) / max(1 - best, TICK)
        fast = bool(getattr(cfg, "mm_refill_fast", False))
        if s.get("mm"):                           # P14 2: stale MM inventory - near fair and the floor, not the edge
            fair = ex.last_fv if ex.last_fv is not None else p
            gap = fair - best if s["kind"] == "long" else best - fair
            conc_ok = gap <= cfg.mm_recycle_concession + 1e-9 or self.p141("alloc_cancel_mm_first")   # P14.1 1
            if not conc_ok or not self.mm_floor_ok(s["kind"] == "long", best, p):
                pr["status"] = "gone"
                log.info("ALLOC %s not sold: %.3f is %.3f from fair %.3f (> concession %.3f) or past the value floor - "
                         "it rests through the recycler", s["label"], best, gap, fair, cfg.mm_recycle_concession)
                return False
        elif edge > cfg.alloc_max_edge_sell + 1e-9 or (bedge is not None
                                                       and bedge - edge < cfg.alloc_min_improvement - 1e-9):
            pr["status"] = "gone"
            log.info("ALLOC %s not sold: edge-held now %.1f%% at %.3f (the pair no longer pays)", s["label"],
                     100 * edge, best)
            return False
        if (pr.get("swap") is not None and b is not None and self.p142_on()   # P14.2: the swap's own floor and
                and s["kind"] in ("long", "short")):                          #  hurdle, on the fresh book (the IOC
            why = None                                                        #  goes out at this very touch)
            if not self.swap_floor_ok(s["kind"] == "long", best, p):
                why = ("swap_floor", f"{best:.3f} is past the swap floor (p {p:.3f} -+ alloc_swap_sell_margin "
                                     f"{cfg.alloc_swap_sell_margin:.3f})")
            elif bedge is not None and bedge - edge < self.swap_hurdle() - 1e-9:
                why = ("swap_gain", f"the gain {100 * (bedge - edge):.1f}% per $ is below the swap hurdle "
                                    f"{100 * self.swap_hurdle():.1f}%")
            if why is not None:
                pr["status"] = "gone"
                self.alloc_block(why[0])
                log.info("ALLOC %s not sold (swap): %s", s["label"], why[1])
                return False
        if fast and b is None and not self.mm_floor_ok(s["kind"] == "long", best, p):   # P14 2: refills >= floor
            pr["status"] = "gone"
            self.alloc_block("floor")
            log.info("ALLOC %s not sold: %.3f is past the value floor (p %.3f -+ %.3f)", s["label"], best, p,
                     cfg.value_sell_margin)
            return False
        if fast and self.mm_sale_lagging(s["eid"], q, now_m):   # P14 (RT13-3): an earlier IOC's sale not read yet
            self.alloc_block("in_flight")
            return False
        unit = best if s["kind"] == "long" else 1 - best
        cap_q = q if s["kind"] == "long" else self.alloc_lone_no(s["eid"], q)
        qty = int(min(s["qty"], lv[0]["quantity"], cap_q) + 1e-9)
        if qty < 1:
            pr["status"] = "gone"
            return False
        order = {"exchangeId": s["eid"], "side": "yes", "action": "sell" if s["kind"] == "long" else "buy",
                 "quantity": qty, "price": best, "tournamentId": self.tid,
                 "expirationDate": iso(util.utcnow() + timedelta(seconds=cfg.take_order_ttl))}
        if s["kind"] == "short":                  # a short's buy-back only as a covered "sell NO" (needs no cash)
            order = self.no_sell_order(order, q)
            if order is None or not order.get("_no_sell"):
                pr["status"] = "dropped"
                log.info("ALLOC %s not sold: its NO cannot go out as a covered sale", s["label"])
                return False
            order["quantity"] = min(int(order["quantity"]), qty)
        if b is not None:
            order["_alloc_paired"] = True          # (Part A1 v: an allocator sale, never a resting quote)
        if self.cash_gate_blocks([order]):
            self.alloc_block("cash")
            return False
        done = self.alloc_send(ex, order, now_m, "sell")
        if done is None:
            return False
        if done < 1:
            pr["status"] = "skipped"
            return True
        usd = done * unit
        inv[s["eid"]] = q + (-done if s["kind"] == "long" else done)
        pr.update(status="sold", proceeds=usd, sold_at=now_m)
        self.alloc_flows.append((now_w, usd))
        self.alloc_run["sold"] = round(self.alloc_run.get("sold", 0.0) + usd, 2)
        self.alloc_totals["sold_total"] += usd
        log.warning("ALLOC sold %s %.0f @ %.3f (edge-held %.1f%%, $%.0f freed)%s", s["label"], done, best, 100 * edge,
                    usd, f" -> buy {b['label']} after the next cash read" if b else
                    " (reserve refill)")
        if self.p141("alloc_cancel_mm_first"):    # P14.1 1: the sale cancelled our quotes there (alloc_send) - the
            self.mmf_hold[s["eid"]] = now_m       #  REDUCING side stays off until the positions read shows the sale
        gave = done * ((p - best) if s["kind"] == "long" else (best - p))   # P14.1 7: EV given up (< 0: above p)
        self.p141_log("swap_sell" if b is not None else "refill", usd, real=-gave, now_w=now_w)
        if pr.get("swap") is not None:            # P14.2: the sale as traded (realised at p)
            self.swap_sold(pr["swap"], done, best, p, usd)
        if fast or self.p141("alloc_cancel_mm_first"):   # P14: not planned / sold again until the read shows it
            self.mmf_sent[s["eid"]] = (now_m, abs(q), float(done))
            if pr.get("fast"):
                self.mmf_refill["sold_usd"] = round(self.mmf_refill["sold_usd"] + usd, 2)
            if s.get("mm"):
                self.mmf_refill["mm_sold_usd"] = round(self.mmf_refill["mm_sold_usd"] + usd, 2)
                log.warning("MM RECYCLE %s: %.0f stale MM shares sold by IOC @ %.3f ($%.0f to the reserve)", s["label"],
                            done, best, usd)
        return True

    def alloc_set_check(self, pr, inv, now_m, now_w):
        """B3: a registered set race - its sets fell (take_arbitrage unwound some) -> sold (cash freed at the planned
        asks sum); past ALLOC_SET_WAIT -> withdrawn, expired."""
        s = pr["sell"]
        reg = self.alloc_set_races.get(s["race"])
        sets = min(-float(inv.get(m, 0.0)) for m in s["members"])
        if reg is not None and sets <= reg["sets_before"] - 1:
            n = reg["sets_before"] - max(0.0, sets)
            usd = n * reg["free"]
            self.alloc_set_races.pop(s["race"], None)
            pr.update(status="sold", proceeds=usd, sold_at=now_m)
            self.alloc_flows.append((now_w, usd))
            self.alloc_run["sold"] = round(self.alloc_run.get("sold", 0.0) + usd, 2)
            self.alloc_totals["sold_total"] += usd
            log.warning("ALLOC %s: %.0f sets unwound (~$%.0f freed)", s["label"], n, usd)
        elif reg is None or now_m > reg["until"]:
            self.alloc_set_races.pop(s["race"], None)
            pr["status"] = "expired"
            log.info("ALLOC %s: no unwind within %.0f s - registration withdrawn", s["label"], self.ALLOC_SET_WAIT)

    def alloc_send(self, ex, order, now_m, what):
        """One immediate-or-cancel allocator order: our orders on ex cancelled first (refused if that
        cannot be confirmed: never a price crossing our own order), the order (alive take_order_ttl), its leftover
        cancelled at once. Returns the shares traded (quantityTraded; 0 if refused), or None if not sent."""
        cfg, e = self.cfg, ex.eid
        if not self.cancel(e, [], whole_exchange=True):
            log.warning("ALLOC %s %s skipped: could not confirm our own orders there are cancelled", what, ex.label)
            return None
        self.orders_stale = True
        try:
            results = self.place_orders([order])
        except ApiError as err:
            if err.code == "WRITE_BUDGET_WAIT":
                self.alloc_block("writes")
                return None
            ex.pending_until = now_m + cfg.pending_seconds
            util.alert(f"allocator order on {ex.label} failed ({err}) - check positions")
            if err.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {err.code}")
            return None
        res = results[0] if results else {}
        data = res.get("data") or {}
        if res.get("ok"):
            self.remember_order(order, data, now_m)
        self.cancel(e, [], whole_exchange=True, quiet=True)     # the leftover, at once
        if data.get("orderId") is not None:
            self.order_meta[data["orderId"]] = {"our_side": "bid" if order["action"] == "buy" else "ask",
                                                "price": order["price"], "take": True, "alloc": True, "eid": e,
                                                "t": util.time.time(), **({"no_sell": True} if order.get("_no_sell") else {})}
            self.notes_dirty = True
        if not res.get("ok"):
            log.info("ALLOC %s %s refused: %s", what, ex.label, (data.get("error") or {}).get("message", "?"))
            return 0.0
        return float(data.get("quantityTraded") or 0)

    # ------------------------------------------------------------------------------ Package 12 L1: rich-leg ladder
    ALLOC_LADDER_REQUOTE = 3600.0  # a race's resting rich-leg ladder is re-quoted at most this often (s)
    ALLOC_LADDER_KEEP = 3000.0     # at a re-quote, an order exactly at its target stays with at least this life left (s)
    ALLOC_LADDER_REFUSED_WAIT = 900.0   # the exchange refused a race's whole ladder: not re-sent before this (s)

    def sl_orders(self, eid=None):
        """Package 12 L1: our resting rich-leg ladder orders (order_meta "set_ladder"), on eid or everywhere."""
        meta = self.order_meta
        return [o for o in list(self.my_orders.values()) if (eid is None or o.eid == eid)
                and (meta.get(o.order_id) or {}).get("set_ladder")]

    def alloc_ladder_plan(self, inv, now_m, skip=()):
        """L1, THE PURE PLANNER: ({race: plan}, {race: why}) - plan = {"eid", "label", "p", "edge", "best_bid", "avail",
        "cap", "levels": [(YES bid price, shares)]} for each NO+NO race whose favourite leg is rich (see Config); why =
        "soft" (cannot be judged now: no fresh book / liquid p, traded by another feature this cycle, a write in
        flight - a resting ladder stays) or the reason the race has no ladder (resting orders there are pulled)."""
        cfg = self.cfg
        plans, why = {}, {}
        on = cfg.alloc_enabled and cfg.alloc_set_rich_leg and self.reduce_no_on()
        pins = self.alloc_pins()
        offs = [float(x) for x in (cfg.alloc_set_ladder or ())][:LADDER_MAX_LEVELS]
        is_lad = (lambda o: bool((self.order_meta.get(o.order_id) or {}).get("set_ladder")))
        for race, members in sorted(self.groups.items()):
            if len(members) < 2 or any(m not in self.ex for m in members):
                continue
            if min(-float(inv.get(m, 0.0)) for m in members) < 1:
                why[race] = "not a set"
                continue
            if not on or not offs:
                why[race] = "off"
                continue
            exs = [self.ex[m] for m in members]
            if race in skip or any(x.eid in skip for x in exs) or any(busy(x, now_m) for x in exs):
                why[race] = "soft"
                continue
            if any(not self.alloc_market_ok(x) for x in exs):
                why[race] = "window"              # (pre-close window / headline)
                continue
            ps = [self.alloc_p(x, now_m) for x in exs]
            if any(p_ is None for p_ in ps):
                why[race] = "soft"
                continue
            p = max(ps)
            if sum(1 for p_ in ps if p_ >= p - 1e-12) != 1:
                why[race] = "no favourite"        # a tie: no rich leg to tell
                continue
            fav = exs[ps.index(p)]                # the rich leg: NO on the favourite (the longshots' NO: never)
            if fav.label in pins:
                why[race] = "pinned"
                continue
            book = self.alloc_fresh_book(fav, now_m)
            if book is None:
                why[race] = "soft"
                continue
            if not book.get("asks") or not book.get("bids"):   # (the cached book: our own orders' size stripped,
                why[race] = "soft"                              #  so the ladder joins other traders' best bid)
                continue
            ask, bb = book["asks"][0]["price"], (book["bids"][0]["price"], book["bids"][0]["quantity"])
            edge = (ask - p) / max(1 - ask, TICK)          # the favourite NO's edge-held (a short's, as alloc_plan)
            if edge > cfg.alloc_max_edge_sell + 1e-9:
                why[race] = "not rich"
                continue
            free = self.cash_free(fav.eid, skip=is_lad)    # (less our OTHER covered NO sales resting there)
            avail = int(min(self.nono_set_part(fav.eid, float(inv.get(fav.eid, 0.0))), free["set"]) + 1e-9)
            per = int(avail / len(offs) + 1e-9)
            own_asks = [o.price for o in list(self.my_orders.values()) if o.eid == fav.eid and not o.is_bid]
            if fav.quote is not None and getattr(fav.quote, "ask", None) is not None:
                own_asks.append(fav.quote.ask)
            cap = min([p + cfg.value_sell_margin, ask - TICK] + [a - TICK for a in own_asks])
            levels = {}
            for off in offs:
                raw = min(bb[0] + off, cap)
                if raw < PMIN - 1e-9 or per < 1:
                    continue
                px = floor_tick(raw)
                levels[px] = levels.get(px, 0) + per
            if not levels:
                why[race] = "too small"
                continue
            plans[race] = {"eid": fav.eid, "label": fav.label, "p": p, "edge": edge, "best_bid": bb[0],
                           "avail": avail, "cap": cap, "levels": sorted(levels.items(), reverse=True)}
        return plans, why

    def alloc_ladder_tick(self, inv, now_m, now, skip=(), place=True):
        """L1, once a cycle from alloc_tick (also while ladder orders rest with the allocator off): pull what is
        unsafe at once; re-quote a race's ladder at most once per ALLOC_LADDER_REQUOTE (orders exactly at target with
        life left keep their queue spot); place through the cash gate (refused: the race waits, retried next cycle).
        Returns the exchanges where orders were placed or cancelled (not quoted this cycle)."""
        cfg = self.cfg
        plans, why = self.alloc_ladder_plan(inv, now_m, skip)
        touched = set()
        rest = defaultdict(list)
        for o in self.sl_orders():
            rest[(self.order_meta.get(o.order_id) or {}).get("sl_race")].append(o)
        # 1. pulls: orphans, races with no ladder (unless only "soft"), unsafe orders, more on sale than the set part
        for race, os_ in sorted(rest.items(), key=lambda kv: str(kv[0])):
            plan = plans.get(race)
            if plan is None:
                bad = list(os_)
                if why.get(race) == "soft" and race in self.alloc_ladder:
                    e = os_[0].eid
                    p_e = self.alloc_p(self.ex[e], now_m) if e in self.ex else None   # (red team RT12-1: the
                    if (all(o.eid == e for o in os_) and p_e is not None              #  ladder's own p known, every
                            and all(o.price <= p_e + cfg.value_sell_margin + 1e-9 for o in os_)   # bid within it)
                            and sum(o.qty for o in os_) <= self.nono_set_part(e, float(inv.get(e, 0.0))) + 1e-9):
                        bad = []                  # (cannot be judged now, still within the set part: it stays)
            else:
                bad = [o for o in os_ if o.eid != plan["eid"] or o.price > plan["cap"] + 1e-9]
                if sum(o.qty for o in os_) > plan["avail"] + 1e-9:
                    bad = list(os_)               # never more NO on sale than the set part: all of it re-planned
            by_e = defaultdict(list)
            for o in bad:
                by_e[o.eid].append(o)
            for e, lst in sorted(by_e.items()):
                if not self.writes_ready(len(lst)):
                    self.alloc_block("writes")
                    continue
                if self.cancel(e, lst, whole_exchange=False):
                    touched.add(e)
                    self.orders_stale = True
                    log.warning("ALLOC LADDER %s: %d order(s) pulled (%s)", race, len(lst),
                                "unsafe" if plan is not None or why.get(race) == "soft"
                                else why.get(race, "no ladder"))
                    self.alloc_ladder.pop(race, None)     # (re-planned as soon as it can be, within the set part)
        for race in [r for r in self.alloc_ladder if r not in plans and why.get(r) != "soft"]:
            self.alloc_ladder.pop(race, None)
        # 2. re-quotes (at most once per ALLOC_LADDER_REQUOTE a race) and new ladders (only with a fresh cash read)
        for race, plan in sorted(plans.items()) if place else ():
            st = self.alloc_ladder.get(race)
            if st is not None and now_m - st["at"] < self.ALLOC_LADDER_REQUOTE:
                continue
            refused = getattr(self, "alloc_ladder_refused", {})
            if st is None and now_m - refused.get(race, -1e18) < self.ALLOC_LADDER_REFUSED_WAIT:
                continue                          # (red team RT12-3: never one batch write a cycle into refusals)
            e = plan["eid"]
            ex = self.ex[e]
            cur = [o for o in self.sl_orders(e) if (self.order_meta.get(o.order_id) or {}).get("sl_race") == race]
            want, keep = list(plan["levels"]), []
            for o in sorted(cur, key=lambda o: -o.price):
                hit = next((w for w in want if abs(w[0] - o.price) < 1e-9 and abs(w[1] - o.qty) < 1e-9), None)
                life = (o.expires - now).total_seconds() if o.expires else 0.0
                if hit is not None and life >= self.ALLOC_LADDER_KEEP:
                    want.remove(hit)
                    keep.append(o)
            gone = [o for o in cur if o not in keep]
            if not self.api.live:                 # dry run: planned and logged, nothing sent
                self.alloc_ladder[race] = {"at": now_m, "eid": e, "no_at": -float(inv.get(e, 0.0)), "sent": 0.0}
                log.info("[dry] ALLOC LADDER %s: sell NO %s (p %.3f, edge-held %.1f%%): %s", race, plan["label"],
                         plan["p"], 100 * plan["edge"], " ".join(f"{n}@{1 - px:.3f}" for px, n in plan["levels"]))
                continue
            if not self.writes_ready(len(gone) + (1 if want else 0)):
                self.alloc_block("writes")
                continue
            if gone:
                if not self.cancel(e, gone, whole_exchange=False):
                    continue                      # (never a new ladder on top of one not confirmed gone)
                touched.add(e)
                self.orders_stale = True
            sent, n_refused = 0.0, 0
            if want:
                orders = [{"exchangeId": e, "side": "yes", "action": "buy", "quantity": int(n), "price": px,
                           "tournamentId": self.tid, "expirationDate": iso(now + timedelta(seconds=MAX_ORDER_TTL)),
                           "_no_sell": True} for px, n in want]
                try:
                    results = self.place_orders(orders)     # (the cash gate trims / refuses each level here)
                except ApiError as err:
                    if err.code == "WRITE_BUDGET_WAIT":
                        self.alloc_block("writes")
                        continue
                    ex.pending_until = now_m + cfg.pending_seconds
                    refused[race] = now_m
                    util.alert(f"set ladder order on {ex.label} failed ({err}) - check positions")
                    if err.code in FATAL_API_CODES:
                        util.fatal(f"orders rejected with {err.code}")
                    continue
                touched.add(e)
                self.orders_stale = True
                by_index = {r.get("index", k): r for k, r in enumerate(results or []) if isinstance(r, dict)}
                prices = [px for px, _ in plan["levels"]]
                for k, o in enumerate(orders):
                    res = by_index.get(k) or {}
                    data = res.get("data") or {}
                    if not res.get("ok"):
                        self.alloc_block("cash" if res.get("cash_gated") else "refused")
                        n_refused += 0 if res.get("cash_gated") else 1
                        continue
                    self.remember_order(o, data, now_m)
                    sent += float(o["quantity"])
                    if data.get("orderId") is not None:
                        self.order_meta[data["orderId"]] = {"our_side": "bid", "price": o["price"], "alloc": True,
                                                            "set_ladder": 1 + prices.index(o["price"]),
                                                            "sl_race": race, "no_sell": True, "eid": e,
                                                            "t": util.time.time()}
                        self.notes_dirty = True
            if keep or sent > 0:
                self.alloc_ladder[race] = {"at": now_m, "eid": e, "no_at": -float(inv.get(e, 0.0)),
                                           "sent": sent + sum(o.qty for o in keep)}
                log.warning("ALLOC LADDER %s: sell NO %s (p %.3f, edge-held %.1f%%, best bid %.3f): %s%s", race,
                            plan["label"], plan["p"], 100 * plan["edge"], plan["best_bid"],
                            " ".join(f"{n}@{1 - px:.3f}" for px, n in plan["levels"]),
                            f" ({len(keep)} kept)" if keep else "")
            else:
                self.alloc_ladder.pop(race, None)     # (the gate refused it all: the race waits, tried next cycle)
                if want and n_refused:                # (the EXCHANGE refused it: tried again after the wait)
                    refused[race] = now_m
                    log.warning("ALLOC LADDER %s: the exchange refused every level - not re-sent for %.0f s", race,
                                self.ALLOC_LADDER_REFUSED_WAIT)
        self.alloc_ladder_status(inv)
        return touched

    def alloc_ladder_status(self, inv):
        """status.json alloc.set_ladder: races laddered, shares resting, shares filled since each race's last
        re-quote (estimate: the rich leg's NO held then less now, within what was put on sale)."""
        filled = sum(max(0.0, min(st.get("sent", 0.0), st.get("no_at", 0.0) + float(inv.get(st["eid"], 0.0))))
                     for st in self.alloc_ladder.values())
        self.alloc_ladder_info = {"races": len(self.alloc_ladder),
                                  "shares_resting": int(sum(o.qty for o in self.sl_orders()) + 1e-9),
                                  "filled": int(filled + 1e-9)}

    def sl_guard_quote(self, ex, q, lad):
        """L1: the quote's ask on a market where our ladder bids rest stays at least a tick above the highest of
        them (and a kept resting ask at / below it is replaced): never a self-cross. None if that is off the grid."""
        top = round(max(o.price for o in lad), 3)
        lo = round(top + TICK, 3)
        if q.ask is None:
            return q
        if q.ask > top + 1e-9:
            if q.ask_limit is not None and q.ask_limit < lo - 1e-9:
                return replace(q, ask_limit=lo)     # (a resting ask at / below the ladder is never kept)
            return q
        if lo > PMAX + 1e-9:
            return replace(q, ask=None, ask_size=0, ask_limit=None, ask_max=None)
        return replace(q, ask=lo, ask_limit=max(lo, q.ask_limit) if q.ask_limit is not None else lo)
