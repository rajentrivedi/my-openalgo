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


@register("mean_reversion")
class MeanReversionScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "mean_reversion"

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

        decile_map: dict[str, tuple[float, float]] = {}
        atr_map: dict[str, float] = {}

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 15:
                continue

            close_vals = df["close"].values.astype(float)
            high_vals = df["high"].values.astype(float)
            low_vals = df["low"].values.astype(float)

            last = len(close_vals) - 1
            ret_10 = (close_vals[last] / close_vals[max(0, last - 10)] - 1) if close_vals[max(0, last - 10)] > 0 else 0.0
            ret_5 = (close_vals[last] / close_vals[max(0, last - 5)] - 1) if close_vals[max(0, last - 5)] > 0 else 0.0

            atr_vals = _atr(high_vals, low_vals, close_vals, 14)
            a = atr_vals[last] if not np.isnan(atr_vals[last]) else close_vals[last] * 0.01
            atr_pct = a / close_vals[last] * 100 if close_vals[last] > 0 else 0.0

            daily_high = df["high"].max()
            daily_low = df["low"].min()
            close_loc = (close_vals[last] - daily_low) / (daily_high - daily_low) * 100 if (daily_high - daily_low) > 0 else 50.0

            decile_map[symbol] = (ret_10, ret_5)
            atr_map[symbol] = a

        if len(decile_map) < 5:
            return {}

        ret_10_vals = np.array([v[0] for v in decile_map.values()])
        order = ret_10_vals.argsort()
        n = len(ret_10_vals)
        percentile_ranks = np.empty(n, dtype=float)
        percentile_ranks[order] = np.arange(n, dtype=float) / (n - 1)

        for i, symbol in enumerate(decile_map):
            df = symbol_data[symbol]
            close_vals = df[df.index <= timestamp]["close"].values.astype(float)
            if len(close_vals) < 15:
                continue

            ret_10, ret_5 = decile_map[symbol]
            atr_val = atr_map[symbol]
            pr = percentile_ranks[i]

            last = len(close_vals) - 1
            high_vals = df[df.index <= timestamp]["high"].values.astype(float)
            low_vals = df[df.index <= timestamp]["low"].values.astype(float)

            atr_pct = atr_val / close_vals[last] * 100 if close_vals[last] > 0 else 0.0
            daily_high = df[df.index <= timestamp]["high"].max()
            daily_low = df[df.index <= timestamp]["low"].min()
            close_loc = (close_vals[last] - daily_low) / (daily_high - daily_low) * 100 if (daily_high - daily_low) > 0 else 50.0

            is_bottom_decile = pr <= 0.1
            is_top_decile = pr >= 0.9

            abs_ret = abs(ret_10)
            score = float(np.clip(abs_ret * 500 * (1 + atr_pct / 5), 0, 100))

            if is_bottom_decile:
                direction = "LONG"
            elif is_top_decile:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            confidence = float(abs(pr - 0.5) * 2)

            features = {
                "ret_10": float(ret_10),
                "ret_5": float(ret_5),
                "atr_pct": float(atr_pct),
                "close_loc": float(close_loc),
                "percentile_rank": float(pr),
                "abs_ret": float(abs_ret),
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
