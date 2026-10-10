"""
Settings: the Config dataclass (every number you might want to change, documented in place), CFG
(the live instance built from the environment), the override whitelist (OVERRIDABLE) and
validate_overrides / validate_market_edge / parse_utc_setting.

Must never import the bot, the mixins or the exchange; only util.
"""
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from mmbot import util


# =============================================================================================
# SETTINGS - every number you might want to change. Nothing below this block needs editing.
# Prices are probabilities: 0.01 = 1 cent = 1 percentage point. Times are in seconds unless named.
# =============================================================================================

@dataclass
class Config:
    # --- RISK LIMITS -------------------------------------------------------------------------
    max_drawdown_pct: float = 0.30        # kill switch: stop if account value falls this far below the initial balance (0.30 = 30%)
    kill_confirmations: int = 2           # ...on this many readings in a row (so one glitchy number can't trigger it)
    reserved_cash_mode: str = "ignore"    # does the API's account value leave out cash locked in our open orders?
                                          #   "auto" = work it out from the first orders we post, "add" = yes, add
                                          #   it back, "ignore" = no, use the API's number as is. Day one settled it:
                                          #   "ignore" (API value - locked stayed 99.2-101.3k while locked moved
                                          #   0-48k, corr 0.9993; "auto" never decided and added it back, inflating
                                          #   the kill switch, the worst-case cap and the summaries by up to 48k)
    reserved_calib_min: float = 1000.0    # "auto" only judges when locked cash changed by at least this much...
    reserved_calib_votes: int = 2         # ...and needs this many agreeing observations before deciding
    max_worst_case_frac: float = 0.30     # settlement risk (risk_model) > 30% of account value -> reduce-only everywhere
    risk_model: str = "correlated"        # R7. "sum_max" = the old measure: add up every race's own worst outcome, as
                                          #   if all ~75 races went wrong at once (day one: 23.3k, growing 1.5k/h with
                                          #   breadth, while the settlement sd was ~4.4k and a 10c national swing cost
                                          #   +-249). "correlated" = national swing shock + risk_z x sd of the rest:
    risk_swing_shock: float = 0.15        #   every Republican price up 15c and Democratic down 15c (or the reverse)...
    risk_z: float = 3.0                   #   ...plus 3 standard deviations of the independent race outcomes
    risk_unheld_legs: str = "half"       # P12 ops (LIVE_0404 / TEXAS_AND_1PCT): the probability used for a race leg we do NOT hold
                                          #   and that has no fair value this cycle. "half" = 0.5 (as before: on 4 Oct 106 such legs
                                          #   inflated settlement_risk by ~7.9k of 34.7k); "ref" = its raw Polymarket reference
                                          #   when there is one, else the race's residual probability (1 - the other legs' values,
                                          #   shared among the unpriced legs), else 0.5. Only unheld legs change: held legs keep risk_fv.
    worst_case_backstop_frac: float = 0.69  # with "correlated": the old sum-of-maxima still forces reduce-only above
                                            #   69% of account value (~70k on 2026-10-02, the owner's choice; a
                                            #   backstop that no longer binds at 30%)
    reduce_only_hysteresis: float = 0.03  # once in reduce-only, leave only below max_worst_case_frac - this (and the
                                          #   backstop - this): risk hovering at 30% used to flip reduce-only every
                                          #   cycle, each flip pulling (then re-placing) a side on every market held.
                                          #   0 = no hysteresis (the old behaviour)
    pulls_cancel_all_over: int = 25       # more pulls than this in one cycle (or more than the writes left this
                                          #   minute, when that's also more than re-placing every quote costs): one
                                          #   tournament-wide cancel-all (1 write) instead of one DELETE each; what
                                          #   should rest is re-placed next cycle in batches. 0 = off. Not with
                                          #   ONLY_EXCHANGES (its cancel-all is one write per exchange anyway)
    # Sizes below are FRACTIONS OF ACCOUNT VALUE (0.01 = 1%), recalculated as the account changes, so the
    # bot sizes up after gains and down after losses. "shares" = contracts that each pay up to 1.
    sizing_step_frac: float = 0.05        # ...but the account value they're a fraction of only moves in steps: it's
                                          #   updated once the real value is 5% away. Otherwise a 10-SUSQie dip turns
                                          #   every 100-share order into a 99-share one and replaces all of them
    max_party_delta_frac: float = 0.15    # hard cap on |net Republican-minus-Democrat YES shares| over all races
                                          #   (national swing): 15% of account = 15,000 shares at 100k, room for
                                          #   the 10,000-share positions in the party-control markets. At the cap,
                                          #   the side that would add to it is blocked on every Rep/Dem market
    party_skew_at_cap: float = 0.015      # before that: as the net exposure builds, shade every Rep/Dem quote
                                          #   against it, growing to 1.5c at the cap, so the bot sheds it while
                                          #   still quoting both sides (0 = off, hard cap only)
    max_position_frac: float = 0.03       # limit on |net YES shares| per exchange WITHOUT a liquid Polymarket
                                          #   price: 3,000 shares at 100k. With one, Kelly sizing sets the limit
    limits_use_race_net: bool = True      # the position limits (max_position_frac, Kelly, headline_position_frac)
                                          #   also apply to the RACE-NETTED position (effective_inventory), on the side
                                          #   that grows it: a short Rep leg counts toward the Dem leg's limit and vice
                                          #   versa. 2 Oct: long 10,834 Dem House AND short 9,396 Rep House = the same
                                          #   ~20k bet twice under a 10k per-leg limit. The per-market limit still
                                          #   applies; the side that shrinks the netted position is never limited by
                                          #   this. False = per-market only (the old behaviour)
    capital_in_positions_max_frac: float = 0.75  # capital ceiling: positions worth more than 75% of account value ->
                                          #   every market's ADDING side (the one growing |race-netted position|) is
                                          #   sized by capital_ceiling_adding_size_factor, the reducing side quotes as
                                          #   usual, until it is back below this - 0.05. 2 Oct: 90.5k of 101k sat in
                                          #   positions, 11k cash left to quote with. 0 = off
    capital_ceiling_adding_size_factor: float = 0.5   # ...0 = adding side not quoted at all, 0.5 = half size
    # Mark fragility (diagnostic only: health mark_frag_*): a position's mark noise = |position| x sd of the 10-min
    # change of the tournament mid (recorder snapshots); analysis/mark_fragility.py. The sizing cap this once drove
    # (mark_frag_enabled, mark_frag_total_max_cash) is gone; the estimate and its knobs stay for the status line.
    mark_frag_max_step_cash: float = 100.0  # ...at most this many $ of mark noise per 10-min step from one position
    mark_frag_window_hours: float = 24.0  # ...sd over the last this many hours of snapshots (refreshed every 30 min)
    mark_frag_min_samples: int = 60       # ...fewer 10-min changes than this in the window -> no estimate, no cap
    mark_frag_floor_sd: float = 0.002     # ...an sd below 0.2c counts as 0.2c, so the limit never explodes
    max_order_cash_frac: float = 0.01     # max cash tied up in a single order: 1,000 at 100k
    tail_low: float = 0.05                # fair value below this: don't SELL YES (risks ~95c a share to earn ~1c)...
    tail_high: float = 0.95               # ...above this: don't BUY YES. Either side still allowed to shrink a position
    jump_threshold: float = 0.15         # fair value moved 15c in one cycle -> probably news -> stop quoting that market...
    jump_cooldown_seconds: float = 60.0   # ...for this long
    # Election night: every market closes 4 Nov 00:00 UTC; the first polls close ~23:00 UTC. Countdown:
    flatten_hours_before_close: float = 12.0   # only trade toward flat (a hedged Rep/Dem pair counts as flat)
    flatten_per_market_hours: float = 6.0      # flatten EACH market on its own (a hedged pair still settles as one
                                               #   win + one loss, which hurts Smart Score's win rate)
    exit_hours_before_close: float = 2.0       # exit hard: trade against other orders if needed to get flat...
    exit_max_slippage: float = 0.03            # ...but never more than 3c worse than fair value
    stop_minutes_before_close: float = 15.0    # nothing at all (orders cancelled)
    max_failed_cycles: int = 3            # this many API-error cycles in a row -> cancel everything until healthy
    positions_stale_max_cycles: int = 0   # positions answering 409 "holdings cannot be valued": the last read is
    positions_stale_max_seconds: float = 120.0  # reused for at most this many cycles in a row OR this long, whichever
                                          #   comes first; then the cycle fails (an API error: after max_failed_cycles
                                          #   of those every quote is pulled until positions read again), because
                                          #   fills keep landing while inventory, limits and reduce-only stay frozen.
                                          #   0 = no limit on that count (both 0 = reuse forever, the old behaviour)

    # --- QUOTING -----------------------------------------------------------------------------
    min_edge: float = 0.01                # never quote closer than this to our reservation price
    max_half_spread: float = 0.04         # never quote further than this from it
    order_size_frac: float = 0.005        # shares per order when size_by_activity is off (and before the first
                                          #   plan): 500 shares at 100k. Normally QUOTE SIZES BY ACTIVITY decides
    skew_per_share: float = 0.00003       # skew_mode "share": reservation price moves 0.3c per 100 shares of
                                          #   race-adjusted inventory, the same in every market (the old rule)
    skew_mode: str = "quote"              # "quote" = scale the skew to the market's own quote size: holding one
                                          #   full quote's worth of shares moves the reservation price skew_per_quote.
                                          #   Day one, "share" moved it 12-24c after one 4-8k headline fill, so the
                                          #   next quotes sat through fair value: 42% of shares traded at or through
                                          #   our own fair value, -804 at the 60-min mid. "share" = the old rule
    skew_per_quote: float = 0.005         # 0.5c per quote-size of inventory...
    skew_max: float = 0.02                # ...at most 2c in total (both modes; 1.0 = no cap)
    max_skew_through: float = 0.0         # a skewed quote may sit at most this far THROUGH fair value (0 = at fair
                                          #   value at worst). Not applied in reduce-only/flatten, which must get out.
                                          #   1.0 = off (the old behaviour: up to max_half_spread through it)
    skew_age_after_hours: float = 1.0     # the quote log line shows a position's share-weighted age beyond this (the
                                          #   age SKEW that used it was removed on simplify: off live since 2 Oct)
                                          #   Never through fair value (max_skew_through). 2 Oct: median held share
                                          #   7.2 h old, 19% > 12 h. False = off (the old behaviour)
    improve_ticks: int = 1                # R4: quote this many ticks better than the best other trader (1 = penny,
                                          #   0 = join their price)
    undercut_step_back: float = 0.0       # R4: another trader already inside our min_edge band -> quote this far
                                          #   from the reservation price instead of at min_edge (0 = off)
    keep_fraction: float = 0.5            # keep a partly-filled order (and its queue spot) while >= 50% remains
    reprice_tolerance_ticks: int = 1      # leave an order alone if its target price moved by at most this many
                                          #   ticks (0.5c each) and it still keeps min_edge without crossing anyone:
                                          #   saves a cancel + replace and keeps our place in line (0 = always move)

    # --- QUOTE SIZES BY MARKET ACTIVITY -------------------------------------------------------
    # Trading is concentrated (9 of 113 races hold 80% of Polymarket's volume) and tournament trades are big,
    # so capital goes where the trades are: busy markets get big quotes, quiet ones small, and all resting
    # quotes together lock at most quote_capital_frac of the account (a two-sided quote locks ~1 a share).
    # Activity = Polymarket volume before the open, shifting toward the tournament's own trades as they come.
    size_by_activity: bool = True         # False = every market quotes order_size_frac
    quote_capital_frac: float = 0.60      # resting quotes lock at most 60% of account value; the rest stays free
                                          #   for positions, arbitrage and the election-night exit
    size_min_frac: float = 0.001          # quietest markets: 100 shares at 100k (still present, still earning)
    size_max_frac: float = 0.02           # busiest ordinary markets: up to 2,000 shares at 100k
    headline_races: tuple = ("U.S. House", "U.S. Senate")   # party control of Congress: by far the most traded
    headline_size_frac: float = 0.10      # quotes of 10,000 shares there (10% of account value; like every size,
                                          #   it grows and shrinks with the account)...
    headline_position_frac: float = 0.10  # ...and positions up to 10,000 shares there (a flat limit, not Kelly)
    size_plan_seconds: float = 1800.0     # re-plan every 30 min, as tournament trades come in
    size_plan_step: float = 0.25          # a market's size only changes when a new plan moves it by more than
                                          #   25%, so re-planning doesn't replace orders (and queue spots) for nothing
    live_activity_trades: int = 1000      # tournament trades seen before they count fully...
    live_activity_max_weight: float = 0.8 # ...which is 80%; Polymarket volume keeps at least 20%

    # --- FAIR VALUE --------------------------------------------------------------------------
    fv_min_depth: int = 200               # skip price levels until this many shares have accumulated (anti-spoofing)
    max_spread_for_fv: float = 0.30       # book wider than this -> no reliable price -> don't quote
    # Thin books (R5). Day one 157-189 of 237 markets went unpriced although their books were two-sided and
    # tight (median spread 1c) and 97% had a Polymarket price: fewer than fv_min_depth shares at the top.
    # 72 held positions sat there, unquotable (Rep U.S. Senate +7,585 after 19:30).
    ref_only_enabled: bool = True         # price such a market from Polymarket alone, when Polymarket is liquid
                                          #   and the tournament's own (raw) mid is within ref_only_max_gap of it
    ref_only_max_gap: float = 0.03        #   (guards against a wrong match: there is no depth-checked book price)
    ref_only_min_edge: float = 0.015      # ...quoting wider than usual there...
    ref_only_size_frac: float = 0.001     # ...and small: 100 shares at 100k on the side that adds to a position...
    ref_only_reduce_full: bool = True     # ...while the side that shrinks one quotes the market's normal size (day one:
                                          #   Rep U.S. Senate +7,585 would otherwise leave 100 shares at a time)
    # 2026-10-02 08:08 restart: every ex.book was None and the books came in at 12-14 a minute, so R5 priced
    # 30 -> 101 markets in 6 min (162-171 before the restart). The bulk best bid/ask (3 requests for all 237)
    # is enough for R5's checks, which only look at the top of the book. The depth-checked fair value never
    # uses it (it needs fv_min_depth shares, i.e. a downloaded book).
    ref_only_use_tops: bool = True        # R5 may use the bulk best bid/ask when the book is missing or stale...
    tops_max_age: float = 120.0           # ...if that bulk reading is at most this old (s) and two-sided (others')
    # Favourite-longshot side bias. Day one + night, 891 fills with a known side (edge = quote vs fair value,
    # markout = fair value 60 min later): asks at fv < 20c +2.26c edge / +2.03c markout on 36k shares, bids there
    # -0.53c / -0.57c on 20k; bids at fv > 80c +1.49c / +1.41c on 29k, asks there -0.44c / -0.49c on 32k. Students
    # buy longshots and sell favourites, and the tournament mid reverts toward Polymarket. So the side that buys
    # the longshot (our bid below fl_low) or sells the favourite (our ask above fl_high) quotes wider and smaller,
    # unless it shrinks a position on this exchange (then it quotes normally: unloading is good).
    fl_bias_enabled: bool = False         # OFF: the Analyst showed the "bad side" losses were day one's skew quoting
                                          #   through fair value, not longshot flow (DATA_REPORT_2 §10c); owner may switch on
    fl_low: float = 0.20                  # fair value below this: the bid is the "bad" side
    fl_high: float = 0.80                 # fair value above this: the ask is the "bad" side
    fl_hysteresis: float = 0.01           # once on, the bias stays until fair value is this far back across the line
    fl_bad_side_extra_edge: float = 0.01  # bad side quotes this much further from the reservation price (capped at
                                          #   max_half_spread); adds to ref_only_min_edge in thin-book markets
    fl_bad_side_size_factor: float = 0.5  # ...and at this fraction of its size (multiplies with burst / thin-book sizes)
    fl_mid_bid_extra_edge: float = 0.0    # optional extra edge on bids with fair value in [fl_low, fl_high] (mid-band
                                          #   bids lost -0.25c / -0.34c on 92k shares; may be day one's skew bug). 0 = off

    # --- REFERENCE PRICES (Polymarket via ref_prices.py; only active if ref_map_file exists) ---
    # Polymarket is treated as the better estimate of the true price: the tournament book is seeded
    # from it and lags it.
    ref_map_file: str = "ref_map.json"    # "" = off
    ref_refresh_seconds: float = 5.0      # download Polymarket prices this often (one round trip: 5 requests side by
                                          #   side on kept-open connections = 1 request/s). Going below 5 s: first
                                          #   confirm Polymarket's published Gamma /markets rate limit allows it.
                                          #   Its public API is live and separate from SIG's request budget, so the
                                          #   only delay is this interval: faster bots pick off stale quotes
    ref_weight: float = 0.7               # fair value = 70% Polymarket + 30% tournament book (0 = guard only)
    ref_guard_gap: float = 0.05           # Polymarket and the TOURNAMENT BOOK disagree by more than this -> don't
                                          #   quote the side Polymarket says is mispriced. Keep >= 0.05: Polymarket
                                          #   can have sudden unexplained moves
    ref_jump_threshold: float = 0.03      # Polymarket moved this much between two readings (5 s apart) ->
    ref_jump_cooldown_seconds: float = 60.0   #   pull that market's quotes for this long. A real move then shows
                                              #   up in the weighting; a spike that reverts never touched us
    ref_max_plausible_gap: float = 0.25   # a Polymarket price further than this from the tournament book is almost
                                          #   certainly a wrong match (e.g. a replaced candidate): ignore it + alert
    ref_liquid_spread: float = 0.03       # only lean on a Polymarket price (weighting + Kelly sizing) when its own
                                          #   bid/ask spread is at most this. Thin or last-trade-only prices (which
                                          #   can be days old) are used for the guards only

    # --- POSITION SIZING (fractional Kelly; markets with a liquid Polymarket price only) -------
    # For each side, the most we'd hold = kelly_fraction x (edge / (1 - cost)) x account value, where
    # edge = how far our price is on the right side of Polymarket's probability and cost = what one
    # contract costs us (bid for YES, 1 - ask for NO). It grows with the account and shrinks with it.
    kelly_fraction: float = 0.25          # quarter Kelly (0.5 = half). Full Kelly (1.0) is far too aggressive for
                                          #   estimated odds. Higher = bigger positions and bigger swings (lower Sharpe)
    kelly_max_market_frac: float = 0.02   # never more than 2% of account value at risk in one market
    kelly_no_edge_frac: float = 0.001     # on a side where Polymarket sees no edge, allow only this much: 100
                                          #   shares at 100k (keeps two-sided quoting without betting against it)

    # --- ARBITRAGE (guaranteed profit inside a race) ------------------------------------------
    arb_enabled: bool = True
    arb_min_profit: float = 0.03          # act when other traders' bids across a race add up to >= 1 + this. Not
                                          #   lower: 1c arbitrage adds lots of volume for little profit, which drags
                                          #   down Smart Score's ROI (P&L / volume)
    arb_max_frac: float = 0.005           # most sets sold per arbitrage: 0.5% of account = 500 at 100k
    arb_order_ttl: float = 10.0           # arbitrage orders expire after this (leftovers are also cancelled at once)
    arb_cooldown_seconds: float = 30.0    # after acting on a race, leave it alone for this long
    # Buy side (arb_two_sided): other traders' asks across ALL of a race's listed parties add up to
    # <= 1 - arb_min_profit_buy -> buy YES on every leg (a long set pays exactly 1 if a listed party wins).
    # 60-s snapshots: ask-sum < 0.98 in 853 race-minutes/day (2.5%). The pair unwinder sells the set later.
    arb_two_sided: bool = True
    arb_min_profit_buy: float = 0.015     # ask-sum <= 0.985. Not done in reduce-only or the pre-close window
    arb_buy_min_ref_sum: float = 0.99     # ...and only when every leg has a LIQUID Polymarket price and those raw prices
                                          #   add up to at least this (an unlisted outsider shows up as a shortfall)
    arb_buy_min_sum: float = 0.90         # ...nor when the asks add up to less than this: that more likely means
                                          #   the market prices a winner OUTSIDE the listed parties (e.g. an
                                          #   independent with no market of its own), when a set pays nothing

    # --- PAIR UNWIND (inventory-aware: turn a held complete set back into cash) ----------------
    # Long YES on EVERY listed party of a race (min over legs = n sets) pays exactly n at settlement: zero risk,
    # zero return, capital tied up and mark noise. When other traders' bids add up to >= 1 + pair_unwind_min_profit,
    # sell the sets (riskless gain of bids-1 per set). Mirror: short every leg and asks add up to <= 1 - this ->
    # buy back. Reduces positions, so it also runs in reduce-only and in the pre-close window. A race with an
    # independent leg is only a set if that leg is held too (min over ALL legs).
    pair_unwind_enabled: bool = True
    pair_unwind_min_profit: float = 0.005  # 0 = unwind at exactly fair (bids sum to 1.000)
    pair_unwind_max_frac: float = 0.01    # at most this much cash per unwind order: 2,000 at 100k
    pair_unwind_cooldown_seconds: float = 30.0

    # --- TAKING STALE HOUSE QUOTES (liquid Polymarket prices only) -----------------------------
    # When Polymarket has clearly moved and the tournament's best quote hasn't followed, trade against
    # that quote directly (buy the stale ask / sell to the stale bid), sized with Kelly.
    take_enabled: bool = True
    take_edge: float = 0.05               # Polymarket must be at least this far past the quote. Keep >= 0.05:
                                          #   Polymarket can have sudden unexplained moves
    take_confirm_seconds: float = 30.0    # ...on every Polymarket reading for at least this long, so a spike that
                                          #   reverts is never traded
    take_cooldown_seconds: float = 60.0   # after taking in a market, leave it alone for this long
    take_order_ttl: float = 10.0          # take orders expire after this (leftovers are also cancelled at once)
    take_ref_max_age_seconds: float = 30.0  # a Polymarket price not re-downloaded for this long never confirms or
                                          #   triggers a take (a failed download keeps the old price for 5 min and
                                          #   still counts as a reading). 0 = off

    # --- ORDER LIFECYCLE ---------------------------------------------------------------------
    order_ttl: float = 1800.0             # every order expires after 30 min (dead-man's switch). Longer = fewer
                                          #   replacements (each costs requests and our place in line)
    refresh_before_expiry: float = 180.0  # replace an order once it has less than 3 min to live
    batch_size: int = 10                  # orders per POST /orders/batch. Was 20: 1 Oct 16:00-2 Oct 08:08, 273 of
                                          #   the 417 "409 in flight" batches (each a batch that ran past the 15 s
                                          #   timeout, then retried) were full 20-order batches, while batches
                                          #   under 20 failed ~5% of the time. Smaller = faster writes, a bit more
                                          #   write budget on big re-quotes (a restart: ~6 batches instead of 3)
    parallel_writes: int = 4              # order writes (cancels, batches) in flight at once. Cancels go first, then
                                          #   new orders: party-control (headline) markets first, then the biggest
                                          #   quotes. 1 = the old way: one write at a time, the cycle waiting for each
    write_read_reserve: int = 3           # order writes leave this many requests/min of the budget free (positions,
                                          #   orders reads); book downloads already leave budget_reserve for writes
    write_fail_fast: bool = True          # an order write that times out after being sent isn't retried at once (it
                                          #   would only get 409 in flight); its orders are recovered from the list
    write_wait_seconds: float = 3.0       # a cycle waits at most this long for its writes; slower ones (day one: 15-30 s)
                                          #   finish in the background and their exchanges are left alone until then
    main_write_wait_margin: float = 2.0   # a write sent by the MAIN thread (take, arbitrage, cancel-all instead of
                                          #   pulls) that would wait longer than write_wait_seconds + this for the write
                                          #   budget or a 429 pause is not sent (429 WRITE_BUDGET_WAIT) - the loop never
                                          #   blocks on the budget (2 Oct 11:31: 2-5 min cycles)
    urgent_writes_per_cycle: int = 20     # at most this many writes per cycle for changes the request budget can't
                                          #   defer (pulls, unsafe orders); price-unsafe / unwanted sides first, the rest
                                          #   next cycle. 0 = no cap
    pause_skip_cycles: bool = True        # while the exchange's 429 pause lasts, skip cycles (reads would only wait
                                          #   for the pause inside the cycle) instead of blocking the loop
    watchdog_alert_seconds: float = 180.0  # no cycle completed for this long -> alert (watchdog thread). 0 = off
    watchdog_exit_seconds: float = 600.0  # ...for this long -> log every thread's stack, cancel everything (at most
                                          #   watchdog_cancel_seconds) and exit with code 5 (systemd restarts). 0 = off
    watchdog_cancel_seconds: float = 20.0
    pending_seconds: float = 90.0         # placement outcome unknown -> leave that exchange alone this long, unless
                                          #   the orders show up in the open-orders list first (recovered: see
                                          #   adopt_unconfirmed), which on a slow exchange takes seconds
    recover_unconfirmed: bool = True      # match orders that landed after their request timed out (day one: 55 of
                                          #   the first 82 fills) to what we sent, by exchange/side/price/expiry, so
                                          #   the bot knows them at once and their fills are attributed
    recent_order_grace_seconds: float = 15.0  # the open-orders list can lag the exchange: for this long, trust our
                                              #   own record of an order we just placed (or cancelled) over it,
                                              #   so the bot never places the same quote twice
    fail_pause_seconds: float = 30.0      # order rejected -> don't retry that exchange for this long
    shutdown_cancel_attempts: int = 3     # tries at cancelling everything when the bot exits
    selftest_enabled: bool = True         # live mode: before quoting, place + check + cancel two 1-share orders at
                                          #   extreme prices to confirm the API behaves as assumed; stop if not
    selftest_retry_seconds: float = 60.0  # exchange busy during the test (timeout, 409 in flight, 429, 5xx): not a
                                          #   failure - try again this much later, quoting meanwhile
    selftest_alert_after: float = 900.0   # ...and send one alert if it still hasn't managed after this long

    # --- CHURN CONTROL (a crowded book: other bots re-quote one tick inside us all day) ---------------
    # Every reprice costs a write (30/min budget) and our place in line. Only reprice when it matters.
    churn_control: bool = True
    min_quote_life_seconds: float = 5.0   # an order younger than this isn't repriced while it's still safe (inside
                                          #   our limit price); unsafe ones, pulls and expiries always go
    churn_max_reprices: int = 4           # a side repriced this many times within churn_window_seconds stops
    churn_window_seconds: float = 60.0    #   chasing: its order stays while safe (no ping-pong with another bot)
    churn_count_sent: bool = True         # count a reprice toward churn_max_reprices only when its cancel is actually
                                          #   sent (False = when planned, as before: a change deferred by the request
                                          #   budget or dropped by burst mode still counted, so a side could be held
                                          #   off target for up to churn_window_seconds without ever repricing)
    urgent_ref_move: float = 0.005        # Polymarket moved this much since the last reading: that market's changes
                                          #   are sent first (stale quotes get picked off by the fastest bot)

    # --- BURST PROTECTION (the exchange is slow: day one's open, writes 15-30 s) ------------------
    # While writes or cycles are slow, every quote can sit stale for long, and every change costs a slow write.
    # So: quote only the biggest markets, smaller and one tick wider; elsewhere keep what's resting while it's
    # safe and only pull, never place. Back to normal after burst_calm_seconds of normal speed.
    burst_protection: bool = True
    burst_write_seconds: float = 5.0      # enter when the median order write of the last minute takes this long...
    burst_cycle_seconds: float = 60.0     # ...or a cycle takes this long (2 Oct live: normal cycles take 20-30 s, so 20
                                          #   fired 4 times with writes at 0.4 s and no timeouts; 60 = owner override)...
    burst_timeouts: int = 2               # ...or this many writes timed out in the last minute
    burst_calm_seconds: float = 120.0     # leave after this long without any of that
    burst_startup_grace_seconds: float = 90.0   # after a (re)start or handover, the cycle-length trigger is ignored
                                          #   this long, and while the first download of every book is still running
                                          #   (at most BURST_LOADING_MAX_SECONDS): those cycles are long because of our
                                          #   own throttled reads (2 Oct 08:08: 21 s -> burst for 2 min, write median
                                          #   0.4 s). Slow writes and timeouts still trigger. 0 = no grace
    burst_markets: int = 40               # quote only this many markets: House/Senate, then by planned size
    burst_size_factor: float = 0.5        # new quotes at this fraction of their usual size
    burst_extra_edge: float = 0.005       # ...and this much further from fair value (one tick)

    # --- ORDER BOOKS -------------------------------------------------------------------------
    book_depth: int = 10                  # price levels per side to download
    max_books_per_cycle: int = 30         # full-book downloads per cycle (still capped by the spare request budget)
    book_max_age: float = 600.0           # re-download each book at least this often, even if it looks unchanged.
                                          #   Changed books are caught much sooner by the bulk prices anyway
    book_stale: float = 900.0             # a book not confirmed current (downloaded, or its best prices matched a
                                          #   bulk check) for this long isn't trusted -> don't quote it. Before that
                                          #   happens the bot re-checks it (book_reverify_seconds), so this only
                                          #   bites when the exchange can't be read at all
    book_reverify_seconds: float = 120.0  # a book unconfirmed for this long gets a bulk best-price check (one request
                                          #   per 100 books) on the next cycle, even between full checks
    # After a (re)start every book is missing. Order writes keep only write_read_reserve (3) requests free while
    # book downloads keep budget_reserve (20) free for writes, so writes win the shared per-minute budget and
    # books trickled in at 12-14 a minute (2026-10-02 08:08). While priming: more books per cycle, and books
    # (downloaded before this cycle's writes) only leave startup_prime_reserve requests for the writes, up to
    # startup_prime_books_per_min; Bot.write_reserve() is what order writes should then leave free for them.
    startup_books_first: bool = True      # prime the books after a start or handover restart...
    startup_prime_seconds: float = 180.0  # ...for at most this long after trading starts...
    startup_prime_missing_frac: float = 0.10   # ...and only while more than this share of books is missing
    startup_prime_books: int = 60         # book downloads per cycle while priming (instead of max_books_per_cycle)
    startup_prime_reserve: int = 8        # requests per minute book downloads leave free while priming
    startup_prime_books_per_min: int = 45 # ...but at most this many downloads in any 60 s, the rest for order writes
    startup_prime_held_max_seconds: float = 900.0  # ...except: priming never ends while a market we HOLD a position in
                                          #   has no downloaded book, up to this hard maximum (s) after the start
    unpriced_held_warn_cycles: int = 5    # a held market without a fair value this many cycles in a row: one WARNING
                                          #   with the reason (no book / tops blanked / gap / guard); 0 = off

    # --- TIMING / NETWORK --------------------------------------------------------------------
    loop_seconds: float = 10.0            # target time between cycle starts
    market_reload_seconds: float = 600.0  # re-read the market list (adds new markets, drops closed ones)
    start_check_seconds: float = 60.0     # before the tournament opens, check its status this often...
    open_quiet_seconds: float = 60.0      # ...until this long before the start: then stop downloading books (keeps
                                          #   the request budget free for the first quotes) and sleep until the start
    open_poll_seconds: float = 1.0        # from the start time, check every second until trading is open, so the
                                          #   first quotes go out within ~1 s (first in line at each price wins)
    # The API rate-limits and doesn't publish the limit. Measured 25 Sep: roughly 100 requests per
    # minute, then "429, retry after 60 s". So every request (all threads, all features) goes through
    # a hard per-minute budget. The docs warn that polling /orders and /portfolio/* hits it fastest.
    # NOTE: other commands (status, markets...) run while the bot is live use the same key's budget.
    requests_per_minute: int = 80         # hard budget, sliding 60 s window. Cut by 25% after a 429, then
                                          #   recovers slowly (+1 a minute), so it tunes itself
    writes_per_minute: int = 28           # separate budget for order writes (each batch, cancel-all or DELETE = 1),
                                          #   2 Oct live (Package 2.3): 4 x 429 in the first hour while the budget
                                          #   climbed to 36-50/min, 0 since capped at 28: the limit is ~30/min per bot.
                                          #   the value it starts at. A copy of the platform docs says "100 reads and
                                          #   30 writes per minute per key", but 1-2 Oct (16:00-08:08) ran 63 minutes
                                          #   at >= 40 writes (peaks ~60) with no 429; the 6 429s came at 33-53 writes
                                          #   and didn't follow the write rate. 30 deferred changes on every cycle for
                                          #   3 minutes after the 2 Oct 08:08 restart
    writes_per_minute_max: int = 28       # ...it then grows slowly (+1 per 60 successful writes) up to this while no
                                          #   write is rate limited (= writes_per_minute: never grows)
    startup_writes_per_minute: int = 30   # while the first download of every book is still running after a (re)start,
                                          #   writes stay at most this (the old budget), so the bigger write budget
                                          #   doesn't slow the book downloads new quotes need. 0 = no cap
    write_budget_cut: float = 0.75        # a write answered 429 cuts the write budget to this fraction (min 10/min)
    never_defer_unsafe: bool = True       # a change that removes an UNSAFE order (beyond its limit price, above the
                                          #   position / cash limits, or a side we no longer want) is never deferred by
                                          #   the request budget, like a pull (False = only pure pulls are exempt).
                                          #   An order only bigger than a size FACTOR now wants (capital ceiling, burst,
                                          #   favourite-longshot) is not unsafe: it stays (see Quote.bid_max)
    budget_reserve: int = 20              # requests per minute kept free for orders, cancels and account
                                          #   reads; book downloads only use what's left
    parallel_requests: int = 2            # HTTP requests in flight at once (1 = one at a time)
    min_request_gap: float = 0.50         # min time between request STARTS, all threads together (2/s max);
                                          #   smooths bursts; doubles after a 429 and drifts back
    max_request_gap: float = 2.0          # ceiling for that automatic slow-down
    request_timeout: float = 15.0         # give up on one HTTP request after this long (it's then retried)
    max_clock_skew_seconds: float = 3.0   # alert at start-up if this computer's clock is further than this from the
                                          #   exchange's: order expiry and the timing of the open use our clock
    max_retries: int = 5                  # retries for network errors / 429 / 502 / 503 / 504
    slow_poll_seconds: float = 30.0       # read P&L (kill switch) and fills this often (plus at once after a fill)
    max_fill_pages: int = 5               # pages of 200 fills to read back per check (catches up to 1000 fills)

    # --- REALTIME FEED (pushed updates instead of polling; needs `pip install realtime`) -------
    realtime_enabled: bool = True         # False = always poll every loop_seconds
    min_cycle_seconds: float = 0.5        # at most one cycle per 0.5 s (the exchange already groups its pushed
                                          #   updates every 250 ms; a cycle with nothing to download costs no requests)
    bulk_check_over: int = 3              # more books than this reported changed at once -> one bulk-price check
                                          #   (3 requests for all 237) instead of downloading each book
    realtime_heartbeat_seconds: float = 30.0   # with the feed: full safety check (bulk prices, positions, orders)
                                               #   this often, because delivery is best-effort
    realtime_token_refresh_seconds: float = 600.0  # start a new session (fresh 3-hour login) this long before it expires
    realtime_healthy_seconds: float = 60.0         # a session up this long that then drops reconnects after 1 s again
                                                   #   (the back-off only grows over drops that come in quick succession)
    realtime_session_max_seconds: float = 3600.0   # ...and at least this often anyway: a long-lived socket can die
                                                   #   without the library noticing (it happened on 29 Sep)

    # --- LIVE SETTINGS (change settings without a restart) -----------------------------------
    overrides_file: str = "settings_override.json"   # {"min_edge": 0.015, ...}: re-read every overrides_seconds;
                                          #   only OVERRIDABLE settings, each checked; a removed key goes back to
                                          #   its default. Every change is logged. "" = off
    overrides_seconds: float = 30.0
    analyze_daily_hour: int = -1          # send the first lines of `analyze` (last 24 h) to your phone daily at this
                                          #   hour UTC (-1 = off)
    slow_cycle_alert_seconds: float = 120.0   # a cycle running this long: status.json says so and one alert is sent
    # Rival-floor map (analysis/rival_floor.py writes market_edge.json from the recorder's books): a per-market
    # min_edge, max(min_edge, the market's entry capped at market_edge_max); markets without an entry keep
    # min_edge. Off until calibrated on the live recorder data.
    market_edge_enabled: bool = True 
    market_edge_file: str = "market_edge.json"   # {"<exchange id>": {"min_edge": 0.015, ...}, ...}; "" = off
    market_edge_reload_seconds: float = 600.0    # re-read this often (when the file changed)
    market_edge_max: float = 0.02         # no market's own edge above this

    # --- FILES (relative names are kept in the bot's own folder) -----------------------------
    fills_csv: str = "fills.csv"
    log_file: str = "mm_bot.log"          # "" = log to the terminal only
    log_max_mb: float = 10.0              # start a new log file at this size...
    log_backups: int = 5                  # ...keeping this many old ones (so the log can't fill the disk)
    status_file: str = "status.json"      # health snapshot rewritten after every cycle
    order_notes_file: str = "order_notes.json"  # survives restarts, so fills can still be attributed
    position_lots_file: str = "position_lots.json"  # position ages (age skew) survive restarts; without it they are
                                          #   rebuilt from fills.csv at start (latest fills = what FIFO leaves held)
    kill_file: str = "kill_switch.tripped"      # the kill switch creates it; delete it to allow trading again
    handover_file: str = "handover.json"        # written by a handover stop (SIGUSR1): the next start adopts the
    handover_max_age: float = 300.0             #   orders left resting instead of cancelling them, if within this
                                                #   many seconds (else: clean slate as usual)
    handover_exit_max_seconds: float = 150.0    # a handover stop that hasn't exited this long after SIGUSR1 exits at
                                                #   once (exit 0): finishing the cycle, the 60 s write drain, then
                                                #   queued writes on the writer threads (each up to ~4 x 15 s plus the
                                                #   write-budget wait) had no bound. Writes still running are
                                                #   abandoned: the next run re-reads the open-orders list (and without
                                                #   a handover file starts from a clean slate). 0 = no cap
    record_file: str = "market_data.sqlite"     # snapshots for tuning later ("" = off)
    record_seconds: float = 60.0          # one snapshot of every market this often (~15 MB a day)
    record_books: bool = True             # also record OTHER traders' top book levels (with sizes) whenever a downloaded
                                          #   book's top changed, and every tournament trade the feed reports: the data
                                          #   to measure rival bots (repricing speed, floors, sizes, hours). No extra
                                          #   requests; roughly 10-15 MB a day
    record_book_levels: int = 3           # levels per side kept in those rows
    record_positions: bool = True         # also record the positions read (per position: quantity, the exchange's
                                          #   mark currentPrice and every other number it gives) and the account
                                          #   marks (account value, market value, cash, P&L) - the data to pin down
                                          #   how the exchange values positions (analysis/mark_rule.py). No extra
                                          #   requests: at most once per record_seconds plus once after a fill, and
                                          #   a position's row only when it changed (or hourly). ~1-3 MB a day
    record_positions_full_seconds: float = 3600.0   # ...but every position at least this often
    # --- REDUCING SIDE JOINS THE BEST -----------------------------------------------------------
    # Data (Explorer): P(fill in 10 min) 33-34% AT the best, 6-8% one tick behind; our reducing side was at the
    # best only 24% of the time; 11 of 12 positions >= 1,000 sh had no reducing fill in 6 h; round trips made all
    # the realised profit (+1,824 on 254k shares). So the side that shrinks |race-netted position| joins the best.
    reduce_join_best: bool = False         # when the best other price on that side is INSIDE our normal quote, that
                                          #   side joins it (AT the best, no pennying), but never closer than
                                          #   reduce_join_min_edge to fair (rounded away) and never crossing. A best
                                          #   outside our quote changes nothing (we stay the best). A fast unload
                                          #   window still wins when closer to fair. Off in reduce-only
    reduce_join_min_edge: float = 0.01    # closest the joining side may sit to fair value (0 = fv rounded away)
    reduce_join_min_shares: int = 100     # only while |race-netted position| is at least this

    # --- TURNOVER (dead-market diagnostic: health turnover_dead_* / turnover_judged, status line) -------------
    # A market whose observed flow (shares traded in the last turnover_window_hours: our fills, or the realtime trade
    # tape if larger - the tape includes ours) is below turnover_min_shares_per_hour is DEAD; while we hold a position
    # there (|race-netted| >= min(quote size, 100)) it is counted in health turnover_dead_markets / _capital / _top.
    # Until ~turnover_window_hours of flow has been observed (this run plus what fills.csv / the recorder's trades
    # cover), every market counts as alive. The quoting control this once drove (turnover_control_enabled) is gone.
    turnover_window_hours: float = 6.0
    turnover_min_shares_per_hour: float = 50.0
    turnover_use_tape: bool = True        # count other traders' trades from the realtime feed (and its recording)
    turnover_alive_shares_per_hour: float = 75.0   # hysteresis: a dead market is alive again only above this flow
    turnover_min_state_minutes: float = 30.0       # a market stays dead / alive at least this long before flipping

    # --- CONNECTION / ALERTS (from the environment: see top of file) ------------------------
    summary_every_hours: int = 2          # phone summary every N hours, on the hour UTC (2 = 00:00, 02:00, 04:00...),
                                          #   covering what happened since the previous one. 0 = off. Live only
    alert_url: str = os.environ.get("ALERT_URL", "")   # e.g. https://ntfy.sh/some-long-random-name
    api_key: str = os.environ.get("SUPERMARKET_API_KEY", "")
    base_url: str = os.environ.get("SUPERMARKET_BASE_URL", "https://sig.thesuper.market/api/v1")
    slug: str = os.environ.get("TOURNAMENT_SLUG", "")
    only_exchanges: str = os.environ.get("ONLY_EXCHANGES", "")
    # --- Package 5: T2.1 tilt-corrected reference ---
    # The tournament prices every market with one favourite-longshot tilt: mid ~ c + (1 - s)(r - c), c = 1/legs.
    # s is estimated every cycle from the cross-section (TiltEstimator) and reported (status.json tilt_s / tilt_diag,
    # the summary's "tilt X%"); applying it to quotes (ref_tilt_enabled and its ramps) was removed on simplify.
    ref_tilt_min_markets: int = 50        # fewer usable markets than this: hold the last estimate
    ref_tilt_halflife_min: float = 30.0   # EMA half-life of the estimate, minutes
    ref_tilt_max: float = 0.20            # estimate clipped to [0, this]. Live s was 6.3% on 2 Oct evening (2.4% a day
                                          #   earlier); in a 15%-tilt simulated world a 0.12 clip left the bot buying 3
                                          #   points of tilt for the same 3-h P&L (SIM_NOTES Round 5), so the cap is set
                                          #   where it does not bind and the estimate stays readable; alarm above 0.12
    ref_tilt_winsor: float = 0.08         # each market's gap (Polymarket - book) clipped to +-this
    ref_tilt_estimator: str = "slope"     # how s is read from the cross-section (x = r - c, g = winsorised gap):
                                          #   "slope": sum x g / sum x^2 (x^2 weights: the |x| > 0.45 tails carry ~60%
                                          #   of the weight); "median": median of per-market g / x over |x| > 0.1
                                          #   (a block of tail markets pinned at the winsor cannot drag it); "wls":
                                          #   their mean (= least squares weighted 1 / x^2). TILT_ESTIMATOR.md
    # --- Package 5: B kelly_edge_cap, A reduce_from_book ---
    # B: Kelly sizes on at most this much edge (0 = off; try 0.015 / 0.01): size on the spread, not on a persistent
    #   tournament-vs-Polymarket gap (otherwise the bet is biggest exactly where the tournament disagrees most).
    kelly_edge_cap: float = 0.0
    # --- Package 6: reduce NO holdings as covered NO sales ---
    # Live 3 Oct: every order was sent as side "yes", so a BID that buys back a short YES position (we hold NO) went
    # out as "buy YES @ p", which the exchange treats as a new cash purchase and refuses at 0 free cash ("Insufficient
    # available funds"): the short book could never shrink. True: the part of a bid that reduces the NO held in that
    # market (per market, not race-netted) is sent as a covered sale "sell NO @ 1-p" (needs no cash); any part
    # beyond the NO held stays "buy YES @ p" (see order_wire). Asks and adding bids unchanged. The start-up
    # self-test checks the exchange accepts it and falls back to False for the run if not. False = unchanged.
    reduce_no_as_sell: bool = False
    # --- Package 7: NO+NO sets ---
    # Live 3 Oct 14:00: 47 of the 49 remaining "Insufficient available funds" refusals were covered NO sales on races
    # where we hold NO on EVERY leg. The exchange collateralises NO+NO as a SET ("collateralSavings"): selling NO on
    # ONE leg breaks the set, the remaining lone NO then needs full collateral, i.e. cash. Selling the LONE part (NO
    # held beyond the race's smallest leg) is fine.
    # no_set_aware_bids True (with reduce_no_as_sell): a covered bid / take on such a leg is capped at its lone part,
    # max(0, NO held - the most NO held on any OTHER leg of the race; a leg without NO counts 0) - the worst-case
    # (sum - max) collateral model, so [10, 8, 5] -> [2, 0, 0] and NO on 2 of 3 legs is capped too (0 = no bid
    # there, it would need cash). The set part is only unwound as a pair (below). False = unchanged.
    no_set_aware_bids: bool = False
    # pair_no_unwind_max_cost >= 0 (with reduce_no_as_sell; live, after its own start-up check): the short-set
    # buy-back in arb_plan (buy YES on every leg = covered "sell NO" on every leg, ONE batch) also runs when the asks
    # add up to <= 1 + this (a cost of at most this per set to free the set's capital), not only <= 1 -
    # pair_unwind_min_profit. Both legs go out as covered sales in one batch or the batch is not sent. -1 = off.
    pair_no_unwind_max_cost: float = -1.0
    # --- Package 7: pair unwind follow-up ---
    # Live 3 Oct 14:11: a short-set pair unwind on Hawaii Governor filled [314, 1069] - 755 shares of a hedged set
    # became one-sided inventory. pair_unwind_followup True: (1) every leg of a pair unwind is sized to what can fill
    # together (the least, over legs, of the cached book's depth at or better than the planned price, and the sets);
    # (2) legs that still fill unequally leave the lagging leg(s) OWED the difference: on the next cycles (up to
    # pair_unwind_followup_tries) one immediate-or-cancel order per lagging leg for what is owed, at a limit up to
    # pair_unwind_followup_max_cost per share past the planned price (buy-back legs as a covered "sell NO" with
    # reduce_no_as_sell), then one alert with what is left. The owed state is in memory only (a restart drops it,
    # logged) and in status.json pair_owed. False = unchanged.
    pair_unwind_followup: bool = False
    pair_unwind_followup_max_cost: float = 0.01
    pair_unwind_followup_tries: int = 6
    # pair_no_unwind_max_per_cycle: at most this many short-set unwinds that run only because of the widened
    # pair_no_unwind_max_cost threshold per cycle (each costs 5 writes and a cooldown; up to 38 NO+NO races qualify
    # at once when it is switched on); the rest wait for the next cycles. No effect with pair_no_unwind_max_cost -1.
    pair_no_unwind_max_per_cycle: int = 2
    # --- Package 8: cash gate and per-market adding side ---
    # Live 3 Oct 19:56: at 100% capital / ~0 free cash still 299 refusals an hour with 400 "Insufficient available
    # funds" (85 on NO+NO races, 44 on FLAT markets, 5 on YES holdings), each one a wasted write.
    # cash_gate_enabled True (live only): no order goes out that needs more cash than is available. An order's cash
    # need (YES terms): a bid = price x shares (buying YES is a purchase, also on a NO holding - live 3 Oct), except a
    # covered "sell NO" (reduce_no_as_sell): 0 up to the NO free to sell there less the part locked in NO+NO sets
    # (1 a share beyond); an ask = (1 - price) x the shares beyond the YES free to sell there (beyond = buying NO);
    # a batch selling NO on every NO-holding leg of a race closes sets (0). Available = the exchange's cash figure
    # (P&L read: an "available..." field as is, else "cash..."/"balance..." less the cash our resting orders lock,
    # else account value - market value less that lock) - cash_gate_reserve, less what this bot has sent since that
    # read (confirmed cancels give theirs back, and so do orders not placed: batch failed / refused / never sent).
    # Each order is capped at the part the cash allows (the cash-free part always goes), dropped below 1 share (no
    # request at all when a whole batch is dropped); joint batches (arbitrage, pair unwinds) shrink every leg alike.
    # Quotes are capped the same way when planned (no churn). Takes / arbitrage / follow-ups are pre-checked before
    # our quotes are pulled. The self-tests' 1-share orders are exempt. A P&L read that fails / has no cash figure
    # alerts once (again after a good read); with no good read for 5 minutes the gate stops gating (logged once)
    # until one is read. status.json cash_gated / cash_trimmed (orders) / cash_capped_quotes (this cycle) /
    # cash_gate_left / cash_gate_read_age (s since the last good read). False = off.
    cash_gate_enabled: bool = False
    cash_gate_reserve: float = 25.0
    # adding_factor_per_market True: the "adding" side for the size factors (capital ceiling, turnover-dead, X5 gap
    # shrink, backstop band, tail factor, mark-fragility over) and for the ladder's factor block is the side that
    # grows THIS market's |position| (and the part of a reducing order beyond the position), not the race-netted
    # one, so capital_ceiling_adding_size_factor 0 means no new per-market positions (no hedge purchases). The
    # race-netted position still drives skew, the reduce-only clip and the risk limits. False = unchanged.
    adding_factor_per_market: bool = False
    # --- Package 8 item 2 (all OFF at the defaults, also with the live Package 7 flags pair_no_unwind_max_cost /
    # reduce_no_as_sell / no_set_aware_bids / pair_unwind_followup ON: each needs its own setting below) ---
    # pair_no_unwind_max_sets: > 0 and pair_no_unwind_max_cost in effect: a short-set (NO+NO) buy-back is capped at
    # this many SETS per race per unwind instead of pair_unwind_max_frac x account / YES ask per leg (a cash-per-order
    # cap measured on the YES price, while the legs are covered "sell NO" orders that lock no cash and RECEIVE 1 - ask).
    # 0 = off: the old cash cap (pair_unwind_max_frac) for every unwind.
    pair_no_unwind_max_sets: int = 0
    # pair_unwind_race_order True (with pair_no_unwind_max_cost in effect): take_arbitrage visits the NO+NO races
    # cheapest first (see arb_race_order) and a NO+NO buy-back's unwind_is_safe allows the worst-case MARK artefact
    # (set_slack). False = off: self.groups order and the old strict unwind_is_safe.
    pair_unwind_race_order: bool = False
    # pair_unwind_followup_max_age: > 0: an owed record older than this (seconds) is alerted and cleared, so a
    # follow-up the write budget defers every cycle (not counted as a try) cannot block take_arbitrage on that race
    # for ever. 0 = off (owed records are kept until resolved).
    pair_unwind_followup_max_age: float = 0.0
    # --- Package 8: cut UNPAIRED tilt exposure faster, never crossing ---
    # adding_factor_capital_on > 0: while the capital ceiling is on, capital in positions / account below this ->
    # the adding side is sized by max(capital_ceiling_adding_size_factor, capital_ceiling_adding_size_factor_resume)
    # (owner live: the configured factor 0 = no adding at all); back to the configured factor at >= this + 0.01
    # (hysteresis). status.json capital_ceiling_adding_factor = the factor in force. 0 = off.
    adding_factor_capital_on: float = 0.0
    capital_ceiling_adding_size_factor_resume: float = 0.5
    # --- Package 9 F5 (analysis/p9/SPEC_F2_F5.md; OFF by default) ---
    # F5 arb_cash_rule True (live 3 Oct: at 0 cash the race arbitrage left one-legged sets, some legs refused for
    # cash): an ARBITRAGE (kind "arb", sell side: bids sum >= 1 + arb_min_profit; buy side: asks sum <= 1 -
    # arb_min_profit_buy; pair unwinds are not changed) is planned on other traders' levels only, a level at a price
    # where we have an order of our own (resting, just sent or unconfirmed) being skipped whole, sized <=
    # arb_leg_depth_frac x the thinnest leg's depth there, and sent only for the sets whose whole cash need (the cash
    # gate's own per-order rule, all legs together) fits: cash_left() >= arb_cash_mult x need + arb_cash_reserve
    # (fewer sets when that is what fits, none below 1; no cash figure = none: it needs cash_gate_enabled). If the
    # batch fills its legs unequally, the lagging legs are OWED (pair_owe) and the next cycle's follow-up completes
    # them within pair_unwind_followup_max_cost of the planned price; when nothing can complete them then (same
    # cycle), and from the second try on, the extra legs are bought / sold BACK instead (within arb_min_profit (_buy)
    # + pair_unwind_followup_max_cost), so no one-legged set is kept (tries / max age: pair_unwind_followup_*). status.json arb_cash_blocked; journal "ARB skipped: cash rule (need X, left Y)". False = unchanged.
    # --- Package 10 A (analysis/p10/SPEC_P10.md Part A; everything OFF by default) ---
    # SIG pays positions out at the OUTCOME: a contract is worth its Polymarket probability p, not its mark. Below,
    # "p" = the raw Polymarket price scaled to sum to 1 over the race (when every leg has one; else the raw price),
    # used only where it is LIQUID (Bot.value_p); no liquid p -> nothing below changes that market.
    # A1 value_mode True, the no-panic-sell guard (analysis/p10/ideas_I.md I-5, the audit table):
    #  (i) compute_quote never prices the side REDUCING this exchange's position below value: a long's ask >=
    #      p - value_sell_margin, a short's bid <= p + value_sell_margin, in normal AND reduce-only quoting, after
    #      every skew (inventory, age), reduce_join_best, fast unload, reduce_from_book; it only moves that price AWAY
    #      from the other side, so it never crosses another trader (step 4 still runs after it) and never changes a
    #      size (never flips a position). The same floor is applied once more to decide's final quote. The
    #      max_skew_through clamp (skew never pays through fair value) also runs in reduce-only.
    #  (ii) the pre-close windows do nothing: no exit_hours_before_close taker exit (decide, ladder, status), no
    #      flatten_hours_before_close reduce-only, no flatten_per_market_hours per-market flatten, and every other
    #      check keyed on those windows (cancel urgency, no-chase, takes, arbitrage, hold takes) sees no window
    #      (Bot.close_window). stop_minutes_before_close still stops quoting before the close. The three settings are
    #      live-overridable too (0 = off) for a deploy that keeps value_mode off.
    #  (iii) exit_quote takes the same floor (value_floor) when given one, should the exit ever run again.
    #  (iv) warn_settings logs a WARNING (start-up and override time) for each mark-driven selling path left on with
    #      it: reduce_from_book, fast_unload_enabled. Not forced
    #      off: the owner decides
    #      (the floor (i) still holds for every resting quote they price).
    #  (v) Part B's allocator sells go through their own immediate-or-cancel path, never compute_quote: exempt.
    # False = unchanged.
    value_mode: bool = False
    value_sell_margin: float = 0.005     # how far below p a reducing ask may rest (above p a reducing bid)
    # A2 bloc_delta_enabled True (analysis/p10/ideas_H.md H-2): the national-swing cap measures the book's outcome
    # sensitivity to the party factor F (Gaussian copula) instead of counting YES shares. Per partisan contract (label
    # "Dem ..." / "Rep ...", independents 0) with a liquid p: $ per sd of F per YES share = sqrt(rho) x
    # phi(Phi^-1(p_dem)) x (1 - p_ind), p_dem = the Dem leg's p / (1 - p_ind) (p_ind = the race's other legs; a lone
    # market: its own p), rho = bloc_rho (bloc_rho_control in the headline control markets); sign + on Rep YES, - on
    # Dem YES, so bloc_delta = sum position x sensitivity is + when Republican-leaning, like party_delta. A race with
    # one partisan leg and an independent (no Dem-vs-Rep pair) counts 0, as H's model. One 50c share weighs 0.40, a
    # 0.5c longshot 0.014 (the share count weighs them the same). With the flag party_blocks / party_shift (and the
    # ladder's party room) use |bloc_delta| <= max_bloc_delta_frac x account instead of max_party_delta_frac x
    # account in shares; status.json bloc_delta, bloc_delta_frac (signed, / account); summary " | bloc delta X/sd".
    # Values from H-10: 0.05 (+-5k per sd at 100k) keeps P(final <= 85k) < 2% for the value core. False = unchanged.
    bloc_delta_enabled: bool = False
    bloc_rho: float = 0.45               # race-to-national-factor correlation (H: 0.25-0.65 moves Senate odds +-0.02)
    bloc_rho_control: float = 0.85       # the party-control markets (headline_races) follow the factor more closely
    max_bloc_delta_frac: float = 0.05    # |bloc_delta| cap, $ per sd of the national factor, x account value
    # A4 value_quote_hurdle > 0 (analysis/p10/ideas_I.md I-4), with a liquid p, for the side ADDING to this
    # exchange's position (a bid unless short, an ask unless long): outside the middle band (p below value_mid_low or
    # above value_mid_high) a YES bid never above p / (1 + h) and a YES ask never below 1 - (1 - p) / (1 + h), h =
    # this hurdle per $ of collateral held to the outcome (a fill there must beat what the capital earns elsewhere);
    # a hurdle price off the grid (bid < 0.5c, ask > 99.5c) -> that side is not quoted. CONSEQUENCE: in the tails only
    # favourite bids and longshot asks can rest near the book (the tournament prices favourites low and longshots
    # high: the other side's hurdle price sits far beyond the book, so it rests out of reach). In the middle band the
    # normal min_edge quoting applies instead, and the position here is capped at value_mid_inventory_quotes x the
    # quote size on the side that grows it (beyond it only the reducing side rests), so the cash rotates. The reducing
    # side is A1's (value_mode). 0 = off.
    value_quote_hurdle: float = 0.0
    value_mid_low: float = 0.15
    value_mid_high: float = 0.85
    value_mid_inventory_quotes: float = 2.0
    # A3 (ranges only, no new setting): worst_case_backstop_frac may be overridden up to 1.5. For a fully
    # collateralised outcome book the sum of per-race maxima is a gross-capital cap that protects only against every
    # race failing at once (P ~ 0 at the outcome; H-10: Monte Carlo q0.1% loss 22k vs the 79-112k it measures); 1.3-1.5
    # = effectively off, leaving max_worst_case_frac (R7, 0.35 for the value core) and the bloc-delta cap as the real
    # limits. max_drawdown_pct (the kill switch) stays out of OVERRIDABLE (house rule); 0.40 (H-10, kill at 60k marks)
    # is a code / deploy default change.
    # --- Package 10 B (analysis/p10/SPEC_P10.md Part B; everything OFF by default) ---
    # B1 alloc_enabled True, the capital allocator (analysis/p10/ideas_I.md I-1, I_alloc.py): capital goes where it
    # earns the most per $ held to the OUTCOME. Edge per $ with p = the liquid, race-scaled Polymarket price (read
    # within ALLOC_REF_MAX_AGE seconds): a held long (p - bid) / bid, a held short (ask - p) / (1 - ask) (what we
    # keep by NOT closing at the touch); a book level (top 3, our own orders stripped) bought (p - ask) / ask, sold
    # short (bid - p) / (1 - bid). Once per alloc_interval_s, on a cycle with a fresh cash read (< 5 min, the cash
    # gate's) and fresh books, Bot.alloc_plan pairs the lowest-edge holdings (edge-held <= alloc_max_edge_sell; never a
    # label in alloc_pin, a headline market unless alloc_headline, a market another feature traded this
    # cycle) with the highest-edge levels (edge >= alloc_min_edge_buy) while the gain is >= alloc_min_improvement per
    # $; cash above the reserve (B2) is a holding of edge 0 (bought with directly). Per market the new $ (with what
    # is held there, at p) <= alloc_max_contract_usd. Rotated $ (sales, plus buys paid from spare cash) <=
    # alloc_max_turnover_per_hour in any rolling hour. With bloc_delta_enabled (Part A) a pair is skipped if it would
    # leave |bloc_delta| above bloc_cap() and larger than before (off: no bloc check, logged once).
    # Sequence (Bot.alloc_tick, cycle step 6d, after the takes, before quoting; a run spans cycles): each SALE is an
    # immediate-or-cancel taker order at the touch (our orders there cancelled first; leftover cancelled at once;
    # a short is bought back only as a covered "sell NO", its NO+NO set part never), sent only while a fresh book of
    # its PAIRED buy market still shows that level within ALLOC_LEVEL_TOL and the edges still pass; then nothing is
    # bought until a cash read taken AFTER the sale (the next P&L read) shows the money above the reserve; then the
    # BUY goes as an immediate-or-cancel taker at the touch, only if a fresh book shows the level within
    # ALLOC_LEVEL_TOL at an edge >= alloc_min_edge_buy. A level gone -> that cash stays (in the reserve) and no more
    # sales this run. A position is never flipped (sales <= the position; no buy against a short or short against a
    # long); every order passes the cash gate. At most alloc_max_orders_per_cycle orders a cycle and alloc_writes_frac
    # of the writes left (3 per order). Allocator orders never go through compute_quote, so Part A's value floor
    # does not apply to them (marked "_alloc_paired", never sent). Dry run: the plan is logged, nothing sent.
    alloc_enabled: bool = False
    alloc_interval_s: float = 3600.0
    alloc_min_improvement: float = 0.03   # edge gained per $ rotated (both legs at the touch: net of the spread)
    alloc_min_edge_buy: float = 0.05
    alloc_max_edge_sell: float = 0.02
    alloc_pin: str = ""                   # comma-separated market labels ("Rep Ohio Senate, Dem ...") never sold
    alloc_max_turnover_per_hour: float = 15000.0
    alloc_max_orders_per_cycle: int = 4
    alloc_writes_frac: float = 0.3
    alloc_max_contract_usd: float = 10000.0
    # B2 alloc_mm_reserve: cash ($) the allocator leaves free for market making: it buys only with cash above it;
    # cash below it -> it first sells the lowest-edge holdings (edge-held <= alloc_max_edge_sell) to refill it, with
    # no buy (inside the same turnover cap).
    alloc_mm_reserve: float = 15000.0
    # B3 alloc_set_cost_per_usd > 0 (I-3): a race held NO on every leg (a NO+NO set; needs the Package 7 short-set
    # unwind in effect: pair_unwind_enabled, pair_no_unwind_max_cost >= 0, reduce_no_as_sell) is a holding of edge =
    # its unwind cost per $ freed = (asks sum - 1) / (legs - asks sum), ranked like any other when <= this. Chosen
    # (for the reserve or a paired buy), its race is REGISTERED with take_arbitrage's short-set unwind at that cost
    # (asks sum <= 1 + cost, instead of pair_no_unwind_max_cost) for ALLOC_SET_WAIT seconds: the existing plumbing
    # (covered NO sales on every leg, one batch, pair_no_unwind_max_per_cycle / _max_sets, follow-ups) does the
    # unwind; the paired buy waits for the sets to fall and a cash read after it. 0 = off (sets never ranked).
    alloc_set_cost_per_usd: float = 0.0
    # take_respect_reserve True: a stale-quote take (execute_take) is skipped when the cash it needs would leave less
    # than alloc_mm_reserve of cash free (cash_left - need < reserve), so the market-making reserve the allocator builds is
    # not spent by the takes first (P10 red team C-1: on the 3 Oct state the takes spent 12.7k of the 11.0k the sets freed in
    # 4 h; a take earns ~7.7% per $ once at the outcome, the reserve is meant to turn over). False = takes unchanged.
    take_respect_reserve: bool = False
    # --- Package 12 L (analysis/p11/SPEC_P12.md Part L; everything OFF by default) ---
    # L1 alloc_set_rich_leg True (with alloc_enabled; LIT_REVIEW A4, ANOM-2, MM-11): a NO+NO set is STOCK, not
    # something to unwind at a cost. Its legs are ranked one by one: in a race held NO on every leg (2+ legs, every
    # leg's p liquid), the leg with the highest p (the FAVOURITE; strictly highest, a tie = no ladder) is the rich leg
    # when its edge-held as a short ((ask - p) / (1 - ask), alloc_plan's own measure: NO_fav worth 1 - p ~ 2c, priced
    # 1 - ask ~ 7-12c on the tilted book) is <= alloc_max_edge_sell. The other legs (NO on the longshots, worth ~0.975,
    # priced ~0.90: edge-held positive) are NEVER sold by this feature. The rich leg's set part (nono_set_part, less
    # what our other covered NO sales there already sell) is sold as a RESTING LADDER of covered sales.
    # TERMS (the bot works in YES terms): selling NO on the favourite at NO price x = a YES BID on the favourite at
    # 1 - x, sent as the covered "sell NO @ 1 - b" (_no_sell, wire_order). Selling NO HIGH = bidding YES LOW. The
    # retail tilt flow BUYS longshot YES, i.e. (2-leg race) SELLS favourite YES - into favourite bids. So the ladder
    # = YES bids on the favourite at its best bid (other traders only: our own orders stripped) + each offset of
    # alloc_set_ladder: 0 = at the best bid (fills at today's tilt, NO sold at 1 - best bid), -0.02 / -0.04 = 2c / 4c
    # BELOW it (NO sold 2c / 4c higher: fill only in a late spike of longshot buying). Each level = 1/len(levels) of
    # the set part (whole shares; the remainder unsold). Every level: <= p + value_sell_margin (Part A1's floor for a
    # reducing bid: never a sale below the outcome value 1 - p, give or take the margin), at least a tick below our
    # lowest own ask there (resting or the quote's; and the quote's ask is kept above the ladder while it rests:
    # never a self-cross), on the grid. Sent through the cash gate as it is (cash_tiers: a covered sale of the set
    # part breaks the set, priced conservatively at 1.0 a share; a level the gate refuses is not sent and the ladder
    # waits: it is tried again next cycle, with no write spent until the gate allows it). The resting ladder is
    # re-quoted at most once an hour (ALLOC_LADDER_REQUOTE; queue position matters: an order still exactly at its
    # target keeps its place), lives MAX_ORDER_TTL, and is pulled at once when unsafe (the race no longer a set,
    # the leg not the favourite / not rich, a level above p + margin or at / above our own ask, more NO on sale than
    # the set part left, the pre-close window, pinned, the flag or the allocator off). The quoting engine leaves
    # the ladder's orders alone (plan_exchange), and a covered quote bid there sells only what the ladder does not.
    # A take / arbitrage / allocator IOC on that market cancels the ladder first, as any order of ours (re-placed
    # on the next re-quote). Proceeds are cash the allocator ranks as spare cash on its next run; with the flag the
    # B3 unwind (alloc_set_cost_per_usd) skips a race whose rich leg is laddered. Status: status.json
    # alloc.set_ladder {races, shares_resting, filled}. Dry run: the ladder is planned and logged, nothing sent.
    # P12 red team: a race that cannot be judged ("soft") keeps its ladder only while the rich leg's OWN p is known and
    # every level <= p + value_sell_margin (RT12-1); a ladder the exchange refuses whole is not re-sent for
    # ALLOC_LADDER_REFUSED_WAIT (RT12-3).
    alloc_set_rich_leg: bool = False
    alloc_set_ladder: tuple = (0.0, -0.02, -0.04)   # YES-price offsets from the favourite's best bid (<= 0)
    # L2 alloc_prefer_short True (LIT_REVIEW F5, CMP-1): in a 2-leg race whose best bids (other traders only) sum
    # above 1, the allocator does not buy YES on one leg while it can short the other (we hold no YES there): buying
    # A at ask_A and shorting B at bid_B pay the same (A wins) but 1 - bid_B < bid_A <= ask_A - same exposure, a better
    # price, less cash. The buy level is dropped (blocked_by "prefer_short") and the other leg's best bid is ranked as
    # a short level (its edge per $ is the higher one). Races of 3+ legs: unchanged (no single-leg equivalent).
    alloc_prefer_short: bool = False
    # L3 pair_no_unwind_asks_le1 True (LIT_REVIEW F7, CMP-11): the NO+NO pair unwind at a cost (pair_no_unwind_max_cost)
    # fires only while the race's best asks sum <= 1 + pair_no_unwind_max_cost (its own threshold; an allocator B3
    # registration keeps its own cost) AND the best bids (other traders' levels only, arb_levels) do NOT sum above 1:
    # then the set is worth more sold leg by leg (L1) than bought back at the asks. False = unchanged.
    # P12 red team: the allocator plans no B3 set unwind this gate would refuse (blocked_by "set_bids_gt_1", RT12-2),
    # and no unwind at a cost (asks sum > 1) while our L1 ladder rests in the race (RT12-6).
    pair_no_unwind_asks_le1: bool = False
    # --- Package 12 M (analysis/p11/SPEC_P12.md Part M; LIT_REVIEW F1 part 1 and B1) ---
    # M1 close_override_utc (LIT_REVIEW F1, lit_electionnight.md): load_markets sets each market's close to
    # min(settlementDate, the tournament's endDate) = 4 Nov 00:00 UTC, so stop_minutes_before_close 15 stops the bot
    # at 23:45 UTC on 3 Nov whatever SIG allows. An ISO UTC time here ("2026-11-04T17:00:00Z" = 12:00 pm ET on 4 Nov,
    # live range 2026-11-01 .. 2026-11-07) makes every market's EFFECTIVE close max(API close, this time): it can only
    # EXTEND a close, never shorten one (a market with no API close stays "never closes"). Bot.hours_to_close uses it,
    # so the stop, the pre-close windows (close_window: exit / flatten / per-market flatten, the take / arbitrage /
    # allocator "closing" checks, the phase line) all follow. Nothing else changes (no election-night
    # taking). "" = off (the API close, as before). An invalid
    # time (unparseable, no time zone, outside the range) is ignored (the API close) with one alert.
    close_override_utc: str = ""
    # M2 skew_target_inventory True (LIT_REVIEW B1; lit_marketmaking.md MM-1 / MM-2): the "informed market maker"
    # skew of Bergault-Guéant (2021) / Fodra-Labadie (2012): the inventory skew in compute_quote is measured from the
    # distance to a TARGET holding, not from flat. target = Bot.alloc_target_for(eid): the allocator's latest plan's
    # intended holding for the market (alloc_enabled), else the current holding when its edge-held > 0 in value_mode
    # (a +EV position we hold to the outcome), else 0 (as before). The race netting is the same as eff_inv's, applied
    # to (inv - target). With value_mode on, age_skew is 0 for a holding with edge-held > 0. The quote still never
    # crosses (step 4 of compute_quote) and the reducing side still keeps the value_mode floor (value_floor_quote).
    # P12 red team: never in reduce-only (global_reduce / the flatten window: the skew from flat, RT12-4); a target
    # that is only the holding (no allocator plan for the market) leaves the ADDING side its skew from flat (RT12-7:
    # else every +EV holding - edge-held > 0 whenever p is above the bid - would bid on unskewed to the hard limit).
    skew_target_inventory: bool = False
    # --- P12 ops: market-making risk reserve (owner, 4 Oct; everything OFF by default) ---
    # alloc_mm_reserve keeps CASH for market making, but twice on 4 Oct value buying (takes, value quotes, the
    # allocator) filled the worst-case backstop and the bot went reduce-only with the cash reserve idle. These keep
    # RISK room instead. Each cycle (step 6, beside the reduce-only decision, which they never change):
    #   room_wc   = worst_case_backstop_frac x account - total_worst_case (the sum of per-race maxima)
    #   room_corr = max_worst_case_frac x account - the settlement risk the cap compares (correlated: min(worst,
    #               settlement_risk); "sum": the worst case)
    # mm_risk_reserve_wc > 0 and room_wc below it, OR mm_risk_reserve_corr > 0 and room_corr below it -> "value adds
    # paused": no stale-quote take that grows a position (execute_take: only the part that shrinks one), no allocator
    # BUY (alloc_plan / alloc_buy / a paired sale whose buy could not follow; reserve refills and other sales go on),
    # and in the TAILS (the liquid race-scaled p, else the fair value,
    # outside [value_mid_low, value_mid_high]) the side ADDING to this exchange's position quotes only what shrinks it
    # (also the R3 ladder's caps); a resting tail add is dropped by the next re-quote (bid_max / ask_max follow).
    # The MIDDLE band keeps its two-way quotes (adding within value_mid_inventory_quotes as before) and every
    # reducing side and arbitrage is untouched. It lifts once every room set
    # is back to >= 1.1 x its reserve (MM_RISK_HYST). status.json mm_risk_room {room_wc, room_corr, paused, since,
    # reserve_wc, reserve_corr, blocked {takes, alloc, tail_quotes}}; journal "VALUE ADDS PAUSED ..." /
    # "value adds resumed ..."; summary " | risk room wc Xk corr Yk (paused)". 0 = off (that room is not checked).
    mm_risk_reserve_wc: float = 0.0
    mm_risk_reserve_corr: float = 0.0
    # --- P14: market-making funding (owner, 4 Oct 21:10 UTC: "keep market making FULLY FUNDED at all times"; OFF) ---
    # MM INVENTORY = what the middle-band two-way book left us holding. Tracked always (read-only, Bot.mm_inv_step,
    # cycle step 4): every new fill of one of our RESTING quotes (order note: not take / arb / alloc / set ladder)
    # whose market's p at fill (the race-scaled liquid Polymarket price, mm_carry_24h's; else the fair value
    # when quoted) lies in [value_mid_low, value_mid_high] is an MM fill: it first closes opposite MM lots of that
    # market (FIFO: a round trip), the rest opens a lot [signed shares, YES price, wall time] only in the direction
    # the position now has. Lots never exceed the position (shrunk oldest first, dropped on a flip / flat). Kept in
    # status.json mm_funding.lots (restored at start); with no such key the first cycle seeds them from fills.csv's
    # last 24 h (the order notes' horizon), p = the row's fv_at_quote. A market's MM inventory is STALE when a lot is
    # older than mm_inv_max_age_h, or its $ at p (long q x p, short |q| x (1 - p)) exceeds mm_inv_max_usd (0 = no $
    # limit): stale shares = max(the aged lots, the shares over the $ limit).
    # 1. mm_recycle_enabled True: the stale shares are worked out FIRST, through the quoter itself (decide, after
    #    before the value floor): the REDUCING side of that market is moved in to fair -
    #    mm_recycle_concession (long; fair + it for a short), never crossing the best other bid / ask, never past the value floor in value
    #    mode (value_floor_quote runs after it), sized max(the quoter's, the stale shares) <= the position; our adding
    #    side is pulled a tick behind it and otherwise keeps quoting. One order per side as ever (the quoter's reduce
    #    side IS the recycler's order: no duplicate); a side the quoter left out (a guard, the cooldown) stays out.
    #    If the market's edge-held (alloc_edge_held) >= value_quote_hurdle (0: alloc_min_edge_buy) the stale MM
    #    inventory is VALUE: its lots are handed to the value bucket (no longer MM, not recycled), and the refill (2)
    #    sells the lowest-edge value positions instead. Journal "MM RECYCLE ..." lines; fills of a recycled side are
    #    class "recycle" in mm_carry_24h (key present only once such a fill exists).
    # 2. mm_refill_fast True: with a fresh cash read showing the cash gate's free cash below alloc_mm_reserve, the
    #    allocator's reserve refill (B2, alloc_plan / alloc_sell) runs on the NEXT cycle (at most every MM_REFILL_GAP
    #    s), not only at the hourly run, inside alloc_max_turnover_per_hour and the allocator's write share, in this
    #    order: stale MM inventory (an IOC at the best bid when fair - bid <= mm_recycle_concession and the bid is not
    #    below the floor; else it rests through (1)), then value positions lowest edge-held first; every refill sale
    #    (hourly ones too) at or above the value floor p - value_sell_margin (shorts p + margin). A market sold this
    #    way is not planned again until the positions read shows the sale (or MM_SENT_LAG s: red team RT13-3).
    # 3. mm_room_guard True: mm_risk_reserve_* counts the MM inventory's own worst-case / correlated contribution
    #    (the risk with vs without the MM lots) against the MM room first: the pause is decided on the value book's
    #    share, room + min(MM contribution, reserve), with the same MM_RISK_HYST hysteresis; value buying (allocator
    #    buys, takes, tail adds) stays paused until that is back; the recycler and refills never pause.
    # 4. Monitoring (always written, read-only): status.json mm_funding {cash_free, cash_target, room_free, room_target,
    #    inventory_usd, oldest_inventory_h, stale_markets, recycling, handed_to_value, below_half_since, ...}; with any
    #    of 1-3 on, ONE alert when cash or a room has stayed below 50% of its target for > mm_funding_alert_h (re-armed
    #    once all are back above half) and a "MM funding ..." piece on the 2-hourly summary line.
    mm_recycle_enabled: bool = False
    mm_inv_max_age_h: float = 6.0
    mm_inv_max_usd: float = 3000.0
    mm_recycle_concession: float = 0.01
    mm_refill_fast: bool = False
    mm_room_guard: bool = False
    mm_funding_alert_h: float = 2.0
    # --- P14.1: refill and swaps unstuck (owner, 5 Oct 10:30 UTC; analysis/p14/DIAG_14_1.md; everything OFF) ---
    # Diagnosis on 4ff7d91: the fast refill is starved by PRICE (in a tilted book almost no holding's touch is within
    # value_sell_margin of p), a run in flight locks the fast refill out, and the risk-room pause drops every buy level
    # (no swap). Each flag below is independent; all off = 4ff7d91 byte for byte (orders, quotes, status values).
    # 1. alloc_cancel_mm_first True: stale MM shares are refill candidates like any holding, judged by the refill's
    #    price rule only (the value floor, or alloc_refill_max_cost below half the target) - no longer refused for being
    #    more than mm_recycle_concession from fair ("mm_resting" is no blocker; a refused one is counted "floor"). The
    #    sale is the allocator's IOC (alloc_send: our orders there cancelled first - ONE whole-exchange cancel
    #    - then the IOC, same cycle, the market not quoted that cycle), and after ANY allocator sale the market's
    #    REDUCING quote side is held off (a "refill pending" hold) until the positions read shows the sale or
    #    MM_SENT_LAG s pass: the quoter never re-offers shares already sold (live: an ask beyond the YES held is a NO
    #    purchase). The sold market is not planned again meanwhile (RT13-3, now for every allocator sale).
    # 2. alloc_rank_all_markets True: refill candidates are ALL holdings (not only edge-held <= alloc_max_edge_sell:
    #    that stays the swaps' rule), ranked lowest edge-held first, on the cached book when the fresh one is older
    #    than book_stale (alloc_sell downloads a fresh book before each sale anyway); a refused candidate is skipped and
    #    counted, the run goes on (a paired level gone stops only the paired sales, never the refills). The fast refill
    #    runs EVERY cycle (not at most every MM_REFILL_GAP s) while free cash < alloc_mm_reserve, also while an hourly
    #    run's pairs are in flight (their markets skipped), inside alloc_max_turnover_per_hour and the write share.
    # 3. mm_recycle_sell_first True: recycled sales (the ask on a long: frees cash) are sent before the other changes
    #    and recycled buy-backs (the bid on a short) after them; a buy-back is judged by its NET cash: the gate's need
    #    (covered lone NO 0, a NO+NO set part 1.0 a share, an uncovered YES bid its price) less the (1 - price) a share
    #    its fill frees. While free cash < 0.5 x alloc_mm_reserve, the buy-back shares that lock more than they free are
    #    deferred (the recycler sizes the bid to the rest; counted in mm_funding.deferred_buybacks).
    # 4. alloc_refill_ignore_prefer_short True: while free cash < 0.5 x alloc_mm_reserve the fast refill skips the L2
    #    prefer-short scan (and the buy-level scan): the refill's blocked_by then holds only reasons that stop a SALE.
    #    (In 4ff7d91 L2 only ever dropped buy levels - never a refill sale - but its count sat in that blocked_by.)
    # 5. alloc_swap_room_netting True: while value adds are paused (mm_risk_reserve_*), the allocator still plans and
    #    executes SWAPS (sell low edge-held, buy high edge): a pair is admitted when, after its sale AND its buy, each
    #    risk room (worst case, correlated; the cycle's own measures) is >= min(the room now, its reserve) - a swap may
    #    never take a room below the reserve net of its own sale; re-checked before the sale and before the buy. Its buy
    #    may spend its own sale's proceeds even while cash is below alloc_mm_reserve (never below the cash there was
    #    before the sale: the MM cash is not touched). Refills, takes and tail adds keep the pause as before.
    # 6. alloc_refill_max_cost > 0: while free cash < 0.5 x alloc_mm_reserve a REFILL sale (no buy) may go up to this
    #    far below p (a long's bid >= p - it, a short's buy-back <= p + it) instead of value_sell_margin, lowest cost
    #    first. 0 = the value floor (as 4ff7d91). The EV given up is reported (mm_funding.refill_ev_given_24h).
    # 7. Reporting (always, read-only, MM_FUNDING_KEYS): mm_funding {refill_runs, refill_sold_usd (24 h), refill_last,
    #    refill_ev_given_24h, deferred_buybacks, cash_locked}; with any 14.1 flag on: alloc {swaps_planned,
    #    swaps_usd_24h, ev_gain_est_24h, ev_gain_realised_24h (from the IOC fills: qty x (p - price) bought, (price - p)
    #    sold, summed per pair), refill_blocked_by} and the 2-hourly summary piece " | refill ..., swaps ...".
    alloc_cancel_mm_first: bool = False
    alloc_rank_all_markets: bool = False
    mm_recycle_sell_first: bool = False
    alloc_refill_ignore_prefer_short: bool = False
    alloc_swap_room_netting: bool = False
    alloc_refill_max_cost: float = 0.0
    # --- P14.2: a swap-only value-floor margin (owner, 5 Oct 13:20 UTC; analysis/p14/DIAG_14_2.md; OFF) ---
    # Diagnosis on 4ff7d91 / 124ce75: value_sell_margin never floored an allocator SWAP sale (a paired sale was judged
    # by alloc_max_edge_sell and alloc_min_improvement only); raising it only let more REFILLS (no buy) and stale-MM
    # IOCs through, rested every reducing quote / the recycler lower, and in 14.1 SHRANK the swaps (the refill sells
    # the low-edge holdings first; stale-MM holdings at the floor become refill-only legs).
    # alloc_swap_sell_margin > 0: a SWAP (a long / short sale paired with a buy in the same run) gets its OWN price
    #    limit: the sale at the touch >= p - it (a short's buy-back <= p + it), checked when pairing (alloc_plan: a
    #    holding beyond it is no swap candidate, blocked_by "swap_floor") and again on the fresh book right before the
    #    IOC (alloc_sell; the IOC goes out at that very touch). alloc_max_edge_sell still picks the candidates (both
    #    apply). Every other reducing path keeps value_sell_margin exactly: quotes and reduce-only quotes, the
    #    recycler, refills (no buy), stale-MM IOCs, the set ladder, exits; NO+NO set and spare-cash pairs keep their own
    #    rules. A swap whose buy fails after its sale: the sale stands as traded (an IOC: nothing left to reprice), the
    #    cash stays, the market's quotes keep value_sell_margin. 0 = off (as 124ce75).
    # alloc_swap_min_gain (only with the margin on): a swap is admitted only when buy edge - sale edge-held (both at
    #    the touch) >= max(alloc_min_improvement, alloc_swap_min_gain) per $, at planning and before the sale
    #    (blocked_by "swap_gain"). The sale's cost per $ it frees, (p - price) / price for a long ((price - p) /
    #    (1 - price) for a short's buy-back), IS its edge-held at that price, so it is counted once, not twice.
    # Reporting (with the margin on): "ALLOC SWAP sold ... -> buy ...: net EV gain" journal lines at plan and at fill
    #    (realised at p), status.json alloc.swaps {last_run, counts_24h, usd_24h, ev_gain_est_24h,
    #    ev_gain_realised_24h}, and a WARNING while value_sell_margin > 0.01 (the swap margin makes a raised one moot).
    alloc_swap_sell_margin: float = 0.0
    alloc_swap_min_gain: float = 0.05
    # --- P15: the harvest ladder alone + a per-state collateral cap (owner, 6 Oct 11:00 UTC; everything OFF) ---
    # Package 13's harvest ladder (1fdd467, analysis/p12/SPEC_P13_AGGRESSIVE.md section 4) ported ALONE onto 14.2:
    # no momentum sleeve / buckets / election holdback / aggr_* caps here.
    # 1. tilt_harvest_ladder True: sell the tilt as a MAKER (Bot.hv_tick, cycle step 6e, after the allocator). Every
    #    market with a fresh liquid race-scaled p (alloc_p) that is not a headline (party-control) market
    #    (unless alloc_headline) or a P12 set-ladder market: a LONGSHOT (p <= 0.10) gets resting YES ASKS at its best
    #    other ask + each harvest_offsets, a FAVOURITE (p >= 0.90) resting YES BIDS at its best other bid - each
    #    offset; a level only where its edge per $ of collateral clears harvest_min_edge ((ask - p) / (1 - ask),
    #    (p - bid) / bid), never at / through the other side of the book, never at / through our own orders there and
    #    never at the price of our own resting quote on that side (skipped, never clipped); each harvest_level_usd of
    #    collateral (an ask sells the YES held first - covered - then is a short: 1 - price a share; a bid where we
    #    hold NO goes out as a covered "sell NO" of what is held, cash-free). Caps: the market's position at p + the
    #    ladder's adds <= alloc_max_contract_usd (the per-market $ cap the allocator's value buys keep), the state cap
    #    (3), the carve-out (2), the cash gate keeping the MM's effective reserve, no adds while value adds are paused
    #    (mm_risk_reserve_*: the ladder's levels are tail adds - only covered ones), at most harvest_max_markets markets
    #    (markets already laddered first, then the best edge at the touch) and harvest_writes_frac of the cycle's
    #    writes left (pulls always), batch_size orders a write. Resting MAX_ORDER_TTL, tagged "harvest" in the order
    #    notes, hidden from the quote planner (plan_exchange: the quote's bid kept a tick below our harvest asks); the
    #    market maker does not quote the ladder's side on a laddered market except what reduces a position, net of the
    #    harvest levels on that side (P13 RT13-2). Re-quoted when a level is > 1c off its target or p moved > 1c, else
    #    at most every harvest_requote_s (an order exactly at its target keeps its queue spot); a level gone is
    #    re-placed at once. Pulled at once when p is unknown, reduce-only (the tripwire), the pre-close stop or the
    #    flag goes off (the kill switch cancels everything). A batch whose outcome is unknown (timeout / 409 / 5xx):
    #    its levels are adopted from the next orders read (P13 RT13-1), never placed twice. Fills are VALUE positions
    #    (held to the outcome; the value floor protects them): journal "HARVEST fill ...", fill class "harvest" in
    #    mm_carry_24h (key only once one exists), status.json "harvest", a " | harvest ..." summary piece.
    # 2. harvest_total_usd: the ladder's collateral budget, CARVED FROM alloc_mm_reserve. It is a budget for RESTING
    #    collateral (the cash our resting harvest levels lock). The MM's own reserve is alloc_mm_reserve -
    #    harvest_total_usd (mm_reserve_effective: 20k - 10k = 10k live): the ladder places only through the cash
    #    gate with that much held back, so it never takes the MM's cash below it; and the quotes' plan budget leaves
    #    the ladder what it plans but has not placed yet (hv_quote_hold: the MM never eats the ladder's part either,
    #    nothing is held while the ladder plans no more). The whole reserve is still refilled:
    #    the refill / swaps / spare-cash buys / take_respect_reserve count the cash locked in resting harvest levels
    #    (up to harvest_total_usd) as part of the reserve (free cash + ladder resting >= alloc_mm_reserve), so the
    #    refill target stays alloc_mm_reserve. A FILLED level is a value position: its collateral is value collateral
    #    (the state cap, alloc_max_contract_usd) and it frees its budget slot for the next level - the ladder recycles
    #    its budget into fills, the refill tops the reserve back up.
    # 3. state_max_usd > 0: a per-STATE collateral cap (state_of(label): "Rep Rhode Island Senate", "Dem RI-01 House
    #    race" -> "RI"; the headline U.S. House / Senate markets have no state). Collateral = longs q x p, shorts |q| x
    #    (1 - p) (p the liquid race-scaled Polymarket price, else the exchange's mark, else the fair value) + the cash
    #    our resting orders there lock. Existing positions are KEPT (never a forced reduce); ADDS stop at the cap: the
    #    harvest ladder, the allocator's buys (swaps, spare cash; blocked_by "state_cap"), the stale-quote takes and
    #    the quoter's adding side in the tails (outside value_mid_low..value_mid_high; the R3 ladder too). status.json
    #    state_caps {state over 50% of the cap: {collateral, cap, blocked_adds}}. 0 = off.
    tilt_harvest_ladder: bool = False
    harvest_offsets: tuple = (0.0, 0.02, 0.04, 0.06)   # YES-price offsets away from the touch (asks up, bids down)
    harvest_level_usd: float = 3000.0     # collateral per level, $
    harvest_min_edge: float = 0.08        # edge per $ of collateral a level must clear
    harvest_max_markets: int = 237        # laddered markets, at most
    harvest_writes_frac: float = 0.4      # placements / re-quotes a cycle <= this x the writes left (pulls: always)
    harvest_requote_s: float = 900.0      # a ladder is re-quoted at most this often unless a level / p moved > 1c
    harvest_total_usd: float = 10000.0    # the ladder's resting collateral budget, carved from alloc_mm_reserve
    state_max_usd: float = 0.0            # per-state collateral cap, $ (0 = off; staged 15,000)


