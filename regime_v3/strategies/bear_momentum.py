"""
BEAR_MOMENTUM strategy — short-only trend continuation in BEAR regime.

Entry: close < EMA20 < EMA50, slope < 0, rel_volume >= min_rvol
Exit: ATR-based stop, RR target.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .. import config


def bear_entry_signal(
    candle: pd.Series,
    prev: pd.Series,
    p: config.RegimeV3Params,
) -> tuple[str, float, float, float] | None:
    close = float(prev.get("close", 0))
    ema20 = float(prev.get("ema20", 0))
    ema50 = float(prev.get("ema50", 0))
    slope = float(prev.get("ema20_slope", 0))
    rvol = float(prev.get("rel_volume", 0))
    atr = float(prev.get("atr", 0.5))
    
    if pd.isna(ema20) or pd.isna(ema50) or pd.isna(atr) or atr <= 0:
        return None
    if close >= ema20 or ema20 >= ema50 or slope >= 0:
        return None
    if rvol < p.bear_min_rvol:
        return None
    
    stop = close + atr * p.stop_atr_multiplier
    target = close - atr * (p.stop_atr_multiplier * p.target_rr)
    score = rvol + (ema20 - close) / max(atr, 1e-10)
    
    return ("SHORT", stop, target, float(score))