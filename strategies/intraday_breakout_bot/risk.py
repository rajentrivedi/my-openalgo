"""Position sizing and pre-entry guardrails for the intraday breakout bot.

Every entry in this bot passes through `evaluate` first. Nothing in `executor`
(Task 7) is allowed to order without a `Decision` with `approved is True`, so
this module is the only place where "how much, if anything" is decided.

The sizing rule is deliberately blunt: risk a fixed rupee amount per trade
(`risk_amount()`, 1% of the budget), divide by the plan's own
`risk_per_share`, and floor to whole shares. Everything else here is a veto:

* **Stop width.** A stop further than 3 ATR away is not this strategy's
  trade; it is a different setup wearing its clothes. Rejected by ATR *and*
  implied by a zero share count.
* **Daily loss.** A flat book past the daily limit stops trading for the day.
* **Concurrency.** Never more than `MAX_CONCURRENT` open positions.
* **Margin.** The broker's own margin figure for the *proposed* position plus
  everything already open, checked against available cash. A probe returned
  ~25% of notional for a MIS intraday position, so four concurrent positions
  roughly consume the whole ₹1L budget -- this is the gate that makes the
  concurrency cap an economic constraint rather than an arbitrary one. It
  fails **closed**: if available cash cannot be determined, the trade is
  blocked.

Two properties are load-bearing and easy to regress:

* **`size_for` never returns a negative or auto-raised quantity.** A stop wider
  than the entire risk budget yields 0, not 1. A single share on a ₹1500 stop
  would risk more than the whole per-trade budget; the correct answer is no
  trade.
* **`evaluate` has a fixed gate order** and reports the *first* gate that
  fails, so a plan that is both waiting on volume and too wide says
  `WATCH_VOLUME`, not `stop_too_wide`. The order is cheapest-to-decide first:
  plan validity, plan quality, account state, then the one gate that costs an
  API round trip.

The `margin()` call carries two SDK quirks that are not optional: `quantity`
must be a *string*, and `pricetype` must be `"MARKET"` -- sending `"SL"` with
no price makes the broker reject the request with a Fyers `limitPrice` error
before any margin maths happens.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import config
from planner import Plan

logger = logging.getLogger(__name__)

#: A stop further than this many ATRs is rejected as too wide.
MAX_STOP_ATR = 3.0

#: Reason vocabulary. `stop_too_wide` covers both the explicit ATR rejection
#: and the zero-size outcome -- a stop too wide to size is the same failure.
REASON_WATCH_VOLUME = "WATCH_VOLUME"
REASON_STOP_TOO_WIDE = "stop_too_wide"
REASON_DAILY_LOSS = "daily_loss_halt"
REASON_CONCURRENCY = "concurrency_cap"
REASON_MARGIN = "margin_blocked"


@dataclass
class Decision:
    """Outcome of running one plan through the gate sequence.

    `quantity` is the share count that was approved, and 0 for every rejected
    plan. `reason` is `None` only when `approved` is True.
    """

    approved: bool
    quantity: int = 0
    reason: str | None = None


def _finite_number(value: Any) -> float | None:
    """Coerce `value` to a finite float, or None if it is not one.

    A single guard for every broker-supplied number in this module, because
    each of these was a real defect at some point:

    * `bool` is excluded explicitly -- `isinstance(True, int)` is True, so a
      stray boolean would otherwise be read as a cash figure of `1.00`.
    * Non-finite values are excluded. `float('nan')` in particular is the
      canonical *undeterminable* figure: `required > nan` is False, so a NaN
      cash figure would sail past every comparison and fail the gate **open**,
      defeating the fail-closed rule. `inf` is rejected for the same reason --
      it would wave through any finite requirement.
    * `None` and non-numeric types (including strings) are excluded rather
      than coerced, so a response-shape change surfaces as "unknown" instead
      of as a plausible-looking number.

    Returning None on rejection is deliberate: every caller treats None as
    "cannot determine this", which is the fail-closed input.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _cash_figure(value: Any) -> float | None:
    """Coerce `value` to a usable *available cash* float, or None if it is not.

    Stricter than `_finite_number` on one axis: a negative balance is a
    perfectly finite number but is not spendable cash, so it is treated as an
    unusable figure rather than as a cash balance of -1.00. Zero, by contrast,
    is a real and meaningful figure (an account with nothing free) and is
    returned as-is -- it must be compared against, not discarded, so that a
    fully-deployed account correctly blocks.

    This is the single entry point for every cash figure in the module, from
    all four sources: the `availablecash` in the margin response, `funds()`,
    the client's `cash` attribute, and the `available_cash` override.
    """
    number = _finite_number(value)
    if number is None or number < 0:
        return None
    return number


