#!/usr/bin/env python3
"""
Polymarket prices as the estimate of each tournament contract's probability.

mm_bot (mmbot/bot.py) reads them through the ReferencePrices class below: the probability p that most of the bot's
decisions start from (fair value, the edge of a holding, the value floor under a sale, the guards that pull a quote
when the book and Polymarket disagree or when Polymarket jumps) is the Polymarket price of the matching market,
scaled within the race by mmbot/pricing.py. Without a mapping file the bot quotes off the tournament book alone.

WHY POLYMARKET
    The tournament's books are seeded by a house market maker that follows real-money markets, and anyone can
    watch Polymarket move before the tournament book catches up, so the real-money price is the better estimate of
    the true probability. Polymarket's public Gamma API needs no account and does not use the tournament's request
    budget.

HOW mm_bot USES IT (automatic when ref_map.json exists next to the bot)
    refs = ReferencePrices("ref_map.json"); refs.start()      # refreshes in the background (ref_refresh_seconds)
    refs.get()     ->  {"Ohio Senate|Republican": 0.405, ...}   cached; never waits; never raises
    refs.spreads(), refs.volumes(), refs.ages()                 per key: the quote's width, all-time $ volume, age
    refs.on_refresh(moves)                                      callback after every reading (the jump guard)

THE RACE MAPPING (ref_map.json): one entry per tournament contract, keyed "<race>|<party>" (ref_key):
    "Ohio Senate|Republican": {"source": "polymarket", "id": "631058", "note": "Jon Husted (R) - Ohio Senate ..."}
    The id is the Polymarket market id of that party's candidate in the race's "election winner" event.
    Delete an entry, or set "id" to null, to use no reference for that contract.

COMMANDS (building and checking the mapping)
    python ref_prices.py suggest                 match every tournament race to a Polymarket "election winner"
                                                 market by name and add it to ref_map.json. Existing entries are
                                                 kept. REVIEW THE RESULT: it is name matching.
    python ref_prices.py search "Ohio Senate"    list candidate markets (ids, prices) to fix an entry by hand
    python ref_prices.py check                   every mapped market: tournament price vs the Polymarket price
                                                 (run it when the bot alerts about an unmapped or stale entry)
"""

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
import requests.adapters
from concurrent.futures import ThreadPoolExecutor

# =============================================================================================
# SETTINGS
# =============================================================================================

@dataclass
class RefConfig:
    refresh_seconds: float = 30.0      # re-download reference prices this often (Polymarket's own API, so this
                                       #   doesn't use the tournament's request budget). mm_bot overrides it with
                                       #   its own ref_refresh_seconds (5 s) while trading
    max_age_seconds: float = 300.0     # stop using a price this old (i.e. downloads have been failing), so the
                                       #   bot falls back to the tournament book instead of trusting stale data
    max_spread: float = 0.10           # an external bid/ask wider than this isn't a reliable price...
    use_last_trade: bool = True        # ...so fall back to its last trade price instead (False = no price)
    request_timeout: float = 10.0      # seconds per HTTP request
    batch_size: int = 50               # market ids per request
    parallel_fetches: int = 5          # requests in flight at once (one per batch: 229 ids = 5 requests, so a
                                       #   refresh takes one round trip, not five). 1 = one after another
    search_pause: float = 0.3          # pause between searches in `suggest` (be polite to the API)
    polymarket_url: str = "https://gamma-api.polymarket.com"


REF = RefConfig()

# =============================================================================================
# FIXED FACTS
# =============================================================================================

# How each tournament party shows up in Polymarket market names, e.g. "Jon Husted (R)".
PARTY_TAGS = {"Republican": ("(R)", "Republican"),
              "Democratic": ("(D)", "Democrat"),        # also matches "Democratic"
              "Independent": ("(I)", "Independent")}

log = logging.getLogger("mm.ref")


def ref_key(race, party):
    """The key used in ref_map.json and by mm_bot: 'Ohio Senate|Republican'."""
    return f"{race}|{party}"

# =============================================================================================
# FETCHING PRICES
# =============================================================================================

_session = None
_session_lock = threading.Lock()
_pool = None


