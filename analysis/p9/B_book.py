"""B_book: latest-book checks for a long-tilt basket (explorer B).
(1) Two routes to the same exposure in a binary race: buy the longshot YES at its ask, or sell the favourite YES at its bid
    (= buy favourite NO at 1 - bid). Which is cheaper, by how much (cents and % of price)?
(2) Unwind capacity: for cheap sides (longshot YES with ref < 10c, plus favourite NO via the favourite's YES ask side) the top-3
    bid depth in shares and $, and the $ that can be sold before the price falls 1c / 2c (top-3 levels only).
(3) Entry capacity: top-3 ask depth in $ on the same contracts; spread as % of the price.
Run: python3 analysis/p9/B_book.py"""
import sqlite3, json
import numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
s = pd.read_sql("select ts,eid,label,best_bid bb,best_ask ba,reference ref from snapshots where mode='live' and ts>=(select max(ts) from snapshots where mode='live')", con)
lab = pd.read_sql("select distinct eid,label from snapshots", con); lab['race'] = lab.label.str.split(n=1).str[1]
nl = lab.groupby('race').eid.nunique()
s['race'] = s.label.str.split(n=1).str[1]; s['legs'] = s.race.map(nl)
b = pd.read_sql("select b.eid,b.bids,b.asks from books b join (select eid,max(ts) mt from books group by eid) m on b.eid=m.eid and b.ts=m.mt", con)
s = s.merge(b, on='eid', how='left')
two = s[s.legs == 2].dropna(subset=['bb', 'ba', 'ref'])
rows = []
for race, z in two.groupby('race'):
    if len(z) != 2: continue
    z = z.sort_values('ref'); L, F = z.iloc[0], z.iloc[1]
    if L.ref >= 0.10: continue
    via_yes = L.ba; via_no = 1 - F.bb
    rows.append((race, L.ref, L.bb, L.ba, F.bb, F.ba, via_yes, via_no))
r = pd.DataFrame(rows, columns=['race', 'ref_L', 'bid_L', 'ask_L', 'bid_F', 'ask_F', 'buy_via_L', 'buy_via_F'])
r['saving_c'] = 100 * (r.buy_via_L - r.buy_via_F)
r['exit_via_L'] = r.bid_L; r['exit_via_F'] = 1 - r.ask_F
r['exit_gain_c'] = 100 * (r.exit_via_F - r.exit_via_L)
r['best_in'] = r[['buy_via_L', 'buy_via_F']].min(axis=1); r['best_out'] = r[['exit_via_L', 'exit_via_F']].max(axis=1)
r['rt_pct'] = 100 * (r.best_in - r.best_out) / r.best_in
r['rt_pct_L'] = 100 * (r.ask_L - r.bid_L) / r.ask_L
print("(1) binary races with a longshot ref < 10c: n %d" % len(r))
print("entry: favourite route cheaper in %d, longshot route cheaper in %d, equal %d; mean saving when cheaper %.2fc (%.1f%% of price)" % (
    (r.saving_c > 0).sum(), (r.saving_c < 0).sum(), (r.saving_c == 0).sum(), r.saving_c.abs()[r.saving_c != 0].mean(),
    (r.saving_c.abs() / r.best_in)[r.saving_c != 0].mean()))
print("exit: favourite route better in %d, longshot route better in %d" % ((r.exit_gain_c > 0).sum(), (r.exit_gain_c < 0).sum()))
print("round trip at best route: median %.1f%% of price (longshot book only %.1f%%)" % (r.rt_pct.median(), r.rt_pct_L.median()))
print(r.sort_values('saving_c', ascending=False).head(8)[['race', 'ref_L', 'bid_L', 'ask_L', 'bid_F', 'ask_F', 'saving_c', 'exit_gain_c']].round(3).to_string(index=False))


def lv(js):
    try:
        return [(float(p), float(q)) for p, q in json.loads(js)]
    except Exception:
        return []


cheap = s[(s.ref < 0.10)].dropna(subset=['bids'])
tot = {'bid_sh': 0, 'bid_usd': 0, 'ask_sh': 0, 'ask_usd': 0, 'sell_1c': 0, 'sell_2c': 0, 'buy_1c': 0}
for x in cheap.itertuples():
    bids, asks = lv(x.bids), lv(x.asks)
    if not bids or not asks: continue
    tot['bid_sh'] += sum(q for p, q in bids); tot['bid_usd'] += sum(p * q for p, q in bids)
    tot['ask_sh'] += sum(q for p, q in asks); tot['ask_usd'] += sum(p * q for p, q in asks)
    tot['sell_1c'] += sum(p * q for p, q in bids if p >= bids[0][0] - 0.01 + 1e-9)
    tot['sell_2c'] += sum(p * q for p, q in bids if p >= bids[0][0] - 0.02 + 1e-9)
    tot['buy_1c'] += sum(p * q for p, q in asks if p <= asks[0][0] + 0.01 - 1e-9)
print("\n(2)/(3) contracts with ref < 10c (n %d): top-3 bid %.2fM sh / $%.0fk; ask %.2fM sh / $%.0fk" % (
    len(cheap), tot['bid_sh'] / 1e6, tot['bid_usd'] / 1e3, tot['ask_sh'] / 1e6, tot['ask_usd'] / 1e3))
print("$ sellable within 1c of best bid %.0fk, within 2c %.0fk; $ buyable within 1c of best ask %.0fk" % (tot['sell_1c'] / 1e3, tot['sell_2c'] / 1e3, tot['buy_1c'] / 1e3))
cheap = cheap.assign(mid=(cheap.bb + cheap.ba) / 2, spr=lambda d: 100 * (d.ba - d.bb) / ((d.ba + d.bb) / 2))
print("spread as %% of mid on ref<10c: median %.1f%%, IQR %.1f-%.1f%%; median mid %.3f" % (cheap.spr.median(), cheap.spr.quantile(.25), cheap.spr.quantile(.75), cheap.mid.median()))
