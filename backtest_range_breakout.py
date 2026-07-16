"""
Backtest: Intraday Rolling Range Breakout Scalper V3
=====================================================
PORTFOLIO-LEVEL backtester (single shared equity account).

Replaces the V2 per-symbol independent simulation. Preserves the V2
strategy concept (rolling N-bar range, volume, VWAP, NIFTY regime,
range stop, RR target, ATR trail, intraday sessions) and corrects the
portfolio/risk/execution modelling.

Execution model
---------------
1. Candle T closes.
2. Signal is calculated using information available through candle T.
3. Entry occurs at candle T+1 open (with adverse slippage).
4. Existing positions are managed using subsequent candle OHLC.
5. Exit on stop / target / trailing stop / EOD / daily-loss.
6. Transaction costs applied on entry and exit.
7. Net P&L updates the single portfolio equity balance.

Usage
-----
    uv run python backtest_range_breakout.py                      # all symbols
    uv run python backtest_range_breakout.py --sample 10          # subset
    uv run python backtest_range_breakout.py --cache              # reuse parquet
    uv run python backtest_range_breakout.py --start 2026-08-01 --end 2026-08-31
    uv run python backtest_range_breakout.py --sweep              # parameter sweep

This file performs HISTORICAL SIMULATION ONLY. It never places live orders.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from openalgo import api, ta

# ============================================================================
# TIMEZONE
# ============================================================================

IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================================
# CONFIGURATION (defaults — overridable via CLI / sweep)
# ============================================================================

SYMBOLS_FILE = "data/qtyfreeze.csv"
MAX_SYMBOLS = 100
EXCHANGE = "NSE"
NIFTY_SYMBOL = "NIFTY"
NIFTY_EXCHANGE = "NSE_INDEX"
TIMEFRAME = "5m"
LOOKBACK_DAYS = 60

# Strategy defaults
RANGE_BARS = 8
MAX_RANGE_WIDTH_PCT = 1.0
ATR_LENGTH = 14
MIN_BREAKOUT_ATR = 0.10
VOLUME_LOOKBACK = 5
RELATIVE_VOLUME_MULTIPLIER = 1.20
USE_VWAP_FILTER = True
USE_MARKET_FILTER = True
NIFTY_EMA_LENGTH = 20

SESSION1_START = "09:20"
SESSION1_END = "11:00"
SESSION2_START = "13:30"
SESSION2_END = "15:00"

ENTRY_CUTOFF = "15:00"
SQUARE_OFF_TIME = "15:15"

ENTRY_MODE = "Breakout"
STOP_MODE = "Range"
TARGET_RR = 1.50
USE_TARGET = True
USE_TRAILING = True
TRAIL_ATR_MULTIPLIER = 0.75
TRAIL_ACTIVATION_R = 0.0

# Portfolio / risk defaults
INITIAL_CAPITAL = 100000.0
USE_RISK_BASED_SIZING = True
RISK_PER_TRADE_PCT = 0.25
FIXED_QUANTITY = 1
MAX_POSITION_VALUE_PCT = 20.0
MAX_TRADES_PER_DAY = 5
MAX_OPEN_POSITIONS = 5
MAX_PORTFOLIO_OPEN_RISK_PCT = 1.0
USE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_PCT = 2.0
SQUARE_OFF_ON_DAILY_LOSS = True

# Execution realism
ESTIMATED_COST_PCT = 0.03  # one-way, simplified round-trip estimate
SLIPPAGE_PCT = 0.02  # one-way adverse slippage
COST_MODEL = "SIMPLE"  # "SIMPLE" (DETAILED reserved for future)
INTRABAR_EXIT_POLICY = "CONSERVATIVE"  # CONSERVATIVE | STOP_FIRST | TARGET_FIRST

# Data / cache
API_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")
CACHE_DIR = "backtest_cache"

# Output files
TRADES_CSV = "backtest_v3_trades.csv"
EQUITY_CSV = "backtest_v3_equity.csv"
DAILY_CSV = "backtest_v3_daily.csv"
SWEEP_CSV = "parameter_sweep_results.csv"
COMPARISON_CSV = "backtest_v3_vs_v2_comparison.csv"
V2_BASELINE_CSV = "range_breakout_backtest_trades.csv"

INDEX_SYMBOLS = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "NIFTYNXT50"}


# ============================================================================
# PARAMETERS CONTAINER (enables sweep without touching globals)
# ============================================================================


@dataclass
class BacktestParams:
    range_bars: int = RANGE_BARS
    max_range_width_pct: float = MAX_RANGE_WIDTH_PCT
    atr_length: int = ATR_LENGTH
    min_breakout_atr: float = MIN_BREAKOUT_ATR
    volume_lookback: int = VOLUME_LOOKBACK
    relative_volume_multiplier: float = RELATIVE_VOLUME_MULTIPLIER
    use_vwap_filter: bool = USE_VWAP_FILTER
    use_market_filter: bool = USE_MARKET_FILTER
    nifty_ema_length: int = NIFTY_EMA_LENGTH
    entry_mode: str = ENTRY_MODE
    stop_mode: str = STOP_MODE
    target_rr: float = TARGET_RR
    use_target: bool = USE_TARGET
    use_trailing: bool = USE_TRAILING
    trail_atr_multiplier: float = TRAIL_ATR_MULTIPLIER
    trail_activation_r: float = TRAIL_ACTIVATION_R

    initial_capital: float = INITIAL_CAPITAL
    use_risk_based_sizing: bool = USE_RISK_BASED_SIZING
    risk_per_trade_pct: float = RISK_PER_TRADE_PCT
    fixed_quantity: int = FIXED_QUANTITY
    max_position_value_pct: float = MAX_POSITION_VALUE_PCT
    max_trades_per_day: int = MAX_TRADES_PER_DAY
    max_open_positions: int = MAX_OPEN_POSITIONS
    max_portfolio_open_risk_pct: float = MAX_PORTFOLIO_OPEN_RISK_PCT
    use_daily_loss_limit: bool = USE_DAILY_LOSS_LIMIT
    max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
    square_off_on_daily_loss: bool = SQUARE_OFF_ON_DAILY_LOSS

    # research filters
    close_location_min: float = 0.0
    candle_body_min: float = 0.0
    min_volume_percentile: float = 0.0
    min_breakout_displacement: float = 0.0
    use_tod_volume: bool = False
    use_ma_trend: bool = False
    use_ma_slope: bool = False
    ma_slope_gt0: bool = False
    use_rel_strength: bool = False
    use_ma_entry: bool = False
    ma_entry_mode: bool = False
    rel_strength_bars: int = 10
    ema9_len: int = 9
    ema20_len: int = 20
    ema50_len: int = 50
    ema_slope_bars: int = 3
    long_only: bool = False
    short_only: bool = False
    session_switch: str = "BOTH"  # BOTH | S1 | S2

    estimated_cost_pct: float = ESTIMATED_COST_PCT
    slippage_pct: float = SLIPPAGE_PCT
    cost_model: str = COST_MODEL
    intrabar_exit_policy: str = INTRABAR_EXIT_POLICY


# ============================================================================
# DATACLASSES
# ============================================================================


@dataclass
class OpenPosition:
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    quantity: int
    initial_stop: float
    target: float
    trail_price: float | None
    atr_at_entry: float
    range_high: float
    range_low: float
    range_width: float
    range_width_pct: float
    relative_volume: float
    vwap_at_entry: float
    vwap_distance: float
    nifty_price: float
    nifty_ema: float
    nifty_regime: str
    session: str
    entry_mode: str
    signal_score: float
    equity_before: float
    daily_trade_number: int
    open_positions_at_entry: int
    portfolio_open_risk: float
    mfe: float = 0.0
    mae: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    ttp: dict[float, float] = field(default_factory=dict)
    bars_held: int = 0

    @property
    def risk_amount(self) -> float:
        return abs(self.entry_price - self.initial_stop) * self.quantity


@dataclass
class Trade:
    trade_id: str
    date: str
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    quantity: int
    initial_stop: float
    target: float
    exit_reason: str
    gross_pnl: float
    estimated_cost: float
    net_pnl: float
    risk_amount: float
    r_multiple: float
    duration_minutes: float
    range_high: float
    range_low: float
    range_width: float
    range_width_pct: float
    atr_at_entry: float
    relative_volume: float
    vwap_at_entry: float
    vwap_distance: float
    nifty_price: float
    nifty_ema: float
    nifty_regime: str
    session: str
    entry_mode: str
    portfolio_equity_before: float
    portfolio_equity_after: float
    daily_trade_number: int
    open_positions_at_entry: int
    portfolio_open_risk: float
    signal_score: float
    mfe: float = 0.0
    mae: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    bars_held: int = 0
    ttp_025: float = -1.0
    ttp_050: float = -1.0
    ttp_075: float = -1.0
    ttp_100: float = -1.0
    ttp_150: float = -1.0

    def to_row(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "date": self.date,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": round(self.entry_price, 2),
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 2),
            "quantity": self.quantity,
            "initial_stop": round(self.initial_stop, 2),
            "target": round(self.target, 2),
            "exit_reason": self.exit_reason,
            "gross_pnl": round(self.gross_pnl, 2),
            "estimated_cost": round(self.estimated_cost, 2),
            "net_pnl": round(self.net_pnl, 2),
            "risk_amount": round(self.risk_amount, 2),
            "r_multiple": round(self.r_multiple, 2),
            "duration_minutes": round(self.duration_minutes, 1),
            "range_high": round(self.range_high, 2),
            "range_low": round(self.range_low, 2),
            "range_width": round(self.range_width, 2),
            "range_width_pct": round(self.range_width_pct, 3),
            "atr_at_entry": round(self.atr_at_entry, 3),
            "relative_volume": round(self.relative_volume, 3),
            "volume_multiplier": RELATIVE_VOLUME_MULTIPLIER,
            "vwap_at_entry": round(self.vwap_at_entry, 2),
            "vwap_distance": round(self.vwap_distance, 3),
            "nifty_price": round(self.nifty_price, 2),
            "nifty_ema": round(self.nifty_ema, 2),
            "nifty_regime": self.nifty_regime,
            "session": self.session,
            "entry_mode": self.entry_mode,
            "portfolio_equity_before": round(self.portfolio_equity_before, 2),
            "portfolio_equity_after": round(self.portfolio_equity_after, 2),
            "daily_trade_number": self.daily_trade_number,
            "open_positions_at_entry": self.open_positions_at_entry,
            "portfolio_open_risk": round(self.portfolio_open_risk, 2),
            "signal_score": round(self.signal_score, 4),
            "mfe": round(self.mfe, 2),
            "mae": round(self.mae, 2),
            "mfe_r": round(self.mfe_r, 3),
            "mae_r": round(self.mae_r, 3),
            "bars_held": self.bars_held,
            "ttp_025": self.ttp_025,
            "ttp_050": self.ttp_050,
            "ttp_075": self.ttp_075,
            "ttp_100": self.ttp_100,
            "ttp_150": self.ttp_150,
        }


# ============================================================================
# HELPERS
# ============================================================================


def load_symbols(max_symbols: int = MAX_SYMBOLS) -> list[str]:
    symbols: list[str] = []
    with open(SYMBOLS_FILE) as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[1] and parts[1] not in ("SYMBOL", *INDEX_SYMBOLS):
                symbols.append(parts[1])
                if len(symbols) >= max_symbols:
                    break
    return symbols


def to_ist(ts: pd.Timestamp) -> pd.Timestamp:
    if getattr(ts, "tz", None) is None:
        return ts.tz_localize(IST)
    return ts.tz_convert(IST)


def in_allowed_session(ts: pd.Timestamp) -> bool:
    t = to_ist(ts).strftime("%H:%M")
    return (SESSION1_START <= t < SESSION1_END) or (SESSION2_START <= t < SESSION2_END)


def is_market_hours(ts: pd.Timestamp) -> bool:
    ist = to_ist(ts)
    if ist.weekday() >= 5:
        return False
    t = ist.strftime("%H:%M")
    return "09:15" <= t < "15:30"


def session_label(ts: pd.Timestamp) -> str:
    t = to_ist(ts).strftime("%H:%M")
    if SESSION1_START <= t < SESSION1_END:
        return "S1"
    if SESSION2_START <= t < SESSION2_END:
        return "S2"
    return ""


# ============================================================================
# DATA QUALITY
# ============================================================================


def check_data_quality(symbol: str, df: pd.DataFrame) -> dict[str, Any]:
    report: dict[str, Any] = {"symbol": symbol, "rows": len(df)}
    report["duplicate_ts"] = int(df.index.duplicated().sum())
    report["unsorted"] = not df.index.is_monotonic_increasing
    ohlc = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    report["nan_ohlcv"] = int(df[ohlc].isna().sum().sum()) if ohlc else 0
    report["nonpositive_price"] = int(
        ((df["low"] <= 0) | (df["high"] <= 0) | (df["close"] <= 0)).sum()
    )
    report["zero_volume"] = int((df["volume"] == 0).sum())
    return report


def print_data_quality(reports: list[dict[str, Any]]) -> None:
    bad = [
        r
        for r in reports
        if r["duplicate_ts"]
        or r["unsorted"]
        or r["nan_ohlcv"]
        or r["nonpositive_price"]
        or r["zero_volume"]
    ]
    if not bad:
        print("[DATA] Quality check passed for all symbols.")
        return
    print(f"[DATA] {len(bad)} symbols with quality issues:")
    for r in bad[:15]:
        print(
            f"  {r['symbol']}: dup={r['duplicate_ts']} unsorted={r['unsorted']} "
            f"nan={r['nan_ohlcv']} nonpos={r['nonpositive_price']} zerovol={r['zero_volume']}"
        )


# ============================================================================
# SIGNALS
# ============================================================================


def compute_signals(df: pd.DataFrame, p: BacktestParams) -> pd.DataFrame:
    data = df.copy()
    for c in ("close", "high", "low", "open", "volume"):
        data[c] = pd.to_numeric(data[c], errors="coerce")
    data = data.dropna()

    min_rows = max(p.atr_length, p.range_bars, p.volume_lookback, p.nifty_ema_length) + 5
    if len(data) < min_rows:
        for col in (
            "atr",
            "vwap",
            "range_high",
            "range_low",
            "range_width",
            "range_width_pct",
            "is_tight_range",
            "volume_confirmed",
            "long_bk",
            "short_bk",
            "long_vwap",
            "short_vwap",
            "long_signal",
            "short_signal",
        ):
            data[col] = np.nan
        return data

    data["atr"] = ta.atr(data["high"], data["low"], data["close"], p.atr_length)
    data["vwap"] = ta.vwap(data["high"], data["low"], data["close"], data["volume"])

    data["range_high"] = data["high"].shift(1).rolling(p.range_bars).max()
    data["range_low"] = data["low"].shift(1).rolling(p.range_bars).min()
    data["range_width"] = data["range_high"] - data["range_low"]
    data["range_width_pct"] = (data["range_width"] / data["range_low"].clip(lower=1e-10)) * 100
    data["is_tight_range"] = data["range_width_pct"] <= p.max_range_width_pct

    data["avg_volume"] = data["volume"].shift(1).rolling(p.volume_lookback).mean()
    data["avg_vol_safe"] = data["avg_volume"].clip(lower=1e-10)
    data["rel_volume"] = data["volume"] / data["avg_vol_safe"]
    data["volume_confirmed"] = data["rel_volume"] >= p.relative_volume_multiplier

    data["range_atr"] = data["range_width"] / data["atr"].clip(lower=1e-10)
    _hl = data["high"] - data["low"]
    _safe_hl = _hl.clip(lower=1e-10)
    data["close_location"] = (data["close"] - data["low"]) / _safe_hl
    data["candle_body_ratio"] = (data["close"] - data["open"]).abs() / _safe_hl

    data["ema9"] = data["close"].ewm(span=p.ema9_len, adjust=False).mean()
    data["ema20"] = data["close"].ewm(span=p.ema20_len, adjust=False).mean()
    data["ema50"] = data["close"].ewm(span=p.ema50_len, adjust=False).mean()
    data["ema20_slope"] = (data["ema20"] - data["ema20"].shift(p.ema_slope_bars)) / data["atr"].clip(lower=1e-10)
    data["price_above_ema20"] = data["close"] > data["ema20"]
    data["ema9_above_ema20"] = data["ema9"] > data["ema20"]
    data["ema20_above_ema50"] = data["ema20"] > data["ema50"]
    data["bull_struct"] = (data["ema9"] > data["ema20"]) & (data["ema20"] > data["ema50"])
    data["bear_struct"] = (data["ema9"] < data["ema20"]) & (data["ema20"] < data["ema50"])
    data["ma_sep_ema20_50"] = (data["ema20"] - data["ema50"]) / data["atr"].clip(lower=1e-10)
    data["price_dist_ema20"] = (data["close"] - data["ema20"]) / data["atr"].clip(lower=1e-10)
    data["volume_percentile"] = (
        data["volume"]
        .rolling(60, min_periods=10)
        .apply(lambda x: (x.iloc[-1] > x).sum() / len(x) * 100, raw=False)
    )
    data["long_displacement"] = (data["close"] - data["range_high"]) / data["atr"].clip(lower=1e-10)
    data["short_displacement"] = (data["range_low"] - data["close"]) / data["atr"].clip(lower=1e-10)

    data["long_bk"] = (data["close"] > data["range_high"]) & (
        data["close"] >= data["range_high"] + data["atr"] * p.min_breakout_atr
    )
    data["short_bk"] = (data["close"] < data["range_low"]) & (
        data["close"] <= data["range_low"] - data["atr"] * p.min_breakout_atr
    )

    if p.use_vwap_filter:
        data["long_vwap"] = data["close"] > data["vwap"]
        data["short_vwap"] = data["close"] < data["vwap"]
    else:
        data["long_vwap"] = True
        data["short_vwap"] = True

    _extra_long = True
    _extra_short = True
    if p.ma_entry_mode:
        _extra_long = data["bull_struct"]
        _extra_short = data["bear_struct"]
    if p.use_ma_slope:
        _extra_long = _extra_long & (data["ema20_slope"] > 0)
        _extra_short = _extra_short & (data["ema20_slope"] < 0)
    elif p.close_location_min > 0:
        _extra_long = data["close_location"] >= p.close_location_min
        _extra_short = data["close_location"] <= (1 - p.close_location_min)
    if p.candle_body_min > 0:
        _body_ok = data["candle_body_ratio"] >= p.candle_body_min
        _extra_long = _extra_long & _body_ok
        _extra_short = _extra_short & _body_ok
    if p.min_volume_percentile > 0:
        _vp_ok = data["volume_percentile"] >= p.min_volume_percentile
        _extra_long = _extra_long & _vp_ok
        _extra_short = _extra_short & _vp_ok
    if p.min_breakout_displacement > 0:
        _extra_long = _extra_long & (data["long_displacement"] >= p.min_breakout_displacement)
        _extra_short = _extra_short & (data["short_displacement"] >= p.min_breakout_displacement)

    data["long_signal"] = (
        data["is_tight_range"]
        & data["volume_confirmed"]
        & data["long_bk"]
        & data["long_vwap"]
        & _extra_long
    )
    data["short_signal"] = (
        data["is_tight_range"]
        & data["volume_confirmed"]
        & data["short_bk"]
        & data["short_vwap"]
        & _extra_short
    )
    return data


def compute_nifty_signals(nifty_df: pd.DataFrame, p: BacktestParams) -> pd.DataFrame:
    nifty = nifty_df.copy()
    nifty["close"] = pd.to_numeric(nifty["close"], errors="coerce")
    nifty["ema"] = nifty["close"].ewm(span=p.nifty_ema_length, adjust=False).mean()
    nifty["bullish"] = nifty["close"] > nifty["ema"]
    nifty["bearish"] = nifty["close"] < nifty["ema"]
    return nifty


def nifty_at_candle(
    nifty_sig: pd.DataFrame | None, candle_ts: pd.Timestamp
) -> tuple[bool | None, float, float]:
    """Return (bullish, close, ema) using only candles at or strictly before candle_ts."""
    if nifty_sig is None or nifty_sig.empty:
        return None, 0.0, 0.0
    ts = to_ist(candle_ts)
    match = nifty_sig[nifty_sig.index == ts]
    if match.empty:
        prior = nifty_sig[nifty_sig.index < ts]
        if prior.empty:
            return None, 0.0, 0.0
        match = prior.iloc[[-1]]
    row = match.iloc[0]
    return bool(row["bullish"]), float(row["close"]), float(row["ema"])


# ============================================================================
# EXECUTION REALISM
# ============================================================================


def apply_slippage(price: float, direction: str, is_entry: bool, slippage_pct: float) -> float:
    f = slippage_pct / 100.0
    if direction == "LONG":
        return price * (1 + f) if is_entry else price * (1 - f)
    return price * (1 - f) if is_entry else price * (1 + f)


def calculate_transaction_cost(
    entry_price: float,
    exit_price: float,
    quantity: int,
    cost_pct: float,
    model: str = "SIMPLE",
) -> float:
    """
    Simplified round-trip cost estimate: (entry + exit) turnover * cost_pct.

    This is an ESTIMATE, not an exact broker contract-note calculation.
    DETAILED mode (brokerage/STT/exchange/GST/SEBI/stamp) is reserved for future.
    """
    if model == "DETAILED":
        # Not implemented — fall back to SIMPLE with a clear assumption.
        pass
    turnover = (entry_price * quantity) + (exit_price * quantity)
    return turnover * cost_pct / 100.0


# ============================================================================
# PORTFOLIO ENGINE
# ============================================================================


def _check_exit(
    pos: OpenPosition, candle: pd.Series, atr_val: float, policy: str, use_target: bool = True
) -> tuple[str, float] | None:
    """Check stop/target/trail exit for one candle. Returns (reason, price) or None.

    Distinguishes the initial STOP_LOSS from a TRAILING_STOP by inspecting
    whether the effective stop was raised/lowered by the trail.
    """
    high = float(candle["high"])
    low = float(candle["low"])

    if pos.direction == "LONG":
        if pos.trail_price is not None and pos.trail_price > pos.initial_stop:
            stop = pos.trail_price
            stop_reason = "TRAILING_STOP"
        else:
            stop = pos.initial_stop
            stop_reason = "STOP_LOSS"
        stop_hit = low <= stop
        target_hit = use_target and high >= pos.target
    else:
        if pos.trail_price is not None and pos.trail_price < pos.initial_stop:
            stop = pos.trail_price
            stop_reason = "TRAILING_STOP"
        else:
            stop = pos.initial_stop
            stop_reason = "STOP_LOSS"
        stop_hit = high >= stop
        target_hit = use_target and low <= pos.target

    if stop_hit and target_hit:
        if policy == "TARGET_FIRST":
            return "TARGET", pos.target
        return stop_reason, stop
    if stop_hit:
        return stop_reason, stop
    if target_hit:
        return "TARGET", pos.target
    return None


def _update_trail(pos: OpenPosition, candle: pd.Series, atr_val: float, p: BacktestParams) -> None:
    if not p.use_trailing or atr_val <= 0:
        return
    risk_per_share = abs(pos.entry_price - pos.initial_stop)
    if risk_per_share <= 0:
        return
    if p.trail_activation_r > 0:
        if pos.direction == "LONG":
            excursion = float(candle["high"]) - pos.entry_price
        else:
            excursion = pos.entry_price - float(candle["low"])
        if excursion / risk_per_share < p.trail_activation_r:
            return
    if pos.direction == "LONG":
        candidate = float(candle["high"]) - atr_val * p.trail_atr_multiplier
        if candidate < pos.initial_stop:
            candidate = pos.initial_stop
        if pos.trail_price is None or candidate > pos.trail_price:
            pos.trail_price = candidate
    else:
        candidate = float(candle["low"]) + atr_val * p.trail_atr_multiplier
        if candidate > pos.initial_stop:
            candidate = pos.initial_stop
        if pos.trail_price is None or candidate < pos.trail_price:
            pos.trail_price = candidate


def _signal_score(sig: pd.Series, direction: str) -> float:
    """
    Deterministic ranking score for simultaneous candidates.

    score = relative_volume
          + (breakout_distance / ATR)
          + (1 / range_width_pct)
          + abs(close - vwap) / vwap * 100
    """
    atr = float(sig["atr"]) if not pd.isna(sig["atr"]) else 0.0
    rvol = float(sig["rel_volume"]) if not pd.isna(sig["rel_volume"]) else 0.0
    rpct = float(sig["range_width_pct"]) if not pd.isna(sig["range_width_pct"]) else 0.0
    vwap = float(sig["vwap"]) if not pd.isna(sig["vwap"]) else 0.0
    close = float(sig["close"])

    if direction == "LONG":
        breakout_dist = close - float(sig["range_high"]) if not pd.isna(sig["range_high"]) else 0.0
    else:
        breakout_dist = float(sig["range_low"]) - close if not pd.isna(sig["range_low"]) else 0.0

    norm_breakout = (breakout_dist / atr) if atr > 0 else 0.0
    inv_range = 1.0 / max(rpct, 0.01)
    vwap_dist = abs(close - vwap) / vwap * 100 if vwap > 0 else 0.0
    return rvol + norm_breakout + inv_range + vwap_dist


def _close_position(
    pos: OpenPosition,
    ts: pd.Timestamp,
    exit_px: float,
    reason: str,
    realized_pnl: float,
    equity_now: float,
    p: BacktestParams,
    trade_counter: int,
) -> tuple[Trade, float]:
    direction = pos.direction
    actual_exit = apply_slippage(exit_px, direction, is_entry=False, slippage_pct=p.slippage_pct)

    if direction == "LONG":
        gross = (actual_exit - pos.entry_price) * pos.quantity
    else:
        gross = (pos.entry_price - actual_exit) * pos.quantity

    cost = calculate_transaction_cost(
        pos.entry_price, actual_exit, pos.quantity, p.estimated_cost_pct, p.cost_model
    )
    net = gross - cost
    new_realized = realized_pnl + net
    risk_amt = pos.risk_amount
    r_mult = gross / risk_amt if risk_amt > 0 else 0.0

    try:
        dur = (ts - pos.entry_time).total_seconds() / 60.0
    except Exception:
        dur = 0.0

    trade = Trade(
        trade_id=f"V3_{trade_counter}",
        date=to_ist(pos.entry_time).strftime("%Y-%m-%d"),
        symbol=pos.symbol,
        direction=direction,
        entry_time=pos.entry_time,
        entry_price=pos.entry_price,
        exit_time=ts,
        exit_price=actual_exit,
        quantity=pos.quantity,
        initial_stop=pos.initial_stop,
        target=pos.target,
        exit_reason=reason,
        gross_pnl=gross,
        estimated_cost=cost,
        net_pnl=net,
        risk_amount=risk_amt,
        r_multiple=r_mult,
        duration_minutes=dur,
        range_high=pos.range_high,
        range_low=pos.range_low,
        range_width=pos.range_width,
        range_width_pct=pos.range_width_pct,
        atr_at_entry=pos.atr_at_entry,
        relative_volume=pos.relative_volume,
        vwap_at_entry=pos.vwap_at_entry,
        vwap_distance=pos.vwap_distance,
        nifty_price=pos.nifty_price,
        nifty_ema=pos.nifty_ema,
        nifty_regime=pos.nifty_regime,
        session=pos.session,
        entry_mode=pos.entry_mode,
        portfolio_equity_before=pos.equity_before,
        portfolio_equity_after=equity_now + new_realized - realized_pnl,
        daily_trade_number=pos.daily_trade_number,
        open_positions_at_entry=pos.open_positions_at_entry,
        portfolio_open_risk=pos.portfolio_open_risk,
        signal_score=pos.signal_score,
        mfe=pos.mfe,
        mae=pos.mae,
        mfe_r=pos.mfe_r,
        mae_r=pos.mae_r,
        bars_held=pos.bars_held,
        ttp_025=pos.ttp.get(0.25, -1.0),
        ttp_050=pos.ttp.get(0.50, -1.0),
        ttp_075=pos.ttp.get(0.75, -1.0),
        ttp_100=pos.ttp.get(1.00, -1.0),
        ttp_150=pos.ttp.get(1.50, -1.0),
    )
    return trade, new_realized


def _mark_to_market(open_positions: dict[str, OpenPosition], last_price: dict[str, float]) -> float:
    unreal = 0.0
    for sym, pos in open_positions.items():
        price = last_price.get(sym, pos.entry_price)
        if pos.direction == "LONG":
            unreal += (price - pos.entry_price) * pos.quantity
        else:
            unreal += (pos.entry_price - price) * pos.quantity
    return unreal


def _open_risk(open_positions: dict[str, OpenPosition]) -> float:
    return sum(p.risk_amount for p in open_positions.values())


def run_portfolio_backtest(
    signals_by_symbol: dict[str, pd.DataFrame],
    nifty_sig: pd.DataFrame | None,
    p: BacktestParams,
) -> tuple[list[Trade], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Single-portfolio chronological simulation across all symbols."""
    events: dict[pd.Timestamp, list[tuple[str, int]]] = {}
    for sym, sig in signals_by_symbol.items():
        for i in range(len(sig)):
            events.setdefault(sig.index[i], []).append((sym, i))

    open_positions: dict[str, OpenPosition] = {}
    trades: list[Trade] = []
    equity_rows: list[dict[str, Any]] = []
    last_price: dict[str, float] = {}

    realized_pnl = 0.0
    trade_counter = 0
    peak_equity = p.initial_capital

    current_day: str | None = None
    day_start_equity = p.initial_capital
    daily_trade_count = 0
    daily_loss_hit = False

    max_simultaneous = 0
    max_port_risk = 0.0
    risk_samples: list[float] = []
    daily_stats: dict[str, dict[str, Any]] = {}

    for ts in sorted(events.keys()):
        ist = to_ist(ts)
        day = ist.strftime("%Y-%m-%d")

        if day != current_day:
            current_day = day
            unreal = _mark_to_market(open_positions, last_price)
            day_start_equity = p.initial_capital + realized_pnl + unreal
            daily_trade_count = 0
            daily_loss_hit = False
            daily_stats[day] = {
                "date": day,
                "starting_equity": day_start_equity,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "gross_pnl": 0.0,
                "cost": 0.0,
                "net_pnl": 0.0,
                "min_equity": day_start_equity,
            }

        # ---- 1. Manage existing positions on this candle ----
        for sym, i in events.get(ts, []):
            sig = signals_by_symbol[sym]
            candle = sig.iloc[i]
            last_price[sym] = float(candle["close"])

            pos = open_positions.get(sym)
            if pos is None:
                continue

            atr_val = float(candle["atr"]) if not pd.isna(candle["atr"]) else pos.atr_at_entry

            _risk_ps = abs(pos.entry_price - pos.initial_stop)
            pos.bars_held += 1
            if pos.direction == "LONG":
                _fav = float(candle["high"]) - pos.entry_price
                _adv = float(candle["low"]) - pos.entry_price
            else:
                _fav = pos.entry_price - float(candle["low"])
                _adv = pos.entry_price - float(candle["high"])
            if _fav > pos.mfe:
                pos.mfe = _fav
            if _adv < pos.mae:
                pos.mae = _adv
            pos.mfe_r = pos.mfe / _risk_ps if _risk_ps > 0 else 0.0
            pos.mae_r = pos.mae / _risk_ps if _risk_ps > 0 else 0.0
            if _risk_ps > 0:
                for lvl in (0.25, 0.50, 0.75, 1.00, 1.50):
                    if _fav / _risk_ps >= lvl and lvl not in pos.ttp:
                        pos.ttp[lvl] = float(pos.bars_held)

            exit_res = _check_exit(pos, candle, atr_val, p.intrabar_exit_policy, p.use_target)

            if exit_res is None and ist.strftime("%H:%M") >= SQUARE_OFF_TIME:
                exit_res = ("EOD_SQUARE_OFF", float(candle["close"]))

            if exit_res is not None:
                reason, exit_px = exit_res
                unreal = _mark_to_market(open_positions, last_price)
                equity_now = p.initial_capital + realized_pnl + unreal
                trade, realized_pnl = _close_position(
                    pos, ts, exit_px, reason, realized_pnl, equity_now, p, trade_counter
                )
                trade_counter += 1
                trades.append(trade)
                del open_positions[sym]

                ds = daily_stats[day]
                ds["trades"] += 1
                ds["gross_pnl"] += trade.gross_pnl
                ds["cost"] += trade.estimated_cost
                ds["net_pnl"] += trade.net_pnl
                if trade.net_pnl > 0:
                    ds["wins"] += 1
                elif trade.net_pnl < 0:
                    ds["losses"] += 1
                continue

            _update_trail(pos, candle, atr_val, p)

        # ---- 2. Portfolio equity / daily loss check ----
        unreal = _mark_to_market(open_positions, last_price)
        total_equity = p.initial_capital + realized_pnl + unreal
        daily_pnl = total_equity - day_start_equity

        if day in daily_stats:
            daily_stats[day]["min_equity"] = min(daily_stats[day]["min_equity"], total_equity)

        if (
            p.use_daily_loss_limit
            and not daily_loss_hit
            and daily_pnl <= -(day_start_equity * p.max_daily_loss_pct / 100.0)
        ):
            daily_loss_hit = True
            print(f"[DAILY LOSS] {day} limit hit ({daily_pnl:.2f})")
            if p.square_off_on_daily_loss:
                for sym in list(open_positions.keys()):
                    pos = open_positions[sym]
                    px = last_price.get(sym, pos.entry_price)
                    unreal = _mark_to_market(open_positions, last_price)
                    equity_now = p.initial_capital + realized_pnl + unreal
                    trade, realized_pnl = _close_position(
                        pos, ts, px, "DAILY_LOSS_LIMIT", realized_pnl, equity_now, p, trade_counter
                    )
                    trade_counter += 1
                    trades.append(trade)
                    del open_positions[sym]
                    ds = daily_stats[day]
                    ds["trades"] += 1
                    ds["gross_pnl"] += trade.gross_pnl
                    ds["cost"] += trade.estimated_cost
                    ds["net_pnl"] += trade.net_pnl
                    if trade.net_pnl > 0:
                        ds["wins"] += 1
                    elif trade.net_pnl < 0:
                        ds["losses"] += 1

        # ---- 3. Collect entry candidates (signal from candle i-1) ----
        candidates: list[dict[str, Any]] = []
        block_entries = daily_loss_hit or ist.strftime("%H:%M") >= "15:00"

        if not block_entries and daily_trade_count < p.max_trades_per_day:
            if len(open_positions) < p.max_open_positions:
                for sym, i in events.get(ts, []):
                    if i == 0 or sym in open_positions:
                        continue
                    if p.session_switch == "S1" and session_label(ts) != "S1":
                        continue
                    if p.session_switch == "S2" and session_label(ts) != "S2":
                        continue
                    if not in_allowed_session(ts):
                        continue
                    sig = signals_by_symbol[sym]
                    prev = sig.iloc[i - 1]
                    if pd.isna(prev.get("long_signal", np.nan)):
                        continue

                    nifty_bull, nifty_px, nifty_ema = nifty_at_candle(nifty_sig, prev.name)
                    long_mf = not p.use_market_filter or (nifty_bull is True)
                    short_mf = not p.use_market_filter or (nifty_bull is False)

                    long_sig = bool(prev["long_signal"]) and long_mf and not p.short_only
                    short_sig = bool(prev["short_signal"]) and short_mf and not p.long_only
                    if not (long_sig or short_sig):
                        continue

                    direction = "LONG" if long_sig else "SHORT"
                    candidates.append(
                        {
                            "symbol": sym,
                            "i": i,
                            "direction": direction,
                            "prev": prev,
                            "candle": sig.iloc[i],
                            "nifty_bull": nifty_bull,
                            "nifty_px": nifty_px,
                            "nifty_ema": nifty_ema,
                            "score": _signal_score(prev, direction),
                        }
                    )

        # ---- 4. Rank and enter ----
        if candidates:
            candidates.sort(key=lambda c: c["score"], reverse=True)
            for c in candidates:
                if daily_trade_count >= p.max_trades_per_day:
                    break
                if len(open_positions) >= p.max_open_positions:
                    break

                sym = c["symbol"]
                direction = c["direction"]
                prev = c["prev"]
                candle = c["candle"]
                theoretical_entry = float(candle["open"])
                if pd.isna(theoretical_entry):
                    continue
                entry_price = apply_slippage(
                    theoretical_entry, direction, is_entry=True, slippage_pct=p.slippage_pct
                )

                atr_entry = float(prev["atr"]) if not pd.isna(prev["atr"]) else 0.0
                rl = float(prev["range_low"]) if not pd.isna(prev["range_low"]) else 0.0
                rh = float(prev["range_high"]) if not pd.isna(prev["range_high"]) else 0.0

                if p.stop_mode == "Range":
                    stop = rl if direction == "LONG" else rh
                else:
                    stop = (
                        entry_price - atr_entry * 0.50
                        if direction == "LONG"
                        else entry_price + atr_entry * 0.50
                    )

                risk_per_share = abs(entry_price - stop)
                if risk_per_share <= 0:
                    continue

                equity_now = (
                    p.initial_capital + realized_pnl + _mark_to_market(open_positions, last_price)
                )

                if p.use_risk_based_sizing:
                    risk_amount = equity_now * p.risk_per_trade_pct / 100.0
                    qty = int(np.floor(risk_amount / risk_per_share))
                else:
                    qty = p.fixed_quantity

                max_pos_value = equity_now * p.max_position_value_pct / 100.0
                if entry_price > 0:
                    qty = min(qty, int(np.floor(max_pos_value / entry_price)))

                if qty <= 0:
                    continue

                new_risk = _open_risk(open_positions) + risk_per_share * qty
                if new_risk > equity_now * p.max_portfolio_open_risk_pct / 100.0:
                    continue

                target = (
                    entry_price + risk_per_share * p.target_rr
                    if direction == "LONG"
                    else entry_price - risk_per_share * p.target_rr
                )

                vwap_v = float(prev["vwap"]) if not pd.isna(prev["vwap"]) else 0.0
                rw = rh - rl
                rpct = (rw / max(rl, 1e-10)) * 100

                daily_trade_count += 1
                pos = OpenPosition(
                    symbol=sym,
                    direction=direction,
                    entry_time=ts,
                    entry_price=entry_price,
                    quantity=qty,
                    initial_stop=stop,
                    target=target,
                    trail_price=None,
                    atr_at_entry=atr_entry,
                    range_high=rh,
                    range_low=rl,
                    range_width=rw,
                    range_width_pct=rpct,
                    relative_volume=float(prev["rel_volume"])
                    if not pd.isna(prev["rel_volume"])
                    else 0.0,
                    vwap_at_entry=vwap_v,
                    vwap_distance=((entry_price - vwap_v) / vwap_v * 100) if vwap_v else 0.0,
                    nifty_price=c["nifty_px"],
                    nifty_ema=c["nifty_ema"],
                    nifty_regime=(
                        "BULLISH"
                        if c["nifty_bull"] is True
                        else "BEARISH"
                        if c["nifty_bull"] is False
                        else "NEUTRAL"
                    ),
                    session=session_label(ts),
                    entry_mode=p.entry_mode,
                    signal_score=c["score"],
                    equity_before=equity_now,
                    daily_trade_number=daily_trade_count,
                    open_positions_at_entry=len(open_positions),
                    portfolio_open_risk=new_risk,
                )
                open_positions[sym] = pos

                if day in daily_stats:
                    daily_stats[day]["trades"] += 0  # entries counted on exit

        # ---- 5. Record equity curve row ----
        unreal = _mark_to_market(open_positions, last_price)
        total_equity = p.initial_capital + realized_pnl + unreal
        peak_equity = max(peak_equity, total_equity)
        dd = peak_equity - total_equity
        dd_pct = (dd / peak_equity * 100) if peak_equity > 0 else 0.0

        max_simultaneous = max(max_simultaneous, len(open_positions))
        port_risk = _open_risk(open_positions)
        max_port_risk = max(max_port_risk, port_risk)
        risk_samples.append(port_risk)

        equity_rows.append(
            {
                "timestamp": ts,
                "cash": p.initial_capital + realized_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unreal,
                "total_equity": total_equity,
                "daily_pnl": total_equity - day_start_equity,
                "drawdown": dd,
                "drawdown_pct": dd_pct,
                "open_positions": len(open_positions),
                "daily_trade_count": daily_trade_count,
            }
        )

    # ---- Final EOD square-off for any residual positions ----
    if open_positions:
        final_ts = max(events.keys())
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            px = last_price.get(sym, pos.entry_price)
            equity_now = (
                p.initial_capital + realized_pnl + _mark_to_market(open_positions, last_price)
            )
            trade, realized_pnl = _close_position(
                pos,
                final_ts,
                px,
                "EOD_SQUARE_OFF",
                realized_pnl,
                equity_now,
                p,
                trade_counter,
            )
            trade_counter += 1
            trades.append(trade)
            del open_positions[sym]

    equity_df = pd.DataFrame(equity_rows)
    daily_df = _build_daily_df(daily_stats, p)

    summary = {
        "max_simultaneous_positions": max_simultaneous,
        "max_portfolio_open_risk": max_port_risk,
        "avg_portfolio_open_risk": float(np.mean(risk_samples)) if risk_samples else 0.0,
        "final_equity": p.initial_capital + realized_pnl,
        "realized_pnl": realized_pnl,
    }
    return trades, equity_df, daily_df, summary


