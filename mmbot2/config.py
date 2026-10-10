"""
Settings: every number that is a decision, the list the owner may change while the bot runs, and the file reader.

OWNS     the Settings dataclass (one line of reason per field), LIVE (what settings_override.json may change, with
         its allowed range), validation, SettingsFile (re-read every 30 s), the environment (API key, tournament),
         the run directory, and which strategies the cycle calls (`strategies`).
NEVER    holds a fact about the exchange (the tick, the request limit, the price band: those are named constants
         beside the code that uses them), and never lets a bad file value through: it is refused with one alert.
ORIGIN   Sixteen releases taught one lesson over and over: a change must be a file edit, not a restart. The old
         config grew to 1,316 lines and some 400 settings, most of them release switches that are now always on
         or never were. What is left are the 62 values of the live file of 10 October
         (deploy/settings_override.live_2026-10-10.json), under names that say what they decide. The kill switch's
         drawdown is deliberately NOT live-changeable (house rule since day one).
OPEN     The live file sets skew_max 0 (no inventory lean on prices) while skew_target_inventory is on; the rewrite
         keeps the lean through sizes only (mm.py) and asks the owner whether a price lean was ever intended.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass, fields, replace

log = logging.getLogger("mm2")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the folder holding mm_bot2.py
RELOAD_SECONDS = 30.0                     # how often the settings file is re-read (README §5)


@dataclass
class Settings:
    # --- which strategies run (README §4.3). Off means the cycle does not call it at all. ---
    alloc_enabled: bool = True            # the allocator: the one layer with a large measured return (attribution)
    value_quotes_enabled: bool = True     # resting value quotes in the tails, behind the hurdle (+650 in 4 days)
    mm_enabled: bool = True               # market making in the middle band: small, kept to compare (finding 3.3)
    ladder_enabled: bool = True           # the harvest ladder: 15% per unit of cash on its first fills, 8-9 Oct

    # --- fair value (README §4.1) ---
    ref_max_spread: float = 0.03          # use Polymarket only where its own bid-ask is 3c or less
    ref_max_book_gap: float = 0.25        # a Polymarket price further than this from the book is a wrong match
    ref_guard_gap: float = 0.05           # Polymarket this far from the book: do not quote the side it says is wrong
    tilt_max: float = 0.15                # the tilt estimate is clipped here (it peaked at 0.128 on 4 Oct)

    # --- the value floor and the value quotes (README §4.3) ---
    value_sell_margin: float = 0.04       # a long is never offered below p - this (4c on 7 Oct to raise cash)
    value_quote_hurdle: float = 0.05      # a tail quote that adds must earn this per unit of cash at the result
    band_low: float = 0.15                # market making lives between band_low...
    band_high: float = 0.85               # ...and band_high; outside it only value quotes and the ladder
    tail_low: float = 0.05                # below this p no quote sells YES (risk ~1 to earn ~1c), except to reduce
    tail_high: float = 0.95               # above this p no quote buys YES, except to reduce

    # --- the allocator (README §4.3; attribution: buy the most edge per unit of cash, sell the least) ---
    alloc_interval_s: float = 3600.0      # once an hour, on fresh books and a fresh cash read
    alloc_min_edge_buy: float = 0.05      # a level is bought only at 5% edge or better
    alloc_max_edge_sell: float = 0.05     # a holding is sold only while its edge left is 5% or less
    alloc_swap_min_gain: float = 0.05     # a swap must improve edge by 5 points net of costs
    alloc_swap_sell_margin: float = 0.03  # a swap's sale may go 3c below value, never more (release 14.2)
    alloc_max_turnover_usd: float = 15000.0   # cash rotated in any rolling hour, at most
    alloc_pin: str = "Rep U.S. Senate,Dem U.S. Senate"   # labels never sold: the headline control markets
    alloc_prefer_short: bool = True       # in a two-leg race, short the other leg rather than buy this one if cheaper
    mm_reserve_usd: float = 20000.0       # cash kept for market making and the ladder; the refill tops it up only
                                          #   as far as those two can actually place (attribution, 10 Oct update)

    # --- market making (README §4.3, finding 3.3) ---
    mm_min_edge: float = 0.01             # never quote within 1c of fair value
    mm_quote_frac: float = 0.0005         # shares per quote x account: ~50 at 105k, where the old bot's quotes sat
                                          #   after its capital ceiling (0.002 quoted 4x the old bot; fix brief)
    kelly_fraction: float = 0.25          # quarter Kelly against p
    kelly_max_market_frac: float = 0.02   # at most 2% of the account at risk in one market from quotes
    mm_skew_max: float = 0.0              # price lean from inventory, at most this (live: 0, lean by size only)
    mm_inv_max_age_h: float = 6.0         # market-making inventory older than this is recycled...
    mm_inv_max_usd: float = 3000.0        # ...and so is more than this in one market
    mm_recycle_concession: float = 0.01   # ...at 1c through fair value, never more
    mm_risk_reserve_wc: float = 20000.0   # value adds pause while the worst-case room is below this...
    mm_risk_reserve_corr: float = 4000.0  # ...or the correlated-risk room below this, so they cannot starve MM

    # --- the harvest ladder (README §4.3, the owner's "sell the tilt in tranches") ---
    harvest_offsets: tuple = (0.0, 0.02, 0.04, 0.06)   # YES offsets beyond the best other order
    harvest_level_usd: float = 3000.0     # collateral per level
    harvest_min_edge: float = 0.08        # a level rests only at 8% edge or better
    harvest_total_usd: float = 10000.0    # the ladder's budget, carved out of mm_reserve_usd
    harvest_longshot_p: float = 0.10      # longshots (p at or below this) get asks above the book...
    harvest_favourite_p: float = 0.90     # ...favourites (p at or above this) get bids below it
    harvest_requote_s: float = 900.0      # a level is re-placed at most this often unless the touch moved > 1c
    harvest_writes_frac: float = 0.4      # the ladder may use at most this share of the writes left in a cycle

    # --- risk (README §4.4) ---
    max_worst_case_frac: float = 0.40     # correlated worst case above 40% of the account: reduce only
    worst_case_backstop_frac: float = 1.0 # the plain sum of worst cases: a tripwire only (release 12)
    bloc_rho: float = 0.55                # race-to-national-swing correlation
    bloc_rho_control: float = 0.85        # the party-control markets follow the national swing more closely
    max_bloc_delta_frac: float = 0.05     # national-swing exposure, per sd of the swing, at most 5% of the account
    market_max_usd: float = 10000.0       # collateral in one market, at most
    state_max_usd: float = 15000.0        # collateral in one state across every path (Rhode Island, 27k, one night)
    jump_threshold: float = 0.15          # fair value moving 15c in one cycle is news: pause that market...
    jump_cooldown_s: float = 60.0         # ...for this long
    max_drawdown: float = 0.30            # kill switch: account 30% below the start -> stop, cancel, leave a marker

    # --- the exchange and the loop (README §5) ---
    requests_per_minute: int = 80         # below the measured ~100, after the first 429 penalties
    writes_per_minute: int = 28           # order writes; lowered from 45 after the first penalties
    order_ttl_s: float = 1800.0           # every order expires on its own after 30 minutes
    full_check_s: float = 30.0            # reconcile against REST this often: the feed is best-effort
    cycle_s: float = 10.0                 # longest wait between cycles when the feed is quiet
    watchdog_s: float = 600.0             # no cycle for 10 minutes: cancel everything and exit for systemd
    stop_minutes_before_close: float = 15.0   # no orders this close to a market's close
    summary_every_h: int = 2              # phone summary every N hours (0 = never)


# What settings_override.json may change while the bot runs, with the allowed range. Ranges are wide enough for
# every value used live and narrow enough to catch a typo (a 0.4 written as 40).
LIVE = {
    "alloc_enabled": (False, True), "value_quotes_enabled": (False, True),
    "mm_enabled": (False, True), "ladder_enabled": (False, True),
    "ref_max_spread": (0.0, 0.10), "ref_max_book_gap": (0.05, 1.0), "ref_guard_gap": (0.01, 1.0),
    "tilt_max": (0.0, 0.5),
    "value_sell_margin": (0.0, 0.10), "value_quote_hurdle": (0.0, 1.0),
    "band_low": (0.0, 0.5), "band_high": (0.5, 1.0), "tail_low": (0.0, 0.2), "tail_high": (0.8, 1.0),
    "alloc_interval_s": (60.0, 86400.0), "alloc_min_edge_buy": (0.0, 1.0), "alloc_max_edge_sell": (-1.0, 1.0),
    "alloc_swap_min_gain": (0.0, 1.0), "alloc_swap_sell_margin": (0.0, 0.10),
    "alloc_max_turnover_usd": (0.0, 100000.0), "alloc_pin": ("", ""), "alloc_prefer_short": (False, True),
    "mm_reserve_usd": (0.0, 100000.0),
    "mm_min_edge": (0.0, 0.10), "mm_quote_frac": (0.0, 0.05), "kelly_fraction": (0.0, 1.0), "kelly_max_market_frac": (0.0, 0.10),
    "mm_skew_max": (0.0, 0.10), "mm_inv_max_age_h": (0.0, 168.0), "mm_inv_max_usd": (0.0, 100000.0),
    "mm_recycle_concession": (0.0, 0.05), "mm_risk_reserve_wc": (0.0, 100000.0),
    "mm_risk_reserve_corr": (0.0, 100000.0),
    "harvest_offsets": ((), ()), "harvest_level_usd": (0.0, 50000.0), "harvest_min_edge": (0.0, 1.0),
    "harvest_total_usd": (0.0, 100000.0), "harvest_longshot_p": (0.0, 0.5), "harvest_favourite_p": (0.5, 1.0),
    "harvest_requote_s": (0.0, 86400.0), "harvest_writes_frac": (0.0, 1.0),
    "max_worst_case_frac": (0.05, 1.0), "worst_case_backstop_frac": (0.1, 1.5), "bloc_rho": (0.0, 1.0),
    "bloc_rho_control": (0.0, 1.0), "max_bloc_delta_frac": (0.0, 1.0), "market_max_usd": (0.0, 100000.0),
    "state_max_usd": (0.0, 100000.0), "jump_threshold": (0.01, 1.0), "jump_cooldown_s": (0.0, 3600.0),
    "requests_per_minute": (10, 100), "writes_per_minute": (5, 60), "order_ttl_s": (120.0, 7200.0),
    "full_check_s": (5.0, 600.0), "cycle_s": (1.0, 120.0), "watchdog_s": (120.0, 3600.0),
    "stop_minutes_before_close": (0.0, 1440.0), "summary_every_h": (0, 24),
}


def strategies(s):
    """The deciding steps the cycle runs, in order (README §4: allocator, ladder, quotes). Off = not called."""
    return [name for name, on in (("alloc", s.alloc_enabled), ("ladder", s.ladder_enabled),
                                  ("value_quotes", s.value_quotes_enabled), ("mm", s.mm_enabled)) if on]


def check_value(name, value, default):
    """The value as the setting's type if it is allowed, else None. Bools are not numbers here."""
    lo, hi = LIVE[name]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if isinstance(default, str):
        return value if isinstance(value, str) else None
    if isinstance(default, tuple):
        ok = isinstance(value, list) and all(isinstance(x, (int, float)) and 0 <= x <= 0.5 for x in value)
        return tuple(float(x) for x in value) if ok else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        return None
    return int(value) if isinstance(default, int) else float(value)


