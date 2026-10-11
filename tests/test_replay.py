"""
Replay of a recorded snapshot: the old bot and the new one plan orders on the same live state, side by side.

The state is the 4 October 15:56 ops snapshot (branch ops-snapshot-2026-10-04: market_data.sql.gz -> md.sqlite, plus
status.json, order_notes.json, position_lots.json, settings_override.json): all 237 markets, other traders' books,
Polymarket, positions, marks, cash. The OLD bot (retired 11 October 2026; tag legacy-2026-10-11, or $MM2_OLD_REF)
is checked out of git into a temporary folder and run in a subprocess through its tests/dryrun_harness.py with its
live settings of 10 October; the NEW bot runs here on tests/fakes.FakeClient seeded with the same state and the
rewrite's settings (the same values). Both send to a fake exchange for CYCLES cycles; every order is recorded and
grouped by market and side. Writes REPLAY.md next to the snapshot (market, side, old shares, new shares, tags).
The snapshot is built in a temporary folder from git unless $MM2_SNAP points at one already built.
Run:  python3 tests/test_replay.py      (exit code 0 = all passed; SKIP without the snapshot branch or the old bot's ref)
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
OLD_REF = os.environ.get("MM2_OLD_REF", "legacy-2026-10-11")
OLD_FILES = ("mm_bot.py", "mmbot", "tests", "ref_prices.py", "ref_map.json", "deploy/settings_override.live_2026-10-10.json")
OLD_SCRIPT = """
import json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.join(sys.argv[1], "tests"))
import dryrun_harness as P
S = P.load_snapshot(sys.argv[2])
api, b = P.build(S, overrides=os.path.join(sys.argv[1], "deploy", "settings_override.live_2026-10-10.json"))
P.cycles(b, int(sys.argv[3]), step=30.0)
plan = defaultdict(lambda: [0.0, []])
for o in P.orders(api):
    key = o["label"] + "|" + ("bid" if o["yes_buy"] else "ask")
    plan[key][0] += o["qty"]
    plan[key][1].append(o["tag"])
S["plan"] = plan
json.dump(S, sys.stdout, default=list)
"""
CYCLES = 6
RESULTS = []


def fakes2():
    """tests/fakes.py under its own name (the module name `fakes` is kept free for the old harness's copy)."""
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
    """The snapshot dict and {(label, "bid"/"ask"): [shares, {feature}]} the old bot sent in CYCLES cycles of 30 s,
    computed by the old bot's own code (OLD_REF, extracted with git archive) in a subprocess; None without the ref."""
    d = tempfile.mkdtemp(prefix="mm1")
    r = subprocess.run(f"git archive {OLD_REF} {' '.join(OLD_FILES)} | tar -x -C {d}", shell=True, cwd=ROOT,
                       capture_output=True)
    if r.returncode or not os.path.exists(os.path.join(d, "tests", "dryrun_harness.py")):
        return None, None
    env = dict(os.environ, P9_SNAP=snap, SUPERMARKET_API_KEY="x", TOURNAMENT_SLUG="x")
    r = subprocess.run([sys.executable, "-c", OLD_SCRIPT, d, snap, str(CYCLES)], cwd=d, env=env,
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:], file=sys.stderr)
        return None, None
    S = json.loads(r.stdout)
    plan = {}
    for key, (shares, tags) in S.pop("plan").items():
        label, side = key.rsplit("|", 1)
        plan[(label, side)] = [shares, set(tags)]
    return S, plan


def new_client(S):
    """FakeClient seeded with the snapshot: markets, other traders' books, positions, marks and cash."""
    FakeClient, raw_market = fakes2().FakeClient, fakes2().raw_market
    from mmbot.exchange import parse_markets
    from mmbot.state import held_usd
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
    from mmbot import config
    from mmbot.bot import Bot
    client = new_client(S)
    refs = {}
    for eid, (_, label, r) in S["snap"].items():
        if r is not None:
            p, race = label.split(" ", 1)
            refs[f"{race}|{ {'Rep': 'Republican', 'Dem': 'Democratic'}.get(p, p) }"] = float(r)
    d = tempfile.mkdtemp(prefix="mm2replay")
    env = config.Env("k", "t", "http://x", "", d, os.path.join(d, "settings_override.json"))
    # The replay compares the logic, so it quotes the size the old bot's plan used that day (0.002 x account);
    # the default 0.0005 (fix brief, 10 Oct) is the owner's choice to start where the old bot's ceiling left it.
    s = config.Settings(mm_quote_frac=0.002)
    bot = Bot(client, FakeFeed(), FakeRefs(refs, spread=0.01), s, env, True)
    bot.load_markets(0.0)
    sent, place = [], client.place
    client.place = lambda orders, positions: (sent.extend(orders), place(orders, positions))[1]
    for _ in range(CYCLES):
        bot.budget.last_full = -1e9
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


def write_report(rows, report):
    counts = defaultdict(int)
    for r in rows:
        counts[r[6]] += 1
    with open(report, "w") as f:
        f.write(f"# Replay: old vs new bot on the 4 Oct snapshot ({CYCLES} cycles)\n\n"
                f"Written by tests/test_replay.py (old bot: {OLD_REF}). {dict(counts)}\n\n"
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
    if S is None:
        print(f"SKIP: the old bot's ref {OLD_REF} is not available (set MM2_OLD_REF)")
        return 0
    logging.disable(logging.CRITICAL)
    new, bot, client = new_plan(S)
    rows = compare(old, new)
    report = os.path.join(snap, "REPLAY.md")
    counts = write_report(rows, report)
    check("the new bot ran every cycle on the live state", bot.running and not bot.killed)
    check("the new bot planned orders", len(new) > 0)
    check("the new bot never crossed itself", client.self_crosses == 0)
    check("the new bot priced most markets", len(bot.guard.last_p) >= 200, str(len(bot.guard.last_p)))
    both = counts["same"] + counts["size"]
    check("most markets both bots traded agree within a factor of two", counts["same"] >= both / 2,
          str(dict(counts)))
    print(f"report: {report}  {dict(counts)}")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
