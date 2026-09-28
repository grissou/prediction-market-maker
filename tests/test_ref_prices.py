"""
Offline tests for ref_prices.py. HTTP calls are replaced with fakes, so no network is used.

Run:  python tests/test_ref_prices.py      (exit code 0 = all passed)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ref_prices as R                                    # noqa: E402

RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- turning a market into one price")
check("tight market -> mid-point", abs(R.market_price(0.40, 0.41) - 0.405) < 1e-9)
check("wide market -> last trade", R.market_price(0.10, 0.50, 0.30) == 0.30)
check("wide market, no trade -> no price", R.market_price(0.10, 0.50) is None)
check("one-sided market -> last trade", R.market_price(None, 0.005, "0.004") == 0.004)
R.REF.use_last_trade = False
check("use_last_trade off -> wide market has no price", R.market_price(0.10, 0.50, 0.30) is None)
R.REF.use_last_trade = True

print("--- fetching")
seen = {}
def fake_get(url, params=None):
    seen["url"], seen["params"] = url, params
    if "polymarket" in url or "gamma" in url:
        return [{"id": "1", "bestBid": 0.40, "bestAsk": 0.42, "closed": False, "volumeNum": 12345},
                {"id": "2", "bestBid": 0.99, "bestAsk": 1.0, "closed": True}]        # resolved -> ignored
    return {"markets": [{"ticker": "K-R", "status": "active", "yes_bid_dollars": "0.3000", "yes_ask_dollars": "0.3200"},
                        {"ticker": "K-D", "status": "settled", "yes_bid_dollars": "0.9", "yes_ask_dollars": "1"}]}
real_get, R.http_get = R.http_get, fake_get
pm = R.fetch_polymarket(["1", "2"])
check("Polymarket: open market priced (price, spread), resolved one skipped",
      set(pm) == {"1"} and abs(pm["1"][0] - 0.41) < 1e-9 and abs(pm["1"][1] - 0.02) < 1e-9, pm)
check("Polymarket: ids sent as repeated ?id= parameters", ("id", "1") in seen["params"] and ("id", "2") in seen["params"], seen)
check("Polymarket: all-time volume comes along too (mm_bot sizes quotes by it)", len(pm["1"]) == 3 and pm["1"][2] == 12345.0, pm)
ka = R.fetch_kalshi(["K-R", "K-D"])
check("Kalshi: active market priced (price, spread), settled one skipped",
      set(ka) == {"K-R"} and abs(ka["K-R"][0] - 0.31) < 1e-9, ka)
check("last-trade fallback reports no spread (so mm_bot treats it as thin)", R.market_quote(0.10, 0.50, 0.30) == (0.30, None))
R.http_get = real_get

print("--- ReferencePrices (what mm_bot uses)")
path = os.path.join(tempfile.mkdtemp(), "ref_map.json")
json.dump({"Ohio Senate|Republican": {"source": "polymarket", "id": "1"},
           "Ohio Senate|Democratic": {"source": "kalshi", "id": "K"},
           "Utah Senate|Republican": {"source": "polymarket", "id": None}}, open(path, "w"))
calls = {"n": 0}
def fake_pm(ids):
    calls["n"] += 1
    return {"1": (0.40, 0.01)}
real_fetchers = dict(R.FETCHERS)
R.FETCHERS.update(polymarket=fake_pm, kalshi=lambda ids: {"K": (0.58, None)})
refs = R.ReferencePrices(path)
check("entries without an id are ignored", set(refs.mapping) == {"Ohio Senate|Republican", "Ohio Senate|Democratic"})
check("get() returns prices by key", refs.get() == {"Ohio Senate|Republican": 0.40, "Ohio Senate|Democratic": 0.58})
refs.get()
check("cached: no second download within refresh_seconds", calls["n"] == 1, calls)
check("spreads reported per key (None = last trade only)", refs.spreads() == {"Ohio Senate|Republican": 0.01, "Ohio Senate|Democratic": None}, refs.spreads())
R.FETCHERS["polymarket"] = lambda ids: (_ for _ in ()).throw(RuntimeError("down"))
refs.last_try = -1e9
check("source down: last good price kept", refs.get().get("Ohio Senate|Republican") == 0.40)
key = "Ohio Senate|Republican"
refs.prices[key] = (0.40, time.monotonic() - refs.cfg.max_age_seconds - 1)
check("...until it's older than max_age_seconds, then dropped", key not in refs.get())

print("--- background refresh and jump detection")
readings = iter([{"1": (0.40, 0.01)}, {"1": (0.45, 0.01)}] + [{"1": (0.45, 0.01)}] * 100)
R.FETCHERS.update(polymarket=lambda ids: next(readings), kalshi=lambda ids: {})
refs = R.ReferencePrices(path, R.RefConfig(refresh_seconds=0.2))
refs.start()
deadline = time.monotonic() + 3
while refs.version < 2 and time.monotonic() < deadline:
    time.sleep(0.05)
check("background thread refreshes on its own", refs.version >= 2, refs.version)
check("move since the previous reading is recorded (0.40 -> 0.45 = 5c)",
      abs(refs.last_moves.get(key, 0) - 0.05) < 1e-9 or refs.version > 2, refs.last_moves)
before = refs.version
refs.get(); refs.get()
check("get() never fetches by itself once started (no waiting in the bot's cycle)", refs.version - before <= 1)
refs.stop()
seen_moves = []
refs = R.ReferencePrices(path)
refs.on_refresh = seen_moves.append
R.FETCHERS.update(polymarket=lambda ids: {"1": (0.40, 0.01)}, kalshi=lambda ids: {})
refs.refresh(); R.FETCHERS["polymarket"] = lambda ids: {"1": (0.43, 0.01)}; refs.refresh()
check("on_refresh is called after every reading, with the moves (mm_bot wakes its loop on it)",
      len(seen_moves) == 2 and abs(seen_moves[1].get(key, 0) - 0.03) < 1e-9, seen_moves)
refs.on_refresh = lambda moves: 1 / 0
refs.refresh()
check("a failing callback never breaks the price thread", refs.version == 3)
R.FETCHERS.update(polymarket=lambda ids: {"1": (0.40, 0.01, 5e6)}, kalshi=lambda ids: {})
refs.on_refresh = None
refs.refresh()
check("volumes() reports each market's volume by key", refs.volumes().get(key) == 5e6, refs.volumes())
R.FETCHERS.clear(); R.FETCHERS.update(real_fetchers)

print("--- matching tournament races to Polymarket")
events = [
    {"slug": "ohio-senate-democratic-primary-winner", "title": "Ohio Senate Democratic Primary Winner", "closed": False},
    {"slug": "ohio-senate-margin-of-victory-2026", "title": "Ohio Senate Election Margin of Victory", "closed": False},
    {"slug": "ohio-us-senate-election-winner", "title": "Ohio Senate Election Winner", "closed": True},
    {"slug": "ohio-senate-winner-2024", "title": "Ohio Senate Election Winner", "closed": False, "endDate": "2024-11-05T00:00:00Z", "volume": 9e9},
    {"slug": "ohio-senate-election-winner", "title": "Ohio Senate Election Winner", "closed": False, "endDate": "2026-11-03T00:00:00Z", "volume": 1e6,
     "markets": [{"id": "631057", "groupItemTitle": "Sherrod Brown (D)", "bestBid": 0.57, "closed": False},
                 {"id": "631058", "groupItemTitle": "Jon Husted (R)", "bestBid": 0.40, "closed": False},
                 {"id": "631059", "groupItemTitle": "Person A", "bestBid": 0, "closed": False}]},
]
real_search, R.search_polymarket = R.search_polymarket, lambda q: events
ev = R.find_winner_event("Ohio Senate")
check("picks this cycle's open winner market (not primary / margin / closed / 2024)",
      ev and ev["slug"] == "ohio-senate-election-winner", ev and ev.get("slug"))
check("finds the Republican candidate", R.party_market(ev, "Republican")["id"] == "631058")
check("finds the Democrat candidate", R.party_market(ev, "Democratic")["id"] == "631057")
check("no Independent listed -> None", R.party_market(ev, "Independent") is None)
check("'TN-05 House race' is searched as 'TN-05 House'", R.find_winner_event("TN-05 House race") is None)  # no TN event in fakes
# Real case from Florida: a busier county sub-market must lose to the statewide event (which has no end date).
events = [
    {"slug": "florida-senate-election-miami-dade-county-winner", "title": "Florida Senate Election: Miami-Dade County Winner",
     "closed": False, "endDate": "2026-11-03T23:59:00Z", "volume": 5e6},
    {"slug": "florida-senate-election-winner", "title": "Florida Senate Election Winner", "closed": False, "endDate": None, "volume": 1e6},
]
ev = R.find_winner_event("Florida Senate")
check("statewide market beats a county sub-market (and a missing end date isn't 'over')",
      ev and ev["slug"] == "florida-senate-election-winner", ev and ev.get("slug"))
R.search_polymarket = real_search
check("key format shared with mm_bot", R.ref_key("Ohio Senate", "Republican") == "Ohio Senate|Republican")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
