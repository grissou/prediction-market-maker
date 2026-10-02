"""
Offline tests for T2.5, the passive pair unwind (pair_unwind_passive): a held complete set in a 2-leg race rests
one leg's ask at max(join, 1 - other leg's bid - max_cost); a fill is matched at once by a take of the other leg
at its bid, one slice at a time. Fake exchange, no network.

Run:  python tests/test_pair_passive.py      (exit code 0 = all passed)
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fakes import lvl, make_bot                # noqa: E402
import mm_bot as M                                        # noqa: E402

logging.basicConfig(level=logging.ERROR, format="    log %(levelname)s %(message)s")
RESULTS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"   [{extra}]" if extra and not cond else ""))
    RESULTS.append(bool(cond))


SETS = 7335


def books(a_bid=0.62, a_ask=0.64, b_bid=0.37, b_ask=0.375, size=1000):
    return {"11": {"bids": [lvl(a_bid, size)], "asks": [lvl(a_ask, size)]},     # Rep Ohio = leg A
            "12": {"bids": [lvl(b_bid, size)], "asks": [lvl(b_ask, size)]},     # Dem Ohio = leg B
            "21": {"bids": [lvl(0.48, 1000)], "asks": [lvl(0.56, 1000)]},
            "22": {"bids": [lvl(0.44, 1000)], "asks": [lvl(0.52, 1000)]}}


def bot(on=True, held=(SETS, SETS), **bk):
    api, b = make_bot(books=books(**bk))
    b.cfg.pair_unwind_passive = on
    api.inv = {"11": float(held[0]), "12": float(held[1])}
    return api, b


def run_cycles(b, n):
    for _ in range(n):            # (fakes.run_cycles stops the bot after, which cancels every order)
        b.cycle()


def asks(api, eid):
    return [(p, q) for s, p, q in api.ours(eid) if s == "ask"]


def bids(api, eid):
    return [(p, q) for s, p, q in api.ours(eid) if s == "bid"]


def never_crossed(api):
    for e in ("11", "12", "21", "22"):
        bb, aa = bids(api, e), asks(api, e)
        if bb and aa and max(p for p, _ in bb) >= min(p for p, _ in aa) - 1e-9:
            return False
    return True


print("--- settings")
c = M.Config()
check("defaults: passive OFF, max cost 0.3c, slice = pair_unwind_max_frac (1% cash)",
      (c.pair_unwind_passive, c.pair_unwind_max_cost, c.pair_unwind_max_frac) == (False, 0.003, 0.01))
good, bad = M.validate_overrides({"pair_unwind_passive": True, "pair_unwind_max_cost": 0.005}, c)
check("both live-overridable", len(good) == 2 and not bad, bad)
_, bad = M.validate_overrides({"pair_unwind_max_cost": 0.5}, c)
check("max cost range-checked (0.5 refused)", bool(bad), bad)

print("--- the plan on scripted books (bids 0.62 / 0.37)")
api, b = bot()
for e, bk in api.books.items():
    b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
inv = dict(api.inv)
p = b.pair_passive_plan(["11", "12"], inv, {})
check("leg A's ask joins the best other ask 0.64 (>= 1 - 0.37 - 0.003); leg B would sit behind its touch",
      p and (p["leg"], p["other"], p["price"], p["sign"]) == ("11", "12", 0.64, 1), p)
check("slice = min(sets, B's bid size 1,000, 1% cash / 0.64 = 1,562) = 1,000", p and p["slice"] == 1000, p)
b.ex["11"].book["asks"] = [lvl(0.625, 1000)]                   # touch below the floor: the floor binds
p = b.pair_passive_plan(["11", "12"], inv, {})
check("touch 0.625 under the floor 1 - 0.37 - 0.003 -> 0.63 (rounded up to the grid): set closes for 1.000",
      p and p["price"] == 0.63 and p["price"] + 0.37 >= 1 - 0.003, p)
b.ex["11"].book["asks"] = []                                  # no other ask at all: the floor
b.ex["12"].book["asks"] = [lvl(0.40, 1000)]
p = b.pair_passive_plan(["11", "12"], inv, {})
check("B's ask joins at 0.40 (gap 0) beats A sitting alone at the floor? both gap 0 -> best proceeds",
      p and p["price"] in (0.63, 0.40), p)
check("no set (one leg only) -> no plan", b.pair_passive_plan(["11", "12"], {"11": 5000.0, "12": 0.0}, {}) is None)
check("less than 1 share of a set -> no plan", b.pair_passive_plan(["11", "12"], {"11": 0.4, "12": 900.0}, {}) is None)
b.ex["12"].book["bids"] = []
check("other leg has no bid -> A cannot be the resting leg",
      (b.pair_passive_plan(["11", "12"], inv, {}) or {}).get("leg") != "11")

print("--- the bot: rest, fill, take, one slice at a time")
api, b = bot()
unmatched = []


def record():
    unmatched.append(abs((SETS - api.inv.get("11", 0)) - (SETS - api.inv.get("12", 0))))


run_cycles(b, 1)
record()
check("cycle 1: our ask on leg A at 0.64 for one slice (1,000)", (0.64, 1000) in asks(api, "11"), api.ours("11"))
check("...the only passive slice: no ask of ours on A beyond it",
      sum(q for _, q in asks(api, "11")) == 1000, api.ours("11"))
check("...never own bid >= own ask anywhere", never_crossed(api), [api.ours(e) for e in ("11", "12")])
n_fills = len(api.fills)
api.fill("11", False, 400)                                   # someone lifts 400 of our ask
record()
run_cycles(b, 1)
record()
f = [x for x in api.fills[n_fills:] if x["exchangeId"] == "12"]
check("partial fill 400 -> 400 of leg B sold at its bid 0.37 at once", api.inv["12"] == SETS - 400
      and f and f[-1]["price"] == 0.37 and f[-1]["quantity"] == -400, (api.inv, f))
check("...legs matched again", api.inv["11"] == api.inv["12"] == SETS - 400, api.inv)
run_cycles(b, 1)
check("next cycle: the slice's rest (600) keeps resting on A, nothing more",
      sum(q for _, q in asks(api, "11")) == 600 and b.pp.get("Ohio Senate", {}).get("left") == 600,
      (api.ours("11"), b.pp))
check("...never own bid >= own ask", never_crossed(api))
api.fill("11", False, 600)
record()
run_cycles(b, 1)
record()
check("rest of the slice filled -> 600 more of B sold: 1,000 sets closed", api.inv["11"] == api.inv["12"] == SETS - 1000,
      api.inv)
run_cycles(b, 1)
st = b.pp.get("Ohio Senate") or {}
check("then a NEW slice planned: B's bid is used up, so B would rest at 1 - 0.62 - 0.003 -> 0.38",
      (st.get("leg"), st.get("price"), st.get("slice")) == ("12", 0.38, 1000), b.pp)
check("...but B has no fair value (no bid at all): decide() leaves it unquoted, and so does the slice",
      not asks(api, "12"), api.ours("12"))
api.books["12"]["bids"] = [lvl(0.365, 1000)]                # a new bid on B
run_cycles(b, 1)
st = b.pp.get("Ohio Senate") or {}
check("new bid 0.365 on B: the slice moves to A at 0.64 (joins; floor 0.635) and rests there",
      (st.get("leg"), st.get("price"), st.get("slice")) == ("11", 0.64, 1000) and (0.64, 1000) in asks(api, "11"),
      (b.pp, api.ours("11")))
check("sets closed counted", b.pp_sets_total == 1000, b.pp_sets_total)
check("never an unmatched leg above one slice (1,000)", max(unmatched) <= 1000, unmatched)
check("never own bid >= own ask", never_crossed(api))

print("--- the urgent leg: write budget defers it, cooldowns don't")
api, b = bot()
run_cycles(b, 1)
api.fill("11", False, 300)
b.arb_cooldown["Ohio Senate"] = 1e18                         # a cooldown does not hold the second leg back
b.ex["12"].take_until = 1e18
real = b.writes_ready
b.writes_ready = lambda n: False
run_cycles(b, 1)
check("write budget busy: no take yet (deferred)", api.inv["12"] == SETS, api.inv)
b.writes_ready = real
run_cycles(b, 1)
check("budget back: taken next cycle despite the cooldowns", api.inv["12"] == SETS - 300, api.inv)
check("...the race stays at most one slice unmatched", abs(api.inv["11"] - api.inv["12"]) == 0, api.inv)

print("--- the take only fills what is bid: the rest is retried, never sold twice")
api, b = bot()
run_cycles(b, 1)
api.books["12"]["bids"] = [lvl(0.37, 250)]                   # B's bid shrank after the slice was planned
api.fill("11", False, 1000)
run_cycles(b, 1)
check("only 250 bid: 250 sold, 750 still owed", api.inv["12"] == SETS - 250, api.inv)
check("...unmatched within one slice", abs(api.inv["11"] - api.inv["12"]) <= 1000, api.inv)
api.books["12"]["bids"] = [lvl(0.365, 2000)]
run_cycles(b, 1)
check("next cycle: the other 750 sold at the new best bid", api.inv["12"] == SETS - 1000, api.inv)
run_cycles(b, 2)
check("...and never more (no double take)", api.inv["12"] == SETS - 1000 or b.pp_sets_total >= 1000, api.inv)
check("never own bid >= own ask", never_crossed(api))

print("--- our own bid is kept below the passive ask")
api, b = bot()
for e, bk in api.books.items():
    b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
b.pp["Ohio Senate"] = b.pair_passive_plan(["11", "12"], dict(api.inv), {})
q = b.pair_passive_quote(b.ex["11"], M.Quote(0.645, 100, 0.66, 100, 0.65, 0.65))
check("a bid at 0.645 (>= ask 0.64) is moved a tick under it", q.ask == 0.64 and q.bid == 0.635 and q.bid < q.ask, q)
q = b.pair_passive_quote(b.ex["11"], M.Quote(0.60, 100, 0.66, 100, 0.61, 0.65))
check("a bid already below is left alone; ask replaced (price, slice, keep-limit = price)",
      (q.bid, q.ask, q.ask_size, q.ask_limit) == (0.60, 0.64, 1000, 0.64), q)
q = b.pair_passive_quote(b.ex["12"], M.Quote(0.30, 100, 0.40, 100))
check("the other leg's quote is untouched", q == M.Quote(0.30, 100, 0.40, 100), q)
check("a leg decide() left unquoted stays unquoted", b.pair_passive_quote(b.ex["11"], M.NO_QUOTE) == M.NO_QUOTE)

print("--- short set: the mirror (rest a bid, buy back the other leg at its ask)")
api, b = bot(held=(-SETS, -SETS), a_bid=0.60, a_ask=0.63, b_bid=0.36, b_ask=0.38)
for e, bk in api.books.items():
    b.ex[e].book = {k: [dict(x) for x in v] for k, v in bk.items()}
p = b.pair_passive_plan(["11", "12"], dict(api.inv), {})
check("short set: a BID rests on one leg at <= 1 + 0.003 - the other leg's ask",
      p and p["sign"] == -1 and p["price"] + (0.38 if p["leg"] == "11" else 0.63) <= 1.003 + 1e-9, p)
run_cycles(b, 1)
x = p["leg"]
y = "12" if x == "11" else "11"
check("...placed through the normal quote path", (p["price"], p["slice"]) in bids(api, x), api.ours(x))
api.fill(x, True, 500)
run_cycles(b, 1)
check("...filled 500 -> 500 of the other leg bought back at its ask", api.inv[y] == -SETS + 500, api.inv)
check("...never own bid >= own ask", never_crossed(api))

print("--- flag OFF: no change")
api, b = bot(on=False)
called = []
b.pair_passive_step = lambda *a, **k: called.append("step") or set()
b.pair_passive_quote = lambda ex, q: called.append("quote") or q
run_cycles(b, 2)
check("pair_passive_step / _quote never entered", not called, called)
check("no passive ask (0.64 x 1,000) on A", (0.64, 1000) not in asks(api, "11"), api.ours("11"))
api, b = bot(on=False)
run_cycles(b, 1)
api.fill("11", False, 400) if asks(api, "11") else None
run_cycles(b, 1)
check("a fill on A takes nothing on B", api.inv["12"] == SETS, api.inv)
check("no state", not b.pp)
api, b = bot()
run_cycles(b, 1)
b.cfg.pair_unwind_passive = False                            # switched off live: state dropped, normal quotes
run_cycles(b, 1)
check("switched off while a slice rests: state dropped, the passive ask replaced by the normal one",
      not b.pp and (0.64, 1000) not in asks(api, "11"), (b.pp, api.ours("11")))

print("--- status.json")
api, b = bot()
run_cycles(b, 1)
b.write_status(True)
import json                                                # noqa: E402
st = json.load(open(b.cfg.status_file))
check("status.json shows the open slice", st.get("pair_passive_open", {}).get("Ohio Senate", {}).get("price") == 0.64,
      st.get("pair_passive_open"))

print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
