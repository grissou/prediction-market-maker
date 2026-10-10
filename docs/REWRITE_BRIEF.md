# Brief: rewrite the bot small (cloud session)

You are rewriting a live trading bot from its strategy, not from its code. Work on a new branch `rewrite`
from `main`. Never touch the server, the exchange, `mmbot/`, `mm_bot.py` or `tests/`: the live bot runs from
those and the owner deploys by hand. You deliver code, tests and a short note; the owner runs the shadow run
and the switch-over.

## The goal

A bot a reader can follow in an afternoon and any module in ten minutes, that runs the SAME STRATEGY as the
live one. It does not have to send identical orders. It has to be clearly right, small, and explain itself:
every module's header says which finding or failure produced it, so the owner can write a paper from the code.

**The existing code is the basis.** The package `mmbot/` is a working, tested implementation of this strategy;
the rewrite is that implementation written clearly, not a new design. For each module, start by reading the
old module's public methods (grep `def `, then the docstrings and the first 30 lines of each method that
matters) so you know what the live bot actually does; carry over the logic and the numbers; leave behind the
flags, the release history, the duplicated state and the paths that are never taken. Where the old code does
something you cannot explain in one sentence, keep the behaviour and write the sentence in the header as an
open question for the owner rather than inventing a different rule.

**Clarity is the first priority, ahead of size.** The reader is a student or an interviewer with ten minutes
per file. Concretely:
- one idea per function, 40 lines or fewer; a function's name says what it returns, not what release added it;
- the cycle reads top to bottom as the story in README §4: read, price, risk, decide (allocator, ladder,
  quotes), reconcile, send, report; no feature flag checks inside it (a strategy that is off is simply not
  called, from `config.py`);
- state lives in a few plain dataclasses (`Book`, `Position`, `Order`, `Account`), each defined once, with a
  one-line comment per field; no attribute is added to the bot object outside `__init__`;
- numbers that are decisions are settings with a one-line reason; numbers that are facts about the exchange
  (tick, request limit, the band) are named constants next to their use;
- every module starts with a 10–15 line header: what it owns, what it must never do, where it came from;
- comments explain WHY (the finding, the failure, the number's origin), never WHAT the next line does;
- no cleverness: no metaprogramming, no mixins, no `getattr` tricks, no `**kwargs` plumbing.

Targets: 3,000–3,500 lines across 8–10 files in `mmbot2/`; 40–60 settings, each a real decision (everything
else is a named constant beside the code that uses it); a test suite in `tests2/` of 6–8 files; one entry
point `mm_bot2.py` with the same command line as `mm_bot.py` (`run`, `run --live`, `status`, `cancel`).
If clarity and the size target conflict, clarity wins and the STATUS file says by how much.

## Sources of truth, in this order (read these; do not read the old package end to end)

1. `README.md` §3 (the findings) and §4 (the method: fair value, edge per unit of cash, the four strategies,
   the risk model). This is the specification.
2. `deploy/settings_override.live_2026-10-10.json`: the 62 live settings. Their VALUES are the strategy; their
   names are the old code's and need not survive. `mmbot/config.py` documents what each means (grep the name,
   read the comment; never read the whole file).
3. `analysis/reports/RETURNS_ATTRIBUTION.md`: what earns (the allocator and the ladder) and what cost (the
   refill selling more than the deployers could place; a sleeve that could not buy). Design the funding so a
   sale only happens when a buy can use the cash.
4. `deploy/RELEASES.md` and `docs/TALK.md`: the history and the plain-English story, for the module headers.
5. The old package `mmbot/`, the implementation you are rewriting. For every module, grep its public methods first (see The goal); for the plumbing, these are the files that matter: `mmbot/exchange.py` (the API's
   endpoints, idempotency keys, the 429 handling, the WebSocket feed, the 80 requests / 28 writes a minute
   budgets), `mmbot/util.py` (paths, alerts, the settings-file reader), `mmbot/ops.py` (handover via SIGUSR1
   and the handover file, the watchdog, the start-up self-test, exit codes), `mmbot/status.py` (the
   `status.json` fields the owner reads: account_value, ev_outcome, realised_pnl, reduce_only, orders_resting,
   mm_funding, harvest, state_caps, alloc; keep those names). Read these with grep and 100-line slices.
   `tests/fakes.py` is a fake exchange that follows the API; reuse it (copy it into `tests2/`, trim to what
   you use).

