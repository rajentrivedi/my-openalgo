"""
Scanner Registry — registers all scanner adapters.
Scanner names (from config.SCANNER_NAMES):
    1. swing_mtf
    2. hilega_milega_mtf
    3. range_breakout_v3
    4. momentum_v6
    5. volume_spike
    6. mean_reversion
    7. nifty_regime
    8. relative_strength
    9. multi_tf_trend
    10. volatility_contraction
"""
from __future__ import annotations

from typing import Any

_registry: dict[str, type] = {}

def register(name: str):
    def decorator(cls):
        _registry[name] = cls
        return cls
    return decorator

def get_scanner(name: str):
    if name not in _registry:
        raise KeyError(f"Scanner '{name}' not registered. Available: {list(_registry.keys())}")
    return _registry[name]

def list_scanners() -> list[str]:
    return list(_registry.keys())

def registered_count() -> int:
    return len(_registry)
