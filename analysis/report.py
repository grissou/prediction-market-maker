"""
The results table and the equity curve from the bot's own records (README §6).

    python analysis/report.py run/market_data.sqlite [older.sqlite ...] [--fills fills.csv] [--summaries summaries.txt] [--png docs/equity.png]

Reads the recorder's `account` table (one row a minute: account value at the exchange's marks) from each
database given, in time order, and prints: the return, the lowest reading, the largest peak-to-trough fall,
and the daily closes. With --fills (the fills.csv of releases before 11 October, which carried fair value at
the quote) it adds the edge per fill: what each fill was expected to gain at the result by the bot's own fair
value, per share and per unit of cash. With --summaries (the bot's hourly SUMMARY log lines: `grep SUMMARY
run/mm_bot.log*`) it adds expected value at settlement and the leaderboard rank, and draws EV on the chart.
With --png it draws the curve (needs matplotlib).

The first 37 hours of the tournament (to 2 October 05:00 UTC) are excluded from the curve and the drawdown: with no trades in most
books, the exchange's trade-based marks showed the account anywhere between 100k and 150k.
"""
import argparse
import csv
import re
import sqlite3
from datetime import datetime, timezone

MARKS_SANE_FROM = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)
START = 100_000.0


def account_series(paths):
    rows = []
    for path in paths:
        for ts, value in sqlite3.connect(path).execute("select ts, account_value from account"):
            if value and value > 0:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                rows.append((t, float(value)))
    return sorted(set(rows))


def summary(series):
    s = [(t, v) for t, v in series if t >= MARKS_SANE_FROM]
    last_t, last = s[-1]
    low_t, low = min(s, key=lambda r: r[1])
    peak, fall, fall_at = 0.0, 0.0, None
    for t, v in s:
        peak = max(peak, v)
        if v / peak - 1 < fall:
            fall, fall_at = v / peak - 1, t
    closes = {}
    for t, v in s:
        closes[t.date()] = v
    return dict(last_t=last_t, last=last, ret=last / START - 1, low=low, low_t=low_t,
                low_vs_start=low / START - 1, fall=fall, fall_at=fall_at, closes=closes)


def edge_per_fill(path, since):
    gain = cash = shares = n = 0
    for r in csv.DictReader(open(path)):
        if r["our_side"] not in ("bid", "ask") or not r["fv_at_quote"]:
            continue
        if datetime.fromisoformat(r["filled_at"].replace("Z", "+00:00")) < since:
            continue
        q, px, fv = float(r["qty"]), float(r["fill_price"]), float(r["fv_at_quote"])
        edge = fv - px if r["our_side"] == "bid" else px - fv       # per share, at the result
        gain += edge * q
        cash += (px if r["our_side"] == "bid" else 1 - px) * q
        shares += q
        n += 1
    return dict(fills=n, shares=shares, cash=cash, gain=gain, per_share=gain / shares, per_cash=gain / cash)


SUMMARY = re.compile(r"^(\S+ \S+?),\d+ INFO SUMMARY Balance ([\d.]+)k .*?leaderboard ([\d,]+) of ([\d,]+).*?"
                     r"EV at settlement ([\d.]+)k")


def summaries(path):
    """[(time, balance, rank, entrants, EV at settlement)] from the hourly summary lines, in time order."""
    out = []
    for line in open(path):
        m = SUMMARY.match(line)
        if m:
            t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            out.append((t, float(m.group(2)) * 1000, int(m.group(3).replace(",", "")), int(m.group(4).replace(",", "")),
                        float(m.group(5)) * 1000))
    return sorted(out)


def draw(series, png, hourly=()):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = [(t, v) for t, v in series if t >= MARKS_SANE_FROM]
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=150)
    ax.plot([t for t, _ in s], [v / 1000 for _, v in s], lw=0.9, color="#1f4e79", label="account at the exchange's marks")
    if hourly:
        ax.plot([r[0] for r in hourly], [r[4] / 1000 for r in hourly], lw=1.2, color="#c0504d",
                label="expected value at settlement (positions at Polymarket p)")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.axhline(START / 1000, color="grey", lw=0.6, ls="--")
    ax.axvline(datetime(2026, 11, 4, 17, tzinfo=timezone.utc), color="grey", lw=0.6, ls=":")
    ax.set_ylabel("thousand")
    ax.set_title("SIG Predictions Cup: the account, %s to %s UTC" % (s[0][0].strftime("%d %b"), s[-1][0].strftime("%d %b %H:%M")),
                 fontsize=10)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="+")
    ap.add_argument("--fills")
    ap.add_argument("--summaries")
    ap.add_argument("--png")
    a = ap.parse_args()
    series = account_series(a.db)
    r = summary(series)
    print("As of %s UTC" % r["last_t"].strftime("%d %b %Y %H:%M"))
    print("Account at the marks   %10.0f  (%+.2f%% from %.0f)" % (r["last"], r["ret"] * 100, START))
    print("Lowest reading         %10.0f  (%+.2f%% vs start, %s)" % (r["low"], r["low_vs_start"] * 100, r["low_t"].strftime("%d %b %H:%M")))
    print("Largest peak-to-trough %9.2f%%  (%s)" % (r["fall"] * 100, r["fall_at"].strftime("%d %b %H:%M")))
    print("Daily closes (UTC):")
    for d, v in r["closes"].items():
        print("  %s  %8.0f" % (d, v))
    if a.fills:
        e = edge_per_fill(a.fills, MARKS_SANE_FROM)
        print("Edge per fill (bot's own fair value at the quote), %d fills: %.3f per share, %.1f%% per unit of cash, %.0f in total"
              % (e["fills"], e["per_share"], e["per_cash"] * 100, e["gain"]))
    hourly = summaries(a.summaries) if a.summaries else []
    if hourly:
        best = min(hourly, key=lambda r: r[2])
        last = hourly[-1]
        print("EV at settlement        %10.0f  (first %.0f on %s)" % (last[4], hourly[0][4], hourly[0][0].strftime("%d %b")))
        print("Leaderboard            %6d of %d  (best %d of %d on %s)" % (last[2], last[3], best[2], best[3], best[0].strftime("%d %b")))
    if a.png:
        draw(series, a.png, hourly)
        print("wrote", a.png)


if __name__ == "__main__":
    main()
