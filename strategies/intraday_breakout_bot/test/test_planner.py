import datetime as dt

import pandas as pd
import planner
import pytest


def test_adapt_produces_ist_indexed_frame():
    raw = [
        {"timestamp": 1788924900, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 10.0},
        {"timestamp": 1788925200, "open": 100.5, "high": 102.0,
         "low": 100.0, "close": 101.5, "volume": 20.0},
    ]
    df = planner.adapt(raw)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "Asia/Kolkata"
    assert df.index.is_monotonic_increasing


def test_adapt_drops_non_session_and_offgrid_bars():
    raw = [
        {"timestamp": 1788924900, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"timestamp": 1788924900 + 60 * 12 * 3600, "open": 2, "high": 2, "low": 2,
         "close": 2, "volume": 2},   # 12:00 UTC = 17:30 IST, outside session
    ]
    df = planner.adapt(raw)
    minutes = df.index.hour * 60 + df.index.minute
    assert ((minutes >= 555) & (minutes < 930)).all()
    assert ((minutes - 555) % 5 == 0).all()


# --- fetch_history error handling ------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, raises=None):
        self._payload = payload
        self._raises = raises
        self.raised = False

    def raise_for_status(self):
        if self._raises is not None:
            raise self._raises

    def json(self):
        return self._payload


def _stub_post(monkeypatch, response):
    """Replace planner's requests.post, capturing the payload it sends."""
    calls = {}

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["payload"] = json
        calls["timeout"] = timeout
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(planner.requests, "post", fake_post)
    return calls


def test_fetch_history_posts_to_history_endpoint_with_apikey_in_body(monkeypatch):
    """The apikey travels in the JSON body -- the endpoint does not read a header."""
    import config

    ok = {"status": "success", "data": [
        {"timestamp": 1788924900, "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": 10.0},
    ]}
    calls = _stub_post(monkeypatch, _FakeResponse(ok))

    planner.fetch_history("KAYNES", days=30)

    assert calls["url"].startswith(config.API_HOST.rstrip("/"))
    assert calls["url"].endswith("/api/v1/history")
    assert calls["payload"]["apikey"] == config.API_KEY
    assert calls["payload"]["symbol"] == "KAYNES"
    assert calls["payload"]["interval"] == "5m"
    assert "apikey" not in calls["payload"].keys() - {"apikey"}


def test_fetch_history_date_range_is_days_back(monkeypatch):
    ok = {"status": "success", "data": []}
    calls = _stub_post(monkeypatch, _FakeResponse(ok))

    planner.fetch_history("TCS", days=45, end_date=dt.date(2026, 10, 9))

    assert calls["payload"]["end_date"] == "2026-10-09"
    assert calls["payload"]["start_date"] == "2026-08-25"


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(RuntimeError("connection refused"), id="transport-error"),
        pytest.param(_FakeResponse({"status": "error", "message": "bad symbol"}), id="api-error"),
        pytest.param(_FakeResponse(None), id="null-body"),
        pytest.param(_FakeResponse({"status": "success"}), id="missing-data-key"),
        pytest.param(_FakeResponse({"status": "success", "data": "nope"}), id="data-not-a-list"),
    ],
)
def test_fetch_history_raises_runtime_error(monkeypatch, response):
    _stub_post(monkeypatch, response)
    with pytest.raises(RuntimeError):
        planner.fetch_history("KAYNES")


def test_fetch_history_missing_column_raises(monkeypatch):
    ok = {"status": "success", "data": [
        {"timestamp": 1788924900, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
    ]}
    _stub_post(monkeypatch, _FakeResponse(ok))
    with pytest.raises(RuntimeError, match="volume"):
        planner.fetch_history("KAYNES")


# --- plan_today per-symbol isolation ---------------------------------------
#
# One bad symbol must not abort a whole-universe scan.


def _empty_ist_frame():
    """An empty but well-formed plan frame (the backtest indexes by date)."""
    import config

    return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], tz=config.TZ),
    )


def test_plan_today_records_error_and_continues(monkeypatch):
    """A failed fetch yields an errored Plan, not an exception."""
    def fake_fetch(symbol, days=60):
        if symbol == "BAD":
            raise RuntimeError("history request failed for BAD: boom")
        return _empty_ist_frame()

    monkeypatch.setattr(planner, "fetch_history", fake_fetch)

    plans = planner.plan_today(["GOOD", "BAD", "ALSO_GOOD"], dt.date(2026, 10, 8))

    assert set(plans) == {"GOOD", "BAD", "ALSO_GOOD"}
    assert plans["BAD"].error is not None
    assert "history_error" in plans["BAD"].error
    assert plans["BAD"].is_takeable is False
    assert plans["GOOD"].error is None


def test_plan_today_records_backtest_error_rather_than_raising(monkeypatch):
    """A raising backtest is surfaced on the Plan, not propagated."""
    monkeypatch.setattr(planner, "fetch_history", lambda symbol, days=60: _empty_ist_frame())
    monkeypatch.setattr(
        planner.backtest_source, "plan_for_day",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad frame")),
    )

    plans = planner.plan_today(["GOOD"], dt.date(2026, 10, 8))

    assert plans["GOOD"].error is not None
    assert "ValueError" in plans["GOOD"].error
    assert plans["GOOD"].is_takeable is False


# --- Plan.is_takeable -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"side": "LONG", "status": "WATCH_BREAKOUT"}, True),
        ({"side": "LONG", "status": "WAIT_RETEST"}, True),
        ({"side": "SHORT", "status": "WATCH_BREAKDOWN"}, True),
        ({"side": "LONG", "status": "WATCH_VOLUME"}, False),
        ({"side": None, "status": None}, False),
        ({"side": "LONG", "status": "WATCH_BREAKOUT", "error": "boom"}, False),
    ],
)
def test_is_takeable(kwargs, expected):
    assert planner.Plan(symbol="X", **kwargs).is_takeable is expected
