"""Live planner must produce byte-identical plans to the backtest on the same bars.

This is the blocking gate for the whole bot. Every downstream task sizes risk
and places orders from `Plan` values; if the planner's numbers diverge from the
validated backtest even slightly, those numbers are wrong. A weakened
assertion here is worse than no test, so the comparisons below are exact.

`plan_today_from_frame` runs the *backtest's own* `plan_for_day`, so these
tests assert the frame contract (`adapt` reproduces the backtest frame byte for
byte) plus the `Plan` field mapping, not two independent reimplementations of
the strategy maths.
"""

import datetime as dt
import pathlib

import pandas as pd
import planner
import pytest

#: Ground-truth bars: the read-only Historify export used to validate the
#: backtest. Resolved from this file, not the CWD.
#:
#: The task brief wrote `parents[3]`, which from this location
#: (`<nse>/openalgo/strategies/intraday_breakout_bot/test/`) resolves to
#: `<nse>/openalgo/historify_nifty500` -- a path that does not exist. Three
#: parents only reaches the OpenAlgo repo root; the export is a *sibling* of
#: that root, so it needs a fourth hop and the `intraday-strategies` segment.
#: Kept as a module-level constant so a future reader can fix it in one place.
CSVS = (
    pathlib.Path(__file__).resolve().parents[4]
    / "intraday-strategies"
    / "historify_nifty500"
)

#: (symbol, session date) pairs, chosen to cover both sides, several statuses,
#: and sessions where the backtest legitimately produces no plan.
#:
#: The brief's original three cases were almost entirely "no plan" days, which
#: made the gate near-vacuous: 2 of 12 assertions actually compared a plan. The
#: set below was picked by scanning the export for sessions that do produce
#: plans (WATCH_VOLUME / WATCH_BREAKOUT / WATCH_BREAKDOWN) and mixing in two
#: known no-plan sessions so the `None` branch stays covered.
#:
#: Fail loudly rather than silently skipping if the export is missing -- a
#: parity gate that cannot find its fixture must not report success.
CASES = [
    # side, status                      symbol           day
    ("SHORT", "WATCH_VOLUME", "KAYNES", "2026-10-07"),
    ("LONG", "WATCH_VOLUME", "RELIANCE", "2026-10-06"),
    ("LONG", "WATCH_BREAKOUT", "BHARTIHEXA", "2026-10-07"),
    ("SHORT", "WATCH_BREAKDOWN", "FIVESTAR", "2026-09-04"),
    ("SHORT", "WATCH_VOLUME", "WHIRLPOOL", "2026-10-08"),
    (None, None, "TCS", "2026-10-08"),
    (None, None, "RELIANCE", "2026-10-08"),
]

#: Cases whose session the backtest does plan (see `CASES`). The two no-plan
#: sessions are excluded deliberately.
PLAN_CASES = [c for c in CASES if c[0] is not None]

#: (symbol, day) pairs, the shape the row/frame/plan tests below expect.
SYMBOL_DAYS = [(sym, day) for _s, _st, sym, day in CASES]

#: Distinct symbols, for the whole-export frame comparison (date-independent).
SYMBOLS = sorted({sym for _s, _st, sym, _d in CASES})


def test_fixture_csvs_exist():
    """Guard the fixture path itself before any parity claim rests on it."""
    missing = [
        f"{sym}_NSE_5m.csv"
        for _side, _status, sym, _day in CASES
        if not (CSVS / f"{sym}_NSE_5m.csv").is_file()
    ]
    assert not missing, (
        f"parity fixture CSVs missing under {CSVS}: {missing}. "
        "Fix the CSVS path in this file."
    )


def test_parity_cases_are_not_vacuous():
    """Every planned case must still be a plan, on the backtest's own frame.

    Without this, a fixture edit that silently turned every case into a
    "no plan" day would make the whole gate pass while comparing nothing. The
    expected side and status come from the backtest, so this also catches the
    export drifting underneath the fixtures.
    """
    import backtest_source

    for expected_side, expected_status, sym, day in PLAN_CASES:
        result = backtest_source.plan_for_day(
            load_full(sym), dt.date.fromisoformat(day), backtest_source.args()
        )
        assert result is not None, f"{sym} {day}: backtest produced no plan"
        assert result[0] == expected_side, f"{sym} {day}: side changed"
        assert result[1]["status"] == expected_status, f"{sym} {day}: status changed"


def test_no_plan_cases_still_have_no_plan():
    """The two deliberate no-plan sessions must stay no-plan.

    Without them, the `result is None` branch of `test_plan_matches_backtest`
    would never execute and the "bot invented a plan" check would be untested.
    """
    import backtest_source

    for _side, _status, sym, day in [c for c in CASES if c[0] is None]:
        result = backtest_source.plan_for_day(
            load_full(sym), dt.date.fromisoformat(day), backtest_source.args()
        )
        assert result is None, f"{sym} {day}: expected no plan, got {result[0]}"


