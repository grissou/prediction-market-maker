"""Stdlib-only loaders for the 2026-10-01/02 overnight data (no pandas needed). Python 3.10+.
Set DATA2 to the directory with journal.log, fills.csv, status.json, market_data.sql
(default: the Analyst's scratchpad). A sqlite cache market.db is built next to the dump once.
Times are UTC epoch seconds (float). Helpers: ts(), hh() (hour label), real equity, quote lines,
summary lines, fills with inferred sides, snapshot series per market."""
import bisect, csv, json, os, re, sqlite3
from collections import defaultdict
from datetime import datetime, timezone

DATA2 = os.environ.get("DATA2", "/tmp/claude-0/-home-user-prediction-market-maker/432ffe77-9955-5f86-8f41-9afd68d13411/scratchpad")
J = os.path.join(DATA2, "journal.log")


def ts(s):
    """ISO time ('2026-10-01T16:00:00Z', '...+0000', '2026-10-01 16:00:00') -> epoch seconds."""
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def hh(t, fmt="%d %H"):
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


OPEN = ts("2026-10-01T16:00:00Z")
FIRST_RO = ts("2026-10-01T22:48:00Z")      # first REDUCE-ONLY
REAL_FROM = ts("2026-10-02T05:03:16Z")     # kill-switch detector switched to 'already included'
OUT0, OUT1 = ts("2026-10-02T07:32:00Z"), ts("2026-10-02T07:46:00Z")   # exchange outage
DEPLOY = ts("2026-10-02T08:08:23Z")


def db():
    path = os.path.join(DATA2, "market.db")
    if not os.path.exists(path):
        c = sqlite3.connect(path)
        c.executescript(open(os.path.join(DATA2, "market_data.sql")).read())
        c.commit(); c.close()
    return sqlite3.connect(path)


def account():
    """Rows (t, account_value, locked, worst, party_delta, resting, real_equity)."""
    out = []
    for r in db().execute("select ts,account_value,locked_in_orders,worst_case_loss,party_delta,orders_resting "
                          "from account where mode='live' order by ts"):
        t = ts(r[0])
        real = r[1] - r[2] if t < REAL_FROM else r[1]
        out.append((t,) + tuple(r[1:]) + (real,))
    return out


def snapshots():
    """(dict ts -> {label: row}, sorted ts list); row = dict(eid,bb,ba,fv,ref,ob,oa,pos)."""
    snap = defaultdict(dict)
    for r in db().execute("select ts,eid,label,best_bid,best_ask,fair_value,reference,our_bid,our_ask,position "
                          "from snapshots where mode='live'"):
        snap[ts(r[0])][r[2]] = dict(eid=r[1], bb=r[3], ba=r[4], fv=r[5], ref=r[6], ob=r[7], oa=r[8], pos=r[9] or 0.0)
    return snap, sorted(snap)


def eid_labels():
    return {r[0]: r[1] for r in db().execute("select distinct eid,label from snapshots where mode='live'")}


class Series:
    """Per-label time series from snapshots for 'row at or after t' lookups."""
    def __init__(self, snap, tss):
        self.t = defaultdict(list); self.r = defaultdict(list)
        for t in tss:
            for lab, row in snap[t].items():
                self.t[lab].append(t); self.r[lab].append(row)

    def at(self, lab, t, after=True, tol=600):
        T = self.t.get(lab)
        if not T:
            return None
        i = bisect.bisect_left(T, t) if after else bisect.bisect_right(T, t) - 1
        if 0 <= i < len(T) and abs(T[i] - t) <= tol:
            return self.r[lab][i]
        return None


def mid(row):
    if row and row["bb"] is not None and row["ba"] is not None and row["ba"] > row["bb"]:
        return (row["bb"] + row["ba"]) / 2
    return None


QL = re.compile(r"^(\S+) .*? INFO\s+(.+?)\s+fv\s+([\d.]+|-)\s+(?:\(ref ([\d.-]+|\?|None|-)\) )?inv\s+([+-]?\d+) race\s+([+-]?\d+) \| "
                r"bid\s+([\d.]+|-)(?: x(\d+))?\s+ask\s+([\d.]+|-)(?: x(\d+))?")
SUM = re.compile(r"^(\S+) .*? INFO\s+(realtime|polling[^|]*) \| account (\S+) \(locked in orders (\d+), ([^)]+)\) \| "
                 r"worst-case loss (\d+)(?: \(risk (\d+)\))?( -> REDUCE-ONLY)? \| party delta ([+-]?\d+) \| priced (\d+)/(\d+) \| resting (\d+)")


def _num(x):
    return None if x in (None, "", "-", "?", "None") else float(x)


def quotes():
    """Journal per-market quote lines (logged when the quote changes):
    list of dict(t, label, fv, ref, inv, race, bid, bsz, ask, asz)."""
    out = []
    with open(J) as fh:
        for line in fh:
            if " fv " not in line:
                continue
            m = QL.match(line)
            if m:
                g = m.groups()
                out.append(dict(t=ts(g[0]), label=g[1].strip(), fv=_num(g[2]), ref=_num(g[3]), inv=int(g[4]), race=int(g[5]),
                                bid=_num(g[6]), bsz=_num(g[7]), ask=_num(g[8]), asz=_num(g[9])))
    return out


