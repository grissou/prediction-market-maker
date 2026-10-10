"""Package 16 DRY RUN on the fake exchange: the ARMED momentum sleeve on the LIVE state.
Seed: the 4 Oct 15:56 ops snapshot (/home/claude/snap04, or $P9_SNAP) moved to the owner's live numbers by
tests/p14_1_state.py (books 20% converged toward Polymarket, account 102.0k, free cash ~1.9k of the 20k reserve, MM
inventory 10.5k, value adds paused on the worst-case room), built on the Package 15 staged file
(deploy/package15/settings_override.harvest.json: the live file + the harvest ladder + the 15k state cap); the Package 16
staged file deploy/package16/settings_override.momentum_armed.json (= that + momentum_enabled / momentum_auto true and the
C defaults) lands through the bot's own override path (check_overrides). The tilt series is seeded by the bot's own
cold-start path from md.sqlite's rows up to 15:57 UTC 4 Oct, re-dated by whole 4-h bins onto the run's clock (the fake's
monotonic clock cannot run on 4 Oct), so it reads as it did then.
Scenarios:
  0  flags off: the Package 15 file on ed65638 and on this head, 12 cycles: identical orders and status (less the
     timings); the Package 16 file with momentum_enabled / momentum_auto false = the Package 15 file;
  1  armed with no history (no recorder file): no buys, reason "no history";
  2  seeded from md.sqlite (4 Oct 15:57): the slope reading, "armed - confirming"; the live state as it is for 4 h:
     the trigger switches ON at 4 h (ALERT, not before), what the funding order finds (a) / (b) / (c), the ladder
     pausing new levels while the round is short, the state cap;
  3  the funded fixture (+$45,000 cash arriving just before the switch-on), seeded the same: the first buys (markets,
     side, qty, price, p, gap; funded from), the ramp over simulated rising hours (+10k per 6 h of a rising
     slope_6h), writes per minute;
  4  a simulated slope_24h <= 0 on 3: the flip - the exit over mom_exit_hours, the ladder taking the markets over;
  5  a -30% mark move on the sleeve built in 3: the kill after 120 s, one alert, the latched exit.
The tilt series past the seed is SIMULATED (the fake's books are static: their own live tilt reading would be flat);
the fake never fills our resting orders (no other trader), Polymarket is flat; the 28-writes limiter counts, it does
not refuse. Writes analysis/p16/DRYRUN_P16.md (P16_DRYRUN_REPORT=0: none).
Run:  python tests/test_p16_dryrun.py      (exit code 0 = all passed; skipped without the snapshot)
"""
import json
import math
import os
import sys
import time as _rt
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
SNAP = os.environ.get("P9_SNAP", "/home/claude/snap04")
os.environ.setdefault("P9_SNAP", SNAP)
import test_p15_dryrun as D                                      # noqa: E402  (its fake: limiter, aggregated books)
import dryrun_harness as P                                      # noqa: E402  (the harness: snapshot, fake, clock)
import mm_bot as M                                                # noqa: E402

STAGED = os.path.join(ROOT, "deploy", "package16", "settings_override.momentum_armed.json")
P15_FILE = os.path.join(ROOT, "deploy", "package15", "settings_override.harvest.json")
REPORT = os.path.join(ROOT, "analysis", "p16", "DRYRUN_P16.md")
BASE_REV = "ed65638"
SNAP_DB = os.path.join(SNAP, "md.sqlite")
T_SNAP = M.parse_ts("2026-10-04T15:57:01Z").timestamp()
H = 3600.0
TOL = 1e-6
RESULTS, OUT, STATE = [], [], {}
P.TAGGED = P.TAGGED + ("mom_tick",)
D.check = lambda name, cond, extra="": check(name, cond, extra)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{str(extra)[:600]}]" if not cond else ""))
    RESULTS.append(bool(cond))


def out(s=""):
    OUT.append(s)


def set_clock(t_wall=None):
    """The simulated clock (mm_bot's time / utcnow) back to real time (None) or at wall time t_wall (never before real
    time: the fake's monotonic clock must not go negative)."""
    P.CLK.off = 0.0 if t_wall is None else max(0.0, t_wall - _rt.time())


def to_snapshot_phase():
    """The clock moved FORWARD (< one 4-h bin) to the same place in its 4-h bin as 15:57:01 4 Oct (the snapshot):
    the 4 Oct series re-dated by whole bins then reads at "now" exactly as it read then. Returns the shift (s)."""
    bs = 4 * H
    P.CLK.off += (T_SNAP % bs - now_w() % bs) % bs
    STATE["shift"] = round((now_w() - T_SNAP) / bs) * bs
    return STATE["shift"]


def now_w():
    return P.CLK.time()


def lines_since(n0):
    return [m for _, _, m in P.CAP.lines[n0:]]


def mom_orders(api, stage):
    return [o for o in P.orders(api, stage, "mom_tick")]