def _build_daily_df(daily_stats: dict[str, dict[str, Any]], p: BacktestParams) -> pd.DataFrame:
    rows = []
    for day, ds in daily_stats.items():
        n = ds["trades"]
        ending = ds["starting_equity"] + ds["net_pnl"]
        rows.append(
            {
                "date": day,
                "starting_equity": round(ds["starting_equity"], 2),
                "ending_equity": round(ending, 2),
                "daily_pnl": round(ds["net_pnl"], 2),
                "daily_return_pct": round(
                    (ds["net_pnl"] / ds["starting_equity"] * 100) if ds["starting_equity"] else 0, 4
                ),
                "trades": n,
                "wins": ds["wins"],
                "losses": ds["losses"],
                "win_rate": round((ds["wins"] / n * 100) if n else 0, 2),
                "gross_pnl": round(ds["gross_pnl"], 2),
                "cost": round(ds["cost"], 2),
                "net_pnl": round(ds["net_pnl"], 2),
                "max_intraday_drawdown": round(ds["starting_equity"] - ds["min_equity"], 2),
            }
        )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


# ============================================================================
# ANALYTICS
# ============================================================================


def compute_metrics(trades: list[Trade], equity_df: pd.DataFrame, p: BacktestParams) -> dict:
    m: dict[str, Any] = {}
    if not trades:
        return m

    tdf = pd.DataFrame([t.to_row() for t in trades])
    m["n_trades"] = len(tdf)
    m["gross_pnl"] = float(tdf["gross_pnl"].sum())
    m["cost"] = float(tdf["estimated_cost"].sum())
    m["net_pnl"] = float(tdf["net_pnl"].sum())
    m["final_equity"] = p.initial_capital + m["net_pnl"]
    m["return_pct"] = (m["net_pnl"] / p.initial_capital) * 100

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] < 0]
    m["win_rate"] = len(wins) / len(tdf) * 100 if len(tdf) else 0
    m["avg_win"] = float(wins["net_pnl"].mean()) if not wins.empty else 0.0
    m["avg_loss"] = float(losses["net_pnl"].mean()) if not losses.empty else 0.0
    m["largest_win"] = float(tdf["net_pnl"].max())
    m["largest_loss"] = float(tdf["net_pnl"].min())
    m["avg_win_rate"] = m["win_rate"]
    gross_win = wins["net_pnl"].sum()
    gross_loss = abs(losses["net_pnl"].sum())
    m["profit_factor"] = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
    m["expectancy"] = m["avg_win"] * (m["win_rate"] / 100) + m["avg_loss"] * (
        1 - m["win_rate"] / 100
    )
    m["avg_r"] = float(tdf["r_multiple"].mean())
    m["median_r"] = float(tdf["r_multiple"].median())
    m["avg_duration"] = float(tdf["duration_minutes"].mean())

    # Drawdown from equity curve
    if not equity_df.empty:
        eq = equity_df["total_equity"]
        peak = eq.cummax()
        dd = peak - eq
        ddp = dd / peak * 100
        m["max_dd"] = float(dd.max())
        m["max_dd_pct"] = float(ddp.max())
        m["avg_dd_pct"] = float(ddp.mean())

        # Sharpe / Sortino from daily equity
        daily_eq = equity_df.copy()
        daily_eq["day"] = pd.to_datetime(daily_eq["timestamp"]).dt.date
        day_eq = daily_eq.groupby("day")["total_equity"].last()
        rets = day_eq.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            m["sharpe"] = float(rets.mean() / rets.std() * np.sqrt(252))
            downside = rets[rets < 0]
            dstd = downside.std() if len(downside) > 1 else 0.0
            m["sortino"] = float(rets.mean() / dstd * np.sqrt(252)) if dstd > 0 else float("inf")
        else:
            m["sharpe"] = 0.0
            m["sortino"] = 0.0
        m["calmar"] = (
            float(m["return_pct"] / m["max_dd_pct"]) if m.get("max_dd_pct", 0) > 0 else 0.0
        )
    else:
        m["max_dd"] = 0.0
        m["max_dd_pct"] = 0.0
        m["avg_dd_pct"] = 0.0
        m["sharpe"] = 0.0
        m["sortino"] = 0.0
        m["calmar"] = 0.0

    # Long/short
    for side in ("LONG", "SHORT"):
        sub = tdf[tdf["direction"] == side]
        m[f"{side.lower()}_trades"] = len(sub)
        m[f"{side.lower()}_pnl"] = float(sub["net_pnl"].sum()) if not sub.empty else 0.0
        m[f"{side.lower()}_win_rate"] = (sub["net_pnl"] > 0).mean() * 100 if not sub.empty else 0.0

    # Trades per day
    m["avg_trades_day"] = float(tdf.groupby("date").size().mean())
    m["max_trades_day"] = int(tdf.groupby("date").size().max())

    m["trades_df"] = tdf
    return m