## The modules (one sub-agent each; the lead writes `config.py`, `bot.py` and the headers)

| File | Owns | Comes from (goes in the header) |
|---|---|---|
| `mmbot2/config.py` | the settings dataclass, the live-changeable list, validation, the settings-file reader | every release's lesson that a change must be a file edit, not a restart |
| `mmbot2/exchange.py` | API client, realtime feed, request and write budgets, order-change records | day one: the measured 100/min limit and 429 penalty; the crowded book |
| `mmbot2/pricing.py` | fair value from Polymarket, race scaling, the tilt estimator, edge per unit of cash (the four formulas) | finding 3.1 (the tilt) and 3.2 (settlement at the result) |
| `mmbot2/risk.py` | correlated worst case, per-market and per-state caps, the cash gate, kill switch | the 30%→40% cap history; Rhode Island at a quarter of the account |
| `mmbot2/value.py` | the value book and floor; the allocator: hourly swaps, buys at the touch, the refill sized to what the buyers can place | the 3.7k morning (the floor); the attribution (what earns) |
| `mmbot2/mm.py` | market-making quotes in the middle band, inventory skew, the reserve, the recycler | finding 3.3 (market making demoted, kept small) |
| `mmbot2/ladder.py` | the harvest ladder | the owner's "sell the tilt in tranches" rule; its first fills on 8–9 Oct |
| `mmbot2/bot.py` | state, the cycle (read → price → risk → decide: allocator, ladder, quotes → reconcile → send → report), status.json, summaries | the whole design |
| `mmbot2/ops.py` | handover, watchdog, self-test, run loop, command line | the 7 Oct outage; the refill retry loop (budgets are explicit) |

Not in the rewrite: arbitrage, takes (the allocator's buy at the touch is the same action), pair unwinds of
NO+NO sets unless the owner's positions require them (check `status.json`'s `nono_sets` meaning in
`mmbot/arb.py` by grep; if the live book holds such sets, keep a minimal covered-sale unwind in `value.py` and
say so), the momentum sleeve, anything that was never switched on.

## How to work, token-consciously

- One lead (you) and at most THREE sub-agents at a time, one module each, with this brief and the module's
  row as their whole context. A sub-agent returns a report of at most 25 lines; it never pastes code into chat.
- Never read a file longer than 400 lines whole. Grep for the name, then read the slice. The old `mmbot/`
  files are 300–2,300 lines each: slices only.
- Test output: `| tail -30`. Suites are plain scripts printing PASS/FAIL and `N/N passed`, like `tests/`.
- Commit after each module lands (`git commit`, then `git push -u origin rewrite`), with a STATUS block at the
  top of `docs/REWRITE_STATUS.md` (10 lines: done, next, blockers, lines so far). Update it on every push.
- Budget: stop at 1.2 M tokens or 6 hours, whichever first, and leave the STATUS block saying exactly what is
  left. A half-finished module with a clear note beats a full one with no note.
- No feature not in this brief. If a sub-agent proposes one, write it in `docs/REWRITE_STATUS.md` under
  "Ideas not built" and move on.

## Done means

1. `python3 mm_bot2.py run` (dry-run mode) starts against the real feed with the live settings file and logs
   the orders it WOULD send, without sending any. The owner runs this on the server beside the live bot for a
   day (the shadow run).
2. `tests2/` passes: the fake-exchange suite for each module, a stress run (faults on any request; the bot
   never crashes, never crosses itself, keeps caps), and a replay of a recorded snapshot: build it from the
   `ops-snapshot-2026-10-04` branch (`market_data.sql.gz` → `md.sqlite` with sqlite3, plus the branch's
   `status.json`, `order_notes.json`, `position_lots.json`, `settings_override.json`) and show the orders the
   new bot plans on that state next to what `tests/test_p15_dryrun.py` shows the old bot planned: same markets, same sides, sizes within a factor of two;
   explain each difference in one line in `docs/REWRITE_STATUS.md`.
3. A `deploy/settings_override.rewrite.json` with the new settings at the live strategy's values, and a
   10-line section in `deploy/RUNBOOK.md` for running the shadow and switching over.
4. Last commit message starts with "READY: rewrite" and `docs/REWRITE_STATUS.md` has the line counts.

Fair play, as in every brief: the bot only places orders it intends to fill, one account, within the measured
rate limit; no spoofing, wash trading, collusion or exploiting bugs.
