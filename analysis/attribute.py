"""Infer side, quote and fair value for fills the bot never matched (our_side '?'), from the
journal's quote lines for that market: the last logged quote at or before the fill (within 35 min,
the order expiry) whose bid equals fill_price (-> bid) or whose ask equals fill_price or 1-fill_price (-> ask)."""
import numpy as np, pandas as pd
from load import fills, quotes, snapshots


def label_map():
    s = snapshots()
    return s.drop_duplicates("eid").set_index("eid")["label"].to_dict()


def attributed():
    f, q, lab = fills(), quotes(), label_map()
    f["label"] = f.exchange_id.astype(str).map(lab)
    f["inferred"] = False
    qs = {k: g.sort_values("ts") for k, g in q.groupby("label")}
    for i, r in f[f.our_side == "?"].iterrows():
        g = qs.get(r.label)
        if g is None:
            continue
        g = g[(g.ts <= r.filled_at + pd.Timedelta(seconds=60)) & (g.ts >= r.filled_at - pd.Timedelta(minutes=35))].iloc[::-1]
        for _, x in g.iterrows():
            p = r.fill_price
            if x.bid is not None and np.isclose(x.bid, p):
                f.loc[i, ["our_side", "quote_price", "fv_at_quote", "inferred"]] = ["bid", x.bid, x.fv, True]; break
            if x.ask is not None and (np.isclose(x.ask, p) or np.isclose(1 - x.ask, p)):
                f.loc[i, ["our_side", "quote_price", "fv_at_quote", "inferred"]] = ["ask", x.ask, x.fv, True]; break
    # YES-equivalent price paid/received per share
    f["yes_px"] = np.where(f.our_side == "ask", f.quote_price, f.fill_price)
    f["sgn"] = f.our_side.map({"bid": 1, "ask": -1})
    return f
