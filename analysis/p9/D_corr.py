"""D_corr: are the cheap sides one bet? 6-hour log returns of cheap-side mids (ref < 0.10), 1-3 Oct: share of variance on the
first principal component, mean pairwise correlation, and the equal-weight basket's 6-h return sd vs a single name's.
Run: python3 analysis/p9/D_corr.py"""
import sqlite3, numpy as np, pandas as pd
con = sqlite3.connect('/home/claude/snap03/md.sqlite')
s = pd.read_sql("select ts,eid,best_bid,best_ask,reference from snapshots where ts>='2026-10-01'", con)
s['t'] = pd.to_datetime(s.ts).dt.floor('h'); s['mid'] = (s.best_bid + s.best_ask) / 2
s = s[s.reference < 0.10].dropna(subset=['mid'])
H = s.groupby(['t', 'eid']).mid.last().unstack().ffill()
for k in [1, 6]:
    R = np.log(H.iloc[::k]).diff().dropna(how='all').dropna(axis=1)
    C = R.corr().values; n = C.shape[0]
    ev = np.linalg.eigvalsh(np.cov(R.values.T))
    print('%dh returns: %d contracts x %d obs; mean pairwise corr %.2f; PC1 share %.2f; single-name sd %.3f, basket sd %.3f, basket mean %.3f'
          % (k, n, len(R), (C.sum() - n) / (n * n - n), ev[-1] / ev.sum(), R.std().median(), R.mean(axis=1).std(), R.mean(axis=1).mean()))
