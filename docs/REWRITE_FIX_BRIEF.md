# Brief: make the rewrite trade (overnight fix)

Branch `rewrite` holds the rewritten bot (`mmbot2/`, `mm_bot2.py`, `tests2/`; `docs/REWRITE_STATUS.md`,
`docs/REWRITE_BRIEF.md`). It passes its tests and the snapshot replay. Run in dry-run mode on the real server
for four hours beside the live bot, it valued the book correctly (EV 112.6k, the same as the live bot) and saw the
live bot's fills, but **planned no orders at all**: zero quotes, zero ladder levels, zero allocator pairs, and
`tilt_s` stayed null. Fix that, then do the three small changes below, and push `READY: rewrite 2`.

## Evidence (in this branch)

- `analysis/rewrite/shadow-2026-10-10.log`: the shadow's full log (17:24–21:14 UTC). Note `priced 226/237`,
  `resting 0`, `tilt None` on every status line, and that no PLACE / order-planning line ever appears.
- `analysis/rewrite/shadow-2026-10-10.status.json`: its status at the end (alloc ran once at 20:20 and planned 0
  pairs; mm_funding shows no inventory; harvest 0 levels; `cash` 10,025; `orders_resting` 0).
- `analysis/rewrite/shadow-2026-10-10.settings.json`: the settings it ran with. The owner lowered
  `requests_per_minute` to 20 and `writes_per_minute` to 5 so the shadow could not crowd the live bot's budget
  (both bots use the same API key, which allows about 100 requests a minute in total).

## Conditions the shadow ran under (your tests must reproduce them)

1. **The account already holds positions and 170 resting orders placed by another process** (the live bot).
   The rewrite must treat resting orders it did not place as foreign: never cancel them, never count them as its
   own quotes, but also never refuse to quote because of them. Decide and document the rule in `exchange.py` /
   `bot.py` headers; test it in `tests2/` with a fake that starts with foreign resting orders on the account.
2. **Books arrive slowly.** With 237 markets and 20 requests a minute, the initial book download takes many
   minutes and competes with the per-cycle reads (account, positions, fills, orders). Check whether the bot
   ever had books for the markets it would quote (`priced` counts Polymarket, not books), whether the realtime
   feed's updates were applied without a prior REST download, and whether any budget or "fresh book" rule
   silently refused every quote. Make the status line show how many markets have a fresh book and how many
   orders were refused and why (a `blocked_by` dict like the old bot's), so the next shadow is diagnosable.
3. `tilt_s` must form: the estimator needs book mids for priced markets; find out why it never did.
4. Dry-run mode must LOG every order it would send (`DRY PLACE <market> <side> <qty>@<price> <reason>`), one line
   per order, so the owner can compare with the live bot's fills. It logged none; make sure that is because none
   were planned, and that it will log them once they are.

## Also, before READY

- `mm_quote_frac` default 0.002 → 0.0005 (about 50 shares at 105k): the rewrite dropped the old capital
  ceiling, so its quotes were four times the old bot's; start where the old bot was.
- Add back a minimal recorder: once a minute, append each priced market's best bid/ask, reference price and
  our position to a sqlite table in the run dir (the old `mmbot/status.py` recorder, by grep; ~50 lines). The
  owner's analyses and paper read that data.
- Keep the whole bot under 4,000 lines; report the count in `docs/REWRITE_STATUS.md` with what changed.

## Rules, as before

Opus for you and at most two sub-agents; grep and slices, never whole files over 400 lines; test output
`| tail -30`; commit and push after each fix; `docs/REWRITE_STATUS.md` updated on every push; never touch
`mmbot/`, `mm_bot.py`, `tests/`, the server or any exchange. Stop at 800k tokens or 5 hours and leave the STATUS
block saying exactly what is left. Last commit message starts with `READY: rewrite 2` when the four conditions
above have tests and the three changes are in.
