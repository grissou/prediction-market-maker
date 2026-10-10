"""
The phone: the alerts and the 2-hourly summary, over ntfy (ALERT_URL).

OWNS     Notifier (send; alert with a 30-minute silence per cause), summary_text (the summary from status.json and
         the two leaderboard reads), due (on the hour UTC every summary_every_h).
NEVER    decides anything, raises, or blocks a cycle for more than one short POST.
ORIGIN   The old summary grew to a dozen lines of per-feature detail and the alerts to every refusal; the fix brief
         of 10 Oct: five figures (balance, leaderboard ranks, EV, market-making profit, tilt) and a status line;
         alerts only for the kill switch, a crash or restart, reduce-only on or off, a rate-limit penalty, the
         self-test failing, the realtime feed down for more than 5 minutes, the watchdog. The rest is log only.
"""
import logging
import time

import requests

log = logging.getLogger("mm2")

SILENCE_S = 1800.0            # the same cause alerts at most once in 30 minutes


class Notifier:
    def __init__(self, url, title="mm_bot2"):
        self.url, self.title, self.last = url, title, {}

    def send(self, msg, title=None):
        if not self.url:
            return
        try:
            requests.post(self.url, data=msg.encode(), headers={"Title": title or self.title}, timeout=5)
        except requests.RequestException as e:
            log.warning("phone message not sent: %s", e)

    def alert(self, msg, cause=None):
        """One line at once; the same cause (default: the text before the first colon) not again for 30 minutes."""
        cause = cause or msg.split(":")[0]
        now = time.monotonic()
        if now - self.last.get(cause, -1e9) < SILENCE_S:
            log.warning("ALERT (silenced, sent < 30 min ago) %s", msg)
            return
        self.last[cause] = now
        log.warning("ALERT %s", msg)
        self.send(msg)


def k(x, sign=False):
    return f"{x / 1000:{'+' if sign else ''}.1f}k" if abs(x) >= 1000 else f"{x:{'+' if sign else ''}.0f}"


def summary_text(st, lb=None, scores=None):
    """The summary: at most four lines; a line (or part) whose figure is unknown is left out."""
    lines, value, start = [], st.get("account_value"), st.get("start_balance")
    if value is not None:
        parts = [f"Balance {k(value)}" + (f" ({100 * (value - start) / start:+.1f}%)" if start else "")]
        if (lb or {}).get("myRank"):
            parts.append(f"leaderboard {lb['myRank']:,} of {lb['total']:,}" if lb.get("total")
                         else f"leaderboard {lb['myRank']:,}")
        score = next((s for s in scores or [] if s.get("marketType") == "global"), (scores or [None])[0])
        if score and score.get("smartScoreDecayed") is not None:
            parts.append(f"Smart Score {score['smartScoreDecayed']:.1f}"
                         + (f" (rank {score['rank']:,})" if score.get("rank") else ""))
        lines.append(" · ".join(parts))
    if st.get("ev_outcome") is not None:
        change = st.get("ev_change_24h")
        lines.append(f"EV at settlement {k(st['ev_outcome'])}" + (f" ({k(change, True)} 24h)" if change is not None
                                                                     else ""))
    mm = [f"MM profit 24h {st['mm_profit_24h']:+.0f} (realised)"] if st.get("mm_profit_24h") is not None else []
    mm += [f"tilt {100 * st['tilt_s']:.1f}%"] if st.get("tilt_s") is not None else []
    if mm:
        lines.append(" · ".join(mm))
    flags = [f"REDUCE-ONLY since {st['reduce_only_since']}" if st.get("reduce_only") else ""]
    flags += [f"{st['rate_limits_period']} rate limits" if st.get("rate_limits_period") else "",
              f"{st['errors_period']} errors" if st.get("errors_period") else ""]
    lines.append(" · ".join(f for f in flags if f) or "OK")
    return "\n".join(lines)


def due(now, every_h, last_key):
    """The key of the summary due now (on the hour UTC, every every_h hours), or None if none is due."""
    if not every_h or now.hour % every_h:
        return None
    key = now.strftime("%Y-%m-%d %H")
    return key if key != last_key else None
