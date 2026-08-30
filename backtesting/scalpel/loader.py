import os

import duckdb
import numpy as np
import pandas as pd

from backtesting.scalpel import config, hygiene

IST_OFFSET_SEC = 5.5 * 3600

FEATURE_COLS = [
    "symbol",
    "prev_gap_pct",
    "prev_trend_pct",
    "prev_vol_pct",
    "prev_rvol_median",
    "prev_notional_median",
    "prev_ret_5m",
    "prev_close_to_high",
    "prev_daily_notional_cr",
    "is_liquid",
    "prev_date",
]


def trading_calendar(con):
    rows = con.execute(
        """
        SELECT DISTINCT date(to_timestamp("timestamp") + INTERVAL '5.5 hours') AS d
        FROM market_data
        WHERE strftime('%H:%M', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '15:29'
        ORDER BY d
        """
    ).fetchall()
    return pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows]))


def prev_trading_day(con, date_ist):
    cal = trading_calendar(con)
    target = pd.Timestamp(date_ist)
    prior = cal[cal < target]
    if len(prior) == 0:
        return None
    return prior[-1].date().isoformat()


def day_series(con, symbol, date_ist):
    day = pd.Timestamp(date_ist)
    start = int(day.tz_localize(config.SESSION_TZ).timestamp())
    end = start + 24 * 3600
    df = hygiene.clean_1m_series(con, symbol, start, end)
    return df[df["datetime"].dt.strftime("%Y-%m-%d") == date_ist].reset_index(drop=True)


def _day_bounds(date_ist):
    day = pd.Timestamp(date_ist)
    start = int(day.tz_localize(config.SESSION_TZ).timestamp())
    return start, start + 24 * 3600


def _daily_ohlc(con, start_epoch, end_epoch):
    return con.execute(
        """
        SELECT symbol,
               date(to_timestamp("timestamp") + INTERVAL '5.5 hours') AS d,
               min_by(open, "timestamp") AS open,
               max(high) AS high,
               min(low) AS low,
               max_by(close, "timestamp") AS close,
               sum(volume * close) AS notional
        FROM market_data
        WHERE "timestamp" BETWEEN ? AND ?
          AND NOT (strftime('%H', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '18')
        GROUP BY symbol, d
        """,
        [int(start_epoch), int(end_epoch)],
    ).fetchdf()


def prev_day_features(con, date_ist):
    pd_ = prev_trading_day(con, date_ist)
    if pd_ is None:
        return pd.DataFrame(columns=FEATURE_COLS)
    cal = trading_calendar(con)
    idx = cal.get_loc(pd.Timestamp(pd_))
    window_start = cal[max(0, idx - 21)].date().isoformat()
    ws_start = _day_bounds(window_start)[0]
    ws_end = _day_bounds(pd_)[1]
    daily = _daily_ohlc(con, ws_start, ws_end)
    daily["d"] = pd.to_datetime(daily["d"])
    daily = daily.sort_values(["symbol", "d"])
    daily["prev_close"] = daily.groupby("symbol")["close"].shift(1)
    daily["close_21"] = daily.groupby("symbol")["close"].shift(20)
    d1 = daily[daily["d"].dt.date == pd.Timestamp(pd_).date()].copy()
    if d1.empty:
        return pd.DataFrame(columns=FEATURE_COLS)
    d1["prev_gap_pct"] = (d1["open"] / d1["prev_close"] - 1.0) * 100.0
    d1["prev_trend_pct"] = (d1["close"] / d1["close_21"] - 1.0) * 100.0
    d1["prev_close_to_high"] = d1["close"] / d1["high"]

    ss, se = _day_bounds(pd_)
    bars = con.execute(
        """
        SELECT symbol, "timestamp", open, high, low, close, volume
        FROM market_data
        WHERE "timestamp" BETWEEN ? AND ?
          AND NOT (strftime('%H', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '18')
        """,
        [ss, se],
    ).fetchdf()
    if bars.empty:
        return pd.DataFrame(columns=FEATURE_COLS)
    bars["datetime"] = pd.to_datetime(bars["timestamp"], unit="s", utc=True).dt.tz_convert(config.SESSION_TZ).dt.tz_localize(None)
    bars["time"] = bars["datetime"].dt.strftime("%H:%M")
    bars["notional"] = bars["volume"] * bars["close"]
    bars["ret"] = bars.groupby("symbol")["close"].pct_change()
    bars["vol_ratio"] = bars["volume"] / bars.groupby("symbol")["volume"].transform("mean")
    bars = bars[bars["time"].between(config.SESSION_START, config.SESSION_END)]

    def agg(g):
        return pd.Series(
            {
                "prev_vol_pct": g["ret"].std() * 100.0,
                "prev_rvol_median": g["vol_ratio"].median(),
                "prev_notional_median": g["notional"].median(),
                "prev_daily_notional_cr": g["notional"].sum() / 1e7,
                "prev_ret_5m": g.loc[g["time"] == "09:20", "open"].iloc[0] / g.loc[g["time"] == "09:15", "open"].iloc[0] - 1.0,
            }
        )

    intra = bars.groupby("symbol").apply(agg, include_groups=False).reset_index()

    out = d1[["symbol", "prev_gap_pct", "prev_trend_pct", "prev_close_to_high"]].merge(intra, on="symbol", how="left")
    out["prev_date"] = pd_
    out["is_liquid"] = out["prev_daily_notional_cr"] >= config.LIQUIDITY_GATE_CR
    out = out[FEATURE_COLS].sort_values("symbol").reset_index(drop=True)
    return out


def _median_notional(con, start_epoch, end_epoch):
    rows = con.execute(
        """
        SELECT symbol, date(to_timestamp("timestamp") + INTERVAL '5.5 hours') AS d, sum(volume * close) AS notional
        FROM market_data
        WHERE "timestamp" BETWEEN ? AND ?
          AND NOT (strftime('%H', to_timestamp("timestamp") + INTERVAL '5.5 hours') = '18')
        GROUP BY symbol, d
        """,
        [int(start_epoch), int(end_epoch)],
    ).fetchdf()
    return rows.groupby("symbol")["notional"].median().to_dict()


def notional_medians(con, start_epoch, end_epoch, use_cache=True):
    cache_path = os.path.join(config.CACHE_DIR, "notional_medians.parquet")
    meta_path = os.path.join(config.CACHE_DIR, "notional_medians.meta")
    if use_cache and os.path.exists(cache_path) and os.path.exists(meta_path):
        meta = pd.read_csv(meta_path, index_col=0)
        if int(meta.loc["start", "v"]) == int(start_epoch) and int(meta.loc["end", "v"]) == int(end_epoch):
            df = pd.read_parquet(cache_path)
            return dict(zip(df["symbol"], df["notional"]))
    medians = _median_notional(con, start_epoch, end_epoch)
    df = pd.DataFrame({"symbol": list(medians.keys()), "notional": list(medians.values())})
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    pd.DataFrame({"k": ["start", "end"], "v": [start_epoch, end_epoch]}).to_csv(meta_path, index=False)
    return medians