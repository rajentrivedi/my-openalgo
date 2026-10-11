"""Executor state machine must reproduce `simulate_trade` exit reasons and R.

This is the gate that proves the *live* T1 logic matches the *backtest* T1
logic. `simulate_trade` is the validated reference; `replay.replay` drives the
real `Executor` over the same stored bars. If a stop/target/T1 ordering rule
in `Executor.on_bar` drifts from the backtest's loop, these fail.

The assertions are deliberately exact on `reason` and tight on
`r_multiple` (`abs=1e-3`, the backtest's own rounding). Neither was
weakened to make a case pass -- where the executor genuinely disagreed with
the backtest, the executor was changed.

**Case selection.** The brief's three literal cases were kept, but they are
thin: TCS 2026-10-08 produces no plan at all, HDFCBANK 2026-10-07 plans but
never enters, and INFY 2026-10-01 exits on EXPIRY. Three near-identical
outcomes would let a broken STOP or T2 path pass unnoticed, so four further
cases were added, one per exit reason the backtest can produce
(`STOP`, `T1_TRAIL`, `T2`, `EXPIRY`) and one per side. Every one was found by
scanning the stored history for the first day yielding each reason.

The CSV directory is READ-ONLY ground truth, resolved from this file so the
test does not depend on the working directory.
"""

import datetime as dt
import pathlib

import backtest_source
import config
import journal
import notify
import pandas as pd
import pytest
import replay

#: `test/test_replay.py` -> parents[0]=test, [1]=package, [2]=strategies,
#: [3]=openalgo, [4]=<root>. `historify_nifty500` is a sibling of the openalgo
#: checkout, under <root>/intraday-strategies/. Asserted to exist below.
CSVS = pathlib.Path(__file__).resolve().parents[4] / "intraday-strategies" / "historify_nifty500"

#: (symbol, day, expected reason). The first three are the brief's literal
#: cases; the rest cover every reachable exit reason.
CASES = [
    ("TCS", "2026-10-08", None),  # no plan at all
    ("HDFCBANK", "2026-10-07", None),  # plan, but the entry never triggers
    ("INFY", "2026-10-01", "EXPIRY"),
    ("INFY", "2026-09-28", "STOP"),
    ("TCS", "2026-08-12", "T1_TRAIL"),
    ("RELIANCE", "2026-10-06", "T2"),
]


def test_fixture_directory_resolves_and_is_populated():
    """The four-hop resolution above must land on the real read-only CSVs."""
    assert CSVS.is_dir(), f"ground-truth CSV directory not found at {CSVS}"
    assert (CSVS / "TCS_NSE_5m.csv").is_file()


@pytest.fixture(autouse=True)
def _dry_and_quiet(monkeypatch, tmp_path):
    """No API calls, no notifications, no writes to the real journal."""
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(journal, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(notify, "send", lambda message: True)


def load(symbol):
    df = pd.read_csv(CSVS / f"{symbol}_NSE_5m.csv")
    df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
    df = df.set_index("timestamp").sort_index()
    df.index = df.index.tz_localize("Asia/Kolkata")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


@pytest.mark.parametrize("symbol,day,expected_reason", CASES)
def test_replay_matches_simulate_trade(symbol, day, expected_reason):
    """Drive the executor state machine over stored bars; compare to backtest."""
    df = load(symbol)
    target = dt.date.fromisoformat(day)
    planned = backtest_source.plan_for_day(df, target, backtest_source.args())
    expected = (
        None if planned is None else backtest_source.load().simulate_trade(df[df.index.date == target], *planned)
    )

    result = None if planned is None else replay.replay(symbol, df, target, *planned)

    if expected is None:
        assert result is None
        return

    assert result is not None
    # The case-selection table is a cross-check on the backtest itself, so a
    # future data refresh that changes an outcome cannot pass silently.
    assert expected["reason"] == expected_reason
    assert result["reason"] == expected["reason"]
    assert result["r_multiple"] == pytest.approx(expected["r_multiple"], abs=1e-3)


def test_every_exit_reason_is_covered_by_the_case_table():
    """Every reason `simulate_trade` can emit must be exercised here."""
    assert {reason for _, _, reason in CASES if reason} == {
        "STOP",
        "T1_TRAIL",
        "T2",
        "EXPIRY",
    }


def test_replay_exercises_the_t1_stop_modification(monkeypatch):
    """The T1_TRAIL case must really move the broker stop, not skip past it.

    Runs with `DRY_RUN` off so the API calls are actually issued -- under dry
    run the state machine advances but the broker is never touched, which would
    make this assertion vacuous.
    """
    monkeypatch.setattr(config, "DRY_RUN", False)
    df = load("TCS")
    target = dt.date(2026, 8, 12)
    planned = backtest_source.plan_for_day(df, target, backtest_source.args())

    api = replay.FakeApi()
    result = replay.replay("TCS", df, target, *planned, api=api)

    assert result["reason"] == "T1_TRAIL"
    assert len(api.modified) == 1
    assert api.modified[0]["trigger_price"] == planned[1]["target1"]
    # No partial exit: nothing was closed, and the bracket is still full size.
    assert api.closed == []
    assert api.modified[0]["position_size"] == 1
    # The protective bracket really was placed on the fill, before T1. `replay`
    # models the entry as already filled (the broker's job), so the only order
    # it places is the SL-M bracket.
    assert len(api.orders) == 1
    assert api.orders[0]["price_type"] == "SL-M"
    assert api.orders[0]["stoploss"] == planned[1]["stop"]
    assert api.orders[0]["target"] == planned[1]["target2"]
