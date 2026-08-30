from datetime import datetime

import duckdb
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from backtesting.scalpel import config, hygiene

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


def make_bar(symbol, day, hm, price=100.0):
    t = ts(day, hm)
    return (symbol, "NSE", "1m", t, price, price, price, price, 1000, 0, None)


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(SCHEMA)
    rows = []
    day = "2025-01-02"
    for m in range(375):
        rows.append(make_bar("TEST", day, f"{9 + (m + 15) // 60:02d}:{(m + 15) % 60:02d}"))
    row = list(rows[45])
    row[5], row[6] = 80.0, 120.0
    rows[45] = tuple(row)
    for hm in range(30):
        rows.append(make_bar("TEST", "2024-11-01", f"18:{hm:02d}"))
    for hm in range(30, 60):
        rows.append(make_bar("TEST", "2024-11-02", f"18:{hm:02d}"))
    for m in range(61):
        minute = 13 * 60 + 45 + m
        rows.append(make_bar("TEST", "2025-10-21", f"{minute // 60:02d}:{minute % 60:02d}"))
    c.executemany("INSERT INTO market_data VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return c


def test_no_holiday_evening_bars(con):
    df = hygiene.clean_1m_series(con, "TEST", 0, 2_000_000_000)
    assert not (df["datetime"].dt.hour == 18).any()


def test_only_session_rows(con):
    df = hygiene.clean_1m_series(con, "TEST", 0, 2_000_000_000)
    times = df["datetime"].dt.strftime("%H:%M")
    assert times.between(config.SESSION_START, config.SESSION_END).all()
    assert len(df) == 374 + 61


def test_bad_price_row_dropped(con):
    df = hygiene.clean_1m_series(con, "TEST", 0, 2_000_000_000)
    opened = df["datetime"].dt.strftime("%H:%M")
    assert "10:00" not in set(opened)


def test_sorted_columns_no_nan(con):
    df = hygiene.clean_1m_series(con, "TEST", 0, 2_000_000_000)
    assert list(df.columns) == hygiene.FINAL_COLS
    assert df["timestamp"].is_monotonic_increasing
    assert df[["open", "high", "low", "close"]].notna().all().all()
    assert (df["symbol"] == "TEST").all()


def test_bad_day_still_returned_and_flagged(con):
    df = hygiene.clean_1m_series(con, "TEST", 0, 2_000_000_000)
    bad_day = df["datetime"].dt.strftime("%Y-%m-%d") == "2025-10-21"
    assert bad_day.sum() == 61
    assert "2025-10-21" in config.BAD_DAYS


def test_anomaly_report_counts(con):
    report = hygiene.anomaly_report(con)
    assert report["holiday_evening_bars_removed"] == 60
    assert report["bad_day_present"] is True