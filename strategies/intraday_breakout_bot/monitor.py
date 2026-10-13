"""Poll-based live monitor: reads broker state, drives Executor transitions.

No new thread: `Bot._manage_open` calls `poll` once per open trade per pass.
Reads only -- every order action goes through `Executor` (see the module
invariant in `executor.py`). `poll` never raises: one unreadable order or quote
must not abort the pass over the remaining trades.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import config
from executor import Trade, is_expired

logger = logging.getLogger(__name__)


def poll(api: Any, executor: Any, trade: Trade, *, now: datetime.datetime) -> None:
    """Advance one open `trade` by one poll. Never raises.

    Driven entirely by broker reads: it resolves a pending entry's fill, expiry
    or rejection, then moves an open position's stop at T1 and books the first
    completed exit leg. Every state change is delegated to the executor.
    """
    if config.DRY_RUN:
        return
    try:
        if trade.state == "entry_pending":
            _poll_entry(api, executor, trade, now=now)
        elif trade.state in ("entry_filled", "t1_hit"):
            _poll_open(api, executor, trade)
    except Exception:  # noqa: BLE001 - one bad trade must not abort the pass
        logger.exception("monitor poll failed for %s", trade.symbol)


def _poll_entry(
    api: Any, executor: Any, trade: Trade, *, now: datetime.datetime
) -> None:
    """Resolve a pending entry: fill, rejection, or expiry."""
    data = _order_status(api, trade.entry_order_id)
    if data is not None:
        status = data.get("order_status")
        if status == "complete":
            executor.on_entry_filled(trade, _fill_price(data, trade))
            return
        if status in ("rejected", "cancelled"):
            executor.drop_unfilled(trade, reason="order_rejected")
            return
    if is_expired(trade.expires_at, now=now):
        executor.cancel_stale_entry(trade, now=now)


def _poll_open(api: Any, executor: Any, trade: Trade) -> None:
    """Trail the stop at T1, then book whichever bracket leg has filled."""
    if not trade.t1_hit:
        ltp = _ltp(api, trade.symbol)
        if ltp is not None and _t1_touched(trade, ltp):
            executor.on_t1_touched(trade)

    # Target first: if both legs read complete in the same pass, the target is
    # the one that must win (it is the profitable fill; the stop is a phrase).
    target = _order_status(api, trade.target_order_id)
    if target is not None and target.get("order_status") == "complete":
        executor.on_exit_filled(trade, _fill_price(target, trade), "T2")
        executor.cancel_resting(trade.stop_order_id)
        return

    stop = _order_status(api, trade.stop_order_id)
    if stop is not None and stop.get("order_status") == "complete":
        reason = "T1_TRAIL" if trade.t1_hit else "STOP"
        executor.on_exit_filled(trade, _fill_price(stop, trade), reason)
        executor.cancel_resting(trade.target_order_id)


def _t1_touched(trade: Trade, ltp: float) -> bool:
    """True when the last traded price has reached the first target."""
    if trade.target1 is None:
        return False
    if trade.side == "LONG":
        return ltp >= float(trade.target1)
    return ltp <= float(trade.target1)


def _order_status(api: Any, order_id: str | None) -> dict | None:
    """The order's data dict, or None when there is no usable status."""
    if not order_id:
        return None
    try:
        response = api.orderstatus(order_id=order_id)
    except Exception:  # noqa: BLE001 - an unreadable order is simply "no status"
        logger.exception("could not read order status for %s", order_id)
        return None
    if isinstance(response, dict) and response.get("status") == "success":
        data = response.get("data")
        if isinstance(data, dict):
            return data
    return None


def _ltp(api: Any, symbol: str) -> float | None:
    """Last traded price, or None when there is no usable quote."""
    try:
        response = api.quotes(symbol=symbol, exchange=config.EXCHANGE)
    except Exception:  # noqa: BLE001 - an unreadable quote is simply "no price"
        logger.exception("could not read a quote for %s", symbol)
        return None
    if isinstance(response, dict) and response.get("status") == "success":
        data = response.get("data")
        if isinstance(data, dict):
            try:
                return float(data["ltp"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _fill_price(data: dict, trade: Trade) -> float:
    """The fill price for a completed order, falling back to the planned level."""
    try:
        return float(data["average_price"])
    except (KeyError, TypeError, ValueError):
        return float(trade.entry or 0.0)
