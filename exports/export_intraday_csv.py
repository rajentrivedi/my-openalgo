"""Export intraday OHLCV data from the Historify DuckDB to a combined CSV.

Reads 1m bars from db/historify.duckdb (Historify store) and writes one
combined CSV with a symbol column. Timestamps are emitted as IST strings
plus a raw UTC epoch column.

Handles the DuckDB exclusive lock held by a running OpenAlgo app by
falling back to a temp copy of the database (same pattern as
backtesting/sbi_pine6/validate_reliance.py).

Usage:
    uv run python exports/export_intraday_csv.py \
        --symbols RELIANCE,TCS,INFY \
        --months 6 \
        --interval 1m \
        --out exports/intraday_1m_last6months.csv
"""

import argparse
import calendar
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DB = "db/historify.duckdb"


def resolve_db(db_path: str) -> str:
    """Return a path that duckdb can open read-only.

    If the live DB is locked by the running app, copy it to a temp dir.
    """
    try:
        con = duckdb.connect(db_path, read_only=True)
        con.close()
        return db_path
    except duckdb.IOException:
        tmp = Path(tempfile.mkdtemp(prefix="historify_export_")) / "historify.duckdb"
        print(f"DB is locked by the running app; copying {db_path} -> {tmp} ...")
        shutil.copy2(db_path, tmp)
        return str(tmp)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export intraday data from Historify DuckDB to CSV")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbol list")
    parser.add_argument("--months", type=int, default=6, help="Lookback window in months (default 6)")
    parser.add_argument("--interval", default="1m", help="Stored interval (default 1m)")
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--db", default=None, help=f"DuckDB path (default: {DEFAULT_DB})")
    parser.add_argument("--out", default=None, help="Output CSV path (default: exports/intraday_<interval>.csv)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    db_path = resolve_db(args.db or DEFAULT_DB)
    out_path = Path(args.out or f"exports/intraday_{args.interval}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Window: last N months ending today (IST).
    end_dt = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - _RelDelta(args.months)
    start_epoch = int(start_dt.timestamp())
    end_epoch = int(end_dt.timestamp())
    print(f"Window: {start_dt:%Y-%m-%d %H:%M IST} .. {end_dt:%Y-%m-%d %H:%M IST} "
          f"(epoch {start_epoch}..{end_epoch})")

    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("SET threads TO 4")

        # Guard against typos: fail loudly on symbols not present in the catalog.
        catalog = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT symbol, record_count FROM data_catalog "
                "WHERE interval = ? AND exchange = ? AND symbol IN ("
                + ",".join("?" * len(symbols)) + ")",
                [args.interval, args.exchange, *symbols],
            ).fetchall()
        }
        missing = [s for s in symbols if s not in catalog]
        if missing:
            print(f"ERROR: symbols not in catalog for interval '{args.interval}': {missing}")
            return 1
        print(f"Symbols confirmed: {symbols}")

        placeholders = ",".join("?" * len(symbols))
        query = f"""
            COPY (
                SELECT
                    symbol,
                    strftime(to_timestamp(timestamp) AT TIME ZONE 'Asia/Kolkata',
                             '%Y-%m-%d %H:%M:%S') AS timestamp_ist,
                    open, high, low, close, volume, oi,
                    timestamp AS epoch_utc
                FROM market_data
                WHERE interval = ? AND exchange = ? AND symbol IN ({placeholders})
                  AND timestamp >= ? AND timestamp < ?
                ORDER BY symbol, timestamp
            ) TO '{out_path.as_posix()}' (HEADER, DELIMITER ',')
        """
        con.execute(query, [args.interval, args.exchange, *symbols, start_epoch, end_epoch])
    finally:
        con.close()

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {out_path} ({size_mb:.1f} MB)")
    return 0


class _RelDelta:
    def __init__(self, months: int) -> None:
        self.months = months

    def __rsub__(self, other: datetime) -> datetime:
        total = other.year * 12 + (other.month - 1) - self.months
        y, m = divmod(total, 12)
        m += 1
        day = min(other.day, calendar.monthrange(y, m)[1])
        return other.replace(year=y, month=m, day=day)


if __name__ == "__main__":
    sys.exit(main())
