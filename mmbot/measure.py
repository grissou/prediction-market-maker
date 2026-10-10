"""
Measurement and reporting from fills.csv and the sqlite recorder: the numbers a human reads.

Owns FillLogger and read_fills (the fills tape), TurnoverTracker (how fast capital turns), fill_stats,
build_summary (the 2-hourly phone summary and its ops / EV / carry parts), and the `report` and
`analyze` commands that read the recorded data after the fact.

It only reads and summarises: it never sends orders, never changes bot state and never imports the
bot or the mixins. Anything here failing must not stop trading, so callers wrap it.
"""
import csv
import os
import sqlite3
from collections import defaultdict, deque
from datetime import timedelta

from mmbot import util
from mmbot import exchange
from mmbot.util import HERE, log, parse_ts
from mmbot.exchange import ApiError


# MEASUREMENT - fills.csv and the `report` command
# =============================================================================================

class FillLogger:
    """Appends every new fill to a CSV, tagged with which of our quotes it hit and the fair value
    at the moment we placed that quote, so edge and adverse selection can be measured later."""
    COLUMNS = ["fill_id", "filled_at", "exchange_id", "order_id", "our_side", "qty",
               "fill_price", "quote_price", "fv_at_quote", "fv_after", "level"]   # level: always 0 now

    def __init__(self, path):
        self.path, self.seen = path, set()
        if os.path.exists(path):
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if reader.fieldnames == self.COLUMNS[:-1]:     # before the ladder's level column: add it (blank)
                with open(path + ".tmp", "w", newline="") as f:
                    w = csv.DictWriter(f, self.COLUMNS)
                    w.writeheader()
                    w.writerows({**r, "level": ""} for r in rows)
                os.replace(path + ".tmp", path)
                reader.fieldnames = self.COLUMNS
            if reader.fieldnames != self.COLUMNS:          # file from an older version of the bot
                if rows:
                    os.replace(path, path + ".old")        # keep old data, start a fresh file
                else:
                    os.remove(path)
            else:
                self.seen = {r["fill_id"] for r in rows}
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(self.COLUMNS)

    def record(self, fills, order_meta, fvs):
        with open(self.path, "a", newline="") as fh:
            w = csv.writer(fh)
            for f in reversed(fills):                      # API gives newest first; write oldest first
                meta = order_meta.get(f.get("orderId")) or {}
                qty = abs(float(f.get("quantity") or 0))   # API signs quantity negative for NO-side fills
                w.writerow([f["id"], f.get("filledAt"), f.get("exchangeId"), f.get("orderId"),
                            meta.get("our_side", "?"), qty, f.get("price"), meta.get("price", ""),
                            meta.get("fv", ""), fvs.get(str(f.get("exchangeId")), ""),
                            0 if meta else ""])
                self.seen.add(str(f["id"]))
                log.info("FILL ex %s  our %s x%.0f @ %s  (fv when quoted %s)%s", f.get("exchangeId"),
                         meta.get("our_side", "?"), qty, meta.get("price", f.get("price")), meta.get("fv", "?"),
                         "")


