"""Order state machine for the intraday breakout bot.

`Executor` is the only module in the bot that is allowed to place, modify,
cancel or close an order. Everything upstream of it -- `planner` decides *what*
to trade, `risk` decides *how much* -- produces data, never an order.

Five things here are load-bearing and were each the subject of a blocking
carry-forward from the Task 4/5/6 reviews:

1. **`journal_reason` normalises every reason before it reaches `journal.write`.**
   `journal.write` raises `ValueError` on any reason outside its closed
   vocabulary, and two producers are outside it: `risk.evaluate` returns
   `plan.error` *verbatim* as free text (`"KeyError: 'entry'"`), and
   `plan.status` is `WATCH_BREAKOUT` / `WATCH_BREAKDOWN` / `WAIT_RETEST` for
   plans that `risk.evaluate` **approves**. Journalling either verbatim turns
   a routine decision into a `ValueError` on the entry loop, so the mapping
   lives in exactly one function, `journal_reason`, with every branch tested.

2. **`WAIT_RETEST` is refused, not chased.** The planner emits `WAIT_RETEST`
   once price has *already* crossed the planned entry. Placing an entry order
   at `entry` there buys at a worse price than the plan was built around, and
   the stop was sized for the *original* risk, so the realised per-share risk
   exceeds `risk_per_share` while the sizing arithmetic still believes it
   does not. `decide` therefore downgrades it to a rejection carrying the new
   `WAIT_RETEST` journal token.

3. **`decide` re-reads broker positions at call time.** `risk.evaluate`
   receives `open_count` and `open_positions` and nothing enforces that they
   are fresh; a list captured once at loop start approves the day's fourth
   entry against stale broker state and the cumulative margin bound drifts.
   `decide` takes a `fetch_positions` callable and invokes it immediately
   before `evaluate`, derives `open_count` from that same list so the two can
   never disagree, and **asserts the row shape** -- a position row without a
   `quantity` key is silently dropped by `risk._payload_positions`, so a
   wholesale key mismatch would under-report real exposure rather than fail.

4. **Nothing here calls `check_daily_loss` / `check_concurrency` /
   `check_margin` directly.** They are `risk.evaluate`'s implementation
   detail; calling them would bypass the fixed gate order and the
   fail-closed margin logic. `decide` is the only constructor of an approved
   `Decision`, and `place_entry` refuses any decision that is not approved.

5. **`expires_at` is checked here, because `risk.evaluate` never checks it.**
   The backtest caps a trade at its horizon (and `simulate_trade` returns
   `None` for bars past it), so an expired plan is still approvable by risk.
   `place_entry` refuses one outright and `cancel_stale_entry` cancels a
   working entry whose horizon has passed.

DRY_RUN (`config.DRY_RUN`, read at call time so tests can flip it) suppresses
every API call while leaving the state machine and the journal fully intact:
a dry session records exactly the orders it *would* have sent, which is what
makes it a rehearsal rather than a no-op.

**Fill prices and R.** The R-multiple is always measured against the *planned*
stop and the *actual* fill price, never against the broker-rounded order
levels. `on_entry_filled` rounds the resting stop to `TICK_SIZE` for the order
payload but leaves `Trade.stop` at the planned value, so a sub-tick rounding
of the stop the broker actually holds can never silently redefine the risk
unit the trade was sized against -- and so the live R matches
`simulate_trade`'s exactly, which is what `test_replay.py` asserts.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

import config
import journal
import notify
import risk
from planner import Plan

logger = logging.getLogger(__name__)

#: Exchange timezone, re-exported so callers and tests can build aware
#: timestamps without importing `config` themselves.
TZ = config.TZ

#: Minimum price increment, mirroring the backtest's own `tick_size` default.
TICK_SIZE = 0.05

#: `plan.status` values that mean "tradeable" but are *not* in `risk`'s gate
#: vocabulary. They are journalled verbatim (see `journal.py`) rather than
#: collapsed into a catch-all, so each stays its own group downstream.
PLAN_STATUS_TOKENS = frozenset({"WATCH_BREAKOUT", "WATCH_BREAKDOWN", "WAIT_RETEST"})

#: The one status that must not be traded. See carry-forward (2).
STATUS_WAIT_RETEST = "WAIT_RETEST"

#: Plan statuses the planner emits; anything else is a rename we should not
#: silently accept. Only used for the log line in `decide`.
KNOWN_STATUSES = PLAN_STATUS_TOKENS | {"WATCH_VOLUME"}

#: Synthetic order-id prefix used while `DRY_RUN` is on, so a journalled row
#: is never mistaken for a broker order id.
DRY_ORDER_PREFIX = "DRY"

_STATES = frozenset(
    {"entry_pending", "entry_filled", "t1_hit", "exited", "cancelled"}
)


def round_tick(price: float, up: bool) -> float:
    """Round `price` to a `TICK_SIZE` multiple, upward or downward by `up`.

    `up=True` ceils toward +infinity, `up=False` floors toward -infinity -- so
    the result moves to the tick grid in the direction `up` names, never
    "away from zero". Those differ for a negative price: ceiling -3500.037 is
    -3500.0, which is *toward* zero. Say which direction you want explicitly
    (`round_tick(stop, up=not is_long)`) rather than relying on a sign-based
    reading; every call site in this module needs the direction to be the safe
    one for the position, which is a function of side, not of sign.

    A stop or target must round in the protective direction so it can never
    end up *inside* the level it is meant to hold.

    `up=True` ceils, `up=False` floors -- the direction a stop or target must
    round so that it can never end up *inside* the level it is meant to hold.
    A stop that rounds in the wrong direction silently widens or narrows the
    real risk.

    Uses `Decimal` rather than arithmetic on the float. `3500.037 / 0.05` in
    binary floating point is `70000.74000000001`, which floors correctly but is
    one ULP away from a boundary in general; going through `Decimal(str(...))`
    makes the tick grid exact and the direction unambiguous.
    """
    step = Decimal(str(TICK_SIZE))
    steps = Decimal(str(price)) / step
    integral = steps.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(integral * step)


def entry_limit_price(plan: Plan, atr: float) -> float:
    """Worst price we will pay for an entry: 0.1 ATR beyond the trigger.

    LONG pays up to `entry + 0.1*ATR`, SHORT down to `entry - 0.1*ATR`. The
    offset lets the order fill through a small overshoot of the trigger
    instead of being left behind by a 5-minute bar that jumped it, while the
    0.1 ATR bound keeps the worst-case fill inside the plan's risk envelope --
    a plain market order could fill arbitrarily far past `entry` and break the
    sizing arithmetic.

    A non-finite or missing ATR yields a limit equal to the trigger, which
    degrades to a plain stop-limit rather than raising on the entry path.
    """
    try:
        offset = 0.1 * float(atr)
    except (TypeError, ValueError):
        offset = 0.0
    if offset != offset or offset in (float("inf"), float("-inf")):
        offset = 0.0

    entry = float(plan.entry)
    return entry + offset if plan.side == "LONG" else entry - offset


def _as_datetime(value: Any) -> datetime.datetime | None:
    """Coerce `value` to an aware `datetime`, or None.

    Accepts `datetime.datetime` directly and anything exposing `to_pydatetime`
    (i.e. `pd.Timestamp`) without importing pandas -- `planner` produces
    Timestamps, callers and tests produce datetimes, and both must compare
    against `now` without a TypeError. Naive values are localised to `TZ`
    rather than rejected: a naive expiry is a caller bug we can still honour.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        stamp = value
    elif hasattr(value, "to_pydatetime"):
        stamp = value.to_pydatetime()
    else:
        raise TypeError(f"cannot interpret {value!r} as an expiry timestamp")
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=TZ)
    return stamp


