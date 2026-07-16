"""
ScalpSurge — Breakout Scalping Strategy for Stocks

Entry (all must be true):
  1. Close > yesterday's high  (breakout)
  2. Volume > 1.3x 20-day avg  (participation)
  3. RSI(14) < 70              (not overbought)
  4. Close > 50 EMA            (uptrend context)

Exit (any one triggers):
  1. Close < 5 EMA             (momentum fades)
  2. Trailing stop: 2.5x ATR(14) from highest close since entry
  3. Fixed stop: -1.0%
  4. Target: +2.5%
  5. Time stop: 5 trading days

Architecture:
  - Singleton background engine, scans hourly on daily bars
  - Orders placed through place_smart_order_service (same flow as TradingView/strategy)
  - Positions tracked in-memory + scalping_tracked_symbol DB
  - SL registered with ScalpingRiskMonitor for server-side auto-exit
  - Session-aware, daily P&L limit, max position cap
"""

from __future__ import annotations

import atexit
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ScalpSurgeConfig:
    watchlist: list[str] = field(
        default_factory=lambda: [
            "RELIANCE",
            "INFY",
            "HDFCBANK",
            "SBIN",
        ]
    )
    exchange: str = "NSE"
    product: str = "CNC"
    interval: str = "D"
    history_lookback_days: int = 730  # 2 years for 50 EMA warmup

    # Indicators
    fast_ma: int = 5
    trend_ma: int = 50
    rsi_period: int = 14
    vol_period: int = 20
    atr_period: int = 14

    # Entry thresholds
    vol_mult: float = 1.3  # volume > 1.3x 20-day avg
    rsi_max: float = 70.0  # RSI < 70

    # Exit thresholds
    atr_stop_mult: float = 2.5  # trailing stop = 2.5x ATR
    target_pct: float = 0.025  # +2.5%
    stop_pct: float = 0.01  # -1.0%
    time_stop_days: int = 5  # max 5 trading days

    # Risk
    position_size_pct: float = 0.20  # 20% of capital per trade
    max_positions: int = 4
    scan_interval_sec: int = 3600  # hourly on daily bars

    # Order
    strategy_tag: str = "ScalpSurge"


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    entry_price: float
    qty: int
    entry_date: pd.Timestamp
    highest: float = 0.0

    def __post_init__(self):
        if self.highest == 0.0:
            self.highest = self.entry_price

    def update_highest(self, price: float):
        if price > self.highest:
            self.highest = price


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


def check_entry(df: pd.DataFrame, cfg: ScalpSurgeConfig) -> tuple[bool, dict]:
    """Check entry conditions on the last two bars (use [-2] for completed bar)."""
    close = df["close"]
    volume = df["volume"]

    ma_fast = close.rolling(cfg.fast_ma).mean()
    ma_trend = close.rolling(cfg.trend_ma).mean()
    rsi = calc_rsi(close, cfg.rsi_period)
    vol_ma = volume.rolling(cfg.vol_period).mean()
    atr = calc_atr(df["high"], df["low"], close, cfg.atr_period)

    # Use the last COMPLETED bar ([-2]) to avoid partial candle signals
    if len(df) < 3:
        return False, {}

    last = df.iloc[-2]  # last completed bar
    prev_high = df["high"].iloc[-3]  # bar before the breakout bar

    breakout = last["close"] > prev_high
    volume_ok = last["volume"] > vol_ma.iloc[-2] * cfg.vol_mult
    rsi_ok = rsi.iloc[-2] < cfg.rsi_max
    trend_ok = last["close"] > ma_trend.iloc[-2]

    signal = breakout and volume_ok and rsi_ok and trend_ok
    atr_val = atr.iloc[-2] if not pd.isna(atr.iloc[-2]) else last["close"] * 0.02

    return signal, {
        "ltp": float(last["close"]),
        "atr": float(atr_val),
        "stop": float(last["close"] * (1 - cfg.stop_pct)),
        "target": float(last["close"] * (1 + cfg.target_pct)),
        "ma_fast": float(ma_fast.iloc[-2]),
        "ma_trend": float(ma_trend.iloc[-2]),
        "rsi": float(rsi.iloc[-2]),
    }


