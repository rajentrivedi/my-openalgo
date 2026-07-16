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


def _tf_trend_score(c: np.ndarray) -> tuple[int, int, str]:
    if len(c) < 50:
        return 0, 0, "unknown"
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    slope = _ema_slope(ema20, 3)
    last = len(c) - 1
    e20 = ema20[last]
    e50 = ema50[last]
    slp = slope[last]
    cl = c[last]

    if np.isnan(e20) or np.isnan(e50) or np.isnan(slp):
        return 0, 0, "unknown"

    bullish_count = 0
    bearish_count = 0
    if cl > e20:
        bullish_count += 1
    else:
        bearish_count += 1
    if e20 > e50:
        bullish_count += 1
    else:
        bearish_count += 1
    if slp > 0:
        bullish_count += 1
    else:
        bearish_count += 1

    if bullish_count >= 2:
        trend = "bullish"
    elif bearish_count >= 2:
        trend = "bearish"
    else:
        trend = "neutral"

    return bullish_count, bearish_count, trend


@register("multi_tf_trend")
class MultiTFTrendScanner(BaseScanner):

    @property
    def name(self) -> str:
        return "multi_tf_trend"

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
            if df.empty or len(df) < 60:
                continue

            df = df[df.index <= timestamp]
            if len(df) < 60:
                continue

            tf5m = df
            tf15m = _resample(df, "15min")
            tf1h = _resample(df, "1h")
            tf1d = _resample(df, "1D")

            tf_data = [
                ("5m", tf5m, 0.20),
                ("15m", tf15m, 0.25),
                ("1h", tf1h, 0.30),
                ("1d", tf1d, 0.25),
            ]

            scores_by_name: dict[str, tuple[int, int, str]] = {}

            for tf_name, tf_df, _ in tf_data:
                if tf_df.empty or len(tf_df) < 50:
                    scores_by_name[tf_name] = (0, 0, "unknown")
                    continue
                c = tf_df["close"].values.astype(float)
                scores_by_name[tf_name] = _tf_trend_score(c)

            bullish_tfs = sum(1 for _, (b, _, _) in scores_by_name.items() if b >= 2 and _ != "unknown")
            bearish_tfs = sum(1 for _, (_, be, _) in scores_by_name.items() if be >= 2 and _ != "unknown")
            unknown_tfs = sum(1 for _, (_, _, t) in scores_by_name.items() if t == "unknown")
            available_tfs = 4 - unknown_tfs
            conflict_flag = 1.0 if bullish_tfs > 0 and bearish_tfs > 0 else 0.0

            if bullish_tfs >= 3 and bearish_tfs == 0:
                direction = "LONG"
            elif bearish_tfs >= 3 and bullish_tfs == 0:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            confidence = max(0.0, (bullish_tfs - bearish_tfs) / max(available_tfs, 1)) if direction == "LONG" else max(0.0, (bearish_tfs - bullish_tfs) / max(available_tfs, 1))

            composite_score = 0.0
            for tf_name, (b, be, t) in scores_by_name.items():
                weight = {"5m": 0.20, "15m": 0.25, "1h": 0.30, "1d": 0.25}.get(tf_name, 0.0)
                if t == "unknown":
                    continue
                tf_score = (b / max(b + be, 1)) * 25
                composite_score += tf_score * weight

            score = float(np.clip(composite_score * 4, 0, 100))

            features = {
                "tf_count_bullish": float(bullish_tfs),
                "tf_count_bearish": float(bearish_tfs),
                "conflict_flag": conflict_flag,
                "5m_trend": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "unknown": 0.0}.get(scores_by_name["5m"][2], 0.0),
                "15m_trend": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "unknown": 0.0}.get(scores_by_name["15m"][2], 0.0),
                "1h_trend": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "unknown": 0.0}.get(scores_by_name["1h"][2], 0.0),
                "daily_trend": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "unknown": 0.0}.get(scores_by_name["1d"][2], 0.0),
                "available_tfs": float(available_tfs),
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
