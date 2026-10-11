"""WhatsApp notifications for the intraday breakout bot.

Three surface functions, none of which may ever take the bot down:

* `send` is the only thing that talks to the broker's WhatsApp bridge, and it
  returns a bool instead of raising. A notification is a courtesy; a failed
  one must not abort an entry loop, kill the process mid-session, or take out
  the square-off path at 15:15. Every exception is caught and logged.
* `event` formats a single line for one event (entry, exit, stop, halt).
* `daily_digest` builds the whole-session summary. It returns **one** string
  for the entire universe, not one message per symbol: the server rate limit
  is 30 messages per minute, and a 40-symbol universe would either trip it or
  bury the operator.

`client()` is memoised because the SDK client is stateful and each construction
is a fresh object; and because it is a *module-level function*, the tests
replace it wholesale (`monkeypatch.setattr(notify, "client", ...)`) to inject
a fake or a raising double. `send` must therefore call `client()` through the
module global, never a captured local, or the monkeypatch would not take.

**No API key ever reaches a message or a log line.** The key is passed to the
SDK constructor and nowhere else. Exceptions from the SDK are logged with
`%s` on the exception itself, which can carry a URL but never the key we
passed.
"""

from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger(__name__)

#: Destination for every notification. This bot has one operator.
RECIPIENT = "self"

_client = None


def client() -> Any:
    """Return the memoised OpenAlgo SDK client, constructing it on first use.

    Imported lazily so that importing this module performs no network setup,
    and memoised so repeated `send` calls reuse one client.
    """
    global _client
    if _client is None:
        from openalgo import api as openalgo_api

        _client = openalgo_api(api_key=config.API_KEY, host=config.API_HOST)
    return _client


def send(message: str) -> bool:
    """Send `message` to the operator's WhatsApp. Returns True on success.

    Never raises. The whole point is that a WhatsApp bridge that is unpaired,
    rate-limited, or offline degrades to a `False` in a log line while the bot
    keeps trading.
    """
    try:
        # `client()` is looked up as a module global so tests can replace it.
        client().whatsapp(message=message, to=RECIPIENT)
    except Exception as exc:  # noqa: BLE001 - notification must never propagate
        logger.warning("whatsapp notification failed: %s", exc)
        return False
    return True


def _format_value(value: Any) -> str:
    """Render a field value compactly, or `'-'` when it is absent."""
    if value is None:
        return "-"
    return str(value)


def event(kind: str, **fields: Any) -> str:
    """One event line: the kind, then `key=value` for each field in order.

    Field order follows keyword order, so callers control readability by how
    they write the call. No lookup, formatting, or reflection -- only the
    values the caller passed are ever printed.
    """
    parts = [str(kind)]
    parts.extend(f"{key}={_format_value(value)}" for key, value in fields.items())
    return " ".join(parts)


def daily_digest(plans: dict, taken: list, skipped: list) -> str:
    """Build the single session summary message.

    `plans` maps symbol to `Plan`; `taken` and `skipped` are symbol lists.
    Everything is rendered into one string so the caller sends exactly one
    message regardless of universe size.
    """
    taken_set = set(taken or ())
    skipped_set = set(skipped or ())

    lines = [
        "Intraday breakout bot -- session summary",
        f"plans: {len(plans or {})}  taken: {len(taken_set)}  "
        f"skipped: {len(skipped_set)}",
    ]

    if taken_set:
        lines.append("Taken:")
        lines.extend(_plan_line(plans, symbol) for symbol in sorted(taken_set))
    else:
        lines.append("Taken: none")

    if skipped_set:
        lines.append("Skipped:")
        lines.extend(_plan_line(plans, symbol) for symbol in sorted(skipped_set))
    else:
        lines.append("Skipped: none")

    return "\n".join(lines)


def _plan_line(plans: dict, symbol: str) -> str:
    """One `SYMBOL` line for the digest, with the reason when we know one.

    Reads a `Plan` by attribute, tolerating a stub or `None`: a symbol named
    in `taken`/`skipped` but absent from `plans` still gets a line rather than
    an exception in the middle of a summary.
    """
    plan = (plans or {}).get(symbol)

    if plan is None:
        return f"  {symbol}"

    status = getattr(plan, "status", None) or getattr(plan, "error", None)
    if status:
        return f"  {symbol} [{status}]"

    side = getattr(plan, "side", None)
    entry = getattr(plan, "entry", None)
    stop = getattr(plan, "stop", None)
    return f"  {symbol} {side or '-'} entry={_format_value(entry)} stop={_format_value(stop)}"


__all__ = [
    "RECIPIENT",
    "client",
    "daily_digest",
    "event",
    "send",
]