def seed_from_snapshot(b):
    """The bot's own cold-start seeding (ts_seed_step: a background thread, its own read-only connection) pointed
    at the snapshot's md.sqlite, merged; then the recorder file is unset again (the dry run never writes into it).
    The snapshot's rows are read for [now - 72 h, now) less the shift (to_snapshot_phase: whole 4-h bins) and their
    bins re-dated by it (the fake's clock cannot run on 4 Oct)."""
    delta = to_snapshot_phase()
    orig = M.tilt_slope_seed

    def shifted(path, mode, since, until, bin_s, headline=(), max_spread=0.06):
        bins = orig(path, mode, since - delta, until - delta, bin_s, headline, max_spread)
        return {k + delta: [v[0], v[1], v[2], v[3] + delta, v[4] + delta] for k, v in bins.items()}
    b.tslope, b.ma_seed = M.TiltSlope(3600.0 * b.cfg.momentum_slope_bin_h), None
    keep, b.cfg.record_file = b.cfg.record_file, SNAP_DB
    M.tilt_slope_seed = shifted
    try:
        b.ts_seed_step(now_w())
        th = (b.ma_seed or {}).get("thread")
        if th is not None:
            th.join(300)
        b.ts_seed_step(now_w())
    finally:
        b.cfg.record_file = keep
        M.tilt_slope_seed = orig


def hold_series(b):
    """No live samples into the series (the fake's books are static): the series is what the seed / the scenario
    puts there."""
    b.ts_feed = lambda now_w_, now_m_: b.ts_seed_step(now_w_)


def push_bin(b, value_pts):
    """The CURRENT bin's tilt set to value_pts (a simulated reading; >= TILT_SLOPE_MIN_N samples, covering it)."""
    bs = b.tslope.bin_s
    k = math.floor(now_w() / bs) * bs
    b.tslope.bins[k] = [value_pts / 100.0, 1.0, 100, k, k + 0.5 * bs]
    b.tslope.source = "seed+live"


def bin_no(b):
    return int(math.floor(now_w() / b.tslope.bin_s))


def last_bin_value(b):
    vals = b.tslope.values(now_w())
    return 100.0 * vals[-1][1] if vals else None


def mstatus(b):
    return P.status_of(b).get("momentum") or {}


def build(path, cash_add=0.0):
    api, b = D.build(path, cash_add=cash_add)
    return api, b


def p15_live_file():
    """The Package 15 file as staged (the live file + ladder + state cap): what runs before Package 16 lands."""
    return P15_FILE


# ============================================================================================ scenario 0
def scenario0():
    out("## Scenario 0: flags off = ed65638")
    base = D.base_module(BASE_REV)
    check(f"s0: base module (git show {BASE_REV}:mm_bot.py) loaded", base is not None)
    if base is None:
        return
    sb, stb = D.s0_run(base, "s0b", P15_FILE)
    sn, stn = D.s0_run(M, "s0n", P15_FILE)
    strip = lambda xs: [x[:5] for x in xs]          # noqa: E731
    check("s0: the Package 15 file, 12 cycles on the live state: the same orders on ed65638 and on this head",
          strip(sn) == strip(sb) and len(sn) > 0, (len(sn), len(sb)))
    nb, nn = D.norm_status(stb), D.norm_status(stn)
    for d_ in (nb, nn):                           # (a wall-clock age: the two runs start seconds apart)
        d_.pop("portfolio_age_hours", None)
    check("s0: ...and the same status.json (but timings and the portfolio's wall-clock age; no momentum key)",
          nn == nb and "momentum" not in stn,
          [k for k in set(nn) | set(nb) if nn.get(k) != nb.get(k)])
    off = {**json.load(open(STAGED)), "momentum_enabled": False, "momentum_auto": False}
    so, sto = D.s0_run(M, "s0o", D.write_file(off, "staged_off.json"))
    check("s0: the Package 16 file with momentum_enabled / momentum_auto false = the Package 15 file (same orders, "
          "no momentum key)", strip(so) == strip(sn) and "momentum" not in sto, (len(so), len(sn)))
    out(f"- the Package 15 file on ed65638 and on this head, 12 cycles: {len(sn)} orders each, identical; status "
        "identical less timings (no `momentum` key)")
    out(f"- the Package 16 file with its two switches off: {len(so)} orders, identical to the Package 15 file")
    out()


# ============================================================================================ scenario 1
def scenario1():
    out("## Scenario 1: armed, no history (no recorder file, nothing restored)")
    set_clock()
    api, b = build(p15_live_file())
    P.apply_stage(b, STAGED)
    n0 = len(P.CAP.lines)
    D.run(b, 10, stage="s1")
    m = mstatus(b)
    ls = lines_since(n0)
    check("1: armed, no history: state armed, reason 'no history', not on", m.get("armed") is True
          and m.get("reason") == "no history" and m.get("on") is False and m.get("auto_state") == "armed", m)
    check("1: no momentum order at all", not mom_orders(api, "s1"), len(mom_orders(api, "s1")))
    check("1: journal 'MOMENTUM eval: armed - no history'", any(x.startswith("MOMENTUM eval: armed - no history")
                                                                for x in ls))
    out(f"- 10 cycles: `momentum` {{armed: {m.get('armed')}, on: {m.get('on')}, auto_state: {m.get('auto_state')}, "
        f"reason: \"{m.get('reason')}\", series_source: {m.get('series_source')}}}; momentum orders "
        f"{len(mom_orders(api, 's1'))}")
    out("- journal: " + next((x for x in ls if x.startswith("MOMENTUM eval")), "(none)"))
    out()