def is_expired(expires_at: Any, *, now: datetime.datetime | None = None) -> bool:
    """True when `expires_at` has passed (or is exactly now).

    A plan with no expiry never expires: `planner` leaves `expires_at` as None
    when the backtest emitted none, and treating that as "already expired"
    would silently refuse every such trade.
    """
    stamp = _as_datetime(expires_at)
    if stamp is None:
        return False
    return stamp <= (now or datetime.datetime.now(tz=TZ))


def journal_reason(reason: Any, *, plan: Plan | None = None) -> str | None:
    """Map any producer reason onto the closed `journal.REASONS` vocabulary.

    Returns a token `journal.write` will accept, or None for "no reason".
    Everything funnels through here because `journal.write` raises
    `ValueError` on an unknown reason, and two producers emit free text or a
    non-token status:

    * **`risk.evaluate`'s error gate returns `plan.error` verbatim.** That is
      an operator-facing diagnostic (`"KeyError: 'entry'"`,
      `"history_error: <exc>"`), not a code. It maps to `history_error`; the
      original text is preserved separately by the caller in the journal's
      free-text `note` column, so nothing is lost.
    * **`plan.status` is `WATCH_BREAKOUT` / `WATCH_BREAKDOWN` /
      `WAIT_RETEST` for plans `risk.evaluate` approves.** Those three are
      added to `journal.REASONS` (see `journal.py`) so a taken entry keeps its
      own aggregator bucket rather than collapsing into a catch-all.
    * Anything unrecognised falls back to `history_error` rather than raising.
      That fallback is the safe direction: a bucket the weekly scorer cannot
      interpret is recoverable, whereas a `ValueError` here aborts the entry
      loop over one malformed plan.
    """
    text = reason or getattr(plan, "status", None)
    if not text:
        return None
    if text in journal.REASONS:
        return text
    if isinstance(text, str) and text.startswith("history_error"):
        return "history_error"
    logger.info("journal_reason: unmapped reason %r -> history_error", text)
    return "history_error"


def _read_summary(rows: list[dict]) -> str:
    """One-line summary of the position rows a risk decision was made against.

    Journalled into `note` so a dry session records *what the gate actually
    read*, not just the verdict. A margin rejection with no record of the
    positions behind it is not reproducible after the fact.
    """
    if not rows:
        return "dry-mode read: no open positions"
    described = ",".join(
        f"{row.get('symbol', '?')}x{row.get('quantity', '?')}" for row in rows
    )
    return f"dry-mode read: {len(rows)} open position(s) [{described}]"


