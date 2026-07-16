from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config
from .scanner_adapter import BaseScanner
from .scanner_registry import register
from .scanner_signal import ScannerSignal


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    ohlc = df["close"].resample(rule).ohlc()
    vol = df["volume"].resample(rule).sum()
    result = pd.DataFrame({
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "close": ohlc["close"],
        "volume": vol,
    })
    result = result.dropna(subset=["open", "close"])
    return result


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < period:
        return out
    alpha = 2 / (period + 1)
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _wma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < period:
        return out
    weights = np.arange(1, period + 1, dtype=float)
    wsum = weights.sum()
    for i in range(period - 1, len(arr)):
        out[i] = np.dot(arr[i - period + 1:i + 1], weights) / wsum
    return out


def _rsi(arr: np.ndarray, period: int = 9) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if len(arr) < period + 1:
        return out
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100 - 100 / (1 + rs)
    for i in range(period + 1, len(arr)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - 100 / (1 + rs)
    return out


@register("hilega_milega_mtf")
class HilegaMilegaMTFScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "hilega_milega_mtf"

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
            if df.empty or len(df) < 30:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 30:
                continue

            daily = _resample(df, "1D")
            hourly = _resample(df, "1h")
            fifteen = _resample(df, "15min")
            weekly = _resample(df, "1W")

            d_score, d_cross, d_ema3, d_wma21 = self._tf_hilega_score(daily)
            h_score, h_cross, h_ema3, h_wma21 = self._tf_hilega_score(hourly)
            f_score, f_cross, f_ema3, f_wma21 = self._tf_hilega_score(fifteen)
            w_score, w_cross, w_ema3, w_wma21 = self._tf_hilega_score(weekly)

            has_weekly = w_score is not None and not np.isnan(w_score) and len(weekly) >= 5
            if has_weekly:
                mtf_score = 0.20 * w_score + 0.40 * d_score + 0.25 * h_score + 0.15 * f_score
            else:
                total_w = 0.40 + 0.25 + 0.15
                mtf_score = (0.40 * d_score + 0.25 * h_score + 0.15 * f_score) / total_w if total_w > 0 else 0.0

            cross_distance = d_cross if not np.isnan(d_cross) else 1.0

            bullish_tfs = sum(1 for x in [d_cross, h_cross, f_cross] if not np.isnan(x) and x > 0)
            total_valid = sum(1 for x in [d_cross, h_cross, f_cross] if not np.isnan(x))

            if bullish_tfs >= 2:
                direction = "LONG"
            elif bullish_tfs == 0 and total_valid > 0:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            confidence = bullish_tfs / total_valid if total_valid > 0 else 0.0

            close_vals = df["close"].values.astype(float)
            vol_vals = df["volume"].values.astype(float)
            last = len(close_vals) - 1
            rvol = vol_vals[last] / (np.mean(vol_vals[-21:-1]) if len(vol_vals) > 21 else np.mean(vol_vals[:last]))

            features = {
                "d_score": float(d_score) if not np.isnan(d_score) else 0.0,
                "h_score": float(h_score) if not np.isnan(h_score) else 0.0,
                "f_score": float(f_score) if not np.isnan(f_score) else 0.0,
                "mtf_score": float(mtf_score) if not np.isnan(mtf_score) else 0.0,
                "cross_distance": float(cross_distance),
                "rvol": float(rvol),
                "d_ema3_rsi": float(d_ema3) if not np.isnan(d_ema3) else 50.0,
                "d_wma21_rsi": float(d_wma21) if not np.isnan(d_wma21) else 50.0,
                "h_ema3_rsi": float(h_ema3) if not np.isnan(h_ema3) else 50.0,
                "bullish_tfs": float(bullish_tfs),
            }

            signal = ScannerSignal(
                timestamp=timestamp,
                symbol=symbol,
                scanner_name=self.name,
                score=float(mtf_score),
                rank=0,
                direction=direction,
                confidence=float(confidence),
                timeframe=self.timeframe,
                features=features,
                signal_state="ACTIVE",
            )
            signals[symbol] = signal

        return signals

    def _tf_hilega_score(self, tf_df: pd.DataFrame) -> tuple[float, float, float, float]:
        if tf_df.empty or len(tf_df) < 25:
            return 0.0, np.nan, np.nan, np.nan

        c = tf_df["close"].values.astype(float)
        rsi_vals = _rsi(c, 9)
        ema3_rsi = _ema(rsi_vals, 3)
        wma21_rsi = _wma(rsi_vals, 21)

        last = len(c) - 1
        e3 = ema3_rsi[last]
        w21 = wma21_rsi[last]

        if np.isnan(e3) or np.isnan(w21):
            return 50.0, np.nan, np.nan, np.nan

        diff = e3 - w21
        max_dist = 30.0
        score = np.clip(50 + (diff / max_dist) * 50, 0, 100)
        cross = diff

        return float(score), float(cross), float(e3), float(w21)
