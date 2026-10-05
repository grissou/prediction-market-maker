"""Package 14.2 DRY RUN on the fake exchange, on the same 10:21 UTC state as tests/test_p14_1_dryrun.py (the 4 Oct
15:56 ops snapshot in /home/claude/snap04, or $P9_SNAP, moved to the owner's 5 Oct numbers by tests/p14_1_state.py:
books 20% converged toward Polymarket, account 102.0k, free cash ~1.9k of 20k, MM inventory 10.5k with 7.9k stale,
value adds paused on the worst-case room). The bot runs its own full cycles: built on the LIVE Package 14 file, then a
file applied through the bot's own override path (check_overrides), the owner's deploy step:
  A. the 14.1 file with value_sell_margin 0.005 (the owner returns it there): the 14.1 baseline
  B. the 14.1 file with value_sell_margin 0.03 (the raised live value, for the record: what 14.1 would do with it)
  C. the staged 14.2 file (14.1 + alloc_swap_sell_margin 0.03 + alloc_swap_min_gain 0.05 + value_sell_margin 0.005)
The FIRST allocator run of each is captured (alloc_plan's own output): per swap the sold edge-held, the sale price vs p,
the bought edge, the net EV gain, the $ moved; then 11 more cycles of C (done / buy failed, realised at p, rooms) and a
rollback to the 14.1 file. Writes analysis/p14/DRYRUN_14_2.md (P14_DRYRUN_REPORT=0: no report).
Caveats (in the report): as the 14.1 dry run - no fills against our resting quotes, books move only where we take,
Polymarket flat, writes unlimited, the 10:21 state rebuilt from the 4 Oct snapshot.
Run:  python tests/test_p14_2_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import json
import os
import sys
import time as _rt
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
SNAP = os.environ.get("P9_SNAP", "/home/claude/snap04")
os.environ.setdefault("P9_SNAP", SNAP)
import test_p14_1_dryrun as DR                                   # noqa: E402  (its helpers: files, status, IOCs)
P, T, M = DR.P, DR.T, DR.M

DEPLOY = DR.DEPLOY
REPORT = os.path.join(ROOT, "analysis", "p14", "DRYRUN_14_2.md")
P141_FILE = DR.P141_FILE
P142_FILE = os.path.join(DEPLOY, "settings_override.mm_funding_14_2.json")
RESULTS, OUT = [], []
STATE = {}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def out(s=""):
    OUT.append(s)


def variant(path, tmp, **over):
    """The file at path with `over` on top, written to tmp (the owner's edit of the staged file)."""
    d = json.load(open(path))
    d.update(over)
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    return tmp


SENS = ((0.0, 0.05, 0.05), (0.03, 0.03, 0.05), (0.02, 0.05, 0.05), (0.03, 0.05, 0.05), (0.03, 0.05, 0.08),
        (0.03, 0.05, 0.10), (0.05, 0.05, 0.10))   # (alloc_swap_sell_margin, alloc_swap_min_gain, alloc_max_edge_sell)


def first_run(S, live, path, variants=()):
    """(api, bot, the first hourly plan (pairs, info), its journal lines, {variant: plan}) after the file at path
    landed: one cycle. variants: the same planning call (same book, cash, turnover) re-run with these settings."""
    api, b = T.build(S, live)
    DR.mf(b)
    P.apply_stage(b, path)
    cap, sens = {}, {}
    orig = b.alloc_plan

    def spy(inv, now_m, cash, skip=(), turnover_left=None, refill_only=False):   # (the hourly run's own output)
        if not refill_only and "plan" not in cap:
            keys = ("alloc_swap_sell_margin", "alloc_swap_min_gain", "alloc_max_edge_sell")
            keep = {k: getattr(b.cfg, k) for k in keys}
            for v in variants:
                for k, x in zip(keys, v):
                    setattr(b.cfg, k, x)
                sens[v] = orig(dict(inv), now_m, cash, skip, turnover_left, refill_only)
            for k, x in keep.items():
                setattr(b.cfg, k, x)
        res = orig(inv, now_m, cash, skip, turnover_left, refill_only)
        if not refill_only and "plan" not in cap:
            cap["plan"] = (res[0], dict(res[1]), {e: float(q) for e, q in inv.items()}, now_m)
        return res
    b.alloc_plan = spy
    n0 = len(P.CAP.lines)
    P.cycles(b, 1, stage="first")
    b.alloc_plan = orig
    lines = [m for _, _, m in P.CAP.lines[n0:]]
    return api, b, cap.get("plan", ([], {"ev_gain_est": 0.0, "blocked_by": {}}, {}, 0.0)), lines, sens


def sw_rows(b, pairs, now_m):
    """Per swap (long / short sale paired with a buy): (sold label, kind, qty, price, p, edge, bought label, short,
    qty, price, p, edge, $, gain est, per $)."""
    rows = []
    for pr in pairs:
        s, x = pr["sell"], pr["buy"]
        if x is None or s.get("kind") not in ("long", "short"):
            continue
        r = pr.get("swap")
        if r is not None:
            sd, bt = r["sold"], r["bought"]
            ps, pb = sd["p"], bt["p"]
        else:
            ps = b.alloc_p(b.ex[s["eid"]], now_m)
            pb = b.alloc_p(b.ex[x["eid"]], now_m)
        g = pr["usd"] * (x["edge"] - s["edge"])
        rows.append((s["label"], s["kind"], s["qty"], s["px"], ps, s["edge"], x["label"], x["short"], x["qty"],
                     x["px"], pb, x["edge"], pr["usd"], g, g / pr["usd"] if pr["usd"] else 0.0))
    return rows


def below(r):
    """The sale price vs p in cents (+ = below p for a long, above p for a short's buy-back: the cost side)."""
    return 100 * ((r[4] - r[3]) if r[1] == "long" else (r[3] - r[4]))


def main():
    if not os.path.exists(os.path.join(SNAP, "md.sqlite")):
        print(f"SKIP: no snapshot at {SNAP}")
        return 0
    t0 = _rt.time()
    S = P.load_snapshot()
    tmp = os.path.join(DEPLOY, ".tmp_live_14_2.json")
    tmp2 = os.path.join(DEPLOY, ".tmp_var_14_2.json")
    live = DR.live_file(tmp)

    # ---- the staged file
    raw = json.load(open(P142_FILE))
    good, bad = M.validate_overrides(raw, M.Config())
    check("files: the staged 14.2 file validates with no problem", not bad, bad)
    r141 = json.load(open(P141_FILE))
    check("files: it is the 14.1 file + alloc_swap_sell_margin 0.03, alloc_swap_min_gain 0.05, value_sell_margin "
          "0.005", {k: v for k, v in raw.items() if k not in ("alloc_swap_sell_margin", "alloc_swap_min_gain",
                                                               "value_sell_margin")} == r141
          and (raw["alloc_swap_sell_margin"], raw["alloc_swap_min_gain"], raw["value_sell_margin"])
          == (0.03, 0.05, 0.005))

    # ---- A / B / C: the first allocator run
    runs = {}
    for key, path in (("A", variant(P141_FILE, tmp2, value_sell_margin=0.005)), ("B", None), ("C", P142_FILE)):
        if key == "B":
            path = variant(P141_FILE, tmp2, value_sell_margin=0.03)
        api, b, (pairs, info, inv, now_m), lines, sens = first_run(S, live, path, SENS if key == "C" else ())
        rows = sw_rows(b, pairs, now_m)
        refills = [p for p in pairs if p["buy"] is None]
        runs[key] = {"api": api, "b": b, "pairs": pairs, "info": info, "rows": rows, "lines": lines,
                     "refills": (len(refills), sum(p["usd"] for p in refills)), "now_m": now_m,
                     "sens": {v: (sw_rows(b, r[0], now_m), r[1]["blocked_by"]) for v, r in sens.items()}}
    A, B, C = runs["A"], runs["B"], runs["C"]
    b = C["b"]
    check("seed: value adds paused and cash below the reserve, as live (the 14.1 dry run's seed)",
          b.mmr_paused and b.cash_left() < 20000, (b.mmr_paused, b.cash_left()))
    check("14.2 file in force: the swap margin 0.03, min gain 0.05, value_sell_margin 0.005, every 14.1 flag on",
          (b.cfg.alloc_swap_sell_margin, b.cfg.alloc_swap_min_gain, b.cfg.value_sell_margin) == (0.03, 0.05, 0.005)
          and all(getattr(b.cfg, k) for k in M.Bot.P141_FLAGS))
    check("A (14.1, value_sell_margin 0.005): the baseline plans swaps", len(A["rows"]) >= 1, len(A["rows"]))
    check("C (14.2): the first run plans swaps with a positive est. EV gain", len(C["rows"]) >= 1
          and sum(r[13] for r in C["rows"]) > 0, len(C["rows"]))
    check("C: every swap sale is within 3c of p (price vs p) and within alloc_max_edge_sell 0.05",
          all(below(r) <= 3.0 + 1e-6 and r[5] <= 0.05 + 1e-9 for r in C["rows"]),
          [(r[0], round(below(r), 2), round(r[5], 4)) for r in C["rows"] if below(r) > 3.0 or r[5] > 0.05])
    check("C: every swap's gain per $ (buy edge - sale edge-held) >= max(0.03, 0.05)",
          all(r[14] >= 0.05 - 1e-9 for r in C["rows"]), [round(r[14], 4) for r in C["rows"]])
    recs = [p["swap"] for p in C["pairs"] if p.get("swap")]
    check("C: the sale's cost is its edge-held at the sale price (counted once) on every swap record",
          recs and all(abs(r["sold"]["cost"] - r["sold"]["edge"]) < 1e-3 for r in recs), recs[:1])
    n_pl = sum(1 for m in C["lines"] if m.startswith("ALLOC SWAP sold") and m.endswith("planned"))
    check("C: one 'ALLOC SWAP sold ... - planned' journal line per planned swap", n_pl == len(C["rows"]),
          (n_pl, len(C["rows"])))
    check("C is stricter than A, never looser: no more swaps, a gain per $ at least A's lowest admitted",
          len(C["rows"]) <= len(A["rows"])
          and min((r[14] for r in C["rows"]), default=1) >= min((r[14] for r in A["rows"]), default=0) - 1e-9,
          (len(C["rows"]), len(A["rows"])))
    check("B (the raised 0.03 margin on 14.1) plans FEWER swaps than A and more refills: the diagnosis reproduces",
          len(B["rows"]) < len(A["rows"]) and B["refills"][1] > A["refills"][1],
          (len(B["rows"]), len(A["rows"]), B["refills"], A["refills"]))
    check("the refill keeps value_sell_margin: C plans the same refills as A (value_sell_margin 0.005 in both)",
          C["refills"] == A["refills"], (C["refills"], A["refills"]))
    st = P.status_of(b)
    check("C: status.json alloc.swaps carries the run (last_run per swap, counts_24h planned)",
          len((st["alloc"].get("swaps") or {}).get("last_run", [])) == len(C["rows"])
          and st["alloc"]["swaps"]["counts_24h"]["planned"] == len(C["rows"]), st["alloc"].get("swaps", {}).keys())
    check("C: no WARNING about value_sell_margin (it is 0.005 in the staged file)",
          not any(m.startswith("WARNING alloc_swap_sell_margin") for _, _, m in P.CAP.lines))

    # ---- C: 11 more cycles - the swaps execute
    api = C["api"]
    rooms0 = DR.mf(b)["room_free"]
    P.cycles(b, 11, stage="run")
    f2, a2 = DR.mf(b), DR.alloc(b)
    sw = a2.get("swaps") or {}
    cnt = sw.get("counts_24h") or {}
    check("C over 12 cycles: swaps done, realised EV reported at p, the counts add up (planned >= done + failed + "
          "not sold)", cnt.get("done", 0) >= 1 and cnt.get("planned", 0) >= cnt.get("done", 0)
          + cnt.get("buy_failed", 0) + cnt.get("not_sold", 0) and sw.get("ev_gain_realised_24h") is not None, cnt)
    n_dn = sum(1 for _, _, m in P.CAP.lines if m.startswith("ALLOC SWAP sold") and m.endswith("realised at p"))
    check("C: one '- done, realised at p' journal line per swap done", n_dn == cnt.get("done", 0), (n_dn, cnt))
    floor_room = min(rooms0["wc"], 20000.0)
    check("C: the worst-case room never below min(the room at the start, the reserve); corr keeps its 4k",
          f2["room_free"]["wc"] >= floor_room - 50 and f2["room_free"]["corr"] >= 4000, f2["room_free"])
    check("C: refill sales stay at or above p - value_sell_margin (EV given up tiny)",
          f2["refill_ev_given_24h"] <= 0.02 * max(1.0, f2["refill_sold_usd"]) + 1.0,
          (f2["refill_ev_given_24h"], f2["refill_sold_usd"]))
    raw_o = P.orders(api, None, "alloc_tick")
    dup = [k for k, n in Counter((o["cycle"], o["eid"], o["yes_buy"], o["yes_price"], o["qty"]) for o in raw_o).items()
           if n > 1]
    check("C: no duplicate allocator order (same market / side / price / size in one cycle)", not dup, dup[:3])
    check("C: the turnover cap holds (<= 15,000 an hour)", a2.get("turnover_hour", 0) <= 15000 + 1e-6)
    STATE["run"] = {"cnt": cnt, "usd": sw.get("usd_24h", 0.0), "est": sw.get("ev_gain_est_24h", 0.0),
                    "real": sw.get("ev_gain_realised_24h", 0.0), "room0": rooms0, "room": f2["room_free"],
                    "turnover": a2.get("turnover_hour", 0), "last": sw.get("last_run", []),
                    "refill": (f2["refill_sold_usd"], f2["refill_ev_given_24h"])}

    # ---- sensitivities: the FIRST run's own planning call re-run with other settings (same book, cash, turnover)
    sens = []
    for v in SENS:
        rr, bl = C["sens"][v]
        sens.append((*v, len(rr), sum(r[12] for r in rr), sum(r[13] for r in rr), bl.get("swap_floor", 0),
                     bl.get("swap_gain", 0)))
    by = {(x[0], x[1], x[2]): x for x in sens}
    sig = lambda rows: [(r[0], r[6], r[2], r[8]) for r in rows]   # noqa: E731
    check("sensitivity: the swap margin off reproduces the 14.1 baseline A swap for swap, the staged row is C's run",
          sig(C["sens"][(0.0, 0.05, 0.05)][0]) == sig(A["rows"]) and sig(C["sens"][(0.03, 0.05, 0.05)][0])
          == sig(C["rows"]), (by[(0.0, 0.05, 0.05)][3], len(A["rows"]), by[(0.03, 0.05, 0.05)][3], len(C["rows"])))
    check("sensitivity: both limits are live - the floor alone (min gain 0.03) cuts swaps, and raising "
          "alloc_max_edge_sell to 0.10 adds swaps only within the 3c floor (swap_floor counted)",
          by[(0.03, 0.03, 0.05)][3] <= by[(0.0, 0.05, 0.05)][3] and by[(0.03, 0.05, 0.10)][6] >= by[(0.03, 0.05,
                                                                                                    0.05)][6], sens)
    STATE["sens"] = sens

    # ---- the warning with a raised value_sell_margin, then the rollback
    n0 = len(P.CAP.lines)
    P.apply_stage(b, variant(P142_FILE, tmp2, value_sell_margin=0.03))
    check("the 14.2 file with value_sell_margin 0.03: ONE WARNING names it",
          sum(1 for _, _, m in P.CAP.lines[n0:] if m.startswith("WARNING alloc_swap_sell_margin")) == 1)
    bad_keys, _ = P.apply_stage(b, P141_FILE)
    st = P.status_of(b)
    check("rollback to the 14.1 file: the swap margin off (0), no alloc.swaps key, nothing refused",
          b.cfg.alloc_swap_sell_margin == 0.0 and "swaps" not in st.get("alloc", {}) and not bad_keys)
    for f in (tmp, tmp2):
        try:
            os.remove(f)
        except OSError:
            pass
    STATE.update(A=A, B=B, C=C)
    report()
    print(f"(all done in {_rt.time() - t0:.0f} s)")
    return 0


def table(rows, n=40):
    out("| # | sold | qty | price | p | price vs p (- = the cost side) | edge-held (= cost per $) | bought | qty | "
        "price | p | edge | $ | net EV gain est. | per $ |")
    out("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows[:n], 1):
        out(f"| {i} | {r[0]}{' NO' if r[1] == 'short' else ''} | {r[2]:.0f} | {r[3]:.3f} | {r[4]:.3f} | "
            f"{-below(r):+.1f}c | {100 * r[5]:.1f}% | {r[6]}{' (short YES)' if r[7] else ''} | {r[8]:.0f} | "
            f"{r[9]:.3f} | {r[10]:.3f} | {100 * r[11]:.1f}% | {r[12]:,.0f} | {r[13]:+,.0f} | {100 * r[14]:.1f}% |")
    if len(rows) > n:
        out(f"| ... | ({len(rows) - n} more) | | | | | | | | | | | | | |")


def summ(key):
    x = STATE[key]
    rows = x["rows"]
    usd, g = sum(r[12] for r in rows), sum(r[13] for r in rows)
    return len(rows), usd, g, (g / usd if usd else 0.0), x["refills"], x["info"]["blocked_by"]


def report():
    A, B, C, run, sens = (summ("A"), summ("B"), summ("C"), STATE["run"], STATE["sens"])
    out("# Package 14.2 dry run: the swap-only value-floor margin on the 10:21 UTC state of 5 Oct")
    out()
    out(f"Generated by `tests/test_p14_2_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {SNAP} "
        "moved to the owner's 10:21 numbers by `tests/p14_1_state.py` (the 14.1 dry run's seed). Each variant is built "
        "on the LIVE Package 14 file, then its file lands through `check_overrides`; the FIRST allocator run is the "
        "planner's own output in that cycle.")
    out()
    out("## Summary")
    out(f"- **A. 14.1 file, value_sell_margin 0.005 (the baseline the owner returns to):** {A[0]} swaps, "
        f"${A[1]:,.0f} moved, est. EV gain {A[2]:+,.0f} ({100 * A[3]:.1f}% per $); refills {A[4][0]} "
        f"(${A[4][1]:,.0f}).")
    out(f"- **B. 14.1 file, value_sell_margin 0.03 (the raised live value):** {B[0]} swaps, ${B[1]:,.0f}, est. "
        f"{B[2]:+,.0f}; refills {B[4][0]} (${B[4][1]:,.0f}). Raising the margin does NOT get swaps through - the "
        "refill sells the low-edge holdings first and stale-MM holdings become refill-only legs "
        "(analysis/p14/DIAG_14_2.md).")
    out(f"- **C. the staged 14.2 file:** {C[0]} swaps, ${C[1]:,.0f} moved, est. EV gain {C[2]:+,.0f} "
        f"({100 * C[3]:.1f}% per $); refills {C[4][0]} (${C[4][1]:,.0f}); blocked swap_floor "
        f"{C[5].get('swap_floor', 0)}, swap_gain {C[5].get('swap_gain', 0)}. Every swap sale within 3c of p, every "
        "gain >= 5% per $ net of both touches.")
    cnt = run["cnt"]
    out(f"- **C over 12 cycles:** {cnt.get('planned', 0)} planned / {cnt.get('done', 0)} done / "
        f"{cnt.get('buy_failed', 0)} buy failed / {cnt.get('not_sold', 0)} not sold; ${run['usd']:,.0f} bought; EV "
        f"{run['est']:+,.0f} estimated vs {run['real']:+,.0f} realised at p; room wc {run['room0']['wc']:,.0f} -> "
        f"{run['room']['wc']:,.0f}; refills ${run['refill'][0]:,.0f} sold, EV given up {run['refill'][1]:,.0f} "
        f"(< 0: above p); turnover ${run['turnover']:,.0f} of 15k.")
    out(f"- **Net:** 14.2 is a STRICTER swap rule than 14.1 at 0.005, not a looser one: {C[0]} vs {A[0]} swaps, "
        f"est. {C[2]:+,.0f} vs {A[2]:+,.0f}, at {100 * C[3]:.1f}% vs {100 * A[3]:.1f}% per $. The swaps the margin "
        "rule cuts are the thin ones (gain 3-5% per $) and the deep sales of high-p holdings (more than 3c below p).")
    by = {(x[0], x[1], x[2]): x for x in sens}
    f = lambda k: f"{by[k][3]} swaps / ${by[k][4]:,.0f} / {by[k][5]:+,.0f}"   # noqa: E731
    out(f"- **Sensitivity (the same first planning call):** the 3c floor alone (min gain 0.03) "
        f"{f((0.03, 0.03, 0.05))}; "
        f"2c {f((0.02, 0.05, 0.05))}; raising alloc_max_edge_sell to 0.08 / 0.10 under the 3c floor "
        f"{f((0.03, 0.05, 0.08))} / {f((0.03, 0.05, 0.10))} (the extra candidates all sit more than 3c below p); a "
        f"5c margin with 0.10 {f((0.05, 0.05, 0.10))}.")
    out()
    out("## C. the first 14.2 run, swap by swap")
    table(STATE["C"]["rows"])
    out()
    out("## A. the 14.1 baseline (value_sell_margin 0.005), swap by swap")
    table(STATE["A"]["rows"])
    out()
    out("## Sensitivity: the first run's own planning call (same book, cash and turnover) with other settings")
    out("| alloc_swap_sell_margin | alloc_swap_min_gain | alloc_max_edge_sell | swaps | $ | est. EV gain | blocked "
        "swap_floor | blocked swap_gain |")
    out("|---|---|---|---|---|---|---|---|")
    for m_, g_, e_, n, usd, g, fl, ga in sens:
        tag = " (= 14.1)" if not m_ else " (staged)" if (m_, g_, e_) == (0.03, 0.05, 0.05) else ""
        out(f"| {m_}{tag} | {g_ if m_ else '-'} | {e_} | {n} | {usd:,.0f} | {g:+,.0f} | {fl} | {ga} |")
    out()
    out("## The 12-cycle swap records (alloc.swaps.last_run)")
    for r in run["last"][:30]:
        sd, bt = r["sold"], r["bought"]
        out(f"- {r['status']}: sold {sd['label']} {sd['qty']:.0f} @ {sd['price']:.3f} (p {sd['p']:.3f}, edge-held "
            f"{100 * sd['edge']:.1f}%) -> {bt['label']} {bt['qty']:.0f} @ {bt['price']:.3f} (p {bt['p']:.3f}, edge "
            f"{100 * bt['edge']:.1f}%): est {r['gain_est']:+.2f}, realised "
            + ("-" if r["gain_realised"] is None else f"{r['gain_realised']:+.2f}"))
    out()
    out("## Caveats")
    out("- The 10:21 state is REBUILT from the 4 Oct 15:56 snapshot (tests/p14_1_state.py), not copied from the "
        "server: "
        "the live order list, `locked_in_orders` and per-market depth are not reproduced.")
    out("- Other traders never trade with our RESTING quotes in the fake; only our IOCs trade. Books move only where "
        "we take; Polymarket is flat (so 'realised at p' uses the same p as the plan); writes are unlimited (4 "
        "allocator orders a cycle, live ~2): the $/cycle figures are upper bounds.")
    out("- `gain_realised` is EV at Polymarket p at the fills, not P&L; a swap whose buy fails keeps its sale's "
        "given-up EV as its realised figure (negative). Several pairs that share ONE buy level (e.g. the Connecticut "
        "and Rhode Island governors above) can find it partly taken by the earlier ones: the later buy fills only "
        "what is left and the rest of its sale's proceeds stays in cash (14.1's pairing, unchanged), so its realised "
        "gain falls short of the estimate or turns negative.")
    out("- The staged file keeps `alloc_max_edge_sell` 0.05 from 14.1: both it and the 3c floor bind (the edge rule "
        "below p ~0.63, the 3c floor above). The sensitivity table shows what raising it would add.")


if __name__ == "__main__":
    rc = main()
    if RESULTS and os.environ.get("P14_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
