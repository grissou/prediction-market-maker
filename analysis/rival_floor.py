"""Rival-floor map (Explorer idea #5): how close OTHER traders quote to the fair value in each market, how often
the best price changes, and a recommended per-market min_edge -> market_edge.json (read by mm_bot.py when
market_edge_enabled is on).

Source: the recorder's `books` table (other traders' top levels, our own orders already removed; one row per
change) when it has rows, sampled on a 60-s grid; else the `snapshots` table, using only the sides where our own
quote was not at the best (best_bid/best_ask there include ours).

Per market:
  rival_half_spread_c  median distance of the other traders' best bid / best ask from the fair value (the latest
                       snapshot fair value, else the mid), both sides pooled, in cents
  top_change_rate      share of the 60-s intervals in which the best bid or best ask price changed
  samples              intervals (books) or snapshots with at least one rival side
  min_edge             clip(rival_half_spread + margin, lo, hi) rounded (half up) to the 0.5c grid, in dollars
Markets with fewer than --min-samples samples get no entry.

Standard library only (copy this one file to the server):
  python3 rival_floor.py [market_data.sqlite] [-o market_edge.json] [--hours H] [--min-samples N]
  python3 rival_floor.py --sql dump.sql      (a .sql dump: loaded into a temporary in-memory database)
"""
import argparse
import bisect
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

TICK = 0.005
EPS = 1e-9


def parse_ts(ts):
    """Recorder timestamps: epoch seconds (books) or ISO text (snapshots) -> epoch seconds, or None."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recommend(half_spread, margin=0.0025, lo=0.01, hi=0.02):
    """clip(half_spread + margin, lo, hi), rounded half up to the 0.5c grid (dollars)."""
    x = min(hi, max(lo, half_spread + margin))
    return round(int(x / TICK + 0.5 + EPS) * TICK, 4)


def has_rows(db, table):
    try:
        return db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def snapshot_rows(db, since=None):
    """[(t, eid, label, best_bid, best_ask, fair_value, our_bid, our_ask)] sorted by time."""
    if not has_rows(db, "snapshots"):
        return []
    out = []
    for ts, eid, label, bb, ba, fv, ob, oa in db.execute(
            "SELECT ts, eid, label, best_bid, best_ask, fair_value, our_bid, our_ask FROM snapshots"):
        t = parse_ts(ts)
        if t is not None and (since is None or t >= since):
            out.append((t, str(eid), label, bb, ba, fv, ob, oa))
    out.sort(key=lambda r: r[0])
    return out


def rival_dists(bid, ask, fv):
    """Rival distances from the fair value (else the mid) for the sides present, never negative."""
    ref = fv if fv is not None else ((bid + ask) / 2 if bid is not None and ask is not None else None)
    if ref is None:
        return []
    return [max(0.0, d) for d in ((ref - bid) if bid is not None else None,
                                  (ask - ref) if ask is not None else None) if d is not None]


def _new():
    return {"d": [], "samples": 0, "iv": 0, "chg": 0}


def _summarise(per, labels, min_samples, margin, lo, hi):
    out = {}
    for eid, m in per.items():
        if m["samples"] < min_samples or not m["d"]:
            continue
        hs = statistics.median(m["d"])
        out[eid] = {"label": labels.get(eid, eid), "rival_half_spread_c": round(100 * hs, 3),
                    "top_change_rate": round(m["chg"] / m["iv"], 3) if m["iv"] else None,
                    "samples": m["samples"], "min_edge": recommend(hs, margin, lo, hi)}
    return out


def stats_from_snapshots(db, since=None, min_samples=30, margin=0.0025, lo=0.01, hi=0.02, max_gap=150.0):
    """Per eid from the snapshots: only sides where our own quote was not at the best count. An interval is two
    consecutive snapshots at most max_gap apart with a rival side in both; it changed if that side's price did."""
    per, labels, prev = defaultdict(_new), {}, {}
    for t, eid, label, bb, ba, fv, ob, oa in snapshot_rows(db, since):
        labels[eid] = label or eid
        rb = bb if bb is not None and (ob is None or ob < bb - EPS) else None   # rival best bid
        ra = ba if ba is not None and (oa is None or oa > ba + EPS) else None   # rival best ask
        m = per[eid]
        d = rival_dists(rb, ra, fv)
        if d:
            m["d"].extend(d)
            m["samples"] += 1
        p = prev.get(eid)
        if p and t - p[0] <= max_gap:
            sides = [(x, y) for x, y in ((p[1], rb), (p[2], ra)) if x is not None and y is not None]
            if sides:
                m["iv"] += 1
                m["chg"] += any(abs(x - y) > EPS for x, y in sides)
        prev[eid] = (t, rb, ra)
    return _summarise(per, labels, min_samples, margin, lo, hi)


