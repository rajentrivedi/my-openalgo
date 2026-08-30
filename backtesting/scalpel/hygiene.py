import pandas as pd

from backtesting.scalpel import config

OHLC_COLS = ["open", "high", "low", "close"]
FINAL_COLS = ["timestamp", "datetime", "symbol", "open", "high", "low", "close", "volume", "oi"]


def clean_1m_series(con, symbol, start_epoch, end_epoch):
    rows = con.execute(
        """
        SELECT "timestamp", open, high, low, close, volume, oi
        FROM market_data
        WHERE symbol = ? AND "timestamp" BETWEEN ? AND ?
        ORDER BY "timestamp";
        """,
        [symbol, int(start_epoch), int(end_epoch)],
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=FINAL_COLS)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    local = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(config.SESSION_TZ).dt.tz_localize(None)
    df["datetime"] = local
    session_mask = local.dt.strftime("%H:%M").between(config.SESSION_START, config.SESSION_END)
    df = df[session_mask]
    df = df[(df[OHLC_COLS] > 0).all(axis=1) & df.eval("high >= low")]
    df["symbol"] = symbol
    df = df[FINAL_COLS].reset_index(drop=True)
    return df


def anomaly_report(con):
    holiday = con.execute(
        """
        SELECT count(*) FROM market_data
        WHERE strftime('%H', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '18'
        """
    ).fetchone()[0]
    partial = con.execute(
        """
        SELECT count(DISTINCT date(to_timestamp("timestamp") + INTERVAL '5.5 hours'))
        FROM market_data
        WHERE date(to_timestamp("timestamp") + INTERVAL '5.5 hours') = DATE '2025-10-21'
        """
    ).fetchone()[0]
    return {"holiday_evening_bars_removed": holiday, "bad_day_present": bool(partial)}