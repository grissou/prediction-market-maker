"""Shared loaders for the 4 Oct live analysis (snap04). Read-only."""
import csv, json, sqlite3, bisect, gzip, re
from collections import defaultdict
from datetime import datetime, timezone
SNAP = "/home/claude/snap04"
LO, HI = 0.15, 0.85

def ts_of(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d %H:%M:%S")

def race_party(label):
    p, r = label.split(" ", 1)
    return r, p

def load_snapshots(since="2026-10-03T20:00"):
    """{ts: {eid: row}} with a race-scaled ref 'p' added (all legs with a ref -> r / sum, else raw)."""
    c = sqlite3.connect(f"{SNAP}/md.sqlite")
    by_ts = defaultdict(dict)
    for ts, eid, label, bb, ba, fv, ref, ob, oa, pos in c.execute(
            "select ts,eid,label,best_bid,best_ask,fair_value,reference,our_bid,our_ask,position from snapshots "
            "where ts>=? order by ts", (since,)):
        by_ts[ts][eid] = dict(eid=eid, label=label, bb=bb, ba=ba, fv=fv, ref=ref, ob=ob, oa=oa, pos=pos)
    out = {}
    for ts, rows in by_ts.items():
        races = defaultdict(list)
        for r in rows.values():
            races[race_party(r["label"])[0]].append(r)
        for race, mem in races.items():
            tot = sum(m["ref"] for m in mem) if all(m["ref"] is not None for m in mem) else None
            for m in mem:
                m["race"] = race
                m["p"] = None if m["ref"] is None else (m["ref"] / tot if tot and len(mem) > 1 else m["ref"])
        out[ts_of(ts)] = rows
    return out

class Series:
    def __init__(self, snaps):
        self.ts = sorted(snaps)
        self.s = snaps
    def at(self, t):
        """the snapshot nearest t (rows by eid)"""
        i = bisect.bisect_left(self.ts, t)
        cands = [j for j in (i - 1, i) if 0 <= j < len(self.ts)]
        j = min(cands, key=lambda j: abs(self.ts[j] - t))
        return self.ts[j], self.s[self.ts[j]]
    def p(self, eid, t):
        _, rows = self.at(t)
        r = rows.get(eid)
        return None if r is None else r["p"]

def load_fills():
    rows = []
    for r in csv.DictReader(open(f"{SNAP}/fills.csv")):
        rows.append(r)
    return rows

def load_notes():
    return {str(k): v for k, v in json.load(open(f"{SNAP}/order_notes.json")).items()}

def journal_lines(pat=None):
    rx = re.compile(pat) if pat else None
    with gzip.open(f"{SNAP}/journal_2026-10-03_2247_to_now.log.gz", "rt", errors="replace") as f:
        for line in f:
            if rx is None or rx.search(line):
                yield line.rstrip("\n")

def latest_books():
    c = sqlite3.connect(f"{SNAP}/md.sqlite")
    out = {}
    for ts, eid, b, a in c.execute("select ts,eid,bids,asks from books order by ts"):
        out[eid] = (ts, json.loads(b), json.loads(a))
    return out
