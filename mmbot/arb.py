"""
ArbMixin: arbitrage (arb_*, execute_arbitrage), the pair unwinds and follow-ups (pair_*,
nono_unwind_gated) and the stale-quote takes (take_*, execute_take).

Methods only: all state lives on the Bot instance (self); no __init__ here. Never imports bot.py.
"""
from datetime import timedelta

from mmbot import util
from mmbot import exchange
from mmbot import pricing
from mmbot import quoting
from mmbot.util import FATAL_API_CODES, PARTY_SIGN, TICK, iso, log, rnd
from mmbot.exchange import ApiError, busy
from mmbot.pricing import ceil_tick, floor_tick, strip_own
from mmbot.quoting import kelly_position


class ArbMixin:

    # ------------------------------------------------------------------------------ arbitrage
    def take_arbitrage(self, inv, fvs, mine_real, now_m):
        """Guaranteed profit inside a race, and turning held complete sets back into cash.

        A race's group holds every listed party's YES (one exchange each, grouped by race title), and at
        most one of them wins. Checked in this order, at most one action per race per cycle:

        1. Pair unwind (pair_unwind_enabled): long YES on EVERY leg (n = min over legs) and other traders'
           bids add up to >= 1 + pair_unwind_min_profit -> sell up to n sets at those bids. The set pays
           exactly n whatever happens, so selling for >= n is a riskless gain plus freed capital. Mirror: short
           every leg, asks add up to <= 1 - pair_unwind_min_profit -> buy back. Positions only shrink, so
           this also runs in reduce-only and in the pre-close window. A race with an independent leg is a
           set only if that leg is held too (the min is over ALL legs), so a Dem+Rep pair there is left alone.
        2. Sell-side arbitrage (arb_enabled): bids add up to >= 1 + arb_min_profit -> sell YES on all.
           At most one party wins, so for each set sold we pay out at most 1 but collected the bids (> 1).
        3. Buy-side arbitrage (arb_two_sided): asks add up to <= 1 - arb_min_profit_buy -> buy YES on all.
           Pays 1 per set if one LISTED party wins, so not when the asks add up to less than arb_buy_min_sum
           (the market then likely prices an outsider), not in reduce-only (it adds gross positions) and not
           in the pre-close window. The unwinder sells the set later when the bids reach 1.
        Prices are other traders' only (books are stripped of our orders; our quotes there are pulled first).
        Returns the set of races acted on, whose quoting is skipped until next cycle.
        """
        cfg = self.cfg
        done = set()
        if not (cfg.arb_enabled or cfg.pair_unwind_enabled):
            return done
        b_left = int(getattr(cfg, "pair_no_unwind_max_per_cycle", 2))   # Package 7: B-only short-set unwinds
        b_waiting = 0
        for race, members in self.arb_race_order(inv):
            if (len(members) < 2 or not self.running or now_m < self.arb_cooldown.get(race, 0)
                    or race in getattr(self, "pair_owed", ())  # Package 7: its owed legs are evened up first
                    or any(busy(self.ex[e], now_m) for e in members)):
                continue
            # pre-close window: arbitrage would open positions the per-market flatten then pays to unwind;
            # unwinding a held set only reduces them, so it still runs
            closing = any(self.hours_to_close(self.ex[e]) <= self.close_window("flatten_hours_before_close")
                          for e in members)
            plan = self.arb_plan(members, inv, fvs, closing)      # quick check on the cached books
            if plan is None:
                continue
            if getattr(self, "arb_plan_b", False) and b_left < 1:     # Package 7: B's per-cycle cap: waits
                b_waiting += 1
                continue
            # Write budget: cancel our quotes on each leg, one batch, cancel the leftovers on each leg.
            if getattr(self.api, "writes_left", lambda: 10 ** 6)() < 2 * len(members) + 1:
                log.info("arbitrage/unwind on %s deferred: write budget used up", race)
                continue
            # Cached books can be up to book_max_age old: re-download this race before acting on it.
            books = self.in_parallel(lambda e: self.api.book(e, self.tid), members)
            if any(isinstance(b, Exception) for b in books.values()):
                continue
            for e, b in books.items():
                self.ex[e].book = strip_own(b, mine_real.get(e, []))
                self.ex[e].book_time = self.ex[e].verified = util.time.monotonic()
            plan = self.arb_plan(members, inv, fvs, closing)
            if plan is None:
                continue
            if getattr(self, "arb_plan_b", False):
                if b_left < 1:
                    b_waiting += 1
                    continue
                b_left -= 1
            kind, action, levels, qty = plan
            self.arb_cooldown[race] = now_m + (cfg.pair_unwind_cooldown_seconds if kind == "unwind"
                                               else cfg.arb_cooldown_seconds)
            if qty >= 1:
                traded = self.execute_arbitrage(race, members, levels, qty, fvs, now_m, action=action, kind=kind)
                if traded is not None and not any(traded):      # nothing filled: the prices were gone. 4x cooldown
                    self.arb_cooldown[race] = now_m + 4 * (cfg.pair_unwind_cooldown_seconds if kind == "unwind"
                                                           else cfg.arb_cooldown_seconds)
                done.add(race)
        if b_waiting:
            log.info("short-set pair unwinds at a cost (pair_no_unwind_max_cost): %d more race(s) wait for the next "
                     "cycles (pair_no_unwind_max_per_cycle %d)", b_waiting, cfg.pair_no_unwind_max_per_cycle)
        return done

    def arb_race_order(self, inv):
        """The order take_arbitrage visits races in. Unchanged (self.groups order) unless pair_no_unwind_max_cost is in
        effect; then (Package 8) the races not held NO on every leg keep their order and come first (they are not
        under pair_no_unwind_max_per_cycle), and the NO+NO races follow, cheapest first: the cached YES asks' sum
        ascending (no full ask book last), then the capital the sets lock descending (sets x (legs - 1)), then the
        race name - deterministic, so with the per-cycle cap the cheapest sets go first and the rest wait."""
        items = list(self.groups.items())
        if not (self.pair_no_unwind_on() and getattr(self.cfg, "pair_unwind_race_order", False)):
            return items
        head, nono = [], []
        for race, members in items:
            sets = (min(-float(inv.get(e, 0.0)) for e in members)
                    if len(members) >= 2 and all(e in self.ex for e in members) else 0.0)
            if sets < 1:
                head.append((race, members))
                continue
            asks = self.top_levels(members, "asks")
            total = sum(p for p, _ in asks.values()) if asks else float("inf")
            nono.append(((round(total, 6), -int(sets) * (len(members) - 1), str(race)), race, members))
        nono.sort(key=lambda t: t[0])
        return head + [(race, members) for _, race, members in nono]

    def top_levels(self, members, key):
        """{eid: (price, size)} of the best OTHER-trader level ("bids"/"asks") on every leg, or None if a leg has none."""
        out = {}
        for e in members:
            b = self.ex[e].book
            if not b or not b.get(key):
                return None
            out[e] = (b[key][0]["price"], b[key][0]["quantity"])
        return out

    def arb_bids(self, members):
        """{eid: (price, size)} of the best OTHER-trader bid on each party of a race, if those bids
        add up to at least 1 + arb_min_profit. Otherwise None."""
        bids = self.top_levels(members, "bids")
        return bids if bids and sum(p for p, _ in bids.values()) >= 1 + self.cfg.arb_min_profit - 1e-9 else None

    def arb_plan(self, members, inv, fvs, closing):
        """What take_arbitrage would do in one race on the current books: (kind, action, {eid: (price, size)},
        sets) with kind "unwind" or "arb" and action "sell"/"buy", or None."""
        cfg = self.cfg
        bank = self.bankroll()
        bids, asks = self.top_levels(members, "bids"), self.top_levels(members, "asks")
        self.arb_plan_b = False           # Package 7: True = the plan is a short-set unwind only B's threshold allows
        if cfg.pair_unwind_enabled:
            held = [inv.get(e, 0.0) for e in members]
            for sign, levels, action in ((+1, bids, "sell"), (-1, asks, "buy")):
                sets = min(sign * h for h in held)
                if sets < 1 or not levels:
                    continue
                total = sum(p for p, _ in levels.values())
                edge = total - 1 if sign > 0 else 1 - total
                floor = cfg.pair_unwind_min_profit
                nono = sign < 0 and self.pair_no_unwind_on()
                alloc_cost = None
                if nono:
                    # Package 7: a NO+NO set is unwound as a pair even at a small cost (asks <= 1 + max_cost): the
                    # only way to free it without cash (selling one leg breaks the set's collateral)
                    floor = min(floor, -cfg.pair_no_unwind_max_cost)
                    alloc_cost = (getattr(self, "alloc_set_races", None) or {}).get(self.ex[members[0]].group)
                    if alloc_cost is not None:    # Package 10 B3: a set race the allocator registered, at its cost
                        floor = min(floor, -alloc_cost["cost"])
                if edge < floor - 1e-9:   # (buying back a short set below 0.90 only cuts risk)
                    continue
                if nono and getattr(cfg, "pair_no_unwind_asks_le1", False) and self.nono_unwind_gated(
                        members, total, alloc_cost):
                    continue              # Package 12 L3: not while the bids sum > 1 (or the asks above the cost)
                max_sets = int(getattr(cfg, "pair_no_unwind_max_sets", 0) or 0)
                if nono and max_sets > 0:
                    # Package 8: every leg is a covered "sell NO" (no cash locked, 1 - ask received), so the cap is in
                    # SETS per race, not cash per order at the YES ask (0 = off: the old cash cap)
                    caps = [max_sets]
                else:
                    caps = [cfg.pair_unwind_max_frac * bank / max(p, TICK) for p, _ in levels.values()]
                if (alloc_cost is not None and edge < -cfg.pair_no_unwind_max_cost - 1e-9
                        and edge < cfg.pair_unwind_min_profit - 1e-9):
                    caps.append(alloc_cost.get("sets", float("inf")))   # (P10 B3: only the sets the allocator needs)
                qty = int(min([sets] + [size for _, size in levels.values()] + caps))
                slack_kw = {"set_slack": True} if nono and getattr(cfg, "pair_unwind_race_order", False) else {}
                if qty >= 1 and self.unwind_is_safe(inv, fvs, members, -sign * qty, **slack_kw):
                    self.arb_plan_b = edge < cfg.pair_unwind_min_profit - 1e-9
                    return "unwind", action, levels, qty
        return None      # (the arb_enabled sell-all / buy-set branch that followed was removed on simplify: off live)

    def nono_unwind_gated(self, members, asks_sum, alloc_cost=None):
        """Package 12 L3 (pair_no_unwind_asks_le1): True = a NO+NO set race's pair unwind waits - its best asks sum
        above 1 + pair_no_unwind_max_cost (an allocator B3 registration: its own cost, already arb_plan's floor), or
        its best bids (other traders' levels only, arb_levels: a price we bid at is skipped whole) sum above 1 (the
        set is worth more sold leg by leg, L1). A leg with no other trader's bid: the bids do not sum above 1.
        P12 red team RT12-6: also waits (at a cost, asks sum > 1) while our L1 set ladder rests on a leg - it sells
        this set leg by leg, and arb_levels skips its levels (at the best bid) whole, so the bids sum would read low."""
        cfg = self.cfg
        if alloc_cost is None and asks_sum > 1 + max(0.0, cfg.pair_no_unwind_max_cost) + 1e-9:
            return True
        if asks_sum > 1 + 1e-9 and any(self.sl_orders(m) for m in members):
            return True
        bids = self.arb_levels(members, "bids")
        return bool(bids) and sum(p for p, _ in bids.values()) > 1 + 1e-9

    def own_prices(self, eid):
        """{(is_bid, YES price)} of every order of ours on eid we know of: resting (our record), just placed (not yet
        listed) and sent with no answer yet (unconfirmed)."""
        out = {(o.is_bid, rnd(o.price)) for o in list(self.my_orders.values()) if o.eid == eid}
        out |= {(o.is_bid, rnd(o.price)) for o, _t in list(self.recent_orders.values()) if o.eid == eid}
        out |= {(c[0].get("action") == "buy", rnd(float(c[0].get("price", 0.0))))
                for c in (self.unconfirmed.get(eid) or []) if isinstance(c[0], dict)}
        return out

    def arb_levels(self, members, key):
        """F5: top_levels on other traders only, a level at a price where we have (or just sent) an order on that side
        skipped WHOLE (live 3 Oct: own quotes in 29% of the race-cycles at bids >= 1.04 - strip_own only takes off
        the size our record knows of). {eid: (price, size)} or None if a leg has no such level."""
        out = {}
        for e in members:
            b = self.ex[e].book
            mine = self.own_prices(e)
            bid = key == "bids"
            lv = [x for x in ((b or {}).get(key) or []) if (bid, rnd(x["price"])) not in mine]
            if not lv:
                return None
            out[e] = (lv[0]["price"], lv[0]["quantity"])
        return out

    def arb_orders(self, levels, qty, action):
        """The legs execute_arbitrage sends for qty sets (a leg buying back a short: a covered "sell NO" if all fits)."""
        orders = [{"exchangeId": e, "side": "yes", "action": action, "quantity": int(qty), "price": p,
                   "tournamentId": self.tid} for e, (p, _) in levels.items()]
        for o in orders:
            self.no_sell_order(o, self.ex[o["exchangeId"]].inv, whole=True)
        return orders

    def unwind_is_safe(self, inv, fvs, members, delta, set_slack=False):
        """Would adding `delta` YES shares on every leg of a race leave the party delta within its cap (or no
        further past it) and the settlement risk and the worst case no higher? A complete set is riskless,
        so this holds by construction; it guards against the set being part of a hedge the risk logic relies on.
        set_slack (Package 8, a NO+NO buy-back with pair_no_unwind_max_cost in effect and pair_unwind_race_order
        on; arb_plan passes it only then): the worst case is a MARK, so
        when the legs' risk fair values add up to s > 1 buying back d short sets raises it by d x (s - 1) although
        the set pays the same in every outcome; that mark artefact is allowed (the party delta and the settlement
        risk are still checked)."""
        after = dict(inv)
        for e in members:
            after[e] = after.get(e, 0.0) + delta
        slack = 1e-6
        if set_slack and delta > 0 and all(inv.get(e, 0.0) <= -delta + 1e-9 for e in members):
            s = sum(self.risk_fv(e, fvs, members) for e in members)
            slack += delta * max(0.0, s - 1.0)

        def pdelta(pos):
            return sum(PARTY_SIGN.get(ex.party, 0) * pos.get(eid, 0.0) for eid, ex in self.ex.items())

        before_pd, after_pd = pdelta(inv), pdelta(after)
        cap = self.cfg.max_party_delta_frac * self.bankroll()
        if abs(after_pd) > cap and abs(after_pd) > abs(before_pd) + 1e-6:
            return False
        if self.total_worst_case(after, fvs) > self.total_worst_case(inv, fvs) + slack:
            return False
        return self.settlement_risk(after, fvs, after_pd) <= self.settlement_risk(inv, fvs, before_pd) + 1e-6

    def writes_ready(self, n):
        """Main thread: True if n writes can go now without waiting for the write budget or a 429 pause."""
        if not self.api.live:
            return True
        wait_s = getattr(self.api, "write_wait", lambda: 0.0)()
        left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        return wait_s <= self.cfg.write_wait_seconds and left >= n

    def execute_arbitrage(self, race, members, levels, qty, fvs, now_m, action="sell", kind="arb"):
        cfg = self.cfg
        # Writes: a cancel per leg, the batch, a leftover cancel per leg = 2n + 1.
        if not self.writes_ready(2 * len(members) + 1):
            self.arbs_skipped_budget += 1
            log.info("%s on %s skipped: write budget busy (next cycle)", "pair unwind" if kind == "unwind"
                     else "arbitrage", race)
            return
        followup = kind == "unwind" and getattr(cfg, "pair_unwind_followup", False)
        if followup:                              # Package 7: every leg sized to what can fill together
            joint = self.joint_unwind_qty(members, levels, qty, action)
            if joint < 1:
                log.info("pair unwind on %s not sent: the legs' depth at the planned prices does not fill one set "
                         "together", race)
                return
            if joint < qty:
                log.info("pair unwind on %s: sized %d -> %d sets (joint depth at the planned prices)", race, qty, joint)
            qty = joint
        # The legs (the batch), built first: a short-set unwind whose legs cannot ALL go out as covered "sell NO"
        # is not sent at all (Package 7: a half-converted batch breaks the NO+NO set and needs cash). The expiry is
        # set after our quotes are pulled (below).
        orders = [{"exchangeId": e, "side": "yes", "action": action, "quantity": qty, "price": p,
                   "tournamentId": self.tid} for e, (p, _) in levels.items()]
        for o in orders:                          # a leg buying back a short: covered "sell NO" if ALL of it fits
            self.no_sell_order(o, self.ex[o["exchangeId"]].inv, whole=True)
        if (kind == "unwind" and action == "buy" and self.reduce_no_on()
                and (getattr(cfg, "no_set_aware_bids", False) or getattr(cfg, "pair_no_unwind_max_cost", -1.0) >= 0)
                and not all(o.get("_no_sell") for o in orders)):
            self.pair_no_refused = getattr(self, "pair_no_refused", 0) + 1
            log.warning("pair unwind on %s (short set) not sent: not every leg fits as a covered 'sell NO' (%s)",
                        race, ", ".join(f"{self.ex[o['exchangeId']].label} x{o['quantity']} NO held "
                                        f"{-self.ex[o['exchangeId']].inv:.0f}" for o in orders))
            return
        if self.cash_gate_blocks(orders, joint=True):   # Package 8: not one set fits the cash (gate on only)
            self.cash_gate_log(f"race:{race}", "%s on %s not sent: not enough available cash for one set (cash gate)",
                               "pair unwind" if kind == "unwind" else "arbitrage", race)
            return
        total = sum(p for p, _ in levels.values())
        per_set = total - 1 if action == "sell" else 1 - total
        legs = ", ".join(f"{self.ex[e].label} @{p:.3f} x{s:.0f}" for e, (p, s) in levels.items())
        what = (("PAIR UNWIND", "long set" if action == "sell" else "short set") if kind == "unwind"
                else ("ARBITRAGE", "sell side" if action == "sell" else "buy side"))
        log.warning("%s%s %s (%s): %s add up to %.3f (%s) -> %s %d YES on each, %+.4f per set, locking in %+.2f",
                    "" if self.api.live else "[dry] ", what[0], race, what[1], "bids" if action == "sell" else "asks",
                    total, legs, "selling" if action == "sell" else "buying", qty, per_set, per_set * qty)
        if kind == "unwind":
            self.unwinds_total += 1
        else:
            self.arbs_total += 1
        if not self.api.live:
            return
        # 1. Pull our own quotes in this race, so the arbitrage can't trade against ourselves.
        if not all([self.cancel(e, [], whole_exchange=True) for e in members]):
            log.warning("arbitrage on %s abandoned: could not clear our own quotes", race)
            return
        # 2. Trade at exactly those prices. The orders expire within seconds so leftovers can't rest.
        exp = iso(util.utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        for o in orders:
            o["expirationDate"] = exp
        self.orders_stale = True                  # positions and orders change: re-read next cycle
        try:
            results = self.place_orders(orders, joint=True)   # (cash gate: every leg shrinks alike)
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT":             # never sent: nothing can have traded, no hold, no alert
                self.arbs_skipped_budget += 1
                log.warning("%s on %s not sent: write budget busy (our quotes there are re-placed next cycle)",
                            what[0].lower(), race)
                return
            for m in members:                             # outcome unknown: don't pile in again
                self.ex[m].pending_until = now_m + cfg.pending_seconds
            util.alert(f"arbitrage on {race}: placement failed ({e}) - check positions")
            if e.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {e.code}")
            return
        # 3. Cancel whatever didn't fill straight away, then check each leg got the same amount. The
        #    orders go into our record first, so a leftover whose cancel fails is still known about.
        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, o in enumerate(orders):
            if (by_index.get(k) or {}).get("ok"):
                self.remember_order(o, (by_index.get(k) or {}).get("data") or {}, now_m)
        for e in members:
            self.cancel(e, [], whole_exchange=True, quiet=True)
        traded = []
        for k, o in enumerate(orders):
            data = (by_index.get(k) or {}).get("data") or {}
            traded.append(float(data.get("quantityTraded") or 0))
            if data.get("orderId") is not None:           # so these fills show up in `report`
                self.order_meta[data["orderId"]] = {"our_side": "ask" if action == "sell" else "bid",
                                                    "price": o["price"], "arb": True,
                                                    "fv": fvs.get(o["exchangeId"]), "t": util.time.time(),
                                                    "eid": o["exchangeId"],
                                                    **({"no_sell": True} if o.get("_no_sell") else {})}
                self.notes_dirty = True
        if len(set(traded)) > 1 and followup:     # Package 7: the lagging leg(s) owe the difference
            self.pair_owe(race, orders, traded, action, now_m)
        elif len(set(traded)) > 1:
            util.alert(f"arbitrage on {race} only partly filled {traded}: the difference is now ordinary "
                  f"inventory, which the quoting will work off")
        else:
            log.info("arbitrage on %s: every leg filled %.0f", race, traded[0] if traded else 0)
        return traded

    # --- Package 7: pair unwind follow-up (pair_unwind_followup) ---
    @staticmethod
    def depth_within(book, key, limit):
        """Shares on one side of a book ("bids"/"asks", several levels) at or better than limit: asks at <= limit,
        bids at >= limit. 0 without a book."""
        out = 0.0
        for lv in (book or {}).get(key) or []:
            p = lv["price"]
            if (p <= limit + 1e-9) if key == "asks" else (p >= limit - 1e-9):
                out += lv["quantity"]
        return out

    def joint_unwind_qty(self, members, levels, qty, action):
        """A pair unwind's sets sized to what can fill on every leg together: the least of the planned sets, each
        leg's cached book depth at or better than its planned price, and the smaller leg's position (YES held for a
        long-set sale, NO held for a short-set buy-back)."""
        key, sign = ("bids", 1) if action == "sell" else ("asks", -1)
        sizes = [qty]
        for e in members:
            ex = self.ex[e]
            sizes.append(self.depth_within(ex.book, key, levels[e][0]))
            sizes.append(sign * ex.inv)
        return max(0, int(min(sizes) + 1e-9))

    def pair_owe(self, race, orders, traded, action, now_m):
        """After a pair unwind batch whose legs filled unequally: the lagging leg(s) owe (most filled - their fill),
        at their planned price. Evened up by pair_followup_step on the next cycles.
        Short-set buy-back (action "buy"): each lagging leg's owed is capped at its LONE part AFTER the fills (its NO
        held then less the most NO held then on any other leg of the race; a leg without NO counts 0), and a leg whose
        lone part is < 1 is dropped: the rest of its NO is still in a NO+NO set with the others, so a covered sale
        there would break the set (refused at 0 cash) - nothing is owed. ex.inv is still the pre-batch position here
        (positions are re-read next cycle), so the fills are added to it."""
        top = max(traded)
        legs = {o["exchangeId"]: int(round(top - f)) for o, f in zip(orders, traded) if top - f >= 1 - 1e-9}
        if action == "buy" and legs:
            fill = {o["exchangeId"]: f for o, f in zip(orders, traded)}
            members = [m for m in (self.groups.get(self.ex[next(iter(legs))].group) or fill) if m in self.ex]
            no_after = {m: max(0.0, -(self.ex[m].inv + fill.get(m, 0.0))) for m in members}
            capped = {}
            for e, q in legs.items():
                lone = no_after.get(e, 0.0) - max([no_after[m] for m in members if m != e] or [0.0])
                q = int(min(q, lone + 1e-9))
                if q >= 1:
                    capped[e] = q
                else:
                    log.info("pair unwind on %s: %s owes nothing (NO after the fills %.0f, none of it lone: still in a "
                             "NO+NO set)", race, self.ex[e].label, no_after.get(e, 0.0))
            legs = capped
            if not legs:
                log.warning("pair unwind on %s filled %s unequally: nothing owed (no lagging leg has a lone NO part "
                            "after the fills; the rest is still held as NO+NO sets)", race, traded)
                return
        self.pair_owed[race] = {"action": action, "legs": legs, "t": now_m, "tries": 0,
                                "price": {o["exchangeId"]: o["price"] for o in orders}}
        log.warning("pair unwind on %s filled %s unequally: %s owed (follow-up up to %d cycles, at most %.3f a share "
                    "past the planned price; in memory only, a restart drops it)", race, traded,
                    ", ".join(f"{self.ex[e].label} {q}" for e, q in legs.items()),
                    self.cfg.pair_unwind_followup_tries, self.cfg.pair_unwind_followup_max_cost)

    # --- an arbitrage / pair unwind whose legs filled unequally ---
    @staticmethod
    def arb_owed_legs(st):
        """F5: st["legs"] = {eid: shares behind the most-filled leg} (status.json pair_owed, take_arbitrage's skip)."""
        top = max(st["filled"].values())
        st["legs"] = {e: int(round(top - f)) for e, f in st["filled"].items() if top - f >= 1 - 1e-9}

    def arb_followup(self, race, st, fvs, now_m, mine_real=None, inv=None):
        """F5, once a cycle per owed arbitrage: try 1 COMPLETES the set (the arbitrage's own action on each lagging
        leg, at most pair_unwind_followup_max_cost past its planned price); when nothing can complete it then, and
        from try 2 on, it REVERSES the extra (the
        opposite action on each leg filled beyond the least-filled one, at most arb_min_profit (_buy) +
        pair_unwind_followup_max_cost past the planned price: the arbitrage's edge given back, never more). Each:
        immediate-or-cancel orders on fresh books, at most the shares needed (never past the position the arbitrage
        made: no flip), cash-gated, our orders there cancelled first and the leftovers after. Dropped once even
        (logged), or alerted and dropped after pair_unwind_followup_tries tries / pair_unwind_followup_max_age.
        Returns {race} when writes went out (the race is not quoted this cycle)."""
        cfg = self.cfg
        gone = [e for e in st["filled"] if e not in self.ex]
        if gone:
            util.alert(f"arbitrage on {race}: a leg is no longer listed - owed state dropped ({st['legs']})")
            del self.pair_owed[race]
            return set()
        filled = st["filled"]
        if max(filled.values()) - min(filled.values()) < 1 - 1e-9:
            log.warning("arbitrage on %s: legs even (%s) - owed state cleared", race, filled)
            del self.pair_owed[race]
            return set()
        max_age = float(getattr(cfg, "pair_unwind_followup_max_age", 0.0) or 0.0)
        if max_age > 0 and now_m - st.get("t", now_m) >= max_age:
            util.alert(f"arbitrage on {race}: legs not evened within {max_age:.0f} s ({st['tries']} tries; filled "
                  f"{filled}): owed state cleared, now ordinary inventory")
            del self.pair_owed[race]
            return set()
        if not self.running or not self.api.live:
            return set()
        complete = st["tries"] < 1
        if not self.writes_ready(2 * len(filled) + 1):   # our quotes + the orders + leftover cancels, every leg at most
            self.arbs_skipped_budget += 1
            log.info("arbitrage follow-up on %s deferred: write budget busy (next cycle)", race)
            return set()
        st["tries"] += 1
        if inv is not None:                       # this cycle's positions (ex.inv is last cycle's before decide)
            for e in filled:
                self.ex[e].inv = float(inv.get(e, 0.0))
        orders = self.arb_followup_orders(race, st, complete, mine_real)
        if complete and not orders:               # nothing completes the set now: reverse the extra at once
            complete = False
            orders = self.arb_followup_orders(race, st, complete, mine_real)
        acted = set()
        if orders:
            got = self.arb_followup_send(race, orders, complete, fvs, now_m)
            if got is None:                       # never sent (write budget): not a try
                st["tries"] -= 1
                return set()
            acted.add(race)
            for e, g in got.items():
                filled[e] += g if complete else -g
            self.arb_owed_legs(st)
        if max(filled.values()) - min(filled.values()) < 1 - 1e-9:
            log.warning("arbitrage on %s: follow-up evened the legs after %d %s (%s)", race, st["tries"],
                        "try" if st["tries"] == 1 else "tries", "completed" if complete else "reversed")
            del self.pair_owed[race]
        elif st["tries"] >= cfg.pair_unwind_followup_tries:
            util.alert(f"arbitrage on {race} still unequal after {st['tries']} tries (filled {filled}): now ordinary "
                  f"inventory, which the quoting will work off")
            del self.pair_owed[race]
        return acted

    def arb_followup_orders(self, race, st, complete, mine_real=None):
        """F5: the follow-up's orders on fresh books: complete = the arbitrage's action on each lagging leg for what it
        is behind (limit pair_unwind_followup_max_cost past the planned price); else the opposite action on each leg
        for what it is ahead of the least-filled one (limit arb_min_profit (_buy) + that past it). Sized to the
        book's depth within the limit (walked to the worst level needed); a buy that buys back a short goes as a
        covered "sell NO" when all of it fits; cash-gated per order (a leg that does not fit waits)."""
        cfg, filled = self.cfg, st["filled"]
        top, low = max(filled.values()), min(filled.values())
        want = ({e: int(round(top - f)) for e, f in filled.items() if top - f >= 1 - 1e-9} if complete
                else {e: int(round(f - low)) for e, f in filled.items() if f - low >= 1 - 1e-9})
        buy = (st["action"] == "buy") == complete     # completing repeats the arbitrage's action, reversing undoes it
        key = "asks" if buy else "bids"
        edge = cfg.arb_min_profit if st["action"] == "sell" else cfg.arb_min_profit_buy
        slack = cfg.pair_unwind_followup_max_cost + (0.0 if complete else edge)
        orders = []
        for e, n in want.items():
            ex = self.ex[e]
            if not complete:                      # undoing never flips: at most the position the arbitrage made
                n = int(min(n, max(0.0, -ex.inv) if buy else max(0.0, ex.inv)) + 1e-9)
                if n < 1:
                    log.warning("arbitrage follow-up on %s: nothing to reverse on %s (position %+.0f)", race,
                                ex.label, ex.inv)
                    continue
            try:
                ex.book = strip_own(self.api.book(e, self.tid), (mine_real or {}).get(e, []))
                ex.book_time = ex.verified = util.time.monotonic()
            except ApiError as err:
                log.warning("arbitrage follow-up on %s: book download failed (%s) - cached book", race, err)
            planned = st["price"][e]
            limit = floor_tick(planned + slack) if buy else ceil_tick(planned - slack)
            price, depth = None, 0.0
            for lv in (ex.book or {}).get(key) or []:
                if (lv["price"] > limit + 1e-9) if buy else (lv["price"] < limit - 1e-9):
                    break
                if depth >= n:
                    break
                price, depth = lv["price"], depth + lv["quantity"]
            qty = int(min(n, depth) + 1e-9)
            if qty < 1 or price is None:
                log.warning("arbitrage follow-up on %s (%s): nothing on %s within %.3f (planned %.3f), try %d of %d",
                            race, "complete" if complete else "reverse", ex.label, limit, planned, st["tries"],
                            cfg.pair_unwind_followup_tries)
                continue
            order = {"exchangeId": e, "side": "yes", "action": "buy" if buy else "sell", "quantity": qty,
                     "price": price, "tournamentId": self.tid}
            if buy:
                self.no_sell_order(order, ex.inv, whole=True)   # a covered "sell NO" when all of it fits
            orders.append(order)
        if orders and self.cash_gate_on():
            fits = [o for o in orders if not self.cash_gate_blocks([o])]
            for o in orders:
                if o not in fits:
                    self.cash_gate_log(o["exchangeId"], "arbitrage follow-up on %s: %s not sent - not enough "
                                       "available cash (cash gate)", race, self.ex[o["exchangeId"]].label)
            orders = fits
        return orders

    def arb_followup_send(self, race, orders, complete, fvs, now_m):
        """F5: the follow-up batch: our orders on those legs cancelled, the orders (alive arb_order_ttl), the leftovers
        cancelled. {eid: shares filled}, or None if never sent (write budget)."""
        cfg = self.cfg
        if not all([self.cancel(o["exchangeId"], [], whole_exchange=True) for o in orders]):
            log.warning("arbitrage follow-up on %s: could not clear our own quotes - next cycle", race)
            return {}
        exp = iso(util.utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        for o in orders:
            o["expirationDate"] = exp
        log.warning("ARBITRAGE follow-up %s (%s): %s", race, "complete" if complete else "reverse", ", ".join(
            f"{'buying' if o['action'] == 'buy' else 'selling'} {o['quantity']} YES on {self.ex[o['exchangeId']].label}"
            f" at {o['price']:.3f}{' (sell NO)' if o.get('_no_sell') else ''}" for o in orders))
        self.orders_stale = True
        try:
            results = self.place_orders(orders)
        except ApiError as err:
            if err.code == "WRITE_BUDGET_WAIT":
                self.arbs_skipped_budget += 1
                return None
            for o in orders:
                self.ex[o["exchangeId"]].pending_until = now_m + cfg.pending_seconds
            util.alert(f"arbitrage follow-up on {race}: placement failed ({err}) - check positions")
            if err.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {err.code}")
            return {}
        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, o in enumerate(orders):
            if (by_index.get(k) or {}).get("ok"):
                self.remember_order(o, (by_index.get(k) or {}).get("data") or {}, now_m)
        for o in orders:
            self.cancel(o["exchangeId"], [], whole_exchange=True, quiet=True)
        got = {}
        for k, o in enumerate(orders):
            res = by_index.get(k) or {}
            data = res.get("data") or {}
            if not res.get("ok"):
                log.warning("arbitrage follow-up on %s: %s refused (%s)", race, self.ex[o["exchangeId"]].label,
                            (data.get("error") or {}).get("message") or data.get("error"))
            got[o["exchangeId"]] = float(data.get("quantityTraded") or 0) if res.get("ok") else 0.0
            if data.get("orderId") is not None:
                self.order_meta[data["orderId"]] = {"our_side": "bid" if o["action"] == "buy" else "ask",
                                                    "price": o["price"], "arb": True, "fv": fvs.get(o["exchangeId"]),
                                                    "t": util.time.time(), "eid": o["exchangeId"],
                                                    **({"no_sell": True} if o.get("_no_sell") else {})}
                self.notes_dirty = True
        return got

    def pair_owed_status(self):
        """status.json pair_owed: {race: shares still owed (summed over its lagging legs)}."""
        return {r: int(sum(st["legs"].values())) for r, st in getattr(self, "pair_owed", {}).items()}

    def pair_followup_step(self, fvs, now_m, mine_real=None, inv=None):
        """Once a cycle, before take_arbitrage: per race with owed legs, one immediate-or-cancel order on each lagging
        leg (pair_followup_take). The owed shares shrink by what fills; the state is dropped once even, or after
        pair_unwind_followup_tries cycles that sent (or tried to send) the follow-up, with one alert of what is left.
        A cycle the write budget defers is not a try. Returns the races acted on (not quoted this cycle).
        Package 9 F5: an arbitrage's owed record (kind "arb") goes to arb_followup (inv: this cycle's positions)."""
        cfg, acted = self.cfg, set()
        for race in list(self.pair_owed):
            st = self.pair_owed[race]
            if st.get("kind") == "arb":
                acted |= self.arb_followup(race, st, fvs, now_m, mine_real, inv)
                continue
            for e in [e for e in st["legs"] if e not in self.ex]:   # a leg gone from the market list: dropped
                log.warning("pair unwind follow-up on %s: %s owed on a market no longer listed - dropped", race,
                            st["legs"].pop(e))
            lag = [e for e, q in st["legs"].items() if q >= 1]
            if not lag:
                del self.pair_owed[race]
                continue
            max_age = float(getattr(cfg, "pair_unwind_followup_max_age", 0.0) or 0.0)
            if max_age > 0 and now_m - st.get("t", now_m) >= max_age:   # Package 8 (0 = off): never blocks for ever
                owed = ", ".join(f"{self.ex[e].label} {st['legs'][e]}" for e in lag)
                util.alert(f"pair unwind on {race}: owed legs not evened up within {max_age:.0f} s ({st['tries']} tries; "
                      f"{owed}): owed state cleared, now ordinary inventory, which the quoting will work off")
                del self.pair_owed[race]
                continue
            if not self.running or not self.api.live:
                continue
            if not self.writes_ready(2 * len(lag) + 1):   # pull our quotes + the orders + leftover cancels
                self.arbs_skipped_budget += 1
                log.info("pair unwind follow-up on %s deferred: write budget busy (next cycle)", race)
                continue
            st["tries"] += 1
            st["sent"], st["cleared"] = False, []
            got = self.pair_followup_take(race, st, lag, fvs, now_m, mine_real)
            sent, cleared = st.pop("sent", False), st.pop("cleared", [])
            if sent:                              # writes went out: the race is not quoted this cycle
                acted.add(race)
            if got is None:                       # never sent (write budget): not a try
                st["tries"] -= 1
                continue
            if cleared and not sent and len(cleared) == len(lag):
                st["tries"] -= 1                  # every lagging leg cleared (all its NO in a set): not a try
            for e, g in got.items():
                st["legs"][e] = max(0, int(round(st["legs"][e] - g)))
            left = sum(st["legs"].values())
            if left < 1 and cleared and not got:
                log.warning("pair unwind follow-up on %s: nothing left that can be sent (the lagging legs' NO is in "
                            "NO+NO sets) - owed state cleared", race)
                del self.pair_owed[race]
            elif left < 1:
                log.warning("pair unwind on %s: follow-up evened the legs after %d %s", race, st["tries"],
                            "try" if st["tries"] == 1 else "tries")
                del self.pair_owed[race]
            elif st["tries"] >= cfg.pair_unwind_followup_tries:
                util.alert(f"pair unwind on {race} left {left} shares unpaired after {st['tries']} tries "
                      f"({', '.join(f'{self.ex[e].label} {q}' for e, q in st['legs'].items() if q >= 1)}): "
                      f"now ordinary inventory, which the quoting will work off")
                del self.pair_owed[race]
        return acted

    def pair_followup_take(self, race, st, lag, fvs, now_m, mine_real=None):
        """One immediate-or-cancel order per lagging leg for what it owes, at a limit up to
        pair_unwind_followup_max_cost past its planned price (short-set buy-back: buy YES at the asks, a covered
        "sell NO" with reduce_no_as_sell; long-set sale: sell YES at the bids), sized to the fresh book's depth within
        that limit (never more than held). Our quotes on those legs are pulled first, leftovers cancelled after.
        Returns {eid: shares filled}, or None if the batch was never sent (write budget)."""
        cfg = self.cfg
        buy = st["action"] == "buy"
        key = "asks" if buy else "bids"
        orders = []
        for e in lag:
            ex = self.ex[e]
            try:                                  # the cached book may be old
                ex.book = strip_own(self.api.book(e, self.tid), (mine_real or {}).get(e, []))
                ex.book_time = ex.verified = util.time.monotonic()
            except ApiError as err:
                log.warning("pair unwind follow-up on %s: book download failed (%s) - cached book", race, err)
            planned = st["price"][e]
            limit = (floor_tick(planned + cfg.pair_unwind_followup_max_cost) if buy
                     else ceil_tick(planned - cfg.pair_unwind_followup_max_cost))
            held = -ex.inv if buy else ex.inv
            want = int(min(st["legs"][e], max(0.0, held) + 1e-9))
            # walk the book to the worst level needed (never past the limit): the order's price
            price, depth = None, 0.0
            for lv in (ex.book or {}).get(key) or []:
                if (lv["price"] > limit + 1e-9) if buy else (lv["price"] < limit - 1e-9):
                    break
                if depth >= want:
                    break
                price, depth = lv["price"], depth + lv["quantity"]
            qty = int(min(want, depth))
            if qty < 1 or price is None:
                log.warning("pair unwind follow-up on %s: nothing on %s within %.3f (planned %.3f) - %d owed, "
                            "try %d of %d", race, ex.label, limit, planned, st["legs"][e], st["tries"],
                            cfg.pair_unwind_followup_tries)
                continue
            order = {"exchangeId": e, "side": "yes", "action": "buy" if buy else "sell", "quantity": qty,
                     "price": price, "tournamentId": self.tid}
            if buy:                               # a covered "sell NO" of the lone NO left on this leg
                order = self.no_sell_order(order, ex.inv)
                if order is None:                 # no lone NO left there: nothing can ever be sent - leg cleared
                    log.warning("pair unwind follow-up on %s: the NO on %s is all in a NO+NO set - not sent, %d owed "
                                "there cleared", race, ex.label, st["legs"][e])
                    st["legs"][e] = 0
                    st.setdefault("cleared", []).append(e)
                    continue
            orders.append(order)
        if orders and self.cash_gate_on():        # Package 8: a leg none of which fits the cash waits (a try)
            fits = [o for o in orders if not self.cash_gate_blocks([o])]
            for o in orders:
                if o not in fits:
                    self.cash_gate_log(o["exchangeId"], "pair unwind follow-up on %s: %s not sent - not enough "
                                       "available cash (cash gate)", race, self.ex[o["exchangeId"]].label)
            orders = fits
        if not orders:
            return {}
        st["sent"] = True                         # from here on writes go out (cancels, the batch)
        if not all([self.cancel(o["exchangeId"], [], whole_exchange=True) for o in orders]):
            log.warning("pair unwind follow-up on %s: could not clear our own quotes - next cycle", race)
            return {}
        exp = iso(util.utcnow() + timedelta(seconds=cfg.arb_order_ttl))
        for o in orders:
            o["expirationDate"] = exp
        log.warning("PAIR UNWIND follow-up %s: %s", race, ", ".join(
            f"{'buying' if buy else 'selling'} {o['quantity']} YES on {self.ex[o['exchangeId']].label} at "
            f"{o['price']:.3f}{' (sell NO)' if o.get('_no_sell') else ''}" for o in orders))
        self.orders_stale = True
        try:
            results = self.place_orders(orders)
        except ApiError as err:
            if err.code == "WRITE_BUDGET_WAIT":
                self.arbs_skipped_budget += 1
                return None
            for o in orders:
                self.ex[o["exchangeId"]].pending_until = now_m + cfg.pending_seconds
            util.alert(f"pair unwind follow-up on {race}: placement failed ({err}) - check positions")
            if err.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {err.code}")
            return {}
        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, o in enumerate(orders):
            if (by_index.get(k) or {}).get("ok"):
                self.remember_order(o, (by_index.get(k) or {}).get("data") or {}, now_m)
        for o in orders:
            self.cancel(o["exchangeId"], [], whole_exchange=True, quiet=True)
        got = {}
        for k, o in enumerate(orders):
            res = by_index.get(k) or {}
            data = res.get("data") or {}
            if not res.get("ok"):
                log.warning("pair unwind follow-up on %s: %s refused (%s)", race, self.ex[o["exchangeId"]].label,
                            (data.get("error") or {}).get("message") or data.get("error"))
            got[o["exchangeId"]] = float(data.get("quantityTraded") or 0)
            if data.get("orderId") is not None:
                self.order_meta[data["orderId"]] = {"our_side": "bid" if buy else "ask", "price": o["price"],
                                                    "arb": True, "fv": fvs.get(o["exchangeId"]), "t": util.time.time(),
                                                    "eid": o["exchangeId"],
                                                    **({"no_sell": True} if o.get("_no_sell") else {})}
                self.notes_dirty = True
        return got

    # ------------------------------------------------------------------------------ taking stale quotes
    def take_stale_quotes(self, refs, liquid, fvs, inv, mine_real, global_reduce, party_delta, now_m):
        """Trade against a tournament quote that Polymarket says is clearly wrong.

        On every new Polymarket reading, each market with a liquid Polymarket price gets a direction:
          +1  Polymarket is at least take_edge ABOVE the best other ask  -> that ask is cheap: buy it
          -1  Polymarket is at least take_edge BELOW the best other bid  -> that bid is rich: sell to it
        Only once every reading for take_confirm_seconds has shown the same direction (a spike that
        reverts never counts), the book is re-downloaded and, if the gap is still there, we trade.
        Size = quarter Kelly (see kelly_position), limited to what's offered at that price and to the
        cash-per-order cap. Never inside the pre-close window, when the worst-case cap is hit, or
        against the national-swing cap. Returns the exchanges traded (skipped by quoting this cycle).
        """
        cfg = self.cfg
        taken = set()
        version = getattr(self.refs, "version", 0)
        if not cfg.take_enabled or not self.refs or version == self.take_version_seen:
            return taken
        self.take_version_seen = version
        ages = self.refs.ages() if hasattr(self.refs, "ages") else {}
        for eid, ex in self.ex.items():
            p = refs.get(eid)
            if p is not None and 0 < cfg.take_ref_max_age_seconds < ages.get(f"{ex.group}|{ex.party}", 0.0):
                p = None                                  # an old price, kept through failed downloads: not evidence
            direction = self.take_direction(ex, p) if (p is not None and eid in liquid) else 0
            if direction != ex.take_dir:
                ex.take_since = now_m                     # new direction (or none): the clock starts again
            ex.take_dir = direction
        for eid, ex in self.ex.items():
            if (not self.running or not ex.take_dir or now_m - ex.take_since < cfg.take_confirm_seconds
                    or now_m < ex.take_until
                    or busy(ex, now_m) or global_reduce
                    or self.hours_to_close(ex) <= self.close_window("flatten_hours_before_close")):
                continue
            if not self.writes_ready(3):          # cancel + take + leftover cancel, on the main thread
                continue                          # (the direction stays confirmed: taken once the budget frees)
            no_bid, no_ask = self.party_blocks(ex, party_delta)
            if (ex.take_dir > 0 and no_bid) or (ex.take_dir < 0 and no_ask):
                continue
            try:                                          # the cached book may be old: check it's still there
                ex.book = strip_own(self.api.book(eid, self.tid), mine_real.get(eid, []))
                ex.book_time = ex.verified = util.time.monotonic()
            except ApiError as e:
                log.warning("take on %s skipped: book download failed (%s)", ex.label, e)
                continue
            p = refs[eid]                                 # THIS market's Polymarket price (not the first loop's last)
            if self.take_direction(ex, p) != ex.take_dir:
                ex.take_dir = 0                           # the gap has closed: nothing to take
                continue
            if self.execute_take(ex, p, inv.get(eid, 0.0), fvs.get(eid), now_m):
                taken.add(eid)
        return taken

    def take_direction(self, ex, p):
        """+1 / -1 / 0: is Polymarket's probability p at least take_edge past the best other ask / bid?"""
        b = ex.book or {}
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        if best_ask is not None and p - best_ask >= self.cfg.take_edge - 1e-9:
            return 1
        if best_bid is not None and best_bid - p >= self.cfg.take_edge - 1e-9:
            return -1
        return 0

    def execute_take(self, ex, p, inv, fv, now_m):
        """Buy the stale best ask (direction +1) or sell to the stale best bid (-1). True if an order was sent."""
        cfg, bank = self.cfg, self.bankroll()
        buy = ex.take_dir > 0
        level = ex.book["asks" if buy else "bids"][0]
        price = level["price"]
        limit = kelly_position(p, price, bank, cfg, yes=buy)             # most we'd hold on this side
        room = limit - inv if buy else limit + inv                       # how much more that allows
        # Tail guard, same rule as for quotes: near 0 or 1 never take the side that risks ~1 a share to earn
        # a few cents (buying YES near 1, or selling YES = buying NO near 0), except to shrink a position.
        if fv is not None and ((buy and fv > cfg.tail_high) or (not buy and fv < cfg.tail_low)):
            room = min(room, max(0.0, -inv) if buy else max(0.0, inv))
        cost = price if buy else 1 - price
        qty = int(min(level["quantity"], room, cfg.max_order_cash_frac * bank / max(cost, TICK)))
        if getattr(self, "mmr_paused", False) and qty >= 1:   # P12 ops mm_risk_reserve_*: value adds paused -
            cut = int(min(qty, max(0.0, -inv) if buy else max(0.0, inv)))   # only what shrinks the position here
            if cut < 1:
                self.mm_risk_count("takes")
                log.info("take on %s skipped: value adds paused (mm_risk_reserve)", ex.label)
            qty = cut
        if self.st_on() and qty >= 1:             # P15 state_max_usd: the adding part only within the state's room
            red = int(min(qty, max(0.0, -inv) if buy else max(0.0, inv)))
            add = self.st_add_room(ex, buy, qty - red)
            if red + add < qty:
                self.st_count(ex, "take")
                if red + add < 1:
                    log.info("take on %s skipped: the state cap (state_max_usd) is reached", ex.label)
            qty = red + add
        if qty >= 1 and not self.writes_ready(3):
            # Checked BEFORE pulling our own quote (cancel + take + leftover cancel): a take that can't be sent
            # must not leave the market unquoted. The direction stays confirmed: taken once the budget frees.
            self.takes_skipped_budget += 1
            log.info("take on %s skipped: write budget busy (next cycle)", ex.label)
            return False
        ex.take_until = now_m + cfg.take_cooldown_seconds
        ex.take_dir = 0                                                  # a new gap must be confirmed afresh
        if qty < 1:
            return False
        if buy and self.set_blocked(ex.eid, ex.inv):       # Package 7: all NO here in a NO+NO set: nothing to send
            log.info("take on %s skipped: all its NO is in a NO+NO set (no_set_aware_bids)", ex.label)
            return False
        if self.cash_gate_on():                   # Package 8: nothing of it fits the cash -> our quote stays put
            prov = self.no_sell_order({"exchangeId": ex.eid, "side": "yes", "action": "buy" if buy else "sell",
                                       "quantity": qty, "price": price, "tournamentId": self.tid}, ex.inv)
            if prov is not None and self.cash_gate_blocks([prov]):
                self.cash_gate_log(ex.eid, "take on %s skipped: not enough available cash (cash gate)", ex.label)
                return False
            if prov is not None and self.take_blocked_by_reserve(prov):
                self.cash_gate_log(ex.eid, "take on %s skipped: it would spend the market-making reserve (take_respect_reserve)",
                                   ex.label)
                return False
        log.warning("%sTAKE %s: Polymarket %.3f vs stale %s %.3f -> %s %d YES at %.3f",
                    "" if self.api.live else "[dry] ", ex.label, p, "ask" if buy else "bid", price,
                    "buying" if buy else "selling", qty, price)
        self.takes_total += 1
        if not self.api.live:
            return True
        # Pull our own quotes here first (so we can't trade with ourselves), then take, then cancel leftovers.
        if not self.cancel(ex.eid, [], whole_exchange=True):
            return False
        order = self.no_sell_order({"exchangeId": ex.eid, "side": "yes", "action": "buy" if buy else "sell",
                                    "quantity": qty, "price": price, "tournamentId": self.tid,
                                    "expirationDate": iso(util.utcnow() + timedelta(seconds=cfg.take_order_ttl))}, ex.inv)
        self.orders_stale = True
        if order is None:                         # Package 7: all NO here is in a NO+NO set (no_set_aware_bids)
            self.takes_total -= 1
            log.warning("take on %s not sent: all its NO is in a NO+NO set (our quote there is re-placed next cycle)",
                        ex.label)
            return True
        try:
            results = self.place_orders([order])
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT":             # never sent: no hold, no alert; quotes back next cycle
                self.takes_skipped_budget += 1
                self.takes_total -= 1
                log.warning("take on %s not sent: write budget busy (our quote there is re-placed next cycle)",
                            ex.label)
                return True
            ex.pending_until = now_m + cfg.pending_seconds
            util.alert(f"take on {ex.label} failed ({e}) - check positions")
            if e.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {e.code}")
            return True
        data = (results[0] if results else {}).get("data") or {}
        if (results[0] if results else {}).get("ok"):
            self.remember_order(order, data, now_m)   # so a leftover whose cancel fails is still known about
        self.cancel(ex.eid, [], whole_exchange=True, quiet=True)
        if data.get("orderId") is not None:                # so these fills show up in `report`
            self.order_meta[data["orderId"]] = {"our_side": "bid" if buy else "ask", "price": price, "take": True,
                                                "fv": fv, "t": util.time.time(), "eid": ex.eid,
                                                **({"no_sell": True} if order.get("_no_sell") else {})}
            self.notes_dirty = True
        log.info("take on %s: traded %s of %d", ex.label, data.get("quantityTraded", "?"), order["quantity"])
        return True
