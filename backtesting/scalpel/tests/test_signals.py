import numpy as np
import pandas as pd
import pytest

from backtesting.scalpel import signals


def make_df(prices, volume=1000, start="2025-01-02"):
    n = len(prices)
    times = pd.date_range(f"{start} 09:15", periods=n, freq="1min")
    p = np.asarray(prices, dtype=float)
    vol = np.full(n, volume, dtype=float)
    return pd.DataFrame(
        {
            "datetime": times,
            "open": p,
            "high": p * 1.002,
            "low": p * 0.998,
            "close": p,
            "volume": vol,
        }
    )


def test_vwap_z_flat_is_zero():
    df = make_df(np.full(120, 100.0))
    s = signals.build_frame(df)
    assert np.allclose(s["vwap_z"], 0.0, atol=1e-9)


def test_vwap_z_positive_on_uptrend():
    df = make_df(100.0 + 0.05 * np.arange(120))
    s = signals.build_frame(df)
    assert s["vwap_z"][0] == pytest.approx(0.0, abs=1e-9)
    assert s["vwap_z"][-1] > 0.0


def test_rsi2_hits_100_and_0():
    df = make_df(np.r_[np.full(20, 100.0), [100.5, 101.0]])
    up = signals.build_frame(df)["rsi2"]
    assert up[-1] == pytest.approx(100.0)
    df = make_df(np.r_[np.full(20, 100.0), [99.5, 99.0]])
    dn = signals.build_frame(df)["rsi2"]
    assert dn[-1] == pytest.approx(0.0)


def test_roll_z_uses_only_past_closes():
    k = 110
    prices = np.r_[np.full(100, 100.0), np.full(60, 105.0)]
    s = signals.build_frame(make_df(prices))
    window = prices[k - 60 : k]
    mean = window.mean()
    std = window.std(ddof=0)
    expected = (prices[k] - mean) / std
    assert s["roll_z"][k] == pytest.approx(expected)


def test_or_fade_negative_on_deep_dip():
    prices = np.full(120, 100.0)
    prices[25:30] = 90.0
    prices[30:] = 100.0
    s = signals.build_frame(make_df(prices))
    assert s["or_fade"][27] < 0.0
    assert s["or_fade"][40] > 0.0


def test_all_signals_no_inf_and_correct_length():
    rng = np.random.default_rng(7)
    prices = 100 + np.cumsum(rng.normal(0, 0.3, 300))
    df = make_df(prices)
    s = signals.build_frame(df)
    for name in signals.SIGNALS + ["atr1", "atr5", "or_high", "or_low"]:
        assert len(s[name]) == len(df), name
        assert not np.isinf(s[name]).any(), name


def test_atr5_no_lookahead():
    prices = np.full(120, 100.0)
    prices[40:] = 110.0
    s = signals.build_frame(make_df(prices))
    atr5 = pd.Series(s["atr5"])
    assert np.isnan(atr5[0])
    assert atr5[41] == pytest.approx(0.4, rel=0.3)
    assert atr5[46] > 1.0
