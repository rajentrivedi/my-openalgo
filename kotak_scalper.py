import time
from openalgo import OpenAlgoClient

# --- CONFIGURATION ---
API_KEY = "your_openalgo_api_key"
SYMBOLS = ["NSE:RELIANCE", "NSE:SBIN", "NSE:HDFCBANK"] # Your 3 symbols
QUANTITY = 10            
TARGET_PERCENT = 0.005    # 0.5% profit
STOPLOSS_PERCENT = 0.002  # 0.2% stop loss
TIMEFRAME = "1minute"

# Track active positions to avoid double-entry
active_positions = {symbol: False for symbol in SYMBOLS}

client = OpenAlgoClient(api_key=API_KEY)

def get_signal(symbol):
    data = client.get_historical_data(symbol=symbol, interval=TIMEFRAME, limit=50)
    
    current_price = data['close'][-1]
    ema_20 = client.ta.ema(data['close'], period=20)[-1]
    rsi_14 = client.ta.rsi(data['close'], period=14)[-1]
    
    if current_price > ema_20 and rsi_14 < 35:
        return "BUY"
    elif current_price < ema_20 and rsi_14 > 65:
        return "SELL"
    return "HOLD"

def place_scalp_order(symbol, side):
    price = client.get_last_price(symbol)
    
    if side == "BUY":
        target = price * (1 + TARGET_PERCENT)
        sl = price * (1 - STOPLOSS_PERCENT)
        action = "BUY"
        exit_action = "SELL"
    else:
        target = price * (1 - TARGET_PERCENT)
        sl = price * (1 + STOPLOSS_PERCENT)
        action = "SELL"
        exit_action = "BUY"

    print(f"[{symbol}] Executing {side} | Target: {target:.2f} | SL: {sl:.2f}")
    
    # 1. Entry Order
    client.place_order(symbol=symbol, action=action, quantity=QUANTITY, order_type="MARKET", product="MIS")
    
    # 2. Target Order
    client.place_order(symbol=symbol, action=exit_action, quantity=QUANTITY, order_type="LIMIT", price=target, product="MIS")
    
    # 3. Stop Loss Order
    client.place_order(symbol=symbol, action=exit_action, quantity=QUANTITY, order_type="SL", price=sl, product="MIS")

# --- MAIN LOOP ---
print(f"Scalping Bot Started for {SYMBOLS}...")
while True:
    try:
        for symbol in SYMBOLS:
            # Only look for signals if we don't have an active trade for this symbol
            if not active_positions[symbol]:
                signal = get_signal(symbol)
                if signal != "HOLD":
                    place_scalp_order(symbol, signal)
                    active_positions[symbol] = True
                    print(f"Position opened for {symbol}. Monitoring for exit...")
            else:
                # Check if position is closed
                pass

        time.sleep(30) # Scan all symbols every 30 seconds
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(60)
