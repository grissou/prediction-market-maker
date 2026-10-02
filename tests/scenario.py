"""
Day-one scenarios: the whole bot (real Api.call with its throttle, retries and idempotency keys, real
threads) against a fake HTTP exchange, on a scaled clock (1 simulated second = SCALE real seconds).

  slow     The 2026-10-01 open: order WRITES take 14-30 s (longer than request_timeout, 15 s) but still
           land; a same-key retry while the first try is in flight gets 409 REQUEST_IN_FLIGHT; reads take
           0.5-1.5 s. 237 markets, books downloaded before the open, Polymarket moving.
  crowded  A crowded book: writes mostly 0.5-2 s (5% still slow), plus three rival market-making bots that
           re-quote one tick inside the best price down to their own floor around Polymarket, and pick
           off any of our quotes left stale after a Polymarket move.

Metrics (simulated seconds): time until 50% / 80% of markets have our quotes resting; share of markets
quoted at 1/2/5/10 min; longest cycle; worst time a headline (House/Senate) quote stayed mispriced after
Polymarket moved; write requests per minute; quotes picked off after Polymarket moves; fills the bot
could not attribute to its own quotes; duplicate quotes; whether the self-test killed the bot.

Run:  python tests/scenario.py [slow|crowded|both] [seeds] [minutes]      (prints one line per run)
      SCENARIO_SET="key=value,..." overrides bot settings (e.g. to compare old and new behaviour).
"""
import json
import os
import random
import sys
import tempfile
import threading
import time as real_time
from datetime import timedelta

import requests

from fakes import FakeApi, lvl, market   # noqa: F401  (sets up the path + env)
import mm_bot as M
from mm_bot import Api, Bot, Config, TICK, ceil_tick, floor_tick, rnd

SCALE = float(os.environ.get("SCENARIO_SCALE", "0.1"))   # real seconds per simulated second


class Clock:
    """Replaces the `time` module inside mm_bot (and mm_bot.utcnow): simulated time runs 1/SCALE times
    faster than real time, so a 10-minute session takes 60 s while threads and timeouts stay real."""
    def __init__(self):
        self.r0 = real_time.monotonic()
        self.base = M.datetime.now(M.timezone.utc)
        self.wall0 = real_time.time()
    def now(self): return (real_time.monotonic() - self.r0) / SCALE
    def monotonic(self): return 1000.0 + self.now()
    def time(self): return self.wall0 + self.now()
    def utcnow(self): return self.base + timedelta(seconds=self.now())
    def sleep(self, s): real_time.sleep(max(0.0, s) * SCALE)
    def gmtime(self, *a): return real_time.gmtime(*a)