def session():
    """One shared HTTP session: connections stay open between refreshes (day one: every request opened a new
    TLS connection, ~0.2-0.5 s each, five times per refresh)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": "mm_bot-reference-prices"})
            n = max(2, REF.parallel_fetches) + 2   # the pool's threads, plus the calling thread (search) + 1
            _session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=n))
        return _session


def http_get(url, params=None):
    r = session().get(url, params=params, timeout=REF.request_timeout)
    r.raise_for_status()
    return r.json()


def in_parallel(fn, items):
    """[fn(item) or the exception it raised] for every item, up to parallel_fetches at once."""
    global _pool
    if REF.parallel_fetches <= 1 or len(items) <= 1:
        out = []
        for it in items:
            try:
                out.append(fn(it))
            except Exception as e:
                out.append(e)
        return out
    with _session_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=REF.parallel_fetches, thread_name_prefix="ref-http")
    futures = [_pool.submit(fn, it) for it in items]
    out = []
    for f in futures:
        try:
            out.append(f.result())
        except Exception as e:
            out.append(e)
    return out


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def market_quote(bid, ask, last=None):
    """(probability, spread) for an external market: the bid/ask mid-point and its spread if it's a
    real two-sided market with a sensible spread, else (optionally) (last trade price, None), else
    (None, None). spread=None tells mm_bot the price is only a last trade, which may be old."""
    bid, ask, last = to_float(bid), to_float(ask), to_float(last)
    if bid is not None and ask is not None and 0 < bid < ask < 1 and ask - bid <= REF.max_spread:
        return (bid + ask) / 2, ask - bid
    if REF.use_last_trade and last is not None and 0 < last < 1:
        return last, None
    return None, None


def market_price(bid, ask, last=None):
    """Just the probability from market_quote."""
    return market_quote(bid, ask, last)[0]


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def fetch_polymarket(ids):
    """{market id: (probability, spread, all-time volume in $)} for Polymarket markets (Gamma API; public,
    no account needed). The volume tells mm_bot which markets people actually trade."""
    out = {}
    parts = chunks(ids, REF.batch_size)
    pages = in_parallel(lambda c: http_get(f"{REF.polymarket_url}/markets", [("id", i) for i in c] + [("limit", len(c))]),
                        parts)
    failed = [p for p in pages if isinstance(p, Exception)]
    if failed and len(failed) == len(pages):
        raise failed[0]                       # nothing at all: the caller keeps the old prices
    if failed:                                # some batches failed: their markets keep their old prices
        log.warning("reference prices: %d of %d Polymarket requests failed (%s)", len(failed), len(pages), failed[0])
    for page in pages:
        if isinstance(page, Exception):
            continue
        for m in page:
            if m.get("closed"):
                continue                      # already resolved: not a live opinion any more
            p, spread = market_quote(m.get("bestBid"), m.get("bestAsk"), m.get("lastTradePrice"))
            if p is not None:
                out[str(m["id"])] = (p, spread, to_float(m.get("volumeNum")) or to_float(m.get("volume")))
    return out


FETCHERS = {"polymarket": fetch_polymarket}      # by the mapping entry's "source" (only Polymarket today)

# =============================================================================================
# THE CLASS mm_bot USES
# =============================================================================================

def load_map(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_map(path, mapping):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(sorted(mapping.items())), f, indent=2)
    os.replace(tmp, path)


class ReferencePrices:
    """Cached reference prices for everything in a mapping file.

    Two ways to run it:
      - start() (what mm_bot does when trading): a background thread refreshes every
        refresh_seconds, so get() never waits for the network.
      - without start() (commands, tests): get() refreshes itself when the prices are older than
        refresh_seconds.
    get() never raises: if a source fails, the last good prices are kept until they're older than
    max_age_seconds, then dropped, so mm_bot simply quotes off the tournament book instead.

    For mm_bot's jump guard, every refresh bumps `version` and stores `last_moves`: how far each
    price moved since the previous reading.
    """

    def __init__(self, map_path, cfg=REF):
        self.cfg, self.map_path = cfg, map_path
        self.mapping = {k: v for k, v in load_map(map_path).items() if v and v.get("id")}
        self.prices = {}                      # key -> (probability, time.monotonic() when fetched)
        self.spread = {}                      # key -> bid/ask spread, or None if the price is a last trade
        self.volume = {}                      # key -> all-time traded volume ($), where the source reports it
        self.last_try = -1e9
        self.lock = threading.Lock()          # the background thread writes, mm_bot reads
        self.version = 0                      # +1 after every refresh (so mm_bot can spot a new reading)
        self.last_moves = {}                  # key -> |price change| at the latest refresh
        self.background = False
        self.stopped = threading.Event()
        self.fetch_seconds = None            # duration of the latest download
        self.on_refresh = None                # optional callback(moves) after every refresh (mm_bot wakes its loop)

    def start(self):
        """From now on, refresh in a background thread every refresh_seconds."""
        if not self.background:
            self.background = True
            threading.Thread(target=self._loop, name="ref-prices", daemon=True).start()

    def stop(self):
        self.stopped.set()

    def _loop(self):
        while not self.stopped.is_set():
            t0 = time.monotonic()
            try:
                self.refresh()
            except Exception as e:            # never let one bad reading stop the thread for good: if it died,
                log.warning("reference price refresh failed: %s", e)   # prices would go stale silently
            # refresh_seconds from START to start (the download itself takes ~1 s)
            self.stopped.wait(max(0.0, self.cfg.refresh_seconds - (time.monotonic() - t0)))

    def get(self):
        now = time.monotonic()
        if not self.background and now - self.last_try >= self.cfg.refresh_seconds:
            self.last_try = now
            self.refresh()
        with self.lock:
            return {k: p for k, (p, t) in self.prices.items() if now - t <= self.cfg.max_age_seconds}

    def volumes(self):
        """{key: all-time traded volume in $} - how busy each market is (mm_bot sizes its quotes by it)."""
        with self.lock:
            return dict(self.volume)

    def ages(self):
        """{key: seconds since its price was last downloaded}. A failed download keeps the old price (up to
        max_age_seconds) and still counts as a new reading (`version`), so callers that act on a reading being
        CURRENT (mm_bot's take logic) check this."""
        now = time.monotonic()
        with self.lock:
            return {k: now - t for k, (_, t) in self.prices.items()}

    def spreads(self):
        """{key: Polymarket bid/ask spread} (None where the price is only a last trade). mm_bot only
        leans on a price, and sizes with Kelly, where this is tight."""
        with self.lock:
            return dict(self.spread)

    def refresh(self):
        wanted = {}                           # source -> [ids]
        for entry in self.mapping.values():
            wanted.setdefault(entry.get("source", "polymarket"), []).append(str(entry["id"]))
        fetched, t0 = {}, time.monotonic()
        for source, ids in wanted.items():
            try:
                fetched[source] = FETCHERS[source](ids)
            except Exception as e:            # network, bad JSON, unknown source...: keep old prices
                log.warning("reference prices from %s failed: %s", source, e)
        now, moves = time.monotonic(), {}
        self.fetch_seconds = round(now - t0, 2)   # how long the download took (status.json shows it)
        with self.lock:
            for key, entry in self.mapping.items():
                quote = fetched.get(entry.get("source", "polymarket"), {}).get(str(entry["id"]))
                if quote is None:
                    continue
                p, spread = quote[0], quote[1]
                if len(quote) > 2 and quote[2] is not None:
                    self.volume[key] = quote[2]
                if key in self.prices:
                    moves[key] = abs(p - self.prices[key][0])
                self.prices[key], self.spread[key] = (p, now), spread
            self.last_moves, self.version = moves, self.version + 1
        if self.on_refresh:
            try:
                self.on_refresh(moves)
            except Exception as e:            # a problem in the caller must never stop the price thread
                log.warning("on_refresh callback failed: %s", e)
        log.debug("reference prices: %d of %d mapped markets priced", len(self.prices), len(self.mapping))

# =============================================================================================
# BUILDING THE MAPPING (command line)
# =============================================================================================

def search_polymarket(query):
    return http_get(f"{REF.polymarket_url}/public-search", {"q": query, "limit_per_type": 10}).get("events") or []


def find_winner_event(race):
    """The Polymarket 'election winner' event for a tournament race name such as 'Ohio Senate' or
    'TN-05 House race'. Skips primaries, margin-of-victory markets and resolved (past) elections."""
    name = re.sub(r"\s+race$", "", race).strip()
    exact = f"{name} election winner".lower()
    now = datetime.now(timezone.utc).isoformat()
    candidates = []
    for ev in search_polymarket(f"{name} election winner"):
        slug, title = (ev.get("slug") or "").lower(), (ev.get("title") or "").lower()
        if ev.get("closed") or "winner" not in slug or "primary" in slug or name.lower() not in title:
            continue
        if ":" in title:
            continue                          # a sub-market, e.g. "Florida Senate Election: Miami-Dade County Winner"
        # Several can match. Prefer: the exact title "<race> Election Winner", then events not already
        # over (a missing end date counts as not over), then the most traded.
        ends = ev.get("endDate")
        candidates.append((title == exact, ends is None or ends >= now, float(ev.get("volume") or 0), ev))
    candidates.sort(key=lambda c: c[:3], reverse=True)
    return candidates[0][3] if candidates else None


def party_market(event, party):
    """The market inside a winner event for one party, e.g. 'Jon Husted (R)' for Republican."""
    tags = [t.lower() for t in PARTY_TAGS.get(party, ())]
    matches = [m for m in event.get("markets") or []
               if not m.get("closed") and any(t in (m.get("groupItemTitle") or m.get("question") or "").lower() for t in tags)]
    # One candidate per party in a general election; if not, take the most likely one.
    return max(matches, key=lambda m: to_float(m.get("bestBid")) or 0, default=None)


def tournament_contracts():
    """(race, party, exchange id) for every race contract in the tournament, using mm_bot's client."""
    import mm_bot
    api = mm_bot.Api(mm_bot.CFG, live=False)
    out = []
    for m in api.markets():
        x = mm_bot.RACE_TITLE.match(m.get("title", ""))
        if x and len(m.get("exchanges", [])) == 1:
            out.append((x.group(2), x.group(1), str(m["exchanges"][0]["id"])))
    return api, out


def tournament_mids(api, eids):
    """Mid-price of each tournament book (bid/ask from the bulk endpoint)."""
    import mm_bot
    tid = api.tournament()["id"]
    mids = {}
    for chunk in chunks(eids, mm_bot.BULK_MAX_IDS):
        for eid, (b, a) in api.bulk_prices(chunk, tid).items():
            mids[eid] = (b + a) / 2 if b is not None and a is not None else None
    return mids


def fmt(p):
    return f"{p:.3f}" if p is not None else "  -  "


def cmd_suggest(map_path):
    api, contracts = tournament_contracts()
    mapping = load_map(map_path)
    races = sorted({race for race, _, _ in contracts})
    added = missing = 0
    for race in races:
        todo = [(p, e) for r, p, e in contracts if r == race and ref_key(race, p) not in mapping]
        if not todo:
            continue
        ev = find_winner_event(race)
        time.sleep(REF.search_pause)
        for party, _ in todo:
            m = party_market(ev, party) if ev else None
            if m is None:
                missing += 1
                print(f"  no match   {race} | {party}")
                continue
            mapping[ref_key(race, party)] = {"source": "polymarket", "id": str(m["id"]),
                                             "note": f"{m.get('groupItemTitle')} - {ev.get('title')}"}
            added += 1
    save_map(map_path, mapping)
    print(f"\nadded {added} entries, {missing} without a match -> {map_path}")
    print("NEXT: run `python ref_prices.py check` and look for big differences - they're often a wrong match.")


def cmd_search(query):
    for ev in search_polymarket(query):
        print(f"\nEVENT {ev.get('slug')}  |  {ev.get('title')}  |  closed={ev.get('closed')}  ends={ev.get('endDate')}")
        for m in (ev.get("markets") or [])[:12]:
            print(f"   id {m.get('id'):>9}  {str(m.get('groupItemTitle') or m.get('question'))[:45]:<45} "
                  f"bid {m.get('bestBid')}  ask {m.get('bestAsk')}  closed={m.get('closed')}")


def cmd_check(map_path):
    import mm_bot
    api, contracts = tournament_contracts()
    refs = ReferencePrices(map_path)
    prices = refs.get()
    mids = tournament_mids(api, [e for _, _, e in contracts])
    gap = mm_bot.CFG.ref_guard_gap
    print(f"{'contract':<38} {'tournament':>10} {'reference':>10} {'diff':>7}")
    flagged = 0
    for race, party, eid in sorted(contracts):
        key = ref_key(race, party)
        t, r = mids.get(eid), prices.get(key)
        diff = (r - t) if (r is not None and t is not None) else None
        flag = "  <-- bot will only quote one side" if diff is not None and abs(diff) > gap else ""
        flagged += bool(flag)
        print(f"{key[:38]:<38} {fmt(t):>10} {fmt(r):>10} {('%+.3f' % diff) if diff is not None else '   -  ':>7}{flag}")
    print(f"\n{len(prices)} of {len(contracts)} contracts have a reference price; {flagged} differ by more than {gap}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    here = os.path.dirname(os.path.abspath(__file__))
    map_path = os.path.join(here, "ref_map.json")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "suggest":
        cmd_suggest(map_path)
    elif cmd == "search" and len(sys.argv) > 2:
        cmd_search(" ".join(sys.argv[2:]))
    elif cmd == "check":
        cmd_check(map_path)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