def load_full(symbol):
    """Whole-symbol frame, built exactly as the brief specifies.

    Equivalent to the backtest's own `load_reliance`, but written out here so
    the test does not depend on the module it is testing.
    """
    df = pd.read_csv(CSVS / f"{symbol}_NSE_5m.csv")
    df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
    df = df.set_index("timestamp").sort_index()
    df.index = df.index.tz_localize("Asia/Kolkata")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_csv(symbol, day):
    """Just the requested session, for the bar-level row comparison."""
    return load_full(symbol)[load_full(symbol).index.date == pd.Timestamp(day).date()]


def to_api_rows(df):
    """Render a frame the way `/api/v1/history` returns bars."""
    return [
        {
            "timestamp": int(ts.timestamp()),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for ts, r in df.iterrows()
    ]


@pytest.mark.parametrize("symbol,day", SYMBOL_DAYS)
def test_csv_rows_equal_api_rows(symbol, day):
    """The API adapter must reproduce the backtest CSV for the same session."""
    csv = load_csv(symbol, day)
    api = planner.adapt(to_api_rows(csv))
    common = api.index.intersection(csv.index)
    assert len(common) == len(csv) > 0
    for col in ("open", "high", "low", "close", "volume"):
        assert (api.loc[common, col] - csv.loc[common, col]).abs().max() == 0.0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_adapt_reproduces_whole_backtest_frame(symbol):
    """`adapt` must equal the backtest's own `load_reliance` over all bars.

    Stronger than the per-session row check: it covers every date in the
    export, so a shaping rule that only happens to work on one day cannot slip
    through.
    """
    import backtest_source

    expected = backtest_source.load().load_reliance(CSVS / f"{symbol}_NSE_5m.csv")
    got = planner.adapt(to_api_rows(expected))

    pd.testing.assert_frame_equal(got, expected)


@pytest.mark.parametrize("symbol,day", SYMBOL_DAYS)
def test_plan_matches_backtest(symbol, day):
    """plan_today must return exactly what the backtest returns for that day."""
    import backtest_source

    # Full multi-day frame so indicator warm-up has data, ending at the test day.
    df = load_full(symbol)

    target = dt.date.fromisoformat(day)
    backtest_result = backtest_source.plan_for_day(df, target, backtest_source.args())

    got = planner.plan_today_from_frame(symbol, df, target)[symbol]

    if backtest_result is None:
        assert got.side is None, f"backtest had no plan but bot produced {got}"
        assert got.error is None
        return

    side, expected, _ts = backtest_result
    assert got.error is None, f"planner errored: {got.error}"
    assert got.side == side
    assert got.entry == pytest.approx(expected["entry"])
    assert got.stop == pytest.approx(expected["stop"])
    assert got.target1 == pytest.approx(expected["target1"])
    assert got.target2 == pytest.approx(expected["target2"])
    assert got.risk_per_share == pytest.approx(expected["risk_per_share"])
    assert got.stop_atr == pytest.approx(expected["stop_atr"])
    assert got.status == expected["status"]
    assert got.expires_at == pd.Timestamp(expected["expires_at"])
    assert got.expires_at.tzinfo is not None
    assert got.is_takeable == (got.status != "WATCH_VOLUME")


@pytest.mark.parametrize("symbol,day", SYMBOL_DAYS)
def test_plan_from_api_shaped_bars_matches_backtest(symbol, day):
    """End-to-end: bars -> `adapt` -> plan must equal the backtest's plan.

    This is the strongest form of the gate. The previous test feeds the
    planner a frame built by the test helper; this one feeds it the frame
    `fetch_history` would actually hand to `plan_for_day` in production.
    """
    import backtest_source

    from_api = planner.adapt(to_api_rows(load_full(symbol)))
    target = dt.date.fromisoformat(day)

    backtest_result = backtest_source.plan_for_day(
        load_full(symbol), target, backtest_source.args()
    )
    got = planner.plan_today_from_frame(symbol, from_api, target)[symbol]

    if backtest_result is None:
        assert got.side is None, f"backtest had no plan but bot produced {got}"
        return

    side, expected, _ts = backtest_result
    assert got.error is None, f"planner errored: {got.error}"
    assert got.side == side
    assert got.entry == pytest.approx(expected["entry"])
    assert got.stop == pytest.approx(expected["stop"])
    assert got.target1 == pytest.approx(expected["target1"])
    assert got.target2 == pytest.approx(expected["target2"])
    assert got.status == expected["status"]