class Resp:
    def __init__(self, status, data, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.content = json.dumps(data).encode()
        self.text = self.content.decode()
    def json(self): return json.loads(self.content)


class Feed:
    """Thread-safe stand-in for RealtimeFeed (the exchange thread pushes, the bot takes)."""
    def __init__(self):
        self.wake, self.events, self.lock = threading.Event(), 0, threading.Lock()
        self.dirty, self.account = set(), False
    def healthy(self): return True
    def stop(self): pass
    def push(self, dirty=(), account=False):
        with self.lock:
            self.dirty |= set(dirty); self.account |= account; self.events += 1
        self.wake.set()
    def take(self):
        with self.lock:
            self.wake.clear()
            d, a, self.dirty, self.account = self.dirty, self.account, set(), False
        return d, a, False, False


class Refs:
    """Polymarket stand-in: the scenario moves `true` every 5 s; the bot reads it like ReferencePrices."""
    def __init__(self, prices):
        self.prices, self.mapping, self.version, self.last_moves = dict(prices), dict(prices), 1, {}
        self.on_refresh = None
    def get(self): return dict(self.prices)
    def spreads(self): return {k: 0.01 for k in self.prices}
    def start(self): pass
    def stop(self): pass


class Exchange:
    """The fake server. Wraps FakeApi (the matching engine) behind HTTP routes with latency, a rate limit,
    idempotency keys (409 REQUEST_IN_FLIGHT while a same-key request runs), and timeouts that leave the
    write running. Every book change and fill is pushed to the feed, like Supabase Realtime."""
    def __init__(self, clock, rng, kind, eng, feed):
        self.clock, self.rng, self.kind, self.eng, self.feed = clock, rng, kind, eng, feed
        self.lock = threading.RLock()
        self.inflight, self.done = {}, {}
        self.window = []
        self.n = {"reads": 0, "writes": 0, "409": 0, "429": 0, "timeouts": 0}
        self.write_times = []

    def write_latency(self):
        if self.kind == "slow":     # most writes outlast the 15 s timeout; some (often cancels) don't
            return self.rng.uniform(15, 30) if self.rng.random() < 0.7 else self.rng.uniform(5, 15)
        return self.rng.uniform(16, 22) if self.rng.random() < 0.05 else self.rng.uniform(0.5, 2.0)

    def request(self, method, url, timeout=None, params=None, data=None):
        path = url.split("/api/v1", 1)[-1] if "/api/v1" in url else url[len(M.CFG.base_url):]
        body = json.loads(data) if data else None
        now = self.clock.now()
        with self.lock:
            self.window = [t for t in self.window if now - t < 60]
            if len(self.window) >= 100:
                self.n["429"] += 1
                return Resp(429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}}, {"Retry-After": "60"})
            self.window.append(now)
        write = method in ("POST", "DELETE")
        if not write:
            self.n["reads"] += 1
            self.clock.sleep(self.rng.uniform(0.5, 1.5))
            with self.lock:
                return Resp(200, self.read(path, params or {}))
        self.n["writes"] += 1
        self.write_times.append(now)
        key = (body or {}).get("idempotencyKey")
        with self.lock:
            if key and key in self.done:
                return Resp(200, self.done[key])
            if key and key in self.inflight:
                self.n["409"] += 1
                busy = True
            else:
                busy = False
                ev = threading.Event()
                self.inflight[key or object()] = ev
        if busy:
            self.clock.sleep(self.rng.uniform(0.3, 0.8))
            return Resp(409, {"error": {"code": "REQUEST_IN_FLIGHT", "message": "An identical request with "
                                        "this idempotency key is still in flight. Retry with the same key after 90 s"}})
        lat, box = self.write_latency(), {}

        def work():
            self.clock.sleep(lat)
            with self.lock:
                box["r"] = self.write(method, path, body)
                if key:
                    self.done[key] = box["r"]
                    self.inflight.pop(key, None)
            ev.set()
        threading.Thread(target=work, daemon=True).start()
        if not ev.wait(timeout=(timeout or 15) * SCALE):
            self.n["timeouts"] += 1
            raise requests.exceptions.ReadTimeout("simulated timeout")
        return Resp(200, box["r"])

    # --- routes -----------------------------------------------------------------------------------
    def read(self, path, p):
        e = self.eng
        if path.endswith("/markets"):
            return {"data": e.markets(), "pagination": {"hasMore": False}}
        if path.endswith("/portfolio/positions"):
            return e.positions()
        if path.endswith("/portfolio/pnl"):
            return e.pnl()
        if path.endswith("/portfolio/fills"):
            return {"data": list(reversed(e.fills[-400:])), "pagination": {"hasMore": False}}
        if path == "/orders":
            return {"data": e.open_orders(p.get("tournamentId"), p.get("exchangeId")), "pagination": {"hasMore": False}}
        if path.endswith("/orderbook"):
            b = e.full_book(path.split("/")[2])
            return {"bids": b["bids"][:10], "asks": b["asks"][:10]}
        if path == "/exchanges/prices":
            out = []
            for eid in p["ids"].split(","):
                b = e.full_book(eid)
                out.append({"exchangeId": eid, "bestBid": b["bids"][0]["price"] if b["bids"] else None,
                            "bestAsk": b["asks"][0]["price"] if b["asks"] else None})
            return {"data": out}
        if path.endswith("/leaderboard"):
            return {"myRank": 1, "total": 1}
        if "/tournaments/" in path:
            return e.tournament()
        return {}

    def write(self, method, path, body):
        e = self.eng
        if path == "/orders/batch":
            nfill = len(e.fills)
            res = e.place_batch(body["orders"])
            self.feed.push({o["exchangeId"] for o in body["orders"]}, account=len(e.fills) > nfill)
            return {"results": res}
        if path == "/orders/cancel-all":
            eid = body.get("exchangeId")
            e.cancel_all(body.get("tournamentId"), eid)
            self.feed.push([eid] if eid else [])
            return {}
        if method == "DELETE":
            o = e.orders.pop(int(path.rsplit("/", 1)[1]), None)
            if o:
                self.feed.push([o["exchangeId"]])
            return {}
        return {}


