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
import inspect
from decimal import Decimal

import config
import executor
import journal
import notify
import pytest
import risk
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
    """In-memory client double whose signatures mirror the real SDK.

    `**kw` on every method would let a call the real SDK would reject pass
    here, which is exactly how the executor's `orderid`/positional-`order_id`
    bugs survived 88 tests. These are keyword-only with the SDK's required
    parameters, so a call that would `TypeError` live also `TypeError`s here.
    """

    def __init__(self):
        self.orders, self.placed = [], []
        self.modified, self.cancelled, self.closed = [], [], []
        self.margin_calls = []

    def placeorder(
        self, *, strategy="Python", symbol, action, exchange,
        price_type="MARKET", product="MIS", quantity=1, **kw,
    ):
        self.placed.append(
            dict(symbol=symbol, action=action, exchange=exchange,
                 price_type=price_type, product=product,
                 quantity=quantity, **kw)
        )
        return {"status": "success", "data": {"orderid": f"P{len(self.placed)}"}}

    def placesmartorder(
        self, *, strategy="Python", symbol, action, exchange,
        price_type="MARKET", product="MIS", quantity=1, position_size, **kw,
    ):
        self.orders.append(
            dict(symbol=symbol, action=action, exchange=exchange,
                 price_type=price_type, product=product,
                 quantity=quantity, position_size=position_size, **kw)
        )
        return {"status": "success", "data": {"orderid": f"O{len(self.orders)}"}}

    def modifyorder(
        self, *, order_id, strategy="Python", symbol, action, exchange,
        price_type="LIMIT", product, quantity, price,
        disclosed_quantity="0", trigger_price="0", **kw,
    ):
        self.modified.append(
            {
                "order_id": order_id, "symbol": symbol, "action": action,
                "exchange": exchange, "price_type": price_type,
                "product": product, "quantity": quantity, "price": price,
                "disclosed_quantity": disclosed_quantity,
                "trigger_price": trigger_price,
            }
        )
        return {"status": "success"}

    def cancelorder(self, *, order_id, strategy="Python", **kw):
        self.cancelled.append(order_id)
        return {"status": "success"}

    def closeposition(self, *, strategy="Python", **kw):
        self.closed.append(kw)
        return {"status": "success"}

    def margin(self, positions=None, pricetype=None):
        self.margin_calls.append(positions or [])
        return {
            "status": "success",
            "data": {"total_margin_required": 1000.0, "availablecash": 100000.0},
        }


def Decision(q):
    """An APPROVED decision, built from the real `risk.Decision`.

    Round 1 of the review flagged that this used to be a local stand-in class
    with three attributes set in `__init__`. A drift in the real dataclass --
    a renamed field, a new required argument, `approved` ceasing to be the
    first positional -- would not have failed any test here, because nothing
    exercised the real type on the entry path. Building it from `risk.Decision`
    means the brief's entry tests cannot pass against a stub.
    """
    return risk.Decision(approved=True, quantity=q, reason=None)


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
    o = api.orders[0]
    assert o["price_type"] == "SL"
    assert o["trigger_price"] == 3500.0
    assert o["quantity"] == 50
    assert o["action"] == "BUY" and o["position_size"] == 50
    assert trade.state == "entry_pending"


def test_refuses_second_entry_for_same_symbol():
    ex = Executor(FakeApi())
    ex.place_entry(plan(), Decision(50))
    with pytest.raises(ValueError, match="already"):
        ex.place_entry(plan(), Decision(50))


def test_on_entry_filled_places_stop_and_target_legs():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    stop, target = api.placed[0], api.placed[1]
    assert stop["price_type"] == "SL-M" and stop["trigger_price"] == 3480.0
    assert stop["action"] == "SELL" and stop["quantity"] == 50
    assert target["price_type"] == "LIMIT" and target["price"] == 3543.0
    assert target["action"] == "SELL"
    assert trade.stop_order_id and trade.target_order_id
    assert trade.stop_order_id != trade.target_order_id


def test_on_t1_touched_moves_stop_to_t1_without_partial():
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    m = api.modified[0]
    assert m["order_id"] == trade.stop_order_id
    assert m["action"] == "SELL" and m["trigger_price"] == 3520.0
    assert trade.t1_hit is True
    assert trade.qty == 50


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


