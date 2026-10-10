"""
OpsMixin: running the process - everything around the cycle rather than inside it.

Owns the run loop and its pacing (realtime feed or fixed interval), the wait for the open, the
feed start, the watchdog thread, the start-up self-tests (selftest_*, nosell_*, pairno_*: one
small real order each to prove the account can trade before the quotes go out), request_stop /
request_handover (Ctrl+C cancels everything; SIGUSR1 leaves the quotes resting for the next
version to adopt), adopt_handover, close and shutdown.

It never decides a price, a size or which market to trade; the self-test legs are the only orders
it sends, and they never stop the bot when they fail. Methods only: all state lives on the Bot
instance (self); no __init__ here. Never imports bot.py.
"""
import json
import logging.handlers
import os
import signal
import sys
import threading
import traceback
from collections import defaultdict
from concurrent.futures import wait
from datetime import timedelta

from mmbot import util
from mmbot import exchange
from mmbot import pricing
from mmbot.util import (
    EXIT_KILLED, EXIT_WATCHDOG, FATAL_API_CODES, PMAX, PMIN, bot_path, iso, log, parse_ts, write_json,
)
from mmbot.exchange import ApiError, RealtimeFeed, busy
from mmbot.pricing import Resting, parse_order


class OpsMixin:

    # ------------------------------------------------------------------------------ dry-run helpers
    def sim_by_eid(self):
        out = defaultdict(list)
        for o in list(self.sim.values()):
            if o.expires and o.expires <= util.utcnow():
                del self.sim[o.order_id]                      # simulated expiry, like the real engine
            else:
                out[o.eid].append(o)
        return out

    def sim_orders(self, eid):
        return [o for o in self.sim.values() if o.eid == eid]

    # ------------------------------------------------------------------------------ main loop
    def request_stop(self, *_):
        """Ctrl+C / kill only raises a flag. The loop stops touching the book, then shutdown()
        cancels everything. Cancelling inside the handler would race the cycle, which could post
        fresh orders right after the cancel. A second Ctrl+C forces an immediate exit."""
        if not self.running and not self.handover:
            raise KeyboardInterrupt
        self.handover = False             # a plain stop always cancels, even after a handover request
        log.info("stop requested - finishing the current request, then cancelling all orders (Ctrl+C again to force)")
        self.running = False

    def request_handover(self, *_):
        """SIGUSR1 (deploy): stop like Ctrl+C but leave our quotes resting, so the new version can adopt them
        (see adopt_handover) instead of the market going unquoted for the restart. They expire within order_ttl
        anyway, so a new version that never starts leaves nothing behind for long."""
        log.info("handover requested - stopping without cancelling; the next start adopts the resting orders")
        self.handover = True
        self.running = False
        cap = self.cfg.handover_exit_max_seconds
        if cap > 0 and self.handover_timer is None:
            self.handover_timer = threading.Timer(cap, self.handover_deadline, args=(cap,))
            self.handover_timer.daemon = True
            self.handover_timer.start()

    def handover_deadline(self, cap):
        """Timer thread, handover_exit_max_seconds after a handover request: if the process is still here, exit
        now, so the deploy script (which waits for the old bot to stop) can start the new one. Only while it is
        still a handover (a plain stop or the kill switch since then must cancel; they're never cut short)."""
        if not (self.handover and self.exit_code == 0):
            return
        log.error("handover: still running %.0f s after the request - exiting now (writes still in flight are "
                  "abandoned; the next run re-reads the open-orders list)", cap)
        for h in logging.getLogger().handlers + log.handlers:
            try:
                h.flush()
            except Exception:
                pass
        self.hard_exit(0)

    hard_exit = staticmethod(os._exit)    # no interpreter clean-up: it would wait for the writer threads

    # ------------------------------------------------------------------------------ watchdog
    def start_watchdog(self):
        if self.watchdog_thread is None:
            self.watchdog_thread = threading.Thread(target=self.watchdog_loop, name="watchdog", daemon=True)
            self.watchdog_thread.start()

    def watchdog_loop(self):
        while self.running:
            try:
                self.watchdog_check(util.time.monotonic())
            except Exception:                     # the watchdog itself must never die quietly
                log.exception("watchdog check failed")
            util.time.sleep(5.0)

    def watchdog_check(self, now_m):
        """Watchdog thread: alert when no cycle has completed for watchdog_alert_seconds; after
        watchdog_exit_seconds dump every thread's stack, cancel everything (bounded) and exit with EXIT_WATCHDOG
        so systemd restarts the bot. Returns "alert" / "exit" / None (tests)."""
        cfg = self.cfg
        ref = self.last_cycle_done if self.last_cycle_done is not None else self.trading_since
        if ref is None or not self.running:
            return None
        since = now_m - ref
        if since < 1:
            self.watchdog_alerted = False
        if cfg.watchdog_exit_seconds > 0 and since >= cfg.watchdog_exit_seconds:
            self.watchdog_exit(since)
            return "exit"
        if cfg.watchdog_alert_seconds > 0 and since >= cfg.watchdog_alert_seconds and not self.watchdog_alerted:
            self.watchdog_alerted = True
            util.alert(f"WATCHDOG: no cycle completed for {since:.0f} s (phase: {self.health.get('cycle_phase', '?')}, "
                  f"429 pause {self.api.pause_left() if hasattr(self.api, 'pause_left') else 0:.0f} s left)"
                  + (f" - exiting for a restart at {cfg.watchdog_exit_seconds:.0f} s" if cfg.watchdog_exit_seconds else ""))
            return "alert"
        if since < cfg.watchdog_alert_seconds:
            self.watchdog_alerted = False
        return None

    def watchdog_exit(self, since):
        log.critical("WATCHDOG: no cycle completed for %.0f s - thread stacks follow, then cancel-all and exit %d",
                     since, EXIT_WATCHDOG)
        try:
            frames = sys._current_frames()
            names = {t.ident: t.name for t in threading.enumerate()}
            for ident, frame in frames.items():
                log.critical("thread %s:\n%s", names.get(ident, ident), "".join(traceback.format_stack(frame)[-8:]))
        except Exception:
            pass
        util.alert(f"WATCHDOG: no cycle for {since:.0f} s - cancelling everything and exiting (exit {EXIT_WATCHDOG}, "
              f"systemd restarts the bot)")
        if self.api.live:
            done = threading.Event()

            def cancel():
                try:
                    self.api.cancel_all(self.tid)
                    log.critical("watchdog: cancel-all sent")
                except Exception as e:
                    log.critical("watchdog: cancel-all failed: %s", e)
                done.set()
            threading.Thread(target=cancel, name="watchdog-cancel", daemon=True).start()
            if not done.wait(self.cfg.watchdog_cancel_seconds):
                log.critical("watchdog: cancel-all still running after %g s - exiting anyway (orders expire "
                             "within %.0f min)", self.cfg.watchdog_cancel_seconds, self.cfg.order_ttl / 60)
        for h in logging.getLogger().handlers + log.handlers:
            try:
                h.flush()
            except Exception:
                pass
        self.hard_exit(EXIT_WATCHDOG)

    def adopt_handover(self):
        """At start: True if the previous run handed over recently (its orders are ours to manage, not cancel)."""
        path = bot_path(self.cfg.handover_file)
        try:
            with open(path) as f:
                info = json.load(f)
            os.remove(path)
        except (OSError, ValueError):
            return False
        age = util.time.time() - float(info.get("t", 0))
        if not 0 <= age <= self.cfg.handover_max_age:
            log.info("handover file is %.0f s old - too old, starting from a clean slate", age)
            return False
        log.info("handover: adopting the %s orders the previous run left resting", info.get("orders", "?"))
        now_m = util.time.monotonic()
        for r in info.get("resting") or []:
            try:
                o = Resting(int(r["id"]), str(r["eid"]), bool(r["bid"]), float(r["price"]), float(r["qty"]),
                            parse_ts(r.get("expires")))
            except (KeyError, TypeError, ValueError):
                continue
            self.my_orders[o.order_id] = o
            self.recent_orders[o.order_id] = (o, now_m)      # trusted over a lagging list for the grace period
            if r.get("placed"):
                self.placed_qty[o.order_id], self.filled_qty[o.order_id] = float(r["placed"]), float(r["placed"]) - o.qty
        for oid in info.get("recent_cancels") or []:
            self.recent_cancels[int(oid)] = now_m
        self.orders_stale = True                  # first cycle reads the list; sync_orders takes them over
        return True

    def sleep_until(self, t):
        while self.running and util.time.monotonic() < t:
            util.time.sleep(min(0.25, max(0.0, t - util.time.monotonic())))

    # Exchange answers that mean "busy, try again later" rather than "this API doesn't work the way we think":
    # a network error or timeout (0), the same request still in flight (409), rate limited, server trouble.
    SELFTEST_BUSY = (0, 409, 429, 500, 502, 503, 504)

    def self_test(self):
        """Live mode, straight after the first quotes go out (so they aren't delayed at the open): check the
        exchange behaves the way this bot assumes, using two 1-share orders at the most extreme prices
        allowed (a bid at 0.005 and an ask at 0.995), so they almost certainly won't trade, and if they do
        it costs about half a cent. Checks:
          1. the batch order is accepted and returns order ids,
          2. both show up in our open orders with side / action / priceLimit / quantity / expirationDate,
          3. "sell YES @ 0.995" reads back as an ask at 0.995 (the engine may store it as buy NO @ 0.005),
          4. the expiry time is kept, and cancel-all removes them.
        Anything unexpected -> alert + stop with exit code 3, so a human looks before real quoting.
        A BUSY exchange (timeout, 409 in flight, 429, 5xx: day one's open) is not a failure: the test is
        simply tried again selftest_retry_seconds later. Returns True = passed (or not needed), False = busy.
        This runs the test here and now (used by tests and tools); the main loop uses selftest_tick(),
        which runs the same test on a background thread so slow writes never hold up quoting."""
        if not self.selftest_needed():
            return True
        eid = self.selftest_start()
        return self.selftest_finish(eid, self.selftest_run(eid, self.selftest_ttl()))

    def selftest_needed(self):
        return self.cfg.selftest_enabled and self.api.live and bool(self.ex) and not self.selftest_passed

    def selftest_start(self):
        """Pick the test exchange and keep the bot's own quoting off it until the test is over (the test's
        clean-up cancels everything there)."""
        # On the quietest ordinary market: the test's clean-up cancels our quotes there too, and that must not
        # cost a big market its place in line at the open.
        quiet = [e for e in sorted(self.ex) if self.ex[e].group not in self.cfg.headline_races] or sorted(self.ex)
        eid = min(quiet, key=lambda e: self.size_plan.get(e, 0))
        # Remember any earlier "outcome unknown" hold on it, so finishing the test doesn't lift it early.
        self.selftest_hold = self.ex[eid].pending_until
        self.ex[eid].pending_until = float("inf")
        self.selftest_started = self.selftest_started or util.time.monotonic()
        self.selftest_gen = self.cancel_gen
        return eid

    def selftest_ttl(self):
        """The expiry the self-test checks: order_ttl."""
        return self.cfg.order_ttl

    def selftest_run(self, eid, ttl):
        """One test (API calls only, no bot state touched, so it can run on any thread).
        Returns (verdict, problems, ttl): verdict "passed", "failed" or "busy"."""
        verdict, problems = self.selftest_attempt(eid, ttl)
        if verdict == "rejected" and ttl > self.cfg.order_ttl:
            # TTL saver: the longest tier TTL was refused -> the tiers go off (finish) and order_ttl is tried
            log.warning("self-test: %.0f-min orders (TTL tiers) were rejected (%s) - trying order_ttl", ttl / 60,
                        problems[0])
            ttl = self.cfg.order_ttl
            verdict, problems = self.selftest_attempt(eid, ttl)
        if verdict == "rejected" and ttl > 600:
            # The docs allow any future expiry, but if the exchange caps it, fall back rather than stop.
            log.warning("self-test: %.0f-min orders were rejected (%s) - trying 10-min ones", ttl / 60, problems[0])
            ttl = 600.0
            verdict, problems = self.selftest_attempt(eid, ttl)
        return ("failed" if verdict == "rejected" else verdict), problems, ttl

    def selftest_attempt(self, eid, ttl):
        problems, busy, rejected, funds = [], False, False, False
        exp = iso(util.utcnow() + timedelta(seconds=ttl))              # the same expiry real quotes use
        orders = [{"exchangeId": eid, "side": "yes", "action": act, "quantity": 1, "price": px,
                   "tournamentId": self.tid, "expirationDate": exp} for act, px in (("buy", PMIN), ("sell", PMAX))]
        try:
            # Our own quotes there can't be repriced while the test runs: take them off first.
            self.api.cancel_all(self.tid, eid)
            results = self.api.place_batch(orders)
            if len(results) != 2 or not all(r.get("ok") and (r.get("data") or {}).get("orderId") is not None
                                            for r in results):
                problems.append(f"batch response not as expected: {str(results)[:300]}")
                rejected = len(results) == 2 and not any(r.get("ok") for r in results)
                busy = any(r.get("status") in self.SELFTEST_BUSY for r in results)
                # 3 Oct live: at 100% capital the 1-share test order was refused "Insufficient available funds"
                # (HTTP 400) and the bot stopped twice. No cash free is not an API surprise: retry later (backing
                # off, see selftest_finish) - but only if EVERY order that didn't go through was refused for cash:
                # a funds refusal next to a real rejection is still a rejection.
                bad = [r for r in results if not (r.get("ok") and (r.get("data") or {}).get("orderId") is not None)]
                funds = len(results) == 2 and bool(bad) and all(self.selftest_funds_refusal(r) for r in bad)
                busy = busy or funds
            else:
                # The open-orders list can lag the exchange (up to recent_order_grace_seconds): keep looking.
                # Test orders that never show up at all are reported as "not listed" (busy the first time).
                give_up = util.time.monotonic() + self.cfg.recent_order_grace_seconds
                while True:
                    found = self.selftest_check(self.api.open_orders(self.tid, eid))
                    if not found or util.time.monotonic() >= give_up:
                        break
                    util.time.sleep(min(2.0, max(0.0, give_up - util.time.monotonic())))
                problems += found
        except ApiError as e:
            problems.append(f"API error: {e}")
            busy = e.status in self.SELFTEST_BUSY
        finally:
            try:                                                  # also removes our quotes there (re-placed later)
                if not self.api.cancel_all(self.tid, eid):
                    problems.append("cancel-all didn't confirm the test orders were gone")
            except ApiError as e:
                problems.append(f"cancel-all failed: {e}")
                busy = busy or e.status in self.SELFTEST_BUSY
        if not problems:
            return "passed", []
        if not busy and not rejected and problems[0] == self.SELFTEST_NOT_LISTED:
            return "not listed", problems
        if funds:
            return "funds", problems                      # busy, for lack of free cash (selftest_finish backs off)
        return ("busy" if busy else "rejected" if rejected else "failed"), problems

    SELFTEST_NOT_LISTED = "neither test order is in our open orders"

    @staticmethod
    def selftest_funds_refusal(r):
        """True if one batch result is a refusal for lack of cash ("Insufficient available funds"): the
        account is fully deployed, not an API that behaves differently from what we assume. The message may
        sit in r["data"]["error"] (a string, or a dict with it under message / error / detail) or r["error"]."""
        if not isinstance(r, dict) or r.get("ok"):
            return False
        texts = []

        def add(err):
            if isinstance(err, str):
                texts.append(err)
            elif isinstance(err, dict):
                for k in ("message", "error", "detail"):
                    v = err.get(k)
                    if isinstance(v, str):
                        texts.append(v)
                    elif isinstance(v, dict):
                        add(v)
        data = r.get("data")
        if isinstance(data, dict):
            add(data.get("error"))
        add(r.get("error"))
        return any("insufficient" in t.lower() and "fund" in t.lower() for t in texts)

    @classmethod
    def selftest_check(cls, raw):
        problems = []
        missing = sorted({f for o in raw for f in ("id", "side", "action", "priceLimit", "quantity",
                                                   "expirationDate") if f not in o})
        if missing:
            problems.append(f"open orders are missing fields {missing}")
        mine = [o for o in map(parse_order, raw) if o]
        if not any(o.qty == 1 and (abs(o.price - PMIN) < 1e-9 or abs(o.price - PMAX) < 1e-9) for o in mine) \
                and not missing:
            return [cls.SELFTEST_NOT_LISTED]
        if not any(o.is_bid and abs(o.price - PMIN) < 1e-9 and o.qty == 1 for o in mine):
            problems.append("the test bid at 0.005 isn't in our open orders")
        if not any(not o.is_bid and abs(o.price - PMAX) < 1e-9 and o.qty == 1 for o in mine):
            problems.append("the test 'sell YES @ 0.995' didn't read back as an ask at 0.995")
        if mine and not all(o.expires for o in mine):
            problems.append("the orders' expiry time wasn't kept")
        return problems

    def selftest_finish(self, eid, outcome):
        """Main thread: act on a test's outcome. True = passed."""
        verdict, problems, ttl = outcome
        ex = self.ex.get(eid)
        if ex:
            ex.pending_until = self.selftest_hold if self.selftest_hold != float("inf") else 0.0
        if verdict == "failed" and self.cancel_gen != self.selftest_gen:
            # The bot itself cancelled everything while the test ran (error recovery, kill switch...):
            # its orders may have vanished for that reason, so this proves nothing. Try again.
            verdict, problems = "busy", ["our own cancel-all ran during the test"] + problems
        if verdict == "not listed":
            # Accepted, but never listed: the list may just be lagging a busy exchange. Twice in a row = real.
            self.selftest_unlisted += 1
            verdict = "busy" if self.selftest_unlisted < 2 else "failed"
        elif verdict == "error":
            self.selftest_errors += 1
            verdict = "busy" if self.selftest_errors < 3 else "failed"
        # The clean-up cancelled whatever we had resting there: drop it from our record and re-read the list.
        self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid])
        self.orders_stale = True
        if verdict == "funds":
            # No free cash: retried after selftest_retry_seconds, doubling each time up to SELFTEST_FUNDS_MAX_WAIT
            # (each try costs writes and wipes our quotes on the test market); one alert per new wait.
            prev = self.selftest_funds_wait
            base = self.cfg.selftest_retry_seconds
            wait = min(prev * 2, max(self.SELFTEST_FUNDS_MAX_WAIT, base)) if prev else base
            self.selftest_funds_wait = wait
            self.selftest_next = util.time.monotonic() + wait
            log.warning("self-test: refused for lack of free cash (%s) - trying again in %.0f s, quoting meanwhile",
                        problems[0] if problems else "?", wait)
            if wait != prev:
                util.alert(f"self-test waiting for free cash (insufficient funds), retrying in {wait:.0f} s")
            return False
        self.selftest_funds_wait = 0.0                    # not a funds refusal: the back-off starts over
        if verdict == "passed":
            if ttl != self.cfg.order_ttl and ttl < self.cfg.order_ttl:
                util.alert(f"orders expiring in {self.cfg.order_ttl / 60:.0f} min were rejected but {ttl / 60:.0f}-min "
                      f"ones work - using {ttl / 60:.0f}-min orders from now on")
                self.cfg.order_ttl, self.cfg.refresh_before_expiry = ttl, ttl / 5
            log.info("self-test passed: orders, sell->NO conversion, expiry and cancel all behave as expected")
            self.selftest_passed = True
            # No test runs there any more: that exchange is an ordinary one again (lost-order recovery may lift
            # its hold early; a whole-exchange cancel there no longer bumps cancel_gen).
            self.selftest_eid = None
            return True
        if verdict == "busy":
            waited = util.time.monotonic() - (self.selftest_started or util.time.monotonic())
            self.selftest_next = util.time.monotonic() + self.cfg.selftest_retry_seconds
            log.warning("self-test: exchange busy (%s) - trying again in %.0f s, quoting meanwhile",
                        problems[0] if problems else "?", self.cfg.selftest_retry_seconds)
            if waited >= self.cfg.selftest_alert_after and not self.selftest_alerted:
                self.selftest_alerted = True
                util.alert(f"self-test still not done after {waited / 60:.0f} min: the exchange keeps answering busy "
                      f"({problems[0] if problems else '?'}). Quoting continues; it keeps retrying")
            return False
        util.fatal("self-test failed before trading - " + "; ".join(problems))

    SELFTEST_FUNDS_MAX_WAIT = 1800.0                      # funds-refusal back-off cap (s)

    def selftest_state(self):
        """For status.json: where the start-up self-test is."""
        if self.selftest_passed:
            return "passed"
        if not self.selftest_needed():
            return "off"
        if self.selftest_funds_wait:
            return "waiting_funds"
        if self.selftest_future is not None:
            return "running"
        return "waiting" if util.time.monotonic() < self.selftest_next else "pending"

    def selftest_tick(self):
        """Main loop, after every cycle: run the self-test on a background thread, without ever waiting for
        it (on day one, order writes took 15-30 s at the open), and act on its result once it's in."""
        if not self.selftest_needed():
            return
        f = self.selftest_future
        if f is not None:
            if f.done():
                self.selftest_future = None
                try:
                    outcome = f.result()
                except Exception as e:            # a bug in the test itself: retried, fatal the 3rd time running
                    outcome = ("error", [f"self-test error: {e}"], self.cfg.order_ttl)
                self.selftest_finish(self.selftest_eid, outcome)
            return
        if self.nosell_future is not None or getattr(self, "pairno_future", None) is not None:
            return                                # a sell-NO leg is out: never two tests at once (holds, same market)
        if util.time.monotonic() >= self.selftest_next:
            self.selftest_eid = self.selftest_start()
            self.selftest_future = self.selftest_pool.submit(self.selftest_run, self.selftest_eid, self.selftest_ttl())

    # --- The reduce_no_as_sell self-test leg ---
    def nosell_tick(self):
        """Main loop, after every cycle: with reduce_no_as_sell on (live, self-test on) and not checked yet, once some
        market holds NO, check on a background thread that the exchange takes a covered "sell NO" (nosell_run). Until
        it has passed, NO holdings are reduced as today ("buy YES"). Refused -> alert, and today's behaviour for the
        rest of this run (nosell_state "off"); busy -> tried again selftest_retry_seconds later. Never stops the bot."""
        cfg = self.cfg
        if not (cfg.reduce_no_as_sell and self.api.live and cfg.selftest_enabled) or self.nosell_state is not None:
            return
        f = self.nosell_future
        if f is not None:
            if f.done():
                self.nosell_future = None
                try:
                    verdict, msg = f.result()
                except Exception as e:            # a bug in the test itself: retried later, never fatal
                    verdict, msg = "busy", f"self-test error: {e}"
                self.nosell_finish(verdict, msg)
            return
        if (self.selftest_future is not None or getattr(self, "pairno_future", None) is not None
                or util.time.monotonic() < self.nosell_next):
            return
        held = [e for e in sorted(self.ex) if self.ex[e].inv <= -1]
        # Note: with no_set_aware_bids on, only legs with a LONE NO part are tested. If no market has one
        # (e.g. after a restart every NO is held in NO+NO sets) this check never runs, so it never passes and
        # reduce_no_on() stays False: no_set_aware_bids, pair_no_unwind_max_cost and the follow-up's covered sales
        # stay inert for the run. Logged once (below) when that lasts more than 10 minutes.
        if getattr(cfg, "no_set_aware_bids", False):   # A 1-share sale inside a NO+NO set breaks it and
            lone = [e for e in held if -self.ex[e].inv - self.nono_set_part(e) >= 1]   # is refused: lone parts only
            if held and not lone:
                now = util.time.monotonic()
                since = getattr(self, "nosell_nolone_since", None)
                if since is None:
                    self.nosell_nolone_since = now
                elif now - since > 600 and not getattr(self, "nosell_nolone_logged", False):
                    self.nosell_nolone_logged = True
                    log.warning("reduce_no_as_sell start-up check still waiting after %.0f min: NO is held in %d "
                                "market(s) but none has a lone NO part (all in NO+NO sets), so the check cannot run; "
                                "no_set_aware_bids / pair_no_unwind_max_cost / covered follow-ups stay inert until "
                                "one does", (now - since) / 60, len(held))
            else:
                self.nosell_nolone_since = None
            held = lone
        if not held:
            return                                # nothing to reduce yet: nothing to check
        # The test order is a YES bid at PMIN: only where no other trader's ask (cached book) is at PMIN or below, so
        # it can't fill. No such market (or no book yet) -> skip this tick and look again next time.
        safe = [e for e in held if self.nosell_safe(self.ex[e])]
        if not safe:
            return
        eid = min(safe, key=lambda e: self.ex[e].inv)   # the biggest NO holding that is safe to test on
        self.nosell_eid, self.nosell_hold = eid, self.ex[eid].pending_until
        self.ex[eid].pending_until = float("inf")       # no quoting there while the test order may rest
        self.nosell_future = self.selftest_pool.submit(self.nosell_run, eid)

    @staticmethod
    def nosell_safe(ex):
        """True if the cached book shows no other trader's ask at PMIN or below (an empty ask side counts as safe;
        no book downloaded yet does not)."""
        if ex.book is None:
            return False
        return all(float(l["price"]) > PMIN + 1e-9 for l in (ex.book.get("asks") or []))

    def nosell_test_order(self, eid):
        """ONE 1-share "sell NO @ 0.995" (= our bid at YES 0.005, PMIN): fills only if someone bids 0.995 for NO."""
        return {"exchangeId": eid, "side": "no", "action": "sell", "quantity": 1, "price": round(1 - PMIN, 3),
                "tournamentId": self.tid, "expirationDate": iso(util.utcnow() + timedelta(seconds=self.cfg.order_ttl))}

    def nosell_run(self, eid):
        """Background thread, API calls only: place the test order, cancel it at once. -> (verdict, message):
        "ok" (accepted), "refused" (the exchange said no) or "busy" (try again later)."""
        try:
            results = self.api.place_batch([self.nosell_test_order(eid)])
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT" or e.status in self.SELFTEST_BUSY:
                return "busy", str(e)
            return "refused", str(e)
        r = (results or [{}])[0]
        data = r.get("data") or {}
        oid = data.get("orderId")
        if oid is not None:
            try:
                if not self.api.cancel_order(oid):
                    self.api.cancel_all(self.tid, eid)
            except ApiError:
                try:
                    self.api.cancel_all(self.tid, eid)
                except ApiError as e:
                    log.warning("self-test (sell NO): could not cancel test order %s (%s) - it expires on its own",
                                oid, e)
        if r.get("ok") and oid is not None:
            return "ok", f"order {oid} accepted and cancelled"
        if r.get("status") in self.SELFTEST_BUSY:
            return "busy", str(r)[:300]
        err = data.get("error") or data
        return "refused", (err.get("message") if isinstance(err, dict) and err.get("message") else str(r))[:300]

    def nosell_finish(self, verdict, msg):
        """Main thread: act on the leg's outcome (see nosell_tick)."""
        ex = self.ex.get(getattr(self, "nosell_eid", None))
        if ex is not None:
            ex.pending_until = self.nosell_hold if self.nosell_hold != float("inf") else 0.0
        self.orders_stale = True                  # the clean-up may have touched our orders there: re-read the list
        if verdict == "ok":
            self.nosell_state = "ok"
            log.info("self-test (sell NO): a covered 'sell NO' was accepted (%s) - NO holdings are now reduced as "
                     "covered NO sales (reduce_no_as_sell)", msg)
        elif verdict == "refused":
            self.nosell_state = "off"
            log.error("self-test (sell NO) refused: %s - reduce_no_as_sell OFF for this run", msg)
            util.alert(f"reduce_no_as_sell: the exchange refused a covered 'sell NO' ({msg}). NO holdings are reduced as "
                  f"'buy YES' again (today's behaviour) for the rest of this run; the bot keeps running")
        else:
            self.nosell_next = util.time.monotonic() + self.cfg.selftest_retry_seconds
            log.warning("self-test (sell NO): exchange busy (%s) - trying again in %.0f s", msg,
                        self.cfg.selftest_retry_seconds)

    # --- The pair_no_unwind_max_cost self-test leg (a paired covered NO sale) ---
    PAIRNO_MAX_WAIT = 1800.0                      # busy back-off cap (s)

    def pairno_tick(self):
        """Main loop, after every cycle: with pair_no_unwind_max_cost >= 0 and reduce_no_as_sell on (live, self-test
        on), once the sell-NO leg has passed and some race holds NO on every leg, check on a background thread that
        the exchange takes ONE batch of covered "sell NO" orders, 1 share on each leg of that race (pairno_run: the
        sale that closes a whole set). Until it has passed, short sets are bought back only at today's rule.
        Refused -> alert, the paired unwind off for the rest of this run (pairno_state "off"); busy -> tried again
        with a back-off (selftest_retry_seconds doubling up to PAIRNO_MAX_WAIT). Never stops the bot."""
        cfg = self.cfg
        if (getattr(cfg, "pair_no_unwind_max_cost", -1.0) < 0 or not (cfg.reduce_no_as_sell and self.api.live
                                                                       and cfg.selftest_enabled)
                or self.pairno_state is not None):
            return
        f = self.pairno_future
        if f is not None:
            if f.done():
                self.pairno_future = None
                try:
                    verdict, msg = f.result()
                except Exception as e:            # a bug in the test itself: retried later, never fatal
                    verdict, msg = "busy", f"self-test error: {e}"
                self.pairno_finish(verdict, msg)
            return
        if (self.nosell_state != "ok" or self.selftest_future is not None or self.nosell_future is not None
                or util.time.monotonic() < self.pairno_next):
            return
        cands = []
        for race, members in sorted(self.groups.items()):
            if len(members) < 2 or any(m not in self.ex for m in members):
                continue
            sets = min(-self.ex[m].inv for m in members)
            # every leg: NO held, and no other trader's ask at or below 0.005 (the YES bid at 0.005 cannot fill)
            if sets >= 1 and all(self.nosell_safe(self.ex[m]) for m in members):
                cands.append((len(members) != 2, -sets, race))
        if not cands:
            return                                # no NO+NO set held (or none safe to test on): look again next time
        race = min(cands)[2]                      # a 2-leg race first, the biggest set
        members = list(self.groups[race])
        self.pairno_race = race
        self.pairno_holds = {m: self.ex[m].pending_until for m in members}
        for m in members:
            self.ex[m].pending_until = float("inf")   # no quoting there while the test orders may rest
        self.pairno_future = self.selftest_pool.submit(self.pairno_run, members)

    def pairno_test_orders(self, members):
        """ONE 1-share "sell NO @ 0.995" (= our bid at YES 0.005) per leg: they close one whole NO+NO set."""
        exp = iso(util.utcnow() + timedelta(seconds=self.cfg.order_ttl))
        return [{"exchangeId": e, "side": "no", "action": "sell", "quantity": 1, "price": round(1 - PMIN, 3),
                 "tournamentId": self.tid, "expirationDate": exp} for e in members]

    def pairno_run(self, members):
        """Background thread, API calls only: place the paired test batch, cancel every accepted order at once.
        -> (verdict, message): "ok" (every leg accepted), "refused" (any leg refused, a funds refusal included:
        the set collateral rule does not let a paired sale go without cash) or "busy" (try again later)."""
        try:
            results = self.api.place_batch(self.pairno_test_orders(members))
        except ApiError as e:
            if e.code == "WRITE_BUDGET_WAIT" or e.status in self.SELFTEST_BUSY:
                return "busy", str(e)
            return "refused", str(e)
        results = list(results or [])
        oids = [((r or {}).get("data") or {}).get("orderId") for r in results]
        for k, oid in enumerate(oids):
            if oid is None:
                continue
            eid = members[k] if k < len(members) else None
            try:
                if not self.api.cancel_order(oid) and eid is not None:
                    self.api.cancel_all(self.tid, eid)
            except ApiError:
                try:
                    if eid is not None:
                        self.api.cancel_all(self.tid, eid)
                except ApiError as e:
                    log.warning("self-test (paired NO sale): could not cancel test order %s (%s) - it expires on "
                                "its own", oid, e)
        if len(results) == len(members) and all(r.get("ok") and o is not None for r, o in zip(results, oids)):
            return "ok", f"orders {oids} accepted and cancelled"
        bad = [r for r in results if not r.get("ok")] or [{"error": "no result for every leg"}]
        if any(self.selftest_funds_refusal(r) for r in bad):
            return "refused", "insufficient funds: " + str(bad[0])[:250]
        if all(r.get("status") in self.SELFTEST_BUSY for r in bad):
            return "busy", str(bad[0])[:300]
        err = ((bad[0].get("data") or {}).get("error") if isinstance(bad[0].get("data"), dict) else None) or bad[0]
        return "refused", (err.get("message") if isinstance(err, dict) and err.get("message") else str(bad[0]))[:300]

    def pairno_finish(self, verdict, msg):
        """Main thread: act on the leg's outcome (see pairno_tick)."""
        for m, hold in (getattr(self, "pairno_holds", None) or {}).items():
            if m in self.ex:
                self.ex[m].pending_until = hold if hold != float("inf") else 0.0
        self.pairno_holds = {}
        self.orders_stale = True                  # the clean-up may have touched our orders there: re-read the list
        if verdict == "ok":
            self.pairno_state, self.pairno_wait = "ok", 0.0
            log.info("self-test (paired NO sale) on %s: accepted (%s) - NO+NO sets are unwound as a pair "
                     "(pair_no_unwind_max_cost)", self.pairno_race, msg)
        elif verdict == "refused":
            self.pairno_state = "off"
            log.error("self-test (paired NO sale) on %s refused: %s - pair_no_unwind_max_cost OFF for this run",
                      self.pairno_race, msg)
            util.alert(f"the exchange refuses a paired NO sale: NO+NO sets cannot be unwound without cash ({msg}). "
                  f"pair_no_unwind_max_cost is off for the rest of this run; the bot keeps running")
        else:
            prev, base = self.pairno_wait, self.cfg.selftest_retry_seconds
            wait = min(prev * 2, max(self.PAIRNO_MAX_WAIT, base)) if prev else base
            self.pairno_wait = wait
            self.pairno_next = util.time.monotonic() + wait
            log.warning("self-test (paired NO sale): exchange busy (%s) - trying again in %.0f s", msg, wait)

    def start_feed(self):
        """Start the realtime feed if it's enabled and the `realtime` package is installed."""
        if not self.cfg.realtime_enabled:
            return None
        try:
            import realtime  # noqa: F401  (only checking it's installed)
        except ImportError:
            log.warning("realtime package not installed (pip install realtime) - polling every %.0f s instead",
                        self.cfg.loop_seconds)
            return None
        feed = RealtimeFeed(self.api, self.tid, self.cfg)
        feed.start()
        return feed

    def wait_for_next_cycle(self, t0):
        """Wait at least min_cycle_seconds (batches events), then until the realtime feed pushes something
        or a Polymarket reading wakes us (see on_reference_prices), or loop_seconds pass."""
        self.sleep_until(t0 + self.cfg.min_cycle_seconds)
        deadline = t0 + self.cfg.loop_seconds
        while self.running and util.time.monotonic() < deadline:
            if (self.wake.is_set() or (self.feed and self.feed.healthy() and self.feed.wake.is_set())
                    or (self.writes and self.write_done.is_set())):
                break
            self.wake.wait(timeout=0.05)
        self.wake.clear()

    def wait_for_trading(self):
        """Orders are rejected until the tournament is 'active' (it's 'draft' before the start date).

        Far from the start: check every start_check_seconds and use the wait to download the (house-
        seeded) books a few at a time, so quoting can start at once. In the last open_quiet_seconds:
        no requests at all (keeps the per-minute budget free for the first quotes), sleep until the
        start time, then check every open_poll_seconds, so the first quotes go out within ~1 s of the
        open. First in line at a price gets filled first, and every bot will want the same prices."""
        cfg = self.cfg
        while self.running:
            try:
                t = self.api.tournament()
            except ApiError as e:             # this loop may run for days; a blip mustn't kill it
                log.warning("tournament check failed (%s) - retrying in %.0f s", e, cfg.start_check_seconds)
                self.sleep_until(util.time.monotonic() + cfg.start_check_seconds)
                continue
            if t.get("status") == "active":
                return
            if t.get("status") == "ended":
                log.error("tournament has ended")
                self.running = False
                return
            try:
                start = parse_ts(t.get("startDate"))
            except (ValueError, AttributeError):
                start = None                      # unreadable start time: just keep checking slowly
            to_start = (start - util.utcnow()).total_seconds() if start else float("inf")
            if to_start > cfg.open_quiet_seconds:
                try:
                    self.refresh_books({}, util.time.monotonic())
                except Exception as e:
                    log.warning("pre-open book download failed: %s", e)
                ready = sum(e.book is not None for e in self.ex.values())
                self.phase = (f"waiting for the open ({start:%d %b %H:%M} UTC), {ready}/{len(self.ex)} order books ready"
                              if start else f"waiting for the open, {ready}/{len(self.ex)} order books ready")
                wait = min(cfg.start_check_seconds, to_start - cfg.open_quiet_seconds)
                log.info("tournament is '%s' (starts %s) - %d/%d books ready - checking again in %.0f s",
                         t.get("status"), t.get("startDate"), ready, len(self.ex), wait)
            elif to_start > 0:
                wait = to_start                   # quiet until the start time
                log.info("opening in %.0f s - quiet until then, then checking every %.0f s",
                         to_start, cfg.open_poll_seconds)
            elif to_start > -cfg.start_check_seconds:
                wait = cfg.open_poll_seconds      # start time reached: check every second
            else:
                wait = cfg.start_check_seconds    # well past the start and still not open: back to slow checks
                log.info("tournament still '%s' %.0f s after its start time - checking every %.0f s",
                         t.get("status"), -to_start, wait)
            self.maybe_summary()                  # updates while waiting too, so you know it's alive
            self.sleep_until(util.time.monotonic() + wait)

    def on_cycle_error(self, what, pull_now):
        """API errors are usually transient: tolerate a few, then pull every quote until healthy.
        Unexpected (non-API) errors may be a bug in our own logic, so those pull quotes at once."""
        self.failed_cycles += 1
        self.errors_total += 1
        if (pull_now or self.failed_cycles >= self.cfg.max_failed_cycles) and not self.pulled_after_errors:
            if not self.error_alerted:        # once per outage: a failed cancel-all used to re-alert every cycle
                util.alert(f"{what} - pulling all quotes until cycles succeed again")
                self.error_alerted = True
            try:
                # Only "pulled" once the cancel-all reports nothing left: a partial one (207 with orders still
                # resting) is tried again on the next failed cycle instead of giving up for the whole outage.
                if self.cancel_everything():
                    self.pulled_after_errors = True
                else:
                    log.error("cancel-all left orders resting - trying again on the next failed cycle")
            except Exception as e:        # never let the error handler itself crash the bot
                log.error("cancel-all failed too (%s) - orders expire within %.0f min anyway", e, self.cfg.order_ttl / 60)

    def on_cycle_ok(self):
        """A cycle succeeded: clear the error state, and say so once if the outage was alerted."""
        if self.error_alerted:
            util.alert(f"cycles succeeding again after {self.failed_cycles} failed - quoting resumes")
            self.error_alerted = False
        self.failed_cycles, self.pulled_after_errors = 0, False

    def run(self):
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)   # `systemctl stop` sends this
        if hasattr(signal, "SIGUSR1"):                     # handover restart (deploy): stop WITHOUT cancelling
            signal.signal(signal.SIGUSR1, self.request_handover)
        kill_file = bot_path(self.cfg.kill_file)
        if self.api.live and os.path.exists(kill_file):
            log.critical("the kill switch fired earlier (%s). Check what happened, then delete that file to trade again.",
                         kill_file)
            self.exit_code = EXIT_KILLED
            return
        self.warn_settings()                               # start-up: settings that work against each other
        try:
            # Started before the open, so both are warm at the first cycle: Polymarket prices refresh in
            # the background from now on, and the realtime feed is connected.
            if self.refs:
                self.refs.start()
            self.feed = self.start_feed()
            if self.api.live:
                util.alert("bot starting (live)")
                self.wait_for_trading()
                if self.running and self.adopt_handover():
                    pass                                  # keep the previous run's quotes (see request_handover)
                elif self.running:
                    try:
                        left = [o for o in self.api.open_orders(self.tid) if str(o.get("exchangeId")) in self.ex]
                    except ApiError as e:
                        left = None                       # can't tell: cancel to be safe
                        log.warning("open-orders read failed (%s) - cancelling to be safe", e)
                    try:
                        if left == []:
                            log.info("clean slate: no orders resting - nothing to cancel")
                        else:
                            log.info("clean slate: cancelling %s orders left over from before",
                                     len(left) if left is not None else "any")
                            self.cancel_everything()
                    except ApiError as e:
                        # Day one's open: every write timed out. Crashing here (exit 1, restart, same again) helps
                        # nobody: anything left over shows up in the first open-orders read and is managed (or
                        # cancelled) like any other order.
                        log.warning("clean-slate cancel failed (%s) - going on; leftovers get re-read and managed", e)
                        self.orders_stale = True
            self.phase = "trading"
            self.trading_since = util.time.monotonic()     # startup priming (books first) runs from here
            self.start_watchdog()
            while self.running:
                t0 = util.time.monotonic()
                try:
                    self.check_overrides()
                    self.check_market_edge()
                    self.maybe_daily_analysis()
                    if t0 - self.last_reload > self.cfg.market_reload_seconds:
                        self.load_markets()
                    self.cycle()
                    if self.running:
                        self.selftest_tick()      # stops the bot (exit code 3) if the API surprises us
                        self.nosell_tick()        # Covered "sell NO" leg (never stops the bot)
                        self.pairno_tick()        # Paired NO+NO sale leg (never stops the bot)
                    self.on_cycle_ok()
                    self.write_status(ok=True)
                    self.maybe_summary()
                except ApiError as e:
                    if e.code in FATAL_API_CODES:          # e.g. key revoked: retrying forever won't help
                        util.fatal(f"API says {e.code}: {e}")
                    log.error("cycle failed: %s", e)
                    self.on_cycle_error(f"{self.failed_cycles + 1} failed cycles", pull_now=False)
                    self.write_status(ok=False)
                except Exception:
                    log.exception("unexpected error")
                    self.on_cycle_error("unexpected error", pull_now=True)
                    self.write_status(ok=False)
                self.wait_for_next_cycle(t0)
        finally:
            if self.feed:
                self.feed.stop()
            self.shutdown()
            self.close()

    def close(self):
        """Release the thread pool and the recording database (after shutdown has cancelled orders)."""
        if self.refs and hasattr(self.refs, "stop"):
            self.refs.stop()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.writer.shutdown(wait=False, cancel_futures=True)
        self.selftest_pool.shutdown(wait=False, cancel_futures=True)
        if self.db:
            self.db.close()
            self.db = None

    def shutdown(self):
        """Always runs on exit (Ctrl+C, kill switch, crash): cancel every order we have."""
        if getattr(self, "pair_owed", None):          # In memory only, never carried over
            log.warning("pair unwind follow-up: owed legs dropped at exit (a restart does not resume them): %s",
                        self.pair_owed_status())
        if not self.api.live:
            log.info("dry run finished (no real orders to cancel)")
            return
        # An order write still in flight could land after the cancel-all and be left resting: drop the queued
        # ones, wait for those already sent, and cancel again below if any are still running after that.
        handover = self.handover and self.exit_code == 0
        # On a handover queued writes still go out (a queued pull must not be lost: the quotes stay resting).
        self.writer.shutdown(wait=False, cancel_futures=not handover)
        self.drain_writes(timeout=(4 if handover else 2) * self.cfg.request_timeout)
        if handover:
            self.notes_dirty = True
            self.save_order_notes()                   # the next run attributes their fills
            now_m = util.time.monotonic()
            write_json(bot_path(self.cfg.handover_file), {
                "t": util.time.time(), "orders": len(self.my_orders),
                # Our own record: the new run trusts it like its own for recent_order_grace_seconds, so an order
                # placed moments ago that the open-orders list doesn't show yet is never placed twice.
                "resting": [{"id": o.order_id, "eid": o.eid, "bid": o.is_bid, "price": o.price, "qty": o.qty,
                             "placed": self.placed_qty.get(oid), "expires": iso(o.expires) if o.expires else None}
                            for oid, o in self.my_orders.items()],
                "recent_cancels": [oid for oid, t in self.recent_cancels.items()
                                   if now_m - t <= self.cfg.recent_order_grace_seconds]})
            log.info("handover: %d orders left resting for the next run (they expire within %.0f min)",
                     len(self.my_orders), self.cfg.order_ttl / 60)
            return
        self.notes_dirty = True
        self.save_order_notes()
        for attempt in range(self.cfg.shutdown_cancel_attempts):
            try:
                if self.cancel_everything():
                    if self.writes:               # a write was still running: once it ends, cancel once more
                        self.drain_writes(timeout=2 * self.cfg.request_timeout)
                        self.cancel_everything()
                    log.info("all orders cancelled")
                    util.alert("bot stopped, all orders cancelled")
                    return
            except Exception as e:
                log.error("cancel attempt %d failed: %s", attempt + 1, e)
            util.time.sleep(1)
        util.alert(f"bot stopped but COULD NOT CONFIRM CANCELLATION - check the web UI "
              f"(orders expire within {self.cfg.order_ttl / 60:.0f} min anyway)")
