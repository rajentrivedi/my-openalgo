"""
Volume-Weighted Rate of Change (VWROC) Momentum Backtest - VectorBT

Strategy: Buy/sell NIFTY stocks when their volume-weighted momentum turns positive.

VWROC(t) = sum((close[i] - close[i-1]) * volume[i] for i in window) / sum(volume[i] for i in window)

- Weights each day's price change by that day's traded volume: an up-move on
  heavy volume contributes far more to momentum than an up-move on thin volume.
- Positive VWROC = the market is pushing the stock up with real volume behind it.
- Negative VWROC = sellers are dominating, volume-weighted.

Rule:
- Entry when VWROC crosses above the entry threshold (buy the volume-backed momentum).
- Exit when VWROC falls back below the exit threshold.
- Long-only, daily bars.

Symbol: RELIANCE (NSE)
Interval: Daily
Fees: Indian delivery equity model (0.111% + Rs 20/order).
Benchmark: NIFTY 50 Index via yfinance (^NSEI).

Usage:
    venv/bin/python backtesting/vwroc_momentum/vwroc_momentum_backtest.py
    venv/bin/python backtesting/vwroc_momentum/vwroc_momentum_backtest.py --lookback 20 --entry 1.0 --exit -1.0
    venv/bin/python backtesting/vwroc_momentum/vwroc_momentum_backtest.py --symbol SBIN.NS --lookback 30
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import vectorbt as vbt
from plotly.subplots import make_subplots

try:
    from openalgo import ta
    exrem = ta.exrem
except ImportError:
    def exrem(signal1, signal2):
        result = signal1.copy()
        active = False
        for i in range(len(signal1)):
            if active:
                result.iloc[i] = False
            if signal1.iloc[i] and not active:
                active = True
            if signal2.iloc[i]:
                active = False
        return result

import yfinance as yf

# --- Config ---
script_dir = Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description="VWROC momentum backtest")
parser.add_argument("--symbol", default="SBIN.NS", help="yfinance symbol, e.g. RELIANCE.NS, SBIN.NS")
parser.add_argument("--lookback", type=int, default=15, help="VWROC lookback window in trading days")
parser.add_argument("--entry", type=float, default=0.5, help="entry threshold: VWROC must cross ABOVE this %%")
parser.add_argument("--exit", type=float, default=-1.0, help="exit threshold: VWROC must cross BELOW this %% (hysteresis)")
parser.add_argument("--years", type=int, default=5, help="years of history to backtest")
parser.add_argument("--allocation", type=float, default=0.75, help="fraction of cash deployed per trade")
args = parser.parse_args()

SYMBOL = args.symbol
LOOKBACK = args.lookback
ENTRY_THRESHOLD = args.entry        # enter when VWROC crosses above this (percent)
EXIT_THRESHOLD = args.exit          # exit when VWROC crosses below this (percent)
YEARS = args.years
ALLOCATION = args.allocation

EXCHANGE = "NSE"
INTERVAL = "D"
INIT_CASH = 1_000_000
FEES = 0.00111                # 0.111% per side (Indian delivery: STT + statutory)
FIXED_FEES = 20               # Rs 20 per order (entry and exit)
BENCHMARK_SYMBOL = "^NSEI"    # NIFTY 50 on yfinance

# --- Fetch Data ---
end_date = datetime.now().date()
start_date = end_date - timedelta(days=365 * YEARS)

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
volume = df["Volume"]
print(f"Data loaded: {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")

# --- Indicator: Volume-Weighted Rate of Change ---
# VWROC(t) = sum((close[i] - close[i-1]) * volume[i]) / sum(volume[i]) over the lookback window
# Normalised to % of the current price so thresholds are scale-free across symbols.
price_chg = close.diff()
vwroc_raw = (price_chg * volume).rolling(LOOKBACK).sum() / volume.rolling(LOOKBACK).sum()
vwroc = vwroc_raw / close * 100.0

# --- Signals ---
# Entry: VWROC crosses above entry threshold
buy_raw = (vwroc > ENTRY_THRESHOLD) & (vwroc.shift(1) <= ENTRY_THRESHOLD)
# Exit: VWROC crosses below exit threshold
sell_raw = (vwroc < EXIT_THRESHOLD) & (vwroc.shift(1) >= EXIT_THRESHOLD)

# Clean signals (remove duplicates / conflicting)
entries = exrem(buy_raw.fillna(False), sell_raw.fillna(False))
exits = exrem(sell_raw.fillna(False), buy_raw.fillna(False))

print(f"\nStrategy:")
print(f"  VWROC lookback: {LOOKBACK} days")
print(f"  Entry: VWROC crosses above {ENTRY_THRESHOLD}%")
print(f"  Exit: VWROC crosses below {EXIT_THRESHOLD}%")
print(f"  Signals - Entries: {int(entries.sum())}, Exits: {int(exits.sum())}")

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
print(f"  VWROC Momentum ({LOOKBACK}d) - {SYMBOL}")
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
    "Benchmark (NIFTY 50)": [
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
    title=f"VWROC Momentum (lookback {LOOKBACK}) - {SYMBOL}",
)
chart_file = script_dir / f"{SYMBOL.split('.')[0]}_vwroc_momentum.html"
fig.write_html(str(chart_file), include_plotlyjs="cdn")
print(f"\nChart saved to {chart_file}")

# --- Export Trades ---
trades_file = script_dir / f"{SYMBOL.split('.')[0]}_vwroc_trades.csv"
pf.positions.records_readable.to_csv(trades_file, index=False)
print(f"Trades exported to {trades_file}")