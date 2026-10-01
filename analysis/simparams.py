"""Section 8: parameters for Run C's simulator."""
import os, numpy as np, pandas as pd
from load import snapshots
SNAP = os.environ.get("SNAP", "snap")
s = snapshots(); s = s[s.ts >= "2026-10-01T16:05"].sort_values(["label", "ts"]).copy()
s["mid"] = (s.best_bid + s.best_ask) / 2
s["dt"] = s.groupby("label").ts.diff().dt.total_seconds()
s["dref"] = s.groupby("label").reference.diff()
d = s.dref.dropna(); mins = s.dt.dropna().mean() / 60
print("snapshot interval mean %.1f min" % mins)
print("Polymarket ref change per snapshot: share 0 %.2f, sd %.2fc, |d|>=1c %.3f, >=2c %.4f, >=3c %.4f, >=5c %.5f" % (
    (d == 0).mean(), 100 * d.std(), (d.abs() >= .0099).mean(), (d.abs() >= .0199).mean(), (d.abs() >= .0299).mean(), (d.abs() >= .0499).mean()))
mh = len(d) * mins / 60
print("PM jumps per market-hour: >=1c %.3f, >=2c %.3f, >=3c %.4f  (market-hours %.0f)" % ((d.abs() >= .0099).sum() / mh, (d.abs() >= .0199).sum() / mh, (d.abs() >= .0299).sum() / mh, mh))
ok = s.best_bid.notna() & s.best_ask.notna() & ((s.best_ask - s.best_bid) <= .05) & s.reference.notna()
s["gap"] = np.where(ok, s.mid - s.reference, np.nan)
s["gap_prev"] = s.groupby("label").gap.shift()
x = s.dropna(subset=["gap", "gap_prev"])
rho = np.corrcoef(x.gap, x.gap_prev)[0, 1]
print("tournament-PM gap: sd %.2fc, lag-1 autocorr %.2f -> half-life %.1f min" % (100 * s.gap.std(), rho, mins * np.log(.5) / np.log(rho)))
s["dmid"] = s.groupby("label").mid.diff()
print("tournament mid change per snapshot: sd %.2fc; |d|>=2c %.3f" % (100 * s.dmid.std(), (s.dmid.abs() >= .0199).mean()))
sp = (s.best_ask - s.best_bid)[s.best_bid.notna() & s.best_ask.notna()]
print("spread quantiles (c):", (100 * sp.quantile([.1, .25, .5, .75, .9])).round(1).to_dict())
f = pd.read_pickle(os.path.join(SNAP, "f_mk.pkl"))
hours = (f.filled_at.max() - f.filled_at.min()).total_seconds() / 3600
print("fills/h %.0f, shares/h %.0f; fill size quantiles:" % (len(f) / hours, f.qty.sum() / hours), f.qty.quantile([.25, .5, .75, .9, .99]).to_dict())
for c, g in f.groupby("cls"):
    print(f"  {c}: fills/h {len(g)/hours:.1f}, markets {g.label.nunique()}, median size {g.qty.median():.0f}")
ok = f.mk15_mid.notna()
adv = (f.sgn * (f.mk15_mid - f.yes_px))[ok]
print("informed share (15m mid moves >=1c against fill price): %.2f; >=2c: %.2f; favourable >=1c: %.2f" % ((adv <= -.0099).mean(), (adv <= -.0199).mean(), (adv >= .0099).mean()))
pq = f.groupby("label").size()
print("fills per market quantiles:", pq.quantile([.5, .75, .9]).to_dict(), "markets with fills", len(pq))
# split gap into per-market persistent bias + transient
s["bias"] = s.groupby("label").gap.transform("mean"); s["tr"] = s.gap - s.bias
s["tr_prev"] = s.groupby("label").tr.shift(); y = s.dropna(subset=["tr", "tr_prev"])
r2 = np.corrcoef(y.tr, y.tr_prev)[0, 1]
print("per-market bias sd %.2fc; transient sd %.2fc, lag-1 autocorr %.2f -> half-life %.1f min" % (
    100 * s.drop_duplicates("label").bias.std(), 100 * s.tr.std(), r2, mins * np.log(.5) / np.log(r2)))
print("tournament mid change robust sd (1.4826*MAD) %.2fc" % (100 * 1.4826 * (s.dmid - s.dmid.median()).abs().median()))
