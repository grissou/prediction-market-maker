"""
StatusMixin: status.json (write_status, status_report), the recorder (sqlite), the phone summary,
ops / EV / carry fields, the overrides file reader (check_overrides), warn_settings and the market-
edge file.

Methods only: all state lives on the Bot instance (self); no __init__ here. Never imports bot.py.
Reporting only: never sends orders.
"""
import csv
import json
import os
import sqlite3
from collections import defaultdict, deque

from mmbot import util
from mmbot import config
from mmbot import pricing
from mmbot import measure
from mmbot.util import bot_path, iso, log, numeric_fields, parse_ts, write_json
from mmbot.config import P141_REFILL, validate_market_edge, validate_overrides
from mmbot.pricing import _num, fair_value, normalise
from mmbot.measure import analyze, ev_outcome_part, mm_carry_part, ops_summary_line


class StatusMixin:

    def load_order_notes(self):
        """Order notes saved by a previous run (so fills that land around a restart get attributed)."""
        try:
            with open(bot_path(self.cfg.order_notes_file)) as f:
                return {int(k): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            return {}

    def save_order_notes(self):
        if self.notes_dirty and self.api.live:                # dry-run notes are fake: don't save them
            write_json(bot_path(self.cfg.order_notes_file), {str(k): v for k, v in self.order_meta.items()})
            self.notes_dirty = False

    # ------------------------------------------------------------------------------ recording (#10)
    def open_recorder(self):
        """SQLite file of market snapshots: what the books, our fair value, the outside reference and
        our quotes looked like over time. For tuning settings afterwards; costs no extra requests.
        Open it with any SQLite viewer, or pandas: pd.read_sql("select * from snapshots", conn)."""
        if not self.cfg.record_file:
            return None
        db = sqlite3.connect(bot_path(self.cfg.record_file), check_same_thread=False)
        db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                          ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL, best_ask REAL,
                          fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS account (
                          ts TEXT, mode TEXT, account_value REAL, locked_in_orders REAL,
                          worst_case_loss REAL, party_delta REAL, orders_resting INTEGER,
                          liquidation_value REAL)""")
        # Older files: add the column (nullable; rows written before stay NULL).
        self.acct_liq = True
        try:
            if "liquidation_value" not in {r[1] for r in db.execute("PRAGMA table_info(account)")}:
                db.execute("ALTER TABLE account ADD COLUMN liquidation_value REAL")
        except sqlite3.Error as e:
            self.acct_liq = False
            log.warning("recorder: could not add account.liquidation_value (%s) - not recorded", e)
        db.execute("CREATE INDEX IF NOT EXISTS snapshots_eid_ts ON snapshots (eid, ts)")
        # Other traders' book tops (our own orders removed), one row per change: bids/asks = JSON [[price, size]...]
        db.execute("CREATE TABLE IF NOT EXISTS books (ts REAL, eid TEXT, bids TEXT, asks TEXT)")
        # Tournament trades from the realtime feed (all traders): price/quantity when the item carries them
        db.execute("CREATE TABLE IF NOT EXISTS trades (ts REAL, eid TEXT, price REAL, quantity REAL, item TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS books_eid_ts ON books (eid, ts)")
        if self.cfg.record_positions:
            # The exchange's own valuation, per position (a row when anything in it changed, or hourly): ts = when
            # read, prev_ts = the previous read that saw it (so a change happened in (prev_ts, ts]), fields = JSON
            # of every other number the API gives for it (cost basis, P&L, market value...).
            db.execute("""CREATE TABLE IF NOT EXISTS positions (ts REAL, prev_ts REAL, eid TEXT, quantity REAL,
                              current_price REAL, fields TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS positions_eid_ts ON positions (eid, ts)")
            # Account totals at the same reads; P&L-endpoint numbers are from the read at pnl_ts (they're read
            # every slow_poll_seconds); fields = JSON of every number in the P&L reply and the positions summary.
            db.execute("""CREATE TABLE IF NOT EXISTS account_marks (ts REAL, account_value REAL, market_value REAL,
                              cash REAL, realized REAL, unrealized REAL, pnl_ts REAL, fields TEXT)""")
        db.commit()
        return db

    POS_PRICE_KEYS = ("currentPrice", "markPrice", "valuationPrice", "price")

    def record_positions(self, pos, f_pnl, now_m, fill_event):
        """Recorder (record_positions): the positions reply just read - no extra request. Written at most once per
        record_seconds, plus at the first read after a fill; a position's row only when one of its numbers changed
        since it was last written (or every record_positions_full_seconds), which keeps it to ~1-3 MB a day."""
        cfg = self.cfg
        if f_pnl is not None:
            try:
                self.last_pnl_reply = (f_pnl.result(), util.utcnow().timestamp())
            except Exception:                     # the P&L read failing is handled (and logged) by account_value
                pass
        if not (fill_event or self.pos_record_due or now_m - self.last_pos_record >= cfg.record_seconds):
            return
        self.last_pos_record, self.pos_record_due = now_m, False
        full = now_m - self.last_pos_full >= cfg.record_positions_full_seconds
        if full:
            self.last_pos_full = now_m
        ts = round(util.utcnow().timestamp(), 3)
        try:
            rows, seen = [], set()
            for p in pos.get("positions", []) or []:
                if not isinstance(p, dict) or p.get("settled") or p.get("exchangeId") is None:
                    continue
                eid = str(p["exchangeId"])
                seen.add(eid)
                nums = numeric_fields(p, skip=("exchangeId",))
                price = next((nums.pop(k) for k in self.POS_PRICE_KEYS if k in nums), None)
                qty = nums.pop("quantity", None)
                vals = (qty, price, json.dumps(nums, sort_keys=True, separators=(",", ":")))
                old = self.pos_seen.get(eid)
                if full or old is None or old[0][:2] != vals[:2]:   # quantity or the mark changed (P&L ticks
                    rows.append((ts, old[1] if old else None, eid, *vals))   # alone would write every position every minute)
                self.pos_seen[eid] = (vals, ts)
            for eid in [e for e in self.pos_seen if e not in seen]:   # closed (or settled): one row saying so
                rows.append((ts, self.pos_seen.pop(eid)[1], eid, 0.0, None, "{}"))
            pnl, pnl_ts = self.last_pnl_reply
            pnl_n = numeric_fields(pnl) if isinstance(pnl, dict) else {}
            summ = pos.get("summary")
            sum_n = numeric_fields(summ) if isinstance(summ, dict) else {}
            top_n = numeric_fields({k: v for k, v in pos.items() if k not in ("positions", "summary")})

            def pick(want, avoid=None):
                for src in (pnl_n, sum_n):
                    for k, v in src.items():
                        kl = k.lower()
                        if any(w in kl for w in want) and not (avoid and avoid in kl):
                            return v
                return None
            acct = (ts, pnl_n.get("totalAccountValue"), sum_n.get("totalMarketValue", pnl_n.get("totalMarketValue")),
                    pick(("cash", "balance")), pick(("realized", "realised"), "unreali"),
                    pick(("unrealized", "unrealised")), pnl_ts and round(pnl_ts, 3),
                    json.dumps({**{"pnl." + k: v for k, v in pnl_n.items()}, **{"sum." + k: v for k, v in sum_n.items()},
                                **top_n}, sort_keys=True, separators=(",", ":")))
            self.db.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?)", rows)
            self.db.execute("INSERT INTO account_marks VALUES (?,?,?,?,?,?,?,?)", acct)
            self.db.commit()
        except (sqlite3.Error, AttributeError, TypeError, ValueError) as e:
            log.warning("could not record positions: %s", e)

    def record(self, fvs, now_m):
        """One row per market (best bid/ask incl. ours, fair value, reference, our quote, position)."""
        if not self.db or now_m - self.last_record < self.cfg.record_seconds:
            return
        self.last_record = now_m
        ts, mode, h = iso(util.utcnow()), "live" if self.api.live else "dry", self.health
        rows = [(ts, mode, eid, ex.label, *self.last_tops.get(eid, (None, None)), fvs.get(eid), ex.ref,
                 ex.quote.bid, ex.quote.ask, ex.inv) for eid, ex in self.ex.items()]
        trades = []
        if self.cfg.record_books and self.feed and hasattr(self.feed, "take_trades"):
            for t, item in self.feed.take_trades():
                trades.append((round(t, 3), str(item.get("exchangeId")), _num(item.get("price")),
                               _num(item.get("quantity")), json.dumps(item, default=str)[:1000]))
        book_rows, self.book_rows = self.book_rows, []
        try:
            self.db.executemany("INSERT INTO books VALUES (?,?,?,?)", book_rows)
            self.db.executemany("INSERT INTO trades VALUES (?,?,?,?,?)", trades)
            self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            acct = (ts, mode, h.get("account_value"), h.get("locked_in_orders"), h.get("worst_case_loss"),
                    h.get("party_delta"), h.get("orders_resting"))
            if getattr(self, "acct_liq", False):  # (the latest status write's value: None until there is one)
                self.db.execute("INSERT INTO account (ts, mode, account_value, locked_in_orders, worst_case_loss, "
                                "party_delta, orders_resting, liquidation_value) VALUES (?,?,?,?,?,?,?,?)",
                                acct + ((self.ops_last or {}).get("liquidation_value"),))
            else:
                self.db.execute("INSERT INTO account (ts, mode, account_value, locked_in_orders, worst_case_loss, "
                                "party_delta, orders_resting) VALUES (?,?,?,?,?,?,?)", acct)
            self.db.commit()
        except sqlite3.Error as e:
            log.warning("could not record snapshot: %s", e)

    # ------------------------------------------------------------------------------ phone summary (#12)
    def maybe_summary(self):
        """Every summary_every_hours, on the hour UTC (2 = 00:00, 02:00, 04:00...), push a summary to your
        phone (live only), also while waiting for the open. First line: the bot's status - "OK" or the
        ISSUES since the previous summary (then the title says ISSUES and it's sent at high priority).
        Then P&L, rank, Smart Score, fills since the previous summary and bot health. If an update
        doesn't arrive on time, the bot itself is down. Costs 2-3 requests (P&L, rank, Smart Score)."""
        every, now = self.cfg.summary_every_hours, util.utcnow()
        slot = (now.date(), now.hour)
        if every <= 0 or not self.api.live or now.hour % every or self.last_summary_slot == slot:
            return
        self.last_summary_slot = slot
        value = self.health.get("account_value")
        marks = self.counts_at_last_summary
        status, problems = self.status_report()
        try:                                  # a report must never be able to disturb trading
            title, message = measure.build_summary(self.api, bot_path(self.cfg.fills_csv), self.initial_balance, value=value,
                                           value_prev=self.value_at_last_summary,
                                           arbs=self.arbs_total - marks["arbs"], takes=self.takes_total - marks["takes"],
                                           health=self.health, hours=every, status=status,
                                           ops_line=self.summary_ops_line(value))
        except Exception as e:
            log.warning("phone summary failed (%s) - skipped", e)
            return
        if problems:
            title = title.replace("mm_bot:", "mm_bot ISSUES:", 1)
        log.info("%s\n%s", title, message)
        util.notify(message, title=title, priority="high" if problems else "default",
               tags="warning" if problems else "chart_with_upwards_trend")
        self.value_at_last_summary = value
        self.counts_at_last_summary = {"arbs": self.arbs_total, "takes": self.takes_total,
                                       "errors": self.errors_total, "rate_limits": getattr(self.api, "rate_limited", 0)}

    def summary_ops_line(self, value):
        """ops_summary_line for this bot (latest ops fields; tilt_s / tilt_exposure when T2.1 set them), plus
        " | NO+NO sets N (cap X)" (Package 7: N races held NO on every leg) when any is held. Never raises."""
        try:
            line = ops_summary_line(self.ops_last or self.safe_ops_fields(), value,
                                    getattr(self, "tilt_s", None), getattr(self, "tilt_exposure", None))
        except Exception as e:                    # a report must never disturb trading
            log.warning("summary ops line failed: %s", e)
            return None
        nn = self.safe_nono_sets()
        if nn and nn.get("races"):
            part = f"NO+NO sets {nn['races']} (cap {nn['capital'] / 1000:.1f}k)"
            line = f"{line} | {part}" if line else part
        if getattr(self.cfg, "bloc_delta_enabled", False):   # Package 10 A2: " | bloc delta X/sd"
            part = f"bloc delta {self.bloc_delta:+,.0f}/sd"
            line = f"{line} | {part}" if line else part
        part = self.mm_risk_summary()                 # P12 ops: " | risk room wc Xk corr Yk (paused)"
        if part:
            line = f"{line} | {part}" if line else part
        part = self.mm_funding_summary()              # P14: " | MM funding cash Xk/Yk, ..." (a P14 flag on)
        if part:
            line = f"{line} | {part}" if line else part
        part = self.hv_summary()                      # P15: " | harvest ... | state caps ..." (while in use)
        if part:
            line = f"{line} | {part}" if line else part
        for fn in (ev_outcome_part, mm_carry_part):   # " | EV outcome X (+Y 24h, N unpriced) | MM carry 24h ..."
            try:
                part = fn(self.ops_last)
            except (TypeError, ValueError) as e:
                log.warning("summary %s failed: %s", fn.__name__, e)
                part = None
            if part:
                line = f"{line} | {part}" if line else part
        return line

    def safe_nono_sets(self):
        """nono_sets that never raises (None on any error)."""
        try:
            return self.nono_sets()
        except Exception as e:                    # reporting must never disturb trading
            log.warning("nono_sets unavailable: %s", e)
            return None

    def status_report(self):
        """(status line, problems) for the phone summary: what the bot is doing right now, and anything
        that has gone wrong since the previous summary. Problems are things you may want to look at."""
        h, cfg, marks = self.health, self.cfg, self.counts_at_last_summary
        errors = self.errors_total - marks["errors"]
        rate_limits = getattr(self.api, "rate_limited", 0) - marks["rate_limits"]
        problems = []
        if self.pulled_after_errors:
            problems.append("all quotes PULLED after repeated errors")
        if self.failed_cycles:
            problems.append(f"the last {self.failed_cycles} cycle(s) failed")
        elif errors:
            problems.append(f"{errors} failed cycle(s) since the last update, recovered since")
        if rate_limits:
            problems.append(f"{rate_limits} rate limit(s) (429) since the last update")
        if h.get("realtime") == "reconnecting":
            problems.append("realtime feed down, polling meanwhile")
        if h.get("reduce_only"):
            problems.append("reduce-only: settlement risk above the cap")
        mapped = len(getattr(self.refs, "mapping", {}) or {}) if self.refs else 0
        if mapped and h and h.get("reference_prices", 0) < 0.5 * mapped:
            problems.append(f"Polymarket prices missing ({h.get('reference_prices', 0)} of {mapped})")
        phase = self.phase
        if phase == "trading":
            hrs = min((self.hours_to_close(ex) for ex in self.ex.values()), default=float("inf"))
            if hrs * 60 <= cfg.stop_minutes_before_close:
                phase = "stopped for settlement"
            elif hrs <= self.close_window("exit_hours_before_close"):
                phase = f"election night: exiting positions ({hrs:.1f} h to close)"
            elif hrs <= self.close_window("flatten_hours_before_close"):
                phase = f"election night: reducing positions ({hrs:.1f} h to close)"
            elif h and not h.get("orders_resting"):
                problems.append("no orders resting")
            if self.selftest_passed:
                phase += ", self-test passed"
        return ("Status: " + ("OK" if not problems else "ISSUES - " + "; ".join(problems)) + f" | {phase}"), problems

    # ------------------------------------------------------------------------------ ops fields (Package 5, 3.3)
    OPS_KEYS = ("liquidation_value", "liquidation_unpriced", "realised_pnl", "unrealised_pnl", "pnl_unreconciled",
                "toward_ref_capital_frac", "capital_over_6h_frac", "exit_ratio_24h")
    OPS_FILLS_MIN_SECONDS = 60.0          # fills.csv is re-read at most this often (and only when it changed)

    def ops_fills(self, now):
        """From fills.csv (cached; read when the file changed, at most every OPS_FILLS_MIN_SECONDS): FIFO lots with
        prices {eid: [[signed shares, YES price], ...]}, realised P&L, and over the last 24 h the shares that
        reduced |position| and the shares that added to it. Lots in position_lots_file carry no prices, so the fills
        are replayed: each fill's YES price is its quote price (fill price when absent), sign from our side (bid =
        bought YES); fills not matched to a quote of ours (our_side "?": arbitrage, takes) are skipped, so the
        realised figure covers maker fills only. Incremental: only the bytes appended since the saved offset are
        read (complete lines only); the replay restarts from 0 if the file shrank or was replaced."""
        path = bot_path(self.cfg.fills_csv)
        try:
            st = os.stat(path)
            sig = (st.st_mtime, st.st_size)
        except OSError:
            st, sig = None, None
        c = self.ops_cache
        if c and (c.get("sig") == sig or now - c.get("t", 0) < self.OPS_FILLS_MIN_SECONDS):
            return c
        if (not c or st is None or st.st_size < c.get("off", 0) or c.get("ino") != st.st_ino):
            c = {"off": 0, "ino": st.st_ino if st is not None else None, "fields": None, "book": defaultdict(deque),
                 "realised": 0.0, "events": deque()}
        if st is not None and st.st_size > c["off"]:
            with open(path, "rb") as f:
                f.seek(c["off"])
                data = f.read(st.st_size - c["off"])
            done = data.rfind(b"\n") + 1                # complete lines only: a half-written row waits
            self.ops_cache = c                          # progress kept even if a row raises something unexpected
            for raw in data[:done].split(b"\n")[:-1]:  # the offset advances over each row once it is consumed
                line = raw.decode("utf-8", "replace").rstrip("\r")
                try:
                    vals = next(csv.reader([line]), [])
                    if c["fields"] is None:
                        if vals:
                            c["fields"] = vals
                    elif vals:
                        self.ops_replay(c, dict(zip(c["fields"], vals)))
                except (ValueError, TypeError, KeyError, IndexError, OverflowError, csv.Error):
                    pass                                # a malformed row: skipped, the rest still replayed
                c["off"] += len(raw) + 1
        since = now - 24 * 3600
        ev = c["events"]
        while ev and ev[0][0] < since:
            ev.popleft()
        rows = c.setdefault("rows", deque())
        while rows and rows[0][0] < since:
            rows.popleft()
        c.update(sig=sig, t=now, lots={e: [list(x) for x in v] for e, v in c["book"].items() if v},
                 reduced_24h=sum(r for t, r, _ in ev if t >= since), added_24h=sum(a for t, _, a in ev if t >= since))
        self.ops_cache = c
        return c

    @staticmethod
    def ops_replay(c, r):
        """One fills.csv row into the ops_fills state c (FIFO lots, realised, 24-h events)."""
        side = r.get("our_side")
        if side not in ("bid", "ask"):
            return
        try:
            qty = abs(float(r.get("qty") or 0))
            price = float(r.get("quote_price") or r.get("fill_price") or 0)
        except ValueError:
            return
        if qty <= 0 or not 0 < price < 1:
            return
        ts = parse_ts(r.get("filled_at"))           # a bad timestamp raises here, before any state changes
        ts = ts.timestamp() if ts is not None else None
        book, rem, red, add = c["book"][str(r.get("exchange_id"))], qty if side == "bid" else -qty, 0.0, 0.0
        while abs(rem) > 1e-9 and book and (book[0][0] > 0) != (rem > 0):
            lot = book[0]
            n = min(abs(rem), abs(lot[0]))
            c["realised"] += n * (price - lot[1]) * (1 if lot[0] > 0 else -1)
            lot[0] += n if lot[0] < 0 else -n
            rem += n if rem < 0 else -n
            red += n
            if abs(lot[0]) <= 1e-9:
                book.popleft()
        if abs(rem) > 1e-9:
            book.append([rem, price])
            add += abs(rem)
        if ts is not None:
            c["events"].append((ts, red, add))
            # (mm_carry_24h) the row itself, kept 24 h: (time, fill id, order id, market, bid?, shares, YES price)
            c.setdefault("rows", deque()).append((ts, str(r.get("fill_id")), str(r.get("order_id")),
                                                  str(r.get("exchange_id")), side == "bid", qty, price))
            c["first_ts"] = min(c.get("first_ts") or ts, ts)

    def ops_fields(self, now=None):
        """Read-only reporting for status.json, the recorder and the phone summary (no requests; None = unknown):
          liquidation_value       account value minus the haircut of selling every position to OTHER traders now:
                                  longs at the best other bid, shorts at the best other ask, instead of the mark
                                  (the exchange's valuation price, else the book's fair value). A market with no
                                  quote on that side keeps its mark and is counted in liquidation_unpriced.
          realised_pnl / unrealised_pnl   FIFO over fills.csv maker fills only (ops_fills; status.json says so in
                                  realised_pnl_scope); unrealised at the mark, only over markets
                                  whose replayed position equals the position held (the others: pnl_unreconciled)
          toward_ref_capital_frac share of position capital on the side Polymarket favours (long with Polymarket
                                  above the book's own fair value, or short with it below)
          capital_over_6h_frac    share of position capital in lots older than 6 h (update_lots)
          exit_ratio_24h          fill shares that reduced |position| / shares that added, last 24 h"""
        cfg = self.cfg
        now = util.time.time() if now is None else now
        out = dict.fromkeys(self.OPS_KEYS)
        inv = {e: float(q) for e, q in (self.held or {}).items() if e in self.ex and round(q)}
        book_fv = {}
        for members in self.groups.values():
            if any(e in inv for e in members):
                fv = {e: fair_value(self.ex[e].book, cfg) for e in members if e in self.ex}
                book_fv.update(normalise(fv) if len(fv) > 1 else fv)
        marks, cap, haircut, unpriced = {}, {}, 0.0, 0
        for e, q in inv.items():
            m = self.pos_marks.get(e)
            m = m if m is not None else book_fv.get(e)
            if m is None:
                unpriced += 1
                continue
            marks[e] = m
            cap[e] = abs(q) * (m if q > 0 else 1 - m)
            b = self.ex[e].book or {}
            lvl = b.get("bids" if q > 0 else "asks")
            if not lvl:
                unpriced += 1
                continue
            haircut += abs(q) * ((m - lvl[0]["price"]) if q > 0 else (lvl[0]["price"] - m))
        acct = self.health.get("account_value")
        out["liquidation_unpriced"] = unpriced
        if acct is not None:
            out["liquidation_value"] = round(float(acct) - haircut, 2)
        total = sum(cap.values())
        if total > 0:
            toward = 0.0
            for e, c in cap.items():
                r, f = (self.cur_refs or {}).get(e), book_fv.get(e)
                if r is not None and f is not None and ((inv[e] > 0 and r > f) or (inv[e] < 0 and r < f)):
                    toward += c
            out["toward_ref_capital_frac"] = round(toward / total, 4)
            old = 0.0
            for e, c in cap.items():
                lots = self.lots.get(e) or []
                n = sum(abs(x) for x, _ in lots)
                if n:
                    old += c * min(1.0, sum(abs(x) for x, t in lots if now - t > 6 * 3600) / n)
            out["capital_over_6h_frac"] = round(old / total, 4)
        fl = self.ops_fills(now)
        out["realised_pnl"] = round(fl["realised"], 2)
        unreal, bad = 0.0, 0
        for e in set(inv) | set(fl["lots"]):
            lots = fl["lots"].get(e, [])
            if round(sum(x for x, _ in lots)) != round(inv.get(e, 0.0)) or (e in inv and e not in marks):
                bad += 1
                continue
            unreal += sum(x * (marks[e] - p) for x, p in lots) if lots else 0.0
        out["unrealised_pnl"], out["pnl_unreconciled"] = round(unreal, 2), bad
        out["exit_ratio_24h"] = round(fl["reduced_24h"] / fl["added_24h"], 3) if fl["added_24h"] > 0 else None
        return out

    # ------------------------------------------------------------------------------ EV at the outcome, MM carry
    EV_KEYS = ("ev_outcome", "ev_outcome_unpriced", "ev_outcome_delta_24h", "ev_outcome_scope", "mm_carry_24h")
    EV_HIST_SECONDS = 300.0       # one (wall, ev) sample at most this often (48 h = 576 samples in status.json)
    EV_HIST_KEEP = 48 * 3600.0
    EV_SCOPE = ("cash (cash gate read, else account - positions at marks) + positions held to the outcome at the "
                "race-scaled liquid Polymarket price (long q x r, short |q| x (1 - r)); no liquid price: at the "
                "exchange mark, else the book's fair value, else left out (all counted in ev_outcome_unpriced)")

    def load_ev_hist(self):
        """status.json ev_outcome_history ([[wall, ev], ...]) the previous run left; [] if none or unreadable."""
        try:
            with open(bot_path(self.cfg.status_file)) as f:
                h = json.load(f).get("ev_outcome_history")
            return [[float(t), float(v)] for t, v in h] if isinstance(h, list) else []
        except (OSError, ValueError, TypeError, AttributeError):
            return []

    def ev_p(self, e):
        """The outcome value of one YES share in market e: value_p (race-scaled liquid Polymarket), else None."""
        ex = self.ex.get(e)
        if ex is None:
            return None
        return self.value_p(ex, (self.cur_refs or {}).get(e), e in (self.cur_liquid or ()))

    def note_fill_p(self, new):
        """mm_carry_24h: the Polymarket p of each newly logged fill's market now (= at fill time, to a cycle)."""
        try:
            for f in new:
                self.ev_fill_p[str(f.get("id"))] = self.ev_p(str(f.get("exchangeId")))
            if len(self.ev_fill_p) > 20000:               # (a day of fills is far fewer: drop the oldest half)
                self.ev_fill_p = dict(list(self.ev_fill_p.items())[10000:])
        except Exception as e:                            # reporting must never disturb trading
            log.warning("fill p note failed: %s", e)

    def ev_fields(self, now=None, record=False):
        """Read-only reporting (status.json, the realtime line, the phone summary; None = unknown):
          ev_outcome            what the account pays at the OUTCOME: cash + positions valued at Polymarket (see
                                EV_SCOPE); cash = the cash gate's cg_cash when read, else account value - positions
                                at marks (exchange mark, else book fair value)
          ev_outcome_unpriced   held markets without a liquid Polymarket price (valued at the mark / book, or out)
          ev_outcome_delta_24h  ev now - ev 24 h ago from a 48 h ring of (wall, ev) samples (status.json
                                ev_outcome_history, EV_HIST_SECONDS apart; record=True adds one); None before 24 h
          mm_carry_24h          mm_carry (the market maker's realised middle-band spread, value adds, takes)"""
        cfg = self.cfg
        now = util.time.time() if now is None else now
        out = dict.fromkeys(self.EV_KEYS)
        out["ev_outcome_scope"] = self.EV_SCOPE
        inv = {e: float(q) for e, q in (self.held or {}).items() if e in self.ex and round(q)}
        book_fv = {}
        for members in self.groups.values():
            if any(e in inv for e in members):
                fv = {e: fair_value(self.ex[e].book, cfg) for e in members if e in self.ex}
                book_fv.update(normalise(fv) if len(fv) > 1 else fv)
        at_ref, at_mark, unpriced, unmarked = 0.0, 0.0, 0, 0
        for e, q in inv.items():
            m = self.pos_marks.get(e)
            m = m if m is not None else book_fv.get(e)
            if m is not None:
                at_mark += self.alloc_held_usd(q, m)
            else:
                unmarked += 1
            p = self.ev_p(e)
            if p is None:
                unpriced += 1
                p = m
            if p is not None:
                at_ref += self.alloc_held_usd(q, p)
        out["ev_outcome_unpriced"] = unpriced
        cash = getattr(self, "cg_cash", None)
        if cash is None and self.health.get("account_value") is not None and not unmarked:
            cash = float(self.health["account_value"]) - at_mark
        if cash is not None:
            ev = round(float(cash) + at_ref, 2)
            out["ev_outcome"] = ev
            h = self.ev_hist
            if record and (not h or now - h[-1][0] >= self.EV_HIST_SECONDS):
                h.append([round(now, 1), ev])
            while h and h[0][0] < now - self.EV_HIST_KEEP:
                h.pop(0)
            old = [v for t, v in h if t <= now - 24 * 3600 + 1]     # (+1 s: samples are stored to 0.1 s)
            out["ev_outcome_delta_24h"] = round(ev - old[-1], 2) if old else None
        out["mm_carry_24h"] = self.mm_carry(now)
        return out

    def mm_carry(self, now):
        """mm_carry_24h: over the last 24 h of fills.csv (ops_fills rows, our_side bid / ask) classified with the
        order notes (order_meta, kept a day): take / arb (pair unwinds included) / alloc (set ladder included)
        orders are not maker fills; the rest are our resting quotes ("maker"; a fill whose note is gone
        counts as maker, see meta_missing). p = the market's race-scaled liquid Polymarket price when the fill was
        logged (ev_fill_p; fills logged before this run use the price now: p_now count). Maker fills with p in
        [value_mid_low, value_mid_high] are the middle band: their buys and sells are FIFO-matched per market in time
        order, realised = sum matched qty x (sell - buy); what stays unmatched is unmatched_shares, valued at p -
        price (unmatched_ev, not realised). value_adds_ev = tail maker fills' edge to p at fill (buy q x (p - price),
        sell q x (price - p)); takes_ev the same for take fills. per_day = realised x 24 / hours covered.
        P14: a fill of a side the recycler priced (note "recycle") counts as fills.recycle (not maker_mid) and its edge
        to p in recycle_ev - both keys only once such a fill exists; in the band it still closes the FIFO lots."""
        cfg = self.cfg
        c = self.ops_fills(now)
        notes = {str(k): v for k, v in (self.order_meta or {}).items()}
        lo, hi = cfg.value_mid_low, cfg.value_mid_high
        counts = dict.fromkeys(("maker_mid", "maker_tail", "maker_unpriced", "take", "arb", "alloc", "basket"), 0)
        book, realised, adds, takes = defaultdict(deque), 0.0, 0.0, 0.0
        p_fill = p_now = missing = take_unpriced = 0
        recycle_ev = None                         # P14: recycled fills' edge to p (None: none seen)
        for ts, fid, oid, e, buy, qty, price in sorted(c.get("rows") or (), key=lambda r: r[0]):
            if ts < now - 24 * 3600:
                continue
            meta = notes.get(oid)
            if meta is None:
                missing += 1
                meta = {}
            if meta.get("harvest"):               # P15: its own class (the key absent until such a fill exists)
                cls = "harvest"
                counts[cls] = counts.get(cls, 0)
            elif meta.get("alloc") or meta.get("set_ladder"):
                cls = "alloc"
            elif meta.get("arb"):
                cls = "arb"
            elif meta.get("take"):
                cls = "take"
            else:
                cls = "maker"
            if cls not in ("maker", "take"):
                counts[cls] += 1
                continue
            if fid in self.ev_fill_p:
                p = self.ev_fill_p[fid]
                p_fill += 1
            else:
                p = self.ev_p(e)
                p_now += 1
            edge = None if p is None else qty * ((p - price) if buy else (price - p))
            if cls == "maker" and meta.get("recycle"):   # P14 1: a recycled MM side (keys only once one exists);
                counts["recycle"] = counts.get("recycle", 0) + 1   # it still closes middle-band lots below
                recycle_ev = (recycle_ev or 0.0) + (edge or 0.0)
                if p is None or not lo <= p <= hi:
                    continue
            if cls == "take":
                counts["take"] += 1
                if edge is None:
                    take_unpriced += 1
                else:
                    takes += edge
                continue
            if p is None:
                counts["maker_unpriced"] += 1
            elif not lo <= p <= hi:
                counts["maker_tail"] += 1
                adds += edge
            else:
                if not meta.get("recycle"):
                    counts["maker_mid"] += 1
                lots, rem = book[e], qty if buy else -qty
                while abs(rem) > 1e-9 and lots and (lots[0][0] > 0) != (rem > 0):
                    lot = lots[0]
                    n = min(abs(rem), abs(lot[0]))
                    realised += n * (price - lot[1]) * (1 if lot[0] > 0 else -1)
                    lot[0] += n if lot[0] < 0 else -n
                    rem += n if rem < 0 else -n
                    if abs(lot[0]) <= 1e-9:
                        lots.popleft()
                if abs(rem) > 1e-9:
                    lots.append([rem, price, p])
        left = [x for v in book.values() for x in v]
        first = c.get("first_ts")
        hours = round(min(24.0, max(0.0, (now - first) / 3600)), 2) if first is not None else None
        return {"realised": round(realised, 2),
                "per_day": round(realised * 24 / hours, 2) if hours else None, "hours_covered": hours,
                "unmatched_shares": round(sum(abs(q) for q, _, _ in left), 2),
                "unmatched_ev": round(sum(q * (p - px) for q, px, p in left), 2),
                "value_adds_ev": round(adds, 2), "takes_ev": round(takes, 2), "fills": counts,
                "takes_unpriced": take_unpriced, "meta_missing": missing, "p_at_fill": p_fill, "p_now": p_now,
                "band": [lo, hi], **({"recycle_ev": round(recycle_ev, 2)} if recycle_ev is not None else {})}

    def ev_line_part(self):
        """" | EV outcome X (+Y 24h, N unpriced)" for the realtime / polling cycle line (the latest status write's
        figures), "" while unknown. Never raises."""
        try:
            part = ev_outcome_part(self.ops_last)
        except (TypeError, ValueError, AttributeError):
            part = None
        return f" | {part}" if part else ""

    def safe_ev_fields(self, record=False):
        """ev_fields that never raises: on any error every field is None (logged once)."""
        try:
            return self.ev_fields(record=record)
        except Exception as e:                    # reporting must never disturb trading
            if not self.ev_warned:
                self.ev_warned = True
                log.warning("ev fields unavailable (%s: %s) - reported as null", type(e).__name__, e)
            return dict.fromkeys(self.EV_KEYS)

    def safe_ops_fields(self):
        """ops_fields that never raises: on any error every field is None (logged once)."""
        try:
            return self.ops_fields()
        except Exception as e:                    # reporting must never disturb trading
            if not self.ops_warned:
                self.ops_warned = True
                log.warning("ops fields unavailable (%s: %s) - reported as null", type(e).__name__, e)
            return dict.fromkeys(self.OPS_KEYS)

    def write_status(self, ok):
        """status.json: a one-glance health check, e.g. `cat status.json` over ssh."""
        self.ops_last = {**self.safe_ops_fields(), **self.safe_ev_fields(record=True)}
        try:
            owed = ({"pair_owed": self.pair_owed_status()}
                    if getattr(self.cfg, "pair_unwind_followup", False) or getattr(self, "pair_owed", None) else {})
            write_json(bot_path(self.cfg.status_file), {
                "updated": iso(util.utcnow()), "mode": "live" if self.api.live else "dry run",
                "last_cycle_ok": ok, "failed_cycles_in_a_row": self.failed_cycles,
                "quotes_pulled_after_errors": self.pulled_after_errors, **self.health, **self.ops_last,
                "realised_pnl_scope": "maker fills only",   # arb / take fills (our_side '?') are not in realised_pnl
                **(self.api.pause_state() if hasattr(self.api, "pause_state") else {}),
                # T2.1: the tilt estimate (also restored from here at start) and the position's exposure to it
                "tilt_s": round(self.tilt_s, 4),
                "tilt_exposure": round(self.tilt_exposure),
                "tilt_state": self.tilt_state_dict(),
                "tilt_diag": {**getattr(self.tilt, "diag", {}),
                              "estimator": getattr(self.cfg, "ref_tilt_estimator", "slope")},
                # Package 7: NO+NO sets held (races, sets, capital at k - 1 per set) and the paired-unwind check
                "nono_sets": self.safe_nono_sets(), "pairno_state": getattr(self, "pairno_state", None),
                **owed,                                   # Package 7: pair unwind legs still owed {race: shares}
                # Package 10 B: the allocator (also what a restart restores; absent while never used)
                **({"alloc": self.alloc_status()} if self.alloc_persist_needed() else {}),
                # P14: market-making funding (read-only; MM_FUNDING_KEYS; also what a restart restores: the MM lots)
                "mm_funding": self.safe_mm_funding(),
                # P15: the harvest ladder, the state cap (HARVEST_KEYS; absent while unused)
                **({"harvest": self.hv_info} if getattr(self, "hv_info", None) else {}),
                **({"state_caps": self.st_status()} if self.st_on() else {}),
                # ev_outcome_delta_24h's ring of (wall, ev) samples (also what a restart restores)
                "ev_outcome_history": [list(x) for x in self.ev_hist],
                "seconds_since_cycle": round(util.time.monotonic() - self.last_cycle_done, 1)
                if self.last_cycle_done is not None else None})
        except OSError as e:
            log.warning("could not write status file: %s", e)

    def maybe_daily_analysis(self):
        """Optional daily phone message: the headline lines of `analyze` over the last 24 h (local files only)."""
        hour, now = self.cfg.analyze_daily_hour, util.utcnow()
        if hour < 0 or not self.api.live or now.hour != hour or self.last_analysis_day == now.date():
            return
        self.last_analysis_day = now.date()
        try:
            lines = analyze(bot_path(self.cfg.fills_csv), bot_path(self.cfg.record_file) if self.cfg.record_file else "",
                            hours=24, top=3)
            util.notify("\n".join(lines[:2] + [l[:60] for l in lines[3:]]), title="mm_bot daily analysis", tags="bar_chart")
        except Exception as e:                # a report must never disturb trading
            log.warning("daily analysis failed: %s", e)

    # ------------------------------------------------------------------------------ live settings
    def check_overrides(self, force=False):
        """Every overrides_seconds: re-read settings_override.json if it changed, apply what's valid, put back
        the default of anything removed, log every change and alert on anything refused."""
        cfg = self.cfg
        now_m = util.time.monotonic()
        if not cfg.overrides_file or (not force and now_m - self.last_overrides_check < cfg.overrides_seconds):
            return
        self.last_overrides_check = now_m
        path = bot_path(cfg.overrides_file)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime == self.overrides_mtime:
            return
        self.overrides_mtime = mtime
        raw = {}
        if mtime is not None:
            try:
                with open(path) as f:
                    raw = json.load(f)
            except (OSError, ValueError) as e:
                util.alert(f"{cfg.overrides_file} unreadable ({e}) - keeping the current settings")
                return
        good, bad = validate_overrides(raw, cfg)
        if bad:
            util.alert(f"{cfg.overrides_file}: ignored " + "; ".join(bad))
        wanted = {**{k: self.defaults[k] for k in self.overrides if k not in good}, **good}
        for k, v in wanted.items():
            if getattr(cfg, k) != v:
                log.warning("SETTING %s: %s -> %s%s", k, getattr(cfg, k), v, "" if k in good else " (default again)")
                setattr(cfg, k, v)
                if k == "requests_per_minute":
                    self.api.budget = min(getattr(self.api, "budget", v), v)
                if k == "writes_per_minute":          # the owner asked for this rate: start from it now
                    self.api.wbudget = float(v)
                if k == "writes_per_minute_max":      # a lower ceiling applies at once; a higher one is grown into
                    self.api.wbudget = min(getattr(self.api, "wbudget", v), max(v, cfg.writes_per_minute))
                if k in ("size_min_frac", "size_max_frac", "headline_size_frac", "quote_capital_frac"):
                    self.size_plan_time = -1e9                 # re-plan sizes now
        self.overrides = good
        self.health["overrides"] = dict(good)
        self.warn_settings()

    def warn_settings(self):
        """One-line warnings for setting combinations that work against each other (once per time they turn on)."""
        bad = bool(getattr(self.cfg, "alloc_enabled", False)) and not getattr(self.cfg, "cash_gate_enabled", False)
        if bad and not getattr(self, "warned_alloc_cash", False):   # Package 10 B
            log.warning("alloc_enabled is on without cash_gate_enabled: the allocator does nothing without the gate's "
                        "fresh cash read - turn cash_gate_enabled on")
        self.warned_alloc_cash = bad
        unknown = tuple(sorted(self.alloc_pins() - {x.label for x in self.ex.values()})) if self.ex else ()
        if unknown and unknown != getattr(self, "warned_alloc_pin", ()):   # (P10 red team RT-6: a typo pins nothing)
            log.warning("alloc_pin names no market: %s - those labels pin nothing (labels are matched exactly, e.g. "
                        "'Rep Ohio Senate')", ", ".join(unknown))
        self.warned_alloc_pin = unknown
        c = self.cfg                          # P14: a funding flag that cannot act with these settings
        bad = tuple(n for n, hit in (
            ("mm_refill_fast without alloc_enabled (the fast refill is the allocator's B2 refill)",
             getattr(c, "mm_refill_fast", False) and not getattr(c, "alloc_enabled", False)),
            # P14.1: a refill flag does nothing without the fast refill, the netting nothing without a reserve
            (f"{', '.join(k for k in P141_REFILL if getattr(c, k, False))} without mm_refill_fast (they change "
             "the fast refill)",
             any(getattr(c, k, False) for k in P141_REFILL) and not getattr(c, "mm_refill_fast", False)),
            ("alloc_swap_room_netting with mm_risk_reserve_wc and _corr both 0 (no pause to work through)",
             getattr(c, "alloc_swap_room_netting", False) and not (getattr(c, "mm_risk_reserve_wc", 0.0) > 0
                                                                   or getattr(c, "mm_risk_reserve_corr", 0.0) > 0)),
            ("alloc_refill_max_cost > 0 (refill sales may go below the value floor while cash is under half the "
             "target)", float(getattr(c, "alloc_refill_max_cost", 0.0) or 0.0) > 0),
            ("mm_room_guard with mm_risk_reserve_wc and _corr both 0 (no room is kept, nothing to guard)",
             getattr(c, "mm_room_guard", False) and not (getattr(c, "mm_risk_reserve_wc", 0.0) > 0
                                                         or getattr(c, "mm_risk_reserve_corr", 0.0) > 0))) if hit)
        if bad and bad != getattr(self, "warned_mmf", ()):
            log.warning("MM funding: %s", "; ".join(bad))
        self.warned_mmf = bad
        bad = self.p142_on() and float(getattr(c, "value_sell_margin", 0.0)) > 0.01   # P14.2: a raised margin left on
        if bad and not getattr(self, "warned_swap_margin", False):
            log.warning("WARNING alloc_swap_sell_margin %.3f is on while value_sell_margin is %.3f (> 0.01): every "
                        "resting reducing quote, the recycler and the refills may still sell that far below p - the "
                        "swaps no longer need it (they have their own floor): return value_sell_margin to 0.005",
                        c.alloc_swap_sell_margin, c.value_sell_margin)
        self.warned_swap_margin = bad
        on = ()                               # Package 10 A1 (iv): the mark-driven selling paths it once listed
        if getattr(self.cfg, "value_mode", False):   # (reduce_from_book, fast_unload_enabled) were removed on simplify
            c = self.cfg
            closing = [n for n in ("exit_hours_before_close", "flatten_hours_before_close", "flatten_per_market_hours")
                       if getattr(c, n) > 0]
            if closing and not self.warned_value:
                log.warning("value_mode: the pre-close windows are OFF (%s ignored; stop_minutes_before_close still "
                            "stops quoting before the close)", ", ".join(f"{n} {getattr(c, n):g}" for n in closing))
            on = on or ("(on)",)
        self.warned_value = on

    def check_market_edge(self, force=False):
        """Every market_edge_reload_seconds: re-read market_edge.json (rival-floor map) if it changed. Bad entries
        are refused one by one (a file with nothing usable is refused whole); an unreadable file keeps the current
        map; a removed file empties it."""
        cfg = self.cfg
        now_m = util.time.monotonic()
        if not cfg.market_edge_file or (not force and now_m - self.last_market_edge_check < cfg.market_edge_reload_seconds):
            return
        self.last_market_edge_check = now_m
        path = bot_path(cfg.market_edge_file)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime == self.market_edge_mtime:
            return
        self.market_edge_mtime = mtime
        if mtime is None:
            if self.market_edge:
                log.info("MARKET EDGE %s gone - every market back to min_edge", cfg.market_edge_file)
            self.market_edge = {}
            return
        try:
            with open(path) as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            log.info("MARKET EDGE %s refused (unreadable: %s) - keeping %d markets", cfg.market_edge_file, e,
                     len(self.market_edge))
            return
        good, bad = validate_market_edge(raw, self.ex)
        if bad and not good:
            log.info("MARKET EDGE %s refused: %s - keeping %d markets", cfg.market_edge_file, "; ".join(bad[:5]),
                     len(self.market_edge))
            return
        self.market_edge = good
        vals = sorted(good.values())
        log.info("MARKET EDGE %s loaded: %d markets%s%s%s", cfg.market_edge_file, len(good),
                 f", {100 * vals[0]:g}-{100 * vals[-1]:g}c" if vals else "",
                 "" if cfg.market_edge_enabled else " (market_edge_enabled is off: not used)",
                 f"; refused {len(bad)}: " + "; ".join(bad[:5]) if bad else "")
