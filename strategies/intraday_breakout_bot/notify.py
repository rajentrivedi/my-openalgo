"""WhatsApp notifications for the intraday breakout bot.

Three surface functions, none of which may ever take the bot down:

* `send` is the only thing that talks to the broker's WhatsApp bridge, and it
  returns a bool instead of raising. A notification is a courtesy; a failed
  one must not abort an entry loop, kill the process mid-session, or take out
  the square-off path at 15:15. It catches exceptions *and* inspects the
  returned response -- the SDK reports transport failures as an error dict
  rather than raising, so the exception path alone would report success for
  every real-world delivery failure.
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
%s on the exception itself, which can carry a URL but never the key we
passed; the same applies to a logged error response, whose fields are the
server's own message, code and error_type.
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


def _is_error_response(response: Any) -> bool:
    """True when the SDK's return value reports a failed delivery.

    The OpenAlgo SDK does **not** raise on transport failure.
    `openalgo/whatsapp.py` catches `httpx.TimeoutException`,
    `httpx.ConnectError` and `httpx.HTTPError` and *returns*
    `{'status': 'error', 'message': ...}`, and `_handle_response` does the
    same for a non-200 status, a body whose own status is `error`, and
    unparseable JSON. So an unpaired bridge, a dead server and a timeout all
    arrive as a returned dict -- never as an exception. A `send` that only
    caught exceptions would report success for every one of them, which is
    worse than no alerting at all: the operator is told the alert was delivered
    when it was not.

    Rules, in order:

    * `None` counts as a failure. It is not evidence of delivery.
    * A dict with `status == 'error'`, or a truthy `code`, is a failure. Both
      markers are used by the SDK, and `code` is present on HTTP errors where
      `status` may be absent, so either is sufficient.
    * A dict whose `data.failed` is a non-empty list is a failure, *even
      though* `status == 'success'`. The broadcast endpoint reports per-recipient
      outcomes inside a success envelope, so a partial delivery arrives as
      `{'status': 'success', 'data': {'failed': [...]}}`. Treating that as
      delivered is the same false-True as the error-dict case: the operator is
      told the alert went out when part of it did not. An absent `failed` key,
      or an empty list, is full delivery.
    * Any other dict is a success -- `{'status': 'success', ...}` is what the
      server returns, and a response shape this bot has never seen should not
      be reported as a delivery failure, which would be its own false alarm.
    * A non-dict response (including a plain string) is not an error report,
      so it passes.
    """
    if response is None:
        return True
    if isinstance(response, dict):
        if response.get("status") == "error":
            return True
        if response.get("code"):
            return True
        return _has_failed_recipients(response)
    return False


def _has_failed_recipients(response: dict) -> bool:
    """True when a success envelope reports one or more failed recipients.

    Looks only inside `data`, and only at a list under `failed`. Anything
    else -- no `data`, `failed` absent, `failed: []`, or a non-list value --
    is treated as full delivery. The leniency is deliberate: an unrecognised
    shape should not be reported as a delivery failure, since a false alarm
    on the alerting path is its own failure mode.
    """
    data = response.get("data")
    if not isinstance(data, dict):
        return False
    failed = data.get("failed")
    return isinstance(failed, list) and bool(failed)


def send(message: str) -> bool:
    """Send `message` to the operator's WhatsApp. Returns True on success.

    Returns False on an exception, on an error response dict, and on a
    *partial* delivery reported inside a success envelope -- see
    `_is_error_response` for why the second case is the common one.

    Never raises for an ordinary failure: a WhatsApp bridge that is unpaired,
    rate-limited or offline degrades to a `False` in a log line while the bot
    keeps trading.
    """
    try:
        # `client()` is looked up as a module global so tests can replace it.
        response = client().whatsapp(message=message, to=RECIPIENT)
    # `Exception`, deliberately NOT `BaseException`: KeyboardInterrupt and
    # SystemExit must propagate so Ctrl-C can stop a live session. Do not
    # "fix" this into a bare `except:` or a BaseException catch -- a bot that
    # swallows Ctrl-C cannot be stopped at all.
    except Exception as exc:  # noqa: BLE001 - notification must never propagate
        logger.warning("whatsapp notification failed: %s", exc)
        return False

    if _is_error_response(response):
        logger.warning("whatsapp notification rejected: %s", response)
        return False
    return True


def _format_value(value: Any) -> str:
    """Render a field value compactly, or `'-'` when it is absent."""
    if value is None:
        return "-"
    return str(value)


#: Canonical field order for `event` output. Keyword order is *not* stable
#: across logically equivalent call sites -- `event("ENTRY", symbol=..., qty=...)`
#: and `event("ENTRY", qty=..., symbol=...)` describe the same event but render
#: differently -- so two callers writing the same event in a different order
#: would produce strings that neither a log grep nor a human comparison can
#: rely on. `event` sorts to this list first, then appends anything not named
#: here in alphabetical order, so an ad-hoc field still renders deterministically.
FIELD_ORDER = (
    "symbol",
    "side",
    "qty",
    "price",
    "entry_price",
    "exit_price",
    "r_multiple",
    "pnl",
    "order_id",
    "reason",
    "note",
)


def _ordered_fields(fields: dict) -> list[tuple[str, Any]]:
    """`fields` as (key, value) pairs in canonical order, unknowns last."""
    known = [key for key in FIELD_ORDER if key in fields]
    extra = sorted(key for key in fields if key not in FIELD_ORDER)
    return [(key, fields[key]) for key in [*known, *extra]]


def event(kind: str, **fields: Any) -> str:
    """One event line: the kind, then `key=value` per field in canonical order.

    The order comes from `FIELD_ORDER`, not from keyword order, so the same
    event always renders to the same string. No lookup, formatting, or
    reflection -- only the values the caller passed are ever printed.
    """
    parts = [str(kind)]
    parts.extend(f"{key}={_format_value(value)}" for key, value in _ordered_fields(fields))
    return " ".join(parts)


def daily_digest(plans: dict, taken: list, skipped: list) -> str:
    """Build the single session summary message.

    `plans` maps symbol to `Plan`; `taken` and `skipped` are symbol lists.
    Everything is rendered into one string so the caller sends exactly one
    message regardless of universe size.

    The header counts are computed from the same deduped, sorted symbol lists
    that produce the lines below them, so a symbol appearing twice in `taken`
    is counted once in both places. Deriving the header from one set and the
    lines from another is how a summary ends up claiming "taken: 2" over a
    single TCS line.
    """
    taken_symbols = sorted(set(taken or ()))
    skipped_symbols = sorted(set(skipped or ()))

    lines = [
        "Intraday breakout bot -- session summary",
        f"plans: {len(plans or {})}  taken: {len(taken_symbols)}  "
        f"skipped: {len(skipped_symbols)}",
    ]

    if taken_symbols:
        lines.append("Taken:")
        lines.extend(_plan_line(plans, symbol) for symbol in taken_symbols)
    else:
        lines.append("Taken: none")

    if skipped_symbols:
        lines.append("Skipped:")
        lines.extend(_plan_line(plans, symbol) for symbol in skipped_symbols)
    else:
        lines.append("Skipped: none")

    return "\n".join(lines)


def _plan_line(plans: dict, symbol: str) -> str:
    """One `SYMBOL` line for the digest, with the reason when we know one.

    Reads a `Plan` by attribute, tolerating a stub or `None`: a symbol named
    in `taken`/`skipped` but absent from `plans` still gets a line rather than
    an exception in the middle of a summary.

    **Two vocabularies, on purpose.** This renders `plan.status` verbatim, so
    a `WATCH_BREAKOUT` plan appears here -- but `journal.write` rejects that
    same token, because the backtest's status set (`WATCH_VOLUME`,
    `WATCH_BREAKOUT`, `WATCH_BREAKDOWN`, `WAIT_RETEST`) is not the journal's
    closed `reason` vocabulary. That asymmetry is intentional: this is a
    human-facing WhatsApp summary where fidelity to what the planner said
    matters more than schema conformance, and the journal is a machine-read
    record that Task 10 groups by. Task 7's implementer should not try to
    reconcile the two -- map to a journal token at the `journal.write` call
    site and leave this surface showing the raw status.
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
    "FIELD_ORDER",
    "RECIPIENT",
    "client",
    "daily_digest",
    "event",
    "send",
]
