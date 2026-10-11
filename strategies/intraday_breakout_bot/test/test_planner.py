import datetime as dt

import pandas as pd
import planner
import pytest

IST = "Asia/Kolkata"


def epoch(hhmm, date="2026-09-09"):
    """Epoch seconds for an IST wall-clock time.

    Timestamps are derived from a readable wall-clock time rather than written
    as literals. The brief's originals (`1788924900` / `1788925200`) are
    actually 09:05 and 09:10 IST -- *outside* the session -- so the mask
    deleted every fixture row and both of its tests asserted properties of an
    empty frame, passing for the wrong reason. Deriving from `hhmm` makes that
    class of error impossible to write.
    """
    stamp = pd.Timestamp(f"{date} {hhmm}", tz=IST)
    return int(stamp.timestamp())


def bar(hhmm, **kwargs):
    """One API-shaped bar dict at the given IST wall-clock time."""
    close = float(kwargs.pop("close", 100.0))
    payload = {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}
    payload.update(kwargs)
    return {"timestamp": epoch(hhmm), **payload}


def surviving_times(raw):
    """The HH:MM of each bar that survives the mask."""
    df = planner.adapt(raw)
    return [f"{t.hour:02d}:{t.minute:02d}" for t in df.index]


def test_adapt_produces_ist_indexed_frame():
    """Shape of the frame `adapt` returns -- on rows that actually survive.

    The rows here are 09:15/09:20/09:25 IST, inside the session and on the
    5-minute grid, so the frame is non-empty and every assertion below is a
    statement about real output rather than about an empty frame's defaults.
    """
    raw = [bar("09:15", close=100.0), bar("09:20", close=101.5), bar("09:25", close=102.25)]

    df = planner.adapt(raw)

    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == IST
    assert df.index.is_monotonic_increasing
    assert len(df) == 3, "fixture rows were silently masked out"
    assert df["close"].tolist() == [100.0, 101.5, 102.25]


def test_adapt_drops_non_session_and_offgrid_bars():
    """In-session bars survive; out-of-session and off-grid bars are dropped.

    Asserts on the *surviving* timestamps (and their count) instead of a
    `.all()` over the result, which would be vacuously true on an empty frame.
    """
    raw = [
        bar("09:15"),              # in: first session bar
        bar("09:20"),              # in
        bar("09:10"),              # out: before the session opens
        bar("10:12"),              # out: not on the 5-minute grid
        bar("15:30"),              # out: session end is exclusive
        bar("18:55"),              # out: well after the close
    ]

    assert surviving_times(raw) == ["09:15", "09:20"]
    assert len(planner.adapt(raw)) == 2


@pytest.mark.parametrize(
    "hhmm,expected_kept",
    [
        ("09:15", True),   # session open is inclusive
        ("09:14", False),  # one minute before the open
        ("09:10", False),  # ten minutes before the open
        ("10:10", True),   # mid-session, on the grid
        ("15:25", True),   # last bar inside the exclusive end
        ("15:30", False),  # session end is exclusive
        ("15:35", False),  # one bar past the end
        ("10:12", False),  # off-grid inside the session
        ("09:17", False),  # off-grid at the very start
        ("15:27", False),  # off-grid at the very end
        ("15:29", False),  # inside the session but off the grid
    ],
)
def test_session_mask_boundaries(hhmm, expected_kept):
    """Pin each mask boundary on its own.

    The CSVs contain no bar at 15:30 and not one off-grid bar, so the parity
    tests cannot exercise these edges at all -- a mutation to either bound
    would go unnoticed. These synthetic rows are the only coverage they get.
    """
    kept = surviving_times([bar(hhmm)]) == [hhmm]
    assert kept is expected_kept, f"{hhmm}: expected kept={expected_kept}"


