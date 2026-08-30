import math
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest

from backtesting.scalpel import loader

IST = ZoneInfo("Asia/Kolkata")

SCHEMA = """
CREATE TABLE market_data (
    symbol VARCHAR, exchange VARCHAR, "interval" VARCHAR,
    "timestamp" BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume BIGINT, oi BIGINT, created_at TIMESTAMP
)
"""


def ts(day, hm):
    return int(datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


def seed_days(con, symbols, n_days=25, start="2025-01-06"):
    days = pd.bdate_range(start, periods=n_days)
    rows = []
    for idx, day in enumerate(days):
        open_d = 100.0 + idx
        for sym in symbols:
            vol = 100_000 if sym == "TEST" else 1
            for m in range(375):
                o = open_d + (0.5 if m == 5 else 0.0)
                c = open_d + 1.0 + 0.001 * math.sin(m)
                rows.append(
                    (
                        sym,
                        "NSE",
                        "1m",
                        ts(
                            day.strftime("%Y-%m-%d"),
                            f"{9 + (m + 15) // 60:02d}:{(m + 15) % 60:02d}",
                        ),
                        o,
                        open_d + 1.5,
                        open_d - 1.5,
                        c,
                        vol,
                        0,
                        None,
                    )
                )
    con.executemany("INSERT INTO market_data VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return days


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(SCHEMA)
    seed_days(c, ["TEST", "THIN"])
    return c


def test_day_series_shape(con):
    df = loader.day_series(con, "TEST", "2025-01-06")
    assert len(df) == 375
    assert df["datetime"].min().strftime("%H:%M") == "09:15"
    assert df["datetime"].max().strftime("%H:%M") == "15:29"
    assert (df["datetime"].dt.strftime("%Y-%m-%d") == "2025-01-06").all()


def test_prev_day_features_expected_values(con):
    feat = loader.prev_day_features(con, "2025-02-10")
    assert not feat.empty
    assert feat["prev_date"].iloc[0] == "2025-02-07"
    test = feat[feat["symbol"] == "TEST"].iloc[0]
    assert test["prev_gap_pct"] == pytest.approx(0.0, abs=1e-2)
    assert test["prev_trend_pct"] == pytest.approx((125 / 105 - 1) * 100, rel=1e-4)
    assert test["prev_ret_5m"] == pytest.approx(0.5 / 124.0, rel=1e-6)
    assert test["prev_close_to_high"] == pytest.approx((124.0 + 1.0) / (124.0 + 1.5), rel=1e-4)
    assert test["prev_rvol_median"] == pytest.approx(1.0, rel=1e-6)
    assert test["prev_daily_notional_cr"] > 10.0
    assert bool(test["is_liquid"])
    assert test["prev_vol_pct"] > 0.0


def test_prev_day_features_no_nan_for_rich_history(con):
    feat = loader.prev_day_features(con, "2025-02-10")
    test = feat[feat["symbol"] == "TEST"].iloc[0]
    for col in loader.FEATURE_COLS:
        assert not pd.isna(test[col]), col


def test_liquidity_gate_flags_thin_symbol(con):
    feat = loader.prev_day_features(con, "2025-02-10")
    thin = feat[feat["symbol"] == "THIN"].iloc[0]
    assert not bool(thin["is_liquid"])
    assert thin["prev_daily_notional_cr"] < 10.0


def test_prev_day_features_returns_empty_when_no_prior(con):
    feat = loader.prev_day_features(con, "2024-01-01")
    assert feat.empty


def test_notional_medians(con):
    med = loader.notional_medians(con, 0, 2_000_000_000, use_cache=False)
    assert med["TEST"] == pytest.approx(100_000 * 113.0 * 375.0, rel=1e-2)
    assert med["THIN"] < med["TEST"]