CFG = Config()

# Settings that may be changed while the bot runs (settings_override.json), with their allowed range. Never
# secrets, URLs, file names, the kill switch or anything read only at start-up.
OVERRIDABLE = {
    "min_edge": (0.0, 0.10), "max_half_spread": (0.005, 0.20), "skew_per_share": (0.0, 0.001),
    "reprice_tolerance_ticks": (0, 10), "keep_fraction": (0.0, 1.0),
    "order_size_frac": (0.0, 0.05), "size_min_frac": (0.0, 0.05), "size_max_frac": (0.0, 0.10),
    "headline_size_frac": (0.0, 0.20), "headline_position_frac": (0.0, 0.30), "quote_capital_frac": (0.0, 1.0),
    "max_position_frac": (0.0, 0.10), "max_party_delta_frac": (0.0, 0.50), "party_skew_at_cap": (0.0, 0.05),
    "max_worst_case_frac": (0.05, 0.60), "kelly_fraction": (0.0, 1.0), "kelly_max_market_frac": (0.0, 0.10),
    "ref_weight": (0.0, 1.0), "ref_guard_gap": (0.02, 0.30), "ref_jump_threshold": (0.005, 0.30),
    "ref_jump_cooldown_seconds": (0.0, 3600.0), "jump_threshold": (0.01, 0.50), "jump_cooldown_seconds": (0.0, 3600.0),
    "arb_enabled": (False, True), "arb_min_profit": (0.005, 0.20), "take_enabled": (False, True),
    "take_edge": (0.02, 0.30), "tail_low": (0.0, 0.20), "tail_high": (0.80, 1.0),
    "requests_per_minute": (10, 100), "writes_per_minute": (5, 100), "budget_reserve": (0, 60),
    "writes_per_minute_max": (5, 100), "startup_writes_per_minute": (0, 100), "write_budget_cut": (0.25, 1.0), "never_defer_unsafe": (False, True),
    "max_books_per_cycle": (1, 100), "book_stale": (60.0, 3600.0), "book_reverify_seconds": (10.0, 1800.0),
    "parallel_writes": (1, 8), "write_wait_seconds": (0.0, 30.0), "main_write_wait_margin": (0.0, 60.0),
    "urgent_writes_per_cycle": (0, 500), "pause_skip_cycles": (False, True),
    "watchdog_alert_seconds": (0.0, 3600.0), "watchdog_exit_seconds": (0.0, 7200.0), "watchdog_cancel_seconds": (1.0, 120.0),
    "churn_control": (False, True), "min_quote_life_seconds": (0.0, 120.0), "churn_max_reprices": (1, 100),
    "churn_window_seconds": (5.0, 3600.0), "urgent_ref_move": (0.0, 0.10),
    "burst_protection": (False, True), "burst_write_seconds": (0.5, 60.0), "burst_cycle_seconds": (2.0, 600.0),
    "burst_timeouts": (1, 100), "burst_calm_seconds": (0.0, 3600.0), "burst_markets": (1, 300),
    "burst_size_factor": (0.05, 1.0), "burst_extra_edge": (0.0, 0.05), "burst_startup_grace_seconds": (0.0, 600.0),
    "slow_cycle_alert_seconds": (10.0, 3600.0), "summary_every_hours": (0, 24),
    "churn_count_sent": (False, True),
    "positions_stale_max_cycles": (0, 100),
    "positions_stale_max_seconds": (0.0, 3600.0),
    "handover_exit_max_seconds": (0.0, 600.0),
    "reduce_only_hysteresis": (0.0, 0.1),
    "pulls_cancel_all_over": (0, 500),
    # Quoting (R4, skew), thin-book pricing (R5), risk (R7), order lifecycle. All read from cfg where used, every
    # cycle, so a change applies on the next cycle (refresh_before_expiry is checked against order_ttl below).
    "skew_per_quote": (0.0, 0.05), "skew_max": (0.0, 0.1), "max_skew_through": (0.0, 0.05),
    "improve_ticks": (0, 3), "undercut_step_back": (0.0, 0.05),
    "ref_only_enabled": (False, True), "ref_only_max_gap": (0.005, 0.2), "ref_only_min_edge": (0.0, 0.1),
    "ref_only_size_frac": (0.0, 0.02), "ref_only_reduce_full": (False, True),
    "risk_swing_shock": (0.05, 0.5), "risk_z": (1.0, 6.0), "worst_case_backstop_frac": (0.3, 1.5),   # (P10 A3: 1.5 ~ off)
    "risk_unheld_legs": ("half", "ref"),
    "order_ttl": (300.0, 7200.0), "refresh_before_expiry": (30.0, 900.0), "batch_size": (1, 50),
    "kelly_no_edge_frac": (0.0, 0.01), "take_ref_max_age_seconds": (0.0, 300.0),
    "arb_two_sided": (False, True),
    "arb_min_profit_buy": (0.005, 0.20),
    "arb_buy_min_sum": (0.5, 1.0),
    "arb_buy_min_ref_sum": (0.8, 1.0),
    "pair_unwind_enabled": (False, True),
    "pair_unwind_min_profit": (0.0, 0.10),
    "pair_unwind_max_frac": (0.0, 0.10),
    "pair_unwind_cooldown_seconds": (0.0, 3600.0),
    "limits_use_race_net": (False, True),
    "skew_age_after_hours": (0.0, 48.0),
    "capital_in_positions_max_frac": (0.0, 1.0),
    "capital_ceiling_adding_size_factor": (0.0, 1.0),
    "ref_only_use_tops": (False, True),
    "tops_max_age": (5.0, 900.0),
    "startup_books_first": (False, True),
    "startup_prime_seconds": (0.0, 1800.0),
    "startup_prime_missing_frac": (0.0, 1.0),
    "startup_prime_books": (1, 100),
    "startup_prime_reserve": (0, 60),
    "startup_prime_books_per_min": (1, 100),
    "startup_prime_held_max_seconds": (0.0, 3600.0),
    "unpriced_held_warn_cycles": (0, 1000),
    "fl_bias_enabled": (False, True),
    "fl_low": (0.0, 0.50),
    "fl_high": (0.50, 1.0),
    "fl_hysteresis": (0.0, 0.05),
    "fl_bad_side_extra_edge": (0.0, 0.05),
    "fl_bad_side_size_factor": (0.0, 1.0),
    "fl_mid_bid_extra_edge": (0.0, 0.03),
    "reduce_join_best": (False, True),
    "reduce_join_min_edge": (0.0, 0.05),
    "reduce_join_min_shares": (0, 100000),
    "market_edge_enabled": (False, True),
    "market_edge_max": (0.005, 0.05),
    "turnover_window_hours": (0.5, 48.0),
    "turnover_min_shares_per_hour": (0.0, 100000.0),
    "turnover_use_tape": (False, True),
    "turnover_alive_shares_per_hour": (0.0, 100000.0),
    "turnover_min_state_minutes": (0.0, 1440.0),
    "mark_frag_max_step_cash": (1.0, 100000.0),
    "mark_frag_window_hours": (1.0, 168.0),
    "mark_frag_min_samples": (2, 100000),
    "mark_frag_floor_sd": (0.0005, 0.10),
    # --- Package 5: T2.1 tilt-corrected reference ---
    "ref_tilt_min_markets": (5, 1000),
    "ref_tilt_halflife_min": (0.5, 1440.0),
    "ref_tilt_max": (0.0, 0.3),
    "ref_tilt_winsor": (0.005, 0.3),
    "ref_tilt_estimator": ("slope", "median", "wls"),     # ONE of these (ONE_OF_SETTINGS)
    # --- Package 5: B kelly_edge_cap, A reduce_from_book ---
    "kelly_edge_cap": (0.0, 0.10),
    # --- Package 5: T2.4 tilt exposure limit ---
    # --- Package 5: X11 reduce_from_book scope ---
    # --- Package 5: X12 takes measured from the tilted reference ---
    # --- Package 5: T2.1 ramp-in ---
    # --- Package 6: reduce NO holdings as covered NO sales ---
    "reduce_no_as_sell": (False, True),
    # --- Package 7: NO+NO sets ---
    "no_set_aware_bids": (False, True),
    "pair_no_unwind_max_cost": (-1.0, 0.05),
    # --- Package 7: pair unwind follow-up ---
    "pair_unwind_followup": (False, True),
    "pair_unwind_followup_max_cost": (0.0, 0.05),
    "pair_unwind_followup_tries": (1, 50),
    "pair_no_unwind_max_per_cycle": (1, 10),
    # --- Package 8: cash gate and per-market adding side ---
    "cash_gate_enabled": (False, True),
    "cash_gate_reserve": (0.0, 10000.0),
    "adding_factor_per_market": (False, True),
    # --- Package 8 item 2 ---
    "pair_no_unwind_max_sets": (0, 100000),
    "pair_unwind_race_order": (False, True),
    "pair_unwind_followup_max_age": (0.0, 7200.0),
    # --- Package 8 ---
    "adding_factor_capital_on": (0.0, 1.0),
    "capital_ceiling_adding_size_factor_resume": (0.0, 1.0),
    # --- Package 9 F5 ---
    # --- Package 10 A ---
    "value_mode": (False, True),
    "value_sell_margin": (0.0, 0.05),
    "exit_hours_before_close": (0.0, 48.0),      # (0 = no election-night taker exit)
    "flatten_hours_before_close": (0.0, 48.0),   # (0 = no flatten reduce-only window)
    "flatten_per_market_hours": (0.0, 12.0),
    "bloc_delta_enabled": (False, True),
    "bloc_rho": (0.1, 0.9),
    "bloc_rho_control": (0.1, 0.95),
    "max_bloc_delta_frac": (0.01, 0.5),
    "value_quote_hurdle": (0.0, 0.5),
    "value_mid_low": (0.0, 0.5),
    "value_mid_high": (0.5, 1.0),
    "value_mid_inventory_quotes": (0.0, 20.0),
    # --- Package 10 B ---
    "alloc_enabled": (False, True),
    "alloc_interval_s": (300.0, 86400.0),
    "alloc_min_improvement": (0.005, 0.5),
    "alloc_min_edge_buy": (0.0, 0.5),
    "alloc_max_edge_sell": (0.0, 0.5),
    "alloc_pin": (0, 4000),          # free text: comma-separated labels, at most 4000 characters (FREE_TEXT_SETTINGS)
    "alloc_max_turnover_per_hour": (0.0, 200000.0),
    "alloc_max_orders_per_cycle": (1, 20),
    "alloc_writes_frac": (0.0, 1.0),
    "alloc_max_contract_usd": (0.0, 100000.0),
    "alloc_mm_reserve": (0.0, 100000.0),
    "alloc_set_cost_per_usd": (0.0, 0.2),
    "take_respect_reserve": (False, True),
    # --- Package 12 L ---
    "alloc_set_rich_leg": (False, True),
    "alloc_set_ladder": (-0.2, 0.0),     # a list of 1..LADDER_MAX_LEVELS offsets, each in -0.2..0
    "alloc_prefer_short": (False, True),
    "pair_no_unwind_asks_le1": (False, True),
    # --- Package 12 M ---
    "close_override_utc": ("2026-11-01T00:00:00Z", "2026-11-07T00:00:00Z"),   # ISO UTC time (DATE_SETTINGS), "" = off
    "stop_minutes_before_close": (0.0, 120.0),
    "skew_target_inventory": (False, True),
    # --- P12 ops: market-making risk reserve ---
    "mm_risk_reserve_wc": (0.0, 50000.0),
    "mm_risk_reserve_corr": (0.0, 50000.0),
    # --- P14: market-making funding ---
    "mm_recycle_enabled": (False, True),
    "mm_inv_max_age_h": (0.5, 168.0),
    "mm_inv_max_usd": (0.0, 50000.0),
    "mm_recycle_concession": (0.0, 0.05),
    "mm_refill_fast": (False, True),
    "mm_room_guard": (False, True),
    "mm_funding_alert_h": (0.25, 24.0),
    # --- P14.1: the refill and the swaps unstuck ---
    "alloc_cancel_mm_first": (False, True),
    "alloc_rank_all_markets": (False, True),
    "mm_recycle_sell_first": (False, True),
    "alloc_refill_ignore_prefer_short": (False, True),
    "alloc_swap_room_netting": (False, True),
    "alloc_refill_max_cost": (0.0, 0.05),
    # --- P14.2: a swap-only value-floor margin ---
    "alloc_swap_sell_margin": (0.0, 0.10),
    "alloc_swap_min_gain": (0.0, 0.5),
    # --- P15: the harvest ladder alone + a per-state collateral cap ---
    "tilt_harvest_ladder": (False, True),
    "harvest_offsets": (0.0, 0.2),       # a list of 1..LADDER_MAX_LEVELS offsets, each in 0..0.2
    "harvest_level_usd": (0.0, 50000.0),
    "harvest_min_edge": (0.0, 2.0),
    "harvest_max_markets": (0, 1000),
    "harvest_writes_frac": (0.0, 1.0),
    "harvest_requote_s": (60.0, 7200.0),
    "harvest_total_usd": (0.0, 100000.0),
    "state_max_usd": (0.0, 200000.0),
}
P141_REFILL = ("alloc_cancel_mm_first", "alloc_rank_all_markets",   # P14.1: the flags that need
               "alloc_refill_ignore_prefer_short")                 #  mm_refill_fast to do anything
