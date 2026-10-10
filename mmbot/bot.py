"""
The Bot: state (__init__), the clock, reference prices and tilt, markets and close times, the cycle
(cycle / cycle_body), books and priming, decide and its blocks, reconcile / plan_change,
plan_exchange and the write queue, order sync / place / cancel / apply_batch, the NO-as-sell sets
and log_fills. Everything else is a mixin in its own module.

Never imported by the mixins or the leaf modules.
"""
import email.utils
import json
import math
import os
import threading
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import timedelta

from mmbot import util
from mmbot import config
from mmbot import exchange
from mmbot import pricing
from mmbot import quoting
from mmbot import measure
from mmbot.util import BULK_MAX_IDS, FATAL_API_CODES, PARTY_SIGN, RACE_TITLE, bot_path, iso, log, parse_ts, rnd
from mmbot.config import OVERRIDABLE, parse_utc_setting
from mmbot.exchange import ApiError, Change, Ex, Write, busy, fmt
from mmbot.pricing import (
    Resting, TiltEstimator, blend_fv, bloc_sensitivities, fair_value, normalise, others_top, parse_order,
    predicted_top, reserved_cash, strip_own, tilted_ref, wire_order,
)
from mmbot.quoting import (
    DEFAULT_BANKROLL, NO_QUOTE, exit_quote, fl_side, plan_sizes, quote_lock, side_needs_change, unsafe_order,
    value_floor_quote,
)
from mmbot.measure import FillLogger, TurnoverTracker, read_fills
from mmbot.risk import RiskMixin
from mmbot.value import ValueMixin
from mmbot.mm import MarketMakingMixin
from mmbot.ladder import LadderMixin
from mmbot.arb import ArbMixin
from mmbot.status import StatusMixin
from mmbot.ops import OpsMixin


