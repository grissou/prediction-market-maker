# SPEC Package 12 (small items from the literature sweep; everything OFF by default, in OVERRIDABLE with ranges; house style;
# tests fail-before/pass-after; flags off byte-identical on a grid). Context: PLAN_P10.md (value mode: SIG pays at the OUTCOME),
# analysis/p11/LIT_REVIEW.md (A4, B1, F1, F5, F7), analysis/p10/SPEC_P10.md (Part B allocator), the Package 10 code.

## Part L (builder L): allocator items
L1 `alloc_set_rich_leg` (False): in value mode a NO+NO set's RICH leg is the NO on the favourite (NO_fav worth 1 - p_fav ~ 2c, priced ~7-12c
   on the tilted book). With the flag, the allocator ranks set LEGS individually: NO_fav's edge-held is strongly negative ((1 - p_fav) vs its
   bid), so it is a SALE candidate (a covered "sell NO" of the set part, which breaks the set: the remaining NO_longshot then needs its own
   collateral; the cash gate's set tier already prices that at 1.0 per share, conservative, so a sale needs cash_left >= qty x 1.0 -
   proceeds... use the gate's need functions as they are; if the gate refuses, the sale waits). The NO_longshot leg (worth ~0.975, priced
   ~0.90) is NEVER sold (edge-held positive). Instead of an IOC at the touch, the rich leg is sold as a RESTING LADDER (`alloc_set_ladder`
   "0,0.02,0.04": offsets above the current best bid for NO_fav in YES terms = asks on the longshot... careful with terms: selling NO_fav =
   buying YES_fav; in YES terms it is a BID on the favourite at (1 - NO price); the ladder sits at the favourite's best bid, +2c, +4c, i.e.
   NO prices at the book, -2c, -4c? NO: we want to sell NO_fav HIGH, i.e. buy YES_fav LOW: the ladder = YES bids on the favourite at the best
   bid, best bid - 2c, best bid - 4c? That sells NO at higher NO prices only if the bid is lifted... Think it through in the code comments:
   the retail flow BUYS longshot YES = sells favourite YES into our favourite bid. A favourite bid at the best bid fills at today's tilt;
   bids 2c and 4c lower fill only in a late spike. Default ladder "0,-0.02,-0.04" in YES terms, each leg sized 1/3 of the set part, as
   resting orders under `value_mode`'s floor rules and the covered-sale (`_no_sell`) path; never crossing our own asks; re-quoted at most
   once an hour (queue position matters). Status "alloc.set_ladder" {races, shares resting, filled}. Tests >= 30.
L2 `alloc_prefer_short` (False): when choosing the route for the same race exposure, if the race's best bids sum > 1 (own quotes excluded)
   prefer SELLING the longshot YES (short) over buying the favourite YES: same exposure, better price, less cash (CMP-1). Tests >= 10.
L3 `pair_no_unwind_asks_le1` (False): the NO+NO pair unwind (`pair_no_unwind_max_cost`) fires only while the race's best asks sum <= 1 +
   `pair_no_unwind_max_cost` AND the best bids do NOT sum > 1 (if they do, the set is worth more sold leg by leg: L1). Tests >= 8.

## Part M (builder M): quoting items
M1 `close_override_utc` (string "", OVERRIDABLE as a date string like `basket_exit_utc`): when set, every market's effective close for
   `hours_to_close` / `stop_minutes_before_close` / the pre-close windows is max(API close, this time) — so if SIG confirms trading to 12:00 pm
   ET on 4 Nov the owner sets "2026-11-04T17:00:00Z" and the bot does not stop itself at 23:45 UTC on 3 Nov. Also make
   `stop_minutes_before_close` OVERRIDABLE (range 0-120). NOTHING else changes (no election-night taking: that waits for SIG's answer).
   Validate the string; invalid = ignored with an alert once. Tests >= 15 (hours_to_close, the stop, the windows, validate_overrides).
M2 `skew_target_inventory` (False): in `compute_quote` the inventory skew is measured from (inv - target) instead of inv, where target = the
   allocator's wanted holding for this market when `alloc_enabled` provides one (`Bot.alloc_target_for(eid)` -> shares or None; add that
   accessor to the allocator: the latest plan's intended holding, else the current holding when the position has edge-held > 0 in value
   mode, else 0), so a +EV favourite we want to hold is not quoted as a position to unload; `age_skew` is 0 for holdings with edge-held >
   0 while `value_mode` is on. Document that this is the Bergault-Guéant / Fodra-Labadie "informed market maker" skew. Tests >= 25
   (flag off identical; target from the allocator; fallback; age skew off on +EV holdings only; never crosses; reduce side still floored).
Both builders: suites to run at the end: tests/test_value_mode.py 101, tests/test_alloc.py 123, tests/test_cash_gate.py 83, tests/test_mm_bot.py
600 (+ your new file). Commit "P12 L: ..." / "P12 M: ...". Reply with settings (name, default, range), test counts, decisions, what is left.
