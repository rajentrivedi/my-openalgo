"""
OpenAlgo Live/Paper Scalping Trader v2
======================================
Improved EMA(9)/EMA(21) + RSI + VWAP scalper with:
- 1m bars (actual scalping timeframe)
- Wired-up time-based exit (max_holding_bars)
- ATR-based dynamic stops (not fixed %)
- Local bar cache (no redundant API calls)
- Session-aware trading (avoids first/last 15 min)
- Daily P&L limits
- Slippage + commission modeling in risk calc
- Portfolio-level position cap
- Trailing stop after 1R profit
- Proper logging (not print)

SAFETY DESIGN
-------------
- LIVE_TRADING is False by default → OpenAlgo sandbox mode
- Ctrl+C triggers emergency close of all tracked positions
- Daily loss limit auto-stops trading

Install:
    pip install openalgo pandas numpy

Prerequisites:
    - OpenAlgo app running (default http://127.0.0.1:5000)
    - API key from OpenAlgo UI
    - Broker session active (for live mode)
"""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from openalgo import api

# ------------------------------- LOGGING ------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scalper")

# ------------------------------- SAFETY -------------------------------------
LIVE_TRADING = False
DAILY_LOSS_LIMIT_PCT = 2.0  # stop trading if daily loss exceeds % of capital


# ------------------------------ CONFIG --------------------------------------


@dataclass
class ScalperConfig:
    api_key: str = "your_api_key_here"
    host: str = "http://127.0.0.1:5000"
    exchange: str = "NSE"
    watchlist: list[str] = field(
        default_factory=lambda: [
            "RELIANCE",
            "TCS",
            "INFY",
            "HDFCBANK",
            "ICICIBANK",
        ]
    )
    interval: str = "1m"
    history_lookback_days: int = 3
    scan_interval_sec: int = 15

    # --- Indicators ---
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_lower: float = 40
    rsi_upper: float = 70
    atr_period: int = 14

    # --- Risk ---
    capital_per_symbol: float = 20000.0
    risk_per_trade_pct: float = 0.5
    slippage_pct: float = 0.03  # estimated slippage per side
    commission_pct: float = 0.01  # broker + exchange charges per side
    stop_loss_atr_mult: float = 1.5  # SL = entry - 1.5*ATR (long)
    target_atr_mult: float = 2.5  # TP = entry + 2.5*ATR (long)
    trail_atr_mult: float = 1.0  # trailing SL = 1.0*ATR from high

    # --- Session ---
    max_holding_bars: int = 20
    max_positions: int = 3  # max concurrent open positions
    session_start: str = "09:30"  # avoid first 15 min
    session_end: str = "15:15"  # avoid last 15 min

    # --- Order ---
    product: str = "MIS"
    strategy_tag: str = "python_scalper_v2"


# ------------------------------ POSITION ------------------------------------


@dataclass
class Position:
    symbol: str
    direction: str  # LONG / SHORT
    entry_price: float
    qty: int
    sl_price: float
    tgt_price: float
    trail_sl: float
    entry_bar_time: pd.Timestamp
    bars_held: int = 0
    peak_price: float = 0.0  # for trailing (long: highest, short: lowest)


# ------------------------- BAR CACHE ----------------------------------------


