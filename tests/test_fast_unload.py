"""
Offline tests for the fast unload after sweep fills (fast_unload_*): compute_quote's unload side, the window
bookkeeping (note_unloads / unload_side), and the Bot wiring with the fake exchange. No network.

Run:  python tests/test_fast_unload.py      (exit code 0 = all passed)
"""
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import make_bot                                # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


print("--- settings")
c = M.Config()
check("defaults: on, 2c / 100 sh trigger, 300 s, 0.5c from fair, size x1",
      (c.fast_unload_enabled, c.fast_unload_min_edge, c.fast_unload_min_shares, c.fast_unload_seconds,
       c.fast_unload_edge, c.fast_unload_size_mult) == (True, 0.02, 100, 300.0, 0.005, 1.0))
good, bad = M.validate_overrides({"fast_unload_enabled": False, "fast_unload_min_edge": 0.03,
                                  "fast_unload_min_shares": 200, "fast_unload_seconds": 120.0,
                                  "fast_unload_edge": 0.0, "fast_unload_size_mult": 0.5}, c)
check("all six settings are live-overridable", len(good) == 6 and not bad, bad)

print("--- compute_quote: the unload side")
cfg = M.Config()
cfg.skew_per_quote = 0.0                                 # isolate the unload floor from the inventory skew
cfg.skew_age_enabled = False
kw = dict(bankroll=100000, order_size=100)
base = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, **kw)
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, unload_side="ask", unload_edge=0.005, unload_size=1000, **kw)
check("long 1,000: ask at fv + 0.5c for 1,000 shares (normal: 0.54 at 100)",
      (q.ask, q.ask_size) == (0.505, 1000) and (base.ask, base.ask_size) == (0.54, 100), (q, base))
check("...the adding side (bid) unchanged", (q.bid, q.bid_size) == (base.bid, base.bid_size), (q, base))
check("...the keep limit follows the unload price (a resting unload order is safe)", q.ask_limit <= 0.505, q)
q = M.compute_quote(0.50, 300, 300, 0.45, 0.55, cfg, unload_side="ask", unload_edge=0.005, unload_size=1000, **kw)
check("size capped by the position: never flips (300 held, 1,000 to unload -> 300)", q.ask_size == 300, q)
q = M.compute_quote(0.50, -800, -800, 0.45, 0.55, cfg, unload_side="bid", unload_edge=0.005, unload_size=500, **kw)
check("short: bid at fv - 0.5c for 500", (q.bid, q.bid_size) == (0.495, 500), q)
q = M.compute_quote(0.503, 1000, 1000, 0.45, 0.55, cfg, unload_side="ask", unload_edge=0.0, unload_size=1000, **kw)
check("unload_edge 0: at fair value rounded away (fv 0.503 -> ask 0.505), never through fair", q.ask == 0.505, q)
q = M.compute_quote(0.503, -1000, -1000, 0.45, 0.55, cfg, unload_side="bid", unload_edge=0.0, unload_size=1000, **kw)
check("...short: bid 0.50", q.bid == 0.50, q)
q = M.compute_quote(0.50, 1000, 1000, 0.51, 0.56, cfg, unload_side="ask", unload_edge=0.005, unload_size=1000, **kw)
check("never crosses the other side's best (best bid 0.51 -> ask 0.515, not a take)", q.ask == 0.515, q)
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.505, cfg, unload_side="ask", unload_edge=0.005, unload_size=1000, **kw)
check("joins a best ask already at fv + 0.5c (no pennying through the unload floor)", q.ask == 0.505, q)
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, min_edge=0.015, unload_side="ask", unload_edge=0.005,
                    unload_size=1000, **kw)
check("ref_only edge (1.5c) overridden on the unload side", q.ask == 0.505, q)
q = M.compute_quote(0.50, -1000, -1000, 0.45, 0.55, cfg, unload_side="ask", unload_edge=0.005, unload_size=1000, **kw)
check("a window whose side would ADD (short, ask) is ignored", q == M.compute_quote(0.50, -1000, -1000, 0.45, 0.55,
                                                                                   cfg, **kw), q)
cfg2 = M.Config()                                        # with the default inventory skew already past fv + 0.5c
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg2, order_size=100, bankroll=100000, unload_side="ask",
                    unload_edge=0.005, unload_size=1000)
b2 = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg2, order_size=100, bankroll=100000)
check("never further from fair than the skewed normal quote", q.ask <= b2.ask and q.ask >= 0.50, (q, b2))
q = M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, reduce_only=True, unload_side="ask", unload_edge=0.005,
                    unload_size=1000, **kw)
