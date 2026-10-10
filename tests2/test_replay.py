"""
Replay of a recorded snapshot: the old bot and the new one plan orders on the same live state, side by side.

The state is the 4 October 15:56 ops snapshot (branch ops-snapshot-2026-10-04: market_data.sql.gz -> md.sqlite, plus
status.json, order_notes.json, position_lots.json, settings_override.json): all 237 markets, other traders' books,
Polymarket, positions, marks, cash. The OLD bot runs through tests/dryrun_harness.py (as tests/test_p15_dryrun.py
does) with the live settings of 10 October; the NEW bot runs on tests2/fakes.FakeClient seeded with the same state and
the rewrite's settings (the same values). Both send to a fake exchange for CYCLES cycles; every order is recorded and
grouped by market and side. Writes analysis/rewrite/REPLAY.md (market, side, old shares, new shares, tags).
The snapshot is built in a temporary folder from git unless $MM2_SNAP points at one already built.
Run:  python3 tests2/test_replay.py      (exit code 0 = all passed; SKIP without the snapshot branch)
"""
import gzip
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRANCH = "origin/ops-snapshot-2026-10-04"
FILES = ("market_data.sql.gz", "status.json", "order_notes.json", "position_lots.json", "settings_override.json")
LIVE_FILE = os.path.join(ROOT, "deploy", "settings_override.live_2026-10-10.json")
REPORT = os.path.join(ROOT, "analysis", "rewrite", "REPLAY.md")
CYCLES = 6
RESULTS = []


def fakes2():
    """tests2/fakes.py under its own name: the old harness has already imported tests/fakes.py as `fakes`."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fakes2", os.path.join(HERE, "fakes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def snapshot_dir():
    """The built snapshot folder, or None if the branch is not available."""
    if os.environ.get("MM2_SNAP"):
        return os.environ["MM2_SNAP"]
    d = tempfile.mkdtemp(prefix="snap04")
    for name in FILES:
        r = subprocess.run(["git", "show", f"{BRANCH}:{name}"], cwd=ROOT, capture_output=True)
        if r.returncode:
            return None
        open(os.path.join(d, name), "wb").write(r.stdout)
    c = sqlite3.connect(os.path.join(d, "md.sqlite"))
    c.executescript(gzip.open(os.path.join(d, "market_data.sql.gz"), "rt").read())
    c.commit()
    return d


def old_plan(snap):
    """{(label, "bid"/"ask"): [shares, {feature}]} the old bot sent in CYCLES cycles of 30 s."""
    os.environ["P9_SNAP"] = snap
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import dryrun_harness as P
    S = P.load_snapshot(snap)
    api, b = P.build(S, overrides=LIVE_FILE)
    P.cycles(b, CYCLES, step=30.0)
    plan = defaultdict(lambda: [0.0, set()])
    for o in P.orders(api):
        key = (o["label"], "bid" if o["yes_buy"] else "ask")
        plan[key][0] += o["qty"]
        plan[key][1].add(o["tag"])
    return S, plan


def new_client(S):
    """FakeClient seeded with the snapshot: markets, other traders' books, positions, marks and cash."""
    FakeClient, raw_market = fakes2().FakeClient, fakes2().raw_market
    from mmbot2.exchange import parse_markets
    from mmbot2.state import held_usd
    party = {"Rep": "Republican", "Dem": "Democratic", "Ind": "Independent"}
    markets = []
    for eid, (_, label, _) in sorted(S["snap"].items(), key=lambda kv: int(kv[0])):
        p, race = label.split(" ", 1)
        markets += parse_markets(raw_market(f"m{eid}", eid, party.get(p, p), race), None)
    books = {e: {"bids": [(x["price"], x["quantity"]) for x in b["bids"]],
                 "asks": [(x["price"], x["quantity"]) for x in b["asks"]]} for e, b in S["books"].items()}
    for m in markets:
        books.setdefault(m.eid, {"bids": [], "asks": []})
    value = float(S["status"]["account_value"])
    held = sum(held_usd(q, S["marks"].get(e, 0.5)) for e, q in S["pos"].items())
    client = FakeClient(markets, books, cash=value - held, live=True)
    client.inv, client.marks = dict(S["pos"]), dict(S["marks"])
    client.mid = lambda e: S["marks"].get(e) or 0.5      # value positions at the snapshot's marks, as the old fake
    return client


