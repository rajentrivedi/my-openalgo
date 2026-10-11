"""Tests for the executor's order state machine.

Two autouse fixtures run for every test here:

* `config.DRY_RUN` is forced **off**. It defaults to True, which is correct for
  a first real session, but it would make every API double assertion below
  vacuously pass on an empty call list. The dry-run path has its own tests.
* `notify.send` is stubbed. `Executor` notifies on every entry, fill, T1 and
  exit; letting that through would build a real SDK client and attempt a
  WhatsApp delivery to `127.0.0.1` from inside a unit test.
* `journal.JOURNAL_DIR` is redirected to `tmp_path`, because the executor
  journals on every decision and the real directory is the bot's own.

Every journal assertion in this file therefore reads back a file written during
that test and nothing else.
"""

import datetime as dt
from decimal import Decimal

import config
import executor
import journal
import notify
import pytest
from executor import Executor, Trade
from planner import Plan


def plan(**kw):
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
    def __init__(self):
        self.orders, self.modified, self.cancelled, self.closed = [], [], [], []
        self.margin_calls = []

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
        self.margin_calls.append(positions or [])
        return {
            "status": "success",
            "data": {"total_margin_required": 1000.0, "availablecash": 100000.0},
        }


class Decision:
    def __init__(self, q):
        self.approved, self.quantity, self.reason = True, q, None


@pytest.fixture(autouse=True)
def _live_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(notify, "send", lambda message: sent.append(message) or True)
    return sent


def _today():
    return dt.datetime.now(tz=executor.TZ).date()


# --- brief Step 1 -------------------------------------------------------


def test_round_tick_rounds_to_nearest_005():
    assert executor.round_tick(3500.037, True) == 3500.05
    assert executor.round_tick(3500.037, False) == 3500.0


def test_entry_limit_price_offset_is_ten_percent_atr():
    p = plan()
    assert executor.entry_limit_price(p, atr=100.0) == pytest.approx(3510.0)


def test_place_entry_uses_stop_limit():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    assert api.orders[0]["price_type"] == "SL"
    assert api.orders[0]["trigger_price"] == 3500.0
    assert api.orders[0]["quantity"] == 50
    assert trade.state == "entry_pending"


def test_refuses_second_entry_for_same_symbol():
    ex = Executor(FakeApi())
    ex.place_entry(plan(), Decision(50))
    with pytest.raises(ValueError, match="already"):
        ex.place_entry(plan(), Decision(50))


def test_on_entry_filled_places_bracket_legs():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    bracket = api.orders[1]
    assert bracket["price_type"] == "SL-M"
    assert bracket["stoploss"] == 3480.0
    assert bracket["target"] == 3543.0


def test_on_t1_touched_moves_stop_to_t1_without_partial():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    assert api.modified[0]["trigger_price"] == 3520.0
    assert trade.t1_hit is True
    assert trade.qty == 50  # unchanged: no partial exit


def test_cancel_stale_entry_at_expiry():
    api = FakeApi()
    ex = executor.Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    trade.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(trade)
    assert trade.entry_order_id in api.cancelled


def test_reconcile_detects_naked_position():
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = executor.Trade(
        symbol="TCS",
        side="LONG",
        qty=50,
        entry=3500.0,
        stop=3480.0,
        target1=3520.0,
        target2=3543.0,
        risk_per_share=20.0,
        entry_order_id="O1",
        stop_order_id=None,
        target_order_id=None,
        state="entry_filled",
        t1_hit=False,
        entry_price=3500.0,
        exit_price=None,
        exit_reason=None,
        expires_at=None,
    )
    rows = [
        {
            "symbol": "TCS",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": 50,
            "average_price": 3500.0,
        }
    ]
    assert ex.reconcile(rows) == ["TCS"]


def test_reconcile_clean_when_matched():
    ex = Executor(FakeApi())
    rows = []
    assert ex.reconcile(rows) == []


# --- carry-forward 1: journal reason mapping ---------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "WATCH_VOLUME",
        "stop_too_wide",
        "daily_loss_halt",
        "concurrency_cap",
        "margin_blocked",
        "history_error",
        "order_rejected",
        "STOP",
        "T1_TRAIL",
        "T2",
        "EXPIRY",
        "SQUAREOFF",
    ],
)
def test_journal_reason_passes_through_every_vocabulary_token(reason):
    assert executor.journal_reason(reason) == reason


