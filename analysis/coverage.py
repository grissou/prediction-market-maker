"""Section 4: coverage by hour, reasons markets went unpriced, 409/429/cycle timeline.
Fair value needs a two-sided tournament book with >=200 shares each side within 30c (mm_bot.fair_value) and a
fresh book; Polymarket alone never prices a market."""
import os, numpy as np, pandas as pd
from load import snapshots, events
SNAP = os.environ.get("SNAP", "snap")
s = snapshots(); s = s[s.ts >= "2026-10-01T16:05"].copy()
s["h"] = s.ts.dt.floor("30min").dt.strftime("%H:%M")
two = s.best_bid.notna() & s.best_ask.notna()
s["state"] = np.select(
    [s.fair_value.notna() & (s.our_bid.notna() & s.our_ask.notna()), s.fair_value.notna() & (s.our_bid.notna() | s.our_ask.notna()),
     s.fair_value.notna(), ~two, (s.best_ask - s.best_bid) > 0.30],
    ["quoted 2-sided", "quoted 1-sided", "priced, not quoted", "UNPRICED: one-sided/empty top", "UNPRICED: spread>30c"],
    "UNPRICED: 2-sided top (thin<200sh or stale)")
n = s.ts.groupby(s.h).nunique()
t = s.groupby(["h", "state"]).size().unstack(fill_value=0).div(n, axis=0).round(0)
print(t.to_string())
u = s[s.fair_value.isna()]
print("unpriced rows with a Polymarket reference: %.2f" % u.reference.notna().mean())
last = s[s.ts == s.ts.max()]
print("at 20:18 unpriced markets with ref and 2-sided top:", ((last.fair_value.isna()) & last.reference.notna() & last.best_bid.notna() & last.best_ask.notna()).sum())
pos_unpriced = last[(last.position != 0) & last.fair_value.isna()]
print("positions held in unpriced markets at 20:18:", len(pos_unpriced), pos_unpriced[["label", "position"]].head(8).values.tolist())
e = events(); e["h"] = e.ts.dt.floor("30min").dt.strftime("%H:%M")
print(e[e.ts >= "2026-10-01T15:55"].groupby(["h", "kind"]).size().unstack(fill_value=0).to_string())
r = e[e.kind == "429"]; print(r.ts.dt.strftime("%H:%M:%S").tolist(), [x[-60:] for x in r.text])