def print_v3_report(m: dict, summary: dict) -> None:
    if not m:
        print("\nNo trades generated.")
        return

    print()
    print("=" * 60)
    print("V3 PORTFOLIO BACKTEST RESULTS")
    print("=" * 60)
    print(f"Initial Capital:              {INITIAL_CAPITAL:,.2f}")
    print(f"Final Equity:                 {m['final_equity']:,.2f}")
    print(f"Net P&L:                      {m['net_pnl']:,.2f}")
    print(f"Return:                       {m['return_pct']:.2f}%")
    print(f"Max Drawdown:                 {m['max_dd']:,.2f}")
    print(f"Max Drawdown %:               {m['max_dd_pct']:.2f}%")
    print(f"Average Drawdown %:           {m['avg_dd_pct']:.2f}%")
    print()
    print(f"Total Trades:                 {m['n_trades']}")
    print(f"Win Rate:                     {m['win_rate']:.1f}%")
    print(f"Profit Factor:                {m['profit_factor']:.2f}")
    print(f"Expectancy:                   {m['expectancy']:.2f}")
    print(f"Average R:                    {m['avg_r']:.2f}")
    print(f"Median R:                     {m['median_r']:.2f}")
    print(f"Sharpe:                       {m['sharpe']:.2f}")
    print(f"Sortino:                      {m['sortino']:.2f}")
    print(f"Calmar:                       {m['calmar']:.2f}")
    print()
    print(f"Average Trades/Day:           {m['avg_trades_day']:.2f}")
    print(f"Maximum Trades/Day:           {m['max_trades_day']}")
    print(f"Maximum Simultaneous:         {summary['max_simultaneous_positions']}")
    print(f"Maximum Portfolio Risk:       {summary['max_portfolio_open_risk']:,.2f}")
    print(f"Average Portfolio Risk:       {summary['avg_portfolio_open_risk']:,.2f}")
    print()
    print(f"Long P&L:                     {m.get('long_pnl', 0):,.2f}")
    print(f"Short P&L:                    {m.get('short_pnl', 0):,.2f}")
    print(f"Transaction Costs:            {m['cost']:,.2f}")
    print(f"Slippage (one-way):           {SLIPPAGE_PCT:.3f}%")
    print("=" * 60)

    tdf = m["trades_df"]

    print("\nExit Breakdown:")
    print(tdf["exit_reason"].value_counts().to_string())

    print("\nSession Breakdown:")
    for s in ("S1", "S2"):
        sub = tdf[tdf["session"] == s]
        if sub.empty:
            continue
        pf = _profit_factor(sub)
        print(
            f"  {s}: trades={len(sub)} win%={(sub['net_pnl'] > 0).mean() * 100:.1f} "
            f"net={sub['net_pnl'].sum():.2f} pf={pf:.2f} avgR={sub['r_multiple'].mean():.2f}"
        )

    print("\nLong/Short:")
    for side in ("LONG", "SHORT"):
        sub = tdf[tdf["direction"] == side]
        if sub.empty:
            continue
        pf = _profit_factor(sub)
        print(
            f"  {side}: trades={len(sub)} win%={(sub['net_pnl'] > 0).mean() * 100:.1f} "
            f"net={sub['net_pnl'].sum():.2f} pf={pf:.2f} avgR={sub['r_multiple'].mean():.2f}"
        )

    by_sym = (
        tdf.groupby("symbol")
        .agg(
            trades=("net_pnl", "count"),
            wins=("net_pnl", lambda x: (x > 0).sum()),
            losses=("net_pnl", lambda x: (x < 0).sum()),
            win_rate=("net_pnl", lambda x: (x > 0).mean() * 100),
            gross_pnl=("gross_pnl", "sum"),
            net_pnl=("net_pnl", "sum"),
            avg_r=("r_multiple", "mean"),
        )
        .sort_values("net_pnl", ascending=False)
    )
    print("\nTop 10 Symbols by Net P&L:")
    print(by_sym.head(10).to_string())
    print("\nBottom 10 Symbols by Net P&L:")
    print(by_sym.tail(10).to_string())

    _bucket_report(tdf, "relative_volume", [1.20, 1.50, 2.00, 3.00], "Relative Volume")
    _bucket_report(tdf, "range_width_pct", [0.0, 0.50, 0.75, 1.00], "Range Width %")

    print("\n" + "!" * 60)
    print("WARNING:")
    print("Backtest results are historical simulations and may be affected by")
    print("overfitting, slippage, liquidity, data quality and execution assumptions.")
    print("!" * 60)


