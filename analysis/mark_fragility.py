"""Mark fragility per position: how much mark-to-market noise each open position adds to the account value.

fragility = |position| x sd of the 10-minute change of the tournament mid (best_bid + best_ask) / 2, in $ per
10-minute step. The same estimate the bot's mark-fragility cap uses (mark_frag_* settings in mm_bot.py):
  - each snapshot row is paired with the first row of the same market at least 10 min later, at most 15 min
    later (rows further apart are a gap, not a step);
  - sd of those changes over the window (the last --hours hours of the file), floored at --floor;
  - a market with fewer than --min-samples changes gets no estimate.
Positions: the snapshots' `position` column when the file has it (the latest row per market), else
--positions status.json (its "positions" map, label -> shares), matched by label.
Prints the --top most fragile positions, the total, and per position the cap limit = max_step_cash / sd.

Why not the step between snapshots x sqrt(600 / spacing) (IDEAS_ROUND2 N6, 1,641 $ on the 1 Oct snapshot): the mid
flickers between snapshots and partly reverts, so scaling a short step by sqrt(time) overstates the 10-min noise;
the direct 10-min change gives 1,186 $ on the same data (-28%).

Standard library only. Run:
  python3 analysis/mark_fragility.py market_data.sqlite
  python3 analysis/mark_fragility.py market_data.sql --positions status.json      (a snapshot SQL dump)"""
import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

STEP_SECONDS = 600.0
MAX_STEP_FACTOR = 1.5


def ts_seconds(ts):
    """Recorder timestamps: ISO text ('2026-10-01T16:05:31.578Z') or epoch seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def step_changes(series, step=STEP_SECONDS, max_factor=MAX_STEP_FACTOR):
    """series = [(seconds, mid)] oldest first -> the mid's changes over ~step seconds (overlapping pairs)."""
    out, j, n = [], 0, len(series)
    for i in range(n):
        t0, m0 = series[i]
        if j <= i:
            j = i + 1
        while j < n and series[j][0] < t0 + step:
            j += 1
        if j < n and series[j][0] <= t0 + step * max_factor:
            out.append(series[j][1] - m0)
    return out


def step_sd(series, min_samples=60, floor=0.002, step=STEP_SECONDS):
    """sd of the ~step-second mid change, floored; None with fewer than min_samples changes."""
    ch = step_changes(series, step)
    if len(ch) < max(2, min_samples):
        return None
    mean = sum(ch) / len(ch)
    sd = math.sqrt(sum((c - mean) ** 2 for c in ch) / (len(ch) - 1))
    return max(sd, floor)


def open_db(path):
    """A market_data.sqlite, or a .sql dump loaded into a temp file (the source is never written)."""
    if path.endswith(".sql"):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        db = sqlite3.connect(tmp.name)
        with open(path) as f:
            db.executescript(f.read())
        return db, tmp.name
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True), None


def load(db, hours, mode=None):
    """-> {eid: [(seconds, mid)]}, {eid: label}, {eid: latest position or None}"""
    cols = [r[1] for r in db.execute("PRAGMA table_info(snapshots)")]
    has_pos = "position" in cols
    q = ("SELECT eid, label, ts, best_bid, best_ask" + (", position" if has_pos else ", NULL")
         + " FROM snapshots" + (" WHERE mode = ?" if mode else "") + " ORDER BY eid, ts")
    rows = db.execute(q, (mode,) if mode else ()).fetchall()
    cache, parsed = {}, []
    for eid, label, ts, bb, ba, pos in rows:
        t = cache.get(ts)
        if t is None:
            t = cache[ts] = ts_seconds(ts)
        parsed.append((str(eid), label, t, bb, ba, pos))
    last = max((p[2] for p in parsed), default=0.0)
    cutoff = last - hours * 3600 if hours and hours > 0 else -math.inf
    series, labels, positions = {}, {}, {}
    for eid, label, t, bb, ba, pos in parsed:
        labels[eid] = label
        if pos is not None:
            positions[eid] = float(pos)
        if t < cutoff or bb is None or ba is None:
            continue
        series.setdefault(eid, []).append((t, (float(bb) + float(ba)) / 2))
    return series, labels, positions if has_pos else None


def fragility_table(series, labels, positions, min_samples=60, floor=0.002, max_step_cash=100.0):
    """-> rows (label, position, sd, step cash, cap limit) sorted by step cash, biggest first; total; capped count"""
    rows = []
    for eid, ser in series.items():
        pos = (positions or {}).get(eid, 0.0)
        if not pos:
            continue
        sd = step_sd(ser, min_samples, floor)
        if sd is None:
            continue
        rows.append((labels.get(eid, eid), pos, sd, abs(pos) * sd, max_step_cash / sd))
    rows.sort(key=lambda r: -r[3])
    total = sum(r[3] for r in rows)
    capped = sum(1 for r in rows if abs(r[1]) > r[4])
    return rows, total, capped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", default="market_data.sqlite")
    ap.add_argument("--positions", help="status.json whose 'positions' map (label -> shares) to use")
    ap.add_argument("--hours", type=float, default=24.0, help="window ending at the last snapshot (0 = all)")
    ap.add_argument("--mode", default="live", help="snapshot mode to use ('' = all)")
    ap.add_argument("--min-samples", type=int, default=60)
    ap.add_argument("--floor", type=float, default=0.002)
    ap.add_argument("--max-step-cash", type=float, default=100.0)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    db, tmp = open_db(a.path)
    try:
        series, labels, positions = load(db, a.hours, a.mode or None)
    finally:
        db.close()
        if tmp:
            os.unlink(tmp)
    if a.positions:
        with open(a.positions) as f:
            by_label = json.load(f).get("positions") or {}
        positions = {e: float(by_label[l]) for e, l in labels.items() if l in by_label}
    if positions is None:
        print("no position column in snapshots: pass --positions status.json")
        return 1
    rows, total, capped = fragility_table(series, labels, positions, a.min_samples, a.floor, a.max_step_cash)
    print(f"{len(rows)} positions with an estimate; total {total:,.0f} $ per 10-min step; "
          f"{capped} above the {a.max_step_cash:g} $/step cap")
    print(f"{'market':32s} {'position':>9s} {'sd(c)':>6s} {'$/step':>7s} {'cap sh':>8s}")
    for label, pos, sd, cash, lim in rows[:a.top]:
        print(f"{str(label)[:32]:32s} {pos:9,.0f} {100 * sd:6.2f} {cash:7,.0f} {lim:8,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
