"""Build tests/live_start.json (the simulator's SIM_START=live state) from a status.json, the recorder's snapshots
table and fills.csv. Usage: python tests/live_start_extract.py STATUS.json MARKET.db FILLS.csv [OUT.json]

Per exchange: label, party, race, the last snapshot's best bid/ask (others + ours, as recorded), fair value,
Polymarket reference and position (status.json wins), plus FIFO lots rebuilt as Bot.seed_lots does (newest fills in
the position's direction; shares not covered are stamped at the first fill time). Times are seconds before the
snapshot time (negative)."""
import csv
import json
import sqlite3
import sys
from datetime import datetime


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main(status_path, db_path, fills_path, out_path):
    status = json.load(open(status_path))
    t0 = ts(status["updated"])
    c = sqlite3.connect(db_path)
    rows = c.execute("""select s.eid, s.label, s.best_bid, s.best_ask, s.fair_value, s.reference, s.position, s.ts
                        from snapshots s join (select eid, max(ts) m from snapshots where ts <= ? group by eid) l
                        on s.eid = l.eid and s.ts = l.m""", (status["updated"],)).fetchall()
    fills = list(csv.DictReader(open(fills_path)))
    first = min(ts(f["filled_at"]) for f in fills)
    nfills = {}
    for f in fills:
        nfills[f["exchange_id"]] = nfills.get(f["exchange_id"], 0) + 1
    pos_by_label = status.get("positions") or {}
    out = []
    for eid, label, bb, ba, fv, ref, pos, when in rows:
        party = {"Dem": "Democratic", "Rep": "Republican"}.get(label.split()[0])
        race = label.split(" ", 1)[1] if party else label
        q = float(pos_by_label.get(label, pos or 0) or 0)
        lots, need = [], abs(q)
        for f in reversed(fills):                     # newest first, fills that built the position
            if not need or f["exchange_id"] != eid or f["our_side"] not in ("bid", "ask"):
                continue
            if (f["our_side"] == "bid") != (q > 0):
                continue
            x = min(need, abs(float(f["qty"] or 0)))
            need -= x
            lots.append([x if q > 0 else -x, round(ts(f["filled_at"]) - t0)])
        lots.reverse()
        if need > 0.5:
            lots.insert(0, [need if q > 0 else -need, round(first - t0)])
        out.append(dict(eid=eid, label=label, party=party, race=race, best_bid=bb, best_ask=ba, fv=fv, ref=ref,
                        pos=q, lots=lots if q else [], fills16h=nfills.get(eid, 0)))
    json.dump(dict(updated=status["updated"], account_value=status["account_value"],
                   locked_in_orders=status["locked_in_orders"], party_delta=status.get("party_delta"),
                   markets=out), open(out_path, "w"), indent=0)
    print(f"{len(out)} markets, {sum(1 for m in out if m['pos'])} positions -> {out_path}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3] if len(a) > 3 else "tests/live_start.json")
