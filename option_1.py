from openalgo import api
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import logging
import json
import os

# ============================================================
# PAPER TRADING CONFIGURATION
# ============================================================

# Enable paper trading mode
PAPER_TRADING = True
TRADE_LOG_FILE = "paper_trading_log.json"

# OpenAlgo Configuration
API_KEY = "f7c106276c5424d3e7f077d08fee157acedb9d460bea00f3b53fc5e5d3799360"  # Use paper trading API key
API_HOST = "http://127.0.0.1:5000"

# Strategy Configuration
STRATEGY_NAME = "Nifty_Option_Short_Strangle_Paper"

# Underlying Configuration
SYMBOL = "NIFTY"
EXCHANGE = "NSE_INDEX"
TIMEFRAME = "1m"

# Option Trading Configuration
OPTION_EXCHANGE = "NSE_OPT"
PRODUCT = "MIS"
QUANTITY = 75  # Paper trading quantity

# Strategy Parameters
OTM_OFFSET = 6
STRIKE_STEP = 50

# Exit Parameters
PROFIT_TARGET_PERCENT = 50
STOPLOSS_PERCENT = 100

# Time Parameters
ENTRY_START = "09:30"
ENTRY_CUTOFF = "15:10"
SQUARE_OFF_TIME = "15:20"

# Risk Management
CHECK_INTERVAL = 30
MAX_POSITION_SIZE = 1

# ============================================================
# LOGGING SETUP
# ============================================================

