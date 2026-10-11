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

#: The session mask, restated here *independently* of `planner` so the two can
#: be compared. These must match the backtest's `load_reliance` too -- see
#: `test_parity_mask_constants_match_backtest_source`.
IST = "Asia/Kolkata"
SESSION_START = 9 * 60 + 15   # 09:15, inclusive
SESSION_END = 15 * 60 + 30    # 15:30, exclusive
GRID = 5                      # minutes, anchored at 09:15

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


def load_raw_csv(symbol):
    """Bars straight from the CSV, with **no session mask applied**.

    This is the only honest input to a mask comparison. Feeding `adapt` the
    output of `load_reliance` (already masked) cannot possibly detect a mask
    regression -- the rows that a broken mask would wrongly keep have already
    been removed upstream, so `adapt` sees nothing to over-include.
    """
    df = pd.read_csv(CSVS / f"{symbol}_NSE_5m.csv")
    df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
    df = df.set_index("timestamp").sort_index()
    df.index = df.index.tz_localize(IST)
    # `oi` is dropped here so the frame carries exactly the OHLCV the backtest
    # keeps; everything else is left untouched for the mask to judge.
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_full(symbol):
    """The masked frame, built without going through `planner.adapt`.

    Equivalent to the backtest's own `load_reliance`, but written out here so
    the tests do not depend on the module they are testing when building the
    *expected* side.
    """
    df = load_raw_csv(symbol)
    minutes = df.index.hour * 60 + df.index.minute
    mask = (
        (minutes >= SESSION_START)
        & (minutes < SESSION_END)
        & ((minutes - SESSION_START) % GRID == 0)
    )
    return df[mask]


def load_csv(symbol, day):
    """Just the requested session, for the bar-level row comparison."""
    df = load_full(symbol)
    return df[df.index.date == pd.Timestamp(day).date()]


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
    """The API adapter must reproduce the backtest CSV for the same session.

    Compares the full frames with `assert_frame_equal`, not an
    `index.intersection`: an intersection silently tolerates *extra* rows on
    either side, which is precisely the shape a mask regression takes (a broken
    mask keeps rows it should have dropped).
    """
    csv = load_csv(symbol, day)
    api = planner.adapt(to_api_rows(load_raw_csv(symbol)))
    api_session = api[api.index.date == pd.Timestamp(day).date()]

    pd.testing.assert_frame_equal(api_session, csv)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_adapt_reproduces_whole_backtest_frame(symbol):
    """`adapt` must equal the backtest's `load_reliance` over *all* bars.

    `adapt` is fed the **unmasked** CSV rows. The previous version of this test
    fed it `load_reliance` output, which is already masked -- so any bar the
    mask was supposed to drop had been removed before `adapt` saw it, and a
    regression in the mask was undetectable by construction.

    Comparing against the backtest's own `load_reliance` (not a local copy)
    means the expected side cannot drift with our own constants.
    """
    import backtest_source

    expected = backtest_source.load().load_reliance(CSVS / f"{symbol}_NSE_5m.csv")
    got = planner.adapt(to_api_rows(load_raw_csv(symbol)))

    # Length first, with an explicit message: assert_frame_equal reports a
    # shape mismatch abstractly, and "how many extra rows" is the whole point.
    assert len(got) == len(expected), (
        f"{symbol}: adapt kept {len(got)} bars, backtest kept {len(expected)} "
        f"(difference {len(got) - len(expected)})"
    )
    pd.testing.assert_frame_equal(got, expected)


def test_masked_bars_exist_so_the_mask_is_actually_exercised():
    """Fail loudly if the fixture data stops containing droppable rows.

    The exports currently carry only 12 out-of-session bars per symbol, all at
    18:00-18:55, and *zero* off-grid bars and *zero* bars at 15:30. The mask's
    trailing edge and its grid alignment are therefore untouched by real data,
    which is why the synthetic boundary tests in `test_planner.py` carry the
    entire load for those two edges.

    This test only guards the edge the parity data *can* cover: that droppable
    rows exist at all. If a future export had none, the parity mask comparison
    would be checking nothing and this would catch it.
    """
    raw = load_raw_csv("KAYNES")
    minutes = raw.index.hour * 60 + raw.index.minute
    dropped = ((minutes < SESSION_START) | (minutes >= SESSION_END)).sum()

    assert dropped > 0, "fixture has no out-of-session bars; mask is untested by parity"


def test_parity_mask_constants_match_backtest_source():
    """Our mask constants must equal the backtest's, read from its source.

    `load_reliance` inlines the mask as bare literals (555, 930, 5) with no
    name to import, so `planner` necessarily restates them. This test pulls
    those literals straight out of the backtest's source text and asserts our
    three constants agree -- so an upstream change fails the gate instead of
    silently desynchronising the two implementations.
    """
    import re

    import backtest_source

    source = pathlib.Path(backtest_source.DEFAULT_PATH).read_text()
    start = source.index("def load_reliance")
    body = source[start:source.index("\ndef ", start + 1)]

    # The mask is one expression: (minutes >= A) & (minutes < B) & ((minutes - C) % D == 0)
    match = re.search(
        r"\(minutes\s*>=\s*(\d+)\)\s*&\s*\(minutes\s*<\s*(\d+)\)"
        r"\s*&\s*\(\(minutes\s*-\s*(\d+)\)\s*%\s*(\d+)\s*==\s*0\)",
        body,
    )
    assert match is not None, (
        "could not find the session-mask expression in load_reliance; the "
        "backtest source has been refactored and this test needs updating"
    )
    lower, upper, grid_start, grid_step = (int(g) for g in match.groups())

    assert (planner.SESSION_START_MINUTE, planner.SESSION_END_MINUTE, planner.BAR_MINUTES) == (
        lower,
        upper,
        grid_step,
    ), (
        f"planner mask ({planner.SESSION_START_MINUTE}, {planner.SESSION_END_MINUTE}, "
        f"{planner.BAR_MINUTES}) disagrees with backtest ({lower}, {upper}, {grid_step})"
    )
    assert grid_start == lower, "backtest grid is not anchored at the session open"


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