class Rival:
    """Another market-making bot: one bid and one ask per market, one tick inside the best other price,
    never closer than `floor` to its own fair value (Polymarket, as of its last look), re-checked every
    `delay` s. As a taker, `delay` s after a Polymarket move it trades against any of our quotes that are now
    at least 1c on the wrong side of Polymarket."""
    def __init__(self, name, delay, floor, size, quoting=True):
        self.name, self.delay, self.floor, self.size, self.quoting = name, delay, floor, size, quoting
        self.quotes = {}                  # eid -> (bid, ask)
        self.next = 0.0


class World:
    def __init__(self, kind, seed, minutes, overrides=None, selftest=True):
        self.kind, self.seed, self.minutes = kind, seed, minutes
        self.rng = random.Random(seed)
        self.clock = Clock()
        self.saved = (M.time, M.utcnow)
        M.time, M.utcnow = self.clock, self.clock.utcnow
        self.eng = FakeApi(True)
        self.feed = Feed()
        self.ex = Exchange(self.clock, random.Random(seed + 1), kind, self.eng, self.feed)
        self.build_markets()
        cfg = Config()
        d = tempfile.mkdtemp()
        cfg.fills_csv, cfg.status_file, cfg.order_notes_file, cfg.kill_file = (
            os.path.join(d, n) for n in ("fills.csv", "status.json", "notes.json", "kill.tripped"))
        cfg.record_file, cfg.ref_map_file, cfg.summary_every_hours, cfg.log_file = "", "", 0, ""
        cfg.realtime_enabled, cfg.selftest_enabled = False, selftest
        cfg.api_key, cfg.slug = "test-key", "test"
        for k, v in (overrides or {}).items():
            setattr(cfg, k, type(getattr(cfg, k))(v) if not isinstance(getattr(cfg, k), bool) else v in (True, "1", "true", "True"))
        self.cfg = cfg
        api = Api(cfg, True)
        api.s = self.ex                   # every HTTP request goes to the fake exchange
        self.api = api
        self.bot = Bot(api, cfg)
        self.bot.refs = self.refs
        self.refs.on_refresh = self.bot.on_reference_prices
        self.bot.start_feed = lambda: self.feed
        self.headline = {e for e, ex in self.bot.ex.items() if ex.group in cfg.headline_races}
        self.rivals = ([Rival("fast", 1.0, 0.01, 300), Rival("mid", 2.5, 0.01, 500), Rival("slow", 5.0, 0.015, 1000)]
                       if kind == "crowded" else [Rival("taker", 3.0, 0.01, 1000, quoting=False)])
        self.m = {"cycles": [], "samples": [], "picked": 0, "picked_shares": 0, "picked_cost": 0.0,
                  "danger": {}, "danger_max": 0.0, "dups": 0, "fatal": None}
        self.pick_queue = []              # (time, eid) Polymarket moves rivals will act on

    def build_markets(self):
        rng, mk, books, true = self.rng, [], {}, {}
        races = [("U.S. House", 0.085), ("U.S. Senate", 0.375)]
        races += [(f"Race {i:03d}", rng.choice([rng.uniform(0.03, 0.2), rng.uniform(0.2, 0.8), rng.uniform(0.8, 0.97)]))
                  for i in range(116)]
        n = 1
        for race, p in races:
            for party, q in (("Republican", p), ("Democratic", 1 - p)):
                eid = str(1000 + n)
                mk.append(market(str(n), eid, party, race))
                q = round(q, 3)
                true[f"{race}|{party}"] = q
                books[eid] = self.house(q)
                n += 1
        self.eng.markets_list, self.eng.books, self.true = mk, books, true
        self.key_of = {m["exchanges"][0]["id"]: f"{M.RACE_TITLE.match(m['title']).group(2)}|{M.RACE_TITLE.match(m['title']).group(1)}"
                       for m in mk}
        self.house_levels = {e: (dict(b["bids"][0]), dict(b["asks"][0])) for e, b in books.items()}
        self.refs = Refs(true)

    @staticmethod
    def house(q):
        return {"bids": [lvl(max(0.005, floor_tick(q - 0.04)), 5000)], "asks": [lvl(min(0.995, ceil_tick(q + 0.04)), 5000)]}

    # --- the world moving -----------------------------------------------------------------------
    def move_polymarket(self):
        moves = {}
        for k, p in self.true.items():
            if "Democratic" in k:
                continue
            r = self.rng.random()
            d = self.rng.gauss(0, 0.002) + (self.rng.choice((-1, 1)) * self.rng.uniform(0.01, 0.025) if r < 0.03 else 0)
            q = round(min(0.97, max(0.03, p + d)), 3)
            dem = k.replace("Republican", "Democratic")
            for kk, v in ((k, q), (dem, round(1 - q, 3))):
                if abs(v - self.true[kk]) > 1e-9:
                    moves[kk] = abs(v - self.true[kk])
                self.true[kk] = v
        r = self.refs
        r.prices, r.last_moves, r.version = dict(self.true), moves, r.version + 1
        if r.on_refresh:
            r.on_refresh(moves)
        now = self.clock.now()
        for e, k in self.key_of.items():
            if moves.get(k, 0) >= 0.005:
                for rv in self.rivals:
                    self.pick_queue.append((now + rv.delay, e, rv))

    def ours(self, eid):
        return [o for o in self.eng.orders.values() if o["exchangeId"] == eid]

    def pick_off(self):
        now, keep = self.clock.now(), []
        for t, eid, rv in self.pick_queue:
            if t > now:
                keep.append((t, eid, rv)); continue
            fv = self.true[self.key_of[eid]]
            with self.ex.lock:
                for o in self.ours(eid):
                    is_bid, p = self.eng.yes_view(o)
                    if (p >= fv + 0.01) if is_bid else (p <= fv - 0.01):
                        q = min(o["quantity"], 5 * rv.size)
                        self.m["picked"] += 1; self.m["picked_shares"] += q
                        self.m["picked_cost"] += abs(p - fv) * q
                        self.trade_against(o, q)
        self.pick_queue = keep

    def trade_against(self, o, q):
        is_bid, p = self.eng.yes_view(o)
        eid = o["exchangeId"]
        o["quantity"] -= q
        self.eng.inv[eid] = self.eng.inv.get(eid, 0) + (q if is_bid else -q)
        self.eng.fills.append({"id": len(self.eng.fills) + 1, "orderId": o["id"], "exchangeId": eid, "price": p,
                               "quantity": q if is_bid else -q, "side": "yes" if is_bid else "no",
                               "filledAt": M.iso(self.clock.utcnow())})
        if o["quantity"] <= 0:
            self.eng.orders.pop(o["id"], None)
        self.feed.push([eid], account=True)

    def rivals_requote(self):
        now = self.clock.now()
        for rv in self.rivals:
            if now < rv.next or not rv.quoting:
                continue
            rv.next = now + rv.delay
            changed = []
            with self.ex.lock:
                ours = {}
                for o in self.eng.orders.values():
                    is_bid, p = self.eng.yes_view(o)
                    ours.setdefault(o["exchangeId"], []).append((is_bid, p))
                for eid in self.bot.ex:
                    fv = self.true[self.key_of[eid]]
                    bk = self.eng.books[eid]
                    others = {"bids": bk["bids"] + [{"price": p} for b, p in ours.get(eid, []) if b],
                              "asks": bk["asks"] + [{"price": p} for b, p in ours.get(eid, []) if not b]}
                    mine = rv.quotes.get(eid)
                    bids = [l["price"] for l in others["bids"] if not (mine and abs(l["price"] - mine[0]) < 1e-9)]
                    asks = [l["price"] for l in others["asks"] if not (mine and abs(l["price"] - mine[1]) < 1e-9)]
                    bid = min(rnd((max(bids) if bids else 0.005) + TICK), floor_tick(fv - rv.floor))
                    ask = max(rnd((min(asks) if asks else 0.995) - TICK), ceil_tick(fv + rv.floor))
                    if mine != (bid, ask):
                        rv.quotes[eid] = (bid, ask)
                        changed.append(eid)
                for eid in changed:
                    self.rebuild_book(eid)
            if changed:
                self.feed.push(changed)

    def rebuild_book(self, eid):
        hb, ha = self.house_levels[eid]
        bids, asks = [dict(hb)], [dict(ha)]
        for rv in self.rivals:
            if eid in rv.quotes:
                b, a = rv.quotes[eid]
                bids.append(lvl(b, rv.size * (10 if eid in self.headline else 1)))
                asks.append(lvl(a, rv.size * (10 if eid in self.headline else 1)))
        self.eng.books[eid] = {"bids": sorted(bids, key=lambda l: -l["price"]), "asks": sorted(asks, key=lambda l: l["price"])}

    def noise_takers(self):
        """Uninformed traders: now and then one takes the best price in a random market."""
        for _ in range(2):
            eid = self.rng.choice(list(self.bot.ex))
            is_bid = self.rng.random() < 0.5
            with self.ex.lock:
                best = None
                for o in self.ours(eid):
                    b, p = self.eng.yes_view(o)
                    if b == is_bid:
                        best = o
                if best is None:
                    continue
                b = self.eng.full_book(eid)["bids" if is_bid else "asks"]
                if b and abs(b[0]["price"] - self.eng.yes_view(best)[1]) < 1e-9:
                    self.trade_against(best, min(best["quantity"], self.rng.choice((50, 100, 200))))

    def sample(self, dt):
        now = self.clock.now()
        with self.ex.lock:
            per = {}
            for o in self.eng.orders.values():
                is_bid, p = self.eng.yes_view(o)
                if o["quantity"] == 1 and (p <= 0.005 or p >= 0.995):
                    continue              # the self-test's two 1-share orders
                per.setdefault(o["exchangeId"], []).append((is_bid, p))
        quoted = len(per)
        dups = sum(1 for v in per.values() for side in (True, False) if sum(1 for b, _ in v if b == side) > 1)
        self.m["dups"] = max(self.m["dups"], dups)
        self.m["samples"].append((now - self.t_open, quoted / len(self.bot.ex)))
        for eid in self.headline:
            fv = self.true[self.key_of[eid]]
            bad = any((p > fv - 0.0049) if b else (p < fv + 0.0049) for b, p in per.get(eid, []))
            d = self.m["danger"].get(eid, 0.0)
            d = d + dt if bad else 0.0
            self.m["danger"][eid] = d
            self.m["danger_max"] = max(self.m["danger_max"], d)

    def driver(self):
        last_poly, last_sample, last_noise = -1e9, self.clock.now(), 0.0
        end = self.t_open + self.minutes * 60
        while self.clock.now() < end and self.bot.running:
            now = self.clock.now()
            if now - last_poly >= 5:
                last_poly = now
                self.move_polymarket()
            self.rivals_requote()
            self.pick_off()
            if now - last_noise >= 1:
                last_noise = now
                self.noise_takers()
            if now - last_sample >= 2:
                self.sample(now - last_sample)
                last_sample = now
            self.clock.sleep(0.25)
        self.bot.running = False

    def run(self):
        # Before the open: every book downloaded (as on day one), rivals already quoting.
        self.rivals_requote()
        # (Set directly, not downloaded, so the session starts at the open. Day one: the last pre-open
        # downloads were >= open_quiet_seconds before it.)
        for eid, x in self.bot.ex.items():
            x.book = M.strip_own(self.eng.full_book(eid), [])
            x.book_time = x.verified = self.clock.monotonic() - self.cfg.open_quiet_seconds
        self.t_open = self.clock.now()
        real_cycle = self.bot.cycle

        def timed():
            t0 = self.clock.now()
            try:
                real_cycle()
            finally:
                self.m["cycles"].append((t0, self.clock.now() - t0))
        self.bot.cycle = timed
        th = threading.Thread(target=self.driver, daemon=True)
        th.start()
        try:
            self.bot.run()
        except SystemExit as e:
            self.m["fatal"] = (round(self.clock.now() - self.t_open), e.code)
        finally:
            self.bot.running = False
            th.join(timeout=5)
            M.time, M.utcnow = self.saved
        return self.metrics()

    def metrics(self):
        m, s = self.m, self.m["samples"]
        def at(t):
            v = [f for tt, f in s if tt <= t * 60]
            return round(v[-1], 2) if v else 0.0
        def reach(frac):
            return next((round(t) for t, f in s if f >= frac), None)
        matched = set(map(str, self.bot.order_meta))
        fills = [f for f in self.eng.fills]
        unmatched = sum(1 for f in fills if str(f["orderId"]) not in matched)
        mins = max(1e-9, self.minutes)
        writes = self.ex.n["writes"]
        return {"t50": reach(0.5), "t80": reach(0.8), "q1": at(1), "q2": at(2), "q5": at(5), "q10": at(10),
                "longest_cycle": round(max((d for _, d in m["cycles"]), default=0), 1),
                "headline_stale_max": round(m["danger_max"], 1),
                "writes_per_min": round(writes / mins, 1), "reads_per_min": round(self.ex.n["reads"] / mins, 1),
                "picked": m["picked"], "picked_shares": m["picked_shares"], "picked_cost": round(m["picked_cost"], 1),
                "fills": len(fills), "unmatched": unmatched, "dups_max": m["dups"],
                "409": self.ex.n["409"], "429": self.ex.n["429"], "timeouts": self.ex.n["timeouts"],
                "fatal": m["fatal"]}


def parse_overrides():
    out = {}
    for kv in filter(None, os.environ.get("SCENARIO_SET", "").split(",")):
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main():
    import logging
    logging.basicConfig(level=logging.CRITICAL if not os.environ.get("SCENARIO_LOG") else logging.INFO)
    kinds = {"slow": ["slow"], "crowded": ["crowded"], "both": ["slow", "crowded"]}[sys.argv[1] if len(sys.argv) > 1 else "both"]
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    minutes = float(sys.argv[3]) if len(sys.argv) > 3 else 10
    selftest = os.environ.get("SCENARIO_SELFTEST", "1") == "1"
    for kind in kinds:
        for seed in range(1, seeds + 1):
            r = World(kind, seed, minutes, parse_overrides(), selftest).run()
            print(kind, seed, json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