def test_reconcile_reports_rather_than_raising_on_a_malformed_row():
    """Round 1, Important 2: reconcile must REPORT, not abort.

    A detector that raises on one bad row cannot report the faults it exists to
    find, and OpenAlgo's `getpositions()` routinely returns zero-quantity
    intraday rows. This test replaces the old `pytest.raises` version; the
    entry path's strictness is pinned separately by
    `test_entry_path_still_raises_on_a_malformed_position_row`.
    """
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", stop_order_id=None, entry_price=3500.0
    )
    rows = [
        {"symbol": "A", "qty": 1},  # wrong key entirely
        "not-a-dict",
        {"symbol": "TCS", "quantity": 50},  # the real fault, still findable
    ]
    # No raise, and the naked TCS position is still detected past the junk.
    assert ex.reconcile(rows) == ["TCS"]


def test_reconcile_detail_names_the_fault_bucket():
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", stop_order_id=None, entry_price=3500.0
    )
    rows = [
        {"symbol": "TCS", "quantity": 50},
        {"symbol": "INFY", "quantity": 10},
        {"symbol": "JUNK", "qty": 5},
    ]
    detail = ex.reconcile_detail(rows)
    assert detail["naked"] == ["TCS"]
    assert detail["broker_only"] == ["INFY"]
    assert detail["unmatched_local"] == []
    assert detail["quantity_mismatch"] == []


def test_reconcile_detail_handles_a_whole_broker_failure():
    """An unusable broker response must yield every bucket empty, not raise."""
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", stop_order_id="O2", entry_price=3500.0
    )
    # A filled trade the broker cannot see is an `unmatched_local` fault, and a
    # None broker response must produce that verdict rather than an exception.
    assert ex.reconcile(None) == ["TCS"]
    assert ex.reconcile_detail(None) == {
        "naked": [],
        "unmatched_local": ["TCS"],
        "broker_only": [],
        "quantity_mismatch": [],
    }
    assert ex.reconcile_detail([{"symbol": "TCS", "quantity": 50}]) == {
        name: [] for name in Executor.RECONCILE_FAULTS
    }


def test_reconcile_detail_reports_a_missing_symbol_key():
    ex = Executor(FakeApi())
    assert ex.reconcile_detail([{"quantity": 5}]) == {
        name: [] for name in Executor.RECONCILE_FAULTS
    }


def test_entry_path_still_raises_on_a_malformed_position_row():
    """Carry-forward 3 is untouched: the ENTRY path stays strict.

    Important 2 relaxed `reconcile` only. Before an order, a dropped row would
    silently under-report exposure in the margin gate, so `decide` must still
    refuse rather than absorb.
    """
    ex = Executor(FakeApi())
    with pytest.raises(ValueError, match="quantity"):
        ex.decide(plan(), day_pnl=0.0, fetch_positions=lambda: [{"symbol": "A", "qty": 1}])


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
    assert api.placed[0]["trigger_price"] == 3479.95


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
    assert len(api.orders) == 1  # entry placed, no bracket legs
    assert len(api.placed) == 1  # naked position flattened with a counter order
    assert api.placed[0]["price_type"] == "MARKET"
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
    stop, target = api.placed[0], api.placed[1]
    assert stop["trigger_price"] == 3501.05  # away from the fill: more protective
    assert target["price"] == 3457.0  # toward the fill: never lowered


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
    assert api.placed[-1]["price_type"] == "MARKET"
    assert api.placed[-1]["action"] == "SELL" and api.placed[-1]["quantity"] == 50
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
    placed_after_first = len(api.placed)
    ex.square_off(trade, price=3505.0)
    assert len(api.placed) == placed_after_first


def test_square_off_all_flattens_every_position_with_one_global_close():
    api = FakeApi()
    ex = Executor(api)
    a = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(a, 3500.0)
    b = ex.place_entry(plan(symbol="INFY", entry=100.0, stop=98.0), Decision(10))
    ex.on_entry_filled(b, 100.0)

    ex.square_off_all()

    assert len(api.closed) == 1 and api.closed[0] == {}
    assert api.placed[-2:] and all(p["price_type"] != "MARKET" for p in api.placed[-2:])
    assert ex.open_positions == {}
    assert a.state == "exited" and b.state == "exited"


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


# --- round 1, Important 3: a resting entry order is NOT a naked position ---


def test_working_entry_order_with_no_position_is_not_a_naked_position():
    """A resting, unfilled entry order is the EXPECTED state, not a fault.

    Round 1 flagged `reconcile` conflating this with a naked position. Sending
    an operator to "repair" a healthy resting order -- or worse, flattening it
    -- because it has no broker position yet is exactly the wrong response.
    """
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_pending", entry_order_id="O1", stop_order_id=None
    )
    assert ex.reconcile([]) == []
    assert ex.reconcile_detail([])["naked"] == []
    assert ex.reconcile_detail([])["unmatched_local"] == []


