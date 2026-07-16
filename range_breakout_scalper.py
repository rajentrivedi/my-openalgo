"""
Intraday Rolling Range Breakout Scalper V2
===========================================
OpenAlgo Python implementation of
"Indian Intraday Range Breakout Scalper" strategy.

Trades NSE liquid stocks using a rolling N-candle consolidation
range breakout with volume, VWAP, and NIFTY market regime filters.

Entry modes:  Breakout  (breakout bar triggers entry)
              Retest    (breakout + retest triggers entry)

All configuration via constants at the top.
Set PAPER_TRADING = True for simulated execution.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from openalgo import api, ta

# ============================================================================
# TIMEZONE
# ============================================================================

IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================================
# CONFIGURATION
# ============================================================================

# OpenAlgo connection
API_KEY = os.getenv("OPENALGO_API_KEY", "your-openalgo-api-key")
API_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")

PAPER_TRADING = True
STRATEGY_NAME = "RangeBreakoutScalperV2"

# ----- Universe -----
SYMBOLS_FILE = "data/qtyfreeze.csv"
MAX_SYMBOLS = 100

# ----- Trading parameters -----
EXCHANGE = "NSE"
PRODUCT = "MIS"
QUANTITY = 1
TIMEFRAME = "5m"
LOOKBACK_DAYS = 10

# ----- Strategy parameters -----
RANGE_BARS = 8
MAX_RANGE_WIDTH_PCT = 1.0
ATR_LENGTH = 14
MIN_BREAKOUT_ATR = 0.10
VOLUME_LOOKBACK = 5
RELATIVE_VOLUME_MULTIPLIER = 1.20
USE_VWAP_FILTER = True
USE_MARKET_FILTER = True
NIFTY_EMA_LENGTH = 20

# ----- Sessions -----
SESSION1_START = "09:20"
SESSION1_END = "11:00"
SESSION2_START = "13:30"
SESSION2_END = "15:00"

# ----- Entry mode -----
ENTRY_MODE = "Breakout"  # "Breakout" or "Retest"
RETEST_TOLERANCE_ATR = 0.15
RETEST_MAX_BARS = 3

# ----- Risk management -----
STOP_MODE = "Range"  # "Range" or "ATR"
ATR_STOP_MULTIPLIER = 0.50
TARGET_RR = 1.50
USE_TRAILING = True
TRAIL_ATR_MULTIPLIER = 0.75

# ----- Risk-based position sizing -----
USE_RISK_BASED_SIZING = True
RISK_PER_TRADE_PCT = 0.25

# ----- Daily risk controls -----
USE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_PCT = 2.0
MAX_TRADES_PER_DAY = 5
SQUARE_OFF_ON_DAILY_LOSS = False

# ----- Volume -----
USE_TIME_OF_DAY_VOLUME = True
VOLUME_PROFILE_DAYS = 20

# ----- Intraday timing -----
ENTRY_CUTOFF = "15:00"
SQUARE_OFF_TIME = "15:15"
CHECK_INTERVAL = 15

# ----- Transaction costs -----
ESTIMATED_COST_PCT = 0.03  # one-way, applied on entry + exit

# ----- Trade log -----
TRADE_LOG_DIR = "data/trades"
TRADE_LOG_FILE = f"{STRATEGY_NAME}_trades.csv"

# ============================================================================
# GLOBAL STATE
# ============================================================================

_global_trades_today = 0
_global_trades_today_date: str | None = None
_global_day_start_equity: float | None = None
_global_daily_loss_hit = False
_global_daily_loss_date: str | None = None
_global_trade_counter = 0
_retest_setups: dict[str, dict[str, Any]] = {}
_volume_profile: dict[str, dict[str, float]] = {}
_volume_profile_date: str | None = None

# ============================================================================
# LIQUID STOCKS UNIVERSE
# ============================================================================


def load_liquid_symbols(filepath: str = SYMBOLS_FILE, max_symbols: int = MAX_SYMBOLS) -> list[str]:
    INDEX_SYMBOLS = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "NIFTYNXT50"}
    symbols: list[str] = []
    with open(filepath) as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[1] and parts[1] not in ("SYMBOL", *INDEX_SYMBOLS):
                symbols.append(parts[1])
                if len(symbols) >= max_symbols:
                    break
    return symbols


# ============================================================================
# TRADE LOG
# ============================================================================


TRADE_LOG_PATH = str(Path(TRADE_LOG_DIR) / TRADE_LOG_FILE)

TRADE_LOG_COLUMNS = [
    "trade_id",
    "date",
    "symbol",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "quantity",
    "initial_stop",
    "target",
    "exit_reason",
    "gross_pnl",
    "estimated_cost",
    "net_pnl",
    "risk_amount",
    "r_multiple",
    "duration_minutes",
    "range_high",
    "range_low",
    "range_width",
    "range_width_pct",
    "atr_at_entry",
    "relative_volume",
    "volume_multiplier",
    "vwap_at_entry",
    "vwap_distance",
    "nifty_price",
    "nifty_ema",
    "nifty_regime",
    "session",
    "entry_mode",
]


def init_trade_log() -> None:
    Path(TRADE_LOG_DIR).mkdir(parents=True, exist_ok=True)
    if not os.path.isfile(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(TRADE_LOG_COLUMNS)


def append_trade_log(row: dict[str, Any]) -> None:
    with open(TRADE_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_COLUMNS, extrasaction="ignore")
        writer.writerow(row)


# ============================================================================
# VOLUME PROFILE
# ============================================================================


def _slot_key_from_index(ts: pd.Timestamp) -> str:
    tz_ts = ts.tz_convert(IST) if ts.tz else ts.tz_localize(IST)
    return tz_ts.strftime("%H:%M")


def build_volume_profile(
    symbols: list[str], client: api, lookback_days: int = VOLUME_PROFILE_DAYS
) -> None:
    global _volume_profile, _volume_profile_date
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    if _volume_profile_date == today_str:
        return

    profile: dict[str, dict[str, list[float]]] = {}
    end = datetime.now(IST).strftime("%Y-%m-%d")
    start = (datetime.now(IST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    for symbol in symbols[:10]:
        try:
            df = client.history(
                symbol=symbol, exchange=EXCHANGE, interval=TIMEFRAME, start_date=start, end_date=end
            )
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            for ts, row in df.iterrows():
                slot = _slot_key_from_index(ts)
                vol = float(row.get("volume", 0))
                if vol <= 0:
                    continue
                if slot not in profile:
                    profile[slot] = {"total": 0.0, "count": 0}
                # Include all past data for this slot across symbols
                profile[slot]["total"] += vol
                profile[slot]["count"] += 1
        except Exception:
            continue

    result: dict[str, dict[str, float]] = {}
    for slot, data in profile.items():
        if data["count"] > 0:
            result[slot] = {"avg_volume": data["total"] / data["count"], "count": data["count"]}

    _volume_profile = result  # type: ignore[assignment]
    _volume_profile_date = today_str
    print(f"[VOLUME] Profile built: {len(result)} time slots across {len(symbols[:10])} symbols")


def get_relative_volume_tod(slot: str, current_volume: float) -> float:
    if not _volume_profile or slot not in _volume_profile:
        return current_volume / 1.0
    avg = float(_volume_profile[slot]["avg_volume"])
    if avg <= 0:
        return current_volume / 1.0
    return current_volume / avg


# ============================================================================
# STRATEGY ENGINE
# ============================================================================


@dataclass
class OpenPosition:
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_time: pd.Timestamp
    entry_price: float
    quantity: int
    sl_price: float
    tp_price: float
    trail_price: float | None = None
    atr_at_entry: float = 0.0
    range_high: float = 0.0
    range_low: float = 0.0
    range_width: float = 0.0
    range_width_pct: float = 0.0
    relative_volume: float = 0.0
    vwap_at_entry: float = 0.0
    vwap_distance: float = 0.0
    nifty_price: float = 0.0
    nifty_ema: float = 0.0
    nifty_regime: str = "NEUTRAL"
    session: str = ""
    entry_mode: str = ""


@dataclass
class CandleState:
    last_processed: pd.Timestamp | None = None


class RangeBreakoutScalper:
    def __init__(self) -> None:
        if API_KEY == "your-openalgo-api-key" or not API_HOST:
            print("[ERROR] Set OPENALGO_API_KEY and OPENALGO_HOST env vars (or edit constants)")
            raise SystemExit(1)

        self.client = api(api_key=API_KEY, host=API_HOST)
        self.symbols = load_liquid_symbols()
        self.open_positions: dict[str, OpenPosition] = {}
        self.candle_states: dict[str, CandleState] = {sym: CandleState() for sym in self.symbols}
        self.nifty_data: pd.DataFrame | None = None
        self.nifty_data_date: str | None = None
        self._symbol_data_cache: dict[str, pd.DataFrame] = {}
        self._symbol_cache_ts: dict[str, float] = {}
        self._current_cycle_atr: dict[str, float] = {}

        init_trade_log()
        self._reconcile_positions()
        self._print_startup()

    def _reconcile_positions(self) -> None:
        try:
            pb = self.client.positionbook()
            if isinstance(pb, dict) and "positions" in pb:
                for pos in pb["positions"]:
                    sym = pos.get("symbol", "")
                    qty = int(pos.get("quantity", 0))
                    if sym in self.symbols and qty != 0:
                        self.open_positions[sym] = OpenPosition(
                            symbol=sym,
                            direction="LONG" if qty > 0 else "SHORT",
                            entry_time=pd.Timestamp.now(),
                            entry_price=float(pos.get("entry_price", 0)),
                            quantity=abs(qty),
                            sl_price=0.0,
                            tp_price=0.0,
                        )
                        print(f"[RECONCILE] Detected {sym} {qty} shares")
        except Exception as e:
            print(f"[RECONCILE] Position API unavailable ({e}); assuming flat")

    def _print_startup(self) -> None:
        print(f"\n{'=' * 60}")
        print(f"STRATEGY:      {STRATEGY_NAME}")
        print(f"SYMBOLS:       {len(self.symbols)}")
        print(f"TIMEFRAME:     {TIMEFRAME}")
        print(f"ENTRY MODE:    {ENTRY_MODE}")
        print(
            f"RISK/TRADE:    {RISK_PER_TRADE_PCT}%"
            if USE_RISK_BASED_SIZING
            else f"FIXED QTY: {QUANTITY}"
        )
        print(f"MAX TRADES:    {MAX_TRADES_PER_DAY}/day")
        print(f"MAX LOSS:      {MAX_DAILY_LOSS_PCT}%")
        print(f"SESSIONS:      {SESSION1_START}-{SESSION1_END} + {SESSION2_START}-{SESSION2_END}")
        print(f"PAPER MODE:    {PAPER_TRADING}")
        print(f"{'=' * 60}\n")

    # ------------------------------------------------------------------
    # NIFTY
    # ------------------------------------------------------------------

    def _get_nifty(self) -> pd.DataFrame | None:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        if self.nifty_data_date == today_str and self.nifty_data is not None:
            return self.nifty_data
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        try:
            df = self.client.history(
                symbol="NIFTY",
                exchange="NSE_INDEX",
                interval=TIMEFRAME,
                start_date=start,
                end_date=end,
            )
            if isinstance(df, pd.DataFrame) and not df.empty:
                nifty = df.copy()
                nifty["close"] = pd.to_numeric(nifty["close"], errors="coerce")
                nifty["ema"] = nifty["close"].ewm(span=NIFTY_EMA_LENGTH, adjust=False).mean()
                nifty["bullish"] = nifty["close"] > nifty["ema"]
                nifty["bearish"] = nifty["close"] < nifty["ema"]
                self.nifty_data = nifty
                self.nifty_data_date = today_str
                return nifty
            if isinstance(df, dict):
                print(f"[NIFTY] {df.get('message', 'error')}")
                return None
        except Exception as e:
            print(f"[NIFTY] Fetch error: {e}")
        return None

    def _nifty_at_candle(self, candle_ts: pd.Timestamp) -> tuple[bool | None, float, float]:
        nifty = self._get_nifty()
        if nifty is None or nifty.empty:
            return None, 0.0, 0.0
        if candle_ts.tz is None:
            candle_ts = candle_ts.tz_localize(IST)
        match = nifty[nifty.index == candle_ts]
        if match.empty:
            # Find nearest prior candle
            prior = nifty[nifty.index < candle_ts]
            if prior.empty:
                return None, 0.0, 0.0
            match = prior.iloc[[-1]]
        row = match.iloc[0]
        return bool(row["bullish"]), float(row["close"]), float(row["ema"])

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _fetch_data(self, symbol: str) -> pd.DataFrame | None:
        now_s = time.time()
        cached = self._symbol_data_cache.get(symbol)
        cache_age = now_s - self._symbol_cache_ts.get(symbol, 0) if cached else 999
        if cached is not None and cache_age < 30:
            return cached
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        try:
            df = self.client.history(
                symbol=symbol, exchange=EXCHANGE, interval=TIMEFRAME, start_date=start, end_date=end
            )
            if isinstance(df, pd.DataFrame) and not df.empty:
                self._symbol_data_cache[symbol] = df
                self._symbol_cache_ts[symbol] = now_s
                return df
        except Exception:
            pass
        return None

    def _clear_stale_cache(self) -> None:
        now_s = time.time()
        stale = [k for k, v in self._symbol_cache_ts.items() if now_s - v > 120]
        for k in stale:
            self._symbol_data_cache.pop(k, None)
            self._symbol_cache_ts.pop(k, None)

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        for c in ["close", "high", "low", "open", "volume"]:
            data[c] = pd.to_numeric(data[c], errors="coerce")
        data = data.dropna()

        data["atr"] = ta.atr(data["high"], data["low"], data["close"], ATR_LENGTH)

        data["vwap"] = ta.vwap(data["high"], data["low"], data["close"], data["volume"])

        # Range
        data["range_high"] = data["high"].shift(1).rolling(RANGE_BARS).max()
        data["range_low"] = data["low"].shift(1).rolling(RANGE_BARS).min()
        data["range_width"] = data["range_high"] - data["range_low"]
        data["range_width_pct"] = (data["range_width"] / data["range_low"].clip(lower=1e-10)) * 100
        data["is_tight_range"] = data["range_width_pct"] <= MAX_RANGE_WIDTH_PCT

        # Volume
        data["avg_volume_prior"] = data["volume"].shift(1).rolling(VOLUME_LOOKBACK).mean()
        data["avg_vol_safe"] = data["avg_volume_prior"].clip(lower=1e-10)
        data["rel_volume"] = data["volume"] / data["avg_vol_safe"]
        data["volume_confirmed"] = data["rel_volume"] >= RELATIVE_VOLUME_MULTIPLIER

        # Breakout
        data["long_breakout"] = (data["close"] > data["range_high"]) & (
            data["close"] >= data["range_high"] + data["atr"] * MIN_BREAKOUT_ATR
        )
        data["short_breakout"] = (data["close"] < data["range_low"]) & (
            data["close"] <= data["range_low"] - data["atr"] * MIN_BREAKOUT_ATR
        )

        # VWAP
        if USE_VWAP_FILTER:
            data["long_vwap"] = data["close"] > data["vwap"]
            data["short_vwap"] = data["close"] < data["vwap"]
        else:
            data["long_vwap"] = True
            data["short_vwap"] = True

        return data

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _compute_quantity(self, entry_price: float, stop_price: float) -> int:
        if not USE_RISK_BASED_SIZING:
            return QUANTITY
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return QUANTITY
        eq = self._get_account_equity()
        risk_amount = eq * RISK_PER_TRADE_PCT / 100.0
        qty = int(risk_amount / risk_per_share)
        return max(qty, 1)

    def _get_account_equity(self) -> float:
        if PAPER_TRADING:
            return 100000.0
        try:
            funds = self.client.funds()
            if isinstance(funds, dict):
                return float(funds.get("equity", 0) or funds.get("total_equity", 0) or 100000)
        except Exception:
            pass
        return 100000.0

    # ------------------------------------------------------------------
    # Global daily state
    # ------------------------------------------------------------------

    def _check_daily_reset(self) -> None:
        global \
            _global_trades_today_date, \
            _global_trades_today, \
            _global_daily_loss_hit, \
            _global_daily_loss_date, \
            _global_day_start_equity, \
            _global_trade_counter
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        if _global_trades_today_date != today_str:
            _global_trades_today_date = today_str
            _global_trades_today = 0
            _global_daily_loss_hit = False
            _global_daily_loss_date = None
            _global_day_start_equity = self._get_account_equity()

    def _check_daily_loss(self) -> bool:
        global _global_daily_loss_hit, _global_daily_loss_date
        if not USE_DAILY_LOSS_LIMIT:
            return True
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        if _global_daily_loss_date != today_str:
            _global_daily_loss_hit = False
            _global_daily_loss_date = today_str
        if _global_daily_loss_hit:
            return False
        current_eq = self._get_account_equity()
        start_eq = _global_day_start_equity or current_eq
        loss_pct = ((current_eq - start_eq) / start_eq) * 100
        if loss_pct <= -MAX_DAILY_LOSS_PCT:
            _global_daily_loss_hit = True
            print(f"[DAILY LOSS] Limit {MAX_DAILY_LOSS_PCT}% hit ({loss_pct:.2f}%)")
            if SQUARE_OFF_ON_DAILY_LOSS and not PAPER_TRADING:
                for sym, _pos in list(self.open_positions.items()):
                    self._exit_position(sym, "DAILY_LOSS_LIMIT")
            return False
        return True

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def _enter(self, symbol: str, direction: str, signal: pd.Series, nifty_info: tuple) -> None:
        global _global_trades_today, _global_trade_counter
        if _global_trades_today >= MAX_TRADES_PER_DAY:
            return
        if not self._check_daily_loss():
            return

        entry_price = float(signal["close"])
        atr_val = float(signal["atr"]) if not pd.isna(signal.get("atr", float("nan"))) else 0.0
        range_high = (
            float(signal["range_high"])
            if not pd.isna(signal.get("range_high", float("nan")))
            else 0.0
        )
        range_low = (
            float(signal["range_low"])
            if not pd.isna(signal.get("range_low", float("nan")))
            else 0.0
        )
        range_width = range_high - range_low
        range_width_pct = (range_width / max(range_low, 1e-10)) * 100
        rel_vol = float(signal.get("rel_volume", 0))
        vwap_val = float(signal["vwap"]) if not pd.isna(signal.get("vwap", float("nan"))) else 0.0

        nifty_bullish, nifty_price, nifty_ema = nifty_info

        # SL
        if direction == "LONG":
            if STOP_MODE == "Range":
                sl_price = range_low
            else:
                sl_price = entry_price - atr_val * ATR_STOP_MULTIPLIER
        else:
            if STOP_MODE == "Range":
                sl_price = range_high
            else:
                sl_price = entry_price + atr_val * ATR_STOP_MULTIPLIER

        risk_per = abs(entry_price - sl_price)
        if risk_per <= 0:
            print(f"[SKIP] {symbol} {direction} — zero risk (entry={entry_price} sl={sl_price})")
            return

        # TP
        tp_price = (
            entry_price + risk_per * TARGET_RR
            if direction == "LONG"
            else entry_price - risk_per * TARGET_RR
        )

        qty = self._compute_quantity(entry_price, sl_price)

        _global_trade_counter += 1
        _trade_id = f"{STRATEGY_NAME}_{_global_trade_counter}"
        candle_ts = signal.name
        session_label = (
            "S1"
            if (SESSION1_START <= candle_ts.strftime("%H:%M") < SESSION1_END)
            else "S2"
            if (SESSION2_START <= candle_ts.strftime("%H:%M") < SESSION2_END)
            else ""
        )

        pos = OpenPosition(
            symbol=symbol,
            direction=direction,
            entry_time=candle_ts,
            entry_price=entry_price,
            quantity=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            atr_at_entry=atr_val,
            range_high=range_high,
            range_low=range_low,
            range_width=range_width,
            range_width_pct=range_width_pct,
            relative_volume=rel_vol,
            vwap_at_entry=vwap_val,
            vwap_distance=((entry_price - vwap_val) / vwap_val * 100) if vwap_val else 0,
            nifty_price=nifty_price,
            nifty_ema=nifty_ema,
            nifty_regime="BULLISH"
            if nifty_bullish
            else "BEARISH"
            if nifty_bullish is False
            else "NEUTRAL",
            session=session_label,
            entry_mode=ENTRY_MODE,
        )
        self.open_positions[symbol] = pos
        _global_trades_today += 1

        risk_amount = risk_per * qty
        print(
            f"\n[ENTRY] {direction} {symbol}"
            f"\n  Time:    {candle_ts.strftime('%H:%M')} | Session: {session_label}"
            f"\n  Entry:   {entry_price:.2f}"
            f"\n  SL:      {sl_price:.2f}"
            f"\n  TP:      {tp_price:.2f}"
            f"\n  Qty:     {qty} | Risk: {risk_amount:.2f}"
            f"\n  Range%:  {range_width_pct:.2f} | RelVol: {rel_vol:.2f}"
            f"\n  VWAP:    {vwap_val:.2f} | NIFTY: {'BULL' if nifty_bullish else 'BEAR' if nifty_bullish is False else 'N/A'}"
        )

        if not PAPER_TRADING:
            try:
                ps = qty if direction == "LONG" else -qty
                self.client.placesmartorder(
                    strategy=STRATEGY_NAME,
                    symbol=symbol,
                    action="BUY" if direction == "LONG" else "SELL",
                    exchange=EXCHANGE,
                    price_type="MARKET",
                    product=PRODUCT,
                    quantity=qty,
                    position_size=ps,
                )
            except Exception as e:
                print(f"[ORDER ERROR] {symbol}: {e}")

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def _exit_position(self, symbol: str, reason: str, exit_price: float | None = None) -> None:
        pos = self.open_positions.pop(symbol, None)
        if pos is None:
            return

        gross_pnl = 0.0
        if exit_price is not None:
            if pos.direction == "LONG":
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
            else:
                gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        cost_one_way = (
            (pos.entry_price * pos.quantity * ESTIMATED_COST_PCT / 100.0) if pos.quantity > 0 else 0
        )
        estimated_cost = cost_one_way * 2  # entry + exit
        net_pnl = gross_pnl - estimated_cost
        risk_amount = abs(pos.entry_price - pos.sl_price) * pos.quantity if pos.sl_price else 0
        r_multiple = gross_pnl / risk_amount if risk_amount > 0 else 0.0
        duration = None
        if exit_price is not None:
            try:
                exit_dt = pd.Timestamp.now()
                dur = (exit_dt - pos.entry_time).total_seconds() / 60.0
                duration = round(dur, 1)
            except Exception:
                pass

        print(
            f"\n[EXIT] {pos.direction} {symbol}"
            f"\n  Entry:   {pos.entry_price:.2f}"
            f"\n  Exit:    {exit_price:.2f}"
            if exit_price
            else f"\n  Exit:    N/A"
            f"\n  Reason:  {reason}"
            f"\n  Gross:   {gross_pnl:.2f} | Net: {net_pnl:.2f} | R: {r_multiple:.2f}"
            if exit_price
            else ""
        )

        if not PAPER_TRADING and exit_price is not None:
            try:
                action = "SELL" if pos.direction == "LONG" else "BUY"
                self.client.placesmartorder(
                    strategy=STRATEGY_NAME,
                    symbol=symbol,
                    action=action,
                    exchange=EXCHANGE,
                    price_type="MARKET",
                    product=PRODUCT,
                    quantity=pos.quantity,
                    position_size=0,
                )
            except Exception as e:
                print(f"[ORDER ERROR] {symbol}: {e}")

        # Log
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "trade_id": f"{STRATEGY_NAME}_{pos.symbol}_{pos.entry_time.strftime('%H%M%S')}",
            "date": pos.entry_time.strftime("%Y-%m-%d"),
            "symbol": symbol,
            "direction": pos.direction,
            "entry_time": pos.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "entry_price": pos.entry_price,
            "exit_time": now_str,
            "exit_price": exit_price or 0,
            "quantity": pos.quantity,
            "initial_stop": pos.sl_price,
            "target": pos.tp_price,
            "exit_reason": reason,
            "gross_pnl": round(gross_pnl, 2),
            "estimated_cost": round(estimated_cost, 2),
            "net_pnl": round(net_pnl, 2),
            "risk_amount": round(risk_amount, 2),
            "r_multiple": round(r_multiple, 2),
            "duration_minutes": duration or 0,
            "range_high": pos.range_high,
            "range_low": pos.range_low,
            "range_width": pos.range_width,
            "range_width_pct": round(pos.range_width_pct, 2),
            "atr_at_entry": round(pos.atr_at_entry, 2),
            "relative_volume": round(pos.relative_volume, 2),
            "volume_multiplier": RELATIVE_VOLUME_MULTIPLIER,
            "vwap_at_entry": round(pos.vwap_at_entry, 2),
            "vwap_distance": round(pos.vwap_distance, 2),
            "nifty_price": round(pos.nifty_price, 2),
            "nifty_ema": round(pos.nifty_ema, 2),
            "nifty_regime": pos.nifty_regime,
            "session": pos.session,
            "entry_mode": pos.entry_mode,
        }
        append_trade_log(row)

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def _scan_cycle(self) -> None:
        global _retest_setups
        self._check_daily_reset()
        if not self._check_daily_loss():
            return

        now_kol = datetime.now(IST)
        current_time_str = now_kol.strftime("%H:%M")
        # NIFTY once per cycle
        self._get_nifty()

        # Manage open positions
        for symbol in list(self.open_positions.keys()):
            pos = self.open_positions.get(symbol)
            if pos is None:
                continue

            # Square-off
            if current_time_str >= SQUARE_OFF_TIME:
                self._exit_position(symbol, "EOD_SQUARE_OFF")
                continue

            # Get LTP
            ltp = None
            try:
                quotes = self.client.quotes(symbol=symbol, exchange=EXCHANGE)
                if isinstance(quotes, dict) and "ltp" in quotes:
                    ltp = float(quotes["ltp"])
            except Exception:
                pass
            if ltp is None:
                continue

            atr_current = pos.atr_at_entry
            data = self._fetch_data(symbol)
            if data is not None and len(data) > ATR_LENGTH:
                sig = self._calculate_signals(data)
                if len(sig) > 1:
                    atr_current = (
                        float(sig["atr"].iloc[-1])
                        if not pd.isna(sig["atr"].iloc[-1])
                        else atr_current
                    )

            if pos.direction == "LONG":
                if ltp <= pos.sl_price:
                    self._exit_position(symbol, "STOP_LOSS", ltp)
                    continue
                if ltp >= pos.tp_price:
                    self._exit_position(symbol, "TARGET", ltp)
                    continue
                if USE_TRAILING:
                    new_trail = ltp - atr_current * TRAIL_ATR_MULTIPLIER
                    if pos.trail_price is None or new_trail > pos.trail_price:
                        pos.trail_price = new_trail
                    effective_sl = max(pos.sl_price, pos.trail_price)
                    if ltp <= effective_sl:
                        self._exit_position(symbol, "TRAILING_STOP", ltp)
                        continue
            else:
                if ltp >= pos.sl_price:
                    self._exit_position(symbol, "STOP_LOSS", ltp)
                    continue
                if ltp <= pos.tp_price:
                    self._exit_position(symbol, "TARGET", ltp)
                    continue
                if USE_TRAILING:
                    new_trail = ltp + atr_current * TRAIL_ATR_MULTIPLIER
                    if pos.trail_price is None or new_trail < pos.trail_price:
                        pos.trail_price = new_trail
                    effective_sl = min(pos.sl_price, pos.trail_price)
                    if ltp >= effective_sl:
                        self._exit_position(symbol, "TRAILING_STOP", ltp)
                        continue

        # No new entries after cutoff
        if current_time_str >= ENTRY_CUTOFF:
            return

        # Scan symbols for entries
        for symbol in self.symbols:
            if symbol in self.open_positions:
                continue
            if _global_trades_today >= MAX_TRADES_PER_DAY:
                break
            if not self._check_daily_loss():
                break

            df = self._fetch_data(symbol)
            if df is None or df.empty:
                continue

            signals = self._calculate_signals(df)
            if len(signals) < RANGE_BARS + 5:
                continue

            # Last closed candle
            current = signals.iloc[-2]
            candle_ts = signals.index[-2]

            # Duplicate candle guard
            cs = self.candle_states[symbol]
            if cs.last_processed == candle_ts:
                continue
            cs.last_processed = candle_ts

            # Session filter (based on candle timestamp, not wall clock)
            candle_time_str = candle_ts.strftime("%H:%M")
            in_session1 = SESSION1_START <= candle_time_str < SESSION1_END
            in_session2 = SESSION2_START <= candle_time_str < SESSION2_END
            if not (in_session1 or in_session2):
                continue

            # NIFTY at exact candle time
            nifty_bullish, nifty_price, nifty_ema = self._nifty_at_candle(candle_ts)

            # Time-of-day volume
            if USE_TIME_OF_DAY_VOLUME:
                slot = candle_time_str
                cur_vol = float(current.get("volume", 0))
                rel_vol_tod = get_relative_volume_tod(slot, cur_vol)
                vol_conf = rel_vol_tod >= RELATIVE_VOLUME_MULTIPLIER
            else:
                vol_conf = bool(current["volume_confirmed"])

            is_tight = bool(current["is_tight_range"])
            long_bk = bool(current["long_breakout"])
            short_bk = bool(current["short_breakout"])
            long_vw = bool(current.get("long_vwap", True))
            short_vw = bool(current.get("short_vwap", True))

            long_mf = not USE_MARKET_FILTER or (nifty_bullish is True)
            short_mf = not USE_MARKET_FILTER or (nifty_bullish is False)

            if not (is_tight and vol_conf):
                # Also clean up stale retest setups
                if symbol in _retest_setups:
                    _retest_setups.pop(symbol, None)
                continue

            nifty_info = (nifty_bullish, nifty_price, nifty_ema)

            if ENTRY_MODE == "Breakout":
                if long_bk and long_vw and long_mf:
                    self._enter(symbol, "LONG", current, nifty_info)
                elif short_bk and short_vw and short_mf:
                    self._enter(symbol, "SHORT", current, nifty_info)
            else:
                # Retest mode
                self._handle_retest(
                    symbol,
                    current,
                    long_bk,
                    short_bk,
                    long_vw,
                    short_vw,
                    long_mf,
                    short_mf,
                    nifty_info,
                )

    def _handle_retest(
        self,
        symbol: str,
        candle: pd.Series,
        long_bk: bool,
        short_bk: bool,
        long_vw: bool,
        short_vw: bool,
        long_mf: bool,
        short_mf: bool,
        nifty_info: tuple,
    ) -> None:
        global _retest_setups
        setup = _retest_setups.get(symbol)
        candle_ts = candle.name

        if setup is not None:
            bars_waited = 0
            try:
                bars_waited = (candle_ts - setup["ts"]).total_seconds() / 300
            except Exception:
                pass
            if bars_waited > RETEST_MAX_BARS:
                _retest_setups.pop(symbol, None)
                return

            atr_setup = setup["atr"]
            tol = atr_setup * RETEST_TOLERANCE_ATR

            if setup["direction"] == "LONG":
                retest_low = candle["low"] <= setup["level"] + tol
                close_above = candle["close"] > setup["level"]
                close_above_open = candle["close"] > candle["open"]
                if retest_low and close_above and close_above_open and long_vw and long_mf:
                    self._enter(symbol, "LONG", candle, nifty_info)
                    _retest_setups.pop(symbol, None)
                    return
                if candle["close"] < setup["level"] - atr_setup:
                    _retest_setups.pop(symbol, None)
                    return
            else:
                retest_high = candle["high"] >= setup["level"] - tol
                close_below = candle["close"] < setup["level"]
                close_below_open = candle["close"] < candle["open"]
                if retest_high and close_below and close_below_open and short_vw and short_mf:
                    self._enter(symbol, "SHORT", candle, nifty_info)
                    _retest_setups.pop(symbol, None)
                    return
                if candle["close"] > setup["level"] + atr_setup:
                    _retest_setups.pop(symbol, None)
                    return

        # Fresh breakout detection
        if long_bk:
            _retest_setups[symbol] = {
                "direction": "LONG",
                "level": float(candle["range_high"]),
                "atr": float(candle["atr"]),
                "ts": candle_ts,
            }
        elif short_bk:
            _retest_setups[symbol] = {
                "direction": "SHORT",
                "level": float(candle["range_low"]),
                "atr": float(candle["atr"]),
                "ts": candle_ts,
            }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        print(f"[RUN] {STRATEGY_NAME} | Paper={PAPER_TRADING}")

        if USE_TIME_OF_DAY_VOLUME:
            client_for_profile = api(api_key=API_KEY, host=API_HOST)
            build_volume_profile(self.symbols, client_for_profile)

        while True:
            try:
                now = datetime.now(IST)
                current_time_str = now.strftime("%H:%M")

                if now.weekday() >= 5:
                    time.sleep(120)
                    continue

                if current_time_str >= SQUARE_OFF_TIME:
                    for sym in list(self.open_positions.keys()):
                        self._exit_position(sym, "EOD_SQUARE_OFF")
                    print("[EOD] Market closed")
                    self._print_daily_summary()
                    break

                self._scan_cycle()
                self._clear_stale_cache()

                open_count = len(self.open_positions)
                print(
                    f"[STATUS] {current_time_str} Open={open_count} TradesToday={_global_trades_today}"
                )

            except KeyboardInterrupt:
                print("\n[STOP] User interrupt")
                self._print_daily_summary()
                break
            except Exception as e:
                print(f"[ERROR] {e}")

            time.sleep(CHECK_INTERVAL)

    def _print_daily_summary(self) -> None:
        if not os.path.isfile(TRADE_LOG_PATH):
            return
        try:
            log = pd.read_csv(TRADE_LOG_PATH)
            if log.empty:
                return
            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            today_log = log[log["date"] == today_str]
            if today_log.empty:
                return
            n = len(today_log)
            wins = (today_log["gross_pnl"] > 0).sum()
            losses = (today_log["gross_pnl"] < 0).sum()
            gross = today_log["gross_pnl"].sum()
            net = today_log["net_pnl"].sum()
            avg_r = today_log["r_multiple"].mean()
            best = today_log["gross_pnl"].max()
            worst = today_log["gross_pnl"].min()
            print(f"\n{'=' * 60}")
            print("DAILY SUMMARY")
            print(f"{'=' * 60}")
            print(f"Trades:  {n} | Win: {wins} | Loss: {losses}")
            if n:
                print(f"Win%:   {wins / n * 100:.1f}%")
            print(f"Gross:  {gross:.2f}")
            print(f"Net:    {net:.2f}")
            print(f"Avg R:  {avg_r:.2f}")
            print(f"Best:   {best:.2f} | Worst: {worst:.2f}")
            print(f"{'=' * 60}\n")
        except Exception:
            pass


# ============================================================================
# ENTRY
# ============================================================================

if __name__ == "__main__":
    scalper = RangeBreakoutScalper()
    scalper.run()
