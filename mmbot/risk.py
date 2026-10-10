"""
RiskMixin: the portfolio-level limits every other part must respect.

Owns account value and the drawdown kill switch, the inventory lots and their age, the capital
ceiling and its adding-side factor, the mark-noise estimator, risk_fv / settlement_risk /
total_worst_case (the worst-case loss across races), the MM risk room and the "value adds paused"
flag, turnover health, the cash gate (cash_*, tier_*: what this cycle's orders may spend) and the
per-state collateral caps (st_*).

It decides what is allowed, not what to do: it never prices a quote, never chooses a market and
never sends an order. Other modules read its flags and caps and shrink to fit. Methods only: all
state lives on the Bot instance (self); no __init__ here. Never imports bot.py.
"""
import csv
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mmbot import util
from mmbot import config
from mmbot import exchange
from mmbot import pricing
from mmbot import quoting
from mmbot import measure
from mmbot.util import EXIT_KILLED, TICK, bot_path, iso, log, parse_ts, write_json
from mmbot.config import MM_RISK_HYST
from mmbot.exchange import ApiError, state_of
from mmbot.pricing import _num, race_variance, reserved_cash, worst_case_loss
from mmbot.quoting import mark_step_sd


class RiskMixin:

    # ------------------------------------------------------------------------------ risk
    def account_value(self, pos, f_pnl):
        """Cash + holdings at current prices, straight from the API's P&L endpoint (f_pnl is that
        request, already running in parallel). Falls back to balance + position value if it
        failed. None if both fail."""
        try:
            return float(f_pnl.result()["totalAccountValue"])
        except (ApiError, KeyError, TypeError, ValueError) as e:
            log.warning("P&L endpoint failed (%s) - using balance + positions", e)
        try:
            return float(self.api.tournament()["myBalance"]) + float(pos["summary"]["totalMarketValue"])
        except (ApiError, KeyError, TypeError, ValueError) as e:
            log.warning("account value unavailable (%s) - kill switch skipped this cycle", e)
            return None

    def checked_account_value(self, equity, reserved, inv):
        """Account value for the kill switch, corrected for cash locked in our open orders.

        Problem: if the API's account value leaves out cash reserved by resting orders, then simply
        posting ~350 quotes would look like a ~17k loss and trip the kill switch with nothing lost.
        With reserved_cash_mode="auto" we find out from the data: on a cycle where our positions
        didn't change (no fills) but the locked cash did, a real loss is impossible, so
          - account value fell by about the change in locked cash  -> it's left out -> add it back
          - account value stayed put                               -> it's included -> leave it
        While still undecided we add it back, so a false alarm can't stop the bot. That makes the
        switch a little late only until the first few batches of orders settle the question.
        """
        if equity is None:
            return None
        if self.reserved_mode is None:
            self.detect_reserved_mode(equity, reserved, inv)
        return equity if self.reserved_mode == "ignore" else equity + reserved

    def detect_reserved_mode(self, equity, reserved, inv):
        """One auto-detection step (see checked_account_value). Needs reserved_calib_votes agreeing
        observations in a row, because the API's account value may lag a moment behind new orders."""
        cfg = self.cfg
        positions = {k: round(v) for k, v in inv.items() if round(v)}
        prev, self.calib_prev = self.calib_prev, (equity, reserved, positions)
        if prev is None:
            return
        v0, r0, positions0 = prev
        dv, dr = equity - v0, reserved - r0
        if positions != positions0 or abs(dr) < cfg.reserved_calib_min:
            return                                            # fills happened, or too small a change to judge
        if abs(dv + dr) < 0.2 * abs(dr):
            verdict = "add"                                   # value moved opposite to locked cash: left out
        elif abs(dv) < 0.2 * abs(dr):
            verdict = "ignore"                                # value didn't move: already included
        else:
            self.calib_votes = []                             # unclear (e.g. prices moved): start over
            return
        self.calib_votes = (self.calib_votes + [verdict])[-cfg.reserved_calib_votes:]
        if len(self.calib_votes) == cfg.reserved_calib_votes and len(set(self.calib_votes)) == 1:
            self.reserved_mode = verdict
            log.warning("kill switch: the API's account value %s cash locked in open orders -> %s. "
                        "(Set reserved_cash_mode=\"%s\" in SETTINGS to skip this check next time.)",
                        "LEAVES OUT" if verdict == "add" else "already includes",
                        "adding it back" if verdict == "add" else "using it as is", verdict)

    def kill_switch(self, equity):
        """Stop the bot if the account is max_drawdown_pct below the tournament's INITIAL balance.
        The initial balance (not the value at start-up) is used so restarting the bot doesn't reset
        the limit. It needs kill_confirmations bad readings in a row, so one glitchy number can't
        shut us down."""
        if equity is None or not self.initial_balance:
            return False
        floor = self.initial_balance * (1 - self.cfg.max_drawdown_pct)
        self.kill_breaches = self.kill_breaches + 1 if equity < floor else 0
        if self.kill_breaches >= self.cfg.kill_confirmations:
            msg = f"KILL SWITCH: account value {equity:.0f} < {floor:.0f} - cancelling everything and stopping"
            log.critical(msg)
            # Leave a marker file so neither systemd nor a quick manual restart can resume trading.
            with open(bot_path(self.cfg.kill_file), "w") as f:
                f.write(f"{iso(util.utcnow())}  {msg}\nDelete this file to allow the bot to trade again.\n")
            util.alert(msg)
            self.running, self.exit_code = False, EXIT_KILLED
            return True
        return False

    def effective_inventory(self, inv):
        """Inventory after netting the other parties in the same race.

        Holding YES on every party in a race is (nearly) risk-free, because one of them pays 1.
        What matters is how far each position sits from the average of the others:
            eff_i = x_i - mean(x_j for the other parties j)
        Two-party race, long 500 Republican: eff_R = +500 and eff_D = -500, so the bot becomes keen
        to buy Democrat YES, which is the hedge. After buying 500 Democrat YES, both are 0.
        """
        eff = {}
        for members in self.groups.values():
            for e in members:
                others = [inv.get(o, 0.0) for o in members if o != e]
                eff[e] = inv.get(e, 0.0) - (sum(others) / len(others) if others else 0.0)
        return eff

    # ------------------------------------------------------------------ position age (age skew)
    def load_lots(self):
        """Position lots saved by a previous run: {eid: [[signed shares, epoch time], ...]} ({} if none)."""
        if not self.cfg.position_lots_file:
            return {}
        try:
            with open(bot_path(self.cfg.position_lots_file)) as f:
                raw = json.load(f)
            return {str(e): [[float(q), float(t)] for q, t in v] for e, v in raw.items() if v}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def seed_lots(self, inv, now):
        """Lots for positions we have no record of, rebuilt from fills.csv: under FIFO what is still held is
        the LATEST fills in the position's direction, so walk back from the newest until they add up to it.
        Shares not covered (no fills logged) count as bought now."""
        need = {e: q for e, q in inv.items() if round(q) and e not in self.lots}
        if not need:
            return
        found = defaultdict(list)                       # eid -> [(shares, time)] newest first
        try:
            rows = measure.read_fills(bot_path(self.cfg.fills_csv))
        except (OSError, ValueError, csv.Error):
            rows = []
        for r in reversed(rows):
            e, side = str(r.get("exchange_id")), r.get("our_side")
            if e not in need or side not in ("bid", "ask") or (side == "bid") != (need[e] > 0):
                continue
            left = abs(need[e]) - sum(x for x, _ in found[e])
            ts = parse_ts(r.get("filled_at"))
            if left <= 0 or ts is None:
                continue
            try:
                qty = abs(float(r.get("qty") or 0))
            except ValueError:
                continue
            found[e].append((min(left, qty), ts.timestamp()))
        for e, q in need.items():
            sign = 1.0 if q > 0 else -1.0
            got = [[sign * x, t] for x, t in reversed(found.get(e, [])) if x > 0]
            rest = abs(q) - sum(abs(x) for x, _ in got)
            if rest > 0.5:
                got.append([sign * rest, now])
            self.lots[e] = got

    def update_lots(self, inv, now):
        """Keep each market's FIFO lots in step with its position. The positions read is the truth (fills only
        ever reach it), so a cycle's net change is that cycle's fills netted: growing -> a new lot stamped now;
        shrinking -> the OLDEST lots go first; changing sign -> one fresh lot. Saved to position_lots_file (live)."""
        if not self.lots_seeded:
            self.lots_seeded, self.lots_dirty = True, True
            self.seed_lots(inv, now)
        changed = False
        for e in set(self.lots) | {e for e, q in inv.items() if round(q)}:
            q = round(inv.get(e, 0.0))
            lots = self.lots.get(e, [])
            held = round(sum(x for x, _ in lots))
            if q == held:
                continue
            changed = True
            if not q:
                self.lots.pop(e, None)
            elif not held or (q > 0) != (held > 0):
                self.lots[e] = [[float(q), now]]
            elif abs(q) > abs(held):
                self.lots[e] = lots + [[float(q - held), now]]
            else:
                drop = abs(held) - abs(q)               # sold: oldest lots first
                out = []
                for x, t in lots:
                    take = min(abs(x), drop)
                    drop -= take
                    if abs(x) - take > 1e-9:
                        out.append([x - take if x > 0 else x + take, t])
                self.lots[e] = out
        if changed or self.lots_dirty:
            self.lots_dirty = False
            if self.api.live and self.cfg.position_lots_file:
                try:
                    write_json(bot_path(self.cfg.position_lots_file), self.lots)
                except OSError as err:
                    log.warning("could not save position lots: %s", err)

    def age_hours(self, ex, now=None):
        """Share-weighted age of this market's position in hours (0 = flat or unknown)."""
        lots = self.lots.get(ex.eid)
        total = sum(abs(x) for x, _ in lots) if lots else 0.0
        if not total:
            return 0.0
        now = util.time.time() if now is None else now
        return sum(abs(x) * (now - t) for x, t in lots) / total / 3600

    def portfolio_age(self, now):
        """(share-weighted age of every held share in hours, positions older than 3 h, older than 12 h);
        a position's age is its own share-weighted age."""
        shares = weighted = 0.0
        over3 = over12 = 0
        for lots in self.lots.values():
            n = sum(abs(x) for x, _ in lots)
            if not n:
                continue
            age = sum(abs(x) * (now - t) for x, t in lots) / n / 3600
            shares, weighted = shares + n, weighted + n * age
            over3, over12 = over3 + (age > 3), over12 + (age > 12)
        return (weighted / shares if shares else 0.0), over3, over12

    # ------------------------------------------------------------------ capital ceiling
    def capital_in_positions(self, pos, inv, fvs):
        """Cash tied up in positions: the positions read's totalMarketValue when it gives one (the exchange's
        own valuation), else our sum of |shares| x price at fair value (a short YES = NO shares, 1 - price each)."""
        try:
            v = float(((pos or {}).get("summary") or {}).get("totalMarketValue") or 0)
        except (TypeError, ValueError, AttributeError):
            v = 0.0
        if v > 0:
            return v
        total = 0.0
        for e, q in inv.items():
            if not q:
                continue
            p = self.risk_fv(e, fvs) if e in self.ex else (fvs.get(e) or 0.5)
            total += abs(q) * (p if q > 0 else 1 - p)
        return total

    MARK_SD_REFRESH_SECONDS = 1800.0

    def refresh_mark_sd(self, now_m, force=False):
        """Mark-fragility estimator, every 30 min (and at the first cycle): per market, the sd of the 10-min change
        of the tournament mid over the last mark_frag_window_hours of the recorder's snapshots (one SQL). Main
        thread, like the recorder's writes. No recorder, or too few samples -> no estimate for that market."""
        if not self.db or (not force and now_m - self.mark_sd_time < self.MARK_SD_REFRESH_SECONDS):
            return
        self.mark_sd_time = now_m
        cfg = self.cfg
        cutoff = iso(util.utcnow() - timedelta(hours=cfg.mark_frag_window_hours))
        try:
            rows = self.db.execute(
                "SELECT eid, ts, best_bid, best_ask FROM snapshots WHERE mode = ? AND ts >= ? "
                "AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY eid, ts",
                ("live" if self.api.live else "dry", cutoff)).fetchall()
        except sqlite3.Error as e:
            log.warning("mark fragility: could not read snapshots (%s) - keeping the previous estimate", e)
            return
        secs, series = {}, defaultdict(list)
        for eid, ts, bb, ba in rows:
            t = secs.get(ts)
            if t is None:
                try:
                    t = secs[ts] = parse_ts(ts).timestamp()
                except (TypeError, ValueError):
                    continue
            series[str(eid)].append((t, (float(bb) + float(ba)) / 2))
        est = {}
        for eid, ser in series.items():
            sd = mark_step_sd(ser, cfg.mark_frag_min_samples, cfg.mark_frag_floor_sd)
            if sd is not None:
                est[eid] = sd
        self.mark_sd = est
        log.info("mark fragility: sd of the 10-min mid step for %d of %d markets (%d snapshot rows, %g h)",
                 len(est), len(series), len(rows), cfg.mark_frag_window_hours)

    def update_mark_frag(self, inv, cfg):
        """Sum over positions of |pos| x sd (mark noise in $ per 10-min step) and the status.json fields
        (health mark_frag_*). A diagnostic only: nothing sizes from it."""
        steps = {e: abs(q) * self.mark_sd[e] for e, q in inv.items() if q and e in self.mark_sd}
        total = sum(steps.values())
        top = sorted(steps.items(), key=lambda kv: -kv[1])[:10]
        return {"mark_frag_total_cash": round(total, 2),
                "mark_frag_top": {(self.ex[e].label if e in self.ex else e): round(c, 2) for e, c in top},
                "mark_frag_capped_markets": sum(1 for c in steps.values() if c >= cfg.mark_frag_max_step_cash),
                "mark_frag_estimates": len(self.mark_sd)}

    def update_capital_ceiling(self, frac, cfg):
        """Capital ceiling on above capital_in_positions_max_frac, off again below it - 0.05; each change logged once.
        An unknown account value keeps the current state."""
        cap = cfg.capital_in_positions_max_frac
        if cap <= 0:
            on = False
        elif frac is None:
            on = self.capital_over
        elif self.capital_over:
            on = frac >= cap - 0.05
        else:
            on = frac > cap
        if on != self.capital_over:
            log.warning("%s capital ceiling: %s of account value in positions (ceiling %.0f%%) - adding sides %s",
                        "ENTERING" if on else "leaving", f"{100 * frac:.0f}%" if frac is not None else "?",
                        100 * cap, f"at x{cfg.capital_ceiling_adding_size_factor:g}" if on else "back to normal")
        self.capital_over = on

    def update_adding_resume(self, frac, cfg):
        """adding_factor_capital_on (> 0): with the capital ceiling on, capital in positions / account
        below it -> the resume factor is in force (adding_resume); off again at >= it + 0.01, or when the ceiling
        or the setting is off. An unknown account value keeps the current state. Each change logged once."""
        thr = cfg.adding_factor_capital_on
        if thr <= 0 or not self.capital_over:
            on = False
        elif frac is None:
            on = self.adding_resume
        elif self.adding_resume:
            on = frac < thr + 0.01
        else:
            on = frac < thr
        if on != self.adding_resume:
            log.warning("capital ceiling adding factor: %s in positions (resume below %.0f%%) - adding sides at x%g",
                        f"{100 * frac:.1f}%" if frac is not None else "?", 100 * thr,
                        max(cfg.capital_ceiling_adding_size_factor, cfg.capital_ceiling_adding_size_factor_resume)
                        if on else cfg.capital_ceiling_adding_size_factor)
        self.adding_resume = on

    def ceiling_adding_factor(self, cfg):
        """The capital ceiling's adding-side factor in force: 1 (ceiling off), the configured factor, or (with
        adding_factor_capital_on) the larger of it and capital_ceiling_adding_size_factor_resume."""
        if not self.capital_over:
            return 1.0
        if self.adding_resume:
            return max(cfg.capital_ceiling_adding_size_factor, cfg.capital_ceiling_adding_size_factor_resume)
        return cfg.capital_ceiling_adding_size_factor

    def risk_fv(self, e, fvs, members=None):
        """The probability the risk model uses for market e: this cycle's fair value when there is one; else, for
        a market we hold, in this order: the liquid Polymarket reference; 1 minus the other leg's fair value in a
        two-leg race; the exchange's own mark of the position (currentPrice); the last fair value we had; 0.5.
        (2 Oct 11:22: Rep U.S. House, short 9,396, was unpriced after a restart and the old `or 0.5` treated it as
        a coin flip: settlement risk 20.6k -> 31k, reduce-only, no quotes there.) The fallback used for a held
        position is logged once per market and source."""
        p = fvs.get(e)
        if p is not None:
            return p
        src = None
        ref = self.cur_refs.get(e)
        if ref is not None and e in self.cur_liquid:
            p, src = ref, f"Polymarket {ref:.3f}"
        else:
            others = [o for o in (members or self.groups.get(self.ex[e].group, ())) if o != e and fvs.get(o) is not None]
            if len(others) == 1 and len(members or self.groups.get(self.ex[e].group, ())) == 2:
                p, src = max(0.0, min(1.0, 1 - fvs[others[0]])), f"1 - {self.ex[others[0]].label} {fvs[others[0]]:.3f}"
            elif self.pos_marks.get(e) is not None:
                p, src = self.pos_marks[e], f"exchange mark {self.pos_marks[e]:.4f}"
            elif self.ex[e].last_fv is not None:
                p, src = self.ex[e].last_fv, f"last fair value {self.ex[e].last_fv:.3f}"
            else:
                p, src = 0.5, "0.5 (nothing better)"
        if self.fv_fallback_logged.get(e) != src:
            self.fv_fallback_logged[e] = src
            log.warning("%s unpriced: risk uses %s for its %+.0f-share position", self.ex[e].label, src,
                        self.ex[e].inv)
        return p

    def risk_legs(self, inv, fvs, members):
        """[(net YES shares, probability)] for one race's risk measures: held legs at risk_fv (as before); unheld legs at
        their fair value / last fair value, else (risk_unheld_legs "half") 0.5 or ("ref") the raw Polymarket reference,
        else the race's residual probability shared among the unpriced legs, else 0.5."""
        mode = getattr(self.cfg, "risk_unheld_legs", "half")
        out, missing = [], []
        for e in members:
            q = inv.get(e, 0.0)
            if q:
                out.append((q, self.risk_fv(e, fvs, members)))
                continue
            p = fvs.get(e) or self.ex[e].last_fv
            if p is None and mode == "ref":
                r = (self.cur_refs or {}).get(e) if getattr(self, "cur_refs", None) else None
                p = float(r) if r is not None else None
            if p is None:
                missing.append(len(out))
                out.append((0.0, 0.5))
            else:
                out.append((0.0, p))
        if missing and mode == "ref" and len(out) > 1:
            known = sum(p for i, (_, p) in enumerate(out) if i not in missing)
            share = max(0.0, 1.0 - known) / len(missing)
            for i in missing:
                out[i] = (0.0, share)
        return out

    def settlement_risk(self, inv, fvs, party_delta):
        """National swing shock (risk_swing_shock x |net Rep-minus-Dem YES shares|) plus risk_z standard
        deviations of the settlement value of every race, races independent once the swing is taken out.
        The cycle uses min(this, sum of per-race maxima)."""
        stress = 0.0
        var = 0.0
        for members in self.groups.values():
            legs = self.risk_legs(inv, fvs, members)
            if any(x for x, _ in legs):
                var += race_variance(legs)
        return self.cfg.risk_swing_shock * abs(party_delta) + self.cfg.risk_z * math.sqrt(var) + stress

    def mm_risk_room_update(self, worst, risk, equity, mm_part=None):
        """mm_risk_reserve_* (cycle step 6, after the reduce-only decision, which it never touches): this
        cycle's rooms (room_wc = worst_case_backstop_frac x account - worst, room_corr = max_worst_case_frac x account -
        risk) and the "value adds paused" state with its hysteresis (pause below a reserve, resume once each reserve
        set is covered MM_RISK_HYST times). No account value: the rooms are unknown and the state is kept. Returns
        self.mmr_paused. mm_room_guard: mm_part = the MM lots' (worst-case, correlated) contribution, counted
        against the MM room first: the pause is decided on the value book's share, room + min(contribution, reserve)."""
        cfg = self.cfg
        res_wc, res_corr = max(0.0, cfg.mm_risk_reserve_wc), max(0.0, cfg.mm_risk_reserve_corr)
        if not equity:
            self.mmr_room = (None, None)
            return self.mmr_paused
        room_wc = cfg.worst_case_backstop_frac * equity - worst
        room_corr = cfg.max_worst_case_frac * equity - risk
        self.mmr_room = (room_wc, room_corr)
        v_wc, v_corr, guard = room_wc, room_corr, ""
        if mm_part is not None:                   # The value book's share of each room
            v_wc, v_corr = room_wc + min(mm_part[0], res_wc), room_corr + min(mm_part[1], res_corr)
            self.mmf_room_mm, self.mmf_room_value = tuple(mm_part), (v_wc, v_corr)
            guard = (f" [mm_room_guard: MM inventory {mm_part[0]:.0f} wc / {mm_part[1]:.0f} corr counted against "
                     f"the MM room first; value share {v_wc:.0f} / {v_corr:.0f}]")
        short = ((res_wc > 0 and v_wc < res_wc - 1e-9) or (res_corr > 0 and v_corr < res_corr - 1e-9))
        clear = ((res_wc <= 0 or v_wc >= MM_RISK_HYST * res_wc - 1e-9)
                 and (res_corr <= 0 or v_corr >= MM_RISK_HYST * res_corr - 1e-9))
        paused = short or (self.mmr_paused and not clear)
        if paused != self.mmr_paused:
            log.warning("%s: risk room worst case %.0f (reserve %.0f), correlated %.0f (reserve %.0f), account "
                        "%.0f%s%s",
                        "VALUE ADDS PAUSED (mm_risk_reserve: takes, allocator buys and tail adds stop; "
                        "middle-band two-way quoting goes on)" if paused else "value adds resumed (mm_risk_reserve)",
                        room_wc, res_wc, room_corr, res_corr, equity,
                        "" if paused or res_wc > 0 or res_corr > 0 else " - setting off", guard)
            self.mmr_since = util.time.time() if paused else None
        self.mmr_paused = paused
        return paused

    def mm_risk_status(self):
        """Status.json mm_risk_room (None while both settings are 0 and it never paused: the key absent)."""
        cfg = self.cfg
        if not (cfg.mm_risk_reserve_wc > 0 or cfg.mm_risk_reserve_corr > 0 or self.mmr_paused or self.mmr_since):
            return None
        rw, rc = self.mmr_room
        return {"room_wc": None if rw is None else round(rw, 2), "room_corr": None if rc is None else round(rc, 2),
                "paused": self.mmr_paused,
                "since": (datetime.fromtimestamp(self.mmr_since, timezone.utc).isoformat(timespec="seconds")
                          if self.mmr_since else None),
                "reserve_wc": cfg.mm_risk_reserve_wc, "reserve_corr": cfg.mm_risk_reserve_corr,
                "blocked": {**self.mmr_blocked, "tail_quotes": self.mmr_tail_now},
                **({"value_share_wc": None if self.mmf_room_value[0] is None else round(self.mmf_room_value[0], 2),
                    "value_share_corr": None if self.mmf_room_value[1] is None else round(self.mmf_room_value[1], 2)}
                   if getattr(cfg, "mm_room_guard", False) else {})}   # (absent while off)

    def mm_risk_count(self, path, n=1):
        """Count n value adds held back on path (takes / alloc) for status.json mm_risk_room."""
        if n:
            blocked = self.__dict__.setdefault("mmr_blocked", {"takes": 0, "alloc": 0, "basket": 0, "tail_quotes": 0})
            blocked[path] = blocked.get(path, 0) + n

    def mm_risk_summary(self):
        """"risk room wc Xk corr Yk (paused)" for the 2-hourly summary while a setting is on; "" otherwise."""
        cfg = self.cfg
        if not (cfg.mm_risk_reserve_wc > 0 or cfg.mm_risk_reserve_corr > 0):
            return ""
        rw, rc = self.mmr_room
        k = lambda x: "?" if x is None else f"{x / 1000:.1f}k"   # noqa: E731
        return f"risk room wc {k(rw)} corr {k(rc)}" + (" (paused)" if self.mmr_paused else "")

    def mm_tail_adds_off(self, ex, fv, ref, ref_liquid):
        """True while value adds are paused and this market is in a TAIL: its liquid race-scaled p (value_p),
        else its fair value, outside [value_mid_low, value_mid_high]. The middle band keeps two-way quoting."""
        if not self.mmr_paused:
            return False
        p = self.value_p(ex, ref, ref_liquid)
        p = fv if p is None else p
        return p is not None and not (self.cfg.value_mid_low <= p <= self.cfg.value_mid_high)

    def total_worst_case(self, inv, fvs):
        """Sum over races of the worst-case settlement loss (see worst_case_loss)."""
        total = 0.0
        for members in self.groups.values():
            legs = self.risk_legs(inv, fvs, members)
            if any(x for x, _ in legs):
                total += worst_case_loss(legs)
        return total

    # ------------------------------------------------------------------ turnover control
    def seed_turnover(self):
        """At start: the last hours of fills.csv and, if the recorder's file exists, its trades table, so a
        restart neither forgets the flow nor judges markets on minutes of it."""
        now = util.time.time()
        try:
            n_f = self.turnover.seed_fills(measure.read_fills(bot_path(self.cfg.fills_csv)), now)
        except (OSError, csv.Error) as e:
            log.warning("turnover: could not read fills.csv: %s", e)
            n_f = 0
        n_t = self.turnover.seed_tape(bot_path(self.cfg.record_file) if self.cfg.record_file else "", now)
        if n_f or n_t:
            log.info("turnover: seeded %d fills and %d tape trades, %.1fh of the %gh window observed", n_f, n_t,
                     self.turnover.observed_hours(now, self.cfg.turnover_window_hours), self.cfg.turnover_window_hours)

    def note_turnover(self, new):
        """Our new fills (log_fills) and the trades the realtime feed reported since the last call."""
        wall = util.time.time()
        for f in new or ():
            try:
                t = parse_ts(f.get("filledAt"))
            except (TypeError, ValueError):
                t = None
            self.turnover.add(f.get("exchangeId"), min(t.timestamp(), wall) if t is not None else wall,
                              f.get("quantity"))
        if self.feed and hasattr(self.feed, "take_flow"):
            for t, eid, q in self.feed.take_flow():
                self.turnover.add(eid, t, q, tape=True)

    def refresh_turnover(self, now_m, force=False):
        """Each market's observed flow (shares/h over turnover_window_hours), at most once a minute."""
        if not force and now_m - self.turnover_refreshed < 60.0:
            return
        self.turnover_refreshed, now, cfg = now_m, util.time.time(), self.cfg
        self.turnover.prune(now)
        self.turnover_flow = {e: self.turnover.per_hour(e, now, cfg.turnover_window_hours, cfg.turnover_use_tape)
                              for e in self.ex}
        # Hysteresis: dead below turnover_min_shares_per_hour, alive again only above turnover_alive_shares_per_hour,
        # and no flip before turnover_min_state_minutes in the current state (a market's first verdict is at once).
        hold = 60 * cfg.turnover_min_state_minutes
        for e, flow in self.turnover_flow.items():
            st = self.turnover_state.get(e)
            if flow is None:
                self.turnover_state.pop(e, None)          # not judged (yet): alive, no state kept
                continue
            dead = st[0] if st else False
            want = (flow < cfg.turnover_min_shares_per_hour if not dead
                    else flow <= max(cfg.turnover_alive_shares_per_hour, cfg.turnover_min_shares_per_hour))
            if st is None:
                self.turnover_state[e] = (want, now_m)
            elif want != dead and now_m - st[1] >= hold:
                self.turnover_state[e] = (want, now_m)

    def turnover_dead(self, ex, size, cfg):
        """A dead market where we hold a position: dead per refresh_turnover (flow, hysteresis; once judged)
        and |race-netted position| at least min(one quote, 100 shares)."""
        st = self.turnover_state.get(ex.eid)
        if not st or not st[0]:
            return False
        return abs(ex.eff) >= max(1.0, min(size or 0.0, 100.0))

    def turnover_health(self, inv, fvs):
        """status.json: dead markets holding positions, the capital in them (at fair value) and the biggest."""
        rows = []
        for e, x in self.ex.items():
            q = inv.get(e, 0.0)
            if not x.turnover_dead or not q:
                continue
            p = fvs.get(e) or x.last_fv or 0.5
            rows.append((abs(q) * (p if q > 0 else 1 - p), x.label, self.turnover_flow.get(e)))
        rows.sort(reverse=True)
        return {"turnover_dead_markets": len(rows), "turnover_dead_capital": round(sum(r[0] for r in rows)),
                "turnover_dead_top": {lab: [round(c), round(f or 0.0, 1)] for c, lab, f in rows[:8]},
                "turnover_judged": self.turnover.judged(util.time.time(), self.cfg.turnover_window_hours)}

    # --- Cash gate (cash_gate_enabled) ---
    CASH_KEYS_NET = ("availableBalance", "availableCash", "availableFunds", "available")   # already net of locks
    CASH_KEYS = ("cashBalance", "cash", "balance", "myBalance")                            # locks still in them
    CASH_LOG_SECONDS = 600.0
    CASH_STALE_SECONDS = 300.0    # no good cash read for this long (since the last one, or the gate's first cycle):
    #                               stop gating (logged once) rather than gate every order on a stale / missing figure

    def cash_gate_on(self):
        if not (bool(getattr(self.cfg, "cash_gate_enabled", False)) and self.api.live):
            return False
        age = self.cash_read_age()
        if age is not None and age > self.CASH_STALE_SECONDS:
            if not getattr(self, "cg_stale_logged", False):
                self.cg_stale_logged = True
                log.warning("cash gate: no good cash read for %.0f s (> %.0f s) - NOT gating until one is read",
                            age, self.CASH_STALE_SECONDS)
            return False
        return True

    def cash_read_age(self):
        """Seconds since the last good cash read (or since the gate's first cycle, before any), None before that."""
        t = getattr(self, "cg_read_at", None)
        return None if t is None else max(0.0, util.time.monotonic() - t)

    def cash_gate_log(self, eid, msg, *args):
        """A "cash gated" info log on the take / follow-up paths, at most once per market per
        CASH_LOG_SECONDS (as the quote path)."""
        now_m, seen = util.time.monotonic(), self.__dict__.setdefault("cg_logged_take", {})
        if now_m - seen.get(eid, -1e18) >= self.CASH_LOG_SECONDS:
            seen[eid] = now_m
            log.info(msg, *args)

    def cash_figure(self, reply, pos):
        """(cash, already_net) from the P&L reply: a known "available" field (net of what our orders lock), else a
        known cash / balance field, else account value - the positions' market value; (None, False) if none."""
        if isinstance(reply, dict):
            for keys, net in ((self.CASH_KEYS_NET, True), (self.CASH_KEYS, False)):
                for k in keys:
                    v = reply.get(k)
                    if not isinstance(v, bool) and _num(v) is not None:
                        return _num(v), net
            acct = _num(reply.get("totalAccountValue"))
            mv = _num(((pos or {}).get("summary") or {}).get("totalMarketValue")) if isinstance(pos, dict) else None
            if acct is not None and mv is not None:
                return acct - mv, False
        return None, False

    def cash_gate_cycle(self, f_pnl, pos, raw_orders):
        """Once a cycle (live, gate on): on a cycle that read the P&L, take the cash figure and the cash our resting
        orders locked at that read (from the same cycle's open-orders read); what the bot sends from then on is
        counted in cg_spent (writes still in flight at the read count as sent). Then the plan's budget.
        A failed read / one without a cash figure alerts once (again after a good read); cg_read_at is the last good
        read (the first cycle's time before any), see cash_gate_on for a read older than CASH_STALE_SECONDS."""
        if getattr(self, "cg_read_at", None) is None:
            self.cg_read_at = util.time.monotonic()
        if f_pnl is not None:
            try:
                reply = f_pnl.result()
            except Exception:
                reply = None
            cash, net = self.cash_figure(reply, pos)
            if cash is not None:
                self.cg_cash = cash
                self.cg_reserved = 0.0 if net else reserved_cash(raw_orders or [])
                self.cg_spent = sum(getattr(w, "cash_need", 0.0) for w in getattr(self, "writes", []))
                self.cg_read_at = util.time.monotonic()
                if getattr(self, "cg_warned", False) or getattr(self, "cg_stale_logged", False):
                    log.info("cash gate: cash figure read again (%.2f) - gating as normal", cash)
                self.cg_warned = self.cg_stale_logged = False
            elif not getattr(self, "cg_warned", False):
                self.cg_warned = True
                util.alert("cash gate: the P&L read failed or has no cash figure - only cash-free orders go out until one "
                      f"is read (gating stops after {self.CASH_STALE_SECONDS:.0f} s without one)")
        self.cg_plan_left = self.cash_left()

    def cash_left(self):
        """Cash the gate may still spend now (>= 0); 0 while no cash figure has been read."""
        cash = getattr(self, "cg_cash", None)
        if cash is None:
            return 0.0
        return max(0.0, cash - getattr(self, "cg_reserved", 0.0) - getattr(self, "cg_spent", 0.0)
                   - self.cfg.cash_gate_reserve)

    def resting_lock(self, o):
        """Cash a resting order of ours (YES terms) locks: a bid its price a share (a covered "sell NO": 0); an ask
        1 - price a share for the part beyond the YES held (that part is a NO purchase)."""
        if o.is_bid:
            return 0.0 if (self.order_meta.get(o.order_id) or {}).get("no_sell") else o.price * o.qty
        ex = self.ex.get(o.eid)
        held = max(0.0, ex.inv) if ex is not None else 0.0
        return (1 - o.price) * max(0.0, o.qty - held)

    def cash_credit(self, orders):
        """A confirmed cancel gives the cash those orders locked back to the gate (until the next cash read)."""
        if self.cash_gate_on() and getattr(self, "cg_cash", None) is not None:
            self.cg_spent = getattr(self, "cg_spent", 0.0) - sum(self.resting_lock(o) for o in orders)

    def cash_free(self, eid, skip=lambda o: False):
        """{"yes", "lone", "set"}: YES free to sell on eid, and the NO free to sell as covered sales, split into its
        lone part and the part locked in NO+NO sets - each less what our resting orders there already sell
        (except those `skip` says are being replaced)."""
        ex = self.ex.get(eid)
        inv = ex.inv if ex is not None else 0.0
        rest = [o for o in list(self.my_orders.values()) if o.eid == eid and not skip(o)]
        yes = max(0.0, inv) - sum(o.qty for o in rest if not o.is_bid)
        no = max(0.0, -inv) - sum(o.qty for o in rest
                                  if o.is_bid and (self.order_meta.get(o.order_id) or {}).get("no_sell"))
        sets = min(max(0.0, no), self.nono_set_part(eid, inv)) if ex is not None and inv <= -1 else 0.0
        return {"yes": max(0.0, yes), "lone": max(0.0, no - sets), "set": sets}

    @staticmethod
    def cash_tiers(free, is_bid, price, no_sell, sets_closed=False):
        """[(shares, cash a share)] an order fills in turn: its cash-free part first."""
        inf = float("inf")
        if is_bid and no_sell:                    # covered "sell NO": lone part free, set part breaks sets
            return [(free["lone"], 0.0), (free["set"], 0.0 if sets_closed else 1.0), (inf, price)]
        if is_bid:
            return [(inf, price)]                 # buying YES is a purchase (also on a NO holding: live 3 Oct)
        return [(free["yes"], 0.0), (inf, 1 - price)]   # beyond the YES held: buying NO at 1 - price

    @staticmethod
    def tier_need(tiers, qty):
        need, left = 0.0, float(qty)
        for amt, cost in tiers:
            take = min(left, amt)
            need, left = need + take * cost, left - take
            if left <= 1e-9:
                break
        return need

    @staticmethod
    def tier_max(tiers, budget):
        q = 0.0
        for amt, cost in tiers:
            take = amt if cost <= 0 else min(amt, max(0, math.floor(max(0.0, budget) / cost + 1e-9)))
            q += take
            budget -= take * cost
            if take < amt:
                break
        return q

    @staticmethod
    def tier_consume(free, is_bid, no_sell, qty):
        if is_bid and no_sell:
            a = min(free["lone"], qty)
            free["lone"] -= a
            free["set"] = max(0.0, free["set"] - (qty - a))
        elif not is_bid:
            free["yes"] = max(0.0, free["yes"] - qty)

    def cash_gate_orders(self, orders, joint=False, commit=True, skip_eids=()):
        """The gate over one batch (orders in YES terms, as built): returns ([keep], cash needed). commit: shrink
        each order's quantity in place to what the cash allows (its cash-free part always), mark the ones left
        with < 1 share as not kept, and count the cash as spent. joint (arbitrage / pair unwinds): every leg
        shrinks to the same number of shares, all or none. skip_eids: our resting orders there are ignored
        (cancelled before this batch goes: takes)."""
        left = self.cash_left()
        free = {}
        sets_closed = joint and all(o.get("_no_sell") for o in orders)
        plan = []
        for o in orders:
            eid = o["exchangeId"]
            if eid not in free:
                free[eid] = self.cash_free(eid, skip=(lambda r: True) if eid in skip_eids else (lambda r: False))
            plan.append((o, o["action"] == "buy", bool(o.get("_no_sell"))))
        if joint:
            qty = int(min(o["quantity"] for o in orders)) if orders else 0

            def need_at(n, per_order=False):
                fr = {e: dict(v) for e, v in free.items()}
                each = []
                for o, b, ns in plan:
                    each.append(self.tier_need(self.cash_tiers(fr[o["exchangeId"]], b, o["price"], ns, sets_closed), n))
                    self.tier_consume(fr[o["exchangeId"]], b, ns, n)
                return each if per_order else sum(each)
            lo, hi = 0, qty
            if need_at(qty) <= left + 1e-9:
                lo = qty
            else:
                while lo < hi:                    # the most sets every leg can take within the cash
                    mid = (lo + hi + 1) // 2
                    if need_at(mid) <= left + 1e-9:
                        lo = mid
                    else:
                        hi = mid - 1
            allowed = [lo] * len(orders)
            needs = need_at(lo, per_order=True)
        else:
            allowed, needs = [], []
            for o, b, ns in plan:
                tiers = self.cash_tiers(free[o["exchangeId"]], b, o["price"], ns)
                q = int(min(o["quantity"], self.tier_max(tiers, left)))
                n = self.tier_need(tiers, q) if q >= 1 else 0.0
                if q >= 1:
                    self.tier_consume(free[o["exchangeId"]], b, ns, q)
                    left -= n
                allowed.append(q)
                needs.append(n)
        keep = [q >= 1 for q in allowed]
        need = sum(needs) if all(keep) or not joint else 0.0
        if not commit:
            return keep, need
        now_m = util.time.monotonic()
        seen = self.__dict__.setdefault("cg_logged", {})
        for o, q, k in zip(orders, allowed, keep):
            if q >= o["quantity"]:
                continue
            if k:
                self.cash_trimmed = getattr(self, "cash_trimmed", 0) + 1
            else:
                self.cash_gated = getattr(self, "cash_gated", 0) + 1
            eid = o["exchangeId"]
            if now_m - seen.get(eid, -1e18) >= self.CASH_LOG_SECONDS:
                seen[eid] = now_m
                ex = self.ex.get(eid)
                log.info("CASH GATE %s: %s %d @ %.3f%s -> %s (cash left %.2f, reserve %.0f)",
                         ex.label if ex else eid, "bid" if o["action"] == "buy" else "ask", o["quantity"],
                         o["price"], " (sell NO)" if o.get("_no_sell") else "",
                         f"{q} shares" if k else "not sent", self.cash_left(), self.cfg.cash_gate_reserve)
            if k:
                o["quantity"] = int(q)
        if need:
            for o, k, n in zip(orders, keep, needs):   # each sent order's need: given back if it is not placed
                if k and n > 0:
                    o["_cash_need"] = float(n)
        self.cg_spent = getattr(self, "cg_spent", 0.0) + need
        return keep, need

    def cash_refund(self, orders):
        """Give back to the gate the cash need counted as spent when these orders were sent, as they were
        not placed (batch failed / refused / never sent). Each order's need is given back once."""
        back = sum(float(o.pop("_cash_need", 0.0) or 0.0) for o in orders if isinstance(o, dict))
        if back > 0 and getattr(self, "cg_cash", None) is not None:
            self.cg_spent = getattr(self, "cg_spent", 0.0) - back
        return back

    def cash_gate_blocks(self, orders, joint=False):
        """Pre-check before a take / arbitrage pulls our quotes: True if the gate would send none of these orders
        (our resting orders on their exchanges are ignored: they are cancelled first). False with the gate off."""
        if not self.cash_gate_on() or not orders:
            return False
        keep, _ = self.cash_gate_orders(orders, joint=joint, commit=False,
                                        skip_eids={o["exchangeId"] for o in orders})
        if not any(keep):
            self.cash_gated = getattr(self, "cash_gated", 0) + len(orders)
            return True
        return False

    def take_blocked_by_reserve(self, order):
        """take_respect_reserve: True if this take order (YES terms, possibly marked _no_sell) would leave less than
        alloc_mm_reserve of cash free: cash_left() - its gate need < reserve. False with the flag off, the gate off, or
        no reserve. A covered sale / a cash-free order (need 0) is never blocked."""
        cfg = self.cfg
        reserve = float(getattr(cfg, "alloc_mm_reserve", 0.0) or 0.0)
        if not getattr(cfg, "take_respect_reserve", False) or reserve <= 0 or not self.cash_gate_on():
            return False
        eid = order["exchangeId"]
        free = self.cash_free(eid, skip=lambda o: o.eid == eid)      # our quotes there are cancelled before the take
        is_bid = order["action"] == "buy"
        need = self.tier_need(self.cash_tiers(free, is_bid, order["price"], bool(order.get("_no_sell"))),
                              float(order["quantity"]))
        if need <= 1e-9:
            return False
        if self.cash_left() + self.hv_carve_used() - need < reserve - 1e-9:   # (the carve-out counts)
            self.take_reserve_blocked = getattr(self, "take_reserve_blocked", 0) + 1
            return True
        return False

    def cash_gate_quote(self, ex, q, resting):
        """Plan-time cap (plan_exchange): each side's wanted size at most what the cash allows (the resting level-0
        order on that side counts as available: it is kept or replaced), so a capped quote is planned as such
        (no churn against the send-time gate). bid_max / ask_max keep the uncapped size: a resting order is never
        pulled for cash. The plan's budget (cg_plan_left) shrinks by what is planned."""
        left = max(0.0, getattr(self, "cg_plan_left", 0.0))
        lvl0 = list(resting)
        for is_bid in (True, False):
            price, size = (q.bid, q.bid_size) if is_bid else (q.ask, q.ask_size)
            if price is None or size < 1:
                continue
            same = [o for o in lvl0 if o.is_bid == is_bid]
            ids = {o.order_id for o in same}
            free = self.cash_free(ex.eid, skip=lambda o: o.order_id in ids)
            no_sell = is_bid and self.cover_no_qty(ex.eid, ex.inv, 0) >= 1
            own = sum(self.resting_lock(o) for o in same)
            tiers = self.cash_tiers(free, is_bid, price, no_sell)
            cap = int(min(size, self.tier_max(tiers, left + own)))
            left = max(0.0, left - max(0.0, self.tier_need(tiers, cap) - own))
            if cap >= size:
                continue
            self.cg_capped_now = getattr(self, "cg_capped_now", 0) + 1   # status: quote sides capped this cycle
            now_m, seen = util.time.monotonic(), self.__dict__.setdefault("cg_logged", {})
            if now_m - seen.get(ex.eid, -1e18) >= self.CASH_LOG_SECONDS:
                seen[ex.eid] = now_m
                log.info("CASH GATE %s: %s quote %d @ %.3f -> %s (cash left %.2f, reserve %.0f)", ex.label,
                         "bid" if is_bid else "ask", size, price, f"{cap} shares" if cap >= 1 else "not quoted",
                         left, self.cfg.cash_gate_reserve)
            if is_bid:
                q = (replace(q, bid_size=cap, bid_max=q.bid_max if q.bid_max is not None else size) if cap >= 1
                     else replace(q, bid=None, bid_size=0, bid_limit=None, bid_max=None))
            else:
                q = (replace(q, ask_size=cap, ask_max=q.ask_max if q.ask_max is not None else size) if cap >= 1
                     else replace(q, ask=None, ask_size=0, ask_limit=None, ask_max=None))
        self.cg_plan_left = left
        return q
    ST_REPORT_FRAC = 0.5          # status state_caps: the states above this x state_max_usd

    # --- the per-state collateral cap (state_max_usd) ---
    def st_on(self):
        return float(getattr(self.cfg, "state_max_usd", 0.0) or 0.0) > 0

    def st_key(self, eid):
        """The state of a market (state_of its label; cached), or None."""
        if eid not in self.st_keys:
            ex = self.ex.get(eid)
            self.st_keys[eid] = state_of(ex.label) if ex is not None else None
        return self.st_keys[eid]

    def st_px(self, ex, now_m=None):
        """The value of one YES share for the state cap: the liquid race-scaled Polymarket p (alloc_p), else the
        exchange's mark, else the fair value, else 0.5."""
        p = self.alloc_p(ex, now_m)
        if p is None:
            p = (getattr(self, "pos_marks", None) or {}).get(ex.eid)
        if p is None:
            p = ex.last_fv
        return min(1.0, max(0.0, float(p))) if p is not None else 0.5

    def st_snapshot(self, inv, now_m=None, skip_ids=()):
        """({state: $ collateral}, {eid: {is_bid: $ our other resting orders lock}}): positions at p (longs q x p,
        shorts |q| x (1 - p)) + the cash our resting orders lock (resting_lock), but those in skip_ids."""
        coll, own = defaultdict(float), {}
        for e, q in (inv or {}).items():
            ex = self.ex.get(e)
            st = self.st_key(e) if ex is not None else None
            if st is None or abs(float(q)) < 1:
                continue
            p = self.st_px(ex, now_m)
            coll[st] += float(q) * p if q > 0 else -float(q) * (1 - p)
        for o in (list(self.my_orders.values()) if self.api.live else []):
            st = self.st_key(o.eid)
            if st is None or o.order_id in skip_ids:
                continue
            lock = self.resting_lock(o)
            coll[st] += lock
            if not (self.order_meta.get(o.order_id) or {}).get("harvest"):
                d = own.setdefault(o.eid, {True: 0.0, False: 0.0})
                d[o.is_bid] += lock
        return dict(coll), own

    def st_refresh(self, inv, now_m=None):
        """Cycle step 6 (state_max_usd > 0): this cycle's per-state collateral (the takes, the allocator, the ladder
        and the quoter then add to it as they go)."""
        try:
            self.st_coll, self.st_own = self.st_snapshot(inv, now_m)
        except Exception as err:                  # (a reporting-grade figure: never stops a cycle)
            log.warning("state cap: collateral not computed (%s) - no state adds blocked this cycle", err)
            self.st_coll, self.st_own = {}, {}

    def st_rooms(self, inv, now_m=None):
        """{state: $ left under state_max_usd} on these positions (and our resting orders)."""
        coll, _ = self.st_snapshot(inv, now_m)
        return {s: self.cfg.state_max_usd - c for s, c in coll.items()}

    def st_count(self, ex, kind):
        st = self.st_key(ex.eid) if ex is not None else None
        if st is not None:
            self.st_blocked[st] += 1

    def st_add_room(self, ex, buy, n, p=None, commit=True, force=False):
        """Shares (<= n) of an ADD on ex (buy: YES bought, else YES sold short) its state's room allows now, valued at
        p (a long p a share, a short 1 - p); commit: the state's collateral grows by them (force: by all n). Markets
        without a state: n."""
        st = self.st_key(ex.eid)
        n = int(max(0, n))
        if st is None or n < 1:
            return n
        p = self.st_px(ex) if p is None else p
        unit = max(p if buy else 1 - p, TICK)
        room = self.cfg.state_max_usd - self.st_coll.get(st, 0.0)
        ok = n if force else int(min(n, max(0.0, room) / unit) + 1e-9)
        if commit and ok >= 1:
            self.st_coll[st] = self.st_coll.get(st, 0.0) + ok * unit
        return ok

    def st_quote_caps(self, ex, fv, ref, ref_liquid):
        """decide (state_max_usd > 0): (bid cap, ask cap) in shares for a TAIL market (its liquid race-scaled p, else
        its fair value, outside value_mid_low..value_mid_high) of a state: the reducing part + what the state's room
        allows at p (our own resting non-harvest orders on that side excluded: the quote replaces them). None for a
        middle-band / stateless market (no cap)."""
        st = self.st_key(ex.eid)
        if st is None:
            return None
        p = self.value_p(ex, ref, ref_liquid)
        p = fv if p is None else p
        if p is None or self.cfg.value_mid_low <= p <= self.cfg.value_mid_high:
            return None
        own = self.st_own.get(ex.eid) or {True: 0.0, False: 0.0}
        caps = []
        for is_bid in (True, False):
            room = self.cfg.state_max_usd - (self.st_coll.get(st, 0.0) - own.get(is_bid, 0.0))
            unit = max(p if is_bid else 1 - p, TICK)
            red = max(0, int(-ex.inv)) if is_bid else max(0, int(ex.inv))
            caps.append(red + int(max(0.0, room) / unit + 1e-9))
        return tuple(caps)

    def st_status(self):
        """status.json state_caps: {state over ST_REPORT_FRAC of the cap: {collateral, cap, blocked_adds}}."""
        cap = float(self.cfg.state_max_usd)
        return {s: {"collateral": round(c, 2), "cap": cap, "blocked_adds": int(self.st_blocked.get(s, 0))}
                for s, c in sorted(self.st_coll.items()) if c > self.ST_REPORT_FRAC * cap}