def new_plan(S):
    """{(label, side): [shares, {tag}]} the new bot sent in CYCLES cycles, and the bot."""
    FakeFeed, FakeRefs = fakes2().FakeFeed, fakes2().FakeRefs
    from mmbot2 import config
    from mmbot2.bot import Bot
    client = new_client(S)
    refs = {}
    for eid, (_, label, r) in S["snap"].items():
        if r is not None:
            p, race = label.split(" ", 1)
            refs[f"{race}|{ {'Rep': 'Republican', 'Dem': 'Democratic'}.get(p, p) }"] = float(r)
    d = tempfile.mkdtemp(prefix="mm2replay")
    env = config.Env("k", "t", "http://x", "", d, os.path.join(d, "settings_override.json"))
    bot = Bot(client, FakeFeed(), FakeRefs(refs, spread=0.01), config.Settings(), env, True)
    bot.load_markets(0.0)
    sent, place = [], client.place
    client.place = lambda orders, positions: (sent.extend(orders), place(orders, positions))[1]
    for _ in range(CYCLES):
        bot.last_full = -1e9
        bot.cycle()
    plan = defaultdict(lambda: [0.0, set()])
    for o in sent:
        key = (bot.label(o.eid), "bid" if o.is_bid else "ask")
        plan[key][0] += o.size
        plan[key][1].add(o.tag)
    return plan, bot, client


def compare(old, new):
    """Rows (label, side, old shares, new shares, verdict) over every market either bot traded."""
    rows = []
    for key in sorted(set(old) | set(new)):
        o, n = old.get(key, [0.0, set()]), new.get(key, [0.0, set()])
        if o[0] and n[0]:
            verdict = "same" if 0.5 <= n[0] / o[0] <= 2.0 else "size"
        else:
            verdict = "old only" if o[0] else "new only"
        rows.append((key[0], key[1], o[0], ",".join(sorted(o[1])), n[0], ",".join(sorted(n[1])), verdict))
    return rows


def write_report(rows):
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    counts = defaultdict(int)
    for r in rows:
        counts[r[6]] += 1
    with open(REPORT, "w") as f:
        f.write(f"# Replay: old vs new bot on the 4 Oct snapshot ({CYCLES} cycles)\n\n"
                f"Written by tests2/test_replay.py. {dict(counts)}\n\n"
                "| Market | Side | Old shares | Old feature | New shares | New tag | Verdict |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r[0]} | {r[1]} | {r[2]:.0f} | {r[3]} | {r[4]:.0f} | {r[5]} | {r[6]} |\n")
    return counts


def main():
    snap = snapshot_dir()
    if snap is None:
        print("SKIP: no snapshot branch")
        return 0
    S, old = old_plan(snap)
    logging.disable(logging.CRITICAL)
    new, bot, client = new_plan(S)
    rows = compare(old, new)
    counts = write_report(rows)
    check("the new bot ran every cycle on the live state", bot.running and not bot.killed)
    check("the new bot planned orders", len(new) > 0)
    check("the new bot never crossed itself", client.self_crosses == 0)
    check("the new bot priced most markets", len(bot.last_p) >= 200, str(len(bot.last_p)))
    both = counts["same"] + counts["size"]
    check("most markets both bots traded agree within a factor of two", counts["same"] >= both / 2,
          str(dict(counts)))
    print(f"report: {os.path.relpath(REPORT, ROOT)}  {dict(counts)}")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