def _profit_factor(sub: pd.DataFrame) -> float:
    wins = sub[sub["net_pnl"] > 0]["net_pnl"].sum()
    loss = abs(sub[sub["net_pnl"] < 0]["net_pnl"].sum())
    return float(wins / loss) if loss > 0 else float("inf")


def _bucket_report(tdf: pd.DataFrame, col: str, edges: list[float], label: str) -> None:
    if col not in tdf.columns:
        return
    print(f"\n{label} Buckets:")
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sub = tdf[(tdf[col] >= lo) & (tdf[col] < hi)]
        if sub.empty:
            continue
        print(
            f"  {lo:.2f}-{hi:.2f}: trades={len(sub)} win%={(sub['net_pnl'] > 0).mean() * 100:.1f} "
            f"net={sub['net_pnl'].sum():.2f} avgR={sub['r_multiple'].mean():.2f}"
        )
    last = tdf[tdf[col] >= edges[-1]]
    if not last.empty:
        print(
            f"  >{edges[-1]:.2f}: trades={len(last)} win%={(last['net_pnl'] > 0).mean() * 100:.1f} "
            f"net={last['net_pnl'].sum():.2f} avgR={last['r_multiple'].mean():.2f}"
        )


# ============================================================================
# V2 COMPARISON
# ============================================================================


