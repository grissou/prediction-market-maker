# Field activation: fewer than ~20% of participants have traded — what it does to the leaderboard and to prices (4 Oct 17:00 UTC)

## Facts (snap04)
- "Rank N of M": M 1,044 (00:00) -> 1,087 (14:00): ~2.7 new accounts/h today (Sunday) vs ~6/h on 3 Oct. Smart Score denominator 544 of
  1,087 have a score; the owner's count says < 20% have traded.
- The marginal price of a longshot is set by new buyers: average best bid on contracts with Polymarket < 5c (Polymarket itself flat at
  ~2.4c): 2.35c (28 Sep) -> 3.0c (1 Oct) -> 4.9c (2 Oct) -> 7.4c (3 Oct) -> 9.9c (4 Oct): ~+2.5c a day and not slowing in cents; s 0.017 ->
  0.14. Favourites mirror it (0.86-0.92 for 0.975 events) because race sums are held near 1.00-1.03 by the few arbitrageurs.
- Supply of longshot selling is thin and capital-bound: shorting a 10c longshot locks 0.90 of collateral for 7-8c of edge; the few value /
  market-making accounts (us included) hit their risk caps (we were pinned by the backstop twice today). Longshot bid depth is deep
  (bid sums > 1 in 82 of 117 races), the ask side thin.
- Leaderboard at the outcome (Z_board20.py, inactive accounts at 100k included): with 20% active, place 50 needs ~110k and place 10 ~181k;
  our book (E 108.9k) is a coin-flip for the top 50 and 87% for the top 100, but 12.5% to finish below 100k = behind every inactive account.

## What it likely does to prices over the next 2-4 weeks
1. **The tilt has 4x the fuel left.** The 20% who traded moved longshots from 2.4c to 9.9c. The other 80% are a reservoir; the contest
   structure (3 prizes, a visible leaderboard, a leader at +600% on marks, 871 accounts stuck at 100k) rewards exactly the longshot lottery
   that produced the leader, and activation tends to come late (the election approaching, campus deadlines, the leaderboard as social
   proof). If even a quarter of them join and buy like the first 20%, longshots go to 15-25c (s 0.3-0.5) by late October; favourites to
   0.75-0.85 for 0.97 events. The literature (Page & Clemen; Restocchi: the last-24-h jump; Betfair in-play) says the bias grows into
   the close when nothing forces convergence before resolution - and here nothing does.
2. **Momentum, not mean reversion, until the close.** Early longshot buyers look like winners on marks; every new entrant sees that.
   The only reversal mechanisms are (a) longshot holders taking profits on marks (the leader hedging), (b) SIG intervention, (c) the
   tilt running out of buyers (B: cheap-side bid depth halved in 30 h on 3 Oct, then refilled on 4 Oct). Assign ~20-30% to a plateau at
   s 0.15-0.2 and ~70% to further growth; a collapse before the close is the tail.
3. **Marks vs value diverge further.** Our book marks lower every day the tilt rises (rank 181 -> 573 today) while its outcome value
   is unchanged; the leaderboard will show us falling for weeks. That is a presentation problem, not a P&L one - unless a mark-based
   rule sells (the value-mode guard and `risk_unheld_legs` are what keep that from happening).
4. **Edge for value buyers rises with time, cash does not.** Favourites at 0.85 are 15% per $; at 0.78 (s 0.3) they are 25%. The
   same $ buys more edge later IF the tilt keeps growing - the timing trade-off: 5-10% now vs 15-30% in 2-3 weeks with ~70% probability.
5. **Dutch books widen.** Bid sums of 1.02-1.05 become common as longshot bids fatten faster than favourite bids fall: riskless set
   selling (hold NO on every leg) earns 2-5c a set, uses no worst-case room, and is the natural home for idle cash and the reserve.
6. **Middle-band flow grows with activation** (a third of active accounts trade competitive / control contracts): the market-making
   carry that is ~0 today should improve in late October, when it also matters most for the risk-reserve design.
7. **Election day / night activation.** Dormant accounts are most likely to wake on 3 Nov. If SIG keeps trading open to 12:00 pm ET on
   4 Nov, that is the largest flow (and stale-quote) event of the tournament; if it closes at 00:00 UTC, the last 24 h of 3 Nov will see
   the biggest marks jump (Restocchi) and the thinnest books.

## What follows for us
- Keep RISK room and dry powder, not just cash: the new `mm_risk_reserve_wc/_corr` for the market maker, plus 10-15k of value cash
  released in tranches keyed to `tilt_s` (0.16 / 0.18 / 0.20 and the last 72 h) - the edge is likely to be 2-3x richer later.
- Sell longshots as a MAKER above the market (asks at +2c / +4c, the set-ladder idea generalised to any rich longshot within the bloc and
  risk caps): the tilt lifts them to us; 11-22% per $ of collateral vs 10-15% on favourites.
- Riskless sets for idle cash and as backstop-free collateral (bid sums > 1 in 82 races).
- Never sell value to marks; expect the rank to fall for weeks and say so in the summary line (EV outcome vs marks).
- The 100k line is the rank floor: a losing digital drops us behind 871 accounts; size any digital to P(final < 100k) small, not to 85k.
- Watch two numbers daily: the active share (Smart Score denominator / "of M") and the sub-5c longshot bid average; if activation jumps
  (weekday mornings, campus events), the top-50 bar moves up ~8-10k per 10 points of active share.
