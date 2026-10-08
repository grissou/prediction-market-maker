#!/usr/bin/env python3
"""Which layer of the bot earns? Attribution from the bot's own log lines (mm_bot.log*), per UTC day.

Every trade the bot makes leaves a tagged line in the log, with the Polymarket price p (or the fair value) at the
time, so each layer's expected-value contribution can be summed without the order notes (which the bot keeps for a
day only):

  market making   "FILL ex <id> our bid/ask xN @ px (fv when quoted f)" with f in the middle band [0.15, 0.85]:
                  buys and sells FIFO-matched per market -> realised spread; the unmatched rest valued at f - px.
  value adds      the same FILL lines with f outside the band (tail quotes): edge = N x (f - px) for a buy, N x (px - f)
                  for a sell. These are value positions acquired through quotes.
  takes           "TAKE <market>: Polymarket p vs stale bid/ask px -> buying/selling N YES at px": edge to p.
  allocator buys  "ALLOC bought <market> N @ px (edge e%, $D)": EV gained = e x D (swaps and spare-cash buys).
  allocator sales "ALLOC sold <market> N @ px (edge-held e%, $D freed) ...": EV given up = e x D; "(reserve refill)"
                  lines are the refill, "-> buy ..." lines are the sale half of a swap.
  momentum        "MOMENTUM funding sold ... EV given up $x" (its funding sales) and "MOMENTUM bought ..." (its buys).
  harvest ladder  "HARVEST fill ..." lines.
  account path    "realtime | account X ... EV outcome Y.Zk" lines: first and last reading of each day.

Usage: python analysis/returns_attribution.py <dir with mm_bot.log*>   (prints the tables; see
analysis/reports/RETURNS_ATTRIBUTION.md for the write-up). Reads only; no network.
"""
import glob
import os
import re
import sys
from collections import defaultdict, deque

MID = (0.15, 0.85)
R = {
    "fill": re.compile(r"^(\S+ \S+) INFO +FILL ex (\d+) +our (bid|ask) x(\d+) @ ([\d.]+) +\(fv when quoted ([\d.?]+)"),
    "take": re.compile(r"^(\S+ \S+) WARNING TAKE (.+?): Polymarket ([\d.]+) vs stale \w+ ([\d.]+) -> (buying|selling) (\d+) YES at ([\d.]+)"),
    "bought": re.compile(r"^(\S+ \S+) WARNING ALLOC bought (.+?) (\d+) @ ([\d.]+) \(edge (-?[\d.]+)%, \$([\d.]+)\)"),
    "sold": re.compile(r"^(\S+ \S+) WARNING ALLOC sold (.+?) (\d+) @ ([\d.]+) \(edge-held (-?[\d.]+)%, \$([\d.]+) freed\) (.*)$"),
    "mom_sold": re.compile(r"^(\S+ \S+) WARNING MOMENTUM funding sold .*?\$([\d.]+), EV given up \$(-?[\d.]+)"),
    "mom_bought": re.compile(r"^(\S+ \S+) WARNING MOMENTUM bought .*?\$([\d.]+)"),
    "harvest": re.compile(r"^(\S+ \S+) WARNING HARVEST fill (.+?): (sold|bought) (\d+) YES @ ([\d.]+) \(level \d+; p ([\d.]+), edge \$(-?[\d.]+)"),
    "account": re.compile(r"^(\S+ \S+) INFO +realtime \| account (\d+) .*?EV outcome ([\d.]+)k"),
}


def day(ts):
    return ts[:10]