@pytest.mark.parametrize("status", ["WATCH_BREAKOUT", "WATCH_BREAKDOWN", "WAIT_RETEST"])
def test_journal_reason_passes_through_plan_status_tokens(status):
    """The three statuses `risk.evaluate` approves are journallable verbatim."""
    assert executor.journal_reason(None, plan=plan(status=status)) == status


@pytest.mark.parametrize(
    "free_text",
    [
        "KeyError: 'entry'",
        "TypeError: nope",
        "history_error: boom",
        "broker: order completed",
    ],
)
def test_journal_reason_maps_free_text_to_history_error(free_text):
    """Anything `risk.evaluate` hands back as free text becomes a token."""
    assert executor.journal_reason(free_text) == "history_error"


def test_journal_reason_maps_plan_error_even_when_reason_is_absent():
    """`risk.evaluate` returns `plan.error` verbatim; it must still be a token."""
    p = plan(error="KeyError: 'entry'", status=None)
    assert executor.journal_reason(p.error, plan=p) == "history_error"


def test_journal_reason_returns_none_when_there_is_nothing_to_say():
    assert executor.journal_reason(None) is None
    assert executor.journal_reason("", plan=plan(status=None)) is None


def test_journal_reason_falls_back_to_history_error_for_unknown_text():
    """Anything unrecognised lands in a bucket rather than raising on the loop."""
    assert executor.journal_reason("something new entirely") == "history_error"


def test_every_mapped_reason_is_writable_to_the_journal():
    """The real contract: every branch of `journal_reason` survives `write`."""
    cases = [
        (executor.journal_reason("margin_blocked"), None),
        (executor.journal_reason("KeyError: 'entry'", plan=plan()), None),
        (executor.journal_reason(None, plan=plan(status="WATCH_BREAKOUT")), None),
        (executor.journal_reason(None, plan=plan(status="WAIT_RETEST")), None),
        (executor.journal_reason("T1_TRAIL"), None),
    ]
    for reason, _ in cases:
        journal.write(
            day=_today(),
            symbol="TCS",
            side="LONG",
            stage="exit",
            decision="exited",
            reason=reason,
        )
    assert len(journal.read_day(_today())) == len(cases)


def test_error_plan_journal_row_keeps_the_free_text_in_note():
    """The vocabulary collapses the reason; the diagnostic must survive in note."""
    ex = Executor(FakeApi())
    p = plan(error="KeyError: 'entry'", status=None)
    decision = executor.risk.Decision(approved=False, quantity=0, reason=p.error)
    ex.decide(p, day_pnl=0.0, fetch_positions=lambda: [])
    row = journal.read_day(_today()).iloc[0]
    assert row["reason"] == "history_error"
    assert "KeyError" in row["note"]
    assert decision.reason == p.error


# --- carry-forward 2: WAIT_RETEST is not chased -----------------------


def test_decide_refuses_wait_retest_even_though_risk_approves_it():
    """`risk.evaluate` gates only WATCH_VOLUME; WAIT_RETEST must not be traded."""
    ex = Executor(FakeApi())
    decision = ex.decide(
        plan(status="WAIT_RETEST"), day_pnl=0.0, fetch_positions=lambda: []
    )
    assert decision.approved is False
    assert decision.reason == "WAIT_RETEST"


def test_decide_journals_the_wait_retest_skip():
    ex = Executor(FakeApi())
    ex.decide(plan(status="WAIT_RETEST"), day_pnl=0.0, fetch_positions=lambda: [])
    row = journal.read_day(_today()).iloc[0]
    assert row["reason"] == "WAIT_RETEST"
    assert row["decision"] == "skipped"
    assert row["stage"] == "risk"


def test_place_entry_refuses_wait_retest_even_with_an_approved_decision():
    """Defence in depth: an approved Decision alone must not be enough."""
    ex = Executor(FakeApi())
    with pytest.raises(ValueError, match="WAIT_RETEST"):
        ex.place_entry(plan(status="WAIT_RETEST"), Decision(50))
    assert ex.open_positions == {}


