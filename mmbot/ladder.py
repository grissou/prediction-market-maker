"""
LadderMixin: the harvest ladder - resting maker levels that sell inventory we hold back to the
market above fair value, a few shares at each of several prices, re-quoted as the top moves.

Owns the ladder's plan and placement (hv_plan, hv_tick, hv_orders), its re-quote state and fills,
the carve-out it takes from the MM cash reserve (hv_carve_used, mm_reserve_effective), the lock
that keeps the market-making quote a tick clear of our own levels (hv_guard_quote, hv_quote_hold),
place_orders_keep (orders that survive a cancel-all) and its status.json / summary parts.

The ladder only ever reduces a position: it never adds to one, never takes liquidity and never
overrides a risk pause or the reduce-only decision. Methods only: all state lives on the Bot
instance (self); no __init__ here. Never imports bot.py.
"""
from collections import defaultdict, deque
from dataclasses import replace
from datetime import timedelta

from mmbot import util
from mmbot import config
from mmbot import exchange
from mmbot import pricing
from mmbot.util import FATAL_API_CODES, PMAX, PMIN, TICK, iso, log, rnd
from mmbot.config import LADDER_MAX_LEVELS, MAX_ORDER_TTL
from mmbot.exchange import ApiError, busy
from mmbot.pricing import others_top


