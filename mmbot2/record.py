"""
The recorder: once a minute, one row per priced market and one for the account, in run_dir/market_data.sqlite.

OWNS     the sqlite file, in the old bot's schema (mmbot/status.py `snapshots` and `account`), so the owner's
         analyses and the paper read it unchanged: pd.read_sql("select * from snapshots", conn).
NEVER    spends a request (it reads the cycle's View and the bulk tops already read), or stops a cycle: a write
         error is logged and the row is lost.
ORIGIN   The rewrite dropped it (README §9); the fix brief of 10 Oct put it back, minimal: no book or trade tables.
"""
import logging
import os
import sqlite3

from mmbot2 import pricing

log = logging.getLogger("mm2")

RECORD_FILE = "market_data.sqlite"
RECORD_EVERY_S = 60.0


class Recorder:
    def __init__(self, run_dir):
        self.path, self.db, self.last = os.path.join(run_dir, RECORD_FILE), None, -1e9

    def open(self):
        db = sqlite3.connect(self.path)
        db.execute("""CREATE TABLE IF NOT EXISTS snapshots (ts TEXT, mode TEXT, eid TEXT, label TEXT, best_bid REAL,
                      best_ask REAL, fair_value REAL, reference REAL, our_bid REAL, our_ask REAL, position REAL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS account (ts TEXT, mode TEXT, account_value REAL, locked_in_orders REAL,
                      worst_case_loss REAL, party_delta REAL, orders_resting INTEGER, liquidation_value REAL)""")
        db.execute("CREATE INDEX IF NOT EXISTS snapshots_eid_ts ON snapshots (eid, ts)")
        return db

    def record(self, view, tops, refs, ours, status):
        """Every RECORD_EVERY_S: per priced market the bulk best bid/ask (ours included, as the old recorder), fair
        value, raw Polymarket, our best resting bid/ask and position; the account row from status.json's figures."""
        if view.mono - self.last < RECORD_EVERY_S:
            return
        self.last = view.mono
        ts, mode = view.now.isoformat(), "live" if status["mode"] == "live" else "dry"
        rows = []
        for e, p in sorted(view.p.items()):
            m = view.markets[e]
            bids = [o.price for o in ours.get(e, []) if o.is_bid]
            asks = [o.price for o in ours.get(e, []) if not o.is_bid]
            rows.append((ts, mode, e, m.label, *tops.get(e, (None, None)), round(p, 4),
                         refs.get(pricing.ref_key(m)), max(bids, default=None), min(asks, default=None),
                         view.positions.get(e, 0.0)))
        try:
            self.db = self.db or self.open()
            self.db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.execute("INSERT INTO account VALUES (?,?,?,?,?,?,?,?)",
                            (ts, mode, status["account_value"], None, status["worst_case_loss"],
                             status["bloc_delta"], status["orders_resting"], None))
            self.db.commit()
        except sqlite3.Error as err:
            log.warning("recorder: %s", err)