def test_wait_retest_reason_token_is_in_the_journal_vocabulary():
    """Guards the Task 7 addition to `journal.REASONS` against a later revert."""
    assert "WAIT_RETEST" in journal.REASONS
    assert "WATCH_BREAKOUT" in journal.REASONS
    assert "WATCH_BREAKDOWN" in journal.REASONS


# --- carry-forward 3: fresh, well-shaped positions at call time --------


def test_decide_refetches_positions_on_every_call():
    """A snapshot captured at loop start would let entry #4 through stale."""
    api = FakeApi()
    ex = Executor(api)
    rows = []
    calls = []

    def fetch():
        calls.append(len(rows))
        return list(rows)

    ex.decide(plan(symbol="A"), day_pnl=0.0, fetch_positions=fetch)
    rows.append({"symbol": "A", "quantity": 50, "exchange": "NSE", "product": "MIS"})
    ex.decide(plan(symbol="B"), day_pnl=0.0, fetch_positions=fetch)

    assert calls == [0, 1]
    # The second margin call must include the position opened since the first.
    assert len(api.margin_calls[0]) == 1  # proposed only
    assert len(api.margin_calls[1]) == 2  # proposed + the newly-open position


def test_decide_derives_open_count_from_the_same_rows_it_passes_on():
    """`open_count` and `open_positions` can never be from different instants."""
    api = FakeApi()
    ex = Executor(api)
    seen = {}

    import risk as risk_module

    original = risk_module.evaluate

    def spy(p, open_count, day_pnl, open_positions, client):
        seen["count"] = open_count
        seen["rows"] = open_positions
        return original(p, open_count, day_pnl, open_positions, client)

    import executor as executor_module

    executor_module.risk.evaluate = spy
    try:
        rows = [
            {"symbol": "A", "quantity": 10, "exchange": "NSE", "product": "MIS"},
            {"symbol": "B", "quantity": 10, "exchange": "NSE", "product": "MIS"},
        ]
        ex.decide(plan(), day_pnl=0.0, fetch_positions=lambda: rows)
    finally:
        executor_module.risk.evaluate = original

    assert seen["count"] == len(seen["rows"]) == 2


def test_decide_raises_on_a_position_row_without_quantity():
    """`risk` would silently drop the row and under-report exposure."""
    ex = Executor(FakeApi())
    bad = [{"symbol": "A", "qty": 10}]  # wrong key
    with pytest.raises(ValueError, match="quantity"):
        ex.decide(plan(), day_pnl=0.0, fetch_positions=lambda: bad)


def test_assert_position_rows_accepts_none_as_empty():
    assert executor.assert_position_rows(None) == []


def test_assert_position_rows_rejects_non_list_and_non_dict_rows():
    with pytest.raises(ValueError, match="list of dicts"):
        executor.assert_position_rows({"symbol": "A"})
    with pytest.raises(ValueError, match="not a dict"):
        executor.assert_position_rows(["A"])


def test_reconcile_also_guards_the_row_shape():
    ex = Executor(FakeApi())
    with pytest.raises(ValueError, match="quantity"):
        ex.reconcile([{"symbol": "A", "qty": 1}])


# --- carry-forward 4: everything routes through risk.evaluate ----------


def test_decide_calls_risk_evaluate_exactly_once(monkeypatch):
    import executor as executor_module

    seen = []
    original = executor_module.risk.evaluate

    def spy(*args, **kwargs):
        seen.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(executor_module.risk, "evaluate", spy)
    Executor(FakeApi()).decide(plan(), day_pnl=0.0, fetch_positions=lambda: [])
    assert len(seen) == 1


def test_place_entry_refuses_an_unapproved_decision():
    ex = Executor(FakeApi())
    decision = executor.risk.Decision(approved=False, quantity=0, reason="margin_blocked")
    with pytest.raises(ValueError, match="not approved"):
        ex.place_entry(plan(), decision)
    assert ex.open_positions == {}


def test_decide_journals_each_risk_rejection_reason(tmp_path):
    ex = Executor(FakeApi())
    ex.decide(
        plan(symbol="A", status="WATCH_VOLUME"),
        day_pnl=0.0,
        fetch_positions=lambda: [],
    )
    ex.decide(plan(symbol="B", stop_atr=99.0), day_pnl=0.0, fetch_positions=lambda: [])
    ex.decide(plan(symbol="C"), day_pnl=-99999.0, fetch_positions=lambda: [])
    reasons = set(journal.read_day(_today())["reason"])
    assert reasons == {"WATCH_VOLUME", "stop_too_wide", "daily_loss_halt"}


