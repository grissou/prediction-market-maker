"""
Live-start simulator (SIM_START=live): the strategy simulator's crowded-book world, started from the real book of
positions (tests/live_start.json, built by tests/live_start_extract.py from status.json + snapshots + fills.csv),
quoting through the REAL Bot code paths instead of compute_quote alone:

  Bot.decide             (race-netted eff inventory, Kelly/headline limits, age skew from real lot ages, capital
                          ceiling via Bot.update_capital_ceiling, fast unload window, reduce_join_best, fl bias)
  Bot.refill_cooling     (sides withheld after a same-side run, fed by Bot.note_refills)
  Bot.note_unloads       (fills routed as fill dicts with order_meta, as log_fills does)
  Bot.arb_plan           (pair unwind, sell- and buy-side arbitrage) on the simulated other-trader books, executed as
                          immediate-or-cancel takes at those prices; cooldowns as Bot.take_arbitrage
  Bot.update_lots / total_worst_case / effective_inventory

World: every race's legs share one Polymarket path (Dem = S - Rep, S = the race's real reference sum; independent
outsider races have S = 0.90-0.97), each leg's tournament consensus = Polymarket + its REAL starting gap (book mid -
reference, capped at 5c) + AR(1) noise (0.95c, 13.5 min), rivals and humans as in strategy_sim. The markets: the races
of the N biggest positions by capital (both legs), plus 3 synthetic outsider races. The rest of the account is a
fixed block of positions sized so that capital in positions starts at `start_cap` (default 0.90) of account value.

Run:  python tests/live_sim.py SEEDS HOURS REGIME '{overrides}' ['{variant}' ...]   (paired seeds)
      overrides: Config fields, plus "_n" (biggest positions, default 40), "_start_cap" (0.90), "_house"
      ("0945" = House legs +10,834 / -9,396 as at 09:45, default; "0814" = status.json), "_outsiders" (3).
      World knobs (Package 5, PLAN_POLY_BIAS.md 3/3.1): "_rival_anchor" a (0 = today: rivals and informed takers price
      from Polymarket; 1 = from the tournament consensus without its noise: Polymarket + bias + tilt term),
      "_world_tilt" s0 and "_world_tilt_growth" g per hour: consensus = Polymarket - s_t (Polymarket - 0.5) + bias +
      noise, s_t = s0 + g x hours. The real starting gap already holds the live tilt, so the per-market bias becomes
      the residual (bias + s0 (p0 - 0.5)): the start matches the real book either way, and s0 only names the part of
      the real gap that is the tilt (the part that grows). "_world_tilt_add" a (default 0) ADDS a x (0.5 - Polymarket)
      on top, with no residual compensation: a world more tilted than the real book (the ref_tilt_max test). All 0 =
      as before.
      Liquidation-marked fields: pnl_mid, pnl_liq, mk15_mid, exit_ratio, hold_med (see LiveSim.metrics).
      Env LIVE_SIM_CACHE=file: per-(seed, hours, regime, config, LIVE_SIM_TAG) results cached, never run twice.
      "_bg_wc_growth" G and "_bg_wc_decay" D (per hour, default 0): the background worst case CLIMBS by G while the bot
      is not reduce-only (the other ~160 markets keep adding, as live: +280-570/min for the whole account between
      episodes) and FALLS by D while it is (they reduce too); calibrated so the base bot is reduce-only ~80% of cycles
      as live (SIM_NOTES Round 6b). Metric bg_wc_end.
      "_bg_wc" W (default 0 = as before): the rest of the account's sum-of-maxima worst case, so the live reduce-only
      backstop binds as live (mm_bot cycle step 6: worst > (worst_case_backstop_frac - hysteresis) x equity ->
      Bot.decide(global_reduce=True)); live 2 Oct evening: total 77.7-81.6k, backstop 0.8 x ~101k. Metrics wc_start,
      ro_frac (share of cycles in reduce-only).
      Env SIM_EARLY_STOP=1: a variant stops after 4 seeds if d pnl_liq < -3 SE.
"""
import json
import logging
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_sim as S                                   # noqa: E402
from strategy_sim import KINDS, M, Order, Sim, fair_value, floor_tick, ceil_tick, TICK, lot_age  # noqa: E402,F401

logging.disable(logging.CRITICAL)
START = os.path.join(HERE, "live_start.json")
ACCOUNT = 101_498.0
SHARE = float(os.environ.get("SIM_LIVE_SHARE", "1.0"))   # share of writes_per_minute these 74 markets get (the biggest: all 30)
RHO = float(os.environ.get("SIM_RACE_RHO", "0.95"))   # anti-correlation of a race's two legs' tournament noise
BIAS_SUM = float(os.environ.get("SIM_BIAS_SUM", "0.25"))   # share of the legs' summed starting gap kept
LIFT = float(os.environ.get("SIM_LIFT", "0.003"))   # rivals price each race leg this much over Polymarket
FEATURES = {   # Package 2 switches: name -> {setting: value when OFF}
    "age_skew": {"skew_age_enabled": False},
    "ceiling": {"capital_in_positions_max_frac": 0.0},
    "race_net": {"limits_use_race_net": False},
    "refill": {"refill_cooldown_enabled": False},
    "unload": {"fast_unload_enabled": False},
    "join": {"reduce_join_best": False},
    "unwind": {"pair_unwind_enabled": False},
    "arb": {"arb_enabled": False, "arb_two_sided": False},
}


def all_off():
    out = {}
    for v in FEATURES.values():
        out.update(v)
    return out


