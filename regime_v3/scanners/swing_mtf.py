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


def _cmf(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full_like(close, np.nan)
    if len(close) < period:
        return out
    mfv = ((close - low) - (high - close)) / np.where(high - low == 0, 1e-10, high - low) * volume
    for i in range(period - 1, len(close)):
        vol_sum = np.sum(volume[i - period + 1:i + 1])
        out[i] = np.sum(mfv[i - period + 1:i + 1]) / vol_sum if vol_sum > 0 else 0.0
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


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full_like(close, np.nan)
    n = len(close)
    if n < period + 1:
        return out
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0
    atr_series = np.full(n, np.nan)
    atr_series[period] = np.mean(tr[1:period + 1])
    plus_smooth = np.full(n, np.nan)
    minus_smooth = np.full(n, np.nan)
    plus_smooth[period] = np.mean(plus_dm[1:period + 1])
    minus_smooth[period] = np.mean(minus_dm[1:period + 1])
    for i in range(period + 1, n):
        atr_series[i] = (atr_series[i - 1] * (period - 1) + tr[i]) / period
        plus_smooth[i] = (plus_smooth[i - 1] * (period - 1) + plus_dm[i]) / period
        minus_smooth[i] = (minus_smooth[i - 1] * (period - 1) + minus_dm[i]) / period
    pdi = np.full(n, np.nan)
    mdi = np.full(n, np.nan)
    for i in range(period, n):
        pdi[i] = 100 * plus_smooth[i] / atr_series[i] if atr_series[i] > 0 else 0
        mdi[i] = 100 * minus_smooth[i] / atr_series[i] if atr_series[i] > 0 else 0
    dx = np.full(n, np.nan)
    for i in range(period, n):
        num = abs(pdi[i] - mdi[i])
        den = pdi[i] + mdi[i]
        dx[i] = 100 * num / den if den > 0 else 0
    out[2 * period - 1] = np.mean(dx[period:2 * period])
    for i in range(2 * period, n):
        out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out


@register("swing_mtf")
class SwingMTFScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "swing_mtf"

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

            d_score = self._tf_score(daily)
            h_score = self._tf_score(hourly)
            f_score = self._tf_score(fifteen)
            w_score = self._tf_score(weekly)

            has_weekly = w_score is not None and not np.isnan(w_score) and len(weekly) >= 5
            if has_weekly:
                mtf_score = 0.40 * d_score + 0.25 * h_score + 0.15 * f_score + 0.20 * w_score
            else:
                total_w = 0.40 + 0.25 + 0.15
                mtf_score = (0.40 * d_score + 0.25 * h_score + 0.15 * f_score) / total_w if total_w > 0 else 0.0

            close_vals = df["close"].values.astype(float)
            high_vals = df["high"].values.astype(float)
            low_vals = df["low"].values.astype(float)
            vol_vals = df["volume"].values.astype(float)

            rsi_vals = _rsi(close_vals, 9)
            ema20_vals = _ema(close_vals, 20)
            ema50_vals = _ema(close_vals, 50)
            adx_vals = _adx(high_vals, low_vals, close_vals, 14)
            cmf_vals = _cmf(high_vals, low_vals, close_vals, vol_vals, 14)
            atr_vals = _atr(high_vals, low_vals, close_vals, 14)
            slope_vals = _ema_slope(ema20_vals, config.EMA_SLOPE_BARS)

            last = len(close_vals) - 1
            rvol = vol_vals[last] / (np.mean(vol_vals[-21:-1]) if len(vol_vals) > 21 else np.mean(vol_vals[:last]))

            c = close_vals[last]
            e20 = ema20_vals[last]
            e50 = ema50_vals[last]
            slp = slope_vals[last]
            adx = adx_vals[last]
            cmf = cmf_vals[last]
            atr = atr_vals[last]
            rsi = rsi_vals[last]

            if not np.isnan(e20) and not np.isnan(e50) and c > e20 > e50 and (not np.isnan(slp) and slp > 0):
                bias = "BULLISH"
                direction = "LONG"
            elif not np.isnan(e20) and not np.isnan(e50) and c < e20 < e50 and (not np.isnan(slp) and slp < 0):
                bias = "BEARISH"
                direction = "SHORT"
            else:
                bias = "SIDEWAYS"
                direction = "NEUTRAL"

            aligned = 0
            total_tf = 0
            for x in [d_score, h_score, f_score]:
                if not np.isnan(x):
                    total_tf += 1
                    if bias == "BULLISH" and x > 60:
                        aligned += 1
                    elif bias == "BEARISH" and x < 40:
                        aligned += 1
            conf = aligned / total_tf if total_tf > 0 else 0.0

            features = {
                "d_score": float(d_score) if not np.isnan(d_score) else 0.0,
                "h_score": float(h_score) if not np.isnan(h_score) else 0.0,
                "f_score": float(f_score) if not np.isnan(f_score) else 0.0,
                "mtf_score": float(mtf_score) if not np.isnan(mtf_score) else 0.0,
                "rsi": float(rsi) if not np.isnan(rsi) else 50.0,
                "adx": float(adx) if not np.isnan(adx) else 20.0,
                "cmf": float(cmf) if not np.isnan(cmf) else 0.0,
                "rvol": float(rvol),
                "atr": float(atr) if not np.isnan(atr) else 0.0,
                "slope": float(slp) if not np.isnan(slp) else 0.0,
                "bias": 1.0 if bias == "BULLISH" else (-1.0 if bias == "BEARISH" else 0.0),
            }

            signal = ScannerSignal(
                timestamp=timestamp,
                symbol=symbol,
                scanner_name=self.name,
                score=float(mtf_score),
                rank=0,
                direction=direction,
                confidence=float(conf),
                timeframe=self.timeframe,
                features=features,
                signal_state="ACTIVE",
            )
            signals[symbol] = signal

        return signals

    def _tf_score(self, tf_df: pd.DataFrame) -> float:
        if tf_df.empty or len(tf_df) < 10:
            return 0.0
        c = tf_df["close"].values.astype(float)
        h = tf_df["high"].values.astype(float)
        lo = tf_df["low"].values.astype(float)
        v = tf_df["volume"].values.astype(float)

        ema20 = _ema(c, 20)
        slp = _ema_slope(ema20, config.EMA_SLOPE_BARS)
        _atr_val = _atr(h, lo, c, 14)

        last = len(c) - 1
        e20 = ema20[last] if not np.isnan(ema20[last]) else c[last]
        s = slp[last] if not np.isnan(slp[last]) else 0.0
        rvol = v[last] / (np.mean(v[-21:-1]) if len(v) > 21 else np.mean(v[:last]))

        score = 50.0
        if c[last] > e20:
            score += 15.0
        else:
            score -= 15.0

        score += np.clip(s * 1000, -15, 15)
        score += np.clip((rvol - 1) * 20, -10, 20)
        score = np.clip(score, 0, 100)
        return float(score)