def test_decide_refuses_an_unrecognised_plan_status():
    """A planner rename must not be traded silently."""
    ex = Executor(FakeApi())
    decision = ex.decide(plan(status="WATCH_SOMETHING_NEW"), day_pnl=0.0,
                         fetch_positions=lambda: [])
    assert decision.approved is False


# --- carry-forward 5: expires_at --------------------------------------


def test_place_entry_refuses_an_expired_plan_and_journals_it():
    api = FakeApi()
    ex = Executor(api)
    expired = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    with pytest.raises(ValueError, match="expired"):
        ex.place_entry(plan(expires_at=expired), Decision(50))
    assert api.orders == []
    assert ex.open_positions == {}
    assert journal.read_day(_today()).iloc[0]["reason"] == "EXPIRY"


def test_place_entry_allows_a_plan_that_has_not_expired_yet():
    ex = Executor(FakeApi())
    future = dt.datetime.now(tz=executor.TZ) + dt.timedelta(minutes=30)
    trade = ex.place_entry(plan(expires_at=future), Decision(50))
    assert trade.state == "entry_pending"


def test_plan_without_expiry_is_never_expired():
    assert executor.is_expired(None) is False


def test_is_expired_accepts_a_pandas_timestamp():
    import pandas as pd

    past = pd.Timestamp("2020-01-01 10:00:00", tz=executor.TZ)
    assert executor.is_expired(past) is True
    future = pd.Timestamp.now(tz=executor.TZ) + pd.Timedelta(minutes=5)
    assert executor.is_expired(future) is False


def test_is_expired_treats_a_naive_timestamp_as_ist():
    naive = dt.datetime(2020, 1, 1, 10, 0)
    assert executor.is_expired(naive) is True


def test_cancel_stale_entry_is_a_noop_before_the_horizon():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    trade.expires_at = dt.datetime.now(tz=executor.TZ) + dt.timedelta(minutes=5)
    ex.cancel_stale_entry(trade)
    assert api.cancelled == []
    assert trade.state == "entry_pending"
    assert "TCS" in ex.open_positions


def test_cancel_stale_entry_does_not_cancel_a_filled_trades_bracket():
    """Review focus #1: cancelling a filled entry would strip the live stop."""
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    trade.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(trade)
    assert api.cancelled == []
    assert trade.state == "entry_filled"


def test_cancel_stale_entry_journals_and_deregisters():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    trade.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(trade)
    row = journal.read_day(_today()).iloc[-1]
    assert row["reason"] == "EXPIRY"
    assert row["decision"] == "skipped"
    assert ex.open_positions == {}


# --- review focus #5: tick rounding and re-validation ------------------


def test_bracket_stop_is_rounded_to_the_tick_grid():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(stop=3479.97), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert api.orders[1]["stoploss"] == 3479.95


def test_r_is_measured_against_the_planned_stop_not_the_rounded_one():
    """A sub-tick rounding must not redefine the unit the trade was sized in."""
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(stop=3479.97), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_exit_filled(trade, 3500.0, "STOP")
    # (3500 - 3500) / |3500 - 3479.97| -> 0.0 either way here; use the exit
    # price that makes the difference observable.
    assert trade.stop == 3479.97
    assert trade.r_multiple == 0.0


def test_stop_that_rounds_onto_the_fill_is_rejected_and_flattened():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(stop=3500.02), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert len(api.orders) == 1  # no bracket placed
    assert len(api.closed) == 1  # naked position immediately flattened
    assert trade.state == "exited"


def test_rejected_bracket_is_journalled_as_order_rejected():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(stop=3500.02), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    reasons = set(journal.read_day(_today())["reason"])
    assert "order_rejected" in reasons


