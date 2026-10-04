"""F_sides.py (explorer F): maker mark-outs by price bucket x our side (which side the tilt runs over), and fill sizes.
Needs F_flow.py's scratch pickle. Usage: python3 analysis/p10/F_sides.py"""
import pandas as pd
m = pd.read_pickle("/tmp/claude-0/-home-claude-prediction-market-maker/bf15e6d6-a160-5dda-ac11-580ebf7280fb/scratchpad/F_fills.pkl")
f = lambda x: pd.Series({"fills": len(x), "sh": x.qty.sum(), "c0": 100 * x.cap0.sum() / x.qty.sum(), "c1h": 100 * x.mo1.sum() / x.qty.sum(),
                         "c6h": 100 * x.mo6.sum() / x.qty.sum(), "usd6h": x.mo6.sum()})
print(m.groupby(["cheap", "our_side"]).apply(f).round(2).to_string())
print("fill size quantiles:", m.qty.quantile([.25, .5, .75, .9, .99]).to_dict())
print("share of fills at a round size (multiple of 50):", round(((m.qty % 50) == 0).mean(), 3))
m["hr"] = m.t.dt.hour
print(m.groupby(pd.cut(m.hr, [-1, 5, 11, 17, 23])).apply(f).round(2).to_string())