class LiveSim(Sim):
    def __init__(self, seed, hours, regime, cfg, n=40, start_cap=0.90, house="0945", outsiders=3,
                 rival_anchor=0.0, world_tilt=0.0, world_tilt_growth=0.0, bg_wc=0.0, world_tilt_add=0.0,
                 bg_wc_growth=0.0, bg_wc_decay=0.0):
        super().__init__(seed, hours, regime, cfg, share=SHARE)
        self.bg_wc0, self.bg_g, self.bg_d, self.t_prev_bg = float(bg_wc), float(bg_wc_growth), float(bg_wc_decay), 0
        self.tilt_add = float(world_tilt_add)
        self.bg_wc, self.global_reduce, self.ro_cycles, self.n_cycles, self.wc_start = float(bg_wc), False, 0, 0, None
        self.anchor, self.tilt0, self.tilt_g = float(rival_anchor), float(world_tilt), float(world_tilt_growth)
        data = json.load(open(START))
        rows = [r for r in data["markets"] if r["ref"] is not None]
        if house == "0945":
            for r in rows:
                if r["label"] == "Dem U.S. House":
                    r["pos"], r["lots"] = 10834.0, [[10834.0, -16.6 * 3600 + 5400]]
                if r["label"] == "Rep U.S. House":
                    r["pos"] = -9396.0
                    r["lots"] = [[-9396.0 + 8246.0 if abs(r["pos"]) > 8246 else 0, -3600.0]] + r["lots"]
                    r["lots"] = [l for l in r["lots"] if l[0]]
        cap = lambda r: abs(r["pos"]) * (self.px0(r) if r["pos"] > 0 else 1 - self.px0(r))
        races = []
        for r in sorted(rows, key=cap, reverse=True)[:n]:
            if r["race"] not in races:
                races.append(r["race"])
        by_race = {}
        for r in rows:
            if r["race"] in races and r["party"]:
                by_race.setdefault(r["race"], []).append(r)
        self.mkts, self.paths, self.races = [], [], {}
        for race, legs in by_race.items():
            ms = [self.make_mkt(r) for r in legs]
            self.races[race] = ms
        for i in range(outsiders):                       # an independent takes 3-10%: Dem + Rep sum to 0.90-0.97
            s = self.nrng.uniform(0.90, 0.97)
            pr = self.nrng.uniform(0.35, 0.55)
            legs = [dict(label=f"{p} Outsider-{i}", party=party, race=f"Outsider-{i}", pos=0.0, lots=[],
                         ref=x, fv=x, best_bid=x - 0.005, best_ask=x + 0.005, fills16h=30)
                    for p, party, x in (("Rep", "Republican", pr), ("Dem", "Democratic", s - pr))]
            self.races[f"Outsider-{i}"] = [self.make_mkt(r) for r in legs]
        self.mkts = [m for ms in self.races.values() for m in ms]
        for ms in self.races.values():                   # shrink the part of the starting gaps both legs share
            if len(ms) == 2:
                common = (ms[0].bias + ms[1].bias) / 2 * (1 - BIAS_SUM)
                for m in ms:
                    m.bias -= common
                k = math.sqrt(1 - RHO * RHO)              # rivals' fair-value errors: consistent across the legs
                for r0, r1 in zip(ms[0].rivals, ms[1].rivals):
                    r1.offset = -RHO * r0.offset + k * r1.offset
                for m in ms:                              # tournament prices of a race add up to a bit over 1
                    for rv in m.rivals:
                        rv.offset += LIFT
        if self.tilt0:                                    # the starting gap holds the tilt: keep the residual
            for m in self.mkts:
                m.bias += self.tilt0 * (m.p0 - 0.5)
        self.inv0 = {id(m): m.inv for m in self.mkts}
        self.cash0 = {id(m): m.cash for m in self.mkts}
        self.lots0 = {id(m): sorted(([float(x), float(u)] for x, u in m.state["lots"]), key=lambda l: l[1])
                      for m in self.mkts}
        self.takes, self.mid0, self.liq0 = [], {}, {}
        sim_cap = sum(cap(m.row) for m in self.mkts)
        self.bg_cap = max(0.0, start_cap * ACCOUNT - sim_cap)
        self.cap0_sim = sim_cap
        sim_pd = sum(M.PARTY_SIGN.get(m.row["party"], 0) * m.inv for m in self.mkts)
        self.bg_party = (data.get("party_delta") or 0.0) - sim_pd
        self.bot = self.make_bot()
        self.strategy = bot_strategy
        self.unwinds = self.arbs = self.unwind_sh = self.arb_sh = self.outsider_arb_sh = 0
        self.tx_bind = self.tx_quoted = 0                 # Package 5 T2.4: binding / quoted market-cycles
        self.arb_pnl = 0.0
        self.curve, self.wc_peak, self.nf = [], 0.0, 0
        self.bidsum = {"senate": [], "asksum_lo": 0, "race_min": 0}

    # ---------------------------------------------------------------- start state
    @staticmethod
    def px0(r):
        return r["ref"] if r.get("ref") is not None else (r["fv"] or 0.5)

    def make_mkt(self, r):
        headline = r["race"] in ("U.S. House", "U.S. Senate")
        kind = "headline" if headline else "busy" if r["fills16h"] >= 20 else "quiet"
        m = self.new_market(kind, KINDS[kind])
        self.n_made = getattr(self, "n_made", 0) + 1
        m.row, m.eid = r, f"s{self.n_made}"
        m.p0 = self.px0(r)
        mid = ((r["best_bid"] + r["best_ask"]) / 2 if r.get("best_bid") is not None and r.get("best_ask") is not None
               else m.p0)
        m.bias = max(-0.05, min(0.05, mid - m.p0))      # the real starting tournament - Polymarket gap
        m.inv = float(r["pos"])
        m.cash = -m.inv * m.p0                          # P&L starts at 0, marked at Polymarket
        m.state["lots"] = [[float(x), float(t)] for x, t in r["lots"]]
        return m

    def make_bot(self):
        from fakes import make_bot
        _, bot = make_bot(live=False)
        keep = {k: getattr(bot.cfg, k) for k in ("fills_csv", "status_file", "order_notes_file", "kill_file",
                                                 "position_lots_file", "overrides_file", "market_edge_file",
                                                 "handover_file")}
        for k, v in keep.items():
            setattr(self.cfg, k, v)
        self.cfg.record_file, self.cfg.ref_map_file = "", ""
        bot.cfg = self.cfg
        bot.ex, bot.groups, bot.size_plan, bot.lots = {}, {}, {}, {}
        bot.size_bank, bot.last_equity = ACCOUNT, None
        bot.lots_seeded = True
        self.epoch0 = time.time()
        for race, ms in self.races.items():
            bot.groups[race] = [m.eid for m in ms]
            for m in ms:
                ex = M.Ex(m.eid, m.eid, race, m.row["label"], race, m.row["party"], None)
                bot.ex[m.eid] = ex
                bank = ACCOUNT
                bot.size_plan[m.eid] = (self.cfg.headline_size_frac if m.headline else self.cfg.size_max_frac
                                        if m.kind == "busy" else self.cfg.size_min_frac) * bank
                if m.state["lots"]:
                    bot.lots[m.eid] = [[x, self.epoch0 + t] for x, t in m.state["lots"]]
        self.t_now = 0
        M.time = _Clock(self)                     # the bot's time.time() = simulated wall clock (turnover, ages)
        bot.cur_refs, bot.cur_liquid = {}, set()
        bot.mark_sd = {m.eid: m.row["mark_sd"] for m in self.mkts if m.row.get("mark_sd")}
        bot.turnover = M.TurnoverTracker(start=self.epoch0)
        pts = [self.epoch0 - 6 * 3600 + 300 * k for k in range(72)]
        for m in self.mkts:                       # our real fills of the last 6 h (no tape history)
            for t, q in m.row.get("recent_fills", []):
                bot.turnover.add(m.eid, self.epoch0 + t, q)
        bot.turnover._cover(pts)
        self.np, self.lad_gate_hits, self.lad_cycles, self.lad_writes_ok = {}, 0, 0, True
        # Ladder gate counters (lg_*), per market-cycle where level 0 quotes (see bot_strategy), each gate counted
        # on its own (they overlap): calls; wgate = the write gate (ladder_min_writes) shuts; of the rest, exclusive:
        # mkt = not a ladder market (ladder_markets), early = pulled / no fair value / ref-only, geo = no level fits
        # behind level 0 and the book, caps = the position limits leave no level, cash = lad_cash_left drops every
        # level, ok = some level wanted (ok_w: and the write gate open); cash_lv = levels lad_cash_left drops, cap1 =
        # levels the per-order cash cap cuts, clip = cash_clip cut the ladder, sh = ladder share-cycles wanted
        # after every gate (resting ones re-wanted each cycle); cash_avg = lad_cash_left at a cycle's start, average;
        # free_avg = free cash as Bot.ladder_setup counts it (equity - positions - level-0 locks), average; lv_cash
        # = the cash the cheapest wanted level needed where lad_cash_left dropped every level, average.
        self.lg = dict(calls=0, wgate=0, mkt=0, early=0, geo=0, caps=0, cap1=0, cash=0, cash_lv=0, ok=0, ok_w=0,
                       clip=0, sh=0, lv_cash=0.0, cash_avg=0.0, free_avg=0.0)
        if self.cfg.ladder_enabled:
            self.ladder = {"real": True}      # strategy_sim: mm_bot.plan_exchange ordering of ladder writes
        bot.age_hours = lambda ex, now=None, b=bot: M.Bot.age_hours(b, ex, self.epoch0 + self.t_now)
        bot.hours_to_close = lambda ex: 800.0
        return bot

    # ---------------------------------------------------------------- world
    def run(self):
        """Race-consistent Polymarket paths, then strategy_sim's loop."""
        self.paths_by = {}
        for race, ms in self.races.items():
            p_first, c_first = self.make_paths_leg(ms[0])
            self.paths_by[id(ms[0])] = (p_first, c_first)
            if len(ms) == 2:
                s = ms[0].p0 + ms[1].p0
                p2 = [min(0.99, max(0.01, s - x)) for x in p_first]
                x1 = [c - p - ms[0].bias for p, c in zip(p_first, c_first)]
                self.paths_by[id(ms[1])] = (p2, self.consensus(ms[1], p2, x1))
        self.rp = {}
        for m in self.mkts:
            p, c = self.paths_by[id(m)]
            if self.tilt0 or self.tilt_g or self.tilt_add:
                for t in range(len(c)):
                    c[t] = min(0.99, max(0.01, c[t] + self.tilt_term(t, p[t])))
            if self.anchor:
                self.rp[id(m)] = [x + self.anchor * (m.bias + self.tilt_term(t, x)) for t, x in enumerate(p)]
        self.cpath = {id(m): self.paths_by[id(m)][1] for m in self.mkts}
        return super().run()

    def tilt_term(self, t, p):
        """The world's favourite-longshot tilt at second t: consensus - Polymarket from the tilt alone."""
        return -(self.tilt0 + self.tilt_add + self.tilt_g * t / 3600.0) * (p - 0.5)

    def rival_step(self, m, rv, t, p):
        return super().rival_step(m, rv, t, self.rp.get(id(m), p))

    def informed_px(self, m, t, p):
        rp = self.rp.get(id(m))
        return rp[t] if rp is not None else p[t]

    def make_paths(self, m):
        return self.paths_by[id(m)]

    def make_paths_leg(self, m):
        return Sim.make_paths(self, m)

    def consensus(self, m, p, x1):
        """Second leg: its own AR(1) noise, correlated RHO with minus the first leg's (traders keep a race's prices
        adding up to about 1), so bid-sums cross 1 about as often as on day one (see SIM_NOTES Round 3)."""
        r = self.rng
        x, phi = 0.0, 0.5 ** (1 / S.CAL["tr_hl"])
        sd_x = S.CAL["tr_sd"] * math.sqrt(1 - phi * phi)
        out = []
        k = math.sqrt(1 - RHO * RHO)
        for v, y in zip(p, x1):
            x = phi * x + r.gauss(0, sd_x)
            out.append(min(0.99, max(0.01, v + m.bias - RHO * y + k * x)))
        return out

    # ---------------------------------------------------------------- us: one cycle = bot bookkeeping + arb + quotes
    def our_cycle(self, t, paths):
        self.t_now = t
        if t == 0:                                # start marks: other traders' mid, and the liquidation price
            for m, p, c in paths:
                self.mid0[id(m)] = self.mid_px(m, c[t])
                self.liq0[id(m)] = self.liq_px(m, m.inv, c[t])
        bot, cfg = self.bot, self.cfg
        inv = {m.eid: m.inv for m in self.mkts}
        pnow = {m.eid: p[t] for m, p, c in paths}
        self.route_fills(inv, t)
        for m, p, c in paths:                     # every trade in the market -> the turnover tape
            pr = m.state.get("prints", [])
            for tt, px, q in pr[self.np.get(m.eid, 0):]:
                bot.turnover.add(m.eid, self.epoch0 + tt, q, tape=True)
            self.np[m.eid] = len(pr)
        bot.refresh_turnover(t)
        bot.cur_refs = {m.eid: m.ref_seen for m in self.mkts if m.ref_seen is not None}
        bot.cur_liquid = set(bot.cur_refs)
        bot.update_mark_frag(inv, cfg)
        bot.update_lots(inv, self.epoch0 + t)
        fvs = {m.eid: m.state.get("fv") or pnow[m.eid] for m in self.mkts}
        capital = self.bg_cap + sum(abs(q) * (pnow[e] if q > 0 else 1 - pnow[e]) for e, q in inv.items())
        self.cap_frac = capital / ACCOUNT
        # Cash: account value now (start + P&L at Polymarket) - positions - what our resting orders lock. An order
        # only locks cash for the part that ADDS to a position (selling YES we hold, or buying back a short, frees it).
        equity = ACCOUNT + sum(m.cash + m.inv * pnow[m.eid] for m in self.mkts)
        locked = sum(order_lock(o, m.inv) for m in self.mkts for o in m.orders if o.owner == "us")
        self.free = equity - capital - locked
        self.free_min = min(getattr(self, "free_min", 1e18), self.free)
        bot.update_capital_ceiling(self.cap_frac, cfg)
        self.party_delta = self.bg_party + sum(M.PARTY_SIGN.get(bot.ex[e].party, 0) * q for e, q in inv.items())
        self.eff = bot.effective_inventory(inv)
        if cfg.tilt_exposure_max_frac > 0:        # Package 5 T2.4 feed (Bot.update_tilt's exposure, sim markets, legs 2)
            bot.tilt_exposure = sum(m.inv * (m.ref_seen - 0.5) for m in self.mkts if m.inv and m.ref_seen is not None)
        if cfg.ladder_enabled:                    # Bot.ladder_setup: what the ladder may lock this cycle
            other = sum(order_lock(o, m.inv) for m in self.mkts for o in m.orders if o.owner == "us" and o.level == 0)
            eq = equity
            bot.lad_liquid, bot.lad_party_delta = set(bot.cur_refs), self.party_delta
            bot.lad_cash_left = max(0.0, min(eq - capital - other - cfg.ladder_min_cash_frac * eq,
                                             cfg.quote_capital_frac * eq - other))
            self.lad_writes_ok = (self.wcap - sum(c for _, c in self.wlog if t - _ < 60)
                                  >= cfg.ladder_min_writes * SHARE)
            self.lad_cycles += 1
            self.lg["cash_avg"] += bot.lad_cash_left
            self.lg["free_avg"] += eq - capital - other
            self.lad_gate_hits += not self.lad_writes_ok
        if t % 10 == 0:
            self.arbitrage(t, inv, fvs)
        if cfg.pair_unwind_passive and t % 5 == 0:      # Package 5 T2.5 (isolated mirror, see pair_passive)
            self.pair_passive(t, inv, fvs)
        if cfg.hold_target_hours > 0 and t % 5 == 0:    # Package 5 C (isolated mirror, see hold_take)
            self.hold_take(t, inv)
        if self.bg_wc:                            # the live backstop (mm_bot cycle step 6, sum-of-maxima part only)
            if self.bg_g or self.bg_d:            # the rest of the account adds between episodes, reduces inside them
                dt_h = (t - self.t_prev_bg) / 3600.0
                self.bg_wc = max(0.0, self.bg_wc + (-self.bg_d if self.global_reduce else self.bg_g) * dt_h)
                self.t_prev_bg = t
            worst = self.bg_wc + bot.total_worst_case(inv, fvs)
            if self.wc_start is None:
                self.wc_start = worst
            hyst = min(cfg.reduce_only_hysteresis, cfg.max_worst_case_frac / 2) if self.global_reduce else 0.0
            self.global_reduce = worst > (cfg.worst_case_backstop_frac - hyst) * equity
            bot.global_reduce = self.global_reduce
            bot.backstop_adding_factor = M.backstop_soft_factor(worst, equity, cfg)   # Package 6 candidate
            self.n_cycles += 1
            self.ro_cycles += self.global_reduce
        if t % 600 == 0:
            wc = bot.total_worst_case(inv, fvs)
            self.wc_peak = max(self.wc_peak, wc)
            self.curve.append((t, round(self.cap_frac, 3), round(wc)))
        if t % 60 == 0:
            self.sample_sums()
        super().our_cycle(t, paths)

    def route_fills(self, inv, t):
        """Our new quote fills -> fill dicts + order_meta -> Bot.note_refills / note_unloads (as log_fills feeds them)."""
        new = []
        for k in range(self.nf, len(self.fills)):
            ft, m, side, q, price, fv, lvl, taker = self.fills[k]
            oid = f"o{k}"
            self.bot.order_meta[oid] = {"our_side": "bid" if side > 0 else "ask", "price": price, "fv": fv,
                                        "t": time.time(), "eid": m.eid}
            new.append({"orderId": oid, "exchangeId": m.eid, "quantity": q, "filledAt": M.iso(M.utcnow())})
        self.nf = len(self.fills)
        if new:
            new.reverse()                         # log_fills hands them newest first
            for m in self.mkts:
                self.bot.ex[m.eid].inv = m.inv
            self.bot.note_refills(new, inv, t)
            self.bot.note_unloads(new, inv, t)

    def arbitrage(self, t, inv, fvs):
        """Bot.take_arbitrage on the simulated books: Bot.arb_plan, then immediate-or-cancel at those prices."""
        bot = self.bot
        for race, ms in self.races.items():
            if len(ms) < 2 or t < bot.arb_cooldown.get(race, 0):
                continue
            for m in ms:
                bot.ex[m.eid].book = self.book_dict(m)
            members = [m.eid for m in ms]
            plan = bot.arb_plan(members, inv, fvs, False)
            if plan is None:
                continue
            kind, action, levels, qty = plan
            cd = cfg_cd = (self.cfg.pair_unwind_cooldown_seconds if kind == "unwind" else self.cfg.arb_cooldown_seconds)
            if qty < 1:
                bot.arb_cooldown[race] = t + cfg_cd
                continue
            if not (kind == "unwind"):                    # arbitrage adds positions: it needs the cash
                lock = sum((p if action == "buy" else 1 - p) for p, _ in levels.values())
                qty = int(min(qty, max(0.0, self.free) / max(lock, 0.01)))
                if qty < 1:
                    continue
            for m in ms:                                  # pull our quotes in the race first
                m.orders = [o for o in m.orders if o.owner != "us"]
            got = [self.take(m, t, action == "buy", qty, levels[m.eid][0]) for m in ms]
            done = min(got)
            if not any(got):
                cd = 4 * cfg_cd
            bot.arb_cooldown[race] = t + cd
            per_set = (sum(p for p, _ in levels.values()) - 1) * (1 if action == "sell" else -1)
            if kind == "unwind":
                self.unwinds, self.unwind_sh = self.unwinds + 1, self.unwind_sh + done
            else:
                self.arbs, self.arb_sh = self.arbs + 1, self.arb_sh + done
                if race.startswith("Outsider"):
                    self.outsider_arb_sh += done
            self.arb_pnl += per_set * done
            inv.update({m.eid: m.inv for m in ms})

    # ---- Package 5 T2.5: passive pair unwind mirror (isolated; only runs with cfg.pair_unwind_passive) ----
    def pair_passive(self, t, inv, fvs):
        """Bot.pair_passive_step on the simulated books every 5 s: a fill of the resting leg -> the other leg taken at
        once (self.take, our quotes there pulled first). The resting ask goes through Bot.decide, wrapped once with
        Bot.pair_passive_quote as cycle_body applies it. Counts: self.pp_take_sh (second-leg shares taken)."""
        bot = self.bot
        if not getattr(self, "pp_wrapped", False):
            self.pp_wrapped, self.pp_take_sh, plain = True, 0.0, bot.decide
            bot.decide = lambda ex, *a, **k: (bot.pair_passive_quote(ex, plain(ex, *a, **k)) if bot.pp
                                              else plain(ex, *a, **k))
        by = {}
        for ms in self.races.values():
            if len(ms) == 2:
                for m in ms:
                    bot.ex[m.eid].book, by[m.eid] = self.book_dict(m), m

        def execute(eid, buy, qty, price):
            if not self.take_writes_ok(t):
                return 0.0                        # refused for writes: nothing sold (the owed leg waits)
            m = by[eid]
            m.orders = [o for o in m.orders if o.owner != "us"]
            got = self.take(m, t, buy, qty, price)
            inv[eid] = m.inv
            self.pp_take_sh += got
            return got
        bot.pair_passive_step(inv, fvs, t, execute=execute)
    # ---- end Package 5 T2.5 mirror ----

    # ---- Package 5 C: hold target take-half mirror (isolated; only runs with cfg.hold_target_hours > 0) ----
    def hold_take(self, t, inv):
        """Bot.take_aged on the simulated books every 5 s: the same plan (Bot.hold_take_plan: age >= 2 x
        hold_target_hours, best other price, 1c floor, per-order / per-minute / hourly caps, first hour closed),
        executed immediate-or-cancel by self.take with our quotes there pulled first. The quote half needs no
        mirror (bot_strategy calls Bot.decide). Counts: self.hold_take_sh (shares taken)."""
        bot = self.bot
        if not hasattr(self, "hold_take_sh"):
            self.hold_take_sh = 0.0
        by, bfvs = {}, {}
        for m in self.mkts:
            by[m.eid] = m
            bot.ex[m.eid].book = self.book_dict(m)
            bfvs[m.eid] = fair_value(bot.ex[m.eid].book, self.cfg)       # as our_step feeds decide (book_fv)

        def execute(eid, buy, qty, price):
            if not self.take_writes_ok(t):
                return None                       # refused for writes: not counted (as live)
            m = by[eid]
            m.orders = [o for o in m.orders if o.owner != "us"]
            got = self.take(m, t, buy, qty, price)
            inv[eid] = m.inv
            self.hold_take_sh += got
            return got
        return bot.take_aged(inv, bfvs, {}, t, execute=execute)
    # ---- end Package 5 C mirror ----

    def take_writes_ok(self, t, n=3):
        """Package 5 mirrors (T2.5 second leg, C takes): a take costs n writes as live (pull our quotes, the IOC
        order, the leftover cancel); charge them to the write log, or refuse when the budget has no room (as
        Bot.take_aged / pair_passive_take wait for writes_ready). The arbitrage path keeps its Round 3 accounting."""
        spare = self.wcap - sum(c for _, c in self.wlog if t - _ < 60)
        if spare < n:
            self.take_refused = getattr(self, "take_refused", 0) + 1
            return False
        self.wlog.append((t, n))
        self.writes += n
        return True

    def take(self, m, t, is_buy, qty, limit):
        """Our immediate-or-cancel order: walks other traders' orders up to `limit`; returns shares done."""
        book = sorted((o for o in m.orders if o.is_bid != is_buy and o.owner != "us"
                       and (o.price <= limit + 1e-9 if is_buy else o.price >= limit - 1e-9)),
                      key=lambda o: ((o.price if is_buy else -o.price), o.t))
        done = 0.0
        for o in book:
            if done >= qty:
                break
            q = min(o.qty, qty - done)
            o.qty -= q
            done += q
            if o.owner.startswith("r"):
                self.rival(m, o.owner).inv += q if o.is_bid else -q
            m.state.setdefault("prints", []).append((t, o.price, q))
            m.inv += q if is_buy else -q
            m.cash -= (q if is_buy else -q) * o.price
            S.add_lot(m.state.setdefault("lots", []), q if is_buy else -q, t)
            self.takes.append((t, m, 1 if is_buy else -1, q, o.price))
        m.orders = [o for o in m.orders if o.qty > 1e-9]
        return done

    def sample_sums(self):
        for race, ms in self.races.items():
            if len(ms) != 2:
                continue
            bids = [self.best(self.others(m, "us"), True) for m in ms]
            asks = [self.best(self.others(m, "us"), False) for m in ms]
            if race == "U.S. Senate" and None not in bids:
                self.bidsum["senate"].append(sum(bids))
            if None not in bids and not race.startswith("Outsider"):
                self.bidsum.setdefault("all", []).append(sum(bids))
            if None not in asks and race.startswith("Outsider"):
                self.bidsum["out_n"] = self.bidsum.get("out_n", 0) + 1
                self.bidsum["out_lo"] = self.bidsum.get("out_lo", 0) + (sum(asks) <= 0.985)
            if None not in asks and not race.startswith("Outsider"):
                self.bidsum["race_min"] += 1
                self.bidsum["asksum_lo"] += sum(asks) < 0.98

    # ---------------------------------------------------------------- metrics
    def mid_px(self, m, c):
        """Other traders' mid; one side missing -> the consensus value."""
        bb, ba = self.best(self.others(m, "us"), True), self.best(self.others(m, "us"), False)
        return (bb + ba) / 2 if bb is not None and ba is not None else c

    def liq_px(self, m, inv, c):
        """What a position sells for now: longs at the best other bid, shorts at the best other ask; no quote on
        that side -> the consensus value 2c worse."""
        if inv > 0:
            bb = self.best(self.others(m, "us"), True)
            return bb if bb is not None else c - 0.02
        if inv < 0:
            ba = self.best(self.others(m, "us"), False)
            return ba if ba is not None else c + 0.02
        return 0.0

    def replay(self):
        """Our fills and takes in time order from the starting book: (shares that reduced |inventory|, shares that
        added, closed lots as (hours held, shares)) with FIFO lots and the starting lots' real ages."""
        ev = sorted([(f[0], f[1], f[2], f[3]) for f in self.fills] + [(x[0], x[1], x[2], x[3]) for x in self.takes],
                    key=lambda e: e[0])
        state = {}
        red = add = 0.0
        closed = []
        for t, m, side, q in ev:
            if id(m) not in state:
                inv0 = self.inv0[id(m)]
                lots = [l[:] for l in self.lots0[id(m)] if l[0] * inv0 > 0]
                tot = sum(abs(l[0]) for l in lots)
                while lots and tot > abs(inv0) + 1e-9:             # more lots than position: trim the newest
                    cut = min(tot - abs(inv0), abs(lots[-1][0]))
                    lots[-1][0] -= math.copysign(cut, lots[-1][0])
                    tot -= cut
                    if abs(lots[-1][0]) < 1e-9:
                        lots.pop()
                if abs(inv0) - tot > 1e-9:
                    lots.append([math.copysign(abs(inv0) - tot, inv0), 0.0])
                state[id(m)] = [inv0, lots]
            st = state[id(m)]
            x = side * q
            if st[0] * x < 0:
                r = min(abs(x), abs(st[0]))
                red += r
                left = r
                while left > 1e-9 and st[1]:
                    k = min(left, abs(st[1][0][0]))
                    closed.append(((t - st[1][0][1]) / 3600.0, k))
                    st[1][0][0] -= math.copysign(k, st[1][0][0])
                    left -= k
                    if abs(st[1][0][0]) < 1e-9:
                        st[1].pop(0)
                if abs(x) - r > 1e-9:
                    add += abs(x) - r
                    st[1].append([math.copysign(abs(x) - r, x), t])
            else:
                add += abs(x)
                st[1].append([x, t])
            st[0] += x
        return red, add, closed

    def liq_fields(self, T):
        """Package 5 yardstick: P&L from the start marked at other traders' mid and at liquidation (start and end
        both), markout against the consensus 15 min later, exit ratio and median hold of closed lots."""
        cT = {id(m): self.cpath[id(m)][T] for m in self.mkts}
        pnl_mid = sum(m.cash + m.inv * self.mid_px(m, cT[id(m)]) - self.cash0[id(m)]
                      - self.inv0[id(m)] * self.mid0.get(id(m), m.p0) for m in self.mkts)
        pnl_liq = sum(m.cash + m.inv * self.liq_px(m, m.inv, cT[id(m)]) - self.cash0[id(m)]
                      - self.inv0[id(m)] * self.liq0.get(id(m), m.p0) for m in self.mkts)
        fsh = sum(f[3] for f in self.fills)
        mk_mid = sum(f[2] * f[3] * (self.cpath[id(f[1])][min(T, int(f[0]) + 900)] - f[4]) for f in self.fills)
        red, add, closed = self.replay()
        hold_med, acc, tot_c = 0.0, 0.0, sum(w for _, w in closed)
        for a, w in sorted(closed):
            acc += w
            if acc >= tot_c / 2:
                hold_med = a
                break
        return dict(pnl_mid=round(pnl_mid), pnl_liq=round(pnl_liq), mk15_mid=round(100 * mk_mid / fsh, 2) if fsh else 0.0,
                   exit_ratio=round(red / max(add, 1.0), 3), hold_med=round(hold_med, 2))

    def metrics(self):
        out = super().metrics()
        T = self.T
        pend = {id(m): p[T] for m, p, c in self.paths}
        fvs = {m.eid: pend[id(m)] for m in self.mkts}
        inv = {m.eid: m.inv for m in self.mkts}
        cap_end = sum(abs(m.inv) * (pend[id(m)] if m.inv > 0 else 1 - pend[id(m)]) for m in self.mkts)
        held = sorted((S.lot_age([x], T), abs(x[0])) for m in self.mkts for x in m.state.get("lots", []))
        tot, acc, med = sum(w for _, w in held), 0.0, 0.0
        for a, w in held:
            acc += w
            if acc >= tot / 2:
                med = a
                break
        sen = self.bidsum["senate"]
        out.update(self.liq_fields(T))
        out.update(cap_start=round((self.bg_cap + self.cap0_sim) / ACCOUNT, 3),
                   cap_end=round((self.bg_cap + cap_end) / ACCOUNT, 3),
                   cap_peak=round(max(c for _, c, _ in self.curve), 3) if self.curve else 0.0,
                   freed=round(self.cap0_sim - cap_end), age_end=round(med, 2),
                   unwinds=self.unwinds, unwind_sh=round(self.unwind_sh), arbs=self.arbs, arb_sh=round(self.arb_sh),
                   outsider_arb_sh=round(self.outsider_arb_sh), arb_pnl=round(self.arb_pnl),
                   wc_peak=round(self.wc_peak), wc_end=round(self.bot.total_worst_case(inv, fvs)),
                   sen_bidsum_ge1=round(sum(1 for x in sen if x >= 1.0) / len(sen), 3) if sen else 0.0,
                   sen_bidsum_max=round(max(sen), 3) if sen else 0.0,
                   bidsum_ge1=round(sum(1 for x in self.bidsum.get("all", []) if x >= 1.0) / max(1, len(self.bidsum.get("all", []))), 3),
                   bidsum_ge1005=round(sum(1 for x in self.bidsum.get("all", []) if x >= 1.005) / max(1, len(self.bidsum.get("all", []))), 3),
                   bidsum_ge103=round(sum(1 for x in self.bidsum.get("all", []) if x >= 1.03) / max(1, len(self.bidsum.get("all", []))), 4),
                   outsider_asksum_le0985=round(self.bidsum.get("out_lo", 0) / max(1, self.bidsum.get("out_n", 0)), 3),
                   asksum_lt098=round(self.bidsum["asksum_lo"] / max(1, self.bidsum["race_min"]), 3),
                   gross_sh=round(sum(abs(m.inv) for m in self.mkts)), n_mkts=len(self.mkts),
                   free_min=round(getattr(self, "free_min", 0)), clipped=getattr(self, "clipped", 0),
                   writes_pm=round(self.writes / (T / 60), 2), deferred_h=round(self.deferred / (T / 3600)),
                   lad_gate=round(self.lad_gate_hits / max(1, self.lad_cycles), 3),
                   **{f"lg_{k}": (round(v / max(1, self.lad_cycles)) if k.endswith("_avg")
                                         else round(v / max(1, self.lg["cash"])) if k == "lv_cash" else round(v))
                      for k, v in self.lg.items()},
                   dead=sum(1 for e in self.bot.ex.values() if e.turnover_dead),
                   tx_bind_frac=round(self.tx_bind / max(1, self.tx_quoted), 3),   # Package 5 T2.4
                   hold_take_sh=round(getattr(self, "hold_take_sh", 0.0)),
                   ro_frac=round(self.ro_cycles / max(1, self.n_cycles), 3), bg_wc_end=round(self.bg_wc),
                   wc_start=round(self.wc_start) if self.wc_start is not None else 0,
                   tilt_s_end=round(getattr(getattr(self, "tilt", None), "s", 0.0), 4),
                   take_refused=getattr(self, "take_refused", 0))
        return out


