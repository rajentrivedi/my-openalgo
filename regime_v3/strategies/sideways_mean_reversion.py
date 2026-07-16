"""
SIDEWAYS_MEAN_REVERSION — fade VWAP/EMA extremes.

Entry: price significantly above/below VWAP and EMA20 (ATR-normalized).
Exit: tight stop, target at VWAP/EMA convergence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .. import config


def sideways_entry_signal(
    prev: pd.Series,
    candle: pd.Series,
    p: config.RegimeV3Params,
) -> tuple[str, float, float, float] | None:
    close = float(prev.get("close", 0))
    vwap = float(prev.get("vwap", close))
    ema20 = float(prev.get("ema20", close))
    atr = float(prev.get("atr", 0.5))
    rvol = float(prev.get("rel_volume", 0))
    
    if pd.isna(atr) or atr <= 0:
        return None
    
    vwap_dev = (close - vwap) / atr
    ema_dev = (close - ema20) / atr
    
    # LONG: price below VWAP and EMA20 (oversold)
    if vwap_dev < -config.VWAP_DEVIATION_ENTRY and ema_dev < -config.EMA_DEVIATION_ENTRY and rvol >= p.sideways_min_rvol:
        stop = close - atr * p.sideways_stop_atr
        target = min(vwap, ema20)
        score = abs(vwap_dev * 100)
        return ("LONG", stop, target, float(score))
    
    # SHORT: price above VWAP and EMA20 (overbought)
    if vwap_dev > config.VWAP_DEVIATION_ENTRY and ema_dev > config.EMA_DEVIATION_ENTRY and rvol >= p.sideways_min_rvol:
        stop = close + atr * p.sideways_stop_atr
        target = max(vwap, ema20)
        score = abs(vwap_dev * 100)
        return ("SHORT", stop, target, float(score))
    
    return None