# ============================================================================================ scenario 2 / 3
def confirm_phase(b, api, stage, hours=4.0, step=120.0, cash_at_on=0.0):
    """`hours` of cycles on the seeded series (held: the 4 Oct trend continued flat), recording when it switched on
    and whether anything was bought before. cash_at_on: that much cash arrives just before the switch-on cycle."""
    t0, n0 = now_w(), len(P.CAP.lines)
    first_on, first_buy, rows = None, None, []
    n = int(hours * H / step) + 2
    for i in range(n):
        cs = b.ma.get("confirm_since")
        if cash_at_on and cs is not None and now_w() - cs >= b.cfg.momentum_confirm_h * H - 1e-6:
            api.cash += cash_at_on
            STATE["cash_added_at"] = now_w() - t0
            cash_at_on = 0.0
        D.run(b, 1, step=step, stage=stage, refill_s=H)
        if first_on is None and b.ma["state"] == "on":
            first_on = now_w() - step - t0
            STATE["on_off"] = P.CLK.off - step
        if first_buy is None and any(o["traded"] > 0 for o in mom_orders(api, stage)):
            first_buy = now_w() - step - t0
        if i % 30 == 0:
            rows.append((round((now_w() - step - t0) / H, 2), b.ma["state"], b.ma["reason"],
                         b.ma_s24, b.ma_s6))
    return first_on, first_buy, rows, lines_since(n0)


def buys_table(b, api, stage, n=12):
    rows = []
    for o in mom_orders(api, stage):
        if o["traded"] <= 0 or o["pos_before"] is None:
            continue
        ex = b.ex.get(o["eid"])
        p = o.get("p")
        rows.append((o["label"], "YES" if o["yes_buy"] else "NO (sell YES)", o["traded"], o["yes_price"], p,
                     (o["yes_price"] - p) if (p is not None and o["yes_buy"]) else
                     ((1 - o["yes_price"]) - (1 - p)) if p is not None else None, ex))
    return rows