def main(path):
    files = sorted(glob.glob(os.path.join(path, "mm_bot.log*")), key=lambda f: (len(f), f), reverse=True)
    lines = []
    for f in files:
        with open(f, errors="replace") as fh:
            lines.extend(fh)
    lines.sort(key=lambda l: l[:19])               # the files rotate; the timestamp orders them
    by = defaultdict(lambda: defaultdict(float))    # by[day][metric]
    lots = defaultdict(deque)                        # market -> FIFO lots of middle-band maker fills
    acct = {}                                        # day -> (first account, first ev, last account, last ev)
    for l in lines:
        m = R["fill"].match(l)
        if m:
            ts, e, side, q, px, fv = m.groups()
            if fv == "?":
                continue
            q, px, fv, buy = float(q), float(px), float(fv), side == "bid"
            d = day(ts)
            if MID[0] <= fv <= MID[1]:
                by[d]["mm_fills"] += 1
                book, rem = lots[e], q if buy else -q
                while abs(rem) > 1e-9 and book and (book[0][0] > 0) != (rem > 0):
                    lot = book[0]
                    n = min(abs(rem), abs(lot[0]))
                    by[d]["mm_realised"] += n * (px - lot[1]) * (1 if lot[0] > 0 else -1)
                    lot[0] += n if lot[0] < 0 else -n
                    rem += n if rem < 0 else -n
                    if abs(lot[0]) <= 1e-9:
                        book.popleft()
                if abs(rem) > 1e-9:
                    book.append([rem, px])
            else:
                by[d]["value_quote_fills"] += 1
                by[d]["value_quote_ev"] += q * ((fv - px) if buy else (px - fv))
            continue
        m = R["take"].match(l)
        if m:
            ts, mkt, p, stale, verb, q, px = m.groups()
            q, p, px = float(q), float(p), float(px)
            by[day(ts)]["take_n"] += 1
            by[day(ts)]["take_ev"] += q * ((p - px) if verb == "buying" else (px - p))
            continue
        m = R["bought"].match(l)
        if m:
            ts, mkt, q, px, e, usd = m.groups()
            by[day(ts)]["alloc_buy_n"] += 1
            by[day(ts)]["alloc_buy_usd"] += float(usd)
            by[day(ts)]["alloc_buy_ev"] += float(e) / 100 * float(usd)
            continue
        m = R["sold"].match(l)
        if m:
            ts, mkt, q, px, e, usd, rest = m.groups()
            k = "refill" if "reserve refill" in rest else "swap_sale"
            by[day(ts)][k + "_n"] += 1
            by[day(ts)][k + "_usd"] += float(usd)
            by[day(ts)][k + "_ev_given"] += float(e) / 100 * float(usd)
            continue
        m = R["mom_sold"].match(l)
        if m:
            ts, usd, ev = m.groups()
            by[day(ts)]["mom_sold_usd"] += float(usd)
            by[day(ts)]["mom_ev_given"] += float(ev)
            continue
        m = R["mom_bought"].match(l)
        if m:
            by[day(m.group(1))]["mom_bought_usd"] += float(m.group(2))
            continue
        m = R["harvest"].match(l)
        if m:
            by[day(m.group(1))]["harvest_n"] += 1
            by[day(m.group(1))]["harvest_ev"] += float(m.group(7))
            continue
        m = R["account"].match(l)
        if m:
            ts, a, ev = m.groups()
            d = day(ts)
            a, ev = float(a), float(ev) * 1000
            if d not in acct:
                acct[d] = [a, ev, a, ev]
            acct[d][2], acct[d][3] = a, ev
    unmatched = sum(q * 0 for v in lots.values() for q, _ in v)   # no p for the leftover lots here: reported as shares
    left = sum(abs(q) for v in lots.values() for q, _ in v)

    days = sorted(set(by) | set(acct))
    cols = [("MM realised", "mm_realised"), ("MM fills", "mm_fills"), ("Value-quote EV", "value_quote_ev"),
            ("Takes EV", "take_ev"), ("Alloc buys EV", "alloc_buy_ev"), ("Alloc buys $", "alloc_buy_usd"),
            ("Swap sales EV given", "swap_sale_ev_given"), ("Refill $", "refill_usd"), ("Refill EV given", "refill_ev_given"),
            ("Momentum $ sold", "mom_sold_usd"), ("Momentum EV given", "mom_ev_given"), ("Harvest EV", "harvest_ev")]
    print(f"{'day':10}" + "".join(f"{c[0]:>20}" for c in cols) + f"{'account':>18}{'EV outcome':>18}")
    tot = defaultdict(float)
    for d in days:
        row = by[d]
        for _, k in cols:
            tot[k] += row[k]
        a = acct.get(d)
        acc = f"{a[0]:,.0f}->{a[2]:,.0f}" if a else ""
        evs = f"{a[1]:,.0f}->{a[3]:,.0f}" if a else ""
        print(f"{d:10}" + "".join(f"{row[k]:>20,.0f}" for _, k in cols) + f"{acc:>18}{evs:>18}")
    print(f"{'total':10}" + "".join(f"{tot[k]:>20,.0f}" for _, k in cols))
    print(f"\nmiddle-band maker lots left unmatched: {left:,.0f} shares (valued in status.json mm_carry_24h.unmatched_ev)")
    net_alloc = tot["alloc_buy_ev"] - tot["swap_sale_ev_given"]
    print(f"allocator net EV (buys - swap sales): {net_alloc:+,.0f}; refill cost {tot['refill_ev_given']:,.0f}; "
          f"momentum cost {tot['mom_ev_given']:,.0f} for ${tot['mom_bought_usd']:,.0f} bought")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
