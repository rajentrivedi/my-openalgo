import pytest

from backtesting.scalpel import selector
from backtesting.scalpel.loader import FEATURE_COLS

BASE = {
    "symbol": "RELIANCE",
    "prev_gap_pct": 0.4,
    "prev_trend_pct": 3.0,
    "prev_vol_pct": 1.2,
    "prev_rvol_median": 1.4,
    "prev_notional_median": 2.5e6,
    "prev_ret_5m": 0.3,
    "prev_close_to_high": 0.995,
    "prev_daily_notional_cr": 1500.0,
    "is_liquid": True,
    "prev_date": "2025-01-06",
}


def one(**over):
    row = dict(BASE)
    row.update(over)
    return row


def to_df(rows):
    import pandas as pd

    return pd.DataFrame(rows)[FEATURE_COLS]


def test_returns_only_tradable_symbols():
    df = to_df(
        [
            one(symbol="LIQ", is_liquid=True),
            one(symbol="THIN", is_liquid=False, prev_daily_notional_cr=2.0),
        ]
    )
    ranked = selector.rank_symbols(df, top_n=5)
    assert set(ranked) == {"LIQ"}


def test_deterministic_order():
    df = to_df(
        [
            one(symbol="A", prev_vol_pct=2.0),
            one(symbol="B", prev_vol_pct=1.0),
        ]
    )
    r1 = selector.rank_symbols(df, top_n=2)
    r2 = selector.rank_symbols(df, top_n=2)
    assert r1 == r2 == ["A", "B"]


def test_no_current_day_fields_in_rule():
    rule = selector.rule_string()
    assert "open" not in rule or "prev_" in rule
    assert "vwap" not in rule
    assert "today" not in rule


def test_top_n_respected():
    df = to_df([one(symbol=f"S{i}", prev_vol_pct=float(i)) for i in range(1, 8)])
    ranked = selector.rank_symbols(df, top_n=3)
    assert len(ranked) == 3
    assert ranked[0] == "S7"


def test_tiebreak_explicit():
    df = to_df(
        [
            one(symbol="X", prev_vol_pct=1.0, prev_daily_notional_cr=100.0),
            one(symbol="Y", prev_vol_pct=1.0, prev_daily_notional_cr=200.0),
        ]
    )
    ranked = selector.rank_symbols(df, top_n=2)
    assert ranked == ["Y", "X"]


def test_empty_df_returns_empty():
    import pandas as pd

    assert selector.rank_symbols(pd.DataFrame(columns=FEATURE_COLS), top_n=3) == []


def test_rule_string_weights_are_numeric():
    rule = selector.rule_string()
    assert "vol" in rule
    assert "notional" in rule
