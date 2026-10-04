# Package 11 literature sweep (owner, 4 Oct ~10:00 UTC: "spend 5 hours reading as many different papers and online resources as
# possible for any ideas"). Readers write `analysis/p11/lit_<theme>.md`; the executor synthesises `analysis/p11/LIT_REVIEW.md`.

## Our situation, in one paragraph (so every idea is judged against it)
We run a market-making bot in the SIG Predictions Cup: play money, 100k start, ~1,040 players, 237 binary contracts across 117 US midterm
races (state Senate/Governor/House seats by party, plus U.S. House and U.S. Senate control), REST + realtime feed, one write = one batch of
10 orders, 28 writes/min, 0.5c tick, settlement AT THE OUTCOME (SIG pays positions out at the election result; markets close 4 Nov 00:00
UTC, before results). The tournament prices show a growing favourite-longshot tilt vs Polymarket (tournament mid ~ c + (1 - s)(Polymarket
- c), c = 1/legs, s 1.7% (1 Oct) -> 12.6% (3 Oct) -> ~14% (4 Oct)): longshots are 4-12c rich, favourites cheap. The leader (+600% on
marks) is long that tilt; at the outcome those books settle at ~28k. We hold ~100k of toward-Polymarket positions (E[final] 109k on
Polymarket probabilities with a national factor rho 0.45; P(>= 150k) 0%), ~0 free cash, 21.9k in riskless NO+NO sets. Our edge per $
of collateral when buying favourites / shorting longshots is ~5-10% once; two-way market making in the 15-85c middle made ~1.5k on the
one funded day. The leaderboard at the outcome: top 50 ~133k, top 10 ~197k. Packages 5-10 built: tilt-corrected reference, cash gate,
set-aware covered sales, pair unwinds, a long-tilt basket (now wrong), a value-mode guard (never sell below Polymarket), a bloc-delta risk
cap, an hourly capital allocator by edge per $, value market making (favourite bids / longshot asks in the tails, two-way middle).
Rules: fair play (no manipulation, wash trades, self-marking), the write budget, nothing that trades to move a mark. The owner wants
P(final >= 150k) first, P(>= 120k), E[final], P(<= 85k) < 10%; "ideas outside the risk limit will be considered".
Repo: /home/claude/prediction-market-maker (read-only for readers: never commit, never switch branches, never push); the live data
/home/claude/snap03 (see analysis/p9/BRIEF.md); earlier research analysis/p9/*, analysis/p10/* (ideas_A..I, SYNTHESIS, VALUE_PLAN).

## What a reader does
Use WebSearch / WebFetch widely: papers (arXiv, SSRN, JSTOR abstracts, NBER, journals), textbooks' public chapters, blogs of practitioners
(Kalshi / Polymarket / PredictIt / Metaculus / Manifold market makers, quant blogs), forum threads (r/algotrading, Manifold discussions,
Polymarket docs), tournament write-ups, SIG's own public material. Read at least 15 distinct sources on your theme; skim many more. For
each source: 2-4 lines of what it says that matters to us. Then IDEAS in this exact shape (>= 10 per theme; wild is fine if the mechanism
is clear):
```
### <theme>-<n>. <name>   [source: <author/site, year, url>]
What the source says: <2-3 lines>
Idea for us: <mechanism, 2-4 lines, in our terms: which quotes/orders/settings change>
Value: <E[final] / P(>= 150k) / P(<= 85k) direction and a rough size, with the reasoning> | Cost: <config / lines of code / data needed>
Check: <how to test it on our data (snap03) or simulator before building; or "untestable offline">
Fair play: <ok / where the line is>
```
End with your TOP 5 for our situation and a list of sources you could not access. Finish by 13:00 UTC. Do not run live_sim or test suites.
