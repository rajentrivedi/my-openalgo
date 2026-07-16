"""Import OHLCV CSV files into the OpenAlgo Historify DuckDB.

Kotak Neo does not support historical data, so 1m/daily bars must be
imported from CSV. This script mirrors database.historify_db.import_from_csv
and writes into db/historify.duckdb so backtests can load the bars via DuckDB.

Usage:
    venv/bin/python backtesting/import_csv.py RELIANCE_1m.csv --symbol RELIANCE --exchange NSE --interval 1m
    venv/bin/python backtesting/import_csv.py SBIN.csv --symbol SBIN --exchange NSE --interval 1m
    venv/bin/python backtesting/import_csv.py MY.csv --dry-run

Accepted column layouts (headers, case-insensitive):
    timestamp, open, high, low, close, volume, oi
    datetime, open, high, low, close, volume
    date, time, open, high, low, close, volume, oi

timestamp may be epoch seconds/milliseconds or an ISO datetime string.
--symbol defaults to the CSV filename stem (e.g. RELIANCE_1m.csv -> RELIANCE).
"""

import argparse
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import find_dotenv, load_dotenv


def resolve_db_path(db_arg: str | None) -> str:
    load_dotenv(find_dotenv(), override=False)
    if db_arg:
        return os.path.abspath(db_arg)
    env = os.getenv("HISTORIFY_DB_PATH") or os.getenv("HISTORIFY_DATABASE_PATH")
    if env:
        return os.path.abspath(env)
    return os.path.abspath("db/historify.duckdb")


def parse_csv(path: str) -> tuple[pd.DataFrame, int]:
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("CSV file is empty")

    df.columns = df.columns.str.lower().str.strip()

    if "timestamp" in df.columns:
        if pd.api.types.is_numeric_dtype(df["timestamp"]):
            if df["timestamp"].iloc[0] > 1e12:
                df["timestamp"] = df["timestamp"] // 1000
        else:
            df["timestamp"] = (
                pd.to_datetime(df["timestamp"]).astype("datetime64[ns]").astype("int64") // 10**9
            )
    elif "datetime" in df.columns:
        df["timestamp"] = (
            pd.to_datetime(df["datetime"]).astype("datetime64[ns]").astype("int64") // 10**9
        )
    elif "date" in df.columns:
        if "time" in df.columns:
            df["timestamp"] = (
                pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
                .astype("datetime64[ns]")
                .astype("int64")
                // 10**9
            )
        else:
            df["timestamp"] = (
                pd.to_datetime(df["date"].astype(str)).astype("datetime64[ns]").astype("int64")
                // 10**9
            )
    else:
        raise ValueError("CSV must have 'timestamp', 'datetime', or 'date' column")

    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    if "oi" not in df.columns:
        df["oi"] = 0

    df = df[["timestamp", "open", "high", "low", "close", "volume", "oi"]]
    df["timestamp"] = df["timestamp"].astype("int64")
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0).astype("int64")

    dropped = len(df) - len(df.dropna(subset=["open", "high", "low", "close"]))
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df, dropped


def import_file(
    con: duckdb.DuckDBPyConnection,
    path: str,
    symbol: str,
    exchange: str,
    interval: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    df, dropped = parse_csv(path)
    if df.empty:
        raise ValueError("No valid data rows after parsing")

    df = df.copy()
    df["symbol"] = symbol.upper()
    df["exchange"] = exchange.upper()
    df["interval"] = interval
    df = df[
        [
            "symbol",
            "exchange",
            "interval",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "oi",
        ]
    ]

    if dry_run:
        print(df.head(3).to_string(index=False))
        return 0, dropped

    con.execute(
        """
        INSERT INTO market_data
        (symbol, exchange, interval, timestamp, open, high, low, close, volume, oi)
        SELECT symbol, exchange, interval, timestamp, open, high, low, close, volume, oi
        FROM df
        ON CONFLICT (symbol, exchange, interval, timestamp) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            oi = EXCLUDED.oi
        """
    )

    symbol = symbol.upper()
    exchange = exchange.upper()
    existing = con.execute(
        "SELECT id FROM data_catalog WHERE symbol = ? AND exchange = ? AND interval = ?",
        [symbol, exchange, interval],
    ).fetchone()

    if existing:
        con.execute(
            """
            UPDATE data_catalog SET
                first_timestamp = (SELECT MIN(timestamp) FROM market_data
                                  WHERE symbol = ? AND exchange = ? AND interval = ?),
                last_timestamp = (SELECT MAX(timestamp) FROM market_data
                                 WHERE symbol = ? AND exchange = ? AND interval = ?),
                record_count = (SELECT COUNT(*) FROM market_data
                               WHERE symbol = ? AND exchange = ? AND interval = ?),
                last_download_at = current_timestamp
            WHERE symbol = ? AND exchange = ? AND interval = ?
            """,
            [
                symbol,
                exchange,
                interval,
                symbol,
                exchange,
                interval,
                symbol,
                exchange,
                interval,
                symbol,
                exchange,
                interval,
            ],
        )
    else:
        next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM data_catalog").fetchone()[0]
        con.execute(
            """
            INSERT INTO data_catalog
            (id, symbol, exchange, interval, first_timestamp, last_timestamp,
             record_count, last_download_at)
            SELECT ?, ?, ?, ?, MIN(timestamp), MAX(timestamp), COUNT(*), current_timestamp
            FROM market_data
            WHERE symbol = ? AND exchange = ? AND interval = ?
            """,
            [next_id, symbol, exchange, interval, symbol, exchange, interval],
        )

    return len(df), dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Import OHLCV CSV files into the Historify DuckDB")
    parser.add_argument("files", nargs="+", help="CSV file(s) to import")
    parser.add_argument("--symbol", default=None, help="Symbol (default: CSV filename stem)")
    parser.add_argument("--exchange", default="NSE", help="Exchange code (default: NSE)")
    parser.add_argument("--interval", default="1m", help="Interval (default: 1m)")
    parser.add_argument(
        "--db", default=None, help="DuckDB path (default: HISTORIFY_DB_PATH from .env)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and preview without writing")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    if not os.path.exists(db_path):
        print(f"Error: database not found at {db_path}")
        sys.exit(1)

    print(f"Opening DuckDB: {db_path}")
    con = duckdb.connect(db_path)
    try:
        for path in args.files:
            if not os.path.exists(path):
                print(f"[{path}] ERROR: file not found")
                continue
            symbol = args.symbol or Path(path).stem.upper()
            try:
                records, dropped = import_file(
                    con, path, symbol, args.exchange, args.interval, args.dry_run
                )
                skipped = f" ({dropped} rows skipped)" if dropped else ""
                action = "would import" if args.dry_run else "imported"
                print(
                    f"[{path}] {action} {records} records -> {symbol}:{args.exchange}:{args.interval}{skipped}"
                )
            except Exception as e:
                print(f"[{path}] ERROR: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
