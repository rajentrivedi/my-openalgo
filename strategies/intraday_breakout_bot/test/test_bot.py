"""Tests for the daily orchestrator (`bot.py`).

The orchestrator places no orders itself: it drives `executor`, which is the
only thing that talks to the broker. These tests therefore assert on the
*effects* the executor produces (orders on the fake client, entries in
`open_positions`) when `Bot` is driven at each point of the trading day.

Autouse fixtures mirror `test_executor.py`:

* `config.DRY_RUN` is forced **off**, so the executor actually calls the fake
  client and the API-call assertions below are not vacuously true. The
  dry-run propagation path has its own dedicated test that re-enables it.
* `notify.send` is stubbed, because every entry / square-off notifies and a
  real send would build an SDK client and hit `127.0.0.1`.
* `journal.JOURNAL_DIR` is redirected to `tmp_path`, because the executor
  journals every decision and the real directory is the bot's own.

The session clock is always supplied explicitly to `run_once(now)`; `now_fn`
is only exercised where `main`'s loop needs it. The simulated session date
(2026-10-08) is deliberately a few days before "today", so a plan that is
unexpired *at the session clock* but expired at the wall clock proves the
entry path is evaluated against the injected clock, not `datetime.now()`.
"""

import datetime as dt

import bot
import config
import journal
import notify
import planner
import pytest
import universe
from planner import Plan

# A fixed in-session instant and the matching "same day, later" expiry. The
# date is in the past relative to the real wall clock on purpose (see module
# docstring): any logic that reached for `datetime.now()` instead of the
# session clock would treat these plans as expired and skip them.
SESSION_DAY = dt.date(2026, 10, 8)


