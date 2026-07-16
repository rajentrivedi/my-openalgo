import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from dotenv import find_dotenv, load_dotenv

script_dir = Path(__file__).resolve().parent
load_dotenv(find_dotenv(), override=False)

sys.path.insert(0, str(script_dir.parent.parent))

SYMBOL = "SBIN"
EXCHANGE = "NSE"
INTERVAL = "15m"
INIT_CASH = 500_000
FEES = 0.000225
FIXED_FEES = 20
ALLOCATION = 0.85
MIN_TRADES = 20

USE_OPENALGO = False
try:
    from openalgo import api, ta
    client = api(
        api_key=os.getenv("OPENALGO_API_KEY") or "",
        host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
    )
    test_df = client.history(
        symbol=SYMBOL, exchange=EXCHANGE, interval=INTERVAL,
        start_date="2025-01-01",
        end_date="2025-01-10",
    )
    if isinstance(test_df, pd.DataFrame) and not test_df.empty:
        USE_OPENALGO = True
    del test_df
except Exception:
    pass

import yfinance as yf
import talib

def ema(s, p): return pd.Series(talib.EMA(s.values.astype(float), p), index=s.index)
def sma(s, p): return pd.Series(talib.SMA(s.values.astype(float), p), index=s.index)
def rsi(s, p): return pd.Series(talib.RSI(s.values.astype(float), p), index=s.index)
def atr(h, l, c, p): return pd.Series(talib.ATR(h.values.astype(float), l.values.astype(float), c.values.astype(float), p), index=c.index)
def adx(h, l, c, p): return pd.Series(talib.ADX(h.values.astype(float), l.values.astype(float), c.values.astype(float), p), index=c.index)
def macd(c):
    m, s, h_ = talib.MACD(c.values.astype(float))
    return pd.Series(m, index=c.index), pd.Series(s, index=c.index), pd.Series(h_, index=c.index)
