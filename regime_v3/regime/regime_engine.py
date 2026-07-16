"""
Regime engine — intraday NIFTY regime classification (wraps regime_engine.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def compute_regime(
    nifty_sig: pd.DataFrame | None,
    timestamp: pd.Timestamp | None = None,
    confirm_bars: int = 1,
) -> str:
    """
    Compute NIFTY regime at the given timestamp.

    Returns one of BULL, BEAR, SIDEWAYS, TRANSITION.
    Uses existing regime_engine if available, otherwise computes directly.
    """
    try:
        from regime_engine import compute_regime as _engine_regime
        if nifty_sig is not None:
            return _engine_regime(nifty_sig, timestamp, confirm_bars=confirm_bars)
    except (ImportError, AttributeError):
        pass

    if nifty_sig is None or nifty_sig.empty:
        return config.SIDEWAYS

    sig = _prepare_nifty(nifty_sig)
    if timestamp is not None:
        mask = sig.index <= timestamp
        if not mask.any():
            return config.SIDEWAYS
        sig = sig.loc[mask]

    if len(sig) < 50:
        return config.SIDEWAYS

    last = sig.iloc[-1]
    close = float(last["close"])
    ema20 = float(last.get("ema20", close))
    ema50 = float(last.get("ema50", close))
    slope = float(last.get("ema20_slope", 0))

    # BULL: close > EMA20 > EMA50, slope > 0
    if close > ema20 > ema50 and slope > 0:
        return config.BULL

    # BEAR: close < EMA20 < EMA50, slope < 0
    if close < ema20 < ema50 and slope < 0:
        return config.BEAR

    # Check transition: weak conditions
    if confirm_bars > 1 and len(sig) >= confirm_bars:
        recent = sig.tail(confirm_bars)
        mixed = (recent["close"] > recent["ema20"]).sum()
        if 0 < mixed < confirm_bars:
            return config.TRANSITION

    return config.SIDEWAYS


def _prepare_nifty(df: pd.DataFrame) -> pd.DataFrame:
    sig = df.copy()
    required = {"close", "high", "low"}
    if not required.issubset(sig.columns):
        for c in required:
            if c not in sig.columns:
                sig[c] = 0.0
    for c in ("close", "high", "low"):
        sig[c] = pd.to_numeric(sig[c], errors="coerce")
    try:
        from openalgo import ta as _ta
        sig["atr"] = _ta.atr(sig["high"], sig["low"], sig["close"], config.ATR_LENGTH)
    except Exception:
        hl = sig["high"] - sig["low"]
        hc = (sig["high"] - sig["close"].shift()).abs()
        lc = (sig["low"] - sig["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        sig["atr"] = tr.ewm(span=config.ATR_LENGTH, adjust=False).mean()
    sig["ema20"] = sig["close"].ewm(span=config.EMA20_LEN, adjust=False).mean()
    sig["ema50"] = sig["close"].ewm(span=config.EMA50_LEN, adjust=False).mean()
    sig["ema20_slope"] = (sig["ema20"] - sig["ema20"].shift(config.EMA_SLOPE_BARS)) / sig["atr"].clip(lower=1e-10)
    return sig