def at(hhmm):
    """The session clock for a `"HH:MM"` IST time on the test's session date."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    return dt.datetime(
        SESSION_DAY.year, SESSION_DAY.month, SESSION_DAY.day,
        hour, minute, tzinfo=config.TZ,
    )


# Derived from the configured schedule, not hardcoded: if `STAGES["plan"]` moves
# forward, `PLAN_NOW` must move with it or `test_run_once_at_plan_time_...`
# silently degrades into a before-plan no-op that passes for the wrong reason.
PLAN_NOW = at(bot.STAGES["plan"])
BEFORE_PLAN = at("09:30")
AFTER_PLAN = at(bot.PLAN_WINDOW_END) - dt.timedelta(minutes=5)
SQUAREOFF_NOW = at(bot.STAGES["squareoff"])


def plan(**kw):
    """A takeable LONG plan risk approves at size 50 (1000 budget / 20 risk)."""
    base = {
        "symbol": "TCS",
        "side": "LONG",
        "entry": 3500.0,
        "stop": 3480.0,
        "target1": 3520.0,
        "target2": 3543.0,
        "risk_per_share": 20.0,
        "stop_atr": 1.0,
        "expires_at": None,
        "status": "WATCH_BREAKOUT",
        "error": None,
    }
    base.update(kw)
    return Plan(**base)


class FakeApi:
    """A broker double that records writes and answers the reads the bot makes.

    `positions` seeds what `positionbook()` reports, so reconciliation and the
    risk gate's position read can be driven per-test. `margin()` approves by
    default (requirement well under cash); individual tests do not need to
    touch it.
    """

    def __init__(self, positions=None):
        self._positions = positions or []
        self.orders, self.modified, self.cancelled, self.closed = [], [], [], []

    def positionbook(self):
        return {"status": "success", "data": list(self._positions)}

    def placesmartorder(self, **kw):
        self.orders.append(kw)
        return {"status": "success", "data": {"orderid": f"O{len(self.orders)}"}}

    def modifyorder(self, **kw):
        self.modified.append(kw)
        return {"status": "success"}

    def cancelorder(self, order_id, **kw):
        self.cancelled.append(order_id)

    def closeposition(self, **kw):
        self.closed.append(kw)

    def margin(self, positions=None, pricetype=None):
        return {
            "status": "success",
            "data": {"total_margin_required": 1000.0, "availablecash": 100000.0},
        }

    def funds(self):
        return {"status": "success", "mode": "analyze", "data": {"availablecash": 100000.0}}


@pytest.fixture(autouse=True)
def _live_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(notify, "send", lambda message: True)


def _plan_stage(monkeypatch, plans):
    """Wire the plan-generation seams to return `plans`; return a call counter.

    Patches the real seams (`universe.load`, `planner.plan_today`) rather than
    the Bot method, so the test exercises `_plans_for` as written. The counter
    lets a test assert the plan stage fired exactly once.
    """
    calls = {"n": 0}

    def _plan_today(symbols, day):
        calls["n"] += 1
        assert day == SESSION_DAY
        return plans

    monkeypatch.setattr(universe, "load", lambda: ["TCS"])
    monkeypatch.setattr(planner, "plan_today", _plan_today)
    return calls


# --- stage dispatch --------------------------------------------------------


def test_run_once_before_plan_time_does_nothing(monkeypatch):
    calls = _plan_stage(monkeypatch, {"TCS": plan()})
    api = FakeApi()
    b = bot.Bot(api)

    b.run_once(BEFORE_PLAN)

    assert calls["n"] == 0  # plan stage not reached
    assert api.orders == []
    assert b.executor.open_positions == {}


def test_run_once_at_plan_time_places_entries(monkeypatch):
    _plan_stage(monkeypatch, {"TCS": plan()})
    api = FakeApi()
    b = bot.Bot(api)

    b.run_once(PLAN_NOW)

    assert "TCS" in b.executor.open_positions
    assert api.orders and api.orders[0]["price_type"] == "SL"
    assert api.orders[0]["quantity"] == 50


def test_run_once_plan_stage_fires_once_per_day(monkeypatch):
    calls = _plan_stage(monkeypatch, {"TCS": plan()})
    b = bot.Bot(FakeApi())

    b.run_once(PLAN_NOW)
    b.run_once(AFTER_PLAN)

    assert calls["n"] == 1


def test_plan_stage_is_scheduled_after_the_trigger_bar_closes():
    """The plan stage must not run before the strategy's trigger bar exists.

    `plan_for_day` pins its plan to the 10:10 five-minute bar and advances `now`
    by one bar, so the trigger bar is only complete at 10:15. Running the plan
    stage at 10:10 makes the planner see a bar that does not exist yet and
    return no plans for the whole universe; `_planned_day` then latches and the
    day is skipped with nothing journaled. The trigger time is read from the
    backtest source rather than restated here, so this gate tracks the strategy
    and not a local literal that can drift away from it.
    """
    import pathlib
    import re

    import backtest_source
    import planner

    source = pathlib.Path(backtest_source._resolve_path()).read_text()
    match = re.search(r'f"\{day\}\s+(\d{1,2}):(\d{2})"', source)
    assert match is not None, (
        "could not find the 10:10 trigger expression in the backtest source; "
        "the backtest has been refactored and this test needs updating"
    )
    hour, minute = (int(part) for part in match.groups())
    trigger_close = dt.datetime(2026, 10, 8, hour, minute, tzinfo=config.TZ)
    trigger_close += dt.timedelta(minutes=planner.BAR_MINUTES)

    assert at(bot.STAGES["plan"]) >= trigger_close, (
        f"plan stage at {bot.STAGES['plan']} runs before the trigger bar closes "
        f"({trigger_close:%H:%M}); the planner would see a missing bar and "
        f"skip the entire day"
    )


def test_run_once_skips_non_takeable_plans(monkeypatch):
    # An error plan is not takeable; nothing should be ordered for it.
    _plan_stage(monkeypatch, {"TCS": plan(error="KeyError: 'entry'", status=None)})
    api = FakeApi()
    b = bot.Bot(api)

    b.run_once(PLAN_NOW)

    assert api.orders == []
    assert b.executor.open_positions == {}


def test_entry_expiry_is_judged_against_the_session_clock(monkeypatch):
    # Expiry is later than the session clock but earlier than the real wall
    # clock. The entry must be placed: the horizon has NOT passed in-session.
    expires = dt.datetime(2026, 10, 8, 11, 10, tzinfo=config.TZ)
    _plan_stage(monkeypatch, {"TCS": plan(expires_at=expires)})
    api = FakeApi()
    b = bot.Bot(api)

    b.run_once(PLAN_NOW)

    assert "TCS" in b.executor.open_positions


def test_entry_refused_when_expired_at_session_clock(monkeypatch):
    expires = dt.datetime(2026, 10, 8, 9, 10, tzinfo=config.TZ)  # before PLAN_NOW
    _plan_stage(monkeypatch, {"TCS": plan(expires_at=expires)})
    api = FakeApi()
    b = bot.Bot(api)

    b.run_once(PLAN_NOW)

    assert "TCS" not in b.executor.open_positions
    assert api.orders == []


def test_run_once_at_squareoff_flattens_open_positions():
    api = FakeApi()
    b = bot.Bot(api)
    b.executor.open_positions["TCS"] = bot.executor.Trade(
        symbol="TCS", side="LONG", qty=50, entry=3500.0, stop=3480.0,
        entry_price=3500.0, stop_order_id="O1", state="entry_filled",
    )

    b.run_once(SQUAREOFF_NOW)

    assert api.closed and api.closed[0]["symbol"] == "TCS"
    assert b.executor.open_positions == {}


# --- startup reconciliation ------------------------------------------------


def test_reconcile_startup_flags_broker_only_orphans():
    api = FakeApi(positions=[{"symbol": "INFY", "quantity": 10}])
    b = bot.Bot(api)

    assert b.reconcile_startup() == ["INFY"]


def test_reconcile_startup_clean_when_broker_flat():
    assert bot.Bot(FakeApi(positions=[])).reconcile_startup() == []


# --- construction knobs ----------------------------------------------------


def test_dry_run_argument_propagates_to_config():
    bot.Bot(FakeApi(), dry_run=True)
    assert config.DRY_RUN is True


# --- main entry point ------------------------------------------------------


def test_main_aborts_when_account_not_paper(monkeypatch):
    class LiveClient:
        def funds(self):
            return {"status": "success", "mode": "live", "data": {}}

    monkeypatch.setattr(config, "_sdk_factory", lambda key, host: LiveClient())

    assert bot.main() == 1  # paper-only guard


def test_main_aborts_on_startup_orphans(monkeypatch):
    monkeypatch.setattr(
        config, "_sdk_factory",
        lambda key, host: FakeApi(positions=[{"symbol": "INFY", "quantity": 5}]),
    )

    assert bot.main() == 2  # reconciliation guard, never enters the loop
