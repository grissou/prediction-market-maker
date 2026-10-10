"""
Package 10 END-TO-END DRY RUN on the fake exchange, seeded with the REAL live state of 3 Oct 22:47 UTC (the ops snapshot
in /home/claude/snap03, or $P9_SNAP; the tests/dryrun_harness.py harness: all 237 markets, other traders' 3-level books,
raw Polymarket references, positions, the exchange's marks, cash ~0, position lots). The base is the owner's LIVE
value-mode file (Package 8 in value mode, deploy/package10/settings_override.stage0_code_only.json); each staged file of
deploy/package10/ is applied with the bot's own override path (check_overrides), then full cycles (Bot.cycle) run on a
simulated clock and every order the fake exchange receives is logged with the feature that sent it (alloc_tick added).
Stages: 0 code only (identical to the Package 9 head a67d9da on the same seed), 1 the value guard + bloc delta + value
limits, 2 the allocator (sets at 0.06 per $, 15k reserve, pins), 3 value market making (hurdle 0.08, adding factor 0.5);
then a simulated TILT RISE (longshot asks +3c, favourite bids -3c) and a CONVERGENCE (every book to Polymarket +-1c).
Writes the readable report to analysis/p10/DRYRUN.md (P10_DRYRUN_REPORT=0: no report).

What the fake does NOT model (caveats, in the report): other traders never trade with our resting quotes (the market
maker's fills are estimated from F's side-hour model instead), the books move only where we take (plus the stated hourly
refills / scenario rewrites), Polymarket is flat, every Polymarket price counts as liquid, the exchange's marks stay at the
snapshot's, the 28/min write limiter is not modelled (the fake reports unlimited writes: the allocator's 30% share then
allows 4 orders a cycle; live it is ~2).

Run:  python tests/test_p10_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time as _rt
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import dryrun_harness as P                                      # noqa: E402  (the harness: snapshot, fake, clock)
RETIRED_KEYS = ("tilt_exit_priority", "tilt_exit_full_size")   # removed from Config; off in every staged file
import mm_bot as M                                                # noqa: E402

P.TAGGED = P.TAGGED + ("alloc_tick",)
DEPLOY = os.path.join(ROOT, "deploy", "package10")
REPORT = os.path.join(ROOT, "analysis", "p10", "DRYRUN.md")
BASE_REV = "a67d9da"                                              # the Package 9 head (Package 10 spec only)
RESULTS = []
LIVE = {"arb_two_sided": False, "worst_case_backstop_frac": 0.8, "capital_ceiling_adding_size_factor": 0.0,
        "writes_per_minute": 28, "writes_per_minute_max": 28, "burst_cycle_seconds": 60, "ref_tilt_enabled": False,
        "ref_tilt_max": 0.11, "ref_tilt_headline": True, "reduce_no_as_sell": True, "no_set_aware_bids": True,
        "pair_unwind_followup": True, "pair_no_unwind_max_cost": 0.003, "arb_enabled": False, "cash_gate_enabled": True,
        "adding_factor_per_market": True, "pair_no_unwind_max_per_cycle": 1, "pair_unwind_race_order": True,
        "pair_no_unwind_max_sets": 1000, "pair_unwind_followup_max_age": 600,
        "ref_guard_tilted": False, "ref_guard_exits": False, "take_tilted_ref": False}
STAGES = ("stage0_code_only", "stage1_value_guard", "stage2_allocator", "stage3_value_mm")
FILES = {k: os.path.join(DEPLOY, f"settings_override.{k}.json") for k in STAGES}
OUT = []
TOL = 1e-9


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if not cond else ""))
    RESULTS.append(bool(cond))


def out(s=""):
    OUT.append(s)


# ============================================================================================ helpers
def pval(b, e):
    """The value-mode p of a market (liquid, race-scaled Polymarket), as Bot.value_p sees it in a cycle."""
    ex = b.ex.get(e)
    if ex is None or e not in (b.cur_liquid or ()):
        return None
    return b.scaled_ref(ex)


def resting(api):
    """Our resting orders in YES terms: [(eid, is_bid, price, qty)]."""
    res = []
    for o in api.orders.values():
        yb, yp = api.yes_view(o)
        res.append((o["exchangeId"], yb, yp, o.get("quantity") or o.get("quantityRemaining") or 0))
    return res


def touch(api, e):
    bk = api.books.get(e) or {}
    bb = (bk.get("bids") or [{}])[0].get("price")
    ba = (bk.get("asks") or [{}])[0].get("price")
    return bb, ba


def reducing(pos, is_bid):
    return (pos >= 1 and not is_bid) or (pos <= -1 and is_bid)


def floor_viol(b, api, margin=0.005):
    """Resting reducing quotes beyond Polymarket -+ margin (a long's ask below p - margin, a short's bid above)."""
    bad = []
    for e, is_bid, px, q in resting(api):
        pos, p = api.inv.get(e, 0.0), pval(b, e)
        if p is None or not reducing(pos, is_bid):
            continue
        if (not is_bid and px < M.ceil_tick(p - margin) - TOL) or (is_bid and px > p + margin + TOL):
            bad.append((b.ex[e].label, "bid" if is_bid else "ask", px, round(p, 4), pos))
    return bad


def crossing(api):
    """Our bid >= our ask in a market, or a resting order of ours through another trader's best price."""
    bad, by = [], defaultdict(lambda: {True: [], False: []})
    for e, is_bid, px, q in resting(api):
        by[e][is_bid].append(px)
    for e, s in by.items():
        bb, ba = touch(api, e)
        if s[True] and s[False] and max(s[True]) >= min(s[False]) - TOL:
            bad.append((e, "self", max(s[True]), min(s[False])))
        if s[True] and ba is not None and max(s[True]) >= ba - TOL:
            bad.append((e, "bid through ask", max(s[True]), ba))
        if s[False] and bb is not None and min(s[False]) <= bb + TOL:
            bad.append((e, "ask through bid", min(s[False]), bb))
    return bad