def check_exit(
    df: pd.DataFrame,
    entry_price: float,
    entry_date: pd.Timestamp,
    highest_since_entry: float,
    cfg: ScalpSurgeConfig,
) -> tuple[bool, list[str], float]:
    """Check exit conditions. Returns (should_exit, reasons, trailing_stop_price)."""
    close = df["close"]
    ma_fast = close.rolling(cfg.fast_ma).mean()
    atr = calc_atr(df["high"], df["low"], close, cfg.atr_period)

    last = df.iloc[-1]
    last_date = last.name

    # Calculate trading days held (skip weekends)
    if hasattr(last_date, "date"):
        days_held = np.busday_count(entry_date.date(), last_date.date())
    else:
        days_held = 0

    trailing_stop = highest_since_entry - (atr.iloc[-1] * cfg.atr_stop_mult)
    if pd.isna(trailing_stop):
        trailing_stop = highest_since_entry * (1 - cfg.stop_pct)

    below_ma_fast = last["close"] < ma_fast.iloc[-1]
    hit_trail = last["close"] <= trailing_stop
    hit_target = last["close"] >= (entry_price * (1 + cfg.target_pct))
    hit_stop = last["close"] <= (entry_price * (1 - cfg.stop_pct))
    time_up = days_held >= cfg.time_stop_days

    reasons = []
    if below_ma_fast:
        reasons.append("below_ma5")
    if hit_trail:
        reasons.append("trailing_stop")
    if hit_target:
        reasons.append("target_hit")
    if hit_stop:
        reasons.append("stop_hit")
    if time_up:
        reasons.append("time_stop")

    return bool(reasons), reasons, float(trailing_stop)


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------


def fetch_bars(symbol: str, cfg: ScalpSurgeConfig, api_key: str) -> pd.DataFrame | None:
    """Fetch daily bars from history API."""
    try:
        from services.history_service import get_history

        end = datetime.now()
        start = end - timedelta(days=cfg.history_lookback_days)
        success, response, _code = get_history(
            symbol=symbol,
            exchange=cfg.exchange,
            interval=cfg.interval,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            api_key=api_key,
        )
        if not success or not isinstance(response, dict):
            return None
        rows = response.get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.set_index("timestamp")
        df = df.sort_index()
        if df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as e:
        logger.warning("ScalpSurge: history failed for %s: %s", symbol, e)
        return None
    finally:
        try:
            from database.auth_db import db_session as auth_session

            auth_session.remove()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Order placement (internal service layer)
# ---------------------------------------------------------------------------


def _place_smart_order(
    symbol: str,
    action: str,
    quantity: int,
    position_size: int,
    cfg: ScalpSurgeConfig,
    api_key: str,
) -> bool:
    """Place a smart order through the internal service layer."""
    try:
        from services.place_smart_order_service import place_smart_order

        order_data = {
            "apikey": api_key,
            "strategy": cfg.strategy_tag,
            "symbol": symbol,
            "action": action,
            "exchange": cfg.exchange,
            "price_type": "MARKET",
            "product": cfg.product,
            "quantity": quantity,
            "position_size": position_size,
        }
        success, response, _code = place_smart_order(order_data)
        if success:
            from database.scalping_db import track_symbol
            from database.settings_db import get_analyze_mode

            mode = "analyze" if get_analyze_mode() else "live"
            track_symbol(symbol, cfg.exchange, cfg.product, mode=mode)
        return success
    except Exception as e:
        logger.error("ScalpSurge: order failed for %s: %s", symbol, e)
        return False
    finally:
        try:
            from database.scalping_db import db_session as scalping_session

            scalping_session.remove()
        except Exception:
            pass


def _register_sl(
    symbol: str,
    action: str,
    quantity: int,
    sl_price: float,
    target: float,
    cfg: ScalpSurgeConfig,
) -> None:
    """Register SL with the risk monitor for server-side auto-exit."""
    try:
        from database.scalping_db import upsert_sl_state
        from database.settings_db import get_analyze_mode

        mode = "analyze" if get_analyze_mode() else "live"
        upsert_sl_state(
            {
                "symbol": symbol,
                "exchange": cfg.exchange,
                "product": cfg.product,
                "mode": mode,
                "side": action,
                "quantity": quantity,
                "entry_price": 0,
                "initial_sl": sl_price,
                "trailing_enabled": True,
                "trailing_step": 0,
                "current_sl": sl_price,
                "target": target,
                "is_active": True,
            }
        )
        from services.scalping_risk_monitor_service import notify_sl_changed

        notify_sl_changed()
    except Exception as e:
        logger.warning("ScalpSurge: SL register failed for %s: %s", symbol, e)
    finally:
        try:
            from database.scalping_db import db_session as scalping_session

            scalping_session.remove()
        except Exception:
            pass


def _clear_sl(symbol: str, cfg: ScalpSurgeConfig) -> None:
    """Clear the SL state after exit."""
    try:
        from database.scalping_db import delete_sl_state
        from database.settings_db import get_analyze_mode

        mode = "analyze" if get_analyze_mode() else "live"
        delete_sl_state(symbol, cfg.exchange, cfg.product, mode=mode)
        from services.scalping_risk_monitor_service import notify_sl_changed

        notify_sl_changed()
    except Exception as e:
        logger.debug("ScalpSurge: SL clear failed for %s: %s", symbol, e)
    finally:
        try:
            from database.scalping_db import db_session as scalping_session

            scalping_session.remove()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ScalpSurge engine (singleton)