def bot_strategy(sim, m, t, fv, bfv, ref, book):
    """Bot.decide for this market, then the refill cooldown as Bot.plan_change applies it."""
    if fv is None:
        return []
    bot = sim.bot
    ex = bot.ex[m.eid]
    ex.book = book
    if sim.cfg.reduce_from_book and m.cooldown_until >= 0:   # A's pause reads ex.ref_jump_at, which only mm_bot's
        # reference_jump_guard sets: feed it the sim's Polymarket jump (see_ref sets cooldown_until = jump + cooldown)
        ex.ref_jump_at = max(ex.ref_jump_at, m.cooldown_until - sim.cfg.ref_jump_cooldown_seconds)
    inv = {x.eid: x.inv for x in sim.mkts}
    q = bot.decide(ex, fv, inv, sim.eff, sim.global_reduce, sim.party_delta, t, ref=ref, book_fv=bfv, ref_liquid=True)
    if sim.cfg.tilt_exposure_max_frac > 0:         # Package 5 T2.4: market-cycles where the tilt limit binds
        sim.tx_quoted += 1
        sim.tx_bind += any(bot.tilt_blocks(ex, ref))
    want = S.quote_to_want(q)
    if sim.cfg.ladder_enabled:                     # R3: mm_bot's ladder_targets (anchor, pulls, caps, cash)
        lg, quoted = sim.lg, q.bid is not None or q.ask is not None
        lg["calls"] += quoted
        seen = []                                  # ladder_targets' first ladder_levels call (before the cash)
        real = M.ladder_levels

        def spy(*a, **k):
            r = real(*a, **k)
            if not seen:
                big = {True: lambda px: 1e12, False: lambda px: 1e12}
                seen.append((dict(r[0]), real(*a[:7], big)[0],    # (a copy: ladder_targets then drops levels)
                             real(*a[:8])[0] if len(a) > 8 and a[8] is not None else dict(r[0])))
            return r
        M.ladder_levels = spy                      # (as Bot.plan_exchange: ladder_targets runs every cycle - anchor,
        try:                                       #  cash - and the write gate then only stops new writes)
            lw, _, _ = bot.ladder_targets(ex, q, fv, t)
        finally:
            M.ladder_levels = real
        if quoted:
            lg["wgate"] += not sim.lad_writes_ok
            if bot.ladder_market(ex) is None:
                lg["mkt"] += 1
            elif not seen:
                lg["early"] += 1                   # pulled after a Polymarket jump, no fair value, ref-only
            else:
                pre, geo, uncapped = seen[0]
                lg["geo"] += not geo               # every level at/inside level 0, crossing the book or at the edge
                lg["caps"] += bool(geo) and not uncapped   # the position limits leave no level
                lg["cap1"] += sum(1 for kk in pre if pre[kk][1] < uncapped.get(kk, (0, 0))[1])
                lg["cash"] += bool(pre) and not lw
                if pre and not lw:                 # the cheapest level it wanted: the cash it would need
                    lg["lv_cash"] += min(sz * (px if kk[0] else 1 - px) for kk, (px, sz) in pre.items())
                lg["cash_lv"] += len(pre) - len(lw)
                lg["ok"] += bool(lw)
                lg["ok_w"] += bool(lw) and sim.lad_writes_ok
        if sim.lad_writes_ok:
            want += [(k[0], px, sz, k[1], None) for k, (px, sz) in sorted(lw.items(), key=lambda kv: kv[0][1])]
        else:                                      # write gate: what rests stays, nothing new
            want += [(o.is_bid, o.price, o.qty, o.level, None) for o in m.orders if o.owner == "us" and o.level > 0]
    n_lad = sum(w[2] for w in want if w[3] > 0)
    want = cash_clip(sim, m, want)
    if sim.cfg.ladder_enabled:
        sim.lg["sh"] += sum(w[2] for w in want if w[3] > 0)
        sim.lg["clip"] += n_lad > sum(w[2] for w in want if w[3] > 0)
    out = []
    for w in want:
        if bot.refill_cooling(ex, w[0], t):        # withheld: what rests there stays (if any), nothing new
            cur = [o for o in m.orders if o.owner == "us" and o.is_bid == w[0] and o.level == 0]
            out += [(o.is_bid, o.price, o.qty, 0, None) for o in cur[:1]]
        else:
            out.append(w)
    return out