MM_RISK_HYST = 1.1        # mm_risk_reserve_*: value adds resume once each room set is >= this x its reserve
MAX_ORDER_TTL = 7200.0    # no order of ours lives longer than this (dead-man's switch), whatever the TTL settings
LADDER_MAX_LEVELS = 8
ONE_OF_SETTINGS = {"ref_tilt_estimator", "risk_unheld_legs"}   # string settings that take exactly one of their OVERRIDABLE names
DATE_SETTINGS = {"close_override_utc"}     # string settings: one ISO UTC time within their range
DATE_EMPTY_OK = {"close_override_utc"}     # ...that also take "" (= off)
FREE_TEXT_SETTINGS = {"alloc_pin"}         # string settings that take any text of (min, max) characters


def parse_utc_setting(v):
    """An ISO UTC time setting ("2026-10-18T12:00:00Z") -> aware datetime, or None if it is not one (no time zone
    counts as invalid: the exit date must never be read in the server's local time)."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def validate_overrides(raw, cfg):
    """{name: value} from the overrides file -> ({name: checked value}, [problems]). Unknown names, wrong types
    and out-of-range values are refused (and reported), never applied."""
    good, bad = {}, []
    if not isinstance(raw, dict):
        return good, ["the file must hold one JSON object, e.g. {\"min_edge\": 0.015}"]
    for k, v in raw.items():
        if k not in OVERRIDABLE:
            bad.append(f"{k}: not a live setting")
            continue
        cur = getattr(cfg, k)
        if k in DATE_SETTINGS:                             # one ISO UTC time in its range (close_override_utc)
            if k in DATE_EMPTY_OK and isinstance(v, str) and not v.strip():
                good[k] = ""                               # (close_override_utc "": off)
                continue
            dt, lo_s, hi_s = parse_utc_setting(v), *OVERRIDABLE[k]
            if dt is None or not parse_utc_setting(lo_s) <= dt <= parse_utc_setting(hi_s):
                bad.append(f"{k}: must be an ISO UTC time like \"2026-10-18T12:00:00Z\" in {lo_s}..{hi_s}")
                continue
            good[k] = v.strip()
            continue
        if k in FREE_TEXT_SETTINGS:                        # any text of bounded length (alloc_pin: market labels)
            lo_n, hi_n = OVERRIDABLE[k]
            if not isinstance(v, str) or not lo_n <= len(v) <= hi_n:
                bad.append(f"{k}: must be a text of at most {hi_n} characters")
                continue
            good[k] = v
            continue
        if isinstance(cur, str):                         # a comma list of allowed names (ladder_markets)
            names = [x.strip() for x in v.split(",")] if isinstance(v, str) else None
            if k in ONE_OF_SETTINGS:                       # exactly one allowed name (ref_tilt_estimator)
                if names is None or len(names) != 1 or names[0] not in OVERRIDABLE[k]:
                    bad.append(f"{k}: must be one of {', '.join(OVERRIDABLE[k])}")
                    continue
            if names is None or any(x not in OVERRIDABLE[k] for x in names if x):
                bad.append(f"{k}: must be a comma list of {', '.join(OVERRIDABLE[k])}")
                continue
            good[k] = ",".join(x for x in names if x)
            continue
        lo, hi = OVERRIDABLE[k]
        if isinstance(cur, tuple):                         # a list of numbers (ladder offsets / size multiples)
            if (not isinstance(v, (list, tuple)) or not 1 <= len(v) <= LADDER_MAX_LEVELS
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not lo <= x <= hi for x in v)):
                bad.append(f"{k}: must be a list of 1..{LADDER_MAX_LEVELS} numbers in {lo}..{hi}")
                continue
            good[k] = tuple(float(x) for x in v)
            continue
        if isinstance(cur, bool):
            if not isinstance(v, bool):
                bad.append(f"{k}: must be true or false")
                continue
        elif isinstance(cur, int):
            if isinstance(v, bool) or not isinstance(v, int):
                bad.append(f"{k}: must be a whole number")
                continue
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            bad.append(f"{k}: must be a number")
            continue
        if not isinstance(cur, bool) and not (lo <= v <= hi):
            bad.append(f"{k}: {v} is outside {lo}..{hi}")
            continue
        good[k] = float(v) if isinstance(cur, float) else v
    # An order must live well past its refresh point, or every order is "about to expire" as soon as it's placed
    # and gets replaced every cycle. (A key not in the file is judged at its current value.)
    ttl, refresh = good.get("order_ttl", cfg.order_ttl), good.get("refresh_before_expiry", cfg.refresh_before_expiry)
    if refresh * 2 > ttl:
        for k in ("order_ttl", "refresh_before_expiry"):
            if k in good:
                del good[k]
                bad.append(f"{k}: refresh_before_expiry ({refresh:.0f}) must be at most half of order_ttl ({ttl:.0f})")
    return good, bad


MARKET_EDGE_RANGE = (0.005, 0.05)


def validate_market_edge(raw, known):
    """market_edge.json -> ({eid: min_edge}, [problems]). Entries are {"min_edge": x, ...} or a bare number x, x in
    MARKET_EDGE_RANGE (dollars); keys starting with "_" (metadata) are skipped; an unknown exchange id or a bad
    value is refused (that entry only)."""
    good, bad = {}, []
    if not isinstance(raw, dict):
        return good, ["the file must hold one JSON object {exchange id: {\"min_edge\": ...}}"]
    lo, hi = MARKET_EDGE_RANGE
    for k, v in raw.items():
        k = str(k)
        if k.startswith("_"):
            continue
        if k not in known:
            bad.append(f"{k}: unknown exchange id")
            continue
        x = v.get("min_edge") if isinstance(v, dict) else v
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not (lo <= x <= hi):
            bad.append(f"{k}: min_edge {x!r} is not a number in {lo}..{hi}")
            continue
        good[k] = float(x)
    return good, bad
