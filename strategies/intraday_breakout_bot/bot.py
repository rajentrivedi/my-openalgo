"""Daily orchestrator for the intraday breakout bot.

This is the module that wires every other module together into a single
session. It owns *when* each thing happens -- `planner` decides what to
trade, `risk` decides how much, `executor` is the only thing allowed to send
an order -- and nothing more. `Bot` places no order itself; every order flows
through `self.executor`, so the `DRY_RUN` guard that makes a paper session a
rehearsal (and never a live trade) is honoured by construction.

Three properties are load-bearing:

1. **Paper-only is the first thing `main` does.** `config.assert_paper_only()`
   runs before a client is built for trading and before any position is read,
   and the process aborts with a non-zero exit if the connected account is not
   in `analyze` (paper) mode. A live account must never reach the trading loop.

2. **Reconciliation is mandatory before trading.** `reconcile_startup` asks the
   broker what it already holds and compares it against local state (which is
   empty at startup, so the result is the set of broker-only orphans). `main`
   refuses to start the loop if anything is flagged: an unknown pre-existing
   position would otherwise pass the executor's duplicate-entry guard and let
   the day double real risk on a symbol we are not accounting for.

3. **The entry path runs against the session's logical clock, not the wall
   clock.** `executor.place_entry` re-checks `plan.expires_at` against
   `datetime.now()` with no injection seam of its own (deliberately -- see its
   carry-forward 5). To keep `run_once(now)` testable with a simulated clock
   *without* reaching into the executor, `_place_entries` temporarily rebinds
   the module-level `executor.is_expired` so its default `now` is the bot's
   `run_once` clock, and restores it in a `finally`. In production
   (`now_fn=None`) the bot clock *is* the wall clock, so this is a pure
   pass-through; it only matters when a caller drives `run_once` with an
   explicit `now`. This is the single intentional deviation from using the
   executor exactly as-is, and it changes no behaviour on the live path.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import time
from collections.abc import Iterator
from typing import Any

import config
import executor
import notify
import planner
import universe

logger = logging.getLogger(__name__)

#: The three fixed points of a trading day, as `"HH:MM"` IST strings. `wake`
#: marks the session open; `plan` is when the day's plans are generated and
#: entries placed; `squareoff` is the hard flatten-everything deadline.
STAGES: dict[str, str] = {"wake": "09:15", "plan": "10:10", "squareoff": "15:15"}

#: End of the planning window. The plan stage fires once per day somewhere in
#: `[plan, PLAN_WINDOW_END)`; after it, the bot only manages open positions.
#: A window (rather than an instant) means a poll that lands a few minutes late
#: still runs the plan stage instead of skipping the day entirely.
PLAN_WINDOW_END = "11:15"

#: Seconds between `run_once` passes in the live loop. Deliberately coarse: the
#: strategy is 5-minute-bar driven, so sub-minute polling buys nothing and only
#: adds broker-read traffic.
POLL_SECONDS = 30


def _hhmm(value: str) -> datetime.time:
    """Parse a `"HH:MM"` stage string into a `datetime.time` for comparison."""
    hour, minute = value.split(":")
    return datetime.time(int(hour), int(minute))


class _BotExecutor(executor.Executor):
    """`Executor` plus the one seam the orchestrator and its tests need.

    `_blank_trade` builds a minimal pending `Trade` for a symbol -- used to
    seed `open_positions` when reconstructing or testing session state without
    going through the full entry path. It lives here rather than in `executor`
    so Task 9 adds no surface to the order state machine itself.
    """

    def _blank_trade(self, symbol: str) -> executor.Trade:
        """A minimal, entry-pending `Trade` for `symbol`; everything else default."""
        return executor.Trade(symbol=symbol)


class Bot:
    """One trading session's orchestrator.

    `api_client` is the OpenAlgo SDK client (a test double in tests). `now_fn`
    supplies the session clock and defaults to real IST wall-clock time;
    injecting it is what lets a test drive `run_once` deterministically.
    `dry_run`, when not None, sets `config.DRY_RUN` for the session -- `True`
    keeps every order suppressed at the executor's existing gates.
    """

    def __init__(
        self,
        api_client: Any,
        now_fn: Any = None,
        dry_run: bool | None = None,
    ) -> None:
        if dry_run is not None:
            config.DRY_RUN = bool(dry_run)

        self.api = api_client
        self.now_fn = now_fn or (lambda: datetime.datetime.now(tz=config.TZ))
        self.executor = _BotExecutor(api_client)

        #: Realised P&L for the day, tracked in-memory so the daily-loss gate
        #: has a figure to read without re-parsing the journal mid-session.
        self.day_pnl = 0.0
        #: The date the plan stage has already run for, so it fires once a day.
        self._planned_day: datetime.date | None = None
        #: The clock the current plan stage is running against; consumed by the
        #: entry-expiry shim in `_place_entries`.
        self._entry_now: datetime.datetime | None = None

    # -- stage dispatch -----------------------------------------------------

    def run_once(self, now: datetime.datetime) -> None:
        """Execute whichever stage is due at `now`. Pure dispatch, no clock.

        * at/after `squareoff` -> flatten everything;
        * within `[plan, PLAN_WINDOW_END)` and not yet planned today -> run the
          plan stage (generate plans and place entries) exactly once;
        * otherwise -> manage the open positions.

        Taking `now` as an argument (rather than reading a clock) is what makes
        the dispatch testable; the live loop passes `self.now_fn()`.
        """
        clock = now.time()

        if clock >= _hhmm(STAGES["squareoff"]):
            self._square_off_all()
        elif (
            _hhmm(STAGES["plan"]) <= clock < _hhmm(PLAN_WINDOW_END)
            and self._planned_day != now.date()
        ):
            self._planned_day = now.date()
            self._run_plan_stage(now)
        else:
            self._manage_open(now)

    # -- reconciliation -----------------------------------------------------

    def reconcile_startup(self) -> list[str]:
        """Symbols the broker holds that local state does not account for.

        Mandatory before trading. At startup `executor.open_positions` is
        empty, so the executor's reconciliation reduces to "every broker
        position is an orphan"; a non-empty result means the account is not in
        the clean state the day assumes. This only *reports* -- `main` is where
        a non-empty result aborts the start, so the detection path stays
        network-cheap and free of side effects.
        """
        orphans = self.executor.reconcile(self._fetch_positions())
        if orphans:
            logger.warning(
                "startup reconciliation flagged %d symbol(s) needing attention: %s",
                len(orphans),
                orphans,
            )
        return orphans

    # -- plan stage ---------------------------------------------------------

    def _run_plan_stage(self, now: datetime.datetime) -> None:
        """Generate the day's plans and place entries for the takeable ones."""
        self._entry_now = now
        plans = self._plans_for(now.date())
        self._place_entries(plans)

    def _plans_for(self, day: datetime.date) -> dict[str, planner.Plan]:
        """The day's plans for the whole validated universe. Live fetch path.

        Isolated behind its own method so a test can replace it with a canned
        plan set and drive the entry path without any network access.
        """
        return planner.plan_today(universe.load(), day)

    def _place_entries(self, plans: dict) -> None:
        """Gate every takeable plan and place entries for those risk approves.

        Each symbol is handled inside its own try/except so one malformed plan
        or one rejected broker call cannot abort the rest of the universe scan.
        The whole loop runs under `_entry_expiry_clock` so the executor's
        internal expiry check is evaluated against the session clock -- see the
        module docstring, property 3.
        """
        with self._entry_expiry_clock():
            for symbol, plan in plans.items():
                if not getattr(plan, "is_takeable", False):
                    continue
                try:
                    decision = self.executor.decide(
                        plan,
                        day_pnl=self.day_pnl,
                        fetch_positions=self._fetch_positions,
                    )
                    if decision.approved:
                        self.executor.place_entry(plan, decision)
                except Exception as exc:  # noqa: BLE001 - one bad symbol must not abort the scan
                    logger.warning("entry handling failed for %s: %s", symbol, exc)

    @contextlib.contextmanager
    def _entry_expiry_clock(self) -> Iterator[None]:
        """Rebind `executor.is_expired` to default `now` to the session clock.

        `place_entry` calls the module-global `is_expired(plan.expires_at)`
        with no `now`, so it would otherwise test the plan against the wall
        clock even when `run_once` was driven with an explicit `now`. The shim
        preserves the real behaviour for any caller that passes `now`
        explicitly (e.g. `cancel_stale_entry`) and only supplies the default.
        Restored unconditionally in `finally`, including on the no-op path when
        no session clock is set.
        """
        reference = self._entry_now
        if reference is None:
            yield
            return

        original = executor.is_expired

        def _shim(expires_at: Any, *, now: datetime.datetime | None = None) -> bool:
            return original(expires_at, now=now if now is not None else reference)

        executor.is_expired = _shim
        try:
            yield
        finally:
            executor.is_expired = original

    # -- management and square-off -----------------------------------------

    def _manage_open(self, now: datetime.datetime) -> None:
        """Housekeep open positions for `now`.

        The orchestrator owns the one time-driven action here: cancelling a
        still-working entry whose horizon has passed. Fill resolution and
        bar-driven exits are driven by the market-data feed, not by this
        wall-clock pass. Iterates a snapshot because `cancel_stale_entry`
        mutates `open_positions`.
        """
        for trade in list(self.executor.open_positions.values()):
            try:
                self.executor.cancel_stale_entry(trade, now=now)
            except Exception as exc:  # noqa: BLE001 - one bad trade must not abort the pass
                logger.warning("managing %s failed: %s", trade.symbol, exc)

    def _square_off_all(self) -> None:
        """Flatten every open position at the square-off deadline.

        Iterates a snapshot of the values because `square_off` removes each
        trade from `open_positions` as it closes it.
        """
        for trade in list(self.executor.open_positions.values()):
            try:
                self.executor.square_off(trade)
            except Exception as exc:  # noqa: BLE001 - keep flattening the rest
                logger.error("square-off failed for %s: %s", trade.symbol, exc)

    # -- broker reads -------------------------------------------------------

    def _fetch_positions(self) -> list[dict]:
        """Current broker positions as a list of rows, or `[]` on any oddity.

        Passed to `executor.decide` as its `fetch_positions` callable so the
        risk gate reads fresh broker state immediately before each entry. A
        non-success or malformed response yields `[]` rather than raising: an
        unreadable position list must not crash the entry loop, and an empty
        list is the safe reading (the margin gate still fails closed on an
        unknown cash figure downstream).
        """
        response = self.api.openposition()
        if isinstance(response, dict) and response.get("status") == "success":
            data = response.get("data")
            if isinstance(data, list):
                return data
        return []


