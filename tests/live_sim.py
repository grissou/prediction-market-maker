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
    def __init__(self, seed, hours, regime, cfg, n=40, start_cap=0.90, house="0945", outsiders=3):
        super().__init__(seed, hours, regime, cfg)
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
        sim_cap = sum(cap(m.row) for m in self.mkts)
        self.bg_cap = max(0.0, start_cap * ACCOUNT - sim_cap)
        self.cap0_sim = sim_cap
        sim_pd = sum(M.PARTY_SIGN.get(m.row["party"], 0) * m.inv for m in self.mkts)
        self.bg_party = (data.get("party_delta") or 0.0) - sim_pd
        self.bot = self.make_bot()
        self.strategy = bot_strategy
        self.unwinds = self.arbs = self.unwind_sh = self.arb_sh = self.outsider_arb_sh = 0
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
        return super().run()

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
        bot, cfg = self.bot, self.cfg
        inv = {m.eid: m.inv for m in self.mkts}
        pnow = {m.eid: p[t] for m, p, c in paths}
        self.route_fills(inv, t)
        bot.update_lots(inv, self.epoch0 + t)
        fvs = {m.eid: m.state.get("fv") or pnow[m.eid] for m in self.mkts}
        capital = self.bg_cap + sum(abs(q) * (pnow[e] if q > 0 else 1 - pnow[e]) for e, q in inv.items())
        self.cap_frac = capital / ACCOUNT
        bot.update_capital_ceiling(self.cap_frac, cfg)
        self.party_delta = self.bg_party + sum(M.PARTY_SIGN.get(bot.ex[e].party, 0) * q for e, q in inv.items())
        self.eff = bot.effective_inventory(inv)
        if t % 10 == 0:
            self.arbitrage(t, inv, fvs)
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
                   gross_sh=round(sum(abs(m.inv) for m in self.mkts)), n_mkts=len(self.mkts))
        return out


def bot_strategy(sim, m, t, fv, bfv, ref, book):
    """Bot.decide for this market, then the refill cooldown as Bot.plan_change applies it."""
    if fv is None:
        return []
    bot = sim.bot
    ex = bot.ex[m.eid]
    ex.book = book
    inv = {x.eid: x.inv for x in sim.mkts}
    q = bot.decide(ex, fv, inv, sim.eff, False, sim.party_delta, t, ref=ref, book_fv=bfv, ref_liquid=True)
    want = S.quote_to_want(q)
    out = []
    for w in want:
        if bot.refill_cooling(ex, w[0], t):        # withheld: what rests there stays (if any), nothing new
            cur = [o for o in m.orders if o.owner == "us" and o.is_bid == w[0] and o.level == 0]
            out += [(o.is_bid, o.price, o.qty, 0, None) for o in cur[:1]]
        else:
            out.append(w)
    return out


def _one(args):
    seed, hours, regime, ov = args
    ov = dict(ov)
    kw = dict(n=int(ov.pop("_n", 40)), start_cap=float(ov.pop("_start_cap", 0.90)), house=str(ov.pop("_house", "0945")),
              outsiders=int(ov.pop("_outsiders", 3)))
    sim = LiveSim(seed, hours, regime, S.make_cfg(ov), **kw)
    return sim.run()


def run_many(seeds, hours, regime, ov):
    jobs = [(s, hours, regime, ov) for s in range(1, seeds + 1)]
    procs = int(os.environ.get("SIM_PROCS", "4"))
    if procs > 1 and seeds > 1:
        import multiprocessing
        with multiprocessing.Pool(min(procs, seeds)) as pool:
            return pool.map(_one, jobs)
    return [_one(j) for j in jobs]


KEYS = ("pnl", "pnl_lag", "cap_end", "cap_peak", "freed", "age_end", "unwind_sh", "arb_sh", "outsider_arb_sh",
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
          KEYS + ("cap_start", "bidsum_ge1", "bidsum_ge1005", "bidsum_ge103", "sen_bidsum_ge1", "sen_bidsum_max", "asksum_lt098", "outsider_asksum_le0985", "n_mkts")), flush=True)
    for v in variants:
        rv = run_many(seeds, hours, regime, {**base, **v})
        parts = []
        for k in KEYS:
            mu, se = stats([y[k] - x[k] for x, y in zip(rb, rv)])
            parts.append(f"d{k} {mu:+.3g}+-{se:.2g}")
        print(f"{json.dumps(v)[:60]:<60} | " + " ".join(parts), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