def test_adapt_keeps_only_grid_aligned_minutes():
    """Every survivor sits on the 09:15-anchored 5-minute grid."""
    raw = [bar(f"{h:02d}:{m:02d}") for h in (9, 10, 14) for m in range(0, 60, 7)]

    minutes = planner.adapt(raw).index.hour * 60 + planner.adapt(raw).index.minute

    assert len(minutes) > 0
    assert ((minutes - planner.SESSION_START_MINUTE) % planner.BAR_MINUTES == 0).all()


def test_adapt_empty_input_returns_empty_ist_frame():
    df = planner.adapt([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == IST


def test_adapt_index_dtype_matches_backtest_load_reliance():
    """The index dtype must be identical to the backtest's, not merely equal.

    `backtest_source.IST` is the string `'Asia/Kolkata'`; pandas resolves it
    internally. If this ever reverts to a `zoneinfo.ZoneInfo` (as `config.TZ`),
    the instants still match but the dtype object does not, and
    `assert_frame_equal` in the parity test rejects it.
    """
    import backtest_source

    tz = planner._backtest_tz()
    assert tz == backtest_source.IST
    assert isinstance(tz, str)
    assert planner.adapt([bar("09:15")]).index.dtype == pd.DatetimeIndex(
        [pd.Timestamp("2026-09-09 09:15", tz=tz)]
    ).dtype


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
    """Replace planner's requests.post, capturing the request it sends."""
    calls = {}

    def fake_post(url, json, timeout, headers=None):
        calls["url"] = url
        calls["payload"] = json
        calls["timeout"] = timeout
        calls["headers"] = headers
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(planner.requests, "post", fake_post)
    return calls


def test_fetch_history_posts_to_history_endpoint_with_apikey_in_body(monkeypatch):
    """The apikey travels in the JSON body -- the endpoint does not read a header."""
    import config

    ok = {"status": "success", "data": [bar("09:15")]}
    calls = _stub_post(monkeypatch, _FakeResponse(ok))

    planner.fetch_history("KAYNES", days=30)

    assert calls["url"].startswith(config.API_HOST.rstrip("/"))
    assert calls["url"].endswith("/api/v1/history")
    assert calls["payload"]["apikey"] == config.API_KEY
    assert calls["payload"]["symbol"] == "KAYNES"
    assert calls["payload"]["interval"] == "5m"


def test_fetch_history_does_not_send_apikey_as_a_header(monkeypatch):
    """The real invariant the tautological line was meant to state.

    `/api/v1/history` reads the apikey out of the JSON body. Sending it as an
    HTTP header as well (or instead) would be the actual defect -- so assert
    the key is in the body and that no `headers=` argument carries it.
    """
    import config

    calls = _stub_post(monkeypatch, _FakeResponse({"status": "success", "data": [bar("09:15")]}))
    planner.fetch_history("KAYNES")

    assert "apikey" in calls["payload"]
    headers = calls["headers"]
    if headers is not None:
        assert not any("apikey" in str(k).lower() for k in headers)
        assert config.API_KEY not in [str(v) for v in headers.values()]


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


def test_fetch_history_does_not_mislabel_a_bug_as_an_api_error(monkeypatch):
    """A programming error inside `fetch_history` must stay loud.

    `except Exception` around the request would relabel a `TypeError` from a
    changed signature as `history_error: ...`, silently degrading a symbol that
    should have failed the process.
    """
    def exploding_post(*a, **k):
        raise TypeError("post() got an unexpected keyword argument 'json'")

    monkeypatch.setattr(planner.requests, "post", exploding_post)

    with pytest.raises(TypeError):
        planner.fetch_history("KAYNES")


def test_fetch_history_missing_column_raises(monkeypatch):
    incomplete = bar("09:15")
    incomplete.pop("volume")
    ok = {"status": "success", "data": [incomplete]}
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


# --- CRITICAL: a plan-dict key rename must not abort the universe scan -------
#
# The `Plan(...)` field mapping reads keys the backtest owns. If a rename
# upstream turns `plan["stop"]` into a KeyError *outside* the error handling,
# the exception escapes `plan_today_from_frame` and kills the entire scan on
# whichever symbol hits it first. These tests reproduce that mutation directly.

_REAL_PLAN = {
    "status": "WATCH_BREAKOUT",
    "entry": 100.0,
    "stop": 95.0,
    "target1": 105.0,
    "target2": 111.0,
    "risk_per_share": 5.0,
    "stop_atr": 1.4,
    "expires_at": "2026-10-08T11:10:00+05:30",
}


@pytest.mark.parametrize("broken_key", sorted(_REAL_PLAN))
def test_plan_today_from_frame_survives_a_missing_plan_key(monkeypatch, broken_key):
    """Dropping any single plan key must degrade the symbol, not raise."""
    broken = {k: v for k, v in _REAL_PLAN.items() if k != broken_key}
    monkeypatch.setattr(
        planner.backtest_source, "plan_for_day", lambda *a, **k: ("LONG", broken, None)
    )

    got = planner.plan_today_from_frame("X", _empty_ist_frame(), dt.date(2026, 10, 8))["X"]

    assert got.error is not None, f"missing {broken_key!r} did not surface"
    assert got.error.startswith("KeyError"), got.error
    assert broken_key in got.error
    assert got.is_takeable is False
    assert got.side is None, "a half-built Plan must not look like a real setup"


def test_plan_today_from_frame_maps_a_complete_plan_without_error(monkeypatch):
    """The all-keys-present counterpart, so the test above cannot pass vacuously."""
    monkeypatch.setattr(
        planner.backtest_source, "plan_for_day", lambda *a, **k: ("LONG", dict(_REAL_PLAN), None)
    )

    got = planner.plan_today_from_frame("X", _empty_ist_frame(), dt.date(2026, 10, 8))["X"]

    assert got.error is None
    assert got.side == "LONG"
    assert got.entry == 100.0
    assert got.stop == 95.0
    assert got.status == "WATCH_BREAKOUT"
    assert got.is_takeable is True


def test_plan_today_scan_survives_a_broken_symbol_in_first_position(monkeypatch):
    """The reviewer's mutation, end to end.

    A `KeyError` raised while mapping the very first symbol must not prevent
    the rest of the universe from being planned.
    """
    monkeypatch.setattr(planner, "fetch_history", lambda symbol, days=60: _empty_ist_frame())

    calls = {"n": 0}

    def flaky(_df, _day, _args):
        calls["n"] += 1
        plan = dict(_REAL_PLAN)
        if calls["n"] == 1:
            del plan["stop_atr"]
        return ("LONG", plan, None)

    monkeypatch.setattr(planner.backtest_source, "plan_for_day", flaky)

    plans = planner.plan_today(["AAA", "BBB", "CCC"], dt.date(2026, 10, 8))

    assert set(plans) == {"AAA", "BBB", "CCC"}, "scan was aborted"
    assert calls["n"] == 3, "later symbols were never planned"
    assert plans["AAA"].error is not None, "first symbol should have been errored"
    assert "KeyError" in plans["AAA"].error
    assert plans["BBB"].error is None
    assert plans["BBB"].side == "LONG"
    assert plans["CCC"].error is None
    assert plans["CCC"].side == "LONG"


def test_plan_today_does_not_swallow_programming_errors_from_fetch(monkeypatch):
    """A `TypeError` from `fetch_history` is a bug and must stay loud.

    `plan_today` catches only `RuntimeError`/`requests.RequestException`, so a
    changed signature surfaces instead of being relabelled `history_error`.
    """
    def broken_fetch(symbol, days=60):
        raise TypeError("fetch_history() got an unexpected keyword argument 'days'")

    monkeypatch.setattr(planner, "fetch_history", broken_fetch)

    with pytest.raises(TypeError):
        planner.plan_today(["AAA"], dt.date(2026, 10, 8))


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