def assert_position_rows(rows: Any) -> list[dict]:
    """Validate the broker position rows `risk.evaluate` is about to receive.

    `risk._payload_positions` **drops** any open position with no `quantity`
    key, logging a warning. That is the right behaviour there -- it refuses to
    invent a position it cannot describe -- but it means a wholesale key-shape
    mismatch (passing `qty` instead of `quantity`, or a reshaped broker dict)
    does not fail: every row vanishes, the margin gate compares a requirement
    that excludes positions we actually hold, and the day under-reports its own
    exposure. The check belongs on this side of the boundary, so it raises.

    Returns the rows unchanged (as a list) so callers can use the return value
    directly rather than trusting their input's container type.
    """
    if rows is None:
        return []
    if not isinstance(rows, (list, tuple)):
        raise ValueError(f"open positions must be a list of dicts, got {type(rows).__name__}")

    checked = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"open position row {index} is {type(row).__name__}, not a dict")
        if row.get("quantity") is None:
            raise ValueError(
                f"open position row {index} has no 'quantity' key ({row!r}); risk.evaluate "
                "would drop it and under-report exposure"
            )
        checked.append(dict(row))
    return checked


@dataclass
class Trade:
    """One live (or dry-run) position and its order ids.

    `symbol` is the only required field; everything else defaults so a
    partially-known trade can be reconstructed for repair -- which is exactly
    the case `reconcile` has to handle after a crash between the entry fill
    and the stop placement.

    `stop` / `target1` / `target2` hold the **planned** levels and are never
    overwritten by tick-rounding, because they define the R-multiple.
    `current_stop` is the level actually working at the broker: `stop` until
    T1 is touched, `target1` after. Keeping the two apart is what lets the
    resting order be tick-correct without redefining the trade's risk unit.
    """

    symbol: str
    side: str | None = None
    qty: int = 0
    entry: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    risk_per_share: float | None = None
    entry_order_id: str | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    state: str = "entry_pending"
    t1_hit: bool = False
    entry_price: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    expires_at: Any = None
    current_stop: float | None = None
    r_multiple: float | None = None
    pnl: float | None = None
    plan: Any = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.current_stop is None:
            self.current_stop = self.stop


