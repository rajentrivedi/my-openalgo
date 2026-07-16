"""
Stock data fetching and caching via OpenAlgo SDK.
Mirrors load_universe_data from backtest_range_breakout but outputs dict of DataFrames.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)

from .. import config

IST = timezone(timedelta(hours=5, minutes=30))

_CACHE_DIR = "backtest_cache"


def load_symbols() -> list[str]:
    syms = []
    with open(config.SYMBOLS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = [p.strip() for p in line.split(",")]
                sym = parts[1] if len(parts) > 1 else parts[0]
                if sym.upper() in {"S.NO.", "SYMBOL", ""}:
                    continue
                syms.append(sym)
    nifty_filters = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "NIFTYNXT50"}
    syms = [s for s in syms if s.upper() not in nifty_filters]
    return syms[: config.MAX_SYMBOLS]


def _fetch_symbol(symbol: str, exchange: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        from openalgo import api
        api_key = os.environ.get("OPENALGO_API_KEY", "")
        host = os.environ.get("OPENALGO_HOST", "http://127.0.0.1:5000")
        client = api(api_key=api_key, host=host)
        df = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=config.TIMEFRAME,
            start_date=start,
            end_date=end,
        )
        if df is None or df.empty:
            return None
        df = df.copy()
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df.index = pd.to_datetime(df.index)
        df.index.name = "timestamp"
        return df
    except Exception:
        return None


def load_universe_data(
    symbols: list[str],
    use_cache: bool = False,
    start: str | None = None,
    end: str | None = None,
    warmup_days: int = 60,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None, dict[str, int]]:
    """Fetch data with extra warmup period for indicator computation."""
    if start is None:
        start = (datetime.now() - timedelta(days=config.LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    # Extend start backwards for warmup
    warmup_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    data_start = start  # store the real start for tracking

    # Extended fetch for warmup period
    cache_key_base = f"{start}_{end}".replace("-", "")
    warmup_cache_key_base = f"{warmup_start}_{end}".replace("-", "")

    os.environ["TZ"] = "Asia/Kolkata"
    raw: dict[str, pd.DataFrame] = {}
    quality: dict[str, int] = {"total": len(symbols), "success": 0, "failed": 0, "min_rows": 999999, "max_rows": 0}
    os.makedirs(_CACHE_DIR, exist_ok=True)

    for sym in symbols:
        cache_key = f"{sym}_{config.TIMEFRAME}_{warmup_cache_key_base}"
        cache_path = f"{_CACHE_DIR}/{cache_key}.parquet"
        df = None
        if use_cache and os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path)
            except Exception:
                df = None

        if df is None:
            df = _fetch_symbol(sym, config.STOCK_EXCHANGE, warmup_start, end)
            if df is not None and use_cache:
                try:
                    df.to_parquet(cache_path)
                except Exception:
                    pass

        if df is not None and len(df) >= 20:
            raw[sym] = df
            quality["success"] += 1
            quality["min_rows"] = min(quality["min_rows"], len(df))
            quality["max_rows"] = max(quality["max_rows"], len(df))
        else:
            quality["failed"] += 1

    nifty_raw = _fetch_symbol(config.NIFTY_SYMBOL, config.NIFTY_EXCHANGE, warmup_start, end)
    return raw, nifty_raw, quality


def print_data_quality(q: dict[str, int]) -> None:
    print(f"Data: {q['success']}/{q['total']} symbols, "
          f"rows {q['min_rows']}-{q['max_rows']}, failed={q['failed']}")


def resample_timeframe(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    mappings = {
        "15m": "15min",
        "1h": "1h",
        "1d": "1D",
        "1w": "1W",
    }
    rule = mappings.get(tf, tf)
    ohlc = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    ohlc.dropna(subset=["open"], inplace=True)
    return ohlc
