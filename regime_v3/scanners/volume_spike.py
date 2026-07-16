from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_adapter import BaseScanner
from .scanner_registry import register
from .scanner_signal import ScannerSignal


def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < period:
        return out
    cum = np.cumsum(arr)
    out[period - 1] = cum[period - 1] / period
    for i in range(period, len(arr)):
        out[i] = (cum[i] - cum[i - period]) / period
    return out


@register("volume_spike")
class VolumeSpikeScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "volume_spike"

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
        lb = config.VOLUME_LOOKBACK

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < lb + 2:
                continue

            close_vals = df["close"].values.astype(float)
            vol_vals = df["volume"].values.astype(float)

            avg_vol_arr = _sma(vol_vals, lb)
            last = len(vol_vals) - 1

            avg_vol = avg_vol_arr[last] if not np.isnan(avg_vol_arr[last]) else 0.0
            rel_volume = vol_vals[last] / avg_vol if avg_vol > 0 else 1.0

            price_up = close_vals[last] > close_vals[last - 1] if last > 0 else False
            price_down = close_vals[last] < close_vals[last - 1] if last > 0 else False

            if price_up and rel_volume > 1.5:
                direction = "LONG"
            elif price_down and rel_volume > 1.5:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            volume_percentile = 0.5
            if len(vol_vals) > 1:
                above_count = np.sum(vol_vals[:last] < vol_vals[last])
                volume_percentile = above_count / last

            score = float(rel_volume)
            confidence = float(np.clip((rel_volume - 1.0) / 2.0, 0.0, 1.0))

            features = {
                "volume": float(vol_vals[last]),
                "avg_volume": float(avg_vol),
                "rel_volume": float(rel_volume),
                "volume_percentile": float(volume_percentile),
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
