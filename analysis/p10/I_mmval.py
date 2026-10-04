"""I_mmval: what our fills are worth AT THE OUTCOME (Package 10, explorer I). Every fill valued at the latest Polymarket reference
(race-normalised p0 from H_outcome.load): a bid fill (we bought YES) earns p - price per share, an ask fill price - p.
Split by day, by hour on 3 Oct, by price bucket and side (tails: favourite bids / longshot asks = value side), and the last
journal hour (22:00-22:47 3 Oct, still tilt mode: ref_tilt on; value mode started ~07:30 4 Oct and is NOT in the snapshot).
Usage: python3 analysis/p10/I_mmval.py"""
import sys, os, gzip, re
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load
d, cash, st = load()
f = pd.read_csv('/home/claude/snap03/fills.csv')
f['eid'] = f.exchange_id.astype(str); d['eid'] = d.eid.astype(str)
P = d.set_index('eid').p0; L = d.set_index('eid').label; C = d.set_index('eid').c
f['p'] = f.eid.map(P); f['label'] = f.eid.map(L); f['c'] = f.eid.map(C)
f = f[f.our_side.isin(['bid', 'ask']) & f.p.notna()].copy()
f['t'] = pd.to_datetime(f.filled_at)
# fills.csv records some fills at the complementary (NO) price (fill_price + quote_price = 1): map them back to YES (vs the quote, else the mid)
M = d.set_index('eid').mid; f['mid'] = f.eid.map(M)
ref = f.quote_price.where(f.quote_price.notna(), f.mid)
flip = (np.abs(1 - f.fill_price - ref) + 0.05 < np.abs(f.fill_price - ref))
f.loc[flip, 'fill_price'] = 1 - f.loc[flip, 'fill_price']
print(f"fills mapped from the NO price back to YES: {int(flip.sum())} of {len(f)}")
f['sgn'] = np.where(f.our_side == 'bid', 1, -1)
f['ev'] = f.sgn * (f.p - f.fill_price) * f.qty                     # at the outcome (Polymarket now)
f['cap'] = np.where(f.our_side == 'bid', f.fill_price, 1 - f.fill_price) * f.qty
f['toward'] = np.sign(f.p - f.fill_price) == f.sgn                  # the fill is on Polymarket's side of the price
f['bucket'] = pd.cut(f.fill_price, [0, 0.05, 0.15, 0.5, 0.85, 0.95, 1.0])
f['day'] = f.t.dt.strftime('%m-%d')
print("ALL fills valued at the outcome (latest Polymarket):")
g = f.groupby('day').agg(fills=('qty', 'size'), shares=('qty', 'sum'), ev=('ev', 'sum'), cap=('cap', 'sum'))
g['ev_c_per_share'] = 100 * g.ev / g.shares; g['ev_per_cap'] = g.ev / g.cap
print(g.round(3).to_string())
print("\nby price bucket x side (all days): EV at the outcome")
g = f.groupby(['bucket', 'our_side'], observed=True).agg(shares=('qty', 'sum'), ev=('ev', 'sum'))
g['c_per_share'] = 100 * g.ev / g.shares
print(g.round(2).to_string())
print("\ntoward-Polymarket fills vs away (all days):")
print(f.groupby('toward').agg(shares=('qty', 'sum'), ev=('ev', 'sum')).round(0).to_string())
x = f[f.t >= '2026-10-03T22:00']
print(f"\n3 Oct 22:00-22:47: {len(x)} fills, {x.qty.sum():,.0f} shares, EV at the outcome {x.ev.sum():+,.0f} "
      f"({100*x.ev.sum()/max(x.qty.sum(),1):+.2f}c/share)")
print(x.groupby(['label', 'our_side']).agg(shares=('qty', 'sum'), px=('fill_price', 'mean'), p=('p', 'first'), ev=('ev', 'sum'))
      .sort_values('ev').round(3).to_string())
x = f[f.t >= '2026-10-03T00:00']
h = x.groupby(x.t.dt.hour).agg(shares=('qty', 'sum'), ev=('ev', 'sum'))
print("\n3 Oct by hour (UTC): shares, EV at the outcome"); print(h.round(0).T.to_string())
# journal: the quotes resting in the last cycle and the take / fill lines 22:00-22:47
lines = [l for l in gzip.open('/home/claude/snap03/journal_2026-10-02_2037_to_now.log.gz', 'rt', errors='ignore')
         if l.startswith('2026-10-03T22:')]