class _Clock:
    """Stands in for mm_bot's `time` module: time() is the simulated wall clock, everything else is real."""
    def __init__(self, sim):
        self._sim = sim

    def time(self):
        return self._sim.epoch0 + self._sim.t_now

    def __getattr__(self, k):
        return getattr(time, k)


def order_lock(o, inv):
    """Cash an order of ours locks: only its part that adds to the position."""
    if o.is_bid:
        return o.price * max(0.0, o.qty - max(0.0, -inv))
    return (1 - o.price) * max(0.0, o.qty - max(0.0, inv))


def cash_clip(sim, m, want):
    """The exchange takes an order only if the cash it locks is there: clip each adding side to the free cash
    (the order it replaces gives its lock back). Reducing shares need none."""
    out = []
    for w in want:
        is_bid, price, qty = w[0], w[1], w[2]
        lock_ps = price if is_bid else 1 - price
        reduce = max(0.0, -m.inv) if is_bid else max(0.0, m.inv)
        back = sum(order_lock(o, m.inv) for o in m.orders if o.owner == "us" and o.is_bid == is_bid and o.level == w[3])
        room = max(0.0, sim.free + back)
        allowed = reduce + room / max(lock_ps, 0.005)
        if qty > allowed:
            qty = int(allowed)
            sim.clipped = getattr(sim, "clipped", 0) + 1
        if qty >= 1:
            sim.free -= lock_ps * max(0.0, qty - reduce) - back
            out.append((is_bid, price, qty) + tuple(w[3:]))
    return out