def best_price(levels_json):
    """The first level's price from a books row's JSON [[price, size], ...], or None."""
    try:
        lv = json.loads(levels_json) if levels_json else []
        return float(lv[0][0]) if lv else None
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def stats_from_books(db, since=None, min_samples=30, margin=0.0025, lo=0.01, hi=0.02, step=60.0):
    """Per eid from the books table (one row per change of the other traders' top levels), sampled every `step`
    seconds from the market's first row to its last (the state in force = the latest row; a long stretch without
    rows reads as "unchanged", so time the bot was down counts as quiet). The fair value is the latest
    snapshot's at most 300 s old, else the mid."""
    rows = defaultdict(list)
    for ts, eid, bids, asks in db.execute("SELECT ts, eid, bids, asks FROM books"):
        t = parse_ts(ts)
        if t is not None and (since is None or t >= since):
            rows[str(eid)].append((t, best_price(bids), best_price(asks)))
    fvs, labels = defaultdict(list), {}
    for t, eid, label, _bb, _ba, fv, _ob, _oa in snapshot_rows(db, since):
        labels[eid] = label or eid
        if fv is not None:
            fvs[eid].append((t, fv))
    per = defaultdict(_new)
    for eid, rs in rows.items():
        rs.sort(key=lambda r: r[0])
        m, ft = per[eid], [x[0] for x in fvs.get(eid, [])]
        i, prev, t = 0, None, rs[0][0]
        while t <= rs[-1][0] + EPS:
            while i + 1 < len(rs) and rs[i + 1][0] <= t + EPS:
                i += 1
            _, bb, ba = rs[i]
            k = bisect.bisect_right(ft, t) - 1
            fv = fvs[eid][k][1] if k >= 0 and t - ft[k] <= 300 else None
            d = rival_dists(bb, ba, fv)
            if d:
                m["d"].extend(d)
                m["samples"] += 1
            if prev is not None:
                m["iv"] += 1
                m["chg"] += prev != (bb, ba)
            prev = (bb, ba)
            t += step
    return _summarise(per, labels, min_samples, margin, lo, hi)


def rival_floor(db, since=None, **kw):
    """(source, {eid: entry}) from the books table when it has rows, else from the snapshots."""
    if has_rows(db, "books"):
        return "books", stats_from_books(db, since, **kw)
    return "snapshots", stats_from_snapshots(db, since, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("db", nargs="?", default="market_data.sqlite")
    ap.add_argument("--sql", help="a .sql dump to load instead of a database file")
    ap.add_argument("-o", "--out", default="market_edge.json")
    ap.add_argument("--hours", type=float, help="only the last H hours")
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--margin-c", type=float, default=0.25)
    ap.add_argument("--top", type=int, default=12, help="widest markets printed")
    a = ap.parse_args(argv)
    db = sqlite3.connect(":memory:" if a.sql else a.db)
    if a.sql:
        with open(a.sql) as f:
            db.executescript(f.read())
    since = time.time() - a.hours * 3600 if a.hours else None
    src, st = rival_floor(db, since, min_samples=a.min_samples, margin=a.margin_c / 100)
    meta = {"source": src, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "min_samples": a.min_samples, "margin_c": a.margin_c, "markets": len(st)}
    with open(a.out, "w") as f:
        json.dump({"_meta": meta, **dict(sorted(st.items()))}, f, indent=1)
    dist = defaultdict(int)
    for v in st.values():
        dist[v["min_edge"]] += 1
    print(f"{len(st)} markets from {src} -> {a.out}; min_edge: "
          + ", ".join(f"{100 * k:g}c x{n}" for k, n in sorted(dist.items())))
    hs = sorted(v["rival_half_spread_c"] for v in st.values())
    if hs:
        q = lambda p: hs[min(len(hs) - 1, int(p * len(hs)))]
        print("rival half-spread c, 10/25/50/75/90th pct: " + " / ".join(f"{q(p):.2f}" for p in (.1, .25, .5, .75, .9)))
    for eid, v in sorted(st.items(), key=lambda kv: -kv[1]["rival_half_spread_c"])[:a.top]:
        rate = v["top_change_rate"]
        print(f"  {v['label'][:28]:28} {eid:>6} half {v['rival_half_spread_c']:5.2f}c  top chg "
              f"{'-' if rate is None else f'{rate:.2f}':>5}  n {v['samples']:5d}  min_edge {100 * v['min_edge']:g}c")
    return 0


if __name__ == "__main__":
    sys.exit(main())
