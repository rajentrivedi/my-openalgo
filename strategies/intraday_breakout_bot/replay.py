"""Replay harness: drives `executor.Trade` over stored bars.

Test-support only -- nothing on the live path imports this module.

`backtest_source.load().simulate_trade` is the ground truth for what the live
bot must reproduce: same bars, same entry fill assumption, same bar-by-bar
exit ordering, same R denominator. This module mirrors `simulate_trade`'s
*loop* but delegates every decision about a live position to `Executor`:

* the fill is handed to `Executor.on_entry_filled`;
* each subsequent bar goes through `Executor.on_bar`, which owns the
  stop / T1 / T2 ordering and calls `on_t1_touched` (so the broker-side stop
  modification is exercised too, not stubbed past);
* the resolved exit goes through `Executor.on_exit_filled`, which is where R
  is computed.

So a divergence between `replay` and `simulate_trade` is a divergence in the
executor's live exit logic, which is the whole point of the gate. The loop
here keeps only what genuinely belongs to the harness: how bars are selected
(after the trigger, up to and including the expiry), and the entry trigger,
which in production is the broker's job rather than the bot's.

`FakeApi` is the same double `test_executor.py` uses, so a replay exercises
the exact API shapes the unit tests pin.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from executor import Executor, Trade


class FakeApi:
    """In-memory OpenAlgo client double. Records every call it receives."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.modified: list[dict] = []
        self.cancelled: list[str] = []
        self.closed: list[dict] = []
        self.margin_calls: list[list[dict]] = []

    def placesmartorder(self, **kwargs):
        self.orders.append(kwargs)
        return {"status": "success", "data": {"orderid": f"O{len(self.orders)}"}}

    def modifyorder(self, **kwargs):
        self.modified.append(kwargs)
        return {"status": "success"}

    def cancelorder(self, order_id, **kwargs):
        self.cancelled.append(order_id)

    def closeposition(self, **kwargs):
        self.closed.append(kwargs)

    def margin(self, positions=None, pricetype=None):
        self.margin_calls.append(positions or [])
        return {
            "status": "success",
            "data": {"total_margin_required": 1000.0, "availablecash": 100000.0},
        }


def replay(
    symbol: str,
    df: pd.DataFrame,
    day: dt.date,
    side: str,
    plan: dict[str, Any],
    trigger_ts: pd.Timestamp,
    *,
    api: FakeApi | None = None,
) -> dict[str, Any] | None:
    """Run `Executor` over `day`'s bars exactly as `simulate_trade` would.

    Returns `{"reason", "r_multiple"}` -- the two fields `test_replay.py`
    asserts against the backtest -- or None when the backtest would not have
    entered at all (no bars in the window, or the entry never triggered).

    `api` lets a caller supply the double instead of getting a throwaway one,
    so a test can assert on the order calls the state machine made rather than
    only on its outcome. Note those calls are still suppressed while
    `config.DRY_RUN` is set -- the *state machine* runs, the broker does not.
    """
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    t1 = float(plan["target1"])
    t2 = float(plan["target2"])
    expiry = pd.Timestamp(plan["expires_at"])

    # Bar window, verbatim from simulate_trade: strictly after the trigger bar
    # (that bar is where the signal was confirmed, not where we could fill) and
    # up to and including the expiry bar.
    bars = df[df.index.date == day]
    bars = bars[bars.index > trigger_ts]
    bars = bars[bars.index <= expiry]
    if bars.empty:
        return None

    ex = Executor(api if api is not None else FakeApi())

    trade = Trade(
        symbol=symbol,
        side=side,
        qty=1,
        entry=entry,
        stop=stop,
        target1=t1,
        target2=t2,
        risk_per_share=abs(entry - stop),
        state="entry_pending",
        expires_at=expiry,
    )

    for _, row in bars.iterrows():
        o, h, low = float(row["open"]), float(row["high"]), float(row["low"])

        if trade.entry_price is None:
            # The broker's fill assumption, mirroring simulate_trade: a bar
            # that opens beyond the trigger fills at the open, not the trigger.
            if side == "LONG":
                if h < entry:
                    continue
                fill = max(entry, o)
            else:
                if low > entry:
                    continue
                fill = min(entry, o)
            ex.on_entry_filled(trade, fill)
            continue

        outcome = ex.on_bar(trade, h, low)
        if outcome is not None:
            reason, exit_price = outcome
            ex.on_exit_filled(trade, exit_price, reason)
            return {"reason": trade.exit_reason, "r_multiple": trade.r_multiple}

    if trade.entry_price is None:
        return None

    ex.on_exit_filled(trade, float(bars.iloc[-1]["close"]), "EXPIRY")
    return {"reason": trade.exit_reason, "r_multiple": trade.r_multiple}


__all__ = ["FakeApi", "replay"]