def stddev(s, p): return pd.Series(talib.STDDEV(s.values.astype(float), p), index=s.index)
def supertrender(h, l, c, period, mult):
    hl = (h + l) / 2
    tr_ = pd.concat([(h-l).abs(), (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    atr_ = tr_.rolling(period).mean()
    upper = hl + mult * atr_
    lower = hl - mult * atr_
    st = pd.Series(1, index=c.index)
    for i in range(1, len(c)):
        if c.iloc[i] > lower.iloc[i-1]: st.iloc[i] = 1
        elif c.iloc[i] < upper.iloc[i-1]: st.iloc[i] = -1
        else: st.iloc[i] = st.iloc[i-1]
    return st

def exrem(s1, s2):
    r = s1.copy()
    a = False
    for i in range(len(s1)):
        if a: r.iloc[i] = False
        if s1.iloc[i] and not a: a = True
        if s2.iloc[i]: a = False
    return r

print("Fetching data...")
end_date = datetime.now().date()
ybars = 252 * 4  # for daily
if INTERVAL != "D":
    # yfinance 15m data only available for last 60 days
    ybars = 60
start_date = end_date - timedelta(days=ybars)

results = {}

if USE_OPENALGO:
    client = api(
        api_key=os.getenv("OPENALGO_API_KEY"),
        host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
    )
    df = client.history(
        symbol=SYMBOL, exchange=EXCHANGE, interval=INTERVAL,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
    if isinstance(df, pd.DataFrame):
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        df = df.sort_index()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
else:
    ticker = yf.Ticker(f"{SYMBOL}.NS")
    df = ticker.history(start=start_date, end=end_date, interval=INTERVAL)
    if df.empty:
        # fallback to daily, wider range
        df = ticker.history(start=end_date - timedelta(days=365*4), end=end_date, interval="1d")
        print("15m data unavailable, using daily data instead")
    if not df.empty:
        df.columns = [c.lower() for c in df.columns]

if df.empty:
    print("ERROR: No data could be fetched. Exiting.")
    sys.exit(1)

print(f"Data: {len(df)} rows ({df.index[0].date()} to {df.index[-1].date()})")

# Detect actual frequency
bar_seconds = (df.index[1] - df.index[0]).total_seconds()
trade_freq = "15min"
if bar_seconds >= 82800:  # >23h = daily
    trade_freq = "D"
elif bar_seconds >= 3500:  # ~1h
    trade_freq = "1h"

h = df["high"]
l = df["low"]
c = df["close"]
o = df["open"]

results["Buy & Hold"] = vbt.Portfolio.from_holding(
    c, init_cash=INIT_CASH, fees=FEES, freq=trade_freq,
)

# --- Strategy 1: 3-EMA Crossover (3/6/9) ---
print("Testing: 3-EMA Crossover...")
e3 = ta.ema(c, 3) if USE_OPENALGO else ema(c, 3)
e6 = ta.ema(c, 6) if USE_OPENALGO else ema(c, 6)
e9 = ta.ema(c, 9) if USE_OPENALGO else ema(c, 9)
buy1 = (e3 > e6) & (e6 > e9) & ~((e3.shift(1) > e6.shift(1)) & (e6.shift(1) > e9.shift(1)))
sell1 = (e3 < e6) & (e6 < e9) & ~((e3.shift(1) < e6.shift(1)) & (e6.shift(1) < e9.shift(1)))
entries1 = ta.exrem(buy1.fillna(False), sell1.fillna(False)) if USE_OPENALGO else exrem(buy1.fillna(False), sell1.fillna(False))
exits1 = ta.exrem(sell1.fillna(False), buy1.fillna(False)) if USE_OPENALGO else exrem(sell1.fillna(False), buy1.fillna(False))
pf1 = vbt.Portfolio.from_signals(c, entries1, exits1, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["3-EMA Crossover"] = pf1

# --- Strategy 2: Kalman Filter Trend ---
print("Testing: Kalman Filter...")
atr14 = ta.atr(h, l, c, 14) if USE_OPENALGO else atr(h, l, c, 14)
atr_ma = atr14.rolling(50).mean().bfill()
V = (atr14 / atr_ma).fillna(1.0).ewm(span=5).mean().clip(0.5, 3.0)
price = c.values
x = np.zeros(2); x[0] = price[0]; x[1] = 0.0
P = np.eye(2) * 0.1
F = np.array([[1,1],[0,1]])
H = np.array([[1.0,0.0]])
mu = np.zeros(len(price)); vel = np.zeros(len(price))
R0 = max(1e-6, np.nanstd(np.diff(price))**2)
for t in range(len(price)):
    Q = np.diag([1e-4, 1e-6]) * V.iloc[t]
    R = R0 / V.iloc[t]
    x_pred = F @ x
    P_pred = F @ P @ F.T + Q
    z = price[t]; y = z - (H @ x_pred)[0]
    S = (H @ P_pred @ H.T)[0,0] + R
    K = (P_pred @ H.T).flatten() / S
    x = x_pred + K * y
    P = (np.eye(2) - np.outer(K, H[0])) @ P_pred
    mu[t] = x[0]; vel[t] = x[1]
kf_indicator = pd.Series(mu, index=c.index)
kf_vel = pd.Series(vel, index=c.index)
buy2 = (c > kf_indicator) & ~(c.shift(1) > kf_indicator.shift(1)) & (kf_vel > 0)
sell2 = (c < kf_indicator) | (kf_vel < 0)
entries2 = ta.exrem(buy2.fillna(False), sell2.fillna(False)) if USE_OPENALGO else exrem(buy2.fillna(False), sell2.fillna(False))
exits2 = ta.exrem(sell2.fillna(False), buy2.fillna(False)) if USE_OPENALGO else exrem(sell2.fillna(False), buy2.fillna(False))
pf2 = vbt.Portfolio.from_signals(c, entries2, exits2, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["Kalman Filter"] = pf2

# --- Strategy 3: V-ATR Breakout ---
print("Testing: V-ATR Breakout...")
atr14_3 = ta.atr(h, l, c, 14) if USE_OPENALGO else atr(h, l, c, 14)
atr_ma3 = atr14_3.rolling(50).mean().bfill()
V3 = (atr14_3 / atr_ma3).fillna(1.0)
mb3 = (1.0 * (1 + 1.0 * (V3 - 1.0))).clip(0.3, 5.0)
break_high = h.rolling(20).max()
indicator3 = break_high + mb3 * atr14_3
vatr_trail = c - 1.5 * (1 + 1.0 * (V3 - 1.0)) * atr14_3
buy3 = (c > indicator3) & ~(c.shift(1) > indicator3.shift(1))
sell3 = c < vatr_trail
entries3 = ta.exrem(buy3.fillna(False), sell3.fillna(False)) if USE_OPENALGO else exrem(buy3.fillna(False), sell3.fillna(False))
exits3 = ta.exrem(sell3.fillna(False), buy3.fillna(False)) if USE_OPENALGO else exrem(sell3.fillna(False), buy3.fillna(False))
pf3 = vbt.Portfolio.from_signals(c, entries3, exits3, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["V-ATR Breakout"] = pf3

# --- Strategy 4: Regime Strategy ---
print("Testing: Regime Strategy...")
atr_s = ta.atr(h, l, c, 14) if USE_OPENALGO else atr(h, l, c, 14)
atr_l = ta.atr(h, l, c, 50) if USE_OPENALGO else atr(h, l, c, 50)
V4 = (atr_s / atr_l).fillna(1.0)
ema_s = ta.ema(c, 12) if USE_OPENALGO else ema(c, 12)
ema_l = ta.ema(c, 26) if USE_OPENALGO else ema(c, 26)
momentum4 = ema_s - ema_l
mom_norm = momentum4 / (atr_s + 1e-12)
regime = pd.Series(index=c.index, dtype=str)
for v, m in zip(V4, mom_norm.abs()):
    pass
is_trend = V4 < 0.9
is_uptrend = momentum4 > 0
buy4 = (c > ema_s) & is_trend & is_uptrend & ~((c.shift(1) > ema_s.shift(1)) & is_trend.shift(1) & is_uptrend.shift(1))
sell4 = (c < ema_s) | (momentum4 < 0)
entries4 = ta.exrem(buy4.fillna(False), sell4.fillna(False)) if USE_OPENALGO else exrem(buy4.fillna(False), sell4.fillna(False))
exits4 = ta.exrem(sell4.fillna(False), buy4.fillna(False)) if USE_OPENALGO else exrem(sell4.fillna(False), buy4.fillna(False))
pf4 = vbt.Portfolio.from_signals(c, entries4, exits4, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["Regime Strategy"] = pf4

# --- Strategy 5: Double Supertrend (10/3 + 7/2) ---
print("Testing: Double Supertrend...")
st1 = ta.supertrend(h, l, c, 10, 3) if USE_OPENALGO else supertrender(h, l, c, 10, 3)
st2 = ta.supertrend(h, l, c, 7, 2) if USE_OPENALGO else supertrender(h, l, c, 7, 2)
if isinstance(st1, pd.DataFrame): st1 = st1.iloc[:, 0]
if isinstance(st2, pd.DataFrame): st2 = st2.iloc[:, 0]
buy5 = (st1 == 1) & (st2 == 1) & ~((st1.shift(1) == 1) & (st2.shift(1) == 1))
sell5 = (st1 == -1) | (st2 == -1)
entries5 = ta.exrem(buy5.fillna(False), sell5.fillna(False)) if USE_OPENALGO else exrem(buy5.fillna(False), sell5.fillna(False))
exits5 = ta.exrem(sell5.fillna(False), buy5.fillna(False)) if USE_OPENALGO else exrem(sell5.fillna(False), buy5.fillna(False))
pf5 = vbt.Portfolio.from_signals(c, entries5, exits5, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["Double Supertrend"] = pf5

# --- Strategy 6: ADX + StochRSI ---
print("Testing: ADX + StochRSI...")
rsi6 = ta.rsi(c, 9) if USE_OPENALGO else rsi(c, 9)
stoch_k = rsi6.rolling(9).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min()) * 100 if x.max() - x.min() != 0 else 0)
stoch_d = stoch_k.rolling(3).mean()
adx6 = ta.adx(h, l, c, 14) if USE_OPENALGO else adx(h, l, c, 14)
buy6 = (stoch_k > stoch_d) & ~(stoch_k.shift(1) > stoch_d.shift(1)) & (adx6 > 25)
sell6 = (stoch_k < stoch_d) & ~(stoch_k.shift(1) < stoch_d.shift(1))
entries6 = ta.exrem(buy6.fillna(False), sell6.fillna(False)) if USE_OPENALGO else exrem(buy6.fillna(False), sell6.fillna(False))
exits6 = ta.exrem(sell6.fillna(False), buy6.fillna(False)) if USE_OPENALGO else exrem(sell6.fillna(False), buy6.fillna(False))
pf6 = vbt.Portfolio.from_signals(c, entries6, exits6, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["ADX+StochRSI"] = pf6

# --- Strategy 7: MACD + KNN-inspired (EMA + RSI combo) ---
print("Testing: MACD Momentum...")
macd_line = ta.macd(c, 12, 26) if USE_OPENALGO else macd(c)
if isinstance(macd_line, tuple): macd_line = macd_line[0]
macd_signal_7 = ta.macd_signal(c, 12, 26, 9) if USE_OPENALGO else macd(c)[1]
macd_hist_7 = macd_line - macd_signal_7
rsi7 = ta.rsi(c, 14) if USE_OPENALGO else rsi(c, 14)
buy7 = (macd_hist_7 > 0) & (macd_hist_7.shift(1) <= 0) & (rsi7 > 50)
sell7 = (macd_hist_7 < 0) & (macd_hist_7.shift(1) >= 0) | (rsi7 < 40)
entries7 = ta.exrem(buy7.fillna(False), sell7.fillna(False)) if USE_OPENALGO else exrem(buy7.fillna(False), sell7.fillna(False))
exits7 = ta.exrem(sell7.fillna(False), buy7.fillna(False)) if USE_OPENALGO else exrem(sell7.fillna(False), buy7.fillna(False))
pf7 = vbt.Portfolio.from_signals(c, entries7, exits7, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["MACD Momentum"] = pf7

# --- Strategy 8: Bollinger Band Squeeze (TTM-like) ---
print("Testing: BB Squeeze Momentum...")
bb_mid = ta.sma(c, 20) if USE_OPENALGO else sma(c, 20)
bb_std = ta.stddev(c, 20) if USE_OPENALGO else stddev(c, 20)
bb_upper = bb_mid + 2 * bb_std
bb_lower = bb_mid - 2 * bb_std
kc_mid = ta.sma(c, 20) if USE_OPENALGO else sma(c, 20)
kc_range = ta.atr(h, l, c, 10) if USE_OPENALGO else atr(h, l, c, 10)
kc_upper = kc_mid + 1.5 * kc_range
kc_lower = kc_mid - 1.5 * kc_range
squeeze_on = ((bb_lower > kc_lower) & (bb_upper < kc_upper)).fillna(False)
mom8 = c - c.shift(5)
buy8 = ~squeeze_on.shift(1).fillna(False) & squeeze_on & (mom8 > 0)
sell8 = ~squeeze_on
entries8 = ta.exrem(buy8.fillna(False), sell8.fillna(False)) if USE_OPENALGO else exrem(buy8.fillna(False), sell8.fillna(False))
exits8 = ta.exrem(sell8.fillna(False), buy8.fillna(False)) if USE_OPENALGO else exrem(sell8.fillna(False), buy8.fillna(False))
pf8 = vbt.Portfolio.from_signals(c, entries8, exits8, init_cash=INIT_CASH, size=ALLOCATION, size_type="percent",
    fees=FEES, fixed_fees=FIXED_FEES, direction="longonly", min_size=1, size_granularity=1, freq=trade_freq)
results["BB Squeeze"] = pf8

# --- Compile Results ---
print("\n" + "=" * 100)
print("INTRADAY STRATEGY COMPARISON — SBIN 15min")
print(f"Period: {df.index[0].date()} to {df.index[-1].date()}  |  {len(df)} bars")
print("=" * 100)

comp_data = {}
for name, pf in results.items():
    trades = pf.trades.count()
    comp_data[name] = {
        "Total Return %": f"{pf.total_return() * 100:.2f}",
        "Sharpe": f"{pf.sharpe_ratio():.2f}",
        "Sortino": f"{pf.sortino_ratio():.2f}",
        "Max DD %": f"{pf.max_drawdown() * 100:.2f}",
        "Win Rate %": f"{pf.trades.win_rate() * 100:.1f}" if trades > 0 else "N/A",
        "Trades": trades,
        "Profit Factor": f"{pf.trades.profit_factor():.2f}" if trades > 0 else "N/A",
    }

df_results = pd.DataFrame(comp_data).T
print("\n" + df_results.to_string())
print()

# Sort by Sharpe ratio
df_sorted = df_results.sort_values("Sharpe", ascending=False)
print("\n" + "-" * 100)
print("RANKING BY SHARPE RATIO (higher = better risk-adjusted returns)")
print("-" * 100)
ranked = df_sorted[["Total Return %", "Sharpe", "Max DD %", "Win Rate %", "Trades", "Profit Factor"]]
print(ranked.to_string())

# Top 3 summary
print("\n" + "=" * 100)
print("TOP 3 STRATEGIES")
print("=" * 100)
for i, (name, row) in enumerate(df_sorted.head(3).iterrows()):
    print(f"\n{i+1}. {name}")
    print(f"   Return: {row['Total Return %']}% | Sharpe: {row['Sharpe']} | Max DD: {row['Max DD %']}%")
    print(f"   Win Rate: {row['Win Rate %']} | Trades: {row['Trades']} | Profit Factor: {row['Profit Factor']}")

best_name = df_sorted.index[0]
best_pf = results[best_name]
fig = best_pf.plot(subplots=['value', 'underwater', 'cum_returns', 'trades'], template="plotly_dark")
fig.update_layout(title=f"Best Strategy: {best_name} — SBIN 15min Intraday")
fig.write_html(str(script_dir / "intraday_comparison.html"))
print(f"\nChart saved: {script_dir / 'intraday_comparison.html'}")

# Export trades
for name, pf in results.items():
    if pf.trades.count() > 0:
        pf.positions.records_readable.to_csv(script_dir / f"{name.lower().replace(' ', '_').replace('+', '_')}_trades.csv", index=False)

print("\nDone. All trade CSVs saved.")