def main() -> int:
    """Real entry point. Returns a process exit code.

    Order is non-negotiable: confirm paper-only mode, then reconcile, then
    trade. Any failure of the first two aborts before a single order could be
    placed.

    * `0` -- a clean session that ran to the square-off deadline.
    * `1` -- the paper-only guard failed (not confirmed `analyze` mode).
    * `2` -- startup reconciliation found orphan positions; refusing to trade.
    """
    try:
        config.assert_paper_only()
    except Exception as exc:  # noqa: BLE001 - any failure here must abort, loudly
        logger.error("aborting: paper-only check failed: %s", exc)
        return 1

    client = config._sdk_factory(config.API_KEY, config.API_HOST)
    trading_bot = Bot(client)

    orphans = trading_bot.reconcile_startup()
    if orphans:
        logger.error(
            "aborting: startup reconciliation found %d orphan position(s): %s; "
            "refusing to trade against unaccounted broker state",
            len(orphans),
            orphans,
        )
        try:
            notify.send(
                notify.event("RECONCILE", note=f"orphans: {','.join(orphans)}")
            )
        except Exception as exc:  # noqa: BLE001 - a failed alert must not change the exit path
            logger.warning("reconcile notification failed: %s", exc)
        return 2

    squareoff = _hhmm(STAGES["squareoff"])
    while True:
        now = trading_bot.now_fn()
        trading_bot.run_once(now)
        if now.time() >= squareoff:
            break
        time.sleep(POLL_SECONDS)
    return 0


__all__ = ["PLAN_WINDOW_END", "POLL_SECONDS", "STAGES", "Bot", "main"]


if __name__ == "__main__":  # pragma: no cover - process entry point
    # The systemd unit's ExecStart is `python3 -m bot`, which runs this module
    # as `__main__`. Without this guard the module would import and exit 0
    # without ever starting a session. Logging is configured here (not in
    # `main`, which stays side-effect-free for the tests) so operator-facing
    # output lands in journald via stdout/stderr.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
