"""Replay harness: drives `executor.Trade` over stored bars.

Test-support only -- nothing on the live path imports this module.

`backtest_source.load().simulate_trade` is the ground truth for what the live
bot must reproduce: same bars, same entry fill assumption, same bar-by-bar
exit ordering, same R denominator. This module mirrors `simulate_trade`'s
*loop* but delegates every decision about a live position to `Executor`:

* the fill is handed to `Executor.on_entry_filled`;
* each subsequent bar -- **and the entry bar itself** -- goes through
  `Executor.on_bar`, which owns the stop / T1 / T2 ordering and calls
  `on_t1_touched` (so the broker-side stop modification is exercised too, not
  stubbed past);
* the resolved exit goes through `Executor.on_exit_filled`, which is where R
  is computed.

The loop here keeps only what genuinely belongs to the harness: how bars are
selected (after the trigger, up to and including the expiry), and the entry
trigger, which in production is the broker's job rather than the bot's.

**On the entry bar.** The entry bar is evaluated for an exit exactly as
`simulate_trade` does -- deliberately, and pinned by tests. See "Why the entry
bar is evaluated" below.

`FakeApi` is the same double `test_executor.py` uses, so a replay exercises
the exact API shapes the unit tests pin.


Why the entry bar is evaluated
------------------------------

**Round 1 of the Task 7 review** found that this harness used to `continue`
past the entry bar, skipping its exit evaluation, while `simulate_trade` falls
through and evaluates it in the same iteration. The gate could not see it:
deleting the `continue` left all nine replay tests green, because the harness
was reproducing the omission rather than checking for it. That also falsified
this module's earlier claim that any divergence is "a divergence in the
executor's live exit logic, not in the harness" -- the harness could conceal a
bug by copying one.

**Conclusion: the harness was wrong; `simulate_trade` is right.**

The evidence is `INFY 2026-05-12`, where the two disagree by ~4.8R (backtest
`STOP` -1.000R; the skipping harness `EXPIRY` -0.209R). The entry bar is
10:20, a SHORT with `entry=1131.10`, `stop=1135.40`:

    bar 10:20   open 1133.80   high 1135.50   low 1131.10

`open=1133.80` is a real print at the first instant of the bar. From it, price
reached the entry trigger 1131.10 *and* the stop 1135.40, in some order OHLC
cannot express. So:

* If the **low came first** (simulate_trade's reading): the entry fills at
  1131.10, the protective stop is live from that instant, and price then
  rallies through 1135.40 -> `STOP`, -1.000R.
* If the **high came first** (the skipping harness's implicit reading): price
  rises to 1135.50 with no position, then falls to 1131.10 and the short fills
  there. But the bracket's protective stop is a sell stop resting at 1135.40
  while the market sits at 1131.10 -- already *through* it. A stop order on the
  wrong side of the market is marketable and fills immediately at roughly the
  fill price, so this branch cannot produce a clean -0.209R round trip either;
  it produces an immediate scratch at best.

So the second reading does not rescue the harness, it only relocates the loss.
Every realistic path through this bar loses, and `simulate_trade` books it at
the canonical -1.000R.

Three general reasons follow, and they are the durable argument:

1. **A bracket is live from the fill.** `on_entry_filled` places the SL-M
   bracket, so the resting stop and target exist the moment the position does.
   No broker mechanism knows that the entry bar's earlier excursion preceded
   the fill, and none needs to.
2. **`simulate_trade`'s rule is "earliest possible fill", not "worst case".**
   It assumes the entry triggers at the first instant consistent with the
   trigger price, after which the rest of the bar is available to both stop
   and targets. That is precisely how a real stop order behaves -- it fills as
   soon as price trades through the trigger, never at the bar close. The rule
   is symmetric (it credits favourable excursions too), so it is a fill-timing
   model, not a pessimism trick.
3. **The error direction matters.** Skipping the entry bar makes live results
   systematically *better* than backtested, because trades the backtest killed
   on the entry bar survive. That silently invalidates every expectancy,
   profit-factor and daily-loss-limit figure derived from the backtest, in the
   optimistic direction. A gate that lets live results flatter the backtest is
   worse than no gate.

Frequency over the stored history (2026-01-01..2026-10-09, 20 symbols, 338
trades that actually entered): the entry bar alone resolves a `STOP` on **6
trades (1.8%)** and alone touches T1 on a further **5 (1.5%)**. Small in
aggregate, but each is a wholly different booked outcome, and 1.8% is not a
negligible error rate for a risk system.
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
            # Deliberately NO `continue` here.
            #
            # Round 1 of the Task 7 review found this `continue` and the gate
            # did not catch it: the harness reproduced simulate_trade's
            # fall-through only by accident of its own shape, and the claim
            # that a replay/simulate_trade divergence is "a divergence in the
            # executor's live exit logic, not in the harness" was therefore
            # false -- the harness could hide a bug by copying a bug.
            #
            # The entry bar IS evaluated. See the module docstring: a bracket
            # is live from the fill, and simulate_trade's "entry fills at the
            # earliest moment consistent with the trigger, remainder of bar
            # available" is exactly what a stop order does.

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