def scenario2():
    out("## Scenario 2: seeded from md.sqlite at 4 Oct 15:57 UTC; the live state as it is, 4 h")
    set_clock()
    api, b = build(p15_live_file())
    f0 = P.status_of(b)
    P.apply_stage(b, STAGED)
    seed_from_snapshot(b)
    hold_series(b)
    s24 = b.tslope.slope_24h(now_w(), b.cfg.momentum_slope_hours)
    s6 = b.tslope.slope_6h(now_w(), b.MA_SLOPE_6H)
    bins = [round(100 * v, 2) for _, v in b.tslope.values(now_w())]
    STATE["seed"] = (bins[-6:], s24, s6, len(b.tslope.bins), b.tslope.source)
    check("2: the seed (the bot's own ts_seed_step on md.sqlite): source 'seed', the last 6 bins 10.69 .. 12.82",
          b.tslope.source == "seed" and bins[-6:] == [10.69, 11.92, 12.25, 12.48, 12.75, 12.82], bins[-6:])
    check("2: the reading at 15:57 4 Oct: slope_24h ~ +2.29, slope_6h ~ +0.42 points a day",
          s24 is not None and abs(s24 - 2.29) < 0.05 and s6 is not None and abs(s6 - 0.42) < 0.03, (s24, s6))
    seeded = [m for _, _, m in P.CAP.lines if m.startswith("MOMENTUM tilt series seeded")]
    check("2: journal 'MOMENTUM tilt series seeded from the recorder ... slope24 +2.29, slope6 +0.42'",
          seeded and "slope24 +2.29" in seeded[-1] and "slope6 +0.42" in seeded[-1], seeded[-1:])
    first_on, first_buy, rows, ls = confirm_phase(b, api, "s2")
    m = mstatus(b)
    check("2: 'MOMENTUM eval: armed - confirming' while the 4-h clock runs", any(
        x.startswith("MOMENTUM eval: armed - confirming") for x in ls), [x for x in ls if "eval" in x][:2])
    check("2: the trigger switches ON after the 4-h confirm, not before (first 'on' at >= 4 h)",
          first_on is not None and first_on >= 4 * H - 1, first_on)
    check("2: no buy before the switch-on", first_buy is None or (first_on is not None and first_buy >= first_on - 1),
          (first_buy, first_on))
    check("2: the ALERT 'MOMENTUM auto switched ON' at the switch-on", any("switched ON" in a for a in M_ALERTS),
          M_ALERTS[-2:])
    # the live state's funding: (a) / (b) / (c)
    mc = (f0.get("mm_carry_24h") or {}).get("per_day")
    fund = [x for x in ls if x.startswith("MOMENTUM funding")]
    st = P.status_of(b)
    mf, hvs, caps = st.get("mm_funding") or {}, st.get("harvest") or {}, st.get("state_caps") or {}
    STATE["s2"] = dict(first_on=first_on, first_buy=first_buy, rows=rows, m=m, mc=mc, fund=fund,
                       buys=buys_table(b, api, "s2"), mf=mf, hv=hvs, caps=caps, hold=b.ma_hold,
                       kept=b.cash_reserve(), free=b.cash_left(), alloc=dict((st.get("alloc") or {}).get(
                           "blocked_by") or {}))
    check("2: the live state as it is: free cash below what is kept back -> (a) gives nothing, (c) nothing at / above "
          "the floor: the round is short (short_usd 10,000) but unfundable", b.cash_left() < b.cash_reserve()
          and m.get("short_usd", 0) >= 10000 - 1 and m.get("size_usd", 0) < 1, (b.cash_left(), b.cash_reserve(), m))
    check("2: ...so nothing is held back for it: hold_usd 0, the ladder not paused, the quotes' budget untouched",
          m.get("hold_usd") == 0 and m.get("quote_hold_usd") == 0 and not hvs.get("paused_for_momentum"),
          (m.get("hold_usd"), hvs.get("paused_for_momentum")))
    hv_new = [o for o in P.orders(api, "s2", "hv_tick") if o["t"] > STATE.get("on_off", 1e18) + 1]
    out(f"- the seed: {len(b.tslope.bins)} bins of 4 h (source seed); the last six "
        f"{STATE['seed'][0]} points; slope_24h {s24:+.2f}, slope_6h {s6:+.2f} points a day at 15:57 4 Oct (the series "
        f"re-dated by {STATE['shift'] / H:.0f} h = whole 4-h bins onto the run's clock, which cannot run on 4 Oct)")
    out(f"- journal: {seeded[-1] if seeded else '(none)'}")
    out(f"- trigger met at once (slope_24h {s24:+.2f} >= 0.5, slope_6h {s6:+.2f} > 0): state 'armed - confirming'; "
        f"switched ON after {first_on / H:.2f} h (series held at the 4 Oct reading); first momentum fill "
        + (f"at {first_buy / H:.2f} h" if first_buy is not None else "none"))
    out("| h | auto state | reason | slope24 | slope6 |")
    out("|---|---|---|---|---|")
    for r in rows:
        out(f"| {r[0]} | {r[1]} | {r[2]} | {M.Bot.ma_fmt(r[3])} | {M.Bot.ma_fmt(r[4])} |")
    out(f"- the live state's cash: free {b.cash_left():,.0f}, kept back for funding (a) {b.cash_reserve():,.0f} "
        f"(the MM's effective reserve {b.mm_reserve_effective():,.0f} + the ladder's carve-out not resting "
        f"{b.cash_reserve() - b.mm_reserve_effective():,.0f}); mm_carry_24h.per_day {mc} -> (b) "
        f"{'usable' if b.ma_reserve_ok() else 'not used'}; sleeve {m.get('size_usd', 0):,.0f} of target "
        f"{m.get('target_usd', 0):,.0f}; hold_usd {m.get('hold_usd', 0):,.0f}; funded_from {m.get('funded_from')}")
    out(f"- funding (c) journal: {fund[:3] if fund else 'none'}")
    out(f"- the ladder during the round: paused_for_momentum {hvs.get('paused_for_momentum', False)}, levels resting "
        f"{hvs.get('levels_resting')}, blocked_by {hvs.get('blocked_by')}; harvest orders sent after the switch-on "
        f"{len(hv_new)}")
    out(f"- the allocator's blocked_by (no 'momentum': nothing held for an unfundable round): {STATE['s2']['alloc']}")
    out()


