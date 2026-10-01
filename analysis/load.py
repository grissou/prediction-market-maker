"""Loaders for the day-one snapshot (branch ops-snapshot-2026-10-01).
Set SNAP to the directory holding the extracted snapshot files (default ./snap)."""
import os, re, sqlite3
import pandas as pd

SNAP = os.environ.get("SNAP", "snap")


def db():
    path = os.path.join(SNAP, "market.db")
    if not os.path.exists(path):
        c = sqlite3.connect(path)
        c.executescript(open(os.path.join(SNAP, "market_data.sql")).read())
        c.commit()
        c.close()
    return sqlite3.connect(path)


def snapshots(live=True):
    s = pd.read_sql("select * from snapshots" + (" where mode='live'" if live else ""), db())
    s["ts"] = pd.to_datetime(s["ts"])
    s["party"] = s["label"].str.split().str[0]
    s["race"] = s["label"].str.split(n=1).str[1]
    return s


def account(live=True):
    a = pd.read_sql("select * from account" + (" where mode='live'" if live else ""), db())
    a["ts"] = pd.to_datetime(a["ts"])
    return a


def fills():
    f = pd.read_csv(os.path.join(SNAP, "fills.csv"))
    f["filled_at"] = pd.to_datetime(f["filled_at"])
    return f


LINE = re.compile(r"^(\S+) .*? INFO\s+(.+?)\s+fv ([\d.]+)(?: \(ref ([\d.-]+|\?|None|-)\))? inv\s+([+-]?\d+) race\s+([+-]?\d+) \| "
                  r"bid\s+([\d.]+|-)(?: x(\d+))?\s+ask\s+([\d.]+|-)(?: x(\d+))?")


def quotes():
    """Per-market quote lines from the journal: ts, label, fv, ref, inv, race_inv, bid, bid_sz, ask, ask_sz."""
    rows = []
    with open(os.path.join(SNAP, "journal_2026-10-01.log")) as fh:
        for line in fh:
            if " fv " not in line:
                continue
            m = LINE.match(line)
            if not m:
                continue
            ts, lab, fv, ref, inv, rinv, b, bs, a, as_ = m.groups()
            num = lambda x: float(x) if x not in (None, "-", "?", "None") else None
            rows.append((ts, lab.strip(), float(fv), num(ref), int(inv), int(rinv), num(b), num(bs), num(a), num(as_)))
    q = pd.DataFrame(rows, columns=["ts", "label", "fv", "ref", "inv", "race_inv", "bid", "bid_sz", "ask", "ask_sz"])
    q["ts"] = pd.to_datetime(q["ts"], format="%Y-%m-%dT%H:%M:%S%z")
    return q


def events():
    """Errors/warnings of interest with timestamps: kind in {409, 429, cycle_failed, cancel_fail, ws_down, traded_immediately}."""
    pats = {"409": "REQUEST_IN_FLIGHT", "429": "RATE LIMITED", "cycle_failed": "cycle failed",
            "cancel_fail": "could not confirm cancels", "ws_down": "realtime feed down",
            "traded_immediately": "traded", "missed_msg": "realtime: missed"}
    rows = []
    with open(os.path.join(SNAP, "journal_2026-10-01.log")) as fh:
        for line in fh:
            for k, p in pats.items():
                if p in line and (k != "traded_immediately" or "immediately" in line):
                    rows.append((line[:24], k, line[50:].strip()[:200]))
    e = pd.DataFrame(rows, columns=["ts", "kind", "text"])
    e["ts"] = pd.to_datetime(e["ts"], format="%Y-%m-%dT%H:%M:%S%z")
    return e