def read_fills(path):
    """All rows of fills.csv as dicts ([] if there's no file yet)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class TurnoverTracker:
    """Shares traded per market over a trailing window (turnover control, see Config). Two sources per market:
    our own fills and the realtime trade tape (every trader's trades, ours included), each a deque of
    (unix time, shares). Observed flow = the larger of the two (the tape includes our fills, so adding them would
    count ours twice; our fills are the floor when the tape has gaps), per hour of OBSERVED time in the window.

    Observed time = the part of the window covered by this run (since `start`) or by the seeds read at start-up.
    Seeds give time points (fills.csv fills; the recorder's trades and snapshot times, one a minute while it ran);
    points no more than GAP_SECONDS apart (and the last one to the start) join into observed intervals, and a longer
    gap - an outage, a restart after one - is NOT observed (it is not zero flow). Until the observed time reaches
    MIN_COVERAGE of the window nothing is judged (every market is alive): no false "dead" in the first hours after
    a start without history, nor after a restart that followed an outage."""
    KEEP_HOURS = 48.0                     # retention (the largest turnover_window_hours allowed live)
    MIN_COVERAGE = 0.9                    # share of the window that must be observed before judging
    GAP_SECONDS = 600.0                   # seed points further apart than this: the time between is unobserved

    def __init__(self, start=None):
        self.start = util.time.time() if start is None else float(start)
        self.seed_points = []             # unix times before `start` known to be observed
        self.seed_spans = []              # [(t0, t1)] observed intervals built from them (see _rebuild)
        self.ours, self.tape = defaultdict(deque), defaultdict(deque)

    def _cover(self, points):
        """Add observed time points (before start) and rebuild the observed intervals."""
        self.seed_points.extend(float(t) for t in points if t is not None and float(t) <= self.start)
        pts = sorted(set(self.seed_points))
        spans = []
        for t in pts + ([self.start] if pts else []):
            if spans and t - spans[-1][1] <= self.GAP_SECONDS:
                spans[-1][1] = t
            else:
                spans.append([t, t])
        self.seed_spans = [(a, b) for a, b in spans if b > a]

    def add(self, eid, t, qty, tape=False):
        """One trade of |qty| shares at unix time t (ours, or from the tape)."""
        q = abs(float(qty or 0))
        if q > 0:
            (self.tape if tape else self.ours)[str(eid)].append((float(t), q))

    def prune(self, now):
        cut = now - 3600 * self.KEEP_HOURS
        for book in (self.ours, self.tape):
            for dq in book.values():
                while dq and dq[0][0] < cut:
                    dq.popleft()

    def observed_hours(self, now, window_hours):
        """Hours of the window [now - window, now] covered by this run or by the seeds' intervals."""
        lo = now - 3600 * window_hours
        run = max(0.0, now - max(lo, self.start))
        seed = sum(max(0.0, min(b, self.start, now) - max(a, lo)) for a, b in self.seed_spans)
        return min(window_hours, (run + seed) / 3600)

    def judged(self, now, window_hours):
        return self.observed_hours(now, window_hours) >= self.MIN_COVERAGE * window_hours - 1e-9

    @staticmethod
    def _sum(dq, lo, now):
        return sum(q for t, q in dq if lo <= t <= now)

    def shares(self, eid, now, window_hours, use_tape=True):
        """Shares traded in the window: max(our fills, tape)."""
        lo = now - 3600 * window_hours
        ours = self._sum(self.ours.get(str(eid), ()), lo, now)
        return max(ours, self._sum(self.tape.get(str(eid), ()), lo, now)) if use_tape else ours

    def per_hour(self, eid, now, window_hours, use_tape=True):
        """Observed flow in shares per hour, None while too little of the window has been observed."""
        if not self.judged(now, window_hours):
            return None
        return self.shares(eid, now, window_hours, use_tape) / max(self.observed_hours(now, window_hours), 1e-9)

    def seed_fills(self, rows, now):
        """fills.csv rows (read_fills): those within KEEP_HOURS feed `ours` and count as observed time points.
        Returns how many rows were used."""
        cut, pts = now - 3600 * self.KEEP_HOURS, []
        for r in rows:
            try:
                t = parse_ts(r.get("filled_at"))
                q = float(r.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if t is not None and cut <= t.timestamp() <= self.start:
                pts.append((t.timestamp(), str(r.get("exchange_id")), q))
        pts.sort()
        for t, eid, q in pts:
            self.add(eid, t, q)
        self._cover(t for t, _, _ in pts)
        return len(pts)

    @staticmethod
    def _first_rowid_at(db, table, ts_min, to_ts):
        """Smallest rowid whose ts >= ts_min (rowid grows with time): a binary search, a few indexed reads."""
        top = db.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0]
        if top is None:
            return None
        lo, hi = 1, top + 1
        while lo < hi:
            mid = (lo + hi) // 2
            row = db.execute(f"SELECT ts FROM {table} WHERE rowid >= ? ORDER BY rowid LIMIT 1", (mid,)).fetchone()
            t = to_ts(row[0]) if row else None
            if row is None or (t is not None and t >= ts_min):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def seed_tape(self, db_path, now):
        """The recorder (read-only): its trades within KEEP_HOURS feed `tape`; their times and the snapshot times
        (one a minute while the bot ran) are observed time points. Returns trades used (0 = nothing to read)."""
        if not db_path or not os.path.exists(db_path):
            return 0
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return 0
        cut, pts, rows = now - 3600 * self.KEEP_HOURS, [], []

        def snap_ts(v):
            t = parse_ts(v) if isinstance(v, str) else None
            return t.timestamp() if t is not None else None
        try:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "trades" in names:
                first = self._first_rowid_at(db, "trades", cut, lambda v: float(v) if v is not None else None)
                if first is not None:
                    rows = db.execute("SELECT ts, eid, quantity FROM trades WHERE rowid >= ? AND ts <= ? "
                                      "ORDER BY ts", (first, self.start)).fetchall()
            if "snapshots" in names:
                first = self._first_rowid_at(db, "snapshots", cut, snap_ts)
                if first is not None:
                    pts = [snap_ts(r[0]) for r in db.execute("SELECT DISTINCT ts FROM snapshots WHERE rowid >= ?",
                                                             (first,))]
        except (sqlite3.Error, TypeError, ValueError):
            return 0
        finally:
            db.close()
        used = 0
        for t, eid, q in rows:
            if t is not None and q is not None and cut <= float(t):
                self.add(eid, t, q, tape=True)
                used += 1
        self._cover([float(t) for t, _, _ in rows if t is not None and float(t) >= cut]
                    + [t for t in pts if t is not None and t >= cut])
        return used


def fill_stats(rows):
    """Two numbers tell you whether the market making is working:

      edge     how far on the right side of fair value each fill was, using the fair value at the
               moment we QUOTED. Positive = we earned spread.
      markout how fair value moved straight after the fill, measured in our direction. Negative =
               the people trading with us knew something (adverse selection).

    Profit per share is roughly edge + markout. Our fills are as a maker (or an arbitrage at a
    price we chose), so they happen at our own order price; that's the price used here.
    """
    q_tot = edge_tot = mk_tot = mk_q = 0.0
    unknown = 0
    for r in rows:
        if r["our_side"] not in ("bid", "ask") or not r["fv_at_quote"] or not r["quote_price"]:
            unknown += 1
            continue
        q, p, fvq = float(r["qty"]), float(r["quote_price"]), float(r["fv_at_quote"])
        d = 1 if r["our_side"] == "bid" else -1           # bid = we bought YES, ask = we sold YES
        q_tot += q
        edge_tot += d * (fvq - p) * q
        if r["fv_after"]:
            mk_tot += d * (float(r["fv_after"]) - fvq) * q
            mk_q += q
    return {"fills": len(rows), "unmatched": unknown, "shares": q_tot, "edge_total": edge_tot,
            "edge_c": 100 * edge_tot / max(q_tot, 1), "markout_c": 100 * mk_tot / max(mk_q, 1)}


def ops_summary_line(ops, account=None, tilt_s=None, tilt_exposure=None):
    """The phone summary's ops line, e.g. "Liquidation 101.1k (account 101.5k), realised +154, 76% of
    capital toward Polymarket, 69% older than 6 h | tilt 5.1%, exposure +15.1k (-151 per point)". The tilt part only
    when tilt_s is known (a fraction: 0.051 = 5.1%); per point = -exposure x 0.01. Unknown pieces show "?";
    None when there is nothing to show."""
    ops = ops or {}
    if account is None and all(ops.get(k) is None for k in ("liquidation_value", "realised_pnl",
                                                             "toward_ref_capital_frac", "capital_over_6h_frac")):
        line = None
    else:
        k = lambda v: f"{v / 1000:.1f}k" if v is not None else "?"
        pct = lambda v: f"{100 * v:.0f}%" if v is not None else "?"
        real = ops.get("realised_pnl")
        line = (f"Liquidation {k(ops.get('liquidation_value'))} (account {k(account)}), realised "
                + (f"{real:+,.0f}" if real is not None else "?")
                + f", {pct(ops.get('toward_ref_capital_frac'))} of capital toward Polymarket, "
                f"{pct(ops.get('capital_over_6h_frac'))} older than 6 h")
    if tilt_s is not None:
        tilt = f"tilt {100 * tilt_s:.1f}%" + (f", exposure {tilt_exposure / 1000:+.1f}k ({-0.01 * tilt_exposure:+,.0f} "
                                               f"per point)" if tilt_exposure is not None else "")
        line = f"{line} | {tilt}" if line else tilt
    return line


def ev_outcome_part(ops):
    """"EV outcome 101.2k (+1,234 24h, 2 unpriced)" from the ev fields ("?" for an unknown 24-h delta); None
    while ev_outcome is unknown."""
    ops = ops or {}
    ev = ops.get("ev_outcome")
    if ev is None:
        return None
    d = ops.get("ev_outcome_delta_24h")
    return (f"EV outcome {ev / 1000:.1f}k ({f'{d:+,.0f}' if d is not None else '?'} 24h, "
            f"{ops.get('ev_outcome_unpriced') or 0} unpriced)")


def mm_carry_part(ops):
    """"MM carry 24h +12 (mid), value adds +340, takes -25" from mm_carry_24h; None without it."""
    mc = (ops or {}).get("mm_carry_24h")
    if not isinstance(mc, dict):
        return None
    return (f"MM carry 24h {mc.get('realised', 0):+,.0f} (mid), value adds {mc.get('value_adds_ev', 0):+,.0f}, "
            f"takes {mc.get('takes_ev', 0):+,.0f}")


def build_summary(api, fills_path, initial_balance, value=None, value_prev=None, arbs=None, health=None, takes=None,
                  hours=24, status=None, ops_line=None):
    """The phone summary as (title, message). Reads account value (unless given), rank and Smart Score
    from the API, and the last `hours` of fills from fills.csv. value_prev = account value at the previous
    summary. status = the bot's status line, shown first. Missing pieces show as "?" (e.g. no rank before
    our first trade) instead of failing."""
    def safe(fn, default=None):
        try:
            return fn()
        except (ApiError, KeyError, TypeError, ValueError):
            return default

    if value is None:
        value = safe(lambda: float(api.pnl()["totalAccountValue"]))
    lb = safe(api.leaderboard, {}) or {}
    scores = safe(api.smart_score, []) or []
    score = next((s for s in scores if s.get("marketType") == "global"), scores[0] if scores else None)
    since = util.utcnow() - timedelta(hours=hours)
    day = fill_stats([r for r in read_fills(fills_path) if (parse_ts(r.get("filled_at")) or since) >= since])

    if value is None:
        title, account = "mm_bot: account ?", "Account: unavailable"
    else:
        total = value - initial_balance
        title = f"mm_bot: {total:+,.0f} ({100 * total / initial_balance:+.1f}%)"
        day_change = "" if value_prev is None else f", {value - value_prev:+,.0f} in {hours:g}h"
        account = f"Account {value:,.0f} ({total:+,.0f} total{day_change})"
    rank = (f"Rank {lb['myRank']} of {lb.get('total', '?')}" if lb.get("myRank") else "Rank: not ranked yet")
    smart = (f"Smart Score {score.get('smartScoreDecayed', 0):.1f} (rank {score.get('rank', '?')} of "
             f"{score.get('totalTraders', '?')}{', ELITE' if score.get('isElite') else ''})"
             if score else "Smart Score: not scored yet")
    lines = ([status] if status else []) + [account] + ([ops_line] if ops_line else []) + [f"{rank} | {smart}",
             f"Last {hours:g}h: {day['fills']} fills, {day['shares']:,.0f} shares, edge {day['edge_c']:+.2f}c, "
             f"markout {day['markout_c']:+.2f}c" + (f", {arbs} arbitrages" if arbs is not None else "")
             + (f", {takes} takes" if takes is not None else "")]
    if health:
        lines.append(f"Now: {health.get('orders_resting', '?')} orders resting, worst-case loss "
                     f"{health.get('worst_case_loss', 0):,.0f}, realtime {health.get('realtime', '?')}, "
                     f"rate limits {health.get('rate_limited_total', 0)}")
        if health.get("portfolio_age_hours") is not None:
            frac = health.get("capital_in_positions_frac")
            lines.append(f"Positions: avg age {health['portfolio_age_hours']:.1f}h ({health.get('positions_over_3h', 0)} "
                         f"> 3h, {health.get('positions_over_12h', 0)} > 12h), capital in positions "
                         + (f"{100 * frac:.0f}%" if frac is not None else "?")
                         + (" - CEILING: adding sides cut" if health.get("capital_ceiling_active") else ""))
        if health.get("turnover_dead_markets") is not None:
            lines.append(f"dead-turnover markets: {health['turnover_dead_markets']} holding "
                         f"{health.get('turnover_dead_capital', 0) / 1000:.1f}k")
        if health.get("mark_frag_estimates"):
            top = next(iter((health.get("mark_frag_top") or {}).items()), None)
            lines.append(f"Mark noise: {health['mark_frag_total_cash']:,.0f} $ per 10 min, "
                         f"{health.get('mark_frag_capped_markets', 0)} positions at the cap"
                         + (f", biggest {top[0]} {top[1]:,.0f}" if top else ""))
    return title, "\n".join(lines)


def report(path):
    """The `report` command: print fill_stats for every fill so far."""
    rows = read_fills(path)
    if not rows:
        return print("no fills yet")
    s = fill_stats(rows)
    print(f"{s['fills']} fills ({s['unmatched']} not matched to a bot quote), {s['shares']:.0f} shares")
    print(f"  edge    {s['edge_c']:+.2f} c/share   total {s['edge_total']:+.0f} SUSQies")
    print(f"  markout {s['markout_c']:+.2f} c/share   (fair value ~1 cycle after the fill)")

MARKOUT_MINUTES = (1, 5, 30)


def analyze(fills_path, db_path, hours=None, top=15):
    """The `analyze` command: from fills.csv and market_data.sqlite only (no API requests). Lines of text:
      - per market: fills, shares, edge at the quote (c/share: how far inside fair value we traded), markout
        after 1/5/30 min (fair value then vs our price, c/share: negative = picked off), P&L marked at the
        latest fair value;
      - per market from the snapshots: share of the time our bid/ask was the best price ("at top"), and how
        often a quote that was at the top was beaten by the next snapshot ("undercut", per hour quoted).
    hours: only the last N hours."""
    out = []
    since = (util.utcnow() - timedelta(hours=hours)) if hours else None
    fvs = defaultdict(list)                                  # eid -> [(time, fv)] from the snapshots
    tops = defaultdict(list)                                 # eid -> [(time, bid at top, ask at top, quoted)]
    labels = {}
    if db_path and os.path.exists(db_path):
        db = sqlite3.connect(db_path)
        try:
            for ts, eid, label, bb, ba, fv, ob, oa in db.execute(
                    "SELECT ts, eid, label, best_bid, best_ask, fair_value, our_bid, our_ask FROM snapshots ORDER BY ts"):
                t = parse_ts(ts)
                if t is None or (since and t < since):
                    continue
                labels[eid] = label
                if fv is not None:
                    fvs[eid].append((t, fv))
                tops[eid].append((t, ob is not None and bb is not None and abs(ob - bb) < 1e-9,
                                  oa is not None and ba is not None and abs(oa - ba) < 1e-9, ob is not None or oa is not None))
        except sqlite3.Error as e:
            out.append(f"(snapshots unreadable: {e})")
        finally:
            db.close()

    def fv_at(eid, t):
        series = fvs.get(eid) or []
        for tt, v in series:                                 # first snapshot at or after t
            if tt >= t:
                return v
        return None

    per = defaultdict(lambda: {"fills": 0, "shares": 0.0, "edge": 0.0, "edge_n": 0.0, "pnl": 0.0,
                               **{f"m{m}": 0.0 for m in MARKOUT_MINUTES}, **{f"m{m}_n": 0.0 for m in MARKOUT_MINUTES}})
    unmatched = 0
    by_level = defaultdict(lambda: {"fills": 0, "shares": 0.0, "edge": 0.0, "edge_n": 0.0})
    for r in read_fills(fills_path):
        t = parse_ts(r.get("filled_at"))
        if since and (t is None or t < since):
            continue
        eid, side = str(r.get("exchange_id")), r.get("our_side")
        try:
            qty = float(r.get("qty") or 0)
            price = float(r.get("quote_price") or r.get("fill_price") or 0)
        except ValueError:
            continue
        if side not in ("bid", "ask"):
            unmatched += 1
            continue
        sign = 1 if side == "bid" else -1                    # +1 = we bought YES
        lv = by_level[int(r["level"]) if str(r.get("level") or "").isdigit() else 0]
        lv["fills"] += 1
        lv["shares"] += qty
        if r.get("fv_at_quote") not in ("", None):
            lv["edge"] += sign * (float(r["fv_at_quote"]) - price) * qty
            lv["edge_n"] += qty
        m = per[eid]
        m["fills"] += 1
        m["shares"] += qty
        if r.get("fv_at_quote") not in ("", None):
            m["edge"] += sign * (float(r["fv_at_quote"]) - price) * qty
            m["edge_n"] += qty
        for mins in MARKOUT_MINUTES:
            v = fv_at(eid, t + timedelta(minutes=mins)) if t else None
            if v is not None:
                m[f"m{mins}"] += sign * (v - price) * qty
                m[f"m{mins}_n"] += qty
        last = fvs[eid][-1][1] if fvs.get(eid) else None
        if last is not None:
            m["pnl"] += sign * (last - price) * qty
    c = lambda m, k: f"{100 * m[k] / m[k + '_n']:+6.2f}" if m.get(k + "_n") else "    - "
    out.append(f"fills: {sum(m['fills'] for m in per.values())} matched, {unmatched} not matched to a bot quote"
               + (f" (last {hours:g} h)" if hours else ""))
    tot = {k: sum(m[k] for m in per.values()) for k in ("shares", "edge", "edge_n", "pnl",
                                                        *[f"m{x}" for x in MARKOUT_MINUTES], *[f"m{x}_n" for x in MARKOUT_MINUTES])}
    out.append(f"all markets: {tot['shares']:.0f} shares, edge {c(tot, 'edge')}c, markout "
               + " / ".join(f"{x}m {c(tot, f'm{x}')}c" for x in MARKOUT_MINUTES) + f", P&L at latest fair value {tot['pnl']:+.0f}")
    if any(k > 0 for k in by_level):                         # the ladder filled: touch vs each ladder level
        out.append("by level: " + " | ".join(f"L{k} {v['fills']} fills {v['shares']:.0f} sh edge {c(v, 'edge').strip()}c"
                                             for k, v in sorted(by_level.items())))
    out.append(f"{'market':26} {'fills':>5} {'shares':>8} {'edge c':>7} " + " ".join(f"{f'mk{x}m':>7}" for x in MARKOUT_MINUTES)
               + f" {'P&L':>8} {'top bid':>7} {'top ask':>7} {'undercut/h':>10}")
    rows = []
    for eid in set(per) | set(tops):
        m, snaps = per[eid], tops.get(eid, [])
        quoted = [x for x in snaps if x[3]]
        top_b = sum(x[1] for x in quoted) / len(quoted) if quoted else None
        top_a = sum(x[2] for x in quoted) / len(quoted) if quoted else None
        under = sum(1 for p, q in zip(snaps, snaps[1:]) if (p[1] and q[3] and not q[1]) or (p[2] and q[3] and not q[2]))
        span_h = (quoted[-1][0] - quoted[0][0]).total_seconds() / 3600 if len(quoted) > 1 else 0
        rows.append((m["pnl"], eid, m, top_b, top_a, under / span_h if span_h else None))
    for pnl, eid, m, tb, ta, uh in sorted(rows, key=lambda r: r[0])[:top] + (
            sorted(rows, key=lambda r: r[0])[-top:] if len(rows) > 2 * top else sorted(rows, key=lambda r: r[0])[top:]):
        pct = lambda v: f"{100 * v:6.0f}%" if v is not None else "     - "
        out.append(f"{labels.get(eid, eid)[:26]:26} {m['fills']:5d} {m['shares']:8.0f} {c(m, 'edge'):>7} "
                   + " ".join(f"{c(m, f'm{x}'):>7}" for x in MARKOUT_MINUTES)
                   + f" {m['pnl']:+8.0f} {pct(tb)} {pct(ta)} {uh if uh is None else round(uh, 1)!s:>10}")
    out.extend(rival_floor_lines(db_path, since, top))
    return out


def rival_floor_lines(db_path, since=None, top=15):
    """`analyze`, last table: the other traders' half-spread around the fair value and how often the best price
    changes (60-s intervals), per market, from the recorder's books table, via rival_floor.py (next to mm_bot.py
    or in analysis/). The widest `top` markets plus the spread of the recommended min_edge."""
    if not (db_path and os.path.exists(db_path)):
        return []
    src = next((x for x in (os.path.join(HERE, "rival_floor.py"), os.path.join(HERE, "analysis", "rival_floor.py"))
                if os.path.exists(x)), None)
    if src is None:
        return ["(rival floor: copy analysis/rival_floor.py next to mm_bot.py for this table)"]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rival_floor", src)
        rf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rf)
        db = sqlite3.connect(db_path)
        try:
            if not rf.has_rows(db, "books"):
                return []
            st = rf.stats_from_books(db, since.timestamp() if since else None, min_samples=10)
        finally:
            db.close()
    except Exception as e:                    # an analysis aid: never let it break `analyze`
        return [f"(rival floor unavailable: {e})"]
    dist = defaultdict(int)
    for v in st.values():
        dist[v["min_edge"]] += 1
    out = [f"rival floor (books, {len(st)} markets): recommended min_edge "
           + ", ".join(f"{100 * k:g}c x{n}" for k, n in sorted(dist.items())),
           f"{'market':26} {'half c':>6} {'top chg':>7} {'n':>6} {'edge c':>6}"]
    for eid, v in sorted(st.items(), key=lambda kv: -kv[1]["rival_half_spread_c"])[:top]:
        rate = v["top_change_rate"]
        out.append(f"{v['label'][:26]:26} {v['rival_half_spread_c']:6.2f} {'-' if rate is None else f'{rate:.2f}':>7} "
                   f"{v['samples']:6d} {100 * v['min_edge']:6g}")
    return out