check("reduce-only: the window does nothing (stricter anyway)",
      q == M.compute_quote(0.50, 1000, 1000, 0.45, 0.55, cfg, reduce_only=True, **kw), q)

print("--- the bot: a 3.5c sweep fill of 1,000 on Rep Ohio (fv 0.14, our bid 0.105)")


def bot(**kw):
    a, b = make_bot()
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    return a, b


def side(a, s, eid="11"):
    return [o for o in a.ours(eid) if o[0] == s]


def swept(**kw):
    a, b = bot(**kw)
    b.cycle()
    a.fill("11", True, 1000)                              # a student's market order walks our bid
    b.cycle()
    return a, b


a0, b0 = swept(fast_unload_enabled=False)
a, b = swept()
normal_ask = side(a0, "ask")
check("disabled = today: no window, the normal skewed ask", not b0.unloads and normal_ask
      and normal_ask[0][2] == 100, a0.ours("11"))
check("enabled: ask at fv + 0.5c (0.145) for 1,000 shares, in the cycle that read the fill",
      side(a, "ask") == [("ask", 0.145, 1000)], a.ours("11"))
check("...the bid side unchanged", side(a, "bid") == side(a0, "bid"), (a.ours("11"), a0.ours("11")))
check("...window: ask side, 1,000 to unload, ~300 s", b.unloads.get("11", {}).get("side") == "ask"
      and b.unloads["11"]["left"] == 1000 and 290 < b.unloads["11"]["until"] - time.monotonic() <= 300, b.unloads)
check("...status.json counts open windows", b.health.get("fast_unload_windows") == 1, b.health.get("fast_unload_windows"))
check("...other markets unaffected", a.ours("12") == a0.ours("12") and a.ours("21") == a0.ours("21"))
b.cycle()
check("...stays while the window is open", side(a, "ask") == [("ask", 0.145, 1000)], a.ours("11"))

a.fill("11", False, 700)                                  # 700 unloaded at 0.145
b.cycle()
check("partly unloaded (700): the rest (300) still offered at 0.145, position 300", side(a, "ask") == [("ask", 0.145, 300)]
      and a.inv["11"] == 300 and b.unloads["11"]["left"] == 300, (a.ours("11"), b.unloads))
a.fill("11", False, 300)
b.cycle()
check("fully unloaded: window closed (never flips: no NO bought by it)", "11" not in b.unloads and a.inv["11"] == 0
      and b.health.get("fast_unload_windows") == 0, (b.unloads, a.inv))

a, b = swept()
a.inv["11"] = 300                                         # 700 left some other way (e.g. a take): position 300
b.cycle()
check("position shrank to 300 another way: unload size capped at 300", side(a, "ask") == [("ask", 0.145, 300)],
      a.ours("11"))
b.unloads["11"]["until"] = time.monotonic() - 1           # 300 s later
b.cycle()
a0, b0 = bot(fast_unload_enabled=False)
a0.inv = {"11": 300.0}
b0.cycle()
check("expiry: the normal quote is restored", "11" not in b.unloads and side(a, "ask") == side(a0, "ask"),
      (a.ours("11"), a0.ours("11")))

a, b = swept()
a.books["11"] = {"bids": [{"price": 0.12, "quantity": 1000}],
                 "asks": [{"price": 0.20, "quantity": 1000}]}  # fair value moves up to 0.16 (against the unload)
b.cycle()
ask = side(a, "ask")
check("fair moved against us (0.14 -> 0.16): the unload ask follows, never through fair (0.165)",
      ask and ask[0][1] == 0.165, a.ours("11"))

# Urgency: churn control would hold a young safe ask; the unload window places it in the fill's cycle.
a, b = bot(churn_control=True, min_quote_life_seconds=60.0)
b.cycle()
a.fill("11", True, 1000)
b.cycle()
check("urgent: placed in the same cycle as the fill despite churn control (young ask not held)",
      side(a, "ask") == [("ask", 0.145, 1000)], a.ours("11"))
check("...and sent in the urgent group (like a Polymarket move)", b.change_key(b.ex["11"], False, True)[0] == 0.5)

a, b = bot()
b.cycle()
a.fill("11", True, 50)                                    # below fast_unload_min_shares
b.cycle()
check("a 50-share fill (< 100 min shares) opens nothing", not b.unloads, b.unloads)