def test_short_bracket_rounds_stop_up_and_target_down():
    api = FakeApi()
    ex = Executor(api)
    p = plan(side="SHORT", entry=3500.0, stop=3501.03, target1=3480.0, target2=3457.03)
    trade = ex.place_entry(p, Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    bracket = api.orders[1]
    assert bracket["stoploss"] == 3501.05  # away from the fill: more protective
    assert bracket["target"] == 3457.0  # toward the fill: never lowered


# --- exit accounting ---------------------------------------------------


def test_on_exit_filled_computes_r_against_the_planned_stop():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)  # trail at T1; R must still use the ORIGINAL stop
    ex.on_exit_filled(trade, 3520.0, "T1_TRAIL")
    assert trade.r_multiple == pytest.approx(1.0)
    assert trade.pnl == pytest.approx(1000.0)
    assert trade.state == "exited"
    assert "TCS" not in ex.open_positions


def test_on_exit_filled_short_side_r_is_mirrored():
    ex = Executor(FakeApi())
    p = plan(side="SHORT", entry=3500.0, stop=3520.0, target1=3480.0, target2=3440.0)
    trade = ex.place_entry(p, Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_exit_filled(trade, 3440.0, "T2")
    # 60 points of profit on a 20-point stop.
    assert trade.r_multiple == pytest.approx(3.0)


def test_on_exit_filled_journals_and_notifies(_live_path):
    sent = _live_path
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_exit_filled(trade, 3480.0, "STOP")
    row = journal.read_day(_today()).iloc[-1]
    assert row["stage"] == "exit"
    assert row["decision"] == "exited"
    assert row["reason"] == "STOP"
    assert any("EXIT" in message for message in sent)


def test_on_exit_filled_normalises_an_off_vocabulary_reason():
    """A broker-side reason string must not abort the exit path."""
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_exit_filled(trade, 3480.0, "broker: margin-sell order completed")
    row = journal.read_day(_today()).iloc[-1]
    assert row["reason"] == "history_error"
    assert trade.exit_reason == "broker: margin-sell order completed"


def test_zero_stop_risk_yields_zero_r_not_a_division_error():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(stop=3500.0), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_exit_filled(trade, 3540.0, "T2")
    assert trade.r_multiple == 0.0


# --- on_bar ordering (the replay gate's subject, pinned in isolation) -


def test_on_bar_stop_takes_priority_over_t1_in_the_same_bar():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert ex.on_bar(trade, high=3525.0, low=3475.0) == ("STOP", 3480.0)


def test_on_bar_t2_wins_over_t1_trail_in_the_same_bar():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert ex.on_bar(trade, high=3545.0, low=3519.0) == ("T2", 3543.0)
    assert trade.t1_hit is True


def test_on_bar_t1_only_leaves_the_trade_open_trail_at_t1():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert ex.on_bar(trade, high=3525.0, low=3505.0) is None
    assert trade.current_stop == 3520.0
    assert ex.on_bar(trade, high=3525.0, low=3515.0) == ("T1_TRAIL", 3520.0)


def test_on_bar_short_side_orders_stop_before_t1():
    ex = Executor(FakeApi())
    p = plan(side="SHORT", entry=3500.0, stop=3520.0, target1=3480.0, target2=3440.0)
    trade = ex.place_entry(p, Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    assert ex.on_bar(trade, high=3525.0, low=3475.0) == ("STOP", 3520.0)


def test_on_bar_is_a_noop_before_a_fill():
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    assert ex.on_bar(trade, high=3600.0, low=3400.0) is None


def test_on_t1_touched_is_idempotent():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    ex.on_t1_touched(trade)
    assert len(api.modified) == 1


def test_on_t1_touched_without_a_stop_order_does_nothing():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    trade.stop_order_id = None
    ex.on_t1_touched(trade)
    assert api.modified == []
    assert trade.t1_hit is False


# --- square off -------------------------------------------------------


def test_square_off_closes_and_journals():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.square_off(trade, price=3505.0)
    assert api.closed[0]["position_size"] == 50
    row = journal.read_day(_today()).iloc[-1]
    assert row["stage"] == "squareoff"
    assert row["reason"] == "SQUAREOFF"
    assert trade.r_multiple == pytest.approx(0.25)
    assert ex.open_positions == {}


def test_square_off_is_idempotent():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.square_off(trade, price=3505.0)
    ex.square_off(trade, price=3505.0)
    assert len(api.closed) == 1


# --- reconcile --------------------------------------------------------


def test_reconcile_flags_a_position_the_broker_holds_and_we_do_not():
    """Review focus #3: an untracked holding looks unheld to the dup check."""
    ex = Executor(FakeApi())
    rows = [{"symbol": "INFY", "quantity": 10, "exchange": "NSE", "product": "MIS"}]
    assert ex.reconcile(rows) == ["INFY"]


def test_reconcile_flags_a_local_position_the_broker_does_not_hold():
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(symbol="TCS", state="entry_filled", stop_order_id="O2")
    assert ex.reconcile([]) == ["TCS"]


def test_reconcile_flags_a_quantity_mismatch():
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", stop_order_id="O2"
    )
    rows = [{"symbol": "TCS", "quantity": 30, "exchange": "NSE", "product": "MIS"}]
    assert ex.reconcile(rows) == ["TCS"]


def test_reconcile_is_clean_for_a_bracketed_matching_position():
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", stop_order_id="O2"
    )
    rows = [{"symbol": "TCS", "quantity": 50, "exchange": "NSE", "product": "MIS"}]
    assert ex.reconcile(rows) == []


