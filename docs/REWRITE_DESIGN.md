# mmbot2: the module contract

The lead's contract between modules. Every module imports only `mmbot2.state` and `mmbot2.config` (plus
`mmbot2.pricing` for the edge formulas, and `mmbot2.risk` for the cash a single order needs). Strategy modules
are pure: they read a `View` and a `RiskState` and return wanted `Order`s; only `bot.py` and `exchange.py` touch
the network. Everything is in YES terms (see `state.py`). Settings are `config.Settings` (`s` below); a module
that needs a new setting reports it instead of adding it.

Rules for every file (from the brief): a 10-15 line header (OWNS / NEVER / ORIGIN, plus OPEN for open
questions); functions of 40 lines or fewer, named for what they return; comments say why, never what; no flags
inside the cycle; no `getattr` tricks, mixins, metaprogramming or `**kwargs` plumbing; exchange facts are named
constants beside their use.

## exchange.py
- `ApiError(Exception)` with `.status`, `.code`, `.message`.
- `Client(env, s, live)`: one `requests.Session`, a sliding-60-s request budget (`s.requests_per_minute`) and a
  separate write budget (`s.writes_per_minute`), the 429 pause (Retry-After, all threads), retries with the same
  idempotency key, `set_budgets(rpm, wpm)`, `requests_left()`, `writes_left()`, counters `rate_limited`,
  `pauses`.
  - reads: `tournament() -> dict`; `markets() -> list[Market]`; `positions() -> dict[eid, qty]` (unsettled);
    `account() -> Account` (value + free cash, from /portfolio/pnl, falling back to balance + positions);
    `open_orders() -> list[Order]` (YES terms, `oid` set, `tag=""`); `book(eid, mine) -> Book` (our orders in
    `mine` stripped); `tops(eids) -> dict[eid, (bid, ask)]` (bulk prices, 100 ids a request, includes ours);
    `fills(after_id) -> list[Fill]` (newest last; `tag=""`).
  - writes (live only; the bot never calls them in a dry run): `place(orders, positions) -> list[Placed]` (one
    batch; `Placed(order, oid, traded, error)`; a bid while short NO goes out as the covered "sell NO" up to the
    NO held, so it needs no cash; an `ioc` order's unfilled rest is cancelled at once); `cancel(oid) -> bool`;
    `cancel_all() -> bool`.
- `Feed(client, tournament_id)`: the WebSocket feed on a thread: `start()`, `stop()`, `healthy()`,
  `take() -> (dirty_eids: set, account_changed: bool, resync: bool)`.

## pricing.py
- `ref_key(market) -> "Ohio Senate|Republican"` (the key `ref_prices.py` uses).
- `fair_values(markets, refs, spreads, books, s) -> dict[eid, p]`: liquid Polymarket prices only (own spread <=
  `s.ref_max_spread`, within `s.ref_max_book_gap` of the book's mid when there is one), the legs of a race
  scaled to sum to 1. Unpriced markets are absent.
- the four edge formulas: `edge_buy(p, ask)`, `edge_short(p, bid)`, `edge_keep_long(p, bid)`,
  `edge_keep_short(p, ask)`; `edge_held(qty, p, book)` (edge of keeping a holding at the touch, None if no
  touch).
- `kelly_shares(p, price, is_bid, bankroll, s) -> float`.
- `TiltEstimator`: `update(markets, books, p, now) -> s or None`, `to_dict()`, `from_dict(d)`.

## risk.py
- `RiskState` dataclass: `worst_case`, `correlated`, `bloc_delta`, `reduce_only`, `room_wc`, `room_corr`
  (dollars left below each cap), `adds_paused` (rooms below the market-making risk reserve), `market_usd`
  (eid -> collateral), `state_usd` (state -> collateral).
- `assess(view, s, was_reduce_only) -> RiskState`; `kill_switch_hit(account, s) -> bool`.
- `cash_need(order, qty_held) -> float` (a covered sale needs none).
- `Gate(view, risk, s)`: the per-cycle checker; `admit(order) -> Order | None` (trimmed or refused) applies
  reduce-only, the per-market and per-state caps and the cash gate in the order orders arrive (the bot sends
  them in priority order); `refused_usd: dict[tag, float]` is the cash each strategy wanted and did not get.

## value.py
- `floor_prices(qty, p, margin) -> (max_bid, min_ask)`; `apply_floor(orders, view, s) -> list[Order]`: every
  order that reduces a position is moved to the value floor (allocator swap sales use
  `s.alloc_swap_sell_margin`).
- `tail_quotes(view, risk, s) -> list[Order]` (tag `value`): outside the band, the side that adds, priced by
  the hurdle.
- `Allocator(saved=None)`: `step(view, risk, demand_usd, s) -> list[Order]` (ioc; tags `alloc` and `refill`),
  `note_fills(fills)`, `status() -> dict` (the `alloc` field), `funding() -> dict` (the `mm_funding` field),
  `to_dict()`. `demand_usd` is the cash market making and the ladder wanted last cycle and did not get: the refill
  sells no more than that.

## mm.py
- `Inventory(saved=None)`: market-making lots from fills tagged `mm`; `note_fills(fills)`,
  `stale(view, s) -> dict[eid, shares]`, `to_dict()`, `status()`.
- `quotes(view, risk, s, inventory) -> list[Order]` (tags `mm`, `recycle`).

## ladder.py
- `Ladder(saved=None)`: `plan(view, risk, s) -> list[Order]` (tag `ladder`; keeps a resting level unchanged
  until `s.harvest_requote_s` unless the touch moved > 1c), `status() -> dict` (the `harvest` field),
  `to_dict()`.

## bot.py (lead)
`Bot(client, feed, refs, s, env, live)`: `cycle()`, `cancel_everything()`, `adopt(orders)`, `save()`,
`last_cycle_mono`, `running`. The cycle: read -> price -> risk -> decide (allocator, ladder, value quotes, market
making) -> floor -> gate -> reconcile -> send -> report (`status.json`).

## ops.py
`main(argv) -> int`: `run`, `run --live`, `status`, `cancel`; the run loop, SIGUSR1 handover (exit without
cancelling, the next process adopts), the watchdog, the start-up self-test, exit codes.