def risk_amount() -> float:
    """Rupees at risk per trade: `BUDGET * RISK_PCT / 100` (1000.0)."""
    return config.BUDGET * config.RISK_PCT / 100.0


def size_for(plan: Plan) -> int:
    """Whole shares whose total stop risk fits inside `risk_amount()`.

    Floored at 0 and *never* raised to 1. A zero result means the stop is
    wider than the whole risk budget, which is a "do not trade" signal, not a
    rounding artefact -- rounding it up would silently risk more than the
    budget allows.

    A `risk_per_share` that is not a finite number -- None, a string, NaN --
    returns 0 rather than raising. This sits on the guardrail path, where a
    malformed plan must yield a value the caller can act on; an exception
    here would abort an entry loop over one bad plan.
    """
    per_share = _finite_number(plan.risk_per_share)
    if per_share is None or per_share <= 0:
        return 0

    shares = math.floor(risk_amount() / per_share)
    return max(0, int(shares))


def check_daily_loss(pnl: float) -> bool:
    """True when `pnl` is at or past the daily loss limit -- i.e. halted.

    The comparison is `<=`, so breaching the limit exactly halts rather than
    allowing one more trade on the boundary.
    """
    return pnl <= -config.DAILY_LOSS_LIMIT


def check_concurrency(open_count: int) -> bool:
    """True when `open_count` positions already open blocks another entry."""
    return open_count >= config.MAX_CONCURRENT