def tag_cash(api, stage):
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for o in P.orders(api, stage):
        by[o["tag"]][0] += 1
        by[o["tag"]][1] += -min(0.0, o["cash"])
        by[o["tag"]][2] += max(0.0, o["cash"])
    return by


def log_since(n0, needle):
    return [m for _, _, m in P.CAP.lines[n0:] if needle in m]


def fresh(seed=7):
    """A new bot on the snapshot with the live value-mode file (stage 0), warmed up 4 cycles."""
    random.seed(seed)
    api, b = P.build(S, overrides=FILES["stage0_code_only"])
    P.cycles(b, 4, stage="warm")
    return api, b


# ============================================================================================ stage 0
def base_module():
    try:
        src = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REV}:mm_bot.py"], capture_output=True, text=True,
                             timeout=30)
        if src.returncode != 0:
            return None
        path = os.path.join(tempfile.mkdtemp(), "mm_bot_p9head.py")
        with open(path, "w") as f:
            f.write(src.stdout)
        spec = importlib.util.spec_from_file_location("mm_bot_p9head", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.alert, mod.notify, mod.time = P.M.alert, (lambda *a, **k: False), P.CLK
        mod.utcnow = P.M.utcnow
        mod.log.addHandler(P.CAP)
        mod.log.propagate = False
        return mod
    except Exception as e:                        # (reported as a failure)
        print("    (base module unavailable:", e, ")")
        return None


def stage0_run(mod, tag):
    """Stage 0 on module mod: 4 warm-up + 8 cycles from the same seed and clock; the orders sent, in YES terms."""
    keep_m, keep_off, keep_tags = P.M, P.CLK.off, P.TAGGED
    P.M = mod
    P.TAGGED = tuple(t for t in P.TAGGED if hasattr(mod.Bot, t))
    try:
        random.seed(3)
        api, b = P.build(S, overrides=FILES["stage0_code_only"])
        P.cycles(b, 12, stage=tag)
        sent = [(o["cycle"], o["eid"], o["yes_buy"], o["yes_price"], o["qty"], o["tag"]) for o in P.orders(api, tag)]
        return sent, P.status_of(b), b
    finally:
        P.M, P.TAGGED = keep_m, keep_tags
        P.CLK.off = keep_off


def stage0():
    out("## Stage 0: code only (the live value-mode file, Package 10 code)")
    base = base_module()
    check(f"stage0: the Package 9 head ({BASE_REV}) loaded for the comparison", base is not None)
    new, st_new, b_new = stage0_run(M, "s0-new")
    if base is not None:
        old, st_old, _ = stage0_run(base, "s0-base")
        same = new == old
        check("stage0: identical orders to the Package 9 head on the live seed (12 cycles: every order, price, size, "
              "feature)", same, (len(new), len(old), [x for x in new if x not in old][:3]))
        ev_ign = (set(getattr(M.Bot, "EV_KEYS", ())) | {"ev_outcome_history"}   # later read-only ev / carry report keys
                  | set(getattr(M.Bot, "MM_FUNDING_KEYS", ())))   # (and P14 mm_funding)
        keys = (set(st_new) ^ set(st_old)) - ev_ign
        check("stage0: identical status.json keys (no bloc / alloc section)", not keys, keys)
        out(f"- 12 cycles on the live seed, Package 10 code vs the Package 9 head {BASE_REV}: {len(new)} orders each, "
            f"identical: {same}; status.json keys identical: {not keys}")
    out(f"- state: reduce-only {st_new['reduce_only']} (worst case {st_new['worst_case_loss']:,.0f} vs backstop 0.8 x "
        f"account = {0.8 * b_new.last_equity:,.0f}), settlement risk {st_new['settlement_risk']:,.0f} (cap 0.30 x "
        f"account = {0.30 * b_new.last_equity:,.0f}), share party delta {st_new['party_delta']:,.0f}, cash gate left "
        f"{st_new.get('cash_gate_left')}")
    out()
    STATE["s0"] = st_new


# ============================================================================================ stage 1
def stage1(api, b):
    out("## Stage 1: value guard (value_mode, ref_weight 1, no skew, windows 0, bloc delta, backstop 1.3, R7 0.35)")
    before = {(e, ib): px for e, ib, px, q in resting(api)}
    st0 = P.status_of(b)
    bad0 = floor_viol(b, api)
    bad, al = P.apply_stage(b, FILES["stage1_value_guard"])
    check("stage1: applied with no refused key / alert", not bad and not al, (bad, al))
    check("stage1: value_mode, bloc delta, backstop 1.3, R7 0.35, windows 0 in force",
          b.cfg.value_mode and b.cfg.bloc_delta_enabled and b.cfg.worst_case_backstop_frac == 1.3
          and b.cfg.max_worst_case_frac == 0.35 and b.cfg.exit_hours_before_close == 0)
    P.cycles(b, 10, stage="stage1")
    st = P.status_of(b)
    viol, cross = floor_viol(b, api), crossing(api)
    check("stage1: no resting reducing quote below Polymarket - 0.5c (above + 0.5c for a short) anywhere", not viol,
          viol[:5])
    check("stage1: no crossing (our bid < our ask; never through another trader's best)", not cross, cross[:5])
    sent_bad = []
    for o in P.orders(api, "stage1", "quote"):
        p = pval(b, o["eid"])
        if p is None or not reducing(o["pos_before"], o["yes_buy"]):
            continue
        if (not o["yes_buy"] and o["yes_price"] < M.ceil_tick(p - 0.005) - TOL) or (o["yes_buy"] and o["yes_price"]
                                                                                    > p + 0.005 + TOL):
            sent_bad.append((o["label"], o["yes_price"], round(p, 4)))
    check("stage1: no reducing quote SENT beyond the floor either", not sent_bad, sent_bad[:5])
    after = {(e, ib): px for e, ib, px, q in resting(api)}
    red_before = {k: v for k, v in before.items() if reducing(api.inv.get(k[0], 0.0), k[1])}
    red_after = {k: v for k, v in after.items() if reducing(api.inv.get(k[0], 0.0), k[1])}
    moved = [k for k in red_before if k in red_after and abs(red_after[k] - red_before[k]) > TOL]
    gone = [k for k in red_before if k not in red_after]
    new = [k for k in red_after if k not in red_before]
    dist_touch, dist_p, at_touch = [], [], 0
    for (e, ib), px in red_after.items():
        bb, ba = touch(api, e)
        p = pval(b, e)
        t = ba if not ib else bb
        if t is not None:
            d = round((px - t) / M.TICK) if not ib else round((t - px) / M.TICK)
            dist_touch.append(d)
            at_touch += d <= 0
        if p is not None:
            dist_p.append(round(100 * ((px - p) if not ib else (p - px)), 1))
    worst, acct = st["worst_case_loss"], b.last_equity
    check("stage1: out of global reduce-only (worst case below 1.3 x account, settlement risk below 0.35 x account)",
          not st["reduce_only"], (worst, st["settlement_risk"], acct))
    check("stage1: bloc delta in status.json, inside the 5% cap (H: ~+2.55k/sd)",
          st.get("bloc_delta") is not None and abs(st["bloc_delta"]) <= 0.05 * acct, st.get("bloc_delta"))
    out(f"- before (stage 0, last cycle): {len(red_before)} reducing quotes resting, {len(bad0)} of them below "
        "Polymarket - 0.5c (the reduce-only skew)")
    out(f"- after 10 cycles: {len(red_after)} reducing quotes resting; {len(moved)} moved in price, {len(gone)} pulled, "
        f"{len(new)} new; 0 below the floor, 0 crossing")
    if dist_touch:
        c = Counter(min(x, 20) for x in dist_touch)
        out(f"- where the reducing quotes rest vs the other traders' touch (ticks behind; 20 = 20+): "
            + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())) + f"; at or inside the touch {at_touch}")
    if dist_p:
        c = Counter(min(int(x // 1), 10) for x in dist_p)
        out("- distance from Polymarket p in cents (+ = above p for an ask / below p for a bid; floor -0.5c): "
            + ", ".join(f"{k:+d}c: {v}" for k, v in sorted(c.items())))
    out(f"- risk: reduce-only {st0['reduce_only']} -> {st['reduce_only']}; worst case {worst:,.0f} vs the new backstop "
        f"1.3 x {acct:,.0f} = {1.3 * acct:,.0f}; settlement risk {st['settlement_risk']:,.0f} vs 0.35 x account = "
        f"{0.35 * acct:,.0f}; bloc delta {st.get('bloc_delta'):+,.0f}/sd ({100 * (st.get('bloc_delta_frac') or 0):+.2f}% "
        f"of the account; cap 5%) against the share party delta {st['party_delta']:,.0f} (cap 15% = "
        f"{0.15 * acct:,.0f} shares)")
    P.summarise(api, "stage1", "stage1")
    STATE["s1"] = dict(moved=len(moved), resting=len(red_after), worst=worst, ro=st["reduce_only"],
                       bloc=st.get("bloc_delta"), sr=st["settlement_risk"], below0=len(bad0))


# ============================================================================================ stage 2
def alloc_orders(api, stage):
    return P.orders(api, stage, "alloc_tick")


def stage2(api, b, hours=4, label="stage2", report=True, extra=None):
    n0 = len(P.CAP.lines)
    bad, al = P.apply_stage(b, FILES["stage2_allocator"])
    check(f"{label}: applied with no refused key / alert", not bad and not al, (bad, al))
    for k, v in (extra or {}).items():
        setattr(b.cfg, k, v)
    pins = {"Rep U.S. Senate", "Dem U.S. Senate"}
    hourly, t0 = [], P.CLK.off
    cash0 = api.cash
    for h in range(hours):
        P.cycles(b, 60, step=60.0, stage=label)
        st = P.status_of(b)
        al_ = st.get("alloc") or {}
        hourly.append((h + 1, api.cash, st.get("cash_gate_left"), al_.get("sold"), al_.get("bought"),
                       al_.get("ev_gain_est"), al_.get("pairs_planned"), al_.get("blocked_by"), st["reduce_only"],
                       st.get("bloc_delta")))
        api.refill()                                  # the books replenish to the snapshot's depth every hour
    ao = alloc_orders(api, label)
    runs = log_since(n0, "ALLOC run:")
    plan_lines = []
    for _, _, m in P.CAP.lines[n0:]:
        if m.startswith("ALLOC run:") and plan_lines:
            break
        if m.startswith("ALLOC ") and ("-> " in m) and not m.startswith("ALLOC run"):
            plan_lines.append(m)
    sold_lines = log_since(n0, "ALLOC sold ")
    bought_lines = log_since(n0, "ALLOC bought ")
    set_lines = [m for m in log_since(n0, "sets unwound")]
    set_usd, set_cost, sum_at = 0.0, 0.0, {}
    for _, _, m in P.CAP.lines[n0:]:              # the EV the sets gave up: sets x (asks sum - 1) at registration
        mt = re.match(r"ALLOC (.+) NO\+NO set: registered for the short-set unwind at asks sum <= ([0-9.]+)", m)
        if mt:
            sum_at[mt.group(1)] = float(mt.group(2))
        mt = re.match(r"ALLOC (.+) NO\+NO set: ([0-9.]+) sets unwound \(~\$([0-9.]+) freed\)", m)
        if mt:
            set_usd += float(mt.group(3))
            set_cost += float(mt.group(2)) * max(0.0, sum_at.get(mt.group(1), 1.0) - 1)
    pinned = [o for o in ao if o["label"] in pins]
    pinned_sets = [m for m in log_since(n0, "registered for the short-set unwind") if "U.S. Senate" in m]
    check(f"{label}: nothing pinned sold (no allocator order / set registration on the U.S. Senate control legs)",
          not pinned and not pinned_sets, (pinned[:2], pinned_sets[:2]))
    # sells: never a flip, a long sold at >= p / 1.02 (edge-held <= 2%), a short bought back as a covered NO sale
    sbad = []
    for o in ao:
        p, pos = pval(b, o["eid"]), o["pos_before"]
        if (pos > 0 and not o["yes_buy"]) or (pos < 0 and o["yes_buy"]):
            if o["qty"] > abs(pos) + TOL:
                sbad.append(("flip", o["label"], o["qty"], pos))
            if p is not None:
                edge = (p - o["yes_price"]) / o["yes_price"] if pos > 0 else (o["yes_price"] - p) / (1 - o["yes_price"])
                if edge > b.cfg.alloc_max_edge_sell + 1e-6:
                    sbad.append(("edge-held", o["label"], o["yes_price"], round(p, 4), round(edge, 4)))
            if pos < 0 and o["side"] == "yes":
                sbad.append(("short bought back as a YES purchase", o["label"]))
        if o["own_resting"]:
            sbad.append(("own order resting at send", o["label"]))
    check(f"{label}: every allocator sale shrinks a position (no flip), edge-held <= 2%, shorts as covered NO sales, "
          "no own order resting", not sbad, sbad[:5])
    # sequencing: a paired buy only on a cycle after its funding sale (or set unwind), never the same cycle
    buys = [o for o in ao if (o["yes_buy"] and o["pos_before"] >= 0) or (not o["yes_buy"] and o["pos_before"] <= 0)]
    first_sale = min([o["cycle"] for o in ao if o not in buys] + [10 ** 9])
    seq_ok = all(o["cycle"] > first_sale for o in buys) if buys else True
    check(f"{label}: sell -> cash read -> buy (no allocator buy before or in the cycle of the first sale)", seq_ok,
          [(o["cycle"], o["label"]) for o in buys[:3]])
    per_cycle = Counter(o["cycle"] for o in ao)
    check(f"{label}: <= 4 allocator orders a cycle", max(per_cycle.values() or [0]) <= 4, per_cycle.most_common(2))
    # turnover per rolling hour (sales + set unwinds + buys from spare cash), from the status flows
    st = P.status_of(b)
    al_ = st.get("alloc") or {}
    tc = tag_cash(api, label)
    if report:
        out("## Stage 2: the allocator (sets at 0.06 per $, reserve 15k, pins U.S. Senate)")
        out(f"- {hours} simulated hours at 60-s cycles, books refilled to the snapshot depth each hour; "
            f"{len(runs)} allocator runs")
        out("- the first plan (cash ~0, so every line is a reserve REFILL: no pair is planned until the cash is "
            "above the 15k reserve):")
        out()
        for m in plan_lines[:16]:
            out(f"    {m}")
        out()
        out(f"- executed: {len(sold_lines)} sales, {len(set_lines)} NO+NO set unwinds (~${set_usd:,.0f} freed by the "
            f"sets at an EV cost of ~${set_cost:,.0f}, {100 * set_cost / max(set_usd, 1):.1f}% per $), "
            f"{len(bought_lines)} buys; totals sold ${al_.get('sold_total', 0):,.0f}, bought "
            f"${al_.get('bought_total', 0):,.0f}")
        reached = next((h for h, cash, cgl, *_ in hourly if (cgl or 0) >= 15000 - 1), None)
        out(f"- the 15k reserve: {'reached in hour ' + str(reached) if reached else 'NOT reached in ' + str(hours) + ' h'}"
            " (cash_gate_left each hour in the table: the stale-quote takes spend what the sales free, see below)")
        out(f"- cash at switch-on {cash0:,.0f}; most writes in one cycle {P.WRITES.get(label, 0)} (the fake does not "
            "enforce 28/min)")
        out()
        out("| hour | cash (fake ledger) | cash_gate_left | alloc sold (run) | bought (run) | ev_gain_est | pairs planned | "
            "blocked_by | reduce-only | bloc delta |")
        out("|---|---|---|---|---|---|---|---|---|---|")
        for h, cash, cgl, sold, bought, ev, npl, blk, ro, bl in hourly:
            out(f"| {h} | {cash:,.0f} | {cgl} | {sold} | {bought} | {ev} | {npl} | {blk} | {ro} | "
                f"{(bl or 0):+,.0f} |")
        out()
        out("Where the cash went (all orders of the stage, by feature; $ paid / $ received):")
        out()
        for tag, (n, paid, recv) in sorted(tc.items()):
            out(f"- {tag}: {n} orders, paid ${paid:,.0f}, received ${recv:,.0f}")
        ev = usd_ = 0.0
        for o in P.orders(api, label, "take_stale_quotes"):
            p = pval(b, o["eid"])
            if p is None or not o["traded"]:
                continue
            ev += o["traded"] * ((p - o["yes_price"]) if o["yes_buy"] else (o["yes_price"] - p))
            usd_ += o["traded"] * (o["yes_price"] if o["yes_buy"] else 1 - o["yes_price"])
        out(f"- the stale-quote takes' value at the outcome (vs race-scaled Polymarket): +${ev:,.0f} on ${usd_:,.0f} "
            f"({100 * ev / max(usd_, 1):.1f}% per $), against the sets' {100 * set_cost / max(set_usd, 1):.1f}% per $ "
            "given up to fund them")
        STATE["take_ev"] = (ev, usd_)
        out()
        P.summarise(api, label, label)
    STATE[label] = dict(runs=len(runs), sold=al_.get("sold_total", 0), bought=al_.get("bought_total", 0),
                        sets=set_usd, set_cost=set_cost, nsets=len(set_lines), cash=api.cash, hourly=hourly, tc=dict(tc),
                        plan=plan_lines, writes=P.WRITES.get(label, 0))
    return hourly


# ============================================================================================ stage 3
def adding_quotes(b, api):
    rows = []
    for e, is_bid, px, q in resting(api):
        pos, p = api.inv.get(e, 0.0), pval(b, e)
        if reducing(pos, is_bid) and (q <= abs(pos) + TOL):
            continue
        bb, ba = touch(api, e)
        top = (is_bid and (bb is None or px >= bb - TOL)) or (not is_bid and (ba is None or px <= ba + TOL))
        rows.append(dict(eid=e, label=b.ex[e].label, bid=is_bid, px=px, q=q, p=p, pos=pos, top=top,
                         cash=q * (px if is_bid else 1 - px)))
    return rows


def computed_adding(b, api):
    """The adding sides decide computed this cycle (Ex.quote: before the cash gate / write budget), YES terms."""
    rows = []
    for e, ex in b.ex.items():
        q, pos = ex.quote, api.inv.get(e, 0.0)
        bb, ba = touch(api, e)
        for is_bid, px, sz in ((True, q.bid, q.bid_size), (False, q.ask, q.ask_size)):
            if px is None or not sz or (reducing(pos, is_bid) and sz <= abs(pos) + TOL):
                continue
            top = (is_bid and (bb is None or px >= bb - TOL)) or (not is_bid and (ba is None or px <= ba + TOL))
            rows.append(dict(eid=e, label=ex.label, bid=is_bid, px=px, q=sz, p=pval(b, e), pos=pos, top=top,
                             cash=sz * (px if is_bid else 1 - px)))
    return rows


def classify(rows, b):
    h, lo, hi = b.cfg.value_quote_hurdle, b.cfg.value_mid_low, b.cfg.value_mid_high
    viol, cats = [], Counter()
    for r in rows:
        p = r["p"]
        if p is None:
            r["kind"] = "no liquid p"
            cats["no liquid p"] += 1
            continue
        if p < lo - TOL or p > hi + TOL:
            if r["bid"] and r["px"] > M.floor_tick(p / (1 + h)) + TOL:
                viol.append(("tail bid above p/(1+h)", r["label"], r["px"], round(p, 4)))
            if not r["bid"] and r["px"] < M.ceil_tick(1 - (1 - p) / (1 + h)) - TOL:
                viol.append(("tail ask below 1-(1-p)/(1+h)", r["label"], r["px"], round(p, 4)))
            r["kind"] = ("favourite bid" if p > hi else "longshot bid") if r["bid"] else \
                ("longshot ask" if p < lo else "favourite ask")
        else:
            r["kind"] = "middle bid" if r["bid"] else "middle ask"
        cats[r["kind"]] += 1
    return viol, cats


def stage3(api, b):
    out("## Stage 3: value market making (value_quote_hurdle 0.08, capital_ceiling_adding_size_factor 0.5)")
    bad, al = P.apply_stage(b, FILES["stage3_value_mm"])
    check("stage3: applied with no refused key / alert", not bad and not al, (bad, al))
    api.cash += 20000.0          # (the reserve the allocator is meant to keep: see stage 2 for whether it gets there)
    P.cycles(b, 10, stage="stage3")
    comp = computed_adding(b, api)
    cviol, ccats = classify(comp, b)
    check("stage3: every COMPUTED adding quote in the tails obeys the hurdle", not cviol, cviol[:5])
    cnear = Counter(r["kind"] for r in comp if r["top"])
    check("stage3: computed, in the tails only favourite bids and longshot asks at the top of the book",
          cnear.get("longshot bid", 0) + cnear.get("favourite ask", 0) == 0, cnear)
    two_way_c = {r["eid"] for r in comp if r["kind"] == "middle bid"} & {r["eid"] for r in comp
                                                                        if r["kind"] == "middle ask"}
    ccash = sum(r["cash"] for r in comp)
    ctop = sum(1 for r in comp if r["top"])
    out(f"- computed by decide after 10 cycles (before the cash gate): {len(comp)} adding quote sides in "
        f"{len({r['eid'] for r in comp})} markets, {ctop} at the top of the book; cash they need if all filled "
        f"${ccash:,.0f}")
    out("- computed by kind (all / at the top): " + ", ".join(f"{k} {v} / {cnear.get(k, 0)}"
                                                              for k, v in sorted(ccats.items())))
    out(f"- middle markets computed two-way (adding both sides): {len(two_way_c)}")
    csz = sorted(r["q"] for r in comp)
    if csz:
        out(f"- computed sizes: median {csz[len(csz) // 2]:,} shares (min {csz[0]:,}, max {csz[-1]:,})")
    tc = tag_cash(api, "stage3")
    out("- where the +20k went in these 10 cycles (paid / received): " + ", ".join(
        f"{k} {v[1]:,.0f} / {v[2]:,.0f}" for k, v in sorted(tc.items())) + f"; cash gate left now "
        f"{P.status_of(b).get('cash_gate_left')}")
    STATE["s3c"] = dict(sides=len(comp), top=ctop, cats=dict(ccats), cash=ccash, two_way=len(two_way_c),
                        est=24 * (0.44 * (len(comp) - ctop) - 0.09 * ctop), tc=dict(tc))
    rows = adding_quotes(b, api)
    h, lo, hi = b.cfg.value_quote_hurdle, b.cfg.value_mid_low, b.cfg.value_mid_high
    viol, cats = [], Counter()
    for r in rows:
        p = r["p"]
        if p is None:
            cats["no liquid p"] += 1
            continue
        tail = p < lo - TOL or p > hi + TOL
        if tail:
            if r["bid"] and r["px"] > M.floor_tick(p / (1 + h)) + TOL:
                viol.append(("tail bid above p/(1+h)", r["label"], r["px"], round(p, 4)))
            if not r["bid"] and r["px"] < M.ceil_tick(1 - (1 - p) / (1 + h)) - TOL:
                viol.append(("tail ask below 1-(1-p)/(1+h)", r["label"], r["px"], round(p, 4)))
            kind = ("favourite bid" if p > hi else "longshot bid") if r["bid"] else \
                ("longshot ask" if p < lo else "favourite ask")
        else:
            kind = "middle bid" if r["bid"] else "middle ask"
        r["kind"] = kind
        cats[kind] += 1
    check("stage3: every adding quote in the tails obeys the hurdle (bid <= p/1.08, ask >= 1-(1-p)/1.08)", not viol,
          viol[:5])
    near = Counter(r["kind"] for r in rows if r.get("kind") and r["top"])
    tail_near_wrong = near.get("longshot bid", 0) + near.get("favourite ask", 0)
    check("stage3: in the tails only favourite bids and longshot asks reach the top of the book", tail_near_wrong == 0,
          near)
    mid_two_way = {r["eid"] for r in rows if r.get("kind") == "middle bid"} & \
        {r["eid"] for r in rows if r.get("kind") == "middle ask"}
    cash_need = sum(r["cash"] for r in rows)
    sides_top = sum(1 for r in rows if r["top"])
    sides = len(rows)
    est = 24 * (0.44 * (sides - sides_top) - 0.09 * sides_top)
    out(f"- RESTING after the cash gate (+20k cash given to the fake so the adding side can be seen; stage 2 shows "
        f"whether the allocator gets there): {sides} adding quote sides resting, {sides_top} at the top of the book")
    out("- by kind (all / at the top): " + ", ".join(f"{k} {v} / {near.get(k, 0)}" for k, v in sorted(cats.items())))
    out(f"- middle markets quoted two-way (adding on both sides): {len(mid_two_way)}")
    szs = sorted(r["q"] for r in rows)
    if szs:
        out(f"- sizes: median {szs[len(szs) // 2]:,} shares (min {szs[0]:,}, max {szs[-1]:,}); cash the adding quotes "
            f"need if all filled: ${cash_need:,.0f}")
    out(f"- F's side-hour model (1-h mark-out: +0.44 $ per side-hour quoted below the top, -0.09 at the top): "
        f"~${est:,.0f}/day on these {sides} resting sides; ~${STATE['s3c']['est']:,.0f}/day if all "
        f"{STATE['s3c']['sides']} computed sides were funded (F-15's central 1.0-1.4k/day needs 15-25k of rotating cash and "
        "~350-500k maker shares a day; I-4: tail value-side fills +5.6% per $, middle +0.64c a share at the outcome)")
    top_rows = sorted([r for r in rows if r["top"]], key=lambda r: -r["cash"])[:10]
    if top_rows:
        out("- the biggest adding quotes at the top of the book:")
        out()
        for r in top_rows:
            p_txt = f"{r['p']:.3f}" if r.get('p') is not None else "none"
            out(f"    {r['label']:<30} {'BID' if r['bid'] else 'ASK'} {r['q']:>6} @ {r['px']:.3f} (p {p_txt}, "
                f"pos {r['pos']:+.0f}, {r.get('kind')})")
        out()
    P.summarise(api, "stage3", "stage3")
    STATE["s3"] = dict(sides=sides, top=sides_top, cats=dict(cats), near=dict(near), cash=cash_need, est=est,
                       two_way=len(mid_two_way))


# ============================================================================================ scenarios
def scenario(name, rewrite):
    """A fresh bot through stages 1-3, then the books rewritten (rewrite(api, b)) and 3 hours of stage 3."""
    api, b = fresh()
    P.apply_stage(b, FILES["stage1_value_guard"])
    P.cycles(b, 3, stage=f"{name}-s1")
    P.apply_stage(b, FILES["stage3_value_mm"])
    P.cycles(b, 2, stage=f"{name}-s3")
    rewrite(api, b)
    api.base_books = {e: {"bids": [dict(x) for x in v["bids"]], "asks": [dict(x) for x in v["asks"]]}
                      for e, v in api.books.items()}
    n0 = len(P.CAP.lines)
    pos0 = dict(api.inv)
    lab = f"{name}-run"
    for _ in range(3):
        P.cycles(b, 60, step=60.0, stage=lab)
        api.refill()
    os_ = P.orders(api, lab)
    lost, sells = [], []
    for o in os_:
        p, pos = pval(b, o["eid"]), o["pos_before"]
        if p is None or not o["traded"]:
            continue
        if (pos > 0 and not o["yes_buy"]) or (pos < 0 and o["yes_buy"]):
            give = (p - o["yes_price"]) if pos > 0 else (o["yes_price"] - p)
            sells.append((o["tag"], o["label"], o["yes_price"], round(p, 4), o["traded"], round(give, 4)))
            lim = 0.005 if o["tag"] == "quote" else (0.02 * o["yes_price"] if pos > 0 else 0.02 * (1 - o["yes_price"]))
            if o["tag"] == "take_arbitrage":
                continue                                   # (set unwinds: the cost rule of the set, not per leg)
            if give > lim + 1e-6:
                lost.append((o["tag"], o["label"], o["yes_price"], round(p, 4), round(give, 4)))
    viol = floor_viol(b, api)
    st = P.status_of(b)
    al_ = st.get("alloc") or {}
    # churn: a market the allocator sold (reduced) where our market maker now rests an ADDING order back toward the
    # sold side at (or better for the counterparty than) the sale price
    sold_at = {}
    for o in os_:
        if o["tag"] == "alloc_tick" and o["traded"] and ((o["pos_before"] > 0 and not o["yes_buy"])
                                                         or (o["pos_before"] < 0 and o["yes_buy"])):
            sold_at[o["eid"]] = (o["pos_before"] > 0, o["yes_price"])
    churn = []
    for e, is_bid, px, q in resting(api):
        if e in sold_at:
            was_long, spx = sold_at[e]
            if (was_long and is_bid and px >= spx - 0.005 - TOL) or (not was_long and not is_bid and px <= spx + 0.005
                                                                      + TOL):
                churn.append((b.ex[e].label, "bid" if is_bid else "ask", px, spx, q))
    return api, b, dict(churn=churn, lost=lost, sells=sells, viol=viol, tc=dict(tag_cash(api, lab)), sold=al_.get("sold_total", 0),
                        bought=al_.get("bought_total", 0), runs=len(log_since(n0, "ALLOC run:")),
                        bought_lines=log_since(n0, "ALLOC bought "), sold_lines=log_since(n0, "ALLOC sold "),
                        sets=log_since(n0, "sets unwound"), cash=api.cash, ro=st["reduce_only"],
                        moved=sum(1 for e in pos0 if abs(api.inv.get(e, 0.0) - pos0[e]) >= 1))


def tilt_rise(api, b):
    for e, bk in api.books.items():
        p = pval(b, e)
        if p is None:
            continue
        if p < 0.5:
            for lv in bk.get("asks") or []:
                lv["price"] = round(min(0.999, lv["price"] + 0.03), 3)
        else:
            for lv in bk.get("bids") or []:
                lv["price"] = round(max(0.001, lv["price"] - 0.03), 3)


def convergence(api, b):
    for e, bk in api.books.items():
        p = pval(b, e)
        if p is None:
            continue
        bq = sum(lv["quantity"] for lv in (bk.get("bids") or [])[:1]) or 500
        aq = sum(lv["quantity"] for lv in (bk.get("asks") or [])[:1]) or 500
        bid, ask = M.floor_tick(max(0.001, p - 0.01)), M.ceil_tick(min(0.999, p + 0.01))
        bk["bids"] = [{"price": bid, "quantity": bq}, {"price": round(bid - 0.01, 3), "quantity": bq}] if bid > 0.011 \
            else [{"price": bid, "quantity": bq}]
        bk["asks"] = [{"price": ask, "quantity": aq}, {"price": round(ask + 0.01, 3), "quantity": aq}] if ask < 0.989 \
            else [{"price": ask, "quantity": aq}]


def scenarios():
    out("## Scenarios (fresh bot: stage 1 -> stage 3 incl. the allocator, then the books rewritten; 3 h at 60-s cycles)")
    for name, fn, what in (("tilt", tilt_rise, "TILT RISE: longshot asks +3c, favourite bids -3c"),
                           ("conv", convergence, "CONVERGENCE: every book to Polymarket +-1c (2 levels)")):
        api, b, r = scenario(name, fn)
        STATE[name] = r
        check(f"{name}: no value sold (quotes >= p - 0.5c; allocator sales at edge-held <= 2%)", not r["lost"],
              r["lost"][:5])
        check(f"{name}: no resting reducing quote beyond the floor", not r["viol"], r["viol"][:5])
        check(f"{name}: no churn (no adding quote of ours buying back what the allocator sold, within 0.5c of the "
              "sale price)", not r["churn"], r["churn"][:5])
        out(f"### {what}")
        out(f"- allocator runs {r['runs']}: sold ${r['sold']:,.0f}, bought ${r['bought']:,.0f} ({len(r['sold_lines'])} "
            f"sales, {len(r['sets'])} set unwinds, {len(r['bought_lines'])} buys); cash at the end {r['cash']:,.0f}; "
            f"reduce-only {r['ro']}; positions changed in {r['moved']} markets")
        by_tag = Counter(s[0] for s in r["sells"])
        out(f"- position-reducing trades by feature: {dict(by_tag)}; biggest give-up vs p (positive = sold below p): "
            + ", ".join(f"{s[1]} {s[2]:.3f} vs {s[3]:.3f} ({s[5]:+.3f})" for s in sorted(r["sells"],
                                                                                        key=lambda s: -s[5])[:4]))
        out("- cash by feature (paid / received): " + ", ".join(f"{k} {v[1]:,.0f} / {v[2]:,.0f}"
                                                                for k, v in sorted(r["tc"].items())))
        for m in (r["sold_lines"][:3] + r["bought_lines"][:3]):
            out(f"    {m}")
        out()


# ============================================================================================ the run
def main():
    global S
    if not os.path.exists(os.path.join(P.SNAP, "md.sqlite")):
        print(f"SKIP: no snapshot at {P.SNAP}")
        return 0
    t0 = _rt.time()
    S = P.load_snapshot()
    for k, path in FILES.items():
        raw = json.load(open(path))
        good, bad = M.validate_overrides({k: v for k, v in raw.items() if k not in RETIRED_KEYS}, M.Config())
        check(f"files: {k} validates (no refused key)", not bad and len(good) == len(raw) - sum(x in raw for x in RETIRED_KEYS), bad)
        check(f"files: {k} keeps arb_enabled false and ref_tilt_headline true, never resets the adding factor below "
              "the live 0", raw.get("arb_enabled") is False and raw.get("ref_tilt_headline") is True
              and raw.get("capital_ceiling_adding_size_factor", -1) >= 0)
        base_keys = {x: v for x, v in raw.items() if x in LIVE}
        diff = {x for x in LIVE if x not in raw}
        check(f"files: {k} carries every key of the live file", not diff, diff)
        if k == "stage0_code_only":
            check("files: stage0 == the live value-mode file (less the keys retired on simplify)",
                  {k: v for k, v in raw.items() if k not in RETIRED_KEYS}
                  == {k: v for k, v in LIVE.items() if k not in RETIRED_KEYS})
        _ = base_keys
    out("# Package 10 dry run on the fake exchange, seeded with the live state of 3 Oct 22:47 UTC")
    out()
    out(f"Generated by `tests/test_p10_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {P.SNAP} with the "
        "tests/dryrun_harness.py harness. Base = the owner's live value-mode file (stage 0); each staged file of "
        "deploy/package10/ applied through `check_overrides`; full `Bot.cycle`s on a simulated clock; every order the "
        "fake receives logged with the feature that sent it.")
    out()
    stage0()
    api, b = fresh()
    out(f"- seed: {len(b.ex)} markets in {len(b.groups)} races, {len(api.inv)} positions, account {api.account:,.0f} "
        f"(cash {api.cash:,.0f})")
    out()
    stage1(api, b)
    stage2(api, b)
    # counterfactual: the same stage 2 with the stale-quote takes off (who would reach the reserve, and when)
    api2, b2 = fresh()
    P.apply_stage(b2, FILES["stage1_value_guard"])
    P.cycles(b2, 3, stage="nt-s1")
    hourly = stage2(api2, b2, hours=4, label="stage2-notakes", report=False, extra={"take_enabled": False})
    reached = next((h for h, cash, cgl, *_ in hourly if (cgl or 0) >= 15000 - 1), None)
    out("- counterfactual, the same stage 2 with `take_enabled` false (the takes no longer spend the freed cash): "
        "cash_gate_left by hour " + ", ".join(f"{h}: {cgl}" for h, cash, cgl, *_ in hourly)
        + f"; the 15k reserve {'reached in hour ' + str(reached) if reached else 'not reached in 4 h'}; sold "
        f"${STATE['stage2-notakes']['sold']:,.0f} (sets ${STATE['stage2-notakes']['sets']:,.0f} at EV cost "
        f"~${STATE['stage2-notakes']['set_cost']:,.0f})")
    out()
    stage3(api, b)
    print(f"(stages done in {_rt.time() - t0:.0f} s)")
    scenarios()
    findings()
    print(f"(all done in {_rt.time() - t0:.0f} s)")
    return 0


def findings():
    s1, s2, s3, nt = STATE.get("s1", {}), STATE.get("stage2", {}), STATE.get("s3c", {}), STATE.get("stage2-notakes", {})
    tl, cv = STATE.get("tilt", {}), STATE.get("conv", {})
    summ = [
        "## Summary (numbers below; analysis/p10/REDTEAM.md for the findings)",
        f"- Stage 0: identical orders to the Package 9 head on the live seed; the book is in global reduce-only "
        f"(worst case vs the 0.8 backstop), {s1.get('below0', 0)} of its resting reducing quotes sit below "
        "Polymarket - 0.5c (the reduce-only skew).",
        f"- Stage 1: out of reduce-only (worst case {s1.get('worst', 0):,.0f} vs 1.3 x account); {s1.get('resting', 0)} "
        f"reducing quotes, none below p - 0.5c, none crossing; bloc delta {s1.get('bloc') or 0:+,.0f}/sd (cap ~5.05k).",
        f"- Stage 2: every allocator line is a reserve REFILL (cash ~0 < 15k): ${s2.get('sold', 0):,.0f} freed in 4 h, "
        f"${s2.get('sets', 0):,.0f} of it from NO+NO sets at ~${s2.get('set_cost', 0):,.0f} of EV; 0 pairs, 0 buys; "
        f"the stale-quote takes spent ${(s2.get('tc', {}).get('take_stale_quotes') or [0, 0])[1]:,.0f} of it, so the "
        f"15k reserve is never reached (takes off: {', '.join(str(int(c or 0)) for _, _, c, *_ in nt.get('hourly', []))}"
        " by hour).",
        f"- Stage 3: decide computes {s3.get('sides', 0)} adding sides ({s3.get('top', 0)} at the top; "
        f"{s3.get('two_way', 0)} middle markets two-way; ${s3.get('cash', 0):,.0f} of cash if all filled; F's model "
        f"~${s3.get('est', 0):,.0f}/day), but the takes spend the cash first: "
        f"{(s3.get('tc', {}).get('take_stale_quotes') or [0, 0])[1]:,.0f} of the +20k in 10 cycles; 5 adding sides rest.",
        f"- The takes are value buys: +${STATE.get('take_ev', (0, 0))[0]:,.0f} at the outcome on "
        f"${STATE.get('take_ev', (0, 0))[1]:,.0f} in stage 2 (who gets the cash, takes or the market maker, is the "
        "owner's call: REDTEAM.md C-1).",
        f"- Tilt rise: no position sold below value (only set unwinds within their cost); convergence: the allocator "
        f"frees ${cv.get('sold', 0):,.0f} (sales at edge-held 1.0-1.2% + sets) into the reserve and buys nothing (no "
        "level with >= 5% edge at Polymarket +-1c).",
        ""]
    _ = tl
    OUT[2:2] = summ
    out("## Caveats of the fake")
    out("Other traders never trade with our resting quotes (stage 3's market-making P&L is F's side-hour model, not "
        "simulated fills); the books move only where we take, plus the hourly refill to the snapshot depth and the two "
        "scenario rewrites; Polymarket is flat (no stale-p path); every Polymarket price counts as liquid; marks stay at "
        "the snapshot's; the 28/min write limiter is not modelled (FakeApi reports unlimited writes, so the allocator's "
        "0.3 share allows its 4 orders a cycle; live ~2) and the self-test is off (live: passed).")


S = None
STATE = {}

if __name__ == "__main__":
    rc = main()
    if os.environ.get("P10_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