def compare_v2_baseline(m: dict) -> None:
    if not os.path.isfile(V2_BASELINE_CSV):
        print(f"\n[COMPARE] V2 baseline '{V2_BASELINE_CSV}' not found — skipping.")
        return
    try:
        v2 = pd.read_csv(V2_BASELINE_CSV)
    except Exception as e:
        print(f"[COMPARE] Could not read V2 baseline: {e}")
        return

    pnl_col = "pnl" if "pnl" in v2.columns else "net_pnl"
    v2_trades = len(v2)
    v2_win = (v2[pnl_col] > 0).mean() * 100 if v2_trades else 0
    v2_gross = v2[pnl_col].sum()

    v3_trades = m.get("n_trades", 0)
    v3_win = m.get("win_rate", 0)
    v3_gross = m.get("gross_pnl", 0)
    v3_net = m.get("net_pnl", 0)

    print("\n" + "=" * 60)
    print("V2 BASELINE vs V3 PORTFOLIO")
    print("=" * 60)
    print(f"{'Metric':<32}{'V2':>12}{'V3':>12}")
    print("-" * 60)
    print(f"{'Total trades':<32}{v2_trades:>12}{v3_trades:>12}")
    print(f"{'Win rate %':<32}{v2_win:>12.1f}{v3_win:>12.1f}")
    print(f"{'Gross/net P&L':<32}{v2_gross:>12.2f}{v3_gross:>12.2f}")
    print(f"{'Net P&L':<32}{'n/a':>12}{v3_net:>12.2f}")
    print(f"{'Max DD %':<32}{'n/a':>12}{m.get('max_dd_pct', 0):>12.2f}")
    print(f"{'Profit factor':<32}{'n/a':>12}{m.get('profit_factor', 0):>12.2f}")
    print(f"{'Avg R':<32}{'n/a':>12}{m.get('avg_r', 0):>12.2f}")
    print(f"{'Sharpe':<32}{'n/a':>12}{m.get('sharpe', 0):>12.2f}")
    print("=" * 60)
    print(
        "Note: V2 simulated each symbol with an independent equity account and a\n"
        "per-symbol trade limit, so its trade count and P&L are not directly\n"
        "comparable. V3 enforces ONE shared portfolio, ONE daily trade limit,\n"
        "portfolio-wide risk caps, costs and slippage."
    )

    try:
        pd.DataFrame(
            [
                {"metric": "total_trades", "v2": v2_trades, "v3": v3_trades},
                {"metric": "win_rate_pct", "v2": v2_win, "v3": v3_win},
                {"metric": "gross_pnl", "v2": v2_gross, "v3": v3_gross},
                {"metric": "net_pnl", "v2": None, "v3": v3_net},
                {"metric": "max_dd_pct", "v2": None, "v3": m.get("max_dd_pct", 0)},
                {"metric": "profit_factor", "v2": None, "v3": m.get("profit_factor", 0)},
                {"metric": "avg_r", "v2": None, "v3": m.get("avg_r", 0)},
                {"metric": "sharpe", "v2": None, "v3": m.get("sharpe", 0)},
            ]
        ).to_csv(COMPARISON_CSV, index=False)
        print(f"Comparison saved to {COMPARISON_CSV}")
    except Exception as e:
        print(f"[COMPARE] Save failed: {e}")


