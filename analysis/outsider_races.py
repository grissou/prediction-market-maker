"""Races where an UNLISTED candidate is priced: the legs' Polymarket references add up to well under 1.

Usage: python analysis/outsider_races.py [market_data.sql or .sqlite] [--min-sum 0.99]
Reads the snapshots table (ts, eid, label, reference) and ref_map.json; groups legs by race title; reports, per race,
the number of legs, the median sum of the legs' references over the snapshots where every leg had one, and the share
of snapshots with sum < min_sum. Standard library only."""
import json, os, re, sqlite3, statistics, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    if path.endswith(".sql"):
        db = sqlite3.connect(":memory:")
        db.executescript(open(path, encoding="utf-8").read())
        return db
    return sqlite3.connect(path)


def race_of(label):
    # labels look like "Rep Ohio Senate", "Dem U.S. House", "Ind RI Gov": the race is the label without the party
    return re.sub(r"^(Rep|Dem|Ind|Lib|Grn|Oth)\s+", "", label.strip())


def main(argv):
    path = next((a for a in argv if not a.startswith("--")), None)
    min_sum = float(argv[argv.index("--min-sum") + 1]) if "--min-sum" in argv else 0.99
    if path is None:
        for cand in ("market_data.sqlite", "market_data.sql"):
            if os.path.exists(os.path.join(HERE, cand)):
                path = os.path.join(HERE, cand)
    db = load(path)
    rows = db.execute("SELECT ts, eid, label, reference FROM snapshots WHERE label IS NOT NULL").fetchall()
    legs, by_ts = {}, {}
    for ts, eid, label, ref in rows:
        race = race_of(label)
        legs.setdefault(race, set()).add(str(eid))
        if ref is not None:
            by_ts.setdefault((race, ts), {})[str(eid)] = float(ref)
    out = []
    for race, eids in legs.items():
        sums = [sum(d.values()) for (r, ts), d in by_ts.items() if r == race and len(d) == len(eids)]
        if not sums:
            out.append((race, len(eids), None, None, 0))
            continue
        med = statistics.median(sums)
        low = sum(1 for x in sums if x < min_sum) / len(sums)
        out.append((race, len(eids), med, low, len(sums)))
    flagged = [o for o in out if o[1] >= 3 or (o[2] is not None and o[2] < min_sum) or o[2] is None]
    flagged.sort(key=lambda o: (o[2] if o[2] is not None else 0))
    print(f"{len(legs)} races; {len(flagged)} flagged (3+ legs, median reference sum < {min_sum}, or no complete reading)")
    print(f"{'race':34} legs  median_sum  share<min  samples")
    for race, n, med, low, k in flagged:
        print(f"{race:34} {n:4}  {('%.3f' % med) if med is not None else '   -  ':>10}  "
              f"{('%.0f%%' % (100 * low)) if low is not None else '  -':>9}  {k:7}")
    return flagged


if __name__ == "__main__":
    main(sys.argv[1:])
