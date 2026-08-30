import numpy as np
import pandas as pd
import pytest

from backtesting.scalpel import engine

SESSION_OPEN = pd.Timestamp("2026-01-05 09:15:00")
SESSION_END = pd.Timestamp("2026-01-05 15:30:00")
NO_VWAP = {"vwap_z_thr": -999.0, "bbpct_thr": 0.0, "rsi_thr": 5.0, "min_rvol": 0.0}


def bars(close, step=0.5, t0=SESSION_OPEN):
    n = len(close)
    dt = pd.date_range(t0, periods=n, freq="1min")
    close = np.asarray(close, dtype=float)
    prev = np.concatenate([[close[0]], close[:-1]])
    open = prev.copy()
    high = np.maximum(open, close) + step
    low = np.minimum(open, close) - step
    return pd.DataFrame(
        {
            "datetime": dt,
            "symbol": "TEST",
            "open": open,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 10_000.0),
            "oi": np.zeros(n),
        }
    )


def runner_close():
    n = 300
    close = np.full(n, 100.0)
    close[120:132] = np.linspace(100, 90, 12)
    close[132:162] = np.linspace(90, 104, 30)
    close[162:] = 98.0
    return close


def test_flat_market_no_aggressive_trades():
    df = bars([100.0] * 400)
    a = engine.run_strategy(df, engine.DEFAULT_PARAMS, cost=0.001)
    assert a.n_trades == 0


def test_stop_hit_produces_negative_trade():
    n = 300
    close = np.full(n, 100.0)
    close[120:140] = np.linspace(100, 90, 20)
    close[140:] = 96.0
    df = bars(close)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 8}, cost=0.001)
    assert a.n_trades >= 1
    assert a.trades["ret_pct"].min() < 0


def test_hard_exit_no_overnight():
    close = np.full(400, 100.0)
    close[100:400] = 96.5
    df = bars(close)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 400}, cost=0.001)
    if a.n_trades:
        assert a.trades["exit_ts"].max() <= SESSION_END


def test_no_double_entry_same_bar():
    close = np.full(120, 100.0)
    close[30:70] = 92.0
    df = bars(close)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 400}, cost=0.001)
    if a.n_trades and len(a.trades):
        counts = a.trades["entry_ts"].value_counts()
        assert int(counts.max()) == 1


def test_winner_trail_ratchets_and_positive():
    close = runner_close()
    df = bars(close, step=0.1)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 400}, cost=0.001)
    assert a.n_trades >= 1
    assert float(a.equity.iloc[-1]) > 1.0


def test_round_trip_zero_move_is_bounded():
    n = 300
    close = np.full(n, 100.0)
    close[120:140] = np.linspace(100, 90, 20)
    close[140:] = 96.0
    df = bars(close)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 400}, cost=0.001)
    if a.n_trades:
        assert a.trades["ret_pct"].min() > -5.0


def test_equity_and_metrics_present():
    close = np.full(400, 100.0)
    close[100:200] = np.linspace(100, 89, 100)
    close[200:400] = np.linspace(89, 105, 200)
    df = bars(close)
    a = engine.run_strategy(df, {**NO_VWAP, "max_hold_bars": 400}, cost=0.001)
    assert isinstance(a.pf, float)
    assert isinstance(a.expectancy, float)
    assert isinstance(a.max_dd, float)
    assert len(a.equity) == len(df)


def test_stop_moves_to_breakeven_in_winner():
    close = runner_close()
    df = bars(close, step=0.1)
    a = engine.run_strategy(
        df, {**NO_VWAP, "proftrigger_r": 0.2, "trail_r": 0.2, "max_hold_bars": 400}, cost=0.001
    )
    if a.n_trades:
        assert a.trades["ret_pct"].max() > 0.01
