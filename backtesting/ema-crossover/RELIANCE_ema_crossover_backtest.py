"""
EMA Crossover + RSI + ADX + Supertrend Backtest - VectorBT
Strategy: EMA 5/20 crossover with multi-filter confirmation.
Filters: RSI(14) < 70 for entries, ADX(14) > 20 for trend strength, Supertrend for direction.
Symbol: RELIANCE (NSE)
Interval: Daily
Fees: Indian delivery equity model (0.111% + Rs 20/order).
Benchmark: NIFTY 50 Index via yfinance.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import talib
import vectorbt as vbt
import yfinance as yf

# --- Config ---
script_dir = Path(__file__).resolve().parent

SYMBOL = "RELIANCE.NS"
EXCHANGE = "NSE"
INTERVAL = "D"
FAST_EMA = 5
SLOW_EMA = 20
INIT_CASH = 1_000_000
FEES = 0.00111              # 0.111% per side (Indian delivery: STT + statutory)
FIXED_FEES = 20             # Rs 20 per order (entry and exit)
ALLOCATION = 0.75
BENCHMARK_SYMBOL = "^NSEI"  # NIFTY 50 on yfinance

# Filter params
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
ADX_PERIOD = 14
ADX_THRESHOLD = 20
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0

# --- Fetch Data ---
end_date = datetime.now().date()
start_date = end_date - timedelta(days=365 * 3)

print(f"Fetching {SYMBOL} ({EXCHANGE}) {INTERVAL} data from {start_date} to {end_date}")

df = yf.download(SYMBOL, start=start_date, end=end_date, interval="1d", progress=False)

if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

df.index = pd.to_datetime(df.index)
df = df.sort_index()
if df.index.tz is not None:
    df.index = df.index.tz_convert(None)

high = df["High"]
low = df["Low"]
close = df["Close"]
print(f"Data loaded: {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")

# --- Indicators ---
# EMA
ema_fast = pd.Series(talib.EMA(close.values, timeperiod=FAST_EMA), index=close.index)
ema_slow = pd.Series(talib.EMA(close.values, timeperiod=SLOW_EMA), index=close.index)

# RSI
rsi = pd.Series(talib.RSI(close.values, timeperiod=RSI_PERIOD), index=close.index)

# ADX
adx = pd.Series(talib.ADX(high.values, low.values, close.values, timeperiod=ADX_PERIOD), index=close.index)

# Supertrend (built from ATR)
atr = pd.Series(talib.ATR(high.values, low.values, close.values, timeperiod=SUPERTREND_PERIOD), index=close.index)
hl2 = (high + low) / 2

upper_band = hl2 + SUPERTREND_MULTIPLIER * atr
lower_band = hl2 - SUPERTREND_MULTIPLIER * atr

supertrend = pd.Series(np.nan, index=close.index)
direction = pd.Series(1, index=close.index)  # 1 = up, -1 = down

for i in range(1, len(close)):
    if close.iloc[i] > upper_band.iloc[i - 1]:
        direction.iloc[i] = 1
    elif close.iloc[i] < lower_band.iloc[i - 1]:
        direction.iloc[i] = -1
    else:
        direction.iloc[i] = direction.iloc[i - 1]

    if direction.iloc[i] == 1:
        lower_band.iloc[i] = max(lower_band.iloc[i], lower_band.iloc[i - 1]) if direction.iloc[i - 1] == 1 else lower_band.iloc[i]
    else:
        upper_band.iloc[i] = min(upper_band.iloc[i], upper_band.iloc[i - 1]) if direction.iloc[i - 1] == -1 else upper_band.iloc[i]

supertrend = direction.copy()

# --- Signals ---
# Base EMA crossover
buy_raw = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
sell_raw = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

# Apply filters to entries
entries = buy_raw.copy()
# RSI: not overbought
entries = entries & (rsi < RSI_OVERBOUGHT)
# ADX: trend is strong enough
entries = entries & (adx > ADX_THRESHOLD)
# Supertrend: direction is up
entries = entries & (supertrend == 1)

# Apply filters to exits (only on state changes, not continuous conditions)
exits = sell_raw.copy()
# Exit if Supertrend flips from +1 to -1
st_flip_down = (supertrend == -1) & (supertrend.shift(1) == 1)
exits = exits | st_flip_down
# Exit if RSI crosses above overbought (take profit)
rsi_overbought_cross = (rsi > RSI_OVERBOUGHT) & (rsi.shift(1) <= RSI_OVERBOUGHT)
exits = exits | rsi_overbought_cross

# Clean signals
entries = entries.fillna(False).copy()
exits = exits.fillna(False).copy()
conflict = entries & exits
entries[conflict] = False
entries = entries & (~entries.shift(1).fillna(False))
exits = exits & (~exits.shift(1).fillna(False))

print(f"\nFilters applied:")
print(f"  RSI < {RSI_OVERBOUGHT} (not overbought)")
print(f"  ADX > {ADX_THRESHOLD} (strong trend)")
print(f"  Supertrend = bullish")
print(f"  Exits: EMA cross < OR Supertrend flip OR RSI > {RSI_OVERBOUGHT}")
print(f"\nSignals - Entries: {entries.sum()}, Exits: {exits.sum()}")

# --- Backtest ---
pf = vbt.Portfolio.from_signals(
    close, entries, exits,
    init_cash=INIT_CASH,
    size=ALLOCATION,
    size_type="percent",
    fees=FEES,
    fixed_fees=FIXED_FEES,
    direction="longonly",
    min_size=1,
    size_granularity=1,
    freq="1D",
)

# --- Benchmark: NIFTY 50 Index ---
print(f"\nFetching benchmark: {BENCHMARK_SYMBOL}")
df_bench = yf.download(BENCHMARK_SYMBOL, start=start_date, end=end_date, interval="1d", progress=False)
if isinstance(df_bench.columns, pd.MultiIndex):
    df_bench.columns = df_bench.columns.get_level_values(0)
df_bench.index = pd.to_datetime(df_bench.index)
df_bench = df_bench.sort_index()
if df_bench.index.tz is not None:
    df_bench.index = df_bench.index.tz_convert(None)

bench_close = df_bench["Close"].reindex(close.index).ffill().bfill()
pf_bench = vbt.Portfolio.from_holding(bench_close, init_cash=INIT_CASH, fees=FEES, freq="1D")

# --- Results ---
print("\n" + "=" * 60)
print(f"  EMA {FAST_EMA}/{SLOW_EMA} + RSI + ADX + Supertrend - {SYMBOL}")
print("=" * 60)
print(pf.stats())

# --- Strategy vs Benchmark Metrics ---
print("\n--- Strategy vs Benchmark ---")
comparison = pd.DataFrame({
    "Strategy": [
        f"{pf.total_return() * 100:.2f}%",
        f"{pf.sharpe_ratio():.2f}",
        f"{pf.sortino_ratio():.2f}",
        f"{pf.max_drawdown() * 100:.2f}%",
        f"{pf.trades.win_rate() * 100:.1f}%",
        f"{pf.trades.count()}",
        f"{pf.trades.profit_factor():.2f}",
    ],
    f"Benchmark (NIFTY 50)": [
        f"{pf_bench.total_return() * 100:.2f}%",
        f"{pf_bench.sharpe_ratio():.2f}",
        f"{pf_bench.sortino_ratio():.2f}",
        f"{pf_bench.max_drawdown() * 100:.2f}%",
        "-",
        "-",
        "-",
    ],
}, index=["Total Return", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown",
          "Win Rate", "Total Trades", "Profit Factor"])
print(comparison.to_string())

# --- Explain Backtest Report ---
print("\n--- Backtest Report Explanation ---")
print(f"* Total Return: Strategy returned {pf.total_return() * 100:.2f}% vs "
      f"benchmark {pf_bench.total_return() * 100:.2f}%")
alpha = pf.total_return() - pf_bench.total_return()
print(f"  -> Alpha (excess return): {alpha * 100:.2f}%")
print(f"* Sharpe Ratio: {pf.sharpe_ratio():.2f} "
      f"({'Good' if pf.sharpe_ratio() > 1 else 'Below 1 - needs improvement'})")
print(f"  -> Measures risk-adjusted return. >1 acceptable, >2 excellent.")
print(f"* Sortino Ratio: {pf.sortino_ratio():.2f} "
      f"({'Good' if pf.sortino_ratio() > 1.5 else 'Moderate'})")
print(f"  -> Like Sharpe but only penalizes downside volatility.")
print(f"* Max Drawdown: {pf.max_drawdown() * 100:.2f}%")
print(f"  -> Worst peak-to-trough decline.")
print(f"* Win Rate: {pf.trades.win_rate() * 100:.1f}%")
print(f"  -> Percentage of winning trades.")
print(f"* Profit Factor: {pf.trades.profit_factor():.2f} "
      f"({'Good' if pf.trades.profit_factor() > 1.5 else 'Marginal' if pf.trades.profit_factor() > 1 else 'Unprofitable'})")
print(f"  -> Gross profit / gross loss. >1.5 is good, >2 is excellent.")
print(f"* Total Trades: {pf.trades.count()}")
print(f"  -> {'Sufficient for statistical significance' if pf.trades.count() >= 30 else 'Too few trades - results unreliable'}")

# --- Plot ---
fig = pf.plot(
    subplots=["value", "underwater", "cum_returns"],
    template="plotly_dark",
    title=f"EMA {FAST_EMA}/{SLOW_EMA} + RSI + ADX + Supertrend - {SYMBOL}",
)
fig.show()

# --- Export Trades ---
trades_file = script_dir / f"{SYMBOL.split()[0]}_filtered_trades.csv"
pf.positions.records_readable.to_csv(trades_file, index=False)
print(f"\nTrades exported to {trades_file}")
