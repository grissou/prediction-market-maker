"""D_spike: single-name pumps on cheap sides (ref < 0.10): hourly mids 1-3 Oct; a spike = the mid reaches >= 1.4x its value at t
within 6 h; give-back = (peak - mid 24 h after t) / (peak - mid at t). Run: python3 analysis/p9/D_spike.py"""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
s = pd.read_sql("select ts,eid,label,best_bid,best_ask,reference from snapshots where ts>='2026-10-01'", con)
s['t'] = pd.to_datetime(s.ts).dt.floor('h'); s['mid'] = (s.best_bid + s.best_ask) / 2
s = s[(s.reference < 0.10)].dropna(subset=['mid'])
H = s.groupby(['t', 'eid']).mid.last().unstack()
ev = []
for e in H.columns:
    x = H[e].dropna()
    i = 0
    while i < len(x) - 30:
        win = x.iloc[i + 1:i + 7]
        if win.max() >= 1.4 * x.iloc[i]:
            pk = win.max(); after = x.iloc[i + 24]
            ev.append((e, x.index[i], x.iloc[i], pk, after, (pk - after) / (pk - x.iloc[i])))
            i += 24
        else:
            i += 1
ev = pd.DataFrame(ev, columns=['eid', 't', 'm0', 'peak', 'm24', 'giveback'])
print('spikes >= 1.4x within 6 h: %d on %d contracts over %d h; median peak/m0 %.2f; give-back by +24 h median %.2f, share > 0.3: %.2f'
      % (len(ev), ev.eid.nunique(), len(H), (ev.peak / ev.m0).median(), ev.giveback.median(), (ev.giveback > 0.3).mean()))
print('selling at the peak vs holding to +24 h: median gain %.1f%% of the peak price' % (100 * ((ev.peak - ev.m24) / ev.peak).median()))
