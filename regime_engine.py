"""
Regime Engine — mutually exclusive NIFTY regime classifier (intraday).
Used by regime_three_v3.py to select active strategy per timestamp.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# Regime constants
BULL = "BULL"
BEAR = "BEAR"
SIDEWAYS = "SIDEWAYS"
TRANSITION = "TRANSITION"
ALL_REGIMES = [BULL, BEAR, SIDEWAYS, TRANSITION]


def compute_regime(nifty_sig: pd.DataFrame | None, ts: pd.Timestamp, confirm_bars: int = 1) -> str:
    """Classify regime using NIFTY data up to ts. Mutually exclusive.

    Priority: BULL > BEAR > SIDEWAYS > TRANSITION
    """
    if nifty_sig is None or nifty_sig.empty:
        return SIDEWAYS
    prior = nifty_sig[nifty_sig.index <= ts]
    if len(prior) < max(50, confirm_bars + 1):
        return SIDEWAYS

    last = prior.iloc[-1]
    _last_n = lambda n: prior.iloc[-n] if len(prior) >= n else last

    close = float(last["close"])
    ema20 = float(last.get("ema20", close))
    ema50 = float(last.get("ema50", close))
    slope = float(last.get("ema20_slope", 0))
    atr = float(last.get("atr", 0.5))
    ema_sep = (ema20 - ema50) / max(atr, 1e-10)

    # 20-bar return
    ret20 = (close / float(_last_n(20)["close"]) - 1) if not np.isnan(float(_last_n(20)["close"])) else 0.0

    # Extended regime features with multi-bar confirmation
    close_gt_ema20 = close > ema20
    ema20_gt_ema50 = ema20 > ema50
    slope_gt0 = slope > 0
    close_lt_ema20 = close < ema20
    ema20_lt_ema50 = ema20 < ema50
    slope_lt0 = slope < 0
    abs_slope_small = abs(slope) < 0.3
    sep_small = abs(ema_sep) < 1.0
    ret20_small = abs(ret20 * 100) < 3.0
    atr_vs_median = atr < prior["atr"].median() * 0.8 if "atr" in prior.columns and len(prior) > 20 else False

    if confirm_bars > 1:
        # Require N consecutive bars confirming the regime
        for c in range(1, min(confirm_bars + 1, len(prior))):
            row = prior.iloc[-c]
            cr = float(row["close"])
            e20 = float(row.get("ema20", cr))
            e50 = float(row.get("ema50", cr))
            sl = float(row.get("ema20_slope", 0))
            _close_gt = cr > e20
            _e20_gt_e50 = e20 > e50
            _sl_gt = sl > 0
            _close_lt = cr < e20
            _e20_lt_e50 = e20 < e50
            _sl_lt = sl < 0
            if c == 1:
                continue  # current bar already computed
            close_gt_ema20 = close_gt_ema20 and _close_gt
            ema20_gt_ema50 = ema20_gt_ema50 and _e20_gt_e50
            slope_gt0 = slope_gt0 and _sl_gt
            close_lt_ema20 = close_lt_ema20 and _close_lt
            ema20_lt_ema50 = ema20_lt_ema50 and _e20_lt_e50
            slope_lt0 = slope_lt0 and _sl_lt

    # Mutually exclusive: BULL first, BEAR second, SIDEWAYS third, else TRANSITION
    bull = close_gt_ema20 and ema20_gt_ema50 and slope_gt0 and (ret20 * 100 > 0.5)
    bear = close_lt_ema20 and ema20_lt_ema50 and slope_lt0 and (ret20 * 100 < -0.5)
    sideways = abs_slope_small or sep_small or ret20_small or atr_vs_median

    if bull:
        return BULL
    if bear:
        return BEAR
    if sideways:
        return SIDEWAYS
    return TRANSITION