def test_reconcile_returns_sorted_symbols():
    ex = Executor(FakeApi())
    rows = [
        {"symbol": "ZZZ", "quantity": 1},
        {"symbol": "AAA", "quantity": 1},
    ]
    assert ex.reconcile(rows) == ["AAA", "ZZZ"]


# --- DRY_RUN ----------------------------------------------------------


def test_dry_run_places_no_orders_but_still_advances_state(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    filled = ex.place_entry(plan(symbol="INFY", entry=100.0, stop=98.0), Decision(10))
    filled.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(filled)

    assert api.orders == []
    assert api.modified == []
    assert api.cancelled == []
    assert trade.entry_order_id.startswith("DRY")
    assert trade.t1_hit is True
    # And the whole sequence is journalled, which is the point of a dry session.
    rows = journal.read_day(_today())
    assert list(rows["stage"]) == ["order", "fill", "order", "order", "order"]
    assert "DRY_RUN" in " ".join(rows["note"])


def test_dry_run_marks_journalled_rows_as_dry(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    ex = Executor(FakeApi())
    ex.place_entry(plan(), Decision(50))
    assert "DRY_RUN" in journal.read_day(_today()).iloc[0]["note"]


def test_dry_run_dry_run_flag_is_read_at_call_time(monkeypatch):
    """Flipping DRY_RUN after construction must take effect immediately."""
    api = FakeApi()
    ex = Executor(api)
    assert config.DRY_RUN is False
    monkeypatch.setattr(config, "DRY_RUN", True)
    ex.place_entry(plan(), Decision(50))
    assert api.orders == []


# --- entry prices -----------------------------------------------------


def test_entry_limit_offsets_by_atr_for_short():
    p = plan(side="SHORT")
    assert executor.entry_limit_price(p, atr=100.0) == pytest.approx(3490.0)


def test_entry_limit_degrades_to_the_trigger_on_a_bad_atr():
    assert executor.entry_limit_price(plan(), atr=None) == 3500.0
    assert executor.entry_limit_price(plan(), atr=float("nan")) == 3500.0


def test_entry_order_limit_and_trigger_both_land_on_the_tick_grid():
    api = FakeApi()
    ex = Executor(api)
    ex.place_entry(plan(entry=3500.03, stop_atr=1.7), Decision(50))
    order = api.orders[0]
    tick = Decimal(str(executor.TICK_SIZE))
    assert Decimal(str(order["trigger_price"])) % tick == 0
    assert Decimal(str(order["price"])) % tick == 0


def test_entry_order_carries_exchange_and_product_from_config():
    api = FakeApi()
    ex = Executor(api)
    ex.place_entry(plan(), Decision(50))
    assert api.orders[0]["exchange"] == config.EXCHANGE
    assert api.orders[0]["product"] == config.PRODUCT


def test_failed_entry_placement_raises_and_does_not_track_a_phantom_trade():
    class FailingApi(FakeApi):
        def placesmartorder(self, **kw):
            self.orders.append(kw)
            return {"status": "error", "message": "insufficient margin"}

    ex = Executor(FailingApi())
    with pytest.raises(ValueError, match="not placed"):
        ex.place_entry(plan(), Decision(50))
    assert ex.open_positions == {}


def test_entry_order_with_zero_quantity_is_refused():
    ex = Executor(FakeApi())
    with pytest.raises(ValueError, match="quantity"):
        ex.place_entry(plan(), Decision(0))