class LadderMixin:

    # ------------------------------------------------------------------------------ fills
    # ------------------------------------------------------------------------------ Harvest ladder, state cap
    HARVEST_KEYS = ("harvest", "state_caps")   # status.json keys adds (absent while unused; identity checks skip)
    HV_LONGSHOT, HV_FAVOURITE = 0.10, 0.90   # the harvest ladder's bands: asks where p <= / bids where p >= these
    HV_TOL = 0.01                 # a level more than this off its target, or p moved more since the re-quote: at once
    HV_KEEP_MARGIN = 60.0         # at a re-quote an order exactly at its target stays if it outlives the next by this
    HV_REFUSED_WAIT = 900.0       # the exchange refused a market's levels: not re-sent there before this (s)

    def p15_init(self):
        """State (this run): the harvest ladder's per-market re-quote state, its fills, and the state cap's
        per-cycle collateral and blocked counters."""
        self.hv_state = {}                        # eid -> {"at": monotonic re-quote time, "p": p then, "side"}
        self.hv_plans = {}                        # this cycle's plans {eid: plan}
        self.hv_why = {}                          # this cycle's reasons a market has no ladder {eid: why}
        self.hv_sides = {}                        # eid -> "ask" / "bid": the ladder's side (decide, ladder_caps)
        self.hv_refused = {}                      # eid -> monotonic time the exchange refused a level there
        self.hv_fills = deque()                   # (wall, shares, edge $ at p, collateral $) of harvest fills, 24 h
        self.hv_totals = {"placed": 0, "pulled": 0}
        self.hv_info = {}                         # status.json "harvest"
        self.st_coll = {}                         # state -> $ of collateral (positions at p + resting locks)
        self.st_own = {}                          # eid -> {is_bid: $ our resting non-harvest orders there lock}
        self.st_blocked = defaultdict(int)        # state -> adds held back by the cap (this run)
        self.st_keys = {}                         # eid -> state (state_of(label), cached)

    # --- the carve-out (harvest_total_usd from alloc_mm_reserve) ---
    def hv_carve_used(self):
        """$ of the reserve the ladder's RESTING levels hold now (the cash they lock, at most harvest_total_usd);
        0 with the ladder off. The refill / spare cash / take reserve count it as reserve (cash + this >= target)."""
        if not getattr(self.cfg, "tilt_harvest_ladder", False):
            return 0.0
        return min(float(self.cfg.harvest_total_usd), self.hv_resting_lock())

    def hv_resting_lock(self):
        return sum(self.resting_lock(o) for o in self.hv_orders()) if self.api.live else 0.0

    def hv_quote_hold(self):
        """$ of free cash the quotes' plan budget leaves to the ladder this cycle: what this cycle's ladder plans lock,
        up to harvest_total_usd, less what its levels already lock (the carve-out is the ladder's: the MM keeps the
        rest of the reserve, never the ladder's part). 0 while the ladder plans nothing more (off, paused, capped)."""
        if not getattr(self.cfg, "tilt_harvest_ladder", False) or not self.hv_plans:
            return 0.0
        want = min(float(self.cfg.harvest_total_usd), sum(pl["carve"] for pl in self.hv_plans.values()))
        return max(0.0, want - self.hv_resting_lock())

    def mm_reserve_effective(self):
        """The cash the MM keeps for itself: alloc_mm_reserve - harvest_total_usd while the ladder is on (the ladder
        never places below it), else alloc_mm_reserve."""
        res = float(self.cfg.alloc_mm_reserve)
        if not getattr(self.cfg, "tilt_harvest_ladder", False):
            return res
        return max(0.0, res - float(self.cfg.harvest_total_usd))

    def hv_carve_fields(self):
        """{"budget", "resting", "free"}: the carve-out (harvest_total_usd) and what the resting levels use."""
        total = float(self.cfg.harvest_total_usd)
        used = self.hv_resting_lock()
        return {"budget": round(total, 2), "resting": round(used, 2), "free": round(max(0.0, total - used), 2)}

    def place_orders_keep(self, orders, keep):
        """place_orders with `keep` $ of the gate's cash held back (the MM's effective reserve): the gate sees that
        much less while it trims / drops these orders. keep 0 or no gate: place_orders as it is."""
        if keep <= 0 or not self.cash_gate_on() or getattr(self, "cg_cash", None) is None:
            return self.place_orders(orders)
        self.cg_spent = getattr(self, "cg_spent", 0.0) + keep
        try:
            return self.place_orders(orders)
        finally:
            self.cg_spent -= keep

    # --- the harvest ladder ---
    def hv_orders(self, eid=None):
        """Our resting harvest-ladder orders (order_meta "harvest" = level + 1), on eid or everywhere."""
        meta = self.order_meta
        return [o for o in list(self.my_orders.values()) if (eid is None or o.eid == eid)
                and (meta.get(o.order_id) or {}).get("harvest")]

    def hv_side_qty(self, eid, is_bid):
        """Shares our resting harvest levels on eid offer on one side (0 without any). the quote's
        reduce-only cap on a laddered market leaves those to the ladder."""
        if not self.hv_sides:
            return 0.0
        return sum(o.qty for o in self.hv_orders(eid) if o.is_bid == is_bid)

    def hv_on(self):
        """Cycle step 6e runs: the flag, a plan / state left, or levels of ours resting (pulled once the flag is
        off)."""
        return bool(getattr(self.cfg, "tilt_harvest_ladder", False) or self.hv_state or self.hv_plans
                    or self.hv_sides or self.hv_info or (self.api.live and self.hv_orders()))

    def hv_plan(self, inv, now_m, skip=()):
        """THE PURE PLANNER: ({eid: plan}, {eid: why}). plan = {"eid", "label", "side": "ask" | "bid", "p", "touch",
        "other", "edge" (at the touch), "levels": [(level, YES price, shares, edge per $, covered shares)], "coll" ($
        of collateral the adds hold at p), "carve" ($ of cash the levels lock)}; why = "soft" (cannot be judged now:
        no fresh book / no touch, traded by another feature this cycle, a write in flight, the touch moved since the
        cached book - resting levels stay) or why the market has no ladder (its levels are pulled). Caps: the market
        (alloc_max_contract_usd at p), the state (state_max_usd), the carve-out (harvest_total_usd of cash locked),
        harvest_max_markets; markets already laddered are served first, then the best edge at the touch."""
        cfg, plans, why = self.cfg, {}, {}
        offs = sorted({round(float(x), 3) for x in (cfg.harvest_offsets or ())})[:LADDER_MAX_LEVELS]
        on = bool(cfg.tilt_harvest_ladder) and bool(offs) and not self.global_reduce
        sl = {o.eid for o in self.sl_orders()}
        cands = []
        for e, ex in sorted(self.ex.items()):
            if not on:
                why[e] = "reduce-only" if self.global_reduce and cfg.tilt_harvest_ladder else "off"
            elif ex.group in cfg.headline_races:
                why[e] = "headline"
            elif e in sl:
                why[e] = "set ladder"
            elif self.hours_to_close(ex) * 60 <= cfg.stop_minutes_before_close:
                why[e] = "stop"
            else:
                p = self.alloc_p(ex, now_m)
                if p is None:
                    why[e] = "no p"
                elif self.HV_LONGSHOT + 1e-12 < p < self.HV_FAVOURITE - 1e-12:
                    why[e] = "middle"
                elif e in skip or ex.group in skip or busy(ex, now_m) or self.hv_top_moved(ex, now_m):
                    why[e] = "soft"
                else:
                    book = self.alloc_fresh_book(ex, now_m)
                    ask_side = p <= self.HV_LONGSHOT + 1e-12
                    touch = ((book or {}).get("asks" if ask_side else "bids") or [None])[0]
                    if touch is None:
                        why[e] = "soft"
                    else:
                        t = touch["price"]
                        cands.append((e, ex, p, ask_side, book, t,
                                      (t - p) / max(1 - t, TICK) if ask_side else (p - t) / max(t, TICK)))
        if not cands:
            return plans, why
        hv = self.hv_orders() if self.api.live else []
        hv_ids = {o.order_id for o in hv}
        laddered = {o.eid for o in hv}
        st_coll, _ = self.st_snapshot(inv, now_m, skip_ids=hv_ids) if self.st_on() else ({}, {})
        rest = list(self.my_orders.values()) if self.api.live else [o for v in self.sim_by_eid().values() for o in v]
        own = defaultdict(list)                   # our other orders, per market (never crossed)
        for o in rest:
            if o.order_id not in hv_ids:
                own[o.eid].append(o)
        carve_left = float(cfg.harvest_total_usd)
        paused = bool(getattr(self, "mmr_paused", False))   # mm_risk_reserve_*: value adds paused -> the
        n = 0                                               #  ladder's levels only sell what is held (covered)
        for e, ex, p, ask_side, book, t, edge0 in sorted(cands, key=lambda c: (c[0] not in laddered, -c[6], c[0])):
            if n >= cfg.harvest_max_markets:
                why[e] = "max markets"
                continue
            o_lv = ((book.get("bids") if ask_side else book.get("asks")) or [None])[0]
            other = o_lv["price"] if o_lv is not None else None   # the other side's best (other traders)
            mine, q = own.get(e, []), float(inv.get(e, 0.0))
            # the market's room (alloc_max_contract_usd: the position at p and our other resting orders' locks)
            room_m = cfg.alloc_max_contract_usd - self.alloc_held_usd(q, p) - sum(self.resting_lock(o) for o in mine)
            st = self.st_key(e) if self.st_on() else None
            room_s = cfg.state_max_usd - st_coll.get(st, 0.0) if st is not None else float("inf")
            if ask_side:                          # YES held and not yet on sale: sold covered first
                free = max(0.0, q) - sum(o.qty for o in mine if not o.is_bid)
                lim = max([o.price for o in mine if o.is_bid], default=None)
            else:                                 # NO held and not yet on sale: a covered "sell NO" (whole levels)
                free = (self.cover_no_qty(e, q) - sum(o.qty for o in mine if o.is_bid and (
                    self.order_meta.get(o.order_id) or {}).get("no_sell"))) if q <= -1 else 0.0
                lim = min([o.price for o in mine if not o.is_bid], default=None)
            free = max(0.0, free)
            # (a quote order of ours resting on the ladder's side at a level's price: that level waits for
            #  it to go - never two of our orders at one price)
            same = {round(o.price, 3) for o in mine if o.is_bid != ask_side}
            levels, used, carve, cut = [], 0.0, 0.0, None
            for k, off in enumerate(offs):
                if ask_side:
                    px = round(t + off, 3)
                    if (px > PMAX + 1e-9 or (other is not None and px <= other + 1e-9)
                            or (lim is not None and px <= lim + 1e-9) or px in same):
                        continue                  # (off the grid / at or through the book or our own bid: skipped)
                    edge, lock, val = (px - p) / max(1 - px, TICK), 1 - px, max(1 - px, 1 - p)
                else:
                    px = round(t - off, 3)
                    if (px < PMIN - 1e-9 or (other is not None and px >= other - 1e-9)
                            or (lim is not None and px >= lim - 1e-9) or px in same):
                        continue
                    edge, lock, val = (p - px) / max(px, TICK), px, max(px, p)
                if edge < cfg.harvest_min_edge - 1e-9:
                    continue
                want = int(cfg.harvest_level_usd / max(lock, TICK) + 1e-9)
                if not ask_side and free >= 1:    # (buying back NO held: cash-free, reduces the collateral)
                    cov = int(min(want, free) + 1e-9)
                    qty = cov
                else:
                    cov = int(min(want, free) + 1e-9) if ask_side else 0
                    caps = {"mm_risk_reserve": 0.0 if paused else float("inf"),   # (tail adds paused)
                            "market_cap": max(0.0, room_m - used) / max(val, TICK),
                            "state_cap": max(0.0, room_s - used) / max(val, TICK),
                            "carve": max(0.0, carve_left - carve) / max(lock, TICK)}
                    add = want - cov
                    for why_k, c in caps.items():
                        if c < add - 1e-9:
                            add, cut = c, why_k
                    qty = cov + int(max(0.0, add) + 1e-9)
                if qty < 1:
                    continue
                free -= cov
                used += (qty - cov) * val
                carve += (qty - cov) * lock
                levels.append((k, px, qty, edge, cov))
            if not levels:
                why[e] = cut or "no edge"
                if cut == "state_cap":
                    self.st_count(ex, "harvest")
                continue
            if cut == "state_cap":
                self.st_count(ex, "harvest")
            if st is not None:
                st_coll[st] = st_coll.get(st, 0.0) + used     # (the state's other markets see this one's levels)
            carve_left -= carve
            plans[e] = {"eid": e, "label": ex.label, "side": "ask" if ask_side else "bid", "p": p, "touch": t,
                        "other": other, "edge": edge0, "levels": levels, "coll": used, "carve": carve}
            n += 1
        return plans, why

    def hv_top_moved(self, ex, now_m):
        """The latest bulk best bid / ask (last_tops) shows this market's top moved since its cached book (not
        re-downloaded yet: max_books_per_cycle), so the cached touch may be stale: no new levels from it this cycle
        (a level planned from it could cross the book). A book confirmed / downloaded since that reading is current."""
        top = (getattr(self, "last_tops", None) or {}).get(ex.eid)
        at = (getattr(self, "last_tops_at", None) or {}).get(ex.eid)
        if top is None or at is None or ex.book is None or ex.verified >= at - 1e-9:
            return False
        mine = ([o for o in self.my_orders.values() if o.eid == ex.eid] if self.api.live
                else self.sim_by_eid().get(ex.eid, []))
        theirs = others_top(tuple(top), mine)     # (a side where our own order is at / better than the top: unknown)
        for k, key in ((0, "bids"), (1, "asks")):
            lv = ex.book.get(key) or []
            if theirs[k] is not None and (not lv or abs(rnd(lv[0]["price"]) - theirs[k]) > 1e-9):
                return True
        return False

    def hv_level_lock(self, side, px, n, cov):
        """Cash a planned level locks: an ask (1 - price) a share beyond the covered part, a bid its price a share
        (a covered "sell NO": 0)."""
        if side == "ask":
            return (1 - px) * max(0.0, n - cov)
        return 0.0 if cov >= n else px * n

    def hv_tick(self, now, inv, mine_real, now_m=None, skip=()):
        """Cycle step 6e: plan (hv_plan); pull a market's levels when it has no plan (unless only "soft") or an
        order that no longer clears (hv_order_ok); re-quote a market when an order is > HV_TOL off its nearest
        target, p moved > HV_TOL or harvest_requote_s passed (an order exactly at a target with life left keeps its
        queue spot); place a missing level (filled / gone) at once. Placements and re-quotes within
        harvest_writes_frac of the writes left (pulls always), batched batch_size orders a write, within the carve-out
        (harvest_total_usd of cash locked) and through the cash gate with the MM's effective reserve held back.
        Returns the exchanges where orders were placed or cancelled (not quoted this cycle)."""
        cfg = self.cfg
        now_m = util.time.monotonic() if now_m is None else now_m
        self.alloc_ages = self.refs.ages() if self.refs is not None and hasattr(self.refs, "ages") else {}
        plans, why = self.hv_plan(dict(inv), now_m, skip)
        self.hv_plans, self.hv_why = plans, why
        meta, touched = self.order_meta, set()
        rest = defaultdict(list)
        for o in self.hv_orders():
            rest[o.eid].append(o)
        # 1. pulls: no plan (unless soft); on a planned market an order on the other side, one whose edge at today's p
        #    no longer clears harvest_min_edge, or one at / through the other side of the book
        for e, os_ in sorted(rest.items()):
            plan = plans.get(e)
            if plan is None:
                if why.get(e) == "soft":
                    continue
                bad = list(os_)
            else:
                bad = [o for o in os_ if not self.hv_order_ok(o, plan)]
            if not bad or not self.writes_ready(len(bad)):
                continue
            if self.cancel(e, bad, whole_exchange=False):
                touched.add(e)
                self.orders_stale = True
                self.hv_totals["pulled"] += len(bad)
                log.warning("HARVEST %s: %d level(s) pulled (%s)", self.ex[e].label if e in self.ex else e, len(bad),
                            why.get(e, "?") if plan is None else "no longer clears")
        for e in [e for e in self.hv_state if e not in plans and why.get(e) != "soft"]:
            self.hv_state.pop(e, None)
        # 2. re-quotes (due) and missing levels. A resting order belongs to the target nearest its price.
        writes = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        budget = cfg.harvest_writes_frac * max(0, writes)
        bs = max(1, int(cfg.batch_size))
        send = []
        tol = self.HV_TOL + 1e-9
        locked = self.hv_resting_lock()           # (the carve-out: what our resting levels lock now)
        for e, plan in sorted(plans.items(), key=lambda kv: (-kv[1]["edge"], kv[0])):
            ex = self.ex[e]
            tg = list(plan["levels"])
            cur = self.hv_orders(e)
            st = self.hv_state.get(e)

            def near(o, tg=tg):
                return min(tg, key=lambda t: (abs(t[1] - o.price), t[1]))
            due = (st is None or abs(plan["p"] - st["p"]) > tol or now_m - st["at"] >= cfg.harvest_requote_s - 1e-9
                   or len(cur) > len(tg) or len({round(o.price, 3) for o in cur}) < len(cur)
                   or any(abs(near(o)[1] - o.price) > tol or o.qty > near(o)[2] + 1e-9 for o in cur))
            if due:
                keep, left = [], list(tg)
                for o in sorted(cur, key=lambda o: o.order_id):
                    t = next((t for t in left if abs(t[1] - o.price) < 1e-9 and abs(t[2] - o.qty) < 1e-9), None)
                    life = (o.expires - now).total_seconds() if o.expires else 0.0
                    if t is not None and life >= cfg.harvest_requote_s + self.HV_KEEP_MARGIN:
                        keep.append(o)            # (exactly at its target: keeps its queue spot)
                        left.remove(t)
                want = left
            else:                                 # (only levels with no order of ours within HV_TOL: filled / gone)
                keep = list(cur)
                want = [t for t in tg if not any(abs(o.price - t[1]) <= tol for o in cur)]
            gone = [o for o in cur if o not in keep]
            if not gone and not want:
                if due:
                    self.hv_state[e] = {"at": now_m, "p": plan["p"], "side": plan["side"]}
                continue
            if not self.api.live:                 # dry run: planned and logged once per re-quote, nothing sent
                if due:
                    self.hv_state[e] = {"at": now_m, "p": plan["p"], "side": plan["side"]}
                    log.info("[dry] HARVEST %s: %s YES (p %.3f, touch %.3f): %s", ex.label, plan["side"], plan["p"],
                             plan["touch"], " ".join(f"{n}@{px:.3f}" for _, px, n, _, _ in plan["levels"]))
                continue
            if want and now_m - self.hv_refused.get(e, -1e18) < self.HV_REFUSED_WAIT and not gone:
                continue                          # (the exchange refused here lately: not re-sent yet)
            # the carve-out: what stays resting + these levels <= harvest_total_usd of cash locked
            after = locked - sum(self.resting_lock(o) for o in gone)
            fit = []
            for lv in want:
                need = self.hv_level_lock(plan["side"], lv[1], lv[2], lv[4])
                if after + need > cfg.harvest_total_usd + 1e-6:
                    continue
                after += need
                fit.append(lv)
            want = fit
            if not gone and not want:
                continue
            cost = len(gone) + len(want) / bs
            if cost > budget + 1e-9:
                continue                          # (this cycle's writes share is spent: next cycle)
            if gone:
                if not self.writes_ready(len(gone)) or not self.cancel(e, gone, whole_exchange=False):
                    continue                      # (never new levels on top of ones not confirmed gone)
                touched.add(e)
                self.orders_stale = True
            locked = after
            budget -= cost
            if due:
                self.hv_state[e] = {"at": now_m, "p": plan["p"], "side": plan["side"]}
            for k, px, n, edge, cov in want:
                o = {"exchangeId": e, "side": "yes", "action": "sell" if plan["side"] == "ask" else "buy",
                     "quantity": int(n), "price": px, "tournamentId": self.tid,
                     "expirationDate": iso(now + timedelta(seconds=MAX_ORDER_TTL))}
                if plan["side"] == "bid" and cov >= n:
                    o["_no_sell"] = True          # (buying back NO held: the covered "sell NO")
                send.append((e, k, edge, plan, o))
        # 3. the batches (several markets a write), through the cash gate with the MM's effective reserve kept
        keep_usd, placed = self.mm_reserve_effective(), defaultdict(list)
        for i in range(0, len(send), bs):
            chunk = send[i:i + bs]
            if not self.writes_ready(1):
                break
            try:
                results = self.place_orders_keep([c[4] for c in chunk], keep_usd)
            except ApiError as err:
                if err.code == "WRITE_BUDGET_WAIT":
                    break
                for e in {c[0] for c in chunk}:
                    self.ex[e].pending_until = now_m + cfg.pending_seconds
                if err.status in (0, 409, 502, 503, 504):   # (outcome unknown - levels may rest: re-read
                    self.orders_stale = True                 #  the list and adopt them as harvest levels, as
                    if cfg.recover_unconfirmed:              #  apply_batch does; never a second ladder on top)
                        for e, k, edge, plan, o in chunk:
                            self.unconfirmed.setdefault(e, []).append((o, {
                                "our_side": "ask" if plan["side"] == "ask" else "bid", "price": o["price"],
                                "harvest": k + 1, "p": round(plan["p"], 4), "edge": round(edge, 4), "t": util.time.time(),
                                **({"no_sell": True} if o.get("_no_sell") else {})}, now_m))
                util.alert(f"harvest ladder orders failed ({err}) - check positions")
                if err.code in FATAL_API_CODES:
                    util.fatal(f"orders rejected with {err.code}")
                continue
            by_index = {r.get("index", j): r for j, r in enumerate(results or []) if isinstance(r, dict)}
            for j, (e, k, edge, plan, o) in enumerate(chunk):
                res = by_index.get(j) or {}
                data = res.get("data") or {}
                if not res.get("ok"):
                    if not res.get("cash_gated"):
                        self.hv_refused[e] = now_m
                        log.info("HARVEST %s level %d refused: %s", plan["label"], k + 1,
                                 (data.get("error") or {}).get("message", "?"))
                    continue                      # (cash gate: tried again next cycle, no write until it fits)
                touched.add(e)
                self.orders_stale = True
                self.remember_order(o, data, now_m)
                self.hv_totals["placed"] += 1
                placed[e].append(f"{o['quantity']}@{o['price']:.3f}")
                if data.get("orderId") is not None:
                    meta[data["orderId"]] = {"our_side": "ask" if plan["side"] == "ask" else "bid", "price": o["price"],
                                             "harvest": k + 1, "eid": e, "p": round(plan["p"], 4),
                                             "edge": round(edge, 4), "t": util.time.time(),
                                             **({"no_sell": True} if o.get("_no_sell") else {})}
                    self.notes_dirty = True
        for e, lv in sorted(placed.items()):
            pl = plans[e]
            log.warning("HARVEST %s: %s YES (p %.3f, touch %.3f, edge at touch %.1f%%): %s", pl["label"],
                        "ask" if pl["side"] == "ask" else "bid", pl["p"], pl["touch"], 100 * pl["edge"], " ".join(lv))
        sides = {e: pl["side"] for e, pl in plans.items()}
        for o in self.hv_orders():
            sides.setdefault(o.eid, "bid" if o.is_bid else "ask")
        self.hv_sides = sides
        on = cfg.tilt_harvest_ladder or sides or self.hv_fills
        self.hv_info = self.hv_status() if on else {}
        return touched

    def hv_order_ok(self, o, plan):
        """A resting harvest order may stay on its planned market: the plan's side, an edge per $ at today's p still >=
        harvest_min_edge, not at / through the other side of the book (other traders' best)."""
        p, ask = plan["p"], plan["side"] == "ask"
        if o.is_bid == ask:
            return False
        edge = (o.price - p) / max(1 - o.price, TICK) if ask else (p - o.price) / max(o.price, TICK)
        if edge < self.cfg.harvest_min_edge - 1e-9:
            return False
        other = plan.get("other")
        return other is None or (o.price > other + 1e-9 if ask else o.price < other - 1e-9)

    def hv_guard_quote(self, ex, q, asks):
        """The quote's bid on a market where our harvest asks rest stays at least a tick below the lowest of
        them (a kept resting bid at / above that is replaced): never a self-cross. None if that is off the grid."""
        low = round(min(o.price for o in asks), 3)
        hi = round(low - TICK, 3)
        if q.bid is None:
            return q
        if q.bid < low - 1e-9:
            if q.bid_limit is not None and q.bid_limit > hi + 1e-9:
                return replace(q, bid_limit=hi)     # (a resting bid at / above the ladder is never kept)
            return q
        if hi < PMIN - 1e-9:
            return replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        return replace(q, bid=hi, bid_limit=min(hi, q.bid_limit) if q.bid_limit is not None else hi)

    def hv_status(self):
        """status.json "harvest": {markets, levels_resting, collateral_resting, filled_24h, edge_filled_24h, carve
        {total, used, free}, blocked_by, ...}."""
        now_w = util.time.time()
        while self.hv_fills and self.hv_fills[0][0] < now_w - 86400:
            self.hv_fills.popleft()
        rest = self.hv_orders() if self.api.live else []
        c = self.hv_carve_fields()
        blocked = defaultdict(int)
        for w in self.hv_why.values():
            if w not in ("off", "middle"):
                blocked[w] += 1
        return {"enabled": bool(getattr(self.cfg, "tilt_harvest_ladder", False)),
                "markets": len({o.eid for o in rest}) if self.api.live else len(self.hv_plans),
                "levels_resting": len(rest),
                "collateral_resting": round(sum(self.resting_lock(o) for o in rest), 2),
                "filled_24h": int(sum(x[1] for x in self.hv_fills) + 1e-9),
                "edge_filled_24h": round(sum(x[2] for x in self.hv_fills), 2),
                "collateral_filled_24h": round(sum(x[3] for x in self.hv_fills), 2),
                "carve": {"total": c["budget"], "used": c["resting"], "free": c["free"]},
                "mm_reserve_effective": round(self.mm_reserve_effective(), 2),
                "held_from_quotes": round(self.hv_quote_hold(), 2),
                "blocked_by": dict(sorted(blocked.items())),
                "planned_markets": len(self.hv_plans),
                "planned_levels": sum(len(pl["levels"]) for pl in self.hv_plans.values()),
                "planned_collateral": round(sum(pl["carve"] for pl in self.hv_plans.values()), 2),
                **{k: int(v) for k, v in self.hv_totals.items()}}

    def hv_note_fills(self, new):
        """log_fills: each new fill of a harvest level -> journal "HARVEST fill ..." and the 24-h tallies (shares, edge
        at p = the market's race-scaled liquid Polymarket price now, collateral)."""
        for f in reversed(new):                   # (oldest first)
            m = self.order_meta.get(f.get("orderId")) or {}
            if not m.get("harvest"):
                continue
            e = str(f.get("exchangeId"))
            qty = abs(float(f.get("quantity") or 0))
            px = float(m.get("price") if m.get("price") is not None else (f.get("price") or 0))
            p = self.ev_p(e)
            bid = m.get("our_side") == "bid"
            edge = 0.0 if p is None else qty * ((p - px) if bid else (px - p))
            self.hv_fills.append((util.time.time(), qty, edge, qty * (px if bid else 1 - px)))
            ex = self.ex.get(e)
            log.warning("HARVEST fill %s: %s %.0f YES @ %.3f (level %s; p %s, edge $%.2f) - a VALUE position, held "
                        "to the outcome", ex.label if ex else e, "bought" if bid else "sold", qty, px, m.get("harvest"),
                        f"{p:.3f}" if p is not None else "?", edge)
        if self.hv_info:
            self.hv_info = self.hv_status()

    def hv_summary(self):
        """" | harvest N mkts, L levels, $Xk resting of $Yk, F filled 24h ($E edge)" (+ " | state caps RI 23.1k/15k,
        ...") for the 2-hourly summary while in use; "" otherwise. Never raises."""
        try:
            parts = []
            h = self.hv_info or {}
            if h:
                parts.append(f"harvest {h.get('markets', 0)} mkts, {h.get('levels_resting', 0)} levels, "
                             f"${h.get('collateral_resting', 0) / 1000:.1f}k resting of "
                             f"${(h.get('carve') or {}).get('total', 0) / 1000:.1f}k, {h.get('filled_24h', 0)} filled "
                             f"24h (${h.get('edge_filled_24h', 0):.0f} edge)")
            if self.st_on():
                over = sorted(((c, s) for s, c in self.st_coll.items() if c > self.cfg.state_max_usd), reverse=True)
                if over:
                    parts.append("state caps " + ", ".join(f"{s} {c / 1000:.1f}k/{self.cfg.state_max_usd / 1000:.0f}k"
                                                           for c, s in over[:4]))
            return " | ".join(parts)
        except (TypeError, ValueError, AttributeError) as e:
            log.warning("summary P15 part failed: %s", e)
            return ""
