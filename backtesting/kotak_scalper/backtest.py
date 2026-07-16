import vectorbt as vbt
import pandas as pd
import numpy as np
from openalgo import ta
from openalgo import OpenAlgoClient

# --- CONFIGURATION ---
API_KEY = "your_openalgo_api_key"
SYMBOLS = ["NSE:RELIANCE", "NSE:SBIN", "NSE:HDFCBANK"]
TIMEFRAME = "1minute"
EMA_PERIOD = 20
RSI_PERIOD = 14
TARGET_PERCENT = 0.005
STOPLOSS_PERCENT = 0.002

client = OpenAlgoClient(api_key=API_KEY)

def run_backtest(symbol):
    print(f"Backtesting {symbol}...")
    # Fetch historical data
    data = client.get_historical_data(symbol=symbol, interval=TIMEFRAME, limit=5000)
    df = pd.DataFrame(data)
    close = df['close']

    # Indicators using openalgo.ta
    ema = ta.ema(close, period=EMA_PERIOD)
    rsi = ta.rsi(close, period=RSI_PERIOD)

    # Entry Signals
    # BUY if Price > EMA and RSI < 35
    entries = (close > ema) & (rsi < 35)
    # SELL if Price < EMA and RSI > 65
    shorts = (close < ema) & (rsi > 65)

    # VectorBT Portfolio
    # We use a simplified SL/TP approach
    pf = vbt.Portfolio.from_signals(
        close, 
        entries=entries, 
        exits=None, 
        short_entries=shorts, 
        short_exits=None,
        sl_stop=STOPLOSS_PERCENT, 
        tp_stop=TARGET_PERCENT,
        fees=0.0 # Zero brokerage as requested
    )
    
    return pf.stats()

if __name__ == "__main__":
    results = {}
    for sym in SYMBOLS:
        try:
            results[sym] = run_backtest(sym)
        except Exception as e:
            print(f"Error backtesting {sym}: {e}")
    
    for sym, stats in results.items():
        print(f"\n--- Stats for {sym} ---")
        print(stats)
