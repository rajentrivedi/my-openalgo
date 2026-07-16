"""
Scanner Adapter — abstract base for all scanner adapters.
Each scanner adapter wraps a signal computation and normalizes its output.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from .scanner_signal import NormalizedSignal, ScannerSignal


def _rankdata(arr: np.ndarray) -> np.ndarray:
    """Percentile rank (0..1) — manual implementation, scipy-free.
    Ties receive the mean rank of the group.
    """
    n = len(arr)
    if n <= 1:
        return np.full_like(arr, 0.5, dtype=float)
    order = arr.argsort()
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(n, dtype=float)
    unique_vals = np.unique(arr)
    for v in unique_vals:
        mask = arr == v
        ranks[mask] = ranks[mask].mean()
    return ranks / (n - 1)


class BaseScanner(ABC):
    """Abstract base for all scanner adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique scanner name matching config.SCANNER_NAMES."""
        ...

    @property
    @abstractmethod
    def timeframe(self) -> str:
        """Primary timeframe for this scanner."""
        ...

    @abstractmethod
    def compute_signals(
        self,
        symbol_data: dict[str, pd.DataFrame],
        nifty_data: pd.DataFrame | None,
        timestamp: pd.Timestamp,
    ) -> dict[str, ScannerSignal]:
        """
        Compute scanner signals at the given timestamp.

        Args:
            symbol_data: dict of symbol -> DataFrame with columns [open, high, low, close, volume]
                         Data is provided through the current timestamp only (no future data).
            nifty_data: NIFTY index DataFrame (same structure) or None
            timestamp: the decision timestamp

        Returns:
            dict of symbol -> ScannerSignal for all symbols that produce a signal.
        """
        ...

    def normalize_scores(
        self, signals: dict[str, ScannerSignal]
    ) -> dict[str, NormalizedSignal]:
        """
        Cross-sectional normalization of scanner signals at a single timestamp.
        Default: percentile rank + min-max to [0, 1].
        Handles degenerate cases: single symbol, all-equal scores.
        """
        if not signals:
            return {}

        scores = {sym: sig.score for sym, sig in signals.items()}
        symbols_list = list(scores.keys())
        scores_arr = np.array([scores[s] for s in symbols_list], dtype=float)

        # Percentile rank (0..1) — try scipy first, fall back to manual
        n = len(scores_arr)
        if n == 1:
            percentiles = np.array([0.5])
        else:
            try:
                from scipy.stats import rankdata
                raw = rankdata(scores_arr)
                percentiles = (raw - 1) / (n - 1)
            except ImportError:
                percentiles = _rankdata(scores_arr)

        # Min-max normalize to [0, 1]
        s_min, s_max = scores_arr.min(), scores_arr.max()
        if s_max > s_min:
            normalized = (scores_arr - s_min) / (s_max - s_min)
        else:
            normalized = np.full_like(scores_arr, 0.5)

        result = {}
        for i, sym in enumerate(symbols_list):
            sig = signals[sym]
            result[sym] = NormalizedSignal(
                timestamp=sig.timestamp,
                symbol=sym,
                scanner_name=self.name,
                raw_score=sig.score,
                normalized_score=float(normalized[i]),
                percentile_score=float(percentiles[i]),
                direction=sig.direction,
                confidence=sig.confidence,
                features=dict(sig.features),
            )
        return result
