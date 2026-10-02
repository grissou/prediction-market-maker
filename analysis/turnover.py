"""Per-market turnover: how fast could each position leave at the flow we observe? (standard library only)

From fills.csv (our fills) and market_data.sqlite (the latest snapshot per market: label, best bid/ask, fair value,
position; the trades table: every trader's trades from the realtime feed, ours included), over the last --hours
(default 6) before the latest snapshot:

  fills/h     our fills per hour            ours sh/h   our shares traded per hour
  tape sh/h   all traders' shares per hour  flow sh/h   max(ours, tape) - what turnover control uses
  pos         position (YES shares, negative = NO)       capital   |pos| x price at mid (fair value if no mid)
  h-flat      hours to flatten at HALF the observed flow (only the reducing half of two-way flow helps)
  dead        flow below --dead (shares/h, default 50 = turnover_min_shares_per_hour) while holding a position

Then totals: dead markets holding positions and their capital; markets with < 2 fills/h; capital needing > 24 h.

Run on the server:  python3 analysis/turnover.py [--dir BOT_DIR] [--hours 6] [--dead 50] [--top 40]
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone


def parse_ts(s):
    """ISO timestamp -> unix time (None if empty or unreadable). Fractions are padded / cut to 6 digits
    (Python 3.10 reads only 3 or 6)."""
    if not s:
        return None
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s.strip().replace("Z", "+00:00"))
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def latest_snapshot(db):
    """{eid: {label, mid, fv, pos}} from the most recent snapshot cycle, and its unix time (None if none)."""
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "snapshots" not in names:
        return {}, None
    last = db.execute("SELECT rowid, ts FROM snapshots ORDER BY rowid DESC LIMIT 1").fetchone()
    if not last:
        return {}, None
    rows = db.execute("SELECT eid, label, best_bid, best_ask, fair_value, position FROM snapshots "
                      "WHERE rowid > ? AND ts = ?", (last[0] - 5000, last[1])).fetchall()
    out = {}
    for eid, label, bb, ba, fv, pos in rows:
        mid = (bb + ba) / 2 if bb is not None and ba is not None else None
        out[str(eid)] = {"label": label or str(eid), "mid": mid, "fv": fv, "pos": float(pos or 0)}
    return out, parse_ts(last[1])


def tape_shares(db, lo, hi):
    """{eid: shares} from the trades table in [lo, hi] (empty if there is no table)."""
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "trades" not in names:
        return {}
    out = {}
    for eid, q in db.execute("SELECT eid, SUM(ABS(quantity)) FROM trades WHERE ts >= ? AND ts <= ? "
                             "AND quantity IS NOT NULL GROUP BY eid", (lo, hi)):
        out[str(eid)] = float(q or 0)
    return out


def our_fills(path, lo, hi):
    """({eid: shares}, {eid: fill count}) from fills.csv in [lo, hi]."""
    sh, n = {}, {}
    if not os.path.exists(path):
        return sh, n
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            t = parse_ts(r.get("filled_at"))
            if t is None or not lo <= t <= hi:
                continue
            try:
                q = abs(float(r.get("qty") or 0))
            except ValueError:
                continue
            e = str(r.get("exchange_id"))
            sh[e] = sh.get(e, 0.0) + q
            n[e] = n.get(e, 0) + 1
    return sh, n


def table(fills_path, db_path, hours=6.0, dead=50.0, now=None):
    """Rows (one per market with a position or any flow), sorted by capital, and the window end used."""
    snap, t_snap = {}, None
    tape = {}
    if db_path and os.path.exists(db_path):
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            snap, t_snap = latest_snapshot(db)
            hi = now or t_snap or datetime.now(timezone.utc).timestamp()
            tape = tape_shares(db, hi - 3600 * hours, hi)
        finally:
            db.close()
    hi = now or t_snap or datetime.now(timezone.utc).timestamp()
    ours, counts = our_fills(fills_path, hi - 3600 * hours, hi)
    rows = []
    for e in set(snap) | set(ours) | set(tape):
        s = snap.get(e, {"label": e, "mid": None, "fv": None, "pos": 0.0})
        pos = s["pos"]
        price = s["mid"] if s["mid"] is not None else (s["fv"] if s["fv"] is not None else 0.5)
        capital = abs(pos) * (price if pos > 0 else 1 - price)
        o, tp = ours.get(e, 0.0) / hours, tape.get(e, 0.0) / hours
        flow = max(o, tp)
        h_flat = abs(pos) / (flow / 2) if flow > 0 else (0.0 if not pos else float("inf"))
        if not pos and not flow:
            continue
        rows.append({"eid": e, "label": s["label"], "fills_h": counts.get(e, 0) / hours, "ours_h": o, "tape_h": tp,
                     "flow_h": flow, "pos": pos, "capital": capital, "h_flat": h_flat,
                     "dead": flow < dead and abs(pos) >= 1})
    rows.sort(key=lambda r: -r["capital"])
    return rows, hi


def summary(rows, dead=50.0):
    """Totals as lines of text."""
    cap = sum(r["capital"] for r in rows)
    d = [r for r in rows if r["dead"]]
    slow = [r for r in rows if r["pos"] and r["fills_h"] < 2]
    long_ = [r for r in rows if r["pos"] and r["h_flat"] > 24]
    flats = sorted(r["h_flat"] for r in rows if r["pos"])
    med = flats[len(flats) // 2] if flats else 0.0
    return [f"capital in positions (at mid): {cap / 1000:.1f}k in {sum(1 for r in rows if r['pos'])} markets",
            f"dead-turnover markets (< {dead:g} sh/h): {len(d)} holding {sum(r['capital'] for r in d) / 1000:.1f}k",
            f"markets with < 2 fills/h (ours): {len(slow)} holding {sum(r['capital'] for r in slow) / 1000:.1f}k",
            f"capital needing > 24 h to flatten at half the flow: {sum(r['capital'] for r in long_) / 1000:.1f}k "
            f"in {len(long_)} markets; median hours to flatten {med:.1f}"]


def fmt_h(x):
    return "inf" if x == float("inf") else f"{x:.1f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    help="folder with fills.csv and market_data.sqlite (default: the bot's folder)")
    ap.add_argument("--fills", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--dead", type=float, default=50.0)
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args(argv)
    fills = a.fills or os.path.join(a.dir, "fills.csv")
    db = a.db or os.path.join(a.dir, "market_data.sqlite")
    rows, hi = table(fills, db, a.hours, a.dead)
    end = datetime.fromtimestamp(hi, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"turnover over the {a.hours:g} h to {end} ({len(rows)} markets with a position or flow)")
    print(f"{'market':<30} {'fills/h':>7} {'ours sh/h':>9} {'tape sh/h':>9} {'flow sh/h':>9} {'pos':>8} "
          f"{'capital':>8} {'h-flat':>7}")
    for r in rows[:a.top]:
        print(f"{r['label'][:30]:<30} {r['fills_h']:7.2f} {r['ours_h']:9.0f} {r['tape_h']:9.0f} {r['flow_h']:9.0f} "
              f"{r['pos']:8.0f} {r['capital']:8.0f} {fmt_h(r['h_flat']):>7}{' dead' if r['dead'] else ''}")
    for line in summary(rows, a.dead):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