def summaries():
    """Summary lines: dict(t, acct, locked, mode, worst, risk, ro, delta, priced, resting, real)."""
    out = []
    with open(J) as fh:
        for line in fh:
            if "| account " not in line:
                continue
            m = SUM.match(line)
            if not m:
                continue
            g = m.groups()
            t = ts(g[0]); acct = float(g[2]); locked = float(g[3])
            real = acct - locked if g[4] == "detecting" else acct
            out.append(dict(t=t, acct=acct, locked=locked, mode=g[4], worst=float(g[5]), risk=_num(g[6]), ro=bool(g[7]),
                            delta=float(g[8]), priced=int(g[9]), resting=int(g[11]), real=real))
    return out


EVENTS = {"409": "REQUEST_IN_FLIGHT", "429": "RATE LIMITED", "cycle_failed": "cycle failed", "unexpected": "ERROR   unexpected error",
          "cancel_fail": "could not confirm cancels", "ws_down": "realtime feed down", "traded_imm": "immediately",
          "deferred": "order changes deferred", "recovered": "recovered order", "net_err": "NETWORK", "batch_fail": "batch of",
          "missed": "missed message"}


def events():
    """(t, kind, text) for lines matching EVENTS."""
    out = []
    with open(J) as fh:
        for line in fh:
            if line[:4] != "2026":
                continue
            for k, p in EVENTS.items():
                if p in line:
                    out.append((ts(line[:24]), k, line[47:].strip()[:220]))
    return out


def fills(attribute=True):
    """Fills as dicts: t, eid, label, side ('bid'/'ask'/'?'), qty, yes_px (YES-equivalent price), sgn (+1 bought YES),
    fvq (fv at quote), inferred. Unrecorded fills ('?') get a side from the journal quote line in force
    (bid == price -> bid; ask == price or 1-price -> ask), as Run B's attribute.py did."""
    lab = eid_labels()
    rows = list(csv.DictReader(open(os.path.join(DATA2, "fills.csv"))))
    qs = defaultdict(list)
    if attribute:
        for q in quotes():
            qs[q["label"]].append(q)
    out = []
    for r in rows:
        t = ts(r["filled_at"]); L = lab.get(r["exchange_id"], r["exchange_id"])
        side, px, qp = r["our_side"], float(r["fill_price"]), _num(r["quote_price"])
        fvq = _num(r["fv_at_quote"]); inferred = False
        if side == "?" and attribute:
            for q in reversed(qs.get(L, [])):
                if q["t"] > t + 60:
                    continue
                if q["t"] < t - 35 * 60:
                    break
                if q["bid"] is not None and abs(q["bid"] - px) < 1e-6:
                    side, qp, fvq, inferred = "bid", q["bid"], q["fv"], True; break
                if q["ask"] is not None and (abs(q["ask"] - px) < 1e-6 or abs(1 - q["ask"] - px) < 1e-6):
                    side, qp, fvq, inferred = "ask", q["ask"], q["fv"], True; break
        if side == "bid":
            yes = px
        elif side == "ask":
            yes = qp if qp is not None else (1 - px)
        else:
            yes = None
        out.append(dict(t=t, eid=r["exchange_id"], label=L, side=side, qty=float(r["qty"]), px=px, yes_px=yes,
                        sgn={"bid": 1, "ask": -1}.get(side, 0), fvq=fvq, inferred=inferred,
                        fv_after=_num(r["fv_after"]), oid=r["order_id"]))
    out.sort(key=lambda f: f["t"])
    return out


def fills_reconciled():
    """fills() with sides of unrecorded/inferred fills re-chosen so that cumulative fills match the snapshot
    `position` column (the bot's copy of exchange holdings). Per market, the inferred or unknown fills between two
    snapshots get the sign combination (brute force, up to 12 fills) that best matches the position at the later
    snapshot. A sell is booked at YES price 1 - fill_price (NO-side fills are reported at the NO price).
    Adds f['recon'] = True where the side was changed or filled in."""
    fl = fills()
    snap, T = snapshots()
    byl = defaultdict(list)
    for f in fl:
        byl[f["label"]].append(f)
    for lab, fs in byl.items():
        pos_at = [(t, snap[t][lab]["pos"]) for t in T if lab in snap[t]]
        for f in fs:
            if f["sgn"] != 0:
                continue
            tgt = next(((t, p) for t, p in pos_at if t >= f["t"] + 120), None)
            if tgt is None:
                continue
            cum = sum(g["sgn"] * g["qty"] for g in fs if g["t"] <= tgt[0] and g is not f)
            sg = 1 if abs(cum + f["qty"] - tgt[1]) <= abs(cum - f["qty"] - tgt[1]) else -1
            f["recon"] = True
            f["sgn"] = sg; f["side"] = "bid" if sg > 0 else "ask"
            # unrecorded fills come at the YES or the NO price: take the one nearer the book (or reference) then
            ref = None
            for t, _ in pos_at:
                if t >= f["t"] - 300:
                    r = snap[t][lab]
                    ref = mid(r) or r["fv"] or r["ref"]
                    break
            if ref is None:
                f["yes_px"] = f["px"] if sg > 0 else 1 - f["px"]
            else:
                f["yes_px"] = f["px"] if abs(f["px"] - ref) <= abs(1 - f["px"] - ref) else 1 - f["px"]
    return fl


def status():
    return json.load(open(os.path.join(DATA2, "status.json")))


def table(rows, header):
    """Markdown table string."""
    s = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        s.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(s)