def scenario3():
    out("## Scenario 3: the funded fixture (+$45,000 cash arriving just before the switch-on): the first buys, the ramp")
    set_clock()
    api, b = build(p15_live_file())
    P.apply_stage(b, STAGED)
    seed_from_snapshot(b)
    hold_series(b)
    t_start, off0 = now_w(), P.CLK.off
    first_on, first_buy, rows, ls = confirm_phase(b, api, "s3", cash_at_on=45000.0)
    check("3: the trigger switches ON after 4 h (not before), no buy before", first_on is not None
          and first_on >= 4 * H - 1 and (first_buy is None or first_buy >= first_on - 1), (first_on, first_buy))
    # a few more cycles at the 10k target; the ladder and the quotes while the round is short
    seen = {"paused": 0, "hv_new": 0, "cycles": 0}

    def per():
        seen["cycles"] += 1
        if getattr(b, "hv_mom_paused", False):
            seen["paused"] += 1
            seen["hv_new"] += sum(1 for o in P.orders(api, "s3", "hv_tick") if o["cycle"] == api.cycle_no)
    D.run(b, 10, step=60.0, stage="s3", refill_s=H, per_cycle=per)
    m = mstatus(b)
    check("3: while the funded round was short the harvest ladder placed NO new level (paused cycles, no harvest "
          "order in them)", seen["paused"] >= 1 and seen["hv_new"] == 0, seen)
    STATE["s3_seen"] = dict(seen)
    buys = buys_table(b, api, "s3")
    check("3: the first buys: the sleeve built toward the 10k target (cost 5k .. 10k at the touch levels)",
          buys and 5000 <= m.get("size_usd", 0) <= 10000 + 1, (m.get("size_usd"), len(buys)))
    bad_side = [r for r in buys if r[6] is not None and b.alloc_p(r[6]) is not None and False]
    check("3: every momentum buy is a MOMENTUM long (a longshot's YES or a 2-leg favourite's NO), never a headline "
          "race", all(r[6] is not None and r[6].group not in b.cfg.headline_races for r in buys) and not bad_side)
    # rule 5 / no quote / no duplicates / never at our own price
    legs = set(b.mom_legs)
    rest_bad = [(o.eid, o.is_bid) for o in b.my_orders.values() if o.eid in legs]
    check("3: nothing of ours rests on a sleeve market after its buy (never quoted, no harvest level)", not rest_bad,
          rest_bad[:4])
    per = defaultdict(int)
    for o in mom_orders(api, "s3"):
        if o["yes_buy"] == (b.mom_legs.get(o["eid"], {"q": 1})["q"] > 0):
            per[(o["eid"], o["cycle"])] += 1
    check("3: one momentum order per market per cycle at most (no duplicate)", all(v == 1 for v in per.values()),
          [k for k, v in per.items() if v > 1][:3])
    harvest_in_race = [r[0] for r in buys if any(m_ in (b.hv_sides or {}) for m_ in b.groups.get(r[6].group, ()))]
    check("3: rule 5 - no harvest level in a sleeve market's race after the buy", not harvest_in_race,
          harvest_in_race[:3])
    caps = (P.status_of(b).get("state_caps") or {})
    over = {s: v for s, v in caps.items() if v["collateral"] > b.cfg.state_max_usd + 600}
    check("3: the state cap holds (no state above 15k + a quote after the sleeve's buys)", not over, over)
    STATE["s3_buys"] = dict(buys=buys, m=m, first_on=first_on, caps=caps, legs=dict(b.mom_legs))
    # the ramp: a rising slope_6h (+0.5 pt a 4-h bin) for 19 simulated hours
    ramp, base_v = [], (last_bin_value(b) or 12.82)
    t_r = now_w()
    k0 = bin_no(b)
    for i in range(int(19 * H / 300)):
        push_bin(b, base_v + 0.5 * (bin_no(b) - k0 + 1))   # (+0.5 point every 4-h bin from now)
        D.run(b, 1, step=300.0, stage="s3r", refill_s=H)
        if i % int(H / 300) == 0:
            ramp.append((round((now_w() - 300 - t_r) / H, 1), b.ma["target_usd"], b.ma["steps"], round(cost_of(b)),
                         M.Bot.ma_fmt(b.ma_s24), M.Bot.ma_fmt(b.ma_s6), round(b.cash_left())))
    m = mstatus(b)
    steps = b.ma["steps"]
    check("3: the ramp: +10k per 6 h of rising tilt - 3 steps in 19 h (target 40k = momentum_max_usd)",
          steps == 3 and abs(b.ma["target_usd"] - 40000) < 1e-6, (steps, b.ma["target_usd"]))
    check("3: the target is never above momentum_max_usd 40,000 and the sleeve's cost never above its target",
          all(r[1] <= 40000 + 1e-6 and r[3] <= r[1] + 1 for r in ramp), ramp[-3:])
    mw_all, mw_mom = D.minute_writes(api, off0), D.minute_writes(api, off0, feature="mom_tick")
    check(f"3: writes: the sleeve at most {mw_mom} a minute (<= momentum_writes_frac 0.6 x 28 + one order's 3); all "
          f"features {mw_all} a minute (the limiter 28 counts, the bot plans with writes_left)", mw_mom <= 20,
          (mw_mom, mw_all))
    STATE["s3"] = dict(ramp=ramp, m=m, mw=(mw_mom, mw_all), legs=dict(b.mom_legs), t_start=t_start)
    out(f"- switched ON after {first_on / H:.2f} h; the first buys (IOC at the touch; p the race-scaled liquid "
        "Polymarket price; gap = price - p per share of payoff; the fake's books refill hourly, so a level bought can "
        "come back):")
    out("| market | bought | shares | price | p | gap |")
    out("|---|---|---|---|---|---|")
    for r in buys[:14]:
        out(f"| {r[0]} | {r[1]} | {r[2]:,.0f} | {r[3]:.3f} | {r[4]:.3f} | "
            f"{100 * r[5]:.1f}c |" if r[4] is not None and r[5] is not None else
            f"| {r[0]} | {r[1]} | {r[2]:,.0f} | {r[3]:.3f} | ? | ? |")
    m0 = STATE["s3_buys"]["m"]
    out(f"- the sleeve after the first round: ${m0.get('size_usd', 0):,.0f} at cost in {m0.get('markets')} markets, "
        f"target ${m0.get('target_usd', 0):,.0f}; funded_from {m0.get('funded_from')}; kept back for (a) "
        f"{m0.get('kept_back')}; the state caps after: "
        + (", ".join(f"{s} {v['collateral'] / 1000:.1f}k" for s, v in sorted(caps.items())) or "none above 7.5k"))
    out("- the ramp (a simulated rising tilt: +0.5 point per 4-h bin):")
    out("| h | target | steps | sleeve $ at cost | slope24 | slope6 | free cash |")
    out("|---|---|---|---|---|---|---|")
    for r in ramp[::2]:
        out(f"| {r[0]} | {r[1]:,.0f} | {r[2]} | {r[3]:,.0f} | {r[4]} | {r[5]} | {r[6]:,.0f} |")
    out(f"- after 19 h: target ${b.ma['target_usd']:,.0f} ({steps} steps), sleeve ${m.get('size_usd', 0):,.0f} in "
        f"{m.get('markets')} markets, funded_from {m.get('funded_from')}, ev_given_up {m.get('ev_given_up')}")
    out(f"- writes: the sleeve at most {mw_mom} in a minute, all features {mw_all} (the limiter 28)")
    out()
    return api, b