def test_a_filled_trade_missing_from_the_broker_is_unmatched_local():
    """The same shape with state=entry_filled IS a fault -- just a different one."""
    ex = Executor(FakeApi())
    ex.open_positions["TCS"] = Trade(
        symbol="TCS", qty=50, state="entry_filled", entry_price=3500.0, stop_order_id="O2"
    )
    detail = ex.reconcile_detail([])
    assert detail["unmatched_local"] == ["TCS"]
    assert detail["naked"] == []


def test_naked_and_resting_entries_are_separated_in_one_sweep():
    ex = Executor(FakeApi())
    ex.open_positions["RESTING"] = Trade(
        symbol="RESTING", qty=10, state="entry_pending", entry_order_id="O1"
    )
    ex.open_positions["NAKED"] = Trade(
        symbol="NAKED", qty=10, state="entry_filled", entry_price=100.0, stop_order_id=None
    )
    rows = [
        {"symbol": "RESTING", "quantity": 10},
        {"symbol": "NAKED", "quantity": 10},
    ]
    detail = ex.reconcile_detail(rows)
    assert detail["naked"] == ["NAKED"]
    assert ex.reconcile(rows) == ["NAKED"]


def test_reconcile_detail_exposes_all_four_buckets():
    assert tuple(Executor.RECONCILE_FAULTS) == (
        "naked",
        "unmatched_local",
        "broker_only",
        "quantity_mismatch",
    )


# --- round 1, Important 1: the square-off dry-run guard ---


def test_square_off_places_no_close_order_under_dry_run(monkeypatch):
    """Round 1 proved the 15:15 real-close path had NO dry-run guard test.

    Deleting the `if config.DRY_RUN:` guard from `square_off` used to leave all
    88 tests green, because every other dry-run assertion covered only
    `orders`/`modified`/`cancelled`. `api.closed` and `api.placed` were never
    checked and `square_off` was never called in dry mode. This is that test.
    """
    monkeypatch.setattr(config, "DRY_RUN", True)
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)

    ex.square_off(trade, price=3505.0)

    assert api.placed == [], "square_off sent a counter order during DRY_RUN"
    assert api.closed == [], "square_off called closeposition during DRY_RUN"
    assert api.orders == []
    assert api.modified == []


def test_square_off_still_settles_and_journals_under_dry_run(monkeypatch):
    """Suppressing the call must not suppress the bookkeeping."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    ex = Executor(FakeApi())
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.square_off(trade, price=3505.0)
    row = journal.read_day(_today()).iloc[-1]
    assert row["stage"] == "squareoff"
    assert row["reason"] == "SQUAREOFF"
    assert trade.state == "exited"
    assert trade.r_multiple == pytest.approx(0.25)
    assert ex.open_positions == {}


def test_square_off_really_closes_when_not_dry(monkeypatch):
    """The paired assertion, so the dry-run test cannot pass by a dead guard."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.square_off(trade, price=3505.0)
    flat = [p for p in api.placed if p["price_type"] == "MARKET"]
    assert len(flat) == 1
    assert flat[0]["symbol"] == "TCS"
    assert flat[0]["action"] == "SELL"