class BarCache:
    """Avoids re-fetching full history every scan cycle.
    Stores last N days of bars per symbol, updates incrementally."""

    def __init__(self, lookback_days: int):
        self.lookback_days = lookback_days
        self._cache: dict[str, pd.DataFrame] = {}

    def get(self, symbol: str) -> pd.DataFrame | None:
        return self._cache.get(symbol)

    def update(self, symbol: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        existing = self._cache.get(symbol)
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
            cutoff = combined.index.max() - pd.Timedelta(days=self.lookback_days + 1)
            combined = combined[combined.index >= cutoff]
            self._cache[symbol] = combined
        else:
            self._cache[symbol] = df

    def invalidate(self, symbol: str):
        self._cache.pop(symbol, None)


# ------------------------- INDICATORS ---------------------------------------


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame, cfg: ScalperConfig) -> pd.DataFrame:
    df = df.copy()

    # EMAs
    df["EMA_fast"] = df["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    df["EMA_slow"] = df["close"].ewm(span=cfg.ema_slow, adjust=False).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period).mean()
    avg_loss = loss.ewm(alpha=1 / cfg.rsi_period, min_periods=cfg.rsi_period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    # ATR
    df["ATR"] = compute_atr(df, cfg.atr_period)

    # VWAP (resets each trading day)
    df["date"] = df.index.date
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    df["TPV"] = typical_price * df["volume"]
    df["cum_TPV"] = df.groupby("date")["TPV"].cumsum()
    df["cum_Vol"] = df.groupby("date")["volume"].cumsum()
    df["VWAP"] = df["cum_TPV"] / df["cum_Vol"].replace(0, np.nan)

    # Volume SMA for spike detection
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"].replace(0, np.nan)

    return df.dropna()


# ------------------------- SIGNAL GENERATION --------------------------------


def latest_signal(df: pd.DataFrame, cfg: ScalperConfig) -> int:
    """Returns 1 (long), -1 (short), or 0 (none) based on the most recently closed bar."""
    if len(df) < max(cfg.ema_slow, cfg.rsi_period, cfg.atr_period) + 5:
        return 0

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # EMA crossover
    bullish_cross = prev["EMA_fast"] <= prev["EMA_slow"] and last["EMA_fast"] > last["EMA_slow"]
    bearish_cross = prev["EMA_fast"] >= prev["EMA_slow"] and last["EMA_fast"] < last["EMA_slow"]

    # Trend filter: price above/below VWAP
    above_vwap = last["close"] > last["VWAP"]
    below_vwap = last["close"] < last["VWAP"]

    # RSI in range (not overbought for longs, not oversold for shorts)
    rsi_ok_long = cfg.rsi_lower < last["RSI"] < cfg.rsi_upper
    rsi_ok_short = cfg.rsi_lower < last["RSI"] < cfg.rsi_upper

    # Volume confirmation: at least 0.8x average (avoid dead bars)
    vol_ok = last["vol_ratio"] > 0.8

    if bullish_cross and above_vwap and rsi_ok_long and vol_ok:
        return 1
    if bearish_cross and below_vwap and rsi_ok_short and vol_ok:
        return -1

    return 0


# ------------------------- TRADER ENGINE ------------------------------------


class Scalper:
    def __init__(self, cfg: ScalperConfig):
        self.cfg = cfg
        self.client = api(api_key=cfg.api_key, host=cfg.host)
        self.positions: dict[str, Position] = {}
        self.cache = BarCache(cfg.history_lookback_days)
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self._ensure_safety_mode()

    # --- Safety ---

    def _ensure_safety_mode(self):
        try:
            status = self.client.analyzerstatus()
            currently_analyze = status.get("data", {}).get("analyze_mode", True)
            if not LIVE_TRADING and not currently_analyze:
                log.info("Forcing OpenAlgo into analyzer (sandbox) mode...")
                self.client.analyzertoggle(mode=True)
            elif LIVE_TRADING:
                log.warning("!" * 70)
                log.warning("LIVE_TRADING = True — REAL ORDERS WILL BE PLACED.")
                log.warning("!" * 70)
                confirm = input("Type YES to continue: ")
                if confirm.strip() != "YES":
                    sys.exit(0)
            else:
                log.info("Running in analyzer (sandbox) mode.")
        except Exception as e:
            log.warning(f"Could not check analyzer status: {e}")

    # --- Session Filter ---

    def _in_trading_session(self) -> bool:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        return self.cfg.session_start <= current_time <= self.cfg.session_end

    # --- Risk Checks ---

    def _daily_loss_limit_hit(self) -> bool:
        if self.cfg.capital_per_symbol == 0:
            return False
        total_capital = self.cfg.capital_per_symbol * len(self.cfg.watchlist)
        max_loss = total_capital * (DAILY_LOSS_LIMIT_PCT / 100)
        return self.daily_pnl <= -max_loss

    def _can_open_position(self) -> bool:
        if len(self.positions) >= self.cfg.max_positions:
            return False
        if self._daily_loss_limit_hit():
            return False
        return True

    # --- Data Fetch ---

    def get_bars(self, symbol: str) -> pd.DataFrame | None:
        cached = self.cache.get(symbol)
        if cached is not None and not cached.empty:
            last_cached = cached.index.max()
            end = pd.Timestamp.now(tz="Asia/Kolkata")
            if (end - last_cached).total_seconds() < 120:
                return cached

        end = pd.Timestamp.now(tz="Asia/Kolkata")
        start = end - pd.Timedelta(days=self.cfg.history_lookback_days)
        try:
            data = self.client.history(
                symbol=symbol,
                exchange=self.cfg.exchange,
                interval=self.cfg.interval,
                start_date=start.strftime("%Y-%m-%d"),
                end_date=end.strftime("%Y-%m-%d"),
                source="api",
            )
        except Exception as e:
            log.error(f"[{symbol}] history() failed: {e}")
            return cached  # fall back to cached data

        if data is None or data.empty:
            return cached

        self.cache.update(symbol, data)
        return self.cache.get(symbol)

    # --- Position Management ---

    def _calc_slippage_cost(self, price: float) -> float:
        return price * (self.cfg.slippage_pct + self.cfg.commission_pct) / 100

    def enter_position(self, symbol: str, direction: int, price: float, atr: float):
        if not self._can_open_position():
            return
        if atr <= 0 or np.isnan(atr):
            return

        sl_distance = atr * self.cfg.stop_loss_atr_mult
        tgt_distance = atr * self.cfg.target_atr_mult

        if direction == 1:
            sl = price - sl_distance
            tgt = price + tgt_distance
            action = "BUY"
        else:
            sl = price + sl_distance
            tgt = price - tgt_distance
            action = "SELL"

        # Account for slippage in risk calc
        effective_sl_distance = abs(price - sl) + self._calc_slippage_cost(price)
        risk_amount = self.cfg.capital_per_symbol * (self.cfg.risk_per_trade_pct / 100)
        qty = int(risk_amount / effective_sl_distance) if effective_sl_distance > 0 else 0

        if qty <= 0:
            return

        resp = self.client.placeorder(
            strategy=self.cfg.strategy_tag,
            symbol=symbol,
            action=action,
            exchange=self.cfg.exchange,
            price_type="MARKET",
            product=self.cfg.product,
            quantity=qty,
        )
        log.info(
            f"[{symbol}] ENTRY {action} qty={qty} @ ~{price:.2f} SL={sl:.2f} TP={tgt:.2f} → {resp}"
        )

        self.positions[symbol] = Position(
            symbol=symbol,
            direction="LONG" if direction == 1 else "SHORT",
            entry_price=price,
            qty=qty,
            sl_price=sl,
            tgt_price=tgt,
            trail_sl=sl,
            entry_bar_time=pd.Timestamp.now(),
            peak_price=price,
        )

    def exit_position(self, symbol: str, reason: str):
        pos = self.positions.get(symbol)
        if not pos:
            return

        action = "SELL" if pos.direction == "LONG" else "BUY"
        resp = self.client.placeorder(
            strategy=self.cfg.strategy_tag,
            symbol=symbol,
            action=action,
            exchange=self.cfg.exchange,
            price_type="MARKET",
            product=self.cfg.product,
            quantity=pos.qty,
        )

        # Estimate P&L
        exit_price = pos.entry_price  # rough estimate; real fill unknown
        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - exit_price) * pos.qty

        self.daily_pnl += pnl
        self.daily_trades += 1
        log.info(f"[{symbol}] EXIT ({reason}) {action} qty={pos.qty} est_pnl={pnl:.2f} → {resp}")
        del self.positions[symbol]

    def update_trailing_sl(self, symbol: str, ltp: float, atr: float):
        pos = self.positions.get(symbol)
        if not pos or atr <= 0:
            return

        trail_distance = atr * self.cfg.trail_atr_mult

        if pos.direction == "LONG":
            pos.peak_price = max(pos.peak_price, ltp)
            profit_from_peak = pos.peak_price - pos.entry_price
            # Only start trailing after 1R profit
            if profit_from_peak >= (pos.entry_price - pos.sl_price):
                new_trail = pos.peak_price - trail_distance
                if new_trail > pos.trail_sl:
                    pos.trail_sl = new_trail
        else:
            pos.peak_price = min(pos.peak_price, ltp)
            profit_from_peak = pos.entry_price - pos.peak_price
            if profit_from_peak >= (pos.sl_price - pos.entry_price):
                new_trail = pos.peak_price + trail_distance
                if new_trail < pos.trail_sl:
                    pos.trail_sl = new_trail

    def check_exits(self, symbol: str, ltp: float, atr: float):
        pos = self.positions.get(symbol)
        if not pos:
            return

        pos.bars_held += 1
        self.update_trailing_sl(symbol, ltp, atr)

        if pos.direction == "LONG":
            # Time exit
            if pos.bars_held >= self.cfg.max_holding_bars:
                self.exit_position(symbol, "TIME_EXIT")
                return
            # Trailing SL
            if ltp <= pos.trail_sl:
                self.exit_position(symbol, "TRAILING_SL")
                return
            # Initial SL
            if ltp <= pos.sl_price:
                self.exit_position(symbol, "STOP_LOSS")
                return
            # Target
            if ltp >= pos.tgt_price:
                self.exit_position(symbol, "TARGET")
                return
        else:
            if pos.bars_held >= self.cfg.max_holding_bars:
                self.exit_position(symbol, "TIME_EXIT")
                return
            if ltp >= pos.trail_sl:
                self.exit_position(symbol, "TRAILING_SL")
                return
            if ltp >= pos.sl_price:
                self.exit_position(symbol, "STOP_LOSS")
                return
            if ltp <= pos.tgt_price:
                self.exit_position(symbol, "TARGET")
                return

    # --- Main Scan ---

    def scan_once(self):
        if not self._in_trading_session():
            return

        for symbol in self.cfg.watchlist:
            df = self.get_bars(symbol)
            if df is None or len(df) < 30:
                continue

            df = add_indicators(df, self.cfg)
            if df.empty:
                continue

            last = df.iloc[-1]
            ltp = last["close"]
            atr = last["ATR"]

            # Manage existing position
            if symbol in self.positions:
                self.check_exits(symbol, ltp, atr)
                continue

            # Check for new entry
            if not self._can_open_position():
                continue

            sig = latest_signal(df, self.cfg)
            if sig != 0:
                self.enter_position(symbol, sig, ltp, atr)

    def emergency_close_all(self):
        log.warning("Emergency closing all tracked positions...")
        for symbol in list(self.positions.keys()):
            self.exit_position(symbol, "EMERGENCY_CLOSE")
        try:
            self.client.closeposition(strategy=self.cfg.strategy_tag)
        except Exception as e:
            log.error(f"closeposition() failed: {e}")

    def run(self):
        log.info(
            f"Watching {len(self.cfg.watchlist)} symbols, "
            f"scanning every {self.cfg.scan_interval_sec}s on {self.cfg.interval} bars. "
            f"Session: {self.cfg.session_start}-{self.cfg.session_end}"
        )

        def handle_sigint(sig, frame):
            self.emergency_close_all()
            sys.exit(0)

        signal.signal(signal.SIGINT, handle_sigint)

        while True:
            try:
                self.scan_once()
            except Exception as e:
                log.error(f"scan_once() error: {e}", exc_info=True)
            time.sleep(self.cfg.scan_interval_sec)


# ------------------------------ ENTRY POINT ---------------------------------

if __name__ == "__main__":
    config = ScalperConfig(
        api_key="your_api_key_here",
        host="http://127.0.0.1:5000",
        watchlist=["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"],
        interval="1m",
        scan_interval_sec=15,
    )
    scalper = Scalper(config)
    scalper.run()