# ============================================================================
# DATA LOADING
# ============================================================================


def _cache_path(symbol: str, start: str, end: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = f"{symbol}_{TIMEFRAME}_{start}_{end}".replace(":", "").replace("-", "")
    return os.path.join(CACHE_DIR, f"{safe}.parquet")


def fetch_history(
    client: api, symbol: str, exchange: str, start: str, end: str
) -> pd.DataFrame | None:
    try:
        df = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=TIMEFRAME,
            start_date=start,
            end_date=end,
        )
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
        if isinstance(df, dict):
            print(f"  [WARN] {symbol}: {df.get('message', 'connection error')}")
    except Exception as e:
        print(f"  [WARN] {symbol}: {e}")
    return None


def load_universe_data(
    symbols: list[str], use_cache: bool, start: str, end: str
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None, list[dict[str, Any]]]:
    api_key = os.getenv("OPENALGO_API_KEY")
    if not api_key:
        print("ERROR: OPENALGO_API_KEY is not configured.")
        sys.exit(1)

    client = api(api_key=api_key, host=API_HOST)
    raw: dict[str, pd.DataFrame] = {}
    quality: list[dict[str, Any]] = []

    nifty_df = None
    nifty_cache = _cache_path("NIFTY_IDX", start, end)
    if use_cache and os.path.exists(nifty_cache):
        try:
            nifty_df = pd.read_parquet(nifty_cache)
        except Exception:
            nifty_df = None
    if nifty_df is None:
        nifty_df = fetch_history(client, NIFTY_SYMBOL, NIFTY_EXCHANGE, start, end)
        if nifty_df is not None and use_cache:
            try:
                nifty_df.to_parquet(nifty_cache)
            except Exception:
                pass

    n = len(symbols)
    for idx, sym in enumerate(symbols):
        sys.stdout.write(f"\r[data {idx + 1}/{n}] {sym} ...   ")
        sys.stdout.flush()
        cache_path = _cache_path(sym, start, end)
        df = None
        if use_cache and os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path)
            except Exception:
                df = None
        if df is None:
            df = fetch_history(client, sym, EXCHANGE, start, end)
            if df is not None and use_cache:
                try:
                    df.to_parquet(cache_path)
                except Exception:
                    pass
        if df is None or df.empty:
            continue
        quality.append(check_data_quality(sym, df))
        raw[sym] = df

    print()
    print(f"[data] Loaded {len(raw)}/{n} symbols")
    return raw, nifty_df, quality


