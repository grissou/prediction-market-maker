"""J_carry_mm: VALUE market making at the OUTCOME, per day (Package 10, analyst J). Read-only on /home/claude/snap03.
Every fill valued at a probability p: bid (bought YES) earns qty*(p - price), ask (sold YES) qty*(price - p).
p variants: 'final' = latest Polymarket reference race-scaled (H_outcome.load p0); 'fill' = the reference at fill time
(race-scaled in that snapshot); 'fill24' = reference at fill time + 24 h (latest if beyond the data); each also with H's
calibration k=0.9 (p' = logistic(0.9 logit p), re-normalised per race: favourites less likely).
Classification: order_notes.json take / arb flags (cover 2 Oct 22:52 on) -> take / arb; our_side '?' (no quote record,
no quote_price: not one of our tracked quotes) -> 'untracked' (side inferred from the positions table where possible);
the rest (our_side bid/ask with a quote_price) -> maker. NO-price rows mapped back to YES as in I_mmval.py.
Usage: python3 analysis/p10/J_carry_mm.py"""
import sys, os, json, sqlite3
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from H_outcome import load, calibrate
S = '/home/claude/snap03'
d, cash, st = load()
d['eid'] = d.eid.astype(str)
c = sqlite3.connect(S + '/md.sqlite')
f = pd.read_csv(S + '/fills.csv')
f['eid'] = f.exchange_id.astype(str); f['oid'] = f.order_id.astype(str)
f['t'] = pd.to_datetime(f.filled_at).dt.tz_localize(None)
n = pd.DataFrame.from_dict(json.load(open(S + '/order_notes.json')), orient='index')
f = f.join(n[['take', 'arb']].rename(columns={'take': 'tk', 'arb': 'ab'}), on='oid')
f['cls'] = np.where(f.tk.notna(), 'take', np.where(f.ab.notna(), 'arb', np.where(f.our_side == '?', 'untracked', 'maker')))
DI = d.set_index('eid')
f['label'] = f.eid.map(DI.label); f['race'] = f.eid.map(DI.race)
f['p_final'] = f.eid.map(DI.p0)
d9 = d.copy(); d9['p0k'] = calibrate(d, 0.9); f['p_final_k'] = f.eid.map(d9.set_index('eid').p0k)

# ---- reference history, race-scaled per snapshot ----
s = pd.read_sql("select ts,eid,label,reference,best_bid,best_ask from snapshots", c)
s['eid'] = s.eid.astype(str); s['t'] = pd.to_datetime(s.ts).dt.tz_localize(None)
s['race'] = s.label.str.split(n=1).str[1]
s['mid'] = (s.best_bid + s.best_ask) / 2
s['r'] = s.reference.fillna(s.mid).clip(0.0005, 0.9995)
s['r'] = s.r / s.groupby(['ts', 'race']).r.transform('sum')
lg = s.r.clip(1e-4, 1 - 1e-4); q = 1 / (1 + np.exp(-0.9 * np.log(lg / (1 - lg))))
s['rk'] = q / q.groupby([s.ts, s.race]).transform('sum')
s = s.sort_values('t')
def at(times, col):
    out = pd.Series(np.nan, index=times.index)
    tmp = pd.DataFrame({'t': times, 'eid': f.loc[times.index, 'eid'], 'i': times.index}).sort_values('t')
    m = pd.merge_asof(tmp, s[['t', 'eid', col, 'mid']].rename(columns={'mid': 'smid'}), on='t', by='eid', direction='backward')
    out.loc[m.i.values] = m[col].values
    return out, pd.Series(m.smid.values, index=m.i.values)
f['p_fill'], f['smid'] = at(f.t, 'r')
f['p_fill_k'], _ = at(f.t, 'rk')
tl = s.t.max()
t24 = (f.t + pd.Timedelta(hours=24)).clip(upper=tl)
f['p_24'], _ = at(t24, 'r'); f['p_24_k'], _ = at(t24, 'rk')
f = f[f.p_final.notna()].copy()

# ---- NO-price rows back to YES ----
ref = f.quote_price.where(f.quote_price.notna(), f.smid)
flip = (np.abs(1 - f.fill_price - ref) + 0.05 < np.abs(f.fill_price - ref))
f.loc[flip, 'fill_price'] = 1 - f.loc[flip, 'fill_price']

# ---- side of untracked fills from the positions table (quantity change around the fill) ----
pos = pd.read_sql("select ts,eid,quantity,current_price from positions", c)
pos['eid'] = pos.eid.astype(str); pos['t'] = pd.to_datetime(pos.ts, unit='s')
pos = pos.sort_values(['eid', 't']); pos['dq'] = pos.groupby('eid').quantity.diff()
u = f[f.our_side == '?']
side = {}
pp = pos[pos.dq.notna() & (pos.dq != 0)]
for i, r in u.iterrows():
    w = pp[(pp.eid == r.eid) & (pp.t >= r.t - pd.Timedelta(seconds=5)) & (pp.t <= r.t + pd.Timedelta(seconds=180))]
    if len(w):
        side[i] = 'bid' if w.dq.iloc[0] > 0 else 'ask'
