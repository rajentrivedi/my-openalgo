"""Tests for the poll-based live monitor.

`monitor.poll` is the only place that reads broker state and drives the
executor's transitions, so every test here scripts what the broker "says"
(`orderstatus` / `quotes`) and asserts the state machine advanced correctly.

The autouse fixture mirrors `test_executor.py`: `DRY_RUN` off, the journal
redirected to `tmp_path`, and notifications stubbed. `MonitorApi` records every
write and returns scripted read responses; its write methods mirror the real
SDK signatures so a call the live client would reject fails here too.
"""

import datetime as dt

import config
import executor
import journal
import monitor
import notify
import pytest
import risk
from executor import Executor
from planner import Plan

NOW = dt.datetime(2026, 8, 12, 10, 0, tzinfo=executor.TZ)


def plan(**kw):
    """A minimal approved LONG plan; override with keyword arguments."""
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


def Decision(q):
    return risk.Decision(approved=True, quantity=q, reason=None)


class MonitorApi:
    """A scripted, call-recording broker double.

    Reads (`orderstatus`, `quotes`) return whatever a test registered, verbatim,
    so malformed-response handling can be exercised. Writes mirror the SDK's
    required keyword-only parameters.
    """

    def __init__(self):
        self.orders, self.placed = [], []
        self.modified, self.cancelled, self.closed = [], [], []
        self.responses: dict[str, object] = {}
        self.quote_responses: dict[str, object] = {}
        self.status_calls, self.quote_calls = [], []

    # -- reads --------------------------------------------------------------

    def orderstatus(self, *, order_id, **kw):
        self.status_calls.append(order_id)
        return self.responses.get(order_id, {"status": "error", "message": "unknown"})

    def quotes(self, *, symbol, exchange, **kw):
        self.quote_calls.append(symbol)
        return self.quote_responses.get(symbol, {"status": "error", "message": "unknown"})

    # -- writes (SDK-shaped) ------------------------------------------------

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


@pytest.fixture(autouse=True)
def _live_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(notify, "send", lambda message: True)


def _pending(api, symbol="TCS", qty=50):
    """Place an entry so the executor holds one `entry_pending` trade."""
    ex = Executor(api)
    trade = ex.place_entry(plan(symbol=symbol), Decision(qty))
    return ex, trade


def _complete(api, order_id, price):
    """Script a completed order status for `order_id`."""
    api.responses[order_id] = {
        "status": "success",
        "data": {"order_status": "complete", "average_price": price},
    }


def _ltp(api, symbol, price):
    api.quote_responses[symbol] = {"status": "success", "data": {"ltp": price}}


def _filled(api, symbol="TCS", qty=50, price=3500.0):
    """An executor holding one open, bracketed trade (stop + target placed)."""
    ex, trade = _pending(api, symbol=symbol, qty=qty)
    _complete(api, trade.entry_order_id, price)
    monitor.poll(api, ex, trade, now=NOW)
    assert trade.state == "entry_filled"
    return ex, trade


def _today():
    return dt.datetime.now(tz=executor.TZ).date()


# --- entry lifecycle ----------------------------------------------------


def test_poll_fills_entry_when_orderstatus_complete():
    api = MonitorApi()
    ex, trade = _pending(api)
    _complete(api, "O1", 3500.5)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.state == "entry_filled"
    assert trade.entry_price == 3500.5
    assert len(api.placed) == 2


def test_poll_cancels_stale_pending_entry():
    api = MonitorApi()
    ex, trade = _pending(api)
    trade.expires_at = NOW - dt.timedelta(minutes=1)

    monitor.poll(api, ex, trade, now=NOW)

    assert api.cancelled == ["O1"]
    assert trade.state == "cancelled"
    assert "TCS" not in ex.open_positions


def test_poll_drops_a_rejected_entry():
    api = MonitorApi()
    ex, trade = _pending(api)
    api.responses["O1"] = {"status": "success", "data": {"order_status": "rejected"}}

    monitor.poll(api, ex, trade, now=NOW)

    assert "TCS" not in ex.open_positions
    assert trade.state == "cancelled"
    rows = journal.read_day(_today())
    assert (rows["reason"] == "order_rejected").any()


# --- T1 -----------------------------------------------------------------


def test_poll_trails_stop_when_ltp_touches_t1():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3525.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.t1_hit is True
    assert api.modified[0]["trigger_price"] == 3520.0
    monitor.poll(api, ex, trade, now=NOW)
    assert len(api.modified) == 1


def test_poll_t1_fires_once_at_the_boundary():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3520.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.t1_hit is True
    assert len(api.modified) == 1
    monitor.poll(api, ex, trade, now=NOW)
    assert len(api.modified) == 1


# --- exits --------------------------------------------------------------


def test_poll_books_t2_and_cancels_the_stop_when_target_fills():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3505.0)
    _complete(api, trade.target_order_id, 3543.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.exit_reason == "T2"
    assert trade.stop_order_id in api.cancelled
    assert "TCS" not in ex.open_positions


def test_poll_books_stop_and_cancels_the_target_when_stop_fills():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3505.0)
    _complete(api, trade.stop_order_id, 3480.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.exit_reason == "STOP"
    assert trade.target_order_id in api.cancelled
    assert "TCS" not in ex.open_positions


def test_poll_labels_post_t1_stop_as_t1_trail():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3525.0)
    monitor.poll(api, ex, trade, now=NOW)
    assert trade.t1_hit is True
    _complete(api, trade.stop_order_id, 3520.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert trade.exit_reason == "T1_TRAIL"
    assert "TCS" not in ex.open_positions


def test_poll_books_one_exit_when_both_legs_are_complete():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3505.0)
    _complete(api, trade.stop_order_id, 3480.0)
    _complete(api, trade.target_order_id, 3543.0)

    monitor.poll(api, ex, trade, now=NOW)

    assert "TCS" not in ex.open_positions
    rows = journal.read_day(_today())
    assert (rows["decision"] == "exited").sum() == 1
    assert api.cancelled == [trade.stop_order_id]


# --- guards -------------------------------------------------------------


def test_poll_is_a_noop_under_dry_run(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    api = MonitorApi()
    ex, trade = _pending(api)

    monitor.poll(api, ex, trade, now=NOW)

    assert api.status_calls == []
    assert api.quote_calls == []
    assert api.orders == []
    assert api.placed == []
    assert api.modified == []
    assert api.cancelled == []


def test_poll_ignores_a_malformed_orderstatus():
    for bad in (None, {"status": "error"}, "not-a-dict"):
        api = MonitorApi()
        ex, trade = _pending(api)
        api.responses["O1"] = bad

        monitor.poll(api, ex, trade, now=NOW)

        assert trade.state == "entry_pending"


def test_poll_ignores_a_malformed_quote():
    for bad in (
        {"status": "success", "data": "nope"},
        {"status": "success", "data": {"ltp": "abc"}},
        {"status": "error"},
    ):
        api = MonitorApi()
        ex, trade = _filled(api)
        api.quote_responses["TCS"] = bad

        monitor.poll(api, ex, trade, now=NOW)

        assert trade.t1_hit is False
        assert api.modified == []


def test_poll_books_one_exit_when_both_legs_are_complete_only_once():
    api = MonitorApi()
    ex, trade = _filled(api)
    _ltp(api, "TCS", 3505.0)
    _complete(api, trade.target_order_id, 3543.0)
    _complete(api, trade.stop_order_id, 3480.0)

    monitor.poll(api, ex, trade, now=NOW)
    monitor.poll(api, ex, trade, now=NOW)

    rows = journal.read_day(_today())
    assert (rows["decision"] == "exited").sum() == 1