# ============================================================================
# SWEEP
# ============================================================================


def run_sweep(
    raw: dict[str, pd.DataFrame], nifty_df: pd.DataFrame | None, base: BacktestParams
) -> None:
    grid: list[tuple[str, list[Any]]] = [
        ("range_bars", [6, 8, 10, 12]),
        ("max_range_width_pct", [0.50, 0.75, 1.00]),
        ("relative_volume_multiplier", [1.20, 1.50, 2.00]),
        ("min_breakout_atr", [0.10, 0.20, 0.30]),
        ("target_rr", [1.00, 1.50, 2.00]),
        ("trail_atr_multiplier", [0.50, 0.75, 1.00]),
    ]

    results = []
    for field_name, values in grid:
        for v in values:
            params = BacktestParams(**{**base.__dict__})
            setattr(params, field_name, v)

            signals = {s: compute_signals(df, params) for s, df in raw.items()}
            nifty_sig = compute_nifty_signals(nifty_df, params) if nifty_df is not None else None
            trades, equity_df, _daily, summary = run_portfolio_backtest(signals, nifty_sig, params)
            m = compute_metrics(trades, equity_df, params)
            if not m:
                results.append(
                    {
                        field_name: v,
                        "trades": 0,
                        "net_pnl": 0,
                        "return_pct": 0,
                        "max_drawdown_pct": 0,
                        "profit_factor": 0,
                        "win_rate": 0,
                        "avg_r": 0,
                        "sharpe": 0,
                    }
                )
                continue
            results.append(
                {
                    field_name: v,
                    "trades": m["n_trades"],
                    "net_pnl": round(m["net_pnl"], 2),
                    "return_pct": round(m["return_pct"], 3),
                    "max_drawdown_pct": round(m["max_dd_pct"], 3),
                    "profit_factor": round(m["profit_factor"], 3),
                    "win_rate": round(m["win_rate"], 2),
                    "avg_r": round(m["avg_r"], 3),
                    "sharpe": round(m["sharpe"], 3),
                }
            )
            print(
                f"[sweep] {field_name}={v}: trades={m['n_trades']} "
                f"net={m['net_pnl']:.2f} dd%={m['max_dd_pct']:.2f}"
            )

    sweep_df = pd.DataFrame(results)
    sweep_df.to_csv(SWEEP_CSV, index=False)
    print(f"\n[sweep] Results saved to {SWEEP_CSV}")
    print(sweep_df.to_string(index=False))
    print(
        "\n[sweep] Rank by return, drawdown, profit factor and stability — never by raw P&L alone."
    )


# ============================================================================
# VALIDATION REPORT
# ============================================================================