def _one(args):
    seed, hours, regime, ov = args
    ov = dict(ov)
    kw = dict(n=int(ov.pop("_n", 40)), start_cap=float(ov.pop("_start_cap", 0.90)), house=str(ov.pop("_house", "0945")),
              outsiders=int(ov.pop("_outsiders", 3)), rival_anchor=float(ov.pop("_rival_anchor", 0.0)),
              world_tilt=float(ov.pop("_world_tilt", 0.0)), world_tilt_growth=float(ov.pop("_world_tilt_growth", 0.0)),
              bg_wc=float(ov.pop("_bg_wc", 0.0)), world_tilt_add=float(ov.pop("_world_tilt_add", 0.0)),
              bg_wc_growth=float(ov.pop("_bg_wc_growth", 0.0)), bg_wc_decay=float(ov.pop("_bg_wc_decay", 0.0)))
    sim = LiveSim(seed, hours, regime, S.make_cfg(ov), **kw)
    return sim.run()


def _key(j):
    s, hours, regime, ov = j
    return json.dumps([s, hours, regime, sorted(ov.items()), os.environ.get("LIVE_SIM_TAG", "")])


def run_many(seeds, hours, regime, ov, first=1):
    jobs = [(s, hours, regime, ov) for s in range(first, seeds + 1)]
    path, cache = os.environ.get("LIVE_SIM_CACHE"), {}
    if path and os.path.exists(path):
        for line in open(path):
            k, v = json.loads(line)
            cache[k] = v
    todo = [j for j in jobs if _key(j) not in cache]
    procs = int(os.environ.get("SIM_PROCS", "4"))

    def done(j, r):                                   # cache each seed as it finishes: a cut-off loses one seed
        cache[_key(j)] = r
        if path:
            with open(path, "a") as f:
                f.write(json.dumps([_key(j), r]) + "\n")
    if procs > 1 and len(todo) > 1:
        import multiprocessing
        with multiprocessing.Pool(min(procs, len(todo))) as pool:
            for j, r in zip(todo, pool.imap(_one, todo)):
                done(j, r)
    else:
        for j in todo:
            done(j, _one(j))
    return [cache[_key(j)] for j in jobs]


