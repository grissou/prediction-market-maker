"""
Offline tests for Package 5 T2.4 (tilt exposure as a risk limit): above tilt_exposure_max_frac x account, the side
that would grow |tilt_exposure| in a market (contribution sign = sign(Polymarket - c)) is switched off in decide;
the side that shrinks it stays; nothing is forced out; headline gate; flag off = identical quotes. No network.

Run:  python tests/test_tilt_limit.py      (exit code 0 = all passed)
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


def bot(**kw):
    a, b = make_bot()
    for k, v in kw.items():
        setattr(b.cfg, k, v)
    b.cycle()
    return b


def dec(b, pos, ref, exposure, fv=None, eid="21"):
    b.tilt_exposure = exposure
    fv = ref if fv is None else fv
    b.ex[eid].last_fv, b.ex[eid].cooldown_until = None, 0.0   # each call stands alone (no jump guard between calls)
    return b.decide(b.ex[eid], fv, {eid: float(pos)}, {eid: float(pos)}, False, 0.0, time.monotonic(),
                    ref=ref, book_fv=fv)


def bid_off(q):
    return q.bid is None or not q.bid_size


def ask_off(q):
    return q.ask is None or not q.ask_size


def same_bid(q, q0):
    return (q.bid, q.bid_size, q.bid_limit) == (q0.bid, q0.bid_size, q0.bid_limit)


def same_ask(q, q0):
    return (q.ask, q.ask_size, q.ask_limit) == (q0.ask, q0.ask_size, q0.ask_limit)


print("--- settings")
c = M.Config()
check("defaults: tilt_exposure_max_frac 0 (off), tilt_exposure_headline False",
      (c.tilt_exposure_max_frac, c.tilt_exposure_headline) == (0.0, False))
fields = list(M.Config.__dataclass_fields__)
check("the two settings close Config", fields[-2:] == ["tilt_exposure_max_frac", "tilt_exposure_headline"], fields[-4:])
check("...and OVERRIDABLE", list(M.OVERRIDABLE)[-2:] == ["tilt_exposure_max_frac", "tilt_exposure_headline"])
good, bad = M.validate_overrides({"tilt_exposure_max_frac": 0.1, "tilt_exposure_headline": True}, c)
check("both live-overridable", len(good) == 2 and not bad, bad)

print("--- flag off = identical quotes on a grid (any exposure)")
b0 = bot()
bank = b0.bankroll()
diff = n = quoted = 0
for pos in (-600, -200, 0, 200, 600):
    for ref in (0.30, 0.45, 0.50, 0.52, 0.55, 0.70):
        for fv in (ref - 0.01, ref, ref + 0.01):
            base = dec(b0, pos, ref, 0.0, fv)
            quoted += base.bid is not None or base.ask is not None
            for x in (-10 * bank, -0.2 * bank, 0.2 * bank, 10 * bank):
                n += 1
                diff += dec(b0, pos, ref, x, fv) != base
check(f"flag 0: {n} quotes with huge exposures equal the zero-exposure quote ({quoted} of 90 grid points quote)",
      diff == 0 and quoted >= 60, (diff, quoted))

print("--- on: over the limit, r > c (0.55 in a 2-leg race, c 0.5)")
b1 = bot(tilt_exposure_max_frac=0.10)
over, under = 0.2 * bank, 0.05 * bank
for pos in (300, 0, -300):
    q0 = dec(b0, pos, 0.55, 0.0)
    qp = dec(b1, pos, 0.55, over)
    check(f"pos {pos:+d}, exposure +20%: buying grows it -> bid off; ask unchanged", bid_off(qp) and same_ask(qp, q0)
          and not ask_off(q0), (qp, q0))
    qn = dec(b1, pos, 0.55, -over)
    check(f"pos {pos:+d}, exposure -20%: selling grows |it| -> ask off; bid unchanged", ask_off(qn) and same_bid(qn, q0)
          and not bid_off(q0), (qn, q0))
    check(f"pos {pos:+d}, exposure +-5% (under the limit): unchanged",
          dec(b1, pos, 0.55, under) == q0 and dec(b1, pos, 0.55, -under) == q0)

print("--- on: r < c (0.45)")
for pos in (300, 0, -300):
    q0 = dec(b0, pos, 0.45, 0.0)
    qp = dec(b1, pos, 0.45, over)
    check(f"pos {pos:+d}, exposure +20%: selling grows it (short x negative = positive) -> ask off; bid unchanged",
          ask_off(qp) and same_bid(qp, q0) and not ask_off(q0), (qp, q0))
    qn = dec(b1, pos, 0.45, -over)
    check(f"pos {pos:+d}, exposure -20%: buying grows |it| -> bid off; ask unchanged", bid_off(qn) and same_ask(qn, q0)
          and not bid_off(q0), (qn, q0))

print("--- unaffected: r == c, no Polymarket price")
for x in (over, -over):
    check(f"r == c = 0.5, exposure {x / bank:+.0%}: unchanged", dec(b1, 300, 0.50, x) == dec(b0, 300, 0.50, 0.0))
    check(f"ref None, exposure {x / bank:+.0%}: unchanged", dec(b1, 300, None, x, fv=0.55) == dec(b0, 300, None, 0.0,
                                                                                                   fv=0.55))

print("--- no forced exit: the reducing side is the normal quote, never more aggressive")
q0 = dec(b0, 600, 0.55, 0.0)
qp = dec(b1, 600, 0.55, 10 * bank)
check("long 600, r > c, exposure 10x the cap: ask price / size as flag off (no dump at the bid, no cross)",
      same_ask(qp, q0) and qp.ask > b1.ex["21"].book["bids"][0]["price"], (qp, q0))
q0 = dec(b0, -600, 0.45, 0.0)
qp = dec(b1, -600, 0.45, 10 * bank)
check("short 600, r < c, exposure +10x: the bid (buying back, shrinks exposure) as flag off",
      same_bid(qp, q0) and qp.bid < b1.ex["21"].book["asks"][0]["price"], (qp, q0))

print("--- headline gate")
bh = bot(tilt_exposure_max_frac=0.10, headline_races=("Utah Senate",))
bh0 = bot(headline_races=("Utah Senate",))
check("headline market, tilt_exposure_headline False: unchanged over the limit",
      dec(bh, 300, 0.55, over) == dec(bh0, 300, 0.55, 0.0))
bh.tilt_exposure = over
check("...tilt_blocks on Ohio (not headline), r > c, +20%: (no_bid, no_ask) = (True, False)",
      bh.tilt_blocks(bh.ex["11"], 0.55) == (True, False))
bh.cfg.tilt_exposure_headline = True
qh = dec(bh, 300, 0.55, over)
check("tilt_exposure_headline True: headline market limited too (bid off)", bid_off(qh), qh)

print("--- tilt_blocks direct (the take path uses it too)")
b1.tilt_exposure = 0.2 * bank
check("exactly at the cap: nothing blocked", (setattr(b1, "tilt_exposure", 0.1 * bank) or
                                              b1.tilt_blocks(b1.ex["21"], 0.6)) == (False, False))
b1.tilt_exposure = 0.2 * bank
check("r 0.6 / +20%: (True, False); r 0.4: (False, True)",
      b1.tilt_blocks(b1.ex["21"], 0.6) == (True, False) and b1.tilt_blocks(b1.ex["21"], 0.4) == (False, True))
b1.tilt_exposure = -0.2 * bank
check("r 0.6 / -20%: (False, True); r 0.4: (True, False)",
      b1.tilt_blocks(b1.ex["21"], 0.6) == (False, True) and b1.tilt_blocks(b1.ex["21"], 0.4) == (True, False))

print("--- live_sim feed (source)")
src = open(os.path.join(HERE, "live_sim.py")).read()
check("live_sim feeds bot.tilt_exposure behind the flag and reports tx_bind_frac",
      "bot.tilt_exposure = sum(" in src and "tx_bind_frac=" in src and "self.tx_bind" in src)

print(f"\n{sum(RESULTS)} of {len(RESULTS)} passed")
sys.exit(0 if all(RESULTS) else 1)