def validate(raw, defaults):
    """({name: value} accepted, [reasons refused]) for a parsed settings file."""
    good, bad = {}, []
    if not isinstance(raw, dict):
        return good, ["the file is not one JSON object"]
    for name, value in raw.items():
        if name not in LIVE:
            bad.append(f"{name}: not a live setting")
            continue
        v = check_value(name, value, asdict(defaults)[name])
        if v is None:
            bad.append(f"{name}: {value!r} is outside {LIVE[name]}")
        else:
            good[name] = v
    if good.get("band_low", defaults.band_low) >= good.get("band_high", defaults.band_high):
        bad.append("band_low must be below band_high: both ignored")
        good.pop("band_low", None), good.pop("band_high", None)
    return good, bad


class SettingsFile:
    """The live settings file: re-read when it changes, every RELOAD_SECONDS. A removed key returns to its default."""

    def __init__(self, path, defaults=None):
        self.path, self.defaults = path, defaults or Settings()
        self.mtime, self.refused = None, set()

    def load(self, current, alert=lambda msg: None):
        """New Settings if the file changed, else None. Every change is logged; every refusal alerts once."""
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = None
        if mtime == self.mtime:
            return None
        self.mtime, raw = mtime, {}
        if mtime is not None:
            try:
                with open(self.path) as f:
                    raw = json.load(f)
            except (OSError, ValueError) as e:
                alert(f"{self.path} unreadable ({e}): keeping the current settings")
                return None
        good, bad = validate(raw, self.defaults)
        for reason in set(bad) - self.refused:
            alert(f"{os.path.basename(self.path)}: refused {reason}")
        self.refused = set(bad)
        defaults, now = asdict(self.defaults), asdict(current)
        wanted = {f.name: good.get(f.name, defaults[f.name]) for f in fields(Settings)}
        for name, value in wanted.items():
            if now[name] != value:
                log.warning("SETTING %s: %s -> %s", name, now[name], value)
        return replace(current, **wanted)


def load_env_file(path):
    """KEY=value lines from a .env file into the environment, never overriding what is already set."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Env:
    """Who we are and where files go: not decisions, so not Settings."""
    api_key: str
    slug: str
    base_url: str
    alert_url: str
    run_dir: str                          # every file the bot writes lives here, apart from the live bot's
    settings_path: str                    # the live settings file


def env():
    """The environment, after reading .env next to mm_bot2.py."""
    load_env_file(os.path.join(HERE, ".env"))
    run_dir = os.environ.get("MMBOT2_DIR", os.path.join(HERE, "run2"))
    os.makedirs(run_dir, exist_ok=True)
    return Env(api_key=os.environ.get("SUPERMARKET_API_KEY", ""), slug=os.environ.get("TOURNAMENT_SLUG", ""),
               base_url=os.environ.get("SUPERMARKET_BASE_URL", "https://sig.thesuper.market/api/v1"),
               alert_url=os.environ.get("ALERT_URL", ""), run_dir=run_dir,
               settings_path=os.environ.get("MMBOT2_SETTINGS", os.path.join(run_dir, "settings_override.json")))