KEYS = ("pnl_liq", "pnl_mid", "pnl", "pnl_lag", "exit_ratio", "hold_med", "mk15_mid", "pick_cost", "wc_end", "writes_pm", "deferred_h", "lvl_sh", "lvl_pnl", "lad_gate", "lg_ok_w", "lg_sh", "dead", "free_min", "clipped", "cap_end", "cap_peak", "freed", "age_end", "unwind_sh", "arb_sh", "outsider_arb_sh",
        "arb_pnl", "wc_peak", "shares")


def stats(d):
    n = len(d)
    mu = sum(d) / n
    return mu, (sum((x - mu) ** 2 for x in d) / max(1, n - 1)) ** 0.5 / n ** 0.5


def main(argv):
    seeds, hours, regime = int(argv[0]), float(argv[1]), argv[2]
    base = json.loads(argv[3]) if len(argv) > 3 else {}
    variants = [json.loads(v) for v in argv[4:]]
    rb = run_many(seeds, hours, regime, base)
    agg = {k: sum(r[k] for r in rb) / len(rb) for k in rb[0]}
    print(f"BASE {json.dumps(base)[:60]} | " + " ".join(f"{k} {agg[k]:.3g}" for k in
          KEYS + tuple(k for k in rb[0] if k.startswith("lg_") and k not in KEYS) + ("cap_start", "bidsum_ge1", "bidsum_ge1005", "bidsum_ge103", "sen_bidsum_ge1", "sen_bidsum_max", "asksum_lt098", "outsider_asksum_le0985", "n_mkts")), flush=True)
    for v in variants:
        early = os.environ.get("SIM_EARLY_STOP") == "1" and seeds > 4
        rv = run_many(min(seeds, 4) if early else seeds, hours, regime, {**base, **v})
        stop = ""
        if early:
            mu, se = stats([y["pnl_liq"] - x["pnl_liq"] for x, y in zip(rb, rv)])
            if se > 0 and mu < -3 * se:
                stop = " STOPPED_AT_4"
            else:
                rv += run_many(seeds, hours, regime, {**base, **v}, first=5)
        parts = []
        for k in KEYS:
            mu, se = stats([y[k] - x[k] for x, y in zip(rb, rv)])
            parts.append(f"d{k} {mu:+.3g}+-{se:.2g}")
        print(f"{json.dumps(v)[:60]:<60}{stop} | " + " ".join(parts), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
