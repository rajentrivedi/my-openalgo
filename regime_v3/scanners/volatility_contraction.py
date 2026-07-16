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


@register("volatility_contraction")
class VolatilityContractionScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "volatility_contraction"

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

            atr14_vals = _atr(high_vals, low_vals, close_vals, 14)
            last = len(close_vals) - 1
            atr14 = atr14_vals[last] if not np.isnan(atr14_vals[last]) else 0.0

            atr5_vals = _atr(high_vals, low_vals, close_vals, 5)
            atr5 = atr5_vals[last] if not np.isnan(atr5_vals[last]) else 0.0

            contraction_ratio = atr5 / atr14 if atr14 > 0 else 1.0

            atr_pct = atr14 / close_vals[last] * 100 if close_vals[last] > 0 else 0.0

            lookback = 14
            ranges = high_vals[-lookback:] - low_vals[-lookback:]
            range_avg_14 = float(np.mean(ranges))
            current_range = high_vals[last] - low_vals[last]
            range_contraction = current_range / range_avg_14 if range_avg_14 > 0 else 1.0

            score = float(np.clip((1 - contraction_ratio) * 100, 0, 100))

            ema20_vals = _ema(close_vals, 20)
            e20 = ema20_vals[last] if not np.isnan(ema20_vals[last]) else close_vals[last]
            c = close_vals[last]
            uptrend = c > e20

            if uptrend and contraction_ratio < 0.8:
                direction = "LONG"
            elif not uptrend and contraction_ratio < 0.8:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            confidence = float(np.clip((1 - contraction_ratio) / 0.5, 0, 1))

            features = {
                "atr_14": float(atr14),
                "atr_5": float(atr5),
                "contraction_ratio": float(contraction_ratio),
                "atr_pct": float(atr_pct),
                "range_avg_14": float(range_avg_14),
                "range_contraction": float(range_contraction),
                "uptrend": 1.0 if uptrend else 0.0,
            }

            signal = ScannerSignal(
                timestamp=timestamp,
                symbol=symbol,
                scanner_name=self.name,
                score=score,
                rank=0,
                direction=direction,
                confidence=confidence,
                timeframe=self.timeframe,
                features=features,
                signal_state="ACTIVE",
            )
            signals[symbol] = signal

        return signals
