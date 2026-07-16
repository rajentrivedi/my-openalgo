"""
Quantix FOMO Momentum Strategy - Python Backtest
Converted from Pine Script (h-m.pine)

Strategy Logic:
- Intraday momentum strategy on NSE equities (5-min bars)
- Opening range breakout (09:15-09:45) with multiple confirmations
- Gap filter, Relative Volume, EMA alignment, VWAP, ADX
- ATR-based stop loss (1.5x) and take profit (3.0x)
- Hard exit at 15:15

Author: Converted from Pine Script by Quantix
"""

import os
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from dotenv import find_dotenv, load_dotenv
from openalgo import ta

# Try DuckDB, fallback to yfinance
try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

# ============================================================
# CONFIG
# ============================================================

script_dir = Path(__file__).resolve().parent
load_dotenv(find_dotenv(), override=False)

SYMBOL = "SBIN"
EXCHANGE = "NSE"
INTERVAL = "3m"
LOOKBACK_DAYS = 365

INIT_CASH = 1_000_000
FEES = 0.000025           # Near-zero: only statutory charges (STT sell-side)
FIXED_FEES = 0            # Zero brokerage account
ALLOCATION = 1.0          # 100% of capital per trade

# Strategy Parameters (scalp-optimized for zero brokerage)
TRADE_SESSION_START = "09:15"
TRADE_SESSION_END = "15:15"
OPENING_RANGE_START = "09:15"
OPENING_RANGE_END = "09:45"
ENABLE_GAP_FILTER = False    # Disabled for more trades
MIN_GAP_PCT = 0.5
RVOL_LENGTH = 20
MIN_RVOL = 1.0               # Lowered for more trades
EMA_FAST = 20
EMA_SLOW = 50
ATR_LENGTH = 14
ATR_SL_MULT = 1.0            # Tighter SL for scalping
ATR_TP_MULT = 1.5            # Quick TP
ADX_LENGTH = 14
MIN_ADX = 15                 # Lowered for more setups
EXIT_HOUR = 15
EXIT_MINUTE = 15

BENCHMARK_SYMBOL = "NIFTY"
BENCHMARK_EXCHANGE = "NSE_INDEX"

# ============================================================
# FETCH DATA (OpenAlgo API -> DuckDB -> yfinance)
# ============================================================

print(f"Loading {SYMBOL} {INTERVAL} data...")

end_date = datetime.now().date()
start_date = end_date - timedelta(days=LOOKBACK_DAYS)

HISTORIFY_DB = Path(__file__).resolve().parent.parent.parent / "db" / "historify.duckdb"

# Try OpenAlgo API first
df = None
try:
    from openalgo import api
    print("  Trying OpenAlgo API...")

    client = api(
        api_key=os.getenv("OPEN_ALGO_API_KEY"),
        host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
    )

    df_api = client.history(
        symbol=SYMBOL, exchange=EXCHANGE, interval=INTERVAL,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        source="api",
    )

    if isinstance(df_api, pd.DataFrame) and not df_api.empty:
        if "timestamp" in df_api.columns:
            df_api["timestamp"] = pd.to_datetime(df_api["timestamp"])
            df_api = df_api.set_index("timestamp")
        else:
            df_api.index = pd.to_datetime(df_api.index)

        df_api = df_api.sort_index()
        if df_api.index.tz is not None:
            df_api.index = df_api.index.tz_localize(None)

        df = df_api[["open", "high", "low", "close", "volume"]].copy()
        print(f"  Loaded from OpenAlgo API: {len(df)} bars")
    else:
        print("  No data from API, trying DuckDB...")
except Exception as e:
    print(f"  API error: {e}, trying DuckDB...")

