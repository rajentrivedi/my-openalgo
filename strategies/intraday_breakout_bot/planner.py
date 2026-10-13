"""History fetch and plan generation for the intraday breakout bot.

Parity with the backtest is the whole point of this module. Every bar that
reaches `plan_for_day` is built here by `adapt`, which reproduces the
backtest's own `load_reliance` frame construction exactly: IST-localised
timestamp index, the five OHLCV columns, numeric coercion with NaN/infinite
rows dropped, and the 09:15-15:30 on-the-5-minute-grid mask.

Three rules make that guarantee survivable:

* `adapt` delegates nothing to retyped constants of its own where it can avoid
  it -- the timezone comes from `backtest_source.IST`, so the bot and the
  backtest can never disagree about it. The session-mask constants
  (`SESSION_START_MINUTE`, `SESSION_END_MINUTE`, `BAR_MINUTES`) *are* retyped,
  because the backtest inlines them in `load_reliance` with no name to import.
  `test_parity_mask_constants_match_backtest_source` extracts the mask straight
  out of the backtest's source text and asserts ours agrees, so a change
  upstream fails the gate instead of silently desynchronising the two.
* The whole per-symbol planning path -- fetching, calling `plan_for_day`, and
  mapping its result into a `Plan` -- is wrapped so that no failure can abort a
  whole-universe scan. Failures are recorded on `Plan.error`.
* Nothing in here calls the API without going through `requests.post`, and
  `plan_today_from_frame` takes an already-loaded frame, so the plan maths can
  be tested against the backtest CSVs without any network access at all.
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any

import backtest_source
import config
import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

#: Columns of the plan frame, in the backtest's order.
COLUMNS = ["open", "high", "low", "close", "volume"]

#: Session window as minutes past midnight IST, matching `load_reliance`:
#: 09:15 (555) inclusive to 15:30 (930) exclusive.
SESSION_START_MINUTE = 9 * 60 + 15
SESSION_END_MINUTE = 15 * 60 + 30

#: Bar spacing in minutes. The session mask is only meaningful on this grid.
BAR_MINUTES = 5

#: Default lookback for `fetch_history`, in calendar days. Comfortably more
#: than the backtest's `warmup_days` (45) so indicator convergence matches.
DEFAULT_DAYS = 60

#: OpenAlgo history endpoint, relative to the configured host.
HISTORY_PATH = "/api/v1/history"

#: Seconds before giving up on a single history request.
REQUEST_TIMEOUT = 30

#: Total attempts per symbol, including the first. A universe scan fetches
#: DEFAULT_DAYS of 5-minute bars for every symbol back to back, so a read that
#: trips the 30s timeout is usually contention rather than a bad symbol --
#: and `plan_today` degrades any failure to an errored plan, so one blip
#: silently removes that symbol from the day.
RETRY_ATTEMPTS = 3

#: Seconds between attempts. Flat, not exponential: the scan is sequential, so
#: backing off for many seconds would let a slow symbol hold the whole book
#: behind it.
RETRY_BACKOFF_SECONDS = 1.0


@dataclass
class Plan:
    """One symbol's plan for one session.

    Field names are load-bearing: Tasks 4 (sizing), 7 (execution) and 9
    (reporting) all read them by name.

    A `None` `side` with `error is None` means the backtest found no setup for
    the day -- not a failure. A non-`None` `error` means the symbol could not be
    evaluated at all (bad history fetch, or the backtest raised).
    """

    symbol: str
    side: str | None = None
    entry: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    risk_per_share: float | None = None
    stop_atr: float | None = None
    expires_at: pd.Timestamp | None = None
    status: str | None = None
    error: str | None = None

    @property
    def is_takeable(self) -> bool:
        """True when this plan is a real, actionable setup.

        False for any plan that errored, produced no side, or is still waiting
        on the volume confirmation.
        """
        return self.error is None and self.side is not None and self.status != "WATCH_VOLUME"


def _backtest_tz() -> str:
    """The backtest module's own timezone, as a string.

    `backtest_source.IST` is the plain string `'Asia/Kolkata'`, not a tzinfo
    object. pandas accepts a string in `tz_convert`/`tz_localize` and resolves
    it internally, which is why using it yields an index dtype identical to the
    backtest's own `load_reliance` output.

    Resolved lazily (through `backtest_source`) rather than at import, so
    importing this module does not import the backtest.
    """
    return backtest_source.IST


def adapt(raw: list[dict]) -> pd.DataFrame:
    """Convert OpenAlgo history JSON bars into the backtest's frame shape.

    `raw` is the `data` list from `/api/v1/history`: dicts with epoch-second
    `timestamp` and OHLCV fields. The returned frame is what `plan_for_day`
    expects, so this is the single place where API-sourced bars are shaped --
    and it is covered by the parity test against the backtest CSVs.

    Exposed separately from `fetch_history` so tests can exercise the shaping
    without HTTP.
    """
    tz = _backtest_tz()

    if not raw:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz=tz))

    frame = pd.DataFrame(raw)
    for column in COLUMNS:
        if column not in frame.columns:
            raise RuntimeError(
                f"history response is missing required column {column!r}; "
                f"got {list(frame.columns)}"
            )

    index = pd.to_datetime(frame["timestamp"], unit="s", utc=True).dt.tz_convert(tz)
    frame = frame.assign(**{"timestamp": index}).set_index("timestamp").sort_index()
    frame = frame[COLUMNS].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()

    minutes = frame.index.hour * 60 + frame.index.minute
    mask = (
        (minutes >= SESSION_START_MINUTE)
        & (minutes < SESSION_END_MINUTE)
        & ((minutes - SESSION_START_MINUTE) % BAR_MINUTES == 0)
    )
    return frame[mask]


def fetch_history(
    symbol: str,
    days: int = DEFAULT_DAYS,
    *,
    end_date: datetime.date | None = None,
) -> pd.DataFrame:
    """Fetch `days` calendar days of 5-minute bars for `symbol` from OpenAlgo.

    The apikey travels in the JSON body, not a header -- that is what the
    OpenAlgo `/api/v1/history` endpoint expects.

    Raises `RuntimeError` on any API or transport failure. Callers that must
    survive a bad symbol (e.g. a whole-universe scan) catch it per symbol; see
    `plan_today`.
    """
    end = end_date or datetime.datetime.now(tz=config.TZ).date()
    start = end - datetime.timedelta(days=days)

    payload = {
        "apikey": config.API_KEY,
        "symbol": symbol,
        "exchange": config.EXCHANGE,
        "interval": "5m",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = f"{config.API_HOST.rstrip('/')}{HISTORY_PATH}"

    body = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            body = response.json()
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Retryable: the request never got an answer, so nothing about it
            # tells us the symbol is bad. Exhausting the budget falls through
            # to the RuntimeError below.
            if attempt == RETRY_ATTEMPTS:
                raise RuntimeError(
                    f"history request failed for {symbol}: {exc} "
                    f"(after {RETRY_ATTEMPTS} attempts)"
                ) from exc
            logger.warning(
                "history fetch for %s hit %s; retrying (attempt %s/%s)",
                symbol, type(exc).__name__, attempt, RETRY_ATTEMPTS,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)
        except requests.RequestException as exc:
            # An HTTP status failure is deterministic -- a 400 for an unknown
            # symbol will be a 400 on every retry -- so it is raised at once.
            # A `TypeError` or `AttributeError` is not a RequestException and so
            # propagates: it is a bug in this function, not a bad symbol, and
            # must not be relabelled as an API failure.
            raise RuntimeError(f"history request failed for {symbol}: {exc}") from exc

    if not isinstance(body, dict) or body.get("status") != "success":
        message = (
            body.get("message", body) if isinstance(body, dict) else repr(body)
        )
        raise RuntimeError(f"history API error for {symbol}: {message}")

    data = body.get("data")
    if not isinstance(data, list):
        raise RuntimeError(
            f"history API returned no usable data list for {symbol}: {body!r}"
        )

    return adapt(data)


def _parse_timestamp(value: Any) -> pd.Timestamp | None:
    """Parse the backtest's `expires_at` (an ISO string) into a tz-aware stamp.

    Naive stamps are localised with `_backtest_tz()`, not `config.TZ`, so this
    produces the same tz dtype the frame index uses. The backtest always emits
    an aware ISO string, so that branch is currently unreachable -- but a second
    tz source here would be a latent reintroduction of the very dtype
    divergence this module exists to eliminate.
    """
    if value is None:
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(_backtest_tz())
    return stamp


def plan_today_from_frame(
    symbol: str,
    df: pd.DataFrame,
    day: datetime.date,
) -> dict[str, Plan]:
    """Plan one symbol from an already-loaded frame. No network access.

    This is the testable seam the parity test drives. It delegates the maths to
    the backtest module itself rather than reimplementing it, which is what
    makes parity structural instead of a coincidence that needs re-checking.

    Nothing in here raises. Calling `plan_for_day` *and* mapping its result into
    a `Plan` are both inside the error handling, because the mapping is where
    a backtest key rename or a type change surfaces as a `KeyError`/`TypeError`
    -- and an exception escaping here would abort the caller's whole-universe
    scan over one symbol. Such a failure is recorded on `Plan.error` instead.
    """
    try:
        result = backtest_source.plan_for_day(df, day, backtest_source.args())

        if result is None:
            return {symbol: Plan(symbol=symbol)}

        side, plan, _trigger_ts = result
        return {
            symbol: Plan(
                symbol=symbol,
                side=side,
                entry=plan["entry"],
                stop=plan["stop"],
                target1=plan["target1"],
                target2=plan["target2"],
                risk_per_share=plan["risk_per_share"],
                stop_atr=plan["stop_atr"],
                expires_at=_parse_timestamp(plan["expires_at"]),
                status=plan["status"],
            )
        }
    except Exception as exc:
        # Deliberately broad: the mapping touches a dict whose keys come from a
        # module we do not control, and any failure there must degrade this one
        # symbol rather than the scan. It is logged at warning level so a
        # systematic failure (e.g. an upstream key rename affecting every
        # symbol) is still loud in the log rather than silent.
        logger.warning("planning failed for %s on %s: %s", symbol, day, exc)
        return {symbol: Plan(symbol=symbol, error=f"{type(exc).__name__}: {exc}")}


def plan_today(symbols: list[str], day: datetime.date) -> dict[str, Plan]:
    """Plan every symbol in `symbols` for `day`. Live path: fetch, then plan.

    A symbol whose history fetch fails is recorded as an errored `Plan` rather
    than raising, so one bad symbol cannot abort a whole-universe scan.

    Only `RuntimeError` and `requests.RequestException` are treated as "bad
    symbol". A `TypeError` or `AttributeError` from a signature change is a bug
    that should be loud, so it propagates instead of being quietly mislabelled
    `history_error` and degrading the symbol in silence.
    """
    out: dict[str, Plan] = {}
    for symbol in symbols:
        try:
            df = fetch_history(symbol, days=DEFAULT_DAYS)
        except (RuntimeError, requests.RequestException) as exc:
            logger.warning("history fetch failed for %s: %s", symbol, exc)
            out[symbol] = Plan(symbol=symbol, error=f"history_error: {exc}")
            continue
        out.update(plan_today_from_frame(symbol, df, day))
    return out


__all__ = [
    "BAR_MINUTES",
    "COLUMNS",
    "DEFAULT_DAYS",
    "SESSION_END_MINUTE",
    "SESSION_START_MINUTE",
    "Plan",
    "adapt",
    "fetch_history",
    "plan_today",
    "plan_today_from_frame",
]