kinds = {'FILL': 0, 'TAKE': 0, 'REDUCE-ONLY': 0, 'Insufficient': 0, 'PAIR UNWIND': 0, 'ARBITRAGE': 0, 'refused': 0}
for l in lines:
    for k in kinds:
        if k in l:
            kinds[k] += 1
print("\njournal 22:00-22:47 line counts:", kinds)
q = [l for l in lines if ' fv ' in l and '| bid' in l]
last = {}
for l in q:
    m = re.search(r'INFO\s+(.+?)\s+fv ([\d.]+) \(ref ([\d.]+|-+)\) inv\s+([-+\d]+).*\| bid\s+(\S+)(?: x(\d+))?\s+ask\s+(\S+)(?: x(\d+))?', l)
    if m:
        last[m.group(1).strip()] = m.groups()
both = sum(1 for v in last.values() if v[4] != '-' and v[6] != '-')
bo = sum(1 for v in last.values() if v[4] != '-' and v[6] == '-'); ao = sum(1 for v in last.values() if v[4] == '-' and v[6] != '-')
print(f"quote lines 22:00-22:47: {len(q)}; markets seen {len(last)}: two-sided {both}, bid only {bo}, ask only {ao}, none {len(last)-both-bo-ao}")

# ---- 3 Oct: takes vs maker fills (a TAKE journal line for the same market and price within 30 s marks the fill as a take) ----
tk = []
for l in gzip.open('/home/claude/snap03/journal_2026-10-02_2037_to_now.log.gz', 'rt', errors='ignore'):
    if 'WARNING TAKE ' in l:
        m = re.search(r'^(\S+) .*TAKE (.+?): Polymarket ([\d.]+) vs stale (bid|ask) ([\d.]+) -> (selling|buying) (\d+) YES at ([\d.]+)', l)
        if m:
            tk.append(dict(t=pd.to_datetime(m.group(1)), label=m.group(2), price=float(m.group(8))))
tk = pd.DataFrame(tk)
x = f[(f.t >= '2026-10-02T20:37') & (f.t < '2026-10-04')].copy()
x['is_take'] = False
tz = x.t.dt.tz_localize(None) if x.t.dt.tz is not None else x.t
for i, r in tk.iterrows():
    tt = r.t.tz_localize(None) if r.t.tzinfo is not None else r.t
    m = (x.label == r.label) & (np.abs(x.fill_price - r.price) < 0.0051) & ((tz - tt).dt.total_seconds().between(-5, 30))
    x.loc[m, 'is_take'] = True
print(f"\n2 Oct 20:37 - 3 Oct 22:47: {len(tk)} TAKE lines; fills matched to a take {int(x.is_take.sum())}")
g = x.groupby(['is_take', 'toward']).agg(fills=('qty', 'size'), shares=('qty', 'sum'), ev=('ev', 'sum'), cap=('cap', 'sum'))
g['c_per_share'] = 100 * g.ev / g.shares; g['ev_per_cap'] = g.ev / g.cap
print(g.round(3).to_string())
mk = x[~x.is_take]
tail_val = ((mk.fill_price >= 0.85) & (mk.our_side == 'bid')) | ((mk.fill_price <= 0.15) & (mk.our_side == 'ask'))
tail_bad = ((mk.fill_price >= 0.85) & (mk.our_side == 'ask')) | ((mk.fill_price <= 0.15) & (mk.our_side == 'bid'))
mid_ = ~tail_val & ~tail_bad
for nm, m in [('tail value side (fav bids, longshot asks)', tail_val), ('tail against side (fav asks, longshot bids)', tail_bad), ('middle 15-85c', mid_)]:
    print(f"  maker {nm:44s}: {mk[m].qty.sum():8,.0f} shares, EV at the outcome {mk[m].ev.sum():+7,.0f} "
          f"({100*mk[m].ev.sum()/max(mk[m].qty.sum(),1):+.2f}c/share, {mk[m].ev.sum()/max(mk[m].cap.sum(),1):+.1%} per $ of capital)")