# ---------------------------------------------------------------------------


class ScalpSurgeEngine:
    """Singleton breakout scalping engine integrated with the /scalping system."""

    _instance: ScalpSurgeEngine | None = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> ScalpSurgeEngine:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self) -> None:
        self._lock = threading.RLock()
        self._config = ScalpSurgeConfig()
        self._running = False
        self._paused = False
        self._positions: dict[str, Position] = {}
        self._scan_thread: threading.Thread | None = None
        self._scan_stop = threading.Event()
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        atexit.register(self.stop)

    # ------------------------------------------------------------------ lifecycle

    def start(self, config: ScalpSurgeConfig | None = None) -> dict:
        with self._lock:
            if self._running:
                return {"status": "already_running", "message": "ScalpSurge is already running"}
            if config is not None:
                self._config = config
            self._running = True
            self._paused = False
            self._positions.clear()
            self._daily_pnl = 0.0
            self._daily_trades = 0

        self._start_scan_timer()
        logger.info(
            "ScalpSurge started: %d symbols, interval=%s, scan every %ds",
            len(self._config.watchlist),
            self._config.interval,
            self._config.scan_interval_sec,
        )
        return {"status": "success", "message": "ScalpSurge started"}

    def stop(self) -> dict:
        with self._lock:
            if not self._running:
                return {"status": "not_running", "message": "ScalpSurge is not running"}
            self._running = False
            self._paused = False

        self._stop_scan_timer()
        self._close_all_positions("STRATEGY_STOP")
        logger.info("ScalpSurge stopped")
        return {"status": "success", "message": "ScalpSurge stopped"}

    def pause(self) -> dict:
        with self._lock:
            if not self._running:
                return {"status": "not_running"}
            self._paused = True
        logger.info("ScalpSurge paused")
        return {"status": "success", "message": "ScalpSurge paused"}

    def resume(self) -> dict:
        with self._lock:
            if not self._running:
                return {"status": "not_running"}
            self._paused = False
        logger.info("ScalpSurge resumed")
        return {"status": "success", "message": "ScalpSurge resumed"}

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "positions": len(self._positions),
                "daily_pnl": self._daily_pnl,
                "daily_trades": self._daily_trades,
                "watchlist": self._config.watchlist,
                "config": {
                    "exchange": self._config.exchange,
                    "product": self._config.product,
                    "interval": self._config.interval,
                    "fast_ma": self._config.fast_ma,
                    "trend_ma": self._config.trend_ma,
                    "rsi_period": self._config.rsi_period,
                    "vol_mult": self._config.vol_mult,
                    "atr_stop_mult": self._config.atr_stop_mult,
                    "target_pct": self._config.target_pct,
                    "stop_pct": self._config.stop_pct,
                    "time_stop_days": self._config.time_stop_days,
                    "position_size_pct": self._config.position_size_pct,
                    "max_positions": self._config.max_positions,
                    "scan_interval_sec": self._config.scan_interval_sec,
                    "strategy_tag": self._config.strategy_tag,
                },
                "position_details": [
                    {
                        "symbol": p.symbol,
                        "entry_price": p.entry_price,
                        "qty": p.qty,
                        "entry_date": str(p.entry_date),
                        "highest": p.highest,
                    }
                    for p in self._positions.values()
                ],
            }

    def update_config(self, **kwargs) -> dict:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config, k):
                    setattr(self._config, k, v)
        return {"status": "success", "config": self.status().get("config", {})}

    # ------------------------------------------------------------------ scan loop

    def _start_scan_timer(self) -> None:
        self._scan_stop.clear()
        self._scan_thread = threading.Thread(
            target=self._scan_loop, name="scalpsurge-scan", daemon=True
        )
        self._scan_thread.start()

    def _stop_scan_timer(self) -> None:
        self._scan_stop.set()
        self._scan_thread = None

    def _scan_loop(self) -> None:
        # Initial scan after 10s bootstrap
        self._scan_stop.wait(10)
        while not self._scan_stop.is_set():
            if not self._running:
                break
            try:
                self._scan_once()
            except Exception as e:
                logger.error("ScalpSurge: scan error: %s", e, exc_info=True)
            self._scan_stop.wait(self._config.scan_interval_sec)

    def _scan_once(self) -> None:
        api_key = self._resolve_api_key()
        if not api_key:
            logger.warning("ScalpSurge: no API key, skipping scan")
            return

        with self._lock:
            if self._paused:
                return
            positions_snapshot = dict(self._positions)
            max_pos = self._config.max_positions

        # --- Check exits first ---
        for symbol, pos in list(positions_snapshot.items()):
            df = fetch_bars(symbol, self._config, api_key)
            if df is None or len(df) < self._config.trend_ma + 5:
                continue

            pos.update_highest(float(df["close"].iloc[-1]))
            should_exit, reasons, trail_stop = check_exit(
                df, pos.entry_price, pos.entry_date, pos.highest, self._config
            )
            if should_exit:
                ok = _place_smart_order(symbol, "SELL", pos.qty, 0, self._config, api_key)
                if ok:
                    _clear_sl(symbol, self._config)
                    self._daily_trades += 1
                    logger.info(
                        "ScalpSurge: EXIT %s (%s) qty=%d @~%.2f",
                        symbol,
                        ", ".join(reasons),
                        pos.qty,
                        pos.entry_price,
                    )
                    with self._lock:
                        self._positions.pop(symbol, None)
            else:
                logger.info(
                    "ScalpSurge: HOLD %s | LTP: %.2f | Trail: %.2f | Days: %d",
                    symbol,
                    df["close"].iloc[-1],
                    trail_stop,
                    (df.index[-1] - pos.entry_date).days,
                )

        # --- Check entries ---
        with self._lock:
            current_count = len(self._positions)
            if current_count >= max_pos:
                return

        for symbol in self._config.watchlist:
            with self._lock:
                if symbol in self._positions:
                    continue
                if len(self._positions) >= max_pos:
                    return

            df = fetch_bars(symbol, self._config, api_key)
            if df is None or len(df) < self._config.trend_ma + 10:
                continue

            # Skip stale data
            last_date = df.index[-1]
            today = datetime.now().date()
            if last_date.date() < today - timedelta(days=5):
                logger.debug("ScalpSurge: skipping %s — stale data (%s)", symbol, last_date.date())
                continue

            signal, info = check_entry(df, self._config)
            if not signal:
                continue

            # Position sizing: 20% of capital
            capital = self._get_capital(api_key)
            if capital <= 0:
                continue
            risk_amount = capital * self._config.position_size_pct
            qty = int(risk_amount / info["ltp"]) if info["ltp"] > 0 else 0
            if qty <= 0:
                continue

            ok = _place_smart_order(symbol, "BUY", qty, qty, self._config, api_key)
            if not ok:
                continue

            _register_sl(
                symbol,
                "BUY",
                qty,
                info["stop"],
                info["target"],
                self._config,
            )

            entry_date = df.index[-1]
            self._positions[symbol] = Position(
                symbol=symbol,
                entry_price=info["ltp"],
                qty=qty,
                entry_date=entry_date,
            )
            logger.info(
                "ScalpSurge: ENTRY %s | Qty: %d | LTP: %.2f | Stop: %.2f | Target: %.2f | RSI: %.1f",
                symbol,
                qty,
                info["ltp"],
                info["stop"],
                info["target"],
                info["rsi"],
            )

        with self._lock:
            logger.info(
                "ScalpSurge: scan complete — %d/%d positions active",
                len(self._positions),
                self._config.max_positions,
            )

    # ------------------------------------------------------------------ helpers

    def _get_capital(self, api_key: str) -> float:
        """Get available cash from funds API."""
        try:
            from services.funds_service import get_funds

            ok, resp, _code = get_funds(api_key=api_key)
            if ok and isinstance(resp, dict):
                data = resp.get("data", {})
                return float(data.get("available_cash", 0))
        except Exception:
            pass
        return 100000.0  # fallback

    def _close_all_positions(self, reason: str) -> None:
        api_key = self._resolve_api_key()
        for symbol, pos in list(self._positions.items()):
            ok = _place_smart_order(symbol, "SELL", pos.qty, 0, self._config, api_key)
            if ok:
                _clear_sl(symbol, self._config)
                self._daily_trades += 1
                logger.info(
                    "ScalpSurge: EXIT (%s) %s SELL qty=%d",
                    reason,
                    symbol,
                    pos.qty,
                )
        with self._lock:
            self._positions.clear()

    def _resolve_api_key(self) -> str | None:
        try:
            from database.auth_db import get_api_key_for_tradingview
            from database.user_db import find_user_by_username

            user = find_user_by_username()
            if not user:
                return None
            return get_api_key_for_tradingview(user.username)
        except Exception:
            return None
        finally:
            try:
                from database.user_db import db_session as user_session

                user_session.remove()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton + accessors
# ---------------------------------------------------------------------------

scalpsurge_engine = ScalpSurgeEngine()


def get_scalpsurge_engine() -> ScalpSurgeEngine:
    return scalpsurge_engine