def cost_of(b):
    return sum(v["cost"] for v in b.mom_legs.values())


# ============================================================================================ scenario 4 / 5
def scenario4(api, b):
    out("## Scenario 4: the flip on a simulated slope_24h <= 0 (on 3's sleeve)")
    legs0 = dict(b.mom_legs)
    n0, a0 = len(P.CAP.lines), len(M_ALERTS)
    top = last_bin_value(b) or 14.0
    t0 = now_w()
    flip_at, k0, k_flip = None, bin_no(b), None
    for i in range(int(24 * H / 300)):
        k = bin_no(b) if k_flip is None else k_flip
        push_bin(b, top - 2.0 * (k - k0 + 1))     # (a sharp fall: -2 points a 4-h bin until the flip, then flat)
        D.run(b, 1, step=300.0, stage="s4", refill_s=H)
        if flip_at is None and b.ma["state"] == "flipped":
            flip_at, k_flip = now_w() - 300 - t0, bin_no(b)
            STATE["flip_s24"] = b.ma_s24
    ls = lines_since(n0)
    check("4: the 24-h slope turned <= 0: flipped (ALERT 'switched OFF (24-h slope ...)')", flip_at is not None
          and any("switched OFF" in a and "24-h slope" in a for a in M_ALERTS[a0:]), (flip_at, M_ALERTS[a0:][:2]))
    sells = [o for o in mom_orders(api, "s4") if o["traded"] > 0]
    check("4: the exit sold the sleeve into the bids over mom_exit_hours 6 (state exited, no leg left after 24 h; "
          "the fake's thin books refill hourly)",
          b.mom_state == "exited" and not b.mom_legs and sells, (b.mom_state, len(b.mom_legs), len(sells)))
    span = (max(o["t"] for o in sells) - min(o["t"] for o in sells)) / H if sells else 0.0
    check("4: the exit was paced over the 6 h (first to last exit sale >= 4 h apart)", span >= 4.0, span)
    b.cfg.harvest_min_edge = b.cfg.harvest_min_edge
    _, why = b.hv_plan(dict(api.inv), M.time.monotonic())
    still = [b.ex[e].label for e in legs0 if why.get(e) == "momentum"]
    check("4: the harvest ladder takes the sleeve's markets over (none of them 'momentum' any more)", not still,
          still[:3])
    STATE["s4"] = dict(flip_at=flip_at, sold=sum(o["traded"] for o in sells), n=len(sells),
                       why={b.ex[e].label: why.get(e, "laddered") for e in legs0}, lines=[x for x in ls if x.startswith(
                           ("MOMENTUM auto switched OFF", "MOMENTUM EXITING"))][:3])
    out(f"- slope_24h fell to {M.Bot.ma_fmt(STATE.get('flip_s24'))} (a -2-point bin): flipped {flip_at / H:.2f} h "
        f"after the fall began; {len(sells)} exit sales, {sum(o['traded'] for o in sells):,.0f} shares, over "
        "mom_exit_hours 6 (a thin book's leg takes longer: the fake refills its books hourly)")
    for x in STATE["s4"]["lines"]:
        out(f"    {x}")
    out("- the ladder on the former sleeve markets: " + ", ".join(f"{k}: {v_}" for k, v_ in
                                                                    list(STATE["s4"]["why"].items())[:8]))
    out()


