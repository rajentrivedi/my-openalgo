from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_adapter import BaseScanner
from .scanner_registry import register
from .scanner_signal import ScannerSignal


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


def _vwap(df: pd.DataFrame) -> np.ndarray:
    typical = (df["high"].values + df["low"].values + df["close"].values) / 3.0
    vol = df["volume"].values.astype(float)
    tp_vol = np.cumsum(typical * vol)
    vol_sum = np.cumsum(vol)
    out = np.where(vol_sum > 0, tp_vol / np.where(vol_sum == 0, 1, vol_sum), np.nan)
    return out


@register("range_breakout_v3")
class RangeBreakoutScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "range_breakout_v3"

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
        rng = config.RANGE_BARS

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < rng + 2:
                continue

            close_vals = df["close"].values.astype(float)
            high_vals = df["high"].values.astype(float)
            low_vals = df["low"].values.astype(float)
            vol_vals = df["volume"].values.astype(float)

            last = len(close_vals) - 1
            atr_vals = _atr(high_vals, low_vals, close_vals, 14)
            atr = atr_vals[last] if not np.isnan(atr_vals[last]) else 0.0

            prior = df.iloc[last - rng:last]
            if len(prior) < rng:
                continue
            range_high = prior["high"].max()
            range_low = prior["low"].min()
            range_width = (range_high - range_low) / close_vals[last] * 100 if close_vals[last] != 0 else 0.0

            if range_width > config.MAX_RANGE_WIDTH_PCT:
                continue

            avg_vol = np.mean(vol_vals[-rng - 1:-1])
            rel_volume = vol_vals[last] / avg_vol if avg_vol > 0 else 1.0

            vwap_vals = _vwap(df)
            vwap = vwap_vals[last] if not np.isnan(vwap_vals[last]) else close_vals[last]

            if close_vals[last] > range_high:
                displacement_atr = (close_vals[last] - range_high) / atr if atr > 0 else 0.0
                if rel_volume >= config.RELATIVE_VOLUME_MULTIPLIER:
                    direction = "LONG"
                else:
                    direction = "NEUTRAL"
            elif close_vals[last] < range_low:
                displacement_atr = (close_vals[last] - range_low) / atr if atr > 0 else 0.0
                if rel_volume >= config.RELATIVE_VOLUME_MULTIPLIER:
                    direction = "SHORT"
                else:
                    direction = "NEUTRAL"
            else:
                displacement_atr = 0.0
                direction = "NEUTRAL"

            score = rel_volume + abs(displacement_atr)
            score = float(np.clip(score, 0, 10))

            confidence = 0.5
            if direction != "NEUTRAL":
                if rel_volume >= config.RELATIVE_VOLUME_MULTIPLIER and abs(displacement_atr) >= config.MIN_BREAKOUT_ATR:
                    confidence = 0.8
                else:
                    confidence = 0.6

            features = {
                "range_high": float(range_high),
                "range_low": float(range_low),
                "rel_volume": float(rel_volume),
                "atr": float(atr),
                "displacement_atr": float(displacement_atr),
                "vwap": float(vwap),
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
