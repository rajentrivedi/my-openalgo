"""
Scanner package — auto-imports all scanner modules so they self-register.
"""
from __future__ import annotations

# Import all scanner modules to trigger @register decorators
from . import (
    hilega_milega,  # noqa: F401
    mean_reversion,  # noqa: F401
    momentum,  # noqa: F401
    multi_tf_trend,  # noqa: F401
    nifty_regime,  # noqa: F401
    range_breakout,  # noqa: F401
    relative_strength,  # noqa: F401
    swing_mtf,  # noqa: F401
    volatility_contraction,  # noqa: F401
    volume_spike,  # noqa: F401
)
from .scanner_registry import get_scanner, list_scanners, registered_count  # noqa: F401
from .scanner_signal import (  # noqa: F401
    NormalizedSignal,
    RankedCandidate,
    ScannerConsensus,
    ScannerSignal,
)

__all__ = [
    "list_scanners",
    "get_scanner",
    "registered_count",
    "ScannerSignal",
    "NormalizedSignal",
    "ScannerConsensus",
    "RankedCandidate",
]