# Try DuckDB if API failed
if df is None and HAS_DUCKDB and HISTORIFY_DB.exists():
    try:
        con = duckdb.connect(str(HISTORIFY_DB), read_only=True)
        df_db = con.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ? AND exchange = ? AND interval = ?
            ORDER BY timestamp
        """, [SYMBOL.upper(), EXCHANGE.upper(), INTERVAL]).fetchdf()
        con.close()

        if not df_db.empty:
            df_db["datetime"] = pd.to_datetime(df_db["timestamp"], unit="s")
            df_db = df_db.set_index("datetime").sort_index()
            df_db = df_db.drop(columns=["timestamp"])
            if df_db.index.tz is not None:
                df_db.index = df_db.index.tz_localize(None)
            df = df_db[["open", "high", "low", "close", "volume"]]
            print(f"  Loaded from DuckDB: {len(df)} bars")
    except Exception as e:
        print(f"  DuckDB error: {e}")

# Fallback to yfinance (download in 60-day chunks for intraday)
if df is None:
    try:
        import yfinance as yf

        yf_symbol = f"{SYMBOL}.NS" if EXCHANGE == "NSE" else SYMBOL

        # yfinance intraday limits: 5m max 60 days per request
        yf_interval = "5m"
        chunk_days = 55  # safe margin under 60-day limit
        max_intraday_days = 59  # yfinance hard limit for 5m

        # Calculate how far back we can go with intraday data
        intraday_start = end_date - timedelta(days=max_intraday_days)
        if start_date < intraday_start:
            print(f"  yfinance 5m limit: last {max_intraday_days} days only")
            actual_start = intraday_start
        else:
            actual_start = start_date

        print(f"  Downloading {yf_symbol} 5m data from yfinance ({actual_start} to {end_date})...")

        # Download in chunks
        chunks = []
        current_end = end_date
        while current_end > actual_start:
            current_start = max(current_end - timedelta(days=chunk_days), actual_start)
            try:
                chunk = yf.download(
                    yf_symbol,
                    start=current_start.strftime("%Y-%m-%d"),
                    end=(current_end + timedelta(days=1)).strftime("%Y-%m-%d"),
                    interval=yf_interval,
                    auto_adjust=True,
                    multi_level_index=False,
                )
                if not chunk.empty:
                    chunks.append(chunk)
                    print(f"    {current_start} to {current_end}: {len(chunk)} bars")
            except Exception as e:
                print(f"    {current_start} to {current_end}: error - {e}")
            current_end = current_start - timedelta(days=1)

        if not chunks:
            raise ValueError(f"No data returned from yfinance for {yf_symbol}")

        df_yf = pd.concat(chunks[::-1])
        df_yf = df_yf[~df_yf.index.duplicated(keep="first")]
        df_yf = df_yf.sort_index()

        df_yf.columns = df_yf.columns.str.lower()
        df_yf.index = pd.to_datetime(df_yf.index)
        if df_yf.index.tz is not None:
            df_yf.index = df_yf.index.tz_localize(None)

        df = df_yf[["open", "high", "low", "close", "volume"]].copy()
        df = df.dropna()
        print(f"  Loaded from yfinance: {len(df)} bars")
    except ImportError:
        print("ERROR: yfinance not installed. Install with: uv add yfinance")
        raise
    except Exception as e:
        print(f"ERROR: yfinance failed: {e}")
        raise

# Filter to trading hours (9:15 - 15:30 IST)
df["time"] = df.index.time
market_open = dtime(9, 15)
market_close = dtime(15, 30)
df = df[(df["time"] >= market_open) & (df["time"] <= market_close)]
df = df.drop(columns=["time"])

print(f"Data: {len(df)} bars from {df.index[0]} to {df.index[-1]}")

close = df["close"]
high = df["high"]
low = df["low"]
open_ = df["open"]
volume = df["volume"]

# ============================================================
# INDICATORS
# ============================================================

print("Computing indicators...")

# Add date column for daily grouping
df["date"] = df.index.date
unique_dates = np.unique(df["date"])

# --- EMA ---
ema_fast = ta.ema(close, EMA_FAST)
ema_slow = ta.ema(close, EMA_SLOW)

# --- VWAP (manual - session-based) ---
# VWAP = cumulative(typical_price * volume) / cumulative(volume) per day
typical_price = (high + low + close) / 3
tp_vol = typical_price * volume

# Cumulative within each day
vwap_values = pd.Series(index=df.index, dtype=float)
for d in unique_dates:
    mask = df["date"] == d
    cum_tp_vol = tp_vol[mask].cumsum()
    cum_vol = volume[mask].cumsum()
    vwap_values[mask] = cum_tp_vol / cum_vol.replace(0, np.nan)

vwap_value = vwap_values.ffill()

# --- ATR ---
atr = ta.atr(high, low, close, period=ATR_LENGTH)

# --- ADX (Directional Movement) ---
plus_di, minus_di, adx = ta.adx(high, low, close, period=ADX_LENGTH)

# --- Relative Volume ---
avg_volume = ta.sma(volume, RVOL_LENGTH)
relative_volume = volume / avg_volume.replace(0, np.nan)

# --- Gap % ---
# Gap = (open of first bar of day - close of last bar of prev day) / close of prev day * 100
# For intraday, we compute gap at the start of each day
gap_pct = pd.Series(0.0, index=df.index)
for i, d in enumerate(unique_dates):
    mask = df["date"] == d
    first_bar_idx = df[mask].index[0]
    if i > 0:
        prev_day_mask = df["date"] == unique_dates[i - 1]
        if prev_day_mask.any():
            prev_day_close = close[prev_day_mask].iloc[-1]
            gap_pct[first_bar_idx] = ((open_[first_bar_idx] - prev_day_close) / prev_day_close) * 100
    # Forward fill gap for all bars in the day
    gap_pct[mask] = gap_pct[first_bar_idx]

# --- Opening Range (09:15 - 09:45) ---
# With hourly data, the opening range is just the 09:15 bar
opening_high = pd.Series(np.nan, index=df.index)
opening_low = pd.Series(np.nan, index=df.index)

for d in unique_dates:
    mask = df["date"] == d
    day_data = df[mask]
    or_mask = (day_data.index.time >= dtime(9, 15)) & (day_data.index.time <= dtime(9, 45))

    if or_mask.any():
        or_data = day_data[or_mask]
        or_high = or_data["high"].max()
        or_low = or_data["low"].min()
        opening_high[mask] = or_high
        opening_low[mask] = or_low

# ============================================================
# SESSION FILTERS
# ============================================================

bar_time = df.index.time
in_trade_session = pd.Series(
    [(dtime(9, 15) <= t <= dtime(15, 15)) for t in bar_time],
    index=df.index
)
in_opening_range = pd.Series(
    [(dtime(9, 15) <= t <= dtime(9, 45)) for t in bar_time],
    index=df.index
)

# ============================================================
# ENTRY CONDITIONS
# ============================================================

print("Generating entry signals...")

# Gap filter (can be disabled)
gap_pass = gap_pct >= MIN_GAP_PCT if ENABLE_GAP_FILTER else pd.Series(True, index=df.index)

# Relative Volume filter
rvol_pass = relative_volume >= MIN_RVOL

# EMA alignment (bullish)
ema_bull = (close > ema_fast) & (ema_fast > ema_slow)

# VWAP filter
vwap_bull = close > vwap_value

# ADX filter
adx_pass = adx >= MIN_ADX

# Opening range breakout
or_breakout = close > opening_high

# Combined entry condition
entry_conditions = (
    gap_pass &
    rvol_pass &
    ema_bull &
    vwap_bull &
    adx_pass &
    or_breakout &
    in_trade_session &
    (~in_opening_range)  # Only after opening range ends
)

# --- One trade per day limit ---
# Track if we already entered today
traded_today = pd.Series(False, index=df.index)
for d in unique_dates:
    mask = df["date"] == d
    day_entries = entry_conditions[mask]
    if day_entries.any():
        first_entry_idx = day_entries[day_entries].index[0]
        # Mark all bars after first entry as traded
        traded_today[mask & (df.index >= first_entry_idx)] = True

# Final entry: first signal of the day only
entries_raw = entry_conditions & (~traded_today.shift(1).fillna(False))

# ============================================================
# EXIT CONDITIONS
# ============================================================

# Hard exit at 15:15
is_exit_bar = pd.Series(
    [(t == dtime(EXIT_HOUR, EXIT_MINUTE)) for t in bar_time],
    index=df.index
)

# Exit at end of session (last bar of the day)
last_bar_of_day = pd.Series(False, index=df.index)
for d in unique_dates:
    mask = df["date"] == d
    last_idx = df[mask].index[-1]
    last_bar_of_day[last_idx] = True

exits_raw = is_exit_bar | last_bar_of_day

# ============================================================
# SIGNAL CLEANING
# ============================================================

entries = ta.exrem(entries_raw.fillna(False), exits_raw.fillna(False))
exits = ta.exrem(exits_raw.fillna(False), entries_raw.fillna(False))

# Ensure exits come after entries (VectorBT handles this internally)
print(f"Entries: {entries.sum()}, Exits: {exits.sum()}")

# ============================================================
# ATR-BASED SL/TP (approximated as percentages)
# ============================================================

# For VectorBT, we use fixed SL/TP percentages
# Scalp mode: tight SL/TP for quick exits with zero brokerage
SL_PCT = 0.005    # 0.5% stop loss (tight scalp)
TP_PCT = 0.01     # 1.0% take profit (quick scalp)

# ============================================================
# BACKTEST
# ============================================================

print("Running backtest...")

pf = vbt.Portfolio.from_signals(
    close, entries, exits,
    sl_stop=SL_PCT,
    tp_stop=TP_PCT,
    init_cash=INIT_CASH,
    fees=FEES,
    fixed_fees=FIXED_FEES,
    size=ALLOCATION,
    size_type="percent",
    direction="longonly",
    min_size=1,
    size_granularity=1,
    freq="5min",
)

# ============================================================
# BENCHMARK (OpenAlgo API -> DuckDB -> yfinance)
# ============================================================

print("Loading benchmark...")

df_bench = None

# Try OpenAlgo API first
try:
    from openalgo import api as api_class
    print("  Trying OpenAlgo API for benchmark...")

    client_bench = api_class(
        api_key=os.getenv("OPEN_ALGO_API_KEY"),
        host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
    )

    df_bench_api = client_bench.history(
        symbol=BENCHMARK_SYMBOL, exchange=BENCHMARK_EXCHANGE, interval="D",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        source="api",
    )

    if isinstance(df_bench_api, pd.DataFrame) and not df_bench_api.empty:
        if "timestamp" in df_bench_api.columns:
            df_bench_api["timestamp"] = pd.to_datetime(df_bench_api["timestamp"])
            df_bench_api = df_bench_api.set_index("timestamp")
        else:
            df_bench_api.index = pd.to_datetime(df_bench_api.index)

        df_bench_api = df_bench_api.sort_index()
        if df_bench_api.index.tz is not None:
            df_bench_api.index = df_bench_api.index.tz_localize(None)

        df_bench = df_bench_api[["close"]]
        print(f"  Loaded benchmark from OpenAlgo API")
except Exception:
    pass

# Fallback to yfinance
if df_bench is None or df_bench.empty:
    try:
        import yfinance as yf
        print("  Fetching benchmark from yfinance...")
        yf_bench = f"{BENCHMARK_SYMBOL}.NS" if BENCHMARK_EXCHANGE == "NSE_INDEX" else BENCHMARK_SYMBOL
        df_bench_yf = yf.download(
            yf_bench,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
            multi_level_index=False,
        )
        if not df_bench_yf.empty:
            df_bench_yf.columns = df_bench_yf.columns.str.lower()
            df_bench_yf.index = pd.to_datetime(df_bench_yf.index)
            if df_bench_yf.index.tz is not None:
                df_bench_yf.index = df_bench_yf.index.tz_localize(None)
            df_bench = df_bench_yf[["close"]]
            print(f"  Loaded benchmark from yfinance")
    except Exception as e:
        print(f"  Warning: Could not load benchmark: {e}")

# Create benchmark portfolio if data available
pf_bench = None
if df_bench is not None and not df_bench.empty and "close" in df_bench.columns:
    bench_close = df_bench["close"].reindex(close.index).ffill().bfill()
    pf_bench = vbt.Portfolio.from_holding(bench_close, init_cash=INIT_CASH, fees=FEES, freq="5min")
else:
    print("  Warning: Benchmark data not available, skipping benchmark comparison")

# ============================================================
# RESULTS
# ============================================================

print("\n" + "=" * 60)
print("STRATEGY PERFORMANCE")
print("=" * 60)
print(pf.stats())

# Strategy vs Benchmark
print("\n" + "=" * 60)
print("STRATEGY vs BENCHMARK")
print("=" * 60)

strategy_metrics = [
    f"{pf.total_return() * 100:.2f}%",
    f"{pf.sharpe_ratio():.2f}",
    f"{pf.sortino_ratio():.2f}",
    f"{pf.max_drawdown() * 100:.2f}%",
    f"{pf.trades.win_rate() * 100:.1f}%",
    f"{pf.trades.count()}",
    f"{pf.trades.profit_factor():.2f}",
]

if pf_bench is not None:
    bench_metrics = [
        f"{pf_bench.total_return() * 100:.2f}%",
        f"{pf_bench.sharpe_ratio():.2f}",
        f"{pf_bench.sortino_ratio():.2f}",
        f"{pf_bench.max_drawdown() * 100:.2f}%",
        "-", "-", "-",
    ]
    comparison = pd.DataFrame({
        "Strategy": strategy_metrics,
        f"Benchmark ({BENCHMARK_SYMBOL})": bench_metrics,
    }, index=[
        "Total Return", "Sharpe Ratio", "Sortino Ratio",
        "Max Drawdown", "Win Rate", "Total Trades", "Profit Factor"
    ])
else:
    comparison = pd.DataFrame({
        "Strategy": strategy_metrics,
    }, index=[
        "Total Return", "Sharpe Ratio", "Sortino Ratio",
        "Max Drawdown", "Win Rate", "Total Trades", "Profit Factor"
    ])
print(comparison.to_string())

# ============================================================
# EXPLAIN
# ============================================================

print("\n" + "=" * 60)
print("EXPLANATION")
print("=" * 60)
if pf_bench is not None:
    print(f"* Total Return: {pf.total_return() * 100:.2f}% vs NIFTY {pf_bench.total_return() * 100:.2f}%")
else:
    print(f"* Total Return: {pf.total_return() * 100:.2f}%")
print(f"* Max Drawdown: {pf.max_drawdown() * 100:.2f}%")
print(f"  -> On Rs {INIT_CASH:,}, worst temporary loss = Rs {abs(pf.max_drawdown()) * INIT_CASH:,.0f}")
print(f"* Total Trades: {pf.trades.count()}")
print(f"* Win Rate: {pf.trades.win_rate() * 100:.1f}%")
print(f"* Profit Factor: {pf.trades.profit_factor():.2f}")

if pf.trades.count() > 0:
    avg_win = pf.trades.records_readable["Return"].mean()
    print(f"* Avg Trade Return: {avg_win * 100:.2f}%")

# ============================================================
# PLOT
# ============================================================

fig = pf.plot(
    subplots=["value", "underwater", "cum_returns"],
    template="plotly_dark",
    title=f"Quantix FOMO Momentum - {SYMBOL}",
)
fig.write_html(script_dir / f"{SYMBOL}_quantix_fomo.html")
print(f"\nChart saved: {script_dir / f'{SYMBOL}_quantix_fomo.html'}")

# ============================================================
# EXPORT TRADES
# ============================================================

if pf.trades.count() > 0:
    trades_df = pf.positions.records_readable
    trades_df.to_csv(script_dir / f"{SYMBOL}_quantix_fomo_trades.csv", index=False)
    print(f"Trades saved: {script_dir / f'{SYMBOL}_quantix_fomo_trades.csv'}")

print("\nDone!")