logging.basicConfig(
    level=logging.DEBUG,  # More verbose for paper trading
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('paper_trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# PAPER TRADING LOGGER
# ============================================================

class PaperTradeLogger:
    def __init__(self, log_file=TRADE_LOG_FILE):
        self.log_file = log_file
        self.trades = []
        self.load_trades()
    
    def load_trades(self):
        """Load existing trades from file"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    self.trades = json.load(f)
            except:
                self.trades = []
        else:
            self.trades = []
    
    def save_trades(self):
        """Save trades to file"""
        with open(self.log_file, 'w') as f:
            json.dump(self.trades, f, indent=2, default=str)
    
    def log_trade(self, trade_data):
        """Log a trade"""
        trade_data['timestamp'] = datetime.now().isoformat()
        self.trades.append(trade_data)
        self.save_trades()
        logger.info(f"📝 Trade logged: {trade_data}")
    
    def get_summary(self):
        """Get trade summary statistics"""
        if not self.trades:
            return "No trades yet"
        
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in self.trades if t.get('pnl', 0) < 0]
        
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
        
        summary = f"""
        📊 PAPER TRADING SUMMARY
        =========================
        Total Trades: {total_trades}
        Winning Trades: {len(winning_trades)}
        Losing Trades: {len(losing_trades)}
        Win Rate: {win_rate:.2f}%
        Total P&L: {sum([t.get('pnl', 0) for t in self.trades]):.2f}
        Max Profit: {max([t.get('pnl', 0) for t in self.trades]) if self.trades else 0:.2f}
        Max Loss: {min([t.get('pnl', 0) for t in self.trades]) if self.trades else 0:.2f}
        """
        
        return summary

# ============================================================
# PAPER TRADING EXECUTION
# ============================================================

class PaperTradingExecutor:
    """Simulates order execution without actually placing trades"""
    
    def __init__(self):
        self.logger = PaperTradeLogger()
        self.pending_orders = []
        self.filled_orders = []
        
    def place_order(self, symbol, action, quantity, price_type="MARKET", price=0):
        """Simulate order placement"""
        order_id = f"PAPER_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        order = {
            'order_id': order_id,
            'symbol': symbol,
            'action': action,
            'quantity': quantity,
            'price_type': price_type,
            'price': price,
            'status': 'PENDING',
            'timestamp': datetime.now().isoformat(),
            'executed': False
        }
        
        self.pending_orders.append(order)
        
        # Simulate immediate execution for paper trading
        if PAPER_TRADING:
            order['status'] = 'EXECUTED'
            order['executed_at'] = datetime.now().isoformat()
            self.filled_orders.append(order)
            logger.info(f"🖥️ PAPER ORDER: {action} {quantity} of {symbol} at {price_type}")
        
        return order
    
    def get_positions(self):
        """Get current simulated positions"""
        positions = {}
        for order in self.filled_orders:
            if order['executed']:
                symbol = order['symbol']
                if symbol not in positions:
                    positions[symbol] = {'quantity': 0, 'avg_price': 0}
                
                qty = order['quantity'] if order['action'] == 'BUY' else -order['quantity']
                positions[symbol]['quantity'] += qty
                # Use market price for simplicity
                positions[symbol]['avg_price'] = order.get('price', 0)
        
        return positions

# ============================================================
# ENHANCED PAPER TRADING STRATEGY
# ============================================================

class PaperOptionSellingStrategy:
    def __init__(self):
        self.position = 0
        self.entry_price = None
        self.current_price = None
        self.total_premium = 0
        self.call_symbol = None
        self.put_symbol = None
        self.call_strike = None
        self.put_strike = None
        self.entry_time = None
        self.is_expiry_week = False
        self.last_processed_time = None
        self.running = True
        self.paper_executor = PaperTradingExecutor()
        self.performance_data = []
        
        # Track premiums
        self.call_premium = 0
        self.put_premium = 0
        
        # Historical tracking
        self.historical_prices = []
        self.pnl_history = []
        
        logger.info("=" * 60)
        logger.info("📈 PAPER TRADING STRATEGY")
        logger.info(f"Strategy: {STRATEGY_NAME}")
        logger.info(f"Underlying: {SYMBOL}")
        logger.info(f"OTM Offset: {OTM_OFFSET} strikes")
        logger.info(f"Profit Target: {PROFIT_TARGET_PERCENT}%")
        logger.info(f"Stop-loss: {STOPLOSS_PERCENT}%")
        logger.info("=" * 60)
        
        # Start performance tracking
        self.start_performance_tracking()
    
    def start_performance_tracking(self):
        """Initialize performance tracking"""
        self.performance_data.append({
            'start_time': datetime.now().isoformat(),
            'strategy': STRATEGY_NAME,
            'paper_mode': PAPER_TRADING,
            'trades': []
        })

# ============================================================
# HELPER FUNCTIONS (SAME AS BEFORE)
# ============================================================

def get_current_time():
    return datetime.now().strftime("%H:%M")

def get_current_date():
    return datetime.now().strftime("%Y-%m-%d")

def get_nifty_expiry_dates():
    current_date = datetime.now()
    days_until_thursday = (3 - current_date.weekday()) % 7
    if days_until_thursday == 0:
        days_until_thursday = 7
    next_thursday = current_date + timedelta(days=days_until_thursday)
    days_to_expiry = (next_thursday - current_date).days
    return next_thursday, days_to_expiry

def get_spot_price():
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        df = client.history(
            symbol=SYMBOL,
            exchange=EXCHANGE,
            interval=TIMEFRAME,
            start_date=start_date,
            end_date=end_date
        )
        
        if df is not None and not df.empty:
            return float(df['close'].iloc[-1])
        else:
            logger.warning("Could not get spot price")
            return None
            
    except Exception as e:
        logger.error(f"Error getting spot price: {e}")
        return None

def get_otm_strike(spot_price, offset, option_type):
    base_strike = round(spot_price / STRIKE_STEP) * STRIKE_STEP
    
    if option_type == "CE":
        strike = base_strike + (offset * STRIKE_STEP)
    else:
        strike = base_strike - (offset * STRIKE_STEP)
    
    return strike

def get_current_expiry():
    current_date = datetime.now()
    days_until_thursday = (3 - current_date.weekday()) % 7
    if days_until_thursday == 0:
        days_until_thursday = 7
    next_thursday = current_date + timedelta(days=days_until_thursday)
    return next_thursday.strftime("%d%b%y").upper()

def get_option_ltp(symbol, exchange="NSE_OPT"):
    try:
        df = client.history(
            symbol=symbol,
            exchange=exchange,
            interval="1m",
            start_date=datetime.now().strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d")
        )
        
        if df is not None and not df.empty:
            return float(df['close'].iloc[-1])
        else:
            logger.warning(f"No data for {symbol}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting option price for {symbol}: {e}")
        return None

# ============================================================
# PAPER TRADING SPECIFIC FUNCTIONS
# ============================================================

def place_order(symbol, action, quantity, price_type="MARKET", price=0):
    """Paper trading order placement"""
    if PAPER_TRADING:
        # Simulate order without actually placing
        logger.info(f"📝 PAPER TRADE: {action} {quantity} x {symbol}")
        logger.info(f"   Price Type: {price_type}, Price: {price}")
        
        # Log the paper trade
        paper_logger.log_trade({
            'symbol': symbol,
            'action': action,
            'quantity': quantity,
            'price_type': price_type,
            'price': price,
            'type': 'paper_trade'
        })
        
        return {
            'success': True,
            'message': 'Paper trade logged',
            'order_id': f"PAPER_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        }
    else:
        # Live trading code (same as before)
        try:
            response = client.placesmartorder(
                strategy=STRATEGY_NAME,
                symbol=symbol,
                action=action,
                exchange=OPTION_EXCHANGE,
                price_type=price_type,
                product=PRODUCT,
                quantity=quantity,
                price=price,
                position_size=quantity if action == "BUY" else -quantity
            )
            
            logger.info(f"Order placed: {action} {quantity} of {symbol}")
            return response
            
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return None

def square_off_position(symbol, quantity, action):
    """Close a position in paper trading"""
    close_action = "SELL" if action == "BUY" else "BUY"
    return place_order(symbol, close_action, quantity)

# ============================================================
# ENHANCED PERFORMANCE TRACKING
# ============================================================

def track_performance(strategy_instance, current_pnl):
    """Track performance metrics"""
    strategy_instance.pnl_history.append({
        'time': datetime.now().isoformat(),
        'pnl': current_pnl,
        'position': strategy_instance.position,
        'premium': strategy_instance.total_premium
    })
    
    # Keep only last 100 entries for performance
    if len(strategy_instance.pnl_history) > 100:
        strategy_instance.pnl_history = strategy_instance.pnl_history[-100:]
    
    # Calculate running statistics
    if len(strategy_instance.pnl_history) > 1:
        pnl_values = [p['pnl'] for p in strategy_instance.pnl_history]
        current_pnl = pnl_values[-1] if pnl_values else 0
        
        logger.debug(f"📊 P&L Tracking: {current_pnl:.2f}")

# ============================================================
# MAIN PAPER TRADING EXECUTION
# ============================================================

# Initialize paper trade logger
paper_logger = PaperTradeLogger()

# Initialize the strategy
strategy = PaperOptionSellingStrategy()

# The rest of the strategy methods are the same as before
# But we'll add paper trading specific enhancements

def run_paper_strategy():
    """Main paper trading strategy loop"""
    logger.info("🚀 Starting Paper Trading Strategy...")
    
    while strategy.running:
        try:
            # Check trading time
            current_time = get_current_time()
            if current_time < ENTRY_START or current_time > SQUARE_OFF_TIME:
                logger.debug(f"Outside trading hours: {current_time}")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Check expiry week
            _, days_to_expiry = get_nifty_expiry_dates()
            if days_to_expiry <= 7:
                logger.info(f"⚠️ Expiry week - days to expiry: {days_to_expiry}")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Get current data
            spot = get_spot_price()
            if spot is None:
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Get option data
            data = get_options_data(spot)
            if data is None:
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Display current status
            logger.info(f"📍 Spot: {spot}, Time: {current_time}")
            logger.info(f"📊 Call Premium: {data['call_premium']:.2f}, Put Premium: {data['put_premium']:.2f}")
            logger.info(f"💰 Total Premium: {data['total_premium']:.2f}")
            
            # Check if we have a position
            if strategy.position != 0:
                # Monitor position
                current_pnl = monitor_position()
                track_performance(strategy, current_pnl)
                
                # Check exit conditions
                if should_exit_position():
                    logger.info(f"✅ Exiting position at {current_time}")
                    exit_position()
                    continue
            
            # Check for entry opportunity
            if strategy.position == 0:
                if current_time >= ENTRY_START and current_time <= ENTRY_CUTOFF:
                    if data['total_premium'] >= 50:  # Minimum premium threshold
                        logger.info(f"🎯 Entry signal at {current_time}")
                        enter_position(data)
                    else:
                        logger.info(f"⏳ Premium too low: {data['total_premium']:.2f}")
            
            # Display summary every 5 minutes
            if int(current_time.split(':')[1]) % 5 == 0:
                logger.info(f"\n📈 {paper_logger.get_summary()}")
            
        except KeyboardInterrupt:
            logger.info("🛑 Paper trading stopped by user")
            strategy.running = False
            break
        except Exception as e:
            logger.error(f"❌ Error in paper strategy: {e}")
        
        time.sleep(CHECK_INTERVAL)

# ============================================================
# TESTING SCRIPT
# ============================================================

def run_test_scenario():
    """Run a test scenario for paper trading"""
    logger.info("🧪 Running paper trading test scenario...")
    
    test_prices = [22000, 22100, 22200, 22300, 22400, 22500]
    
    for test_price in test_prices:
        logger.info(f"\n📊 Testing at spot price: {test_price}")
        
        # Get option data
        spot = test_price
        call_strike = get_otm_strike(spot, 6, "CE")
        put_strike = get_otm_strike(spot, 6, "PE")
        
        logger.info(f"   Call Strike: {call_strike}, Put Strike: {put_strike}")
        
        # Simulate premium (simplified)
        call_premium = max(10, (spot - call_strike) * 0.1) if spot > call_strike else 10
        put_premium = max(10, (put_strike - spot) * 0.1) if spot < put_strike else 10
        
        logger.info(f"   Call Premium: {call_premium:.2f}, Put Premium: {put_premium:.2f}")
        logger.info(f"   Total Premium: {call_premium + put_premium:.2f}")
    
    logger.info("\n✅ Test scenario completed!")

# ============================================================
# START PAPER TRADING
# ============================================================

if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("🖥️  PAPER TRADING MODE ENABLED")
    logger.info("=" * 70)
    logger.info(f"📁 Trade Log: {TRADE_LOG_FILE}")
    logger.info(f"📋 Log File: paper_trading.log")
    logger.info("=" * 70)
    
    # Run test scenario first
    run_test_scenario()
    
    # Start live paper trading
    logger.info("\n" + "=" * 70)
    logger.info("🚀 Starting live paper trading...")
    logger.info("=" * 70)
    
    run_paper_strategy()
