"""Which rule does the exchange use to mark open positions? (DATA_REPORT_2 section 10(d), the deciding test.)

Matches each recorded per-position `currentPrice` (market_data.sqlite, table positions, written by the bot with
record_positions on) against candidate marks computed from the recorded tournament trades (table trades) up to
the moment the position was read:
  vwap_last_N   volume-weighted average of the last N trades, N = 1..20
  avg_last_N    simple average of the last N trades, N = 1..20
  vwap_Wmin     volume-weighted average of the trades in the last W minutes (W = 5, 15, 30, 60); the last trade
                when there is none in the window
  ema_A         exponential average over trades, weight A per trade (A = 0.1, 0.3)
Prints, per rule: rows compared, RMS misfit (cents), share matching to 4 decimals and the price convention for
short (NO) positions that fits better (the YES price as is, or 1 - it). Then the test that separates the families:
does currentPrice change between two reads with NO trade in between (old trades ageing out -> time window), or
only when someone trades (last-N, EMA)? Also lists positions read before any recorded trade (the fallback).

The trades table only has trades seen while the bot ran: a rule needing older trades is skipped for that row.
Standard library only. Run:  python3 analysis/mark_rule.py [market_data.sqlite]"""
import bisect
import math
import sqlite3
import sys
from collections import defaultdict

LAST_N = range(1, 21)
WINDOWS_MIN = (5, 15, 30, 60)
EMA_ALPHAS = (0.1, 0.3)
CHANGE_EPS = 5e-5                     # currentPrice has 4 decimals


def rule_names():
    return ([f"vwap_last_{n}" for n in LAST_N] + [f"avg_last_{n}" for n in LAST_N]
            + [f"vwap_{w}min" for w in WINDOWS_MIN] + [f"ema_{a}" for a in EMA_ALPHAS])


class Tape:
    """One market's trades, oldest first, with running sums so every candidate is O(log n)."""

    def __init__(self, trades):
        self.t = [x[0] for x in trades]
        self.p = [x[1] for x in trades]
        q = [abs(x[2]) if x[2] else 1.0 for x in trades]     # no size reported: weigh it as 1
        self.cpq, self.cq, self.cp = [0.0], [0.0], [0.0]
        for pi, qi in zip(self.p, q):
            self.cpq.append(self.cpq[-1] + pi * qi)
            self.cq.append(self.cq[-1] + qi)
            self.cp.append(self.cp[-1] + pi)
        self.ema = {}
        for a in EMA_ALPHAS:
            m, out = None, []
            for pi in self.p:
                m = pi if m is None else a * pi + (1 - a) * m
                out.append(m)
            self.ema[a] = out

    def count_upto(self, ts):
        return bisect.bisect_right(self.t, ts)

    def trades_between(self, t0, t1):
        """Trades with t0 < time <= t1."""
        return bisect.bisect_right(self.t, t1) - bisect.bisect_right(self.t, t0)

    def marks(self, ts):
        """{rule: mark} at time ts (rules lacking enough trades are left out)."""
        k = self.count_upto(ts)
        out = {}
        if k == 0:
            return out
        for n in LAST_N:
            if k >= n:
                j = k - n
                w = self.cq[k] - self.cq[j]
                if w > 0:
                    out[f"vwap_last_{n}"] = (self.cpq[k] - self.cpq[j]) / w
                out[f"avg_last_{n}"] = (self.cp[k] - self.cp[j]) / n
        for wmin in WINDOWS_MIN:
            j = bisect.bisect_right(self.t, ts - 60 * wmin)
            j = min(j, k - 1)                                  # none in the window: the last trade
            w = self.cq[k] - self.cq[j]
            if w > 0:
                out[f"vwap_{wmin}min"] = (self.cpq[k] - self.cpq[j]) / w
        for a in EMA_ALPHAS:
            out[f"ema_{a}"] = self.ema[a][k - 1]
        return out


def load(path):
    db = sqlite3.connect(path)
    have = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    missing = {"positions", "trades"} - have
    if missing:
        return None, None, missing
    trades = defaultdict(list)
    for ts, eid, price, qty in db.execute("select ts, eid, price, quantity from trades "
                                          "where price is not null order by ts"):
        trades[str(eid)].append((float(ts), float(price), float(qty) if qty is not None else None))
    rows = [(float(ts), prev, str(eid), qty, cp) for ts, prev, eid, qty, cp in db.execute(
        "select ts, prev_ts, eid, quantity, current_price from positions order by eid, ts")]
    return {e: Tape(v) for e, v in trades.items()}, rows, set()