a, b = bot(fast_unload_min_edge=0.04)                    # our 3.5c fill is below a 4c minimum
b.cycle()
a.fill("11", True, 1000)
b.cycle()
check("a fill below fast_unload_min_edge opens nothing", not b.unloads and side(a, "ask")[0][2] == 100, a.ours("11"))

a, b = swept()
b.global_reduce = True
q = b.decide(b.ex["11"], 0.14, {"11": 1000.0}, {"11": 1000.0}, True, 0.0, time.monotonic())
check("reduce-only: decide ignores the window", q.ask != 0.145 or q.ask_size != 1000, q)
b.global_reduce = False
b.ref_only = {"11"}
q = b.decide(b.ex["11"], 0.14, {"11": 1000.0}, {"11": 1000.0}, False, 0.0, time.monotonic())
check("ref_only market: the window overrides the wider edge and the small size", (q.ask, q.ask_size) == (0.145, 1000), q)

print("--- note_unloads (unit)")


class Grab(logging.Handler):
    def __init__(self):
        super().__init__(); self.msgs = []

    def emit(self, r): self.msgs.append(r.getMessage())


a, b = bot()
g = Grab()
M.log.addHandler(g)
old_level = M.log.level
M.log.setLevel(logging.INFO)
M.log.propagate = False
now_iso = M.iso(M.utcnow())
t = time.time()
b.order_meta.update({1: {"our_side": "ask", "price": 0.55, "fv": 0.515, "t": t},
                     2: {"our_side": "ask", "price": 0.55, "fv": 0.535, "t": t},
                     3: {"our_side": "ask", "price": 0.55, "fv": 0.515, "take": True, "t": t},
                     4: {"our_side": "bid", "price": 0.50, "fv": 0.53, "t": t}})
b.note_unloads([{"orderId": 2, "exchangeId": "21", "quantity": -1200, "filledAt": now_iso}], {"21": -1200.0}, 100.0)
check("1.5c edge: nothing", not b.unloads, b.unloads)
b.note_unloads([{"orderId": 3, "exchangeId": "21", "quantity": -1200, "filledAt": now_iso}], {"21": -1200.0}, 100.0)
check("a take fill: nothing", not b.unloads, b.unloads)
old = M.iso(M.utcnow() - M.timedelta(minutes=10))
b.note_unloads([{"orderId": 1, "exchangeId": "21", "quantity": -1200, "filledAt": old}], {"21": -1200.0}, 100.0)
check("a fill older than the window: nothing", not b.unloads, b.unloads)
b.note_unloads([{"orderId": 4, "exchangeId": "21", "quantity": 1200, "filledAt": now_iso}], {"21": -300.0}, 100.0)
check("a 3c bid fill that REDUCED a short: nothing", not b.unloads, b.unloads)
b.note_unloads([{"orderId": 1, "exchangeId": "21", "quantity": -1200, "filledAt": now_iso}], {"21": -1200.0}, 100.0)
check("3.5c ask fill of 1,200 adding to a short: bid window for 1,200 until +300 s",
      b.unloads.get("21") == {"until": 400.0, "side": "bid", "left": 1200.0}, b.unloads)
msgs = [m for m in g.msgs if "fast unload" in m]
check("one INFO line, as specified", msgs == ["fast unload Rep Utah Senate: sold 1,200 at +3.5c, bidding 0.5c under "
                                               "fair for 300 s"] or (len(msgs) == 1 and "sold 1,200 at +3.5c, bidding "
                                               "0.5c under fair for 300 s" in msgs[0]), msgs)
b.note_unloads([{"orderId": 1, "exchangeId": "21", "quantity": -500, "filledAt": now_iso}], {"21": -1700.0}, 200.0)
check("a second sweep adds its shares and extends the window", b.unloads["21"] == {"until": 500.0, "side": "bid",
                                                                                    "left": 1700.0}, b.unloads)
b.note_unloads([{"orderId": 4, "exchangeId": "21", "quantity": 400, "filledAt": now_iso}], {"21": -1300.0}, 210.0)
check("a fill on the reducing side counts the shares down", b.unloads["21"]["left"] == 1300.0, b.unloads)
ex = b.ex["21"]
ex.inv = -1300.0
check("unload_side: open while short and before 'until'", b.unload_side(ex, 300.0) == "bid")
ex.inv = 0.0
check("...closed once the position is gone", b.unload_side(ex, 300.0) is None and "21" not in b.unloads)
M.log.removeHandler(g)
M.log.setLevel(old_level)
M.log.propagate = True

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
