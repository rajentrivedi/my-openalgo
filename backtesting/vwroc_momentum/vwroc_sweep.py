"""
Parameter sweep for the VWROC momentum strategy.

Scans lookback window x entry/exit (hysteresis) thresholds x allocation
to find robust configurations. Uses yfinance daily data for a symbol.

Usage:
    venv/bin/python backtesting/vwroc_momentum/vwroc_sweep.py
    venv/bin/python backtesting/vwroc_momentum/vwroc_sweep.py --symbol SBIN.NS
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

import yfinance as yf

parser = argparse.ArgumentParser(description="VWROC parameter sweep")
parser.add_argument("--symbol", default="RELIANCE.NS")
parser.add_argument("--years", type=int, default=5)
args = parser.parse_args()

SYMBOL = args.symbol
YEARS = args.years
INIT_CASH = 1_000_000
FEES = 0.00111
FIXED_FEES = 20
ALLOCATIONS = [0.35, 0.5, 0.75]

LOOKBACKS = [15, 20, 30, 50]
ENTRY_THRESHOLDS = [0.5, 0.8, 1.2]
EXIT_THRESHOLDS = [-1.5, -1.0, -0.5]

end_date = datetime.now().date()
start_date = end_date - timedelta(days=365 * YEARS)

print(f"Sweeping VWROC on {SYMBOL}, {YEARS}y daily...")
df = yf.download(SYMBOL, start=start_date, end=end_date, interval="1d", progress=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
df.index = pd.to_datetime(df.index)
df = df.sort_index()
if df.index.tz is not None:
    df.index = df.index.tz_convert(None)
close = df["Close"]
volume = df["Volume"]
print(f"Bars: {len(df)} ({df.index[0].date()} to {df.index[-1].date()})")

price_chg = close.diff()

rows = []
for lb in LOOKBACKS:
    vwroc = (price_chg * volume).rolling(lb).sum() / volume.rolling(lb).sum() / close * 100.0
    for en in ENTRY_THRESHOLDS:
        for ex in EXIT_THRESHOLDS:
            if ex >= en:
                continue
            buy_raw = (vwroc > en) & (vwroc.shift(1) <= en)
            sell_raw = (vwroc < ex) & (vwroc.shift(1) >= ex)
            entries = buy_raw.fillna(False).copy()
            exits = sell_raw.fillna(False).copy()
            active = False
            for i in range(len(entries)):
                if active:
                    entries.iloc[i] = False
                if entries.iloc[i] and not active:
                    active = True
                if exits.iloc[i]:
                    active = False
            for alloc in ALLOCATIONS:
                pf = vbt.Portfolio.from_signals(
                    close, entries, exits,
                    init_cash=INIT_CASH,
                    size=alloc, size_type="percent",
                    fees=FEES, fixed_fees=FIXED_FEES,
                    direction="longonly",
                    min_size=1, size_granularity=1, freq="1D",
                )
                rows.append({
                    "lookback": lb,
                    "entry": en,
                    "exit": ex,
                    "alloc": alloc,
                    "total_return": pf.total_return(),
                    "sharpe": pf.sharpe_ratio(),
                    "max_dd": pf.max_drawdown(),
                    "win_rate": pf.trades.win_rate(),
                    "trades": pf.trades.count(),
                    "profit_factor": pf.trades.profit_factor(),
                    "expectancy": pf.trades.expectancy(),
                })

sweep = pd.DataFrame(rows)
sweep["calmar"] = sweep["total_return"] / -sweep["max_dd"].replace(0, np.nan)
sweep = sweep.sort_values("sharpe", ascending=False)

out = script_dir = Path(__file__).resolve().parent / f"{SYMBOL.split('.')[0]}_vwroc_sweep.csv"
sweep.to_csv(out, index=False)

active = sweep[sweep["trades"] >= 8].copy()
if active.empty:
    active = sweep[sweep["trades"] >= 1].copy()
active = active.sort_values("total_return", ascending=False)

print(f"\nValid configs (trades >= 8): {len(active)} of {len(sweep)}\nTop 15 by total return:\n")
top = active.head(15)[["lookback", "entry", "exit", "alloc", "total_return", "sharpe", "max_dd", "win_rate", "trades", "profit_factor", "calmar"]]
top["total_return"] = (top["total_return"] * 100).round(1).astype(str) + "%"
top["max_dd"] = (top["max_dd"] * 100).round(1).astype(str) + "%"
top["win_rate"] = (top["win_rate"] * 100).round(1).astype(str) + "%"
print(top.to_string(index=False))

print(f"\nBest by Calmar (risk-adjusted, trades >= 8):\n")
calmar_top = active.sort_values("calmar", ascending=False).head(10)
calmar_top = calmar_top[["lookback", "entry", "exit", "alloc", "total_return", "sharpe", "max_dd", "win_rate", "trades", "profit_factor", "calmar"]]
for _, r in calmar_top.iterrows():
    print(f"  lb={int(r.lookback):>3} entry={r.entry:>4} exit={r.exit:>5} alloc={r.alloc:.2f}  "
          f"return={r.total_return*100:>7.1f}%  sharpe={r.sharpe:>5.2f}  maxDD={r.max_dd*100:>6.1f}%  "
          f"win={r.win_rate*100:>4.0f}%  trades={int(r.trades):>3}  PF={r.profit_factor:>4.2f}  calmar={r.calmar:>6.2f}")

print(f"\nFull sweep saved to {out}")