def _payload_positions(
    proposed: dict[str, Any], open_positions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Shape proposed + open positions into the list `margin()` expects.

    Every `quantity` is coerced with `str()` and every row carries
    `pricetype="MARKET"`. Both are SDK requirements, not cosmetic: an integer
    quantity is rejected by the API, and a non-MARKET pricetype is rejected by
    the broker.

    Open positions with no `quantity` key are **dropped**, not defaulted. A
    fabricated `"0"` row would be sent to the broker as a real position and
    inflate the broker-side position count, corrupting the very margin figure
    this gate exists to read. A position we cannot describe is a position we
    cannot account for, so the safe move is to leave it out and let the drop
    show up in the log.

    The **proposed** position is treated differently, and deliberately so.
    Dropping it would send an empty or zero-length list and make the broker
    return a requirement for *nothing* -- skipping the margin check rather
    than failing it, which is the exact fail-open this module forbids.
    Defaulting it to `"0"` is no better: it registers a real, zero-quantity
    position at the broker and understates the requirement. So a proposed
    position with no `quantity` raises `ValueError` and the gate fails closed
    without making an API call at all. The trade this could affect is already
    unrepresentable at the broker, so there is nothing worth asking.

    An explicit `quantity` of `0` is *not* absent: it is a real, meaningful
    value that is passed through as `"0"`.
    """
    rows = []
    for position, is_proposed in [(proposed, True), *((p, False) for p in open_positions)]:
        row = dict(position)
        quantity = row.get("quantity")

        if quantity is None:
            if is_proposed:
                raise ValueError(
                    f"proposed position has no 'quantity' key: {position!r}"
                )
            logger.warning(
                "dropping open position with no quantity from the margin "
                "payload: %r",
                position,
            )
            continue

        row["quantity"] = str(quantity)
        row["pricetype"] = "MARKET"
        rows.append(row)
    return rows


def _available_cash(data: dict[str, Any], api_client: Any) -> float | None:
    """Best available estimate of spendable cash, or None if unobtainable.

    Tried in order, cheapest and most authoritative first:

    1. `availablecash` in the margin response itself -- the broker already told
       us, no second round trip.
    2. `api_client.funds()` -- the real SDK path.
    3. A `cash` attribute on the client -- lets a caller (or a test double)
       supply a known figure without an extra call.

    Every value passes through `_cash_figure`, so a bool, a string, NaN,
    infinity or a negative balance is treated as absent rather than as a cash
    figure. NaN is the dangerous one: `required > nan` is False, so accepting
    it would make this function approve a trade it should block.

    Returns None when none of those produce a usable number. The caller treats
    that as "cannot verify", and therefore blocks.
    """
    if isinstance(data, dict):
        direct = _cash_figure(data.get("availablecash"))
        if direct is not None:
            return direct

    funds = getattr(api_client, "funds", None)
    if callable(funds):
        try:
            response = funds()
        except Exception as exc:  # noqa: BLE001 - a failed probe is not fatal
            logger.warning("funds() failed while checking margin: %s", exc)
            response = None
        if isinstance(response, dict):
            payload = response.get("data")
            if isinstance(payload, dict):
                available = _cash_figure(payload.get("availablecash"))
                if available is not None:
                    return available

    return _cash_figure(getattr(api_client, "cash", None))


def check_margin(
    proposed: dict[str, Any],
    open_positions: list[dict[str, Any]],
    api_client: Any,
    *,
    available_cash: float | None = None,
) -> tuple[bool, str]:
    """Ask the broker whether the proposed position fits in available cash.

    Returns `(ok, detail)`. `ok` is False when the margin call fails, returns a
    non-success status, when the required margin exceeds the cash we can
    establish, **or when available cash cannot be established at all**.

    That last case is deliberate and is the gate's governing rule: this check
    is the only mechanism bounding how much capital the bot can deploy beyond
    the budget, so an unknown cash figure must block rather than pass. A
    broker response-shape change, a transient `funds()` failure, or a missing
    key must not silently convert the safety gate into a no-op that approves a
    trade against an arbitrary margin requirement. Every such block logs at
    `error` level naming the reason, so an operator sees *why* the gate fired
    rather than finding a mysteriously untraded day.

    `available_cash` is a caller-supplied short circuit for when the caller
    already knows the cash figure. It is validated by the same `_cash_figure`
    guard as the broker-derived sources; a non-finite or otherwise unusable
    override is logged and discarded in favour of the normal sources, rather
    than being trusted. It must never be a way to smuggle an unchecked figure
    past the gate.
    """
    try:
        positions = _payload_positions(proposed, open_positions)
    except ValueError as exc:
        # An unrepresentable proposed position. Fail closed without an API
        # call: there is no requirement for the broker to compute.
        logger.error("margin gate BLOCKING: %s", exc)
        return False, f"margin call failed: {exc}"

    try:
        response = api_client.margin(positions=positions, pricetype="MARKET")
    except Exception as exc:  # noqa: BLE001 - any broker/transport failure
        logger.warning("margin call raised: %s", exc)
        return False, f"margin call failed: {exc}"

    if not isinstance(response, dict) or response.get("status") != "success":
        return False, f"margin call failed: {response!r}"

    data = response.get("data")
    if not isinstance(data, dict):
        return False, f"margin call failed: {response!r}"

    required = _finite_number(data.get("total_margin_required"))
    if required is None:
        return False, f"margin call failed: no usable total_margin_required in {data!r}"

    if required < 0:
        # A negative requirement is not a small requirement, it is an
        # unreadable one. Treat it as unknown and block rather than letting
        # `-500 > cash` trivially succeed.
        logger.error(
            "margin gate BLOCKING: broker reported a negative total_margin_required "
            "(%r); treating as undeterminable and failing closed", required,
        )
        return False, f"margin call failed: negative total_margin_required {required}"

    # The override goes through `_cash_figure` exactly like the three
    # broker-derived sources. Consuming it raw was the same fail-open the
    # guard exists to prevent, reachable through this module's own public
    # signature: `available_cash=float('nan')` skips the None check, then
    # `required > nan` is False, and the gate approves. A future caller
    # passing one bad value would have silently disabled the safety gate.
    cash = _cash_figure(available_cash)
    if cash is None:
        if available_cash is not None:
            logger.warning(
                "available_cash override %r is not a usable cash figure; "
                "discarding it and falling back to the broker-derived sources",
                available_cash,
            )
        cash = _available_cash(data, api_client)

    if cash is None:
        logger.error(
            "margin gate BLOCKING: %.2f margin required but available cash could "
            "not be determined from the margin response, funds(), or the client. "
            "Failing closed -- an unknown cash figure must never approve a trade, "
            "because this gate is the only bound on capital deployed beyond the "
            "budget. Check the OpenAlgo/funds response shape and API key.",
            required,
        )
        return False, (
            f"margin blocked: available cash could not be determined to cover "
            f"{required:.2f} required"
        )

    if required > cash:
        return False, (
            f"margin blocked: {required:.2f} required exceeds "
            f"available {cash:.2f}"
        )

    return True, f"margin ok: {required:.2f} required of {cash:.2f} available"


def evaluate(
    plan: Plan,
    open_count: int,
    day_pnl: float,
    open_positions: list[dict[str, Any]],
    api_client: Any,
) -> Decision:
    """Run one plan through every guardrail; approve only if all pass.

    Gate order is fixed and each gate reports before the next runs:

        error -> WATCH_VOLUME -> stop_atr -> daily loss -> concurrency
              -> zero size -> margin

    Plan-level facts (error, volume confirmation, stop width) are checked before
    account-level state (loss, concurrency), and the only gate that costs an
    API round trip runs last, so a doomed plan never spends one. The first
    failing gate is the one reported, which keeps the rejection reason
    actionable rather than a list.

    **Caller contract for `open_positions` (Task 7):** this argument must be
    the *current* broker positions, fetched at call time. Do not capture a
    list once at loop start and reuse it across entries -- `check_margin`
    sums the proposed position against everything in this list, so a stale
    snapshot approves the day's fourth entry against stale broker state and
    the cumulative margin bound silently drifts. Likewise `open_count` must
    agree with `open_positions` at the same instant. Read fresh state
    immediately before each entry; a one-trade-late margin check is exactly
    the failure this gate exists to prevent.

    The rows must also use the **key shape this module expects**: a
    `quantity` key per position (plus whatever `symbol`/`exchange` the broker
    returned). A position missing `quantity` is dropped from the margin
    payload with a warning, so a wholesale key mismatch -- passing `qty`,
    say, or a reshaped broker dict -- would silently under-report real
    exposure and the gate would compare a requirement that excludes positions
    you actually hold. **A shape mismatch is the caller's error, not this
    module's to absorb.** Task 7 should assert that the positions list is
    well-formed and non-empty whenever positions are genuinely open; that
    check belongs at the Task 7 boundary, and this contract exists so it can
    be written as an acceptance criterion there.

    **Caller contract for `Decision.reason` (Task 7):** every gate returns one
    of the fixed vocabulary values *except* the `error` gate, which passes
    `plan.error` through verbatim -- it is an operator-facing diagnostic
    string (`"history_error: ..."`, `"KeyError: 'entry'"`) rather than a
    token, and no test pins it. Journal validation must map or normalise it
    before writing; treat it as untrusted free text, not as a reason code.
    """
    if plan.error is not None:
        # Reason is `plan.error` verbatim, deliberately outside the fixed
        # vocabulary: this is a diagnostic, not a code. See the caller contract
        # in `evaluate`'s docstring -- Task 7 must map it before journalling.
        return Decision(approved=False, quantity=0, reason=plan.error)

    if plan.status == REASON_WATCH_VOLUME:
        return Decision(approved=False, quantity=0, reason=REASON_WATCH_VOLUME)

    if plan.stop_atr is not None and plan.stop_atr > MAX_STOP_ATR:
        return Decision(approved=False, quantity=0, reason=REASON_STOP_TOO_WIDE)

    if check_daily_loss(day_pnl):
        return Decision(approved=False, quantity=0, reason=REASON_DAILY_LOSS)

    if check_concurrency(open_count):
        return Decision(approved=False, quantity=0, reason=REASON_CONCURRENCY)

    quantity = size_for(plan)
    if quantity == 0:
        return Decision(approved=False, quantity=0, reason=REASON_STOP_TOO_WIDE)

    proposed = {
        "symbol": plan.symbol,
        "quantity": quantity,
        "exchange": config.EXCHANGE,
        "product": config.PRODUCT,
    }
    ok, detail = check_margin(proposed, open_positions, api_client)
    if not ok:
        logger.info("margin gate blocked %s: %s", plan.symbol, detail)
        return Decision(approved=False, quantity=0, reason=REASON_MARGIN)

    return Decision(approved=True, quantity=quantity, reason=None)


__all__ = [
    "MAX_STOP_ATR",
    "Decision",
    "check_concurrency",
    "check_daily_loss",
    "check_margin",
    "evaluate",
    "risk_amount",
    "size_for",
]