def test_dry_run_never_reaches_a_write_api_across_the_whole_module(monkeypatch):
    """One sweep over every state transition, checking every write call."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    api = FakeApi()
    ex = Executor(api)

    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    ex.on_exit_filled(trade, 3520.0, "T1_TRAIL")

    pending = ex.place_entry(plan(symbol="INFY", entry=100.0, stop=98.0), Decision(10))
    pending.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(pending)

    late = ex.place_entry(plan(symbol="SBIN", entry=900.0, stop=898.0), Decision(10))
    ex.on_entry_filled(late, 900.0)
    ex.square_off(late, price=901.0)

    ex.place_entry(plan(symbol="HDFC", entry=1500.0, stop=1495.0), Decision(10))
    ex.square_off_all()
    ex.cancel_resting("DRY-x")

    assert api.orders == []
    assert api.placed == []
    assert api.modified == []
    assert api.cancelled == []
    assert api.closed == []


# --- round 1, Important 4: decide()'s read APIs and what it journals ---


def test_decide_journals_the_positions_the_gate_read():
    """Round 1: the dry-mode reads were undocumented and unjournalled.

    A margin rejection with no record of the positions behind it cannot be
    reproduced after the fact. `note` must carry what the gate actually saw.
    """
    ex = Executor(FakeApi())
    rows = [
        {"symbol": "A", "quantity": 10, "exchange": "NSE", "product": "MIS"},
        {"symbol": "B", "quantity": 20, "exchange": "NSE", "product": "MIS"},
    ]
    ex.decide(plan(symbol="TCS", status="WATCH_VOLUME"), day_pnl=0.0,
              fetch_positions=lambda: rows)
    note = journal.read_day(_today()).iloc[0]["note"]
    assert "2 open position(s)" in note
    assert "Ax10" in note and "Bx20" in note


def test_decide_journals_an_empty_read_when_nothing_is_open():
    ex = Executor(FakeApi())
    ex.decide(plan(symbol="TCS", status="WATCH_VOLUME"), day_pnl=0.0,
              fetch_positions=lambda: [])
    assert "no open positions" in journal.read_day(_today()).iloc[0]["note"]


def test_decide_read_apis_are_permitted_in_dry_run(monkeypatch):
    """Round 1, Important 4: the reads are a DOCUMENTED decision, not an oversight.

    `decide` cannot avoid reading positions and margin. None of those calls can
    place, modify, cancel or close, and the margin gate is the logic most worth
    rehearsing -- stubbing it would make a dry session systematically more
    permissive than the real thing. What is asserted here is that the read
    still happens, and that no *write* API is reached.
    """
    monkeypatch.setattr(config, "DRY_RUN", True)
    api = FakeApi()
    ex = Executor(api)

    seen = []

    def fetch():
        seen.append("positions")
        return []

    decision = ex.decide(plan(), day_pnl=0.0, fetch_positions=fetch)

    assert seen == ["positions"], "positions were not read under DRY_RUN"
    assert api.margin_calls, "the margin gate was skipped under DRY_RUN"
    assert decision.approved is True
    # ...and still no write.
    assert api.orders == [] and api.closed == [] and api.modified == []


# --- round 2: every order call is checked against the installed SDK ----


def _binds(method, payload):
    inspect.signature(method).bind(None, **payload)


def _installed_openalgo_api():
    """The installed `openalgo.api` class, even when the platform repo shadows it.

    Running pytest from inside the OpenAlgo platform checkout puts the repo root
    -- which contains its own `openalgo/` package directory -- ahead of
    site-packages on `sys.path`, so a plain `from openalgo import api` resolves
    to the repo and raises ImportError. The SDK directory is placed first for
    the import, then the path and the module cache are restored.
    """
    import site
    import sys

    parents = [*site.getsitepackages(), site.getusersitepackages()]
    sdk_parent = next(
        (p for p in parents if __import__("os").path.isfile(
            __import__("os").path.join(p, "openalgo", "__init__.py")
        )),
        None,
    )
    if sdk_parent is None:
        pytest.skip("openalgo SDK is not installed")
    cached = sys.modules.pop("openalgo", None)
    sys.path.insert(0, sdk_parent)
    try:
        from openalgo import api as openalgo_api
    finally:
        sys.path.remove(sdk_parent)
        if cached is not None:
            sys.modules["openalgo"] = cached
    return openalgo_api


def test_executor_order_payloads_bind_against_the_real_sdk():
    """A payload the real SDK would reject must fail here, not live.

    `FakeApi` mirrors the SDK by hand, so a future SDK rename would be absorbed
    by its own `**kw` unless we bind the recorded payloads against the
    installed client directly. This is what turns the executor's historical
    `orderid` / positional-`order_id` bugs into a test failure.
    """
    openalgo_api = _installed_openalgo_api()

    api = FakeApi()
    ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    ex.on_exit_filled(trade, 3520.0, "T1_TRAIL")

    pending = ex.place_entry(plan(symbol="INFY", entry=100.0, stop=98.0), Decision(10))
    pending.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(pending)

    live = ex.place_entry(plan(symbol="SBIN", entry=900.0, stop=898.0), Decision(10))
    ex.on_entry_filled(live, 900.0)
    ex.square_off(live, price=901.0)

    for kw in api.orders:
        _binds(openalgo_api.placesmartorder, kw)
    for kw in api.placed:
        _binds(openalgo_api.placeorder, kw)
    for kw in api.modified:
        _binds(openalgo_api.modifyorder, kw)
    for oid in api.cancelled:
        _binds(openalgo_api.cancelorder, {"order_id": oid})
    _binds(openalgo_api.closeposition, {})
    assert api.orders and api.placed and api.modified and api.cancelled

    # The bracket legs carry ONLY supported keys - no stoploss/target.
    for leg in api.placed:
        assert set(leg) <= {"symbol", "action", "exchange", "price_type",
                            "product", "quantity", "price", "trigger_price"}


def test_cancel_resting_cancels_through_the_executor(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    api = FakeApi()
    ex = Executor(api)
    ex.cancel_resting("O7")
    assert api.cancelled == ["O7"]


def test_cancel_resting_is_a_noop_for_a_missing_id():
    api = FakeApi()
    ex = Executor(api)
    ex.cancel_resting(None)
    assert api.cancelled == []