class Executor:
    """The bot's order state machine. One instance per session.

    `api_client` is the OpenAlgo SDK client; tests inject a double. The
    instance owns `open_positions`, the symbol -> `Trade` map that `decide`
    and `reconcile` both read.
    """

    def __init__(self, api_client: Any) -> None:
        self.api = api_client
        self.open_positions: dict[str, Trade] = {}
        self._dry_order_seq = 0

    # -- helpers ------------------------------------------------------------

    def _next_dry_order_id(self) -> str:
        self._dry_order_seq += 1
        return f"{DRY_ORDER_PREFIX}-{self._dry_order_seq}"

    def _order_id(self, response: Any) -> str:
        """Pull the order id out of a `placesmartorder` response.

        A response we cannot read is an *unplaced* order as far as we are
        concerned -- the trade must not be recorded as having a live working
        order id we cannot cancel later. Returns "" and logs.
        """
        if not isinstance(response, dict) or response.get("status") != "success":
            logger.error("order placement failed: %r", response)
            return ""
        data = response.get("data")
        order_id = data.get("orderid") if isinstance(data, dict) else data
        if not order_id:
            logger.error("order placement returned no order id: %r", response)
            return ""
        return str(order_id)

    def _notify(self, kind: str, **fields: Any) -> None:
        """Best-effort notification. A failed one must never break trading."""
        try:
            notify.send(notify.event(kind, **fields))
        except Exception as exc:  # noqa: BLE001 - courtesy path only
            logger.warning("notification %s failed: %s", kind, exc)

    def _record(
        self,
        trade_or_symbol: Trade | str,
        *,
        stage: str,
        decision: str,
        reason: Any = None,
        side: str | None = None,
        note: str = "",
        **fields: Any,
    ) -> None:
        """Write one journal row for a decision this executor made.

        Every journal write in the module goes through here so that
        `journal_reason` is applied uniformly -- a single call site that can
        be forgotten is the bug this indirection exists to prevent.
        """
        if isinstance(trade_or_symbol, Trade):
            trade = trade_or_symbol
            symbol, side = trade.symbol, trade.side
            plan = trade.plan
        else:
            trade = None
            symbol, plan = trade_or_symbol, fields.get("plan")
            side = side or getattr(plan, "side", None)

        journal.write(
            day=fields.pop("day", datetime.datetime.now(tz=TZ).date()),
            symbol=symbol,
            side=side or "",
            stage=stage,
            decision=decision,
            reason=journal_reason(reason, plan=plan),
            plan=plan,
            qty=fields.pop("qty", trade.qty if trade else 0),
            order_id=fields.pop("order_id", trade.entry_order_id if trade else ""),
            entry_price=fields.pop("entry_price", trade.entry_price if trade else None),
            exit_price=fields.pop("exit_price", trade.exit_price if trade else None),
            r_multiple=fields.pop("r_multiple", trade.r_multiple if trade else None),
            pnl=fields.pop("pnl", trade.pnl if trade else None),
            note=note,
        )

    # -- entry path ---------------------------------------------------------

    def decide(
        self,
        plan: Plan,
        *,
        day_pnl: float = 0.0,
        fetch_positions: Callable[[], list[dict]],
    ) -> risk.Decision:
        """Gate `plan` through `risk.evaluate` and journal the outcome.

        **This is the only place a `Decision` is created.** It exists so the
        three properties below cannot be got wrong at a call site:

        * *Fresh state.* `fetch_positions()` is invoked here, immediately
          before `evaluate`, and `open_count` is derived from the very list
          that is passed as `open_positions` -- so the count and the rows can
          never be from different instants.
        * *Right shape.* `assert_position_rows` raises on a row `risk` would
          silently drop.
        * *Single gate.* `risk.evaluate` is called; no `check_*` is.

        On a rejection it returns the rejection and journals it
        (`stage="risk"`, `decision="skipped"`). On approval it returns an
        approved `Decision` for `place_entry`; nothing is journalled yet,
        because nothing has been sent.

        `WAIT_RETEST` is turned into a rejection here rather than traded --
        see carry-forward (2).

        **Read APIs are NOT suppressed by `DRY_RUN`; only writes are.**
        Round 1 of the Task 7 review flagged that this method calls
        `fetch_positions()` and hands `self.api` to `risk.evaluate`, which
        calls `margin()` and possibly `funds()`, so a dry session does issue
        API traffic. That is a deliberate decision, not an oversight:

        * None of these calls can place, modify, cancel or close an order.
          `DRY_RUN`'s purpose -- never let a rehearsal move the account -- is
          intact, and `test_dry_run_places_no_orders_but_still_advances_state`
          is the guard for that.
        * The margin gate is precisely the logic worth rehearsing. Stubbing
          the reads would make dry mode report approvals that live mode would
          not produce, which is the dangerous direction: a rehearsal that is
          systematically more permissive than the thing it rehearses teaches
          the operator the wrong thing.
        * It is journalled. The rows that were read are summarised into the
          `note` column of the decision row, so the dry journal records what
          the gate actually saw.
        """
        rows = assert_position_rows(fetch_positions())

        decision = risk.evaluate(plan, len(rows), day_pnl, rows, self.api)

        if decision.approved and plan.status == STATUS_WAIT_RETEST:
            decision = risk.Decision(
                approved=False, quantity=0, reason=STATUS_WAIT_RETEST
            )
        elif decision.approved and plan.status not in KNOWN_STATUSES:
            # `risk.evaluate` approves anything that is not `WATCH_VOLUME`, so
            # a future planner rename would be traded silently. Refuse, loudly.
            logger.error(
                "%s: unrecognised plan.status %r; refusing to trade", plan.symbol, plan.status
            )
            decision = risk.Decision(approved=False, quantity=0, reason="history_error")

        if not decision.approved:
            note = _read_summary(rows)
            if decision.reason == STATUS_WAIT_RETEST:
                note = f"{note}; entry already crossed; not chasing at a worse price"
            elif plan.error:
                # Free text the vocabulary cannot hold. `history_error` is the
                # bucket; the original text lives here so the operator still
                # gets the diagnostic.
                note = f"{note}; {str(plan.error)[:400]}"
            self._record(
                plan.symbol,
                plan=plan,
                stage="risk",
                decision="skipped",
                reason=decision.reason,
                note=note,
            )
            self._notify(
                "SKIP",
                symbol=plan.symbol,
                side=plan.side,
                reason=journal_reason(decision.reason, plan=plan),
                note=note,
            )

        return decision

    def place_entry(self, plan: Plan, decision: risk.Decision) -> Trade:
        """Place the STOP-LIMIT entry for an approved `plan`; return the Trade.

        Refuses, with a journalled reason, on each of:

        * a decision that is not approved (carry-forward 4 -- there is no way
          to reach here without having been through `decide`);
        * `symbol` already in `open_positions` (review focus #3: a second
          entry on a held symbol doubles the real risk while `risk_per_share`
          still counts one);
        * `plan.status == WAIT_RETEST` (carry-forward 2);
        * `plan.expires_at` already passed (carry-forward 5 -- `risk.evaluate`
          never checks it).

        The first three raise `ValueError` because the caller's state is wrong
        (a bug or a stale snapshot); expiry also raises, after journalling
        `EXPIRY`, because placing a doomed order is the more dangerous of the
        two and must not pass silently.
        """
        if not getattr(decision, "approved", False):
            raise ValueError(
                f"{plan.symbol}: entry refused, risk decision is not approved "
                f"(reason={decision.reason!r})"
            )
        if plan.symbol in self.open_positions:
            raise ValueError(f"{plan.symbol} already held")

        if plan.status == STATUS_WAIT_RETEST:
            self._record(
                plan.symbol,
                plan=plan,
                stage="order",
                decision="skipped",
                reason=STATUS_WAIT_RETEST,
                note="entry already crossed; not chasing at a worse price",
            )
            raise ValueError(
                f"{plan.symbol}: refusing to chase a WAIT_RETEST entry; price has "
                "already crossed the planned entry"
            )

        if is_expired(plan.expires_at):
            self._record(
                plan.symbol,
                plan=plan,
                stage="order",
                decision="skipped",
                reason="EXPIRY",
                note=f"plan horizon {plan.expires_at} already passed",
            )
            raise ValueError(f"{plan.symbol}: plan expired at {plan.expires_at}")

        qty = int(getattr(decision, "quantity", 0) or 0)
        if qty <= 0:
            raise ValueError(f"{plan.symbol}: approved decision carries quantity {qty}")

        trigger = round_tick(float(plan.entry), plan.side == "LONG")
        limit = round_tick(
            entry_limit_price(plan, plan.stop_atr if plan.stop_atr is not None else 0.0),
            plan.side == "LONG",
        )

        trade = Trade(
            symbol=plan.symbol,
            side=plan.side,
            qty=qty,
            entry=trigger,
            stop=plan.stop,
            target1=plan.target1,
            target2=plan.target2,
            risk_per_share=plan.risk_per_share,
            state="entry_pending",
            expires_at=plan.expires_at,
            plan=plan,
        )
        self.open_positions[plan.symbol] = trade

        payload = {
            "symbol": plan.symbol,
            "exchange": config.EXCHANGE,
            "product": config.PRODUCT,
            "price_type": "SL",
            "quantity": qty,
            "trigger_price": trigger,
            "price": limit,
        }

        if config.DRY_RUN:
            trade.entry_order_id = self._next_dry_order_id()
            logger.info("DRY_RUN entry (not sent): %r", payload)
        else:
            trade.entry_order_id = self._order_id(self.api.placesmartorder(**payload))
            if not trade.entry_order_id:
                # No usable order id means we cannot cancel this order later,
                # so it must not be tracked as if we had.
                self.open_positions.pop(plan.symbol, None)
                self._record(
                    plan.symbol,
                    plan=plan,
                    stage="order",
                    decision="skipped",
                    reason="order_rejected",
                    qty=qty,
                    note="entry order placement returned no order id",
                )
                raise ValueError(f"{plan.symbol}: entry order was not placed")

        self._record(
            trade,
            stage="order",
            decision="taken",
            reason=plan.status,
            qty=qty,
            order_id=trade.entry_order_id or "",
            note=(
                f"SL entry trigger={trigger} limit={limit}"
                + (" [DRY_RUN]" if config.DRY_RUN else "")
            ),
        )
        self._notify(
            "ENTRY",
            symbol=trade.symbol,
            side=trade.side,
            qty=qty,
            price=trigger,
            order_id=trade.entry_order_id,
        )
        return trade

    # -- fill and bracket ---------------------------------------------------

    def on_entry_filled(self, trade: Trade, fill_price: float) -> None:
        """Record the entry fill and place the resting SL / T2 bracket.

        The bracket is one `placesmartorder` with `price_type="SL-M"` and the
        `stoploss` / `target` fields, so the **broker** holds both legs for the
        life of the position rather than this process polling and re-sending
        them. That matters most for the stop: a stop that depends on the bot
        process being alive is not a stop.

        Review focus #5: the stop and target are rounded to `TICK_SIZE` for
        the order and then re-validated. Rounding can move a stop onto or past
        the fill (a stop only 0.02 away rounds to the fill price), which the
        broker would reject -- or, worse, accept at a distance that no longer
        matches the risk the position was sized for. If the re-validation
        fails, the bracket is **not** placed and the naked position is
        immediately squared off: an unprotected position is the one outcome
        that must never persist.
        """
        trade.entry_price = float(fill_price)
        trade.state = "entry_filled"
        trade.current_stop = trade.stop

        stop, target = self._bracket_levels(trade, fill_price)
        if stop is None:
            self._reject_bracket(trade, fill_price)
            return

        payload = {
            "symbol": trade.symbol,
            "exchange": config.EXCHANGE,
            "product": config.PRODUCT,
            "price_type": "SL-M",
            "quantity": trade.qty,
            "position_size": trade.qty,
            "stoploss": stop,
            "target": target,
        }

        if config.DRY_RUN:
            trade.stop_order_id = self._next_dry_order_id()
            trade.target_order_id = trade.stop_order_id
            logger.info("DRY_RUN bracket (not sent): %r", payload)
        else:
            response = self.api.placesmartorder(**payload)
            order_id = self._order_id(response)
            # SL-M returns one order id covering both legs; recording it as the
            # stop id is what lets `reconcile` tell a bracketed position from
            # a naked one.
            trade.stop_order_id = order_id or None
            trade.target_order_id = order_id or None

        self._record(
            trade,
            stage="fill",
            decision="filled",
            entry_price=trade.entry_price,
            note=(
                f"bracket stop={stop} target={target}"
                + (" [DRY_RUN]" if config.DRY_RUN else "")
            ),
        )
        self._notify(
            "FILL",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            entry_price=trade.entry_price,
            order_id=trade.stop_order_id,
        )

    def _bracket_levels(self, trade: Trade, fill_price: float) -> tuple[float | None, float]:
        """Tick-rounded (stop, target) for the broker, or (None, target).

        The stop rounds *away* from the fill (down for a LONG, up for a SHORT)
        so rounding can only widen protection, never narrow it. The returned
        stop is the order level; `trade.stop` is deliberately left at the
        planned value, because R is measured against the planned risk.

        Returns a stop of None when the rounded stop is no longer on the
        correct side of the fill -- the review-focus-#5 re-validation.
        """
        if trade.stop is None:
            logger.error("%s: no stop on a filled trade", trade.symbol)
            return None, float(trade.target2 or 0.0)

        is_long = trade.side == "LONG"
        # Stop rounds AWAY from the fill (down for a LONG, up for a SHORT) so
        # rounding can only widen protection; the profit target rounds TOWARD it
        # (up for a LONG, down for a SHORT) so the target is never lowered out
        # of reach. Both directions are the safe one for the position.
        stop = round_tick(float(trade.stop), not is_long)
        target = round_tick(float(trade.target2), is_long)

        if (is_long and stop >= fill_price) or (not is_long and stop <= fill_price):
            logger.error(
                "%s: stop %r rounds to %r, which is not protective against a fill of %r",
                trade.symbol, trade.stop, stop, fill_price,
            )
            return None, target

        if stop != float(trade.stop):
            # Expected whenever the plan's stop is not tick-aligned. Recorded so
            # the gap between the planned risk and the level the broker holds
            # is visible in the journal rather than silently assumed away.
            logger.info(
                "%s: stop %.4f rounded to the tick grid at %.2f (R still measured "
                "against the planned stop)",
                trade.symbol, trade.stop, stop,
            )
        return stop, target

    def _reject_bracket(self, trade: Trade, fill_price: float) -> None:
        """Journal an unusable bracket and flatten the now-naked position."""
        self._record(
            trade,
            stage="order",
            decision="skipped",
            reason="order_rejected",
            entry_price=fill_price,
            note="protective stop not valid against the fill; flattening",
        )
        self._notify(
            "REJECT",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            price=fill_price,
            reason="order_rejected",
            note="no valid protective stop; flattened",
        )
        self.square_off(trade, price=fill_price)

    # -- bar-driven exit resolution --------------------------------------

    def on_bar(self, trade: Trade, high: float, low: float) -> tuple[str, float] | None:
        """Resolve one bar against a live `trade`; return `(reason, price)`.

        This is the ordering that `test_replay.py` pins against
        `simulate_trade`, and each clause exists because of an ambiguity in a
        5-minute bar -- a single bar's high and low cannot be sequenced, so
        the rule has to be chosen rather than discovered:

        * **Stop before T1, before T2, before T1's own retest.** With the stop
          still at its original level, `low <= stop` is checked *first*. A bar
          that dips to the stop and rallies to T1 must book the stop; taking
          T1 there would credit a win to a trade that was dead first, and
          `simulate_trade` books the stop.
        * **T2 wins over T1_TRAIL in the same bar.** Once T1 is touched the
          stop *is* at T1, so a bar whose low also tags T1 looks like a
          T1_TRAIL -- but the backtest checks T2 in the same pass and returns
          it. The favourable case is credited over the adverse one, which is
          also the honest reading: the bar's high came after its low far more
          often than not.
        * **`t1_hit` is sticky.** It is set by `on_t1_touched` and never
          cleared, so the trail persists for the rest of the trade.

        Returns None while the trade is still open. `price` is the *level* to
        exit at, not the bar's extreme: a stop or target is a trigger price,
        and the backtest books the trigger, not wherever the bar happened to
        close.
        """
        if trade.state in ("exited", "cancelled") or trade.entry_price is None:
            return None

        if trade.side == "LONG":
            if not trade.t1_hit:
                if low <= trade.current_stop:
                    return "STOP", trade.current_stop
                if high >= trade.target1:
                    self.on_t1_touched(trade)
                    if high >= trade.target2:
                        return "T2", trade.target2
            else:
                if low <= trade.current_stop:
                    return "T1_TRAIL", trade.current_stop
                if high >= trade.target2:
                    return "T2", trade.target2
        else:
            if not trade.t1_hit:
                if high >= trade.current_stop:
                    return "STOP", trade.current_stop
                if low <= trade.target1:
                    self.on_t1_touched(trade)
                    if low <= trade.target2:
                        return "T2", trade.target2
            else:
                if high >= trade.current_stop:
                    return "T1_TRAIL", trade.current_stop
                if low <= trade.target2:
                    return "T2", trade.target2

        return None

    # -- exits --------------------------------------------------------------

    def on_t1_touched(self, trade: Trade) -> None:
        """Move the working stop to T1 and hold the full size for T2.

        **No partial exit.** `trade.qty` is untouched. A partial take at T1
        would be a different strategy from the one the backtest validated: the
        backtest's `simulate_trade` holds the whole position after T1 and only
        trails the stop, so taking profit at T1 here would make the live R
        systematically diverge from the backtest R in the T1_TRAIL and T2
        buckets -- exactly the divergence `test_replay.py` exists to catch.
        """
        if trade.t1_hit:
            return
        if trade.stop_order_id is None:
            # Filled but unprotected: there is no working stop to move. The
            # entry-fill path places the bracket, so reaching here means the
            # broker never took it.
            logger.error("%s: T1 touched with no working stop order", trade.symbol)
            return

        trade.t1_hit = True
        trade.state = "t1_hit"
        trade.current_stop = trade.target1

        trigger = round_tick(float(trade.target1), trade.side == "LONG")
        payload = {
            "orderid": trade.stop_order_id,
            "symbol": trade.symbol,
            "exchange": config.EXCHANGE,
            "product": config.PRODUCT,
            "price_type": "SL-M",
            "trigger_price": trigger,
            "quantity": trade.qty,
            "position_size": trade.qty,
        }

        if config.DRY_RUN:
            logger.info("DRY_RUN stop move (not sent): %r", payload)
        else:
            self.api.modifyorder(**payload)

        self._record(
            trade,
            stage="order",
            decision="taken",
            note=(
                f"stop moved to T1 {trigger}; holding full qty for T2 {trade.target2}"
                + (" [DRY_RUN]" if config.DRY_RUN else "")
            ),
        )
        self._notify(
            "T1",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            price=trade.target1,
            order_id=trade.stop_order_id,
        )

    def on_exit_filled(self, trade: Trade, price: float, reason: str) -> None:
        """Record a filled exit, journal it, notify, and drop the position.

        R is `(price - entry_price) / abs(entry_price - stop)` for a LONG and
        the mirror for a SHORT -- measured against the **planned** stop and the
        **actual** fill, which is precisely `simulate_trade`'s denominator. It
        is never measured against the trailed stop, because doing so would
        score a T1_TRAIL exit at a multiple of the *remaining* risk rather
        than of the risk the trade was taken with.

        A `reason` outside the vocabulary is normalised by `journal_reason`
        rather than raising, so a broker-side reason string cannot abort the
        exit path with the position already closed.
        """
        exit_price = float(price)
        entry_price = float(trade.entry_price or trade.entry or 0.0)
        risk_per_share = abs(entry_price - float(trade.stop)) if trade.stop is not None else 0.0

        if trade.side == "LONG":
            pnl_per_share = exit_price - entry_price
        else:
            pnl_per_share = entry_price - exit_price

        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.r_multiple = (pnl_per_share / risk_per_share) if risk_per_share > 0 else 0.0
        trade.pnl = pnl_per_share * trade.qty
        trade.state = "exited"

        self.open_positions.pop(trade.symbol, None)

        self._record(
            trade,
            stage="exit",
            decision="exited",
            reason=reason,
            exit_price=exit_price,
        )
        self._notify(
            "EXIT",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            entry_price=entry_price,
            exit_price=exit_price,
            r_multiple=round(trade.r_multiple, 3),
            pnl=round(trade.pnl, 2),
            reason=journal_reason(reason),
        )

    def cancel_stale_entry(self, trade: Trade, *, now: datetime.datetime | None = None) -> None:
        """Cancel a still-working entry whose horizon has passed. Focus #1.

        A no-op unless the trade is actually `entry_pending` and actually past
        `expires_at`. Both guards are needed: an expired plan that has *filled*
        has no entry order left to cancel, and cancelling the bracket instead
        would strip the live position of its stop.

        Journals `EXPIRY` because the cancelled entry is a decision with a
        real outcome -- the symbol was traded-or-not and why.
        """
        if trade.state != "entry_pending" or not is_expired(trade.expires_at, now=now):
            return
        if not trade.entry_order_id:
            logger.warning("%s: entry expired with no order id to cancel", trade.symbol)
            return

        if config.DRY_RUN:
            logger.info("DRY_RUN cancel (not sent): %s", trade.entry_order_id)
        else:
            self.api.cancelorder(trade.entry_order_id, symbol=trade.symbol)

        trade.state = "cancelled"
        self.open_positions.pop(trade.symbol, None)

        self._record(
            trade,
            stage="order",
            decision="skipped",
            reason="EXPIRY",
            order_id=trade.entry_order_id or "",
            note=(
                f"entry cancelled at horizon {trade.expires_at}"
                + (" [DRY_RUN]" if config.DRY_RUN else "")
            ),
        )
        self._notify(
            "CANCEL",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            order_id=trade.entry_order_id,
            reason="EXPIRY",
        )

    def square_off(self, trade: Trade, *, price: float | None = None) -> None:
        """Flatten `trade` at the 15:15 square-off. Journals `SQUAREOFF`.

        Used both by the scheduled 15:15 pass and by the bracket-rejection
        path, where a position exists but has no protective stop and must not
        be left open even for one bar.
        """
        if trade.state == "exited":
            return

        payload = {
            "symbol": trade.symbol,
            "exchange": config.EXCHANGE,
            "product": config.PRODUCT,
            "position_size": trade.qty,
        }

        if config.DRY_RUN:
            logger.info("DRY_RUN square-off (not sent): %r", payload)
        else:
            self.api.closeposition(**payload)

        self.open_positions.pop(trade.symbol, None)
        trade.state = "exited"
        trade.exit_reason = "SQUAREOFF"
        if price is not None:
            self._settle(trade, float(price))

        self._record(
            trade,
            stage="squareoff",
            decision="exited",
            reason="SQUAREOFF",
            note="[DRY_RUN]" if config.DRY_RUN else "",
        )
        self._notify(
            "SQUAREOFF",
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            exit_price=trade.exit_price,
            reason="SQUAREOFF",
        )

    def _settle(self, trade: Trade, exit_price: float) -> None:
        """Compute R and pnl for an exit we already know the price of."""
        entry_price = float(trade.entry_price or trade.entry or 0.0)
        risk_per_share = abs(entry_price - float(trade.stop)) if trade.stop is not None else 0.0
        pnl_per_share = (
            exit_price - entry_price if trade.side == "LONG" else entry_price - exit_price
        )
        trade.exit_price = exit_price
        trade.r_multiple = (pnl_per_share / risk_per_share) if risk_per_share > 0 else 0.0
        trade.pnl = pnl_per_share * trade.qty

    # -- reconciliation -----------------------------------------------------

    #: Fault buckets `reconcile_detail` separates. The repair for each is a
    #: different action, which is why they are not collapsed into one list.
    RECONCILE_FAULTS = ("naked", "unmatched_local", "broker_only", "quantity_mismatch")

    def reconcile_detail(self, open_position_rows: list[dict]) -> dict[str, list[str]]:
        """Reconcile broker positions against local state, bucketed by fault.

        **This reports; it never raises.** Round 1 of the Task 7 review found
        `reconcile` was routing its input through `assert_position_rows`,
        which raises `ValueError` on any row missing `quantity`. That is
        correct on the *entry* path, where a dropped row silently under-reports
        exposure before an order (see the carry-forward-3 contract), and
        wrong here: a detector that aborts on one malformed row cannot report
        the broker-only and local-only faults it exists to find. OpenAlgo's
        `getpositions()` routinely returns zero-quantity intraday rows that the
        caller must pre-filter, so raising would make this function unusable
        on real data -- it would fail on the very rows it should be skipping
        over to find the real problems.

        Malformed rows are therefore *dropped* (with a warning naming the
        symbol) rather than aborting the sweep, and the returned buckets say
        exactly which faults were found.

        The four buckets are genuinely different problems:

        * **`naked`** -- we hold a filled `Trade` but the broker has no
          protective stop order for it (`stop_order_id is None`). The
          crash-between-fill-and-bracket case: live position, nothing
          protecting it. This is the only bucket with real market risk.
        * **`unmatched_local`** -- we hold a `Trade` the broker does not show.
          Almost always a *working, unfilled entry order*, which is normal and
          is not a fault at all. Kept separate from `naked` because
          conflating the two sends an operator to repair a healthy resting
          order. A filled trade missing from the broker is a different matter
          and lands here too; `entry_pending` is distinguished by state.
        * **`broker_only`** -- the broker holds a symbol we have no `Trade` for.
          Either we entered without recording it, or we restarted mid-fill.
          Under our accounting the symbol looks unheld, so it would pass the
          duplicate-entry check -- the review-focus-#3 duplicate-entry risk.
        * **`quantity_mismatch`** -- the symbol is known on both sides but the
          quantities disagree, so our `qty` no longer describes reality.
        """
        broker: dict[str, dict] = {}
        for index, row in enumerate(open_position_rows or []):
            if not isinstance(row, dict):
                logger.warning(
                    "reconcile: ignoring position row %d (%s, not a dict)",
                    index, type(row).__name__,
                )
                continue
            symbol = row.get("symbol")
            quantity = row.get("quantity")
            if symbol is None or quantity is None:
                # Not fatal. Skip it and keep looking for the faults we can
                # actually see; raising here would blind us to those.
                logger.warning(
                    "reconcile: ignoring position row %d with no %s key: %r",
                    index, "symbol" if symbol is None else "quantity", row,
                )
                continue
            broker[str(symbol)] = row

        faults: dict[str, set[str]] = {name: set() for name in self.RECONCILE_FAULTS}

        for symbol, trade in self.open_positions.items():
            row = broker.get(symbol)
            if row is None:
                # A resting entry order with no position is the expected state
                # of `entry_pending`, not a naked position. Only a trade we
                # believe is *filled* and cannot find at the broker is a fault.
                if trade.state == "entry_pending":
                    continue
                faults["unmatched_local"].add(symbol)
                continue

            if trade.entry_price is not None and trade.stop_order_id is None:
                faults["naked"].add(symbol)

            try:
                if int(float(row["quantity"])) != int(trade.qty):
                    faults["quantity_mismatch"].add(symbol)
            except (TypeError, ValueError):
                faults["quantity_mismatch"].add(symbol)

        for symbol in broker:
            if symbol not in self.open_positions:
                faults["broker_only"].add(symbol)

        return {name: sorted(found) for name, found in faults.items()}

    def reconcile(self, open_position_rows: list[dict]) -> list[str]:
        """Symbols needing repair: the sorted union of every fault bucket.

        Kept as the flat union for the Task 7 brief's interface and for callers
        that only need "does anything need attention?". Callers that need to
        know *which* fault -- and therefore what repair is appropriate -- must
        use `reconcile_detail`, because a working resting entry order and a
        genuinely naked position both appear in this list and need opposite
        responses.

        Reports rather than raises; see `reconcile_detail` for why. Detection
        only: this never places an order. Repair is the caller's call, because
        re-placing the bracket vs. flattening is a risk decision, not a
        reconciliation one.
        """
        detail = self.reconcile_detail(open_position_rows)
        return sorted({symbol for names in detail.values() for symbol in names})


__all__ = [
    "DRY_ORDER_PREFIX",
    "PLAN_STATUS_TOKENS",
    "STATUS_WAIT_RETEST",
    "TICK_SIZE",
    "TZ",
    "Executor",
    "Trade",
    "assert_position_rows",
    "entry_limit_price",
    "is_expired",
    "journal_reason",
    "round_tick",
]
