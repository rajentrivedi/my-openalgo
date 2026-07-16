from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_adapter import BaseScanner
from .scanner_registry import register
from .scanner_signal import ScannerSignal


@register("relative_strength")
class RelativeStrengthScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "relative_strength"

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

        if nifty_data is None or nifty_data.empty:
            return signals

        ndf = nifty_data[nifty_data.index <= timestamp]
        if len(ndf) < 25:
            return signals

        nifty_close = ndf["close"].values.astype(float)
        nifty_last = len(nifty_close) - 1
        nifty_ret_20 = (nifty_close[nifty_last] / nifty_close[max(0, nifty_last - 20)] - 1) if nifty_close[max(0, nifty_last - 20)] > 0 else 0.0

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 25:
                continue

            close_vals = df["close"].values.astype(float)
            last = len(close_vals) - 1

            sym_ret_20 = (close_vals[last] / close_vals[max(0, last - 20)] - 1) if close_vals[max(0, last - 20)] > 0 else 0.0
            sym_ret_10 = (close_vals[last] / close_vals[max(0, last - 10)] - 1) if close_vals[max(0, last - 10)] > 0 else 0.0
            sym_ret_5 = (close_vals[last] / close_vals[max(0, last - 5)] - 1) if close_vals[max(0, last - 5)] > 0 else 0.0

            rs_20 = sym_ret_20 - nifty_ret_20

            nifty_ret_10 = (nifty_close[nifty_last] / nifty_close[max(0, nifty_last - 10)] - 1) if nifty_close[max(0, nifty_last - 10)] > 0 else nifty_ret_20 * 0.5
            rs_10 = sym_ret_10 - nifty_ret_10

            raw_score = rs_20 * 0.7 + rs_10 * 0.3
            score = float(np.clip((raw_score * 500 + 50), 0, 100))

            if rs_20 > 0:
                direction = "LONG"
            elif rs_20 < -0.005:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            confidence = float(np.clip(abs(raw_score) * 50, 0, 1))

            features = {
                "rs_20": float(rs_20),
                "rs_10": float(rs_10),
                "rs_5": float(sym_ret_5 - (nifty_ret_20 * 5 / 20)),
                "excess_return_20": float(sym_ret_20 - nifty_ret_20),
                "nifty_ret_20": float(nifty_ret_20),
                "sym_ret_20": float(sym_ret_20),
                "sym_ret_10": float(sym_ret_10),
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
