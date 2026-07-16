from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_adapter import BaseScanner
from .scanner_registry import register
from .scanner_signal import ScannerSignal


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < period:
        return out
    alpha = 2 / (period + 1)
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return out
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    out[period] = np.mean(tr[:period])
    for i in range(period + 1, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i - 1]) / period
    return out


def _ema_slope(arr: np.ndarray, bars: int = 3) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < bars + 1:
        return out
    for i in range(bars, len(arr)):
        if arr[i - bars] != 0 and not np.isnan(arr[i - bars]):
            out[i] = (arr[i] - arr[i - bars]) / abs(arr[i - bars])
        else:
            out[i] = 0.0
    return out


@register("momentum_v6")
class MomentumScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "momentum_v6"

    @property
    def timeframe(self) -> str:
        return "5m"

    def compute_signals(
        self,
        symbol_data: dict[str, pd.DataFrame],
        nifty_data: pd.DataFrame | None,
        timestamp: pd.Timestamp,
    ) -> dict[str, ScannerSignal]:

        signals: dict[str, ScannerSignal] = {}

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 60:
                continue

            close_vals = df["close"].values.astype(float)
            high_vals = df["high"].values.astype(float)
            low_vals = df["low"].values.astype(float)
            vol_vals = df["volume"].values.astype(float)

            ema9 = _ema(close_vals, config.EMA9_LEN)
            ema20 = _ema(close_vals, config.EMA20_LEN)
            ema50 = _ema(close_vals, config.EMA50_LEN)
            slope = _ema_slope(ema20, config.EMA_SLOPE_BARS)
            atr = _atr(high_vals, low_vals, close_vals, config.ATR_LENGTH)

            last = len(close_vals) - 1
            c = close_vals[last]
            e9 = ema9[last]
            e20 = ema20[last]
            e50 = ema50[last]
            slp = slope[last]
            a = atr[last]

            avg_vol = np.mean(vol_vals[-21:-1]) if len(vol_vals) > 21 else np.mean(vol_vals[:last])
            rvol = vol_vals[last] / avg_vol if avg_vol > 0 else 1.0

            ret_20 = (c / close_vals[max(0, last - 20)] - 1) if close_vals[max(0, last - 20)] > 0 else 0.0
            ret_20_scaled = float(np.clip(ret_20 * 100, -20, 20))

            conditions_met = 0
            total_conditions = 5

            if c > e20 and not np.isnan(e20):
                conditions_met += 1
            if e9 > e20 and not np.isnan(e9) and not np.isnan(e20):
                conditions_met += 1
            if e20 > e50 and not np.isnan(e20) and not np.isnan(e50):
                conditions_met += 1
            if not np.isnan(slp) and slp > 0:
                conditions_met += 1
            if rvol >= config.BULL_MOMENTUM_MIN_RVOL:
                conditions_met += 1

            bear_cond_met = 0
            if c < e20 and not np.isnan(e20):
                bear_cond_met += 1
            if e9 < e20 and not np.isnan(e9) and not np.isnan(e20):
                bear_cond_met += 1
            if e20 < e50 and not np.isnan(e20) and not np.isnan(e50):
                bear_cond_met += 1
            if not np.isnan(slp) and slp < 0:
                bear_cond_met += 1
            if rvol >= config.BEAR_MOMENTUM_MIN_RVOL:
                bear_cond_met += 1

            bullish = c > e20 > e50 and (not np.isnan(slp) and slp > 0) if not np.isnan(e20) and not np.isnan(e50) else False
            bearish = c < e20 < e50 and (not np.isnan(slp) and slp < 0) if not np.isnan(e20) and not np.isnan(e50) else False

            if bullish:
                direction = "LONG"
            elif bearish:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            score = ret_20_scaled + rvol
            if not np.isnan(e9) and not np.isnan(e20) and not np.isnan(a) and a > 0:
                score += (e9 - e20) / a
            score = float(np.clip(score, 0, 10))

            confidence = conditions_met / total_conditions if bullish else (bear_cond_met / total_conditions if bearish else 0.0)

            features = {
                "c_gt_e20": 1.0 if (not np.isnan(e20) and c > e20) else 0.0,
                "ema9_gt_ema20": 1.0 if (not np.isnan(e9) and not np.isnan(e20) and e9 > e20) else 0.0,
                "ema20_gt_ema50": 1.0 if (not np.isnan(e20) and not np.isnan(e50) and e20 > e50) else 0.0,
                "slope_gt_0": 1.0 if (not np.isnan(slp) and slp > 0) else 0.0,
                "conditions_met": float(conditions_met),
                "bear_conditions_met": float(bear_cond_met),
                "ret_20": float(ret_20),
                "rvol": float(rvol),
                "ema9_minus_ema20_atr": float((e9 - e20) / a) if (not np.isnan(e9) and not np.isnan(e20) and not np.isnan(a) and a > 0) else 0.0,
            }

            signal = ScannerSignal(
                timestamp=timestamp,
                symbol=symbol,
                scanner_name=self.name,
                score=score,
                rank=0,
                direction=direction,
                confidence=float(confidence),
                timeframe=self.timeframe,
                features=features,
                signal_state="ACTIVE",
            )
            signals[symbol] = signal

        return signals