def analyse(path):
    """-> {"ranking": [(rule, rms, n, exact_share, convention)], "changes", "changes_without_trade",
    "examples", "no_trade_rows", "rows"} (or {"missing": {...}})."""
    tapes, rows, missing = load(path)
    if missing:
        return {"missing": missing}
    sq = {conv: defaultdict(float) for conv in ("as_is", "no_flip")}
    exact = {conv: defaultdict(int) for conv in sq}
    n = defaultdict(int)
    no_trade = []
    for ts, _, eid, qty, cp in rows:
        if cp is None:
            continue
        tape = tapes.get(eid)
        marks = tape.marks(ts) if tape else {}
        if not marks:
            no_trade.append((ts, eid, cp))
            continue
        flip = qty is not None and qty < 0
        for rule, m in marks.items():
            n[rule] += 1
            for conv, target in (("as_is", cp), ("no_flip", 1 - cp if flip else cp)):
                d = target - m
                sq[conv][rule] += d * d
                exact[conv][rule] += abs(d) < CHANGE_EPS
    ranking = []
    for rule in rule_names():
        if not n[rule]:
            continue
        conv = min(sq, key=lambda c: sq[c][rule])
        ranking.append((rule, math.sqrt(sq[conv][rule] / n[rule]), n[rule], exact[conv][rule] / n[rule], conv))
    ranking.sort(key=lambda r: (r[1], r[0]))

    changes = no_trade_changes = 0
    examples = []
    last = {}
    for ts, prev_ts, eid, qty, cp in rows:                    # rows are ordered by eid, ts
        before = last.get(eid)
        last[eid] = (ts, cp)
        if before is None or before[1] is None or cp is None or abs(cp - before[1]) <= CHANGE_EPS:
            continue
        changes += 1
        since = prev_ts if prev_ts is not None else before[0]
        tape = tapes.get(eid)
        if not tape or tape.trades_between(since, ts) == 0:
            no_trade_changes += 1
            if len(examples) < 5:
                examples.append((eid, since, ts, before[1], cp))
    return {"ranking": ranking, "changes": changes, "changes_without_trade": no_trade_changes,
            "examples": examples, "no_trade_rows": no_trade, "rows": len(rows)}


def main(argv):
    path = argv[0] if argv else "market_data.sqlite"
    res = analyse(path)
    if "missing" in res:
        print(f"{path}: no table(s) {', '.join(sorted(res['missing']))} - run the bot with record_positions "
              "and record_books on first")
        return 1
    print(f"{path}: {res['rows']} position rows")
    print(f"{'rule':<14} {'rows':>6} {'RMS (c)':>8} {'exact 4dp':>9}  NO-position price")
    for rule, rms, cnt, ex, conv in res["ranking"]:
        print(f"{rule:<14} {cnt:>6} {100 * rms:>8.3f} {100 * ex:>8.1f}%  {'1 - YES' if conv == 'no_flip' else 'as recorded'}")
    if not res["ranking"]:
        print("(no position row has a recorded trade before it yet)")
    c, nt = res["changes"], res["changes_without_trade"]
    print(f"\ncurrentPrice changes between reads: {c}; with NO trade in between: {nt}"
          + (f" ({100 * nt / c:.0f}%)" if c else ""))
    if c:
        print("  -> " + ("changes without trades: old trades ageing out = a TIME-WINDOW rule" if nt
                         else "changes only with trades: a last-N or EMA rule (not a time window)"))
    for eid, t0, t1, p0, p1 in res["examples"]:
        print(f"  e.g. {eid}: {p0:.4f} -> {p1:.4f} between ts {t0:.0f} and {t1:.0f}, no trade")
    if res["no_trade_rows"]:
        print(f"\npositions read before any recorded trade (the fallback): {len(res['no_trade_rows'])}, e.g. "
              + ", ".join(f"{e} @ {p:.4f}" for _, e, p in res["no_trade_rows"][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