class Bot(RiskMixin, ValueMixin, MarketMakingMixin, LadderMixin, ArbMixin, StatusMixin, OpsMixin):
    def __init__(self, api, cfg):
        self.api, self.cfg = api, cfg
        self.running = True
        self.t = api.tournament()
        self.check_clock()
        self.tid = self.t["id"]
        self.initial_balance = float(self.t.get("initialBalance") or 0)
        self.fills = FillLogger(bot_path(cfg.fills_csv))
        self.ex = {}                      # eid -> Ex
        self.groups = {}                  # race -> [eid, ...]
        self.sim = {}                     # DRY RUN ONLY: pretend resting orders, so dry runs behave like live
        self.next_sim_id = -1
        self.order_meta = self.load_order_notes()   # orderId -> what that order was for (attributes fills)
        self.lots = self.load_lots()      # eid -> [[signed shares, epoch time bought], ...] oldest first (age skew)
        self.lots_seeded = False          # first reconcile rebuilds missing ones from fills.csv
        self.lots_dirty = False
        self.capital_over = False         # capital ceiling active (capital_in_positions_max_frac)
        self.adding_resume = False        # Package 8 adding_factor_capital_on: the resume factor is in force
        self.cur_refs, self.cur_liquid = {}, set()   # this cycle's Polymarket prices (for risk_fv)
        _tilt_saved = self.load_tilt()
        self.tilt = TiltEstimator(cfg).from_dict(_tilt_saved)   # T2.1: tournament tilt s (see update_tilt)
        self.tilt_s, self.tilt_exposure = self.tilt.s, 0.0
        self.alloc_init(self.load_status_key("alloc"))   # Package 10 B: the allocator (last run, turnover)
        self.mmf_init(self.load_status_key("mm_funding"))   # P14: MM inventory lots, alert state (status.json)
        self.p15_init()                                     # P15: the harvest ladder, the state cap (this run)
        self.pos_marks = {}             # {eid: the exchange's own valuation price of the position (currentPrice)}
        self.fv_fallback_logged = {}      # {eid: source} - which fallback risk_fv used for a held position (logged once)
        self.mark_sd = {}                 # eid -> sd of the 10-min mid change (mark_frag_*; from the recorder)
        self.mark_sd_time = -1e9          # monotonic time of the last estimate (refreshed every MARK_SD_REFRESH_SECONDS)
        self.notes_dirty = False
        self.exit_code = 0                # what the process exits with (see EXIT_* codes)
        self.health = {}                  # latest cycle summary, written to status.json
        self.last_cycle_done = None       # monotonic time the last cycle ended (watchdog, status.json)
        self.last_cycle_phases, self.pause_logged = {}, False
        self.watchdog_alerted, self.watchdog_thread = False, None
        self.kill_breaches = 0
        self.reserved_mode = None if cfg.reserved_cash_mode == "auto" else cfg.reserved_cash_mode
        self.calib_prev = None            # (account value, locked cash, positions) from the previous cycle
        self.calib_votes = []             # recent "add"/"ignore" verdicts while auto-detecting
        self.failed_cycles = 0
        self.pulled_after_errors = False
        self.pos_fallbacks = 0            # cycles in a row on the last positions read (409, see cycle)
        self.pos_fallback_since = None    # when that run of 409s started (monotonic)
        self.pos_fallback_failing = False # ...and it went on too long: cycles fail until positions read again
        self.error_alerted = False        # one phone alert per outage, plus one when it ends
        self.last_reload = 0.0
        self.last_equity = None           # latest account value (read every slow_poll_seconds)
        self.size_bank = self.initial_balance or DEFAULT_BANKROLL   # account value sizes are based on (see bankroll)
        self.size_plan = {}               # eid -> shares per quote (see update_size_plan)
        self.size_plan_time = -1e9
        self.size_plan_bank = None        # account value the plan was made for
        self.last_slow_poll = -1e9        # when P&L and fills were last read
        self.last_full_check = -1e9       # when the last full check (bulk prices, positions, orders) ran
        self.feed = None                  # RealtimeFeed, started by run() (None = polling only)
        self.wake = threading.Event()     # set by the Polymarket thread: start the next cycle now
        self.cached_pos = None            # latest positions / open orders, reused on event cycles
        self.cached_orders = None         #   that don't need to re-read them
        self.orders_stale = True          # unsure what's resting (or positions changed) -> re-read both next cycle
        self.pending_dirty = set()        # books the feed reported but we haven't downloaded yet
        self.my_orders = {}               # LIVE: orderId -> Resting, our own record of our resting orders (see sync_orders)
        self.recent_orders = {}           # orderId -> (Resting, time placed): placed moments ago, maybe not listed yet
        self.recent_cancels = {}          # orderId -> time cancelled: cancelled moments ago, maybe still listed
        self.unconfirmed = {}             # eid -> [(order, meta, time sent)]: sent, outcome unknown (see adopt_unconfirmed)
        self.placed_qty = {}              # orderId -> shares we placed, and...
        self.filled_qty = defaultdict(float)   # ...shares filled so far (each fill counted once, from fills)
        self.ref_rejected = set()         # Polymarket keys currently ignored as implausible (alerted once)
        self.ref_only = set()             # eids priced from Polymarket alone this cycle (thin book, R5)
        self.bloc_sens, self.bloc_delta, self.bloc_inv = {}, 0.0, {}   # Package 10 A2 (bloc_refresh)
        self.warned_value = ()                    # Package 10 A1 (iv): the value_mode warnings last logged
        # Threads for sending several HTTP requests at once (downloads mostly wait on the network).
        self.pool = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_requests), thread_name_prefix="http")
        # ...and for order writes, so a slow one never holds up the others or the cycle (see send_changes).
        self.writer = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_writes), thread_name_prefix="write")
        self.writes = []                  # Write jobs in flight; only the main thread reads or changes this list
        self.write_done = threading.Event()   # set when one finishes (wakes the main loop to apply it)
        self.write_log = deque()          # (time done, seconds taken, timed out) of recent writes (burst detection)
        self.last_cycle_seconds = 0.0
        self.burst, self.burst_calm_since, self.burst_set = False, 0.0, set()
        self.trading_since = None         # monotonic time the trading loop started (burst_startup_grace_seconds)
        self.global_reduce = False
        # P12 ops mm_risk_reserve_*: "value adds paused" (cycle step 6, mm_risk_room_update), when it started (wall
        # time), the latest rooms, and what it held back (cumulative; tail_quotes: sides in the latest cycle)
        self.mmr_paused, self.mmr_since, self.mmr_room = False, None, (None, None)
        self.mmr_blocked = {"takes": 0, "alloc": 0, "basket": 0, "tail_quotes": 0}   # (basket: retired, 0)
        self.mmr_tail_now = 0
        self.ref_moved = set()            # eids whose Polymarket price moved >= urgent_ref_move at the latest reading
        self.ref_version_urgent = 0
        self.refs = self.load_reference_prices()
        self.ref_version_seen = 0         # last Polymarket reading the jump guard has looked at
        self.last_tops = {}               # latest bulk best bid/ask per exchange (for recording)
        self.last_tops_at = {}            # (when each was read, monotonic: P15 hv_top_moved)
        self.other_tops = {}              # eid -> (other traders' best bid, best ask, time read) from bulk prices
        self.ref_tops = {}                # eid -> (best bid, best ask): R5 priced it from other_tops this cycle
        self.trading_since = None         # util.time.monotonic() when the trading loop started (startup priming)
        self.book_reqs = deque()          # times of recent book downloads (startup priming's per-minute cap)
        self.held = {}                    # eid -> signed shares held (latest positions read; books_to_fetch, priming)
        self.unpriced_held = {}           # eid -> cycles in a row a held market had no fair value
        self.unpriced_warned = set()      # ...those already warned about (once per unpriced spell)
        self.arb_cooldown = {}            # race -> util.time.monotonic() until which we leave it alone
        self.arbs_total = 0               # arbitrages / takes since start (summaries report the change)
        self.unwinds_total = 0            # pair unwinds since start (status.json pair_unwinds_total)
        self.pair_owed = {}               # Package 7 pair_unwind_followup: race -> owed legs (pair_followup_step)
        self.ops_last = {}                # ops fields of the latest status write (ops_fields): recorder, summary
        self.ops_cache = {}               # ops_fields: fills.csv-derived numbers, recomputed when the file changes
        self.ops_warned = False           # ops_fields failed once (logged once)
        self.ev_hist = self.load_ev_hist()   # [[wall, ev_outcome], ...] last 48 h (status.json, survives a restart)
        self.ev_fill_p = {}               # {fill_id: Polymarket p when the fill was logged} (mm_carry_24h; this run)
        self.ev_warned = False            # ev_fields failed once (logged once)
        self.takes_total = 0
        self.takes_skipped_budget = self.arbs_skipped_budget = 0   # not sent: write budget busy (status.json)
        self.take_version_seen = 0        # last Polymarket reading the take logic has counted
        self.db = self.open_recorder()
        self.last_record = -1e9
        self.book_tops, self.book_rows = {}, []    # recorder: last top levels logged per eid, rows not yet written
        self.last_pos_record, self.last_pos_full = -1e9, -1e9   # recorder: positions table (record_positions)
        self.pos_seen = {}                # eid -> (values last written, wall time last read)
        self.pos_record_due = False       # a fill was seen: record the next positions read
        self.last_pnl_reply = (None, None)  # recorder: (latest P&L reply, wall time read)
        self.refills = defaultdict(deque)  # (eid, "bid"/"ask") -> (time, shares) of recent fills that added
        self.turnover = TurnoverTracker() # turnover control: shares traded per market (ours + the tape)
        self.turnover_flow = {}           # eid -> observed shares/h (None = not judged yet), see refresh_turnover
        self.turnover_refreshed = -1e9    # monotonic time of the last refresh
        self.turnover_state = {}          # eid -> (dead?, monotonic time it became so): hysteresis (refresh_turnover)
        self.seed_turnover()
        self.last_summary_slot = None     # (date, hour) of the last phone summary
        self.value_at_last_summary = None
        self.counts_at_last_summary = {"arbs": 0, "takes": 0, "errors": 0, "rate_limits": 0}
        self.errors_total = 0             # failed cycles since start (summaries report new ones)
        self.phase = "starting"           # what the bot is doing, for the status line
        self.selftest_passed = False
        self.cycle_started, self.cycle_alerted = None, False
        self.last_analysis_day = None
        self.handover = False             # SIGUSR1: stop without cancelling (see request_handover)
        self.handover_timer = None        # ...and its exit deadline (see handover_deadline)
        self.last_progress_write, self.last_slow_alert = -1e9, -1e9
        self.defaults = {k: getattr(cfg, k) for k in OVERRIDABLE}   # what a removed override goes back to
        self.overrides, self.overrides_mtime, self.last_overrides_check = {}, None, -1e9
        self.market_edge, self.market_edge_mtime, self.last_market_edge_check = {}, None, -1e9   # rival-floor map
        self.selftest_future = None       # the self-test running in the background (see selftest_tick)
        self.selftest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selftest")
        self.selftest_eid = None
        self.selftest_next = 0.0          # when to try again after the exchange answered busy
        self.selftest_started = None
        self.selftest_alerted = False
        self.selftest_funds_wait = 0.0    # current funds-refusal back-off (s); 0 = not waiting for cash
        self.selftest_hold = 0.0          # the test exchange's pending_until before the test
        # Package 6 reduce_no_as_sell: its start-up self-test leg (None = not run yet, "ok", "off" = refused: today's
        # behaviour for this run), the background run, when to retry it, and this plan's covered NO per exchange/level
        self.nosell_state, self.nosell_future, self.nosell_next, self.cover_planned = None, None, 0.0, {}
        self.nosell_eid, self.nosell_hold = None, 0.0
        # Package 7 pair_no_unwind_max_cost: its start-up check leg (a paired covered NO sale), state as nosell's
        self.pairno_state, self.pairno_future, self.pairno_next, self.pairno_wait = None, None, 0.0, 0.0
        self.pairno_race, self.pairno_holds = None, {}
        self.selftest_gen = 0             # cancel_gen when the test started
        self.selftest_unlisted = 0        # tests in a row whose orders were accepted but never listed
        self.selftest_errors = 0          # tests in a row that crashed (a bug in the test)
        self.cancel_gen = 0               # +1 on every cancel-everything (the self-test checks it)
        self.load_markets()

    def check_clock(self):
        """Order expiry and the timing of the open use this computer's clock. Compare it with the exchange's
        (the Date header of the last response, 1 s resolution) and alert if they're far apart."""
        hdr, received = getattr(self.api, "last_date", (None, None))
        try:
            server = email.utils.parsedate_to_datetime(hdr)
        except (TypeError, ValueError, IndexError):
            return None
        skew = (received - server).total_seconds() - 0.5          # the header is truncated to the second
        if abs(skew) > self.cfg.max_clock_skew_seconds:
            util.alert(f"this computer's clock is {skew:+.0f} s off the exchange's - turn on automatic time sync "
                  f"(order expiry and the timing of the open depend on it)")
        return skew

    def on_reference_prices(self, moves):
        """Called by the Polymarket thread after every reading. Wakes the main loop so quotes follow
        within ~0.5 s, instead of at the next realtime event or after loop_seconds (up to 10 s away).
        With a healthy feed, cycles that download nothing cost no requests, so any price change wakes
        it. Without one, every cycle is a full check (~7 requests), so only a jump does."""
        biggest = max(moves.values(), default=0.0)
        if biggest >= self.cfg.ref_jump_threshold or (biggest > 0 and self.feed and self.feed.healthy()):
            self.wake.set()

    def load_reference_prices(self):
        """Outside reference prices (ref_prices.py), only if a mapping file exists next to the bot."""
        path = bot_path(self.cfg.ref_map_file) if self.cfg.ref_map_file else None
        if not path or not os.path.exists(path):
            return None
        try:
            from ref_prices import REF, ReferencePrices
        except ImportError:
            log.warning("%s exists but ref_prices.py is missing - reference prices off", path)
            return None
        refs = ReferencePrices(path, replace(REF, refresh_seconds=self.cfg.ref_refresh_seconds))
        refs.on_refresh = self.on_reference_prices
        log.info("reference prices on: %d contracts mapped in %s", len(refs.mapping), path)
        return refs

    def in_parallel(self, fn, items):
        """Run fn(item) for every item on up to parallel_requests threads.
        Returns {item: result, or the exception it raised}: one failure never stops the others."""
        futures = {item: self.pool.submit(fn, item) for item in items}
        out = {}
        for item, f in futures.items():
            try:
                out[item] = f.result()
            except Exception as e:
                out[item] = e
        return out

    # ------------------------------------------------------------------------------ markets
    def load_markets(self):
        """(Re)read the tournament's open markets. New exchanges are added. Exchanges whose market
        has closed are cancelled and forgotten, so their failing requests can't clog every cycle."""
        only = {x.strip() for x in self.cfg.only_exchanges.split(",") if x.strip()}
        t_end = parse_ts(self.t.get("endDate"))
        seen = set()
        for m in self.api.markets():
            if m.get("isComposite"):
                continue
            close = min([d for d in (parse_ts(m.get("settlementDate")), t_end) if d], default=None)
            exchanges = m.get("exchanges", [])
            x = RACE_TITLE.match(m.get("title", ""))
            for e in exchanges:
                eid = str(e["id"])
                if only and eid not in only:
                    continue
                seen.add(eid)
                if eid in self.ex:
                    continue
                if x and len(exchanges) == 1:        # a party's YES in a race
                    party, group = x.group(1), x.group(2)
                    label = f"{party[:3]} {group}"
                else:                                # anything else: its own market is its group
                    party, group = None, f"market:{m['id']}"
                    label = f"{m.get('title', '')[:24]} {e.get('option') or ''}"
                self.ex[eid] = Ex(eid, str(m["id"]), m.get("title", ""), label, group, party, close)
        for eid in [e for e in self.ex if e not in seen]:
            log.info("market closed/removed: %s - cancelling and dropping it", self.ex[eid].label)
            self.cancel(eid, self.sim_orders(eid), whole_exchange=True, quiet=True)
            del self.ex[eid]
        self.groups = defaultdict(list)
        for ex in self.ex.values():
            self.groups[ex.group].append(ex.eid)
        self.last_reload = util.time.monotonic()
        log.info("Tracking %d exchanges in %d races", len(self.ex), len(self.groups))

    def hours_to_close(self, ex):
        close = self.effective_close(ex)
        return (close - util.utcnow()).total_seconds() / 3600 if close else float("inf")

    def close_override(self):
        """Package 12 M1: close_override_utc as an aware datetime, or None (off, or invalid: unparseable, no time
        zone, outside its OVERRIDABLE range - ignored, with one alert per bad value)."""
        v = getattr(self.cfg, "close_override_utc", "")
        if not isinstance(v, str) or not v.strip():
            return None
        cache = getattr(self, "_close_ovr", None)
        if cache is not None and cache[0] == v:
            return cache[1]
        dt, (lo_s, hi_s) = parse_utc_setting(v), OVERRIDABLE["close_override_utc"]
        if dt is not None and not parse_utc_setting(lo_s) <= dt <= parse_utc_setting(hi_s):
            dt = None
        if dt is None:
            msg = (f"close_override_utc {v!r} is not an ISO UTC time in {lo_s}..{hi_s}: IGNORED (every market keeps "
                   f"its API close)")
            log.warning(msg)
            util.alert(msg)
        self._close_ovr = (v, dt)
        return dt

    def effective_close(self, ex):
        """Package 12 M1: the close every pre-close rule uses = max(the API close, close_override_utc): an override
        only ever EXTENDS a close. No API close -> None (never closes), whatever the override."""
        if not ex.close:
            return ex.close
        ovr = self.close_override()
        return max(ex.close, ovr) if ovr is not None else ex.close

    # ------------------------------------------------------------------------------ one cycle
    def cycle(self):
        cfg = self.cfg
        now, now_m = util.utcnow(), util.time.monotonic()
        self.cycle_started, self.cycle_alerted = now_m, False
        self.cycle_phases, self.phase_t = {}, now_m
        left = self.api.pause_left() if hasattr(self.api, "pause_left") else 0.0
        if cfg.pause_skip_cycles and left > 0:
            # The exchange said wait (429): every read would only sleep out the pause inside the cycle, the loop
            # blocked for minutes. Apply finished writes and come back after it.
            self.harvest_writes()
            if not self.pause_logged:
                self.pause_logged = True
                log.warning("exchange rate-limit pause: %.0f s left - cycles skipped until it ends", left)
            self.cycle_started = None             # (last_cycle_done is NOT touched: a chain of pauses must still
            self.wake.wait(timeout=min(left, 1.0))    #  reach the watchdog's alert and exit: that is its purpose)
            return
        self.pause_logged = False
        tl = getattr(self.api, "tl", None)
        if tl is not None:                        # main-thread writes never wait long for the write budget
            tl.max_write_wait = cfg.write_wait_seconds + cfg.main_write_wait_margin
        try:
            self.cycle_body(cfg, now, now_m)
        finally:
            if tl is not None:
                tl.max_write_wait = None
            self.phase_mark("other")
            self.last_cycle_seconds = util.time.monotonic() - now_m
            self.last_cycle_phases = {k: round(v, 1) for k, v in self.cycle_phases.items()}
            self.health["last_cycle_seconds"] = round(self.last_cycle_seconds, 1)
            self.health["last_cycle_phases"] = self.last_cycle_phases
            self.cycle_started = None
            self.last_cycle_done = util.time.monotonic()

    def phase_mark(self, name):
        """Cycle timing: the time since the previous mark is added to phase `name` (summary line, status.json)."""
        t = util.time.monotonic()
        phases = getattr(self, "cycle_phases", None)
        if phases is not None:
            phases[name] = phases.get(name, 0.0) + t - getattr(self, "phase_t", t)
        self.phase_t = t

    def phases_text(self):
        """The last cycle's length and where it went, for the summary line: '31.2 s (reads 2.1, ...)'."""
        ph = getattr(self, "last_cycle_phases", None) or {}
        parts = ", ".join(f"{k} {v:.1f}" for k, v in ph.items() if v >= 0.05)
        return f"{self.last_cycle_seconds:.1f} s" + (f" ({parts})" if parts else "")

    def progress(self, phase):
        """Inside a cycle: if it's running long, keep status.json fresh (it's otherwise written only after a
        cycle, so a 4-minute cycle looked like a dead bot) and alert once when it passes slow_cycle_alert_seconds."""
        if self.cycle_started is None:
            return
        now_m = util.time.monotonic()
        running = now_m - self.cycle_started
        if running < 10 or now_m - self.last_progress_write < 5:
            return
        self.last_progress_write = now_m
        self.health["cycle_running_seconds"], self.health["cycle_phase"] = round(running), phase
        self.write_status(ok=True)
        if running >= self.cfg.slow_cycle_alert_seconds and not self.cycle_alerted \
                and now_m - self.last_slow_alert >= 900:
            self.cycle_alerted, self.last_slow_alert = True, now_m
            util.alert(f"slow cycle: {running:.0f} s so far ({phase}) - the exchange may be slow")

    def cycle_body(self, cfg, now, now_m):

        # 0. What does this cycle need to read? ----------------------------------------------------
        # With a healthy realtime feed, most cycles are triggered by a pushed event and only read what
        # changed. A FULL check (bulk prices, positions, orders) runs every realtime_heartbeat_seconds,
        # after a resync request, and on every cycle when there is no feed (plain polling).
        realtime = bool(self.feed and self.feed.healthy())
        dirty, fill_event, resync, settled = self.feed.take() if self.feed else (set(), False, False, False)
        full = not realtime or resync or now_m - self.last_full_check >= cfg.realtime_heartbeat_seconds
        if full:
            self.last_full_check = now_m
        if settled:
            self.last_reload = -1e9               # a market settled: reload the market list next cycle
        self.pending_dirty |= dirty & set(self.ex)
        # Account value (kill switch) changes slowly: read it every slow_poll_seconds, always on a full
        # check, so it's read together with our open orders (the locked-cash correction compares the two).
        slow_poll = full and now_m - self.last_slow_poll >= cfg.slow_poll_seconds
        if slow_poll:
            self.last_slow_poll = now_m
        # Positions change only through fills. Our open orders change mostly through our own placements
        # and cancels, which we track ourselves (self.my_orders), so the list is only re-read on full
        # checks, or when we're unsure of it (orders_stale: an unclear placement, a take, an arbitrage).
        read_orders = full or self.orders_stale or self.cached_orders is None
        read_positions = read_orders or fill_event or self.cached_pos is None
        read_fills = fill_event or slow_poll

        # 0b. Results of order writes that finished since the last cycle (slow ones run in the background).
        self.harvest_writes()
        self.update_burst(now_m)

        # 1. Account state: independent reads, sent at the same time ------------------------------
        f_pos = self.pool.submit(self.api.positions) if read_positions else None
        f_orders = self.pool.submit(self.api.open_orders, self.tid) if read_orders else None
        f_pnl = self.pool.submit(self.api.pnl) if slow_poll else None
        pos_failed = False
        if read_positions:                        # if a read fails, the cycle fails...
            try:
                self.cached_pos = f_pos.result()
            except ApiError as e:
                # ...except day one's 409 "holdings cannot be valued" (a market without a valuation price):
                # positions only change through fills, so the last good read is fine for a cycle or two.
                if e.status != 409 or self.cached_pos is None:
                    raise
                self.positions_fallback(e, now_m)     # raises once the last read is too old to trade on
                pos_failed = True
            else:
                if self.pos_fallbacks:
                    log.warning("positions readable again after %d cycles (%.0f s) on an old read",
                                self.pos_fallbacks, now_m - self.pos_fallback_since)
                self.pos_fallbacks, self.pos_fallback_since, self.pos_fallback_failing = 0, None, False
        if read_orders:
            self.cached_orders = f_orders.result()
            self.orders_stale = False
        if pos_failed:
            self.orders_stale = True              # read positions (and orders) again next cycle
        pos, raw_orders = self.cached_pos, self.cached_orders
        # position.quantity is already signed by the API: + YES shares, - NO shares.
        self.pos_marks = {str(p["exchangeId"]): float(next(p[k] for k in Bot.POS_PRICE_KEYS if p.get(k) is not None))
                          for p in pos.get("positions", []) if not p.get("settled")
                          and any(p.get(k) is not None for k in Bot.POS_PRICE_KEYS)} if isinstance(pos, dict) else {}
        inv = {str(p["exchangeId"]): float(p.get("quantity") or 0)
               for p in pos.get("positions", []) if not p.get("settled")}
        self.held = {e: q for e, q in inv.items() if q and e in self.ex}
        reserved = reserved_cash(raw_orders)
        self.update_lots(inv, util.time.time())
        if self.db and cfg.record_positions and read_positions and not pos_failed:
            self.record_positions(pos, f_pnl, now_m, fill_event)
        if f_pnl is not None:
            self.last_equity = self.checked_account_value(self.account_value(pos, f_pnl), reserved, inv)
            if self.kill_switch(self.last_equity):
                return
        equity = self.last_equity

        # Our REAL resting orders (used to clean the books), and the orders we compare against
        # when reconciling: the real ones when live, the simulated ones in a dry run.
        if self.api.live:
            if read_orders:
                self.sync_orders(raw_orders, now_m)
            mine_real = self.orders_by_eid(now)
        else:
            mine_real = defaultdict(list)
            for r in filter(None, map(parse_order, raw_orders)):
                mine_real[r.eid].append(r)
        resting = mine_real if self.api.live else self.sim_by_eid()
        if getattr(cfg, "cash_gate_enabled", False) and self.api.live:   # Package 8: this cycle's cash budget
            self.cash_gate_cycle(f_pnl, pos, raw_orders)     # (also while stale: a good read resumes the gate)

        # 2. Order books (only the ones that changed) ------------------------------------------
        if full or len(self.pending_dirty) > cfg.bulk_check_over:
            self.refresh_books(mine_real, now_m)  # bulk prices for everything, then only books that moved
        else:
            self.download_books(self.books_to_fetch([]), mine_real)   # just the few the feed reported
            self.reverify_books(mine_real, now_m)  # books unconfirmed for a while: cheap bulk check first

        self.log_priming()
        self.phase_mark("reads")
        self.progress("books")

        # 3. Fair values ---------------------------------------------------------------------------
        # book_fvs: the tournament book's own price (parties in a race scaled to sum to 1).
        # fvs:      what we quote around - book_fvs leaned toward Polymarket by ref_weight.
        # The guard compares Polymarket with book_fvs: comparing it with the already-leaned fvs would
        # shrink every gap to (1 - ref_weight) of its real size and make the 5c guard a ~17c guard.
        book_fvs = {eid: fair_value(ex.book, cfg) if now_m - ex.verified < cfg.book_stale else None
                    for eid, ex in self.ex.items()}
        for members in self.groups.values():
            if len(members) > 1:
                book_fvs.update(normalise({e: book_fvs[e] for e in members}))
        refs, liquid = self.reference_prices(book_fvs)
        self.cur_refs, self.cur_liquid = refs, liquid
        self.mark_ref_moves()
        self.reference_jump_guard(now_m)
        fvs = dict(book_fvs)
        self.ref_only = self.thin_book_prices(fvs, refs, liquid, now_m) if cfg.ref_only_enabled else set()
        self.warn_unpriced_held(fvs, book_fvs, refs, liquid, now_m)
        self.update_tilt(book_fvs, refs, liquid, inv, now_m)      # T2.1: runs (read-only) with the flag off too
        if cfg.ref_weight > 0 and refs:
            for eid, r in refs.items():
                if fvs.get(eid) is not None and eid in liquid and eid not in self.ref_only:   # liquid only
                    fvs[eid] = blend_fv(fvs[eid], r, cfg)
            for members in self.groups.values():
                if len(members) > 1:
                    fvs.update(normalise({e: fvs[e] for e in members}))

        # 3b. How many shares to quote in each market (every size_plan_seconds, or on an account step) ----
        if cfg.size_by_activity:
            self.update_size_plan(now_m, fvs)

        self.phase_mark("fair_values")
        # 4. Fills (every slow_poll_seconds, and straight after a fill) --------------------------------
        if read_fills:
            new_fills = self.log_fills(fvs)
            if new_fills:
                self.pos_record_due = True            # recorder: note the positions straight after a fill
        self.note_turnover(new_fills if read_fills else ())
        self.refresh_turnover(now_m)
        self.mm_inv_step(new_fills if read_fills else (), inv)   # P14: MM inventory lots (read-only, never raises)

        # 5. Guaranteed arbitrage inside races (takes liquidity; our quotes there are pulled first) --
        # Package 7 pair_unwind_followup: legs an earlier pair unwind left unequal are evened up first
        owed_races = (self.pair_followup_step(fvs, now_m, mine_real, inv=inv) if getattr(self, "pair_owed", None)
                      else set())
        arb_races = self.take_arbitrage(inv, fvs, mine_real, now_m) if self.running else set()
        arb_races |= owed_races

        # 6. Portfolio-level risk ------------------------------------------------------------------
        eff = self.effective_inventory(inv)
        worst = self.total_worst_case(inv, fvs)
        party_delta = sum(PARTY_SIGN.get(ex.party, 0) * inv.get(eid, 0.0) for eid, ex in self.ex.items())
        if cfg.bloc_delta_enabled:                # Package 10 A2: the bloc delta the party cap uses this cycle
            self.bloc_refresh(inv)
        # Hysteresis: once in reduce-only, both caps are reduce_only_hysteresis lower until it has been left.
        hyst = min(cfg.reduce_only_hysteresis, cfg.max_worst_case_frac / 2) if self.global_reduce else 0.0
        if cfg.risk_model == "correlated":
            # never above the sum of maxima, which is a hard bound (for a few big positions 3 sd exceeds it)
            risk = min(worst, self.settlement_risk(inv, fvs, party_delta))
            global_reduce = bool(equity) and (risk > (cfg.max_worst_case_frac - hyst) * equity
                                              or worst > (cfg.worst_case_backstop_frac - hyst) * equity)
        else:
            risk = worst
            global_reduce = bool(equity) and worst > (cfg.max_worst_case_frac - hyst) * equity
        if global_reduce != self.global_reduce:
            log.warning("%s reduce-only: risk %.0f, worst case %.0f, account %s", "ENTERING" if global_reduce
                        else "leaving", risk, worst, f"{equity:.0f}" if equity is not None else "?")
        self.global_reduce = global_reduce
        if getattr(cfg, "alloc_swap_room_netting", False):   # P14.1 5: the room a swap's own legs would leave
            self.alloc_fvs = dict(fvs)
        if cfg.mm_risk_reserve_wc > 0 or cfg.mm_risk_reserve_corr > 0 or self.mmr_paused:   # P12 ops (after it:
            self.mm_risk_room_update(worst, risk, equity,                                   #  never changes it)
                                     self.mm_room_part(inv, fvs, worst, risk)               # P14 3 mm_room_guard
                                     if getattr(cfg, "mm_room_guard", False) else None)
        capital = self.capital_in_positions(pos, inv, fvs)
        cap_frac = capital / equity if equity else None
        self.update_capital_ceiling(cap_frac, cfg)
        self.update_adding_resume(cap_frac, cfg)
        self.refresh_mark_sd(now_m)
        frag = self.update_mark_frag(inv, cfg)
        ages = self.portfolio_age(util.time.time())
        if full:                                  # summary line on full checks only (event cycles can be every 2 s)
            log.info("%s | account %s (locked in orders %.0f, %s) | worst-case loss %.0f (risk %.0f)%s | party delta %+.0f | "
                     "priced %d/%d | resting %d | last cycle %s%s",
                     "realtime" if realtime else ("polling (realtime connecting)" if self.feed else "polling"),
                     f"{equity:.0f}" if equity is not None else "?", reserved,
                     {"add": "added back", "ignore": "already included"}.get(self.reserved_mode, "detecting"), worst, risk,
                     " -> REDUCE-ONLY" if global_reduce else "", party_delta,
                     sum(v is not None for v in fvs.values()), len(fvs), sum(len(v) for v in resting.values()),
                     self.phases_text(), self.ev_line_part())
        self.health = {"account_value": equity, "locked_in_orders": round(reserved, 2),
                       "reserved_cash_mode": self.reserved_mode or "detecting", "worst_case_loss": round(worst, 2),
                       "reduce_only": global_reduce, "party_delta": party_delta,
                       "settlement_risk": round(risk, 2), "risk_model": cfg.risk_model,
                       "markets_priced": sum(v is not None for v in fvs.values()), "markets_tracked": len(fvs),
                       "orders_resting": sum(len(v) for v in resting.values()),
                       "reference_prices": len(refs), "reference_prices_liquid": len(liquid),
                       "polymarket_fetch_seconds": getattr(self.refs, "fetch_seconds", None),
                       "burst_mode": self.burst,
                       "arbitrages_total": self.arbs_total,
                       "pair_unwinds_total": self.unwinds_total,
                       "rate_limited_total": getattr(self.api, "rate_limited", 0),     # should stay 0
                       "request_budget_per_min": round(getattr(self.api, "budget", 0)),
                       "write_budget_per_min": round(getattr(self.api, "wbudget", 0)),
                       "requests_last_min": round(getattr(self.api, "budget", 0)) - getattr(self.api, "budget_left", lambda: 0)(),
                       "realtime": "connected" if realtime else ("reconnecting" if self.feed else "off"),
                       "realtime_events": self.feed.events if self.feed else 0,
                       "takes_total": self.takes_total,
                       "takes_skipped_budget": self.takes_skipped_budget,
                       "arbs_skipped_budget": self.arbs_skipped_budget,
                       "write_budget_wait_total": getattr(self.api, "write_budget_wait_total", 0),
                       "quote_capital_planned": round(getattr(self, "plan_capital", 0.0)),
                       "biggest_quotes": {self.ex[e].label: s for e, s in sorted(self.size_plan.items(),
                                          key=lambda kv: -kv[1])[:6] if e in self.ex},
                       "capital_in_positions": round(capital),
                       "capital_in_positions_frac": round(cap_frac, 3) if cap_frac is not None else None,
                       "capital_ceiling_active": self.capital_over,
                       "capital_ceiling_adding_factor": self.ceiling_adding_factor(cfg),
                       "capital_ceiling_adding_resume": self.adding_resume,
                       "portfolio_age_hours": round(ages[0], 2), "positions_over_3h": ages[1],
                       "positions_over_12h": ages[2],
                       **frag,
                       "positions": {self.ex[e].label: q for e, q in inv.items() if q and e in self.ex}}

        self.health.update(books_loaded=self.books_loaded(), markets_priced_from_tops=len(self.ref_tops))
        if getattr(cfg, "take_respect_reserve", False) or getattr(self, "take_reserve_blocked", 0):   # P10 (absent while off)
            self.health["take_reserve_blocked"] = getattr(self, "take_reserve_blocked", 0)
        if cfg.bloc_delta_enabled:                # Package 10 A2 (absent while off)
            self.health["bloc_delta"] = round(self.bloc_delta, 2)
            self.health["bloc_delta_frac"] = round(self.bloc_delta / equity, 4) if equity else None

        if self.st_on():                          # P15 state_max_usd: each state's collateral this cycle
            self.st_refresh(inv, now_m)
        # 6b. Take tournament quotes that Polymarket says are clearly stale (confirmed over 2 readings) ---
        taken = (self.take_stale_quotes(refs, liquid, fvs, inv, mine_real, global_reduce, party_delta, now_m)
                 if self.running else set())
        # 6d. Package 10 B: the capital allocator (sell -> cash read -> buy, immediate-or-cancel; see Config)
        if self.running and (cfg.alloc_enabled or self.alloc_pairs or self.alloc_set_races or self.alloc_ladder
                             or (self.api.live and self.sl_orders())):
            try:
                taken |= self.alloc_tick(now, inv, mine_real, now_m, skip=taken | arb_races)
            except ApiError:
                raise                                 # (as the takes: the cycle's own error handling)
            except Exception:                         # an allocator bug must never stop the market maker
                self.orders_stale = True
                log.exception("allocator tick failed - skipped this cycle")
        # 6e. P15: the harvest ladder (resting maker levels; pulls)
        if self.running and self.hv_on():
            try:
                taken |= self.hv_tick(now, inv, mine_real, now_m, skip=taken | arb_races)
            except ApiError:
                raise                                 # (as the takes: the cycle's own error handling)
            except Exception:                         # a ladder bug must never stop the market maker
                self.orders_stale = True
                log.exception("harvest ladder tick failed - skipped this cycle")

        self.phase_mark("fills_risk_takes")
        # 7. Decide + reconcile each exchange. One write at a time (parallel_writes = 1): cancels happen now,
        #    new orders are batched after. Otherwise every change is planned first, then sent in parallel.
        new_orders, changes = [], []
        self.mmr_tail_now = 0                     # P12 ops: tail adding sides held back this cycle (decide counts)
        if self.mmf_recycling:                    # P14 1: this cycle's recycler overrides (decide fills it again)
            self.mmf_recycling = {}
        if self.cash_gate_on():                   # Package 8: the quotes' plan budget, after this cycle's takes
            self.cg_plan_left, self.cg_capped_now = self.cash_left(), 0
            if self.hv_plans:                     # P15: the quotes leave the ladder the part of its carve-out it
                self.cg_plan_left = max(0.0, self.cg_plan_left - self.hv_quote_hold())   # still waits to place
        for eid, ex in list(self.ex.items()):
            if not self.running:          # Ctrl+C: stop touching the book immediately
                return
            if self.api.live and (ex.group in arb_races or eid in taken):
                continue                  # just traded here: positions changed, re-quote next cycle
            try:
                ex.quote = self.decide(ex, fvs.get(eid), inv, eff, global_reduce, party_delta, now_m,
                                       refs.get(eid), book_fvs.get(eid), eid in liquid)
                if cfg.parallel_writes > 1:
                    ch = self.plan_exchange(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
                    if ch:
                        changes.append(ch)
                else:
                    new_orders += self.reconcile(ex, ex.quote, resting.get(eid, []), fvs.get(eid), now, now_m)
            except ApiError as e:         # one exchange failing must not stop the others
                log.error("exchange %s (%s): %s", eid, ex.label, e)
        self.phase_mark("decide")
        self.progress("sending orders")
        if self.running:
            if cfg.parallel_writes > 1:
                self.send_changes(changes)
            else:
                self.place(new_orders, now_m)
                self.phase_mark("send")
        # Orders resting NOW, after this cycle's changes (the health snapshot above was taken before them).
        self.health["orders_resting"] = len(self.my_orders) if self.api.live else len(self.sim)
        self.health["selftest_state"] = self.selftest_state()
        mmr = self.mm_risk_status()               # P12 ops mm_risk_reserve_* (absent while off and never paused)
        if mmr is not None:
            self.health["mm_risk_room"] = mmr
        if getattr(cfg, "cash_gate_enabled", False):   # Package 8 (absent while the gate is off)
            self.health["cash_gated"] = getattr(self, "cash_gated", 0)
            self.health["cash_trimmed"] = getattr(self, "cash_trimmed", 0)
            self.health["cash_capped_quotes"] = getattr(self, "cg_capped_now", 0)
            self.health["cash_gate_left"] = round(self.cash_left(), 2) if getattr(self, "cg_cash", None) is not None else None
            age = self.cash_read_age()
            self.health["cash_gate_read_age"] = round(age, 1) if age is not None else None   # s since a good read
        self.health["fl_bias_markets"] = {k: sum(1 for x in self.ex.values() if x.fl_tag.startswith(f" fl:{k}"))
                                          for k in ("bid", "ask")}
        self.health["market_edge_markets"] = len(self.market_edge) if self.cfg.market_edge_enabled else 0
        self.health.update(self.turnover_health(inv, fvs))

        self.mm_funding_tick()                    # P14 4: the below-half clock and its alert (never raises)

        # 8. Snapshot for later analysis (every record_seconds) -------------------------------------
        self.record(fvs, now_m)

    def reference_prices(self, book_fvs=None):
        """({eid: Polymarket probability}, {eids whose Polymarket price is liquid}), both empty if the
        feature is off. Liquid = a real two-sided Polymarket market with spread <= ref_liquid_spread.
        A price more than ref_max_plausible_gap from the tournament book's own price is dropped (and you
        get one alert): that's almost certainly a wrong match, e.g. a candidate who was replaced."""
        if not self.refs:
            return {}, set()
        try:
            by_key = self.refs.get()
            spreads = self.refs.spreads() if hasattr(self.refs, "spreads") else {}
        except Exception as e:            # ref_prices shouldn't raise, but never let it stop quoting
            log.warning("reference prices unavailable: %s", e)
            return {}, set()
        refs, liquid = {}, set()
        for eid, ex in self.ex.items():
            key = f"{ex.group}|{ex.party}"   # same key format as ref_prices.ref_key: "Ohio Senate|Republican"
            if key in by_key:
                book = (book_fvs or {}).get(eid)
                if book is not None and abs(by_key[key] - book) > self.cfg.ref_max_plausible_gap:
                    if key not in self.ref_rejected:
                        self.ref_rejected.add(key)
                        util.alert(f"Polymarket price for {ex.label} ({by_key[key]:.3f}) is {100 * abs(by_key[key] - book):.0f}c "
                              f"from the tournament book ({book:.3f}) - ignoring it. Probably a wrong match in "
                              f"ref_map.json (e.g. a replaced candidate): run `python ref_prices.py check`")
                    continue
                self.ref_rejected.discard(key)       # back in a plausible range: use it again
                refs[eid] = by_key[key]
                s = spreads.get(key)
                if s is not None and s <= self.cfg.ref_liquid_spread:
                    liquid.add(eid)
        return refs, liquid

    def positions_fallback(self, e, now_m):
        """Positions answered 409: reuse the last read, but only for positions_stale_max_cycles cycles in a row or
        positions_stale_max_seconds, whichever first. Fills keep landing meanwhile, so inventory, position limits,
        skew, reduce-only and the risk model would all run on a frozen inventory; past the limit this re-raises,
        the cycle fails and on_cycle_error pulls every quote (after max_failed_cycles) until positions read again.
        Logged when the run of 409s starts and when it ends (not every cycle)."""
        cfg = self.cfg
        if not self.pos_fallbacks:
            self.pos_fallback_since = now_m
            log.warning("positions unavailable (%s) - using the last read (at most %d cycles / %.0f s)",
                        e, cfg.positions_stale_max_cycles, cfg.positions_stale_max_seconds)
        self.pos_fallbacks += 1
        age = now_m - self.pos_fallback_since
        if ((cfg.positions_stale_max_cycles and self.pos_fallbacks > cfg.positions_stale_max_cycles)
                or (cfg.positions_stale_max_seconds and age >= cfg.positions_stale_max_seconds)):
            if not self.pos_fallback_failing:
                self.pos_fallback_failing = True
                log.error("positions still unavailable after %d cycles (%.0f s) - failing cycles (quotes are "
                          "pulled) until they read again", self.pos_fallbacks - 1, age)
            raise e

    def thin_book_prices(self, fvs, refs, liquid, now_m):
        """R5: markets whose book is too thin for a depth-checked price (fair_value None) but that have a
        liquid Polymarket price get fv = Polymarket, if the tournament's raw best bid/ask (other traders,
        any size, verified recently) are two-sided, not wider than max_spread_for_fv, and their mid is
        within ref_only_max_gap of Polymarket. Fills fvs in place; returns the set of those eids.
        The best bid/ask come from the downloaded book, or (ref_only_use_tops) from a fresh bulk reading when
        there is no current book; those eids are also in self.ref_tops, for decide (see r5_top)."""
        cfg, out = self.cfg, set()
        self.ref_tops = {}
        for eid, ex in self.ex.items():
            if fvs.get(eid) is not None or eid not in liquid or eid not in refs:
                continue
            top = self.r5_top(ex, now_m)
            if top is None:
                continue
            bb, ba, from_tops = top
            if ba <= bb or ba - bb > cfg.max_spread_for_fv or abs((bb + ba) / 2 - refs[eid]) > cfg.ref_only_max_gap:
                continue
            fvs[eid] = refs[eid]
            out.add(eid)
            if from_tops:
                self.ref_tops[eid] = (bb, ba)
        return out

    def warn_unpriced_held(self, fvs, book_fvs, refs, liquid, now_m):
        """A market we hold a position in with no fair value for unpriced_held_warn_cycles cycles in a row gets
        ONE warning with the reason (it is then neither quoted nor reduced); again only after it was priced."""
        n = self.cfg.unpriced_held_warn_cycles
        for eid in list(self.unpriced_held):
            if eid not in self.held or fvs.get(eid) is not None:
                self.unpriced_held.pop(eid, None)
                self.unpriced_warned.discard(eid)
        if n <= 0:
            return
        for eid in self.held:
            if fvs.get(eid) is not None:
                continue
            k = self.unpriced_held[eid] = self.unpriced_held.get(eid, 0) + 1
            if k >= n and eid not in self.unpriced_warned:
                self.unpriced_warned.add(eid)
                ex = self.ex[eid]
                log.warning("UNPRICED held market %s (inv %+.0f) for %d cycles: %s", ex.label, self.held[eid], k,
                            self.unpriced_reason(ex, book_fvs, refs, liquid, now_m))

    def unpriced_reason(self, ex, book_fvs, refs, liquid, now_m):
        """Why a market has no fair value (for warn_unpriced_held)."""
        cfg, eid = self.cfg, ex.eid
        has_book = ex.book is not None and now_m - ex.verified < cfg.book_stale
        if has_book and fair_value(ex.book, cfg) is not None and book_fvs.get(eid) is None:
            return "a race leg has no price (normalise needs all legs)"
        if eid not in refs:
            return ("no book downloaded" if ex.book is None else "book stale" if not has_book
                    else "book too thin/wide") + ", no Polymarket price (or rejected by ref_max_plausible_gap)"
        if eid not in liquid:
            return "book thin/missing and Polymarket not liquid"
        if not cfg.ref_only_enabled:
            return "book thin/missing and ref_only_enabled off"
        top = self.r5_top(ex, now_m)
        if top is None:
            if has_book:
                return "book one-sided (other traders)"
            t = self.other_tops.get(eid)
            if not cfg.ref_only_use_tops:
                return "no book downloaded and ref_only_use_tops off"
            if t is None or now_m - t[2] > cfg.tops_max_age:
                return "no book downloaded, no fresh bulk tops"
            side = 0 if t[0] is None else 1
            raw = self.last_tops.get(eid, (None, None))[side]
            return "no book downloaded, bulk tops one-sided (%s)" % (
                ("bid", "ask")[side] + (" blanked by our own order at the best" if raw is not None else " empty"))
        bb, ba, _ = top
        if ba <= bb or ba - bb > cfg.max_spread_for_fv:
            return f"spread {bb:.3f}/{ba:.3f} wider than max_spread_for_fv"
        return f"mid {(bb + ba) / 2:.3f} more than ref_only_max_gap (Polymarket {refs[eid]:.3f})"

    def r5_top(self, ex, now_m):
        """(other traders' best bid, best ask, from_tops) for R5, or None (one-sided, or nothing current).
        A book confirmed within book_stale is used as before. Without one (after a restart every book is None;
        downloading 237 takes minutes of request budget) and with ref_only_use_tops: the bulk best bid/ask, if
        read within tops_max_age and two-sided once our own orders are discounted (see others_top)."""
        cfg = self.cfg
        if ex.book is not None and now_m - ex.verified < cfg.book_stale:
            b = ex.book
            if not b.get("bids") or not b.get("asks"):
                return None
            return b["bids"][0]["price"], b["asks"][0]["price"], False
        if cfg.ref_only_use_tops:
            t = self.other_tops.get(ex.eid)
            if t and t[0] is not None and t[1] is not None and now_m - t[2] <= cfg.tops_max_age:
                return t[0], t[1], True
        return None

    def note_other_tops(self, tops, mine_real, now_m):
        """Remember the bulk best bid/ask as OTHER traders' best prices (see others_top), with the time read.
        Once our own quote is the best price, a reading can't see the others behind it: that side keeps the
        others' price seen before (if not better than ours), as long as that one was still within tops_max_age.
        The fresh reading confirms nobody is better than us, so the entry's time moves on; a stale carried side
        can only be off for pennying (others are behind us: nothing to cross), and the downloaded book, which
        books_to_fetch fetches first for these markets, takes over soon anyway.
        """
        for eid, top in tops.items():
            bid, ask = others_top(top, mine_real.get(eid, []))
            prev = self.other_tops.get(eid)
            t = now_m
            if prev is not None and now_m - prev[2] <= self.cfg.tops_max_age:
                if bid is None and top[0] is not None and prev[0] is not None and prev[0] <= top[0] + 1e-9:
                    bid, t = prev[0], prev[2]             # carried: keeps the time it was really seen, so a
                if ask is None and top[1] is not None and prev[1] is not None and prev[1] >= top[1] - 1e-9:
                    ask, t = prev[1], prev[2]             #   side we can't see expires after tops_max_age
            self.other_tops[eid] = (bid, ask, t)

    def mark_ref_moves(self):
        """Which markets' Polymarket price just moved (urgent: see urgent_ref_move). Cleared once handled."""
        version = getattr(self.refs, "version", 0)
        if not self.refs or version == self.ref_version_urgent:
            self.ref_moved = set()
            return
        self.ref_version_urgent = version
        moved = {k for k, m in getattr(self.refs, "last_moves", {}).items() if m >= self.cfg.urgent_ref_move}
        self.ref_moved = {e for e, ex in self.ex.items() if f"{ex.group}|{ex.party}" in moved}
        for e in self.ref_moved:
            self.ex[e].ref_moved_at = util.time.monotonic()


    def legs(self, ex):
        """Number of markets in this market's race (1 for a lone market)."""
        return max(1, len(self.groups.get(ex.group) or ()))

    def load_tilt(self):
        """T2.1: the tilt estimate the previous run left in status.json ({} if none: the estimate starts at 0).
        The dict also carries "saved_wall": tilt_state's own save time, else the file's mtime."""
        try:
            path = bot_path(self.cfg.status_file)
            with open(path) as f:
                st = json.load(f)
            d = st.get("tilt_state") or ({"s": st["tilt_s"], "ready": True} if "tilt_s" in st else {})
            if d and not isinstance(d.get("saved_wall"), (int, float)):
                d = dict(d, saved_wall=os.path.getmtime(path))
            return d
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            return {}


    def tilt_state_dict(self):
        """status.json tilt_state: the estimate plus its own save time (saved_wall; the old ramp-in times are gone)."""
        return {**self.tilt.to_dict(), "saved_wall": round(util.time.time(), 3)}

    def update_tilt(self, book_fvs, refs, liquid, inv, now_m):
        """T2.1, every cycle (read-only): feed the tilt estimator from the markets that are
        liquid, not R5 (ref_only), not headline, with a book price and Polymarket, and not under a jump guard; and
        tilt_exposure = sum over held markets of position x (raw Polymarket - c), c = 1/legs."""
        cfg, samples = self.cfg, []
        for eid, r in refs.items():
            ex = self.ex.get(eid)
            if (ex is None or r is None or book_fvs.get(eid) is None or eid not in liquid or eid in self.ref_only
                    or ex.group in cfg.headline_races or now_m < ex.cooldown_until):
                continue
            samples.append((r, book_fvs[eid], self.legs(ex)))
        self.tilt_s = self.tilt.update(samples, now_m)
        exposure = 0.0
        for eid, q in (inv or {}).items():
            ex, r = self.ex.get(eid), refs.get(eid)
            if q and ex is not None and r is not None:
                exposure += q * (r - tilted_ref(r, 1.0, self.legs(ex)))    # tilted_ref(r, 1, legs) = c
        self.tilt_exposure = exposure

    def reference_jump_guard(self, now_m):
        """After each new Polymarket reading, pull quotes on any market whose Polymarket price moved
        at least ref_jump_threshold since the previous reading. Polymarket usually moves first, so
        this steps back BEFORE the tournament book catches up. It also means a sudden unexplained
        spike can't drag our prices: if it reverts within the cooldown, we never quoted on it."""
        version = getattr(self.refs, "version", 0)
        if not self.refs or version == self.ref_version_seen:
            return
        self.ref_version_seen = version
        moved = {k: m for k, m in getattr(self.refs, "last_moves", {}).items() if m >= self.cfg.ref_jump_threshold}
        for ex in self.ex.values():
            key = f"{ex.group}|{ex.party}"
            if key in moved:
                ex.cooldown_until = max(ex.cooldown_until, now_m + self.cfg.ref_jump_cooldown_seconds)
                ex.ref_jump_at = now_m
                log.warning("POLYMARKET JUMP %s moved %.1fc - pulling quotes for %.0f s",
                            ex.label, 100 * moved[key], self.cfg.ref_jump_cooldown_seconds)

    # ------------------------------------------------------------------------------ books
    def refresh_books(self, mine_real, now_m):
        """FULL check of the books: bulk best bid/ask for every exchange, then re-download the books
        that need it (see books_to_fetch). The age rule exists because bulk prices can't show size
        changes or anything behind the best price."""
        # Bulk best bid/ask, 100 exchanges per request, all requests at once. A failed chunk just means
        # those exchanges fall back to the age rule this cycle.
        eids = list(self.ex)
        chunks = [tuple(eids[i:i + BULK_MAX_IDS]) for i in range(0, len(eids), BULK_MAX_IDS)]
        tops = {}
        for chunk, res in self.in_parallel(lambda c: self.api.bulk_prices(list(c), self.tid), chunks).items():
            if isinstance(res, Exception):
                log.warning("bulk prices failed for %d exchanges (%s) - using the age rule for them", len(chunk), res)
            else:
                tops.update(res)
        self.last_tops = tops
        self.last_tops_at = dict.fromkeys(tops, now_m)
        self.note_other_tops(tops, mine_real, now_m)

        extra = []
        for eid, ex in self.ex.items():
            mine = mine_real.get(eid, [])
            if tops.get(eid) == (None, None) and not mine:
                # Bulk data says the book is completely empty (e.g. not seeded yet): no need to download it.
                ex.book, ex.book_time, ex.verified = {"bids": [], "asks": []}, now_m, now_m
                self.pending_dirty.discard(eid)
                continue
            if ex.book is None:
                continue                          # books_to_fetch handles never-downloaded books
            if eid in tops and tops[eid] == predicted_top(ex.book, mine):
                ex.verified = now_m               # best prices unchanged: our copy still counts as current
            if eid in tops and tops[eid] != predicted_top(ex.book, mine):
                extra.append((1, ex.book_time, eid))
            elif eid in self.pending_dirty and eid in tops:
                # The feed said this book changed, but its best prices didn't: the change was deeper
                # in the book (sizes). Still worth a look, but after books whose best price moved.
                self.pending_dirty.discard(eid)
                extra.append((2, ex.book_time, eid))
            elif now_m - ex.book_time > self.cfg.book_max_age:
                extra.append((2, ex.book_time, eid))
        if self.running:
            self.download_books(self.books_to_fetch(extra), mine_real)

    def reverify_books(self, mine_real, now_m):
        """Books not confirmed current for book_reverify_seconds get one bulk best-price check (one request
        per 100), so a quiet book never goes stale (and gets its quotes pulled) just because nothing made the
        bot look at it. Unchanged best prices confirm it; a moved one is downloaded next cycle."""
        old = [eid for eid, ex in self.ex.items()
               if ex.book is not None and now_m - ex.verified >= self.cfg.book_reverify_seconds]
        if not old or not self.running or getattr(self.api, "budget_left", lambda: 10 ** 6)() <= 0:
            return
        chunks = [tuple(old[i:i + BULK_MAX_IDS]) for i in range(0, len(old), BULK_MAX_IDS)]
        for chunk, res in self.in_parallel(lambda c: self.api.bulk_prices(list(c), self.tid), chunks).items():
            if isinstance(res, Exception):
                log.warning("bulk re-check failed for %d books (%s)", len(chunk), res)
                continue
            self.last_tops.update(res)
            self.last_tops_at.update(dict.fromkeys(res, now_m))
            self.note_other_tops(res, mine_real, now_m)
            for eid in chunk:
                ex = self.ex.get(eid)
                if ex is None or eid not in res:
                    continue
                if res[eid] == predicted_top(ex.book, mine_real.get(eid, [])):
                    ex.verified = now_m
                else:
                    self.pending_dirty.add(eid)      # moved: download it next cycle

    def books_to_fetch(self, extra):
        """Which books to download this cycle, most urgent first:
             0  reported changed by the realtime feed
             1  never downloaded, or best price changed according to the bulk prices (`extra`)
             2  changed deeper in the book, or older than book_max_age (`extra`)
        At most max_books_per_cycle, and never more than the request budget has spare after keeping
        budget_reserve back for orders. The rest wait for a later cycle (reported books stay pending)."""
        candidates = [(0, self.ex[e].book_time, e) for e in self.pending_dirty if e in self.ex]
        # Never-downloaded books of markets we HOLD a position in come before everything (even feed-reported
        # changes): without a book a held market can go unpriced (R5 can't see the side our own reducing order
        # tops, see others_top), and then it is neither quoted nor reduced. Biggest exposure first (held_weights).
        # Then never-downloaded books where we have orders resting (e.g. adopted at a handover restart), then
        # the ones we quote from the bulk tops, then the rest, busiest (Polymarket volume) first.
        live = {o.eid for o in self.my_orders.values()}
        held = self.held_weights()
        vols = {}
        if self.refs and hasattr(self.refs, "volumes"):
            try:
                vols = self.refs.volumes() or {}
            except Exception:             # ordering only: never let it stop the books
                vols = {}
        for eid, ex in self.ex.items():
            if ex.book is not None:
                continue
            if eid in held:
                candidates.append((-1, -held[eid], eid))
            else:
                v = float(vols.get(f"{ex.group}|{ex.party}") or 0.0)
                candidates.append((1, -2.0 if eid in live else -1.0 if eid in self.ref_tops
                                   else -0.5 * v / (v + 1e5), eid))     # -0.5..0: busiest first
        candidates += extra
        todo = []
        for _, _, eid in sorted(candidates):
            if eid not in todo:
                todo.append(eid)
        cap, reserve = self.cfg.max_books_per_cycle, self.cfg.budget_reserve
        if self.priming():                # after a (re)start: books first (see startup_books_first)
            cap = min(max(cap, min(self.cfg.startup_prime_books, self.prime_need())), self.prime_allowance())
            reserve = min(reserve, self.cfg.startup_prime_reserve)
        spare = getattr(self.api, "budget_left", lambda: 10 ** 6)() - reserve
        return todo[:max(0, min(cap, spare))]

    def held_weights(self):
        """{eid: shares held x the price of the shares held} (p for YES, 1 - p for NO; p = last fair value,
        else Polymarket, else 0.5) for every market we hold a position in: the priming order (books_to_fetch)."""
        out = {}
        for eid, q in self.held.items():
            ex = self.ex.get(eid)
            if ex is None or not q:
                continue
            p = next((x for x in (ex.last_fv, ex.ref) if x is not None), 0.5)
            out[eid] = abs(q) * (p if q > 0 else 1 - p)
        return out

    def held_missing(self):
        """Markets we hold a position in whose book has never been downloaded, biggest exposure first."""
        w = self.held_weights()
        return sorted((e for e in w if self.ex[e].book is None), key=lambda e: -w[e])

    def books_loaded(self):
        return sum(ex.book is not None for ex in self.ex.values())

    def books_last_minute(self):
        now = util.time.monotonic()
        while self.book_reqs and now - self.book_reqs[0] >= 60.0:
            self.book_reqs.popleft()
        return len(self.book_reqs)

    def prime_allowance(self):
        """Book downloads still allowed in the current 60 s while priming (startup_prime_books_per_min)."""
        return max(0, self.cfg.startup_prime_books_per_min - self.books_last_minute())

    def write_reserve(self):
        """Requests order writes should leave free in the shared budget: write_read_reserve, plus while priming
        what the book downloads may still use this minute (capped by the books still missing). Without it the
        writes, which keep only 3 free, take every slot the book downloads (which keep 20 free) would need.
        For send_changes' `spare` (Engineer 1's region): budget_left() - self.write_reserve()."""
        r = self.cfg.write_read_reserve
        if self.priming():
            r += min(self.prime_allowance(), self.prime_need())
        return r

    def priming(self):
        """Startup priming is on: within startup_prime_seconds of the trading loop starting (fresh start or
        handover restart alike), and more than startup_prime_missing_frac of the books not downloaded yet.
        It never ends while a market we hold a position in has no downloaded book (held_missing), up to
        startup_prime_held_max_seconds after the start (a hard cap: a book that keeps failing can't hold it on)."""
        cfg = self.cfg
        if not cfg.startup_books_first or self.trading_since is None or not self.ex:
            return False
        elapsed = util.time.monotonic() - self.trading_since
        if elapsed <= cfg.startup_prime_held_max_seconds and self.held_missing():
            return True
        return self.prime_full()

    def prime_full(self):
        """The ordinary priming condition (startup_prime_seconds, startup_prime_missing_frac), without the
        held-market extension. Assumes startup_books_first and a trading start (see priming)."""
        cfg = self.cfg
        if self.trading_since is None or util.time.monotonic() - self.trading_since > cfg.startup_prime_seconds:
            return False
        return len(self.ex) - self.books_loaded() > cfg.startup_prime_missing_frac * len(self.ex)

    def prime_need(self):
        """Books priming still wants: every missing one, or once only the held-market extension keeps it on,
        just the held markets' (so the extension doesn't take the order writes' budget for other books)."""
        return len(self.ex) - self.books_loaded() if self.prime_full() else len(self.held_missing())

    def log_priming(self):
        """One line per cycle while priming the books, and one when it's over."""
        if self.priming():
            self.primed_logged = False
            missing = self.held_missing()
            if missing and util.time.monotonic() - self.trading_since > self.cfg.startup_prime_seconds:
                log.info("priming books: %d of %d loaded - extended (max %.0f s) for %d held market(s) without a "
                         "book: %s", self.books_loaded(), len(self.ex), self.cfg.startup_prime_held_max_seconds,
                         len(missing), ", ".join(self.ex[e].label for e in missing[:8]))
            else:
                log.info("priming books: %d of %d loaded", self.books_loaded(), len(self.ex))
        elif self.trading_since is not None and not getattr(self, "primed_logged", True):
            self.primed_logged = True
            log.info("priming books done: %d of %d loaded after %.0f s", self.books_loaded(), len(self.ex),
                     util.time.monotonic() - self.trading_since)

    def download_books(self, eids, mine_real):
        """Download books (in parallel), remove our own orders from them, and cache them on the Ex."""
        self.book_reqs.extend([util.time.monotonic()] * len(eids))     # for startup priming's per-minute cap
        for eid, book in self.in_parallel(lambda e: self.api.book(e, self.tid), eids).items():
            if isinstance(book, Exception):   # keep the old copy; it stops being used after book_stale
                log.warning("book %s failed: %s", eid, book)
            else:
                self.ex[eid].book = strip_own(book, mine_real.get(eid, []))
                self.ex[eid].book_time = self.ex[eid].verified = util.time.monotonic()
                self.pending_dirty.discard(eid)
                self.note_book(eid, self.ex[eid].book)

    def note_book(self, eid, book):
        """Recorder: queue a row of other traders' top levels if they changed since the last row for eid."""
        if not self.db or not self.cfg.record_books:
            return
        n = self.cfg.record_book_levels
        top = tuple(tuple((rnd(l["price"]), round(float(l["quantity"]), 2)) for l in (book.get(k) or [])[:n])
                    for k in ("bids", "asks"))
        if self.book_tops.get(eid) == top:
            return
        self.book_tops[eid] = top
        self.book_rows.append((round(util.time.time(), 3), eid, json.dumps(top[0]), json.dumps(top[1])))

    # ------------------------------------------------------------------------------ decide
    def decide(self, ex, fv, inv, eff, global_reduce, party_delta, now_m, ref=None, book_fv=None, ref_liquid=False):
        """What should be resting on this exchange right now? NO_QUOTE = nothing.
        fv is what we quote around; book_fv is the tournament book's own price (for the Polymarket guard);
        ref_liquid says whether the Polymarket price is reliable enough to size positions with Kelly."""
        cfg = self.burst_cfg if self.burst else self.cfg
        ex.fl_tag, ex.turnover_dead = "", False
        ex.ro_clip = ""
        ex.inv, ex.eff, ex.ref = inv.get(ex.eid, 0.0), eff.get(ex.eid, 0.0), ref
        hrs = self.hours_to_close(ex)
        if hrs * 60 <= cfg.stop_minutes_before_close:
            return NO_QUOTE                                   # too close to settlement
        b = ex.book or {}
        best_bid = b["bids"][0]["price"] if b.get("bids") else None
        best_ask = b["asks"][0]["price"] if b.get("asks") else None
        if ex.eid in self.ref_only and ex.eid in self.ref_tops:   # R5 from bulk tops: no current book
            best_bid, best_ask = self.ref_tops[ex.eid]           # (other traders' best prices, see r5_top)

        # Election night, final hours: only get flat, even by trading against other orders (exit_quote).
        # This comes before every other guard on purpose: getting out must never be blocked. If the book
        # has gone too thin for a fair value (likely on election night), exit around our last known fair
        # value, or failing that Polymarket's price, instead of holding the position into settlement.
        # Package 10 A1: the value-mode p (liquid, race-scaled Polymarket) and no pre-close windows (close_window)
        vp = (self.value_p(ex, ref, ref_liquid)
              if getattr(cfg, "value_mode", False) or getattr(cfg, "value_quote_hurdle", 0.0) > 0 else None)
        if hrs <= self.close_window("exit_hours_before_close", cfg):
            anchor = next((x for x in (fv, ex.last_fv, ref) if x is not None), None)
            if fv is not None:
                ex.last_fv = fv
            planned = self.size_plan.get(ex.eid) if cfg.size_by_activity else None   # big positions leave in big pieces
            return (exit_quote(anchor, ex.inv, best_bid, best_ask, cfg, self.bankroll(), max_size=planned,
                               value_floor=vp if getattr(cfg, "value_mode", False) else None)
                    if anchor is not None else NO_QUOTE)
        if fv is None:
            return NO_QUOTE                                   # no trustworthy price

        # Jump guard: a big move in one cycle usually means news; informed traders will pick us off.
        if ex.last_fv is not None and abs(fv - ex.last_fv) >= cfg.jump_threshold:
            log.warning("JUMP %s %.3f -> %.3f - pausing %.0fs", ex.label, ex.last_fv, fv, cfg.jump_cooldown_seconds)
            ex.cooldown_until = now_m + cfg.jump_cooldown_seconds
        ex.last_fv = fv
        if now_m < ex.cooldown_until:
            return NO_QUOTE

        no_bid, no_ask = self.party_blocks(ex, party_delta)

        # Reference-price guard: if Polymarket says this contract is worth clearly MORE than the
        # tournament book does, don't sell it to anyone here (they probably know); clearly LESS -> don't
        # buy. Compared with the book's own price, not the leaned fair value (see cycle step 3).
        if ref is not None:
            base = book_fv if book_fv is not None else fv
            g_ask, g_bid = ref - base > cfg.ref_guard_gap, base - ref > cfg.ref_guard_gap
            no_ask = no_ask or g_ask
            no_bid = no_bid or g_bid

        # Tail guard: near 0 or 1, one side risks ~1 a share to earn ~1c, and a single upset wipes out
        # many fills. Don't take that side, except to shrink a position we already hold.
        bid_cap = ask_cap = None
        if fv < cfg.tail_low:
            ask_cap = max(0, int(ex.inv))         # selling YES near 0 = buying NO near 1
        if fv > cfg.tail_high:
            bid_cap = max(0, int(-ex.inv))        # buying YES near 1
        # P12 ops mm_risk_reserve_*: value adds paused -> in the tails only what shrinks this exchange's position
        ex.mmr_tail = (self.mm_tail_adds_off(ex, fv, ref, ref_liquid) if getattr(self, "mmr_paused", False)
                       else False)
        if ex.mmr_tail:
            red_bid, red_ask = max(0, int(-ex.inv)), max(0, int(ex.inv))
            held = (bid_cap is None or bid_cap > red_bid) + (ask_cap is None or ask_cap > red_ask)
            self.mmr_tail_now = getattr(self, "mmr_tail_now", 0) + held
            bid_cap = red_bid if bid_cap is None else min(bid_cap, red_bid)
            ask_cap = red_ask if ask_cap is None else min(ask_cap, red_ask)
        # P15: the harvest ladder's side quotes only what reduces this position (the ladder is that side), less what
        # our harvest levels there already offer (P13 RT13-2)
        hv_s = (getattr(self, "hv_sides", None) or {}).get(ex.eid)
        if hv_s == "bid":
            red = max(0, int(-ex.inv - self.hv_side_qty(ex.eid, True)))
            bid_cap = red if bid_cap is None else min(bid_cap, red)
        if hv_s == "ask":
            red = max(0, int(ex.inv - self.hv_side_qty(ex.eid, False)))
            ask_cap = red if ask_cap is None else min(ask_cap, red)
        # P15 state_max_usd: in the tails the side ADDING to this position stops at the state's collateral cap
        ex.st_caps = self.st_quote_caps(ex, fv, ref, ref_liquid) if self.st_on() else None
        if ex.st_caps is not None:
            sb, sa = ex.st_caps
            if sb is not None and (bid_cap is None or bid_cap > sb):
                self.st_count(ex, "quote")
                bid_cap = sb
            if sa is not None and (ask_cap is None or ask_cap > sa):
                self.st_count(ex, "quote")
                ask_cap = sa

        reduce_only = global_reduce or hrs <= self.close_window("flatten_hours_before_close", cfg)
        # From flatten_per_market_hours: flatten each market on its own, i.e. judge (and skew) by this
        # market's own position rather than the race-netted one.
        inv_for_quote = ex.inv if hrs <= self.close_window("flatten_per_market_hours", cfg) else ex.eff
        # Kelly position limits, only with a liquid Polymarket price and a known account value.
        kelly_p = ref if (ref is not None and ref_liquid) else None
        # Size: this market's share of the capital plan; the party-control markets get a flat position limit.
        planned = self.size_plan.get(ex.eid, cfg.size_min_frac * self.bankroll()) if cfg.size_by_activity else None
        headline_limit = (cfg.headline_position_frac * self.bankroll()
                          if cfg.size_by_activity and ex.group in cfg.headline_races else None)
        edge = reduce_size = None
        if ex.eid in self.ref_only:               # R5: priced from Polymarket alone -> wider and small
            edge = max(cfg.min_edge, cfg.ref_only_min_edge)
            full = planned if planned is not None else cfg.order_size_frac * self.bankroll()
            planned = min(full, max(1.0, cfg.ref_only_size_frac * self.bankroll()))
            if cfg.ref_only_reduce_full:          # ...but a held position leaves at the market's normal size
                reduce_size = full
        if cfg.market_edge_enabled and ex.eid in self.market_edge:   # rival-floor map: this market's own floor
            own = max(cfg.min_edge, min(self.market_edge[ex.eid], cfg.market_edge_max))
            edge = own if edge is None else max(edge, own)
        ex.age = self.age_hours(ex)
        adding = cfg.capital_ceiling_adding_size_factor if self.capital_over else 1.0
        if self.adding_resume:                    # Package 8 adding_factor_capital_on: the resume factor
            adding = self.ceiling_adding_factor(cfg)
        adding_limit = 1.0
        ex.turnover_dead = self.turnover_dead(ex, planned if planned is not None
                                              else cfg.order_size_frac * self.bankroll(), cfg)
        side, bias_edge, bias_size = fl_side(fv, ex.fl_side, cfg)
        ex.fl_side, tag = side, side
        side = "bid" if side == "mid" else side       # mid band: an optional extra edge on bids, full size
        ex.fl_tag = (f" fl:{tag}+{100 * bias_edge:g}c" if side and (ex.inv > -1 if side == "bid" else ex.inv < 1)
                     else "")                         # (shown only while it changes the quote: not when unloading)
        why = {}
        skew_inv, age_off = None, False
        # Package 12 M2: skew from the target holding - never in reduce-only (global_reduce: the risk cap is over, or
        # the flatten window): there the skew is from flat as before (red team RT12-4)
        if getattr(cfg, "skew_target_inventory", False) and not reduce_only:
            skew_inv, age_off = self.skew_target_inputs(ex, inv, inv_for_quote,
                                                        hrs <= self.close_window("flatten_per_market_hours", cfg),
                                                        cfg, now_m)
        q = quoting.compute_quote(fv, ex.inv, inv_for_quote, best_bid, best_ask, cfg, reduce_only, no_bid, no_ask,
                             bid_cap, ask_cap, kelly_p=kelly_p, bankroll=self.bankroll(),
                             shift=self.party_shift(ex, party_delta), order_size=planned, position_limit=headline_limit,
                             min_edge=edge, reduce_size=reduce_size, net_inv=ex.eff, age_hours=ex.age,
                             adding_factor=adding, bias_side=side, bias_edge=bias_edge, bias_size=bias_size,
                             adding_limit_factor=adding_limit,
                             why=why,
                             adding_per_market=bool(getattr(cfg, "adding_factor_per_market", False)),
                             value_p=vp, skew_inv=skew_inv, age_off=age_off,
                             skew_add_flat=skew_inv is not None and not (cfg.alloc_enabled and ex.eid in
                                                                         (getattr(self, "alloc_targets", None) or {})))
        ex.ro_clip = why.get("ro_clip", "")
        if getattr(cfg, "mm_recycle_enabled", False):   # P14 1: stale MM inventory out through the reducing side
            q = self.mm_recycle_quote(ex, q, fv, best_bid, best_ask, vp, cfg, now_m)
        if self.p141("alloc_cancel_mm_first"):     # P14.1 1: a "refill pending" hold - an allocator sale here
            q = self.mm_hold_quote(ex, q, now_m)   #  cancelled our quotes; the reducing side waits for the read
        if getattr(cfg, "value_mode", False) and vp is not None:   # Package 10 A1: the final quote, value floor
            q = value_floor_quote(q, vp, ex.inv, cfg)
        return q

    def update_size_plan(self, now_m, fvs):
        """Every size_plan_seconds: work out how many shares to quote in each market (see plan_sizes).
        Only markets that have a fair value (i.e. will be quoted) get capital, each at what its quotes
        really lock (quote_lock). Activity = each market's share of Polymarket's traded volume, blended
        with its share of the tournament trades the realtime feed has reported. The tournament's own
        trades count more as they accumulate: fully (live_activity_max_weight) after live_activity_trades.
        A market that gets a fair value between plans quotes the minimum until the next plan."""
        cfg = self.cfg
        if (self.size_plan and now_m - self.size_plan_time < cfg.size_plan_seconds
                and self.size_plan_bank == self.bankroll()):
            return                                         # (a 5% account step re-plans at once)
        quoted = [e for e in self.ex if fvs.get(e) is not None]
        vols = self.refs.volumes() if self.refs and hasattr(self.refs, "volumes") else {}
        poly = {e: float(vols.get(f"{self.ex[e].group}|{self.ex[e].party}") or 0.0) for e in quoted}
        live = dict(getattr(self.feed, "trade_counts", None) or {}) if self.feed else {}
        live = {e: live.get(e, 0) for e in quoted}
        tot_p, tot_l = sum(poly.values()), sum(live.values())
        w_live = min(cfg.live_activity_max_weight, tot_l / cfg.live_activity_trades) if tot_l else 0.0
        activity = {e: (1 - w_live) * (poly[e] / tot_p if tot_p else 0.0) + w_live * (live[e] / tot_l if tot_l else 0.0)
                    for e in quoted}
        headline = {e for e in quoted if self.ex[e].group in cfg.headline_races}
        lock = {e: quote_lock(fvs[e], cfg) for e in quoted}
        new = plan_sizes(activity, headline, self.bankroll(), cfg, prev=self.size_plan, lock=lock,
                         prev_bankroll=self.size_plan_bank)
        changed = sum(1 for e, s in new.items() if self.size_plan.get(e) != s)
        self.size_plan, self.size_plan_time, self.size_plan_bank = new, now_m, self.bankroll()
        self.plan_capital = sum(s * lock[e] for e, s in new.items())
        top = sorted(new.items(), key=lambda kv: -kv[1])[:6]
        log.info("size plan: %d markets quoted, locking up to %s (cap %.0f), %d changed, tournament trades weigh %.0f%% | "
                 "biggest: %s", len(new), f"{sum(s * lock[e] for e, s in new.items()):,.0f}",
                 cfg.quote_capital_frac * self.bankroll(), changed, 100 * w_live,
                 ", ".join(f"{self.ex[e].label} {s:,}" for e, s in top))

    def party_shift(self, ex, party_delta):
        """National-swing shading: how much to lower this market's reservation price. Net long Republican
        (party_delta > 0) -> Republican quotes shade down (we sell Rep more readily, buy it less) and
        Democratic ones up, so fills bring the net exposure back. Grows linearly to party_skew_at_cap at
        the hard cap (where party_blocks takes over). Zero for markets that aren't Rep/Dem."""
        sign = PARTY_SIGN.get(ex.party, 0)
        party_delta, cap = self.party_measure(party_delta)
        if not sign or cap <= 0:
            return 0.0
        return self.cfg.party_skew_at_cap * sign * max(-1.0, min(1.0, party_delta / cap))

    def party_measure(self, party_delta):
        """(net exposure, cap) for the national-swing cap: the share-count party_delta and max_party_delta_frac x
        account; Package 10 A2 bloc_delta_enabled: the bloc delta ($ per sd, + = Republican) and max_bloc_delta_frac
        x account instead."""
        if getattr(self.cfg, "bloc_delta_enabled", False):
            return self.bloc_delta, self.cfg.max_bloc_delta_frac * self.bankroll()
        return party_delta, self.cfg.max_party_delta_frac * self.bankroll()

    def bloc_refresh(self, inv):
        """Package 10 A2 (cycle step 6, flag on): this cycle's per-share sensitivities (liquid race-scaled
        Polymarket) and the bloc delta of inv. Returns the bloc delta. A contract with no liquid price this cycle
        keeps its last sensitivity (a Polymarket outage must not zero the bloc delta and open the party cap, which
        the share count it replaces never needed Polymarket for; P10 red team RT-4)."""
        races = defaultdict(list)
        for eid, ex in self.ex.items():
            races[ex.group].append((eid, ex.label, self.scaled_ref(ex), eid in (self.cur_liquid or ())))
        sens = bloc_sensitivities(races, self.cfg)
        for e, v in (self.bloc_sens or {}).items():
            if e in self.ex:
                sens.setdefault(e, v)
        self.bloc_sens, self.bloc_inv = sens, dict(inv)
        self.bloc_delta = self.bloc_delta_now(inv)
        return self.bloc_delta

    def bloc_delta_now(self, inv=None):
        """Package 10 A2: sum position x sensitivity ($ per sd of the national factor; + = Republican-leaning) for a
        position map {eid: YES shares} (None = the last positions read), with this cycle's sensitivities. Part B's
        allocator calls it on a hypothetical book to check a pair against the cap (bloc_cap)."""
        inv = self.bloc_inv if inv is None else inv
        return sum(q * self.bloc_sens.get(e, 0.0) for e, q in (inv or {}).items())

    def bloc_cap(self):
        """Package 10 A2: the |bloc_delta| cap in $ per sd (max_bloc_delta_frac x account)."""
        return self.cfg.max_bloc_delta_frac * self.bankroll()

    def scaled_ref(self, ex):
        """Package 10: this cycle's raw Polymarket price for ex scaled to sum to 1 over its race when every leg has
        one (else the raw price; None without one). Liquidity is the caller's check."""
        refs = self.cur_refs or {}
        r = refs.get(ex.eid)
        if r is None:
            return None
        members = self.groups.get(ex.group) or [ex.eid]
        if len(members) > 1 and all(refs.get(e) is not None for e in members):
            tot = sum(refs[e] for e in members)
            if tot > 0:
                return r / tot
        return r

    def value_p(self, ex, ref, ref_liquid):
        """Package 10 A1 / A4: the outcome value of one YES share here = the race-scaled Polymarket price, only when
        liquid (ref_liquid); None otherwise (the value rules then leave this market alone)."""
        if ref is None or not ref_liquid:
            return None
        if (self.cur_refs or {}).get(ex.eid) is None:     # (decide called outside a cycle: the raw price given)
            return ref
        return self.scaled_ref(ex)

    def close_window(self, name, cfg=None):
        """Package 10 A1 (ii): the pre-close window setting `name` (exit_hours_before_close, flatten_hours_before_close,
        flatten_per_market_hours) as the code should apply it: the setting, or no window in value_mode - but never
        less than the stop window (stop_minutes_before_close): there "nothing at all" holds for every check keyed on
        a pre-close window too (stale-quote / hold takes, arbitrage's "closing", the allocator), in value_mode or
        with the windows set to 0 (P10 red team RT-3; the defaults 2 / 12 / 6 h are above it: unchanged)."""
        cfg = cfg or self.cfg
        w = float("-inf") if getattr(cfg, "value_mode", False) else getattr(cfg, name)
        return max(w, cfg.stop_minutes_before_close / 60.0)

    def party_blocks(self, ex, party_delta):
        """National-swing cap -> (no_bid, no_ask). Buying YES on a Republican market pushes the net
        Republican-minus-Democrat delta up, on a Democratic market down; selling does the opposite.
        Beyond the cap, block whichever side would make it worse."""
        sign = PARTY_SIGN.get(ex.party, 0)
        party_delta, party_cap = self.party_measure(party_delta)   # (Package 10 A2: the bloc delta with the flag)
        too_red, too_blue = party_delta > party_cap, party_delta < -party_cap
        return (sign > 0 and too_red) or (sign < 0 and too_blue), (sign > 0 and too_blue) or (sign < 0 and too_red)


    def bankroll(self):
        """Account value that every size is a fraction of. It follows the real value in steps: only once
        that has moved sizing_step_frac away. Sizes are whole shares, so following every wobble would
        turn 100-share orders into 99-share ones on a 10-SUSQie dip and replace every order at once.
        While "auto" hasn't yet worked out whether the API's value includes cash locked in our orders
        (see checked_account_value), the value may be off by that cash, so it isn't followed at all."""
        eq = self.last_equity
        if eq and self.reserved_mode is not None and abs(eq / self.size_bank - 1) >= self.cfg.sizing_step_frac:
            log.info("sizing: account value %.0f is %+.1f%% from %.0f - order sizes rescaled",
                     eq, 100 * (eq / self.size_bank - 1), self.size_bank)
            self.size_bank = eq
        return self.size_bank

    # ------------------------------------------------------------------------------ reconcile
    def plan_change(self, ex, q, resting, fv, now, now_m):
        """What has to change on this exchange so its resting orders match quote q: a Change, or None.
        Nothing new is ever planned while an earlier write there is unresolved (it could double up);
        pulling orders is always allowed, unless a cancel is already on its way."""
        if busy(ex, now_m):
            if q.bid is None and q.ask is None and resting and not ex.cancelling:
                return Change(ex, resting, True, [], self.change_key(ex, pull=True, reprice=True), count=False,
                              unsafe=True)
            return None

        # The normal sizes (no size factor: burst, capital ceiling, favourite-longshot), within every position /
        # cash limit: the most a resting order may hold and still stay. A full-size order placed before a factor
        # shrank the wanted size is kept, not pulled as "unsafe" (2 Oct 11:31: the capital ceiling's x0.25 made
        # ~200 resting orders urgent reprices at once, which bypassed the budget and drew the 429s).
        full_bid = max(q.bid_size, q.bid_max or 0) if q.bid is not None else q.bid_size
        full_ask = max(q.ask_size, q.ask_max or 0) if q.ask is not None else q.ask_size
        if self.burst and self.cfg.burst_size_factor != 1.0:
            # Burst mode places burst_size_factor of each size: compare what rests with THAT size, or a half-size
            # order fails keep_fraction (187 of 375 < 50%) and is cancelled and replaced every cycle. A full-size
            # order placed before the burst is NOT oversized though (ceiling = the normal size), or entering burst
            # mode would pull and re-place every resting quote while the exchange is slow.
            k = self.cfg.burst_size_factor
            q = replace(q, bid_size=max(1, int(q.bid_size * k)) if q.bid_size else 0,
                        ask_size=max(1, int(q.ask_size * k)) if q.ask_size else 0)
        if q.bid is not None and q.bid_size > 0 and ex.inv <= -1:
            # Package 6 reduce_no_as_sell: a bid buying back NO we hold goes out as a covered "sell NO", capped at
            # the NO free to sell; the part beyond it (going long) waits until the short is gone. Capping the WANTED
            # size (not only the order) keeps a resting capped order from looking too small and churning.
            cover = self.cover_no_qty(ex.eid, ex.inv, 0)
            if 1 <= cover < q.bid_size:
                q = replace(q, bid_size=cover)
        bids = [o for o in resting if o.is_bid]
        asks = [o for o in resting if not o.is_bid]
        fix_bid = self.side_fix(ex, bids, q.bid, q.bid_size, q.bid_limit, True, full_bid, fv, now, now_m)
        fix_ask = self.side_fix(ex, asks, q.ask, q.ask_size, q.ask_limit, False, full_ask, fv, now, now_m)
        # Reduce-only, flatten and exit windows: always do exactly what the risk logic asks (no holding, no
        # burst-mode skipping), or a position could be left to grow or never be exited.
        critical = self.global_reduce or self.hours_to_close(ex) <= self.close_window("flatten_hours_before_close")
        if self.cfg.churn_control and not critical:
            if fix_bid and self.hold_side(ex, bids, q.bid, q.bid_limit, full_bid, True, now, now_m):
                fix_bid = False                   # (full size: a full-size order is still safe in a burst)
            if fix_ask and self.hold_side(ex, asks, q.ask, q.ask_limit, full_ask, False, now, now_m):
                fix_ask = False
        if not (fix_bid or fix_ask):
            return None                   # book already matches: keep our queue position
        if not self.cfg.churn_count_sent:            # old behaviour: counted when planned (see count_reprices)
            for side, fix, rest in (("bid", fix_bid, bids), ("ask", fix_ask, asks)):
                if fix and rest:
                    ex.reprices.setdefault(side, deque()).append(now_m)

        if self.burst and ex.eid not in self.burst_set and not critical:
            # Burst mode, not a top market: no reprices. Keep what rests while it's still safe (price inside the
            # limit, size not above what's wanted) and pull what isn't; an EMPTY side still gets a (smaller) quote,
            # which costs only a share of one batch.
            fix_bid = fix_bid and (q.bid is None or not bids or any(
                o.price > (q.bid_limit if q.bid_limit is not None else q.bid) + 1e-9 or o.qty > full_bid + 1e-9 for o in bids))
            fix_ask = fix_ask and (q.ask is None or not asks or any(
                o.price < (q.ask_limit if q.ask_limit is not None else q.ask) - 1e-9 or o.qty > full_ask + 1e-9 for o in asks))
            doomed = (bids if fix_bid else []) + (asks if fix_ask else [])
            new = [] if now_m < ex.pause_until else (
                ([self.new_order(ex, True, q.bid, q.bid_size, fv, now)]
                 if fix_bid and q.bid is not None and not bids else [])
                + ([self.new_order(ex, False, q.ask, q.ask_size, fv, now)]
                   if fix_ask and q.ask is not None and not asks else []))
            if not doomed and not new:
                return None
            return Change(ex, doomed, False, new,
                          self.change_key(ex, pull=bool(doomed) or not new, reprice=bool(doomed)),
                          unsafe=bool(doomed))
        # Cancel the wrong side(s). Both wrong -> one cancel-all for the exchange; else per order.
        doomed = (bids if fix_bid else []) + (asks if fix_ask else [])
        new = []
        if now_m >= ex.pause_until:       # (sizes already scaled for burst mode above)
            if fix_bid and q.bid is not None:
                new.append(self.new_order(ex, True, q.bid, q.bid_size, fv, now))
            if fix_ask and q.ask is not None:
                new.append(self.new_order(ex, False, q.ask, q.ask_size, fv, now))
        log.info("%s%-26.26s fv %s%s inv %+5.0f race %+5.0f%s | bid %s ask %s", "" if self.api.live else "[dry] ",
                 ex.label, f"{fv:.3f}" if fv is not None else "  -  ",
                 f" (ref {ex.ref:.3f})" if ex.ref is not None else "", ex.inv, ex.eff,
                 f" age {ex.age:.0f}h" if ex.inv and ex.age > self.cfg.skew_age_after_hours else "",
                 fmt(q.bid, q.bid_size), fmt(q.ask, q.ask_size) + ex.fl_tag)
        if not doomed and not new:
            return None
        # An order that must not stay (unsafe: beyond its limit price, a side we no longer want, or above the
        # position / cash limits - NOT merely above a size factor, see full_bid) makes this change as urgent as a
        # pull: never deferred by the budget (but capped by urgent_writes_per_cycle).
        unsafe = ((fix_bid and any(unsafe_order(o, q.bid, full_bid, q.bid_limit, True) for o in bids))
                  or (fix_ask and any(unsafe_order(o, q.ask, full_ask, q.ask_limit, False) for o in asks)))
        urgent = self.cfg.never_defer_unsafe and unsafe
        return Change(ex, doomed, fix_bid and fix_ask, new,
                      self.change_key(ex, pull=not new or urgent, reprice=bool(doomed)),
                      unsafe=unsafe or (not new and bool(doomed)))

    def hold_side(self, ex, resting, price, limit, size, is_bid, now, now_m):
        """Churn control: keep this side's single resting order although it's off target, because it's still safe
        (inside the limit price) and either young (min_quote_life_seconds) or this side keeps getting re-quoted
        (churn_max_reprices in churn_window_seconds: another bot is stepping in front of us each time)."""
        cfg = self.cfg
        if price is None or limit is None or len(resting) != 1:
            return False
        o = resting[0]
        if (o.price > limit + 1e-9) if is_bid else (o.price < limit - 1e-9):
            return False                                  # no longer safe: must move
        if o.qty > size + 1e-9:
            return False                                  # bigger than now allowed (limits tightened): must shrink
        if o.expires and (o.expires - now).total_seconds() < cfg.refresh_before_expiry:
            return False
        if ex.eid in self.ref_moved or now_m - ex.ref_moved_at < 15:
            return False                                  # Polymarket moved here lately: follow it now
        young = o.order_id in self.recent_orders and now_m - self.recent_orders[o.order_id][1] < cfg.min_quote_life_seconds
        hist = ex.reprices.get("bid" if is_bid else "ask") or deque()
        while hist and now_m - hist[0] > cfg.churn_window_seconds:
            hist.popleft()
        return young or len(hist) >= cfg.churn_max_reprices

    def count_reprices(self, ch, now_m):
        """Churn control: this change's cancels are being sent now - count them as reprices of their sides."""
        if self.cfg.churn_count_sent and ch is not None:
            for side in ch.reprice_sides():
                ch.ex.reprices.setdefault(side, deque()).append(now_m)

    def change_key(self, ex, pull, reprice=False):
        """Sending order: pulls first, then the party-control markets, then quotes for empty sides (cheap: a
        share of one batch), then reprices (a cancel each), biggest quotes first within each."""
        urgent = ex.eid in self.ref_moved
        head = 0 if ex.group in self.cfg.headline_races else 1
        rec = ()
        if getattr(self.cfg, "mm_recycle_sell_first", False):   # P14.1 3: recycled SALES (the ask on a long: they
            r = (self.mmf_recycling or {}).get(ex.eid)          #  free cash) before the rest, BUY-BACKS after them
            if r is None or r.get("blocked"):                   #  (one extra slot, absent while the flag is off: the
                rec = (0,)                                      #  key's shape is then exactly 4ff7d91's)
            else:
                rec = (-1,) if r.get("side") == "ask" else (1,)
        return (0 if pull else 0.5 if urgent else 1, head) + rec + (
                1 if reprice else 0,
                -self.size_plan.get(ex.eid, 0))

    def reconcile(self, ex, q, resting, fv, now, now_m):
        """One write at a time (parallel_writes = 1): make the orders resting on this exchange match quote q.
        Cancels happen right away. New orders are returned so they can be sent in batches."""
        ch = self.plan_exchange(ex, q, resting, fv, now, now_m)
        if ch is None:
            return []
        self.count_reprices(ch, now_m)
        if ch.doomed and not self.cancel(ex.eid, ch.doomed, whole_exchange=ch.whole):
            log.warning("%s: could not confirm cancels - retrying next cycle", ex.label)
            return []                     # never stack new quotes on top of old ones
        return ch.new

    def plan_exchange(self, ex, q, resting, fv, now, now_m):
        """plan_exchange_core, except (Package 12 L1) that our rich-leg set ladder's orders resting here are left
        alone: not seen by the quote's plan, never in a cancel-all, and the quote's ask kept above them. P15: the
        harvest ladder's orders too (its bids as the set ladder's; the quote's bid kept below its asks)."""
        meta = self.order_meta
        lad = [o for o in resting if (meta.get(o.order_id) or {}).get("set_ladder")
               or (meta.get(o.order_id) or {}).get("harvest")] if resting else []
        if not lad:
            return self.plan_exchange_core(ex, q, resting, fv, now, now_m)
        bids, asks = [o for o in lad if o.is_bid], [o for o in lad if not o.is_bid]
        q = self.sl_guard_quote(ex, q, bids) if bids else q
        q = self.hv_guard_quote(ex, q, asks) if asks else q
        ch = self.plan_exchange_core(ex, q, [o for o in resting if o not in lad], fv, now, now_m)
        if ch is not None:
            ch.whole = False                      # (a cancel-all would take the ladder too)
        return ch

    def plan_exchange_core(self, ex, q, resting, fv, now, now_m):
        """The level-0 quote plan for this exchange (plan_change after the cash gate): a Change, or None."""
        cfg = self.cfg
        self.__dict__.setdefault("cover_planned", {}).pop(ex.eid, None)   # Package 6: this plan's covered NO afresh
        if q.bid is not None and q.bid_size > 0 and self.set_blocked(ex.eid, ex.inv, 0):
            # Package 7 no_set_aware_bids: every NO share here is in a NO+NO set - a bid would be a covered sale that
            # breaks the set (or a cash purchase): refused at 0 cash.
            q = replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None)
        if self.cash_gate_on():                   # Package 8: each side at most what the cash allows (planned so)
            q = self.cash_gate_quote(ex, q, resting)
        return self.plan_change(ex, q, resting, fv, now, now_m)

    # ------------------------------------------------------------------------------ parallel order writes
    BURST_LOADING_MAX_SECONDS = 600.0     # the "first book download still running" grace never lasts longer

    def starting_up(self, now_m):
        """Just (re)started: cycles are long because every book is being downloaded for the first time, under our
        own request budget - not because the exchange is slow. See burst_startup_grace_seconds."""
        grace = self.cfg.burst_startup_grace_seconds
        if self.trading_since is None or grace <= 0:
            return False
        return now_m - self.trading_since < grace or self.first_books_loading(now_m)

    def first_books_loading(self, now_m):
        """Since the trading loop started, some book has never been downloaded (at most BURST_LOADING_MAX_SECONDS)."""
        return (self.trading_since is not None and now_m - self.trading_since < self.BURST_LOADING_MAX_SECONDS
                and any(ex.book is None for ex in self.ex.values()))

    def update_burst(self, now_m):
        """Burst mode on/off (see BURST PROTECTION). Its effects are in decide() and plan_change()."""
        cfg = self.cfg
        times = getattr(self.api, "write_times", None)       # measured on the wire (no queueing / throttle wait)
        while times:
            self.write_log.append(times.popleft())
        while self.write_log and now_m - self.write_log[0][0] > 60:
            self.write_log.popleft()
        if not cfg.burst_protection:
            self.burst = False
            return
        secs = sorted(d for _, d, _ in self.write_log)
        slow = (sum(t for _, _, t in self.write_log) >= cfg.burst_timeouts
                or (secs and secs[len(secs) // 2] >= cfg.burst_write_seconds)
                or (self.last_cycle_seconds >= cfg.burst_cycle_seconds and not self.starting_up(now_m)))
        if slow:
            self.burst_calm_since = now_m
            if not self.burst:
                self.burst = True
                log.warning("BURST MODE: the exchange is slow (median write %.1f s, %d timeouts, last cycle %.0f s) - "
                            "quoting only the %d biggest markets, %.0f%% size, %.1fc wider",
                            secs[len(secs) // 2] if secs else 0, sum(t for _, _, t in self.write_log),
                            self.last_cycle_seconds, cfg.burst_markets, 100 * cfg.burst_size_factor,
                            100 * cfg.burst_extra_edge)
        elif self.burst and now_m - self.burst_calm_since >= cfg.burst_calm_seconds:
            self.burst = False
            log.warning("burst mode over: exchange back to normal speed")
        if self.burst:
            ranked = sorted(self.ex, key=lambda e: (self.ex[e].group not in cfg.headline_races,
                                                    -self.size_plan.get(e, 0), e))
            self.burst_set = set(ranked[:cfg.burst_markets])
            self.burst_cfg = replace(cfg, min_edge=cfg.min_edge + cfg.burst_extra_edge)

    def send_changes(self, changes):
        """Send this cycle's order changes with up to parallel_writes requests in flight, most urgent first:
          1. every cancel at once (pulls first), plus the new orders on exchanges that need no cancel;
          2. as each cancel is confirmed, the new orders that were waiting for it.
        New orders on an exchange are only ever sent after its cancel is CONFIRMED (never two quotes on one
        side). The cycle waits at most write_wait_seconds; writes still running then carry on in the
        background, their exchanges are left alone (Ex.writes) and the results are applied next cycle."""
        cfg = self.cfg
        if self.pull_storm(changes):
            return
        deadline = util.time.monotonic() + cfg.write_wait_seconds
        # Urgent changes (key[0] == 0) that remove an unsafe order go first among them (urgent_writes_per_cycle).
        changes = sorted(changes, key=lambda c: (c.key[0], 0 if c.key[0] == 0 and c.unsafe else 1) + tuple(c.key[1:]))
        # Within the request budget, keeping write_read_reserve back so reads (positions, orders, books) never
        # starve; the least urgent changes wait for the next cycle.
        writes_left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        if cfg.startup_writes_per_minute and self.first_books_loading(util.time.monotonic()):
            # Still downloading the first books after a (re)start: book downloads and writes share the request
            # budget, so writes stay at the old 30/min until every book is in (pulls and unsafe orders still go).
            used = int(getattr(self.api, "wbudget", 0)) - writes_left
            writes_left = min(writes_left, cfg.startup_writes_per_minute - used)
        spare = min(getattr(self.api, "budget_left", lambda: 10 ** 6)() - self.write_reserve(), writes_left)
        kept, cost, orders, urgent_cost, capped = [], 0.0, 0, 0, False
        cap = cfg.urgent_writes_per_cycle
        for ch in changes:
            n = orders + len(ch.new)
            c = (0 if not ch.doomed else 1 if ch.whole else len(ch.doomed)) + (
                math.ceil(n / cfg.batch_size) - math.ceil(orders / cfg.batch_size))
            if ch.key[0] == 0 and cap > 0 and urgent_cost + c > cap and urgent_cost > 0:
                capped = True                 # urgent writes capped: further URGENT changes wait for the next cycle,
                continue                      #   the ordinary reprices and new quotes behind them still go
            if cost + c > spare and ch.key[0] != 0:
                break                         # pulls always go; everything after the first misfit waits
            kept.append(ch)
            cost, orders = cost + c, n
            if ch.key[0] == 0:
                urgent_cost += c
        if len(kept) < len(changes):
            log.info("request budget: %d of %d order changes deferred to the next cycle%s",
                     len(changes) - len(kept), len(changes),
                     f" (urgent writes capped at {cap}/cycle)" if capped else "")
        changes = kept
        now_m = util.time.monotonic()
        for ch in changes:
            self.count_reprices(ch, now_m)        # only what is sent counts (not what the budget deferred)
        waiting = set()
        for ch in changes:
            if ch.doomed:
                waiting.add(self.submit_write("cancel", [ch.ex.eid], (ch.doomed, ch.whole), change=ch))
        self.send_orders([c for c in changes if not c.doomed and c.new])
        self.phase_mark("send")
        while self.running:
            # Only cancels sent by THIS call release their new orders: a late one from an earlier cycle carries
            # that cycle's prices (the next cycle re-plans that exchange instead).
            ready = [w.change for w in self.harvest_writes() if w in waiting and w.change.new and w.payload_ok]
            if ready:
                self.send_orders(sorted(ready, key=lambda c: c.key))
            left = deadline - util.time.monotonic()
            if left <= 0 or not self.writes:
                break
            wait([w.future for w in self.writes], timeout=left, return_when=FIRST_COMPLETED)
            self.progress("waiting for order writes")
        self.harvest_writes()
        self.phase_mark("wait")

    def pull_storm(self, changes):
        """Many pulls at once (reduce-only switching on pulls a side on every market held; pulls bypass the write
        budget, so ~70 DELETEs used to take ~2.4 min of a 30/min budget, every write behind them waiting): send
        ONE tournament-wide cancel-all instead and plan again next cycle, which re-places what should rest in
        batches. True = done that (send nothing else this cycle). Only when it's clearly cheaper: more pulls
        than pulls_cancel_all_over, or more than the writes left this minute AND than re-placing every quote
        would cost. Never while the self-test runs (its orders would vanish under it) or with ONLY_EXCHANGES.
        cancel_everything forgets our orders only once the cancel-all is confirmed (else orders_stale: re-read)."""
        cfg = self.cfg
        if not (self.api.live and cfg.pulls_cancel_all_over > 0) or cfg.only_exchanges or self.selftest_future:
            return False
        if self.burst:                            # burst mode re-quotes only the top markets: a cancel-all would
            return False                          # leave ~200 safe resting orders empty until the burst ends
        pulls = 0
        for c in changes:
            if c.key[0] == 0 and c.doomed and not c.new:
                n0 = len(c.doomed)
                pulls += 1 if c.whole and n0 else n0
        if not pulls:                             # (urgent reprices carry new orders: they are not pulls)
            return False
        left = getattr(self.api, "writes_left", lambda: 10 ** 6)()
        if not pulls > cfg.pulls_cancel_all_over:  # (only the count decides: a drained write budget would also
            return False                          # defer the re-placement, leaving the whole book empty)
        log.warning("%d pulls planned (%d writes left this minute) - one cancel-all instead; what should rest "
                    "is re-placed next cycle", pulls, left)
        try:
            if not self.cancel_everything():
                log.warning("cancel-all instead of pulls: not confirmed - re-reading our orders next cycle")
        except ApiError as e:                     # fall back to the pulls themselves: they reduce risk
            log.error("cancel-all instead of %d pulls failed (%s) - sending the pulls", pulls, e)
            return False
        return True

    def send_orders(self, changes):
        """New orders of these changes in batches of batch_size, in the order given; party-control markets get
        a batch of their own, so it's never queued behind (or slowed by) a big one."""
        orders = [(c.key, o) for c in changes for o in c.new if c.ex.writes == 0]
        head = [o for k, o in orders if k[1] == 0]
        rest = [o for k, o in orders if k[1] != 0]
        for group in (head, rest):
            for i in range(0, len(group), self.cfg.batch_size):
                chunk = group[i:i + self.cfg.batch_size]
                self.submit_write("batch", [o["exchangeId"] for o, _ in chunk], chunk)

    def submit_write(self, kind, eids, payload, change=None):
        w = Write(kind, eids, payload, util.time.monotonic(), change)
        for eid in eids:
            ex = self.ex.get(eid)
            if ex:
                ex.writes += 1
                ex.cancelling = ex.cancelling or kind == "cancel"
        if kind == "cancel":
            eid, (orders, whole) = eids[0], payload
            if whole and eid == self.selftest_eid:
                self.cancel_gen += 1      # also removes any self-test orders there (see selftest_finish)
            w.future = self.writer.submit(self.cancel_request, eid, orders, whole)
        elif self.cash_gate_on():             # Package 8: the gate runs here, on the main thread (its budget)
            orders = [o for o, _ in payload]
            gate, w.cash_need = self.cash_gate_orders(orders)
            w.future = self.writer.submit(self.place_orders, orders, gate=gate)
        else:
            w.future = self.writer.submit(self.place_orders, [o for o, _ in payload])
        def done(_f, w=w):
            w.done_at = util.time.monotonic()
            self.write_done.set()         # main loop: apply it soon
        w.future.add_done_callback(done)
        self.writes.append(w)
        return w

    def cancel_request(self, eid, orders, whole):
        """Writer thread: the cancel request(s) only. True = confirmed gone."""
        if not self.api.live:
            return True
        if whole:
            return self.api.cancel_all(self.tid, eid)
        return all([self.api.cancel_order(o.order_id) for o in orders])

    def harvest_writes(self):
        """Main thread: apply the results of writes that have finished; returns those Write jobs."""
        self.write_done.clear()
        done = [w for w in self.writes if w.future.done()]
        if not done:
            return []
        self.writes = [w for w in self.writes if w not in done]
        now_m = util.time.monotonic()
        for w in done:                    # counters first, so a failure below can't leave an exchange stuck
            for eid in w.eids:
                ex = self.ex.get(eid)
                if ex:
                    ex.writes = max(0, ex.writes - 1)
                    if w.kind == "cancel":
                        ex.cancelling = False
        for w in done:
            if w.future.cancelled():      # never sent (dropped from the queue by stop_queued_writes)
                if w.kind != "cancel":
                    self.cash_refund([o for o, _ in w.payload])   # Package 8: its cash need back to the gate
                continue
            try:
                self.apply_write(w, now_m)
            except Exception:
                log.exception("applying a write result failed")
                self.orders_stale = True
        return done

    def apply_write(self, w, now_m):
        """Main thread: apply one finished write's result."""
        try:
            result = w.future.result()
        except Exception as e:
            result = e
        if w.kind == "cancel":
            eid, (orders, whole) = w.eids[0], w.payload
            if result is True:
                w.payload_ok = True
                if not self.api.live:
                    for o in orders:
                        self.sim.pop(o.order_id, None)
                self.cash_credit([o for o in self.my_orders.values() if o.eid == eid] if whole else
                                 [o for o in orders if o.order_id in self.my_orders])   # Package 8 (gate on only)
                self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid] if whole
                                   else [o.order_id for o in orders])
            else:
                if isinstance(result, Exception):
                    log.error("cancel on %s failed: %s", eid, result)
                label = self.ex[eid].label if eid in self.ex else eid
                log.warning("%s: could not confirm cancels - retrying next cycle", label)
                self.orders_stale = True
        else:
            self.apply_batch(w.payload, result, now_m)

    def stop_queued_writes(self):
        """Drop writes still waiting for a writer thread (not yet sent): before cancelling everything, so
        nothing queued earlier is placed after the cancel."""
        for w in self.writes:
            w.future.cancel()             # only succeeds for ones not started

    def drain_writes(self, timeout):
        """Wait (up to timeout) for writes still in flight and apply them: before cancelling everything at
        shutdown, so an order that lands late isn't left resting."""
        if self.writes:
            wait([w.future for w in self.writes], timeout=timeout)
        self.harvest_writes()

    def new_order(self, ex, is_bid, price, size, fv, now, level=0):
        """One order for POST /orders/batch, plus notes about why we placed it (level 0 = the touch quote)."""
        cover = self.cover_no_qty(ex.eid, ex.inv, level) if is_bid else 0
        if cover >= 1:                    # Package 6: a covered NO sale, capped at the NO free to sell (the add waits)
            size = min(int(size), cover)
            self.__dict__.setdefault("cover_planned", {}).setdefault(ex.eid, {})[level] = size
        order = {"exchangeId": ex.eid, "side": "yes", "action": "buy" if is_bid else "sell",
                 "quantity": int(size), "price": round(price, 3), "tournamentId": self.tid,
                 # Dead-man's switch: the order dies on its own unless we keep refreshing it.
                 "expirationDate": iso(now + timedelta(seconds=self.order_ttl(ex, level)))}
        meta = {"our_side": "bid" if is_bid else "ask", "price": round(price, 3), "fv": fv, "t": util.time.time()}
        if cover >= 1:
            order["_no_sell"] = meta["no_sell"] = True   # sent as "sell NO @ 1-p" (wire_order)
        rec = self.mmf_recycling.get(ex.eid) if not level and getattr(self, "mmf_recycling", None) else None
        if rec is not None and rec.get("price") is not None and rec["side"] == ("bid" if is_bid else "ask"):
            meta["recycle"] = True                # P14 1: this side carries the recycler (fills class "recycle")
        return order, meta

    # --- Package 6: reduce NO holdings as covered NO sales (reduce_no_as_sell) ---
    def reduce_no_on(self):
        """reduce_no_as_sell in effect: the setting, and live with the self-test on, its own start-up leg passed
        (nosell_state "ok"; "off" = the exchange refused it: today's behaviour for the rest of this run)."""
        state = getattr(self, "nosell_state", None)
        if not self.cfg.reduce_no_as_sell or state == "off":
            return False
        if self.api.live and self.cfg.selftest_enabled:
            return state == "ok"
        return True

    def cover_no_qty(self, eid, inv, level=None, sets_ok=False):
        """NO shares a bid on eid may sell as a covered "sell NO" now: the NO held in that market (per market, -inv;
        never race-netted) less what our OTHER resting / just-planned bids there may already be selling (other ladder
        levels; the order a re-quote replaces is cancelled first, so the same level never counts). level None = a
        take, sent after every order of ours there was cancelled: all the NO held. 0 = not in effect / no NO held.
        Package 7 no_set_aware_bids: less the part locked in NO+NO sets (nono_set_part), unless sets_ok (a batch
        that sells NO on every leg of the race at once: arbitrage / short-set unwind legs)."""
        if not self.reduce_no_on() or inv is None or inv > -1:
            return 0
        held = -float(inv)
        if getattr(self.cfg, "no_set_aware_bids", False) and not sets_ok:
            held -= self.nono_set_part(eid, inv)
        if level is not None:
            if level == 0 and not (getattr(self.cfg, "no_set_aware_bids", False) and not sets_ok):
                held -= sum(o.qty for o in self.sl_orders(eid))   # Package 12 L1: what our set ladder sells there
            if level == 0 and getattr(self, "hv_sides", None):   # P15: what our harvest bids there buy back
                held -= sum(o.qty for o in self.hv_orders(eid) if o.is_bid)
            held -= sum(q for lv, q in (getattr(self, "cover_planned", {}).get(eid) or {}).items() if lv != level)
        return max(0, int(held + 1e-9))

    def no_sell_order(self, order, inv, whole=False):
        """A take / arbitrage order (YES terms) that buys back a short: marked to go out as a covered "sell NO",
        capped at the NO held (whole=True: only if ALL of it fits, else unchanged - arbitrage legs must stay equal).
        Returns the order (changed in place), or None (Package 7 no_set_aware_bids, not whole): all the NO held
        there is in a NO+NO set, so the buy-back can be neither a covered sale nor (at 0 cash) a purchase - not sent."""
        if order["action"] != "buy":
            return order
        if not whole and self.set_blocked(order["exchangeId"], inv):
            return None
        cover = self.cover_no_qty(order["exchangeId"], inv, sets_ok=whole)
        if cover < 1 or (whole and order["quantity"] > cover):
            return order
        order["quantity"] = min(int(order["quantity"]), cover)
        order["_no_sell"] = True
        return order

    # --- Package 7: NO+NO sets ---
    def nono_set_part(self, eid, inv=None):
        """NO shares of eid locked in NO+NO sets, worst-case collateral model (the exchange collateralises a race's NO
        at sum - max): eid's NO held n_i less its LONE part, lone_i = max(0, n_i - max over the race's OTHER legs of
        their NO held), a leg not holding NO counting 0 - i.e. min(n_i, max over the other legs). 0 if eid holds < 1
        NO or is not in a 2+-leg race. inv = this leg's position if given, else ex.inv. For a 2-leg race this is the
        smaller leg's NO when both hold NO, as before; with 3+ legs NO on only some legs is capped too."""
        ex = self.ex.get(eid)
        members = self.groups.get(ex.group) if ex is not None else None
        if not members or len(members) < 2 or any(m not in self.ex for m in members):
            return 0.0
        n = -float(inv if inv is not None else ex.inv)
        if n < 1:
            return 0.0
        others = max(max(0.0, -float(self.ex[m].inv)) for m in members if m != eid)
        return min(n, others) if others >= 1 else 0.0

    def set_blocked(self, eid, inv, level=None):
        """no_set_aware_bids in effect and a bid / take buying back NO on eid has no lone part left to sell as a
        covered sale (all of its NO is in a NO+NO set): any order there would need cash, so none is sent."""
        return (getattr(self.cfg, "no_set_aware_bids", False) and self.reduce_no_on() and inv is not None
                and inv <= -1 and self.nono_set_part(eid, inv) >= 1 and self.cover_no_qty(eid, inv, level) < 1)

    def pair_no_unwind_on(self):
        """pair_no_unwind_max_cost in effect: set (>= 0), reduce_no_as_sell in effect, and live with the self-test on,
        its own start-up check (pairno_tick) passed; "off" = the exchange refused a paired NO sale this run."""
        if getattr(self.cfg, "pair_no_unwind_max_cost", -1.0) < 0 or not self.reduce_no_on():
            return False
        state = getattr(self, "pairno_state", None)
        if state == "off":
            return False
        if self.api.live and self.cfg.selftest_enabled:
            return state == "ok"
        return True

    def nono_sets(self):
        """status.json nono_sets: races held NO on every leg (2+ legs), the sets in them (min over legs of NO held)
        and the capital they lock at the set's guaranteed payout (k - 1 per set of a k-leg race)."""
        races, sets, cap = 0, 0, 0.0
        for race, members in self.groups.items():
            if len(members) < 2 or any(m not in self.ex for m in members):
                continue
            n = min(-self.ex[m].inv for m in members)
            if n >= 1:
                races, sets, cap = races + 1, sets + int(n), cap + int(n) * (len(members) - 1)
        return {"races": races, "sets": sets, "capital": round(cap, 2)}

    def place_orders(self, orders, joint=False, gate=None):
        """POST /orders/batch with each order in its wire form (wire_order): every placement goes through here.
        Package 8 cash gate (live, cash_gate_enabled): orders are capped / dropped first (gate: the keep flags if
        the main thread already ran it, see submit_write); a dropped order gets a result {"ok": False,
        "cash_gated": True} in its place, and a batch dropped whole sends no request.
        When the gate runs HERE (the main thread's takes / arbitrage / follow-ups), the cash need of an order not
        placed (the batch raised, or its result is not ok) is given back to the gate before returning / raising."""
        own_gate = gate is None and self.cash_gate_on()
        if own_gate:
            gate, _ = self.cash_gate_orders(orders, joint=joint)
        try:
            out = self.place_gated(orders, gate)
        except Exception:
            if own_gate:
                self.cash_refund(orders)
            raise
        if own_gate:
            by_index = {r.get("index", k): r for k, r in enumerate(out or []) if isinstance(r, dict)}
            self.cash_refund([o for k, o in enumerate(orders) if not (by_index.get(k) or {}).get("ok")])
        return out

    def place_gated(self, orders, gate):
        if gate is None or all(gate):
            return self.api.place_batch([wire_order(o) for o in orders])
        send = [o for o, k in zip(orders, gate) if k]
        res = self.api.place_batch([wire_order(o) for o in send]) if send else []
        by_index = {r.get("index", j): r for j, r in enumerate(res or [])}
        out, j = [], 0
        for k, keep in enumerate(gate):
            if keep:
                r = dict(by_index.get(j) or {})
                r["index"] = k
                j += 1
            else:
                r = {"index": k, "ok": False, "status": 0, "cash_gated": True,
                     "data": {"error": {"code": "CASH_GATED", "message": "cash gate: not enough available cash"}}}
            out.append(r)
        return out

    def order_ttl(self, ex, level=0):
        """Seconds a new order lives (order_ttl for the resting quote and the set / harvest ladders alike)."""
        return self.cfg.order_ttl

    def side_fix(self, ex, resting, price, size, limit, is_bid, max_size, fv, now, now_m=None):
        """plan_change's per-side test: side_needs_change."""
        return side_needs_change(resting, price, size, self.cfg, now, limit, is_bid, max_size)

    def sync_orders(self, raw_orders, now_m):
        """Reset our record of our resting orders (self.my_orders) from the API's open-orders list.

        Between these reads (every full check) the bot keeps the record itself: placements add to it,
        cancels and fills take from it. That saves re-reading the whole list (several requests) after
        every change we make. The list is a reporting projection that can lag the exchange by a moment,
        so for recent_order_grace_seconds our own record wins: an order we just placed stays even if the
        list doesn't show it yet (else we'd place it twice), and one we just cancelled stays gone."""
        grace = self.cfg.recent_order_grace_seconds
        listed = {r.order_id: r for r in map(parse_order, raw_orders) if r}
        self.adopt_unconfirmed(listed, now_m)
        for oid, t in list(self.recent_cancels.items()):
            if now_m - t > grace:
                del self.recent_cancels[oid]
            else:
                listed.pop(oid, None)
        for oid, (order, placed) in list(self.recent_orders.items()):
            if oid in listed or now_m - placed > grace:
                del self.recent_orders[oid]           # the list shows it now (or it's old enough to trust the list)
            else:
                listed[oid] = order
        # Shares left: the list can lag a fill we've already read, and we may not have read a fill the list
        # already shows. Both only ever OVERstate what's left, so the smaller of the two is right.
        for oid, o in listed.items():
            if oid in self.placed_qty:
                o.qty = min(o.qty, self.placed_qty[oid] - self.filled_qty.get(oid, 0.0))
        self.my_orders = {oid: o for oid, o in listed.items() if o.qty > 0}
        for oid in [k for k in self.placed_qty if k not in listed]:
            self.placed_qty.pop(oid, None)            # no longer resting: stop tracking its size
            self.filled_qty.pop(oid, None)

    def match_unconfirmed(self, eid, is_bid, price, oid, expires=None, lift=True, no_sell=None):
        """An order (or fill) we have no record of: is it one we sent whose response never came back? Matched on
        exchange, side and price (and expiry when known: every batch has its own). Adopts it: its notes go to
        order_meta (fills get attributed), and the exchange's 'outcome unknown' hold lifts once every order
        sent there is accounted for. Returns the order we sent, or None. no_sell True: only an order sent as a covered
        "sell NO" (Package 6; orders are kept in YES terms, so side and price compare as for any other)."""
        cands = self.unconfirmed.get(eid) or []
        for k, (o, meta, _t) in enumerate(cands):
            if (o["action"] == "buy") != is_bid or abs(o["price"] - price) > 1e-6:
                continue
            if no_sell is not None and bool(o.get("_no_sell")) != no_sell:
                continue
            if expires is not None and abs((parse_ts(o["expirationDate"]) - expires).total_seconds()) > 1.5:
                continue
            del cands[k]
            self.order_meta[oid] = {**meta, "eid": eid, "recovered": True}
            self.notes_dirty = True
            self.placed_qty[oid], self.filled_qty[oid] = float(o["quantity"]), self.filled_qty.get(oid, 0.0)
            if not cands:
                self.unconfirmed.pop(eid, None)
                ex = self.ex.get(eid)
                # All accounted for: quote here again straight away - but only lift the hold these sends set
                # (not one from a take, an arbitrage, a 502 or the self-test), and only when the order was seen
                # in the open-orders list (a fill alone doesn't say whether the rest of it still rests).
                if (lift and ex and ex.pending_until == ex.unconfirmed_hold and eid != self.selftest_eid
                        and not any(eid in w.eids for w in self.writes)):
                    ex.pending_until = 0.0
            log.info("recovered order %s on %s (its placement response was lost)", oid, eid)
            return o
        return None

    def adopt_unconfirmed(self, listed, now_m):
        """Open-orders list just read: adopt listed orders we have no record of (see match_unconfirmed), and
        forget sends older than pending_seconds (never landed, or filled at once - fills still match them)."""
        for eid in list(self.unconfirmed):
            self.unconfirmed[eid] = [c for c in self.unconfirmed[eid] if now_m - c[2] <= 2 * self.cfg.pending_seconds]
            if not self.unconfirmed[eid]:
                del self.unconfirmed[eid]
        if not self.unconfirmed:
            return
        for oid, r in listed.items():
            if oid not in self.order_meta and r.eid in self.unconfirmed:
                self.match_unconfirmed(r.eid, r.is_bid, r.price, oid, r.expires)

    def orders_by_eid(self, now):
        """Our resting orders (live) grouped by exchange, dropping any that have expired."""
        out = defaultdict(list)
        for oid, o in list(self.my_orders.items()):
            if o.expires and o.expires <= now:
                del self.my_orders[oid]
            else:
                out[o.eid].append(o)
        return out

    def remember_order(self, order, data, now_m):
        """Add an order we just placed (API request format + its batch result) to our record of resting
        orders, unless it traded in full straight away. Every placement goes through here (quotes,
        arbitrage, takes), so the bot never quotes on top of an order it doesn't know about."""
        oid = data.get("orderId")
        traded = float(data.get("quantityTraded") or 0)
        left = order["quantity"] - traded
        if not self.api.live or oid is None or left <= 0:
            return
        o = Resting(oid, order["exchangeId"], order["action"] == "buy", order["price"], left,
                    parse_ts(order["expirationDate"]))
        self.my_orders[oid], self.recent_orders[oid] = o, (o, now_m)
        # filled_qty starts at 0 even if part traded at once: that trade also arrives as fills (log_fills),
        # and counting it here too would count it twice. o.qty already starts at what's left.
        self.placed_qty[oid], self.filled_qty[oid] = float(order["quantity"]), 0.0

    def forget_orders(self, oids):
        """These orders are gone (cancelled or fully filled): drop them from our record."""
        now_m = util.time.monotonic()
        for oid in oids:
            self.my_orders.pop(oid, None)
            self.recent_orders.pop(oid, None)
            self.recent_cancels[oid] = now_m

    def cancel_everything(self):
        """Cancel every order the bot is responsible for. With ONLY_EXCHANGES set, that's just those
        exchanges, so orders you placed by hand elsewhere survive. Otherwise it's the whole tournament."""
        self.sim.clear()
        self.cancel_gen += 1
        if hasattr(self, "writes"):
            self.stop_queued_writes()
        self.orders_stale = True                  # confirm with a fresh read next cycle
        # Forget our orders only once the cancel is confirmed. Forgetting them first (as before) meant a cancel
        # that failed (409, timeout) left every order resting while the bot believed - and for
        # recent_order_grace_seconds even hid from the open-orders list - that they were gone: the next cycle
        # quoted every market a second time.
        gone = list(self.my_orders)
        if not self.cfg.only_exchanges:
            ok = self.api.cancel_all(self.tid)
        else:
            ok = all([self.api.cancel_all(self.tid, eid) for eid in self.ex])   # list: try every one
        if ok:
            self.forget_orders(gone)
        return ok

    def cancel(self, eid, orders, whole_exchange, quiet=False):
        """Cancel orders on one exchange. Returns True only if we're sure they're gone."""
        if not self.api.live:
            for o in orders:
                self.sim.pop(o.order_id, None)
            return True
        if whole_exchange and eid == self.selftest_eid:
            self.cancel_gen += 1          # this also removes any self-test orders there (see selftest_finish)
        try:
            if whole_exchange:
                ok = self.api.cancel_all(self.tid, eid)
            else:
                ok = all(self.api.cancel_order(o.order_id) for o in orders)
        except ApiError as e:
            if not quiet:
                log.error("cancel on %s failed: %s", eid, e)
            ok = False
        if ok:                                # gone: update our record of what's resting
            self.cash_credit([o for o in self.my_orders.values() if o.eid == eid] if whole_exchange else
                             [o for o in orders if o.order_id in self.my_orders])   # Package 8 (gate on only)
            self.forget_orders([oid for oid, o in self.my_orders.items() if o.eid == eid] if whole_exchange
                               else [o.order_id for o in orders])
        else:
            self.orders_stale = True          # not sure what's left: re-read the list next cycle
        return ok

    def place(self, new_orders, now_m):
        """One write at a time: send new orders in batches and record what each one was for."""
        cfg = self.cfg
        for i in range(0, len(new_orders), cfg.batch_size):
            chunk = new_orders[i:i + cfg.batch_size]
            try:
                results = self.place_orders([o for o, _ in chunk])
            except ApiError as e:
                results = e
            self.apply_batch(chunk, results, now_m)

    def apply_batch(self, chunk, results, now_m):
        """Main thread: record the outcome of one batch (its results, or the exception it raised): what each
        order was for (fill attribution), our record of resting orders, and back-offs."""
        cfg = self.cfg
        if isinstance(results, Exception):
            self.cash_refund([o for o, _ in chunk])   # Package 8: their cash need back (WRITE_BUDGET_WAIT, errors)
            e = results if isinstance(results, ApiError) else ApiError(0, "NETWORK", str(results))
            # Unknown outcome (network, 409 in flight, 502/503 after retries): some orders may
            # exist, so leave these exchanges alone until they show up. Clear rejection: back off.
            ambiguous = e.status in (0, 409, 502, 503, 504)
            self.orders_stale = self.orders_stale or ambiguous   # some may exist: re-read the list
            for o, meta in chunk:
                ex = self.ex.get(o["exchangeId"])
                if ex:
                    if ambiguous:
                        ex.pending_until = ex.unconfirmed_hold = now_m + cfg.pending_seconds
                        if cfg.recover_unconfirmed:
                            self.unconfirmed.setdefault(ex.eid, []).append((o, meta, now_m))
                    else:
                        ex.pause_until = now_m + cfg.fail_pause_seconds
            log.error("batch of %d orders failed (%s)", len(chunk), e)
            if e.code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {e.code} - fix it (accept the terms in the web UI / check the API key) and restart")
            return

        by_index = {r.get("index", k): r for k, r in enumerate(results)}
        for k, (order, meta) in enumerate(chunk):
            r, ex = by_index.get(k, {}), self.ex.get(order["exchangeId"])
            data = r.get("data") or {}
            if r.get("ok"):
                order.pop("_cash_need", None)             # placed: its cash stays spent until the next read
                oid = data.get("orderId")
                if not self.api.live:                     # dry run: remember it as if it were resting
                    oid, self.next_sim_id = self.next_sim_id, self.next_sim_id - 1
                    self.sim[oid] = Resting(oid, order["exchangeId"], order["action"] == "buy",
                                            order["price"], order["quantity"], parse_ts(order["expirationDate"]))
                if oid is not None:
                    self.order_meta[oid] = {**meta, "eid": order["exchangeId"]}
                    self.notes_dirty = True
                self.remember_order(order, data, now_m)       # our record of resting orders
                if data.get("quantityTraded"):
                    log.info("order on %s traded %s immediately", order["exchangeId"], data["quantityTraded"])
                    self.orders_stale = True          # positions changed: re-read them next cycle
                continue
            self.cash_refund([order])                 # Package 8: not placed: its cash need back to the gate
            if r.get("cash_gated"):                   # Package 8: never sent (cash gate, logged there): no back-off
                continue
            err = data.get("error") or {}
            if not isinstance(err, dict):             # plain-text error: {"error": "Not found"}
                err = {"message": str(err)}
            code = err.get("code") or data.get("code") or r.get("status")
            log.warning("order rejected on %s (%s): %s", ex.label if ex else order["exchangeId"],
                        code, err.get("message") or data.get("error"))
            if ex:
                if r.get("status") == 502:                # ORDER_STATUS_UNKNOWN: it may exist
                    ex.pending_until = now_m + cfg.pending_seconds
                    self.orders_stale = True
                elif r.get("status") not in (429, 503):   # transient ones just retry next cycle
                    ex.pause_until = now_m + cfg.fail_pause_seconds
            if code in FATAL_API_CODES:
                util.fatal(f"orders rejected with {code} - fix it (accept the terms in the web UI / check the API key) and restart")

    def log_fills(self, fvs):
        """Fetch fills newer than the last one we logged (paging back up to max_fill_pages pages), and
        update our record of resting orders: an order's shares left = what we placed - what has filled
        (each fill is new exactly once, so nothing is counted twice)."""
        new, cursor = [], None
        for _ in range(self.cfg.max_fill_pages):
            page = self.api.fills_page(cursor)
            batch = page.get("data", [])
            fresh = [f for f in batch if str(f["id"]) not in self.fills.seen]
            new += fresh
            if len(fresh) < len(batch) or not page.get("pagination", {}).get("hasMore"):
                break
            cursor = page["pagination"]["nextCursor"]
        for f in new:
            oid = f.get("orderId")
            if oid is not None and oid not in self.order_meta and str(f.get("exchangeId")) in self.unconfirmed:
                # A fill's price is in the terms of its side: a NO-side fill (our converted ask) is 1 - the YES price.
                # quantity > 0 = a YES-side fill (our bid, or a sell of YES we held); < 0 = a NO-side fill (our
                # ask, converted to buy NO): day one reported those at the NO price (1 - our ask), but accept the
                # YES price too.
                qty, p = float(f.get("quantity") or 0), float(f.get("price") or 0)
                eid = str(f.get("exchangeId"))
                # Package 6: a covered "sell NO @ 1-b" (our bid at b) is a NO-side fill too, at the NO price: tried
                # (only against orders sent that way) before the YES-price fallback for asks.
                # With the flag on, the covered-bid reading of a YES-side fill goes before the ask one: in a lost batch
                # holding a covered sale at b and an ask at 1-b, a covered-sale fill must not be adopted as the ask.
                tries = ((((True, p, None), (True, rnd(1 - p), True), (False, p, None)) if self.cfg.reduce_no_as_sell
                          else ((True, p, None), (False, p, None), (True, rnd(1 - p), True))) if qty > 0 else
                         ((False, rnd(1 - p), None), (True, rnd(1 - p), True), (False, p, None), (True, p, True)))
                for is_bid, price, no_sell in tries:
                    if self.match_unconfirmed(eid, is_bid, price, oid, lift=False, no_sell=no_sell):
                        break
        if new:
            self.fills.record(new, self.order_meta, fvs)
            self.note_fill_p(new)                             # (mm_carry_24h: Polymarket p at fill time)
            try:
                self.hv_note_fills(new)                       # P15: "HARVEST fill ..." and its tallies
            except Exception as e:                            # reporting must never disturb trading
                log.warning("harvest fill note failed: %s", e)
        for f in reversed(new):                               # oldest first
            oid = f.get("orderId")
            o = self.my_orders.get(oid)
            if o is None:
                if oid in self.placed_qty:                    # recovered from its fill, not listed yet
                    self.filled_qty[oid] += abs(float(f.get("quantity") or 0))
                continue
            if oid not in self.placed_qty:
                self.orders_stale = True                      # an order we didn't place this run: re-read the list
                continue
            self.filled_qty[oid] += abs(float(f.get("quantity") or 0))
            o.qty = min(o.qty, self.placed_qty[oid] - self.filled_qty[oid])
            if o.qty <= 0:                                    # fully filled: not resting any more
                self.forget_orders([oid])
        cutoff = util.time.time() - 86400                          # forget order notes older than a day
        kept = {k: v for k, v in self.order_meta.items() if v["t"] > cutoff}
        if len(kept) != len(self.order_meta):
            self.order_meta, self.notes_dirty = kept, True
        self.save_order_notes()
        return new