f['side'] = f.our_side.where(f.our_side != '?', pd.Series(side)).fillna('?')
print(f"positions table from {pos.t.min()}; untracked fills: {len(u)}, side inferred {len(side)}; NO-price rows flipped {int(flip.sum())}")

f['sgn'] = np.where(f.side == 'bid', 1, np.where(f.side == 'ask', -1, 0))
P = ['p_final', 'p_fill', 'p_24', 'p_final_k', 'p_fill_k', 'p_24_k']
for p in P:
    f['ev_' + p] = f.sgn * (f[p] - f.fill_price) * f.qty
f['cash'] = np.where(f.side == 'bid', f.fill_price, 1 - f.fill_price) * f.qty * (f.sgn != 0)
fp = f.fill_price
f['sc'] = np.select([(fp >= .85) & (f.side == 'bid'), (fp <= .15) & (f.side == 'ask'),
                     (fp >= .85) & (f.side == 'ask'), (fp <= .15) & (f.side == 'bid'), f.sgn != 0],
                    ['value', 'value', 'anti', 'anti', 'middle'], 'noside')
f['day'] = f.t.dt.strftime('%m-%d')
f.to_pickle('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/J_fills.pkl')

def tab(x, by):
    g = x.groupby(by).agg(fills=('qty', 'size'), shares=('qty', 'sum'), cash=('cash', 'sum'),
                          **{e: ('ev_' + e, 'sum') for e in P})
    g['c/sh'] = 100 * g.p_final / g.shares; g['ev/$'] = g.p_final / g.cash
    return g
pd.set_option('display.width', 250)
print("\nfills by day x class x side-class (EV at the outcome under each p; cash = buys px*q, shorts (1-px)*q)")
g = tab(f, ['day', 'cls', 'sc']); print(g.round(2).to_string())
print("\nall days by class x side-class"); print(tab(f, ['cls', 'sc']).round(2).to_string())
mk = f[f.cls == 'maker']
vm = mk[mk.sc.isin(['value', 'middle'])]
print("\nVALUE-MODE rule (maker value sides in tails + middle two-way) by day"); g = tab(vm, 'day'); print(g.round(3).to_string())
print("\nmaker value sides only by day"); print(tab(mk[mk.sc == 'value'], 'day').round(3).to_string())
print("\nmaker middle only by day"); print(tab(mk[mk.sc == 'middle'], 'day').round(3).to_string())
print("\nmaker anti sides by day"); print(tab(mk[mk.sc == 'anti'], 'day').round(3).to_string())
print("\nall maker by day"); print(tab(mk, 'day').round(3).to_string())
v = mk[mk.sc == 'value']
tops = v.groupby('label').ev_p_final.sum().sort_values(ascending=False)
print("\nmaker value-side EV by market: top 10", tops.head(10).round(0).to_dict())
print(f"top-10 share {tops.head(10).sum():,.0f} of {tops.sum():,.0f}; markets with value fills {len(tops)}; bottom 5 {tops.tail(5).round(0).to_dict()}")
hl = v.label.str.contains('U.S. House|U.S. Senate')
print(f"headline (U.S. House/Senate control) legs value EV {v[hl].ev_p_final.sum():+,.0f} on {v[hl].qty.sum():,.0f} sh, cash {v[hl].cash.sum():,.0f}; rest {v[~hl].ev_p_final.sum():+,.0f}")
print(v[hl].groupby(['day', 'label']).agg(sh=('qty', 'sum'), ev=('ev_p_final', 'sum'), cash=('cash', 'sum'), px=('fill_price', 'mean')).round(2).to_string())
top10 = v.label.isin(tops.head(10).index)
print(tab(v.assign(grp=np.where(hl, 'headline', np.where(top10, 'top10_other', 'rest'))), ['day', 'grp']).round(2).to_string())
# median p minus price by side class for maker
print("\nmaker: mean edge c/share by side class and p")
print((100 * mk.groupby('sc')[['ev_' + p for p in P]].sum().div(mk.groupby('sc').qty.sum(), axis=0)).round(2).to_string())

# ---- exchange flow proxies: mark steps from positions.current_price, snapshot best bid/ask changes ----
pos['day'] = pos.t.dt.strftime('%m-%d'); pos['dp'] = pos.groupby('eid').current_price.diff()
fl = pos[pos.dp.notna()].groupby('day').agg(pos_rows=('eid', 'size'), mark_steps=('dp', lambda x: int((x.abs() > 1e-9).sum())),
                                              mark_steps_1c=('dp', lambda x: int((x.abs() >= 0.01).sum())), eids=('eid', 'nunique'))
s['day'] = s.t.dt.strftime('%m-%d'); s = s.sort_values(['eid', 't'])
s['bbch'] = s.groupby('eid').best_bid.diff().fillna(0).ne(0) | s.groupby('eid').best_ask.diff().fillna(0).ne(0)
sb = s.groupby('day').agg(snap_cycles=('ts', 'nunique'), book_top_changes=('bbch', 'sum'))
print("\nflow proxies by day"); print(fl.join(sb, how='outer').to_string())
print("\nfills per day (all) and our shares:", f.groupby('day').qty.agg(['size', 'sum']).to_dict())