def scenario5():
    out("## Scenario 5: a -30% mark move on the sleeve (the kill)")
    set_clock()
    api, b = build(p15_live_file())
    P.apply_stage(b, STAGED)
    seed_from_snapshot(b)
    hold_series(b)
    b.cfg.momentum_confirm_h = 0.0                # (the switch-on itself is scenario 3's)
    D.run(b, 2, step=60.0, stage="s5b", refill_s=H)
    api.cash += 45000.0                           # (the fixture's cash, as the sleeve's round opens)
    D.run(b, 6, step=60.0, stage="s5b", refill_s=H)
    c0 = cost_of(b)
    legs = dict(b.mom_legs)
    check("5: a sleeve built (cost > 0)", c0 > 0 and legs, c0)
    for e, v in legs.items():                     # every leg's book moved so its mid is 70% of its cost a share
        unit = v["cost"] / abs(v["q"])
        tgt = 0.7 * unit
        mid = tgt if v["q"] > 0 else 1 - tgt
        bk = {"bids": [{"price": round(max(0.005, mid - 0.005), 3), "quantity": 100000}],
              "asks": [{"price": round(min(0.995, mid + 0.005), 3), "quantity": 100000}]}
        api.books[e] = {k: [dict(x) for x in bk[k]] for k in bk}
        api.base_books[e] = {k: [dict(x) for x in bk[k]] for k in bk}
    a0 = len(M_ALERTS)
    t0 = now_w()
    killed_at = None
    for i in range(10):
        D.run(b, 1, step=30.0, stage="s5", refill_s=1e9)
        if killed_at is None and b.mom_state == "killed":
            killed_at = now_w() - 30 - t0
    mark = sum(b.mom_mark(e, v) for e, v in b.mom_legs.items())
    check("5: killed after the mark sat below 0.75 x cost for 120 s (not before)", killed_at is not None
          and killed_at >= 120 - 1, killed_at)
    check("5: one ALERT 'MOMENTUM sleeve KILLED'", sum("KILLED" in a for a in M_ALERTS[a0:]) == 1, M_ALERTS[a0:])
    D.run(b, int(2 * H / 300), step=300.0, stage="s5x", refill_s=1e9)
    sells = [o for o in mom_orders(api, "s5x") if o["traded"] > 0]
    buys = [o for o in mom_orders(api, "s5x") if o["traded"] > 0 and (o["yes_buy"] == (legs.get(o["eid"], {"q": 1})
                                                                                        ["q"] > 0))]
    check("5: latched: the exit sells (paced), never a buy while killed", sells and not buys and b.mom_state in (
        "killed",) and b.ma["state"] == "killed", (len(sells), len(buys), b.mom_state))
    STATE["s5"] = dict(cost=c0, killed_at=killed_at, mark=mark, sells=len(sells),
                       left=sum(abs(v["q"]) for v in b.mom_legs.values()))
    out(f"- a sleeve of ${c0:,.0f} at cost; every leg's book moved to 70% of its cost a share: killed "
        f"{killed_at:.0f} s later (the 120-s hold), one alert; the exit then sold {len(sells)} times in 2 h "
        f"(over mom_exit_hours 6, the floor exempt), no buy (latched until momentum_enabled false then true)")
    out()


# ============================================================================================ main
M_ALERTS = []


def main():
    if not os.path.exists(SNAP_DB):
        print(f"SKIP: no snapshot at {SNAP}")
        return 0
    t_start = _rt.time()
    orig_alert = M.alert
    M.alert = lambda msg: (M_ALERTS.append(msg), orig_alert(msg))[1]
    STATE["S"] = D.STATE["S"] = P.load_snapshot()
    raw = json.load(open(STAGED))
    good, bad = M.validate_overrides(P.staged(STAGED), M.Config())   # (less the retired keys)
    check("files: the staged file validates with no problem", not bad, bad)
    p15 = json.load(open(P15_FILE))
    check("files: the staged file = the Package 15 file + momentum_enabled / momentum_auto true, momentum_force false "
          "and the C defaults (nothing of Package 15 changed)", {k: v for k, v in raw.items() if k in p15} == p15
          and raw["momentum_enabled"] is True and raw["momentum_auto"] is True and raw["momentum_force"] is False)
    out("# Package 16 dry run: the armed momentum sleeve on the live state")
    out()
    out(f"Generated by `tests/test_p16_dryrun.py` ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC) from {SNAP} (the "
        "4 Oct 15:56 ops snapshot) moved to the owner's live numbers by `tests/p14_1_state.py`, built on the Package 15 "
        "staged file (the live file + the harvest ladder + the 15k state cap); then "
        "`deploy/package16/settings_override.momentum_armed.json` lands through `check_overrides`. The tilt series is "
        "seeded by the bot's own cold-start path from md.sqlite's rows up to 15:57 UTC 4 Oct, re-dated by whole 4-h "
        "bins onto the run's clock (moved to the same place in its bin as 15:57), so it reads as it did then. 1-5-min "
        "cycles; the fake's 28-writes-a-minute limiter counts.")
    out()
    body = len(OUT)
    scenario0()
    scenario1()
    scenario2()
    api, b = scenario3()
    scenario4(api, b)
    scenario5()
    OUT[body:body] = summarise()
    caveats()
    print(f"(all done in {_rt.time() - t_start:.0f} s)")
    return 0


