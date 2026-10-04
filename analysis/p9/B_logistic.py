"""B_logistic: fit s(t) = K / (1 + exp(-r (t - t0))) to the hourly tilt (B_tilt.py output) and profile the ceiling K."""
import pandas as pd, numpy as np
from scipy.optimize import curve_fit
g = pd.read_csv('/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/B/tilt_hourly_B.csv', parse_dates=['h']).set_index('h')
f = lambda t, K, r, t0: K / (1 + np.exp(-r * (t - t0)))
for col in ['ols', 'wins', 'long_ols']:
    z = g[col].dropna(); t = np.asarray((z.index - z.index[0]).total_seconds() / 3600); y = z.values
    p, cov = curve_fit(f, t, y, p0=[0.3, 0.05, 60], maxfev=20000, bounds=([0.05, 0.001, -500], [5, 1, 2000]))
    se = np.sqrt(np.diag(cov)); res = y - f(t, *p); sl = p[1] * f(t[-1], *p) * (1 - f(t[-1], *p) / p[0])
    print("%-8s K %.3f (se %.3f) r %.4f/h t0 %.1f h; resid sd %.4f; slope at end %.5f/h; last %.3f" % (col, p[0], se[0], p[1], p[2], res.std(), sl, y[-1]))
    rss0 = (res ** 2).sum(); n = len(y)
    out = []
    for K in [0.13, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 2.0]:
        q, _ = curve_fit(lambda t, r, t0: K / (1 + np.exp(-r * (t - t0))), t, y, p0=[0.05, 60], maxfev=20000)
        rss = ((y - K / (1 + np.exp(-q[0] * (t - q[1])))) ** 2).sum()
        out.append("K%.2f: dAIC %+.1f" % (K, n * np.log(rss / rss0)))
    print("   profile:", "; ".join(out))
    lin = np.polyfit(t, y, 1); print("   linear rss ratio vs logistic: %.2f" % (((y - np.polyval(lin, t)) ** 2).sum() / rss0))
