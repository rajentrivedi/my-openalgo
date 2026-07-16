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


def _ema_slope(arr: np.ndarray, bars: int = 5) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < bars + 1:
        return out
    for i in range(bars, len(arr)):
        if arr[i - bars] != 0 and not np.isnan(arr[i - bars]):
            out[i] = (arr[i] - arr[i - bars]) / abs(arr[i - bars])
        else:
            out[i] = 0.0
    return out


def _compute_regime(nifty_close: np.ndarray, nifty_idx: int) -> str:
    if nifty_idx < 50:
        return config.SIDEWAYS
    ema20 = _ema(nifty_close, 20)
    ema50 = _ema(nifty_close, 50)
    slope = _ema_slope(ema20, 5)

    c = nifty_close[nifty_idx]
    e20 = ema20[nifty_idx]
    e50 = ema50[nifty_idx]
    slp = slope[nifty_idx]

    if not np.isnan(e20) and not np.isnan(e50) and not np.isnan(slp):
        if c > e20 > e50 and slp > 0:
            return config.BULL
        elif c < e20 < e50 and slp < 0:
            return config.BEAR

    return config.SIDEWAYS


@register("nifty_regime")
class NiftyRegimeScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "nifty_regime"

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
        if len(ndf) < 50:
            return signals

        nifty_close = ndf["close"].values.astype(float)
        nifty_last = len(nifty_close) - 1

        try:
            from regime_engine import compute_regime
            regime = compute_regime(ndf, timestamp)
        except (ImportError, TypeError):
            try:
                import inspect

                from regime_engine import compute_regime as _cr
                sig = inspect.signature(_cr)
                if 'ts' in sig.parameters:
                    regime = _cr(ndf, timestamp)
                else:
                    regime = _cr(ndf)
            except Exception:
                regime = _compute_regime(nifty_close, nifty_last)

        nifty_ret_20 = (nifty_close[nifty_last] / nifty_close[max(0, nifty_last - 20)] - 1) if nifty_close[max(0, nifty_last - 20)] > 0 else 0.0

        nifty_ema20 = _ema(nifty_close, 20)
        nifty_slope_vals = _ema_slope(nifty_ema20, 5)
        nifty_slope = nifty_slope_vals[nifty_last] if not np.isnan(nifty_slope_vals[nifty_last]) else 0.0

        for symbol, df in symbol_data.items():
            if df.empty:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 25:
                continue

            close_vals = df["close"].values.astype(float)
            last = len(close_vals) - 1

            sym_ret_20 = (close_vals[last] / close_vals[max(0, last - 20)] - 1) if close_vals[max(0, last - 20)] > 0 else 0.0

            min_len = min(len(close_vals), len(nifty_close))
            c_slice = close_vals[-min_len:]
            n_slice = nifty_close[-min_len:]
            mean_c = np.mean(c_slice)
            mean_n = np.mean(n_slice)
            if min_len < 3:
                corr = 0.0
                beta = 0.0
            else:
                c_dev = c_slice - mean_c
                n_dev = n_slice - mean_n
                num = np.sum(c_dev * n_dev)
                den = np.sqrt(np.sum(n_dev ** 2)) * np.sqrt(np.sum(c_dev ** 2))
                corr = num / den if den > 0 else 0.0
                var_n = np.sum(n_dev ** 2)
                beta = num / var_n if var_n > 0 else 0.0

            corr = float(np.clip(corr, -1, 1))

            if regime == config.BULL and corr > 0:
                direction = "LONG"
            elif regime == config.BEAR and corr > 0:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            regime_code = {"BULL": 1.0, "BEAR": -1.0, "SIDEWAYS": 0.0, "TRANSITION": 0.5}.get(regime, 0.0)
            confidence = float(abs(corr))
            score = float(np.clip((abs(corr) * 100) + (abs(nifty_ret_20) * 100), 0, 100))

            features = {
                "nifty_regime": regime_code,
                "nifty_regime_str": regime,
                "nifty_ret_20": float(nifty_ret_20),
                "nifty_slope": float(nifty_slope),
                "symbol_corr": float(corr),
                "symbol_beta": float(beta),
                "symbol_ret_20": float(sym_ret_20),
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