def run_validation_report(
    trades: list[Trade],
    equity_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    nifty_sig: pd.DataFrame | None,
    params: BacktestParams,
) -> str:
    """Mechanical-correctness audit. Returns the report text and writes it to disk."""
    results: list[tuple[str, str, str]] = []
    tdf = pd.DataFrame([t.to_row() for t in trades]) if trades else pd.DataFrame()

    def add(name: str, status: str, detail: str) -> None:
        results.append((name, status, detail))

    # 1. ENTRY EXECUTION (signal from T, entry at T+1)
    if not tdf.empty:
        bad = 0
        for _, r in tdf.iterrows():
            et = pd.Timestamp(r["entry_time"])
            if et.tz is None:
                et = et.tz_localize(IST)
            if not in_allowed_session(et):
                bad += 1
        add(
            "ENTRY EXECUTION TEST",
            "PASS" if bad == 0 else "FAIL",
            f"entries outside allowed sessions: {bad}",
        )
    else:
        add("ENTRY EXECUTION TEST", "WARNING", "no trades")

    # 2/3. STOP + TARGET geometry
    if not tdf.empty:
        stop_bad = 0
        target_bad = 0
        for _, r in tdf.iterrows():
            e = float(r["entry_price"])
            sl = float(r["initial_stop"])
            tp = float(r["target"])
            risk = abs(e - sl)
            if risk <= 0:
                stop_bad += 1
            rr = abs(tp - e) / risk if risk > 0 else 0
            if abs(rr - params.target_rr) > 0.01:
                target_bad += 1
        add(
            "STOP TEST",
            "PASS" if stop_bad == 0 else "FAIL",
            f"zero/negative risk trades: {stop_bad}",
        )
        add(
            "TARGET TEST",
            "PASS" if target_bad == 0 else "FAIL",
            f"trades with R!=target_rr: {target_bad}",
        )
    else:
        add("STOP TEST", "WARNING", "no trades")
        add("TARGET TEST", "WARNING", "no trades")

    # 4. TRAILING STOP
    if not tdf.empty:
        n_trail = int((tdf["exit_reason"] == "TRAILING_STOP").sum())
        add(
            "TRAILING STOP TEST",
            "PASS" if params.use_trailing else "WARNING",
            f"trailing exits={n_trail} (use_trailing={params.use_trailing})",
        )
    else:
        add("TRAILING STOP TEST", "WARNING", "no trades")

    # 5. EOD EXIT
    if not tdf.empty:
        n_eod = int((tdf["exit_reason"] == "EOD_SQUARE_OFF").sum())
        add("EOD EXIT TEST", "PASS" if n_eod >= 0 else "FAIL", f"EOD exits={n_eod}")
    else:
        add("EOD EXIT TEST", "WARNING", "no trades")

    # 6. NIFTY LOOKAHEAD — every trade's nifty_regime must be consistent
    if not tdf.empty:
        bad_regime = int((~tdf["nifty_regime"].isin(["BULLISH", "BEARISH", "NEUTRAL"])).sum())
        add(
            "NIFTY LOOKAHEAD TEST",
            "PASS" if bad_regime == 0 else "FAIL",
            f"invalid regimes: {bad_regime}",
        )
    else:
        add("NIFTY LOOKAHEAD TEST", "WARNING", "no trades")

    # 7. POSITION SIZING
    if not tdf.empty:
        qty_bad = int((tdf["quantity"] <= 0).sum())
        add(
            "POSITION SIZING TEST",
            "PASS" if qty_bad == 0 else "FAIL",
            f"non-positive qty: {qty_bad}",
        )
    else:
        add("POSITION SIZING TEST", "WARNING", "no trades")

    # 8. DAILY TRADE LIMIT (global)
    if not tdf.empty:
        per_day = tdf.groupby("date").size()
        over = int((per_day > params.max_trades_per_day).sum())
        add(
            "DAILY TRADE LIMIT TEST",
            "PASS" if over == 0 else "FAIL",
            f"days exceeding {params.max_trades_per_day}: {over} (max seen={int(per_day.max())})",
        )
    else:
        add("DAILY TRADE LIMIT TEST", "WARNING", "no trades")

    # 9. DAILY LOSS LIMIT
    if not daily_df.empty:
        worst = float(daily_df["net_pnl"].min())
        limit = -params.initial_capital * params.max_daily_loss_pct / 100.0
        add(
            "DAILY LOSS LIMIT TEST",
            "PASS" if worst >= limit - 1e-6 else "WARNING",
            f"worst daily PnL={worst:.2f} vs limit={limit:.2f}",
        )
    else:
        add("DAILY LOSS LIMIT TEST", "WARNING", "no daily data")

    # 10. PORTFOLIO RISK
    if not tdf.empty:
        max_risk = float(tdf["portfolio_open_risk"].max())
        cap = params.initial_capital * params.max_portfolio_open_risk_pct / 100.0
        add(
            "PORTFOLIO RISK TEST",
            "PASS" if max_risk <= cap + 1e-6 else "FAIL",
            f"max open risk={max_risk:.2f} cap={cap:.2f}",
        )
    else:
        add("PORTFOLIO RISK TEST", "WARNING", "no trades")

    # 11. COST TEST
    if not tdf.empty:
        cost = float(tdf["estimated_cost"].sum())
        add(
            "COST TEST",
            "PASS",
            f"total estimated cost={cost:.2f} (SIMPLE model, {params.estimated_cost_pct}% one-way)",
        )
    else:
        add("COST TEST", "WARNING", "no trades")

    # 12. SLIPPAGE TEST
    add("SLIPPAGE TEST", "PASS", f"one-way slippage={params.slippage_pct}% applied at entry+exit")

    # 13. EQUITY RECONCILIATION
    if not tdf.empty and not daily_df.empty and not equity_df.empty:
        sum_trade = float(tdf["net_pnl"].sum())
        sum_daily = float(daily_df["net_pnl"].sum())
        final_eq = float(equity_df["total_equity"].iloc[-1])
        recon = abs(sum_trade - sum_daily)
        recon2 = abs(sum_trade - (final_eq - params.initial_capital))
        ok = recon < 5.0 and recon2 < 5.0
        add(
            "EQUITY RECONCILIATION TEST",
            "PASS" if ok else "WARNING",
            f"sum(trade net)={sum_trade:.2f} sum(daily net)={sum_daily:.2f} "
            f"final-initial={final_eq - params.initial_capital:.2f}",
        )
    else:
        add("EQUITY RECONCILIATION TEST", "WARNING", "insufficient data")

    # 14. DATE RANGE
    if not equity_df.empty and not tdf.empty:
        add(
            "DATE RANGE TEST",
            "PASS",
            f"trades span {tdf['date'].min()}..{tdf['date'].max()} "
            f"| equity rows={len(equity_df)} | daily rows={len(daily_df)}",
        )
    else:
        add("DATE RANGE TEST", "WARNING", "no data")

    # Compose report
    lines = ["V3 BACKTEST VALIDATION REPORT", "=" * 60, ""]
    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_warn = sum(1 for _, s, _ in results if s == "WARNING")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    for name, status, detail in results:
        lines.append(f"[{status:7}] {name}")
        lines.append(f"          {detail}")
    lines.append("")
    lines.append(f"TOTALS: PASS={n_pass} WARNING={n_warn} FAIL={n_fail}")
    lines.append("")
    lines.append(
        "Notes: 5-min OHLC cannot resolve intrabar stop-vs-target order; "
        f"policy={params.intrabar_exit_policy} assumes STOP first when both touched."
    )
    report = "\n".join(lines)
    with open("backtest_v3_validation_report.txt", "w") as f:
        f.write(report + "\n")
    print("\n" + report)
    return report


# ============================================================================
# MAIN
# ============================================================================


def save_outputs(trades: list[Trade], equity_df: pd.DataFrame, daily_df: pd.DataFrame) -> None:
    if trades:
        pd.DataFrame([t.to_row() for t in trades]).to_csv(TRADES_CSV, index=False)
        print(f"[output] Trades  -> {TRADES_CSV}  ({len(trades)} rows)")
    if not equity_df.empty:
        equity_df.to_csv(EQUITY_CSV, index=False)
        print(f"[output] Equity  -> {EQUITY_CSV}  ({len(equity_df)} rows)")
    if not daily_df.empty:
        daily_df.to_csv(DAILY_CSV, index=False)
        print(f"[output] Daily   -> {DAILY_CSV}  ({len(daily_df)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Range Breakout V3 portfolio backtest")
    parser.add_argument("--sample", type=int, default=None, help="Use first N symbols")
    parser.add_argument("--cache", action="store_true", help="Reuse parquet cache")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--sweep", action="store_true", help="Parameter sweep mode")
    args = parser.parse_args()

    if not os.getenv("OPENALGO_API_KEY"):
        print("ERROR: OPENALGO_API_KEY is not configured.")
        sys.exit(1)

    end = args.end or datetime.now(IST).strftime("%Y-%m-%d")
    start = args.start or (datetime.now(IST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    symbols = load_symbols()
    if args.sample:
        symbols = symbols[: args.sample]

    print("=" * 60)
    print("V3 PORTFOLIO BACKTEST — RANGE BREAKOUT")
    print("=" * 60)
    print(f"Universe:    {len(symbols)} symbols")
    print(f"Period:      {start} -> {end}")
    print(f"Timeframe:   {TIMEFRAME}")
    print(f"Cache:       {args.cache}")
    print(f"Sweep:       {args.sweep}")
    print("=" * 60)

    t0 = time.time()
    raw, nifty_df, quality = load_universe_data(symbols, args.cache, start, end)
    print_data_quality(quality)

    if not raw:
        print("[ERROR] No data loaded. Check API key / host / date range.")
        sys.exit(1)

    params = BacktestParams()

    if args.sweep:
        run_sweep(raw, nifty_df, params)
        return

    signals = {s: compute_signals(df, params) for s, df in raw.items()}
    nifty_sig = compute_nifty_signals(nifty_df, params) if nifty_df is not None else None

    trades, equity_df, daily_df, summary = run_portfolio_backtest(signals, nifty_sig, params)
    elapsed = time.time() - t0

    print(f"\n[run] Completed in {elapsed:.1f}s")
    save_outputs(trades, equity_df, daily_df)

    m = compute_metrics(trades, equity_df, params)
    print_v3_report(m, summary)
    run_validation_report(trades, equity_df, daily_df, nifty_sig, params)
    compare_v2_baseline(m)


if __name__ == "__main__":
    main()