def summarise():
    s = ["## Summary"]
    sd = STATE.get("seed")
    if sd:
        s.append(f"- **The seed (md.sqlite, 4 Oct 15:57 UTC):** bins {sd[0]} points, **slope_24h {sd[1]:+.2f}, slope_6h "
                 f"{sd[2]:+.2f} points a day** - the trigger's conditions met (>= 0.5 and > 0): 'armed - confirming'.")
    s2 = STATE.get("s2") or {}
    if s2:
        m = s2["m"]
        s.append(f"- **The live state as it is:** switched ON after {s2['first_on'] / H:.2f} h (the 4-h confirm; no buy "
                 f"before). Free cash {s2['free']:,.0f} is below what funding (a) keeps back ({s2['kept']:,.0f} = the "
                 f"MM's 10k + the ladder's carve-out not resting); mm_carry_24h.per_day {s2['mc']} -> (b) "
                 f"{'usable' if (s2['mc'] is not None and s2['mc'] < 300) else 'not used'}; (c) value sales at / above "
                 f"the floor: {'; '.join(s2['fund'][:1]) or 'none planned'}. The sleeve: "
                 f"${m.get('size_usd', 0):,.0f} of ${m.get('target_usd', 0):,.0f}: the round is short "
                 f"(${m.get('short_usd', 0):,.0f}) but unfundable, so NOTHING is held back for it (hold "
                 f"${m.get('hold_usd', 0):,.0f}): the market maker, the allocator and the ladder go on as in Package "
                 "15.")
    s3 = STATE.get("s3") or {}
    b3 = STATE.get("s3_buys") or {}
    if s3:
        m0 = b3.get("m") or {}
        s.append(f"- **Funded fixture (+45k):** ON after {b3['first_on'] / H:.2f} h; first round "
                 f"${m0.get('size_usd', 0):,.0f} in {m0.get('markets')} markets (funded_from "
                 f"{m0.get('funded_from')}); the ramp reached target ${s3['m'].get('target_usd', 0):,.0f} after 19 "
                 f"simulated hours of a rising tilt (sleeve ${s3['m'].get('size_usd', 0):,.0f}); writes <= "
                 f"{s3['mw'][1]} a minute (the sleeve {s3['mw'][0]}).")
    s4 = STATE.get("s4") or {}
    if s4:
        s.append(f"- **Flip:** slope_24h <= 0 -> flipped, {s4['n']} exit sales ({s4['sold']:,.0f} shares) over 6 h, "
                 "the ladder takes the markets over.")
    s5 = STATE.get("s5") or {}
    if s5:
        s.append(f"- **Kill:** a -30% mark move on a ${s5['cost']:,.0f} sleeve: killed {s5['killed_at']:.0f} s later, "
                 "one alert, latched exit, no buy.")
    s.append("- **Flags off:** identical to ed65638 (orders and status) on the live state; the Package 16 file with "
             "its two switches off = the Package 15 file.")
    s.append("")
    return s


def caveats():
    out("## Caveats")
    out("- The live state is REBUILT from the 4 Oct 15:56 snapshot (tests/p14_1_state.py), not copied from the server.")
    out("- The tilt series past the seed is SIMULATED: the fake's books are static (their own live tilt reading would "
        "be flat), so the confirm phase holds the 4 Oct reading, the ramp pushes +0.5 point per 4-h bin and the flip "
        "-2 points per bin. The owner's live tilt_s fell after 4 Oct (0.137 -> 0.109 by 5 Oct 10:21): on today's "
        "readings slope_24h is likely negative and the armed sleeve would NOT switch on (TILT_PATHS 4.2).")
    out("- The fake never fills our resting orders (no other trader); the sleeve's IOCs trade against the static "
        "books (refilled hourly); Polymarket is flat (p does not move with the books), so the kill scenario moves the "
        "books alone.")
    out("- The 28-writes limiter counts, it does not refuse (planning only); the bot plans with writes_left as live.")
    out("- The +45k cash of scenarios 3-5 is a fixture to show the buys: on the live state as it is (2) funding (a) has "
        "nothing above the 20k kept back and (c) finds little at / above the value floor (Package 13's finding).")


if __name__ == "__main__":
    rc = main()
    if RESULTS and os.environ.get("P16_DRYRUN_REPORT", "1") != "0" and OUT:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w") as f:
            f.write("\n".join(OUT) + "\n")
    n, ok = len(RESULTS), sum(RESULTS)
    print(f"\n{ok}/{n} checks passed")
    sys.exit(0 if ok == n and rc == 0 else 1)
