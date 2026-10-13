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
import executor
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
    # The entry-bar divergence case. Round 1 of the review found the harness
    # skipping the entry bar's exit evaluation; on this day that turned a
    # -1.000R STOP into a -0.209R EXPIRY. Pinned here so it can never drift
    # back. See `replay`'s module docstring, "Why the entry bar is evaluated".
    ("INFY", "2026-05-12", "STOP"),
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
    m = api.modified[0]
    side = planned[0]
    exit_side = "BUY" if side == "SHORT" else "SELL"
    assert m["trigger_price"] == planned[1]["target1"]
    assert m["action"] == exit_side and m["order_id"].startswith("P")
    # No partial exit: nothing was closed, and the bracket is still full size.
    assert api.closed == []
    # The bracket is two legs placed on fill -- an SL-M stop and a LIMIT
    # target, both on the exit side -- and the T1 modify moves the stop toward
    # target1 (down for a short, up for a long).
    assert len(api.placed) == 2
    stop, target = api.placed[0], api.placed[1]
    assert stop["price_type"] == "SL-M" and stop["action"] == exit_side
    assert target["price_type"] == "LIMIT" and target["action"] == exit_side
    if planned[0] == "SHORT":
        assert m["trigger_price"] < stop["trigger_price"]
    else:
        assert m["trigger_price"] > stop["trigger_price"]


# --- the entry-bar rule, pinned so it cannot regress in either direction ---


def _synthetic(bars, *, day="2026-03-02", trigger="09:55"):
    """A minimal tz-aware frame plus a trigger one bar before the first bar.

    `bars` is `(time, open, high, low, close)`. Synthetic rather than stored
    data on purpose: these assertions must keep holding whatever the CSVs
    contain, and they must state the rule in isolation -- each bar is here
    specifically to make one clause of the entry-bar behaviour visible.
    """
    index = pd.DatetimeIndex(
        [pd.Timestamp(f"{day} {row[0]}", tz=executor.TZ) for row in bars]
    )
    frame = pd.DataFrame(
        {
            "open": [float(row[1]) for row in bars],
            "high": [float(row[2]) for row in bars],
            "low": [float(row[3]) for row in bars],
            "close": [float(row[4]) for row in bars],
            "volume": [1.0] * len(bars),
        },
        index=index,
    )
    return frame, pd.Timestamp(f"{day} {trigger}", tz=executor.TZ)


def _long_plan(**over):
    plan = {"entry": 100.0, "stop": 99.0, "target1": 100.5, "target2": 104.0,
            "expires_at": "2026-03-02T10:30:00+05:30"}
    plan.update(over)
    return plan


def _run(frame, trigger, plan, side="LONG"):
    import replay as replay_module

    return replay_module.replay("SYN", frame, dt.date(2026, 3, 2), side, plan, trigger)


def test_entry_bar_alone_can_resolve_a_stop():
    """Round 1, Critical: the entry bar must be evaluated.

    Bar 10:00 triggers the entry (high 101 >= 100) and also trades down to the
    stop (low 98.5 <= 99). The bracket is live from the fill, so the position
    is stopped on the bar that opened it.

    If the entry bar were skipped this would fall through to EXPIRY at the
    final close -- a different reason AND a different R, which is exactly the
    ~4.8R INFY 2026-05-12 divergence the review found.
    """
    frame, trigger = _synthetic([("10:00", 100.0, 101.0, 98.5, 100.5)])
    result = _run(frame, trigger, _long_plan())
    assert result == {"reason": "STOP", "r_multiple": pytest.approx(-1.0)}


def test_entry_bar_alone_can_reach_t2():
    """The favourable mirror: T2 on the entry bar must not become an expiry."""
    frame, trigger = _synthetic([("10:00", 99.5, 105.0, 99.4, 104.0)])
    result = _run(frame, trigger, _long_plan())
    assert result["reason"] == "T2"
    assert result["r_multiple"] == pytest.approx(4.0)


def test_entry_bar_t1_moves_the_stop_for_later_bars():
    """Round 1, Critical: T1 on the entry bar must arm the trail.

    Bar 10:00 touches T1 (high 101 >= 100.5) so the stop moves to 100.5. Bar
    10:05 then trades to 99.0 -- through the ORIGINAL stop but short of the
    trail.

    Skipping the entry bar leaves T1 unarmed, so 10:05 books STOP -1.0R.
    Evaluating it books T1_TRAIL +0.5R. Same bars, opposite outcomes, so this
    fails in EITHER direction of the regression.
    """
    frame, trigger = _synthetic(
        [("10:00", 99.5, 101.0, 99.4, 100.0), ("10:05", 100.2, 100.3, 99.0, 99.5)]
    )
    result = _run(frame, trigger, _long_plan())
    assert result["reason"] == "T1_TRAIL"
    assert result["r_multiple"] == pytest.approx(0.5)


def test_r_is_measured_against_the_planned_stop_not_the_trailed_one():
    """Round 1 minor: close the case-table gap that made `current_stop` invisible.

    Every stored T1/T2 case in `CASES` has a symmetric plan -- `target1` sits
    exactly `risk_per_share` from entry -- so swapping `stop` for `current_stop`
    in the R denominator changed nothing and all replay tests still passed. Here
    `target1` is deliberately 0.5 above entry against a stop 1.0 below it, so
    the two denominators differ and the choice is observable.

    Planned-risk denominator: 0.5 / 1.0 = +0.5R.
    Trailed-stop denominator: 0.5 / 0.5 = +1.0R.
    """
    frame, trigger = _synthetic(
        [("10:00", 99.5, 101.0, 99.4, 100.0), ("10:05", 100.2, 100.3, 99.0, 99.5)]
    )
    result = _run(frame, trigger, _long_plan(target1=100.5, target2=110.0))
    assert result["reason"] == "T1_TRAIL"
    assert result["r_multiple"] == pytest.approx(0.5)
    assert result["r_multiple"] != pytest.approx(1.0)


def test_entry_bar_divergence_case_is_named_and_pinned():
    """The real stored day the review flagged, asserted end to end."""
    df = load("INFY")
    target = dt.date(2026, 5, 12)
    planned = backtest_source.plan_for_day(df, target, backtest_source.args())
    side, plan, trigger = planned
    assert side == "SHORT"

    bars = df[df.index.date == target]
    bars = bars[bars.index > trigger]
    bars = bars[bars.index <= pd.Timestamp(plan["expires_at"])]
    entry_bar = next(
        row
        for _, row in bars.iterrows()
        if float(row["low"]) <= float(plan["entry"])
    )
    # The bar that carries the whole argument, asserted from the stored data:
    # it OPENS between the entry and the stop, then reaches both extremes.
    assert float(plan["entry"]) <= float(entry_bar["open"]) <= float(plan["stop"])
    assert float(entry_bar["low"]) <= float(plan["entry"])  # triggers the entry
    assert float(entry_bar["high"]) >= float(plan["stop"])  # reaches the stop

    import replay as replay_module

    result = replay_module.replay("INFY", df, target, side, plan, trigger)
    assert result["reason"] == "STOP"
    assert result["r_multiple"] == pytest.approx(-1.0, abs=1e-3)
